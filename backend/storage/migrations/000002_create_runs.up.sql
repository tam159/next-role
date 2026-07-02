create table if not exists run (
    run_id uuid primary key default gen_random_uuid(),
    thread_id uuid not null references thread(thread_id),
    assistant_id uuid not null references assistant(assistant_id),
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    metadata jsonb not null default '{}',
    status text not null default 'pending'
);


create table if not exists run_event (
    event_id uuid primary key default gen_random_uuid(),
    run_id uuid not null references run(run_id),
    received_at timestamptz default now(),
    span_id uuid not null, -- maps to langsmith run id
    event text not null,
    name text not null,
    tags jsonb not null default '[]',
    data jsonb not null default '{}',
    metadata jsonb not null default '{}'
);



alter table checkpoints
    add column if not exists run_id uuid references run(run_id);

create index concurrently if not exists run_thread_id_idx on run(thread_id);
create index concurrently if not exists run_assistant_id_idx on run(assistant_id);
create index concurrently if not exists run_event_run_id_idx on run_event(run_id);
create index concurrently if not exists checkpoints_run_id_idx on checkpoints(run_id);
