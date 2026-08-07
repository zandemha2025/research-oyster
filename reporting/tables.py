"""Normalize a model-authored metrics_table into printable columns.

The synthesis authors each table with the columns that fit it — a Discord table has
online/members/rate/voice-channels, a rate table has median/n, a chatter table has
messages/substantive. Renderers (markdown report, docx, deck, charts) must therefore adapt to
whatever columns a row actually carries instead of assuming a fixed median/n/range schema — that
mismatch left most tables rendering as empty cells. This turns any row shape into a header + a
grid of formatted cells, and finds the best numeric column to chart.
"""

from __future__ import annotations

from typing import Any


def _fmt(v: Any) -> str:
    """Format a table cell. Full comma-separated numbers (106,825 — not '107' or '107k') so a data
    table stays unambiguous; charts/tiles do their own compact k/M formatting where space is tight."""
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float) and v != int(v):
        return f"{v:,.2f}"
    if isinstance(v, (int, float)):
        return f"{int(v):,}"
    return str(v)


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            if k != "group" and k not in cols:
                cols.append(k)
    return cols


def _label(col: str) -> str:
    return col.replace("_", " ").strip()


def normalize_table(table: dict[str, Any]) -> dict[str, Any]:
    """Return {title, unit, note, group_label, header:[...], body:[[cell,...],...]} where header
    and each body row line up. Columns are the union of the rows' keys (order preserved)."""
    rows = [r for r in (table.get("rows") or []) if isinstance(r, dict)]
    cols = _columns(rows)
    group_label = _label(table.get("group_by") or "group").title()
    header = [group_label] + [_label(c) for c in cols]
    body = [[str(r.get("group", "—"))] + [_fmt(r.get(c)) for c in cols] for r in rows]
    return {"title": table.get("title") or "Metric", "unit": table.get("unit") or "",
            "note": table.get("note") or "", "group_label": group_label,
            "header": header, "body": body}


def chart_series(table: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the best numeric column to chart (prefers median, then a count/online/followers-type
    column) and return {title, unit, col_label, points:[{group,value}]}. None if nothing numeric."""
    rows = [r for r in (table.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return None
    cols = _columns(rows)

    def _numeric(col: str) -> bool:
        return any(isinstance(r.get(col), (int, float)) and not isinstance(r.get(col), bool) for r in rows)

    preferred = ["median", "online", "online_n1", "members", "members_n1", "followers",
                 "substantive", "messages", "viewers", "count", "n"]
    pick = next((c for c in preferred if c in cols and _numeric(c)), None)
    if pick is None:
        pick = next((c for c in cols if _numeric(c)), None)
    if pick is None:
        return None
    points = [{"group": str(r.get("group", "—")), "value": float(r[pick])}
              for r in rows if isinstance(r.get(pick), (int, float)) and not isinstance(r.get(pick), bool)]
    if not points:
        return None
    return {"title": table.get("title") or "Metric", "unit": table.get("unit") or "",
            "col_label": _label(pick), "points": points}
