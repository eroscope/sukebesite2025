from __future__ import annotations

import base64
import json
import re
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
from add_article import ValidationError, normalize_article_html  # noqa: E402
from indanya_desktop.fanza_affiliate import save_fanza_settings  # noqa: E402


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
            "adult_content": True,
            "adult_reason": "成人向け画像を扱うテストページ",
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
        "tags": ["テスト", "画像", "成人向け"],
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
    def test_codex_output_schemas_require_every_declared_object_property(self) -> None:
        schema_paths = (
            ROOT / "tools" / "article_studio_codex_analysis_schema.json",
            ROOT / "tools" / "article_studio_codex_schema.json",
            ROOT / "tools" / "social_profile_verification_schema.json",
            ROOT / "tools" / "x_trend_templates_schema.json",
        )

        def verify(node: object, location: str = "$") -> None:
            if isinstance(node, dict):
                self.assertNotIn("uniqueItems", node, location)
                if node.get("type") == "object" and node.get("additionalProperties") is False:
                    properties = set((node.get("properties") or {}).keys())
                    required = set(node.get("required") or [])
                    self.assertEqual(properties, required, location)
                for key, value in node.items():
                    verify(value, f"{location}/{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    verify(value, f"{location}/{index}")

        for path in schema_paths:
            verify(json.loads(path.read_text(encoding="utf-8")), path.name)

    def test_media_count_prefixes_are_normalized(self) -> None:
        self.assertEqual(
            "【画像】記事タイトル",
            article_studio.normalize_article_title_label("【画像25枚】記事タイトル"),
        )
        self.assertEqual(
            "【画像＋動画】記事タイトル",
            article_studio.normalize_article_title_label("【動画1本＋画像11枚】記事タイトル"),
        )
        self.assertEqual(
            "【画像＋インスタ】記事タイトル",
            article_studio.normalize_article_title_label("【画像10枚＋インスタ】記事タイトル"),
        )
        self.assertEqual(
            "【画像＋動画＋X】記事タイトル",
            article_studio.normalize_article_title_label("【動画1本＆画像9枚＋X】記事タイトル"),
        )
        self.assertEqual(
            "【GIF】記事タイトル",
            article_studio.normalize_article_title_label("【GIF2本】記事タイトル"),
        )
        self.assertEqual(
            "【画像】ベッドで絡むセックス画像",
            article_studio.normalize_article_title_label(
                "【画像】ベッドで絡むセックス画像100枚"
            ),
        )

    def test_public_article_meta_shows_media_type_without_numeric_counts(self) -> None:
        payload = make_payload()
        image_only = article_studio.build_article(payload, self.site_root, preview=True)
        self.assertIn("<span>画像</span>", image_only.article_html)
        self.assertNotIn("画像1枚", image_only.article_html)

        payload["videos"] = [{
            "id": "video-a",
            "kind": "iframe",
            "url": "https://www.dmm.co.jp/service/digitalapi/-/html5_player/=/cid=test001/",
            "label": "公式サンプル",
        }]
        payload["blocks"].insert(2, {
            "id": "videos-a",
            "type": "videos",
            "video_ids": ["video-a"],
        })
        mixed = article_studio.build_article(payload, self.site_root, preview=True)
        self.assertIn("<span>画像＋動画</span>", mixed.article_html)
        self.assertNotIn("動画1本", mixed.article_html)

    def test_legacy_unverified_product_cta_is_removed_when_loaded(self) -> None:
        payload = {
            "source_url": "https://example.com/article",
            "blocks": [
                {"id": "images", "type": "images", "image_ids": ["image-1"]},
                {
                    "id": "legacy-pr",
                    "type": "product_cta",
                    "url": "https://video.dmm.co.jp/av/content/?id=unrelated001",
                    "title": "無関係な旧おすすめ",
                },
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }

        sanitized = article_studio._sanitize_legacy_product_ctas(payload)

        self.assertEqual(
            ["images", "ad"],
            [block["type"] for block in sanitized["blocks"]],
        )

    def test_legacy_source_exact_product_moves_below_video(self) -> None:
        payload = {
            "source_url": "https://video.dmm.co.jp/av/content/?id=AAA-001",
            "blocks": [
                {"id": "lead", "type": "images", "image_ids": ["image-1"]},
                {"id": "sample", "type": "videos", "video_ids": ["video-1"]},
                {"id": "gallery", "type": "images", "image_ids": ["image-2"]},
                {
                    "id": "legacy-pr",
                    "type": "product_cta",
                    "url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                    "title": "元記事と同じ作品",
                },
                {
                    "id": "unrelated-pr",
                    "type": "product_cta",
                    "url": "https://video.dmm.co.jp/av/content/?id=bbb002",
                    "title": "無関係な旧おすすめ",
                },
            ],
        }

        sanitized = article_studio._sanitize_legacy_product_ctas(payload)

        self.assertEqual(
            ["images", "videos", "product_cta", "images"],
            [block["type"] for block in sanitized["blocks"]],
        )
        product = sanitized["blocks"][2]
        self.assertEqual("exact_video", product["match_type"])
        self.assertEqual("この動画の商品", product["placement_label"])
        self.assertEqual(100, product["match_confidence"])

    def test_fanza_official_iframe_embed_renders_as_video_block(self) -> None:
        payload = make_payload()
        payload["videos"] = [{
            "id": "fanza-video-1",
            "kind": "iframe",
            "url": "https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/",
            "mime_type": "text/html",
            "label": "FANZA公式サンプル動画",
            "rights_basis": "fanza_official_embed",
            "rights_source_url": "https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/",
            "width": 720,
            "height": 480,
        }]
        payload["blocks"].insert(2, {
            "id": "fanza-videos",
            "type": "videos",
            "video_ids": ["fanza-video-1"],
        })

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn('<iframe class="article-video"', build.article_html)
        self.assertIn("https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/", build.article_html)
        self.assertIn('style="aspect-ratio: 720 / 480; height: auto;" scrolling="no"', build.article_html)
        self.assertEqual("fanza_official_embed", build.payload["videos"][0]["rights_basis"])

    def test_fanza_official_embed_rejects_non_dmm_iframe(self) -> None:
        payload = make_payload()
        payload["videos"] = [{
            "id": "bad-video",
            "kind": "iframe",
            "url": "https://example.com/player",
            "mime_type": "text/html",
            "label": "bad",
            "rights_basis": "fanza_official_embed",
        }]
        payload["blocks"].insert(2, {
            "id": "bad-videos",
            "type": "videos",
            "video_ids": ["bad-video"],
        })

        with self.assertRaisesRegex(ValidationError, "official FANZA/DMM iframe"):
            article_studio.build_article(payload, self.site_root, preview=True)

    def test_only_fanza_product_thumbnails_may_use_trusted_remote_images(self) -> None:
        html = (
            '<!doctype html><html><body><aside>'
            '<img class="fanza-product-thumb" '
            'src="https://awsimgsrc.dmm.co.jp/product.jpg?w=200">'
            '</aside></body></html>'
        )
        normalized = normalize_article_html(html, "test-article", set())
        self.assertIn("https://awsimgsrc.dmm.co.jp/product.jpg?w=200", normalized)
        with self.assertRaises(ValidationError):
            normalize_article_html(
                '<!doctype html><html><body>'
                '<img class="fanza-product-thumb" src="https://ads.example/product.jpg">'
                '</body></html>',
                "test-article",
                set(),
            )
        local = normalize_article_html(
            '<!doctype html><html><body>'
            '<img src="images/product.jpg">'
            '<img class="fanza-product-thumb" src="images/product.jpg">'
            '<img class="side-ad-link-thumb" src="images/product.jpg">'
            '</body></html>',
            "test-article",
            {"product.jpg"},
        )
        self.assertIn(
            'src="../assets/articles/test-article/product.jpg"',
            local,
        )
        self.assertEqual(3, local.count("product.jpg"))

    def test_fanza_product_card_renders_a_sponsored_purchase_link(self) -> None:
        save_fanza_settings(self.site_root, "article-owner-001")
        payload = make_payload()
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=test001",
            "title": "テスト作品",
            "text": "サンプルと価格を確認できます。",
            "button_text": "FANZAで作品を見る",
            "thumbnail_image_id": "image-a",
            "placement_label": "この画像の商品",
            "match_type": "exact_image",
            "match_evidence": "画像の横に同じ品番の商品リンクがある",
            "match_confidence": 98,
        })
        build = article_studio.build_article(payload, self.site_root, preview=True)
        self.assertIn('class="fanza-product"', build.article_html)
        self.assertIn('href="https://al.dmm.com/?lurl=', build.article_html)
        self.assertIn("af_id=article-owner-001", build.article_html)
        self.assertNotIn("al.dmm.co.jp", build.article_html)
        self.assertIn('rel="sponsored noopener noreferrer"', build.article_html)
        self.assertIn("border-left: 4px solid #c72d22", article_studio.FANZA_PRODUCT_STYLE)
        self.assertIn('class="fanza-product-thumb"', build.article_html)
        self.assertIn("この画像の商品 / PR", build.article_html)
        self.assertIn('data-pr-kind="exact_image"', build.article_html)
        self.assertIn('data-pr-confidence="98"', build.article_html)
        self.assertIn("配置: この画像の商品 / 一致度: 98%", build.article_html)
        self.assertNotIn('data-pr-id="article-related-footer-product"', build.article_html)
        self.assertNotIn("この記事で紹介している作品 / PR", build.article_html)
        self.assertNotIn('data-link-kind="inferred_topic_search"', build.article_html)
        self.assertNotIn("に近い作品", build.article_html)
        self.assertEqual(1, build.article_html.count('class="fanza-product-thumb"'))
        self.assertNotIn('class="side-ad-link-thumb"', build.article_html)
        self.assertNotIn("border: 2px solid #1a1a1a", article_studio.FANZA_PRODUCT_STYLE)

    def test_fanza_package_ownership_survives_article_validation(self) -> None:
        save_fanza_settings(self.site_root, "article-owner-package")
        payload = make_payload()
        payload["source_url"] = "https://video.dmm.co.jp/av/content/?id=test001"
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=test001",
            "title": "テスト作品",
            "thumbnail_url": "https://pics.dmm.co.jp/digital/video/test001/test001pl.jpg",
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": "https://video.dmm.co.jp/av/content/?id=test001",
            "match_type": "exact_image",
            "match_confidence": 100,
        })

        build = article_studio.build_article(payload, self.site_root, preview=True)
        product = next(
            block for block in build.payload["blocks"]
            if block.get("type") == "product_cta"
        )

        self.assertEqual("fanza_package", product["thumbnail_source_kind"])
        self.assertEqual(
            "https://video.dmm.co.jp/av/content/?id=test001",
            product["thumbnail_owner_url"],
        )

    def test_fanza_package_from_another_product_is_rejected(self) -> None:
        save_fanza_settings(self.site_root, "article-owner-package")
        payload = make_payload()
        payload["source_url"] = "https://video.dmm.co.jp/av/content/?id=test001"
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=test001",
            "title": "テスト作品",
            "thumbnail_url": "https://pics.dmm.co.jp/digital/video/other002/other002pl.jpg",
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": "https://video.dmm.co.jp/av/content/?id=other002",
            "match_type": "exact_image",
            "match_confidence": 100,
        })

        with self.assertRaisesRegex(ValidationError, "package thumbnail does not match"):
            article_studio.build_article(payload, self.site_root, preview=True)

    def test_fanza_product_preview_is_not_clickable_before_one_time_setup(self) -> None:
        payload = make_payload()
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=test001",
            "title": "テスト作品",
            "text": "作品情報",
            "button_text": "FANZAで作品を見る",
            "match_type": "exact_article",
            "match_confidence": 100,
        })

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn("アフィリエイトIDを保存すると自動反映", build.article_html)
        self.assertNotIn('class="fanza-product-button" href=', build.article_html)

    def test_fanza_product_cannot_publish_before_one_time_setup(self) -> None:
        payload = make_payload()
        payload["blocks"].insert(-1, {
            "id": "fanza-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=test001",
            "title": "テスト作品",
            "text": "作品情報",
            "button_text": "FANZAで作品を見る",
            "match_type": "exact_article",
            "match_confidence": 100,
        })

        with self.assertRaisesRegex(ValidationError, "アフィリエイトIDが未設定"):
            article_studio.build_article(payload, self.site_root)

    def test_unregistered_official_work_link_is_clickable_without_pr_label(self) -> None:
        payload = make_payload()
        payload["affiliate_opportunities"] = [{
            "program_id": "mgs",
            "product_code": "300MIUM-1293",
            "product_url": "https://www.mgstage.com/product/product_detail/300MIUM-1293/",
            "article_match": True,
        }]
        payload["blocks"].insert(-1, {
            "id": "official-work",
            "type": "related_link",
            "url": "https://www.mgstage.com/product/product_detail/300MIUM-1293/",
            "title": "MGS動画 300MIUM-1293",
            "text": "記事内で特定できた作品の公式ページです。",
            "button_text": "公式作品ページを見る",
            "placement_label": "この作品の公式ページ",
            "provider": "mgs",
            "link_kind": "exact_official_work",
            "match_evidence": "作品番号を確認",
            "match_confidence": 100,
            "affiliate_network": "",
            "affiliate_eligible": False,
        })

        build = article_studio.build_article(payload, self.site_root)

        self.assertIn('class="article-destination"', build.article_html)
        self.assertIn("公式作品ページを見る", build.article_html)
        self.assertIn("https://www.mgstage.com/product/product_detail/300MIUM-1293/", build.article_html)
        self.assertNotIn("この作品の公式ページ / PR", build.article_html)
        self.assertNotIn('data-pr-id="official-work"', build.article_html)

    def test_tiktoker_profile_is_rendered_as_a_non_pr_official_account(self) -> None:
        payload = make_payload()
        payload["blocks"].insert(2, {
            "id": "creator-tiktok",
            "type": "related_link",
            "url": "https://www.tiktok.com/@creator.name",
            "title": "紹介した人物のTikTok",
            "text": "記事で紹介した本人のTikTokです。",
            "button_text": "TikTokで見る",
            "placement_label": "本人の公式アカウント",
            "provider": "tiktok",
            "link_kind": "official_profile",
            "match_evidence": "元ページ内のTikTokリンクを確認",
            "match_confidence": 100,
            "affiliate_network": "",
            "affiliate_eligible": False,
            "thumbnail_url": "https://linktr.ee/og/image/creator.jpg",
            "thumbnail_source_kind": "official_hub_profile",
            "thumbnail_owner_url": "https://linktr.ee/creator.name",
        })

        build = article_studio.build_article(payload, self.site_root)

        self.assertIn("本人の公式アカウント", build.article_html)
        self.assertIn("https://www.tiktok.com/@creator.name", build.article_html)
        self.assertIn("TikTokで見る", build.article_html)
        self.assertIn("https://linktr.ee/og/image/creator.jpg", build.article_html)
        self.assertNotIn('data-pr-id="creator-tiktok"', build.article_html)

    def test_verified_people_are_labeled_below_images_and_grouped_in_one_rail(self) -> None:
        payload = make_payload()
        profile_url = "https://x.com/ichinose_luna"
        payload["main_subject"] = {
            "kind": "person",
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": True,
        }
        payload["identified_people"] = [{
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": True,
            "confidence": 96,
            "evidence_types": ["headline", "alt", "official_profile"],
        }]
        payload["media_person_attributions"] = [{
            "person_name": "一ノ瀬瑠菜",
            "image_ids": ["image-a"],
            "video_ids": [],
            "confidence": 96,
            "evidence_types": ["headline", "alt", "official_profile"],
        }]
        payload["person_identity_gate"] = {
            "status": "verified",
            "minimum_confidence": 95,
        }
        payload["verified_social_profiles"] = [{
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "service": "x",
            "url": profile_url,
            "confidence": 96,
            "thumbnail_url": "https://pbs.twimg.com/profile_images/example.jpg",
        }]
        payload["blocks"].insert(2, {
            "id": "official-x",
            "type": "related_link",
            "url": profile_url,
            "title": "一ノ瀬瑠菜のX",
            "provider": "x",
            "link_kind": "official_profile",
            "thumbnail_url": "https://pbs.twimg.com/profile_images/example.jpg",
            "thumbnail_source_kind": "profile",
            "thumbnail_owner_url": profile_url,
            "match_confidence": 96,
        })

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn('class="image-person-label image-person-verified"', build.article_html)
        self.assertIn('<strong>一ノ瀬瑠菜</strong>', build.article_html)
        self.assertNotIn("本人確認済み", build.article_html)
        self.assertNotIn("特定確率", build.article_html)
        self.assertIn('class="person-discovery-rail"', build.article_html)
        self.assertEqual(1, build.article_html.count(profile_url))
        self.assertNotIn('data-link-kind="official_profile"', build.article_html)

    def test_multiple_fanza_performers_use_the_horizontal_person_rail(self) -> None:
        payload = make_payload()
        performer_url = "https://video.dmm.co.jp/av/list/?actress=12345"
        product_url = "https://video.dmm.co.jp/av/content/?id=sample001"
        payload["fanza_people"] = [{
            "name": "出演者A",
            "image_ids": ["image-a"],
            "reason": "FANZA商品ページの出演者欄",
        }]
        payload["blocks"].append({
            "id": "performer-a",
            "type": "related_link",
            "url": performer_url,
            "title": "出演者Aの出演作品",
            "person_name": "出演者A",
            "provider": "fanza",
            "link_kind": "verified_person_search",
            "thumbnail_url": (
                "https://pics.dmm.co.jp/digital/video/sample001/sample001pl.jpg"
            ),
            "thumbnail_source_kind": "fanza_performer_sample",
            "thumbnail_owner_url": product_url,
            "sample_product_url": product_url,
            "match_confidence": 100,
        })

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn('class="person-discovery-rail"', build.article_html)
        self.assertIn("FANZA出演作", build.article_html)
        self.assertIn("sample001pl.jpg", build.article_html)
        self.assertEqual(1, build.article_html.count(performer_url))
        self.assertNotIn('data-link-kind="verified_person_search"', build.article_html)

    def test_grouped_profile_rail_does_not_package_hidden_duplicate_thumbnails(self) -> None:
        payload = make_payload()
        payload["main_subject"] = {
            "kind": "person",
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": True,
        }
        payload["identified_people"] = [{
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": True,
            "confidence": 96,
            "evidence_types": ["headline", "alt", "official_profile"],
        }]
        payload["media_person_attributions"] = [{
            "person_name": "一ノ瀬瑠菜",
            "image_ids": ["image-a"],
            "video_ids": [],
            "confidence": 96,
            "evidence_types": ["headline", "alt", "official_profile"],
        }]
        profile_urls = {
            "x": "https://x.com/ichinose_luna",
            "instagram": "https://www.instagram.com/ichinose_2266/",
        }
        profile_data_url = payload["images"][0]["data_url"]
        payload["verified_social_profiles"] = [
            {
                "name": "一ノ瀬瑠菜",
                "role": "グラビアモデル",
                "service": service,
                "url": url,
                "confidence": 96,
            }
            for service, url in profile_urls.items()
        ]
        for index, (service, url) in enumerate(profile_urls.items(), start=1):
            image_id = f"profile-{service}"
            payload["images"].append({
                "id": image_id,
                "name": f"profile-{index}.png",
                "data_url": profile_data_url,
                "alt": f"一ノ瀬瑠菜の{service}プロフィール画像",
                "orientation": "landscape",
                "related_thumbnail_only": True,
                "thumbnail_owner_url": url,
            })
            payload["blocks"].append({
                "id": f"official-{service}",
                "type": "related_link",
                "url": url,
                "title": f"一ノ瀬瑠菜の{service}",
                "provider": service,
                "link_kind": "official_profile",
                "thumbnail_image_id": image_id,
                "thumbnail_source_kind": "profile",
                "thumbnail_owner_url": url,
                "match_confidence": 96,
            })

        build = article_studio.build_article(payload, self.site_root)

        packaged_ids = {image.image_id for image in build.images}
        self.assertIn("profile-x", packaged_ids)
        self.assertNotIn("profile-instagram", packaged_ids)
        self.assertEqual(len(build.images), build.metadata["images_used"])

    def test_uncertain_person_shows_probability_and_ranked_candidate_dialog(self) -> None:
        payload = make_payload()
        payload["person_identity_candidates"] = [{
            "media_type": "image",
            "media_id": "image-a",
            "candidates": [
                {
                    "name": "候補甲",
                    "role": "コスプレイヤー",
                    "confidence": 82,
                    "evidence_types": ["watermark_ocr", "web_search_result"],
                    "evidence_urls": ["https://example.com/candidate-a"],
                    "reason": "透かし名と同じ投稿者名が見つかった",
                },
                {
                    "name": "候補乙",
                    "role": "モデル",
                    "confidence": 61,
                    "evidence_types": ["reverse_image_result"],
                    "evidence_urls": ["https://example.com/candidate-b"],
                    "reason": "同じ構図の画像が見つかった",
                },
            ],
            "unresolved_reason": "公式プロフィールとの同一性を確認できていない",
        }]

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn("推定：候補甲", build.article_html)
        self.assertIn("特定確率 82%", build.article_html)
        self.assertIn('class="person-candidate-dialog"', build.article_html)
        self.assertIn("特定候補ランキング", build.article_html)
        self.assertLess(build.article_html.index("候補甲"), build.article_html.index("候補乙"))
        self.assertIn("https://example.com/candidate-a", build.article_html)
        self.assertNotIn("本人確認済み", build.article_html)

    def test_video_person_identity_uses_same_verified_and_candidate_rules(self) -> None:
        payload = make_payload()
        payload["videos"] = [{
            "id": "video-a",
            "kind": "iframe",
            "url": "https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/",
            "mime_type": "text/html",
            "label": "公式サンプル動画",
            "rights_basis": "fanza_official_embed",
            "rights_source_url": "https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/",
            "width": 720,
            "height": 480,
        }]
        payload["blocks"].insert(2, {
            "id": "videos-a",
            "type": "videos",
            "video_ids": ["video-a"],
        })
        payload["person_identity_candidates"] = [{
            "media_type": "video",
            "media_id": "video-a",
            "candidates": [{
                "name": "動画候補",
                "role": "出演者",
                "confidence": 74,
                "evidence_types": ["video_frame_match"],
                "evidence_urls": ["https://example.com/video-candidate"],
                "reason": "代表フレームが公開画像に近い",
            }],
            "unresolved_reason": "作品クレジット未確認",
        }]

        build = article_studio.build_article(payload, self.site_root, preview=True)

        self.assertIn("推定：動画候補", build.article_html)
        self.assertIn("特定確率 74%", build.article_html)
        self.assertIn("動画の人物候補", build.article_html)

    def test_empty_related_ad_becomes_real_recommendation_with_account_below(self) -> None:
        save_fanza_settings(self.site_root, "article-owner-footer")
        payload = make_payload()
        payload["title"] = "【画像】やんやんの競泳水着コスプレ"
        payload["tags"] = ["やんやん", "コスプレ", "競泳水着"]
        payload["main_subject"] = {"name": "やんやん", "role": "コスプレイヤー"}
        payload["blocks"][-1]["text"] = "記事内容に合う関連広告枠"
        payload["blocks"].insert(2, {
            "id": "verified-yanyan-x",
            "type": "related_link",
            "url": "https://x.com/yanyan_cos",
            "title": "やんやんのX",
            "text": "本人の公式Xです。",
            "button_text": "Xで見る",
            "placement_label": "本人の公式アカウント",
            "provider": "x",
            "link_kind": "official_profile",
            "match_evidence": "確認済み人物名簿",
            "match_confidence": 98,
            "affiliate_network": "",
            "affiliate_eligible": False,
        })

        build = article_studio.build_article(payload, self.site_root)

        self.assertNotIn("記事内容に合う関連広告枠", build.article_html)
        self.assertNotIn('<div class="side-ad">関連広告枠</div>', build.article_html)
        self.assertIn("記事の題材から選ぶ / PR", build.article_html)
        self.assertIn("この記事が気に入った人向け", build.article_html)
        self.assertEqual(1, build.article_html.count("https://x.com/yanyan_cos"))
        self.assertIn('class="side-ad side-ad-link fanza-product-button"', build.article_html)
        self.assertEqual(0, build.article_html.count('class="fanza-product-thumb"'))
        self.assertNotIn('class="side-ad-link-thumb"', build.article_html)
        self.assertNotIn("searchstr%3D%25E3%2582%2584%25E3%2582%2593%25E3%2582%2584%25E3%2582%2593", build.article_html)

    def test_related_fanza_search_uses_owner_id_and_is_labeled_pr(self) -> None:
        save_fanza_settings(self.site_root, "article-owner-006")
        payload = make_payload()
        payload["tags"] = ["制服"]
        payload["blocks"].insert(-1, {
            "id": "related-search",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=制服",
            "title": "制服に近い作品",
            "text": "記事の題材から作った関連作品検索です。",
            "button_text": "関連作品をFANZAで見る",
            "placement_label": "記事内容に近い関連作品",
            "provider": "fanza",
            "link_kind": "inferred_topic_search",
            "match_evidence": "記事タグから作成した関連検索",
            "match_confidence": 45,
            "affiliate_network": "fanza",
            "affiliate_eligible": True,
        })

        build = article_studio.build_article(payload, self.site_root)

        self.assertIn("af_id=article-owner-006", build.article_html)
        self.assertIn("記事の題材から選ぶ / PR", build.article_html)
        self.assertIn('data-pr-id="article-related-footer-recommendation"', build.article_html)
        self.assertIn("制服系の作品を探す", build.article_html)
        self.assertIn("この記事にはFANZAのアフィリエイト広告", build.article_html)

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

    def test_draft_listing_ignores_repair_backup_json(self) -> None:
        drafts = self.site_root / ".article-studio" / "drafts"
        drafts.mkdir(parents=True)
        payload = make_payload()
        article_studio.save_draft(payload, self.site_root)
        (drafts / "studio-check.before-source-fix.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        rows = article_studio.list_drafts(self.site_root)

        self.assertEqual(["studio-check"], [row["slug"] for row in rows])

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

    def test_local_profile_thumbnail_does_not_enter_article_image_count(self) -> None:
        payload = make_payload()
        profile_url = "https://www.instagram.com/example/"
        payload["images"].append({
            "id": "profile-image",
            "name": "profile.png",
            "data_url": PNG_DATA_URL,
            "alt": "本人のInstagramプロフィール画像",
            "orientation": "landscape",
            "rights_basis": "official_profile_thumbnail",
            "rights_source_url": profile_url,
            "thumbnail_owner_url": profile_url,
            "related_thumbnail_only": True,
            "ai_role": "profile_thumbnail",
        })
        payload["blocks"].append({
            "id": "profile-card",
            "type": "related_link",
            "url": profile_url,
            "title": "本人のInstagram",
            "text": "公式プロフィールです。",
            "button_text": "Instagramで見る",
            "thumbnail_image_id": "profile-image",
            "thumbnail_source_kind": "profile",
            "thumbnail_owner_url": profile_url,
            "placement_label": "本人の公式アカウント",
            "provider": "instagram",
            "link_kind": "official_profile",
        })

        preview = article_studio.build_article(payload, self.site_root, preview=True)
        final = article_studio.build_article(payload, self.site_root)

        self.assertEqual(2, preview.metadata["images_used"])
        self.assertEqual(1, preview.metadata["body_images_used"])
        self.assertIn('<span class="image-count">1 / 1</span>', preview.article_html)
        self.assertIn("data:image/png;base64,", preview.article_html)
        self.assertIn('src="images/image-02.png"', final.article_html)

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
        source["images"][0].update({
            "embedded_status_url": "https://x.com/creator/status/123",
            "owner_name": "creator",
            "owner_profile_url": "https://x.com/creator",
            "ai_content_group": "x-account:creator",
            "ai_role": "article_gallery",
            "ai_reason": "本文で明示された本人投稿",
        })
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
        self.assertEqual(draft["images"][0]["ai_content_group"], "x-account:creator")
        self.assertEqual(draft["images"][0]["owner_profile_url"], "https://x.com/creator")
        self.assertEqual(
            draft["images"][0]["embedded_status_url"],
            "https://x.com/creator/status/123",
        )
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
        self.assertNotIn(draft["videos"][0]["poster_data_url"], final.article_html)
        self.assertIn('poster="images/video-poster-01.jpg"', final.article_html)

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
        rendered_path = self.site_root / "articles" / f"{draft['slug']}.html"
        self.assertTrue(rendered_path.is_file())
        rendered = rendered_path.read_text(encoding="utf-8")
        self.assertNotIn("data:image/", rendered)
        self.assertIn(
            f'poster="../assets/articles/{draft["slug"]}/video-poster-01.jpg"',
            rendered,
        )
        self.assertTrue(
            (self.site_root / "assets" / "articles" / draft["slug"] / "video-poster-01.jpg").is_file()
        )

    def test_gateway_draft_keeps_the_requested_url_as_article_identity(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        source.update({
            "requested_url": "http://hnalady.com/blog-entry-31209.html",
            "url": "https://eromitai.com/archives/421501/",
            "site_name": "eromitai.com",
        })

        draft = article_studio.build_source_draft_payload(source, ["media-1", "media-2"])

        self.assertEqual("url-hnalady-com-92dd8148", draft["slug"])
        self.assertEqual(
            "http://hnalady.com/blog-entry-31209.html",
            draft["source_url"],
        )
        self.assertEqual("https://eromitai.com/archives/421501/", draft["resolved_source_url"])
        self.assertEqual("hnalady.com", draft["source_label"])

    def test_source_parser_keeps_article_body_media_and_drops_page_chrome(self) -> None:
        parser = article_studio._SourcePageParser()
        parser.feed(
            '<html><head><meta property="og:image" content="/cover.jpg"></head><body>'
            '<article><div class="above-content-links"><img src="/related-before.jpg"></div>'
            '<div class="entry-content"><p>本文として使う十分な長さの説明テキストです。</p>'
            '<img src="/body-1.jpg"><video src="/body.mp4"></video></div>'
            '<div class="related-posts"><img src="/related-after.jpg"></div></article>'
            '</body></html>'
        )

        images = article_studio._candidate_image_urls(parser, "https://site.example/story")
        videos = article_studio._candidate_videos(parser, "https://site.example/story")

        self.assertEqual(
            ["https://site.example/body-1.jpg", "https://site.example/cover.jpg"],
            [item["url"] for item in images],
        )
        self.assertTrue(images[0]["inside_article"])
        self.assertEqual(["https://site.example/body.mp4"], [item["url"] for item in videos])
        self.assertEqual(1, len(parser.article_text_items))

    def test_video_led_source_uses_metadata_image_not_sidebar_images(self) -> None:
        parser = article_studio._SourcePageParser()
        parser.feed(
            '<html><head><meta property="og:image" content="/cover.jpg"></head><body>'
            '<article><div class="entry-content"><video src="/body.mp4"></video></div>'
            '<aside><img src="/sidebar-1.jpg"><img src="/sidebar-2.jpg"></aside>'
            '</article></body></html>'
        )

        images = article_studio._candidate_image_urls(parser, "https://site.example/story")

        self.assertEqual(["https://site.example/cover.jpg"], [item["url"] for item in images])

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
        self.assertTrue(payload["media_alignment_checked"])
        self.assertEqual(len([block for block in payload["blocks"] if block["type"] == "post"]), 5)
        self.assertEqual(
            sorted(image_id for block in payload["blocks"] for image_id in block.get("image_ids", [])),
            ["source-image-1", "source-image-2"],
        )
        article_studio.build_article(payload, self.site_root)

    def test_codex_result_keeps_only_article_topic_tags(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        base = article_studio.build_source_draft_payload(source, ["media-1", "media-2"])
        generated = FakeCodexRunner().generate(source, {"reply_count": "5"})
        generated["tags"] = [
            "PR", "FANZA", "成人向け", "フィギュア", "野球部ちゃん", "#開脚",
        ]

        payload = article_studio.apply_codex_result(base, generated)

        self.assertEqual(["フィギュア", "野球部ちゃん", "開脚"], payload["tags"])

    def test_public_article_does_not_expose_source_url(self) -> None:
        source = article_studio.analyze_source_url(
            "https://news.example.com/cosplay/story",
            FakeSourceOpener(),
        )
        payload = article_studio.build_source_draft_payload(source, ["media-1", "media-2"])
        payload["transparency_note"] = "レス本文は記事構成のための再構成です。"

        build = article_studio.build_article(payload, self.site_root)

        self.assertNotIn(source["url"], build.article_html)
        self.assertNotIn("元記事：", build.article_html)

    def test_thumbnail_image_is_used_as_the_lead_article_image(self) -> None:
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
        self.assertEqual(["source-image-1", "source-image-2"], body_ids)
        self.assertEqual("images", draft["blocks"][0]["type"])

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

    def test_codex_analysis_cache_reuses_unchanged_chunks_only(self) -> None:
        class CountingRunner(article_studio.CodexRunner):
            def __init__(self, site_root: Path) -> None:
                super().__init__(site_root, executable=Path(__file__))
                self.calls = 0

            def _execute(
                self,
                prompt: str,
                schema_path: Path,
                *,
                attachments: list[dict[str, object]] | None = None,
                run_prefix: str = "run-",
            ) -> dict[str, object]:
                self.calls += 1
                image_ids = list(dict.fromkeys(
                    re.findall(r'"image_id": "(media-\d+)"', prompt)
                ))
                return {
                    "title": "素材を確認した記事",
                    "description": "素材ごとの役割を確認した。",
                    "category": "画像",
                    "page_role": "article",
                    "follow_url": "",
                    "follow_reason": "",
                    "analysis_summary": "広告と本編を確認した。",
                    "adult_content": True,
                    "adult_reason": "成人向け本文を扱うテストページ",
                    "fanza_relevance": "none",
                    "fanza_performer_name": "",
                    "fanza_search_query": "",
                    "fanza_product_code": "",
                    "fanza_reason": "",
                    "fanza_people": [],
                    "fanza_recommendation_queries": [],
                    "image_decisions": [
                        {
                            "image_id": image_id,
                            "verdict": "article",
                            "role": "本文画像",
                            "recommended_use": "body",
                            "content_group": "main",
                            "relation": "",
                            "relevance_score": 90,
                            "reason": "本文内の素材",
                        }
                        for image_id in image_ids
                    ],
                    "video_decisions": [],
                }

        source = {
            "url": "https://example.com/gallery",
            "title": "画像記事",
            "description": "画像を確認する。",
            "images": [
                {
                    "id": f"media-{index}",
                    "data": PNG_BYTES,
                    "extension": ".png",
                    "url": f"https://example.com/{index}.png",
                }
                for index in range(1, 32)
            ],
            "videos": [],
            "browser_attachments": [],
            "links": [],
        }
        runner = CountingRunner(self.site_root)
        runner.analyze(source)
        self.assertEqual(2, runner.calls)

        runner.analyze(source)
        self.assertEqual(2, runner.calls)

        changed = {**source, "images": [dict(item) for item in source["images"]]}
        changed["images"][-1]["data"] = SECOND_PNG_BYTES
        runner.analyze(changed)
        self.assertEqual(3, runner.calls)

    def test_codex_compose_classifies_and_writes_article_in_one_call(self) -> None:
        class OnePassRunner(article_studio.CodexRunner):
            def __init__(self, site_root: Path) -> None:
                super().__init__(site_root, executable=Path(__file__))
                self.calls = 0
                self.schema: dict[str, object] = {}

            def _execute(
                self,
                prompt: str,
                schema_path: Path,
                *,
                attachments: list[dict[str, object]] | None = None,
                run_prefix: str = "run-",
                web_search: bool = False,
                reasoning_effort: str = article_studio.CODEX_ARTICLE_REASONING_EFFORT,
            ) -> dict[str, object]:
                self.calls += 1
                self.schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertions = (
                    prompt, attachments or [], run_prefix, web_search, reasoning_effort,
                )
                return {
                    "title": "素材を見て決めた題材",
                    "description": "画像の内容を確認した記事。",
                    "category": "画像",
                    "page_role": "article",
                    "follow_url": "",
                    "follow_reason": "",
                    "analysis_summary": "本編画像を確認した。",
                    "adult_content": True,
                    "adult_reason": "成人向け本文を扱うテストページ",
                    "fanza_relevance": "none",
                    "fanza_performer_name": "",
                    "fanza_search_query": "",
                    "fanza_product_code": "",
                    "fanza_reason": "",
                    "fanza_people": [],
                    "fanza_image_products": [],
                    "fanza_recommendation_queries": [],
                    "image_decisions": [{
                        "image_id": "media-1",
                        "verdict": "article",
                        "role": "article_main",
                        "recommended_use": "thumbnail_and_body",
                        "content_group": "main",
                        "relation": "記事の中心画像",
                        "relevance_score": 100,
                        "reason": "本文と一致する",
                    }],
                    "video_decisions": [],
                    "article": {
                        "title": "【画像】素材を見て決めた題材",
                        "summary": "画像を中心に紹介する。",
                        "category": "画像",
                        "tags": ["題材"],
                        "responses": [
                            {"text": "まずこれ見てくれ", "style": "normal", "image_ids": ["media-1"], "video_ids": []},
                            {"text": "構図ええな", "style": "large", "image_ids": [], "video_ids": []},
                            {"text": "これは気になる", "style": "normal", "image_ids": [], "video_ids": []},
                        ],
                    },
                }

        runner = OnePassRunner(self.site_root)
        result = runner.compose({
            "url": "https://example.com/article",
            "title": "題材",
            "description": "説明",
            "images": [{
                "id": "media-1",
                "data": PNG_BYTES,
                "extension": ".png",
                "mime_type": "image/png",
                "url": "https://example.com/main.png",
            }],
            "videos": [],
            "browser_attachments": [],
            "links": [],
        }, {"reply_count": "3", "category": "auto"})

        self.assertEqual(1, runner.calls)
        self.assertIn("article", runner.schema["properties"])
        self.assertIn("official_work", runner.schema["required"])
        self.assertTrue(runner.assertions[3])
        self.assertEqual("【画像】素材を見て決めた題材", result["article"]["title"])
        self.assertEqual("article", result["analysis"]["page_role"])

    def test_codex_compose_repairs_long_source_text_overlap_without_reanalysis(self) -> None:
        copied = (
            "海辺の白い壁を背景に水着姿から脱ぎかけの場面へ進み、"
            "室内では衣装を替えながら複数の構図を順番に見せている。"
        ) * 2

        class RepairRunner(article_studio.CodexRunner):
            def __init__(self, site_root: Path) -> None:
                super().__init__(site_root, executable=Path(__file__))
                self.calls = 0
                self.prefixes: list[str] = []

            def _execute(
                self,
                prompt: str,
                schema_path: Path,
                *,
                attachments: list[dict[str, object]] | None = None,
                run_prefix: str = "run-",
                web_search: bool = False,
                reasoning_effort: str = article_studio.CODEX_ARTICLE_REASONING_EFFORT,
            ) -> dict[str, object]:
                self.calls += 1
                self.prefixes.append(run_prefix)
                if self.calls == 2:
                    self.assert_repair_prompt = prompt
                    return {
                        "title": "【画像】浜辺から室内へ続く脱衣ギャラリー",
                        "summary": "水着姿と室内カットを別々の視点で眺める画像記事。",
                        "category": "画像",
                        "tags": ["水着", "脱衣"],
                        "responses": [
                            {"text": "最初の水着からもうええやん", "style": "normal", "image_ids": ["media-1"], "video_ids": []},
                            {"text": "場所変わると印象かなり違うな", "style": "large", "image_ids": [], "video_ids": []},
                            {"text": "室内の方が好きやわ", "style": "normal", "image_ids": [], "video_ids": []},
                        ],
                    }
                return {
                    "title": "素材を確認した題材",
                    "description": "本編画像を確認した。",
                    "category": "画像",
                    "page_role": "article",
                    "follow_url": "",
                    "follow_reason": "",
                    "analysis_summary": "本編画像を確認した。",
                    "adult_content": True,
                    "adult_reason": "成人向け画像ギャラリー",
                    "fanza_relevance": "none",
                    "fanza_performer_name": "",
                    "fanza_search_query": "",
                    "fanza_product_code": "",
                    "fanza_reason": "",
                    "fanza_people": [],
                    "fanza_image_products": [],
                    "fanza_recommendation_queries": [],
                    "image_decisions": [{
                        "image_id": "media-1",
                        "verdict": "article",
                        "role": "article_main",
                        "recommended_use": "thumbnail_and_body",
                        "content_group": "main",
                        "relation": "記事の中心画像",
                        "relevance_score": 100,
                        "reason": "本文と一致する",
                    }],
                    "video_decisions": [],
                    "article": {
                        "title": "【画像】素材を確認した題材",
                        "summary": "画像を中心に紹介する。",
                        "category": "画像",
                        "tags": ["水着"],
                        "responses": [
                            {"text": copied, "style": "normal", "image_ids": ["media-1"], "video_ids": []},
                            {"text": "二枚目もええな", "style": "large", "image_ids": [], "video_ids": []},
                            {"text": "これは気になる", "style": "normal", "image_ids": [], "video_ids": []},
                        ],
                    },
                }

        runner = RepairRunner(self.site_root)
        result = runner.compose({
            "url": "https://example.com/article",
            "title": "水着画像",
            "description": "画像記事",
            "body_text": copied,
            "images": [{
                "id": "media-1",
                "data": PNG_BYTES,
                "extension": ".png",
                "mime_type": "image/png",
                "url": "https://example.com/main.png",
            }],
            "videos": [],
            "browser_attachments": [],
            "links": [],
        }, {"reply_count": "3", "category": "auto"})

        self.assertEqual(2, runner.calls)
        self.assertEqual(["compose-", "originality-"], runner.prefixes)
        self.assertIn("独自性検査で不合格", runner.assert_repair_prompt)
        self.assertEqual("【画像】浜辺から室内へ続く脱衣ギャラリー", result["article"]["title"])
        self.assertFalse(article_studio._codex_article_overlap_chunks(
            {"body_text": copied}, result["article"]
        ))

    def test_codex_article_generation_is_pinned_to_luna_high(self) -> None:
        self.assertEqual("gpt-5.6-luna", article_studio.CODEX_ARTICLE_MODEL)
        self.assertEqual("high", article_studio.CODEX_ARTICLE_REASONING_EFFORT)

        runner = article_studio.CodexRunner(self.site_root, executable=Path(__file__))
        command = runner._build_command(Path("schema.json"), Path("result.json"))
        self.assertEqual("gpt-5.6-luna", command[command.index("--model") + 1])
        self.assertEqual(
            'model_reasoning_effort="high"',
            command[command.index("--config") + 1],
        )

    def test_social_profile_verification_uses_luna_web_search_and_low_reasoning(self) -> None:
        runner = article_studio.CodexRunner(self.site_root, executable=Path(__file__))
        command = runner._build_command(
            Path("schema.json"),
            Path("result.json"),
            web_search=True,
            reasoning_effort="low",
        )

        self.assertEqual("gpt-5.6-luna", command[command.index("--model") + 1])
        self.assertIn("standalone_web_search", command)
        self.assertEqual(
            'model_reasoning_effort="low"',
            command[command.index("--config") + 1],
        )

    def test_codex_analysis_keeps_the_named_public_main_subject(self) -> None:
        value = {
            "title": "やんやんのコスプレ",
            "description": "コスプレ画像を紹介する記事。",
            "category": "画像",
            "page_role": "article",
            "follow_url": "",
            "follow_reason": "",
            "analysis_summary": "記事本編を確認した。",
            "adult_content": True,
            "adult_reason": "成人向けグラビア",
            "main_subject": {
                "name": "やんやん",
                "kind": "person",
                "role": "コスプレイヤー",
                "is_public_creator": True,
                "reason": "見出しと本文に活動名とコスプレイヤー表記がある",
            },
            "social_profiles": [],
            "fanza_relevance": "none",
            "fanza_performer_name": "",
            "fanza_search_query": "",
            "fanza_product_code": "",
            "fanza_reason": "",
            "fanza_people": [],
            "fanza_image_products": [],
            "fanza_recommendation_queries": [],
            "image_decisions": [],
            "video_decisions": [],
        }

        result = article_studio._validate_codex_analysis(value, {"images": [], "videos": []})

        self.assertEqual("やんやん", result["main_subject"]["name"])
        self.assertEqual("コスプレイヤー", result["main_subject"]["role"])
        self.assertTrue(result["main_subject"]["is_public_creator"])

    def test_codex_analysis_keeps_only_an_exact_verified_official_work_page(self) -> None:
        value = {
            "title": "ゾンビ漫画の紹介",
            "description": "作品ページの画像を紹介する記事。",
            "category": "画像",
            "analysis_summary": "作品名と公式ページを確認した。",
            "adult_content": True,
            "adult_reason": "成人向け漫画作品",
            "main_subject": {
                "name": "ゾンビのあふれた世界で俺だけが襲われない 時子 IF STORY",
                "kind": "work",
                "role": "漫画",
                "is_public_creator": False,
                "reason": "記事見出しに作品名がある",
            },
            "official_work": {
                "status": "verified",
                "title": "ゾンビのあふれた世界で俺だけが襲われない 時子 IF STORY",
                "url": "https://comic-ragchew.jp/comics/zombietokiko/",
                "provider": "COMICらぐちゅう",
                "reason": "出版社公式ページの作品名が完全一致",
                "thumbnail_url": "",
            },
            "image_decisions": [],
            "video_decisions": [],
        }

        result = article_studio._validate_codex_analysis(
            value, {"images": [], "videos": []}
        )

        self.assertEqual("verified", result["official_work"]["status"])
        self.assertEqual(
            "https://comic-ragchew.jp/comics/zombietokiko/",
            result["official_work"]["url"],
        )

        value["official_work"] = {
            **value["official_work"],
            "url": "https://www.google.com/search?q=zombie",
        }
        rejected = article_studio._validate_codex_analysis(
            value, {"images": [], "videos": []}
        )
        self.assertEqual("ambiguous", rejected["official_work"]["status"])
        self.assertEqual("", rejected["official_work"]["url"])

    def test_codex_large_gallery_uses_labelled_contact_sheets(self) -> None:
        source = {
            "images": [
                {
                    "id": f"image-{index}",
                    "data": PNG_BYTES,
                    "extension": ".png",
                    "url": f"https://example.com/{index}.png",
                    "alt": f"image {index}",
                }
                for index in range(1, 19)
            ]
        }

        attachments = article_studio._codex_image_attachments(source)

        self.assertEqual(18, len(attachments))
        self.assertEqual(
            3,
            len({str(item["filename"]) for item in attachments}),
        )
        self.assertEqual(
            {f"image-{index}" for index in range(1, 19)},
            {str(item["id"]) for item in attachments},
        )
        sheet_records = [
            item for item in attachments if item.get("kind") == "contact_sheet"
        ]
        self.assertEqual(17, len(sheet_records))
        self.assertTrue(all(item.get("contact_sheet_cell") for item in sheet_records))
        self.assertTrue(all(item["data"].startswith(b"\xff\xd8") for item in sheet_records))

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
            "adult_content": True,
            "adult_reason": "成人向け画像を扱うテストページ",
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

    def test_codex_analysis_excludes_media_with_a_different_visible_handle(self) -> None:
        profile_url = "https://www.youtube.com/@panpianoatelier"
        source = {
            "links": [{"url": profile_url, "text": "Pan Piano公式YouTube"}],
            "images": [{"id": "media-1"}],
            "videos": [{"id": "video-1"}],
        }
        result = article_studio._validate_codex_analysis({
            "title": "Pan Pianoのチャンネル画面",
            "description": "公式チャンネルの画面を確認した。",
            "category": "画像",
            "analysis_summary": "別人の透かしがある素材を検出した。",
            "adult_content": True,
            "adult_reason": "成人向けの衣装サムネイルを扱うページ",
            "main_subject": {
                "name": "Pan Piano",
                "kind": "person",
                "role": "YouTuber",
                "is_public_creator": True,
                "reason": "見出しと公式チャンネル名が一致する",
            },
            "social_profiles": [{
                "name": "Pan Piano",
                "service": "youtube",
                "url": profile_url,
                "is_main_subject": True,
                "reason": "本文に公式チャンネルへのリンクがある",
            }],
            "image_decisions": [{
                "image_id": "media-1",
                "verdict": "article",
                "role": "本文画像",
                "recommended_use": "body",
                "content_group": "gallery",
                "relation": "",
                "visible_creator_handle": "@nacocomusic1552",
                "subject_match": "unknown",
                "relevance_score": 95,
                "reason": "本文付近にある",
            }],
            "video_decisions": [{
                "video_id": "video-1",
                "verdict": "article",
                "visible_creator_handle": "@nacocomusic1552",
                "subject_match": "matched",
                "relevance_score": 95,
                "reason": "本文付近にある",
            }],
        }, source)

        image_decision = result["image_decisions"][0]
        self.assertEqual("unrelated", image_decision["verdict"])
        self.assertEqual("exclude", image_decision["recommended_use"])
        self.assertEqual("mismatch", image_decision["subject_match"])
        self.assertEqual(0, image_decision["relevance_score"])
        video_decision = result["video_decisions"][0]
        self.assertEqual("unrelated", video_decision["verdict"])
        self.assertEqual("mismatch", video_decision["subject_match"])
        self.assertEqual(0, video_decision["relevance_score"])

    def test_codex_analysis_maps_named_people_only_to_available_images(self) -> None:
        source = {
            "images": [{"id": "media-1"}, {"id": "media-2"}],
            "videos": [],
        }
        result = article_studio._validate_codex_analysis({
            "title": "出演者名を確認できる画像記事",
            "description": "画像周辺の見出しから出演者名を確認した。",
            "category": "画像",
            "analysis_summary": "画像と人物名の対応を確認した。",
            "adult_content": True,
            "adult_reason": "成人向け出演作品を扱うテストページ",
            "fanza_people": [
                {
                    "name": "宮下玲奈",
                    "image_ids": ["media-1", "missing-image"],
                    "reason": "画像直前の見出しに出演者名がある",
                }
            ],
            "image_decisions": [
                {
                    "image_id": image_id,
                    "verdict": "article",
                    "role": "本文画像",
                    "recommended_use": "body",
                    "content_group": "gallery",
                    "relation": "",
                    "relevance_score": 90,
                    "reason": "本文内にある",
                }
                for image_id in ("media-1", "media-2")
            ],
            "video_decisions": [],
        }, source)

        self.assertEqual(
            [{
                "name": "宮下玲奈",
                "image_ids": ["media-1"],
                "reason": "画像直前の見出しに出演者名がある",
            }],
            result["fanza_people"],
        )

    def test_codex_analysis_keeps_only_evidenced_main_social_profiles(self) -> None:
        source = {
            "url": "https://example.com/article",
            "links": [
                {
                    "url": "https://www.tiktok.com/@riri_official/video/123456",
                    "text": "本人TikTok",
                },
                {"url": "https://x.com/intent/tweet?text=test", "text": "Xで共有"},
            ],
            "images": [],
            "videos": [],
        }
        result = article_studio._validate_codex_analysis({
            "title": "TikTokerりりの投稿",
            "description": "本人投稿を紹介する。",
            "category": "SNS",
            "analysis_summary": "本文と本人リンクを確認した。",
            "adult_content": True,
            "adult_reason": "成人本人の成人向け投稿",
            "social_profiles": [
                {
                    "name": "りり",
                    "service": "tiktok",
                    "url": "https://www.tiktok.com/@riri_official/video/123456",
                    "is_main_subject": True,
                    "reason": "本文中の本人TikTokリンク",
                },
                {
                    "name": "りり",
                    "service": "x",
                    "url": "https://x.com/intent/tweet?text=test",
                    "is_main_subject": True,
                    "reason": "共有ボタン",
                },
            ],
            "image_decisions": [],
            "video_decisions": [],
        }, source)

        self.assertEqual(
            ["https://www.tiktok.com/@riri_official/video/123456"],
            [item["url"] for item in result["social_profiles"]],
        )

    def test_codex_analysis_does_not_force_video_category_for_mixed_articles(self) -> None:
        result = article_studio.apply_codex_analysis(
            {
                "images": [],
                "videos": [{"id": "video-1", "kind": "direct", "url": "https://media.example.com/1.mp4"}],
            },
            {
                "title": "画像中心の記事",
                "description": "画像を中心に動画も一本掲載する。",
                "category": "画像",
                "analysis_summary": "画像が中心で動画は補足素材。",
                "adult_content": True,
                "adult_reason": "成人向け画像を扱うテストページ",
                "image_decisions": [],
                "video_decisions": [{
                    "video_id": "video-1",
                    "verdict": "article",
                    "relevance_score": 90,
                    "reason": "本文の補足動画",
                }],
            },
        )

        self.assertEqual("画像", result["ai_category"])
        self.assertEqual(["video-1"], result["recommended_video_ids"])

    def test_direct_fanza_facts_survive_an_empty_codex_performer_answer(self) -> None:
        result = article_studio.apply_codex_analysis(
            {
                "source_type": "fanza_product",
                "fanza_performer_name": "博多彩葉",
                "fanza_maker_code": "SIVR-503",
                "fanza_people": [{
                    "name": "博多彩葉",
                    "image_ids": ["media-1"],
                    "reason": "FANZA商品詳細の出演者欄で確認",
                }],
                "images": [{"id": "media-1"}],
                "videos": [],
            },
            {
                "title": "博多彩葉のVR作品",
                "description": "公式商品画像を紹介する。",
                "category": "画像",
                "analysis_summary": "FANZA公式商品ページ。",
                "adult_content": True,
                "adult_reason": "成人向け商品ページ",
                "fanza_relevance": "exact_product",
                "fanza_performer_name": "",
                "fanza_product_code": "",
                "fanza_people": [],
                "image_decisions": [{
                    "image_id": "media-1",
                    "verdict": "article",
                    "role": "article_main",
                    "recommended_use": "thumbnail_and_body",
                    "relevance_score": 100,
                    "reason": "公式パッケージ",
                }],
                "video_decisions": [],
            },
        )

        self.assertEqual("博多彩葉", result["ai_fanza_performer_name"])
        self.assertEqual("SIVR-503", result["ai_fanza_product_code"])
        self.assertEqual(["博多彩葉"], [item["name"] for item in result["ai_fanza_people"]])

    def test_generation_can_attach_selected_images_after_candidate_fifty(self) -> None:
        source = {
            "images": [
                {
                    "id": f"media-{index}",
                    "data": PNG_BYTES,
                    "extension": ".png",
                    "url": f"https://media.example.com/{index}.png",
                    "alt": f"候補 {index}",
                }
                for index in range(1, 61)
            ]
        }

        attachments = article_studio._codex_image_attachments(source, {"media-60"})

        self.assertEqual(1, len(attachments))
        self.assertEqual("media-60", attachments[0]["id"])

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

    def test_codex_mixed_media_article_keeps_body_images_and_videos(self) -> None:
        generated = {
            "title": "画像と動画のある記事",
            "summary": "画像と動画の両方を紹介する記事。",
            "category": "動画",
            "tags": ["動画", "画像"],
            "responses": [
                {"text": "まずこれ", "style": "normal", "video_ids": ["source-video-1"]},
                {"text": "写真もええやん", "style": "normal", "video_ids": []},
                {"text": "もう一本", "style": "normal", "video_ids": ["source-video-2"]},
            ],
        }
        base = make_payload()
        base["images"].append({
            "id": "image-b",
            "name": "second.png",
            "data_url": PNG_DATA_URL,
            "alt": "二枚目の画像",
            "orientation": "landscape",
        })
        base["blocks"] = [
            {"id": "seed-post", "type": "post", "text": "仮レス", "style": "normal"},
            {"id": "source-videos-1", "type": "videos", "video_ids": ["source-video-1", "source-video-2"]},
            {"id": "source-images-1", "type": "images", "image_ids": ["image-a"]},
            {"id": "source-images-2", "type": "images", "image_ids": ["image-b"]},
        ]
        base["videos"] = [
            {
                "id": f"source-video-{index}",
                "kind": "direct",
                "url": f"https://media.example.com/{index}.mp4",
                "mime_type": "video/mp4",
                "label": f"動画 {index}",
            }
            for index in range(1, 3)
        ]

        payload = article_studio.apply_codex_result(base, generated)

        self.assertEqual(
            [["image-a"], ["image-b"]],
            [block["image_ids"] for block in payload["blocks"] if block["type"] == "images"],
        )
        self.assertEqual(
            [["source-video-1"], ["source-video-2"]],
            [block["video_ids"] for block in payload["blocks"] if block["type"] == "videos"],
        )
        article_studio.build_article(payload, self.site_root)

    def test_fanza_gallery_splits_large_official_image_sets_before_saving(self) -> None:
        source = {
            "source_type": "fanza_product",
            "media_rights_profile": "fanza_product",
            "title": "official product",
            "site_name": "FANZA",
            "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "description": "",
            "excerpts": [],
            "images": [
                {
                    "id": f"image-{index}",
                    "data": PNG_BYTES,
                    "extension": ".png",
                    "mime_type": "image/png",
                    "url": f"https://pics.dmm.co.jp/digital/video/abc001/abc001jp-{index}.jpg",
                    "rights_basis": "fanza_product_sample_image",
                    "alt": f"official image {index}",
                    "orientation": "portrait",
                }
                for index in range(1, 14)
            ],
            "videos": [{
                "id": "video-1",
                "kind": "iframe",
                "url": "https://www.dmm.co.jp/service/digitalapi/-/html5_player/=/cid=abc001/",
                "mime_type": "text/html",
                "rights_basis": "fanza_free_video_tool_embed",
                "rights_source_url": "https://www.dmm.co.jp/litevideo/-/part/=/cid=abc001/size=720_480/",
                "title": "official sample video",
            }],
        }

        payload = article_studio.build_source_draft_payload(
            source,
            [f"image-{index}" for index in range(1, 14)],
            selected_video_ids=["video-1"],
            thumbnail_image_id="image-1",
        )

        media = [block for block in payload["blocks"] if block["type"] in {"images", "videos"}]
        self.assertEqual(["images", "videos", "images", "images", "images"], [block["type"] for block in media])
        self.assertEqual([1, 4, 4, 4], [len(block["image_ids"]) for block in media if block["type"] == "images"])
        self.assertEqual([["source-video-1"]], [block["video_ids"] for block in media if block["type"] == "videos"])
        article_studio.build_article(payload, self.site_root, preview=True)

    def test_codex_video_placement_deduplicates_without_forcing_all_into_comments(self) -> None:
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
        self.assertEqual(set(placed), {"video-2", "video-4"})
        self.assertEqual(len(placed), len(set(placed)))
        self.assertTrue(all(len(response["video_ids"]) <= 2 for response in generated["responses"]))

    def test_codex_result_keeps_unassigned_videos_as_standalone_media(self) -> None:
        base = make_payload()
        base["videos"] = [
            {
                "id": f"source-video-{index}",
                "kind": "direct",
                "url": f"https://media.example.com/{index}.mp4",
                "mime_type": "video/mp4",
                "label": f"video {index}",
            }
            for index in range(1, 31)
        ]
        base["blocks"] = [{
            "id": "source-videos-1",
            "type": "videos",
            "video_ids": [video["id"] for video in base["videos"]],
        }]
        generated = {
            "title": "many videos",
            "summary": "all videos remain in the article",
            "category": "動画",
            "tags": ["動画"],
            "responses": [
                {"text": f"response {index}", "style": "normal", "video_ids": []}
                for index in range(1, 9)
            ],
        }

        payload = article_studio.apply_codex_result(base, generated)

        placed = [
            video_id
            for block in payload["blocks"]
            if block.get("type") == "videos"
            for video_id in block.get("video_ids", [])
        ]
        self.assertEqual([f"source-video-{index}" for index in range(1, 31)], placed)

    def test_unassigned_videos_start_after_first_response(self) -> None:
        base = make_payload()
        base["videos"] = [
            {
                "id": f"source-video-{index}",
                "kind": "direct",
                "url": f"https://media.example.com/{index}.mp4",
                "mime_type": "video/mp4",
                "label": f"video {index}",
            }
            for index in range(1, 4)
        ]
        generated = {
            "title": "video first",
            "summary": "videos are placed near the top",
            "category": "動画",
            "tags": ["動画"],
            "responses": [
                {"text": "first", "style": "normal", "video_ids": []},
                {"text": "second", "style": "normal", "video_ids": []},
                {"text": "third", "style": "normal", "video_ids": []},
            ],
        }

        payload = article_studio.apply_codex_result(base, generated)

        self.assertEqual("post", payload["blocks"][0]["type"])
        self.assertEqual("videos", payload["blocks"][1]["type"])
        self.assertEqual(["source-video-1", "source-video-2"], payload["blocks"][1]["video_ids"])

    def test_source_draft_puts_official_video_and_image_before_comments(self) -> None:
        source = {
            "source_type": "fanza_product",
            "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "title": "ABC-001",
            "description": "official product",
            "site_name": "FANZA",
            "images": [{
                "id": "media-1",
                "extension": ".png",
                "mime_type": "image/png",
                "data": base64.b64decode(PNG_DATA_URL.split(",", 1)[1]),
                "alt": "package",
            }],
            "videos": [{
                "id": "video-1",
                "kind": "direct",
                "url": "https://cc3001.dmm.co.jp/litevideo/freepv/a/abc/abc001/abc001mhb.mp4",
                "mime_type": "video/mp4",
                "rights_basis": "fanza_official_share_embed",
            }],
            "excerpts": [],
        }
        draft = article_studio.build_source_draft_payload(
            source,
            [],
            selected_video_ids=["video-1"],
            thumbnail_image_id="media-1",
        )
        self.assertEqual("images", draft["blocks"][0]["type"])
        self.assertEqual("videos", draft["blocks"][1]["type"])
        self.assertEqual("post", draft["blocks"][2]["type"])

    def test_video_rights_basis_is_preserved_for_fanza_review(self) -> None:
        source = {
            "source_type": "fanza_product",
            "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "requested_url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "title": "ABC-001",
            "description": "",
            "site_name": "FANZA",
            "images": [{
                "id": "media-1",
                "extension": ".png",
                "mime_type": "image/png",
                "data": base64.b64decode(PNG_DATA_URL.split(",", 1)[1]),
                "alt": "package",
                "orientation": "portrait",
                "url": "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
                "rights_basis": "fanza_product_main_image",
            }],
            "videos": [{
                "id": "video-1",
                "kind": "direct",
                "url": "https://cc3001.dmm.co.jp/litevideo/freepv/a/abc/abc001/abc001mhb.mp4",
                "mime_type": "video/mp4",
                "rights_basis": "fanza_official_share_embed",
                "rights_source_url": "https://cc3001.dmm.co.jp/litevideo/freepv/a/abc/abc001/abc001mhb.mp4",
            }],
            "excerpts": [],
        }

        draft = article_studio.build_source_draft_payload(
            source,
            [],
            selected_video_ids=["video-1"],
            thumbnail_image_id="media-1",
        )
        build = article_studio.build_article(draft, self.site_root, preview=True)

        self.assertEqual("fanza_official_share_embed", build.payload["videos"][0]["rights_basis"])
        self.assertEqual("images", build.payload["blocks"][0]["type"])
        self.assertEqual("videos", build.payload["blocks"][1]["type"])

    def test_video_article_requires_non_thumbnail_images_in_the_body(self) -> None:
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
            {"id": "images-b", "type": "images", "image_ids": ["image-b"]},
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
