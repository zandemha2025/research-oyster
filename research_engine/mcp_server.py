from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from db.queries import connect, migrate, seed_config
from research_engine.connectors import fetch_rss as fetch_rss_connector
from research_engine.connectors import search_x as search_x_connector
from research_engine.connectors import inspect_discord_invite as inspect_discord_connector
from research_engine.connectors import read_discord_channel as read_discord_connector
from research_engine.connectors import run_apify_actor as run_apify_connector
from research_engine.connectors import search_kick as search_kick_connector
from research_engine.connectors import search_twitch as search_twitch_connector
from research_engine.connectors import crawl_web_page as crawl_web_connector
from research_engine.connectors import CONNECTOR_GUIDES, READY_CHECKS
from research_engine.planner import build_plan
from research_engine.capture import CaptureStore
from research_engine.export import export_job
from research_engine.store import ResearchStore
from settings import Settings


settings = Settings()
store = ResearchStore(settings.database_url)
mcp = MCPServer(
    "Research Oyster",
    instructions=(
        "Use this server for open-ended research assignments. Start with create_research_job. "
        "Use the returned plan to discover evidence with native search tools and Oyster connectors. "
        "Store useful findings with add_evidence, then call get_research_dossier before synthesizing. "
        "If a connector returns {\"not_configured\": true}, do not give up: try its listed fallbacks "
        "in order, finish with the evidence you have, and report every remaining gap together with its "
        "setup instructions from connector_status. Always end a job by calling export_research_report so "
        "the user gets the report and raw evidence as files. Cite evidence URLs and distinguish observations "
        "from inference."
    ),
)


@mcp.tool()
def create_research_job(brief: str, decision: str = "", market: str = "", time_horizon: str = "",
                        audience: str = "", deliverable: str = "", required_sources: list[str] | None = None,
                        exclusions: list[str] | None = None) -> dict[str, Any]:
    """Create and persist a source-agnostic research plan from any natural-language assignment."""
    plan = build_plan(brief, decision, market, time_horizon, audience, deliverable, required_sources, exclusions)
    return store.create_job(brief, decision, market, time_horizon, plan)


@mcp.tool()
def add_evidence(job_id: int, source_type: str, url: str, title: str, excerpt: str,
                 author: str = "", published_at: str = "", query: str = "",
                 metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store one relevant finding collected by the host or an external connector with provenance."""
    timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00")) if published_at else None
    return store.add_evidence(job_id, source_type=source_type, url=url, title=title, excerpt=excerpt,
                              author=author, published_at=timestamp, query=query, metadata=metadata)


@mcp.tool()
async def fetch_rss(job_id: int, feed_url: str, query_terms: list[str], limit: int = 50) -> dict[str, Any]:
    """Fetch an RSS/Atom feed and store entries matching any supplied research term."""
    return await fetch_rss_connector(store, job_id, feed_url, query_terms, limit)


@mcp.tool()
async def search_x(job_id: int, query: str, max_results: int = 25) -> dict[str, Any]:
    """Search recent public X posts through the official API and store them as evidence."""
    return await search_x_connector(store, job_id, settings.x_bearer_token, query, max_results)


@mcp.tool()
async def inspect_discord_invite(job_id: int, invite_url_or_code: str) -> dict[str, Any]:
    """Inspect and store public metadata for any Discord invite discovered during research."""
    return await inspect_discord_connector(store, job_id, invite_url_or_code)


@mcp.tool()
async def run_apify_actor(job_id: int, actor_id: str, actor_input: dict[str, Any],
                          source_type: str, limit: int = 100) -> dict[str, Any]:
    """Run any chosen Apify Actor and store its dataset items as evidence."""
    return await run_apify_connector(store, job_id, settings.apify_token, actor_id, actor_input, source_type, limit)


@mcp.tool()
async def search_twitch(job_id: int, query: str, limit: int = 40) -> dict[str, Any]:
    """Search arbitrary Twitch channels and categories rather than a fixed game watchlist."""
    return await search_twitch_connector(store, job_id, settings.twitch_client_id, settings.twitch_client_secret, query, limit)


@mcp.tool()
async def search_kick(job_id: int, query: str, pages: int = 3) -> dict[str, Any]:
    """Search active Kick streams for any topic, category, creator, or title."""
    return await search_kick_connector(store, job_id, settings.kick_client_id, settings.kick_client_secret, query, pages)


@mcp.tool()
async def read_discord_channel(job_id: int, channel_id: str, limit: int = 50) -> dict[str, Any]:
    """Read messages from a Discord channel where the configured bot has explicit access."""
    return await read_discord_connector(store, job_id, settings.discord_bot_token, channel_id, limit)


@mcp.tool()
async def crawl_web_page(job_id: int, url: str, query: str = "") -> dict[str, Any]:
    """Collect readable text and links from a public webpage with the self-hosted adaptive crawler."""
    return await crawl_web_connector(store, job_id, url, query)


@mcp.tool()
def get_research_dossier(job_id: int) -> dict[str, Any]:
    """Return a research job with its plan, evidence, source coverage, and remaining gaps."""
    return store.dossier(job_id)


@mcp.tool()
def list_research_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """List recent assignments so an agent can resume prior work."""
    return store.list_jobs(max(1, min(limit, 100)))


@mcp.tool()
def connector_status() -> dict[str, Any]:
    """Explain which research connectors are ready and the easiest setup or fallback for each."""
    return {
        name: {"ready": READY_CHECKS.get(name, lambda s: True)(settings), **guide}
        for name, guide in CONNECTOR_GUIDES.items()
    }


@mcp.tool()
def export_research_report(job_id: int) -> dict[str, Any]:
    """Write report.md, report.html, evidence.json, evidence.csv, and raw_responses.jsonl for a job into output/."""
    return export_job(store, job_id, settings.output_dir)


@mcp.tool()
def get_browser_capture_mission(job_id: int) -> dict[str, Any]:
    """Turn an existing research brief into questions and terms for supervised browser capture."""
    return CaptureStore(settings.database_url).mission(job_id)


@mcp.tool()
def request_browser_traffic_session(job_id: int, domain: str, reason: str) -> dict[str, Any]:
    """Ask the researcher to approve a time-limited, single-domain browser traffic capture session.

    Capture does not begin until the researcher approves the request in the Oyster extension.
    """
    result = CaptureStore(settings.database_url).request_session(job_id, domain, reason)
    result["instructions"] = (
        "Ask the user to open the Research Oyster extension popup and approve this capture "
        f"session for {result['domain']}, then poll get_browser_traffic_session with session_id "
        f"{result['id']} until status is 'approved'. Nothing is captured before approval."
    )
    return result


@mcp.tool()
def get_browser_traffic_session(session_id: int) -> dict[str, Any]:
    """Check whether a requested browser traffic capture session has been approved yet."""
    return CaptureStore(settings.database_url).session_status(session_id)


@mcp.prompt()
def research_assignment(brief: str, decision: str = "") -> str:
    """Reusable workflow for completing an evidence-backed research assignment."""
    return f"""Complete this research assignment: {brief}
Decision to support: {decision or 'Infer it, and state the inference.'}

1. Call create_research_job before searching.
2. Review its questions, source-specific queries, and clarifications. Infer sensible defaults; ask the user only when a missing answer would materially change the result.
3. Call connector_status and choose the strongest ready route: official API, RSS, Apify, then self-hosted web crawl.
   When useful evidence is visible only inside a page the researcher is authorized to access, use the supervised browser-capture mission and let the user approve captures.
4. Discover relevant sources. Use native web tools and Oyster connectors; do not force irrelevant platforms.
5. If any connector returns {{"not_configured": true}}, do not stop. Try its listed fallbacks in order until one produces evidence, then continue.
6. Call add_evidence for every finding that may support a material claim.
7. Triangulate important claims across independent sources and actively seek counterevidence.
8. Call get_research_dossier and identify coverage gaps before finishing.
9. Never end on a bare failure. Deliver findings, implications, opportunities, risks, and recommended next actions with direct citations from the evidence you gathered, even when some sources were unavailable.
10. For every remaining gap, tell the user exactly how to unlock it using the setup instructions from connector_status — do not just say you gave up.
11. Label observations, inferences, and recommendations separately.
12. Call export_research_report and give the user the path to the exported folder so they have the report and raw evidence as files, not only chat."""


def main() -> None:
    with connect(settings.database_url) as conn:
        migrate(conn)
        seed_config(conn)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
