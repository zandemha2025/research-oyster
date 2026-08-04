"""Local, click-first control center for Gaming Culture Pulse.

The server binds to localhost only. It has no remote access and introduces no
runtime dependency beyond Python's standard library and the pipeline itself.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from db.queries import connect, migrate
from research_engine.capture import CaptureStore
from research_engine.export import export_job, job_folder
from research_engine.store import ResearchStore
from settings import Settings


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
TOKEN = secrets.token_urlsafe(24)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
CAPTURE_PORT = 8765
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://([a-p]{32})$")
SCHEMA_LOCK = threading.Lock()
SCHEMA_READY = False
RATE_LOCK = threading.Lock()
RATE_EVENTS: dict[tuple[str, str], list[float]] = {}


def capture_store() -> CaptureStore:
    """Create the capture boundary lazily so Setup can still open before the database is ready."""
    global SCHEMA_READY
    settings = Settings()
    if not SCHEMA_READY:
        with SCHEMA_LOCK:
            if not SCHEMA_READY:
                with connect(settings.database_url) as conn:
                    migrate(conn, ROOT / "db" / "migrations")
                SCHEMA_READY = True
    return CaptureStore(settings.database_url)


def extension_id_from_origin(origin: str) -> str:
    match = EXTENSION_ORIGIN.fullmatch(origin.strip())
    if not match:
        raise PermissionError("Open this request from the paired Oyster browser extension.")
    return match.group(1)


def bearer_token(header: str) -> str:
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise PermissionError("Pair the browser extension with Oyster before capturing.")
    return token.strip()


def enforce_rate(bucket: str, key: str, limit: int, window_seconds: int = 60) -> None:
    """Bound local extension traffic and pairing attempts without retaining request content."""
    now = time.monotonic()
    with RATE_LOCK:
        events = [stamp for stamp in RATE_EVENTS.get((bucket, key), []) if now - stamp < window_seconds]
        if len(events) >= limit:
            raise PermissionError("Too many capture requests. Wait a minute and try again.")
        events.append(now)
        RATE_EVENTS[(bucket, key)] = events


def monday_for_today() -> str:
    today = datetime.now(timezone.utc).date()
    return (today.fromordinal(today.toordinal() - today.weekday())).isoformat()


def latest_output(pattern: str) -> Path | None:
    files = list(OUTPUT.glob(pattern)) if OUTPUT.exists() else []
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def open_path(path: Path) -> None:
    """Open a file or folder in the OS file browser, cross-platform."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)])
    else:
        raise ValueError(f"Opening files is not supported on this platform ({sys.platform}). The file is at {path}.")


def friendly_error(text: str) -> str:
    lowered = text.lower()
    if "database_url" in lowered or "connection" in lowered:
        return "The database is not connected. Open Setup and check the database address."
    if "twitch_client" in lowered:
        return "Twitch is not connected. Open Setup and add the Twitch Client ID and Client Secret."
    if "kick_client" in lowered:
        return "Kick is not connected. Open Setup and add the Kick Client ID and Client Secret."
    if "no such file" in lowered or "not found" in lowered:
        return "A required local program or file could not be found. Run the system check for details."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:500] if lines else "The action did not finish. Check the details and try again."


def health() -> dict:
    checks = []
    env_exists = (ROOT / ".env").exists()
    checks.append({"name": "Setup file", "ok": env_exists, "detail": "Saved" if env_exists else "Setup required"})
    try:
        settings = Settings()
    except Exception:
        settings = None
    db_ok = False
    db_detail = "Not configured"
    if settings:
        try:
            with connect(settings.database_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            db_ok, db_detail = True, "Connected"
        except Exception:
            db_detail = "Could not connect"
    checks.append({"name": "Database", "ok": db_ok, "detail": db_detail})
    checks.append({"name": "Discord", "ok": db_ok, "detail": "No login needed" if db_ok else "Waiting for database"})
    checks.append({"name": "Twitch", "ok": bool(settings and settings.twitch_client_id and settings.twitch_client_secret), "optional": True, "detail": "Credentials saved" if settings and settings.twitch_client_id and settings.twitch_client_secret else "Optional — not connected"})
    checks.append({"name": "Kick", "ok": bool(settings and settings.kick_client_id and settings.kick_client_secret), "optional": True, "detail": "Credentials saved" if settings and settings.kick_client_id and settings.kick_client_secret else "Optional — not connected"})
    checks.append({"name": "Apify", "ok": bool(settings and settings.apify_token), "optional": True, "detail": "Token saved" if settings and settings.apify_token else "Optional — not connected"})
    checks.append({"name": "X search", "ok": bool(settings and (settings.x_bearer_token or settings.apify_token)), "optional": True, "detail": "Official API ready" if settings and settings.x_bearer_token else "Apify fallback ready" if settings and settings.apify_token else "Optional — not connected"})
    checks.append({"name": "Discord messages", "ok": bool(settings and settings.discord_bot_token), "optional": True, "detail": "Bot token saved" if settings and settings.discord_bot_token else "Optional — not connected"})
    checks.append({"name": "Browser capture", "ok": db_ok, "detail": "Ready to pair" if db_ok else "Waiting for database"})
    checks.append({"name": "Press", "ok": db_ok, "detail": "No login needed" if db_ok else "Waiting for database"})
    weekly = latest_output("*-gaming-pulse.html")
    fresh = latest_output("*-fresh-signals.md")
    return {
        "ready": db_ok,
        "checks": checks,
        "available_sources": [
            "discord",
            *(["twitch"] if settings and settings.twitch_client_id and settings.twitch_client_secret else []),
            *(["kick"] if settings and settings.kick_client_id and settings.kick_client_secret else []),
            "press",
        ] if db_ok else [],
        "latest_weekly": weekly.name if weekly else None,
        "latest_fresh": fresh.name if fresh else None,
        "week_start": monday_for_today(),
    }


def public_settings() -> dict:
    """Return setup-safe values and credential presence without exposing secrets."""
    try:
        settings = Settings()
    except Exception:
        return {"database_url": "", "collection_hour_utc": 16, "configured": {}}
    return {
        "database_url": "",
        "collection_hour_utc": settings.collection_hour_utc,
        "configured": {
            "database": bool(settings.database_url),
            "twitch": bool(settings.twitch_client_id and settings.twitch_client_secret),
            "kick": bool(settings.kick_client_id and settings.kick_client_secret),
            "x": bool(settings.x_bearer_token),
            "apify": bool(settings.apify_token),
            "discord_bot": bool(settings.discord_bot_token),
        },
    }


def save_settings(values: dict) -> None:
    allowed = {
        "DATABASE_URL", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
        "KICK_CLIENT_ID", "KICK_CLIENT_SECRET", "X_BEARER_TOKEN", "APIFY_TOKEN",
        "DISCORD_BOT_TOKEN", "COLLECTION_HOUR_UTC",
    }
    existing: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in allowed:
                existing[key] = value
    lines = []
    for key in allowed:
        value = str(values.get(key, "")).replace("\n", "").replace("\r", "")
        # Blank credential fields mean "keep the saved secret", which prevents
        # reopening Setup from accidentally disconnecting an existing account.
        if not value and (key == "DATABASE_URL" or key.endswith(("_ID", "_SECRET", "_TOKEN"))):
            value = existing.get(key, "")
        lines.append(f"{key}={value}")
    temp = ROOT / ".env.tmp"
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def command_for(action: str, payload: dict) -> list[str]:
    base = [sys.executable, str(ROOT / "main.py")]
    if action == "setup":
        return base + ["migrate"]
    if action == "collect":
        return base + ["collect", "all"]
    if action == "pulse":
        sources = payload.get("sources") or ["discord", "twitch", "kick", "press"]
        return base + ["pulse", "--sources", ",".join(sources), "--press-hours", str(payload.get("press_hours", 48))]
    if action == "report":
        return base + ["report", payload.get("week_start") or monday_for_today()]
    raise ValueError("Unknown action")


def run_job(job_id: str, command: list[str]) -> None:
    started = time.time()
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800)
        combined = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "success" if proc.returncode == 0 else "failed",
                "message": "Finished successfully." if proc.returncode == 0 else friendly_error(combined),
                "details": combined[-4000:], "finished_at": time.time(),
                "elapsed": round(time.time() - started, 1),
            })
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id].update({"status": "failed", "message": friendly_error(str(exc)), "details": str(exc), "finished_at": time.time()})


def start_job(action: str, payload: dict) -> str:
    command = command_for(action, payload)
    job_id = secrets.token_hex(6)
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "action": action, "status": "running", "message": "Working. You can leave this window open.", "started_at": time.time()}
    threading.Thread(target=run_job, args=(job_id, command), daemon=True).start()
    return job_id


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Oyster</title><style>
:root{--ink:#18211d;--muted:#66716b;--paper:#f6f4ee;--card:#fff;--green:#26734d;--red:#a53b32;--line:#dedfd9;--accent:#173f35}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1050px;margin:auto;padding:46px 24px 80px}
header{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:30px}h1{font:700 clamp(34px,6vw,60px)/.95 Georgia,serif;letter-spacing:-2px;margin:0;max-width:620px}header p{color:var(--muted);max-width:330px;margin:0}
.status{padding:18px 20px;border-radius:14px;background:var(--card);border:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.ready{color:var(--green);font-weight:700}.notready{color:var(--red);font-weight:700}
.actions{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;margin:18px 0 28px}.action{min-height:150px;text-align:left;padding:22px;border:1px solid var(--line);border-radius:18px;background:var(--card);cursor:pointer;transition:.15s}.action:hover{transform:translateY(-2px);border-color:#9ba8a1}.action.primary{background:var(--accent);color:white}.action b{display:block;font-size:19px;margin-bottom:7px}.action span{color:var(--muted)}.action.primary span{color:#d9e5df}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}.panel h2{font-size:19px;margin:0 0 16px}.check{display:flex;justify-content:space-between;border-top:1px solid #eee;padding:10px 0}.check:first-of-type{border:0}.ok{color:var(--green)}.bad{color:var(--red)}
button,.button{font:inherit;font-weight:650;border:0;border-radius:10px;padding:11px 15px;background:#e8ece9;color:var(--ink);cursor:pointer;text-decoration:none;display:inline-block}button.dark{background:var(--accent);color:white}.links{display:flex;gap:10px;flex-wrap:wrap}.job{display:none;margin:18px 0;padding:18px;border-radius:14px;background:#fff9df;border:1px solid #eadb96}.job.show{display:block}.job.failed{background:#fff0ee;border-color:#e3aaa4}.job.success{background:#eaf6ed;border-color:#aad2b5}details{margin-top:12px;color:var(--muted)}pre{white-space:pre-wrap;font-size:12px;max-height:220px;overflow:auto}
.pairing{margin-top:18px}.paircode{display:none;margin:12px 0;padding:14px;border:1px dashed #81948a;border-radius:10px;background:#f4faf6}.paircode.show{display:block}.paircode code{display:block;font-size:20px;font-weight:750;letter-spacing:1px;overflow-wrap:anywhere}.muted{color:var(--muted)}
.modal{display:none;position:fixed;inset:0;background:#0008;padding:24px;overflow:auto}.modal.show{display:block}.dialog{max-width:700px;margin:4vh auto;background:white;border-radius:20px;padding:28px}.dialog h2{margin-top:0}.field{margin:15px 0}.field label{display:block;font-weight:650;margin-bottom:5px}.field small{color:var(--muted)}input{width:100%;font:inherit;padding:11px;border:1px solid #bfc5c1;border-radius:9px}.row{display:flex;gap:10px;justify-content:flex-end;margin-top:22px}
@media(max-width:760px){header{display:block}header p{margin-top:15px}.actions,.grid{grid-template-columns:1fr}.action{min-height:120px}}
</style></head><body><main class="wrap">
<header><h1>Research Oyster</h1><p>Plan open-ended research, collect traceable evidence, and turn connected signals into a cited dossier.</p></header>
<section class="status"><div><strong id="headline">Checking your system...</strong><div id="subline">This takes a moment.</div></div><button onclick="openSetup()">Setup</button></section>
<section id="job" class="job"><strong id="jobTitle"></strong><div id="jobMessage"></div><details><summary>Technical details</summary><pre id="jobDetails"></pre></details></section>
<section class="actions">
 <button class="action primary" onclick="run('pulse')"><b>Get fresh signals</b><span>Collect every connected source now. Best when research is needed today.</span></button>
 <button class="action" onclick="run('collect')"><b>Collect everything</b><span>Add today's scheduled-style snapshots to the database.</span></button>
 <button class="action" onclick="run('report')"><b>Create weekly report</b><span>Build this week's Markdown and HTML report.</span></button>
</section>
<section class="grid"><div class="panel"><h2>System check</h2><div id="checks"></div><button onclick="refresh()">Check again</button></div>
<div class="panel"><h2>Your reports</h2><p id="reportText">No reports found yet.</p><div class="links"><button id="weekly" onclick="openFile('weekly')" disabled>Open weekly report</button><button id="fresh" onclick="openFile('fresh')" disabled>Open fresh brief</button><button onclick="openFile('output')">Open output folder</button></div></div>
<div class="panel"><h2>Research jobs</h2><p class="muted">Export any research job to a folder with a readable report, the raw evidence (JSON + CSV), and the captured network payloads.</p><div id="researchJobs"><p class="muted">Loading…</p></div></div>
<div class="panel"><h2>Active capture sessions</h2><p class="muted">Approved browser traffic capture sessions. You can stop any session here; it also stops automatically when it expires.</p><div id="researchSessions"><p class="muted">None active.</p></div></div>
<div class="panel pairing"><h2>Supervised browser capture</h2><p>Pair the Oyster extension to save approved excerpts from pages you can already access. Oyster never receives browser cookies or passwords.</p><button id="pairButton" onclick="createPairingCode()">Create one-time pairing code</button><div id="pairCode" class="paircode" aria-live="polite"><span class="muted">Local service</span><code id="captureAddress">http://127.0.0.1:8765</code><span class="muted">Pairing code — expires in 10 minutes</span><code id="captureCode"></code></div></div>
<div class="panel"><h2>How capture feeds research</h2><p>Select an active brief in the extension. Oyster turns that brief into a capture mission, keeps candidates in review, and promotes only approved captures into the original dossier.</p><p class="muted">Best-effort name redaction is on by default; review every excerpt. Only capture material you are authorized to view and retain.</p></div></section>
</main>
<div id="modal" class="modal"><div class="dialog"><h2>Connections &amp; setup</h2><p>The database is required. Every research connector is optional: add Twitch, Kick, X, Apify, or an authorized Discord bot only when you need that source. Saved values stay in a private file on this computer.</p>
 <div class="field"><label>Database address</label><input id="db" placeholder="postgresql://user:password@localhost:5432/gaming_pulse"><small>Required. This tells the app where to save its history.</small></div>
 <div class="field"><label>Twitch Client ID</label><input id="tid"></div><div class="field"><label>Twitch Client Secret</label><input id="tsecret" type="password"></div>
 <div class="field"><label>Kick Client ID</label><input id="kid"></div><div class="field"><label>Kick Client Secret</label><input id="ksecret" type="password"></div>
 <div class="field"><label>X API Bearer Token</label><input id="xtoken" type="password"><small>Optional. Enables recent-post search in the Research Oyster MCP.</small></div>
 <div class="field"><label>Apify API Token</label><input id="apifytoken" type="password"><small>Recommended. Enables Reddit, X alternatives, web discovery, and any Apify Actor you select.</small></div>
 <div class="field"><label>Discord Bot Token</label><input id="discordtoken" type="password"><small>Optional. Reads messages only in servers where your bot has been installed and granted channel access.</small></div>
 <div class="field"><label>Daily collection hour in UTC</label><input id="hour" type="number" min="0" max="23" value="16"><small>Use the same hour every day for fair comparisons.</small></div>
 <div class="row"><button onclick="closeSetup()">Cancel</button><button class="dark" onclick="saveSetup()">Save and prepare database</button></div></div></div>
<script>
const token='__TOKEN__';let state={};
async function api(path,body){const opt=body?{method:'POST',headers:{'Content-Type':'application/json','X-Pulse-Token':token},body:JSON.stringify(body)}:{};const r=await fetch(path,opt);const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data}
async function refresh(){state=await api('/api/status');document.getElementById('headline').textContent=state.ready?'Your system is ready':'Setup needs attention';document.getElementById('headline').className=state.ready?'ready':'notready';document.getElementById('subline').textContent=state.ready?'You can collect fresh signals or create reports.':'Open Setup to finish connecting the database.';document.getElementById('checks').innerHTML=state.checks.map(c=>`<div class="check"><span>${c.name}</span><span class="${c.ok?'ok':c.optional?'muted':'bad'}">${c.ok?'✓':c.optional?'—':'○'} ${c.detail}</span></div>`).join('');document.getElementById('weekly').disabled=!state.latest_weekly;document.getElementById('fresh').disabled=!state.latest_fresh;document.getElementById('reportText').textContent=state.latest_weekly?`Latest weekly report: ${state.latest_weekly}`:'No weekly report has been created yet.';loadResearchJobs();loadResearchSessions()}
async function run(action){if(!state.ready&&action!=='setup'){openSetup();return}showJob(action==='pulse'?'Collecting fresh signals':action==='collect'?'Collecting today’s data':'Creating weekly report','Working. Keep this window open.','running');try{const payload={action,week_start:state.week_start};if(action==='pulse')payload.sources=state.available_sources;const x=await api('/api/run',payload);poll(x.job_id)}catch(e){showJob('Could not start',e.message,'failed')}}
async function poll(id){const x=await api('/api/job?id='+id);showJob(x.status==='running'?'Working...':x.status==='success'?'Done':'Needs attention',x.message,x.status,x.details);if(x.status==='running')setTimeout(()=>poll(id),1000);else refresh()}
function showJob(title,msg,status,details=''){const e=document.getElementById('job');e.className='job show '+status;document.getElementById('jobTitle').textContent=title;document.getElementById('jobMessage').textContent=msg;document.getElementById('jobDetails').textContent=details||'No additional details.'}
async function openSetup(){try{const saved=await api('/api/settings');db.value='';db.placeholder=saved.configured.database?'Saved — leave blank to keep':'postgresql://user:password@localhost:5432/research';hour.value=saved.collection_hour_utc??16;const labels={tid:'twitch',tsecret:'twitch',kid:'kick',ksecret:'kick',xtoken:'x',apifytoken:'apify',discordtoken:'discord_bot'};for(const [id,key] of Object.entries(labels)){document.getElementById(id).placeholder=saved.configured[key]?'Saved — leave blank to keep':'Not configured'}}catch(e){showJob('Could not load setup',e.message,'failed')}document.getElementById('modal').classList.add('show')}function closeSetup(){document.getElementById('modal').classList.remove('show')}
async function saveSetup(){const values={DATABASE_URL:db.value,TWITCH_CLIENT_ID:tid.value,TWITCH_CLIENT_SECRET:tsecret.value,KICK_CLIENT_ID:kid.value,KICK_CLIENT_SECRET:ksecret.value,X_BEARER_TOKEN:xtoken.value,APIFY_TOKEN:apifytoken.value,DISCORD_BOT_TOKEN:discordtoken.value,COLLECTION_HOUR_UTC:hour.value};try{await api('/api/settings',values);closeSetup();await refresh();run('setup')}catch(e){alert(e.message)}}
async function openFile(kind){await api('/api/open',{kind})}
async function loadResearchJobs(){try{const jobs=await api('/api/research/jobs');const el=document.getElementById('researchJobs');if(!jobs.length){el.innerHTML='<p class="muted">No research jobs yet. Ask your AI host to use Research Oyster.</p>';return}el.innerHTML=jobs.map(j=>`<div class="check"><span>#${j.id} ${(j.brief||'').slice(0,60)}<br><span class="muted">${j.status} · ${(j.created_at||'').slice(0,10)}</span></span><span class="links"><button onclick="exportResearch(${j.id})">Export report</button><button onclick="openResearch(${j.id})">Open folder</button></span></div>`).join('')}catch(e){document.getElementById('researchJobs').innerHTML=`<p class="bad">${e.message}</p>`}}
async function exportResearch(id){showJob('Exporting research report','Writing report and raw evidence to a folder…','running');try{const x=await api('/api/research/export',{job_id:id});showJob('Report exported',`${x.evidence_count} evidence items and ${x.raw_count} raw payloads written to ${x.folder}`,'success')}catch(e){showJob('Could not export',e.message,'failed')}}
async function openResearch(id){try{await api('/api/open',{kind:'research',job_id:id})}catch(e){showJob('Could not open folder',e.message,'failed')}}
async function loadResearchSessions(){try{const s=await api('/api/research/sessions');const el=document.getElementById('researchSessions');const open=s.filter(x=>x.status==='approved'||x.status==='requested');if(!open.length){el.innerHTML='<p class="muted">None active.</p>';return}el.innerHTML=open.map(x=>`<div class="check"><span>${escapeHtml(x.domain)}<br><span class="muted">job ${escapeHtml(x.job_id)} · ${escapeHtml(x.status)}${x.expires_at?' · ends '+escapeHtml(String(x.expires_at).slice(11,16)):''}</span></span><span class="links"><button onclick="stopResearchSession(${x.id})">Stop</button></span></div>`).join('')}catch(e){document.getElementById('researchSessions').innerHTML=`<p class="bad">${e.message}</p>`}}
async function stopResearchSession(id){try{await api('/api/research/sessions/'+id+'/stop',{});await loadResearchSessions()}catch(e){showJob('Could not stop session',e.message,'failed')}}
function escapeHtml(v){const d=document.createElement('div');d.textContent=String(v==null?'':v);return d.innerHTML}
async function createPairingCode(){if(!state.ready){openSetup();return}try{const x=await api('/api/capture/pairing-code',{});document.getElementById('captureCode').textContent=x.pairing_code;document.getElementById('pairCode').classList.add('show');document.getElementById('pairButton').textContent='Create a new code'}catch(e){showJob('Could not create pairing code',e.message,'failed')}}
refresh();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin", "")
        if EXTENSION_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, maximum: int = 262_144) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > maximum:
            raise ValueError("Capture request is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def capture_client(self) -> tuple[CaptureStore, dict]:
        expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host", "").lower() not in expected_hosts:
            raise PermissionError("Capture requests must use Oyster's exact local address.")
        origin = self.headers.get("Origin", "")
        extension_id_from_origin(origin)
        token = bearer_token(self.headers.get("Authorization", ""))
        enforce_rate("capture", hashlib.sha256(token.encode()).hexdigest(), 120)
        store = capture_store()
        client = store.authenticate(token, origin=origin)
        return store, client

    def do_OPTIONS(self) -> None:
        try:
            expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host", "").lower() not in expected_hosts:
                raise PermissionError("Preflight must use Oyster's exact local address.")
            origin = self.headers.get("Origin", "")
            extension_id_from_origin(origin)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()
        except PermissionError:
            self.send_json({"error": "This origin is not allowed."}, 403)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML.replace("__TOKEN__", TOKEN).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            self.send_json(health())
        elif parsed.path == "/api/settings":
            self.send_json(public_settings())
        elif parsed.path == "/api/job":
            job_id = parsed.query.removeprefix("id=")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            self.send_json(job or {"error": "Job not found"}, 200 if job else 404)
        elif parsed.path == "/api/research/jobs":
            try:
                self.send_json(ResearchStore(Settings().database_url).list_jobs(50))
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        elif parsed.path == "/api/research/sessions":
            try:
                self.send_json(capture_store().list_sessions())
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        elif parsed.path == "/api/capture/sessions":
            try:
                store, _client = self.capture_client()
                query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
                job_id = int(query["job_id"]) if query.get("job_id") else None
                self.send_json(store.list_sessions(job_id=job_id))
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        elif parsed.path == "/api/capture/jobs":
            try:
                _, _client = self.capture_client()
                self.send_json([job for job in ResearchStore(Settings().database_url).list_jobs(100) if job["status"] == "active"])
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        elif parsed.path.startswith("/api/capture/jobs/") and parsed.path.endswith("/mission"):
            try:
                store, _client = self.capture_client()
                job_id = int(parsed.path.split("/")[4])
                self.send_json(store.mission(job_id))
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        elif parsed.path == "/api/capture/pending":
            try:
                store, client = self.capture_client()
                query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
                job_id = int(query["job_id"]) if query.get("job_id") else None
                self.send_json(store.list_pending(job_id=job_id, client_id=client["client_id"]))
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/capture/pair":
            try:
                origin = self.headers.get("Origin", "")
                extension_id = extension_id_from_origin(origin)
                expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                if self.headers.get("Host", "").lower() not in expected_hosts:
                    raise PermissionError("Pairing must use Oyster's exact local address.")
                enforce_rate("pair", extension_id, 10)
                payload = self.read_json(16_384)
                result = capture_store().exchange_pairing_code(
                    str(payload.get("code", "")), client_name=str(payload.get("client_name", "Oyster browser extension")),
                    extension_id=extension_id,
                )
                self.send_json(result)
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
            except Exception as exc:
                self.send_json({"error": friendly_error(str(exc))}, 400)
            return
        is_capture_request = parsed.path.startswith("/api/capture/") and parsed.path != "/api/capture/pairing-code"
        if not is_capture_request and self.headers.get("X-Pulse-Token") != TOKEN:
            self.send_json({"error": "This control-center session has expired. Refresh the page."}, 403)
            return
        try:
            # Approved-session captures carry a network payload plus excerpt; allow more headroom.
            payload = self.read_json(524_288 if parsed.path == "/api/capture/approve" else 262_144)
            if parsed.path == "/api/settings":
                if not str(payload.get("DATABASE_URL", "")).strip():
                    try:
                        Settings()
                    except Exception as exc:
                        raise ValueError("The database address is required.") from exc
                hour = int(payload.get("COLLECTION_HOUR_UTC", 16))
                if not 0 <= hour <= 23:
                    raise ValueError("The collection hour must be between 0 and 23.")
                save_settings(payload)
                self.send_json({"ok": True})
            elif parsed.path == "/api/run":
                self.send_json({"job_id": start_job(payload.get("action", ""), payload)})
            elif parsed.path == "/api/research/export":
                job_id = int(payload.get("job_id", 0))
                if job_id <= 0:
                    raise ValueError("A research job id is required.")
                self.send_json(export_job(ResearchStore(Settings().database_url), job_id, Settings().output_dir))
            elif parsed.path.startswith("/api/research/sessions/") and parsed.path.endswith("/stop"):
                session_id = int(parsed.path.split("/")[4])
                self.send_json(capture_store().stop_session(session_id))
            elif parsed.path == "/api/open":
                kind = payload.get("kind")
                if kind == "research":
                    job_id = int(payload.get("job_id", 0))
                    store = ResearchStore(Settings().database_url)
                    target = job_folder(store, job_id, Settings().output_dir)
                    if not target.exists():  # export on first open; later opens reuse the folder
                        export_job(store, job_id, Settings().output_dir)
                elif kind == "output":
                    target = OUTPUT
                else:
                    target = latest_output("*-gaming-pulse.html" if kind == "weekly" else "*-fresh-signals.md")
                if not target:
                    raise ValueError("There is no report to open yet.")
                open_path(Path(target))
                self.send_json({"ok": True})
            elif parsed.path == "/api/capture/pairing-code":
                self.send_json(capture_store().create_pairing_code())
            elif parsed.path == "/api/capture/preview":
                store, _client = self.capture_client()
                mission = store.mission(int(payload.get("job_id", 0)))
                excerpt = str(payload.get("excerpt", "")).strip()
                if not excerpt or len(excerpt) > 20_000:
                    raise ValueError("Preview text must be between 1 and 20000 characters.")
                matched = [term for term in mission["look_for"] if str(term).lower() in excerpt.lower()][:12]
                self.send_json({"relevant": bool(matched), "matched_terms": matched, "mission": mission})
            elif parsed.path == "/api/capture/pending":
                store, client = self.capture_client()
                permitted = {"job_id", "source_type", "url", "excerpt", "page_title", "author", "anonymize", "research_question", "researcher_note", "capture_mode", "metadata", "captured_at", "client_capture_id"}
                values = {key: value for key, value in payload.items() if key in permitted}
                if "anonymized" in payload:
                    values["anonymize"] = payload["anonymized"] is not False
                self.send_json(store.submit(client["client_id"], **values), 201)
            elif parsed.path == "/api/capture/approve":
                store, client = self.capture_client()
                if payload.get("approved_by_user") is not True:
                    raise ValueError("Explicit user approval is required before saving evidence.")
                permitted = {"job_id", "source_type", "url", "excerpt", "page_title", "author", "anonymize", "research_question", "researcher_note", "capture_mode", "metadata", "captured_at", "client_capture_id"}
                values = {key: value for key, value in payload.items() if key in permitted}
                if "anonymized" in payload:
                    values["anonymize"] = payload["anonymized"] is not False
                client_capture_id = str(values.pop("client_capture_id", ""))
                self.send_json(store.approve_direct(client["client_id"], client_capture_id=client_capture_id, **values), 201)
            elif parsed.path.startswith("/api/capture/pending/") and parsed.path.endswith("/approve"):
                store, client = self.capture_client()
                if payload.get("approved_by_user") is not True:
                    raise ValueError("Explicit user approval is required before saving evidence.")
                capture_id = int(parsed.path.split("/")[4])
                self.send_json(store.review(capture_id, approve=True, client_id=client["client_id"]))
            elif parsed.path == "/api/capture/revoke":
                store, client = self.capture_client()
                store.revoke_client(client["client_id"])
                self.send_json({"ok": True})
            elif parsed.path.startswith("/api/capture/sessions/") and parsed.path.endswith("/approve"):
                store, client = self.capture_client()
                if payload.get("approved_by_user") is not True:
                    raise ValueError("Explicit user approval is required to start a capture session.")
                session_id = int(parsed.path.split("/")[4])
                ttl = int(payload.get("ttl_minutes", 30))
                self.send_json(store.approve_session(session_id, client["client_id"], ttl_minutes=ttl))
            elif parsed.path.startswith("/api/capture/sessions/") and parsed.path.endswith("/decline"):
                store, client = self.capture_client()
                session_id = int(parsed.path.split("/")[4])
                self.send_json(store.decline_session(session_id, client["client_id"]))
            elif parsed.path.startswith("/api/capture/sessions/") and parsed.path.endswith("/stop"):
                store, client = self.capture_client()
                session_id = int(parsed.path.split("/")[4])
                self.send_json(store.stop_session(session_id, client_id=client["client_id"]))
            else:
                self.send_json({"error": "Not found"}, 404)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except Exception as exc:
            self.send_json({"error": friendly_error(str(exc))}, 400)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            store, client = self.capture_client()
            if parsed.path == "/api/capture/pending":
                query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
                job_id = int(query["job_id"]) if query.get("job_id") else None
                if job_id is None:
                    raise ValueError("job_id is required when clearing pending captures.")
                self.send_json(store.reject_pending(job_id, client_id=client["client_id"]))
            elif parsed.path.startswith("/api/capture/pending/"):
                capture_id = int(parsed.path.split("/")[4])
                self.send_json(store.review(capture_id, approve=False, client_id=client["client_id"]))
            elif parsed.path.startswith("/api/capture/evidence/"):
                capture_id = int(parsed.path.split("/")[4])
                self.send_json(store.delete_capture(capture_id, client["client_id"]))
            else:
                self.send_json({"error": "Not found"}, 404)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except Exception as exc:
            self.send_json({"error": friendly_error(str(exc))}, 400)


def main() -> None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", CAPTURE_PORT), Handler)
    except OSError as exc:
        raise SystemExit(f"Research Oyster could not start because local port {CAPTURE_PORT} is already in use. Close the other local process and try again.") from exc
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Research Oyster is open at {url}")
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
