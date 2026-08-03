ALTER TABLE browser_captures DROP CONSTRAINT browser_captures_status_check;
ALTER TABLE browser_captures
  ADD CONSTRAINT browser_captures_status_check CHECK (status IN ('pending','approved','rejected','expired'));
ALTER TABLE browser_captures
  ADD COLUMN expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours');
CREATE INDEX idx_browser_captures_expiry ON browser_captures(expires_at) WHERE status='pending';
