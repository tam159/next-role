"""Define the career agent."""

from typing import Any

from backend.agents.career_agent import prompts as _prompts
from backend.agents.career_agent.middleware import (
    PREFERENCES_PATH,
    EnsurePreferencesFileMiddleware,
    ModelOverrideMiddleware,
    UtcDatetimeMiddleware,
)
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.scope import kv_namespace
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend, default_shell_env
from backend.agents.career_agent.tools import (
    CAREER_AGENT_DIR,
    make_extract_jd,
    make_list_files,
    make_parse_document,
    make_render_battlecard_pdf,
    make_render_resume_pdf,
    web_extract,
    web_search,
)
from backend.agents.career_agent.utils import load_subagents
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION, FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

# No prompt monkey patching remains: every customization rides a supported
# deepagents 0.7 parameter. There is deliberately no `## task` prompt section —
# SubAgentMiddleware's `system_prompt` defaults to None, and 0.7 carries
# subagent usage guidance plus the subagent list in the `task` TOOL description
# ({available_agents}); CAREER_AGENT.md adds the per-stage task-input templates.

_MODEL = "openai:gpt-5.6-terra"

_backend = CompositeBackend(
    default=VirtualPathShellBackend(
        root_dir=CAREER_AGENT_DIR,
        virtual_mode=True,
        timeout=60,
        env=default_shell_env(),
    ),
    routes={
        # Namespaces are scoped to the authenticated caller at call time
        # (kv_namespace reads the run's identity via get_config); single-user
        # mode keeps the original ("career_agent", <area>) tuples. The lambda's
        # Runtime arg is unused — identity comes from the run config.
        "/memory/": StoreBackend(
            namespace=lambda _rt: kv_namespace("memory"),
        ),
        "/processed/": StoreBackend(
            namespace=lambda _rt: kv_namespace("processed"),
        ),
        "/research/": StoreBackend(
            namespace=lambda _rt: kv_namespace("research"),
        ),
        "/interview_coach/": StoreBackend(
            namespace=lambda _rt: kv_namespace("interview_coach"),
        ),
        "/large_tool_results/": StoreBackend(
            namespace=lambda _rt: kv_namespace("large_tool_results"),
        ),
        "/workspace/": StoreBackend(
            namespace=lambda _rt: kv_namespace("workspace"),
        ),
        # Binary artifact areas live in S3-compatible object storage (SeaweedFS
        # locally, S3/GCS/Azure in the cloud). NOTE: these prefixes hold the
        # text sources of truth too (rendercv YAML, battlecard JSON), not just
        # PDFs. rendercv renders in a throwaway temp dir outside the repo tree
        # (see render_resume_pdf in tools.py) — no disk artifact area remains.
        "/upload/": ObjectStoreBackend("upload"),
        "/tailored_resume/": ObjectStoreBackend("tailored_resume"),
        "/interview_battlecard/": ObjectStoreBackend("interview_battlecard"),
    },
)

# Prompt-bearing middleware, constructed once and shared. deepagents 0.7
# merges `middleware=` entries by `.name`: an instance whose name matches a
# default in create_deep_agent's stack REPLACES that default in place — the
# supported way to customize the built-in middlewares' prompts (no monkey
# patching). A future upstream class rename would silently demote these to
# the custom slot; the assembled-prompt snapshot test is the tripwire.
_fs_middleware = FilesystemMiddleware(
    backend=_backend,
    system_prompt=_prompts.FILE_TOOLS,
    # 0.7 keeps tool prose in the tool descriptions; extend (not replace) the
    # stock execute description with the guardrail that keeps file writes on
    # the virtual routes. NOTE: if `permissions=` is ever passed to
    # create_deep_agent, mirror it here — this replacement instance is the one
    # that actually runs.
    custom_tool_descriptions={
        "execute": EXECUTE_TOOL_DESCRIPTION + "\n\n" + _prompts.EXECUTE_GUARDRAIL,
    },
)

# Loaded into <agent_memory> every turn. Also passed as `memory=` below, which
# makes create_deep_agent construct its default MemoryMiddleware at the
# cache-friendly tail of the stack — the instance here then replaces it in
# place by name. Keep both lists identical: this one is what actually loads.
_MEMORY_SOURCES = ["CAREER_AGENT.md", PREFERENCES_PATH]

_memory_middleware = MemoryMiddleware(
    backend=_backend,
    sources=_MEMORY_SOURCES,
    add_cache_control=True,  # match the default instance this replaces
    system_prompt=_prompts.MEMORY,
)

# Build each tool instance once so the closure over `_backend` is not
# duplicated, and we can hand the same instance to both the main agent and any
# subagent that opts into it via subagents.yaml.
_list_files = make_list_files(_backend)
_parse_document = make_parse_document(_backend)
_extract_jd = make_extract_jd(_backend)
_render_resume_pdf = make_render_resume_pdf(_backend)
_render_battlecard_pdf = make_render_battlecard_pdf(_backend)

# Generic filesystem utilities every subagent gets unconditionally — saves
# re-declaring them in subagents.yaml per entry. (The old overwrite_file tool
# is gone: deepagents 0.7's built-in write_file has write-or-replace
# semantics, so every agent already has overwrite via its filesystem tools.)
_SUBAGENT_DEFAULT_TOOLS = [_list_files]

# Opt-in pool: tools a subagent must explicitly request via `tools:` in YAML.
# Anything NOT listed here is unavailable to subagents — without this guard
# deepagents would silently inherit the main agent's full tool set
# (parse_document, extract_jd, …) into subagents that have no business with them.
_SUBAGENT_TOOLS = {
    "web_search": web_search,
    "web_extract": web_extract,
    "render_resume_pdf": _render_resume_pdf,
}

# Shared across the main agent and every declarative subagent: declarative
# subagents are built by deepagents via `create_agent(..., middleware=...)`
# and do NOT inherit the main agent's middleware list, so the override
# middleware has to be threaded into each one explicitly. The filesystem
# override rides along so every subagent gets the same execute guardrail and
# file-tools prompt (each subagent graph builds its own default stack;
# name-matching applies per graph).
_model_override_middleware = ModelOverrideMiddleware()
# `Any` state param: the middlewares carry heterogeneous state schemas
# (PlanningState, FilesystemState, …); deepagents accepts them at runtime.
_SUBAGENT_DEFAULT_MIDDLEWARE: list[AgentMiddleware[Any, Any, Any]] = [
    _model_override_middleware,
    _fs_middleware,
]


def build_career_agent(
    model: str | BaseChatModel = _MODEL,
    *,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Build the career-agent graph.

    Args:
        model: Main-agent model (subagent models come from subagents.yaml;
            per-run overrides ride ModelOverrideMiddleware). Tests pass a fake
            chat model to assemble the graph offline.
        store: Explicit LangGraph store for offline construction/tests. The
            server runtime injects its own when this is None.

    """
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        EnsurePreferencesFileMiddleware(_backend),
        _model_override_middleware,
        # deepagents 0.7 dropped TodoListMiddleware from the default stack,
        # so planning todos (the write_todos tool + `todos` state channel the
        # frontend Plan panel reads) are opt-in. Main agent only — subagents
        # are single-shot by design. Stock langchain prompt (career-specific
        # usage gates live in SYSTEM_PROMPT). Keep it before
        # UtcDatetimeMiddleware so its section lands ahead of the date line.
        TodoListMiddleware(),
        UtcDatetimeMiddleware(),
        # Name-matched overrides — these replace the corresponding defaults
        # in place (positions in this list don't matter):
        _fs_middleware,
        _memory_middleware,
    ]
    return create_deep_agent(
        system_prompt=_prompts.SYSTEM_PROMPT,
        model=model,
        memory=_MEMORY_SOURCES,
        skills=["skills/career-agent/"],
        tools=[
            _list_files,
            _parse_document,
            _extract_jd,
            _render_battlecard_pdf,
        ],
        subagents=load_subagents(
            CAREER_AGENT_DIR / "subagents.yaml",
            tools=_SUBAGENT_TOOLS,
            default_tools=_SUBAGENT_DEFAULT_TOOLS,
            default_middleware=_SUBAGENT_DEFAULT_MIDDLEWARE,
        ),
        backend=_backend,
        middleware=middleware,
        store=store,
    )


career_agent = build_career_agent()
