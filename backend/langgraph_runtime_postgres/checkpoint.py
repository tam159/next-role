import asyncio
import builtins
import contextlib
import random
import time
import typing
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from enum import Enum
from typing import Any, NamedTuple, cast

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.constants import TASKS

from langgraph_api import config as api_config
from langgraph_api.asyncio import (
    AsyncQueue,
    aclosing_aiter,
    call_soon_threadsafe,
)
from langgraph_api.feature_flags import (
    DELTA_CHANNEL_SUPPORT,
    OMIT_PENDING_SENDS,
)

if DELTA_CHANNEL_SUPPORT:
    from langgraph.checkpoint.base import (
        DeltaChannelHistory,
        get_checkpoint_id,
    )
    from langgraph.checkpoint.postgres.base import (
        BasePostgresSaver,
        _DeltaStage2Row,
    )
    from langgraph.checkpoint.serde.types import _DeltaSnapshot
else:
    _DeltaSnapshot = type("_DeltaSnapshot", (), {})

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from langgraph_api.logging import LOG_LEVEL_DEBUG
from langgraph_api.schema import MetadataInput
from langgraph_api.serde import Fragment, Serializer, json_loads
from langgraph_runtime_postgres.custom_encryption_serializer import (
    AsyncSerializerProtocol,
    ensure_async_serde,
)

logger = structlog.stdlib.get_logger(__name__)
PENDING_SENDS_CTE = (
    ""
    if OMIT_PENDING_SENDS
    else f""",
    (
        select array_agg(array[cw.type::bytea, cw.blob] order by cw.task_id, cw.idx)
        from checkpoint_writes cw
        where cw.thread_id = checkpoints.thread_id
            and cw.checkpoint_ns = checkpoints.checkpoint_ns
            and cw.checkpoint_id = checkpoints.parent_checkpoint_id
            and cw.channel = '{TASKS}'
    ) as pending_sends
"""
)

# Configurable keys that are transient (per-request) and not persisted.
TRANSIENT_CONFIGURABLE_KEYS = frozenset(
    {
        "langgraph_auth_user",
        "langgraph_request_id",
        "langgraph_auth_user_id",
        "langgraph_auth_permissions",
    },
)

# When metadata source is "update", these keys are owned by the originating
# run and must not be overwritten by the run performing the update.
UPDATE_CHECKPOINT_EXCLUDED_KEYS = frozenset(
    {
        "run_id",
        "user_id",
        "assistant_id",
    },
)

SELECT_SQL = f"""
select
    thread_id,
    checkpoint,
    checkpoint_ns,
    checkpoint_id,
    parent_checkpoint_id,
    metadata,
    (
        select array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])
        from jsonb_each_text(checkpoint -> 'channel_versions')
        inner join checkpoint_blobs bl
            on bl.thread_id = checkpoints.thread_id
            and bl.checkpoint_ns = checkpoints.checkpoint_ns
            and bl.channel = jsonb_each_text.key
            and bl.version = jsonb_each_text.value
    ) as channel_values,
    (
        select
        array_agg(array[cw.task_id::text::bytea, cw.channel::bytea, cw.type::bytea, cw.blob] order by cw.task_id, cw.idx)
        from checkpoint_writes cw
        where cw.thread_id = checkpoints.thread_id
            and cw.checkpoint_ns = checkpoints.checkpoint_ns
            and cw.checkpoint_id = checkpoints.checkpoint_id
    ) as pending_writes{PENDING_SENDS_CTE}
from checkpoints """


# ---------------------------------------------------------------------------
# DeltaChannel history reconstruction
# ---------------------------------------------------------------------------
#
# DeltaChannel channels do not store a full snapshot at every checkpoint.
# Instead each checkpoint records only the *writes* that mutated the channel
# at that step, plus an occasional full *seed* snapshot. To materialise the
# value of a DeltaChannel as of some target checkpoint we have to walk the
# checkpoint ancestry backwards, collecting the per-step writes, until we
# either reach a seed snapshot or hit the root of the thread.
#
# Terminology
# -----------
#   chain   The ordered list of checkpoint ids, newest first, from the target
#           checkpoint back to (and including) the checkpoint that carries the
#           seed snapshot — or back to the root when no seed exists.
#   seed    The full snapshot a channel was last serialised with. Replaying the
#           chain's writes on top of the seed reproduces the channel value.
#   WALK    The first SQL pass. It scans checkpoint metadata only (no blob
#           bytes), one page at a time, following parent pointers and noting
#           for each requested channel whether the checkpoint has a recorded
#           version (it is part of the chain) and/or a snapshot (it is a seed).
#   FETCH   The second SQL pass. Given the chains and seed versions discovered
#           by WALK, it pulls the actual write blobs from ``checkpoint_writes``
#           and the seed blobs from ``checkpoint_blobs`` in a single round trip.
#
# Why two passes?
# ---------------
# Channels in the same graph can have wildly different chain depths. A naive
# single query that joined writes for ``channel = ANY(...)`` across the union
# of every channel's chain would over-fetch enormously for shallow channels.
# Splitting WALK (cheap metadata scan, paged) from FETCH (targeted blob pull,
# one branch per channel) keeps both the scanned row count and the transferred
# byte count proportional to what each channel actually needs.
#
# Paging
# ------
# WALK runs with a ``LIMIT`` so a pathologically deep history cannot blow up
# memory. After each page we advance every channel's walk cursor independently;
# the loop stops once every channel has found its seed (or exhausted its
# ancestry) or the final, short page has been consumed.
#
# This whole machinery is only reachable on langgraph >= 1.2, which is the
# first version that emits DeltaChannel snapshots; on older installs the
# overrides below are never invoked.
#
# The two helpers below build the parameterised WALK and FETCH SQL. They are
# module-level (not methods) so they can be unit-tested without a live
# connection; the async orchestration lives in the Checkpointer overrides.
# ---------------------------------------------------------------------------

_DELTA_PAGE_SIZE = 1024


def _build_delta_walk_sql(channels: Sequence[str]) -> str:
    """SQL for the WALK pass — scans checkpoint metadata only.

    Returns one row per checkpoint with `2*K` parallel JSONB key lookups
    (one `ver_i` / `hs_i` column pair per requested channel). No blob
    bytes; the result set fits a paged `LIMIT` cleanly.

    Caller must extend params with
    `[ch_0, ch_0, ch_1, ch_1, ..., thread_id, ns, cursor, cursor, page_size]`.
    The `cursor` is the smallest `checkpoint_id` from the previous page
    (or `None` on the first page); `(%s::text IS NULL OR ...)` makes the
    first-page `WHERE` a no-op.
    """
    cols = []
    for i in range(len(channels)):
        # Two parallel lookups per channel, both keyed by the channel name
        # bound as a parameter (hence two ``%s`` placeholders per iteration):
        #   ver_i  the recorded channel version at this checkpoint, or NULL
        #   hs_i   whether this checkpoint carries a snapshot for the channel
        # The suffix ``i`` keeps the output columns positional per channel.
        cols.append(
            f"checkpoint -> 'channel_versions' ->> %s AS ver_{i}"
            f", (checkpoint -> 'channel_values' -> %s) IS NOT NULL AS hs_{i}",
        )
    return (
        "SELECT checkpoint_id::text, parent_checkpoint_id::text, "
        + ", ".join(cols)
        + " FROM checkpoints"
        + " WHERE thread_id = %s AND checkpoint_ns = %s"
        + " AND (%s::text IS NULL OR checkpoint_id < %s::uuid)"
        + " ORDER BY checkpoint_id DESC LIMIT %s"
    )


def _build_delta_fetch_sql(
    *,
    channels_with_chain: Sequence[str],
    channels_with_seed: Sequence[str],
) -> str:
    """SQL for the FETCH pass — pulls bytes for chain writes + seed blobs.

    Built as a per-channel UNION ALL: one branch per channel that has
    chain checkpoints (reads `checkpoint_writes`) and one branch per
    channel that found a seed (reads `checkpoint_blobs` at that exact
    version). This avoids the over-fetch a `channel = ANY(...) AND
    checkpoint_id = ANY(union)` form would cause when channels have
    different chain depths.

    Caller must pass parameters in matching order:

        for ch in channels_with_chain:
            params += [thread_id, checkpoint_ns, ch, chain_cids[ch]]
        for ch in channels_with_seed:
            params += [thread_id, checkpoint_ns, ch, seed_version[ch]]

    Returns "" when both lists are empty (caller skips executing).
    """
    branches = []
    # One write-reading branch per channel that has a non-empty chain. Each
    # branch is tagged ``_kind = 'w'`` so the caller can demux the mixed result,
    # and bound to that channel's exact chain ids via ``checkpoint_id = ANY``.
    for _ in channels_with_chain:
        branches.append(
            "SELECT 'w'::text AS _kind, checkpoint_id::text, channel, type, blob, task_id::text, idx, NULL::text AS version FROM checkpoint_writes WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND checkpoint_id = ANY(%s::uuid[])",
        )
    # One blob-reading branch per channel that discovered a seed snapshot. The
    # columns are kept positionally identical to the write branch (NULLs stand
    # in for the write-only columns) so a single ``UNION ALL`` is well typed.
    # ``_kind = 'b'`` marks these rows; ``version`` carries the seed version so
    # the caller can match the blob back to the right channel snapshot.
    for _ in channels_with_seed:
        branches.append(
            "SELECT 'b'::text AS _kind, NULL::text AS checkpoint_id, channel, type, blob, NULL::text AS task_id, NULL::int AS idx, version FROM checkpoint_blobs WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s",
        )
    # Empty when neither list contributed a branch; the caller treats an empty
    # string as "nothing to fetch" and skips the round trip entirely. Joining
    # with UNION ALL (not UNION) preserves duplicates and avoids a needless
    # sort/dedup over what are already distinct rows.
    return " UNION ALL ".join(branches)


class CheckpointBlob(NamedTuple):
    thread_id: str
    checkpoint_ns: str
    channel: str
    version: str
    type: str
    blob: bytes | None


class CheckpointPut(NamedTuple):
    run_id: str | None
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: uuid.UUID
    parent_checkpoint_id: uuid.UUID | None
    checkpoint: Jsonb
    metadata: Jsonb


class CheckpointWrite(NamedTuple):
    # idx is the position in WRITES_IDX_MAP (or the enumeration index).
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    task_id: str
    idx: int
    channel: str
    type: str
    blob: bytes


class ItemType(Enum):
    CHECKPOINT = "put"
    WRITE = "write"
    BLOB = "blob"


class CheckpointQueueItem(NamedTuple):
    fut: tuple[asyncio.AbstractEventLoop, asyncio.Future]
    items: typing.Sequence[CheckpointPut | CheckpointWrite | CheckpointBlob]


PUTS_QUEUE = AsyncQueue[CheckpointQueueItem]()


class Checkpointer(BaseCheckpointSaver):
    latest_iter: AsyncIterator[CheckpointTuple] | None
    _serde: AsyncSerializerProtocol

    def __init__(
        self,
        conn: AsyncConnection | None = None,
        latest: AsyncIterator[CheckpointTuple] | None = None,
        unpack_hook: Callable[[int, bytes], Any] | None = None,
        use_direct_connection: bool = False,
    ) -> None:
        if unpack_hook is not None and DELTA_CHANNEL_SUPPORT:
            from langgraph.checkpoint.serde.jsonplus import (
                EXT_DELTA_SNAPSHOT,
            )
            from langgraph.checkpoint.serde.types import (
                _DeltaSnapshot as _DS,
            )

            _inner_hook = unpack_hook

            def _delta_aware_hook(code: int, data: bytes) -> Any:
                if code == EXT_DELTA_SNAPSHOT:
                    import ormsgpack

                    return _DS(
                        ormsgpack.unpackb(
                            data,
                            ext_hook=_delta_aware_hook,
                            option=ormsgpack.OPT_NON_STR_KEYS,
                        ),
                    )
                return _inner_hook(code, data)

            base_serde = Serializer(__unpack_ext_hook__=_delta_aware_hook)
        elif unpack_hook is not None:
            base_serde = Serializer(__unpack_ext_hook__=unpack_hook)
        else:
            base_serde = Serializer()

        serde = base_serde

        if api_config.LANGGRAPH_AES_KEY:
            serde = EncryptedSerializer.from_pycryptodome_aes(
                base_serde,
                key=api_config.LANGGRAPH_AES_KEY,
            )
            logger.info("AES encryption configured for checkpoints")

        if api_config.LANGGRAPH_ENCRYPTION:
            from langgraph_api.encryption import get_custom_encryption_instance

            encryption_instance = get_custom_encryption_instance()
            if encryption_instance:
                from langgraph_runtime_postgres.custom_encryption_serializer import (
                    CustomEncryptionSerializer,
                )

                aes_serializer = serde if api_config.LANGGRAPH_AES_KEY else None
                serde = CustomEncryptionSerializer(
                    base_serde,
                    encryption_instance,
                    aes_serializer,
                )
                logger.info("Using custom encryption for checkpoints")

        super().__init__(serde=serde)
        self.loop = asyncio.get_running_loop()
        self.latest_iter = latest
        self.latest_tuple = None
        self.conn = conn
        self.use_direct_connection = use_direct_connection
        if conn is None and use_direct_connection:
            raise ValueError(
                "use_direct_connection requires a connection to be provided.",
            )

    @property
    def serde(self) -> AsyncSerializerProtocol:
        return self._serde

    @serde.setter
    def serde(self, value: SerializerProtocol) -> None:
        "Set the serializer, automatically wrapping it to ensure async support."
        self._serde = ensure_async_serde(value)

    @contextlib.asynccontextmanager
    async def _connect(self) -> AsyncIterator[AsyncConnection]:
        from langgraph_runtime_postgres import database

        if self.conn is None:
            async with database.connect(supports_core_api=False) as conn:
                yield conn
        else:
            yield self.conn

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        where, args = self._search_where(config, filter, before)
        query = SELECT_SQL + where + " ORDER BY checkpoint_id DESC"
        if limit:
            query += f" LIMIT {limit}"
        async with self._connect() as conn, conn.cursor(binary=True) as cur:
            async for value in await cur.execute(query, args, binary=True):
                checkpoint, metadata, pending_writes = await asyncio.gather(
                    self._load_checkpoint(
                        value["checkpoint"],
                        value["channel_values"],
                        None if OMIT_PENDING_SENDS else value["pending_sends"],
                    ),
                    self._decrypt_json(json_loads(value["metadata"])),
                    self._load_writes(value["pending_writes"]),
                )
                yield CheckpointTuple(
                    {
                        "configurable": {
                            "thread_id": value["thread_id"],
                            "checkpoint_ns": value["checkpoint_ns"],
                            "checkpoint_id": value["checkpoint_id"],
                        },
                    },
                    checkpoint,
                    metadata,
                    {
                        "configurable": {
                            "thread_id": value["thread_id"],
                            "checkpoint_ns": value["checkpoint_ns"],
                            "checkpoint_id": value["parent_checkpoint_id"],
                        },
                    }
                    if value["parent_checkpoint_id"]
                    else None,
                    pending_writes,
                )

    async def aget_iter(self, config: RunnableConfig) -> AsyncIterator[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")
        if checkpoint_id:
            args = (thread_id, checkpoint_ns, checkpoint_id)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s"
        else:
            args = (thread_id, checkpoint_ns)
            where = (
                "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"
            )
        async with self._connect() as conn:
            cur = await conn.execute(SELECT_SQL + where, args, binary=True)

            async def _gen():
                async for value in aclosing_aiter(cur):
                    checkpoint, metadata, pending_writes = await asyncio.gather(
                        self._load_checkpoint(
                            value["checkpoint"],
                            value["channel_values"],
                            None if OMIT_PENDING_SENDS else value["pending_sends"],
                        ),
                        self._decrypt_json(json_loads(value["metadata"])),
                        self._load_writes(value["pending_writes"]),
                    )
                    yield CheckpointTuple(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": value["checkpoint_ns"],
                                "checkpoint_id": value["checkpoint_id"],
                            },
                        },
                        checkpoint,
                        metadata,
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": value["checkpoint_ns"],
                                "checkpoint_id": value["parent_checkpoint_id"],
                            },
                        }
                        if value["parent_checkpoint_id"]
                        else None,
                        pending_writes,
                    )

            return _gen()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        if self.latest_iter is not None:
            try:
                latest_tuple = await anext(self.latest_iter, None)
                if not latest_tuple:
                    return None
                if latest_tuple.config["configurable"]["thread_id"] == config["configurable"][
                    "thread_id"
                ] and latest_tuple.config["configurable"]["checkpoint_ns"] == config[
                    "configurable"
                ].get("checkpoint_ns", ""):
                    return latest_tuple
            finally:
                self.latest_iter = None
        ckpt_tuple = await anext(await self.aget_iter(config), None)
        if LOG_LEVEL_DEBUG and ckpt_tuple is not None:
            parent_config = ckpt_tuple.parent_config or {}
            await logger.adebug(
                "Checkpoint retrieved",
                thread_id=ckpt_tuple.config["configurable"]["thread_id"],
                checkpoint_ns=ckpt_tuple.config["configurable"]["checkpoint_ns"],
                checkpoint_id=ckpt_tuple.config["configurable"]["checkpoint_id"],
                parent_checkpoint_id=parent_config.get("configurable", {}).get(
                    "checkpoint_id",
                ),
                requested_checkpoint_ns=config["configurable"].get(
                    "checkpoint_ns",
                    "",
                ),
                requested_checkpoint_id=config["configurable"].get("checkpoint_id"),
            )
        return ckpt_tuple

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        next_versions: dict[str, str],
    ) -> RunnableConfig:
        configurable = config["configurable"].copy()
        run_id = configurable.pop("run_id", None)
        thread_id = configurable.pop("thread_id")
        checkpoint_ns = configurable.pop("checkpoint_ns", "")
        checkpoint_id = configurable.pop("checkpoint_id", None)
        copy = checkpoint.copy()
        copy["channel_values"] = copy["channel_values"].copy()
        copy.pop("pending_sends", None)
        next_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
        }

        blob_values = {}
        for k, v in checkpoint["channel_values"].items():
            if isinstance(v, _DeltaSnapshot):
                blob_values[k] = copy["channel_values"].pop(k)
                copy["channel_values"][k] = True
            elif v is None or isinstance(v, str | int | float | bool):
                continue
            else:
                blob_values[k] = copy["channel_values"].pop(k)

        if blob_versions := {k: v for k, v in next_versions.items() if k in blob_values}:
            blobs = await self._dump_blobs(
                thread_id,
                checkpoint_ns,
                blob_values,
                blob_versions,
            )
        else:
            blobs = []

        config_metadata = config.get("metadata", {})
        if config_metadata and isinstance(config_metadata, dict):
            config_metadata = await self._decrypt_json(config_metadata)

        is_update_source = metadata.get("source") == "update"
        merged_metadata = {
            **{
                k: v
                for k, v in configurable.items()
                if not k.startswith("__")
                and k not in TRANSIENT_CONFIGURABLE_KEYS
                and (not is_update_source or k not in UPDATE_CHECKPOINT_EXCLUDED_KEYS)
            },
            **{
                k: v
                for k, v in config_metadata.items()
                if k not in TRANSIENT_CONFIGURABLE_KEYS
                and (not is_update_source or k not in UPDATE_CHECKPOINT_EXCLUDED_KEYS)
            },
            **{k: v for k, v in metadata.items() if k not in TRANSIENT_CONFIGURABLE_KEYS},
        }

        encrypted_channel_values, encrypted_metadata = await asyncio.gather(
            self._encrypt_json(copy["channel_values"]),
            self._encrypt_json(merged_metadata, path="checkpoint_metadata"),
        )
        copy["channel_values"] = encrypted_channel_values

        puts = CheckpointPut(
            run_id,
            thread_id,
            checkpoint_ns,
            _ensure_uuid(checkpoint["id"]),
            _ensure_uuid(checkpoint_id) if checkpoint_id else None,
            Jsonb(copy),
            Jsonb(encrypted_metadata),
        )

        try:
            if self.use_direct_connection and self.conn is not None:
                await self._execute_puts_direct(self.conn, blobs, [puts], [])
                return next_config

            put_fut = self.loop.create_future()
            call_soon_threadsafe(
                PUTS_QUEUE.put_nowait,
                CheckpointQueueItem(
                    (self.loop, put_fut),
                    (puts, *blobs),
                ),
            )
            await put_fut
            return next_config
        except Exception as e:
            await logger.aerror(
                "Failed to put checkpoint",
                thread_id=config["configurable"]["thread_id"],
                checkpoint_ns=config["configurable"].get("checkpoint_ns"),
                checkpoint_id=config["configurable"].get("checkpoint_id"),
                exc_info=e,
            )
            raise

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: list[tuple[str, Any]],
        task_id: str,
    ) -> None:
        checkpoint_writes = await self._dump_writes(
            config["configurable"]["thread_id"],
            config["configurable"]["checkpoint_ns"],
            config["configurable"]["checkpoint_id"],
            task_id,
            writes,
        )
        try:
            if self.use_direct_connection and self.conn is not None:
                await self._execute_puts_direct(
                    self.conn,
                    [],
                    [],
                    checkpoint_writes,
                )
                return
            fut = self.loop.create_future()
            call_soon_threadsafe(
                PUTS_QUEUE.put_nowait,
                CheckpointQueueItem((self.loop, fut), checkpoint_writes),
            )
            await fut
        except Exception as e:
            await logger.aerror(
                "Failed to put writes",
                thread_id=config["configurable"]["thread_id"],
                checkpoint_ns=config["configurable"]["checkpoint_ns"],
                checkpoint_id=config["configurable"]["checkpoint_id"],
                task_id=task_id,
                exc_info=e,
            )
            raise

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:.16f}"

    async def _encrypt_json(
        self,
        data: dict[str, Any],
        path: str | None = None,
    ) -> dict[str, Any]:
        """Encrypt a dict if encryption (AES or custom) is configured.

        Args:
            data: The dict to encrypt
            path: Optional path for encryption skip rules (e.g., "checkpoint_metadata" to
                  distinguish from channel_values which uses default "checkpoint")
        """
        from langgraph_api.encryption import get_encryption

        encryption = get_encryption()
        if encryption is None:
            return data
        from langgraph_api.encryption.middleware import encrypt_json_if_needed

        result = await encrypt_json_if_needed(
            data,
            encryption,
            "checkpoint",
            path=path,
        )
        if result is None:
            raise ValueError(
                "encrypt_json_if_needed returned None for non-None input",
            )
        return result

    async def _decrypt_json(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a dict if encryption (AES or custom) is configured."""
        from langgraph_api.encryption import get_encryption

        encryption = get_encryption()
        if encryption is None:
            return data
        from langgraph_api.encryption.middleware import decrypt_json_if_needed

        result = await decrypt_json_if_needed(data, encryption, "checkpoint")
        if result is None:
            raise ValueError(
                "decrypt_json_if_needed returned None for non-None input",
            )
        return result

    async def _load_checkpoint(
        self,
        checkpoint_f: Fragment,
        blob_values: list[tuple[bytes, bytes, bytes]],
        pending_sends: list[tuple[bytes, bytes]] | None,
    ) -> Checkpoint:
        checkpoint = json_loads(checkpoint_f)
        if "channel_values" in checkpoint:
            checkpoint["channel_values"] = await self._decrypt_json(
                checkpoint["channel_values"],
            )
        pending_sends_list = []
        for c, b in pending_sends or []:
            pending_sends_list.append(
                await self.serde.aloads_typed((c.decode(), b)),
            )
        return {
            **checkpoint,
            "pending_sends": pending_sends_list,
            "channel_values": {
                **checkpoint.get("channel_values", {}),
                **await self._load_blobs(blob_values),
            },
        }

    async def _load_blobs(
        self,
        blob_values: list[tuple[bytes, bytes, bytes]],
    ) -> dict[str, Any]:
        if not blob_values:
            return {}
        result = {}
        for k, t, v in blob_values:
            if t.decode() != "empty":
                result[k.decode()] = await self.serde.aloads_typed((t.decode(), v))
        return result

    async def _dump_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        values: dict[str, Any],
        versions: dict[str, str],
    ) -> list[CheckpointBlob]:
        if not versions:
            return []
        rows = []
        for k, ver in versions.items():
            t, b = await self.serde.adumps_typed(values[k])
            rows.append(CheckpointBlob(thread_id, checkpoint_ns, k, ver, t, b))
        return rows

    async def _load_writes(
        self,
        writes: list[tuple[bytes, bytes, bytes, bytes]],
    ) -> list[tuple[str, str, Any]]:
        if not writes:
            return []
        result = []
        for tid, channel, t, v in writes:
            value = await self.serde.aloads_typed((t.decode(), v))
            result.append((tid.decode(), channel.decode(), value))
        return result

    async def _dump_writes(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        task_id: str,
        writes: list[tuple[str, Any]],
    ) -> list[CheckpointWrite]:
        result = []
        for idx, (channel, value) in enumerate(writes):
            t, b = await self.serde.adumps_typed(value)
            result.append(
                CheckpointWrite(
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    task_id,
                    WRITES_IDX_MAP.get(channel, idx),
                    channel,
                    t,
                    b,
                ),
            )
        return result

    async def _assemble_delta_history(
        self,
        channels: Sequence[str],
        *,
        chain_by_ch: Mapping[str, builtins.list[str]],
        seed_ver_by_ch: Mapping[str, str | None],
        fetch_rows: "Sequence[_DeltaStage2Row]",
    ) -> "dict[str, DeltaChannelHistory]":
        """Turn the FETCH rowset into per-channel `DeltaChannelHistory`.

        FETCH returns a single mixed rowset: write rows (`_kind = 'w'`)
        for chain checkpoints across all channels, plus seed-blob rows
        (`_kind = 'b'`) for channels that found a snapshot. Here we:
          1. Demux by channel into `writes_by_ch_by_cid` and
             `seed_blob_by_ver` lookup tables.
          2. For each channel, walk its chain newest→oldest pulling the
             writes that landed at each ancestor, then reverse to get
             oldest→newest (the order `replay_writes` expects).
          3. Deserialize the seed blob if present and not the "empty"
             tombstone (an explicit non-snapshot marker the saver uses
             for blob slots that were created but carry no value).

        Async-flavored equivalent of upstream
        `BasePostgresSaver._build_delta_channels_writes_history` — same
        logic, but uses `await self.serde.aloads_typed` for our async
        serde wrapper.
        """
        writes_by_ch_by_cid = {ch: {} for ch in channels}
        seed_blob_by_ver = {}

        for r in fetch_rows:
            ch = cast(str, r["channel"])
            kind = r["_kind"]
            if kind == "w":
                cid = cast(str, r["checkpoint_id"])
                writes_by_ch_by_cid.setdefault(ch, {}).setdefault(cid, []).append(
                    cast(
                        "tuple[str, bytes, str, int]",
                        (r["type"], r["blob"], r["task_id"], r["idx"]),
                    ),
                )
            else:
                ver = cast(str, r["version"])
                seed_blob_by_ver[ch, ver] = cast(
                    "tuple[str, bytes]",
                    (r["type"], r["blob"]),
                )

        for cid_map in writes_by_ch_by_cid.values():
            for ws in cid_map.values():
                ws.sort(key=lambda w: (w[2], w[3]), reverse=True)

        result = {}
        for ch in channels:
            chain_cids = chain_by_ch.get(ch, [])
            seed_version = seed_ver_by_ch.get(ch)

            collected = []
            cid_writes = writes_by_ch_by_cid.get(ch, {})
            for cid in chain_cids:
                for type_tag, write_blob, task_id, _idx in cid_writes.get(cid, []):
                    val = await self.serde.aloads_typed((type_tag, write_blob))
                    collected.append((task_id, ch, val))

            collected.reverse()

            entry = {"writes": collected}
            if seed_version is not None:
                blob = seed_blob_by_ver.get((ch, seed_version))
                if blob is not None and blob[0] != "empty":
                    entry["seed"] = await self.serde.aloads_typed(blob)
            result[ch] = entry
        return result

    async def aget_delta_channel_history(
        self,
        config: RunnableConfig,
        *,
        channels: Sequence[str],
    ) -> "Mapping[str, DeltaChannelHistory]":
        """Reconstruct delta-channel history for one target checkpoint.

        See the `# DeltaChannel history reconstruction` comment block at
        the top of this file for the algorithm and terminology (chain,
        seed, WALK / FETCH).

        Loop shape: drive WALK in pages until every channel either found
        its snapshot or hit the root. WALK accumulates `chain_by_ch`
        (newest-first per channel) and `seed_ver_by_ch`. FETCH then
        executes a single per-channel UNION ALL covering both — one
        roundtrip regardless of how many channels are requested.

        Only invoked on langgraph >= 1.2; on older installs no DeltaChannel
        graphs can exist, so langgraph never calls this override.
        """
        if not channels:
            return {}
        channels = list(channels)
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id is None:
            target = await self.aget_tuple(config)
            if target is None:
                return {ch: {"writes": []} for ch in channels}
            checkpoint_id = target.config["configurable"]["checkpoint_id"]
        target_id_str = str(checkpoint_id)

        walk_sql = _build_delta_walk_sql(channels)
        parent_of = {}

        ver_by_i_by_cid = [{} for _ in channels]
        hs_by_i_by_cid = [{} for _ in channels]

        chain_by_ch = {ch: [] for ch in channels}
        seed_ver_by_ch = {ch: None for ch in channels}

        walk_cursor_by_ch = {}
        seeded = set()
        cursor = None

        while True:
            walk_params = []
            for ch in channels:
                walk_params.extend([ch, ch])
            walk_params.extend(
                [thread_id, checkpoint_ns, cursor, cursor, _DELTA_PAGE_SIZE],
            )

            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(walk_sql, walk_params)
                    page = await cur.fetchall()

            if not page:
                break

            oldest = BasePostgresSaver._ingest_stage1_page(
                cast("list[Mapping[str, Any]]", page),
                channels,
                parent_of,
                ver_by_i_by_cid,
                hs_by_i_by_cid,
            )

            BasePostgresSaver._try_advance_walks(
                target_id_str,
                channels,
                parent_of,
                ver_by_i_by_cid,
                hs_by_i_by_cid,
                chain_by_ch,
                seed_ver_by_ch,
                walk_cursor_by_ch,
                seeded,
            )

            if len(seeded) == len(channels) or len(page) < _DELTA_PAGE_SIZE:
                break
            cursor = oldest

        channels_with_chain = [ch for ch in channels if chain_by_ch[ch]]
        channels_with_seed = [ch for ch in channels if seed_ver_by_ch[ch] is not None]
        fetch_sql = _build_delta_fetch_sql(
            channels_with_chain=channels_with_chain,
            channels_with_seed=channels_with_seed,
        )
        if fetch_sql:
            fetch_params = []
            for ch in channels_with_chain:
                fetch_params.extend(
                    [thread_id, checkpoint_ns, ch, chain_by_ch[ch]],
                )
            for ch in channels_with_seed:
                fetch_params.extend(
                    [thread_id, checkpoint_ns, ch, seed_ver_by_ch[ch]],
                )
            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(fetch_sql, fetch_params)
                    fetch_rows = await cur.fetchall()
        else:
            fetch_rows = []
        return await self._assemble_delta_history(
            channels=channels,
            chain_by_ch=chain_by_ch,
            seed_ver_by_ch=seed_ver_by_ch,
            fetch_rows=cast("list[_DeltaStage2Row]", fetch_rows),
        )

    def get_delta_channel_history(
        self,
        config: RunnableConfig,
        *,
        channels: Sequence[str],
    ) -> "Mapping[str, DeltaChannelHistory]":
        return asyncio.run_coroutine_threadsafe(
            self.aget_delta_channel_history(config=config, channels=channels),
            self.loop,
        ).result()

    def _search_where(
        self,
        config: RunnableConfig | None,
        filter: MetadataInput,
        before: RunnableConfig | None = None,
    ) -> tuple[str, list[Any]]:
        """Return WHERE clause predicates for alist() given config, filter, cursor.

        This method returns a tuple of a string and a tuple of values. The string
        is the parametered WHERE clause predicate (including the WHERE keyword):
        "WHERE column1 = $1 AND column2 IS $2". The list of values contains the
        values for each of the corresponding parameters.
        """
        wheres = []
        param_values = []

        if config:
            wheres.append("thread_id = %s ")
            param_values.append(config["configurable"]["thread_id"])
            checkpoint_ns = config["configurable"].get("checkpoint_ns")
            if checkpoint_ns is not None:
                wheres.append("checkpoint_ns = %s ")
                param_values.append(checkpoint_ns)

        if filter:
            wheres.append("metadata @> %s ")
            param_values.append(Jsonb(filter))

        if before is not None:
            wheres.append("checkpoint_id < %s ")
            param_values.append(before["configurable"]["checkpoint_id"])

        return (
            "WHERE " + " AND ".join(wheres) if wheres else "",
            param_values,
        )

    async def _execute_puts_direct(
        self,
        conn: AsyncConnection,
        blobs: list[CheckpointBlob],
        checkpoints: list[CheckpointPut],
        writes: list[CheckpointWrite],
    ) -> None:
        """Execute checkpoint puts directly using the provided connection."""
        async with conn.cursor(binary=True) as cur:
            if blobs:
                copy_sql = """
                    COPY checkpoint_blobs
                         (thread_id, checkpoint_ns, channel, version, type, blob)
                    FROM STDIN
                """
                async with cur.copy(copy_sql) as cp:
                    for row in blobs:
                        await cp.write_row(row)
            if checkpoints:
                copy_sql = """
                    COPY checkpoints
                         (run_id, thread_id, checkpoint_ns, checkpoint_id,
                          parent_checkpoint_id, checkpoint, metadata)
                    FROM STDIN
                """
                async with cur.copy(copy_sql) as cp:
                    for row in checkpoints:
                        await cp.write_row(row)
            if writes:
                await cur.executemany(
                    "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)\n                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)\n                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n                    DO UPDATE SET blob = EXCLUDED.blob\n                    WHERE checkpoint_writes.channel = EXCLUDED.channel\n                      AND checkpoint_writes.type = EXCLUDED.type",
                    writes,
                )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints from the database.

        This method retrieves a list of checkpoint tuples from the Postgres database based
        on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

        Args:
            config (Optional[RunnableConfig]): Base configuration for filtering checkpoints.
            filter (Optional[Dict[str, Any]]): Additional filtering criteria for metadata.
            before (Optional[RunnableConfig]): If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.
            limit (Optional[int]): Maximum number of checkpoints to return.

        Yields:
            Iterator[CheckpointTuple]: An iterator of matching checkpoint tuples.
        """
        aiter_ = self.alist(config, filter=filter, before=before, limit=limit)
        try:
            while True:
                yield asyncio.run_coroutine_threadsafe(
                    anext(aiter_),
                    self.loop,
                ).result()
        except StopAsyncIteration:
            return

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Get a checkpoint tuple from the database.

        This method retrieves a checkpoint tuple from the Postgres database based on the
        provided config. If the config contains a "checkpoint_id" key, the checkpoint with
        the matching thread ID and "checkpoint_id" is retrieved. Otherwise, the latest checkpoint
        for the given thread ID is retrieved.

        Args:
            config (RunnableConfig): The config to use for retrieving the checkpoint.

        Returns:
            Optional[CheckpointTuple]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.
        """
        return asyncio.run_coroutine_threadsafe(
            self.aget_tuple(config),
            self.loop,
        ).result()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str],
    ) -> RunnableConfig:
        """Save a checkpoint to the database.

        This method saves a checkpoint to the Postgres database. The checkpoint is associated
        with the provided config and its parent config (if any).

        Args:
            config (RunnableConfig): The config to associate with the checkpoint.
            checkpoint (Checkpoint): The checkpoint to save.
            metadata (CheckpointMetadata): Additional metadata to save with the checkpoint.
            new_versions (ChannelVersions): New channel versions as of this write.

        Returns:
            RunnableConfig: Updated configuration after storing the checkpoint.
        """
        return asyncio.run_coroutine_threadsafe(
            self.aput(config, checkpoint, metadata, new_versions),
            self.loop,
        ).result()

    def put_writes(
        self,
        config: RunnableConfig,
        writes: builtins.list[tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Store intermediate writes linked to a checkpoint.

        This method saves intermediate writes associated with a checkpoint to the database.

        Args:
            config (RunnableConfig): Configuration of the related checkpoint.
            writes (Sequence[Tuple[str, Any]]): List of writes to store, each as (channel, value) pair.
            task_id (str): Identifier for the task creating the writes.
        """
        return asyncio.run_coroutine_threadsafe(
            self.aput_writes(config, writes, task_id),
            self.loop,
        ).result()


_ingestion_loop_task: asyncio.Task | None = None


async def start_checkpoint_ingestion_loop() -> None:
    global _ingestion_loop_task
    if _ingestion_loop_task is not None:
        return
    _ingestion_loop_task = asyncio.create_task(checkpoint_ingestion_loop())


async def stop_checkpoint_ingestion_loop() -> None:
    global _ingestion_loop_task
    if _ingestion_loop_task is None:
        return
    _ingestion_loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _ingestion_loop_task
    _ingestion_loop_task = None


async def checkpoint_ingestion_loop() -> None:
    if api_config.IS_EXECUTOR_ENTRYPOINT:
        await logger.adebug(
            "Executor entrypoint, skipping checkpointer ingestion loop",
        )
        return
    await logger.ainfo("Starting checkpointer ingestion loop")
    max_batch_size = api_config.CHECKPOINT_MAX_BATCH_SIZE
    max_batch_window_s = api_config.CHECKPOINT_BATCH_DELAY
    while True:
        try:
            await PUTS_QUEUE.wait()
            await _ingest_batch(max_batch_size, max_batch_window_s)
        except asyncio.CancelledError:
            await logger.ainfo(
                "Checkpointer ingestion task cancelled. Draining queue.",
            )
            break
        except Exception as e:
            await logger.aexception(
                "Checkpointer ingestion task failed",
                exc_info=e,
            )

    try:
        await _ingest_batch(None, 0.0)
    except Exception as e:
        await logger.aexception(
            "Final checkpointer ingestion batch failed",
            exc_info=e,
        )
        raise


async def _ingest_batch(
    max_batch_size: int | None,
    max_batch_window_s: float,
) -> None:
    from langgraph_runtime_postgres import database

    futs = []
    blobs = []
    checkpoints = []
    writes = []

    thread_ids = set()
    total_items = 0

    try:
        batch_start = time.monotonic() if max_batch_window_s else None

        while True:
            if max_batch_size is not None and total_items >= max_batch_size:
                break

            try:
                queue_item = PUTS_QUEUE.get_nowait()
            except asyncio.QueueEmpty:
                if batch_start is None or not futs:
                    break
                remaining_s = max_batch_window_s - (time.monotonic() - batch_start)
                if remaining_s <= 0:
                    break
                try:
                    queue_item = await asyncio.wait_for(
                        PUTS_QUEUE.get(),
                        timeout=remaining_s,
                    )
                except TimeoutError:
                    break

            (loop, fut), items = queue_item.fut, queue_item.items
            for item in items:
                thread_ids.add(item.thread_id)
                if isinstance(item, CheckpointBlob):
                    blobs.append(item)
                elif isinstance(item, CheckpointPut):
                    checkpoints.append(item)
                elif isinstance(item, CheckpointWrite):
                    writes.append(item)
                else:
                    raise ValueError(f"Unknown item type: {type(item)}")
            total_items += len(items)
            futs.append((loop, fut))

            if batch_start is not None and time.monotonic() - batch_start >= max_batch_window_s:
                break

        if not (futs or blobs or checkpoints or writes):
            return

        await logger.adebug(
            "Ingesting puts",
            n_threads=len(thread_ids),
            blobs=len(blobs),
            checkpoints=len(checkpoints),
            writes=len(writes),
        )

        async with database.connect(supports_core_api=False) as conn:
            async with conn.transaction():
                async with conn.cursor(binary=True) as cur:
                    if blobs:
                        copy_sql = """
                    COPY checkpoint_blobs
                         (thread_id, checkpoint_ns, channel, version, type, blob)
                    FROM STDIN
                """
                        async with cur.copy(copy_sql) as cp:
                            for row in blobs:
                                await cp.write_row(row)
                    if checkpoints:
                        copy_sql = """
                    COPY checkpoints
                         (run_id, thread_id, checkpoint_ns, checkpoint_id,
                          parent_checkpoint_id, checkpoint, metadata)
                    FROM STDIN
                """
                        async with cur.copy(copy_sql) as cp:
                            for row in checkpoints:
                                await cp.write_row(row)
                                if LOG_LEVEL_DEBUG:
                                    await logger.adebug(
                                        "Checkpoint put",
                                        run_id=str(row.run_id),
                                        thread_id=str(row.thread_id),
                                        checkpoint_ns=str(row.checkpoint_ns),
                                        checkpoint_id=str(row.checkpoint_id),
                                        parent_checkpoint_id=str(
                                            row.parent_checkpoint_id,
                                        ),
                                    )
                    if writes:
                        await cur.executemany(
                            "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)\n                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)\n                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n                    DO UPDATE SET blob = EXCLUDED.blob\n                    WHERE checkpoint_writes.channel = EXCLUDED.channel\n                      AND checkpoint_writes.type = EXCLUDED.type",
                            writes,
                        )

        for loop, fut in futs:
            if fut.done():
                continue
            try:
                loop.call_soon_threadsafe(fut.set_result, None)
            except Exception as e:
                logger.exception("Failed to set result", exc_info=e)
    except (asyncio.CancelledError, Exception) as e:
        exc = e
        for loop, fut in futs:
            if fut.done():
                continue
            try:
                loop.call_soon_threadsafe(fut.set_exception, exc)
            except Exception as exc2:
                logger.exception("Failed to set exception", exc_info=exc2)
        raise


def _ensure_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


__all__ = ["Checkpointer", "checkpoint_ingestion_loop"]
