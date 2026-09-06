# The NextRole warehouse

Loaded every turn. `describe_data` has the full column dictionary; this is what
is true before you ask.

## Layers

| Database | What it is |
| --- | --- |
| `nextrole_marts` | Gold. Star schema around `fct_run`. Start here. |
| `nextrole_staging` | Silver. Typed, deduplicated entities, one per source table. Drill-downs only. |
| `nextrole` | Bronze. Not readable, by design — raw load metadata and hashed identifiers. |

If the marts and staging do not carry something, it is not available to you: say so rather than
probing bronze. Those queries are denied and only cost a round trip.

Refreshed hourly by Dagster (dlt extract → dbt build), so "today" is complete only up to the last run.

## Marts

| Table | One row is | Time column |
| --- | --- | --- |
| `fct_run` | One agent run: a user turn, or a resume after an approval pause | `run_date` |
| `fct_message` | One message in a conversation: a user turn, a model turn, or a tool result | `first_seen_date` |
| `fct_thread` | One conversation, with message and run roll-ups | `created_date` |
| `fct_session` | One login session (registered users only) | `session_date` |
| `dim_user` | One user, including the synthetic `default` owner | `signup_date` |
| `dim_assistant` | One agent definition (graph and version) | — |
| `model_prices` | Per-1M-token list prices behind `est_cost_usd` | — |

Facts are `ORDER BY (<date>, owner)`. Filtering on the date column is what keeps a query fast.

## Caveats that change what a number means

- **`owner = 'default'`** is single-user mode and all pre-auth history, not a person. Exclude it from
  per-user questions, or treat it as one anonymous account.
- **`status = 'interrupted'`** is a pause for human approval, not a failure. The resume arrives as a
  *new* run on the same thread. Reliability is `success` against `error` and `timeout`.
- **`fct_thread.status`** is the thread's state right now, not history. Never sum it over time; use
  `fct_run.status` for that.
- **Messages are not a usage metric.** Tool results outnumber user turns roughly five to one. Count
  `type = 'human'` messages, runs, or threads instead.
- **`first_seen_at` is a capture time, not a send time.** Source messages carry no timestamp, so the
  pipeline stamps them when it first sees them: accurate to the hour going forward, but everything
  from before the pipeline existed shares one backfill timestamp. Bucket older history by
  `thread_created_date`.
- **`est_cost_usd` is an estimate at list prices.** Tokens times the `model_prices` seed. Cache
  discounts and reasoning-token rates are not modelled. It is a floor, never an invoice.
- **`duration_s` is end-to-end.** It includes queue wait and any time a run sat waiting for a human
  approval, so it is what the user felt, not model latency. Only set once a run is terminal.
- **`steps`** counts LangGraph steps across the main graph and its subagents. A chat is a handful; a
  resume tailoring is 100+. Zero means the checkpoint telemetry is missing, not that nothing ran.

## Columns that are always NULL

`fct_message.finish_reason`, `fct_run.main_model_override`, `fct_run.subagent_model_override`.
Filtering on them silently returns nothing. Model usage lives on `fct_message.model_name`.

## Writing SQL here — the rules that cause errors

These are in memory rather than in a skill because every query needs them. Follow them as written;
do not reason from other SQL dialects.

- **Never alias an expression to a name that is already a column.** For *any* column `x`,
  `sum(x) AS x` makes every other mention of `x` in that SELECT resolve to the alias, so the next
  aggregate wraps this one and ClickHouse fails with *"aggregate function ... is found inside
  another aggregate function"* (code 184). Suffix the alias instead: `AS total_x`. Check **every**
  aliased aggregate in the select list, not only the one you happened to think of —
  `sum(input_tokens) AS input_tokens` and `sum(est_cost_usd) AS est_cost_usd` fail identically.
  This bites regardless of clause order.
- **"found in WHERE" is usually the same alias problem.** `min(d) AS d` plus `WHERE d >= '...'`
  reports *"aggregate function ... is found in WHERE"*, but the fix is still to rename the alias —
  the `WHERE` was referring to the column. Moving that condition to `HAVING` makes it run and
  quietly answer a different question, because `HAVING` filters the aggregate rather than each row.
  Only move to `HAVING` when you genuinely meant to filter on an aggregate (`count() AS n` then
  `WHERE n > 0`). Aliases are fine in `GROUP BY`, `HAVING` and `ORDER BY`.
- **Percentiles are `quantile(0.95)(duration_s)`.** `PERCENTILE_CONT ... WITHIN GROUP` is a syntax
  error.
- **Function names are camelCase and case-sensitive**: `toStartOfWeek`, `countIf`, `uniqExact`,
  `subtractDays`. `count_if` and `COUNT_IF` do not exist.
- **`LEFT JOIN` misses fill non-Nullable columns with type defaults** (`0`, `1970-01-01`, `false`),
  not NULL. When a miss must stay missing: `if(j.key = b.key, j.col, null)`.
- **Never `FINAL`.** Illegal on these plain `MergeTree` marts, and unnecessary.
- **Do not invent a `toXxx` function.** `toDayOfWeek` exists, so `toDayName` looks plausible — it
  does not exist. Day and month names are `dateName('weekday', d)` and `dateName('month', d)`.
  Standard SQL functions mostly *do* work here (`DATE_TRUNC`, `EXTRACT`, `DATEDIFF`, `COALESCE`,
  `IFNULL`, `NOW`), so use those rather than guessing a ClickHouse-looking name.
- **Prefer grouping the real rows to building a scaffold.** Generating a calendar or index table
  with `numbers()` and joining to it is where most join errors come from. Group by the real column
  first; only scaffold when the answer genuinely needs empty buckets shown.
- **Joins are strict.** `ON` needs a plain equality between the two sides (a computed expression on
  either side is fine, but a function predicate or an inequality is not — those belong in `WHERE`).
  And the key types must already match: `numbers()` yields `UInt64` while `toDayOfWeek(d) - 1`
  yields `Int16`, so cast one side, `toUInt64(...)`.
- **Schemas come from `describe_data`**, not `information_schema` (not readable here). Only that
  tool carries the column meanings and caveats that decide whether a number is right.

Conveniences worth knowing: conditional aggregates (`countIf(status = 'error')`, `avgIf(...)`) read
better than `CASE`; `LIMIT n BY <expr>` gives top-n per group; dates compare against string literals
(`run_date >= '2026-09-01'`); `today()`, `now()` and `subtractDays(today(), 7)` build relative
windows — always state the window in your answer.

Anything not covered here: read `references/clickhouse-dialect.md` in the `warehouse-analysis`
skill **before** guessing. A wrong guess costs a full round trip; the reference costs one read.
