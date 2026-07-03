from collections.abc import Mapping
from typing import Any

import orjson
from google.protobuf import struct_pb2


def struct_from_dict(d: Mapping[str, Any]) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


def _default_serializer(obj: Any) -> Any:
    if hasattr(obj, "dict") and callable(obj.dict):
        return obj.dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Type is not JSON serializable: {type(obj).__name__}")


def raw_map_from_dict(d: Mapping[str, Any]) -> Mapping[str, bytes]:
    return {k: orjson.dumps(v, default=_default_serializer) for k, v in d.items()}


def dict_from_raw_map(m: Mapping[str, bytes]) -> dict[str, Any]:
    return {k: orjson.loads(v) for k, v in m.items()}
