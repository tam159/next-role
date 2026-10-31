"""`render_resume_pdf` without a shell default (`SANDBOX_PROVIDER=none`).

The composite default is a plain `FilesystemBackend`, so the tool runs rendercv
as a host subprocess itself. `subprocess.run` is scripted here — no real
rendercv process runs — and stands in for rendercv by dropping its outputs.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.agents.career_agent import tools as tools_mod
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from deepagents.backends import CompositeBackend, FilesystemBackend
from obstore.store import MemoryStore

_YAML_PATH = "/tailored_resume/tam-resume/acme-jd.yaml"
_PDF_PATH = "/tailored_resume/tam-resume/acme-jd.pdf"

_MINIMAL_CV_YAML = """\
cv:
  name: Tam Nguyen
  email: t@example.com
design:
  theme: engineeringclassic
"""


class _FakeRun:
    """Scripted `subprocess.run`: records argv and env, drops fake outputs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.returncode = 0
        self.stderr = ""
        self.raises: Exception | None = None

    def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append({"argv": argv, **kwargs})
        if self.raises is not None:
            raise self.raises
        yaml_file = Path(argv[-1])
        if self.returncode == 0:
            (yaml_file.parent / f"{yaml_file.stem}.pdf").write_bytes(b"%PDF-1.4 host")
        return SimpleNamespace(
            returncode=self.returncode,
            stdout="rendercv ok\n",
            stderr=self.stderr,
        )


@pytest.fixture
def fake_run(monkeypatch) -> _FakeRun:
    fake = _FakeRun()
    monkeypatch.setattr(tools_mod.subprocess, "run", fake)
    return fake


@pytest.fixture
def backend(tmp_path: Path) -> CompositeBackend:
    """Production-shaped composite with the no-shell default."""
    store = MemoryStore()
    composite = CompositeBackend(
        default=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        routes={
            "/tailored_resume/": ObjectStoreBackend("tailored_resume", store_factory=lambda: store),
        },
    )
    responses = composite.upload_files([(_YAML_PATH, _MINIMAL_CV_YAML.encode("utf-8"))])
    assert responses[0].error is None
    return composite


def _invoke(backend: CompositeBackend) -> str:
    return tools_mod.make_render_resume_pdf(backend).invoke({"yaml_path": _YAML_PATH})


def test_renders_on_the_host_without_a_shell_backend(backend, fake_run):
    result = _invoke(backend)

    assert result == f"Rendered and published {_PDF_PATH}"
    assert backend.download_files([_PDF_PATH])[0].content == b"%PDF-1.4 host"
    (call,) = fake_run.calls
    assert call["argv"][1:4] == ["-m", "rendercv", "render"]
    assert "nextrole-render-" in call["argv"][-1]


def test_the_host_render_gets_no_secrets(backend, fake_run, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")

    _invoke(backend)

    (call,) = fake_run.calls
    assert set(call["env"]) == {"PATH"}


def test_a_failed_host_render_surfaces_rendercv_output(backend, fake_run):
    fake_run.returncode = 2
    fake_run.stderr = "cv.phone Input should be a valid string"

    result = _invoke(backend)

    assert result.startswith("Error (render): rendercv exited 2")
    assert "cv.phone Input should be a valid string" in result
    assert backend.download_files([_PDF_PATH])[0].error == "file_not_found"


def test_a_hung_host_render_times_out_in_band(backend, fake_run):
    fake_run.raises = subprocess.TimeoutExpired(cmd="rendercv", timeout=60)

    result = _invoke(backend)

    assert result == "Error (render): rendercv timed out after 60s"
