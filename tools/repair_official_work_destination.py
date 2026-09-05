#!/usr/bin/env python3
"""Replace a generic recommendation with a verified official work page."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import build_article, load_draft_payload, save_draft  # noqa: E402
from indanya_desktop.adaptive_quality import article_quality_report  # noqa: E402
from indanya_desktop.official_work_registry import (  # noqa: E402
    resolve_verified_official_work,
)
from indanya_desktop.related_links import (  # noqa: E402
    ensure_related_footer,
    related_link_insert_index,
    resolve_article_destinations,
)


_REPLACEABLE_KINDS = {
    "exact_official_work",
    "fallback_ranking",
    "inferred_topic_search",
    "person_search",
}


def repair_payload_official_work(
    payload: dict[str, Any],
    site_root: Path,
) -> dict[str, Any]:
    subject = payload.get("main_subject") or payload.get("ai_main_subject") or {}
    subject_name = str(subject.get("name") or "").strip()
    official = resolve_verified_official_work(site_root, subject_name)
    if not official:
        raise ValueError(f"検証済み公式作品ページがありません: {subject_name or '主役不明'}")

    payload["ai_official_work"] = dict(official)
    payload["official_work_required"] = True
    payload["suppress_generic_related_recommendation"] = True
    payload["verified_work_destinations"] = [{
        "url": official["url"],
        "title": official["title"],
        "provider": official["provider"],
        "reason": official["reason"],
        "thumbnail_url": official.get("thumbnail_url", ""),
        "confidence": 100,
    }]
    payload["blocks"] = [
        block for block in payload.get("blocks") or []
        if not (
            isinstance(block, dict)
            and (
                str(block.get("link_kind") or "") in _REPLACEABLE_KINDS
                or str(block.get("id") or "").startswith(
                    "article-related-footer-recommendation"
                )
                or str(block.get("id") or "") == "article-related-footer-product"
            )
        )
    ]
    payload["related_destinations"] = []
    resolved = resolve_article_destinations(
        payload,
        payload,
        [
            item for item in payload.get("affiliate_opportunities") or []
            if isinstance(item, dict)
        ],
    )
    official_block = next(
        (
            item for item in resolved
            if item.get("link_kind") == "exact_official_work"
            and item.get("url") == official["url"]
        ),
        None,
    )
    if official_block is None:
        raise ValueError("公式作品カードを構成できませんでした")
    if not official_block.get("thumbnail_url"):
        available_image_ids = {
            str(image.get("id") or "").strip()
            for image in payload.get("images") or []
            if isinstance(image, dict) and str(image.get("id") or "").strip()
        }
        thumbnail_id = str(payload.get("thumbnail_id") or "").strip()
        if thumbnail_id in available_image_ids:
            official_block["thumbnail_image_id"] = thumbnail_id
    insert_at = related_link_insert_index(payload["blocks"], "exact_official_work")
    payload["blocks"].insert(insert_at, official_block)
    payload["related_destinations"] = [{
        "url": official_block.get("url", ""),
        "title": official_block.get("title", ""),
        "provider": official_block.get("provider", ""),
        "link_kind": "exact_official_work",
        "match_confidence": official_block.get("match_confidence", 100),
    }]
    ensure_related_footer(payload)
    return payload


def repair_draft(site_root: Path, slug: str) -> dict[str, Any]:
    payload = load_draft_payload(slug, site_root)
    payload = repair_payload_official_work(payload, site_root)
    quality = article_quality_report(payload, payload)
    if quality.get("blockers"):
        raise ValueError(
            "品質検査で停止しました: " + ", ".join(quality["blockers"])
        )
    payload["replace_existing"] = True
    save_draft(payload, site_root)
    build_article(payload, site_root, preview=True)
    return {
        "slug": slug,
        "title": payload.get("title", ""),
        "official_work": payload.get("ai_official_work", {}),
        "quality": quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    result = repair_draft(args.site_root.resolve(), args.slug)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
