"""Compute real numbers over collected evidence — medians, rates, and sample sizes.

This is what turns a pile of scraped rows into "1.02% share rate, n=22" — the difference
between a Reddit summary and a cited, quantified finding. It reads the normalized numbers that
`apify.normalize_row` stamped onto each evidence row's `metadata.metrics`; for evidence that
lacks that (older connectors), it falls back to extracting numbers from `metadata.record`.

Pure functions over the `dossier()["evidence"]` list — unit-tested with no DB. The MCP
`compute_metric` / `compute_rate` / `list_metric_fields` tools call these so the synthesize node
can cite computed figures with sample sizes instead of eyeballing quotes.
"""

from __future__ import annotations

import statistics
from typing import Any, Iterator

from research_engine import apify


def _metrics_of(evidence_row: dict[str, Any]) -> dict[str, float]:
    meta = evidence_row.get("metadata") or {}
    metrics = meta.get("metrics")
    if isinstance(metrics, dict) and metrics:
        return {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    record = meta.get("record")
    if isinstance(record, dict):
        return apify.extract_metrics(record)
    return {}


def _group_of(evidence_row: dict[str, Any], group_by: str) -> str:
    meta = evidence_row.get("metadata") or {}
    if group_by == "entity":
        return str(meta.get("entity") or evidence_row.get("author") or "—")
    if group_by == "query_shape":
        return str(meta.get("query_shape") or evidence_row.get("query") or "—")
    if group_by == "source_type":
        return str(evidence_row.get("source_type") or "—")
    return str(meta.get(group_by) or "—")


def _pairs(evidence: list[dict[str, Any]], value_field: str, group_by: str) -> Iterator[tuple[str, float]]:
    for row in evidence:
        val = _metrics_of(row).get(value_field)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            yield _group_of(row, group_by), float(val)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "mean": None, "min": None, "max": None, "total": 0}
    return {"n": len(values), "median": round(statistics.median(values), 4),
            "mean": round(statistics.fmean(values), 4), "min": min(values), "max": max(values),
            "total": round(sum(values), 4)}


def list_metric_fields(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    fields: set[str] = set()
    for row in evidence:
        fields.update(_metrics_of(row).keys())
    return {"value_fields": sorted(fields),
            "group_by_options": ["entity", "query_shape", "source_type"],
            "evidence_count": len(evidence)}


def compute_metric(evidence: list[dict[str, Any]], value_field: str,
                   group_by: str = "entity", min_sample: int = 1) -> dict[str, Any]:
    """Median/mean/min/max/n of a numeric field, per group and overall."""
    grouped: dict[str, list[float]] = {}
    overall: list[float] = []
    for group, val in _pairs(evidence, value_field, group_by):
        grouped.setdefault(group, []).append(val)
        overall.append(val)
    rows = [{"group": g, **_summary(v)} for g, v in grouped.items() if len(v) >= max(1, min_sample)]
    rows.sort(key=lambda r: (r["median"] if r["median"] is not None else -1), reverse=True)
    return {"value_field": value_field, "group_by": group_by, "min_sample": min_sample,
            "groups": rows, "overall": _summary(overall)}


def compute_rate(evidence: list[dict[str, Any]], numerator: str, denominator: str,
                 group_by: str = "entity", min_sample: int = 1, as_percent: bool = True) -> dict[str, Any]:
    """Per-item ratio numerator/denominator (e.g. shares/views = share rate), then median per
    group and overall. The metric the Truly report is built on."""
    scale = 100.0 if as_percent else 1.0
    grouped: dict[str, list[float]] = {}
    overall: list[float] = []
    for row in evidence:
        m = _metrics_of(row)
        num, den = m.get(numerator), m.get(denominator)
        if isinstance(num, (int, float)) and isinstance(den, (int, float)) and den > 0:
            rate = (num / den) * scale
            grouped.setdefault(_group_of(row, group_by), []).append(rate)
            overall.append(rate)
    rows = [{"group": g, **_summary(v)} for g, v in grouped.items() if len(v) >= max(1, min_sample)]
    rows.sort(key=lambda r: (r["median"] if r["median"] is not None else -1), reverse=True)
    return {"rate": f"{numerator}/{denominator}", "unit": "%" if as_percent else "ratio",
            "group_by": group_by, "min_sample": min_sample, "groups": rows, "overall": _summary(overall)}
