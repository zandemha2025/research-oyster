"""The credibility layer: anonymize speakers, pull decision-grade patterns, and measure whether
we actually collected ENOUGH signal to answer.

This is what separates "here's some chatter" from "here's the answer, here's how much we heard,
and here's why you can trust it." Three jobs, all pure functions over the dossier evidence list
(unit-testable, no DB):

1. ANONYMIZE — pseudonymize(author): a stable per-job pseudonym (user_7f3a) so deliverables never
   expose a real handle, while "the SAME person said X then Y" survives for pattern tracking.
2. PATTERNS (computed, not vibes) — recurring terms/phrases, per-channel/group splits, the loudest
   voices, and a signal ratio (substantive messages vs emote/one-word noise). Numbers, not a word
   cloud: every pattern carries a count.
3. SUFFICIENCY — the honest answer to "did we hear enough?". Two separate tests: SATURATION (has
   new-theme discovery flattened?) and ADEQUACY (enough substantive messages, weighted by signal
   not raw count). It ABSTAINS when there's no text to judge (e.g. landscape/metadata-only rows),
   so it never fabricates a verdict — it feeds the review gate and a plain-English caveat.

The semantics (what a theme MEANS) stay the model's job in synthesis; this module only measures
what is honestly countable and keeps it traceable.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

# Small, dependency-free English stoplist + chat noise. Enough to separate signal from filler
# without pretending to be a full NLP pipeline.
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "than", "as", "of", "to", "in", "on",
    "for", "with", "at", "by", "from", "up", "out", "is", "are", "was", "were", "be", "been", "being",
    "am", "do", "does", "did", "have", "has", "had", "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "them", "us", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "there", "here", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "some", "no", "not", "only", "own", "same",
    "just", "too", "very", "can", "will", "would", "should", "could", "now", "get", "got", "im",
    "dont", "u", "ur", "lol", "lmao", "lmfao", "yeah", "yep", "nah", "ok", "okay", "gg", "wp", "omg",
    "bruh", "bro", "like", "gonna", "wanna", "yall", "aint", "cant", "thats", "theyre", "youre",
}
_EMOTE_RE = re.compile(r"\[emote:\d+:[^\]]+\]")
_URL_RE = re.compile(r"https?://\S+")
_TOKEN_RE = re.compile(r"[a-z][a-z'\-]{2,}")


def pseudonymize(author: str, salt: str = "") -> str:
    """Stable pseudonym for a speaker. Same (salt, author) → same pseudonym, so cross-message
    patterns survive; the real handle is never recoverable from the output. Use job_id as salt so
    pseudonyms don't correlate across unrelated jobs."""
    author = (author or "").strip()
    if not author:
        return "anon"
    digest = hashlib.sha256(f"{salt}\n{author.lower()}".encode()).hexdigest()[:4]
    return f"user_{digest}"


def _text_of(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("excerpt") or row.get("text") or "")


def _clean(text: str) -> str:
    return _URL_RE.sub(" ", _EMOTE_RE.sub(" ", text.lower()))


def _meaningful_tokens(text: str) -> list[str]:
    return [w for w in _TOKEN_RE.findall(_clean(text)) if w not in _STOP]


def _is_substantive(text: str) -> bool:
    """A message carries real signal if it has >=3 meaningful (non-stopword, non-emote) words.
    Emote spam, 'LOL', and one-word reactions are noise for theme discovery."""
    return len(_meaningful_tokens(text)) >= 3


def _group_of(row: dict[str, Any], group_by: str) -> str:
    meta = row.get("metadata") or {}
    if group_by == "source_type":
        return str(row.get("source_type") or "—")
    if group_by == "channel":
        return str(meta.get("channel") or meta.get("entity") or row.get("source_type") or "—")
    return str(meta.get(group_by) or row.get(group_by) or "—")


def signal_ratio(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """How much of what we collected is substantive vs noise. Low ratio = lots of chatter, little
    to analyze — the honest reason a huge n can still be a weak sample."""
    texts = [_text_of(r) for r in evidence if _text_of(r).strip()]
    substantive = sum(1 for t in texts if _is_substantive(t))
    total = len(texts)
    return {"total_with_text": total, "substantive": substantive,
            "ratio": round(substantive / total, 3) if total else 0.0}


def top_terms(evidence: list[dict[str, Any]], limit: int = 20, min_count: int = 2) -> list[dict[str, Any]]:
    """Most frequent meaningful terms + two-word phrases across substantive messages. This is how
    'the same gripe worded 40 ways' becomes a countable pattern instead of a hunch."""
    counter: Counter[str] = Counter()
    for row in evidence:
        text = _text_of(row)
        if not _is_substantive(text):
            continue
        toks = _meaningful_tokens(text)
        counter.update(toks)
        counter.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    return [{"term": t, "count": c} for t, c in counter.most_common(limit) if c >= min_count]


def top_voices(evidence: list[dict[str, Any]], salt: str = "", limit: int = 10) -> list[dict[str, Any]]:
    """The loudest speakers, pseudonymized. Reveals whether a 'consensus' is a crowd or three
    accounts posting 50 times each — a real credibility check on chatter."""
    counter: Counter[str] = Counter()
    for row in evidence:
        if not isinstance(row, dict):
            continue
        author = row.get("author") or (row.get("metadata") or {}).get("entity")
        if author:
            counter[pseudonymize(str(author), salt)] += 1
    return [{"speaker": s, "messages": n} for s, n in counter.most_common(limit)]


def by_group(evidence: list[dict[str, Any]], group_by: str = "source_type") -> list[dict[str, Any]]:
    """Message + substantive counts per channel/group, so the answer can say 'in #support vs
    #general the split is…' instead of averaging everything into mush."""
    groups: dict[str, dict[str, int]] = {}
    for row in evidence:
        if not isinstance(row, dict) or not _text_of(row).strip():
            continue
        g = _group_of(row, group_by)
        bucket = groups.setdefault(g, {"messages": 0, "substantive": 0})
        bucket["messages"] += 1
        if _is_substantive(_text_of(row)):
            bucket["substantive"] += 1
    rows = [{"group": g, **v} for g, v in groups.items()]
    rows.sort(key=lambda r: r["substantive"], reverse=True)
    return rows


def saturation(evidence: list[dict[str, Any]], chunk: int = 25) -> dict[str, Any]:
    """Theme-discovery saturation, proxied by the rate of NEW meaningful terms as we accumulate
    substantive messages. When each new chunk stops adding new vocabulary, discovery has flattened
    — that's when 'collect more' stops paying off. Returns the curve for transparency plus a
    verdict. Abstains (assessable=False) when there's too little text to judge."""
    texts = [_text_of(r) for r in evidence if _is_substantive(_text_of(r))]
    if len(texts) < max(2 * chunk, 20):
        return {"assessable": False, "substantive": len(texts),
                "reason": "too few substantive messages to measure saturation"}
    seen: set[str] = set()
    curve: list[dict[str, int]] = []
    for start in range(0, len(texts), chunk):
        batch = texts[start:start + chunk]
        before = len(seen)
        for t in batch:
            seen.update(_meaningful_tokens(t))
        curve.append({"through": start + len(batch), "new_terms": len(seen) - before})
    head = curve[0]["new_terms"] or 1
    tail = curve[-1]["new_terms"]
    tail_rate = tail / head  # fraction of first-chunk novelty the last chunk still adds
    return {"assessable": True, "substantive": len(texts), "unique_terms": len(seen),
            "curve": curve, "tail_novelty": round(tail_rate, 3),
            "saturated": tail_rate <= 0.25}


def assess_sufficiency(evidence: list[dict[str, Any]], group_by: str = "source_type",
                       min_substantive: int = 20) -> dict[str, Any]:
    """The honest 'did we hear enough?' verdict, combining adequacy (enough substantive messages,
    weighted by signal not raw count) and saturation (has discovery flattened?).

    Design choice: ABSTAIN (enough=True, assessable=False) when there is no text to judge — e.g.
    landscape/metadata-only rows like Discord member counts. Never block on evidence we can't
    measure, and never fabricate a verdict. `min_substantive` is a floor, not magic: theme
    saturation in qualitative work typically needs ~20-30 substantive items, adjust per question."""
    text_rows = [r for r in evidence if isinstance(r, dict) and _text_of(r).strip()]
    sig = signal_ratio(text_rows)
    if not text_rows:
        return {"assessable": False, "enough": True, "substantive": 0,
                "reason": "no text evidence to assess (metadata/landscape only)"}
    sat = saturation(text_rows)
    substantive = sig["substantive"]
    saturated = bool(sat.get("saturated"))
    # Enough if discovery saturated with a real floor of substantive items, OR sheer substantive
    # volume is high enough (3x floor) that thin novelty is just breadth, not gaps.
    enough = (substantive >= min_substantive and saturated) or (substantive >= 3 * min_substantive)
    reasons: list[str] = []
    if substantive < min_substantive:
        reasons.append(f"only {substantive} substantive messages (floor {min_substantive})")
    if not saturated and sat.get("assessable"):
        reasons.append(f"new themes still appearing (tail novelty {sat.get('tail_novelty')})")
    if sig["ratio"] < 0.2 and sig["total_with_text"] >= 20:
        reasons.append(f"low signal — only {int(sig['ratio']*100)}% of messages are substantive")
    return {
        "assessable": True,
        "enough": enough,
        "substantive": substantive,
        "signal_ratio": sig["ratio"],
        "saturated": saturated,
        "tail_novelty": sat.get("tail_novelty"),
        "per_group": by_group(text_rows, group_by),
        "reasons": reasons,
        "verdict": ("sufficient" if enough else "collect more"),
    }


# --- consensus weighting (signal at scale) -----------------------------------
# The unit of evidence isn't a comment — it's a comment × the people behind it. A 1k-upvote comment
# is a poll result, not an anecdote. Different engagement types carry different conviction:
#   shares/reposts — the strongest (you put your own name on it)
#   upvotes/likes  — endorsement, the core agreement signal
#   comments       — effort/depth (ambiguous sentiment, so weighted modestly)
#   views          — reach, not conviction (dampened: huge, cheap)
#   dislikes       — disagreement (subtracts)
# Engagement lands in metadata inconsistently (metadata.metrics for Apify/X/kick, metadata.record
# for raw Apify, and TOP-LEVEL keys like 'upvotes'/'num_comments' for the Reddit path). engagement_of
# reads all of them so the free path's Reddit signal is never silently dropped.
_ENGAGEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "upvotes": ("upvotes", "score", "ups", "upvotecount", "likes", "like_count", "likecount",
                "diggcount", "favoritecount", "favorite_count", "reactionscount", "heartcount"),
    "comments": ("comments", "num_comments", "comment_count", "commentcount", "commentscount",
                 "reply_count", "replycount", "replies"),
    "shares": ("shares", "sharecount", "retweetcount", "retweet_count", "reposts", "repostcount",
               "quotecount", "quote_count", "collectcount"),
    "views": ("views", "viewcount", "view_count", "videoviewcount", "playcount", "plays",
              "impressions", "impressioncount", "impression_count"),
    "dislikes": ("dislikes", "downvotes", "dislikecount", "downvotecount"),
}
# How much each signal counts toward "the crowd agrees." Views are handled separately (sqrt-damped).
_CONVICTION: dict[str, float] = {"upvotes": 1.0, "comments": 0.5, "shares": 3.0, "dislikes": -1.5}


def engagement_of(row: dict[str, Any]) -> dict[str, float]:
    """Every engagement signal on one evidence row, from wherever the connector stashed it:
    metadata.metrics, metadata.record (raw actor row), or top-level metadata keys (the Reddit path).
    Returns raw counts keyed by normalized signal (upvotes/comments/shares/views/dislikes)."""
    meta = row.get("metadata") or {}
    pools: list[dict[str, Any]] = []
    for candidate in (meta.get("metrics"), meta.get("record"), meta):
        if isinstance(candidate, dict):
            pools.append(candidate)
    flat: dict[str, float] = {}
    for pool in pools:
        for k, v in pool.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            flat.setdefault(str(k).lower(), float(v))
    out: dict[str, float] = {}
    for signal, aliases in _ENGAGEMENT_ALIASES.items():
        for alias in aliases:
            if alias in flat:
                out[signal] = flat[alias]
                break
    return out


def _conviction_score(sums: dict[str, float]) -> float:
    """Composite RANK score from raw signal sums. Endorsement counts near-linearly (a 1k-upvote
    comment really is ~1000 people); shares count most; comments modestly; views only sqrt-damped
    (reach, not conviction); dislikes subtract. The raw per-signal sums carry the narrative."""
    import math
    score = sum(coeff * sums.get(sig, 0.0) for sig, coeff in _CONVICTION.items())
    score += 2.0 * math.sqrt(max(sums.get("views", 0.0), 0.0))
    return round(score, 2)


def weighted_consensus(evidence: list[dict[str, Any]], group_by: str = "source_type",
                       limit: int = 12) -> dict[str, Any]:
    """Rank what the crowd actually ENDORSES, not how often it was mentioned. Per group, sum each
    engagement signal and compute a conviction weight; return groups ranked by weight with the raw
    sums for the narrative ('4,300 upvotes across 8 threads'). Signal at scale: one high-endorsement
    datapoint can outrank twenty obscure ones. Normalization note: raw counts favour big communities
    and older posts — treat as a strong-but-imperfect consensus proxy, not gospel."""
    groups: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    endorsed = 0
    for row in evidence:
        eng = engagement_of(row)
        agg = groups.setdefault(_group_of(row, group_by), {})
        for signal, val in eng.items():
            agg[signal] = agg.get(signal, 0.0) + val
        counts[_group_of(row, group_by)] = counts.get(_group_of(row, group_by), 0) + 1
        if any(eng.get(s, 0) for s in ("upvotes", "shares", "comments", "views")):
            endorsed += 1
    ranked = []
    for g, sums in groups.items():
        ranked.append({
            "group": g, "items": counts[g],
            "upvotes": int(sums.get("upvotes", 0)), "comments": int(sums.get("comments", 0)),
            "shares": int(sums.get("shares", 0)), "views": int(sums.get("views", 0)),
            "dislikes": int(sums.get("dislikes", 0)), "weight": _conviction_score(sums),
        })
    ranked.sort(key=lambda r: r["weight"], reverse=True)
    return {
        "group_by": group_by,
        "rows_with_engagement": endorsed, "rows_total": len(evidence),
        "total_endorsement": {k: sum(r[k] for r in ranked) for k in ("upvotes", "comments", "shares", "views")},
        "ranked": ranked[:limit],
        "note": ("Ranked by conviction weight (shares>upvotes>comments; views sqrt-damped; dislikes "
                 "subtract), NOT mention count. Lead the report with the highest-endorsement findings "
                 "and cite the weight (e.g. 'N upvotes across M threads'). Raw counts favour large "
                 "communities/older posts — a strong but imperfect consensus proxy."),
    }


def analyze_chatter(evidence: list[dict[str, Any]], salt: str = "",
                    group_by: str = "source_type") -> dict[str, Any]:
    """One call for the whole credibility layer over a job's evidence: anonymized top voices,
    recurring terms/phrases, per-group breakdown, signal ratio, the sufficiency verdict, and the
    consensus weighting (what the crowd most endorses, by engagement — not mention count)."""
    return {
        "sufficiency": assess_sufficiency(evidence, group_by=group_by),
        "signal": signal_ratio(evidence),
        "consensus": weighted_consensus(evidence, group_by=group_by),
        "top_terms": top_terms(evidence),
        "top_voices": top_voices(evidence, salt=salt),
        "by_group": by_group(evidence, group_by),
    }
