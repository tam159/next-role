"""gRPC client wrapper for LangGraph persistence services."""

import asyncio
import os
import threading
import time

import structlog
from grpc import aio
from grpc_health.v1 import health_pb2, health_pb2_grpc
from langgraph_grpc_common.proto.checkpointer_pb2_grpc import CheckpointerStub
from langgraph_grpc_common.proto.core_api_pb2_grpc import (
    AdminStub,
    AssistantsStub,
    CacheStub,
    CronsStub,
    RunsStub,
    ThreadsStub,
)

from langgraph_api import config
from langgraph_api.logging import worker_config

logger = structlog.stdlib.get_logger(__name__)

_REQUEST_ID_METADATA_KEY = "x-request-id"


class _RequestIdCallDetails:
    """Duck-typed ClientCallDetails with overridden metadata.

    Does not subclass aio.ClientCallDetails because its shape varies across
    grpcio versions (namedtuple vs. regular class), causing __new__ errors.
    gRPC reads call details by attribute access, so duck typing is sufficient.
    """

    __slots__ = (
        "compression",
        "credentials",
        "metadata",
        "method",
        "timeout",
        "wait_for_ready",
    )

    def __init__(
        self, original: aio.ClientCallDetails, metadata: list[tuple[str, str]]
    ) -> None:
        self.method = original.method
        self.timeout = original.timeout
        self.metadata = metadata
        self.credentials = original.credentials
        self.wait_for_ready = original.wait_for_ready
        self.compression = getattr(original, "compression", None)


class _RequestIdInterceptor(
    aio.UnaryUnaryClientInterceptor,
    aio.UnaryStreamClientInterceptor,
    aio.StreamStreamClientInterceptor,
):
    """Injects x-request-id from the current logging context into outgoing gRPC metadata."""

    def _inject(
        self, client_call_details: aio.ClientCallDetails
    ) -> aio.ClientCallDetails:
        ctx = worker_config.get()
        request_id: str | None = ctx.get("request_id") if ctx else None
        if not request_id:
            return client_call_details
        metadata = list(client_call_details.metadata or [])
        metadata.append((_REQUEST_ID_METADATA_KEY, request_id))
        return _RequestIdCallDetails(client_call_details, metadata)

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        return await continuation(self._inject(client_call_details), request)

    async def intercept_unary_stream(self, continuation, client_call_details, request):
        return await continuation(self._inject(client_call_details), request)

    async def intercept_stream_stream(
        self, continuation, client_call_details, request_iterator
    ):
        return await continuation(self._inject(client_call_details), request_iterator)


# Shared gRPC client pools (main thread + thread-local for isolated loops).
_client_pool: "GrpcClientPool | None" = None
_thread_local = threading.local()


GRPC_HEALTHCHECK_TIMEOUT = 5.0
GRPC_INIT_TIMEOUT = 60.0
GRPC_INIT_PROBE_INTERVAL = 0.5


class GrpcClient:
    """gRPC client for LangGraph persistence services."""

    def __init__(
        self,
        server_address: str | None = None,
    ):
        """Initialize the gRPC client.

        Args:
            server_address: The gRPC server address (default: localhost:50051)
        """
        self.server_address = server_address or config.LSD_GRPC_SERVER_ADDRESS
        self._channel: aio.Channel | None = None
        self._assistants_stub: AssistantsStub | None = None
        self._runs_stub: RunsStub | None = None
        self._threads_stub: ThreadsStub | None = None
        self._crons_stub: CronsStub | None = None
        self._admin_stub: AdminStub | None = None
        self._cache_stub: CacheStub | None = None
        self._checkpointer_stub: CheckpointerStub | None = None
        self._health_stub: health_pb2_grpc.HealthStub | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self):
        """Connect to the gRPC server."""
        if self._channel is not None:
            return

        options = [
            ("grpc.max_receive_message_length", config.GRPC_CLIENT_MAX_RECV_MSG_BYTES),
            ("grpc.max_send_message_length", config.GRPC_CLIENT_MAX_SEND_MSG_BYTES),
            (
                "grpc.http2.initial_window_size",
                config.GRPC_CLIENT_HTTP2_INITIAL_WINDOW_SIZE,
            ),
        ]

        self._channel = aio.insecure_channel(
            self.server_address,
            options=options,
            interceptors=[_RequestIdInterceptor()],
        )

        self._assistants_stub = AssistantsStub(self._channel)
        self._runs_stub = RunsStub(self._channel)
        self._threads_stub = ThreadsStub(self._channel)
        self._crons_stub = CronsStub(self._channel)
        self._admin_stub = AdminStub(self._channel)
        self._cache_stub = CacheStub(self._channel)
        self._checkpointer_stub = CheckpointerStub(self._channel)
        self._health_stub = health_pb2_grpc.HealthStub(self._channel)

        await logger.adebug(
            "Connected to gRPC server", server_address=self.server_address
        )

    async def close(self):
        """Close the gRPC connection."""
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._assistants_stub = None
            self._runs_stub = None
            self._threads_stub = None
            self._crons_stub = None
            self._admin_stub = None
            self._cache_stub = None
            self._checkpointer_stub = None
            self._health_stub = None
            await logger.adebug("Closed gRPC connection")

    async def healthcheck(self) -> bool:
        """Check if the gRPC server is healthy.

        Returns:
            True if the server is healthy and serving.

        Raises:
            RuntimeError: If the client is not connected or the server is unhealthy.
        """
        if self._health_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )

        request = health_pb2.HealthCheckRequest(service="")
        response = await self._health_stub.Check(
            request, timeout=GRPC_HEALTHCHECK_TIMEOUT
        )

        if response.status != health_pb2.HealthCheckResponse.SERVING:
            raise RuntimeError(f"gRPC server is not healthy. Status: {response.status}")

        return True

    @property
    def assistants(self) -> AssistantsStub:
        """Get the assistants service stub."""
        if self._assistants_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._assistants_stub

    @property
    def crons(self) -> CronsStub:
        """Get the crons service stub."""
        if self._crons_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._crons_stub

    @property
    def threads(self) -> ThreadsStub:
        """Get the threads service stub."""
        if self._threads_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._threads_stub

    @property
    def runs(self) -> RunsStub:
        """Get the runs service stub."""
        if self._runs_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._runs_stub

    @property
    def admin(self) -> AdminStub:
        """Get the admin service stub."""
        if self._admin_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._admin_stub

    @property
    def cache(self) -> CacheStub:
        """Get the cache service stub."""
        if self._cache_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._cache_stub

    @property
    def checkpointer(self) -> CheckpointerStub:
        """Get the checkpointer service stub."""
        if self._checkpointer_stub is None:
            raise RuntimeError(
                "Client not connected. Use async context manager or call connect() first."
            )
        return self._checkpointer_stub


class GrpcClientPool:
    """Pool of gRPC clients for load distribution."""

    def __init__(self, pool_size: int = 5, server_address: str | None = None):
        self.pool_size = pool_size
        self.server_address = server_address
        self.clients: list[GrpcClient] = []
        self._current_index = 0
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _initialize(self):
        """Initialize the pool of clients."""
        async with self._init_lock:
            if self._initialized:
                return

            await logger.ainfo(
                "Initializing gRPC client pool",
                pool_size=self.pool_size,
                server_address=self.server_address,
            )

            for _ in range(self.pool_size):
                client = GrpcClient(server_address=self.server_address)
                await client.connect()
                self.clients.append(client)

            self._initialized = True
            await logger.ainfo(
                f"gRPC client pool initialized with {self.pool_size} clients"
            )

    async def get_client(self) -> GrpcClient:
        """Get next client using round-robin selection.

        Round-robin without strict locking - slight races are acceptable
        and result in good enough distribution under high load.
        """
        if not self._initialized:
            await self._initialize()

        idx = self._current_index % self.pool_size
        self._current_index = idx + 1
        return self.clients[idx]

    async def close(self):
        """Close all clients in the pool."""
        if self._initialized:
            await logger.ainfo(f"Closing gRPC client pool ({self.pool_size} clients)")
            for client in self.clients:
                await client.close()
            self.clients.clear()
            self._initialized = False


async def get_shared_client() -> GrpcClient:
    """Get a gRPC client from the shared pool.

    Uses a pool of channels for better performance under high concurrency.
    Each channel is a separate TCP connection that can handle ~100-200
    concurrent streams effectively. Pools are scoped per thread/loop to
    avoid cross-loop gRPC channel usage.

    Returns:
        A GrpcClient instance from the pool
    """
    if threading.current_thread() is not threading.main_thread():
        pool = getattr(_thread_local, "grpc_pool", None)
        if pool is None:
            pool = GrpcClientPool(
                pool_size=1,
                server_address=config.LSD_GRPC_SERVER_ADDRESS,
            )
            _thread_local.grpc_pool = pool
        return await pool.get_client()

    global _client_pool
    if _client_pool is None:
        _client_pool = GrpcClientPool(
            pool_size=config.GRPC_CLIENT_POOL_SIZE,
            server_address=config.LSD_GRPC_SERVER_ADDRESS,
        )
    return await _client_pool.get_client()


def _get_go_core_exit_detail() -> str | None:
    """Return a diagnostic message if the Go core-api-grpc process has exited."""
    pid_str = os.environ.get("CORE_API_GRPC_PID")
    if not pid_str:
        return None
    try:
        pid = int(pid_str)
    except ValueError:
        return None

    # In the container entrypoints, the shell starts Go, then execs Python.
    # That leaves Go as a child process, so waitpid can surface its exit status.
    try:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == 0:
            return None
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
            return f"Go core server (PID {pid}) exited with code {code}"
        if os.WIFSIGNALED(status):
            sig = os.WTERMSIG(status)
            return f"Go core server (PID {pid}) was killed by signal {sig}"
        return f"Go core server (PID {pid}) terminated (wait status={status})"
    except ChildProcessError:
        pass

    try:
        os.kill(pid, 0)
        return None
    except ProcessLookupError:
        return (
            f"Go core server (PID {pid}) is no longer running (exit code unavailable)"
        )
    except PermissionError:
        return None


async def wait_until_grpc_ready(
    timeout_seconds: float = GRPC_INIT_TIMEOUT,
    interval_seconds: float = GRPC_INIT_PROBE_INTERVAL,
):
    """Wait for the gRPC server to be ready with retries during startup.

    Args:
        timeout_seconds: Maximum time to wait for the server to be ready.
        interval_seconds: Time to wait between health check attempts.
    Raises:
        RuntimeError: If the server is not ready within the timeout period.
    """
    client = await get_shared_client()
    max_attempts = int(timeout_seconds / interval_seconds)

    await logger.ainfo(
        "Waiting for gRPC server to be ready",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        max_attempts=max_attempts,
    )
    start_time = time.time()
    for attempt in range(max_attempts):
        try:
            await client.healthcheck()
            await logger.ainfo(
                "gRPC server is ready",
                attempt=attempt + 1,
                elapsed_seconds=round(time.time() - start_time, 3),
            )
            return
        except Exception as exc:
            proc_msg = _get_go_core_exit_detail()
            if proc_msg is not None:
                await logger.aerror(
                    "Go core server process has exited",
                    detail=proc_msg,
                    server_address=config.LSD_GRPC_SERVER_ADDRESS,
                )
                raise RuntimeError(
                    f"gRPC server not ready: {proc_msg}. "
                    f"Check Go core server logs above for errors."
                ) from exc

            if attempt >= max_attempts - 1:
                raise RuntimeError(
                    f"gRPC server not ready after {timeout_seconds}s (reached max attempts: {max_attempts})"
                ) from exc
            else:
                await logger.adebug(
                    "Waiting for gRPC server to be ready",
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                )
                await asyncio.sleep(interval_seconds)


async def close_shared_client():
    """Close the shared gRPC client pool."""
    if threading.current_thread() is not threading.main_thread():
        pool = getattr(_thread_local, "grpc_pool", None)
        if pool is not None:
            await pool.close()
            delattr(_thread_local, "grpc_pool")
        return

    global _client_pool
    if _client_pool is not None:
        await _client_pool.close()
        _client_pool = None
