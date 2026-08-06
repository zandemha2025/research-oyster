"""Assemble the non-document parts of the deliverable package.

- Sources-and-Citations.md — the numbered [n] ledger. Prefers the model-authored
  synthesis.numbered_sources; falls back to building one from the source_runs ledger + raw
  responses so every run still ships a traceable ledger.
- README-start-here.md — orientation: what each file is, read-me-first.
- raw-data/ — the raw pulls split per connector, so a skeptic can audit the source rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _raw_url(raw_rows: list[dict[str, Any]], connector: str) -> str:
    # raw_responses rows key the connector as `collector` and carry the request_url.
    for r in raw_rows:
        if r.get("collector") == connector and r.get("request_url"):
            return str(r["request_url"])
    return ""


def build_numbered_sources(synthesis: dict[str, Any], source_runs: list[dict[str, Any]],
                           raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The [n] ledger. Use what the model authored if present; else derive from the run ledger."""
    authored = synthesis.get("numbered_sources") or []
    if authored:
        return authored
    out: list[dict[str, Any]] = []
    for i, run in enumerate(source_runs or [], start=1):
        connector = run.get("connector") or "source"
        out.append({
            "n": i,
            "label": f"{connector}" + (f' — "{run["query"]}"' if run.get("query") else ""),
            "tool": connector,
            "url": _raw_url(raw_rows, connector),
            "pulled_at": str(run.get("attempted_at"))[:19] if run.get("attempted_at") else "",
            "note": f"{run.get('outcome', '')}"
                    + (f", {run.get('yield_count')} rows" if run.get("yield_count") is not None else ""),
        })
    return out


def write_sources_ledger(sources: list[dict[str, Any]], job: dict[str, Any], path: Path) -> None:
    lines = [f"# Sources & citations — {job.get('brief', '')}".rstrip(),
             "",
             "Every `[n]` in the report resolves to an entry here: the tool that pulled it, the "
             "request URL where there is one, the pull date, and a note on what it returned.",
             ""]
    if not sources:
        lines.append("_No sources recorded._")
    for s in sources:
        head = f"**[{s.get('n')}]** {s.get('label', '')}".rstrip()
        lines.append(head)
        detail = []
        if s.get("tool"):
            detail.append(f"tool: `{s['tool']}`")
        if s.get("url"):
            detail.append(f"url: {s['url']}")
        if s.get("pulled_at"):
            detail.append(f"pulled: {s['pulled_at']}")
        if s.get("note"):
            detail.append(f"note: {s['note']}")
        if detail:
            lines.append("  " + " · ".join(detail))
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_readme(job: dict[str, Any], synthesis: dict[str, Any], manifest: list[tuple[str, str]],
                 path: Path) -> None:
    pov = synthesis.get("point_of_view") or synthesis.get("executive_answer") or ""
    lines = [f"# {job.get('brief', 'Research package')}",
             "",
             f"Research Oyster deliverable for job #{job.get('id')}. Start here.",
             ""]
    if pov:
        lines += ["## The short version", "", pov, ""]
    lines += ["## What's in this folder", ""]
    for name, desc in manifest:
        lines.append(f"- **{name}** — {desc}")
    lines += ["",
              "Open `report.docx` or `report.html` for the full written report; `deck.pptx` for the "
              "slides; `charts/` for the figures and their CSVs; `Sources-and-Citations.md` to trace "
              "any `[n]`; `raw-data/` for the underlying pulls."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_raw_data(raw_rows: list[dict[str, Any]], evidence: list[dict[str, Any]], raw_dir: Path) -> None:
    """Split the raw pulls per connector so each source's rows are auditable on their own."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    by_connector: dict[str, list[dict[str, Any]]] = {}
    for r in raw_rows or []:
        by_connector.setdefault(str(r.get("collector") or "unknown"), []).append(r)
    for connector, rows in by_connector.items():
        safe = connector.replace("/", "-").replace(" ", "_")[:60] or "source"
        with (raw_dir / f"{safe}.jsonl").open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")
    # Evidence rows (the captured quotes/records) as one file for convenience.
    (raw_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
