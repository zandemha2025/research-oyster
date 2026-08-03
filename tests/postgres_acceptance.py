"""Repeatable Postgres-backed user-story acceptance run.

Run with: .venv/bin/python tests/postgres_acceptance.py
"""
from datetime import date
from pathlib import Path
import asyncio
import subprocess
import sys
import uuid
import os

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collectors.replay import replay_response
from collectors.base import HTTPClient, RunLog
from db.queries import connect, insert_raw_response, migrate, seed_config
from reporting.ondemand import render_signal_brief
from reporting.render import render_report
from reporting.rollup import build_rollup


def check(condition: bool, story_id: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{story_id} failed: {detail}")
    print(f"PASS {story_id} {detail}".rstrip())


def main() -> None:
    db_name = f"gcp_story_{uuid.uuid4().hex[:12]}"
    subprocess.run(["createdb", db_name], check=True)
    url = f"postgresql://localhost/{db_name}"
    try:
        with connect(url) as conn:
            migrate(conn)
            seed_config(conn)
            migrate(conn)
            seed_config(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM schema_migrations")
                expected_migrations = len(list((REPO_ROOT / "db" / "migrations").glob("*.sql")))
                check(
                    cur.fetchone()["n"] == expected_migrations,
                    "SET-02",
                    f"all {expected_migrations} migrations applied exactly once",
                )
                cur.execute("SELECT count(*) AS n FROM titles")
                check(cur.fetchone()["n"] == 10, "SET-03", "seed rerun created no duplicate titles")
                cur.execute("UPDATE discord_servers SET guild_name='Marvel Rivals Official' WHERE invite_code='marvelrivals'")
                cur.execute("INSERT INTO discord_servers(invite_code,guild_name,title_slug) VALUES('marvel-rivals-two','Marvel Rivals Two','marvel-rivals')")
                cur.execute("INSERT INTO discord_servers(invite_code,guild_name,title_slug) VALUES('minecraft-two','Minecraft Two','minecraft')")
                cur.execute(
                    """INSERT INTO collection_runs(collector,started_at,finished_at,status,items_ok,items_failed,run_mode)
                       SELECT c,d+interval '16 hours',d+interval '16 hours 1 minute','success',10,0,'scheduled'
                       FROM unnest(ARRAY['discord','twitch','kick','press']) c
                       CROSS JOIN generate_series('2026-07-27'::date,'2026-07-31'::date,'1 day') d"""
                )
                cur.execute(
                    """INSERT INTO collection_runs(collector,started_at,finished_at,status,items_ok,items_failed,run_mode)
                       SELECT c,d+interval '16 hours',d+interval '16 hours 1 minute','success',10,0,'scheduled'
                       FROM unnest(ARRAY['discord','twitch','kick','press']) c
                       CROSS JOIN generate_series('2026-08-03'::date,'2026-08-07'::date,'1 day') d"""
                )
                cur.execute(
                    """INSERT INTO discord_snapshots(server_id,run_id,observed_at,member_count,presence_count,raw)
                       SELECT ds.id,cr.id,cr.started_at,100000,
                         CASE WHEN cr.started_at<'2026-08-03' THEN 100 ELSE 150 END,'{}'
                       FROM collection_runs cr CROSS JOIN LATERAL
                         (SELECT id FROM discord_servers WHERE title_slug IN ('marvel-rivals','minecraft')) ds
                       WHERE cr.collector='discord' AND cr.run_mode='scheduled'"""
                )
                cur.execute(
                    """INSERT INTO stream_categories(platform,platform_cat_id,category_name,title_slug) VALUES
                       ('twitch','1','Marvel Rivals','marvel-rivals'),
                       ('twitch','2','Marvel Rivals Competitive','marvel-rivals'),
                       ('kick','1','Marvel Rivals','marvel-rivals')"""
                )
                cur.execute(
                    """INSERT INTO category_snapshots(category_id,run_id,observed_at,total_viewers,channel_count,raw)
                       SELECT sc.id,cr.id,cr.started_at,
                         CASE WHEN cr.started_at<'2026-08-03'
                           THEN CASE WHEN sc.platform_cat_id='2' THEN 2000 ELSE 8000 END
                           ELSE CASE WHEN sc.platform_cat_id='2' THEN 4000 ELSE 16000 END END,
                         10,'{}'
                       FROM stream_categories sc JOIN collection_runs cr ON cr.collector=sc.platform
                       WHERE cr.run_mode='scheduled'"""
                )
                cur.execute(
                    """INSERT INTO collection_runs(collector,started_at,finished_at,status,items_ok,items_failed,run_mode)
                       VALUES('discord','2026-08-05 18:00Z','2026-08-05 18:01Z','success',1,0,'on_demand'),
                             ('twitch','2026-08-05 18:00Z','2026-08-05 18:01Z','success',1,0,'on_demand') RETURNING id"""
                )
                demand_ids = [row["id"] for row in cur.fetchall()]
                cur.execute(
                    """INSERT INTO discord_snapshots(server_id,run_id,observed_at,member_count,presence_count,raw)
                       SELECT ds.id,cr.id,cr.started_at,100000,999999,'{}'
                       FROM collection_runs cr CROSS JOIN LATERAL
                         (SELECT id FROM discord_servers WHERE invite_code='marvelrivals') ds
                       WHERE cr.collector='discord' AND cr.run_mode='on_demand'"""
                )
                cur.execute(
                    """INSERT INTO category_snapshots(category_id,run_id,observed_at,total_viewers,channel_count,raw)
                       SELECT sc.id,cr.id,cr.started_at,999999,10,'{}'
                       FROM stream_categories sc JOIN collection_runs cr ON cr.collector=sc.platform
                       WHERE cr.run_mode='on_demand' AND sc.platform='twitch'"""
                )
                cur.execute(
                    """INSERT INTO articles(source_id,url_hash,url,headline,published_at,title_slug,raw)
                       SELECT id,'prior','https://e.test/prior','Marvel Rivals prior','2026-07-28','marvel-rivals','{}' FROM sources WHERE slug='ign'"""
                )
                cur.execute(
                    """INSERT INTO articles(source_id,url_hash,url,headline,published_at,title_slug,raw)
                       SELECT id,'now1','https://e.test/now1','Marvel Rivals now','2026-08-04','marvel-rivals','{}' FROM sources WHERE slug='ign'"""
                )
                cur.execute(
                    """INSERT INTO articles(source_id,url_hash,url,headline,published_at,title_slug,raw)
                       SELECT id,'now2','https://e.test/now2','Marvel Rivals now','2026-08-05','marvel-rivals','{}' FROM sources WHERE slug='polygon'"""
                )
                cur.execute(
                    """INSERT INTO articles(source_id,url_hash,url,headline,published_at,title_slug,raw)
                       SELECT id,'meme','https://e.test/meme','A game meme','2026-08-05',NULL,'{}' FROM sources WHERE slug='know-your-meme'"""
                )
            conn.commit()

            rollup = build_rollup(conn, date(2026, 8, 3))
            marvel = next(row for row in rollup["streams"] if row["title"] == "Marvel Rivals")
            check(marvel["twitch"]["value"] == 20000, "REP-03", "mapped Twitch categories summed before weekly mean")
            check(all(mover["title"] != "Minecraft" for mover in rollup["movers"]), "REP-04", "two Discord servers did not create agreement")
            check(len(rollup["memes"]) == 1 and all(row["title"] != "A game meme" for row in rollup["press"]), "PRS-05")
            check(all(value["current_days"] == 5 for value in rollup["coverage"].values()), "REP-01", "complete-day coverage counted")

            md_path, html_path = render_report(conn, date(2026, 8, 3), Path("output"), 16, 10000)
            markdown, html = md_path.read_text(), html_path.read_text()
            required = ["## Movers", "## Top IP categories", "## Discord community", "## Press volume", "## Meme and trend watch", "## Methodology"]
            check(all(section in markdown for section in required) and html_path.exists(), "REP-05")
            check("week over week" in markdown and "viewers" in markdown and "members online" in markdown, "REP-06")
            check("—" not in markdown and "<th>" in html, "REP-07")
            check("999,999" not in markdown, "RUN-03", "on-demand outlier excluded from weekly report")

            brief = render_signal_brief(conn, Path("output"), run_ids=demand_ids).read_text()
            check("999,999 members online" in brief and "1,999,998 viewers" in brief, "PUL-02")

            raw_id = insert_raw_response(
                conn, run_id=demand_ids[0], collector="press", payload_kind="press_document",
                method="GET", url="https://e.test/feed", request_headers={}, status=200,
                response_headers={"content-type":"application/rss+xml"},
                content=b'<rss><channel><item><title>Story</title><link>https://e.test/story</link></item></channel></rss>',
                parser_version="acceptance",
            )
            with conn.cursor() as cur:
                cur.execute("SELECT body_hash,parse_status,parser_version FROM raw_responses WHERE id=%s", (raw_id,))
                raw = cur.fetchone()
            check(bool(raw["body_hash"]) and raw["parse_status"] == "pending" and raw["parser_version"] == "acceptance", "RAW-03")
            replayed = replay_response(conn, raw_id)
            check(replayed[0]["headline"] == "Story", "RPL-01")
            replay_cli = subprocess.run(
                [sys.executable, str(REPO_ROOT / "main.py"), "replay", "--run-id", str(demand_ids[0])],
                cwd=REPO_ROOT, env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True,
            )
            check(replay_cli.returncode == 0 and f"Replayed raw response {raw_id}" in replay_cli.stdout, "RPL-02")

            async def verify_preparse_capture() -> None:
                client = HTTPClient(timeout=1, max_retries=0, min_interval=0)
                await client.client.aclose()
                client.client = httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json={"value": 1}, request=request)
                    ), timeout=1,
                )
                try:
                    with RunLog(conn, "press") as run:
                        response = await client.request("GET", "https://feed.test/rss")
                        with connect(url) as evidence_conn, evidence_conn.cursor() as cur:
                            cur.execute("SELECT count(*) AS n FROM raw_responses WHERE run_id=%s", (run.run_id,))
                            captured = cur.fetchone()["n"]
                        check(captured == 1 and "raw_response_id" in response.extensions, "RAW-01", "response committed before caller parsing")
                        run.success()
                finally:
                    await client.close()

            asyncio.run(verify_preparse_capture())
    finally:
        subprocess.run(["dropdb", db_name], check=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise
