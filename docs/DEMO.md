# Demo runbook: Research Oyster in ~15 minutes

A tested, credential-free demo script. Everything in this runbook works with **no API keys** — only Python, PostgreSQL, and an internet connection for the RSS step.

## The story you are telling

> "Research tools either give you a chat answer with no receipts, or a firehose of scraped data with no plan. Research Oyster sits in the middle: you give it a plain-language brief, it builds a research plan, collects evidence with full provenance into a local database, tracks what's still missing, and hands any MCP-capable AI host a citable dossier it can resume later."

Three beats, in order:

1. **The control center** — visual proof it's a real product, not a script.
2. **The MCP flow in Claude Code / Codex** — the core product: brief → plan → evidence → dossier.
3. **Provenance and resumability** — what makes it different: every claim has a URL, a query, and a timestamp, and jobs survive restarts.

## Before the demo (do this the night before, ~10 min)

```sh
cd research-oyster
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # default DATABASE_URL=postgresql:///gaming_pulse
createdb gaming_pulse
python main.py migrate        # prints "Database migrated and configuration seeded."
python -m pytest tests/ -q    # expect all passing (network/credential tests skip)
```

Attach the MCP server to your AI host, then restart the host:

```sh
claude mcp add research-oyster -- "$(pwd)/research-oyster-mcp"
# or: codex mcp add research-oyster -- "$(pwd)/research-oyster-mcp"
```

**Dry-run the whole demo once.** Then run the seed below so the demo starts with one finished job already in the database — this is your safety net if the venue Wi-Fi dies.

```sh
python - <<'EOF'
from research_engine.planner import build_plan
from research_engine.store import ResearchStore
from settings import Settings

store = ResearchStore(Settings().database_url)
brief = ("Research Kirkland Italian sparkling mineral water for a US Christmas 2026 "
         "campaign. Find consumer tensions, competitor activity, and three creative opportunities.")
plan = build_plan(brief, decision="Choose three campaign territories",
                  market="US", time_horizon="Christmas 2026")
job = store.create_job(brief, "Choose three campaign territories", "US", "Christmas 2026", plan)
store.add_evidence(job["job_id"], source_type="reddit",
    url="https://www.reddit.com/r/Costco/",
    title="Costco members on sparkling water value",
    excerpt="Members repeatedly compare Kirkland Italian sparkling water to San Pellegrino at a third of the price.",
    query="kirkland sparkling water")
print("Seeded job", job["job_id"])
EOF
```

## The demo

### Beat 1 — Control center (2 min)

```sh
python control_center.py     # opens http://127.0.0.1:8765/
```

Point at three things, then move on:

- **System check**: database connected, Discord/Press need no login, everything else clearly marked *optional*. Talking point: "No credential is required unless you actually select that source."
- The three big buttons (**Get fresh signals / Collect everything / Create weekly report**) — the non-engineer workflow.
- **Supervised browser capture** panel. Talking point: "For pages only a human can access — Discord, X — the extension queues excerpts and nothing is saved until the researcher clicks Approve. Oyster never sees cookies or passwords."

### Beat 2 — The MCP flow (8 min, the main event)

Switch to Claude Code (or Codex) and type, verbatim:

> Use Research Oyster to research Kirkland Italian sparkling mineral water for a US Christmas 2026 campaign. Find consumer tensions, competitor activity, and three creative opportunities. Check which connectors are ready first, collect what you can from RSS feeds and your own web search, save everything as evidence, and finish by showing me the dossier with coverage and gaps.

Narrate what the host does — this is the product:

1. **`create_research_job`** returns a structured plan: research questions, entities, query families, recommended sources, and clarifying questions. Talking point: "One sentence in, a research plan out — it recommends *web, RSS, X, Reddit* here and deliberately does **not** drag in Twitch, because this isn't a gaming brief."
2. **`connector_status`** shows exactly what's ready and, for anything unconfigured, the precise setup step or fallback. Nothing fails mysteriously.
3. **`fetch_rss`** and **`add_evidence`** store findings. Talking point: "Every row keeps the URL, excerpt, author, timestamp, the query that found it, and a content hash for dedup — evidence, not vibes."
4. **`get_research_dossier`** returns evidence grouped with a coverage count per source **and a `gaps` list** of recommended sources that produced nothing yet. Talking point: "It tells you what it *doesn't* know. Gaps are first-class."

Let the host write its cited synthesis from the dossier.

### Beat 3 — Resumability + provenance (3 min)

Kill the AI host session entirely. Start a fresh one and type:

> Use Research Oyster to list my recent research jobs, then show me the dossier for the Kirkland job.

Talking point: "The research outlives the chat. Tomorrow, another teammate picks up the same job — resuming the conversation and its stored synthesis — and fills the gaps." (Scheduled monitors that re-run a job automatically are on the roadmap, not built yet — don't demo them as live.) If anyone asks about trust, open `psql gaming_pulse` and run:

```sql
SELECT source_type, url, LEFT(excerpt, 60), query, collected_at
FROM research_evidence ORDER BY collected_at DESC LIMIT 5;
```

### Close (1 min)

- MCP is the product boundary: works from Claude Code, Codex, or any MCP host — same engine, no lock-in.
- Everything is local: your data, your Postgres, your credentials, optional per connector.
- Roadmap hook: `create_monitor` turns any one-off job into recurring collection.

## If things go wrong

| Failure | Fallback |
|---|---|
| Venue Wi-Fi dies | The seeded job from prep still demos Beats 2–3 fully: `list_research_jobs` → `get_research_dossier` works offline. Skip `fetch_rss`, have the host `add_evidence` from anything it already knows. |
| MCP server won't attach | Run the seed script's flow live in a terminal instead — same tools, called as Python. Less magic, same story. |
| Port 8765 busy | `lsof -ti :8765 | xargs kill`, restart `control_center.py`. |
| Postgres down | `pg_ctl status` / restart the service; the control center's Setup dialog shows a friendly error rather than crashing. |
| A connector errors live | Read the error out loud — they're intentionally human-readable, and `connector_status` explaining fallbacks *is* a feature. Pivot to it. |

## Questions to expect

- **"Does it scrape?"** Official APIs where they exist; the web crawler only fetches public pages the host names; browser capture is human-approved excerpt by excerpt. `SECURITY.md` and `DISCLAIMER.md` cover the posture.
- **"What does it cost?"** Oyster is free and local. Only optional connectors (X API, Apify) have third-party costs, and only if you configure them.
- **"Why not just ask the LLM?"** Show the `research_evidence` table: URL + timestamp + query per claim, deduped, resumable. A chat answer has none of that.
- **"What about sources needing login?"** That's the browser extension: supervised, one-time pairing code, approve-per-excerpt.
