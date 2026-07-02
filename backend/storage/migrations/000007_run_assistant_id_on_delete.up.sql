alter table run drop constraint if exists run_assistant_id_fkey,
    add foreign key (assistant_id) references assistant(assistant_id) on delete cascade;
