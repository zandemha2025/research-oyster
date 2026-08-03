from datetime import date, datetime, time, timedelta, timezone
from typing import Any


COVERAGE_THRESHOLD_DAYS = 5


def _delta(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return (current - prior) * 100.0 / prior


def _windows(week_start: date) -> tuple[datetime, datetime, datetime]:
    start = datetime.combine(week_start, time.min, timezone.utc)
    return start - timedelta(days=7), start, start + timedelta(days=7)


def _movement(row: dict[str, Any], coverage_ok: bool, baseline: bool) -> None:
    row["value"] = float(row["value"]) if row.get("value") is not None else None
    row["prior_value"] = float(row["prior_value"]) if row.get("prior_value") is not None else None
    row["absolute_change"] = (
        row["value"] - row["prior_value"]
        if row["value"] is not None and row["prior_value"] is not None
        else None
    )
    if not coverage_ok:
        row["state"], row["delta"] = "INSUFFICIENT_DATA", None
    elif baseline and row["prior_value"] is None:
        row["state"], row["delta"] = "BASELINE", None
    elif row["prior_value"] in (None, 0) and (row["value"] or 0) > 0:
        row["state"], row["delta"] = "NEW", None
    elif (row["value"] or 0) == 0 and (row["prior_value"] or 0) > 0:
        row["state"], row["delta"] = "DROPPED", None
    else:
        row["delta"] = _delta(row["value"], row["prior_value"])
        row["state"] = "UP" if (row["delta"] or 0) > 0 else "DOWN" if (row["delta"] or 0) < 0 else "UNCHANGED"


def build_rollup(conn: Any, week_start: date) -> dict[str, Any]:
    prior_start, start, end = _windows(week_start)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT collector,
                      count(DISTINCT (started_at AT TIME ZONE 'UTC')::date)
                        FILTER (WHERE started_at >= %s AND started_at < %s AND status='success' AND items_failed=0) AS prior_days,
                      count(DISTINCT (started_at AT TIME ZONE 'UTC')::date)
                        FILTER (WHERE started_at >= %s AND started_at < %s AND status='success' AND items_failed=0) AS current_days
               FROM collection_runs WHERE started_at >= %s AND started_at < %s
                 AND run_mode='scheduled' GROUP BY collector""",
            (prior_start, start, start, end, prior_start, end),
        )
        coverage = {
            row["collector"]: {"current_days": int(row["current_days"]), "prior_days": int(row["prior_days"])}
            for row in cur.fetchall()
        }

        cur.execute(
            """SELECT ds.server_id, d.guild_name AS server, t.slug AS title_slug,
                      COALESCE(t.display_name, d.guild_name, d.invite_code) AS title,
                      avg(ds.presence_count) FILTER (WHERE ds.observed_at >= %s AND ds.observed_at < %s) AS prior_value,
                      avg(ds.presence_count) FILTER (WHERE ds.observed_at >= %s AND ds.observed_at < %s) AS value,
                      max(ds.member_count) FILTER (WHERE ds.observed_at >= %s AND ds.observed_at < %s) AS member_count
               FROM discord_snapshots ds JOIN discord_servers d ON d.id=ds.server_id
               JOIN collection_runs cr ON cr.id=ds.run_id AND cr.run_mode='scheduled'
               LEFT JOIN titles t ON t.slug=d.title_slug
               WHERE ds.observed_at >= %s AND ds.observed_at < %s
               GROUP BY ds.server_id,d.guild_name,d.invite_code,t.slug,t.display_name""",
            (prior_start, start, start, end, start, end, prior_start, end),
        )
        discord = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """WITH observations AS (
                 SELECT d.title_slug,t.display_name AS title,ds.run_id,sum(ds.presence_count) AS presence
                 FROM discord_snapshots ds JOIN discord_servers d ON d.id=ds.server_id
                 JOIN collection_runs cr ON cr.id=ds.run_id AND cr.run_mode='scheduled'
                 JOIN titles t ON t.slug=d.title_slug
                 WHERE ds.observed_at >= %s AND ds.observed_at < %s
                 GROUP BY d.title_slug,t.display_name,ds.run_id
               )
               SELECT title_slug,title,
                      avg(presence) FILTER (WHERE run_id IN (SELECT id FROM collection_runs WHERE started_at >= %s AND started_at < %s AND run_mode='scheduled')) AS prior_value,
                      avg(presence) FILTER (WHERE run_id IN (SELECT id FROM collection_runs WHERE started_at >= %s AND started_at < %s AND run_mode='scheduled')) AS value
               FROM observations GROUP BY title_slug,title""",
            (prior_start, end, prior_start, start, start, end),
        )
        discord_title_rows = [dict(row) for row in cur.fetchall()]

        # Sum all mapped categories for a title within each run before taking weekly means.
        cur.execute(
            """WITH observations AS (
                 SELECT sc.platform, sc.title_slug, COALESCE(t.display_name, sc.category_name) AS title,
                        cs.run_id, sum(cs.total_viewers) AS viewers
                 FROM category_snapshots cs JOIN stream_categories sc ON sc.id=cs.category_id
                 JOIN collection_runs cr ON cr.id=cs.run_id AND cr.run_mode='scheduled'
                 LEFT JOIN titles t ON t.slug=sc.title_slug
                 WHERE cs.observed_at >= %s AND cs.observed_at < %s
                 GROUP BY sc.platform,sc.title_slug,COALESCE(t.display_name, sc.category_name),cs.run_id
               )
               SELECT platform,title_slug,title,
                      avg(viewers) FILTER (WHERE run_id IN (SELECT id FROM collection_runs WHERE started_at >= %s AND started_at < %s AND run_mode='scheduled')) AS prior_value,
                      avg(viewers) FILTER (WHERE run_id IN (SELECT id FROM collection_runs WHERE started_at >= %s AND started_at < %s AND run_mode='scheduled')) AS value
               FROM observations GROUP BY platform,title_slug,title""",
            (prior_start, end, prior_start, start, start, end),
        )
        streaming = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """SELECT a.title_slug,t.display_name AS title,
                      count(*) FILTER (WHERE a.published_at >= %s AND a.published_at < %s) AS prior_value,
                      count(*) FILTER (WHERE a.published_at >= %s AND a.published_at < %s) AS value,
                      count(DISTINCT s.id) FILTER (WHERE a.published_at >= %s AND a.published_at < %s) AS outlets
               FROM articles a JOIN sources s ON s.id=a.source_id JOIN titles t ON t.slug=a.title_slug
               WHERE s.source_class='news' AND a.published_at >= %s AND a.published_at < %s
               GROUP BY a.title_slug,t.display_name""",
            (prior_start, start, start, end, start, end, prior_start, end),
        )
        press = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """SELECT a.headline,a.url,s.display_name FROM articles a JOIN sources s ON s.id=a.source_id
               WHERE s.source_class='meme' AND a.published_at >= %s AND a.published_at < %s
               ORDER BY a.published_at DESC""", (start, end))
        memes = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT display_name FROM sources WHERE status='unavailable' ORDER BY display_name")
        unavailable = [row["display_name"] for row in cur.fetchall()]

    has_any_prior = any(v["prior_days"] for v in coverage.values())
    baseline = not has_any_prior

    def coverage_ok(collector: str) -> bool:
        item = coverage.get(collector, {})
        return item.get("current_days", 0) >= COVERAGE_THRESHOLD_DAYS and (
            baseline or item.get("prior_days", 0) >= COVERAGE_THRESHOLD_DAYS
        )

    for row in discord:
        _movement(row, coverage_ok("discord"), baseline)
    for row in streaming:
        _movement(row, coverage_ok(row["platform"]), baseline)
    for row in press:
        _movement(row, coverage_ok("press"), baseline)

    discord = sorted(
        (r for r in discord if r["value"] is not None),
        key=lambda r: r["delta"] if r["delta"] is not None else -10**9,
        reverse=True,
    )[:15]
    press_display = sorted((r for r in press if r["value"]), key=lambda r: r["value"], reverse=True)[:15]

    stream_titles: dict[str, dict[str, Any]] = {}
    for row in streaming:
        key = row["title_slug"] or f"{row['platform']}:{row['title']}"
        target = stream_titles.setdefault(key, {"title_slug": row["title_slug"], "title": row["title"], "twitch": None, "kick": None})
        target[row["platform"]] = row
    top_streams = sorted(stream_titles.values(), key=lambda r: sum((r[p] or {}).get("value") or 0 for p in ("twitch", "kick")), reverse=True)[:15]

    # Collapse Discord servers to one title-level signal. Each source class contributes at most once.
    discord_title = {row["title_slug"]: row for row in discord_title_rows}
    for row in discord_title.values():
        _movement(row, coverage_ok("discord"), baseline)

    signals: dict[str, dict[str, Any]] = {}
    source_rows = [("Discord", slug, row) for slug, row in discord_title.items()]
    source_rows += [(row["platform"].title(), row.get("title_slug"), row) for row in streaming]
    source_rows += [("Press", row.get("title_slug"), row) for row in press]
    for source, slug, row in source_rows:
        if slug and row["state"] in ("UP", "DOWN") and row.get("delta") is not None:
            signals.setdefault(slug, {"title": row["title"], "signals": {}})["signals"][source] = row

    movers = []
    for signal in signals.values():
        rows = signal["signals"]
        up = [name for name, row in rows.items() if row["state"] == "UP"]
        down = [name for name, row in rows.items() if row["state"] == "DOWN"]
        agreed = up if len(up) >= 2 else down if len(down) >= 2 else []
        if agreed:
            signal["signals"] = [(name, rows[name]) for name in agreed]
            signal["strength"] = max(abs(rows[name]["delta"]) for name in agreed)
            movers.append(signal)
    movers.sort(key=lambda row: row["strength"], reverse=True)
    return {"week_start": week_start, "week_end": week_start + timedelta(days=6), "baseline": baseline,
            "discord": discord, "streams": top_streams, "press": press_display, "memes": memes,
            "movers": movers[:5], "coverage": coverage, "coverage_threshold": COVERAGE_THRESHOLD_DAYS,
            "unavailable": unavailable}
