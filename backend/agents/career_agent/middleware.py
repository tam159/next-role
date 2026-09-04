"""Custom middleware for the career agent."""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock, ChatBedrockConverse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config

logger = logging.getLogger(__name__)


class UtcDatetimeMiddleware(AgentMiddleware):
    """Append a `Current UTC date: ...` line to the system message.

    Without this the agent has no clock and can't interpret `modified_at`
    timestamps from `list_files(...)` (e.g. tell "uploaded yesterday" apart
    from "uploaded last month"). Injecting per call rather than at module
    import keeps the value accurate across long-lived deployments, while date
    precision avoids invalidating prompt caches on every turn.
    """

    @staticmethod
    def _inject(request: Any) -> Any:  # noqa: ANN401  # ModelRequest is generic
        existing = request.system_message.text if request.system_message else ""
        today = datetime.now(UTC).date().isoformat()
        new_content = f"{existing}\n\nCurrent UTC date: {today}".strip()
        return request.override(system_message=SystemMessage(content=new_content))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._inject(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._inject(request))


# Path of the always-loaded preferences file (a StoreBackend route; the same
# string is wired as a `memory=` source in agents.py).
PREFERENCES_PATH = "/memory/preferences.md"

# Scaffold written when the preferences file is absent. The section headings
# give the model an obvious place to append each preference, and let it pull the
# right ones per stage when delegating to subagents.
_PREFERENCES_SCAFFOLD = """# Saved preferences

Standing preferences for how to prepare this user's materials. Apply the relevant
ones on every run, and fold them into subagent task descriptions.

## Research

## Tailored resume

## Interview prep

## Battlecard

## General
"""


class EnsurePreferencesFileMiddleware(AgentMiddleware):
    """Guarantee the always-loaded preferences file exists before the model runs.

    The model reliably *appends* a preference to `/memory/preferences.md` when the
    file already exists, but won't reliably *create* it on a clean slate — it
    fumbles toward CAREER_AGENT.md or starts the intake workflow instead. So we
    seed the scaffold here in `before_agent`, gated on an explicit existence
    probe: one cheap store read per run, one write ever. The probe is what makes
    this idempotent — deepagents 0.7 `write()` overwrites existing files (0.6
    refused, which used to be the guard), so an unconditional write would wipe
    the user's saved preferences on every turn.

    Main-agent only — subagents have no memory and never touch this file.
    """

    def __init__(self, backend: Any) -> None:  # noqa: ANN401  # CompositeBackend
        """Capture the backend used to seed the preferences scaffold."""
        self._backend = backend

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Seed the scaffold if missing (sync invocation path)."""
        try:
            # `error is None` ⇒ the file exists; never overwrite saved preferences.
            if self._backend.read(PREFERENCES_PATH).error is not None:
                self._backend.write(PREFERENCES_PATH, _PREFERENCES_SCAFFOLD)
        except Exception:  # never break a run over preference seeding
            logger.debug("ensure preferences file (sync) skipped", exc_info=True)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Seed the scaffold if missing (async invocation path)."""
        try:
            # `error is None` ⇒ the file exists; never overwrite saved preferences.
            if (await self._backend.aread(PREFERENCES_PATH)).error is not None:
                await self._backend.awrite(PREFERENCES_PATH, _PREFERENCES_SCAFFOLD)
        except Exception:  # never break a run over preference seeding
            logger.debug("ensure preferences file (async) skipped", exc_info=True)


# Module-level cache: `init_chat_model` builds a client each call (allocates
# network plumbing, reads env vars). The same one or two strings are reused
# across every node call in a run, so cache by the input string.
_MODEL_CACHE: dict[str, BaseChatModel] = {}


def _resolve_model(name: str) -> BaseChatModel | None:
    """Build (and memoize) a chat model from a `provider:model` string.

    Returns `None` on any failure — bad provider, unknown model, missing
    credentials — so the caller can fall back to the bake-time default
    instead of crashing the whole run on a user typo.
    """
    if cached := _MODEL_CACHE.get(name):
        return cached
    try:
        model = init_chat_model(name)
    except Exception:  # init_chat_model raises a mix
        logger.warning("ModelOverrideMiddleware: cannot init '%s'; falling back", name)
        return None
    _MODEL_CACHE[name] = model
    return model


# Feature toggle (see PRD 16). Default `False`: subagents stream tokens live.
# The freeze that forced the original `True` default was an O(n^2) per-token
# chunk concat in the legacy `@langchain/langgraph-sdk` useStream path; the
# frontend now runs on `@langchain/react`'s v2 stream runtime (fragment
# accumulation + per-tick batched flushes), verified in-browser with
# `resume-tailor` + `interview-coach` streaming large tool-call args in
# parallel. Kept as a rollback lever: set `True` here — or per run via
# `configurable.disable_subagent_streaming` — to drop subagents back to
# per-step (whole-message) updates if a client-side freeze ever resurfaces.
DISABLE_SUBAGENT_STREAMING: bool = False


def _without_streaming(model: BaseChatModel) -> BaseChatModel:
    """Return a copy of `model` with token streaming disabled.

    Rollback path for subagent streaming (off by default since the
    `@langchain/react` migration — see `DISABLE_SUBAGENT_STREAMING`). When
    enabled, `disable_streaming` makes the model defer to `(a)invoke`, so
    LangGraph emits one complete message per step instead of token deltas —
    the historical mitigation for the legacy SDK's O(n^2) per-token concat
    under parallel large tool-call args.

    `model_copy` leaves the shared/cached instance untouched — the main agent
    may use the same `provider:model` string (shared with
    `resume-tailor`) and must keep streaming.
    """
    try:
        return model.model_copy(update={"disable_streaming": True})
    except Exception:  # never break a real run over a streaming tweak
        logger.warning("could not disable streaming on %r; leaving as-is", model)
        return model


# Claude buffers each tool-input parameter server-side until its value is
# complete unless the request opts into fine-grained tool streaming, so a large
# `write_file.content` reached the client as one late chunk — the UI showed
# `file_path`, then nothing until the whole document landed. Opted in, fragments
# stream as generated (like OpenAI does by default). Two knobs, one per transport:
#   - Direct Anthropic API: the per-tool definition field `eager_input_streaming`.
#   - Bedrock (Converse or legacy InvokeModel): no per-tool field — the tool
#     schema is AWS-normalized — but the legacy beta flag on the Anthropic-native
#     request body still turns it on for every tool that leaves the field unset.
# Gemini's generateContent API has no equivalent: a function call arrives whole.
_EAGER_INPUT_STREAMING = "eager_input_streaming"
_FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"


def _with_eager_tool_streaming(tools: list[Any]) -> list[Any] | None:
    """Return copies of `tools` opted into Anthropic fine-grained tool streaming.

    `langchain_anthropic` lifts `BaseTool.extras["eager_input_streaming"]` onto
    the wire tool definition (`convert_to_anthropic_tool`), so the flag rides
    the tool objects handed to `bind_tools`. `model_copy` keeps the agent's
    shared tool instances provider-neutral (the same objects are bound to
    OpenAI/Gemini/Bedrock requests, whose converters ignore the key). Dict tools
    (provider built-ins) and tools that already set the key — an explicit
    `False` keeps buffered streaming — pass through untouched.

    Returns `None` when no tool needed the flag so the caller skips the override.
    """
    changed = False
    out: list[Any] = []
    for tool in tools:
        if not isinstance(tool, BaseTool):
            out.append(tool)
            continue
        extras = tool.extras or {}
        if _EAGER_INPUT_STREAMING in extras:
            out.append(tool)
            continue
        out.append(tool.model_copy(update={"extras": {**extras, _EAGER_INPUT_STREAMING: True}}))
        changed = True
    return out if changed else None


def _serves_anthropic(model: ChatBedrockConverse | ChatBedrock) -> bool:
    """Whether a Bedrock model wrapper fronts a Claude model (mirrors `langchain_aws`).

    Inference-profile ids (`global.anthropic.claude-…`, `us.anthropic.…`) and
    foundation-model ARNs all carry the `anthropic.` provider segment; an
    application inference profile hides it, so `provider`/`base_model_id` are
    consulted too. Unknown → `False`: buffered streaming, never a bad request.
    """
    ids = f"{getattr(model, 'base_model_id', None) or ''} {model.model_id}".lower()
    return (model.provider or "") == "anthropic" or "anthropic" in ids


def _with_fine_grained_beta(betas: object) -> list[str] | None:
    """Append the fine-grained flag to a user-configured `anthropic_beta` list.

    Returns `None` when the flag is already present so callers skip the copy.
    """
    existing = [betas] if isinstance(betas, str) else list(betas) if isinstance(betas, list) else []
    if _FINE_GRAINED_TOOL_STREAMING_BETA in existing:
        return None
    return [*existing, _FINE_GRAINED_TOOL_STREAMING_BETA]


def _with_bedrock_fine_grained_streaming(
    model: ChatBedrockConverse | ChatBedrock,
) -> ChatBedrockConverse | ChatBedrock:
    """Return a copy of a Bedrock Claude model with fine-grained tool streaming on.

    `anthropic_beta` rides `additional_model_request_fields` on the Converse API
    and `model_kwargs` (merged into the InvokeModel body) on legacy `ChatBedrock`.
    Both are passed through verbatim by `langchain_aws`; other keys the user
    configured (e.g. `reasoningConfig`) are preserved. Returns `model` itself
    when the flag is already set.
    """
    if isinstance(model, ChatBedrockConverse):
        fields = dict(model.additional_model_request_fields or {})
        betas = _with_fine_grained_beta(fields.get("anthropic_beta"))
        if betas is None:
            return model
        fields["anthropic_beta"] = betas
        return model.model_copy(update={"additional_model_request_fields": fields})
    kwargs = dict(model.model_kwargs or {})
    betas = _with_fine_grained_beta(kwargs.get("anthropic_beta"))
    if betas is None:
        return model
    kwargs["anthropic_beta"] = betas
    return model.model_copy(update={"model_kwargs": kwargs})


def _fine_grained_tool_streaming_overrides(
    model: BaseChatModel,
    tools: list[Any],
) -> dict[str, Any]:
    """`ModelRequest.override` kwargs that opt a Claude call into fine-grained streaming.

    Keyed off the model the call will actually use (default or override, main
    or subagent). Empty for non-Claude providers and when nothing needs changing.
    """
    if isinstance(model, ChatAnthropic):
        tools_ = _with_eager_tool_streaming(tools)
        return {"tools": tools_} if tools_ is not None else {}
    if isinstance(model, ChatBedrockConverse | ChatBedrock) and _serves_anthropic(model):
        model_ = _with_bedrock_fine_grained_streaming(model)
        return {"model": model_} if model_ is not model else {}
    return {}


class ModelOverrideMiddleware(AgentMiddleware):
    """Shape the request model per main-agent-vs-subagent context.

    Three responsibilities; the first two are keyed off whether the call
    belongs to a subagent, the third off the model the call ends up using:

    1. **Model override.** Reads two `RunnableConfig.configurable` keys:
         - `main_agent_model` — applies to the top-level career_agent call.
         - `subagent_model`   — applies to every declarative subagent call.
       When the matching key is missing/empty or `init_chat_model` fails, the
       bake-time default (`_MODEL` in `agents.py`; `model:` in `subagents.yaml`)
       still wins.

    2. **Disable streaming for subagents (rollback lever).** When
       `DISABLE_SUBAGENT_STREAMING` is on (module default `False` since the
       `@langchain/react` migration; overridable per run via
       `configurable.disable_subagent_streaming`), every subagent call (override
       or default model) gets `disable_streaming=True` so parallel subagents
       emitting large tool-call args don't flood the client (see
       `_without_streaming`). The main agent always keeps streaming.

    3. **Fine-grained tool streaming for Claude.** Keyed off the *effective*
       model (default or override, main or subagent) so large tool-call args
       (`write_file.content`, `edit_file.new_string`) stream token-by-token in
       the UI instead of arriving in one late chunk — see
       `_fine_grained_tool_streaming_overrides`:
         - `ChatAnthropic` (direct API): the request's tools are swapped for
           copies carrying `extras["eager_input_streaming"]=True`.
         - `ChatBedrockConverse` / legacy `ChatBedrock` fronting a Claude model:
           the model is swapped for a copy whose Anthropic-native request body
           carries the `fine-grained-tool-streaming-2025-05-14` beta flag.
       Lives here rather than in its own middleware because it must see the
       model *after* the override in (1) — the same ordering argument that put
       (2) here (PRD 16).

    Subagent vs. main is differentiated by `metadata.lc_agent_name`, which
    deepagents stamps onto each subagent's runnable (see
    `deepagents/middleware/subagents.py` → `with_config({"metadata":
    {"lc_agent_name": ...}})`). Absent → main agent.
    """

    @staticmethod
    def _read_config() -> tuple[bool, str | None, bool]:
        """Return `(is_subagent, model_name_override, disable_subagent_streaming)`."""
        try:
            config = get_config()
        except RuntimeError:
            # Called outside a runnable context (e.g. a unit test that invokes
            # the middleware directly). Treat as main agent, no override, no-op.
            return False, None, False
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        disable_streaming = configurable.get("disable_subagent_streaming")
        if disable_streaming is None:  # not set per run → fall back to the module default
            disable_streaming = DISABLE_SUBAGENT_STREAMING
        if metadata.get("lc_agent_name"):
            return True, configurable.get("subagent_model") or None, bool(disable_streaming)
        return False, configurable.get("main_agent_model") or None, bool(disable_streaming)

    @classmethod
    def _maybe_override(cls, request: Any) -> Any:  # noqa: ANN401
        is_subagent, name, disable_streaming = cls._read_config()
        model = _resolve_model(name) if name else None  # None → keep request's default
        overrides: dict[str, Any] = {}
        if is_subagent and disable_streaming:
            # Disable streaming on whichever model the subagent ends up using.
            base = model if model is not None else request.model
            overrides["model"] = _without_streaming(base)
        elif model is not None:
            overrides["model"] = model
        # Provider tweak keyed off the model the call will actually use.
        effective_model = overrides.get("model", request.model)
        overrides.update(_fine_grained_tool_streaming_overrides(effective_model, request.tools))
        return request.override(**overrides) if overrides else request

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._maybe_override(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._maybe_override(request))
