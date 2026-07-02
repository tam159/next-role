drop table if exists checkpoints;

create table if not exists checkpoints (
    thread_id uuid not null REFERENCES thread(thread_id) on delete cascade,
    checkpoint_id uuid not null,
    run_id uuid references run(run_id) on delete cascade,
    parent_checkpoint_id uuid,
    checkpoint bytea NOT NULL,
    metadata jsonb not null default '{}',
    PRIMARY KEY (thread_id, checkpoint_id)
);

create index concurrently if not exists checkpoints_run_id_idx on checkpoints(run_id);
