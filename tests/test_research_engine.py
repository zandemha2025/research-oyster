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
             "crawl_web_page", "get_research_dossier", "list_research_jobs", "connector_status"},
            names,
        )

    async def test_x_without_token_explains_the_fix(self):
        store = mock.Mock()
        with self.assertRaisesRegex(ValueError, "X_BEARER_TOKEN"):
            await connectors.search_x(store, 1, "", "sparkling water")

    async def test_optional_connectors_explain_onboarding(self):
        store = mock.Mock()
        with self.assertRaisesRegex(ValueError, "APIFY_TOKEN"):
            await connectors.run_apify_actor(store, 1, "", "owner/actor", {}, "reddit")
        with self.assertRaisesRegex(ValueError, "Client ID"):
            await connectors.search_twitch(store, 1, "", "", "cars")
        with self.assertRaisesRegex(ValueError, "DISCORD_BOT_TOKEN"):
            await connectors.read_discord_channel(store, 1, "", "123")

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


if __name__ == "__main__":
    unittest.main()
