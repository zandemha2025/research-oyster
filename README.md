# Research Oyster

Research Oyster is a local, source-agnostic research engine for Claude Code, Codex, and other MCP-compatible AI hosts. Give it an ordinary research brief; it turns the brief into questions and platform-specific searches, stores cited evidence in PostgreSQL, tracks coverage gaps, and returns a resumable dossier.

The repository also includes Gaming Culture Pulse, a browser control center and reporting workflow built on the same collection foundation.

The optional **Research Oyster Capture** browser extension adds supervised evidence capture for Discord, X, Twitch chat, Reddit, and other pages a researcher can already access. Candidates stay in the extension until the user explicitly approves them; approved excerpts enter the original research job and dossier.

## What a user gives Oyster

Only a subject and the decision the research should support are required:

> Research Kirkland Italian sparkling mineral water for a US Christmas 2026 campaign. Find consumer tensions, competitor activity, and three creative opportunities.

Audience, market, time period, required platforms, exclusions, and output format are optional. Oyster recommends relevant sources and creates source-specific queries. It does not force Twitch or Discord into research where they add no value.

## Fastest setup (one line)

On macOS or Linux, paste this into a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | bash
```

It downloads Oyster, installs Python and PostgreSQL if they are missing, creates and migrates a local database, attaches Oyster to Claude Code / Codex, and opens the control center. It is safe to re-run and never overwrites an existing `.env` or database. (macOS needs [Homebrew](https://brew.sh); the script tells you if it is missing.) Then restart your AI host and ask: “Use Research Oyster to research …”.

## Manual setup on macOS

1. Download or clone this repository.
2. Double-click `Install Gaming Pulse.command`.
3. Follow the Homebrew prompt if it appears, then run the installer again.
4. Wait for the local control center to open.
5. Click **Setup** and add only the source credentials you want.
6. Double-click `Attach Research Oyster.command`.
7. Restart Claude Code or Codex.
8. Ask your AI host: “Use Research Oyster to research …”

To add supervised browser capture, open `chrome://extensions` or `edge://extensions`, enable Developer mode, choose **Load unpacked**, and select the repository's `browser_extension` folder. In Oyster's control center, create a one-time pairing code and enter it in the extension settings.

The database is required. Every external connector is optional. The installer creates a local PostgreSQL database and isolated Python environment without replacing an existing `.env` file or database.

For the complete beginner walkthrough, credential instructions, manual Linux setup, verification, and troubleshooting, read [docs/SETUP.md](docs/SETUP.md).

## Using Research Oyster: step by step

### 1. Install it (once)

Run the one-line installer above. It sets up the database and attaches Oyster to Claude Code / Codex.

### 2. Restart your AI host

Quit and reopen Claude Code (or Codex) so it picks up the new tool. There is nothing else to "launch" — you talk to Oyster through your AI host in plain English.

### 3. Ask for research in plain English

Type a normal request. You don't list feeds or platforms — Oyster plans that itself. Concrete examples:

**Social sentiment across platforms**
> Use Research Oyster to research what people on Discord, Kick, and Twitch are saying about the new Spider-Man movie. Check which sources are ready first, use the ones that are, and export the report when you're done.

**Brand / campaign research**
> Use Research Oyster to study Kirkland Italian sparkling mineral water for a US Christmas 2026 campaign. Find consumer tensions, competitor activity, and three creative territories. Cite every claim and tell me what you couldn't cover.

**Market / competitive map**
> Use Research Oyster to map how US college students discuss affordable gaming laptops across Reddit, X, and tech press over the last six months. Give me a cited opportunity map and clearly flag unavailable sources.

**Resume earlier work**
> Use Research Oyster to list my recent research jobs, resume the Spider-Man one, fill the biggest gaps, and re-export the report.

### 4. What Oyster does with that

1. **Plans** the brief into research questions, entities, and per-source queries.
2. **Checks connectors** and uses the ones that are ready. If a source needs credentials you haven't added, it does **not** stop — it falls back to the next best source (Apify, public web, RSS) and tells you how to unlock the rest later.
3. **Collects and stores evidence** with full provenance: the URL, excerpt, author, timestamp, and the query that found it — deduplicated.
4. **Returns a dossier**: findings grouped by source, a coverage count, and the gaps that remain.
5. **Exports** a folder you can open and share.

### 5. Get the results as files

Ask the host to export (or click **Export report** in the dashboard). Each job produces a folder under `output/`:

- `report.md` and `report.html` — a readable, cited report
- `evidence.json` and `evidence.csv` — the raw evidence (CSV opens in any spreadsheet)
- `raw_responses.jsonl` — the redacted network payloads collected during the run

### 6. The dashboard (optional)

Reopen it any time with `Open Gaming Pulse.command` (macOS) or `python control_center.py`. It shows readiness, your research jobs with one-click **Export report** / **Open folder**, active capture sessions, and report shortcuts.

![Research Oyster control center](docs/images/control-center.png)

### 7. Sources behind a login, without API keys (optional)

For data on pages you're already signed into — a Discord server you're in, X, etc. — the **Research Oyster Capture** browser extension captures what those pages load, with your approval. The dashboard's **"Set up browser capture"** panel walks you through it: it shows the exact folder to load (Chrome/Edge → Extensions → Developer mode → Load unpacked), the pairing code, and whether a browser is connected. Then in the extension popup you pick a job and click **Start capturing this site** — that's the on-switch; your click is the approval and it records that one site for 30 minutes or until you press Stop. You can also trust a domain once so it starts hands-free. (Chrome can't auto-install an unpacked extension, so loading it once is manual; see [browser_extension/README.md](browser_extension/README.md).) See also [docs/USAGE.md](docs/USAGE.md).

## Source support

| Source | What Oyster can do | What you need |
|---|---|---|
| Web | Crawl a supplied public page and save readable evidence | Nothing |
| RSS/Atom | Match and store feed entries | Public feed URL |
| X | Search recent public posts through the official API | X bearer token |
| X fallback | Run a user-selected Apify Actor | Apify token and Actor access |
| Reddit and other sites | Run a user-selected Apify Actor | Apify token and Actor access |
| Discord public metadata | Inspect a public invite | Invite URL or code |
| Discord messages | Read channels where your bot is explicitly admitted | Bot token, server permission, required intent |
| Twitch | Search arbitrary channels/topics | Twitch app credentials |
| Kick | Search active streams/topics | Kick app credentials |
| Host-native search | Save evidence found by Claude, Codex, or another tool | Whatever access that host uses |
| Supervised browser capture | Review and approve visible excerpts into the active brief | Chrome/Edge extension and access to the page |
| Approved-session browser traffic | Capture the network payloads a page you approve already receives, one domain at a time | Chrome/Edge extension, page access, and an explicit per-domain approval |

Every research job can be exported to a folder under `output/` containing a readable report (Markdown and HTML), the raw evidence as `evidence.json` and `evidence.csv`, and the redacted network payloads collected during the run as `raw_responses.jsonl`. Ask the host to export, or use the **Research jobs** panel in the control center. When a connector is not configured, Oyster returns the next fallback to try rather than a dead error, so a research run finishes with the evidence it could gather plus clear instructions for unlocking the rest.

No credential is required unless the corresponding source is selected. API/provider charges are separate from Oyster.

See [docs/USAGE.md](docs/USAGE.md) for prompt templates, the individual MCP tools, the browser workflow, and command-line examples.

## Manual quick start

Requirements: Python 3.11+, PostgreSQL 15+, and Git.

```sh
git clone https://github.com/zandemha2025/research-oyster.git
cd research-oyster
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
createdb gaming_pulse
python main.py migrate
```

Attach using the absolute path returned by `pwd`:

```sh
codex mcp add research-oyster -- "$(pwd)/research-oyster-mcp"
claude mcp add --scope user research-oyster -- "$(pwd)/research-oyster-mcp"
```

Run the browser control center with:

```sh
./Open\ Gaming\ Pulse.command  # macOS
# or
.venv/bin/python control_center.py
```

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
.venv/bin/python tests/postgres_acceptance.py
.venv/bin/python tests/research_postgres_acceptance.py
```

The release was validated against a catalog of 99 user stories ([docs/FEATURE_STATUS.xlsx](docs/FEATURE_STATUS.xlsx)). The audit found and fixed several logistical, data-quality, and UX defects, then re-tested every behavior; continuous integration runs the full suite on every change.

## Important boundaries

Research Oyster is a collection and evidence-management tool, not a license to access restricted data. Use only public material or systems you are authorized to access. Follow platform terms, API terms, robots directives where applicable, privacy and employment rules, copyright law, and local consent requirements. Do not use it to bypass authentication, rate limits, access controls, bans, or technical protections. Do not collect sensitive personal data merely because it is technically visible.

Generated results can be incomplete, outdated, biased, or incorrect. Verify material claims and obtain professional advice before legal, medical, employment, financial, safety, or similarly consequential decisions. Read the full [DISCLAIMER.md](DISCLAIMER.md) before use and [SECURITY.md](SECURITY.md) before exposing or deploying the software.

## Security model

The included server uses local MCP stdio and the browser control center/capture API binds to `127.0.0.1:8765`. The extension pairs with a single-use code and a revocable token bound to its extension origin. Credentials are stored in the local `.env` file and are excluded from Git; the extension never receives them. This is not a production multi-user hosted service. Internet exposure requires authentication, tenant isolation, encrypted secret storage, rate limiting, audit logging, retention controls, and isolated collection workers.

## License

MIT. See [LICENSE](LICENSE). Third-party services, APIs, content, and Actors retain their own terms and licenses.
