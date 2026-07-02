from typing import Any

from langgraph_sdk.auth import Auth
from starlette.responses import Response
from starlette.routing import BaseRoute

from langgraph_api.auth.custom import handle_event as _handle_event
from langgraph_api.encryption.middleware import (
    decrypt_response,
    decrypt_responses,
    encrypt_request,
)
from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.schema import STORE_ENCRYPTION_FIELDS
from langgraph_api.store import get_store
from langgraph_api.utils import get_auth_ctx
from langgraph_api.validation import (
    StoreDeleteRequest,
    StoreListNamespacesRequest,
    StorePutRequest,
    StoreSearchRequest,
)
from langgraph_runtime.retry import retry_db


def _validate_namespace(namespace: tuple[str, ...]) -> Response | None:
    for label in namespace:
        if not label or "." in label:
            return Response(
                status_code=422,
                content=f"Namespace labels cannot be empty or contain periods. Received: {namespace}",
            )


async def handle_event(
    action: str,
    value: Any,
) -> None:
    ctx = get_auth_ctx()
    if not ctx:
        return
    await _handle_event(
        Auth.types.AuthContext(
            user=ctx.user,
            permissions=ctx.permissions,
            resource="store",
            action=action,
        ),
        value,
    )


@retry_db
async def put_item(request: ApiRequest):
    """Store or update an item."""
    payload = await request.json(StorePutRequest)
    payload = await encrypt_request(payload, "store", STORE_ENCRYPTION_FIELDS)
    namespace = tuple(payload["namespace"]) if payload.get("namespace") else ()
    if err := _validate_namespace(namespace):
        return err
    handler_payload: dict[str, Any] = {
        "namespace": namespace,
        "key": payload["key"],
        "value": payload["value"],
        "index": payload.get("index"),
        "ttl": payload.get("ttl"),
    }
    await handle_event("put", handler_payload)
    await (await get_store()).aput(
        handler_payload["namespace"],
        handler_payload["key"],
        handler_payload["value"],
        index=handler_payload["index"],
        ttl=handler_payload["ttl"],
    )
    return Response(status_code=204)


@retry_db
async def get_item(request: ApiRequest):
    """Retrieve a single item."""
    namespace = tuple(request.query_params.get("namespace", "").split("."))
    if err := _validate_namespace(namespace):
        return err
    key = request.query_params.get("key")
    if not key:
        return ApiResponse({"error": "Key is required"}, status_code=400)
    refresh_ttl_raw = request.query_params.get("refresh_ttl")
    handler_payload: dict[str, Any] = {
        "namespace": namespace,
        "key": key,
        "refresh_ttl": refresh_ttl_raw.lower() == "true"
        if refresh_ttl_raw is not None
        else None,
    }
    await handle_event("get", handler_payload)
    result = await (await get_store()).aget(
        handler_payload["namespace"],
        handler_payload["key"],
        refresh_ttl=handler_payload["refresh_ttl"],
    )
    if result is None:
        return ApiResponse(None)
    return ApiResponse(
        await decrypt_response(result.dict(), "store", STORE_ENCRYPTION_FIELDS)
    )


@retry_db
async def delete_item(request: ApiRequest):
    """Delete an item."""
    payload = await request.json(StoreDeleteRequest)
    namespace = tuple(payload["namespace"]) if payload.get("namespace") else ()
    if err := _validate_namespace(namespace):
        return err
    handler_payload = {
        "namespace": namespace,
        "key": payload["key"],
    }
    await handle_event("delete", handler_payload)
    await (await get_store()).adelete(
        handler_payload["namespace"], handler_payload["key"]
    )
    return Response(status_code=204)


@retry_db
async def search_items(request: ApiRequest):
    """Search or list items within a namespace prefix."""
    payload = await request.json(StoreSearchRequest)
    namespace_prefix = tuple(payload["namespace_prefix"])
    if err := _validate_namespace(namespace_prefix):
        return err
    filter = payload.get("filter")
    limit = int(payload.get("limit") or 10)
    offset = int(payload.get("offset") or 0)
    query = payload.get("query")
    handler_payload: dict[str, Any] = {
        "namespace": namespace_prefix,
        "filter": filter,
        "limit": limit,
        "offset": offset,
        "query": query,
        "refresh_ttl": payload.get("refresh_ttl"),
    }
    await handle_event("search", handler_payload)
    items = await (await get_store()).asearch(
        handler_payload["namespace"],
        filter=handler_payload["filter"],
        limit=handler_payload["limit"],
        offset=handler_payload["offset"],
        query=handler_payload["query"],
        refresh_ttl=handler_payload["refresh_ttl"],
    )
    return ApiResponse(
        {
            "items": await decrypt_responses(
                [item.dict() for item in items], "store", STORE_ENCRYPTION_FIELDS
            )
        }
    )


@retry_db
async def list_namespaces(request: ApiRequest):
    """List namespaces with optional match conditions."""
    payload = await request.json(StoreListNamespacesRequest)
    prefix = tuple(payload["prefix"]) if payload.get("prefix") else None
    suffix = tuple(payload["suffix"]) if payload.get("suffix") else None
    err = None
    if prefix and (err := _validate_namespace(prefix)):
        return err
    if suffix and (err := _validate_namespace(suffix)):
        return err
    max_depth = payload.get("max_depth")
    limit = payload.get("limit", 100)
    offset = payload.get("offset", 0)
    handler_payload = {
        "namespace": prefix,
        "suffix": suffix,
        "max_depth": max_depth,
        "limit": limit,
        "offset": offset,
    }
    await handle_event("list_namespaces", handler_payload)
    result = await (await get_store()).alist_namespaces(
        prefix=handler_payload["namespace"],
        suffix=handler_payload["suffix"],
        max_depth=handler_payload["max_depth"],
        limit=handler_payload["limit"],
        offset=handler_payload["offset"],
    )
    return ApiResponse({"namespaces": result})


store_routes: list[BaseRoute] = [
    ApiRoute("/store/items", endpoint=put_item, methods=["PUT"]),
    ApiRoute("/store/items", endpoint=get_item, methods=["GET"]),
    ApiRoute("/store/items", endpoint=delete_item, methods=["DELETE"]),
    ApiRoute("/store/items/search", endpoint=search_items, methods=["POST"]),
    ApiRoute("/store/namespaces", endpoint=list_namespaces, methods=["POST"]),
]
