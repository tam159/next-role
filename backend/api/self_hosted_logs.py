"""
Self-hosted OTLP log export — disabled.

Log export to the LangChain/LangSmith OTLP collector (``LANGGRAPH_LOGS_ENDPOINT``,
authenticated with the ``X-Langchain-License-Key`` header) has been removed for
local / self-study use. The public entry points are kept as no-ops so the
postgres-runtime lifespan wiring keeps working, but no logs are exported off-box.
"""


def initialize_self_hosted_logs() -> None:
    """No-op: self-hosted log export to LangChain/LangSmith has been removed."""


def shutdown_self_hosted_logs() -> None:
    """No-op: self-hosted log export to LangChain/LangSmith has been removed."""
