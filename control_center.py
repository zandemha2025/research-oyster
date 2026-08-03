"""Local, click-first control center for Gaming Culture Pulse.

The server binds to localhost only. It has no remote access and introduces no
runtime dependency beyond Python's standard library and the pipeline itself.
"""

from __future__ import annotations

import json
import os
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

from db.queries import connect
from settings import Settings


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
TOKEN = secrets.token_urlsafe(24)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def monday_for_today() -> str:
    today = datetime.now(timezone.utc).date()
    return (today.fromordinal(today.toordinal() - today.weekday())).isoformat()


def latest_output(pattern: str) -> Path | None:
    files = list(OUTPUT.glob(pattern)) if OUTPUT.exists() else []
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


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
<title>Gaming Culture Pulse</title><style>
:root{--ink:#18211d;--muted:#66716b;--paper:#f6f4ee;--card:#fff;--green:#26734d;--red:#a53b32;--line:#dedfd9;--accent:#173f35}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1050px;margin:auto;padding:46px 24px 80px}
header{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:30px}h1{font:700 clamp(34px,6vw,60px)/.95 Georgia,serif;letter-spacing:-2px;margin:0;max-width:620px}header p{color:var(--muted);max-width:330px;margin:0}
.status{padding:18px 20px;border-radius:14px;background:var(--card);border:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.ready{color:var(--green);font-weight:700}.notready{color:var(--red);font-weight:700}
.actions{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;margin:18px 0 28px}.action{min-height:150px;text-align:left;padding:22px;border:1px solid var(--line);border-radius:18px;background:var(--card);cursor:pointer;transition:.15s}.action:hover{transform:translateY(-2px);border-color:#9ba8a1}.action.primary{background:var(--accent);color:white}.action b{display:block;font-size:19px;margin-bottom:7px}.action span{color:var(--muted)}.action.primary span{color:#d9e5df}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}.panel h2{font-size:19px;margin:0 0 16px}.check{display:flex;justify-content:space-between;border-top:1px solid #eee;padding:10px 0}.check:first-of-type{border:0}.ok{color:var(--green)}.bad{color:var(--red)}
button,.button{font:inherit;font-weight:650;border:0;border-radius:10px;padding:11px 15px;background:#e8ece9;color:var(--ink);cursor:pointer;text-decoration:none;display:inline-block}button.dark{background:var(--accent);color:white}.links{display:flex;gap:10px;flex-wrap:wrap}.job{display:none;margin:18px 0;padding:18px;border-radius:14px;background:#fff9df;border:1px solid #eadb96}.job.show{display:block}.job.failed{background:#fff0ee;border-color:#e3aaa4}.job.success{background:#eaf6ed;border-color:#aad2b5}details{margin-top:12px;color:var(--muted)}pre{white-space:pre-wrap;font-size:12px;max-height:220px;overflow:auto}
.modal{display:none;position:fixed;inset:0;background:#0008;padding:24px;overflow:auto}.modal.show{display:block}.dialog{max-width:700px;margin:4vh auto;background:white;border-radius:20px;padding:28px}.dialog h2{margin-top:0}.field{margin:15px 0}.field label{display:block;font-weight:650;margin-bottom:5px}.field small{color:var(--muted)}input{width:100%;font:inherit;padding:11px;border:1px solid #bfc5c1;border-radius:9px}.row{display:flex;gap:10px;justify-content:flex-end;margin-top:22px}
@media(max-width:760px){header{display:block}header p{margin-top:15px}.actions,.grid{grid-template-columns:1fr}.action{min-height:120px}}
</style></head><body><main class="wrap">
<header><h1>Gaming Culture Pulse</h1><p>Collect reliable gaming signals now or let the weekly schedule build the bigger picture.</p></header>
<section class="status"><div><strong id="headline">Checking your system...</strong><div id="subline">This takes a moment.</div></div><button onclick="openSetup()">Setup</button></section>
<section id="job" class="job"><strong id="jobTitle"></strong><div id="jobMessage"></div><details><summary>Technical details</summary><pre id="jobDetails"></pre></details></section>
<section class="actions">
 <button class="action primary" onclick="run('pulse')"><b>Get fresh signals</b><span>Collect every connected source now. Best when research is needed today.</span></button>
 <button class="action" onclick="run('collect')"><b>Collect everything</b><span>Add today's scheduled-style snapshots to the database.</span></button>
 <button class="action" onclick="run('report')"><b>Create weekly report</b><span>Build this week's Markdown and HTML report.</span></button>
</section>
<section class="grid"><div class="panel"><h2>System check</h2><div id="checks"></div><button onclick="refresh()">Check again</button></div>
<div class="panel"><h2>Your reports</h2><p id="reportText">No reports found yet.</p><div class="links"><button id="weekly" onclick="openFile('weekly')" disabled>Open weekly report</button><button id="fresh" onclick="openFile('fresh')" disabled>Open fresh brief</button><button onclick="openFile('output')">Open output folder</button></div></div></section>
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
async function refresh(){state=await api('/api/status');document.getElementById('headline').textContent=state.ready?'Your system is ready':'Setup needs attention';document.getElementById('headline').className=state.ready?'ready':'notready';document.getElementById('subline').textContent=state.ready?'You can collect fresh signals or create reports.':'Open Setup to finish connecting the database.';document.getElementById('checks').innerHTML=state.checks.map(c=>`<div class="check"><span>${c.name}</span><span class="${c.ok?'ok':c.optional?'muted':'bad'}">${c.ok?'✓':c.optional?'—':'○'} ${c.detail}</span></div>`).join('');document.getElementById('weekly').disabled=!state.latest_weekly;document.getElementById('fresh').disabled=!state.latest_fresh;document.getElementById('reportText').textContent=state.latest_weekly?`Latest weekly report: ${state.latest_weekly}`:'No weekly report has been created yet.'}
async function run(action){if(!state.ready&&action!=='setup'){openSetup();return}showJob(action==='pulse'?'Collecting fresh signals':action==='collect'?'Collecting today’s data':'Creating weekly report','Working. Keep this window open.','running');try{const payload={action,week_start:state.week_start};if(action==='pulse')payload.sources=state.available_sources;const x=await api('/api/run',payload);poll(x.job_id)}catch(e){showJob('Could not start',e.message,'failed')}}
async function poll(id){const x=await api('/api/job?id='+id);showJob(x.status==='running'?'Working...':x.status==='success'?'Done':'Needs attention',x.message,x.status,x.details);if(x.status==='running')setTimeout(()=>poll(id),1000);else refresh()}
function showJob(title,msg,status,details=''){const e=document.getElementById('job');e.className='job show '+status;document.getElementById('jobTitle').textContent=title;document.getElementById('jobMessage').textContent=msg;document.getElementById('jobDetails').textContent=details||'No additional details.'}
async function openSetup(){try{const saved=await api('/api/settings');db.value='';db.placeholder=saved.configured.database?'Saved — leave blank to keep':'postgresql://user:password@localhost:5432/research';hour.value=saved.collection_hour_utc??16;const labels={tid:'twitch',tsecret:'twitch',kid:'kick',ksecret:'kick',xtoken:'x',apifytoken:'apify',discordtoken:'discord_bot'};for(const [id,key] of Object.entries(labels)){document.getElementById(id).placeholder=saved.configured[key]?'Saved — leave blank to keep':'Not configured'}}catch(e){showJob('Could not load setup',e.message,'failed')}document.getElementById('modal').classList.add('show')}function closeSetup(){document.getElementById('modal').classList.remove('show')}
async function saveSetup(){const values={DATABASE_URL:db.value,TWITCH_CLIENT_ID:tid.value,TWITCH_CLIENT_SECRET:tsecret.value,KICK_CLIENT_ID:kid.value,KICK_CLIENT_SECRET:ksecret.value,X_BEARER_TOKEN:xtoken.value,APIFY_TOKEN:apifytoken.value,DISCORD_BOT_TOKEN:discordtoken.value,COLLECTION_HOUR_UTC:hour.value};try{await api('/api/settings',values);closeSetup();await refresh();run('setup')}catch(e){alert(e.message)}}
async function openFile(kind){await api('/api/open',{kind})}
refresh();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        if self.headers.get("X-Pulse-Token") != TOKEN:
            self.send_json({"error": "This control-center session has expired. Refresh the page."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/settings":
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
            elif self.path == "/api/run":
                self.send_json({"job_id": start_job(payload.get("action", ""), payload)})
            elif self.path == "/api/open":
                kind = payload.get("kind")
                target = OUTPUT if kind == "output" else latest_output("*-gaming-pulse.html" if kind == "weekly" else "*-fresh-signals.md")
                if not target:
                    raise ValueError("There is no report to open yet.")
                subprocess.Popen(["open", str(target)])
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_json({"error": friendly_error(str(exc))}, 400)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Gaming Culture Pulse is open at {url}")
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
