"""gRPC server for Python-side services (encryption, checkpointing).

This module provides a gRPC server that runs alongside the Starlette HTTP server
to expose Python implementations of encryption and checkpointing to the Go server.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import grpc
import grpc.aio
import structlog
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from langgraph_grpc_common.proto.checkpointer_pb2_grpc import (
    add_CheckpointerServicer_to_server,
)
from langgraph_grpc_common.proto.encryption_pb2_grpc import (
    add_EncryptionServicer_to_server,
)

from langgraph_api import config
from langgraph_api.grpc.servicers.checkpointer import CheckpointerServicerImpl
from langgraph_api.grpc.servicers.encryption import EncryptionServicerImpl
from langgraph_api.utils.network import format_hostport, get_healthcheck_target_host

logger = structlog.stdlib.get_logger(__name__)

# Liveness check settings
PYTHON_GRPC_HEALTHCHECK_TIMEOUT = 5.0
PYTHON_GRPC_INIT_TIMEOUT = 10.0
PYTHON_GRPC_INIT_PROBE_INTERVAL = 0.5

# Global server instance for shutdown coordination
_server: grpc.aio.Server | None = None
_server_task: asyncio.Task | None = None


async def start_python_grpc_server(
    port: int | None = None,
    host: str | None = None,
) -> grpc.aio.Server:
    """Start the Python gRPC server for encryption and checkpointing services.

    This server exposes:
    - Checkpointer service: checkpoint persistence operations (if custom checkpointer is configured)
    - Encryption service: custom encryption/decryption operations (if custom encryption is configured)

    The server binds to loopback (127.0.0.1) by default since it's only
    meant to be called by the co-located Go server. Set PYTHON_GRPC_BIND_HOST=0.0.0.0
    to allow external connections (e.g., for CI testing with Docker).

    Args:
        port: Port to bind to (default: config.PYTHON_GRPC_SERVER_PORT)
        host: Host to bind to (default: config.PYTHON_GRPC_BIND_HOST, 127.0.0.1)

    Returns:
        The started gRPC server instance
    """
    global _server

    if port is None:
        port = config.PYTHON_GRPC_SERVER_PORT
    if host is None:
        host = config.PYTHON_GRPC_BIND_HOST

    await logger.ainfo(
        f"Starting Python gRPC server on {host}:{port}",
        port=port,
        host=host,
        encryption_enabled=bool(config.LANGGRAPH_ENCRYPTION),
        custom_checkpointer_enabled=config.USE_CUSTOM_CHECKPOINTER,
    )

    # Keepalive settings are permissive to tolerate varied client ping intervals.
    server = grpc.aio.server(
        options=[
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_recv_ping_interval_without_data_ms", 50000),  # 50s
            ("grpc.http2.max_ping_strikes", 2),
            (
                "grpc.max_receive_message_length",
                config.LSD_GRPC_SERVER_MAX_RECV_MSG_BYTES,
            ),
            ("grpc.max_send_message_length", config.LSD_GRPC_SERVER_MAX_SEND_MSG_BYTES),
        ],
    )

    # Register health service for readiness checks
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # Register Checkpointer service (only if custom checkpointer is configured)
    if config.USE_CUSTOM_CHECKPOINTER:
        checkpointer_servicer = CheckpointerServicerImpl()
        add_CheckpointerServicer_to_server(checkpointer_servicer, server)
        await logger.ainfo("Registered Checkpointer service")

    # Register Encryption service (only if custom encryption is configured)
    if config.LANGGRAPH_ENCRYPTION:
        encryption_servicer = EncryptionServicerImpl()
        add_EncryptionServicer_to_server(encryption_servicer, server)
        await logger.ainfo("Registered Encryption service")

    bind_address = format_hostport(host, port)
    resolved_port = server.add_insecure_port(bind_address)

    await server.start()
    _server = server

    # Mark all services as serving
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    await logger.ainfo(
        f"Python gRPC server started on {host}:{resolved_port}",
        port=resolved_port,
        host=host,
    )

    return server


async def serve_forever():
    """Run the gRPC server until termination.

    This is a blocking call that waits for the server to terminate.
    Use stop_python_grpc_server() to gracefully shut down.
    """
    global _server
    if _server is not None:
        await _server.wait_for_termination()


async def stop_python_grpc_server(grace_period: float = 15.0):
    """Stop the Python gRPC server gracefully.

    Args:
        grace_period: Time in seconds to wait for in-flight RPCs to complete
    """
    global _server, _server_task

    if _server is not None:
        await logger.ainfo("Stopping Python gRPC server", grace_period=grace_period)
        await _server.stop(grace_period)
        _server = None
        await logger.ainfo("Python gRPC server stopped")

    if _server_task is not None:
        _server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _server_task
        _server_task = None


async def wait_until_python_grpc_ready(
    timeout_seconds: float = PYTHON_GRPC_INIT_TIMEOUT,
    interval_seconds: float = PYTHON_GRPC_INIT_PROBE_INTERVAL,
):
    """Wait for the Python gRPC server to be ready with retries during startup.

    Args:
        timeout_seconds: Maximum time to wait for the server to be ready.
        interval_seconds: Time to wait between health check attempts.

    Raises:
        RuntimeError: If the server is not ready within the timeout period.
    """
    host = get_healthcheck_target_host(config.PYTHON_GRPC_BIND_HOST)
    port = config.PYTHON_GRPC_SERVER_PORT
    address = format_hostport(host, port)
    max_attempts = int(timeout_seconds / interval_seconds)

    await logger.ainfo(
        "Waiting for Python gRPC server to be ready",
        address=address,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        max_attempts=max_attempts,
    )

    start_time = time.time()
    channel = grpc.aio.insecure_channel(address)
    health_stub = health_pb2_grpc.HealthStub(channel)

    try:
        for attempt in range(max_attempts):
            try:
                request = health_pb2.HealthCheckRequest(service="")
                response = await health_stub.Check(
                    request, timeout=PYTHON_GRPC_HEALTHCHECK_TIMEOUT
                )
                if response.status == health_pb2.HealthCheckResponse.SERVING:
                    await logger.ainfo(
                        "Python gRPC server is ready",
                        attempt=attempt + 1,
                        elapsed_seconds=round(time.time() - start_time, 3),
                    )
                    return
            except Exception as exc:
                if attempt >= max_attempts - 1:
                    raise RuntimeError(
                        f"Python gRPC server not ready after {timeout_seconds}s "
                        f"(reached max attempts: {max_attempts})"
                    ) from exc
                else:
                    await logger.adebug(
                        "Waiting for Python gRPC server to be ready",
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        exc_info=True,
                    )
                    await asyncio.sleep(interval_seconds)
    finally:
        await channel.close()


async def run_python_grpc_server(
    port: int | None = None,
    host: str | None = None,
):
    """Start and run the Python gRPC server.

    This function starts the server and waits for termination.
    It's meant to be run as a background task.

    Args:
        port: Port to bind to (default: config.PYTHON_GRPC_SERVER_PORT)
        host: Host to bind to (default: config.PYTHON_GRPC_BIND_HOST)
    """
    try:
        await start_python_grpc_server(port=port, host=host)
        await serve_forever()
    except asyncio.CancelledError:
        await logger.ainfo("Python gRPC server task cancelled")
    except Exception as e:
        await logger.aerror("Python gRPC server error", error=str(e), exc_info=True)
        raise
