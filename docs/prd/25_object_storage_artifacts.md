# PRD: Object Storage for Binary Artifacts (v1)

**Status:** shipped · **Scope:** storage layer (backend + compose + frontend file plumbing) · **Extends:** [21_own_agent_server](21_own_agent_server.md)

## Why

Cloud-deployment step 1. Binary artifacts (uploaded CV/JD files, rendered resume and battlecard PDFs) lived on local disk under `backend/agents/career_agent/`, reachable only because docker-compose bind-mounts the whole repo into *both* the frontend and backend containers — a shared-disk assumption that cannot deploy anywhere. Byte-moving logic was also triplicated (agent `CompositeBackend`, agent PDF tools writing disk directly, five Next.js `/api/files/*` routes doing raw `node:fs`), and the Next.js routes held the only HTTP path for file bytes. This PRD moves the three binary prefixes (`/upload/`, `/tailored_resume/`, `/interview_battlecard/`) into S3-compatible object storage — SeaweedFS locally, S3 / GCS / Azure in the cloud via the same client — with one path↔key mapping owned by Python.

## What the user sees

Nothing changes in the UI — workspace listing, PDF preview, upload (composer paperclip + Files button), edit, and delete all behave as before. What's different underneath: files survive in a bucket (named volume locally) instead of the repo tree; uploads no longer materialize under `backend/agents/career_agent/upload/`; and a new SeaweedFS filer UI (`http://localhost:${OBJECT_STORE_UI_LOCAL_PORT}`) lets you eyeball raw objects. Keys are deterministic: `/upload/cv.pdf` ↔ `users/default/career_agent/upload/cv.pdf`. Deploying to a cloud bucket is an `OBJECT_STORE_*` env change, not a code change.

## How — the key architectural choices

**A custom deepagents backend (`ObjectStoreBackend`), mounted as `CompositeBackend` routes — not a FUSE mount or a disk-sync layer.** deepagents ships no S3 backend (the community catalog has only MongoDB), but `BackendProtocol` is the documented extension point and the existing route architecture is exactly the recommended shape, so the change is additive: three routes swap physical stores while `/render_intermediate/`, all Postgres `StoreBackend` routes, and the `execute` shell stay put. FUSE (privileged containers on macOS, flaky subprocess semantics) and write-through disk sync (two sources of truth that drift) were rejected.

**The Python server owns the file HTTP surface, mounted via the `LANGGRAPH_HTTP` custom-app hook — the five Next.js routes are deleted, not converted.** The "minimal" alternative (swap `node:fs` for an S3 SDK inside the Next routes) would have left the storage layer written twice, put credentials in the frontend, and hit Vercel's ~4.5 MB serverless body cap on 10 MB uploads — all of it redone at deploy time. Instead `backend/agents/files_api.py` (plain Starlette, zero imports from `server/`) serves `/files/{list,read,upload,write,delete}` with the exact response shapes the frontend already consumed, so `FileViewDialog`/`useChat` didn't change; the browser now calls the deployment URL for files exactly as it already did for store/threads/runs. `enable_custom_route_auth` is the ready-made auth hook for multi-user later.

**Rendering became one tool, `render_resume_pdf` (read → hydrate → rendercv → verify → publish), replacing `prepare_render_settings` + raw `execute`.** rendercv is a subprocess and needs a real filesystem, so the tool writes a render copy into a throwaway `TemporaryDirectory`, injects the `settings:` block *only there*, and uploads the resulting `.pdf` (plus the `.typ` typesetting intermediate — `dont_generate_typst: true` breaks PDF output, a rendercv quirk) to the bucket next to the YAML. The stored YAML stays settings-free — the old flow baked machine-absolute paths into a durable artifact, which breaks re-rendering from any other host. There is deliberately **no** `/render_intermediate/` artifact area (v1 briefly had one; it accumulated PII-bearing render copies in the repo tree). The tool result names only the PDF, so the agent never cites the `.typ`. Single-tool over the previous three-step dance: the agent physically cannot render-and-forget-to-publish, and failures return one stage-named error (`Error (render): …` carries rendercv's output for the fix-YAML-and-retry loop).

## Files of interest

| Concern | Path |
|---|---|
| Settings, lazy obstore client, path↔key mapping, byte helpers | `backend/agents/career_agent/object_storage.py` |
| `BackendProtocol` implementation (per-area routes) | `backend/agents/career_agent/object_backend.py` |
| Files HTTP API (mounted via `LANGGRAPH_HTTP`) | `backend/agents/files_api.py` |
| Route mounting + tool wiring | `backend/agents/career_agent/agents.py` (routes, `_SUBAGENT_TOOLS`) |
| `render_resume_pdf` pipeline + YAML auto-repair | `backend/agents/career_agent/tools.py` (`make_render_resume_pdf`, `_normalize_resume_yaml`) |
| SeaweedFS service + bucket init + backend env | `docker-compose.yml` (`object-store`, `object-store-init`) |
| Frontend artifact fetch/write via deployment URL | `frontend/src/app/lib/agentFiles.ts` (`filesApiUrl`, `fetchArtifactFiles`) |
| Virtual-path upload/delete client | `frontend/src/app/lib/uploadFiles.ts` |
| Contract tests over in-memory store (no emulator) | `backend/tests/career_agent/test_object_backend.py`, `backend/tests/test_files_api.py` |

## Decisions worth remembering

- **SeaweedFS as the local stand-in — because MinIO and LocalStack both died in 2026.** MinIO's community repo was archived (Apr 2026, source-only, console gutted); LocalStack went closed-source and its free tier loses S3 state on every restart — disqualifying for a store holding the user's uploads. RustFS (nice console) is still alpha. SeaweedFS: Apache-2.0, one container (`weed server -s3`), persistent volume, filer UI. Without `-s3.config` its auth is disabled, so the `OBJECT_STORE_*` dev creds are accepted-but-not-enforced — fine on the compose network, real IAM arrives with cloud provisioning.
- **obstore over fsspec/boto3.** One Rust wheel with zero Python deps (s3fs's aiobotocore pin would fight the boto3 already locked for Bedrock), native S3+GCS+Azure (the user named all three), `MemoryStore` for emulator-free unit tests, `sign()` for future presigned URLs. Gotchas: missing keys raise builtin `FileNotFoundError` (`obstore.exceptions.NotFoundError` is a deprecated alias); `delete()` is idempotent, so the API head-checks to preserve its 404 contract; there is no create-bucket API — hence the curl-PUT init container (SeaweedFS only auto-creates buckets for authenticated admins).
- **No Postgres path/URL registry.** The original plan assumed "Postgres stores file path/url"; review showed a registry is dual-write liability with no consumer — the key is a pure function of the virtual path and `ListObjectsV2` is the listing source of truth. The `users/default/` key segment is the multi-user seam: inject a real identity into one mapping function, no key migration.
- **`OBJECT_STORE_*` is deliberately not `AWS_*`.** `AWS_ACCESS_KEY_ID` etc. are live Bedrock credentials read globally by boto3; pointing them at the emulator would break model calls. Two separate credential planes.
- **`ObjectStoreBackend` must NOT subclass `SandboxBackendProtocol`, and `write` must refuse overwrite with the framework's exact error literal.** `CompositeBackend.execute` always dispatches to the *default* backend — a sandbox-flavored route would break the gating. And `tools._upsert` falls back to `edit` only when `write` returns the canonical "already exists. Read and then make an edit" message; a paraphrase silently breaks every re-parse/overwrite flow. Both are locked by tests.
- **Deterministic YAML auto-repair before rendercv (added after dogfooding).** A real run burned 3–4 LLM roundtrips on one defect class: unquoted `colon + space` inside a bullet, which YAML parses as a one-pair mapping ("Input should be a valid string"). `_normalize_resume_yaml` repairs the mechanically-invertible classes (mid-string colons, trailing colons, bare numbers) in the scratch copy only — the stored YAML keeps the agent's text, the success message reports `auto-repaired N …` as corrective feedback, and booleans are skipped (`Answer: yes` → `True` has no faithful inverse). The skill also gained a proactive quoting rule and a "sweep the whole file for the same pattern" instruction.
- **Migration was a one-shot in-container script, not app code.** Six legacy files copied via the same `put_bytes`/`key_for_virtual_path` helpers; old disk copies left in place (gitignored, inert) for the user to delete.

## Deferred (intentional non-goals for v1)

- **Presigned URLs + lazy content fetch.** The workspace still eagerly fetches every file as base64 through the backend on refresh — same behavior as before, fine locally. Presign (`obstore.sign()`, ≤15 min) plus on-demand reads is the cloud-phase fix; it needs auth to be meaningful.
- **Auth on `/files/*`.** Single-user posture unchanged; `enable_custom_route_auth` + `LANGGRAPH_AUTH` are the prepared hooks when multi-user lands.
- **Managed-bucket provisioning** (versioning, SSE, Block Public Access, lifecycle rules, least-priv IAM) — belongs to cloud infra, not this repo.
- **Azurite / fake-gcs-server compose profiles.** obstore is the portability layer; add provider-fidelity emulators only when an Azure/GCS target is concrete.
- **Remote-sandbox renders.** rendercv still runs on whichever process hosts the shell backend (in a throwaway temp dir); moving it into an isolated sandbox is the roadmap's multi-tenant step.

## How to verify end-to-end

1. `docker compose up -d --build` — `object-store` healthy, `object-store-init` exits 0, bucket visible: `curl -s http://localhost:${OBJECT_STORE_LOCAL_PORT}/next-role-artifacts` returns `ListBucketResult`.
2. `curl -s "http://localhost:${LANGGRAPH_LOCAL_PORT}/files/list?prefixes=/upload/"` — JSON `{files:[…]}` with virtual paths; `/files/read` on a PDF returns base64, on a YAML returns utf-8; DELETE twice → 200 then 404; `/files/read?path=/processed/x.md` → 403.
3. UI: upload a PDF from Workspace > Files — object appears in the bucket, **no file** under `backend/agents/career_agent/upload/`; card preview renders the PDF; delete works (confirm dialog).
4. Full agent flow (CV + JD → tailor): the stored `/tailored_resume/<r>/<j>.yaml` has **no `settings:` block**, `.pdf` + `.typ` siblings land in the bucket, nothing is written under `backend/agents/career_agent/`, and the reply cites only the PDF path. A YAML with unquoted mid-string colons renders first try with an `auto-repaired N` note.
5. `docker compose restart object-store` → objects persist; `docker compose stop object-store` → `/files/*` returns in-band 500s, agent tools return `Error (…)` strings; `start` again → self-heals (~20 s warmup).
6. `cd backend && uv run pytest` (unit, no emulator needed) and `uv run pytest -m integration` (against compose SeaweedFS); `pnpm --dir frontend test`.
