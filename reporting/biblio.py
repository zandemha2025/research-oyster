"""Build a numbered [n] bibliography from a synthesis — deterministically, from the sources the
report actually cites, so traceability doesn't depend on the model remembering to fill it in.

Every quote in a theme carries a specific source URL; every entry the model put in numbered_sources
carries one too. We merge them, dedupe by URL, number them [1..N], and hand back both a url->n map
(to stamp [n] next to each inline citation) and the ordered entries (to render the Sources list with
deep, clickable links). A report is only as trustworthy as this list is complete.
"""

from __future__ import annotations

from typing import Any


def build_bibliography(synthesis: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    url_to_n: dict[str, int] = {}

    def add(url: Any, label: Any = "", quote: Any = "", tool: Any = "", pulled_at: Any = "") -> int | None:
        url = (str(url) if url else "").strip()
        if not url.startswith("http"):
            return None
        if url in url_to_n:
            e = entries[url_to_n[url] - 1]
            if quote and not e.get("quote"):
                e["quote"] = str(quote)
            if tool and not e.get("tool"):
                e["tool"] = str(tool)
            return url_to_n[url]
        n = len(entries) + 1
        url_to_n[url] = n
        entries.append({"n": n, "label": (str(label) or url)[:200], "url": url,
                        "quote": str(quote or "")[:280], "tool": str(tool or ""),
                        "pulled_at": str(pulled_at or "")})
        return n

    # Author-supplied bibliography first (keeps its labels/pull dates), then everything quoted.
    for s in (synthesis.get("numbered_sources") or []):
        add(s.get("url"), s.get("label") or s.get("note"), "", s.get("tool"), s.get("pulled_at"))
    for th in (synthesis.get("themes") or []):
        for c in (th.get("citations") or []):
            add(c.get("url"), c.get("source") or th.get("title"), c.get("quote"))
    return url_to_n, entries


def annotate_themes(synthesis: dict[str, Any], url_to_n: dict[str, int]) -> list[dict[str, Any]]:
    """Return themes with each citation stamped with its [n] (None if it had no URL), so the
    renderers can show the marker without re-deriving numbers."""
    out = []
    for th in (synthesis.get("themes") or []):
        cites = []
        for c in (th.get("citations") or []):
            cites.append({**c, "n": url_to_n.get((str(c.get("url")) if c.get("url") else "").strip())})
        out.append({**th, "citations": cites})
    return out
