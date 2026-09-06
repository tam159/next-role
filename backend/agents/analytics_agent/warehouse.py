"""Read-only ClickHouse access for the analytics agent.

The real boundary is the server: the `analytics_agent` ClickHouse user holds
SELECT on the marts and staging databases only, runs under a `readonly=2`
profile, and its limits carry `<constraints><max>` so a model-authored
`SETTINGS` clause cannot raise them (see
`analytics/clickhouse/users.d/analytics-agent.xml`). Everything here is the
second layer: it keeps obvious mistakes from becoming ClickHouse errors, and
turns the errors that do happen into text the model can act on.

Nothing in this module raises. Every failure comes back as an `Error: ...`
string so the model can read it, adjust, and retry — the same contract the
career agent's tools follow.
"""

from __future__ import annotations

import functools
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import clickhouse_connect
from backend.agents.analytics_agent.settings import WarehouseSettings

if TYPE_CHECKING:
    from clickhouse_connect.driver.client import Client


class QueryResult(Protocol):
    """The slice of a clickhouse-connect result this module reads."""

    column_names: Any
    result_rows: Any


class QueryClient(Protocol):
    """Anything that can run a query (structural, so fakes qualify).

    Declaring the minimal surface rather than the concrete driver client keeps
    this module a leaf and lets tests drive it with a plain stub — the same
    reason `career_agent/render_scratch.py` declares `ScratchBackend`.
    """

    def query(self, query: str) -> QueryResult:
        """Run a read query and return its result."""
        ...


#: Statements the agent may run. Read-only by intent; the grants enforce it.
_ALLOWED_LEADS = frozenset(
    {"SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN", "EXISTS"},
)

#: Leading keywords worth naming in the error, so the model learns the rule
#: instead of guessing. Anything else falls through to a generic message.
_WRITE_LEADS = frozenset(
    {
        "INSERT",
        "ALTER",
        "CREATE",
        "DROP",
        "TRUNCATE",
        "RENAME",
        "ATTACH",
        "DETACH",
        "OPTIMIZE",
        "SYSTEM",
        "KILL",
        "GRANT",
        "REVOKE",
        "SET",
        "USE",
    },
)

#: Statements where a row limit is meaningless (they return metadata).
_NO_LIMIT_LEADS = frozenset({"DESCRIBE", "DESC", "SHOW", "EXPLAIN", "EXISTS"})

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\b", re.IGNORECASE)

#: ClickHouse names the offending aggregate in code 184, e.g.
#: `Aggregate function min(first_seen_date) AS first_seen_date is found in WHERE`.
_OFFENDING_AGGREGATE = re.compile(r"Aggregate function (.+?)(?: AS (\w+))? is found", re.DOTALL)

_SHADOWING_FIX = (
    "An alias reuses a column's own name, so every other mention of that name — "
    "including in WHERE — resolves to the alias instead of the column. Rename the "
    "alias (`sum(x) AS total_x`), and fix every aliased aggregate in the select "
    "list, not only the one named above. Do NOT move the condition to HAVING: "
    "that would filter on the aggregate rather than on each row, which quietly "
    "answers a different question."
)

_HAVING_FIX = (
    "An aggregate's alias is used in WHERE, which filters rows before aggregation "
    "happens. Move that condition to HAVING, or repeat the underlying expression "
    "in WHERE if you meant to filter rows."
)


def _illegal_aggregation_hint(message: str) -> str:
    """Tell the two causes of code 184 apart.

    Both print the same shape, and they need opposite fixes: a shadowed column
    must be renamed, while a genuine aggregate-in-WHERE must move to HAVING.
    The difference is whether the alias also appears inside the aggregate it
    names — `min(first_seen_date) AS first_seen_date` shadows, `count() AS n`
    does not.
    """
    match = _OFFENDING_AGGREGATE.search(message)
    if match is not None:
        expression, alias = match.group(1), match.group(2)
        if alias and re.search(rf"\b{re.escape(alias)}\b", expression):
            return _SHADOWING_FIX
    if "in WHERE" in message:
        return _HAVING_FIX
    # "found inside another aggregate" with no alias named: still shadowing —
    # a genuinely nested aggregate is rare next to how often an alias collides.
    return _SHADOWING_FIX


#: ClickHouse error codes worth a tailored recovery hint. Anything else keeps
#: the server's own message, which is usually specific enough.
_ERROR_HINTS = {
    46: (
        "That function does not exist here. Most standard SQL functions do work "
        "(DATE_TRUNC, EXTRACT, DATEDIFF, COALESCE, IFNULL, NOW), so do not avoid "
        "them — the trap is inventing a name from ClickHouse's `toXxx` family. "
        "Day and month names are `dateName('weekday', d)` / `dateName('month', d)`, "
        "not `toDayName`; string aggregation is "
        "`arrayStringConcat(groupArray(x), ',')`, not `STRING_AGG`. Check the "
        "dialect reference in the `warehouse-analysis` skill before substituting "
        "another guess."
    ),
    47: "Unknown column. Run describe_data('<table>') for the exact column names.",
    60: "Unknown table. Run describe_data() to list what exists.",
    81: "Unknown database. Only the marts and staging databases are readable.",
    159: (
        "The query hit the 30s limit. Narrow the date range, filter on the "
        "table's sort key, or aggregate instead of scanning rows."
    ),
    241: (
        "The query ran out of memory. Reduce GROUP BY cardinality, add a date "
        "filter, or aggregate in stages."
    ),
    386: (
        "A join or UNION is comparing two different integer types — commonly "
        "`numbers()` (UInt64) against an arithmetic expression (Int16). ClickHouse "
        "will not widen them for you. Cast both sides to the same type, e.g. "
        "`toUInt64(toDayOfWeek(d) - 1)`."
    ),
    403: (
        "`JOIN ... ON` needs at least one plain equality between the two sides "
        "(`a.key = b.key`; computed expressions on either side are fine). A "
        "function predicate or an inequality gives ClickHouse no join key — put "
        "those in `WHERE`, or as an extra `AND` alongside a real equality. Also "
        "worth asking whether the join is needed: grouping the real rows is "
        "usually simpler than joining against a generated scaffold."
    ),
    452: ("That SETTINGS value exceeds this user's cap. Limits can be lowered, never raised."),
    497: (
        "Access denied. This connection reads the marts and staging databases "
        "only. If you were looking up a schema, use describe_data('<table>') — it "
        "carries the column meanings and caveats that `information_schema` does "
        "not, and `DESCRIBE <table>` works as a bare fallback. If you were "
        "reaching for bronze or writing, that is out of scope: say so rather than "
        "looking for another route."
    ),
}


def strip_noise(sql: str) -> str:
    """Return `sql` with comments and string literals blanked out.

    Used for classification only, so a `;` or a keyword inside a literal or a
    comment cannot be mistaken for statement structure. Literals collapse to
    `''` rather than vanishing, keeping token boundaries intact.
    """
    without_comments = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))
    return _STRING_LITERAL.sub("''", without_comments)


def leading_keyword(sql: str) -> str:
    """Return the statement's first bare keyword, upper-cased (`""` if none)."""
    match = re.search(r"[A-Za-z]+", strip_noise(sql).lstrip().lstrip("("))
    return match.group(0).upper() if match else ""


def classify_statement(sql: str) -> str | None:
    """Return `None` when `sql` is a single readable statement, else an error.

    Rejects empty input, multiple statements, and anything that is not a read.
    The message names the rule so the model can correct itself in one turn.
    """
    stripped = sql.strip()
    if not stripped:
        return "Error: empty query. Pass a single SELECT statement."

    cleaned = strip_noise(stripped).strip().rstrip(";").strip()
    if ";" in cleaned:
        return (
            "Error: only one statement per call. Split the query and call "
            "run_sql once per statement."
        )

    lead = leading_keyword(cleaned)
    if lead in _WRITE_LEADS:
        return (
            f"Error: {lead} is not allowed. This agent has read-only access to "
            f"the warehouse; use SELECT (or DESCRIBE / SHOW / EXPLAIN)."
        )
    if lead not in _ALLOWED_LEADS:
        allowed = ", ".join(sorted(_ALLOWED_LEADS))
        return f"Error: unsupported statement '{lead or sql[:20]}'. Allowed: {allowed}."
    return None


def ensure_limit(sql: str, limit: int) -> tuple[str, bool]:
    """Append `LIMIT limit` when the statement has none.

    Returns the SQL to run and whether a limit was added, so the caller can say
    so rather than letting the model believe it saw the whole result. Metadata
    statements (`DESCRIBE`, `SHOW`, ...) are returned untouched.
    """
    body = sql.strip().rstrip(";").rstrip()
    if leading_keyword(body) in _NO_LIMIT_LEADS:
        return body, False
    if _LIMIT_CLAUSE.search(strip_noise(body)):
        return body, False
    return f"{body}\nLIMIT {limit}", True


@dataclass
class QueryOutcome:
    """A successful read, plus what the caller must disclose about it."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    elapsed_s: float
    read_rows: int | None = None
    limit_injected: bool = False
    #: The row limit appended to the query, when one was.
    injected_limit: int | None = None
    #: True when the server's `max_result_rows` cap truncated the result.
    #: `result_overflow_mode=break` truncates without raising, so an
    #: undisclosed partial result would read as a complete answer.
    server_capped: bool = False
    types: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        """Number of rows returned."""
        return len(self.rows)


@functools.lru_cache(maxsize=1)
def get_client() -> Client:
    """Build (once) the read-only ClickHouse client.

    Lazy on purpose: agent modules are imported by the core-server just to
    enumerate graphs, which must not require a reachable warehouse.

    `autogenerate_session_id=False` matters: a ClickHouse session serializes
    its queries, so the client's default per-connection session id makes two
    parallel `run_sql` calls — which a model issues routinely — fail with
    "concurrent queries within the same session". The agent keeps no session
    state (no temporary tables, no SET), so the id buys nothing and costs
    concurrency.
    """
    settings = WarehouseSettings()
    return clickhouse_connect.get_client(
        host=settings.host,
        port=settings.port,
        username=settings.agent_user,
        password=settings.agent_password,
        connect_timeout=settings.connect_timeout,
        send_receive_timeout=settings.send_receive_timeout,
        autogenerate_session_id=False,
        client_name="nextrole-analytics-agent",
    )


def friendly_error(exc: Exception) -> str:
    """Render a ClickHouse exception as an `Error: ...` line plus a hint."""
    message = str(exc).strip()
    code_match = re.search(r"Code:\s*(\d+)", message)
    # Keep the server's own sentence: it names the column or table at fault.
    first_line = message.split("\n")[0].strip()
    code = int(code_match.group(1)) if code_match else None
    # 184 has two opposite fixes depending on the cause, so it is derived from
    # the message rather than looked up.
    hint = _illegal_aggregation_hint(message) if code == 184 else _ERROR_HINTS.get(code)  # noqa: PLR2004
    return f"Error (clickhouse): {first_line}" + (f"\nHint: {hint}" if hint else "")


def run_readonly(
    sql: str,
    *,
    display_limit: int | None = None,
    client: QueryClient | None = None,
    settings: WarehouseSettings | None = None,
) -> QueryOutcome | str:
    """Run a read-only query, returning a `QueryOutcome` or an `Error: ...` string.

    `display_limit` appends a row limit when the statement has none — pass
    `None` when the full result is wanted (a chart, or a `save_as` export).
    """
    settings = settings or WarehouseSettings()
    rejection = classify_statement(sql)
    if rejection is not None:
        return rejection

    body = sql.strip().rstrip(";").rstrip()
    limit_injected = False
    if display_limit is not None:
        body, limit_injected = ensure_limit(body, display_limit)

    started = time.monotonic()
    try:
        result = (client or get_client()).query(body)
    except Exception as exc:
        return friendly_error(exc)
    elapsed = time.monotonic() - started

    rows = [tuple(row) for row in result.result_rows]
    summary = getattr(result, "summary", None) or {}
    read_rows = summary.get("read_rows")
    return QueryOutcome(
        columns=list(result.column_names),
        types=[str(t) for t in getattr(result, "column_types", [])],
        rows=rows,
        elapsed_s=elapsed,
        read_rows=int(read_rows) if read_rows is not None else None,
        limit_injected=limit_injected,
        injected_limit=display_limit if limit_injected else None,
        server_capped=len(rows) >= settings.server_max_result_rows,
    )


def _cell(value: Any, max_width: int) -> str:  # noqa: ANN401 — any column type
    """Render one value for a markdown table, escaping pipes and truncating."""
    if value is None:
        return "∅"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= max_width else f"{text[: max_width - 1]}…"


def render_table(outcome: QueryOutcome, *, max_rows: int = 200, max_width: int = 80) -> str:
    """Render a result as a markdown table with a one-line summary above it.

    The summary always states how many rows exist versus how many are shown, so
    a truncated result is never mistaken for a complete one.
    """
    shown = outcome.rows[:max_rows]
    parts = [f"{outcome.row_count} row(s)"]
    if len(shown) < outcome.row_count:
        parts.append(f"showing {len(shown)}")
    parts.append(f"{outcome.elapsed_s:.2f}s")
    if outcome.read_rows is not None:
        parts.append(f"scanned {outcome.read_rows:,}")
    header = " · ".join(parts)

    notices = []
    # Only worth saying when the limit actually bit. Announcing it on a complete
    # result would suggest rows are missing when none are.
    if outcome.limit_injected and outcome.row_count == outcome.injected_limit:
        notices.append(
            f"The query had no LIMIT, so {outcome.injected_limit} was applied and "
            f"reached — there are more rows. Aggregate, or set your own LIMIT.",
        )
    if outcome.server_capped:
        notices.append(
            "The server's 10,000-row cap truncated this result. Aggregate or "
            "filter further; the numbers above are incomplete.",
        )

    if not outcome.columns:
        return "\n".join([header, *notices])
    if not shown:
        return "\n".join([header, "", "_No rows._", *notices])

    head = "| " + " | ".join(outcome.columns) + " |"
    rule = "| " + " | ".join("---" for _ in outcome.columns) + " |"
    body = ["| " + " | ".join(_cell(value, max_width) for value in row) + " |" for row in shown]
    return "\n".join([header, "", head, rule, *body, *(["", *notices] if notices else [])])
