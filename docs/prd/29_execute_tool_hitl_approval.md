---
type: PRD
title: "Human-in-the-Loop Approval for the execute Tool"
description: "Risky execute (bash) commands pause for approve/edit/reject in the chat — main agent and subagents alike — with a fail-closed read-only allowlist auto-approving safe commands and an env kill switch."
tags: [agent, backend, frontend, subagents]
timestamp: '2026-08-16T11:35:00+07:00'
status: "shipped"
scope: "career agent execute tool + chat approval UI"
version: v1
---

**Extends:** [deepagents 0.7 Behavioral Migration](28_deepagents_v07_migration.md)

# Why

The `execute` tool runs LLM-authored bash on the host with no sandbox (its path handling is [PRD 13](13_execute_virtual_path_translation.md)), and the shared `FilesystemMiddleware` instance hands it to the main agent **and every subagent**. Until a sandbox exists, any model can be one hallucinated `rm -rf` away from real damage. This feature makes a human the gate: commands that are not provably harmless pause the run and wait for an explicit decision, while a conservative read-only allowlist keeps trivial pokes (`ls`, `cat`, `date`) from nagging.

# What the user sees

When the agent proposes a gated command, the run pauses: the execute box pins open with a **Needs review** pill and an approval card — **Approve** (run as-is), **Edit** (fix the args inline, then run), **Reject** (optional message fed back to the model; the sanctioned "never mind, do X instead" channel). Subagent-issued commands show the same card inside the SubagentCard, whose header flips to **Needs review** instead of "Failed". Allowlisted commands run with no pause at all. While a review is pending the composer locks with a "Waiting for your review…" hint, and the thread appears under **Requiring Attention** in the Threads panel. Parallel gated calls each get a card; deciding one shows an "Approved — queued" chip until its siblings are decided, then one resume carries all decisions. After a page reload, an interrupt whose tool box can't re-render (subagent activity doesn't replay) surfaces as a standalone card below the transcript — a pending review is never invisible. `CAREER_AGENT_EXECUTE_APPROVAL=false` (documented in the README env-vars section) disables the gate for offline evals or rollback.

# How — the key architectural choices

**Framework-native gating: one `interrupt_on` parameter, zero server changes.** `create_deep_agent(interrupt_on=execute_interrupt_on())` installs langchain's `HumanInTheLoopMiddleware` on the main agent, and deepagents 0.7 threads the same config into every declarative subagent plus the auto-added general-purpose one — no per-subagent wiring. Subagent interrupts bubble to the root run through the inherited checkpointer/config, and the vendored server ([PRD 21](21_own_agent_server.md)) already implemented the whole lifecycle (thread → `interrupted`, `input_requested` events, id-keyed `input.respond` resume), so the backend diff is one module plus one constructor argument. The graph must NOT pass `checkpointer=` — the server injects Postgres per run and rejects graph-owned savers.

**A fail-closed allowlist as the `when` predicate, instead of gating everything.** `is_auto_approvable` clears a command only if every check passes: ≤500 chars, no shell control/substitution characters anywhere in the raw string (quoted ones also prompt — parsing shell quoting is harder than prompting), clean `shlex` parse, argv[0] in a frozen read-only set (no `find`/`sed`/`git`/`python` — exec/write escape hatches), and no `/`-prefixed or `..` tokens (blocks `cat /etc/passwd` and traversal; relative paths stay under the backend cwd). Anything unprovable interrupts, including predicate exceptions.

**One frontend owner for approval state: `useInterruptApprovals`.** The SDK exposes interrupts inconsistently — `stream.interrupts` carries root-namespace ones live and *all* active ones after hydration, while live subagent interrupts exist only in `getThread().interrupts` ([PRD 18](18_langchain_react_migration.md) runtime). The hook unions both sources, assigns each `action_request` to its tool call **by id** via ordered name+args matching (the middleware emits no call ids; name-keyed maps collapsed parallel same-name calls), accumulates per-action decisions, and submits exactly one ordered `{decisions: [...]}` resume per interrupt — the middleware hard-errors unless `len(decisions)` equals the gated-call count.

# Files of interest

| Concern | Path |
|---|---|
| Approval policy: allowlist, `when` predicate, kill switch | `backend/agents/career_agent/execute_approval.py` |
| `interrupt_on` wiring into the graph | `backend/agents/career_agent/agents.py` (`build_career_agent`, lines ~210–218) |
| Intentionally ungated direct shell call | `backend/agents/career_agent/tools.py` (`_run_rendercv`, lines ~404–425) |
| Interrupt derivation + decision batching + claims | `frontend/src/app/hooks/useInterruptApprovals.ts` |
| Id-targeted resume (`respond` with `interruptId`/`namespace`) | `frontend/src/app/hooks/useChat.ts` (`resumeInterrupt`) |
| Approval card (emits ONE decision; queued chip) | `frontend/src/app/components/ToolApprovalInterrupt.tsx` |
| Per-call status, fallback approval block, composer lock | `frontend/src/app/components/ChatInterface.tsx` |
| Nested subagent approvals + terminal-spinner coercion | `frontend/src/app/components/SubagentCard.tsx` |
| Wire types (snake_case `ReviewConfig`, `PendingApproval`) | `frontend/src/app/types/types.ts` |
| Classifier table + interrupt/resume flow tests | `backend/tests/career_agent/test_execute_approval.py`, `test_agents_hitl.py` |

# Decisions worth remembering

- **The composer locks while a review is pending.** The v2 protocol coerces ANY new input on an interrupted thread into `Command(resume=<input>)` — a plain chat message crashes the middleware with `KeyError('decisions')` and errors the run (observed live). Blocking input, with Reject-with-message as the redirect channel, is the only shape the protocol supports; "send a new message to skip the approval" was the original plan and had to be abandoned.
- **The allowlist verdict is character-level, which can surprise.** `date '+%Y-%m-%d %H:%M:%S %Z (%z)'` gates (parens are metacharacters, even quoted) while `date -u '+%Y-%m-%dT%H:%M:%S %z'` auto-approves — a real user-confusion incident that looked like a subagent bypass but was two different commands. Conservative-by-character is intended; widening means editing `_SAFE_BINARIES`/`_UNSAFE_CHARS` plus the test table.
- **`_run_rendercv` stays ungated.** It calls `backend.execute()` directly with a developer-authored `rendercv render` command in a throwaway temp dir — not model-authored bash. Gating it would double-prompt a purpose-built tool. Also: `VirtualPathShellBackend._translate`'s shlex-rejoin quoting is NOT a security boundary (raw passthrough on shlex errors) — the allowlist never leans on it.
- **`review_configs` pair with `action_requests` by index, never by name.** The pre-existing UI keyed both maps by tool name (and read camelCase keys) — always missing, silently falling back to default decisions, and collapsing parallel same-name calls. Fixed with wire-shape types and id-keyed routing.
- **Responded interrupt ids are never pruned.** The ThreadStream replays historical `input.requested` events on reattach; forgetting a responded id would resurrect a resolved interrupt as a phantom approval card. Ids are namespace hashes (globally unique), so the session-lifetime set is safe. Same hash fact makes id-keyed `respond` work for nested interrupts even though the server strips `ns`.
- **Post-resume subagent spinners are coerced, not streamed.** Gated calls get no tools-channel events (the middleware pauses before the tool starts) and a resumed run's namespaced events don't reach already-mounted projections — so once the snapshot is terminal, leftover "pending" nested calls render as done (render-time only, after approval matching). Their RESULT panes still need a refresh; the infinite spinner was the bug, missing data is a fact.
- **Kill switch reads at graph build time** (pydantic-settings, `CAREER_AGENT_` prefix, precedent `ObjectStoreSettings`); env changes need `docker compose up -d backend` (recreate), not `restart`. Per user preference, the `.env.example` comment is one line — the real documentation lives in the README's "Environment variables" section.

# Deferred (intentional non-goals for v1)

- **A real sandbox.** HiL is the interim safety layer; a sandboxed executor would let the allowlist widen dramatically and is the trigger to revisit this whole policy.
- **`respond` decision type.** Meaningless for a shell tool (it fakes a successful tool result); the card has no respond branch. Revisit if an `ask_user`-style tool appears.
- **Per-subagent `interrupt_on` overrides.** deepagents supports them in the subagent spec; one shared policy is enough until a subagent needs different rules.
- **Gating other tools** (`write_file`, `edit_file`). They write to virtual routes, not the host shell. If `permissions=` is ever passed to `create_deep_agent`, mirror it on the replacement `_fs_middleware` instance (comment in `agents.py`).
- **Live RESULT panes for subagent calls after resume** — requires SDK-level event replay for mounted namespaced projections; refresh shows the truth.
- **A server smoke e2e for the interrupt lifecycle** — covered today by graph-level tests plus manual verification.

# How to verify end-to-end

1. `docker compose up -d`; sign in with a throwaway demo account; ports from `docker ps`.
2. "Call the execute tool yourself with command: id" → execute box pins open with the approval card; Approve → `uid=...` output lands. Repeat with Reject (+ message) → error tool row, agent acknowledges, command never ran. Repeat with Edit → the edited command's output returns.
3. "Run `ls` with execute" → no pause (allowlist). `date '+%Y-%m-%d %H:%M:%S %Z (%z)'` → pauses (parens).
4. Delegate a gated command to the general-purpose subagent → SubagentCard shows **Needs review**, pinned open, card inside; Approve → task completes, **no lingering spinners**.
5. While a review is pending: composer is locked with the hint; reload the page → the card reappears (in the box for main-agent, in the fallback block for subagent) and still resumes.
6. `SELECT status, interrupts FROM thread ...` mid-pause shows `interrupted` + the HITL payload; after approve, `idle` + `{}`.
7. `cd backend && uv run pytest` (classifier table + 10 interrupt/resume flow tests) and `pnpm --dir frontend test`.
8. Set `CAREER_AGENT_EXECUTE_APPROVAL=false`, `docker compose up -d backend` → step 2 runs with no pause. Revert.
