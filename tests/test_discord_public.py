from __future__ import annotations

import unittest

from research_engine.connectors import _discord_invite_codes


class DiscordInviteExtractTests(unittest.TestCase):
    def test_extracts_from_discord_gg_and_invite_urls(self):
        text = ("check out https://discord.gg/python and "
                "https://discord.com/invite/VVWspYKHTN plus discordapp.com/invite/abc123")
        self.assertEqual(_discord_invite_codes(text), ["python", "VVWspYKHTN", "abc123"])

    def test_dedupes_preserving_order(self):
        text = "discord.gg/aaa discord.gg/bbb discord.gg/aaa"
        self.assertEqual(_discord_invite_codes(text), ["aaa", "bbb"])

    def test_ignores_plain_text_without_invites(self):
        self.assertEqual(_discord_invite_codes("no invites here, just discord talk"), [])

    def test_respects_limit(self):
        text = " ".join(f"discord.gg/code{i}" for i in range(30))
        self.assertEqual(len(_discord_invite_codes(text, limit=5)), 5)


if __name__ == "__main__":
    unittest.main()
