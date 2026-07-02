from __future__ import annotations

import asyncio
import random
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterator,
    Sequence,
)
from typing import TYPE_CHECKING, Any, TypeVar, cast

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from langgraph_grpc_common.conversion import checkpoint as ckpt_conv
from langgraph_grpc_common.conversion.config import (
    config_from_proto,
    config_to_proto,
    convert_dict_to_json_bytes,
)
from langgraph_grpc_common.proto import checkpointer_pb2

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from langgraph_grpc_common.proto.checkpointer_pb2_grpc import CheckpointerStub

T = TypeVar("T")
RetryFn = Callable[[Callable[[], Awaitable[Any]], str], Awaitable[Any]]
StubProvider = Callable[[], Awaitable["CheckpointerStub"]]


class GrpcCheckpointer(BaseCheckpointSaver):
    """Base gRPC checkpointer client with injectable transport + retry policy."""

    latest_iter: AsyncIterator[CheckpointTuple] | None

    def __init__(
        self,
        *,
        get_stub: StubProvider,
        retry: RetryFn | None = None,
        retry_context_prefix: str | None = None,
    ) -> None:
        super().__init__(serde=None)
        self._get_stub = get_stub
        self._retry = retry
        self._retry_context_prefix = retry_context_prefix or type(self).__name__
        self.latest_iter = None
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError(
                "Sync checkpointer methods require initialization inside an event loop"
            )
        return self._loop

    def _run_sync(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    async def _call(self, op_name: str, func: Callable[[], Awaitable[T]]) -> T:
        if self._retry is None:
            return await func()
        context = f"{self._retry_context_prefix}.{op_name}"
        return cast("T", await self._retry(func, context))

    async def _stub(self) -> CheckpointerStub:
        return await self._get_stub()

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        if value := self.get_tuple(config):
            return value.checkpoint
        return None

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._run_sync(self.aget_tuple(config))

    async def aget(self, config: RunnableConfig) -> Checkpoint | None:
        if value := await self.aget_tuple(config):
            return value.checkpoint
        return None

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        request = checkpointer_pb2.GetTupleRequest(config=config_to_proto(config))

        async def _request() -> CheckpointTuple | None:
            response = await (await self._stub()).GetTuple(request)
            if not response.HasField("checkpoint_tuple"):
                return None
            return ckpt_conv.checkpoint_tuple_from_proto(response.checkpoint_tuple)

        return await self._call("aget_tuple", _request)

    async def aget_iter(self, config: RunnableConfig) -> AsyncIterator[CheckpointTuple]:
        async def _gen() -> AsyncIterator[CheckpointTuple]:
            tup = await self.aget_tuple(config)
            if tup is not None:
                yield tup

        return _gen()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._run_sync(self.aput(config, checkpoint, metadata, new_versions))

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        request = checkpointer_pb2.PutRequest(
            config=config_to_proto(config),
            checkpoint=ckpt_conv.checkpoint_to_proto(checkpoint),
            metadata=ckpt_conv.checkpoint_metadata_to_proto(metadata),
            new_versions={k: str(v) for k, v in new_versions.items()},
        )

        async def _request() -> RunnableConfig:
            response = await (await self._stub()).Put(request)
            next_config = config_from_proto(response.next_config)
            if next_config is None:
                raise ValueError("Unexpected None value for next_config")
            return next_config

        return await self._call("aput", _request)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._run_sync(self.aput_writes(config, writes, task_id, task_path))

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        request = checkpointer_pb2.PutWritesRequest(
            config=config_to_proto(config),
            writes=ckpt_conv.writes_to_proto(writes),
            task_id=task_id,
            task_path=task_path,
        )

        async def _request() -> None:
            await (await self._stub()).PutWrites(request)

        await self._call("aput_writes", _request)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        return iter(
            self._run_sync(
                self._alist_to_list(config, filter=filter, before=before, limit=limit)
            )
        )

    async def _alist_to_list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Sequence[CheckpointTuple]:
        return [
            item
            async for item in self.alist(
                config,
                filter=filter,
                before=before,
                limit=limit,
            )
        ]

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        request = checkpointer_pb2.ListRequest(
            config=config_to_proto(config) if config is not None else None,
            filter_json=convert_dict_to_json_bytes(filter) or b"",
            before=config_to_proto(before) if before is not None else None,
        )
        if limit is not None:
            request.limit = limit

        async def _request() -> checkpointer_pb2.ListResponse:
            return await (await self._stub()).List(request)

        response = await self._call("alist", _request)
        for proto_tuple in response.checkpoint_tuples:
            if (tup := ckpt_conv.checkpoint_tuple_from_proto(proto_tuple)) is not None:
                yield tup

    def delete_thread(self, thread_id: str) -> None:
        self._run_sync(self.adelete_thread(thread_id))

    async def adelete_thread(self, thread_id: str) -> None:
        request = checkpointer_pb2.DeleteThreadRequest(thread_id=thread_id)

        async def _request() -> None:
            await (await self._stub()).DeleteThread(request)

        await self._call("adelete_thread", _request)

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        self._run_sync(self.adelete_for_runs(run_ids))

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        request = checkpointer_pb2.DeleteForRunsRequest(run_ids=list(run_ids))

        async def _request() -> None:
            await (await self._stub()).DeleteForRuns(request)

        await self._call("adelete_for_runs", _request)

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        self._run_sync(self.acopy_thread(source_thread_id, target_thread_id))

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        request = checkpointer_pb2.CopyThreadRequest(
            from_thread_id=source_thread_id,
            to_thread_id=target_thread_id,
        )

        async def _request() -> None:
            await (await self._stub()).CopyThread(request)

        await self._call("acopy_thread", _request)

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        self._run_sync(self.aprune(thread_ids, strategy=strategy))

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        request = checkpointer_pb2.PruneRequest(
            thread_ids=list(thread_ids),
            strategy=ckpt_conv.prune_strategy_to_proto(strategy),
        )

        async def _request() -> None:
            await (await self._stub()).Prune(request)

        await self._call("aprune", _request)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:.16f}"
