"""Human-in-the-loop approval policy for the `execute` (shell) tool.

The `execute` tool runs LLM-authored bash — unsandboxed on the host under
`SANDBOX_PROVIDER=local`, inside a remote microVM under `e2b` (see
`sandbox_backend.py`). The gate applies identically in both modes (a sandbox
contains blast radius, not exfiltration or API misuse), so every call that is
not provably harmless pauses the run for human review (approve / edit /
reject) via langchain's `HumanInTheLoopMiddleware`.
`create_deep_agent(interrupt_on=...)` installs the middleware on the main
agent and threads the same config into every declarative subagent (including
the auto-added general-purpose one), so the policy holds across the roster.

Two deliberate carve-outs:

- `is_auto_approvable` skips the interrupt for a conservative read-only
  allowlist (`ls`, `cat`, `grep`, ...) so trivial pokes don't nag. The
  classifier fails closed: anything it cannot positively clear interrupts.
- `_run_rendercv` in `tools.py` calls `backend.execute()` directly with a
  developer-authored command — it never passes through the tool layer, so it
  is intentionally ungated.

Kill switch: `CAREER_AGENT_EXECUTE_APPROVAL=false` disables the gate entirely
(offline evals, emergency rollback). Default is on.

Lives in its own module (like `shell_backend.py`) so tests can exercise the
policy without triggering the eager `create_deep_agent()` call at `agents.py`
module load.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from langchain.agents.middleware import InterruptOnConfig
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from langchain.agents.middleware import ToolCallRequest

# Binaries safe to run without review: read-only, no exec/write/network escape
# hatches (`find -exec`, `sed -i`, `git push`, `python -c`, ... stay gated).
_SAFE_BINARIES = frozenset(
    {
        "cat",
        "date",
        "df",
        "du",
        "echo",
        "file",
        "grep",
        "head",
        "ls",
        "printf",
        "pwd",
        "rg",
        "stat",
        "tail",
        "tree",
        "uname",
        "wc",
        "which",
        "whoami",
    },
)

# Shell control/substitution/redirection/escape characters. Scanned on the RAW
# command string by design: a quoted `;` also prompts — conservative on
# purpose, since prompting is cheap and parsing shell quoting is not. Globs
# (`*?[`) stay allowed; expansion is contained by the backend cwd.
_UNSAFE_CHARS = frozenset(";&|<>`$(){}\\~\n\r")

_MAX_AUTO_APPROVE_LEN = 500


class HitlSettings(BaseSettings):
    """Env toggle for execute-tool human approval.

    `CAREER_AGENT_EXECUTE_APPROVAL=false` is the kill switch for offline
    evals or emergency rollback; approval defaults to on.
    """

    model_config = SettingsConfigDict(env_prefix="CAREER_AGENT_", extra="ignore")

    execute_approval: bool = True


def is_auto_approvable(command: str) -> bool:
    """Return True only for commands provably safe to run without review.

    Fail-closed allowlist: the command must be short, free of shell
    control/substitution characters, tokenize cleanly, invoke an allowlisted
    read-only binary, and touch no absolute (`/...`, host files like
    `/etc/passwd`) or traversal (`..`) paths — relative paths stay under the
    shell backend's cwd. Do NOT rely on `VirtualPathShellBackend._translate`'s
    shlex-rejoin quoting as a guard: it passes the raw command through on
    shlex errors, so it is not a security boundary.
    """
    if not command or len(command) > _MAX_AUTO_APPROVE_LEN:
        return False
    if any(ch in _UNSAFE_CHARS for ch in command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # Unbalanced quote / trailing backslash — the exact inputs that would
        # bypass the backend's rejoin quoting. Always review.
        return False
    if not tokens or tokens[0] not in _SAFE_BINARIES:
        return False
    return all(not t.startswith("/") and ".." not in t for t in tokens)


def should_interrupt_execute(request: ToolCallRequest) -> bool:
    """`when` predicate for the execute tool: True pauses for human review."""
    try:
        command = str(request.tool_call.get("args", {}).get("command", ""))
        return not is_auto_approvable(command)
    except Exception:
        return True


def execute_interrupt_on() -> dict[str, bool | InterruptOnConfig] | None:
    """Build the `interrupt_on` config for `create_deep_agent`, or None if off.

    Evaluated at agent build time so tests (and container restarts) pick up
    the current `CAREER_AGENT_EXECUTE_APPROVAL` value.
    """
    if not HitlSettings().execute_approval:
        return None
    return {
        "execute": InterruptOnConfig(
            allowed_decisions=["approve", "edit", "reject"],
            description="NextRole wants to run a shell command. Review it before it executes.",
            when=should_interrupt_execute,
        ),
    }
