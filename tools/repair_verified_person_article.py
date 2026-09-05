from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from article_studio import add_built_article, load_draft_payload, save_draft  # noqa: E402
from indanya_desktop.browser_capture import discover_fanza_products  # noqa: E402
from indanya_desktop.editorial_policy import download_exact_fanza_package  # noqa: E402
from indanya_desktop.fanza_catalog import hydrate_related_fanza_products  # noqa: E402
from indanya_desktop.legacy_identity_repairs import (  # noqa: E402
    backfill_verified_main_subject_identity,
)
from indanya_desktop.related_thumbnail_assets import (  # noqa: E402
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
    prune_unreferenced_related_thumbnail_assets,
)


def repair(slug: str, site_root: Path = ROOT, *, hydrate_fanza: bool = True) -> dict:
    payload = load_draft_payload(slug, site_root)
    identity_changed = backfill_verified_main_subject_identity(payload)
    fanza_changed = False
    if hydrate_fanza:
        fanza_changed = hydrate_related_fanza_products(
            payload,
            site_root,
            lambda query: discover_fanza_products(
                [query], limit_per_query=1, product_kind="video"
            ),
            lambda product_id: str(
                (download_exact_fanza_package(product_id.casefold()) or {}).get(
                    "url", ""
                )
            ),
        )
    thumbnails_changed = localize_related_thumbnail_assets(payload)
    fallback_changed = apply_related_thumbnail_fallbacks(payload)
    pruned = prune_unreferenced_related_thumbnail_assets(payload)
    payload["replace_existing"] = True
    save_draft(payload, site_root)
    result = add_built_article(payload, site_root)
    return {
        **result,
        "identity_changed": identity_changed,
        "fanza_changed": fanza_changed,
        "thumbnails_changed": thumbnails_changed,
        "fallback_changed": fallback_changed,
        "pruned": pruned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair a verified single-person article and rebuild it."
    )
    parser.add_argument("slug")
    parser.add_argument("--site-root", type=Path, default=ROOT)
    parser.add_argument("--skip-fanza", action="store_true")
    args = parser.parse_args()
    result = repair(
        args.slug,
        args.site_root.resolve(),
        hydrate_fanza=not args.skip_fanza,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
