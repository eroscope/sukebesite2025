from __future__ import annotations

import json
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .editorial_policy import (
    canonical_fanza_product_url,
    fanza_product_id,
    is_fanza_package_image,
)


CACHE_VERSION = 1
CACHE_DAYS = 7
NEGATIVE_CACHE_HOURS = 12


def _clean_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _cache_path(site_root: Path) -> Path:
    return Path(site_root) / ".article-studio" / "fanza-related-products.json"


def _load_cache(site_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_cache_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict) or value.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "queries": {}}
    if not isinstance(value.get("queries"), dict):
        value["queries"] = {}
    return value


def _save_cache(site_root: Path, cache: dict[str, Any]) -> None:
    path = _cache_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _query_from_block(block: dict[str, Any]) -> str:
    explicit = _clean_text(block.get("search_query"), 80)
    if explicit:
        return explicit
    try:
        query = parse_qs(urlparse(str(block.get("url") or "")).query)
    except ValueError:
        return ""
    return _clean_text(unquote(str((query.get("searchstr") or [""])[-1])), 80)


def _performer_query_from_block(block: dict[str, Any]) -> str:
    explicit = _clean_text(block.get("person_name"), 80)
    if explicit:
        return explicit
    title = _clean_text(block.get("title"), 120)
    match = __import__("re").match(r"(.+?)の出演作品(?:一覧)?$", title)
    return _clean_text(match.group(1), 80) if match else ""


def _product_candidate(product: Any) -> dict[str, str] | None:
    if not isinstance(product, dict):
        return None
    url = canonical_fanza_product_url(str(product.get("url") or "").strip())
    thumbnail_url = str(product.get("thumbnail_url") or "").strip()
    title = _clean_text(product.get("title"), 240)
    product_id = fanza_product_id(url)
    if not url or not product_id or not title:
        return None
    return {
        "product_id": product_id,
        "url": url,
        "title": title,
        "thumbnail_url": thumbnail_url,
        "matched_query": _clean_text(product.get("matched_query"), 80),
    }


def _valid_product(product: Any) -> dict[str, str] | None:
    candidate = _product_candidate(product)
    if not candidate or not is_fanza_package_image(
        {"url": candidate["thumbnail_url"]}, candidate["product_id"]
    ):
        return None
    return candidate


def _cache_age(cached: dict[str, Any]) -> timedelta:
    try:
        checked_at = datetime.fromisoformat(str(cached.get("checked_at") or ""))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
    except ValueError:
        checked_at = datetime.min.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at


def prefetch_related_fanza_products(
    site_root: Path,
    queries: list[str],
    discoverer: Callable[[list[str]], list[dict[str, str]]],
    package_resolver: Callable[[str], str] | None = None,
    *,
    force: bool = False,
    package_workers: int = 12,
) -> dict[str, dict[str, str]]:
    """Resolve many topic searches once and persist one verified package per query."""
    cleaned = list(dict.fromkeys(
        _clean_text(query, 80)
        for query in queries
        if _clean_text(query, 80)
    ))
    cache = _load_cache(site_root)
    resolved: dict[str, dict[str, str]] = {}
    pending: list[str] = []
    for query in cleaned:
        cached = cache["queries"].get(query.casefold())
        product = _valid_product(cached.get("product")) if isinstance(cached, dict) else None
        if not force and product and _cache_age(cached) < timedelta(days=CACHE_DAYS):
            resolved[query.casefold()] = product
            continue
        if (
            not force
            and isinstance(cached, dict)
            and not cached.get("product")
            and _cache_age(cached) < timedelta(hours=NEGATIVE_CACHE_HOURS)
        ):
            continue
        pending.append(query)

    try:
        discovered = discoverer(pending) if pending else []
    except Exception:
        discovered = []
    candidates: dict[str, dict[str, str]] = {}
    for raw in discovered:
        candidate = _product_candidate(raw)
        query_key = _clean_text(raw.get("matched_query") if isinstance(raw, dict) else "", 80).casefold()
        if candidate and query_key and query_key not in candidates:
            candidates[query_key] = candidate

    if package_resolver and candidates:
        product_ids = sorted({item["product_id"] for item in candidates.values()})
        packages: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, package_workers)) as executor:
            futures = {
                executor.submit(package_resolver, product_id): product_id
                for product_id in product_ids
            }
            for future in as_completed(futures):
                product_id = futures[future]
                try:
                    packages[product_id] = str(future.result() or "").strip()
                except Exception:
                    packages[product_id] = ""
        for candidate in candidates.values():
            package = packages.get(candidate["product_id"], "")
            if is_fanza_package_image({"url": package}, candidate["product_id"]):
                candidate["thumbnail_url"] = package

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for query in pending:
        key = query.casefold()
        product = _valid_product(candidates.get(key))
        cache["queries"][key] = {
            "query": query,
            "checked_at": checked_at,
            "product": product or {},
        }
        if product:
            resolved[key] = product
    _save_cache(site_root, cache)
    return resolved


def resolve_related_fanza_product(
    site_root: Path,
    query: str,
    discoverer: Callable[[str], list[dict[str, str]]],
    package_resolver: Callable[[str], str] | None = None,
) -> dict[str, str] | None:
    query = _clean_text(query, 80)
    if not query:
        return None
    cache = _load_cache(site_root)
    key = query.casefold()
    cached = cache["queries"].get(key)
    if isinstance(cached, dict):
        product = _valid_product(cached.get("product"))
        age = _cache_age(cached)
        if product and age < timedelta(days=CACHE_DAYS):
            return product
        if not cached.get("product") and age < timedelta(hours=NEGATIVE_CACHE_HOURS):
            return None

    try:
        products = discoverer(query)
    except Exception:
        products = []
    candidate = next(
        (item for raw in products if (item := _product_candidate(raw))),
        None,
    )
    if candidate and package_resolver and candidate.get("product_id"):
        try:
            official_package = str(package_resolver(candidate["product_id"]) or "").strip()
        except Exception:
            official_package = ""
        if is_fanza_package_image({"url": official_package}, candidate["product_id"]):
            candidate["thumbnail_url"] = official_package
    product = _valid_product(candidate)
    cache["queries"][key] = {
        "query": query,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product": product or {},
    }
    _save_cache(site_root, cache)
    return product


def hydrate_related_fanza_products(
    payload: dict[str, Any],
    site_root: Path,
    discoverer: Callable[[str], list[dict[str, str]]],
    package_resolver: Callable[[str], str] | None = None,
) -> bool:
    """Pair a topic recommendation with one real product and its own package."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return False
    changed = False
    unresolved_performer_blocks: set[int] = set()
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            continue
        link_kind = str(block.get("link_kind") or "")
        if link_kind not in {
            "inferred_topic_search", "inferred_topic_product",
            "verified_person_search",
        }:
            continue
        performer_name = (
            _performer_query_from_block(block)
            if link_kind == "verified_person_search" else ""
        )
        query = performer_name or _query_from_block(block)
        product = resolve_related_fanza_product(
            site_root, query, discoverer, package_resolver
        )
        if not product:
            if performer_name:
                # A person-name search with no verified FANZA product makes a
                # gravure model or creator look like an AV performer. Remove
                # it; the caller can then add an explicitly unrelated topic
                # recommendation without reusing the person's image.
                unresolved_performer_blocks.add(id(block))
                suppressed = [
                    _clean_text(value, 80)
                    for value in payload.get("unresolved_fanza_performer_names") or []
                    if _clean_text(value, 80)
                ]
                if performer_name.casefold() not in {
                    value.casefold() for value in suppressed
                }:
                    suppressed.append(performer_name)
                payload["unresolved_fanza_performer_names"] = suppressed
                changed = True
                continue
            for key in (
                "thumbnail_image_id", "thumbnail_url", "thumbnail_source_kind",
                "thumbnail_owner_url",
            ):
                if block.pop(key, None) is not None:
                    changed = True
            continue
        if performer_name:
            suppressed = [
                _clean_text(value, 80)
                for value in payload.get("unresolved_fanza_performer_names") or []
                if _clean_text(value, 80).casefold() != performer_name.casefold()
            ]
            if suppressed:
                payload["unresolved_fanza_performer_names"] = suppressed
            else:
                payload.pop("unresolved_fanza_performer_names", None)
            updates = {
                "text": (
                    f"{performer_name}の出演作品の一例として、"
                    f"「{product['title']}」の公式パッケージを表示しています。"
                    "ボタンから本人の出演作品一覧を確認できます。"
                ),
                "thumbnail_url": product["thumbnail_url"],
                "thumbnail_source_kind": "fanza_performer_sample",
                "thumbnail_owner_url": product["url"],
                "sample_product_url": product["url"],
                "sample_product_id": product["product_id"],
                "sample_product_title": product["title"],
                "search_query": performer_name,
                "affiliate_network": "fanza",
                "affiliate_eligible": True,
            }
            for key, value in updates.items():
                if block.get(key) != value:
                    block[key] = value
                    changed = True
            if block.pop("thumbnail_image_id", None) is not None:
                changed = True
            continue
        is_popular = query == "人気作品"
        updates = {
            "url": product["url"],
            "title": product["title"],
            "text": (
                "人物・作品を特定できなかったため、FANZA月間ランキングから"
                "実在作品を1件案内します。表示画像とリンク先は同じ商品です。"
                if is_popular
                else (
                    f"記事本人の作品ではありません。「{query}」という題材が近い"
                    "FANZA作品です。表示画像とリンク先は同じ商品です。"
                )
            ),
            "button_text": "この関連作品をFANZAで見る",
            "placement_label": (
                "FANZAの月間人気作品"
                if is_popular else "記事の題材に近い別作品"
            ),
            "link_kind": "inferred_topic_product",
            "match_evidence": f"FANZAで「{query}」を検索し、商品ページと公式パッケージを照合",
            "match_confidence": 70,
            "search_query": query,
            "thumbnail_url": product["thumbnail_url"],
            "thumbnail_source_kind": "fanza_package",
            "thumbnail_owner_url": product["url"],
            "affiliate_network": "fanza",
            "affiliate_eligible": True,
        }
        for key, value in updates.items():
            if block.get(key) != value:
                block[key] = value
                changed = True
        if block.pop("thumbnail_image_id", None) is not None:
            changed = True
    if unresolved_performer_blocks:
        blocks[:] = [
            block for block in blocks
            if id(block) not in unresolved_performer_blocks
        ]
    return changed
