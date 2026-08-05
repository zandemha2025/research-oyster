from __future__ import annotations

import unittest

from reporting.render import _to_html


class ToHtmlTests(unittest.TestCase):
    """The synthesis is full of **bold**, *italic*, and > blockquotes; the report must
    render them, not show literal markup."""

    def test_bold_and_italic(self):
        html = _to_html("This is **very** important and *slightly* less so.")
        self.assertIn("<strong>very</strong>", html)
        self.assertIn("<em>slightly</em>", html)
        self.assertNotIn("**", html)

    def test_underscore_italic_byline(self):
        html = _to_html("_Generated 2026 · job #5_")
        self.assertIn("<em>Generated 2026 · job #5</em>", html)

    def test_blockquote(self):
        html = _to_html('> "I just use the case for cheaper patches"')
        self.assertIn("<blockquote>", html)
        self.assertIn("</blockquote>", html)
        self.assertIn("cheaper patches", html)

    def test_links_still_work_and_are_escaped(self):
        html = _to_html("See [the thread](https://reddit.com/r/x) now.")
        self.assertIn('<a href="https://reddit.com/r/x">the thread</a>', html)

    def test_headings_and_hr(self):
        html = _to_html("# Title\n## Section\n### Sub\n---\ntext")
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<h2>Section</h2>", html)
        self.assertIn("<h3>Sub</h3>", html)
        self.assertIn("<hr>", html)

    def test_table_with_inline_formatting(self):
        md = "| Source | Note |\n|---|---|\n| x | **failed** |"
        html = _to_html(md)
        self.assertIn("<table>", html)
        self.assertIn("<th>Source</th>", html)
        self.assertIn("<td><strong>failed</strong></td>", html)
        self.assertNotIn("<td>---</td>", html)  # separator row consumed

    def test_html_is_escaped(self):
        html = _to_html("a <script>alert(1)</script> b")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
