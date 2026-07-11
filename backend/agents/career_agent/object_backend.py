"""Deepagents filesystem backend over S3-compatible object storage.

`ObjectStoreBackend` implements the deepagents `BackendProtocol` for one
artifact area (`upload`, `tailored_resume`, `interview_battlecard`) and is
mounted per-area as a `CompositeBackend` route in `agents.py`. The composite
strips the route prefix before dispatching, so this backend sees paths like
`/cv.pdf` and maps them to object keys via `object_storage.key_for_area`.

Deliberately NOT a `SandboxBackendProtocol`: `execute` must keep dispatching
to the composite's default `VirtualPathShellBackend` (rendercv needs a real
filesystem; see `render_resume_pdf` in `tools.py` for the hydrate→render→
publish flow).

Conventions mirrored from the built-in backends (see `StoreBackend` /
`FilesystemBackend` in deepagents 0.6.x):

- errors are returned in-band via each result's `error` field, never raised;
- `write` refuses to overwrite (the `_upsert` helper in `tools.py` relies on
  that exact error to fall back to `edit`);
- binary reads return base64 `FileData`, which the filesystem middleware
  renders as a multimodal content block (PDFs surface as `file` blocks);
- bytes flow only through `upload_files`/`download_files`.

Sync methods only: the base class provides `asyncio.to_thread` async twins,
which is sufficient for the small artifact files this backend serves.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import obstore.exceptions
from backend.agents.career_agent.object_storage import (
    area_key_prefix,
    get_bytes,
    get_store,
    head_meta,
    key_for_area,
    list_meta,
    put_bytes,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    _get_file_type,
    _glob_search_files,
    grep_matches_from_files,
    perform_string_replacement,
    slice_read_response,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from obstore.store import ObjectStore

# Unexpected storage failures (network, auth, provider errors) — converted to
# in-band errors. Missing keys are handled earlier by the byte helpers.
_STORE_ERRORS = (obstore.exceptions.BaseError, OSError)

# Skip objects larger than this in grep, mirroring FilesystemBackend's
# `max_file_size_mb=10` guard — grep must download candidates to scan them.
_GREP_MAX_BYTES = 10 * 1024 * 1024


def _iso_modified(meta: dict[str, Any]) -> str:
    """Extract an ISO-8601 `modified_at` from an object-meta mapping."""
    last_modified = meta.get("last_modified")
    return last_modified.isoformat() if last_modified is not None else ""


class ObjectStoreBackend(BackendProtocol):
    """Storage-only deepagents backend for one object-store artifact area."""

    def __init__(
        self,
        area: str,
        store_factory: Callable[[], ObjectStore] = get_store,
    ) -> None:
        """Bind the backend to `area`, resolving the store lazily per call.

        Args:
            area: Artifact area name (first segment of the virtual prefix,
                e.g. `"upload"` for the `/upload/` route).
            store_factory: Zero-arg callable returning the object store —
                defaults to the env-configured client; tests inject a
                `MemoryStore`.

        """
        self._area = area
        self._store_factory = store_factory

    def _key(self, path: str) -> str | None:
        """Map a composite-stripped path to its object key (None if unsafe)."""
        return key_for_area(self._area, path)

    def _area_files(self) -> list[dict[str, Any]]:
        """List all object metadata in this area, with area-relative paths.

        Each returned mapping gains a `"vpath"` entry: the stripped-side
        virtual path (`/sub/file.pdf`) the composite layer expects back.
        """
        base = area_key_prefix(self._area)
        metas = list_meta(self._store_factory(), f"{base}/")
        for meta in metas:
            meta["vpath"] = "/" + str(meta["path"])[len(base) + 1 :]
        return metas

    def ls(self, path: str) -> LsResult:
        """List files and immediate subdirectories under `path`."""
        norm_dir = path if path.endswith("/") else path + "/"
        try:
            metas = self._area_files()
        except _STORE_ERRORS as e:
            return LsResult(error=f"Error listing {path}: {e}")

        infos: list[FileInfo] = []
        subdirs: set[str] = set()
        for meta in metas:
            vpath = meta["vpath"]
            if not vpath.startswith(norm_dir):
                continue
            rest = vpath[len(norm_dir) :]
            if "/" in rest:
                subdirs.add(norm_dir + rest.split("/")[0] + "/")
                continue
            infos.append(
                FileInfo(
                    path=vpath,
                    is_dir=False,
                    size=int(meta.get("size", 0)),
                    modified_at=_iso_modified(meta),
                ),
            )
        infos.extend(FileInfo(path=d, is_dir=True, size=0, modified_at="") for d in sorted(subdirs))
        infos.sort(key=lambda info: info.get("path", ""))
        return LsResult(entries=infos)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read a file: text is line-sliced; binaries return base64 FileData."""
        key = self._key(file_path)
        try:
            data = get_bytes(self._store_factory(), key) if key is not None else None
        except _STORE_ERRORS as e:
            return ReadResult(error=f"Error reading {file_path}: {e}")
        if data is None:
            # Unsafe paths report the same way as absent ones.
            return ReadResult(error=f"File '{file_path}' not found")

        text: str | None = None
        if _get_file_type(file_path) == "text":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                # Text-looking extension with binary content: fall through to
                # base64 rather than corrupting it.
                text = None
        if text is None:
            encoded = base64.standard_b64encode(data).decode("ascii")
            return ReadResult(file_data=FileData(content=encoded, encoding="base64"))

        sliced = slice_read_response(FileData(content=text, encoding="utf-8"), offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=FileData(content=sliced, encoding="utf-8"))

    def write(self, file_path: str, content: str) -> WriteResult:
        """Create a new text file; refuses overwrite per framework contract."""
        key = self._key(file_path)
        if key is None:
            return WriteResult(error=f"Invalid path {file_path!r}")
        try:
            store = self._store_factory()
            if head_meta(store, key) is not None:
                return WriteResult(
                    error=(
                        f"Cannot write to {file_path} because it already exists. "
                        "Read and then make an edit, or write to a new path."
                    ),
                )
            put_bytes(store, key, content.encode("utf-8"))
        except _STORE_ERRORS as e:
            return WriteResult(error=f"Error writing {file_path}: {e}")
        return WriteResult(path=file_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Replace occurrences of `old_string` in an existing text file."""
        key = self._key(file_path)
        store = self._store_factory()
        try:
            data = get_bytes(store, key) if key is not None else None
        except _STORE_ERRORS as e:
            return EditResult(error=f"Error editing {file_path}: {e}")
        if data is None or key is None:
            # Unsafe paths report the same way as absent ones.
            return EditResult(error=f"Error: File '{file_path}' not found")

        content: str | None = None
        if _get_file_type(file_path) == "text":
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                content = None
        if content is None:
            return EditResult(error=f"Error: Cannot edit binary file '{file_path}'")

        result = perform_string_replacement(content, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        try:
            put_bytes(store, key, new_content.encode("utf-8"))
        except _STORE_ERRORS as e:
            return EditResult(error=f"Error editing {file_path}: {e}")
        return EditResult(path=file_path, occurrences=int(occurrences))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Find files matching a glob pattern (listing only, no downloads)."""
        try:
            metas = self._area_files()
        except _STORE_ERRORS as e:
            return GlobResult(error=f"Error globbing {pattern!r}: {e}")

        # `_glob_search_files` only reads `modified_at` (sort order), so stub
        # the content instead of downloading every object.
        by_vpath = {meta["vpath"]: meta for meta in metas}
        files: dict[str, Any] = {
            vpath: {"content": "", "encoding": "utf-8", "modified_at": _iso_modified(meta)}
            for vpath, meta in by_vpath.items()
        }
        result = _glob_search_files(files, pattern, path)
        if result == "No files found":
            return GlobResult(matches=[])
        infos: list[FileInfo] = [
            FileInfo(
                path=p,
                is_dir=False,
                size=int(by_vpath[p].get("size", 0)) if p in by_vpath else 0,
                modified_at=_iso_modified(by_vpath[p]) if p in by_vpath else "",
            )
            for p in result.split("\n")
        ]
        return GlobResult(matches=infos)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Search text objects for a literal pattern (downloads candidates)."""
        try:
            store = self._store_factory()
            metas = self._area_files()
        except _STORE_ERRORS as e:
            return GrepResult(error=f"Error searching {pattern!r}: {e}")

        norm: str | None = None
        if path and path != "/":
            norm = path.rstrip("/")
        files: dict[str, Any] = {}
        for meta in metas:
            vpath = meta["vpath"]
            if norm is not None and vpath != norm and not vpath.startswith(norm + "/"):
                continue
            if _get_file_type(vpath) != "text" or int(meta.get("size", 0)) > _GREP_MAX_BYTES:
                continue
            try:
                data = get_bytes(store, str(meta["path"]))
                if data is None:
                    continue
                text = data.decode("utf-8")
            except _STORE_ERRORS:
                continue
            except UnicodeDecodeError:
                continue
            files[vpath] = {
                "content": text,
                "encoding": "utf-8",
                "modified_at": _iso_modified(meta),
            }
        return grep_matches_from_files(files, pattern, path, glob)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Store raw bytes per path (system publish path; overwrites allowed)."""
        responses: list[FileUploadResponse] = []
        store = self._store_factory()
        for path, content in files:
            key = self._key(path)
            if key is None:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                put_bytes(store, key, content)
            except _STORE_ERRORS as e:
                responses.append(FileUploadResponse(path=path, error=str(e)))
            else:
                responses.append(FileUploadResponse(path=path, error=None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Fetch raw bytes per path; missing keys report `file_not_found`."""
        responses: list[FileDownloadResponse] = []
        store = self._store_factory()
        for path in paths:
            key = self._key(path)
            if key is None:
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            try:
                data = get_bytes(store, key)
            except _STORE_ERRORS as e:
                responses.append(FileDownloadResponse(path=path, error=str(e)))
                continue
            if data is None:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found"),
                )
            else:
                responses.append(FileDownloadResponse(path=path, content=data, error=None))
        return responses
