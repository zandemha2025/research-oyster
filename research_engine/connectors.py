from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import html as _html
import httpx
import re
import ipaddress
import socket
from urllib.parse import urljoin, urlparse, parse_qs, unquote

_TAG_RE = re.compile(r"<[^>]+>")

# A stable, honest User-Agent. Reddit's public JSON and DuckDuckGo's HTML endpoint both
# rate-limit anonymous clients; a descriptive UA is the difference between best-effort
# working and being blocked outright.
_UA = "ResearchOyster/0.2 (+https://github.com/zandemha2025/research-oyster)"


def _plain_text(value: str) -> str:
    """Reduce feed HTML (Google News summaries, CDATA markup) to readable text."""
    text = _TAG_RE.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()

from research_engine.raw_log import record_raw, record_raw_parts
from research_engine.store import ResearchStore


# Single source of truth for connector readiness and, crucially, what to do when a
# connector is not configured. Every unconfigured connector returns an ordered list of
# fallbacks so the AI host walks to the next door instead of giving up. connector_status()
# and not_configured() both read from here so guidance never drifts between the two.
CONNECTOR_GUIDES: dict[str, dict[str, Any]] = {
    "apify": {
        "enables": ["Reddit", "X scraping alternative", "web/community discovery", "arbitrary Actors"],
        "setup": "Paste APIFY_TOKEN in Setup (optional — Reddit and web work without it).",
        "fallbacks": [
            "search_reddit / fetch_reddit_thread for Reddit discussion (free, no key)",
            "discover_sources then crawl_web_page on the public pages it finds",
            "search_web to locate coverage, then read the best results",
        ],
    },
    "x_official": {
        "setup": "Paste X_BEARER_TOKEN in Setup; X charges per API usage.",
        "fallbacks": [
            "search_web for the topic to find where it is discussed publicly",
            "search_reddit — much of the same conversation happens on Reddit, for free",
            "crawl_web_page on public posts, threads, or news coverage",
        ],
    },
    "twitch": {
        "setup": "Add a Twitch developer Client ID and Client Secret in Setup.",
        "fallbacks": [
            "search_web / search_reddit for what viewers say about the streams (live chat itself is not autonomously retrievable)",
            "crawl_web_page on public stats pages such as twitchtracker.com or sullygnome.com",
            "get_browser_capture_mission only if the researcher needs live chat from a stream they are watching",
        ],
    },
    "kick": {
        "setup": "Add a Kick developer Client ID and Client Secret in Setup.",
        "fallbacks": [
            "search_web / search_reddit for the surrounding discussion (free)",
            "crawl_web_page on public kick.com category and channel pages",
            "get_browser_capture_mission only for live chat the researcher is watching",
        ],
    },
    "discord_public": {
        "scope": "Invite discovery and public member/activity metadata via inspect_discord_invite.",
    },
    "discord_messages": {
        "setup": "Install your Discord bot in each server and grant View Channel, Read Message History, and the Message Content intent.",
        "fallbacks": [
            "search_web / search_reddit — the same fans usually discuss the topic in public too",
            "inspect_discord_invite for public community size and activity metadata",
            "get_browser_capture_mission for supervised capture inside a server the researcher has already joined",
        ],
    },
    "rss": {
        "scope": "Any public RSS or Atom URL via fetch_rss.",
    },
    "self_hosted_web": {
        "engine": "Scrapling",
        "scope": "Readable text and links from any public page via crawl_web_page.",
        "fallbacks": [
            "run_apify_actor when a page needs browser interaction or blocks direct collection",
            "get_browser_capture_mission for pages behind a login the researcher holds",
        ],
    },
    "supervised_browser_capture": {
        "scope": "User-approved excerpts and, within an approved session, network payloads from pages the researcher can already access.",
        "setup": "Start the control center, load the browser_extension folder in Chrome or Edge, and pair it with a one-time code.",
        "boundary": "Candidates stay local until the user approves; Oyster never receives browser cookies or passwords.",
    },
}


# Predicate per connector over a Settings-shaped object. Connectors that are always ready
# (rss, discord_public, self_hosted_web, supervised_browser_capture) are omitted and default True.
READY_CHECKS: dict[str, Any] = {
    "apify": lambda s: bool(s.apify_token),
    "x_official": lambda s: bool(s.x_bearer_token),
    "twitch": lambda s: bool(s.twitch_client_id and s.twitch_client_secret),
    "kick": lambda s: bool(s.kick_client_id and s.kick_client_secret),
    "discord_messages": lambda s: bool(s.discord_bot_token),
}


def not_configured(connector: str) -> dict[str, Any]:
    """Structured 'try the next door' response returned when a connector lacks credentials.

    The AI host is instructed to try `fallbacks` in order rather than stop. This is a
    normal return value, not an exception, precisely so a missing credential never reads
    as a hard failure.
    """
    guide = CONNECTOR_GUIDES.get(connector, {})
    fallbacks = guide.get("fallbacks", [])
    return {
        "not_configured": True,
        "connector": connector,
        "error": f"{connector} is not configured.",
        "setup": guide.get("setup", ""),
        "fallbacks": fallbacks,
        "next_step": fallbacks[0] if fallbacks else "",
    }


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be a public http:// or https:// address.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("The page hostname could not be resolved.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Private, local, and reserved network addresses cannot be crawled.")


def _published(entry: Any) -> datetime | None:
    value = entry.get("published") or entry.get("updated")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


async def fetch_rss(store: ResearchStore, job_id: int, feed_url: str, query_terms: list[str], limit: int = 50) -> dict[str, Any]:
    _validate_public_url(feed_url)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.get(feed_url)
        raw_id = record_raw(store.database_url, job_id, "research.rss", "rss_feed", response)
        response.raise_for_status()
    _validate_public_url(str(response.url))
    parsed = feedparser.parse(response.content)
    terms = [term.lower() for term in query_terms if term.strip()]
    stored = []
    for entry in parsed.entries[: max(1, min(limit, 200))]:
        title = _plain_text(entry.get("title", ""))
        excerpt = _plain_text(entry.get("summary", entry.get("description", title))) or title
        if terms and not any(term in f"{title} {excerpt}".lower() for term in terms):
            continue
        result = store.add_evidence(
            job_id, source_type="rss", url=str(entry.get("link", feed_url)), title=title,
            excerpt=excerpt[:4000], author=_plain_text(entry.get("author", "")), published_at=_published(entry),
            query=" OR ".join(query_terms), metadata={"feed_url": feed_url},
        )
        stored.append({**result, "title": title, "url": str(entry.get("link", feed_url))})
    return {"feed_url": feed_url, "matched": len(stored), "items": stored, "raw_response_id": raw_id}


async def search_x(store: ResearchStore, job_id: int, bearer_token: str, query: str, max_results: int = 25) -> dict[str, Any]:
    if not bearer_token:
        return not_configured("x_official")
    params = {
        "query": query, "max_results": max(10, min(max_results, 100)),
        "tweet.fields": "created_at,author_id,conversation_id,lang,public_metrics",
        "expansions": "author_id", "user.fields": "name,username,verified",
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {bearer_token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.get("https://api.x.com/2/tweets/search/recent", params=params)
        raw_id = record_raw(store.database_url, job_id, "research.x", "x_search", response)
        response.raise_for_status()
        payload = response.json()
    users = {user["id"]: user for user in payload.get("includes", {}).get("users", [])}
    stored = []
    for post in payload.get("data", []):
        username = users.get(post.get("author_id"), {}).get("username", "unknown")
        url = f"https://x.com/{username}/status/{post['id']}"
        result = store.add_evidence(
            job_id, source_type="x", url=url, title=f"Post by @{username}", excerpt=post.get("text", ""),
            author=f"@{username}", published_at=datetime.fromisoformat(post["created_at"].replace("Z", "+00:00")) if post.get("created_at") else None,
            query=query, metadata={"post_id": post["id"], "metrics": post.get("public_metrics", {}), "conversation_id": post.get("conversation_id")},
        )
        stored.append({**result, "url": url, "author": f"@{username}", "text": post.get("text", "")})
    return {"query": query, "matched": len(stored), "items": stored, "meta": payload.get("meta", {}), "raw_response_id": raw_id}


async def inspect_discord_invite(store: ResearchStore, job_id: int, invite_url_or_code: str) -> dict[str, Any]:
    """Inspect public invite metadata for a community discovered during research."""
    match = re.search(r"(?:discord(?:app)?\.com/invite/|discord\.gg/)?([A-Za-z0-9-]+)$", invite_url_or_code.strip())
    if not match:
        raise ValueError("Provide a Discord invite code or a discord.gg invite URL.")
    code = match.group(1)
    url = f"https://discord.com/api/v10/invites/{code}"
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.get(url, params={"with_counts": "true", "with_expiration": "true"})
        raw_id = record_raw(store.database_url, job_id, "research.discord", "discord_invite", response)
        if response.status_code == 404:
            raise ValueError("That Discord invite is invalid or expired.")
        response.raise_for_status()
        payload = response.json()
    guild = payload.get("guild", {})
    name = guild.get("name", code)
    members = payload.get("approximate_member_count")
    online = payload.get("approximate_presence_count")
    excerpt = f"{name}: {members if members is not None else 'unknown'} approximate members; {online if online is not None else 'unknown'} online."
    result = store.add_evidence(
        job_id, source_type="discord", url=f"https://discord.gg/{code}", title=name, excerpt=excerpt,
        metadata={"invite_code": code, "guild_id": guild.get("id"), "member_count": members, "presence_count": online},
    )
    return {**result, "invite_code": code, "guild_name": name, "member_count": members, "presence_count": online, "raw_response_id": raw_id}


async def run_apify_actor(store: ResearchStore, job_id: int, token: str, actor_id: str,
                          actor_input: dict[str, Any], source_type: str, limit: int = 100) -> dict[str, Any]:
    if not token:
        return not_configured("apify")
    actor = actor_id.strip().replace("/", "~")
    if not actor:
        raise ValueError("Choose an Apify Actor ID, such as owner/actor-name.")
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    clamped_limit = max(1, min(limit, 1000))
    async with httpx.AsyncClient(timeout=300, headers={"Authorization": f"Bearer {token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.post(url, params={"clean": "true", "limit": clamped_limit}, json=actor_input)
        raw_id = record_raw(store.database_url, job_id, "research.apify", "apify_dataset", response)
        response.raise_for_status()
        rows = response.json()
    stored = []
    for raw_row in rows[:clamped_limit]:
        row = raw_row if isinstance(raw_row, dict) else {"value": raw_row}
        target = str(row.get("url") or row.get("link") or row.get("tweetUrl") or row.get("postUrl") or f"apify://{actor}")
        title = str(row.get("title") or row.get("name") or row.get("author") or source_type)
        excerpt = str(row.get("text") or row.get("body") or row.get("description") or row.get("content") or row)[:4000]
        result = store.add_evidence(job_id, source_type=source_type, url=target, title=title, excerpt=excerpt,
                                    metadata={"actor_id": actor_id, "record": row})
        stored.append({**result, "url": target, "title": title})
    return {"actor_id": actor_id, "received": len(rows), "stored": len(stored), "items": stored, "raw_response_id": raw_id}


async def search_twitch(store: ResearchStore, job_id: int, client_id: str, client_secret: str,
                        query: str, limit: int = 40) -> dict[str, Any]:
    if not client_id or not client_secret:
        return not_configured("twitch")
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post("https://id.twitch.tv/oauth2/token", params={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        record_raw(store.database_url, job_id, "research.twitch", "oauth_token", token_response)
        token_response.raise_for_status()
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}", "Client-Id": client_id}
        response = await client.get("https://api.twitch.tv/helix/search/channels", headers=headers,
                                    params={"query": query, "live_only": "false", "first": max(1, min(limit, 100))})
        raw_id = record_raw(store.database_url, job_id, "research.twitch", "twitch_search_channels", response)
        response.raise_for_status()
        rows = response.json().get("data", [])
    stored = []
    for row in rows:
        url = f"https://twitch.tv/{row['broadcaster_login']}"
        excerpt = f"{row.get('display_name')}: {row.get('title') or 'No current title'}; category {row.get('game_name') or 'unknown'}; live={row.get('is_live', False)}."
        result = store.add_evidence(job_id, source_type="twitch", url=url, title=row.get("display_name", ""), excerpt=excerpt,
                                    query=query, metadata=row)
        stored.append({**result, "url": url, **row})
    return {"query": query, "matched": len(stored), "items": stored, "raw_response_id": raw_id}


async def search_kick(store: ResearchStore, job_id: int, client_id: str, client_secret: str,
                      query: str, pages: int = 3) -> dict[str, Any]:
    if not client_id or not client_secret:
        return not_configured("kick")
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post("https://id.kick.com/oauth/token", data={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        record_raw(store.database_url, job_id, "research.kick", "oauth_token", token_response)
        token_response.raise_for_status()
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
        rows, cursor, raw_id = [], None, None
        for _ in range(max(1, min(pages, 10))):
            params = {"limit": 1000, **({"cursor": cursor} if cursor else {})}
            response = await client.get("https://api.kick.com/public/v2/livestreams", headers=headers, params=params)
            raw_id = record_raw(store.database_url, job_id, "research.kick", "kick_livestreams", response)
            response.raise_for_status()
            payload = response.json(); rows.extend(payload.get("data", []))
            cursor = payload.get("pagination", {}).get("next_cursor")
            if not cursor: break
    needle = query.lower(); matched = []
    for row in rows:
        category = row.get("category") or {}; channel = row.get("channel") or {}; user = row.get("broadcaster_user") or {}
        haystack = " ".join(str(x) for x in (category.get("name"), channel.get("slug"), user.get("username"), row.get("stream_title"))).lower()
        if needle not in haystack: continue
        slug = channel.get("slug") or user.get("username") or "unknown"
        url = f"https://kick.com/{slug}"
        result = store.add_evidence(job_id, source_type="kick", url=url, title=str(row.get("stream_title") or slug),
                                    excerpt=f"{slug}: {row.get('viewer_count', 0)} viewers in {category.get('name', 'unknown')}.", query=query, metadata=row)
        matched.append({**result, "url": url, "stream": row})
    return {"query": query, "scanned": len(rows), "matched": len(matched), "items": matched, "raw_response_id": raw_id}


async def read_discord_channel(store: ResearchStore, job_id: int, bot_token: str, channel_id: str, limit: int = 50) -> dict[str, Any]:
    if not bot_token:
        return not_configured("discord_messages")
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bot {bot_token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        channel_response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}")
        record_raw(store.database_url, job_id, "research.discord", "discord_channel", channel_response)
        if channel_response.status_code == 403:
            raise ValueError("The Oyster bot cannot view this channel. Grant View Channel before collecting messages.")
        channel_response.raise_for_status()
        guild_id = channel_response.json().get("guild_id")
        response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}/messages", params={"limit": max(1, min(limit, 100))})
        raw_id = record_raw(store.database_url, job_id, "research.discord", "discord_messages", response)
        if response.status_code == 403:
            raise ValueError("The Oyster bot cannot read this channel. Grant View Channel and Read Message History, and enable Message Content intent.")
        response.raise_for_status(); rows = response.json()
    stored = []
    for row in rows:
        content = str(row.get("content") or "").strip()
        if not content: continue
        author = row.get("author") or {}; url = f"https://discord.com/channels/{guild_id or '@me'}/{channel_id}/{row['id']}"
        result = store.add_evidence(job_id, source_type="discord_message", url=url, title=f"Discord message by {author.get('username', 'unknown')}",
                                    excerpt=content[:4000], author=author.get("username", ""),
                                    published_at=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) if row.get("timestamp") else None,
                                    metadata={"channel_id": channel_id, "message_id": row["id"]})
        stored.append({**result, "url": url, "author": author.get("username", ""), "text": content})
    return {"channel_id": channel_id, "received": len(rows), "stored": len(stored), "items": stored, "raw_response_id": raw_id}


async def crawl_web_page(store: ResearchStore, job_id: int, url: str, query: str = "") -> dict[str, Any]:
    """Fetch a public page with Scrapling and normalize its readable text and links."""
    _validate_public_url(url)
    import anyio
    from scrapling.fetchers import Fetcher

    response = await anyio.to_thread.run_sync(lambda: Fetcher.get(url, timeout=30, stealthy_headers=True))
    _validate_public_url(str(response.url))
    raw_id = record_raw_parts(
        store.database_url, job_id, "research.web", "web_page",
        method="GET", url=str(response.url), status=int(getattr(response, "status", 0) or 0),
        response_headers=dict(getattr(response, "headers", {}) or {}),
        content=(getattr(response, "body", None) or str(getattr(response, "html_content", "")).encode("utf-8", "replace")),
    )
    if response.status >= 400:
        raise ValueError(f"The page returned HTTP {response.status}.")
    title = (response.css("title::text").get() or url).strip()
    chunks = [str(text).strip() for text in response.css("body *::text").getall()]
    excerpt = " ".join(chunk for chunk in chunks if chunk)[:12000]
    if not excerpt:
        raise ValueError("The page did not contain extractable text. Try an Apify Actor or authorized browser route.")
    links = []
    for href in response.css("a::attr(href)").getall():
        absolute = urljoin(str(response.url), str(href))
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
        if len(links) >= 100:
            break
    result = store.add_evidence(job_id, source_type="web", url=str(response.url), title=title, excerpt=excerpt,
                                query=query, metadata={"status": response.status, "discovered_links": links, "raw_response_id": raw_id})
    return {**result, "url": str(response.url), "title": title, "text": excerpt, "discovered_links": links, "raw_response_id": raw_id}


# ---------------------------------------------------------------------------
# Discovery — the "where is this conversation happening?" primitives.
#
# search_web and discover_sources return *leads*, not stored evidence: the researcher
# decides which to pursue (read a thread, crawl a page). Only when substance is collected
# does it become add_evidence. Reddit search/thread fetch DO store, because they read the
# actual comments where "what people are saying" lives.
# ---------------------------------------------------------------------------

async def _search_tavily(store: ResearchStore, job_id: int, key: str, query: str, limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": _UA}) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": max(1, min(limit, 20)), "search_depth": "basic"},
        )
        record_raw(store.database_url, job_id, "research.search", "web_search", response)
        response.raise_for_status()
        payload = response.json()
    return [
        {"title": _plain_text(row.get("title", "")), "url": str(row.get("url", "")),
         "snippet": _plain_text(row.get("content", ""))}
        for row in payload.get("results", []) if row.get("url")
    ]


async def _search_brave(store: ResearchStore, job_id: int, key: str, query: str, limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": _UA}) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max(1, min(limit, 20))},
        )
        record_raw(store.database_url, job_id, "research.search", "web_search", response)
        response.raise_for_status()
        payload = response.json()
    return [
        {"title": _plain_text(row.get("title", "")), "url": str(row.get("url", "")),
         "snippet": _plain_text(row.get("description", ""))}
        for row in payload.get("web", {}).get("results", []) if row.get("url")
    ]


async def _search_serper(store: ResearchStore, job_id: int, key: str, query: str, limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": _UA}) as client:
        response = await client.post("https://google.serper.dev/search", json={"q": query, "num": max(1, min(limit, 20))})
        record_raw(store.database_url, job_id, "research.search", "web_search", response)
        response.raise_for_status()
        payload = response.json()
    return [
        {"title": _plain_text(row.get("title", "")), "url": str(row.get("link", "")),
         "snippet": _plain_text(row.get("snippet", ""))}
        for row in payload.get("organic", []) if row.get("link")
    ]


def _ddg_clean_url(href: str) -> str:
    """DuckDuckGo HTML wraps results in /l/?uddg=<encoded>. Unwrap to the real URL."""
    href = _html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


_DDG_RESULT = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DDG_SNIPPET = re.compile(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.DOTALL)


async def _search_duckduckgo(store: ResearchStore, job_id: int, query: str, limit: int) -> list[dict[str, Any]]:
    """Free, no-key fallback. Best-effort: DuckDuckGo may rate-limit or change its HTML."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
        record_raw(store.database_url, job_id, "research.search", "web_search", response)
        response.raise_for_status()
        html_text = response.text
    hits = _DDG_RESULT.findall(html_text)
    snippets = [_plain_text(match) for match in _DDG_SNIPPET.findall(html_text)]
    results = []
    for index, (href, title_html) in enumerate(hits[: max(1, min(limit, 20))]):
        url = _ddg_clean_url(href)
        if not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": _plain_text(title_html),
            "url": url,
            "snippet": snippets[index] if index < len(snippets) else "",
        })
    return results


async def search_web(store: ResearchStore, job_id: int, query: str, limit: int = 10, *,
                     tavily_key: str = "", brave_key: str = "", serper_key: str = "") -> dict[str, Any]:
    """Search the open web and return ranked {title, url, snippet} leads (not stored as evidence).

    A configured Tavily/Brave/Serper key makes results reliable; with no key, a free
    DuckDuckGo HTML provider is used (best-effort, may be rate-limited). The researcher
    decides which leads to actually read and turn into evidence.
    """
    if not query.strip():
        raise ValueError("Provide a search query.")
    if tavily_key:
        provider, results = "tavily", await _search_tavily(store, job_id, tavily_key, query, limit)
    elif brave_key:
        provider, results = "brave", await _search_brave(store, job_id, brave_key, query, limit)
    elif serper_key:
        provider, results = "serper", await _search_serper(store, job_id, serper_key, query, limit)
    else:
        provider, results = "duckduckgo", await _search_duckduckgo(store, job_id, query, limit)
    return {
        "query": query,
        "provider": provider,
        "count": len(results),
        "results": results,
        "note": (
            "These are leads, not evidence. Read the promising ones (crawl_web_page, "
            "fetch_reddit_thread) and store what actually answers the question with add_evidence."
            if results else
            "No results. If provider is 'duckduckgo' it may be rate-limited — retry, rephrase, "
            "or add a TAVILY_API_KEY/BRAVE_API_KEY/SERPER_API_KEY for reliable search."
        ),
    }


_VENUE_RULES: list[tuple[str, str]] = [
    ("reddit", r"(^|\.)reddit\.com$"),
    ("youtube", r"(^|\.)(youtube\.com|youtu\.be)$"),
    ("x", r"(^|\.)(x\.com|twitter\.com)$"),
    ("discord", r"(^|\.)(discord\.gg|discord\.com|discordapp\.com)$"),
    ("twitch", r"(^|\.)twitch\.tv$"),
    ("kick", r"(^|\.)kick\.com$"),
    ("tiktok", r"(^|\.)tiktok\.com$"),
    ("facebook", r"(^|\.)(facebook\.com|instagram\.com)$"),
]
_FORUM_HINT = re.compile(r"forum|community|/board|viewtopic|/t/|/threads?/", re.IGNORECASE)


def _classify_venue(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for venue, pattern in _VENUE_RULES:
        if re.search(pattern, host):
            return venue
    if _FORUM_HINT.search(url):
        return "forum"
    return "news_or_web"


async def discover_sources(store: ResearchStore, job_id: int, topic: str, *,
                           tavily_key: str = "", brave_key: str = "", serper_key: str = "") -> dict[str, Any]:
    """Find where a topic is actually discussed: run targeted searches and group hits by venue.

    Answers "where is this conversation happening?" — returns leads grouped into reddit,
    youtube, x, discord, twitch, kick, forum, and news_or_web so the researcher can go read
    the real discussion. Nothing is stored; these are directions, not evidence.
    """
    if not topic.strip():
        raise ValueError("Provide a topic to discover sources for.")
    probes = [
        topic,
        f"{topic} reddit",
        f"{topic} discussion OR forum OR community",
        f"{topic} review OR opinion OR reaction",
    ]
    seen: set[str] = set()
    venues: dict[str, list[dict[str, Any]]] = {}
    for probe in probes:
        found = await search_web(store, job_id, probe, limit=10,
                                 tavily_key=tavily_key, brave_key=brave_key, serper_key=serper_key)
        for lead in found["results"]:
            url = lead["url"]
            key = urlparse(url)._replace(query="", fragment="").geturl()
            if key in seen:
                continue
            seen.add(key)
            venues.setdefault(_classify_venue(url), []).append(lead)
    ordered = ["reddit", "forum", "youtube", "x", "discord", "twitch", "kick", "tiktok", "facebook", "news_or_web"]
    grouped = {venue: venues[venue] for venue in ordered if venue in venues}
    return {
        "topic": topic,
        "total_leads": sum(len(items) for items in grouped.values()),
        "venues": grouped,
        "note": (
            "Go where the conversation is. For reddit venues use search_reddit / "
            "fetch_reddit_thread; for forums/news use crawl_web_page; note x/discord/twitch/kick "
            "leads but remember live chat and private servers need the supervised browser route."
        ),
    }


def _reddit_json_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not (host == "reddit.com" or host.endswith(".reddit.com")):
        raise ValueError("Provide a reddit.com thread URL.")
    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        return f"https://www.reddit.com{path}"
    return f"https://www.reddit.com{path}.json"


def _reddit_blocked(status: int) -> ValueError:
    return ValueError(
        f"Reddit returned HTTP {status} (it rate-limits anonymous requests). "
        "Retry shortly, or route around it with search_web / crawl_web_page."
    )


async def search_reddit(store: ResearchStore, job_id: int, query: str, limit: int = 25,
                        subreddit: str = "") -> dict[str, Any]:
    """Search Reddit's public JSON for posts about a topic and store them as evidence.

    Free, no key. Optionally restrict to one subreddit. Stores each matching post as
    source_type='reddit' evidence with author, score, and permalink provenance.
    """
    if not query.strip():
        raise ValueError("Provide a Reddit search query.")
    sub = subreddit.strip().lstrip("r/").strip("/")
    base = f"https://www.reddit.com/r/{sub}/search.json" if sub else "https://www.reddit.com/search.json"
    params = {"q": query, "limit": max(1, min(limit, 100)), "sort": "relevance", "type": "link",
              "raw_json": 1, **({"restrict_sr": "true"} if sub else {})}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        response = await client.get(base, params=params)
        raw_id = record_raw(store.database_url, job_id, "research.reddit", "reddit_search", response)
        if response.status_code in (403, 429):
            raise _reddit_blocked(response.status_code)
        response.raise_for_status()
        payload = response.json()
    stored = []
    for child in payload.get("data", {}).get("children", []):
        post = child.get("data", {})
        permalink = post.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink else str(post.get("url", ""))
        title = _plain_text(post.get("title", ""))
        body = _plain_text(post.get("selftext", "")) or title
        result = store.add_evidence(
            job_id, source_type="reddit", url=url, title=title,
            excerpt=body[:4000], author=post.get("author", ""),
            published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc) if post.get("created_utc") else None,
            query=query,
            metadata={"subreddit": post.get("subreddit"), "score": post.get("score"),
                      "num_comments": post.get("num_comments"), "permalink": permalink},
        )
        stored.append({**result, "url": url, "title": title, "subreddit": post.get("subreddit"),
                       "score": post.get("score"), "num_comments": post.get("num_comments")})
    return {"query": query, "subreddit": sub or None, "matched": len(stored), "items": stored, "raw_response_id": raw_id}


def _walk_comments(node: Any, out: list[dict[str, Any]], limit: int) -> None:
    """Depth-first collect message-bearing comment bodies from a Reddit listing tree."""
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        if node.get("kind") == "t1":
            data = node.get("data", {})
            body = _plain_text(data.get("body", ""))
            if body and body not in ("[deleted]", "[removed]"):
                out.append({"author": data.get("author", ""), "score": data.get("score"),
                            "body": body, "created_utc": data.get("created_utc")})
        for value in node.values():
            _walk_comments(value, out, limit)
    elif isinstance(node, list):
        for item in node:
            _walk_comments(item, out, limit)


async def fetch_reddit_thread(store: ResearchStore, job_id: int, url: str, max_comments: int = 40) -> dict[str, Any]:
    """Fetch a Reddit thread's post + top comments (public JSON) and store them as evidence.

    This is where "what people are actually saying" lives — the comment bodies, with their
    authors and scores. Free, no key. Stores the post and each substantive comment as
    source_type='reddit' evidence.
    """
    json_url = _reddit_json_url(url)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": _UA}) as client:
        response = await client.get(json_url, params={"raw_json": 1, "limit": max(1, min(max_comments, 200))})
        raw_id = record_raw(store.database_url, job_id, "research.reddit", "reddit_thread", response)
        if response.status_code in (403, 429):
            raise _reddit_blocked(response.status_code)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list) or len(payload) < 1:
        raise ValueError("That URL did not return a Reddit thread.")
    post = payload[0].get("data", {}).get("children", [{}])[0].get("data", {})
    thread_url = f"https://www.reddit.com{post.get('permalink', '')}" if post.get("permalink") else json_url
    subreddit = post.get("subreddit")
    title = _plain_text(post.get("title", ""))
    stored = []
    if post:
        body = _plain_text(post.get("selftext", "")) or title
        result = store.add_evidence(
            job_id, source_type="reddit", url=thread_url, title=title, excerpt=body[:4000],
            author=post.get("author", ""),
            published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc) if post.get("created_utc") else None,
            metadata={"subreddit": subreddit, "score": post.get("score"),
                      "num_comments": post.get("num_comments"), "kind": "post"},
        )
        stored.append({**result, "kind": "post", "title": title, "score": post.get("score")})
    comments: list[dict[str, Any]] = []
    if len(payload) > 1:
        _walk_comments(payload[1], comments, max(1, min(max_comments, 200)))
    for comment in comments:
        excerpt = comment["body"][:4000]
        result = store.add_evidence(
            job_id, source_type="reddit", url=thread_url,
            title=f"Comment by u/{comment['author']} in r/{subreddit}" if subreddit else f"Comment by u/{comment['author']}",
            excerpt=excerpt, author=comment["author"],
            published_at=datetime.fromtimestamp(comment["created_utc"], tz=timezone.utc) if comment.get("created_utc") else None,
            metadata={"subreddit": subreddit, "score": comment.get("score"), "kind": "comment", "thread": title},
        )
        stored.append({**result, "kind": "comment", "author": comment["author"], "score": comment.get("score"),
                       "excerpt": excerpt})
    return {"url": thread_url, "title": title, "subreddit": subreddit, "post_stored": bool(post),
            "comments_stored": len(comments), "items": stored, "raw_response_id": raw_id}
