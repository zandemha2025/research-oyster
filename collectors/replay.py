"""Offline response replay. This module never performs network calls."""
from __future__ import annotations

from typing import Any, Callable

import feedparser

from db.queries import json_value

ReplayHandler = Callable[[dict[str, Any]], Any]


def _json_payload(row: dict[str, Any]) -> Any:
    if row["body"] is None:
        raise ValueError("expected a JSON response body")
    return row["body"]


def _press(row: dict[str, Any]) -> list[dict[str, Any]]:
    document = row["body_text"]
    if document is None:
        raise ValueError("expected an RSS or Atom text body")
    parsed = feedparser.parse(document)
    return [{
        "url": entry.get("link"), "headline": entry.get("title"),
        "summary": entry.get("summary"), "author": entry.get("author"),
        "published": entry.get("published"),
    } for entry in parsed.entries]


REPLAY_REGISTRY: dict[str, ReplayHandler] = {
    "oauth_token": _json_payload,
    "discord_invite": _json_payload,
    "discord_widget": _json_payload,
    "twitch_streams": _json_payload,
    "twitch_games_top": _json_payload,
    "kick_livestreams": _json_payload,
    "press_document": _press,
}


def replay_response(conn: Any, raw_response_id: int) -> Any:
    """Reparse one stored payload and retain the normalized result for auditing."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM raw_responses WHERE id=%s", (raw_response_id,))
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"raw response {raw_response_id} does not exist")
    handler = REPLAY_REGISTRY.get(row["payload_kind"])
    if handler is None:
        error = f"replay unsupported for payload kind {row['payload_kind']}"
        with conn.cursor() as cur:
            cur.execute("UPDATE raw_responses SET parse_status='unsupported', parse_error=%s WHERE id=%s", (error, raw_response_id))
        conn.commit()
        raise NotImplementedError(error)
    try:
        result = handler(row)
    except Exception as exc:
        with conn.cursor() as cur:
            cur.execute("UPDATE raw_responses SET parse_status='failed', parse_error=%s WHERE id=%s", (str(exc)[:2000], raw_response_id))
        conn.commit()
        raise
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE raw_responses SET parse_status='parsed', parsed=%s, parse_error=NULL WHERE id=%s",
            (json_value(result), raw_response_id),
        )
    conn.commit()
    return result
