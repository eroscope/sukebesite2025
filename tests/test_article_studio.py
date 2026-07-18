from __future__ import annotations

import base64
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
PNG_BYTES = base64.b64decode(PNG_DATA_URL.split(",", 1)[1])


class FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "application/json", url: str = "https://api.x.com/") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]

    def geturl(self) -> str:
        return self.url


class FakeXOpener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def open(self, request: urllib.request.Request, timeout: int = 20) -> FakeResponse:
        url = request.full_url
        self.urls.append(url)
        if "/users/by/username/" in url:
            return FakeResponse(json.dumps({
                "data": {
                    "id": "12345",
                    "name": "テスト投稿者",
                    "username": "Test_User",
                    "description": "公開プロフィール",
                    "profile_image_url": "https://pbs.twimg.com/profile_images/test_normal.jpg",
                    "protected": False,
                    "verified": False,
                    "public_metrics": {"followers_count": 3456},
                }
            }, ensure_ascii=False).encode("utf-8"), url=url)
        if "/users/12345/tweets" in url:
            return FakeResponse(json.dumps({
                "data": [
                    {
                        "id": "1900000000000000001",
                        "text": "公開投稿の本文です。",
                        "created_at": "2026-07-18T08:30:00.000Z",
                        "lang": "ja",
                        "possibly_sensitive": True,
                        "public_metrics": {"like_count": 120, "retweet_count": 8, "reply_count": 4},
                        "attachments": {"media_keys": ["3_photo"]},
                    },
                    {
                        "id": "1900000000000000002",
                        "text": "画像のない投稿",
                        "created_at": "2026-07-18T07:30:00.000Z",
                    },
                ],
                "includes": {
                    "media": [{
                        "media_key": "3_photo",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/test.png",
                        "alt_text": "投稿者が公開したテスト画像",
                        "width": 1200,
                        "height": 800,
                    }]
                },
            }, ensure_ascii=False).encode("utf-8"), url=url)
        if url == "https://pbs.twimg.com/media/test.png":
            return FakeResponse(PNG_BYTES, content_type="image/png", url=url)
        if url == "https://pbs.twimg.com/profile_images/test_normal.jpg":
            return FakeResponse(PNG_BYTES, content_type="image/png", url=url)
        raise AssertionError(f"unexpected URL: {url}")


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

    def test_x_account_import_builds_official_embed_draft(self) -> None:
        opener = FakeXOpener()
        result = article_studio.fetch_x_candidates("https://x.com/Test_User", "test-token", opener)

        self.assertEqual(result["account"]["username"], "Test_User")
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(result["posts"][0]["media"][0]["media_key"], "3_photo")
        self.assertTrue(result["posts"][0]["possibly_sensitive"])

        draft = article_studio.build_x_draft_payload(
            result,
            ["1900000000000000001"],
            "3_photo",
            opener,
        )
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["thumbnail_id"], "x-cover")
        self.assertEqual(draft["blocks"][1]["type"], "x_embed")
        self.assertEqual(draft["blocks"][1]["text"], "公開投稿の本文です。")
        self.assertFalse(draft["rights_confirmed"])

        preview = article_studio.build_article(draft, self.site_root, preview=True)
        final = article_studio.build_article(draft, self.site_root)
        self.assertIn('class="twitter-tweet"', preview.article_html)
        self.assertNotIn("platform.twitter.com/widgets.js", preview.article_html)
        self.assertIn("platform.twitter.com/widgets.js", final.article_html)
        self.assertIn("https://x.com/Test_User/status/1900000000000000001", final.article_html)

    def test_x_username_validation(self) -> None:
        self.assertEqual(article_studio.normalize_x_username("@Test_User"), "Test_User")
        self.assertEqual(article_studio.normalize_x_username("https://twitter.com/Test_User/"), "Test_User")
        with self.assertRaisesRegex(ValidationError, "1 to 15"):
            article_studio.normalize_x_username("bad-name")

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

    def test_local_x_api_creates_a_draft_without_exposing_configured_token(self) -> None:
        fake_x = FakeXOpener()
        server = article_studio.StudioServer(
            ("127.0.0.1", 0),
            self.site_root,
            x_bearer_token="configured-token",
            url_opener=fake_x,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with opener.open(f"{base}/api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
            self.assertTrue(bootstrap["x_token_configured"])
            self.assertNotIn("configured-token", json.dumps(bootstrap))

            account_request = urllib.request.Request(
                f"{base}/api/x/account",
                data=json.dumps({"username": "@Test_User"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(account_request, timeout=5) as response:
                account = json.loads(response.read().decode("utf-8"))
            with opener.open(
                f"{base}/api/x/media/{account['session_id']}/3_photo", timeout=5
            ) as response:
                self.assertEqual(response.read(), PNG_BYTES)
            with opener.open(
                f"{base}/api/x/avatar/{account['session_id']}", timeout=5
            ) as response:
                self.assertEqual(response.read(), PNG_BYTES)

            draft_request = urllib.request.Request(
                f"{base}/api/x/draft",
                data=json.dumps({
                    "session_id": account["session_id"],
                    "selected_post_ids": ["1900000000000000001"],
                    "cover_media_key": "3_photo",
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(draft_request, timeout=5) as response:
                draft = json.loads(response.read().decode("utf-8"))["payload"]
            self.assertEqual(draft["images"][0]["id"], "x-cover")
            self.assertEqual(draft["blocks"][1]["type"], "x_embed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
