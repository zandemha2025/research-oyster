-- Per-source run ledger: the authoritative record of what each collection attempt
-- actually did — attempted, its outcome, and how much it yielded. This is what stops
-- a source that ran and failed (e.g. an X search that returned HTTP 200 with zero
-- posts, or a Reddit search rate-limited with 403) from being narrated in the report
-- as "not available". The synthesis must be reconciled against these rows.
CREATE TABLE IF NOT EXISTS research_source_runs (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    connector       TEXT NOT NULL,
    query           TEXT,
    outcome         TEXT NOT NULL CHECK (outcome IN ('ok', 'empty', 'rate_limited', 'not_configured', 'error')),
    http_status     INTEGER,
    yield_count     INTEGER NOT NULL DEFAULT 0,
    error_text      TEXT,
    raw_response_id BIGINT,
    attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_runs_job ON research_source_runs (job_id, attempted_at);
