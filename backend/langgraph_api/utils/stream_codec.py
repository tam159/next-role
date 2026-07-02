from __future__ import annotations

import base64
from dataclasses import dataclass

import orjson
import structlog
import zstandard

PROTOCOL_VERSION = 1
PROTOCOL_VERSION_COMPRESSED = 2
"""
Version 1:
Byte Offsets
0        1                  3                5                    5+N                5+N+M
+--------+------------------+----------------+------------------+------------------+--------------------+
| version| stream_id_len    | event_len      | stream_id        | event            | message            |
+--------+------------------+----------------+------------------+------------------+--------------------+
   1 B         2 B                2 B              N B                 M B               variable

Version 2: Same header layout as v1, but message is zstd-compressed.

---- Old (to be dropped soon / multiple formats)
Version 0 (old):
1) b"$:" + <stream_id> + b"$:" + <event> + b"$:" + <raw_json>
2) b"$:" + <stream_id> + b"$:" + <raw_json>
"""

BYTE_MASK = 0xFF
HEADER_LEN = 5
logger = structlog.stdlib.get_logger(__name__)

_zstd_decompressor = zstandard.ZstdDecompressor()


class StreamFormatError(ValueError):
    """Raised when a stream frame fails validation."""


@dataclass(slots=True)
class StreamPacket:
    version: int
    event: memoryview | bytes
    message: memoryview | bytes
    stream_id: memoryview | bytes | None

    @property
    def event_bytes(self) -> bytes:
        return (
            self.event.tobytes() if isinstance(self.event, memoryview) else self.event
        )

    @property
    def message_bytes(self) -> bytes:
        return (
            self.message.tobytes()
            if isinstance(self.message, memoryview)
            else self.message
        )

    @property
    def resumable(self) -> bool:
        return self.stream_id is not None

    @property
    def stream_id_bytes(self) -> bytes | None:
        if self.stream_id is None:
            return None
        if isinstance(self.stream_id, bytes):
            return self.stream_id
        return self.stream_id.tobytes()


class StreamCodec:
    """Encodes v1 stream frames and decodes any supported version (v1, v2)."""

    __slots__ = ()

    def encode(
        self,
        event: str,
        message: bytes,
        *,
        stream_id: str | None = None,
    ) -> bytes:
        if not event:
            raise StreamFormatError("event cannot be empty")
        event_bytes = event.encode("utf-8")
        if len(event_bytes) > 0xFFFF:
            raise StreamFormatError("event exceeds 65535 bytes; cannot encode")

        if stream_id:
            stream_id_bytes = stream_id.encode("utf-8")
            if len(stream_id_bytes) > 0xFFFF:
                raise StreamFormatError("stream_id exceeds 65535 bytes; cannot encode")
        else:
            stream_id_bytes = None
        stream_id_len = len(stream_id_bytes) if stream_id_bytes else 0
        event_len = len(event_bytes)
        frame = bytearray(HEADER_LEN + stream_id_len + event_len + len(message))
        frame[0] = PROTOCOL_VERSION
        frame[1:3] = stream_id_len.to_bytes(2, "big")
        frame[3:5] = event_len.to_bytes(2, "big")

        cursor = HEADER_LEN
        if stream_id_bytes is not None:
            frame[cursor : cursor + stream_id_len] = stream_id_bytes
            cursor += stream_id_len

        frame[cursor : cursor + event_len] = event_bytes
        cursor += event_len
        frame[cursor:] = message
        return bytes(frame)

    def decode(self, data: bytes | bytearray | memoryview) -> StreamPacket:
        view = data if isinstance(data, memoryview) else memoryview(data)
        if len(view) < HEADER_LEN:
            raise StreamFormatError("frame too short")

        version = view[0]
        if version not in (PROTOCOL_VERSION, PROTOCOL_VERSION_COMPRESSED):
            raise StreamFormatError(f"unsupported protocol version: {version}")

        stream_id_len = int.from_bytes(view[1:3], "big")
        event_len = int.from_bytes(view[3:5], "big")
        if event_len == 0:
            raise StreamFormatError("event cannot be empty")
        offset = HEADER_LEN
        if stream_id_len > 0:
            stream_id_view = view[offset : offset + stream_id_len]
            offset += stream_id_len
        else:
            stream_id_view = None
        if len(view) < offset + event_len:
            raise StreamFormatError("truncated event payload")
        event_view = view[offset : offset + event_len]
        offset += event_len
        message_view = view[offset:]

        if version == PROTOCOL_VERSION_COMPRESSED:
            try:
                decompressed = _zstd_decompressor.decompress(message_view)
            except zstandard.ZstdError as exc:
                raise StreamFormatError(
                    f"failed to decompress zstd message: {exc}"
                ) from exc
            message_view = memoryview(decompressed)

        return StreamPacket(
            version=version,
            event=event_view,
            message=message_view,
            stream_id=stream_id_view,
        )

    def decode_safe(self, data: bytes | bytearray | memoryview) -> StreamPacket | None:
        try:
            return self.decode(data)
        except StreamFormatError as e:
            logger.warning("Failed to decode stream frame", error=e)
            return None


STREAM_CODEC = StreamCodec()


def decode_stream_message(
    data: bytes | bytearray | memoryview,
    *,
    channel: bytes | str | None = None,
) -> StreamPacket:
    if isinstance(data, memoryview):
        view = data
    elif isinstance(data, (bytes, bytearray)):
        view = memoryview(data)
    else:
        logger.warning("Unknown type for stream message", type=type(data))
        view = memoryview(bytes(data))

    # Current protocol version (v1 + v2)
    if packet := STREAM_CODEC.decode_safe(view):
        return packet
    logger.debug("Attempting to decode a v0 formatted stream message")
    # Legacy codecs. Yuck. Won't be hit unless you have stale pods running (or for a brief period during upgrade).
    # Schedule for removal in next major release.
    if packet := _decode_v0_resumable_format(view, channel):
        return packet

    # Non-resumable format.
    if packet := _decode_v0_live_format(view, channel):
        return packet
    raise StreamFormatError("failed to decode stream message")


_STREAMING_DELIMITER = b"$:"
_STREAMING_DELIMITER_LEN = len(_STREAMING_DELIMITER)


def _decode_v0_resumable_format(
    view: memoryview,
    channel: bytes | str | None = None,
) -> StreamPacket | None:
    """
    Legacy v0 resumable format:
      1) b"$:" + <stream_id> + b"$:" + <event> + b"$:" + <raw_json>
      2) b"$:" + <stream_id> + b"$:" + <raw_json>
    """

    # must start with "$:"
    if (
        len(view) < _STREAMING_DELIMITER_LEN
        or view[:_STREAMING_DELIMITER_LEN] != _STREAMING_DELIMITER
    ):
        return None

    # "$:<stream_id>$:"
    first = _find_delim(view, _STREAMING_DELIMITER_LEN, _STREAMING_DELIMITER)
    if first == -1:
        return None
    stream_view = view[_STREAMING_DELIMITER_LEN:first]

    # try "$:<event>$:"
    second = _find_delim(view, first + _STREAMING_DELIMITER_LEN, _STREAMING_DELIMITER)
    if second != -1:
        event_view = view[first + _STREAMING_DELIMITER_LEN : second]
        msg_view = view[second + _STREAMING_DELIMITER_LEN :]
        return StreamPacket(
            version=0,
            event=event_view,
            message=msg_view,
            stream_id=stream_view,
        )

    chan_bytes = channel.encode("utf-8") if isinstance(channel, str) else channel

    if chan_bytes:
        marker = b":stream:"
        idx = chan_bytes.rfind(marker)
        event_bytes = chan_bytes[idx + len(marker) :] if idx != -1 else chan_bytes
    else:
        event_bytes = b""

    msg_view = view[first + _STREAMING_DELIMITER_LEN :]
    return StreamPacket(
        version=0,
        event=memoryview(event_bytes),
        message=msg_view,
        stream_id=stream_view,
    )


def _decode_v0_live_format(
    view: memoryview, channel: bytes | str | None = None
) -> StreamPacket | None:
    try:
        package = orjson.loads(view)
    except orjson.JSONDecodeError:
        return _decode_v0_flat_format(view, channel)
    if (
        not isinstance(package, dict)
        or "event" not in package
        or "message" not in package
    ):
        return _decode_v0_flat_format(view, channel)
    event_obj = package.get("event")
    message_obj = package.get("message")
    if event_obj is None:
        event_bytes = b""
    elif isinstance(event_obj, str):
        event_bytes = event_obj.encode()
    elif isinstance(event_obj, (bytes, bytearray, memoryview)):
        event_bytes = bytes(event_obj)
    else:
        event_bytes = orjson.dumps(event_obj)

    if isinstance(message_obj, (bytes, bytearray, memoryview)):
        message_view = memoryview(bytes(message_obj))
    elif isinstance(message_obj, str):
        try:
            message_view = memoryview(base64.b64decode(message_obj))
        except Exception:
            message_view = memoryview(message_obj.encode())
    elif message_obj is None:
        message_view = memoryview(b"")
    else:
        message_view = memoryview(orjson.dumps(message_obj))

    return StreamPacket(
        event=event_bytes,
        message=message_view,
        stream_id=None,
        version=0,
    )


def _decode_v0_flat_format(
    view: memoryview, channel: bytes | str | None = None
) -> StreamPacket | None:
    packet = bytes(view)
    stream_id = None
    if channel is None:
        return
    if packet.startswith(b"$:"):
        _, stream_id, packet = packet.split(b":", 2)
    channel = channel.encode("utf-8") if isinstance(channel, str) else channel
    channel = channel.split(b":")[-1]
    return StreamPacket(
        version=0,
        event=memoryview(channel),
        message=memoryview(packet),
        stream_id=stream_id,
    )


def _find_delim(view: memoryview, start: int, delimiter: bytes) -> int:
    delim_len = len(delimiter)
    end = len(view) - delim_len
    i = start
    while i <= end:
        if view[i : i + delim_len] == delimiter:
            return i
        i += 1
    return -1
