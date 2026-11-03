# Deploy the core app to Vultr (Singapore) + Cloudflare R2

The manual for running NextRole's **core app** (frontend, backend agent server, core-server, Postgres, Redis) on one Vultr VPS, with binary artifacts in Cloudflare R2, Google-only sign-in, a fixed Claude Sonnet 5.5 model, and per-user usage quotas sized for a $100 Anthropic API credit. The analytics stack (ClickHouse, Dagster, dbt, Cube, Superset) and the analytics agent are out of scope.

```
Browser ──https──▶ Cloudflare (DNS + proxy, TLS) ──https──▶ Vultr VPS (Singapore)
                                                             Caddy :443
                                                               ├─ app.next-role.dev → frontend:3000 (Next.js + Better Auth)
                                                               └─ api.next-role.dev → backend:8000 (agent server, SSE)
                                                             core-server (gRPC) · Postgres 18 · Redis 8
backend ──S3 API──▶ Cloudflare R2 (uploads + rendered PDFs)
backend ──▶ Anthropic API (claude-sonnet-5-5) · LlamaParse · Tavily/TinyFish
```

Placeholders used below: `next-role.dev` (the zone already on Cloudflare), `app.` for the UI and `api.` for the agent server. Swap in your own names if they differ.

## Part 1 — What this folder adds

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Production stack: Caddy, frontend (`next build`, standalone), backend + core-server (no hot reload, no bind mounts), Postgres, Redis, and a one-shot `auth-migrate`. Core only: no SeaweedFS (R2 instead), no analytics stack, only `career_agent` registered. Only Caddy publishes a port. |
| `Caddyfile` | TLS with the Cloudflare Origin certificate, `app.` → frontend, `api.` → backend with SSE flushing, and `404` for `/docs`, `/openapi.json`, `/info`, `/metrics`, `/noauth`. |
| `.env.example` | Every production setting, with the recommended limits filled in. |

The shared-deployment limits are env flags in the app itself, **off by default**, so a local clone keeps every capability (README "Shared deployment limits"):

| Flag | Local default | This deployment | Effect |
| --- | --- | --- | --- |
| `AUTH_EMAIL_PASSWORD_ENABLED` | `true` | `false` | Google-only login; Better Auth's email endpoints are disabled, not just hidden |
| `MODEL_OVERRIDE_ENABLED` | `true` | `false` | The backend ignores per-run model choices, so a direct API call cannot pick a pricier model; Settings hides the fields |
| `QUOTA_*` | `0` (off) | Part 2 | Per-user and global limits, `429` with the reset time, "N runs left today" in the composer |

The production compose file also sets `REQUIRE_AUTH=true`, `LANGGRAPH_AUTH`, `disable_mcp` / `disable_a2a`, and `CORS_ALLOW_ORIGINS` itself, so they are not in `.env`. R2 needs no code: `OBJECT_STORE_*` takes its endpoint, region `auto` and path-style addressing as-is.

## Part 2 — Sizing the quota for a $100 credit

Claude Sonnet 5.5 list prices: $2 / 1M input tokens, $10 / 1M output, $0.20 / 1M cache reads. The career agent caches well (86% of input tokens were cache reads in local history), so cost is driven by how many subagents a run fans out to, not by message count.

| Run type | Example | Estimated cost |
| --- | --- | --- |
| Light | follow-up question, small edit to a file | $0.02 – $0.05 |
| Medium | parse a CV + JD, one research pass | $0.15 – $0.40 |
| Heavy | full prep: research + tailored resume + interview prep + battlecard (4 subagents) | $1.00 – $2.50 |

These are estimates: local history ran on OpenAI models and its token counts exclude subagent calls, so treat them as a floor and re-check against the Anthropic Console usage page after the first week.

A run-count cap alone does not protect the credit, because one heavy run costs as much as 50 light ones. The recommended design caps both, and adds a global ceiling:

| Limit | Default | Env var | What it buys |
| --- | --- | --- | --- |
| Runs per user, rolling 24 h | 10 | `QUOTA_RUNS_PER_DAY` | Simple rule users understand (≈ 2 roles prepped per day) |
| Runs per user, rolling 7 days | 30 | `QUOTA_RUNS_PER_WEEK` | Stops daily maximums every day |
| Est. spend per user, rolling 24 h | $1.50 | `QUOTA_USD_PER_DAY` | About one full prep per day |
| Est. spend per user, rolling 7 days | $4.00 | `QUOTA_USD_PER_WEEK` | Caps a single user at ~4% of the credit per week |
| Est. spend, all users, rolling 24 h | $3.00 | `QUOTA_GLOBAL_USD_PER_DAY` | **The real guarantee: the $100 credit lasts at least ~33 days whatever users do** |
| Concurrent runs per user | 1 | — | No parallel fan-out from several tabs |

Quotas are checked when a run starts, so an in-flight run always finishes; the worst overshoot is one heavy run (~$2.50). Your proposed 50 runs/week is too generous on its own: 50 heavy runs is ~$100 from a single user.

Two more limits outside the app:

- **Anthropic Console spend limit** on a dedicated workspace (Part 3, step 5). The hard backstop if the app's meter is ever wrong.
- **Who can sign up.** With open Google sign-in, anyone with a Google account (or ten of them) shares your $3/day. Start as a closed beta: keep the Google OAuth app in **Testing** status, where only the test users you list (up to 100) can sign in. This needs no code. Publish the app to production later, once you want open sign-up (Part 3, step 4, "Open sign-up").

## Part 3 — Console setup, step by step

Do these in order. Keep a scratch note of every value marked **save**; they go into the server's `.env` in step 6.

### 1. Cloudflare R2 (artifact storage)

1. Cloudflare dashboard → **R2 Object Storage**. First time: accept the R2 plan (free tier: 10 GB storage, no egress fees).
2. **Create bucket** → name `next-role-artifacts` → Location: **Automatic**, with location hint **Asia-Pacific (APAC)** → Default storage class: Standard → **Create bucket**.
3. In the bucket → **Settings**: leave **Public access** disabled (R2.dev subdomain off, no custom domain). The backend reads and writes with API credentials; nothing is served publicly.
4. R2 overview → **Manage R2 API Tokens** (or *API* → *Manage API tokens*) → **Create API token**:
   - Permissions: **Object Read & Write**
   - Specify bucket(s): **Apply to specific buckets only** → `next-role-artifacts`
   - TTL: forever (rotate manually)
5. **Save** the *Access Key ID*, *Secret Access Key*, and the *S3 endpoint* `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. The secret is shown once.

### 2. Vultr VPS (Singapore)

1. **Account → SSH Keys → Add SSH Key**: paste your public key (`cat ~/.ssh/id_ed25519.pub`).
2. **Network → Firewall → Add Firewall Group** `next-role-web` with inbound IPv4 rules (and the same for IPv6):
   - SSH, TCP 22, source **My IP**
   - HTTPS, TCP 443, source **Cloudflare** (Vultr's built-in Cloudflare source list, so only Cloudflare can reach the origin)
   - Nothing else. Port 80 stays closed: TLS uses a Cloudflare Origin certificate (step 3), not ACME.
3. **Products → Deploy (+)**:
   - Type: **Cloud Compute – Shared CPU**, *Regular* or *High Performance*
   - Location: **Singapore**
   - Image: **Ubuntu 24.04 LTS x64**
   - Plan: **2 vCPU / 4 GB RAM** (≈ 80 GB SSD). The running stack needs ~1–1.5 GB; the headroom is for `next build` and WeasyPrint PDF renders. 2 GB is too tight to build the frontend.
   - Additional features: **Auto Backups** on (whole-disk daily snapshots, covers Postgres), **IPv6** on
   - SSH Keys: select yours. Firewall Group: `next-role-web`. Hostname: `next-role-prod`
   - **Deploy Now**. **Save** the public IPv4 (and IPv6).

### 3. Cloudflare DNS + TLS

1. Zone `next-role.dev` → **DNS → Records → Add record**, twice:
   - `A` `app` → VPS IPv4, **Proxied** (orange cloud)
   - `A` `api` → VPS IPv4, **Proxied**
   - Optional: matching `AAAA` records for the IPv6 address.
2. **SSL/TLS → Overview**: encryption mode **Full (strict)**.
3. **SSL/TLS → Origin Server → Create Certificate**: RSA (2048), hostnames `app.next-role.dev`, `api.next-role.dev`, validity 15 years → **Create**. **Save** the certificate and private key as `origin.pem` and `origin-key.pem` (the key is shown once).
4. **SSL/TLS → Edge Certificates**: **Always Use HTTPS** on, **Minimum TLS Version** 1.2.
5. Optional abuse guard: **Security → WAF → Rate limiting rules → Create rule** for `api.next-role.dev`, e.g. more than 100 requests per 10 s per IP → Block for 10 s. The in-app quota is the cost control; this only blunts floods.

Streaming works through the Cloudflare proxy: the agent server sends an SSE heartbeat every 5 s, well inside Cloudflare's 100 s idle timeout.

### 4. Google OAuth client

1. [Google Cloud Console](https://console.cloud.google.com/) → project picker → **New project** `next-role` → **Create**, then select it.
2. **Google Auth Platform → Branding** (first time: **Get started**): app name `NextRole`, support email, developer contact email. Optional: logo, home page `https://next-role.dev`.
3. **Audience**: User type **External**, Publishing status **Testing** → **Test users → Add users**: the Google emails of your beta users (max 100). This list is your allowlist (Part 2).
4. **Data Access**: scopes `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` only. They are non-sensitive, so no Google verification review is needed now or later.
5. **Clients → Create client** → Application type **Web application**, name `next-role-prod`:
   - Authorized JavaScript origins: `https://app.next-role.dev`
   - Authorized redirect URIs: `https://app.next-role.dev/api/auth/callback/google`
   - **Create** → **save** the *Client ID* and *Client secret*.

In Testing status users see a "Google hasn't verified this app" notice on first sign-in; that is expected for a closed beta. Publishing to production later requires a privacy policy URL on the consent screen (see Part 5).

#### Open sign-up (any Google account)

Better Auth creates the account on a user's first Google sign-in, so open sign-up is a Google-side change only: no code, `.env`, or redeploy. Do it at launch or any time later:

1. **Deploy the landing site** (`deploy/landing/`) so `https://next-role.dev/privacy` and `/terms` load and the home page footer links to both. Google's branding check fetches them.
2. **Branding**: home page `https://next-role.dev`, privacy policy `https://next-role.dev/privacy`, terms of service `https://next-role.dev/terms`, and `next-role.dev` under **Authorized domains**.
3. **Verify the domain** in [Google Search Console](https://search.google.com/search-console), signed in as an Owner or Editor of the Google Cloud project (the Cloudflare login email doesn't matter): **Add property → Domain** → `next-role.dev`, add the `google-site-verification=` TXT record on Cloudflare at `@`, **Verify**. Google asks for 24 hours after this before the next step.
4. **Audience → Publishing status → Publish app** (Testing → In production). The test-user list and its 100-user cap stop applying.
5. **Branding → Verify branding**. Until it passes, the consent screen shows the domain instead of `NextRole`; sign-in itself already works.

The scopes from step 4 are non-sensitive, so data access needs no verification; only the branding (name, links, and logo if you upload one) is reviewed. The user support email can only be the signed-in Google account or a Google Group it manages, so a forwarding-only address like `hello@next-role.dev` needs its own Google account ("Use your existing email" at sign-up) with Owner or Editor on the project before it can be picked.

With open sign-up one person can make several Google accounts, each with its own per-user quota. `QUOTA_GLOBAL_USD_PER_DAY` and the Console spend limit still protect the credit, but a drained global cap blocks everyone until the 24 h window rolls; if that happens, tighten the per-user `QUOTA_*` values rather than raising the global one. Don't open email/password sign-up (`AUTH_EMAIL_PASSWORD_ENABLED=true`) for this: there is no email verification, so anyone can register any address and script throwaway accounts.

### 5. Anthropic Console (the credit)

1. Confirm the credit is **API credit** on [console.anthropic.com](https://console.anthropic.com) → **Billing** (balance and expiry date). A Claude Max *subscription* is a separate product: its usage cannot back an API key, and signing a public app in with subscription credentials is against Anthropic's terms. If the $100 is not visible under Console Billing, sort that out before going further.
2. Note the credit's **expiry date**. Promotional credits often expire; if it lapses before it is spent, the quota is too strict and can be loosened.
3. **Settings → Workspaces → Create workspace** `next-role-prod`. Open it → **Limits**: set the monthly **spend limit** to `$100` (or lower). Calls beyond it fail instead of billing a card.
4. **API Keys → Create Key** in workspace `next-role-prod`, name `vultr-prod` → **save** it. Use this key only on the server, never your local dev key.
5. Optional: **Settings → Notifications**: usage alerts at 50% and 80%.

Also create production keys for **LlamaCloud** (document parsing) and **Tavily** or **TinyFish** (web research). Both have free monthly tiers; check their quotas against the run caps above (one heavy run uses several searches; every uploaded CV/JD is one parse).

### 6. Server bootstrap (SSH, ~15 minutes)

Everything else is UI; this part is a few commands. All `docker compose` commands run from the repo root, and Compose reads `deploy/vultr/.env` on its own.

```bash
ssh root@<VPS_IPV4>
```

```bash
# Updates, a 2 GB swap file (insurance during next build), unattended security upgrades
apt-get update && apt-get -y upgrade
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
apt-get install -y unattended-upgrades git
```

```bash
# Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sh
```

```bash
git clone https://github.com/tam159/next-role.git /opt/next-role
cd /opt/next-role
cp deploy/vultr/.env.example deploy/vultr/.env && chmod 600 deploy/vultr/.env
```

Put the Cloudflare Origin certificate from step 3 in place (the folder is gitignored):

```bash
mkdir -p /opt/next-role/deploy/vultr/certs && chmod 700 /opt/next-role/deploy/vultr/certs
nano /opt/next-role/deploy/vultr/certs/origin.pem       # paste the certificate
nano /opt/next-role/deploy/vultr/certs/origin-key.pem   # paste the private key
```

Fill `deploy/vultr/.env` (`nano deploy/vultr/.env`). Everything not listed keeps its `.env.example` value:

| Variable | Value |
| --- | --- |
| `APP_DOMAIN` / `API_DOMAIN` | `app.next-role.dev` / `api.next-role.dev` (the API URL is baked into the frontend at build time) |
| `ANTHROPIC_API_KEY` | key from step 5 |
| `LLAMA_CLOUD_API_KEY`, `TAVILY_API_KEY` or `TINYFISH_API_KEY` | production keys (`WEB_SEARCH_PROVIDER` picks one) |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `BETTER_AUTH_SECRET` | `openssl rand -base64 32` |
| `GOOGLE_AUTH_CLIENT_ID` / `GOOGLE_AUTH_CLIENT_SECRET` | from step 4 |
| `OBJECT_STORE_ENDPOINT` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `OBJECT_STORE_ACCESS_KEY` / `OBJECT_STORE_SECRET_KEY` | R2 token from step 1 |
| `QUOTA_*` | keep the Part 2 defaults; adjust later from real usage |
| `LANGCHAIN_TRACING_V2` | `false` unless you will actually read traces: they contain users' CV text |

Build and start, then create the Better Auth tables once:

```bash
cd /opt/next-role
docker compose -f deploy/vultr/docker-compose.yml up -d --build
docker compose -f deploy/vultr/docker-compose.yml run --rm auth-migrate
docker compose -f deploy/vultr/docker-compose.yml ps
```

All services should report `healthy` within about two minutes of the build finishing (the first build takes several minutes on 2 vCPUs). The backend applies its own migrations on boot; the `usage_ledger` table is created on the first run.

### 7. Smoke test

1. `https://app.next-role.dev` → the login page shows **only** "Continue with Google". Sign in with a test-user account; a Google account that is not on the test-user list must be refused by Google.
2. Upload a CV, ask for a short task. The file appears in Cloudflare → R2 → `next-role-artifacts` under `users/<id>/career_agent/…`, and a rendered PDF downloads.
3. The Configuration dialog shows no model fields. Optional direct check: a run created with `"configurable": {"main_agent_model": "anthropic:claude-opus-5-5"}` still answers with Sonnet 5.5 (Console usage shows only `claude-sonnet-5-5`).
4. `https://api.next-role.dev/docs` and `/metrics` return `404`; `/ok` returns `200`.
5. Quota: the composer footer shows "10 runs left today". Temporarily set `QUOTA_RUNS_PER_DAY=2` and run `docker compose -f deploy/vultr/docker-compose.yml up -d backend`, send messages until the composer locks with the reset time, then restore the value. To inspect the ledger: `docker compose -f deploy/vultr/docker-compose.yml exec postgres psql -U next-role -c "select owner, kind, model, cost_usd, created_at from usage_ledger order by id desc limit 20"`.
6. Sign in with a second test user and confirm it sees none of the first user's threads or files.

## Part 4 — Operating it

- **Deploy an update:** `cd /opt/next-role && git pull && docker compose -f deploy/vultr/docker-compose.yml up -d --build`. `.env` changes need `up -d <service>` (recreate), not `restart`; a flag the frontend bakes in at build time (`API_DOMAIN`, `AUTH_*_ENABLED`, `MODEL_OVERRIDE_ENABLED`) needs `up -d --build frontend`. After a Better Auth upgrade, bump the CLI version in the compose file and re-run `auth-migrate`.
- **One-time: Better Auth 1.7.2 → 1.7.3+.** A stack first started on 1.7.2 has a `NOT NULL` `account.issuer` column that 1.7.3+ never writes, so every sign-up fails until it is relaxed. Run the cleanup before the rebuild. `git pull` alone doesn't touch the running containers, and the script is safe while 1.7.2 still serves:

  ```bash
  cd /opt/next-role && git pull
  docker compose -f deploy/vultr/docker-compose.yml exec -T postgres pg_dump -U next-role \
    -t '"user"' -t account -t session -t verification -t jwks > ~/auth-tables-$(date +%F).sql
  docker compose -f deploy/vultr/docker-compose.yml exec -T postgres psql -U next-role -v ON_ERROR_STOP=1 -f - \
    < frontend/scripts/better-auth-1.7.3-relax-account-issuer.sql
  docker compose -f deploy/vultr/docker-compose.yml up -d --build
  docker compose -f deploy/vultr/docker-compose.yml run --rm auth-migrate
  ```

  Then sign up a new test user and sign in with an existing one.
- **Logs:** `docker compose -f deploy/vultr/docker-compose.yml logs -f backend` (or `frontend`, `core-server`).
- **Spend:** check the Anthropic Console usage page daily for the first week and compare with the ledger (`select sum(cost_usd) from usage_ledger where created_at > now() - interval '1 day'`); adjust `QUOTA_*` from real numbers. Quota changes need only `up -d backend`.
- **Backups:** Vultr Auto Backups cover the disk (Postgres included). Before risky upgrades take a manual snapshot (instance → **Snapshots → Take Snapshot**). R2 objects are not in the Vultr backup; they are durable on their own, but deletions are permanent.
- **Uptime:** a free external monitor (for example UptimeRobot or Better Stack) on `https://api.next-role.dev/ok` and `https://app.next-role.dev/`.
- **Adding beta users:** Google Auth Platform → **Audience → Test users → Add users**. Nothing to change on the server. To let anyone sign up instead, publish the app (Part 3, step 4, "Open sign-up").
- **Rotating a secret:** update `.env`, then `up -d` the services that read it. Rotating `BETTER_AUTH_SECRET` signs everyone out.

## Part 5 — Other points worth deciding before launch

- **Personal data.** Users upload CVs: names, contacts, work history. They reach your Postgres and R2, Anthropic, LlamaCloud (parsing), the web-research provider (queries only), and LangSmith if tracing is on. The privacy notice covering this (what is stored, which processors see it, how to get it deleted) is `deploy/landing/privacy.html`, served at `https://next-role.dev/privacy`; Google requires it to publish the app beyond Testing. Keep it in sync when you add a processor (for example turning on LangSmith tracing) or change what is stored.
- **Account deletion.** There is no self-serve delete. The privacy policy promises deletion within 30 days of an email to `hello@next-role.dev`; handle requests by hand: delete the user's threads, the `users/<id>/` prefix in R2, the store rows, and the Better Auth `user` row.
- **Credit run-out behaviour.** When the credit or the Console spend limit is reached, every run fails with an Anthropic billing error. The global quota should stop runs first, but check what the UI shows in that case.
- **Cost lever if the credit drains too fast.** Lower the reasoning effort for subagents before changing the model; caching is already high, and effort is the next biggest factor in Sonnet 5.5 spend.
- **Single box, single point of failure.** Acceptable for a beta. The upgrade path is managed Postgres plus a second app VPS, not Kubernetes.
- **Analytics later.** When the analytics stack comes back, run it on a separate box that reads Postgres through the `analytics_ro` role, so it cannot starve the app of RAM.
