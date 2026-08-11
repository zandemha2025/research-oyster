# Handoff — Research Oyster, Discord capture (and everything around it)

> Written for the next agent/engineer to pick up cold. Nothing summarized away. Repo:
> `zandemha2025/research-oyster`. Dev branch `claude/demo-prep-p0iazw`, kept in sync with `main`
> (main IS the current Studio build). Head at time of writing: `47f8fee`. User is on macOS
> ("Nazeems-MacBook-Pro"), install dir `~/research-oyster`.

---

## 0. TL;DR of the current state

- The user wants to capture **Discord message chatter** about a topic (test topic: *Marvel's
  Wolverine game*) and get **raw data**.
- The **"thinking" 90% works**: Oyster discovers the right Discord servers, picks search terms,
  renders a **clickable server list + one-click copy-chip search terms** in the Studio chat. The
  user has SEEN this working (chips rendered, terms grouped Core/Reveal/Timing/etc.).
- The **last mile is broken on the user's machine**: the browser extension ("Capture desk") shows
  a red **"Unavailable"** badge and an empty job dropdown, so no messages actually get captured.
- I found and fixed several real bugs (zombie ports, stale code loading, wrong pipeline routing,
  and the Origin-header rejection that most likely causes "Unavailable"). **The Origin fix
  (`47f8fee`) is pushed but UNVERIFIED against the user's actual Chrome** — I run in a cloud
  container and cannot see/drive their local browser, extension, or Discord login.
- The user is exhausted and frustrated. Be honest, minimize hoops, do not over-promise.

---

## 1. What Research Oyster is (orientation)

Local, source-agnostic research engine. You give it a brief; it discovers where a topic is
discussed, collects real posts/threads/comments, and writes a sourced report with computed numbers.
Free-first acquisition (see `docs/ACQUISITION-ENGINE.md`). Runs on the user's own Claude
subscription (no API key) via the Claude Agent SDK.

### The moving parts (this matters for the Discord bug)
| Piece | Port | Process | Purpose |
|---|---|---|---|
| **Studio** | 8770 | `.venv-studio/bin/python -m research_engine.studio` | The chat UI the user works in. Runs the agent loop. Serves the inline HTML page (`research_engine/studio_page.py`). |
| **Capture bridge** (a.k.a. Control Center) | 8765 | `.venv/bin/python control_center.py` | The ONLY thing the browser extension talks to. Serves `/api/capture/*`. Hosts the "Set up browser capture" pairing panel. |
| **MCP server** | 8771 (streamable-HTTP, in-process to Studio) | spawned by Studio | Exposes the research tools (incl. `plan_discord_capture`). Studio spawns its own; `_reap_orphan_mcp()` kills stale ones. |
| **Browser extension** | — | Chrome/Edge unpacked `browser_extension/` | Captures page data in the user's own logged-in browser. Pairs to the bridge (8765). |
| **PostgreSQL** | 5432 | local | DB `gaming_pulse`. Shared by all of the above. |

Two Python venvs: `.venv` (engine, MCP, control_center, export) and `.venv-studio` (Studio + Claude
Agent SDK). This split is real and load-bearing.

### Launch model (as of this session's fixes)
- `./studio` sets up venvs/db, signs into Claude if needed, **auto-starts the capture bridge in the
  background silently** (`OYSTER_NO_BROWSER=1 nohup .venv/bin/python control_center.py &`), then
  `exec`s Studio. So one command brings up Studio + bridge. Studio opens `localhost:8770`.
- Install one-liner (main now = Studio):
  `curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | bash`

---

## 2. The Discord goal + the design we converged on (READ THIS — it constrains everything)

**Hard constraints (not solvable by more engineering):**
1. **Discord has NO cross-server search.** You cannot query "all Wolverine messages across Discord."
   The data is not publicly indexable.
2. **Reading a server's messages requires an account that is a MEMBER of that server.**
3. **Automating a user account (typing/searching/scrolling for the user) = a "self-bot" = a
   bannable Discord ToS violation**, regardless of browser/tool. A fancier "agentic browser" does
   NOT change this — it's the automation of the account that Discord bans, not the automation tool.
4. The user explicitly rejected bot-farm tactics (mobile/residential proxy evasion) earlier. Stay
   on the defensible side.

**Therefore the agreed design (the only clean one) is a human-in-the-loop split:**
- **Oyster (automatic):** reason about which hub servers hold the conversation, discover them with
  member/online counts, pick the search terms, and ARM hands-free capture.
- **Human (a few clicks, ~30s):** click the servers to open them (they're a member / they join),
  paste the terms into Discord's own search bar, and scroll the results.
- **Extension (automatic):** captures the messages the page loads (from Discord's own internal
  search-results JSON) into the research job as raw data.

The human's click + search is exactly what keeps it "a person browsing their own servers," not a
self-bot. This is deliberate, not a limitation to remove.

---

## 3. What I built this session (Discord-relevant, newest first)

All committed to `claude/demo-prep-p0iazw` and fast-forwarded to `main`.

| Commit | What |
|---|---|
| `47f8fee` | **Capture bridge Origin fix (the "Unavailable" fix).** `control_center.py:extension_id_from_origin` no longer 403s requests that omit the `Origin` header (browsers omit it on GET). Still gated by loopback Host + bearer token. **Pushed, unverified on user's Chrome.** |
| `bf580a7` | **Studio routing fix.** `research_engine/studio.py:_is_capture_setup()` routes a Discord/browser capture-setup ask (or explicit `plan_discord_capture`) to a single conversational tool turn instead of the full plan→discover→…→export graph. Root cause of "it ran the whole pipeline and ignored me." |
| `c945800` | **Studio self-reap.** `studio.py:_reap_stale_studio()` kills a stale Studio holding port 8770 before binding, so a reinstall's new code actually loads (old process was serving old code). |
| `18d9c87` | **Checklist plan + copy chips + clickable links.** `discord_reach.format_capture_plan` → checklist form. `studio_page.py:chipify()` turns ``backtick`` terms into click-to-copy chips and `[text](url)` into clickable links on message completion. **Surface-tested in real headless Chromium** (chips copy to real clipboard, links clickable). |
| `b97fe8a` | **`plan_discord_capture` MCP tool** + `research_engine/discord_reach.py` (pure helpers, 7 unit tests). Composes `discord_landscape` (server discovery+counts) → top servers → agent-picked terms → arms a capture session for discord.com → formats the plan. |
| `a71f12f` | **Dead-simple capture.** `./studio` auto-starts the bridge; `control_center.py` self-reaps a stale bridge on port 8765 + honors `OYSTER_NO_BROWSER=1`; extension `popup.js` auto-selects the single job + clear empty-state + "Oyster isn't running" message. |

Supporting/earlier same session: acquisition-engine docs, evidence-layer doc, install→Studio fix,
merge to main, START-HERE.md.

### Key files to know
- `research_engine/discord_reach.py` — pure: `suggest_channels`, `search_terms`, `top_communities`,
  `format_capture_plan`. Unit tests: `tests/test_discord_reach.py` (7, green).
- `research_engine/mcp_server.py` — `plan_discord_capture` tool (~line 181), plus existing
  `discord_landscape`, `inspect_discord_invite`, `discord_widget`, `read_discord_channel`,
  `request_browser_traffic_session`, `get_browser_traffic_session`.
- `research_engine/connectors.py` — `discord_landscape` (`:345`, topic→ranked servers+counts, no
  token), `inspect_discord_invite` (`:251`), `discord_widget` (`:298`), `read_discord_channel`
  (`:715`, needs bot token).
- `research_engine/studio.py` — `_run_agent` (`:~395`, routing), `_is_capture_setup`,
  `_conversational_turn`, `_run_research_graph`, `_reap_stale_studio`, `main()`.
- `research_engine/studio_page.py` — inline UI. `chipify()` (just before `function onEvent`),
  `.copychip`/`.msglink` CSS, wired on `result`/`done` events.
- `control_center.py` — the capture bridge. `extension_id_from_origin` (`:57`, JUST PATCHED),
  `capture_client()` (`:360`), `do_OPTIONS` (`:372`), `main()` (`:633`, self-reap + NO_BROWSER),
  all `/api/capture/*` handlers.
- `research_engine/capture.py` — `CaptureStore`: `authenticate(token, origin="")` (`:154` — note it
  ALREADY skips the origin match when origin is empty), `request_session`, `mission`, `add_evidence`.
- `browser_extension/` — `popup.js` (`api()`, `loadJobs()`, `renderStatus()` → "Paired"/
  "Unavailable"/"Not paired"), `options.html` (pairing form: Local address + code + Pair browser),
  `content.js` (DOM adapters — Discord selector `li[id^='chat-messages'], [class*='messageListItem']`),
  `session_recorder.js` (wraps page fetch/XHR to capture internal JSON), `core.js`, `background.js`.
  Origin regex on the server: `^chrome-extension://([a-p]{32})$`.

---

## 4. THE DISCORD PART, in full — how it's supposed to work end to end

1. User (in Studio) says: *"Set up Discord capture for <topic>"* (or names `plan_discord_capture`).
2. `_is_capture_setup()` routes this to a **single conversational turn** (NOT the research graph).
3. The agent creates a research job, then calls **`plan_discord_capture(job_id, topic,
   search_terms=…)`**:
   - `discord_landscape(topic)` web-discovers Discord servers + enriches each via the invite API
     (`approximate_member_count` / `approximate_presence_count`) and the public widget. No token.
   - `top_communities()` ranks by online then members, keeps top 3.
   - The agent supplies rich `search_terms` (it knows entities/synonyms); `discord_reach.search_terms`
     is the fallback.
   - `CaptureStore.request_session(job_id, "discord.com", reason)` ARMS a browser-traffic capture
     session so the extension can record on discord.com once approved / trusted.
   - `format_capture_plan()` returns a markdown checklist: servers as `[name](invite)` links, terms
     as ``backtick`` chips, channel hints, "capture armed" note.
4. The agent prints that `plan` verbatim in chat.
5. Studio's `chipify()` (runs on the `result`/`done` SSE event) rewrites the finished message:
   `[name](url)` → clickable `<a>`; ``term`` → `<button class="copychip">` that copies the term to
   the clipboard on click.
6. **User does the manual half:** clicks a server link (opens Discord tab; must be logged in + a
   member), clicks a copy chip, pastes into Discord's in-app **search bar**, hits enter, scrolls.
7. **Extension captures:** with the discord.com capture session approved (or the domain trusted for
   hands-free), `session_recorder.js` records the internal JSON responses Discord loads for the
   search/scroll, and the messages land in the job as evidence (raw data).
8. User reviews/uses the raw data in Studio / export.

**Two capture modes exist in the extension popup ("Capture desk"):**
- **Supervised collection** → "Find visible candidates": scans messages currently on screen into a
  review queue; user clicks "Approve & save".
- **Network capture** (the better one for volume): captures the data the page loads as you scroll,
  for an approved/trusted domain — hands-free once approved. Standing consent ("trust discord.com
  once") auto-approves future sessions on tabs the user already has open (never headless, never
  navigates for the user).

---

## 5. What's NOT working (the troubles, in order they surfaced)

Each was a real bug; most are fixed. The LAST one is the current blocker and is unverified.

1. **Installer opened the OLD UI.** The plain one-liner installed `main`, which had no Studio and
   opened `control_center.py`. FIXED by merging the Studio build to `main` (`61976c0`), so the
   one-liner now installs+opens Studio.

2. **Zombie port 8765 ("port already in use").** A detached `control_center.py` from a prior run
   kept holding 8765; closing terminals didn't kill it; `kill` attempts didn't stick. The stale
   process ran OLD code and rejected the extension → "Unavailable". FIXED: `control_center.py`
   self-reaps its own stale instance on startup (`a71f12f`); `./studio` also force-clears+starts it.

3. **Reinstall didn't load new code.** The running **Studio** (port 8770) kept serving OLD code
   through reinstalls (same zombie pattern). Symptom: agent said *"there is no plan_discord_capture
   tool"* even after updating. FIXED: `studio.py:_reap_stale_studio()` (`c945800`). Also gave the
   user a hard-reset reload command (below).

4. **Studio ran the full research pipeline instead of the capture setup.** A fresh chat always ran
   the plan→discover→collect→…→export graph, so *"just set up Discord capture"* launched the whole
   machine (the Live-activity graph panel showed plan/discover/collect·reddit/collect·web/…). FIXED
   by `_is_capture_setup()` routing (`bf580a7`).

5. **CURRENT BLOCKER — extension "Unavailable" + banner "Open this request from the paired Oyster
   browser extension".** The bridge IS up and answering, but was rejecting the extension's requests.
   - Root cause I identified: `control_center.py:capture_client()` called
     `extension_id_from_origin(origin)`, which raised `PermissionError("Open this request from the
     paired Oyster browser extension.")` whenever the `Origin` header was absent. Browsers omit
     `Origin` on many GET requests the extension makes. So every job/status poll 403'd →
     `popup.js:renderStatus(false)` → red "Unavailable" + empty job dropdown.
   - FIX pushed (`47f8fee`): `extension_id_from_origin` now returns `""` for an absent Origin
     (allowed), still validates a PRESENT origin against `^chrome-extension://([a-p]{32})$`, and a
     website origin like `https://evil.com` is still rejected. `authenticate()` already skips the
     origin match when origin is empty, and the request is still gated by loopback Host + bearer
     token. Verified in a local unit check (empty allowed / valid parsed / evil rejected).
   - **UNVERIFIED against the user's real Chrome.** The user had not yet reloaded + retested when
     this doc was written. It may be fixed; it may not.

---

## 6. If "Unavailable" is STILL red after reload — hypotheses to check (in order)

I could not test these against the user's browser. Diagnose on the user's Mac:

**A. Is the bridge actually running and answering?**
```sh
curl -s -o /dev/null -w "bridge HTTP %{http_code}\n" http://127.0.0.1:8765/
tail -20 /tmp/research-oyster-capture.log
lsof -nP -iTCP:8765 -sTCP:LISTEN
```
If curl fails → the `./studio` auto-start of the bridge didn't run or crashed (check the log; check
`.venv/bin/python` exists and `control_center.py` imports cleanly with the DB up).

**B. Is the running bridge the NEW code (has the Origin fix)?**
```sh
grep -n "if not origin:" ~/research-oyster/control_center.py    # should be present near extension_id_from_origin
```
If absent → the reset/reload didn't land; re-run the reload command and confirm no stale Studio/bridge.

**C. Extension ID / pairing mismatch (the OTHER possible "Unavailable" cause).**
If the user ever **reloaded/re-added the unpacked extension**, Chrome may assign a **different
extension ID**, so the stored paired `extension_id` no longer matches the current origin. That would
throw in `authenticate()` → `"This browser origin is not paired with Oyster."` (different banner
text, but still red). Check/fix:
```sh
psql gaming_pulse -c "select id,name,extension_id,created_at,revoked_at from browser_clients order by created_at desc;"
```
Then **re-pair fresh**: in the bridge panel (`http://127.0.0.1:8765`) click "Create one-time pairing
code" → extension → "Pairing & settings" → paste → "Pair browser". Consider revoking stale
`browser_clients` rows.

**D. Confirm what the extension actually sends.** In Chrome: open the extension popup, right-click →
Inspect, Network tab, watch the `/api/capture/jobs` request — check its **Origin request header**
and the **response status/body**. This is the single most decisive diagnostic and I could not run
it remotely. If Origin is absent and status is now 200 → fixed. If 403 with the origin banner → the
fix didn't load. If 401/"invalid token" → re-pair. If "not paired with Oyster" → the ID-mismatch in
(C).

**E. Message capture itself (once connected).** Even with a green badge, the DOM/network capture of
Discord messages is the next fragile layer: Discord changes its DOM (the "Find visible candidates"
selector `li[id^='chat-messages'], [class*='messageListItem']` in `content.js` may drift), and the
"Network capture" path depends on `session_recorder.js` catching Discord's search-results JSON. Not
yet validated end-to-end with a real Discord login (I can't log into the user's Discord).

---

## 7. What DOES reliably work (use this for a demo)

- **The research pipeline** (fresh brief → plan→discover→collect→quantify→synthesize→review→verify→
  export): Reddit + YouTube + web + news, real chatter + computed numbers + sourced report. This is
  the star and needs none of the Discord/extension machinery. In the user's own runs it successfully
  pulled r/MarvelsWolverine, NeoGAF 11-page reaction threads, ResetEra threads, etc.
- **The Discord PLAN** (`plan_discord_capture`): discovers real servers with real counts (in the
  user's run: Insomniac Games server ~22.4k members; Marvel server ~113k), renders clickable server
  links + copy-chip terms. Confirmed rendering on the user's screen.
- **Discord LANDSCAPE** (`discord_landscape`, `inspect_discord_invite`, `discord_widget`): member/
  online counts, live voice channels — all free, no token, no membership.

The only thing NOT confirmed working end-to-end is the **live message capture** (extension → bridge
→ job), blocked on the "Unavailable" connection.

---

## 8. Exact commands the user has (for reference)

**Kill everything Oyster:**
```sh
kill -9 $(lsof -ti tcp:8770) $(lsof -ti tcp:8765) 2>/dev/null; pkill -9 -f research_engine.studio; pkill -9 -f control_center.py; pkill -9 -f research_engine.mcp_server; echo "oysters killed"
```

**Clean reload to latest code + start (the command they keep using):**
```sh
cd ~/research-oyster && kill -9 $(lsof -ti tcp:8770) $(lsof -ti tcp:8765) 2>/dev/null; git fetch origin main && git reset --hard origin/main && ./studio
```

**Trigger the Discord capture plan (in a NEW Studio chat):**
> Set up Discord capture for Marvel's Wolverine game — show me the server list and search terms.

**Pairing (first time / after ID change):** open `http://127.0.0.1:8765` → Create one-time pairing
code → extension icon → "Pairing & settings" → paste code (address `http://127.0.0.1:8765`) → Pair
browser.

---

## 9. Environment reality (why "just control my computer" isn't the answer)

- This agent runs in an **isolated cloud container** with only the repo — NOT the user's Mac. It
  cannot see or drive the user's Chrome, extension, Discord login, or local Studio/bridge. All fixes
  are code fixes verified in-container; the browser↔bridge handshake can only be verified on the
  user's machine.
- Even a computer-use/agentic-browser setup would NOT solve Discord message capture, because that
  requires the **user's own logged-in Discord account and server membership** — the one piece that
  cannot be delegated without becoming a bannable self-bot. The human-in-the-loop is by design.

---

## 10. Suggested next steps for the next agent

1. Get the user (or a screenshare/logs) to run the section-6 diagnostics — specifically **(D)**, the
   Network-tab look at `/api/capture/jobs`. That single observation resolves whether `47f8fee` fixed
   it or whether it's the extension-ID pairing mismatch (C).
2. If it's the ID mismatch: add a fresh re-pair step and consider auto-revoking stale
   `browser_clients`; optionally pin the extension ID via a `key` in `browser_extension/manifest.json`
   so the ID is stable across reloads (removes an entire failure class).
3. Once the badge is green: validate the **actual message capture** with a real Discord login on a
   server the user is in — confirm `session_recorder.js` catches the search-results JSON and rows
   land in the job. Update the `content.js` Discord selectors if the DOM has drifted.
4. Consider surfacing the bridge connection status inside **Studio** itself (not only the extension),
   so the user isn't juggling two windows to know if capture is live.
5. Do NOT pursue automating the Discord search/scroll (self-bot/ban). Keep the human-in-the-loop.
6. Be honest with the user and keep it low-hoop; they are burned out on this feature specifically.

---

## 11. Open questions / unknowns

- Did `47f8fee` actually clear "Unavailable" on the user's Chrome? **Unknown.**
- Is the user's paired `browser_clients.extension_id` still matching their current extension ID?
  **Unknown** (they reloaded the unpacked extension multiple times — plausible mismatch).
- Does the hands-free `session_recorder.js` capture Discord's search JSON in practice? **Never
  confirmed with a real login.**
- Was the bridge actually auto-started by `./studio` in their last run, or did it fail silently?
  **Unknown** — check `/tmp/research-oyster-capture.log`.
