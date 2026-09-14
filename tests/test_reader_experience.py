import json
import tempfile
import unittest
from pathlib import Path

from tools.indanya_desktop.reader_experience import compact_articles, refresh_reader_experience, replace_content

ROOT = Path(__file__).resolve().parents[1]


class ReaderExperienceTests(unittest.TestCase):
    def item(self, i=1):
        return dict(slug=f"sample-{i}", title=f"Sample <{i}>", url=f"articles/sample-{i}.html", status="published",
                    thumbnail="assets/common/indanya-logo.png", published_at=f"2026-09-{i % 28 + 1:02d}", comments=999,
                    source_url="https://example.com/private", search_text="searchable body", tags=["Topic"])

    def test_replace_nested_element_preserves_siblings(self):
        source = '<div id="x"><div><b>old</b></div></div><div>keep</div>'
        self.assertEqual(replace_content(source, "x", "new"), '<div id="x">new</div><div>keep</div>')

    def test_compact_index_filters_unsafe_and_deduplicates(self):
        rows = compact_articles([self.item(), self.item(), {**self.item(2), "slug": "../../no"}])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("comments", rows[0])
        self.assertNotIn("search_text", rows[0])
        self.assertNotIn("source_url", rows[0])

    def test_static_lists_paginate_escape_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("index.html", "latest.html"):
                (root / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
            items = [self.item(i) for i in range(1, 51)]
            generated = refresh_reader_experience(root, "https://example.com/site/", items)
            self.assertEqual(generated, ["latest.html", "latest-2.html", "latest-3.html"])
            home = (root / "index.html").read_text(encoding="utf-8")
            self.assertIn('data-reader-static="true"', home)
            self.assertIn('href="articles/sample-', home)
            self.assertNotIn("site.js", home)
            self.assertNotIn("padding:70px", home)
            latest = (root / "latest-2.html").read_text(encoding="utf-8")
            self.assertEqual(latest.count('class="post-card"'), 24)
            self.assertIn('rel="next" href="latest-3.html"', latest)
            self.assertIn("&lt;", latest)
            self.assertNotIn("catalog.js", latest)
            self.assertEqual(len(json.loads((root / "data/reader/catalog.json").read_text(encoding="utf-8"))), 50)
            refresh_reader_experience(root, "https://example.com/site/", items)
            self.assertEqual(home, (root / "index.html").read_text(encoding="utf-8"))
            refresh_reader_experience(root, "https://example.com/site/", items[:2])
            self.assertFalse((root / "latest-3.html").exists())


if __name__ == "__main__":
    unittest.main()
