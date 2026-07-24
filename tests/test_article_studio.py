from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import article_studio  # noqa: E402
from add_article import ValidationError  # noqa: E402


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEklEQVR4nGP4DwYMYMAEoQhxACK8BgFIJminAAAAAElFTkSuQmCC"
)
PNG_BYTES = base64.b64decode(PNG_DATA_URL.split(",", 1)[1])
SECOND_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAGklEQVR4nGP8zxDAAANMcBYDAwMjQ8V/7DIAZTACzSlBxwcAAAAASUVORK5CYII="
)


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
        if url.startswith("https://publish.x.com/oembed?"):
            if "limit=6" in url:
                return FakeResponse(json.dumps({
                    "url": "https://x.com/Test_User",
                    "html": (
                        '<a class="twitter-timeline" href="https://x.com/Test_User">'
                        'Posts by Test_User</a>'
                    ),
                }).encode("utf-8"), url=url)
            return FakeResponse(json.dumps({
                "url": "https://x.com/Test_User/status/1900000000000000001",
                "author_name": "テスト投稿者",
                "author_url": "https://x.com/Test_User",
                "html": (
                    '<blockquote class="twitter-tweet"><p lang="ja" dir="ltr">'
                    '無料投稿の本文です。 <a href="https://x.com/hashtag/test">#test</a>'
                    '</p>&mdash; テスト投稿者 (@Test_User) '
                    '<a href="https://twitter.com/Test_User/status/1900000000000000001?ref_src=twsrc">'
                    'July 18, 2026</a></blockquote>'
                ),
            }, ensure_ascii=False).encode("utf-8"), url=url)
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


class FakeSourceOpener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def open(self, request: urllib.request.Request, timeout: int = 20) -> FakeResponse:
        url = request.full_url
        self.urls.append(url)
        if url == "https://news.example.com/cosplay/story":
            return FakeResponse(
                (
                    '<!doctype html><html lang="ja"><head>'
                    '<title>ページ側タイトル</title>'
                    '<meta property="og:title" content="注目コスプレイヤーの新作が話題">'
                    '<meta property="og:description" content="公開された新作写真と活動内容を紹介します。">'
                    '<meta property="og:site_name" content="テストニュース">'
                    '<meta property="og:image" content="/media/main.png">'
                    '<link rel="canonical" href="https://news.example.com/cosplay/story">'
                    '</head><body><main><h1>注目コスプレイヤーの新作が話題</h1>'
                     '<p>今回公開された写真には、衣装や撮影場所へのこだわりが詰まっています。</p>'
                     '<img src="/media/duplicate.png" alt="主画像のサイズ違い" width="300" height="300">'
                     '<img src="/media/second.png" alt="公開された二枚目の写真" width="600" height="900">'
                     '<video class="article-player" width="640" height="360"><source type="video/mp4" src="/media/main.mp4"></video>'
                     '<iframe class="chat-ad" src="https://ads.example.net/player"></iframe>'
                     '</main></body></html>'
                ).encode("utf-8"),
                content_type="text/html; charset=utf-8",
                url=url,
            )
        if url == "https://news.example.com/media/main.png":
            return FakeResponse(PNG_BYTES, content_type="image/png", url=url)
        if url == "https://news.example.com/media/duplicate.png":
            return FakeResponse(PNG_BYTES + b"x", content_type="image/png", url=url)
        if url == "https://news.example.com/media/second.png":
            return FakeResponse(SECOND_PNG_BYTES, content_type="image/png", url=url)
        raise AssertionError(f"unexpected URL: {url}")


class FakeCodexRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], dict[str, object]]] = []

    def status(self) -> dict[str, object]:
        return {"available": True, "version": "codex-test", "message": "Codex接続済み"}

    def analyze(self, source: dict[str, object]) -> dict[str, object]:
        images = source.get("images", [])
        videos = source.get("videos", [])
        return {
            "title": "注目コスプレイヤーの新作写真を分析",
            "description": "衣装と撮影場所にこだわった新作写真が公開された。",
            "category": "画像",
            "analysis_summary": "本文と画像候補を照合し、記事本体の画像だけを選別した。",
            "image_decisions": [
                {
                    "image_id": item["id"],
                    "verdict": "article" if index == 0 else "advertisement",
                    "role": "article_main" if index == 0 else "advertisement",
                    "recommended_use": "thumbnail_and_body" if index == 0 else "exclude",
                    "content_group": "main-subject" if index == 0 else "",
                    "relation": "記事本文の中心素材" if index == 0 else "記事外の誘導画像",
                    "relevance_score": 94 if index == 0 else 8,
                    "reason": "記事の主画像" if index == 0 else "記事外の誘導画像",
                }
                for index, item in enumerate(images)
            ],
            "video_decisions": [
                {
                    "video_id": item["id"],
                    "verdict": "article" if item.get("kind") == "direct" else "advertisement",
                    "relevance_score": 96 if item.get("kind") == "direct" else 4,
                    "reason": "記事本文の動画" if item.get("kind") == "direct" else "外部チャット広告",
                }
                for item in videos
            ],
        }

    def generate(self, source: dict[str, object], options: dict[str, object]) -> dict[str, object]:
        self.calls.append((source, options))
        selected_video_ids = [
            f"source-video-{index}"
            for index, _video_id in enumerate(options.get("selected_video_ids", []), start=1)
        ]
        return {
            "title": "【画像】衣装と撮影のこだわりに注目集まる",
            "summary": "公開された新作写真について、衣装と撮影場所の見どころをまとめた。",
            "category": "画像",
            "tags": ["画像", "コスプレ"],
            "responses": [
                {"text": "これ貼っとく", "style": "large", "video_ids": selected_video_ids[:2]},
                {"text": "撮影場所の選び方も雰囲気に合ってるな", "style": "normal", "video_ids": []},
                {"text": "続きの作品も見てみたい", "style": "highlight", "video_ids": selected_video_ids[2:4]},
                {"text": "元ページにほかの写真も載っている", "style": "normal", "video_ids": []},
                {"text": "公開時期も確認しておきたい", "style": "normal", "video_ids": selected_video_ids[4:6]},
            ],
        }


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
    def test_fanza_product_card_renders_a_sponsored_purchase_link(self) -> None:
        payload = make_payload()
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://al.dmm.co.jp/?lurl=product",
            "title": "テスト作品",
            "text": "サンプルと価格を確認できます。",
            "button_text": "FANZAで作品を見る",
        })
        build = article_studio.build_article(payload, ROOT, preview=True)
        self.assertIn('class="fanza-product"', build.article_html)
        self.assertIn('href="https://al.dmm.co.jp/?lurl=product"', build.article_html)
        self.assertIn('rel="sponsored noopener noreferrer"', build.article_html)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.site_root = Path(self.temporary.name)
        (self.site_root / "articles").mkdir(parents=True)
        (self.site_root / "data").mkdir(parents=True)
        shutil.copy2(ROOT / "articles" / "pool-look-back.html", self.site_root / "articles" / "pool-look-back.html")
        (self.site_root / "data" / "articles.json").write_text("[]\n", encoding="utf-8")

    def test_source_url_normalizes_unicode_paths_and_queries(self) -> None:
        normalized = article_studio._validate_source_url(
            "https://例え.jp/画像/制服 写真.jpg?名前=テスト"
        )

        self.assertEqual(
            normalized,
            "https://xn--r8jz45g.jp/%E7%94%BB%E5%83%8F/%E5%88%B6%E6%9C%8D%20%E5%86%99%E7%9C%9F.jpg?%E5%90%8D%E5%89%8D=%E3%83%86%E3%82%B9%E3%83%88",
        )
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
        self.assertIn('src="images/image-01.png"', final.article_html)

    def test_x_username_validation(self) -> None:
        self.assertEqual(article_studio.normalize_x_username("@Test_User"), "Test_User")
        self.assertEqual(article_studio.normalize_x_username("https://twitter.com/Test_User/"), "Test_User")
        with self.assertRaisesRegex(ValidationError, "1 to 15"):
            article_studio.normalize_x_username("bad-name")

    def test_x_account_prompt_uses_natural_reactions_without_follow_cta(self) -> None:
        prompt = article_studio._codex_prompt(
            {
                "source_type": "x_profile",
                "url": "https://x.com/Test_User",
                "title": "Test User",
                "editorial_intent": {
                    "content_mode": "x_account",
                    "promotion_type": "organic",
                    "editorial_brief": "写真の雰囲気を中心に",
                },
                "images": [],
                "videos": [],
            },
            {"category": "SNS", "reply_count": "5"},
        )

        self.assertIn("結果として本人の良さが伝わる", prompt)
        self.assertIn("フォローして損はない", prompt)
        self.assertIn("行動を読者へ促さない", prompt)
        self.assertNotIn("private_note", prompt)

    def test_free_x_oembed_draft_needs_no_bearer_token(self) -> None:
        opener = FakeXOpener()
        canonical, username, post_id = article_studio.normalize_x_post_url(
            "https://twitter.com/Test_User/status/1900000000000000001/photo/1?ref=test"
        )
        self.assertEqual(canonical, "https://x.com/Test_User/status/1900000000000000001")
        self.assertEqual(username, "Test_User")
        self.assertEqual(post_id, "1900000000000000001")

        draft = article_studio.build_x_free_draft_payload(
            [canonical],
            {
                "name": "creator.png",
                "data_url": PNG_DATA_URL,
                "alt": "投稿者本人の公開画像",
                "orientation": "landscape",
            },
            opener,
        )
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["images"][0]["id"], "x-cover")
        self.assertEqual(draft["blocks"][1]["text"], "無料投稿の本文です。 #test")
        self.assertTrue(any(url.startswith("https://publish.x.com/oembed?") for url in opener.urls))
        self.assertFalse(any("api.x.com" in url for url in opener.urls))

        final = article_studio.build_article(draft, self.site_root)
        self.assertIn("platform.twitter.com/widgets.js", final.article_html)
        self.assertIn(canonical, final.article_html)
        self.assertIn('src="images/image-01.png"', final.article_html)

        timeline_draft = article_studio.build_x_free_draft_payload(
            ["https://x.com/Test_User"],
            {
                "name": "creator.png",
                "data_url": PNG_DATA_URL,
                "alt": "投稿者本人の公開画像",
                "orientation": "landscape",
            },
            opener,
        )
        self.assertEqual(timeline_draft["blocks"][1]["type"], "x_timeline")
        self.assertEqual(timeline_draft["blocks"][1]["limit"], 6)
        timeline = article_studio.build_article(timeline_draft, self.site_root)
        self.assertIn('class="twitter-timeline"', timeline.article_html)
        self.assertIn("https://x.com/Test_User", timeline.article_html)
        self.assertIn('src="images/image-01.png"', timeline.article_html)

    def test_url_analysis_builds_an_editable_article_with_source_images(self) -> None:
        opener = FakeSourceOpener()
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            opener,
        )

        self.assertEqual(source["title"], "注目コスプレイヤーの新作が話題")
        self.assertEqual(source["site_name"], "テストニュース")
        self.assertEqual(len(source["images"]), 2)
        self.assertEqual(len(source["videos"]), 2)
        self.assertEqual(source["videos"][0]["kind"], "direct")
        self.assertEqual(source["videos"][1]["kind"], "iframe")
        self.assertEqual(source["images"][0]["orientation"], "portrait")
        poster_buffer = BytesIO()
        Image.new("RGB", (12, 20), "#8c3a45").save(poster_buffer, format="JPEG")
        source["videos"][0]["frame_data"] = poster_buffer.getvalue()

        draft = article_studio.build_source_draft_payload(
            source,
            ["media-1", "media-2"],
            selected_video_ids=["video-1"],
        )
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["rights_status"], "unconfirmed")
        self.assertEqual(draft["source_label"], "テストニュース")
        self.assertEqual(len(draft["images"]), 2)
        self.assertEqual(len(draft["videos"]), 1)
        self.assertEqual(draft["videos"][0]["poster"], "")
        self.assertTrue(draft["videos"][0]["poster_data_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(len([block for block in draft["blocks"] if block["type"] == "videos"]), 1)
        self.assertGreaterEqual(len([block for block in draft["blocks"] if block["type"] == "images"]), 1)
        self.assertTrue(draft["title"].startswith("【動画】"))
        self.assertTrue(draft["fictional_responses"])

        final = article_studio.build_article(draft, self.site_root)
        self.assertIn('src="images/image-01.png"', final.article_html)
        self.assertIn('<video class="article-video"', final.article_html)
        preview = article_studio.build_article(draft, self.site_root, preview=True)
        self.assertIn('href="indanya-video://play/source-video-1"', preview.article_html)
        self.assertIn('class="video-native-thumb"', preview.article_html)
        self.assertIn("動画を再生", preview.article_html)
        self.assertIn(draft["videos"][0]["poster_data_url"], preview.article_html)
        self.assertIn(draft["videos"][0]["poster_data_url"], final.article_html)

        without_poster = json.loads(json.dumps(draft))
        without_poster["videos"][0]["poster_data_url"] = ""
        missing_preview = article_studio.build_article(without_poster, self.site_root, preview=True)
        self.assertIn("この動画のサムネイルを取得できませんでした", missing_preview.article_html)
        self.assertNotIn('poster="images/image-01.png"', article_studio.build_article(without_poster, self.site_root).article_html)

        draft.update({
            "adult_confirmed": True,
            "rights_confirmed": True,
            "privacy_confirmed": True,
            "source_confirmed": True,
        })
        result = article_studio.add_built_article(draft, self.site_root)
        self.assertEqual(result["slug"], draft["slug"])
        self.assertTrue((self.site_root / "articles" / f"{draft['slug']}.html").is_file())

    def test_codex_result_replaces_template_responses_without_losing_images(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        base = article_studio.build_source_draft_payload(source, ["media-1", "media-2"])
        generated = FakeCodexRunner().generate(source, {"reply_count": "5"})
        payload = article_studio.apply_codex_result(base, generated)

        self.assertEqual(payload["generation_method"], "codex")
        self.assertEqual(payload["comments"], 5)
        self.assertEqual(len([block for block in payload["blocks"] if block["type"] == "post"]), 5)
        self.assertEqual(
            sorted(image_id for block in payload["blocks"] for image_id in block.get("image_ids", [])),
            ["source-image-1", "source-image-2"],
        )
        article_studio.build_article(payload, self.site_root)

    def test_thumbnail_only_image_is_not_inserted_into_article_body(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        source["images"][0]["ai_recommended_use"] = "thumbnail"
        source["images"][1]["ai_recommended_use"] = "body"

        draft = article_studio.build_source_draft_payload(
            source,
            ["media-2"],
            thumbnail_image_id="media-1",
        )

        self.assertEqual("source-image-1", draft["thumbnail_id"])
        self.assertTrue(draft["thumbnail_only"])
        body_ids = [
            image_id
            for block in draft["blocks"]
            if block["type"] == "images"
            for image_id in block["image_ids"]
        ]
        self.assertEqual(["source-image-2"], body_ids)
        self.assertNotIn("source-image-1", body_ids)

    def test_x_profile_places_gallery_images_after_timeline_cover(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        source.update({
            "source_type": "x_profile",
            "url": "https://x.com/Test_User",
            "site_name": "X",
            "x_info": {"username": "Test_User"},
            "x_embed": {
                "url": "https://x.com/Test_User",
                "username": "Test_User",
                "limit": 6,
            },
        })

        draft = article_studio.build_source_draft_payload(
            source,
            ["media-2"],
            thumbnail_image_id="media-1",
        )

        timeline = next(block for block in draft["blocks"] if block["type"] == "x_timeline")
        gallery = next(block for block in draft["blocks"] if block["type"] == "images")
        self.assertEqual(["source-image-1"], timeline["image_ids"])
        self.assertEqual(["source-image-2"], gallery["image_ids"])
        article_studio.build_article(draft, self.site_root, preview=True)

    def test_codex_title_normalization_uses_media_kind_and_image_count(self) -> None:
        self.assertEqual(
            article_studio._normalize_codex_title(
                "ヒョウ柄ビキニ美女の動画に5ch民が反応",
                "動画",
                1,
            ),
            "【動画】ヒョウ柄ビキニ美女の動画",
        )
        self.assertEqual(
            article_studio._normalize_codex_title(
                "黒髪ボブ美女の後ろ姿画像まとめ",
                "画像",
                1,
            ),
            "【画像】黒髪ボブ美女の後ろ姿画像",
        )
        self.assertEqual(
            article_studio._normalize_codex_title(
                "夏のコスプレ画像まとめ",
                "画像",
                3,
            ),
            "【画像】夏のコスプレ画像まとめ",
        )

    def test_codex_prompt_requires_url_and_visual_clues_for_title(self) -> None:
        prompt = article_studio._codex_prompt(
            {
                "source_type": "web",
                "url": "https://example.com/article",
                "title": "夏の水泳部女子大生",
                "description": "シャワー室で撮影された写真",
                "images": [],
            },
            {"reply_count": "5", "category": "画像"},
            [{
                "id": "media-1",
                "filename": "attachment.jpg",
                "alt": "日焼け跡のある後ろ姿",
                "ai_reason": "記事の主画像",
            }],
        )
        self.assertIn("元タイトル、本文、画像、動画情報を照合", prompt)
        self.assertIn("このページ固有の見どころ", prompt)
        self.assertIn("固定の構文、文字数、語尾、俗語へ当てはめない", prompt)
        self.assertIn("全員を親切、機転が利く、物分かりがよい人物にしない", prompt)
        self.assertIn("無難な抽象語へ一律に言い換えて内容を隠さない", prompt)
        self.assertIn("卑猥な単語を入れること自体を目的やノルマにしない", prompt)
        self.assertIn("最大限に下品にすることを混同しない", prompt)
        self.assertIn("読者がジャンルやフィクション上の設定として読む編集語", prompt)
        self.assertIn("毎回「風」「コスプレ」「設定上」「成人女性」などの注釈を足して", prompt)

    def test_recent_draft_language_is_supplied_only_for_repetition_avoidance(self) -> None:
        draft_root = self.site_root / ".article-studio" / "drafts"
        draft_root.mkdir(parents=True, exist_ok=True)
        (draft_root / "recent.json").write_text(
            json.dumps({
                "title": "【画像】前の記事固有のタイトル",
                "blocks": [
                    {"type": "post", "text": "前の記事だけで使った反応"},
                    {"type": "images", "image_ids": ["source-image-1"]},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        history = article_studio._recent_draft_language(self.site_root)
        prompt = article_studio._codex_prompt(
            {"url": "https://example.com/new", "title": "新しい題材", "images": []},
            {"recent_language": history},
        )

        self.assertEqual(history[0]["title"], "【画像】前の記事固有のタイトル")
        self.assertEqual(history[0]["responses"], ["前の記事だけで使った反応"])
        self.assertIn("コピー禁止・重複回避用", prompt)
        self.assertIn("前の記事だけで使った反応", prompt)

    def test_codex_refinement_rejects_editor_voice_and_mixed_video_roles(self) -> None:
        prompt = article_studio._codex_refinement_prompt(
            {"url": "https://example.com/video", "title": "元タイトル", "description": "説明", "excerpts": []},
            {"reply_count": "5", "selected_video_ids": ["video-1"]},
            {
                "title": "【動画】成人向け配信の距離感が近い",
                "summary": "動画記事です。",
                "category": "動画",
                "tags": ["動画"],
                "responses": [
                    {"text": "配信系、まずこれ貼っとく", "style": "normal", "video_ids": ["video-1"]},
                    {"text": "見どころが目立つ", "style": "large", "video_ids": []},
                    {"text": "これは当たり", "style": "normal", "video_ids": []},
                    {"text": ">>2 わかる", "style": "normal", "video_ids": []},
                    {"text": "ええやん", "style": "normal", "video_ids": []},
                ],
            },
        )
        self.assertIn("全員が元投稿を正確に理解し", prompt)
        self.assertIn("video_ids付きレスは動画を投稿する側", prompt)
        self.assertIn("アンカーは会話を成立させる時だけ", prompt)
        self.assertIn("無難な抽象語だけで隠していないか", prompt)
        self.assertIn("下品さ自体が主役になっていないか", prompt)

    def test_codex_analysis_accepts_page_specific_image_roles(self) -> None:
        source = {
            "images": [{"id": "media-1"}],
            "videos": [],
        }
        result = article_studio._validate_codex_analysis({
            "title": "画像の関係を読んだ記事",
            "description": "ページ固有の画像関係を判断した。",
            "category": "画像",
            "analysis_summary": "固定分類にない役割もページから判断した。",
            "image_decisions": [{
                "image_id": "media-1",
                "verdict": "article",
                "role": "本文冒頭へ誘導するためだけの加工済み予告カット",
                "recommended_use": "thumbnail",
                "content_group": "subject-a",
                "relation": "後続する鮮明版と同じ場面を加工したもの",
                "relevance_score": 92,
                "reason": "記事カードと本文の関係から判断",
            }],
            "video_decisions": [],
        }, source)

        decision = result["image_decisions"][0]
        self.assertEqual("本文冒頭へ誘導するためだけの加工済み予告カット", decision["role"])
        self.assertEqual("thumbnail", decision["recommended_use"])

    def test_codex_video_responses_attach_media_only_to_posting_lines(self) -> None:
        generated = article_studio._validate_codex_result(
            {
                "title": "韓国配信の動画まとめ",
                "summary": "動画5本を紹介する記事。",
                "category": "動画",
                "tags": ["動画"],
                "responses": [
                    {"text": "まず2本貼っとく", "style": "normal", "video_ids": ["video-1", "video-2"]},
                    {"text": "でっかｗ", "style": "large", "video_ids": []},
                    {"text": "次これ", "style": "normal", "video_ids": ["video-3"]},
                    {"text": "これは当たり", "style": "highlight", "video_ids": []},
                    {"text": "残りも置いとく", "style": "normal", "video_ids": ["video-4", "video-5"]},
                ],
            },
            requested_count="5",
            selected_media_count=5,
            selected_video_ids=[f"video-{index}" for index in range(1, 6)],
        )
        base = make_payload()
        base["thumbnail_id"] = "image-a"
        base["blocks"] = [{"id": "seed-post", "type": "post", "text": "仮レス", "style": "normal"}]
        base["videos"] = [
            {
                "id": f"source-video-{index}",
                "kind": "direct",
                "url": f"https://media.example.com/{index}.mp4",
                "mime_type": "video/mp4",
                "label": f"動画 {index}",
            }
            for index in range(1, 6)
        ]
        for response in generated["responses"]:
            response["video_ids"] = [video_id.replace("video-", "source-video-") for video_id in response["video_ids"]]
        payload = article_studio.apply_codex_result(base, generated)
        sequence = [block["type"] for block in payload["blocks"]]
        self.assertEqual(sequence[:7], ["post", "videos", "post", "post", "videos", "post", "post"])
        self.assertEqual(
            [block["video_ids"] for block in payload["blocks"] if block["type"] == "videos"],
            [["source-video-1", "source-video-2"], ["source-video-3"], ["source-video-4", "source-video-5"]],
        )
        article_studio.build_article(payload, self.site_root)

    def test_codex_video_placement_repairs_missing_and_duplicate_ids(self) -> None:
        generated = article_studio._validate_codex_result(
            {
                "title": "動画5本まとめ",
                "summary": "動画5本の記事。",
                "category": "動画",
                "tags": ["動画"],
                "responses": [
                    {"text": "まずこれ", "style": "normal", "video_ids": ["video-2", "video-2"]},
                    {"text": "これは強い", "style": "large", "video_ids": []},
                    {"text": "わかる", "style": "normal", "video_ids": ["unknown"]},
                    {"text": ">>2 ええやん", "style": "normal", "video_ids": []},
                    {"text": "まだある", "style": "highlight", "video_ids": ["video-4"]},
                ],
            },
            requested_count="5",
            selected_media_count=5,
            selected_video_ids=[f"video-{index}" for index in range(1, 6)],
        )

        placed = [video_id for response in generated["responses"] for video_id in response["video_ids"]]
        self.assertEqual(set(placed), {f"video-{index}" for index in range(1, 6)})
        self.assertEqual(len(placed), len(set(placed)))
        self.assertEqual("video-1", generated["responses"][0]["video_ids"][0])
        self.assertTrue(all(len(response["video_ids"]) <= 2 for response in generated["responses"]))

    def test_video_article_allows_extra_thumbnail_reference_images(self) -> None:
        payload = make_payload()
        payload["images"].append({
            "id": "image-b",
            "name": "second.png",
            "data_url": PNG_DATA_URL,
            "alt": "サムネイル候補2",
            "orientation": "landscape",
        })
        payload["videos"] = [{
            "id": "source-video-1",
            "kind": "direct",
            "url": "https://media.example.com/1.mp4",
            "mime_type": "video/mp4",
            "label": "動画 1",
        }]
        payload["blocks"] = [
            {"id": "post-a", "type": "post", "text": "これ置いとく", "style": "normal"},
            {"id": "video-a", "type": "videos", "video_ids": ["source-video-1"]},
            {"id": "post-b", "type": "post", "text": "ええやん", "style": "large"},
        ]

        rendered = article_studio.build_article(payload, self.site_root, preview=True)
        self.assertIn("source-video-1", rendered.payload["videos"][0]["id"])

    def test_codex_job_saves_draft_and_registers_permission_status(self) -> None:
        fake_codex = FakeCodexRunner()
        server = article_studio.StudioServer(
            ("127.0.0.1", 0),
            self.site_root,
            url_opener=FakeSourceOpener(),
            codex_runner=fake_codex,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with opener.open(f"{base}/api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
            self.assertTrue(bootstrap["codex"]["available"])
            headers = {"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]}
            analyze_request = urllib.request.Request(
                f"{base}/api/source/analyze",
                data=json.dumps({"url": "https://news.example.com/cosplay/story"}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with opener.open(analyze_request, timeout=5) as response:
                analysis = json.loads(response.read().decode("utf-8"))
            generate_request = urllib.request.Request(
                f"{base}/api/source/generate",
                data=json.dumps({
                    "session_id": analysis["session_id"],
                    "selected_image_ids": analysis["recommended_image_ids"],
                    "selected_video_ids": analysis["recommended_video_ids"],
                    "category": "画像",
                    "reply_count": "5",
                    "tone": "thread",
                }).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with opener.open(generate_request, timeout=5) as response:
                job = json.loads(response.read().decode("utf-8"))["job"]

            completed = None
            for _ in range(100):
                job_request = urllib.request.Request(
                    f"{base}/api/jobs/{job['id']}",
                    headers={"X-Indanya-Token": bootstrap["token"]},
                )
                with opener.open(job_request, timeout=5) as response:
                    completed = json.loads(response.read().decode("utf-8"))["job"]
                if completed["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(completed)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(fake_codex.calls), 1)
            self.assertEqual(fake_codex.calls[0][1]["selected_image_ids"], ["media-1"])
            self.assertEqual(fake_codex.calls[0][1]["selected_video_ids"], ["video-1"])

            draft_path = self.site_root / ".article-studio" / "drafts" / f"{completed['slug']}.json"
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["generation_method"], "codex")
            self.assertEqual(payload["rights_status"], "unconfirmed")
            self.assertEqual(len(payload["videos"]), 1)

            rights_request = urllib.request.Request(
                f"{base}/api/rights/{completed['slug']}",
                data=json.dumps({
                    "rights_status": "requested",
                    "rights_contact": "@creator",
                    "rights_note": "DM送信済み",
                }).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with opener.open(rights_request, timeout=5) as response:
                rights = json.loads(response.read().decode("utf-8"))
            self.assertEqual(rights["rights_status"], "requested")
            updated = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["rights_contact"], "@creator")
            self.assertFalse(updated["rights_confirmed"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

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
            server.desktop_preview_html = rendered["html"]
            with opener.open(f"{base}/desktop-preview.html", timeout=5) as response:
                desktop_preview = response.read().decode("utf-8")
            with opener.open(f"{base}/preview.css", timeout=5) as response:
                preview_css = response.read().decode("utf-8")
            self.assertIn('<link rel="stylesheet" href="/preview.css">', desktop_preview)
            self.assertIn(".article-title", preview_css)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_local_free_x_endpoint_builds_a_draft_without_a_token(self) -> None:
        fake_x = FakeXOpener()
        server = article_studio.StudioServer(
            ("127.0.0.1", 0),
            self.site_root,
            x_bearer_token="",
            url_opener=fake_x,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with opener.open(f"{base}/api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
            self.assertFalse(bootstrap["x_token_configured"])
            request = urllib.request.Request(
                f"{base}/api/x/free-draft",
                data=json.dumps({
                    "post_urls": ["https://x.com/Test_User/status/1900000000000000001"],
                    "cover_image": {
                        "name": "creator.png",
                        "data_url": PNG_DATA_URL,
                        "alt": "投稿者本人の公開画像",
                        "orientation": "landscape",
                    },
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(request, timeout=5) as response:
                draft = json.loads(response.read().decode("utf-8"))["payload"]
            self.assertEqual(draft["blocks"][1]["type"], "x_embed")
            self.assertEqual(draft["thumbnail_id"], "x-cover")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_local_url_endpoint_analyzes_media_and_builds_a_draft(self) -> None:
        fake_source = FakeSourceOpener()
        server = article_studio.StudioServer(
            ("127.0.0.1", 0),
            self.site_root,
            url_opener=fake_source,
            codex_runner=FakeCodexRunner(),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with opener.open(f"{base}/api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
            analyze_request = urllib.request.Request(
                f"{base}/api/source/analyze",
                data=json.dumps({"url": "https://news.example.com/cosplay/story"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(analyze_request, timeout=5) as response:
                analysis = json.loads(response.read().decode("utf-8"))
            self.assertEqual(analysis["source"]["site_name"], "テストニュース")
            self.assertEqual(len(analysis["images"]), 2)
            self.assertEqual(analysis["recommended_image_ids"], ["media-1"])
            self.assertEqual(analysis["images"][1]["ai_verdict"], "advertisement")

            with opener.open(f"{base}{analysis['images'][0]['preview_url']}", timeout=5) as response:
                self.assertEqual(response.read(), SECOND_PNG_BYTES)

            draft_request = urllib.request.Request(
                f"{base}/api/source/draft",
                data=json.dumps({
                    "session_id": analysis["session_id"],
                    "selected_image_ids": analysis["recommended_image_ids"],
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Indanya-Token": bootstrap["token"]},
                method="POST",
            )
            with opener.open(draft_request, timeout=5) as response:
                draft = json.loads(response.read().decode("utf-8"))["payload"]
            self.assertEqual(draft["rights_status"], "unconfirmed")
            self.assertEqual(len(draft["images"]), 1)
            self.assertEqual(draft["source_url"], "https://news.example.com/cosplay/story")
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
