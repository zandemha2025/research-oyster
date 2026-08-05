# Usage guide

## Write a strong brief

A useful request describes:

- **Subject:** the product, company, community, event, or question.
- **Decision:** what the findings should help decide.
- **Market and audience:** when relevant.
- **Time horizon:** current, historical, or future occasion.
- **Required sources:** only when a platform is genuinely required.
- **Exclusions:** data, people, communities, or sites not to use.
- **Deliverable:** campaign territories, market map, risk assessment, creator list, briefing, etc.

Only the subject and decision are required. Oyster infers safe defaults and should ask only when missing information materially changes the research strategy.

## Prompt templates

General market research:

> Use Research Oyster to study [subject] so I can decide [decision]. Focus on [market/audience] during [period]. Include [required sources] and exclude [exclusions]. Return [deliverable]: an executive answer, cited themes, tensions, and recommendations, with an honest confidence note.

Fast discovery:

> Use Research Oyster to answer [question] about [topic]. Find where the conversation is happening yourself, read the real threads, and give me the answer with cited quotes — not a list of sources you couldn't reach.

Platform-specific:

> Use Research Oyster to investigate [question]. The conversation is likely on Discord, Twitch, Kick, and X; go where it is public (Reddit, forums, coverage) and only note in one line anything genuinely private you couldn't reach. Use only public or explicitly authorized access.

Resume work:

> Use Research Oyster to list my recent research jobs, resume job [ID], inspect its dossier, gather more evidence where the answer is still thin, and re-write and re-export the synthesis.

## Recommended agent workflow

1. **Decompose.** Break the decision into sub-questions and entities and pass them to `create_research_job` as `questions`/`entities`/`angles`. You are the researcher — don't rely on defaults.
2. **Discover.** Call `discover_sources` and `search_web` to find where the topic is actually discussed. Do not collect from a platform merely because it is available.
3. **Read the substance.** Use `search_reddit` + `fetch_reddit_thread` for real comment text (free), `crawl_web_page` for articles and forums, and the platform connectors (`search_x`, `search_twitch`, `search_kick`, `read_discord_channel`, `run_apify_actor`) when their credentials exist. Save real quotes with `add_evidence`, noting stance/sentiment.
4. **Route around walls.** If a connector is `not_configured` or a venue is closed, use the free fallbacks. A private Discord or live stream chat is at most one sentence of limitations — never the answer.
5. **Iterate.** Follow leads and chase disagreements; use `get_research_dossier` to see what you have. Coverage is a progress signal, not a finish line.
6. **Synthesize.** Call `write_research_synthesis` with an executive answer, themes with cited quotes, tensions, sentiment, recommendations, confidence, and one short limitations note.
7. **Export.** Call `export_research_report` and give the user the folder path. It refuses to run until the synthesis exists — because a report answers the question.

## MCP tools

- `create_research_job`: persist a plan; accepts model-authored `questions`, `entities`, and `angles`.
- `search_web`: search the open web for ranked `{title, url, snippet}` leads (free provider by default; keyed for reliability).
- `discover_sources`: find where a topic is discussed, grouped by venue (reddit, forum, youtube, news, …).
- `search_reddit`: search Reddit's public JSON and store matching posts as evidence (free, no key).
- `fetch_reddit_thread`: read a Reddit thread's post and top comments and store them as evidence (free, no key).
- `add_evidence`: save evidence found by the AI host or another authorized tool.
- `fetch_rss`: store matching RSS/Atom entries.
- `search_x`: use official X recent search.
- `inspect_discord_invite`: save public invite metadata.
- `read_discord_channel`: read a channel accessible to the configured bot.
- `run_apify_actor`: run a user-selected Actor and normalize its results.
- `search_twitch`: search arbitrary Twitch topics and channels.
- `search_kick`: search current Kick streams.
- `crawl_web_page`: extract readable content and links from a public page.
- `connector_status`: report readiness and setup guidance without revealing secrets.
- `list_research_jobs`: find resumable jobs.
- `get_research_dossier`: return evidence, source coverage, and any stored synthesis.
- `write_research_synthesis`: author the report that answers the question (executive answer, themes with cited quotes, tensions, sentiment, recommendations, confidence, limitations).
- `export_research_report`: write the report and raw evidence for a job to a folder under `output/` (refuses until a synthesis exists).
- `get_browser_capture_mission`: derive browser terms and questions from the original brief.
- `request_browser_traffic_session`: ask the researcher to approve a domain-scoped traffic capture session.
- `get_browser_traffic_session`: poll whether a requested traffic session has been approved.

## Oyster answers the question — it doesn't dead-end

When a connector lacks credentials it does not raise a failure and stop. It returns `{"not_configured": true, ...}` with an ordered list of free `fallbacks` (`search_web`, `search_reddit`, `crawl_web_page`) and the optional `setup` step. A well-behaved host routes to where the topic is public rather than stopping. A research run ends with an **answer** and an honest confidence note — never a list of gaps handed back as the deliverable, and never a bare "I couldn't do that." Anything genuinely private or closed (a Discord you're not in, live stream chat) is one sentence of limitations, not a section.

## Exporting the report and raw data

Every job can leave chat as files. `export_research_report(job_id)` writes a folder `output/research-job-<id>-<slug>/` containing:

- `report.md` and `report.html` — the report, **led by the executive answer**: themes with cited quotes, tensions, sentiment, recommendations, and confidence & limitations. The evidence-by-source listing and coverage are demoted to an appendix. Export refuses to run until `write_research_synthesis` has authored the answer.
- `evidence.json` and `evidence.csv` — every stored evidence row, including metadata; the CSV opens directly in a spreadsheet.
- `raw_responses.jsonl` — the redacted network payloads captured during collection.

The control center's **Research jobs** panel exposes **Export report** and **Open folder** for the same result without an AI host.

## Approved-session browser traffic

For evidence that lives in a page's network responses rather than its visible text:

1. The host calls `request_browser_traffic_session(job_id, domain, reason)`. This only asks; nothing is captured yet.
2. The researcher approves the request once in the extension popup (plus Chrome's own domain-permission prompt).
3. While the approved session is live, the extension records the page's own JSON responses for that one domain, extracts message text into evidence, and stores the redacted payload as a `browser_network` raw response linked to the job.
4. The session is single-domain, size-capped, at most thirty minutes, and stoppable at any time. The server re-validates every capture against the approved session.

This is supervised and audited, but capturing platform traffic may breach that platform's terms of service — see `DISCLAIMER.md`.

To avoid approving every session, **trust a domain once** (extension settings, or "Always capture this domain" when approving). Future sessions on that domain then auto-approve and start recording on tabs you already have open — still time-limited, stoppable, revocable, and audited. It never runs unattended or drives the browser; capture happens only in your signed-in browser on pages you open.

## Supervised browser capture

The extension has two collection actions:

- **Selected text:** highlight one passage, right-click, and queue it for review.
- **Find visible candidates:** explicitly scan up to 25 currently rendered passages in the visible viewport and queue only passages matching the mission terms.

Neither action saves evidence immediately. The local review card shows the platform, page title, excerpt, and anonymization state. **Approve & save** sends that one candidate to the selected job; **Reject** and **Clear all** discard candidates locally. **Stop** blocks new selection and scan candidates until resumed.

For the Kirkland example, the active capture mission might ask the researcher to look for holiday hosting, table presentation, non-alcoholic celebration, packaging, price, and Costco value. An approved Discord excerpt is stored as `source_type="discord"` with supervised-capture metadata, so it appears in the Kirkland dossier and satisfies the plan's Discord coverage without disguising how it was collected.

## Browser control center

- **Get fresh signals:** collect every currently connected source for immediate use.
- **Collect everything:** add scheduled-style comparable observations.
- **Create weekly report:** render the current Monday-based reporting week.
- **Setup:** save the database address and optional connector credentials.

Closing the Terminal process stops the local control center. It does not delete saved research or credentials.

## Evidence and interpretation

An evidence row records its source type, URL, title, excerpt, author when available, publication and collection timestamps, query, and connector metadata. Evidence storage improves traceability but does not prove a source is truthful or representative. Review original context before relying on a claim.

Avoid using counts from different platforms as if they were directly comparable. Disclose sampling, API, pagination, deletion, private-community, demographic, and time-window limitations.
