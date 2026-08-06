import asyncio
import socket
import unittest
from unittest import mock

from mcp.client import Client

from research_engine import connectors
from research_engine.mcp_server import mcp
from research_engine.planner import build_plan


class PlannerTests(unittest.TestCase):
    def test_planner_carries_no_hardcoded_topic_questions_or_query_templates(self):
        # The dumb regex planner is gone: no canned per-brief questions, no query_families,
        # no source_queries. Intelligence is the model's job.
        plan = build_plan(
            "Develop a Christmas 2026 campaign for Kirkland Italian sparkling mineral water",
            "Choose a differentiated creative territory", "United States", "Christmas 2026",
        )
        self.assertNotIn("query_families", plan)
        self.assertNotIn("source_queries", plan)
        # Fallback questions are generic meta-questions, never topic-specific canned ones.
        joined = " ".join(plan["research_questions"]).lower()
        self.assertNotIn("campaign", joined)
        self.assertIn("answer", joined)

    def test_model_authored_questions_and_entities_are_honored(self):
        plan = build_plan(
            "What do fans think of the new Spider-Man movie",
            questions=["Is the sentiment net positive?", "What do people dislike?"],
            entities=["Spider-Man", "Tom Holland"], angles=["fan reaction", "critic reaction"],
        )
        self.assertEqual(["Is the sentiment net positive?", "What do people dislike?"], plan["research_questions"])
        self.assertEqual(["Spider-Man", "Tom Holland"], plan["entities_and_terms"])
        self.assertEqual(["fan reaction", "critic reaction"], plan["angles"])

    def test_planner_does_not_force_irrelevant_platforms(self):
        # Discovery decides where the conversation is; the planner only offers a light hint.
        plan = build_plan("Understand creator sentiment around a new gaming launch")
        self.assertEqual(["web", "reddit"], plan["recommended_sources"])
        self.assertNotIn("twitch", plan["recommended_sources"])

    def test_required_sources_are_honored_and_clarifications_track_missing_fields(self):
        plan = build_plan(
            "Understand how owners discuss electric vehicle winter range",
            decision="Choose campaign proof points", market="Canada",
            required_sources=["discord"], deliverable="Three messaging territories",
        )
        self.assertEqual("discord", plan["recommended_sources"][0])
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
             "run_apify_actor", "apify_collect", "search_twitch", "read_twitch_chat", "search_kick", "read_discord_channel",
             "crawl_web_page", "search_web", "discover_sources", "search_reddit", "fetch_reddit_thread",
             "get_research_dossier", "list_research_jobs", "connector_status",
             "list_metric_fields", "compute_metric", "compute_rate",
             "write_research_synthesis", "export_research_report",
             "get_browser_capture_mission",
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

    def test_research_prompt_drives_a_real_investigation_ending_in_a_synthesis(self):
        from research_engine.mcp_server import research_assignment
        prompt = research_assignment("Research winter tires", "Choose a campaign claim")
        # The method must decompose, discover, collect real quotes, and author an answer.
        for behavior in ("create_research_job", "discover_sources", "add_evidence",
                         "write_research_synthesis", "export_research_report", "executive"):
            self.assertIn(behavior, prompt)
        # It must not teach the old cop-out: reporting gaps as the deliverable.
        self.assertNotIn("coverage gaps", prompt.lower())

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


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    class _Client:
        def __init__(self, *, get=None, post=None):
            self.get = mock.AsyncMock(side_effect=get) if isinstance(get, list) else mock.AsyncMock(return_value=get)
            self.post = mock.AsyncMock(side_effect=post) if isinstance(post, list) else mock.AsyncMock(return_value=post)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    @staticmethod
    def _json(payload, status=200):
        response = mock.Mock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status.side_effect = None if status < 400 else RuntimeError(str(status))
        return response

    @staticmethod
    def _text(text, status=200):
        response = mock.Mock(status_code=status, text=text)
        response.raise_for_status.side_effect = None if status < 400 else RuntimeError(str(status))
        return response

    async def test_search_web_prefers_a_configured_provider_and_parses_it(self):
        store = mock.Mock()
        tavily = {"results": [{"title": "T", "url": "https://a.test/1", "content": "snip"}]}
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(post=self._json(tavily))):
            result = await connectors.search_web(store, 1, "spider-man reactions", tavily_key="k")
        self.assertEqual("tavily", result["provider"])
        self.assertEqual("https://a.test/1", result["results"][0]["url"])
        self.assertEqual("snip", result["results"][0]["snippet"])

    async def test_search_web_falls_back_to_free_duckduckgo_and_unwraps_redirects(self):
        store = mock.Mock()
        html = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freddit.com%2Fr%2Fmovies%2F1&amp;rut=x">'
            'Reddit thread</a>'
            '<a class="result__snippet">people loved it</a>'
        )
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._text(html))):
            result = await connectors.search_web(store, 1, "spider-man")
        self.assertEqual("duckduckgo", result["provider"])
        self.assertEqual("https://reddit.com/r/movies/1", result["results"][0]["url"])
        self.assertEqual("Reddit thread", result["results"][0]["title"])
        self.assertEqual("people loved it", result["results"][0]["snippet"])

    async def test_discover_sources_groups_leads_by_venue(self):
        store = mock.Mock()
        leads = {"results": [
            {"title": "r/movies", "url": "https://www.reddit.com/r/movies/1", "snippet": ""},
            {"title": "clip", "url": "https://youtube.com/watch?v=1", "snippet": ""},
            {"title": "review", "url": "https://variety.com/spiderman", "snippet": ""},
        ]}
        with mock.patch.object(connectors, "search_web", mock.AsyncMock(return_value=leads)):
            result = await connectors.discover_sources(store, 1, "spider-man")
        self.assertIn("reddit", result["venues"])
        self.assertIn("youtube", result["venues"])
        self.assertIn("news_or_web", result["venues"])
        self.assertEqual(3, result["total_leads"])  # deduped across the 4 probes

    async def test_search_reddit_stores_posts_as_evidence(self):
        store = mock.Mock()
        store.add_evidence.return_value = {"evidence_id": 1, "collected_at": "now"}
        payload = {"data": {"children": [
            {"data": {"title": "Loved it", "selftext": "best marvel movie", "permalink": "/r/movies/comments/1/loved/",
                      "author": "fan", "subreddit": "movies", "score": 120, "num_comments": 30, "created_utc": 1700000000}}
        ]}}
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._json(payload))):
            result = await connectors.search_reddit(store, 1, "spider-man")
        self.assertEqual(1, result["matched"])
        self.assertEqual("https://www.reddit.com/r/movies/comments/1/loved/", result["items"][0]["url"])
        store.add_evidence.assert_called_once()
        self.assertEqual("reddit", store.add_evidence.call_args.kwargs["source_type"])

    async def test_search_reddit_surfaces_rate_limiting_clearly(self):
        store = mock.Mock()
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._json({}, status=429))):
            with self.assertRaisesRegex(ValueError, "429"):
                await connectors.search_reddit(store, 1, "spider-man")

    async def test_fetch_reddit_thread_stores_post_and_real_comments(self):
        store = mock.Mock()
        store.add_evidence.return_value = {"evidence_id": 1, "collected_at": "now"}
        post = {"data": {"children": [{"data": {"title": "Thread", "selftext": "post body",
                "permalink": "/r/movies/comments/1/thread/", "author": "op", "subreddit": "movies",
                "score": 50, "num_comments": 2, "created_utc": 1700000000}}]}}
        comments = {"data": {"children": [
            {"kind": "t1", "data": {"author": "a1", "body": "the villain stole the show", "score": 9, "created_utc": 1700000001}},
            {"kind": "t1", "data": {"author": "a2", "body": "[deleted]", "score": 0}},
        ]}}
        with mock.patch.object(connectors.httpx, "AsyncClient", return_value=self._Client(get=self._json([post, comments]))):
            result = await connectors.fetch_reddit_thread(store, 1, "https://www.reddit.com/r/movies/comments/1/thread/")
        self.assertTrue(result["post_stored"])
        self.assertEqual(1, result["comments_stored"])  # [deleted] is skipped
        # post + one real comment = two evidence rows
        self.assertEqual(2, store.add_evidence.call_count)

    async def test_fetch_reddit_thread_rejects_non_reddit_urls(self):
        store = mock.Mock()
        with self.assertRaisesRegex(ValueError, "reddit.com"):
            await connectors.fetch_reddit_thread(store, 1, "https://example.com/thread")


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

    def test_export_leads_with_the_authored_answer_and_has_no_gaps_section(self):
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
        store.save_synthesis(job_id, {
            "executive_answer": "People overwhelmingly prefer the glass-bottle format for gifting.",
            "themes": [{"title": "Gifting appeal", "insight": "Glass reads as premium.",
                        "citations": [{"quote": "Glass bottles look nicer on the table",
                                       "url": "https://reddit.com/r/x/1", "source": "r/x"}]}],
            "tensions": "Price sensitivity cuts against the premium read.",
            "sentiment": "Broadly positive.",
            "recommendations": ["Lead with the glass bottle in holiday creative."],
            "confidence": "Moderate — one illustrative source in this test.",
            "limitations": "Private groups were out of scope.",
        })

        with tempfile.TemporaryDirectory() as directory:
            result = export_job(store, job_id, Path(directory))
            folder = Path(result["folder"])
            names = {Path(f).name for f in result["files"]}
            # The core report files are always present...
            self.assertLessEqual({"report.md", "report.html", "evidence.json", "evidence.csv",
                                  "raw_responses.jsonl"}, names)
            # ...alongside the polished deliverable package (no metrics here, so no charts).
            self.assertLessEqual({"report.docx", "deck.pptx", "Sources-and-Citations.md",
                                  "README-start-here.md"}, names)
            self.assertEqual(result["warnings"], [])
            md = folder.joinpath("report.md").read_text()
            # The report LEADS with the executive answer, before any evidence.
            self.assertLess(md.index("## Executive answer"), md.index("## Appendix"))
            self.assertIn("prefer the glass-bottle format", md)
            self.assertIn("Glass bottles look nicer on the table", md)  # a real quoted citation
            # The cop-out is gone.
            self.assertNotIn("Gaps and how to unlock", md)
            # HTML renders a table (the pipe in the appendix excerpt did not break the row).
            self.assertIn("<table>", folder.joinpath("report.html").read_text())
            # CSV re-parses and the formula-injection guard prefixed the risky cell.
            with folder.joinpath("evidence.csv").open(newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(2, len(rows))
            self.assertTrue(rows[1][4].startswith("'="))
            self.assertIsInstance(json.loads(folder.joinpath("evidence.json").read_text()), list)

    def test_export_refuses_to_dump_evidence_without_a_synthesis(self):
        import tempfile
        from pathlib import Path
        from research_engine.store import ResearchStore
        from research_engine.export import export_job, job_folder

        store = ResearchStore(self.database_url)
        plan = build_plan("Unsynthesized export must be refused, not dumped", decision="ship")
        job_id = store.create_job("Unsynthesized export must be refused, not dumped", "ship", "", "", plan)["job_id"]
        store.add_evidence(job_id, source_type="reddit", url="https://r/1", title="t", excerpt="only reddit", author="u")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "write_research_synthesis"):
                export_job(store, job_id, Path(directory))
            # job_folder still computes a path without writing or requiring a synthesis.
            self.assertEqual(directory, str(job_folder(store, job_id, Path(directory)).parent))
            self.assertIn(f"research-job-{job_id}-", job_folder(store, job_id, Path(directory)).name)


if __name__ == "__main__":
    unittest.main()
