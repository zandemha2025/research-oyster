"""Build report.docx — the results-first written report, as a real Word document.

Mirrors the markdown report's structure (POV → executive answer → the numbers → themes →
recommendations → method → confidence/limitations → sources) but styled as a shareable .docx
a consultant would send. Built directly from the synthesis dict (not by parsing markdown), so
headings, the metrics tables, and the [n] ledger get proper Word styles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from reporting import theme

_INK = RGBColor(*theme.rgb(theme.INK))
_SUBTLE = RGBColor(*theme.rgb(theme.SUBTLE))
_ACCENT = RGBColor(*theme.rgb(theme.ACCENT))


def _style(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = theme.FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _INK


def _heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_heading(level=level)
    run = p.add_run(text)
    run.font.name = theme.FONT
    run.font.color.rgb = _ACCENT if level == 1 else _INK
    run.font.size = Pt(15 if level == 1 else 12.5)


def _para(doc: Document, text: str, *, color: RGBColor | None = None, size: float = 10.5,
          italic: bool = False, bold: bool = False) -> None:
    if not text:
        return
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = theme.FONT
    run.font.size = Pt(size)
    run.font.italic = italic
    run.font.bold = bold
    run.font.color.rgb = color or _INK


def _metric_table(doc: Document, table: dict[str, Any]) -> None:
    rows = table.get("rows") or []
    if not rows:
        return
    _heading(doc, (table.get("title") or "Metric") + (f"  ({table['unit']})" if table.get("unit") else ""), level=2)
    group_label = (table.get("group_by") or "Group").title()
    t = doc.add_table(rows=1, cols=4)
    t.style = "Light Grid Accent 1"
    for cell, label in zip(t.rows[0].cells, [group_label, "Median", "n", "Range"]):
        run = cell.paragraphs[0].add_run(label)
        run.font.bold = True
        run.font.size = Pt(10)
        run.font.name = theme.FONT
    for r in rows:
        cells = t.add_row().cells
        med = r.get("median")
        rng = f"{r.get('min')}–{r.get('max')}" if r.get("min") is not None and r.get("max") is not None else "—"
        vals = [str(r.get("group", "—")), "—" if med is None else str(med),
                "—" if r.get("n") is None else str(r.get("n")), rng]
        for cell, v in zip(cells, vals):
            run = cell.paragraphs[0].add_run(v)
            run.font.size = Pt(10)
            run.font.name = theme.FONT
    if table.get("note"):
        _para(doc, table["note"], color=_SUBTLE, size=9, italic=True)


def build_docx(job: dict[str, Any], synthesis: dict[str, Any], generated_at: str,
               charts: list[dict[str, Any]] | None, path: Path) -> Path:
    doc = Document()
    _style(doc)

    _heading(doc, job.get("brief") or "Research report", level=1)
    _para(doc, f"Generated {generated_at} · job #{job.get('id')}", color=_SUBTLE, size=9)

    if synthesis.get("point_of_view"):
        _heading(doc, "Our point of view", level=2)
        _para(doc, synthesis["point_of_view"], size=12, bold=True)

    _heading(doc, "Executive answer", level=2)
    _para(doc, synthesis.get("executive_answer") or "—")

    tables = synthesis.get("metrics_tables") or []
    if tables:
        _heading(doc, "The numbers", level=1)
        for table in tables:
            _metric_table(doc, table)
        # Embed the rendered charts under the numbers, if we have them.
        for ch in charts or []:
            png = ch.get("png")
            if png and Path(png).exists():
                try:
                    doc.add_picture(str(png))
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    pass

    themes = synthesis.get("themes") or []
    if themes:
        _heading(doc, "Key themes", level=1)
        for th in themes:
            _heading(doc, th.get("title") or "Theme", level=2)
            if th.get("insight"):
                _para(doc, th["insight"])
            for c in th.get("citations") or []:
                quote, src, url = c.get("quote"), c.get("source"), c.get("url")
                if quote:
                    line = f'"{quote}"' + (f" — {src or url}" if (src or url) else "")
                elif url:
                    line = src or url
                else:
                    continue
                p = doc.add_paragraph(style="List Bullet")
                run = p.add_run(line)
                run.font.size = Pt(10)
                run.font.name = theme.FONT

    if synthesis.get("tensions"):
        _heading(doc, "Tensions and disagreement", level=2)
        _para(doc, synthesis["tensions"])
    if synthesis.get("sentiment"):
        _heading(doc, "Sentiment", level=2)
        _para(doc, synthesis["sentiment"])

    recs = synthesis.get("recommendations") or []
    if recs:
        _heading(doc, "Recommendations", level=1)
        for rec in recs:
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(str(rec))
            run.font.size = Pt(10.5)
            run.font.name = theme.FONT

    if synthesis.get("method"):
        _heading(doc, "Method — how the numbers were made", level=2)
        _para(doc, synthesis["method"], size=10)

    if synthesis.get("confidence") or synthesis.get("limitations"):
        _heading(doc, "Confidence and limitations", level=2)
        _para(doc, synthesis.get("confidence") or "")
        if synthesis.get("limitations"):
            _para(doc, synthesis["limitations"], color=_SUBTLE, size=10)

    sources = synthesis.get("numbered_sources") or []
    if sources:
        _heading(doc, "Sources", level=1)
        for s in sources:
            bits = [f"[{s.get('n')}]", s.get("label") or ""]
            if s.get("tool"):
                bits.append(f"· {s['tool']}")
            if s.get("url"):
                bits.append(f"· {s['url']}")
            if s.get("pulled_at"):
                bits.append(f"· pulled {s['pulled_at']}")
            if s.get("note"):
                bits.append(f"— {s['note']}")
            _para(doc, " ".join(str(b) for b in bits if b), size=9.5, color=_SUBTLE)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
