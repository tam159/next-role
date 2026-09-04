-- One row per user the product has ever seen: every registered (Better Auth)
-- user plus any owner id that appears on runs/threads — including the
-- synthetic 'default' owner of single-user mode.
{{ config(engine='MergeTree()', order_by=['user_id']) }}

with auth_users as (
    select
        u.user_id,
        u.name,
        u.email_hash,
        u.email_domain,
        u.email_verified,
        u.signup_date,
        u.created_at as signup_at,
        a.provider
    from {{ ref('stg_auth__users') }} as u
    left join (
        select
            user_id,
            any(provider_id) as provider
        from {{ ref('stg_auth__accounts') }}
        group by user_id
    ) as a on u.user_id = a.user_id
),

all_owners as (
    select user_id from auth_users
    union distinct
    select owner as user_id from {{ ref('stg_app__runs') }}
    union distinct
    select owner as user_id from {{ ref('stg_app__threads') }}
)

select
    o.user_id,
    coalesce(u.name, if(o.user_id = 'default', 'Single-user mode', 'Unknown')) as name,
    u.email_hash,
    u.email_domain,
    coalesce(u.provider, if(o.user_id = 'default', 'none', null)) as provider,
    -- ClickHouse LEFT JOIN fills non-Nullable columns with type defaults
    -- (false / 1970-01-01) on a miss — guard them back to NULL explicitly.
    if(u.user_id = o.user_id, u.email_verified, null) as email_verified,
    if(u.user_id = o.user_id, u.signup_date, null) as signup_date,
    u.signup_at
from all_owners as o
left join auth_users as u on o.user_id = u.user_id
