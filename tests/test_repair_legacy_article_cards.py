from __future__ import annotations

import os
import base64
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from repair_legacy_article_cards import (  # noqa: E402
    _existing_official_profiles,
    _resolve_official_page_thumbnail,
    infer_public_url,
    refresh_rebuilt_site_discovery,
    repair_exact_product_card_thumbnails,
    repair_official_work_card_thumbnails,
    repair_selected_media_exact_product,
    repair_video_thumbnail_only_flag,
)
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
    prune_unreferenced_related_thumbnail_assets,
)


def test_exact_product_card_uses_package_and_drops_article_thumbnail() -> None:
    product_url = "https://video.dmm.co.jp/av/content/?id=abc001"
    package_url = "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg"
    payload = {
        "blocks": [{
            "type": "product_cta",
            "match_type": "exact_image",
            "url": product_url,
            "thumbnail_image_id": "source-image-1",
        }],
    }

    assert repair_exact_product_card_thumbnails(
        payload, {product_url: package_url}
    ) is True
    card = payload["blocks"][0]
    assert card["thumbnail_url"] == package_url
    assert card["thumbnail_source_kind"] == "fanza_package"
    assert card["thumbnail_owner_url"] == product_url
    assert "thumbnail_image_id" not in card


def test_selected_media_filename_code_adds_verified_inline_product() -> None:
    payload = {
        "images": [{
            "id": "source-image-1",
            "source_url": "https://blog-imgs-182.example.com/e/savr-1195_20260901_sns.jpg",
        }],
        "blocks": [
            {"id": "gallery", "type": "images", "image_ids": ["source-image-1"]},
            {"id": "post", "type": "post", "text": "本文"},
        ],
    }
    product_url = "https://video.dmm.co.jp/av/content/?id=savr1195"
    package_url = (
        "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/"
        "savr1195/savr1195pl.jpg"
    )

    assert repair_selected_media_exact_product(payload, {
        "savr-1195": {
            "url": product_url,
            "product_id": "savr1195",
            "title": "SAVR-1195の作品",
            "thumbnail_url": package_url,
        }
    }) is True

    card = payload["blocks"][1]
    assert card["type"] == "product_cta"
    assert card["url"] == product_url
    assert card["thumbnail_url"] == package_url
    assert card["match_type"] == "exact_image"
    assert card["match_confidence"] == 98


def test_official_work_card_uses_its_page_image_not_article_media() -> None:
    page_url = "https://publisher.example.com/comics/exact-work/"
    thumbnail_url = "https://publisher.example.com/images/exact-work.jpg"
    payload = {
        "images": [{"id": "source-image-1", "rights_basis": "article_source"}],
        "blocks": [{
            "type": "related_link",
            "link_kind": "exact_official_work",
            "url": page_url,
            "thumbnail_image_id": "source-image-1",
        }],
    }

    assert repair_official_work_card_thumbnails(
        payload, {page_url: thumbnail_url}
    ) is True
    card = payload["blocks"][0]
    assert card["thumbnail_url"] == thumbnail_url
    assert card["thumbnail_source_kind"] == "official_page"
    assert card["thumbnail_owner_url"] == page_url
    assert "thumbnail_image_id" not in card


def test_official_work_card_drops_wrong_image_when_page_has_no_thumbnail() -> None:
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = {
        "images": [{"id": "source-image-1", "rights_basis": "article_source"}],
        "blocks": [{
            "type": "related_link",
            "link_kind": "exact_official_work",
            "url": page_url,
            "thumbnail_image_id": "source-image-1",
            "thumbnail_source_kind": "article_media",
        }],
    }

    assert repair_official_work_card_thumbnails(payload, {}) is True
    card = payload["blocks"][0]
    assert "thumbnail_image_id" not in card
    assert "thumbnail_source_kind" not in card


def test_official_page_thumbnail_is_saved_as_non_article_asset() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEklEQVR4nGP4DwYMYMAEoQhxACK8BgFIJminAAAAAElFTkSuQmCC"
    )
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = {
        "images": [],
        "blocks": [{
            "type": "related_link",
            "link_kind": "exact_official_work",
            "url": page_url,
            "title": "確定作品の公式ページ",
            "thumbnail_url": "https://publisher.example.com/images/work.png",
            "thumbnail_source_kind": "official_page",
            "thumbnail_owner_url": page_url,
        }],
    }

    assert localize_related_thumbnail_assets(
        payload,
        downloader=lambda _url: (png, ".png", "image/png"),
    ) is True
    card = payload["blocks"][0]
    assert card["thumbnail_image_id"].startswith("related-official-")
    asset = payload["images"][0]
    assert asset["rights_basis"] == "official_page_thumbnail"
    assert asset["thumbnail_owner_url"] == page_url
    assert asset["related_thumbnail_only"] is True


def test_mgs_exact_work_fetches_its_own_title_and_package() -> None:
    jpg = base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
    )
    product_url = "https://www.mgstage.com/product/product_detail/300MIUM-1293/"
    payload = {
        "images": [],
        "blocks": [{
            "type": "related_link",
            "link_kind": "exact_official_work",
            "provider": "mgs",
            "url": product_url,
            "title": "MGS動画 300MIUM-1293",
        }],
    }

    assert localize_related_thumbnail_assets(
        payload,
        downloader=lambda _url: (jpg, ".jpg", "image/jpeg"),
        mgs_metadata_resolver=lambda _url: {
            "product_title": "社内で一番の美人巨乳と中出し不倫",
            "thumbnail_url": "https://image.mgstage.com/work-package.jpg",
        },
    ) is True

    card = payload["blocks"][0]
    assert card["title"] == "社内で一番の美人巨乳と中出し不倫"
    assert card["thumbnail_image_id"].startswith("related-official-")
    assert payload["images"][0]["rights_source_url"] == product_url


def test_official_page_thumbnail_falls_back_to_rendered_main_image() -> None:
    page_url = "https://publisher.example.com/comics/exact-work/"
    result = _resolve_official_page_thumbnail(
        page_url,
        static_fetcher=lambda _url: "",
        rendered_fetcher=lambda _url: {
            "images": [
                {
                    "url": "https://publisher.example.com/loading.gif",
                    "width": 900,
                    "height": 500,
                    "browser_visible": True,
                },
                {
                    "url": "https://publisher.example.com/images/exact-work.jpg",
                    "width": 900,
                    "height": 394,
                    "browser_visible": True,
                    "inside_article": True,
                },
                {
                    "url": "https://publisher.example.com/images/tiny.jpg",
                    "width": 120,
                    "height": 120,
                },
            ],
        },
    )

    assert result == "https://publisher.example.com/images/exact-work.jpg"


def test_repair_video_thumbnail_only_flag_preserves_video_first_articles() -> None:
    payload = {
        "thumbnail_id": "source-image-1",
        "images": [{"id": "source-image-1"}],
        "videos": [{"id": "source-video-1"}],
        "blocks": [{"type": "videos", "video_ids": ["source-video-1"]}],
    }

    assert repair_video_thumbnail_only_flag(payload) is True
    assert payload["thumbnail_only"] is True
    assert repair_video_thumbnail_only_flag(payload) is False


def test_prune_unreferenced_related_thumbnail_assets_keeps_live_cards_only() -> None:
    payload = {
        "images": [
            {"id": "article-image"},
            {"id": "profile-live", "related_thumbnail_only": True},
            {"id": "profile-orphan", "related_thumbnail_only": True},
        ],
        "blocks": [
            {"type": "images", "image_ids": ["article-image"]},
            {
                "type": "related_link",
                "link_kind": "official_profile",
                "thumbnail_image_id": "profile-live",
            },
        ],
    }

    assert prune_unreferenced_related_thumbnail_assets(payload) is True
    assert [image["id"] for image in payload["images"]] == [
        "article-image", "profile-live",
    ]
    assert prune_unreferenced_related_thumbnail_assets(payload) is False


def test_existing_verified_profile_is_reusable_without_inventing_an_account() -> None:
    payload = {
        "main_subject": {
            "name": "やんやん",
            "role": "コスプレイヤー",
        },
        "blocks": [{
            "type": "related_link",
            "link_kind": "official_profile",
            "provider": "x",
            "url": "https://x.com/yanyan_cos",
            "title": "やんやんのX",
            "match_evidence": "確認済み人物名簿",
            "match_confidence": 98,
        }],
    }

    assert _existing_official_profiles(payload) == [{
        "name": "やんやん",
        "role": "コスプレイヤー",
        "service": "x",
        "url": "https://x.com/yanyan_cos",
        "is_main_subject": True,
        "reason": "確認済み人物名簿",
        "confidence": 98,
    }]


def test_product_subject_does_not_replace_the_performer_name_on_social_cards() -> None:
    payload = {
        "main_subject": {
            "name": "非常に長いFANZA商品タイトル",
            "kind": "product",
            "role": "AV作品",
        },
        "blocks": [{
            "type": "related_link",
            "link_kind": "official_profile",
            "provider": "x",
            "url": "https://x.com/performer_a",
            "title": "出演者AのX",
            "person_name": "出演者A",
            "match_confidence": 98,
        }],
    }

    profiles = _existing_official_profiles(payload)

    assert profiles[0]["name"] == "出演者A"


def test_performer_sample_package_is_saved_as_a_card_only_asset() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEklEQVR4nGP4DwYMYMAEoQhxACK8BgFIJminAAAAAElFTkSuQmCC"
    )
    product_url = "https://video.dmm.co.jp/av/content/?id=sample001"
    payload = {
        "images": [],
        "blocks": [{
            "type": "related_link",
            "link_kind": "verified_person_search",
            "url": "https://video.dmm.co.jp/av/list/?actress=12345",
            "title": "出演者Aの出演作品",
            "thumbnail_url": (
                "https://pics.dmm.co.jp/digital/video/sample001/sample001pl.jpg"
            ),
            "thumbnail_source_kind": "fanza_performer_sample",
            "thumbnail_owner_url": product_url,
            "sample_product_url": product_url,
        }],
    }

    assert localize_related_thumbnail_assets(
        payload, downloader=lambda _url: (png, ".png", "image/png")
    )

    card = payload["blocks"][0]
    image = payload["images"][0]
    assert card["thumbnail_image_id"].startswith("related-product-")
    assert image["thumbnail_owner_url"] == product_url
    assert image["related_thumbnail_only"] is True


def test_profile_thumbnail_is_saved_as_a_non_article_asset() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEklEQVR4nGP4DwYMYMAEoQhxACK8BgFIJminAAAAAElFTkSuQmCC"
    )
    payload = {
        "images": [],
        "blocks": [{
            "type": "related_link",
            "link_kind": "official_profile",
            "provider": "instagram",
            "url": "https://www.instagram.com/example/",
            "title": "本人のInstagram",
            "thumbnail_url": "https://cdn.example/profile.png?expires=1",
            "thumbnail_source_kind": "profile",
            "thumbnail_owner_url": "https://www.instagram.com/example/",
        }],
    }

    changed = localize_related_thumbnail_assets(
        payload,
        downloader=lambda _url: (png, ".png", "image/png"),
    )

    assert changed is True
    card = payload["blocks"][0]
    assert "thumbnail_url" not in card
    assert card["thumbnail_image_id"].startswith("related-profile-")
    asset = payload["images"][0]
    assert asset["id"] == card["thumbnail_image_id"]
    assert asset["related_thumbnail_only"] is True
    assert asset["ai_role"] == "profile_thumbnail"


def test_blocked_profile_uses_another_verified_profile_image_not_article_media() -> None:
    payload = {
        "images": [
            {"id": "article-image", "name": "article.jpg"},
            {
                "id": "x-profile",
                "name": "x-profile.png",
                "data_url": "data:image/png;base64,AAAA",
                "source_url": "https://pbs.twimg.com/profile_images/example.png",
                "thumbnail_owner_url": "https://x.com/example",
                "rights_basis": "official_profile_thumbnail",
                "related_thumbnail_only": True,
            },
        ],
        "blocks": [
            {
                "type": "related_link",
                "link_kind": "official_profile",
                "provider": "x",
                "url": "https://x.com/example",
                "title": "本人のX",
                "thumbnail_image_id": "x-profile",
                "thumbnail_source_kind": "profile",
                "thumbnail_owner_url": "https://x.com/example",
            },
            {
                "type": "related_link",
                "link_kind": "official_profile",
                "provider": "tiktok",
                "url": "https://www.tiktok.com/@example",
                "title": "本人のTikTok",
                "thumbnail_image_id": "article-image",
            },
        ],
    }

    assert apply_related_thumbnail_fallbacks(payload) is True

    card = payload["blocks"][1]
    assert card["thumbnail_image_id"] != "article-image"
    assert card["thumbnail_source_kind"] == "official_identity_fallback"
    fallback = next(
        image for image in payload["images"]
        if image["id"] == card["thumbnail_image_id"]
    )
    assert fallback["thumbnail_owner_url"] == card["url"]
    assert fallback["rights_source_url"] == "https://x.com/example"
    assert fallback["related_thumbnail_only"] is True


def test_infer_public_url_uses_site_identity(tmp_path: Path) -> None:
    state = tmp_path / ".article-studio"
    state.mkdir()
    (state / "analytics-owner-v2.json").write_text(
        json.dumps({"public_url": "https://example.com/site"}),
        encoding="utf-8",
    )

    assert infer_public_url(tmp_path) == "https://example.com/site/"
    assert infer_public_url(tmp_path, "https://override.example/base/") == (
        "https://override.example/base/"
    )


def test_bulk_rebuild_refreshes_and_validates_all_sitemaps(tmp_path: Path) -> None:
    article_dir = tmp_path / "articles"
    data_dir = tmp_path / "data"
    article_dir.mkdir()
    data_dir.mkdir()
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head></head><body><nav></nav></body></html>",
        encoding="utf-8",
    )
    for name in (
        "new.html", "popular.html", "random.html", "tags.html", "guide.html",
        "categories.html", "fanza.html", "about.html", "privacy.html",
        "advertising.html", "contact.html",
    ):
        (tmp_path / name).write_text("<!doctype html><html></html>", encoding="utf-8")
    (article_dir / "sample.html").write_text(
        '<!doctype html><html><body><img src="../assets/sample.jpg" alt="sample"></body></html>',
        encoding="utf-8",
    )
    articles = [{
        "slug": "sample",
        "status": "published",
        "url": "articles/sample.html",
        "title": "sample",
        "summary": "sample",
        "thumbnail": "assets/sample.jpg",
        "published_at": "2026-09-01T00:00:00+09:00",
        "tags": [],
    }]
    (data_dir / "articles.json").write_text(json.dumps(articles), encoding="utf-8")

    result = refresh_rebuilt_site_discovery(
        tmp_path,
        public_url="https://example.com/site/",
    )

    assert result["status"] == "healthy"
    assert result["health"]["published_articles"] == 1
    robots = (tmp_path / "robots.txt").read_text(encoding="utf-8")
    for name in ("sitemap.xml", "sitemap-images.xml", "sitemap-videos.xml"):
        assert (tmp_path / name).is_file()
        assert f"Sitemap: https://example.com/site/{name}" in robots
