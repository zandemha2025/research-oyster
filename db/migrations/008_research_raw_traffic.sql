ALTER TABLE raw_responses
  ADD COLUMN research_job_id BIGINT REFERENCES research_jobs(id) ON DELETE SET NULL;

CREATE INDEX idx_raw_responses_research_job ON raw_responses(research_job_id, received_at);

COMMENT ON COLUMN raw_responses.research_job_id IS
  'Set for research-connector and approved-session browser traffic; run_id stays NULL for these rows.';
