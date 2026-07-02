import copy
import logging
import typing
from functools import lru_cache

import orjson

from langgraph_api import config
from langgraph_api.graph import GRAPHS
from langgraph_api.metadata import USER_API_URL
from langgraph_api.validation import openapi

logger = logging.getLogger(__name__)

CUSTOM_OPENAPI_SPEC = None


def set_custom_spec(spec: dict):
    global CUSTOM_OPENAPI_SPEC
    CUSTOM_OPENAPI_SPEC = spec


@lru_cache(maxsize=1)
def get_openapi_spec() -> bytes:
    # patch the graph_id enums
    graph_ids = list(GRAPHS.keys())
    for schema in (
        "Assistant",
        "AssistantCreate",
        "AssistantPatch",
        "GraphSchema",
        "AssistantSearchRequest",
    ):
        openapi["components"]["schemas"][schema]["properties"]["graph_id"]["enum"] = (
            graph_ids
        )
    # patch the auth schemes
    if config.LANGGRAPH_AUTH_TYPE == "langsmith":
        openapi["security"] = [
            {"x-api-key": []},
        ]
        openapi["components"]["securitySchemes"] = {
            "x-api-key": {"type": "apiKey", "in": "header", "name": "x-api-key"}
        }
    if config.LANGGRAPH_AUTH:
        # Allow user to specify OpenAPI security configuration
        if (
            isinstance(config.LANGGRAPH_AUTH, dict)
            and "openapi" in config.LANGGRAPH_AUTH
        ):
            openapi_config = config.LANGGRAPH_AUTH["openapi"]
            if isinstance(openapi_config, dict):
                # Add security schemes
                if "securitySchemes" in openapi_config:
                    openapi["components"]["securitySchemes"] = openapi_config[
                        "securitySchemes"
                    ]
                elif "security_schemes" in openapi_config:
                    # For our sorry python users
                    openapi["components"]["securitySchemes"] = openapi_config[
                        "security_schemes"
                    ]

                # Add default security if specified
                if "security" in openapi_config:
                    openapi["security"] = openapi_config["security"]

                if "paths" in openapi_config:
                    for path, methods in openapi_config["paths"].items():
                        if path in openapi["paths"]:
                            openapi_path = openapi["paths"][path]
                            for method, security in methods.items():
                                method = method.lower()
                                if method in openapi_path:
                                    openapi_path[method]["security"] = security
        else:
            logger.warning(
                "Custom authentication is enabled but no OpenAPI security configuration was provided. "
                "API documentation will not show authentication requirements. "
                "Add 'openapi' section to auth section of your `langgraph.json` file to specify security schemes."
            )

    # Remove webhook parameters if webhooks are disabled
    if config.WEBHOOKS_ENABLED:
        webhook_schemas = [
            "CronCreate",
            "ThreadCronCreate",
            "RunCreateStateful",
            "RunCreateStateless",
            "RunCreateStreamingStateful",
            "RunCreateStreamingStateless",
        ]
        for schema_name in webhook_schemas:
            if schema_name in openapi["components"]["schemas"]:
                schema = openapi["components"]["schemas"][schema_name]
                if "properties" in schema and "webhook" in schema["properties"]:
                    del schema["properties"]["webhook"]
                    logger.info(
                        f"Removed webhook parameter from {schema_name} schema due to disable_webhooks setting"
                    )

    final = openapi
    if CUSTOM_OPENAPI_SPEC:
        final = merge_openapi_specs(openapi, CUSTOM_OPENAPI_SPEC)
    if USER_API_URL:
        final["servers"] = [{"url": USER_API_URL}]
    elif config.MOUNT_PREFIX:
        final["servers"] = [{"url": config.MOUNT_PREFIX}]

    if not config.MCP_ENABLED:
        # Remove the MCP paths from the OpenAPI spec
        final["paths"].pop("/mcp/", None)
        # Remove the MCP tag definition
        final["tags"] = [t for t in final.get("tags", []) if t.get("name") != "MCP"]

    if not config.A2A_ENABLED:
        # Remove the A2A paths from the OpenAPI spec
        final["paths"].pop("/a2a/{assistant_id}", None)
        final["paths"].pop("/.well-known/agent-card.json", None)
        # Remove the A2A tag definition
        final["tags"] = [t for t in final.get("tags", []) if t.get("name") != "A2A"]

    return orjson.dumps(final)


def merge_openapi_specs(spec_a: dict, spec_b: dict) -> dict:
    """
    Merge two OpenAPI specifications with spec_b taking precedence on conflicts.

    This function handles merging of the following keys:
      - "openapi": Uses spec_b's version.
      - "info": Merges dictionaries with spec_b taking precedence.
      - "servers": Merges lists with deduplication (by URL and description).
      - "paths": For shared paths, merges HTTP methods:
           - If a method exists in both, spec_b's definition wins.
           - Otherwise, methods from both are preserved.
         Additionally, merges path-level "parameters" by (name, in).
      - "components": Merges per component type (schemas, responses, etc.).
      - "security" and "tags": Merges lists with deduplication using a key function.
      - "externalDocs" and any additional keys: spec_b wins.

    Args:
        spec_a (dict): First OpenAPI specification.
        spec_b (dict): Second OpenAPI specification (takes precedence).

    Returns:
        dict: The merged OpenAPI specification.

    Raises:
        TypeError: If either input is not a dict.
        ValueError: If a required field (openapi, info, paths) is missing.
    """
    if not isinstance(spec_a, dict) or not isinstance(spec_b, dict):
        raise TypeError("Both specifications must be dictionaries.")

    required_fields = {"openapi", "info", "paths"}
    for spec in (spec_a, spec_b):
        missing = required_fields - spec.keys()
        if missing:
            raise ValueError(f"Missing required OpenAPI fields: {missing}")

    merged = copy.deepcopy(spec_a)

    if "openapi" in spec_b:
        merged["openapi"] = spec_b["openapi"]

    # Merge "info": Combine dictionaries with spec_b overriding spec_a.
    merged["info"] = {**merged.get("info", {}), **spec_b.get("info", {})}

    # Merge "servers": Use deduplication based on (url, description).
    merged["servers"] = _merge_lists(
        merged.get("servers", []),
        spec_b.get("servers", []),
        key_func=lambda x: (x.get("url"), x.get("description")),
    )

    # Merge "paths": Merge individual paths and methods.
    merged["paths"] = _merge_paths(merged.get("paths", {}), spec_b.get("paths", {}))

    # Merge "components": Merge per component type.
    merged["components"] = _merge_components(
        merged.get("components", {}), spec_b.get("components", {})
    )

    # Merge "security": Merge lists with deduplication.
    merged["security"] = _merge_lists(
        merged.get("security", []),
        spec_b.get("security", []),
        key_func=lambda x: tuple(sorted(x.items())),
    )

    # Merge "tags": Deduplicate tags by "name".
    merged["tags"] = _merge_lists(
        merged.get("tags", []), spec_b.get("tags", []), key_func=lambda x: x.get("name")
    )

    # Merge "externalDocs": Use spec_b if provided.
    if "externalDocs" in spec_b:
        merged["externalDocs"] = spec_b["externalDocs"]

    # Merge any additional keys not explicitly handled.
    handled_keys = {
        "openapi",
        "info",
        "servers",
        "paths",
        "components",
        "security",
        "tags",
        "externalDocs",
    }
    for key in set(spec_a.keys()).union(spec_b.keys()) - handled_keys:
        merged[key] = spec_b.get(key, spec_a.get(key))

    return merged


def _merge_lists(list_a: list, list_b: list, key_func) -> list:
    """
    Merge two lists using a key function for deduplication.
    Items from list_b take precedence over items from list_a.

    Args:
        list_a (list): First list.
        list_b (list): Second list.
        key_func (callable): Function that returns a key used for deduplication.

    Returns:
        list: Merged list.
    """
    merged_dict = {}
    for item in list_a:
        key = _ensure_hashable(key_func(item))
        if key not in merged_dict:
            merged_dict[key] = item
    for item in list_b:
        key = _ensure_hashable(key_func(item))
        merged_dict[key] = item  # spec_b wins
    return list(merged_dict.values())


def _merge_paths(paths_a: dict, paths_b: dict) -> dict:
    """
    Merge OpenAPI paths objects.

    For each path:
      - If the path exists in both specs, merge HTTP methods:
          - If a method exists in both, use spec_b's definition.
          - Otherwise, preserve both.
      - Additionally, merge path-level "parameters" if present.

    Args:
        paths_a (dict): Paths from the first spec.
        paths_b (dict): Paths from the second spec.

    Returns:
        dict: Merged paths.
    """
    merged_paths = {}
    # Start with all paths from paths_a.
    for path, methods in paths_a.items():
        merged_paths[path] = copy.deepcopy(methods)

    # Merge or add paths from paths_b.
    for path, methods_b in paths_b.items():
        if path not in merged_paths:
            merged_paths[path] = copy.deepcopy(methods_b)
        else:
            methods_a = merged_paths[path]
            for method, details_b in methods_b.items():
                key = method.lower()
                # If the method is "parameters", merge them.
                if key == "parameters":
                    params_a = methods_a.get("parameters", [])
                    params_b = details_b if isinstance(details_b, list) else []
                    methods_a["parameters"] = _merge_lists(
                        params_a,
                        params_b,
                        key_func=lambda x: (x.get("name"), x.get("in")),
                    )
                else:
                    # For HTTP methods, spec_b wins if conflict.
                    methods_a[key] = copy.deepcopy(details_b)
            merged_paths[path] = methods_a
    return merged_paths


def _merge_components(components_a: dict, components_b: dict) -> dict:
    """
    Merge OpenAPI components objects.

    For each component type (schemas, responses, parameters, examples, requestBodies,
    headers, securitySchemes, links, callbacks), merge dictionaries with spec_b taking precedence.

    Args:
        components_a (dict): Components from the first spec.
        components_b (dict): Components from the second spec.

    Returns:
        dict: Merged components.
    """
    merged_components = {}
    # Define the common component types to merge.
    component_types = {
        "schemas",
        "responses",
        "parameters",
        "examples",
        "requestBodies",
        "headers",
        "securitySchemes",
        "links",
        "callbacks",
    }

    for comp_type in component_types:
        comp_a = components_a.get(comp_type, {})
        comp_b = components_b.get(comp_type, {})
        merged_components[comp_type] = {**comp_a, **comp_b}

    # Merge any additional keys in components.
    extra_keys = set(components_a.keys()).union(components_b.keys()) - component_types
    for key in extra_keys:
        merged_components[key] = {
            **components_a.get(key, {}),
            **components_b.get(key, {}),
        }

    return merged_components


def _ensure_hashable(obj, depth=0, max_depth=3):
    """
    Recursively convert a Python object into a hashable representation up to a maximum depth.
    If the depth limit is reached, return str(obj).

    - Lists are converted to tuples.
    - Dictionaries are converted to tuples of sorted (key, value) pairs.
    - Other types are returned as-is.

    Args:
        obj: The object to convert.
        depth (int): Current recursion depth.
        max_depth (int): Maximum recursion depth.
    """
    if depth >= max_depth:
        return str(obj)
    if isinstance(obj, typing.Sequence):
        return tuple(_ensure_hashable(e, depth + 1, max_depth) for e in obj)
    if isinstance(obj, typing.Mapping):
        return tuple(
            sorted(
                (k, _ensure_hashable(v, depth + 1, max_depth)) for k, v in obj.items()
            )
        )
    return obj
