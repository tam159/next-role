{% macro source_relation_exists(source_name, table_name) %}
    {#- True when the bronze table behind a source actually exists.

        The Better Auth tables only exist after the one-time auth migration has
        run (see README "Authentication & multi-user"), so on a fresh checkout
        the raw_auth bronze tables may be absent. Staging models use this guard
        to degrade to an empty, correctly-typed relation instead of failing.
        At parse time (manifest builds) adapter calls return none — the guard
        only resolves during compile/run, which is exactly when it matters. -#}
    {%- if execute -%}
        {%- set src = source(source_name, table_name) -%}
        {%- set rel = adapter.get_relation(
            database=src.database, schema=src.schema, identifier=src.identifier
        ) -%}
        {{ return(rel is not none) }}
    {%- else -%}
        {{ return(true) }}
    {%- endif -%}
{% endmacro %}
