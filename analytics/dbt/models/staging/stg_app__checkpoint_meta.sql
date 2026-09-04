-- Checkpoint step telemetry: one row per LangGraph checkpoint, metadata keys
-- whitelisted at extract. ts comes from the checkpoint body (ISO string in
-- Postgres, typed to a timestamp by dlt at load).
select
    toString(thread_id) as thread_id,
    checkpoint_ns,
    toString(checkpoint_id) as checkpoint_id,
    toString(run_id) as run_id,
    ts as checkpoint_ts,
    source,
    step,
    graph_id,
    toString(assistant_id) as assistant_id,
    coalesce(owner, user_id, 'default') as owner
from {{ source('raw_app', 'checkpoint_meta') }}
