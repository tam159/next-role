"""Runtime settings for core_server, sourced from env + the project .env file."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader with ${VAR} interpolation; never overrides real env."""
    if not path.exists():
        return
    raw: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        raw[k.strip()] = v.strip()

    def _resolve(val: str) -> str:
        return re.sub(
            r"\$\{([^}]+)\}",
            lambda m: os.environ.get(m.group(1)) or raw.get(m.group(1), ""),
            val,
        )

    for k, v in raw.items():
        os.environ.setdefault(k, _resolve(v))


_load_dotenv(_ROOT / ".env")


def _pg_uri() -> str:
    uri = os.environ.get("CORE_SERVER_POSTGRES_URI")
    if uri:
        return uri
    user = os.environ.get("POSTGRES_USER", "langgraph_clone")
    pw = os.environ.get("POSTGRES_PASSWORD", "langgraph_clone")
    db = os.environ.get("POSTGRES_DB", "langgraph_clone")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_LOCAL_PORT", "5406")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}?sslmode=disable"


def _redis_uri() -> str:
    uri = os.environ.get("CORE_SERVER_REDIS_URI") or os.environ.get("REDIS_URI")
    if uri and "${" not in uri:
        return uri
    port = os.environ.get("REDIS_LOCAL_PORT", "6306")
    return f"redis://localhost:{port}"


POSTGRES_URI = _pg_uri()
REDIS_URI = _redis_uri()

BIND = os.environ.get("CORE_SERVER_BIND", "0.0.0.0:50052")
# Forward not-yet-native RPCs here. Empty string => fully native (no fallback).
GO_FALLBACK = os.environ.get("CORE_SERVER_GO_FALLBACK", "localhost:50051")

MAX_MSG_BYTES = int(os.environ.get("CORE_SERVER_MAX_MSG_BYTES", str(300 * 1024 * 1024)))
POOL_MIN = int(os.environ.get("CORE_SERVER_POOL_MIN", "2"))
POOL_MAX = int(os.environ.get("CORE_SERVER_POOL_MAX", "10"))
