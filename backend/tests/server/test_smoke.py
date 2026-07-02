"""End-to-end smoke tests for the vendored LangGraph Agent Server.

The vendored server dirs carry no mirrored unit tests (see backend/CLAUDE.md,
"Vendored server code") — these integration checks against the running local
stack are the regression net for the server swap and future dependency bumps.
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.integration

_REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


def _base_url() -> str:
    """Backend base URL from LANGGRAPH_LOCAL_PORT (env first, then repo .env)."""
    port = os.getenv("LANGGRAPH_LOCAL_PORT") or dotenv_values(_REPO_ENV).get("LANGGRAPH_LOCAL_PORT")
    if not port:
        pytest.skip("LANGGRAPH_LOCAL_PORT not configured (env or repo .env)")
    return f"http://localhost:{port}"


def test_ok_endpoint_reports_healthy() -> None:
    """`/ok` returns 200 — it also pings the core-server gRPC health service."""
    response = httpx.get(f"{_base_url()}/ok", timeout=10)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_info_reports_server_version() -> None:
    """`/info` serves version + host metadata (regression: AttributeError 500)."""
    response = httpx.get(f"{_base_url()}/info", timeout=10)

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"]
    assert payload["host"]["kind"] == "self-hosted"


def test_store_roundtrip() -> None:
    """PUT → GET → search → DELETE through the KV store (DeepAgents memory path)."""
    base = _base_url()
    namespace = ["smoke-test"]
    key = f"roundtrip-{uuid.uuid4().hex[:8]}"
    value = {"ok": True}

    with httpx.Client(base_url=base, timeout=10) as client:
        try:
            put = client.put(
                "/store/items",
                json={"namespace": namespace, "key": key, "value": value},
            )
            assert put.status_code == 204

            got = client.get("/store/items", params={"namespace": namespace[0], "key": key})
            assert got.status_code == 200
            assert got.json()["value"] == value

            search = client.post("/store/items/search", json={"namespace_prefix": namespace})
            assert key in {item["key"] for item in search.json()["items"]}
        finally:
            deleted = client.request(
                "DELETE",
                "/store/items",
                json={"namespace": namespace, "key": key},
            )
            assert deleted.status_code == 204
