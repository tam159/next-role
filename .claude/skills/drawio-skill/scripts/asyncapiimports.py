#!/usr/bin/env python3
"""Turn an AsyncAPI 2/3 spec into an event-driven architecture graph.

Emits autolayout graph JSON with channel, publish/subscribe operation, and
message-payload schema nodes. JSON is supported with the standard library;
YAML additionally requires PyYAML.

Usage: python3 asyncapiimports.py <spec.json|spec.yaml> [-o graph.json]
       [--direction TB|LR] [--group]
"""
import argparse
import json
import os
import sys


CHANNEL_STYLE = (
    "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;"
    "fillColor=#fff2cc;strokeColor=#d6b656;"
)
PUBLISH_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;"
SUBSCRIBE_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
SCHEMA_STYLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;"
EVENT_EDGE = "edgeStyle=orthogonalEdgeStyle;html=1;rounded=0;fontSize=10;endArrow=open;"
SCHEMA_EDGE = (
    "edgeStyle=orthogonalEdgeStyle;html=1;rounded=0;fontSize=10;"
    "dashed=1;endArrow=open;strokeColor=#9673a6;"
)


def load_spec(path):
    """Parse JSON directly and YAML through the optional PyYAML dependency."""
    # pi-lens-ignore: ast-grep:unchecked-throwing-call-python
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            sys.exit("error: spec is YAML but PyYAML is not installed (pip install pyyaml)")
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError:
            sys.exit("error: could not parse spec as JSON (install PyYAML to read YAML)")
        return yaml.safe_load(text)


def resolve_ref(spec, ref):
    """Resolve an internal JSON Pointer, returning None for external refs."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    value = spec
    try:
        for part in ref[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[key]
    except (KeyError, TypeError):
        return None
    return value


def pointer_token(value):
    """Escape a mapping key for use as a JSON Pointer token."""
    return str(value).replace("~", "~0").replace("/", "~1")


def decode_pointer_token(value):
    return str(value).replace("~1", "/").replace("~0", "~")


def schema_refs(obj, spec, seen=None):
    """Yield component-schema names reachable through messages and payloads."""
    seen = set() if seen is None else seen
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.split("/")[-1].replace("~1", "/").replace("~0", "~")
            yield name
        if isinstance(ref, str) and ref not in seen:
            resolved = resolve_ref(spec, ref)
            if resolved is not None:
                seen.add(ref)
                yield from schema_refs(resolved, spec, seen)
        for key, value in obj.items():
            if key != "$ref":
                yield from schema_refs(value, spec, seen)
    elif isinstance(obj, list):
        for value in obj:
            yield from schema_refs(value, spec, seen)


def first_tag(obj):
    tags = obj.get("tags") if isinstance(obj, dict) else None
    if not isinstance(tags, list) or not tags:
        return None
    tag = tags[0]
    return tag.get("name") if isinstance(tag, dict) else str(tag)


def channel_group(name, channel):
    """Prefer a channel tag, falling back to the address/name prefix."""
    tag = first_tag(channel)
    if tag:
        return tag
    address = str(channel.get("address") or name).strip("/")
    return address.split("/", 1)[0] or "root"


def build(spec, group=False, direction="LR"):
    """Convert an AsyncAPI 2 or 3 mapping to autolayout graph JSON."""
    channels = spec.get("channels") or {}
    schemas = (spec.get("components") or {}).get("schemas") or {}
    nodes, edges, edge_keys = [], [], set()
    channel_ids = {name: f"channel:{name}" for name in channels}
    schema_ids = {name: f"schema:{name}" for name in schemas}

    def add_edge(source, target, label="", style=EVENT_EDGE, pointer=None):
        key = (source, target, label)
        if source == target or key in edge_keys:
            return
        edge_keys.add(key)
        edge = {"source": source, "target": target, "label": label, "style": style}
        if pointer:
            edge["provenance"] = {"pointer": pointer}
        edges.append(edge)

    for name, raw_channel in channels.items():
        channel = raw_channel if isinstance(raw_channel, dict) else {}
        address = str(channel.get("address") or name)
        node = {
            "id": channel_ids[name],
            "label": address,
            "style": CHANNEL_STYLE,
            "width": max(150, 8 * len(address) + 24),
            "height": 50,
            "provenance": {"pointer": f"#/channels/{pointer_token(name)}"},
        }
        if group:
            node["group"] = channel_group(name, channel)
        nodes.append(node)

    operations = []
    # AsyncAPI 2 keeps publish/subscribe operations under each channel.
    for channel_name, raw_channel in channels.items():
        channel = raw_channel if isinstance(raw_channel, dict) else {}
        for action in ("publish", "subscribe"):
            operation = channel.get(action)
            if isinstance(operation, dict):
                operations.append(
                    (
                        f"{channel_name}:{action}",
                        action,
                        channel_name,
                        operation,
                        f"#/channels/{pointer_token(channel_name)}/{action}",
                    )
                )

    # AsyncAPI 3 promotes operations to the top level and calls the actions
    # send/receive. A channel is referenced by JSON Pointer.
    for operation_name, raw_operation in (spec.get("operations") or {}).items():
        operation = raw_operation if isinstance(raw_operation, dict) else {}
        action = str(operation.get("action") or "")
        action = {"send": "publish", "receive": "subscribe"}.get(action, action)
        channel_ref = (operation.get("channel") or {}).get("$ref")
        channel_name = (
            decode_pointer_token(channel_ref.split("/")[-1])
            if isinstance(channel_ref, str)
            else None
        )
        if action in ("publish", "subscribe") and channel_name in channels:
            operations.append(
                (
                    operation_name,
                    action,
                    channel_name,
                    operation,
                    f"#/operations/{pointer_token(operation_name)}",
                )
            )

    for operation_name, action, channel_name, operation, pointer in operations:
        operation_id = f"operation:{operation_name}"
        raw_channel = channels[channel_name]
        channel = raw_channel if isinstance(raw_channel, dict) else {}
        # Without a summary or operationId (common in AsyncAPI 2), the channel
        # address reads better than the synthetic "channel:action" name.
        title = (
            operation.get("summary")
            or operation.get("operationId")
            or str(channel.get("address") or channel_name)
        )
        node = {
            "id": operation_id,
            "label": f"{action.upper()}\n{title}",
            "style": PUBLISH_STYLE if action == "publish" else SUBSCRIBE_STYLE,
            "width": max(150, 8 * len(str(title)) + 24),
            "height": 50,
            "provenance": {"pointer": pointer},
        }
        if group:
            node["group"] = first_tag(operation) or channel_group(channel_name, channel)
        nodes.append(node)
        add_edge(operation_id, channel_ids[channel_name], action, EVENT_EDGE, pointer)
        # In AsyncAPI 3, the channel reference identifies the connection but
        # does not mean that an operation uses every message on that channel.
        message_source = {
            key: value for key, value in operation.items() if key != "channel"
        }
        for schema_name in sorted(set(schema_refs(message_source, spec))):
            if schema_name in schema_ids:
                add_edge(
                    operation_id,
                    schema_ids[schema_name],
                    "payload",
                    SCHEMA_EDGE,
                    pointer,
                )

    for name, raw_schema in schemas.items():
        schema = raw_schema if isinstance(raw_schema, dict) else {}
        properties = schema.get("properties") or {}
        count = len(properties)
        label = name + (f"\n({count} field{'s' if count != 1 else ''})" if count else "")
        node = {
            "id": schema_ids[name],
            "label": label,
            "style": SCHEMA_STYLE,
            "width": max(140, 9 * len(name) + 20),
            "height": 40,
            "provenance": {"pointer": f"#/components/schemas/{pointer_token(name)}"},
        }
        if group:
            node["group"] = "schemas"
        nodes.append(node)
        for ref_name in sorted(set(schema_refs(schema, spec))):
            if ref_name in schema_ids:
                add_edge(schema_ids[name], schema_ids[ref_name], "", SCHEMA_EDGE)

    return {"direction": direction, "nodes": nodes, "edges": edges}


def main():
    parser = argparse.ArgumentParser(
        description="AsyncAPI 2/3 spec -> event architecture graph JSON."
    )
    parser.add_argument("spec", help="AsyncAPI 2/3 spec (.json, .yaml, or .yml)")
    parser.add_argument("-o", "--output", help="output JSON path (default: stdout)")
    parser.add_argument("--direction", default="LR", choices=["TB", "LR"])
    parser.add_argument(
        "--group",
        action="store_true",
        help="group by operation tag or channel prefix",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.spec):
        sys.exit(f"error: {args.spec} not found")
    spec = load_spec(args.spec) or {}
    if not spec.get("asyncapi"):
        sys.exit("error: missing asyncapi version (is this an AsyncAPI spec?)")
    if not spec.get("channels"):
        sys.exit("error: no channels found in AsyncAPI spec")

    graph = build(spec, args.group, args.direction)
    text = json.dumps(graph, indent=2)
    if args.output:
        # pi-lens-ignore: ast-grep:unchecked-throwing-call-python
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        sys.stderr.write(f"wrote {args.output}\n")
    else:
        sys.stdout.write(text)
    operation_count = sum(node["id"].startswith("operation:") for node in graph["nodes"])
    sys.stderr.write(
        f"{operation_count} operations, {len(spec['channels'])} channels, "
        f"{len((spec.get('components') or {}).get('schemas') or {})} schemas, "
        f"{len(graph['edges'])} edges\n"
    )


if __name__ == "__main__":
    main()
