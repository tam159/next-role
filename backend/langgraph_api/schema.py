from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, NotRequired, Optional, TypeAlias
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig
from typing_extensions import TypedDict

from langgraph_api.serde import Fragment

MetadataInput = dict[str, Any] | None
MetadataValue = dict[str, Any]

RunStatus = Literal["pending", "running", "error", "success", "timeout", "interrupted"]

ThreadStatus = Literal["idle", "busy", "interrupted", "error"]

StreamMode = Literal[
    "values",
    "messages",
    "updates",
    "events",
    "debug",
    "tasks",
    "checkpoints",
    "custom",
    "messages-tuple",
    "tools",
    "lifecycle",
]

ThreadStreamMode = Literal["lifecycle", "run_modes", "state_update"]

MultitaskStrategy = Literal["reject", "rollback", "interrupt", "enqueue"]

OnConflictBehavior = Literal["raise", "do_nothing"]

OnCompletion = Literal["delete", "keep"]

IfNotExists = Literal["create", "reject"]

All = Literal["*"]

Context: TypeAlias = dict[str, Any]


class Config(TypedDict, total=False):
    tags: list[str]
    """
    Tags for this call and any sub-calls (eg. a Chain calling an LLM).
    You can use these to filter calls.
    """

    recursion_limit: int
    """
    Maximum number of times a call can recurse. If not provided, defaults to 25.
    """

    configurable: dict[str, Any]
    """
    Runtime values for attributes previously made configurable on this Runnable,
    or sub-Runnables, through .configurable_fields() or .configurable_alternatives().
    Check .output_schema() for a description of the attributes that have been made
    configurable.
    """

    __encryption_context__: dict[str, Any]
    """
    Internal: Encryption context for encryption/decryption operations.
    Not exposed to users.
    """


class PostgresPoolStats(TypedDict, total=False):
    """Postgres connection pool metrics. All keys optional for merge/partial results."""

    pool_max: int
    pool_size: int
    pool_available: int
    requests_queued: int
    requests_errors: int


class RedisPoolStats(TypedDict, total=False):
    """Redis connection pool metrics. All keys optional for merge/partial results."""

    idle_connections: int
    in_use_connections: int
    max_connections: int
    max_connections_per_node: int


class PoolStats(TypedDict, total=False):
    """Top-level pool stats: optional postgres and/or redis sections."""

    postgres: PostgresPoolStats
    redis: RedisPoolStats


class Checkpoint(TypedDict):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str | None
    checkpoint_map: dict[str, Any] | None


class Assistant(TypedDict, total=False):
    """Assistant model."""

    assistant_id: UUID
    """The ID of the assistant."""
    graph_id: str
    """The ID of the graph."""
    name: str
    """The name of the assistant."""
    description: str | None
    """The description of the assistant."""
    config: Config
    """The assistant config."""
    context: Fragment
    """The static context of the assistant."""
    created_at: datetime
    """The time the assistant was created."""
    updated_at: datetime
    """The last time the assistant was updated."""
    metadata: Fragment
    """The assistant metadata."""
    version: int
    """The assistant version."""


class Interrupt(TypedDict):
    id: str | None
    """The ID of the interrupt."""
    value: Any
    """The value of the interrupt."""


class DeprecatedInterrupt(TypedDict, total=False):
    """We document this old interrupt format internally, but not in API spec.

    Should be dropped with lg-api v1.0.0.
    """

    id: str | None
    """The ID of the interrupt."""
    value: Any
    """The value of the interrupt."""
    resumable: bool
    """Whether the interrupt is resumable."""
    ns: Sequence[str] | None
    """The optional namespace of the interrupt."""
    when: Literal["during"]
    """When the interrupt occurred, always "during"."""


class ThreadTTLInfo(TypedDict, total=False):
    """TTL information for a thread. Only present when ?include=ttl is passed."""

    strategy: Literal["delete", "keep_latest"]
    """The TTL strategy."""
    ttl_minutes: float
    """The TTL in minutes."""
    expires_at: datetime
    """When the thread will expire."""


class Thread(TypedDict):
    thread_id: UUID
    """The ID of the thread."""
    created_at: datetime
    """The time the thread was created."""
    updated_at: datetime
    """The last time the thread was updated."""
    state_updated_at: datetime
    """The last time the thread state was updated."""
    metadata: Fragment
    """The thread metadata."""
    config: Fragment
    """The thread config."""
    status: ThreadStatus
    """The status of the thread. One of 'idle', 'busy', 'interrupted', "error"."""
    values: Fragment
    """The current state of the thread."""
    interrupts: dict[str, list[Interrupt]]
    """The current interrupts of the thread, a map of task_id to list of interrupts."""
    ttl: NotRequired[ThreadTTLInfo]
    """TTL information if set for this thread. Only present when ?include=ttl is passed."""
    extracted: NotRequired[dict[str, Any]]
    """Extracted values from thread JSONB columns, populated when extract is specified."""


class ThreadTask(TypedDict):
    id: str
    name: str
    error: str | None
    interrupts: list[Interrupt]
    checkpoint: Checkpoint | None
    state: Optional["ThreadState"]


class ThreadState(TypedDict):
    values: dict[str, Any]
    """The state values."""
    next: Sequence[str]
    """The name of the node to execute in each task for this step."""
    checkpoint: Checkpoint
    """The checkpoint keys. This object can be passed to the /threads and /runs
    endpoints to resume execution or update state."""
    metadata: Fragment
    """Metadata for this state"""
    created_at: str | None
    """Timestamp of state creation"""
    parent_checkpoint: Checkpoint | None
    """The parent checkpoint. If missing, this is the root checkpoint."""
    tasks: Sequence[ThreadTask]
    """Tasks to execute in this step. If already attempted, may contain an error."""
    interrupts: list[Interrupt]
    """The interrupts for this state."""


class RunKwargs(TypedDict):
    config: RunnableConfig
    context: dict[str, Any]
    input: dict[str, Any] | None
    command: dict[str, Any] | None
    stream_mode: StreamMode
    interrupt_before: Sequence[str] | str | None
    interrupt_after: Sequence[str] | str | None
    webhook: str | None
    feedback_keys: Sequence[str] | None
    temporary: bool
    subgraphs: bool
    resumable: bool
    checkpoint_during: bool
    durability: str | None


class Run(TypedDict):
    run_id: UUID
    """The ID of the run."""
    thread_id: UUID
    """The ID of the thread."""
    assistant_id: UUID
    """The assistant that was used for this run."""
    created_at: datetime
    """The time the run was created."""
    updated_at: datetime
    """The last time the run was updated."""
    status: RunStatus
    """The status of the run. One of 'pending', 'error', 'success'."""
    metadata: Fragment
    """The run metadata."""
    kwargs: RunKwargs
    """The run kwargs."""
    multitask_strategy: MultitaskStrategy
    """Strategy to handle concurrent runs on the same thread."""


class RunSend(TypedDict):
    node: str
    input: dict[str, Any] | None


class RunCommand(TypedDict):
    goto: str | RunSend | Sequence[RunSend | str] | None
    update: dict[str, Any] | Sequence[tuple[str, Any]] | None
    resume: Any | None


class Cron(TypedDict):
    """Cron model."""

    cron_id: UUID
    """The ID of the cron."""
    assistant_id: UUID
    """The ID of the assistant."""
    thread_id: UUID | None
    """The ID of the thread."""
    on_run_completed: NotRequired[Literal["delete", "keep"] | None]
    """What to do with the thread after the run completes."""
    end_time: datetime | None
    """The end date to stop running the cron."""
    schedule: str
    """The schedule to run, cron format."""
    timezone: str | None
    """IANA timezone for the cron schedule (e.g. 'America/New_York'). Defaults to null, which is treated as UTC."""
    created_at: datetime
    """The time the cron was created."""
    updated_at: datetime
    """The last time the cron was updated."""
    user_id: str | None
    """The ID of the user (string identity)."""
    payload: Fragment
    """The run payload to use for creating new run."""
    next_run_date: datetime
    """The next run date of the cron."""
    metadata: Fragment
    """The cron metadata."""
    now: NotRequired[datetime]
    """The current time (present in internal next() only)."""
    enabled: bool
    """Whether the cron is enabled."""


class ThreadUpdateResponse(TypedDict):
    """Response for updating a thread."""

    checkpoint: Checkpoint


class QueueStats(TypedDict):
    n_pending: int
    n_running: int
    pending_runs_wait_time_max_secs: float | None
    pending_runs_wait_time_med_secs: float | None
    pending_unblocked_runs_wait_time_max_secs: float | None


# Canonical field sets for select= validation and type aliases for ops

# Assistant select fields (intentionally excludes 'context')
AssistantSelectField = Literal[
    "assistant_id",
    "graph_id",
    "name",
    "description",
    "config",
    "context",
    "created_at",
    "updated_at",
    "metadata",
    "version",
]
ASSISTANT_FIELDS: set[str] = set(AssistantSelectField.__args__)

# Thread select fields
ThreadSelectField = Literal[
    "thread_id",
    "created_at",
    "updated_at",
    "state_updated_at",
    "metadata",
    "config",
    "status",
    "values",
    "interrupts",
]
THREAD_FIELDS: set[str] = set(ThreadSelectField.__args__)

# Run select fields
RunSelectField = Literal[
    "run_id",
    "thread_id",
    "assistant_id",
    "created_at",
    "updated_at",
    "status",
    "metadata",
    "kwargs",
    "multitask_strategy",
]
RUN_FIELDS: set[str] = set(RunSelectField.__args__)

# Cron select fields
CronSelectField = Literal[
    "cron_id",
    "assistant_id",
    "thread_id",
    "on_run_completed",
    "end_time",
    "schedule",
    "timezone",
    "created_at",
    "updated_at",
    "user_id",
    "payload",
    "next_run_date",
    "metadata",
    "enabled",
]
CRON_FIELDS: set[str] = set(CronSelectField.__args__)

# Encryption field constants
# These define which fields are encrypted for each model type.
#
# Note: Checkpoint encryption (checkpoint, metadata columns in checkpoints table, plus
# blob data in checkpoint_blobs and checkpoint_writes) is handled directly by the
# Checkpointer class in storage_postgres/langgraph_runtime_postgres/checkpoint.py.
# The checkpointer uses encrypt_json_if_needed/decrypt_json_if_needed directly rather
# than the field list pattern used by the API middleware. This is because checkpoints
# are only accessed via the checkpointer's internal methods (aget_tuple, aput, etc.),
# not through generic API CRUD operations.

THREAD_ENCRYPTION_FIELDS = ["metadata", "config", "values", "interrupts", "error"]

# kwargs is a nested blob - its subfields are decrypted automatically by the middleware
RUN_ENCRYPTION_FIELDS = ["metadata", "kwargs"]

ASSISTANT_ENCRYPTION_FIELDS = ["metadata", "config", "context"]

# payload is a nested blob - its subfields are decrypted automatically by the middleware
CRON_ENCRYPTION_FIELDS = ["metadata", "payload"]

# Store encryption - only the value field contains user data
STORE_ENCRYPTION_FIELDS = ["value"]

# The middleware automatically decrypts these subfields when decrypting the parent field.
# This is recursive: if a subfield is also in NESTED_ENCRYPTED_SUBFIELDS, its subfields
# are decrypted too (e.g., run.kwargs.config.configurable).
NESTED_ENCRYPTED_SUBFIELDS: dict[tuple[str, str], list[str]] = {
    ("run", "kwargs"): ["input", "config", "context", "command"],
    ("run", "config"): ["configurable", "metadata"],
    ("cron", "payload"): ["metadata", "context", "input", "config"],
    ("cron", "config"): ["configurable", "metadata"],
    ("assistant", "config"): ["configurable"],
    ("thread", "config"): ["configurable"],
}

# Convenience alias for cron payload subfields.
#
# This is a reflection of an unfortunate asymmetry in cron's data model.
#
# The cron API requests have payload fields (metadata, input, config, context) at the
# top level, but at rest they're nested inside the `payload` JSONB column (with
# metadata also duplicated as a top-level column). This alias is used to encrypt
# those fields in the flat request before storage.
CRON_PAYLOAD_ENCRYPTION_SUBFIELDS = NESTED_ENCRYPTED_SUBFIELDS[("cron", "payload")]

# Convenience alias for run kwargs subfields, used by the worker for decryption.
RUN_KWARGS_ENCRYPTION_SUBFIELDS = NESTED_ENCRYPTED_SUBFIELDS[("run", "kwargs")]

# Fields that should NEVER be encrypted in ANY context.
# These are system fields with unique prefixes or names that are safe to skip globally.
NEVER_ENCRYPT_FIELDS_GLOBAL: frozenset[str] = frozenset(
    {
        # System identifiers (UUIDs, not user-provided)
        "thread_id",
        "run_id",
        "assistant_id",
        "graph_id",
        "checkpoint_id",
        "task_id",
        # Internal __pregel_* fields - uniquely named, safe to skip everywhere
        "__pregel_checkpointer",
        "__pregel_resuming",
        "__pregel_durability",
        "__pregel_stream",
        "__pregel_task_id",
        "__pregel_checkpoint_ns",
        # Internal timing/scheduling fields
        "__after_seconds__",
        "__request_start_time_ms__",
        # Encryption context markers (must stay plaintext for routing)
        "__encryption_context__",
        "__blob_encryption_context__",
        # LangGraph system fields (mirrors AES_JSON_DISALLOWED_KEYS)
        "langgraph_version",
        "langgraph_api_version",
        "langgraph_plan",
        "langgraph_host",
        "langgraph_api_url",
        "langgraph_request_id",
        "langgraph_auth_user_id",
        "langgraph_auth_permissions",
    }
)

# Path-based skip rules for encryption.
# Format: "model_type.field.subfield...key" using dot notation.
# These allow surgical control over which fields skip encryption at specific locations.
# Use "*" as a wildcard segment to match any field at that level.
#
# Examples:
#   "run.kwargs.config.configurable.ttl" - skip ttl only in run's configurable
NEVER_ENCRYPT_PATHS: frozenset[str] = frozenset(
    {
        # Run execution parameters
        "run.kwargs.config.recursion_limit",
        "run.kwargs.config.max_concurrency",
        # Temporary flag - must stay plaintext for SQL: (kwargs->>'temporary')::boolean
        "run.kwargs.temporary",
        # Thread TTL in run configurable - needs to stay plaintext for system to apply
        "run.kwargs.config.configurable.ttl",
        # Checkpoint metadata execution state - system-controlled, not user data
        "checkpoint_metadata.source",
        "checkpoint_metadata.step",
        "checkpoint_metadata.parents",
        "checkpoint_metadata.run_attempt",
    }
)
