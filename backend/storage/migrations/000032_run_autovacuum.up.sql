-- Adjust autovacuum thresholds for run table. Due to frequent updates
-- to run status this table can accumulate dead tuples, which severely
-- affect performance of run_pending_idx, used by Runs.next()

ALTER TABLE run
  SET (autovacuum_vacuum_scale_factor  = 0.01,
       autovacuum_vacuum_threshold     = 50,
       autovacuum_analyze_scale_factor = 0.01,
       autovacuum_analyze_threshold    = 50);
