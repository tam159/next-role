"""Define the career agent."""

import deepagents.graph as _graph_mod
import deepagents.middleware.filesystem as _fs_mw
import deepagents.middleware.memory as _mem_mw
import deepagents.middleware.skills as _skills_mw
import deepagents.middleware.subagents as _sub_mw
import langchain.agents.middleware.todo as _todo_mw
from backend.app.career_agent import prompts as _prompts
from backend.app.career_agent.middleware import UtcDatetimeMiddleware
from backend.app.career_agent.tools import (
    CAREER_AGENT_DIR,
    make_extract_jd,
    make_list_files,
    make_overwrite_file,
    make_parse_document,
)
from backend.app.career_agent.utils import load_subagents
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend, StoreBackend


def _apply_prompt_overrides() -> None:
    """Replace the prompts that deepagents/langchain inject into the system message.

    Most middlewares read their constant by bare name at call time, so patching
    the module attribute is enough. `TodoListMiddleware` and `SubAgentMiddleware`
    are different — they capture the constant as a keyword-only default arg,
    which Python freezes into `__init__.__kwdefaults__` at class-definition
    time. Reassigning the module attribute after that does nothing; we must
    patch `__kwdefaults__` directly.

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


_apply_prompt_overrides()


_MODEL = "bedrock_converse:global.anthropic.claude-sonnet-4-6"


_backend = CompositeBackend(
    default=LocalShellBackend(
        root_dir=CAREER_AGENT_DIR,
        virtual_mode=True,
        timeout=60,
    ),
    routes={
        "/memory/": StoreBackend(
            namespace=lambda _: ("career_agent", "memory"),
        ),
        "/processed": StoreBackend(
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

career_agent = create_deep_agent(
    system_prompt=_prompts.SYSTEM_PROMPT,
    model=_MODEL,
    memory=["AGENTS.md"],
    skills=["skills/career-agent/"],
    tools=[
        make_list_files(_backend),
        make_parse_document(_backend),
        make_extract_jd(_backend),
        make_overwrite_file(_backend),
    ],
    subagents=load_subagents(CAREER_AGENT_DIR / "subagents.yaml"),
    backend=_backend,
    middleware=[UtcDatetimeMiddleware()],
)
