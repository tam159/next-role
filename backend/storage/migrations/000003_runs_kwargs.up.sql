alter table run
    add column if not exists kwargs jsonb not null;

create index concurrently if not exists run_pending_idx on run(created_at) where status = 'pending';
