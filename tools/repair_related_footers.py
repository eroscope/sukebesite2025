#!/usr/bin/env python3
"""Replace legacy empty related-ad shells in drafts and rendered articles."""

from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import (  # noqa: E402
    FANZA_PRODUCT_STYLE,
    add_built_article,
    _render_product_cta_block,
    _render_related_link_block,
    _render_sidebar_related_section,
    save_draft,
)
from indanya_desktop.fanza_affiliate import (  # noqa: E402
    bind_payload_fanza_affiliate_links,
    load_fanza_settings,
)
from indanya_desktop.related_links import (  # noqa: E402
    ensure_related_footer,
    is_empty_related_ad,
    sanitize_related_destinations,
)
from indanya_desktop.affiliate_opportunities import mgs_product_code_from_url  # noqa: E402


FOOTER_RECOMMENDATION_KINDS = {
    "inferred_topic_search",
    "person_search",
    "verified_person_search",
}
BODY_PLACEHOLDER = re.compile(
    r'<div class="ad">\s*PR<br>\s*(?:記事内容に合う)?関連広告枠\s*</div>',
    re.IGNORECASE,
)
SIDEBAR_PLACEHOLDER = re.compile(
    r'<section class="sidebox"><h2 class="side-title">PR</h2>\s*'
    r'<div class="sidebody"><div class="side-ad">関連広告枠</div></div></section>',
    re.IGNORECASE,
)
SIDEBAR_RELATED_SECTION = re.compile(
    r'<section class="sidebox(?: fanza-product)?"[^>]*>'
    r'<h2 class="side-title">(?:PR|関連リンク)</h2>'
    r'<div class="sidebody"><a class="side-ad side-ad-link[^"]*"[\s\S]*?'
    r'</a></div></section>',
    re.IGNORECASE,
)
FIRST_ARTICLE_IMAGE = re.compile(
    r'<img class="zoomable" src="([^"]+)"',
    re.IGNORECASE,
)
OFFICIAL_ACCOUNT_CARD = re.compile(
    r'<aside class="article-destination" '
    r'data-link-kind="(?:official_profile|official_content)"[^>]*>[\s\S]*?</aside>',
    re.IGNORECASE,
)
ARTICLE_DESTINATION_CARD = re.compile(
    r'<aside class="article-destination[^>]*>[\s\S]*?</aside>',
    re.IGNORECASE,
)
SIDEBAR_THUMB_STYLE = r'''
.side-ad-link-thumb {
  display: block;
  width: 100%;
  max-height: 220px;
  margin-bottom: 10px;
  object-fit: contain;
  background: #fff;
}
'''


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("draft payload must be an object")
    return payload


def _write_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="")
    temporary.replace(path)


def _footer_blocks(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recommendations: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if (
            block.get("type") == "product_cta"
            and str(block.get("id") or "") == "article-related-footer-product"
        ):
            recommendations.append(block)
        elif (
            block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in FOOTER_RECOMMENDATION_KINDS
        ):
            recommendations.append(block)
        elif (
            block.get("type") == "related_link"
            and str(block.get("id") or "").startswith("article-related-footer-profile-")
        ):
            profiles.append(block)
    return recommendations, profiles


def _strip_generated_footer_cards(source: str) -> tuple[str, int]:
    """Remove only the generated cards immediately before the editorial note."""
    marker_index = _footer_marker_index(source)
    if marker_index < 0:
        return source, 0

    updated = source
    removed = 0
    cursor = marker_index
    while True:
        prefix = updated[:cursor]
        trimmed = prefix.rstrip()
        if not trimmed.endswith("</aside>"):
            break
        close_end = len(trimmed)
        open_index = trimmed.rfind("<aside")
        if open_index < 0:
            break
        snippet = trimmed[open_index:close_end]
        is_generated = (
            'data-pr-id="article-related-footer-product"' in snippet
            or 'data-pr-id="article-related-footer-recommendation' in snippet
            or any(
                f'data-link-kind="{kind}"' in snippet
                for kind in (
                    *FOOTER_RECOMMENDATION_KINDS,
                    "official_profile",
                    "official_content",
                )
            )
        )
        if not is_generated:
            break
        updated = updated[:open_index] + updated[cursor:]
        cursor = open_index
        removed += 1
    return updated, removed


def _footer_marker_index(source: str) -> int:
    markers = (
        '<div class="editorial-note">',
        '<div class="source">',
    )
    indexes = [source.find(marker) for marker in markers]
    valid = [index for index in indexes if index >= 0]
    return min(valid) if valid else -1


def _strip_disallowed_mgs_cards(
    source: str,
    payload: dict[str, Any],
) -> tuple[str, int]:
    allowed_codes = {
        mgs_product_code_from_url(str(block.get("url") or ""))
        for block in payload.get("blocks") or []
        if isinstance(block, dict)
    }
    allowed_codes.discard("")
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        snippet = html.unescape(match.group(0))
        code = next((
            mgs_product_code_from_url(url)
            for url in re.findall(r'href=["\']([^"\']+)', snippet, re.IGNORECASE)
            if mgs_product_code_from_url(url)
        ), "")
        if code and code not in allowed_codes:
            removed += 1
            return ""
        return match.group(0)

    return ARTICLE_DESTINATION_CARD.sub(replace, source), removed


def _with_rendered_thumbnail(
    block: dict[str, Any],
    thumbnail_url: str,
) -> dict[str, Any]:
    rendered = dict(block)
    link_kind = str(rendered.get("link_kind") or "")
    if rendered.get("type") == "product_cta" or link_kind in {
        "official_profile",
        "official_content",
    }:
        return rendered
    if thumbnail_url and not rendered.get("thumbnail_url"):
        rendered.pop("thumbnail_image_id", None)
        rendered["thumbnail_url"] = thumbnail_url
    return rendered


def repair_rendered_article(
    source: str,
    payload: dict[str, Any],
    affiliate_id: str,
) -> tuple[str, dict[str, int]]:
    working = json.loads(json.dumps(payload, ensure_ascii=False))
    working = sanitize_related_destinations(working)
    ensure_related_footer(working)
    working = bind_payload_fanza_affiliate_links(
        working,
        affiliate_id,
        require_configured=False,
    )
    recommendations, profiles = _footer_blocks(working)
    recommendation = recommendations[0] if recommendations else None
    image_match = FIRST_ARTICLE_IMAGE.search(source)
    thumbnail_url = html.unescape(image_match.group(1)) if image_match else ""

    updated, removed_mgs_cards = _strip_disallowed_mgs_cards(source, working)
    updated, removed_footer_cards = _strip_generated_footer_cards(updated)
    updated, original_profiles_replaced = OFFICIAL_ACCOUNT_CARD.subn("", updated)

    fragments: list[str] = []
    if recommendation is not None:
        rendered_recommendation = _with_rendered_thumbnail(
            recommendation,
            thumbnail_url,
        )
        if rendered_recommendation.get("type") == "product_cta":
            fragments.append(
                _render_product_cta_block(
                    rendered_recommendation,
                    {},
                    preview=False,
                )
            )
        else:
            fragments.append(
                _render_related_link_block(
                    rendered_recommendation,
                    {},
                    preview=False,
                )
            )

    added_profiles = 0
    for profile in profiles:
        url = str(profile.get("url") or "")
        if not url:
            continue
        rendered_profile = _with_rendered_thumbnail(profile, thumbnail_url)
        fragments.append(
            _render_related_link_block(rendered_profile, {}, preview=False)
        )
        added_profiles += 1

    footer_markup = "".join(fragments)
    updated, body_replacements = BODY_PLACEHOLDER.subn(
        footer_markup,
        updated,
        count=1,
    )
    inserted_footer = 0
    if not body_replacements and footer_markup:
        index = _footer_marker_index(updated)
        if index >= 0:
            updated = updated[:index] + footer_markup + updated[index:]
            inserted_footer = 1

    sidebar_markup = ""
    if recommendation is not None:
        sidebar_recommendation = {
            **_with_rendered_thumbnail(recommendation, thumbnail_url),
            "id": f'{str(recommendation.get("id") or "related")}-sidebar',
        }
        sidebar_markup = _render_sidebar_related_section(sidebar_recommendation)
    rendered_sidebar = SIDEBAR_RELATED_SECTION.search(updated)
    if rendered_sidebar and rendered_sidebar.group(0) == sidebar_markup:
        sidebar_replacements = 0
    else:
        updated, sidebar_replacements = SIDEBAR_RELATED_SECTION.subn(
            sidebar_markup,
            updated,
            count=1,
        )
    if not sidebar_replacements:
        updated, sidebar_replacements = SIDEBAR_PLACEHOLDER.subn(
            sidebar_markup,
            updated,
            count=1,
        )

    style_added = 0
    if updated != source and ".side-ad-link-thumb {" not in updated:
        style_to_add = (
            SIDEBAR_THUMB_STYLE
            if ".side-ad.side-ad-link {" in updated
            else FANZA_PRODUCT_STYLE
        )
        updated, style_added = re.subn(
            r"</style>",
            style_to_add + "\n</style>",
            updated,
            count=1,
            flags=re.IGNORECASE,
        )

    return updated, {
        "body_replacements": body_replacements,
        "footer_cards_replaced": removed_footer_cards,
        "disallowed_mgs_cards_removed": removed_mgs_cards,
        "inserted_footers": inserted_footer,
        "sidebar_replacements": sidebar_replacements,
        "profiles_added": added_profiles,
        "original_profiles_replaced": original_profiles_replaced,
        "styles_added": style_added,
    }


def repair_drafts(
    site_root: Path,
    *,
    apply: bool,
    slugs: set[str] | None = None,
    changed_slugs: set[str] | None = None,
) -> Counter[str]:
    stats: Counter[str] = Counter()
    draft_root = site_root / ".article-studio" / "drafts"
    for path in sorted(draft_root.glob("*.json")):
        if slugs and path.stem not in slugs:
            continue
        stats["drafts_scanned"] += 1
        payload = _load_payload(path)
        sanitized = sanitize_related_destinations(payload)
        changed = sanitized != payload
        payload = sanitized
        placeholders_before = sum(
            is_empty_related_ad(block) for block in payload.get("blocks") or []
        )
        changed = ensure_related_footer(payload) or changed
        recommendations, profiles = _footer_blocks(payload)
        stats["draft_placeholders_removed"] += placeholders_before
        stats["draft_recommendations"] += bool(recommendations)
        stats["drafts_with_footer_profiles"] += bool(profiles)
        if not changed:
            continue
        stats["drafts_changed"] += 1
        if changed_slugs is not None:
            changed_slugs.add(path.stem)
        if apply:
            save_draft(payload, site_root)
    return stats


def repair_rendered_articles(
    site_root: Path,
    article_root: Path,
    *,
    apply: bool,
    slugs: set[str] | None = None,
) -> Counter[str]:
    stats: Counter[str] = Counter()
    draft_root = site_root / ".article-studio" / "drafts"
    affiliate_id = str(load_fanza_settings(site_root).get("affiliate_id") or "")
    for article_path in sorted(article_root.glob("*.html")):
        if article_path.name == "pool-look-back.html":
            continue
        if slugs and article_path.stem not in slugs:
            continue
        stats["articles_scanned"] += 1
        draft_path = draft_root / f"{article_path.stem}.json"
        if not draft_path.is_file():
            stats["articles_without_draft"] += 1
            continue
        payload = _load_payload(draft_path)
        source = article_path.read_text(encoding="utf-8")
        updated, item_stats = repair_rendered_article(source, payload, affiliate_id)
        stats.update(item_stats)
        if updated == source:
            continue
        stats["articles_changed"] += 1
        if apply:
            _write_atomic(article_path, updated)
    return stats


def rebuild_changed_published_articles(
    site_root: Path,
    *,
    apply: bool,
    slugs: set[str] | None = None,
) -> Counter[str]:
    """Rebuild published files from sanitized drafts instead of patching stale HTML."""
    stats: Counter[str] = Counter()
    if not apply or not slugs:
        return stats
    draft_root = site_root / ".article-studio" / "drafts"
    article_root = site_root / "articles"
    for draft_path in sorted(draft_root.glob("*.json")):
        slug = draft_path.stem
        if slug not in slugs:
            continue
        payload = sanitize_related_destinations(_load_payload(draft_path))
        if (
            not (article_root / f"{slug}.html").is_file()
            and str(payload.get("status") or "") != "published"
        ):
            continue
        ensure_related_footer(payload)
        published = {
            **payload,
            "adult_confirmed": True,
            "rights_confirmed": True,
            "privacy_confirmed": True,
            "source_confirmed": True,
            "replace_existing": True,
        }
        add_built_article(published, site_root)
        stats["articles_rebuilt"] += 1
    return stats


def missing_published_slugs(site_root: Path) -> set[str]:
    draft_root = site_root / ".article-studio" / "drafts"
    article_root = site_root / "articles"
    missing: set[str] = set()
    for path in sorted(draft_root.glob("*.json")):
        payload = _load_payload(path)
        slug = str(payload.get("slug") or "").strip()
        if (
            slug
            and str(payload.get("status") or "") == "published"
            and not (article_root / f"{slug}.html").is_file()
        ):
            missing.add(slug)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--article-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--rebuild-missing-published",
        action="store_true",
        help="公開済み下書きがあるのにローカルHTMLがない記事を再構築します",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="指定した記事だけを修復します。複数回指定できます",
    )
    args = parser.parse_args()
    site_root = args.site_root.resolve()
    article_root = (
        args.article_root.resolve()
        if args.article_root
        else site_root / "articles"
    )
    slugs = {str(value).strip() for value in args.slug if str(value).strip()} or None
    changed_slugs: set[str] = set()
    stats = repair_drafts(
        site_root,
        apply=args.apply,
        slugs=slugs,
        changed_slugs=changed_slugs,
    )
    if args.apply:
        rebuild_slugs = set(slugs or ()) | changed_slugs
        if args.rebuild_missing_published:
            rebuild_slugs.update(missing_published_slugs(site_root))
        stats.update(rebuild_changed_published_articles(
            site_root,
            apply=True,
            slugs=rebuild_slugs,
        ))
    else:
        stats.update(repair_rendered_articles(
            site_root,
            article_root,
            apply=False,
            slugs=slugs,
        ))
    print(json.dumps(dict(sorted(stats.items())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
