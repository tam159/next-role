"""Integration round-trip against the compose SeaweedFS object store.

Requires the local stack (`docker compose up -d`); reads `OBJECT_STORE_*`
from the repo `.env` (host-side endpoint). Cleans up every key it writes.
"""

import uuid
from pathlib import Path

import pytest
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.object_storage import (
    ObjectStoreSettings,
    build_store_from_settings,
    delete_key,
    get_bytes,
    list_meta,
    put_bytes,
)
from dotenv import load_dotenv

pytestmark = pytest.mark.integration

_REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


@pytest.fixture(scope="module")
def store():
    """S3 client for the compose emulator, from the repo .env."""
    load_dotenv(_REPO_ENV)
    settings = ObjectStoreSettings()
    if not settings.endpoint or not settings.bucket:
        pytest.skip("OBJECT_STORE_* not configured in .env")
    return build_store_from_settings(settings)


def test_bytes_round_trip_and_cleanup(store):
    key = f"tests/integration/{uuid.uuid4()}.bin"
    payload = b"%PDF-1.4 integration"
    try:
        put_bytes(store, key, payload)
        assert get_bytes(store, key) == payload
        assert any(str(m["path"]) == key for m in list_meta(store, "tests/integration/"))
    finally:
        delete_key(store, key)
    assert get_bytes(store, key) is None


def test_backend_contract_against_real_emulator(store):
    marker = uuid.uuid4().hex[:8]
    backend = ObjectStoreBackend("upload", store_factory=lambda: store)
    text_path = f"/tests-{marker}/notes.md"
    pdf_path = f"/tests-{marker}/cv.pdf"
    try:
        assert backend.write(text_path, "hello emulator").error is None
        assert "already exists" in (backend.write(text_path, "again").error or "")
        assert backend.upload_files([(pdf_path, b"%PDF-1.4")])[0].error is None

        read = backend.read(pdf_path)
        assert read.file_data is not None
        assert read.file_data["encoding"] == "base64"

        listed = backend.ls(f"/tests-{marker}")
        assert {e["path"] for e in listed.entries or []} == {text_path, pdf_path}

        grep = backend.grep("hello", path=f"/tests-{marker}")
        assert [(m["path"], m["line"]) for m in grep.matches or []] == [(text_path, 1)]
    finally:
        for meta in list_meta(store, f"users/default/career_agent/upload/tests-{marker}/"):
            delete_key(store, str(meta["path"]))
