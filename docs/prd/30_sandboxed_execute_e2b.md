---
type: PRD
title: "Sandboxed Shell Execution via the E2B Protocol"
description: "SANDBOX_PROVIDER=e2b moves execute (and rendercv renders) off the backend host into thread-scoped remote microVMs over the E2B API — self-hosted CubeSandbox in production, E2B Cloud by env change — with local mode untouched and HiL approval applying in both."
tags: [agent, backend, infra, pdf]
timestamp: '2026-08-22T10:56:00+07:00'
status: "shipped"
scope: "career agent shell backend + rendercv pipeline + deploy guide"
version: v1
---

**Extends:** [Human-in-the-Loop Approval for the execute Tool](29_execute_tool_hitl_approval.md), [Virtual-Path Translation in the execute Tool](13_execute_virtual_path_translation.md)

# Why

`execute` ran LLM-authored bash via `subprocess` on the backend host — [PRD 29](29_execute_tool_hitl_approval.md) made a human the gate, but named "a real sandbox" as the deferred fix, and the README listed it as the blocker before untrusted signups. The requirement: an **open-source, self-hostable** runtime usable freely in production with **no data leaving to third parties** — managed sandboxes (E2B Cloud, Modal, Runloop, …) were excluded on that ground, while keeping a future move to E2B Cloud cheap. A landscape survey (2026-08) left exactly one candidate satisfying all of it: Tencent **CubeSandbox** (Apache-2.0, KVM microVMs) is the *only* OSS runtime exposing the E2B API; Daytona's OSS repo froze in June 2026, E2B's own infra needs a Terraform+Nomad estate, microsandbox/OpenShell speak proprietary SDKs, and container-based options (docker.sock, Judge0) are weaker isolation with privilege problems.

# What the user sees

Nothing, by default: `SANDBOX_PROVIDER=local` keeps today's behavior byte-for-byte and `docker compose up` stays zero-config. With `SANDBOX_PROVIDER=e2b` + `SANDBOX_E2B_API_URL/API_KEY/TEMPLATE` set, the same chat UI and the same HiL approval cards appear — but approved commands run inside a hardware-isolated microVM (`uname -a` shows the sandbox's hostname), one sandbox per conversation, reused across turns and reaped after `SANDBOX_TTL_SECONDS` idle. `render_resume_pdf` renders inside the sandbox too. **Deliberately not a compose service**: CubeSandbox owns a dedicated Linux machine (custom PVM kernel on x86_64 cloud VMs, or native-KVM bare metal incl. ARM64, XFS at `/data/cubelet`, host processes) and nothing runs on macOS — provisioning + the NextRole template image live in `deploy/cubesandbox/`.

# How — the key architectural choices

**One provider toggle at the composite default, speaking the E2B protocol.** [PRD 13](13_execute_virtual_path_translation.md) established that swapping the backend class is the entire wiring change, so `make_default_shell_backend()` picks the `CompositeBackend(default=…)` at graph build: `local` → the historical `VirtualPathShellBackend` (the rollback lever), `e2b` → `E2BSandboxBackend`, a `deepagents.backends.sandbox.BaseSandbox` subclass over the standard `e2b` SDK. Subclassing is mandatory, not stylistic — the framework gates `execute` exposure with nominal `isinstance` checks. Speaking E2B (not CubeSandbox-specific APIs) means self-hosted CubeSandbox and E2B Cloud are the same code path, selected purely by `SANDBOX_E2B_API_URL`.

**Every failure maps in-band; the backend never raises.** Two verified facts shaped this: the filesystem middleware's tool wrapper catches only `NotImplementedError`/`ValueError` (anything else crashes the run instead of producing a ToolMessage), and the e2b SDK *raises* `CommandExitException` on non-zero exits. So the backend converts exits to `ExecuteResponse(exit_code=…)`, timeouts to exit 124, and endpoint-down/misconfiguration to an `Error: sandbox unavailable (…)` result the model can react to — a dead sandbox host degrades to failed commands, never errored runs or a refused boot.

**Thread-scoped sandboxes resolved at call time; the render scratch follows the shell.** The backend is a module-level singleton (deepagents 0.7 style), resolving `<identity>:<thread_id>` from `get_config()` per call (the `scope.py` precedent), caching handles in-process, rediscovering live sandboxes by metadata after backend restarts, and creating with `envs={}` — the remote parallel of `default_shell_env()`'s PATH-only stance, so provider secrets never enter a sandbox. Because rendercv needs its working files where the command runs, the host-`TemporaryDirectory` flow became a `RenderScratch` seam: tools dispatch through the composite default's `open_render_scratch()` hook (host temp dir fallback), so in e2b mode the YAML is uploaded into a sandbox dir, rendered there, and the PDF/typ downloaded before publishing.

# Files of interest

| Concern | Path |
|---|---|
| Settings, `E2BSandboxBackend`, provider factory | `backend/agents/career_agent/sandbox_backend.py` |
| Host/sandbox render-scratch implementations | `backend/agents/career_agent/render_scratch.py` |
| Scratch dispatch + refactored render pipeline | `backend/agents/career_agent/tools.py` (`_open_render_scratch` → `_collect_render_outputs`, lines ~380–470) |
| The one-expression wiring swap | `backend/agents/career_agent/agents.py` (`_backend`, lines ~44–56) |
| Fake-SDK unit tests (lifecycle, error mapping, files) | `backend/tests/career_agent/test_sandbox_backend.py` |
| Sandbox-mode render pipeline tests | `backend/tests/career_agent/test_tools_render_resume_sandbox.py` |
| Opt-in live-endpoint smoke (`-m integration`) | `backend/tests/career_agent/test_sandbox_backend_integration.py` |
| CubeSandbox provisioning + template image | `deploy/cubesandbox/README.md`, `deploy/cubesandbox/template/Dockerfile` |
| Production topology & env knobs | `backend/ARCHITECTURE.md` (§7 table, §10 "Sandboxed shell execution") |

# Decisions worth remembering

- **CubeSandbox is reached over HTTP, not run in compose.** The original ask was a sandbox service in `docker-compose.yml`; research killed it — CubeSandbox requires a custom PVM host kernel or native KVM, XFS, and root host processes, none of which exist inside Docker Desktop on macOS. Confirmed direction: env-pointer integration, `local` default for dev, `deploy/cubesandbox/` for provisioning. A privileged compose stunt was rejected as upstream-unsupported.
- **Own `BaseSandbox` subclass instead of `langchain-e2b`.** The integration package's `E2BSandbox(sandbox=…)` binds one live sandbox at construction — wrong lifecycle for an import-time singleton needing per-thread resolution. `LangSmithSandbox` (in-tree) served as the reference implementation instead.
- **No virtual-path translation in sandbox mode.** `_rewrite_token` stats local disk, which is meaningless remotely — and unnecessary: in e2b mode, unrouted file-tool paths and shell paths are both sandbox-native, and the framework's "Shell paths vs. virtual paths" prompt section auto-flips to declare routed prefixes shell-inaccessible when the default isn't a `LocalShellBackend`.
- **Parity numbers are deliberate.** `SANDBOX_EXECUTE_TIMEOUT=60` matches `VirtualPathShellBackend(timeout=60)`; `SANDBOX_MAX_OUTPUT_BYTES=100_000` mirrors `LocalShellBackend` truncation; keep the `timeout` kwarg in `execute()` — `execute_accepts_timeout` introspects the signature.
- **HiL stays on in both modes, unchanged.** Isolation contains blast radius, not exfiltration or API misuse — an explicit product call. [PRD 29](29_execute_tool_hitl_approval.md)'s allowlist, kill switch, and `_run_rendercv` bypass are untouched; the gate keys on the tool name, so the swap needed zero frontend or approval-policy changes.
- **Capture-offload stays off.** `/large_tool_results/` routes to `StoreBackend`, which disables deepagents' capture-at-source path even with a `BaseSandbox` default; `enable_capture_offload = False` pins it against future route edits.
- **Sandbox template is wheels-only.** `python:3.13-slim` + `rendercv[full]` — Typst and the fonts ship as wheels, so no apt fonts/typst; WeasyPrint is absent because the battlecard renders in-process in the backend ([PRD 25](25_object_storage_artifacts.md) render pattern).

# Deferred (intentional non-goals for v1)

- **Widening the auto-approve allowlist for sandboxed deployments** — PRD 29's named trigger has now fired, but the user wants the conservative gate as-is; revisit as a policy-only change once a real e2b deployment runs.
- **Native `aexecute`** — the protocol default thread-offloads sync `execute`; switch to `e2b.AsyncSandbox` only if worker throughput demands it.
- **Assistant-scoped or pooled sandboxes** — thread-scoped is deepagents' recommended default; pooling is a cost optimization with no current pressure.
- **CubeSandbox cluster/Terraform/K8s deploys and egress-proxy policies** — the guide covers a single node; upstream docs own the rest.
- **envd parent-dir semantics on CubeSandbox** — E2B's `files.write` creates parents (the protocol requires uploads to); the integration smoke asserts it against a real endpoint before first production use.

# How to verify end-to-end

1. Local zero-regression: default `.env` (no `SANDBOX_*`), `docker compose up -d --build backend` (new `e2b` dep needs the rebuild) → tailor a resume; `render_resume_pdf` publishes the PDF; a gated `execute` still pauses for approval. Verified this session.
2. `cd backend && uv run pytest` — 282 passed incl. 26 new sandbox/scratch tests; `uv run ty check` and the pre-commit batch clean.
3. Against a real endpoint (host from `deploy/cubesandbox/README.md`): set `SANDBOX_PROVIDER=e2b` + `SANDBOX_E2B_*` + `SANDBOX_TEMPLATE`, `docker compose up -d backend` (recreate, not restart) → `execute` of `uname -a` returns the microVM's hostname; the sandbox id is stable across turns in one thread; renders publish; HiL still gates.
4. `SANDBOX_E2B_API_URL=… uv run pytest -m integration tests/career_agent/test_sandbox_backend_integration.py` — smokes exec, `rendercv --version` in the template, and a non-UTF8 file roundtrip.
