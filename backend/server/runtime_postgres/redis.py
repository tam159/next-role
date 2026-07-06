import asyncio
import base64
import binascii
import itertools
import json
import logging
import threading
from collections.abc import Callable
from typing import cast

import google.auth.transport.requests
import structlog
from google.oauth2 import service_account
from redis.asyncio import Redis, RedisCluster
from redis.asyncio.client import PubSub
from redis.asyncio.cluster import ClusterNode, NodesManager
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.commands.core import AsyncPubSubCommands
from redis.credentials import CredentialProvider
from redis.event import (
    AfterPubSubConnectionInstantiationEvent,
    ClientType,
    EventDispatcher,
)

from server.api.config import (
    REDIS_CLUSTER,
    REDIS_CONNECT_TIMEOUT,
    REDIS_GCP_SERVICE_ACCOUNT_JSON,
    REDIS_HEALTH_CHECK_INTERVAL,
    REDIS_MAX_CONNECTIONS,
    REDIS_TLS_CA_CERT,
    REDIS_URI,
    STATS_INTERVAL_SECS,
)
from server.api.config import (
    REDIS_KEY_PREFIX as _REDIS_KEY_PREFIX,
)
from server.api.schema import RedisPoolStats

logger = structlog.stdlib.get_logger(__name__)


class LanggraphRedisCluster(RedisCluster, AsyncPubSubCommands):
    pass


class ClusterPubSub(PubSub):
    """
    Redis-py doesn't have an async cluster pubsub out of the box, so we implement it here.
    This builds on the async PubSub class, but uses a ClusterNode to manage connections instead of the connection pool.

    Other changes include:
    - ClusterNode doesn't have an encoder, so use the one from the cluster client
    - We dispatch a slightly malformed event because we don't have a connection pool (don't think this is used anywhere)
    """

    def __init__(
        self,
        cluster_node: ClusterNode,
        cluster_client: LanggraphRedisCluster,
        shard_hint: str | None = None,
        ignore_subscribe_messages: bool = False,
        encoder=None,
        push_handler_func: Callable | None = None,
        event_dispatcher: EventDispatcher | None = None,
    ):
        if event_dispatcher is None:
            self._event_dispatcher = EventDispatcher()
        else:
            self._event_dispatcher = event_dispatcher
        self.cluster_node = cluster_node
        self.cluster_client = cluster_client
        self.shard_hint = shard_hint
        self.ignore_subscribe_messages = ignore_subscribe_messages
        self.connection = None
        self.encoder = encoder
        self.push_handler_func = push_handler_func
        if self.encoder is None:
            self.encoder = self.cluster_client.get_encoder()
        if self.encoder.decode_responses:
            self.health_check_response = [
                ["pong", self.HEALTH_CHECK_MESSAGE],
                self.HEALTH_CHECK_MESSAGE,
            ]
        else:
            self.health_check_response = [
                [b"pong", self.encoder.encode(self.HEALTH_CHECK_MESSAGE)],
                self.encoder.encode(self.HEALTH_CHECK_MESSAGE),
            ]
        if self.push_handler_func is None:
            _set_info_logger()
        self.channels = {}
        self.pending_unsubscribe_channels = set()
        self.patterns = {}
        self.pending_unsubscribe_patterns = set()
        self._lock = asyncio.Lock()

    async def aclose(self):
        if not hasattr(self, "connection"):
            return
        async with self._lock:
            if self.connection:
                await self.connection.disconnect()
                self.connection.deregister_connect_callback(self.on_connect)
                self.cluster_node.release(self.connection)
                self.connection = None
            self.channels = {}
            self.pending_unsubscribe_channels = set()
            self.patterns = {}
            self.pending_unsubscribe_patterns = set()

    async def connect(self):
        """
        Ensure that the PubSub is connected
        """
        if self.connection is None:
            self.connection = self.cluster_node.acquire_connection()
            self.connection.register_connect_callback(self.on_connect)
        else:
            await self.connection.connect()
        if self.push_handler_func is not None:
            self.connection._parser.set_pubsub_push_handler(self.push_handler_func)
        self._event_dispatcher.dispatch(
            AfterPubSubConnectionInstantiationEvent(
                self.connection,
                self.cluster_node,
                ClientType.ASYNC,
                self._lock,
            ),
        )


# The main Redis client, set up at startup. This is a module-level global so it
# can be shared across the entire application. We use a single client (and its
# underlying connection pool) for the lifetime of the process, rather than
# creating a new one per request, to amortize connection setup costs and to
# keep the pool warm. In cluster mode this is a RedisCluster instance, which
# manages a separate connection pool per node.
_aredis: Redis | LanggraphRedisCluster
_stats_task: asyncio.Task

# Thread-local storage for Redis clients used outside the main event loop thread
# (e.g. background threads), since async Redis clients are bound to a loop.
_thread_local = threading.local()

# The client class to instantiate, depending on whether we're in cluster mode.
_cls_cl = Redis if not REDIS_CLUSTER else LanggraphRedisCluster


def _decode_base64_env(env_name: str, value: str) -> str:
    """Decode a base64-encoded env var, tolerating whitespace and line wraps.

    Base64 is required (rather than raw PEM/JSON) so the payload ships cleanly
    through env/YAML plumbing without quoting or newline-handling issues.
    """
    compact = "".join(value.split())
    try:
        return base64.b64decode(compact, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{env_name} must be base64-encoded") from exc


# Decode the optional TLS CA certificate once at import time.
_tls_ca_cert_pem: str = (
    _decode_base64_env("REDIS_TLS_CA_CERT", REDIS_TLS_CA_CERT) if REDIS_TLS_CA_CERT else ""
)


class _GCPIAMCredentialProvider(CredentialProvider):
    """Redis credential provider backed by a GCP service account key JSON.

    Mints OAuth2 access tokens on demand for IAM-authed Memorystore for Redis
    Cluster connections. google-auth caches the underlying token and only
    performs the network refresh when it's close to expiration.
    """

    _SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, service_account_json: str) -> None:
        info = json.loads(
            _decode_base64_env("REDIS_GCP_SERVICE_ACCOUNT_JSON", service_account_json),
        )
        self._creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=[self._SCOPE],
        )
        self._lock = threading.Lock()

    def _refresh_sync(self) -> str:
        with self._lock:
            if not self._creds.valid:
                self._creds.refresh(google.auth.transport.requests.Request())
            return cast(str, self._creds.token)

    def get_credentials(self) -> tuple[str, str]:
        return ("default", self._refresh_sync())

    async def get_credentials_async(self) -> tuple[str, str]:
        token = await asyncio.to_thread(self._refresh_sync)
        return ("default", token)


_credential_provider: CredentialProvider | None = (
    _GCPIAMCredentialProvider(REDIS_GCP_SERVICE_ACCOUNT_JSON)
    if REDIS_GCP_SERVICE_ACCOUNT_JSON
    else None
)


def _create_redis_client(uri: str) -> Redis | LanggraphRedisCluster:
    kwargs = {}
    if _tls_ca_cert_pem:
        kwargs["ssl_ca_data"] = _tls_ca_cert_pem
    if _credential_provider is not None:
        kwargs["credential_provider"] = _credential_provider
    return _cls_cl.from_url(
        uri,
        max_connections=REDIS_MAX_CONNECTIONS,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
        socket_keepalive=True,
        decode_responses=False,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        retry=Retry(
            retries=3,
            backoff=ExponentialWithJitterBackoff(base=0.1, cap=2.0),
        ),
        **kwargs,
    )


async def start_redis() -> None:
    global _aredis, _stats_task
    try:
        _aredis = _create_redis_client(REDIS_URI)

        await asyncio.wait_for(_aredis.ping(), timeout=5.0)
    except TimeoutError:
        logger.exception("Redis ping timed out", redis_uri=REDIS_URI)
    except Exception as e:
        logger.exception("Redis ping failed", error=str(e), redis_uri=REDIS_URI)

    _stats_task = asyncio.create_task(stats_loop())


async def stop_redis() -> None:
    _stats_task.cancel("Shutting down Redis")
    await _aredis.aclose()


async def stats_loop() -> None:
    while True:
        pool_stats = redis_stats()
        await logger.ainfo("Redis pool stats", **pool_stats)
        await asyncio.sleep(STATS_INTERVAL_SECS)


def redis_stats() -> RedisPoolStats:
    """Get stats for the main Redis client"""
    if REDIS_CLUSTER:
        idle_connections = 0
        in_use_connections = 0
        max_connections = 0
        max_connections_per_node = REDIS_MAX_CONNECTIONS
        for node in cast(
            LanggraphRedisCluster,
            _aredis,
        ).nodes_manager.nodes_cache.values():
            idle_connections += len(node._free)
            in_use_connections += len(node._connections) - len(node._free)
            max_connections += node.max_connections
        return RedisPoolStats(
            idle_connections=idle_connections,
            in_use_connections=in_use_connections,
            max_connections=max_connections,
            max_connections_per_node=max_connections_per_node,
        )
    redis_client = cast(Redis, _aredis)
    return RedisPoolStats(
        idle_connections=len(redis_client.connection_pool._available_connections),
        in_use_connections=len(redis_client.connection_pool._in_use_connections),
        max_connections=redis_client.connection_pool.max_connections,
    )


def get_redis() -> Redis | LanggraphRedisCluster:
    if threading.current_thread() is threading.main_thread():
        return _aredis

    if not hasattr(_thread_local, "redis_client"):
        _thread_local.redis_client = _create_redis_client(REDIS_URI)
        logger.info(
            "Created new thread-local Redis client",
            thread_name=threading.current_thread().name,
        )
    return _thread_local.redis_client


async def get_pubsub(
    *,
    channels: list[str] | None = None,
    patterns: list[str] | None = None,
) -> PubSub:
    channels = channels or []
    patterns = patterns or []

    if not channels and not patterns:
        raise ValueError("At least one channel or pattern must be provided")

    redis = get_redis()
    if REDIS_CLUSTER:
        await redis.initialize()

        slot = None
        cluster = cast(LanggraphRedisCluster, redis)
        for key in [*channels, *patterns]:
            cur_slot = cluster.keyslot(key)
            if slot is None:
                slot = cur_slot
            elif slot != cur_slot:
                raise ValueError("All channels/patterns must hash to the same slot")

        nodes_manager = cast(NodesManager, cluster.nodes_manager)
        node = nodes_manager.get_node_from_slot(int(slot))
        pubsub = ClusterPubSub(node, cluster)
    else:
        pubsub = cast(Redis, redis).pubsub()

    if channels:
        await pubsub.subscribe(*channels)

    if patterns:
        await pubsub.psubscribe(*patterns)

    for _ in itertools.chain(channels, patterns):
        message = await pubsub.get_message(timeout=0.5)
        if message is None:
            await logger.awarning(
                "Timed out waiting for acknowledgement of subscription, continuing anyway",
            )
            continue
        await logger.adebug(
            "Received acknowledgement of subscription",
            message=message,
        )

    return pubsub


def _set_info_logger():
    if "push_response" in logging.root.manager.loggerDict:
        logger = logging.getLogger("push_response")
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)


REDIS_KEY_PREFIX = f"{_REDIS_KEY_PREFIX.rstrip(':')}:" if _REDIS_KEY_PREFIX else ""

RUN_ID_SEGMENT = "{{{}}}" if REDIS_CLUSTER else "{}"
RUN_ID_SEGMENT_NON_HASH = "{}"
THREAD_ID_SEGMENT = "{{{}}}" if REDIS_CLUSTER else "{}"
LICENSE_KEY_SEGMENT = "{{{}}}" if REDIS_CLUSTER else "{}"

CHANNEL_RUN_STREAM = (
    f"{REDIS_KEY_PREFIX}thread:{THREAD_ID_SEGMENT}:run:{RUN_ID_SEGMENT_NON_HASH}:stream"
)
CHANNEL_RUN_STREAM_OLD = f"{REDIS_KEY_PREFIX}run:{RUN_ID_SEGMENT}:stream"

STREAM_THREAD_CACHE = f"{REDIS_KEY_PREFIX}thread:{THREAD_ID_SEGMENT}:cache"

CHANNEL_RUN_CONTROL = (
    f"{REDIS_KEY_PREFIX}thread:{THREAD_ID_SEGMENT}:run:{RUN_ID_SEGMENT_NON_HASH}:control"
)
CHANNEL_RUN_CONTROL_OLD = f"{REDIS_KEY_PREFIX}run:{RUN_ID_SEGMENT}:control"

RUN_STATUS_STRING = (
    f"{REDIS_KEY_PREFIX}thread:{THREAD_ID_SEGMENT}:run:{RUN_ID_SEGMENT_NON_HASH}:control"
)
STRING_RUN_ATTEMPT = f"{REDIS_KEY_PREFIX}run:{RUN_ID_SEGMENT}:attempt"
STRING_RUN_RUNNING = f"{REDIS_KEY_PREFIX}run:{RUN_ID_SEGMENT}:running"

LIST_RUN_QUEUE = f"{REDIS_KEY_PREFIX}run:" + ("{queue}" if REDIS_CLUSTER else "queue")

QUEUE_THREADS_ZSET = f"{REDIS_KEY_PREFIX}run:" + (
    "{queue}:threads" if REDIS_CLUSTER else "queue:threads"
)

LOCK_RUN_SWEEP = f"{REDIS_KEY_PREFIX}run:" + ("{sweep_v2}" if REDIS_CLUSTER else "sweep:v2")

LOCK_THREAD_SWEEP = f"{REDIS_KEY_PREFIX}thread:" + ("{sweep}" if REDIS_CLUSTER else "sweep")

LOCK_STORE_SWEEP = f"{REDIS_KEY_PREFIX}store:" + ("{sweep}" if REDIS_CLUSTER else "sweep")

STRING_THREAD_LAST_SWEEP = f"{REDIS_KEY_PREFIX}thread:" + (
    "{last_sweep}" if REDIS_CLUSTER else "last_sweep"
)

STRING_STORE_LAST_SWEEP = f"{REDIS_KEY_PREFIX}store:" + (
    "{last_sweep}" if REDIS_CLUSTER else "last_sweep"
)

LOCK_RUN_STATS = f"{REDIS_KEY_PREFIX}run:" + ("{stats_lock}" if REDIS_CLUSTER else "stats_lock")

STRING_RUN_STATS_CACHE = f"{REDIS_KEY_PREFIX}run:" + (
    "{stats_cache}" if REDIS_CLUSTER else "stats_cache"
)

LOCK_LONG_QUERY_MONITOR = f"{REDIS_KEY_PREFIX}monitor:" + (
    "{long_query}" if REDIS_CLUSTER else "long_query"
)

STRING_LONG_QUERY_LAST_SCAN = f"{REDIS_KEY_PREFIX}monitor:" + (
    "{last_scan}" if REDIS_CLUSTER else "last_scan"
)

LOCK_LICENSE_KEY = f"{REDIS_KEY_PREFIX}license_key_lock:{LICENSE_KEY_SEGMENT}"
STRING_LICENSE_KEY = f"{REDIS_KEY_PREFIX}license_key:{LICENSE_KEY_SEGMENT}"

LOCK_MIGRATION = f"{REDIS_KEY_PREFIX}migration:" + ("{lock}" if REDIS_CLUSTER else "lock")
