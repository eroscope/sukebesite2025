from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from add_article import add_article, commit_staged  # noqa: E402
from validate_article import ValidationError  # noqa: E402


class ArticleToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "data" / "articles.json").write_text("[]\n", encoding="utf-8")
        self.package = self.root / "package"
        self.images = self.package / "images"
        self.images.mkdir(parents=True)
        (self.images / "image-01.webp").write_bytes(b"image one")
        (self.images / "image-02.webp").write_bytes(b"image two")
        self.article = self.package / "article.html"
        self.article.write_text(
            "<!doctype html><html lang=\"ja\"><body>"
            "<img src=\"images/image-01.webp\" alt=\"one\">"
            "<img src=\"images/image-02.webp\" alt=\"two\">"
            "<img id=\"lightboxImage\" alt=\"\">"
            "</body></html>",
            encoding="utf-8",
        )
        self.metadata = self.package / "metadata.json"
        self.metadata_value = {
            "id": "test-001",
            "slug": "test-article",
            "title": "テスト記事",
            "category": "画像",
            "status": "published",
            "published_at": "2026-07-18T10:00:00+09:00",
            "display_date": "2026.07.18",
            "comments": 3,
            "url": "articles/test-article.html",
            "thumbnail": "assets/articles/test-article/image-01.webp",
            "source_url": "https://example.com/source",
            "images_used": 2,
            "featured": True,
        }
        self._write_metadata()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_metadata(self) -> None:
        self.metadata.write_text(
            json.dumps(self.metadata_value, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_add_and_update_article(self) -> None:
        result = add_article(self.root, self.metadata, self.article)
        self.assertIn("add", result)
        installed = (self.root / "articles" / "test-article.html").read_text(encoding="utf-8")
        self.assertIn("../assets/articles/test-article/image-01.webp", installed)
        self.assertTrue((self.root / "assets" / "articles" / "test-article" / "image-02.webp").is_file())

        self.metadata_value["comments"] = 9
        self._write_metadata()
        result = add_article(self.root, self.metadata, self.article)
        self.assertIn("update", result)
        database = json.loads((self.root / "data" / "articles.json").read_text(encoding="utf-8"))
        self.assertEqual(len(database), 1)
        self.assertEqual(database[0]["comments"], 9)

    def test_dry_run_does_not_change_site(self) -> None:
        before = (self.root / "data" / "articles.json").read_bytes()
        result = add_article(self.root, self.metadata, self.article, dry_run=True)
        self.assertIn("Dry run OK", result)
        self.assertEqual((self.root / "data" / "articles.json").read_bytes(), before)
        self.assertFalse((self.root / "articles").exists())

    def test_rejects_image_count_mismatch_without_changes(self) -> None:
        self.metadata_value["images_used"] = 3
        self._write_metadata()
        before = (self.root / "data" / "articles.json").read_bytes()
        with self.assertRaises(ValidationError):
            add_article(self.root, self.metadata, self.article)
        self.assertEqual((self.root / "data" / "articles.json").read_bytes(), before)

    def test_rejects_invalid_slug_without_changes(self) -> None:
        self.metadata_value["slug"] = "../escape"
        self._write_metadata()
        with self.assertRaises(ValidationError):
            add_article(self.root, self.metadata, self.article)
        self.assertFalse((self.root / "articles").exists())

    def test_commit_rolls_back_all_targets(self) -> None:
        target_article = self.root / "articles" / "item.html"
        target_images = self.root / "assets" / "articles" / "item"
        target_data = self.root / "data" / "articles.json"
        target_article.parent.mkdir(parents=True)
        target_images.mkdir(parents=True)
        target_article.write_text("old article", encoding="utf-8")
        (target_images / "old.webp").write_bytes(b"old image")
        target_data.write_text("old data", encoding="utf-8")

        stage = self.root / "stage"
        backup = self.root / "backup"
        stage.mkdir()
        backup.mkdir()
        stage_article = stage / "item.html"
        stage_images = stage / "images"
        stage_data = stage / "articles.json"
        stage_article.write_text("new article", encoding="utf-8")
        stage_images.mkdir()
        (stage_images / "new.webp").write_bytes(b"new image")
        stage_data.write_text("new data", encoding="utf-8")

        def fail_on_data(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            if Path(source) == stage_data:
                raise OSError("simulated replacement failure")
            os.replace(source, destination)

        with self.assertRaises(OSError):
            commit_staged(
                [
                    (stage_article, target_article),
                    (stage_images, target_images),
                    (stage_data, target_data),
                ],
                backup,
                fail_on_data,
            )

        self.assertEqual(target_article.read_text(encoding="utf-8"), "old article")
        self.assertEqual((target_images / "old.webp").read_bytes(), b"old image")
        self.assertEqual(target_data.read_text(encoding="utf-8"), "old data")


if __name__ == "__main__":
    unittest.main()
