"""Guardrails around the agent's read-only SQL path.

The ClickHouse user's grants and settings profile are the real boundary (see
`analytics/clickhouse/users.d/analytics-agent.xml`); these tests pin the second
layer — that obvious mistakes are caught before they become server errors, that
truncation is always disclosed, and that nothing raises.
"""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from backend.agents.analytics_agent import warehouse
from backend.agents.analytics_agent.settings import WarehouseSettings
from backend.agents.analytics_agent.warehouse import (
    QueryOutcome,
    classify_statement,
    ensure_limit,
    friendly_error,
    leading_keyword,
    render_table,
    run_readonly,
    strip_noise,
)


class FakeResult:
    """Stand-in for a clickhouse-connect QueryResult."""

    def __init__(
        self,
        columns: list[str],
        rows: list[tuple[object, ...]],
        summary: dict[str, str] | None = None,
    ) -> None:
        """Capture the shape a query would have returned."""
        self.column_names = columns
        self.column_types = ["String"] * len(columns)
        self.result_rows = rows
        self.summary = summary or {}


class FakeClient:
    """Records the SQL it was handed and replays a canned result.

    Satisfies `QueryClient` structurally, which is the point of declaring that
    protocol: the guardrails are testable without a warehouse.
    """

    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        """Replay `result`, or raise `error`, for every query."""
        self._result = result
        self._error = error
        self.seen: list[str] = []

    def query(self, query: str) -> Any:  # noqa: ANN401 — replays whatever the test set
        """Record the SQL and replay the canned outcome."""
        self.seen.append(query)
        if self._error is not None:
            raise self._error
        return self._result


# ---------------------------------------------------------------------------
# statement classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  select count() from nextrole_marts.fct_run  ",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "DESCRIBE nextrole_marts.fct_run",
        "SHOW TABLES FROM nextrole_marts",
        "EXPLAIN SELECT 1",
        "SELECT 1;",
        "-- a comment\nSELECT 1",
    ],
)
def test_reads_are_accepted(sql):
    assert classify_statement(sql) is None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "ALTER TABLE t DELETE WHERE 1",
        "DROP TABLE t",
        "TRUNCATE TABLE t",
        "CREATE TABLE t (a Int)",
        "SYSTEM RELOAD CONFIG",
        "GRANT SELECT ON x.* TO y",
        "KILL QUERY WHERE 1",
    ],
)
def test_writes_are_rejected_by_name(sql):
    error = classify_statement(sql)
    assert error is not None
    assert error.startswith("Error:")
    assert sql.split()[0] in error


def test_multiple_statements_are_rejected():
    error = classify_statement("SELECT 1; SELECT 2")
    assert error is not None
    assert "one statement" in error


def test_empty_query_is_rejected():
    assert "empty query" in (classify_statement("   ") or "")


def test_a_semicolon_inside_a_literal_is_not_a_second_statement():
    assert classify_statement("SELECT 'a;b' AS x") is None


def test_a_write_keyword_inside_a_literal_does_not_reject():
    assert classify_statement("SELECT 'INSERT' AS label") is None


def test_a_write_keyword_inside_a_comment_does_not_reject():
    assert classify_statement("SELECT 1 -- DROP TABLE t") is None


def test_strip_noise_keeps_token_boundaries():
    assert strip_noise("SELECT 'a' /* c */ FROM t -- tail").split() == [
        "SELECT",
        "''",
        "FROM",
        "t",
    ]


def test_leading_keyword_sees_through_a_parenthesised_select():
    assert leading_keyword("(SELECT 1)") == "SELECT"


# ---------------------------------------------------------------------------
# limit injection
# ---------------------------------------------------------------------------


def test_limit_is_added_when_absent():
    sql, injected = ensure_limit("SELECT * FROM t", 200)
    assert injected is True
    assert sql.endswith("LIMIT 200")


def test_existing_limit_is_respected():
    sql, injected = ensure_limit("SELECT * FROM t LIMIT 5", 200)
    assert injected is False
    assert sql == "SELECT * FROM t LIMIT 5"


def test_limit_inside_a_literal_does_not_count_as_a_limit():
    sql, injected = ensure_limit("SELECT 'limit' AS x FROM t", 200)
    assert injected is True
    assert sql.endswith("LIMIT 200")


def test_metadata_statements_are_left_alone():
    sql, injected = ensure_limit("DESCRIBE nextrole_marts.fct_run", 200)
    assert injected is False
    assert sql == "DESCRIBE nextrole_marts.fct_run"


def test_trailing_semicolon_is_dropped_before_appending():
    sql, _ = ensure_limit("SELECT 1;", 10)
    assert ";" not in sql


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def test_run_readonly_returns_rows_and_records_the_injected_limit():
    client = FakeClient(FakeResult(["n"], [(1,)], {"read_rows": "42"}))

    outcome = run_readonly("SELECT count() FROM t", display_limit=200, client=client)

    assert isinstance(outcome, QueryOutcome)
    assert outcome.columns == ["n"]
    assert outcome.rows == [(1,)]
    assert outcome.read_rows == 42
    assert outcome.limit_injected is True
    assert outcome.injected_limit == 200
    assert client.seen[0].endswith("LIMIT 200")


def test_run_readonly_skips_limit_injection_when_the_caller_wants_everything():
    client = FakeClient(FakeResult(["n"], [(1,)]))

    run_readonly("SELECT 1", display_limit=None, client=client)

    assert "LIMIT" not in client.seen[0]


def test_run_readonly_rejects_a_write_without_touching_the_client():
    client = FakeClient(FakeResult([], []))

    outcome = run_readonly("DROP TABLE t", client=client)

    assert isinstance(outcome, str)
    assert client.seen == []


def test_run_readonly_reports_server_errors_in_band():
    client = FakeClient(error=RuntimeError("Code: 47. DB::Exception: Unknown expression x"))

    outcome = run_readonly("SELECT x FROM t", client=client)

    assert isinstance(outcome, str)
    assert outcome.startswith("Error (clickhouse):")
    assert "describe_data" in outcome


def test_run_readonly_flags_the_silent_server_cap():
    settings = WarehouseSettings(server_max_result_rows=3)
    client = FakeClient(FakeResult(["n"], [(1,), (2,), (3,)]))

    outcome = run_readonly("SELECT n FROM t LIMIT 3", client=client, settings=settings)

    assert isinstance(outcome, QueryOutcome)
    assert outcome.server_capped is True


def test_friendly_error_without_a_code_keeps_the_message():
    assert friendly_error(RuntimeError("connection refused")).startswith("Error (clickhouse):")


def test_shadowing_reported_as_found_in_where_says_rename_not_having():
    """Code 184's two causes look identical and need opposite fixes.

    `min(d) AS d` with `WHERE d >= '...'` reports "found in WHERE", but the
    WHERE meant the column. Moving it to HAVING runs and silently filters the
    aggregate instead of each row — a wrong answer, not an error.
    """
    hinted = friendly_error(
        RuntimeError(
            "Code: 184. DB::Exception: Aggregate function min(first_seen_date) AS "
            "first_seen_date is found in WHERE in query.",
        ),
    )

    assert "Rename the alias" in hinted
    assert "Do NOT move the condition to HAVING" in hinted


def test_a_genuine_aggregate_alias_in_where_is_sent_to_having():
    """`count() AS n` is not a column, so this one really does belong in HAVING."""
    hinted = friendly_error(
        RuntimeError("Code: 184. DB::Exception: Aggregate function count() AS n is found in WHERE"),
    )

    assert "Move that condition to HAVING" in hinted
    assert "Rename the alias" not in hinted


def test_the_alias_shadowing_error_gets_a_named_cause():
    """Code 184 reads as a query-structure problem but is almost always an alias.

    ClickHouse resolves `sum(x) AS x` so that every other mention of `x` points
    at the alias, nesting the aggregates. Without this hint a model tends to
    rewrite the whole query instead of renaming one alias.
    """
    hinted = friendly_error(
        RuntimeError(
            "Code: 184. DB::Exception: Aggregate function sumIf(est_cost_usd, ...) "
            "is found inside another aggregate function in query.",
        ),
    )

    assert "reuses a column's own name" in hinted
    assert "total_x" in hinted
    # The error names only the first offending aggregate, so a model that fixes
    # just that one hits the same error again on the next.
    assert "every aliased aggregate" in hinted


def test_an_invented_function_is_named_along_with_the_real_one():
    """Code 46 means a guessed name, usually from ClickHouse's `toXxx` family.

    The hint also has to say that standard SQL functions *do* work, or a model
    over-corrects and avoids DATE_TRUNC and friends that are perfectly valid.
    """
    hinted = friendly_error(
        RuntimeError("Code: 46. DB::Exception: Function with name `toDayName` does not exist."),
    )

    assert "dateName('weekday', d)" in hinted
    assert "DATE_TRUNC" in hinted


def test_an_unjoinable_on_clause_names_the_requirement():
    """Code 403 means `ON` had no determinable key.

    The hint also questions the join itself: these come from generating a
    scaffold table and joining to it, where grouping the real rows would do.
    """
    hinted = friendly_error(
        RuntimeError("Code: 403. DB::Exception: Cannot determine join keys in JOIN ON expression"),
    )

    assert "plain equality" in hinted
    assert "scaffold" in hinted


def test_a_join_type_mismatch_names_the_cast():
    """Code 386 is almost always `numbers()` (UInt64) against an Int expression."""
    hinted = friendly_error(
        RuntimeError("Code: 386. DB::Exception: There is no supertype for types Int16, UInt64"),
    )

    assert "toUInt64" in hinted


def test_access_denied_points_at_the_schema_tool():
    """The 497 a model actually hits is often introspection, not a data grab.

    `information_schema` is unreadable here, so the hint has to name the path
    that works rather than only restating what is forbidden.
    """
    hinted = friendly_error(
        RuntimeError(
            "Code: 497. DB::Exception: analytics_agent: Not enough privileges. "
            "To execute this query, it's necessary to have the grant SELECT ON "
            "information_schema.columns.",
        ),
    )

    assert "describe_data(" in hinted


def test_friendly_error_adds_a_hint_for_a_timeout():
    assert "Narrow the date range" in friendly_error(
        RuntimeError("Code: 159. DB::Exception: Timeout exceeded"),
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


_BASE_OUTCOME = QueryOutcome(columns=["day", "runs"], rows=[("2026-09-01", 3)], elapsed_s=0.12)


def _outcome(**kwargs: object) -> QueryOutcome:
    """A one-row result, with fields overridden per test."""
    return replace(_BASE_OUTCOME, **kwargs)


def test_render_table_leads_with_a_summary():
    text = render_table(_outcome(read_rows=1234))
    assert text.splitlines()[0] == "1 row(s) · 0.12s · scanned 1,234"


def test_render_table_emits_a_markdown_table():
    text = render_table(_outcome())
    assert "| day | runs |" in text
    assert "| 2026-09-01 | 3 |" in text


def test_render_table_discloses_truncation():
    outcome = _outcome(rows=[(f"d{i}", i) for i in range(10)])
    text = render_table(outcome, max_rows=3)
    assert "showing 3" in text
    assert len([ln for ln in text.splitlines() if ln.startswith("| d") and "day" not in ln]) == 3


def test_render_table_discloses_the_server_cap():
    assert "incomplete" in render_table(_outcome(server_capped=True))


def test_an_injected_limit_that_was_reached_is_disclosed():
    outcome = _outcome(rows=[("d", 1)], limit_injected=True, injected_limit=1)
    assert "there are more rows" in render_table(outcome)


def test_an_injected_limit_that_was_not_reached_stays_quiet():
    # The result is complete; saying a limit was applied would imply otherwise.
    outcome = _outcome(rows=[("d", 1)], limit_injected=True, injected_limit=200)
    assert "more rows" not in render_table(outcome)


def test_render_table_escapes_pipes_and_newlines():
    text = render_table(_outcome(rows=[("a|b", "x\ny")]))
    assert "a\\|b" in text
    assert "x y" in text


def test_render_table_marks_nulls_distinctly():
    assert "∅" in render_table(_outcome(rows=[(None, 1)]))


def test_render_table_handles_an_empty_result():
    assert "_No rows._" in render_table(_outcome(rows=[]))


def test_render_table_truncates_wide_cells():
    text = render_table(_outcome(rows=[("x" * 200, 1)]), max_width=10)
    assert "…" in text
    assert "x" * 200 not in text


def test_fake_result_summary_absent_is_tolerated():
    client = FakeClient(SimpleNamespace(column_names=["n"], result_rows=[(1,)]))
    outcome = run_readonly("SELECT 1", client=client)
    assert isinstance(outcome, QueryOutcome)
    assert outcome.read_rows is None


# ---------------------------------------------------------------------------
# client construction
# ---------------------------------------------------------------------------


def test_the_client_does_not_open_a_clickhouse_session(monkeypatch):
    """Parallel tool calls must not serialize on one session.

    A ClickHouse session runs its queries one at a time, so the driver's
    default per-connection session id makes two simultaneous `run_sql` calls
    fail with "concurrent queries within the same session" — which a model
    issuing parallel tool calls hits immediately.
    """
    captured: dict = {}
    monkeypatch.setattr(
        warehouse.clickhouse_connect,
        "get_client",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    warehouse.get_client.cache_clear()

    warehouse.get_client()
    warehouse.get_client.cache_clear()

    assert captured["autogenerate_session_id"] is False
