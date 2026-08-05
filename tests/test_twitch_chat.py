from __future__ import annotations

import unittest

from research_engine.connectors import _parse_irc_line


class TwitchIrcParserTests(unittest.TestCase):
    """The IRC line parser is the fragile part of anonymous Twitch chat reading; the socket
    loop is standard. These cover the real line shapes Twitch sends."""

    def test_plain_privmsg(self):
        line = ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #xqc :the movie was incredible"
        self.assertEqual(_parse_irc_line(line), {"user": "viewer", "text": "the movie was incredible"})

    def test_privmsg_with_ircv3_tags(self):
        line = "@badge-info=;color=#FF0000;display-name=Fan :fan!fan@fan.tmi.twitch.tv PRIVMSG #dune :best part two ever"
        self.assertEqual(_parse_irc_line(line), {"user": "fan", "text": "best part two ever"})

    def test_message_with_colon_inside(self):
        line = ":u!u@u.tmi.twitch.tv PRIVMSG #c :timestamp 12:30 was the best scene"
        self.assertEqual(_parse_irc_line(line), {"user": "u", "text": "timestamp 12:30 was the best scene"})

    def test_ping_is_flagged_for_pong(self):
        self.assertEqual(_parse_irc_line("PING :tmi.twitch.tv"), {"ping": ":tmi.twitch.tv"})

    def test_non_chat_lines_ignored(self):
        for line in (
            ":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!",
            ":justinfan123!justinfan123@justinfan123.tmi.twitch.tv JOIN #xqc",
            "",
            ":tmi.twitch.tv CAP * ACK :twitch.tv/tags",
        ):
            self.assertIsNone(_parse_irc_line(line))


if __name__ == "__main__":
    unittest.main()
