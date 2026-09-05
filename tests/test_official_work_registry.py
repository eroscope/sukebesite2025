import json
from pathlib import Path

from tools.indanya_desktop.official_work_registry import (
    enrich_analysis_official_work,
    remember_verified_official_work,
    resolve_verified_official_work,
)
from tools.repair_official_work_destination import repair_payload_official_work


def test_bundled_registry_resolves_anime_title_without_another_search(tmp_path: Path) -> None:
    result = resolve_verified_official_work(
        tmp_path,
        "TVアニメ『ゾンビのあふれた世界で俺だけが襲われない』",
    )

    assert result is not None
    assert result["url"] == "https://zom-ore.com/"
    assert result["status"] == "verified"


def test_ambiguous_ai_result_is_replaced_by_verified_registry_entry(tmp_path: Path) -> None:
    analysis = {
        "main_subject": {
            "name": "ゾンビのあふれた世界で俺だけが襲われない",
            "kind": "work",
        },
        "official_work": {
            "status": "ambiguous",
            "title": "",
            "url": "",
        },
    }

    enriched = enrich_analysis_official_work(tmp_path, analysis)

    assert enriched["official_work"]["url"] == "https://zom-ore.com/"
    assert enriched["official_work"]["registry_match"] is True


def test_verified_result_is_persisted_and_reused(tmp_path: Path) -> None:
    verified = {
        "status": "verified",
        "title": "固有テスト作品",
        "url": "https://publisher.example.com/works/test-work/",
        "provider": "出版社公式",
        "reason": "出版社の作品詳細ページで完全一致",
        "thumbnail_url": "https://publisher.example.com/works/test-work/cover.jpg",
    }
    remember_verified_official_work(tmp_path, "固有テスト作品 アニメ版", verified)

    learned = resolve_verified_official_work(tmp_path, "固有テスト作品 アニメ版")
    stored = json.loads(
        (tmp_path / ".article-studio" / "official-work-registry.json").read_text(
            encoding="utf-8"
        )
    )

    assert learned is not None
    assert learned["url"] == verified["url"]
    assert stored["entries"][0]["verified_by"] == "codex_web_search"


def test_repair_replaces_generic_ranking_with_exact_official_card(tmp_path: Path) -> None:
    payload = {
        "slug": "zombie-anime",
        "title": "ゾンビアニメPV",
        "tags": ["アニメ"],
        "main_subject": {
            "name": "ゾンビのあふれた世界で俺だけが襲われない",
            "kind": "work",
        },
        "thumbnail_id": "image-1",
        "images": [{"id": "image-1"}],
        "blocks": [
            {"id": "media", "type": "images", "image_ids": ["image-1"]},
            {
                "id": "old-ranking",
                "type": "related_link",
                "link_kind": "inferred_topic_search",
                "url": "https://www.dmm.co.jp/digital/videoa/-/ranking/=/term=monthly/",
            },
        ],
        "affiliate_opportunities": [],
    }

    repaired = repair_payload_official_work(payload, tmp_path)
    links = [
        block for block in repaired["blocks"]
        if block.get("type") == "related_link"
    ]

    assert [block["link_kind"] for block in links] == ["exact_official_work"]
    assert links[0]["url"] == "https://zom-ore.com/"
    assert links[0]["thumbnail_image_id"] == "image-1"
