"""Run/node metadata.

The usage-telemetry loop that periodically POSTed graph-run and node-execution
counts to LangChain/LangSmith endpoints (``beacon.langchain.com`` and the
LangSmith control-plane ``/metadata/submit`` endpoint) has been removed for
local / self-study use. Nothing here collects or transmits usage data anymore.

What is kept and why:
  * ``RUN_COUNTER`` / ``NODE_COUNTER`` and their ``incr_runs`` / ``incr_nodes``
    updaters — retained as *local-only* in-process counters. ``incr_runs`` is
    called from ``worker.py`` and ``incr_nodes`` is wired as the pregel
    ``__pregel_node_finished`` callback in ``stream.py``. Nothing reads them
    today; they are kept for possible local use (a metrics readout, debugging)
    and are never transmitted off-box.
  * ``metadata_loop`` — still scheduled by the postgres and inmem lifespans.
    Kept as an async no-op so that wiring keeps working without sending anything.
  * ``HOST`` / ``PLAN`` / ``USER_API_URL`` — these are *labels* attached to each
    run's ``config["metadata"]`` (see ``stream.py``) and to OpenAPI/A2A server
    URLs. They are not telemetry and are retained unchanged.

This intentionally does NOT touch LangSmith LLM tracing (LLM run traces), which
is a separate, supported feature.
"""

import os
import uuid

import structlog

logger = structlog.stdlib.get_logger(__name__)

# Not in public docs: set by SaaS control plane, not user-configurable
VARIANT = os.getenv("LANGSMITH_LANGGRAPH_API_VARIANT")
PROJECT_ID = os.getenv("LANGSMITH_HOST_PROJECT_ID")
if PROJECT_ID:
    try:
        uuid.UUID(PROJECT_ID)
    except ValueError:
        raise ValueError(f"Invalid project ID: {PROJECT_ID}. Must be a valid UUID") from None
if VARIANT == "cloud":
    HOST = "saas"
elif PROJECT_ID:
    HOST = "byoc"
else:
    HOST = "self-hosted"
# NextRole runs without license machinery (open source); upstream derived this
# from langgraph_license.validation.plus_features_enabled(), a stub that always
# returned True, so the constant is behavior-identical.
PLAN = "enterprise"
USER_API_URL = os.getenv("LANGGRAPH_API_URL", None)

# Local-only run/node counters, maintained in-process (cumulative since startup).
# Retained for possible local use (a metrics readout, debugging); nothing reads
# them today and nothing transmits them. Updated without a lock, so counts are
# approximate when runs execute across concurrent worker threads/loops.
RUN_COUNTER = 0
NODE_COUNTER = 0


def incr_runs(*, incr: int = 1) -> None:
    """Increment the local cumulative run counter (kept local, not transmitted)."""
    global RUN_COUNTER
    RUN_COUNTER += incr


def incr_nodes(_, *, incr: int = 1) -> None:
    """Increment the local cumulative node counter (kept local, not transmitted)."""
    global NODE_COUNTER
    NODE_COUNTER += incr


async def metadata_loop() -> None:
    """No-op: usage metadata is no longer submitted to LangChain/LangSmith."""
    logger.info("Usage metadata submission disabled; metadata loop is a no-op")
