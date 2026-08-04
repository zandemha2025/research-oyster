"""Persist full, redacted HTTP payloads from research connectors into raw_responses.

The research engine uses bare httpx clients rather than the legacy collectors' HTTPClient,
so it does not automatically populate raw_responses. This module bridges that gap: after
each response, a connector calls record_raw / record_raw_parts to store the immutable
payload keyed by research_job_id. It reuses the collectors' redaction helpers, dedupes by
(research_job_id, body_hash) so retries and pagination re-fetches do not bloat the table,
and is deliberately best-effort — a logging failure returns None and never breaks a
connector, consistent with the "connectors never dead-ends" principle.

record_raw_parts accepts an optional open connection. When one is supplied the insert runs
inside the caller's transaction (no separate commit) — this is required for approved-session
capture, where the surrounding transaction already holds a FOR UPDATE lock on the job row
that raw_responses' foreign key would otherwise deadlock against from a second connection.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from collectors.base import PARSER_VERSION, SECRET_KEYS, redacted_headers, safe_response_content
from db.queries import connect, json_value

MAX_RAW_BODY = 2_000_000


def _redacted_url(url: str) -> str:
    """Redact secret-looking query parameter values in a plain URL string."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.query:
        return url
    pairs = [
        (key, "[REDACTED]" if SECRET_KEYS.match(key) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def _insert(conn: Any, job_id: int, collector: str, kind: str, method: str, url: str,
            status: int, request_headers: dict[str, str], response_headers: dict[str, str],
            content: bytes) -> int:
    """Dedup by (research_job_id, body_hash) then insert one raw row. No commit."""
    body_hash = hashlib.sha256(content).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM raw_responses WHERE research_job_id=%s AND body_hash=%s LIMIT 1",
            (job_id, body_hash),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        text = content.decode("utf-8", errors="replace")
        try:
            body, body_text = json.loads(text), None
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            body, body_text = None, text
        cur.execute(
            """INSERT INTO raw_responses
               (run_id, research_job_id, collector, payload_kind, request_method, request_url,
                request_headers, response_status, response_headers, body, body_text,
                body_hash, parser_version)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (None, job_id, collector, kind, method, url, json_value(request_headers), status,
             json_value(response_headers), json_value(body) if body is not None else None,
             body_text, body_hash, PARSER_VERSION),
        )
        return cur.fetchone()["id"]


def record_raw_parts(
    database_url: str, job_id: int, collector: str, kind: str, *,
    method: str, url: str, status: int,
    request_headers: Mapping[str, Any] | None = None,
    response_headers: Mapping[str, Any] | None = None,
    content: bytes = b"",
    conn: Any = None,
) -> int | None:
    """Store one redacted payload for a research job; dedupe identical bodies. Best-effort.

    Pass `conn` to insert inside an existing transaction (no commit); otherwise a private
    connection is opened and committed.
    """
    try:
        safe_content = safe_response_content(kind, content or b"")
        truncated = False
        if len(safe_content) > MAX_RAW_BODY:
            safe_content = safe_content[:MAX_RAW_BODY]
            truncated = True
        resp_headers = redacted_headers(dict(response_headers or {}))
        if truncated:
            resp_headers["X-Oyster-Truncated"] = "true"
        req_headers = redacted_headers(dict(request_headers or {}))
        safe_url = _redacted_url(url)
        if conn is not None:
            return _insert(conn, job_id, collector, kind, method, safe_url, status,
                           req_headers, resp_headers, safe_content)
        with connect(database_url) as owned:
            raw_id = _insert(owned, job_id, collector, kind, method, safe_url, status,
                             req_headers, resp_headers, safe_content)
            owned.commit()
            return raw_id
    except Exception:
        # Raw logging is provenance, not the product. Never let it break collection.
        return None


def record_raw(database_url: str, job_id: int, collector: str, kind: str, response: Any) -> int | None:
    """Record an httpx (or Scrapling) response object. Best-effort."""
    try:
        request = getattr(response, "request", None)
        method = getattr(request, "method", "GET")
        url = str(getattr(response, "url", ""))
        status = int(getattr(response, "status_code", getattr(response, "status", 0)) or 0)
        req_headers = dict(getattr(request, "headers", {}) or {})
        resp_headers = dict(getattr(response, "headers", {}) or {})
        content = getattr(response, "content", b"") or b""
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        return record_raw_parts(
            database_url, job_id, collector, kind, method=method, url=url, status=status,
            request_headers=req_headers, response_headers=resp_headers, content=content,
        )
    except Exception:
        return None
