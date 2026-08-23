"""Throwaway scratch directories for `rendercv` runs.

`rendercv` is a subprocess that needs a real filesystem, but none of its
working files are artifacts — the durable YAML/PDF/typ live in the object
store. The scratch dir therefore lives wherever the shell backend actually
executes commands: a host `TemporaryDirectory` for the local subprocess
backend, a directory inside the sandbox for remote sandbox backends. The
render pipeline picks the implementation through the composite default's
`open_render_scratch()` hook and falls back to the host flavor (see
`_open_render_scratch` in tools.py).
"""

from __future__ import annotations

import contextlib
import shlex
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, Self
from uuid import uuid4

from deepagents.backends.protocol import FILE_NOT_FOUND

if TYPE_CHECKING:
    from types import TracebackType

    from deepagents.backends.protocol import (
        ExecuteResponse,
        FileDownloadResponse,
        FileUploadResponse,
    )

#: Directory-name marker shared by both scratch flavors (also asserted in tests).
RENDER_DIR_PREFIX = "nextrole-render-"


class ScratchBackend(Protocol):
    """The backend surface a sandbox scratch needs (structural, so fakes qualify).

    Every `SandboxBackendProtocol` implementation satisfies this; declaring the
    minimal structural slice keeps this module a leaf and lets tests drive
    `SandboxRenderScratch` with a plain duck-typed fake.
    """

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run a shell command where the scratch lives."""
        ...

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files, partial-success per file."""
        ...

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files, partial-success per file."""
        ...


class RenderScratchError(RuntimeError):
    """A scratch-dir operation failed (transport/storage — not rendercv itself)."""


class RenderScratch(Protocol):
    """A disposable directory the render pipeline writes to and reads back from."""

    dir: str

    def __enter__(self) -> Self:
        """Materialize the directory."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Best-effort removal of the directory and everything in it."""
        ...

    def write_text(self, name: str, content: str) -> str:
        """Write `content` to `<dir>/<name>`; return the absolute path string."""
        ...

    def read_bytes(self, name: str) -> bytes | None:
        """Return `<dir>/<name>` as bytes, or None when the file doesn't exist."""
        ...


class HostRenderScratch:
    """Scratch in a host `TemporaryDirectory` (local subprocess backend)."""

    def __init__(self) -> None:
        """Create the temp dir eagerly so `dir` is a valid path from birth."""
        self._tmp = tempfile.TemporaryDirectory(prefix=RENDER_DIR_PREFIX)
        self.dir: str = self._tmp.name

    def __enter__(self) -> Self:
        """Return self — the directory already exists."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Delete the temp dir."""
        self._tmp.cleanup()

    def write_text(self, name: str, content: str) -> str:
        """Write `content` under the temp dir; return the on-disk path."""
        target = Path(self.dir) / name
        target.write_text(content, encoding="utf-8")
        return str(target)

    def read_bytes(self, name: str) -> bytes | None:
        """Read a file from the temp dir, or None when rendercv didn't produce it."""
        target = Path(self.dir) / name
        return target.read_bytes() if target.is_file() else None


class SandboxRenderScratch:
    """Scratch inside a remote sandbox, driven through the backend protocol.

    Failure semantics: transport/storage errors raise :class:`RenderScratchError`
    (the render tool reports the real failing stage), while a missing file reads
    as ``None`` — rendercv simply didn't produce it.
    """

    def __init__(self, backend: ScratchBackend, base_dir: str = "/tmp") -> None:  # noqa: S108
        """Pick a uuid-suffixed dir under `base_dir` (sandbox-internal, not host /tmp)."""
        self._backend = backend
        self.dir: str = str(PurePosixPath(base_dir) / f"{RENDER_DIR_PREFIX}{uuid4().hex[:8]}")

    def __enter__(self) -> Self:
        """Create the directory in the sandbox."""
        result = self._backend.execute(f"mkdir -p {shlex.quote(self.dir)}")
        if result.exit_code not in (0, None):
            msg = f"could not create sandbox scratch dir {self.dir}: {result.output[:500]}"
            raise RenderScratchError(msg)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Best-effort cleanup — the sandbox TTL reaps leftovers regardless."""
        with contextlib.suppress(Exception):
            self._backend.execute(f"rm -rf {shlex.quote(self.dir)}")

    def write_text(self, name: str, content: str) -> str:
        """Upload `content` into the sandbox scratch dir; return the sandbox path."""
        path = f"{self.dir}/{name}"
        responses = self._backend.upload_files([(path, content.encode("utf-8"))])
        error = responses[0].error if responses else "no response from the sandbox"
        if error is not None:
            msg = f"could not write {path} to the sandbox: {error}"
            raise RenderScratchError(msg)
        return path

    def read_bytes(self, name: str) -> bytes | None:
        """Download a scratch file; None when it doesn't exist in the sandbox."""
        path = f"{self.dir}/{name}"
        responses = self._backend.download_files([path])
        if not responses:
            msg = f"could not read {path} from the sandbox: no response"
            raise RenderScratchError(msg)
        response = responses[0]
        if response.error == FILE_NOT_FOUND:
            return None
        if response.error is not None:
            msg = f"could not read {path} from the sandbox: {response.error}"
            raise RenderScratchError(msg)
        return response.content
