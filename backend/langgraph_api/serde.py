import asyncio
import re
import uuid
from base64 import b64encode
from collections import deque
from collections.abc import Callable, Mapping
from datetime import timedelta, timezone
from decimal import Decimal
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path
from re import Pattern
from typing import Any, Literal, NamedTuple, cast, overload
from zoneinfo import ZoneInfo

import cloudpickle
import orjson
import structlog
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import (
    EmptyInputError,
    GraphBubbleUp,
    InvalidUpdateError,
    TaskNotFound,
)
from starlette.exceptions import HTTPException

logger = structlog.stdlib.get_logger(__name__)


class Fragment(NamedTuple):
    buf: bytes


def fragment_loads(data: bytes) -> Fragment:
    return Fragment(data)


def decimal_encoder(dec_value: Decimal) -> int | float:
    """
    Encodes a Decimal as int of there's no exponent, otherwise float

    This is useful when we use ConstrainedDecimal to represent Numeric(x,0)
    where a integer (but not int typed) is used. Encoding this as a float
    results in failed round-tripping between encode and parse.
    Our Id type is a prime example of this.

    >>> decimal_encoder(Decimal("1.0"))
    1.0

    >>> decimal_encoder(Decimal("1"))
    1
    """
    if (
        # maps to float('nan') / float('inf') / float('-inf')
        not dec_value.is_finite()
        # or regular float
        or cast("int", dec_value.as_tuple().exponent) < 0
    ):
        return float(dec_value)
    return int(dec_value)


def default(obj):
    # Only need to handle types that orjson doesn't serialize by default
    # https://github.com/ijl/orjson#serialize
    if isinstance(obj, Fragment):
        return orjson.Fragment(obj.buf)
    if (
        hasattr(obj, "model_dump")
        and callable(obj.model_dump)
        and not isinstance(obj, type)
    ):
        return obj.model_dump()
    elif hasattr(obj, "dict") and callable(obj.dict) and not isinstance(obj, type):
        return obj.dict()
    elif (
        hasattr(obj, "_asdict") and callable(obj._asdict) and not isinstance(obj, type)
    ):
        return obj._asdict()
    elif isinstance(obj, BaseException):
        if isinstance(
            obj,
            (
                # Python builtins
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                RecursionError,
                TimeoutError,
                # LangGraph DSL errors (GraphRecursionError ⊂ RecursionError,
                # GraphInterrupt/ParentCommand ⊂ GraphBubbleUp)
                InvalidUpdateError,
                GraphBubbleUp,
                EmptyInputError,
                TaskNotFound,
                # HTTP errors (e.g. auth failures during streaming)
                HTTPException,
            ),
        ):
            return {"error": type(obj).__name__, "message": str(obj)}
        return {"error": type(obj).__name__, "message": "An internal error occurred"}
    elif isinstance(obj, (set, frozenset, deque)):
        return list(obj)
    elif isinstance(obj, (timezone, ZoneInfo)):
        return obj.tzname(None)
    elif isinstance(obj, timedelta):
        return obj.total_seconds()
    elif isinstance(obj, Decimal):
        return decimal_encoder(obj)
    elif isinstance(
        obj,
        (
            uuid.UUID,
            IPv4Address,
            IPv4Interface,
            IPv4Network,
            IPv6Address,
            IPv6Interface,
            IPv6Network,
            Path,
        ),
    ):
        return str(obj)
    elif isinstance(obj, Pattern):
        return obj.pattern
    elif isinstance(obj, bytes | bytearray):
        return b64encode(obj).decode()
    return None


_option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _replace_surr(s: str) -> str:
    return s if _SURROGATE_RE.search(s) is None else _SURROGATE_RE.sub("?", s)


def _sanitise(o: Any) -> Any:
    if isinstance(o, str):
        return _replace_surr(o)
    if isinstance(o, Mapping):
        return {_sanitise(k): _sanitise(v) for k, v in o.items()}
    if isinstance(o, list | tuple | set):
        if (
            isinstance(o, tuple)
            and hasattr(o, "_asdict")
            and callable(o._asdict)
            and hasattr(o, "_fields")
            and isinstance(o._fields, tuple)
        ):  # named tuple
            return {f: _sanitise(ov) for f, ov in zip(o._fields, o, strict=True)}
        ctor = list if isinstance(o, list) else type(o)
        return ctor(_sanitise(x) for x in o)
    return o


def json_dumpb(obj) -> bytes:
    try:
        dumped = orjson.dumps(obj, default=default, option=_option)
    except TypeError as e:
        if "surrogates not allowed" not in str(e):
            raise
        dumped = orjson.dumps(_sanitise(obj), default=default, option=_option)
    if rb"\u0000" not in dumped:
        return dumped  # fast path — no null bytes
    # Replace with U+FFFD (replacement character) instead of stripping,
    # so that "key\x00" becomes "key\uFFFD" rather than "key" — preventing
    # a poisoned key from projecting onto a legitimate one.
    return dumped.replace(rb"\\u0000", rb"\\ufffd").replace(rb"\u0000", rb"\ufffd")


def json_loads(content: bytes | Fragment | dict) -> Any:
    if isinstance(content, Fragment):
        content = content.buf
    if isinstance(content, dict):
        return content
    return orjson.loads(content)


@overload
def json_dumpb_optional(obj: None) -> None: ...


@overload
def json_dumpb_optional(obj: Any) -> bytes: ...


def json_dumpb_optional(obj: Any | None) -> bytes | None:
    if obj is None:
        return
    return json_dumpb(obj)


def json_loads_optional(content: bytes | None) -> Any | None:
    if content is None:
        return
    return json_loads(content)


# Do not use. orjson holds the GIL the entire time it's running anyway.
async def ajson_loads(content: bytes | Fragment) -> Any:
    return await asyncio.to_thread(json_loads, content)


class Serializer(JsonPlusSerializer):
    def __init__(
        self,
        __unpack_ext_hook__: Callable[[int, bytes], Any] | None = None,
        pickle_fallback: bool | None = None,
    ):
        from langgraph_api.config import SERDE, USE_PICKLE_FALLBACK  # noqa: PLC0415

        allowed_json_modules: list[tuple[str, ...]] | Literal[True] | None = None
        if SERDE and "allowed_json_modules" in SERDE:
            allowed_ = SERDE["allowed_json_modules"]
            if allowed_ is True:
                allowed_json_modules = True
            elif allowed_ is None:
                allowed_json_modules = None
            else:
                allowed_json_modules = [tuple(x) for x in allowed_]
        if pickle_fallback is None:
            pickle_fallback = USE_PICKLE_FALLBACK

        super().__init__(
            allowed_json_modules=allowed_json_modules,
            __unpack_ext_hook__=__unpack_ext_hook__,
        )
        self.pickle_fallback = pickle_fallback

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        try:
            return super().dumps_typed(obj)
        except TypeError:
            return "pickle", cloudpickle.dumps(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data[0] == "pickle":
            if not self.pickle_fallback:
                raise ValueError(
                    "Pickle fallback is disabled. Cannot deserialize pickled object."
                )
            try:
                return cloudpickle.loads(data[1])
            except Exception as e:
                logger.warning(
                    "Failed to unpickle object, replacing w None", exc_info=e
                )
                return None
        try:
            return super().loads_typed(data)
        except Exception:
            if data[0] == "json":
                logger.exception(
                    "Heads up! There was a deserialization error of an item stored using 'json'-type serialization."
                    ' For security reasons, starting in langgraph-api version 0.5.0, we no longer serialize objects using the "json" type.'
                    " If you would like to retain the ability to deserialize old checkpoints saved in this format, "
                    'please set the "allowed_json_modules" option in your langgraph.json configuration to add the'
                    " necessary module and type paths to an allow-list to be deserialized. You can alkso retain the"
                    ' ability to insecurely deserialize custom types by setting it to "true".'
                )
            raise
