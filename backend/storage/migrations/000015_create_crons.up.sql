create table if not exists cron (
    cron_id uuid primary key default gen_random_uuid(),
    assistant_id uuid null references assistant(assistant_id) on delete cascade,
    thread_id uuid null references thread(thread_id) on delete cascade,
    user_id text,
    payload jsonb not null default '{}',
    schedule text not null,
    next_run_date timestamptz,
    end_time timestamptz,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);
