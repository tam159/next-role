alter table run
    add column if not exists multitask_strategy text not null default 'reject';
