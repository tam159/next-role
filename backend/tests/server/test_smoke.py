"""End-to-end smoke tests for the agent server.

The server packages carry no mirrored unit tests (see backend/CLAUDE.md,
"Server packages") — these integration checks against the running local
stack are the regression net for the server and future dependency bumps.
"""

import asyncio
import contextlib
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


def test_openapi_spec_keeps_builtin_docs_with_custom_app() -> None:
    """`/openapi.json` serves the documented spec plus the mounted files API.

    Regression: with a custom HTTP app mounted (LANGGRAPH_HTTP), the server
    generated a skeleton spec from every route — built-ins included — and
    merged it over server/openapi.json with precedence, wiping each operation
    to `{}`; /docs rendered a flat, detail-less endpoint list.
    """
    response = httpx.get(f"{_base_url()}/openapi.json", timeout=10)

    assert response.status_code == 200
    paths = response.json()["paths"]
    create_assistant = paths["/assistants"]["post"]
    assert create_assistant["tags"] == ["Assistants"]
    assert create_assistant["summary"] == "Create Assistant"
    assert "requestBody" in create_assistant
    # The custom app's own routes must still be merged into the served spec.
    assert "/files/list" in paths


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


async def test_thread_event_stream_survives_idle() -> None:
    """The v2 thread stream is connection-scoped — it must stay open while idle.

    Regression: core-server's Threads.Stream once self-terminated ~2s after a
    thread had no active runs, silently killing the UI's long-lived SSE after
    every run — follow-up messages then executed with no subscriber and the
    chat never rendered the reply.
    """
    base = _base_url()
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        created = await client.post("/threads", json={})
        assert created.status_code == 200
        thread_id = created.json()["thread_id"]
        try:
            async with client.stream(
                "POST",
                f"/threads/{thread_id}/stream/events",
                json={"channels": ["lifecycle"]},
            ) as response:
                assert response.status_code == 200
                closed_early = False
                # 8s ≫ the old 2s idle-exit. If the timeout fires, the stream
                # is still open (pass); if the line iterator is exhausted, the
                # server closed an idle stream (the regression).
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(8):
                        async for _line in response.aiter_lines():
                            pass
                        closed_early = True
                assert not closed_early, "thread event stream closed while thread was idle"
        finally:
            deleted = await client.request("DELETE", f"/threads/{thread_id}")
            assert deleted.status_code == 204
