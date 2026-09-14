from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from indanya_desktop.sitemap_health import (
    check_public_sitemaps,
    combined_sitemap_health,
    load_sitemap_health,
    record_search_console_observation,
    save_sitemap_health,
    validate_local_sitemaps,
    _parse_sitemap_bytes,
)


def sitemap_xml(urls: list[str]) -> bytes:
    rows = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{rows}</urlset>"
    ).encode("utf-8")


class SitemapHealthTests(unittest.TestCase):
    def test_http_success_never_claims_google_success(self) -> None:
        report = combined_sitemap_health({"status": "healthy"}, {"status": "healthy"})
        self.assertEqual(report["search_console"]["status"], "unverified")
        observation = {"status": "fetch_failed", "source": "ui", "observed_at": "2026-09-14T20:28:00+09:00"}
        record_search_console_observation(self.root, observation)
        save_sitemap_health(self.root, report)
        self.assertEqual(load_sitemap_health(self.root)["search_console"], observation)

    def test_search_observation_needs_time_and_source(self) -> None:
        with self.assertRaises(ValueError):
            record_search_console_observation(self.root, {"status": "success"})

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
            "https://example.test/robots.txt": (self.root / "robots.txt").read_bytes(),
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
            "https://example.test/robots.txt": (self.root / "robots.txt").read_bytes(),
            self.article_url: b"<!doctype html><title>one</title>",
        }

        def request(url: str, _timeout: float) -> tuple[int, bytes]:
            return 200, responses[url]

        with patch("indanya_desktop.sitemap_health._request_bytes", side_effect=request):
            report = check_public_sitemaps(self.public_url, expected)
        self.assertEqual("pending", report["status"])
        self.assertIn("公開先は3件、今回生成は2件", " / ".join(report["errors"]))

    def test_root_robots_status_semantics(self) -> None:
        expected = validate_local_sitemaps(self.root, self.public_url)
        for status, body, healthy in (
            (404, b"", True), (403, b"", True), (429, b"", False), (503, b"", False),
            (200, b"User-agent: Googlebot\nDisallow: /site/articles/\n", False),
        ):
            with self.subTest(status=status):
                visited = []
                def request(url, timeout):
                    visited.append(url)
                    if url == "https://example.test/robots.txt":
                        if status != 200:
                            raise urllib.error.HTTPError(url, status, "test", {}, None)
                        return status, body
                    if url == self.article_url:
                        return 200, b"article"
                    return 200, (self.root / url.rsplit("/", 1)[-1]).read_bytes()
                with patch("indanya_desktop.sitemap_health._request_bytes", side_effect=request):
                    report = check_public_sitemaps(self.public_url, expected)
                self.assertEqual(report["status"] == "healthy", healthy)
                self.assertNotIn(self.public_url + "robots.txt", visited)
                if healthy:
                    self.assertTrue(report["warnings"])
                    self.assertTrue(report["robots"]["allows_crawl"])

    def test_plain_sitemap_must_match_xml_and_be_valid(self) -> None:
        path = self.root / "sitemap-pages.txt"
        path.write_text(self.public_url + "\n" + self.article_url + "\n", encoding="utf-8")
        report = validate_local_sitemaps(self.root, self.public_url)
        self.assertEqual(report["sitemaps"][path.name]["url_count"], 2)
        path.write_text(self.public_url + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "different URLs"):
            validate_local_sitemaps(self.root, self.public_url)
        for invalid in (b"\xff", b"http://example.test/\n", b"https://a.test/\nhttps://a.test/\n", b"<xml/>", b"https://a.test/#f"):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                _parse_sitemap_bytes(invalid, "sitemap-pages.txt")

    def test_same_count_different_urls_is_not_a_successful_deployment(self) -> None:
        expected = validate_local_sitemaps(self.root, self.public_url)
        def request(url, timeout):
            if url.endswith("sitemap.xml"):
                return 200, sitemap_xml([self.article_url, self.public_url + "wrong.html"])
            if url == self.article_url:
                return 200, b"article"
            return 200, (self.root / url.rsplit("/", 1)[-1]).read_bytes()
        with patch("indanya_desktop.sitemap_health._request_bytes", side_effect=request):
            report = check_public_sitemaps(self.public_url, expected)
        self.assertEqual(report["status"], "pending")
        self.assertIn("URL件数は同じ", " ".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
