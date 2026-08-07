from __future__ import annotations

import unittest

from research_engine import patterns


def _msg(text, author="a", source="kick_chat", channel="xqc"):
    return {"source_type": source, "author": author, "excerpt": text,
            "metadata": {"channel": channel}}


class ConsensusWeightingTests(unittest.TestCase):
    """Weight evidence by the people behind it, not by mention count. A 1k-upvote comment is a poll,
    not an anecdote."""

    def test_engagement_read_from_every_metadata_shape(self):
        reddit = {"metadata": {"upvotes": 1200, "num_comments": 340}}          # top-level (Reddit path)
        apify_metrics = {"metadata": {"metrics": {"likes": 50, "shares": 9}}}  # metrics dict
        apify_record = {"metadata": {"record": {"playCount": 90000, "diggCount": 800}}}  # raw actor row
        self.assertEqual(patterns.engagement_of(reddit)["upvotes"], 1200)
        self.assertEqual(patterns.engagement_of(reddit)["comments"], 340)
        self.assertEqual(patterns.engagement_of(apify_metrics)["upvotes"], 50)   # likes -> upvotes
        self.assertEqual(patterns.engagement_of(apify_metrics)["shares"], 9)
        self.assertEqual(patterns.engagement_of(apify_record)["views"], 90000)
        self.assertEqual(patterns.engagement_of(apify_record)["upvotes"], 800)   # diggCount -> upvotes

    def test_one_high_endorsement_row_outranks_many_obscure_ones(self):
        ev = [{"source_type": "reddit", "metadata": {"upvotes": 2000}}]
        ev += [{"source_type": "web", "metadata": {"upvotes": 5}} for _ in range(20)]
        c = patterns.weighted_consensus(ev, group_by="source_type")
        self.assertEqual(c["ranked"][0]["group"], "reddit")          # the 2k-upvote voice leads
        self.assertGreater(c["ranked"][0]["weight"], c["ranked"][1]["weight"])
        self.assertEqual(c["total_endorsement"]["upvotes"], 2100)

    def test_shares_outweigh_upvotes_and_dislikes_subtract(self):
        shares = patterns._conviction_score({"shares": 100})
        upvotes = patterns._conviction_score({"upvotes": 100})
        self.assertGreater(shares, upvotes)                          # a share is stronger than an upvote
        self.assertLess(patterns._conviction_score({"upvotes": 100, "dislikes": 100}),
                        patterns._conviction_score({"upvotes": 100}))  # dislikes pull it down

    def test_views_are_dampened_not_dominant(self):
        # 1,000,000 views should NOT swamp a solid upvote signal the way linear counting would
        views = patterns._conviction_score({"views": 1_000_000})
        upvotes = patterns._conviction_score({"upvotes": 5000})
        self.assertLess(views, upvotes)

    def test_uniform_zero_field_flagged_as_tooling_artifact(self):
        # The SGF 2026 signature: a scraper returns comment counts but score=0 for the whole batch.
        ev = [{"source_type": "reddit", "metadata": {"upvotes": 0, "num_comments": 40 + i}}
              for i in range(6)]
        suspects = patterns.detect_uniform_zero(ev)
        self.assertTrue(any(s["field"] == "upvotes" and s["platform"] == "reddit" for s in suspects))
        # and the consensus surfaces it with a warning + a suspect_fields list
        c = patterns.weighted_consensus(ev, group_by="source_type")
        self.assertTrue(c["suspect_fields"])
        self.assertIn("DATA-QUALITY WARNING", c["note"])

    def test_genuine_engagement_is_not_flagged(self):
        # Real upvotes present -> no false alarm
        ev = [{"source_type": "reddit", "metadata": {"upvotes": 100 + i, "num_comments": 5}}
              for i in range(6)]
        self.assertEqual(patterns.detect_uniform_zero(ev), [])
        self.assertEqual(patterns.weighted_consensus(ev)["suspect_fields"], [])


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
