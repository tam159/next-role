---
name: warehouse-analysis
description: Explore the NextRole ClickHouse warehouse and answer a question with SQL. Read this before writing your first query of a conversation — it carries the discovery loop and the ClickHouse syntax rules that differ from other dialects. Use for any question about product usage, activity, reliability, LLM cost, agent behaviour, users, runs, threads, or messages, and whenever a query errors, returns nothing, or returns a number that looks wrong.
---

# Warehouse analysis

## The loop

Work in small steps and let each one inform the next. Do not write a long query
against a table you have not inspected.

1. **Orient.** `describe_data()` once per conversation. It lists the marts, what
   one row of each means, how large they are, and the syntax rules below.
2. **Inspect.** `describe_data("<table>")` before querying a table for the first
   time. Read the sort key and the caveats; they decide both correctness and
   speed.
3. **Count.** `SELECT count() FROM <table> WHERE <your filter>` — a cheap check
   that your filter matches anything at all, before you build on it.
4. **Sample.** `SELECT * FROM <table> WHERE <filter> LIMIT 5`. Actual values
   settle what enums contain, how dates are shaped, and where NULLs live.
5. **Aggregate.** Now write the real query, with an explicit date window and an
   `ORDER BY`.
6. **Verify.** Check a surprising result from a second angle before reporting
   it: a different grain, a neighbouring week, or a total that should match.

## Writing queries that stay fast

- Always filter on the table's sort key — the date column on every fact.
- Always qualify names: `nextrole_marts.fct_run`.
- Select the columns you need; `SELECT *` on a fact wastes the scan.
- Aggregate in the warehouse. Do not pull rows out to count them.
- The connection is capped at 30 seconds and 10,000 rows. Hitting either means
  the query is too broad, not that the cap is wrong.

## ClickHouse syntax that differs from other dialects

Your memory carries the full list. The two that cause most failures:

- **Never alias to a name that is already a column.** For any column `x`,
  `sum(x) AS x` makes every other mention of that name resolve to the alias,
  nesting one aggregate inside another (code 184). Use `AS total_x`, and check
  every aliased aggregate in the select list, not just the first.
- **"found in WHERE" is usually that same alias problem**, not a `WHERE`/`HAVING`
  mistake: `min(d) AS d` with `WHERE d >= '...'` reports it, and the fix is to
  rename the alias. Only move a condition to `HAVING` when you really meant to
  filter on an aggregate — otherwise it silently filters the aggregate instead
  of each row.

Two more that have bitten in practice: do not invent a `toXxx` function name
(`toDayName` does not exist; day names come from `dateName('weekday', d)`), and
join keys must already share a type (`numbers()` is `UInt64`, so cast the
derived side).

Read `references/clickhouse-dialect.md` before writing anything whose syntax you
are not certain of — joins, percentiles, arrays, top-n-per-group, date
arithmetic. Reading it costs one file read; guessing costs a failed query, a
retry, and the user's time. Being confident about SQL you have not checked
against this reference is exactly how the failures above happen.

## Recovering from an error

The error text names the cause; read it before changing anything. If it is a
syntax or semantics error, read `references/clickhouse-dialect.md` before your
retry — a second guess usually fails the same way.

- **"found inside another aggregate function" / "found in WHERE"** (code 184) —
  an alias is shadowing a column, or an aggregate alias is used in `WHERE`. Rename
  the alias; do not restructure the query.
- **Unknown column or table** — you guessed a name. `describe_data` the table.
- **Timeout** — narrow the date window, filter on the sort key, or aggregate
  more aggressively.
- **Memory** — reduce `GROUP BY` cardinality, or aggregate in two stages.
- **Access denied** — you reached outside the marts and staging. The answer is
  not there; say so rather than trying another path to it.

## Marts or staging

Marts answer nearly everything and carry the documented semantics. Reach for
staging only when the question needs a field the marts do not carry — for
instance per-step agent telemetry in `stg_app__checkpoint_meta`. Staging is
typed and deduplicated but not described, so state the extra uncertainty when
you use it.

## Reporting

Give the number, its window, and the caveat, in that order. Prefer a specific
sentence ("47 runs failed in the last 7 days, 4.1% of 1,142") over a bare
figure. Show the SQL when the question is about methodology, not by default.

Save an analysis worth keeping to `/reports/<thread id>/<slug>.md`, embedding
its charts by path.

## References

- `references/clickhouse-dialect.md` — syntax that differs from other databases.
- `references/question-recipes.md` — worked queries for the common questions.
