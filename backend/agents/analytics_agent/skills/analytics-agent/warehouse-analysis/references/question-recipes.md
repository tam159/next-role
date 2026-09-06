# Question recipes

Starting points for the questions that come up most. Adapt the window and the
grouping; do not paste them blind. Every one was run against the warehouse.

## Reliability — how many runs failed this week?

`interrupted` is an approval pause, so it is excluded from both sides.

```sql
SELECT
    toStartOfWeek(run_date, 1)                       AS week,
    count()                                          AS runs,
    countIf(status IN ('error', 'timeout'))          AS failed,
    round(countIf(status IN ('error', 'timeout')) / count(), 4) AS failure_rate
FROM nextrole_marts.fct_run
WHERE run_date >= subtractDays(today(), 28)
GROUP BY week
ORDER BY week
```

## Failures by day, this week against last

```sql
SELECT
    run_date,
    countIf(status IN ('error', 'timeout')) AS failed,
    count()                                 AS runs
FROM nextrole_marts.fct_run
WHERE run_date >= subtractDays(today(), 14)
GROUP BY run_date
ORDER BY run_date
```

## Cost — estimated spend per model

A floor at list prices; say so alongside the number.

```sql
SELECT
    model_name,
    count()                       AS ai_messages,
    sum(total_tokens)             AS tokens,
    round(sum(est_cost_usd), 2)   AS est_usd
FROM nextrole_marts.fct_message
WHERE first_seen_date >= subtractDays(today(), 30)
  AND model_name IS NOT NULL
GROUP BY model_name
ORDER BY est_usd DESC
```

## Cost per day, split by model — the usual stacked chart

```sql
SELECT
    first_seen_date               AS day,
    model_name                    AS model,
    round(sum(est_cost_usd), 4)   AS est_usd
FROM nextrole_marts.fct_message
WHERE first_seen_date >= subtractDays(today(), 30)
  AND model_name IS NOT NULL
GROUP BY day, model
ORDER BY day, model
```

## Activity — active users per day

Activity means "ran the agent", not "logged in". Exclude the synthetic owner.

```sql
SELECT
    run_date                 AS day,
    uniqExact(owner)         AS active_users,
    count()                  AS runs
FROM nextrole_marts.fct_run
WHERE run_date >= subtractDays(today(), 30)
  AND owner != 'default'
GROUP BY day
ORDER BY day
```

## Latency — p50 and p95 run duration per week

End-to-end, including approval waits, and only for terminal runs.

```sql
SELECT
    toStartOfWeek(run_date, 1)          AS week,
    round(quantile(0.5)(duration_s), 1)  AS p50_s,
    round(quantile(0.95)(duration_s), 1) AS p95_s,
    count()                              AS runs
FROM nextrole_marts.fct_run
WHERE duration_s IS NOT NULL
  AND run_date >= subtractDays(today(), 56)
GROUP BY week
ORDER BY week
```

## Users — top users by runs and cost

```sql
SELECT
    u.name                                 AS user_name,
    countDistinct(r.run_id)                AS runs,
    round(sum(ifNull(m.est_cost_usd, 0)), 2) AS est_usd
FROM nextrole_marts.fct_run AS r
LEFT JOIN nextrole_marts.dim_user AS u ON u.user_id = r.owner
LEFT JOIN nextrole_marts.fct_message AS m ON m.thread_id = r.thread_id
WHERE r.run_date >= subtractDays(today(), 30)
GROUP BY user_name
ORDER BY runs DESC
LIMIT 20
```

The message join is at thread grain, so cost is per user, not per run. Do not
divide one by the other.

## Engagement — how many conversations become multi-turn?

```sql
SELECT
    toStartOfWeek(created_date, 1)  AS week,
    count()                         AS threads,
    countIf(run_count > 1)          AS multi_turn,
    round(countIf(run_count > 1) / count(), 3) AS multi_turn_share
FROM nextrole_marts.fct_thread
WHERE created_date >= subtractDays(today(), 56)
GROUP BY week
ORDER BY week
```

## Agent behaviour — which tools are used, and which fail?

```sql
SELECT
    name                                   AS tool,
    count()                                AS calls,
    countIf(tool_status = 'error')         AS errors
FROM nextrole_marts.fct_message
WHERE type = 'tool'
  AND first_seen_date >= subtractDays(today(), 30)
GROUP BY tool
ORDER BY calls DESC
```

## Approval friction — how often do runs pause for a human?

```sql
SELECT
    toStartOfWeek(run_date, 1)          AS week,
    countIf(status = 'interrupted')      AS interrupted,
    count()                              AS runs
FROM nextrole_marts.fct_run
WHERE run_date >= subtractDays(today(), 56)
GROUP BY week
ORDER BY week
```
