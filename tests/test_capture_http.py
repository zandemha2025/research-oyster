from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import control_center
from db.queries import connect, migrate
from research_engine.planner import build_plan
from research_engine.store import ResearchStore
from settings import Settings


EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
ORIGIN = f"chrome-extension://{EXTENSION_ID}"


class CaptureHTTPAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = Settings().database_url
        try:
            with connect(cls.database_url) as conn:
                migrate(conn)
        except Exception as exc:
            raise unittest.SkipTest(f"PostgreSQL is unavailable: {exc}") from exc
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), control_center.Handler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        plan = build_plan(
            "Develop a Christmas 2026 campaign for Kirkland Italian sparkling mineral water",
            "Choose a differentiated creative territory", "United States", "Christmas 2026",
        )
        self.job_id = ResearchStore(self.database_url).create_job(
            plan["objective"], plan["decision"], plan["market"], plan["time_horizon"], plan,
        )["job_id"]
        code = self.request("POST", "/api/capture/pairing-code", {}, {"X-Pulse-Token": control_center.TOKEN})
        self.assertEqual(200, code[0])
        paired = self.request(
            "POST", "/api/capture/pair", {"code": code[1]["pairing_code"], "client_name": "HTTP Test"},
            {"Origin": ORIGIN},
        )
        self.assertEqual(200, paired[0])
        self.client_id, self.token = paired[1]["client_id"], paired[1]["token"]

    def tearDown(self):
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM research_jobs WHERE id=%s", (self.job_id,))
            cur.execute("DELETE FROM browser_capture_audit WHERE client_id=%s", (self.client_id,))
            cur.execute("DELETE FROM browser_clients WHERE id=%s", (self.client_id,))
            conn.commit()

    def request(self, method: str, path: str, payload: dict | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        value = json.loads(raw) if raw else None
        result = response.status, value, dict(response.getheaders())
        connection.close()
        return result

    def auth(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "Authorization": f"Bearer {self.token}"}

    def test_extension_flow_feeds_capture_into_original_dossier(self):
        jobs = self.request("GET", "/api/capture/jobs", headers=self.auth())
        self.assertEqual(200, jobs[0])
        self.assertIn(self.job_id, [job["id"] for job in jobs[1]])
        self.assertEqual(ORIGIN, jobs[2]["Access-Control-Allow-Origin"])

        mission = self.request("GET", f"/api/capture/jobs/{self.job_id}/mission", headers=self.auth())
        self.assertIn("Kirkland", mission[1]["brief"])
        question = mission[1]["research_questions"][2]
        payload = {
            "job_id": self.job_id, "source_type": "discord_supervised",
            "url": "https://discord.com/channels/1/2/3?utm_source=test#message",
            "page_title": "Costco Fans — holiday-hosting",
            "excerpt": "Participant 1: Glass bottles look nicer on our Christmas table.",
            "research_question": question, "capture_mode": "supervised", "anonymized": True,
            "client_capture_id": "http-flow-1", "captured_at": "2026-08-01T12:00:00Z",
        }
        denied = self.request("POST", "/api/capture/approve", {**payload, "approved_by_user": False}, self.auth())
        self.assertEqual(400, denied[0])
        approved = self.request("POST", "/api/capture/approve", {**payload, "approved_by_user": True}, self.auth())
        self.assertEqual("approved", approved[1]["status"])
        retried = self.request("POST", "/api/capture/approve", {**payload, "approved_by_user": True}, self.auth())
        self.assertTrue(retried[1]["idempotent"])
        self.assertEqual(approved[1]["evidence_id"], retried[1]["evidence_id"])

        dossier = ResearchStore(self.database_url).dossier(self.job_id)
        evidence = next(item for item in dossier["evidence"] if item["id"] == approved[1]["evidence_id"])
        self.assertEqual("discord", evidence["source_type"])
        self.assertEqual(question, evidence["metadata"]["research_question"])
        self.assertEqual("2026-08-01T12:00:00+00:00", evidence["metadata"]["captured_at"])
        self.assertNotIn("utm_source", evidence["url"])
        deleted = self.request("DELETE", f"/api/capture/evidence/{approved[1]['capture_id']}", headers=self.auth())
        self.assertTrue(deleted[1]["deleted"])
        self.assertEqual([], ResearchStore(self.database_url).dossier(self.job_id)["evidence"])

    def test_hostile_origin_and_revoked_token_are_rejected(self):
        hostile = self.request("GET", "/api/capture/jobs", headers={
            "Origin": "https://attacker.example", "Authorization": f"Bearer {self.token}",
        })
        self.assertEqual(403, hostile[0])
        revoked = self.request("POST", "/api/capture/revoke", {}, self.auth())
        self.assertEqual(200, revoked[0])
        after = self.request("GET", "/api/capture/jobs", headers=self.auth())
        self.assertEqual(403, after[0])

    def test_host_header_and_rate_limits_fail_closed(self):
        wrong_host = self.request("GET", "/api/capture/jobs", headers={
            **self.auth(), "Host": "attacker.example",
        })
        self.assertEqual(403, wrong_host[0])
        key = "unit-rate-key"
        with control_center.RATE_LOCK:
            control_center.RATE_EVENTS.pop(("unit", key), None)
        control_center.enforce_rate("unit", key, 2)
        control_center.enforce_rate("unit", key, 2)
        with self.assertRaisesRegex(PermissionError, "Too many"):
            control_center.enforce_rate("unit", key, 2)

    def test_traffic_session_requires_approval_then_accepts_network_capture(self):
        from research_engine.capture import CaptureStore
        session = CaptureStore(self.database_url).request_session(self.job_id, "discord.com", "collect chatter")

        # The researcher sees the request in the extension-facing list.
        listed = self.request("GET", "/api/capture/sessions", headers=self.auth())
        self.assertEqual(200, listed[0])
        self.assertIn(session["id"], [s["id"] for s in listed[1]])

        # Approval requires explicit user consent.
        denied = self.request("POST", f"/api/capture/sessions/{session['id']}/approve", {"approved_by_user": False}, self.auth())
        self.assertEqual(400, denied[0])
        approved = self.request("POST", f"/api/capture/sessions/{session['id']}/approve", {"approved_by_user": True, "ttl_minutes": 30}, self.auth())
        self.assertEqual(200, approved[0])
        self.assertEqual(session["id"], approved[1]["session_id"])

        # A network capture under the approved session is accepted and promoted.
        payload = {
            "job_id": self.job_id, "source_type": "discord_supervised",
            "url": "https://discord.com/channels/1/2/3", "excerpt": "bob: the movie was great",
            "capture_mode": "approved_session", "anonymized": True, "client_capture_id": "net-http-1",
            "metadata": {"session_id": session["id"], "network": {
                "url": "https://discord.com/api/messages", "method": "GET", "status": 200,
                "content_type": "application/json", "body": '[{"content":"the movie was great","author":{"username":"bob"}}]'}},
        }
        saved = self.request("POST", "/api/capture/approve", {**payload, "approved_by_user": True}, self.auth())
        self.assertEqual("approved", saved[1]["status"])
        raws = ResearchStore(self.database_url).list_raw_responses(self.job_id)
        self.assertEqual(["browser_network"], [r["payload_kind"] for r in raws])

        # The control-center user can stop the session with the page token.
        stopped = self.request("POST", f"/api/research/sessions/{session['id']}/stop", {}, {"X-Pulse-Token": control_center.TOKEN})
        self.assertEqual(200, stopped[0])
        after = self.request("POST", "/api/capture/approve", {**payload, "approved_by_user": True, "client_capture_id": "net-http-2"}, self.auth())
        self.assertEqual(403, after[0])

    def test_standing_consent_trusts_domain_and_auto_approves(self):
        # Trusting a domain requires explicit user consent.
        denied = self.request("POST", "/api/capture/trusted-domains", {"domain": "discord.com"}, self.auth())
        self.assertEqual(400, denied[0])
        trusted = self.request("POST", "/api/capture/trusted-domains", {"domain": "discord.com", "approved_by_user": True}, self.auth())
        self.assertEqual(201, trusted[0])
        listed = self.request("GET", "/api/capture/trusted-domains", None, self.auth())
        self.assertIn("discord.com", [d["domain"] for d in listed[1]])

        # A later session request on the trusted domain auto-approves without another click.
        from research_engine.capture import CaptureStore
        session = CaptureStore(self.database_url).request_session(self.job_id, "discord.com", "reactions")
        auto = self.request("POST", "/api/capture/sessions/auto-approve", {}, self.auth())
        self.assertEqual(200, auto[0])
        self.assertIn("discord.com", [a["domain"] for a in auto[1]["approved"]])
        self.assertEqual("approved", CaptureStore(self.database_url).session_status(session["id"])["status"])

        # Untrusting stops future auto-approval.
        removed = self.request("DELETE", "/api/capture/trusted-domains/discord.com", None, self.auth())
        self.assertTrue(removed[1]["removed"])
        self.assertEqual([], self.request("GET", "/api/capture/trusted-domains", None, self.auth())[1])

    def test_human_start_endpoint_requires_consent_and_returns_approved_session(self):
        denied = self.request("POST", "/api/capture/sessions/start", {"job_id": self.job_id, "domain": "discord.com"}, self.auth())
        self.assertEqual(400, denied[0])
        started = self.request("POST", "/api/capture/sessions/start",
                               {"job_id": self.job_id, "domain": "discord.com", "approved_by_user": True}, self.auth())
        self.assertEqual(201, started[0])
        self.assertEqual("discord.com", started[1]["domain"])
        from research_engine.capture import CaptureStore
        self.assertEqual("approved", CaptureStore(self.database_url).session_status(started[1]["session_id"])["status"])


if __name__ == "__main__":
    unittest.main()
