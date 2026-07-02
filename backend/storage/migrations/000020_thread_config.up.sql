alter table thread add column if not exists config jsonb not null default '{}';
