"""Artifact files HTTP API, mounted into the agent server via `LANGGRAPH_HTTP`.

The frontend moves file bytes through these endpoints (it holds no storage
credentials); bytes live in the S3-compatible object store configured by
`OBJECT_STORE_*`. The wire contract speaks **virtual paths** (`/upload/cv.pdf`)
— the same currency the agent's filesystem tools use — and the virtual-path ↔
object-key mapping is `backend.agents.career_agent.object_storage`, shared
with the agent's `ObjectStoreBackend`.

Endpoints (response shapes preserved from the former Next.js `/api/files/*`
routes so the frontend components stay unchanged):

- `GET  /files/list?prefixes=/upload/,...` → `{files: [{path, size, isBinary, modifiedAt}]}`
- `GET  /files/read?path=/upload/cv.pdf`   → `{content, encoding: "utf-8"|"base64"}`
- `POST /files/upload` (multipart `path` + `file`*) → `{uploaded: [...], errors: [...]}`
- `PUT  /files/write` (`{path, content, encoding?}`) → `{ok: true}`
- `DELETE /files/delete?path=...` → `{ok: true}` (404 when absent)

Deliberately imports nothing from `server.*` (those modules require env at
import time); the server wraps this app with its own CORS + logging middleware
and, later, auth via `enable_custom_route_auth`. Storage calls run in worker
threads so the (sync) obstore client never blocks the server's event loop.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import os
from typing import TYPE_CHECKING, Any

from backend.agents.career_agent.object_storage import (
    AREAS,
    area_key_prefix,
    delete_key,
    get_bytes,
    get_store,
    key_for_virtual_path,
    list_meta,
    put_bytes,
    virtual_path_for_key,
)
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from starlette.requests import Request

_ALLOWED_UPLOAD_EXTS = {"pdf", "doc", "docx", "txt", "md"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Multi-user mode marker: when the server runs with custom auth, its
# middleware wraps this app too (`enable_custom_route_auth` in LANGGRAPH_HTTP)
# and populates request.scope["user"]. The guard below is belt-and-braces for
# the misconfigured case (auth set but the custom-route flag missing).
_AUTH_ENABLED = bool(os.environ.get("LANGGRAPH_AUTH"))


def _authenticated(handler):  # noqa: ANN001, ANN202
    """Reject unauthenticated requests with 401 in multi-user mode."""

    @functools.wraps(handler)
    async def wrapped(request: Request) -> JSONResponse:
        if _AUTH_ENABLED:
            user = request.scope.get("user")
            if user is None or not getattr(user, "is_authenticated", False):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await handler(request)

    return wrapped


def _scope(request: Request) -> str | None:
    """Return the caller's object-store scope (identity), None for single-user.

    The server's auth middleware puts the authenticated user on the request
    scope; its identity scopes object keys the same way the agent's runtime
    identity does. None (no user) resolves to the default single-user layout.
    """
    user = request.scope.get("user")
    identity = getattr(user, "identity", None) if user is not None else None
    return identity or None


# Extensions served as base64 (mirrors the frontend's former BINARY_EXTS).
_BINARY_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico",
    "pdf", "doc", "docx", "zip", "gz", "tar",
    "mp3", "mp4", "wav", "ogg",
    "woff", "woff2", "ttf", "otf",
}  # fmt: skip


def _ext_of(path: str) -> str:
    """Lowercased extension of `path` without the dot (`""` if none)."""
    _, _, ext = path.rpartition(".")
    return ext.lower() if "." in path else ""


def _is_binary_path(path: str) -> bool:
    """Whether `path` should be transported as base64."""
    return _ext_of(path) in _BINARY_EXTS


def _iso_modified(meta: dict[str, Any]) -> str:
    """ISO-8601 `modified_at` from an object-meta mapping (`""` if unknown)."""
    last_modified = meta.get("last_modified")
    return last_modified.isoformat() if last_modified is not None else ""


def _area_of_prefix(prefix: str) -> str | None:
    """Validate a `?prefixes=` entry (`/upload/`) down to its area name."""
    area = prefix.strip().strip("/")
    return area if area in AREAS else None


async def list_files(request: Request) -> JSONResponse:
    """List artifact files under the requested virtual prefixes."""
    prefixes_param = request.query_params.get("prefixes")
    if not prefixes_param:
        return JSONResponse({"error": "Missing 'prefixes'"}, status_code=400)

    areas: list[str] = []
    for raw in prefixes_param.split(","):
        if not raw.strip():
            continue
        area = _area_of_prefix(raw)
        if area is None:
            return JSONResponse({"error": f"Disallowed prefix: {raw}"}, status_code=403)
        areas.append(area)

    scope = _scope(request)

    def _collect() -> list[dict[str, Any]]:
        store = get_store()
        files: list[dict[str, Any]] = []
        for area in areas:
            for meta in list_meta(store, area_key_prefix(area, scope) + "/"):
                vpath = virtual_path_for_key(str(meta["path"]), scope)
                if vpath is None:
                    continue
                files.append(
                    {
                        "path": vpath,
                        "size": int(meta.get("size", 0)),
                        "isBinary": _is_binary_path(vpath),
                        "modifiedAt": _iso_modified(meta),
                    },
                )
        files.sort(key=lambda f: f["path"])
        return files

    try:
        files = await asyncio.to_thread(_collect)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"files": files})


async def read_file(request: Request) -> JSONResponse:
    """Return one file's content, base64 for binaries and utf-8 for text."""
    path = request.query_params.get("path")
    if not path:
        return JSONResponse({"error": "Missing 'path'"}, status_code=400)
    key = key_for_virtual_path(path, _scope(request))
    if key is None:
        return JSONResponse({"error": "Forbidden path"}, status_code=403)

    try:
        data = await asyncio.to_thread(get_bytes, get_store(), key)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if data is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    if not _is_binary_path(path):
        try:
            return JSONResponse({"content": data.decode("utf-8"), "encoding": "utf-8"})
        except UnicodeDecodeError:
            pass  # fall through to base64 for text-named binary content
    return JSONResponse(
        {"content": base64.standard_b64encode(data).decode("ascii"), "encoding": "base64"},
    )


async def upload_files(request: Request) -> JSONResponse:
    """Accept multipart uploads into an artifact area (silently overwrites)."""
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "Expected multipart/form-data"}, status_code=400)

    dir_field = form.get("path")
    if not isinstance(dir_field, str) or not dir_field:
        return JSONResponse({"error": "Missing 'path' field"}, status_code=400)

    file_entries = [v for v in form.getlist("file") if isinstance(v, UploadFile)]
    if not file_entries:
        return JSONResponse({"error": "No files provided"}, status_code=400)

    uploaded: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    store = get_store()
    scope = _scope(request)

    for file in file_entries:
        name = file.filename or ""
        ext = _ext_of(name)
        if ext not in _ALLOWED_UPLOAD_EXTS:
            errors.append({"name": name, "reason": f"Unsupported extension: .{ext}"})
            continue
        if "/" in name or "\\" in name or name.startswith("."):
            errors.append({"name": name, "reason": "Invalid filename"})
            continue

        target = f"{dir_field.rstrip('/')}/{name}"
        key = key_for_virtual_path(target, scope)
        if key is None:
            errors.append({"name": name, "reason": "Forbidden path"})
            continue

        content = await file.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            limit_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
            errors.append({"name": name, "reason": f"File exceeds {limit_mb} MB limit"})
            continue

        try:
            await asyncio.to_thread(put_bytes, store, key, content)
        except Exception as e:
            errors.append({"name": name, "reason": str(e)})
            continue
        uploaded.append({"path": target, "size": len(content)})

    status = 400 if not uploaded else 200
    return JSONResponse({"uploaded": uploaded, "errors": errors}, status_code=status)


async def write_file(request: Request) -> JSONResponse:
    """Overwrite (or create) one file from a JSON body, utf-8 or base64."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    path = body.get("path")
    content = body.get("content")
    encoding = body.get("encoding", "utf-8")
    if not path or not isinstance(content, str):
        return JSONResponse({"error": "Missing 'path' or 'content'"}, status_code=400)
    key = key_for_virtual_path(path, _scope(request))
    if key is None:
        return JSONResponse({"error": "Forbidden path"}, status_code=403)

    if encoding == "base64":
        try:
            # validate=True: reject junk instead of silently dropping it.
            data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            return JSONResponse({"error": "Invalid base64 content"}, status_code=400)
    else:
        data = content.encode("utf-8")

    try:
        await asyncio.to_thread(put_bytes, get_store(), key, data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


async def delete_file(request: Request) -> JSONResponse:
    """Delete one file; 404 when it does not exist (delete is head-checked)."""
    path = request.query_params.get("path")
    if not path:
        return JSONResponse({"error": "Missing 'path'"}, status_code=400)
    key = key_for_virtual_path(path, _scope(request))
    if key is None:
        return JSONResponse({"error": "Forbidden path"}, status_code=403)

    try:
        existed = await asyncio.to_thread(delete_key, get_store(), key)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not existed:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/files/list", _authenticated(list_files), methods=["GET"]),
        Route("/files/read", _authenticated(read_file), methods=["GET"]),
        Route("/files/upload", _authenticated(upload_files), methods=["POST"]),
        Route("/files/write", _authenticated(write_file), methods=["PUT"]),
        Route("/files/delete", _authenticated(delete_file), methods=["DELETE"]),
    ],
)
