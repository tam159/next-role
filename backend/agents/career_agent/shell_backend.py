"""Virtual-path-aware shell backend used by the career agent.

Lives in its own module (rather than inline in `agents.py`) so tests can
exercise the path-translation logic without triggering the eager
`create_deep_agent()` call at `agents.py` module load.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import LocalShellBackend

if TYPE_CHECKING:
    from deepagents.backends.protocol import ExecuteResponse


class VirtualPathShellBackend(LocalShellBackend):
    """`LocalShellBackend` that rewrites `/virtual/path` tokens to real disk paths.

    With `virtual_mode=True` the filesystem tools speak in virtual paths
    (`/tailored_resume/foo.yaml` maps to `<root_dir>/tailored_resume/foo.yaml`).
    The middleware's `execute` tool forwards the command string verbatim to
    `subprocess.run`, so a virtual path would be misread as a real absolute
    path on container disk. This subclass closes that gap: any `/`-prefixed
    token whose target (or whose parent dir) exists under `root_dir` is
    rewritten to its on-disk absolute form. Real absolute paths like
    `/tmp/foo` or `/usr/bin/python` are left alone because they neither
    exist nor have a parent under `root_dir`.
    """

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Translate virtual paths in `command`, then delegate to the parent backend."""
        return super().execute(self._translate(command), timeout=timeout)

    def _translate(self, command: str) -> str:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            # Unbalanced quotes — let the shell surface the real error.
            return command
        rewritten = [self._rewrite_token(t) for t in tokens]
        return shlex.join(rewritten)

    def _rewrite_token(self, token: str) -> str:
        if not token.startswith("/"):
            return token
        root = Path(self.cwd).resolve()
        candidate = (root / token.lstrip("/")).resolve()
        # Reject paths that escape root_dir via `..` — those are real absolute
        # paths the user intends, not virtual paths.
        try:
            candidate.relative_to(root)
        except ValueError:
            return token
        if candidate.exists() or candidate.parent.exists():
            return str(candidate)
        return token
