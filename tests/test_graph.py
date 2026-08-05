from __future__ import annotations

import asyncio
import unittest

from research_engine import graph


class GraphLogicTests(unittest.TestCase):
    def test_detect_platforms(self):
        self.assertEqual(
            set(graph.detect_platforms("what are people on Discord and Twitch saying")),
            {"discord", "twitch"},
        )
        self.assertEqual(graph.detect_platforms("general vibe check"), [])

    def test_default_lanes_include_named_platforms(self):
        state = graph.ResearchState(brief="x", named_platforms=["discord", "twitch"])
        lanes = graph.default_lanes(state)
        self.assertIn("discord", lanes)
        self.assertIn("twitch", lanes)
        self.assertIn("reddit", lanes)  # workhorse always present
        self.assertIn("web", lanes)

    def test_review_flags_uncovered_named_platform(self):
        state = graph.ResearchState(brief="x", named_platforms=["twitch", "discord"], round=1)
        dossier = {
            "source_runs": [{"connector": "reddit_search"}],  # twitch/discord never attempted
            "synthesis": {"executive_answer": "a", "themes": [{"citations": [{"url": "u"}]}]},
            "evidence": [1, 2, 3, 4],
        }
        verdict, gaps = graph.review_gate(state, dossier)
        self.assertEqual(verdict, "needs_more")
        self.assertTrue(any("twitch" in g for g in gaps))
        self.assertTrue(any("discord" in g for g in gaps))

    def test_review_passes_when_covered_and_backed(self):
        state = graph.ResearchState(brief="x", named_platforms=["twitch"], round=1)
        dossier = {
            "source_runs": [{"connector": "twitch_chat"}, {"connector": "reddit_search"}],
            "synthesis": {"executive_answer": "a", "themes": [{"citations": [{"url": "u"}]}]},
            "evidence": [1, 2, 3, 4],
        }
        self.assertEqual(graph.review_gate(state, dossier)[0], "pass")

    def test_review_forces_pass_at_max_rounds(self):
        state = graph.ResearchState(brief="x", named_platforms=["twitch"], round=graph.MAX_ROUNDS)
        dossier = {"source_runs": [], "synthesis": {}, "evidence": []}  # everything wrong
        self.assertEqual(graph.review_gate(state, dossier)[0], "pass")  # but capped, so it stops


class GraphRunTests(unittest.TestCase):
    def test_run_graph_loops_then_exports(self):
        calls = []
        # dossier is thin on round 1 (→ needs_more), rich on round 2 (→ pass)
        rounds = {"n": 0}

        async def run_node(node_id, instruction, timeout):
            calls.append(node_id)
            return {"job_id": 42 if node_id == "plan" else None, "is_error": False,
                    "produced": True, "session_id": None, "text": "", "cost_usd": None}

        def get_dossier(job_id):
            rounds["n"] += 1
            if rounds["n"] == 1:
                return {"source_runs": [], "synthesis": {"executive_answer": "a", "themes": []}, "evidence": []}
            return {"source_runs": [{"connector": "reddit_search"}],
                    "synthesis": {"executive_answer": "a", "themes": [{"citations": [{"url": "u"}]}]},
                    "evidence": [1, 2, 3, 4]}

        events = []
        asyncio.run(graph.run_graph("what do people say about X", events.append, run_node, get_dossier))

        self.assertEqual(calls.count("plan"), 1)
        self.assertEqual(calls.count("discover"), 1)
        self.assertEqual(calls.count("synthesize"), 2)   # looped once
        self.assertEqual(calls.count("export"), 1)       # exported after review passed
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("collect:")), 2)
        self.assertTrue(any(e.get("type") == "graph_init" for e in events))

    def test_run_graph_aborts_if_plan_makes_no_job(self):
        async def run_node(node_id, instruction, timeout):
            return {"job_id": None, "is_error": True, "produced": False}

        events = []
        asyncio.run(graph.run_graph("x", events.append, run_node, lambda j: {}))
        self.assertTrue(any(e.get("type") == "error" for e in events))


if __name__ == "__main__":
    unittest.main()
