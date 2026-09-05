from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.fanza_catalog import (  # noqa: E402
    hydrate_related_fanza_products,
    prefetch_related_fanza_products,
    resolve_related_fanza_product,
)


def _product() -> dict[str, str]:
    return {
        "product_id": "swim001",
        "url": "https://video.dmm.co.jp/av/content/?id=swim001",
        "title": "競泳水着で撮影する作品",
        "thumbnail_url": "https://pics.dmm.co.jp/digital/video/swim001/swim001pl.jpg",
        "matched_query": "水着",
    }


def test_topic_search_becomes_one_real_product_with_its_own_package(tmp_path: Path) -> None:
    payload = {
        "blocks": [{
            "id": "related",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=%E6%B0%B4%E7%9D%80",
            "title": "水着系の作品を探す",
            "link_kind": "inferred_topic_search",
            "search_query": "水着",
        }],
    }

    assert hydrate_related_fanza_products(payload, tmp_path, lambda _query: [_product()])

    card = payload["blocks"][0]
    assert card["link_kind"] == "inferred_topic_product"
    assert card["url"] == _product()["url"]
    assert card["thumbnail_url"] == _product()["thumbnail_url"]
    assert card["thumbnail_owner_url"] == card["url"]
    assert "記事本人の作品ではありません" in card["text"]


def test_topic_product_prefers_downloaded_official_large_package(tmp_path: Path) -> None:
    payload = {
        "blocks": [{
            "id": "related",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=%E6%B0%B4%E7%9D%80",
            "title": "水着系の作品を探す",
            "link_kind": "inferred_topic_search",
            "search_query": "水着",
            "thumbnail_image_id": "article-person-image",
        }],
    }
    large_package = (
        "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/"
        "swim001/swim001pl.jpg"
    )

    assert hydrate_related_fanza_products(
        payload,
        tmp_path,
        lambda _query: [_product()],
        lambda product_id: large_package if product_id == "swim001" else "",
    )

    card = payload["blocks"][0]
    assert card["thumbnail_url"] == large_package
    assert card["thumbnail_owner_url"] == _product()["url"]
    assert "thumbnail_image_id" not in card


def test_related_product_lookup_uses_seven_day_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def discover(query: str) -> list[dict[str, str]]:
        calls.append(query)
        return [_product()]

    assert resolve_related_fanza_product(tmp_path, "水着", discover)
    assert resolve_related_fanza_product(tmp_path, "水着", discover)
    assert calls == ["水着"]


def test_unresolved_topic_search_never_keeps_an_article_image(tmp_path: Path) -> None:
    payload = {
        "blocks": [{
            "id": "related",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/search/?searchstr=%E6%B0%B4%E7%9D%80",
            "link_kind": "inferred_topic_search",
            "search_query": "水着",
            "thumbnail_image_id": "article-image",
            "thumbnail_url": "https://example.com/person.jpg",
        }],
    }

    assert hydrate_related_fanza_products(payload, tmp_path, lambda _query: [])
    card = payload["blocks"][0]
    assert "thumbnail_image_id" not in card
    assert "thumbnail_url" not in card


def test_verified_performer_card_uses_a_real_work_package_as_its_sample(
    tmp_path: Path,
) -> None:
    performer_url = "https://video.dmm.co.jp/av/list/?actress=12345"
    payload = {
        "blocks": [{
            "id": "performer",
            "type": "related_link",
            "url": performer_url,
            "title": "出演者Aの出演作品",
            "person_name": "出演者A",
            "link_kind": "verified_person_search",
        }],
    }

    assert hydrate_related_fanza_products(
        payload, tmp_path, lambda query: [{**_product(), "matched_query": query}]
    )

    card = payload["blocks"][0]
    assert card["url"] == performer_url
    assert card["thumbnail_source_kind"] == "fanza_performer_sample"
    assert card["thumbnail_url"] == _product()["thumbnail_url"]
    assert card["thumbnail_owner_url"] == _product()["url"]
    assert card["sample_product_url"] == _product()["url"]
    assert "出演作品の一例" in card["text"]


def test_unresolved_performer_card_is_removed_instead_of_implying_av_work(
    tmp_path: Path,
) -> None:
    payload = {
        "blocks": [{
            "id": "performer",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=人物A",
            "title": "人物Aの出演作品",
            "person_name": "人物A",
            "link_kind": "verified_person_search",
            "thumbnail_image_id": "article-person-image",
        }],
    }

    assert hydrate_related_fanza_products(payload, tmp_path, lambda _query: [])
    assert payload["blocks"] == []
    assert payload["unresolved_fanza_performer_names"] == ["人物A"]


def test_many_topic_queries_are_discovered_once_and_cached(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def discover(queries: list[str]) -> list[dict[str, str]]:
        calls.append(queries)
        return [
            {
                **_product(),
                "product_id": f"item{index:03d}",
                "url": f"https://video.dmm.co.jp/av/content/?id=item{index:03d}",
                "thumbnail_url": (
                    "https://pics.dmm.co.jp/digital/video/"
                    f"item{index:03d}/item{index:03d}pl.jpg"
                ),
                "matched_query": query,
            }
            for index, query in enumerate(queries)
        ]

    first = prefetch_related_fanza_products(
        tmp_path, ["水着", "バニー"], discover
    )
    second = prefetch_related_fanza_products(
        tmp_path, ["水着", "バニー"], discover
    )

    assert set(first) == {"水着", "バニー"}
    assert second == first
    assert calls == [["水着", "バニー"]]
