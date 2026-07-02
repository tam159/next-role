# NextRole backend — vendored LangGraph Agent Server (see backend/ARCHITECTURE.md).
# One image, two compose services: `backend` runs uvicorn (HTTP/SSE + graph
# workers), `core-server` runs `python -m core_server` (gRPC data plane).
#
# Stage 1 resolves the locked dependencies into a self-contained venv at
# /opt/venv — outside the /deps/next-role bind mount, so the dev volume overlay
# never shadows installed packages. Stage 2 carries only python + the venv +
# the backend source.

# ---------- Stage 1: builder ----------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.16 /uv /usr/local/bin/uv

# UV_COMPILE_BYTECODE: precompile .pyc into the venv (faster cold start).
# UV_LINK_MODE=copy: self-contained venv, safe to copy across stages.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

# Wheels cover the C extensions (grpcio, psycopg[binary], jsonschema-rs);
# build-essential is builder-stage insurance for any sdist fallback.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /deps/next-role/backend

# Resolve deps from uv.lock only — the project itself is a uv virtual project
# (no build backend); its packages are imported via PYTHONPATH, not installed.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.13-slim

# weasyprint + pango/harfbuzz: native stack for the career agent's PDF
# rendering. curl/wget/vim: healthcheck + in-container debugging conveniences.
RUN apt-get update && apt-get install -y --no-install-recommends \
      weasyprint libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      curl wget vim \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Baked at /deps/next-role/backend so the image layout equals the dev bind
# mount (compose mounts the repo root at /deps/next-role and overlays this).
WORKDIR /deps/next-role/backend
COPY . .

# PYTHONPATH: /deps/next-role resolves the repo-root-anchored
# `backend.agents.*` imports; /deps/next-role/backend resolves the vendored
# server packages (langgraph_api, core_server, ...). openapi.json is read at
# import time from the directory containing langgraph_api/ — satisfied here
# by construction.
ENV PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/deps/next-role:/deps/next-role/backend \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Mirrors the official image's uvicorn invocation (graceful shutdown +
# keep-alive). The compose dev override appends --reload.
CMD ["uvicorn", "langgraph_api.server:app", \
     "--log-config", "logging.json", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", \
     "--timeout-graceful-shutdown", "3600", \
     "--timeout-keep-alive", "75"]
