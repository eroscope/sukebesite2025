from __future__ import annotations

import base64
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.person_identity import apply_verified_person_identity_to_source  # noqa: E402
from indanya_desktop.visual_identity import (  # noqa: E402
    apply_known_visual_identity_matches,
    apply_known_visual_identity_matches_to_payload,
    mark_payload_identity_research_state,
    recheck_unresolved_draft_identities,
    record_verified_visual_identities,
)


def _sample_image(*, quality: int | None = None) -> bytes:
    image = Image.new("RGB", (480, 720), (238, 226, 214))
    draw = ImageDraw.Draw(image)
    draw.rectangle((45, 60, 430, 640), fill=(62, 105, 148))
    draw.ellipse((110, 130, 370, 390), fill=(220, 166, 142))
    draw.polygon([(80, 610), (240, 410), (420, 610)], fill=(184, 48, 69))
    buffer = io.BytesIO()
    if quality is None:
        image.save(buffer, format="PNG")
    else:
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _verified_source(data: bytes, *, media_type: str = "image") -> dict:
    name = "人物甲"
    source = {
        "title": f"{name}の公開写真",
        "verified_social_profiles": [{
            "name": name,
            "display_name": name,
            "role": "モデル",
            "service": "x",
            "url": "https://x.com/person_a",
            "confidence": 98,
        }],
        "ai_identified_people": [{
            "name": name,
            "role": "モデル",
            "is_public_creator": True,
            "confidence": 98,
            "evidence_types": ["headline", "alt", "official_profile"],
            "reason": "公式アカウントまで一致",
        }],
        "ai_media_person_attributions": [{
            "person_name": name,
            "image_ids": ["media-1"] if media_type == "image" else [],
            "video_ids": ["video-1"] if media_type == "video" else [],
            "confidence": 98,
            "evidence_types": ["headline", "alt", "official_profile"],
            "reason": "見出し、説明、公式アカウントが一致",
        }],
        "images": ([{"id": "media-1", "alt": f"{name}の写真", "data": data}]
                   if media_type == "image" else []),
        "videos": ([{"id": "video-1", "alt": f"{name}の動画", "frame_data": data}]
                   if media_type == "video" else []),
        "url": "https://example.com/verified-source",
    }
    apply_verified_person_identity_to_source(source)
    return source


def test_exact_whole_image_match_reuses_prior_verified_identity() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary)
        data = _sample_image()
        original = _verified_source(data)
        assert record_verified_visual_identities(site_root, original) == 1

        unnamed = {
            "title": "名前のない転載画像",
            "images": [{"id": "unknown-image", "data": data}],
            "videos": [],
        }
        apply_known_visual_identity_matches(site_root, unnamed)
        apply_verified_person_identity_to_source(unnamed)

        assert [item["name"] for item in unnamed["identified_people"]] == ["人物甲"]
        attribution = unnamed["media_person_attributions"][0]
        assert attribution["image_ids"] == ["unknown-image"]
        assert attribution["confidence"] == 100
        assert attribution["evidence_types"] == [
            "visual_exact_match", "verified_visual_registry"
        ]
        assert unnamed["verified_social_profiles"][0]["url"] == "https://x.com/person_a"

        registry_path = site_root / ".article-studio" / "person-identity-visual-registry.json"
        registry_text = registry_path.read_text(encoding="utf-8")
        assert "unknown-image" not in registry_text
        assert "data:image" not in registry_text
        assert json.loads(registry_text)["records"][0]["fingerprint"]["normalized_sha256"]


def test_exact_video_frame_match_uses_same_precision_gate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary)
        data = _sample_image()
        original = _verified_source(data, media_type="video")
        assert record_verified_visual_identities(site_root, original) == 1

        unnamed = {
            "title": "出演者名のない動画",
            "images": [],
            "videos": [{"id": "unknown-video", "frame_data": data}],
        }
        apply_known_visual_identity_matches(site_root, unnamed)
        apply_verified_person_identity_to_source(unnamed)

        assert unnamed["media_person_attributions"][0]["video_ids"] == ["unknown-video"]
        assert unnamed["media_person_attributions"][0]["confidence"] == 100


def test_saved_payload_is_rechecked_from_its_data_url_without_blocking() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary)
        data = _sample_image()
        original = _verified_source(data)
        assert record_verified_visual_identities(site_root, original) == 1
        payload = {
            "images": [{
                "id": "image-1",
                "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
            }],
            "videos": [],
            "person_identity_candidates": [{
                "media_type": "image",
                "media_id": "image-1",
                "candidates": [],
                "unresolved_reason": "元ページに人物名がない",
            }],
            "identity_resolution": {"status": "not_applicable"},
        }

        assert mark_payload_identity_research_state(payload) is True
        assert payload["identity_research"]["blocks_publication"] is False
        assert payload["identity_resolution"]["status"] == "unresolved"
        assert apply_known_visual_identity_matches_to_payload(site_root, payload) == 1

        assert payload["person_identity_candidates"] == []
        assert payload["media_person_attributions"][0]["person_name"] == "人物甲"
        assert payload["images"][0]["identified_people"] == ["人物甲"]
        assert payload["identity_research"]["status"] == "resolved"


def test_registry_change_rechecks_and_queues_an_old_unresolved_draft() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary)
        data = _sample_image()
        draft_root = site_root / ".article-studio" / "drafts"
        draft_root.mkdir(parents=True)
        draft_path = draft_root / "old-unresolved-article.json"
        draft_path.write_text(json.dumps({
            "slug": "old-unresolved-article",
            "title": "名前のない画像",
            "status": "published",
            "images": [{
                "id": "image-1",
                "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
            }],
            "videos": [],
            "blocks": [{"id": "media", "type": "images", "image_ids": ["image-1"]}],
            "person_identity_candidates": [{
                "media_type": "image",
                "media_id": "image-1",
                "candidates": [],
                "unresolved_reason": "元ページに人物名がない",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        assert record_verified_visual_identities(
            site_root, _verified_source(data)
        ) == 1

        result = recheck_unresolved_draft_identities(site_root, limit=12)

        assert result["matched"] == 1
        assert result["queued"] == ["old-unresolved-article"]
        saved = json.loads(draft_path.read_text(encoding="utf-8"))
        assert saved["person_identity_candidates"] == []
        assert saved["media_person_attributions"][0]["person_name"] == "人物甲"
        automation = json.loads(
            (site_root / ".article-studio" / "automation-settings.json").read_text(encoding="utf-8")
        )
        assert automation["queue"][0]["slug"] == "old-unresolved-article"
