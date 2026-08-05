from __future__ import annotations

import os
import unittest

from db.queries import connect, migrate
from research_engine.capture import CaptureStore, allowed_local_origin
from research_engine.planner import build_plan
from research_engine.store import ResearchStore


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql:///gaming_pulse")
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


class CaptureOriginTests(unittest.TestCase):
    def test_only_local_ui_or_exact_paired_extension_is_allowed(self):
        self.assertTrue(allowed_local_origin("http://127.0.0.1"))
        self.assertTrue(allowed_local_origin(f"chrome-extension://{EXTENSION_ID}", EXTENSION_ID))
        self.assertFalse(allowed_local_origin("https://attacker.example", EXTENSION_ID))
        self.assertFalse(allowed_local_origin("chrome-extension://pppppppppppppppppppppppppppppppp", EXTENSION_ID))


class CaptureBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with connect(DATABASE_URL) as conn:
                migrate(conn)
        except Exception as exc:  # pragma: no cover - environment-specific skip
            raise unittest.SkipTest(f"PostgreSQL is unavailable: {exc}") from exc

    def setUp(self):
        self.store = CaptureStore(DATABASE_URL)
        self.plan = build_plan(
            "Develop a Christmas 2026 campaign for Kirkland Italian sparkling mineral water",
            "Choose a differentiated creative territory", "United States", "Christmas 2026",
        )
        plan = self.plan
        self.job_id = ResearchStore(DATABASE_URL).create_job(
            plan["objective"], plan["decision"], plan["market"], plan["time_horizon"], plan,
        )["job_id"]
        pairing = self.store.create_pairing_code()
        self.pairing_code = pairing["pairing_code"]
        paired = self.store.exchange_pairing_code(
            self.pairing_code, client_name="Test Chrome", extension_id=EXTENSION_ID,
        )
        self.client_id, self.token = paired["client_id"], paired["token"]

    def tearDown(self):
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM research_jobs WHERE id=%s", (self.job_id,))
            cur.execute("DELETE FROM browser_capture_audit WHERE client_id=%s", (self.client_id,))
            cur.execute("DELETE FROM browser_clients WHERE id=%s", (self.client_id,))
            cur.execute("DELETE FROM browser_pairing_codes WHERE code_hash IS NOT NULL AND created_at > now() - interval '1 hour'")
            conn.commit()

    def _submit(self, **overrides):
        values = {
            "job_id": self.job_id,
            "source_type": "discord_supervised",
            "url": "https://discord.com/channels/1/2/3",
            "excerpt": "We buy glass bottles for Christmas dinner because they look nicer.",
            "page_title": "Costco Fans — holiday-hosting",
            "author": "visible-user",
            "research_question": self.plan["research_questions"][0],
            "researcher_note": "Potential holiday table ritual",
            "capture_mode": "supervised",
            "metadata": {"server": "Costco Fans", "channel": "holiday-hosting"},
        }
        values.update(overrides)
        return self.store.submit(self.client_id, **values)

    def test_pairing_code_is_single_use_and_auth_checks_origin(self):
        client = self.store.authenticate(self.token, origin=f"chrome-extension://{EXTENSION_ID}")
        self.assertEqual(self.client_id, client["client_id"])
        with self.assertRaises(PermissionError):
            self.store.authenticate(self.token, origin="https://attacker.example")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.store.exchange_pairing_code(self.pairing_code, client_name="Replay", extension_id=EXTENSION_ID)

    def test_revoke_immediately_invalidates_token_and_submission(self):
        self.store.revoke_client(self.client_id)
        with self.assertRaises(PermissionError):
            self.store.authenticate(self.token)
        with self.assertRaises(PermissionError):
            self._submit()

    def test_mission_is_derived_from_original_job_plan(self):
        mission = self.store.mission(self.job_id)
        self.assertEqual(self.job_id, mission["job_id"])
        self.assertIn("Kirkland", mission["brief"])
        self.assertGreaterEqual(len(mission["research_questions"]), 3)
        self.assertIn("kirkland", mission["look_for"])

    def test_pending_capture_approval_promotes_anonymized_evidence_and_audits(self):
        capture_id = self._submit()["capture_id"]
        pending = self.store.list_pending(self.client_id, self.job_id)
        self.assertEqual([capture_id], [item["id"] for item in pending])
        self.assertRegex(pending[0]["author"], r"^Participant [0-9a-f]{8}$")
        self.assertNotIn("visible-user", str(pending[0]))

        result = self.store.review(capture_id, client_id=self.client_id, approve=True)
        self.assertEqual("approved", result["status"])
        self.assertIsInstance(result["evidence_id"], int)
        dossier = ResearchStore(DATABASE_URL).dossier(self.job_id)
        evidence = next(item for item in dossier["evidence"] if item["id"] == result["evidence_id"])
        self.assertRegex(evidence["author"], r"^Participant [0-9a-f]{8}$")
        self.assertTrue(evidence["metadata"]["supervised_capture"])
        self.assertEqual("discord", evidence["source_type"])
        self.assertEqual(capture_id, evidence["metadata"]["capture_id"])
        self.assertEqual(["capture.submitted", "capture.approved"], [event["event_type"] for event in self.store.audit_log(capture_id)])
        with self.assertRaisesRegex(ValueError, "already been approved"):
            self.store.review(capture_id, client_id=self.client_id, approve=True)

    def test_reject_does_not_add_evidence(self):
        capture_id = self._submit(anonymize=False)["capture_id"]
        result = self.store.review(capture_id, client_id=self.client_id, approve=False)
        self.assertEqual({"capture_id": capture_id, "status": "rejected", "evidence_id": None}, result)
        self.assertEqual([], ResearchStore(DATABASE_URL).dossier(self.job_id)["evidence"])

    def test_bulk_reject_is_scoped_to_job_and_optional_client(self):
        self._submit(excerpt="First visible finding")
        self._submit(excerpt="Second visible finding")
        self.assertEqual(2, self.store.reject_pending(self.job_id, self.client_id)["rejected"])
        self.assertEqual([], self.store.list_pending(self.client_id, self.job_id))

    def test_capture_input_limits_and_url_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "http or https"):
            self._submit(url="javascript:alert(1)")
        with self.assertRaisesRegex(ValueError, "between 1"):
            self._submit(excerpt="")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self._submit(capture_mode="silent_forever")
        with self.assertRaisesRegex(ValueError, "belong"):
            self._submit(research_question="An invented question")

    def test_url_secrets_fragments_and_tracking_are_removed(self):
        capture_id = self._submit(
            url="https://user:password@example.com/thread?id=7&utm_source=spy&fbclid=tracking#message",
        )["capture_id"]
        item = self.store.list_pending(self.client_id, self.job_id)[0]
        self.assertEqual(capture_id, item["id"])
        self.assertEqual("https://example.com/thread?id=7", item["url"])

    def test_review_queue_is_not_visible_or_mutable_by_another_client(self):
        pairing = self.store.create_pairing_code()
        other = self.store.exchange_pairing_code(
            pairing["pairing_code"], client_name="Other Chrome", extension_id="pppppppppppppppppppppppppppppppp",
        )
        try:
            capture_id = self._submit()["capture_id"]
            self.assertEqual([], self.store.list_pending(other["client_id"], self.job_id))
            with self.assertRaisesRegex(ValueError, "not found"):
                self.store.review(capture_id, client_id=other["client_id"], approve=True)
            self.assertEqual(0, self.store.reject_pending(self.job_id, other["client_id"])["rejected"])
            self.assertEqual(1, len(self.store.list_pending(self.client_id, self.job_id)))
        finally:
            with connect(DATABASE_URL) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM browser_capture_audit WHERE client_id=%s", (other["client_id"],))
                cur.execute("DELETE FROM browser_clients WHERE id=%s", (other["client_id"],))
                conn.commit()

    def test_inactive_job_rejects_new_capture(self):
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("UPDATE research_jobs SET status='complete' WHERE id=%s", (self.job_id,))
            conn.commit()
        with self.assertRaisesRegex(ValueError, "active"):
            self._submit()

    def test_review_and_mission_reject_job_that_became_inactive(self):
        capture_id = self._submit()["capture_id"]
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("UPDATE research_jobs SET status='complete' WHERE id=%s", (self.job_id,))
            conn.commit()
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.review(capture_id, client_id=self.client_id, approve=True)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.mission(self.job_id)

    def test_expired_pending_content_is_purged(self):
        capture_id = self._submit()["capture_id"]
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("UPDATE browser_captures SET expires_at=now() - interval '1 second' WHERE id=%s", (capture_id,))
            conn.commit()
        self.assertEqual([], self.store.list_pending(self.client_id, self.job_id))
        self.assertEqual("capture.expired", self.store.audit_log(capture_id)[-1]["event_type"])

    def test_anonymized_author_is_never_persisted_and_review_scrubs_staging(self):
        capture_id = self._submit()["capture_id"]
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT original_author FROM browser_captures WHERE id=%s", (capture_id,))
            self.assertIsNone(cur.fetchone()["original_author"])
        self.store.review(capture_id, client_id=self.client_id, approve=False)
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT excerpt,original_author,researcher_note,source_metadata FROM browser_captures WHERE id=%s", (capture_id,))
            row = cur.fetchone()
        self.assertEqual("[rejected]", row["excerpt"])
        self.assertIsNone(row["original_author"])
        self.assertIsNone(row["researcher_note"])
        self.assertEqual({}, row["source_metadata"])

    def test_source_family_comes_from_hostname_not_client_label(self):
        capture_id = self._submit(
            source_type="discord_supervised", url="https://x.com/someone/status/1",
        )["capture_id"]
        self.assertEqual("x", self.store.list_pending(self.client_id, self.job_id)[0]["source_type"])
        result = self.store.review(capture_id, client_id=self.client_id, approve=True)
        evidence = next(e for e in ResearchStore(DATABASE_URL).dossier(self.job_id)["evidence"] if e["id"] == result["evidence_id"])
        self.assertEqual("x", evidence["source_type"])
        self.assertEqual("discord_supervised", evidence["metadata"]["submitted_source_type"])

    def test_captured_at_and_client_key_reach_evidence_and_direct_approval_is_idempotent(self):
        payload = {
            "job_id": self.job_id, "source_type": "anything", "url": "https://www.reddit.com/r/Costco/comments/1",
            "excerpt": "Glass bottles make the holiday table feel special.", "page_title": "Holiday hosting",
            "author": "shopper", "anonymize": True, "capture_mode": "manual",
            "captured_at": "2026-08-01T12:00:00Z",
        }
        first = self.store.approve_direct(self.client_id, client_capture_id="capture-device-123", **payload)
        second = self.store.approve_direct(self.client_id, client_capture_id="capture-device-123", **payload)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        evidence = next(e for e in ResearchStore(DATABASE_URL).dossier(self.job_id)["evidence"] if e["id"] == first["evidence_id"])
        self.assertEqual("capture-device-123", evidence["metadata"]["client_capture_id"])
        self.assertEqual("2026-08-01T12:00:00+00:00", evidence["metadata"]["captured_at"])
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT excerpt,original_author,source_metadata FROM browser_captures WHERE id=%s", (first["capture_id"],))
            staging = cur.fetchone()
        self.assertEqual("[promoted to evidence]", staging["excerpt"])
        self.assertIsNone(staging["original_author"])
        self.assertEqual({}, staging["source_metadata"])

    def test_delete_capture_removes_promoted_evidence_and_is_client_scoped(self):
        approved = self.store.approve_direct(
            self.client_id, client_capture_id="delete-me", job_id=self.job_id, source_type="x",
            url="https://x.com/user/status/9", excerpt="Delete this evidence", anonymize=True,
        )
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.delete_capture(approved["capture_id"], self.client_id + 99999)
        deleted = self.store.delete_capture(approved["capture_id"], self.client_id)
        self.assertTrue(deleted["deleted"])
        self.assertEqual([], ResearchStore(DATABASE_URL).dossier(self.job_id)["evidence"])
        self.assertEqual("capture.deleted", self.store.audit_log(approved["capture_id"])[-1]["event_type"])

    # --- Approved-session traffic capture -------------------------------------------

    def _network_capture(self, session_id, url="https://discord.com/channels/1/2/3", key="net-1"):
        body = '[{"content":"the movie was great","author":{"username":"bob"}}]'
        return self.store.approve_direct(
            self.client_id, client_capture_id=key, job_id=self.job_id,
            url=url, excerpt="bob: the movie was great", capture_mode="approved_session",
            metadata={"session_id": session_id, "network": {
                "url": "https://discord.com/api/v9/channels/1/messages?token=SECRET",
                "method": "GET", "status": 200, "content_type": "application/json", "body": body}},
        )

    def test_traffic_session_request_validates_domain_and_rejects_duplicates(self):
        for bad in ("localhost", "127.0.0.1", "http://discord.com/x", "discord.com:443"):
            with self.assertRaises(ValueError):
                self.store.request_session(self.job_id, bad, "collect chatter")
        session = self.store.request_session(self.job_id, "discord.com", "collect movie chatter")
        self.assertEqual("requested", session["status"])
        with self.assertRaisesRegex(ValueError, "already open"):
            self.store.request_session(self.job_id, "discord.com", "again")

    def test_approved_session_gate_blocks_capture_until_approved_then_promotes_raw(self):
        session = self.store.request_session(self.job_id, "discord.com", "collect movie chatter")
        with self.assertRaisesRegex(PermissionError, "No approved capture session"):
            self._network_capture(session["id"])
        self.store.approve_session(session["id"], self.client_id, ttl_minutes=30)
        result = self._network_capture(session["id"])
        self.assertEqual("approved", result["status"])
        research = ResearchStore(DATABASE_URL)
        evidence = next(e for e in research.dossier(self.job_id)["evidence"] if e["id"] == result["evidence_id"])
        # Evidence keeps a bounded summary; the raw body is not stored on the evidence row.
        self.assertEqual(200, evidence["metadata"]["network"]["status"])
        self.assertNotIn("body", evidence["metadata"]["network"])
        raws = research.list_raw_responses(self.job_id)
        self.assertEqual([("browser.approved_session", "browser_network")],
                         [(r["collector"], r["payload_kind"]) for r in raws])
        # The secret query param is redacted in the stored raw payload.
        self.assertNotIn("SECRET", raws[0]["request_url"])

    def test_approved_session_rejects_offdomain_and_stopped_sessions(self):
        session = self.store.request_session(self.job_id, "discord.com", "collect chatter")
        self.store.approve_session(session["id"], self.client_id, ttl_minutes=30)
        with self.assertRaisesRegex(PermissionError, "No approved capture session"):
            self._network_capture(session["id"], url="https://evil.com/x", key="off-domain")
        self.store.stop_session(session["id"])
        with self.assertRaisesRegex(PermissionError, "No approved capture session"):
            self._network_capture(session["id"], key="after-stop")

    def test_expired_session_is_not_active(self):
        session = self.store.request_session(self.job_id, "discord.com", "collect chatter")
        self.store.approve_session(session["id"], self.client_id, ttl_minutes=30)
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("UPDATE capture_sessions SET expires_at=now() - interval '1 minute' WHERE id=%s", (session["id"],))
            conn.commit()
        self.assertIsNone(self.store.active_session(self.client_id, self.job_id, "discord.com"))
        with self.assertRaisesRegex(PermissionError, "No approved capture session"):
            self._network_capture(session["id"], key="expired")

    # --- Standing per-domain consent -------------------------------------------------

    def test_trusted_domain_auto_approves_only_matching_sessions(self):
        # Validation: bare public domains only.
        for bad in ("localhost", "1.2.3.4", "http://discord.com/x"):
            with self.assertRaises(ValueError):
                self.store.trust_domain(self.client_id, bad)
        self.store.trust_domain(self.client_id, "discord.com")
        self.assertEqual(["discord.com"], [d["domain"] for d in self.store.list_trusted_domains(self.client_id)])
        # Subdomain matches the trust; unrelated host does not.
        self.assertIsNotNone(self.store.is_domain_trusted(self.client_id, "gateway.discord.com"))
        self.assertIsNone(self.store.is_domain_trusted(self.client_id, "evil.com"))

        trusted = self.store.request_session(self.job_id, "discord.com", "reactions")
        untrusted = self.store.request_session(self.job_id, "x.com", "reactions")
        approved = self.store.auto_approve_for_client(self.client_id)
        self.assertEqual(["discord.com"], [a["domain"] for a in approved])
        self.assertEqual("approved", self.store.session_status(trusted["id"])["status"])
        self.assertEqual("requested", self.store.session_status(untrusted["id"])["status"])
        # The auto-approval is auditable as standing consent.
        with connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT details FROM browser_capture_audit WHERE event_type='session.approved' AND client_id=%s ORDER BY id DESC LIMIT 1", (self.client_id,))
            self.assertTrue(cur.fetchone()["details"].get("standing_consent"))

    def test_untrusting_a_domain_stops_auto_approval(self):
        self.store.trust_domain(self.client_id, "discord.com")
        self.assertTrue(self.store.untrust_domain(self.client_id, "discord.com")["removed"])
        self.assertEqual([], self.store.list_trusted_domains(self.client_id))
        self.store.request_session(self.job_id, "discord.com", "reactions")
        self.assertEqual([], self.store.auto_approve_for_client(self.client_id))

    def test_trust_requires_a_live_client(self):
        self.store.revoke_client(self.client_id)
        with self.assertRaises(PermissionError):
            self.store.trust_domain(self.client_id, "discord.com")

    def test_start_session_creates_and_approves_in_one_step(self):
        # The human-initiated on-switch: one call yields an approved session ready to record.
        result = self.store.start_session(self.client_id, self.job_id, "discord.com")
        self.assertEqual("discord.com", result["domain"])
        self.assertIn("session_id", result)
        self.assertEqual("approved", self.store.session_status(result["session_id"])["status"])
        # A capture under it is accepted immediately.
        self._network_capture(result["session_id"], key="human-start")
        self.assertEqual("discord",
                         [e for e in ResearchStore(DATABASE_URL).dossier(self.job_id)["evidence"]][0]["source_type"])


if __name__ == "__main__":
    unittest.main()
