from __future__ import annotations

import asyncio
import functools
import typing
from collections import ChainMap
from contextvars import copy_context
from os import getenv
from typing import Any, ParamSpec, TypeVar

from langgraph.constants import CONF
from typing_extensions import TypedDict

if typing.TYPE_CHECKING:
    from concurrent.futures import Executor

    from langchain_core.runnables import RunnableConfig

try:
    from langchain_core.runnables.config import (
        var_child_runnable_config,
    )
except ImportError:
    var_child_runnable_config = None

CONFIG_KEYS = [
    "tags",
    "metadata",
    "callbacks",
    "run_name",
    "max_concurrency",
    "recursion_limit",
    "configurable",
    "run_id",
]

COPIABLE_KEYS = [
    "tags",
    "metadata",
    "callbacks",
    "configurable",
]

DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10011"))

T = TypeVar("T")
P = ParamSpec("P")


def _is_not_empty(value: Any) -> bool:
    if isinstance(value, list | tuple | dict):
        return len(value) > 0
    else:
        return value is not None


class _Config(TypedDict):
    tags: list[str]
    metadata: ChainMap
    callbacks: None
    recursion_limit: int
    configurable: dict[str, Any]


def ensure_config(*configs: RunnableConfig | None) -> RunnableConfig:
    """Return a config with all keys, merging any provided configs.

    Args:
        *configs: Configs to merge before ensuring defaults.

    Returns:
        RunnableConfig: The merged and ensured config.
    """
    empty = _Config(
        tags=[],
        metadata=ChainMap(),
        callbacks=None,
        recursion_limit=DEFAULT_RECURSION_LIMIT,
        configurable={},
    )
    if var_child_runnable_config is not None and (
        var_config := var_child_runnable_config.get()
    ):
        empty.update(
            {
                k: v.copy() if k in COPIABLE_KEYS else v
                for k, v in var_config.items()
                if _is_not_empty(v)
            },
        )
    for config in configs:
        if config is None:
            continue
        for k, v in config.items():
            if _is_not_empty(v) and k in CONFIG_KEYS:
                if k == CONF:
                    empty[k] = v.copy()
                else:
                    empty[k] = v
        for k, v in config.items():
            if _is_not_empty(v) and k not in CONFIG_KEYS:
                empty[CONF][k] = v
    for key, value in empty[CONF].items():
        if (
            not key.startswith("__")
            and isinstance(value, str | int | float | bool)
            and key not in empty["metadata"]
        ):
            empty["metadata"][key] = value
    return empty


async def run_in_executor(
    executor_or_config: Executor | RunnableConfig | None,
    func: typing.Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run a function in an executor.

    Args:
        executor_or_config: The executor or config to run in.
        func (Callable[P, Output]): The function.
        *args (Any): The positional arguments to the function.
        **kwargs (Any): The keyword arguments to the function.

    Returns:
        Output: The output of the function.

    Raises:
        RuntimeError: If the function raises a StopIteration.
    """

    def wrapper() -> T:
        try:
            return func(*args, **kwargs)
        except StopIteration as exc:
            # StopIteration can't be set on an asyncio.Future
            # it raises a TypeError and leaves the Future pending forever
            # so we need to convert it to a RuntimeError
            raise RuntimeError from exc

    if executor_or_config is None or isinstance(executor_or_config, dict):
        # Use default executor with context copied from current context
        return await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(copy_context().run, wrapper),
        )

    return await asyncio.get_running_loop().run_in_executor(executor_or_config, wrapper)
