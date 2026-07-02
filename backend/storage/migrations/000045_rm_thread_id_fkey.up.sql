-- Remove cascading delete from thread to run.
-- We will need to manually run a CTE on relevant deletion endpoints;
alter table run drop constraint if exists run_thread_id_fkey;