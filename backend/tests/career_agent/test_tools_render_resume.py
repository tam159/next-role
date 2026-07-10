"""Unit tests for the `render_resume_pdf` hydrate→render→verify→publish pipeline.

The composite mirrors production wiring: `/tailored_resume/` routes to an
`ObjectStoreBackend` over an in-memory store (no emulator needed), while the
default is a filesystem-backed fake shell whose `execute` is scripted — no
real rendercv subprocess runs.
"""

import shlex
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from obstore.store import MemoryStore

_YAML_PATH = "/tailored_resume/tam-resume/acme-jd.yaml"
_PDF_PATH = "/tailored_resume/tam-resume/acme-jd.pdf"
_TYP_PATH = "/tailored_resume/tam-resume/acme-jd.typ"

_MINIMAL_CV_YAML = """\
# changes:
# - reordered: Role A above Role B
cv:
  name: Tam Nguyen
  email: t@example.com
design:
  theme: engineeringclassic
locale:
  language: english
"""


class _FakeShell(FilesystemBackend, SandboxBackendProtocol):
    """Filesystem backend with a scripted `execute` standing in for rendercv.

    The tool renders in a throwaway temp dir whose path arrives only via the
    command, so the fake parses it out, snapshots the hydrated render copy
    (the dir is gone by the time assertions run), and drops the outputs
    rendercv would produce.
    """

    def __init__(self, root_dir: Path) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=True)
        self.commands: list[str] = []
        self.exit_code = 0
        self.output = "rendercv ok"
        self.produce_pdf = True
        self.produce_typ = True
        self.render_dir: Path | None = None
        self.hydrated: str | None = None

    @property
    def id(self) -> str:
        """Stable identifier required by the sandbox protocol."""
        return "fake-shell"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Record the command, snapshot the render copy, drop fake outputs."""
        del timeout
        self.commands.append(command)
        yaml_file = Path(shlex.split(command)[-1])
        self.render_dir = yaml_file.parent
        if yaml_file.is_file():
            self.hydrated = yaml_file.read_text()
        if self.exit_code == 0:
            if self.produce_pdf:
                (yaml_file.parent / f"{yaml_file.stem}.pdf").write_bytes(b"%PDF-1.4 rendered")
            if self.produce_typ:
                (yaml_file.parent / f"{yaml_file.stem}.typ").write_text("#set page(margin: 1cm)")
        return ExecuteResponse(output=self.output, exit_code=self.exit_code)


@pytest.fixture
def mem_store() -> MemoryStore:
    """Fresh in-memory object store per test."""
    return MemoryStore()


@pytest.fixture
def shell(tmp_path: Path) -> _FakeShell:
    """Fake shell rooted at a sandbox dir (renders never touch this root)."""
    return _FakeShell(tmp_path)


@pytest.fixture
def backend(shell: _FakeShell, mem_store: MemoryStore) -> CompositeBackend:
    """Production-shaped composite: object-store route + shell default."""
    return CompositeBackend(
        default=shell,
        routes={
            "/tailored_resume/": ObjectStoreBackend(
                "tailored_resume",
                store_factory=lambda: mem_store,
            ),
        },
    )


def _seed_yaml(backend: CompositeBackend, content: str = _MINIMAL_CV_YAML) -> None:
    responses = backend.upload_files([(_YAML_PATH, content.encode("utf-8"))])
    assert responses[0].error is None


def _invoke(backend: CompositeBackend, yaml_path: str = _YAML_PATH) -> str:
    from backend.agents.career_agent.tools import make_render_resume_pdf

    return make_render_resume_pdf(backend).invoke({"yaml_path": yaml_path})


def test_happy_path_renders_and_publishes(tmp_path, backend, shell):
    _seed_yaml(backend)

    result = _invoke(backend)

    assert result == f"Rendered and published {_PDF_PATH}"
    # The PDF (result-visible) and the .typ intermediate (silent) were both
    # published to the object store next to the YAML.
    downloads = backend.download_files([_PDF_PATH, _TYP_PATH])
    assert downloads[0].error is None
    assert downloads[0].content == b"%PDF-1.4 rendered"
    assert downloads[1].error is None
    assert (downloads[1].content or b"").startswith(b"#set page")
    assert ".typ" not in result  # the agent should never cite the typ path
    # rendercv ran against a throwaway temp render copy, not the stored YAML
    # and not anywhere under the shell root.
    assert len(shell.commands) == 1
    assert "nextrole-render-" in shell.commands[0]
    assert shell.render_dir is not None
    assert not shell.render_dir.exists()  # temp dir cleaned up after the run
    assert not (tmp_path / "render_intermediate").exists()


def test_render_copy_carries_settings_and_stored_yaml_stays_clean(tmp_path, backend, shell):
    _seed_yaml(backend)

    _invoke(backend)

    stored = backend.read(_YAML_PATH, offset=0, limit=10**9)
    assert stored.file_data is not None
    assert "settings:" not in stored.file_data["content"]
    # The temp render copy carried the injected canonical settings block.
    hydrated = shell.hydrated or ""
    assert hydrated.count("settings:") == 1
    assert "cv:\n  name: Tam Nguyen" in hydrated
    assert shell.render_dir is not None
    assert f"output_folder: {shell.render_dir}" in hydrated
    assert f"typst_path: {shell.render_dir / 'acme-jd'}.typ" in hydrated
    assert "pdf_path: OUTPUT_FOLDER/acme-jd.pdf" in hydrated
    assert "dont_generate_markdown: true" in hydrated
    assert "dont_generate_pdf: false" in hydrated


def test_strips_legacy_settings_block_on_hydrate(tmp_path, backend, shell):
    legacy = _MINIMAL_CV_YAML + "settings:\n  render_command:\n    output_folder: /stale/path\n"
    _seed_yaml(backend, legacy)

    result = _invoke(backend)

    assert result.startswith("Rendered and published")
    hydrated = shell.hydrated or ""
    assert hydrated.count("settings:") == 1
    assert "/stale/path" not in hydrated


def test_rerender_is_idempotent(tmp_path, backend, shell):
    _seed_yaml(backend)

    first = _invoke(backend)
    second = _invoke(backend)

    assert first == second == f"Rendered and published {_PDF_PATH}"
    hydrated = shell.hydrated or ""
    assert hydrated.count("settings:") == 1


def test_render_failure_surfaces_rendercv_output(tmp_path, backend, shell):
    _seed_yaml(backend)
    shell.exit_code = 1
    shell.output = "cv.phone Input should be a valid string"

    result = _invoke(backend)

    assert result.startswith("Error (render): rendercv exited 1")
    assert "cv.phone Input should be a valid string" in result
    # Nothing was published.
    assert backend.download_files([_PDF_PATH])[0].error == "file_not_found"


def test_missing_pdf_after_render_is_a_verify_error(tmp_path, backend, shell):
    _seed_yaml(backend)
    shell.produce_pdf = False

    result = _invoke(backend)

    assert result.startswith("Error (verify):")
    assert "acme-jd.pdf was not produced" in result
    assert backend.download_files([_PDF_PATH])[0].error == "file_not_found"


def test_missing_typ_is_tolerated(tmp_path, backend, shell):
    _seed_yaml(backend)
    shell.produce_typ = False

    result = _invoke(backend)

    assert result == f"Rendered and published {_PDF_PATH}"
    downloads = backend.download_files([_PDF_PATH, _TYP_PATH])
    assert downloads[0].error is None
    assert downloads[1].error == "file_not_found"


def test_publish_failure_names_the_stage(tmp_path, shell, mem_store):
    class _PublishBoom(ObjectStoreBackend):
        def upload_files(self, files):
            return [SimpleNamespace(path=p, error="bucket offline") for p, _ in files]

    backend = CompositeBackend(
        default=shell,
        routes={
            "/tailored_resume/": _PublishBoom(
                "tailored_resume",
                store_factory=lambda: mem_store,
            ),
        },
    )
    # Seed directly into the store (the boom backend can't upload).
    ObjectStoreBackend("tailored_resume", store_factory=lambda: mem_store).upload_files(
        [("/tam-resume/acme-jd.yaml", _MINIMAL_CV_YAML.encode("utf-8"))],
    )

    result = _invoke(backend)

    assert result.startswith("Error (publish): rendered OK but saving")
    assert _PDF_PATH in result
    assert "re-renders from the stored YAML" in result


@pytest.mark.parametrize(
    "bad_path",
    [
        "/processed/x.yaml",  # outside /tailored_resume/
        "tailored_resume/r/j.yaml",  # not absolute
        "/tailored_resume/r/j.md",  # wrong extension
        "/tailored_resume/r/j",  # no extension
        "/tailored_resume/../etc/x.yaml",  # traversal
    ],
)
def test_rejects_bad_paths(backend, bad_path):
    result = _invoke(backend, bad_path)
    assert result.startswith("Error: invalid yaml_path")


def test_missing_yaml_is_a_read_error(backend):
    result = _invoke(backend, "/tailored_resume/r/missing.yaml")
    assert result.startswith("Error (read): /tailored_resume/r/missing.yaml")


def test_normalizer_repairs_colon_and_number_entries():
    """Unit contract for `_normalize_resume_yaml` — the roundtrip-killer."""
    from backend.agents.career_agent.tools import _normalize_resume_yaml

    body = textwrap.dedent(
        """\
        # changes:
        # - tailored
        cv:
          name: Alex Rivera
          sections:
            summary:
              - Plain string stays untouched.
              - Strongest where AI meets execution: reusable patterns and guidance.
            experience:
              - company: Acme
                position: Engineer
                highlights:
                  - Designed the KB around duplicate merge: entity resolution maps variants.
                  - Utilize different approaches:
                  - 42
                  - Already quoted: fine? No — this one was written unquoted too
            skills:
              - name: Agentic AI
                highlights:
                  - Build knowledge bases with the Open Knowledge Format: two-tier pipelines.
        design:
          theme: classic
        """,
    )

    fixed_body, fixes = _normalize_resume_yaml(body)

    assert fixes == 6
    tree = yaml.safe_load(fixed_body)
    summary = tree["cv"]["sections"]["summary"]
    assert summary[1] == "Strongest where AI meets execution: reusable patterns and guidance."
    highlights = tree["cv"]["sections"]["experience"][0]["highlights"]
    assert (
        highlights[0] == "Designed the KB around duplicate merge: entity resolution maps variants."
    )
    assert highlights[1] == "Utilize different approaches:"
    assert highlights[2] == "42"
    skills = tree["cv"]["sections"]["skills"][0]["highlights"]
    assert skills[0] == "Build knowledge bases with the Open Knowledge Format: two-tier pipelines."
    # Every repaired entry is now a real string.
    assert all(isinstance(x, str) for x in summary + highlights + skills)


def test_normalizer_leaves_clean_yaml_verbatim():
    from backend.agents.career_agent.tools import _normalize_resume_yaml

    body = "# changes:\n# - kept\ncv:\n  name: Alex\n  sections:\n    summary:\n      - Fine.\n"
    fixed_body, fixes = _normalize_resume_yaml(body)
    assert fixes == 0
    assert fixed_body == body  # untouched -> comments survive in the scratch copy


def test_normalizer_passes_through_unparseable_yaml():
    from backend.agents.career_agent.tools import _normalize_resume_yaml

    body = "cv:\n  sections:\n    summary:\n      - A: B: C\n"  # true parse error
    fixed_body, fixes = _normalize_resume_yaml(body)
    assert (fixed_body, fixes) == (body, 0)


def test_render_repairs_colon_entries_without_a_roundtrip(tmp_path, backend, shell):
    """Regression for the observed multi-roundtrip failure.

    The exact defect class (unquoted mid-string colons) renders first try via
    auto-repair — no LLM fix-and-retry loop.
    """
    buggy = textwrap.dedent(
        """\
        # changes:
        # - tailored
        cv:
          name: Alex Rivera
          sections:
            summary:
              - Strongest where AI meets execution: reusable patterns and guidance.
            experience:
              - company: Acme
                position: Engineer
                highlights:
                  - Designed the KB around duplicate merge: entity resolution maps variants.
        design:
          theme: classic
        locale:
          language: english
        """,
    )
    _seed_yaml(backend, buggy)

    result = _invoke(backend)

    assert result.startswith(f"Rendered and published {_PDF_PATH}")
    assert "auto-repaired 2 invalid string entries" in result
    # The temp render copy fed to rendercv is fully repaired...
    tree = yaml.safe_load(shell.hydrated or "")
    assert isinstance(tree["cv"]["sections"]["summary"][0], str)
    assert isinstance(tree["cv"]["sections"]["experience"][0]["highlights"][0], str)
    # ...while the stored YAML keeps the agent's original text.
    stored = backend.read(_YAML_PATH, offset=0, limit=10**9)
    assert "meets execution: reusable" in stored.file_data["content"]


def test_accepts_yml_extension(tmp_path, backend, shell, mem_store):
    ObjectStoreBackend("tailored_resume", store_factory=lambda: mem_store).upload_files(
        [("/tam-resume/acme-jd.yml", _MINIMAL_CV_YAML.encode("utf-8"))],
    )

    result = _invoke(backend, "/tailored_resume/tam-resume/acme-jd.yml")

    assert result == "Rendered and published /tailored_resume/tam-resume/acme-jd.pdf"
