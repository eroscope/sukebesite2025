from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from indanya_desktop.sitemap_health import (
    check_public_sitemaps,
    validate_local_sitemaps,
)


def sitemap_xml(urls: list[str]) -> bytes:
    rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{rows}</urlset>"
    ).encode("utf-8")


class SitemapHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        self.public_url = "https://example.test/site/"
        self.article_url = self.public_url + "articles/one.html"
        (self.root / "data" / "articles.json").write_text(
            json.dumps([
                {
                    "slug": "one",
                    "status": "published",
                    "url": "articles/one.html",
                }
            ]),
            encoding="utf-8",
        )
        (self.root / "sitemap.xml").write_bytes(
            sitemap_xml([self.public_url, self.article_url])
        )
        (self.root / "sitemap-images.xml").write_bytes(
            sitemap_xml([self.article_url])
        )
        (self.root / "sitemap-videos.xml").write_bytes(sitemap_xml([]))
        (self.root / "robots.txt").write_text(
            "".join(
                f"Sitemap: {self.public_url}{name}\n"
                for name in (
                    "sitemap.xml",
                    "sitemap-images.xml",
                    "sitemap-videos.xml",
                )
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_validation_requires_every_published_article(self) -> None:
        report = validate_local_sitemaps(self.root, self.public_url)
        self.assertEqual("healthy", report["status"])
        self.assertEqual(1, report["published_articles"])
        self.assertEqual(2, report["sitemaps"]["sitemap.xml"]["url_count"])

        (self.root / "sitemap.xml").write_bytes(sitemap_xml([self.public_url]))
        with self.assertRaisesRegex(RuntimeError, "1件がsitemap.xmlにありません"):
            validate_local_sitemaps(self.root, self.public_url)

    def test_public_validation_checks_xml_counts_robots_and_latest_article(self) -> None:
        expected = validate_local_sitemaps(self.root, self.public_url)
        responses = {
            self.public_url + "sitemap.xml": sitemap_xml(
                [self.public_url, self.article_url]
            ),
            self.public_url + "sitemap-images.xml": sitemap_xml([self.article_url]),
            self.public_url + "sitemap-videos.xml": sitemap_xml([]),
            self.public_url + "robots.txt": (self.root / "robots.txt").read_bytes(),
            self.article_url: b"<!doctype html><title>one</title>",
        }

        def request(url: str, _timeout: float) -> tuple[int, bytes]:
            return 200, responses[url]

        with patch("indanya_desktop.sitemap_health._request_bytes", side_effect=request):
            report = check_public_sitemaps(self.public_url, expected)
        self.assertEqual("healthy", report["status"])
        self.assertEqual(2, report["sitemaps"]["sitemap.xml"]["url_count"])
        self.assertEqual(200, report["sample_article"]["http_status"])

    def test_public_validation_rejects_an_older_sitemap_with_extra_urls(self) -> None:
        expected = validate_local_sitemaps(self.root, self.public_url)
        responses = {
            self.public_url + "sitemap.xml": sitemap_xml(
                [self.public_url, self.article_url, self.public_url + "articles/old.html"]
            ),
            self.public_url + "sitemap-images.xml": sitemap_xml([self.article_url]),
            self.public_url + "sitemap-videos.xml": sitemap_xml([]),
            self.public_url + "robots.txt": (self.root / "robots.txt").read_bytes(),
            self.article_url: b"<!doctype html><title>one</title>",
        }

        def request(url: str, _timeout: float) -> tuple[int, bytes]:
            return 200, responses[url]

        with patch("indanya_desktop.sitemap_health._request_bytes", side_effect=request):
            report = check_public_sitemaps(self.public_url, expected)
        self.assertEqual("pending", report["status"])
        self.assertIn("公開先は3件、今回生成は2件", " / ".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
