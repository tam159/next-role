"""Unit tests for object-storage settings, key mapping, and byte helpers."""

import pytest
from backend.agents.career_agent.object_storage import (
    KEY_SCOPE,
    ObjectStoreSettings,
    area_key_prefix,
    delete_key,
    get_bytes,
    get_store,
    head_meta,
    key_for_area,
    key_for_virtual_path,
    list_meta,
    put_bytes,
    virtual_path_for_key,
)
from obstore.store import MemoryStore

# ---------------------------------------------------------------------------
# settings / store factory
# ---------------------------------------------------------------------------


def test_settings_read_object_store_env(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "http://object-store:8333")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "next-role-artifacts")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY", "k")
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "s")
    monkeypatch.setenv("OBJECT_STORE_FORCE_PATH_STYLE", "true")

    settings = ObjectStoreSettings()

    assert settings.endpoint == "http://object-store:8333"
    assert settings.bucket == "next-role-artifacts"
    assert settings.force_path_style is True


def test_get_store_requires_configuration(monkeypatch):
    for var in ("OBJECT_STORE_ENDPOINT", "OBJECT_STORE_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    get_store.cache_clear()

    with pytest.raises(RuntimeError, match="Object storage is not configured"):
        get_store()
    get_store.cache_clear()


def test_get_store_builds_s3_client_lazily(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "http://localhost:8333")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "b")
    get_store.cache_clear()

    store = get_store()

    assert store is get_store()  # cached singleton
    assert type(store).__name__ == "S3Store"
    get_store.cache_clear()


# ---------------------------------------------------------------------------
# key mapping
# ---------------------------------------------------------------------------


def test_key_for_area_maps_stripped_paths():
    assert key_for_area("upload", "/cv.pdf") == f"{KEY_SCOPE}/upload/cv.pdf"
    assert key_for_area("tailored_resume", "r/j.yaml") == f"{KEY_SCOPE}/tailored_resume/r/j.yaml"


@pytest.mark.parametrize("bad", ["", "/", "../x", "/a/../b", "~/x", "a\\b"])
def test_key_for_area_rejects_unsafe_paths(bad):
    assert key_for_area("upload", bad) is None


def test_key_for_area_normalizes_dot_segments():
    # PurePosixPath drops bare "." segments — the result is a clean key.
    assert key_for_area("upload", "/./x") == f"{KEY_SCOPE}/upload/x"


def test_key_for_virtual_path_allowlists_areas():
    assert key_for_virtual_path("/upload/cv.pdf") == f"{KEY_SCOPE}/upload/cv.pdf"
    assert (
        key_for_virtual_path("/interview_battlecard/r/j.pdf")
        == f"{KEY_SCOPE}/interview_battlecard/r/j.pdf"
    )
    # Non-artifact prefixes are refused — this doubles as the files-API allowlist.
    assert key_for_virtual_path("/processed/x.md") is None
    assert key_for_virtual_path("/render_intermediate/r/j.typ") is None
    # An area with no filename is not a key.
    assert key_for_virtual_path("/upload") is None
    assert key_for_virtual_path("/upload/") is None


def test_key_for_virtual_path_rejects_traversal():
    assert key_for_virtual_path("/upload/../processed/x.md") is None


def test_virtual_path_round_trip():
    for vpath in ("/upload/cv.pdf", "/tailored_resume/r/j.yaml"):
        key = key_for_virtual_path(vpath)
        assert key is not None
        assert virtual_path_for_key(key) == vpath
    assert virtual_path_for_key("users/other/career_agent/upload/cv.pdf") is None
    assert virtual_path_for_key(f"{KEY_SCOPE}/not_an_area/x.pdf") is None


def test_area_key_prefix():
    assert area_key_prefix("upload") == f"{KEY_SCOPE}/upload"


# ---------------------------------------------------------------------------
# per-user scoping
# ---------------------------------------------------------------------------


def test_scoped_keys_isolate_users():
    a = key_for_area("upload", "/cv.pdf", "alice")
    b = key_for_area("upload", "/cv.pdf", "bob")
    assert a == "users/alice/career_agent/upload/cv.pdf"
    assert b == "users/bob/career_agent/upload/cv.pdf"
    assert a != b


def test_scoped_virtual_path_builders_agree():
    key = key_for_virtual_path("/tailored_resume/r/j.yaml", "alice")
    assert key == "users/alice/career_agent/tailored_resume/r/j.yaml"
    assert area_key_prefix("tailored_resume", "alice") == "users/alice/career_agent/tailored_resume"
    # Round-trips only under the same scope; a mismatched scope rejects the key.
    assert virtual_path_for_key(key, "alice") == "/tailored_resume/r/j.yaml"
    assert virtual_path_for_key(key, "bob") is None


def test_scoped_keys_still_reject_traversal():
    assert key_for_area("upload", "../x", "alice") is None
    assert key_for_virtual_path("/upload/../processed/x.md", "alice") is None


# ---------------------------------------------------------------------------
# byte helpers (MemoryStore)
# ---------------------------------------------------------------------------


def test_byte_helpers_round_trip():
    store = MemoryStore()

    assert get_bytes(store, "k") is None
    assert head_meta(store, "k") is None

    put_bytes(store, "k", b"data")
    assert get_bytes(store, "k") == b"data"
    meta = head_meta(store, "k")
    assert meta is not None
    assert meta["size"] == 4

    put_bytes(store, "k", b"overwritten")  # PutObject semantics
    assert get_bytes(store, "k") == b"overwritten"


def test_delete_key_reports_existence():
    store = MemoryStore()
    put_bytes(store, "k", b"x")

    assert delete_key(store, "k") is True
    assert delete_key(store, "k") is False  # already gone -> 404 contract


def test_list_meta_filters_by_prefix():
    store = MemoryStore()
    put_bytes(store, "a/one.txt", b"1")
    put_bytes(store, "a/sub/two.txt", b"22")
    put_bytes(store, "b/three.txt", b"333")

    paths = sorted(str(m["path"]) for m in list_meta(store, "a/"))
    assert paths == ["a/one.txt", "a/sub/two.txt"]
