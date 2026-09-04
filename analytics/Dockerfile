# NextRole analytics — the Dagster code-location image (see analytics/CLAUDE.md).
# One image, two compose services: `dagster-webserver` (UI + GraphQL) and
# `dagster-daemon` (schedules + run launcher). Both parse the dbt project into a
# container-local manifest at boot, then exec their dagster process.
#
# Stage 1 resolves the locked dependencies into a self-contained venv at
# /opt/venv — outside the /deps/next-role bind mount, so the dev volume overlay
# never shadows installed packages. Stage 2 carries only python + the venv +
# the analytics source.

# ---------- Stage 1: builder ----------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.16 /uv /usr/local/bin/uv

# UV_COMPILE_BYTECODE: precompile .pyc into the venv (faster cold start).
# UV_LINK_MODE=copy: self-contained venv, safe to copy across stages.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

# Wheels cover the C extensions (psycopg2-binary, clickhouse-driver);
# build-essential is builder-stage insurance for any sdist fallback.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /deps/next-role/analytics

# Resolve deps from uv.lock only — the project itself is a uv virtual project
# (no build backend); its package is imported via PYTHONPATH, not installed.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.13-slim

# curl: compose healthchecks + in-container API pokes.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Baked at /deps/next-role/analytics so the image layout equals the dev bind
# mount (compose mounts the repo root at /deps/next-role and overlays this).
WORKDIR /deps/next-role/analytics
COPY . .

# DAGSTER_HOME holds the instance config; compose overlays a named volume and
# bind-mounts the repo dagster.yaml into it.
RUN mkdir -p /opt/dagster/home

ENV PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/deps/next-role/analytics \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/home \
    DBT_PROFILES_DIR=/deps/next-role/analytics/dbt

EXPOSE 3000

# Default to the webserver; compose overrides per service (daemon runs
# `dagster-daemon run`, both prefixed by a `dbt parse` for the manifest).
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", \
     "-w", "/deps/next-role/analytics/dagster/workspace.yaml"]
