#!/usr/bin/env python3
"""Regenerate one existing draft from its source and leave an auditable report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import (  # noqa: E402
    build_article,
    build_source_draft_payload,
    load_draft_payload,
    save_draft,
)
from indanya_desktop.adaptive_quality import (  # noqa: E402
    apply_quality_gate,
    article_quality_report,
)
from indanya_desktop.browser_capture import capture_fanza_product_metadata  # noqa: E402
from indanya_desktop.editorial_policy import restrict_source_to_fanza_product  # noqa: E402
from indanya_desktop.person_identity import (  # noqa: E402
    apply_verified_person_identity_to_payload,
    apply_verified_person_identity_to_source,
)
from indanya_desktop.related_links import apply_official_social_destinations  # noqa: E402
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
)
from indanya_desktop.social_profiles import (  # noqa: E402
    enrich_source_profile_thumbnails,
    resolve_identified_people_social_profiles,
    resolve_performer_social_profiles,
    resolve_subject_social_profiles,
)
from indanya_desktop.workers import (  # noqa: E402
    _apply_editorial_metadata,
    _capture_source_candidates,
    _generate_article_payload,
)


_FANZA_REFRESH_FIELDS = (
    "fanza_people",
    "ai_fanza_people",
    "fanza_performer_name",
    "ai_fanza_performer_name",
    "fanza_performer_pages",
    "verified_social_profiles",
    "performer_identity_resolution",
)

_FANZA_REFRESH_LINK_KINDS = {
    "official_profile",
    "official_content",
    "verified_person_search",
}


def _reset_fanza_metadata(payload: dict[str, Any]) -> None:
    """Drop stale performer metadata before rebuilding deterministic links."""
    for field in _FANZA_REFRESH_FIELDS:
        payload.pop(field, None)
    payload.pop("related_destinations", None)
    payload["blocks"] = [
        block
        for block in (payload.get("blocks") or [])
        if not (
            isinstance(block, dict)
            and block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in _FANZA_REFRESH_LINK_KINDS
        )
    ]


def _link_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": str(block.get("type") or ""),
            "link_kind": str(block.get("link_kind") or ""),
            "match_type": str(block.get("match_type") or ""),
            "title": str(block.get("title") or ""),
            "url": str(block.get("url") or ""),
            "thumbnail_source_kind": str(
                block.get("thumbnail_source_kind") or ""
            ),
            "thumbnail_owner_url": str(block.get("thumbnail_owner_url") or ""),
        }
        for block in payload.get("blocks") or []
        if isinstance(block, dict)
        and block.get("type") in {"product_cta", "related_link"}
    ]


def regenerate(
    site_root: Path,
    slug: str,
    source_url: str,
    *,
    category: str = "auto",
    reply_count: str = "auto",
) -> dict[str, Any]:
    previous = load_draft_payload(slug, site_root)

    def progress(value: int, message: str) -> None:
        print(f"[{value:3d}%] {message}", flush=True)

    payload = _generate_article_payload(
        site_root,
        source_url,
        category,
        reply_count,
        progress,
        {"content_mode": "auto", "promotion_type": "organic"},
    )
    payload["slug"] = slug
    payload["published_at"] = str(
        previous.get("published_at") or payload.get("published_at") or ""
    )
    payload["status"] = "published"
    payload["editorial_status"] = "published"
    payload["replace_existing"] = True
    saved_slug = save_draft(payload, site_root)
    if saved_slug != slug:
        raise RuntimeError(f"draft slug mismatch: {saved_slug} != {slug}")

    validated = load_draft_payload(slug, site_root)
    build_article(validated, site_root, preview=True)
    quality = article_quality_report(validated)
    report = {
        "slug": slug,
        "source_url": source_url,
        "resolved_source_url": str(validated.get("resolved_source_url") or ""),
        "source_chain": list(validated.get("source_chain") or []),
        "navigation_trace": list(validated.get("navigation_trace") or []),
        "title": str(validated.get("title") or ""),
        "main_subject": validated.get("main_subject") or {},
        "image_count": len(validated.get("images") or []),
        "video_count": len(validated.get("videos") or []),
        "links": _link_summary(validated),
        "quality": quality,
    }
    report_root = site_root / ".article-studio" / "repairs"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{slug}.json"
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)
    return report


def refresh_fanza_metadata(
    site_root: Path,
    slug: str,
    source_url: str,
) -> dict[str, Any]:
    """Refresh only deterministic FANZA/person links; keep the written article."""
    payload = load_draft_payload(slug, site_root)
    _reset_fanza_metadata(payload)
    source = restrict_source_to_fanza_product(
        capture_fanza_product_metadata(source_url)
    )
    subject = payload.get("main_subject")
    if isinstance(subject, dict):
        source["ai_main_subject"] = dict(subject)
        source["official_work_required"] = bool(
            subject.get("kind") in {"work", "product"}
            and str(subject.get("name") or "").strip()
        )
    source = resolve_performer_social_profiles(site_root, source)
    source = enrich_source_profile_thumbnails(site_root, source)
    _apply_editorial_metadata(
        payload,
        source,
        {"content_mode": "fanza_product", "promotion_type": "affiliate"},
        site_root,
    )
    payload["slug"] = slug
    payload["replace_existing"] = True
    saved_slug = save_draft(payload, site_root)
    if saved_slug != slug:
        raise RuntimeError(f"draft slug mismatch: {saved_slug} != {slug}")
    validated = load_draft_payload(slug, site_root)
    build_article(validated, site_root, preview=True)
    return {
        "slug": slug,
        "source_url": source_url,
        "title": str(validated.get("title") or ""),
        "image_count": len(validated.get("images") or []),
        "video_count": len(validated.get("videos") or []),
        "links": _link_summary(validated),
        "quality": article_quality_report(validated),
        "metadata_only": True,
    }


def _replace_media_blocks(
    blocks: list[dict[str, Any]],
    image_ids: list[str],
    video_ids: list[str],
) -> list[dict[str, Any]]:
    """Keep the written response order while replacing every media reference."""
    replaced: list[dict[str, Any]] = []
    image_index = 0
    video_index = 0
    last_media_index = -1
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        block_type = str(block.get("type") or "")
        if block_type == "separator" and "gallery-separator" in str(block.get("id") or ""):
            continue
        if block_type == "images":
            capacity = max(1, len(block.get("image_ids") or []))
            assigned = image_ids[image_index:image_index + capacity]
            image_index += len(assigned)
            if not assigned:
                continue
            block["image_ids"] = assigned
            replaced.append(block)
            last_media_index = len(replaced) - 1
            continue
        if block_type == "videos":
            capacity = max(1, len(block.get("video_ids") or []))
            assigned = video_ids[video_index:video_index + capacity]
            video_index += len(assigned)
            if not assigned:
                continue
            block["video_ids"] = assigned
            replaced.append(block)
            last_media_index = len(replaced) - 1
            continue
        replaced.append(block)

    remaining_media: list[dict[str, Any]] = []
    for offset in range(image_index, len(image_ids), 2):
        remaining_media.append({
            "id": f"repaired-images-{offset + 1}",
            "type": "images",
            "image_ids": image_ids[offset:offset + 2],
        })
    if video_index < len(video_ids):
        remaining_media.append({
            "id": "repaired-videos",
            "type": "videos",
            "video_ids": video_ids[video_index:],
        })
    if remaining_media:
        insertion = last_media_index + 1 if last_media_index >= 0 else 0
        replaced[insertion:insertion] = remaining_media
    return replaced


def refresh_media(
    site_root: Path,
    slug: str,
    source_url: str,
) -> dict[str, Any]:
    """Recapture deterministic media without spending another model call."""
    payload = load_draft_payload(slug, site_root)
    capture_url = str(payload.get("resolved_source_url") or source_url).strip()

    def progress(value: int, message: str) -> None:
        print(f"[{value:3d}%] {message}", flush=True)

    source = _capture_source_candidates(site_root, capture_url, progress)
    subject = payload.get("main_subject")
    if isinstance(subject, dict):
        source["ai_main_subject"] = dict(subject)
    source["ai_tags"] = list(payload.get("tags") or [])
    source = resolve_subject_social_profiles(site_root, source)
    source = resolve_performer_social_profiles(site_root, source)
    source = resolve_identified_people_social_profiles(site_root, source)
    source = enrich_source_profile_thumbnails(site_root, source)
    source = apply_verified_person_identity_to_source(source)
    selected_image_ids = [
        str(item.get("id") or "")
        for item in source.get("images") or []
        if isinstance(item, dict) and item.get("id")
    ]
    selected_video_ids = [
        str(item.get("id") or "")
        for item in source.get("videos") or []
        if isinstance(item, dict) and item.get("id")
    ]
    if not selected_image_ids and not selected_video_ids:
        raise RuntimeError("再取得した本編に画像・動画がありません")
    fresh = build_source_draft_payload(
        source,
        selected_image_ids,
        selected_video_ids=selected_video_ids,
        thumbnail_image_id=selected_image_ids[0] if selected_image_ids else None,
    )
    payload["images"] = list(fresh.get("images") or [])
    payload["videos"] = list(fresh.get("videos") or [])
    payload["thumbnail_id"] = str(fresh.get("thumbnail_id") or "")
    payload["thumbnail_only"] = bool(fresh.get("thumbnail_only"))
    payload["blocks"] = _replace_media_blocks(
        list(payload.get("blocks") or []),
        [str(item["id"]) for item in payload["images"]],
        [str(item["id"]) for item in payload["videos"]],
    )
    payload["capture_strategy"] = str(source.get("capture_strategy") or "")
    payload["embedded_x_status_urls"] = list(source.get("embedded_x_status_urls") or [])
    payload["media_alignment_checked"] = True
    profiles = [
        dict(item)
        for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict)
    ]
    if profiles:
        payload["verified_social_profiles"] = json.loads(
            json.dumps(profiles, ensure_ascii=False)
        )
        apply_official_social_destinations(payload, profiles)
    identity_resolution = source.get("identity_resolution")
    if isinstance(identity_resolution, dict):
        payload["identity_resolution"] = dict(identity_resolution)
    apply_verified_person_identity_to_payload(payload, source)
    localize_related_thumbnail_assets(payload)
    apply_related_thumbnail_fallbacks(payload)
    payload["slug"] = slug
    payload["replace_existing"] = True
    apply_quality_gate(site_root, payload, source, persist=False)
    saved_slug = save_draft(payload, site_root)
    if saved_slug != slug:
        raise RuntimeError(f"draft slug mismatch: {saved_slug} != {slug}")
    validated = load_draft_payload(slug, site_root)
    build_article(validated, site_root, preview=True)
    return {
        "slug": slug,
        "source_url": source_url,
        "capture_url": capture_url,
        "title": str(validated.get("title") or ""),
        "image_count": len(validated.get("images") or []),
        "video_count": len(validated.get("videos") or []),
        "embedded_x_status_urls": list(validated.get("embedded_x_status_urls") or []),
        "quality": article_quality_report(validated, source),
        "media_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="既存記事を現在の素材解析・Luna生成フローで再作成します"
    )
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--category", default="auto")
    parser.add_argument("--reply-count", default="auto")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="本文を作り直さずFANZA・出演者・SNS導線だけを更新します",
    )
    parser.add_argument(
        "--media-only",
        action="store_true",
        help="本文を作り直さず取得元の画像・動画だけを再回収します",
    )
    args = parser.parse_args()
    if args.metadata_only and args.media_only:
        parser.error("--metadata-only と --media-only は同時に指定できません")
    if args.media_only:
        report = refresh_media(
            args.site_root.resolve(),
            args.slug,
            args.source_url,
        )
    elif args.metadata_only:
        report = refresh_fanza_metadata(
            args.site_root.resolve(),
            args.slug,
            args.source_url,
        )
    else:
        report = regenerate(
            args.site_root.resolve(),
            args.slug,
            args.source_url,
            category=args.category,
            reply_count=args.reply_count,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
