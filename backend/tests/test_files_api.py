"""Unit tests for the artifact files HTTP API (`backend/agents/files_api.py`).

Runs the Starlette app through its TestClient with the storage layer swapped
for an in-memory object store — validation and response-shape parity with the
former Next.js `/api/files/*` routes is asserted here.
"""

import base64
import io

import pytest
from backend.agents import files_api
from backend.agents.career_agent.object_storage import KEY_SCOPE, put_bytes
from obstore.store import MemoryStore
from starlette.testclient import TestClient


@pytest.fixture
def mem_store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    """Swap the module's store factory for a fresh in-memory store."""
    store = MemoryStore()
    monkeypatch.setattr(files_api, "get_store", lambda: store)
    return store


@pytest.fixture
def client(mem_store: MemoryStore) -> TestClient:
    """TestClient over the files app (raises no server exceptions silently)."""
    return TestClient(files_api.app, raise_server_exceptions=True)


def _seed(store: MemoryStore, vpath: str, data: bytes) -> None:
    put_bytes(store, f"{KEY_SCOPE}{vpath}", data)


# ---------------------------------------------------------------------------
# GET /files/list
# ---------------------------------------------------------------------------


def test_list_requires_prefixes(client):
    res = client.get("/files/list")
    assert res.status_code == 400


def test_list_rejects_unknown_prefix(client):
    res = client.get("/files/list", params={"prefixes": "/secrets/"})
    assert res.status_code == 403


def test_list_returns_entries_sorted_by_path(client, mem_store):
    _seed(mem_store, "/upload/cv.pdf", b"%PDF-1.4")
    _seed(mem_store, "/upload/a-notes.md", b"hi")
    _seed(mem_store, "/tailored_resume/r/j.yaml", b"cv: {}")

    res = client.get(
        "/files/list",
        params={"prefixes": "/upload/,/tailored_resume/,/interview_battlecard/"},
    )

    assert res.status_code == 200
    files = res.json()["files"]
    assert [f["path"] for f in files] == [
        "/tailored_resume/r/j.yaml",
        "/upload/a-notes.md",
        "/upload/cv.pdf",
    ]
    by_path = {f["path"]: f for f in files}
    assert by_path["/upload/cv.pdf"]["isBinary"] is True
    assert by_path["/upload/a-notes.md"]["isBinary"] is False
    assert by_path["/upload/cv.pdf"]["size"] == len(b"%PDF-1.4")
    assert by_path["/upload/cv.pdf"]["modifiedAt"]  # ISO timestamp present


def test_list_empty_bucket_gives_empty_files(client):
    res = client.get("/files/list", params={"prefixes": "/upload/"})
    assert res.status_code == 200
    assert res.json() == {"files": []}


# ---------------------------------------------------------------------------
# GET /files/read
# ---------------------------------------------------------------------------


def test_read_requires_path(client):
    assert client.get("/files/read").status_code == 400


def test_read_forbids_non_artifact_paths(client):
    assert client.get("/files/read", params={"path": "/processed/x.md"}).status_code == 403
    assert client.get("/files/read", params={"path": "/upload/../x"}).status_code == 403


def test_read_missing_is_404(client):
    assert client.get("/files/read", params={"path": "/upload/ghost.pdf"}).status_code == 404


def test_read_text_and_binary_shapes(client, mem_store):
    _seed(mem_store, "/upload/notes.md", "héllo".encode())
    pdf = b"%PDF-1.4 body"
    _seed(mem_store, "/upload/cv.pdf", pdf)

    text = client.get("/files/read", params={"path": "/upload/notes.md"}).json()
    assert text == {"content": "héllo", "encoding": "utf-8"}

    binary = client.get("/files/read", params={"path": "/upload/cv.pdf"}).json()
    assert binary["encoding"] == "base64"
    assert base64.standard_b64decode(binary["content"]) == pdf


# ---------------------------------------------------------------------------
# POST /files/upload
# ---------------------------------------------------------------------------


def _upload(client: TestClient, name: str, data: bytes, target: str = "/upload"):
    return client.post(
        "/files/upload",
        data={"path": target},
        files=[("file", (name, io.BytesIO(data), "application/octet-stream"))],
    )


def test_upload_happy_path_overwrites_silently(client, mem_store):
    first = _upload(client, "cv.pdf", b"v1")
    assert first.status_code == 200
    assert first.json() == {"uploaded": [{"path": "/upload/cv.pdf", "size": 2}], "errors": []}

    second = _upload(client, "cv.pdf", b"v2-longer")
    assert second.status_code == 200

    read = client.get("/files/read", params={"path": "/upload/cv.pdf"}).json()
    assert base64.standard_b64decode(read["content"]) == b"v2-longer"


def test_upload_validation_parity(client):
    # Extension allowlist.
    res = _upload(client, "evil.exe", b"x")
    assert res.status_code == 400
    assert res.json()["errors"][0]["reason"] == "Unsupported extension: .exe"

    # Filename rules.
    res = _upload(client, ".hidden.pdf", b"x")
    assert res.json()["errors"][0]["reason"] == "Invalid filename"

    # Size cap (10 MB, mirrored from the former Next.js route).
    res = _upload(client, "big.pdf", b"x" * (10 * 1024 * 1024 + 1))
    assert res.json()["errors"][0]["reason"] == "File exceeds 10 MB limit"

    # Prefix allowlist.
    res = _upload(client, "cv.pdf", b"x", target="/processed")
    assert res.json()["errors"][0]["reason"] == "Forbidden path"


def test_upload_mixed_batch_partial_success(client):
    res = client.post(
        "/files/upload",
        data={"path": "/upload"},
        files=[
            ("file", ("ok.pdf", io.BytesIO(b"fine"), "application/pdf")),
            ("file", ("bad.exe", io.BytesIO(b"nope"), "application/octet-stream")),
        ],
    )
    assert res.status_code == 200  # at least one uploaded
    body = res.json()
    assert [u["path"] for u in body["uploaded"]] == ["/upload/ok.pdf"]
    assert [e["name"] for e in body["errors"]] == ["bad.exe"]


def test_upload_requires_path_and_files(client):
    assert client.post("/files/upload", data={"path": "/upload"}).status_code == 400
    res = client.post(
        "/files/upload",
        files=[("file", ("cv.pdf", io.BytesIO(b"x"), "application/pdf"))],
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# PUT /files/write
# ---------------------------------------------------------------------------


def test_write_utf8_and_base64(client, mem_store):
    res = client.put("/files/write", json={"path": "/upload/notes.md", "content": "hello"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    payload = base64.standard_b64encode(b"%PDF-1.4").decode("ascii")
    res = client.put(
        "/files/write",
        json={"path": "/upload/cv.pdf", "content": payload, "encoding": "base64"},
    )
    assert res.status_code == 200

    read = client.get("/files/read", params={"path": "/upload/cv.pdf"}).json()
    assert base64.standard_b64decode(read["content"]) == b"%PDF-1.4"


def test_write_validation(client):
    assert client.put("/files/write", content=b"not json").status_code == 400
    assert client.put("/files/write", json={"path": "/upload/x.md"}).status_code == 400
    assert (
        client.put(
            "/files/write",
            json={"path": "/processed/x.md", "content": "x"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/files/write",
            json={"path": "/upload/x.pdf", "content": "!!!", "encoding": "base64"},
        ).status_code
        == 400
    )


# ---------------------------------------------------------------------------
# DELETE /files/delete
# ---------------------------------------------------------------------------


def test_delete_contract(client, mem_store):
    _seed(mem_store, "/upload/cv.pdf", b"x")

    assert client.delete("/files/delete", params={"path": "/upload/cv.pdf"}).status_code == 200
    # Second delete: the file is gone -> 404 (head-checked, S3 delete itself
    # would succeed silently).
    assert client.delete("/files/delete", params={"path": "/upload/cv.pdf"}).status_code == 404
    assert client.delete("/files/delete", params={"path": "/processed/x.md"}).status_code == 403
    assert client.delete("/files/delete").status_code == 400
