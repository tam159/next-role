# PRD: Chat Streaming Throttle (v1)

**Status:** partially superseded by [18_langchain_react_migration](18_langchain_react_migration.md)
— the headline mechanism (80 ms hot-window throttle, `isHotStreaming`, hot-exit scroll re-engage,
`sdkSubagents` memo + nested tool-call cache, `uiByMessageId`) was removed with the
`@langchain/react` migration, whose v2 stream runtime batches store flushes per macrotask and
makes a render-side throttle unnecessary. The orthogonal hardening from the same commit **remains
shipped and load-bearing**: `ToolCall`/`toolCalls[]` identity caches in `ChatInterface`, the
`pending`-state `JSON.stringify` guards + 256-char error-inspection cap in `ToolCallBox`, the
`onPointerDown`+`onClick` dedupe (Safari click-swallowing is OS behavior, SDK-independent), and
the sweep animation. · **Scope:** Frontend chat surface (LangGraph SDK consumer) — throttle +
identity-stable caching + click responsiveness under streaming load

## Why

The chat panel renders LangGraph-SDK stream events into React state on every token. When an LLM streams long content into a *tool-call argument* (rather than into message text), tokens arrive at 100–200 Hz. With **two or more subagents emitting in parallel**, the main thread can't drain between tokens — clicks stop landing on tool boxes and their args, and content from later steps doesn't appear until the whole burst finishes. Observed today in the career-agent flow (parallel `resume-tailor` + `interview-coach`), but the failure mode is structural and will recur for any future agent whose subagents stream long tool args.

The pressure has three compounding sources, and the fix addresses all three:

1. **Re-render rate.** Every token produces a new `stream.messages` reference and re-runs the messages-processing pipeline.
2. **Per-render cost.** Even when little has changed, `JSON.stringify` over a streaming tool arg in `previewValue`/`parseToolError`, plus rebuilding every `ToolCall` object fresh, defeats `React.memo` on `ChatMessage`/`ToolCallBox` and burns the budget.
3. **Input responsiveness.** On Safari, the OS swallows `click` during heavy main-thread work, so tool boxes stop toggling mid-stream even when the surface is otherwise painting.

## What the user sees

During any high-rate streaming window, the chat surface continues to paint smoothly (~12 fps for tool-arg content), tool boxes and subagent rows stay clickable, and step-N+1 output appears as soon as step N finishes — no manual scroll required. Outside that window every update is delivered at native cadence (per-token text streaming, instant tool-completion flips, etc.).

The trade-off is intentional and acknowledged: while a subagent is mid-tool-call, its streaming args text fills in 80 ms chunks instead of letter-by-letter. The user agreed this is a non-issue — they care about smooth observation, not character-level animation of tool inputs.

A running subagent now also gets a subtle sweep animation on its indicator row (matches the existing `tool-running-sweep` on the active `ToolCallBox`), and the animation is suppressed under `prefers-reduced-motion`.

## How — the key architectural choice

**Conditionally throttle `stream.messages` at 80 ms only while a "hot streaming" window is active.** A window is active iff `stream.isLoading` and at least one running subagent has a pending tool call. While active, downstream React work (the messages-processing useMemo in `ChatInterface`, the per-message subagent rebuild in `ChatMessage`, and every memoized tool-call array) samples a snapshot updated by `setInterval`; outside, `stream.messages` is passed through directly.

Why this shape, not the obvious alternatives:

- **Throttle, not skip.** Earlier options considered: (a) skip rendering the streaming `content` arg entirely and show a "writing… (N chars)" placeholder; (b) use React 19 `useDeferredValue`. The user picked throttling at ~12 fps because it preserves the "live" feel without character-level cost, and the fix needed to work even when nothing is expanded.
- **Conditional, not always-on.** Throttling outside the hot window would delay tool-completion flips and other genuinely-cheap updates. Gating on "running subagent has a pending tool call" matches the only known main-thread saturation mode.
- **Tool-name agnostic trigger.** The trigger does *not* check for `write_file` specifically — any pending tool call inside a running subagent counts. This is the cheapest generalization that survives the current backend evolving (different tool names, different agents).
- **Re-engage sticky scroll on window exit.** Independent fix in the same surface: when `isHotStreaming` flips false, call `useStickToBottom`'s `scrollToBottom()` once. During the hot window the user can lose the bottom-lock (a stray scroll-wheel tick, or the library reading a batched content jump as an escape), which otherwise leaves the next step's output below the fold.

### Supporting work in the same commit

The throttle alone reduces the *rate* of work. Three more changes reduce the *cost per render* and unblock input — all required for the smooth feel to actually land:

- **Identity-stable `ToolCall` and `toolCalls[]` references.** The messages-processing useMemo in `ChatInterface` now keeps two refs (`toolCallCacheRef`, `toolCallArrayCacheRef`): finalized `ToolCall` objects are cached by id, and the per-message `toolCalls` array reuses its previous reference when no entry's identity changed. Pending tool calls intentionally always emit a fresh ref so the streaming box re-renders and shows live tokens; everything earlier in the conversation goes through `React.memo` unchanged. `ChatMessage` mirrors this with `nestedToolCallCacheRef` for finalized nested subagent tool calls. Without this, the throttle still fires but every `ChatMessage` and `ToolCallBox` re-renders at sample time anyway.
- **`uiByMessageId` map instead of `.filter()` per render.** `ChatInterface` pre-buckets `ui` items by `metadata.message_id` once per `ui` change, so each `ChatMessage` gets a stable per-message array reference instead of a fresh `.filter()` result on every parent render.
- **Skip `JSON.stringify` work while `status === "pending"`.** In `ToolCallBox`, `previewValue` and `parseToolError` are gated on completion — both feed only the collapsed header, so there's no reason to stringify the growing args object on every token. `parseToolError` additionally caps its inspection window at 256 chars (error markers always appear at the start), so a long completed result doesn't pay full stringify cost either. `previewValue` short-circuits on strings before calling `JSON.stringify`.
- **`onPointerDown` + `onClick` with timestamp dedupe.** Both `ToolCallBox` and `SubAgentIndicator` toggle on `pointerdown`, which fires before the OS click-eating window during heavy main-thread work (the Safari mid-stream failure mode). `onClick` is kept as the keyboard fallback (Enter/Space only emit click) and is deduped against a recent pointer toggle with a 300 ms threshold, so a single tap doesn't toggle twice. Same shape in both components — copy the pattern for any future expandable surface that needs to stay responsive under streaming load.

## Files of interest

| Concern | Path |
|---|---|
| Hot-window detection + throttled messages snapshot + `isHotStreaming` export | `frontend/src/app/hooks/useChat.ts` |
| Sticky-scroll re-engage on window exit, `ToolCall` + `toolCalls[]` identity caching, `uiByMessageId` bucketing | `frontend/src/app/components/ChatInterface.tsx` |
| Subagent-map memo gated on `toolCalls` (throttled transitively) instead of unstable `stream.subagents` getter; nested tool-call identity caching | `frontend/src/app/components/ChatMessage.tsx` |
| `pending`-state guards on `previewValue`/`parseToolError`, 256-char cap on error inspection, `onPointerDown` + `onClick` dedupe | `frontend/src/app/components/ToolCallBox.tsx` |
| `onPointerDown` + `onClick` dedupe, active sweep animation when subagent is running | `frontend/src/app/components/SubAgentIndicator.tsx` |
| `will-change`/`translateZ` on `.tool-running-sweep`, `prefers-reduced-motion` opt-out | `frontend/src/app/globals.css` |

## Decisions worth remembering

- **`stream.messages` and `stream.subagents` are unstable getters.** The LangGraph SDK exposes both as accessors that return a fresh reference on every read. Putting either in a `useEffect` dep array combined with a `setState` inside the effect creates an infinite re-render loop ("Maximum update depth exceeded") — and putting them in a `useMemo` dep makes the memo recompute on every render (which is what caused the original lag). The hook now depends only on `isHotStreaming` (a boolean that flips a handful of times per run) and reads the latest messages via a ref at sample time. Any future code in this layer must respect the same constraint.
- **`messagesSnapshot = isHotStreaming ? throttledMessages : stream.messages`.** The ternary is deliberate. A single `useState` that we keep in sync always (even outside the hot window) reintroduces the infinite-loop trap because the unstable upstream ref differs each render and `setState` would fire on every render. Pass-through outside the hot window costs nothing and stays correct.
- **First implementation attempt failed: tried to propagate a `streamTick` counter and bump it on every upstream change.** The bump effect depended on `stream.subagents`, which (per the previous bullet) is a fresh reference each render — infinite loop. Replaced with the ternary + ref design above. Captured here so the next person doesn't reach for the same pattern.
- **80 ms is the single tunable knob, hardcoded in `useChat.ts`.** ~12 fps is below most users' threshold for "feels live" while well within the main-thread budget. Lower (60 ms) for snappier streaming on fast machines; higher (120–150 ms) if a slow machine still struggles. No env var — change the literal.
- **Subagent re-render gating uses `toolCalls` as the dep, not the live `stream.subagents` Map.** `toolCalls` is throttled transitively through `messagesSnapshot` → the messages-processing memo. This means `ChatMessage`'s `sdkSubagents` memo re-runs at the same cadence as the messages snapshot, without needing to plumb the snapshot reference any deeper.
- **Cache only finalized `ToolCall` objects, never pending ones.** Both the `ChatInterface` top-level cache and the `ChatMessage` nested cache key off completion. Pending tool calls intentionally emit a fresh reference on every sample so the *one* streaming box re-renders to show live tokens; every other (completed) tool call holds a stable identity and short-circuits through `React.memo`. Caching pending entries would freeze the live view.
- **Per-message `toolCalls[]` array reuses its previous reference iff every entry has the same identity.** If a single entry changes (the streaming one), the array gets a new reference and only that `ChatMessage` re-renders. This is what makes `React.memo` on `ChatMessage` actually pay off during streaming. The `arrayCache` is keyed by message id and pruned on each pass against `seenMessageIds` so thread switches don't leak references.
- **`previewValue`/`parseToolError` must not run during `pending`.** They feed only the collapsed header text; calling `JSON.stringify` on a streaming tool-arg object every token is the dominant per-render cost we measured. Gate on `status === "pending"` in `ToolCallBox` and skip both. The 256-char cap on `parseToolError` is the secondary guard for when a *completed* result is also large (errors always appear at the start of the payload).
- **`onPointerDown` is the responsive trigger; `onClick` is the keyboard fallback.** Safari swallows `click` during heavy main-thread work; `pointerdown` fires earlier in the input pipeline and survives. The 300 ms timestamp dedupe is necessary because a normal tap fires both events — without it, a single click toggles twice. Apply the same pattern to any future expandable element in the chat surface (`ToolCallBox` and `SubAgentIndicator` are the current copies; keep them in sync).
- **No streamTick prop, no context plumbing for the throttle.** Everything threads through the existing `messages` field on `useChatContext`. The only new exported field is `isHotStreaming`, used solely by `ChatInterface` for the scroll re-engage.

## Deferred (intentional non-goals for v1)

- **Content-skip placeholder for streaming tool args.** Showing "writing… (12,450 chars)" instead of the live text would drop the cost further, but the 80 ms throttle + cost reductions are already smooth. Revisit if a future agent streams much larger payloads (e.g. >100 KB tool args).
- **Throttling files / todos / UI panels.** They already refresh off coarse signals (`stateFilesSig`, `stream.isLoading`), not per-token. No churn observed.
- **Virtualized message list.** `ChatInterface` still maps every message; fine for the current conversation lengths. Add `react-virtuoso` if a single thread starts holding hundreds of messages.
- **Backend changes (batched token emission, async subagent parallelism).** The throttle is purely a UI shape and survives any backend evolution. The deepagents `SubAgentMiddleware` is currently sequential; if it becomes truly async, the same hot window still fires.

## How to verify end-to-end

1. `docker compose up -d` and grab the frontend host port from `docker ps`.
2. Open the chat UI, kick off a flow that ends in **two or more subagents running in parallel and each calling a tool with long streamed args**. (Career-agent's resume-tailor + interview-coach phase is the canonical case today.)
3. During the parallel streaming, without clicking anything: the chat surface keeps painting smoothly, tool boxes appear and update in visible 80 ms chunks, the running subagent rows show the sweep animation, and the next step's output begins streaming below as soon as the parallel phase ends — no manual scroll.
4. During the parallel streaming, click into a streaming tool's `content` arg: the click registers within ~100 ms (it's `pointerdown`-driven, so Safari mid-stream still toggles) and the expanded `<pre>` fills in at the same 80 ms cadence. Click a subagent indicator — same responsiveness.
5. Keyboard activation still works: Tab to a tool box, hit Enter/Space, it toggles. The `onClick` dedupe must not swallow keyboard events (they don't fire `pointerdown`).
6. After streaming ends: the final tool-arg content is fully present (cleanup flush guarantees no missing tail from the last interval tick), the sticky scroll re-engages, the sweep animation stops on completed subagents.
7. Earlier, completed tool boxes in the same thread do **not** re-render while a later one is streaming (verify in React DevTools Profiler — only the streaming `ChatMessage`/`ToolCallBox` should highlight per sample tick).
8. Single-subagent streaming (e.g. an earlier `hiring-recon` step): output streams at native cadence — confirm the throttle didn't regress non-hot paths.
9. With `prefers-reduced-motion` enabled at the OS level, the sweep animation is suppressed but every other behavior above still works.
10. Optional Chrome DevTools Performance recording across the parallel phase: long-task count drops sharply vs. the pre-fix branch.
