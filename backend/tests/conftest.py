"""Fixtures shared by every agent package's tests."""

import importlib
import os
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest


@pytest.fixture
def with_shell(monkeypatch) -> Iterator[Callable[[str], ModuleType]]:
    """Reload agent modules under `SANDBOX_PROVIDER=local`, the execute opt-in.

    Each agent builds its backend and tools at import time, so the provider is
    fixed per import. The returned loader re-imports a module with the shell
    on; teardown restores the environment and reloads it back to the default.
    """
    reloaded: list[ModuleType] = []

    def load(module_name: str) -> ModuleType:
        monkeypatch.setenv("SANDBOX_PROVIDER", "local")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        module = importlib.reload(importlib.import_module(module_name))
        reloaded.append(module)
        return module

    yield load

    monkeypatch.undo()
    with pytest.MonkeyPatch.context() as restore:
        # Graph construction resolves the default model, which wants a key.
        restore.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "test-key"))
        for module in reloaded:
            importlib.reload(module)
