-- One row per conversation thread; the values body was reduced to counts at
-- extract time, so this is a pure rename/cast layer.
select
    toString(thread_id) as thread_id,
    coalesce(owner, 'default') as owner,
    graph_id,
    toString(assistant_id) as assistant_id,
    status,
    coalesce(message_count, 0) as message_count,
    coalesce(file_count, 0) as file_count,
    coalesce(todo_count, 0) as todo_count,
    coalesce(has_interrupt, false) as has_interrupt,
    created_at,
    updated_at,
    state_updated_at,
    assumeNotNull(toDate(created_at)) as created_date
from {{ source('raw_app', 'thread') }}
