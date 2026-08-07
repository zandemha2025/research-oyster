"""Graph-engineered research pipeline.

'Graph engineering' here means execution-graph orchestration (per the article the user
referenced): the research run is an explicit graph of specialized NODES connected by
control-flow EDGES with a shared STATE object — NOT a knowledge graph / GraphRAG.

    plan → discover → parallel(collect lanes) → synthesize → review
    review == needs_more AND round < MAX → parallel(collect: targeted) → synthesize → review
    review == pass (or round == MAX) → export → done

Each agent node is an independent, focused agent call that operates on the shared research
job in Postgres (keyed by job_id) — so 'shared state' is the job itself, and this module
stays pure orchestration. studio.py injects `run_node` (drives an agent) and `get_dossier`
(reads the job); this module owns the nodes, prompts, edges, and the deterministic review
gate. Kept framework-free (no LangGraph) by design.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from research_engine import patterns

MAX_ROUNDS = 2

# Platforms a user might name, and which collection lane serves each.
KNOWN_PLATFORMS = ["discord", "twitch", "tiktok", "instagram", "reddit", "kick", "youtube", "twitter"]
LANE_FOR = {
    "discord": "discord", "twitch": "twitch", "reddit": "reddit", "kick": "kick",
    "tiktok": "web", "instagram": "web", "youtube": "web", "twitter": "web",
}
# A named platform's collection lands under this source_runs connector prefix (for the review gate).
CONNECTOR_KEY = {"twitch": "twitch", "discord": "discord", "reddit": "reddit"}

NODE_PREAMBLE = (
    "You are ONE node in a research pipeline. Do ONLY the step described in the message and then "
    "stop. The user is watching every tool call and raw result live, so be transparent. Never "
    "call a source that ran and returned nothing usable 'not available' — say plainly it was "
    "attempted and returned no usable data."
)


@dataclass
class ResearchState:
    brief: str
    job_id: int | None = None
    named_platforms: list[str] = field(default_factory=list)
    round: int = 0
    gaps: list[str] = field(default_factory=list)


def detect_platforms(brief: str) -> list[str]:
    low = brief.lower()
    return [p for p in KNOWN_PLATFORMS if re.search(rf"\b{p}\b", low)]


def default_lanes(state: ResearchState) -> list[str]:
    """Reddit + web are the workhorses; every named platform always gets its own lane."""
    lanes = {"reddit", "web"}
    for p in state.named_platforms:
        lanes.add(LANE_FOR.get(p, "web"))
    return sorted(lanes)


def lanes_for_gaps(gaps: list[str]) -> list[str]:
    lanes: set[str] = set()
    for gap in gaps:
        gl = gap.lower()
        for p in KNOWN_PLATFORMS:
            if p in gl:
                lanes.add(LANE_FOR.get(p, "web"))
        if "evidence" in gl or "thin" in gl:
            lanes.update({"web", "reddit"})
    return sorted(lanes) or ["web", "reddit"]


def review_gate(state: ResearchState, dossier: dict[str, Any]) -> tuple[str, list[str]]:
    """Deterministic review node: does the run actually cover what was asked, and is the
    synthesis backed? Returns ('pass'|'needs_more', gaps). This is what stops the agent from
    silently substituting Reddit for a named platform or exporting a thin/unbacked report."""
    gaps: list[str] = []
    runs = dossier.get("source_runs") or []
    connectors = " ".join(str(r.get("connector", "")) for r in runs)
    for platform in state.named_platforms:
        key = CONNECTOR_KEY.get(platform)
        if key and key not in connectors:
            gaps.append(f"{platform}: no collection attempt was recorded")
    synth = dossier.get("synthesis") or {}
    if not synth.get("executive_answer"):
        gaps.append("no synthesis authored yet")
    else:
        themes = synth.get("themes") or []
        if themes and all(not t.get("citations") for t in themes):
            gaps.append("themes are not backed by citations")
    evidence = dossier.get("evidence") or []
    if len(evidence) < 3:
        gaps.append("very thin evidence collected")
    # Sufficiency: did we actually hear ENOUGH chatter to answer? assess_sufficiency abstains
    # (enough=True) when there's no text to judge — e.g. landscape/metadata-only rows — so this
    # only ever fires on real, measurably-thin chatter, never on numbers-only evidence.
    suff = patterns.assess_sufficiency(evidence)
    if suff.get("assessable") and not suff.get("enough"):
        gaps.append("insufficient signal: " + "; ".join(suff.get("reasons") or ["collect more chatter"]))
    verdict = "needs_more" if (gaps and state.round < MAX_ROUNDS) else "pass"
    return verdict, gaps


# --- node prompts -------------------------------------------------------------
def plan_prompt(brief: str) -> str:
    return (
        f'PLAN step. Create the research job for this brief by calling create_research_job with a '
        f'clear brief plus model-authored `questions`, `entities`, and `angles`. Brief: "{brief}". '
        f'Then STOP — do not collect or synthesize. Report the job_id.'
    )


def discover_prompt(state: ResearchState) -> str:
    return (
        f"DISCOVER step for research job {state.job_id}. Use discover_sources and/or search_web to "
        f'find WHERE "{state.brief}" is actually being discussed (subreddits, forums, articles, '
        f"communities). Do not synthesize. Briefly list the best venues you found."
    )


def collect_prompt(lane: str, state: ResearchState) -> str:
    j, brief = state.job_id, state.brief
    prompts = {
        "reddit": (
            f'COLLECT step (Reddit) for job {j}. PREFER apify_collect(platform="reddit", query="…") — it '
            f'returns posts/comments WITH numbers (score, comments) and avoids the anon 403. Also use '
            f'search_reddit/fetch_reddit_thread for deep threads. Capture real quotes about "{brief}". Do not synthesize.'
        ),
        "twitch": (
            f'COLLECT step (Twitch) for job {j}. Use apify_collect(platform="twitch", query="…") for channel/'
            f'clip stats WITH numbers (views, followers, viewers); and read_twitch_chat for any channel live on '
            f'"{brief}" right now (no creds). Store real data via add_evidence. Do not synthesize.'
        ),
        "kick": (
            f'COLLECT step (Kick) for job {j}. NO key needed: search_kick(query="…") returns channel followers, '
            f'live status, viewer counts, and category WITH numbers about "{brief}"; then read_kick_chat(channel) '
            f'reads the ACTUAL live chat messages of any channel that is streaming on-topic. apify_collect'
            f'(platform="kick", …) is the paid alternative. Store via add_evidence. Do not synthesize.'
        ),
        "discord": (
            f'COLLECT step (Discord) for job {j}. Get the most Discord signal WITHOUT any token by stacking public '
            f'sources: (1) discord_landscape(topic="{brief}") — finds the relevant servers and returns member/online '
            f'counts + live voice activity WITH numbers; (2) discord_widget on any specific invite for a deeper live '
            f'read (online members, voice channels); (3) for message CONTENT that leaked publicly, search_reddit and '
            f'search_web for dev announcements, quotes, and recaps of those servers, and crawl_web_page the best pages. '
            f"Full in-channel reading is opt-in only — do not attempt it unless enabled. Store via add_evidence. Do not synthesize."
        ),
        "web": (
            f'COLLECT step (Web) for job {j}. Use search_web to find the most relevant public pages/articles about '
            f'"{brief}", then crawl_web_page the best few; for social platforms prefer apify_collect '
            f'(platform="tiktok"/"instagram"/"x"/"youtube") to get posts WITH numbers. Store real quotes/claims '
            f"via add_evidence. Do not synthesize."
        ),
    }
    return prompts.get(lane, prompts["web"])


def quantify_prompt(state: ResearchState) -> str:
    j, brief = state.job_id, state.brief
    return (
        f"QUANTIFY step for job {j}. Turn the data you collected into REAL numbers — this is what makes "
        f"the report consultant-grade instead of a summary. "
        f"(1) Platform metrics: call list_metric_fields({j}), then compute_metric / compute_rate for the "
        f'figures that matter for "{brief}" (median views/likes/followers by entity, or a rate like '
        f"shares/views), each grouped and carrying its sample size n. "
        f"(2) Chatter credibility: call analyze_chatter({j}) — it returns the recurring terms/phrases with "
        f"counts, per-channel breakdown, anonymized top voices, and a SUFFICIENCY verdict (did we hear "
        f"enough? saturated? enough substantive messages?). Note the recurring patterns and the sufficiency "
        f"verdict briefly. "
        f"If no numeric fields were captured, say so in one line — do not invent numbers. Do NOT synthesize or export."
    )


def verify_prompt(state: ResearchState) -> str:
    j = state.job_id
    return (
        f"VERIFY step for job {j}. Steelman-check the emerging answer. First get_research_dossier({j}) to read the "
        f"current synthesis and evidence. Then ATTACK the main claim from another angle: re-pull a thin sample for a "
        f"bigger n, cross-check a key number on a SECOND source or platform, or hunt the strongest DISCONFIRMING "
        f"evidence. Store what you find via add_evidence and note it as a verification check. Then report in 2–3 "
        f"sentences whether the claim held up, needs a caveat, or should change. Do NOT rewrite the synthesis or export."
    )


def synth_prompt(state: ResearchState, final: bool = False) -> str:
    parts = [
        f"SYNTHESIZE step for job {state.job_id}. Work ONLY through the research tools — do not write "
        f"code, run shells, or spawn sub-agents; you already have everything you need. First call "
        f"get_research_dossier({state.job_id}) (a compact, readable view) to see the evidence, coverage, "
        f"and source_runs ledger, and call compute_metric / compute_rate / analyze_chatter for numbers over "
        f"ALL rows. Then call write_research_synthesis to author a CONSULTANT-GRADE, RESULTS-FIRST answer:",
        "• LEAD WITH THE ANSWER AND THE NUMBERS. executive_answer directly answers the brief and puts the key "
        "computed figures in it (with n). point_of_view states a sharp thesis — take a position, and disagree "
        "with the brief's framing if the evidence warrants.",
        "• metrics_tables: paste the compute_metric/compute_rate tables AND the analyze_chatter patterns "
        "(recurring terms with counts, per-channel splits) — with n; never hand-type figures. method: state "
        "how the numbers were made (metric, query shapes, window, min sample, any cross-check).",
        "• ANONYMIZE speakers — refer to people by their pseudonym (user_xxxx) from analyze_chatter, never a "
        "real handle. In confidence, report the sufficiency verdict honestly (how much substantive chatter, "
        "saturated or not) — that is how the reader knows they can trust the answer.",
        "• themes: each backed by real quoted evidence with URLs and by numbers where you have them; cite by [n] "
        "into numbered_sources. recommendations: what we'd do.",
        "• If a source was blocked, ROUTE AROUND IT with a proxy/other angle and give it ONE line — keep "
        "limitations to a short closing caveat, never the headline. Never fabricate.",
    ]
    if state.named_platforms:
        parts.append(
            f"The user explicitly named: {', '.join(state.named_platforms)} — account for each, but lead with the "
            f"answer, not with what you couldn't get."
        )
    if state.gaps:
        parts.append(f"A prior review flagged: {'; '.join(state.gaps)} — address these.")
    parts.append(
        "Even if evidence is thin, synthesize what you HAVE with a real executive_answer and a point_of_view — do "
        "NOT finish this step without calling write_research_synthesis."
    )
    if final:
        parts.append("This is the FINAL synthesis; author it now from all evidence, metrics, and verification.")
    parts.append("Do NOT export.")
    return " ".join(parts)


def _has_synthesis(dossier: dict[str, Any]) -> bool:
    return bool((dossier.get("synthesis") or {}).get("executive_answer"))


def export_prompt(state: ResearchState) -> str:
    return f"EXPORT step for job {state.job_id}. Call export_research_report({state.job_id}) and report the folder path."


# --- the runner ---------------------------------------------------------------
RunNode = Callable[[str, str, int], Awaitable[dict[str, Any]]]
GetDossier = Callable[[int], dict[str, Any]]
Emit = Callable[[dict[str, Any]], None]

# Per-node inactivity timeout (seconds). A stalled node fails alone; the run continues.
TIMEOUTS = {"plan": 120, "discover": 120, "collect": 200, "quantify": 180,
            "synthesize": 300, "verify": 200, "export": 90}


async def run_graph(brief: str, emit: Emit, run_node: RunNode, get_dossier: GetDossier) -> None:
    state = ResearchState(brief=brief, named_platforms=detect_platforms(brief))
    planned = default_lanes(state)
    emit({"type": "graph_init", "named_platforms": state.named_platforms,
          "nodes": ["plan", "discover", *[f"collect:{l}" for l in planned],
                    "quantify", "synthesize", "review", "verify", "export"]})

    plan = await run_node("plan", plan_prompt(brief), TIMEOUTS["plan"])
    state.job_id = plan.get("job_id")
    if not state.job_id:
        emit({"type": "error", "message": (
            "The pipeline could not create a research job. This usually means the machine isn't signed in "
            "to Claude — run `claude auth login` in Terminal, then try again.")})
        return

    await run_node("discover", discover_prompt(state), TIMEOUTS["discover"])

    while True:
        state.round += 1
        lanes = lanes_for_gaps(state.gaps) if state.gaps else default_lanes(state)
        emit({"type": "note", "text": f"Round {state.round}: collecting from {', '.join(lanes)}."})
        await asyncio.gather(*[run_node(f"collect:{lane}", collect_prompt(lane, state), TIMEOUTS["collect"]) for lane in lanes])

        # QUANTIFY: turn the collected data into real numbers before we write the answer.
        await run_node("quantify", quantify_prompt(state), TIMEOUTS["quantify"])

        await run_node("synthesize", synth_prompt(state), TIMEOUTS["synthesize"])

        emit({"type": "node", "id": "review", "state": "running"})
        verdict, gaps = review_gate(state, get_dossier(state.job_id))
        state.gaps = gaps
        emit({"type": "node", "id": "review", "state": "done",
              "detail": verdict + (": " + "; ".join(gaps) if gaps else "")})
        if verdict == "pass":
            break
        emit({"type": "note", "text": f"Review found gaps — looping to collect more: {'; '.join(gaps)}"})

    # VERIFY: steelman the answer once it's backed — cross-check a key number / hunt disconfirming
    # evidence — then author the FINAL results-first synthesis incorporating what verify found.
    if _has_synthesis(get_dossier(state.job_id)):
        await run_node("verify", verify_prompt(state), TIMEOUTS["verify"])

    emit({"type": "note", "text": "Authoring the final results-first write-up."})
    await run_node("synthesize", synth_prompt(state, final=True), TIMEOUTS["synthesize"])

    if not _has_synthesis(get_dossier(state.job_id)):
        emit({"type": "error", "message": (
            "The pipeline collected evidence but could not author a synthesis, so there is no report yet. "
            "Ask it directly in the chat: 'write the synthesis and export the report for job "
            f"{state.job_id}'.")})
        return

    await run_node("export", export_prompt(state), TIMEOUTS["export"])
