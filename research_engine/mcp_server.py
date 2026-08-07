from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from db.queries import connect, migrate, seed_config
from research_engine.connectors import fetch_rss as fetch_rss_connector
from research_engine.connectors import search_x as search_x_connector
from research_engine.connectors import inspect_discord_invite as inspect_discord_connector
from research_engine.connectors import discord_widget as discord_widget_connector
from research_engine.connectors import discord_landscape as discord_landscape_connector
from research_engine.connectors import read_discord_channel as read_discord_connector
from research_engine.connectors import run_apify_actor as run_apify_connector
from research_engine.connectors import apify_collect as apify_collect_connector
from research_engine.connectors import search_kick as search_kick_connector
from research_engine.connectors import search_twitch as search_twitch_connector
from research_engine.connectors import read_twitch_chat as read_twitch_chat_connector
from research_engine.connectors import read_kick_chat as read_kick_chat_connector
from research_engine.connectors import crawl_web_page as crawl_web_connector
from research_engine.connectors import search_web as search_web_connector
from research_engine.connectors import discover_sources as discover_sources_connector
from research_engine.connectors import search_reddit as search_reddit_connector
from research_engine.connectors import fetch_reddit_thread as fetch_reddit_thread_connector
from research_engine.connectors import CONNECTOR_GUIDES, READY_CHECKS
from research_engine import metrics as metrics_engine
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
        "You are a capable researcher, not a data collector. Your deliverable is a report that "
        "ANSWERS the user's question — an executive answer, themes backed by real quoted evidence, "
        "where opinion splits, overall sentiment, recommendations, and one honest note on confidence. "
        "Raw evidence is an appendix, never the product.\n\n"
        "Method: (1) create_research_job — decompose the decision yourself and pass in questions, "
        "entities, and angles. (2) discover_sources / search_web to find WHERE the conversation "
        "actually happens. (3) Go read it: search_reddit + fetch_reddit_thread for real comments, "
        "crawl_web_page for articles and forums, the platform connectors when their credentials exist. "
        "Capture what people actually said (quotes), not headlines, via add_evidence. (4) Iterate — "
        "follow leads and keep collecting until you can answer confidently, not until each platform has "
        "one row. (5) write_research_synthesis to author the answer. (6) export_research_report and give "
        "the user the folder path.\n\n"
        "Hard rules: Answer the question. 'I can't reach X' is never a stopping point when the topic is "
        "public elsewhere — route around it. Truly private/closed venues (a Discord you're not in, live "
        "Twitch/Kick chat) get ONE sentence under limitations, never a section and never the headline. "
        "If a connector returns {\"not_configured\": true}, try its free fallbacks (search_web, "
        "search_reddit, crawl_web_page) — do not stop and do not hand the user a setup to-do list as the "
        "answer. Never export without a synthesis that answers the question.\n\n"
        "Report failures honestly. A source that RAN and returned nothing usable — an empty result "
        "set, HTTP 403/429, or an error — is a FAILED attempt, not an absent capability. Say "
        "'attempted, returned no usable data' (and why), NEVER 'not available'. 'Not available' is "
        "only for a source you never ran or cannot run (no credentials, a venue you're not in). "
        "get_research_dossier returns a source_runs ledger of what every attempt actually did; "
        "reconcile your confidence and limitations against it — do not describe an outcome the "
        "ledger contradicts."
    ),
)


# --- Source-run ledger --------------------------------------------------------
# Every collection tool records what actually happened (attempted, outcome, yield) into
# research_source_runs, so the authored synthesis can be held to the truth instead of
# narrating a failed/empty source as "not available". Recording is best-effort and never
# breaks a tool call.
def _yield_of(result: dict[str, Any]) -> int:
    for key in ("matched", "stored", "received", "scanned"):
        if result.get(key) is not None:
            return int(result[key])
    for key in ("results", "items", "comments", "discovered_links"):
        value = result.get(key)
        if isinstance(value, list):
            return len(value)
    if result.get("text") or result.get("guild_name") or result.get("post_stored"):
        return 1
    return 0


def _classify_error(exc: Exception) -> tuple[str, int | None]:
    import re

    text = str(exc)
    match = re.search(r"\b([1-5]\d\d)\b", text)
    status = int(match.group(1)) if match else None
    low = text.lower()
    if status in (403, 429) or "rate" in low or "blocked" in low or "too many" in low:
        return "rate_limited", status
    return "error", status


async def _tracked(connector: str, job_id: int, query: str, coro) -> dict[str, Any]:
    """Await a collection connector and record its real outcome in the ledger."""
    try:
        result = await coro
    except Exception as exc:
        outcome, status = _classify_error(exc)
        try:
            store.record_source_run(job_id, connector, query, outcome=outcome,
                                    http_status=status, yield_count=0, error_text=str(exc)[:1000])
        except Exception:
            pass
        raise
    if isinstance(result, dict):
        count = _yield_of(result)
        outcome = "not_configured" if result.get("not_configured") else ("ok" if count > 0 else "empty")
        try:
            store.record_source_run(job_id, connector, query, outcome=outcome, yield_count=count,
                                    error_text=str(result.get("error", ""))[:1000],
                                    raw_response_id=result.get("raw_response_id"))
        except Exception:
            pass
    return result


@mcp.tool()
def create_research_job(brief: str, decision: str = "", market: str = "", time_horizon: str = "",
                        audience: str = "", deliverable: str = "", required_sources: list[str] | None = None,
                        exclusions: list[str] | None = None, questions: list[str] | None = None,
                        entities: list[str] | None = None, angles: list[str] | None = None) -> dict[str, Any]:
    """Create and persist a research plan for any natural-language assignment.

    You are the researcher: decompose the decision yourself and pass in `questions` (the
    sub-questions you will answer), `entities` (the specific people/products/communities to
    track), and `angles` (the distinct lenses worth pursuing). If you omit them, Oyster
    falls back to generic scaffolding — supplying them is how the research gets sharp.
    """
    plan = build_plan(brief, decision, market, time_horizon, audience, deliverable,
                      required_sources, exclusions, questions, entities, angles)
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
    return await _tracked("rss", job_id, feed_url, fetch_rss_connector(store, job_id, feed_url, query_terms, limit))


@mcp.tool()
async def search_x(job_id: int, query: str, max_results: int = 25) -> dict[str, Any]:
    """Search recent public X posts through the official API and store them as evidence."""
    return await _tracked("x", job_id, query, search_x_connector(store, job_id, settings.x_bearer_token, query, max_results))


@mcp.tool()
async def inspect_discord_invite(job_id: int, invite_url_or_code: str) -> dict[str, Any]:
    """Inspect and store public metadata for any Discord invite discovered during research."""
    return await _tracked("discord_invite", job_id, invite_url_or_code, inspect_discord_connector(store, job_id, invite_url_or_code))


@mcp.tool()
async def discord_landscape(job_id: int, topic: str, limit: int = 6) -> dict[str, Any]:
    """Map the Discord communities for a TOPIC using only public data — no token, no bot. Web
    search finds the servers + invites, then each is enriched with member/online counts and the
    public widget (live online members + active voice channels). Returns a ranked list with the
    numbers in metadata.metrics. This is the free 'community landscape' signal; it does not read
    message content."""
    return await _tracked("discord_landscape", job_id, topic,
                          discord_landscape_connector(store, job_id, topic, limit))


@mcp.tool()
async def discord_widget(job_id: int, invite_or_guild: str) -> dict[str, Any]:
    """Read a Discord server's PUBLIC widget (no token): live online count, a sample of online
    members with status, and active voice channels. Accepts an invite code/URL or a guild id. A
    server with the widget disabled returns widget_enabled=False, not an error."""
    return await _tracked("discord_widget", job_id, invite_or_guild,
                          discord_widget_connector(store, job_id, invite_or_guild))


@mcp.tool()
async def run_apify_actor(job_id: int, actor_id: str, actor_input: dict[str, Any],
                          source_type: str, limit: int = 100) -> dict[str, Any]:
    """Run any chosen Apify Actor and store its dataset items as evidence."""
    return await _tracked(f"apify:{source_type}", job_id, actor_id,
                          run_apify_connector(store, job_id, settings.apify_token, actor_id, actor_input, source_type, limit))


@mcp.tool()
async def apify_collect(job_id: int, platform: str, query: str, intent: str = "",
                        limit: int = 30, days: int = 90) -> dict[str, Any]:
    """Collect real DATA + NUMBERS from a platform via the curated Apify engine — the preferred way
    to get Reddit, X, TikTok, Instagram, YouTube, Twitch, or Kick data. It picks the RIGHT actor for
    the platform (no guessing), enforces the date window, and extracts normalized metrics
    (views/likes/comments/shares/followers) into each evidence row's metadata so you can compute
    medians/rates with compute_metric. Requires APIFY_TOKEN (Settings ⚙). `platform` e.g. 'tiktok',
    'reddit', 'x', 'instagram', 'youtube', 'twitch', 'kick'; `intent` optional (e.g. 'hashtag',
    'profile'). Discord messages are opt-in and need a separate token — use inspect_discord_invite
    for community-landscape signal by default."""
    platform_token = settings.discord_research_token if platform.lower() == "discord" else ""
    return await _tracked(f"apify:{platform}", job_id, query,
                          apify_collect_connector(store, job_id, settings.apify_token, platform,
                                                  intent, query, limit, days, platform_token=platform_token))


@mcp.tool()
async def search_twitch(job_id: int, query: str, limit: int = 40) -> dict[str, Any]:
    """Search arbitrary Twitch channels and categories rather than a fixed game watchlist."""
    return await _tracked("twitch", job_id, query,
                          search_twitch_connector(store, job_id, settings.twitch_client_id, settings.twitch_client_secret, query, limit))


@mcp.tool()
async def read_twitch_chat(job_id: int, channel: str, seconds: int = 8, limit: int = 200) -> dict[str, Any]:
    """Read a Twitch channel's LIVE chat via anonymous IRC — no credentials, no API key.

    Live Twitch chat IS autonomously retrievable this way. Give a channel name (e.g. 'xqc');
    Oyster listens for a few seconds and stores the chat messages as evidence. An empty
    result means the channel was offline or chat was quiet during the window.
    """
    return await _tracked("twitch_chat", job_id, channel,
                          read_twitch_chat_connector(store, job_id, channel, seconds, limit))


@mcp.tool()
async def read_kick_chat(job_id: int, channel: str, seconds: int = 12, limit: int = 200) -> dict[str, Any]:
    """Read a Kick channel's LIVE chat via its public Pusher websocket — no credentials, no API key.

    The Kick twin of read_twitch_chat. Give a channel slug (e.g. 'xqc'); Oyster resolves the
    chatroom, listens for a few seconds, and stores each chat message as evidence. An empty
    result means the channel was offline or chat was quiet during the window.
    """
    return await _tracked("kick_chat", job_id, channel,
                          read_kick_chat_connector(store, job_id, channel, seconds, limit))


@mcp.tool()
async def search_kick(job_id: int, query: str, pages: int = 3) -> dict[str, Any]:
    """Search Kick for any topic, category, creator, or slug — FREE, no key required. Returns
    channel followers, live status, viewer counts, and category (numbers land in metadata.metrics
    for compute_metric). Uses Kick's public API by default; the official OAuth API if creds are set."""
    return await _tracked("kick", job_id, query,
                          search_kick_connector(store, job_id, settings.kick_client_id, settings.kick_client_secret, query, pages))


@mcp.tool()
async def read_discord_channel(job_id: int, channel_id: str, limit: int = 50) -> dict[str, Any]:
    """Read messages from a Discord channel where the configured bot has explicit access."""
    return await _tracked("discord_channel", job_id, channel_id,
                          read_discord_connector(store, job_id, settings.discord_bot_token, channel_id, limit))


@mcp.tool()
async def crawl_web_page(job_id: int, url: str, query: str = "") -> dict[str, Any]:
    """Collect readable text and links from a public webpage. Uses a server-side URL→markdown
    reader for JS-heavy pages when a web-reader key is set (no browser needed), else httpx+Scrapling."""
    return await _tracked("web_crawl", job_id, url,
                          crawl_web_connector(store, job_id, url, query, reader_key=settings.web_reader_api_key))


@mcp.tool()
async def search_web(job_id: int, query: str, limit: int = 10) -> dict[str, Any]:
    """Search the open web for a query and return ranked {title, url, snippet} leads to pursue.

    Results are leads, not stored evidence — read the promising ones and store what answers
    the question. Uses a configured search key if present, otherwise a free web provider.
    """
    return await _tracked("web_search", job_id, query,
                          search_web_connector(store, job_id, query, limit,
                                               tavily_key=settings.tavily_api_key, brave_key=settings.brave_api_key,
                                               serper_key=settings.serper_api_key))


@mcp.tool()
async def discover_sources(job_id: int, topic: str) -> dict[str, Any]:
    """Find where a topic is actually being discussed, grouped by venue (reddit, forums, news, etc.)."""
    return await _tracked("discover_sources", job_id, topic,
                          discover_sources_connector(store, job_id, topic,
                                                     tavily_key=settings.tavily_api_key, brave_key=settings.brave_api_key,
                                                     serper_key=settings.serper_api_key))


@mcp.tool()
async def search_reddit(job_id: int, query: str, limit: int = 25, subreddit: str = "") -> dict[str, Any]:
    """Search Reddit's public JSON for posts on a topic and store matches as evidence (free, no key)."""
    return await _tracked("reddit_search", job_id, query, search_reddit_connector(store, job_id, query, limit, subreddit))


@mcp.tool()
async def fetch_reddit_thread(job_id: int, url: str, max_comments: int = 40) -> dict[str, Any]:
    """Read a Reddit thread's post and top comments (public JSON) and store them as evidence (free, no key)."""
    return await _tracked("reddit_thread", job_id, url, fetch_reddit_thread_connector(store, job_id, url, max_comments))


@mcp.tool()
def get_research_dossier(job_id: int) -> dict[str, Any]:
    """Return a research job with its plan, evidence, source coverage, and the source_runs
    ledger — every collection attempt and its real outcome (ok/empty/rate_limited/error/
    not_configured). Reconcile your synthesis's limitations against source_runs: a run that
    is 'empty' or 'error' was attempted and failed, and must not be reported as 'not available'."""
    return store.dossier(job_id)


@mcp.tool()
def list_metric_fields(job_id: int) -> dict[str, Any]:
    """List the numeric fields you can compute over for this job (views, likes, followers, score, …)
    plus the grouping options (entity / query_shape / source_type). Call before compute_metric."""
    return metrics_engine.list_metric_fields(store.dossier(job_id)["evidence"])


@mcp.tool()
def compute_metric(job_id: int, value_field: str, group_by: str = "entity", min_sample: int = 1) -> dict[str, Any]:
    """Compute REAL numbers over collected evidence: median/mean/min/max and the sample size n of a
    numeric field (e.g. views, followers), grouped by entity / query_shape / source_type. This is how
    you cite figures with sample sizes instead of eyeballing quotes. See list_metric_fields first."""
    return metrics_engine.compute_metric(store.dossier(job_id)["evidence"], value_field, group_by, min_sample)


@mcp.tool()
def compute_rate(job_id: int, numerator: str, denominator: str, group_by: str = "entity",
                 min_sample: int = 1) -> dict[str, Any]:
    """Compute a RATE — numerator/denominator per item (e.g. shares/views = share rate) — as a median
    percentage per group and overall, with sample sizes. The kind of metric a consultant report leads with."""
    return metrics_engine.compute_rate(store.dossier(job_id)["evidence"], numerator, denominator, group_by, min_sample)


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
def write_research_synthesis(
    job_id: int,
    executive_answer: str,
    point_of_view: str = "",
    themes: list[dict[str, Any]] | None = None,
    metrics_tables: list[dict[str, Any]] | None = None,
    method: str = "",
    tensions: str = "",
    sentiment: str = "",
    recommendations: list[str] | None = None,
    confidence: str = "",
    limitations: str = "",
    numbered_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Author the report that answers the question, and persist it as the job's deliverable.

    Write like a consultant, not a data collector: LEAD WITH THE ANSWER AND THE NUMBERS, take a
    position, and demote what you couldn't get to one short closing paragraph. Call this once you
    can answer the brief from the evidence — thin evidence still gets a synthesis, with the limits
    stated honestly.

    - executive_answer: 2-5 sentences that directly answer the brief, with the key computed
      numbers in them (e.g. "share rate 1.0%, n=22"). Required.
    - point_of_view: the headline POSITION — a sharp, decision-ready thesis that may disagree with
      the brief's framing. This is what separates a consultant report from a summary. One or two
      sentences. State it even when it pushes back on what was asked.
    - themes: list of {"title", "insight", "citations": [{"quote", "url", "source"}]} — the key
      findings, each backed by real quoted evidence with its URL and by numbers where you have them.
      Cite computed figures by [n] into numbered_sources. Quote what people actually said.
    - metrics_tables: the computed numbers, structured. Each: {"title", "unit" (e.g. "%"/"views"),
      "group_by", "rows": [{"group", "median", "n", ...}], "note"}. Build these from compute_metric /
      compute_rate output — never hand-type figures. Every table must carry sample sizes (n).
    - method: how the numbers were made — the metric chosen, query shapes, window, min sample, and
      any triangulation/verify cross-check you ran. This is what makes the numbers trustworthy.
    - tensions: where opinion splits or evidence disagrees.
    - sentiment: the overall mood and how it breaks down.
    - recommendations: concrete, decision-ready next actions ("what we'd do").
    - confidence: one honest paragraph on how confident you are and why.
    - limitations: ONE short closing paragraph on what constrained the research — a caveat, never
      the headline. Distinguish HONESTLY using the source_runs ledger: sources never run are "not
      available"; sources that RAN and returned nothing usable are "attempted, returned no usable
      data". When a source was blocked, say in one line what proxy/angle you used instead.
    - numbered_sources: the [n] citation ledger — list of {"n", "label", "tool", "url",
      "pulled_at", "note"} that every [n] in the report resolves to. Build from the source_runs
      ledger and list_raw_responses.

    export_research_report will refuse to run until this exists.
    """
    if not executive_answer.strip():
        raise ValueError("executive_answer is required — the report must answer the question.")
    synthesis = {
        "executive_answer": executive_answer.strip(),
        "point_of_view": point_of_view.strip(),
        "themes": themes or [],
        "metrics_tables": metrics_tables or [],
        "method": method.strip(),
        "tensions": tensions.strip(),
        "sentiment": sentiment.strip(),
        "recommendations": recommendations or [],
        "confidence": confidence.strip(),
        "limitations": limitations.strip(),
        "numbered_sources": numbered_sources or [],
    }
    return store.save_synthesis(job_id, synthesis)


@mcp.tool()
def export_research_report(job_id: int) -> dict[str, Any]:
    """Write the full deliverable package for a job into output/: report.md/.html/.docx, deck.pptx,
    charts/ (PNG+SVG+CSV of the metrics), Sources-and-Citations.md (the numbered [n] ledger),
    README-start-here.md, raw-data/, and evidence.json/.csv. Refuses until a synthesis exists."""
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
    """Reusable workflow for producing a report that answers a research question."""
    return f"""You are the researcher. Produce a report that ANSWERS this: {brief}
Decision to support: {decision or 'Infer it from the brief, and state the inference.'}

Your deliverable is an answer, not a pile of data. The report must open with an executive
answer and back its themes with real quotes people actually wrote, each with a URL.

1. DECOMPOSE. Break the decision into the specific sub-questions you must answer and the
   entities (products, people, communities) you must track. Call create_research_job and pass
   them in as questions/entities/angles — do not rely on defaults.
2. DISCOVER where the conversation actually happens. Use discover_sources and search_web to
   find the subreddits, forums, threads, videos, and articles that discuss this. Do not force
   platforms that add nothing.
3. COLLECT SUBSTANCE. Go read the sources: search_reddit + fetch_reddit_thread for real
   comment text, crawl_web_page for articles and forum threads, and the platform connectors
   when their credentials exist. add_evidence with the actual quote and a short note on the
   stance/sentiment — capture what people said, not headlines.
4. ITERATE. Follow the strongest leads, chase disagreements, and keep collecting until you can
   answer each sub-question confidently (saturation). Use get_research_dossier to see what you
   have; coverage is a progress signal, not a finish line.
5. ROUTE AROUND WALLS. If a connector is not configured or a venue is closed, use the free
   fallbacks (search_web / search_reddit / crawl_web_page). A private Discord or live stream
   chat you can't reach is at most one sentence in the limitations — never the story.
6. SYNTHESIZE. Call write_research_synthesis with: an executive_answer that directly answers
   the brief; themes, each with real quoted citations (quote + url); tensions; sentiment;
   recommendations; a confidence paragraph; and one short limitations note.
7. EXPORT. Call export_research_report and give the user the folder path. It will refuse if you
   have not written the synthesis first — because a report answers the question."""


def main() -> None:
    with connect(settings.database_url) as conn:
        migrate(conn)
        seed_config(conn)
    # Normally stdio (one server per agent client). When OYSTER_MCP_HTTP_PORT is set, run a
    # single long-lived streamable-HTTP server instead, so the Studio can share ONE MCP process
    # across every graph node (no per-node Python-import + DB-connect startup). See studio.py.
    port = os.environ.get("OYSTER_MCP_HTTP_PORT")
    if port:
        mcp.run(transport="streamable-http",
                host=os.environ.get("OYSTER_MCP_HTTP_HOST", "127.0.0.1"),
                port=int(port))
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
