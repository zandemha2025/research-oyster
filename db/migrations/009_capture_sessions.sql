-- Consent-gated browser traffic capture sessions.
-- An agent requests a session for (job, domain); the researcher approves it once in the
-- extension; approved-session captures are then accepted for that domain until the session
-- expires or is stopped. The server re-validates every submission against this table.
CREATE TABLE capture_sessions (
  id                BIGSERIAL PRIMARY KEY,
  job_id            BIGINT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  client_id         BIGINT REFERENCES browser_clients(id) ON DELETE SET NULL,
  domain            TEXT NOT NULL CHECK (length(domain) BETWEEN 4 AND 253),
  reason            TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
  requested_by      TEXT NOT NULL DEFAULT 'agent',
  status            TEXT NOT NULL DEFAULT 'requested'
                    CHECK (status IN ('requested','approved','declined','stopped','expired')),
  max_payload_bytes INTEGER NOT NULL DEFAULT 131072,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at       TIMESTAMPTZ,
  expires_at        TIMESTAMPTZ,
  ended_at          TIMESTAMPTZ
);

CREATE INDEX idx_capture_sessions_status ON capture_sessions(status, created_at DESC);
CREATE INDEX idx_capture_sessions_job ON capture_sessions(job_id, created_at DESC);
