# Research Oyster: product and build plan

## Product definition

Research Oyster is a reusable research-engine MCP server. A user gives an AI host a plain-language assignment, not a list of feeds or communities. The host uses Oyster to turn that assignment into a research plan, discover and collect evidence across relevant sources, retain provenance, synthesize findings, and optionally convert the work into a recurring monitor.

Example: “Develop a Christmas 2026 campaign for Kirkland Italian sparkling mineral water.” Oyster should propose brand, category, audience, occasion, competitor, cultural, and whitespace questions; search relevant public surfaces; and return evidence that helps make the campaign decision.

The browser control center is an optional operator client. MCP is the primary product boundary so Claude Code, Codex, another agent, or a first-party platform can use the same engine.

## Product principles

1. Start with the decision the research must support.
2. Discover sources after understanding the brief; never require a fixed watchlist.
3. Choose sources by expected information value. Twitch is not useful for every assignment.
4. Preserve the URL, timestamp, source type, query, and excerpt for every claim.
5. Separate evidence from interpretation and label uncertainty.
6. Produce an immediate answer first; recurring monitoring is an optional continuation.
7. Use official APIs where available and make credentials optional per connector.

## Primary workflow

1. `create_research_job` accepts the brief, decision, market, and time horizon.
2. It returns research questions, entities, query families, recommended source types, and gaps.
3. The host searches with its native tools and/or calls Oyster connectors.
4. `add_evidence` stores useful findings from any source with provenance.
5. `get_research_dossier` returns an evidence bundle grouped by question and source.
6. The host writes the answer, cites the evidence, and records unresolved questions.
7. `create_monitor` later turns selected queries and sources into recurring collection.

## What the user provides

Only the subject and desired outcome are required. The strongest short brief contains:

- **Subject:** what is being researched.
- **Decision:** what choice the findings must help make.
- **Market:** geography or cultural market, when relevant.
- **Audience:** known priority audience, or permission for Oyster to discover it.
- **Time horizon:** current conversation, a historical window, or a future occasion.
- **Required sources:** only when the assignment explicitly needs a platform.
- **Exclusions:** sources, communities, competitors, or data types that must not be used.
- **Deliverable:** campaign territories, market map, trend report, creator list, risk assessment, etc.

The user may supply all of this in one ordinary sentence. Oyster extracts a research plan and source-specific queries. Missing details are inferred when safe. The host asks a clarification only when different answers would materially alter the collection strategy or conclusion.

Example: “Find consumer tensions and creative opportunities for a US Christmas 2026 Kirkland Italian sparkling mineral water campaign. Include Reddit and X, and give me three campaign territories.”

## MCP surface

### Phase 1: usable local MCP

- `create_research_job`: plan any arbitrary brief.
- `add_evidence`: accept evidence collected by the host or another connector.
- `fetch_rss`: fetch and store matching RSS/Atom items.
- `search_x`: search recent X posts when `X_BEARER_TOKEN` is configured.
- `inspect_discord_invite`: inspect any public Discord invite discovered by the host.
- `run_apify_actor`: run any user-selected Actor for Reddit, X, discovery, or a new source without changing Oyster.
- `search_twitch` and `search_kick`: search arbitrary topics instead of fixed gaming watchlists.
- `read_discord_channel`: collect messages only where the configured bot has explicit permission.
- `connector_status`: show what is ready and the exact setup or fallback for every connector.
- `get_research_dossier`: return the brief, plan, evidence, coverage, and gaps.
- `list_research_jobs`: resume previous work.
- `research_assignment` prompt: teach an MCP host the complete research loop.
- `get_browser_capture_mission`: derive supervised browser terms and questions from the original job.
- Research Oyster Capture extension: queue selected or currently visible evidence locally and promote only user-approved excerpts into the original dossier.

### Phase 2: autonomous discovery

- Add configurable web-search providers (Brave, Tavily, Serper, or platform-native delegation).
- Discover RSS/Atom feeds, Discord invites, subreddits, X accounts, YouTube channels, forums, and publications from search results.
- Add Reddit through Apify and Discord invite/widget collection for discovered communities.
- Add entity resolution, duplicate detection, query refinement, and source-quality scoring.

### Phase 3: synthesis and monitoring

- Claim/evidence graph with contradiction detection.
- Reference-quality and freshness scoring.
- Saved monitor definitions, scheduler, change detection, and alerts.
- Export to Markdown, HTML, JSON, Google Docs, Notion, or a platform API.

### Phase 4: hosted platform

- Streamable HTTP transport with OAuth and tenant isolation.
- Encrypted connector credentials, quotas, audit logs, retention controls, and billing.
- Job queue and workers for long-running collection.
- Web application for briefs, evidence review, monitors, and exports.

## Source strategy

| Source | Discovery | Collection | Credential |
|---|---|---|---|
| Web/RSS | Search results and page feed links | HTTP + feedparser | Optional search-provider key |
| X | Query/account discovery | Official recent/full-archive API | X bearer token |
| Reddit | Search/discovered subreddits | Apify actor or Reddit API | Provider token |
| Discord | Web/invite discovery | Public invite and widget metadata | None for public metadata |
| Twitch/Kick | Category/creator discovery | Existing collectors | Platform credentials |
| Host-native sources | Host decides and searches | `add_evidence` ingestion | Managed by host |

## Architecture

```text
Claude / Codex / platform
          |
       MCP stdio                         optional Streamable HTTP
          |
  research_engine.mcp_server
          |
  planner -- jobs -- evidence store -- dossier
          |                 |
  connectors          PostgreSQL
  RSS / X / host / later Reddit, Discord, web
```

The MCP process must never log to stdout because stdio carries JSON-RPC. Diagnostics go to stderr. Local mode trusts the local user. The browser capture boundary separately requires loopback binding, one-time pairing, hashed revocable tokens, exact extension-origin checks, explicit approval, bounded visible-page scanning, and expiring pending content. Hosted mode requires OAuth, tenant-scoped authorization, SSRF controls, encrypted secrets, rate limits, and background workers before exposure.

## Definition of done for the first release

- A user can attach the server to an MCP host in under five minutes.
- An arbitrary non-gaming brief produces a coherent, editable research plan.
- The host can store findings from any of its own tools.
- RSS and X connectors return provenance-bearing evidence.
- A dossier can be resumed across sessions.
- No platform credential is required unless that platform is selected.
- Automated tests cover planning, persistence, RSS ingestion, X error behavior, and MCP tool discovery.
