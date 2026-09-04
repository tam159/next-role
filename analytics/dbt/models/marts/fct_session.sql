-- Grain: one row per login session (Better Auth). Empty until the auth
-- migration + first logins exist; DAU-by-product-activity comes from fct_run.
{{ config(engine='MergeTree()', order_by=['session_date', 'user_id']) }}

select
    session_id,
    session_date,
    user_id,
    ip_prefix,
    user_agent,
    created_at,
    expires_at
from {{ ref('stg_auth__sessions') }}
