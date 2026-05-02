"""Unit tests for career-agent custom tools (`list_files`, `parse_document`).

LlamaParse is mocked — tests never hit the network or burn LlamaCloud credits.
"""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend


@pytest.fixture
def backend(tmp_path: Path) -> CompositeBackend:
    """A composite backed by a tmp dir, mirroring the production virtual_mode setup."""
    return CompositeBackend(
        default=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        routes={},
    )


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_sorts_by_mtime_desc(tmp_path, backend):
    from backend.app.career_agent.tools import make_list_files

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "older.pdf").write_text("a")
    time.sleep(0.02)
    (upload / "newer.pdf").write_text("b")
    time.sleep(0.02)
    (upload / "newest.pdf").write_text("c")

    result = make_list_files(backend).invoke({"path": "/upload/"})

    paths = [r["path"] for r in result]
    assert paths == [
        "/upload/newest.pdf",
        "/upload/newer.pdf",
        "/upload/older.pdf",
    ]
    assert all("modified_at" in r for r in result)
    assert all("size" in r for r in result)


def test_list_files_returns_empty_for_nonexistent_dir(tmp_path, backend):
    """FilesystemBackend treats missing dirs as empty — that's fine for our usage."""
    result = make_list_files_then_invoke(backend, "/upload/")
    assert result == []


def test_list_files_handles_backend_exception(tmp_path):
    """If the backend raises, we surface it as a single-element error list."""
    from backend.app.career_agent.tools import make_list_files

    class _Boom:
        def ls(self, path: str):
            msg = "kaboom"
            raise RuntimeError(msg)

    result = make_list_files(_Boom()).invoke({"path": "/x/"})  # type: ignore # noqa: PGH003
    assert result == [{"error": "ls failed: kaboom"}]


def make_list_files_then_invoke(backend, path: str):
    from backend.app.career_agent.tools import make_list_files

    return make_list_files(backend).invoke({"path": path})


# ---------------------------------------------------------------------------
# parse_document
# ---------------------------------------------------------------------------


def _fake_llamacloud_returning(markdown: str):
    """Build a SimpleNamespace that mimics the LlamaCloud sync client."""
    return SimpleNamespace(
        files=SimpleNamespace(create=lambda **_kw: SimpleNamespace(id="file-123")),
        parsing=SimpleNamespace(parse=lambda **_kw: SimpleNamespace(markdown_full=markdown)),
    )


def test_parse_document_writes_markdown_to_backend(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "resume.pdf").write_bytes(b"%PDF-fake")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# Resume\nhi")):
        result = tools.make_parse_document(backend).invoke(
            {"filename": "resume.pdf", "save_as": "tam-resume"},
        )

    assert "Saved /processed/tam-resume.md" in result
    assert "(11 chars)" in result
    written = (tmp_path / "processed" / "tam-resume.md").read_text()
    assert written == "# Resume\nhi"


def test_parse_document_passes_expected_args_to_llamacloud(tmp_path, monkeypatch, backend):
    """Lock in the LlamaParse call shape so we notice if it drifts."""
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "x.pdf").write_bytes(b"x")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    captured: dict = {}

    def _capture_create(*, file: str, purpose: str):
        captured["create"] = {"file": file, "purpose": purpose}
        return SimpleNamespace(id="f1")

    def _capture_parse(**kwargs):
        captured["parse"] = kwargs
        return SimpleNamespace(markdown_full="ok")

    fake_client = SimpleNamespace(
        files=SimpleNamespace(create=_capture_create),
        parsing=SimpleNamespace(parse=_capture_parse),
    )
    with patch("llama_cloud.LlamaCloud", return_value=fake_client):
        tools.make_parse_document(backend).invoke({"filename": "x.pdf", "save_as": "x"})

    assert captured["create"]["purpose"] == "parse"
    assert captured["create"]["file"].endswith("/upload/x.pdf")
    parse_kwargs = captured["parse"]
    assert parse_kwargs["file_id"] == "f1"
    assert parse_kwargs["tier"] == "agentic"
    assert parse_kwargs["expand"] == ["markdown_full"]
    assert parse_kwargs["processing_options"] == {"cost_optimizer": {"enable": True}}


def test_parse_document_rejects_path_traversal(backend):
    from backend.app.career_agent.tools import make_parse_document

    result = make_parse_document(backend).invoke({"filename": "../etc/passwd", "save_as": "x"})
    assert result.startswith("Error: file not found")


def test_parse_document_handles_missing_file(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    result = tools.make_parse_document(backend).invoke({"filename": "ghost.pdf", "save_as": "x"})
    assert result.startswith("Error: file not found")


def test_parse_document_surfaces_parse_failure(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "r.pdf").write_bytes(b"x")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    def _explode(**_kw):
        msg = "api down"
        raise RuntimeError(msg)

    fake = SimpleNamespace(
        files=SimpleNamespace(create=_explode),
        parsing=SimpleNamespace(parse=lambda **_kw: None),
    )
    with patch("llama_cloud.LlamaCloud", return_value=fake):
        result = tools.make_parse_document(backend).invoke({"filename": "r.pdf", "save_as": "x"})

    assert result.startswith("Error processing r.pdf")
    assert "api down" in result


def test_parse_document_overwrites_existing_processed_file(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "r.pdf").write_bytes(b"x")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    tool = tools.make_parse_document(backend)

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# v1")):
        first = tool.invoke({"filename": "r.pdf", "save_as": "tam"})
    assert "Saved /processed/tam.md" in first
    assert (tmp_path / "processed" / "tam.md").read_text() == "# v1"

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# v2 newer")):
        second = tool.invoke({"filename": "r.pdf", "save_as": "tam"})
    assert "Saved /processed/tam.md" in second
    assert (tmp_path / "processed" / "tam.md").read_text() == "# v2 newer"


def test_parse_document_noop_when_content_identical(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "r.pdf").write_bytes(b"x")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    tool = tools.make_parse_document(backend)
    same = "# Resume\nidentical"

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning(same)):
        first = tool.invoke({"filename": "r.pdf", "save_as": "x"})
        second = tool.invoke({"filename": "r.pdf", "save_as": "x"})

    assert "Saved /processed/x.md" in first
    assert "Saved /processed/x.md" in second
    assert (tmp_path / "processed" / "x.md").read_text() == same


def test_parse_document_handles_empty_markdown(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    upload = tmp_path / "upload"
    upload.mkdir(parents=True)
    (upload / "r.pdf").write_bytes(b"x")
    monkeypatch.setattr(tools, "UPLOAD_DIR", upload)

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("")):
        result = tools.make_parse_document(backend).invoke({"filename": "r.pdf", "save_as": "x"})

    assert "no markdown" in result.lower()
    assert not (tmp_path / "processed" / "x.md").exists()
