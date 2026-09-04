-- Auth provider links (credential / google). Same existence guard as
-- stg_auth__users.
{% if source_relation_exists('raw_auth', 'account') %}
    select
        toString(id) as account_id,
        toString(user_id) as user_id,
        provider_id,
        created_at,
        updated_at
    from {{ source('raw_auth', 'account') }}
{% else %}
    select
        '' as account_id,
        '' as user_id,
        cast(null as Nullable(String)) as provider_id,
        cast(null as Nullable(DateTime64(6))) as created_at,
        cast(null as Nullable(DateTime64(6))) as updated_at
    where 1 = 0
{% endif %}
