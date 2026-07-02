import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import ssl
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal, Self, cast

import certifi
import httpx
import orjson
import structlog
import uvicorn
from langchain_core.runnables.config import RunnableConfig, merge_configs
from langchain_core.runnables.graph import Edge, Node
from langchain_core.runnables.graph import Graph as DrawableGraph
from langchain_core.runnables.schema import (
    CustomStreamEvent,
    StandardStreamEvent,
    StreamEvent,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.store.base import GetOp, Item, ListNamespacesOp, PutOp, SearchOp
from langgraph.types import Command, PregelTask, Send, StateSnapshot
from langgraph_sdk import Auth
from pydantic import BaseModel
from starlette import types
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    BaseUser,
)
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection, Request
from starlette.routing import Route

from langgraph_api import store as api_store
from langgraph_api.auth.custom import DotDict, ProxyUser
from langgraph_api.config import LANGGRAPH_AUTH, LANGGRAPH_AUTH_TYPE
from langgraph_api.js.base import BaseRemotePregel, RemoteInterrupt
from langgraph_api.js.errors import RemoteException
from langgraph_api.js.sse import SSEDecoder, aiter_lines_raw
from langgraph_api.route import ApiResponse
from langgraph_api.schema import Config
from langgraph_api.serde import json_dumpb
from langgraph_api.utils import get_auth_ctx, get_user_id

logger = structlog.stdlib.get_logger(__name__)

REMOTE_PORT = 5555
GRAPH_PORT = 5556
GRAPH_HTTP_PORT = 5557
SSL = ssl.create_default_context(cafile=certifi.where())

if (port := int(os.getenv("PORT", "8080"))) and port in (GRAPH_PORT, REMOTE_PORT):
    raise ValueError(
        f"PORT={port} is a reserved port for the JS worker. Please choose a different port."
    )

_client = httpx.AsyncClient(
    base_url=f"http://localhost:{GRAPH_PORT}",
    timeout=httpx.Timeout(15.0),  # 3 x HEARTBEAT_MS
    limits=httpx.Limits(),
    transport=httpx.AsyncHTTPTransport(verify=SSL),
)


def _snapshot_defaults():
    if not hasattr(StateSnapshot, "interrupts"):
        return {}
    return {"interrupts": tuple()}


def default_command(obj):
    if isinstance(obj, Send):
        return {"node": obj.node, "args": obj.arg}
    if isinstance(obj, ProxyUser):
        return obj.dict()
    raise TypeError


def _js_sidecar_error_message(
    *,
    method: str,
    graph_id: Any,
    status_code: int,
    body: bytes,
) -> str:
    """Best-effort detail string from a non-2xx JS sidecar HTTP response."""
    if not body:
        return f"JS graph {graph_id}/{method} returned HTTP {status_code} with an empty body"
    try:
        parsed = orjson.loads(body)
    except orjson.JSONDecodeError:
        text = body.decode("utf-8", errors="replace").strip()
        return f"JS graph {graph_id}/{method} returned HTTP {status_code}: {text[:500]}"
    if isinstance(parsed, dict):
        for key in ("message", "detail", "error"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return (
                    f"JS graph {graph_id}/{method} returned HTTP {status_code}: {value}"
                )
            if isinstance(value, dict):
                nested = value.get("message") or value.get("detail")
                if isinstance(nested, str) and nested:
                    return (
                        f"JS graph {graph_id}/{method} returned HTTP {status_code}: "
                        f"{nested}"
                    )
    return (
        f"JS graph {graph_id}/{method} returned HTTP {status_code}: "
        f"{body.decode('utf-8', errors='replace')[:500]}"
    )


async def _client_stream(method: str, data: dict[str, Any]):
    graph_id = data.get("graph_id")
    async with _client.stream(
        "POST",
        f"/{graph_id}/{method}",
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
        },
        data=orjson.dumps(data, default=default_command),
    ) as response:
        if response.is_error:
            raw = await response.aread()
            raise RemoteException(
                f"HTTP_{response.status_code}",
                _js_sidecar_error_message(
                    method=method,
                    graph_id=graph_id,
                    status_code=response.status_code,
                    body=raw,
                ),
            )
        decoder = SSEDecoder()
        async for line in aiter_lines_raw(response):
            sse = decoder.decode(line)
            if sse is not None:
                if sse.event == "error":
                    raise RemoteException(sse.data["error"], sse.data["message"])
                yield sse.data


class NoopModel(BaseModel):
    pass


async def _client_invoke(method: str, data: dict[str, Any]):
    graph_id = data.get("graph_id")
    res = await _client.post(
        f"/{graph_id}/{method}",
        headers={"Content-Type": "application/json"},
        data=orjson.dumps(data, default=default_command),
    )
    try:
        result = res.json()
    except json.JSONDecodeError as e:
        # Get the raw response for debugging
        raw = res.text.strip()
        if not raw:
            raise RemoteException(
                "EmptyResponse",
                "Graph returned empty response",
            ) from e
        logger.exception(
            "JS graph returned invalid JSON for method call %s: %s... (truncated)",
            method,
            raw[:50],
        )
        raise RemoteException(
            "InvalidJSON",
            "Graph returned invalid JSON",
        ) from e
    # Check if the JS side returned an error
    if isinstance(result, dict) and result.get("__error__"):
        raise RemoteException(result.get("error", "Unknown"), result.get("message", ""))
    return result


class RemotePregel(BaseRemotePregel):
    def __init__(
        self,
        graph_id: str,
        *,
        config: Config | None = None,
        **kwargs: Any,
    ):
        super().__init__()
        self.graph_id = graph_id
        self.config = config

    async def astream_events(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        version: Literal["v1", "v2"],
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if version != "v2":
            raise ValueError("Only v2 of astream_events is supported")

        data = {
            "graph_id": self.graph_id,
            "graph_config": self.config,
            "graph_name": self.name,
            "command" if isinstance(input, Command) else "input": input,
            "config": config,
            **kwargs,
        }

        async for event in _client_stream("streamEvents", data):
            if event["event"] == "on_custom_event":
                yield CustomStreamEvent(**event)
            else:
                yield StandardStreamEvent(**event)

    async def astream_protocol_events(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream native Protocol v2 (``ProtocolEvent``) frames from the sidecar.

        Used only for Protocol v2 event-streaming runs. Requests
        ``stream_protocol: "v3"`` so the sidecar drives the graph through
        LangGraphJS's native v3 stream and forwards protocol-aligned events
        (``{"type": "event", "method", "params"}``) instead of legacy
        ``on_chain_stream`` chunks. The worker feeds these straight into
        ``EventStreamingSession`` via ``normalize_protocol_event``, matching
        the native-graph v3 path. Legacy runs keep using
        :meth:`astream_events` (``version="v2"``).
        """
        data = {
            "graph_id": self.graph_id,
            "graph_config": self.config,
            "graph_name": self.name,
            "command" if isinstance(input, Command) else "input": input,
            "config": config,
            "stream_protocol": "v3",
            **kwargs,
        }

        async for event in _client_stream("streamEvents", data):
            yield event

    async def fetch_state_schema(self):
        return await _client_invoke("getSchema", {"graph_id": self.graph_id})

    async def fetch_graph(
        self,
        config: RunnableConfig | None = None,
        *,
        xray: int | bool = False,
    ) -> DrawableGraph:
        response = await _client_invoke(
            "getGraph",
            {
                "graph_id": self.graph_id,
                "graph_config": self._inject_auth_to_config(self.config),
                "graph_name": self.name,
                "config": self._inject_auth_to_config(config),
                "xray": xray,
            },
        )

        nodes: list[Any] = response.pop("nodes")
        edges: list[Any] = response.pop("edges")

        return DrawableGraph(
            {
                data["id"]: Node(
                    data["id"], data["id"], NoopModel, data.get("metadata")
                )
                for data in nodes
            },
            list(
                {
                    Edge(
                        data["source"],
                        data["target"],
                        data.get("data"),
                        data.get("conditional", False),
                    )
                    for data in edges
                }
            ),
        )

    async def fetch_subgraphs(
        self,
        *,
        namespace: str | None = None,
        config: RunnableConfig | None = None,
        recurse: bool = False,
    ) -> dict[str, dict]:
        return await _client_invoke(
            "getSubgraphs",
            {
                "graph_id": self.graph_id,
                "graph_config": self._inject_auth_to_config(self.config),
                "graph_name": self.name,
                "namespace": namespace,
                "recurse": recurse,
                "config": self._inject_auth_to_config(config),
            },
        )

    def _convert_state_snapshot(self, item: dict) -> StateSnapshot:
        def _convert_tasks(tasks: list[dict]) -> tuple[PregelTask, ...]:
            result: list[PregelTask] = []
            for task in tasks:
                state = task.get("state")

                if state and isinstance(state, dict) and "config" in state:
                    state = self._convert_state_snapshot(state)

                interrupts: list[RemoteInterrupt] = []
                if task_interrupts := task.get("interrupts"):
                    interrupts = [RemoteInterrupt(raw=i) for i in task_interrupts]

                result.append(
                    PregelTask(
                        task["id"],
                        task["name"],
                        tuple(task["path"]) if task.get("path") else tuple(),
                        # TODO: figure out how to properly deserialise errors
                        task.get("error"),
                        tuple(interrupts),
                        state,
                        task.get("result"),
                    )
                )
            return tuple(result)

        return StateSnapshot(
            item.get("values"),
            cast("tuple", item.get("next", ())),
            item.get("config"),
            item.get("metadata"),
            item.get("createdAt"),
            item.get("parentConfig"),
            _convert_tasks(item.get("tasks", [])),
            # TODO: add handling of interrupts when multiple resumes land in JS
            **_snapshot_defaults(),
        )

    async def aget_state(
        self, config: RunnableConfig, *, subgraphs: bool = False
    ) -> StateSnapshot:
        return self._convert_state_snapshot(
            await _client_invoke(
                "getState",
                {
                    "graph_id": self.graph_id,
                    "graph_config": self._inject_auth_to_config(self.config),
                    "graph_name": self.name,
                    "config": self._inject_auth_to_config(config),
                    "subgraphs": subgraphs,
                },
            )
        )

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, Any] | Any,
        as_node: str | None = None,
    ) -> RunnableConfig:
        response = await _client_invoke(
            "updateState",
            {
                "graph_id": self.graph_id,
                "graph_config": self._inject_auth_to_config(self.config),
                "graph_name": self.name,
                "config": self._inject_auth_to_config(config),
                "values": values,
                "as_node": as_node,
            },
        )
        return RunnableConfig(**response)

    async def aget_state_history(
        self,
        config: RunnableConfig,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[StateSnapshot]:
        async for event in _client_stream(
            "getStateHistory",
            {
                "graph_id": self.graph_id,
                "graph_config": self._inject_auth_to_config(self.config),
                "graph_name": self.name,
                "config": self._inject_auth_to_config(config),
                "limit": limit,
                "filter": filter,
                "before": before,
            },
        ):
            yield self._convert_state_snapshot(event)

    def get_graph(
        self,
        config: RunnableConfig | None = None,
        *,
        xray: int | bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError()

    def get_input_schema(self, config: RunnableConfig | None = None) -> type[BaseModel]:
        raise NotImplementedError()

    def get_output_schema(
        self, config: RunnableConfig | None = None
    ) -> type[BaseModel]:
        raise NotImplementedError()

    def config_schema(self) -> type[BaseModel]:
        raise NotImplementedError()

    def get_context_jsonschema(self) -> dict:
        raise NotImplementedError()

    async def invoke(self, input: Any, config: RunnableConfig | None = None):
        raise NotImplementedError()

    def copy(self, update: dict[str, Any] | None = None) -> Self:
        attrs = {**self.__dict__, **(update or {})}
        return self.__class__(**attrs)

    async def fetch_nodes_executed(self):
        result = await _client_invoke("getNodesExecuted", {"graph_id": self.graph_id})
        await logger.adebug(
            f"Fetched {result['nodesExecuted']} nodes executed",
            nodes_executed=result["nodesExecuted"],
            graph_id=self.graph_id,
        )
        return result["nodesExecuted"]

    def _inject_auth_to_config(self, config: RunnableConfig | Config):
        if ctx := get_auth_ctx():
            user_id = get_user_id(cast("BaseUser | None", ctx.user))

            # Skip if cannot serialize the user to JSON
            if not hasattr(ctx.user, "model_dump"):
                return config

            return merge_configs(
                config,
                {
                    "configurable": {
                        "langgraph_auth_user": ctx.user,
                        "langgraph_auth_user_id": user_id,
                        "langgraph_auth_permissions": list(ctx.permissions),
                    }
                },
            )

        return config


async def run_js_process(paths_str: str | None, watch: bool = False):
    # check if tsx is available
    tsx_path = shutil.which("tsx")
    if tsx_path is None:
        raise FileNotFoundError(
            "tsx not found in PATH. Please upgrade to latest LangGraph CLI to support running JS graphs."
        )
    attempt = 0
    while (current_task := asyncio.current_task()) and not current_task.cancelled():
        client_file = os.path.join(os.path.dirname(__file__), "client.mts")
        client_preload_file = os.path.join(
            os.path.dirname(__file__), "src", "preload.mjs"
        )

        args = (
            (
                "tsx",
                "watch",
                "--import",
                client_preload_file,
                client_file,
                "--skip-schema-cache",
            )
            if False
            else ("tsx", "--import", client_preload_file, client_file)
        )
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                env={
                    "LANGSERVE_GRAPHS": paths_str,
                    "LANGCHAIN_CALLBACKS_BACKGROUND": "true",
                    "NODE_ENV": "development" if watch else "production",
                    "CHOKIDAR_USEPOLLING": "true",
                    **os.environ,
                },
            )
            logger.info(
                "Started JS graphs process",
                pid=process.pid,
                attempt=attempt,
                args=args,
            )
            code = await process.wait()
            logger.error(
                "JS graphs process exited unexpectedly",
                pid=process.pid,
                exit_code=code,
                attempt=attempt,
            )
            raise Exception(f"JS process exited with code {code}")
        except asyncio.CancelledError:
            logger.info("Terminating JS graphs process")
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                    await process.wait()
            raise
        except Exception:
            if attempt >= 3:
                raise
            else:
                logger.warning(f"Retrying JS process {3 - attempt} more times...")
                attempt += 1


async def run_js_http_process(
    paths_str: str | None, http_config: dict, watch: bool = False
):
    # check if tsx is available
    tsx_path = shutil.which("tsx")
    if tsx_path is None:
        raise FileNotFoundError(
            "tsx not found in PATH. Please upgrade to latest LangGraph CLI to support running JS graphs."
        )

    attempt = 0
    while (current_task := asyncio.current_task()) and not current_task.cancelled():
        client_file = os.path.join(os.path.dirname(__file__), "client.http.mts")
        args = ("tsx", "watch", client_file) if watch else ("tsx", client_file)
        pid = None
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                env={
                    "LANGGRAPH_HTTP": orjson.dumps(http_config),
                    "LANGSERVE_GRAPHS": paths_str,
                    "LANGCHAIN_CALLBACKS_BACKGROUND": "true",
                    "NODE_ENV": "development" if watch else "production",
                    "CHOKIDAR_USEPOLLING": "true",
                    **os.environ,
                },
            )

            pid = process.pid
            logger.info(
                "Started JS HTTP process",
                pid=pid,
                attempt=attempt,
                args=args,
            )

            code = await process.wait()
            logger.error(
                "JS HTTP process exited unexpectedly",
                pid=pid,
                exit_code=code,
                attempt=attempt,
            )
            raise Exception(f"JS HTTP process exited with code {code}")

        except asyncio.CancelledError:
            logger.info("Shutting down JS HTTP process [%d]", pid or -1)
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                    await process.wait()
            raise
        except Exception:
            if attempt >= 3:
                logger.exception("JS HTTP process failed")
                raise
            else:
                logger.warning(f"Retrying JS HTTP process {3 - attempt} more times...")
                attempt += 1


_BAD_SURROGATE_RE = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
_BAD_ESCAPE_RE = re.compile(r"\\(?![\"\\/bfnrtu])")


def _safe_json_loads(data: bytes):
    """Attempt *orjson.loads* first; if it fails, repair common escape issues.

    For a time, we had a bug in our surrogate cleanup in serde.py, which
    allowed sequences containing a stray backslash to be stored which would
    then fail upon loading. This function attempts to repair those sequences.
    """
    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError:
        txt = data.decode("utf-8", "replace")
        txt = _BAD_ESCAPE_RE.sub(r"\\\\", txt)
        txt = _BAD_SURROGATE_RE.sub("", txt)
        return json.loads(txt)


class PassthroughSerialiser(SerializerProtocol):
    def dumps(self, obj: Any) -> bytes:
        return json_dumpb(obj)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "json", json_dumpb(obj)

    def loads(self, data: bytes) -> Any:
        return _safe_json_loads(data)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type, payload = data
        if type != "json":
            raise ValueError(f"Unsupported type {type}")
        return _safe_json_loads(payload)


async def _get_passthrough_checkpointer():
    from langgraph_api import _checkpointer as api_checkpointer  # noqa: PLC0415

    checkpointer = await api_checkpointer.get_checkpointer()
    # This checkpointer does not attempt to revive LC-objects.
    # Instead, it will pass through the JSON values as-is.
    checkpointer.serde = PassthroughSerialiser()

    return checkpointer


async def _get_passthrough_store():
    return await api_store.get_store()


# Setup a HTTP server on top of CHECKPOINTER_SOCKET unix socket
# used by `client.mts` to communicate with the Python checkpointer
async def run_remote_checkpointer():
    async def checkpointer_list(payload: dict):
        """Search checkpoints"""

        result = []
        checkpointer = await _get_passthrough_checkpointer()
        async for item in checkpointer.alist(
            config=payload.get("config"),
            limit=int(payload.get("limit") or 10),
            before=payload.get("before"),
            filter=payload.get("filter"),
        ):
            result.append(item)

        return result

    async def checkpointer_put(payload: dict):
        """Put the new checkpoint metadata"""

        checkpointer = await _get_passthrough_checkpointer()
        return await checkpointer.aput(
            payload["config"],
            payload["checkpoint"],
            payload["metadata"],
            payload.get("new_versions", {}),
        )

    async def checkpointer_get_tuple(payload: dict):
        """Get actual checkpoint values (reads)"""
        checkpointer = await _get_passthrough_checkpointer()
        return await checkpointer.aget_tuple(config=payload["config"])

    async def checkpointer_put_writes(payload: dict):
        """Put actual checkpoint values (writes)"""

        checkpointer = await _get_passthrough_checkpointer()
        return await checkpointer.aput_writes(
            payload["config"],
            payload["writes"],
            payload["taskId"],
        )

    async def store_batch(payload: dict):
        """Batch operations on the store"""
        operations = payload.get("operations", [])

        if not operations:
            raise ValueError("No operations provided")

        # Convert raw operations to proper objects
        processed_operations = []
        for op in operations:
            if "value" in op:
                processed_operations.append(
                    PutOp(
                        namespace=tuple(op["namespace"]),
                        key=op["key"],
                        value=op["value"],
                    )
                )
            elif "namespace_prefix" in op:
                processed_operations.append(
                    SearchOp(
                        namespace_prefix=tuple(op["namespace_prefix"]),
                        filter=op.get("filter"),
                        limit=op.get("limit", 10),
                        offset=op.get("offset", 0),
                    )
                )

            elif "namespace" in op and "key" in op:
                processed_operations.append(
                    GetOp(namespace=tuple(op["namespace"]), key=op["key"])
                )
            elif "match_conditions" in op:
                processed_operations.append(
                    ListNamespacesOp(
                        match_conditions=tuple(op["match_conditions"]),
                        max_depth=op.get("max_depth"),
                        limit=int(op.get("limit", 100)),
                        offset=op.get("offset", 0),
                    )
                )
            else:
                raise ValueError(f"Unknown operation type: {op}")

        store = await _get_passthrough_store()
        results = await store.abatch(processed_operations)

        # Handle potentially undefined or non-dict results
        processed_results = []
        # Result is of type: Union[Item, list[Item], list[tuple[str, ...]], None]
        for result in results:
            if isinstance(result, Item):
                processed_results.append(result.dict())
            elif isinstance(result, dict):
                processed_results.append(result)
            elif isinstance(result, list):
                coerced = []
                for res in result:
                    if isinstance(res, Item):
                        coerced.append(res.dict())
                    elif isinstance(res, tuple):
                        coerced.append(list(res))
                    elif res is None:
                        coerced.append(res)
                    else:
                        coerced.append(str(res))
                processed_results.append(coerced)
            elif result is None:
                processed_results.append(None)
            else:
                processed_results.append(str(result))
        return processed_results

    async def store_get(payload: dict):
        """Get store data"""
        namespaces = payload.get("namespace")
        key = payload.get("key")

        if not namespaces or not key:
            raise ValueError("Both namespaces and key are required")

        namespaces = tuple(namespaces)

        store = await _get_passthrough_store()
        result = await store.aget(namespaces, key)

        return result

    async def store_put(payload: dict):
        """Put the new store data"""

        namespace = tuple(payload["namespace"])
        key = payload["key"]
        value = payload["value"]
        index = payload.get("index")

        store = await _get_passthrough_store()
        await store.aput(namespace, key, value, index=index)

        return {"success": True}

    async def store_search(payload: dict):
        """Search stores"""
        namespace_prefix = tuple(payload["namespace_prefix"])
        filter = payload.get("filter")
        limit = payload.get("limit", 10)
        offset = payload.get("offset", 0)
        query = payload.get("query")

        store = await _get_passthrough_store()
        result = await store.asearch(
            namespace_prefix, filter=filter, limit=limit, offset=offset, query=query
        )

        return [item.dict() for item in result]

    async def store_delete(payload: dict):
        """Delete store data"""

        namespace = tuple(payload["namespace"])
        key = payload["key"]

        store = await _get_passthrough_store()
        await store.adelete(namespace, key)

        return {"success": True}

    async def store_list_namespaces(payload: dict):
        """List all namespaces"""
        prefix = tuple(payload.get("prefix", [])) or None
        suffix = tuple(payload.get("suffix", [])) or None
        max_depth = payload.get("max_depth")
        limit = payload.get("limit", 100)
        offset = payload.get("offset", 0)

        store = await _get_passthrough_store()
        result = await store.alist_namespaces(
            prefix=prefix,
            suffix=suffix,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
        )

        return [list(ns) for ns in result]

    def wrap_handler(cb):
        async def wrapped(request: Request):
            try:
                payload = orjson.loads(await request.body())
                return ApiResponse(await cb(payload))
            except ValueError as exc:
                await logger.aexception(
                    "ValueError calling remote handler", exc_info=exc
                )
                return ApiResponse({"error": str(exc)}, status_code=400)
            except Exception as exc:
                await logger.aexception("Error calling remote handler", exc_info=exc)
                return ApiResponse({"error": str(exc)}, status_code=500)

        return wrapped

    remote = Starlette(
        routes=[
            Route(
                "/checkpointer_get_tuple",
                wrap_handler(checkpointer_get_tuple),
                methods=["POST"],
            ),
            Route(
                "/checkpointer_list", wrap_handler(checkpointer_list), methods=["POST"]
            ),
            Route(
                "/checkpointer_put", wrap_handler(checkpointer_put), methods=["POST"]
            ),
            Route(
                "/checkpointer_put_writes",
                wrap_handler(checkpointer_put_writes),
                methods=["POST"],
            ),
            Route("/store_get", wrap_handler(store_get), methods=["POST"]),
            Route("/store_put", wrap_handler(store_put), methods=["POST"]),
            Route("/store_delete", wrap_handler(store_delete), methods=["POST"]),
            Route("/store_search", wrap_handler(store_search), methods=["POST"]),
            Route(
                "/store_list_namespaces",
                wrap_handler(store_list_namespaces),
                methods=["POST"],
            ),
            Route("/store_batch", wrap_handler(store_batch), methods=["POST"]),
            Route("/ok", lambda _: ApiResponse({"ok": True}), methods=["GET"]),
        ]
    )

    server = uvicorn.Server(
        uvicorn.Config(
            remote,
            port=REMOTE_PORT,
            # We need to _explicitly_ set these values in order
            # to avoid reinitialising the logger, which removes
            # the structlog logger setup before.
            # See: https://github.com/encode/uvicorn/blob/8f4c8a7f34914c16650ebd026127b96560425fde/uvicorn/config.py#L357-L393
            log_config=None,
            log_level=None,
            access_log=True,
        )
    )
    await server.serve()


class DisableHttpxLoggingContextManager(contextlib.AbstractContextManager):
    """
    Disable HTTP/1.1 200 OK logs spamming stdout.
    """

    filter: logging.Filter

    def __init__(self, filter: Callable[[logging.LogRecord], bool] | None = None):
        self.filter = filter or (lambda record: "200 OK" not in record.getMessage())

    def __enter__(self):
        logging.getLogger("httpx").addFilter(self.filter)

    def __exit__(self, exc_type, exc_value, traceback):
        logging.getLogger("httpx").removeFilter(self.filter)


async def wait_until_js_ready(
    bg_tasks: set[asyncio.Task] | None = None,
):
    from langgraph_api.config import JS_READY_TIMEOUT_SECS  # noqa: PLC0415

    poll_interval = 0.5
    max_attempts = int(JS_READY_TIMEOUT_SECS / poll_interval)

    with DisableHttpxLoggingContextManager():
        async with (
            httpx.AsyncClient(
                base_url=f"http://localhost:{GRAPH_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as graph_client,
            httpx.AsyncClient(
                base_url=f"http://localhost:{REMOTE_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as checkpointer_client,
        ):
            attempt = 0
            graph_ready = False
            while (
                current_task := asyncio.current_task()
            ) and not current_task.cancelled():
                if bg_tasks:
                    for task in bg_tasks:
                        if task.done():
                            exc = task.exception() if not task.cancelled() else None
                            logger.error(
                                "JS background task finished while waiting for"
                                " JS ready",
                                task_name=task.get_name(),
                                task_cancelled=task.cancelled(),
                                task_exception=repr(exc) if exc else None,
                            )
                            raise RuntimeError(
                                f"JS background task '{task.get_name()}' died"
                                " during startup"
                            ) from exc
                try:
                    if not graph_ready:
                        res = await graph_client.get("/ok")
                        res.raise_for_status()
                        graph_ready = True
                    res = await checkpointer_client.get("/ok")
                    res.raise_for_status()
                    return
                except httpx.HTTPError as exc:
                    if attempt > max_attempts:
                        logger.exception(
                            "Timed out waiting for JS process to become ready",
                            attempts=attempt,
                            timeout_secs=JS_READY_TIMEOUT_SECS,
                            graph_ready=graph_ready,
                        )
                        raise
                    else:
                        if attempt % 20 == 0 and attempt > 0:
                            logger.warning(
                                "Still waiting for JS process to become ready",
                                attempts=attempt,
                                elapsed_seconds=attempt * poll_interval,
                                timeout_secs=JS_READY_TIMEOUT_SECS,
                                graph_ready=graph_ready,
                                last_error=repr(exc),
                            )
                        attempt += 1
                        await asyncio.sleep(poll_interval)


async def js_healthcheck():
    with DisableHttpxLoggingContextManager():
        async with (
            httpx.AsyncClient(
                base_url=f"http://localhost:{GRAPH_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as graph_client,
            httpx.AsyncClient(
                base_url=f"http://localhost:{REMOTE_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as checkpointer_client,
        ):
            graph_passed = False
            try:
                res = await graph_client.get("/ok")
                res.raise_for_status()
                graph_passed = True
                res = await checkpointer_client.get("/ok")
                res.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                logger.warning(
                    "JS healthcheck failed. Either the JS server is not running or the event loop is blocked by a CPU-intensive task.",
                    graph_passed=graph_passed,
                    error=exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="JS healthcheck failed. Either the JS server is not running or the event loop is blocked by a CPU-intensive task.",
                ) from exc


class CustomJsAuthBackend(AuthenticationBackend):
    ls_auth: AuthenticationBackend | None

    def __init__(self, disable_studio_auth: bool = False):
        from langgraph_api.utils.cache import LRUCache  # noqa: PLC0415

        self.ls_auth = None
        if not disable_studio_auth and LANGGRAPH_AUTH_TYPE == "langsmith":
            from langgraph_api.auth.langsmith.backend import (  # noqa: PLC0415
                LangsmithAuthBackend,
            )

            self.ls_auth = LangsmithAuthBackend()
        self.ttl_cache: LRUCache | None = None
        self.cache_keys: list[str] | None = None
        if LANGGRAPH_AUTH is None:
            raise ValueError("LANGGRAPH_AUTH is not set")
        if cache := LANGGRAPH_AUTH.get("cache"):
            keys = cache.get("cache_keys", [])
            if not isinstance(keys, list):
                raise ValueError(
                    f"LANGGRAPH_AUTH.cache.cache_keys must be a list. Got: {keys}"
                )
            self.cache_keys = [key.lower() for key in keys]
            self.ttl_cache = LRUCache(
                max_size=cache.get("max_size", 1000),
                ttl=cache.get("ttl_seconds", 60),
            )

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        if self.ls_auth is not None and (
            (auth_scheme := conn.headers.get("x-auth-scheme"))
            and auth_scheme == "langsmith"
        ):
            return await self.ls_auth.authenticate(conn)

        headers = dict(conn.headers)
        # need to remove content-length to prevent confusing the HTTP client
        headers.pop("content-length", None)
        headers["x-langgraph-auth-url"] = str(conn.url)
        headers["x-langgraph-auth-method"] = conn.scope.get("method")
        cache_key = None
        if self.cache_keys:
            cache_key = tuple((k, headers[k]) for k in self.cache_keys if k in headers)
            if cache_key and self.ttl_cache is not None:
                cached = await self.ttl_cache.get(cache_key)
                if cached:
                    return cached

        res = await _client.post("/auth/authenticate", headers=headers)
        data = res.json()

        if data.get("error"):
            status = data.get("status") or 403
            headers = data.get("headers")
            message = data.get("message") or "Unauthorized"

            raise HTTPException(status_code=status, detail=message, headers=headers)
        result = AuthCredentials(data["scopes"]), ProxyUser(DotDict(data["user"]))
        if cache_key:
            self.ttl_cache.set(cache_key, result)

        return result


async def handle_js_auth_event(
    ctx: Auth.types.AuthContext,
    value: dict,
) -> Auth.types.FilterType | None:
    if hasattr(ctx.user, "dict") and callable(ctx.user.dict):
        user = ctx.user.dict()
    else:
        user = {
            "is_authenticated": ctx.user.is_authenticated,
            "display_name": ctx.user.display_name,
            "identity": ctx.user.identity,
            "permissions": ctx.user.permissions,
        }

    res = await _client.post(
        "/auth/authorize",
        headers={"Content-Type": "application/json"},
        data=json_dumpb(
            {
                "resource": ctx.resource,
                "action": ctx.action,
                "value": value,
                "context": (
                    {
                        "user": user,
                        "scopes": ctx.permissions,
                    }
                    if ctx
                    else None
                ),
            }
        ),
    )

    response = res.json()

    if response.get("error"):
        status = response.get("status") or 403
        headers = response.get("headers")
        message = response.get("message") or "Unauthorized"

        raise HTTPException(status_code=status, detail=message, headers=headers)

    filters = cast("Auth.types.FilterType | None", response.get("filters"))

    # mutate metadata in value if applicable
    # we need to preserve the identity of the object, so cannot create a new
    # dictionary, otherwise the changes will not persist
    metadata = None
    if (
        isinstance(value, dict)
        and (updated_value := response.get("value"))
        and isinstance(value.get("metadata"), dict)
        and (metadata := updated_value.get("metadata"))
    ):
        value["metadata"].update(metadata)

    return filters


class JSCustomHTTPProxyMiddleware:
    def __init__(self, app: types.ASGIApp) -> None:
        self.app = app
        self.proxy_client = httpx.AsyncClient(
            base_url=f"http://localhost:{GRAPH_HTTP_PORT}",
            timeout=httpx.Timeout(None),
            limits=httpx.Limits(),
            transport=httpx.AsyncHTTPTransport(verify=SSL),
        )

    async def __call__(
        self, scope: types.Scope, receive: types.Receive, send: types.Send
    ) -> None:
        if scope["type"] != "http" or "__langgraph_check" in scope["path"]:
            # TODO: add support for proxying `websockets``
            await self.app(scope, receive, send)
            return

        # First, check if the request can be handled by the JS server
        with DisableHttpxLoggingContextManager(
            filter=lambda record: "__langgraph_check" not in record.getMessage()
        ):
            res = await self.proxy_client.options(
                "/__langgraph_check",
                headers={
                    "x-langgraph-method": scope["method"],
                    "x-langgraph-path": scope["path"],
                },
            )

        input_buffer: deque[types.Message] = deque()

        async def yield_to_python(node_request: httpx.Response):
            nonlocal input_buffer

            async def replay_request():
                if input_buffer:
                    if node_request.headers.get("x-langgraph-body") == "true":
                        input_buffer.clear()
                        return {
                            "type": "http.request",
                            "body": await node_request.aread(),
                            "more_body": False,
                        }

                    return input_buffer.popleft()
                else:
                    return await receive()

            async def send_with_extra_headers(message: types.Message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    for k, v in node_request.headers.items():
                        if k in (
                            "content-length",
                            "content-encoding",
                            "content-type",
                            "transfer-encoding",
                            "connection",
                            "keep-alive",
                            "x-langgraph-body",
                            "x-langgraph-status",
                        ):
                            continue

                        # Respect existing headers set by the Python server
                        headers.append(k, headers.get(k, None) or v)

                await send(message)

            return await self.app(scope, replay_request, send_with_extra_headers)

        # If the JS server does not handle the request, yield the control back to the
        # Python server.
        if not res.is_success:
            return await yield_to_python(res)

        # Stream request body
        async def upload_request_body() -> AsyncIterator[bytes]:
            nonlocal input_buffer

            more_body = True
            while more_body:
                message = await receive()
                input_buffer.append(message)

                more_body = message.get("more_body", False)
                yield message.get("body", b"")

        # Make the proxied request
        async with self.proxy_client.stream(
            scope["method"],
            scope["path"],
            params=scope["query_string"],
            headers={
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in scope["headers"]
                if k.lower() not in (b"host", b"content-length")
            },
            content=upload_request_body(),
        ) as response:
            if (
                response.status_code == 404
                and response.headers.get("x-langgraph-status") == "not-found"
            ):
                return await yield_to_python(response)

            # Send the response headers
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": [
                        (k.encode("latin-1"), v.encode("latin-1"))
                        for k, v in response.headers.items()
                        if k.lower() not in (b"transfer-encoding",)
                    ],
                }
            )

            # Stream the response body
            async for chunk in response.aiter_raw():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )

            # Send the final empty chunk to indicate the end of the response
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.proxy_client.aclose()
