---
name: upgrade-frontend-deps
description: Upgrade the Next.js frontend's pnpm dependencies to their latest versions in verified waves. Inventories with `pnpm outdated`, removes verified-unused deps, batches safe minor/patch bumps, handles the @langchain/react + langgraph-sdk lockstep pair, takes majors one commit at a time with registry peer-dep checks, gates every wave on type-check + lint + build + an agent-browser check against baseline screenshots, then dedupes, audits for duplicate versions, and opens a PR. Use when the user says "upgrade frontend libs", "bump frontend deps", "update npm/pnpm packages", or `pnpm outdated` shows a backlog. Backend deps are a separate flow (`upgrade-backend-deps`).
---

Upgrade the frontend's pnpm dependencies in revertable waves, preserving exact-pin style and the single-resolved-version invariant, with browser verification against a pre-upgrade baseline.

## Ground rules

- **Exact pins stay exact.** `react`, `react-dom`, and `@langchain/langgraph-sdk` have no `^` in `package.json`. Upgrade them with `pnpm --dir frontend add -E pkg@x.y.z`; everything else gets a new `^` floor (`pnpm --dir frontend add pkg@^x.y.z`).
- **One resolved version per dep.** The lockfile must not carry two copies of anything that crosses a package boundary (the `@langchain/langgraph-sdk` single-copy invariant in `frontend/CLAUDE.md` is the critical one; `scripts/check-langchain-sdk-sync.mjs` guards it).
- **One commit per wave** (majors: one per package) so any regression reverts surgically.
- **Lockfile changes need `docker compose restart frontend`; source edits hot-reload.** Never restart for source-only changes; never skip the restart after `pnpm add/remove`.
- **`@types/node` tracks the Docker runtime**, not npm `latest`. Read the major from `frontend/Dockerfile`'s `FROM node:XX-alpine` and stay on `@types/node@^XX`. Document it under "Held back".

## Workflow

### 1. Inventory

```bash
pnpm --dir frontend outdated --format json
```

Exits 1 when anything is outdated — that's data, not an error. Split the list into: patch/minor (wave 3), majors (waves 5–7), and the LangChain pair (wave 4). For every **major**, check compatibility before planning:

```bash
pnpm view "pkg@<target>" peerDependencies   # quote the spec; zsh doesn't word-split vars
```

Gate examples that have mattered: typescript-eslint's `typescript: <X` upper bound gates TS majors; eslint plugins' `eslint` peer range gates eslint majors. A major whose peers don't fit gets held back, not forced.

### 2. Branch + baseline

```bash
git switch -c chore/upgrade-frontend-deps
cd frontend && pnpm install --frozen-lockfile   # host node_modules current (pre-commit hooks need it)
pnpm type-check && pnpm lint && pnpm build      # "Gate G" — must be green BEFORE touching anything
```

Record the current lint warning count — you'll need to distinguish pre-existing warnings from upgrade fallout later.

Then capture a browser baseline with **agent-browser** (`agent-browser skills get core` for the workflow):

- Screenshots, light AND dark (`agent-browser set media dark|light`): home, a thread with markdown + code blocks, the file dialog open, the select dropdown open, `/print/file`.
- One full E2E run: send a real job-prep prompt, wait for completion, confirm streaming, subagent cards, plan progress, and workspace artifacts. To submit a **multiline** prompt (Enter sends in this UI), set the textarea via the React native-setter instead of typing:

```bash
cat <<'EOF' | agent-browser eval --stdin
{
  const el = document.querySelector('textarea');
  const set = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  set.call(el, `multi
line
prompt`);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}
EOF
agent-browser find role button click --name "Send"
```

- Completion signal: poll until `agent-browser snapshot -i -c` no longer shows `button "Stop"` (background loop, ~30s interval, run-in-background so you're notified).
- `/print/file` reads a JSON payload from `sessionStorage["nextrole:print-file"]` — set a fixed synthetic markdown payload (heading, list, blockquote, fenced code, table) and reuse the **same** payload post-upgrade for an apples-to-apples diff.

### 3. Wave: removals, then minor/patch batch

If any dep looks unused, **verify empirically** (grep imports across `src/` AND config files — a dep can be config-only like a tailwind plugin), confirm with the user if it wasn't already agreed, remove, and commit separately.

Then bump all patch/minors in one command with the new versions as explicit floors, exact pins via `-E`:

```bash
pnpm --dir frontend add pkgA@^x.y.z pkgB@^x.y.z ...
pnpm --dir frontend add -E react@x.y.z react-dom@x.y.z
pnpm --dir frontend add -D devPkg@^x.y.z ...
```

Gate G + restart + a quick browser smoke (page loads, open an existing thread). Commit.

### 4. Wave: @langchain/react + langgraph-sdk lockstep

Follow the protocol in `frontend/CLAUDE.md` exactly: bump `@langchain/react` first, read its pinned SDK version (`pnpm view "@langchain/react@<ver>" dependencies`), then `pnpm --dir frontend add -E @langchain/langgraph-sdk@<that-version>`, then:

```bash
pnpm --dir frontend run check:sdk-sync   # must print "single copy"
```

This wave touches the streaming layer — run the **full E2E** (not just a smoke): streaming tokens, subagent cards populating live, then reload the idle thread and submit again (catches replay/resume regressions). Commit.

### 5. Wave: independent majors, one commit each, cheapest first

For each major: read the changelog/release notes, but treat the **installed package's `.d.ts` as ground truth** for API shape — and verify semantics empirically when the types are ambiguous. Real example: react-resizable-panels v4's d.ts doesn't say bare numeric sizes became **pixels** (v3: percent); only a browser check caught panels collapsing to 30px. Research can be wrong in both directions — a claim that "numbers still mean %" survived two reviews here.

Per package: `pnpm add`, make any source edits, Gate G, targeted browser check of the surfaces that package owns (markdown → chat bubbles + dialogs; highlighter → code blocks light/dark; icons → render pass; panels → drag, reload-persistence, sidebar toggle, console clean), commit. One restart at wave end is fine if no package needed an interactive check earlier.

### 6. Wave: toolchain (eslint / typescript)

Dev-only but loud. Expect **new findings, not breakage**: new eslint core rules and react-hooks compiler rules flag pre-existing patterns. Policy: fix trivial ones; downgrade noisy new rules to `"warn"` in `eslint.config.js` with a comment — do **not** refactor app logic inside a deps PR. For TS majors, check `tsconfig` fallout (TS 6 dropped auto-inclusion of `node_modules/@types` → needed `"types": ["node"]`). Gate G, commit.

### 7. Wave: coupled/risky majors last (own commits = revert lever)

Anything that rewires a build pipeline (CSS framework, bundler plugin) goes last, in two commits: codemod output, then manual fixes. Two lessons that generalize:

- **A green build does not mean correct output.** After pipeline changes, grep the *emitted* artifact for invariants, e.g. compiled CSS must contain the expected utility rules. Minified CSS gotchas: `grep -c` counts lines (minified = 1 line — use `grep -o | wc -l`), minifiers merge selectors (`.a,.b{...}` — don't anchor on `.cls{`), and attribute quotes get stripped (`[type=checkbox]`, not `[type='checkbox']`).
- **"Now it finally works" can be a regression.** Classes/config that were silently dead under the old version (never emitted CSS) may come alive after the upgrade and *change* rendering. The pre-upgrade baseline screenshots are the arbiter — match the baseline, not the docs' idea of correct. Tailwind-4-specific landmines for this repo are recorded in `tailwind.config.mjs` / `globals.css` comments and the PR #16 description (prose is an intentional no-op; element CSS stays in `@layer base`; packaged plugins load via `@plugin` lines, not the `@config` plugins array).

Full visual checklist vs baseline (light + dark, dialogs, selects, hover, print page), plus a cold-build check: `rm -rf frontend/.next && pnpm --dir frontend build` and grep the emitted CSS.

### 8. Finalize

```bash
pnpm --dir frontend dedupe
pnpm --dir frontend run check:sdk-sync          # dedupe must not split the SDK copy
pnpm --dir frontend outdated                    # expect: empty, or only documented hold-backs
cd frontend && pnpm quality && pnpm build
docker compose restart frontend
pnpm --dir frontend why clsx react react-dom @langchain/langgraph-sdk   # one version each
```

Run the **full E2E one last time** on the final state, then commit the dedupe diff if any.

### 9. PR

Push as `tam159` (`gh auth switch --user tam159` first for gh; `git push` already uses the right SSH identity), open the PR with `mcp__github__create_pull_request` (owner `tam159`, repo `next-role`, base `main`). Body follows the PR #15/#16 format: **Summary / Upgraded table (old → new, mark exact pins) / Removed / Held back (with reasons) / Migration notes / Test plan**. Watch `code-quality` + `backend-tests`; fix failures in the same PR. Leave merging to the user.

### 10. Report

- **Upgraded:** direct deps with old → new (the wave commits are the ledger).
- **Removed:** with the verification evidence (zero imports / config-only / dead config).
- **Held back:** package, available version, and the concrete blocker (peer range, runtime match, failed gate + revert).
- Surprises that cost time — they're the seed of the next CLAUDE.md/skill update.

## Gotchas

- **`pnpm outdated` exits 1 when outdated packages exist** — don't treat as failure.
- **Pre-commit reformats, then fails the commit.** prettier (+ class sorting) rewrites codemod/sed output on first commit attempt → "files were modified by this hook". Re-stage and commit again; second pass is clean. After any hook auto-fix, re-read files before further edits.
- **Browser console/errors accumulate across container restarts.** A scary `CssSyntaxError` or missing-module error may be from the window when the container was mid-restart with mismatched node_modules. `agent-browser errors --clear` + reload before judging; only fresh-after-reload errors are real.
- **agent-browser refs go stale on re-render** — re-snapshot before clicking; `scrollintoview` before clicking elements outside the viewport (clicks can silently no-op).
- **Screenshots save relative to the daemon's cwd, not the shell's** — always pass absolute paths (`agent-browser screenshot /tmp/...png`), or strays land in the repo.
- **pnpm 10 blocks postinstall scripts of new deps** (e.g. `@tailwindcss/oxide`). If pnpm warns "Ignored build scripts", allowlist via `pnpm.onlyBuiltDependencies` and reinstall.
- **The E2E creates real threads/files in the dev stack** — they overwrite by deterministic path on re-runs; leave or bulk-delete at the end, but say which.
