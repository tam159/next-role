from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import (
        ChannelVersions,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )
    from langgraph.checkpoint.serde.base import SerializerProtocol


@runtime_checkable
class CheckpointerProtocol(Protocol):
    """Protocol for graph checkpointers (BaseCheckpointSaver-compatible)."""

    serde: SerializerProtocol
    latest_iter: AsyncIterator[CheckpointTuple] | None

    def get(self, config: RunnableConfig) -> Checkpoint | None: ...

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None: ...

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]: ...

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig: ...

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None: ...

    def delete_thread(self, thread_id: str) -> None: ...

    async def aget(self, config: RunnableConfig) -> Checkpoint | None: ...

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None: ...

    def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]: ...

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig: ...

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None: ...

    async def adelete_thread(self, thread_id: str) -> None: ...

    async def aget_iter(
        self, config: RunnableConfig
    ) -> AsyncIterator[CheckpointTuple]: ...


@runtime_checkable
class FullCheckpointerProtocol(CheckpointerProtocol, Protocol):
    """Protocol for checkpointers implementing the full conformance spec.

    Extends CheckpointerProtocol with the optional extended capabilities
    (delete_for_runs, copy_thread, prune). Concrete implementations that
    override these methods satisfy this protocol.

    Use CheckpointerProtocol for base validation (isinstance checks at
    startup). Use FullCheckpointerProtocol when you need the extended
    methods to be present.
    """

    def delete_for_runs(self, run_ids: Sequence[str]) -> None: ...

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None: ...

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None: ...

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None: ...

    async def acopy_thread(
        self, source_thread_id: str, target_thread_id: str
    ) -> None: ...

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None: ...
