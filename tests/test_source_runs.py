from __future__ import annotations

import os
import unittest

from db.queries import connect, migrate
from research_engine.export import export_job
from research_engine.store import ResearchStore

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql:///gaming_pulse")


class SourceRunLedgerTests(unittest.TestCase):
    """The source-run ledger is what stops the report from laundering a failed source
    into 'not available'. It must record every attempt's real outcome, expose it in the
    dossier, and surface it in the exported report."""

    @classmethod
    def setUpClass(cls):
        try:
            with connect(DATABASE_URL) as conn:
                migrate(conn)
        except Exception as exc:  # pragma: no cover - environment-specific skip
            raise unittest.SkipTest(f"PostgreSQL is unavailable: {exc}") from exc

    def setUp(self):
        self.store = ResearchStore(DATABASE_URL)
        self.job_id = self.store.create_job("Ledger test brief", "ship", "US", "now", {})["job_id"]

    def test_dossier_records_each_outcome_distinctly(self):
        # The key distinction: a source that RAN and returned nothing ('empty') is not the
        # same as one that was never configured ('not_configured').
        self.store.record_source_run(self.job_id, "web_search", "q", outcome="ok", yield_count=8)
        self.store.record_source_run(self.job_id, "x", "q", outcome="empty", yield_count=0)
        self.store.record_source_run(self.job_id, "reddit_search", "q", outcome="rate_limited",
                                     http_status=403, yield_count=0, error_text="HTTP 403")
        self.store.record_source_run(self.job_id, "twitch", "q", outcome="not_configured", yield_count=0)

        runs = self.store.dossier(self.job_id)["source_runs"]
        by_connector = {r["connector"]: r for r in runs}
        self.assertEqual(by_connector["web_search"]["outcome"], "ok")
        self.assertEqual(by_connector["web_search"]["yield_count"], 8)
        self.assertEqual(by_connector["x"]["outcome"], "empty")  # ran, returned zero — a failure
        self.assertEqual(by_connector["reddit_search"]["outcome"], "rate_limited")
        self.assertEqual(by_connector["reddit_search"]["http_status"], 403)
        self.assertEqual(by_connector["twitch"]["outcome"], "not_configured")

    def test_export_surfaces_the_collection_log(self):
        self.store.record_source_run(self.job_id, "x", "q", outcome="empty", yield_count=0)
        self.store.add_evidence(self.job_id, source_type="web", url="https://example.com/a",
                                title="A", excerpt="something", query="q")
        self.store.save_synthesis(self.job_id, {
            "executive_answer": "An answer.", "themes": [], "tensions": "", "sentiment": "",
            "recommendations": [], "confidence": "", "limitations": "X ran but returned nothing.",
        })
        result = export_job(self.store, self.job_id, os.getenv("OYSTER_TEST_OUT", "output"))
        report = open(os.path.join(result["folder"], "report.md"), encoding="utf-8").read()
        self.assertIn("data collection log", report.lower())
        self.assertIn("not \"not available\"", report)  # the honest-framing note is present
        self.assertRegex(report, r"\|\s*x\s*\|.*\|\s*empty\s*\|")  # the empty X attempt is logged


if __name__ == "__main__":
    unittest.main()
