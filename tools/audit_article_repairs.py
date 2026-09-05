#!/usr/bin/env python3
"""Print a compact, repeatable audit for named article drafts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def audit_draft(site_root: Path, slug: str) -> dict[str, Any]:
    path = site_root / ".article-studio" / "drafts" / f"{slug}.json"
    if not path.is_file():
        return {"slug": slug, "missing": True}
    payload = json.loads(path.read_text(encoding="utf-8"))
    subject = payload.get("main_subject")
    links = []
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") not in {
            "product_cta", "related_link"
        }:
            continue
        links.append({
            "kind": str(block.get("link_kind") or block.get("match_type") or ""),
            "title": str(block.get("title") or ""),
            "url": str(block.get("url") or ""),
            "thumbnail_source_kind": str(
                block.get("thumbnail_source_kind") or ""
            ),
            "thumbnail_owner_url": str(block.get("thumbnail_owner_url") or ""),
        })
    return {
        "slug": slug,
        "title": str(payload.get("title") or ""),
        "source_url": str(payload.get("source_url") or ""),
        "subject": (
            str(subject.get("name") or "") if isinstance(subject, dict) else ""
        ),
        "images": len(payload.get("images") or []),
        "videos": len(payload.get("videos") or []),
        "links": links,
        "score": int((payload.get("quality_gate") or {}).get("score") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("slug", nargs="+")
    args = parser.parse_args()
    rows = [
        audit_draft(args.site_root.resolve(), slug.strip())
        for slug in args.slug
        if slug.strip()
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
