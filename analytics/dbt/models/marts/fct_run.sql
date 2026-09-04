-- Grain: one row per agent run — the core fact. steps/interrupt telemetry
-- comes from checkpoint metadata (max step per run).
{{ config(engine='MergeTree()', order_by=['run_date', 'owner']) }}

with steps as (
    select
        run_id,
        max(step) as max_step
    from {{ ref('stg_app__checkpoint_meta') }}
    where run_id is not null and run_id != ''
    group by run_id
)

select
    r.run_id,
    r.run_date,
    r.owner,
    r.thread_id,
    r.assistant_id,
    r.status,
    r.status = 'success' as is_success,
    r.status = 'interrupted' as was_interrupted,
    r.duration_s,
    coalesce(s.max_step + 1, 0) as steps,
    r.main_model_override,
    r.subagent_model_override,
    r.multitask_strategy,
    r.created_at,
    r.updated_at
from {{ ref('stg_app__runs') }} as r
left join steps as s on r.run_id = s.run_id
