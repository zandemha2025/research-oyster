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

MAX_ROUNDS = 2

# Platforms a user might name, and which collection lane serves each.
KNOWN_PLATFORMS = ["discord", "twitch", "tiktok", "instagram", "reddit", "kick", "youtube", "twitter"]
LANE_FOR = {
    "discord": "discord", "twitch": "twitch", "reddit": "reddit",
    "kick": "web", "tiktok": "web", "instagram": "web", "youtube": "web", "twitter": "web",
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
    if len(dossier.get("evidence") or []) < 3:
        gaps.append("very thin evidence collected")
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
            f'COLLECT step (Reddit) for job {j}. Find and read REAL Reddit discussion about "{brief}" '
            f"using search_reddit and fetch_reddit_thread; if Reddit's API is blocked (403), crawl "
            f"old.reddit.com threads with crawl_web_page. Store real user quotes via add_evidence. Do not synthesize."
        ),
        "twitch": (
            f'COLLECT step (Twitch) for job {j}. Twitch chat is LIVE and per-channel via read_twitch_chat '
            f'(no credentials needed). Identify any channel likely discussing "{brief}" right now and read '
            f"its chat; if nothing relevant is live, record that plainly. Store anything real via add_evidence. Do not synthesize."
        ),
        "discord": (
            f'COLLECT step (Discord) for job {j}. Use inspect_discord_invite to capture public metadata for '
            f'the most relevant community about "{brief}". read_discord_channel only works if the Oyster bot '
            f"is already in that server — if it isn't, state that plainly as an access limit. Store what you get "
            f"via add_evidence. Do not synthesize."
        ),
        "web": (
            f'COLLECT step (Web) for job {j}. Use search_web to find the most relevant public pages/articles '
            f'about "{brief}", then crawl_web_page the best few and store real quotes/claims via add_evidence. '
            f"Do not synthesize."
        ),
    }
    return prompts.get(lane, prompts["web"])


def synth_prompt(state: ResearchState, final: bool = False) -> str:
    parts = [
        f"SYNTHESIZE step for job {state.job_id}. First call get_research_dossier({state.job_id}) to see all "
        f"evidence and the source_runs ledger. Then you MUST call write_research_synthesis with a NON-EMPTY "
        f"executive_answer that directly answers the brief, plus themes (each backed by real cited quotes with "
        f"URLs where you have them), tensions, sentiment, recommendations, confidence, and limitations."
    ]
    if state.named_platforms:
        parts.append(
            f"The user explicitly named these platforms: {', '.join(state.named_platforms)} — structure the answer "
            f"to account for each one honestly (what you got, what you couldn't and why, per the ledger)."
        )
    if state.gaps:
        parts.append(f"A prior review found these gaps to address: {'; '.join(state.gaps)}.")
    parts.append(
        "Even if the evidence is thin, synthesize what you HAVE and state the limits honestly — do NOT finish "
        "this step without calling write_research_synthesis with a real executive_answer."
    )
    if final:
        parts.append("This is the FINAL synthesis attempt; author it now from whatever evidence exists.")
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
TIMEOUTS = {"plan": 120, "discover": 120, "collect": 200, "synthesize": 300, "export": 90}


async def run_graph(brief: str, emit: Emit, run_node: RunNode, get_dossier: GetDossier) -> None:
    state = ResearchState(brief=brief, named_platforms=detect_platforms(brief))
    planned = default_lanes(state)
    emit({"type": "graph_init", "named_platforms": state.named_platforms,
          "nodes": ["plan", "discover", *[f"collect:{l}" for l in planned], "synthesize", "review", "export"]})

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

        await run_node("synthesize", synth_prompt(state), TIMEOUTS["synthesize"])

        emit({"type": "node", "id": "review", "state": "running"})
        verdict, gaps = review_gate(state, get_dossier(state.job_id))
        state.gaps = gaps
        emit({"type": "node", "id": "review", "state": "done",
              "detail": verdict + (": " + "; ".join(gaps) if gaps else "")})
        if verdict == "pass":
            break
        emit({"type": "note", "text": f"Review found gaps — looping to collect more: {'; '.join(gaps)}"})

    # Guarantee a synthesis exists before export — otherwise export_research_report has nothing
    # to write and the run ends with no report. If the loop never produced one, force a final pass.
    if not _has_synthesis(get_dossier(state.job_id)):
        emit({"type": "note", "text": "No synthesis yet — forcing a final write-up from the evidence gathered."})
        await run_node("synthesize", synth_prompt(state, final=True), TIMEOUTS["synthesize"])

    if not _has_synthesis(get_dossier(state.job_id)):
        emit({"type": "error", "message": (
            "The pipeline collected evidence but could not author a synthesis, so there is no report yet. "
            "Ask it directly in the chat: 'write the synthesis and export the report for job "
            f"{state.job_id}'.")})
        return

    await run_node("export", export_prompt(state), TIMEOUTS["export"])
