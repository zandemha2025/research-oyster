# Research Oyster Studio — live, transparent research chat

The Studio is a local web app where you chat with an Oyster-powered agent and **watch
it work in real time**: every tool call, every raw tool result, and the streaming
answer — with the generated report embedded inline. It exists to make the research
trustworthy: you see exactly what ran, what came back, and what failed, so a source
that errored can never be quietly reported as "not available."

It runs on **your own Claude subscription** — no per-request API key.

```
┌────────────┐   HTTP + SSE    ┌──────────────────┐   claude-agent-sdk   ┌───────────┐
│  browser   │ ◀────────────▶ │  studio.py       │ ◀─────────────────▶ │ claude CLI│
│  chat UI   │  live events    │ (Starlette, 8770)│                      │  (model)  │
└────────────┘                 └────────┬─────────┘                      └───────────┘
                                        │ launches as a subprocess (stdio)
                                        ▼
                               ┌──────────────────────┐
                               │ research_engine.      │  the EXISTING MCP server,
                               │ mcp_server (in .venv) │  all ~22 research tools
                               └──────────────────────┘
```

## Why two virtualenvs

`claude-agent-sdk` requires `mcp < 2.0`; the MCP server (`research_engine/mcp_server.py`)
requires `mcp >= 2.0`. They cannot share one environment. So:

- **`.venv`** — the existing app + MCP server (mcp 2.0). Untouched.
- **`.venv-studio`** — the Studio + claude-agent-sdk (mcp 1.29).

The Studio launches the MCP server as a **subprocess over stdio** (`.venv/bin/python -m
research_engine.mcp_server`), so the two `mcp` versions never collide, and the agent
inherits all existing research tools with zero new tool code.

## Setup — two commands, total

**First time on a machine** (installs Python + PostgreSQL, creates the database, sets up
both envs):

```bash
curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | OYSTER_BRANCH=claude/demo-prep-p0iazw bash
```

**Every time you want it** (from the install directory, e.g. `~/research-oyster`):

```bash
./studio
```

That's it. `./studio` is idempotent and self-healing: it sets up anything missing,
starts the database, runs migrations, checks you're signed in to Claude, launches the
app, and opens `http://127.0.0.1:8770/` in your browser. Run it every time — it only
does the missing steps.

**Signing in to Claude** (once): the Studio runs on your own subscription. If your
`claude` CLI is already logged in, nothing to do. Otherwise run `claude setup-token`
once and either `export CLAUDE_CODE_OAUTH_TOKEN=...` or paste that line into `.env`.
Each person runs against **their own** login — the app only reads a token from the
environment and never sees whose it is. Nobody shares an account.

## Using it

- **New research** starts a conversation. Type a question in plain English.
- The **Live activity** panel (right) streams each tool call and its raw result as the
  agent works. Click any entry to expand the full JSON. Failed sources show their real
  error (e.g. `HTTP 403`, an SSL error) — never a euphemism.
- When the agent creates a research job, it's **linked** to the conversation; switch to
  the **Report** tab to read the exported dossier inline.
- **Continue the conversation** with follow-ups — the session resumes.
- Start as many conversations as you like; each is its own session.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /` | the chat UI |
| `GET /api/health` | db + auth status |
| `GET/POST /api/conversations` | list / create conversations |
| `POST /api/chat/send` | send a user turn |
| `GET /api/chat/stream?conversation_id=…` | SSE: `text`, `thinking`, `tool_call`, `tool_result`, `job_linked`, `result`, `done`, `error` |
| `GET /api/report/{job_id}` | the exported report HTML (rendered inline) |
| `GET /api/dossier/{job_id}` | raw evidence for a job |
| `GET /api/jobs` | recent research jobs |

Binds `127.0.0.1:8770` only. The existing dashboard (`control_center.py`) is untouched
on `8765`.

## Notes / limits (honest)

- **Thinking stream.** Text and thinking are streamed as SDK `StreamEvent` deltas
  (`include_partial_messages`), and the UI shows a "thinking…" block whenever the model
  starts reasoning. Whether the reasoning *text* appears depends on the endpoint: on the
  subscription/CLI path tested here, the model emits a thinking *start* signal but
  **redacts the thinking content deltas**, so you see the indicator, not the words. If an
  endpoint exposes thinking deltas, the same wiring streams them verbatim. Live tool calls
  and raw results are the always-reliable transparency surface.
- **Network / proxy.** All collection honors `HTTPS_PROXY` + the trusted CA bundle:
  `crawl_web_page` fetches over httpx (proxy-aware) and `read_twitch_chat` uses Twitch's
  WebSocket endpoint (`wss://…:443`) with proxy CONNECT — both work behind an agent or
  corporate egress proxy. (Raw IRC :6667 does not traverse such proxies, which is why chat
  uses WSS.) Sites that block datacenter IPs (some anonymous Reddit/DuckDuckGo requests)
  can still return 403/empty; that is recorded honestly in the source-run ledger, and a
  configured search key (Tavily/Brave/Serper) makes web discovery reliable.
- **Auth** uses ambient CLI login or `CLAUDE_CODE_OAUTH_TOKEN`. Anthropic does not
  permit third-party apps to silently ride claude.ai login; `claude setup-token` is the
  supported explicit path.
