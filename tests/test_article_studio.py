from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import article_studio  # noqa: E402
from add_article import ValidationError  # noqa: E402


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlKXmgAAAAASUVORK5CYII="
)


def make_payload() -> dict[str, object]:
    return {
        "title": "【画像】記事スタジオの動作確認",
        "slug": "studio-check",
        "category": "画像",
        "summary": "記事スタジオで生成したテスト記事。",
        "published_at": "2026-07-18T10:00:00+09:00",
        "status": "published",
        "comments": 4,
        "poster_name": "風吹けば名無し",
        "tags": ["テスト", "画像"],
        "featured": True,
        "fictional_responses": True,
        "source_url": "https://example.com/source",
        "source_label": "確認用出典",
        "transparency_note": "テスト用の画像を使用。",
        "thumbnail_id": "image-a",
        "adult_confirmed": True,
        "rights_confirmed": True,
        "privacy_confirmed": True,
        "source_confirmed": True,
        "replace_existing": False,
        "images": [
            {
                "id": "image-a",
                "name": "source.png",
                "data_url": PNG_DATA_URL,
                "alt": "確認用の画像",
                "orientation": "portrait",
            }
        ],
        "blocks": [
            {"id": "post-a", "type": "post", "text": "最初のレス", "style": "large"},
            {"id": "images-a", "type": "images", "image_ids": ["image-a"]},
            {"id": "post-b", "type": "post", "text": ">>1\n確認できた", "style": "highlight"},
            {"id": "ad-a", "type": "ad", "text": "関連広告枠"},
        ],
    }


class ArticleStudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.site_root = Path(self.temporary.name)
        (self.site_root / "articles").mkdir(parents=True)
        (self.site_root / "data").mkdir(parents=True)
        shutil.copy2(ROOT / "articles" / "pool-look-back.html", self.site_root / "articles" / "pool-look-back.html")
        (self.site_root / "data" / "articles.json").write_text("[]\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_and_adds_article_through_existing_receiver(self) -> None:
        payload = make_payload()
        result = article_studio.add_built_article(payload, self.site_root)

        self.assertEqual(result["slug"], "studio-check")
        article_path = self.site_root / "articles" / "studio-check.html"
        article = article_path.read_text(encoding="utf-8")
        self.assertIn("【画像】記事スタジオの動作確認", article)
        self.assertIn("../assets/articles/studio-check/image-01.png", article)
        self.assertIn('<span class="anchor">&gt;&gt;1</span>', article)
        self.assertNotIn("data:image/png", article)

        database = json.loads((self.site_root / "data" / "articles.json").read_text(encoding="utf-8"))
        self.assertEqual(database[0]["thumbnail"], "assets/articles/studio-check/image-01.png")
        self.assertTrue((self.site_root / "assets" / "articles" / "studio-check" / "image-01.png").is_file())

    def test_preview_and_package_use_the_expected_image_sources(self) -> None:
        payload = make_payload()
        preview = article_studio.build_article(payload, self.site_root, preview=True)
        final = article_studio.build_article(payload, self.site_root)
        self.assertIn("data:image/png;base64,", preview.article_html)
        self.assertIn('src="images/image-01.png"', final.article_html)

        filename, package = article_studio.make_package(payload, self.site_root)
        self.assertEqual(filename, "studio-check.zip")
        with zipfile.ZipFile(BytesIO(package)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["article.html", "images/image-01.png", "metadata.json"],
            )

    def test_rejects_duplicate_or_unplaced_images(self) -> None:
        payload = make_payload()
        payload["blocks"].append({"id": "images-b", "type": "images", "image_ids": ["image-a"]})
        with self.assertRaisesRegex(ValidationError, "only once"):
            article_studio.build_article(payload, self.site_root)

        payload = make_payload()
        payload["blocks"] = [block for block in payload["blocks"] if block["type"] != "images"]
        with self.assertRaisesRegex(ValidationError, "all images"):
            article_studio.build_article(payload, self.site_root)

    def test_existing_slug_requires_explicit_update(self) -> None:
        payload = make_payload()
        article_studio.add_built_article(payload, self.site_root)
        with self.assertRaisesRegex(ValidationError, "replace_existing"):
            article_studio.add_built_article(payload, self.site_root)

        payload["replace_existing"] = True
        result = article_studio.add_built_article(payload, self.site_root)
        self.assertIn("update", result["message"])

    def test_local_api_renders_with_session_token(self) -> None:
        server = article_studio.StudioServer(("127.0.0.1", 0), self.site_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with opener.open(f"{base}/api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
            request = urllib.request.Request(
                f"{base}/api/render",
                data=json.dumps(make_payload()).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(request, timeout=5) as response:
                rendered = json.loads(response.read().decode("utf-8"))
            self.assertEqual(rendered["metadata"]["slug"], "studio-check")
            self.assertIn("data:image/png;base64,", rendered["html"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
