from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.editorial_policy import (  # noqa: E402
    _fanza_product_image_urls,
    FANZA_MEDIA_PROFILE,
    POLICY_VERSION,
    approve_generated_article,
    assess_analyzed_source,
    assess_saved_article,
    canonical_fanza_product_url,
    check_originality,
    fanza_image_product_id,
    fanza_product_id,
    is_fanza_package_image,
    is_fanza_product_sample_image,
    is_fanza_product_url,
    require_publishable_article,
    restrict_source_to_fanza_product,
)


PRODUCT_URL = "https://video.dmm.co.jp/av/content/?id=abc001"
PACKAGE_URL = "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg"


def adult_source(**overrides: object) -> dict:
    source = {
        "url": PRODUCT_URL,
        "requested_url": PRODUCT_URL,
        "title": "成人向けヌード画像まとめ",
        "description": "全裸の成人女性を撮影したグラビアです。",
        "ai_analysis_summary": "本編画像は成人向けヌード作品です。",
        "ai_adult_content": True,
        "ai_adult_reason": "本編に成人女性の全裸と乳首を写した画像があります。",
        "ai_fanza_relevance": "none",
        "recommended_image_ids": ["media-1"],
        "recommended_video_ids": [],
        "media_rights_profile": FANZA_MEDIA_PROFILE,
        "images": [{
            "id": "media-1",
            "url": PACKAGE_URL,
            "rights_basis": "fanza_product_main_image",
        }],
        "body_text": "元ページ側の説明文です。",
        "text_blocks": [],
        "excerpts": [],
    }
    source.update(overrides)
    return source


def adult_payload() -> dict:
    return {
        "title": "【画像】成人向けヌード、浴室のカットが印象的",
        "summary": "成人女性のヌードグラビアを、撮影場所や構図の違いに注目して紹介します。",
        "source_url": PRODUCT_URL,
        "content_mode": "fanza_product",
        "fanza_product_id": "abc001",
        "media_rights_profile": FANZA_MEDIA_PROFILE,
        "adult_confirmed": True,
        "images": [{
            "id": "image-1",
            "source_url": PACKAGE_URL,
            "rights_basis": "fanza_product_main_image",
        }],
        "videos": [],
        "tags": ["成人向け", "ヌード"],
        "blocks": [
            {"type": "post", "text": "浴室とベッドで光の当たり方が変わるから、同じ人物でも写真の印象がかなり違って見える。"},
            {"type": "post", "text": "ただ並べるだけではなく、表情と構図が変わる順番で見るとグラビアとして流れが分かりやすい。"},
            {"type": "product_cta", "url": PRODUCT_URL, "text": "作品ページを見る"},
        ],
    }


class EditorialPolicyTests(unittest.TestCase):
    def test_rejects_general_news_even_when_ai_says_adult(self) -> None:
        source = adult_source(
            title="特殊詐欺を止めた店員に感謝状",
            description="コンビニ店員が詐欺を防いだ一般ニュースです。",
            ai_analysis_summary="一般ニュースです。",
            ai_adult_reason="成人が写っています。",
        )
        decision = assess_analyzed_source(source)
        self.assertFalse(decision.allowed)
        self.assertIn("一般記事", decision.message)

    def test_rejects_ambiguous_swimsuit_only_page(self) -> None:
        source = adult_source(
            title="海辺の水着写真",
            description="夏の海で撮影した写真です。",
            ai_analysis_summary="水着写真です。",
            ai_adult_reason="水着の女性が写っています。",
        )
        self.assertFalse(assess_analyzed_source(source).allowed)

    def test_rejects_privacy_and_minor_risk_topics(self) -> None:
        for title in ("盗撮された着替え動画", "幼少期と成人後の比較"):
            with self.subTest(title=title):
                self.assertFalse(assess_analyzed_source(adult_source(title=title)).allowed)

    def test_unrelated_footer_risk_words_do_not_reject_reviewed_adult_article(self) -> None:
        source = adult_source(
            url="https://example.com/archives/12345",
            requested_url="https://example.com/archives/12345",
            excerpts=["おすすめ記事: 未成年のニュース", "別記事: 盗撮事件"],
        )

        self.assertTrue(assess_analyzed_source(source).allowed)

    def test_rejects_long_verbatim_copy(self) -> None:
        copied = "これは元ページからそのまま複製された長い説明文です。" * 8
        source = adult_source(body_text=copied)
        payload = adult_payload()
        payload["blocks"] = [{"type": "post", "text": copied}]
        decision = check_originality(source, payload)
        self.assertFalse(decision.allowed)
        self.assertIn("長く一致", decision.message)

    def test_separate_short_segments_do_not_create_false_overlap_at_boundaries(self) -> None:
        first = "甲" * 48
        second = "乙" * 48
        independent = "丙" * 48
        source = adult_source(body_text=first, text_blocks=[second])
        payload = adult_payload()
        payload["summary"] = first
        payload["blocks"] = [
            {"type": "post", "text": second},
            {"type": "post", "text": independent},
        ]

        decision = check_originality(source, payload)

        self.assertTrue(decision.allowed, decision.message)

    def test_approved_generated_article_records_policy_audit(self) -> None:
        payload = adult_payload()
        approve_generated_article(adult_source(), payload)
        self.assertEqual(POLICY_VERSION, payload["editorial_policy_version"])
        self.assertEqual("adult_approved", payload["editorial_policy_status"])
        self.assertTrue(payload["originality_checked"])

    def test_ordinary_web_article_uses_non_fanza_publish_policy(self) -> None:
        source = adult_source(
            url="https://example.com/archives/12345",
            requested_url="https://example.com/archives/12345",
            media_rights_profile="",
            images=[{"id": "media-1", "url": "https://example.com/main.jpg"}],
        )
        payload = adult_payload()
        payload.update({
            "source_url": "https://example.com/archives/12345",
            "content_mode": "web",
            "fanza_product_id": "",
            "media_rights_profile": "",
            "images": [{"id": "image-1", "source_url": "https://example.com/main.jpg"}],
            "blocks": [
                {"type": "post", "text": "This is an independently written adult article response with enough detail to pass the originality review safely."},
                {"type": "images", "image_ids": ["image-1"]},
                {"type": "post", "text": "A second independently written reaction discusses only what can be seen in the supplied source material."},
            ],
        })
        approve_generated_article(source, payload)
        self.assertEqual("source-page-reviewed", payload["media_rights_profile"])
        require_publishable_article(payload)

    def test_publish_gate_rejects_general_saved_article(self) -> None:
        payload = adult_payload()
        payload.update({"title": "特殊詐欺を止めた店員に感謝状", "summary": "一般ニュースです。", "tags": ["ニュース"]})
        with self.assertRaisesRegex(RuntimeError, "成人向け"):
            require_publishable_article(payload)

    def test_publish_gate_rejects_legacy_article(self) -> None:
        payload = adult_payload()
        payload.pop("media_rights_profile")
        self.assertFalse(assess_saved_article(payload).allowed)
        with self.assertRaises(RuntimeError):
            require_publishable_article(payload)

    def test_product_url_is_canonicalized_for_deduplication(self) -> None:
        tracked = PRODUCT_URL + "&i3_ref=recommend&i3_ord=1"
        self.assertTrue(is_fanza_product_url(tracked))
        self.assertEqual(PRODUCT_URL, canonical_fanza_product_url(tracked))
        self.assertFalse(is_fanza_product_url("https://video.dmm.co.jp/av/list/"))

    def test_only_exact_official_product_images_survive_restriction(self) -> None:
        source = adult_source(images=[
            {"id": "package", "url": PACKAGE_URL, "alt": "パッケージ画像"},
            {"id": "sample", "url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg"},
            {"id": "external", "url": "https://example.com/banner.jpg"},
        ], videos=[{"url": "https://example.com/sample.mp4"}])
        with patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_package",
            return_value=None,
        ), patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_samples",
            return_value=[{
                "id": "sample-1",
                "url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
                "rights_source_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
                "rights_basis": "fanza_product_sample_image",
                "data": b"sample",
            }],
        ):
            restricted = restrict_source_to_fanza_product(source)
        self.assertEqual(2, len(restricted["images"]))
        self.assertTrue(is_fanza_package_image(restricted["images"][0]))
        self.assertTrue(is_fanza_product_sample_image(restricted["images"][1]))
        self.assertEqual(
            ["media-1", "media-2"],
            restricted["recommended_body_image_ids"],
        )
        self.assertEqual([], restricted["videos"])
        self.assertEqual(FANZA_MEDIA_PROFILE, restricted["media_rights_profile"])

    def test_official_fanza_sample_videos_are_selected(self) -> None:
        source = adult_source(
            images=[{"id": "package", "url": PACKAGE_URL, "alt": "package"}],
            videos=[{
                "id": "raw-video",
                "kind": "direct",
                "url": "https://cc3001.dmm.co.jp/litevideo/freepv/a/abc/abc001/abc001mhb.mp4",
                "mime_type": "video/mp4",
            }],
        )
        with patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_package",
            return_value=None,
        ), patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_samples",
            return_value=[],
        ):
            restricted = restrict_source_to_fanza_product(source)
        self.assertEqual(1, len(restricted["videos"]))
        self.assertEqual(["video-1"], restricted["recommended_video_ids"])

    def test_product_and_package_ids_are_extracted(self) -> None:
        self.assertEqual("abc001", fanza_product_id(PRODUCT_URL + "&i3_ref=recommend"))
        self.assertEqual("abc001", fanza_image_product_id(PACKAGE_URL))
        self.assertEqual(
            "abc001",
            fanza_image_product_id(
                "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-12.jpg"
            ),
        )
        doujin_package = (
            "https://doujin-assets.dmm.co.jp/digital/comic/"
            "d_773789/d_773789pr.jpg"
        )
        self.assertEqual("d_773789", fanza_image_product_id(doujin_package))
        self.assertTrue(is_fanza_package_image({"url": doujin_package}, "d_773789"))

    def test_doujin_image_urls_use_official_comic_paths_and_padded_samples(self) -> None:
        package_urls = _fanza_product_image_urls("d_432488", "pl")
        sample_urls = _fanza_product_image_urls("d_432488", "jp-1")
        self.assertEqual(
            "https://doujin-assets.dmm.co.jp/digital/comic/"
            "d_432488/d_432488pl.jpg",
            package_urls[0],
        )
        self.assertEqual(
            "https://doujin-assets.dmm.co.jp/digital/comic/"
            "d_432488/d_432488jp-001.jpg",
            sample_urls[0],
        )

    def test_restriction_rejects_another_products_package(self) -> None:
        source = adult_source(images=[{
            "id": "wrong-package",
            "url": "https://pics.dmm.co.jp/digital/video/xyz999/xyz999pl.jpg",
            "alt": "パッケージ画像",
        }])
        with patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_package",
            return_value=None,
        ), patch(
            "indanya_desktop.editorial_policy._download_exact_fanza_samples",
            return_value=[],
        ):
            with self.assertRaisesRegex(Exception, "商品ID abc001"):
                restrict_source_to_fanza_product(source)

    def test_generation_gate_accepts_exact_official_sample_image(self) -> None:
        payload = adult_payload()
        payload["images"].append({
            "id": "image-2",
            "source_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
            "rights_basis": "fanza_product_sample_image",
        })
        approve_generated_article(adult_source(), payload)
        self.assertEqual("adult_approved", payload["editorial_policy_status"])

    def test_generation_gate_rejects_another_products_package(self) -> None:
        payload = adult_payload()
        payload["images"][0]["source_url"] = (
            "https://pics.dmm.co.jp/digital/video/xyz999/xyz999pl.jpg"
        )
        with self.assertRaisesRegex(Exception, "別商品のパッケージ"):
            approve_generated_article(adult_source(), payload)

    def test_publish_gate_rejects_another_products_cta(self) -> None:
        payload = adult_payload()
        payload["blocks"][-1]["url"] = "https://video.dmm.co.jp/av/content/?id=xyz999"
        self.assertFalse(assess_saved_article(payload).allowed)


if __name__ == "__main__":
    unittest.main()
