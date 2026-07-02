from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog

from langgraph_api import __version__, config

if TYPE_CHECKING:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality,
        MetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
else:
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
        from opentelemetry.sdk.metrics.export import (
            AggregationTemporality,
            MetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource

        OTEL_AVAILABLE = True
    except ModuleNotFoundError:
        OTLPMetricExporter = None
        MeterProvider = None
        PeriodicExportingMetricReader = None
        Resource = None
        AggregationTemporality = None
        Counter = object
        Histogram = object
        MetricExporter = object
        OTEL_AVAILABLE = False

logger = structlog.stdlib.get_logger(__name__)

SERVICE_NAME = "lsd_langgraph_api"
DD_OTEL_METRIC_CONFIG = (
    '{"resource_attributes_as_tags":true,"histograms":{"mode":"distributions"}}'
)
METRIC_NAME_PREFIX = config.METRIC_PREFIX

METRIC_TIER_CRITICAL = 1
METRIC_TIER_INFO = 2
METRIC_TIER_DEBUG = 3
METRIC_TIER_DEEP_DEBUG = 4

MetricType = Literal["counter", "histogram", "latency", "gauge"]


@dataclass(frozen=True, slots=True)
class MetricDef:
    metric_type: MetricType
    name: str
    tier: int


def def_counter(name: str, tier: int) -> MetricDef:
    return MetricDef(
        metric_type="counter", name=f"{METRIC_NAME_PREFIX}{name}", tier=tier
    )


def def_histogram(name: str, tier: int) -> MetricDef:
    return MetricDef(
        metric_type="histogram", name=f"{METRIC_NAME_PREFIX}{name}", tier=tier
    )


def def_latency(name: str, tier: int) -> MetricDef:
    return MetricDef(
        metric_type="latency", name=f"{METRIC_NAME_PREFIX}{name}", tier=tier
    )


def def_gauge(name: str, tier: int) -> MetricDef:
    return MetricDef(metric_type="gauge", name=f"{METRIC_NAME_PREFIX}{name}", tier=tier)


# Pre-defined counter metrics.
COUNTER_STREAMING_DATA_LOSS = def_counter(
    "streaming_data_loss_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_ATTEMPT_STARTED = def_counter(
    "run_attempt_started_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_SUCCESS = def_counter("run_success_counter", METRIC_TIER_CRITICAL)
COUNTER_RUN_CANCELED_BY_REQUEST = def_counter(
    "run_canceled_by_request_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_FAILED_RETRIABLE = def_counter(
    "run_failed_retriable_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_FAILED_AFTER_RETRY = def_counter(
    "run_failed_after_retry_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_EXCEED_MAX_ATTEMPTS_AT_START = def_counter(
    "run_exceed_max_attempts_at_start_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_ABANDONED_BY_SHUTDOWN = def_counter(
    "run_abandoned_by_shutdown_counter", METRIC_TIER_CRITICAL
)
COUNTER_RUN_SET_STATUS_ERROR = def_counter(
    "run_set_status_error_counter", METRIC_TIER_CRITICAL
)
COUNTER_GRAPH_RECURSION_LIMIT_ERROR = def_counter(
    "graph_recursion_limit_error_counter", METRIC_TIER_INFO
)
COUNTER_FAILED_TO_FETCH_RUNS = def_counter(
    "failed_to_fetch_runs_counter", METRIC_TIER_CRITICAL
)
COUNTER_SERVER_STARTED = def_counter("server_started_counter", METRIC_TIER_INFO)
COUNTER_SERVER_REQUESTED_TO_STOP = def_counter(
    "server_requested_to_stop_counter", METRIC_TIER_INFO
)
COUNTER_SERVER_STOPPED = def_counter("server_stopped_counter", METRIC_TIER_INFO)
COUNTER_PROTOCOL_V2_BUFFER_EVICTED = def_counter(
    "protocol_v2_buffer_evicted_counter", METRIC_TIER_INFO
)
COUNTER_PROTOCOL_V2_EVENT_EMITTED = def_counter(
    "protocol_v2_event_emitted_counter", METRIC_TIER_DEBUG
)
COUNTER_PROTOCOL_V2_RESUME_GAP = def_counter(
    "protocol_v2_resume_gap_counter", METRIC_TIER_INFO
)
COUNTER_PROTOCOL_V2_TRANSPORT_SEND_FAILURE = def_counter(
    "protocol_v2_transport_send_failure_counter", METRIC_TIER_INFO
)

# Pre-defined latency metrics.
LATENCY_RUN_EXECUTION = def_latency("run_execution_latency", METRIC_TIER_INFO)
LATENCY_RUN_QUEUE_WAIT_TIME_1ST_ATTEMPT = def_latency(
    "run_queue_wait_time_1st_attempt", METRIC_TIER_INFO
)
LATENCY_RUN_QUEUE_WAIT_TIME_RETRY_ATTEMPT = def_latency(
    "run_queue_wait_time_retry_attempt", METRIC_TIER_INFO
)
LATENCY_STREAM_PUBLISH = def_latency("stream_publish_latency", METRIC_TIER_INFO)

# Pre-defined gauge metrics.
GAUGE_WORKERS_ACTIVE = def_gauge("workers_active", METRIC_TIER_CRITICAL)
GAUGE_WORKERS_AVAILABLE = def_gauge("workers_available", METRIC_TIER_CRITICAL)
GAUGE_PUBLISH_QUEUE_AVAILABILITY = def_gauge(
    "publish_queue_availability", METRIC_TIER_CRITICAL
)
# Protocol v2 sessions retain a bounded replay buffer per run. Track the
# observed occupancy so operators can tune LSD_PROTOCOL_V2_BUFFER_SIZE before
# reconnects start seeing resume gaps.
GAUGE_PROTOCOL_V2_BUFFER_SIZE = def_gauge("protocol_v2_buffer_size", METRIC_TIER_DEBUG)


# Pre-defined histogram metrics.
HISTOGRAM_STREAM_DATA_SIZE = def_histogram("stream_data_size_bytes", METRIC_TIER_DEBUG)
HISTOGRAM_PROTOCOL_V2_REPLAYED_EVENTS = def_histogram(
    "protocol_v2_replayed_events", METRIC_TIER_DEBUG
)


def _normalize_emitting_tier(value: int) -> int:
    if value < METRIC_TIER_CRITICAL:
        return METRIC_TIER_CRITICAL
    if value > METRIC_TIER_DEEP_DEBUG:
        return METRIC_TIER_DEEP_DEBUG
    return value


class _FilteringExporter(MetricExporter):
    """Wrapper that skips export when there are no metric points."""

    def __init__(self, exporter: MetricExporter):
        super().__init__()
        self._exporter = exporter
        self._preferred_temporality = getattr(exporter, "_preferred_temporality", {})
        self._preferred_aggregation = getattr(exporter, "_preferred_aggregation", {})

    def export(
        self,
        metrics_data: Any,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> Any:
        if not metrics_data or not metrics_data.resource_metrics:
            return None
        for resource_metric in metrics_data.resource_metrics:
            if resource_metric.scope_metrics:
                for scope_metric in resource_metric.scope_metrics:
                    if scope_metric.metrics:
                        return self._exporter.export(
                            metrics_data, timeout_millis, **kwargs
                        )
        return None

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        self._exporter.shutdown(timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return self._exporter.force_flush(timeout_millis)


class DatadogMetricsReporter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initialized = False
        self._enabled = False
        self._meter_provider: MeterProvider | None = None
        self._meter = None
        self._max_tier = _normalize_emitting_tier(config.METRIC_MAX_EMITTING_TIER)
        self._instruments: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._initialized = True

            if not config.DATADOG_METRICS_ENABLED:
                logger.info("Datadog metrics disabled (no DD API key configured)")
                return
            if not OTEL_AVAILABLE:
                logger.warning(
                    "Datadog metrics disabled because OpenTelemetry dependencies are not installed"
                )
                return

            try:
                resource = Resource.create(
                    {
                        "service.name": SERVICE_NAME,
                        "host.id": os.getenv("HOSTNAME", ""),
                        # Not in public docs: these LANGSMITH_* vars are set by SaaS control plane
                        "api_version": os.getenv("LANGSMITH_LANGGRAPH_API_VERSION")
                        or __version__,
                        "project_id": os.getenv("LANGSMITH_HOST_PROJECT_ID", ""),
                        "k8s.deployment.name": os.getenv(
                            "LANGSMITH_HOST_PROJECT_NAME", ""
                        ),
                        "has_deepagents": "true"
                        if os.getenv("DEEPAGENTS_VERSION", "") != ""
                        else "false",
                        "deployment_type": os.getenv("LSD_DEPLOYMENT_TYPE", ""),
                    }
                )
                base_exporter = OTLPMetricExporter(
                    endpoint=f"https://{config.LSD_DD_ENDPOINT}/v1/metrics",
                    headers={
                        "dd-api-key": config.LSD_DD_API_KEY,
                        "dd-otel-metric-config": DD_OTEL_METRIC_CONFIG,
                    },
                    preferred_temporality={
                        Counter: AggregationTemporality.DELTA,
                        Histogram: AggregationTemporality.DELTA,
                    },
                )
                # Burn after reading: remove DD keys from env so user code
                # and child processes cannot access them.
                for key in (
                    "LSD_DD_API_KEY",
                    "LSD_DD_ENDPOINT",
                ):
                    os.environ.pop(key, None)

                reader = PeriodicExportingMetricReader(
                    _FilteringExporter(base_exporter),
                    export_interval_millis=10_000,
                )
                self._meter_provider = MeterProvider(
                    resource=resource, metric_readers=[reader]
                )
                self._meter = self._meter_provider.get_meter(SERVICE_NAME)
                self._enabled = True
                logger.info(
                    "Initialized Datadog metrics reporter",
                    endpoint=f"https://{config.LSD_DD_ENDPOINT}/v1/metrics",
                    metric_prefix=METRIC_NAME_PREFIX,
                    max_emitting_tier=self._max_tier,
                )
                logger.info("Datadog metrics reporter initialized")
            except Exception:
                self._enabled = False
                self._meter_provider = None
                self._meter = None
                logger.exception("Failed to initialize Datadog metrics reporter")
                raise

    def shutdown(self) -> None:
        with self._lock:
            if self._meter_provider:
                try:
                    self._meter_provider.shutdown()
                except Exception:
                    logger.exception("Failed to shutdown Datadog metrics reporter")
                finally:
                    self._meter_provider = None
                    self._meter = None
            self._enabled = False
            self._initialized = False
            self._instruments.clear()

    def _instrument_name(self, metric_name: str) -> str:
        return metric_name

    def _tier_enabled(self, tier: int) -> bool:
        return _normalize_emitting_tier(tier) <= self._max_tier

    def _get_or_create_instrument(self, metric: MetricDef):
        name = self._instrument_name(metric.name)
        instrument = self._instruments.get(name)
        if instrument is not None:
            return instrument
        if metric.metric_type == "counter":
            instrument = self._meter.create_counter(name=name)
        elif metric.metric_type in {"histogram", "latency"}:
            instrument = self._meter.create_histogram(name=name)
        elif metric.metric_type == "gauge":
            instrument = self._meter.create_gauge(name=name)
        else:
            raise ValueError(f"Unsupported metric type: {metric.metric_type}")
        self._instruments[name] = instrument
        return instrument

    def inc_counter(
        self,
        metric: MetricDef,
        value: int = 1,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if metric.metric_type != "counter":
            raise ValueError(f"{metric.name} is not a counter metric")
        if not self._enabled or not self._meter or not self._tier_enabled(metric.tier):
            return
        instrument = self._get_or_create_instrument(metric)
        try:
            instrument.add(value, attributes or {})
        except Exception:
            logger.warning("Failed to add Datadog counter", metric_name=metric.name)

    def record_histogram(
        self,
        metric: MetricDef,
        value: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if metric.metric_type != "histogram":
            raise ValueError(f"{metric.name} is not a histogram metric")
        if not self._enabled or not self._meter or not self._tier_enabled(metric.tier):
            return
        instrument = self._get_or_create_instrument(metric)
        try:
            instrument.record(value, attributes or {})
        except Exception:
            logger.warning(
                "Failed to record Datadog histogram", metric_name=metric.name
            )

    def record_latency(
        self,
        metric: MetricDef,
        duration_seconds: float | timedelta,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if metric.metric_type != "latency":
            raise ValueError(f"{metric.name} is not a latency metric")
        if isinstance(duration_seconds, timedelta):
            seconds = duration_seconds.total_seconds()
        else:
            seconds = float(duration_seconds)
        value = seconds * 1000
        if not self._enabled or not self._meter or not self._tier_enabled(metric.tier):
            return
        instrument = self._get_or_create_instrument(metric)
        try:
            instrument.record(value, attributes or {})
        except Exception:
            logger.warning("Failed to record Datadog latency", metric_name=metric.name)

    def record_gauge(
        self,
        metric: MetricDef,
        value: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if metric.metric_type != "gauge":
            raise ValueError(f"{metric.name} is not a gauge metric")
        if not self._enabled or not self._meter or not self._tier_enabled(metric.tier):
            return
        instrument = self._get_or_create_instrument(metric)
        try:
            instrument.set(value, attributes or {})
        except Exception:
            logger.warning("Failed to record Datadog gauge", metric_name=metric.name)

    @contextmanager
    def track_latency_ms(
        self,
        metric: MetricDef,
        attributes: dict[str, Any] | None = None,
    ):
        if not self._enabled or not self._meter or not self._tier_enabled(metric.tier):
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record_latency(
                metric,
                time.perf_counter() - start,
                attributes=attributes,
            )


_metrics_reporter: DatadogMetricsReporter | None = None


def get_datadog_metrics_reporter() -> DatadogMetricsReporter:
    global _metrics_reporter
    if _metrics_reporter is None:
        _metrics_reporter = DatadogMetricsReporter()
    return _metrics_reporter
