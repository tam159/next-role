# PRD: @langchain/react migration + subagent streaming re-enable (v1)

**Status:** shipped (PR #12) · **Scope:** Frontend chat surface (stream runtime swap) + backend
career-agent middleware (toggle flip) · **Supersedes:** [16_disable_subagent_streaming](16_disable_subagent_streaming.md),
partially [05_chat_streaming_throttle](05_chat_streaming_throttle.md)

## Why

PRD 16 disabled subagent token streaming because the legacy `@langchain/langgraph-sdk/react`
`useStream` re-ran an O(n²) per-token `concat` (`MessageTupleManager.add` →
`AIMessageChunk.concat`), freezing the browser whenever `resume-tailor` + `interview-coach`
streamed large tool-call args in parallel. That PRD's stated endgame was "flip the toggle back
once the FE/SDK is fixed for good." LangChain shipped that fix as a new package:
`@langchain/react`, a v2-native stream runtime that accumulates content-block fragments in
arrays (rope-concat per block) and batches store flushes per macrotask — the O(n²) is
structurally gone, verified by reading the installed dist before migrating. Two goals, in order:
migrate the frontend onto the new runtime, then re-enable subagent streaming and prove in a real
browser that the freeze is dead.

## What the user sees

Subagent output streams live again: nested tool boxes inside a subagent card appear while the
LLM is still emitting their args, and the args grow incrementally instead of landing whole per
step. The chat surface stays responsive through the parallel phase — measured zero long tasks
(worst 79 ms all-session), 8 ms worst input delay. Everything else is behavior parity: optimistic
message echo, subagent cards with Running/Complete badges, files sidebar, thread switching,
hard-reload hydration. One server-side caveat: main-agent *prose* now arrives per content block
rather than per token (see Decisions) — tool args, the payload that caused the freeze, stream
fine.

## How — the key architectural choices

- **Migrate to the v2 runtime instead of patching the legacy SDK.** PRD 16 had already rejected
  `pnpm patch` of the O(n²) `concat` as fragile. `@langchain/react` is the upstream fix: a
  different protocol (`POST /threads/{id}/stream/events`, channels `values`/`messages`/`tools`/
  `lifecycle`), supported by our `langchain/langgraph-api:3.13` image (0.9.0 — confirmed live via
  `openapi.json` before committing to the plan). Messages become `@langchain/core` `BaseMessage`
  instances; interrupts resume via `respond()`; hydration and reattach-to-in-flight-runs are
  automatic, so `reconnectOnMount`/`fetchStateHistory`/`onFinish`/`filterSubagentMessages` all
  disappear rather than being ported.
- **Subagent UI = discovery map + scoped selector hooks, with live args derived from
  `tool_call_chunks`.** `stream.subagents` is keyed by the spawning `task` tool-call id, which
  replaces `getSubagentsByMessage`. Each `SubagentCard` mounts `useToolCalls(stream, snapshot)` +
  `useMessages(stream, snapshot)` (mount = ref-counted subscribe). The dual subscription is
  load-bearing: the `tools` channel only emits `tool-started/finished/error` — **no arg deltas**
  — so a call whose args are still streaming exists only as `tool_call_chunks` on the subagent's
  messages channel. Chunk-derived entries (partial-JSON args via `parsePartialJson`) render until
  the tools channel takes over at `tool-started`.
- **Flip the backend default; keep the toggle as a rollback lever.** `DISABLE_SUBAGENT_STREAMING
  = False` in `middleware.py` — no logic changes, comments rewritten as rollback docs. Rollback is
  one hot-reloaded line (effective on the next subagent model call) or per-run
  `configurable.disable_subagent_streaming: true`; the FE renders both cadences transparently.
- **Delete dead surface instead of migrating it** (user-confirmed). `runSingleStep`,
  `continueStream`, `markCurrentThreadAsResolved`, `getMessagesMetadata` had zero UI consumers
  and depended on removed submit options (`interruptBefore/After`, `command`); the GenUI
  `LoadExternalComponent` path was unreachable (backend never emits `values.ui`). Rebuilding any
  of these later means raw `client.runs.*` / `useMessageMetadata`, not the old options.

## Files of interest

| Concern | Path |
|---|---|
| Hub rewrite: v2 `useStream`, `respond()`, throttle deletion, assistantId-hydrate guard | `frontend/src/app/hooks/useChat.ts` |
| Per-subagent card: scoped hooks + chunk-derived pending tool calls | `frontend/src/app/components/SubagentCard.tsx` |
| Task-call → snapshot association, pending-discovery fallback indicator | `frontend/src/app/components/ChatMessage.tsx` |
| `BaseMessage` extraction (`tool_calls` + `tool_call_chunks`), GenUI/hot-scroll removal | `frontend/src/app/components/ChatInterface.tsx` |
| `.text` accessor, `parsePartialArgs`/`toResultString` helpers | `frontend/src/app/utils/utils.ts` |
| Sources from scoped tool calls (`SubagentSourcesProbe` consumer) | `frontend/src/app/utils/sources.ts` (`extractSourcesFromToolCalls`) |
| Per-subagent sources probes replacing legacy `sub.messages` merge | `frontend/src/app/components/Workspace.tsx` |
| `stream_mode` body-rewrite workaround removal (0.8.1-era, dead on the new protocol) | `frontend/src/providers/ClientProvider.tsx` |
| Toggle flip + rollback-lever comments | `backend/app/career_agent/middleware.py` (`DISABLE_SUBAGENT_STREAMING`) |
| Default/rollback test matrix | `backend/tests/career_agent/test_middleware_model_override.py` |

## Decisions worth remembering

- **Pin the direct `@langchain/langgraph-sdk` to the exact version `@langchain/react` bundles**
  (1.9.20, plus `@langchain/core` `^1.1.48` per peers). Installing `@langchain/react` alone left
  two SDK copies in node_modules; a `Client` constructed from one copy crossing into the other's
  `StreamController` breaks instance identity. Keep them locked together on every future bump
  (noted in `frontend/CLAUDE.md`).
- **Hold `threadId` back until the assistant resolves.** Found in browser testing: on reload the
  controller hydrated the URL's thread with `assistantId: ""` (assistant still fetching) and threw
  `ThreadStream requires an assistantId option` — the legacy hook tolerated the empty string. Fix
  in `useChat.ts`: `threadId: activeAssistant ? (threadId ?? null) : null`; the assistantId change
  recreates the controller, which hydrates on `activate()`.
- **langgraph-api 0.9.0 emits no `text` deltas on the v2 bridge.** Wire replay of
  `/stream/events` showed all `content-block-delta`s carry tool-call `args` fields (550–1189 per
  run); narration text arrives whole per block. Per-block prose cadence is server behavior — don't
  chase it in the FE; re-check on langgraph-api upgrades and consider an upstream report.
- **PRD 05 is only *partially* superseded.** The 80 ms throttle existed to slow the legacy SDK's
  per-token churn; the v2 runtime batches flushes itself, so the throttle died. But the same
  commit's hardening stays: `ToolCall` identity caches (a store flush still fires several times
  per second mid-stream — without the caches every completed message re-renders per flush),
  pending-state `JSON.stringify` guards, and `pointerdown` click hardening (Safari swallows
  `click` under load regardless of SDK).
- **Two-gate verification, baseline first.** Gate A ran the full SAP-JD pipeline on the migrated
  FE with streaming still disabled — isolating migration regressions from streaming behavior and
  producing a metrics baseline (2 long tasks, worst 88 ms). Only then was the toggle flipped for
  Gate B's freeze test (zero long tasks during the parallel phase; streaming proven at wire +
  UI levels). Debugging a freeze and a migration bug simultaneously would have been far slower.
- **`defaultHeaders` on the hook was dead code.** Both legacy and v2 hooks ignore it whenever a
  `client` is passed (headers live on the `Client`); the `x-auth-scheme` header was never sent.
  Dropped rather than ported.

## Deferred (intentional non-goals for v1)

- **Per-token main-agent prose.** Blocked on the server bridge emitting text deltas (see
  Decisions); nothing to do in the FE today.
- **Step-mode / GenUI rebuild.** Deleted as dead code; if either returns, build on
  `client.runs.*` (interrupt_before) / `useMessageMetadata` / the v2 `Client` re-exports — the
  removed implementations targeted APIs that no longer exist.
- **Frontend Settings switch for the rollback toggle.** Carried over from PRD 16:
  `configurable.disable_subagent_streaming` is honored per-run; wiring it into `buildSubmitConfig`
  is trivial if it ever needs to be user-facing. Operator knob today.

## How to verify end-to-end

1. `cd backend && uv run pytest tests/career_agent/test_middleware_model_override.py` — 13 green
   (default pass-through + module/per-run rollback paths).
2. `docker compose up -d`; both containers hot-reload. Open the frontend port from `docker ps`.
3. Run a flow to the parallel `resume-tailor` + `interview-coach` phase (CV upload + JD URL +
   timeline). During the phase: both cards show **Running** with nested tool boxes whose args grow
   live; clicks land; no freeze. Optionally inject a `PerformanceObserver({type:'longtask'})`
   before submitting — expect no entry > 1 s during the phase.
4. Wire check (replace `<tid>`): `curl -N -X POST http://localhost:<port>/threads/<tid>/stream/events
   -H 'Content-Type: application/json' -d '{"channels":["messages"],"namespaces":[],"since":0}'`
   — subagent namespaces carry `content-block-delta` events with incremental `args` fields.
5. Hard-reload mid-thread: messages, tool boxes, and subagent cards rehydrate; no
   `ThreadStream requires an assistantId` overlay error.
6. Rollback drill: set `DISABLE_SUBAGENT_STREAMING = True` (hot-reloads), rerun a subagent —
   nested output arrives per step, whole; flip back.
