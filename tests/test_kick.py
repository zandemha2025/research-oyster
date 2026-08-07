from __future__ import annotations

import unittest

import json

from research_engine.connectors import _kick_extract, _parse_kick_event


class KickExtractTests(unittest.TestCase):
    def test_channel_endpoint_shape_live(self):
        # The v2/channels/{slug} shape: livestream nested with viewer_count + categories.
        payload = {"slug": "xqc", "followers_count": 1098999,
                   "livestream": {"viewer_count": 7326, "session_title": "LIVE",
                                  "categories": [{"name": "Minecraft"}]}}
        m = _kick_extract(payload)
        self.assertEqual(m["slug"], "xqc")
        self.assertEqual(m["followers"], 1098999)
        self.assertTrue(m["is_live"])
        self.assertEqual(m["viewers"], 7326)
        self.assertEqual(m["category"], "Minecraft")

    def test_search_shape_offline_uses_flat_flags(self):
        # The /api/search channels shape: flat isLive + recentCategories, no livestream object.
        payload = {"slug": "tabbyminecraft", "followersCount": 2703, "isLive": False,
                   "recentCategories": [{"name": "Minecraft"}]}
        m = _kick_extract(payload)
        self.assertEqual(m["slug"], "tabbyminecraft")
        self.assertEqual(m["followers"], 2703)   # tolerates followersCount alias
        self.assertFalse(m["is_live"])
        self.assertIsNone(m["viewers"])          # offline → no viewer count
        self.assertEqual(m["category"], "Minecraft")

    def test_missing_numbers_and_slug_fallback(self):
        m = _kick_extract({"user": {"username": "someone"}}, slug="")
        self.assertEqual(m["slug"], "someone")   # falls back to user.username
        self.assertIsNone(m["followers"])
        self.assertFalse(m["is_live"])

    def test_numbers_are_coerced_to_int(self):
        m = _kick_extract({"slug": "x", "followers_count": "1500",
                           "livestream": {"viewer_count": "42"}})
        self.assertEqual(m["followers"], 1500)
        self.assertEqual(m["viewers"], 42)


class KickChatParseTests(unittest.TestCase):
    def test_parses_chat_message_event(self):
        frame = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                            "data": json.dumps({"content": "gg wp", "created_at": "2026-08-07T00:00:00Z",
                                                "sender": {"username": "viewer1"}})})
        self.assertEqual(_parse_kick_event(frame),
                         {"user": "viewer1", "text": "gg wp", "created_at": "2026-08-07T00:00:00Z"})

    def test_ping_frame(self):
        self.assertEqual(_parse_kick_event(json.dumps({"event": "pusher:ping", "data": {}})), {"ping": True})

    def test_ignores_subscription_ack_and_junk(self):
        self.assertIsNone(_parse_kick_event(json.dumps(
            {"event": "pusher_internal:subscription_succeeded", "data": "{}"})))
        self.assertIsNone(_parse_kick_event("not json at all"))

    def test_ignores_empty_content_or_missing_user(self):
        blank = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                            "data": json.dumps({"content": "  ", "sender": {"username": "u"}})})
        nouser = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                             "data": json.dumps({"content": "hi", "sender": {}})})
        self.assertIsNone(_parse_kick_event(blank))
        self.assertIsNone(_parse_kick_event(nouser))


if __name__ == "__main__":
    unittest.main()
