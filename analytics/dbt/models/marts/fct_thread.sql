-- Grain: one row per conversation thread, with run rollups.
{{ config(engine='MergeTree()', order_by=['created_date', 'owner']) }}

with run_stats as (
    select
        thread_id,
        count() as run_count,
        countIf(status = 'success') as successful_runs,
        countIf(status = 'error') as failed_runs
    from {{ ref('stg_app__runs') }}
    group by thread_id
)

select
    t.thread_id,
    t.created_date,
    t.owner,
    t.graph_id,
    t.assistant_id,
    t.status,
    t.message_count,
    t.file_count,
    t.todo_count,
    t.has_interrupt,
    coalesce(r.run_count, 0) as run_count,
    coalesce(r.successful_runs, 0) as successful_runs,
    coalesce(r.failed_runs, 0) as failed_runs,
    t.created_at,
    t.updated_at
from {{ ref('stg_app__threads') }} as t
left join run_stats as r on t.thread_id = r.thread_id
