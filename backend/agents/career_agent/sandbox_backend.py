"""E2B-protocol sandbox backend: isolated shell execution for the career agent.

`make_default_shell_backend()` picks the composite-default shell backend from
`SANDBOX_PROVIDER` (read once at graph build):

* ``local`` (default) — today's `VirtualPathShellBackend`: subprocesses on the
  backend host, zero external moving parts; the HiL approval gate
  (execute_approval.py) is the safety layer.
* ``e2b`` — `E2BSandboxBackend`: every `execute` (and the derived file tools on
  unrouted paths) runs inside a remote hardware-isolated microVM reached over
  the E2B API — a self-hosted CubeSandbox deployment (deploy/cubesandbox/) or
  E2B Cloud, selected purely by `SANDBOX_E2B_API_URL`. The HiL gate applies
  identically: it keys on the `execute` tool, not on the backend.

Sandboxes are **thread-scoped** (deepagents' recommended lifecycle): the first
command on a thread creates one, later turns reuse it — rediscovered through
sandbox metadata after a backend restart — and the provider reaps it after
`SANDBOX_TTL_SECONDS` idle. Secrets never enter the sandbox: creation passes
``envs={}``, the remote parallel of `default_shell_env()`'s PATH-only stance.

Every failure is mapped **in-band** into an `ExecuteResponse` / per-file error:
the filesystem middleware's tool wrapper only catches `NotImplementedError` and
`ValueError`, so a raising backend would crash the run instead of handing the
model a ToolMessage it can react to.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Literal

from backend.agents.career_agent.render_scratch import SandboxRenderScratch
from backend.agents.career_agent.scope import current_identity
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend, default_shell_env
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langgraph.config import get_config
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Same directory as `tools.CAREER_AGENT_DIR` (this module lives beside tools.py);
#: defined locally so the provider factory doesn't import the heavy tools module.
_CAREER_AGENT_DIR = Path(__file__).parent

#: Sandbox metadata key carrying the identity:thread scope (rediscovery after restarts).
_METADATA_SCOPE_KEY = "nextrole_scope"


def _e2b() -> Any:  # noqa: ANN401 - module object; typed as Any for the lazy-import test seam
    """Import and return the e2b SDK module (monkeypatch seam for unit tests)."""
    import e2b

    return e2b


class SandboxSettings(BaseSettings):
    """`SANDBOX_*` env config for the execute tool's shell backend.

    Every field is defaulted so importing `agents.py` (which core-server does
    just to enumerate graphs) never requires env; `e2b` connection values are
    validated lazily at first sandbox use. `SANDBOX_E2B_API_URL` targets a
    self-hosted CubeSandbox (`http://<host>:3000`); leave it empty to use the
    E2B Cloud default domain. Docs: README "Environment variables".
    """

    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")

    provider: Literal["local", "e2b"] = "local"
    e2b_api_url: str = ""
    e2b_api_key: str = ""
    template: str = ""
    ttl_seconds: int = 1800
    execute_timeout: int = 60  # parity with VirtualPathShellBackend(timeout=60)
    cwd: str = "/home/user"
    max_output_bytes: int = 100_000  # parity with LocalShellBackend truncation


class E2BSandboxBackend(BaseSandbox):
    """`BaseSandbox` over the E2B SDK with thread-scoped sandbox resolution.

    The instance is a module-level singleton (deepagents 0.7 style); the actual
    sandbox is resolved per call from the run config (`thread_id` + authenticated
    identity), cached in-process, and rediscovered via sandbox metadata.
    """

    def __init__(self, settings: SandboxSettings | None = None) -> None:
        """Store settings only — no SDK import, no network until first use."""
        self._settings = settings or SandboxSettings()
        self._lock = threading.Lock()
        self._sandboxes: dict[str, Any] = {}

    # -- protocol surface ---------------------------------------------------

    @property
    def id(self) -> str:
        """Sandbox id for the current scope, or a placeholder before first use."""
        sandbox = self._sandboxes.get(self._scope_key())
        return getattr(sandbox, "sandbox_id", None) or "e2b:unbound"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run `command` in the caller's sandbox, mapping every failure in-band."""
        effective = timeout if timeout is not None else self._settings.execute_timeout
        try:
            sandbox = self._sandbox_for_current_run()
            return self._run_command(sandbox, command, effective)
        except Exception as first:
            if not self._is_stale(first):
                return self._unavailable_response(first)
            self._invalidate_current()
        # The sandbox vanished under us (TTL/eviction): resolve a fresh one and
        # retry exactly once — the command never started, so this is safe.
        try:
            sandbox = self._sandbox_for_current_run()
            return self._run_command(sandbox, command, effective)
        except Exception as exc:
            return self._unavailable_response(exc)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files into the sandbox with per-file partial success."""
        try:
            sandbox = self._sandbox_for_current_run()
        except Exception as exc:
            logger.warning("sandbox unavailable for upload", exc_info=exc)
            error = f"sandbox unavailable: {type(exc).__name__}: {exc}"
            return [FileUploadResponse(path=path, error=error) for path, _ in files]
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                # envd creates missing parent dirs (the protocol requires it).
                sandbox.files.write(path, content)
                responses.append(FileUploadResponse(path=path))
            except Exception as exc:
                responses.append(FileUploadResponse(path=path, error=self._file_error(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files from the sandbox with per-file partial success."""
        try:
            sandbox = self._sandbox_for_current_run()
        except Exception as exc:
            logger.warning("sandbox unavailable for download", exc_info=exc)
            error = f"sandbox unavailable: {type(exc).__name__}: {exc}"
            return [FileDownloadResponse(path=path, error=error) for path in paths]
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                data = sandbox.files.read(path, format="bytes")
                responses.append(FileDownloadResponse(path=path, content=bytes(data)))
            except Exception as exc:
                responses.append(FileDownloadResponse(path=path, error=self._file_error(exc)))
        return responses

    def open_render_scratch(self) -> SandboxRenderScratch:
        """Scratch dir inside the sandbox for rendercv runs (see render_scratch.py)."""
        return SandboxRenderScratch(self)

    # -- sandbox lifecycle ---------------------------------------------------

    def _scope_key(self) -> str:
        """`<identity>:<thread_id>` for the active run; stable fallbacks outside one."""
        try:
            configurable = get_config().get("configurable") or {}
            thread_id = configurable.get("thread_id")
        except RuntimeError:  # bare invoke / unit test outside a runnable context
            thread_id = None
        identity = current_identity()
        return f"{identity or 'default'}:{thread_id or 'adhoc'}"

    def _sandbox_for_current_run(self) -> Any:  # noqa: ANN401 - SDK handle (lazy import)
        """Return the live sandbox for the caller's scope: cache → metadata → create."""
        key = self._scope_key()
        with self._lock:
            sandbox = self._sandboxes.get(key)
            if sandbox is not None:
                return sandbox
            self._require_e2b_config()
            sandbox = self._reconnect(key)
            if sandbox is None:
                sandbox = _e2b().Sandbox.create(
                    template=self._settings.template,
                    timeout=self._settings.ttl_seconds,
                    metadata={_METADATA_SCOPE_KEY: key},
                    # No env vars cross into the sandbox — the remote parallel of
                    # default_shell_env()'s "PATH only, never secrets" stance.
                    envs={},
                    **self._conn_params(),
                )
                logger.info("created sandbox %s for scope %s", sandbox.sandbox_id, key)
            self._sandboxes[key] = sandbox
            return sandbox

    def _reconnect(self, key: str) -> Any | None:  # noqa: ANN401 - SDK handle (lazy import)
        """Find a still-alive sandbox for `key` by metadata (survives restarts)."""
        e2b = _e2b()
        try:
            paginator = e2b.Sandbox.list(
                query=e2b.SandboxQuery(metadata={_METADATA_SCOPE_KEY: key}),
                **self._conn_params(),
            )
            infos = paginator.next_items() if paginator.has_next else []
        except Exception:
            logger.warning("sandbox list-by-metadata failed; will create fresh", exc_info=True)
            return None
        for info in infos:
            try:
                # Classmethod variant: connects by id and resumes paused sandboxes.
                sandbox = e2b.Sandbox.connect(
                    info.sandbox_id,
                    timeout=self._settings.ttl_seconds,
                    **self._conn_params(),
                )
            except Exception:
                logger.debug("sandbox %s not connectable", info.sandbox_id, exc_info=True)
                continue
            logger.info("reconnected sandbox %s for scope %s", info.sandbox_id, key)
            return sandbox
        return None

    def _invalidate_current(self) -> None:
        """Drop the cached handle for the caller's scope (stale-sandbox healing)."""
        with self._lock:
            self._sandboxes.pop(self._scope_key(), None)

    def _require_e2b_config(self) -> None:
        """Fail lazily (and clearly) when the e2b provider is missing its config."""
        required = (
            ("SANDBOX_E2B_API_KEY", self._settings.e2b_api_key),
            ("SANDBOX_TEMPLATE", self._settings.template),
        )
        missing = [name for name, value in required if not value]
        if missing:
            msg = (
                f"Sandbox provider 'e2b' is not configured: set {', '.join(missing)} "
                "(and SANDBOX_E2B_API_URL for a self-hosted CubeSandbox) — see .env.example"
            )
            raise RuntimeError(msg)

    def _conn_params(self) -> dict[str, Any]:
        """E2B connection kwargs from settings; empty api_url → SDK default (E2B Cloud)."""
        params: dict[str, Any] = {"api_key": self._settings.e2b_api_key}
        if self._settings.e2b_api_url:
            params["api_url"] = self._settings.e2b_api_url
        return params

    # -- command execution ----------------------------------------------------

    def _run_command(
        self,
        sandbox: Any,  # noqa: ANN401 - SDK handle (lazy import)
        command: str,
        timeout: int,
    ) -> ExecuteResponse:
        """Run one command, mapping SDK results and exit/timeout raises in-band."""
        e2b = _e2b()
        self._refresh_ttl(sandbox)
        try:
            result = sandbox.commands.run(command, cwd=self._settings.cwd, timeout=timeout)
        except e2b.CommandExitException as exc:
            # The SDK *raises* on non-zero exit; the agent needs it as a plain result.
            return self._as_execute_response(exc.stdout, exc.stderr, exc.exit_code)
        except e2b.TimeoutException:
            return ExecuteResponse(
                output=f"Error: command timed out after {timeout}s in the sandbox",
                exit_code=124,
            )
        return self._as_execute_response(result.stdout, result.stderr, result.exit_code)

    def _refresh_ttl(self, sandbox: Any) -> None:  # noqa: ANN401 - SDK handle (lazy import)
        """Best-effort idle-TTL refresh; relative seconds, applied server-side."""
        try:
            sandbox.set_timeout(self._settings.ttl_seconds)
        except Exception:
            logger.debug("sandbox TTL refresh failed", exc_info=True)

    def _as_execute_response(
        self,
        stdout: str,
        stderr: str,
        exit_code: int | None,
    ) -> ExecuteResponse:
        """Combine streams and truncate at `max_output_bytes` (LocalShellBackend parity)."""
        output = stdout or ""
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        truncated = False
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) > self._settings.max_output_bytes:
            output = encoded[: self._settings.max_output_bytes].decode("utf-8", errors="replace")
            truncated = True
        return ExecuteResponse(
            output=output,
            exit_code=exit_code if exit_code is not None else 0,
            truncated=truncated,
        )

    def _is_stale(self, exc: Exception) -> bool:
        """Return True when `exc` means the cached sandbox no longer exists server-side."""
        e2b = _e2b()
        return isinstance(exc, (e2b.SandboxNotFoundException, e2b.NotFoundException))

    def _unavailable_response(self, exc: Exception) -> ExecuteResponse:
        """In-band failure the model can react to (the tool wrapper must not see raises)."""
        logger.warning("sandbox execution unavailable", exc_info=exc)
        return ExecuteResponse(
            output=(
                f"Error: sandbox unavailable ({type(exc).__name__}: {exc}). "
                "Check the SANDBOX_* settings and the sandbox host "
                "(deploy/cubesandbox/README.md)."
            ),
            exit_code=1,
        )

    def _file_error(self, exc: Exception) -> str:
        """Map SDK file errors to protocol literals, else a readable message."""
        e2b = _e2b()
        not_found = tuple(
            cls
            for cls in (
                getattr(e2b, "FileNotFoundException", None),
                getattr(e2b, "NotFoundException", None),
            )
            if cls is not None
        )
        if not_found and isinstance(exc, not_found):
            return FILE_NOT_FOUND
        return f"{type(exc).__name__}: {exc}"


def make_default_shell_backend(
    settings: SandboxSettings | None = None,
) -> VirtualPathShellBackend | E2BSandboxBackend:
    """Composite-default shell backend for the configured `SANDBOX_PROVIDER`.

    ``local`` returns the host-subprocess backend with its historical
    construction (byte-for-byte pre-sandbox behavior — the rollback lever);
    ``e2b`` returns the remote sandbox backend. Read at graph build time, so
    changing `.env` needs a `docker compose up -d backend` recreate.
    """
    settings = settings or SandboxSettings()
    if settings.provider == "e2b":
        return E2BSandboxBackend(settings)
    return VirtualPathShellBackend(
        root_dir=_CAREER_AGENT_DIR,
        virtual_mode=True,
        timeout=60,
        env=default_shell_env(),
    )
