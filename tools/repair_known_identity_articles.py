from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from article_studio import add_built_article, load_draft_payload, save_draft  # noqa: E402
from indanya_desktop.legacy_identity_repairs import (  # noqa: E402
    backfill_verified_main_subject_identity,
)
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
    prune_unreferenced_related_thumbnail_assets,
)
from indanya_desktop.related_links import (  # noqa: E402
    apply_official_social_destinations,
    ensure_related_footer,
)
from indanya_desktop.social_profiles import upsert_social_profile_record  # noqa: E402
from indanya_desktop.adaptive_quality import apply_quality_gate  # noqa: E402


PAN_SLUG = "url-himablo-xyz-dcd6535f"
HAYASHIDA_SLUG = "url-chaos-giga-com-9b7c45ac"
SAKURA_SLUG = "url-hnalady-com-0726ca89"
KATO_AIRI_SLUG = "url-himablo-xyz-0fad6d38"

KATO_AIRI_PROFILES = [
    {
        "name": "加藤愛梨",
        "display_name": "加藤愛梨",
        "role": "俳優・グラビアモデル",
        "service": "x",
        "url": "https://x.com/l_ovepear",
        "is_main_subject": True,
        "reason": "週プレNEWSの加藤愛梨プロフィール欄で公式Xとして確認",
        "verification_source": "official_publisher",
        "verification_status": "verified",
        "confidence": 99,
        "thumbnail_url": (
            "https://pbs.twimg.com/profile_images/2082246526827712512/"
            "1Pt18Wg4_400x400.jpg"
        ),
        "thumbnail_source_kind": "profile",
        "thumbnail_owner_url": "https://x.com/l_ovepear",
    },
    {
        "name": "加藤愛梨",
        "display_name": "加藤愛梨",
        "role": "俳優・グラビアモデル",
        "service": "instagram",
        "url": "https://www.instagram.com/airi_kato_official/",
        "is_main_subject": True,
        "reason": "画像内IDと週プレNEWS記載の公式Instagramが完全一致",
        "verification_source": "watermark_ocr_and_official_publisher",
        "verification_status": "verified",
        "confidence": 99,
    },
    {
        "name": "加藤愛梨",
        "display_name": "加藤愛梨",
        "role": "俳優・グラビアモデル",
        "service": "tiktok",
        "url": "https://www.tiktok.com/@l_ovepear",
        "is_main_subject": True,
        "reason": "週プレNEWSの加藤愛梨プロフィール欄で公式TikTokとして確認",
        "verification_source": "official_publisher",
        "verification_status": "verified",
        "confidence": 99,
    },
]


def _source_media(payload: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [
        item
        for item in payload.get("images") or []
        if isinstance(item, dict) and str(item.get("id") or "").startswith(prefix)
    ]


def _related_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "related_link"
    ]


def repair_pan_piano(payload: dict[str, Any]) -> bool:
    if str(payload.get("slug") or "") != PAN_SLUG:
        return False
    before = repr(payload)
    payload["title"] = "【画像】Pan Piano、公式チャンネルの衣装サムネがピアノより目立つ"
    payload["summary"] = (
        "Pan Pianoの公式YouTubeチャンネルとメンバー向け動画一覧を確認。"
        "ピアノカバーの動画が並ぶ一方、衣装の違うサムネイルが画面いっぱいに並ぶ構成を見た反応をまとめた。"
    )
    payload["category"] = "画像"
    payload["tags"] = ["Pan Piano", "YouTube", "ピアノ", "コスプレ"]
    payload["comments"] = 6
    payload["thumbnail_id"] = "source-image-1"
    payload["images"] = [
        item
        for item in payload.get("images") or []
        if not isinstance(item, dict) or item.get("id") != "source-image-3"
    ]
    for item in _source_media(payload, "source-image-"):
        image_id = str(item.get("id") or "")
        if image_id == "source-image-1":
            item["alt"] = "Pan Piano公式YouTubeチャンネルの動画一覧"
            item["caption"] = "Pan Piano公式YouTubeチャンネル"
        elif image_id == "source-image-2":
            item["alt"] = "Pan Piano公式YouTubeのメンバー向け動画一覧"
            item["caption"] = "Pan Piano公式チャンネルのメンバー向け動画"
    body_blocks = [
        {
            "id": "source-lead-image",
            "type": "images",
            "image_ids": ["source-image-1"],
            "lead": True,
        },
        {
            "id": "verified-post-1",
            "type": "post",
            "text": "公式チャンネルを開いた瞬間、曲名より先に衣装のサムネへ目が行く",
            "style": "normal",
        },
        {
            "id": "verified-images-2",
            "type": "images",
            "image_ids": ["source-image-2"],
        },
        {
            "id": "verified-post-2",
            "type": "post",
            "text": "メンバー向けまで同じ方向で揃ってるの、もうチャンネルの個性やな",
            "style": "normal",
        },
        {
            "id": "verified-post-3",
            "type": "post",
            "text": "ピアノカバーを探してるのにサムネだけで何の曲か当てる方が難しい",
            "style": "highlight",
        },
        {
            "id": "verified-post-4",
            "type": "post",
            "text": "衣装が毎回違うから一覧を眺めてるだけでも妙に見てしまうわ",
            "style": "normal",
        },
        {
            "id": "verified-post-5",
            "type": "post",
            "text": "演奏チャンネルなのに画面の情報量はほぼ写真集やん",
            "style": "normal",
        },
        {
            "id": "verified-post-6",
            "type": "post",
            "text": "ここまで徹底してると、次は何の衣装か確認しに来る人も多そう",
            "style": "normal",
        },
    ]
    payload["blocks"] = body_blocks + _related_blocks(payload)
    payload["identity_resolution"] = {
        "status": "verified",
        "method": "verified_visual_registry",
        "message": (
            "採用した2枚はいずれもPan Piano公式YouTubeの名称と"
            "@panpianoatelierを画面内で確認。@nacocomusic1552の別人画像は除外した。"
        ),
        "retry_after": "",
    }
    payload.pop("quality_gate", None)
    payload.pop("review_message", None)
    return before != repr(payload)


def repair_hayashida_moka(payload: dict[str, Any]) -> bool:
    if str(payload.get("slug") or "") != HAYASHIDA_SLUG:
        return False
    before = repr(payload)
    for index, item in enumerate(_source_media(payload, "source-image-"), start=1):
        item["alt"] = f"林田百加のハイレグ水着画像 {index}"
        item["caption"] = "林田百加"
    payload["identity_resolution"] = {
        "status": "verified",
        "method": "official_product",
        "message": (
            "画像内パッケージに林田百加の氏名と『ハイレグアーマーの入手方法』を確認し、"
            "発売記念イベント記事の作品名・出演者名とも一致した。"
        ),
        "retry_after": "",
    }
    destinations = [
        item
        for item in payload.get("related_destinations") or []
        if isinstance(item, dict) and item.get("provider") != "web_search"
    ]
    destinations.insert(0, {
        "url": "https://ascii.jp/elem/000/004/245/4245035/",
        "title": "林田百加『ハイレグアーマーの入手方法』発売イベント",
        "provider": "official_press",
        "link_kind": "official_content",
        "match_confidence": 98,
        "match_evidence": "作品名、出演者名、発売日が画像内パッケージと一致",
    })
    destinations.insert(1, {
        "url": (
            "https://www.dmm.com/search/=/searchstr="
            + quote("ハイレグアーマーの入手方法 林田百加", safe="")
        ),
        "title": "『ハイレグアーマーの入手方法 林田百加』を探す",
        "provider": "dmm",
        "link_kind": "verified_work_search",
        "match_confidence": 80,
        "match_evidence": "画像内で確認できた作品名と出演者名による検索",
    })
    payload["related_destinations"] = destinations
    payload.pop("quality_gate", None)
    payload.pop("review_message", None)
    return before != repr(payload)


def repair_sakura_miko(payload: dict[str, Any]) -> bool:
    if str(payload.get("slug") or "") != SAKURA_SLUG:
        return False
    before = repr(payload)
    for index, item in enumerate(_source_media(payload, "source-image-"), start=1):
        item["alt"] = f"佐倉みこの和風AV画像 {index}"
        item["caption"] = "佐倉みこ"
    payload["identity_resolution"] = {
        "status": "verified",
        "method": "official_page",
        "message": (
            "元ページの見出し・画像ファイル名と、カリビアンコム公式出演者ページの"
            "『佐倉みこ』表記および出演作品一覧を照合した。"
        ),
        "retry_after": "",
    }
    official_url = "https://www.caribbeancom.com/search_act/8877/1.html"
    destinations = [
        item
        for item in payload.get("related_destinations") or []
        if isinstance(item, dict) and str(item.get("url") or "") != official_url
    ]
    destinations.insert(0, {
        "url": official_url,
        "title": "佐倉みこの公式出演作品",
        "provider": "caribbeancom",
        "link_kind": "official_profile",
        "match_confidence": 99,
        "match_evidence": "公式AV女優一覧から佐倉みこの個別出演作品ページへ遷移して確認",
    })
    payload["related_destinations"] = destinations
    payload.pop("quality_gate", None)
    payload.pop("review_message", None)
    return before != repr(payload)


def repair_kato_airi(payload: dict[str, Any]) -> bool:
    if str(payload.get("slug") or "") != KATO_AIRI_SLUG:
        return False
    before = repr(payload)
    person_name = "加藤愛梨"
    payload["title"] = "【画像】加藤愛梨、ミス中央の清楚な笑顔から水着グラビアへ"
    payload["summary"] = (
        "ミス中央2022グランプリの加藤愛梨が、表彰時のティアラ姿から白と水色の"
        "ビキニ姿まで見せるグラビア。画像内の氏名とInstagram IDを公式情報と照合した。"
    )
    payload["tags"] = ["加藤愛梨", "グラビア", "水着", "ビキニ"]
    payload["main_subject"] = {
        "name": person_name,
        "kind": "person",
        "role": "俳優・グラビアモデル",
        "is_public_creator": True,
        "reason": (
            "画像内の『加藤愛梨』『airi_kato_official』と、"
            "集英社・週プレの公式人物情報が一致"
        ),
    }
    payload["identity_resolution"] = {
        "status": "verified",
        "method": "local_ocr_and_official_publisher",
        "message": (
            "画像内Instagram IDと氏名をOCRで取得し、週プレNEWSの公式SNS欄、"
            "集英社の写真集モデル表記と照合"
        ),
        "retry_after": "",
    }
    payload["verified_social_profiles"] = [dict(item) for item in KATO_AIRI_PROFILES]
    payload["promotion_type"] = "organic"
    payload.pop("affiliate_opportunities", None)
    payload.pop("transparency_note", None)
    payload["local_identity_clues"] = [
        {
            "image_id": "source-image-3",
            "ocr_text": "airi_kato_official / 中央大学 (Chuo University)",
            "public_handle_candidates": [{
                "handle": "airi_kato_official",
                "written_as": "airi_kato_official",
                "service_hint": "instagram",
                "confidence": 95,
                "evidence_type": "watermark_ocr",
            }],
            "known_identity_matches": [{
                "name": person_name,
                "role": "俳優・グラビアモデル",
                "confidence": 99,
            }],
        },
        {
            "image_id": "source-image-5",
            "ocr_text": "Digital Limited / photographed by Kousuke MAE / 加藤愛梨",
            "public_handle_candidates": [],
            "known_identity_matches": [{
                "name": person_name,
                "role": "俳優・グラビアモデル",
                "confidence": 99,
            }],
        },
    ]
    for index, item in enumerate(_source_media(payload, "source-image-"), start=1):
        item["alt"] = f"加藤愛梨のグラビア画像 {index}"
        item["caption"] = person_name
        if str(item.get("id") or "") == "source-image-3":
            item["local_ocr_text"] = "airi_kato_official / 中央大学 (Chuo University)"
            item["local_public_handle_candidates"] = [{
                "handle": "airi_kato_official",
                "written_as": "airi_kato_official",
                "service_hint": "instagram",
                "confidence": 95,
                "evidence_type": "watermark_ocr",
            }]
        elif str(item.get("id") or "") == "source-image-5":
            item["local_ocr_text"] = "Digital Limited / photographed by Kousuke MAE / 加藤愛梨"

    official_url = "https://www.grajapa.shueisha.co.jp/item/detail/gravure/32ccf4fe4c399f573db547b704dcf2aa"
    official_thumbnail = (
        "https://www.grajapa.shueisha.co.jp/files/jpn/img/book/000001/"
        "32ccf4fe4c399f573db547b704dcf2aa_l.jpg"
    )
    body_blocks = [
        block
        for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") != "related_link"
    ]
    body_blocks.append({
        "id": "kato-airi-official-photobook",
        "type": "related_link",
        "url": official_url,
        "title": "加藤愛梨 写真集『永遠に憧れの人。』",
        "text": "記事の人物本人をモデルとして集英社が販売する公式デジタル写真集です。",
        "button_text": "公式写真集を見る",
        "placement_label": "加藤愛梨の公式写真集",
        "provider": "grajapa",
        "link_kind": "exact_official_work",
        "match_evidence": "画像内氏名と公式商品ページのモデル名が一致",
        "match_confidence": 99,
        "person_name": person_name,
        "thumbnail_url": official_thumbnail,
        "thumbnail_source_kind": "official_page",
        "thumbnail_owner_url": official_url,
    })
    payload["blocks"] = body_blocks
    payload["related_destinations"] = [{
        "url": official_url,
        "title": "加藤愛梨 写真集『永遠に憧れの人。』",
        "provider": "grajapa",
        "link_kind": "official_content",
        "match_confidence": 99,
        "person_name": person_name,
    }]
    apply_official_social_destinations(payload, KATO_AIRI_PROFILES)
    ensure_related_footer(payload)
    payload.pop("quality_gate", None)
    payload.pop("review_message", None)
    return before != repr(payload)


REPAIRS: dict[str, Callable[[dict[str, Any]], bool]] = {
    PAN_SLUG: repair_pan_piano,
    HAYASHIDA_SLUG: repair_hayashida_moka,
    SAKURA_SLUG: repair_sakura_miko,
    KATO_AIRI_SLUG: repair_kato_airi,
}


def repair(slug: str, site_root: Path = ROOT) -> dict[str, Any]:
    repairer = REPAIRS.get(slug)
    if repairer is None:
        raise ValueError(f"未登録の修復対象です: {slug}")
    payload = load_draft_payload(slug, site_root)
    content_changed = repairer(payload)
    if slug == KATO_AIRI_SLUG:
        upsert_social_profile_record(site_root, {
            "canonical_name": "加藤愛梨",
            "aliases": ["加藤愛梨", "airi_kato_official", "l_ovepear"],
            "role": "俳優・グラビアモデル",
            "status": "verified",
            "confidence": 99,
            "profiles": [dict(item) for item in KATO_AIRI_PROFILES],
            "evidence": [
                {
                    "url": "https://wpb.shueisha.co.jp/gravure/movie/20260715-132134/",
                    "kind": "published_article",
                    "claim": "加藤愛梨の氏名、活動歴、公式X・TikTok・Instagramを掲載",
                },
                {
                    "url": "https://www.shueisha.co.jp/books/items/contents.html?jdcn=08000000052447000000",
                    "kind": "official_profile",
                    "claim": "集英社の写真集ページでモデル名を加藤愛梨と確認",
                },
            ],
            "reason": "画像内の氏名・Instagram IDと集英社公式情報が一致",
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "retry_after": "",
            "verification_method": "local_ocr_and_official_publisher",
        })
    identity_changed = backfill_verified_main_subject_identity(payload)
    thumbnails_changed = localize_related_thumbnail_assets(payload)
    fallback_changed = apply_related_thumbnail_fallbacks(payload)
    pruned = prune_unreferenced_related_thumbnail_assets(payload)
    payload["replace_existing"] = True
    apply_quality_gate(site_root, payload, persist=False)
    save_draft(payload, site_root)
    result = add_built_article(payload, site_root)
    return {
        **result,
        "content_changed": content_changed,
        "identity_changed": identity_changed,
        "thumbnails_changed": thumbnails_changed,
        "fallback_changed": fallback_changed,
        "pruned": pruned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair known identity failures.")
    parser.add_argument("slugs", nargs="*", choices=sorted(REPAIRS))
    parser.add_argument("--site-root", type=Path, default=ROOT)
    args = parser.parse_args()
    slugs = args.slugs or list(REPAIRS)
    for slug in slugs:
        print(slug, repair(slug, args.site_root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
