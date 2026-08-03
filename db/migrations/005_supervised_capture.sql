CREATE TABLE browser_pairing_codes (
  id            BIGSERIAL PRIMARY KEY,
  code_hash     TEXT NOT NULL UNIQUE,
  expires_at    TIMESTAMPTZ NOT NULL,
  used_at       TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE browser_clients (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  extension_id  TEXT NOT NULL,
  token_hash    TEXT NOT NULL UNIQUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ,
  UNIQUE (extension_id, name)
);

CREATE TABLE browser_captures (
  id                 BIGSERIAL PRIMARY KEY,
  job_id             BIGINT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  client_id          BIGINT NOT NULL REFERENCES browser_clients(id) ON DELETE RESTRICT,
  status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  capture_mode       TEXT NOT NULL CHECK (capture_mode IN ('manual','supervised','approved_session')),
  source_type        TEXT NOT NULL,
  url                TEXT NOT NULL,
  page_title         TEXT,
  excerpt            TEXT NOT NULL,
  original_author    TEXT,
  anonymized_author  TEXT,
  anonymize           BOOLEAN NOT NULL DEFAULT true,
  research_question  TEXT,
  researcher_note    TEXT,
  source_metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_id        BIGINT REFERENCES research_evidence(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at        TIMESTAMPTZ,
  CHECK (length(excerpt) BETWEEN 1 AND 20000),
  CHECK (length(url) BETWEEN 1 AND 4096)
);

CREATE INDEX idx_browser_captures_review ON browser_captures(status, created_at DESC);
CREATE INDEX idx_browser_captures_job ON browser_captures(job_id, created_at DESC);

CREATE TABLE browser_capture_audit (
  id          BIGSERIAL PRIMARY KEY,
  capture_id  BIGINT REFERENCES browser_captures(id) ON DELETE SET NULL,
  client_id   BIGINT REFERENCES browser_clients(id) ON DELETE SET NULL,
  event_type  TEXT NOT NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_browser_capture_audit_capture ON browser_capture_audit(capture_id, created_at);
