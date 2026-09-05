from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.sites import SiteRegistry  # noqa: E402
from indanya_desktop.editorial_policy import (  # noqa: E402
    EditorialPolicyError,
    FANZA_MEDIA_PROFILE,
    is_fanza_official_sample_video_url,
    restrict_source_to_fanza_product,
)
from indanya_desktop.workers import (  # noqa: E402
    XLoginRequiredError,
    _apply_editorial_metadata,
    _apply_adaptive_quality,
    _attach_verified_fanza_products,
    _infer_single_embedded_fanza_product,
    _embedded_x_status_urls,
    _hydrate_embedded_x_status_media,
    _capture_and_analyze_source,
    _capture_for_manual_generation,
    _mark_ready_to_publish,
    _fanza_product_kind,
    _filter_source_videos,
    _resolve_fanza_promotion,
    _dedupe_direct_fanza_product_ctas,
    save_fanza_settings,
    _is_recommendation_material,
    _select_article_images,
)
from indanya_desktop.browser_capture import (  # noqa: E402
    _find_x_media_urls,
    _image_candidate_urls,
    _merge_snapshot,
    _normalized_text_blocks,
    _plausible_video_candidate,
    _register_capture_cleanup,
    parse_fanza_product_identity,
    _usable_final_url,
    _video_canvas_frame,
    _video_priority,
    _redundant_dmm_player,
    _set_chatgpt_prompt,
    _x_capture_scroll_steps,
    _x_video_asset_key,
    discover_fanza_products,
)
from repair_article_from_source import (  # noqa: E402
    _replace_media_blocks,
    _reset_fanza_metadata,
)


def test_normalized_text_blocks_accepts_page_records_and_x_snapshot_strings() -> None:
    assert _normalized_text_blocks([
        {"text": " 通常ページの本文 "},
        "Xで統合済みの本文",
        {"other": "ignored"},
        "",
    ]) == ["通常ページの本文", "Xで統合済みの本文"]


def test_capture_cleanup_closes_only_registered_context_and_browser_on_failure() -> None:
    closed: list[str] = []

    class Closable:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    try:
        with ExitStack() as cleanup:
            _register_capture_cleanup(
                cleanup,
                Closable("browser"),
                Closable("context"),
            )
            raise RuntimeError("capture failed")
    except RuntimeError:
        pass

    assert closed == ["context", "browser"]


def test_fanza_product_discovery_returns_collected_products() -> None:
    class ImageLocator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def count(self) -> int:
            return 1

        def get_attribute(self, name: str) -> str:
            return {
                "src": "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
                "data-src": "",
            }.get(name, "")

    class Link:
        def get_attribute(self, name: str) -> str:
            return "/av/content/?id=abc001" if name == "href" else ""

        def inner_text(self) -> str:
            return "abc001 テスト作品"

        def locator(self, _selector: str) -> ImageLocator:
            return ImageLocator()

    class Locator:
        first = None

        def __init__(self, *, product: bool = False) -> None:
            self.product = product
            self.first = self

        def count(self) -> int:
            return 1 if self.product else 0

        def nth(self, _index: int) -> Link:
            return Link()

        def click(self) -> None:
            return None

    class Page:
        url = "https://www.dmm.co.jp/search/"

        def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

        def wait_for_selector(self, _selector: str, **_kwargs: object) -> None:
            return None

        def locator(self, selector: str) -> Locator:
            return Locator(product="/av/content/" in selector)

    class Context:
        def new_page(self) -> Page:
            return Page()

        def close(self) -> None:
            return None

    class Browser:
        def new_context(self, **_kwargs: object) -> Context:
            return Context()

        def close(self) -> None:
            return None

    class Chromium:
        def launch(self, **_kwargs: object) -> Browser:
            return Browser()

    class Playwright:
        chromium = Chromium()

    class Manager:
        def __enter__(self) -> Playwright:
            return Playwright()

        def __exit__(self, *_args: object) -> None:
            return None

    with patch("indanya_desktop.browser_capture.sync_playwright", return_value=Manager()):
        products = discover_fanza_products(["abc001"])

    assert products == [{
        "product_id": "abc001",
        "url": "https://video.dmm.co.jp/av/content/?id=abc001",
        "title": "abc001 テスト作品",
        "thumbnail_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
        "matched_query": "abc001",
        "product_kind": "video",
    }]


def test_x_status_capture_does_not_scroll_into_unrelated_timeline_posts() -> None:
    assert _x_capture_scroll_steps("https://x.com/example/status/123456789") == 1
    assert _x_capture_scroll_steps("https://x.com/example/status/123456789/") == 1
    assert _x_capture_scroll_steps("https://x.com/example") > 1


def test_embedded_x_status_media_replaces_timeline_and_sidebar_spillover() -> None:
    image_buffer = BytesIO()
    Image.new("RGB", (120, 160), "#f2f2f2").save(image_buffer, "JPEG")
    image_data = image_buffer.getvalue()
    first = "https://x.com/person/status/111"
    second = "https://x.com/person/status/222"
    source = {
        "body_text": f"本人投稿\n{first}/\n{second}\n関連記事\nhttps://x.com/other/status/333",
        "images": [{"id": "media-1", "url": "https://pbs.twimg.com/media/unrelated.jpg"}],
        "videos": [{"id": "video-1", "url": "https://example.com/sidebar-player"}],
        "browser_attachments": [
            {"id": "page", "filename": "page.jpg", "kind": "full_page", "data": image_data},
            {"id": "old", "filename": "old.jpg", "kind": "contact_sheet", "data": image_data},
        ],
    }
    captures = [
        {
            "images": [{"id": "media-1", "url": "https://pbs.twimg.com/media/one.jpg", "data": image_data}],
            "videos": [],
        },
        {
            "images": [{"id": "media-1", "url": "https://pbs.twimg.com/media/two.jpg", "data": image_data}],
            "videos": [{"id": "video-1", "url": "https://video.twimg.com/two.mp4"}],
        },
    ]

    with patch("indanya_desktop.workers.capture_rendered_source", side_effect=captures):
        result = _hydrate_embedded_x_status_media(source)

    assert _embedded_x_status_urls(source) == [first, second]
    assert [item["url"] for item in result["images"]] == [
        "https://pbs.twimg.com/media/one.jpg",
        "https://pbs.twimg.com/media/two.jpg",
    ]
    assert [item["id"] for item in result["images"]] == ["media-1", "media-2"]
    assert {item["ai_content_group"] for item in result["images"]} == {"x-account:person"}
    assert {item["owner_profile_url"] for item in result["images"]} == {"https://x.com/person"}
    assert [item["url"] for item in result["videos"]] == ["https://video.twimg.com/two.mp4"]
    assert all(
        item["kind"] != "contact_sheet" or item["id"].startswith("exact-x-status")
        for item in result["browser_attachments"]
    )


def test_media_only_repair_keeps_post_order_and_drops_stale_media_references() -> None:
    blocks = [
        {"id": "lead", "type": "images", "image_ids": ["old-1"]},
        {"id": "post-1", "type": "post", "text": "本文1"},
        {"id": "movie", "type": "videos", "video_ids": ["old-video"]},
        {"id": "post-2", "type": "post", "text": "本文2"},
        {"id": "gallery-separator", "type": "separator"},
        {"id": "extra", "type": "images", "image_ids": ["old-2", "old-3"]},
    ]

    repaired = _replace_media_blocks(blocks, ["new-1", "new-2"], [])

    assert [block["id"] for block in repaired] == ["lead", "post-1", "post-2", "extra"]
    assert repaired[0]["image_ids"] == ["new-1"]
    assert repaired[-1]["image_ids"] == ["new-2"]
    assert all(block.get("type") != "videos" for block in repaired)


def test_fanza_metadata_reset_removes_stale_people_links_but_keeps_article() -> None:
    payload = {
        "fanza_people": [{"name": "old"}],
        "fanza_performer_name": "old performer",
        "verified_social_profiles": [{"url": "https://x.com/old"}],
        "performer_identity_resolution": {"status": "verified"},
        "related_destinations": [{"url": "https://x.com/old"}],
        "blocks": [
            {"id": "post", "type": "post", "text": "本文"},
            {
                "id": "product",
                "type": "product_cta",
                "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            },
            {
                "id": "stale-work",
                "type": "related_link",
                "link_kind": "verified_person_search",
                "url": "https://example.com/old-work",
            },
            {
                "id": "stale-profile",
                "type": "related_link",
                "link_kind": "official_profile",
                "url": "https://x.com/old",
            },
            {
                "id": "exact-work",
                "type": "related_link",
                "link_kind": "exact_official_work",
                "url": "https://example.com/exact-work",
            },
        ],
    }

    _reset_fanza_metadata(payload)

    assert "fanza_people" not in payload
    assert "fanza_performer_name" not in payload
    assert "verified_social_profiles" not in payload
    assert "performer_identity_resolution" not in payload
    assert "related_destinations" not in payload
    assert [block["id"] for block in payload["blocks"]] == [
        "post",
        "product",
        "exact-work",
    ]


class _FakeKeyboard:
    def __init__(self) -> None:
        self.text = ""

    def insert_text(self, value: str) -> None:
        self.text = value


def test_fanza_product_identity_uses_explicit_performer_field_and_page() -> None:
    result = parse_fanza_product_identity(
        """商品情報
出演者：
博多彩葉
シリーズ： S1 VR
メーカー： エスワン ナンバーワンスタイル
レーベル： S1 VR
配信品番： sivr00503
メーカー品番： SIVR-503
""",
        [
            {
                "text": "松本いちか",
                "url": "https://video.dmm.co.jp/av/list/?actress=1073001&utm_source=global-nav",
            },
            {
                "text": "博多彩葉",
                "url": "https://video.dmm.co.jp/av/list/?actress=1109954",
            },
            {
                "text": "北岡果林",
                "url": "https://video.dmm.co.jp/av/list/?actress=1118950&utm_source=global-nav",
            },
        ],
    )

    assert result["performers"] == [{
        "name": "博多彩葉",
        "url": "https://video.dmm.co.jp/av/list/?actress=1109954",
        "reason": "FANZA商品詳細の出演者欄で確認",
    }]
    assert result["maker_code"] == "SIVR-503"
    assert result["distribution_code"] == "sivr00503"
    assert result["maker"] == "エスワン ナンバーワンスタイル"


def test_fanza_product_identity_splits_collapsed_linked_performers() -> None:
    result = parse_fanza_product_identity(
        """商品情報
出演者：
黒咲華瀬那ルミナすべて表示する
配信品番： miab00645
""",
        [
            {
                "text": "黒咲華",
                "url": "https://video.dmm.co.jp/av/list/?actress=1111111",
            },
            {
                "text": "瀬那ルミナ",
                "url": "https://video.dmm.co.jp/av/list/?actress=2222222",
            },
            {
                "text": "無関係な女優",
                "url": "https://video.dmm.co.jp/av/list/?actress=3333333",
            },
        ],
    )

    assert [item["name"] for item in result["performers"]] == [
        "黒咲華",
        "瀬那ルミナ",
    ]


def test_direct_fanza_product_keeps_only_one_exact_product_card() -> None:
    product_url = "https://video.dmm.co.jp/av/content/?id=sivr00503"
    payload = {
        "content_mode": "fanza_product",
        "source_url": product_url,
        "blocks": [
            {"type": "images", "image_ids": ["media-1"]},
            {
                "type": "product_cta",
                "url": product_url,
                "match_type": "exact_image",
                "match_confidence": 100,
                "thumbnail_url": "https://pics.dmm.co.jp/sivr00503pl.jpg",
            },
            {"type": "post", "text": "本文"},
            {"type": "product_cta", "url": product_url},
        ],
    }

    assert _dedupe_direct_fanza_product_ctas(payload) is True
    cards = [block for block in payload["blocks"] if block.get("type") == "product_cta"]
    assert len(cards) == 1
    assert cards[0]["match_type"] == "exact_image"


class _FakeComposer:
    def __init__(self, keyboard: _FakeKeyboard) -> None:
        self.keyboard = keyboard

    def evaluate(self, _script: str, value: str) -> bool:
        self.keyboard.text = value
        return True

    def is_visible(self) -> bool:
        return True

    def input_value(self, **_kwargs: object) -> str:
        raise RuntimeError("contenteditable")

    def inner_text(self, **_kwargs: object) -> str:
        return self.keyboard.text


class _FakePage:
    def __init__(self) -> None:
        self.keyboard = _FakeKeyboard()
        self.composer = _FakeComposer(self.keyboard)

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def locator(self, _selector: str) -> "_FakeCandidates":
        return _FakeCandidates(self.composer)


class _FakeCandidates:
    def __init__(self, composer: _FakeComposer) -> None:
        self.composer = composer

    def count(self) -> int:
        return 1

    def nth(self, _index: int) -> _FakeComposer:
        return self.composer

    @property
    def first(self) -> _FakeComposer:
        return self.composer


class SiteRegistryTests(unittest.TestCase):
    def test_long_chatgpt_prompt_uses_fast_text_insertion(self) -> None:
        page = _FakePage()
        prompt = ("長い解析指示です。\n" * 8000).strip()

        _set_chatgpt_prompt(page, page.composer, prompt)

        self.assertEqual(prompt, page.keyboard.text)

    def test_linked_full_size_image_is_considered_before_thumbnail(self) -> None:
        urls = _image_candidate_urls({
            "url": "https://awsimgsrc.dmm.co.jp/sample.jpg?w=120&h=90",
            "urls": ["https://awsimgsrc.dmm.co.jp/sample.jpg?w=120&h=90"],
            "link_url": "https://awsimgsrc.dmm.co.jp/sample-large.jpg",
        })

        self.assertEqual(
            "https://awsimgsrc.dmm.co.jp/sample-large.jpg",
            urls[0],
        )
        self.assertIn(
            "https://awsimgsrc.dmm.co.jp/sample.jpg?w=120&h=90",
            urls,
        )

    def test_stale_ohayua_twitter_image_link_recovers_pbs_asset(self) -> None:
        urls = _image_candidate_urls({
            "url": "https://ohayua.cyou/twimg/GEJFGyebAAADAos.jpg",
            "urls": ["https://ohayua.cyou/twimg/GEJFGyebAAADAos.jpg"],
            "link_url": "https://ohayua.cyou/twimg/GEJFGyebAAADAos.jpg",
        })

        self.assertEqual(
            "https://pbs.twimg.com/media/GEJFGyebAAADAos?format=jpg&name=large",
            urls[0],
        )

    def test_dmm_iframe_is_removed_when_same_product_mp4_exists(self) -> None:
        self.assertTrue(_redundant_dmm_player(
            "https://www.dmm.co.jp/service/digitalapi/-/html5_player/=/cid=hsoda00069/",
            "iframe",
            "https://video.dmm.co.jp/av/content/?id=hsoda00069",
            ["https://cc3001.dmm.co.jp/pv/path/hsoda00069mhb.mp4"],
        ))
        self.assertFalse(_redundant_dmm_player(
            "https://player.example.com/embed/other",
            "iframe",
            "https://video.dmm.co.jp/av/content/?id=hsoda00069",
            ["https://cc3001.dmm.co.jp/pv/path/hsoda00069mhb.mp4"],
        ))
        self.assertFalse(_plausible_video_candidate(
            "https://video.dmm.co.jp/av/content/?id=hsoda00069",
            "iframe",
            "text/html",
            "https://video.dmm.co.jp/av/content/?id=hsoda00069",
        ))
        self.assertFalse(_plausible_video_candidate(
            "https://10201484.fls.doubleclick.net/activityi",
            "iframe",
            "text/html",
            "https://video.dmm.co.jp/av/content/?id=hsoda00069",
        ))

    def test_fanza_sample_video_detection_requires_official_same_product_media(self) -> None:
        self.assertTrue(is_fanza_official_sample_video_url(
            "https://cc3001.dmm.co.jp/litevideo/freepv/h/hsoda/hsoda00069/hsoda00069mhb.mp4",
            "hsoda00069",
        ))
        self.assertTrue(is_fanza_official_sample_video_url(
            "https://www.dmm.co.jp/service/digitalapi/-/html5_player/=/cid=hsoda00069/",
            "hsoda00069",
        ))
        self.assertFalse(is_fanza_official_sample_video_url(
            "https://10201484.fls.doubleclick.net/activityi",
            "hsoda00069",
        ))
        self.assertFalse(is_fanza_official_sample_video_url(
            "https://cc3001.dmm.co.jp/litevideo/freepv/a/abc/abc001/abc001mhb.mp4",
            "hsoda00069",
        ))

    def test_fanza_review_mode_excludes_sample_videos_by_default(self) -> None:
        product_url = "https://video.dmm.co.jp/av/content/?id=hsoda00069"
        package_url = "https://pics.dmm.co.jp/digital/video/hsoda00069/hsoda00069pl.jpg"
        with patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_package",
            return_value={
                "id": "fanza-exact-package",
                "url": package_url,
                "rights_source_url": package_url,
                "alt": "package",
                "extension": ".jpg",
                "mime_type": "image/jpeg",
                "data": b"image",
                "width": 800,
                "height": 1200,
                "orientation": "portrait",
                "rights_basis": "fanza_product_main_image",
                "product_id": "hsoda00069",
            },
        ), patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_samples",
            return_value=[],
        ):
            result = restrict_source_to_fanza_product({
                "url": product_url,
                "requested_url": product_url,
                "title": "HSODA-069",
                "images": [],
                "videos": [
                    {
                        "id": "raw-video-1",
                        "kind": "direct",
                        "url": "https://cc3001.dmm.co.jp/litevideo/freepv/h/hsoda/hsoda00069/hsoda00069mhb.mp4",
                        "mime_type": "video/mp4",
                    },
                    {
                        "id": "ad-video",
                        "kind": "iframe",
                        "url": "https://ads.example.com/player",
                        "mime_type": "text/html",
                    },
                ],
                "links": [],
            })

        self.assertEqual(["video-1"], result["recommended_video_ids"])
        self.assertEqual(1, len(result["videos"]))

    def test_page_product_link_without_media_mapping_is_not_treated_as_exact(self) -> None:
        result = _resolve_fanza_promotion(
            {
                "url": "https://example.com/review",
                "title": "新人AV女優のデビュー作を見た",
                "links": [
                    {
                        "url": "https://al.dmm.co.jp/?lurl=https%3A%2F%2Fwww.dmm.co.jp%2Fdigital%2Fvideoa%2F-%2Fdetail%2F%3D%2Fcid%3Dabc001%2F&af_id=other",
                        "text": "作品を見る",
                    },
                ],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertIsNone(result)

    def test_fanza_mode_does_not_publish_an_unverified_product_code_search(self) -> None:
        result = _resolve_fanza_promotion(
            {
                "url": "https://example.com/review",
                "title": "ABP-123のレビュー",
                "links": [],
            },
            {"content_mode": "fanza_product", "promotion_type": "affiliate"},
        )
        self.assertIsNone(result)

    def test_unrelated_article_does_not_get_a_fanza_link(self) -> None:
        self.assertIsNone(_resolve_fanza_promotion(
            {"url": "https://example.com/news", "title": "今日の天気", "links": []},
            {"content_mode": "auto", "promotion_type": "organic"},
        ))

    def test_generic_genre_article_does_not_get_a_useless_fanza_search(self) -> None:
        result = _resolve_fanza_promotion(
            {
                "url": "https://example.com/exposure",
                "title": "露出系の動画が攻めすぎ",
                "description": "野外露出の短い動画を紹介",
                "links": [],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertIsNone(result)

    def test_codex_performer_name_does_not_publish_a_search_page(self) -> None:
        result = _resolve_fanza_promotion(
            {
                "url": "https://example.com/movie",
                "title": "宮下玲奈が可愛すぎる",
                "ai_fanza_relevance": "likely_product",
                "ai_fanza_performer_name": "宮下玲奈",
                "ai_fanza_search_query": "宮下玲奈",
                "links": [{
                    "url": "https://www.dmm.co.jp/digital/videoa/-/actress/=/id=12345/",
                    "text": "宮下玲奈 出演者ページ",
                }],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertIsNone(result)

    def test_direct_fanza_source_adds_disclosure_and_product_card(self) -> None:
        payload = {
            "tags": ["画像"],
            "blocks": [
                {"id": "lead", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=abp123/",
                "title": "ABP-123のレビュー",
                "links": [],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertEqual("affiliate", payload["promotion_type"])
        self.assertNotIn("FANZA", payload["tags"])
        product = next(block for block in payload["blocks"] if block["type"] == "product_cta")
        self.assertEqual(
            "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=abp123/",
            product["url"],
        )
        self.assertEqual("この画像の商品", product["placement_label"])
        self.assertIn("アフィリエイト広告", payload["transparency_note"])

    def test_exact_fanza_product_card_uses_official_package_as_thumbnail(self) -> None:
        payload = {
            "content_mode": "fanza_product",
            "tags": [],
            "blocks": [{"id": "post-1", "type": "post", "text": "本文"}],
            "images": [{
                "id": "source-image-1",
                "rights_basis": "fanza_product_main_image",
            }],
        }
        payload["blocks"].insert(0, {
            "id": "lead-image",
            "type": "images",
            "image_ids": ["source-image-1"],
        })
        promotion = {
            "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "title": "作品名",
            "text": "作品情報",
            "button_text": "FANZAでこの作品を見る",
            "match_level": "exact",
        }
        with patch(
            "indanya_desktop.workers._resolve_fanza_promotion",
            return_value=promotion,
        ), patch(
            "indanya_desktop.workers._resolve_verified_fanza_recommendations",
            return_value=[],
        ):
            _apply_editorial_metadata(
                payload,
                {
                    "requested_url": "https://video.dmm.co.jp/av/content/?id=abc001",
                    "url": "https://video.dmm.co.jp/av/content/?id=abc001",
                    "media_rights_profile": FANZA_MEDIA_PROFILE,
                },
                {"content_mode": "fanza_product"},
                ROOT,
            )
        card = next(block for block in payload["blocks"] if block["type"] == "product_cta")
        self.assertEqual("source-image-1", card["thumbnail_image_id"])

    def test_exact_product_card_is_placed_after_the_first_media(self) -> None:
        payload = {
            "tags": ["動画"],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "これ"},
                {"id": "videos", "type": "videos", "video_ids": ["video-1"]},
                {"id": "post-2", "type": "post", "text": "強い"},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://video.dmm.co.jp/av/content/?id=midv00461",
                "title": "MIDV-461",
                "links": [],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertEqual(
            ["post", "videos", "product_cta", "post", "ad"],
            [block["type"] for block in payload["blocks"]],
        )

    def test_tiktoker_profile_is_not_suppressed_by_a_product_card(self) -> None:
        payload = {
            "slug": "tiktoker-profile",
            "title": "TikToker みおの画像",
            "tags": ["TikToker", "みお"],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "本人の投稿を紹介"},
                {"id": "images", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        promotion = {
            "url": "https://video.dmm.co.jp/av/content/?id=sample001",
            "title": "関連作品",
            "text": "記事に近い作品",
            "button_text": "FANZAで見る",
            "match_level": "related",
        }
        with patch(
            "indanya_desktop.workers._resolve_fanza_promotion",
            return_value=promotion,
        ), patch(
            "indanya_desktop.workers._resolve_verified_fanza_recommendations",
            return_value=[],
        ):
            _apply_editorial_metadata(
                payload,
                {
                    "url": "https://example.com/mio",
                    "title": "TikToker みおの画像",
                    "links": [{
                        "url": "https://www.tiktok.com/@mio_sample/video/1234567890",
                        "text": "TikTok",
                    }],
                },
                {
                    "content_mode": "auto",
                    "promotion_type": "organic",
                    "fanza_url": "https://video.dmm.co.jp/av/content/?id=sample001",
                },
            )

        products = [block for block in payload["blocks"] if block["type"] == "product_cta"]
        profiles = [
            block for block in payload["blocks"]
            if block["type"] == "related_link"
            and block.get("link_kind") == "official_profile"
        ]
        recommendations = [
            block for block in payload["blocks"]
            if block["type"] == "related_link"
            and block.get("link_kind") != "official_profile"
        ]
        self.assertEqual(1, len(products))
        self.assertEqual(1, len(profiles))
        self.assertEqual(1, len(recommendations))
        self.assertEqual(["tiktok"], [profile["provider"] for profile in profiles])
        self.assertEqual(
            ["本人の公式アカウント"],
            [profile["placement_label"] for profile in profiles],
        )
        recommendation_index = payload["blocks"].index(recommendations[0])
        profile_index = payload["blocks"].index(profiles[0])
        self.assertGreater(
            profile_index,
            recommendation_index,
            "本人アカウントは関連PRの後に一度だけ表示する",
        )

    def test_genre_only_article_gets_no_guessed_product(self) -> None:
        payload = {
            "tags": ["動画"],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "これ"},
                {"id": "videos", "type": "videos", "video_ids": ["video-1"]},
                {"id": "post-2", "type": "post", "text": "強い"},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://example.com/exposure",
                "title": "露出系の動画",
                "links": [],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertEqual(
            ["post", "videos", "post", "ad"],
            [block["type"] for block in payload["blocks"]],
        )

    def test_named_people_without_product_pages_get_named_search_links(self) -> None:
        payload = {
            "tags": ["画像"],
            "images": [
                {"id": "source-image-1", "source_id": "media-a"},
                {"id": "source-image-2", "source_id": "media-b"},
            ],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "まず一人目"},
                {"id": "images-a", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "post-2", "type": "post", "text": "次は二人目"},
                {"id": "images-b", "type": "images", "image_ids": ["source-image-2"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://example.com/gallery",
                "title": "出演者が複数いる画像記事",
                "ai_fanza_people": [
                    {
                        "name": "宮下玲奈",
                        "image_ids": ["media-a"],
                        "reason": "画像直前の見出しに名前がある",
                    },
                    {
                        "name": "石川澪",
                        "image_ids": ["media-b"],
                        "reason": "画像のキャプションに名前がある",
                    },
                ],
                "links": [{
                    "url": "https://www.dmm.co.jp/digital/videoa/-/actress/=/id=12345/",
                    "text": "出演者ページ",
                }],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        self.assertEqual(
            [
                "post",
                "images",
                "post",
                "images",
                "related_link",
                "related_link",
                "ad",
            ],
            [block["type"] for block in payload["blocks"]],
        )
        related = [
            block for block in payload["blocks"]
            if block["type"] == "related_link"
        ]
        self.assertEqual(
            ["宮下玲奈の出演作品", "石川澪の出演作品"],
            [block["title"] for block in related],
        )
        self.assertTrue(all(
            block["link_kind"] == "verified_person_search"
            for block in related
        ))
        self.assertFalse(any(
            block["type"] == "product_cta" for block in payload["blocks"]
        ))

    def test_theme_search_products_are_not_published_without_media_mapping(self) -> None:
        payload = {
            "tags": ["画像"],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "露出の画像"},
                {"id": "images", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://example.com/exposure",
                "title": "屋外露出の画像",
                "verified_fanza_products": [
                    {
                        "product_id": "aaa001",
                        "url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                        "title": "屋外露出デート作品 AAA-001",
                        "matched_query": "屋外 露出",
                    },
                    {
                        "product_id": "bbb002",
                        "url": "https://video.dmm.co.jp/av/content/?id=bbb002",
                        "title": "公共露出チャレンジ BBB-002",
                        "matched_query": "公共 露出",
                    },
                ],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        products = [
            block for block in payload["blocks"] if block["type"] == "product_cta"
        ]
        self.assertEqual([], products)

    def test_each_exact_av_product_is_inserted_after_its_own_image_group(self) -> None:
        payload = {
            "tags": ["画像"],
            "images": [
                {"id": f"source-image-{index}", "source_id": f"media-{index}"}
                for index in range(1, 6)
            ],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "画像を見ていく"},
                {
                    "id": "all-images",
                    "type": "images",
                    "image_ids": [f"source-image-{index}" for index in range(1, 6)],
                },
                {"id": "post-2", "type": "post", "text": "どっちもええな"},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        source = {
            "url": "https://example.com/mixed-av",
            "title": "二作品の画像",
            "verified_fanza_image_products": [
                {
                    "url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                    "title": "作品A",
                    "image_ids": ["media-1", "media-2"],
                },
                {
                    "url": "https://video.dmm.co.jp/av/content/?id=bbb002",
                    "title": "作品B",
                    "image_ids": ["media-3", "media-4", "media-5"],
                },
            ],
            "verified_fanza_products": [{
                "url": "https://video.dmm.co.jp/av/content/?id=unrelated003",
                "title": "関連作品",
                "matched_query": "制服",
            }],
            "links": [],
        }

        _apply_editorial_metadata(
            payload,
            source,
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        self.assertEqual(
            ["post", "images", "product_cta", "images", "product_cta", "post", "ad"],
            [block["type"] for block in payload["blocks"]],
        )
        first_images, first_pr, second_images, second_pr = payload["blocks"][1:5]
        self.assertEqual(["source-image-1", "source-image-2"], first_images["image_ids"])
        self.assertEqual("作品A", first_pr["title"])
        self.assertEqual("source-image-1", first_pr["thumbnail_image_id"])
        self.assertEqual(
            ["source-image-3", "source-image-4", "source-image-5"],
            second_images["image_ids"],
        )
        self.assertEqual("作品B", second_pr["title"])
        self.assertFalse(any(
            block.get("title") == "関連作品" for block in payload["blocks"]
        ))

    def test_theme_recommendation_is_left_to_internal_related_articles(self) -> None:
        payload = {
            "tags": ["画像"],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "制服画像"},
                {"id": "images", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "post-2", "type": "post", "text": "制服が中心やな"},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            {
                "url": "https://example.com/amateur-gallery",
                "title": "制服画像まとめ",
                "verified_fanza_image_products": [],
                "verified_fanza_products": [{
                    "url": "https://video.dmm.co.jp/av/content/?id=uniform001",
                    "title": "制服が主題の実在作品",
                    "matched_query": "制服",
                    "thumbnail_url": "https://pics.dmm.co.jp/example.jpg",
                }],
                "links": [],
            },
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        self.assertEqual(
            ["post", "images", "post", "ad"],
            [block["type"] for block in payload["blocks"]],
        )

    def test_direct_product_image_mapping_is_verified_without_a_search(self) -> None:
        source = {
            "ai_fanza_image_products": [{
                "product_title": "作品A",
                "product_code": "AAA-001",
                "product_url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                "image_ids": ["media-1", "media-2"],
                "reason": "作品名と商品リンクが画像の直前にある",
            }],
            "ai_fanza_recommendation_queries": ["制服"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=[],
            ) as discover, patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=None,
            ):
                _attach_verified_fanza_products(source, Path(temporary))

        discover.assert_not_called()
        self.assertEqual([], source["verified_fanza_products"])
        self.assertEqual(
            ["media-1", "media-2"],
            source["verified_fanza_image_products"][0]["image_ids"],
        )

    def test_direct_product_mapping_rejects_a_mismatched_product_code(self) -> None:
        source = {
            "url": "https://example.com/article",
            "ai_fanza_image_products": [{
                "product_title": "無関係な作品",
                "product_code": "AAA-001",
                "product_url": "https://video.dmm.co.jp/av/content/?id=bbb002",
                "image_ids": ["media-1"],
                "video_ids": [],
                "reason": "広告リンクを誤認",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=[],
            ) as discover, patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=None,
            ):
                _attach_verified_fanza_products(source, Path(temporary))

        discover.assert_not_called()
        self.assertEqual([], source["verified_fanza_media_products"])

    def test_same_product_image_and_video_mappings_merge_and_prefer_video(self) -> None:
        source = {
            "url": "https://example.com/article",
            "ai_fanza_image_products": [
                {
                    "product_title": "作品A",
                    "product_code": "AAA-001",
                    "product_url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                    "image_ids": ["media-image"],
                    "video_ids": [],
                    "reason": "パッケージ画像に品番がある",
                },
                {
                    "product_title": "作品A",
                    "product_code": "AAA-001",
                    "product_url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                    "image_ids": [],
                    "video_ids": ["media-video"],
                    "reason": "動画の直前に同じ品番がある",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=None,
            ):
                _attach_verified_fanza_products(source, Path(temporary))

        self.assertEqual(1, len(source["verified_fanza_media_products"]))
        product = source["verified_fanza_media_products"][0]
        self.assertEqual(["media-image"], product["image_ids"])
        self.assertEqual(["media-video"], product["video_ids"])

        payload = {
            "tags": ["動画"],
            "images": [{"id": "image-1", "source_id": "media-image"}],
            "videos": [{"id": "video-1", "source_id": "media-video"}],
            "blocks": [
                {"id": "images", "type": "images", "image_ids": ["image-1"]},
                {"id": "videos", "type": "videos", "video_ids": ["video-1"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        _apply_editorial_metadata(
            payload,
            source,
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertEqual(
            ["images", "videos", "product_cta", "ad"],
            [block["type"] for block in payload["blocks"]],
        )
        self.assertEqual("exact_video", payload["blocks"][2]["match_type"])

    def test_exact_product_code_uses_the_namespaced_cache_key(self) -> None:
        source = {
            "title": "AAA-001のサンプル動画",
            "ai_fanza_image_products": [{
                "product_title": "作品A",
                "product_code": "AAA-001",
                "product_url": "",
                "image_ids": [],
                "video_ids": ["video-a"],
                "reason": "動画直前に品番AAA-001が明記されている",
            }],
        }
        discovered = [{
            "matched_query": "AAA-001",
            "product_id": "aaa001",
            "url": "https://video.dmm.co.jp/av/content/?id=aaa001",
            "title": "作品A AAA-001",
            "thumbnail_url": "https://pics.dmm.co.jp/aaa001.jpg",
        }]
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=discovered,
            ), patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=None,
            ):
                _attach_verified_fanza_products(source, Path(temporary))

        product = source["verified_fanza_media_products"][0]
        self.assertEqual(["video-a"], product["video_ids"])
        self.assertEqual("exact_product_code", product["evidence_type"])
        self.assertEqual("https://video.dmm.co.jp/av/content/?id=aaa001", product["url"])

    def test_selected_media_filename_product_code_is_verified_without_ai_mapping(self) -> None:
        source = {
            "url": "https://example.com/article",
            "title": "小那海あやのサンプル画像",
            "recommended_thumbnail_ids": ["media-1"],
            "recommended_body_image_ids": ["media-1"],
            "images": [{
                "id": "media-1",
                "url": "https://blog-imgs-182.example.com/a/savr-1195_20260901_sns.jpg",
            }],
        }
        discovered = [{
            "matched_query": "SAVR-1195",
            "product_id": "savr1195",
            "url": "https://video.dmm.co.jp/av/content/?id=savr1195",
            "title": "小那海あや SAVR-1195",
            "thumbnail_url": "https://pics.dmm.co.jp/digital/video/savr1195/savr1195pl.jpg",
        }]
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=discovered,
            ) as discover, patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=None,
            ):
                _attach_verified_fanza_products(source, Path(temporary))

        discover.assert_called_once_with(
            ["SAVR-1195"], limit_per_query=2, product_kind="video"
        )
        product = source["verified_fanza_media_products"][0]
        self.assertEqual(["media-1"], product["image_ids"])
        self.assertEqual("savr1195", product["product_id"])
        self.assertEqual("exact_product_code", product["evidence_type"])

    def test_selected_media_filename_does_not_guess_between_multiple_product_codes(self) -> None:
        source = {
            "url": "https://example.com/article",
            "recommended_body_image_ids": ["media-1", "media-2"],
            "images": [
                {"id": "media-1", "url": "https://cdn.example.com/SAVR-1195_01.jpg"},
                {"id": "media-2", "url": "https://cdn.example.com/DASS-949_01.jpg"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=[],
            ) as discover:
                _attach_verified_fanza_products(source, Path(temporary))

        discover.assert_not_called()
        self.assertEqual([], source["verified_fanza_media_products"])

    def test_selected_media_filename_ignores_dimensions_and_news_suffixes(self) -> None:
        source = {
            "url": "https://example.com/article",
            "recommended_body_image_ids": ["media-1", "media-2"],
            "images": [
                {"id": "media-1", "url": "https://cdn.example.com/photo-800x1000.jpg"},
                {"id": "media-2", "url": "https://cdn.example.com/mantan-000-1-view.jpg"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.discover_fanza_products",
                return_value=[],
            ) as discover:
                _attach_verified_fanza_products(source, Path(temporary))

        discover.assert_not_called()
        self.assertEqual([], source["verified_fanza_media_products"])

    def test_exact_product_url_fetches_its_official_package_when_source_lacks_it(self) -> None:
        source = {
            "url": "https://example.com/article",
            "ai_fanza_image_products": [{
                "product_title": "作品A",
                "product_code": "AAA-001",
                "product_url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                "image_ids": ["media-1"],
                "video_ids": [],
                "reason": "本文の品番と作品URLが一致",
            }],
        }
        package = {
            "url": "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/aaa001/aaa001pl.jpg",
            "rights_basis": "fanza_product_main_image",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "indanya_desktop.workers.download_exact_fanza_package",
                return_value=package,
            ) as fetch:
                _attach_verified_fanza_products(source, Path(temporary))

        product = source["verified_fanza_media_products"][0]
        fetch.assert_called_once_with("aaa001")
        self.assertEqual(package["url"], product["thumbnail_url"])
        self.assertEqual("fanza_package", product["thumbnail_source_kind"])
        self.assertEqual(product["url"], product["thumbnail_owner_url"])

    def test_unique_same_subject_product_below_gallery_is_inferred(self) -> None:
        source = {
            "url": "https://example.com/article",
            "title": "百田光稀の画像まとめ",
            "ai_main_subject": {"kind": "person", "name": "百田光稀"},
            "recommended_thumbnail_ids": ["media-1"],
            "recommended_body_image_ids": ["media-1", "media-2"],
            "recommended_video_ids": ["video-1"],
            "images": [
                {
                    "id": "media-1",
                    "browser_rect": {"y": 3000, "height": 500},
                    "browser_ancestors": "img > div#more > div.content > div#main",
                },
                {
                    "id": "media-2",
                    "browser_rect": {"y": 5000, "height": 600},
                    "browser_ancestors": "img > div#more > div.content > div#main",
                },
            ],
            "videos": [{
                "id": "video-1",
                "browser_rect": {"y": 6800, "height": 500},
                "browser_ancestors": "video > div#more > div.content > div#main",
            }],
            "links": [
                {
                    "text": "国宝級ボディ 百田光稀",
                    "url": "https://al.fanza.co.jp/?lurl=https%3A%2F%2Fvideo.dmm.co.jp%2Fav%2Fcontent%2F%3Fid%3Dmida00763",
                    "browser_ancestors": "a > div.wakupr > div#more > div.content > div#main",
                    "browser_context": "国宝級ボディ 百田光稀",
                    "browser_rect": {"y": 6400, "height": 30},
                },
                {
                    "text": "無料サンプルを見る",
                    "url": "https://video.dmm.co.jp/av/content/?id=mida00763",
                    "browser_ancestors": "a > div.wakupr > div#more > div.content > div#main",
                    "browser_context": "百田光稀の作品",
                    "browser_rect": {"y": 6470, "height": 30},
                },
            ],
        }

        inferred = _infer_single_embedded_fanza_product(source)

        self.assertEqual(1, len(inferred))
        self.assertEqual("mida00763", inferred[0]["product_code"])
        self.assertEqual(["media-1", "media-2"], inferred[0]["image_ids"])
        self.assertEqual(["video-1"], inferred[0]["video_ids"])
        self.assertEqual(
            ["https://video.dmm.co.jp/av/content/?id=mida00763"],
            source["verified_embedded_fanza_product_urls"],
        )

    def test_sidebar_or_wrong_subject_product_is_not_inferred(self) -> None:
        base = {
            "url": "https://example.com/article",
            "title": "百田光稀の画像まとめ",
            "ai_main_subject": {"kind": "person", "name": "百田光稀"},
            "recommended_body_image_ids": ["media-1"],
            "images": [{
                "id": "media-1",
                "browser_rect": {"y": 1000, "height": 500},
                "browser_ancestors": "img > div.content > div#main",
            }],
            "videos": [],
        }
        sidebar = {
            **base,
            "links": [{
                "text": "百田光稀のおすすめ作品",
                "url": "https://video.dmm.co.jp/av/content/?id=mida00763",
                "browser_ancestors": "a > div.sidebar > div.content",
                "browser_rect": {"y": 1600, "height": 30},
            }],
        }
        wrong_subject = {
            **base,
            "links": [{
                "text": "別人の作品",
                "url": "https://video.dmm.co.jp/av/content/?id=other001",
                "browser_ancestors": "a > div.wakupr > div.content > div#main",
                "browser_context": "別人の作品",
                "browser_rect": {"y": 1600, "height": 30},
            }],
        }

        self.assertEqual([], _infer_single_embedded_fanza_product(sidebar))
        self.assertEqual([], _infer_single_embedded_fanza_product(wrong_subject))

    def test_quality_block_writes_compact_diagnostic(self) -> None:
        payload = {"slug": "blocked", "images": [{"data_url": "data:image/png;base64,AAA"}]}
        source = {"url": "https://example.com/article", "images": [{"data": b"abc"}]}
        report = {"effective_decision": "discard", "blockers": ["bad_media"]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("indanya_desktop.workers.apply_quality_gate", return_value=report):
                with self.assertRaises(EditorialPolicyError):
                    _apply_adaptive_quality(root, payload, source)
            saved = json.loads(
                (root / ".article-studio" / "quality-blocks" / "latest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(["bad_media"], saved["report"]["blockers"])
        self.assertNotIn("data", saved["source"]["images"][0])
        self.assertEqual("<data-url:25>", saved["payload"]["images"][0]["data_url"])

    def test_each_exact_video_product_is_inserted_below_its_video(self) -> None:
        payload = {
            "tags": ["動画"],
            "videos": [
                {"id": "source-video-1", "source_id": "video-a"},
                {"id": "source-video-2", "source_id": "video-b"},
            ],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "動画を見ていく"},
                {
                    "id": "all-videos",
                    "type": "videos",
                    "video_ids": ["source-video-1", "source-video-2"],
                },
                {"id": "post-2", "type": "post", "text": "どっちも別作品やな"},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }
        source = {
            "url": "https://example.com/two-videos",
            "title": "二作品の動画",
            "verified_fanza_media_products": [
                {
                    "url": "https://video.dmm.co.jp/av/content/?id=aaa001",
                    "title": "作品A",
                    "image_ids": [],
                    "video_ids": ["video-a"],
                    "reason": "動画Aの直前に商品リンクがある",
                    "match_confidence": 98,
                },
                {
                    "url": "https://video.dmm.co.jp/av/content/?id=bbb002",
                    "title": "作品B",
                    "image_ids": [],
                    "video_ids": ["video-b"],
                    "reason": "動画Bの直前に商品リンクがある",
                    "match_confidence": 98,
                },
            ],
        }

        _apply_editorial_metadata(
            payload,
            source,
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        self.assertEqual(
            ["post", "videos", "product_cta", "videos", "product_cta", "post", "ad"],
            [block["type"] for block in payload["blocks"]],
        )
        cards = [block for block in payload["blocks"] if block["type"] == "product_cta"]
        self.assertEqual(["作品A", "作品B"], [card["title"] for card in cards])
        self.assertTrue(all(card["placement_label"] == "この動画の商品" for card in cards))
        self.assertTrue(all(card["match_type"] == "exact_video" for card in cards))

    def test_named_youtuber_gets_no_unverified_fanza_recommendation(self) -> None:
        source = {
            "url": "https://example.com/youtuber",
            "title": "温泉系YouTuberちゃづりを紹介",
            "description": "浴室で撮影したYouTube動画と写真を紹介する記事",
            "ai_fanza_relevance": "likely_product",
            "ai_fanza_performer_name": "ちゃづり",
            "ai_fanza_search_query": "ちゃづり",
            "ai_fanza_people": [{
                "name": "ちゃづり",
                "image_ids": ["media-a"],
                "reason": "本文に名前がある",
            }],
            "links": [{
                "url": "https://al.fanza.co.jp/?lurl=https%3A%2F%2Fwww.dmm.co.jp%2Fdigital%2F-%2Fwelcome-coupon%2F",
                "text": "FANZAクーポン広告",
            }],
        }
        payload = {
            "tags": ["YouTuber"],
            "images": [{"id": "source-image-1", "source_id": "media-a"}],
            "blocks": [
                {"id": "post-1", "type": "post", "text": "この人の動画"},
                {"id": "images", "type": "images", "image_ids": ["source-image-1"]},
                {"id": "ad", "type": "ad", "text": "広告"},
            ],
        }

        self.assertIsNone(_resolve_fanza_promotion(
            source,
            {"content_mode": "auto", "promotion_type": "organic"},
        ))
        _apply_editorial_metadata(
            payload,
            source,
            {"content_mode": "auto", "promotion_type": "organic"},
        )

        self.assertEqual("web", payload["content_mode"])
        self.assertEqual("organic", payload["promotion_type"])
        self.assertNotIn("FANZA", payload["tags"])
        self.assertFalse(any(
            block["type"] == "product_cta" for block in payload["blocks"]
        ))

    def test_fanza_floor_follows_article_medium(self) -> None:
        self.assertEqual("anime", _fanza_product_kind({
            "title": "二次元アニメの新作を紹介",
        }))
        self.assertEqual("comic", _fanza_product_kind({
            "title": "成人漫画の新刊を紹介",
        }))
        self.assertEqual("doujin", _fanza_product_kind({
            "title": "同人CG集を紹介",
        }))
        self.assertEqual("video", _fanza_product_kind({
            "title": "満員電車のAI生成動画",
        }))

    def test_anime_article_does_not_use_a_hardcoded_fallback_product(self) -> None:
        payload = {
            "tags": ["アニメ"],
            "images": [],
            "blocks": [{"id": "post", "type": "post", "text": "感想"}],
        }
        _apply_editorial_metadata(
            payload,
            {"url": "https://example.com/anime", "title": "二次元アニメ作品"},
            {"content_mode": "auto", "promotion_type": "organic"},
        )
        self.assertFalse(any(
            block["type"] == "product_cta" for block in payload["blocks"]
        ))

    def test_discovered_product_keeps_canonical_url_until_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_fanza_settings(root, "my-affiliate-001")
            result = _resolve_fanza_promotion(
                {
                    "url": "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=abc001/",
                    "title": "AV作品レビュー",
                    "links": [],
                },
                {"content_mode": "auto", "promotion_type": "organic"},
                root,
            )
        self.assertEqual(
            "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=abc001/",
            result["url"],
        )
        self.assertNotIn("af_id=", result["url"])

    def test_generated_desktop_articles_require_source_policy_evidence(self) -> None:
        product_url = "https://video.dmm.co.jp/av/content/?id=abc001"
        package_url = "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg"
        payload = {
            "rights_status": "unconfirmed",
            "title": "【画像】成人女性のヌードグラビア、浴室とベッドで変わる構図を紹介",
            "summary": "成人女性の全裸グラビアを、浴室とベッドで変わる光や構図、寄りと全身の切り替えが分かる公式商品画像とともに紹介する記事です。",
            "source_url": product_url,
            "content_mode": "fanza_product",
            "fanza_product_id": "abc001",
            "media_rights_profile": FANZA_MEDIA_PROFILE,
            "images": [
                {
                    "id": "media-1",
                    "source_url": package_url,
                    "rights_basis": "fanza_product_main_image",
                },
                {
                    "id": "media-2",
                    "source_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
                    "rights_basis": "fanza_product_sample_image",
                },
            ],
            "videos": [],
            "blocks": [
                {"type": "post", "text": "浴室の光が肌へ反射して、ベッドの写真とは違う雰囲気になっている。同じ人物でも撮影場所で見え方が変わる。"},
                {"type": "post", "text": "構図と表情の変化を追うと、写真の並びにもきちんと流れがある。寄りと全身の切り替えも自然に見える。"},
                {"type": "post", "text": "元ページの説明を写すのではなく、実際に選んだ画像の順番と見どころに沿って反応を組み立てる。"},
                {"type": "images", "image_ids": ["media-1", "media-2"]},
                {"type": "product_cta", "url": product_url, "text": "作品ページを見る"},
            ],
            "media_alignment_checked": True,
            "adult_confirmed": False,
            "rights_confirmed": False,
            "privacy_confirmed": False,
            "source_confirmed": False,
        }
        source = {
            "url": product_url,
            "requested_url": product_url,
            "title": "成人女性のヌードグラビア",
            "description": "乳首が写る成人向けヌード作品です。",
            "ai_adult_content": True,
            "ai_adult_reason": "本編に成人女性の全裸と乳首が写っています。",
            "ai_analysis_summary": "本編は成人向けヌードグラビアです。",
            "ai_fanza_relevance": "exact_product",
            "recommended_image_ids": ["media-1", "media-2"],
            "media_rights_profile": FANZA_MEDIA_PROFILE,
            "images": [
                {
                    "id": "media-1",
                    "url": package_url,
                    "rights_basis": "fanza_product_main_image",
                },
                {
                    "id": "media-2",
                    "url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
                    "rights_basis": "fanza_product_sample_image",
                },
            ],
        }
        updated = _mark_ready_to_publish(payload, source)
        self.assertEqual("confirmed", updated["rights_status"])
        self.assertTrue(updated["adult_confirmed"])
        self.assertTrue(updated["rights_confirmed"])
        self.assertTrue(updated["privacy_confirmed"])
        self.assertTrue(updated["source_confirmed"])

    def test_article_image_selection_follows_codex_and_avoids_advertisements(self) -> None:
        source = {
            "recommended_image_ids": ["thumb", "main-1", "main-2"],
            "recommended_thumbnail_ids": ["thumb"],
            "recommended_body_image_ids": ["main-1", "main-2"],
            "images": [
                {
                    "id": "thumb",
                    "url": "https://example.com/mosaic-thumbnail.jpg",
                    "width": 240,
                    "height": 135,
                    "source_score": -80,
                    "ai_verdict": "article",
                    "ai_role": "article_thumbnail",
                    "ai_recommended_use": "thumbnail",
                },
                {
                    "id": "main-1",
                    "url": "https://example.com/uploads/body-a.jpg",
                    "width": 800,
                    "height": 560,
                    "source_score": 90,
                    "ai_verdict": "article",
                    "ai_role": "article_main",
                    "ai_recommended_use": "body",
                    "ai_relevance_score": 94,
                },
                {
                    "id": "main-2",
                    "url": "https://example.com/uploads/body-b.jpg",
                    "width": 800,
                    "height": 540,
                    "source_score": 80,
                    "ai_verdict": "article",
                    "ai_role": "article_gallery",
                    "ai_recommended_use": "body",
                    "ai_relevance_score": 90,
                },
                {
                    "id": "ad",
                    "url": "https://example.com/feedly-follow-banner.webp",
                    "width": 131,
                    "height": 56,
                    "source_score": -120,
                    "ai_verdict": "advertisement",
                },
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("thumb", selected["thumbnail_id"])
        self.assertEqual(["main-1", "main-2"], selected["body_ids"])
        self.assertNotIn("thumb", selected["body_ids"])
        self.assertNotIn("ad", selected["body_ids"])

    def test_article_image_selection_does_not_replace_codex_rejection(self) -> None:
        source = {
            "recommended_image_ids": [],
            "images": [{
                "id": "large-ad",
                "url": "https://example.com/large.jpg",
                "width": 1600,
                "height": 900,
                "ai_verdict": "advertisement",
                "ai_relevance_score": 5,
            }],
        }
        self.assertEqual(
            {"thumbnail_id": "", "body_ids": []},
            _select_article_images(source),
        )

    def test_article_image_selection_keeps_all_approved_images(self) -> None:
        source = {
            "recommended_thumbnail_ids": ["thumb"],
            "recommended_body_image_ids": [f"body-{index}" for index in range(60)],
            "images": [
                {
                    "id": "thumb",
                    "data": b"thumbnail",
                    "ai_recommended_use": "thumbnail",
                    "ai_verdict": "article",
                },
                *[
                    {
                        "id": f"body-{index}",
                        "data": bytes([index]) * 32,
                        "ai_recommended_use": "body",
                        "ai_verdict": "article",
                        "ai_relevance_score": 100 - index,
                    }
                    for index in range(60)
                ],
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("thumb", selected["thumbnail_id"])
        self.assertEqual(60, len(selected["body_ids"]))
        self.assertEqual(61, len({selected["thumbnail_id"], *selected["body_ids"]}))

    def test_named_person_article_excludes_unmatched_and_ungrouped_images(self) -> None:
        source = {
            "ai_main_subject": {"kind": "person", "name": "南ゆい"},
            "recommended_thumbnail_ids": ["subject-thumb"],
            "recommended_body_image_ids": ["subject-body", "other-person", "unknown"],
            "images": [
                {
                    "id": "subject-thumb", "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail", "ai_content_group": "minami-yui",
                },
                {
                    "id": "subject-body", "ai_verdict": "article",
                    "ai_recommended_use": "body", "ai_content_group": "minami-yui",
                },
                {
                    "id": "other-person", "ai_verdict": "article",
                    "ai_recommended_use": "body", "ai_content_group": "mgs-performer",
                },
                {
                    "id": "unknown", "ai_verdict": "article",
                    "ai_recommended_use": "body", "ai_content_group": "",
                },
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("subject-thumb", selected["thumbnail_id"])
        self.assertEqual(["subject-body"], selected["body_ids"])

    def test_named_person_article_excludes_contrast_reply_images(self) -> None:
        contrast = {
            "id": "other-person",
            "browser_context": "30: 名無しさん 僕はこっち （出典 i.imgur.com）",
            "browser_ancestors": "img > a#img_30_1 > div#surebody30",
            "ai_verdict": "article",
            "ai_recommended_use": "thumbnail_and_body",
            "ai_relevance_score": 96,
            "ai_content_group": "hongo-yuzuha",
        }
        source = {
            "ai_main_subject": {"kind": "person", "name": "本郷柚巴"},
            "recommended_thumbnail_ids": ["subject", "other-person"],
            "recommended_body_image_ids": ["subject", "other-person"],
            "images": [
                {
                    "id": "subject",
                    "browser_context": "本郷柚巴のグラビア",
                    "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail_and_body",
                    "ai_relevance_score": 80,
                    "ai_content_group": "hongo-yuzuha",
                },
                contrast,
            ],
        }

        selected = _select_article_images(source)

        self.assertTrue(_is_recommendation_material(contrast))
        self.assertEqual("subject", selected["thumbnail_id"])
        self.assertEqual([], selected["body_ids"])

    def test_named_person_thread_uses_lead_reply_and_drops_unproven_later_images(self) -> None:
        source = {
            "ai_main_subject": {"kind": "person", "name": "本郷柚巴"},
            "recommended_thumbnail_ids": ["reply-26", "reply-1"],
            "recommended_body_image_ids": ["reply-1", "reply-26"],
            "images": [
                {
                    "id": "reply-1",
                    "thread_reply_number": 1,
                    "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail_and_body",
                    "ai_relevance_score": 70,
                    "ai_content_group": "hongo-yuzuha",
                },
                {
                    "id": "reply-26",
                    "thread_reply_number": 26,
                    "alt": "本郷柚巴の記事タイトル",
                    "browser_context": "（出典 i.imgur.com）",
                    "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail_and_body",
                    "ai_relevance_score": 99,
                    "ai_content_group": "hongo-yuzuha",
                },
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("reply-1", selected["thumbnail_id"])
        self.assertEqual([], selected["body_ids"])

    def test_named_person_thread_drops_static_images_without_reply_evidence(self) -> None:
        source = {
            "ai_main_subject": {"kind": "person", "name": "本郷柚巴"},
            "recommended_thumbnail_ids": ["static-cache", "reply-1"],
            "recommended_body_image_ids": ["reply-1", "static-cache"],
            "images": [
                {
                    "id": "reply-1",
                    "thread_reply_number": 1,
                    "browser_ancestors": "a#img_1_8 > div#surebody1",
                    "anchor_href_candidate": True,
                    "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail_and_body",
                    "ai_relevance_score": 70,
                },
                {
                    "id": "static-cache",
                    "inside_article": True,
                    "alt": "本郷柚巴の記事タイトル",
                    "ai_verdict": "article",
                    "ai_recommended_use": "thumbnail_and_body",
                    "ai_relevance_score": 99,
                },
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("reply-1", selected["thumbnail_id"])
        self.assertEqual([], selected["body_ids"])

    def test_gateway_follow_keeps_previous_link_intent(self) -> None:
        first_url = "https://example.com/entry"
        relay_url = "https://example.com/relay?id=123"
        final_url = "https://example.com/article"
        sources = {
            first_url: {
                "source_type": "web", "url": first_url, "title": "入口記事",
                "description": "本編への入口", "site_name": "入口", "author": "",
                "images": [], "videos": [],
                "links": [{"url": relay_url, "text": "目的の記事 volume184"}],
            },
            relay_url: {
                "source_type": "web", "url": relay_url, "title": "リンク集",
                "description": "新着リンク", "site_name": "中継", "author": "",
                "images": [], "videos": [],
                "links": [
                    {"url": "https://example.com/latest", "text": "先頭の別記事"},
                    {"url": final_url, "text": "目的の記事 volume184"},
                ],
            },
            final_url: {
                "source_type": "web", "url": final_url, "title": "本編",
                "description": "画像ギャラリー", "site_name": "本編", "author": "",
                "images": [{
                    "id": "media-1", "url": "https://example.com/main.jpg",
                    "data": b"image", "extension": ".jpg", "mime_type": "image/jpeg",
                    "width": 800, "height": 600,
                }], "videos": [], "links": [],
            },
        }

        class FakeRunner:
            calls = 0

            def compose(self, source: dict[str, object], _options: dict[str, object]) -> dict[str, object]:
                self.calls += 1
                context = source.get("navigation_context")
                self_outer.assertEqual("目的の記事 volume184", context["followed_link_text"])
                analysis = {
                    "title": str(source["title"]),
                    "description": str(source["description"]),
                    "category": "画像",
                    "analysis_summary": "テスト判定",
                    "adult_content": True,
                    "adult_reason": "成人向け素材を扱うテストページ",
                    "image_decisions": [{
                        "image_id": "media-1", "verdict": "article", "role": "article_main",
                        "recommended_use": "thumbnail_and_body", "content_group": "main",
                        "relation": "本編", "relevance_score": 100, "reason": "本編画像",
                    }],
                    "video_decisions": [],
                    "page_role": "article", "follow_url": "",
                    "follow_reason": "",
                }
                return {"analysis": analysis, "article": {"title": "完成稿"}}

        self_outer = self
        runner = FakeRunner()
        with patch(
            "indanya_desktop.workers.capture_rendered_source",
            side_effect=lambda url, _progress: dict(sources[url]),
        ), patch(
            "indanya_desktop.workers.analyze_source_url",
            side_effect=lambda url: dict(sources[url]),
        ):
            result = _capture_and_analyze_source(ROOT, first_url, runner)
        self.assertEqual(1, runner.calls)
        self.assertEqual([first_url, relay_url, final_url], result["source_chain"])

    def test_gateway_preview_and_relay_ogp_do_not_become_article_media(self) -> None:
        first_url = "http://hnalady.example/blog-entry-31209.html"
        relay_url = (
            "https://relay.example/archives/60013924.html?"
            "url=%2F105124%2Fsevihcra%2Fmoc.iatimore%2F%2F%3Asptth"
        )
        final_url = "https://eromitai.com/archives/421501/"
        target_title = "水着を脱いでヌードになろうとする、半裸の美女たち"
        sources = {
            first_url: {
                "source_type": "web", "url": first_url,
                "title": "残暑に負けないっ…水着とヌード画像",
                "description": "", "body_text": "カテゴリー：Link",
                "text_blocks": [
                    "残暑に負けないっ…水着とヌード画像", "Link",
                    target_title, "▼ オススメ画像記事",
                ],
                "site_name": "入口", "author": "", "videos": [],
                "images": [
                    {"id": "preview", "data": b"preview", "url": "https://cdn.example/preview.jpg"},
                    {"id": "back-to-top", "data": b"icon", "url": "https://cdn.example/top.png"},
                ],
                "links": [
                    {
                        "url": relay_url, "text": target_title,
                        "contains_image": False, "font_size": "18px",
                        "browser_rect": {"width": 580, "y": 847},
                        "browser_ancestors": "a > p.entry_more2 > div#more > div.content",
                        "browser_context": f"{target_title} / ▼ オススメ画像記事",
                    },
                    {
                        "url": "https://unrelated.example/archives/1", "text": "別のおすすめ記事",
                        "contains_image": True, "font_size": "16px",
                        "browser_rect": {"width": 680, "y": 979},
                        "browser_ancestors": "a > div.recom1 > div#more > div.content",
                        "browser_context": "▼ オススメ画像記事",
                    },
                ],
            },
            relay_url: {
                "source_type": "web", "url": relay_url,
                "title": "先頭にある別の動画記事", "description": "リンク集",
                "body_text": f"先頭の別記事 {target_title}", "text_blocks": [],
                "site_name": "中継", "author": "", "videos": [],
                "images": [{
                    "id": "relay-ogp", "data": b"ogp",
                    "url": "https://relay.example/ogp.jpg",
                }],
                "links": [
                    {
                        "url": "https://unrelated.example/archives/2",
                        "text": "先頭の別記事",
                        "browser_ancestors": "a > div.pickuplink > div.widget",
                    },
                    {
                        "url": final_url, "text": target_title,
                        "browser_ancestors": "a > div.pickuplink.is_adult > div.widget",
                    },
                ],
            },
            final_url: {
                "source_type": "web", "url": final_url,
                "title": target_title, "description": "本編ギャラリー",
                "body_text": "水着を半分脱いだ写真を集めた本編です。",
                "text_blocks": ["★画像25枚★"], "site_name": "本編", "author": "",
                "images": [{
                    "id": f"media-{index}", "url": f"https://eromitai.com/{index}.jpg",
                    "data": bytes([index]) * 32, "extension": ".jpg", "mime_type": "image/jpeg",
                    "width": 800, "height": 1200,
                } for index in range(1, 26)],
                "videos": [], "links": [],
            },
        }

        class FakeRunner:
            calls = 0

            def compose(self, source: dict[str, object], _options: dict[str, object]) -> dict[str, object]:
                self.calls += 1
                self_outer.assertEqual(final_url, source["url"])
                self_outer.assertEqual(25, len(source["images"]))
                analysis = {
                    "title": target_title, "description": "本編ギャラリー", "category": "画像",
                    "analysis_summary": "最終ページの25枚を本編と判定",
                    "adult_content": True, "adult_reason": "成人向け画像ギャラリー",
                    "image_decisions": [
                        {
                            "image_id": f"media-{index}", "verdict": "article",
                            "role": "article_main", "recommended_use": "thumbnail_and_body",
                            "content_group": "main", "relation": "連続画像",
                            "relevance_score": 100, "reason": "本編画像",
                        }
                        for index in range(1, 26)
                    ],
                    "video_decisions": [], "page_role": "article", "follow_url": "",
                    "follow_reason": "",
                }
                return {"analysis": analysis, "article": {"title": "完成稿"}}

        self_outer = self
        runner = FakeRunner()
        with patch(
            "indanya_desktop.workers.capture_rendered_source",
            side_effect=lambda url, _progress: dict(sources[url]),
        ), patch(
            "indanya_desktop.workers.analyze_source_url",
            side_effect=lambda url: dict(sources[url]),
        ):
            result = _capture_and_analyze_source(ROOT, first_url, runner)

        self.assertEqual(1, runner.calls)
        self.assertEqual([first_url, relay_url, final_url], result["source_chain"])
        self.assertEqual(2, len(result["navigation_trace"]))
        self.assertEqual("gateway_chain", result["capture_strategy"])
        self.assertEqual(
            [f"media-{index}" for index in range(1, 26)],
            result["recommended_image_ids"],
        )

    def test_roundup_gallery_follows_matching_source_before_using_local_images(self) -> None:
        roundup_url = "https://roundup.example/2026/08/31/kazame-kotori/"
        article_url = "https://publisher.example/archives/1172996"
        title = "【画像】グラドル風愛ことりさん(29)が振り返ると凄い"
        roundup = {
            "source_type": "web", "url": roundup_url, "title": title,
            "description": "画像8枚をまとめました", "site_name": "入口", "author": "",
            "body_text": f"{title} / 1: 名無しさん / {article_url}",
            "text_blocks": [title, "1: 名無しさん"], "videos": [],
            "images": [
                {
                    "id": f"media-{index}",
                    "url": f"https://roundup.example/uploads/matome_img_{index:03}.jpg",
                    "data": bytes([index]) * 32, "extension": ".jpg",
                    "mime_type": "image/jpeg", "width": 640, "height": 480,
                    "browser_rect": {"y": 1500 + index * 300},
                    "browser_ancestors": "img.gallery-img > div.entry-content > article.post",
                    "browser_context": f"{title}のおすすめ画像",
                }
                for index in range(1, 9)
            ],
            "links": [
                {
                    "url": article_url, "text": article_url,
                    "contains_image": False, "font_size": "15px",
                    "browser_rect": {"width": 280, "y": 1400},
                    "browser_ancestors": "a > p > div.entry-content > article.post",
                    "browser_context": f"{title} / 1: 名無しさん / {article_url}",
                },
                {
                    "url": "https://roundup.example/2026/08/31/another/",
                    "text": "別の人気グラドル記事", "contains_image": True,
                    "browser_rect": {"width": 200, "y": 2600},
                    "browser_ancestors": "a > div.related-posts > article.post",
                    "browser_context": "人気の記事をチェック",
                },
                {
                    "url": article_url, "text": article_url,
                    "contains_image": False, "font_size": "13px",
                    "browser_rect": {"width": 240, "y": 5200},
                    "browser_ancestors": "a > div > div.entry-content > article.post",
                    "browser_context": f"出典 / {title} / {article_url}",
                },
            ],
        }
        publisher_browser = {
            "source_type": "web", "url": article_url,
            "title": "グラドル風愛ことりさん(29)が振り返ると凄い - 本編",
            "description": "風愛ことり本人の投稿画像", "site_name": "本編", "author": "",
            "body_text": "風愛ことり 1997年3月23日生まれ",
            "text_blocks": ["風愛ことり 1997年3月23日生まれ"],
            "images": [], "videos": [],
            "links": [{
                "url": "https://x.com/kazame_kotori/status/1",
                "text": "https://x.com/kazame_kotori/status/1",
                "browser_ancestors": "a > p > div.entry-content > article.post",
                "browser_context": "風愛ことり本人の公開投稿",
            }],
        }
        publisher_semantic = {
            **publisher_browser,
            "images": [
                {
                    "id": f"media-{index}",
                    "url": f"https://pbs.twimg.com/media/kotori-{index}.jpg",
                    "data": bytes([index + 20]) * 32, "extension": ".jpg",
                    "mime_type": "image/jpeg", "width": 900, "height": 1200,
                    "inside_article": True,
                }
                for index in range(1, 13)
            ],
        }

        class FakeRunner:
            calls = 0

            def compose(self, source: dict[str, object], _options: dict[str, object]) -> dict[str, object]:
                self.calls += 1
                self_outer.assertEqual(article_url, source["url"])
                self_outer.assertEqual(12, len(source["images"]))
                self_outer.assertTrue(all(
                    "pbs.twimg.com/media/" in str(item["url"])
                    for item in source["images"]
                ))
                analysis = {
                    "title": title, "description": "本人の公開投稿画像", "category": "画像",
                    "analysis_summary": "リンク先と本人アカウントを照合",
                    "adult_content": True, "adult_reason": "成人向けグラビア",
                    "image_decisions": [
                        {
                            "image_id": f"media-{index}", "verdict": "article",
                            "role": "article_main", "recommended_use": "thumbnail_and_body",
                            "content_group": "main", "relation": "本人の投稿画像",
                            "relevance_score": 100, "reason": "本編画像",
                        }
                        for index in range(1, 13)
                    ],
                    "video_decisions": [], "page_role": "article", "follow_url": "",
                    "follow_reason": "",
                }
                return {"analysis": analysis, "article": {"title": title}}

        self_outer = self
        runner = FakeRunner()

        def browser_capture(url: str, _progress: object) -> dict[str, object]:
            return dict(roundup if url == roundup_url else publisher_browser)

        def semantic_capture(url: str) -> dict[str, object]:
            return dict(roundup if url == roundup_url else publisher_semantic)

        with patch(
            "indanya_desktop.workers.capture_rendered_source",
            side_effect=browser_capture,
        ), patch(
            "indanya_desktop.workers.analyze_source_url",
            side_effect=semantic_capture,
        ):
            result = _capture_and_analyze_source(ROOT, roundup_url, runner)

        self.assertEqual(1, runner.calls)
        self.assertEqual([roundup_url, article_url], result["source_chain"])
        self.assertEqual(article_url, result["navigation_trace"][0]["followed_url"])
        self.assertEqual(
            [f"media-{index}" for index in range(1, 13)],
            result["recommended_image_ids"],
        )

    def test_x_account_intent_reaches_codex_without_private_sales_note(self) -> None:
        profile_url = "https://x.com/Test_User"
        browser_source = {
            "source_type": "web",
            "url": profile_url,
            "title": "Test User (@Test_User) / X",
            "description": "",
            "site_name": "X",
            "author": "",
            "images": [{
                "id": "media-1", "url": "https://pbs.twimg.com/media/test.jpg",
                "data": b"image", "extension": ".jpg", "mime_type": "image/jpeg",
                "width": 800, "height": 600,
            }],
            "videos": [],
            "links": [],
            "browser_capture": True,
            "x_authenticated": True,
        }
        semantic_source = {
            **browser_source,
            "source_type": "x_profile",
            "description": "公開プロフィール",
            "x_info": {"username": "Test_User"},
            "x_embed": {"author_name": "Test User", "text": "公開投稿"},
        }

        class FakeRunner:
            def compose(self, source: dict[str, object], _options: dict[str, object]) -> dict[str, object]:
                intent = source["editorial_intent"]
                self_outer.assertEqual("x_account", intent["content_mode"])
                self_outer.assertNotIn("private_note", intent)
                analysis = {
                    "title": str(source["title"]),
                    "description": str(source["description"]),
                    "category": "SNS",
                    "analysis_summary": "Xアカウント",
                    "adult_content": True,
                    "adult_reason": "成人向けSNSを扱うテストページ",
                    "image_decisions": [{
                        "image_id": "media-1", "verdict": "article", "role": "article_main",
                        "recommended_use": "thumbnail_and_body", "content_group": "main",
                        "relation": "X投稿", "relevance_score": 100, "reason": "投稿画像",
                    }],
                    "video_decisions": [],
                    "page_role": "article",
                    "follow_url": "",
                    "follow_reason": "",
                }
                return {"analysis": analysis, "article": {"title": "完成稿"}}

        self_outer = self
        with (
            patch("indanya_desktop.workers.capture_rendered_source", return_value=browser_source),
            patch("indanya_desktop.workers.analyze_source_url", return_value=semantic_source),
        ):
            result = _capture_and_analyze_source(
                ROOT,
                profile_url,
                FakeRunner(),
                editorial_intent={
                    "content_mode": "auto",
                    "promotion_type": "organic",
                    "editorial_brief": "衣装を中心に",
                    "private_note": "料金と連絡先",
                },
            )
        self.assertEqual("x_profile", result["source_type"])

    def test_x_profile_without_login_stops_before_creating_incomplete_article(self) -> None:
        profile_url = "https://x.com/Test_User"
        browser_source = {
            "source_type": "web",
            "url": profile_url,
            "title": "Test User (@Test_User) / X",
            "description": "",
            "site_name": "X",
            "author": "",
            "images": [],
            "videos": [],
            "links": [{"url": f"{profile_url}/status/1", "text": "投稿"}],
            "browser_capture": True,
            "x_authenticated": False,
            "x_timeline_media_count": 0,
        }
        semantic_source = {
            **browser_source,
            "source_type": "x_profile",
            "x_info": {"username": "Test_User"},
            "x_embed": {"author_name": "Test User", "text": "公開投稿"},
        }

        with (
            patch("indanya_desktop.workers.capture_rendered_source", return_value=browser_source),
            patch("indanya_desktop.workers.analyze_source_url", return_value=semantic_source),
        ):
            with self.assertRaisesRegex(XLoginRequiredError, "Xの投稿素材"):
                _capture_and_analyze_source(ROOT, profile_url, object())

    def test_manual_x_generation_logs_in_once_and_retries_automatically(self) -> None:
        completed = {"source_type": "x_profile", "x_authenticated": True}
        progress: list[str] = []
        with (
            patch(
                "indanya_desktop.workers._capture_and_analyze_source",
                side_effect=[RuntimeError("unexpected")],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                _capture_for_manual_generation(
                    ROOT,
                    "https://x.com/Test_User",
                    object(),
                    lambda _value, message: progress.append(message),
                )

        with (
            patch(
                "indanya_desktop.workers._capture_and_analyze_source",
                side_effect=[
                    XLoginRequiredError("login"),
                    completed,
                ],
            ) as capture,
            patch("indanya_desktop.workers.open_x_login_session") as login,
        ):
            result = _capture_for_manual_generation(
                ROOT,
                "https://x.com/Test_User",
                object(),
                lambda _value, message: progress.append(message),
            )

        self.assertIs(completed, result)
        self.assertEqual(2, capture.call_count)
        login.assert_called_once()

    def test_sponsored_metadata_is_disclosed_but_keeps_sales_note_private(self) -> None:
        payload = {"tags": ["SNS"], "blocks": []}
        source = {"source_type": "x_profile", "x_info": {"username": "Test_User"}}
        _apply_editorial_metadata(
            payload,
            source,
            {
                "content_mode": "x_account",
                "promotion_type": "sponsored",
                "editorial_brief": "写真の雰囲気",
                "private_note": "依頼者の連絡先",
            },
        )

        self.assertEqual("x_account", payload["content_mode"])
        self.assertEqual("@Test_UserのX", payload["source_label"])
        self.assertEqual("依頼者の連絡先", payload["private_client_note"])
        self.assertNotIn("PR", payload["tags"])
        self.assertEqual("sponsored-disclosure", payload["blocks"][0]["id"])

    def test_browser_video_candidates_prioritize_media_before_iframes(self) -> None:
        items = [
            {"kind": "iframe", "urls": ["https://ads.example/player"]},
            {"kind": "direct", "urls": ["https://cdn.example/movie.mp4"]},
            {"kind": "network", "urls": ["https://cdn.example/stream?id=1"]},
        ]
        ordered = sorted(items, key=_video_priority)
        self.assertEqual(["direct", "network", "iframe"], [item["kind"] for item in ordered])

    def test_x_video_variants_prefer_highest_quality_and_share_an_asset_key(self) -> None:
        low = "https://video.twimg.com/amplify_video/123/vid/avc1/320x568/low.mp4"
        high = "https://video.twimg.com/amplify_video/123/vid/avc1/720x1280/high.mp4"
        ordered = sorted(
            [{"kind": "network", "urls": [low]}, {"kind": "network", "urls": [high]}],
            key=_video_priority,
        )
        self.assertEqual(high, ordered[0]["urls"][0])
        self.assertEqual("123", _x_video_asset_key(low))
        self.assertEqual(_x_video_asset_key(low), _x_video_asset_key(high))

    def test_html_page_url_is_not_accepted_as_a_direct_video(self) -> None:
        page_url = "https://example.com/article/"
        self.assertFalse(_plausible_video_candidate(page_url, "direct", "", page_url))
        self.assertFalse(_plausible_video_candidate(
            "https://example.com/player",
            "direct",
            "text/html",
            page_url,
        ))
        self.assertTrue(_plausible_video_candidate(
            "https://media.example.com/movie.mp4",
            "direct",
            "",
            page_url,
        ))

    def test_comment_and_like_iframes_are_not_treated_as_videos(self) -> None:
        page_url = "https://example.com/article/"
        self.assertFalse(_plausible_video_candidate(
            "https://comment.blogcms.jp/livedoor/example/123/like_frame",
            "iframe",
            "text/html",
            page_url,
        ))
        self.assertFalse(_plausible_video_candidate(
            "https://example.com/widgets/comment_frame?id=123",
            "iframe",
            "text/html",
            page_url,
        ))

    def test_merged_source_video_filter_removes_widgets_but_keeps_movies(self) -> None:
        source = {
            "url": "https://example.com/article/",
            "videos": [
                {
                    "kind": "iframe",
                    "url": "https://comment.blogcms.jp/site/123/like_frame",
                    "mime_type": "text/html",
                },
                {
                    "kind": "direct",
                    "url": "https://media.example.com/movie.mp4",
                    "mime_type": "video/mp4",
                },
            ],
        }

        filtered = _filter_source_videos(source)

        self.assertEqual(
            ["https://media.example.com/movie.mp4"],
            [item["url"] for item in filtered["videos"]],
        )

    def test_x_dash_manifest_is_one_video_and_fragments_are_rejected(self) -> None:
        page_url = "https://x.com/Test_User/status/1"
        self.assertTrue(_plausible_video_candidate(
            "https://video.twimg.com/amplify_video/1/pl/abc.mpd?tag=14",
            "direct",
            "application/dash+xml",
            page_url,
        ))
        for fragment in (
            "https://video.twimg.com/amplify_video/1/aud/mp4a/0/0/init.mp4",
            "https://video.twimg.com/amplify_video/1/aud/mp4a/128000/segment.m4s",
            "https://video.twimg.com/amplify_video/1/vid/avc1/720x1280/segment.m4s",
            "https://video.twimg.com/amplify_video/1/vid/avc1/0/0/init.mp4",
        ):
            self.assertFalse(_plausible_video_candidate(
                fragment,
                "direct",
                "video/mp4",
                page_url,
            ))

    def test_x_scroll_snapshots_keep_media_removed_from_later_dom(self) -> None:
        collected: dict[str, object] = {}
        _merge_snapshot(
            collected,
            {
                "images": [{"url": "https://pbs.twimg.com/media/first.jpg"}],
                "videos": [{"urls": ["https://video.twimg.com/first.mp4"]}],
                "links": [{"url": "https://x.com/Test_User/status/1", "text": "投稿1"}],
                "text_blocks": ["最初の投稿"],
            },
        )
        _merge_snapshot(
            collected,
            {
                "images": [{"url": "https://pbs.twimg.com/media/second.jpg"}],
                "videos": [],
                "links": [{"url": "https://x.com/Test_User/status/2", "text": "投稿2"}],
                "text_blocks": ["次の投稿"],
            },
        )

        self.assertEqual(2, len(collected["images"]))
        self.assertEqual(1, len(collected["videos"]))
        self.assertEqual(["最初の投稿", "次の投稿"], collected["text_blocks"])

    def test_x_graphql_media_urls_are_collected(self) -> None:
        images: set[str] = set()
        videos: set[str] = set()
        _find_x_media_urls(
            {
                "media": [
                    {"url": "https://pbs.twimg.com/media/photo.jpg?format=jpg&name=large"},
                    {"variants": [{"url": "https://video.twimg.com/ext_tw_video/clip/vid/720x1280/movie.mp4?tag=12"}]},
                ],
            },
            images,
            videos,
        )
        self.assertEqual(1, len(images))
        self.assertEqual(1, len(videos))

    def test_browser_error_page_keeps_requested_url(self) -> None:
        fallback = "https://example.com/story"
        self.assertEqual(fallback, _usable_final_url("chrome-error://chromewebdata/", fallback))

    def test_video_thumbnail_uses_canvas_pixels_instead_of_dom_screenshot(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (64, 96), "#663344").save(buffer, format="JPEG")

        class FakeVideo:
            def evaluate(self, _script: str) -> str:
                return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

            def screenshot(self, **_kwargs: object) -> bytes:
                raise AssertionError("DOM screenshot must not be used for video thumbnails")

        captured = _video_canvas_frame(FakeVideo())
        with Image.open(BytesIO(captured)) as image:
            self.assertEqual((64, 96), image.size)

    def test_default_site_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_data = Path(temporary) / "appdata"
            with patch.dict(os.environ, {"APPDATA": str(app_data)}):
                registry = SiteRegistry(ROOT)
                self.assertEqual("淫談屋", registry.active.name)
                self.assertEqual(ROOT.resolve(), registry.active.root)
                reloaded = SiteRegistry(ROOT)
                self.assertEqual(registry.active_id, reloaded.active_id)

    def test_add_switch_and_remove_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            app_data = temporary_root / "appdata"
            second_root = temporary_root / "second"
            second_root.mkdir()
            with patch.dict(os.environ, {"APPDATA": str(app_data)}):
                registry = SiteRegistry(ROOT)
                second = registry.upsert({
                    "name": "2つ目のサイト",
                    "public_url": "https://example.com/",
                    "local_path": str(second_root),
                    "repository_url": "https://github.com/example/site",
                    "provider": "GitHub Pages",
                })
                self.assertEqual(second.site_id, registry.active_id)
                self.assertEqual(2, len(registry.sites))
                registry.remove(second.site_id)
                self.assertEqual(1, len(registry.sites))
                saved = json.loads(registry.path.read_text(encoding="utf-8"))
                self.assertEqual("indanya", saved["active_id"])


if __name__ == "__main__":
    unittest.main()
