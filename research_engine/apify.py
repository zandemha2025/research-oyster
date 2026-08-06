"""The Apify intelligence layer — "1000x better than calling one actor and dumping rows".

Naive Apify use = the model guesses an actor id and dumps rows as text excerpts (the failure
that returned follower counts instead of tweets). This module is the intelligence on top:

  1. A CURATED registry: (platform, intent) -> the right actor + a correct input builder.
  2. NUMERIC EXTRACTION: pull metric fields (views, likes, followers, …) out of messy actor
     rows into normalized numbers, so the metrics layer can compute medians/rates/n.
  3. WINDOWS + SAMPLE discipline: enforce the date window locally (scrapers ignore their own),
     find the timestamp whatever the field is called.
  4. ENTITY resolution: which account/community a row belongs to, for grouping.
  5. BUDGET: clamp result counts.

These are all pure functions (no network) so they're unit-tested here; `connectors.apify_collect`
does the actual Apify HTTP using this module to decide the actor, input, and post-processing.

Actor ids are the one thing that drifts over time — they live ONLY in REGISTRY below, so they're
trivial to update, and the numeric extraction is deliberately shape-tolerant so a different actor's
output still yields the numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

MAX_LIMIT = 100  # budget guard — never let a single pull exceed this


@dataclass(frozen=True)
class ActorSpec:
    actor: str
    build_input: Callable[[str, int, int], dict[str, Any]]
    kind: str = "item"          # what one row represents (post/video/tweet/channel/message)
    opt_in: bool = False        # requires explicit user opt-in (ToS-gray)
    note: str = ""


def _reddit_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"searches": [q], "maxItems": limit, "sort": "relevance", "time": "year", "type": "posts"}


def _tweet_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"searchTerms": [q], "maxItems": limit, "sort": "Latest"}


def _tiktok_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"searchQueries": [q], "resultsPerPage": limit, "shouldDownloadVideos": False, "shouldDownloadCovers": False}


def _instagram_hashtag_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"search": q.lstrip("#"), "searchType": "hashtag", "resultsType": "posts", "resultsLimit": limit}


def _instagram_profile_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"usernames": [q.lstrip("@")], "resultsType": "details", "resultsLimit": limit}


def _youtube_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"searchQueries": [q], "maxResults": limit, "maxResultsShorts": limit}


def _twitch_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"queries": [q], "maxItems": limit}


def _kick_input(q: str, limit: int, days: int) -> dict[str, Any]:
    return {"searchKeywords": [q], "maxItems": limit}


def _discord_input(q: str, limit: int, days: int) -> dict[str, Any]:
    # q must be "guild_id/channel_id"; token supplied by caller (opt-in path).
    guild, _, channel = q.partition("/")
    return {"guild_id": guild.strip(), "channel_id": channel.strip(), "max_messages": limit}


# (platform, intent) -> ActorSpec. Actor ids are best-known defaults; update here if one drifts.
REGISTRY: dict[tuple[str, str], ActorSpec] = {
    ("reddit", "search"): ActorSpec("trudax/reddit-scraper-lite", _reddit_input, "post"),
    ("x", "search"): ActorSpec("apidojo/tweet-scraper", _tweet_input, "tweet"),
    ("twitter", "search"): ActorSpec("apidojo/tweet-scraper", _tweet_input, "tweet"),
    ("tiktok", "search"): ActorSpec("clockworks/tiktok-scraper", _tiktok_input, "video"),
    ("instagram", "hashtag"): ActorSpec("apify/instagram-scraper", _instagram_hashtag_input, "post"),
    ("instagram", "profile"): ActorSpec("apify/instagram-scraper", _instagram_profile_input, "profile"),
    ("youtube", "search"): ActorSpec("streamers/youtube-scraper", _youtube_input, "video"),
    ("twitch", "search"): ActorSpec("automation-lab/twitch-scraper", _twitch_input, "channel"),
    ("kick", "search"): ActorSpec("aitooolsmax/kick-data-scraper", _kick_input, "channel"),
    ("discord", "messages"): ActorSpec(
        "harvestedge/discord-message-scraper", _discord_input, "message",
        opt_in=True, note="Requires a burner Discord user token; ToS-gray. Opt-in only."),
}

# Default intent per platform when the caller doesn't specify one.
DEFAULT_INTENT = {
    "reddit": "search", "x": "search", "twitter": "search", "tiktok": "search",
    "instagram": "hashtag", "youtube": "search", "twitch": "search", "kick": "search",
    "discord": "messages",
}


def resolve(platform: str, intent: str = "") -> ActorSpec | None:
    platform = (platform or "").lower().strip()
    intent = (intent or DEFAULT_INTENT.get(platform, "search")).lower().strip()
    return REGISTRY.get((platform, intent))


def build_input(spec: ActorSpec, query: str, limit: int, days: int) -> dict[str, Any]:
    return spec.build_input(query, clamp_limit(limit), max(1, days))


def clamp_limit(limit: int) -> int:
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except Exception:
        return 30


# --- numeric extraction -------------------------------------------------------
# Normalized metric -> the field names actors actually use (across TikTok/IG/X/Reddit/YT/etc.).
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "views": ("playcount", "viewcount", "videoviewcount", "views", "view_count", "viewscount", "plays", "playsCount".lower()),
    "likes": ("diggcount", "likecount", "likescount", "likes", "like_count", "favoritecount", "favorite_count", "reactionscount", "heartcount"),
    "comments": ("commentcount", "commentscount", "comments", "num_comments", "reply_count", "replycount", "replies"),
    "shares": ("sharecount", "shares", "retweetcount", "retweet_count", "repostcount", "reposts", "collectcount"),
    "followers": ("followerscount", "followers", "followers_count", "fanscount", "fans", "subscribercount", "subscribers", "subscribercounttext".lower()),
    "score": ("score", "upvotes", "ups", "upvotecount"),
}


def _iter_numeric(obj: Any, depth: int = 0):
    """Yield (lowercased_key, numeric_value) pairs from a dict, one level of nesting deep."""
    if not isinstance(obj, dict) or depth > 2:
        return
    for key, value in obj.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            yield str(key).lower(), float(value)
        elif isinstance(value, dict):
            yield from _iter_numeric(value, depth + 1)


def extract_metrics(row: dict[str, Any]) -> dict[str, float]:
    """Pull normalized numeric metrics out of a messy actor row (shape-tolerant, nested-aware)."""
    found: dict[str, float] = {}
    pairs = list(_iter_numeric(row))
    for metric, aliases in _METRIC_ALIASES.items():
        for lk, val in pairs:
            if lk in aliases:
                found[metric] = val
                break
    return found


# --- entity + timestamp -------------------------------------------------------
_ENTITY_PATHS = (
    ("authorMeta", "name"), ("author", "userName"), ("author", "username"), ("author", "name"),
    ("owner", "username"), ("ownerUsername",), ("user", "username"), ("user", "name"),
    ("channel", "name"), ("channelName",), ("username",), ("displayName",), ("slug",),
    ("parsedCommunityName",), ("subreddit",), ("communityName",), ("handle",),
)


def pick_entity(row: dict[str, Any]) -> str:
    for path in _ENTITY_PATHS:
        cur: Any = row
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                cur = None
                break
        if isinstance(cur, str) and cur.strip():
            return cur.strip().lstrip("@")
    return ""


_TS_KEYS = ("createtime", "createtimeiso", "timestamp", "taken_at_timestamp", "created_utc",
            "createdat", "publishedat", "published", "date", "uploadedat", "posteddate", "time")


def row_timestamp(row: dict[str, Any]) -> float | None:
    """Best-effort epoch seconds for a row, whatever the date field is named."""
    for lk, val in _iter_numeric(row):
        if lk in _TS_KEYS:
            return val / 1000.0 if val > 1e11 else float(val)  # ms vs s
    # string dates
    def _walk(obj, depth=0):
        if not isinstance(obj, dict) or depth > 2:
            return None
        for k, v in obj.items():
            if str(k).lower() in _TS_KEYS and isinstance(v, str) and v:
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
            got = _walk(v, depth + 1) if isinstance(v, dict) else None
            if got:
                return got
        return None
    return _walk(row)


def within_window(row: dict[str, Any], days: int, now: float | None = None) -> bool:
    """Enforce the date window LOCALLY. Rows with no discoverable date are kept (can't prove stale)."""
    if not days or days <= 0:
        return True
    ts = row_timestamp(row)
    if ts is None:
        return True
    reference = now if now is not None else time.time()
    return (reference - ts) <= days * 86400 + 3600


def normalize_row(row: dict[str, Any], query_shape: str = "", now: float | None = None) -> dict[str, Any]:
    """The per-row output the collector stores in evidence.metadata: the raw record plus the
    normalized numbers, entity, and a within-window flag."""
    return {
        "record": row,
        "metrics": extract_metrics(row),
        "entity": pick_entity(row),
        "query_shape": query_shape,
        "timestamp": row_timestamp(row),
    }
