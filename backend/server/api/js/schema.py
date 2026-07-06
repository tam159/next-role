from typing import Any

from typing_extensions import TypedDict


class RequestPayload(TypedDict):
    method: str
    id: str
    data: dict


class ResponsePayload(TypedDict):
    method: str
    id: str
    success: bool | None
    data: Any | None


class StreamPingData(TypedDict):
    method: str
    id: str


class StreamData(TypedDict):
    done: bool
    value: Any


class ErrorData(TypedDict):
    error: str
    message: str
