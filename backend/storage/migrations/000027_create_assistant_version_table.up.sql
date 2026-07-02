create table if not exists assistant_versions (
    assistant_id uuid references assistant(assistant_id) on delete cascade,
    version integer not null default 1,
    graph_id text not null,
    config jsonb not null default '{}',
    metadata jsonb not null default '{}',
    created_at timestamptz default now(),
    primary key (assistant_id, version)
);