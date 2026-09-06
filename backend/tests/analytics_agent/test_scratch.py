"""Per-thread scratch space for the data hand-off to Python.

Two properties matter. Scratch must land where the shell actually runs, and it
must never travel through the CompositeBackend — its default route is jailed
under the agent package, so a "/tmp/..." write there would end up inside the
bind-mounted repository.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.agents.analytics_agent.scratch import (
    SANDBOX_ROOT,
    HostScratch,
    SandboxScratch,
    ScratchError,
    open_scratch,
    safe_segment,
)
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend


class FakeSandbox:
    """A duck-typed stand-in for a remote sandbox backend."""

    def __init__(self, *, exit_code: int = 0, upload_error: str | None = None):
        """Record commands and uploads, replaying canned outcomes."""
        self.commands: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []
        self._exit_code = exit_code
        self._upload_error = upload_error
        self.output = ""

    def execute(self, command, *, timeout=None):
        """Record the command and replay the canned exit code."""
        self.commands.append(command)
        return SimpleNamespace(exit_code=self._exit_code, output=self.output)

    def upload_files(self, files):
        """Record the uploads and replay the canned error."""
        self.uploads.extend(files)
        return [SimpleNamespace(error=self._upload_error) for _ in files]


# ---------------------------------------------------------------------------
# name safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["thread-1", "abc_123", "a.csv"])
def test_safe_segments_pass_through(raw):
    assert safe_segment(raw, fallback="adhoc") == raw


@pytest.mark.parametrize("raw", ["../escape", "a/b", "", None, ".hidden", "x" * 100])
def test_unsafe_segments_fall_back(raw):
    assert safe_segment(raw, fallback="adhoc") == "adhoc"


# ---------------------------------------------------------------------------
# host flavour
# ---------------------------------------------------------------------------


def test_host_scratch_writes_a_real_readable_file(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    scratch = HostScratch("thread-1")

    path = scratch.write_text("rows.csv", "day,runs\n2026-09-01,3\n")

    assert Path(path).read_text() == "day,runs\n2026-09-01,3\n"
    read_back = scratch.read_text(path)
    assert read_back is not None
    assert read_back.startswith("day,runs")


def test_host_scratch_is_namespaced_per_thread(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    assert HostScratch("a").dir != HostScratch("b").dir


def test_host_scratch_read_of_a_missing_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    assert HostScratch("t").read_text(str(tmp_path / "nope.csv")) is None


# ---------------------------------------------------------------------------
# sandbox flavour
# ---------------------------------------------------------------------------


def test_sandbox_scratch_creates_its_directory_then_uploads():
    sandbox = FakeSandbox()

    path = SandboxScratch(sandbox, "thread-1").write_text("rows.csv", "a,b\n")

    assert sandbox.commands[0].startswith("mkdir -p ")
    assert path == f"{SANDBOX_ROOT}/thread-1/rows.csv"
    assert sandbox.uploads == [(path, b"a,b\n")]


def test_sandbox_scratch_reports_a_failed_mkdir():
    with pytest.raises(ScratchError, match="scratch dir"):
        SandboxScratch(FakeSandbox(exit_code=1), "t")


def test_sandbox_scratch_reports_a_failed_upload():
    scratch = SandboxScratch(FakeSandbox(upload_error="disk full"), "t")
    with pytest.raises(ScratchError, match="disk full"):
        scratch.write_text("rows.csv", "a")


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def test_the_local_shell_backend_gets_host_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    backend = VirtualPathShellBackend(root_dir=str(tmp_path), virtual_mode=True)

    assert isinstance(open_scratch(backend, "t"), HostScratch)


def test_a_remote_sandbox_backend_gets_sandbox_scratch():
    assert isinstance(open_scratch(FakeSandbox(), "t"), SandboxScratch)


def test_scratch_paths_survive_shell_path_translation(tmp_path):
    """The shell backend must not rewrite a real /tmp path into its jail.

    `VirtualPathShellBackend` rewrites virtual paths to on-disk ones; if it also
    caught the scratch path, `execute` would read a file inside the repository
    instead of the exported rows.
    """
    backend = VirtualPathShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    command = f"python {SANDBOX_ROOT}/t-1/plot.py {SANDBOX_ROOT}/t-1/rows.csv"

    assert backend._translate(command) == command  # noqa: SLF001 — the behaviour under test
