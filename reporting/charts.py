"""Render the computed metrics_tables into chart images + CSVs for the deliverable package.

Each metrics_table (built by the quantify node from compute_metric/compute_rate) becomes:
  - charts/<slug>.png and .svg  — a horizontal median-by-group bar chart, annotated with n
  - charts/<slug>.csv           — the underlying rows (group, median, mean, n, min, max)

Pure over the synthesis dict; used by export_job. matplotlib runs headless (Agg). Returns the
list of chart PNG paths so the deck can embed them. Defensive: a malformed table is skipped, not
fatal — a report should still export.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

# matplotlib is OPTIONAL. If it's missing, charts are skipped and the rest of the package
# (report.md/.html/.docx, deck text, Sources, raw-data) still ships — a missing chart library must
# never take down the whole export. Import lazily so `from reporting import charts` always succeeds.
try:
    import matplotlib
    matplotlib.use("Agg")  # no display in a server/subprocess
    import matplotlib.pyplot as plt
except Exception:  # noqa: BLE001 — any import/backend failure: degrade to no-charts
    plt = None

from reporting import theme  # noqa: E402

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-")[:50] or "metric"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _rows_with_median(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for r in table.get("rows") or []:
        if _num(r.get("median")) is not None:
            rows.append(r)
    return rows


def write_metric_csv(table: dict[str, Any], path: Path) -> None:
    """Write the table's real columns (whatever the row shape is), group first."""
    rows = [r for r in (table.get("rows") or []) if isinstance(r, dict)]
    cols: list[str] = []
    for r in rows:
        for k in r.keys():
            if k != "group" and k not in cols:
                cols.append(k)
    header = ["group"] + cols
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(c, "") for c in header])


def render_metric_chart(table: dict[str, Any], path_png: Path) -> Path | None:
    """Horizontal bar of the table's best numeric column by group, tallest at top, each bar
    labelled with its value (and n when present)."""
    from reporting.tables import chart_series

    if plt is None:  # matplotlib not installed — skip charts, the rest of the package still ships
        return None
    series = chart_series(table)
    if not series:
        return None
    pts = sorted(series["points"], key=lambda p: p["value"])  # ascending → largest at top
    groups = [p["group"] for p in pts]
    medians = [p["value"] for p in pts]
    # attach n per group when the row carried one (rate/count tables)
    n_by_group = {str(r.get("group")): r.get("n") for r in (table.get("rows") or []) if isinstance(r, dict)}
    ns = [n_by_group.get(g) for g in groups]

    height = max(2.2, 0.55 * len(pts) + 1.2)
    fig, ax = plt.subplots(figsize=(8.5, height), dpi=150)
    fig.patch.set_facecolor(theme.PAPER)
    ax.set_facecolor(theme.PAPER)

    bars = ax.barh(groups, medians, color=theme.ACCENT, edgecolor="none", height=0.62)
    # Highlight the leader; mute the rest for a clear "this one" read.
    for b in bars[:-1]:
        b.set_color(theme.SUBTLE)
        b.set_alpha(0.55)

    unit = table.get("unit") or ""
    span = max(medians) or 1.0
    for y, (val, n) in enumerate(zip(medians, ns)):
        label = _fmt(val) + (f"{unit}" if unit in ("%",) else "")
        if n is not None:
            label += f"   n={n}"
        ax.text(val + span * 0.01, y, label, va="center", ha="left",
                fontsize=10, color=theme.INK, fontfamily=theme.FONT)

    ax.set_title(table.get("title") or "Metric", loc="left", fontsize=13.5,
                 color=theme.INK, fontweight="bold", fontfamily=theme.FONT, pad=12)
    col = series.get("col_label") or "value"
    xlabel = f"{col} ({unit})" if unit and unit != "%" else col
    ax.set_xlabel(xlabel, fontsize=10, color=theme.SUBTLE, fontfamily=theme.FONT)
    ax.set_xlim(0, span * 1.22)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(theme.GRID)
    ax.tick_params(axis="y", length=0, labelsize=10.5, colors=theme.INK)
    ax.tick_params(axis="x", length=0, labelsize=9, colors=theme.SUBTLE)
    ax.xaxis.grid(True, color=theme.GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()

    path_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path_png, facecolor=theme.PAPER, bbox_inches="tight")
    fig.savefig(path_png.with_suffix(".svg"), facecolor=theme.PAPER, bbox_inches="tight")
    plt.close(fig)
    return path_png


def _fmt(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}k"
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def build_charts(metrics_tables: list[dict[str, Any]], charts_dir: Path) -> list[dict[str, Any]]:
    """Render every table to chart + CSV. Returns [{table, png, csv}] for tables that produced a
    chart, so the deck can embed them. Skips tables with no numeric medians."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, table in enumerate(metrics_tables or []):
        slug = _slug(table.get("title") or f"metric-{i+1}")
        while slug in seen:
            slug += "-x"
        seen.add(slug)
        csv_path = charts_dir / f"{slug}.csv"
        try:
            write_metric_csv(table, csv_path)
        except Exception:
            csv_path = None  # type: ignore
        png = None
        try:
            png = render_metric_chart(table, charts_dir / f"{slug}.png")
        except Exception:
            png = None
        if png or csv_path:
            out.append({"table": table, "png": png, "csv": csv_path, "slug": slug})
    return out
