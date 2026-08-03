ALTER TABLE browser_captures ADD COLUMN captured_at TIMESTAMPTZ;
ALTER TABLE browser_captures ADD COLUMN client_capture_id TEXT;
CREATE UNIQUE INDEX uq_browser_capture_client_key
  ON browser_captures(client_id, client_capture_id) WHERE client_capture_id IS NOT NULL;
