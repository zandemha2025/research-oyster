import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from collectors.base import HTTPClient, RequestFailed
from collectors import discord, kick, press, twitch


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.conn.executed.append((normalized, params))
        self.rowcount = 0
        if "INSERT INTO collection_runs" in normalized:
            self.rows = [{"id": self.conn.next_run_id}]
            self.conn.next_run_id += 1
        elif "SELECT id, invite_code FROM discord_servers" in normalized:
            self.rows = list(self.conn.servers)
        elif "SELECT * FROM sources" in normalized:
            self.rows = list(self.conn.sources)
        elif "SELECT slug, aliases FROM titles" in normalized:
            self.rows = list(self.conn.titles)
        elif "INSERT INTO stream_categories" in normalized:
            platform, cat_id = params[0], params[1]
            key = (platform, cat_id)
            if key not in self.conn.category_ids:
                self.conn.category_ids[key] = len(self.conn.category_ids) + 101
            self.rows = [{"id": self.conn.category_ids[key]}]
        elif "INSERT INTO discord_snapshots" in normalized:
            self.conn.discord_snapshots.append(params)
        elif "INSERT INTO category_snapshots" in normalized:
            self.conn.category_snapshots.append(params)
        elif "INSERT INTO articles" in normalized:
            url_hash = params[1]
            if url_hash in self.conn.article_hashes:
                self.rowcount = 0
            else:
                self.conn.article_hashes.add(url_hash)
                self.conn.articles.append(params)
                self.rowcount = 1
        elif "UPDATE collection_runs" in normalized:
            self.conn.run_updates.append(params)
        elif "UPDATE discord_servers SET status='invite_dead'" in normalized:
            self.conn.dead_servers.append(params[0])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, *, servers=(), sources=(), titles=()):
        self.servers = list(servers)
        self.sources = list(sources)
        self.titles = list(titles)
        self.executed = []
        self.discord_snapshots = []
        self.category_snapshots = []
        self.articles = []
        self.article_hashes = set()
        self.category_ids = {}
        self.dead_servers = []
        self.run_updates = []
        self.next_run_id = 1
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def response(status=200, payload=None, *, url="https://example.test/resource", headers=None, content=None):
    request = httpx.Request("GET", url)
    if content is not None:
        return httpx.Response(status, content=content, request=request, headers=headers)
    return httpx.Response(status, json=payload or {}, request=request, headers=headers)


class HTTPAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_429_and_honors_retry_after(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(429 if calls == 1 else 200, headers={"Retry-After": "0"}, request=request)

        client = HTTPClient(timeout=1, max_retries=1, min_interval=0)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=1)
        with patch("collectors.base.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await client.request("GET", "https://example.test")
        await client.close()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(calls, 2)
        sleep.assert_awaited_once_with(0.0)

    async def test_network_failure_exhausts_bounded_retries(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("offline", request=request)

        client = HTTPClient(timeout=1, max_retries=2, min_interval=0)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=1)
        with patch("collectors.base.asyncio.sleep", new=AsyncMock()), patch("collectors.base.random.random", return_value=0):
            with self.assertRaises(RequestFailed):
                await client.request("GET", "https://example.test")
        await client.close()
        self.assertEqual(calls, 3)


class CollectorAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_success_uses_widget_presence(self):
        conn = FakeConnection(servers=[{"id": 7, "invite_code": "stable"}])
        fake = AsyncMock()
        fake.request.side_effect = [
            response(payload={"guild": {"id": "g1", "name": "Guild"}, "approximate_member_count": 500, "approximate_presence_count": 40}),
            response(payload={"presence_count": 52}),
        ]
        fake.close = AsyncMock()
        with patch.object(discord, "HTTPClient", return_value=fake):
            await discord.collect(conn)
        self.assertEqual(len(conn.discord_snapshots), 1)
        self.assertEqual(conn.discord_snapshots[0][4], 52)
        self.assertEqual(conn.run_updates[-1][0:3], ("success", 1, 0))

    async def test_discord_dead_invite_and_network_loss_are_partial(self):
        conn = FakeConnection(servers=[{"id": 1, "invite_code": "dead"}, {"id": 2, "invite_code": "offline"}])
        fake = AsyncMock()
        fake.request.side_effect = [response(404), RequestFailed("network vanished")]
        fake.close = AsyncMock()
        with patch.object(discord, "HTTPClient", return_value=fake):
            await discord.collect(conn)
        self.assertEqual(conn.dead_servers, [1])
        self.assertEqual(conn.run_updates[-1][0:3], ("failed", 0, 2))
        self.assertEqual(len(conn.run_updates[-1][3].obj), 2)

    async def test_discord_widget_failure_does_not_lose_invite_snapshot(self):
        conn = FakeConnection(servers=[{"id": 1, "invite_code": "ok"}])
        fake = AsyncMock()
        fake.request.side_effect = [
            response(payload={"guild": {"id": "g"}, "approximate_presence_count": 11}),
            RequestFailed("widget disabled"),
        ]
        fake.close = AsyncMock()
        with patch.object(discord, "HTTPClient", return_value=fake):
            await discord.collect(conn)
        self.assertEqual(conn.discord_snapshots[0][4], 11)
        self.assertEqual(conn.run_updates[-1][0], "success")

    async def test_discord_network_loss_after_progress_records_partial(self):
        conn = FakeConnection(servers=[{"id": 1, "invite_code": "ok"}, {"id": 2, "invite_code": "offline"}])
        fake = AsyncMock()
        fake.request.side_effect = [
            response(payload={"guild": {}, "approximate_member_count": 20, "approximate_presence_count": 4}),
            RequestFailed("connection dropped"),
        ]
        fake.close = AsyncMock()
        with patch.object(discord, "HTTPClient", return_value=fake):
            await discord.collect(conn)
        self.assertEqual(len(conn.discord_snapshots), 1)
        self.assertEqual(conn.run_updates[-1][0:3], ("partial", 1, 1))

    async def test_twitch_refreshes_token_paginates_and_aggregates(self):
        conn = FakeConnection()
        fake = AsyncMock()
        fake.request.side_effect = [
            response(payload={"access_token": "old"}),
            response(401),
            response(payload={"access_token": "new"}),
            response(payload={"data": [
                {"game_id": "10", "game_name": "Game", "viewer_count": 8, "user_name": "a"},
                {"game_id": "10", "game_name": "Game", "viewer_count": 12, "user_name": "b"},
            ], "pagination": {"cursor": "next"}}),
            response(payload={"data": [{"game_id": "20", "game_name": "Other", "viewer_count": 5, "user_name": "c"}], "pagination": {}}),
            response(payload={"data": [{"id": "10", "name": "Game"}, {"id": "20", "name": "Other"}]}),
        ]
        fake.close = AsyncMock()
        with patch.object(twitch, "HTTPClient", return_value=fake):
            await twitch.collect(conn, "id", "secret", pages=3)
        self.assertEqual(len(conn.category_snapshots), 2)
        game = next(row for row in conn.category_snapshots if row[0] == conn.category_ids[("twitch", "10")])
        self.assertEqual((game[3], game[4], game[5], game[6], game[7]), (20, 2, "b", 12, 1))
        stream_calls = [c for c in fake.request.await_args_list if c.args[1].endswith("/streams")]
        self.assertEqual(stream_calls[-1].kwargs["params"]["after"], "next")
        self.assertEqual(stream_calls[1].kwargs["headers"]["Authorization"], "Bearer new")

    async def test_kick_cursor_pagination_aggregation_and_shape_edges(self):
        conn = FakeConnection()
        fake = AsyncMock()
        fake.request.side_effect = [
            response(payload={"access_token": "token"}),
            response(payload={"data": [
                {"category": {"id": 7, "name": "Game"}, "viewer_count": 9, "broadcaster_user": {"username": "top"}},
                {"category": None, "viewer_count": 100},
            ], "pagination": {"next_cursor": "cursor2"}}),
            response(payload={"data": [
                {"category": {"id": 7, "name": "Game"}, "viewer_count": 2, "channel": {"slug": "low"}},
                {"category": {"id": 8}, "channel": {"slug": "missing-count"}},
            ], "pagination": {}}),
        ]
        fake.close = AsyncMock()
        with patch.object(kick, "HTTPClient", return_value=fake):
            await kick.collect(conn, "id", "secret", pages=3)
        self.assertEqual(len(conn.category_snapshots), 2)
        game = next(row for row in conn.category_snapshots if row[0] == conn.category_ids[("kick", "7")])
        self.assertEqual((game[3], game[4], game[5], game[6]), (11, 2, "top", 9))
        live_calls = [c for c in fake.request.await_args_list if c.args[1].endswith("/livestreams")]
        self.assertEqual(live_calls[1].kwargs["params"]["cursor"], "cursor2")

    async def test_press_resolves_feed_and_second_poll_deduplicates(self):
        source = {"id": 3, "slug": "paper", "status": "unavailable", "endpoint_url": "https://paper.test", "source_class": "news"}
        conn = FakeConnection(sources=[source], titles=[{"slug": "game", "aliases": ["The Game"]}])
        rss = b'''<rss version="2.0"><channel><item><title>The Game update</title><link>https://PAPER.test/story/?utm_source=x</link><description>News</description></item></channel></rss>'''

        async def run_once():
            fake = AsyncMock()
            fake.request.side_effect = [
                response(content=b'<link rel="alternate" type="application/rss+xml" href="/rss.xml">', url="https://paper.test"),
                response(content=rss, url="https://paper.test/rss.xml"),
                response(content=rss, url="https://paper.test/rss.xml"),
            ]
            fake.close = AsyncMock()
            with patch.object(press, "HTTPClient", return_value=fake):
                await press.collect(conn)

        await run_once()
        await run_once()
        self.assertEqual(len(conn.articles), 1)
        self.assertEqual(conn.articles[0][2], "https://paper.test/story")
        self.assertEqual(conn.articles[0][7], "game")


if __name__ == "__main__":
    unittest.main()
