from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from repair_related_footers import missing_published_slugs, repair_rendered_article


def _payload() -> dict[str, object]:
    return {
        "slug": "creator-test",
        "title": "【画像】やんやんの競泳水着コスプレ",
        "tags": ["やんやん", "コスプレ", "競泳水着"],
        "main_subject": {"name": "やんやん", "role": "コスプレイヤー"},
        "images": [{"id": "image-1"}],
        "thumbnail_id": "image-1",
        "blocks": [
            {"id": "post", "type": "post", "text": "ええな", "style": "normal"},
            {
                "id": "profile",
                "type": "related_link",
                "url": "https://x.com/yanyan_cos",
                "title": "やんやんのX",
                "text": "本人の公式Xです。",
                "button_text": "Xで見る",
                "placement_label": "本人の公式アカウント",
                "provider": "x",
                "link_kind": "official_profile",
                "match_confidence": 98,
                "thumbnail_url": "https://pbs.twimg.com/profile_images/yanyan/avatar.jpg",
                "thumbnail_source_kind": "profile",
                "thumbnail_owner_url": "https://x.com/yanyan_cos",
            },
            {"id": "empty", "type": "ad", "text": "記事内容に合う関連広告枠"},
        ],
    }


def test_rendered_repair_fills_body_sidebar_and_moves_account_once() -> None:
    source = (
        '<html><head><style>.side-ad{min-height:106px}</style></head><body>'
        '<div class="thread"><aside class="article-destination" '
        'data-link-kind="official_profile" data-link-confidence="98">'
        '<a href="https://x.com/yanyan_cos">Xで見る</a></aside>'
        '<img class="zoomable" src="../assets/articles/creator-test/image-01.jpg">'
        '<div class="ad">PR<br>記事内容に合う関連広告枠</div>'
        '<div class="source">source note</div></div>'
        '<aside class="sidebar"><section class="sidebox"><h2 class="side-title">PR</h2>'
        '<div class="sidebody"><div class="side-ad">関連広告枠</div></div></section></aside>'
        '</body></html>'
    )

    updated, stats = repair_rendered_article(source, _payload(), "owner-test")

    assert "記事内容に合う関連広告枠" not in updated
    assert '<div class="side-ad">関連広告枠</div>' not in updated
    assert 'data-link-kind="inferred_topic_search"' in updated
    assert "af_id=owner-test" in updated
    assert "この記事が気に入った人向け" in updated
    assert updated.count("https://x.com/yanyan_cos") == 1
    assert 'class="side-ad side-ad-link fanza-product-button"' in updated
    assert updated.count('class="fanza-product-thumb"') == 2
    assert 'class="side-ad-link-thumb"' in updated
    assert updated.count("../assets/articles/creator-test/image-01.jpg") == 3
    assert updated.count("https://pbs.twimg.com/profile_images/yanyan/avatar.jpg") == 1
    assert stats["body_replacements"] == 1
    assert stats["sidebar_replacements"] == 1
    assert stats["original_profiles_replaced"] == 1

    second, second_stats = repair_rendered_article(updated, _payload(), "owner-test")
    assert second == updated
    assert second_stats["body_replacements"] == 0
    assert second_stats["sidebar_replacements"] == 0


def test_rendered_repair_does_not_repeat_exact_fanza_product_at_footer() -> None:
    payload = {
        "slug": "url-video-dmm-co-jp-product",
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
            {"id": "empty", "type": "ad", "text": "記事内容に合う関連広告枠"},
        ],
    }
    source = (
        '<html><head><style>.side-ad.side-ad-link{display:block}</style></head><body>'
        '<div class="thread">'
        '<img class="zoomable" '
        'src="../assets/articles/url-video-dmm-co-jp-product/image-01.jpg">'
        '<aside class="fanza-product" data-pr-id="fanza-media-product-1">本文の商品</aside>'
        '<aside class="article-destination fanza-product" '
        'data-link-kind="inferred_topic_search" '
        'data-pr-id="article-related-footer-recommendation">似た作品</aside>'
        '<div class="editorial-note">note</div></div>'
        '<aside class="sidebar"><section class="sidebox fanza-product">'
        '<h2 class="side-title">PR</h2><div class="sidebody">'
        '<a class="side-ad side-ad-link fanza-product-button">似た作品</a>'
        '</div></section></aside></body></html>'
    )

    updated, stats = repair_rendered_article(source, payload, "owner-test")

    assert 'data-pr-id="article-related-footer-product"' not in updated
    assert "この記事で紹介している作品 / PR" not in updated
    assert 'data-link-kind="inferred_topic_search"' not in updated
    assert "似た作品" not in updated
    assert updated.count('class="side-ad-link-thumb"') == 0
    assert stats["footer_cards_replaced"] == 1
    assert stats["sidebar_replacements"] == 1

    second, _ = repair_rendered_article(updated, payload, "owner-test")
    assert second == updated


def test_rendered_repair_removes_incidental_legacy_mgs_work() -> None:
    wrong_url = "https://www.mgstage.com/product/product_detail/892OERO-006/"
    payload = {
        "slug": "url-bakufu-jp-person",
        "source_url": "https://bakufu.jp/archives/1172727",
        "title": "【画像】希望みうの水着グラビア",
        "tags": ["希望みう", "水着", "グラビア"],
        "images": [{"id": "source-image-1"}],
        "thumbnail_id": "source-image-1",
        "affiliate_opportunities": [{
            "program_id": "mgs",
            "product_code": "892OERO-006",
            "product_url": wrong_url,
        }],
        "blocks": [
            {"id": "lead", "type": "images", "image_ids": ["source-image-1"]},
            {
                "id": "wrong-mgs",
                "type": "related_link",
                "url": wrong_url,
                "title": "MGS動画 892OERO-006",
                "link_kind": "exact_official_work",
            },
        ],
    }
    source = (
        '<html><body><div class="thread">'
        '<img class="zoomable" src="../assets/articles/url-bakufu-jp-person/image-01.jpg">'
        '<aside class="article-destination" data-link-kind="exact_official_work">'
        f'<a href="{wrong_url}">MGS動画 892OERO-006</a></aside>'
        '<div class="editorial-note">note</div></div></body></html>'
    )

    updated, _stats = repair_rendered_article(source, payload, "owner-test")

    assert wrong_url not in updated


def test_missing_published_slugs_only_returns_published_gaps(tmp_path: Path) -> None:
    draft_root = tmp_path / ".article-studio" / "drafts"
    article_root = tmp_path / "articles"
    draft_root.mkdir(parents=True)
    article_root.mkdir()
    for slug, status in (
        ("published-missing", "published"),
        ("draft-missing", "draft"),
        ("published-present", "published"),
    ):
        (draft_root / f"{slug}.json").write_text(
            json.dumps({"slug": slug, "status": status}), encoding="utf-8"
        )
    (article_root / "published-present.html").write_text("ok", encoding="utf-8")

    assert missing_published_slugs(tmp_path) == {"published-missing"}
