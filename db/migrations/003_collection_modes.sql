ALTER TABLE collection_runs
  ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'scheduled';

ALTER TABLE collection_runs
  ADD CONSTRAINT collection_runs_run_mode_check
  CHECK (run_mode IN ('scheduled', 'on_demand'));

CREATE INDEX idx_collection_runs_mode_time
  ON collection_runs(run_mode, collector, started_at DESC);

