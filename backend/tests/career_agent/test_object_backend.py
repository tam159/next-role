"""Contract tests for `ObjectStoreBackend` against an in-memory object store.

These lock in the deepagents `BackendProtocol` conventions the rest of the
system depends on: the overwrite-refusal error literal (`_upsert`'s
write→edit fallback), base64 `FileData` for binary reads (multimodal blocks),
in-band errors, and prefix-stripped path semantics under `CompositeBackend`.
"""

import base64

import pytest
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.object_storage import KEY_SCOPE
from deepagents.backends.protocol import SandboxBackendProtocol
from obstore.store import MemoryStore


@pytest.fixture
def mem_store() -> MemoryStore:
    """Fresh in-memory object store per test."""
    return MemoryStore()


@pytest.fixture
def backend(mem_store: MemoryStore) -> ObjectStoreBackend:
    """An `upload`-area backend over the in-memory store."""
    return ObjectStoreBackend("upload", store_factory=lambda: mem_store)


def test_is_not_a_sandbox_backend(backend):
    """`execute` must keep dispatching to the composite default (the shell)."""
    assert not isinstance(backend, SandboxBackendProtocol)


def test_keys_carry_the_scoped_area_prefix(backend, mem_store):
    backend.write("/cv.yaml", "name: Tam")

    keys = [str(m["path"]) for m in mem_store.list().collect()]
    assert keys == [f"{KEY_SCOPE}/upload/cv.yaml"]


# ---------------------------------------------------------------------------
# write / edit (the `_upsert` contract)
# ---------------------------------------------------------------------------


def test_write_then_refuse_overwrite_then_edit(backend):
    first = backend.write("/cv.yaml", "name: Tam")
    assert first.error is None
    assert first.path == "/cv.yaml"

    second = backend.write("/cv.yaml", "other")
    assert second.error is not None
    # Exact framework literal — tools._upsert falls back to edit() on it.
    assert "already exists. Read and then make an edit" in second.error

    edited = backend.edit("/cv.yaml", "Tam", "Tam NGUYEN")
    assert edited.error is None
    assert edited.occurrences == 1

    read = backend.read("/cv.yaml")
    assert read.file_data is not None
    assert read.file_data["content"] == "name: Tam NGUYEN"


def test_write_rejects_unsafe_path(backend):
    assert backend.write("/../escape.txt", "x").error is not None


def test_edit_missing_file_uses_framework_literal(backend):
    result = backend.edit("/ghost.yaml", "a", "b")
    assert result.error == "Error: File '/ghost.yaml' not found"


def test_edit_refuses_binary(backend):
    backend.upload_files([("/cv.pdf", b"%PDF-1.4")])
    result = backend.edit("/cv.pdf", "a", "b")
    assert result.error == "Error: Cannot edit binary file '/cv.pdf'"


def test_edit_surfaces_replacement_errors_in_band(backend):
    backend.write("/cv.yaml", "aa aa")
    result = backend.edit("/cv.yaml", "aa", "bb")  # ambiguous without replace_all
    assert result.error is not None

    replaced = backend.edit("/cv.yaml", "aa", "bb", replace_all=True)
    assert replaced.error is None
    assert replaced.occurrences == 2


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_missing_uses_framework_literal(backend):
    assert backend.read("/ghost.txt").error == "File '/ghost.txt' not found"


def test_read_binary_returns_base64_file_data(backend):
    pdf = b"%PDF-1.4 fake"
    backend.upload_files([("/cv.pdf", pdf)])

    result = backend.read("/cv.pdf")

    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["encoding"] == "base64"
    assert base64.standard_b64decode(result.file_data["content"]) == pdf


def test_read_text_honors_offset_and_limit(backend):
    backend.write("/notes.md", "l1\nl2\nl3\nl4\n")

    window = backend.read("/notes.md", offset=1, limit=2)
    assert window.file_data is not None
    assert window.file_data["content"] == "l2\nl3\n"

    past_end = backend.read("/notes.md", offset=99, limit=2)
    assert past_end.error is not None


def test_read_undecodable_text_extension_falls_back_to_base64(backend):
    backend.upload_files([("/weird.txt", b"\xff\xfe\x00binary")])

    result = backend.read("/weird.txt")

    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["encoding"] == "base64"


# ---------------------------------------------------------------------------
# ls / glob / grep
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(backend: ObjectStoreBackend) -> ObjectStoreBackend:
    """Backend with a small tree: two files at root, one nested."""
    backend.write("/cv.yaml", "name: Tam")
    backend.upload_files([("/cv.pdf", b"%PDF-1.4")])
    backend.write("/sub/notes.md", "hello world")
    return backend


def test_ls_lists_files_and_synthesizes_subdirs(seeded):
    result = seeded.ls("/")

    assert result.error is None
    entries = {e["path"]: e for e in result.entries or []}
    assert set(entries) == {"/cv.pdf", "/cv.yaml", "/sub/"}
    assert entries["/sub/"]["is_dir"] is True
    assert entries["/cv.pdf"]["is_dir"] is False
    assert entries["/cv.pdf"]["size"] > 0


def test_ls_subdirectory(seeded):
    result = seeded.ls("/sub")
    assert [e["path"] for e in result.entries or []] == ["/sub/notes.md"]


def test_ls_empty_area_returns_empty_entries(backend):
    result = backend.ls("/")
    assert result.error is None
    assert result.entries == []


def test_glob_matches_without_downloading(seeded):
    result = seeded.glob("**/*.md")
    assert [m["path"] for m in result.matches or []] == ["/sub/notes.md"]

    none = seeded.glob("*.docx")
    assert none.matches == []


def test_grep_searches_text_objects_only(seeded):
    result = seeded.grep("hello")

    assert result.error is None
    assert [(m["path"], m["line"]) for m in result.matches or []] == [("/sub/notes.md", 1)]


def test_grep_scopes_to_path(seeded):
    assert seeded.grep("hello", path="/sub").matches
    assert seeded.grep("hello", path="/elsewhere").matches == []


# ---------------------------------------------------------------------------
# upload_files / download_files
# ---------------------------------------------------------------------------


def test_upload_download_round_trip(backend):
    pdf = b"%PDF-1.4 body"
    up = backend.upload_files([("/cv.pdf", pdf), ("/../bad", b"x")])
    assert up[0].error is None
    assert up[1].error == "invalid_path"

    down = backend.download_files(["/cv.pdf", "/missing.pdf", "/../bad"])
    assert down[0].content == pdf
    assert down[1].error == "file_not_found"
    assert down[2].error == "invalid_path"


def test_upload_overwrites_silently(backend):
    backend.upload_files([("/cv.pdf", b"v1")])
    backend.upload_files([("/cv.pdf", b"v2")])
    assert backend.download_files(["/cv.pdf"])[0].content == b"v2"
