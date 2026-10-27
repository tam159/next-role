# RunLedger replay suite

Deterministic check for one career-agent tool trace. CI replays a cassette, so the run does not call a model and does not need an API key.

The recorded trace follows the resume path's research-before-write order:

1. `web_search` — the research tool in `backend/agents/career_agent/tools.py`. It dispatches to Tavily or TinyFish (`WEB_SEARCH_PROVIDER`). This repo has no `tavily_search` tool.
2. `write_file` — the tailored-resume YAML. Recording uses the same path check as deepagents' `write_file`, then `CompositeBackend.write` into the `/tailored_resume/` `ObjectStoreBackend` route. The store is in-memory (`obstore.store.MemoryStore`, the unit-test stand-in), so recording does not need SeaweedFS.

The YAML is normalized with the career agent's `_normalize_resume_yaml` and must validate as rendercv's `RenderCVModel` before it is stored in the cassette. The final output is that document (the YAML parsed to JSON, including `cv`, `design`, and `locale`, and no `settings` block).

## What fails the run

- **Tool order.** `call_order` requires `web_search` before `write_file`.
- **Resume schema.** The final output must match `schema.json`. That file is rendercv's schema from `rendercv.schema.json_schema_generator.generate_json_schema` (`RenderCVModel`). `required` is set to `["cv"]` because rendercv clears `required` so the same schema can describe a design-only file. Regenerate it with `export_schema.py` when the rendercv pin changes.
- **Tool-call budget.** `max_tool_calls` is 4. This trace uses 2 calls (search, then write). Four leaves room for one page extract and one extra read, and fails a tool loop.

Token and cost fields are not set. RunLedger accepts them in config and does not fail a run on them.

## Replay (same command as CI)

From the repo root:

```bash
uvx runledger==0.2.3 run ./evals/runledger --mode replay
```

## Re-record

Recording executes `web_search`, so the provider key has to be available (`TAVILY_API_KEY` when `WEB_SEARCH_PROVIDER` is `tavily`, the default, or `TINYFISH_API_KEY` for `tinyfish`). Replay does not use it.

From the repo root, after `uv sync` in `backend/`:

```bash
uv run --project backend python evals/runledger/export_schema.py
PYTHONPATH=. uv run --project backend --with runledger==0.2.3 \
  runledger run ./evals/runledger --mode record
uvx runledger==0.2.3 run ./evals/runledger --mode replay
```

`--mode record` overwrites `cassettes/tailored-resume.jsonl`. Commit that cassette and `schema.json` when the new trace should be the CI gate. `runledger_out/` is local run output; leave it untracked.
