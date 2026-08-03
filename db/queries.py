import json
import hashlib
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def migrate(conn: psycopg.Connection, directory: Path = Path("db/migrations")) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted(directory.glob("*.sql")):
            cur.execute("SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,))
            if cur.fetchone():
                continue
            cur.execute(path.read_text())
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
    conn.commit()


def load_seed(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def seed_config(conn: psycopg.Connection, config_dir: Path = Path("config")) -> None:
    titles = load_seed(config_dir / "titles.yaml")
    servers = load_seed(config_dir / "watchlist.discord.yaml")
    outlets = load_seed(config_dir / "outlets.press.yaml")
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO titles (slug, display_name, publisher, aliases)
               VALUES (%(slug)s, %(display_name)s, %(publisher)s, %(aliases)s)
               ON CONFLICT (slug) DO UPDATE SET display_name=EXCLUDED.display_name,
                 publisher=EXCLUDED.publisher, aliases=EXCLUDED.aliases""", titles)
        cur.executemany(
            """INSERT INTO discord_servers (invite_code, title_slug)
               VALUES (%(invite_code)s, %(title_slug)s)
               ON CONFLICT (invite_code) DO UPDATE SET title_slug=EXCLUDED.title_slug""", servers)
        cur.executemany(
            """INSERT INTO sources (slug, platform, display_name, endpoint_url, source_class)
               VALUES (%(slug)s, 'press', %(display_name)s, %(homepage)s, %(source_class)s)
               ON CONFLICT (slug) DO UPDATE SET display_name=EXCLUDED.display_name,
                 source_class=EXCLUDED.source_class""", outlets)
    conn.commit()


def upsert_category(cur: Any, platform: str, cat_id: str, name: str) -> int:
    cur.execute(
        """INSERT INTO stream_categories (platform, platform_cat_id, category_name, title_slug)
           VALUES (%s, %s, %s, (SELECT slug FROM titles WHERE lower(%s) = ANY(SELECT lower(x) FROM unnest(aliases) x) LIMIT 1))
           ON CONFLICT (platform, platform_cat_id) DO UPDATE SET category_name=EXCLUDED.category_name
           RETURNING id""", (platform, cat_id, name, name))
    return cur.fetchone()["id"]


def json_value(value: Any) -> psycopg.types.json.Jsonb:
    return psycopg.types.json.Jsonb(value)


def insert_raw_response(
    conn: Any, *, run_id: int | None, collector: str, payload_kind: str,
    method: str, url: str, request_headers: dict[str, str], status: int,
    response_headers: dict[str, str], content: bytes, parser_version: str,
) -> int:
    """Persist immutable response evidence and commit it before parsing begins."""
    text = content.decode("utf-8", errors="replace")
    try:
        body, body_text = json.loads(text), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        body, body_text = None, text
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO raw_responses
               (run_id, collector, payload_kind, request_method, request_url,
                request_headers, response_status, response_headers, body, body_text,
                body_hash, parser_version)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (run_id, collector, payload_kind, method, url, json_value(request_headers),
             status, json_value(response_headers), json_value(body) if body is not None else None,
             body_text, hashlib.sha256(content).hexdigest(), parser_version),
        )
        raw_id = cur.fetchone()["id"]
    conn.commit()
    return raw_id
