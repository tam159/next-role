---
name: commit
description: Stage current changes and create a Conventional Commits-style commit matching this repo's history. Use when the user says "commit", "commit this", or asks for a commit message. Pre-commit hooks will run automatically.
disable-model-invocation: true
---

Create a single commit using Conventional Commits format, matching the style in `git log`.

## Workflow

1. Run these in parallel:
   - `git status` (see what's untracked / modified)
   - `git diff` and `git diff --staged` (see what will be committed)
   - `git log --oneline -10` (match recent style)

2. Review the changes and group them into ONE commit. If the diff spans clearly unrelated changes, ask the user whether to split.

3. Stage explicitly (avoid `git add -A` / `git add .`):
   - Add the specific files that belong in this commit by name.
   - Never stage `.env`, credentials, or other secrets. If `gitleaks` would flag it, don't stage it.

4. Compose the commit message:
   - Subject: `<type>: <short imperative summary>` — lowercase, no trailing period, ≤72 chars.
   - Types in use here: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`.
   - Body (optional): explain the **why**, not the **what**. Wrap at 72 chars.

5. Commit with a HEREDOC so formatting survives:

   ```bash
   git commit -m "$(cat <<'EOF'
   feat: <subject>

   <optional body>
   EOF
   )"
   ```

6. Pre-commit will run automatically. If it modifies files (auto-fix), re-stage and create a NEW commit — do not amend unless the user asks. If hooks fail with errors, fix the underlying issue and create a new commit; never bypass with `--no-verify`.

7. After the commit succeeds, run `git status` to confirm a clean tree and report the commit SHA + subject.

## Do not

- Push to the remote unless the user asks.
- Add a `Co-Authored-By` trailer unless the user has asked for one (this repo's history doesn't use them).
- Use `--amend` unless explicitly requested.
