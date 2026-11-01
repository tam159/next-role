"""Per-user usage quotas for shared deployments.

Off by default: with every ``QUOTA_*`` limit unset (or ``0``) nothing here
touches the database and the agents behave exactly as in local development.
Set any limit (see README "Environment variables") and three pieces switch on:

* **Metering** — :class:`UsageMeter`, a LangChain callback bound to each agent
  graph (:func:`with_usage_metering`), records the provider-reported token
  usage of every chat-model call: main agent, declarative subagents, the
  general-purpose subagent, and summarization calls alike, because callbacks
  propagate through the whole run tree. Each call becomes a ``llm`` row in the
  ``usage_ledger`` table with its estimated cost at list prices.
* **Enforcement** — :func:`check_and_record_run`, called from the
  ``threads.create_run`` auth handler (``auth.py``), refuses a new run with
  ``429`` when the caller is over a run or spend limit, or when the whole
  deployment is over its daily spend ceiling, and otherwise writes a ``run``
  row. A run that started always finishes, so the worst overshoot is one run.
* **Reporting** — :func:`usage_report` backs ``GET /usage`` (``files_api.py``)
  so the UI can show the runs left and lock the composer before a refusal.

Enforcement lives in the auth layer, so quotas apply in multi-user mode
(``LANGGRAPH_AUTH`` set) only. All windows are rolling (24 hours, 7 days), so
there is no timezone to configure. The ledger is append-only and owned by
this module (``CREATE TABLE IF NOT EXISTS`` on first use), independent of the
agent server's own migrations; deleting threads does not refund usage.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from backend.agents.career_agent.scope import current_identity
from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.config import get_config
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from uuid import UUID

    from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

DAY = timedelta(days=1)
WEEK = timedelta(days=7)

#: Ledger owner for single-user mode (no identity), matching the object-store
#: ``users/default/`` convention.
DEFAULT_OWNER = "default"


class QuotaSettings(BaseSettings):
    """Limits from ``QUOTA_*`` env vars; ``0`` (the default) disables a limit."""

    model_config = SettingsConfigDict(env_prefix="QUOTA_", extra="ignore")

    runs_per_day: int = 0
    runs_per_week: int = 0
    usd_per_day: float = 0.0
    usd_per_week: float = 0.0
    global_usd_per_day: float = 0.0
    max_concurrent_runs: int = 0

    @property
    def enabled(self) -> bool:
        """Whether any limit is set — the master switch for this module."""
        return any(
            value > 0
            for value in (
                self.runs_per_day,
                self.runs_per_week,
                self.usd_per_day,
                self.usd_per_week,
                self.global_usd_per_day,
                self.max_concurrent_runs,
            )
        )


def quota_settings() -> QuotaSettings:
    """Read the limits fresh from the environment (cheap; keeps tests simple)."""
    return QuotaSettings()


# ---------------------------------------------------------------- pricing

#: USD per 1M tokens: (input, output, cache read, cache write), matched by the
#: longest model-name substring. List prices — an estimate, not an invoice.
_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable": (10.0, 50.0, 0.25, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.2, 5.0),
    "claude-opus": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-5-5": (0.1, 0.5, 0.01, 0.125),
    "claude-haiku": (1.0, 5.0, 0.1, 1.25),
}

#: Unknown models (other providers, new releases) are priced like the default
#: model rather than as free, so a meter never under-counts to zero.
_FALLBACK_PRICE = _PRICES["claude-sonnet-5"]


def _price_for(model: str | None) -> tuple[float, float, float, float]:
    name = (model or "").lower()
    matches = [pattern for pattern in _PRICES if pattern in name]
    if not matches:
        return _FALLBACK_PRICE
    return _PRICES[max(matches, key=len)]


@dataclass(frozen=True)
class TokenUsage:
    """Provider-reported usage of one model call (LangChain ``usage_metadata``)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    #: All cache writes; ``cache_write_1h_tokens`` is the 1-hour-TTL subset.
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Sum two usages field by field (a response can carry several generations)."""
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.cache_write_1h_tokens + other.cache_write_1h_tokens,
        )

    @classmethod
    def from_metadata(cls, usage: dict[str, Any] | None) -> TokenUsage:
        """Parse a LangChain ``usage_metadata`` dict (``input_tokens`` includes cache).

        ``langchain_anthropic`` reports cache writes either as ``cache_creation``
        or, when the API breaks them down by TTL, as ``ephemeral_5m_input_tokens``
        / ``ephemeral_1h_input_tokens`` with ``cache_creation`` left at 0.
        """
        if not usage:
            return cls()
        details = usage.get("input_token_details") or {}
        write_5m = int(details.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(details.get("ephemeral_1h_input_tokens") or 0)
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(details.get("cache_read") or 0),
            cache_write_tokens=max(int(details.get("cache_creation") or 0), write_5m + write_1h),
            cache_write_1h_tokens=write_1h,
        )

    def cost_usd(self, model: str | None) -> float:
        """Estimated cost; cached tokens are billed at their own rates.

        5-minute cache writes cost the table's write price (1.25x input),
        1-hour writes 2x input.
        """
        price_in, price_out, price_read, price_write = _price_for(model)
        uncached = max(self.input_tokens - self.cache_read_tokens - self.cache_write_tokens, 0)
        write_5m = max(self.cache_write_tokens - self.cache_write_1h_tokens, 0)
        return (
            uncached * price_in
            + self.cache_read_tokens * price_read
            + write_5m * price_write
            + self.cache_write_1h_tokens * price_in * 2
            + self.output_tokens * price_out
        ) / 1_000_000


# ---------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class UsageStats:
    """Rolling-window aggregates for one owner (plus the deployment-wide spend).

    ``*_oldest`` is the oldest ledger row inside the window: when it ages out,
    the window frees up — the basis of the "try again after" time.
    """

    runs_day: int = 0
    runs_week: int = 0
    usd_day: float = 0.0
    usd_week: float = 0.0
    global_usd_day: float = 0.0
    runs_day_oldest: datetime | None = None
    runs_week_oldest: datetime | None = None
    usd_day_oldest: datetime | None = None
    usd_week_oldest: datetime | None = None
    global_usd_day_oldest: datetime | None = None
    active_runs: int = 0


@dataclass(frozen=True)
class Verdict:
    """Whether a new run may start, and if not, why and until when."""

    allowed: bool
    reason: str | None = None
    message: str | None = None
    resets_at: datetime | None = None


_REASON_MESSAGES = {
    "runs_day": "You've used all {limit} runs for the last 24 hours.",
    "runs_week": "You've used all {limit} runs for the last 7 days.",
    "usd_day": "You've used your usage allowance for the last 24 hours.",
    "usd_week": "You've used your usage allowance for the last 7 days.",
    "global_usd_day": "NextRole has reached its shared daily capacity.",
}


def _blocked(reason: str, limit: float, oldest: datetime | None, window: timedelta) -> Verdict:
    resets_at = (oldest + window) if oldest else None
    message = _REASON_MESSAGES[reason].format(limit=int(limit))
    if resets_at:
        message += f" You can start a new run after {resets_at:%H:%M} UTC on {resets_at:%b %d}."
    return Verdict(allowed=False, reason=reason, message=message, resets_at=resets_at)


def evaluate(stats: UsageStats, settings: QuotaSettings, *, check_active: bool = True) -> Verdict:
    """Apply the limits to the aggregates. Pure, so the policy is unit-testable.

    Order: the shared ceiling first (it is nobody's personal fault), then the
    caller's run counts, then spend. ``check_active`` covers the concurrency
    limit, which only makes sense at the moment a run is being created.
    """
    s = settings
    checks: tuple[tuple[str, float, float, datetime | None, timedelta], ...] = (
        (
            "global_usd_day",
            s.global_usd_per_day,
            stats.global_usd_day,
            stats.global_usd_day_oldest,
            DAY,
        ),
        ("runs_day", s.runs_per_day, stats.runs_day, stats.runs_day_oldest, DAY),
        ("runs_week", s.runs_per_week, stats.runs_week, stats.runs_week_oldest, WEEK),
        ("usd_day", s.usd_per_day, stats.usd_day, stats.usd_day_oldest, DAY),
        ("usd_week", s.usd_per_week, stats.usd_week, stats.usd_week_oldest, WEEK),
    )
    for reason, limit, used, oldest, window in checks:
        if limit > 0 and used >= limit:
            return _blocked(reason, limit, oldest, window)
    if check_active and s.max_concurrent_runs > 0 and stats.active_runs >= s.max_concurrent_runs:
        return Verdict(
            allowed=False,
            reason="concurrent",
            message="Another run is still in progress. Wait for it to finish, then try again.",
        )
    return Verdict(allowed=True)


def runs_left_today(stats: UsageStats, settings: QuotaSettings) -> int | None:
    """Count the runs the caller can still start now (``None`` = no run limit)."""
    left = [
        limit - used
        for limit, used in (
            (settings.runs_per_day, stats.runs_day),
            (settings.runs_per_week, stats.runs_week),
        )
        if limit > 0
    ]
    return max(min(left), 0) if left else None


# ---------------------------------------------------------------- storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_ledger (
    id bigserial PRIMARY KEY,
    owner text NOT NULL,
    kind text NOT NULL,
    run_id text,
    model text,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cache_read_tokens bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,
    cost_usd double precision NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_ledger_owner_created_idx ON usage_ledger (owner, created_at);
CREATE INDEX IF NOT EXISTS usage_ledger_created_idx ON usage_ledger (created_at);
"""

_STATS_SQL = """
SELECT
    count(*) FILTER (WHERE kind = 'run' AND created_at > now() - interval '1 day'),
    count(*) FILTER (WHERE kind = 'run'),
    coalesce(sum(cost_usd) FILTER (WHERE created_at > now() - interval '1 day'), 0),
    coalesce(sum(cost_usd), 0),
    min(created_at) FILTER (WHERE kind = 'run' AND created_at > now() - interval '1 day'),
    min(created_at) FILTER (WHERE kind = 'run'),
    min(created_at) FILTER (WHERE kind = 'llm' AND created_at > now() - interval '1 day'),
    min(created_at) FILTER (WHERE kind = 'llm')
FROM usage_ledger
WHERE owner = %(owner)s AND created_at > now() - interval '7 days'
"""

_GLOBAL_SQL = """
SELECT coalesce(sum(cost_usd), 0), min(created_at)
FROM usage_ledger
WHERE kind = 'llm' AND created_at > now() - interval '1 day'
"""

# Runs the server still considers in flight for this owner. The one-hour bound
# keeps a run orphaned by a crash from locking its owner out forever.
_ACTIVE_SQL = """
SELECT count(*) FROM run
WHERE status IN ('pending', 'running')
  AND metadata ->> 'owner' = %(owner)s
  AND created_at > now() - interval '1 hour'
"""

# Arbitrary constant for the advisory lock that serializes schema creation.
_SCHEMA_LOCK_KEY = 7_105_115_097_103_101
_schema_ready = False


def _conninfo() -> str:
    # `.env` carries the SQLAlchemy-style URI (`postgresql+psycopg://`); psycopg
    # wants the plain libpq scheme.
    return os.environ["POSTGRES_URI"].replace("postgresql+psycopg://", "postgresql://", 1)


async def _connect() -> psycopg.AsyncConnection:
    """Open a short-lived connection, creating the ledger table on first use.

    One connection per operation: calls are rare (per run / per model call),
    and this stays correct across event loops without a pool.
    """
    global _schema_ready  # noqa: PLW0603
    conn = await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True)
    if not _schema_ready:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
            await conn.execute(_SCHEMA)
        _schema_ready = True
    return conn


async def _load_stats(conn: psycopg.AsyncConnection, owner: str, *, active: bool) -> UsageStats:
    row = await (await conn.execute(_STATS_SQL, {"owner": owner})).fetchone()
    global_row = await (await conn.execute(_GLOBAL_SQL)).fetchone()
    active_runs = 0
    if active:
        active_row = await (await conn.execute(_ACTIVE_SQL, {"owner": owner})).fetchone()
        active_runs = int(active_row[0]) if active_row else 0
    if row is None or global_row is None:  # aggregates always return a row
        return UsageStats(active_runs=active_runs)
    return UsageStats(
        runs_day=int(row[0]),
        runs_week=int(row[1]),
        usd_day=float(row[2]),
        usd_week=float(row[3]),
        runs_day_oldest=row[4],
        runs_week_oldest=row[5],
        usd_day_oldest=row[6],
        usd_week_oldest=row[7],
        global_usd_day=float(global_row[0]),
        global_usd_day_oldest=global_row[1],
        active_runs=active_runs,
    )


async def check_and_record_run(owner: str, run_id: str | None) -> Verdict:
    """Gate a new run for ``owner``; on success, count it. No-op when disabled.

    The per-owner advisory lock serializes concurrent creates (two tabs, a
    double click), so a burst cannot slip past the run limit together.
    """
    settings = quota_settings()
    if not settings.enabled:
        return Verdict(allowed=True)
    async with await _connect() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"usage:{owner}",))
        stats = await _load_stats(conn, owner, active=settings.max_concurrent_runs > 0)
        verdict = evaluate(stats, settings)
        if verdict.allowed:
            await conn.execute(
                "INSERT INTO usage_ledger (owner, kind, run_id) VALUES (%s, 'run', %s)",
                (owner, run_id),
            )
    return verdict


async def usage_report(owner: str) -> dict[str, Any]:
    """Report the caller's quota state for the UI (``GET /usage``)."""
    settings = quota_settings()
    if not settings.enabled:
        return {"enabled": False}
    async with await _connect() as conn:
        stats = await _load_stats(conn, owner, active=False)
    verdict = evaluate(stats, settings, check_active=False)
    return {
        "enabled": True,
        "blocked": not verdict.allowed,
        "reason": verdict.reason,
        "message": verdict.message,
        "resets_at": verdict.resets_at.astimezone(UTC).isoformat() if verdict.resets_at else None,
        "runs_left_today": runs_left_today(stats, settings),
        "limits": {"runs_per_day": settings.runs_per_day, "runs_per_week": settings.runs_per_week},
    }


async def record_model_usage(
    owner: str,
    run_id: str | None,
    model: str | None,
    usage: TokenUsage,
) -> None:
    """Append one ``llm`` row. Never raises: metering must not break a run."""
    try:
        async with await _connect() as conn:
            await conn.execute(
                """
                INSERT INTO usage_ledger (owner, kind, run_id, model, input_tokens,
                    output_tokens, cache_read_tokens, cache_write_tokens, cost_usd)
                VALUES (%s, 'llm', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    owner,
                    run_id,
                    model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_write_tokens,
                    usage.cost_usd(model),
                ),
            )
    except Exception:
        logger.warning("usage metering: could not record a model call", exc_info=True)


# ---------------------------------------------------------------- metering


def _usage_from_result(response: LLMResult) -> TokenUsage:
    total = TokenUsage()
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            total += TokenUsage.from_metadata(getattr(message, "usage_metadata", None))
    return total


def _model_from_result(response: LLMResult) -> str | None:
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) or {}
            if name := metadata.get("model_name") or metadata.get("model"):
                return str(name)
    return None


class UsageMeter(AsyncCallbackHandler):
    """Record every chat-model call of a run into the usage ledger.

    The owner is resolved when the call starts, from the run config in context
    (``scope.current_identity``): the auth identity is deliberately kept out of
    callback metadata by LangGraph, but the config contextvar carries it into
    nested subagent calls too.
    """

    def __init__(self) -> None:
        """Track in-flight calls between their start and end events."""
        self._calls: dict[UUID, tuple[str, str | None, str | None]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],  # noqa: ARG002
        messages: list[list[Any]],  # noqa: ARG002
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Capture who this call is billed to."""
        graph_run_id: str | None = None
        try:
            config = get_config()
            graph_run_id = (config.get("metadata") or {}).get("run_id") or (
                config.get("configurable") or {}
            ).get("run_id")
        except RuntimeError:
            pass
        model = (metadata or {}).get("ls_model_name")
        self._calls[run_id] = (
            current_identity() or DEFAULT_OWNER,
            str(graph_run_id) if graph_run_id else None,
            str(model) if model else None,
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Write the call's usage to the ledger."""
        owner, graph_run_id, start_model = self._calls.pop(run_id, (DEFAULT_OWNER, None, None))
        usage = _usage_from_result(response)
        if usage == TokenUsage():
            return
        model = _model_from_result(response) or start_model
        await record_model_usage(owner, graph_run_id, model, usage)

    async def on_llm_error(
        self,
        error: BaseException,  # noqa: ARG002
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Forget a failed call (providers bill nothing usable to attribute)."""
        self._calls.pop(run_id, None)


def with_usage_metering(graph: Any) -> Any:  # noqa: ANN401  # CompiledStateGraph
    """Bind a :class:`UsageMeter` to ``graph`` when quotas are enabled.

    Returns ``graph`` untouched otherwise, so local development builds the
    exact same graph as before. Callbacks bound here merge with the server's
    per-run callbacks and reach every nested model call.
    """
    if not quota_settings().enabled:
        return graph
    if not os.environ.get("LANGGRAPH_AUTH"):
        logger.warning(
            "QUOTA_* limits are set but LANGGRAPH_AUTH is not: usage is metered, "
            "but limits are enforced only in multi-user mode.",
        )
    return graph.with_config(callbacks=[UsageMeter()])
