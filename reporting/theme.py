"""Shared visual language for the deliverables (charts, deck, docx).

One palette, one set of fonts, so report.docx / deck.pptx / charts/*.png all look like they
came from the same studio. Muted, print-friendly, high-contrast — consultant-deck aesthetic,
not dashboard neon.
"""

from __future__ import annotations

# Core palette (hex). INK on PAPER; ACCENT for the lead bar / key stat; the SERIES cycle for
# multi-bar charts. Kept deliberately small and calm.
INK = "#1a1f2b"
SUBTLE = "#5b6472"
PAPER = "#ffffff"
PANEL = "#f4f2ec"      # warm off-white panel behind stat tiles
ACCENT = "#c2410c"     # burnt orange — the one attention colour
ACCENT_SOFT = "#e8d9cf"
GRID = "#e6e3db"
SERIES = ["#c2410c", "#0f766e", "#1e3a5f", "#a16207", "#6d28d9", "#9d174d", "#4d7c0f"]

FONT = "DejaVu Sans"   # ships with matplotlib; consistent across the three outputs

# python-pptx / python-docx take RGB tuples, not hex.
def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
