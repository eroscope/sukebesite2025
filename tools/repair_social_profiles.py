from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import add_built_article, save_draft
from indanya_desktop.related_links import apply_official_social_destinations
from indanya_desktop.related_thumbnail_assets import (
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
    prune_unreferenced_related_thumbnail_assets,
)
from indanya_desktop.social_profiles import (
    enrich_source_profile_thumbnails,
    merge_verified_social_profiles,
    registry_profiles_for_payload,
)
from repair_legacy_article_cards import refresh_rebuilt_site_discovery


def _load_payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _published_metadata_by_slug(site_root: Path) -> dict[str, dict[str, Any]]:
    try:
        database = json.loads(
            (site_root / "data" / "articles.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        database = []
    if not isinstance(database, list):
        database = []
    return {
        str(item.get("slug") or ""): item
        for item in database
        if isinstance(item, dict) and str(item.get("slug") or "")
    }


def _profiles_for_repair(
    site_root: Path,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    existing = [
        dict(item)
        for item in (payload.get("verified_social_profiles") or [])
        if isinstance(item, dict)
    ]
    registered = registry_profiles_for_payload(site_root, payload)
    return merge_verified_social_profiles(existing, registered)


def repair_registered_social_profiles(
    site_root: Path,
    *,
    rebuild: bool = True,
    slugs: set[str] | None = None,
) -> list[str]:
    repaired: list[str] = []
    published_by_slug = _published_metadata_by_slug(site_root)
    draft_root = site_root / ".article-studio" / "drafts"
    for path in sorted(draft_root.glob("*.json")):
        if slugs and path.stem not in slugs:
            continue
        payload = _load_payload(path)
        if payload is None:
            continue
        published = published_by_slug.get(path.stem)
        if published and published.get("published_at"):
            payload["published_at"] = str(published["published_at"])
        article_path = site_root / "articles" / f"{path.stem}.html"
        try:
            renderer_rebuild = (
                rebuild
                and article_path.is_file()
                and 'class="person-discovery"' in article_path.read_text(encoding="utf-8")
            )
        except OSError:
            renderer_rebuild = False
        profiles = _profiles_for_repair(site_root, payload)
        if not profiles and not renderer_rebuild:
            continue
        changed = renderer_rebuild
        if profiles:
            source = {"verified_social_profiles": profiles}
            enrich_source_profile_thumbnails(site_root, source)
            profiles = source["verified_social_profiles"]
            profiles_changed = payload.get("verified_social_profiles") != profiles
            if profiles_changed:
                payload["verified_social_profiles"] = profiles
            changed = (
                apply_official_social_destinations(payload, profiles)
                or profiles_changed
                or changed
            )
        changed = localize_related_thumbnail_assets(payload) or changed
        changed = apply_related_thumbnail_fallbacks(payload) or changed
        for block in payload.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "related_link":
                continue
            if not block.get("thumbnail_url"):
                continue
            block.pop("thumbnail_url", None)
            block.pop("thumbnail_source_kind", None)
            block.pop("thumbnail_owner_url", None)
            changed = True
        changed = prune_unreferenced_related_thumbnail_assets(payload) or changed
        if profiles:
            first = profiles[0]
            verified_role = str(first.get("role") or "公開活動者")
            subject = payload.get("main_subject")
            if not isinstance(subject, dict):
                payload["main_subject"] = {
                    "name": str(first.get("name") or ""),
                    "kind": "person",
                    "role": verified_role,
                    "is_public_creator": True,
                    "reason": "記事タイトルまたはタグと検証済み人物名簿が一致",
                }
                changed = True
            elif (
                str(subject.get("name") or "") == str(first.get("name") or "")
                and verified_role
                and str(subject.get("role") or "") in {"", "公開活動者"}
            ):
                subject["role"] = verified_role
                subject["is_public_creator"] = True
                changed = True
            payload["identity_resolution"] = {
                "status": "verified",
                "method": "verified_registry_repair",
                "message": (
                    f"検証済み人物名簿から{first.get('name', '主役')}"
                    "の公式アカウントを追加"
                ),
            }
        if not changed:
            continue
        slug = save_draft(payload, site_root)
        if rebuild and (site_root / "articles" / f"{slug}.html").is_file():
            payload["replace_existing"] = True
            add_built_article(payload, site_root)
        repaired.append(slug)
    if rebuild and repaired:
        discovery = refresh_rebuilt_site_discovery(site_root)
        if discovery.get("status") == "skipped":
            raise RuntimeError(str(discovery.get("reason") or "検索向け情報を再生成できません"))
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description="検証済み人物の公式SNSを既存記事へ反映します")
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="修復する記事slug。複数指定できます",
    )
    args = parser.parse_args()
    repaired = repair_registered_social_profiles(
        args.site_root.resolve(),
        rebuild=not args.no_rebuild,
        slugs=set(args.slug) or None,
    )
    print(json.dumps({"count": len(repaired), "slugs": repaired}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
