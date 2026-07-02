import logging
from typing import Any, cast

import orjson
from langchain_core.runnables import RunnableConfig

from langgraph_grpc_common import conversion
from langgraph_grpc_common.conversion._compat import (
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
    Runtime,
    StreamProtocol,
)
from langgraph_grpc_common.conversion.struct import _default_serializer
from langgraph_grpc_common.proto import engine_common_pb2, enum_stream_mode_pb2

CONFIG_KEY_GRAPH_ID = "graph_id"
# DR-specific key for storing root graph stream modes, required for patched subgraph streaming.
CONFIG_KEY_ROOT_STREAM_MODES = "__pregel_root_stream_modes"
CONFIG_KEY_TRACING_PROJECT = "__langsmith_project__"
CONFIG_KEY_TRACING_EXAMPLE_ID = "__langsmith_example_id__"

logger = logging.getLogger(__name__)


def config_from_proto(
    config_proto: engine_common_pb2.EngineRunnableConfig | None,
) -> RunnableConfig:
    if not config_proto:
        return RunnableConfig()
    configurable = _configurable_from_proto(config_proto)

    metadata = {}
    for k, v in config_proto.metadata_json.items():
        metadata[k] = orjson.loads(v)
    if config_proto.HasField("run_attempt"):
        metadata["run_attempt"] = config_proto.run_attempt
    if config_proto.HasField("server_run_id"):
        metadata["run_id"] = config_proto.server_run_id

    config: RunnableConfig = (
        RunnableConfig(configurable=configurable) if configurable else RunnableConfig()
    )
    if config_proto.tags:
        config["tags"] = list(config_proto.tags)
    if metadata:
        config["metadata"] = metadata
    if config_proto.HasField("run_name"):
        config["run_name"] = config_proto.run_name

    if config_proto.HasField("max_concurrency"):
        config["max_concurrency"] = config_proto.max_concurrency

    if config_proto.HasField("recursion_limit"):
        config["recursion_limit"] = config_proto.recursion_limit

    if config_proto.extra_json:
        for k, v in config_proto.extra_json.items():
            config[k] = orjson.loads(v)  # type: ignore[invalid-key]  # ty: ignore[invalid-key]

    return config


def config_from_proto_optional(
    config_proto: engine_common_pb2.EngineRunnableConfig | None,
) -> RunnableConfig | None:
    if not config_proto:
        return None
    if not config_proto.ListFields():
        return None
    return config_from_proto(config_proto)


def _configurable_from_proto(
    config_proto: engine_common_pb2.EngineRunnableConfig,
) -> dict[str, Any]:
    configurable = {}

    if config_proto.HasField("resuming"):
        configurable[CONFIG_KEY_RESUMING] = config_proto.resuming

    if config_proto.HasField("task_id"):
        configurable[CONFIG_KEY_TASK_ID] = config_proto.task_id

    if config_proto.HasField("thread_id"):
        configurable[CONFIG_KEY_THREAD_ID] = config_proto.thread_id

    if config_proto.HasField("checkpoint_id") and config_proto.checkpoint_id:
        configurable[CONFIG_KEY_CHECKPOINT_ID] = config_proto.checkpoint_id

    if config_proto.HasField("checkpoint_ns"):
        configurable[CONFIG_KEY_CHECKPOINT_NS] = config_proto.checkpoint_ns

    if config_proto.HasField("durability"):
        if durability := conversion.durability.durability_from_proto(
            config_proto.durability
        ):
            configurable[CONFIG_KEY_DURABILITY] = durability

    if config_proto.HasField("graph_id"):
        configurable[CONFIG_KEY_GRAPH_ID] = config_proto.graph_id

    if len(config_proto.root_stream_modes) > 0:
        root_modes: set[str] = {
            enum_stream_mode_pb2.StreamMode.Name(mode)
            for mode in config_proto.root_stream_modes
        }
        # required for OSS custom event emission which checks CONFIG_KEY_STREAM.modes
        configurable[CONFIG_KEY_STREAM] = StreamProtocol(
            lambda _: None,
            root_modes,  # ty: ignore[invalid-argument-type]
        )
        # preserves root modes through nested subgraph calls
        configurable[CONFIG_KEY_ROOT_STREAM_MODES] = root_modes

    # Handle runtime
    # TODO need context schema to create runtime here. Handle to ensure_runtime for now, but only called during executetasks
    if config_proto.HasField("runtime"):
        runtime_proto = config_proto.runtime
        configurable[CONFIG_KEY_RUNTIME] = {
            "context": (
                orjson.loads(runtime_proto.langgraph_context_json)
                if runtime_proto.HasField("langgraph_context_json")
                else None
            ),
            "previous": (
                conversion.value.value_from_proto(runtime_proto.previous)
                if runtime_proto.HasField("previous")
                else None
            ),
        }

    if len(config_proto.checkpoint_map) > 0:
        configurable[CONFIG_KEY_CHECKPOINT_MAP] = dict(config_proto.checkpoint_map)

    if len(config_proto.resume_map) > 0:
        resume_map_proto = dict(config_proto.resume_map)
        configurable[CONFIG_KEY_RESUME_MAP] = {
            k: conversion.value.serialized_value_from_proto(v)
            for k, v in resume_map_proto.items()
        }

    if len(config_proto.extra_configurable_json) > 0:
        for k, v in config_proto.extra_configurable_json.items():
            configurable[k] = orjson.loads(v)

    # Add run_id to configurable - this is the tracing run ID
    if config_proto.HasField("run_id") and config_proto.run_id:
        configurable["run_id"] = config_proto.run_id
    if config_proto.HasField("tracing_project") and config_proto.tracing_project:
        configurable[CONFIG_KEY_TRACING_PROJECT] = config_proto.tracing_project
    if config_proto.HasField("tracing_example_id") and config_proto.tracing_example_id:
        configurable[CONFIG_KEY_TRACING_EXAMPLE_ID] = config_proto.tracing_example_id

    return configurable


KNOWN_CONFIG_KEYS = {
    "metadata",
    "run_name",
    "run_id",
    "max_concurrency",
    "recursion_limit",
    "tags",
    "configurable",
    "callbacks",
}


def config_to_proto(
    config: RunnableConfig,
) -> engine_common_pb2.EngineRunnableConfig | None:
    # Prepare kwargs for construction
    if not config:
        return None
    pb_config = engine_common_pb2.EngineRunnableConfig()
    for k, v in (config.get("metadata") or {}).items():
        if k == "run_attempt":
            pb_config.run_attempt = v
        elif k == "run_id":
            pb_config.server_run_id = str(v)
        else:
            try:
                pb_config.metadata_json[k] = orjson.dumps(
                    v, default=_default_serializer
                )
            except Exception:
                logger.warning(
                    "Failed to serialize metadata value",
                    extra={
                        "metadata_key": str(k),
                        "metadata_value_type": str(type(v)),
                    },
                )
                raise
    if run_name := config.get("run_name"):
        pb_config.run_name = run_name

    if (run_id := config.get("run_id")) or (
        run_id := config.get("configurable", {}).get("run_id")
    ):
        pb_config.run_id = str(run_id)

    if max_concurrency := config.get("max_concurrency"):
        pb_config.max_concurrency = max_concurrency

    if recursion_limit := config.get("recursion_limit"):
        pb_config.recursion_limit = recursion_limit

    # Handle collections after construction
    if tags := config.get("tags"):
        pb_config.tags.extend(tags)

    if configurable := config.get("configurable"):
        _inject_configurable_into_proto(configurable, pb_config)

    # Preserve extra top-level keys (not in KNOWN_CONFIG_KEYS)
    extra = {k: v for k, v in config.items() if k not in KNOWN_CONFIG_KEYS}
    if extra:
        # Note: These aren't really supposed to be supported in any case, but
        # they've been around for a while so some people may rely on them.
        extra_json = {}
        for k, v in extra.items():
            try:
                extra_json[k] = orjson.dumps(v, default=_default_serializer)
            except Exception:
                logger.warning(
                    "Ignoring unserializable extra config value",
                    extra={
                        "config_key": str(k),
                        "config_value_type": str(type(v)),
                    },
                )
        pb_config.extra_json.update(extra_json)

    return pb_config


RESTRICTED_RESERVED_CONFIGURABLE_KEYS = {
    CONFIG_KEY_SEND,
    CONFIG_KEY_READ,
    CONFIG_KEY_SCRATCHPAD,
    CONFIG_KEY_CALL,
    CONFIG_KEY_CHECKPOINTER,
    CONFIG_KEY_STREAM,
    CONFIG_KEY_CACHE,
    CONFIG_KEY_RUNNER_SUBMIT,
    CONFIG_KEY_ROOT_STREAM_MODES,
}


def _inject_configurable_into_proto(
    configurable: dict[str, Any], proto: engine_common_pb2.EngineRunnableConfig
) -> None:
    extra = {}
    for key, value in configurable.items():
        if key == CONFIG_KEY_RESUMING:
            if value is not None:
                proto.resuming = bool(value)
        elif key == CONFIG_KEY_TASK_ID:
            if value is not None:
                proto.task_id = str(value)
        elif key == CONFIG_KEY_THREAD_ID:
            if value is not None:
                proto.thread_id = str(value)
        elif key == CONFIG_KEY_CHECKPOINT_MAP:
            if value is not None:
                proto.checkpoint_map.update(cast("dict[str, str]", value))
        elif key == CONFIG_KEY_CHECKPOINT_ID:
            if value is not None:
                proto.checkpoint_id = str(value)
        elif key == CONFIG_KEY_CHECKPOINT_NS:
            if value is not None:
                proto.checkpoint_ns = str(value)
        elif key == CONFIG_KEY_RESUME_MAP:
            if value is not None:
                for k, v in cast("dict[str, Any]", value).items():
                    proto.resume_map[k].CopyFrom(
                        conversion.value.any_to_serialized_value(v)
                    )
        elif key == CONFIG_KEY_RUNTIME:
            if value is not None:
                proto.runtime.CopyFrom(runtime_to_proto(value))
        elif key == CONFIG_KEY_DURABILITY:
            if value is not None:
                proto.durability = conversion.durability.durability_to_proto(value)
        elif key == CONFIG_KEY_ROOT_STREAM_MODES:
            if value is not None:
                converted = [
                    enum_stream_mode_pb2.StreamMode.Value(mode) for mode in value
                ]
                proto.root_stream_modes.extend(converted)
        elif key == CONFIG_KEY_TRACING_PROJECT:
            if value is not None:
                proto.tracing_project = str(value)
        elif key == CONFIG_KEY_TRACING_EXAMPLE_ID:
            if value is not None:
                proto.tracing_example_id = str(value)
        elif key not in RESTRICTED_RESERVED_CONFIGURABLE_KEYS:
            extra[key] = value
    if extra:
        extra_configurable_json = {}
        for k, v in extra.items():
            try:
                extra_configurable_json[k] = orjson.dumps(
                    v, default=_default_serializer
                )
            except Exception:
                logger.warning(
                    "Failed to serialize extra configurable value",
                    extra={
                        "configurable_key": str(k),
                        "configurable_value_type": str(type(v)),
                    },
                )
                raise

        proto.extra_configurable_json.update(extra_configurable_json)


def runtime_to_proto(runtime: Runtime) -> engine_common_pb2.Runtime:
    proto = engine_common_pb2.Runtime()

    if runtime.context:
        context_json = convert_dict_to_json_bytes(runtime.context)
        if context_json:
            proto.langgraph_context_json = context_json

    if runtime.previous is not None:
        proto.previous.CopyFrom(conversion.value.value_to_proto(None, runtime.previous))

    return proto


def convert_dict_to_json_bytes(content: dict[str, Any] | Any) -> bytes | None:
    """Convert dict[str, Any] to JSON bytes for proto serialization."""
    if content is None:
        return None

    # Convert dataclass or other objects to dict if needed
    if hasattr(content, "__dict__") and not hasattr(content, "items"):
        # Convert dataclass to dict
        context_dict = content.__dict__
    elif hasattr(content, "items"):
        # Already a dict-like object
        context_dict = dict(content)
    else:
        # Try to convert to dict using vars()
        context_dict = vars(content) if hasattr(content, "__dict__") else {}

    return orjson.dumps(context_dict)
