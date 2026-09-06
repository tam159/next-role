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


class _AuthedUserWithEmail(_AuthedUser):
    """A caller whose token carried an email claim (Better Auth's default)."""

    def __init__(self, identity: str, email: str) -> None:
        super().__init__(identity)
        self.email = email


# ---------------------------------------------------------------------------
# per-user scoping of the analytics agent's areas
# ---------------------------------------------------------------------------


def test_chart_writes_are_scoped_to_the_caller(monkeypatch, mem_store) -> None:
    client = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("alice"))

    response = client.put(
        "/files/write",
        json={"path": "/charts/t-1/runs.plotly.json", "content": "{}"},
    )

    assert response.status_code == 200
    keys = [str(meta["path"]) for meta in mem_store.list().collect()]
    assert keys == ["users/alice/analytics_agent/charts/t-1/runs.plotly.json"]


def test_one_users_charts_are_invisible_to_another(monkeypatch, mem_store) -> None:
    alice = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("alice"))
    alice.put("/files/write", json={"path": "/charts/t-1/runs.plotly.json", "content": "{}"})

    bob = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("bob"))
    listing = bob.get("/files/list", params={"prefixes": "/charts/"})

    assert listing.json()["files"] == []


# ---------------------------------------------------------------------------
# /agents/available
# ---------------------------------------------------------------------------


@pytest.fixture
def _two_graphs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register both graphs the way compose does."""
    monkeypatch.setenv(
        "LANGSERVE_GRAPHS",
        '{"career_agent": "a.py:career_agent", "analytics_agent": "b.py:analytics_agent"}',
    )


def _availability(response) -> dict[str, bool]:
    return {entry["graph_id"]: entry["allowed"] for entry in response.json()["agents"]}


@pytest.mark.usefixtures("_two_graphs")
def test_single_user_mode_allows_every_agent(monkeypatch) -> None:
    client = _client(monkeypatch, auth_enabled=False)

    assert _availability(client.get("/agents/available")) == {
        "career_agent": True,
        "analytics_agent": True,
    }


@pytest.mark.usefixtures("_two_graphs")
def test_an_unlisted_user_cannot_run_the_analytics_agent(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_AUTH", '{"path": "x"}')
    monkeypatch.setenv("ANALYTICS_AGENT_ALLOWED_USERS", "someone-else")
    client = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("alice"))

    assert _availability(client.get("/agents/available")) == {
        "career_agent": True,
        "analytics_agent": False,
    }


@pytest.mark.usefixtures("_two_graphs")
def test_a_listed_user_can_run_the_analytics_agent(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_AUTH", '{"path": "x"}')
    monkeypatch.setenv("ANALYTICS_AGENT_ALLOWED_USERS", "alice,bob")
    client = _client(monkeypatch, auth_enabled=True, user=_AuthedUser("alice"))

    assert _availability(client.get("/agents/available"))["analytics_agent"] is True


@pytest.mark.usefixtures("_two_graphs")
def test_the_allowlist_accepts_an_email_claim(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_AUTH", '{"path": "x"}')
    monkeypatch.setenv("ANALYTICS_AGENT_ALLOWED_USERS", "tam@example.com")
    user = _AuthedUserWithEmail("opaque-id", "tam@example.com")
    client = _client(monkeypatch, auth_enabled=True, user=user)

    assert _availability(client.get("/agents/available"))["analytics_agent"] is True


@pytest.mark.usefixtures("_two_graphs")
def test_availability_requires_authentication_in_multi_user_mode(monkeypatch) -> None:
    client = _client(monkeypatch, auth_enabled=True, user=None)

    assert client.get("/agents/available").status_code == 401


def test_availability_reports_only_registered_graphs(monkeypatch) -> None:
    monkeypatch.setenv("LANGSERVE_GRAPHS", '{"career_agent": "a.py:career_agent"}')
    client = _client(monkeypatch, auth_enabled=False)

    assert list(_availability(client.get("/agents/available"))) == ["career_agent"]


def test_malformed_graph_registration_is_not_fatal(monkeypatch) -> None:
    monkeypatch.setenv("LANGSERVE_GRAPHS", "not json")
    client = _client(monkeypatch, auth_enabled=False)

    assert client.get("/agents/available").json() == {"agents": []}
