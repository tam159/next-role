"""Usage ledger SQL against the compose Postgres (``-m integration``).

Exercises what the unit tests stub out: table creation, the rolling-window
aggregates, the per-owner advisory lock + insert, and the report. Rows are
written under a unique owner and deleted afterwards.
"""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
from backend.agents import usage
from backend.agents.usage import TokenUsage
from dotenv import load_dotenv

pytestmark = pytest.mark.integration

_REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


@pytest.fixture
async def owner(monkeypatch) -> AsyncIterator[str]:
    """A fresh ledger owner under a 2-runs/day, $0.05/day limit; rows removed after."""
    load_dotenv(_REPO_ENV)
    if not os.environ.get("POSTGRES_URI"):
        pytest.skip("POSTGRES_URI not configured in .env")
    for name in ("QUOTA_RUNS_PER_WEEK", "QUOTA_USD_PER_WEEK", "QUOTA_GLOBAL_USD_PER_DAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUOTA_RUNS_PER_DAY", "2")
    monkeypatch.setenv("QUOTA_USD_PER_DAY", "0.05")
    monkeypatch.setenv("QUOTA_MAX_CONCURRENT_RUNS", "0")
    name = f"it-usage-{uuid.uuid4()}"
    yield name
    async with await psycopg.AsyncConnection.connect(usage._conninfo(), autocommit=True) as conn:  # noqa: SLF001
        await conn.execute("DELETE FROM usage_ledger WHERE owner = %s", (name,))


async def test_run_limit_counts_and_then_refuses(owner):
    assert (await usage.check_and_record_run(owner, "run-1")).allowed
    assert (await usage.check_and_record_run(owner, "run-2")).allowed

    refused = await usage.check_and_record_run(owner, "run-3")

    assert refused.allowed is False
    assert refused.reason == "runs_day"
    assert refused.resets_at is not None
    report = await usage.usage_report(owner)
    assert report["blocked"] is True
    assert report["runs_left_today"] == 0


async def test_metered_spend_blocks_the_next_run(owner):
    assert (await usage.check_and_record_run(owner, "run-1")).allowed
    # 30k uncached Sonnet input tokens = $0.06, over the $0.05 daily allowance.
    await usage.record_model_usage(
        owner,
        "run-1",
        "claude-sonnet-5-5",
        TokenUsage(input_tokens=30_000),
    )

    refused = await usage.check_and_record_run(owner, "run-2")

    assert refused.reason == "usd_day"
    report = await usage.usage_report(owner)
    assert report["reason"] == "usd_day"
    assert report["runs_left_today"] == 1
