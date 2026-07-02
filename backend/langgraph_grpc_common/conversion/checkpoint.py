from collections.abc import Sequence as SequenceType
from typing import Any, Literal, cast

from langgraph.checkpoint.base import (
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
)
from langgraph.types import Send

from langgraph_grpc_common.conversion.config import (
    config_from_proto,
    config_from_proto_optional,
    config_to_proto,
)
from langgraph_grpc_common.conversion.struct import dict_from_raw_map, raw_map_from_dict
from langgraph_grpc_common.conversion.value import (
    any_to_serialized_value,
    send_to_proto,
    value_from_proto,
    value_to_proto,
)
from langgraph_grpc_common.proto import checkpointer_pb2, engine_common_pb2

SOURCE_MAP = {
    None: engine_common_pb2.CheckpointMetadata.CheckpointSource.unknown,
    "input": engine_common_pb2.CheckpointMetadata.CheckpointSource.input,
    "loop": engine_common_pb2.CheckpointMetadata.CheckpointSource.loop,
    "update": engine_common_pb2.CheckpointMetadata.CheckpointSource.update,
    "fork": engine_common_pb2.CheckpointMetadata.CheckpointSource.fork,
}


def checkpoint_from_proto(
    request_checkpoint: engine_common_pb2.Checkpoint,
) -> Checkpoint:
    channel_versions: dict[str, str | int | float] = dict(
        request_checkpoint.channel_versions
    )
    versions_seen: dict[str, dict[str, str | int | float]] = {
        k: dict(v.channel_versions) for k, v in request_checkpoint.versions_seen.items()
    }

    channel_values = {}
    if request_checkpoint.channel_values:
        for k, v in request_checkpoint.channel_values.items():
            if v.WhichOneof("val") is not None:
                channel_values[k] = value_from_proto(v)

    updated_channels = list(request_checkpoint.updated_channels)
    return Checkpoint(
        v=request_checkpoint.v,
        id=request_checkpoint.id,
        channel_versions=channel_versions,
        updated_channels=updated_channels,
        channel_values=channel_values,
        versions_seen=versions_seen,
        ts=request_checkpoint.ts,
    )


def checkpoint_to_proto(checkpoint: Checkpoint) -> engine_common_pb2.Checkpoint:
    checkpoint_proto = engine_common_pb2.Checkpoint()
    # Set core checkpoint fields
    checkpoint_proto.id = checkpoint["id"]
    checkpoint_proto.v = checkpoint["v"]
    checkpoint_proto.ts = checkpoint["ts"]
    # Convert int values to strings for protobuf map<string, string>
    checkpoint_proto.channel_versions.update(
        {k: str(v) for k, v in checkpoint["channel_versions"].items()}
    )
    for node, versions_dict in checkpoint["versions_seen"].items():
        checkpoint_proto.versions_seen[node].channel_versions.update(
            {k: str(v) for k, v in versions_dict.items()}
        )
    # Checkpoint.updated_channels is `list[str] | None` and NotRequired in some
    # langgraph versions. update_state(values=..., as_node=None) on a thread
    # with no prior checkpoint produces None, which would crash extend().
    checkpoint_proto.updated_channels.extend(checkpoint.get("updated_channels") or ())
    for k, v in checkpoint["channel_values"].items():
        if isinstance(v, Send):
            checkpoint_proto.channel_values[k].CopyFrom(
                engine_common_pb2.ChannelValue(
                    sends=engine_common_pb2.Sends(sends=[send_to_proto(v)])
                )
            )
        else:
            checkpoint_proto.channel_values[k].CopyFrom(
                engine_common_pb2.ChannelValue(
                    serialized_value=any_to_serialized_value(v)
                )
            )

    return checkpoint_proto


def checkpoint_tuple_from_proto(
    checkpoint_tuple_pb: engine_common_pb2.CheckpointTuple,
) -> CheckpointTuple | None:
    if not checkpoint_tuple_pb:
        return None

    return CheckpointTuple(
        config=config_from_proto(checkpoint_tuple_pb.config),
        checkpoint=checkpoint_from_proto(checkpoint_tuple_pb.checkpoint),
        metadata=cast(
            "CheckpointMetadata",
            checkpoint_metadata_from_proto(checkpoint_tuple_pb.metadata) or {},
        ),
        parent_config=config_from_proto_optional(checkpoint_tuple_pb.parent_config),
        pending_writes=pending_writes_from_proto(checkpoint_tuple_pb.pending_writes),
    )


def checkpoint_tuple_to_proto(
    checkpoint_tuple: CheckpointTuple,
) -> engine_common_pb2.CheckpointTuple:
    proto = engine_common_pb2.CheckpointTuple()
    if config_pb := config_to_proto(checkpoint_tuple.config):
        proto.config.CopyFrom(config_pb)
    proto.checkpoint.CopyFrom(checkpoint_to_proto(checkpoint_tuple.checkpoint))
    if checkpoint_tuple.metadata:
        proto.metadata.CopyFrom(checkpoint_metadata_to_proto(checkpoint_tuple.metadata))
    if checkpoint_tuple.parent_config:
        if parent_pb := config_to_proto(checkpoint_tuple.parent_config):
            proto.parent_config.CopyFrom(parent_pb)
    if checkpoint_tuple.pending_writes:
        for task_id, channel, value in checkpoint_tuple.pending_writes:
            proto.pending_writes.append(
                engine_common_pb2.PendingWrite(
                    task_id=str(task_id),
                    channel=str(channel),
                    value=value_to_proto(channel, value),
                )
            )
    return proto


def checkpoint_metadata_from_proto(
    metadata_pb: engine_common_pb2.CheckpointMetadata,
) -> CheckpointMetadata | None:
    if not metadata_pb:
        return None

    return {
        "source": metadata_source_from_proto(metadata_pb.source),
        "step": metadata_pb.step,
        "parents": dict(metadata_pb.parents),
        **dict_from_raw_map(metadata_pb.extras),
    }


def checkpoint_metadata_to_proto(
    metadata: CheckpointMetadata,
) -> engine_common_pb2.CheckpointMetadata:
    # Known fields handled explicitly
    known_keys = {"source", "step", "parents", "run_id"}
    # Extras are any additional metadata keys not in the known set
    extras = {k: v for k, v in metadata.items() if k not in known_keys}
    proto = engine_common_pb2.CheckpointMetadata(
        source=SOURCE_MAP[metadata.get("source")],
        step=metadata.get("step", -1),
        parents=metadata.get("parents", {}),
    )
    if run_id := metadata.get("run_id"):
        proto.run_id = run_id
    if extras:
        proto.extras.update(raw_map_from_dict(extras))
    return proto


def metadata_source_from_proto(
    source: engine_common_pb2.CheckpointMetadata.CheckpointSource.ValueType,
) -> Literal["input", "loop", "update", "fork"]:
    if source == engine_common_pb2.CheckpointMetadata.CheckpointSource.input:
        return "input"
    elif source == engine_common_pb2.CheckpointMetadata.CheckpointSource.loop:
        return "loop"
    elif source == engine_common_pb2.CheckpointMetadata.CheckpointSource.update:
        return "update"
    elif source == engine_common_pb2.CheckpointMetadata.CheckpointSource.fork:
        return "fork"
    else:
        raise ValueError(f"Unknown checkpoint source: {source}")


def pending_writes_from_proto(
    pb: SequenceType[engine_common_pb2.PendingWrite],
) -> list[PendingWrite] | None:
    if not pb:
        return []

    return [(pw.task_id, pw.channel, value_from_proto(pw.value)) for pw in pb]


def pending_writes_to_proto(
    pb: SequenceType[engine_common_pb2.PendingWrite],
) -> list[PendingWrite] | None:
    if not pb:
        return None

    return [(pw.task_id, pw.channel, value_to_proto(None, pw.value)) for pw in pb]


def writes_to_proto(
    writes: SequenceType[tuple[str, Any]],
) -> list[engine_common_pb2.Write]:
    return [
        engine_common_pb2.Write(
            channel=channel,
            value=value_to_proto(channel, value),
        )
        for channel, value in writes
    ]


def writes_from_proto(
    writes: SequenceType[engine_common_pb2.Write],
) -> list[tuple[str, Any]]:
    return [(w.channel, value_from_proto(w.value)) for w in writes]


def prune_strategy_from_proto(
    strategy: checkpointer_pb2.PruneRequest.PruneStrategy.ValueType,
) -> Literal["keep_latest", "delete_all"]:
    match strategy:
        case checkpointer_pb2.PruneRequest.PruneStrategy.KEEP_LATEST:
            return "keep_latest"
        case checkpointer_pb2.PruneRequest.PruneStrategy.DELETE_ALL:
            return "delete_all"
    raise ValueError("Unknown prune strategy: " + str(strategy))


def prune_strategy_to_proto(
    strategy: str,
) -> checkpointer_pb2.PruneRequest.PruneStrategy.ValueType:
    match strategy:
        case "keep_latest":
            return checkpointer_pb2.PruneRequest.PruneStrategy.KEEP_LATEST
        case "delete_all":
            return checkpointer_pb2.PruneRequest.PruneStrategy.DELETE_ALL
    raise ValueError("Unknown prune strategy: " + strategy)
