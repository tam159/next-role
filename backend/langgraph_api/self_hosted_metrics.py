"""
Self-hosted OTLP metrics export — disabled.

Metric export to the LangChain/LangSmith OTLP collector
(``LANGGRAPH_METRICS_ENDPOINT``, authenticated with the
``X-Langchain-License-Key`` header) has been removed for local / self-study use.

The public entry points are kept as no-ops so existing callers keep working
without sending anything off-box:
  * ``initialize_self_hosted_metrics`` / ``shutdown_self_hosted_metrics`` — the
    postgres-runtime lifespan still calls these.
  * ``record_http_request`` — ``http_metrics.py`` still calls this. Its own
    in-process Prometheus counters (scraped, not pushed) are unaffected.
"""


def initialize_self_hosted_metrics() -> None:
    """No-op: self-hosted metrics export to LangChain/LangSmith has been removed."""


def shutdown_self_hosted_metrics() -> None:
    """No-op: self-hosted metrics export to LangChain/LangSmith has been removed."""


def record_http_request(
    method: str, route_path: str, status: int, latency_seconds: float
) -> None:
    """No-op: self-hosted HTTP-request metrics export has been removed."""
