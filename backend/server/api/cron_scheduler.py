import asyncio
import json
from random import random
from typing import Any, cast

import structlog

from server.api import config
from server.api.encryption.context import set_encryption_context
from server.api.encryption.middleware import decrypt_response
from server.api.encryption.shared import BLOB_ENCRYPTION_CONTEXT_KEY
from server.api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from server.api.models.run import create_valid_run
from server.api.schema import Cron
from server.api.serde import json_loads
from server.api.utils import next_cron_date
from server.api.utils.config import run_in_executor
from server.api.worker import set_auth_ctx_for_run
from server.runtime.database import connect
from server.runtime.retry import retry_db

if IS_POSTGRES_OR_GRPC_BACKEND:
    from server.api.grpc.ops import Crons
else:
    from server.runtime.ops import Crons

logger = structlog.stdlib.get_logger(__name__)

SLEEP_TIME = config.CRON_SCHEDULER_SLEEP_TIME


def get_metadata_from_payload(
    cron: Cron,
    run_payload: dict[str, Any],
) -> dict[str, Any]:
    cron_metadata = cron.get("metadata", {}) or {}
    if not isinstance(cron_metadata, dict):
        try:
            cron_metadata = json_loads(cron_metadata)
            if not isinstance(cron_metadata, dict):
                logger.warning(
                    f"Parsed cron metadata is not a dict: {type(cron_metadata)}. Will ignore.",
                )
                cron_metadata = {}
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            logger.warning(f"Failed to parse cron metadata: {e}. Will ignore.")
            cron_metadata = {}

    existing_metadata = run_payload.get("metadata", {})
    return {**cron_metadata, **existing_metadata}


@retry_db
async def cron_scheduler():
    logger.info("Starting cron scheduler")
    while True:
        try:
            async with connect() as conn:
                async for item in Crons.next(conn):
                    # gRPC path yields (cron, enc_ctx) tuples;
                    # non-gRPC path yields plain Cron dicts.
                    if isinstance(item, tuple):
                        cron, enc_ctx = item
                    else:
                        cron = item
                        enc_ctx = None

                    on_run_completed = cron.get("on_run_completed")

                    run_payload = cron["payload"]
                    if not isinstance(run_payload, dict):
                        run_payload = json_loads(run_payload)
                    run_payload = cast("dict", run_payload)

                    # Restore the encryption context so runs created by crons
                    # inherit the same context used when the cron was created.
                    # gRPC path: Go extracts it before decryption and returns
                    # it as a separate value from next().
                    # Non-gRPC path: it lives inside the payload blob.
                    if enc_ctx is None:
                        enc_ctx = run_payload.get(BLOB_ENCRYPTION_CONTEXT_KEY)
                    # Always set (even if empty) to avoid leaking context from
                    # a previous cron iteration.
                    set_encryption_context(enc_ctx or {})

                    run_payload = await decrypt_response(
                        run_payload,
                        "cron",
                        ["metadata", "context", "input", "config"],
                    )

                    if on_run_completed == "keep":
                        run_payload.setdefault("on_completion", "keep")

                    run_payload["metadata"] = get_metadata_from_payload(
                        cron,
                        run_payload,
                    )

                    async with set_auth_ctx_for_run(
                        run_payload,
                        user_id=cron["user_id"],
                    ):
                        logger.debug(f"Scheduling cron run {cron}")
                        try:
                            run = await create_valid_run(
                                conn,
                                thread_id=(
                                    str(cron.get("thread_id")) if cron.get("thread_id") else None
                                ),
                                payload=run_payload,
                                headers={},
                            )
                            if not run:
                                logger.error(
                                    "Run not created for cron_id={} payload".format(
                                        cron["cron_id"],
                                    ),
                                )
                        except Exception:
                            logger.exception(
                                "Error scheduling cron run cron_id={}".format(
                                    cron["cron_id"],
                                ),
                            )
                        # gRPC/Postgres path: next_run_date is advanced
                        # atomically in Go's Crons.Next() to prevent
                        # concurrent schedulers from claiming the same cron.
                        if not IS_POSTGRES_OR_GRPC_BACKEND:
                            next_run_date = await run_in_executor(
                                None,
                                next_cron_date,
                                cron["schedule"],
                                cron["now"],
                                cron.get("timezone"),
                            )
                            await Crons.set_next_run_date(
                                conn,
                                cron["cron_id"],
                                next_run_date,
                            )

            await asyncio.sleep(SLEEP_TIME)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in cron_scheduler")
            await asyncio.sleep(SLEEP_TIME + random())
