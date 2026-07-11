"""Unit tests for `render_battlecard_pdf`.

Validation paths always run. The two tests that actually invoke weasyprint
(`test_*_publishes_to_artifact_store`, `test_*_overwrites_existing_pdf`) are
skipped on hosts where weasyprint can't load its system libs (Pango / GLib via
cffi). They run in Docker and CI, where the libs are installed.
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from deepagents.backends import CompositeBackend, FilesystemBackend
from obstore.store import MemoryStore


def _weasyprint_importable() -> bool:
    """Return True iff weasyprint's cffi-loaded native deps resolve on this host."""
    if importlib.util.find_spec("weasyprint") is None:
        return False
    try:
        import weasyprint  # noqa: F401
    except OSError:
        return False
    return True


_render_only = pytest.mark.skipif(
    not _weasyprint_importable(),
    reason="weasyprint native deps (Pango/GLib) not installed on this host",
)


@pytest.fixture
def backend(tmp_path: Path) -> CompositeBackend:
    """Production-shaped composite for the battlecard flow.

    `/interview_battlecard/` lives in an in-memory object store; the default
    stays a tmp-dir filesystem.
    """
    store = MemoryStore()
    return CompositeBackend(
        default=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        routes={
            "/interview_battlecard/": ObjectStoreBackend(
                "interview_battlecard",
                store_factory=lambda: store,
            ),
        },
    )


def _seed_templates_and_anchor(tmp_path: Path, monkeypatch) -> None:
    """Point `CAREER_AGENT_DIR` at `tmp_path` and copy the real templates into it.

    The tool resolves the Jinja2 template + the on-disk PDF path relative to
    `CAREER_AGENT_DIR`. Pointing that at `tmp_path` keeps writes inside the
    test sandbox; copying templates over makes the template lookup succeed.
    """
    from backend.agents.career_agent import tools

    real_dir = Path(tools.__file__).parent
    shutil.copytree(real_dir / "templates", tmp_path / "templates")
    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)


_MINIMAL_JSON: dict = {
    "document_title": "Test Battle Card",
    "rounds": [
        {
            "title": "Recruiter screen",
            "subtitle": "Acme · AI Engineer",
            "introduction": ["Senior AI eng", "8 yrs", "RAG + agents"],
            "stories_ready": [
                {"title": "Shipped X", "body": "did the thing in 6 weeks"},
            ],
            "company_facts": ["Series C, $80M"],
            "questions": ["What's the on-call cadence?"],
            "watch_outs": ["Glassdoor flags launch hours"],
        },
    ],
}


def _seed_json(backend: CompositeBackend, content: str | None) -> str:
    """Upload a battlecard JSON into the object-store route.

    Pass `content=None` to skip seeding — caller wants the missing-file case.
    Returns the backend path the tool expects.
    """
    backend_path = "/interview_battlecard/tam-resume/acme-jd.json"
    if content is not None:
        responses = backend.upload_files([(backend_path, content.encode("utf-8"))])
        assert responses[0].error is None
    return backend_path


def test_render_battlecard_pdf_rejects_path_outside_interview_battlecard(
    tmp_path,
    monkeypatch,
    backend,
):
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)

    result = make_render_battlecard_pdf(backend).invoke(
        {"json_path": "/processed/something.json"},
    )
    assert result.startswith("Error: invalid json_path")
    assert "/interview_battlecard/" in result


def test_render_battlecard_pdf_rejects_non_json_extension(tmp_path, monkeypatch, backend):
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)

    result = make_render_battlecard_pdf(backend).invoke(
        {"json_path": "/interview_battlecard/r/j.md"},
    )
    assert result.startswith("Error: invalid json_path")
    assert ".json" in result


def test_render_battlecard_pdf_errors_on_missing_file(tmp_path, monkeypatch, backend):
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)
    path = _seed_json(backend, content=None)

    result = make_render_battlecard_pdf(backend).invoke({"json_path": path})
    assert result.startswith(f"Error reading {path}")


def test_render_battlecard_pdf_errors_on_malformed_json(tmp_path, monkeypatch, backend):
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)
    path = _seed_json(backend, content="{not valid json")

    result = make_render_battlecard_pdf(backend).invoke({"json_path": path})
    assert result.startswith(f"Error: {path} is not valid JSON")


@_render_only
def test_render_battlecard_pdf_publishes_to_artifact_store(tmp_path, monkeypatch, backend):
    """Happy path: seed JSON, run tool, assert the PDF lands in the object store."""
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)
    path = _seed_json(backend, content=json.dumps(_MINIMAL_JSON))

    result = make_render_battlecard_pdf(backend).invoke({"json_path": path})

    assert result == "Rendered PDF to /interview_battlecard/tam-resume/acme-jd.pdf"
    download = backend.download_files(["/interview_battlecard/tam-resume/acme-jd.pdf"])
    assert download[0].error is None
    pdf_bytes = download[0].content or b""
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000  # sanity: a real PDF, not a stub
    # Nothing binary escaped to local disk.
    assert not (tmp_path / "interview_battlecard").exists()


@_render_only
def test_render_battlecard_pdf_overwrites_existing_pdf(tmp_path, monkeypatch, backend):
    """Re-rendering replaces the previous PDF — supports user edits + re-run."""
    from backend.agents.career_agent.tools import make_render_battlecard_pdf

    _seed_templates_and_anchor(tmp_path, monkeypatch)
    path = _seed_json(backend, content=json.dumps(_MINIMAL_JSON))
    pdf_path = "/interview_battlecard/tam-resume/acme-jd.pdf"

    stale = backend.upload_files([(pdf_path, b"stale stub")])
    assert stale[0].error is None

    make_render_battlecard_pdf(backend).invoke({"json_path": path})

    fresh = backend.download_files([pdf_path])[0].content or b""
    assert fresh.startswith(b"%PDF-")
    assert fresh != b"stale stub"
