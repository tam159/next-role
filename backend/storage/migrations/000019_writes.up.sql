create table if not exists checkpoint_writes (
    thread_id uuid not null references thread(thread_id) on delete cascade,
    checkpoint_id uuid not null,
    task_id uuid not null,
    idx integer not null,
    channel text not null,
    type text not null,
    blob bytea not null,
    PRIMARY KEY (thread_id, checkpoint_id, task_id, idx)
);
