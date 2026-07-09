---
name: visual-verification
description: Verify UI behavior visually in the running Next.js app. Use after frontend UI, styling, layout, interaction, file-upload, or user-flow changes, or when the user asks to inspect the app in a browser.
---

# Visual Verification

Use browser automation when available; otherwise fall back to `curl` or screenshots from the active toolset.

## Workflow

1. Check whether the local stack is already running with `docker ps`.
2. Read the host port from the running container mapping instead of assuming defaults.
3. Open the frontend at `http://localhost:<FRONTEND_LOCAL_PORT>/`.
4. Exercise the changed workflow, not just page load.
5. Check desktop and mobile widths for layout-sensitive changes.
6. Inspect console and network failures when browser tooling exposes them.
7. Report what was verified and any residual risk.

## Stack Handling

If the stack is not running or a service connection fails, remind the user to run:

```bash
docker compose up -d
```

Do not start or restart the whole stack unless the user explicitly asks. Plain frontend/backend source edits hot-reload.

## Useful URLs

- Frontend UI: `http://localhost:<FRONTEND_LOCAL_PORT>/`
- Backend API docs: `http://localhost:<LANGGRAPH_LOCAL_PORT>/docs`
