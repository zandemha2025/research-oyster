from __future__ import annotations

import unittest

from research_engine import metrics


def _ev(entity, metrics_dict, source_type="tiktok", query_shape="q1"):
    return {"source_type": source_type, "author": entity,
            "metadata": {"entity": entity, "query_shape": query_shape, "metrics": metrics_dict}}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            _ev("betches", {"views": 100, "shares": 2}),
            _ev("betches", {"views": 200, "shares": 6}),
            _ev("mcf", {"views": 50, "shares": 1}),
            _ev("mcf", {"views": 150, "shares": 3}),
            {"source_type": "web", "metadata": {}},  # no metrics — ignored
        ]

    def test_list_fields(self):
        fields = metrics.list_metric_fields(self.evidence)["value_fields"]
        self.assertIn("views", fields)
        self.assertIn("shares", fields)

    def test_compute_metric_median_and_n(self):
        out = metrics.compute_metric(self.evidence, "views", group_by="entity")
        by = {g["group"]: g for g in out["groups"]}
        self.assertEqual(by["betches"]["median"], 150)   # median(100,200)
        self.assertEqual(by["betches"]["n"], 2)
        self.assertEqual(out["overall"]["n"], 4)          # 4 rows have views
        # sorted by median desc: betches (150) before mcf (100)
        self.assertEqual(out["groups"][0]["group"], "betches")

    def test_compute_rate_percent(self):
        out = metrics.compute_rate(self.evidence, "shares", "views", group_by="entity")
        self.assertEqual(out["unit"], "%")
        by = {g["group"]: g for g in out["groups"]}
        # betches per-item rates: 2/100=2%, 6/200=3% -> median 2.5
        self.assertEqual(by["betches"]["median"], 2.5)
        # mcf: 1/50=2%, 3/150=2% -> median 2.0
        self.assertEqual(by["mcf"]["median"], 2.0)

    def test_min_sample_filters(self):
        out = metrics.compute_metric(self.evidence, "views", group_by="entity", min_sample=3)
        self.assertEqual(out["groups"], [])  # no entity has 3+

    def test_fallback_extracts_from_record(self):
        ev = [{"source_type": "x", "metadata": {"record": {"public_metrics": {"like_count": 40}}}}]
        out = metrics.compute_metric(ev, "likes")
        self.assertEqual(out["overall"]["n"], 1)
        self.assertEqual(out["overall"]["median"], 40)


if __name__ == "__main__":
    unittest.main()
