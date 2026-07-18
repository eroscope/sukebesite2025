from __future__ import annotations

import json
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from validate_article import validate_database  # noqa: E402


class ReferenceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if value and key.lower() in {"href", "src"}:
                self.references.append(value)


class SiteIntegrityTests(unittest.TestCase):
    def test_published_articles_and_images_exist(self) -> None:
        database = validate_database(
            json.loads((ROOT / "data" / "articles.json").read_text(encoding="utf-8"))
        )
        published = [article for article in database if article["status"] == "published"]
        self.assertTrue(published)
        self.assertEqual(
            published,
            sorted(published, key=lambda article: article["published_at"], reverse=True),
        )

        for article in published:
            self.assertTrue((ROOT / article["url"]).is_file())
            self.assertTrue((ROOT / article["thumbnail"]).is_file())
            image_directory = ROOT / "assets" / "articles" / article["slug"]
            image_files = [path for path in image_directory.iterdir() if path.is_file()]
            self.assertEqual(len(image_files), article["images_used"])

    def test_home_and_articles_have_no_broken_local_references(self) -> None:
        pages = [ROOT / "index.html", *sorted((ROOT / "articles").glob("*.html"))]
        for page in pages:
            source = page.read_text(encoding="utf-8")
            self.assertNotIn("data:image/", source)
            collector = ReferenceCollector()
            collector.feed(source)
            for reference in collector.references:
                parsed = urlparse(reference)
                if parsed.scheme or parsed.netloc or reference.startswith("#"):
                    continue
                local = (page.parent / parsed.path).resolve()
                self.assertTrue(local.is_relative_to(ROOT.resolve()), reference)
                self.assertTrue(local.exists(), f"{page.name}: {reference}")

    def test_home_renderer_avoids_html_string_insertion(self) -> None:
        script = (ROOT / "assets" / "common" / "site.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn('article.status === "published"', script)


if __name__ == "__main__":
    unittest.main()
