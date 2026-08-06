from __future__ import annotations

import unittest

from research_engine.connectors import _md_links, _md_title


class WebReaderParsingTests(unittest.TestCase):
    def test_title_from_jina_prefix(self):
        md = "Title: Dune: Part Two | Rotten Tomatoes\nURL Source: https://x\n\nbody text"
        self.assertEqual(_md_title(md), "Dune: Part Two | Rotten Tomatoes")

    def test_title_from_h1_fallback(self):
        self.assertEqual(_md_title("# Big Heading\n\ntext"), "Big Heading")

    def test_links_extracted_and_deduped(self):
        md = "see [a](https://x.com/1) and [b](https://x.com/2) and [again](https://x.com/1)"
        self.assertEqual(_md_links(md), ["https://x.com/1", "https://x.com/2"])


if __name__ == "__main__":
    unittest.main()
