"""Object-storage settings, key mapping, and byte helpers for binary artifacts.

The artifact prefixes `/upload/`, `/tailored_resume/`, and
`/interview_battlecard/` live in an S3-compatible object store (SeaweedFS in
local dev via docker compose; S3 / GCS / Azure or any S3-compatible service in
the cloud — `obstore` speaks all of them). Postgres keeps only text artifacts;
renders use a throwaway temp dir, so no artifact ever lives on local disk.

Object keys are a pure function of the virtual path and the caller's scope —
there is no database registry. `/upload/cv.pdf` maps to
`users/<scope>/career_agent/upload/cv.pdf`; `<scope>` is the authenticated
identity in multi-user mode and `default` otherwise (see `scope.object_scope`).
The agent segment comes from `AREA_ROOTS`, which maps each routed area to the
agent that owns it — the analytics agent's `/charts/` and `/reports/` land under
`users/<scope>/analytics_agent/` through these same builders.
The agent segment comes from `AREA_ROOTS`, which maps each routed area to the
agent that owns it — the analytics agent's `/charts/` and `/reports/` land
under `users/<scope>/analytics_agent/` through the same builders.

Everything here is shared by two consumers: `ObjectStoreBackend` (the
deepagents filesystem backend mounted as CompositeBackend routes) and
`backend/agents/files_api.py` (the HTTP file surface for the frontend), so the
path↔key mapping exists exactly once. Both pass the resolved `scope` — the
agent backend from the run's runtime, the files API from the request user.
"""

from __future__ import annotations

import functools
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from backend.agents.career_agent.scope import DEFAULT_OBJECT_SCOPE, KV_ROOT, object_scope
from obstore.store import S3Store
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from obstore.store import ObjectStore

# Single-user / unauthenticated key prefix (identity == "default") for the
# career agent's areas. Kept as a module constant for the default layout;
# multi-user callers pass an explicit `scope` to the key builders below.
KEY_SCOPE = object_scope(None)

#: Root segment for the analytics agent's areas (mirrors `scope.KV_ROOT`,
#: which is the career agent's).
ANALYTICS_ROOT = "analytics_agent"

# Artifact areas routed to object storage, mapped to the agent whose key space
# they live in: `/upload/cv.pdf` -> `users/<scope>/career_agent/upload/cv.pdf`,
# `/charts/t1/runs.plotly.json` -> `users/<scope>/analytics_agent/charts/...`.
# An area name is globally unique — it is the first segment of the virtual path
# every agent's CompositeBackend routes on. Keep in sync with the routes in each
# agent's `agents.py`; this mapping doubles as the files-API allowlist.
AREA_ROOTS: Mapping[str, str] = MappingProxyType(
    {
        "upload": KV_ROOT,
        "tailored_resume": KV_ROOT,
        "interview_battlecard": KV_ROOT,
        "charts": ANALYTICS_ROOT,
        "reports": ANALYTICS_ROOT,
    },
)

#: Every routed area, in registration order. Retained as the public name the
#: files API and tests import.
AREAS = tuple(AREA_ROOTS)


class ObjectStoreSettings(BaseSettings):
    """Connection settings for the artifact object store.

    Read from `OBJECT_STORE_*` env vars (compose passes them from `.env`;
    the in-container endpoint is overridden to the service-network URL).
    All fields default so importing modules never requires storage env —
    `get_store()` validates at first use instead.
    """

    model_config = SettingsConfigDict(env_prefix="OBJECT_STORE_", extra="ignore")

    endpoint: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    force_path_style: bool = True


def build_store_from_settings(settings: ObjectStoreSettings) -> ObjectStore:
    """Build an S3-compatible store client for the given settings."""
    if not settings.endpoint or not settings.bucket:
        msg = (
            "Object storage is not configured: set OBJECT_STORE_ENDPOINT and "
            "OBJECT_STORE_BUCKET (see .env.example)."
        )
        raise RuntimeError(msg)
    return S3Store(
        settings.bucket,
        endpoint=settings.endpoint,
        access_key_id=settings.access_key,
        secret_access_key=settings.secret_key,
        region=settings.region,
        virtual_hosted_style_request=not settings.force_path_style,
        # SeaweedFS (and most emulators) serve plain HTTP on the compose network.
        client_options={"allow_http": settings.endpoint.startswith("http://")},
    )


@functools.lru_cache(maxsize=1)
def get_store() -> ObjectStore:
    """Build (once) the S3-compatible store client from env settings.

    Lazy on purpose: `agents.py` is imported by core-server purely to
    enumerate graphs, which must not require storage configuration.
    """
    return build_store_from_settings(ObjectStoreSettings())


def _safe_relative(path: str) -> str | None:
    """Normalize a path fragment to a safe key suffix, or `None` if unsafe.

    Rejects traversal (`..`), home expansion (`~`), backslashes, and empty
    results. Accepts either `/foo/bar.pdf` or `foo/bar.pdf` forms.
    """
    rel = path.strip().lstrip("/")
    if not rel or "\\" in rel or rel.startswith("~"):
        return None
    parts = PurePosixPath(rel).parts
    if not parts or any(part in ("..", ".") for part in parts):
        return None
    return "/".join(parts)


def area_key_prefix(area: str, scope: str | None = None) -> str:
    """Object-key prefix (no trailing slash) holding everything in `area`.

    `scope` is the caller's identity; omitted/`None` uses the default
    single-user layout (`object_scope` resolves it). The agent segment comes
    from :data:`AREA_ROOTS`, so each area lands in its owning agent's space.
    """
    return f"{object_scope(scope, agent_root=AREA_ROOTS[area])}/{area}"


def key_for_area(area: str, rel_path: str, scope: str | None = None) -> str | None:
    """Map a composite-stripped path within `area` to its object key.

    `area="upload"`, `rel_path="/cv.pdf"` → `users/<scope>/career_agent/upload/cv.pdf`;
    `area="charts"` → `users/<scope>/analytics_agent/charts/...`.
    Returns `None` for unsafe paths and unregistered areas.
    """
    rel = _safe_relative(rel_path)
    root = AREA_ROOTS.get(area)
    if rel is None or root is None:
        return None
    return f"{object_scope(scope, agent_root=root)}/{area}/{rel}"


def key_for_virtual_path(path: str, scope: str | None = None) -> str | None:
    """Map a full virtual path (e.g. `/upload/cv.pdf`) to its object key.

    Returns `None` when the path is unsafe or its first segment is not a
    routed artifact area — this doubles as the files-API allowlist.
    """
    rel = _safe_relative(path)
    if rel is None:
        return None
    area, _, remainder = rel.partition("/")
    root = AREA_ROOTS.get(area)
    if root is None or not remainder:
        return None
    return f"{object_scope(scope, agent_root=root)}/{rel}"


def virtual_path_for_key(key: str, scope: str | None = None) -> str | None:
    """Invert `key_for_virtual_path`: object key → `/area/...` virtual path.

    The key must carry the agent root that owns its area, so a key filed under
    another agent's root is rejected rather than mapped back to a valid-looking
    virtual path.
    """
    user_prefix = f"users/{scope or DEFAULT_OBJECT_SCOPE}/"
    if not key.startswith(user_prefix):
        return None
    root, _, rel = key[len(user_prefix) :].partition("/")
    area, _, remainder = rel.partition("/")
    if not remainder or AREA_ROOTS.get(area) != root:
        return None
    return f"/{rel}"


def get_bytes(store: ObjectStore, key: str) -> bytes | None:
    """Fetch an object's bytes, or `None` when the key does not exist."""
    # obstore raises builtins.FileNotFoundError for missing keys (its
    # `exceptions.NotFoundError` alias is deprecated).
    try:
        return bytes(store.get(key).bytes())
    except FileNotFoundError:
        return None


def put_bytes(store: ObjectStore, key: str, data: bytes) -> None:
    """Write an object (PutObject semantics: silently overwrites)."""
    store.put(key, data)


def head_meta(store: ObjectStore, key: str) -> dict[str, Any] | None:
    """Return an object's metadata mapping, or `None` when absent."""
    try:
        return dict(store.head(key))
    except FileNotFoundError:
        return None


def delete_key(store: ObjectStore, key: str) -> bool:
    """Delete an object, reporting whether it existed.

    S3 deletes are idempotent (deleting a missing key succeeds), so existence
    is head-checked first to preserve 404 contracts at the HTTP layer.
    """
    if head_meta(store, key) is None:
        return False
    store.delete(key)
    return True


def list_meta(store: ObjectStore, prefix: str) -> list[dict[str, Any]]:
    """List object metadata under a key prefix (recursive, whole subtree)."""
    return [dict(meta) for meta in store.list(prefix=prefix).collect()]
