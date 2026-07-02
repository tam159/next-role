import datetime

from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from . import engine_common_pb2 as _engine_common_pb2
from . import enum_run_status_pb2 as _enum_run_status_pb2
from . import enum_multitask_strategy_pb2 as _enum_multitask_strategy_pb2
from . import enum_stream_mode_pb2 as _enum_stream_mode_pb2
from . import enum_cancel_run_action_pb2 as _enum_cancel_run_action_pb2
from . import enum_thread_status_pb2 as _enum_thread_status_pb2
from . import enum_thread_stream_mode_pb2 as _enum_thread_stream_mode_pb2
from . import enum_control_signal_pb2 as _enum_control_signal_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OnConflictBehavior(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RAISE: _ClassVar[OnConflictBehavior]
    DO_NOTHING: _ClassVar[OnConflictBehavior]

class SortOrder(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DESC: _ClassVar[SortOrder]
    ASC: _ClassVar[SortOrder]

class AssistantsSortBy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    UNSPECIFIED: _ClassVar[AssistantsSortBy]
    ASSISTANT_ID: _ClassVar[AssistantsSortBy]
    GRAPH_ID: _ClassVar[AssistantsSortBy]
    NAME: _ClassVar[AssistantsSortBy]
    CREATED_AT: _ClassVar[AssistantsSortBy]
    UPDATED_AT: _ClassVar[AssistantsSortBy]

class ThreadTTLStrategy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    THREAD_TTL_STRATEGY_DELETE: _ClassVar[ThreadTTLStrategy]
    THREAD_TTL_STRATEGY_KEEP_LATEST: _ClassVar[ThreadTTLStrategy]

class CheckpointSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHECKPOINT_SOURCE_UNSPECIFIED: _ClassVar[CheckpointSource]
    CHECKPOINT_SOURCE_INPUT: _ClassVar[CheckpointSource]
    CHECKPOINT_SOURCE_LOOP: _ClassVar[CheckpointSource]
    CHECKPOINT_SOURCE_UPDATE: _ClassVar[CheckpointSource]
    CHECKPOINT_SOURCE_FORK: _ClassVar[CheckpointSource]

class ThreadsSortBy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    THREADS_SORT_BY_UNSPECIFIED: _ClassVar[ThreadsSortBy]
    THREADS_SORT_BY_THREAD_ID: _ClassVar[ThreadsSortBy]
    THREADS_SORT_BY_CREATED_AT: _ClassVar[ThreadsSortBy]
    THREADS_SORT_BY_UPDATED_AT: _ClassVar[ThreadsSortBy]
    THREADS_SORT_BY_STATUS: _ClassVar[ThreadsSortBy]

class CreateRunBehavior(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REJECT_RUN_IF_THREAD_NOT_EXISTS: _ClassVar[CreateRunBehavior]
    CREATE_THREAD_IF_THREAD_NOT_EXISTS: _ClassVar[CreateRunBehavior]

class CancelRunStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CANCEL_RUN_STATUS_PENDING: _ClassVar[CancelRunStatus]
    CANCEL_RUN_STATUS_RUNNING: _ClassVar[CancelRunStatus]
    CANCEL_RUN_STATUS_ALL: _ClassVar[CancelRunStatus]

class CronsSortBy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CRONS_SORT_BY_UNSPECIFIED: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_CRON_ID: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_ASSISTANT_ID: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_THREAD_ID: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_NEXT_RUN_DATE: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_END_TIME: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_CREATED_AT: _ClassVar[CronsSortBy]
    CRONS_SORT_BY_UPDATED_AT: _ClassVar[CronsSortBy]
RAISE: OnConflictBehavior
DO_NOTHING: OnConflictBehavior
DESC: SortOrder
ASC: SortOrder
UNSPECIFIED: AssistantsSortBy
ASSISTANT_ID: AssistantsSortBy
GRAPH_ID: AssistantsSortBy
NAME: AssistantsSortBy
CREATED_AT: AssistantsSortBy
UPDATED_AT: AssistantsSortBy
THREAD_TTL_STRATEGY_DELETE: ThreadTTLStrategy
THREAD_TTL_STRATEGY_KEEP_LATEST: ThreadTTLStrategy
CHECKPOINT_SOURCE_UNSPECIFIED: CheckpointSource
CHECKPOINT_SOURCE_INPUT: CheckpointSource
CHECKPOINT_SOURCE_LOOP: CheckpointSource
CHECKPOINT_SOURCE_UPDATE: CheckpointSource
CHECKPOINT_SOURCE_FORK: CheckpointSource
THREADS_SORT_BY_UNSPECIFIED: ThreadsSortBy
THREADS_SORT_BY_THREAD_ID: ThreadsSortBy
THREADS_SORT_BY_CREATED_AT: ThreadsSortBy
THREADS_SORT_BY_UPDATED_AT: ThreadsSortBy
THREADS_SORT_BY_STATUS: ThreadsSortBy
REJECT_RUN_IF_THREAD_NOT_EXISTS: CreateRunBehavior
CREATE_THREAD_IF_THREAD_NOT_EXISTS: CreateRunBehavior
CANCEL_RUN_STATUS_PENDING: CancelRunStatus
CANCEL_RUN_STATUS_RUNNING: CancelRunStatus
CANCEL_RUN_STATUS_ALL: CancelRunStatus
CRONS_SORT_BY_UNSPECIFIED: CronsSortBy
CRONS_SORT_BY_CRON_ID: CronsSortBy
CRONS_SORT_BY_ASSISTANT_ID: CronsSortBy
CRONS_SORT_BY_THREAD_ID: CronsSortBy
CRONS_SORT_BY_NEXT_RUN_DATE: CronsSortBy
CRONS_SORT_BY_END_TIME: CronsSortBy
CRONS_SORT_BY_CREATED_AT: CronsSortBy
CRONS_SORT_BY_UPDATED_AT: CronsSortBy

class Tags(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, values: _Optional[_Iterable[str]] = ...) -> None: ...

class EqAuthFilter(_message.Message):
    __slots__ = ("key", "match")
    KEY_FIELD_NUMBER: _ClassVar[int]
    MATCH_FIELD_NUMBER: _ClassVar[int]
    key: str
    match: str
    def __init__(self, key: _Optional[str] = ..., match: _Optional[str] = ...) -> None: ...

class ContainsAuthFilter(_message.Message):
    __slots__ = ("key", "matches")
    KEY_FIELD_NUMBER: _ClassVar[int]
    MATCHES_FIELD_NUMBER: _ClassVar[int]
    key: str
    matches: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, key: _Optional[str] = ..., matches: _Optional[_Iterable[str]] = ...) -> None: ...

class OrAuthFilter(_message.Message):
    __slots__ = ("filters",)
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class AndAuthFilter(_message.Message):
    __slots__ = ("filters",)
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class AuthFilter(_message.Message):
    __slots__ = ("eq", "contains", "or_filter", "and_filter")
    EQ_FIELD_NUMBER: _ClassVar[int]
    CONTAINS_FIELD_NUMBER: _ClassVar[int]
    OR_FILTER_FIELD_NUMBER: _ClassVar[int]
    AND_FILTER_FIELD_NUMBER: _ClassVar[int]
    eq: EqAuthFilter
    contains: ContainsAuthFilter
    or_filter: OrAuthFilter
    and_filter: AndAuthFilter
    def __init__(self, eq: _Optional[_Union[EqAuthFilter, _Mapping]] = ..., contains: _Optional[_Union[ContainsAuthFilter, _Mapping]] = ..., or_filter: _Optional[_Union[OrAuthFilter, _Mapping]] = ..., and_filter: _Optional[_Union[AndAuthFilter, _Mapping]] = ...) -> None: ...

class UUID(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: str
    def __init__(self, value: _Optional[str] = ...) -> None: ...

class CountResponse(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...

class StreamEvent(_message.Message):
    __slots__ = ("event_type", "message", "stream_id")
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    event_type: str
    message: bytes
    stream_id: str
    def __init__(self, event_type: _Optional[str] = ..., message: _Optional[bytes] = ..., stream_id: _Optional[str] = ...) -> None: ...

class Assistant(_message.Message):
    __slots__ = ("assistant_id", "graph_id", "version", "created_at", "updated_at", "config", "context_json", "metadata_json", "name", "description")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    graph_id: str
    version: int
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    config: _engine_common_pb2.EngineRunnableConfig
    context_json: bytes
    metadata_json: bytes
    name: str
    description: str
    def __init__(self, assistant_id: _Optional[str] = ..., graph_id: _Optional[str] = ..., version: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., context_json: _Optional[bytes] = ..., metadata_json: _Optional[bytes] = ..., name: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class AssistantVersion(_message.Message):
    __slots__ = ("assistant_id", "graph_id", "version", "created_at", "config", "context_json", "metadata_json", "name", "description")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    graph_id: str
    version: int
    created_at: _timestamp_pb2.Timestamp
    config: _engine_common_pb2.EngineRunnableConfig
    context_json: bytes
    metadata_json: bytes
    name: str
    description: str
    def __init__(self, assistant_id: _Optional[str] = ..., graph_id: _Optional[str] = ..., version: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., context_json: _Optional[bytes] = ..., metadata_json: _Optional[bytes] = ..., name: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class CreateAssistantRequest(_message.Message):
    __slots__ = ("assistant_id", "graph_id", "filters", "if_exists", "config", "context_json", "name", "description", "metadata_json")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    IF_EXISTS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    graph_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    if_exists: OnConflictBehavior
    config: _engine_common_pb2.EngineRunnableConfig
    context_json: bytes
    name: str
    description: str
    metadata_json: bytes
    def __init__(self, assistant_id: _Optional[str] = ..., graph_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., if_exists: _Optional[_Union[OnConflictBehavior, str]] = ..., config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., context_json: _Optional[bytes] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., metadata_json: _Optional[bytes] = ...) -> None: ...

class GetAssistantRequest(_message.Message):
    __slots__ = ("assistant_id", "filters")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, assistant_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class PatchAssistantRequest(_message.Message):
    __slots__ = ("assistant_id", "filters", "graph_id", "config", "context_json", "name", "description", "metadata_json")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    graph_id: str
    config: _engine_common_pb2.EngineRunnableConfig
    context_json: bytes
    name: str
    description: str
    metadata_json: bytes
    def __init__(self, assistant_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., graph_id: _Optional[str] = ..., config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., context_json: _Optional[bytes] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., metadata_json: _Optional[bytes] = ...) -> None: ...

class DeleteAssistantRequest(_message.Message):
    __slots__ = ("assistant_id", "filters", "delete_threads")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    DELETE_THREADS_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    delete_threads: bool
    def __init__(self, assistant_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., delete_threads: bool = ...) -> None: ...

class DeleteAssistantsResponse(_message.Message):
    __slots__ = ("assistant_ids",)
    ASSISTANT_IDS_FIELD_NUMBER: _ClassVar[int]
    assistant_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, assistant_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class SetLatestAssistantRequest(_message.Message):
    __slots__ = ("assistant_id", "version", "filters")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    version: int
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, assistant_id: _Optional[str] = ..., version: _Optional[int] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class SearchAssistantsRequest(_message.Message):
    __slots__ = ("filters", "graph_id", "metadata_json", "limit", "offset", "sort_by", "sort_order", "select", "name")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_ORDER_FIELD_NUMBER: _ClassVar[int]
    SELECT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    graph_id: str
    metadata_json: bytes
    limit: int
    offset: int
    sort_by: AssistantsSortBy
    sort_order: SortOrder
    select: _containers.RepeatedScalarFieldContainer[str]
    name: str
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., graph_id: _Optional[str] = ..., metadata_json: _Optional[bytes] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., sort_by: _Optional[_Union[AssistantsSortBy, str]] = ..., sort_order: _Optional[_Union[SortOrder, str]] = ..., select: _Optional[_Iterable[str]] = ..., name: _Optional[str] = ...) -> None: ...

class SearchAssistantsResponse(_message.Message):
    __slots__ = ("assistants",)
    ASSISTANTS_FIELD_NUMBER: _ClassVar[int]
    assistants: _containers.RepeatedCompositeFieldContainer[Assistant]
    def __init__(self, assistants: _Optional[_Iterable[_Union[Assistant, _Mapping]]] = ...) -> None: ...

class GetAssistantVersionsRequest(_message.Message):
    __slots__ = ("assistant_id", "filters", "metadata_json", "limit", "offset")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    assistant_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    metadata_json: bytes
    limit: int
    offset: int
    def __init__(self, assistant_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., metadata_json: _Optional[bytes] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class GetAssistantVersionsResponse(_message.Message):
    __slots__ = ("versions",)
    VERSIONS_FIELD_NUMBER: _ClassVar[int]
    versions: _containers.RepeatedCompositeFieldContainer[AssistantVersion]
    def __init__(self, versions: _Optional[_Iterable[_Union[AssistantVersion, _Mapping]]] = ...) -> None: ...

class CountAssistantsRequest(_message.Message):
    __slots__ = ("filters", "graph_id", "metadata_json", "name")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    graph_id: str
    metadata_json: bytes
    name: str
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., graph_id: _Optional[str] = ..., metadata_json: _Optional[bytes] = ..., name: _Optional[str] = ...) -> None: ...

class TruncateRequest(_message.Message):
    __slots__ = ("runs", "threads", "assistants", "checkpointer", "store")
    RUNS_FIELD_NUMBER: _ClassVar[int]
    THREADS_FIELD_NUMBER: _ClassVar[int]
    ASSISTANTS_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINTER_FIELD_NUMBER: _ClassVar[int]
    STORE_FIELD_NUMBER: _ClassVar[int]
    runs: bool
    threads: bool
    assistants: bool
    checkpointer: bool
    store: bool
    def __init__(self, runs: bool = ..., threads: bool = ..., assistants: bool = ..., checkpointer: bool = ..., store: bool = ...) -> None: ...

class ThreadTTLConfig(_message.Message):
    __slots__ = ("strategy", "default_ttl", "sweep_interval_minutes")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_TTL_FIELD_NUMBER: _ClassVar[int]
    SWEEP_INTERVAL_MINUTES_FIELD_NUMBER: _ClassVar[int]
    strategy: ThreadTTLStrategy
    default_ttl: float
    sweep_interval_minutes: int
    def __init__(self, strategy: _Optional[_Union[ThreadTTLStrategy, str]] = ..., default_ttl: _Optional[float] = ..., sweep_interval_minutes: _Optional[int] = ...) -> None: ...

class Fragment(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: bytes
    def __init__(self, value: _Optional[bytes] = ...) -> None: ...

class CheckpointTask(_message.Message):
    __slots__ = ("id", "name", "error", "interrupts_json", "state_json")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    INTERRUPTS_JSON_FIELD_NUMBER: _ClassVar[int]
    STATE_JSON_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    error: str
    interrupts_json: _containers.RepeatedScalarFieldContainer[bytes]
    state_json: bytes
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., error: _Optional[str] = ..., interrupts_json: _Optional[_Iterable[bytes]] = ..., state_json: _Optional[bytes] = ...) -> None: ...

class CheckpointMetadata(_message.Message):
    __slots__ = ("source", "step", "parents")
    class ParentsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    PARENTS_FIELD_NUMBER: _ClassVar[int]
    source: CheckpointSource
    step: int
    parents: _containers.ScalarMap[str, str]
    def __init__(self, source: _Optional[_Union[CheckpointSource, str]] = ..., step: _Optional[int] = ..., parents: _Optional[_Mapping[str, str]] = ...) -> None: ...

class CheckpointPayload(_message.Message):
    __slots__ = ("config", "metadata", "values_json", "next", "parent_config", "tasks")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    VALUES_JSON_FIELD_NUMBER: _ClassVar[int]
    NEXT_FIELD_NUMBER: _ClassVar[int]
    PARENT_CONFIG_FIELD_NUMBER: _ClassVar[int]
    TASKS_FIELD_NUMBER: _ClassVar[int]
    config: _engine_common_pb2.EngineRunnableConfig
    metadata: CheckpointMetadata
    values_json: bytes
    next: _containers.RepeatedScalarFieldContainer[str]
    parent_config: _engine_common_pb2.EngineRunnableConfig
    tasks: _containers.RepeatedCompositeFieldContainer[CheckpointTask]
    def __init__(self, config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., metadata: _Optional[_Union[CheckpointMetadata, _Mapping]] = ..., values_json: _Optional[bytes] = ..., next: _Optional[_Iterable[str]] = ..., parent_config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., tasks: _Optional[_Iterable[_Union[CheckpointTask, _Mapping]]] = ...) -> None: ...

class ThreadStatusCheckpoint(_message.Message):
    __slots__ = ("values_json", "next", "interrupts_json")
    VALUES_JSON_FIELD_NUMBER: _ClassVar[int]
    NEXT_FIELD_NUMBER: _ClassVar[int]
    INTERRUPTS_JSON_FIELD_NUMBER: _ClassVar[int]
    values_json: bytes
    next: _containers.RepeatedScalarFieldContainer[str]
    interrupts_json: bytes
    def __init__(self, values_json: _Optional[bytes] = ..., next: _Optional[_Iterable[str]] = ..., interrupts_json: _Optional[bytes] = ...) -> None: ...

class Interrupt(_message.Message):
    __slots__ = ("id", "value", "when", "resumable", "ns")
    ID_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    WHEN_FIELD_NUMBER: _ClassVar[int]
    RESUMABLE_FIELD_NUMBER: _ClassVar[int]
    NS_FIELD_NUMBER: _ClassVar[int]
    id: str
    value: bytes
    when: str
    resumable: bool
    ns: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., value: _Optional[bytes] = ..., when: _Optional[str] = ..., resumable: bool = ..., ns: _Optional[_Iterable[str]] = ...) -> None: ...

class Interrupts(_message.Message):
    __slots__ = ("interrupts",)
    INTERRUPTS_FIELD_NUMBER: _ClassVar[int]
    interrupts: _containers.RepeatedCompositeFieldContainer[Interrupt]
    def __init__(self, interrupts: _Optional[_Iterable[_Union[Interrupt, _Mapping]]] = ...) -> None: ...

class Thread(_message.Message):
    __slots__ = ("thread_id", "created_at", "updated_at", "metadata", "config", "status", "values", "interrupts", "error", "extracted_json")
    class InterruptsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: Interrupts
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[Interrupts, _Mapping]] = ...) -> None: ...
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    INTERRUPTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    EXTRACTED_JSON_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    metadata: Fragment
    config: Fragment
    status: _enum_thread_status_pb2.ThreadStatus
    values: Fragment
    interrupts: _containers.MessageMap[str, Interrupts]
    error: Fragment
    extracted_json: bytes
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., metadata: _Optional[_Union[Fragment, _Mapping]] = ..., config: _Optional[_Union[Fragment, _Mapping]] = ..., status: _Optional[_Union[_enum_thread_status_pb2.ThreadStatus, str]] = ..., values: _Optional[_Union[Fragment, _Mapping]] = ..., interrupts: _Optional[_Mapping[str, Interrupts]] = ..., error: _Optional[_Union[Fragment, _Mapping]] = ..., extracted_json: _Optional[bytes] = ...) -> None: ...

class CreateThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters", "if_exists", "metadata_json", "ttl")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    IF_EXISTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    TTL_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    if_exists: OnConflictBehavior
    metadata_json: bytes
    ttl: ThreadTTLConfig
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., if_exists: _Optional[_Union[OnConflictBehavior, str]] = ..., metadata_json: _Optional[bytes] = ..., ttl: _Optional[_Union[ThreadTTLConfig, _Mapping]] = ...) -> None: ...

class GetThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class PatchThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters", "metadata_json", "ttl")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    TTL_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    metadata_json: bytes
    ttl: ThreadTTLConfig
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., metadata_json: _Optional[bytes] = ..., ttl: _Optional[_Union[ThreadTTLConfig, _Mapping]] = ...) -> None: ...

class DeleteThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class CopyThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class SearchThreadsRequest(_message.Message):
    __slots__ = ("filters", "metadata_json", "values_json", "status", "limit", "offset", "sort_by", "sort_order", "select", "extract")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    VALUES_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_ORDER_FIELD_NUMBER: _ClassVar[int]
    SELECT_FIELD_NUMBER: _ClassVar[int]
    EXTRACT_FIELD_NUMBER: _ClassVar[int]
    class ExtractEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    metadata_json: bytes
    values_json: bytes
    status: _enum_thread_status_pb2.ThreadStatus
    limit: int
    offset: int
    sort_by: ThreadsSortBy
    sort_order: SortOrder
    select: _containers.RepeatedScalarFieldContainer[str]
    extract: _containers.ScalarMap[str, str]
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., metadata_json: _Optional[bytes] = ..., values_json: _Optional[bytes] = ..., status: _Optional[_Union[_enum_thread_status_pb2.ThreadStatus, str]] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., sort_by: _Optional[_Union[ThreadsSortBy, str]] = ..., sort_order: _Optional[_Union[SortOrder, str]] = ..., select: _Optional[_Iterable[str]] = ..., extract: _Optional[_Mapping[str, str]] = ...) -> None: ...

class SearchThreadsResponse(_message.Message):
    __slots__ = ("threads",)
    THREADS_FIELD_NUMBER: _ClassVar[int]
    threads: _containers.RepeatedCompositeFieldContainer[Thread]
    def __init__(self, threads: _Optional[_Iterable[_Union[Thread, _Mapping]]] = ...) -> None: ...

class CountThreadsRequest(_message.Message):
    __slots__ = ("filters", "metadata_json", "values_json", "status")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    VALUES_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    metadata_json: bytes
    values_json: bytes
    status: _enum_thread_status_pb2.ThreadStatus
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., metadata_json: _Optional[bytes] = ..., values_json: _Optional[bytes] = ..., status: _Optional[_Union[_enum_thread_status_pb2.ThreadStatus, str]] = ...) -> None: ...

class SetThreadStatusRequest(_message.Message):
    __slots__ = ("thread_id", "checkpoint", "exception_json", "expected_status")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_FIELD_NUMBER: _ClassVar[int]
    EXCEPTION_JSON_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_STATUS_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    checkpoint: ThreadStatusCheckpoint
    exception_json: bytes
    expected_status: _containers.RepeatedScalarFieldContainer[_enum_thread_status_pb2.ThreadStatus]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., checkpoint: _Optional[_Union[ThreadStatusCheckpoint, _Mapping]] = ..., exception_json: _Optional[bytes] = ..., expected_status: _Optional[_Iterable[_Union[_enum_thread_status_pb2.ThreadStatus, str]]] = ...) -> None: ...

class SetThreadJointStatusRequest(_message.Message):
    __slots__ = ("thread_id", "run_id", "run_status", "graph_id", "checkpoint", "exception_json")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_STATUS_FIELD_NUMBER: _ClassVar[int]
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_FIELD_NUMBER: _ClassVar[int]
    EXCEPTION_JSON_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    run_id: UUID
    run_status: str
    graph_id: str
    checkpoint: ThreadStatusCheckpoint
    exception_json: bytes
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., run_id: _Optional[_Union[UUID, _Mapping]] = ..., run_status: _Optional[str] = ..., graph_id: _Optional[str] = ..., checkpoint: _Optional[_Union[ThreadStatusCheckpoint, _Mapping]] = ..., exception_json: _Optional[bytes] = ...) -> None: ...

class StreamThreadRequest(_message.Message):
    __slots__ = ("thread_id", "filters", "stream_modes", "last_event_id")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    STREAM_MODES_FIELD_NUMBER: _ClassVar[int]
    LAST_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    stream_modes: _containers.RepeatedScalarFieldContainer[_enum_thread_stream_mode_pb2.ThreadStreamMode]
    last_event_id: str
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., stream_modes: _Optional[_Iterable[_Union[_enum_thread_stream_mode_pb2.ThreadStreamMode, str]]] = ..., last_event_id: _Optional[str] = ...) -> None: ...

class RunKwargs(_message.Message):
    __slots__ = ("config", "context_json", "input_json", "command_json", "stream_mode", "interrupt_before", "interrupt_after", "webhook", "feedback_keys", "temporary", "subgraphs", "resumable", "checkpoint_during", "durability")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    INPUT_JSON_FIELD_NUMBER: _ClassVar[int]
    COMMAND_JSON_FIELD_NUMBER: _ClassVar[int]
    STREAM_MODE_FIELD_NUMBER: _ClassVar[int]
    INTERRUPT_BEFORE_FIELD_NUMBER: _ClassVar[int]
    INTERRUPT_AFTER_FIELD_NUMBER: _ClassVar[int]
    WEBHOOK_FIELD_NUMBER: _ClassVar[int]
    FEEDBACK_KEYS_FIELD_NUMBER: _ClassVar[int]
    TEMPORARY_FIELD_NUMBER: _ClassVar[int]
    SUBGRAPHS_FIELD_NUMBER: _ClassVar[int]
    RESUMABLE_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_DURING_FIELD_NUMBER: _ClassVar[int]
    DURABILITY_FIELD_NUMBER: _ClassVar[int]
    config: _engine_common_pb2.EngineRunnableConfig
    context_json: bytes
    input_json: bytes
    command_json: bytes
    stream_mode: _enum_stream_mode_pb2.StreamMode
    interrupt_before: _engine_common_pb2.StaticInterruptConfig
    interrupt_after: _engine_common_pb2.StaticInterruptConfig
    webhook: str
    feedback_keys: _containers.RepeatedScalarFieldContainer[str]
    temporary: bool
    subgraphs: bool
    resumable: bool
    checkpoint_during: bool
    durability: str
    def __init__(self, config: _Optional[_Union[_engine_common_pb2.EngineRunnableConfig, _Mapping]] = ..., context_json: _Optional[bytes] = ..., input_json: _Optional[bytes] = ..., command_json: _Optional[bytes] = ..., stream_mode: _Optional[_Union[_enum_stream_mode_pb2.StreamMode, str]] = ..., interrupt_before: _Optional[_Union[_engine_common_pb2.StaticInterruptConfig, _Mapping]] = ..., interrupt_after: _Optional[_Union[_engine_common_pb2.StaticInterruptConfig, _Mapping]] = ..., webhook: _Optional[str] = ..., feedback_keys: _Optional[_Iterable[str]] = ..., temporary: bool = ..., subgraphs: bool = ..., resumable: bool = ..., checkpoint_during: bool = ..., durability: _Optional[str] = ...) -> None: ...

class Run(_message.Message):
    __slots__ = ("run_id", "thread_id", "assistant_id", "created_at", "updated_at", "status", "metadata", "kwargs", "multitask_strategy")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    KWARGS_FIELD_NUMBER: _ClassVar[int]
    MULTITASK_STRATEGY_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    assistant_id: UUID
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    status: _enum_run_status_pb2.RunStatus
    metadata: Fragment
    kwargs: RunKwargs
    multitask_strategy: _enum_multitask_strategy_pb2.MultitaskStrategy
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., assistant_id: _Optional[_Union[UUID, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., status: _Optional[_Union[_enum_run_status_pb2.RunStatus, str]] = ..., metadata: _Optional[_Union[Fragment, _Mapping]] = ..., kwargs: _Optional[_Union[RunKwargs, _Mapping]] = ..., multitask_strategy: _Optional[_Union[_enum_multitask_strategy_pb2.MultitaskStrategy, str]] = ...) -> None: ...

class RunStats(_message.Message):
    __slots__ = ("n_pending", "n_running", "pending_runs_wait_time_max_secs", "pending_runs_wait_time_med_secs", "pending_unblocked_runs_wait_time_max_secs")
    N_PENDING_FIELD_NUMBER: _ClassVar[int]
    N_RUNNING_FIELD_NUMBER: _ClassVar[int]
    PENDING_RUNS_WAIT_TIME_MAX_SECS_FIELD_NUMBER: _ClassVar[int]
    PENDING_RUNS_WAIT_TIME_MED_SECS_FIELD_NUMBER: _ClassVar[int]
    PENDING_UNBLOCKED_RUNS_WAIT_TIME_MAX_SECS_FIELD_NUMBER: _ClassVar[int]
    n_pending: int
    n_running: int
    pending_runs_wait_time_max_secs: float
    pending_runs_wait_time_med_secs: float
    pending_unblocked_runs_wait_time_max_secs: float
    def __init__(self, n_pending: _Optional[int] = ..., n_running: _Optional[int] = ..., pending_runs_wait_time_max_secs: _Optional[float] = ..., pending_runs_wait_time_med_secs: _Optional[float] = ..., pending_unblocked_runs_wait_time_max_secs: _Optional[float] = ...) -> None: ...

class NextRunRequest(_message.Message):
    __slots__ = ("wait", "limit")
    WAIT_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    wait: bool
    limit: int
    def __init__(self, wait: bool = ..., limit: _Optional[int] = ...) -> None: ...

class RunWithAttempt(_message.Message):
    __slots__ = ("run", "attempt")
    RUN_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    run: Run
    attempt: int
    def __init__(self, run: _Optional[_Union[Run, _Mapping]] = ..., attempt: _Optional[int] = ...) -> None: ...

class NextRunResponse(_message.Message):
    __slots__ = ("runs",)
    RUNS_FIELD_NUMBER: _ClassVar[int]
    runs: _containers.RepeatedCompositeFieldContainer[RunWithAttempt]
    def __init__(self, runs: _Optional[_Iterable[_Union[RunWithAttempt, _Mapping]]] = ...) -> None: ...

class CreateRunRequest(_message.Message):
    __slots__ = ("assistant_id", "kwargs_json", "thread_filters", "assistant_filters", "thread_id", "user_id", "run_id", "status", "metadata_json", "prevent_insert_if_inflight", "multitask_strategy", "if_not_exists", "after_seconds", "thread_ttl")
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    KWARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    THREAD_FILTERS_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_FILTERS_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    PREVENT_INSERT_IF_INFLIGHT_FIELD_NUMBER: _ClassVar[int]
    MULTITASK_STRATEGY_FIELD_NUMBER: _ClassVar[int]
    IF_NOT_EXISTS_FIELD_NUMBER: _ClassVar[int]
    AFTER_SECONDS_FIELD_NUMBER: _ClassVar[int]
    THREAD_TTL_FIELD_NUMBER: _ClassVar[int]
    assistant_id: UUID
    kwargs_json: bytes
    thread_filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    assistant_filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    thread_id: UUID
    user_id: str
    run_id: UUID
    status: _enum_run_status_pb2.RunStatus
    metadata_json: bytes
    prevent_insert_if_inflight: bool
    multitask_strategy: _enum_multitask_strategy_pb2.MultitaskStrategy
    if_not_exists: CreateRunBehavior
    after_seconds: int
    thread_ttl: ThreadTTLConfig
    def __init__(self, assistant_id: _Optional[_Union[UUID, _Mapping]] = ..., kwargs_json: _Optional[bytes] = ..., thread_filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., assistant_filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., user_id: _Optional[str] = ..., run_id: _Optional[_Union[UUID, _Mapping]] = ..., status: _Optional[_Union[_enum_run_status_pb2.RunStatus, str]] = ..., metadata_json: _Optional[bytes] = ..., prevent_insert_if_inflight: bool = ..., multitask_strategy: _Optional[_Union[_enum_multitask_strategy_pb2.MultitaskStrategy, str]] = ..., if_not_exists: _Optional[_Union[CreateRunBehavior, str]] = ..., after_seconds: _Optional[int] = ..., thread_ttl: _Optional[_Union[ThreadTTLConfig, _Mapping]] = ...) -> None: ...

class GetRunRequest(_message.Message):
    __slots__ = ("run_id", "thread_id", "filters")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class DeleteRunRequest(_message.Message):
    __slots__ = ("run_id", "thread_id", "filters")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class CancelRunIdsTarget(_message.Message):
    __slots__ = ("thread_id", "run_ids")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    run_ids: _containers.RepeatedCompositeFieldContainer[UUID]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., run_ids: _Optional[_Iterable[_Union[UUID, _Mapping]]] = ...) -> None: ...

class CancelStatusTarget(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: CancelRunStatus
    def __init__(self, status: _Optional[_Union[CancelRunStatus, str]] = ...) -> None: ...

class CancelRunRequest(_message.Message):
    __slots__ = ("filters", "run_ids", "status", "action")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    run_ids: CancelRunIdsTarget
    status: CancelStatusTarget
    action: _enum_cancel_run_action_pb2.CancelRunAction
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., run_ids: _Optional[_Union[CancelRunIdsTarget, _Mapping]] = ..., status: _Optional[_Union[CancelStatusTarget, _Mapping]] = ..., action: _Optional[_Union[_enum_cancel_run_action_pb2.CancelRunAction, str]] = ...) -> None: ...

class SearchRunsRequest(_message.Message):
    __slots__ = ("thread_id", "filters", "limit", "offset", "status", "select")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SELECT_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    limit: int
    offset: int
    status: _enum_run_status_pb2.RunStatus
    select: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., status: _Optional[_Union[_enum_run_status_pb2.RunStatus, str]] = ..., select: _Optional[_Iterable[str]] = ...) -> None: ...

class SearchRunsResponse(_message.Message):
    __slots__ = ("runs",)
    RUNS_FIELD_NUMBER: _ClassVar[int]
    runs: _containers.RepeatedCompositeFieldContainer[Run]
    def __init__(self, runs: _Optional[_Iterable[_Union[Run, _Mapping]]] = ...) -> None: ...

class SetRunStatusRequest(_message.Message):
    __slots__ = ("run_id", "status")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    status: _enum_run_status_pb2.RunStatus
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., status: _Optional[_Union[_enum_run_status_pb2.RunStatus, str]] = ...) -> None: ...

class SweepRunsResponse(_message.Message):
    __slots__ = ("run_ids",)
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    run_ids: _containers.RepeatedCompositeFieldContainer[UUID]
    def __init__(self, run_ids: _Optional[_Iterable[_Union[UUID, _Mapping]]] = ...) -> None: ...

class CountRunsRequest(_message.Message):
    __slots__ = ("thread_id", "statuses")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    statuses: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., statuses: _Optional[_Iterable[str]] = ...) -> None: ...

class StreamRunClientMessage(_message.Message):
    __slots__ = ("subscribe", "join")
    SUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    JOIN_FIELD_NUMBER: _ClassVar[int]
    subscribe: SubscribeRunRequest
    join: JoinRunRequest
    def __init__(self, subscribe: _Optional[_Union[SubscribeRunRequest, _Mapping]] = ..., join: _Optional[_Union[JoinRunRequest, _Mapping]] = ...) -> None: ...

class SubscribeRunRequest(_message.Message):
    __slots__ = ("thread_id", "run_id")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    run_id: UUID
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ..., run_id: _Optional[_Union[UUID, _Mapping]] = ...) -> None: ...

class JoinRunRequest(_message.Message):
    __slots__ = ("filters", "stream_modes", "ignore_run_not_found", "cancel_on_disconnect", "last_event_id")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    STREAM_MODES_FIELD_NUMBER: _ClassVar[int]
    IGNORE_RUN_NOT_FOUND_FIELD_NUMBER: _ClassVar[int]
    CANCEL_ON_DISCONNECT_FIELD_NUMBER: _ClassVar[int]
    LAST_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    stream_modes: _containers.RepeatedScalarFieldContainer[_enum_stream_mode_pb2.StreamMode]
    ignore_run_not_found: bool
    cancel_on_disconnect: bool
    last_event_id: str
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., stream_modes: _Optional[_Iterable[_Union[_enum_stream_mode_pb2.StreamMode, str]]] = ..., ignore_run_not_found: bool = ..., cancel_on_disconnect: bool = ..., last_event_id: _Optional[str] = ...) -> None: ...

class EnterRunRequest(_message.Message):
    __slots__ = ("run_id", "thread_id", "resumable")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RESUMABLE_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    resumable: bool
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., resumable: bool = ...) -> None: ...

class MarkRunDoneRequest(_message.Message):
    __slots__ = ("run_id", "thread_id", "resumable")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RESUMABLE_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    resumable: bool
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., resumable: bool = ...) -> None: ...

class PublishStreamEventRequest(_message.Message):
    __slots__ = ("run_id", "thread_id", "event_type", "message", "resumable")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RESUMABLE_FIELD_NUMBER: _ClassVar[int]
    run_id: UUID
    thread_id: UUID
    event_type: str
    message: bytes
    resumable: bool
    def __init__(self, run_id: _Optional[_Union[UUID, _Mapping]] = ..., thread_id: _Optional[_Union[UUID, _Mapping]] = ..., event_type: _Optional[str] = ..., message: _Optional[bytes] = ..., resumable: bool = ...) -> None: ...

class GetGraphIDRequest(_message.Message):
    __slots__ = ("thread_id",)
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    thread_id: UUID
    def __init__(self, thread_id: _Optional[_Union[UUID, _Mapping]] = ...) -> None: ...

class GetGraphIDResponse(_message.Message):
    __slots__ = ("graph_id",)
    GRAPH_ID_FIELD_NUMBER: _ClassVar[int]
    graph_id: str
    def __init__(self, graph_id: _Optional[str] = ...) -> None: ...

class CreateRunResponse(_message.Message):
    __slots__ = ("runs",)
    RUNS_FIELD_NUMBER: _ClassVar[int]
    runs: _containers.RepeatedCompositeFieldContainer[Run]
    def __init__(self, runs: _Optional[_Iterable[_Union[Run, _Mapping]]] = ...) -> None: ...

class ControlEvent(_message.Message):
    __slots__ = ("action",)
    ACTION_FIELD_NUMBER: _ClassVar[int]
    action: _enum_control_signal_pb2.ControlSignal
    def __init__(self, action: _Optional[_Union[_enum_control_signal_pb2.ControlSignal, str]] = ...) -> None: ...

class Cron(_message.Message):
    __slots__ = ("cron_id", "assistant_id", "thread_id", "on_run_completed", "end_time", "schedule", "created_at", "updated_at", "user_id", "payload_json", "next_run_date", "metadata_json")
    CRON_ID_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    ON_RUN_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    END_TIME_FIELD_NUMBER: _ClassVar[int]
    SCHEDULE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    NEXT_RUN_DATE_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    cron_id: str
    assistant_id: str
    thread_id: str
    on_run_completed: str
    end_time: _timestamp_pb2.Timestamp
    schedule: str
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    user_id: str
    payload_json: bytes
    next_run_date: _timestamp_pb2.Timestamp
    metadata_json: bytes
    def __init__(self, cron_id: _Optional[str] = ..., assistant_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., on_run_completed: _Optional[str] = ..., end_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., schedule: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., user_id: _Optional[str] = ..., payload_json: _Optional[bytes] = ..., next_run_date: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., metadata_json: _Optional[bytes] = ...) -> None: ...

class CreateCronRequest(_message.Message):
    __slots__ = ("filters", "schedule", "payload_json", "metadata_json", "cron_id", "thread_id", "on_run_completed", "end_time")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    SCHEDULE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    CRON_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    ON_RUN_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    END_TIME_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    schedule: str
    payload_json: bytes
    metadata_json: bytes
    cron_id: str
    thread_id: str
    on_run_completed: str
    end_time: _timestamp_pb2.Timestamp
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., schedule: _Optional[str] = ..., payload_json: _Optional[bytes] = ..., metadata_json: _Optional[bytes] = ..., cron_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., on_run_completed: _Optional[str] = ..., end_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class DeleteCronRequest(_message.Message):
    __slots__ = ("cron_id", "filters")
    CRON_ID_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    cron_id: str
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    def __init__(self, cron_id: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ...) -> None: ...

class SearchCronsRequest(_message.Message):
    __slots__ = ("filters", "assistant_id", "thread_id", "limit", "offset", "sort_by", "sort_order", "select")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_ORDER_FIELD_NUMBER: _ClassVar[int]
    SELECT_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    assistant_id: str
    thread_id: str
    limit: int
    offset: int
    sort_by: CronsSortBy
    sort_order: SortOrder
    select: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., assistant_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., sort_by: _Optional[_Union[CronsSortBy, str]] = ..., sort_order: _Optional[_Union[SortOrder, str]] = ..., select: _Optional[_Iterable[str]] = ...) -> None: ...

class SearchCronsResponse(_message.Message):
    __slots__ = ("crons",)
    CRONS_FIELD_NUMBER: _ClassVar[int]
    crons: _containers.RepeatedCompositeFieldContainer[Cron]
    def __init__(self, crons: _Optional[_Iterable[_Union[Cron, _Mapping]]] = ...) -> None: ...

class CountCronsRequest(_message.Message):
    __slots__ = ("filters", "assistant_id", "thread_id")
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    filters: _containers.RepeatedCompositeFieldContainer[AuthFilter]
    assistant_id: str
    thread_id: str
    def __init__(self, filters: _Optional[_Iterable[_Union[AuthFilter, _Mapping]]] = ..., assistant_id: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...
