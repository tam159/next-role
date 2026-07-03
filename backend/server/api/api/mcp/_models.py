from typing import Any, NotRequired

from typing_extensions import TypedDict


class JsonRpcErrorObject(TypedDict):
    code: int
    message: str
    data: NotRequired[Any]


class JsonRpcRequest(TypedDict):
    jsonrpc: str  # Must be "2.0"
    id: str | int
    method: str
    params: NotRequired[dict[str, Any]]


class JsonRpcResponse(TypedDict):
    jsonrpc: str  # Must be "2.0"
    id: str | int
    result: NotRequired[dict[str, Any]]
    error: NotRequired[JsonRpcErrorObject]


class JsonRpcNotification(TypedDict):
    jsonrpc: str  # Must be "2.0"
    method: str
    params: NotRequired[dict[str, Any]]
