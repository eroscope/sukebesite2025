from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.adaptive_quality import (
    SHADOW_DAYS,
    apply_quality_gate,
    article_quality_report,
    candidate_eligibility,
    quality_mode,
    record_editorial_feedback,
    route_template,
    sync_ga4_performance,
)


def _payload() -> dict:
    return {
        "slug": "quality-test",
        "source_url": "https://example.com/2026/08/31/sample-title/",
        "title": "【画像】確認できた内容を具体的に紹介するテスト記事",
        "summary": "元ページで確認できた画像と人物情報だけを使い、内容を具体的に紹介するテスト記事です。",
        "category": "画像",
        "tags": ["画像", "水着"],
        "thumbnail_id": "image-1",
        "images": [{"id": "image-1", "source_url": "https://cdn.example.com/1.jpg"}],
        "videos": [],
        "blocks": [
            {"id": "image", "type": "images", "image_ids": ["image-1"]},
            {"id": "post", "type": "post", "text": "画像と本文が合ってる"},
        ],
    }


class AdaptiveQualityTests(unittest.TestCase):
    def test_date_slug_routes_are_reused_across_articles(self) -> None:
        first = route_template("https://himablo.xyz/2026/08/31/first-title/")
        second = route_template("https://himablo.xyz/2026/09/01/second-title/")
        self.assertEqual("/{date}/{slug}", first)
        self.assertEqual(first, second)

    def test_shadow_mode_records_review_recommendation_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 9, 1, 12, 0)
            payload = _payload()
            payload["blocks"].append({
                "id": "profile",
                "type": "related_link",
                "url": "https://x.com/person",
                "provider": "x",
                "link_kind": "official_profile",
            })
            report = apply_quality_gate(root, payload, now=now)
            self.assertEqual("shadow", report["mode"])
            self.assertEqual("review", report["recommendation"])
            self.assertEqual("auto_ready", report["effective_decision"])
            self.assertIn("profile_card_image_mismatch", report["blockers"])

    def test_shadow_switches_to_advisory_without_enough_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            started = datetime(2026, 9, 1, 12, 0)
            apply_quality_gate(root, _payload(), now=started)
            mode = quality_mode(root, now=started + timedelta(days=SHADOW_DAYS + 1))
            self.assertEqual("advisory", mode["mode"])

    def test_unknown_or_duplicate_media_is_a_hard_failure(self) -> None:
        payload = _payload()
        payload["blocks"][0]["image_ids"] = ["image-1", "missing", "image-1"]
        report = article_quality_report(payload)
        self.assertEqual("discard", report["recommendation"])
        self.assertIn("unknown_media_reference", report["blockers"])
        self.assertIn("duplicate_media_reference", report["blockers"])

    def test_generic_topic_search_cannot_use_the_article_person_image(self) -> None:
        payload = _payload()
        payload["blocks"].append({
            "id": "generic",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/search/?searchstr=%E6%B0%B4%E7%9D%80",
            "link_kind": "inferred_topic_search",
            "thumbnail_image_id": "image-1",
        })

        report = article_quality_report(payload)

        self.assertIn("topic_search_uses_article_image", report["blockers"])

    def test_real_related_product_requires_the_same_product_package(self) -> None:
        payload = _payload()
        product_url = "https://video.dmm.co.jp/av/content/?id=swim001"
        payload["blocks"].append({
            "id": "related-product",
            "type": "related_link",
            "url": product_url,
            "link_kind": "inferred_topic_product",
            "thumbnail_url": "https://pics.dmm.co.jp/digital/video/swim001/swim001pl.jpg",
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": product_url,
        })

        report = article_quality_report(payload)
        self.assertNotIn("related_product_card_mismatch", report["blockers"])

        payload["blocks"][-1]["thumbnail_owner_url"] = (
            "https://video.dmm.co.jp/av/content/?id=another001"
        )
        mismatch = article_quality_report(payload)
        self.assertIn("related_product_card_mismatch", mismatch["blockers"])

    def test_named_public_person_needs_the_verified_identity_gate(self) -> None:
        payload = _payload()
        payload["main_subject"] = {
            "kind": "person",
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": True,
        }

        missing = article_quality_report(payload)
        self.assertIn("named_person_identity_unverified", missing["blockers"])

        payload["identified_people"] = [{
            "name": "一ノ瀬瑠菜",
            "confidence": 96,
        }]
        payload["media_person_attributions"] = [{
            "person_name": "一ノ瀬瑠菜",
            "image_ids": ["image-1"],
            "video_ids": [],
            "confidence": 96,
            "evidence_types": ["headline", "alt", "official_profile"],
        }]
        payload["person_identity_gate"] = {"status": "verified"}
        verified = article_quality_report(payload)
        self.assertNotIn("named_person_identity_unverified", verified["blockers"])
        self.assertNotIn("person_identity_below_precision_gate", verified["blockers"])

    def test_named_person_with_multiple_content_groups_is_rejected(self) -> None:
        payload = _payload()
        payload["main_subject"] = {"kind": "person", "name": "南ゆい"}
        payload["images"] = [
            {"id": "image-1", "source_id": "media-1"},
            {"id": "image-2", "source_id": "media-2"},
        ]
        payload["blocks"][0]["image_ids"] = ["image-1", "image-2"]
        source = {
            "images": [
                {"id": "media-1", "ai_content_group": "minami-yui"},
                {"id": "media-2", "ai_content_group": "another-person"},
            ]
        }

        report = article_quality_report(payload, source)

        self.assertEqual("discard", report["recommendation"])
        self.assertIn("cross_subject_media", report["blockers"])

    def test_private_subject_does_not_require_an_official_destination(self) -> None:
        payload = _payload()
        payload["main_subject"] = {
            "kind": "person",
            "name": "投稿者の奥さん",
            "role": "自称44歳の奥さん",
            "is_public_creator": False,
        }

        report = article_quality_report(payload)

        self.assertNotIn("missing_person_destination", report["warnings"])

    def test_public_person_search_satisfies_the_destination_check(self) -> None:
        payload = _payload()
        payload["main_subject"] = {
            "kind": "person",
            "name": "公開活動者",
            "role": "グラビアアイドル",
            "is_public_creator": True,
        }
        payload["blocks"].append({
            "id": "person-search",
            "type": "related_link",
            "url": "https://www.google.com/search?q=public",
            "link_kind": "person_search",
        })

        report = article_quality_report(payload)

        self.assertNotIn("missing_person_destination", report["warnings"])

    def test_saved_content_groups_are_used_after_source_capture_is_gone(self) -> None:
        payload = _payload()
        payload["main_subject"] = {"kind": "person", "name": "南ゆい"}
        payload["images"] = [
            {
                "id": "image-1",
                "source_id": "media-1",
                "ai_content_group": "x-account:crybaby0430",
            },
            {
                "id": "image-2",
                "source_id": "media-2",
                "ai_content_group": "x-account:crybaby0430",
            },
        ]
        payload["blocks"][0]["image_ids"] = ["image-1", "image-2"]

        report = article_quality_report(payload)

        self.assertNotIn("cross_subject_media", report["blockers"])
        self.assertNotIn("unverified_subject_media", report["warnings"])
        self.assertIn("保存済みの素材所有者グループが全画像で一致", report["evidence"])

        payload["images"][1]["ai_content_group"] = "x-account:someone-else"
        mixed = article_quality_report(payload)
        self.assertIn("cross_subject_media", mixed["blockers"])

    def test_verified_embedded_product_must_have_an_exact_cta(self) -> None:
        payload = _payload()
        source = {
            "verified_embedded_fanza_product_urls": [
                "https://video.dmm.co.jp/av/content/?id=mida00763"
            ]
        }

        missing = article_quality_report(payload, source)

        self.assertEqual("discard", missing["recommendation"])
        self.assertIn("missing_embedded_exact_product_cta", missing["blockers"])

        payload["blocks"].append({
            "id": "exact-product",
            "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=mida00763",
            "match_type": "exact_image",
            "thumbnail_url": "https://pics.dmm.co.jp/digital/video/mida00763/mida00763pl.jpg",
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": "https://video.dmm.co.jp/av/content/?id=mida00763",
        })
        complete = article_quality_report(payload, source)

        self.assertNotIn("missing_embedded_exact_product_cta", complete["blockers"])
        self.assertIn("本文ギャラリー直後の確定作品PRを照合", complete["evidence"])

    def test_named_work_requires_verified_official_card_with_thumbnail(self) -> None:
        payload = _payload()
        source = {
            "official_work_required": True,
            "verified_work_destinations": [{
                "url": "https://publisher.example.com/comics/exact-work/",
                "title": "確定作品",
                "provider": "出版社",
                "reason": "公式ページで作品名が一致",
            }],
        }

        missing = article_quality_report(payload, source)
        self.assertEqual("discard", missing["recommendation"])
        self.assertIn("missing_verified_official_work", missing["blockers"])

        payload["blocks"].append({
            "id": "official-work",
            "type": "related_link",
            "url": "https://publisher.example.com/comics/exact-work/",
            "link_kind": "exact_official_work",
            "thumbnail_image_id": "official-work-image",
            "thumbnail_source_kind": "official_page",
            "thumbnail_owner_url": "https://publisher.example.com/comics/exact-work/",
        })
        payload["images"].append({
            "id": "official-work-image",
            "related_thumbnail_only": True,
            "rights_basis": "official_page_thumbnail",
            "thumbnail_owner_url": "https://publisher.example.com/comics/exact-work/",
        })
        complete = article_quality_report(payload, source)
        self.assertNotIn("missing_verified_official_work", complete["blockers"])
        self.assertNotIn("official_work_card_missing_thumbnail", complete["blockers"])
        self.assertIn("作品名と一致する公式・正規販売ページを照合", complete["evidence"])

        payload["official_work_required"] = True
        payload["verified_work_destinations"] = source["verified_work_destinations"]
        reloaded = article_quality_report(payload)
        self.assertNotIn("missing_verified_official_work", reloaded["blockers"])
        self.assertIn("作品名と一致する公式・正規販売ページを照合", reloaded["evidence"])

    def test_editor_feedback_records_before_after_and_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = _payload()
            after = _payload()
            after["source_url"] = "https://example.com/2026/09/01/real-article/"
            event = record_editorial_feedback(root, before, after, "wrong_source")
            self.assertEqual("wrong_source", event["failure_code"])
            self.assertIn("source_url", event["changed_fields"])
            state = json.loads((root / ".article-studio" / "adaptive-quality.json").read_text(encoding="utf-8"))
            self.assertTrue(state["routes"])
            self.assertNotEqual(state["feedback"][0]["before_hash"], state["feedback"][0]["after_hash"])

    def test_candidate_score_uses_stable_source_and_real_performance_only(self) -> None:
        low = candidate_eligibility({"title": "短い", "buzz_score": 10})
        high = candidate_eligibility(
            {"title": "掲載内容が確認できる具体的な候補タイトル", "buzz_score": 80, "structural_score": 20},
            site_plan={"site_successes": 9, "site_failures": 1},
            source_performance={"page_views": 50, "pr_ctr": 8.0},
        )
        self.assertGreater(high["score"], low["score"])
        self.assertTrue(high["eligible"])

    def test_ga4_performance_is_grouped_by_real_draft_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drafts = root / ".article-studio" / "drafts"
            drafts.mkdir(parents=True)
            (drafts / "quality-test.json").write_text(
                json.dumps({"source_url": "https://example.com/article"}), encoding="utf-8"
            )
            report = {
                "external": {
                    "articles": [{
                        "pagePath": "/sukebesite2025/articles/quality-test.html",
                        "eventCount": 30,
                        "activeUsers": 12,
                        "prImpressions": 20,
                        "prClicks": 4,
                    }]
                }
            }
            result = sync_ga4_performance(root, report)
            self.assertEqual(20.0, result["articles"]["quality-test"]["pr_ctr"])
            self.assertTrue(result["sources"]["example.com"]["eligible_for_weighting"])
