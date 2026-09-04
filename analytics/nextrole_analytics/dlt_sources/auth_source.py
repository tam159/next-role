"""dlt source over the Better Auth tables → bronze dataset ``raw_auth``.

The auth tables are created by a one-time ``@better-auth/cli migrate`` (see the
README's multi-user section), so a fresh checkout may not have them yet: every
resource probes table existence at extraction time and yields nothing (with a
warning) when the table is missing — the pipeline never crashes over it, and
the dbt staging layer degrades to empty relations via its source guard.

PII handling at extract (blueprint doctrine): email → sha256 + domain, IP →
/24 prefix, user agent truncated; secret columns (session token, account
credentials, jwks keys) are never selected.

Better Auth columns are camelCase and must stay double-quoted in SQL; dlt's
snake_case naming convention normalizes them in the destination
(``createdAt`` → ``created_at``, ``userId`` → ``user_id``, …).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import dlt
import sqlalchemy as sa

from nextrole_analytics.dlt_sources.app_source import _text_columns, pg_uri
from nextrole_analytics.dlt_sources.transforms import shape_session, shape_user

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from dlt.sources import DltResource

logger = logging.getLogger(__name__)

_USER_SQL_FULL = (
    'SELECT "id", "name", "email", "emailVerified", "createdAt", "updatedAt" FROM public."user"'
)
_USER_SQL_INCREMENTAL = _USER_SQL_FULL + ' WHERE "updatedAt" >= :cursor'
_SESSION_SQL_FULL = (
    'SELECT "id", "expiresAt", "createdAt", "updatedAt", "ipAddress", "userAgent", "userId" '
    "FROM public.session"
)
_SESSION_SQL_INCREMENTAL = _SESSION_SQL_FULL + ' WHERE "updatedAt" >= :cursor'
_ACCOUNT_SQL_FULL = (
    'SELECT "id", "providerId", "userId", "createdAt", "updatedAt" FROM public.account'
)
_ACCOUNT_SQL_INCREMENTAL = _ACCOUNT_SQL_FULL + ' WHERE "updatedAt" >= :cursor'


def _rows_if_table_exists(
    uri: str,
    table: str,
    sql_full: str,
    sql_incremental: str,
    cursor: Any,  # noqa: ANN401
) -> Iterator[dict[str, Any]]:
    """Stream mapping rows, or nothing (with a warning) when the table is absent."""
    engine = sa.create_engine(uri)
    try:
        if not sa.inspect(engine).has_table(table, schema="public"):
            logger.warning(
                "Better Auth table public.%s does not exist yet (auth migration not run) — "
                "skipping extraction for it.",
                table,
            )
            return
        stmt = sa.text(sql_incremental if cursor is not None else sql_full)
        params = {"cursor": cursor} if cursor is not None else {}
        with engine.connect() as conn:
            for row in conn.execute(stmt, params).mappings():
                yield dict(row)
    finally:
        engine.dispose()


@dlt.resource(
    name="user",
    primary_key="id",
    write_disposition="merge",
    columns=_text_columns("name", "email_hash", "email_domain"),
)
def _user_resource(
    uri: str,
    updated_at: dlt.sources.incremental[Any] = dlt.sources.incremental("updatedAt"),
) -> Iterator[dict[str, Any]]:
    """Yield registered users, pseudonymized (email hash + domain; image dropped)."""
    for row in _rows_if_table_exists(
        uri,
        "user",
        _USER_SQL_FULL,
        _USER_SQL_INCREMENTAL,
        updated_at.last_value,
    ):
        yield shape_user(row)


@dlt.resource(
    name="session",
    primary_key="id",
    write_disposition="merge",
    columns=_text_columns("ip_prefix", "user_agent"),
)
def _session_resource(
    uri: str,
    updated_at: dlt.sources.incremental[Any] = dlt.sources.incremental("updatedAt"),
) -> Iterator[dict[str, Any]]:
    """Login sessions with coarsened network identifiers (token never selected)."""
    for row in _rows_if_table_exists(
        uri,
        "session",
        _SESSION_SQL_FULL,
        _SESSION_SQL_INCREMENTAL,
        updated_at.last_value,
    ):
        yield shape_session(row)


@dlt.resource(name="account", primary_key="id", write_disposition="merge")
def _account_resource(
    uri: str,
    updated_at: dlt.sources.incremental[Any] = dlt.sources.incremental("updatedAt"),
) -> Iterator[dict[str, Any]]:
    """OAuth/credential account links (provider only; secrets never selected)."""
    yield from _rows_if_table_exists(
        uri,
        "account",
        _ACCOUNT_SQL_FULL,
        _ACCOUNT_SQL_INCREMENTAL,
        updated_at.last_value,
    )


@dlt.source(name="nextrole_auth")
def nextrole_auth_source() -> Sequence[DltResource]:
    """Build the three Better Auth resources (incremental merge loads)."""
    uri = pg_uri()
    return [_user_resource(uri), _session_resource(uri), _account_resource(uri)]
