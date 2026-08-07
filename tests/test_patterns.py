from __future__ import annotations

import unittest

from research_engine import patterns


def _msg(text, author="a", source="kick_chat", channel="xqc"):
    return {"source_type": source, "author": author, "excerpt": text,
            "metadata": {"channel": channel}}


class AnonymizeTests(unittest.TestCase):
    def test_pseudonym_is_stable_and_hides_handle(self):
        p1 = patterns.pseudonymize("BigStreamerFan", salt="job7")
        p2 = patterns.pseudonymize("bigstreamerfan", salt="job7")  # case-insensitive
        self.assertEqual(p1, p2)
        self.assertTrue(p1.startswith("user_"))
        self.assertNotIn("streamer", p1.lower())

    def test_salt_separates_jobs(self):
        self.assertNotEqual(patterns.pseudonymize("x", "job1"), patterns.pseudonymize("x", "job2"))

    def test_empty_author_is_anon(self):
        self.assertEqual(patterns.pseudonymize(""), "anon")


class SignalTests(unittest.TestCase):
    def test_substantive_vs_noise(self):
        ev = [_msg("the onboarding flow is genuinely confusing for new users"),  # substantive
              _msg("[emote:1:KEKW]"), _msg("LOL"), _msg("gg")]                    # noise
        sig = patterns.signal_ratio(ev)
        self.assertEqual(sig["total_with_text"], 4)
        self.assertEqual(sig["substantive"], 1)
        self.assertAlmostEqual(sig["ratio"], 0.25)


class PatternTests(unittest.TestCase):
    def test_top_terms_counts_recurring_phrases(self):
        ev = [_msg("the pricing is too expensive for what it is"),
              _msg("pricing feels expensive honestly"),
              _msg("expensive pricing again wow")]
        terms = {t["term"]: t["count"] for t in patterns.top_terms(ev, min_count=2)}
        self.assertGreaterEqual(terms.get("pricing", 0), 2)
        self.assertGreaterEqual(terms.get("expensive", 0), 2)

    def test_top_voices_pseudonymized_and_counted(self):
        ev = [_msg("real content here about the topic", author="loud")] * 3 + \
             [_msg("another substantive comment on it", author="quiet")]
        voices = patterns.top_voices(ev, salt="j")
        self.assertTrue(voices[0]["speaker"].startswith("user_"))
        self.assertEqual(voices[0]["messages"], 3)

    def test_by_group_splits_channels(self):
        ev = [_msg("substantive one two three words", channel="general"),
              _msg("substantive four five six words", channel="support"),
              _msg("substantive seven eight nine words", channel="support")]
        groups = {g["group"]: g for g in patterns.by_group(ev, group_by="channel")}
        self.assertEqual(groups["support"]["substantive"], 2)
        self.assertEqual(groups["general"]["substantive"], 1)


class SufficiencyTests(unittest.TestCase):
    def test_abstains_on_metadata_only_evidence(self):
        # Landscape rows (Discord member counts) have no text → must not fabricate a verdict.
        ev = [{"source_type": "discord", "metadata": {"metrics": {"members": 4000000}}}]
        out = patterns.assess_sufficiency(ev)
        self.assertFalse(out["assessable"])
        self.assertTrue(out["enough"])  # abstain = don't block

    def test_flags_thin_sample_as_collect_more(self):
        ev = [_msg("a genuinely substantive message number %d here now" % i) for i in range(5)]
        out = patterns.assess_sufficiency(ev, min_substantive=20)
        self.assertTrue(out["assessable"])
        self.assertFalse(out["enough"])
        self.assertEqual(out["verdict"], "collect more")
        self.assertTrue(any("substantive" in r for r in out["reasons"]))

    def test_high_volume_counts_as_sufficient(self):
        # 60 varied substantive messages → volume alone clears the 3x floor.
        ev = [_msg(f"users keep asking about integration option number {i} and workflow detail {i}")
              for i in range(60)]
        out = patterns.assess_sufficiency(ev, min_substantive=20)
        self.assertTrue(out["enough"])


if __name__ == "__main__":
    unittest.main()
