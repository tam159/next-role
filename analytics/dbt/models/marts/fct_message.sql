-- Grain: one row per thread message — token/cost accounting. Event time is the
-- first-seen ledger (accurate to the pipeline cadence going forward; the
-- initial backfill lumps pre-deployment history at deploy date, so
-- thread_created_date is carried for backfill-aware bucketing). Cost = tokens
-- × the longest-matching pattern in the model_prices seed (argMax by pattern
-- length over a cross join — the seed is tiny).
{{ config(engine='MergeTree()', order_by=['first_seen_date', 'owner']) }}

with messages as (
    select * from {{ ref('stg_app__messages') }}
),

priced as (
    select
        m.message_key,
        argMax(p.input_usd_per_1m, length(p.model_pattern)) as input_usd_per_1m,
        argMax(p.output_usd_per_1m, length(p.model_pattern)) as output_usd_per_1m
    from messages as m
    cross join {{ ref('model_prices') }} as p
    where m.model_name like p.model_pattern
    group by m.message_key
)

select
    -- explicit aliases: these two also exist on the joined relations, and
    -- ClickHouse names ambiguous unaliased columns "m.message_key" verbatim
    m.message_key as message_key,  -- noqa: AL09
    m.thread_id as thread_id,  -- noqa: AL09
    m.message_index,
    m.message_id,
    m.type,
    m.name,
    m.model_name,
    m.model_provider,
    m.finish_reason,
    m.content_length,
    m.tool_call_count,
    m.tool_call_names,
    m.tool_call_id,
    m.tool_status,
    m.input_tokens,
    m.output_tokens,
    m.total_tokens,
    m.cache_creation_tokens,
    m.cache_read_tokens,
    m.reasoning_tokens,
    m.first_seen_at,
    assumeNotNull(toDate(m.first_seen_at)) as first_seen_date,
    coalesce(t.owner, 'default') as owner,
    t.created_date as thread_created_date,
    if(
        p.input_usd_per_1m is not null,
        (
            coalesce(m.input_tokens, 0) * p.input_usd_per_1m
            + coalesce(m.output_tokens, 0) * p.output_usd_per_1m
        ) / 1000000,
        null
    ) as est_cost_usd
from messages as m
left join {{ ref('stg_app__threads') }} as t on m.thread_id = t.thread_id
left join priced as p on m.message_key = p.message_key
