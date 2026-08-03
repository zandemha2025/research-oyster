# Research Oyster

Research Oyster is a local, source-agnostic research engine for Claude Code, Codex, and other MCP-compatible AI hosts. Give it an ordinary research brief; it turns the brief into questions and platform-specific searches, stores cited evidence in PostgreSQL, tracks coverage gaps, and returns a resumable dossier.

The repository also includes Gaming Culture Pulse, a browser control center and reporting workflow built on the same collection foundation.

## What a user gives Oyster

Only a subject and the decision the research should support are required:

> Research Kirkland Italian sparkling mineral water for a US Christmas 2026 campaign. Find consumer tensions, competitor activity, and three creative opportunities.

Audience, market, time period, required platforms, exclusions, and output format are optional. Oyster recommends relevant sources and creates source-specific queries. It does not force Twitch or Discord into research where they add no value.

## Fastest setup on macOS

1. Download or clone this repository.
2. Double-click `Install Gaming Pulse.command`.
3. Follow the Homebrew prompt if it appears, then run the installer again.
4. Wait for the local control center to open.
5. Click **Setup** and add only the source credentials you want.
6. Double-click `Attach Research Oyster.command`.
7. Restart Claude Code or Codex.
8. Ask your AI host: “Use Research Oyster to research …”

The database is required. Every external connector is optional. The installer creates a local PostgreSQL database and isolated Python environment without replacing an existing `.env` file or database.

For the complete beginner walkthrough, credential instructions, manual Linux setup, verification, and troubleshooting, read [docs/SETUP.md](docs/SETUP.md).

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

No credential is required unless the corresponding source is selected. API/provider charges are separate from Oyster.

## How to use it

In your MCP-compatible AI host:

> Use Research Oyster to compare how US college students discuss affordable gaming laptops across Reddit, X, Twitch, and technology press. Focus on purchase barriers from the last six months. Give me a cited opportunity map and clearly identify unavailable sources.

The host should create a job, inspect connector readiness, gather and store evidence, inspect the final dossier, and synthesize with direct citations. Jobs persist locally and can be resumed later.

See [docs/USAGE.md](docs/USAGE.md) for prompt templates, individual MCP tools, the browser workflow, and command-line examples.

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

The release was validated against 93 user stories. The local audit found and fixed nine logistical, security, data-quality, and UX defects, then passed all 93 post-fix behaviors.

## Important boundaries

Research Oyster is a collection and evidence-management tool, not a license to access restricted data. Use only public material or systems you are authorized to access. Follow platform terms, API terms, robots directives where applicable, privacy and employment rules, copyright law, and local consent requirements. Do not use it to bypass authentication, rate limits, access controls, bans, or technical protections. Do not collect sensitive personal data merely because it is technically visible.

Generated results can be incomplete, outdated, biased, or incorrect. Verify material claims and obtain professional advice before legal, medical, employment, financial, safety, or similarly consequential decisions. Read the full [DISCLAIMER.md](DISCLAIMER.md) before use and [SECURITY.md](SECURITY.md) before exposing or deploying the software.

## Security model

The included server uses local MCP stdio and the browser control center binds to `127.0.0.1`. Credentials are stored in the local `.env` file and are excluded from Git. This is not a production multi-user hosted service. Internet exposure requires authentication, tenant isolation, encrypted secret storage, rate limiting, audit logging, retention controls, and isolated collection workers.

## License

MIT. See [LICENSE](LICENSE). Third-party services, APIs, content, and Actors retain their own terms and licenses.
