-- Agent-definition dimension: joins runs to graph/agent versions.
{{ config(engine='MergeTree()', order_by=['assistant_id']) }}

select
    assistant_id,
    graph_id,
    name,
    description,
    version,
    created_at,
    updated_at
from {{ ref('stg_app__assistants') }}
