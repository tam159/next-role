"""Smoke test against a real E2B-compatible endpoint (CubeSandbox or E2B Cloud).

Opt-in and never run in CI: set `SANDBOX_E2B_API_URL` (plus
`SANDBOX_E2B_API_KEY` and `SANDBOX_TEMPLATE`) to a live endpoint — see
deploy/cubesandbox/README.md — then run
`uv run pytest -m integration tests/career_agent/test_sandbox_backend_integration.py`.
"""

import os

import pytest
from backend.agents.career_agent.sandbox_backend import E2BSandboxBackend, SandboxSettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SANDBOX_E2B_API_URL"),
        reason="SANDBOX_E2B_API_URL not set (needs a live CubeSandbox / E2B endpoint)",
    ),
]


def test_execute_and_files_roundtrip_in_real_sandbox():
    backend = E2BSandboxBackend(SandboxSettings(provider="e2b"))

    result = backend.execute("echo hello-from-sandbox && uname -a")
    assert result.exit_code == 0, result.output
    assert "hello-from-sandbox" in result.output

    # The template contract (deploy/cubesandbox/template/Dockerfile): rendercv
    # must resolve inside the sandbox, or renders fail with exit 127.
    version = backend.execute("rendercv --version")
    assert version.exit_code == 0, version.output

    # envd semantics the backend relies on: parent dirs auto-created on write,
    # bytes roundtrip unmangled (non-UTF8 payload).
    probe = "/tmp/nextrole-int/nested/probe.bin"  # noqa: S108 - path inside the sandbox
    uploads = backend.upload_files([(probe, b"\xffbinary")])
    assert uploads[0].error is None, uploads[0].error
    downloads = backend.download_files([probe])
    assert downloads[0].error is None, downloads[0].error
    assert downloads[0].content == b"\xffbinary"
