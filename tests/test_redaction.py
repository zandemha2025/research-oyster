from __future__ import annotations

import unittest

from collectors.base import redacted_headers, SECRET_KEYS


class RedactionTests(unittest.TestCase):
    def test_prefixed_and_variant_secret_headers_are_redacted(self):
        # These leaked before: the regex was anchored and missed x-api-key / Client-Id.
        h = redacted_headers({
            "Authorization": "Bearer abc", "X-Api-Key": "sk-secret", "Client-Id": "twitch123",
            "Client-Secret": "shh", "Cookie": "sid=1", "Content-Type": "application/json",
            "User-Agent": "oyster", "X-Request-Id": "r-1",
        })
        for leaky in ("Authorization", "X-Api-Key", "Client-Id", "Client-Secret", "Cookie"):
            self.assertEqual(h[leaky], "[REDACTED]", leaky)
        # Non-secret headers are preserved.
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertEqual(h["User-Agent"], "oyster")
        self.assertEqual(h["X-Request-Id"], "r-1")

    def test_secret_keys_matches_anywhere_not_just_anchored(self):
        self.assertTrue(SECRET_KEYS.search("x-api-key"))
        self.assertTrue(SECRET_KEYS.search("Client-Id"))
        self.assertFalse(SECRET_KEYS.search("content-type"))


if __name__ == "__main__":
    unittest.main()
