#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(TOOLS_ROOT))

from indanya_desktop.editorial_policy import (  # noqa: E402
    canonical_fanza_product_url,
    fanza_product_id,
    is_fanza_package_image,
)
from indanya_desktop.social_profiles import (  # noqa: E402
    canonical_social_profile_url,
)
from indanya_desktop.affiliate_opportunities import (  # noqa: E402
    mgs_product_code_from_url,
    normalize_affiliate_opportunities,
)


COUNT_TITLE = re.compile(r"(?:画像|動画|GIF)\s*\d+\s*(?:枚|本)", re.IGNORECASE)
VALID_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,99}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _body_image_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "images":
            continue
        ids.update(str(item) for item in block.get("image_ids") or [] if item)
    return ids


def _search_terms(url: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    raw = str((query.get("key") or query.get("searchstr") or [""])[0])
    return [item for item in re.split(r"[\s　]+", unquote_plus(raw).strip()) if item]


def _is_direct_fanza_product(payload: dict[str, Any]) -> bool:
    parsed = urlparse(str(payload.get("source_url") or ""))
    return (
        (parsed.hostname or "").casefold() == "video.dmm.co.jp"
        and parsed.path.rstrip("/") == "/av/content"
        and bool(fanza_product_id(str(payload.get("source_url") or "")))
    )


def audit_site(site_root: Path) -> dict[str, Any]:
    drafts = [
        path
        for path in sorted((site_root / ".article-studio" / "drafts").glob("*.json"))
        if VALID_SLUG.fullmatch(path.stem)
    ]
    issue_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    scores: list[int] = []
    warning_counts: Counter[str] = Counter()
    exact_cards = 0
    profile_cards = 0
    local_profile_cards = 0

    def flag(kind: str, slug: str) -> None:
        issue_counts[kind] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 20:
            bucket.append(slug)

    for path in drafts:
        payload = _load_json(path)
        slug = str(payload.get("slug") or path.stem)
        if not payload:
            flag("invalid_draft_json", slug)
            continue
        if (
            str(payload.get("status") or "") == "deleted"
            or str(payload.get("review_status") or "") == "deleted"
            or str(payload.get("editorial_status") or "").startswith("removed_")
        ):
            continue
        quality = payload.get("quality_gate") or {}
        scores.append(int(quality.get("score") or 0))
        for blocker in quality.get("blockers") or []:
            flag(f"quality_blocker:{blocker}", slug)
        for warning in quality.get("warnings") or []:
            warning_counts[str(warning)] += 1

        if COUNT_TITLE.search(str(payload.get("title") or "")):
            flag("numeric_media_count_in_title", slug)
        article_path = site_root / "articles" / f"{slug}.html"
        if (
            str(payload.get("status") or "") == "published"
            and not article_path.is_file()
        ):
            flag("missing_article_html", slug)

        matched_mgs_codes = {
            str(item.get("product_code") or "").upper()
            for item in normalize_affiliate_opportunities(
                payload.get("affiliate_opportunities")
            )
            if item.get("article_match") is True and item.get("product_code")
        }
        direct_mgs_code = mgs_product_code_from_url(payload.get("source_url"))
        if direct_mgs_code:
            matched_mgs_codes.add(direct_mgs_code)
        if article_path.is_file():
            rendered = article_path.read_text(encoding="utf-8", errors="replace")
            rendered_codes = {
                mgs_product_code_from_url(match)
                for match in re.findall(
                    r'https?://[^"\'<>\s]+', rendered, re.IGNORECASE
                )
            }
            rendered_codes.discard("")
            if rendered_codes.difference(matched_mgs_codes):
                flag("rendered_unverified_mgs_product", slug)

        images = {
            str(item.get("id") or ""): item
            for item in payload.get("images") or []
            if isinstance(item, dict) and item.get("id")
        }
        body_ids = _body_image_ids(payload)
        for image_id in body_ids:
            image = images.get(image_id)
            if image and image.get("related_thumbnail_only"):
                flag("related_thumbnail_in_body", slug)

        official_account_urls: set[str] = set()
        direct_fanza_product = _is_direct_fanza_product(payload)
        direct_exact_cards = 0
        direct_inferred_cards = 0
        direct_performer_cards = 0
        direct_profile_cards = 0
        for block in payload.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "related_link":
                mgs_code = mgs_product_code_from_url(block.get("url"))
                if mgs_code and mgs_code not in matched_mgs_codes:
                    flag("unverified_mgs_product", slug)
                link_kind = str(block.get("link_kind") or "")
                if direct_fanza_product and link_kind.startswith("inferred_"):
                    direct_inferred_cards += 1
                if link_kind == "inferred_topic_search":
                    flag("unresolved_related_fanza_product", slug)
                if link_kind == "inferred_topic_product":
                    product = canonical_fanza_product_url(str(block.get("url") or ""))
                    owner = canonical_fanza_product_url(
                        str(block.get("thumbnail_owner_url") or "")
                    )
                    local_image = images.get(str(block.get("thumbnail_image_id") or ""))
                    local_url = str(
                        (local_image or {}).get("source_url")
                        or (local_image or {}).get("url")
                        or ""
                    )
                    remote_url = str(block.get("thumbnail_url") or "")
                    package_url = local_url or remote_url
                    if not (
                        product
                        and owner == product
                        and str(block.get("thumbnail_source_kind") or "")
                        == "fanza_package"
                        and is_fanza_package_image(
                            {"url": package_url}, fanza_product_id(product)
                        )
                    ):
                        flag("invalid_related_fanza_product_thumbnail", slug)
                if link_kind == "verified_person_search":
                    sample_product = canonical_fanza_product_url(
                        str(block.get("sample_product_url") or block.get("thumbnail_owner_url") or "")
                    )
                    image = images.get(str(block.get("thumbnail_image_id") or ""))
                    package_url = str(
                        (image or {}).get("source_url")
                        or (image or {}).get("url")
                        or block.get("thumbnail_url")
                        or ""
                    )
                    local_owner = canonical_fanza_product_url(
                        str((image or {}).get("thumbnail_owner_url") or "")
                    )
                    source_kind = str(block.get("thumbnail_source_kind") or "")
                    if not (
                        sample_product
                        and source_kind == "fanza_performer_sample"
                        and is_fanza_package_image(
                            {"url": package_url}, fanza_product_id(sample_product)
                        )
                        and (not image or local_owner == sample_product)
                    ):
                        flag("invalid_performer_card_thumbnail", slug)
                if block.get("link_kind") == "exact_official_work":
                    destination = str(block.get("url") or "").rstrip("/")
                    image = images.get(str(block.get("thumbnail_image_id") or ""))
                    local_thumbnail_valid = bool(
                        destination
                        and isinstance(image, dict)
                        and image.get("related_thumbnail_only") is True
                        and str(image.get("rights_basis") or "")
                        == "official_page_thumbnail"
                        and str(image.get("thumbnail_owner_url") or "").rstrip("/")
                        == destination
                    )
                    remote_thumbnail_valid = bool(
                        destination
                        and block.get("thumbnail_url")
                        and str(block.get("thumbnail_source_kind") or "")
                        == "official_page"
                        and str(block.get("thumbnail_owner_url") or "").rstrip("/")
                        == destination
                    )
                    if not (local_thumbnail_valid or remote_thumbnail_valid):
                        flag("invalid_official_work_thumbnail", slug)
            if block_type == "product_cta":
                match_type = str(block.get("match_type") or "")
                if match_type.startswith("exact_"):
                    exact_cards += 1
                    if direct_fanza_product:
                        direct_exact_cards += 1
                    owner = canonical_fanza_product_url(
                        str(block.get("thumbnail_owner_url") or "")
                    )
                    product = canonical_fanza_product_url(str(block.get("url") or ""))
                    local_image = images.get(
                        str(block.get("thumbnail_image_id") or "")
                    )
                    local_package_valid = bool(
                        product
                        and owner == product
                        and str(block.get("thumbnail_source_kind") or "")
                        == "fanza_package"
                        and isinstance(local_image, dict)
                        and is_fanza_package_image(
                            {
                                "url": str(
                                    local_image.get("source_url")
                                    or local_image.get("url")
                                    or ""
                                )
                            },
                            fanza_product_id(product),
                        )
                    )
                    remote_package_valid = bool(
                        product
                        and owner == product
                        and str(block.get("thumbnail_source_kind") or "")
                        == "fanza_package"
                        and is_fanza_package_image(
                            {"url": str(block.get("thumbnail_url") or "")},
                            fanza_product_id(product),
                        )
                    )
                    if (
                        not local_package_valid
                        and not remote_package_valid
                    ):
                        flag("invalid_exact_product_thumbnail", slug)
                elif match_type.startswith("inferred_"):
                    if direct_fanza_product:
                        direct_inferred_cards += 1
                elif "dmm." in str(block.get("url") or "").casefold():
                    if len(_search_terms(str(block.get("url") or ""))) > 1:
                        flag("multiword_fanza_search", slug)
            if (
                direct_fanza_product
                and block_type == "related_link"
                and block.get("link_kind") == "verified_person_search"
            ):
                direct_performer_cards += 1
            if (
                block_type != "related_link"
                or block.get("link_kind") not in {"official_profile", "official_content"}
            ):
                continue
            profile_cards += 1
            if direct_fanza_product:
                direct_profile_cards += 1
            provider = str(block.get("provider") or "").casefold()
            profile_url = canonical_social_profile_url(provider, block.get("url"))
            if profile_url in official_account_urls:
                flag("duplicate_official_account_url", slug)
            official_account_urls.add(profile_url)
            source_kind = str(block.get("thumbnail_source_kind") or "")
            expected_owner = str(
                block.get("thumbnail_owner_url") or profile_url
            ).rstrip("/")
            image_id = str(block.get("thumbnail_image_id") or "")
            image = images.get(image_id)
            if not image:
                flag("profile_without_local_thumbnail", slug)
                continue
            owner = str(image.get("thumbnail_owner_url") or "").rstrip("/")
            direct_owner_valid = (
                source_kind in {"profile", "official_identity_fallback"}
                and expected_owner == profile_url.rstrip("/")
            )
            hub_owner_valid = (
                source_kind == "official_hub_profile" and bool(expected_owner)
            )
            if (
                not image.get("related_thumbnail_only")
                or owner != expected_owner
                or not (direct_owner_valid or hub_owner_valid)
            ):
                flag("profile_thumbnail_owner_mismatch", slug)
                continue
            local_profile_cards += 1

        if direct_fanza_product:
            if direct_exact_cards != 1:
                flag("direct_fanza_exact_product_card_count", slug)
            if direct_inferred_cards:
                flag("direct_fanza_inferred_product_card", slug)
            named_performers = [
                item
                for item in payload.get("fanza_people") or []
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            if named_performers and not direct_performer_cards:
                flag("direct_fanza_missing_performer_works", slug)
            if (
                str(payload.get("fanza_performer_name") or "").strip()
                and direct_profile_cards == 0
            ):
                flag("direct_fanza_performer_profiles_unresolved", slug)

    critical_prefixes = (
        "invalid_draft_json",
        "quality_blocker:",
        "numeric_media_count_in_title",
        "missing_article_html",
        "related_thumbnail_in_body",
        "invalid_exact_product_thumbnail",
        "invalid_official_work_thumbnail",
        "unverified_mgs_product",
        "rendered_unverified_mgs_product",
        "multiword_fanza_search",
        "unresolved_related_fanza_product",
        "invalid_related_fanza_product_thumbnail",
        "invalid_performer_card_thumbnail",
        "profile_without_local_thumbnail",
        "profile_thumbnail_owner_mismatch",
        "duplicate_official_account_url",
        "direct_fanza_exact_product_card_count",
        "direct_fanza_inferred_product_card",
        "direct_fanza_missing_performer_works",
    )
    critical = sum(
        count
        for kind, count in issue_counts.items()
        if kind.startswith(critical_prefixes)
    )
    return {
        "drafts": len(drafts),
        "critical_issues": critical,
        "issues": dict(sorted(issue_counts.items())),
        "examples": examples,
        "quality": {
            "minimum_score": min(scores) if scores else 0,
            "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "warnings": dict(warning_counts.most_common()),
        },
        "cards": {
            "exact_product": exact_cards,
            "official_profile": profile_cards,
            "local_official_profile": local_profile_cards,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="全記事の公開前整合性を監査します")
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    args = parser.parse_args()
    result = audit_site(args.site_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["critical_issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
