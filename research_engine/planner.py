from __future__ import annotations

import re
from collections import Counter
from typing import Any


# A tiny stopword set for the *fallback* entity extractor. The planner no longer authors
# research questions or query templates — a capable model (Opus 5 / Fable 5) decomposes the
# brief and supplies questions/entities/angles through create_research_job. This module only
# records the brief cleanly and extracts candidate entities when the model supplies none.
STOPWORDS = {
    "about", "after", "again", "also", "because", "before", "being",
    "could", "doing", "figure", "for", "from", "have", "into", "just",
    "need", "related", "research", "something", "that", "their", "there",
    "these", "they", "this", "what", "when", "where", "which", "with",
    "would", "your", "people", "saying",
}


def _terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+'-]{2,}", text)
    counts = Counter(word.lower() for word in words if word.lower() not in STOPWORDS)
    ordered = list(dict.fromkeys(word.lower() for word in words if word.lower() not in STOPWORDS))
    return sorted(ordered, key=lambda word: (-counts[word], ordered.index(word)))[:14]


def build_plan(brief: str, decision: str = "", market: str = "", time_horizon: str = "",
               audience: str = "", deliverable: str = "", required_sources: list[str] | None = None,
               exclusions: list[str] | None = None, questions: list[str] | None = None,
               entities: list[str] | None = None, angles: list[str] | None = None) -> dict[str, Any]:
    """Record a research brief and a working plan.

    The intelligence is the model's: it decomposes the decision into sub-questions, names the
    entities, and chooses angles, then passes them in. When it does not, this falls back to
    clean entity extraction and generic meta-questions — never topic-specific canned ones.
    """
    if len(brief.strip()) < 12:
        raise ValueError("The research brief needs enough detail to identify a subject and goal.")

    resolved_entities = [e.strip() for e in (entities or []) if e.strip()] or _terms(
        " ".join((brief, decision, market))
    )
    resolved_questions = [q.strip() for q in (questions or []) if q.strip()] or [
        "What is the direct answer to the brief, and what evidence supports it?",
        "Where is this actually being discussed, and what are people saying in their own words?",
        "Where do opinions split, and what would change the decision this supports?",
    ]

    # A light source hint, not a mandate. Discovery decides where the conversation lives;
    # the model is explicitly told not to force irrelevant platforms.
    sources = list(dict.fromkeys(list(required_sources or []) + ["web", "reddit"]))

    return {
        "objective": brief.strip(),
        "decision": decision.strip() or "Clarify the decision with the user if it cannot be inferred from the brief.",
        "market": market.strip() or "Not specified",
        "audience": audience.strip() or "Infer likely audiences, then validate them through evidence.",
        "deliverable": deliverable.strip() or "A report that answers the question: executive answer, cited themes, tensions, sentiment, recommendations, and an honest note on confidence.",
        "time_horizon": time_horizon.strip() or "Current, unless the brief specifies otherwise",
        "entities_and_terms": resolved_entities,
        "research_questions": resolved_questions,
        "angles": [a.strip() for a in (angles or []) if a.strip()],
        "recommended_sources": sources,
        "clarifications": [
            prompt for missing, prompt in (
                (market, "Which geography or market should the research represent?"),
                (audience, "Is there a priority audience, or should Oyster discover the strongest candidates?"),
                (decision, "What decision will this research support?"),
            ) if not missing
        ],
        "exclusions": exclusions or [],
        "method": [
            "decompose the decision into sub-questions",
            "discover where the conversation actually happens",
            "collect substance — real quotes, not headlines",
            "iterate until the question can be answered confidently (saturation)",
            "synthesize a report that answers the question, with citations",
        ],
        "quality_bar": (
            "The deliverable is a report that ANSWERS the question, with every claim tied to "
            "cited evidence; raw data is an appendix. 'I could not reach X' is never a stopping "
            "point when the topic is discussed publicly elsewhere."
        ),
    }
