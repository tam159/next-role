from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_protocol import (
    AgentResult,
    AgentStatus,
    AgentStatusEntry,
    AgentTreeNode,
    AudioContentBlock,
    Channel,
    Checkpoint,
    CheckpointRef,
    CheckpointsEvent,
    CheckpointSource,
    Command,
    CommandResponse,
    ContentBlock,
    ContentBlockDeltaData,
    ContentBlockStartData,
    CustomData,
    ErrorCode,
    ErrorResponse,
    Event,
    EventStreamRequest,
    FileContentBlock,
    FinalizedContentBlock,
    ImageContentBlock,
    InputRequestedData,
    InputRespondEntry,
    InputRespondMany,
    InputRespondOne,
    InputRespondParams,
    LifecycleCause,
    LifecycleCauseEdge,
    LifecycleCauseSend,
    LifecycleCauseToolCall,
    LifecycleData,
    MessageErrorData,
    MessageFinishData,
    MessageMetadata,
    MessageRole,
    MessagesData,
    MessageStartData,
    Namespace,
    NonStandardContentBlock,
    ReasoningContentBlock,
    ReconnectParams,
    ReconnectResult,
    ResponseMeta,
    RunResult,
    RunStartParams,
    ServerToolCall,
    ServerToolCallChunk,
    StateGetResult,
    SubscribeParams,
    SubscribeResult,
    TextContentBlock,
    ToolErrorData,
    ToolFinishedData,
    ToolOutputDeltaData,
    ToolsData,
    ToolStartedData,
    UnsubscribeParams,
    UpdatesData,
    UsageInfo,
    ValuesEvent,
)

__all__ = [
    "AgentResult",
    "AgentStatus",
    "AgentStatusEntry",
    "AgentTreeNode",
    "AudioContentBlock",
    "Channel",
    "Checkpoint",
    "CheckpointRef",
    "CheckpointSource",
    "CheckpointsEvent",
    "Command",
    "CommandResponse",
    "ContentBlock",
    "ContentBlockDeltaData",
    "ContentBlockStartData",
    "CustomData",
    "ErrorCode",
    "ErrorResponse",
    "Event",
    "EventStreamRequest",
    "FileContentBlock",
    "FinalizedContentBlock",
    "ImageContentBlock",
    "InputRequestedData",
    "InputRespondEntry",
    "InputRespondMany",
    "InputRespondOne",
    "InputRespondParams",
    "LifecycleCause",
    "LifecycleCauseEdge",
    "LifecycleCauseSend",
    "LifecycleCauseToolCall",
    "LifecycleData",
    "MessageErrorData",
    "MessageFinishData",
    "MessageMetadata",
    "MessageRole",
    "MessageStartData",
    "MessagesData",
    "Namespace",
    "NamespaceInfo",
    "NonStandardContentBlock",
    "ReasoningContentBlock",
    "ReconnectParams",
    "ReconnectResult",
    "ResponseMeta",
    "RunResult",
    "RunStartParams",
    "ServerToolCall",
    "ServerToolCallChunk",
    "StateGetResult",
    "SubscribeParams",
    "SubscribeResult",
    "Subscription",
    "SupportedChannel",
    "TextContentBlock",
    "ToolErrorData",
    "ToolFinishedData",
    "ToolOutputDeltaData",
    "ToolStartedData",
    "ToolsData",
    "UnsubscribeParams",
    "UpdatesData",
    "UsageInfo",
    "ValuesEvent",
]

SupportedChannel = Literal[
    "values",
    "updates",
    "checkpoints",
    "messages",
    "tools",
    "custom",
    "lifecycle",
    "input",
    "tasks",
]


@dataclass
class Subscription:
    """Active subscription within a ``EventStreamingSession``.

    Subscriptions are WebSocket-only in the thread-centric model. SSE
    event streams apply filters at the connection level (via
    ``EventStreamRequest`` body) instead of using persistent subscriptions.
    """

    id: str
    channels: set[str]
    namespaces: list[list[str]] | None = None
    depth: int | None = None
    active: bool = False


@dataclass
class NamespaceInfo:
    """Cached lifecycle metadata for a namespace."""

    namespace: list[str]
    status: str
    graph_name: str
