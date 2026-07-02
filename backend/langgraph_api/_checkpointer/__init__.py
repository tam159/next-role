from collections.abc import Callable
from typing import Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver

from langgraph_api._checkpointer import _adapter
from langgraph_api._checkpointer.protocol import (
    CheckpointerProtocol,
    FullCheckpointerProtocol,
)

logger = structlog.stdlib.get_logger(__name__)


async def get_checkpointer(
    *,
    conn: Any | None = None,
    unpack_hook: Callable[[int, bytes], Any] | None = None,
    use_direct_connection: bool = False,
) -> FullCheckpointerProtocol:
    return await _adapter.get_checkpointer(
        conn=conn,
        unpack_hook=unpack_hook,
        use_direct_connection=use_direct_connection,
    )


async def start_checkpointer() -> None:
    """Start the checkpointer resources."""
    # Load the custom checkpointer from LANGGRAPH_CHECKPOINTER env var, if configured.
    await _adapter.collect_checkpointer_from_env()
    # If the custom checkpointer is provided, it will be started here / enter the stack.
    checkpointer = await get_checkpointer()
    if not isinstance(checkpointer, (BaseCheckpointSaver, CheckpointerProtocol)):
        # Should only occur if we are using a custom checkpointer.
        logger.warning(
            "Custom checkpointer does not implement the expected checkpointer protocol; "
            "expected to be a subclass of BaseCheckpointSaver and export the proper "
            "async methods: aget_tuple/aput/aput_writes. "
            "Check your `checkpointer.path` target and ensure it returns a "
            "BaseCheckpointSaver instance or equivalent."
        )


async def exit_checkpointer() -> None:
    """Close the checkpointer resources."""
    # This will close the exit stack if a given custom checkpointer is provided.
    await _adapter.exit_checkpointer()


def get_checkpointer_capabilities() -> _adapter.CheckpointerCapabilities | None:
    """Return the capabilities of the custom checkpointer, or None if not configured."""
    return _adapter.get_checkpointer_capabilities()


__all__ = [
    "CheckpointerProtocol",
    "FullCheckpointerProtocol",
    "exit_checkpointer",
    "get_checkpointer",
    "get_checkpointer_capabilities",
    "start_checkpointer",
]
