import asyncio
import socket
import unittest
from unittest import mock

from mcp.client import Client

from research_engine import connectors
from research_engine.mcp_server import mcp
from research_engine.planner import build_plan


class PlannerTests(unittest.TestCase):
    def test_arbitrary_product_brief_is_not_gaming_specific(self):
        plan = build_plan(
            "Develop a Christmas 2026 campaign for Kirkland Italian sparkling mineral water",
            "Choose a differentiated creative territory", "United States", "Christmas 2026",
        )
        self.assertEqual("kirkland italian sparkling mineral water", plan["query_families"][0])
        self.assertEqual(["web", "rss", "x", "reddit"], plan["recommended_sources"])

    def test_gaming_brief_selects_relevant_community_sources(self):
        plan = build_plan("Understand creator sentiment around a new gaming launch")
        for source in ("twitch", "kick", "discord"):
            self.assertIn(source, plan["recommended_sources"])

    def test_user_can_require_sources_without_writing_queries(self):
        plan = build_plan(
            "Understand how owners discuss electric vehicle winter range",
            decision="Choose campaign proof points", market="Canada",
            required_sources=["discord", "twitch"], deliverable="Three messaging territories",
        )
        self.assertEqual(["discord", "twitch"], plan["recommended_sources"][:2])
        self.assertTrue(plan["source_queries"]["discord"])
        self.assertEqual("Three messaging territories", plan["deliverable"])
        self.assertNotIn("Which geography", " ".join(plan["clarifications"]))

    def test_too_short_brief_is_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "enough detail"):
            build_plan("water")


class RssCleaningTests(unittest.TestCase):
    def test_feed_html_is_reduced_to_readable_text(self):
        # Google News and many feeds put HTML (anchor tags, entities) in summaries;
        # the stored excerpt must be readable text, not raw markup.
        raw = '<a href="https://news.google.com/x">Spider-Man &amp; the ending</a>  <b>review</b>'
        self.assertEqual("Spider-Man & the ending review", connectors._plain_text(raw))
        self.assertEqual("", connectors._plain_text("<p></p>"))
        self.assertEqual("plain", connectors._plain_text("plain"))


class MCPTests(unittest.IsolatedAsyncioTestCase):
    class _Client:
        def __init__(self, *, get=None, post=None):
            self.get = mock.AsyncMock(side_effect=get) if isinstance(get, list) else mock.AsyncMock(return_value=get)
            self.post = mock.AsyncMock(side_effect=post) if isinstance(post, list) else mock.AsyncMock(return_value=post)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    @staticmethod
    def _response(payload, status=200):
        response = mock.Mock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status.side_effect = None if status < 400 else RuntimeError(str(status))
        return response

    async def test_tools_are_discoverable_through_mcp_client(self):
        async with Client(mcp) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
        self.assertEqual(
            {"create_research_job", "add_evidence", "fetch_rss", "search_x", "inspect_discord_invite",
             "run_apify_actor", "search_twitch", "search_kick", "read_discord_channel",
             "crawl_web_page", "get_research_dossier", "list_research_jobs", "connector_status",
             "get_browser_capture_mission", "export_research_report",
             "request_browser_traffic_session", "get_browser_traffic_session"},
            names,
        )

    async def test_unconfigured_connector_offers_a_next_door_instead_of_failing(self):
        store = mock.Mock()
        result = await connectors.search_x(store, 1, "", "sparkling water")
        self.assertTrue(result["not_configured"])
        self.assertEqual("x_official", result["connector"])
        self.assertTrue(result["setup"])
        self.assertTrue(result["fallbacks"])
        self.assertEqual(result["next_step"], result["fallbacks"][0])

    async def test_optional_connectors_always_offer_ordered_fallbacks(self):
        store = mock.Mock()
        cases = [
            (connectors.run_apify_actor(store, 1, "", "owner/actor", {}, "reddit"), "apify"),
            (connectors.search_twitch(store, 1, "", "", "cars"), "twitch"),
            (connectors.search_kick(store, 1, "", "", "cars"), "kick"),
            (connectors.read_discord_channel(store, 1, "", "123"), "discord_messages"),
        ]
        for coro, connector in cases:
            result = await coro
            self.assertTrue(result["not_configured"], connector)
            self.assertEqual(connector, result["connector"])
            self.assertTrue(result["fallbacks"], f"{connector} must offer at least one fallback")
            self.assertTrue(result["next_step"])

    def test_connector_status_is_derived_from_one_registry(self):
        from research_engine import mcp_server
        blank = mock.Mock(
            apify_token="", x_bearer_token="", twitch_client_id="", twitch_client_secret="",
            kick_client_id="", kick_client_secret="", discord_bot_token="",
        )
        with mock.patch.object(mcp_server, "settings", blank):
            status = mcp_server.connector_status()
        for connector in ("twitch", "kick", "apify", "discord_messages"):
            self.assertIn("ready", status[connector])
            self.assertFalse(status[connector]["ready"])
            self.assertTrue(status[connector]["fallbacks"], f"{connector} needs fallbacks in connector_status")

    def test_private_and_local_crawl_targets_are_rejected(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        with mock.patch.object(connectors.socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "Private"):
                connectors._validate_public_url("http://localhost/admin")
        with self.assertRaisesRegex(ValueError, "public http"):
            connectors._validate_public_url("file:///etc/passwd")

    def test_connector_status_does_not_return_secret_values(self):
        from research_engine import mcp_server
        configured = mock.Mock(
            apify_token="apify-secret", x_bearer_token="x-secret",
            twitch_client_id="id", twitch_client_secret="t-secret",
            kick_client_id="id", kick_client_secret="k-secret", discord_bot_token="d-secret",
        )
        with mock.patch.object(mcp_server, "settings", configured):
            status = mcp_server.connector_status()
        self.assertTrue(status["apify"]["ready"])
        rendered = str(status)
        for value in ("apify-secret", "x-secret", "t-secret", "k-secret", "d-secret"):
            self.assertNotIn(value, rendered)

    def test_research_prompt_covers_complete_agent_loop(self):
        from research_engine.mcp_server import research_assignment
        prompt = research_assignment("Research winter tires", "Choose a campaign claim")
        for behavior in ("create_research_job", "connector_status", "add_evidence", "get_research_dossier", "citations"):
            self.assertIn(behavior, prompt)

    async def test_generic_platform_connectors_store_successful_results(self):
        store = mock.Mock()
        store.add_evidence.return_value = {"evidence_id": 1, "collected_at": "now"}

        x_payload = {"data":[{"id":"1","author_id":"u","text":"battery anxiety","created_at":"2026-08-01T00:00:00Z","public_metrics":{}}],"includes":{"users":[{"id":"u","username":"driver"}]},"meta":{}}
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._response(x_payload))):
            result = await connectors.search_x(store, 1, "token", "battery anxiety", 25)
        self.assertEqual(1, result["matched"])
        self.assertEqual("https://x.com/driver/status/1", result["items"][0]["url"])

        twitch_rows = {"data":[{"broadcaster_login":"creator","display_name":"Creator","title":"Winter EV test","game_name":"Science & Technology","is_live":True}]}
        client = self._Client(post=self._response({"access_token":"token"}), get=self._response(twitch_rows))
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=client):
            result = await connectors.search_twitch(store, 1, "id", "secret", "winter EV")
        self.assertEqual(1, result["matched"])

        kick_rows = {"data":[{"id":"s","viewer_count":12,"stream_title":"Formula 1 talk","category":{"name":"Sports"},"channel":{"slug":"racer"}}],"pagination":{}}
        client = self._Client(post=self._response({"access_token":"token"}), get=self._response(kick_rows))
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=client):
            result = await connectors.search_kick(store, 1, "id", "secret", "Formula 1")
        self.assertEqual(1, result["matched"])

        discord_payload = {"guild":{"id":"g","name":"EV Owners"},"approximate_member_count":100,"approximate_presence_count":20}
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._response(discord_payload))):
            result = await connectors.inspect_discord_invite(store, 1, "https://discord.gg/evowners")
        self.assertEqual("EV Owners", result["guild_name"])


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        from db.queries import connect, migrate
        cls.database_url = os.getenv("DATABASE_URL", "postgresql:///gaming_pulse")
        try:
            with connect(cls.database_url) as conn:
                migrate(conn)
        except Exception as exc:  # pragma: no cover - environment-specific skip
            raise unittest.SkipTest(f"PostgreSQL is unavailable: {exc}") from exc

    def test_export_writes_report_and_raw_evidence_files(self):
        import csv
        import json
        import tempfile
        from pathlib import Path
        from research_engine.store import ResearchStore
        from research_engine.export import export_job

        store = ResearchStore(self.database_url)
        plan = build_plan("Export test brief for pipes and formulas", decision="ship it")
        job_id = store.create_job("Export test brief for pipes and formulas", "ship it", "", "", plan)["job_id"]
        store.add_evidence(job_id, source_type="reddit", url="https://reddit.com/r/x/1",
                           title="value | comparison", excerpt="=danger they said A | B at a third", author="user")

        with tempfile.TemporaryDirectory() as directory:
            result = export_job(store, job_id, Path(directory))
            folder = Path(result["folder"])
            names = {Path(f).name for f in result["files"]}
            self.assertEqual({"report.md", "report.html", "evidence.json", "evidence.csv", "raw_responses.jsonl"}, names)
            # HTML renders a table (the pipe in the excerpt did not break the row).
            self.assertIn("<table>", folder.joinpath("report.html").read_text())
            # CSV re-parses and the formula-injection guard prefixed the risky cell.
            with folder.joinpath("evidence.csv").open(newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(2, len(rows))
            self.assertTrue(rows[1][4].startswith("'="))
            # JSON round-trips and the gaps section carries setup instructions.
            self.assertIsInstance(json.loads(folder.joinpath("evidence.json").read_text()), list)
            self.assertIn("X_BEARER_TOKEN", folder.joinpath("report.md").read_text())

    def test_always_ready_source_gap_has_guidance_not_empty_heading(self):
        # F2 fix: an rss/web gap (no credentials) must render a guidance line, not a bare heading.
        import tempfile
        from pathlib import Path
        from research_engine.store import ResearchStore
        from research_engine.export import export_job, job_folder

        store = ResearchStore(self.database_url)
        plan = build_plan("Always-ready gap guidance regression brief", decision="ship")
        job_id = store.create_job("Always-ready gap guidance regression brief", "ship", "", "", plan)["job_id"]
        store.add_evidence(job_id, source_type="reddit", url="https://r/1", title="t", excerpt="only reddit", author="u")
        with tempfile.TemporaryDirectory() as directory:
            result = export_job(store, job_id, Path(directory))
            md = Path(result["folder"]).joinpath("report.md").read_text()
            i = md.find("### rss")
            self.assertNotEqual(-1, i)
            block = md[i:i + 80]
            self.assertIn("fetch_rss", block)  # guidance present, not an empty heading
            # job_folder returns the same path without writing when called separately
            self.assertEqual(Path(result["folder"]), job_folder(store, job_id, Path(directory)))


if __name__ == "__main__":
    unittest.main()
