import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from collectors import discord, kick, press, twitch
from collectors.base import collection_mode
from collectors.replay import replay_response
from db.queries import connect, migrate, seed_config
from reporting.render import render_report
from reporting.ondemand import render_signal_brief
from settings import Settings


SOURCES = ("discord", "twitch", "kick", "press")


def week_start(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("week_start must be an ISO date in YYYY-MM-DD format") from exc
    if parsed.weekday() != 0:
        raise argparse.ArgumentTypeError("week_start must be a Monday")
    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def parse_sources(value: str) -> list[str]:
    names = list(dict.fromkeys(name.strip().lower() for name in value.split(",") if name.strip()))
    if not names:
        raise argparse.ArgumentTypeError("at least one source is required")
    invalid = sorted(set(names) - set(SOURCES))
    if invalid:
        raise argparse.ArgumentTypeError(f"Unknown sources: {', '.join(invalid)}")
    return names


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Gaming Culture Pulse data pipeline")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations and configuration seeds")
    collect = commands.add_parser("collect", help="Run one independent collector")
    collect.add_argument("source", choices=(*SOURCES, "all"))
    report = commands.add_parser("report", help="Render one UTC week beginning on the supplied date")
    report.add_argument("week_start", type=week_start)
    pulse = commands.add_parser("pulse", help="Collect fresh sources and render an on-demand signal brief")
    pulse.add_argument("--sources", type=parse_sources, default=list(SOURCES), help="Comma-separated sources")
    pulse.add_argument("--press-hours", type=positive_int, default=48, help="Recent press window in hours")
    replay = commands.add_parser("replay", help="Reparse stored raw responses without external calls")
    replay_target = replay.add_mutually_exclusive_group(required=True)
    replay_target.add_argument("--response-id", type=int)
    replay_target.add_argument("--run-id", type=int)
    return root


async def run_collector(name: str, conn, settings: Settings) -> None:
    common = {"timeout": settings.http_timeout_seconds, "max_retries": settings.max_retries}
    if name == "discord":
        await discord.collect(conn, **common)
    elif name == "press":
        await press.collect(conn, **common)
    elif name == "twitch":
        if not settings.twitch_client_id or not settings.twitch_client_secret:
            raise SystemExit("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are required")
        await twitch.collect(conn, settings.twitch_client_id, settings.twitch_client_secret, settings.twitch_stream_pages, **common)
    elif name == "kick":
        if not settings.kick_client_id or not settings.kick_client_secret:
            raise SystemExit("KICK_CLIENT_ID and KICK_CLIENT_SECRET are required")
        await kick.collect(conn, settings.kick_client_id, settings.kick_client_secret, settings.kick_stream_pages, **common)


async def run_collectors(names: list[str], conn, settings: Settings) -> list[str]:
    failed: list[str] = []
    for name in names:
        try:
            await run_collector(name, conn, settings)
        except (Exception, SystemExit) as exc:
            failed.append(f"{name}: {exc}")
    return failed


def main() -> None:
    args = parser().parse_args()
    settings = Settings()
    with connect(settings.database_url) as conn:
        if args.command == "migrate":
            migrate(conn)
            seed_config(conn)
            print("Database migrated and configuration seeded.")
        elif args.command == "collect":
            if args.source == "all":
                # "all" means all usable sources. Twitch and Kick are optional,
                # so a public-only installation remains a successful workflow.
                names = ["discord"]
                if settings.twitch_client_id and settings.twitch_client_secret:
                    names.append("twitch")
                if settings.kick_client_id and settings.kick_client_secret:
                    names.append("kick")
                names.append("press")
            else:
                names = [args.source]
            failed = asyncio.run(run_collectors(names, conn, settings))
            print(f"Collection finished for {', '.join(names)}. Inspect collection_runs for status.")
            if failed:
                raise SystemExit("; ".join(failed))
        elif args.command == "report":
            md_path, html_path = render_report(
                conn, args.week_start, settings.output_dir, settings.collection_hour_utc,
                settings.kick_stream_pages * 1000,
            )
            print(md_path)
            print(html_path)
        elif args.command == "pulse":
            names = args.sources
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(max(id),0) AS id FROM collection_runs")
                before_run_id = cur.fetchone()["id"]
            with collection_mode("on_demand"):
                failed = asyncio.run(run_collectors(names, conn, settings))
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM collection_runs WHERE id > %s AND run_mode='on_demand' ORDER BY id", (before_run_id,))
                pulse_run_ids = [row["id"] for row in cur.fetchall()]
            path = render_signal_brief(
                conn, settings.output_dir,
                datetime.now(timezone.utc) - timedelta(hours=args.press_hours), pulse_run_ids,
            )
            print(path)
            if failed:
                raise SystemExit("Brief rendered with collector failures: " + "; ".join(failed))
        elif args.command == "replay":
            if args.response_id is not None:
                ids = [args.response_id]
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM raw_responses WHERE run_id=%s ORDER BY id", (args.run_id,))
                    ids = [row["id"] for row in cur.fetchall()]
                if not ids:
                    raise SystemExit(f"No raw responses found for run {args.run_id}")
            failed = []
            for raw_id in ids:
                try:
                    replay_response(conn, raw_id)
                    print(f"Replayed raw response {raw_id}")
                except Exception as exc:
                    failed.append(f"{raw_id}: {exc}")
            if failed:
                raise SystemExit("Replay failures: " + "; ".join(failed))


if __name__ == "__main__":
    main()
