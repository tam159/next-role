"""Entrypoint: serve the data-plane gRPC services on :50052.

Native servicers are used where implemented; every other RPC forwards to the
Go server at CORE_SERVER_GO_FALLBACK. A failing native import degrades to
forwarding (never breaks the server), so services can be ported incrementally.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from core_server import db, settings
from core_server._forward import install_fallback
from core_server.redis_db import close_redis
from grpc_common.proto import checkpointer_pb2, core_api_pb2
from grpc_common.proto import checkpointer_pb2_grpc as ckpg
from grpc_common.proto import core_api_pb2_grpc as capg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("core_server")

# service name -> (base servicer cls, add-to-server fn, stub ctor, pb module, full name)
SERVICES = {
    "Assistants": (
        capg.AssistantsServicer,
        capg.add_AssistantsServicer_to_server,
        capg.AssistantsStub,
        core_api_pb2,
        "coreApi.Assistants",
    ),
    "Threads": (
        capg.ThreadsServicer,
        capg.add_ThreadsServicer_to_server,
        capg.ThreadsStub,
        core_api_pb2,
        "coreApi.Threads",
    ),
    "Runs": (
        capg.RunsServicer,
        capg.add_RunsServicer_to_server,
        capg.RunsStub,
        core_api_pb2,
        "coreApi.Runs",
    ),
    "Crons": (
        capg.CronsServicer,
        capg.add_CronsServicer_to_server,
        capg.CronsStub,
        core_api_pb2,
        "coreApi.Crons",
    ),
    "Admin": (
        capg.AdminServicer,
        capg.add_AdminServicer_to_server,
        capg.AdminStub,
        core_api_pb2,
        "coreApi.Admin",
    ),
    "Cache": (
        capg.CacheServicer,
        capg.add_CacheServicer_to_server,
        capg.CacheStub,
        core_api_pb2,
        "coreApi.Cache",
    ),
    "Checkpointer": (
        ckpg.CheckpointerServicer,
        ckpg.add_CheckpointerServicer_to_server,
        ckpg.CheckpointerStub,
        checkpointer_pb2,
        "checkpointer.Checkpointer",
    ),
}


def _load_native() -> dict[str, type]:
    """Import native servicer impls; tolerate failures (degrade to forwarding)."""
    native: dict[str, type] = {}
    try:
        from core_server.servicers.assistants import AssistantsServicerImpl

        native["Assistants"] = AssistantsServicerImpl
    except Exception:
        log.exception("native Assistants import failed; will forward to Go")
    try:
        from core_server.servicers.threads import ThreadsServicerImpl

        native["Threads"] = ThreadsServicerImpl
    except Exception:
        log.exception("native Threads import failed; will forward to Go")
    try:
        from core_server.servicers.crons import CronsServicerImpl

        native["Crons"] = CronsServicerImpl
    except Exception:
        log.exception("native Crons import failed; will forward to Go")
    try:
        from core_server.servicers.cache import CacheServicerImpl

        native["Cache"] = CacheServicerImpl
    except Exception:
        log.exception("native Cache import failed; will forward to Go")
    try:
        from core_server.servicers.admin import AdminServicerImpl

        native["Admin"] = AdminServicerImpl
    except Exception:
        log.exception("native Admin import failed; will forward to Go")
    try:
        from core_server.servicers.checkpointer import CheckpointerServicerImpl

        native["Checkpointer"] = CheckpointerServicerImpl
    except Exception:
        log.exception("native Checkpointer import failed; will forward to Go")
    try:
        from core_server.servicers.runs import RunsServicerImpl

        native["Runs"] = RunsServicerImpl
    except Exception:
        log.exception("native Runs import failed; will forward to Go")
    return native


def _go_channel() -> aio.Channel | None:
    if not settings.GO_FALLBACK:
        return None
    return aio.insecure_channel(
        settings.GO_FALLBACK,
        options=[
            ("grpc.max_receive_message_length", settings.MAX_MSG_BYTES),
            ("grpc.max_send_message_length", settings.MAX_MSG_BYTES),
        ],
    )


async def build_server() -> tuple[aio.Server, aio.Channel | None]:
    server = aio.server(
        options=[
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_recv_ping_interval_without_data_ms", 50000),
            ("grpc.http2.max_ping_strikes", 2),
            ("grpc.max_receive_message_length", settings.MAX_MSG_BYTES),
            ("grpc.max_send_message_length", settings.MAX_MSG_BYTES),
        ],
    )
    go = _go_channel()
    native = _load_native()

    for name, (base_cls, add_fn, stub_ctor, pb_mod, _full) in SERVICES.items():
        impl_cls = native.get(name, base_cls)
        inst = impl_cls()
        desc = pb_mod.DESCRIPTOR.services_by_name[name]
        if go is not None:
            forwarded = install_fallback(inst, base_cls, stub_ctor(go), desc)
        else:
            forwarded = []
        native_methods = [m.name for m in desc.methods if m.name not in forwarded]
        add_fn(inst, server)
        log.info(
            "service %-12s native=%s forwarded=%d",
            name,
            native_methods if name in native else "[]",
            len(forwarded),
        )

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    server.add_insecure_port(settings.BIND)
    await server.start()

    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    for _name, (_b, _a, _s, _m, full) in SERVICES.items():
        health_servicer.set(full, health_pb2.HealthCheckResponse.SERVING)
    return server, go


async def main() -> None:
    try:
        await db.open_pool()
        log.info("postgres pool opened: %s", settings.POSTGRES_URI)
    except Exception:
        log.exception(
            "postgres pool open failed (native services will error; forwarding still works)",
        )

    server, go = await build_server()
    log.info(
        "core_server listening on %s | go_fallback=%s",
        settings.BIND,
        settings.GO_FALLBACK or "(disabled)",
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()

    log.info("shutting down")
    await server.stop(5.0)
    if go is not None:
        await go.close()
    await close_redis()
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
