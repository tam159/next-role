"""Unit tests for career-agent custom tools.

External services (LlamaParse, Tavily) are mocked — tests never hit the network
or burn LlamaCloud / Tavily credits.
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


def _seed_upload(tmp_path: Path, monkeypatch, filename: str, content: bytes = b"x") -> Path:
    """Anchor `CAREER_AGENT_DIR` at `tmp_path` and drop a fake upload on disk.

    `parse_document` resolves source paths under `CAREER_AGENT_DIR`, so pointing
    that at the test's tmp_path lets us seed `/upload/<name>` (and any other
    backend path) as a real on-disk file.
    """
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    upload = tmp_path / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    src = upload / filename
    src.write_bytes(content)
    return src


def test_parse_document_writes_markdown_to_backend(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "resume.pdf", b"%PDF-fake")

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# Resume\nhi")):
        result = tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/resume.pdf", "output_path": "/processed/tam-resume.md"},
        )

    assert "Saved /processed/tam-resume.md" in result
    assert "(11 chars)" in result
    written = (tmp_path / "processed" / "tam-resume.md").read_text()
    assert written == "# Resume\nhi"


def test_parse_document_passes_expected_args_to_llamacloud(tmp_path, monkeypatch, backend):
    """Lock in the LlamaParse call shape so we notice if it drifts."""
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "x.pdf")

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
        tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/x.pdf", "output_path": "/processed/x.md"},
        )

    assert captured["create"]["purpose"] == "parse"
    assert captured["create"]["file"].endswith("/upload/x.pdf")
    parse_kwargs = captured["parse"]
    assert parse_kwargs["file_id"] == "f1"
    assert parse_kwargs["tier"] == "agentic"
    assert parse_kwargs["expand"] == ["markdown_full"]
    assert parse_kwargs["processing_options"] == {"cost_optimizer": {"enable": True}}


def test_parse_document_rejects_path_traversal(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    result = tools.make_parse_document(backend).invoke(
        {"source_path": "/upload/../etc/passwd", "output_path": "/processed/x.md"},
    )
    assert result.startswith("Error: invalid source_path")
    assert "traversal" in result


def test_parse_document_rejects_relative_source_path(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    result = tools.make_parse_document(backend).invoke(
        {"source_path": "upload/x.pdf", "output_path": "/processed/x.md"},
    )
    assert result.startswith("Error: invalid source_path")
    assert "absolute" in result


def test_parse_document_rejects_non_md_output(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")
    result = tools.make_parse_document(backend).invoke(
        {"source_path": "/upload/r.pdf", "output_path": "/processed/x.txt"},
    )
    assert result.startswith("Error: invalid output_path")


def test_parse_document_handles_missing_file(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    (tmp_path / "upload").mkdir(parents=True, exist_ok=True)

    result = tools.make_parse_document(backend).invoke(
        {"source_path": "/upload/ghost.pdf", "output_path": "/processed/x.md"},
    )
    assert result.startswith("Error: file not found at /upload/ghost.pdf")


def test_parse_document_surfaces_parse_failure(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")

    def _explode(**_kw):
        msg = "api down"
        raise RuntimeError(msg)

    fake = SimpleNamespace(
        files=SimpleNamespace(create=_explode),
        parsing=SimpleNamespace(parse=lambda **_kw: None),
    )
    with patch("llama_cloud.LlamaCloud", return_value=fake):
        result = tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/r.pdf", "output_path": "/processed/x.md"},
        )

    assert result.startswith("Error processing /upload/r.pdf")
    assert "api down" in result


def test_parse_document_overwrites_existing_output_file(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")

    tool = tools.make_parse_document(backend)
    args = {"source_path": "/upload/r.pdf", "output_path": "/processed/tam.md"}

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# v1")):
        first = tool.invoke(args)
    assert "Saved /processed/tam.md" in first
    assert (tmp_path / "processed" / "tam.md").read_text() == "# v1"

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# v2 newer")):
        second = tool.invoke(args)
    assert "Saved /processed/tam.md" in second
    assert (tmp_path / "processed" / "tam.md").read_text() == "# v2 newer"


def test_parse_document_noop_when_content_identical(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")

    tool = tools.make_parse_document(backend)
    args = {"source_path": "/upload/r.pdf", "output_path": "/processed/x.md"}
    same = "# Resume\nidentical"

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning(same)):
        first = tool.invoke(args)
        second = tool.invoke(args)

    assert "Saved /processed/x.md" in first
    assert "Saved /processed/x.md" in second
    assert (tmp_path / "processed" / "x.md").read_text() == same


def test_parse_document_accepts_arbitrary_output_path(tmp_path, monkeypatch, backend):
    """Output can land outside `/processed/` — the tool is generic."""
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "spec.docx")

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("# Spec")):
        result = tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/spec.docx", "output_path": "/workspace/notes/spec.md"},
        )

    assert "Saved /workspace/notes/spec.md" in result
    assert (tmp_path / "workspace" / "notes" / "spec.md").read_text() == "# Spec"


def test_parse_document_strips_image_filename_refs(tmp_path, monkeypatch, backend):
    """LlamaParse emits `![alt](page_X_image_Y.jpg)`; we strip the `(filename)` part."""
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")

    raw = (
        "# Resume\n\n"
        "* ![check mark](page_3_image_23_v2.jpg) Use Agile methodology.\n"
        "* ![](page_4_image_1.png) Empty alt text.\n"
        "Plain link [click here](https://example.com) should NOT be stripped.\n"
    )
    expected = (
        "# Resume\n\n"
        "* [check mark] Use Agile methodology.\n"
        "* [] Empty alt text.\n"
        "Plain link [click here](https://example.com) should NOT be stripped.\n"
    )

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning(raw)):
        tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/r.pdf", "output_path": "/processed/x.md"},
        )

    assert (tmp_path / "processed" / "x.md").read_text() == expected


def test_parse_document_passes_images_to_save_empty(tmp_path, monkeypatch, backend):
    """Verify we tell LlamaParse not to save any images (no extraction cost)."""
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "x.pdf")

    captured: dict = {}

    def _capture_parse(**kwargs):
        captured["parse"] = kwargs
        return SimpleNamespace(markdown_full="ok")

    fake = SimpleNamespace(
        files=SimpleNamespace(create=lambda **_kw: SimpleNamespace(id="f1")),
        parsing=SimpleNamespace(parse=_capture_parse),
    )
    with patch("llama_cloud.LlamaCloud", return_value=fake):
        tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/x.pdf", "output_path": "/processed/x.md"},
        )

    assert captured["parse"]["output_options"]["images_to_save"] == []


def test_parse_document_handles_empty_markdown(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    _seed_upload(tmp_path, monkeypatch, "r.pdf")

    with patch("llama_cloud.LlamaCloud", return_value=_fake_llamacloud_returning("")):
        result = tools.make_parse_document(backend).invoke(
            {"source_path": "/upload/r.pdf", "output_path": "/processed/x.md"},
        )

    assert "no markdown" in result.lower()
    assert not (tmp_path / "processed" / "x.md").exists()


# ---------------------------------------------------------------------------
# extract_jd
# ---------------------------------------------------------------------------


def _fake_tavily_returning(*, title: str = "", raw_content: str = "", url: str = "https://e.com/j"):
    """Build a fake TavilyClient whose `extract` returns a single-result payload."""

    class _Fake:
        def extract(self, **_kwargs):
            return {
                "results": [
                    {"url": url, "title": title, "raw_content": raw_content, "images": []},
                ],
            }

    return _Fake()


def _fake_tavily_empty():
    class _Fake:
        def extract(self, **_kwargs):
            return {"results": []}

    return _Fake()


def test_extract_jd_writes_markdown_with_header(tmp_path, backend):
    from backend.app.career_agent import tools

    fake = _fake_tavily_returning(title="Senior AI SA", raw_content="Body here.")
    with patch("tavily.TavilyClient", return_value=fake):
        result = tools.make_extract_jd(backend).invoke(
            {"url": "https://www.amazon.jobs/en/jobs/3195366", "save_as": "amazon-senior-ai-sa-jd"},
        )

    assert "Saved /processed/amazon-senior-ai-sa-jd.md" in result
    assert "https://www.amazon.jobs/en/jobs/3195366" in result
    written = (tmp_path / "processed" / "amazon-senior-ai-sa-jd.md").read_text()
    assert written.startswith("# Senior AI SA\n\n")
    assert "_Source: https://www.amazon.jobs/en/jobs/3195366_" in written
    assert "Body here." in written


def test_extract_jd_omits_header_when_title_empty(tmp_path, backend):
    from backend.app.career_agent import tools

    fake = _fake_tavily_returning(title="", raw_content="Just body.")
    with patch("tavily.TavilyClient", return_value=fake):
        tools.make_extract_jd(backend).invoke(
            {"url": "https://example.com/jd", "save_as": "ex-jd"},
        )

    written = (tmp_path / "processed" / "ex-jd.md").read_text()
    assert not written.startswith("# ")
    assert written.startswith("_Source: https://example.com/jd_\n\n")


def test_extract_jd_strips_image_filename_refs(tmp_path, backend):
    from backend.app.career_agent import tools

    raw = "Job summary\n\n![logo](https://cdn.example.com/logo.png) Apply now."
    fake = _fake_tavily_returning(title="JD", raw_content=raw)
    with patch("tavily.TavilyClient", return_value=fake):
        tools.make_extract_jd(backend).invoke(
            {"url": "https://example.com/jd", "save_as": "ex-jd"},
        )

    written = (tmp_path / "processed" / "ex-jd.md").read_text()
    assert "[logo]" in written
    assert "https://cdn.example.com/logo.png" not in written


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "file:///etc/passwd",
        "ftp://example.com/jd",
        "https://",
    ],
)
def test_extract_jd_rejects_invalid_urls(backend, url):
    from backend.app.career_agent.tools import make_extract_jd

    result = make_extract_jd(backend).invoke({"url": url, "save_as": "x"})
    assert result.startswith("Error: invalid url")


@pytest.mark.parametrize(
    "save_as",
    [
        "",
        "../escape",
        "nested/slug",
        "with.extension",
        "back\\slash",
    ],
)
def test_extract_jd_rejects_invalid_save_as(backend, save_as):
    from backend.app.career_agent.tools import make_extract_jd

    result = make_extract_jd(backend).invoke({"url": "https://example.com/jd", "save_as": save_as})
    assert result.startswith("Error: invalid save_as")


def test_extract_jd_handles_empty_tavily_results(tmp_path, backend):
    from backend.app.career_agent import tools

    with patch("tavily.TavilyClient", return_value=_fake_tavily_empty()):
        result = tools.make_extract_jd(backend).invoke(
            {"url": "https://example.com/jd", "save_as": "ex-jd"},
        )

    assert result.startswith("Error extracting https://example.com/jd")
    assert "no results" in result.lower()
    assert not (tmp_path / "processed" / "ex-jd.md").exists()


def test_extract_jd_surfaces_tavily_exception(tmp_path, backend):
    from backend.app.career_agent import tools

    class _Boom:
        def extract(self, **_kwargs):
            msg = "tavily down"
            raise RuntimeError(msg)

    with patch("tavily.TavilyClient", return_value=_Boom()):
        result = tools.make_extract_jd(backend).invoke(
            {"url": "https://example.com/jd", "save_as": "ex-jd"},
        )

    assert result.startswith("Error extracting https://example.com/jd")
    assert "tavily down" in result
    assert not (tmp_path / "processed" / "ex-jd.md").exists()


def test_extract_jd_overwrites_existing_processed_file(tmp_path, backend):
    from backend.app.career_agent import tools

    tool = tools.make_extract_jd(backend)

    with patch(
        "tavily.TavilyClient",
        return_value=_fake_tavily_returning(title="v1", raw_content="first"),
    ):
        first = tool.invoke({"url": "https://example.com/jd", "save_as": "ex"})
    assert "Saved /processed/ex.md" in first
    assert "first" in (tmp_path / "processed" / "ex.md").read_text()

    with patch(
        "tavily.TavilyClient",
        return_value=_fake_tavily_returning(title="v2", raw_content="second"),
    ):
        second = tool.invoke({"url": "https://example.com/jd", "save_as": "ex"})
    assert "Saved /processed/ex.md" in second
    written = (tmp_path / "processed" / "ex.md").read_text()
    assert "second" in written
    assert "first" not in written


# ---------------------------------------------------------------------------
# overwrite_file
# ---------------------------------------------------------------------------


def test_overwrite_file_writes_new_file(tmp_path, backend):
    from backend.app.career_agent.tools import make_overwrite_file

    result = make_overwrite_file(backend).invoke(
        {"file_path": "/processed/fresh.md", "new_content": "# Fresh\nbody"},
    )

    assert "Saved /processed/fresh.md" in result
    assert "(12 chars)" in result
    assert (tmp_path / "processed" / "fresh.md").read_text() == "# Fresh\nbody"


def test_overwrite_file_replaces_existing_content(tmp_path, backend):
    from backend.app.career_agent.tools import make_overwrite_file

    processed = tmp_path / "processed"
    processed.mkdir(parents=True)
    (processed / "stale.md").write_text("old login page content")

    tool = make_overwrite_file(backend)
    result = tool.invoke(
        {
            "file_path": "/processed/stale.md",
            "new_content": "_Source: https://example.com/jd_\n\nReal JD body.",
        },
    )

    assert "Saved /processed/stale.md" in result
    written = (processed / "stale.md").read_text()
    assert "_Source: https://example.com/jd_" in written
    assert "Real JD body." in written
    assert "login page" not in written


def test_overwrite_file_surfaces_upsert_error(backend):
    """Pass-through: if `_upsert` returns an error, the tool reports it."""
    from backend.app.career_agent import tools
    from deepagents.backends.protocol import WriteResult

    def _fake_upsert(_backend, _path, _content):
        return WriteResult(error="disk full")

    with patch.object(tools, "_upsert", _fake_upsert):
        result = tools.make_overwrite_file(backend).invoke(
            {"file_path": "/processed/x.md", "new_content": "anything"},
        )

    assert result == "Error overwriting /processed/x.md: disk full"


# ---------------------------------------------------------------------------
# prepare_render_settings
# ---------------------------------------------------------------------------


_MINIMAL_CV_YAML = """\
# changes:
# - reordered: Role A above Role B
# - keywords added: rag, agents
cv:
  name: Tam Nguyen
  email: t@example.com
  sections:
    summary:
      - Senior AI engineer.
design:
  theme: engineeringclassic
locale:
  language: english
"""


def _seed_yaml(tmp_path: Path, monkeypatch, *, relative: str, content: str) -> Path:
    """Anchor `CAREER_AGENT_DIR` at tmp_path and drop a YAML under /tailored_resume/."""
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    target = tmp_path / relative.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_prepare_render_settings_appends_block(tmp_path, monkeypatch, backend):
    from backend.app.career_agent.tools import make_prepare_render_settings

    yaml_path = "/tailored_resume/tam-resume/aitomatic-jd.yaml"
    on_disk = _seed_yaml(tmp_path, monkeypatch, relative=yaml_path, content=_MINIMAL_CV_YAML)

    result = make_prepare_render_settings(backend).invoke({"yaml_path": yaml_path})

    # Confirmation names the prepared YAML; the agent invokes `rendercv render`
    # separately via `execute` (the shell backend translates the virtual path).
    assert f"Prepared for rendering: {yaml_path}" in result
    assert "Ready to render now." in result
    written = on_disk.read_text()
    # The body the LLM wrote is preserved verbatim.
    assert "# changes:" in written
    assert "cv:\n  name: Tam Nguyen" in written
    assert "design:\n  theme: engineeringclassic" in written
    # And the canonical settings block is appended.
    assert "\nsettings:\n" in written
    assert "current_date: today" in written
    # PDF lands next to the YAML, .typ is routed into /render_intermediate/.
    assert "pdf_path: OUTPUT_FOLDER/aitomatic-jd.pdf" in written
    expected_typ = tmp_path / "render_intermediate" / "tam-resume" / "aitomatic-jd.typ"
    assert f"typst_path: {expected_typ}" in written
    # And rendercv's intermediate-dir mkdir was honoured.
    assert expected_typ.parent.exists()
    assert "dont_generate_markdown: true" in written
    assert "dont_generate_html: true" in written
    assert "dont_generate_png: true" in written
    assert "dont_generate_typst: false" in written
    assert "dont_generate_pdf: false" in written
    # output_folder is the on-disk parent of the YAML.
    assert f"output_folder: {on_disk.parent}" in written


def test_prepare_render_settings_is_idempotent(tmp_path, monkeypatch, backend):
    """Re-running the tool replaces the existing settings block instead of stacking."""
    from backend.app.career_agent.tools import make_prepare_render_settings

    yaml_path = "/tailored_resume/tam-resume/aitomatic-jd.yaml"
    on_disk = _seed_yaml(tmp_path, monkeypatch, relative=yaml_path, content=_MINIMAL_CV_YAML)

    tool = make_prepare_render_settings(backend)
    tool.invoke({"yaml_path": yaml_path})
    first = on_disk.read_text()
    tool.invoke({"yaml_path": yaml_path})
    second = on_disk.read_text()

    assert first == second
    # Exactly one settings header in the file.
    assert second.count("\nsettings:\n") == 1


def test_prepare_render_settings_derives_paths_from_stem(tmp_path, monkeypatch, backend):
    """The injected pdf/typ filenames track the YAML stem and resume sub-dir."""
    from backend.app.career_agent.tools import make_prepare_render_settings

    yaml_path = "/tailored_resume/jane-doe-resume/google-staff-swe-jd.yaml"
    on_disk = _seed_yaml(tmp_path, monkeypatch, relative=yaml_path, content=_MINIMAL_CV_YAML)

    make_prepare_render_settings(backend).invoke({"yaml_path": yaml_path})

    written = on_disk.read_text()
    assert "pdf_path: OUTPUT_FOLDER/google-staff-swe-jd.pdf" in written
    expected_typ = tmp_path / "render_intermediate" / "jane-doe-resume" / "google-staff-swe-jd.typ"
    assert f"typst_path: {expected_typ}" in written


@pytest.mark.parametrize(
    "bad_path",
    [
        "/processed/x.yaml",  # outside /tailored_resume/
        "tailored_resume/r/j.yaml",  # not absolute
        "/tailored_resume/r/j.md",  # wrong extension
        "/tailored_resume/r/j",  # no extension
    ],
)
def test_prepare_render_settings_rejects_bad_paths(backend, bad_path):
    from backend.app.career_agent.tools import make_prepare_render_settings

    result = make_prepare_render_settings(backend).invoke({"yaml_path": bad_path})
    assert result.startswith("Error: invalid yaml_path")


def test_prepare_render_settings_rejects_missing_file(tmp_path, monkeypatch, backend):
    from backend.app.career_agent import tools

    monkeypatch.setattr(tools, "CAREER_AGENT_DIR", tmp_path)
    result = tools.make_prepare_render_settings(backend).invoke(
        {"yaml_path": "/tailored_resume/r/missing.yaml"},
    )
    assert result.startswith("Error reading /tailored_resume/r/missing.yaml")


def test_prepare_render_settings_accepts_yml_extension(tmp_path, monkeypatch, backend):
    from backend.app.career_agent.tools import make_prepare_render_settings

    yaml_path = "/tailored_resume/r/j.yml"
    on_disk = _seed_yaml(tmp_path, monkeypatch, relative=yaml_path, content=_MINIMAL_CV_YAML)

    result = make_prepare_render_settings(backend).invoke({"yaml_path": yaml_path})
    assert f"Prepared for rendering: {yaml_path}" in result
    assert "Ready to render now." in result
    assert "settings:" in on_disk.read_text()
