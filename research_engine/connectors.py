from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import html as _html
import httpx
import json
import os
import re
import ipaddress
import socket
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote

_TAG_RE = re.compile(r"<[^>]+>")

# A stable, honest User-Agent. Reddit's public JSON and DuckDuckGo's HTML endpoint both
# rate-limit anonymous clients; a descriptive UA is the difference between best-effort
# working and being blocked outright.
_UA = "ResearchOyster/0.2 (+https://github.com/zandemha2025/research-oyster)"
# Many sites (Rotten Tomatoes, news outlets) serve a real page to a browser UA but block or
# empty a bot UA. Page crawling uses this; RSS/API calls keep the honest _UA above.
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


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
        "enables": ["Reddit", "X/tweet scraping (use a tweet/search actor, not a profile scraper)", "web/community discovery", "arbitrary Actors"],
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
            "run_apify_actor with a TWEET/SEARCH actor (e.g. apidojo/tweet-scraper) and searchTerms input, source_type='x' — this returns tweet TEXT. Do NOT use a profile/user scraper, which returns only account metadata (followers, handles) and no post content.",
            "search_web for the topic to find where it is discussed publicly",
            "search_reddit — much of the same conversation happens on Reddit, for free",
            "crawl_web_page on public posts, threads, or news coverage",
        ],
    },
    "twitch": {
        "setup": "Add a Twitch developer Client ID and Client Secret in Setup (only needed for channel/category SEARCH; reading chat needs nothing).",
        "fallbacks": [
            "read_twitch_chat(channel) — read a channel's LIVE chat via anonymous IRC, no credentials required",
            "search_web / search_reddit for what viewers say about the streams",
            "crawl_web_page on public stats pages such as twitchtracker.com or sullygnome.com",
        ],
    },
    "kick": {
        "setup": "No key needed — search_kick works free via Kick's public API (real-browser "
                 "fingerprint). Optional Client ID/Secret in Setup switches to the official OAuth API.",
        "fallbacks": [
            "search_kick(query) — free: returns channel followers, live status, viewer counts, and category",
            "read_kick_chat(channel) — free: reads a live channel's actual chat messages (Pusher websocket)",
            "search_web / search_reddit for the surrounding discussion (free)",
            "crawl_web_page on public kick.com category and channel pages",
        ],
    },
    "discord_public": {
        "scope": "Public community landscape + live activity, no token: discord_landscape(topic) maps the "
                 "relevant servers with member/online counts + voice activity; discord_widget reads a "
                 "server's live online members and voice channels; inspect_discord_invite for a single invite. "
                 "For message content that leaked publicly, use search_reddit / search_web / crawl_web_page.",
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
    # kick omitted → always ready: search_kick now works free via the public API path.
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


# --- Discord public "exhaust": landscape + live activity, no token, no ToS-gray access ---------
# Discord message content lives behind auth, but a lot of Discord SIGNAL spills onto the open web
# and through Discord's own PUBLIC endpoints. We stack every clean source: web search finds the
# servers + invite links; the invite API gives member/online counts; the public widget gives the
# live online-member list + active voice channels for servers that enabled it. That covers "how
# big, how active, who's around, what's announced" — most of what a brief needs — without touching
# a burner account.
_INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite/|discord\.gg/)([A-Za-z0-9-]{2,32})")


def _discord_invite_codes(text: str, limit: int = 25) -> list[str]:
    """Extract unique Discord invite codes from arbitrary text (search results, crawled pages)."""
    out: list[str] = []
    for code in _INVITE_RE.findall(text or ""):
        if code not in out:
            out.append(code)
        if len(out) >= limit:
            break
    return out


async def discord_widget(store: ResearchStore, job_id: int, invite_or_guild: str) -> dict[str, Any]:
    """Read a server's PUBLIC widget: live online count, a sample of online members with status,
    and active voice channels — for any server with its widget enabled. Accepts a guild id, an
    invite code, or an invite URL. No token. A widget-disabled server returns widget_enabled=False
    (not an error) — the invite-API landscape counts still work via inspect_discord_invite."""
    val = str(invite_or_guild).strip()
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "ResearchOyster/0.2"}) as client:
        guild_id = val if val.isdigit() else None
        name = None
        if guild_id is None:
            match = _INVITE_RE.search(val)
            code = match.group(1) if match else val.rsplit("/", 1)[-1]
            inv = await client.get(f"https://discord.com/api/v10/invites/{code}", params={"with_counts": "true"})
            record_raw(store.database_url, job_id, "research.discord", "discord_invite", inv)
            if inv.status_code != 200:
                raise ValueError("That Discord invite is invalid or expired.")
            guild = inv.json().get("guild", {})
            guild_id, name = guild.get("id"), guild.get("name")
        widget = await client.get(f"https://discord.com/api/guilds/{guild_id}/widget.json")
        raw_id = record_raw(store.database_url, job_id, "research.discord", "discord_widget", widget)
        if widget.status_code == 403:
            return {"guild_id": guild_id, "widget_enabled": False,
                    "note": "This server has its public widget disabled; landscape member/online counts "
                            "are still available via inspect_discord_invite."}
        widget.raise_for_status()
        data = widget.json()
    name = data.get("name") or name or str(guild_id)
    members = data.get("members") or []
    channels = data.get("channels") or []
    online = data.get("presence_count")
    sample = [f"{m.get('username')} [{m.get('status')}]" for m in members[:15] if m.get("username")]
    voice = [c.get("name") for c in channels if c.get("name")][:15]
    excerpt = (f"{name}: {online if online is not None else 'unknown'} online now; "
               f"{len(members)} members shown; {len(channels)} active voice channels"
               + (f" ({', '.join(voice)})" if voice else "") + ".")
    metrics: dict[str, int] = {"voice_channels": len(channels)}
    if online is not None:
        metrics["online"] = online
    result = store.add_evidence(
        job_id, source_type="discord", url=f"https://discord.com/channels/{guild_id}", title=name,
        excerpt=excerpt, metadata={"entity": name, "metrics": metrics, "widget_enabled": True,
                                   "online_members_sample": sample, "voice_channels": voice,
                                   "guild_id": guild_id, "raw_response_id": raw_id})
    return {**result, "guild_id": guild_id, "name": name, "widget_enabled": True, "online": online,
            "voice_channels": len(channels), "online_members_sample": sample, "raw_response_id": raw_id}


async def discord_landscape(store: ResearchStore, job_id: int, topic: str, limit: int = 6) -> dict[str, Any]:
    """Turn a topic into a ranked map of the relevant Discord communities using ONLY public data:
    web search finds the servers + invite links, then each is enriched with the invite API
    (member/online counts) and the public widget (live online + voice activity). Stores one
    consolidated row per server with the numbers under metadata.metrics so metrics.py can rank
    them. No token, no ToS-gray access — landscape + activity, not message content."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("Provide a topic to map Discord communities for.")
    limit = max(1, min(limit, 12))

    # 1) Seed likely VANITY invite codes from the topic — big communities usually own
    # discord.gg/<name> (verified: valorant→2.5M, fortnite→1.8M). Invalid seeds 404 and are
    # dropped in step 2, so this only ever helps.
    codes: list[str] = []
    for seed in (re.sub(r"[^a-z0-9]", "", topic.lower()), re.sub(r"[^a-z0-9]", "-", topic.lower()).strip("-")):
        if seed and seed not in codes:
            codes.append(seed)

    # 2) Discover more via open-web search (finds discord.gg links + directory pages).
    try:
        hits = await search_web(store, job_id, f"{topic} discord server invite", limit=10)
    except Exception:
        hits = []
    hitlist = hits if isinstance(hits, list) else (hits.get("results") or hits.get("items") or [])
    for hit in hitlist:
        for code in _discord_invite_codes(str(hit)):
            if code not in codes:
                codes.append(code)
    codes = codes[:limit]

    # 2) Enrich each server: invite counts + widget activity → one consolidated evidence row.
    communities: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "ResearchOyster/0.2"}) as client:
        for code in codes:
            try:
                inv = await client.get(f"https://discord.com/api/v10/invites/{code}", params={"with_counts": "true"})
                record_raw(store.database_url, job_id, "research.discord", "discord_invite", inv)
                if inv.status_code != 200:
                    continue
                payload = inv.json()
                guild = payload.get("guild", {})
                gid, name = guild.get("id"), (guild.get("name") or code)
                members = payload.get("approximate_member_count")
                online = payload.get("approximate_presence_count")
                voice: list[str] = []
                widget_on = False
                wid = None
                try:
                    widget = await client.get(f"https://discord.com/api/guilds/{gid}/widget.json")
                    wid = record_raw(store.database_url, job_id, "research.discord", "discord_widget", widget)
                    if widget.status_code == 200:
                        widget_on = True
                        wdata = widget.json()
                        voice = [c.get("name") for c in (wdata.get("channels") or []) if c.get("name")]
                        if online is None:
                            online = wdata.get("presence_count")
                except Exception:
                    pass
                metrics: dict[str, int] = {}
                if members is not None:
                    metrics["members"] = members
                if online is not None:
                    metrics["online"] = online
                excerpt = (f"{name}: {members if members is not None else 'unknown'} members, "
                           f"{online if online is not None else 'unknown'} online"
                           + (f"; {len(voice)} active voice channels" if widget_on else "") + ".")
                result = store.add_evidence(
                    job_id, source_type="discord", url=f"https://discord.gg/{code}", title=name,
                    excerpt=excerpt, query=topic,
                    metadata={"entity": name, "query_shape": topic, "metrics": metrics,
                              "invite_code": code, "guild_id": gid, "widget_enabled": widget_on,
                              "voice_channels": voice, "raw_response_id": wid})
                communities.append({**result, "name": name, "members": members, "online": online,
                                    "widget_enabled": widget_on, "voice_channels": len(voice),
                                    "invite_code": code})
            except Exception:
                continue
    communities.sort(key=lambda c: (c.get("members") or 0), reverse=True)
    return {"topic": topic, "discovered": len(codes), "matched": len(communities),
            "communities": communities,
            "note": "Public landscape only (member/online counts + widget activity). For message "
                    "content, the same discussion is usually reachable via search_reddit/search_web "
                    "and any announcement mirrors; full in-channel reads require the opt-in path."}


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


async def apify_collect(store: ResearchStore, job_id: int, apify_token: str, platform: str,
                        intent: str = "", query: str = "", limit: int = 30, days: int = 90,
                        platform_token: str = "") -> dict[str, Any]:
    """Curated, numbers-aware Apify collection: pick the right actor for (platform, intent), run it,
    enforce the date window locally, extract normalized metrics, and store rich evidence. This is the
    'never a no' path — with the key set, a named platform returns real data + numbers."""
    from research_engine import apify as engine

    if not apify_token:
        return not_configured("apify")
    spec = engine.resolve(platform, intent)
    if spec is None:
        return {"error": f"No curated Apify actor for {platform}/{intent or 'default'}.",
                "supported": sorted({p for p, _ in engine.REGISTRY})}
    if spec.opt_in and not platform_token:
        return {"opt_in_required": True, "platform": platform, "note": spec.note or
                "This source is opt-in and needs an extra token; using landscape/other sources instead."}

    actor_input = engine.build_input(spec, query, limit, days)
    if platform_token:  # e.g. Discord burner user token injected into the actor input
        actor_input = {**actor_input, "token": platform_token}
    actor = spec.actor.replace("/", "~")
    clamped = engine.clamp_limit(limit)
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    async with httpx.AsyncClient(timeout=300, headers={"Authorization": f"Bearer {apify_token}", "User-Agent": _UA}) as client:
        response = await client.post(url, params={"clean": "true", "limit": clamped}, json=actor_input)
        raw_id = record_raw(store.database_url, job_id, f"research.apify.{platform}", "apify_dataset", response)
        response.raise_for_status()
        rows = response.json()

    stored, kept = [], 0
    for raw in rows[:clamped]:
        row = raw if isinstance(raw, dict) else {"value": raw}
        if not engine.within_window(row, days):
            continue
        kept += 1
        norm = engine.normalize_row(row, query_shape=query)
        target = str(row.get("url") or row.get("link") or row.get("postUrl") or row.get("webVideoUrl")
                     or row.get("tweetUrl") or f"apify://{spec.actor}")
        title = str(row.get("title") or row.get("name") or norm["entity"] or spec.kind)
        excerpt = str(row.get("text") or row.get("caption") or row.get("body") or row.get("description")
                      or row.get("content") or row.get("title") or "")[:4000]
        res = store.add_evidence(job_id, source_type=platform.lower(), url=target, title=title, excerpt=excerpt,
                                 author=norm["entity"], query=query,
                                 metadata={"actor": spec.actor, "record": row, "metrics": norm["metrics"],
                                           "entity": norm["entity"], "query_shape": query, "raw_response_id": raw_id})
        stored.append({"entity": norm["entity"], "metrics": norm["metrics"], "evidence_id": res["evidence_id"]})

    metrics_present = sorted({m for s in stored for m in s["metrics"]})
    return {"platform": platform, "intent": intent or engine.DEFAULT_INTENT.get(platform.lower()),
            "actor": spec.actor, "received": len(rows), "kept_in_window": kept, "stored": len(stored),
            "sample_n": len(stored), "metrics_present": metrics_present, "items": stored[:20],
            "raw_response_id": raw_id}


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
    # No OAuth creds? Use the free public path (real-browser fingerprint) instead of declining.
    if not client_id or not client_secret:
        return await search_kick_public(store, job_id, query)
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


# --- Kick, no credentials -------------------------------------------------------------------
# Kick sits behind Cloudflare, which 403s a plain UA from a datacenter IP but serves a real
# browser TLS fingerprint. curl_cffi's Safari impersonation gets through (verified live), so the
# free Kick lane needs no OAuth app — same spirit as the Scrapling fallback in crawl_web_page.
# curl_cffi is synchronous and bypasses the agent proxy by design; run it in a thread.
_KICK_IMPERSONATE = ("safari17_0", "safari15_5", "safari15_3")


def _kick_get_blocking(path: str) -> tuple[int, bytes, str]:
    """GET a public kick.com JSON endpoint with a real-browser fingerprint. Returns
    (status, body, final_url); status 0 means the request could not be made at all."""
    from curl_cffi import requests as creq

    url = path if path.startswith("http") else f"https://kick.com{path}"
    last: tuple[int, bytes, str] = (0, b"", url)
    for imp in _KICK_IMPERSONATE:
        try:
            r = creq.get(url, impersonate=imp, timeout=25, headers={"Accept": "application/json"})
            last = (r.status_code, r.content, str(r.url))
            if r.status_code == 200:
                return last
        except Exception:
            continue
    return last


def _kick_extract(channel: dict[str, Any], slug: str = "") -> dict[str, Any]:
    """Pull the numbers a report needs out of a Kick payload. Pure + tolerant of both the
    v2/channels/{slug} shape (livestream nested) and the /api/search channels shape (isLive flat)."""
    def _int(v: Any) -> int | None:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    slug = slug or channel.get("slug") or (channel.get("user") or {}).get("username") or ""
    followers = channel.get("followers_count")
    if followers is None:
        followers = channel.get("followersCount")
    live_obj = channel.get("livestream") if isinstance(channel.get("livestream"), dict) else None
    is_live = bool(live_obj) or bool(channel.get("isLive")) or bool(channel.get("is_live"))
    viewers = category = title = None
    if live_obj:
        viewers = live_obj.get("viewer_count") or live_obj.get("viewers")
        cats = live_obj.get("categories") or []
        if cats and isinstance(cats[0], dict):
            category = cats[0].get("name")
        title = live_obj.get("session_title") or live_obj.get("stream_title")
    if category is None:
        rc = channel.get("recentCategories") or []
        if rc and isinstance(rc[0], dict):
            category = rc[0].get("name")
    return {"slug": slug, "followers": _int(followers), "is_live": is_live,
            "viewers": _int(viewers), "category": category, "title": title}


async def search_kick_public(store: ResearchStore, job_id: int, query: str, limit: int = 6) -> dict[str, Any]:
    """No-credential Kick lane: resolve channels for a keyword via the public search endpoint,
    then pull each channel's authoritative stats (followers, live status, viewers, category).
    Stores each as evidence with the numbers under metadata.metrics so metrics.py can aggregate.
    query may also be a direct channel slug."""
    import anyio
    import json as _json

    query = (query or "").strip()
    if not query:
        raise ValueError("Provide a search term or a Kick channel slug.")
    limit = max(1, min(limit, 15))

    # 1. Discover slugs from the keyword (search covers channels + live streams).
    status, body, url = await anyio.to_thread.run_sync(
        lambda: _kick_get_blocking(f"/api/search?searched_word={quote(query)}"))
    search_raw = record_raw_parts(store.database_url, job_id, "research.kick", "kick_search",
                                  method="GET", url=url, status=status, content=body)
    slugs: list[str] = []
    if status == 200 and body:
        try:
            payload = _json.loads(body)
        except Exception:
            payload = {}
        for ch in (payload.get("channels") or []):
            if not isinstance(ch, dict):
                continue
            slug = ch.get("slug") or (ch.get("user") or {}).get("username")
            if slug and slug not in slugs:
                slugs.append(slug)
        for ls in (payload.get("livestreams") or []):
            if not isinstance(ls, dict):
                continue
            chan = ls.get("channel")
            slug = (chan.get("slug") if isinstance(chan, dict) else None) or ls.get("slug")
            if slug and slug not in slugs:
                slugs.append(slug)
    # Also try the query itself as a slug (handles "pull kick.com/<slug>" style asks).
    candidate = re.sub(r"[^a-z0-9_]", "", query.lower())
    if candidate and candidate not in slugs:
        slugs.append(candidate)
    slugs = slugs[:limit]

    # 2. Pull each channel's authoritative stats.
    stored: list[dict[str, Any]] = []
    scanned = 0
    raw_id = search_raw
    for slug in slugs:
        st, cbody, curl = await anyio.to_thread.run_sync(
            lambda s=slug: _kick_get_blocking(f"/api/v2/channels/{s}"))
        scanned += 1
        rid = record_raw_parts(store.database_url, job_id, "research.kick", "kick_channel",
                               method="GET", url=curl, status=st, content=cbody)
        if st != 200 or not cbody:
            continue
        try:
            channel = _json.loads(cbody)
        except Exception:
            continue
        m = _kick_extract(channel, slug)
        if m["followers"] is None and not m["is_live"]:
            continue  # not a real channel result
        chan_url = f"https://kick.com/{m['slug'] or slug}"
        live = f"LIVE, {m['viewers'] or 0} viewers in {m['category'] or 'unknown'}" if m["is_live"] else "offline"
        excerpt = (f"{m['slug'] or slug}: "
                   f"{m['followers'] if m['followers'] is not None else 'unknown'} followers; {live}.")
        metrics: dict[str, int] = {}
        if m["followers"] is not None:
            metrics["followers"] = m["followers"]
        if m["viewers"] is not None:
            metrics["viewers"] = m["viewers"]
        result = store.add_evidence(
            job_id, source_type="kick", url=chan_url, title=str(m["title"] or m["slug"] or slug),
            excerpt=excerpt, query=query,
            metadata={"entity": m["slug"] or slug, "query_shape": query, "metrics": metrics,
                      "is_live": m["is_live"], "category": m["category"], "raw_response_id": rid,
                      "record": channel})
        stored.append({**result, "url": chan_url, **m})
        raw_id = rid or raw_id
    return {"query": query, "scanned": scanned, "matched": len(stored), "items": stored,
            "raw_response_id": raw_id, "via": "public"}


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


async def _reader_fetch(url: str, key: str) -> tuple[str, int]:
    """Server-side URL→markdown via a reader service (Jina Reader). Renders JS on the reader's
    servers, so JS-heavy pages come back as clean text with NO browser open. Best-effort."""
    headers = {"User-Agent": _UA, "X-Return-Format": "markdown"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=45, trust_env=True, follow_redirects=True) as client:
        response = await client.get(f"https://r.jina.ai/{url}", headers=headers)
    return (response.text or ""), response.status_code


def _md_title(md: str) -> str:
    for line in md.splitlines()[:6]:
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    match = re.search(r"^#\s+(.+)$", md, re.M)
    return match.group(1).strip() if match else ""


def _md_links(md: str) -> list[str]:
    out: list[str] = []
    for link in re.findall(r"\]\((https?://[^)\s]+)\)", md):
        if link not in out:
            out.append(link)
    return out


async def crawl_web_page(store: ResearchStore, job_id: int, url: str, query: str = "",
                         reader_key: str = "") -> dict[str, Any]:
    """Read a public page. When a web-reader key is set, use a server-side URL→markdown reader
    (renders JS, no browser open needed) — this fixes JS-heavy pages that otherwise return
    boilerplate. Otherwise fall back to httpx + Scrapling (proxy/CA-aware)."""
    _validate_public_url(url)

    # 1. Preferred when keyed: server-side reader (handles JS-heavy pages).
    if reader_key:
        try:
            md, status = await _reader_fetch(url, reader_key)
        except Exception:
            md, status = "", 0
        if md and status < 400 and len(md.strip()) >= 200:
            raw_id = record_raw_parts(store.database_url, job_id, "research.web", "web_reader",
                                      method="GET", url=url, status=status, content=md.encode("utf-8", "replace"))
            title = _md_title(md) or url
            excerpt = md.strip()[:12000]
            links = _md_links(md)[:100]
            result = store.add_evidence(job_id, source_type="web", url=url, title=title, excerpt=excerpt,
                                        query=query, metadata={"status": status, "discovered_links": links,
                                                               "raw_response_id": raw_id, "via": "reader"})
            return {**result, "url": url, "title": title, "text": excerpt, "discovered_links": links,
                    "raw_response_id": raw_id, "via": "reader"}

    # 2. Fallback: httpx + Scrapling (honors HTTPS_PROXY + CA bundle).
    from scrapling import Selector

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, trust_env=True,
                                 headers={"User-Agent": _BROWSER_UA}) as client:
        response = await client.get(url)
    final_url = str(response.url)
    _validate_public_url(final_url)
    raw_id = record_raw_parts(
        store.database_url, job_id, "research.web", "web_page",
        method="GET", url=final_url, status=response.status_code,
        response_headers=dict(response.headers), content=response.content,
    )
    if response.status_code >= 400:
        raise ValueError(f"The page returned HTTP {response.status_code}.")
    selector = Selector(response.text)
    title = (selector.css("title::text").get() or url).strip()
    chunks = [str(text).strip() for text in selector.css("body *::text").getall()]
    excerpt = " ".join(chunk for chunk in chunks if chunk)[:12000]
    if not excerpt:
        raise ValueError("The page did not contain extractable text. Try an Apify Actor or the web reader (add a key).")
    links = []
    for href in selector.css("a::attr(href)").getall():
        absolute = urljoin(final_url, str(href))
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
        if len(links) >= 100:
            break
    result = store.add_evidence(job_id, source_type="web", url=final_url, title=title, excerpt=excerpt,
                                query=query, metadata={"status": response.status_code, "discovered_links": links,
                                                       "raw_response_id": raw_id, "via": "httpx"})
    return {**result, "url": final_url, "title": title, "text": excerpt, "discovered_links": links,
            "raw_response_id": raw_id, "via": "httpx"}


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


# --- Twitch live chat via anonymous IRC-over-WebSocket ------------------------
# Twitch live chat IS autonomously retrievable: anyone can read any channel's chat with
# an anonymous "justinfan" nick — no Client ID, no OAuth. We use Twitch's WebSocket chat
# endpoint (wss://…:443) rather than raw IRC (tcp:6667) because 443 is what browsers use
# and, crucially, what a corporate/agent HTTPS CONNECT proxy will tunnel — raw IRC ports
# do not traverse such proxies. This replaces the old "live chat is not autonomously
# retrievable" defeatism.
_TWITCH_WS_URL = "wss://irc-ws.chat.twitch.tv:443"


def _parse_irc_line(line: str) -> dict[str, Any] | None:
    """Parse one raw Twitch IRC line. Pure and testable.

    Returns {"ping": token} for a PING (caller must PONG), {"user", "text"} for a chat
    message (PRIVMSG), or None for anything else (JOIN, server notices, etc.).
    """
    line = line.rstrip("\r\n")
    if not line:
        return None
    if line.startswith("PING"):
        return {"ping": line.split(" ", 1)[1] if " " in line else ":tmi.twitch.tv"}
    if line.startswith("@"):  # strip optional IRCv3 tags
        _, _, line = line.partition(" ")
    if " PRIVMSG " not in line:
        return None
    prefix, _, rest = line.partition(" PRIVMSG ")
    nick = prefix[1:].split("!", 1)[0].strip() if prefix.startswith(":") else prefix.split("!", 1)[0].strip()
    _, _, message = rest.partition(" :")
    message = message.strip()
    if not nick or not message:
        return None
    return {"user": nick, "text": message}


def _read_twitch_chat_blocking(channel: str, seconds: int, limit: int) -> tuple[list[dict[str, Any]], str]:
    """Anonymous read of a channel's live chat over WebSocket. Blocking — run in a thread.

    Routes through an HTTPS proxy when one is configured (HTTPS_PROXY), so it works both
    behind an agent/corporate egress proxy and on a direct connection.
    """
    import time as _time
    import websocket  # websocket-client

    channel = channel.lstrip("#").lower().strip()
    kwargs: dict[str, Any] = {"timeout": 8}
    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        kwargs["sslopt"] = {"ca_certs": ca_bundle}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        parsed_proxy = urlparse(proxy)
        kwargs.update(http_proxy_host=parsed_proxy.hostname, http_proxy_port=parsed_proxy.port, proxy_type="http")

    messages: list[dict[str, Any]] = []
    transcript: list[str] = []
    ws = websocket.create_connection(_TWITCH_WS_URL, **kwargs)
    try:
        ws.settimeout(2.0)
        nick = f"justinfan{(abs(hash(channel)) % 80000) + 1000}"
        ws.send("PASS SCHMOOPIIE")
        ws.send(f"NICK {nick}")
        ws.send(f"JOIN #{channel}")
        deadline = _time.monotonic() + max(2, min(seconds, 30))
        while _time.monotonic() < deadline and len(messages) < limit:
            try:
                data = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            for raw in str(data).split("\r\n"):
                if not raw:
                    continue
                transcript.append(raw)
                parsed = _parse_irc_line(raw)
                if not parsed:
                    continue
                if "ping" in parsed:
                    ws.send(f"PONG {parsed['ping']}")
                elif "text" in parsed:
                    messages.append(parsed)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return messages, "\n".join(transcript)


# Kick live chat runs on Pusher (not IRC). The app key + cluster are public constants baked into
# Kick's web client; reading a chatroom needs no login — the exact twin of the Twitch WSS reader.
_KICK_PUSHER_APP = "32cbd69e4b950bf97679"
_KICK_WS_URL = (f"wss://ws-us2.pusher.com/app/{_KICK_PUSHER_APP}"
                "?protocol=7&client=js&version=8.4.0&flash=false")


def _parse_kick_event(raw: str) -> dict[str, Any] | None:
    """Parse one raw Kick/Pusher frame. Pure and testable.

    Returns {"ping": True} for a pusher:ping (caller must pong), {"user", "text", "created_at"}
    for a chat message, or None for anything else (subscription acks, presence, etc.)."""
    try:
        event = json.loads(raw)
    except Exception:
        return None
    name = event.get("event", "")
    if name == "pusher:ping":
        return {"ping": True}
    if name.endswith("ChatMessageEvent"):
        data = event.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return None
        if not isinstance(data, dict):
            return None
        sender = data.get("sender") or {}
        text = str(data.get("content") or "").strip()
        user = sender.get("username") or sender.get("slug") or ""
        if not text or not user:
            return None
        return {"user": user, "text": text, "created_at": data.get("created_at")}
    return None


def _read_kick_chat_blocking(chatroom_id: Any, seconds: int, limit: int) -> tuple[list[dict[str, Any]], str]:
    """Anonymous read of a Kick chatroom's live chat over the Pusher WebSocket. Blocking — run in
    a thread. Proxy/CA-aware like the Twitch reader (wss on :443 tunnels through HTTPS_PROXY)."""
    import time as _time
    import websocket  # websocket-client

    kwargs: dict[str, Any] = {"timeout": 8}
    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        kwargs["sslopt"] = {"ca_certs": ca_bundle}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        parsed_proxy = urlparse(proxy)
        kwargs.update(http_proxy_host=parsed_proxy.hostname, http_proxy_port=parsed_proxy.port, proxy_type="http")

    messages: list[dict[str, Any]] = []
    transcript: list[str] = []
    ws = websocket.create_connection(_KICK_WS_URL, **kwargs)
    try:
        ws.settimeout(2.0)
        ws.send(json.dumps({"event": "pusher:subscribe",
                            "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}}))
        deadline = _time.monotonic() + max(2, min(seconds, 40))
        while _time.monotonic() < deadline and len(messages) < limit:
            try:
                data = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not data:
                continue
            transcript.append(str(data))
            parsed = _parse_kick_event(str(data))
            if not parsed:
                continue
            if parsed.get("ping"):
                ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
            elif "text" in parsed:
                messages.append(parsed)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return messages, "\n".join(transcript)


async def read_kick_chat(store: ResearchStore, job_id: int, channel: str,
                         seconds: int = 12, limit: int = 200) -> dict[str, Any]:
    """Read a Kick channel's LIVE chat via its public Pusher websocket (no credentials). Resolves
    the channel's chatroom id, then reads messages and stores each as source_type='kick_chat'
    evidence. An empty result usually means the channel is offline or chat was quiet during the
    listening window — 'attempted, no usable data', not a missing capability."""
    import anyio

    slug = re.sub(r"[^a-z0-9_]", "", (channel or "").lstrip("#").lower().strip())
    if not slug:
        raise ValueError("Provide a Kick channel name, for example 'xqc'.")
    status, body, _ = await anyio.to_thread.run_sync(lambda: _kick_get_blocking(f"/api/v2/channels/{slug}"))
    if status != 200 or not body:
        raise ValueError(f"Could not load the Kick channel '{slug}' (HTTP {status}).")
    try:
        chatroom_id = (json.loads(body).get("chatroom") or {}).get("id")
    except Exception:
        chatroom_id = None
    if not chatroom_id:
        raise ValueError(f"Kick channel '{slug}' has no readable chatroom.")
    messages, transcript = await anyio.to_thread.run_sync(
        lambda: _read_kick_chat_blocking(chatroom_id, seconds, limit))
    raw_id = record_raw_parts(
        store.database_url, job_id, "research.kick_chat", "kick_pusher",
        method="WSS", url=f"{_KICK_WS_URL}#chatrooms.{chatroom_id}.v2", status=200,
        content=transcript.encode("utf-8", "replace"),
    )
    stored = []
    for message in messages:
        row = store.add_evidence(
            job_id, source_type="kick_chat", url=f"https://kick.com/{slug}",
            title=f"Kick {slug} chat", excerpt=message["text"], author=message["user"], query=slug,
            metadata={"channel": slug, "chatroom_id": chatroom_id,
                      "created_at": message.get("created_at"), "raw_response_id": raw_id},
        )
        stored.append({"user": message["user"], "text": message["text"], "evidence_id": row["evidence_id"]})
    return {"channel": slug, "chatroom_id": chatroom_id, "matched": len(stored), "items": stored,
            "raw_response_id": raw_id}


async def read_twitch_chat(store: ResearchStore, job_id: int, channel: str,
                           seconds: int = 8, limit: int = 200) -> dict[str, Any]:
    """Read a Twitch channel's LIVE chat via anonymous IRC (no credentials). Stores each
    chat message as evidence. An empty result usually means the channel is offline or chat
    was quiet during the listening window — that is 'attempted, no usable data', not a
    missing capability."""
    import anyio

    channel = (channel or "").lstrip("#").strip()
    if not channel:
        raise ValueError("Provide a Twitch channel name, for example 'xqc'.")
    messages, transcript = await anyio.to_thread.run_sync(
        lambda: _read_twitch_chat_blocking(channel, seconds, limit)
    )
    raw_id = record_raw_parts(
        store.database_url, job_id, "research.twitch_chat", "twitch_irc",
        method="WSS", url=f"{_TWITCH_WS_URL}/#{channel.lower()}", status=200,
        content=transcript.encode("utf-8", "replace"),
    )
    stored = []
    for message in messages:
        row = store.add_evidence(
            job_id, source_type="twitch_chat", url=f"https://twitch.tv/{channel.lower()}",
            title=f"Twitch #{channel.lower()} chat", excerpt=message["text"],
            author=message["user"], query=channel,
            metadata={"channel": channel.lower(), "raw_response_id": raw_id},
        )
        stored.append({"user": message["user"], "text": message["text"], "evidence_id": row["evidence_id"]})
    return {"channel": channel.lower(), "matched": len(stored), "items": stored, "raw_response_id": raw_id}
