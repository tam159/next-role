alter table checkpoints drop constraint if exists checkpoints_thread_id_fkey,
    add foreign key (thread_id) references thread(thread_id) on delete cascade; 
