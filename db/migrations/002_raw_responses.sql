CREATE TABLE raw_responses (
  id              BIGSERIAL PRIMARY KEY,
  run_id          INTEGER REFERENCES collection_runs(id),
  collector       TEXT NOT NULL,
  payload_kind    TEXT NOT NULL,
  request_method  TEXT NOT NULL,
  request_url     TEXT NOT NULL,
  request_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
  response_status INTEGER NOT NULL,
  response_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
  body            JSONB,
  body_text       TEXT,
  body_hash       TEXT NOT NULL,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  parse_status    TEXT NOT NULL DEFAULT 'pending',
  parser_version  TEXT NOT NULL,
  parsed          JSONB,
  parse_error     TEXT
);

CREATE INDEX idx_raw_responses_run ON raw_responses(run_id, received_at);
CREATE INDEX idx_raw_responses_replay ON raw_responses(collector, payload_kind, parse_status);

COMMENT ON TABLE raw_responses IS 'Append-only HTTP response evidence. Replay only updates parse metadata, never response evidence.';
