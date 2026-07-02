drop table if exists checkpoints;

create table if not exists checkpoints (
    thread_id uuid not null references thread(thread_id) on delete cascade,
    checkpoint_id uuid not null,
    run_id uuid references run(run_id) on delete cascade,
    parent_checkpoint_id uuid,
    checkpoint jsonb not null,
    metadata jsonb not null default '{}',
    PRIMARY KEY (thread_id, checkpoint_id)
);

create table if not exists checkpoint_blobs (
    thread_id uuid not null references thread(thread_id) on delete cascade,
    channel text not null,
    version integer not null,
    type text not null,
    blob bytea not null,
    PRIMARY KEY (thread_id, channel, version)
);

create index concurrently if not exists checkpoints_run_id_idx on checkpoints(run_id);