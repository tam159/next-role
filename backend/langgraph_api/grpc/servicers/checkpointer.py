"""Checkpointer gRPC servicer implementation.

This module implements the Checkpointer gRPC service, exposing the Python
checkpointer implementation to the Go server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import grpc
import orjson
import structlog
from google.protobuf.empty_pb2 import Empty  # ty: ignore[unresolved-import]
from langgraph_grpc_common.conversion.checkpoint import (
    checkpoint_from_proto,
    checkpoint_metadata_from_proto,
    checkpoint_tuple_to_proto,
    prune_strategy_from_proto,
    writes_from_proto,
)
from langgraph_grpc_common.conversion.config import (
    config_from_proto,
    config_from_proto_optional,
    config_to_proto,
)
from langgraph_grpc_common.proto import checkpointer_pb2
from langgraph_grpc_common.proto.checkpointer_pb2_grpc import CheckpointerServicer

from langgraph_api import _checkpointer as api_checkpointer

if TYPE_CHECKING:
    from grpc import aio as grpc_aio
    from langgraph.checkpoint.base import CheckpointMetadata

logger = structlog.stdlib.get_logger(__name__)


class CheckpointerServicerImpl(CheckpointerServicer):
    """Implementation of the Checkpointer gRPC service.

    This servicer delegates to the Python checkpointer implementation,
    allowing the Go server to use Python-based checkpoint storage.

    The checkpointer is obtained from the global checkpointer instance
    configured during server startup.
    """

    async def Put(
        self,
        request: checkpointer_pb2.PutRequest,
        context: grpc_aio.ServicerContext,
    ) -> checkpointer_pb2.PutResponse:
        """Store a checkpoint with its configuration and metadata."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            config = config_from_proto(request.config)
            checkpoint = checkpoint_from_proto(request.checkpoint)
            metadata = cast(
                "CheckpointMetadata",
                checkpoint_metadata_from_proto(request.metadata) or {},
            )
            new_versions = dict(request.new_versions)
            next_config = await checkpointer.aput(
                config, checkpoint, metadata, new_versions
            )
            next_config_pb = config_to_proto(next_config)
            if next_config_pb is None:
                return checkpointer_pb2.PutResponse()
            return checkpointer_pb2.PutResponse(next_config=next_config_pb)
        except Exception as e:
            await logger.aexception("Checkpointer.Put failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer Put failed: {e}")
            raise

    async def PutWrites(
        self,
        request: checkpointer_pb2.PutWritesRequest,
        context: grpc_aio.ServicerContext,
    ) -> Empty:
        """Store intermediate writes linked to a checkpoint (pending writes)."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            config = config_from_proto(request.config)
            writes = writes_from_proto(request.writes)
            await checkpointer.aput_writes(
                config, writes, request.task_id, request.task_path
            )
            return Empty()
        except Exception as e:
            await logger.aexception("Checkpointer.PutWrites failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer PutWrites failed: {e}")
            raise

    async def GetCapabilities(
        self,
        request: Empty,
        context: grpc_aio.ServicerContext,
    ) -> checkpointer_pb2.Capabilities:
        """Return supported operations and batching limits."""
        caps = api_checkpointer.get_checkpointer_capabilities()
        if caps is None:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Checkpointer capabilities not yet initialized")
            raise RuntimeError("Checkpointer capabilities not yet initialized")
        return checkpointer_pb2.Capabilities(
            supports_delete_thread=caps.has_adelete_thread,
            # The adapter provides generic fallbacks for these, so always True.
            supports_prune=True,
            supports_delete_for_runs=True,
            supports_copy_thread=True,
        )

    async def List(
        self,
        request: checkpointer_pb2.ListRequest,
        context: grpc_aio.ServicerContext,
    ) -> checkpointer_pb2.ListResponse:
        """Return checkpoints that match a given configuration and filter criteria."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            config = config_from_proto(request.config)
            filter_dict = (
                orjson.loads(request.filter_json) if request.filter_json else None
            )
            before = config_from_proto_optional(request.before)
            limit = request.limit if request.HasField("limit") else None

            tuples = []
            async for checkpoint_tuple in checkpointer.alist(
                config, filter=filter_dict, before=before, limit=limit
            ):
                tuples.append(checkpoint_tuple_to_proto(checkpoint_tuple))
            return checkpointer_pb2.ListResponse(checkpoint_tuples=tuples)
        except Exception as e:
            await logger.aexception("Checkpointer.List failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer List failed: {e}")
            raise

    async def GetTuple(
        self,
        request: checkpointer_pb2.GetTupleRequest,
        context: grpc_aio.ServicerContext,
    ) -> checkpointer_pb2.GetTupleResponse:
        """Fetch a checkpoint tuple for a given configuration."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            config = config_from_proto(request.config)
            result = await checkpointer.aget_tuple(config)
            if result is None:
                return checkpointer_pb2.GetTupleResponse()
            return checkpointer_pb2.GetTupleResponse(
                checkpoint_tuple=checkpoint_tuple_to_proto(result)
            )
        except Exception as e:
            await logger.aexception("Checkpointer.GetTuple failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer GetTuple failed: {e}")
            raise

    async def DeleteThread(
        self,
        request: checkpointer_pb2.DeleteThreadRequest,
        context: grpc_aio.ServicerContext,
    ) -> Empty:
        """Delete all checkpoints and writes for a thread."""
        try:
            caps = api_checkpointer.get_checkpointer_capabilities()
            if caps is None or not caps.has_adelete_thread:
                context.set_code(grpc.StatusCode.UNIMPLEMENTED)
                context.set_details(
                    "Custom checkpointer does not implement adelete_thread"
                )
                raise NotImplementedError("adelete_thread not implemented")
            checkpointer = await api_checkpointer.get_checkpointer()
            await checkpointer.adelete_thread(request.thread_id)
            return Empty()
        except Exception as e:
            await logger.aexception("Checkpointer.DeleteThread failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer DeleteThread failed: {e}")
            raise

    async def DeleteForRuns(
        self,
        request: checkpointer_pb2.DeleteForRunsRequest,
        context: grpc_aio.ServicerContext,
    ) -> Empty:
        """Delete all checkpoints and writes for a set of runs (rollbacks)."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            await checkpointer.adelete_for_runs(list(request.run_ids))
            return Empty()
        except Exception as e:
            await logger.aexception("Checkpointer.DeleteForRuns failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer DeleteForRuns failed: {e}")
            raise

    async def CopyThread(
        self,
        request: checkpointer_pb2.CopyThreadRequest,
        context: grpc_aio.ServicerContext,
    ) -> Empty:
        """Copy checkpoint data from one thread to another."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            await checkpointer.acopy_thread(
                request.from_thread_id, request.to_thread_id
            )
            return Empty()
        except Exception as e:
            await logger.aexception("Checkpointer.CopyThread failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer CopyThread failed: {e}")
            raise

    async def Prune(
        self,
        request: checkpointer_pb2.PruneRequest,
        context: grpc_aio.ServicerContext,
    ) -> Empty:
        """Delete checkpoints and related data for a set of threads."""
        try:
            checkpointer = await api_checkpointer.get_checkpointer()
            strategy = prune_strategy_from_proto(request.strategy)
            await checkpointer.aprune(list(request.thread_ids), strategy=strategy)
            return Empty()
        except Exception as e:
            await logger.aexception("Checkpointer.Prune failed")
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Checkpointer Prune failed: {e}")
            raise
