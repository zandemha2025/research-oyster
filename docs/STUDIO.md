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

## Setup

```bash
# 1. main app must be set up first: .venv exists, DATABASE_URL set in .env, migrated
python main.py migrate

# 2. create the Studio venv
./setup-studio.sh

# 3. authenticate on your own Claude subscription (one time)
claude setup-token
export CLAUDE_CODE_OAUTH_TOKEN=...        # the token setup-token prints
# (or skip this if your `claude` CLI is already logged in — ambient login also works)

# 4. launch
./research-oyster-studio
# open http://127.0.0.1:8770/
```

Each person runs the Studio against **their own** login — the app only reads a token
from the environment and never sees whose it is. Nobody shares an account.

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

- **Thinking stream** is best-effort: the SDK exposes `ThinkingBlock`s and the UI
  renders them, but the `claude` CLI does not reliably surface thinking through the
  stream, so "watch it think" may show nothing for many turns. Live tool calls and raw
  results are the reliable transparency surface.
- **Auth** uses ambient CLI login or `CLAUDE_CODE_OAUTH_TOKEN`. Anthropic does not
  permit third-party apps to silently ride claude.ai login; `claude setup-token` is the
  supported explicit path.
- **Coverage** in the Studio reflects whatever sources are configured/reachable. This is
  Phase 1 (the transparency app). Later phases add the per-source run ledger and real
  Twitch/Discord/X coverage — see the project plan.
