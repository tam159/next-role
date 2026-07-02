import os
import re
from typing import Annotated, Literal, NotRequired, Required

from pydantic import Field
from pydantic.functional_validators import AfterValidator
from typing_extensions import TypedDict

__all__ = [
    "VALID_WEBHOOK_ALLOWED_FIELDS",
    "CheckpointerConfig",
    "ConfigurableHeaders",
    "CorsConfig",
    "EncryptionConfig",
    "HttpConfig",
    "IndexConfig",
    "MiddlewareOrders",
    "SecurityConfig",
    "SerdeConfig",
    "StoreConfig",
    "TTLConfig",
    "ThreadTTLConfig",
]

# Valid field names that can appear in webhook payloads.
# This set is used to validate the allowed_fields configuration.
VALID_WEBHOOK_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "thread_id",
        "assistant_id",
        "created_at",
        "updated_at",
        "metadata",
        "kwargs",
        "multitask_strategy",
        "status",
        "run_started_at",
        "run_ended_at",
        "webhook_sent_at",
        "values",
        "error",
    }
)


class CorsConfig(TypedDict, total=False):
    allow_origins: list[str]
    """List of origins allowed to access the API (e.g., ["https://app.com"]).

    Use ["*"] to allow any origin. Combined with `allow_origin_regex` when both are set.
    """
    allow_methods: list[str]
    """HTTP methods permitted for cross-origin requests (e.g., ["GET", "POST"])."""
    allow_headers: list[str]
    """Request headers clients may send in cross-origin requests (e.g., ["authorization"])."""
    allow_credentials: bool
    """Whether browsers may include credentials (cookies, Authorization) in cross-origin requests."""
    allow_origin_regex: str
    """Regular expression that matches allowed origins; evaluated against the request Origin header."""
    expose_headers: list[str]
    """Response headers that browsers are allowed to read from CORS responses."""
    max_age: int
    """Number of seconds browsers may cache the CORS preflight (OPTIONS) response."""


class ConfigurableHeaders(TypedDict, total=False):
    includes: list[str] | None
    """Header name patterns to include.

    Patterns support literal names and the "*" wildcard (e.g., "x-*", "user-agent").
    Matching is performed against lower-cased header names.
    """
    excludes: list[str] | None
    """Header name patterns to exclude after inclusion rules are applied."""
    include: list[str] | None
    """Alias of `includes` for convenience/backwards compatibility."""
    exclude: list[str] | None
    """Alias of `excludes` for convenience/backwards compatibility."""


MiddlewareOrders = Literal["auth_first", "middleware_first"]


class HttpConfig(TypedDict, total=False):
    app: str
    """Import path for a custom Starlette/FastAPI app to mount"""
    disable_assistants: bool
    """Disable /assistants routes"""
    disable_threads: bool
    """Disable /threads routes"""
    disable_runs: bool
    """Disable /runs routes"""
    disable_store: bool
    """Disable /store routes"""
    disable_meta: bool
    """Disable /ok, /info, /metrics, and /docs routes"""
    disable_webhooks: bool
    """Disable webhooks calls on run completion in all routes"""
    cors: CorsConfig | None
    """CORS configuration for all mounted routes; see CorsConfig for details."""
    disable_ui: bool
    """Disable /ui routes"""
    disable_mcp: bool
    """Disable /mcp routes"""
    disable_a2a: bool
    """Disable /a2a routes"""
    mount_prefix: str
    """Prefix for mounted routes. E.g., "/my-deployment/api"."""
    configurable_headers: ConfigurableHeaders | None
    """Controls which inbound request headers are surfaced to runs as `config.configurable`.

    Only headers that match `includes` and do not match `excludes` are exposed, except
    for tracing headers like `langsmith-trace` and select `baggage` keys which are
    always forwarded. Patterns are matched case-insensitively against lower-cased names.
    """
    logging_headers: ConfigurableHeaders | None
    """Controls which inbound request headers may appear in access/application logs.

    Use restrictive patterns (e.g., include "user-agent", exclude "authorization") to
    avoid logging sensitive information.
    """
    enable_custom_route_auth: bool
    """If true, apply the configured authentication middleware to user-supplied routes."""
    middleware_order: MiddlewareOrders | None
    """Ordering for authentication vs custom middleware on user routes.

    - "auth_first": run auth middleware before user middleware
    - "middleware_first": run user middleware before auth (default behavior)
    """


class ThreadTTLConfig(TypedDict, total=False):
    strategy: Literal["delete", "keep_latest"]
    """Action taken when a thread exceeds its TTL.

    - "delete": Remove the thread and all its data entirely.
    - "keep_latest": Prune old checkpoints but keep the thread and its latest state.
    """
    default_ttl: float | None
    """Default thread TTL in minutes; threads past this age are subject to the `strategy`."""
    sweep_interval_minutes: int | None
    """How often to scan for expired threads, in minutes."""
    sweep_limit: int | None
    """Maximum number of threads to process per sweep iteration. Defaults to 1000."""


class IndexConfig(TypedDict, total=False):
    """Configuration for indexing documents for semantic search in the store."""

    dims: int
    """Number of dimensions in the embedding vectors.
    
    Common embedding models have the following dimensions:
        - OpenAI text-embedding-3-large: 256, 1024, or 3072
        - OpenAI text-embedding-3-small: 512 or 1536
        - OpenAI text-embedding-ada-002: 1536
        - Cohere embed-english-v3.0: 1024
        - Cohere embed-english-light-v3.0: 384
        - Cohere embed-multilingual-v3.0: 1024
        - Cohere embed-multilingual-light-v3.0: 384
    """

    embed: str
    """Either a path to an embedding model (./path/to/file.py:embedding_model)
    or a name of an embedding model (openai:text-embedding-3-small)
    
    Note: LangChain is required to use the model format specification.
    """

    fields: list[str] | None
    """Fields to extract text from for embedding generation.
    
    Defaults to the root ["$"], which embeds the json object as a whole.
    """


class TTLConfig(TypedDict, total=False):
    """Configuration for TTL (time-to-live) behavior in the store."""

    refresh_on_read: bool
    """Default behavior for refreshing TTLs on read operations (GET and SEARCH).
    
    If True, TTLs will be refreshed on read operations (get/search) by default.
    This can be overridden per-operation by explicitly setting refresh_ttl.
    Defaults to True if not configured.
    """
    default_ttl: float | None
    """Default TTL (time-to-live) in minutes for new items.
    
    If provided, new items will expire after this many minutes after their last access.
    The expiration timer refreshes on both read and write operations.
    Defaults to None (no expiration).
    """
    sweep_interval_minutes: int | None
    """Interval in minutes between TTL sweep operations.
    
    If provided, the store will periodically delete expired items based on TTL.
    Defaults to None (no sweeping).
    """


class StoreConfig(TypedDict, total=False):
    path: str
    """Import path to a custom `BaseStore` or a callable returning one.

    Examples:
    - "./my_store.py:create_store"
    - "my_package.store:Store"

    When provided, this replaces the default Postgres-backed store.
    """
    index: IndexConfig
    """Vector index settings for the built-in store (ignored by custom stores)."""
    ttl: TTLConfig
    """TTL behavior for stored items in the built-in store (custom stores may not support TTL)."""


class SerdeConfig(TypedDict, total=False):
    """Configuration for the built-in serde, which handles checkpointing of state.

    If omitted, no serde is set up (the object store will still be present, however)."""

    allowed_json_modules: list[list[str]] | Literal[True] | None
    """Optional. List of allowed python modules to de-serialize custom objects from.
    
    If provided, only the specified modules will be allowed to be deserialized.
    If omitted, no modules are allowed, and the object returned will simply be a json object OR
    a deserialized langchain object.
    
    Example:
    {...
        "serde": {
            "allowed_json_modules": [
                ["my_agent", "my_file", "SomeType"],
            ]
        }
    }

    If you set this to True, any module will be allowed to be deserialized.

    Example:
    {...
        "serde": {
            "allowed_json_modules": true
        }
    }
    
    """
    pickle_fallback: bool
    """Optional. Whether to allow pickling as a fallback for deserialization.
    
    If True, pickling will be allowed as a fallback for deserialization.
    If False, pickling will not be allowed as a fallback for deserialization.
    Defaults to True if not configured."""


class DefaultCheckpointerConfig(TypedDict):
    """Configuration for the default built-in checkpointer backend."""

    backend: Literal["default"]
    ttl: NotRequired[ThreadTTLConfig | None]
    """Optional. Defines the TTL (time-to-live) behavior configuration.
    
    If provided, the checkpointer will apply TTL settings according to the configuration.
    If omitted, no TTL behavior is configured.
    """
    serde: NotRequired[SerdeConfig | None]
    """Optional. Defines the configuration for how checkpoints are serialized."""


class CustomCheckpointerConfig(TypedDict):
    """Configuration for a custom checkpointer loaded from Python import path."""

    backend: Literal["custom"]
    path: Required[str]
    """Import path to a custom `BaseCheckpointSaver` or a callable returning one.

    Examples:
    - "./my_checkpointer.py:create_checkpointer"
    - "my_package.checkpointer:Checkpointer"

    When provided, this replaces the default checkpointer.
    """
    ttl: NotRequired[ThreadTTLConfig | None]
    """Optional. Defines the TTL (time-to-live) behavior configuration.
    
    If provided, the checkpointer will apply TTL settings according to the configuration.
    If omitted, no TTL behavior is configured.
    """
    serde: NotRequired[SerdeConfig | None]
    """Optional. Defines the configuration for how checkpoints are serialized."""


class MongoCheckpointerConfig(TypedDict):
    """Configuration for the MongoDB checkpointer backend."""

    backend: Literal["mongo"]
    uri: NotRequired[str]
    """MongoDB connection URI.

    Prefer setting ``LS_MONGODB_URI`` or ``MONGODB_URI`` so the connection
    string does not need to be checked into source control. If omitted here,
    the runtime resolves ``LS_MONGODB_URI`` first, then ``MONGODB_URI``.
    """
    ttl: NotRequired[ThreadTTLConfig | None]


CheckpointerConfig = Annotated[
    MongoCheckpointerConfig | CustomCheckpointerConfig | DefaultCheckpointerConfig,
    Field(discriminator="backend"),
]


class SecurityConfig(TypedDict, total=False):
    securitySchemes: dict
    """OpenAPI `components.securitySchemes` definition to merge into the spec."""
    security: list
    """Default security requirements applied to all operations (OpenAPI `security`)."""
    # path => {method => security}
    paths: dict[str, dict[str, list]]
    """Per-route overrides of security requirements.

    Mapping of path -> method -> OpenAPI `security` array.
    """


class CacheConfig(TypedDict, total=False):
    cache_keys: list[str]
    """Header names used to build the cache key for auth decisions."""
    ttl_seconds: int
    """How long to cache successful auth decisions, in seconds."""
    max_size: int
    """Maximum number of distinct auth cache entries to retain."""


class AuthConfig(TypedDict, total=False):
    path: str
    """Path to the authentication function in a Python file."""
    disable_studio_auth: bool
    """Whether to disable auth when connecting from the LangSmith Studio."""
    openapi: SecurityConfig
    """The schema to use for updating the openapi spec.

    Example:
        {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "password": {
                            "tokenUrl": "/token",
                            "scopes": {
                                "me": "Read information about the current user",
                                "items": "Access to create and manage items"
                            }
                        }
                    }
                }
            },
            "security": [
                {"OAuth2": ["me"]}  # Default security requirement for all endpoints
            ]
        }
    """
    cache: CacheConfig | None
    """Optional cache settings for the custom auth backend to reduce repeated lookups."""


class WebhookUrlPolicy(TypedDict, total=False):
    require_https: bool
    """Enforce HTTPS scheme for absolute URLs; reject `http://` when true."""
    allowed_domains: list[str]
    """Hostname allowlist. Supports exact hosts and wildcard subdomains.

    Use entries like "hooks.example.com" or "*.mycorp.com". The wildcard only
    matches subdomains ("foo.mycorp.com"), not the apex ("mycorp.com"). When
    empty or omitted, any public host is allowed (subject to SSRF IP checks).
    """
    allowed_ports: list[int]
    """Explicit port allowlist for absolute URLs.

    If set, requests must use one of these ports. Defaults are respected when
    a port is not present in the URL (443 for https, 80 for http).
    """
    max_url_length: int
    """Maximum permitted URL length in characters; longer inputs are rejected early."""
    disable_loopback: bool
    """Disallow loopback-flavored webhook targets when true. Defaults to true.

    Covers all of:
    - Relative URLs (e.g. ``/internal/hook``) that would dispatch via the
      in-process ASGI loopback transport at ``root_path="/noauth"``. These
      bypass authentication and were the auth-bypass primitive behind
      GHSA-q3v5-r5ch-p57j.
    - Absolute URLs whose hostname is ``localhost`` / ``localhost.localdomain``
      / ``host.docker.internal``.
    - Absolute URLs that DNS-resolve into the IPv4 loopback range
      (``127.0.0.0/8``) or IPv6 loopback (``::1/128``) — including
      DNS-rebinding-style attacks where an arbitrary hostname resolves to
      a loopback IP.

    Operators who intentionally route webhooks to in-process routes
    (e.g. for local development with ``langgraph dev``, or single-host
    docker-compose setups dispatching to ``host.docker.internal``) can
    set this to ``false`` to restore the pre-GHSA behavior. Only do this
    when you control all routes that loopback webhooks can reach.
    """
    disable_private_ips: bool
    """Block RFC 1918 / CGN private IP ranges as webhook targets when true.

    Defaults to false (private IPs allowed). Set to true to block private
    IP ranges for stricter SSRF protection.
    """


# Matches things like "${{ env.LG_WEBHOOK_FOO_BAR }}"
_WEBHOOK_TEMPLATE_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def _validate_url_policy(
    policy: "WebhookUrlPolicy | None",
) -> "WebhookUrlPolicy | None":
    if not policy:
        return policy
    if "allowed_domains" in policy:
        doms = policy["allowed_domains"]
        if not isinstance(doms, list) or not all(
            isinstance(d, str) and d for d in doms
        ):
            raise ValueError(
                f"webhooks.url.allowed_domains must be a list of non-empty strings. Got: {doms}"
            )
        for d in doms:
            if "*" in d and not d.startswith("*."):
                raise ValueError(
                    f"webhooks.url.allowed_domains wildcard can only be used as a prefix, in the form '*.domain'. Got: {doms}"
                )
    if "require_https" not in policy:
        policy["require_https"] = True
    if "allowed_domains" not in policy:
        policy["allowed_domains"] = []
    if "allowed_ports" not in policy:
        policy["allowed_ports"] = []
    if "max_url_length" not in policy:
        policy["max_url_length"] = 2048
    if "disable_loopback" not in policy:
        # Secure default — covers GHSA-q3v5-r5ch-p57j (relative-URL ASGI
        # loopback auth bypass) and the broader SSRF surface that the same
        # flag governs via SSRFPolicy.block_localhost. Mirrored at the
        # webhook.py read site so deployments without any webhooks block
        # also get the safe default.
        policy["disable_loopback"] = True
    if "disable_private_ips" not in policy:
        policy["disable_private_ips"] = False
    return policy


class WebhooksConfig(TypedDict, total=False):
    env_prefix: str
    """Required prefix for environment variables referenced in header templates.

    Acts as an allowlist boundary to prevent leaking arbitrary environment
    variables. Defaults to "LG_WEBHOOK_" when omitted.
    """
    url: Annotated[WebhookUrlPolicy, AfterValidator(_validate_url_policy)]
    """URL validation policy for user-supplied webhook endpoints."""
    headers: dict[str, str]
    """Static headers to include with webhook requests.

    Values may contain templates of the form "${{ env.VAR }}". On startup, these
    are resolved via the process environment after verifying `VAR` starts with
    `env_prefix`. Mixed literals and multiple templates are allowed.
    """
    allowed_fields: set[str] | None
    """Allow-list of fields to include in webhook payloads.

    When configured, only the specified fields are sent in the webhook payload.
    When omitted or None, all fields are sent (backward compatible).

    Valid field names: run_id, thread_id, assistant_id, created_at, updated_at,
    metadata, kwargs, multitask_strategy, status, run_started_at, run_ended_at,
    webhook_sent_at, values, error.
    """


def webhooks_validator(cfg: "WebhooksConfig") -> "WebhooksConfig":
    # Validate allowed_fields if present
    allowed_fields = cfg.get("allowed_fields")
    if allowed_fields is not None:
        if not isinstance(allowed_fields, list):
            raise ValueError(
                f"webhooks.allowed_fields must be a list of strings. Got: {type(allowed_fields).__name__}"
            )
        for field in allowed_fields:
            if not isinstance(field, str):
                raise ValueError(
                    f"webhooks.allowed_fields must contain only strings. Got: {type(field).__name__}"
                )
            if field not in VALID_WEBHOOK_ALLOWED_FIELDS:
                raise ValueError(
                    f"webhooks.allowed_fields contains invalid field '{field}'. "
                    f"Valid fields are: {sorted(VALID_WEBHOOK_ALLOWED_FIELDS)}"
                )

    # Enforce env prefix & actual env presence at the aggregate level
    headers = cfg.get("headers") or {}
    if headers:
        env_prefix = cfg.get("env_prefix", "LG_WEBHOOK_")
        if env_prefix is None:
            raise ValueError("webhook headers: Invalid null env_prefix")

        def _replace(m: re.Match[str]) -> str:
            expr = m.group(1).strip()
            # Only variables we support are of the form "env.FOO_BAR" right now.
            if not expr.startswith("env."):
                raise ValueError(
                    f"webhook headers: only env.VAR references are allowed (e.g. {{{{ env.{env_prefix}FOO_BAR }}}})"
                )
            var = expr[len("env.") :]
            if not var or "." in var:
                raise ValueError(
                    f"webhook headers: invalid env reference '{var}'. Use env.VAR with no dots (e.g. {{{{ env.{env_prefix}FOO_BAR }}}})"
                )
            if env_prefix and not var.startswith(env_prefix):
                raise ValueError(
                    f"webhook headers: environment variable name '{var}' must start with the configured env_prefix '{env_prefix}'"
                )
            val = os.getenv(var)
            if val is None:
                raise ValueError(
                    f"webhook headers: missing required environment variable '{var}'"
                )
            return val

        rendered_headers = {}
        for k, v in headers.items():
            if not isinstance(v, str):
                raise ValueError(f"Webhook header values must be strings. Got: {v}")
            rendered = _WEBHOOK_TEMPLATE_RE.sub(_replace, v)
            rendered_headers[k] = rendered
        cfg["headers"] = rendered_headers
    return cfg


class EncryptionConfig(TypedDict, total=False):
    path: str
    """Path to the encryption module in a Python file.

    Example: "./encryption.py:my_encryption"

    The module should export an Encrypt instance with registered
    encryption and decryption handlers for blobs and metadata.
    """
