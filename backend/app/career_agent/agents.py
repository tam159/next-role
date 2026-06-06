"""Define the career agent."""

import deepagents.graph as _graph_mod
import deepagents.middleware.filesystem as _fs_mw
import deepagents.middleware.memory as _mem_mw
import deepagents.middleware.skills as _skills_mw
import deepagents.middleware.subagents as _sub_mw
import langchain.agents.middleware.todo as _todo_mw
from backend.app.career_agent import prompts as _prompts
from backend.app.career_agent.middleware import (
    EnsurePreferencesFileMiddleware,
    ModelOverrideMiddleware,
    UtcDatetimeMiddleware,
)
from backend.app.career_agent.shell_backend import VirtualPathShellBackend
from backend.app.career_agent.tools import (
    CAREER_AGENT_DIR,
    make_extract_jd,
    make_list_files,
    make_overwrite_file,
    make_parse_document,
    make_prepare_render_settings,
    make_render_battlecard_pdf,
    web_extract,
    web_search,
)
from backend.app.career_agent.utils import load_subagents
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend


def _apply_prompt_overrides() -> None:
    """Replace the prompts that deepagents/langchain inject into the system message.

    Most middlewares read their constant by bare name at call time, so patching
    the module attribute is enough. `TodoListMiddleware`, `SubAgentMiddleware`,
    and — on deepagents 0.6.3+ — `MemoryMiddleware` are different: they capture
    the constant as a keyword-only default arg, which Python freezes into
    `__init__.__kwdefaults__` at class-definition time. Reassigning the module
    attribute after that does nothing; we must patch `__kwdefaults__` directly.

    `BASE_AGENT_PROMPT` is patched here (rather than via
    `HarnessProfile.base_system_prompt`) because the harness-profile overlay
    also replaces declarative subagents' authored `system_prompt`, wiping out
    the prompts defined in `subagents.yaml`. Patching the module constant
    affects only the main agent's base, not subagent specs.

    Must run before `create_deep_agent()`. Process-global side effect — any
    other deep agent instantiated in the same Python process after this runs
    will also see these prompts.
    """
    # `setattr` (rather than direct assignment) sidesteps ty's literal-type
    # narrowing: upstream constants ≤4096 chars are typed as `Literal["..."]`,
    # so reassigning them to a divergent string fails type-check.
    setattr(_graph_mod, "BASE_AGENT_PROMPT", _prompts.BASE)  # noqa: B010
    setattr(_skills_mw, "SKILLS_SYSTEM_PROMPT", _prompts.SKILLS)  # noqa: B010
    setattr(_fs_mw, "_FILESYSTEM_SYSTEM_PROMPT_TEMPLATE", _prompts.FILESYSTEM)  # noqa: B010
    setattr(  # noqa: B010
        _fs_mw,
        "FILESYSTEM_SYSTEM_PROMPT",
        _prompts.FILESYSTEM.format(large_tool_results_prefix="/large_tool_results"),
    )
    setattr(_fs_mw, "EXECUTION_SYSTEM_PROMPT", _prompts.EXECUTION)  # noqa: B010
    setattr(_mem_mw, "MEMORY_SYSTEM_PROMPT", _prompts.MEMORY)  # noqa: B010
    setattr(_todo_mw, "WRITE_TODOS_SYSTEM_PROMPT", _prompts.TODO)  # noqa: B010
    setattr(_sub_mw, "TASK_SYSTEM_PROMPT", _prompts.TASK)  # noqa: B010

    _todo_mw.TodoListMiddleware.__init__.__kwdefaults__["system_prompt"] = _prompts.TODO  # type: ignore # noqa: PGH003
    _sub_mw.SubAgentMiddleware.__init__.__kwdefaults__["system_prompt"] = _prompts.TASK  # type: ignore # noqa: PGH003

    # deepagents 0.6.3+ freezes the memory prompt into MemoryMiddleware's
    # keyword-only `system_prompt` default, so the module-level setattr above no
    # longer reaches the constructed middleware (0.6.1 read the bare global, so it
    # did). Patch the kwdefault too; guarded so it's a no-op on 0.6.1, which has
    # no such parameter.
    _mem_kwdefaults = _mem_mw.MemoryMiddleware.__init__.__kwdefaults__
    if _mem_kwdefaults and "system_prompt" in _mem_kwdefaults:
        _mem_kwdefaults["system_prompt"] = _prompts.MEMORY


_apply_prompt_overrides()


_MODEL = "openai:gpt-5.4"

_backend = CompositeBackend(
    default=VirtualPathShellBackend(
        root_dir=CAREER_AGENT_DIR,
        virtual_mode=True,
        timeout=60,
    ),
    routes={
        "/memory/": StoreBackend(
            namespace=lambda _: ("career_agent", "memory"),
        ),
        "/processed/": StoreBackend(
            namespace=lambda _: ("career_agent", "processed"),
        ),
        "/research/": StoreBackend(
            namespace=lambda _: ("career_agent", "research"),
        ),
        "/interview_coach/": StoreBackend(
            namespace=lambda _: ("career_agent", "interview_coach"),
        ),
        "/large_tool_results/": StoreBackend(
            namespace=lambda _: ("career_agent", "large_tool_results"),
        ),
        "/workspace/": StoreBackend(
            namespace=lambda _: ("career_agent", "workspace"),
        ),
    },
)

# Build each tool instance once so the closure over `_backend` is not
# duplicated, and we can hand the same instance to both the main agent and any
# subagent that opts into it via subagents.yaml.
_list_files = make_list_files(_backend)
_parse_document = make_parse_document(_backend)
_extract_jd = make_extract_jd(_backend)
_overwrite_file = make_overwrite_file(_backend)
_prepare_render_settings = make_prepare_render_settings(_backend)
_render_battlecard_pdf = make_render_battlecard_pdf(_backend)

# Generic filesystem utilities every subagent gets unconditionally — saves
# re-declaring them in subagents.yaml per entry.
_SUBAGENT_DEFAULT_TOOLS = [_list_files, _overwrite_file]

# Opt-in pool: tools a subagent must explicitly request via `tools:` in YAML.
# Anything NOT listed here is unavailable to subagents — without this guard
# deepagents would silently inherit the main agent's full tool set
# (parse_document, extract_jd, …) into subagents that have no business with them.
_SUBAGENT_TOOLS = {
    "web_search": web_search,
    "web_extract": web_extract,
    "prepare_render_settings": _prepare_render_settings,
}

# Shared across the main agent and every declarative subagent: declarative
# subagents are built by deepagents via `create_agent(..., middleware=...)`
# and do NOT inherit the main agent's middleware list, so the override
# middleware has to be threaded into each one explicitly.
_model_override_middleware = ModelOverrideMiddleware()

career_agent = create_deep_agent(
    system_prompt=_prompts.SYSTEM_PROMPT,
    model=_MODEL,
    memory=["AGENTS.md", "/memory/preferences.md"],
    skills=["skills/career-agent/"],
    tools=[
        _list_files,
        _overwrite_file,
        _parse_document,
        _extract_jd,
        _render_battlecard_pdf,
    ],
    subagents=load_subagents(
        CAREER_AGENT_DIR / "subagents.yaml",
        tools=_SUBAGENT_TOOLS,
        default_tools=_SUBAGENT_DEFAULT_TOOLS,
        default_middleware=[_model_override_middleware],
    ),
    backend=_backend,
    middleware=[
        EnsurePreferencesFileMiddleware(_backend),
        _model_override_middleware,
        UtcDatetimeMiddleware(),
    ],
)
