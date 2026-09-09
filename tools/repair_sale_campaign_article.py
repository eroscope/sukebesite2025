from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from article_studio import add_built_article, load_draft_payload, save_draft  # noqa: E402
from indanya_desktop.related_links import (  # noqa: E402
    ensure_related_footer,
    resolve_article_destination,
)
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    prune_unreferenced_related_thumbnail_assets,
)


def repair(
    slug: str,
    campaign_url: str,
    site_root: Path,
    *,
    campaign_label: str = "セール対象作品",
) -> dict:
    payload = load_draft_payload(slug, site_root)
    source = {
        "title": payload.get("title"),
        "links": [{"url": campaign_url, "text": campaign_label}],
    }
    campaign = resolve_article_destination(payload, source, [])
    if not campaign or campaign.get("link_kind") != "exact_campaign":
        raise RuntimeError("FANZAのセール一覧URLとして確認できませんでした")

    blocks = [
        block
        for block in payload.get("blocks") or []
        if not (
            isinstance(block, dict)
            and block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in {
                "inferred_topic_product", "inferred_topic_search",
            }
        )
    ]
    blocks.append(campaign)
    payload["blocks"] = blocks
    payload["related_destinations"] = [campaign]
    ensure_related_footer(payload)
    pruned = prune_unreferenced_related_thumbnail_assets(payload)
    payload["replace_existing"] = True
    save_draft(payload, site_root)
    result = add_built_article(payload, site_root)
    return {**result, "pruned": pruned, "destination": campaign["url"]}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace a sale roundup's generic recommendation with its campaign list."
    )
    parser.add_argument("slug")
    parser.add_argument("campaign_url")
    parser.add_argument("--campaign-label", default="セール対象作品")
    parser.add_argument("--site-root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(repair(
        args.slug,
        args.campaign_url,
        args.site_root.resolve(),
        campaign_label=args.campaign_label,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
