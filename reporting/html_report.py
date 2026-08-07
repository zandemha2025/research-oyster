"""Build report.html as its own DESIGNED web report — not a markdown dump.

A standalone, self-contained page (charts embedded as data URIs so it works served or opened
directly): a cover, the point of view as a hero, an executive answer, stat tiles + styled tables +
charts under "The numbers", themes rendered as quote cards with superscript [n] citations that jump
to a linked bibliography, recommendations, a quiet method/confidence footer, and the Sources list
with deep links. Consultant aesthetic from reporting/theme. Distinct from the docx and the deck by
design — same content, format-native treatment.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from reporting import theme
from reporting.tables import chart_series, _fmt


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _data_uri(png: Any) -> str | None:
    try:
        p = Path(png)
        if p.exists():
            return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        pass
    return None


def _cite_sup(citation: dict[str, Any]) -> str:
    n = citation.get("n")
    if not n:
        return ""
    return f' <a class="cite" href="#src-{n}">[{n}]</a>'


def _stat_tiles(tables: list[dict[str, Any]], raw_tables: list[dict[str, Any]]) -> str:
    # Use the first table that yields a numeric series for the headline tiles.
    for t in raw_tables:
        series = chart_series(t)
        if series and series["points"]:
            pts = sorted(series["points"], key=lambda p: p["value"], reverse=True)[:3]
            unit = t.get("unit") or ""
            tiles = "".join(
                f'<div class="tile"><div class="tile-num">{_esc(_fmt(p["value"]))}'
                f'{_esc(unit) if unit=="%" else ""}</div><div class="tile-lab">{_esc(p["group"])}</div></div>'
                for p in pts)
            return f'<div class="tiles">{tiles}</div>'
    return ""


def _table_html(t: dict[str, Any]) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in t["header"])
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in t["body"])
    note = f'<p class="note">{_esc(t["note"])}</p>' if t.get("note") else ""
    unit = f' <span class="unit">({_esc(t["unit"])})</span>' if t.get("unit") else ""
    return (f'<h3>{_esc(t["title"])}{unit}</h3>'
            f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{note}')


def build_html(title: str, synthesis: dict[str, Any], themes: list[dict[str, Any]],
               tables: list[dict[str, Any]], biblio: list[dict[str, Any]], generated_at: str,
               charts: list[dict[str, Any]] | None = None) -> str:
    s = synthesis
    charts = charts or []
    raw_tables = s.get("metrics_tables") or []
    chart_imgs = {}
    for ch in charts:
        uri = _data_uri(ch.get("png"))
        if uri:
            chart_imgs[(ch.get("table") or {}).get("title") or ch.get("slug")] = uri

    parts: list[str] = []
    parts.append(f'<div class="cover"><div class="kicker">Research Oyster</div>'
                 f'<h1>{_esc(title)}</h1><div class="meta">{_esc(generated_at)}'
                 f' · {_esc(s.get("evidence_total") or "")} answer drawn from real player voices</div></div>')

    if s.get("point_of_view"):
        parts.append(f'<section class="pov"><div class="pov-label">Our point of view</div>'
                     f'<p>{_esc(s["point_of_view"])}</p></section>')

    if s.get("key_takeaways"):
        items = "".join(f"<li>{_esc(t)}</li>" for t in s["key_takeaways"])
        parts.append(f'<section><div class="glance"><div class="glance-label">At a glance</div>'
                     f'<ul>{items}</ul></div></section>')

    if s.get("executive_answer"):
        parts.append(f'<section><h2>Executive answer</h2><p class="lede">{_esc(s["executive_answer"])}</p>'
                     f'{_stat_tiles(tables, raw_tables)}</section>')

    if tables:
        blocks = []
        for t, rt in zip(tables, raw_tables):
            img = chart_imgs.get(rt.get("title"))
            chart = f'<img class="chart" alt="chart" src="{img}">' if img else ""
            blocks.append(f'<div class="metric">{_table_html(t)}{chart}</div>')
        parts.append(f'<section><h2>The numbers</h2>{"".join(blocks)}</section>')

    if themes:
        tblocks = []
        for th in themes:
            cites = ""
            for c in (th.get("citations") or []):
                q, src = c.get("quote"), (c.get("source") or c.get("url") or "")
                if q:
                    cites += (f'<blockquote>“{_esc(q)}”'
                              f'<cite>— {_esc(src)}{_cite_sup(c)}</cite></blockquote>')
            insight = f'<p>{_esc(th.get("insight"))}</p>' if th.get("insight") else ""
            tblocks.append(f'<div class="theme"><h3>{_esc(th.get("title") or "Theme")}</h3>{insight}{cites}</div>')
        parts.append(f'<section><h2>What players are saying</h2>{"".join(tblocks)}</section>')

    if s.get("tensions"):
        parts.append(f'<section><h2>Where it splits</h2><p>{_esc(s["tensions"])}</p></section>')

    recs = s.get("recommendations") or []
    if recs:
        items = "".join(f"<li>{_esc(r)}</li>" for r in recs)
        parts.append(f'<section><h2>What we’d do</h2><ol class="recs">{items}</ol></section>')

    foot = ""
    if s.get("method"):
        foot += f'<div class="method"><h3>How we got here</h3><p>{_esc(s["method"])}</p></div>'
    if s.get("confidence"):
        foot += f'<p class="conf"><strong>Confidence.</strong> {_esc(s["confidence"])}</p>'
    if s.get("limitations"):
        foot += f'<p class="limit"><strong>Limitations.</strong> {_esc(s["limitations"])}</p>'
    if foot:
        parts.append(f'<section class="footer-notes">{foot}</section>')

    if biblio:
        rows = ""
        for e in biblio:
            q = f'<div class="src-q">“{_esc(e["quote"])}”</div>' if e.get("quote") else ""
            link = f'<a href="{_esc(e["url"])}" target="_blank" rel="noopener">{_esc(e["url"])}</a>'
            rows += (f'<li id="src-{e["n"]}"><span class="src-n">[{e["n"]}]</span>'
                     f'<div class="src-body"><div class="src-label">{_esc(e["label"])}</div>'
                     f'{link}{q}</div></li>')
        parts.append(f'<section class="sources"><h2>Sources</h2><ol class="srclist">{rows}</ol></section>')

    return _PAGE.replace("__TITLE__", _esc(title)).replace("__BODY__", "".join(parts))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{{--ink:{INK};--sub:{SUB};--accent:{ACCENT};--panel:{PANEL};--grid:{GRID};--paper:{PAPER};}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:#e9e7e1;color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.6;-webkit-font-smoothing:antialiased}}
  .doc{{max-width:820px;margin:32px auto;background:var(--paper);
    box-shadow:0 2px 24px rgba(0,0,0,.10);border-radius:6px;overflow:hidden}}
  .cover{{background:var(--ink);color:#fff;padding:56px 52px 44px}}
  .cover .kicker{{color:var(--accent);font-weight:700;letter-spacing:.14em;
    text-transform:uppercase;font-size:12px;margin-bottom:16px}}
  .cover h1{{margin:0;font-size:34px;line-height:1.15;font-weight:800;letter-spacing:-.01em}}
  .cover .meta{{color:#b9c0cc;font-size:13px;margin-top:18px}}
  section{{padding:14px 52px}}
  section:last-child{{padding-bottom:48px}}
  h2{{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);
    font-weight:700;margin:34px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--grid)}}
  h3{{font-size:18px;margin:22px 0 8px;font-weight:700}}
  p{{margin:0 0 14px}}
  .pov{{background:var(--panel);border-left:5px solid var(--accent);margin:0;
    padding:28px 52px}}
  .pov-label{{color:var(--accent);font-weight:700;font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;margin-bottom:8px}}
  .pov p{{font-size:22px;line-height:1.4;font-weight:600;margin:0;color:var(--ink)}}
  .lede{{font-size:17px}}
  .glance{{background:#fff;border:1px solid var(--grid);border-top:4px solid var(--accent);
    border-radius:8px;padding:20px 24px;margin:20px 0 6px;box-shadow:0 1px 8px rgba(0,0,0,.05)}}
  .glance-label{{color:var(--accent);font-weight:700;font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;margin-bottom:10px}}
  .glance ul{{margin:0;padding-left:0;list-style:none}}
  .glance li{{position:relative;padding:7px 0 7px 26px;font-size:15.5px;font-weight:600;
    border-bottom:1px solid #f0eee8}}
  .glance li:last-child{{border-bottom:none}}
  .glance li:before{{content:"→";position:absolute;left:0;color:var(--accent);font-weight:800}}
  .tiles{{display:flex;gap:14px;margin:22px 0 6px;flex-wrap:wrap}}
  .tile{{flex:1;min-width:150px;background:var(--panel);border-radius:8px;padding:20px 16px;text-align:center}}
  .tile-num{{font-size:34px;font-weight:800;color:var(--accent);line-height:1}}
  .tile-lab{{color:var(--sub);font-size:13px;margin-top:8px}}
  .metric{{margin:20px 0 26px}}
  .tablewrap{{overflow-x:auto}}
  table{{border-collapse:collapse;width:100%;font-size:13.5px;margin:6px 0}}
  th{{text-align:left;background:var(--ink);color:#fff;padding:8px 10px;font-weight:600;white-space:nowrap}}
  td{{padding:7px 10px;border-bottom:1px solid var(--grid)}}
  tr:nth-child(even) td{{background:#faf9f6}}
  .unit{{color:var(--sub);font-weight:400;font-size:14px}}
  .note{{color:var(--sub);font-size:12.5px;font-style:italic;margin-top:6px}}
  .chart{{width:100%;max-width:100%;margin:14px 0 4px;border:1px solid var(--grid);border-radius:6px}}
  .theme{{margin:8px 0 22px}}
  blockquote{{margin:12px 0;background:#faf8f4;border-left:3px solid var(--accent);
    padding:12px 16px;border-radius:0 6px 6px 0;font-size:15px}}
  blockquote cite{{display:block;margin-top:8px;color:var(--sub);font-size:13px;font-style:normal}}
  a.cite{{color:var(--accent);text-decoration:none;font-weight:700}}
  .recs{{padding-left:22px}} .recs li{{margin:8px 0;font-size:15.5px}}
  .footer-notes{{background:#faf9f6;margin-top:20px;border-top:1px solid var(--grid)}}
  .method h3{{font-size:14px;color:var(--sub);text-transform:uppercase;letter-spacing:.08em}}
  .method p,.conf,.limit{{font-size:13.5px;color:var(--sub)}}
  .sources ol{{list-style:none;padding:0}}
  .srclist li{{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--grid)}}
  .src-n{{color:var(--accent);font-weight:700;flex:none}}
  .src-label{{font-weight:600;font-size:14px}}
  .src-body a{{color:#1e5aa8;font-size:12.5px;word-break:break-all}}
  .src-q{{color:var(--sub);font-size:13px;font-style:italic;margin-top:4px}}
  @media (max-width:640px){{.cover,section,.pov{{padding-left:22px;padding-right:22px}}.cover h1{{font-size:26px}}}}
</style></head>
<body><div class="doc">__BODY__</div></body></html>"""

_PAGE = _PAGE.format(INK=theme.INK, SUB=theme.SUBTLE, ACCENT=theme.ACCENT,
                     PANEL=theme.PANEL, GRID=theme.GRID, PAPER=theme.PAPER)
