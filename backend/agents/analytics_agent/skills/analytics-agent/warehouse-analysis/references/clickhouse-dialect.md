# ClickHouse dialect crib

Only what differs from the SQL most people write elsewhere.

## Aliases — the most common failure

**Never alias an expression to a name that is already a column of the table.**
This holds for every column, not just the one in the example below — and it has
to be checked on *every* aliased aggregate in the select list, since fixing only
the one named in the error just moves the failure to the next.

```sql
-- WRONG: every other mention of est_cost_usd now resolves to this alias, so the
-- countIf below wraps this aggregate -> code 184, ILLEGAL_AGGREGATION.
SELECT round(sumIf(est_cost_usd, type = 'ai'), 4) AS est_cost_usd,
       countIf(est_cost_usd IS NULL)              AS usage_without_cost
FROM nextrole_marts.fct_message

-- RIGHT: suffix the alias so it cannot shadow the column.
SELECT round(sumIf(est_cost_usd, type = 'ai'), 4) AS total_cost_usd,
       countIf(est_cost_usd IS NULL)              AS usage_without_cost
FROM nextrole_marts.fct_message
```

It fails regardless of which expression comes first — ClickHouse resolves alias
names across the whole select list, not top to bottom.

The same query with a different column behaves identically, which is the point:
the rule is about the shape `sum(x) AS x`, not about any particular column.

```sql
-- Also wrong, for the same reason.
SELECT sum(input_tokens)              AS input_tokens,
       countIf(input_tokens IS NULL)  AS missing_usage
FROM nextrole_marts.fct_message

-- Right.
SELECT sum(input_tokens)              AS total_input_tokens,
       countIf(input_tokens IS NULL)  AS missing_usage
FROM nextrole_marts.fct_message
```

Aliases are resolved in `GROUP BY`, `HAVING` and `ORDER BY`, which is convenient:

```sql
SELECT toStartOfWeek(run_date, 1) AS week, count() AS runs
FROM nextrole_marts.fct_run
GROUP BY week
HAVING runs > 5
ORDER BY week
```

But an aggregate's alias cannot appear in `WHERE` (code 184, "found in WHERE"):
`WHERE` filters rows before aggregation, `HAVING` filters after.

That error has **two causes and opposite fixes**, and they look identical:

```sql
-- Shadowing. WHERE means the column, but the alias captured the name.
-- Fix: rename the alias. Do NOT move this to HAVING — that filters on
-- min(first_seen_date) instead of on each row, and answers a different question.
SELECT min(first_seen_date) AS first_seen_date, count() AS n
FROM nextrole_marts.fct_message
WHERE first_seen_date >= '2026-09-01'

-- Genuine. `n` is not a column; you really are filtering on an aggregate.
-- Fix: move it to HAVING.
SELECT count() AS n FROM nextrole_marts.fct_message WHERE n > 0
```

Tell them apart by whether the alias is also a column: if it is, rename it.

## Names and functions

Function names are camelCase and case-sensitive: `toStartOfWeek`, `toStartOfDay`,
`countIf`, `uniqExact`, `argMax`. `COUNT_IF` and `count_if` are errors.

**Most standard SQL functions do work.** All of these are fine, so do not avoid
them: `DATE_TRUNC('week', d)`, `EXTRACT(DAY FROM d)`, `DATEDIFF('day', a, b)`,
`COALESCE`, `IFNULL`, `NOW()`, `count(DISTINCT x)`, `count(CASE WHEN … END)`.

The real trap is **inventing a `toXxx` name** by pattern-matching the family.
`toDayOfWeek` exists, so `toDayName` looks plausible — it does not exist, and you
get code 46, `UNKNOWN_FUNCTION`. When you want a function you have not actually
seen used here, check this file first.

| You might reach for | Use here |
| --- | --- |
| `toDayName(d)` | `dateName('weekday', d)` — or `formatDateTime(d, '%a')` for `Mon` |
| `toMonthName(d)` | `dateName('month', d)` |
| `STRING_AGG(x, ',')` | `arrayStringConcat(groupArray(x), ',')` |
| `PERCENTILE_CONT(...) WITHIN GROUP` | `quantile(0.95)(x)` |
| `x::int` casts | `toInt64(x)`, `toUInt64(x)`, `toFloat64(x)` |

Note `formatDateTime` supports `%a` (short day name) but **not** `%A`.

## Join and UNION key types

ClickHouse will not widen integer types for you. A join between `numbers()`
(which yields `UInt64`) and any arithmetic expression (`toDayOfWeek(d) - 1`
yields `Int16`) fails with code 386, `NO_COMMON_TYPE`:

```sql
-- WRONG: UInt64 on the left, Int16 on the right.
SELECT ...
FROM (SELECT number AS day_idx FROM numbers(7)) d
LEFT JOIN (SELECT toDayOfWeek(run_date) - 1 AS day_idx, count() AS n
           FROM nextrole_marts.fct_run GROUP BY day_idx) r
  ON d.day_idx = r.day_idx

-- RIGHT: cast the derived side to match.
LEFT JOIN (SELECT toUInt64(toDayOfWeek(run_date) - 1) AS day_idx, count() AS n
           FROM nextrole_marts.fct_run GROUP BY day_idx) r
  ON d.day_idx = r.day_idx
```

The same applies to the branches of a `UNION ALL`.

`ON` also needs a real join key: at least one plain equality between the two
sides. Computed expressions on either side are fine
(`ON toString(a.id) = toString(b.id)`), and you may add further conditions with
`AND`, but a function predicate or an inequality on its own gives ClickHouse
nothing to join on and fails with code 403:

```sql
-- WRONG: no determinable key.
... LEFT JOIN u ON has(array(r.owner), u.user_id)
... LEFT JOIN u ON r.owner != u.user_id

-- RIGHT: equality first, extra conditions alongside it.
... LEFT JOIN u ON r.owner = u.user_id AND u.provider = 'google'
```

**Before building a scaffold table with `numbers()`, ask whether you need it at
all.** Most join errors here come from generating a calendar or index table and
joining to it. Grouping the real column is simpler, has no type to reconcile,
and no key to determine. Scaffold only when the answer genuinely has to show
empty buckets.

## Looking up a schema

Use `describe_data('<table>')`. `information_schema` is **not** readable on this
connection, and `system.columns` / `DESCRIBE <table>` give names and types
without the column meanings and caveats that decide whether a number is right.

## Time

```sql
toStartOfDay(created_at)              -- DateTime -> day
toStartOfWeek(run_date, 1)            -- 1 = weeks start Monday
toDate(created_at)                    -- DateTime -> Date
dateDiff('second', created_at, updated_at)
today(), now(), subtractDays(today(), 7), subtractMonths(today(), 1)
```

Dates compare against string literals: `WHERE run_date >= '2026-09-01'`.

Note that presets like "last week" in BI tools exclude today. When a user asks
about "this week", say which days you included.

## Conditional aggregates

Preferred over `CASE WHEN` — shorter and faster:

```sql
countIf(status = 'error')                    AS errors,
countIf(status = 'success') / count()        AS success_rate,
avgIf(duration_s, status = 'success')        AS avg_ok_duration,
sumIf(est_cost_usd, model_name LIKE 'gpt%')  AS gpt_cost
```

## Percentiles

```sql
quantile(0.5)(duration_s)   AS p50,
quantile(0.95)(duration_s)  AS p95
```

Note the two argument lists: the quantile goes in the first, the column in the
second. `quantileExact` when precision matters more than speed.
`PERCENTILE_CONT (...) WITHIN GROUP (ORDER BY ...)` is a syntax error here.

## NULLs and joins

A `LEFT JOIN` miss fills a non-Nullable column with its type default — `0`,
`''`, `1970-01-01`, `false` — not NULL. When a miss must stay missing:

```sql
if(u.user_id = r.owner, u.signup_date, null) AS signup_date
```

`ifNull(x, 0)`, `coalesce(a, b)`, and `x IS NULL` all work as expected.

Aliasing a qualified column that also exists on the joined relation needs an
explicit alias, or the column is created literally as `m.col`:

```sql
SELECT m.message_key AS message_key   -- not just m.message_key
```

## Top N per group

```sql
SELECT owner, run_date, runs
FROM daily
ORDER BY owner, runs DESC
LIMIT 3 BY owner
```

## Arrays and strings

```sql
splitByChar(',', tool_call_names)     AS tools,
arrayJoin(tools)                      AS tool,     -- one row per element
length(content_summary)               AS chars,
lower(model_name) LIKE '%sonnet%'
```

## Things that will not work

- `FINAL` — illegal on plain `MergeTree`, and unnecessary here: bronze is
  deduplicated by the load's merge strategy and the marts are rebuilt whole.
- Writes of any kind. The connection has SELECT only.
- Cross-database joins outside `nextrole_marts` and `nextrole_staging`.
- Raising a limit with `SETTINGS`; the profile caps them. Lowering one works.
