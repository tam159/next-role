"""Import profiling utilities for diagnosing slow module loads.

When FF_PROFILE_IMPORTS is true, this module provides detailed
profiling of what's slow during module imports - including nested imports
and module-level code execution (network calls, file I/O, etc.).
"""

from __future__ import annotations

import cProfile
import pstats
import time
from contextlib import contextmanager
from typing import TypedDict, cast

import structlog

logger = structlog.stdlib.get_logger(__name__)

# Minimum time (in seconds) for an operation to be reported
MIN_REPORT_THRESHOLD_SECS = 1.0


@contextmanager
def profiled_import(path: str, top_n: int = 10):
    """Context manager for profiling an import with automatic reporting.

    Usage:
        with profiled_import("./my_module.py:obj") as profiler:
            module = exec_module(...)
        # Automatically logs slow calls if any

    Args:
        path: The module path (for logging)
        top_n: Maximum number of slow calls to report
    """
    from langgraph_api import config  # noqa: PLC0415

    if not config.FF_PROFILE_IMPORTS:
        yield None
        return

    start = time.perf_counter()
    pr = cProfile.Profile()
    pr.enable()

    class ProfilerResult:
        def __init__(self) -> None:
            self.slow_calls: list[_SlowCall] = []
            self.total_secs: float = 0.0

    result = ProfilerResult()

    try:
        yield result
    finally:
        pr.disable()
        result.total_secs = time.perf_counter() - start

        # Extract the module filename from path (e.g., "./foo/bar.py:obj" -> "bar.py")
        module_file = path.split(":")[0].rsplit("/", 1)[-1]

        stats_obj = pstats.Stats(pr)
        stats = cast("dict", stats_obj.stats)
        slow_calls: list[_SlowCall] = []

        for (filename, lineno, funcname), (
            _cc,
            nc,
            _tt,
            ct,
            callers,
        ) in stats.items():
            cumtime_secs = ct
            if cumtime_secs >= MIN_REPORT_THRESHOLD_SECS:
                # Skip non-actionable entries
                if "cProfile" in filename or "<frozen" in filename:
                    continue
                # Skip built-in exec (just wrapper around module execution)
                if filename == "~" and "builtins.exec" in funcname:
                    continue
                # Skip the top-level <module> entry (not actionable)
                if funcname == "<module>":
                    continue

                # Find call site in user's module
                call_site = _find_user_call_site(callers, module_file, stats)

                slow_calls.append(
                    _SlowCall(
                        function=funcname,
                        file=f"{filename}:{lineno}",
                        cumulative_secs=round(cumtime_secs, 2),
                        calls=nc,
                        call_site=call_site,
                    )
                )

        slow_calls.sort(key=lambda x: x["cumulative_secs"], reverse=True)
        result.slow_calls = slow_calls[:top_n]

        # Only log if we have slow calls worth reporting
        if result.slow_calls:
            report = _format_slow_calls_report(
                path, result.total_secs, result.slow_calls
            )
            logger.warning(
                f"slow_import_profile: {report}",
                path=path,
                total_secs=round(result.total_secs, 2),
                slow_calls=result.slow_calls,
            )


def _find_user_call_site(
    callers: dict, module_file: str, all_stats: dict, max_depth: int = 20
) -> str | None:
    """Walk up the call chain to find where in the user's module this was called from."""
    visited: set[tuple] = set()
    to_check = list(callers.keys())

    for _ in range(max_depth):
        if not to_check:
            break
        caller_key = to_check.pop(0)
        if caller_key in visited:
            continue
        visited.add(caller_key)

        caller_file, caller_line, caller_func = caller_key
        # Found a call from the user's module
        if caller_file.endswith(module_file):
            # cProfile attributes all module-level code to <module> at line 1,
            # so we can't get the actual line number for top-level calls
            if caller_func == "<module>":
                return f"{module_file} (module-level)"
            return f"{module_file}:{caller_line} in {caller_func}()"

        # Keep walking up
        if caller_key in all_stats:
            parent_callers = all_stats[caller_key][4]  # callers is index 4
            to_check.extend(parent_callers.keys())

    return None


class _SlowCall(TypedDict):
    """A slow function call detected during import profiling."""

    function: str
    file: str
    cumulative_secs: float
    calls: int
    call_site: str | None  # Where in user's module this was called from


def _format_slow_calls_report(
    path: str,
    total_secs: float,
    slow_calls: list[_SlowCall],
) -> str:
    """Format slow calls into a human-readable report."""
    lines = [
        "",
        f"Slow startup for '{path}' ({total_secs:.1f}s)",
        "",
        "    Slowest operations:",
    ]

    for call in slow_calls:
        secs = call["cumulative_secs"]
        func = call["function"]
        # Show last 2 path components for context (e.g., "requests/sessions.py:500")
        file_path = call["file"]
        parts = file_path.rsplit("/", 2)
        loc = "/".join(parts[-2:]) if len(parts) > 2 else file_path

        call_site = call.get("call_site")
        if call_site:
            lines.append(f"      {secs:>6.2f}s  {func:<24} {loc}")
            lines.append(f"               ↳ from {call_site}")
        else:
            lines.append(f"      {secs:>6.2f}s  {func:<24} {loc}")

    lines.extend(
        [
            "",
            "    Slow operations (network calls, file I/O, heavy computation) at",
            "    import time delay startup. Consider moving them inside functions",
            "    or using lazy initialization.",
            "",
        ]
    )

    return "\n".join(lines)


__all__ = [
    "profiled_import",
]
