import os
from os import environ, getenv
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar, cast

import structlog
from pydantic.functional_validators import AfterValidator
from starlette.config import Config, undefined
from starlette.datastructures import CommaSeparatedStrings

from server.api import traceblock
from server.api.config import _parse
from server.api.config.schemas import (
    AuthConfig,
    CheckpointerConfig,
    CorsConfig,
    EncryptionConfig,
    HttpConfig,
    SerdeConfig,
    StoreConfig,
    ThreadTTLConfig,
    TTLConfig,
    WebhooksConfig,
    webhooks_validator,
)

if TYPE_CHECKING:
    from collections.abc import Callable

from dotenv import load_dotenv

load_dotenv()

# env

env = Config()

logger = structlog.stdlib.get_logger(__name__)


TD = TypeVar("TD")


STATS_INTERVAL_SECS = env("STATS_INTERVAL_SECS", cast=int, default=60)

# storage

# Not in public docs: infrastructure, set by platform
DATABASE_URI = env("DATABASE_URI", cast=str, default=getenv("POSTGRES_URI", undefined))
# Not in public docs: infrastructure, set by platform
MIGRATIONS_PATH = env("MIGRATIONS_PATH", cast=str, default="/storage/migrations")
POSTGRES_POOL_MAX_SIZE = env("LANGGRAPH_POSTGRES_POOL_MAX_SIZE", cast=int, default=150)
CHECKPOINTER_POSTGRES_POOL_MIN_SIZE = env(
    "LANGGRAPH_CHECKPOINTER_POSTGRES_POOL_MIN_SIZE",
    cast=int,
    default=1,
)
CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS = env(
    "LANGGRAPH_CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS",
    cast=float,
    default=15.0,
)

# Checkpoint ingestion batch controls
# Go defaults (core/config/config.go): CHECKPOINT_MAX_BATCH_SIZE=1000, CHECKPOINT_BATCH_DELAY=0.005 (5ms)
# CHECKPOINT_BATCH_DELAY uses float seconds to match Go's time.Duration env parsing convention.
# storage_postgres/Makefile sets these to the Go defaults for CI/local dev.
# TODO(braa): flip these to match Go defaults once validated in production (LSD-1404)
CHECKPOINT_MAX_BATCH_SIZE: int | None = env(
    "CHECKPOINT_MAX_BATCH_SIZE",
    cast=int,
    default=None,
)
CHECKPOINT_BATCH_DELAY: float = env("CHECKPOINT_BATCH_DELAY", cast=float, default=0.0)

RESUMABLE_STREAM_TTL_SECONDS = env(
    "RESUMABLE_STREAM_TTL_SECONDS",
    cast=int,
    default=120,  # 2 minutes
)


def _parse_aes_key(key_str: str | None) -> bytes | None:
    """Parse and validate the AES encryption key from string.

    Args:
        key_str: The key string from LANGGRAPH_AES_KEY env var

    Returns:
        The key as bytes, or None if not set

    Raises:
        ValueError: If key is not 16, 24, or 32 bytes (AES-128/192/256)
    """
    if not key_str:
        return None
    key = key_str.encode(encoding="utf-8")
    if len(key) not in (16, 24, 32):
        raise ValueError("LANGGRAPH_AES_KEY must be 16, 24, or 32 bytes long.")
    return key


LANGGRAPH_AES_KEY = env("LANGGRAPH_AES_KEY", default=None, cast=_parse_aes_key)

# System-populated fields that cannot be encrypted (would break functionality)
AES_JSON_DISALLOWED_KEYS = frozenset(
    {
        "langgraph_version",
        "api_version",
        "langgraph_plan",
        "langgraph_host",
        "api_url",
        "langgraph_request_id",
        "langgraph_auth_user_id",
        "langgraph_auth_permissions",
    },
)


def _get_aes_json_keys(keys_str: str | None) -> frozenset[str] | None:
    """Parse LANGGRAPH_AES_JSON_KEYS comma-separated list.

    Validates:
    - No disallowed system keys
    - LANGGRAPH_AES_KEY must be set
    """
    if not keys_str:
        return None
    keys = frozenset(k.strip() for k in keys_str.split(",") if k.strip())
    if not keys:
        return None

    # Check for disallowed keys
    disallowed = keys & AES_JSON_DISALLOWED_KEYS
    if disallowed:
        raise ValueError(
            f"LANGGRAPH_AES_JSON_KEYS contains disallowed system keys: {sorted(disallowed)}. "
            f"These keys are used internally and cannot be encrypted. Remove them from LANGGRAPH_AES_JSON_KEYS",
        )

    # Require AES key to be set
    if LANGGRAPH_AES_KEY is None:
        raise ValueError(
            "LANGGRAPH_AES_JSON_KEYS requires LANGGRAPH_AES_KEY to be set.",
        )

    return keys


LANGGRAPH_AES_JSON_KEYS: frozenset[str] | None = env(
    "LANGGRAPH_AES_JSON_KEYS",
    default=None,
    cast=_get_aes_json_keys,
)

# redis
# Not in public docs: infrastructure, set by platform
REDIS_URI = env("REDIS_URI_CUSTOM", cast=str, default="") or env("REDIS_URI", cast=str)
REDIS_CLUSTER = env("REDIS_CLUSTER", cast=bool, default=False)
REDIS_MAX_CONNECTIONS = env("REDIS_MAX_CONNECTIONS", cast=int, default=2000)
REDIS_CONNECT_TIMEOUT = env("REDIS_CONNECT_TIMEOUT", cast=float, default=10.0)
REDIS_HEALTH_CHECK_INTERVAL = env(
    "REDIS_HEALTH_CHECK_INTERVAL",
    cast=float,
    default=10.0,
)
REDIS_KEY_PREFIX = env("REDIS_KEY_PREFIX", cast=str, default="")
# CA bundle (contents, not a path) for verifying Redis TLS. Must be
# base64-encoded PEM (base64 sidesteps newline-handling issues in
# env/YAML plumbing). Required for Memorystore for Redis Cluster with
# in-transit encryption.
REDIS_TLS_CA_CERT = env("REDIS_TLS_CA_CERT", cast=str, default="")
# GCP service account key JSON used to obtain access tokens for IAM-authed
# Memorystore for Redis Cluster. When set, every new Redis connection AUTHs
# with "default" + a fresh access token minted from this key.
REDIS_GCP_SERVICE_ACCOUNT_JSON = env(
    "REDIS_GCP_SERVICE_ACCOUNT_JSON",
    cast=str,
    default="",
)
RUN_STATS_CACHE_SECONDS = env("RUN_STATS_CACHE_SECONDS", cast=int, default=60)

# server
ALLOW_PRIVATE_NETWORK = env("ALLOW_PRIVATE_NETWORK", cast=bool, default=False)
"""Only enable for langgraph dev when server is running on loopback address.

See https://developer.chrome.com/blog/private-network-access-update-2024-03
"""

# gRPC client pool size for persistence server.
GRPC_CLIENT_POOL_SIZE = env("GRPC_CLIENT_POOL_SIZE", cast=int, default=5)

# HTTP request body size limit (default matches gRPC limits: 300MB)
HTTP_MAX_REQUEST_BODY_BYTES = env(
    "HTTP_MAX_REQUEST_BODY_BYTES",
    cast=int,
    default=300 * 1024 * 1024,
)

# gRPC message size limits (300MB default)
# Not in public docs: infrastructure, set by platform
LSD_GRPC_SERVER_MAX_RECV_MSG_BYTES = env(
    "LSD_GRPC_SERVER_MAX_RECV_MSG_BYTES",
    cast=int,
    default=300 * 1024 * 1024,
)
# Not in public docs: infrastructure, set by platform
LSD_GRPC_SERVER_MAX_SEND_MSG_BYTES = env(
    "LSD_GRPC_SERVER_MAX_SEND_MSG_BYTES",
    cast=int,
    default=300 * 1024 * 1024,
)
LSD_PUBLISH_QUEUE_SIZE = env("LSD_PUBLISH_QUEUE_SIZE", cast=int, default=512)
# Per-run protocol v2 event buffer size. Each ``EventStreamingSession``
# retains at most this many normalized protocol events for
# reconnecting clients to replay via the body ``since`` cursor.
# When the limit is exceeded, oldest events are evicted; reconnects
# whose cursor falls behind the buffer head receive a ``resume_gap``
# error. Operators running graphs that emit >10k events per run can
# bump this to avoid gaps on flaky networks, at the cost of memory
# (roughly ``size * avg_event_bytes`` per active run). See
# ``api.event_streaming.constants.DEFAULT_MAX_BUFFER_SIZE``.
_DEFAULT_PROTOCOL_V2_BUFFER_SIZE = 10_000
LSD_PROTOCOL_V2_BUFFER_SIZE = env(
    "LSD_PROTOCOL_V2_BUFFER_SIZE",
    cast=int,
    default=_DEFAULT_PROTOCOL_V2_BUFFER_SIZE,
)
# Not in public docs: infrastructure, set by platform
GRPC_CLIENT_MAX_RECV_MSG_BYTES = env(
    "GRPC_CLIENT_MAX_RECV_MSG_BYTES",
    cast=int,
    default=300 * 1024 * 1024,
)
# Not in public docs: infrastructure, set by platform
GRPC_CLIENT_MAX_SEND_MSG_BYTES = env(
    "GRPC_CLIENT_MAX_SEND_MSG_BYTES",
    cast=int,
    default=300 * 1024 * 1024,
)
GRPC_CLIENT_HTTP2_INITIAL_WINDOW_SIZE = env(
    "GRPC_CLIENT_HTTP2_INITIAL_WINDOW_SIZE",
    cast=int,
    default=64 * 1024,
)
LSD_GRPC_SERVER_ADDRESS = env(
    "LSD_GRPC_SERVER_ADDRESS",
    cast=str,
    default="localhost:50052",
)

# Python gRPC server settings (for encryption/checkpointer services called by Go)
# By default, binds to loopback interface only for security.
# Set PYTHON_GRPC_BIND_HOST=0.0.0.0 to allow external connections (e.g., for CI testing).
PYTHON_GRPC_SERVER_PORT = 50071
PYTHON_GRPC_BIND_HOST = env("PYTHON_GRPC_BIND_HOST", cast=str, default="127.0.0.1")

# Minimum payload size to use the dedicated thread pool for JSON parsing.
# (Otherwise, the payload is parsed directly in the event loop.)
JSON_THREAD_POOL_MINIMUM_SIZE_BYTES = 100 * 1024  # 100 KB

# Not in public docs: populated by langgraph.json config, not set as env var directly
HTTP_CONFIG = env("LANGGRAPH_HTTP", cast=_parse.parse_schema(HttpConfig), default=None)
MCP_ENABLED = HTTP_CONFIG is None or not HTTP_CONFIG.get("disable_mcp")
A2A_ENABLED = HTTP_CONFIG is None or not HTTP_CONFIG.get("disable_a2a")
WEBHOOKS_ENABLED = HTTP_CONFIG and HTTP_CONFIG.get("disable_webhooks")
# Not in public docs: populated by langgraph.json config, not set as env var directly
STORE_CONFIG = env(
    "LANGGRAPH_STORE",
    cast=_parse.parse_schema(StoreConfig),
    default=None,
)


def _validate_mount_prefix(mount_prefix: str | None) -> str | None:
    if not mount_prefix:
        return None
    if not mount_prefix.startswith("/"):
        raise ValueError(
            f"Invalid mount_prefix '{mount_prefix}': Must start with '/'. "
            f"Valid examples: '/my-api', '/v1', '/api/v1'.\nInvalid examples: 'api/', '/api/'",
        )
    if mount_prefix.endswith("/"):
        mount_prefix = mount_prefix[:-1]
    if mount_prefix == "/noauth" or mount_prefix.startswith("/noauth/"):
        raise ValueError(
            f"Invalid mount_prefix '{mount_prefix}': '/noauth' is reserved for internal SDK loopback requests.",
        )
    return mount_prefix


MOUNT_PREFIX: str | None = _validate_mount_prefix(
    env("MOUNT_PREFIX", cast=str, default=None)
    or (HTTP_CONFIG.get("mount_prefix") if HTTP_CONFIG else None),
)

CORS_ALLOW_ORIGINS = env("CORS_ALLOW_ORIGINS", cast=CommaSeparatedStrings, default="*")
CORS_CONFIG = env(
    "CORS_CONFIG",
    cast=_parse.parse_schema(CorsConfig),
    default=None,
) or (HTTP_CONFIG.get("cors") if HTTP_CONFIG else None)
"""
{
    "type": "object",
    "properties": {
        "allow_origins": {
            "type": "array",
            "items": {"type": "string"},
            "default": []
        },
        "allow_methods": {
            "type": "array",
            "items": {"type": "string"},
            "default": ["GET"]
        },
        "allow_headers": {
            "type": "array",
            "items": {"type": "string"},
            "default": []
        },
        "allow_credentials": {
            "type": "boolean",
            "default": false
        },
        "allow_origin_regex": {
            "type": ["string", "null"],
            "default": null
        },
        "expose_headers": {
            "type": "array",
            "items": {"type": "string"},
            "default": []
        },
        "max_age": {
            "type": "integer",
            "default": 600
        }
    }
}
"""
if (
    CORS_CONFIG is not None
    and CORS_ALLOW_ORIGINS != "*"
    and CORS_CONFIG.get("allow_origins") is None
):
    CORS_CONFIG["allow_origins"] = CORS_ALLOW_ORIGINS

# queue

BG_JOB_HEARTBEAT = 120  # seconds
BG_JOB_INTERVAL = 30  # seconds
BG_JOB_MAX_RETRIES = env("BG_JOB_MAX_RETRIES", cast=int, default=3)
BG_JOB_ISOLATED_LOOPS = env("BG_JOB_ISOLATED_LOOPS", cast=bool, default=False)
BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS = env(
    "BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS",
    cast=int,
    default=180,  # 3 minutes
)
# We set the default termination grace period to 60 minutes for hosts so that's the max we could allow here
if BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS > 3600:
    logger.warning(
        f"BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS was set to greater than the default termination grace period of 3600 seconds. If you are running on cloud, this may cause the pod to be terminated before the workers finish. If you are running on self-hosted, make sure to set the termination grace period to a value greater than {BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS} seconds",
        BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS=BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS,
    )

MAX_STREAM_CHUNK_SIZE_BYTES = env(
    "MAX_STREAM_CHUNK_SIZE_BYTES",
    cast=int,
    default=1024 * 1024 * 128,
)


# Not in public docs: populated by langgraph.json config, not set as env var directly
CHECKPOINTER_CONFIG: CheckpointerConfig | None = _parse.parse_checkpointer(
    env("LANGGRAPH_CHECKPOINTER", cast=str, default=None),
)
USE_CUSTOM_CHECKPOINTER = bool(
    CHECKPOINTER_CONFIG is not None
    and CHECKPOINTER_CONFIG.get("backend") == "custom"
    and "path" in CHECKPOINTER_CONFIG
    and CHECKPOINTER_CONFIG["path"].strip(),
)

SERDE: SerdeConfig | None = (
    CHECKPOINTER_CONFIG["serde"] if CHECKPOINTER_CONFIG and "serde" in CHECKPOINTER_CONFIG else None
)
USE_PICKLE_FALLBACK = SERDE["pickle_fallback"] if SERDE and "pickle_fallback" in SERDE else True
THREAD_TTL: ThreadTTLConfig | None = env(
    "LANGGRAPH_THREAD_TTL",
    cast=_parse.parse_thread_ttl,
    default=None,
)
if THREAD_TTL is None and CHECKPOINTER_CONFIG is not None:
    THREAD_TTL = CHECKPOINTER_CONFIG.get("ttl")

N_JOBS_PER_WORKER = env("N_JOBS_PER_WORKER", cast=int, default=10)
BG_JOB_TIMEOUT_SECS = env("BG_JOB_TIMEOUT_SECS", cast=float, default=86400)
STREAM_PUBLISH_RETRY_MAX_DURATION_SECS = env(
    "LSD_STREAM_PUBLISH_RETRY_MAX_DURATION_SECS",
    cast=float,
    default=60.0,
)
STREAM_PUBLISH_RETRY_INITIAL_INTERVAL_SECS = env(
    "LSD_STREAM_PUBLISH_RETRY_INITIAL_INTERVAL_SECS",
    cast=float,
    default=0.1,
)
STREAM_PUBLISH_RETRY_MAX_INTERVAL_SECS = env(
    "LSD_STREAM_PUBLISH_RETRY_MAX_INTERVAL_SECS",
    cast=float,
    default=10.0,
)
STREAM_PUBLISH_RETRY_BACKOFF_FACTOR = env(
    "LSD_STREAM_PUBLISH_RETRY_BACKOFF_FACTOR",
    cast=float,
    default=2.0,
)
STREAM_PUBLISH_RETRY_JITTER = env(
    "LSD_STREAM_PUBLISH_RETRY_JITTER",
    cast=float,
    default=0.3,  # 0 means no jitter, 1 means max jitter
)

FF_ASYNC_PUBLISH_QUEUE = env("FF_ASYNC_PUBLISH_QUEUE", cast=bool, default=False)
FF_CRONS_ENABLED = env("FF_CRONS_ENABLED", cast=bool, default=True)
FF_LOG_DROPPED_EVENTS = env("FF_LOG_DROPPED_EVENTS", cast=bool, default=False)
FF_LOG_QUERY_AND_PARAMS = env("FF_LOG_QUERY_AND_PARAMS", cast=bool, default=False)
# Not in public docs: internal feature flag
FF_USE_REDIS_QUEUE = env("FF_USE_REDIS_QUEUE", cast=bool, default=True)


# Internal flag intended for testing only
CRON_SCHEDULER_SLEEP_TIME = env("CRON_SCHEDULER_SLEEP_TIME", cast=int, default=5)


# auth

LANGGRAPH_AUTH_TYPE = env("LANGGRAPH_AUTH_TYPE", cast=str, default="noop")
# Not in public docs: populated by langgraph.json config, not set as env var directly
LANGGRAPH_POSTGRES_EXTENSIONS: Literal["standard", "lite"] = env(
    "LANGGRAPH_POSTGRES_EXTENSIONS",
    cast=str,
    default="standard",
)
if LANGGRAPH_POSTGRES_EXTENSIONS not in ("standard", "lite"):
    raise ValueError(
        f"Unknown LANGGRAPH_POSTGRES_EXTENSIONS value: {LANGGRAPH_POSTGRES_EXTENSIONS}",
    )
# Not in public docs: populated by langgraph.json config, not set as env var directly
LANGGRAPH_AUTH = env(
    "LANGGRAPH_AUTH",
    cast=_parse.parse_schema(AuthConfig),
    default=None,
)
# Deployment guard for multi-user mode: a cloud deployment sets
# REQUIRE_AUTH=true so a misconfigured (auth-less) boot fails loudly instead
# of serving every user's data to everyone. Local zero-login mode leaves both
# unset.
REQUIRE_AUTH = env("REQUIRE_AUTH", cast=bool, default=False)
if REQUIRE_AUTH and not LANGGRAPH_AUTH:
    raise RuntimeError(
        "REQUIRE_AUTH is set but LANGGRAPH_AUTH is not configured — refusing "
        "to start an unauthenticated multi-user deployment. Set LANGGRAPH_AUTH "
        '(e.g. {"path": "/deps/next-role/backend/agents/auth.py:auth", '
        '"disable_studio_auth": true}) or unset REQUIRE_AUTH for single-user use.',
    )

# Not in public docs: populated by langgraph.json config, not set as env var directly
LANGGRAPH_ENCRYPTION = env(
    "LANGGRAPH_ENCRYPTION",
    cast=_parse.parse_schema(EncryptionConfig),
    default=None,
)
# Not in public docs: set by SaaS control plane, not user-configurable
LANGSMITH_TENANT_ID = env("LANGSMITH_TENANT_ID", cast=str, default=None)
LANGSMITH_AUTH_VERIFY_TENANT_ID = env(
    "LANGSMITH_AUTH_VERIFY_TENANT_ID",
    cast=bool,
    default=LANGSMITH_TENANT_ID is not None,
)

if LANGGRAPH_AUTH_TYPE == "langsmith":
    LANGSMITH_AUTH_ENDPOINT = env("LANGSMITH_AUTH_ENDPOINT", cast=str)
    LANGSMITH_TENANT_ID = env("LANGSMITH_TENANT_ID", cast=str)
    LANGSMITH_AUTH_VERIFY_TENANT_ID = env(
        "LANGSMITH_AUTH_VERIFY_TENANT_ID",
        cast=bool,
        default=True,
    )

else:
    LANGSMITH_AUTH_ENDPOINT = env(
        "LANGSMITH_AUTH_ENDPOINT",
        cast=str,
        default=getenv(
            "LANGCHAIN_ENDPOINT",
            getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        ),
    )

# Webhooks


WEBHOOKS_CONFIG = env(
    "LANGGRAPH_WEBHOOKS",
    cast=cast(
        "Callable[[str | None], WebhooksConfig | None]",
        _parse.parse_schema(
            Annotated[WebhooksConfig, AfterValidator(webhooks_validator)],
        ),
    ),
    default=None,
)

# license

LANGGRAPH_CLOUD_LICENSE_KEY = env("LANGGRAPH_CLOUD_LICENSE_KEY", cast=str, default="")

# Products that are built on top of langgraph-api can be configured
# to check for additional claims in the LangSmith license key. By default,
# no additional claims are checked. The `lgp_enabled` claim is always
# checked.
LANGSMITH_LICENSE_REQUIRED_CLAIMS = env(
    "LANGSMITH_LICENSE_REQUIRED_CLAIMS",
    cast=CommaSeparatedStrings,
    default=[],
)

# Not in public docs: LANGCHAIN_API_KEY is a legacy alias (prefer LANGSMITH_API_KEY)
LANGSMITH_API_KEY = env(
    "LANGSMITH_API_KEY",
    cast=str,
    default=getenv("LANGCHAIN_API_KEY", ""),
)
# LANGSMITH_CONTROL_PLANE_API_KEY is used for license verification and
# submitting usage metadata to LangSmith SaaS.
#
# Use case: A self-hosted deployment can configure LANGSMITH_API_KEY
# from a self-hosted LangSmith instance (i.e. trace to self-hosted
# LangSmith) and configure LANGSMITH_CONTROL_PLANE_API_KEY from LangSmith SaaS
# to facilitate license key verification and metadata submission.
LANGSMITH_CONTROL_PLANE_API_KEY = env(
    "LANGSMITH_CONTROL_PLANE_API_KEY",
    cast=str,
    default=LANGSMITH_API_KEY,
)


# if langsmith api key is set, enable tracing unless explicitly disabled
def _first_defined(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


TRACING = _first_defined(
    env("LANGSMITH_TRACING", cast=bool, default=None),
    env("LANGSMITH_TRACING_V2", cast=bool, default=None),
    env("LANGCHAIN_TRACING_V2", cast=bool, default=None),
    env("LANGCHAIN_TRACING", cast=bool, default=None),
)
if LANGSMITH_CONTROL_PLANE_API_KEY and TRACING is None:
    environ["LANGSMITH_TRACING"] = "true"
    TRACING = True

# OpenTelemetry
# Centralized enablement flag so app code does not read raw env vars.
# If OTEL_ENABLED is unset, auto-enable when a standard OTLP endpoint var is present.
OTEL_ENABLED = env("OTEL_ENABLED", cast=bool, default=None)
if OTEL_ENABLED is None:
    OTEL_ENABLED = bool(
        getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )

# if variant is "licensed", update to "local" if using LANGSMITH_CONTROL_PLANE_API_KEY instead

# Not in public docs: LANGSMITH_LANGGRAPH_API_VARIANT is set by SaaS control plane
if getenv("LANGSMITH_LANGGRAPH_API_VARIANT") == "licensed" and LANGSMITH_CONTROL_PLANE_API_KEY:
    environ["LANGSMITH_LANGGRAPH_API_VARIANT"] = "local"


# Metrics.
USES_INDEXING = (
    STORE_CONFIG and STORE_CONFIG.get("index") and STORE_CONFIG.get("index").get("embed")
)
USES_CUSTOM_APP = HTTP_CONFIG and HTTP_CONFIG.get("app")
USES_CUSTOM_AUTH = bool(LANGGRAPH_AUTH)
USES_THREAD_TTL = bool(THREAD_TTL)
USES_STORE_TTL = bool(STORE_CONFIG and STORE_CONFIG.get("ttl"))

API_VARIANT = env("LANGSMITH_LANGGRAPH_API_VARIANT", cast=str, default="")

# UI
UI_USE_BUNDLER = env("LANGGRAPH_UI_BUNDLER", cast=bool, default=False)

LANGGRAPH_METRICS_ENABLED = env("LANGGRAPH_METRICS_ENABLED", cast=bool, default=False)
LANGGRAPH_METRICS_ENDPOINT = env("LANGGRAPH_METRICS_ENDPOINT", cast=str, default=None)
LANGGRAPH_METRICS_EXPORT_INTERVAL_MS = env(
    "LANGGRAPH_METRICS_EXPORT_INTERVAL_MS",
    cast=int,
    default=60000,
)
# Not in public docs: infrastructure, set by platform
LSD_DD_API_KEY = _first_non_empty(
    env("CUSTOM_LSD_DD_API_KEY", cast=str, default=None),
    env("LSD_DD_API_KEY", cast=str, default=None),
)
# Not in public docs: infrastructure, set by platform
LSD_DD_ENDPOINT = _first_non_empty(
    env("CUSTOM_LSD_DD_ENDPOINT", cast=str, default=None),
    env("LSD_DD_ENDPOINT", cast=str, default=None),
    "otlp.us5.datadoghq.com",
)
# Not in public docs: infrastructure, set by platform
METRIC_PREFIX = env("METRIC_PREFIX", cast=str, default="lg_api_")
# Not in public docs: infrastructure, set by platform
_METRIC_MAX_EMITTING_TIER_DEFAULT = (
    1 if os.environ.get("LSD_DEPLOYMENT_TYPE", "") in ("dev", "dev_free") else 2
)
METRIC_MAX_EMITTING_TIER = env(
    "METRIC_MAX_EMITTING_TIER",
    cast=int,
    default=_METRIC_MAX_EMITTING_TIER_DEFAULT,
)
DATADOG_METRICS_ENABLED = bool(LSD_DD_API_KEY)
LANGGRAPH_LOGS_ENDPOINT = env("LANGGRAPH_LOGS_ENDPOINT", cast=str, default=None)
LANGGRAPH_LOGS_ENABLED = env("LANGGRAPH_LOGS_ENABLED", cast=bool, default=False)

FF_PYSPY_PROFILING_ENABLED = env("FF_PYSPY_PROFILING_ENABLED", cast=bool, default=False)
if FF_PYSPY_PROFILING_ENABLED:
    import shutil

    pyspy = shutil.which("py-spy")
    if not pyspy:
        raise ValueError(
            "py-spy not found on PATH. Please re-deploy with py-spy installed.",
        )
FF_PYSPY_PROFILING_MAX_DURATION_SECS = env(
    "FF_PYSPY_PROFILING_MAX_DURATION_SECS",
    cast=int,
    default=240,
)
FF_PROFILE_IMPORTS = env("FF_PROFILE_IMPORTS", cast=bool, default=False)

JS_READY_TIMEOUT_SECS = env("LANGGRAPH_JS_READY_TIMEOUT_SECS", cast=int, default=120)

SELF_HOSTED_OBSERVABILITY_SERVICE_NAME = "LGP_Self_Hosted"

IS_QUEUE_ENTRYPOINT = False
IS_EXECUTOR_ENTRYPOINT = False
PYTHON_GRPC_SERVER_ENABLED = bool(LANGGRAPH_ENCRYPTION or USE_CUSTOM_CHECKPOINTER)


# Not in public docs: LANGSMITH_LANGGRAPH_GIT_REF_SHA is set by SaaS control plane
ref_sha = None
if not os.getenv("LANGCHAIN_REVISION_ID") and (
    ref_sha := os.getenv("LANGSMITH_LANGGRAPH_GIT_REF_SHA")
):
    # This is respected by the langsmith SDK env inference
    # https://github.com/langchain-ai/langsmith-sdk/blob/1b93e4c13b8369d92db891ae3babc3e2254f0e56/python/langsmith/env/_runtime_env.py#L190
    os.environ["LANGCHAIN_REVISION_ID"] = ref_sha

traceblock.patch_requests()

__all__ = [
    "AES_JSON_DISALLOWED_KEYS",
    "ALLOW_PRIVATE_NETWORK",
    "API_VARIANT",
    "BG_JOB_HEARTBEAT",
    "BG_JOB_INTERVAL",
    "BG_JOB_ISOLATED_LOOPS",
    "BG_JOB_MAX_RETRIES",
    "BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS",
    "BG_JOB_TIMEOUT_SECS",
    "CHECKPOINTER_CONFIG",
    "CHECKPOINTER_POSTGRES_POOL_MIN_SIZE",
    "CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS",
    "CHECKPOINT_BATCH_DELAY",
    "CHECKPOINT_MAX_BATCH_SIZE",
    "CORS_ALLOW_ORIGINS",
    "CORS_CONFIG",
    "CRON_SCHEDULER_SLEEP_TIME",
    "DATABASE_URI",
    "DATADOG_METRICS_ENABLED",
    "FF_CRONS_ENABLED",
    "FF_LOG_DROPPED_EVENTS",
    "FF_LOG_QUERY_AND_PARAMS",
    "FF_PYSPY_PROFILING_ENABLED",
    "FF_PYSPY_PROFILING_MAX_DURATION_SECS",
    "FF_USE_REDIS_QUEUE",
    "GRPC_CLIENT_HTTP2_INITIAL_WINDOW_SIZE",
    "GRPC_CLIENT_MAX_RECV_MSG_BYTES",
    "GRPC_CLIENT_MAX_SEND_MSG_BYTES",
    "GRPC_CLIENT_POOL_SIZE",
    "HTTP_CONFIG",
    "HTTP_MAX_REQUEST_BODY_BYTES",
    "IS_EXECUTOR_ENTRYPOINT",
    "IS_QUEUE_ENTRYPOINT",
    "JSON_THREAD_POOL_MINIMUM_SIZE_BYTES",
    "JS_READY_TIMEOUT_SECS",
    "LANGGRAPH_AES_JSON_KEYS",
    "LANGGRAPH_AES_KEY",
    "LANGGRAPH_AUTH",
    "LANGGRAPH_AUTH_TYPE",
    "LANGGRAPH_CLOUD_LICENSE_KEY",
    "LANGGRAPH_LOGS_ENABLED",
    "LANGGRAPH_LOGS_ENDPOINT",
    "LANGGRAPH_METRICS_ENABLED",
    "LANGGRAPH_METRICS_ENDPOINT",
    "LANGGRAPH_METRICS_EXPORT_INTERVAL_MS",
    "LANGGRAPH_POSTGRES_EXTENSIONS",
    "LANGSMITH_API_KEY",
    "LANGSMITH_AUTH_ENDPOINT",
    "LANGSMITH_AUTH_VERIFY_TENANT_ID",
    "LANGSMITH_CONTROL_PLANE_API_KEY",
    "LANGSMITH_TENANT_ID",
    "LSD_DD_API_KEY",
    "LSD_DD_ENDPOINT",
    "LSD_GRPC_SERVER_ADDRESS",
    "LSD_GRPC_SERVER_MAX_RECV_MSG_BYTES",
    "LSD_GRPC_SERVER_MAX_SEND_MSG_BYTES",
    "MAX_STREAM_CHUNK_SIZE_BYTES",
    "METRIC_MAX_EMITTING_TIER",
    "METRIC_PREFIX",
    "MIGRATIONS_PATH",
    "MOUNT_PREFIX",
    "N_JOBS_PER_WORKER",
    "OTEL_ENABLED",
    "POSTGRES_POOL_MAX_SIZE",
    "PYTHON_GRPC_BIND_HOST",
    "PYTHON_GRPC_SERVER_ENABLED",
    "PYTHON_GRPC_SERVER_PORT",
    "REDIS_CLUSTER",
    "REDIS_CONNECT_TIMEOUT",
    "REDIS_HEALTH_CHECK_INTERVAL",
    "REDIS_KEY_PREFIX",
    "REDIS_MAX_CONNECTIONS",
    "REDIS_URI",
    "RESUMABLE_STREAM_TTL_SECONDS",
    "RUN_STATS_CACHE_SECONDS",
    "SELF_HOSTED_OBSERVABILITY_SERVICE_NAME",
    "SERDE",
    "STATS_INTERVAL_SECS",
    "STORE_CONFIG",
    "STREAM_PUBLISH_RETRY_BACKOFF_FACTOR",
    "STREAM_PUBLISH_RETRY_INITIAL_INTERVAL_SECS",
    "STREAM_PUBLISH_RETRY_JITTER",
    "STREAM_PUBLISH_RETRY_MAX_DURATION_SECS",
    "STREAM_PUBLISH_RETRY_MAX_INTERVAL_SECS",
    "THREAD_TTL",
    "TRACING",
    "UI_USE_BUNDLER",
    "USES_CUSTOM_APP",
    "USES_CUSTOM_AUTH",
    "USES_INDEXING",
    "USES_STORE_TTL",
    "USES_THREAD_TTL",
    "USE_CUSTOM_CHECKPOINTER",
    "AuthConfig",
    "CheckpointerConfig",
    "CorsConfig",
    "HttpConfig",
    "SerdeConfig",
    "StoreConfig",
    "TTLConfig",
    "ThreadTTLConfig",
    "WebhooksConfig",
]
