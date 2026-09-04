-- One row per agent run (bronze is deduped by dlt's merge disposition).
-- duration_s only makes sense once a run reached a terminal status; it
-- approximates created_at → last update.
select
    toString(run_id) as run_id,
    toString(thread_id) as thread_id,
    toString(assistant_id) as assistant_id,
    coalesce(owner, 'default') as owner,
    status,
    multitask_strategy,
    main_model_override,
    subagent_model_override,
    created_at,
    updated_at,
    assumeNotNull(toDate(created_at)) as run_date,
    if(
        status in ('success', 'error', 'timeout', 'interrupted'),
        dateDiff('second', created_at, updated_at),
        null
    ) as duration_s
from {{ source('raw_app', 'run') }}
