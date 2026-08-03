from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from collectors.base import HTTPClient, RunLog
from db.queries import json_value, upsert_category


TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_URL = "https://api.twitch.tv/helix"


async def _token(http: HTTPClient, client_id: str, secret: str) -> str:
    response = await http.request("POST", TOKEN_URL, params={"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"})
    response.raise_for_status()
    return response.json()["access_token"]


async def collect(conn: Any, client_id: str, secret: str, pages: int = 10, timeout: float = 20, max_retries: int = 4) -> None:
    http = HTTPClient(timeout, max_retries)
    try:
        with RunLog(conn, "twitch") as run:
            try:
                token = await _token(http, client_id, secret)
                headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
                raw_pages, streams, cursor = [], [], None
                for _ in range(pages):
                    params = {"first": 100}
                    if cursor:
                        params["after"] = cursor
                    response = await http.request("GET", f"{API_URL}/streams", headers=headers, params=params)
                    if response.status_code == 401:
                        token = await _token(http, client_id, secret)
                        headers["Authorization"] = f"Bearer {token}"
                        response = await http.request("GET", f"{API_URL}/streams", headers=headers, params=params)
                    response.raise_for_status()
                    page = response.json()
                    raw_pages.append(page)
                    streams.extend(page.get("data", []))
                    cursor = page.get("pagination", {}).get("cursor")
                    if not cursor:
                        break
                top_response = await http.request("GET", f"{API_URL}/games/top", headers=headers, params={"first": 100})
                top_response.raise_for_status()
                top_raw = top_response.json()
                ranks = {g["id"]: i for i, g in enumerate(top_raw.get("data", []), 1)}
                grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
                unique_streams = {
                    str(stream.get("id") or f"row:{index}"): stream
                    for index, stream in enumerate(streams)
                }
                for stream in unique_streams.values():
                    if stream.get("game_id"):
                        grouped[stream["game_id"]].append(stream)
                observed = datetime.now(timezone.utc)
                with conn.cursor() as cur:
                    for game_id, rows in grouped.items():
                        top = max(rows, key=lambda x: x.get("viewer_count") or 0)
                        cat_id = upsert_category(cur, "twitch", game_id, rows[0].get("game_name") or "Unknown")
                        cur.execute(
                            """INSERT INTO category_snapshots
                               (category_id, run_id, observed_at, total_viewers, channel_count, top_channel,
                                top_channel_viewers, directory_rank, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (cat_id, run.run_id, observed, sum(r.get("viewer_count") or 0 for r in rows), len(rows),
                             top.get("user_name"), top.get("viewer_count") or 0, ranks.get(game_id),
                             json_value({"streams": rows, "top_game": next((g for g in top_raw.get("data", []) if g.get("id") == game_id), None),
                                         "page_cursors": [p.get("pagination", {}).get("cursor") for p in raw_pages]})),
                        )
                conn.commit()
                run.success(len(grouped))
            except Exception as exc:
                conn.rollback()
                run.error("twitch", exc)
    finally:
        await http.close()
