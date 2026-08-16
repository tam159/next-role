"""Unit tests for the execute-tool approval policy (allowlist + kill switch)."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from backend.agents.career_agent.execute_approval import (
    execute_interrupt_on,
    is_auto_approvable,
    should_interrupt_execute,
)

if TYPE_CHECKING:
    from langchain.agents.middleware import ToolCallRequest


def _request(**attrs: object) -> "ToolCallRequest":
    """Duck-typed ToolCallRequest — the predicate only reads `.tool_call`."""
    return cast("ToolCallRequest", SimpleNamespace(**attrs))


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la",
        "ls *.md",
        "cat notes.md",
        "head -20 report.md",
        "tail -n 5 log.txt",
        "grep -r foo .",
        "rg --files",
        "wc -l *.md",
        "echo hi",
        "printf hello",
        "date",
        "pwd",
        "whoami",
        "uname -a",
        "stat notes.md",
        "which python3",
        "du -sh sub_dir",
        "grep 'a b' notes.md",
    ],
)
def test_auto_approves_readonly_commands(command) -> None:
    """Short read-only commands with relative paths skip the interrupt."""
    assert is_auto_approvable(command)


@pytest.mark.parametrize(
    "command",
    [
        # Chaining / substitution / redirection / escapes.
        "ls; rm -rf /",
        "ls && rm -rf /",
        "cat x | sh",
        "echo $(rm x)",
        "echo `rm x`",
        "grep a notes.md > out.txt",
        "cat < secret.txt",
        "echo hi \\",
        "cat 'unbalanced",
        "echo ${HOME}",
        "cat ~/.ssh/id_rsa",
        "echo hi\nrm -rf /",
        # Quoted metacharacters still prompt — raw-string scan by design.
        "grep 'a;b' notes.md",
        # Absolute / traversal paths (host reads like /etc/passwd).
        "cat /etc/passwd",
        "cat ../../etc/passwd",
        "ls /",
        # Binaries outside the allowlist (write/exec/network escape hatches).
        "touch x.txt",
        "rm -rf sub_dir",
        "python -c 'print(1)'",
        "find . -name x",
        "sed -n 1p notes.md",
        "git status",
        "sh -c 'echo hi'",
        "sleep 1",
        # Env-assignment prefix means argv[0] is not the binary.
        "FOO=bar ls",
        # Degenerate inputs.
        "",
        "   ",
        "ls " + "x" * 600,
    ],
)
def test_interrupts_everything_else(command) -> None:
    """Anything not provably safe requires review (fail closed)."""
    assert not is_auto_approvable(command)


def test_should_interrupt_reads_command_arg() -> None:
    """The `when` predicate maps allowlist hits to False (no interrupt)."""
    safe = _request(tool_call={"args": {"command": "ls -la"}})
    risky = _request(tool_call={"args": {"command": "rm -rf /"}})
    assert not should_interrupt_execute(safe)
    assert should_interrupt_execute(risky)


def test_should_interrupt_fails_closed_on_bad_request() -> None:
    """Missing args, wrong shapes, or raising accessors all mean review."""
    assert should_interrupt_execute(_request(tool_call={}))
    assert should_interrupt_execute(_request(tool_call={"args": {}}))
    assert should_interrupt_execute(_request(tool_call=None))
    assert should_interrupt_execute(_request())


def test_execute_interrupt_on_default_enabled(monkeypatch) -> None:
    """Default config gates `execute` with approve/edit/reject."""
    monkeypatch.delenv("CAREER_AGENT_EXECUTE_APPROVAL", raising=False)
    config = execute_interrupt_on()
    assert config is not None
    execute_config = config["execute"]
    assert isinstance(execute_config, dict)
    assert execute_config["allowed_decisions"] == ["approve", "edit", "reject"]
    assert execute_config["when"] is should_interrupt_execute


def test_execute_interrupt_on_kill_switch(monkeypatch) -> None:
    """CAREER_AGENT_EXECUTE_APPROVAL=false disables the gate entirely."""
    monkeypatch.setenv("CAREER_AGENT_EXECUTE_APPROVAL", "false")
    assert execute_interrupt_on() is None
