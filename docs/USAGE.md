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

> Use Research Oyster to study [subject] so I can decide [decision]. Focus on [market/audience] during [period]. Include [required sources] and exclude [exclusions]. Return [deliverable] with direct citations, counterevidence, and remaining gaps.

Fast discovery:

> Use Research Oyster to find the strongest current signals around [topic]. Choose the relevant sources yourself. Tell me which sources were unavailable and do not present inference as evidence.

Platform-specific:

> Use Research Oyster to investigate [question] specifically on Discord, Twitch, Kick, and X. Before collecting, show which integrations are ready. Use only public or explicitly authorized access and explain any coverage limits.

Resume work:

> Use Research Oyster to list my recent research jobs, resume job [ID], inspect its dossier, fill the most important evidence gaps, and update the cited synthesis.

## Recommended agent workflow

1. Call `create_research_job` with the user's brief.
2. Review clarifications, questions, recommended sources, and source-specific queries.
3. Call `connector_status`.
4. Use the strongest relevant ready sources. Do not collect from a platform merely because it is available.
5. Save material host-native findings with `add_evidence`.
6. Use Oyster connectors for RSS, X, Discord, Twitch, Kick, Apify, and public web pages as appropriate.
7. For useful sources available only in the researcher's browser, call `get_browser_capture_mission`; the researcher selects the same job/question in the extension and approves relevant excerpts.
8. Seek independent confirmation and counterevidence.
9. Call `get_research_dossier` and address or disclose important gaps.
10. Separate observations, inferences, and recommendations; cite direct evidence URLs.

## MCP tools

- `create_research_job`: plan and persist an arbitrary assignment.
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
- `get_research_dossier`: return evidence, source coverage, and gaps.
- `get_browser_capture_mission`: derive browser terms and questions from the original brief.

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
