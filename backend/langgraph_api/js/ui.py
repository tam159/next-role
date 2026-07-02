import asyncio
import contextlib
import os
import shutil
import sys
from pathlib import Path

import structlog

from langgraph_api.config import UI_USE_BUNDLER

logger = structlog.stdlib.get_logger(__name__)
bg_tasks: set[asyncio.Task] = set()

UI_ROOT_DIR = (
    Path(
        os.path.abspath(".langgraph_api")
        if UI_USE_BUNDLER
        else os.path.dirname(__file__)
    )
    / "ui"
)

UI_PUBLIC_DIR = UI_ROOT_DIR / "public"
UI_SCHEMAS_FILE = UI_ROOT_DIR / "schemas.json"


async def start_ui_bundler() -> None:
    # LANGGRAPH_UI_ROOT_DIR is only set by in-memory server
    # @see langgraph_api/cli.py
    # Not in public docs: LANGGRAPH_UI is populated by langgraph.json config
    if not UI_USE_BUNDLER or not os.getenv("LANGGRAPH_UI"):
        return

    logger.info("Starting UI bundler")

    bundler_task = asyncio.create_task(_start_ui_bundler_process(), name="ui-bundler")
    bundler_task.add_done_callback(_handle_exception)

    bg_tasks.add(bundler_task)


async def stop_ui_bundler() -> None:
    for task in bg_tasks:
        task.cancel()


async def _start_ui_bundler_process():
    npx_path = shutil.which("npx")
    if npx_path is None:
        raise FileNotFoundError(
            "To run LangGraph with UI support, Node.js and npm are required. "
            "Please install Node.js from https://nodejs.org/ (this will include npm and npx). "
            "After installation, restart your terminal and try again."
        )

    if not os.path.exists(UI_ROOT_DIR):
        os.mkdir(UI_ROOT_DIR)

    pid = None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            npx_path,
            "-y",
            "@langchain/langgraph-ui@latest",
            "watch",
            "-o",
            UI_ROOT_DIR,
            env=dict(os.environ),
        )
        pid = process.pid
        logger.info("Started UI bundler process [%d]", pid)

        code = await process.wait()
        raise Exception(f"UI bundler process exited with code {code}")

    except asyncio.CancelledError:
        logger.info("Shutting down UI bundler process [%d]", pid or -1)
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
                await process.wait()
        raise


def _handle_exception(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    finally:
        bg_tasks.discard(task)
        # if the task died either with exception or not, we should exit
        sys.exit(1)
