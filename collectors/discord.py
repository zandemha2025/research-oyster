from datetime import datetime, timezone
from typing import Any

from collectors.base import HTTPClient, RunLog
from db.queries import json_value


INVITE_URL = "https://discord.com/api/v10/invites/{code}"
WIDGET_URL = "https://discord.com/api/v10/guilds/{guild_id}/widget.json"


async def collect(conn: Any, timeout: float = 20, max_retries: int = 4) -> None:
    http = HTTPClient(timeout, max_retries, min_interval=1.0)
    try:
        with RunLog(conn, "discord") as run:
            with conn.cursor() as cur:
                cur.execute("SELECT id, invite_code FROM discord_servers WHERE status = 'active' ORDER BY id")
                servers = cur.fetchall()
            for server in servers:
                try:
                    response = await http.request("GET", INVITE_URL.format(code=server["invite_code"]), params={"with_counts":"true", "with_expiration":"true"})
                    if response.status_code == 404:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE discord_servers SET status='invite_dead' WHERE id=%s", (server["id"],))
                        conn.commit()
                        run.error(server["invite_code"], "invite returned 404")
                        continue
                    response.raise_for_status()
                    raw = response.json()
                    guild = raw.get("guild") or {}
                    presence = raw.get("approximate_presence_count")
                    widget_raw = None
                    if guild.get("id"):
                        try:
                            widget = await http.request("GET", WIDGET_URL.format(guild_id=guild["id"]))
                            if widget.status_code == 200:
                                widget_raw = widget.json()
                                presence = widget_raw.get("presence_count", presence)
                        except Exception as widget_error:
                            widget_raw = {"unavailable": str(widget_error)}
                    stored_raw = {"invite": raw, "widget": widget_raw}
                    with conn.cursor() as cur:
                        cur.execute("UPDATE discord_servers SET guild_id=%s, guild_name=%s WHERE id=%s", (guild.get("id"), guild.get("name"), server["id"]))
                        cur.execute(
                            """INSERT INTO discord_snapshots
                               (server_id, run_id, observed_at, member_count, presence_count, raw)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (server["id"], run.run_id, datetime.now(timezone.utc), raw.get("approximate_member_count"), presence, json_value(stored_raw)),
                        )
                    conn.commit()
                    run.success()
                except Exception as exc:
                    conn.rollback()
                    run.error(server["invite_code"], exc)
    finally:
        await http.close()
