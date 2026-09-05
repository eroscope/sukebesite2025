from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.related_links import (
    apply_official_social_destinations,
    ensure_related_footer,
    is_empty_related_ad,
    resolve_article_destination,
    resolve_article_destinations,
    sanitize_related_destinations,
)


def test_social_repair_updates_existing_profile_card_thumbnail() -> None:
    payload = {
        "blocks": [{
            "id": "profile",
            "type": "related_link",
            "url": "https://www.instagram.com/yanyan_cos/",
            "title": "紹介した人物のInstagram",
            "provider": "instagram",
            "link_kind": "official_profile",
            "thumbnail_image_id": "article-image-1",
        }],
    }
    profiles = [{
        "name": "やんやん",
        "service": "instagram",
        "url": "https://www.instagram.com/yanyan_cos/",
        "is_main_subject": True,
        "thumbnail_url": "https://linktr.ee/og/image/yanyan.jpg",
        "thumbnail_source_kind": "official_hub_profile",
        "thumbnail_owner_url": "https://linktr.ee/yanyan_cos",
    }]

    assert apply_official_social_destinations(payload, profiles) is True

    card = payload["blocks"][0]
    assert card["title"] == "やんやんのInstagram"
    assert card["thumbnail_source_kind"] == "official_hub_profile"
    assert card["thumbnail_owner_url"] == "https://linktr.ee/yanyan_cos"
    assert "thumbnail_image_id" not in card


def test_social_repair_drops_local_thumbnail_owned_by_a_different_page() -> None:
    payload = {
        "images": [{
            "id": "wrong-profile",
            "related_thumbnail_only": True,
            "thumbnail_owner_url": "https://x.com/example",
        }],
        "blocks": [{
            "id": "profile",
            "type": "related_link",
            "url": "https://www.instagram.com/example/",
            "title": "本人のInstagram",
            "provider": "instagram",
            "link_kind": "official_profile",
            "thumbnail_image_id": "wrong-profile",
            "thumbnail_source_kind": "profile",
            "thumbnail_owner_url": "https://www.instagram.com/example/",
        }],
    }
    profiles = [{
        "name": "本人",
        "service": "instagram",
        "url": "https://www.instagram.com/example/",
        "is_main_subject": True,
    }]

    assert apply_official_social_destinations(payload, profiles) is True
    assert "thumbnail_image_id" not in payload["blocks"][0]


def test_related_footer_replaces_empty_ad_and_repeats_verified_account() -> None:
    payload = {
        "slug": "yanyan-footer-test",
        "title": "【画像17枚】やんやんの競泳水着コスプレ",
        "tags": ["やんやん", "コスプレ", "競泳水着", "タイツ"],
        "main_subject": {"name": "やんやん", "role": "コスプレイヤー"},
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [
            {"id": "lead", "type": "post", "text": "これ見て", "style": "large"},
            {"id": "media", "type": "images", "image_ids": ["image-1"]},
            {
                "id": "verified-yanyan-x",
                "type": "related_link",
                "url": "https://x.com/yanyan_cos",
                "title": "やんやんのX",
                "text": "本人の公式Xです。",
                "button_text": "Xで見る",
                "placement_label": "本人の公式アカウント",
                "provider": "x",
                "link_kind": "official_profile",
                "match_evidence": "確認済み人物名簿",
                "match_confidence": 98,
                "thumbnail_url": "https://pbs.twimg.com/profile_images/yanyan.jpg",
                "thumbnail_source_kind": "profile",
                "thumbnail_owner_url": "https://x.com/yanyan_cos",
            },
            {"id": "reply", "type": "post", "text": "衣装ええな", "style": "normal"},
            {"id": "empty-pr", "type": "ad", "text": "記事内容に合う関連広告枠"},
        ],
    }

    assert ensure_related_footer(payload) is True

    assert not any(is_empty_related_ad(block) for block in payload["blocks"])
    recommendation = payload["blocks"][-2]
    footer_profile = payload["blocks"][-1]
    assert recommendation["link_kind"] == "inferred_topic_search"
    assert "コスプレ" in unquote(recommendation["url"])
    assert "やんやん" not in unquote(recommendation["url"])
    assert not recommendation.get("thumbnail_image_id")
    assert not recommendation.get("thumbnail_url")
    assert footer_profile["link_kind"] == "official_profile"
    assert footer_profile["url"] == "https://x.com/yanyan_cos"
    assert "この記事が気に入った人向け" in footer_profile["text"]
    assert not footer_profile.get("thumbnail_image_id")
    assert footer_profile["thumbnail_url"] == "https://pbs.twimg.com/profile_images/yanyan.jpg"
    assert sum(
        block.get("url") == "https://x.com/yanyan_cos"
        for block in payload["blocks"]
        if isinstance(block, dict)
    ) == 1
    assert ensure_related_footer(payload) is False
    assert sum(
        block.get("url") == "https://x.com/yanyan_cos"
        for block in payload["blocks"]
        if isinstance(block, dict)
    ) == 1


def test_exact_fanza_product_replaces_generic_footer_recommendation() -> None:
    payload = {
        "slug": "url-video-dmm-co-jp-exact-test",
        "source_url": "https://video.dmm.co.jp/av/content/?id=jur00071",
        "content_mode": "fanza_product",
        "title": "【画像＆動画】矢埜愛茉 jur00071",
        "tags": ["矢埜愛茉", "jur00071", "人妻"],
        "images": [{"id": "source-image-1"}],
        "thumbnail_id": "source-image-1",
        "blocks": [
            {"id": "lead", "type": "images", "image_ids": ["source-image-1"]},
            {
                "id": "fanza-media-product-1",
                "type": "product_cta",
                "url": "https://video.dmm.co.jp/av/content/?id=jur00071",
                "title": "義父と同居して4年 矢埜愛茉",
                "text": "上の動画に対応する作品です。",
                "button_text": "FANZAでこの作品を見る",
                "thumbnail_image_id": "source-image-1",
                "placement_label": "この動画の商品",
                "match_type": "exact_video",
                "match_confidence": 100,
            },
            {"id": "empty-pr", "type": "ad", "text": "記事内容に合う関連広告枠"},
        ],
    }

    assert ensure_related_footer(payload) is True

    products = [
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("type") == "product_cta"
    ]
    assert [block["id"] for block in products] == ["fanza-media-product-1"]
    assert not any(
        isinstance(block, dict)
        and block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
    )
    assert ensure_related_footer(payload) is False


def test_exact_fanza_product_suppresses_generic_but_keeps_verified_performer() -> None:
    performer_url = "https://video.dmm.co.jp/av/list/?actress=1109954"
    payload = {
        "slug": "url-video-dmm-co-jp-hakata-test",
        "source_url": "https://video.dmm.co.jp/av/content/?id=sivr00503",
        "content_mode": "fanza_product",
        "suppress_generic_related_recommendation": True,
        "title": "博多彩葉のVR作品",
        "tags": ["博多彩葉", "VR"],
        "images": [{"id": "source-image-1"}],
        "thumbnail_id": "source-image-1",
        "blocks": [
            {"id": "lead", "type": "images", "image_ids": ["source-image-1"]},
            {
                "id": "fanza-media-product-1",
                "type": "product_cta",
                "url": "https://video.dmm.co.jp/av/content/?id=sivr00503",
                "title": "博多彩葉 VR SEX 解禁",
                "match_type": "exact_image",
                "match_confidence": 100,
            },
            {
                "id": "article-related-destination-1",
                "type": "related_link",
                "url": performer_url,
                "title": "博多彩葉の出演作品",
                "link_kind": "verified_person_search",
                "provider": "fanza",
                "match_confidence": 85,
            },
            {
                "id": "generic",
                "type": "related_link",
                "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=VR",
                "title": "VR系の作品を探す",
                "link_kind": "inferred_topic_search",
                "provider": "fanza",
            },
        ],
    }

    assert ensure_related_footer(payload) is True
    assert any(
        block.get("url") == performer_url
        and block.get("link_kind") == "verified_person_search"
        for block in payload["blocks"]
        if isinstance(block, dict)
    )
    assert not any(
        block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
        if isinstance(block, dict)
    )


def test_fanza_performer_gets_a_catalogue_even_without_a_social_profile() -> None:
    payload = {
        "slug": "fanza-performer-footer",
        "source_url": "https://video.dmm.co.jp/av/content/?id=abc001",
        "content_mode": "fanza_product",
        "main_subject": {
            "kind": "person", "name": "出演者名", "role": "本作の出演者",
            "is_public_creator": False,
        },
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [{
            "id": "exact", "type": "product_cta",
            "url": "https://video.dmm.co.jp/av/content/?id=abc001",
            "match_type": "exact_image",
        }],
    }

    assert ensure_related_footer(payload) is True
    performer = next(
        block for block in payload["blocks"]
        if isinstance(block, dict)
        and block.get("link_kind") == "verified_person_search"
    )
    assert performer["title"] == "出演者名の出演作品"
    assert "出演者名" in unquote(performer["url"])
    assert sum(
        block.get("type") == "product_cta"
        for block in payload["blocks"] if isinstance(block, dict)
    ) == 1


def test_unresolved_fanza_performer_is_not_reintroduced_during_rebuild() -> None:
    payload = {
        "slug": "unresolved-fanza-performer-footer",
        "main_subject": {
            "kind": "person", "name": "出演者名", "role": "AV女優",
            "is_public_creator": True,
        },
        "unresolved_fanza_performer_names": ["出演者名"],
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [],
        "tags": ["ランジェリー"],
    }

    assert ensure_related_footer(payload) is True
    assert not any(
        isinstance(block, dict)
        and block.get("link_kind") == "verified_person_search"
        for block in payload["blocks"]
    )
    assert any(
        isinstance(block, dict)
        and block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
    )


def test_public_person_without_a_verified_profile_gets_an_honest_search() -> None:
    payload = {
        "slug": "public-person-footer",
        "main_subject": {
            "kind": "person", "name": "公開活動者", "role": "グラビアアイドル",
            "is_public_creator": True,
        },
        "images": [{"id": "image-1"}],
        "blocks": [{"id": "lead", "type": "images", "image_ids": ["image-1"]}],
    }

    assert ensure_related_footer(payload) is True
    search = next(
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("link_kind") == "person_search"
    )
    assert search["title"] == "公開活動者の公式情報を探す"
    assert "公開活動者 公式" in unquote(search["url"])


def test_public_official_search_survives_with_a_topic_recommendation() -> None:
    payload = {
        "slug": "public-person-topic-footer",
        "title": "【画像】公開活動者の黒バニーグラビア",
        "tags": ["公開活動者", "黒バニー", "グラビア"],
        "main_subject": {
            "kind": "person", "name": "公開活動者", "role": "グラビアアイドル",
            "is_public_creator": True,
        },
        "images": [{"id": "image-1"}],
        "blocks": [{"id": "lead", "type": "images", "image_ids": ["image-1"]}],
    }

    assert ensure_related_footer(payload) is True
    recommendations = [
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("type") == "related_link"
    ]
    assert any(
        block.get("id") == "article-related-destination-person"
        and block.get("link_kind") == "person_search"
        for block in recommendations
    )
    assert any(
        block.get("link_kind") == "inferred_topic_search"
        for block in recommendations
    )


def test_official_accounts_are_moved_after_the_recommendation_without_duplicates() -> None:
    x_url = "https://x.com/example_creator"
    youtube_url = "https://www.youtube.com/@example_creator"
    payload = {
        "slug": "account-footer-order",
        "title": "【画像】公開活動者の黒バニーグラビア",
        "tags": ["黒バニー", "グラビア"],
        "images": [{"id": "image-1"}],
        "blocks": [
            {
                "id": "old-x", "type": "related_link", "url": x_url,
                "title": "公開活動者のX", "provider": "x",
                "link_kind": "official_profile",
            },
            {
                "id": "old-youtube", "type": "related_link", "url": youtube_url,
                "title": "公開活動者のYouTube", "provider": "youtube",
                "link_kind": "official_content",
            },
            {"id": "lead", "type": "images", "image_ids": ["image-1"]},
        ],
    }

    assert ensure_related_footer(payload) is True
    blocks = [block for block in payload["blocks"] if isinstance(block, dict)]
    assert sum(block.get("url") == x_url for block in blocks) == 1
    assert sum(block.get("url") == youtube_url for block in blocks) == 1
    recommendation_index = next(
        index for index, block in enumerate(blocks)
        if block.get("link_kind") == "inferred_topic_search"
    )
    account_indexes = [
        index for index, block in enumerate(blocks)
        if block.get("link_kind") in {"official_profile", "official_content"}
    ]
    assert account_indexes and min(account_indexes) > recommendation_index


def test_official_profile_wins_over_a_post_from_the_same_service() -> None:
    profile_url = "https://www.instagram.com/example_creator/"
    post_url = "https://www.instagram.com/p/EXAMPLE/"
    payload = {
        "slug": "profile-over-post",
        "title": "【画像】公開活動者のグラビア",
        "tags": ["グラビア"],
        "images": [{"id": "image-1"}],
        "blocks": [
            {
                "id": "instagram-post", "type": "related_link", "url": post_url,
                "title": "Instagram投稿", "provider": "instagram",
                "link_kind": "official_content",
            },
            {
                "id": "instagram-profile", "type": "related_link", "url": profile_url,
                "title": "Instagram", "provider": "instagram",
                "link_kind": "official_profile",
            },
            {"id": "lead", "type": "images", "image_ids": ["image-1"]},
        ],
    }

    ensure_related_footer(payload)
    instagram_cards = [
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("provider") == "instagram"
    ]
    assert len(instagram_cards) == 1
    assert instagram_cards[0]["url"] == profile_url
    assert instagram_cards[0]["link_kind"] == "official_profile"


def test_private_article_subject_does_not_get_an_invented_person_search() -> None:
    payload = {
        "slug": "private-person-footer",
        "main_subject": {
            "kind": "person", "name": "投稿者の奥さん", "role": "自称44歳の奥さん",
            "is_public_creator": False,
        },
        "images": [{"id": "image-1"}],
        "blocks": [{"id": "lead", "type": "images", "image_ids": ["image-1"]}],
    }

    ensure_related_footer(payload)
    assert not any(
        block.get("link_kind") in {"person_search", "verified_person_search"}
        for block in payload["blocks"] if isinstance(block, dict)
    )


def test_x_post_routes_to_the_author_profile() -> None:
    result = resolve_article_destination(
        {"slug": "x-test", "title": "投稿紹介", "tags": ["SNS"]},
        {
            "source_type": "x_post",
            "requested_url": "https://x.com/Test_User/status/1900000000000000001",
            "x_info": {"username": "Test_User"},
        },
        [],
    )

    assert result is not None
    assert result["url"] == "https://x.com/Test_User"
    assert result["link_kind"] == "official_profile"
    assert result["affiliate_eligible"] is False


def test_youtube_article_routes_to_the_original_youtube_page() -> None:
    result = resolve_article_destination(
        {"slug": "youtube-test", "title": "動画紹介", "tags": ["動画"]},
        {
            "source_type": "youtube",
            "requested_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "title": "本人の公開動画",
        },
        [],
    )

    assert result is not None
    assert result["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert result["link_kind"] == "official_content"


def test_named_work_uses_verified_official_page_without_generic_fanza_ranking() -> None:
    payload = {
        "slug": "verified-comic",
        "title": "【画像】ゾンビ漫画の紹介",
        "tags": ["漫画"],
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [{"id": "media", "type": "images", "image_ids": ["image-1"]}],
    }
    source = {
        "official_work_required": True,
        "verified_work_destinations": [{
            "url": "https://comic-ragchew.jp/comics/zombietokiko/",
            "title": "ゾンビのあふれた世界で俺だけが襲われない 時子 IF STORY",
            "provider": "COMICらぐちゅう",
            "reason": "出版社公式ページで作品名を確認",
            "confidence": 98,
        }],
    }

    results = resolve_article_destinations(payload, source, [])

    assert len(results) == 1
    assert results[0]["link_kind"] == "exact_official_work"
    assert not results[0].get("thumbnail_image_id")
    payload["blocks"].append(results[0])
    ensure_related_footer(payload)
    assert not any(
        block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
        if isinstance(block, dict)
    )


def test_unverified_named_work_does_not_fall_back_to_fanza_ranking() -> None:
    payload = {
        "slug": "unverified-comic",
        "title": "【画像】作品名が判明している漫画",
        "tags": ["漫画"],
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [{"id": "media", "type": "images", "image_ids": ["image-1"]}],
    }

    results = resolve_article_destinations(
        payload, {"official_work_required": True}, []
    )
    ensure_related_footer(payload)

    assert results == []
    assert payload["suppress_generic_related_recommendation"] is True
    assert not any(
        block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
        if isinstance(block, dict)
    )


def test_verified_av_performer_routes_to_a_named_fanza_search() -> None:
    result = resolve_article_destination(
        {"slug": "performer-test", "title": "出演作紹介", "tags": ["AV"]},
        {
            "url": "https://example.com/article",
            "ai_fanza_people": [{"name": "宮下玲奈", "reason": "出演者表記"}],
        },
        [],
    )

    assert result is not None
    assert "searchstr=%E5%AE%AE%E4%B8%8B%E7%8E%B2%E5%A5%88" in result["url"]
    assert result["link_kind"] == "verified_person_search"
    assert result["affiliate_network"] == "fanza"


def test_gravure_creator_uses_one_relevant_fanza_topic_beside_official_profiles() -> None:
    result = resolve_article_destination(
        {
            "slug": "hongo-yuzuha",
            "title": "本郷柚巴の黒バニーグラビア",
            "summary": "白ビキニや黒バニー姿を紹介。",
            "tags": ["本郷柚巴", "グラビア", "黒バニー"],
            "main_subject": {
                "name": "本郷柚巴",
                "kind": "person",
                "role": "グラビアアイドル",
                "is_public_creator": True,
            },
        },
        {"title": "本郷柚巴のグラビア"},
        [],
    )

    assert result is not None
    assert result["link_kind"] == "inferred_topic_search"
    assert result["title"] == "バニー系の作品を探す"
    assert "searchstr=%E3%83%90%E3%83%8B%E3%83%BC" in result["url"]


def test_saved_gravure_goods_search_is_migrated_to_one_fanza_topic() -> None:
    payload = {
        "slug": "hongo-yuzuha-saved",
        "title": "本郷柚巴の黒バニーグラビア",
        "summary": "白ビキニや黒バニー姿を紹介。",
        "tags": ["本郷柚巴", "グラビア", "黒バニー"],
        "main_subject": {
            "name": "本郷柚巴",
            "kind": "person",
            "role": "グラビアアイドル",
            "is_public_creator": True,
        },
        "blocks": [{
            "id": "article-related-destination-1",
            "type": "related_link",
            "url": "https://www.google.com/search?q=%E6%9C%AC%E9%83%B7%E6%9F%9A%E5%B7%B4%20%E5%85%AC%E5%BC%8F%20%E3%82%B0%E3%83%83%E3%82%BA",
            "title": "本郷柚巴の公式グッズを探す",
            "provider": "web_search",
            "link_kind": "person_search",
            "match_confidence": 60,
        }],
    }

    assert ensure_related_footer(payload) is True
    recommendation = next(
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("link_kind") == "inferred_topic_search"
    )
    assert recommendation["title"] == "バニー系の作品を探す"
    assert "searchstr=%E3%83%90%E3%83%8B%E3%83%BC" in recommendation["url"]


def test_subscription_profile_uses_its_own_thumbnail() -> None:
    results = resolve_article_destinations(
        {"slug": "creator-links", "title": "配信者の記事", "tags": ["配信者"]},
        {
            "url": "https://example.com/article",
            "ai_social_profiles": [{
                "name": "配信者A",
                "service": "myfans",
                "url": "https://myfans.jp/c/creator-a",
                "thumbnail_url": "https://cdn.myfans.jp/creator-a/avatar.jpg",
                "is_main_subject": True,
                "reason": "本人の公式リンク集で確認",
                "confidence": 96,
            }],
        },
        [],
    )

    assert results[0]["provider"] == "myfans"
    assert results[0]["thumbnail_source_kind"] == "profile"
    assert results[0]["thumbnail_owner_url"] == "https://myfans.jp/c/creator-a"
    assert results[0]["thumbnail_url"] == "https://cdn.myfans.jp/creator-a/avatar.jpg"


def test_unknown_person_uses_topic_search_without_claiming_an_exact_work() -> None:
    result = resolve_article_destination(
        {
            "slug": "unknown-test",
            "title": "【画像】正体不明の素人女性",
            "tags": ["画像", "制服", "巨乳", "成人向け"],
        },
        {"url": "https://example.com/article"},
        [],
    )

    assert result is not None
    assert result["link_kind"] == "inferred_topic_search"
    assert unquote(result["url"]).endswith("searchstr=制服")
    assert result["title"] == "制服系の作品を探す"
    assert "同一作品ではありません" in result["text"]
    assert result["match_confidence"] == 40


def test_dolphin_shorts_use_a_fanza_searchable_clothing_term() -> None:
    result = resolve_article_destination(
        {
            "slug": "dolphin-shorts",
            "title": "ドルフィンパンツ姿の成人女性",
            "tags": ["ドルフィンパンツ", "成人女性"],
        },
        {"url": "https://example.com/article"},
        [],
    )

    assert result is not None
    assert unquote(result["url"]).endswith("searchstr=ブルマ")


def test_anatomy_topic_uses_a_fanza_searchable_act_term() -> None:
    result = resolve_article_destination(
        {
            "slug": "anatomy-topic",
            "title": "成人女性の女性器を近距離で見る",
            "tags": ["女性器", "成人女性"],
        },
        {"url": "https://example.com/article"},
        [],
    )

    assert result is not None
    assert unquote(result["url"]).endswith("searchstr=クンニ")


def test_source_author_is_not_treated_as_the_person_in_the_article() -> None:
    result = resolve_article_destination(
        {
            "slug": "url-himablo-xyz-674a90ad",
            "title": "【画像】水着とランジェリーが混ざる画像まとめ",
            "summary": "名前のない複数女性の水着・ランジェリー画像。",
            "tags": ["おっぱい", "グラビア", "水着", "ランジェリー", "画像まとめ"],
            "main_subject": {
                "name": "",
                "kind": "group",
                "is_public_creator": False,
            },
        },
        {
            "url": "https://himablo.xyz/example",
            "author": "etietidoga",
            "title": "素敵なおっぱい グラビア画像まとめ",
        },
        [],
    )

    assert result is not None
    assert result["link_kind"] == "inferred_topic_search"
    assert "etietidoga" not in unquote(result["url"])
    assert "ランジェリー" in unquote(result["url"])


def test_saved_author_person_search_is_replaced_when_subject_is_unidentified() -> None:
    old_url = "https://www.google.com/search?q=etietidoga%20公式%20グッズ"
    payload = {
        "slug": "url-himablo-xyz-674a90ad",
        "title": "【画像】水着とランジェリーが混ざる画像まとめ",
        "summary": "名前のない複数女性の水着・ランジェリー画像。",
        "tags": ["おっぱい", "グラビア", "水着", "ランジェリー", "画像まとめ"],
        "main_subject": {
            "name": "",
            "kind": "group",
            "is_public_creator": False,
        },
        "images": [{"id": "source-image-1"}],
        "thumbnail_id": "source-image-1",
        "blocks": [{
            "id": "article-related-destination-1",
            "type": "related_link",
            "url": old_url,
            "title": "etietidogaの公式グッズを探す",
            "text": "人物名から公式物販を探す検索ページです。",
            "button_text": "公式グッズを探す",
            "placement_label": "関連ページ",
            "provider": "web_search",
            "link_kind": "person_search",
            "match_confidence": 60,
        }],
        "related_destinations": [{
            "url": old_url,
            "title": "etietidogaの公式グッズを探す",
            "provider": "web_search",
            "link_kind": "person_search",
            "match_confidence": 60,
        }],
        "related_footer_version": 3,
    }

    assert ensure_related_footer(payload) is True

    recommendations = [
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("type") == "related_link"
    ]
    assert len(recommendations) == 1
    assert recommendations[0]["link_kind"] == "inferred_topic_search"
    assert "etietidoga" not in recommendations[0]["title"]
    assert "etietidoga" not in unquote(recommendations[0]["url"])
    assert payload["related_footer_version"] == 9
    assert all(
        item.get("link_kind") != "person_search"
        for item in payload["related_destinations"]
    )


def test_saved_author_person_search_must_match_the_verified_subject() -> None:
    payload = {
        "slug": "identified-subject-test",
        "title": "【画像】井口裕香の水着グラビア",
        "tags": ["井口裕香", "グラビア", "水着"],
        "main_subject": {
            "name": "井口裕香",
            "kind": "person",
            "is_public_creator": True,
        },
        "blocks": [{
            "id": "article-related-destination-1",
            "type": "related_link",
            "url": "https://www.google.com/search?q=etietidoga%20公式%20グッズ",
            "title": "etietidogaの公式グッズを探す",
            "provider": "web_search",
            "link_kind": "person_search",
            "match_confidence": 60,
        }],
        "related_destinations": [{
            "url": "https://www.google.com/search?q=etietidoga%20公式%20グッズ",
            "title": "etietidogaの公式グッズを探す",
            "provider": "web_search",
            "link_kind": "person_search",
            "match_confidence": 60,
        }],
        "related_footer_version": 3,
    }

    assert ensure_related_footer(payload) is True
    assert all(
        "etietidoga" not in str(block.get("title") or "")
        for block in payload["blocks"]
        if isinstance(block, dict)
    )
    assert all(
        "etietidoga" not in str(item.get("title") or "")
        for item in payload["related_destinations"]
    )


def test_topic_search_never_turns_person_names_or_format_tags_into_a_work() -> None:
    result = resolve_article_destination(
        {
            "slug": "url-chaos-giga-com-1e95832d",
            "title": "【画像2枚】デリヘル嬢の投稿、長瀬智也の名前まで出てきて展開が急すぎる",
            "summary": "バッグの投稿とベッド上の下着姿を紹介。",
            "tags": ["デリヘル", "投稿画像", "長瀬智也", "下着", "東京ドリーム"],
        },
        {"url": "https://chaos-giga.com/archives/example"},
        [],
    )

    assert result is not None
    decoded_url = unquote(result["url"])
    assert result["title"] == "デリヘル系の作品を探す"
    assert decoded_url.endswith("searchstr=デリヘル")
    assert "長瀬智也" not in decoded_url
    assert "投稿画像" not in decoded_url
    assert "東京ドリーム" not in decoded_url
    assert "長瀬智也" not in result["title"]
    assert "同一作品ではありません" in result["text"]


def test_related_footer_replaces_a_saved_unsafe_topic_search() -> None:
    payload = {
        "slug": "url-chaos-giga-com-1e95832d",
        "title": "【画像2枚】デリヘル嬢の投稿、長瀬智也の名前まで出てきて展開が急すぎる",
        "summary": "バッグの投稿とベッド上の下着姿を紹介。",
        "tags": ["デリヘル", "投稿画像", "長瀬智也", "下着", "東京ドリーム"],
        "images": [{"id": "source-image-1"}],
        "thumbnail_id": "source-image-1",
        "blocks": [{
            "id": "article-related-destination-1",
            "type": "related_link",
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr="
            "%E3%83%87%E3%83%AA%E3%83%98%E3%83%AB+%E6%8A%95%E7%A8%BF%E7%94%BB%E5%83%8F+"
            "%E9%95%B7%E7%80%AC%E6%99%BA%E4%B9%9F+%E4%B8%8B%E7%9D%80",
            "title": "「デリヘル 投稿画像 長瀬智也 下着」に近い作品",
            "text": "人物や作品を特定できなかったため、記事の題材から作った検索です。",
            "button_text": "関連作品をFANZAで見る",
            "placement_label": "記事内容に近い関連作品",
            "provider": "fanza",
            "link_kind": "inferred_topic_search",
            "match_confidence": 45,
            "affiliate_network": "fanza",
            "affiliate_eligible": True,
        }],
        "related_destinations": [{
            "url": "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr="
            "%E3%83%87%E3%83%AA%E3%83%98%E3%83%AB+%E6%8A%95%E7%A8%BF%E7%94%BB%E5%83%8F+"
            "%E9%95%B7%E7%80%AC%E6%99%BA%E4%B9%9F+%E4%B8%8B%E7%9D%80",
            "title": "「デリヘル 投稿画像 長瀬智也 下着」に近い作品",
            "provider": "fanza",
            "link_kind": "inferred_topic_search",
            "match_confidence": 45,
        }],
        "related_footer_version": 2,
    }

    assert ensure_related_footer(payload) is True

    recommendation = payload["blocks"][-1]
    decoded_url = unquote(recommendation["url"])
    assert recommendation["title"] == "デリヘル系の作品を探す"
    assert "長瀬智也" not in decoded_url
    assert "投稿画像" not in decoded_url
    assert "東京ドリーム" not in decoded_url
    assert payload["related_footer_version"] == 9
    assert payload["related_destinations"] == [{
        "url": recommendation["url"],
        "title": recommendation["title"],
        "provider": "fanza",
        "link_kind": "inferred_topic_search",
        "match_confidence": 40,
    }]


def test_tiktoker_profile_is_kept_before_an_exact_product_destination() -> None:
    results = resolve_article_destinations(
        {
            "slug": "tiktoker-test",
            "title": "TikToker みおの投稿を紹介",
            "tags": ["TikToker", "みお"],
        },
        {
            "url": "https://example.com/mio",
            "title": "TikToker みおの画像",
            "links": [{
                "url": "https://www.tiktok.com/@mio_sample/video/1234567890",
                "text": "TikTok",
            }],
        },
        [{
            "product_url": "https://video.dmm.co.jp/av/content/?id=sample001",
            "product_code": "sample001",
            "program_name": "FANZA",
            "program_id": "fanza",
            "reason": "記事下の関連作品",
            "confidence": 80,
        }],
    )

    assert [item["provider"] for item in results] == ["tiktok", "fanza"]
    assert results[0]["url"] == "https://www.tiktok.com/@mio_sample"
    assert results[0]["link_kind"] == "official_profile"
    assert results[0]["placement_label"] == "本人の公式アカウント"
    assert results[1]["link_kind"] == "exact_official_work"


def test_exact_work_also_keeps_the_verified_performers_other_works() -> None:
    results = resolve_article_destinations(
        {
            "slug": "performer-exact-work",
            "title": "百田光稀の作品紹介",
            "tags": ["百田光稀"],
        },
        {
            "url": "https://example.com/mitsuki",
            "ai_fanza_people": [{"name": "百田光稀", "reason": "出演者表記"}],
        },
        [{
            "product_url": "https://video.dmm.co.jp/av/content/?id=exact001",
            "product_code": "exact001",
            "program_name": "FANZA",
            "program_id": "fanza",
            "reason": "作品番号を確認",
            "confidence": 100,
        }],
    )

    assert [item["link_kind"] for item in results] == [
        "exact_official_work",
        "verified_person_search",
    ]
    assert "searchstr=%E7%99%BE%E7%94%B0%E5%85%89%E7%A8%80" in results[1]["url"]


def test_direct_fanza_product_prefers_its_official_performer_page() -> None:
    results = resolve_article_destinations(
        {
            "slug": "hakata-iroha",
            "title": "博多彩葉の作品紹介",
            "tags": ["博多彩葉"],
        },
        {
            "url": "https://video.dmm.co.jp/av/content/?id=sivr00503",
            "ai_fanza_people": [{"name": "博多彩葉", "reason": "出演者欄"}],
            "fanza_performer_pages": [{
                "name": "博多彩葉",
                "url": "https://video.dmm.co.jp/av/list/?actress=1109954",
            }],
        },
        [{
            "product_url": "https://video.dmm.co.jp/av/content/?id=sivr00503",
            "product_code": "sivr00503",
            "program_name": "FANZA",
            "program_id": "fanza",
            "reason": "同じ商品ID",
            "confidence": 100,
        }],
    )

    performer = next(item for item in results if item["link_kind"] == "verified_person_search")
    assert performer["url"] == "https://video.dmm.co.jp/av/list/?actress=1109954"


def test_direct_fanza_product_keeps_performer_page_when_exact_cta_is_already_embedded() -> None:
    results = resolve_article_destinations(
        {
            "slug": "hakata-iroha",
            "title": "博多彩葉の作品紹介",
            "tags": ["博多彩葉"],
        },
        {
            "url": "https://video.dmm.co.jp/av/content/?id=sivr00503",
            "official_work_required": True,
            "ai_fanza_people": [{"name": "博多彩葉", "reason": "出演者欄"}],
            "fanza_performer_pages": [{
                "name": "博多彩葉",
                "url": "https://video.dmm.co.jp/av/list/?actress=1109954",
            }],
        },
        [],
    )

    assert [item["link_kind"] for item in results] == ["verified_person_search"]
    assert results[0]["url"] == "https://video.dmm.co.jp/av/list/?actress=1109954"


def test_direct_fanza_product_keeps_every_verified_performer_page() -> None:
    results = resolve_article_destinations(
        {"slug": "two-performers", "title": "二人の出演作", "tags": []},
        {
            "url": "https://video.dmm.co.jp/av/content/?id=two001",
            "official_work_required": True,
            "fanza_people": [
                {"name": "黒咲華", "reason": "出演者欄"},
                {"name": "瀬那ルミナ", "reason": "出演者欄"},
            ],
            "fanza_performer_pages": [
                {
                    "name": "黒咲華",
                    "url": "https://video.dmm.co.jp/av/list/?actress=1094629",
                },
                {
                    "name": "瀬那ルミナ",
                    "url": "https://video.dmm.co.jp/av/list/?actress=1079245",
                },
            ],
        },
        [],
    )

    performer_cards = [
        item for item in results
        if item.get("link_kind") == "verified_person_search"
    ]
    assert [item["person_name"] for item in performer_cards] == [
        "黒咲華",
        "瀬那ルミナ",
    ]
    assert [item["url"] for item in performer_cards] == [
        "https://video.dmm.co.jp/av/list/?actress=1094629",
        "https://video.dmm.co.jp/av/list/?actress=1079245",
    ]


def test_related_footer_keeps_same_service_for_different_people() -> None:
    payload = {
        "slug": "two-social-profiles",
        "title": "二人の記事",
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [
            {"id": "media", "type": "images", "image_ids": ["image-1"]},
            {
                "id": "profile-a",
                "type": "related_link",
                "link_kind": "official_profile",
                "provider": "x",
                "person_name": "黒咲華",
                "title": "黒咲華のX",
                "url": "https://x.com/kurohana_gal",
            },
            {
                "id": "profile-b",
                "type": "related_link",
                "link_kind": "official_profile",
                "provider": "x",
                "person_name": "瀬那ルミナ",
                "title": "瀬那ルミナのX",
                "url": "https://x.com/senarumina",
            },
        ],
    }

    ensure_related_footer(payload)

    profile_urls = [
        block.get("url") for block in payload["blocks"]
        if isinstance(block, dict)
        and block.get("link_kind") == "official_profile"
    ]
    assert profile_urls == [
        "https://x.com/kurohana_gal",
        "https://x.com/senarumina",
    ]


def test_incidental_product_link_never_replaces_the_subject_profile() -> None:
    results = resolve_article_destinations(
        {
            "slug": "kotori-test",
            "title": "風愛ことりの水着グラビア",
            "tags": ["風愛ことり", "水着", "グラビア"],
        },
        {
            "url": "https://example.com/kotori",
            "links": [{"url": "https://x.com/kazame_kotori", "text": "風愛ことりのX"}],
        },
        [{
            "product_url": "https://www.mgstage.com/product/product_detail/ABF-379/",
            "product_code": "ABF-379",
            "program_name": "MGS動画",
            "program_id": "mgs",
            "article_match": False,
        }],
    )

    assert results[0]["url"] == "https://x.com/kazame_kotori"
    assert all("ABF-379" not in item["url"] for item in results)


def test_label_less_instagram_profile_is_treated_as_a_person_profile() -> None:
    results = resolve_article_destinations(
        {"slug": "creator-test", "title": "配信者の画像", "tags": ["配信者"]},
        {
            "url": "https://example.com/creator",
            "links": [{"url": "https://www.instagram.com/creator_name/", "text": ""}],
        },
        [],
    )

    assert results[0]["provider"] == "instagram"
    assert results[0]["url"] == "https://www.instagram.com/creator_name/"


def test_direct_tiktok_video_source_routes_back_to_the_creator_profile() -> None:
    results = resolve_article_destinations(
        {"slug": "direct-tiktok", "title": "TikTokerの動画", "tags": ["TikToker"]},
        {
            "requested_url": "https://www.tiktok.com/@creator.name/video/7654321",
            "title": "TikTok動画",
        },
        [],
    )

    assert results[0]["provider"] == "tiktok"
    assert results[0]["url"] == "https://www.tiktok.com/@creator.name"
    assert results[0]["link_kind"] == "official_profile"


def test_instagram_embed_is_kept_as_official_content_when_profile_is_unknown() -> None:
    results = resolve_article_destinations(
        {"slug": "instagram-post", "title": "インフルエンサーの投稿", "tags": ["Instagram"]},
        {
            "url": "https://example.com/article",
            "links": [{
                "url": "https://www.instagram.com/p/AbCdEf12/embed/?v=14",
                "text": "",
            }],
        },
        [],
    )

    assert results[0]["provider"] == "instagram"
    assert results[0]["url"] == "https://www.instagram.com/p/AbCdEf12/"
    assert results[0]["link_kind"] == "official_content"


def test_x_share_intent_is_never_treated_as_a_person_profile() -> None:
    results = resolve_article_destinations(
        {"slug": "share-link", "title": "インフルエンサーの記事", "tags": ["SNS"]},
        {
            "url": "https://example.com/article",
            "links": [{"url": "https://x.com/intent/tweet?text=test", "text": "X"}],
        },
        [],
    )

    assert all(item.get("url") != "https://x.com/intent" for item in results)
    assert all(item.get("provider") != "x" for item in results)


def test_x_intent_source_url_is_not_treated_as_a_person_profile() -> None:
    result = resolve_article_destination(
        {"slug": "intent-source", "title": "投稿紹介", "tags": ["SNS"]},
        {"requested_url": "https://twitter.com/intent/tweet?text=test"},
        [],
    )

    assert result is not None
    assert result["link_kind"] != "official_profile"


def test_sanitizer_removes_existing_x_intent_profile_only() -> None:
    payload = {
        "blocks": [
            {
                "type": "related_link",
                "url": "https://x.com/intent",
                "provider": "x",
                "link_kind": "official_profile",
            },
            {
                "type": "related_link",
                "url": "https://x.com/real_creator",
                "provider": "x",
                "link_kind": "official_profile",
            },
        ],
        "related_destinations": [
            {"url": "https://x.com/intent", "provider": "x"},
            {"url": "https://x.com/real_creator", "provider": "x"},
        ],
    }

    result = sanitize_related_destinations(payload)

    assert [block["url"] for block in result["blocks"]] == [
        "https://x.com/real_creator"
    ]
    assert [item["url"] for item in result["related_destinations"]] == [
        "https://x.com/real_creator"
    ]


def test_codex_verified_main_tiktoker_profile_is_used_with_the_person_name() -> None:
    results = resolve_article_destinations(
        {"slug": "main-creator", "title": "りりの投稿", "tags": ["TikToker"]},
        {
            "url": "https://example.com/article",
            "ai_social_profiles": [{
                "name": "りり",
                "service": "tiktok",
                "url": "https://www.tiktok.com/@riri_official/video/123456",
                "is_main_subject": True,
                "reason": "本文中の本人TikTokリンク",
            }],
        },
        [],
    )

    assert results[0]["title"] == "りりのTikTok"
    assert results[0]["url"] == "https://www.tiktok.com/@riri_official"
    assert results[0]["link_kind"] == "official_profile"


def test_registry_verified_cosplayer_profile_is_placed_before_topic_recommendation() -> None:
    results = resolve_article_destinations(
        {
            "slug": "yanyan-test",
            "title": "【画像17枚】やんやん、競泳水着で見せるコスプレ",
            "tags": ["やんやん", "コスプレ", "競泳水着"],
        },
        {
            "url": "https://bakufu.jp/archives/1171616",
            "verified_social_profiles": [{
                "name": "やんやん",
                "service": "x",
                "url": "https://x.com/yanyan_cos",
                "is_main_subject": True,
                "reason": "公式リンク集と独立プロフィールの2系統で照合",
                "confidence": 98,
            }],
        },
        [],
    )

    assert results[0]["title"] == "やんやんのX"
    assert results[0]["url"] == "https://x.com/yanyan_cos"
    assert results[0]["match_confidence"] == 98
    assert results[0]["link_kind"] == "official_profile"


def test_legacy_incidental_mgs_products_are_removed_from_person_article() -> None:
    product_url = "https://www.mgstage.com/product/product_detail/892OERO-006/"
    payload = {
        "source_url": "https://bakufu.jp/archives/1172727",
        "affiliate_opportunities": [{
            "program_id": "mgs",
            "product_code": "892OERO-006",
            "product_url": product_url,
        }],
        "blocks": [
            {
                "id": "wrong-mgs",
                "type": "related_link",
                "url": product_url,
                "title": "MGS動画 892OERO-006",
            },
            {
                "id": "profile",
                "type": "related_link",
                "url": "https://x.com/miumiu__mirai",
                "title": "希望みうのX",
                "provider": "x",
                "link_kind": "official_profile",
            },
        ],
        "related_destinations": [
            {"url": product_url, "link_kind": "exact_official_work"},
            {"url": "https://x.com/miumiu__mirai", "link_kind": "official_profile"},
        ],
    }

    result = sanitize_related_destinations(payload)

    assert [block["id"] for block in result["blocks"]] == ["profile"]
    assert [item["url"] for item in result["related_destinations"]] == [
        "https://x.com/miumiu__mirai"
    ]


def test_direct_mgs_article_keeps_its_own_product_destination() -> None:
    product_url = "https://www.mgstage.com/product/product_detail/892OERO-006/"
    payload = {
        "source_url": product_url,
        "blocks": [{
            "id": "exact-mgs",
            "type": "related_link",
            "url": product_url,
            "title": "MGS動画 892OERO-006",
        }],
    }

    assert sanitize_related_destinations(payload) is payload


def test_exact_official_work_drops_article_image_thumbnail() -> None:
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = {
        "images": [{"id": "article-image", "rights_basis": "article_source"}],
        "blocks": [{
            "id": "official-work",
            "type": "related_link",
            "link_kind": "exact_official_work",
            "url": page_url,
            "thumbnail_image_id": "article-image",
        }],
    }

    result = sanitize_related_destinations(payload)

    assert "thumbnail_image_id" not in result["blocks"][0]


def test_exact_official_work_keeps_owned_official_page_thumbnail() -> None:
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = {
        "images": [{
            "id": "official-image",
            "related_thumbnail_only": True,
            "rights_basis": "official_page_thumbnail",
            "thumbnail_owner_url": page_url,
        }],
        "blocks": [{
            "id": "official-work",
            "type": "related_link",
            "link_kind": "exact_official_work",
            "url": page_url,
            "thumbnail_image_id": "official-image",
            "thumbnail_source_kind": "official_page",
            "thumbnail_owner_url": page_url,
        }],
    }

    assert sanitize_related_destinations(payload) is payload
