from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from repair_known_identity_articles import (
    HAYASHIDA_SLUG,
    KATO_AIRI_SLUG,
    PAN_SLUG,
    SAKURA_SLUG,
    repair_hayashida_moka,
    repair_kato_airi,
    repair_pan_piano,
    repair_sakura_miko,
)


def test_pan_repair_removes_the_different_creator_and_dependent_copy() -> None:
    payload = {
        "slug": PAN_SLUG,
        "title": "old",
        "images": [
            {"id": "source-image-1", "alt": "old"},
            {"id": "source-image-2", "alt": "old"},
            {"id": "source-image-3", "alt": "@nacocomusic1552"},
        ],
        "blocks": [
            {"id": "old", "type": "images", "image_ids": ["source-image-3"]},
            {"id": "footer", "type": "related_link", "url": "https://example.com"},
        ],
    }

    assert repair_pan_piano(payload)
    assert "ギター" not in payload["title"]
    assert {item["id"] for item in payload["images"]} == {
        "source-image-1", "source-image-2"
    }
    assert all(
        "source-image-3" not in (block.get("image_ids") or [])
        for block in payload["blocks"]
    )
    assert payload["blocks"][-1]["id"] == "footer"


def test_hayashida_repair_adds_named_media_and_official_work_evidence() -> None:
    payload = {
        "slug": HAYASHIDA_SLUG,
        "images": [{"id": "source-image-1", "alt": "generic"}],
        "related_destinations": [{"provider": "web_search", "url": "https://google.com"}],
    }

    assert repair_hayashida_moka(payload)
    assert payload["images"][0]["alt"].startswith("林田百加")
    assert payload["identity_resolution"]["status"] == "verified"
    assert payload["related_destinations"][0]["link_kind"] == "official_content"
    assert all(item.get("provider") != "web_search" for item in payload["related_destinations"])


def test_sakura_repair_adds_official_performer_page() -> None:
    payload = {
        "slug": SAKURA_SLUG,
        "images": [{"id": "source-image-1", "alt": "OGP image"}],
        "related_destinations": [],
    }
    before = deepcopy(payload)

    assert repair_sakura_miko(payload)
    assert payload != before
    assert payload["images"][0]["alt"].startswith("佐倉みこ")
    assert payload["identity_resolution"]["method"] == "official_page"
    assert payload["related_destinations"][0]["match_confidence"] == 99


def test_kato_airi_repair_replaces_generic_fanza_with_official_destinations() -> None:
    payload = {
        "slug": KATO_AIRI_SLUG,
        "title": "old",
        "images": [
            {"id": f"source-image-{index}", "alt": "generic"}
            for index in range(1, 6)
        ],
        "blocks": [
            {"id": "media", "type": "images", "image_ids": ["source-image-1"]},
            {"id": "post", "type": "post", "text": "本文"},
            {
                "id": "generic-fanza",
                "type": "related_link",
                "url": "https://video.dmm.co.jp/av/content/?id=unrelated",
                "link_kind": "inferred_topic_product",
            },
        ],
        "related_destinations": [{
            "provider": "fanza",
            "link_kind": "inferred_topic_product",
        }],
    }

    assert repair_kato_airi(payload)
    assert payload["main_subject"]["name"] == "加藤愛梨"
    assert all(item["alt"].startswith("加藤愛梨") for item in payload["images"])
    assert all(
        "video.dmm.co.jp" not in str(block.get("url") or "")
        for block in payload["blocks"]
    )
    assert any(
        block.get("link_kind") == "exact_official_work"
        for block in payload["blocks"]
    )
    profile_blocks = [
        block for block in payload["blocks"]
        if block.get("link_kind") == "official_profile"
    ]
    assert {block["provider"] for block in profile_blocks} == {
        "x", "instagram", "tiktok",
    }
