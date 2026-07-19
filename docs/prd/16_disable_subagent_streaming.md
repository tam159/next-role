---
type: PRD
title: "Disable token streaming for subagents"
description: "Backend middleware flips disable_streaming on subagent models to kill an O(n²) SDK concat that froze the chat during parallel subagent runs."
tags: [backend, streaming, subagents, superseded]
timestamp: '2026-06-11T10:59:37+07:00'
status: "superseded by 18_langchain_react_migration"
scope: "Backend (career-agent model middleware)"
version: v1
---

> **Superseded by [18_langchain_react_migration](18_langchain_react_migration.md)** —
> default flipped to `False` (subagents stream again) after the frontend migrated to
> `@langchain/react`'s v2 stream runtime, whose fragment-array accumulation + per-tick batched
> flushes remove the O(n²) concat this PRD worked around. The toggle machinery is kept
> verbatim as the rollback lever (`DISABLE_SUBAGENT_STREAMING = True` or per-run
> `configurable.disable_subagent_streaming`).

**Extends:** [05_chat_streaming_throttle](05_chat_streaming_throttle.md)

# Why

The chat UI hangs when **two or more subagents run in parallel and each streams a large
tool-call argument** — the canonical case is `resume-tailor` + `interview-coach` both writing
a full file body into `overwrite_file`'s `new_content`. [PRD 05](05_chat_streaming_throttle.md) added an 80 ms render throttle
that fixed the *rendering* rate, and explicitly deferred a "content-skip placeholder" as the
next lever. But the hang came back as payloads grew, and the deferred lever turned out to be
the wrong one.

Tracing it end-to-end, the cost is **inside the LangGraph SDK, upstream of the throttle**:
`useStream` (LangGraph Platform path) feeds every streamed token into
`MessageTupleManager.add()`, which does `prev.concat(chunk)` per token
(`@langchain/langgraph-sdk/dist/ui/messages.js:85`). For an `AIMessageChunk` carrying
`tool_call_chunks`, `concat` re-copies the entire growing args string every token → **O(n²)**
per file, ×N parallel subagents. No frontend render change can fix work that happens before
rendering. So the fix moved to the backend: stop subagents from streaming tokens at all.

# What the user sees

During the parallel-subagent phase the chat surface stays responsive — no freeze, clicks land.
The trade-off, accepted deliberately: **subagent output no longer streams token-by-token.** A
subagent's text and tool calls now appear *per LLM step* (each step's message arrives whole)
instead of letter-by-letter. The **main agent is unchanged** — its narration and its own tool
calls still stream live. In practice subagents mostly emit tool calls with little prose, so the
per-step cadence reads as "stepped," not broken.

# How — the key architectural choices

- **Disable streaming on the subagent *model*, not in the frontend.** `disable_streaming`
  makes the model defer `astream` → `ainvoke`, so LangGraph emits one complete `messages` event
  per step (message events fire even under `invoke`) — the per-token `concat` never runs. This
  is the documented LangChain pattern for "control which agents stream their output," and it is
  decoupled from SDK internals, so it survives SDK upgrades. The rejected frontend paths (below)
  were both coupled to the just-rewritten SDK.
- **Fold it into the existing `ModelOverrideMiddleware`, no new middleware.** That class is
  already the single shared instance attached to *both* the main agent and every subagent
  (`agents.py:147,150`), and it already branches main-vs-subagent on `metadata.lc_agent_name`.
  The subagent branch is the exact, proven insertion point — adding a separate middleware would
  reintroduce an ordering dependency (it must see the model *after* any `subagent_model`
  override is applied).
- **Set the flag via `model_copy`, not `.bind()` and not mutation.** `_streaming_disabled`
  reads `self.disable_streaming` off the model attribute, so `.bind(disable_streaming=…)`
  (which only adds call-kwargs) would silently do nothing. `model_copy` returns a fresh instance
  so the shared/cached model keeps streaming for the main agent — critical because the main
  agent and `resume-tailor` share `openai:gpt-5.6-terra` and the same `_MODEL_CACHE` entry.
- **Ship it behind a default-on toggle, not hardcoded.** The behavior is gated by
  `DISABLE_SUBAGENT_STREAMING` (module default `True`) with an optional per-run
  `configurable.disable_subagent_streaming` override — mirroring how `_MODEL` (bake default)
  pairs with `configurable.subagent_model`. This is a deliberately reversible workaround: once
  the SDK/FE is permanently fixed, flip the default to `False` (or set the configurable key per
  run / via assistant config) to restore live subagent streaming without touching the override
  logic. No new settings module or env var was introduced — the backend has none, and this
  middleware already reads `configurable`, so the toggle lives where its siblings do.

# Files of interest

| Concern | Path |
|---|---|
| The reversible toggle (default `True`) | `backend/app/career_agent/middleware.py` (`DISABLE_SUBAGENT_STREAMING`) |
| Disable-streaming for subagents; main-vs-subagent branch | `backend/app/career_agent/middleware.py` (`_maybe_override`) |
| The `model_copy(disable_streaming=True)` helper | `backend/app/career_agent/middleware.py` (`_without_streaming`) |
| Reads `is_subagent` + override + streaming flag from config | `backend/app/career_agent/middleware.py` (`_read_config`) |
| Middleware wired onto main agent + every subagent | `backend/app/career_agent/agents.py:129,147,150` |
| Unit tests (override + streaming, copy-not-mutation) | `backend/tests/career_agent/test_middleware_model_override.py` |
| Root-cause O(n²) concat (SDK, for reference) | `node_modules/@langchain/langgraph-sdk/dist/ui/messages.js:85` |

# Decisions worth remembering

- **The hang is an SDK bug, upstream of the [PRD 05](05_chat_streaming_throttle.md) throttle.** This is the non-obvious fact the
  code can't show: the throttle (and any render-side placeholder) gate work that happens *after*
  the SDK's per-token `concat`. We confirmed `useStream` (LGP path, `react/stream.lgp.js`) routes
  through `MessageTupleManager`, so the O(n²) is on the hot path. Don't re-attempt a frontend
  render fix for this class of hang.
- **`disable_streaming=True` over `"tool_calling"`.** Operationally identical here — deepagents'
  react loop always binds tools, so every subagent call would bypass streaming under either — so
  we picked the simpler, unambiguous value. The flag was applied to subagents only; the main
  agent's branch never touches it.
- **We considered preserving subagent text and rejected it once the true cost was clear.** The
  decision moved BE → "preserve text" (FE) → BE: an early lean toward keeping subagent text
  streaming was abandoned after finding the only ways to do it were (a) `pnpm patch` the SDK's
  `concat` to O(n), or (b) a custom-fetch SSE rewriter dropping subagent tool-arg deltas — both
  fragile against the SDK's recent (1.9.x) streaming rewrite, with no clean custom-`fetch` hook.
  The per-step cadence was accepted as the cheaper, durable trade.
- **Graceful fallback in `_without_streaming`.** Mirrors `_resolve_model`'s ethos: if `model_copy`
  ever raises (e.g. an unexpected configurable-wrapper model), log and return the model unchanged
  rather than crash a live run over a streaming tweak.

# Deferred (intentional non-goals for v1)

- **Preserving live subagent token streaming.** The `DISABLE_SUBAGENT_STREAMING` toggle
  re-enables it instantly, but the hang returns until the underlying O(n²) is fixed — either the
  SDK `concat` patch (accumulate fragments, join lazily → O(n)) or an upstream fix. Flipping the
  toggle back on is the *point* of the toggle: do it once the FE/SDK fix lands. Also revisit if a
  single **main-agent** large tool-arg write starts hanging — the main agent still streams, so it
  still pays the SDK O(n²) for one stream (mitigated today by the [PRD 05](05_chat_streaming_throttle.md) throttle).
- **A frontend Settings switch for the toggle.** `configurable.disable_subagent_streaming` is
  already honored, so wiring a UI control is just adding it to `buildSubmitConfig` in
  `useChat.ts` next to the model selectors ([PRD 15](15_configurable_llm_models.md)). Not built — it's an operator/dev knob today,
  not an end-user one.
- **Upstream fix.** The O(n²) `concat` is a LangGraph SDK bug worth reporting to
  `langchain-ai/langgraph`; a merged fix would let us drop this middleware branch entirely.

# How to verify end-to-end

1. `cd backend && uv run pytest tests/career_agent/test_middleware_model_override.py` — green
   (covers subagent-default, subagent-override, main-agent-untouched, copy-not-mutation).
2. `pre-commit run --files backend/app/career_agent/middleware.py backend/tests/career_agent/test_middleware_model_override.py` — ruff + `ty` pass.
3. `docker compose up -d`, grab the frontend port from `docker ps`. Backend hot-reloads — confirm
   the `career_agent` graph re-imports cleanly in `docker logs next-role-backend-1`.
4. Run a flow to the parallel `resume-tailor` + `interview-coach` phase (both write large files).
   The chat surface stays responsive; subagent text/tool calls appear **per step** (whole), and
   the **main agent still streams token-by-token** (its narration + the battlecard write).
