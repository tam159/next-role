"""`render_resume_pdf` in sandbox mode: the scratch dir lives inside the sandbox.

Mirrors test_tools_render_resume.py's composite shape, but the default is a
`BaseSandbox`-style fake exposing `open_render_scratch()` — so the pipeline
must upload the render copy into the sandbox, execute rendercv there, download
the outputs, and publish, never touching a host temp dir.
"""

import shlex

import pytest
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.render_scratch import RENDER_DIR_PREFIX, SandboxRenderScratch
from backend.agents.career_agent.tools import make_render_resume_pdf
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from obstore.store import MemoryStore

_YAML_PATH = "/tailored_resume/tam-resume/acme-jd.yaml"
_PDF_PATH = "/tailored_resume/tam-resume/acme-jd.pdf"
_TYP_PATH = "/tailored_resume/tam-resume/acme-jd.typ"

_MINIMAL_CV_YAML = """\
cv:
  name: Tam Nguyen
  email: t@example.com
design:
  theme: engineeringclassic
locale:
  language: english
"""


class _FakeRemoteShell(BaseSandbox):
    """In-memory 'sandbox': dict filesystem + scripted rendercv."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.exit_code = 0
        self.produce_pdf = True
        self.upload_error: str | None = None

    @property
    def id(self) -> str:
        """Stable identifier required by the sandbox protocol."""
        return "fake-remote"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Record commands; script rendercv against the dict filesystem."""
        del timeout
        self.commands.append(command)
        tokens = shlex.split(command)
        if tokens[:2] != ["rendercv", "render"]:
            return ExecuteResponse(output="", exit_code=0)  # mkdir -p / rm -rf
        if self.exit_code != 0:
            return ExecuteResponse(output="rendercv blew up", exit_code=self.exit_code)
        yaml_path = tokens[-1]
        prefix, _, name = yaml_path.rpartition("/")
        stem = name.rsplit(".", 1)[0]
        if self.produce_pdf:
            self.files[f"{prefix}/{stem}.pdf"] = b"%PDF-1.4 sandbox-rendered"
        self.files[f"{prefix}/{stem}.typ"] = b"#set page(margin: 1cm)"
        return ExecuteResponse(output="rendercv ok", exit_code=0)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Store bytes in the dict fs (or fail every file with `upload_error`)."""
        if self.upload_error is not None:
            return [FileUploadResponse(path=path, error=self.upload_error) for path, _ in files]
        for path, content in files:
            self.files[path] = content
        return [FileUploadResponse(path=path) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Serve bytes from the dict fs; unknown paths are file_not_found."""
        return [
            FileDownloadResponse(path=path, content=self.files[path])
            if path in self.files
            else FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
            for path in paths
        ]

    def open_render_scratch(self) -> SandboxRenderScratch:
        """The hook `_open_render_scratch` dispatches on: scratch in this sandbox."""
        return SandboxRenderScratch(self)


@pytest.fixture
def shell() -> _FakeRemoteShell:
    return _FakeRemoteShell()


@pytest.fixture
def backend(shell: _FakeRemoteShell) -> CompositeBackend:
    """Production-shaped composite with a sandbox default (SANDBOX_PROVIDER=e2b)."""
    mem_store = MemoryStore()
    return CompositeBackend(
        default=shell,
        routes={
            "/tailored_resume/": ObjectStoreBackend(
                "tailored_resume",
                store_factory=lambda: mem_store,
            ),
        },
    )


def _seed_and_invoke(backend: CompositeBackend) -> str:
    responses = backend.upload_files([(_YAML_PATH, _MINIMAL_CV_YAML.encode("utf-8"))])
    assert responses[0].error is None
    return make_render_resume_pdf(backend).invoke({"yaml_path": _YAML_PATH})


def test_sandbox_mode_renders_inside_the_sandbox_and_publishes(backend, shell):
    result = _seed_and_invoke(backend)

    assert result == f"Rendered and published {_PDF_PATH}"
    downloads = backend.download_files([_PDF_PATH, _TYP_PATH])
    assert downloads[0].content == b"%PDF-1.4 sandbox-rendered"
    assert (downloads[1].content or b"").startswith(b"#set page")

    # The whole scratch lifecycle happened inside the sandbox, in order.
    mkdir, render, cleanup = shell.commands
    assert mkdir.startswith("mkdir -p ")
    assert RENDER_DIR_PREFIX in mkdir
    assert render.startswith("rendercv render ")
    assert RENDER_DIR_PREFIX in render
    assert cleanup.startswith("rm -rf ")
    assert RENDER_DIR_PREFIX in cleanup

    # The hydrated render copy was uploaded into the sandbox with a settings
    # block pinning outputs to the sandbox scratch dir (never persisted).
    scratch_yaml = shlex.split(render)[-1]
    hydrated = shell.files[scratch_yaml].decode("utf-8")
    assert f"output_folder: {scratch_yaml.rsplit('/', 1)[0]}" in hydrated
    stored = backend.download_files([_YAML_PATH])[0].content or b""
    assert b"settings:" not in stored


def test_sandbox_mode_render_failure_still_cleans_up(backend, shell):
    shell.exit_code = 3

    result = _seed_and_invoke(backend)

    assert result.startswith("Error (render): rendercv exited 3")
    assert "rendercv blew up" in result
    assert shell.commands[-1].startswith("rm -rf ")


def test_sandbox_mode_missing_pdf_is_a_verify_error(backend, shell):
    shell.produce_pdf = False

    result = _seed_and_invoke(backend)

    assert result.startswith("Error (verify):")


def test_sandbox_mode_upload_failure_is_a_scratch_error(backend, shell):
    shell.upload_error = "disk full"

    result = _seed_and_invoke(backend)

    assert result.startswith("Error (scratch):")
    assert "disk full" in result
