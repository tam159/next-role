"""Factory classification, runtime construction, and dispatch helpers."""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from langgraph_sdk.runtime import (
    ServerRuntime,
    _ExecutionRuntime,
    _ReadRuntime,
)

from langgraph_api.schema import Config

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore


AccessContext = Literal[
    "threads.create_run",
    "threads.update",
    "threads.read",
    "assistants.read",
]


_HOOK_TYPE = Callable[[Config, ServerRuntime], dict]
FACTORY_KWARGS: dict[str, _HOOK_TYPE] = {}

# Concrete runtime classes for issubclass checks.
_RUNTIME_CLASSES: tuple[type, ...] = (_ExecutionRuntime, _ReadRuntime)

# Namespace for resolving string annotations in factory functions.
# Allows users to import ServerRuntime inside TYPE_CHECKING blocks.
_RUNTIME_LOCALNS: dict[str, Any] = {
    "ServerRuntime": ServerRuntime,
    "RunnableConfig": Config,
    "Config": Config,
}


def classify_factory(fn: Callable, graph_id: str) -> None:
    if graph_id in FACTORY_KWARGS:
        return
    _hook = _classify_factory(fn)
    if _hook is not None:
        FACTORY_KWARGS[graph_id] = _hook


def _is_runtime_annotation(annotation: Any) -> bool:
    """Check if a resolved type annotation refers to ServerRuntime.

    Handles:
    - The ``ServerRuntime`` TypeAliasType directly
    - Parameterized forms like ``ServerRuntime[MyContext]``
    - Concrete runtime classes (``_ExecutionRuntime``, ``_ReadRuntime``)
      and their subclasses
    - ``Annotated[ServerRuntime, ...]`` wrappers
    """
    if annotation is inspect.Parameter.empty:
        return False
    # Identity check against the ServerRuntime TypeAliasType
    if annotation is ServerRuntime:
        return True
    # issubclass check against concrete runtime classes
    if isinstance(annotation, type):
        return issubclass(annotation, _RUNTIME_CLASSES)
    # Handle parameterized types (ServerRuntime[MyContext], Annotated[...])
    origin = get_origin(annotation)
    if origin is not None:
        if origin is ServerRuntime:
            return True
        # For Annotated[ServerRuntime, ...], recurse on the base type
        args = get_args(annotation)
        if args:
            return _is_runtime_annotation(args[0])
    return False


def _resolve_hints(fn: Callable) -> dict[str, Any]:
    """Resolve string annotations using the function's module globals + runtime types."""
    try:
        return typing.get_type_hints(fn, localns=_RUNTIME_LOCALNS)
    except Exception:
        return {}


def _classify_factory(
    fn: Callable,
) -> _HOOK_TYPE | None:
    """Classify a graph factory by its parameter signature.

    Supports 4 variants:
    - 0 params: ``def make_graph() -> Graph``
    - 1 param (config): ``def make_graph(config) -> Graph``
    - 1 param (runtime): ``def make_graph(runtime: ServerRuntime) -> Graph``
    - 2 params (either order): ``def make_graph(config, runtime: ServerRuntime)``
      or ``def make_graph(runtime: ServerRuntime, config)``

    For 2-param factories, both arguments are always passed by keyword,
    so parameter order does not matter.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    hints = _resolve_hints(fn)

    def _annotation(p: inspect.Parameter) -> Any:
        return hints.get(p.name, p.annotation)

    if len(params) == 0:
        return {}
    elif len(params) == 1:
        if _is_runtime_annotation(_annotation(params[0])):
            return lambda config, runtime: {params[0].name: runtime}
        return lambda config, runtime: {params[0].name: config}
    elif len(params) == 2:
        # Detect which param is runtime by annotation; the other is config.
        rt_indices = [
            i for i, p in enumerate(params) if _is_runtime_annotation(_annotation(p))
        ]
        if len(rt_indices) == 1:
            rt_idx = rt_indices[0]
            cfg_idx = 1 - rt_idx
        else:
            raise ValueError(
                f"Graph factory {fn} can only accept arguments of type "
                f"ServerRuntime and/or RunnableConfig, got {[p.annotation for p in params]}"
            )
        return lambda config, runtime: {
            params[rt_idx].name: runtime,
            params[cfg_idx].name: config,
        }
    else:
        raise ValueError(
            f"Graph factory {fn} must take 0, 1, or 2 arguments. "
            f"Got {len(params)} parameters: {[p.name for p in params]}"
        )


def is_factory(graph_id: str) -> bool:
    return graph_id in FACTORY_KWARGS


def is_for_execution(access_context: AccessContext) -> bool:
    return access_context == "threads.create_run"


def build_server_runtime(
    access_context: AccessContext,
    store: BaseStore,
) -> ServerRuntime:
    """Construct the appropriate ServerRuntime variant for the access context."""
    from langgraph_api.utils import get_auth_ctx  # noqa: PLC0415

    auth_ctx = get_auth_ctx()
    user = auth_ctx.user if auth_ctx else None
    if is_for_execution(access_context):
        return _ExecutionRuntime(
            access_context=access_context,
            user=user,
            store=store,
        )
    return _ReadRuntime(
        access_context=access_context,
        user=user,
        store=store,
    )


def invoke_factory(
    value: Callable,
    graph_id: str,
    config: Config,
    server_runtime: ServerRuntime,
) -> Any:
    """Dispatch a graph factory call based on its classified arity."""
    hook = FACTORY_KWARGS.get(graph_id)
    if not hook:
        return value()
    graph_kwargs = hook(config, server_runtime)
    return value(**graph_kwargs)
