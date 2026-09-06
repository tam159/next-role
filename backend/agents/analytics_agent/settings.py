"""Connection and access settings for the analytics agent.

One `BaseSettings` class per concern, mirroring `career_agent/object_storage.py`:
every field has a default so importing this module never requires env (the
core-server imports agent packages purely to enumerate graphs), and the clients
built from them validate at first use instead.

The warehouse fields are deliberately named `agent_user` / `agent_password`
rather than `user` / `password`: the whole `.env` reaches this container, and a
`CLICKHOUSE_` prefix on `user` would silently pick up the admin credentials that
own every database. The agent only ever connects as the read-only identity
provisioned in `analytics/clickhouse/users.d/analytics-agent.xml`.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class WarehouseSettings(BaseSettings):
    """ClickHouse connection + query caps for the agent's read-only user.

    Read from `CLICKHOUSE_*`. The host defaults to the compose service name;
    `.env` carries only host-published ports, which do not resolve in-network,
    so compose overrides `CLICKHOUSE_HOST`/`CLICKHOUSE_PORT` for the backend.
    """

    model_config = SettingsConfigDict(env_prefix="CLICKHOUSE_", extra="ignore")

    host: str = "clickhouse"
    port: int = 8123
    agent_user: str = "analytics_agent"
    agent_password: str = ""
    db: str = "nextrole"
    connect_timeout: int = 5
    #: Kept above the server's `max_execution_time` so a query that trips the
    #: server limit returns ClickHouse's error rather than a client timeout.
    send_receive_timeout: int = 45
    #: Rows rendered back to the model when a query has no LIMIT of its own.
    display_rows: int = 200
    #: Rows a chart may plot before the tool asks for an aggregate instead.
    chart_max_rows: int = 5000
    #: Mirrors the server-side `max_result_rows`; used to detect silent
    #: truncation, since `result_overflow_mode=break` reports no error.
    server_max_result_rows: int = 10000

    @property
    def marts_db(self) -> str:
        """Gold layer database (`<db>_marts`)."""
        return f"{self.db}_marts"

    @property
    def staging_db(self) -> str:
        """Silver layer database (`<db>_staging`)."""
        return f"{self.db}_staging"


class CubeSettings(BaseSettings):
    """Cube semantic-layer endpoint (metric definitions, not query execution).

    Read from `CUBE_*`. `api_secret` is unused while Cube runs in dev mode,
    where `/v1/meta` needs no Authorization header; it is here so turning dev
    mode off is a config change, not a code change.
    """

    model_config = SettingsConfigDict(env_prefix="CUBE_", extra="ignore")

    api_url: str = "http://cube:4000"
    api_secret: str = ""


class DbtDocsSettings(BaseSettings):
    """dbt docs server, which serves `manifest.json` and `catalog.json`.

    Read from `DBT_DOCS_*`. Artifacts regenerate at that container's boot, so
    the descriptions are as fresh as the last `docker compose restart dbt-docs`.
    """

    model_config = SettingsConfigDict(env_prefix="DBT_DOCS_", extra="ignore")

    url: str = "http://dbt-docs:8080"
    #: Seconds a merged metadata snapshot is reused before refetching.
    cache_ttl_s: int = 600


class AccessSettings(BaseSettings):
    """Who may run the analytics agent when multi-user auth is enabled.

    Read from `ANALYTICS_AGENT_*`. The agent reads every user's activity, so
    access is an explicit allowlist rather than a role derived from the app.
    """

    model_config = SettingsConfigDict(env_prefix="ANALYTICS_AGENT_", extra="ignore")

    #: Comma-separated Better Auth user ids and/or emails. Empty denies
    #: everyone while auth is on; ignored entirely in single-user mode.
    allowed_users: str = ""

    def allowed(self) -> frozenset[str]:
        """Return the normalized allowlist entries (lowercased, blanks dropped)."""
        return frozenset(
            entry.strip().lower() for entry in self.allowed_users.split(",") if entry.strip()
        )
