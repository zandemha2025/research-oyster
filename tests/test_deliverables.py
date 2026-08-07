from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_engine.export import _build_package
from reporting import charts, package
from reporting.tables import normalize_table, chart_series


SYNTH = {
    "executive_answer": "Short-form wins: median 42k views (n=18) vs 9k on YouTube.",
    "point_of_view": "The brief assumes YouTube; the evidence says TikTok.",
    "themes": [{"title": "Creators drive it", "insight": "few accounts",
                "citations": [{"quote": "this changed my routine", "url": "https://x/1", "source": "@a"}]}],
    "metrics_tables": [
        {"title": "Median views by platform", "unit": "views", "group_by": "platform", "rows": [
            {"group": "tiktok", "median": 42000, "mean": 51000, "n": 18, "min": 1200, "max": 310000},
            {"group": "youtube", "median": 9000, "mean": 12000, "n": 11, "min": 400, "max": 60000}],
         "note": "90-day window"},
    ],
    "method": "median views by platform, 90-day, min 1k views",
    "recommendations": ["Lead on TikTok"],
    "confidence": "medium-high",
    "limitations": "Discord messages unreachable; used member counts as a proxy.",
    "numbered_sources": [{"n": 1, "label": "TikTok search", "tool": "apify_collect",
                          "url": "https://apify.com", "pulled_at": "2026-08-06", "note": "18 rows"}],
}
JOB = {"id": 7, "brief": "Where should we grow short-form?"}


class DeliverablesTests(unittest.TestCase):
    def test_full_package_builds_every_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            res = _build_package(
                folder, JOB, SYNTH,
                evidence=[{"source_type": "tiktok", "excerpt": "hi"}],
                raw_rows=[{"collector": "apify_tiktok", "request_url": "https://x", "received_at": "2026-08-06T00:00:00"}],
                source_runs=[{"connector": "apify_tiktok", "query": "topic", "outcome": "ok",
                              "yield_count": 18, "attempted_at": "2026-08-06T00:00:00"}],
            )
            self.assertEqual(res["warnings"], [])  # nothing failed
            names = {p.name for p in res["files"]}
            for expected in {"README-start-here.md", "Sources-and-Citations.md", "report.docx", "deck.pptx"}:
                self.assertIn(expected, names)
            # charts folder + a PNG/SVG/CSV triple
            self.assertTrue((folder / "charts" / "median-views-by-platform.png").exists())
            self.assertTrue((folder / "charts" / "median-views-by-platform.svg").exists())
            self.assertTrue((folder / "charts" / "median-views-by-platform.csv").exists())
            # docx and pptx are non-trivial (real content, not empty stubs)
            self.assertGreater((folder / "report.docx").stat().st_size, 10_000)
            self.assertGreater((folder / "deck.pptx").stat().st_size, 10_000)
            # raw-data split per connector
            self.assertTrue((folder / "raw-data" / "apify_tiktok.jsonl").exists())

    def test_ledger_prefers_authored_then_falls_back(self):
        authored = package.build_numbered_sources(SYNTH, [], [])
        self.assertEqual(authored[0]["label"], "TikTok search")
        # No authored ledger → derive from the source_runs, carrying the real pull date + outcome.
        derived = package.build_numbered_sources(
            {}, [{"connector": "reddit_search", "query": "q", "outcome": "empty",
                  "yield_count": 0, "attempted_at": "2026-08-06T01:02:03"}],
            [{"collector": "reddit_search", "request_url": "https://reddit/q"}])
        self.assertEqual(derived[0]["tool"], "reddit_search")
        self.assertEqual(derived[0]["url"], "https://reddit/q")
        self.assertEqual(derived[0]["pulled_at"], "2026-08-06T01:02:03")

    def test_chart_skips_table_with_no_numeric_median(self):
        with tempfile.TemporaryDirectory() as d:
            out = charts.build_charts(
                [{"title": "empty", "rows": [{"group": "a", "median": None}]}], Path(d))
            self.assertEqual([c for c in out if c.get("png")], [])  # no chart drawn


class TableNormalizationTests(unittest.TestCase):
    def test_arbitrary_columns_render(self):
        # The exact shape that rendered as empty cells: a Discord table with no median/n.
        table = {"title": "Discord engagement", "group_by": "entity", "rows": [
            {"group": "Marvel Rivals", "online_n1": 1131011, "members_n1": 4416132,
             "online_rate_pct": 25.61, "active_voice_channels": "not measured (0 detected)"},
            {"group": "Delta Force", "online_n1": 106825, "members_n1": 768446,
             "online_rate_pct": 13.9, "active_voice_channels": 18}]}
        norm = normalize_table(table)
        self.assertEqual(norm["header"][0], "Entity")
        self.assertIn("online n1", norm["header"])
        self.assertIn("active voice channels", norm["header"])
        self.assertEqual(len(norm["body"]), 2)
        # values are formatted (1.1M) not blank
        self.assertTrue(any("M" in c or "," in c for c in norm["body"][0]))

    def test_chart_series_picks_a_numeric_column_without_median(self):
        table = {"title": "chatter", "rows": [
            {"group": "kick", "messages": 53, "substantive": 40},
            {"group": "web", "messages": 20, "substantive": 18}]}
        s = chart_series(table)
        self.assertIsNotNone(s)
        self.assertEqual(len(s["points"]), 2)

    def test_chart_series_none_when_no_numbers(self):
        self.assertIsNone(chart_series({"rows": [{"group": "a", "note": "x"}]}))


if __name__ == "__main__":
    unittest.main()
