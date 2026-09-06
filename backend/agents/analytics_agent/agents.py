"""Define the analytics agent.

A second graph beside the career agent, registered in `LANGSERVE_GRAPHS` and
selected from the UI's agent picker. Single-purpose: it reads the ClickHouse
warehouse and shows the answer.

It reuses the career agent's plumbing rather than copying it — the model
override middleware, the UTC date stamp, the shell backend factory, the
execute-approval policy, and the object-storage key builders all live in
`career_agent/` as shared modules. Only `career_agent/agents.py` builds a graph
at import time, and nothing here imports it.
"""

from pathlib import Path
from typing import Any

from backend.agents.analytics_agent import prompts as _prompts
from backend.agents.analytics_agent.metadata import SnapshotCache
from backend.agents.analytics_agent.middleware import AnalyticsAccessMiddleware
from backend.agents.analytics_agent.tools import (
    make_create_chart,
    make_describe_data,
    make_run_sql,
)
from backend.agents.career_agent.execute_approval import execute_interrupt_on
from backend.agents.career_agent.middleware import ModelOverrideMiddleware, UtcDatetimeMiddleware
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.sandbox_backend import make_default_shell_backend
from backend.agents.career_agent.scope import kv_namespace
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION, FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

#: Same bake-time default as the career agent, so the frontend's model picker
#: (`configurable.main_agent_model`, read by ModelOverrideMiddleware) behaves
#: identically for both agents.
_MODEL = "openai:gpt-5.6-terra"

#: KV/object root for this agent's artifacts. Registered in
#: `object_storage.AREA_ROOTS`, which is what maps `/charts/` and `/reports/`
#: to `users/<scope>/analytics_agent/...`.
_AGENT_ROOT = "analytics_agent"

ANALYTICS_AGENT_DIR = Path(__file__).parent

_backend = CompositeBackend(
    # Skills and the memory file are read through this default backend, so it
    # is rooted at this package rather than the career agent's.
    default=make_default_shell_backend(root_dir=ANALYTICS_AGENT_DIR),
    routes={
        # Durable, per-user, and surfaced in the UI's Files panel.
        "/charts/": ObjectStoreBackend("charts"),
        "/reports/": ObjectStoreBackend("reports"),
        # Offloaded large tool results — deepagents writes here on its own.
        "/large_tool_results/": StoreBackend(
            namespace=lambda _rt: kv_namespace("large_tool_results", agent_root=_AGENT_ROOT),
        ),
    },
)

# Name-matched overrides: deepagents 0.7 merges `middleware=` by `.name`, so an
# instance whose name matches a default replaces it in place. This is the
# supported way to customize the built-in middlewares' prompts.
_fs_middleware = FilesystemMiddleware(
    backend=_backend,
    system_prompt=_prompts.FILE_TOOLS,
    custom_tool_descriptions={
        "execute": EXECUTE_TOOL_DESCRIPTION + "\n\n" + _prompts.EXECUTE_GUARDRAIL,
    },
)

_MEMORY_SOURCES = ["ANALYTICS_AGENT.md"]

_memory_middleware = MemoryMiddleware(
    backend=_backend,
    sources=_MEMORY_SOURCES,
    add_cache_control=True,  # match the default instance this replaces
    system_prompt=_prompts.MEMORY,
)

# One metadata cache shared by every run in this process: the dbt manifest is
# ~800 KB and the semantics it carries change at deploy cadence, not per turn.
_catalog = SnapshotCache()

_describe_data = make_describe_data(_catalog)
_run_sql = make_run_sql(_backend)
_create_chart = make_create_chart(_backend)


def build_analytics_agent(
    model: str | BaseChatModel = _MODEL,
    *,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Build the analytics-agent graph.

    Args:
        model: Main-agent model; per-run overrides ride ModelOverrideMiddleware.
            Tests pass a fake chat model to assemble the graph offline.
        store: Explicit LangGraph store for offline construction/tests. The
            server runtime injects its own when this is None.

    """
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # First: an unauthorized caller must not reach the model, let alone a
        # tool that reads other users' activity.
        AnalyticsAccessMiddleware(),
        ModelOverrideMiddleware(),
        # Opt-in in 0.7. Multi-step analyses benefit from a visible plan, and
        # the frontend's Plan panel reads the same `todos` channel.
        TodoListMiddleware(),
        UtcDatetimeMiddleware(),
        # Name-matched overrides (position in this list does not matter):
        _fs_middleware,
        _memory_middleware,
    ]
    return create_deep_agent(
        system_prompt=_prompts.SYSTEM_PROMPT,
        model=model,
        memory=_MEMORY_SOURCES,
        skills=["skills/analytics-agent/"],
        tools=[_describe_data, _run_sql, _create_chart],
        backend=_backend,
        middleware=middleware,
        # Shared policy with the career agent (CAREER_AGENT_EXECUTE_APPROVAL):
        # every shell command the model writes pauses for approval unless it is
        # on the read-only allowlist. `create_chart` is the path that needs no
        # approval, which is why the Python fallback is a fallback.
        interrupt_on=execute_interrupt_on(),
        # No `checkpointer=`: the server injects one per run and rejects
        # graph-owned savers.
        store=store,
        # No `subagents=`: deepagents adds a general-purpose one, which the
        # system prompt and skills tell this agent not to use. No `name=`
        # either — it would stamp `lc_agent_name`, and ModelOverrideMiddleware
        # reads that as "this is a subagent" and would apply the wrong override.
    )


analytics_agent = build_analytics_agent()
