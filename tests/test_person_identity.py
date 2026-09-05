from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.person_identity import (  # noqa: E402
    apply_verified_person_identity_to_payload,
    apply_verified_person_identity_to_source,
    person_identity_issues,
)


def _verified_profile(name: str, service: str = "x") -> dict:
    return {
        "name": name,
        "role": "グラビアモデル",
        "service": service,
        "url": f"https://x.com/{service}-{name}",
        "confidence": 96,
    }


def test_verified_single_person_is_mapped_to_only_matching_images() -> None:
    source = {
        "title": "一ノ瀬瑠菜の黄色ビキニグラビア",
        "ai_main_subject": {
            "kind": "person",
            "name": "一ノ瀬瑠菜",
            "role": "グラビアモデル",
        },
        "identity_resolution": {"status": "verified"},
        "verified_social_profiles": [_verified_profile("一ノ瀬瑠菜")],
        "images": [
            {"id": "media-1", "ai_verdict": "article", "alt": "一ノ瀬瑠菜の黄色ビキニ"},
            {"id": "media-2", "ai_verdict": "article", "alt": "サイト内の別記事バナー"},
        ],
    }

    apply_verified_person_identity_to_source(source)

    assert [item["name"] for item in source["identified_people"]] == ["一ノ瀬瑠菜"]
    assert source["media_person_attributions"][0]["image_ids"] == ["media-1"]
    assert source["images"][0]["identified_people"] == ["一ノ瀬瑠菜"]
    assert "identified_people" not in source["images"][1]

    payload = {
        "main_subject": {"kind": "person", "name": "一ノ瀬瑠菜", "is_public_creator": False},
        "images": [
            {"id": "image-1", "source_id": "media-1"},
            {"id": "image-2", "source_id": "media-2"},
        ],
        "videos": [],
    }
    apply_verified_person_identity_to_payload(payload, source)
    assert payload["main_subject"]["is_public_creator"] is True
    assert payload["person_identity_gate"]["status"] == "verified"
    assert payload["media_person_attributions"][0]["image_ids"] == ["image-1"]
    assert person_identity_issues(payload) == []


def test_parenthetical_creator_alias_matches_media_caption() -> None:
    source = {
        "title": "南ゆい（こおりちゃん）のコスプレ画像",
        "ai_main_subject": {
            "kind": "person",
            "name": "南ゆい（こおりちゃん）",
            "role": "コスプレイヤー",
        },
        "identity_resolution": {"status": "verified"},
        "verified_social_profiles": [{
            "name": "南ゆい（こおりちゃん）",
            "role": "コスプレイヤー",
            "service": "x",
            "url": "https://x.com/CRYBABY0430",
            "confidence": 95,
        }],
        "images": [{
            "id": "media-1",
            "ai_verdict": "article",
            "alt": "南ゆいの公開X投稿",
        }],
    }

    apply_verified_person_identity_to_source(source)

    assert source["identified_people"][0]["name"] == "南ゆい（こおりちゃん）"
    assert source["media_person_attributions"][0]["image_ids"] == ["media-1"]


def test_verified_x_account_group_attributes_every_exact_post_image() -> None:
    source = {
        "title": "南ゆい（こおりちゃん）のコスプレ画像",
        "ai_main_subject": {
            "kind": "person",
            "name": "南ゆい（こおりちゃん）",
            "role": "コスプレイヤー",
        },
        "identity_resolution": {"status": "verified"},
        "verified_social_profiles": [{
            "name": "南ゆい",
            "role": "コスプレイヤー",
            "service": "x",
            "url": "https://x.com/CRYBABY0430",
            "confidence": 95,
        }],
        "images": [
            {
                "id": f"media-{number}",
                "ai_verdict": "article",
                "alt": "公開X投稿の画像",
                "ai_content_group": "x-account:crybaby0430",
            }
            for number in range(1, 4)
        ],
    }

    apply_verified_person_identity_to_source(source)

    attribution = source["media_person_attributions"][0]
    assert attribution["image_ids"] == ["media-1", "media-2", "media-3"]
    assert "official_profile" in attribution["evidence_types"]


def test_model_confidence_without_authoritative_evidence_is_rejected() -> None:
    source = {
        "title": "候補人物の水着画像",
        "ai_identified_people": [{
            "name": "候補人物",
            "role": "モデル",
            "is_public_creator": True,
            "confidence": 99,
            "evidence_types": ["headline", "alt", "official_profile"],
        }],
        "ai_media_person_attributions": [{
            "person_name": "候補人物",
            "image_ids": ["media-1"],
            "video_ids": [],
            "confidence": 99,
            "evidence_types": ["headline", "alt", "official_profile"],
        }],
        "images": [{"id": "media-1", "ai_verdict": "article", "alt": "候補人物の画像"}],
    }

    apply_verified_person_identity_to_source(source)

    assert source["identified_people"] == []
    assert source["media_person_attributions"] == []


def test_identity_below_95_is_never_displayed() -> None:
    source = {
        "title": "人物Aの写真",
        "verified_social_profiles": [{**_verified_profile("人物A"), "confidence": 94}],
        "ai_identified_people": [{
            "name": "人物A",
            "is_public_creator": True,
            "confidence": 94,
            "evidence_types": ["headline", "alt", "official_profile"],
        }],
        "ai_media_person_attributions": [{
            "person_name": "人物A",
            "image_ids": ["media-1"],
            "video_ids": [],
            "confidence": 94,
            "evidence_types": ["headline", "alt", "official_profile"],
        }],
        "images": [{"id": "media-1", "alt": "人物A"}],
    }

    apply_verified_person_identity_to_source(source)

    assert source["identified_people"] == []
    assert source["media_person_attributions"] == []


def test_multi_person_article_keeps_per_image_attributions_separate() -> None:
    source = {
        "title": "人物甲と人物乙の水着特集",
        "verified_social_profiles": [
            _verified_profile("人物甲", "x"),
            _verified_profile("人物乙", "instagram"),
        ],
        "ai_identified_people": [
            {
                "name": name,
                "role": "モデル",
                "is_public_creator": True,
                "confidence": 98,
                "evidence_types": ["headline", "alt", "official_profile"],
            }
            for name in ("人物甲", "人物乙")
        ],
        "ai_media_person_attributions": [
            {
                "person_name": "人物甲",
                "image_ids": ["media-1"],
                "video_ids": [],
                "confidence": 98,
                "evidence_types": ["headline", "alt", "official_profile"],
            },
            {
                "person_name": "人物乙",
                "image_ids": ["media-2"],
                "video_ids": [],
                "confidence": 98,
                "evidence_types": ["headline", "alt", "official_profile"],
            },
        ],
        "images": [
            {"id": "media-1", "alt": "人物甲の水着"},
            {"id": "media-2", "alt": "人物乙の水着"},
        ],
    }

    apply_verified_person_identity_to_source(source)

    by_image = {
        item["image_ids"][0]: item["person_name"]
        for item in source["media_person_attributions"]
    }
    assert by_image == {"media-1": "人物甲", "media-2": "人物乙"}


def test_uncertain_candidates_are_mapped_without_becoming_verified() -> None:
    source = {
        "images": [{"id": "media-1"}, {"id": "media-2"}],
        "videos": [{"id": "video-1"}],
        "ai_person_identity_candidates": [
            {
                "media_type": "image",
                "media_id": "media-1",
                "candidates": [
                    {
                        "name": "候補乙",
                        "role": "モデル",
                        "confidence": 72,
                        "evidence_types": ["watermark_ocr", "web_search_result"],
                        "evidence_urls": ["https://example.com/evidence-b"],
                        "reason": "透かしと掲載ページの名前が近い",
                    },
                    {
                        "name": "候補甲",
                        "role": "モデル",
                        "confidence": 88,
                        "evidence_types": ["reverse_image_result"],
                        "evidence_urls": ["https://example.com/evidence-a"],
                        "reason": "同じ画像を掲載したページが見つかった",
                    },
                ],
                "unresolved_reason": "公式ページまで確認できていない",
            },
            {
                "media_type": "video",
                "media_id": "video-1",
                "candidates": [{
                    "name": "動画候補",
                    "role": "配信者",
                    "confidence": 64,
                    "evidence_types": ["video_frame_match"],
                    "evidence_urls": ["https://example.com/video-evidence"],
                    "reason": "代表フレームと公開投稿が近い",
                }],
                "unresolved_reason": "",
            },
        ],
    }
    payload = {
        "images": [
            {"id": "image-1", "source_id": "media-1"},
            {"id": "image-2", "source_id": "media-2"},
        ],
        "videos": [{"id": "payload-video-1", "source_id": "video-1"}],
    }

    apply_verified_person_identity_to_payload(payload, source)

    assert payload["identified_people"] == []
    assert payload["media_person_attributions"] == []
    assert payload["person_identity_gate"]["status"] == "unverified"
    groups = payload["person_identity_candidates"]
    assert groups[0]["media_id"] == "image-1"
    assert [item["name"] for item in groups[0]["candidates"]] == ["候補甲", "候補乙"]
    assert groups[1]["media_id"] == "payload-video-1"


def test_unverified_media_in_verified_single_person_article_gets_context_candidate() -> None:
    source = {
        "title": "本郷柚巴の白ビキニ特集",
        "ai_main_subject": {
            "kind": "person",
            "name": "本郷柚巴",
            "role": "グラビアモデル",
        },
        "verified_social_profiles": [{
            **_verified_profile("本郷柚巴"),
            "url": "https://x.com/yuzuha_hongo",
            "confidence": 98,
        }],
        "images": [
            {"id": "media-1", "ai_verdict": "article", "alt": "本郷柚巴の白ビキニ"},
            {"id": "media-2", "ai_verdict": "article", "alt": "article image link"},
        ],
    }
    payload = {
        "title": "本郷柚巴の白ビキニ特集",
        "main_subject": {
            "kind": "person",
            "name": "本郷柚巴",
            "is_public_creator": True,
        },
        "images": [
            {"id": "image-1", "source_id": "media-1"},
            {"id": "image-2", "source_id": "media-2"},
        ],
        "videos": [],
    }

    apply_verified_person_identity_to_source(source)
    apply_verified_person_identity_to_payload(payload, source)

    assert payload["media_person_attributions"][0]["image_ids"] == ["image-1"]
    assert payload["person_identity_candidates"] == [{
        "media_type": "image",
        "media_id": "image-2",
        "candidates": [{
            "name": "本郷柚巴",
            "role": "グラビアモデル",
            "confidence": 80,
            "evidence_types": ["headline", "source_metadata"],
            "evidence_urls": ["https://x.com/yuzuha_hongo"],
            "reason": (
                "単独人物の記事見出しと検証済み公式プロフィールは一致していますが、"
                "この素材単体では本人と断定できる別の根拠が不足しています"
            ),
        }],
        "unresolved_reason": "素材単体の公式表記または同一画像照合が未確認",
    }]


def test_verified_media_suppresses_uncertain_candidate_for_same_media() -> None:
    source = {
        "title": "確定人物の写真",
        "verified_social_profiles": [_verified_profile("確定人物")],
        "ai_identified_people": [{
            "name": "確定人物",
            "role": "モデル",
            "is_public_creator": True,
            "confidence": 98,
            "evidence_types": ["headline", "alt", "official_profile"],
        }],
        "ai_media_person_attributions": [{
            "person_name": "確定人物",
            "image_ids": ["media-1"],
            "video_ids": [],
            "confidence": 98,
            "evidence_types": ["headline", "alt", "official_profile"],
            "reason": "公式プロフィールまで一致",
        }],
        "ai_person_identity_candidates": [{
            "media_type": "image",
            "media_id": "media-1",
            "candidates": [{
                "name": "別候補",
                "confidence": 80,
                "evidence_types": ["web_search_result"],
                "reason": "未確定候補",
            }],
        }],
        "images": [{"id": "media-1", "alt": "確定人物の写真"}],
    }
    apply_verified_person_identity_to_source(source)
    payload = {
        "images": [{"id": "image-1", "source_id": "media-1"}],
        "videos": [],
    }

    apply_verified_person_identity_to_payload(payload, source)

    assert payload["media_person_attributions"][0]["image_ids"] == ["image-1"]
    assert payload["person_identity_candidates"] == []
