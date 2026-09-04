-- Registered users (pseudonymized at extract). Guarded: the Better Auth tables
-- only exist after the one-time auth migration, so a fresh checkout gets an
-- empty, correctly-typed relation instead of a broken view.
{% if source_relation_exists('raw_auth', 'user') %}
    select
        toString(id) as user_id,
        name,
        email_hash,
        email_domain,
        coalesce(email_verified, false) as email_verified,
        created_at,
        updated_at,
        assumeNotNull(toDate(created_at)) as signup_date
    from {{ source('raw_auth', 'user') }}
{% else %}
    select
        '' as user_id,
        cast(null as Nullable(String)) as name,
        cast(null as Nullable(String)) as email_hash,
        cast(null as Nullable(String)) as email_domain,
        false as email_verified,
        cast(null as Nullable(DateTime64(6))) as created_at,
        cast(null as Nullable(DateTime64(6))) as updated_at,
        toDate('1970-01-01') as signup_date
    where 1 = 0
{% endif %}
