import asyncio
import random
import re
import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from db.queries import connect, insert_raw_response, json_value

PARSER_VERSION = "1"
_raw_context: ContextVar[tuple[Any, int, str] | None] = ContextVar("raw_context", default=None)
_collection_mode: ContextVar[str] = ContextVar("collection_mode", default="scheduled")
_SECRET_KEYS = re.compile(r"^(authorization|cookie|set-cookie|client_secret|token|access_token|refresh_token|api[_-]?key)$", re.I)


def _redacted_headers(headers: Any) -> dict[str, str]:
    return {str(k): ("[REDACTED]" if _SECRET_KEYS.match(str(k)) else str(v)) for k, v in headers.items()}


def _redacted_url(url: httpx.URL) -> str:
    pairs = [(k, "[REDACTED]" if _SECRET_KEYS.match(k) else v) for k, v in url.params.multi_items()]
    return str(url.copy_with(query=str(httpx.QueryParams(pairs)).encode("ascii")))


def _payload_kind(url: httpx.URL) -> str:
    path = url.path.lower()
    if "oauth" in path or "token" in path:
        return "oauth_token"
    if "/invites/" in path:
        return "discord_invite"
    if "widget.json" in path:
        return "discord_widget"
    if "/streams" in path:
        return "twitch_streams"
    if "/games/top" in path:
        return "twitch_games_top"
    if "/livestreams" in path:
        return "kick_livestreams"
    return "press_document"


def _safe_response_content(kind: str, content: bytes) -> bytes:
    """Never persist credential material returned by identity endpoints."""
    if kind != "oauth_token":
        return content
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return b'"[REDACTED OAUTH RESPONSE]"'

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: ("[REDACTED]" if _SECRET_KEYS.match(str(key)) else scrub(child)) for key, child in item.items()}
        if isinstance(item, list):
            return [scrub(child) for child in item]
        return item

    return json.dumps(scrub(value), separators=(",", ":")).encode()


# Public aliases so the research engine reuses this redaction logic without importing
# underscore-prefixed names. Redaction lives here in one place.
SECRET_KEYS = _SECRET_KEYS
redacted_headers = _redacted_headers
safe_response_content = _safe_response_content


class RequestFailed(RuntimeError):
    pass


@contextmanager
def collection_mode(mode: str):
    if mode not in {"scheduled", "on_demand"}:
        raise ValueError(f"unsupported collection mode: {mode}")
    token = _collection_mode.set(mode)
    try:
        yield
    finally:
        _collection_mode.reset(token)


class HTTPClient:
    """Rate-limited HTTP with bounded retries and explicit timeouts."""

    def __init__(self, timeout: float, max_retries: int, min_interval: float = 0.25):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            async with self._lock:
                loop = asyncio.get_running_loop()
                delay = self.min_interval - (loop.time() - self._last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_request = loop.time()
            try:
                response = await self.client.request(method, url, **kwargs)
                context = _raw_context.get()
                if context is not None:
                    conn, run_id, collector = context
                    kind = _payload_kind(response.request.url)
                    raw_id = insert_raw_response(
                        conn, run_id=run_id, collector=collector,
                        payload_kind=kind, method=response.request.method,
                        url=_redacted_url(response.request.url),
                        request_headers=_redacted_headers(response.request.headers),
                        status=response.status_code,
                        response_headers=_redacted_headers(response.headers),
                        content=_safe_response_content(kind, response.content),
                        parser_version=PARSER_VERSION,
                    )
                    response.extensions["raw_response_id"] = raw_id
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                if attempt == self.max_retries:
                    return response
                wait = self._retry_delay(response, attempt)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                wait = min(30.0, (2 ** attempt) + random.random())
            await asyncio.sleep(wait)
        raise RequestFailed(str(last_error or "request failed after retries"))

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return min(120.0, max(0.0, float(value)))
            except ValueError:
                try:
                    dt = parsedate_to_datetime(value)
                    return min(120.0, max(0.0, (dt - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError):
                    pass
        return min(30.0, (2 ** attempt) + random.random())


@dataclass
class RunLog:
    conn: Any
    collector: str
    run_id: int | None = None
    ok: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    _context_token: Token | None = field(default=None, init=False, repr=False)
    _raw_conn: Any = field(default=None, init=False, repr=False)

    def __enter__(self) -> "RunLog":
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO collection_runs (collector, started_at, status, run_mode) VALUES (%s, now(), 'running', %s) RETURNING id",
                (self.collector, _collection_mode.get()),
            )
            self.run_id = cur.fetchone()["id"]
        self.conn.commit()
        # A dedicated connection makes the evidence commit independent, so recording
        # a response never commits half-parsed collector state.
        try:
            self._raw_conn = connect(self.conn.info.dsn)
        except (AttributeError, TypeError):
            self._raw_conn = self.conn
        self._context_token = _raw_context.set((self._raw_conn, self.run_id, self.collector))
        return self

    def success(self, count: int = 1) -> None:
        self.ok += count

    def error(self, item: str, exc: Exception | str) -> None:
        self.failed += 1
        self.errors.append({"item": item, "error": str(exc)[:1000]})

    def __exit__(self, exc_type: Any, exc: Exception | None, tb: Any) -> bool:
        if exc:
            self.error("collector", exc)
        status = "failed" if exc else ("success" if self.failed == 0 else ("partial" if self.ok else "failed"))
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE collection_runs SET finished_at=now(), status=%s, items_ok=%s,
                   items_failed=%s, error_detail=%s WHERE id=%s""",
                (status, self.ok, self.failed, json_value(self.errors) if self.errors else None, self.run_id),
            )
        self.conn.commit()
        if self._context_token is not None:
            _raw_context.reset(self._context_token)
        if self._raw_conn is not None and self._raw_conn is not self.conn:
            self._raw_conn.close()
        return False
