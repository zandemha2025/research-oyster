from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from collectors.base import HTTPClient, RunLog
from db.queries import json_value, upsert_category


TOKEN_URL = "https://id.kick.com/oauth/token"
API_URL = "https://api.kick.com/public/v2"


async def _token(http: HTTPClient, client_id: str, secret: str) -> str:
    response = await http.request("POST", TOKEN_URL, data={"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"})
    response.raise_for_status()
    return response.json()["access_token"]


async def collect(conn: Any, client_id: str, secret: str, pages: int = 10, timeout: float = 20, max_retries: int = 4) -> None:
    http = HTTPClient(timeout, max_retries)
    try:
        with RunLog(conn, "kick") as run:
            try:
                token = await _token(http, client_id, secret)
                headers = {"Authorization": f"Bearer {token}"}
                cursor = None
                raw_pages: list[dict[str, Any]] = []
                streams: list[dict[str, Any]] = []
                for _ in range(pages):
                    params: dict[str, Any] = {"limit": 1000}
                    if cursor:
                        params["cursor"] = cursor
                    response = await http.request("GET", f"{API_URL}/livestreams", headers=headers, params=params)
                    if response.status_code == 401:
                        token = await _token(http, client_id, secret)
                        headers["Authorization"] = f"Bearer {token}"
                        response = await http.request("GET", f"{API_URL}/livestreams", headers=headers, params=params)
                    response.raise_for_status()
                    page = response.json()
                    raw_pages.append(page)
                    streams.extend(page.get("data", []))
                    cursor = page.get("pagination", {}).get("next_cursor")
                    if not cursor:
                        break
                grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
                unique_streams = {
                    str(stream.get("id") or f"row:{index}"): stream
                    for index, stream in enumerate(streams)
                }
                for stream in unique_streams.values():
                    category = stream.get("category") or {}
                    if category.get("id") is not None:
                        grouped[str(category["id"])].append(stream)
                observed = datetime.now(timezone.utc)
                ranked = sorted(grouped, key=lambda key: sum(s.get("viewer_count") or 0 for s in grouped[key]), reverse=True)
                with conn.cursor() as cur:
                    for rank, category_id in enumerate(ranked, 1):
                        rows = grouped[category_id]
                        category = rows[0]["category"]
                        top = max(rows, key=lambda x: x.get("viewer_count") or 0)
                        top_user = top.get("broadcaster_user") or {}
                        top_channel = top.get("channel") or {}
                        db_category_id = upsert_category(cur, "kick", category_id, category.get("name") or "Unknown")
                        cur.execute(
                            """INSERT INTO category_snapshots
                               (category_id, run_id, observed_at, total_viewers, channel_count, top_channel,
                                top_channel_viewers, directory_rank, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (db_category_id, run.run_id, observed, sum(r.get("viewer_count") or 0 for r in rows), len(rows),
                             top_user.get("username") or top_channel.get("slug"), top.get("viewer_count") or 0, rank,
                             json_value({"streams": rows,
                                         "page_cursors": [p.get("pagination", {}).get("next_cursor") for p in raw_pages]})),
                        )
                conn.commit()
                run.success(len(grouped))
            except Exception as exc:
                conn.rollback()
                run.error("kick", exc)
    finally:
        await http.close()
