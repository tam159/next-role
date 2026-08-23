"""Unit tests for the host / sandbox render-scratch implementations."""

import shlex
from pathlib import Path

import pytest
from backend.agents.career_agent.render_scratch import (
    RENDER_DIR_PREFIX,
    HostRenderScratch,
    RenderScratchError,
    SandboxRenderScratch,
)
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)


class _FakeSandboxFs:
    """Minimal duck of the sandbox-backend surface SandboxRenderScratch uses."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.mkdir_exit = 0
        self.upload_error: str | None = None
        self.download_error: str | None = None
        self.exit_raises = False

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Record commands; mkdir honors `mkdir_exit`; rm can be made to raise."""
        del timeout
        self.commands.append(command)
        if command.startswith("mkdir"):
            return ExecuteResponse(
                output="mkdir failed" * self.mkdir_exit,
                exit_code=self.mkdir_exit,
            )
        if command.startswith("rm") and self.exit_raises:
            msg = "transport down"
            raise ConnectionError(msg)
        return ExecuteResponse(output="", exit_code=0)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Store bytes in the dict fs, or fail every file with `upload_error`."""
        responses = []
        for path, content in files:
            if self.upload_error is not None:
                responses.append(FileUploadResponse(path=path, error=self.upload_error))
                continue
            self.files[path] = content
            responses.append(FileUploadResponse(path=path))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Serve bytes from the dict fs; unknown paths are file_not_found."""
        responses = []
        for path in paths:
            if self.download_error is not None:
                responses.append(FileDownloadResponse(path=path, error=self.download_error))
            elif path in self.files:
                responses.append(FileDownloadResponse(path=path, content=self.files[path]))
            else:
                responses.append(FileDownloadResponse(path=path, error=FILE_NOT_FOUND))
        return responses


# --- HostRenderScratch -------------------------------------------------------


def test_host_scratch_roundtrip_and_cleanup():
    with HostRenderScratch() as scratch:
        assert RENDER_DIR_PREFIX in scratch.dir
        path = scratch.write_text("cv.yaml", "cv: {}\n")
        assert path == str(Path(scratch.dir) / "cv.yaml")
        assert scratch.read_bytes("cv.yaml") == b"cv: {}\n"
        assert scratch.read_bytes("missing.pdf") is None
        kept_dir = scratch.dir
    assert not Path(kept_dir).exists()


# --- SandboxRenderScratch ----------------------------------------------------


def test_sandbox_scratch_creates_dir_writes_reads_and_cleans_up():
    fs = _FakeSandboxFs()
    with SandboxRenderScratch(fs) as scratch:
        assert scratch.dir.startswith(f"/tmp/{RENDER_DIR_PREFIX}")  # noqa: S108 - sandbox path
        assert fs.commands[0] == f"mkdir -p {shlex.quote(scratch.dir)}"
        path = scratch.write_text("cv.yaml", "cv: {}\n")
        assert path == f"{scratch.dir}/cv.yaml"
        assert scratch.read_bytes("cv.yaml") == b"cv: {}\n"
        assert scratch.read_bytes("missing.pdf") is None
    assert fs.commands[-1] == f"rm -rf {shlex.quote(scratch.dir)}"


def test_sandbox_scratch_mkdir_failure_raises():
    fs = _FakeSandboxFs()
    fs.mkdir_exit = 1
    with pytest.raises(RenderScratchError, match="could not create"), SandboxRenderScratch(fs):
        pytest.fail("body must not run when the scratch dir cannot be created")


def test_sandbox_scratch_upload_failure_raises():
    fs = _FakeSandboxFs()
    fs.upload_error = "disk full"
    with SandboxRenderScratch(fs) as scratch, pytest.raises(RenderScratchError, match="disk full"):
        scratch.write_text("cv.yaml", "cv: {}\n")


def test_sandbox_scratch_download_transport_error_raises():
    fs = _FakeSandboxFs()
    fs.download_error = "ConnectionError: boom"
    with SandboxRenderScratch(fs) as scratch, pytest.raises(RenderScratchError, match="boom"):
        scratch.read_bytes("cv.pdf")


def test_sandbox_scratch_cleanup_swallows_transport_errors():
    fs = _FakeSandboxFs()
    fs.exit_raises = True
    with SandboxRenderScratch(fs):
        pass  # __exit__'s rm -rf raises inside the backend; the scratch must swallow it
