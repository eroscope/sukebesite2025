from __future__ import annotations

import argparse
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import (
    add_built_article,
    normalize_article_title_label,
    save_draft,
)
from indanya_desktop.adaptive_quality import apply_quality_gate
from indanya_desktop.browser_capture import (
    capture_rendered_source,
    discover_fanza_products,
)
from indanya_desktop.editorial_policy import (
    canonical_fanza_product_url,
    download_exact_fanza_package,
    fanza_product_id,
    is_fanza_package_image,
)
from indanya_desktop.related_links import (
    _fallback_footer_recommendation,
    apply_official_social_destinations,
    ensure_related_footer,
)
from indanya_desktop.fanza_catalog import (
    hydrate_related_fanza_products,
    prefetch_related_fanza_products,
)
from indanya_desktop.legacy_identity_repairs import (
    backfill_verified_main_subject_identity,
)
from indanya_desktop.related_thumbnail_assets import (
    apply_related_thumbnail_fallbacks,
    download_related_thumbnail,
    localize_related_thumbnail_assets,
    prune_unreferenced_related_thumbnail_assets,
)
from indanya_desktop.social_profiles import (
    canonical_social_profile_url,
    enrich_source_profile_thumbnails,
    fetch_profile_thumbnail,
    merge_verified_social_profiles,
    registry_profiles_for_payload,
)
from indanya_desktop.site_discovery import refresh_site_discovery
from indanya_desktop.sitemap_health import validate_local_sitemaps


VALID_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,99}")
CACHE_VERSION = 1
MEDIA_PRODUCT_CODE_RE = re.compile(
    r"(?<![a-z0-9])([a-z]{2,12})[-_](\d{3,6})(?!\d|[xX]\d)", re.I
)
NON_PRODUCT_MEDIA_PREFIXES = {
    "ad", "advert", "archive", "banner", "blog", "content", "detail",
    "entry", "fc", "file", "image", "images", "img", "imgs", "item",
    "media", "page", "photo", "post", "sample", "sns", "thumb",
    "thumbnail", "wp",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_public_url(value: Any) -> str:
    raw = str(value or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = "/" + parts.path.strip("/") if parts.path.strip("/") else "/"
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + "/", "", ""))


def infer_public_url(site_root: Path, explicit: str = "") -> str:
    """Find the canonical site URL without tying migrations to one site."""
    owner = _load_json(site_root / ".article-studio" / "analytics-owner-v2.json")
    for value in (explicit, owner.get("public_url")):
        normalized = _normalize_public_url(value)
        if normalized:
            return normalized
    return ""


def refresh_rebuilt_site_discovery(
    site_root: Path,
    *,
    public_url: str = "",
) -> dict[str, Any]:
    """Regenerate and verify every search-discovery artifact after bulk builds."""
    resolved_url = infer_public_url(site_root, public_url)
    if not resolved_url:
        return {
            "status": "skipped",
            "reason": "公開URLを特定できませんでした",
        }
    database_path = site_root / "data" / "articles.json"
    try:
        articles = json.loads(database_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("公開記事一覧 data/articles.json を読み込めません") from exc
    if not isinstance(articles, list):
        raise RuntimeError("公開記事一覧 data/articles.json の形式が正しくありません")
    counts = refresh_site_discovery(site_root, resolved_url, articles)
    health = validate_local_sitemaps(site_root, resolved_url)
    return {
        "status": "healthy",
        "public_url": resolved_url,
        "counts": counts,
        "health": health,
    }


def _quality_state(payload: dict[str, Any]) -> str:
    gate = dict(payload.get("quality_gate") or {})
    gate.pop("assessed_at", None)
    return json.dumps(
        {
            "quality_gate": gate,
            "review_status": payload.get("review_status"),
            "review_message": payload.get("review_message"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _load_cache(path: Path) -> dict[str, dict[str, str]]:
    value = _load_json(path)
    if value.get("version") != CACHE_VERSION:
        return {"packages": {}, "profiles": {}, "official_pages": {}}
    return {
        "packages": {
            str(key): str(item)
            for key, item in (value.get("packages") or {}).items()
        },
        "profiles": {
            str(key): str(item)
            for key, item in (value.get("profiles") or {}).items()
        },
        "official_pages": {
            str(key): str(item)
            for key, item in (value.get("official_pages") or {}).items()
        },
    }


def _save_cache(path: Path, cache: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": CACHE_VERSION, **cache}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _exact_product_urls(payload: dict[str, Any]) -> set[str]:
    return {
        canonical_fanza_product_url(str(block.get("url") or ""))
        for block in payload.get("blocks") or []
        if isinstance(block, dict)
        and block.get("type") == "product_cta"
        and str(block.get("match_type") or "").startswith("exact_")
        and canonical_fanza_product_url(str(block.get("url") or ""))
    }


def _exact_official_work_urls(payload: dict[str, Any]) -> set[str]:
    return {
        str(block.get("url") or "").strip()
        for block in payload.get("blocks") or []
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and block.get("link_kind") == "exact_official_work"
        and str(block.get("url") or "").strip()
    }


def _related_fanza_queries(payload: dict[str, Any]) -> set[str]:
    queries: set[str] = set()
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            continue
        link_kind = str(block.get("link_kind") or "")
        if link_kind in {"inferred_topic_search", "inferred_topic_product"}:
            query = str(block.get("search_query") or "").strip()
        elif link_kind == "verified_person_search":
            query = str(block.get("person_name") or "").strip()
            if not query:
                query = re.sub(
                    r"の出演作品(?:一覧)?$", "", str(block.get("title") or "")
                ).strip()
        else:
            continue
        if query:
            queries.add(query)
    return queries


def _selected_media_product_codes(
    payload: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    selected_images = {
        str(media_id)
        for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "images"
        for media_id in block.get("image_ids") or []
    }
    selected_videos = {
        str(media_id)
        for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "videos"
        for media_id in block.get("video_ids") or []
    }
    matches: dict[str, dict[str, list[str]]] = {}
    for kind, items, selected in (
        ("image", payload.get("images") or [], selected_images),
        ("video", payload.get("videos") or [], selected_videos),
    ):
        for item in items:
            if not isinstance(item, dict):
                continue
            media_id = str(item.get("id") or "")
            if media_id not in selected or item.get("related_thumbnail_only") is True:
                continue
            values = " ".join(
                str(item.get(key) or "")
                for key in ("url", "source_url", "poster", "thumbnail_url")
            )
            codes = {
                f"{match.group(1).upper()}-{match.group(2)}"
                for match in MEDIA_PRODUCT_CODE_RE.finditer(values)
                if match.group(1).casefold() not in NON_PRODUCT_MEDIA_PREFIXES
                and set(match.group(2)) != {"0"}
            }
            if len(codes) != 1:
                continue
            code = next(iter(codes))
            entry = matches.setdefault(code, {"image_ids": [], "video_ids": []})
            entry[f"{kind}_ids"].append(media_id)
    return matches


def _single_selected_media_product_codes(payload: dict[str, Any]) -> set[str]:
    matches = _selected_media_product_codes(payload)
    return set(matches) if len(matches) == 1 else set()


def _performer_fallback_queries(payload: dict[str, Any]) -> set[str]:
    if not any(
        isinstance(block, dict)
        and block.get("type") == "related_link"
        and block.get("link_kind") == "verified_person_search"
        for block in payload.get("blocks") or []
    ):
        return set()
    query = str(_fallback_footer_recommendation(payload).get("search_query") or "").strip()
    return {query} if query else set()


def repair_selected_media_exact_product(
    payload: dict[str, Any], related_products: dict[str, dict[str, Any]]
) -> bool:
    """Promote one verified filename product code to an exact inline work card."""
    matches = _selected_media_product_codes(payload)
    if len(matches) != 1:
        return False
    product_code, media = next(iter(matches.items()))
    product = related_products.get(product_code.casefold()) or {}
    destination = canonical_fanza_product_url(str(product.get("url") or ""))
    product_id = fanza_product_id(destination)
    normalized_code = re.sub(r"[^a-z0-9]", "", product_code.casefold())
    if not destination or re.sub(r"[^a-z0-9]", "", product_id.casefold()) != normalized_code:
        return False
    if destination in _exact_product_urls(payload):
        return False
    thumbnail = str(product.get("thumbnail_url") or "").strip()
    if not is_fanza_package_image({"url": thumbnail}, product_id):
        return False
    image_ids = set(media["image_ids"])
    video_ids = set(media["video_ids"])
    blocks = payload.get("blocks") or []
    insert_after = next((
        index
        for index, block in enumerate(blocks)
        if isinstance(block, dict)
        and (
            (block.get("type") == "images" and image_ids.intersection(
                str(value) for value in block.get("image_ids") or []
            ))
            or (block.get("type") == "videos" and video_ids.intersection(
                str(value) for value in block.get("video_ids") or []
            ))
        )
    ), None)
    if insert_after is None:
        return False
    match_type = "exact_video" if video_ids else "exact_image"
    placement = "この動画の商品" if video_ids else "この画像の商品"
    blocks.insert(insert_after + 1, {
        "id": f"fanza-filename-product-{product_id.casefold()}",
        "type": "product_cta",
        "url": destination,
        "title": str(product.get("title") or product_code)[:180],
        "text": "上の素材に対応する作品です。作品ページでサンプル、出演者、配信内容を確認できます。",
        "button_text": "FANZAでこの作品を見る",
        "thumbnail_url": thumbnail,
        "thumbnail_source_kind": "fanza_package",
        "thumbnail_owner_url": destination,
        "placement_label": placement,
        "match_type": match_type,
        "match_evidence": "本文採用素材のファイル名にある品番をFANZA商品IDと完全一致確認",
        "match_confidence": 98,
    })
    return True


def repair_exact_product_card_thumbnails(
    payload: dict[str, Any], package_urls: dict[str, str]
) -> bool:
    """Replace legacy article thumbnails with the exact work's package image."""
    changed = False
    for block in payload.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("type") != "product_cta"
            or not str(block.get("match_type") or "").startswith("exact_")
        ):
            continue
        product_url = canonical_fanza_product_url(str(block.get("url") or ""))
        product_id = fanza_product_id(product_url)
        thumbnail = package_urls.get(product_url) or package_urls.get(product_id)
        if not thumbnail:
            continue
        updates = {
            "thumbnail_url": thumbnail,
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": product_url,
        }
        for key, value in updates.items():
            if block.get(key) != value:
                block[key] = value
                changed = True
        if block.pop("thumbnail_image_id", None):
            changed = True
    return changed


def repair_official_work_card_thumbnails(
    payload: dict[str, Any], page_thumbnails: dict[str, str]
) -> bool:
    """Use only the exact destination page's own image for official-work cards."""
    images = {
        str(item.get("id") or ""): item
        for item in payload.get("images") or []
        if isinstance(item, dict) and item.get("id")
    }
    changed = False
    for block in payload.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("type") != "related_link"
            or block.get("link_kind") != "exact_official_work"
        ):
            continue
        destination = str(block.get("url") or "").strip()
        if not destination:
            continue
        existing = images.get(str(block.get("thumbnail_image_id") or ""))
        local_is_exact_page = bool(
            isinstance(existing, dict)
            and existing.get("related_thumbnail_only") is True
            and str(existing.get("rights_basis") or "")
            == "official_page_thumbnail"
            and str(existing.get("thumbnail_owner_url") or "").rstrip("/")
            == destination.rstrip("/")
        )
        if local_is_exact_page:
            updates = {
                "thumbnail_source_kind": "official_page",
                "thumbnail_owner_url": destination,
            }
            for key, value in updates.items():
                if block.get(key) != value:
                    block[key] = value
                    changed = True
            if block.pop("thumbnail_url", None):
                changed = True
            continue

        thumbnail = str(page_thumbnails.get(destination) or "").strip()
        if thumbnail:
            updates = {
                "thumbnail_url": thumbnail,
                "thumbnail_source_kind": "official_page",
                "thumbnail_owner_url": destination,
            }
            for key, value in updates.items():
                if block.get(key) != value:
                    block[key] = value
                    changed = True
            if block.pop("thumbnail_image_id", None):
                changed = True
            continue

        for key in (
            "thumbnail_image_id",
            "thumbnail_url",
            "thumbnail_source_kind",
            "thumbnail_owner_url",
        ):
            if block.pop(key, None) is not None:
                changed = True
    return changed


def repair_video_thumbnail_only_flag(payload: dict[str, Any]) -> bool:
    """Mark a lone unplaced image as the intentional thumbnail for video posts."""
    videos = [
        item for item in payload.get("videos") or []
        if isinstance(item, dict) and item.get("id")
    ]
    thumbnail_id = str(payload.get("thumbnail_id") or "")
    if not videos or not thumbnail_id:
        return False
    article_image_ids = {
        str(item.get("id") or "")
        for item in payload.get("images") or []
        if isinstance(item, dict)
        and item.get("related_thumbnail_only") is not True
        and item.get("id")
    }
    placed_image_ids = {
        str(image_id)
        for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "images"
        for image_id in block.get("image_ids") or []
        if image_id
    }
    if article_image_ids.difference(placed_image_ids) != {thumbnail_id}:
        return False
    if payload.get("thumbnail_only") is True:
        return False
    payload["thumbnail_only"] = True
    return True


def _existing_official_profiles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    subject = payload.get("main_subject")
    subject_name = (
        str(subject.get("name") or "").strip()
        if isinstance(subject, dict) and subject.get("kind") == "person" else ""
    )
    profiles: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in payload.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("type") != "related_link"
            or block.get("link_kind") != "official_profile"
        ):
            continue
        service = str(block.get("provider") or "").strip().casefold()
        url = canonical_social_profile_url(service, block.get("url"))
        if not service or not url or (service, url) in seen:
            continue
        seen.add((service, url))
        profiles.append({
            "name": (
                subject_name
                or str(block.get("person_name") or "").strip()
                or str(block.get("title") or "本人").split("の", 1)[0]
            ),
            "role": str(subject.get("role") or "") if isinstance(subject, dict) else "",
            "service": service,
            "url": url,
            "is_main_subject": True,
            "reason": str(block.get("match_evidence") or "既存記事で確認済みの公式プロフィール"),
            "confidence": int(block.get("match_confidence") or 90),
        })
    return profiles


def verified_profiles_for_payload(
    site_root: Path, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    return merge_verified_social_profiles([
        *registry_profiles_for_payload(site_root, payload),
        *_existing_official_profiles(payload),
    ])


def _resolve_package_thumbnail(product_url: str) -> str:
    product_id = fanza_product_id(product_url)
    if not product_id:
        return ""
    package = download_exact_fanza_package(product_id)
    if package and is_fanza_package_image(package, product_id):
        return str(package.get("url") or "")
    try:
        source = capture_rendered_source(product_url)
    except Exception:
        return ""
    candidates = [
        str(item.get("url") or item.get("source_url") or "")
        for item in source.get("images") or []
        if isinstance(item, dict) and is_fanza_package_image(item, product_id)
    ]
    return candidates[0] if candidates else ""


def _resolve_official_page_thumbnail(
    page_url: str,
    *,
    static_fetcher: Callable[[str], str] = fetch_profile_thumbnail,
    rendered_fetcher: Callable[[str], dict[str, Any]] = capture_rendered_source,
) -> str:
    """Resolve a page-owned hero image, including JavaScript-rendered pages."""
    static_thumbnail = str(static_fetcher(page_url) or "").strip()
    if static_thumbnail:
        return static_thumbnail
    try:
        source = rendered_fetcher(page_url)
    except Exception:
        return ""
    candidates: list[tuple[int, int, str]] = []
    for index, item in enumerate(source.get("images") or []):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("url") or item.get("source_url") or "").strip()
        if not image_url:
            continue
        image_key = image_url.casefold()
        if re.search(r"(?:loader|loading|spinner|sprite|favicon|logo|tracking|pixel|/ads?/)", image_key):
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width < 240 or height < 180:
            continue
        score = min(width * height // 10_000, 160)
        if item.get("browser_visible") is True:
            score += 80
        if item.get("inside_article") is True:
            score += 50
        if width >= 600:
            score += 35
        if height >= 300:
            score += 25
        link_url = str(item.get("browser_link_url") or "").rstrip("/")
        if not link_url or link_url == page_url.rstrip("/"):
            score += 20
        candidates.append((score, -index, image_url))
    return max(candidates, default=(0, 0, ""))[2]


def _prefetch(
    values: set[str],
    target: dict[str, str],
    resolver: Callable[[str], str],
    *,
    workers: int,
    retry_missing: bool,
) -> None:
    missing = [
        value for value in sorted(values)
        if value not in target or (retry_missing and not target.get(value))
    ]
    if not missing:
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(resolver, value): value for value in missing}
        for future in as_completed(futures):
            value = futures[future]
            try:
                target[value] = str(future.result() or "")
            except Exception:
                target[value] = ""


def migrate_legacy_article_cards(
    site_root: Path,
    *,
    apply: bool = False,
    rebuild: bool = False,
    retry_missing: bool = False,
    slugs: set[str] | None = None,
    public_url: str = "",
) -> dict[str, Any]:
    draft_root = site_root / ".article-studio" / "drafts"
    drafts: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(draft_root.glob("*.json")):
        if not VALID_SLUG.fullmatch(path.stem) or (slugs and path.stem not in slugs):
            continue
        payload = _load_json(path)
        if payload:
            drafts.append((path, payload))

    cache_path = site_root / ".article-studio" / "legacy-card-cache.json"
    cache = _load_cache(cache_path)
    product_urls = {
        url for _path, payload in drafts for url in _exact_product_urls(payload)
    }
    profile_urls = {
        str(profile.get("url") or "")
        for _path, payload in drafts
        for profile in verified_profiles_for_payload(site_root, payload)
        if str(profile.get("url") or "") and not profile.get("thumbnail_url")
    }
    official_page_urls = {
        url for _path, payload in drafts for url in _exact_official_work_urls(payload)
    }
    related_queries = sorted({
        query for _path, payload in drafts for query in _related_fanza_queries(payload)
    } | {
        code
        for _path, payload in drafts
        for code in _single_selected_media_product_codes(payload)
    } | {
        query
        for _path, payload in drafts
        for query in _performer_fallback_queries(payload)
    })
    related_products = prefetch_related_fanza_products(
        site_root,
        related_queries,
        lambda queries: discover_fanza_products(
            queries,
            limit_per_query=1,
            product_kind="video",
            max_queries=max(1, len(queries)),
            dedupe_across_queries=False,
        ),
        lambda product_id: str(
            (download_exact_fanza_package(product_id) or {}).get("url", "")
        ),
        force=retry_missing,
    )
    _prefetch(
        product_urls,
        cache["packages"],
        _resolve_package_thumbnail,
        workers=12,
        retry_missing=retry_missing,
    )
    _prefetch(
        profile_urls,
        cache["profiles"],
        fetch_profile_thumbnail,
        workers=4,
        retry_missing=retry_missing,
    )
    _prefetch(
        official_page_urls,
        cache["official_pages"],
        _resolve_official_page_thumbnail,
        # Playwright browser instances can deadlock when this migration opens
        # several dynamic official pages concurrently on Windows.
        workers=1,
        retry_missing=retry_missing,
    )
    if apply:
        _save_cache(cache_path, cache)

    cache_lock = threading.Lock()
    asset_download_cache: dict[str, tuple[bytes, str, str]] = {}

    def cached_profile_fetch(url: str) -> str:
        with cache_lock:
            if url in cache["profiles"]:
                return cache["profiles"][url]
        value = _resolve_official_page_thumbnail(url)
        with cache_lock:
            cache["profiles"][url] = value
        return value

    def cached_official_page_fetch(url: str) -> str:
        with cache_lock:
            if url in cache["official_pages"]:
                return cache["official_pages"][url]
        value = fetch_profile_thumbnail(url)
        with cache_lock:
            cache["official_pages"][url] = value
        return value

    def cached_asset_download(url: str) -> tuple[bytes, str, str]:
        with cache_lock:
            cached = asset_download_cache.get(url)
        if cached is not None:
            return cached
        value = download_related_thumbnail(url)
        with cache_lock:
            asset_download_cache[url] = value
        return value

    changed_slugs: list[str] = []
    rebuilt_slugs: list[str] = []
    failures: list[dict[str, str]] = []
    unresolved_products: set[str] = set()
    unresolved_profiles: set[str] = set()
    for _path, payload in drafts:
        normalized_title = normalize_article_title_label(payload.get("title"))
        changed = normalized_title != str(payload.get("title") or "")
        if changed:
            payload["title"] = normalized_title
        changed = (
            repair_exact_product_card_thumbnails(payload, cache["packages"])
            or changed
        )
        changed = repair_selected_media_exact_product(
            payload, related_products
        ) or changed
        changed = hydrate_related_fanza_products(
            payload,
            site_root,
            lambda query: [related_products[query.casefold()]]
            if query.casefold() in related_products
            else [],
        ) or changed
        changed = repair_video_thumbnail_only_flag(payload) or changed
        changed = backfill_verified_main_subject_identity(payload) or changed
        profiles = verified_profiles_for_payload(site_root, payload)
        if profiles:
            source = {
                "ai_main_subject": payload.get("main_subject") or {},
                "verified_social_profiles": profiles,
            }
            enrich_source_profile_thumbnails(
                site_root, source, fetcher=cached_profile_fetch
            )
            enriched = source.get("verified_social_profiles") or []
            if enriched:
                payload["verified_social_profiles"] = enriched
                changed = apply_official_social_destinations(payload, enriched) or changed
        # Footer cleanup is not profile-specific. Legacy FANZA drafts without a
        # known social account can also contain a duplicated exact-product CTA.
        changed = ensure_related_footer(payload) or changed
        changed = hydrate_related_fanza_products(
            payload,
            site_root,
            lambda query: [related_products[query.casefold()]]
            if query.casefold() in related_products
            else [],
        ) or changed
        for official_url in _exact_official_work_urls(payload):
            if official_url not in cache["official_pages"] or (
                retry_missing and not cache["official_pages"].get(official_url)
            ):
                cached_official_page_fetch(official_url)
        changed = repair_official_work_card_thumbnails(
            payload, cache["official_pages"]
        ) or changed
        changed = localize_related_thumbnail_assets(
            payload, downloader=cached_asset_download
        ) or changed
        changed = apply_related_thumbnail_fallbacks(payload) or changed
        changed = prune_unreferenced_related_thumbnail_assets(payload) or changed
        previous_quality = _quality_state(payload)
        apply_quality_gate(site_root, payload, persist=False)
        changed = _quality_state(payload) != previous_quality or changed

        for url in _exact_product_urls(payload):
            if not cache["packages"].get(url):
                unresolved_products.add(url)
        for profile in verified_profiles_for_payload(site_root, payload):
            url = str(profile.get("url") or "")
            if url and not profile.get("thumbnail_url") and not cache["profiles"].get(url):
                unresolved_profiles.add(url)
        if changed:
            changed_slugs.append(str(payload.get("slug") or _path.stem))
        if not apply or (not changed and not rebuild):
            continue
        try:
            slug = (
                save_draft(payload, site_root)
                if changed else str(payload.get("slug") or _path.stem)
            )
            if rebuild:
                payload["replace_existing"] = True
                add_built_article(payload, site_root)
                rebuilt_slugs.append(slug)
        except Exception as exc:
            failures.append({"slug": _path.stem, "error": str(exc)[:300]})

    if apply:
        _save_cache(cache_path, cache)
    discovery: dict[str, Any] = {"status": "not_requested"}
    if apply and rebuild and rebuilt_slugs:
        try:
            discovery = refresh_rebuilt_site_discovery(
                site_root,
                public_url=public_url,
            )
        except Exception as exc:
            failures.append({
                "slug": "__site_discovery__",
                "error": str(exc)[:500],
            })
            discovery = {"status": "error", "error": str(exc)[:500]}
    return {
        "drafts": len(drafts),
        "changed": len(changed_slugs),
        "changed_slugs": changed_slugs,
        "rebuilt": len(rebuilt_slugs),
        "failures": failures,
        "resolved_packages": sum(bool(cache["packages"].get(url)) for url in product_urls),
        "unresolved_products": sorted(unresolved_products),
        "resolved_profiles": sum(bool(cache["profiles"].get(url)) for url in profile_urls),
        "unresolved_profiles": sorted(unresolved_profiles),
        "resolved_official_pages": sum(
            bool(cache["official_pages"].get(url)) for url in official_page_urls
        ),
        "unresolved_official_pages": sorted(
            url for url in official_page_urls if not cache["official_pages"].get(url)
        ),
        "related_queries": len(related_queries),
        "resolved_related_queries": len(related_products),
        "unresolved_related_queries": sorted(
            query for query in related_queries
            if query.casefold() not in related_products
        ),
        "site_discovery": discovery,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="旧記事の作品パッケージ・公式プロフィール画像を実画像へ移行します"
    )
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument(
        "--public-url",
        default="",
        help="公開URL。未指定時はサイトの解析設定から取得します",
    )
    args = parser.parse_args()
    result = migrate_legacy_article_cards(
        args.site_root.resolve(),
        apply=args.apply,
        rebuild=args.rebuild,
        retry_missing=args.retry_missing,
        slugs=set(args.slug) or None,
        public_url=args.public_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
