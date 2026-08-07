"""Write a research job's authored report + a polished deliverable package to output/.

The deliverable is the report that ANSWERS the question — the results-first, model-authored
synthesis (point of view, executive answer with numbers, the metrics tables, themes with cited
quotes, method, recommendations, confidence & a short limitations caveat, and the numbered [n]
ledger). Evidence is demoted to an appendix.

Produces, per job:
  - report.md / report.html — the readable report
  - report.docx — the same report as a shareable Word document (source of truth)
  - deck.pptx — a slide deck led by the point of view and the numbers
  - charts/ — the key metrics as PNG+SVG bar charts, with the CSVs behind them
  - Sources-and-Citations.md — the numbered [n] ledger (tool + pull date + url per source)
  - README-start-here.md — orientation for the package
  - raw-data/ — the raw pulls split per connector, plus evidence.json
  - evidence.json / evidence.csv / raw_responses.jsonl — the raw evidence + network payloads

The core report is written first; the polished package builders are best-effort (a builder that
fails, e.g. a missing optional dep, is recorded as a warning and skipped — the report still ships).
Export refuses to run without a synthesis: a data dump is not a report.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

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


def _report_title(brief: str) -> str:
    """A short, clean report title from the brief — its first meaningful line, stripped of markdown
    and any 'Research brief —' prefix, so the report doesn't render the whole brief as an H1."""
    for line in (brief or "").splitlines():
        line = line.lstrip("#").strip()
        if not line:
            continue
        line = re.sub(r"(?i)^research brief\s*[—:-]\s*", "", line)
        line = re.sub(r"\s*\((?:internal|confidential)[^)]*\)", "", line, flags=re.I).strip(" —-")
        if len(line) > 72:  # cut at a word boundary, never mid-word
            line = line[:72].rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"
        return line or "Research report"
    return "Research report"


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
    synthesis = dossier.get("synthesis")
    if not synthesis or not synthesis.get("executive_answer"):
        # A report answers the question. Refuse to emit a bare data dump.
        raise ValueError(
            "This job has no synthesis yet, so there is nothing to report — only raw evidence. "
            "Call write_research_synthesis(job_id, executive_answer, themes, ...) to author the "
            "answer first, then export."
        )
    raw_rows = store.list_raw_responses(job_id)

    base = Path(output_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base  # MCP host cwd is unpredictable; anchor to the project
    folder = base / f"research-job-{job_id}-{_slug(job.get('brief', ''))}"
    folder.mkdir(parents=True, exist_ok=True)

    from reporting.tables import normalize_table
    from reporting.biblio import build_bibliography, annotate_themes
    from reporting.html_report import build_html
    from reporting import charts as charts_mod

    url_to_n, biblio = build_bibliography(synthesis)
    themes = annotate_themes(synthesis, url_to_n)
    raw_tables = synthesis.get("metrics_tables") or []
    tables = [normalize_table(t) for t in raw_tables]
    title = _report_title(job.get("brief", ""))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build charts up front — the HTML, docx and deck all embed them.
    try:
        charts = charts_mod.build_charts(raw_tables, folder / "charts")
    except Exception:
        charts = []

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    env.filters["cell"] = _cell
    markdown = env.get_template("research_dossier.md.j2").render(
        job=job,
        plan=job.get("plan", {}),
        synthesis=synthesis,
        title=title,
        themes=themes,
        bibliography=biblio,
        metrics_tables=tables,
        generated_at=generated_at,
        findings=_group_findings(evidence),
        coverage=[{"source": source, "count": count} for source, count in dossier.get("coverage", {}).items()],
        source_runs=dossier.get("source_runs", []),
        evidence_count=len(evidence),
    )

    report_md = folder / "report.md"
    report_html = folder / "report.html"
    evidence_json = folder / "evidence.json"
    evidence_csv = folder / "evidence.csv"
    raw_jsonl = folder / "raw_responses.jsonl"

    report_md.write_text(markdown, encoding="utf-8")
    # The HTML is its own designed web report (not a markdown dump) — see reporting/html_report.py.
    try:
        html = build_html(title, synthesis, themes, tables, biblio, generated_at, charts)
    except Exception:
        html = _to_html(markdown)  # fallback: never fail the export over styling
    report_html.write_text(html, encoding="utf-8")
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

    # --- Consultant-grade package: charts, deck, docx, numbered ledger, README, raw-data -----
    # Each builder is best-effort: a failure in one (e.g. a missing optional dep) must not sink
    # the export — the core report above is already written. We collect what succeeds.
    package = _build_package(folder, job, synthesis, evidence, raw_rows,
                             dossier.get("source_runs", []), charts=charts, themes=themes,
                             biblio=biblio, title=title)
    files.extend(package["files"])

    return {
        "job_id": job_id,
        "folder": str(folder),
        "files": [str(path) for path in files],
        "evidence_count": len(evidence),
        "raw_count": len(raw_rows),
        "package": [Path(p).name for p in package["files"]],
        "warnings": package["warnings"],
    }


def _build_package(folder: Path, job: dict[str, Any], synthesis: dict[str, Any],
                   evidence: list[dict[str, Any]], raw_rows: list[dict[str, Any]],
                   source_runs: list[dict[str, Any]], charts: list[dict[str, Any]] | None = None,
                   themes: list[dict[str, Any]] | None = None, biblio: list[dict[str, Any]] | None = None,
                   title: str = "") -> dict[str, Any]:
    """Emit the polished deliverables alongside the core report. Never raises — returns the files
    that were produced plus a list of warnings for anything that couldn't be built."""
    from reporting import package as pkg

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    produced: list[Path] = []
    warnings: list[str] = []
    # export_job builds charts up front and passes them in; when called standalone (charts is None)
    # build them here so the package is complete either way.
    if charts is None:
        try:
            from reporting import charts as charts_mod

            charts = charts_mod.build_charts(synthesis.get("metrics_tables") or [], folder / "charts")
        except Exception as exc:  # pragma: no cover
            charts = []
            warnings.append(f"charts: {type(exc).__name__}: {exc}")
    charts = charts or []

    # Record the chart files for the manifest.
    for ch in charts:
        for key in ("png", "csv"):
            p = ch.get(key)
            if p and Path(p).exists():
                produced.append(Path(p))
                if key == "png":
                    svg = Path(p).with_suffix(".svg")
                    if svg.exists():
                        produced.append(svg)
    if any(ch.get("png") for ch in charts):
        produced.append(folder / "charts")  # so the README/Studio can link the folder

    # Written report (.docx) — designed, with the same bibliography [n] as the HTML.
    try:
        from reporting import docx_report

        produced.append(docx_report.build_docx(job, synthesis, generated_at, charts,
                                               folder / "report.docx", themes=themes,
                                               biblio=biblio, title=title))
    except Exception as exc:  # pragma: no cover
        warnings.append(f"report.docx: {type(exc).__name__}: {exc}")

    # Slide deck (.pptx)
    try:
        from reporting import deck as deck_mod

        produced.append(deck_mod.build_deck(job, synthesis, generated_at, charts, folder / "deck.pptx"))
    except Exception as exc:  # pragma: no cover
        warnings.append(f"deck.pptx: {type(exc).__name__}: {exc}")

    # Numbered [n] bibliography — prefer the deterministic one built from cited URLs (deep links).
    ledger = folder / "Sources-and-Citations.md"
    if biblio:
        pkg.write_bibliography(biblio, job, ledger)
    else:
        pkg.write_sources_ledger(pkg.build_numbered_sources(synthesis, source_runs, raw_rows), job, ledger)
    produced.append(ledger)

    # raw-data/ split per connector
    try:
        pkg.write_raw_data(raw_rows, evidence, folder / "raw-data")
        produced.append(folder / "raw-data")
    except Exception as exc:  # pragma: no cover
        warnings.append(f"raw-data: {type(exc).__name__}: {exc}")

    # README orientation — written last so it can describe what actually got produced.
    produced_names = {p.name for p in produced}
    candidates: list[tuple[str, str]] = [
        ("report.docx", "the full written report (source of truth)"),
        ("report.html", "the same report for the browser"),
        ("deck.pptx", "slide deck led by the point of view and the numbers"),
        ("charts", "the key metrics as PNG+SVG charts, with the CSVs behind them"),
        ("Sources-and-Citations.md", "the numbered [n] ledger — trace any citation to its pull"),
        ("raw-data", "the raw pulls per connector, plus evidence.json"),
        ("evidence.csv", "the captured evidence rows as a spreadsheet"),
    ]
    manifest = [(n, d) for n, d in candidates if n in produced_names or n in ("report.html", "evidence.csv")]
    readme = folder / "README-start-here.md"
    pkg.write_readme(job, synthesis, manifest, readme)
    produced.insert(0, readme)

    return {"files": produced, "warnings": warnings}
