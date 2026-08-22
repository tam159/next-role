# CubeSandbox — self-hosted sandbox for the agent's `execute` tool

With `SANDBOX_PROVIDER=e2b`, every shell command the career agent runs (and the
`rendercv render` step) executes inside a hardware-isolated microVM instead of
the backend host. NextRole speaks the standard **E2B API** for this, so the
endpoint can be a self-hosted [CubeSandbox](https://github.com/TencentCloud/CubeSandbox)
cluster (open source, Apache-2.0, data never leaves your infrastructure) — or
E2B Cloud, by pointing `SANDBOX_E2B_API_URL` elsewhere. This guide provisions
CubeSandbox and builds the NextRole sandbox template.

> **Why this isn't a docker-compose service:** CubeSandbox owns a machine. It
> needs a custom PVM host kernel (x86_64) or native KVM (bare metal, incl.
> ARM64), an XFS filesystem at `/data/cubelet`, and runs its components as host
> processes. Nothing here runs on macOS — local dev stays on
> `SANDBOX_PROVIDER=local`, protected by the HiL approval gate.

## 1. Provision a host

One dedicated Linux machine (dev-grade: 4 cores / 8 GB / 50 GB; recommended:
32 cores / 64 GB / 200 GB), then follow **one** upstream path:

- **Standard x86_64 cloud VM** (no `/dev/kvm` needed): install the PVM host
  kernel, reboot, `modprobe kvm_pvm` —
  [Quick Start](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/quickstart.md).
- **Bare metal with native KVM** (x86_64 or ARM64):
  [Bare-Metal Deployment](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/bare-metal-deploy.md).

Mind the upstream constraints: root access, glibc ≥ 2.31, and **XFS mounted at
`/data/cubelet`** (reflink powers its copy-on-write snapshots; Ubuntu/Debian
default to ext4 — see upstream FAQ #311).

Install (single node; PVM path shown):

```bash
curl -sL https://github.com/tencentcloud/CubeSandbox/raw/master/deploy/one-click/online-install.sh \
  | CUBE_PVM_ENABLE=1 bash
```

This brings up the E2B-compatible REST API on **port 3000**, CubeMaster /
Cubelet / CubeShim as host processes, MySQL + Redis via its own compose, and
CubeProxy (TLS via mkcert + CoreDNS routing). A Web console runs on port 12088.

## 2. Build the NextRole template

The template is the filesystem sandboxes boot from. [`template/Dockerfile`](template/Dockerfile)
holds the source: `python:3.13-slim` + `rendercv[full]` (Typst compiler and
fonts ship as wheels). Build it for the sandbox host's architecture, push it to
a registry the host can pull from, then register it **on the sandbox host**:

```bash
# On your workstation (use --platform to match the sandbox host's arch):
docker build -t <your-registry>/next-role-sandbox:latest deploy/cubesandbox/template
docker push <your-registry>/next-role-sandbox:latest

# On the sandbox host (port/probe args per the upstream quickstart):
cubemastercli tpl create-from-image \
  --image <your-registry>/next-role-sandbox:latest \
  --writable-layer-size 1G \
  --expose-port 49999 --expose-port 49983 --probe 49999
cubemastercli tpl watch --job-id <job_id>   # wait for READY, note the template ID
```

Full options: upstream
[Creating Templates from OCI Images](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/tutorials/template-from-image.md).

## 3. Point NextRole at it

In `.env` (then `docker compose up -d backend` — recreate, not `restart`):

```env
SANDBOX_PROVIDER=e2b
SANDBOX_E2B_API_URL=http://<sandbox-host>:3000
SANDBOX_E2B_API_KEY=e2b_000000        # placeholder until auth is enabled (step 4)
SANDBOX_TEMPLATE=<template-id>
```

Optional knobs (defaults in `backend/agents/career_agent/sandbox_backend.py`):
`SANDBOX_TTL_SECONDS` (idle sandbox lifetime, default 1800), `SANDBOX_EXECUTE_TIMEOUT`
(per-command seconds, default 60), `SANDBOX_CWD`, `SANDBOX_MAX_OUTPUT_BYTES`.

Verify end-to-end:

```bash
# Smoke the template + files API against the live endpoint:
cd backend && SANDBOX_E2B_API_URL=http://<sandbox-host>:3000 \
  SANDBOX_E2B_API_KEY=e2b_000000 SANDBOX_TEMPLATE=<template-id> \
  uv run pytest -m integration tests/career_agent/test_sandbox_backend_integration.py
```

In the app, ask the agent to run `uname -a` via `execute` (approve the HiL
prompt): the hostname is the microVM's, not the backend container's. Renders
(`render_resume_pdf`) now execute in the sandbox too; a `rendercv: not found`
exit-127 error means the sandbox was created from the wrong template.

Sandboxes are **thread-scoped**: one per conversation, reused across turns
(rediscovered by metadata after backend restarts) and reaped after
`SANDBOX_TTL_SECONDS` idle. No provider secrets ever enter a sandbox — the
backend creates them with an empty environment.

## 4. Production hardening

- **Enable API authentication** and replace the placeholder key:
  [Authentication](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/authentication.md).
- **Network**: follow upstream
  [Network Hardening](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/network-hardening.md);
  firewall port 3000 (and 12088) so only the NextRole backend hosts reach them.
- **TLS**: if sandboxes are reached over CubeProxy's mkcert HTTPS, set
  `SSL_CERT_FILE=<path to mkcert rootCA.pem>` in the backend environment.
- **Scale**: single node is fine to start;
  [Multi-Node Cluster](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/guide/multi-node-deploy.md)
  when render/exec volume outgrows it.
- The HiL approval gate (PRD 29) stays on regardless — isolation contains
  blast radius, not exfiltration or credential misuse.

**Tested with:** CubeSandbox v0.6.x, `e2b` Python SDK 2.45.x
(`backend/pyproject.toml` pin).
