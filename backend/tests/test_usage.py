"""Usage-quota policy: pricing, verdicts, the off switch, and the auth gate.

Database access is stubbed throughout; the SQL itself is exercised by the
live verification in deploy/vultr/README.md (smoke test step 5).
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

# `auth.py` reads AUTH_JWKS_URL at import (see test_auth_claims.py).
os.environ.setdefault("AUTH_JWKS_URL", "http://frontend:3000/api/auth/jwks")

from backend.agents import auth, usage
from backend.agents.usage import QuotaSettings, TokenUsage, UsageStats, Verdict, evaluate

_QUOTA_VARS = (
    "QUOTA_RUNS_PER_DAY",
    "QUOTA_RUNS_PER_WEEK",
    "QUOTA_USD_PER_DAY",
    "QUOTA_USD_PER_WEEK",
    "QUOTA_GLOBAL_USD_PER_DAY",
    "QUOTA_MAX_CONCURRENT_RUNS",
)


@pytest.fixture(autouse=True)
def _no_quota_env(monkeypatch):
    for name in _QUOTA_VARS:
        monkeypatch.delenv(name, raising=False)


NOW = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
LIMITS = QuotaSettings(
    runs_per_day=10,
    runs_per_week=30,
    usd_per_day=1.5,
    usd_per_week=4.0,
    global_usd_per_day=3.0,
    max_concurrent_runs=1,
)


def test_quotas_are_off_by_default():
    assert QuotaSettings().enabled is False


def test_any_single_limit_turns_quotas_on(monkeypatch):
    monkeypatch.setenv("QUOTA_RUNS_PER_DAY", "10")

    assert usage.quota_settings().enabled is True


def test_sonnet_cost_bills_cache_tokens_at_their_own_rates():
    # 100k input of which 80k cache reads + 10k cache writes, 2k output.
    tokens = TokenUsage(
        input_tokens=100_000,
        output_tokens=2_000,
        cache_read_tokens=80_000,
        cache_write_tokens=10_000,
    )

    cost = tokens.cost_usd("claude-sonnet-5-5")

    # 10k x $2 + 80k x $0.20 + 10k x $2.50 + 2k x $10, per 1M
    assert cost == pytest.approx((20_000 + 16_000 + 25_000 + 20_000) / 1_000_000)


def test_unknown_models_are_priced_like_the_default_not_free():
    tokens = TokenUsage(input_tokens=1_000_000)

    assert tokens.cost_usd("some-new-model") == pytest.approx(2.0)


def test_usage_metadata_parsing_reads_langchain_cache_details():
    parsed = TokenUsage.from_metadata(
        {
            "input_tokens": 500,
            "output_tokens": 20,
            "input_token_details": {"cache_read": 400, "cache_creation": 50},
        },
    )

    assert parsed == TokenUsage(500, 20, 400, 50)


def test_anthropic_ttl_breakdown_counts_as_cache_writes():
    """Real langchain_anthropic shape: `cache_creation` 0, writes under the TTL keys."""
    parsed = TokenUsage.from_metadata(
        {
            "input_tokens": 16712,
            "output_tokens": 4,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 16708,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    )

    assert parsed.cache_write_tokens == 16708
    # 4 uncached x $2 + 16,708 x $2.50 + 4 output x $10, per 1M
    assert parsed.cost_usd("claude-sonnet-5-5") == pytest.approx(
        (4 * 2 + 16708 * 2.5 + 4 * 10) / 1_000_000,
    )


def test_one_hour_cache_writes_cost_double_input():
    tokens = TokenUsage(
        input_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
    )

    assert tokens.cost_usd("claude-sonnet-5-5") == pytest.approx(4.0)


def test_under_every_limit_is_allowed():
    assert evaluate(UsageStats(runs_day=9, usd_day=1.0), LIMITS).allowed


def test_daily_run_limit_blocks_until_the_oldest_run_ages_out():
    oldest = NOW - timedelta(hours=20)

    verdict = evaluate(UsageStats(runs_day=10, runs_day_oldest=oldest), LIMITS)

    assert verdict.allowed is False
    assert verdict.reason == "runs_day"
    assert verdict.resets_at == oldest + timedelta(days=1)
    assert verdict.message is not None
    assert "10 runs" in verdict.message


def test_spend_limit_catches_a_few_heavy_runs():
    verdict = evaluate(UsageStats(runs_day=2, usd_day=1.6, usd_day_oldest=NOW), LIMITS)

    assert verdict.reason == "usd_day"


def test_shared_ceiling_wins_over_personal_limits():
    verdict = evaluate(
        UsageStats(runs_day=10, global_usd_day=3.2, global_usd_day_oldest=NOW),
        LIMITS,
    )

    assert verdict.reason == "global_usd_day"


def test_concurrency_is_checked_only_when_creating_a_run():
    stats = UsageStats(active_runs=1)

    assert evaluate(stats, LIMITS).reason == "concurrent"
    assert evaluate(stats, LIMITS, check_active=False).allowed


def test_runs_left_today_takes_the_tighter_window():
    stats = UsageStats(runs_day=3, runs_week=28)

    assert usage.runs_left_today(stats, LIMITS) == 2
    assert usage.runs_left_today(stats, QuotaSettings(usd_per_day=1)) is None


async def test_disabled_quotas_never_touch_the_database(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr(usage, "_connect", connect)

    assert (await usage.check_and_record_run("user-1", "run-1")).allowed
    assert await usage.usage_report("user-1") == {"enabled": False}
    connect.assert_not_called()


def test_metering_leaves_the_graph_untouched_when_off():
    graph = object()

    assert usage.with_usage_metering(graph) is graph


def _ctx(identity: str) -> auth.Auth.types.AuthContext:
    """Minimal stand-in for ``Auth.types.AuthContext`` (only ``user.identity`` is read)."""
    fake = type("Ctx", (), {"user": type("User", (), {"identity": identity})()})()
    return cast("auth.Auth.types.AuthContext", fake)


async def test_create_run_stamps_owner_and_returns_the_owner_filter(monkeypatch):
    seen = {}

    async def allow(owner, run_id):
        seen.update(owner=owner, run_id=run_id)
        return Verdict(allowed=True)

    monkeypatch.setattr(usage, "check_and_record_run", allow)
    value: Any = {"run_id": "run-1", "metadata": {}}

    result = await auth.authorize_create_run(ctx=_ctx("user-1"), value=value)

    assert result == {"owner": "user-1"}
    assert value["metadata"]["owner"] == "user-1"
    assert seen == {"owner": "user-1", "run_id": "run-1"}


async def test_create_run_over_quota_is_a_429_with_retry_after(monkeypatch):
    resets_at = datetime.now(UTC) + timedelta(hours=2)

    async def deny(owner, run_id):
        return Verdict(
            allowed=False,
            reason="runs_day",
            message="Limit reached.",
            resets_at=resets_at,
        )

    monkeypatch.setattr(usage, "check_and_record_run", deny)

    with pytest.raises(auth.Auth.exceptions.HTTPException) as raised:
        await auth.authorize_create_run(ctx=_ctx("user-1"), value=cast("Any", {"metadata": {}}))

    assert raised.value.status_code == 429
    assert raised.value.detail == "Limit reached."
    assert raised.value.headers is not None
    assert 7100 < int(raised.value.headers["Retry-After"]) <= 7200
