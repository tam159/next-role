# PRD: Progressive Tool Disclosure (v1)

**Status:** shipped · **Scope:** `frontend/` (chat transcript) · **Extends:** [UI/UX Modernization](20_ui_modernization.md)

## Why

The transcript rendered every main-agent tool call and every subagent's INPUT/ACTIVITY/OUTPUT panel fully expanded, forever. A real prep run (a dozen root tool calls + three subagents) buried the final answer — the only thing non-technical users care about — under screens of machinery. But the machinery can't just disappear: watching it stream is what makes the agent feel alive, and the expanded trail is the first-step debugging view before reaching for LangSmith. Industry research (Vercel AI Elements `Reasoning`/`Tool`, assistant-ui `ToolGroup`, ChatGPT deep research) converged on one pattern: **expanded while running, auto-collapsed to a summary when done, always re-expandable**. Separately, each subagent rendered as two disconnected boxes (a header chip + a separate detail panel) that couldn't collapse as a unit.

## What the user sees

While the agent works, today's live view: tool rows appear with spinners on the timeline rail, subagent cards stream INPUT/ACTIVITY. The moment a unit finishes **it** collapses (per-unit, not at run end): a consecutive run of main-agent tool calls becomes one rail row — "12 tool calls · `write_todos` · `list_files` · `parse_document` +3" with a status node and an "n failed" pill when applicable — and a subagent becomes just its header: identity icon, name, status badge, "n tools" pill, duration. A run of exactly one call stays a plain collapsed `ToolCallBox`, no wrapper. Runs span AI messages and break only on assistant prose, a subagent card, or a user message.

Expanding a collapsed run shows the execution timeline: per-step clusters, with simultaneous calls opening under a "⑂ N IN PARALLEL" micro-label and closing with a hairline break. Subagent ACTIVITY gets the same parallel markers (but no inner collapse). Subagents have identity icons — hiring-recon = Radar, resume-tailor = Scissors, interview-coach = MessagesSquare, anything undeclared = Bot. All disclosure chevrons follow one convention: **right when collapsed, rotating down when expanded** (previously the tool rail used down→up while cards used right→down). Manual toggles win permanently; a pending tool-approval pins its group open; reloaded threads mount already collapsed with no animation. AI prose is never collapsed.

## How — the key architectural choices

**Collapse is pure presentation state derived from signals the frontend already has — zero backend change.** A shared `useAutoCollapse(isRunning, {forceExpanded})` hook mounts expanded iff running, auto-collapses once on the running→terminal transition (render-phase adjustment keyed off a derived boolean, so the `processedMessages` identity caches can't mask a transition), and re-expands on terminal→running (interrupt resume). "Running" is `isLoading`-gated — `!!isLoading && …` — which makes history reload, the Stop button, and an interrupt pause all read as terminal for free. Subagents key off `snapshot.status` (the authority; trailing namespaced events can't hold a card open).

**Collapsed bodies stay mounted** (`Collapse.tsx`: CSS grid-rows `1fr`↔`0fr` transition + `inert`), instead of conditional unmount. This preserves `ToolCallBox` expand state and the ref-counted namespaced subscriptions (the Workspace sources probe shares them), renders history at `0fr` with no entry animation, and keeps DOM cost ≤ the old always-expanded rendering. Consequence for tests: assert `aria-expanded`/`inert`, never text absence.

**Run grouping is computed in `ChatInterface`, not `ChatMessage`, because runs span messages.** A walk over `processedMessages` merges consecutive non-`task` calls into `batches: ToolCall[][]` keyed by head message id (one batch = one AI message = calls issued in parallel), breaking *before* a message with prose (prose renders above its own calls) and *after* one that spawns a subagent (the card renders below them). The same identity-caching discipline as `processedMessages` keeps `React.memo` effective. An `openEndedHeadId` marks a transcript that ends inside a run, holding the tip group open across the model's think-pauses between batches — without it the group would flicker collapsed/expanded between steps.

## Files of interest

| Concern | Path |
|---|---|
| Auto-collapse state machine | `frontend/src/app/hooks/useAutoCollapse.ts` |
| Always-mounted animated disclosure | `frontend/src/app/components/Collapse.tsx` |
| Run summary row + singleton short-circuit + interrupt pin | `frontend/src/app/components/ToolCallGroup.tsx` |
| Batch clusters, "N in parallel" labels, closing hairline | `frontend/src/app/components/ToolCallBatchList.tsx` |
| Cross-message run building + open-ended tip | `frontend/src/app/components/ChatInterface.tsx` (`toolBatchesByHead`, lines ~320–375) |
| Merged one-card subagent, `QueuedSubagentCard`, icon map, activity batching | `frontend/src/app/components/SubagentCard.tsx` (`SUBAGENT_ICON_MAP`, `nestedBatches`) |
| Head-message rendering of the run | `frontend/src/app/components/ChatMessage.tsx` (`toolBatches` prop) |
| Shared pointerdown-first toggle | `frontend/src/app/hooks/usePointerToggle.ts` |
| Chevron convention (right→down) applied to rows/args/sidebar | `frontend/src/app/components/ToolCallBox.tsx`, `TasksFilesSidebar.tsx` |
| `formatDuration` | `frontend/src/app/utils/utils.ts` |
| Component specs (tool-call-group, subagent-card, chevron rule) | `frontend/DESIGN.md` |

## Decisions worth remembering

- **Per-unit collapse, not collapse-at-run-end (user call).** Each unit tidies itself the moment it finishes, so during a long run the only expanded thing is the active one — matching ChatGPT/assistant-ui. The literal alternative (everything stays open until the run ends) was offered and rejected.
- **Runs merge across messages (user correction).** v1 of the group collapsed per AI message, which still left ~10 rows per turn (each `write_todos` was its own "group"). The user wanted one unit per consecutive run *with* the parallelism still visible when expanded — hence batches preserved inside the merged group instead of flattening.
- **The queued subagent card must be hook-free.** `useToolCalls(stream, undefined)` subscribes to the ROOT namespace, so the pre-discovery path can't reuse the main card component. `QueuedSubagentCard` renders the same header markup (pixel-stable queued→running transition) with no hooks.
- **Durations under 1s are suppressed.** History-reseeded `SubagentDiscoverySnapshot`s carry hydration timestamps (`startedAt ≈ completedAt`), so every historical card showed a bogus "<1s". Live runs always exceed 1s, so the guard costs nothing real.
- **Subagent ACTIVITY batches come from the replayed message list, degrading to flat.** `AssembledToolCall` carries no step marker, so parallelism is recovered by walking the subagent's AI messages (`tool_calls` + streaming `tool_call_chunks`); calls absent from replay append as single steps — old threads beyond the event-log window just look like today's flat list. Verified empirically that namespaced replay delivers messages for historical threads (hiring-recon showed 2/3/4/5-in-parallel clusters after reload).
- **Approval requests attach only to `status === "interrupted"` calls.** The old name-keyed lookup hit every same-name box in the last message; with merged runs it would also hit completed same-name calls from earlier messages. Interrupt maps now go to every `ChatMessage` (stable identities; the head of a run isn't necessarily the last message) and route by status.
- **One chevron convention: right = collapsed, down = expanded (user spotted the inconsistency).** The tool rail used down→up while subagent/workspace cards used right→down. Standardized on the disclosure (file-tree) convention product-wide — including `ToolCallBox`'s inner Arguments rows and the sidebar section toggles — and wrote the rule into `DESIGN.md`.
- **The React-Compiler lint shaped the hook.** `react-hooks/refs` forbids ref access during render: the user-override flag became state (it drives render decisions), `forceExpanded` became a `toggle` dependency instead of a mirrored ref, and the dynamic icon lookup had to be a static property access (`SUBAGENT_ICON_MAP[name] ?? Bot`) so `react-hooks/static-components` can prove it isn't a component created mid-render.

## Deferred (intentional non-goals for v1)

- **Scroll compensation during auto-collapse.** Chrome/Firefox native scroll anchoring plus the 300ms height animation handle it; Safari (no anchoring) may show a shift when scrolled up mid-run. The contained fix — scrollTop adjustment in `Collapse` — is designed but unbuilt; add it if Safari verification shows an objectionable jump.
- **Auto-opening `ToolCallBox` on a late-arriving `actionRequest`.** A box only auto-opens if the request exists at mount; the group-level pin already keeps the approval reachable, so the effect was skipped to keep `ToolCallBox` internals untouched.
- **Collapse inside subagent ACTIVITY.** The card is the collapse unit; nesting another accordion inside it adds interaction depth for no scanning benefit (user agreed).
- **A "Stopped" badge for stuck-running snapshots.** After Stop, a subagent snapshot can stay `running` while the collapsed card's badge still says "Running" — pre-existing data limitation, kept for parity.

## How to verify end-to-end

1. `docker compose up -d`; open the frontend host port from `docker ps`. Open a past thread with subagent activity: everything renders **already collapsed, no animation** — "N tool calls · names" rail rows and one-line subagent cards (Radar/Scissors/MessagesSquare icons, "n tools" pill, no "<1s" duration).
2. Expand a run: per-step clusters with "⑂ N IN PARALLEL" labels and a hairline closing each parallel cluster; expand a subagent: one card opens beneath its header with INPUT / ACTIVITY (parallel markers) / OUTPUT. All chevrons point right closed, down open.
3. Send a message that triggers two sequential tool steps then prose (e.g. "read X, then list Y, then answer"). Watch: first call streams as a bare row → second arrives and the group forms, **staying expanded through the think-pause** → the group collapses the moment prose starts streaming.
4. Manually expand a finished unit and collapse a running one — both stick through the rest of the run. Reload mid-thread: collapsed everywhere.
5. `pnpm --dir frontend test` (403 tests) and `pre-commit run --files $(git ls-files --modified --others --exclude-standard)` — clean.
