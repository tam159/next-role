alter table run_event drop constraint if exists run_event_run_id_fkey,
    add foreign key (run_id) references run(run_id) on delete cascade;
