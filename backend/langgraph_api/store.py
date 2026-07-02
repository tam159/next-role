import asyncio
import importlib.util
import sys
import threading
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from random import choice
from typing import Any

import structlog
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel
from langgraph.store.base import BaseStore

from langgraph_api import config, timing
from langgraph_api.asyncio import as_asynccontextmanager
from langgraph_api.timing import profiled_import
from langgraph_api.utils.config import run_in_executor

logger = structlog.stdlib.get_logger(__name__)

CUSTOM_STORE: BaseStore | Callable[[], BaseStore] | None = None
STORE_STACK = threading.local()


async def get_store() -> BaseStore:
    if CUSTOM_STORE:
        if not hasattr(STORE_STACK, "stack"):
            stack = AsyncExitStack()
            STORE_STACK.stack = stack
            store = await stack.enter_async_context(_yield_store(CUSTOM_STORE))
            STORE_STACK.store = store
            await logger.ainfo(f"Using custom store: {store}", kind=str(type(store)))
            return store
        return STORE_STACK.store
    else:
        from langgraph_runtime.store import Store  # noqa: PLC0415

        return Store()


async def exit_store():
    if not CUSTOM_STORE:
        return
    if not hasattr(STORE_STACK, "stack"):
        return
    await STORE_STACK.stack.aclose()


@asynccontextmanager
async def _yield_store(value: Any):
    if isinstance(value, BaseStore):
        yield value
    elif (
        hasattr(value, "__aenter__")
        or hasattr(value, "__enter__")
        or asyncio.iscoroutine(value)
    ):
        async with as_asynccontextmanager(value) as ctx_value:
            yield ctx_value
    elif callable(value):
        async with _yield_store(value()) as ctx_value:
            yield ctx_value
    else:
        raise ValueError(
            f"Unsupported store type: {type(value)}. Expected an instance of BaseStore "
            "or a function or async generator that returns one."
        )


async def collect_store_from_env() -> None:
    global CUSTOM_STORE
    store_path = None
    if not config.STORE_CONFIG or not (store_path := config.STORE_CONFIG.get("path")):
        return
    await logger.ainfo(
        f"Heads up! You are configuring a custom long-term memory store at {store_path}\n\n"
        "This store will be used IN STEAD OF the default postgres + pgvector store."
        "Some functionality, such as TTLs and vector search, may not be available."
        "Search performance & other capabilities will depend on the quality of your implementation."
    )
    # Try to load. The loaded object can either be a BaseStore instance, a function that generates it, etc.
    value = await run_in_executor(None, _load_store, store_path)
    CUSTOM_STORE = value


@timing.timer(
    message="Loading store {store_path}",
    metadata_fn=lambda store_path: {"store_path": store_path},
    warn_threshold_secs=5,
    warn_message="Loading store '{store_path}' took longer than expected",
    error_threshold_secs=10,
)
def _load_store(store_path: str) -> Any:
    with profiled_import(store_path):
        if "/" in store_path or ".py:" in store_path:
            modname = "".join(choice("abcdefghijklmnopqrstuvwxyz") for _ in range(24))
            path_name, function = store_path.rsplit(":", 1)
            module_name = path_name.rstrip(":")
            # Load from file path
            modspec = importlib.util.spec_from_file_location(modname, module_name)
            if modspec is None:
                raise ValueError(f"Could not find store file: {path_name}")
            module = importlib.util.module_from_spec(modspec)
            sys.modules[module_name] = module
            modspec.loader.exec_module(module)

        else:
            path_name, function = store_path.rsplit(".", 1)
            module = importlib.import_module(path_name)

    try:
        store: BaseStore | Callable[[config.StoreConfig], BaseStore] = module.__dict__[
            function
        ]
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
                likely_ = "\n".join(
                    [f"\t- {path_name}:{k}" if path_name else k for k in likely]
                )
                suggestion = f"\nDid you mean to use one of the following?\n{likely_}"
            elif available:
                suggestion = f"\nFound the following exports: {', '.join(available)}"

        raise ValueError(
            f"Could not find store '{store_path}'. "
            f"Please check that:\n"
            f"1. The file exports a variable named '{store_path}'\n"
            f"2. The variable name in your config matches the export name{suggestion}"
        ) from e
    return store
