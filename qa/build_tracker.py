"""Build the canonical QA feature tracker (single xlsx) for Research Oyster Studio.

Each row is a user story written from the perspective of what a HUMAN sees/touches, with the
expected behavior derived from the code, the test verdict, and (where relevant) the bug found and
its fix. Re-run to regenerate the sheet from STORIES (the source of truth lives here, in git).

Verdicts:
  PASS          — tested from the human surface, works.
  PASS (fixed)  — a real defect was found in testing and fixed; re-tested green.
  NOTE          — works but a known limitation / coherence issue worth flagging.
  DEFER         — real, deliberately deferred (v1.1/v2), with the reason in the notes.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# id, surface, user story (human POV), expected behavior (from code), verdict, result / notes
STORIES = [
    # --- Launch & setup ---
    ("L1", "Launch/Setup", "I run the one-line installer", "clones branch, installs Python+Postgres if missing, creates+migrates DB, attaches Oyster; idempotent", "PASS (fixed)", "Reviewed. Fixed the branch trap: header defaulted to main while docs use the Studio branch — added the OYSTER_BRANCH example to the header."),
    ("L2", "Launch/Setup", "I run ./studio to start the app", "starts Postgres, migrates, checks auth, launches on 8770, opens browser", "PASS", "Self-healing launcher verified by code + live run."),
    ("L3", "Launch/Setup", "I open before signing in to Claude", "app loads; health shows auth state; guidance to claude setup-token", "PASS", "auth pill shows ambient/token; ./studio runs claude auth login if a CLI exists."),
    ("L4", "Launch/Setup", "I launch when 8770 is already in use", "a clear message, not a stack trace", "PASS (fixed)", "Was an uvicorn traceback. Now pre-binds the port and prints a friendly 'already running' message + how to stop it. Re-tested live."),
    # --- Sidebar ---
    ("SB1", "Sidebar", "I load the page", "header shows auth + db pills", "PASS", "'auth: ambient · db: ok' rendered."),
    ("SB2", "Sidebar", "I click '+ New research'", "conversation created + active; input enables + focuses", "PASS", "Verified via Playwright: input enabled."),
    ("SB3", "Sidebar", "I switch conversations", "the chosen one activates; its stream/report reload; SSE reconnects", "PASS", "openConversation resets panes + reopens EventSource."),
    ("SB4", "Sidebar", "I click a finished report in Reports", "full-screen doc view opens the report; Back returns", "PASS", "25 reports listed; doc view iframe → /api/report/{id}; Back works."),
    ("SB5", "Sidebar", "I open '⚙ API keys'", "modal lists each key saved/not-set; Save persists; blank keeps", "PASS", "12 key fields shown; presence-only; verified."),
    # --- Chat ---
    ("C1", "Chat", "I type a brief and Send/Enter", "message shows; input disables during the run; agent starts", "PASS", "Validated on Meridian + Voltline live runs."),
    ("C2", "Chat", "I watch it think", "thinking streams live in a 'thinking' block", "PASS", "thinking_start/thinking events render token-by-token."),
    ("C3", "Chat", "I watch it answer", "assistant text streams live", "PASS", "text deltas stream."),
    ("C4", "Chat", "The turn finishes", "'turn complete · $cost' shows; input re-enables", "PASS", "result/done events verified."),
    ("C5", "Chat", "A turn hits an error", "the real error surfaces (never silent)", "PASS", "Verified when synthesis failed earlier — error event shown, not swallowed."),
    ("C6", "Chat", "I send while a run is in progress", "no hang/duplicate; defined behavior", "NOTE", "Today: blocked with a clear 409. Chat-while-working is the planned v1.1 upgrade."),
    # --- Live activity ---
    ("LA1", "Live activity", "I watch the Activity feed", "each tool call (→) + result (✓/✕); click expands raw JSON", "PASS", "Verified on live runs."),
    ("LA2", "Live activity", "I watch the Graph tab", "nodes render on start; update running/done/failed live with detail", "PASS", "Validated on Meridian + Voltline."),
    ("LA3", "Live activity", "A source fails mid-run", "the feed shows the real error (403/SSL), not a euphemism", "PASS", "source_runs + feed show real outcomes."),
    # --- Report & deliverables ---
    ("R1", "Report", "I open the Report tab after a run", "designed HTML renders inline; per-job dropdown", "PASS", "pov/glance/[n] markers present; iframe loads."),
    ("R2", "Report", "I click the download chips", "docx/deck/HTML/Sources/README/CSV each download", "PASS (fixed)", "All chips 200 w/ correct MIME. Fixed a race that duplicated chips (21→7)."),
    ("R3", "Report", "I open report.docx", "formatted Word: title, POV, at-a-glance, tables, themes, sources", "PASS", "Validated (Voltline)."),
    ("R4", "Report", "I open deck.pptx", "16:9 deck: title, POV, stat tiles, charts, recs, sources", "PASS", "Validated."),
    ("R5", "Report", "I open Sources-and-Citations.md", "numbered [n], each a deep link with its quote", "PASS", "19 deep-link entries on Voltline."),
    ("R6", "Report", "I get the charts + raw-data", "charts as PNG+SVG+CSV; raw-data per connector + evidence.json", "PASS (fixed)", "Files present. Fixed the 'Charts (folder)' chip: was a 400 text, now downloads a zip."),
    ("R7", "Report", "I read the report as an exec", "at-a-glance box, 1-2 sentence POV, action titles, plain voice, real numbers", "PASS", "Voltline validated: no jargon/'n=', scannable."),
    # --- Settings / endpoints / robustness ---
    ("E1", "Endpoint", "The page checks health", "/api/health returns db+auth, no secrets", "PASS", "Verified."),
    ("E2", "Endpoint", "I view my saved keys", "/api/settings GET returns presence only, never secrets", "PASS", "configured map only; no values."),
    ("E3", "Endpoint", "I save keys", "POST writes .env 0600; blank keeps; DB/auth token preserved", "PASS", "Verified by code + Step-1 tests."),
    ("E4", "Endpoint", "A file download is requested", "allow-listed; cannot traverse outside the job folder", "PASS", "Traversal → 404."),
    ("E5", "Endpoint", "I open a report for a missing/unsynthesized job", "graceful message, not a 500", "PASS (fixed)", "Missing job returned a 500. Now returns a graceful 404 (missing) / 409 (no synthesis yet). Re-tested."),
    ("E6", "Endpoint", "My browser reconnects to the stream", "/api/chat/stream?from= replays missed transcript then live-tails", "PASS", "Replay verified (dumped full transcript earlier)."),
    ("UX1", "Whole app", "Any page load", "no console errors", "PASS (fixed)", "Was a /favicon.ico 404 on every load. Added an inline data-URI favicon; console errors now 0."),
    # --- Peripheral surfaces (separate apps / legacy) ---
    ("P1", "Control Center", "I use the 8765 dashboard (Gaming Pulse + capture pairing)", "separate legacy app; runs the gaming pipeline + browser-capture pairing", "NOTE", "Distinct from the Studio (8770). Legacy 'two products in one repo' — works, but out of the Studio demo path."),
    ("P2", "Browser extension", "I capture from a page via the extension", "extension pairs with the Control Center (8765), not the Studio", "NOTE", "Demoted fallback path; server-side collection is primary. Real provenance bugs in the extension are v2 (audit #4)."),
    ("P3", "Settings split", "I set keys from the UI", "two settings UIs own disjoint .env keys", "NOTE", "Studio keys modal (12 research keys) vs Control Center Setup (DB+streaming). For the Studio, DATABASE_URL is set by the installer, so not a blocker."),
    ("P4", "CI", "The test suite runs in CI", "the postgres acceptance scripts are collected", "DEFER", "They're standalone __main__ scripts, not pytest — not collected. Real hygiene gap; wiring risks a flaky red step pre-demo. Post-demo fix."),
]

HEADERS = ["ID", "Surface", "User story (human POV)", "Expected behavior (from code)", "Verdict", "Result / found & fixed"]
WIDTHS = [6, 15, 40, 52, 13, 62]
COLORS = {"PASS": "1E7A34", "PASS (fixed)": "0F766E", "NOTE": "8A6D0B", "DEFER": "7A4A1E"}


def build(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Feature tracker"
    head_fill = PatternFill("solid", fgColor="1A1F2B")
    head_font = Font(bold=True, color="FFFFFF")
    for c, (h, w) in enumerate(zip(HEADERS, WIDTHS), start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = w
    for r, story in enumerate(STORIES, start=2):
        for c, val in enumerate(story, start=1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        verdict = story[4]
        vcell = ws.cell(row=r, column=5)
        vcell.font = Font(bold=True, color=COLORS.get(verdict, "000000"))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{len(STORIES)+1}"
    # Summary counts on a second sheet.
    s2 = wb.create_sheet("Summary")
    from collections import Counter
    counts = Counter(s[4] for s in STORIES)
    s2["A1"] = "Verdict"; s2["B1"] = "Count"
    s2["A1"].font = s2["B1"].font = Font(bold=True)
    for i, (k, v) in enumerate(sorted(counts.items()), start=2):
        s2.cell(row=i, column=1, value=k); s2.cell(row=i, column=2, value=v)
    s2["A" + str(len(counts) + 3)] = f"Total stories: {len(STORIES)}"
    s2.column_dimensions["A"].width = 16
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    print(f"wrote {path} with {len(STORIES)} stories | verdicts: {dict(counts)}")


if __name__ == "__main__":
    build(Path(__file__).resolve().parent / "Research-Oyster-QA.xlsx")
