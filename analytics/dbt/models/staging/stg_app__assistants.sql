-- Agent definitions (small dimension source).
select
    toString(assistant_id) as assistant_id,
    graph_id,
    name,
    description,
    version,
    created_at,
    updated_at
from {{ source('raw_app', 'assistant') }}
