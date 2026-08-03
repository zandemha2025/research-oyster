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


def _to_html(markdown: str) -> str:
    lines, output, in_table, in_list, table_header = markdown.splitlines(), [], False, False, True
    for line in lines:
        if line.startswith("|"):
            if in_list:
                output.append("</ul>")
                in_list = False
            cells = [html.escape(cell.strip()) for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":"} for cell in cells):
                table_header = False
                continue
            if not in_table:
                output.append("<table>")
                in_table = True
                table_header = True
            tag = "th" if table_header else "td"
            output.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
            continue
        if in_table:
            output.append("</table>")
            in_table = False
            table_header = True
        if not line.startswith("- ") and in_list:
            output.append("</ul>")
            in_list = False
        escaped = html.escape(line)
        escaped = re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
        if line.startswith("### "):
            output.append(f"<h3>{escaped[4:]}</h3>")
        elif line.startswith("## "):
            output.append(f"<h2>{escaped[3:]}</h2>")
        elif line.startswith("# "):
            output.append(f"<h1>{escaped[2:]}</h1>")
        elif line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{escaped[2:]}</li>")
        elif line:
            output.append(f"<p>{escaped}</p>")
    if in_table:
        output.append("</table>")
    if in_list:
        output.append("</ul>")
    return "<!doctype html><meta charset=\"utf-8\"><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px;text-align:left}th{background:#f3f4f6}</style>" + "\n".join(output)


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
