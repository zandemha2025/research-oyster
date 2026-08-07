"""Research Oyster Studio — a local, transparent chat UI over the research agent.

You chat with an Oyster-powered agent and watch it work live: streaming text,
its thinking, every tool call, and every raw tool result — nothing hidden, nothing
spun. The generated report renders inline.

Architecture (see plan): this app drives the model with the claude-agent-sdk and
points it at the EXISTING Research Oyster MCP server, launched as a subprocess over
stdio. Because the SDK requires mcp<2.0 and the server requires mcp>=2.0, the two
live in separate virtualenvs — this app runs in .venv-studio, the MCP server in
.venv — and stdio isolates them. The agent inherits all ~22 research tools for free.

Auth: the SDK uses the ambient `claude` CLI credentials, or a per-user
CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) exported into the environment
before launch. No per-request API key; runs on the user's own Claude subscription.

Binds 127.0.0.1:8770 only. The existing dashboard (control_center.py) is untouched
on 8765.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import claude_agent_sdk as csdk
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from research_engine.export import export_job, job_folder
from research_engine.store import ResearchStore
from settings import Settings

# Repo root is the parent of research_engine/ — the MCP server subprocess and
# report export both resolve paths against it, and .env lives here.
ROOT = Path(__file__).resolve().parent.parent
MCP_PYTHON = ROOT / ".venv" / "bin" / "python"
STUDIO_PORT = 8770
# One shared MCP server (streamable-HTTP) for the whole Studio process — every graph node and
# conversation reuses it instead of spawning its own stdio subprocess. localhost is in NO_PROXY.
MCP_HTTP_HOST = "127.0.0.1"
MCP_HTTP_PORT = int(os.environ.get("OYSTER_MCP_HTTP_PORT", "8771"))
MCP_HTTP_URL = f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp"
TOKEN = secrets.token_urlsafe(24)

# The research persona. Mirrors the MCP server's own instructions (kept in sync by
# hand — we can't import mcp_server here, it needs mcp 2.0 which isn't in this venv).
SYSTEM_PROMPT = (
    "You are Research Oyster: a capable researcher, not a data collector. Your "
    "deliverable is a report that ANSWERS the user's question — an executive answer, "
    "themes backed by real quoted evidence, where opinion splits, overall sentiment, "
    "recommendations, and one honest note on confidence. Raw evidence is an appendix.\n\n"
    "Method: create_research_job (decompose the decision yourself); discover_sources / "
    "search_web to find WHERE the conversation happens; go read it (search_reddit + "
    "fetch_reddit_thread, crawl_web_page, platform connectors); capture real quotes via "
    "add_evidence; iterate until you can answer confidently; write_research_synthesis to "
    "author the answer; export_research_report.\n\n"
    "WHEN THE USER NAMES SPECIFIC PLATFORMS (e.g. 'Discord and Twitch'), those are the "
    "PRIORITY, not optional. Actually go get them first, with the right tools:\n"
    "- Twitch: read_twitch_chat needs NO credentials — never call Twitch 'not configured'. "
    "But Twitch chat is LIVE and per-channel: find channels currently streaming the topic "
    "and read their chat. If nothing relevant is live, say so plainly.\n"
    "- Discord: inspect_discord_invite for the community's public metadata; read_discord_channel "
    "reads real messages only if the Oyster bot is in that server. If it isn't, say that "
    "explicitly — it is a genuine access limit, not something to paper over.\n"
    "Structure the answer BY the platforms the user asked about, and report per-platform what "
    "you actually got and what you couldn't (and why). Reddit, web, and news are valuable "
    "SUPPLEMENTS — label them as additional context, never silently substitute them for the "
    "named platforms as if that were the answer.\n\n"
    "Be transparent: the user is watching every tool call and raw result live. Never describe "
    "a source that ran and returned nothing usable as 'not available' — say plainly it was "
    "attempted and returned no usable data. Route around walls to enrich the answer, but never "
    "let routing around a named platform hide that you didn't actually cover it."
)

# Tools the agent may use without prompting (this is a trusted local research context;
# there is no human at a permission prompt behind a web request). Research happens
# through the MCP tools + read-only web/search builtins. Editing/shell tools are denied.
# Research nodes use the MCP research tools for everything; these read-only builtins are the only
# extras. ToolSearch/Agent/Bash are deliberately NOT here — a node that goes looking for them
# wanders (it once burned a whole synthesize turn trying Bash + a subagent instead of authoring).
ALLOWED_BUILTINS = {"WebSearch", "WebFetch", "Read", "Glob", "Grep", "TodoWrite"}


def _db_ok() -> bool:
    try:
        from db.queries import connect

        with connect(Settings().database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
    except Exception:
        return False


# --- Settings / API keys (persisted to .env; the MCP server reads them via Settings()) -----
# Label per key shown in the panel. Order = display order; APIFY_TOKEN leads (the data engine).
SETTINGS_KEYS: dict[str, str] = {
    "APIFY_TOKEN": "Apify token — the universal data engine (Reddit, X, TikTok, IG, YouTube, Twitch, Kick)",
    "WEB_READER_API_KEY": "Web reader key — URL→markdown for JS-heavy pages (optional)",
    "X_BEARER_TOKEN": "X / Twitter API bearer token (optional)",
    "TWITCH_CLIENT_ID": "Twitch app Client ID (Helix API)",
    "TWITCH_CLIENT_SECRET": "Twitch app Client Secret (Helix API)",
    "KICK_CLIENT_ID": "Kick Client ID (optional)",
    "KICK_CLIENT_SECRET": "Kick Client Secret (optional)",
    "DISCORD_BOT_TOKEN": "Discord bot token — only servers where the bot is installed",
    "DISCORD_RESEARCH_TOKEN": "OPT-IN burner Discord user token — ToS-gray, off unless you enable it",
    "TAVILY_API_KEY": "Tavily web-search key (optional)",
    "BRAVE_API_KEY": "Brave web-search key (optional)",
    "SERPER_API_KEY": "Serper web-search key (optional)",
}


def _dotenv_path() -> Path:
    return ROOT / ".env"


def _read_dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    path = _dotenv_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                out[key.strip()] = value
    return out


def _save_settings(values: dict[str, Any]) -> None:
    """Persist allow-listed keys to .env. Never blanks a saved secret (empty = keep existing);
    preserves all other lines (DATABASE_URL, CLAUDE_CODE_OAUTH_TOKEN). 0600."""
    env = _read_dotenv()
    for key in SETTINGS_KEYS:
        if key not in values:
            continue
        val = str(values.get(key, "")).replace("\n", "").replace("\r", "").strip()
        if val:  # blank means "keep what's saved"
            env[key] = val
    tmp = _dotenv_path().with_suffix(".tmp")
    tmp.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(_dotenv_path())


def _public_settings() -> dict[str, Any]:
    env = _read_dotenv()
    return {"labels": SETTINGS_KEYS, "configured": {k: bool(env.get(k)) for k in SETTINGS_KEYS}}


async def _allow_research_tools(tool_name: str, tool_input: dict[str, Any], context: Any):
    """Auto-approve Oyster's research tools + safe read-only builtins; deny the rest."""
    if tool_name.startswith("mcp__research-oyster__") or tool_name in ALLOWED_BUILTINS:
        return csdk.PermissionResultAllow()
    return csdk.PermissionResultDeny(
        message=f"{tool_name} is not permitted in the research studio.", interrupt=False
    )


# --- Shared MCP server (the node-speed fix) --------------------------------------------------
# A full graph run drives ~14 focused agent nodes. If each one spawned its own stdio MCP server,
# every node would pay the Python-import + DB-connect startup (seconds each) — that's what made
# runs multi-minute. Instead we launch ONE long-lived streamable-HTTP MCP server for the whole
# Studio process and every node's ClaudeSDKClient connects to it over HTTP. The two mcp versions
# stay isolated because the server runs in .venv (mcp 2.0) as a separate process; the SDK only
# speaks the HTTP wire protocol to it.
_mcp_proc: subprocess.Popen | None = None


def _mcp_alive() -> bool:
    return _mcp_proc is not None and _mcp_proc.poll() is None


def _mcp_responding(timeout: float = 1.0) -> bool:
    # Any HTTP response (even 4xx for a bare GET) means the server is up and listening.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(MCP_HTTP_URL, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure_mcp_server() -> None:
    """Start the shared MCP server once and wait until it answers. Idempotent."""
    global _mcp_proc
    if _mcp_alive():
        return
    env = {**os.environ, "OYSTER_MCP_HTTP_PORT": str(MCP_HTTP_PORT), "OYSTER_MCP_HTTP_HOST": MCP_HTTP_HOST}
    _mcp_proc = subprocess.Popen(
        [str(MCP_PYTHON), "-m", "research_engine.mcp_server"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    atexit.register(_stop_mcp_server)
    deadline = time.time() + 40
    while time.time() < deadline:
        if _mcp_proc.poll() is not None:
            raise RuntimeError(f"MCP server exited during startup (code {_mcp_proc.returncode})")
        if _mcp_responding():
            return
        time.sleep(0.3)
    raise RuntimeError("MCP server did not become ready within 40s")


def _stop_mcp_server() -> None:
    global _mcp_proc
    if _mcp_proc and _mcp_proc.poll() is None:
        _mcp_proc.terminate()
        try:
            _mcp_proc.wait(timeout=5)
        except Exception:
            _mcp_proc.kill()
    _mcp_proc = None


def _reap_orphan_mcp() -> None:
    """A previous Studio killed with SIGTERM skips atexit, orphaning its MCP server on the shared
    port. The new Studio would then REUSE that orphan and run STALE code — old report templates, old
    connectors — even after a git pull. Reap any stray mcp_server at startup so we always spawn one
    on the CURRENT code. Linux /proc based; a no-op elsewhere (a fresh machine has no orphan anyway).
    """
    import glob
    import signal
    for cmdline in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            parts = open(cmdline, "rb").read().split(b"\0")
        except OSError:
            continue
        if any(b"research_engine.mcp_server" in p for p in parts):
            try:
                pid = int(cmdline.split("/")[2])
            except (ValueError, IndexError):
                continue
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass


def _agent_options(resume: str | None, system_prompt: str = SYSTEM_PROMPT) -> csdk.ClaudeAgentOptions:
    ensure_mcp_server()
    return csdk.ClaudeAgentOptions(
        cwd=str(ROOT),
        system_prompt=system_prompt,
        mcp_servers={
            "research-oyster": {
                "type": "http",
                "url": MCP_HTTP_URL,  # one shared server; see ensure_mcp_server above
            }
        },
        # Hard-remove the general-purpose tools from what the model can even see. Denying them via
        # can_use_tool wasn't enough — the model kept *trying* Bash/Agent/ToolSearch/Write to
        # "analyze the data" and burned whole turns getting denied instead of calling the research
        # tools + write_research_synthesis. Not advertising them forces the intended path.
        disallowed_tools=["Bash", "Task", "Agent", "ToolSearch", "Write", "Edit",
                          "NotebookEdit", "KillShell", "BashOutput"],
        can_use_tool=_allow_research_tools,
        permission_mode="default",  # bypassPermissions is blocked when running as root
        resume=resume,
        max_turns=80,
        max_thinking_tokens=6000,
        include_partial_messages=True,  # stream text + thinking deltas live (see _run_agent)
    )


# ---------------------------------------------------------------------------
# Conversations: one conversation == one SDK session (resume by session_id).
# In-memory for v1; SDK sessions themselves persist on disk, and research jobs
# persist in Postgres, so no research output is lost on a Studio restart.
# ---------------------------------------------------------------------------
class Conversation:
    def __init__(self, cid: str, title: str) -> None:
        self.id = cid
        self.title = title
        self.session_id: str | None = None
        self.busy = False
        self.job_ids: list[int] = []
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.transcript: list[dict[str, Any]] = []  # replay for reconnecting clients


CONVERSATIONS: dict[str, Conversation] = {}


def _emit(conv: Conversation, event: dict[str, Any]) -> None:
    conv.transcript.append(event)
    conv.queue.put_nowait(event)


async def _drive(client: "csdk.ClaudeSDKClient", emit, timeout: int = 180) -> dict[str, Any]:
    """Consume one agent response, emitting text/thinking/tool events via `emit`.

    Shared by both the conversational path and every graph node. Returns a summary:
    {produced, is_error, session_id, text, job_id, cost_usd}. A per-message inactivity
    timeout means a stalled step fails on its own instead of hanging forever.
    """
    tool_names: dict[str, str] = {}
    res: dict[str, Any] = {"produced": False, "is_error": False, "session_id": None,
                           "text": "", "job_id": None, "cost_usd": None}
    streamed_text = False
    responses = client.receive_response()
    while True:
        try:
            msg = await asyncio.wait_for(responses.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            res["is_error"] = True
            try:
                await client.interrupt()
            except Exception:
                pass
            break
        if isinstance(msg, csdk.StreamEvent):
            raw = getattr(msg, "event", None) or {}
            etype = raw.get("type")
            if etype == "content_block_start" and raw.get("content_block", {}).get("type") == "thinking":
                emit({"type": "thinking_start"})
            elif etype == "content_block_delta":
                delta = raw.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    res["produced"] = True
                    streamed_text = True
                    res["text"] += delta["text"]
                    emit({"type": "text", "text": delta["text"]})
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    res["produced"] = True
                    emit({"type": "thinking", "text": delta["thinking"]})
        elif isinstance(msg, csdk.AssistantMessage):
            if getattr(msg, "session_id", None):
                res["session_id"] = msg.session_id
            for block in msg.content:
                if isinstance(block, csdk.TextBlock) and block.text.strip() and not streamed_text:
                    res["produced"] = True
                    res["text"] += block.text
                    emit({"type": "text", "text": block.text})
                elif isinstance(block, csdk.ToolUseBlock):
                    res["produced"] = True
                    tool_names[block.id] = block.name
                    emit({"type": "tool_call", "name": block.name.replace("mcp__research-oyster__", ""),
                          "raw_name": block.name, "input": block.input})
            streamed_text = False
        elif isinstance(msg, csdk.UserMessage):
            content = msg.content if isinstance(msg.content, list) else []
            for block in content:
                if isinstance(block, csdk.ToolResultBlock):
                    res["produced"] = True
                    name = tool_names.get(block.tool_use_id, "")
                    text = _stringify_tool_result(block.content)
                    emit({"type": "tool_result", "name": name.replace("mcp__research-oyster__", ""),
                          "is_error": bool(getattr(block, "is_error", False)), "content": text[:8000]})
                    if name.endswith("create_research_job"):
                        try:
                            res["job_id"] = int(json.loads(text).get("job_id"))
                        except Exception:
                            pass
        elif isinstance(msg, csdk.ResultMessage):
            if getattr(msg, "session_id", None):
                res["session_id"] = msg.session_id
            if getattr(msg, "is_error", False):
                res["is_error"] = True
            res["cost_usd"] = getattr(msg, "total_cost_usd", None)
    return res


async def _run_agent(conv: Conversation, message: str) -> None:
    """Dispatch a user turn: a fresh research ask runs the full graph pipeline; a follow-up
    on an existing job runs a single conversational agent turn."""
    conv.busy = True
    try:
        if conv.job_ids:
            await _conversational_turn(conv, message)
        else:
            await _run_research_graph(conv, message)
    except Exception as exc:  # never leave the UI hanging
        _emit(conv, {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        conv.busy = False
        _emit(conv, {"type": "done", "session_id": conv.session_id})


async def _conversational_turn(conv: Conversation, message: str) -> None:
    """Follow-up chat / refinement on an existing conversation — one agent with full tools."""
    def emit(event: dict[str, Any]) -> None:
        _emit(conv, event)

    async with csdk.ClaudeSDKClient(options=_agent_options(resume=conv.session_id)) as client:
        await client.query(message)
        res = await _drive(client, emit, timeout=240)
    if res["session_id"]:
        conv.session_id = res["session_id"]
    if res["job_id"] and res["job_id"] not in conv.job_ids:
        conv.job_ids.append(res["job_id"])
        emit({"type": "job_linked", "job_id": res["job_id"]})
    if res["is_error"] or not res["produced"]:
        emit({"type": "error", "message": (
            "The agent finished without an answer. If this persists, make sure this machine is signed "
            "in to Claude (run `claude auth login` in Terminal), then try again.")})
    emit({"type": "result", "is_error": res["is_error"], "cost_usd": res["cost_usd"]})


async def _run_research_graph(conv: Conversation, message: str) -> None:
    """Run the graph-orchestrated research pipeline (research_engine/graph.py)."""
    from research_engine import graph

    store = ResearchStore(Settings().database_url)

    def emit(event: dict[str, Any]) -> None:
        _emit(conv, event)

    async def run_node(node_id: str, instruction: str, timeout: int) -> dict[str, Any]:
        emit({"type": "node", "id": node_id, "state": "running"})
        try:
            options = _agent_options(resume=None, system_prompt=graph.NODE_PREAMBLE)
            async with csdk.ClaudeSDKClient(options=options) as client:
                await client.query(instruction)
                res = await _drive(client, lambda e: emit({**e, "node": node_id}), timeout=timeout)
        except Exception as exc:
            emit({"type": "node", "id": node_id, "state": "failed", "detail": str(exc)[:200]})
            return {"produced": False, "is_error": True, "job_id": None, "session_id": None, "text": "", "cost_usd": None}
        if res["job_id"] and res["job_id"] not in conv.job_ids:
            conv.job_ids.append(res["job_id"])
            emit({"type": "job_linked", "job_id": res["job_id"]})
        emit({"type": "node", "id": node_id, "state": "failed" if res["is_error"] else "done"})
        return res

    def get_dossier(job_id: int) -> dict[str, Any]:
        try:
            return store.dossier(job_id)
        except Exception:
            return {"evidence": [], "source_runs": [], "synthesis": None}

    await graph.run_graph(message, emit, run_node, get_dossier)


def _stringify_tool_result(content: Any) -> str:
    """Tool results arrive as a list of content parts, a str, or a dict."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or json.dumps(part))
            else:
                parts.append(getattr(part, "text", None) or str(part))
        return "\n".join(parts)
    return json.dumps(content) if isinstance(content, dict) else str(content)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------
def _require_token(request: Request) -> bool:
    return request.headers.get("X-Studio-Token") == TOKEN


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(PAGE.replace("__TOKEN__", TOKEN))


async def health(request: Request) -> JSONResponse:
    has_token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    return JSONResponse({
        "db": _db_ok(),
        "auth": "token" if has_token else "ambient",
        "mcp_python": str(MCP_PYTHON),
        "mcp_python_exists": MCP_PYTHON.exists(),
    })


async def list_conversations(request: Request) -> JSONResponse:
    return JSONResponse([
        {"id": c.id, "title": c.title, "job_ids": c.job_ids, "busy": c.busy}
        for c in CONVERSATIONS.values()
    ])


async def _json_dict(request: Request) -> dict:
    """Parse a JSON request body to a dict, tolerating malformed or non-object bodies. A buggy
    client should get a clean default/404, never an unhandled 500."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def create_conversation(request: Request) -> JSONResponse:
    if not _require_token(request):
        return JSONResponse({"error": "expired session; refresh"}, status_code=403)
    body = await _json_dict(request)
    title = (body.get("title") or "New research").strip()[:120]
    cid = secrets.token_hex(8)
    CONVERSATIONS[cid] = Conversation(cid, title)
    return JSONResponse({"conversation_id": cid, "title": title})


async def send_message(request: Request) -> JSONResponse:
    if not _require_token(request):
        return JSONResponse({"error": "expired session; refresh"}, status_code=403)
    body = await _json_dict(request)
    cid = body.get("conversation_id")
    message = (body.get("message") or "").strip()
    conv = CONVERSATIONS.get(cid)
    if not conv:
        return JSONResponse({"error": "unknown conversation"}, status_code=404)
    if conv.busy:
        return JSONResponse({"error": "the agent is still working on the previous turn"}, status_code=409)
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    _emit(conv, {"type": "user", "text": message})
    asyncio.create_task(_run_agent(conv, message))
    return JSONResponse({"ok": True})


async def stream(request: Request) -> EventSourceResponse:
    cid = request.query_params.get("conversation_id")
    conv = CONVERSATIONS.get(cid)
    if not conv:
        return JSONResponse({"error": "unknown conversation"}, status_code=404)
    try:  # a bad/negative ?from= must not 500 the stream; clamp to a full replay
        replay = max(0, int(request.query_params.get("from", "0")))
    except (TypeError, ValueError):
        replay = 0

    async def gen():
        # Replay anything the client missed (reconnect / late open), then live-tail.
        for event in conv.transcript[replay:]:
            yield {"event": event["type"], "data": json.dumps(event)}
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(conv.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            yield {"event": event["type"], "data": json.dumps(event)}

    return EventSourceResponse(gen())


async def report(request: Request) -> HTMLResponse:
    job_id = int(request.path_params["job_id"])
    store = ResearchStore(Settings().database_url)
    try:
        folder = job_folder(store, job_id, Settings().output_dir)  # raises if the job doesn't exist
    except Exception:
        return HTMLResponse(
            f"<p style='font-family:sans-serif;padding:2rem'>No research job #{job_id} was found.</p>",
            status_code=404,
        )
    html_path = folder / "report.html"
    if not html_path.exists():
        try:
            export_job(store, job_id, Settings().output_dir)
        except Exception:
            # The report isn't ready yet (the run is still working, or it finished without a
            # synthesis). Show the human a calm placeholder — never a tool name or a stack trace.
            # 200, not an error code, so an open Report tab doesn't log a console error mid-run;
            # loadReport re-fetches with a cache-buster and swaps in the real report the moment
            # it lands.
            return HTMLResponse(_report_pending_page(), status_code=200)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


def _report_pending_page() -> str:
    """A quiet, on-brand 'your report is on its way' page shown while a run is still working."""
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;height:100vh;"
        "display:flex;align-items:center;justify-content:center;background:#0f1420;color:#e6e9ef\">"
        "<div style='text-align:center;max-width:34rem;padding:2rem'>"
        "<div style='font-size:2.5rem;margin-bottom:.5rem'>🦪</div>"
        "<h2 style='font-weight:600;margin:0 0 .5rem'>Your report is being prepared</h2>"
        "<p style='color:#9aa3b2;line-height:1.5;margin:0'>The research is still running. "
        "This page updates on its own — your report will appear here the moment it's ready. "
        "Watch the Activity and Graph tabs to follow along.</p>"
        "</div></div>"
    )


# Deliverable files the report pane offers for download, in the order shown. Anything not present
# for a given job is simply skipped. Names are matched against the job's export folder.
_DOWNLOADABLE: list[tuple[str, str]] = [
    ("report.docx", "Report (Word)"),
    ("deck.pptx", "Deck (PPTX)"),
    ("report.html", "Report (HTML)"),
    ("Sources-and-Citations.md", "Sources [n]"),
    ("README-start-here.md", "README"),
    ("evidence.csv", "Evidence (CSV)"),
]


def _job_folder(job_id: int) -> Path:
    store = ResearchStore(Settings().database_url)
    return job_folder(store, job_id, Settings().output_dir)


async def report_files(request: Request) -> JSONResponse:
    """List the deliverable files available for a job (for the report pane's download chips)."""
    job_id = int(request.path_params["job_id"])
    try:
        folder = _job_folder(job_id)
    except Exception as exc:
        return JSONResponse({"files": [], "error": str(exc)}, status_code=404)
    files = [{"name": name, "label": label}
             for name, label in _DOWNLOADABLE if (folder / name).exists()]
    if (folder / "charts").is_dir():
        files.append({"name": "charts", "label": "Charts (folder)"})
    return JSONResponse({"files": files})


async def report_file(request: Request) -> Any:
    """Serve one deliverable file for download. Name is validated against the allow-list so the
    endpoint can never traverse outside the job's export folder."""
    from starlette.responses import FileResponse

    job_id = int(request.path_params["job_id"])
    name = request.path_params["name"]
    allowed = {n for n, _ in _DOWNLOADABLE} | {"charts"}
    if name not in allowed:
        return PlainTextResponse("not a downloadable file", status_code=404)
    try:
        folder = _job_folder(job_id)
    except Exception:
        return PlainTextResponse("no such job", status_code=404)
    target = folder / name
    # The "charts" chip downloads the whole charts/ folder as a zip (a click should give the human
    # something, not a 400). Everything else is a single allow-listed file.
    if name == "charts":
        import io
        import zipfile
        from starlette.responses import Response

        charts_dir = folder / "charts"
        if not charts_dir.is_dir():
            return PlainTextResponse("no charts for this report", status_code=404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(charts_dir.iterdir()):
                if f.is_file():
                    zf.write(str(f), arcname=f"charts/{f.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="charts-{job_id}.zip"'})
    if target.is_dir() or not target.exists():
        return PlainTextResponse("file not found", status_code=404)
    return FileResponse(str(target), filename=name)


async def dossier(request: Request) -> JSONResponse:
    job_id = int(request.path_params["job_id"])
    try:
        data = ResearchStore(Settings().database_url).dossier(job_id)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


async def jobs(request: Request) -> JSONResponse:
    try:
        return JSONResponse(ResearchStore(Settings().database_url).list_jobs(50))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def get_settings(request: Request) -> JSONResponse:
    return JSONResponse(_public_settings())


async def post_settings(request: Request) -> JSONResponse:
    if not _require_token(request):
        return JSONResponse({"error": "expired session; refresh"}, status_code=403)
    body = await _json_dict(request)
    _save_settings(body)
    return JSONResponse({"ok": True, **_public_settings()})


routes = [
    Route("/", index),
    Route("/api/health", health),
    Route("/api/conversations", list_conversations, methods=["GET"]),
    Route("/api/conversations", create_conversation, methods=["POST"]),
    Route("/api/chat/send", send_message, methods=["POST"]),
    Route("/api/chat/stream", stream, methods=["GET"]),
    Route("/api/report/{job_id:int}", report, methods=["GET"]),
    Route("/api/report/{job_id:int}/files", report_files, methods=["GET"]),
    Route("/api/report/{job_id:int}/file/{name}", report_file, methods=["GET"]),
    Route("/api/dossier/{job_id:int}", dossier, methods=["GET"]),
    Route("/api/jobs", jobs, methods=["GET"]),
    Route("/api/settings", get_settings, methods=["GET"]),
    Route("/api/settings", post_settings, methods=["POST"]),
]

app = Starlette(routes=routes)


# The page markup lives in studio_page.py to keep this module focused on behavior.
from research_engine.studio_page import PAGE  # noqa: E402


def main() -> None:
    import threading
    import webbrowser

    import uvicorn

    import socket

    # Fail fast with a friendly message if the port is taken, instead of an uvicorn stack trace.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", STUDIO_PORT))
        probe.close()
    except OSError:
        probe.close()
        print(f"\n  Research Oyster Studio can't start: port {STUDIO_PORT} is already in use.\n"
              f"  It's probably already running — open http://127.0.0.1:{STUDIO_PORT}/ in your browser.\n"
              f"  (To stop the old one: pkill -f research_engine.studio, then run ./studio again.)\n")
        raise SystemExit(1)

    # We own the Studio port, so we're the single Studio instance — reap any orphaned MCP server
    # from a previously-killed Studio so this run's collection/export uses the current code.
    _reap_orphan_mcp()

    url = f"http://127.0.0.1:{STUDIO_PORT}/"
    print(f"Research Oyster Studio is open at {url}")
    if os.environ.get("OYSTER_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=STUDIO_PORT, log_level="warning")


if __name__ == "__main__":
    main()
