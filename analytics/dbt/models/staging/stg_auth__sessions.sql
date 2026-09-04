-- Login sessions (network identifiers coarsened at extract). Same existence
-- guard as stg_auth__users.
{% if source_relation_exists('raw_auth', 'session') %}
    select
        toString(id) as session_id,
        coalesce(toString(user_id), '') as user_id,
        ip_prefix,
        user_agent,
        created_at,
        updated_at,
        expires_at,
        assumeNotNull(toDate(created_at)) as session_date
    from {{ source('raw_auth', 'session') }}
{% else %}
    select
        '' as session_id,
        '' as user_id,
        cast(null as Nullable(String)) as ip_prefix,
        cast(null as Nullable(String)) as user_agent,
        cast(null as Nullable(DateTime64(6))) as created_at,
        cast(null as Nullable(DateTime64(6))) as updated_at,
        cast(null as Nullable(DateTime64(6))) as expires_at,
        toDate('1970-01-01') as session_date
    where 1 = 0
{% endif %}
