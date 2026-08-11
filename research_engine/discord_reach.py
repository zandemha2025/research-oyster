"""Discord 'reach' — turn a topic into an AIMED, click-to-open capture plan.

Discord has no cross-server search and its messages sit behind membership, so Oyster never drives a
bot through it. Instead the agent does the REASONING a human researcher would: figure out which hub
communities hold the conversation (``discord_landscape`` does the public discovery + member/online
counts), then hand the user a short list of **clickable channel links** plus the terms to type into
Discord's own search bar. The human clicks the links and runs the search; the extension captures the
resulting messages as raw data. The human doing the clicking + searching is exactly what keeps it a
person browsing their own servers — never a self-bot, never automation driving the account.

These are pure helpers (no network, no DB) so they're unit-tested directly; the MCP tool
``plan_discord_capture`` composes them with ``discord_landscape`` and an armed capture session.
"""

from __future__ import annotations

import re

# topic-keyword -> channel-name hints a big server of that kind usually has.
_CHANNEL_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("game", "gaming", "video game", "playstation", "xbox", "steam", "console", "insomniac"),
     ("#general", "#games", "#video-games", "#gaming", "#discussion")),
    (("movie", "film", "show", "series", "cinema", "trailer"),
     ("#general", "#movies", "#tv-shows", "#discussion", "#spoilers")),
    (("music", "album", "song", "artist", "band", "tour"),
     ("#general", "#music", "#new-releases", "#discussion")),
    (("anime", "manga", "comic", "marvel", "dc"),
     ("#general", "#anime", "#comics", "#discussion")),
    (("beauty", "makeup", "skincare", "cosmetic", "fragrance"),
     ("#general", "#makeup", "#skincare", "#hauls", "#discussion")),
    (("crypto", "nft", "token", "web3", "defi"),
     ("#general", "#trading", "#discussion", "#announcements")),
)
_FALLBACK_CHANNELS = ("#general", "#discussion", "#off-topic", "#announcements")


def _slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")


def suggest_channels(topic: str) -> list[str]:
    """Best-guess channel names to look in for this topic. A big server of the right kind usually
    has these; a topic-named channel is offered first when the subject is a single word."""
    t = (topic or "").lower()
    slug = _slug(topic)
    chans: list[str] = []
    if slug and "-" not in slug and len(slug) <= 20:
        chans.append(f"#{slug}")  # fan servers often have e.g. #wolverine
    for keys, hints in _CHANNEL_HINTS:
        if any(k in t for k in keys):
            chans.extend(h for h in hints if h not in chans)
            break
    chans.extend(h for h in _FALLBACK_CHANNELS if h not in chans)
    return chans[:6]


def search_terms(topic: str) -> list[str]:
    """Terms to type into Discord's in-app search bar. The whole topic first, then its meaningful
    words (drop short stopwords) so a narrow phrase and its parts both get tried."""
    topic = (topic or "").strip()
    if not topic:
        return []
    stop = {"the", "a", "an", "of", "for", "and", "or", "on", "in", "to", "is", "game", "video"}
    words = [w for w in re.findall(r"[A-Za-z0-9']+", topic) if len(w) > 2 and w.lower() not in stop]
    terms = [topic]
    for w in words:
        if w.lower() != topic.lower() and w not in terms:
            terms.append(w)
    return terms[:4]


def top_communities(communities: list[dict], n: int = 3) -> list[dict]:
    """Pick the most worth-opening servers: most online (live activity) first, then most members.
    Servers with no reachable invite/url are dropped; ones with no counts sink to the bottom."""
    def key(c: dict) -> tuple[int, int]:
        return (int(c.get("online") or 0), int(c.get("members") or 0))
    reachable = [c for c in communities if c.get("invite_code") or c.get("url")]
    return sorted(reachable, key=key, reverse=True)[:max(1, n)]


def community_url(c: dict) -> str:
    return c.get("url") or (f"https://discord.gg/{c['invite_code']}" if c.get("invite_code") else "")


def format_capture_plan(topic: str, communities: list[dict], channels: list[str],
                        terms: list[str], armed: bool) -> str:
    """The human-facing 'click these' plan the agent shows in Studio. Short and aimed: which
    servers to open (as links), what to type in Discord's search bar, and what happens next."""
    if not communities:
        return (f"No public Discord servers surfaced for “{topic}”. Discord has no cross-server "
                f"search, so if you know a specific server, open it and search there and I'll "
                f"capture — otherwise the same conversation is usually richer on Reddit/YouTube.")
    lines = [
        f"Here’s where the “{topic}” conversation likely lives on Discord. **Click a server to open "
        f"it in your browser** (you must be a member — join if you want). Then use Discord’s **search "
        f"bar** for the terms below, or open the channels I list, and scroll the results — I capture "
        f"the messages automatically as they load. I never open, search, or scroll for you.",
        "",
    ]
    for i, c in enumerate(communities, 1):
        name = c.get("name") or c.get("invite_code") or "server"
        url = community_url(c)
        size = []
        if c.get("members") is not None:
            size.append(f"{int(c['members']):,} members")
        if c.get("online") is not None:
            size.append(f"{int(c['online']):,} online")
        sizetxt = f" — {', '.join(size)}" if size else ""
        lines.append(f"{i}. [{name}]({url}){sizetxt}")
    lines.append("")
    if terms:
        lines.append("In each server’s **search bar**, type: " + " · ".join(f"`{t}`" for t in terms))
    lines.append("Or browse channels like: " + ", ".join(channels))
    if armed:
        lines.append("")
        lines.append("✅ Hands-free capture is armed for discord.com — approve it once in the Oyster "
                     "extension (or trust the domain), then just click, search, and scroll. Everything "
                     "on-topic lands in this job as raw data.")
    return "\n".join(lines)
