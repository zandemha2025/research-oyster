import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from time import mktime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser

from collectors.base import HTTPClient, RunLog
from db.queries import json_value, load_seed


class FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        rel = (values.get("rel") or "").lower().split()
        content_type = (values.get("type") or "").lower()
        if tag.lower() == "link" and "alternate" in rel and content_type in {"application/rss+xml", "application/atom+xml"} and values.get("href"):
            self.urls.append(values["href"] or "")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower() or "https", host, path, "", ""))


def _published(entry: Any) -> datetime | None:
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(mktime(value), timezone.utc) if value else None


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def resolve_feed(http: HTTPClient, homepage: str) -> str | None:
    response = await http.request("GET", homepage, headers={"User-Agent": "GamingCulturePulse/1.0"})
    if response.status_code < 400:
        parser = FeedLinkParser()
        parser.feed(response.text)
        candidates = [urljoin(homepage, value) for value in parser.urls]
    else:
        candidates = []
    candidates.extend(urljoin(homepage.rstrip("/") + "/", suffix) for suffix in ("feed", "rss", "feeds/all", "rss/index.xml"))
    for candidate in dict.fromkeys(candidates):
        feed_response = await http.request("GET", candidate, headers={"User-Agent": "GamingCulturePulse/1.0"})
        if feed_response.status_code < 400 and feedparser.parse(feed_response.content).entries:
            return str(feed_response.url)
    return None


async def collect(conn: Any, timeout: float = 20, max_retries: int = 4) -> None:
    http = HTTPClient(timeout, max_retries, min_interval=0.5)
    try:
        with RunLog(conn, "press") as run:
            config_path = Path(__file__).resolve().parents[1] / "config" / "outlets.press.yaml"
            homepages = {item["slug"]: item["homepage"] for item in load_seed(config_path)}
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sources WHERE platform='press' AND status != 'retired' ORDER BY id")
                sources = cur.fetchall()
                cur.execute("SELECT slug, aliases FROM titles")
                titles = cur.fetchall()
            for source in sources:
                try:
                    homepage = homepages.get(source["slug"], source["endpoint_url"] or "")
                    feed_url = source["endpoint_url"] if source["status"] == "active" else None
                    if not feed_url or not feed_url.startswith("http") or feed_url.rstrip("/") == homepage.rstrip("/"):
                        feed_url = await resolve_feed(http, homepage)
                    if not feed_url:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE sources SET status='unavailable', last_error='RSS feed could not be resolved' WHERE id=%s", (source["id"],))
                        conn.commit()
                        run.error(source["slug"], "RSS feed could not be resolved")
                        continue
                    response = await http.request("GET", feed_url, headers={"User-Agent": "GamingCulturePulse/1.0"})
                    if response.status_code >= 400 and homepage and feed_url.rstrip("/") != homepage.rstrip("/"):
                        rediscovered = await resolve_feed(http, homepage)
                        if rediscovered:
                            feed_url = rediscovered
                            response = await http.request("GET", feed_url, headers={"User-Agent": "GamingCulturePulse/1.0"})
                    response.raise_for_status()
                    parsed = feedparser.parse(response.content)
                    inserted = 0
                    with conn.cursor() as cur:
                        cur.execute("UPDATE sources SET endpoint_url=%s, status='active', last_success_at=now(), last_error=NULL WHERE id=%s", (str(response.url), source["id"]))
                        for entry in parsed.entries:
                            url = canonicalize_url(entry.get("link") or "")
                            headline = (entry.get("title") or "").strip()
                            if not url or not headline:
                                continue
                            text = f"{headline} {entry.get('summary','')}"
                            matched = next((title["slug"] for title in titles if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I) for alias in (title["aliases"] or []))), None)
                            cur.execute(
                                """INSERT INTO articles (source_id,url_hash,url,headline,summary,author,published_at,title_slug,raw)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (url_hash) DO NOTHING""",
                                (source["id"], hashlib.sha256(url.encode()).hexdigest(), url, headline, entry.get("summary"),
                                 entry.get("author"), _published(entry), matched, json_value(_plain(entry))),
                            )
                            inserted += cur.rowcount
                    conn.commit()
                    run.success(inserted)
                except Exception as exc:
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE sources SET last_error=%s WHERE id=%s", (str(exc)[:1000], source["id"]))
                    conn.commit()
                    run.error(source["slug"], exc)
    finally:
        await http.close()
