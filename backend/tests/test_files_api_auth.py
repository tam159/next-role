"""Auth-guard behavior of the files API in multi-user mode.

The server's auth middleware (enable_custom_route_auth) populates
``scope["user"]``; the app's own guard 401s when multi-user mode is on and no
authenticated user reached a handler (belt-and-braces for the misconfigured
case where the custom-route flag is missing).
"""

import pytest
from backend.agents import files_api
from obstore.store import MemoryStore
from starlette.testclient import TestClient


class _AuthedUser:
    """Stands in for the custom-auth ProxyUser (concrete `identity`)."""

    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.is_authenticated = True


@pytest.fixture
def mem_store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    """Swap the module's store factory for a fresh in-memory store."""
    store = MemoryStore()
    monkeypatch.setattr(files_api, "get_store", lambda: store)
    return store


class _PlantUser:
    """ASGI wrapper standing in for the server's auth middleware."""

    def __init__(self, app, user) -> None:
        self.app = app
        self.user = user

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and self.user is not None:
            scope["user"] = self.user
        await self.app(scope, receive, send)


def _client(monkeypatch: pytest.MonkeyPatch, *, auth_enabled: bool, user=None) -> TestClient:
    monkeypatch.setattr(files_api, "_AUTH_ENABLED", auth_enabled)
    return TestClient(_PlantUser(files_api.app, user), raise_server_exceptions=True)


def test_single_user_mode_stays_open(monkeypatch, mem_store) -> None:
    client = _client(monkeypatch, auth_enabled=False)
    res = client.get("/files/list", params={"prefixes": "/upload/"})
    assert res.status_code == 200


def test_multi_user_mode_401s_without_a_user(monkeypatch, mem_store) -> None:
    client = _client(monkeypatch, auth_enabled=True)
    for method, path, kwargs in [
        ("get", "/files/list", {"params": {"prefixes": "/upload/"}}),
        ("get", "/files/read", {"params": {"path": "/upload/cv.pdf"}}),
        ("post", "/files/upload", {"data": {"path": "/upload"}}),
        ("put", "/files/write", {"json": {"path": "/upload/x.md", "content": "hi"}}),
        ("delete", "/files/delete", {"params": {"path": "/upload/cv.pdf"}}),
    ]:
        res = getattr(client, method)(path, **kwargs)
        assert res.status_code == 401, f"{method} {path} → {res.status_code}"


def test_multi_user_mode_serves_authenticated_requests(monkeypatch, mem_store) -> None:
    client = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("user-1"))
    res = client.get("/files/list", params={"prefixes": "/upload/"})
    assert res.status_code == 200


def test_files_api_scopes_keys_to_the_request_user(monkeypatch, mem_store) -> None:
    """Uploads land under the authenticated user's object-key scope."""
    client = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("alice"))
    res = client.post(
        "/files/upload",
        data={"path": "/upload"},
        files={"file": ("cv.pdf", b"%PDF-1.4 alice", "application/pdf")},
    )
    assert res.status_code == 200
    keys = [str(m["path"]) for m in mem_store.list().collect()]
    assert keys == ["users/alice/career_agent/upload/cv.pdf"]

    # Bob sees nothing of Alice's.
    bob = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("bob"))
    listing = bob.get("/files/list", params={"prefixes": "/upload/"})
    assert listing.json()["files"] == []
