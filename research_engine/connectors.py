from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
import re
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from research_engine.store import ResearchStore


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
        response.raise_for_status()
    _validate_public_url(str(response.url))
    parsed = feedparser.loads(response.content)
    terms = [term.lower() for term in query_terms if term.strip()]
    stored = []
    for entry in parsed.entries[: max(1, min(limit, 200))]:
        title = str(entry.get("title", ""))
        excerpt = str(entry.get("summary", entry.get("description", title)))
        if terms and not any(term in f"{title} {excerpt}".lower() for term in terms):
            continue
        result = store.add_evidence(
            job_id, source_type="rss", url=str(entry.get("link", feed_url)), title=title,
            excerpt=excerpt[:4000], author=str(entry.get("author", "")), published_at=_published(entry),
            query=" OR ".join(query_terms), metadata={"feed_url": feed_url},
        )
        stored.append({**result, "title": title, "url": str(entry.get("link", feed_url))})
    return {"feed_url": feed_url, "matched": len(stored), "items": stored}


async def search_x(store: ResearchStore, job_id: int, bearer_token: str, query: str, max_results: int = 25) -> dict[str, Any]:
    if not bearer_token:
        raise ValueError("X is not configured. Set X_BEARER_TOKEN in .env, then restart the MCP server.")
    params = {
        "query": query, "max_results": max(10, min(max_results, 100)),
        "tweet.fields": "created_at,author_id,conversation_id,lang,public_metrics",
        "expansions": "author_id", "user.fields": "name,username,verified",
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {bearer_token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.get("https://api.x.com/2/tweets/search/recent", params=params)
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
    return {"query": query, "matched": len(stored), "items": stored, "meta": payload.get("meta", {})}


async def inspect_discord_invite(store: ResearchStore, job_id: int, invite_url_or_code: str) -> dict[str, Any]:
    """Inspect public invite metadata for a community discovered during research."""
    match = re.search(r"(?:discord(?:app)?\.com/invite/|discord\.gg/)?([A-Za-z0-9-]+)$", invite_url_or_code.strip())
    if not match:
        raise ValueError("Provide a Discord invite code or a discord.gg invite URL.")
    code = match.group(1)
    url = f"https://discord.com/api/v10/invites/{code}"
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.get(url, params={"with_counts": "true", "with_expiration": "true"})
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
    return {**result, "invite_code": code, "guild_name": name, "member_count": members, "presence_count": online}


async def run_apify_actor(store: ResearchStore, job_id: int, token: str, actor_id: str,
                          actor_input: dict[str, Any], source_type: str, limit: int = 100) -> dict[str, Any]:
    if not token:
        raise ValueError("Apify is not configured. Paste APIFY_TOKEN in Setup, then restart the MCP host.")
    actor = actor_id.strip().replace("/", "~")
    if not actor:
        raise ValueError("Choose an Apify Actor ID, such as owner/actor-name.")
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    clamped_limit = max(1, min(limit, 1000))
    async with httpx.AsyncClient(timeout=300, headers={"Authorization": f"Bearer {token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        response = await client.post(url, params={"clean": "true", "limit": clamped_limit}, json=actor_input)
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
    return {"actor_id": actor_id, "received": len(rows), "stored": len(stored), "items": stored}


async def search_twitch(store: ResearchStore, job_id: int, client_id: str, client_secret: str,
                        query: str, limit: int = 40) -> dict[str, Any]:
    if not client_id or not client_secret:
        raise ValueError("Twitch is not configured. Add its Client ID and Client Secret in Setup.")
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post("https://id.twitch.tv/oauth2/token", params={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        token_response.raise_for_status()
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}", "Client-Id": client_id}
        response = await client.get("https://api.twitch.tv/helix/search/channels", headers=headers,
                                    params={"query": query, "live_only": "false", "first": max(1, min(limit, 100))})
        response.raise_for_status()
        rows = response.json().get("data", [])
    stored = []
    for row in rows:
        url = f"https://twitch.tv/{row['broadcaster_login']}"
        excerpt = f"{row.get('display_name')}: {row.get('title') or 'No current title'}; category {row.get('game_name') or 'unknown'}; live={row.get('is_live', False)}."
        result = store.add_evidence(job_id, source_type="twitch", url=url, title=row.get("display_name", ""), excerpt=excerpt,
                                    query=query, metadata=row)
        stored.append({**result, "url": url, **row})
    return {"query": query, "matched": len(stored), "items": stored}


async def search_kick(store: ResearchStore, job_id: int, client_id: str, client_secret: str,
                      query: str, pages: int = 3) -> dict[str, Any]:
    if not client_id or not client_secret:
        raise ValueError("Kick is not configured. Add its Client ID and Client Secret in Setup.")
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post("https://id.kick.com/oauth/token", data={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        token_response.raise_for_status()
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
        rows, cursor = [], None
        for _ in range(max(1, min(pages, 10))):
            params = {"limit": 1000, **({"cursor": cursor} if cursor else {})}
            response = await client.get("https://api.kick.com/public/v2/livestreams", headers=headers, params=params)
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
    return {"query": query, "scanned": len(rows), "matched": len(matched), "items": matched}


async def read_discord_channel(store: ResearchStore, job_id: int, bot_token: str, channel_id: str, limit: int = 50) -> dict[str, Any]:
    if not bot_token:
        raise ValueError("Discord message access is not configured. Add DISCORD_BOT_TOKEN in Setup and install that bot in the server.")
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bot {bot_token}", "User-Agent": "ResearchOyster/0.1"}) as client:
        channel_response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}")
        if channel_response.status_code == 403:
            raise ValueError("The Oyster bot cannot view this channel. Grant View Channel before collecting messages.")
        channel_response.raise_for_status()
        guild_id = channel_response.json().get("guild_id")
        response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}/messages", params={"limit": max(1, min(limit, 100))})
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
    return {"channel_id": channel_id, "received": len(rows), "stored": len(stored), "items": stored}


async def crawl_web_page(store: ResearchStore, job_id: int, url: str, query: str = "") -> dict[str, Any]:
    """Fetch a public page with Scrapling and normalize its readable text and links."""
    _validate_public_url(url)
    import anyio
    from scrapling.fetchers import Fetcher

    response = await anyio.to_thread.run_sync(lambda: Fetcher.get(url, timeout=30, stealthy_headers=True))
    _validate_public_url(str(response.url))
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
                                query=query, metadata={"status": response.status, "discovered_links": links})
    return {**result, "url": str(response.url), "title": title, "text": excerpt, "discovered_links": links}
