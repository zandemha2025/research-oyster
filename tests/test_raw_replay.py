import unittest

import httpx

from collectors.base import _payload_kind, _redacted_headers, _redacted_url, _safe_response_content
from collectors.replay import REPLAY_REGISTRY


class RawResponseTests(unittest.TestCase):
    def test_secrets_are_redacted(self):
        url = httpx.URL("https://example.test/token?client_id=visible&client_secret=nope")
        self.assertNotIn("nope", _redacted_url(url))
        headers = _redacted_headers({"Authorization": "Bearer nope", "User-Agent": "pulse"})
        self.assertEqual(headers["Authorization"], "[REDACTED]")
        self.assertEqual(headers["User-Agent"], "pulse")

    def test_payload_classification(self):
        self.assertEqual(_payload_kind(httpx.URL("https://discord.com/api/v10/invites/test")), "discord_invite")
        self.assertEqual(_payload_kind(httpx.URL("https://api.twitch.tv/helix/streams")), "twitch_streams")

    def test_oauth_response_body_is_redacted_recursively(self):
        content = b'{"access_token":"secret","nested":{"refresh_token":"also-secret"},"expires_in":3600}'
        safe = _safe_response_content("oauth_token", content).decode()
        self.assertNotIn("secret", safe)
        self.assertIn("expires_in", safe)

    def test_press_replay_is_offline_and_normalized(self):
        xml = """<rss version="2.0"><channel><title>News</title><item>
          <title>Game update</title><link>https://example.test/story</link>
          <description>Details</description></item></channel></rss>"""
        result = REPLAY_REGISTRY["press_document"]({"body_text": xml})
        self.assertEqual(result[0]["headline"], "Game update")
        self.assertEqual(result[0]["url"], "https://example.test/story")


if __name__ == "__main__":
    unittest.main()
