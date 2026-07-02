import asyncio
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from typing import Literal

import structlog
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute

from langgraph_api import config
from langgraph_api.route import ApiRequest, ApiRoute

logger = structlog.stdlib.get_logger(__name__)


def _clamp_duration(seconds: int) -> int:
    seconds = max(1, seconds)
    return min(seconds, max(1, config.FF_PYSPY_PROFILING_MAX_DURATION_SECS))


async def _profile_with_pyspy(seconds: int, fmt: Literal["svg"]) -> Response:
    """Run py-spy against the current process for N seconds and return SVG."""
    pyspy = shutil.which("py-spy")
    if not pyspy:
        return JSONResponse({"error": "py-spy not found on PATH"}, status_code=501)

    # py-spy writes to a file; use a temp file then return its contents.
    fd, path = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    try:
        pid = os.getpid()
        # Example:
        # py-spy record -p <pid> -d <seconds> --format flamegraph -o out.svg
        cmd = [
            pyspy,
            "record",
            "-p",
            str(pid),
            "-d",
            str(seconds),
            "--format",
            "flamegraph",
            "-o",
            path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=seconds + 15)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return JSONResponse(
                {
                    "error": "py-spy timed out",
                    "hint": "Check ptrace permissions or reduce duration",
                },
                status_code=504,
            )
        if proc.returncode != 0:
            # Common failures: missing ptrace capability in containers.
            msg = stderr.decode("utf-8", errors="ignore") if stderr else "py-spy failed"
            await logger.awarning("py-spy failed", returncode=proc.returncode, msg=msg)
            return JSONResponse(
                {
                    "error": "py-spy failed",
                    "detail": msg,
                    "hint": "Ensure the container has CAP_SYS_PTRACE / seccomp=unconfined",
                },
                status_code=500,
            )

        with open(path, "rb") as f:
            content = f.read()
        ts = int(time.time())
        return Response(
            content,
            media_type="image/svg+xml",
            headers={
                "Content-Disposition": f"inline; filename=pyspy-{ts}.svg",
                "Cache-Control": "no-store",
            },
        )
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


async def profile(request: ApiRequest):
    if not config.FF_PYSPY_PROFILING_ENABLED:
        return JSONResponse({"error": "Profiling disabled"}, status_code=403)

    params = request.query_params
    try:
        seconds = _clamp_duration(int(params.get("seconds", "15")))
    except ValueError:
        return JSONResponse({"error": "Invalid seconds"}, status_code=400)
    return await _profile_with_pyspy(seconds, "svg")


profile_routes: list[BaseRoute] = [
    ApiRoute("/profile", profile, methods=["GET"]),
]
