alter table run drop constraint if exists run_thread_id_fkey,
    add foreign key (thread_id) references thread(thread_id) on delete cascade;
