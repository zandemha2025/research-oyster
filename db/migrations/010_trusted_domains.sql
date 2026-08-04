-- Standing per-domain consent for browser traffic capture.
-- A researcher approves a domain once (bound to their paired browser client); future
-- session requests for that domain auto-approve so capture starts hands-free while they
-- browse. Trust is per-client, per-domain, revocable, and audited. It never enables
-- unattended/headless capture — recording still happens only in the user's real browser
-- on pages they load, and every auto-approved session keeps its own TTL and Stop control.
CREATE TABLE capture_trusted_domains (
  id                BIGSERIAL PRIMARY KEY,
  client_id         BIGINT NOT NULL REFERENCES browser_clients(id) ON DELETE CASCADE,
  domain            TEXT NOT NULL CHECK (length(domain) BETWEEN 4 AND 253),
  max_payload_bytes INTEGER NOT NULL DEFAULT 131072,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_trusted_domain_active
  ON capture_trusted_domains(client_id, domain) WHERE revoked_at IS NULL;
