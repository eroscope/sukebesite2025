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
from indanya_desktop.social_profiles import (
    enrich_source_profile_thumbnails,
    registry_profiles_for_payload,
)


def _load_payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def repair_registered_social_profiles(
    site_root: Path,
    *,
    rebuild: bool = True,
    slugs: set[str] | None = None,
) -> list[str]:
    repaired: list[str] = []
    draft_root = site_root / ".article-studio" / "drafts"
    for path in sorted(draft_root.glob("*.json")):
        if slugs and path.stem not in slugs:
            continue
        payload = _load_payload(path)
        if payload is None:
            continue
        profiles = registry_profiles_for_payload(site_root, payload)
        if not profiles:
            continue
        source = {"verified_social_profiles": profiles}
        enrich_source_profile_thumbnails(site_root, source)
        profiles = source["verified_social_profiles"]
        changed = apply_official_social_destinations(payload, profiles)
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
        if not changed:
            continue
        payload["identity_resolution"] = {
            "status": "verified",
            "method": "verified_registry_repair",
            "message": f"検証済み人物名簿から{first.get('name', '主役')}の公式アカウントを追加",
        }
        slug = save_draft(payload, site_root)
        if rebuild and (site_root / "articles" / f"{slug}.html").is_file():
            payload["replace_existing"] = True
            add_built_article(payload, site_root)
        repaired.append(slug)
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
