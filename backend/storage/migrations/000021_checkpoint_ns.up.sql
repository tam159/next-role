alter table checkpoints
    add column if not exists checkpoint_ns text not null default '',
    drop constraint checkpoints_pkey,
    add constraint checkpoints_pkey primary key (thread_id, checkpoint_ns, checkpoint_id);

alter table checkpoint_blobs
    add column if not exists checkpoint_ns text not null default '',
    drop constraint checkpoint_blobs_pkey,
    add constraint checkpoint_blobs_pkey primary key (thread_id, checkpoint_ns, channel, version);

alter table checkpoint_writes
    add column if not exists checkpoint_ns text not null default '',
    drop constraint checkpoint_writes_pkey,
    add constraint checkpoint_writes_pkey primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx);
