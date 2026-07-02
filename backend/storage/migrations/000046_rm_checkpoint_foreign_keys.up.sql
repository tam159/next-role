-- Remove all foreign key constraints from checkpoint-related tables
-- Keep indexes for query performance
ALTER TABLE checkpoints
    DROP CONSTRAINT IF EXISTS checkpoints_run_id_fkey,
    DROP CONSTRAINT IF EXISTS checkpoints_thread_id_fkey;

ALTER TABLE checkpoint_blobs
    DROP CONSTRAINT IF EXISTS checkpoint_blobs_thread_id_fkey;

ALTER TABLE checkpoint_writes
    DROP CONSTRAINT IF EXISTS checkpoint_writes_thread_id_fkey;