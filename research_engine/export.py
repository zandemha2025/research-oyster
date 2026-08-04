"""Write a research job's dossier and raw evidence to a folder under output/.

Produces five files per job so the work leaves chat and becomes shareable files:
report.md and report.html (readable dossier), evidence.json and evidence.csv (the raw
evidence rows), and raw_responses.jsonl (the redacted network payloads captured during
collection). The gaps section is built from the same CONNECTOR_GUIDES the connectors use,
so "how to unlock" advice never drifts from actual connector behavior.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from research_engine.connectors import CONNECTOR_GUIDES, SOURCE_TO_CONNECTOR
from research_engine.store import ResearchStore
from reporting.render import _to_html

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "reporting" / "templates"
_SLUG = re.compile(r"[^a-z0-9]+")


def _cell(value: Any) -> str:
    """Escape characters that break the Markdown->HTML table/list converter in render.py."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-")[:60] or "job"


def _group_findings(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        source = row.get("source_type") or "unknown"
        if source not in groups:
            groups[source] = []
            order.append(source)
        groups[source].append({
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "author": row.get("author") or "",
            "published": row.get("published_at") or "",
            "excerpt": (row.get("excerpt") or "")[:500],
        })
    return [{"source_type": source, "entries": groups[source]} for source in order]


def _gaps(dossier: dict[str, Any]) -> list[dict[str, Any]]:
    gaps = []
    for source in dossier.get("gaps", []):
        guide = CONNECTOR_GUIDES.get(SOURCE_TO_CONNECTOR.get(source, source), {})
        setup = guide.get("setup", "")
        fallbacks = guide.get("fallbacks", [])
        # Always-ready sources (rss, web) need no credentials, so they carry no setup or
        # fallbacks. Surface their scope as guidance instead of leaving a bare heading.
        note = "" if (setup or fallbacks) else (guide.get("scope") or "This source needs no setup — collect it directly.")
        gaps.append({"source": source, "setup": setup, "fallbacks": fallbacks, "note": note})
    return gaps


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:  # spreadsheet formula-injection guard
        text = "'" + text
    return text


def job_folder(store: ResearchStore, job_id: int, output_dir: Path | str = Path("output")) -> Path:
    """Compute a job's export folder path without writing anything.

    Used by the control center's 'Open folder' so it can open an existing export instead
    of regenerating every file on each click.
    """
    job = store.dossier(job_id)["job"]
    base = Path(output_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base / f"research-job-{job_id}-{_slug(job.get('brief', ''))}"


def export_job(store: ResearchStore, job_id: int, output_dir: Path | str = Path("output")) -> dict[str, Any]:
    dossier = store.dossier(job_id)
    job = dossier["job"]
    evidence = dossier["evidence"]
    raw_rows = store.list_raw_responses(job_id)

    base = Path(output_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base  # MCP host cwd is unpredictable; anchor to the project
    folder = base / f"research-job-{job_id}-{_slug(job.get('brief', ''))}"
    folder.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    env.filters["cell"] = _cell
    markdown = env.get_template("research_dossier.md.j2").render(
        job=job,
        plan=job.get("plan", {}),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        findings=_group_findings(evidence),
        coverage=[{"source": source, "count": count} for source, count in dossier.get("coverage", {}).items()],
        gaps=_gaps(dossier),
        evidence_count=len(evidence),
    )

    report_md = folder / "report.md"
    report_html = folder / "report.html"
    evidence_json = folder / "evidence.json"
    evidence_csv = folder / "evidence.csv"
    raw_jsonl = folder / "raw_responses.jsonl"

    report_md.write_text(markdown, encoding="utf-8")
    report_html.write_text(_to_html(markdown), encoding="utf-8")
    evidence_json.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")

    columns = ["id", "source_type", "url", "title", "excerpt", "author",
               "published_at", "collected_at", "query", "metadata"]
    with evidence_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in evidence:
            writer.writerow([
                _csv_safe(json.dumps(row["metadata"], default=str) if column == "metadata" else row.get(column))
                for column in columns
            ])

    with raw_jsonl.open("w", encoding="utf-8") as handle:
        for row in raw_rows:
            handle.write(json.dumps(row, default=str) + "\n")

    files = [report_md, report_html, evidence_json, evidence_csv, raw_jsonl]
    return {
        "job_id": job_id,
        "folder": str(folder),
        "files": [str(path) for path in files],
        "evidence_count": len(evidence),
        "raw_count": len(raw_rows),
    }
