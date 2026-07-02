create index concurrently if not exists thread_created_at_idx on thread (created_at desc);
create index concurrently if not exists assistant_created_at_idx on assistant (created_at desc);
