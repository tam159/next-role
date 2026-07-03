"""Unit tests for `VirtualPathShellBackend`.

Verifies that `/virtual/path` tokens in `execute()` commands are rewritten
to real on-disk absolute paths under `root_dir`, while real absolute paths
(`/tmp/x`, `/usr/bin/python`) and `..`-escape attempts are left untouched.
Also covers `default_shell_env`, the sanitized environment agent shell
commands run under.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend, default_shell_env


@pytest.fixture
def backend(tmp_path: Path) -> VirtualPathShellBackend:
    """Backend rooted at a real tmp dir with `/tailored_resume/` already on disk."""
    (tmp_path / "tailored_resume").mkdir()
    return VirtualPathShellBackend(root_dir=tmp_path, virtual_mode=True, timeout=10)


def test_translate_rewrites_existing_path(tmp_path: Path, backend: VirtualPathShellBackend) -> None:
    yaml = tmp_path / "tailored_resume" / "foo.yaml"
    yaml.write_text("cv: {}")
    translated = backend._translate(f"rendercv render /tailored_resume/{yaml.name}")  # noqa: SLF001
    assert translated == f"rendercv render {yaml}"


def test_translate_rewrites_when_only_parent_exists(
    tmp_path: Path,
    backend: VirtualPathShellBackend,
) -> None:
    """Output-path arguments (file doesn't exist yet) still translate via the parent dir."""
    expected = tmp_path / "tailored_resume" / "out.pdf"
    translated = backend._translate("rendercv render /tailored_resume/out.pdf")  # noqa: SLF001
    assert translated == f"rendercv render {expected}"


def test_translate_leaves_real_absolute_paths_alone(backend: VirtualPathShellBackend) -> None:
    """`/tmp/x`, `/etc/passwd`, `/usr/bin/python` are NOT under root_dir → unchanged."""
    assert backend._translate("cat /tmp/some-file-that-does-not-exist") == (  # noqa: SLF001
        "cat /tmp/some-file-that-does-not-exist"
    )
    assert backend._translate("cat /etc/passwd") == "cat /etc/passwd"  # noqa: SLF001


def test_translate_blocks_dotdot_escape(backend: VirtualPathShellBackend) -> None:
    """`/../etc/passwd` would resolve outside root_dir → left as-is (no rewrite)."""
    assert backend._translate("cat /../../etc/passwd") == "cat /../../etc/passwd"  # noqa: SLF001


def test_translate_preserves_quoted_arguments(backend: VirtualPathShellBackend) -> None:
    translated = backend._translate('echo "hello world"')  # noqa: SLF001
    assert translated == "echo 'hello world'"


def test_translate_leaves_flags_unchanged(backend: VirtualPathShellBackend) -> None:
    assert backend._translate("ls -la") == "ls -la"  # noqa: SLF001
    assert backend._translate("rendercv --version") == "rendercv --version"  # noqa: SLF001


def test_translate_handles_unbalanced_quotes(backend: VirtualPathShellBackend) -> None:
    """shlex.split raises on unbalanced quotes — fall through, let the shell complain."""
    bad = "echo 'unterminated"
    assert backend._translate(bad) == bad  # noqa: SLF001


def test_execute_runs_translated_command(
    tmp_path: Path,
    backend: VirtualPathShellBackend,
) -> None:
    """End-to-end: write a file under a virtual root, `cat` it via virtual path."""
    yaml = tmp_path / "tailored_resume" / "hello.txt"
    yaml.write_text("hello-from-virtual-path\n")
    result = backend.execute("cat /tailored_resume/hello.txt")
    assert result.exit_code == 0
    assert "hello-from-virtual-path" in result.output


class TestDefaultShellEnv:
    """`default_shell_env` — the sanitized env handed to agent shell commands."""

    def test_path_resolves_interpreter_bin_first(self):
        """The running interpreter's bin dir leads PATH (venv console scripts win)."""
        env = default_shell_env()

        first_entry = env["PATH"].split(os.pathsep)[0]
        assert first_entry == str(Path(sys.executable).parent)

    def test_inherits_process_path_entries(self):
        """System PATH entries stay reachable behind the interpreter's bin dir."""
        env = default_shell_env()

        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
            assert entry in env["PATH"].split(os.pathsep)

    def test_exposes_no_other_variables(self):
        """Secrets (API keys etc.) must never leak into agent subprocesses."""
        assert set(default_shell_env().keys()) == {"PATH"}

    def test_console_scripts_of_this_environment_resolve(self):
        """The venv's own console scripts must be findable via the built PATH."""
        env = default_shell_env()

        found = shutil.which("rendercv", path=env["PATH"])
        assert found is not None
