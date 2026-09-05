from __future__ import annotations

import base64
import hashlib
import urllib.request
from typing import Any, Callable

from .affiliate_opportunities import mgs_product_page_metadata


MAX_RELATED_THUMBNAIL_BYTES = 5 * 1024 * 1024


def _image_format(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
        return ".avif", "image/avif"
    return "", ""


def download_related_thumbnail(url: str) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(MAX_RELATED_THUMBNAIL_BYTES + 1)
    if not data or len(data) > MAX_RELATED_THUMBNAIL_BYTES:
        return b"", "", ""
    extension, mime_type = _image_format(data)
    return (data, extension, mime_type) if extension else (b"", "", "")


def localize_related_thumbnail_assets(
    payload: dict[str, Any],
    downloader: Callable[[str], tuple[bytes, str, str]] | None = None,
    *,
    mgs_metadata_resolver: Callable[[str], dict[str, str]] | None = None,
) -> bool:
    """Store verified profile and product-card images outside article media."""
    images = payload.get("images")
    blocks = payload.get("blocks")
    if not isinstance(images, list) or not isinstance(blocks, list):
        return False
    fetch = downloader or download_related_thumbnail
    image_by_id = {
        str(item.get("id") or ""): item
        for item in images
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    changed = False
    fetched: dict[str, tuple[bytes, str, str]] = {}
    resolve_mgs = mgs_metadata_resolver or mgs_product_page_metadata

    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            continue
        destination_url = str(block.get("url") or "").strip()
        if (
            str(block.get("link_kind") or "") == "exact_official_work"
            and "mgstage.com/product/product_detail/" in destination_url.casefold()
            and not block.get("thumbnail_url")
        ):
            try:
                metadata = resolve_mgs(destination_url)
            except Exception:
                metadata = {}
            official_title = str(metadata.get("product_title") or "").strip()
            thumbnail_url = str(metadata.get("thumbnail_url") or "").strip()
            if official_title and block.get("title") != official_title:
                block["title"] = official_title
                changed = True
            if thumbnail_url:
                updates = {
                    "thumbnail_url": thumbnail_url,
                    "thumbnail_source_kind": "official_page",
                    "thumbnail_owner_url": destination_url,
                }
                for key, value in updates.items():
                    if block.get(key) != value:
                        block[key] = value
                        changed = True
        source_kind = str(block.get("thumbnail_source_kind") or "")
        if source_kind not in {
            "profile", "official_hub_profile", "official_page", "fanza_package",
            "fanza_performer_sample",
        }:
            continue
        owner_url = str(block.get("thumbnail_owner_url") or "").strip()
        if source_kind in {"profile", "official_page", "fanza_package"} and (
            owner_url.rstrip("/") != destination_url.rstrip("/")
        ):
            continue
        if not owner_url:
            continue

        existing_id = str(block.get("thumbnail_image_id") or "")
        existing = image_by_id.get(existing_id)
        if (
            isinstance(existing, dict)
            and existing.get("related_thumbnail_only") is True
            and str(existing.get("thumbnail_owner_url") or "").rstrip("/")
            == owner_url.rstrip("/")
        ):
            if block.pop("thumbnail_url", None):
                changed = True
            continue

        thumbnail_url = str(block.get("thumbnail_url") or "").strip()
        if not thumbnail_url:
            continue
        if thumbnail_url not in fetched:
            try:
                fetched[thumbnail_url] = fetch(thumbnail_url)
            except Exception:
                fetched[thumbnail_url] = (b"", "", "")
        data, extension, mime_type = fetched[thumbnail_url]
        if not data or not extension or not mime_type:
            continue

        digest = hashlib.sha256(
            f"{source_kind}|{owner_url}|{thumbnail_url}".encode("utf-8")
        ).hexdigest()[:18]
        is_product = source_kind in {"fanza_package", "fanza_performer_sample"}
        is_official_page = source_kind == "official_page"
        asset_kind = "product" if is_product else "official" if is_official_page else "profile"
        image_id = f"related-{asset_kind}-{digest}"
        title = str(block.get("title") or "公式プロフィール").strip()[:120]
        asset = {
            "id": image_id,
            "name": f"{image_id}{extension}",
            "data_url": (
                f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
            ),
            "alt": (
                f"{title}の公式商品パッケージ"
                if is_product
                else f"{title}の公式ページ画像"
                if is_official_page
                else f"{title}のプロフィール画像"
            )[:180],
            "orientation": "portrait" if is_product else "landscape",
            "source_url": thumbnail_url,
            "rights_basis": (
                "fanza_product_main_image"
                if is_product
                else "official_page_thumbnail"
                if is_official_page
                else "official_profile_thumbnail"
            ),
            "rights_source_url": owner_url,
            "thumbnail_owner_url": owner_url,
            "related_thumbnail_only": True,
            "ai_role": (
                "related_product_thumbnail"
                if is_product
                else "official_page_thumbnail"
                if is_official_page
                else "profile_thumbnail"
            ),
        }
        if image_id in image_by_id:
            image_by_id[image_id].update(asset)
        else:
            images.append(asset)
            image_by_id[image_id] = asset
        if block.get("thumbnail_image_id") != image_id:
            block["thumbnail_image_id"] = image_id
            changed = True
        if block.pop("thumbnail_url", None):
            changed = True
    return changed


def apply_related_thumbnail_fallbacks(payload: dict[str, Any]) -> bool:
    """Use another verified profile image when a platform blocks its own avatar."""
    images = payload.get("images")
    blocks = payload.get("blocks")
    if not isinstance(images, list) or not isinstance(blocks, list):
        return False
    image_by_id = {
        str(item.get("id") or ""): item
        for item in images
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    profile_blocks = [
        block for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and block.get("link_kind") == "official_profile"
    ]
    provider_priority = {
        "x": 0,
        "instagram": 1,
        "youtube": 2,
        "fantia": 3,
        "myfans": 4,
        "tiktok": 5,
    }
    candidates: list[tuple[int, dict[str, Any]]] = []
    for block in profile_blocks:
        image = image_by_id.get(str(block.get("thumbnail_image_id") or ""))
        if not isinstance(image, dict) or image.get("related_thumbnail_only") is not True:
            continue
        candidates.append((
            provider_priority.get(str(block.get("provider") or "").casefold(), 20),
            image,
        ))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return False

    changed = False
    fallback_by_destination: dict[str, str] = {}
    for block in profile_blocks:
        destination = str(block.get("url") or "").strip()
        if not destination:
            continue
        existing_id = str(block.get("thumbnail_image_id") or "")
        existing = image_by_id.get(existing_id)
        expected_owner = str(block.get("thumbnail_owner_url") or destination).rstrip("/")
        if (
            isinstance(existing, dict)
            and existing.get("related_thumbnail_only") is True
            and str(existing.get("thumbnail_owner_url") or "").rstrip("/")
            == expected_owner
        ):
            continue

        fallback_id = fallback_by_destination.get(destination)
        if not fallback_id:
            source = candidates[0][1]
            digest = hashlib.sha256(
                f"fallback|{destination}|{source.get('id') or ''}".encode("utf-8")
            ).hexdigest()[:18]
            fallback_id = f"related-profile-{digest}"
            fallback = dict(source)
            fallback.update({
                "id": fallback_id,
                "name": f"{fallback_id}{_image_format_from_name(source)}",
                "alt": f"{str(block.get('title') or '公式プロフィール')}のプロフィール画像"[:180],
                "rights_basis": "official_profile_identity_fallback",
                "rights_source_url": (
                    str(source.get("rights_source_url") or "")
                    or str(source.get("thumbnail_owner_url") or "")
                ),
                "thumbnail_owner_url": destination,
                "related_thumbnail_only": True,
                "ai_role": "profile_thumbnail",
            })
            if fallback_id not in image_by_id:
                images.append(fallback)
                image_by_id[fallback_id] = fallback
            fallback_by_destination[destination] = fallback_id

        updates = {
            "thumbnail_image_id": fallback_id,
            "thumbnail_source_kind": "official_identity_fallback",
            "thumbnail_owner_url": destination,
        }
        for key, value in updates.items():
            if block.get(key) != value:
                block[key] = value
                changed = True
        if block.pop("thumbnail_url", None):
            changed = True
    return changed


def prune_unreferenced_related_thumbnail_assets(payload: dict[str, Any]) -> bool:
    """Drop card-only images after their related-link card was removed."""
    images = payload.get("images")
    blocks = payload.get("blocks")
    if not isinstance(images, list) or not isinstance(blocks, list):
        return False
    referenced_ids = {
        str(block.get("thumbnail_image_id") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("thumbnail_image_id")
    }
    retained = [
        image for image in images
        if not (
            isinstance(image, dict)
            and image.get("related_thumbnail_only") is True
            and str(image.get("id") or "") not in referenced_ids
        )
    ]
    if retained == images:
        return False
    payload["images"] = retained
    return True


def _image_format_from_name(image: dict[str, Any]) -> str:
    name = str(image.get("name") or "").casefold()
    for extension in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"):
        if name.endswith(extension):
            return extension
    data_url = str(image.get("data_url") or "").casefold()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/avif": ".avif",
    }.get(data_url.partition(";")[0].removeprefix("data:"), ".jpg")
