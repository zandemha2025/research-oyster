from __future__ import annotations

import unittest

from research_engine import apify
from research_engine.connectors import _apify_reason, _apify_soft_fail


class ApifyGracefulDegradationTests(unittest.TestCase):
    """A rejected/expired/credit-exhausted key must degrade to fallbacks, never raise — this is what
    keeps a bad key from killing a live demo run."""

    def test_reason_maps_common_failures_to_plain_english(self):
        self.assertIn("rejected", _apify_reason(401).lower())
        self.assertIn("rejected", _apify_reason(403).lower())
        self.assertIn("credit", _apify_reason(402).lower())
        self.assertIn("rate", _apify_reason(429).lower())
        self.assertIn("not found", _apify_reason(404).lower())

    def test_soft_fail_carries_fallbacks_and_next_step(self):
        r = _apify_soft_fail(_apify_reason(401), 401)
        self.assertTrue(r["apify_unavailable"])
        self.assertEqual(r["status"], 401)
        self.assertTrue(r["fallbacks"])            # the host is handed somewhere to go
        self.assertTrue(r["next_step"])            # ...and a concrete first move
        self.assertNotIn("Traceback", r["error"])  # never a raw exception


class NumericExtractionTests(unittest.TestCase):
    """The value of the Apify layer is turning messy actor rows into clean numbers. These use
    real-shaped rows per platform."""

    def test_tiktok_row(self):
        row = {"authorMeta": {"name": "user1"}, "playCount": 50000, "diggCount": 1200,
               "commentCount": 80, "shareCount": 30}
        m = apify.extract_metrics(row)
        self.assertEqual(m["views"], 50000)
        self.assertEqual(m["likes"], 1200)
        self.assertEqual(m["comments"], 80)
        self.assertEqual(m["shares"], 30)
        self.assertEqual(apify.pick_entity(row), "user1")

    def test_x_nested_public_metrics(self):
        row = {"author": {"userName": "brianonhere"},
               "public_metrics": {"like_count": 23, "reply_count": 4, "retweet_count": 2}}
        m = apify.extract_metrics(row)
        self.assertEqual(m["likes"], 23)
        self.assertEqual(m["comments"], 4)   # reply_count -> comments
        self.assertEqual(m["shares"], 2)     # retweet_count -> shares
        self.assertEqual(apify.pick_entity(row), "brianonhere")

    def test_instagram_row(self):
        row = {"ownerUsername": "betches", "videoViewCount": 77102, "likesCount": 5000, "commentsCount": 100}
        m = apify.extract_metrics(row)
        self.assertEqual(m["views"], 77102)
        self.assertEqual(m["likes"], 5000)
        self.assertEqual(apify.pick_entity(row), "betches")

    def test_reddit_row(self):
        row = {"parsedCommunityName": "dune", "score": 738, "num_comments": 919}
        m = apify.extract_metrics(row)
        self.assertEqual(m["score"], 738)
        self.assertEqual(m["comments"], 919)
        self.assertEqual(apify.pick_entity(row), "dune")

    def test_followers(self):
        self.assertEqual(apify.extract_metrics({"username": "x", "followersCount": 8700000})["followers"], 8700000)

    def test_booleans_and_missing_are_ignored(self):
        self.assertEqual(apify.extract_metrics({"isLive": True, "verified": False, "title": "hi"}), {})


class WindowTests(unittest.TestCase):
    NOW = 1_800_000_000.0  # fixed reference

    def test_recent_kept_old_excluded(self):
        recent = {"createTime": self.NOW - 10 * 86400}          # 10 days ago
        old = {"createTime": self.NOW - 200 * 86400}            # 200 days ago
        self.assertTrue(apify.within_window(recent, 90, now=self.NOW))
        self.assertFalse(apify.within_window(old, 90, now=self.NOW))

    def test_millisecond_timestamps(self):
        row = {"timestamp": (self.NOW - 5 * 86400) * 1000}      # ms
        self.assertTrue(apify.within_window(row, 90, now=self.NOW))

    def test_iso_string_date(self):
        row = {"createdAt": "2026-08-01T00:00:00Z"}
        # far future 'now' makes it stale; near makes it fresh
        self.assertTrue(apify.within_window(row, 3650, now=self.NOW))

    def test_no_date_is_kept(self):
        self.assertTrue(apify.within_window({"text": "no date here"}, 90, now=self.NOW))


class RegistryTests(unittest.TestCase):
    def test_resolve_known_platforms(self):
        self.assertEqual(apify.resolve("tiktok").actor, "clockworks/tiktok-scraper")
        self.assertEqual(apify.resolve("instagram", "hashtag").actor, "apify/instagram-scraper")
        self.assertEqual(apify.resolve("x").actor, "apidojo/tweet-scraper")
        self.assertIsNone(apify.resolve("myspace"))

    def test_discord_is_opt_in(self):
        self.assertTrue(apify.resolve("discord", "messages").opt_in)

    def test_build_input_clamps_limit(self):
        spec = apify.resolve("tiktok")
        got = apify.build_input(spec, "dune part two", 9999, 90)
        self.assertEqual(got["resultsPerPage"], apify.MAX_LIMIT)
        self.assertIn("dune part two", got["searchQueries"])

    def test_normalize_row_shape(self):
        out = apify.normalize_row({"authorMeta": {"name": "u"}, "playCount": 10}, query_shape="q")
        self.assertEqual(out["metrics"]["views"], 10)
        self.assertEqual(out["entity"], "u")
        self.assertEqual(out["query_shape"], "q")
        self.assertIn("record", out)


if __name__ == "__main__":
    unittest.main()
