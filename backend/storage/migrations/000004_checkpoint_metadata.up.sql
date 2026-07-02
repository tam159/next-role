alter table checkpoints add column if not exists metadata jsonb not null default '{}';
