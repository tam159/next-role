"""Object-storage settings, key mapping, and byte helpers for binary artifacts.

The artifact prefixes `/upload/`, `/tailored_resume/`, and
`/interview_battlecard/` live in an S3-compatible object store (SeaweedFS in
local dev via docker compose; S3 / GCS / Azure or any S3-compatible service in
the cloud — `obstore` speaks all of them). Postgres keeps only text artifacts;
renders use a throwaway temp dir, so no artifact ever lives on local disk.

Object keys are a pure function of the virtual path — there is no database
registry. `/upload/cv.pdf` maps to `users/default/career_agent/upload/cv.pdf`;
the `users/default/` segment is the future multi-user seam (inject a real
identity here and every artifact is scoped without a key migration).

Everything here is shared by two consumers: `ObjectStoreBackend` (the
deepagents filesystem backend mounted as CompositeBackend routes) and
`backend/agents/files_api.py` (the HTTP file surface for the frontend), so the
path↔key mapping exists exactly once.
"""

from __future__ import annotations

import functools
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from obstore.store import S3Store
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from obstore.store import ObjectStore

# The multi-user seam: swap "default" for an authenticated identity later.
KEY_SCOPE = "users/default/career_agent"

# Artifact areas routed to object storage. Keep in sync with the
# CompositeBackend routes in agents.py and the files-API allowlist.
AREAS = ("upload", "tailored_resume", "interview_battlecard")


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


def area_key_prefix(area: str) -> str:
    """Object-key prefix (no trailing slash) holding everything in `area`."""
    return f"{KEY_SCOPE}/{area}"


def key_for_area(area: str, rel_path: str) -> str | None:
    """Map a composite-stripped path within `area` to its object key.

    `area="upload"`, `rel_path="/cv.pdf"` → `users/default/career_agent/upload/cv.pdf`.
    Returns `None` for unsafe paths.
    """
    rel = _safe_relative(rel_path)
    if rel is None:
        return None
    return f"{KEY_SCOPE}/{area}/{rel}"


def key_for_virtual_path(path: str) -> str | None:
    """Map a full virtual path (e.g. `/upload/cv.pdf`) to its object key.

    Returns `None` when the path is unsafe or its first segment is not a
    routed artifact area — this doubles as the files-API allowlist.
    """
    rel = _safe_relative(path)
    if rel is None:
        return None
    area, _, remainder = rel.partition("/")
    if area not in AREAS or not remainder:
        return None
    return f"{KEY_SCOPE}/{rel}"


def virtual_path_for_key(key: str) -> str | None:
    """Invert `key_for_virtual_path`: object key → `/area/...` virtual path."""
    prefix = f"{KEY_SCOPE}/"
    if not key.startswith(prefix):
        return None
    rel = key[len(prefix) :]
    area, _, remainder = rel.partition("/")
    if area not in AREAS or not remainder:
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
