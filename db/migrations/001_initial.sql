CREATE TABLE sources (
  id              SERIAL PRIMARY KEY,
  slug            TEXT UNIQUE NOT NULL,
  platform        TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  endpoint_url    TEXT,
  source_class    TEXT,
  status          TEXT NOT NULL DEFAULT 'active',
  last_success_at TIMESTAMPTZ,
  last_error      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE collection_runs (
  id             SERIAL PRIMARY KEY,
  collector      TEXT NOT NULL,
  started_at     TIMESTAMPTZ NOT NULL,
  finished_at    TIMESTAMPTZ,
  status         TEXT NOT NULL,
  items_ok       INTEGER DEFAULT 0,
  items_failed   INTEGER DEFAULT 0,
  error_detail   JSONB
);

CREATE TABLE discord_servers (
  id            SERIAL PRIMARY KEY,
  invite_code   TEXT UNIQUE NOT NULL,
  guild_id      TEXT,
  guild_name    TEXT,
  title_slug    TEXT,
  status        TEXT NOT NULL DEFAULT 'active',
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE discord_snapshots (
  id                SERIAL PRIMARY KEY,
  server_id         INTEGER NOT NULL REFERENCES discord_servers(id),
  run_id            INTEGER REFERENCES collection_runs(id),
  observed_at       TIMESTAMPTZ NOT NULL,
  member_count      INTEGER,
  presence_count    INTEGER,
  raw               JSONB NOT NULL
);

CREATE INDEX idx_discord_snap_server_time ON discord_snapshots(server_id, observed_at DESC);

CREATE TABLE stream_categories (
  id              SERIAL PRIMARY KEY,
  platform        TEXT NOT NULL,
  platform_cat_id TEXT NOT NULL,
  category_name   TEXT NOT NULL,
  title_slug      TEXT,
  UNIQUE (platform, platform_cat_id)
);

CREATE TABLE category_snapshots (
  id             SERIAL PRIMARY KEY,
  category_id    INTEGER NOT NULL REFERENCES stream_categories(id),
  run_id         INTEGER REFERENCES collection_runs(id),
  observed_at    TIMESTAMPTZ NOT NULL,
  total_viewers  INTEGER,
  channel_count  INTEGER,
  top_channel    TEXT,
  top_channel_viewers INTEGER,
  directory_rank INTEGER,
  raw            JSONB NOT NULL
);

CREATE INDEX idx_cat_snap_cat_time ON category_snapshots(category_id, observed_at DESC);

CREATE TABLE titles (
  slug          TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  publisher     TEXT,
  aliases       TEXT[]
);

CREATE TABLE articles (
  id            SERIAL PRIMARY KEY,
  source_id     INTEGER NOT NULL REFERENCES sources(id),
  url_hash      TEXT UNIQUE NOT NULL,
  url           TEXT NOT NULL,
  headline      TEXT NOT NULL,
  summary       TEXT,
  author        TEXT,
  published_at  TIMESTAMPTZ,
  collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  title_slug    TEXT REFERENCES titles(slug),
  raw           JSONB
);

CREATE INDEX idx_articles_published ON articles(published_at DESC);
CREATE INDEX idx_articles_title ON articles(title_slug, published_at DESC);

CREATE TABLE report_runs (
  id             SERIAL PRIMARY KEY,
  week_start     DATE NOT NULL,
  week_end       DATE NOT NULL,
  generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  output_path    TEXT,
  data_coverage  JSONB NOT NULL
);

