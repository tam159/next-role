import copy
import pathlib
import re
import typing
from functools import lru_cache

import jsonschema_rs
import orjson
import structlog

logger = structlog.getLogger(__name__)

with open(pathlib.Path(__file__).parent.parent / "openapi.json") as f:
    openapi_str = f.read()

openapi = orjson.loads(openapi_str)


NO_NULL_OR_ESCAPED_PATTERN = r"^(?!.*\\[uU]0000)[^\u0000]*$"
RESERVED_CONFIGURABLE_KEYS = (
    "langgraph_auth_user",
    "langgraph_auth_user_id",
    "langgraph_auth_permissions",
    "langgraph_request_id",
    "__langsmith_project__",
    "__langsmith_example_id__",
    "__request_start_time_ms__",
    "__after_seconds__",
    "__otel_traceparent__",
    "__otel_tracestate__",
    "__dd_trace_headers__",
    "__pregel_node_finished",
)
RESERVED_METADATA_KEYS = (
    "thread_id",
    "assistant_id",
    "run_id",
    "cron_id",
)
RESERVED_OR_NULL_OR_ESCAPED_PATTERN = rf"^(?!.*\\[uU]0000)(?!({'|'.join(f'{re.escape(k)}$' for k in RESERVED_CONFIGURABLE_KEYS)}))[^\u0000]*$"
WRITE_SCHEMAS_WITH_CONFIG_OR_CONTEXT = (
    "AssistantCreate",
    "AssistantPatch",
    "CronCreate",
    "CronPatch",
    "RunCreateStateful",
    "RunCreateStateless",
    "RunCreateStreamingStateful",
    "RunCreateStreamingStateless",
    "ThreadCronCreate",
)
WRITE_SCHEMAS_WITH_METADATA = (
    "AssistantCreate",
    "AssistantPatch",
    "CronCreate",
    "CronPatch",
    "RunBatchCreate",
    "RunCreateStateful",
    "RunCreateStateless",
    "RunCreateStreamingStateful",
    "RunCreateStreamingStateless",
    "ThreadCreate",
    "ThreadCronCreate",
    "ThreadPatch",
)


def _set_property_names_pattern(schema: dict, pattern: str) -> None:
    schema["propertyNames"] = {"pattern": pattern}


def _get_object_property(schema: dict, prop_name: str) -> dict | None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    prop = properties.get(prop_name)
    if isinstance(prop, dict) and prop.get("type") == "object":
        return prop
    return None


def _apply_validator_only_security_guards(spec: dict) -> None:
    schemas = spec.get("components", {}).get("schemas")
    if not isinstance(schemas, dict):
        return

    config_schema = schemas.get("Config")
    if isinstance(config_schema, dict):
        _set_property_names_pattern(config_schema, NO_NULL_OR_ESCAPED_PATTERN)
        if configurable := _get_object_property(config_schema, "configurable"):
            _set_property_names_pattern(configurable, NO_NULL_OR_ESCAPED_PATTERN)

    for schema_name in WRITE_SCHEMAS_WITH_CONFIG_OR_CONTEXT:
        schema = schemas.get(schema_name)
        if not isinstance(schema, dict):
            continue

        config = _get_object_property(schema, "config")
        if config:
            _set_property_names_pattern(config, NO_NULL_OR_ESCAPED_PATTERN)
            if configurable := _get_object_property(config, "configurable"):
                _set_property_names_pattern(configurable, NO_NULL_OR_ESCAPED_PATTERN)

        if context := _get_object_property(schema, "context"):
            _set_property_names_pattern(context, NO_NULL_OR_ESCAPED_PATTERN)


_RESERVED_KEYS_SET = frozenset(RESERVED_CONFIGURABLE_KEYS)
_RESERVED_METADATA_KEYS_SET = frozenset(RESERVED_METADATA_KEYS)


def _strip_reserved(
    d: dict,
    location: str,
    reserved_keys: frozenset[str] = _RESERVED_KEYS_SET,
    message: str = "Stripped reserved keys from request",
) -> None:
    """Remove reserved keys from *d* in-place and log a warning."""
    found = [k for k in d if k in reserved_keys]
    if found:
        for k in found:
            del d[k]
        logger.warning(
            message,
            location=location,
            keys=found,
        )


def _strip_metadata(d: dict, location: str) -> None:
    """Strip reserved resource IDs from a ``metadata`` sub-dict if present."""
    metadata = d.get("metadata")
    if isinstance(metadata, dict):
        _strip_reserved(
            metadata,
            location,
            reserved_keys=_RESERVED_METADATA_KEYS_SET,
            message="Stripped reserved keys from metadata",
        )


def _strip_reserved_metadata_keys(data: typing.Any) -> None:
    """Strip reserved metadata keys from known request envelope locations.

    Only touches ``metadata`` at the top level and inside ``config`` —
    never recurses into user-controlled fields like ``input`` or ``command``.
    """
    if isinstance(data, list):
        for item in data:
            _strip_reserved_metadata_keys(item)
        return
    if not isinstance(data, dict):
        return

    _strip_metadata(data, "metadata")

    config = data.get("config")
    if isinstance(config, dict):
        _strip_metadata(config, "config.metadata")


def sanitize_reserved_keys(data: typing.Any, *, strip_metadata: bool = True) -> None:
    """Strip server-reserved configurable/context keys from parsed request data.

    Instead of rejecting the request with a 422, silently remove the keys and
    log a warning so callers are not broken by accidental inclusion of
    internal keys like ``__after_seconds__``.
    """
    if isinstance(data, list):
        for item in data:
            sanitize_reserved_keys(item, strip_metadata=strip_metadata)
        return
    if not isinstance(data, dict):
        return

    if strip_metadata:
        _strip_reserved_metadata_keys(data)

    # write schemas: config.configurable
    config = data.get("config")
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            _strip_reserved(configurable, "config.configurable")

    # Config schema: top-level configurable
    configurable = data.get("configurable")
    if isinstance(configurable, dict):
        _strip_reserved(configurable, "configurable")

    # write schemas: context
    context = data.get("context")
    if isinstance(context, dict):
        _strip_reserved(context, "context")


def should_strip_reserved_metadata(schema: typing.Any) -> bool:
    """Return whether reserved metadata keys should be stripped for this schema."""
    return schema in _WRITE_SCHEMAS_WITH_METADATA_SET


_validation_openapi = copy.deepcopy(openapi)
_apply_validator_only_security_guards(_validation_openapi)

ConfigValidator = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["Config"]
)
AssistantVersionsSearchRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantVersionsSearchRequest"]
)
AssistantSearchRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantSearchRequest"]
)
AssistantCountRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantCountRequest"]
)
ThreadSearchRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["ThreadSearchRequest"]
)
ThreadCountRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["ThreadCountRequest"]
)
AssistantCreate = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantCreate"]
)
AssistantPatch = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantPatch"]
)
AssistantVersionChange = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["AssistantVersionChange"]
)
ThreadCreate = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["ThreadCreate"],
        "components": {
            "schemas": {
                "ThreadSuperstepUpdate": _validation_openapi["components"]["schemas"][
                    "ThreadSuperstepUpdate"
                ],
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
ThreadPatch = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["ThreadPatch"]
)
ThreadPruneRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["ThreadPruneRequest"]
)
ThreadStateUpdate = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["ThreadStateUpdate"],
        "components": {
            "schemas": {
                "CheckpointConfig": _validation_openapi["components"]["schemas"][
                    "CheckpointConfig"
                ]
            }
        },
    }
)

ThreadStateCheckpointRequest = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["ThreadStateCheckpointRequest"],
        "components": {
            "schemas": {
                "CheckpointConfig": _validation_openapi["components"]["schemas"][
                    "CheckpointConfig"
                ]
            }
        },
    }
)
ThreadStateSearch = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["ThreadStateSearch"],
        "components": {
            "schemas": {
                "CheckpointConfig": _validation_openapi["components"]["schemas"][
                    "CheckpointConfig"
                ]
            }
        },
    }
)
RunCreateStateless = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["RunCreateStateless"],
        "components": {
            "schemas": {
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
RunBatchCreate = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["RunBatchCreate"],
        "components": {
            "schemas": {
                "RunCreateStateless": _validation_openapi["components"]["schemas"][
                    "RunCreateStateless"
                ],
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
RunCreateStateful = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["RunCreateStateful"],
        "components": {
            "schemas": {
                "CheckpointConfig": _validation_openapi["components"]["schemas"][
                    "CheckpointConfig"
                ],
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
RunCreateStreamingStateless = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["RunCreateStreamingStateless"],
        "components": {
            "schemas": {
                "RunCreateStateless": _validation_openapi["components"]["schemas"][
                    "RunCreateStateless"
                ],
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
RunCreateStreamingStateful = jsonschema_rs.validator_for(
    {
        **_validation_openapi["components"]["schemas"]["RunCreateStreamingStateful"],
        "components": {
            "schemas": {
                "RunCreateStateful": _validation_openapi["components"]["schemas"][
                    "RunCreateStateful"
                ],
                "CheckpointConfig": _validation_openapi["components"]["schemas"][
                    "CheckpointConfig"
                ],
                "Command": _validation_openapi["components"]["schemas"]["Command"],
                "Send": _validation_openapi["components"]["schemas"]["Send"],
            }
        },
    }
)
RunsCancel = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["RunsCancel"]
)
CronCreate = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["CronCreate"]
)
ThreadCronCreate = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["ThreadCronCreate"]
)
CronPatch = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["CronPatch"]
)
CronSearch = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["CronSearch"]
)
CronCountRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["CronCountRequest"]
)

_WRITE_SCHEMAS_WITH_METADATA_SET = frozenset(
    globals()[schema_name] for schema_name in WRITE_SCHEMAS_WITH_METADATA
)


# Stuff around storage/BaseStore API
StorePutRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["StorePutRequest"]
)
StoreSearchRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["StoreSearchRequest"]
)
StoreDeleteRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["StoreDeleteRequest"]
)
StoreListNamespacesRequest = jsonschema_rs.validator_for(
    _validation_openapi["components"]["schemas"]["StoreListNamespacesRequest"]
)


DOCS_HTML = """<!doctype html>
<html>
  <head>
    <title>Agent Server API Reference</title>
    <meta charset="utf-8" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference"></script>
    <script>
      var configuration = __SCALAR_CONFIGURATION_JSON__
      document.getElementById('api-reference').dataset.configuration =
        JSON.stringify(configuration)
    </script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""


@lru_cache(maxsize=1)
def render_docs_html(openapi_spec: bytes) -> str:
    # Inline the OpenAPI JSON to avoid an extra fetch and support environments
    # where the docs page cannot reach the mounted /openapi.json URL directly.
    configuration = {"content": openapi_spec.decode("utf-8")}

    # If the OpenAPI spec contains a string like </script> (or even just < that starts an HTML-like sequence),
    # the browser's HTML parser can treat it as markup and prematurely end the script tag. That can break the
    # page and can create an injection/XSS risk. Replacing < with \u003c keeps the JSON semantically identical
    # for JavaScript, but prevents the HTML parser from seeing real tag delimiters.
    configuration_json = (
        orjson.dumps(configuration).decode("utf-8").replace("<", "\\u003c")
    )
    return DOCS_HTML.replace("__SCALAR_CONFIGURATION_JSON__", configuration_json)
