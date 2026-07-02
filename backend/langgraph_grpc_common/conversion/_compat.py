from collections.abc import Callable
from typing import Any

try:
    from langgraph._internal._config import (
        ensure_config,
        patch_config,
    )
    from langgraph._internal._constants import (
        CACHE_NS_WRITES,
        CONF,
        CONFIG_KEY_CACHE,
        CONFIG_KEY_CALL,
        CONFIG_KEY_CHECKPOINT_ID,
        CONFIG_KEY_CHECKPOINT_MAP,
        CONFIG_KEY_CHECKPOINT_NS,
        CONFIG_KEY_CHECKPOINTER,
        CONFIG_KEY_DURABILITY,
        CONFIG_KEY_READ,
        CONFIG_KEY_RESUME_MAP,
        CONFIG_KEY_RESUMING,
        CONFIG_KEY_RUNNER_SUBMIT,
        CONFIG_KEY_RUNTIME,
        CONFIG_KEY_SCRATCHPAD,
        CONFIG_KEY_SEND,
        CONFIG_KEY_STREAM,
        CONFIG_KEY_TASK_ID,
        CONFIG_KEY_THREAD_ID,
        NS_SEP,
        PULL,
        PUSH,
        TASKS,
    )
    from langgraph._internal._scratchpad import (
        PregelScratchpad,
    )
    from langgraph._internal._typing import MISSING
    from langgraph.cache.memory import InMemoryCache
    from langgraph.pregel._algo import (
        PregelTaskWrites,
        _proc_input,
        _scratchpad,
        local_read,
    )
    from langgraph.pregel._call import identifier
    from langgraph.pregel._read import PregelNode
    from langgraph.pregel.protocol import (
        StreamProtocol,
    )
    from langgraph.runtime import (
        DEFAULT_RUNTIME,
        Runtime,
    )
except ImportError:  # langgraph < 0.5/6
    from langgraph.pregel.algo import (  # type: ignore[unresolved-import]  # ty: ignore[unresolved-import]
        PregelTaskWrites,
        _proc_input,
        _scratchpad,
        local_read,
    )
    from langgraph.pregel.read import (  # type: ignore[unresolved-import]  # ty: ignore[unresolved-import]
        PregelNode,
    )

    CACHE_NS_WRITES = "__pregel_cache_ns_writes"
    CONF = "configurable"
    CONFIG_KEY_CHECKPOINT_NS = "__pregel_checkpoint_ns"
    CONFIG_KEY_READ = "__pregel_read"
    CONFIG_KEY_RESUME_MAP = "__pregel_resume_map"
    CONFIG_KEY_RUNTIME = "__pregel_runtime"
    CONFIG_KEY_SCRATCHPAD = "__pregel_scratchpad"
    CONFIG_KEY_SEND = "__pregel_send"
    PULL = "__pregel_pull"
    PUSH = "__pregel_push"
    TASKS = "__pregel_tasks"
    NS_SEP = "|"

    def identifier(obj: Any, name: str | None = None) -> str | None:
        raise NotImplementedError(
            "langgraph.pregel._call not found. Please upgrade langgraph."
        )

    class Runtime:
        context: Any = None
        previous: Any = None

        def __init__(self, *args, **kwargs):
            pass

        def override(self, **kwargs):
            raise ImportError("langgraph.runtime not found. Please upgrade langgraph.")

    class InMemoryCache:
        """Placeholder for InMemoryCache when langgraph.cache.memory is unavailable."""

        pass

    DEFAULT_RUNTIME = Runtime()

    class StreamProtocol:
        __slots__ = ("__call__", "modes")

        modes: set[str]

        __call__: Callable

        def __init__(
            self,
            __call__: Callable,
            modes: set[str],
        ) -> None:
            self.__call__ = __call__
            self.modes = modes


__all__ = [
    "CACHE_NS_WRITES",
    "CONF",
    "CONFIG_KEY_CACHE",
    "CONFIG_KEY_CALL",
    "CONFIG_KEY_CHECKPOINTER",
    "CONFIG_KEY_CHECKPOINT_ID",
    "CONFIG_KEY_CHECKPOINT_MAP",
    "CONFIG_KEY_CHECKPOINT_NS",
    "CONFIG_KEY_DURABILITY",
    "CONFIG_KEY_READ",
    "CONFIG_KEY_RESUME_MAP",
    "CONFIG_KEY_RESUMING",
    "CONFIG_KEY_RUNNER_SUBMIT",
    "CONFIG_KEY_RUNTIME",
    "CONFIG_KEY_SCRATCHPAD",
    "CONFIG_KEY_SEND",
    "CONFIG_KEY_STREAM",
    "CONFIG_KEY_TASK_ID",
    "CONFIG_KEY_THREAD_ID",
    "DEFAULT_RUNTIME",
    "MISSING",
    "NS_SEP",
    "PULL",
    "PUSH",
    "TASKS",
    "InMemoryCache",
    "PregelNode",
    "PregelScratchpad",
    "PregelTaskWrites",
    "Runtime",
    "StreamProtocol",
    "_proc_input",
    "_scratchpad",
    "ensure_config",
    "identifier",
    "local_read",
    "patch_config",
]
