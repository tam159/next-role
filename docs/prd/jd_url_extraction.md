# PRD: JD-from-URL Extraction (v1)

**Status:** shipped · **Scope:** career_agent only · **Extends:** [Document Processing (v1)](document_processing.md)

## Why

Document Processing (v1) only handles **uploaded** CV/JD files. But many users have the JD as a careers-page URL (Amazon Jobs, Greenhouse, Lever, etc.), not a downloadable file. Without this feature, they'd have to copy-paste the JD into a doc and upload it. Goal: paste the URL, get the same `/processed/<slug>.md` artifact downstream steps already know how to read.

## What the user sees

User pastes a JD URL into chat ("Here's the JD I want to apply for: <url>") and hits Send:

1. Agent calls `extract_jd(url, save_as)` once. The tool fetches the page via Tavily (markdown format) and persists `/processed/<slug>.md`. Slug is content-meaningful — e.g. `amazon-senior-ai-solution-architect-jd`.
2. Agent replies with one short line naming the saved path. No markdown is dumped.

The processed file shows up in Workspace > Files alongside parsed uploads. Re-pasting the same URL with the same slug overwrites in place.

## How — the key architectural choices

**Separate tool, not an overload of `web_extract`.** Considered adding `offload_result_path: str | None` to the existing `web_extract`, but JD-from-URL has a clean 1-URL → 1-file contract that `web_extract`'s `list[str] | str` signature would muddle. A dedicated `extract_jd(url, save_as)` mirrors `parse_document(filename, save_as)` exactly — same factory shape (`make_extract_jd(backend)`), same `_upsert`, same `_strip_image_filenames`, same kebab-case slug convention. The system prompt becomes one extra paragraph parallel to the upload paragraph, and `web_extract` stays a clean general-purpose primitive for future research flows.

**Auto-offload is a no-op here.** deepagents' `FilesystemMiddleware` evicts any tool result over 80,000 chars to `/large_tool_results/<tool_call_id>` — opaque path, not what we want for a JD that downstream steps need to find by slug. We sidestep it by having the tool persist the markdown itself and return a short `Saved /processed/<slug>.md (<N> chars from <url>)` confirmation. The visible result is tiny → eviction never fires → the JD lands at the predictable `/processed/` path the agent and frontend already understand.

**Header + source line, then the body.** Tavily returns `raw_content` as full-page markdown including nav and footer. We prepend `# <title>\n\n_Source: <url>_\n\n` so the persisted file is self-describing — useful when downstream prompts (custom resume, interview prep) read it without context. The same `_strip_image_filenames` regex from `parse_document` is reused on the body to keep image refs from 404'ing in the Workspace preview.

## Files of interest

| Concern | Path |
|---|---|
| `extract_jd` factory + `_tavily_extract_one` helper | `backend/app/career_agent/tools.py` |
| Tool registration on the main agent | `backend/app/career_agent/agents.py` |
| URL-handling block (parallel to upload block) | `backend/app/career_agent/prompts.py` (`SYSTEM_PROMPT`) |
| Unit tests (Tavily mocked) | `backend/tests/career_agent/test_tools.py` |
| Tavily SDK | `tavily-python` (already in `backend/pyproject.toml`) |
| API key | `TAVILY_API_KEY` in `.env` |

## Decisions worth remembering

- **Tavily over scraping ourselves.** A Playwright-based scraper would handle auth-walled JDs better, but the cold-start latency, headless-browser maintenance, and per-site selector drift make it the wrong shape for v1. Tavily handles `extract_depth="advanced"` for JS-rendered pages out of the box and returns clean markdown.
- **`web_extract` stays unwired.** It's defined in `tools.py` but not registered on the main agent. Defer wiring until a research subagent or specific need lands — for now `extract_jd` is the only Tavily entrypoint the agent has, and that keeps the URL → `/processed/` flow unambiguous.
- **One URL per call.** No `urls: list[str]` overload. Multiple JDs at once → multiple parallel `extract_jd` calls, each with its own slug. Keeps the contract trivial and parallelism is the agent's job, not the tool's.
- **Validation in the tool, not the schema.** Pydantic could constrain `url` to `HttpUrl` and `save_as` to a regex, but that turns invalid input into a tool-call rejection the agent can't explain. We validate inside the function and return `Error: invalid url ...` / `Error: invalid save_as ...` so the model gets a readable message and can self-correct.

## Deferred (intentional non-goals for v1)

- **Auth-walled JDs.** LinkedIn job posts, internal Greenhouse boards, etc. Tavily can't see past the login wall. Out of scope; user can copy-paste into a `.md` and upload it as a fallback.
- **Pre-flight URL preview.** No "this looks like a JD at <company>, proceed?" confirmation. The slug picked by the LLM gives the user enough signal in the reply.
- **Per-site cleanup of nav/footer noise.** `raw_content` includes amazon.jobs sidebars, "Recommended jobs" etc. Downstream prompts handle the noise fine; trimming would be premature.
- **Multi-URL batching in one call.** See "One URL per call" above.

## How to verify end-to-end

1. `docker compose up -d` and confirm `TAVILY_API_KEY` is set in `.env`.
2. Send a chat message: `Process this JD: https://www.amazon.jobs/en/jobs/3195366/senior-ai-solution-architect`.
3. Watch the LangGraph trace:
   - One `extract_jd` call with a sensible `save_as` (e.g. `amazon-senior-ai-solution-architect-jd`).
   - **No** `web_extract` or `write_file` calls.
   - Brief assistant reply naming the saved path.
4. Open Workspace > Files: `/processed/<slug>.md` appears, header line is `# <JD title>`, second block is `_Source: <url>_`.
5. Query the langgraph store via the `next-role-postgres` MCP — confirm a row exists under namespace `("career_agent", "processed")` with the expected key.
6. **Overwrite case.** Re-paste the same URL. Tool returns the same `Saved …` confirmation; no duplicate row in the store.
7. **Tests.** `cd backend && uv run pytest tests/career_agent/test_tools.py` — green; Tavily stays mocked.
