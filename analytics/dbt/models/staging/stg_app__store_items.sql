-- Long-term memory inventory. prefix is `<user>.career_agent.<area>` in
-- multi-user mode and `career_agent.<area>` single-user; ClickHouse arrays are
-- 1-indexed, so a 3-part prefix carries the user in part 1 and the area last.
with parsed as (
    select
        prefix,
        key,
        created_at,
        updated_at,
        coalesce(value_length, 0) as value_length,
        splitByChar('.', assumeNotNull(prefix)) as parts
    from {{ source('raw_app', 'store_item') }}
)

select
    prefix,
    key,
    if(length(parts) >= 3, parts[1], 'default') as owner,
    parts[length(parts)] as area,
    value_length,
    created_at,
    updated_at
from parsed
