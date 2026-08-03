import asyncio
from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import ValidationError

import main
from collectors import kick, press, twitch
from collectors.base import HTTPClient, RunLog, _collection_mode
from reporting.ondemand import render_signal_brief
from reporting.render import _counted, _to_html
from settings import Settings
from tests.test_collectors_acceptance import FakeConnection, response


class SettingsStories(unittest.TestCase):
    def test_required_database_and_numeric_bounds(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValidationError):
            Settings(_env_file=None)
        with self.assertRaises(ValidationError):
            Settings(database_url="postgresql://test", collection_hour_utc=24, _env_file=None)


class CLIStories(unittest.TestCase):
    def test_cli_01_help_lists_workflows(self):
        help_text = main.parser().format_help()
        for command in ("migrate", "collect", "report", "pulse", "replay"):
            self.assertIn(command, help_text)

    def test_cli_02_single_source(self):
        args = main.parser().parse_args(["collect", "discord"])
        self.assertEqual((args.command, args.source), ("collect", "discord"))

    def test_cli_03_all_sources_contain_failures(self):
        settings = MagicMock()

        async def exercise():
            with patch.object(main, "run_collector", new=AsyncMock(side_effect=[None, RuntimeError("down"), None, None])) as run:
                failures = await main.run_collectors(["discord", "twitch", "kick", "press"], object(), settings)
            self.assertEqual(run.await_count, 4)
            self.assertEqual(failures, ["twitch: down"])

        asyncio.run(exercise())

    def test_cli_04_missing_credentials(self):
        settings = MagicMock(twitch_client_id="", twitch_client_secret="", kick_client_id="", kick_client_secret="")
        with self.assertRaisesRegex(SystemExit, "TWITCH_CLIENT_ID"):
            asyncio.run(main.run_collector("twitch", object(), settings))

    def test_cli_05_report_requires_monday(self):
        self.assertEqual(main.week_start("2026-08-03"), date(2026, 8, 3))
        with self.assertRaisesRegex(Exception, "Monday"):
            main.week_start("2026-08-04")

    def test_cli_06_pulse_source_validation(self):
        self.assertEqual(main.parse_sources("discord,twitch,discord"), ["discord", "twitch"])
        with self.assertRaisesRegex(Exception, "at least one"):
            main.parse_sources("")
        with self.assertRaisesRegex(Exception, "Unknown"):
            main.parse_sources("discord,other")

    def test_cli_07_press_hours_positive(self):
        self.assertEqual(main.positive_int("24"), 24)
        for value in ("0", "-1"):
            with self.assertRaises(Exception):
                main.positive_int(value)


class HTTPStories(unittest.TestCase):
    def test_http_01_explicit_timeout(self):
        client = HTTPClient(timeout=12.5, max_retries=0)
        try:
            self.assertEqual(client.client.timeout.connect, 12.5)
            self.assertEqual(client.client.timeout.read, 12.5)
        finally:
            asyncio.run(client.close())


class RunLogStories(unittest.TestCase):
    def test_run_02_fatal_propagates(self):
        conn = FakeConnection()
        with self.assertRaisesRegex(RuntimeError, "fatal"):
            with RunLog(conn, "discord"):
                raise RuntimeError("fatal")
        self.assertEqual(conn.run_updates[-1][0], "failed")


class StreamEdgeStories(unittest.IsolatedAsyncioTestCase):
    async def test_twi_03_dedup_and_null(self):
        conn = FakeConnection()
        fake = AsyncMock()
        repeated = {"id":"same", "game_id":"1", "game_name":"Game", "viewer_count":None, "user_name":"x"}
        fake.request.side_effect = [
            response(payload={"access_token":"token"}),
            response(payload={"data":[repeated],"pagination":{"cursor":"c"}}),
            response(payload={"data":[repeated],"pagination":{}}),
            response(payload={"data":[{"id":"1","name":"Game"}]}),
        ]
        fake.close = AsyncMock()
        with patch.object(twitch, "HTTPClient", return_value=fake):
            await twitch.collect(conn,"id","secret",pages=2)
        self.assertEqual(conn.category_snapshots[0][3:7], (0,1,"x",0))

    async def test_kic_01_hosts(self):
        self.assertEqual(kick.TOKEN_URL, "https://id.kick.com/oauth/token")
        self.assertEqual(kick.API_URL, "https://api.kick.com/public/v2")

    async def test_kic_03_dedup_and_null(self):
        conn = FakeConnection()
        fake = AsyncMock()
        repeated = {"id":"same", "category":{"id":1,"name":"Game"}, "viewer_count":None, "channel":{"slug":"x"}}
        fake.request.side_effect = [
            response(payload={"access_token":"token"}),
            response(payload={"data":[repeated],"pagination":{"next_cursor":"c"}}),
            response(payload={"data":[repeated],"pagination":{}}),
        ]
        fake.close = AsyncMock()
        with patch.object(kick, "HTTPClient", return_value=fake):
            await kick.collect(conn,"id","secret",pages=2)
        self.assertEqual(conn.category_snapshots[0][3:7], (0,1,"x",0))


class PressStories(unittest.IsolatedAsyncioTestCase):
    async def test_prs_02_unavailable_contained(self):
        conn = FakeConnection(sources=[{"id":1,"slug":"missing","status":"unavailable","endpoint_url":"https://missing.test","source_class":"news"}])
        fake = AsyncMock()
        fake.request.return_value = response(404)
        fake.close = AsyncMock()
        with patch.object(press,"HTTPClient",return_value=fake):
            await press.collect(conn)
        self.assertEqual(conn.run_updates[-1][0], "failed")

    async def test_prs_04_unmatched_retained(self):
        source = {"id":1,"slug":"paper","status":"active","endpoint_url":"https://paper.test/feed","source_class":"news"}
        conn = FakeConnection(sources=[source],titles=[{"slug":"known","aliases":["Known Game"]}])
        rss = b'<rss><channel><item><title>Other release</title><link>https://paper.test/a</link></item></channel></rss>'
        fake = AsyncMock()
        fake.request.return_value = response(content=rss,url="https://paper.test/feed")
        fake.close = AsyncMock()
        with patch.object(press,"HTTPClient",return_value=fake):
            await press.collect(conn)
        self.assertIsNone(conn.articles[0][7])

    async def test_prs_06_rotated_feed_recovers(self):
        source = {"id":1,"slug":"paper","status":"active","endpoint_url":"https://paper.test/old-feed","source_class":"news"}
        conn = FakeConnection(sources=[source],titles=[])
        rss = b'<rss><channel><item><title>Recovered</title><link>https://paper.test/a</link></item></channel></rss>'
        fake = AsyncMock()
        fake.request.side_effect = [
            response(404,url="https://paper.test/old-feed"),
            response(content=b'<link rel="alternate" type="application/rss+xml" href="/new-feed">',url="https://paper.test"),
            response(content=rss,url="https://paper.test/new-feed"),
            response(content=rss,url="https://paper.test/new-feed"),
        ]
        fake.close = AsyncMock()
        with patch.object(press,"HTTPClient",return_value=fake):
            await press.collect(conn)
        self.assertEqual(conn.articles[0][3], "Recovered")


class RenderingStories(unittest.TestCase):
    def test_html_uses_headers_and_lists_and_escapes(self):
        result = _to_html("# Report\n\n| Name | Value |\n|---|---:|\n| <unsafe> | 1 |\n\n- [Story](https://example.test/a)\n")
        self.assertIn("<th>", result)
        self.assertIn("<ul>", result)
        self.assertIn("<li>", result)
        self.assertIn("&lt;unsafe&gt;", result)

    def test_counted_units(self):
        self.assertEqual(_counted(1, "article"), "1 article")
        self.assertEqual(_counted(2, "article"), "2 articles")


class PulseStories(unittest.TestCase):
    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params=None): self.sql = sql
        def fetchone(self): return {"id": 10}
        def fetchall(self): return [{"id": 11}, {"id": 12}]

    class _Conn:
        def cursor(self): return PulseStories._Cursor()

    def test_pul_01_selected_sources_run_on_demand(self):
        observed = {}

        async def collect(names, conn, settings):
            observed["names"] = names
            observed["mode"] = _collection_mode.get()
            return []

        context = MagicMock()
        context.__enter__.return_value = self._Conn()
        settings = MagicMock(output_dir=Path("output"))
        with patch.object(sys, "argv", ["main.py", "pulse", "--sources", "discord,twitch,discord"]), \
             patch.object(main, "Settings", return_value=settings), \
             patch.object(main, "connect", return_value=context), \
             patch.object(main, "run_collectors", side_effect=collect), \
             patch.object(main, "render_signal_brief", return_value=Path("output/brief.md")):
            main.main()
        self.assertEqual(observed, {"names":["discord","twitch"], "mode":"on_demand"})

    def test_pul_03_partial_brief_is_written_before_nonzero_exit(self):
        async def collect(names, conn, settings): return ["kick: down"]
        context = MagicMock()
        context.__enter__.return_value = self._Conn()
        render = MagicMock(return_value=Path("output/brief.md"))
        with patch.object(sys, "argv", ["main.py", "pulse", "--sources", "kick"]), \
             patch.object(main, "Settings", return_value=MagicMock(output_dir=Path("output"))), \
             patch.object(main, "connect", return_value=context), \
             patch.object(main, "run_collectors", side_effect=collect), \
             patch.object(main, "render_signal_brief", render), \
             self.assertRaisesRegex(SystemExit, "Brief rendered with collector failures"):
            main.main()
        render.assert_called_once()

    def test_pul_03_empty_run_filter_is_not_unrestricted(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with patch("reporting.ondemand.Path.write_text"):
            render_signal_brief(conn, MagicMock(), run_ids=[])
        executed = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("ds.run_id = ANY" in sql for sql in executed))
        self.assertTrue(any("cs.run_id = ANY" in sql for sql in executed))


if __name__ == "__main__":
    unittest.main()
