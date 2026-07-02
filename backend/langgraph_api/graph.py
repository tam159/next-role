import asyncio
import functools
import glob
import importlib.util
import inspect
import logging
import os
import sys
import time
import warnings
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from itertools import filterfalse
from typing import Any, NamedTuple, TypeGuard
from uuid import UUID, uuid5

import orjson
import structlog
from langchain_core.embeddings import Embeddings  # noqa: TC002
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel
from langgraph.store.base import BaseStore
from starlette.exceptions import HTTPException

from langgraph_api import config as lg_api_config
from langgraph_api import timing
from langgraph_api._factory_utils import (
    AccessContext,
    build_server_runtime,
    classify_factory,
    invoke_factory,
    is_factory,
    is_for_execution,
)
from langgraph_api.asyncio import as_asynccontextmanager
from langgraph_api.feature_flags import (
    IS_POSTGRES_OR_GRPC_BACKEND,
    USE_RUNTIME_CONTEXT_API,
)
from langgraph_api.js.base import BaseRemotePregel, is_js_path
from langgraph_api.schema import Config
from langgraph_api.timing import profiled_import
from langgraph_api.utils.config import run_in_executor, var_child_runnable_config
from langgraph_api.utils.errors import GraphLoadError

logger = structlog.stdlib.get_logger(__name__)

GraphFactoryFromConfig = Callable[[Config], Pregel | StateGraph]
GraphFactory = Callable[[], Pregel | StateGraph]
GraphValue = Pregel | GraphFactory | GraphFactoryFromConfig


GRAPHS: dict[str, GraphValue] = {}
NAMESPACE_GRAPH = UUID("6ba7b821-9dad-11d1-80b4-00c04fd430c8")
SYSTEM_ASSISTANT_IDS: set[str] = set()

# Map of graph_id → zero-arg factory returning a fresh list of custom
# ``StreamTransformer`` instances, populated from a module-level
# ``stream_transformers`` symbol on the graph's source module. Used by
# the Protocol v2 streaming path in ``stream.py`` to wire user-supplied
# projections (``StreamChannel`` / ``EventLog``) into ``StreamingHandler``
# so events from the user's transformers surface on ``custom:<name>``
# subscriptions on the wire.
#
# We require a factory (rather than accepting raw transformer instances)
# because transformers are stateful — sharing one instance across
# concurrent runs would cause state bleed. The factory is invoked once
# per run and the returned list is handed straight to ``StreamMux``.
GRAPH_STREAM_TRANSFORMERS: dict[str, Callable[[], list[Any]]] = {}


async def register_graph(
    graph_id: str,
    graph: GraphValue,
    config: dict | None,
    *,
    description: str | None = None,
) -> None:
    """Register a graph."""
    from langgraph_runtime.database import connect  # noqa: PLC0415

    if IS_POSTGRES_OR_GRPC_BACKEND:
        from langgraph_api.grpc.ops import Assistants  # noqa: PLC0415
    else:
        from langgraph_runtime.ops import Assistants  # noqa: PLC0415

    GRAPHS[graph_id] = graph
    assistant_id = uuid5(NAMESPACE_GRAPH, graph_id)
    SYSTEM_ASSISTANT_IDS.add(str(assistant_id))
    if callable(graph):
        classify_factory(graph, graph_id)

    from langgraph_runtime.retry import retry_db  # noqa: PLC0415

    @retry_db
    async def register_graph_db():
        async with connect() as conn:
            graph_name = (
                getattr(graph, "name", None) if isinstance(graph, Pregel) else None
            )
            assistant_name = (
                graph_name
                if graph_name is not None and graph_name != "LangGraph"
                else graph_id
            )
            result = Assistants.put(
                conn,
                str(assistant_id),
                graph_id=graph_id,
                metadata={"created_by": "system"},
                config=config or {},
                context={},
                if_exists="do_nothing",
                name=assistant_name,
                description=description,
                system=True,
            )
            assistant = None
            async for a in await result:
                assistant = a
            # Sync description and name for existing assistants.
            # put() with if_exists="do_nothing" won't update existing
            # assistants, so we need to patch them to reflect any
            # config changes (e.g. description added to LANGSERVE_GRAPHS).
            if assistant and (
                assistant.get("description") != description
                or assistant.get("name") != assistant_name
            ):
                async for _ in await Assistants.patch(
                    conn,
                    assistant_id,
                    description=description,
                    name=assistant_name,
                ):
                    pass

    if not lg_api_config.IS_EXECUTOR_ENTRYPOINT:
        await register_graph_db()


def _validate_assistant_id(assistant_id: str) -> None:
    """Validate an assistant ID is either a graph_id or a valid UUID. Throw an error if not valid."""
    if assistant_id and assistant_id not in GRAPHS:
        # Not a graph_id, must be a valid UUID
        try:
            UUID(assistant_id)
        except ValueError:
            # Invalid format - return 404 to match test expectations
            raise HTTPException(
                status_code=404,
                detail=f"Assistant '{assistant_id}' not found",
            ) from None


def _log_slow_graph_generation(
    start: float,
    value_type: str,
    graph_id: str,
    run_id: str | None = None,
    warn_threshold_ms: float = 100,
    error_threshold_ms: float = 250,
) -> None:
    """Log warning/error if graph generation was slow."""
    elapsed_secs = time.perf_counter() - start
    elapsed_ms = elapsed_secs * 1000
    elapsed_ms_rounded = round(elapsed_ms, 2)
    log_level = None
    if elapsed_ms > error_threshold_ms:
        log_level = logging.ERROR
    elif elapsed_ms > warn_threshold_ms:
        log_level = logging.WARNING
    if log_level is not None:
        logger.log(
            log_level,
            f"Slow graph load. Accessing graph '{graph_id}' took {elapsed_ms_rounded}ms."
            " Move expensive initialization (API clients, DB connections, model loading)"
            " from graph factory if you are seeing API slowness.",
            elapsed_ms=elapsed_ms_rounded,
            value_type=value_type,
            graph_id=graph_id,
            run_id=run_id,
        )


_ddtracer: Any = None


def _get_ddtracer() -> Any:
    """Return the ddtrace tracer singleton, or None if ddtrace is not installed."""
    global _ddtracer
    if not hasattr(_get_ddtracer, "_checked"):
        _get_ddtracer._checked = True
        try:
            from ddtrace import tracer  # type: ignore[unresolved-import]  # noqa: PLC0415, I001

            _ddtracer = tracer
        except ImportError:
            # ddtrace is an optional dependency; tracing is silently disabled when absent.
            pass
    return _ddtracer


# Eagerly initialize ddtrace at import time so its blocking os.getcwd() call
# (inside ddtrace's module init) runs synchronously before the event loop starts,
# not lazily on the first request (which would trigger a blockbuster BlockingError).
_get_ddtracer()


def _start_graph_load_span(graph_id: str, access_context: str | None) -> Any:
    """Start a ddtrace span covering graph factory __aenter__ time.

    Starting before __aenter__ means HTTP calls made by the factory appear as
    children of this span. Returns the span, or None if ddtrace is unavailable.
    """
    ddtracer = _get_ddtracer()
    if ddtracer is None:
        return None
    try:
        span = ddtracer.trace("langgraph.graph_load")
        span.set_tag("graph_id", graph_id)
        if access_context:
            span.set_tag("access_context", access_context)
        return span
    except Exception:
        return None


def _finish_graph_load_span(span: Any, value_type: str) -> None:
    if span is None:
        return
    try:
        span.set_tag("value_type", value_type)
        span.finish()
    except Exception:
        pass  # Tracing must never interfere with application logic


# Key used to carry the ddtrace span context across the HTTP→worker boundary.
_DD_TRACE_HEADERS_KEY = "__dd_trace_headers__"


def inject_current_dd_trace_context(configurable: dict[str, Any]) -> None:
    """Serialize the active ddtrace span into the run configurable for worker propagation."""
    ddtracer = _get_ddtracer()
    if ddtracer is None:
        return
    try:
        from ddtrace.propagation.http import (  # type: ignore[unresolved-import]  # noqa: PLC0415
            HTTPPropagator,
        )

        span = ddtracer.current_span()
        if span is None:
            return
        headers: dict[str, str] = {}
        HTTPPropagator.inject(span.context, headers)
        if headers:
            configurable[_DD_TRACE_HEADERS_KEY] = headers
    except Exception:
        logger.debug("Failed to inject ddtrace context", exc_info=True)


@contextmanager
def restore_dd_trace_context(
    configurable: dict[str, Any],
    run_id: str | None = None,
    thread_id: str | None = None,
) -> Generator[None, None, None]:
    """Activate a worker.run_graph span under the originating starlette.request span."""
    ddtracer = _get_ddtracer()
    headers = configurable.get(_DD_TRACE_HEADERS_KEY)
    if ddtracer is None or not headers:
        yield
        return

    # Setup the ddtrace span for the worker.run_graph span
    span = None
    try:
        from ddtrace.propagation.http import (  # type: ignore[unresolved-import]  # noqa: PLC0415
            HTTPPropagator,
        )

        ctx = HTTPPropagator.extract(headers)
        # trace() doesn't accept child_of; start_span with activate=True sets the
        # parent and makes this the active span for subsequent trace() calls.
        span = ddtracer.start_span("worker.run_graph", child_of=ctx, activate=True)
        span.set_tag("graph_id", configurable.get("graph_id", ""))
        if run_id:
            span.set_tag("run_id", run_id)
        if thread_id:
            span.set_tag("thread_id", thread_id)
    except Exception:
        logger.debug("Failed to restore ddtrace context", exc_info=True)
        if span is not None:
            try:
                span.finish()
            except Exception:
                logger.warning("Failed to finish ddtrace context span", exc_info=True)
                # Tracing setup failures must never interfere with application logic
        yield
        return

    # Yield to the caller, ensuring the span is finished even if an exception is raised.
    try:
        yield
    finally:
        if span is not None:
            try:
                span.finish()
            except Exception:
                logger.warning("Failed to finish ddtrace context span", exc_info=True)
                # Tracing failures must never interfere with application logic


@asynccontextmanager
async def _generate_graph(
    value: Any,
    graph_id: str,
    run_id: str | None = None,
    access_context: str | None = None,
) -> AsyncIterator[Any]:
    """Yield a graph object regardless of its type.

    Logs a warning if graph generation takes >100ms, error if >250ms.
    run_id is passed through solely for inclusion in slow-load log entries,
    enabling correlation with access logs.
    """
    start = time.perf_counter()
    value_type = type(value).__name__
    if isinstance(value, Pregel | BaseRemotePregel):
        yield value
        return
    span = _start_graph_load_span(graph_id, access_context)
    try:
        async with as_asynccontextmanager(value) as ctx_value:
            _log_slow_graph_generation(start, value_type, graph_id, run_id=run_id)
            _finish_graph_load_span(span, value_type)
            yield ctx_value
    finally:
        _finish_graph_load_span(span, value_type)


def is_js_graph(graph_id: str) -> TypeGuard[BaseRemotePregel]:
    """Return whether a graph is a JS graph."""
    return graph_id in GRAPHS and isinstance(GRAPHS[graph_id], BaseRemotePregel)


@asynccontextmanager
async def get_graph(
    graph_id: str,
    config: Config,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore,
    access_context: AccessContext,
    run_id: str | None = None,
) -> AsyncIterator[Pregel]:
    """Return the runnable."""
    from langgraph_api.utils import config as lg_config  # noqa: PLC0415
    from langgraph_api.utils import merge_auth  # noqa: PLC0415

    assert_graph_exists(graph_id)
    value = GRAPHS[graph_id]
    if is_factory(graph_id):
        config = lg_config.ensure_config(config)
        config = merge_auth(config)
        server_runtime = build_server_runtime(access_context, store)
        if USE_RUNTIME_CONTEXT_API:
            from langgraph._internal._constants import (  # noqa: PLC0415
                CONFIG_KEY_RUNTIME,
            )

            config["configurable"][CONFIG_KEY_RUNTIME] = server_runtime
        elif store is not None:
            from langgraph.constants import CONFIG_KEY_STORE  # noqa: PLC0415

            config["configurable"].setdefault(CONFIG_KEY_STORE, store)

        if checkpointer is not None and not config["configurable"].get(
            CONFIG_KEY_CHECKPOINTER
        ):
            config["configurable"][CONFIG_KEY_CHECKPOINTER] = checkpointer
        config["configurable"]["__is_for_execution__"] = is_for_execution(
            access_context
        )
        var_child_runnable_config.set(config)

        value = invoke_factory(value, graph_id, config, server_runtime)
    try:
        async with _generate_graph(
            value, graph_id, run_id=run_id, access_context=access_context
        ) as graph_obj:
            if isinstance(graph_obj, StateGraph):
                graph_obj = graph_obj.compile()
            if not isinstance(graph_obj, Pregel | BaseRemotePregel):
                raise HTTPException(
                    status_code=424,
                    detail=f"Graph '{graph_id}' is not valid. Review graph registration. {graph_obj}",
                )
            update = {
                "checkpointer": checkpointer,
                "store": store,
            }
            if graph_obj.name == "LangGraph":
                update["name"] = graph_id
            if isinstance(graph_obj, BaseRemotePregel):
                update["config"] = config
            yield graph_obj.copy(update=update)
    finally:
        var_child_runnable_config.set(None)


def graph_exists(graph_id: str) -> bool:
    """Return whether a graph exists."""
    return graph_id in GRAPHS


def assert_graph_exists(graph_id: str) -> None:
    """Assert that a graph exists."""
    if not graph_exists(graph_id):
        raise HTTPException(
            status_code=404,
            detail=f"Graph '{graph_id}' not found. Expected one of: {sorted(GRAPHS.keys())}",
        )


def get_assistant_id(assistant_id: str) -> str:
    """Check if assistant_id is a valid graph_id. If so, retrieve the
    assistant_id from the graph_id. Otherwise, return the assistant_id
    as is.

    This method is used where the API allows passing both assistant_id
    and graph_id interchangeably.
    """
    if assistant_id in GRAPHS:
        assistant_id = str(uuid5(NAMESPACE_GRAPH, assistant_id))
    return assistant_id


class GraphSpec(NamedTuple):
    """A graph specification.

    This is a definition of the graph that can be used to load the graph
    from a file or module.
    """

    id: str
    """The ID of the graph."""
    path: str | None = None
    module: str | None = None
    variable: str | None = None
    config: dict | None = None
    """The configuration for the graph.

    Contains information such as: tags, recursion_limit and configurable.

    Configurable is a dict containing user defined values for the graph.
    """
    description: str | None = None
    """A description of the graph"""


js_bg_tasks: set[asyncio.Task] = set()


def _load_graph_config_from_env() -> dict | None:
    """Return graph config from env."""
    # Not in public docs: LANGGRAPH_CONFIG is internal, set by CLI from langgraph.json
    config_str = os.getenv("LANGGRAPH_CONFIG")
    if not config_str:
        return None
    try:
        config_per_id = orjson.loads(config_str)
    except orjson.JSONDecodeError as e:
        raise ValueError(
            "Provided environment variable LANGGRAPH_CONFIG must be a valid JSON object"
            f"\nFound: {config_str}"
        ) from e

    if not isinstance(config_per_id, dict):
        raise ValueError(
            "Provided environment variable LANGGRAPH_CONFIG must be a JSON object"
            f"\nFound: {config_str}"
        )

    return config_per_id


async def collect_graphs_from_env(register: bool = False) -> None:
    """Return graphs from env."""

    # Not in public docs: LANGSERVE_GRAPHS is internal, set by CLI from langgraph.json "graphs" field
    paths_str = os.getenv("LANGSERVE_GRAPHS")
    config_per_graph = _load_graph_config_from_env() or {}

    if paths_str:
        specs = []
        # graphs-config can be either a mapping from graph id to path where the graph
        # is defined or graph id to a dictionary containing information about the graph.
        try:
            graphs_config = orjson.loads(paths_str)
        except orjson.JSONDecodeError as e:
            raise ValueError(
                "LANGSERVE_GRAPHS must be a valid JSON object."
                f"\nFound: {paths_str}"
                "\n The LANGSERVE_GRAPHS environment variable is typically set"
                'from the "graphs" field in your configuration (langgraph.json) file.'
            ) from e

        for key, value in graphs_config.items():
            if isinstance(value, dict) and "path" in value:
                source = value["path"]
            elif isinstance(value, str):
                source = value
            else:
                msg = (
                    f"Invalid value '{value}' for graph '{key}'. "
                    "Expected a string or a dictionary. "
                    "If a string, it should be the path to the graph definition. "
                    "For example: '/path/to/graph.py:graph_variable' "
                    "or 'my.module:graph_variable'. "
                    "If a dictionary, then it needs to contains a `path` key with the "
                    "path to the graph definition."
                    "It can also contains additional configuration for the graph; "
                    "e.g., `description`."
                    "For example: {'path': '/path/to/graph.py:graph_variable', "
                    "'description': 'My graph'}"
                )
                raise TypeError(msg)

            try:
                path_or_module, variable = source.rsplit(":", maxsplit=1)
            except ValueError as e:
                raise ValueError(
                    f"Invalid path '{value}' for graph '{key}'."
                    " Did you miss a variable name?\n"
                    " Expected one of the following formats:"
                    " 'my.module:variable_name' or '/path/to/file.py:variable_name'"
                ) from e

            graph_config = config_per_graph.get(key, {})
            description = (
                value.get("description", None) if isinstance(value, dict) else None
            )

            # Module syntax uses `.` instead of `/` to separate directories
            if "/" in path_or_module:
                path = path_or_module
                module_ = None
            else:
                path = None
                module_ = path_or_module

            specs.append(
                GraphSpec(
                    key,
                    module=module_,
                    path=path,
                    variable=variable,
                    config=graph_config,
                    description=description,
                )
            )
    else:
        specs = [
            GraphSpec(
                id=graph_path.split("/")[-1].replace(".py", ""),
                path=graph_path,
                config=config_per_graph.get(
                    graph_path.split("/")[-1].replace(".py", "")
                ),
            )
            for graph_path in glob.glob("/graphs/*.py")
        ]

    def is_js_spec(x: GraphSpec) -> bool:
        return is_js_path(x.path)

    js_specs = list(filter(is_js_spec, specs))
    py_specs = list(filterfalse(is_js_spec, specs))

    if js_specs:
        if lg_api_config.API_VARIANT == "local_dev":
            raise NotImplementedError(
                "LangGraph.JS graphs are not yet supported in local development mode. "
                "To run your JS graphs, either use the LangGraph Studio application "
                "or run `langgraph up` to start the server in a Docker container."
            )
        from langgraph_api.js.remote import (  # noqa: PLC0415
            RemotePregel,
            run_js_http_process,
            run_js_process,
            run_remote_checkpointer,
            wait_until_js_ready,
        )

        js_bg_tasks.add(
            asyncio.create_task(
                run_remote_checkpointer(),
                name="remote-socket-poller",
            )
        )
        js_bg_tasks.add(
            asyncio.create_task(
                run_js_process(paths_str, watch="--reload" in sys.argv[1:]),
                name="remote-graphs",
            )
        )

        if (
            lg_api_config.HTTP_CONFIG
            and (js_app := lg_api_config.HTTP_CONFIG.get("app"))
            and is_js_path(js_app.split(":")[0])
        ):
            js_bg_tasks.add(
                asyncio.create_task(
                    run_js_http_process(
                        paths_str,
                        lg_api_config.HTTP_CONFIG or {},
                        watch="--reload" in sys.argv[1:],
                    ),
                )
            )

        for task in js_bg_tasks:
            task.add_done_callback(_handle_exception)

        await wait_until_js_ready(js_bg_tasks)

        for spec in js_specs:
            graph = RemotePregel(graph_id=spec.id)
            if register:
                await register_graph(
                    spec.id, graph, spec.config, description=spec.description
                )

    for spec in py_specs:
        try:
            graph = await run_in_executor(None, _graph_from_spec, spec)
        except Exception as exc:
            raise GraphLoadError(spec, exc) from exc
        if register:
            await register_graph(
                spec.id, graph, spec.config, description=spec.description
            )


def _handle_exception(task: asyncio.Task) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, SystemExit):
        pass
    except Exception as e:
        logger.exception("Task failed", exc_info=e)
    finally:
        # if the task died either with exception or not, we should exit
        sys.exit(1)


async def stop_remote_graphs() -> None:
    logger.info("Shutting down remote graphs")
    for task in js_bg_tasks:
        task.cancel("Stopping remote graphs.")


def verify_graphs() -> None:
    asyncio.run(collect_graphs_from_env())


def _metadata_fn(spec: GraphSpec) -> dict[str, Any]:
    return {"graph_id": spec.id, "module": spec.module, "path": spec.path}


def patch_packages_distributions() -> None:
    """Cache importlib.metadata.packages_distributions() to avoid repeated full scans.

    google-api-core >=2.28.0 calls packages_distributions() — an uncached O(N)
    scan of every installed package's RECORD file — 13+ times at import time
    (once per google.cloud.* sub-package).  With ddtrace's import hooks each
    call takes ~13 s in production, totalling ~170 s.

    The installed-package set never changes during a process lifetime, so a
    single cached result is safe and reduces 13 scans to 1.

    Controlled by LSD_CACHE_PACKAGES_DISTRIBUTIONS (default "false").
    Set to "true" to enable.
    """
    if os.environ.get("LSD_CACHE_PACKAGES_DISTRIBUTIONS", "false").lower() != "true":
        return

    import importlib.metadata  # noqa: PLC0415

    if getattr(importlib.metadata.packages_distributions, "_cached", False):
        return

    logger.info("Caching importlib.metadata.packages_distributions()")

    original = importlib.metadata.packages_distributions
    cache: dict | None = None

    def _cached():
        nonlocal cache
        if cache is None:
            cache = original()
        return cache

    _cached._cached = True
    importlib.metadata.packages_distributions = _cached


@timing.timer(
    message="Importing graph profiling",
    metadata_fn=_metadata_fn,
    warn_threshold_secs=3,
    warn_message=(
        "Graph import exceeded the expected startup time. "
        "Slow initialization (often due to work executed at import time) can delay readiness, "
        "slow scale-out velocity, and may cause deployments to be marked unhealthy."
    ),
    error_threshold_secs=30,
)
def _graph_from_spec(spec: GraphSpec) -> GraphValue:
    """Return a graph from a spec."""
    # import the graph module
    import_path = f"{spec.module or spec.path}:{spec.variable or '<auto>'}"
    with profiled_import(import_path):
        if spec.module:
            module = importlib.import_module(spec.module)
        elif spec.path:
            try:
                modname = (
                    spec.path.replace("/", "__")
                    .replace(".py", "")
                    .replace(" ", "_")
                    .lstrip(".")
                )
                modspec = importlib.util.spec_from_file_location(modname, spec.path)
                if modspec is None:
                    raise ValueError(f"Could not find python file for graph: {spec}")
                module = importlib.util.module_from_spec(modspec)
                sys.modules[modname] = module
                modspec.loader.exec_module(module)
            except ImportError as e:
                e.add_note(f"Could not import python module for graph:\n{spec}")
                if lg_api_config.API_VARIANT == "local_dev":
                    e.add_note(
                        "This error likely means you haven't installed your project and its dependencies yet. Before running the server, install your project:\n\n"
                        "If you are using requirements.txt:\n"
                        "python -m pip install -r requirements.txt\n\n"
                        "If you are using pyproject.toml or setuptools:\n"
                        "python -m pip install -e .\n\n"
                        "Make sure to run this command from your project's root directory (where your setup.py or pyproject.toml is located)"
                    )
                raise
            except FileNotFoundError as e:
                e.add_note(f"Could not find python file for graph: {spec}")
                raise
        else:
            raise ValueError("Graph specification must have a path or module")

    if spec.variable:
        try:
            graph: GraphValue = module.__dict__[spec.variable]
        except KeyError as e:
            available = [k for k in module.__dict__ if not k.startswith("__")]
            suggestion = ""
            if available:
                likely = [
                    k
                    for k in available
                    if isinstance(module.__dict__[k], StateGraph | Pregel)
                ]
                if likely:
                    prefix = spec.module or spec.path
                    likely_ = "\n".join(
                        [f"\t- {prefix}:{k}" if prefix else k for k in likely]
                    )
                    suggestion = (
                        f"\nDid you mean to use one of the following?\n{likely_}"
                    )
                elif available:
                    suggestion = (
                        f"\nFound the following exports: {', '.join(available)}"
                    )

            raise ValueError(
                f"Could not find graph '{spec.variable}' in '{spec.path}'. "
                f"Please check that:\n"
                f"1. The file exports a variable named '{spec.variable}'\n"
                f"2. The variable name in your config matches the export name{suggestion}"
            ) from e
        if callable(graph):
            classify_factory(graph, spec.id)
        elif isinstance(graph, StateGraph):
            graph = graph.compile()
        elif isinstance(graph, Pregel):
            # We don't want to fail real deployments, but this will help folks catch unnecessary custom components
            # before they deploy
            if lg_api_config.API_VARIANT == "local_dev":
                has_checkpointer = isinstance(graph.checkpointer, BaseCheckpointSaver)
                has_store = isinstance(graph.store, BaseStore)
                if has_checkpointer or has_store:
                    components = []
                    if has_checkpointer:
                        components.append(
                            f"checkpointer (type {type(graph.checkpointer)})"
                        )
                    if has_store:
                        components.append(f"store (type {type(graph.store)})")
                    component_list = " and ".join(components)

                    raise ValueError(
                        f"Heads up! Your graph '{spec.variable}' from '{spec.path}' includes a custom {component_list}. "
                        f"With LangGraph API, persistence is handled automatically by the platform, "
                        f"so providing a custom {component_list} here isn't necessary and will be ignored when deployed.\n\n"
                        f"To simplify your setup and use the built-in persistence, please remove the custom {component_list} "
                        f"from your graph definition. If you are looking to customize which postgres database to connect to,"
                        " please set the `POSTGRES_URI` environment variable."
                        " See https://langchain-ai.github.io/langgraph/cloud/reference/env_var/#postgres_uri_custom for more details."
                    )

        else:
            raise ValueError(
                f"Variable '{spec.variable}' in module '{spec.path}' is not a Graph or Graph factory function"
            )
    else:
        # find the graph in the module
        # - first look for a compiled graph (Pregel)
        # - if not found, look for a Graph and compile it
        for _, member in inspect.getmembers(module):
            if isinstance(member, Pregel):
                graph = member
                break
        else:
            for _, member in inspect.getmembers(module):
                if isinstance(member, StateGraph):
                    graph = member.compile()
                    break
            else:
                raise ValueError(
                    f"Could not find a Graph in module at path: {spec.path}"
                )

    _register_stream_transformers_from_module(spec.id, module)
    return graph


def _register_stream_transformers_from_module(graph_id: str, module: Any) -> None:
    """Populate ``GRAPH_STREAM_TRANSFORMERS`` from an optional module export.

    Graph authors opt in by defining a ``stream_transformers`` symbol
    at module scope. The symbol must be a zero-arg callable that returns
    a fresh list of either:

    * ``langgraph.stream.StreamTransformer`` **factories** — callables of
      the shape ``(scope: tuple[str, ...]) -> StreamTransformer``
      (``StreamTransformer`` subclasses are themselves valid factories).
      This is the preferred form and mirrors the JS
      ``streamTransformers: [createMyTransformer]`` convention.
    * ``StreamTransformer`` **instances** — back-compat form. Each bare
      instance is auto-wrapped by :func:`coerce_stream_transformer_factory`
      into a factory that returns the same instance for every mux scope,
      so a single ``custom:<name>`` channel is shared across the whole
      run (rather than fragmenting into a fresh channel per subgraph).

    Anything else is ignored (with a warning on local dev) so bad
    wiring doesn't break the graph.
    """
    factory = getattr(module, "stream_transformers", None)
    if factory is None:
        return
    if not callable(factory):
        logger.warning(
            "Module exports ``stream_transformers`` but it is not a callable — "
            "ignoring. Define it as ``def stream_transformers(): return [...]`` "
            "returning a fresh list of StreamTransformer instances per call.",
            graph_id=graph_id,
            kind=type(factory).__name__,
        )
        return
    GRAPH_STREAM_TRANSFORMERS[graph_id] = factory


@functools.lru_cache(maxsize=1)
def _get_init_embeddings() -> Callable[[str, ...], "Embeddings"] | None:
    try:
        from langchain.embeddings import (  # noqa: PLC0415  # ty: ignore[unresolved-import]
            init_embeddings,
        )

        return init_embeddings
    except ImportError:
        return None


@timing.timer(
    message="Loading embeddings {embeddings_path}",
    metadata_fn=lambda index_config: {"embeddings_path": index_config.get("embed")},
    warn_threshold_secs=5,
    warn_message="Loading embeddings '{embeddings_path}' took longer than expected",
    error_threshold_secs=10,
)
def resolve_embeddings(index_config: dict) -> "Embeddings":
    """Return embeddings from config.

    Args:
        index_config: Configuration for the vector store index
            Must contain an "embed" key specifying either:
            - A path to a Python file and function (e.g. "./embeddings.py:get_embeddings")
            - A LangChain embeddings identifier (e.g. "openai:text-embedding-3-small")

    Returns:
        Embeddings: A LangChain embeddings instance

    Raises:
        ValueError: If embeddings cannot be loaded from the config
    """
    from langchain_core.embeddings import Embeddings  # noqa: PLC0415
    from langgraph.store.base import ensure_embeddings  # noqa: PLC0415

    embed = index_config["embed"]
    if isinstance(embed, Embeddings):
        return embed
    if callable(embed):
        return ensure_embeddings(embed)
    if not isinstance(embed, str):
        raise ValueError(
            f"Embeddings config must be a string or callable, got: {type(embed).__name__}"
        )
    if ".py:" in embed:
        module_name, function = embed.rsplit(":", 1)
        module_name = module_name.rstrip(":")

        try:
            with profiled_import(embed):
                if "/" in module_name:
                    # Load from file path
                    modname = (
                        module_name.replace("/", "__")
                        .replace(".py", "")
                        .replace(" ", "_")
                    )
                    modspec = importlib.util.spec_from_file_location(
                        modname, module_name
                    )
                    if modspec is None:
                        raise ValueError(
                            f"Could not find embeddings file: {module_name}"
                        )
                    module = importlib.util.module_from_spec(modspec)
                    sys.modules[modname] = module
                    modspec.loader.exec_module(module)
                else:
                    # Load from Python module
                    module = importlib.import_module(module_name)

            embedding_fn = getattr(module, function, None)
            if embedding_fn is None:
                raise ValueError(
                    f"Could not find embeddings function '{function}' in module: {module_name}"
                )

            if isinstance(embedding_fn, Embeddings):
                return embedding_fn
            elif not callable(embedding_fn):
                raise ValueError(
                    f"Embeddings function '{function}' in module: {module_name} is not callable"
                )

            return ensure_embeddings(embedding_fn)

        except ImportError as e:
            e.add_note(f"Could not import embeddings module:\n{module_name}\n\n")
            if lg_api_config.API_VARIANT == "local_dev":
                e.add_note(
                    "If you're in development mode, make sure you've installed your project "
                    "and its dependencies:\n"
                    "- For requirements.txt: pip install -r requirements.txt\n"
                    "- For pyproject.toml: pip install -e .\n"
                )
            raise
        except FileNotFoundError as e:
            raise ValueError(f"Could not find embeddings file: {module_name}") from e

    else:
        # Load from LangChain embeddings
        init_embeddings = _get_init_embeddings()
        if init_embeddings is None:
            raise ValueError(
                f"Could not load LangChain embeddings '{embed}'. "
                "Loading embeddings by provider:identifier requires the langchain package (>=0.3.9). "
                "Install it with: pip install 'langchain>=0.3.9'"
                " or specify 'embed' as a path to a "
                "variable in a Python file instead."
            )
        # Capture warnings
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=("The function `init_embeddings` is in beta."),
            )
            return init_embeddings(embed)
