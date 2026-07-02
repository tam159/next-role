"""Transparent fallback: forward not-yet-native RPCs to the original Go server.

For each method declared on a service, if the servicer instance hasn't overridden
it with a native implementation, attach a forwarder (matching the method's
streaming cardinality) that relays the call to the corresponding Go stub.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.stdlib.get_logger(__name__)


def _make_forwarder(stub_method, client_streaming: bool, server_streaming: bool):
    if not client_streaming and not server_streaming:

        async def fwd(request, context):  # unary-unary
            return await stub_method(request)

    elif not client_streaming and server_streaming:

        async def fwd(request, context):  # unary-stream
            async for resp in stub_method(request):
                yield resp

    elif client_streaming and not server_streaming:

        async def fwd(request_iterator, context):  # stream-unary
            return await stub_method(request_iterator)

    else:

        async def fwd(request_iterator, context):  # stream-stream (bidi)
            call = stub_method()

            async def _pump():
                async for req in request_iterator:
                    await call.write(req)
                await call.done_writing()

            pump = asyncio.create_task(_pump())
            try:
                async for resp in call:
                    yield resp
            finally:
                pump.cancel()

    return fwd


def install_fallback(servicer, base_cls, go_stub, service_descriptor) -> list[str]:
    """Attach forwarders for every RPC not natively overridden on `servicer`.

    Returns the list of method names that were wired to forward (for logging).
    """
    forwarded: list[str] = []
    for method in service_descriptor.methods:
        name = method.name
        # Native if the subclass overrode the base servicer's stub method.
        if getattr(type(servicer), name, None) is not getattr(base_cls, name, None):
            continue
        stub_method = getattr(go_stub, name)
        setattr(
            servicer,
            name,
            _make_forwarder(
                stub_method,
                method.client_streaming,
                method.server_streaming,
            ),
        )
        forwarded.append(name)
    return forwarded
