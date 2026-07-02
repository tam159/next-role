alter table thread add column if not exists interrupts jsonb not null default '{}';
