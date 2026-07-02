from collections import defaultdict
from typing import Any

from langgraph_api import config
from langgraph_api.http_metrics_utils import (
    HTTP_LATENCY_BUCKETS,
    get_route,
    should_filter_route,
)

MAX_REQUEST_COUNT_ENTRIES = 5000
MAX_HISTOGRAM_ENTRIES = 1000


class HTTPMetricsCollector:
    def __init__(self):
        # Counter: Key: (method, route, status), Value: count
        self._request_counts: dict[tuple[str, str, int], int] = defaultdict(int)

        self._histogram_buckets = HTTP_LATENCY_BUCKETS
        self._histogram_bucket_labels = [
            "+Inf" if value == float("inf") else str(value)
            for value in self._histogram_buckets
        ]

        self._histogram_data: dict[tuple[str, str], dict] = defaultdict(
            lambda: {
                "bucket_counts": [0] * len(self._histogram_buckets),
                "sum": 0.0,
                "count": 0,
            }
        )

    def record_request(
        self, method: str, route: Any, status: int, latency_ms: float
    ) -> None:
        route_path = get_route(route)
        if route_path is None:
            return

        if should_filter_route(route_path):
            return

        request_count_key = (method, route_path, status)
        histogram_key = (method, route_path)

        if (
            request_count_key not in self._request_counts
            and len(self._request_counts) >= MAX_REQUEST_COUNT_ENTRIES
        ):
            return

        if (
            histogram_key not in self._histogram_data
            and len(self._histogram_data) >= MAX_HISTOGRAM_ENTRIES
        ):
            return

        self._request_counts[request_count_key] += 1

        latency_seconds = latency_ms / 1000.0
        hist_data = self._histogram_data[histogram_key]

        for i, bucket_value in enumerate(self._histogram_buckets):
            if latency_seconds <= bucket_value:
                hist_data["bucket_counts"][i] += 1
                break

        hist_data["sum"] += latency_seconds
        hist_data["count"] += 1

        try:
            if config.LANGGRAPH_METRICS_ENABLED:
                from langgraph_api.self_hosted_metrics import (  # noqa: PLC0415
                    record_http_request,
                )

                record_http_request(method, route_path, status, latency_seconds)
        except Exception:
            pass

    def get_metrics(
        self,
        project_id: str | None,
        revision_id: str | None,
        format: str = "prometheus",
        deployment_type: str = "",
    ) -> dict | list[str]:
        if format == "json":
            return {
                "api": {
                    "http_requests_total": [
                        {
                            "method": method,
                            "path": path,
                            "status": status,
                            "count": count,
                        }
                        for (
                            method,
                            path,
                            status,
                        ), count in self._request_counts.items()
                    ]
                }
            }

        metrics = []

        # Counter metrics
        if self._request_counts:
            metrics.extend(
                [
                    "# HELP lg_api_http_requests_total Total number of HTTP requests.",
                    "# TYPE lg_api_http_requests_total counter",
                ]
            )

            for (method, path, status), count in self._request_counts.items():
                metrics.append(
                    f'lg_api_http_requests_total{{project_id="{project_id}", revision_id="{revision_id}", deployment_type="{deployment_type}", method="{method}", path="{path}", status="{status}"}} {count}'
                )

        # Histogram metrics
        if self._histogram_data:
            metrics.extend(
                [
                    "# HELP lg_api_http_requests_latency_seconds HTTP request latency in seconds.",
                    "# TYPE lg_api_http_requests_latency_seconds histogram",
                ]
            )

            for (method, path), hist_data in self._histogram_data.items():
                acc = 0
                for i, bucket_count in enumerate(hist_data["bucket_counts"]):
                    acc += bucket_count
                    bucket_label = self._histogram_bucket_labels[i]
                    metrics.append(
                        f'lg_api_http_requests_latency_seconds_bucket{{project_id="{project_id}", revision_id="{revision_id}", deployment_type="{deployment_type}", method="{method}", path="{path}", le="{bucket_label}"}} {acc}'
                    )

                metrics.extend(
                    [
                        f'lg_api_http_requests_latency_seconds_sum{{project_id="{project_id}", revision_id="{revision_id}", deployment_type="{deployment_type}", method="{method}", path="{path}"}} {hist_data["sum"]:.6f}',
                        f'lg_api_http_requests_latency_seconds_count{{project_id="{project_id}", revision_id="{revision_id}", deployment_type="{deployment_type}", method="{method}", path="{path}"}} {hist_data["count"]}',
                    ]
                )

        return metrics


HTTP_METRICS_COLLECTOR = HTTPMetricsCollector()
