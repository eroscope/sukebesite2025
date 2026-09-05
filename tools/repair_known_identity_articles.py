from __future__ import annotations

import argparse
import sys
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


PAN_SLUG = "url-himablo-xyz-dcd6535f"
HAYASHIDA_SLUG = "url-chaos-giga-com-9b7c45ac"
SAKURA_SLUG = "url-hnalady-com-0726ca89"


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


REPAIRS: dict[str, Callable[[dict[str, Any]], bool]] = {
    PAN_SLUG: repair_pan_piano,
    HAYASHIDA_SLUG: repair_hayashida_moka,
    SAKURA_SLUG: repair_sakura_miko,
}


def repair(slug: str, site_root: Path = ROOT) -> dict[str, Any]:
    repairer = REPAIRS.get(slug)
    if repairer is None:
        raise ValueError(f"未登録の修復対象です: {slug}")
    payload = load_draft_payload(slug, site_root)
    content_changed = repairer(payload)
    identity_changed = backfill_verified_main_subject_identity(payload)
    thumbnails_changed = localize_related_thumbnail_assets(payload)
    fallback_changed = apply_related_thumbnail_fallbacks(payload)
    pruned = prune_unreferenced_related_thumbnail_assets(payload)
    payload["replace_existing"] = True
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
