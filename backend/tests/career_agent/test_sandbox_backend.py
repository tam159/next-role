"""Unit tests for the E2B-protocol sandbox backend (fake SDK, no network).

The fake `e2b` module below is monkeypatched over `sandbox_backend._e2b`, so
these tests exercise the full backend logic — thread scoping, reconnect,
stale-handle healing, in-band error mapping — deterministically.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.agents.career_agent import sandbox_backend
from backend.agents.career_agent.render_scratch import RENDER_DIR_PREFIX, SandboxRenderScratch
from backend.agents.career_agent.sandbox_backend import (
    E2BSandboxBackend,
    SandboxSettings,
    make_default_shell_backend,
)
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend
from deepagents.backends.protocol import FILE_NOT_FOUND, SandboxBackendProtocol

# --- fake e2b SDK -------------------------------------------------------------


class _CommandExit(Exception):  # noqa: N818 - mirrors the SDK's CommandExitException name
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        super().__init__(stderr or stdout)
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _Timeout(Exception): ...  # noqa: N818 - mirrors the SDK name


class _SandboxNotFound(Exception): ...  # noqa: N818 - mirrors the SDK name


class _NotFound(Exception): ...  # noqa: N818 - mirrors the SDK name


class _FileNotFound(Exception): ...  # noqa: N818 - mirrors the SDK name


class _FakeSandbox:
    """One live 'sandbox': dict filesystem + scripted command runner."""

    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.fs: dict[str, bytes] = {}
        self.timeouts: list[int] = []
        self.run_calls: list[tuple[str, str | None, float | None]] = []
        self.run_behavior = None  # optional callable(command) -> result / raises
        self.write_errors: dict[str, Exception] = {}
        self.set_timeout_error: Exception | None = None
        self.commands = SimpleNamespace(run=self._run)
        self.files = SimpleNamespace(write=self._write, read=self._read)

    def set_timeout(self, timeout: int) -> None:
        if self.set_timeout_error is not None:
            raise self.set_timeout_error
        self.timeouts.append(timeout)

    def _run(self, cmd: str, cwd: str | None = None, timeout: float | None = None):
        self.run_calls.append((cmd, cwd, timeout))
        if self.run_behavior is not None:
            return self.run_behavior(cmd)
        return SimpleNamespace(stdout=f"ran:{cmd}", stderr="", exit_code=0)

    def _write(self, path: str, data: bytes) -> None:
        if path in self.write_errors:
            raise self.write_errors[path]
        self.fs[path] = data

    def _read(self, path: str, format: str = "text"):  # noqa: A002 - SDK kwarg name
        assert format == "bytes"  # the backend must read bytes-safe
        if path not in self.fs:
            raise _FileNotFound(path)
        return bytearray(self.fs[path])


class _FakeCluster:
    """Server-side registry backing the fake `Sandbox` classmethods."""

    def __init__(self) -> None:
        self.sandboxes: dict[str, _FakeSandbox] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.create_calls: list[dict] = []
        self.connect_calls: list[str] = []
        self.create_error: Exception | None = None
        self.list_error: Exception | None = None
        self.counter = 0


def _fake_module(cluster: _FakeCluster) -> SimpleNamespace:
    class _Sandbox:
        @staticmethod
        def create(**kwargs) -> _FakeSandbox:
            cluster.create_calls.append(kwargs)
            if cluster.create_error is not None:
                raise cluster.create_error
            cluster.counter += 1
            sbx = _FakeSandbox(f"sbx-{cluster.counter}")
            cluster.sandboxes[sbx.sandbox_id] = sbx
            cluster.metadata[sbx.sandbox_id] = kwargs.get("metadata") or {}
            return sbx

        @staticmethod
        def list(query=None, **_kwargs) -> SimpleNamespace:
            if cluster.list_error is not None:
                raise cluster.list_error
            wanted = (query.metadata if query is not None else None) or {}
            items = [
                SimpleNamespace(sandbox_id=sid)
                for sid, meta in cluster.metadata.items()
                if sid in cluster.sandboxes and all(meta.get(k) == v for k, v in wanted.items())
            ]
            return SimpleNamespace(has_next=bool(items), next_items=lambda: items)

        @staticmethod
        def connect(sandbox_id, timeout=None, **_kwargs) -> _FakeSandbox:
            del timeout
            cluster.connect_calls.append(sandbox_id)
            if sandbox_id not in cluster.sandboxes:
                raise _SandboxNotFound(sandbox_id)
            return cluster.sandboxes[sandbox_id]

    class _Query:
        def __init__(self, metadata=None) -> None:
            self.metadata = metadata

    return SimpleNamespace(
        Sandbox=_Sandbox,
        SandboxQuery=_Query,
        CommandExitException=_CommandExit,
        TimeoutException=_Timeout,
        SandboxNotFoundException=_SandboxNotFound,
        NotFoundException=_NotFound,
        FileNotFoundException=_FileNotFound,
    )


# --- fixtures ------------------------------------------------------------------

_SANDBOX_ENV_VARS = (
    "SANDBOX_PROVIDER",
    "SANDBOX_E2B_API_URL",
    "SANDBOX_E2B_API_KEY",
    "SANDBOX_TEMPLATE",
    "SANDBOX_TTL_SECONDS",
    "SANDBOX_EXECUTE_TIMEOUT",
    "SANDBOX_CWD",
    "SANDBOX_MAX_OUTPUT_BYTES",
)


@pytest.fixture(autouse=True)
def _clean_sandbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real SANDBOX_* env out of settings construction."""
    for name in _SANDBOX_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cluster() -> _FakeCluster:
    return _FakeCluster()


def _settings(max_output_bytes: int = 100_000) -> SandboxSettings:
    return SandboxSettings(
        provider="e2b",
        e2b_api_url="http://sandbox:3000",
        e2b_api_key="test-key",
        template="tpl-1",
        ttl_seconds=900,
        execute_timeout=45,
        max_output_bytes=max_output_bytes,
    )


@pytest.fixture
def backend(cluster: _FakeCluster, monkeypatch: pytest.MonkeyPatch) -> E2BSandboxBackend:
    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    return E2BSandboxBackend(_settings())


def _set_thread(monkeypatch: pytest.MonkeyPatch, thread_id: str) -> None:
    monkeypatch.setattr(
        sandbox_backend,
        "get_config",
        lambda: {"configurable": {"thread_id": thread_id}},
    )


def _sole_sandbox(cluster: _FakeCluster) -> _FakeSandbox:
    assert len(cluster.sandboxes) == 1
    return next(iter(cluster.sandboxes.values()))


# --- execute mapping -----------------------------------------------------------


def test_execute_success_maps_output_and_defaults(backend, cluster):
    response = backend.execute("echo hi")

    assert response.exit_code == 0
    assert response.output == "ran:echo hi"
    assert not response.truncated
    cmd, cwd, timeout = _sole_sandbox(cluster).run_calls[0]
    assert cmd == "echo hi"
    assert cwd == "/home/user"
    assert timeout == 45  # settings default when the tool passes no timeout


def test_execute_per_call_timeout_wins(backend, cluster):
    backend.execute("sleep 1", timeout=5)

    assert _sole_sandbox(cluster).run_calls[0][2] == 5


def test_nonzero_exit_is_returned_in_band(backend, cluster):
    backend.execute("warm up")
    sandbox = _sole_sandbox(cluster)

    def _raise(_cmd):
        raise _CommandExit(stdout="partial", stderr="boom", exit_code=2)

    sandbox.run_behavior = _raise
    response = backend.execute("false")

    assert response.exit_code == 2
    assert response.output == "partial\nboom"


def test_command_timeout_maps_to_exit_124(backend, cluster):
    backend.execute("warm up")

    def _raise(_cmd):
        raise _Timeout

    _sole_sandbox(cluster).run_behavior = _raise
    response = backend.execute("sleep 999")

    assert response.exit_code == 124
    assert "timed out after 45s" in response.output


def test_endpoint_down_returns_in_band_error(backend, cluster):
    cluster.create_error = ConnectionError("connection refused")

    response = backend.execute("echo hi")

    assert response.exit_code == 1
    assert response.output.startswith("Error: sandbox unavailable (ConnectionError")


def test_unconfigured_provider_names_missing_settings(cluster, monkeypatch):
    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    unconfigured = E2BSandboxBackend(SandboxSettings(provider="e2b"))

    response = unconfigured.execute("echo hi")

    assert response.exit_code == 1
    assert "SANDBOX_E2B_API_KEY" in response.output
    assert "SANDBOX_TEMPLATE" in response.output
    assert cluster.create_calls == []


def test_output_truncated_at_max_bytes(cluster, monkeypatch):
    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    small = E2BSandboxBackend(_settings(10))

    small.execute("warm up")
    _sole_sandbox(cluster).run_behavior = lambda _cmd: SimpleNamespace(
        stdout="x" * 100,
        stderr="",
        exit_code=0,
    )
    response = small.execute("yes")

    assert response.truncated
    assert len(response.output.encode()) <= 10


# --- lifecycle: scoping, reconnect, healing -------------------------------------


def test_thread_scoped_reuse_and_isolation(backend, cluster, monkeypatch):
    _set_thread(monkeypatch, "t1")
    backend.execute("one")
    backend.execute("two")
    assert len(cluster.create_calls) == 1
    assert cluster.metadata["sbx-1"] == {"nextrole_scope": "default:t1"}

    _set_thread(monkeypatch, "t2")
    backend.execute("three")
    assert len(cluster.create_calls) == 2


def test_create_passes_no_envs_and_the_template(backend, cluster):
    backend.execute("echo hi")

    call = cluster.create_calls[0]
    assert call["envs"] == {}  # no host env / secrets ever enter the sandbox
    assert call["template"] == "tpl-1"
    assert call["timeout"] == 900
    assert call["api_key"] == "test-key"
    assert call["api_url"] == "http://sandbox:3000"


def test_reconnects_by_metadata_after_restart(backend, cluster, monkeypatch):
    backend.execute("echo hi")  # first process creates sbx-1

    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    restarted = E2BSandboxBackend(_settings())
    response = restarted.execute("echo again")

    assert response.exit_code == 0
    assert cluster.connect_calls == ["sbx-1"]
    assert len(cluster.create_calls) == 1  # reused, not recreated


def test_list_failure_falls_back_to_create(cluster, monkeypatch):
    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    cluster.list_error = RuntimeError("metadata queries unsupported")
    fresh = E2BSandboxBackend(_settings())

    response = fresh.execute("echo hi")

    assert response.exit_code == 0
    assert len(cluster.create_calls) == 1


def test_stale_sandbox_heals_once(backend, cluster):
    backend.execute("warm up")  # creates and caches sbx-1
    stale = cluster.sandboxes.pop("sbx-1")  # server-side eviction (TTL)
    cluster.metadata.pop("sbx-1")

    def _raise(_cmd):
        gone = "sbx-1"
        raise _SandboxNotFound(gone)

    stale.run_behavior = _raise
    response = backend.execute("echo hi")

    assert response.exit_code == 0
    assert response.output == "ran:echo hi"
    assert len(cluster.create_calls) == 2  # healed with a fresh sandbox


def test_ttl_refreshed_per_call_and_refresh_failure_is_ignored(backend, cluster):
    backend.execute("one")
    sandbox = _sole_sandbox(cluster)
    assert sandbox.timeouts == [900]

    sandbox.set_timeout_error = RuntimeError("refresh down")
    assert backend.execute("two").exit_code == 0


def test_id_is_placeholder_until_first_use(backend, cluster):
    assert backend.id == "e2b:unbound"
    backend.execute("echo hi")
    assert backend.id == "sbx-1"
    assert cluster.sandboxes[backend.id] is not None


# --- files -----------------------------------------------------------------------


def test_upload_partial_success_preserves_order(backend, cluster):
    backend.execute("warm up")
    sandbox = _sole_sandbox(cluster)
    sandbox.write_errors["/sandbox/bad.bin"] = PermissionError("read-only")

    responses = backend.upload_files(
        [("/sandbox/ok.bin", b"\xff\x00binary"), ("/sandbox/bad.bin", b"nope")],
    )

    assert [r.path for r in responses] == ["/sandbox/ok.bin", "/sandbox/bad.bin"]
    assert responses[0].error is None
    assert responses[1].error is not None
    assert "PermissionError" in responses[1].error


def test_download_maps_not_found_and_roundtrips_bytes(backend, cluster):
    backend.upload_files([("/sandbox/data.bin", b"\xff\x00binary")])

    responses = backend.download_files(["/sandbox/data.bin", "/sandbox/missing.bin"])

    assert responses[0].content == b"\xff\x00binary"
    assert responses[0].error is None
    assert responses[1].content is None
    assert responses[1].error == FILE_NOT_FOUND


def test_files_report_unavailable_endpoint_per_entry(cluster, monkeypatch):
    monkeypatch.setattr(sandbox_backend, "_e2b", lambda: _fake_module(cluster))
    cluster.create_error = ConnectionError("connection refused")
    backend = E2BSandboxBackend(_settings())

    uploads = backend.upload_files([("/sandbox/a", b"a"), ("/sandbox/b", b"b")])
    downloads = backend.download_files(["/sandbox/a"])

    assert all(r.error is not None for r in uploads)
    assert downloads[0].error is not None


# --- provider factory & invariants -----------------------------------------------


def test_factory_defaults_to_local_shell_backend():
    backend = make_default_shell_backend()

    assert isinstance(backend, VirtualPathShellBackend)
    assert Path(backend.cwd) == sandbox_backend._CAREER_AGENT_DIR.resolve()  # noqa: SLF001


def test_factory_selects_e2b_from_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROVIDER", "e2b")

    backend = make_default_shell_backend()

    assert isinstance(backend, E2BSandboxBackend)
    assert isinstance(backend, SandboxBackendProtocol)  # the middleware's isinstance gate


def test_capture_offload_stays_disabled():
    assert E2BSandboxBackend.enable_capture_offload is False


def test_open_render_scratch_lives_in_the_sandbox(backend):
    scratch = backend.open_render_scratch()

    assert isinstance(scratch, SandboxRenderScratch)
    assert scratch.dir.startswith(f"/tmp/{RENDER_DIR_PREFIX}")  # noqa: S108 - sandbox path
