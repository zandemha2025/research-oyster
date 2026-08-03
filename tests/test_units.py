import unittest

import httpx

from collectors.base import HTTPClient
from collectors.press import FeedLinkParser, canonicalize_url
from reporting.render import _to_html


class UnitTests(unittest.TestCase):
    def test_canonical_url_dedupes_tracking(self):
        self.assertEqual(canonicalize_url("HTTPS://Example.COM/story/?utm_source=x"), "https://example.com/story")

    def test_feed_discovery_parser(self):
        parser = FeedLinkParser()
        parser.feed('<link rel="alternate" type="application/rss+xml" href="/feed.xml">')
        self.assertEqual(parser.urls, ["/feed.xml"])

    def test_retry_after(self):
        response = httpx.Response(429, headers={"Retry-After": "3"})
        self.assertEqual(HTTPClient._retry_delay(response, 0), 3.0)

    def test_html_conversion(self):
        self.assertIn("<h1>Title</h1>", _to_html("# Title\n"))


if __name__ == "__main__":
    unittest.main()

