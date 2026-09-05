#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import _SourcePageParser, add_built_article, save_draft  # noqa: E402
from indanya_desktop.affiliate_opportunities import (  # noqa: E402
    detect_affiliate_opportunities,
    mgs_product_code_from_url,
)
from indanya_desktop.related_links import (  # noqa: E402
    ensure_related_footer,
    related_link_insert_index,
    resolve_article_destinations,
    sanitize_related_destinations,
)
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
)


MAX_SOURCE_BYTES = 4 * 1024 * 1024


def download_source_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_SOURCE_BYTES + 1)
        if not data or len(data) > MAX_SOURCE_BYTES:
            return ""
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def _source_evidence(payload: dict[str, Any], source_html: str) -> dict[str, Any]:
    parser = _SourcePageParser()
    parser.feed(source_html)
    page_title = (
        parser.metadata.get("og:title")
        or next(
            (text for tag, text in parser.text_items if tag == "title" and text),
            "",
        )
        or str(payload.get("title") or "")
    )
    description = (
        parser.metadata.get("og:description")
        or parser.metadata.get("description")
        or str(payload.get("summary") or "")
    )
    return {
        "title": page_title,
        "description": description,
        "requested_url": str(payload.get("source_url") or ""),
        "url": str(payload.get("source_url") or ""),
        "affiliate_resources": parser.affiliate_resources,
        "affiliate_opportunities": payload.get("affiliate_opportunities") or [],
    }


def repair_payload(
    payload: dict[str, Any],
    source_html: str,
    *,
    thumbnail_downloader: Callable[[str], tuple[bytes, str, str]] | None = None,
    mgs_metadata_resolver: Callable[[str], dict[str, str]] | None = None,
) -> bool:
    if not source_html:
        return False
    source = _source_evidence(payload, source_html)
    opportunities = detect_affiliate_opportunities(source)
    exact_matches = [
        item
        for item in opportunities
        if item.get("article_match") is True and item.get("product_code")
    ]
    if not exact_matches:
        return False

    before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload["affiliate_opportunities"] = opportunities
    exact_codes = {
        str(item.get("product_code") or "").upper() for item in exact_matches
    }
    blocks = [
        block
        for block in (payload.get("blocks") or [])
        if not (
            isinstance(block, dict)
            and block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in {
                "inferred_topic_search",
                "person_search",
            }
        )
    ]
    payload["blocks"] = blocks
    existing_by_url = {
        str(block.get("url") or "").rstrip("/"): block
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and str(block.get("url") or "")
    }
    exact_destinations = [
        item
        for item in resolve_article_destinations(payload, source, opportunities)
        if item.get("link_kind") == "exact_official_work"
        and mgs_product_code_from_url(item.get("url")) in exact_codes
    ]
    for destination in exact_destinations:
        url_key = str(destination.get("url") or "").rstrip("/")
        destination["id"] = (
            "article-related-destination-mgs-"
            + mgs_product_code_from_url(destination.get("url")).casefold()
        )
        existing = existing_by_url.get(url_key)
        if existing is not None:
            existing.update(destination)
            continue
        insert_at = related_link_insert_index(blocks, "exact_official_work")
        blocks.insert(insert_at, destination)
        existing_by_url[url_key] = destination

    payload["suppress_generic_related_recommendation"] = True
    payload["related_destinations"] = [
        {
            key: destination.get(key)
            for key in (
                "url", "title", "provider", "link_kind", "match_confidence"
            )
        }
        for destination in exact_destinations
    ]
    sanitized = sanitize_related_destinations(payload)
    if sanitized is not payload:
        payload.clear()
        payload.update(sanitized)
    ensure_related_footer(payload)
    localize_related_thumbnail_assets(
        payload,
        downloader=thumbnail_downloader,
        mgs_metadata_resolver=mgs_metadata_resolver,
    )
    apply_related_thumbnail_fallbacks(payload)
    after = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return after != before


def repair_site(
    site_root: Path,
    *,
    apply: bool,
    slugs: set[str] | None = None,
) -> dict[str, int]:
    stats = {
        "scanned": 0,
        "source_fetched": 0,
        "exact_repaired": 0,
        "source_failed": 0,
    }
    draft_root = site_root / ".article-studio" / "drafts"
    article_root = site_root / "articles"
    for path in sorted(draft_root.glob("*.json")):
        if slugs and path.stem not in slugs:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        opportunities = payload.get("affiliate_opportunities") or []
        if not any(
            isinstance(item, dict)
            and item.get("program_id") == "mgs"
            and item.get("product_code")
            for item in opportunities
        ):
            continue
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            continue
        stats["scanned"] += 1
        try:
            source_html = download_source_html(source_url)
        except Exception:
            source_html = ""
        if not source_html:
            stats["source_failed"] += 1
            continue
        stats["source_fetched"] += 1
        if not repair_payload(payload, source_html):
            continue
        stats["exact_repaired"] += 1
        if not apply:
            continue
        save_draft(payload, site_root)
        if (
            str(payload.get("status") or "") == "published"
            or (article_root / f"{path.stem}.html").is_file()
        ):
            add_built_article(
                {
                    **payload,
                    "replace_existing": True,
                    "adult_confirmed": True,
                    "rights_confirmed": True,
                    "privacy_confirmed": True,
                    "source_confirmed": True,
                },
                site_root,
            )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    slugs = {str(value).strip() for value in args.slug if str(value).strip()} or None
    stats = repair_site(args.site_root.resolve(), apply=args.apply, slugs=slugs)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
