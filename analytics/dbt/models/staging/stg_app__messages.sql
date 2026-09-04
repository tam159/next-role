-- The message event-time ledger. Source messages carry NO timestamp, so this
-- incremental model stamps first_seen_at when a (thread_id, message_index)
-- first shows up — accurate to the pipeline cadence going forward; the initial
-- backfill lumps pre-deployment history at deploy time (fct_message carries
-- thread_created_date for backfill-aware bucketing). Insert-only append: rows
-- are never restated, which is the point of a ledger.
{{
    config(
        materialized='incremental',
        incremental_strategy='append',
        engine='MergeTree()',
        order_by=['thread_id', 'message_index'],
    )
}}

select
    toString(thread_id) as thread_id,
    assumeNotNull(message_index) as message_index,
    concat(toString(thread_id), ':', toString(message_index)) as message_key,
    message_id,
    type,
    name,
    model_name,
    model_provider,
    finish_reason,
    coalesce(content_length, 0) as content_length,
    coalesce(tool_call_count, 0) as tool_call_count,
    tool_call_names,
    tool_call_id,
    tool_status,
    input_tokens,
    output_tokens,
    total_tokens,
    cache_creation_tokens,
    cache_read_tokens,
    reasoning_tokens,
    thread_updated_at,
    now64(3) as first_seen_at
from {{ source('raw_app', 'message') }}
{% if is_incremental() %}
    where (toString(thread_id), assumeNotNull(message_index)) not in (
        select thread_id, message_index from {{ this }}
    )
{% endif %}
