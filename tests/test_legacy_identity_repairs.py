from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.legacy_identity_repairs import (  # noqa: E402
    backfill_verified_main_subject_identity,
)


def _payload() -> dict:
    return {
        "title": "一ノ瀬瑠菜の黄色ビキニグラビア",
        "summary": "一ノ瀬瑠菜を紹介する記事",
        "main_subject": {
            "kind": "person",
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "is_public_creator": False,
        },
        "identity_resolution": {"status": "verified"},
        "verified_social_profiles": [{
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
            "service": "x",
            "url": "https://x.com/ichinose_luna",
            "confidence": 96,
        }],
        "images": [
            {
                "id": "body-image",
                "source_id": "media-1",
                "alt": "一ノ瀬瑠菜の黄色ビキニ",
            },
            {
                "id": "unrelated-image",
                "source_id": "media-2",
                "alt": "別記事のおすすめ画像",
            },
            {
                "id": "profile-image",
                "alt": "一ノ瀬瑠菜のXプロフィール画像",
                "related_thumbnail_only": True,
            },
        ],
        "videos": [],
        "blocks": [
            {"id": "body", "type": "images", "image_ids": ["body-image", "unrelated-image"]},
            {
                "id": "profile",
                "type": "related_link",
                "thumbnail_image_id": "profile-image",
            },
        ],
    }


def test_backfill_labels_only_matching_body_media_with_plain_verified_identity() -> None:
    payload = _payload()

    assert backfill_verified_main_subject_identity(payload)

    assert payload["main_subject"]["is_public_creator"] is True
    assert payload["identified_people"][0]["name"] == "一ノ瀬瑠菜"
    attribution = payload["media_person_attributions"][0]
    assert attribution["image_ids"] == ["body-image"]
    assert payload["images"][0]["identified_people"] == ["一ノ瀬瑠菜"]
    assert "identified_people" not in payload["images"][1]
    assert "identified_people" not in payload["images"][2]


def test_backfill_refuses_unverified_profile() -> None:
    payload = _payload()
    payload["verified_social_profiles"][0]["confidence"] = 94

    assert not backfill_verified_main_subject_identity(payload)
    assert "identified_people" not in payload


def test_backfill_combines_trusted_resolution_with_lower_profile_score() -> None:
    payload = _payload()
    payload["verified_social_profiles"][0]["confidence"] = 88
    payload["identity_resolution"] = {
        "status": "verified",
        "method": "verified_registry",
    }

    assert backfill_verified_main_subject_identity(payload)

    attribution = payload["media_person_attributions"][0]
    assert attribution["confidence"] == 95
    assert attribution["evidence_types"] == ["headline", "alt", "official_page"]


def test_backfill_direct_fanza_product_uses_product_credit_for_all_body_media() -> None:
    payload = _payload()
    payload["source_url"] = "https://video.dmm.co.jp/av/content/?id=test00001"
    payload["verified_social_profiles"] = []
    payload["identity_resolution"] = {
        "status": "not_found",
        "method": "codex_web_search",
    }
    payload["images"][0]["alt"] = "パッケージ画像"
    payload["images"][1]["alt"] = "公式商品紹介画像 1"

    assert backfill_verified_main_subject_identity(payload)

    attribution = payload["media_person_attributions"][0]
    assert attribution["confidence"] == 99
    assert attribution["image_ids"] == ["body-image", "unrelated-image"]
    assert attribution["evidence_types"] == [
        "headline",
        "official_page",
        "product_credit",
    ]


def test_backfill_refuses_ambiguous_secondary_source_without_profile() -> None:
    payload = _payload()
    payload["verified_social_profiles"] = []
    payload["identity_resolution"] = {
        "status": "ambiguous",
        "method": "codex_web_search",
    }

    assert not backfill_verified_main_subject_identity(payload)
    assert "identified_people" not in payload


def test_backfill_accepts_matching_exact_product_credit_on_secondary_source() -> None:
    payload = _payload()
    payload["source_url"] = "https://example.com/article"
    payload["verified_social_profiles"] = []
    payload["identity_resolution"] = {
        "status": "ambiguous",
        "method": "codex_web_search",
    }
    payload["related_destinations"] = [{
        "link_kind": "exact_video",
        "title": "一ノ瀬瑠菜 はじめての水着作品",
        "url": "https://video.dmm.co.jp/av/content/?id=test00001",
        "match_confidence": 98,
    }]

    assert backfill_verified_main_subject_identity(payload)
    assert payload["media_person_attributions"][0]["confidence"] == 98


def test_backfill_refuses_mismatched_exact_product_credit() -> None:
    payload = _payload()
    payload["verified_social_profiles"] = []
    payload["identity_resolution"] = {
        "status": "ambiguous",
        "method": "codex_web_search",
    }
    payload["related_destinations"] = [{
        "link_kind": "exact_video",
        "title": "別人の水着作品",
        "url": "https://video.dmm.co.jp/av/content/?id=test00001",
        "match_confidence": 100,
    }]

    assert not backfill_verified_main_subject_identity(payload)


def test_backfill_accepts_one_coherent_group_owned_by_verified_profile_handle() -> None:
    payload = _payload()
    payload["identity_resolution"] = {"status": "ambiguous"}
    payload["images"][0]["alt"] = "記事の主画像"
    payload["images"][0]["ai_content_group"] = "ichinose-luna-bikini"
    payload["images"][1]["ai_content_group"] = "ichinose-luna-bikini"

    assert backfill_verified_main_subject_identity(payload)

    attribution = payload["media_person_attributions"][0]
    assert attribution["image_ids"] == ["body-image", "unrelated-image"]
    assert attribution["confidence"] >= 95


def test_backfill_accepts_exact_product_card_credit() -> None:
    payload = _payload()
    payload["verified_social_profiles"] = []
    payload["identity_resolution"] = {"status": "ambiguous"}
    payload["blocks"].append({
        "type": "product_cta",
        "url": "https://video.dmm.co.jp/av/content/?id=test00001",
        "title": "一ノ瀬瑠菜 はじめての作品",
        "match_type": "exact_image",
        "match_confidence": 98,
    })

    assert backfill_verified_main_subject_identity(payload)
    assert payload["media_person_attributions"][0]["confidence"] == 98
