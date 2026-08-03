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


if __name__ == "__main__":
    unittest.main()
