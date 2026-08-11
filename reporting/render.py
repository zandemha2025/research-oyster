import html
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from db.queries import json_value
from reporting.rollup import build_rollup


def _number(value: Any) -> str:
    return "" if value is None else f"{value:,.0f}"


def _counted(value: Any, singular: str, plural: str | None = None) -> str:
    if value is None:
        return ""
    noun = singular if abs(float(value)) == 1 else (plural or f"{singular}s")
    return f"{_number(value)} {noun}"


def _percent(value: Any) -> str:
    if value is None:
        return ""
    return f"{'+' if value >= 0 else ''}{value:.1f}%"


def _change(value: Any) -> str:
    if value is None:
        return "unchanged"
    return f"up {abs(value):.1f}%" if value >= 0 else f"down {abs(value):.1f}%"


def _movement(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    state = row.get("state")
    if state in ("NEW", "DROPPED", "INSUFFICIENT_DATA", "BASELINE"):
        return state.replace("_", " ")
    return _percent(row.get("delta"))


_REPORT_CSS = (
    ":root{--ink:#18211d;--muted:#66716b;--paper:#fbfaf6;--green:#173f35;--accent:#26734d;--line:#e4e5df}"
    "*{box-sizing:border-box}"
    "body{font:16px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink);"
    "background:var(--paper);max-width:820px;margin:0 auto;padding:56px 28px 96px}"
    "h1{font:700 34px/1.2 Georgia,serif;letter-spacing:-.5px;margin:0 0 10px}"
    "h2{font:700 22px/1.3 Georgia,serif;margin:42px 0 14px;padding-bottom:6px;border-bottom:2px solid var(--line)}"
    "h3{font:650 17px/1.4 -apple-system,sans-serif;margin:26px 0 8px;color:var(--green)}"
    "p{margin:0 0 14px}"
    "a{color:var(--accent);text-decoration:none;border-bottom:1px solid #bcd3c6}"
    "strong{font-weight:700}em{font-style:italic}"
    "ul{margin:0 0 16px;padding-left:22px}li{margin:6px 0}"
    "blockquote{margin:16px 0;padding:10px 18px;border-left:3px solid var(--accent);background:#eef3f0;"
    "color:#33403a;border-radius:0 8px 8px 0}blockquote p{margin:6px 0;font-style:italic}"
    "table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}"
    "th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}"
    "th{background:#eef2f0;font-weight:650}tr:nth-child(even) td{background:#faf9f5}"
    "hr{border:0;border-top:1px solid var(--line);margin:36px 0}"
    "@media print{body{padding:0;max-width:none}h2{break-after:avoid}}"
)


def _inline(text: str) -> str:
    """Convert inline markdown (links, bold, italic) in already-HTML-escaped text."""
    text = re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", text)
    return text


def _to_html(markdown: str) -> str:
    lines, output = markdown.splitlines(), []
    in_table = in_list = in_quote = False
    table_header = True

    def close_blocks(*, keep_list=False, keep_quote=False, keep_table=False):
        nonlocal in_table, in_list, in_quote, table_header
        if in_list and not keep_list:
            output.append("</ul>"); in_list = False
        if in_quote and not keep_quote:
            output.append("</blockquote>"); in_quote = False
        if in_table and not keep_table:
            output.append("</table>"); in_table = False; table_header = True

    for line in lines:
        if line.startswith("|"):
            close_blocks(keep_table=True)
            raw = [cell.strip() for cell in line.strip("|").split("|")]
            if raw and all(set(cell) <= {"-", ":"} for cell in raw):
                table_header = False
                continue
            if not in_table:
                output.append("<table>"); in_table = True; table_header = True
            tag = "th" if table_header else "td"
            output.append("<tr>" + "".join(f"<{tag}>{_inline(html.escape(c))}</{tag}>" for c in raw) + "</tr>")
            continue
        if line.startswith("> "):
            close_blocks(keep_quote=True)
            if not in_quote:
                output.append("<blockquote>"); in_quote = True
            output.append(f"<p>{_inline(html.escape(line[2:]))}</p>")
            continue

        close_blocks(keep_list=line.startswith("- "))
        content = _inline(html.escape(line))
        if line.startswith("### "):
            output.append(f"<h3>{content[4:]}</h3>")
        elif line.startswith("## "):
            output.append(f"<h2>{content[3:]}</h2>")
        elif line.startswith("# "):
            output.append(f"<h1>{content[2:]}</h1>")
        elif line.strip() in ("---", "***", "___"):
            output.append("<hr>")
        elif line.startswith("- "):
            if not in_list:
                output.append("<ul>"); in_list = True
            output.append(f"<li>{content[2:]}</li>")
        elif line.strip():
            output.append(f"<p>{content}</p>")
    close_blocks()
    return f'<!doctype html><meta charset="utf-8"><style>{_REPORT_CSS}</style>' + "\n".join(output)


def render_report(conn: Any, week_start: date, output_dir: Path, sampling_hour: int, kick_limit: int) -> tuple[Path, Path]:
    data = build_rollup(conn, week_start)
    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data["sampling_hour"] = sampling_hour
    data["kick_limit"] = kick_limit
    data["coverage_line"] = ", ".join(
        f"{name}: {data['coverage'].get(name, {}).get('current_days', 0)} of 7 complete days"
        for name in ("discord", "twitch", "kick", "press")
    )
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=False, trim_blocks=False, lstrip_blocks=True)
    env.filters.update(number=_number, counted=_counted, percent=_percent, change=_change, movement=_movement)
    markdown = env.get_template("report.md.j2").render(**data).replace("—", "-")
    output_dir.mkdir(parents=True, exist_ok=True)
    iso = week_start.isocalendar()
    stem = f"{iso.year}-{iso.week:02d}-gaming-pulse"
    md_path, html_path = output_dir / f"{stem}.md", output_dir / f"{stem}.html"
    md_path.write_text(markdown)
    html_path.write_text(_to_html(markdown))
    with conn.cursor() as cur:
        cur.execute("INSERT INTO report_runs (week_start,week_end,output_path,data_coverage) VALUES (%s,%s,%s,%s)",
                    (data["week_start"], data["week_end"], str(md_path), json_value(data["coverage"])))
    conn.commit()
    return md_path, html_path
