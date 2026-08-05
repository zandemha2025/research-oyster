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
import json
import os
import secrets
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
    "fetch_reddit_thread, crawl_web_page, platform connectors when configured); capture "
    "real quotes via add_evidence; iterate until you can answer confidently; "
    "write_research_synthesis to author the answer; export_research_report.\n\n"
    "Be transparent: the user is watching every tool call and raw result live. Never "
    "describe a source that ran and returned nothing usable as 'not available' — say "
    "plainly that it was attempted and returned no usable data. 'I can't reach X' is "
    "never a stopping point when the topic is public elsewhere — route around it."
)

# Tools the agent may use without prompting (this is a trusted local research context;
# there is no human at a permission prompt behind a web request). Research happens
# through the MCP tools + read-only web/search builtins. Editing/shell tools are denied.
ALLOWED_BUILTINS = {"WebSearch", "WebFetch", "ToolSearch", "Read", "Glob", "Grep", "TodoWrite"}


def _db_ok() -> bool:
    try:
        from db.queries import connect

        with connect(Settings().database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
    except Exception:
        return False


async def _allow_research_tools(tool_name: str, tool_input: dict[str, Any], context: Any):
    """Auto-approve Oyster's research tools + safe read-only builtins; deny the rest."""
    if tool_name.startswith("mcp__research-oyster__") or tool_name in ALLOWED_BUILTINS:
        return csdk.PermissionResultAllow()
    return csdk.PermissionResultDeny(
        message=f"{tool_name} is not permitted in the research studio.", interrupt=False
    )


def _agent_options(resume: str | None) -> csdk.ClaudeAgentOptions:
    return csdk.ClaudeAgentOptions(
        cwd=str(ROOT),
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "research-oyster": {
                "type": "stdio",
                "command": str(MCP_PYTHON),
                "args": ["-m", "research_engine.mcp_server"],
                "env": {**os.environ},  # MCP server needs DATABASE_URL et al.
            }
        },
        can_use_tool=_allow_research_tools,
        permission_mode="default",  # bypassPermissions is blocked when running as root
        resume=resume,
        max_turns=80,
        max_thinking_tokens=6000,  # surface the agent's reasoning as ThinkingBlocks
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


async def _run_agent(conv: Conversation, message: str) -> None:
    """Drive one user turn and stream every event into the conversation queue.

    Uses the persistent ClaudeSDKClient (not the one-shot query() helper): the
    can_use_tool callback runs over a control channel that stays open only while the
    client connection is alive, so a fresh client is held open for the whole turn.
    """
    conv.busy = True
    tool_names: dict[str, str] = {}  # tool_use_id -> tool name, to interpret results
    try:
        options = _agent_options(resume=conv.session_id)
        async with csdk.ClaudeSDKClient(options=options) as client:
            await client.query(message)
            async for msg in client.receive_response():
                if isinstance(msg, csdk.AssistantMessage):
                    if getattr(msg, "session_id", None):
                        conv.session_id = msg.session_id
                    for block in msg.content:
                        if isinstance(block, csdk.TextBlock) and block.text.strip():
                            _emit(conv, {"type": "text", "text": block.text})
                        elif isinstance(block, csdk.ThinkingBlock) and block.thinking.strip():
                            _emit(conv, {"type": "thinking", "text": block.thinking})
                        elif isinstance(block, csdk.ToolUseBlock):
                            tool_names[block.id] = block.name
                            _emit(conv, {
                                "type": "tool_call",
                                "name": block.name.replace("mcp__research-oyster__", ""),
                                "raw_name": block.name,
                                "input": block.input,
                            })
                elif isinstance(msg, csdk.UserMessage):
                    content = msg.content if isinstance(msg.content, list) else []
                    for block in content:
                        if isinstance(block, csdk.ToolResultBlock):
                            name = tool_names.get(block.tool_use_id, "")
                            text = _stringify_tool_result(block.content)
                            _emit(conv, {
                                "type": "tool_result",
                                "name": name.replace("mcp__research-oyster__", ""),
                                "is_error": bool(getattr(block, "is_error", False)),
                                "content": text[:8000],
                            })
                            _maybe_link_job(conv, name, text)
                elif isinstance(msg, csdk.ResultMessage):
                    if getattr(msg, "session_id", None):
                        conv.session_id = msg.session_id
                    _emit(conv, {
                        "type": "result",
                        "is_error": bool(getattr(msg, "is_error", False)),
                        "cost_usd": getattr(msg, "total_cost_usd", None),
                    })
    except Exception as exc:  # never leave the UI hanging
        _emit(conv, {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        conv.busy = False
        _emit(conv, {"type": "done", "session_id": conv.session_id})


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


def _maybe_link_job(conv: Conversation, tool_name: str, result_text: str) -> None:
    """When the agent creates a research job, link it so the UI can show its report."""
    if not tool_name.endswith("create_research_job"):
        return
    try:
        data = json.loads(result_text)
        job_id = int(data.get("job_id"))
    except Exception:
        return
    if job_id not in conv.job_ids:
        conv.job_ids.append(job_id)
        _emit(conv, {"type": "job_linked", "job_id": job_id})


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


async def create_conversation(request: Request) -> JSONResponse:
    if not _require_token(request):
        return JSONResponse({"error": "expired session; refresh"}, status_code=403)
    body = await request.json()
    title = (body.get("title") or "New research").strip()[:120]
    cid = secrets.token_hex(8)
    CONVERSATIONS[cid] = Conversation(cid, title)
    return JSONResponse({"conversation_id": cid, "title": title})


async def send_message(request: Request) -> JSONResponse:
    if not _require_token(request):
        return JSONResponse({"error": "expired session; refresh"}, status_code=403)
    body = await request.json()
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
    replay = int(request.query_params.get("from", "0"))

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
    folder = job_folder(store, job_id, Settings().output_dir)
    html_path = folder / "report.html"
    if not html_path.exists():
        try:
            export_job(store, job_id, Settings().output_dir)
        except Exception as exc:
            return HTMLResponse(
                f"<p style='font-family:sans-serif;padding:2rem'>No report yet for job "
                f"#{job_id}. The agent must call write_research_synthesis first.<br>"
                f"<small>{exc}</small></p>",
                status_code=409,
            )
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


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


routes = [
    Route("/", index),
    Route("/api/health", health),
    Route("/api/conversations", list_conversations, methods=["GET"]),
    Route("/api/conversations", create_conversation, methods=["POST"]),
    Route("/api/chat/send", send_message, methods=["POST"]),
    Route("/api/chat/stream", stream, methods=["GET"]),
    Route("/api/report/{job_id:int}", report, methods=["GET"]),
    Route("/api/dossier/{job_id:int}", dossier, methods=["GET"]),
    Route("/api/jobs", jobs, methods=["GET"]),
]

app = Starlette(routes=routes)


# The page markup lives in studio_page.py to keep this module focused on behavior.
from research_engine.studio_page import PAGE  # noqa: E402


def main() -> None:
    import uvicorn

    print(f"Research Oyster Studio is open at http://127.0.0.1:{STUDIO_PORT}/")
    uvicorn.run(app, host="127.0.0.1", port=STUDIO_PORT, log_level="warning")


if __name__ == "__main__":
    main()
