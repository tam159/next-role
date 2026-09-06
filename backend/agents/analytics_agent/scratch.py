"""Per-thread scratch space for the Python fallback.

`run_sql(save_as=...)` writes a full result here and `execute` reads it back, so
the model never has to carry a dataset through its own context. The directory
must live where the shell actually runs, which differs by backend:

* the local shell backend runs subprocesses on this host, so scratch is a real
  host directory;
* a remote sandbox runs commands inside the microVM, so scratch is created and
  populated through the backend's own file transport.

It deliberately does **not** go through the CompositeBackend's `write`. Those
routes are virtual paths, and the default route is jailed under the agent
package directory (`virtual_mode=True`), so a "/tmp/..." write there would land
inside the bind-mounted repository. `VirtualPathShellBackend._translate` leaves
real absolute paths alone precisely so this hand-off works.

Same split as `career_agent/render_scratch.py`, different lifetime: renders use
a directory that dies with the call, while these files outlive the tool call
that made them so a later `execute` can read them.
"""

from __future__ import annotations

import re
import shlex
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

from deepagents.backends import LocalShellBackend

if TYPE_CHECKING:
    from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse

#: Parent of every thread's scratch directory. Under the system temp dir on the
#: host; a fixed path inside a sandbox, whose filesystem is disposable anyway.
ROOT_NAME = "nextrole-analytics"
SANDBOX_ROOT = f"/tmp/{ROOT_NAME}"  # noqa: S108 — sandbox-internal, not host /tmp

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ScratchError(RuntimeError):
    """A scratch directory could not be created or written."""


class _Transport(Protocol):
    """The slice of a sandbox backend that scratch needs (structural)."""

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run a shell command where the scratch lives."""
        ...

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files, reporting success per file."""
        ...


def safe_segment(raw: str | None, *, fallback: str) -> str:
    """Return `raw` if it is a safe single path segment, else `fallback`.

    Thread ids and file names reach here from the run config and from the
    model, so neither is trusted to stay inside the scratch directory.
    """
    candidate = (raw or "").strip()
    return candidate if _SAFE_SEGMENT.match(candidate) else fallback


class HostScratch:
    """Scratch as a real directory on this host (local shell backend)."""

    def __init__(self, thread_id: str) -> None:
        """Create the thread's directory under the system temp dir."""
        self.dir = str(Path(tempfile.gettempdir()) / ROOT_NAME / thread_id)
        Path(self.dir).mkdir(parents=True, exist_ok=True)

    def write_text(self, name: str, content: str) -> str:
        """Write `content` to `<dir>/<name>`; return the absolute path."""
        target = Path(self.dir) / name
        target.write_text(content, encoding="utf-8")
        return str(target)

    def read_text(self, path: str) -> str | None:
        """Read a scratch file, or `None` when it does not exist."""
        target = Path(path)
        return target.read_text(encoding="utf-8") if target.is_file() else None


class SandboxScratch:
    """Scratch inside a remote sandbox, driven through the backend protocol."""

    def __init__(self, backend: _Transport, thread_id: str) -> None:
        """Create the thread's directory inside the sandbox."""
        self._backend = backend
        self.dir = str(PurePosixPath(SANDBOX_ROOT) / thread_id)
        result = backend.execute(f"mkdir -p {shlex.quote(self.dir)}")
        if result.exit_code not in (0, None):
            msg = f"could not create the sandbox scratch dir {self.dir}: {result.output[:300]}"
            raise ScratchError(msg)

    def write_text(self, name: str, content: str) -> str:
        """Upload `content` into the sandbox scratch dir; return its path."""
        path = f"{self.dir}/{name}"
        responses = self._backend.upload_files([(path, content.encode("utf-8"))])
        error = responses[0].error if responses else "no response from the sandbox"
        if error is not None:
            msg = f"could not write {path} to the sandbox: {error}"
            raise ScratchError(msg)
        return path

    def read_text(self, path: str) -> str | None:
        """Read a scratch file back out of the sandbox, or `None` when absent."""
        result = self._backend.execute(f"cat {shlex.quote(path)}")
        if result.exit_code not in (0, None):
            return None
        return result.output


def open_scratch(default_backend: object, thread_id: str) -> HostScratch | SandboxScratch:
    """Open the thread's scratch space wherever the shell backend runs commands.

    `LocalShellBackend` (and the repo's `VirtualPathShellBackend` subclass) runs
    subprocesses on this host, so scratch is a host directory those processes
    can already see. Every other sandbox backend runs commands elsewhere, so the
    directory has to be created and filled through its own transport.
    """
    if isinstance(default_backend, LocalShellBackend):
        return HostScratch(thread_id)
    return SandboxScratch(default_backend, thread_id)  # ty: ignore[invalid-argument-type]
