from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _n(value: Any) -> str:
    return "" if value is None else f"{value:,.0f}"


def _counted(value: Any, singular: str) -> str:
    noun = singular if value is not None and abs(float(value)) == 1 else f"{singular}s"
    return f"{_n(value)} {noun}" if value is not None else ""


def render_signal_brief(
    conn: Any, output_dir: Path, since: datetime | None = None,
    run_ids: list[int] | None = None,
) -> Path:
    """Render the freshest collected observations for time-sensitive report work."""
    generated = datetime.now(timezone.utc)
    since = since or generated - timedelta(hours=48)
    restrict_runs = run_ids is not None
    discord_filter = "WHERE ds.run_id = ANY(%s)" if restrict_runs else ""
    stream_filter = "WHERE cs.run_id = ANY(%s)" if restrict_runs else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT DISTINCT ON (d.id) d.guild_name, d.invite_code,
                      COALESCE(t.display_name,d.guild_name,d.invite_code) AS title,
                      ds.presence_count,ds.member_count,ds.observed_at
               FROM discord_servers d JOIN discord_snapshots ds ON ds.server_id=d.id
               LEFT JOIN titles t ON t.slug=d.title_slug
               {discord_filter}
               ORDER BY d.id,ds.observed_at DESC""", (run_ids,) if restrict_runs else ()
        )
        discord = sorted(cur.fetchall(), key=lambda row: row["presence_count"] or 0, reverse=True)[:15]
        cur.execute(
            f"""WITH latest AS (
                 SELECT DISTINCT ON (sc.id) sc.platform,sc.title_slug,sc.category_name,
                        cs.total_viewers,cs.channel_count,cs.observed_at
                 FROM stream_categories sc JOIN category_snapshots cs ON cs.category_id=sc.id
                 {stream_filter}
                 ORDER BY sc.id,cs.observed_at DESC
               )
               SELECT l.platform,COALESCE(t.display_name,l.category_name) AS title,
                      sum(l.total_viewers) AS viewers,sum(l.channel_count) AS channels,
                      max(l.observed_at) AS observed_at
               FROM latest l LEFT JOIN titles t ON t.slug=l.title_slug
               GROUP BY l.platform,COALESCE(t.display_name,l.category_name)
               ORDER BY sum(l.total_viewers) DESC""", (run_ids,) if restrict_runs else ()
        )
        streams = cur.fetchall()[:30]
        cur.execute(
            """SELECT COALESCE(t.display_name,'Unmatched') AS title,count(*) AS articles,
                      count(DISTINCT s.id) AS outlets
               FROM articles a JOIN sources s ON s.id=a.source_id
               LEFT JOIN titles t ON t.slug=a.title_slug
               WHERE s.source_class='news' AND a.published_at >= %s
               GROUP BY t.display_name ORDER BY count(*) DESC LIMIT 15""", (since,))
        press = cur.fetchall()
        cur.execute(
            """SELECT a.headline,a.url,s.display_name,a.published_at
               FROM articles a JOIN sources s ON s.id=a.source_id
               WHERE s.source_class='meme' AND a.published_at >= %s
               ORDER BY a.published_at DESC LIMIT 15""", (since,))
        memes = cur.fetchall()
        if restrict_runs:
            cur.execute(
                """SELECT collector,status,finished_at,items_ok,items_failed
                   FROM collection_runs WHERE id = ANY(%s) ORDER BY collector""", (run_ids,))
        else:
            cur.execute(
                """SELECT collector,status,finished_at,items_ok,items_failed
                   FROM collection_runs WHERE id IN
                     (SELECT max(id) FROM collection_runs GROUP BY collector)
                   ORDER BY collector"""
            )
        runs = cur.fetchall()

    lines = [
        "# Gaming Culture Fresh Signal Brief", "",
        f"Generated: {generated:%Y-%m-%d %H:%M UTC}. Press window begins: {since:%Y-%m-%d %H:%M UTC}.", "",
        "This is an on-demand observation brief. It is not a week over week trend report.", "",
        "## Latest collector status", "",
        "| Source | Status | Finished | Items collected | Items failed |", "|---|---|---|---:|---:|",
    ]
    lines.extend(f"| {r['collector']} | {r['status']} | {r['finished_at'] or ''} | {r['items_ok']} | {r['items_failed']} |" for r in runs)
    lines += ["", "## Live streaming signals", "", "| Platform | Title | Current sampled viewers | Live channels | Observed |", "|---|---|---:|---:|---|"]
    lines.extend(f"| {r['platform'].title()} | {r['title']} | {_counted(r['viewers'], 'viewer')} | {_counted(r['channels'], 'channel')} | {r['observed_at']} |" for r in streams)
    if not streams:
        lines.append("| No collected data | | | | |")
    lines += ["", "## Discord community signals", "", "| Server | Title | Members online | Registered members | Observed |", "|---|---|---:|---:|---|"]
    lines.extend(f"| {r['guild_name'] or r['invite_code']} | {r['title']} | {_counted(r['presence_count'], 'member')} online | {_counted(r['member_count'], 'registered member')} | {r['observed_at']} |" for r in discord)
    if not discord:
        lines.append("| No collected data | | | | |")
    lines += ["", "## Recent press signals", "", "| Title | Articles | Outlets covering |", "|---|---:|---:|"]
    lines.extend(f"| {r['title']} | {_counted(r['articles'], 'article')} | {_counted(r['outlets'], 'outlet')} |" for r in press)
    if not press:
        lines.append("| No matched coverage | 0 articles | 0 outlets |")
    lines += ["", "## Meme and trend watch", ""]
    lines.extend(f"- [{r['headline']}]({r['url']}) from {r['display_name']}." for r in memes)
    if not memes:
        lines.append("No meme-source entries were collected in the selected window.")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{generated:%Y%m%d-%H%M}-fresh-signals.md"
    path.write_text("\n".join(lines).replace("—", "-") + "\n")
    return path
