from __future__ import annotations

import re
from collections import Counter
from typing import Any


STOPWORDS = {
    "about", "after", "again", "also", "because", "before", "being", "campaign",
    "choose", "could", "develop", "differentiated", "doing", "figure", "for", "from",
    "have", "into", "just", "need", "related",
    "research", "something", "that", "their", "there", "these", "they", "this",
    "what", "when", "where", "which", "with", "would", "your",
}


def _terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+'-]{2,}", text)
    counts = Counter(word.lower() for word in words if word.lower() not in STOPWORDS)
    ordered = list(dict.fromkeys(word.lower() for word in words if word.lower() not in STOPWORDS))
    return sorted(ordered, key=lambda word: (-counts[word], ordered.index(word)))[:14]


def build_plan(brief: str, decision: str = "", market: str = "", time_horizon: str = "",
               audience: str = "", deliverable: str = "", required_sources: list[str] | None = None,
               exclusions: list[str] | None = None) -> dict[str, Any]:
    if len(brief.strip()) < 12:
        raise ValueError("The research brief needs enough detail to identify a subject and goal.")
    subject_terms = _terms(brief)
    terms = list(dict.fromkeys(subject_terms + _terms(" ".join((decision, market, time_horizon)))))[:14]
    anchor_terms = [term for term in subject_terms if not re.fullmatch(r"20\d{2}", term) and term not in {"christmas", "holiday", "creative", "territory"}]
    anchor = " ".join(anchor_terms[:6] or subject_terms[:6]) or brief.strip()
    commercial = any(word in brief.lower() for word in ("brand", "campaign", "launch", "customer", "product", "market"))
    sources = list(required_sources or []) + ["web", "rss", "x", "reddit"]
    if any(word in brief.lower() for word in ("game", "gaming", "stream", "creator", "esports")):
        sources += ["twitch", "kick", "discord"]
    elif any(word in brief.lower() for word in ("community", "fan", "enthusiast", "owner")):
        sources += ["discord"]
    questions = [
        "What do people currently believe, value, dislike, or misunderstand about the subject?",
        "Which audiences and communities care most, and what language do they use?",
        "What adjacent brands, products, creators, and cultural moments shape expectations?",
        "Which narratives are growing, contested, or absent from current coverage?",
        "What evidence would change the decision this research is meant to support?",
    ]
    if commercial:
        questions.insert(2, "What occasions, unmet needs, and creative territories could produce a differentiated campaign?")
    queries = [
        anchor,
        f'"{anchor}" reviews OR opinions OR complaints',
        f'"{anchor}" community OR forum OR reddit OR discord',
        f'"{anchor}" {" ".join(term for term in subject_terms if term not in anchor_terms) or "trends culture campaign"}',
        f'"{anchor}" competitors OR alternatives',
    ]
    return {
        "objective": brief.strip(),
        "decision": decision.strip() or "Clarify the decision with the user if it cannot be inferred from the brief.",
        "market": market.strip() or "Not specified",
        "audience": audience.strip() or "Infer likely audiences, then validate them through evidence.",
        "deliverable": deliverable.strip() or "Evidence-backed findings, implications, risks, and recommended actions.",
        "time_horizon": time_horizon.strip() or "Current, unless the brief specifies otherwise",
        "entities_and_terms": terms,
        "research_questions": questions,
        "query_families": queries,
        "recommended_sources": list(dict.fromkeys(sources)),
        "source_queries": {
            "web": queries,
            "x": [f'"{anchor}"', f'"{anchor}" (love OR hate OR wish OR problem OR review) -is:retweet'],
            "reddit": [anchor, f"{anchor} review", f"{anchor} problem OR alternative"],
            "discord": [f"{anchor} discord", f"{anchor} community invite"],
            "twitch": [anchor, *anchor_terms[:3]],
            "kick": [anchor, *anchor_terms[:3]],
            "rss": [anchor, *anchor_terms[:5]],
        },
        "clarifications": [
            prompt for missing, prompt in (
                (market, "Which geography or market should the research represent?"),
                (audience, "Is there a priority audience, or should Oyster discover the strongest candidates?"),
                (decision, "What decision will this research support?"),
            ) if not missing
        ],
        "exclusions": exclusions or [],
        "method": ["discover", "collect", "triangulate", "identify gaps", "synthesize with citations"],
        "quality_bar": "Every material claim must point to stored evidence; separate observation, inference, and recommendation.",
    }
