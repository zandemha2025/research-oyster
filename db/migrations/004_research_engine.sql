CREATE TABLE research_jobs (
  id            BIGSERIAL PRIMARY KEY,
  brief         TEXT NOT NULL,
  decision      TEXT,
  market        TEXT,
  time_horizon  TEXT,
  status        TEXT NOT NULL DEFAULT 'active',
  plan          JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE research_evidence (
  id            BIGSERIAL PRIMARY KEY,
  job_id        BIGINT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  source_type   TEXT NOT NULL,
  url           TEXT NOT NULL,
  title         TEXT,
  excerpt       TEXT NOT NULL,
  author        TEXT,
  published_at  TIMESTAMPTZ,
  collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  query         TEXT,
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_hash  TEXT NOT NULL,
  UNIQUE (job_id, content_hash)
);

CREATE INDEX idx_research_evidence_job ON research_evidence(job_id, collected_at DESC);
CREATE INDEX idx_research_evidence_source ON research_evidence(job_id, source_type);

