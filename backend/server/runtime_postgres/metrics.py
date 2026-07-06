from typing_extensions import TypedDict

from server.api import config
from server.runtime_postgres import queue


class WorkerMetrics(TypedDict):
    max: int
    active: int
    available: int


class Metrics(TypedDict):
    workers: WorkerMetrics


def get_metrics() -> Metrics:
    workers_max = config.N_JOBS_PER_WORKER
    workers_active = queue.get_num_workers()
    return Metrics(
        workers=WorkerMetrics(
            max=workers_max,
            active=workers_active,
            available=workers_max - workers_active,
        ),
    )
