from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.image_text_identity import (  # noqa: E402
    apply_local_identity_matches_to_analysis,
    enrich_source_with_local_image_text,
    extract_public_handle_candidates,
)
from indanya_desktop.social_profiles import save_social_profile_registry  # noqa: E402


def test_extracts_written_handle_without_guessing_plain_words() -> None:
    result = extract_public_handle_candidates([
        {"text": "airi_kato_official", "confidence": 95},
        {"text": "Chuo University", "confidence": 98},
        {"text": "ミス中央 No.5", "confidence": 94},
        {"text": "https://example.com", "confidence": 91},
    ])

    assert [item["handle"] for item in result] == ["airi_kato_official"]
    assert result[0]["evidence_type"] == "watermark_ocr"


def test_ocr_is_cached_and_matches_a_previously_verified_handle() -> None:
    calls = 0

    class FakeEngine:
        def __call__(self, _data: bytes) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                txts=("airi_kato_official", "加藤愛梨"),
                scores=(0.96, 0.98),
            )

    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary)
        save_social_profile_registry(site_root, {
            "people": [{
                "canonical_name": "加藤愛梨",
                "role": "俳優・グラビア",
                "status": "verified",
                "confidence": 99,
                "profiles": [{
                    "service": "instagram",
                    "url": "https://www.instagram.com/airi_kato_official/",
                }],
            }]
        })
        source = {
            "images": [{
                "id": "media-1",
                "data": b"image-bytes" * 100,
            }]
        }

        first = enrich_source_with_local_image_text(
            site_root, source, engine_factory=FakeEngine
        )
        second = enrich_source_with_local_image_text(
            site_root,
            source,
            engine_factory=lambda: (_ for _ in ()).throw(AssertionError("cache miss")),
        )

        assert calls == 1
        assert first["images"][0]["local_ocr_text"] == "airi_kato_official / 加藤愛梨"
        assert first["local_identity_clues"][0]["known_identity_matches"][0]["name"] == "加藤愛梨"
        assert second["local_ocr"]["cache_hits"] == 1
        assert second["local_ocr"]["model_calls"] == 0


def test_known_handle_match_fills_identity_when_model_misses_it() -> None:
    source = {
        "local_identity_clues": [{
            "image_id": "media-3",
            "known_identity_matches": [{
                "name": "加藤愛梨",
                "role": "俳優・グラビアモデル",
                "confidence": 99,
                "profiles": [{
                    "service": "instagram",
                    "url": "https://www.instagram.com/airi_kato_official/",
                }],
            }],
        }]
    }
    analysis = {
        "main_subject": {
            "name": "",
            "kind": "unknown",
            "role": "",
            "is_public_creator": False,
            "reason": "人物名を拾えなかった",
        },
        "identified_people": [],
        "media_person_attributions": [],
        "person_identity_candidates": [{
            "media_type": "image",
            "media_id": "media-3",
            "candidates": [],
        }],
        "social_profiles": [],
    }

    result = apply_local_identity_matches_to_analysis(source, analysis)

    assert result["main_subject"]["name"] == "加藤愛梨"
    assert result["identified_people"][0]["confidence"] == 99
    assert result["media_person_attributions"][0]["image_ids"] == ["media-3"]
    assert result["social_profiles"][0]["service"] == "instagram"
    assert result["person_identity_candidates"] == []
