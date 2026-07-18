#!/usr/bin/env python3
"""Run the local Indanya article authoring studio."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from add_article import ValidationError, add_article  # noqa: E402
from validate_article import validate_metadata  # noqa: E402


SITE_ROOT = TOOLS_ROOT.parent
STATIC_ROOT = TOOLS_ROOT / "article_studio_app"
DRAFT_ROOT = SITE_ROOT / ".article-studio" / "drafts"
MAX_REQUEST_BYTES = 110 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGES = 20
MAX_X_POSTS = 20
MAX_X_SELECTED_POSTS = 6
X_SESSION_SECONDS = 30 * 60
MAX_SOURCE_PAGE_BYTES = 6 * 1024 * 1024
MAX_SOURCE_IMAGES = 12
MAX_SELECTED_SOURCE_IMAGES = 8
SOURCE_SESSION_SECONDS = 60 * 60
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ANCHOR_PATTERN = re.compile(r"&gt;&gt;([0-9]+)")
X_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_POST_ID_PATTERN = re.compile(r"^[0-9]{1,19}$")
ALLOWED_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
X_MEDIA_HOSTS = {"pbs.twimg.com"}
JST = ZoneInfo("Asia/Tokyo")

X_EMBED_STYLE = r'''
.x-embed-shell {
  max-width: 620px;
  margin: 24px auto;
}
.x-embed-shell .twitter-tweet {
  margin: 0;
  padding: 18px 20px;
  border: 1px solid #cfd3d7;
  border-radius: 8px;
  background: #fff;
  color: #0f1419;
  font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Meiryo,sans-serif;
  line-height: 1.55;
}
.x-embed-shell .twitter-tweet p {
  margin: 0 0 13px;
  white-space: normal;
}
.x-embed-shell .twitter-tweet a {
  color: #0f6eae;
  text-decoration: underline;
}
.x-timeline-shell {
  max-width: 620px;
  min-height: 180px;
  margin: 24px auto;
  padding: 18px 20px;
  border: 1px solid #cfd3d7;
  border-radius: 8px;
  background: #fff;
  font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Meiryo,sans-serif;
}
.x-timeline-shell a { color: #0f6eae; text-decoration: underline; }
'''


@dataclass(frozen=True)
class ImageAsset:
    image_id: str
    filename: str
    mime_type: str
    alt: str
    orientation: str
    data: bytes
    data_url: str


@dataclass(frozen=True)
class ArticleBuild:
    metadata: dict[str, Any]
    article_html: str
    images: tuple[ImageAsset, ...]
    payload: dict[str, Any]


class _SourcePageParser(HTMLParser):
    """Collect editorial metadata without attempting to reproduce the source DOM."""

    TEXT_TAGS = {"title", "h1", "h2", "h3", "p", "figcaption"}
    IGNORED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.canonical_url = ""
        self.text_items: list[tuple[str, str]] = []
        self.images: list[dict[str, Any]] = []
        self._ignored_depth = 0
        self._capture_tag = ""
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            value = attributes.get("content", "").strip()
            if key and value and key not in self.metadata:
                self.metadata[key] = value
            return
        if tag == "link" and "canonical" in attributes.get("rel", "").lower():
            self.canonical_url = attributes.get("href", "").strip()
            return
        if tag == "img":
            source = (
                attributes.get("data-src")
                or attributes.get("data-original")
                or attributes.get("data-lazy-src")
                or attributes.get("src")
                or ""
            ).strip()
            srcset = (attributes.get("data-srcset") or attributes.get("srcset") or "").strip()
            if srcset:
                choices = [item.strip().split()[0] for item in srcset.split(",") if item.strip()]
                if choices:
                    source = choices[-1]
            if source:
                self.images.append({
                    "url": source,
                    "alt": attributes.get("alt", "").strip(),
                    "width": _safe_int(attributes.get("width")),
                    "height": _safe_int(attributes.get("height")),
                })
            return
        if tag in self.TEXT_TAGS and not self._capture_tag:
            self._capture_tag = tag
            self._capture_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag == self._capture_tag:
            value = _clean_space("".join(self._capture_parts))
            if value:
                self.text_items.append((tag, value))
            self._capture_tag = ""
            self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._capture_tag:
            self._capture_parts.append(data)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(str(value or "0").strip()))
    except ValueError:
        return 0


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _trim_text(value: str, maximum: int) -> str:
    cleaned = _clean_space(value)
    if len(cleaned) <= maximum:
        return cleaned
    return cleaned[: maximum - 1].rstrip("、。,. ") + "…"


def _validate_source_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2048:
        raise ValidationError("URLを1件入力してください")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("httpまたはhttpsの公開URLを入力してください")
    if parsed.username or parsed.password:
        raise ValidationError("ユーザー情報を含むURLは取得できません")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise ValidationError("ローカルURLは取得できません")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValidationError("公開インターネットのURLを入力してください")
    return value.strip()


def _decode_source_html(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type, re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates.extend(["utf-8", "cp932"])
    for encoding in dict.fromkeys(candidates):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _fetch_source_html(url_value: str, opener: Any = None) -> tuple[str, str]:
    source_url = _validate_source_url(url_value)
    request = urllib.request.Request(
        source_url,
        headers={
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "ja,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (compatible; IndanyaArticleStudio/2.0; +local-editor)",
        },
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=25) as response:
            final_url = _validate_source_url(response.geturl() if hasattr(response, "geturl") else source_url)
            content_type = str(response.headers.get("Content-Type", ""))
            if content_type and not any(kind in content_type.lower() for kind in ("text/html", "application/xhtml+xml")):
                raise ValidationError("HTMLページのURLを入力してください")
            raw = response.read(MAX_SOURCE_PAGE_BYTES + 1)
    except ValidationError:
        raise
    except urllib.error.HTTPError as exc:
        raise ValidationError(f"ページを取得できませんでした（HTTP {exc.code}）") from exc
    except (OSError, TimeoutError, socket.timeout) as exc:
        raise ValidationError("ページへ接続できませんでした") from exc
    if not raw or len(raw) > MAX_SOURCE_PAGE_BYTES:
        raise ValidationError("ページが大きすぎるため取得できません")
    return final_url, _decode_source_html(raw, content_type)


def _image_extension(data: bytes, content_type: str, image_url: str) -> str:
    candidates = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/avif": ".avif",
    }
    declared = candidates.get(content_type.split(";", 1)[0].strip().lower(), "")
    suffix = Path(urlparse(image_url).path).suffix.lower()
    suffix = ".jpg" if suffix == ".jpeg" else suffix
    for extension in (declared, suffix, ".jpg", ".png", ".gif", ".webp", ".avif"):
        if extension in ALLOWED_IMAGE_EXTENSIONS and _validate_magic(extension, data):
            return ".jpg" if extension == ".jpeg" else extension
    return ""


def _download_source_image(image_url: str, opener: Any = None) -> dict[str, Any]:
    normalized_url = _validate_source_url(image_url)
    request = urllib.request.Request(
        normalized_url,
        headers={"Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif", "User-Agent": "Mozilla/5.0 (IndanyaArticleStudio/2.0)"},
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=20) as response:
            final_url = _validate_source_url(response.geturl() if hasattr(response, "geturl") else normalized_url)
            content_type = str(response.headers.get("Content-Type", ""))
            data = response.read(MAX_IMAGE_BYTES + 1)
    except ValidationError:
        raise
    except (OSError, TimeoutError, socket.timeout, urllib.error.HTTPError) as exc:
        raise ValidationError("画像を取得できませんでした") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("画像は12MB未満である必要があります")
    extension = _image_extension(data, content_type, final_url)
    if not extension:
        raise ValidationError("対応していない画像形式です")
    mime_type = "image/jpeg" if extension == ".jpg" else f"image/{extension[1:]}"
    return {"url": final_url, "data": data, "extension": extension, "mime_type": mime_type}


def _source_kind(url_value: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url_value)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        try:
            post_url, username, post_id = normalize_x_post_url(url_value)
            return "x_post", {"url": post_url, "username": username, "post_id": post_id}
        except ValidationError:
            profile_url, username = normalize_x_profile_url(url_value)
            return "x_profile", {"url": profile_url, "username": username}
    if hostname in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube", {}
    return "web", {}


def _metadata_value(parser: _SourcePageParser, *keys: str) -> str:
    return next((_clean_space(parser.metadata.get(key, "")) for key in keys if parser.metadata.get(key)), "")


def _is_source_boilerplate(value: str) -> bool:
    lowered = value.lower()
    phrases = (
        "今すぐ登録して",
        "タイムラインをカスタマイズ",
        "アカウントを登録することにより",
        "利用規約とプライバシーポリシー",
        "cookieの使用を含む",
        "javascriptを有効",
        "log in",
        "sign up",
    )
    return any(phrase in lowered for phrase in phrases)


def _candidate_image_urls(parser: _SourcePageParser, base_url: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        value = parser.metadata.get(key)
        if value:
            candidates.append({"url": value, "alt": _metadata_value(parser, "og:image:alt", "twitter:image:alt"), "width": 0, "height": 0})
    candidates.extend(parser.images)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        absolute = urljoin(base_url, str(item.get("url") or ""))
        try:
            absolute = _validate_source_url(absolute)
        except ValidationError:
            continue
        lowered = absolute.lower()
        if any(word in lowered for word in ("favicon", "sprite", "spacer", "tracking", "pixel.gif", "logo")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        unique.append({**item, "url": absolute})
        if len(unique) >= MAX_SOURCE_IMAGES * 3:
            break
    return unique


def analyze_source_url(url_value: str, opener: Any = None) -> dict[str, Any]:
    requested_url = _validate_source_url(url_value)
    source_type, x_info = _source_kind(requested_url)
    x_embed: dict[str, Any] | None = None
    if source_type == "x_post":
        x_embed = fetch_x_oembed(x_info["url"], opener)
    elif source_type == "x_profile":
        x_embed = fetch_x_timeline_oembed(x_info["url"], opener)

    try:
        final_url, page_html = _fetch_source_html(requested_url, opener)
    except ValidationError:
        if not x_embed:
            raise
        final_url, page_html = x_info["url"], ""

    parser = _SourcePageParser()
    if page_html:
        parser.feed(page_html)
    canonical = urljoin(final_url, parser.canonical_url) if parser.canonical_url else final_url
    try:
        canonical = _validate_source_url(canonical)
    except ValidationError:
        canonical = final_url

    title = _metadata_value(parser, "og:title", "twitter:title")
    if not title:
        title = next((text for tag, text in parser.text_items if tag in {"h1", "title"}), "")
    if not title and x_embed:
        title = (
            f"{x_embed.get('author_name', x_embed.get('username', 'X'))}のX投稿"
            if source_type == "x_post" else f"@{x_embed.get('username', 'X')}の最新投稿"
        )
    title = _trim_text(title or urlparse(final_url).hostname or "話題のページ", 180)
    description = _trim_text(_metadata_value(parser, "og:description", "twitter:description", "description"), 500)
    site_name = _trim_text(_metadata_value(parser, "og:site_name", "application-name"), 80)
    if not site_name:
        site_name = (urlparse(final_url).hostname or "元ページ").removeprefix("www.")
    author = _trim_text(_metadata_value(parser, "author", "article:author"), 80)

    excerpts: list[str] = []
    seen_text: set[str] = set()
    for tag, text_value in parser.text_items:
        cleaned = _trim_text(text_value, 260)
        if tag == "title" or len(cleaned) < 24 or cleaned in seen_text or cleaned == title or _is_source_boilerplate(cleaned):
            continue
        seen_text.add(cleaned)
        excerpts.append(cleaned)
        if len(excerpts) >= 8:
            break
    if not description and excerpts:
        description = excerpts[0]
    if source_type == "x_post" and x_embed:
        description = _trim_text(str(x_embed.get("text") or description), 500)

    downloaded_images: list[dict[str, Any]] = []
    downloaded_hashes: set[str] = set()
    for candidate in _candidate_image_urls(parser, final_url):
        try:
            downloaded = _download_source_image(candidate["url"], opener)
        except ValidationError:
            continue
        content_hash = hashlib.sha256(downloaded["data"]).hexdigest()
        if content_hash in downloaded_hashes:
            continue
        downloaded_hashes.add(content_hash)
        width = _safe_int(candidate.get("width"))
        height = _safe_int(candidate.get("height"))
        downloaded_images.append({
            "id": f"media-{len(downloaded_images) + 1}",
            "url": downloaded["url"],
            "data": downloaded["data"],
            "extension": downloaded["extension"],
            "mime_type": downloaded["mime_type"],
            "alt": _trim_text(str(candidate.get("alt") or title), 180),
            "orientation": "portrait" if height > width and width > 0 else "landscape",
            "width": width,
            "height": height,
        })
        if len(downloaded_images) >= MAX_SOURCE_IMAGES:
            break

    return {
        "source_type": source_type,
        "url": canonical,
        "requested_url": requested_url,
        "title": title,
        "description": description,
        "site_name": site_name,
        "author": author,
        "excerpts": excerpts,
        "images": downloaded_images,
        "x_embed": x_embed,
        "x_info": x_info,
    }


def _source_slug(source: dict[str, Any]) -> str:
    if source["source_type"] == "x_post":
        username = str(source["x_info"]["username"]).lower().replace("_", "-")
        return f"x-{username}-{str(source['x_info']['post_id'])[-8:]}"
    if source["source_type"] == "x_profile":
        username = str(source["x_info"]["username"]).lower().replace("_", "-")
        return f"x-{username}-profile"
    host = (urlparse(str(source["url"])).hostname or "page").removeprefix("www.")
    host_slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:36] or "page"
    digest = hashlib.sha256(str(source["url"]).encode("utf-8")).hexdigest()[:8]
    return f"url-{host_slug}-{digest}"


def _response_blocks(source: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(source["title"])
    description = _trim_text(str(source.get("description") or ""), 300)
    excerpts = [str(value) for value in source.get("excerpts", []) if isinstance(value, str)]
    responses = [f"『{title}』が公開されていて、ちょっと気になる。"]
    if description:
        responses.append(f"元ページでは「{_trim_text(description, 260)}」と紹介されている。")
    for excerpt in excerpts[:3]:
        if excerpt != description:
            responses.append(_trim_text(excerpt, 320))
    responses.extend([
        "画像の雰囲気だけでも目を引くな。",
        "ほかの投稿や続報も追ってみたい。",
        "気になった人は出典元も見てみてほしい。",
    ])
    unique = list(dict.fromkeys(value for value in responses if value))[:8]
    return [
        {"id": f"auto-post-{index}", "type": "post", "text": value, "style": "large" if index == 1 else "normal"}
        for index, value in enumerate(unique, start=1)
    ]


def build_source_draft_payload(
    source: dict[str, Any],
    selected_image_ids: Any,
    manual_image: Any = None,
) -> dict[str, Any]:
    available = {item["id"]: item for item in source.get("images", []) if isinstance(item, dict)}
    if not isinstance(selected_image_ids, list) or len(selected_image_ids) > MAX_SELECTED_SOURCE_IMAGES:
        raise ValidationError(f"画像は最大{MAX_SELECTED_SOURCE_IMAGES}枚まで選べます")
    if len(selected_image_ids) != len(set(selected_image_ids)) or any(item not in available for item in selected_image_ids):
        raise ValidationError("選択した画像が無効です")

    images: list[dict[str, Any]] = []
    for index, image_id in enumerate(selected_image_ids, start=1):
        item = available[image_id]
        images.append({
            "id": f"source-image-{index}",
            "name": f"source-{index}{item['extension']}",
            "data_url": f"data:{item['mime_type']};base64,{base64.b64encode(item['data']).decode('ascii')}",
            "alt": str(item.get("alt") or source["title"])[:180],
            "orientation": item.get("orientation", "landscape"),
        })
    if not images and isinstance(manual_image, dict):
        fallback = {**manual_image, "id": "source-image-1"}
        _decode_images([fallback])
        images.append(fallback)
    if not images:
        raise ValidationError("記事に使う画像を1枚以上選ぶか、画像ファイルを追加してください")

    responses = _response_blocks(source)
    blocks: list[dict[str, Any]] = [responses[0]]
    first_image_id = images[0]["id"]
    x_embed = source.get("x_embed") if isinstance(source.get("x_embed"), dict) else None
    if source["source_type"] == "x_post" and x_embed:
        blocks.append({
            "id": f"x-post-{x_embed['id']}",
            "type": "x_embed",
            "post_id": x_embed["id"],
            "post_url": x_embed["url"],
            "author_name": x_embed["author_name"],
            "username": x_embed["username"],
            "text": x_embed["text"],
            "created_at": x_embed["created_at"],
            "lang": x_embed["lang"],
            "image_ids": [first_image_id],
        })
    elif source["source_type"] == "x_profile" and x_embed:
        blocks.append({
            "id": "x-timeline",
            "type": "x_timeline",
            "profile_url": x_embed["url"],
            "username": x_embed["username"],
            "limit": x_embed["limit"],
            "image_ids": [first_image_id],
        })
    else:
        blocks.append({"id": "source-images-1", "type": "images", "image_ids": [first_image_id]})

    remaining_image_ids = [item["id"] for item in images[1:]]
    response_index = 1
    for offset in range(0, len(remaining_image_ids), 2):
        if response_index < len(responses):
            blocks.append(responses[response_index])
            response_index += 1
        blocks.append({
            "id": f"source-images-{offset + 2}",
            "type": "images",
            "image_ids": remaining_image_ids[offset:offset + 2],
        })
    blocks.extend(responses[response_index:])
    blocks.append({"id": "auto-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})

    raw_title = str(source["title"])
    title = raw_title if raw_title.startswith("【") else f"【画像】{raw_title}"
    source_type = str(source["source_type"])
    category = "SNS" if source_type.startswith("x_") else "動画" if source_type == "youtube" else "話題"
    tags = [category, str(source["site_name"])]
    if source_type.startswith("x_"):
        tags.extend(["X", str(source["x_info"]["username"])])
    now = datetime.now(JST)
    return {
        "title": _trim_text(title, 180),
        "slug": _source_slug(source),
        "category": category,
        "summary": _trim_text(str(source.get("description") or raw_title), 240),
        "published_at": now.isoformat(timespec="seconds"),
        "status": "draft",
        "editorial_status": "draft",
        "rights_status": "unconfirmed",
        "comments": len([block for block in blocks if block["type"] == "post"]),
        "poster_name": "風吹けば名無し",
        "tags": list(dict.fromkeys(tags)),
        "featured": False,
        "fictional_responses": True,
        "replace_existing": False,
        "source_url": str(source["url"]),
        "source_label": str(source["site_name"]),
        "transparency_note": "元ページの公開情報をもとに編集用のレスとして再構成した下書きです。公開前に内容と画像利用許可を確認してください。",
        "thumbnail_id": first_image_id,
        "adult_confirmed": False,
        "rights_confirmed": False,
        "privacy_confirmed": False,
        "source_confirmed": False,
        "images": images,
        "blocks": blocks,
    }


def _require_text(payload: dict[str, Any], field: str, maximum: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValidationError(f"{field} must contain 1 to {maximum} characters")
    return value


def _optional_text(payload: dict[str, Any], field: str, maximum: int) -> str:
    value = payload.get(field, "")
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise ValidationError(f"{field} must contain at most {maximum} characters")
    return value.strip()


def normalize_x_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("X username must be text")
    candidate = value.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    elif "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            raise ValidationError("X account URL must use x.com")
        candidate = parsed.path.strip("/").split("/", 1)[0]
    if not X_USERNAME_PATTERN.fullmatch(candidate):
        raise ValidationError("X username must contain 1 to 15 ASCII letters, numbers, or underscores")
    return candidate


def normalize_x_post_url(value: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or len(value.strip()) > 2048:
        raise ValidationError("X post URL must be text")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "x.com", "www.x.com", "twitter.com", "www.twitter.com",
    }:
        raise ValidationError("X post URL must use x.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[1].lower() != "status":
        raise ValidationError("paste an X post URL containing /status/")
    username = normalize_x_username(parts[0])
    post_id = parts[2]
    if not X_POST_ID_PATTERN.fullmatch(post_id):
        raise ValidationError("X post URL has an invalid post ID")
    return f"https://x.com/{username}/status/{post_id}", username, post_id


def normalize_x_profile_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or len(value.strip()) > 2048:
        raise ValidationError("X profile URL must be text")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "x.com", "www.x.com", "twitter.com", "www.twitter.com",
    }:
        raise ValidationError("X profile URL must use x.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        raise ValidationError("paste an X profile URL or post URL")
    username = normalize_x_username(parts[0])
    return f"https://x.com/{username}", username


class _XOEmbedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_first_paragraph = False
        self.paragraph_finished = False
        self.post_parts: list[str] = []
        self.paragraph_lang = ""
        self.current_link = ""
        self.current_link_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "p" and not self.paragraph_finished:
            self.in_first_paragraph = True
            self.paragraph_lang = str(attributes.get("lang") or "")[:20]
        elif tag == "br" and self.in_first_paragraph:
            self.post_parts.append("\n")
        if tag == "a":
            self.current_link = str(attributes.get("href") or "")
            self.current_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self.in_first_paragraph:
            self.in_first_paragraph = False
            self.paragraph_finished = True
        if tag == "a" and self.current_link:
            self.links.append((self.current_link, "".join(self.current_link_parts).strip()))
            self.current_link = ""
            self.current_link_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_first_paragraph:
            self.post_parts.append(data)
        if self.current_link:
            self.current_link_parts.append(data)


def fetch_x_oembed(post_url_value: str, opener: Any = None) -> dict[str, Any]:
    post_url, username, post_id = normalize_x_post_url(post_url_value)
    query = urlencode({
        "url": post_url,
        "omit_script": "1",
        "hide_thread": "1",
        "dnt": "true",
        "lang": "en",
    })
    request = urllib.request.Request(
        f"https://publish.x.com/oembed?{query}",
        headers={"Accept": "application/json", "User-Agent": "IndanyaArticleStudio/1.2"},
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise ValidationError("X post was not found or cannot be embedded") from exc
        raise ValidationError(f"X embed service returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError) as exc:
        raise ValidationError("X embed service could not be reached") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValidationError("X embed response was too large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("X embed service returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("html"), str):
        raise ValidationError("X post cannot be embedded")

    parser = _XOEmbedParser()
    parser.feed(result["html"])
    text_value = "".join(parser.post_parts).strip()
    if not text_value:
        raise ValidationError("X embed did not contain post text")
    if len(text_value) > 10000:
        raise ValidationError("X post text is too long")

    date_text = ""
    for href, label in parser.links:
        try:
            _, _, linked_post_id = normalize_x_post_url(href)
        except ValidationError:
            continue
        if linked_post_id == post_id:
            date_text = label
    try:
        posted_date = datetime.strptime(date_text, "%B %d, %Y").replace(tzinfo=JST)
    except ValueError as exc:
        raise ValidationError("X embed did not contain a readable post date") from exc

    author_name = str(result.get("author_name") or username).strip()[:80]
    if not author_name:
        author_name = username
    return {
        "id": post_id,
        "url": post_url,
        "username": username,
        "author_name": author_name,
        "text": text_value,
        "created_at": posted_date.isoformat(),
        "lang": parser.paragraph_lang or "ja",
    }


def fetch_x_timeline_oembed(profile_url_value: str, opener: Any = None) -> dict[str, Any]:
    profile_url, username = normalize_x_profile_url(profile_url_value)
    query = urlencode({
        "url": profile_url,
        "limit": str(MAX_X_SELECTED_POSTS),
        "omit_script": "1",
        "dnt": "true",
        "lang": "ja",
    })
    request = urllib.request.Request(
        f"https://publish.x.com/oembed?{query}",
        headers={"Accept": "application/json", "User-Agent": "IndanyaArticleStudio/1.2"},
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise ValidationError("X profile was not found or cannot be embedded") from exc
        raise ValidationError(f"X embed service returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError) as exc:
        raise ValidationError("X embed service could not be reached") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValidationError("X embed response was too large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("X embed service returned invalid JSON") from exc
    if not isinstance(result, dict) or "twitter-timeline" not in str(result.get("html") or ""):
        raise ValidationError("X profile timeline cannot be embedded")
    return {"url": profile_url, "username": username, "limit": MAX_X_SELECTED_POSTS}


def _x_api_json(url: str, bearer_token: str, opener: Any = None) -> dict[str, Any]:
    if not isinstance(bearer_token, str) or not bearer_token.strip() or len(bearer_token) > 4096:
        raise ValidationError("X API Bearer Token is required")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token.strip()}",
            "User-Agent": "IndanyaArticleStudio/1.1",
        },
    )
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=20) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValidationError("X API Bearer Token was rejected") from exc
        if exc.code == 403:
            raise ValidationError("X API access was refused; check the app permissions and credits") from exc
        if exc.code == 404:
            raise ValidationError("X account was not found") from exc
        if exc.code == 429:
            raise ValidationError("X API rate limit was reached; try again later") from exc
        raise ValidationError(f"X API returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError) as exc:
        raise ValidationError("X API could not be reached") from exc
    if len(raw) > 5 * 1024 * 1024:
        raise ValidationError("X API response was too large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("X API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ValidationError("X API returned an invalid response")
    return result


def fetch_x_candidates(username_value: str, bearer_token: str, opener: Any = None) -> dict[str, Any]:
    username = normalize_x_username(username_value)
    user_query = urlencode({
        "user.fields": "name,username,description,profile_image_url,protected,public_metrics,verified",
    })
    user_result = _x_api_json(
        f"https://api.x.com/2/users/by/username/{quote(username)}?{user_query}",
        bearer_token,
        opener,
    )
    account = user_result.get("data")
    if not isinstance(account, dict) or not isinstance(account.get("id"), str):
        raise ValidationError("X account was not found")
    if account.get("protected") is True:
        raise ValidationError("protected X accounts cannot be imported")

    timeline_query = urlencode({
        "max_results": MAX_X_POSTS,
        "exclude": "retweets,replies",
        "tweet.fields": "attachments,created_at,entities,lang,note_tweet,possibly_sensitive,public_metrics",
        "expansions": "attachments.media_keys",
        "media.fields": "alt_text,height,media_key,preview_image_url,type,url,width",
    })
    timeline_result = _x_api_json(
        f"https://api.x.com/2/users/{quote(account['id'])}/tweets?{timeline_query}",
        bearer_token,
        opener,
    )
    media_items = timeline_result.get("includes", {}).get("media", [])
    media_by_key = {
        item.get("media_key"): item
        for item in media_items
        if isinstance(item, dict) and isinstance(item.get("media_key"), str)
    }

    posts: list[dict[str, Any]] = []
    for raw_post in timeline_result.get("data", []):
        if not isinstance(raw_post, dict) or not X_POST_ID_PATTERN.fullmatch(str(raw_post.get("id", ""))):
            continue
        attachments = raw_post.get("attachments")
        media_keys = attachments.get("media_keys", []) if isinstance(attachments, dict) else []
        photos: list[dict[str, Any]] = []
        for media_key in media_keys:
            item = media_by_key.get(media_key)
            if not item or item.get("type") != "photo" or not isinstance(item.get("url"), str):
                continue
            parsed_media = urlparse(item["url"])
            if parsed_media.scheme != "https" or parsed_media.hostname not in X_MEDIA_HOSTS:
                continue
            photos.append({
                "media_key": media_key,
                "url": item["url"],
                "alt_text": str(item.get("alt_text") or ""),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            })
        if not photos:
            continue
        note = raw_post.get("note_tweet")
        text_value = note.get("text") if isinstance(note, dict) else raw_post.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        metrics = raw_post.get("public_metrics") if isinstance(raw_post.get("public_metrics"), dict) else {}
        post_id = str(raw_post["id"])
        posts.append({
            "id": post_id,
            "url": f"https://x.com/{account.get('username', username)}/status/{post_id}",
            "text": text_value,
            "created_at": str(raw_post.get("created_at") or ""),
            "lang": str(raw_post.get("lang") or "ja")[:20],
            "possibly_sensitive": bool(raw_post.get("possibly_sensitive", False)),
            "metrics": {
                "like_count": int(metrics.get("like_count") or 0),
                "retweet_count": int(metrics.get("retweet_count") or 0),
                "reply_count": int(metrics.get("reply_count") or 0),
            },
            "media": photos,
        })
    if not posts:
        raise ValidationError("no recent public photo posts were found for this X account")

    public_metrics = account.get("public_metrics") if isinstance(account.get("public_metrics"), dict) else {}
    return {
        "account": {
            "id": account["id"],
            "name": str(account.get("name") or account.get("username") or username),
            "username": str(account.get("username") or username),
            "description": str(account.get("description") or ""),
            "profile_image_url": str(account.get("profile_image_url") or ""),
            "verified": bool(account.get("verified", False)),
            "followers_count": int(public_metrics.get("followers_count") or 0),
            "url": f"https://x.com/{account.get('username', username)}",
        },
        "posts": posts,
    }


def _download_x_image(media_url: str, opener: Any = None) -> tuple[bytes, str, str]:
    if not isinstance(media_url, str):
        raise ValidationError("X image is invalid")
    parsed = urlparse(media_url)
    if parsed.scheme != "https" or parsed.hostname not in X_MEDIA_HOSTS:
        raise ValidationError("X image host is not allowed")
    request = urllib.request.Request(media_url, headers={"User-Agent": "IndanyaArticleStudio/1.1"})
    client = opener or urllib.request.build_opener()
    try:
        with client.open(request, timeout=20) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else media_url
            if urlparse(final_url).hostname not in X_MEDIA_HOSTS:
                raise ValidationError("X image redirected to an untrusted host")
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
    except ValidationError:
        raise
    except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
        raise ValidationError("X image could not be downloaded") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("X image must be smaller than 12 MB")
    extension_by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    extension = extension_by_type.get(content_type)
    if not extension or not _validate_magic(extension, data):
        raise ValidationError("X image format is unsupported")
    mime_type = "image/jpeg" if extension == ".jpg" else f"image/{extension[1:]}"
    return data, mime_type, extension


def _download_x_cover(media: dict[str, Any], opener: Any = None) -> dict[str, Any]:
    media_url = media.get("url")
    if not isinstance(media_url, str):
        raise ValidationError("selected X cover image is invalid")
    data, mime_type, extension = _download_x_image(media_url, opener)
    return {
        "id": "x-cover",
        "name": f"x-cover{extension}",
        "data_url": f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
        "alt": str(media.get("alt_text") or "X投稿の画像")[:180],
        "orientation": "landscape" if int(media.get("width") or 0) >= int(media.get("height") or 0) else "portrait",
    }


def build_x_draft_payload(
    result: dict[str, Any],
    selected_post_ids: Any,
    cover_media_key: str,
    opener: Any = None,
) -> dict[str, Any]:
    if not isinstance(selected_post_ids, list) or not 1 <= len(selected_post_ids) <= MAX_X_SELECTED_POSTS:
        raise ValidationError(f"select 1 to {MAX_X_SELECTED_POSTS} X posts")
    if len(selected_post_ids) != len(set(selected_post_ids)) or any(not isinstance(item, str) for item in selected_post_ids):
        raise ValidationError("selected X posts are invalid")
    account = result.get("account")
    posts = result.get("posts")
    if not isinstance(account, dict) or not isinstance(posts, list):
        raise ValidationError("X import session is invalid")
    posts_by_id = {post.get("id"): post for post in posts if isinstance(post, dict)}
    try:
        selected = [posts_by_id[post_id] for post_id in selected_post_ids]
    except KeyError as exc:
        raise ValidationError("selected X post is no longer available") from exc

    cover_post: dict[str, Any] | None = None
    cover_media: dict[str, Any] | None = None
    for post in selected:
        for media in post.get("media", []):
            if isinstance(media, dict) and media.get("media_key") == cover_media_key:
                cover_post = post
                cover_media = media
                break
        if cover_media:
            break
    if not cover_post or not cover_media:
        raise ValidationError("choose a cover image from a selected X post")
    cover = _download_x_cover(cover_media, opener)

    username = normalize_x_username(str(account.get("username") or ""))
    name = str(account.get("name") or username)[:80]
    now = datetime.now(JST)
    blocks: list[dict[str, Any]] = [
        {
            "id": "x-intro",
            "type": "post",
            "text": f"Xで公開されている{name}（@{username}）さんの投稿をまとめました。",
            "style": "large",
        }
    ]
    for post in selected:
        blocks.append({
            "id": f"x-post-{post['id']}",
            "type": "x_embed",
            "post_id": post["id"],
            "post_url": post["url"],
            "author_name": name,
            "username": username,
            "text": post["text"],
            "created_at": post["created_at"],
            "lang": post.get("lang") or "ja",
            "image_ids": [cover["id"]] if post["id"] == cover_post["id"] else [],
        })
    blocks.append({"id": "x-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})
    return {
        "title": f"【画像】{name}（@{username}）のX投稿まとめ",
        "slug": f"x-{username.lower().replace('_', '-')}-{selected[0]['id'][-8:]}",
        "category": "SNS",
        "summary": f"{name}（@{username}）がXで公開している画像付き投稿をまとめています。",
        "published_at": now.isoformat(timespec="seconds"),
        "status": "draft",
        "comments": 0,
        "poster_name": "風吹けば名無し",
        "tags": ["X", "SNS", username],
        "featured": False,
        "fictional_responses": True,
        "replace_existing": False,
        "source_url": selected[0]["url"],
        "source_label": f"@{username}のX投稿",
        "transparency_note": "選択した公開投稿はXの公式埋め込みで表示します。投稿画像は記事一覧のサムネイルにも使用します。投稿の削除・変更があった場合は記事も確認してください。",
        "thumbnail_id": cover["id"],
        "adult_confirmed": False,
        "rights_confirmed": False,
        "privacy_confirmed": False,
        "source_confirmed": False,
        "images": [cover],
        "blocks": blocks,
    }


def build_x_free_draft_payload(
    post_urls: Any,
    cover_image: Any,
    opener: Any = None,
) -> dict[str, Any]:
    if not isinstance(post_urls, list) or not 1 <= len(post_urls) <= MAX_X_SELECTED_POSTS:
        raise ValidationError(f"paste one X profile URL or 1 to {MAX_X_SELECTED_POSTS} post URLs")
    if not isinstance(cover_image, dict):
        raise ValidationError("choose one creator image for the article thumbnail")

    timeline: dict[str, Any] | None = None
    if len(post_urls) == 1:
        try:
            timeline = fetch_x_timeline_oembed(post_urls[0], opener)
        except ValidationError as profile_error:
            try:
                normalize_x_post_url(post_urls[0])
            except ValidationError:
                raise profile_error

    if timeline:
        username = timeline["username"]
        source_url = timeline["url"]
        title = f"【画像】@{username}のX最新投稿まとめ"
        slug = f"x-{username.lower().replace('_', '-')}-timeline"
        summary = f"@{username}がXで公開している最新投稿をまとめています。"
        intro_text = f"Xで公開されている@{username}さんの最新投稿をまとめました。"
        blocks: list[dict[str, Any]] = [
            {"id": "x-intro", "type": "post", "text": intro_text, "style": "large"},
            {
                "id": "x-timeline",
                "type": "x_timeline",
                "profile_url": timeline["url"],
                "username": username,
                "limit": timeline["limit"],
                "image_ids": ["x-cover"],
            },
            {"id": "x-ad", "type": "ad", "text": "記事内容に合う関連広告枠"},
        ]
        transparency_note = "プロフィールURLはXの無料oEmbedで確認し、本文は最新投稿の公式タイムラインで表示します。選択した投稿者画像は記事一覧のサムネイルにも使用します。投稿の削除・変更は埋め込み表示へ反映されます。"
    else:
        normalized_urls: list[str] = []
        usernames: list[str] = []
        for value in post_urls:
            post_url, item_username, _ = normalize_x_post_url(value)
            normalized_urls.append(post_url)
            usernames.append(item_username)
        if len(normalized_urls) != len(set(normalized_urls)):
            raise ValidationError("X post URLs must not contain duplicates")
        if len({item.lower() for item in usernames}) != 1:
            raise ValidationError("all X post URLs must belong to the same account")
        posts = [fetch_x_oembed(post_url, opener) for post_url in normalized_urls]
        username = posts[0]["username"]
        name = posts[0]["author_name"]
        source_url = posts[0]["url"]
        title = f"【画像】{name}（@{username}）のX投稿まとめ"
        slug = f"x-{username.lower().replace('_', '-')}-{posts[0]['id'][-8:]}"
        summary = f"{name}（@{username}）がXで公開している投稿をまとめています。"
        intro_text = f"Xで公開されている{name}（@{username}）さんの投稿をまとめました。"
        blocks = [{"id": "x-intro", "type": "post", "text": intro_text, "style": "large"}]
        for index, post in enumerate(posts):
            blocks.append({
                "id": f"x-post-{post['id']}",
                "type": "x_embed",
                "post_id": post["id"],
                "post_url": post["url"],
                "author_name": post["author_name"],
                "username": post["username"],
                "text": post["text"],
                "created_at": post["created_at"],
                "lang": post["lang"],
                "image_ids": ["x-cover"] if index == 0 else [],
            })
        blocks.append({"id": "x-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})
        transparency_note = "投稿URLはXの無料oEmbedで確認し、本文は公式埋め込みで表示します。選択した投稿者画像は記事一覧のサムネイルにも使用します。投稿の削除・変更があった場合は記事も確認してください。"

    cover = {
        **cover_image,
        "id": "x-cover",
        "alt": str(cover_image.get("alt") or f"@{username}の投稿画像")[:180],
    }
    _decode_images([cover])
    now = datetime.now(JST)
    return {
        "title": title,
        "slug": slug,
        "category": "SNS",
        "summary": summary,
        "published_at": now.isoformat(timespec="seconds"),
        "status": "draft",
        "comments": 0,
        "poster_name": "風吹けば名無し",
        "tags": ["X", "SNS", username],
        "featured": False,
        "fictional_responses": True,
        "replace_existing": False,
        "source_url": source_url,
        "source_label": f"@{username}のX投稿",
        "transparency_note": transparency_note,
        "thumbnail_id": "x-cover",
        "adult_confirmed": False,
        "rights_confirmed": False,
        "privacy_confirmed": False,
        "source_confirmed": False,
        "images": [cover],
        "blocks": blocks,
    }


def _validate_magic(extension: str, data: bytes) -> bool:
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if extension == ".avif":
        return len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}
    return False


def _decode_images(raw_images: Any) -> tuple[ImageAsset, ...]:
    if not isinstance(raw_images, list) or not 1 <= len(raw_images) <= MAX_IMAGES:
        raise ValidationError(f"images must contain 1 to {MAX_IMAGES} files")

    assets: list[ImageAsset] = []
    seen_ids: set[str] = set()
    total_bytes = 0
    for index, raw in enumerate(raw_images, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"image {index} must be an object")
        image_id = _require_text(raw, "id", 120)
        if image_id in seen_ids:
            raise ValidationError(f"duplicate image id: {image_id}")
        seen_ids.add(image_id)

        original_name = _require_text(raw, "name", 180)
        extension = Path(original_name).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValidationError(f"unsupported image type: {original_name}")
        extension = ".jpg" if extension == ".jpeg" else extension

        data_url = _require_text(raw, "data_url", MAX_IMAGE_BYTES * 2)
        if not data_url.startswith("data:image/") or ";base64," not in data_url:
            raise ValidationError(f"image {index} must use an image data URL")
        encoded = data_url.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError(f"image {index} contains invalid base64") from exc
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValidationError(f"image {index} must be smaller than 12 MB")
        if not _validate_magic(extension, data):
            raise ValidationError(f"image {index} content does not match {extension}")

        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise ValidationError("total image size must be smaller than 100 MB")

        alt = _require_text(raw, "alt", 180)
        orientation = raw.get("orientation", "portrait")
        if orientation not in {"portrait", "landscape"}:
            raise ValidationError(f"image {index} orientation is invalid")
        mime_type = "image/jpeg" if extension == ".jpg" else f"image/{extension[1:]}"
        assets.append(
            ImageAsset(
                image_id=image_id,
                filename=f"image-{index:02d}{extension}",
                mime_type=mime_type,
                alt=alt,
                orientation=orientation,
                data=data,
                data_url=f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
            )
        )
    return tuple(assets)


def _validate_blocks(raw_blocks: Any, images: tuple[ImageAsset, ...]) -> list[dict[str, Any]]:
    if not isinstance(raw_blocks, list) or not raw_blocks or len(raw_blocks) > 120:
        raise ValidationError("blocks must contain 1 to 120 items")

    image_ids = {image.image_id for image in images}
    used_images: list[str] = []
    post_count = 0
    blocks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_blocks, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"block {index} must be an object")
        block_type = raw.get("type")
        if block_type == "post":
            text = _require_text(raw, "text", 1000)
            style = raw.get("style", "normal")
            if style not in {"normal", "large", "highlight"}:
                raise ValidationError(f"block {index} has an invalid post style")
            blocks.append({"type": "post", "text": text, "style": style})
            post_count += 1
        elif block_type == "images":
            selected = raw.get("image_ids")
            if not isinstance(selected, list) or not 1 <= len(selected) <= 4:
                raise ValidationError(f"block {index} must contain 1 to 4 images")
            if any(not isinstance(item, str) or item not in image_ids for item in selected):
                raise ValidationError(f"block {index} references an unknown image")
            used_images.extend(selected)
            blocks.append({"type": "images", "image_ids": selected[:]})
        elif block_type == "x_embed":
            post_id = _require_text(raw, "post_id", 19)
            if not X_POST_ID_PATTERN.fullmatch(post_id):
                raise ValidationError(f"block {index} has an invalid X post ID")
            username = normalize_x_username(_require_text(raw, "username", 15))
            author_name = _require_text(raw, "author_name", 80)
            text = _require_text(raw, "text", 10000)
            created_at = _require_text(raw, "created_at", 40)
            try:
                normalized = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
                parsed_created_at = datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise ValidationError(f"block {index} has an invalid X post date") from exc
            if parsed_created_at.tzinfo is None:
                raise ValidationError(f"block {index} X post date needs a timezone")
            post_url = _require_text(raw, "post_url", 2048)
            parsed_url = urlparse(post_url)
            if parsed_url.scheme != "https" or parsed_url.hostname not in {"x.com", "www.x.com"}:
                raise ValidationError(f"block {index} has an invalid X post URL")
            if parsed_url.path.rstrip("/") != f"/{username}/status/{post_id}":
                raise ValidationError(f"block {index} X post URL does not match its account")
            lang = _optional_text(raw, "lang", 20) or "ja"
            selected = raw.get("image_ids", [])
            if not isinstance(selected, list) or len(selected) > 1:
                raise ValidationError(f"block {index} can own at most one cover image")
            if any(not isinstance(item, str) or item not in image_ids for item in selected):
                raise ValidationError(f"block {index} references an unknown cover image")
            used_images.extend(selected)
            blocks.append({
                "type": "x_embed",
                "post_id": post_id,
                "post_url": post_url,
                "author_name": author_name,
                "username": username,
                "text": text,
                "created_at": created_at,
                "lang": lang,
                "image_ids": selected[:],
            })
        elif block_type == "x_timeline":
            username = normalize_x_username(_require_text(raw, "username", 15))
            profile_url = _require_text(raw, "profile_url", 2048)
            normalized_profile_url, profile_username = normalize_x_profile_url(profile_url)
            if profile_username.lower() != username.lower():
                raise ValidationError(f"block {index} X profile URL does not match its account")
            limit = raw.get("limit", MAX_X_SELECTED_POSTS)
            if not isinstance(limit, int) or not 1 <= limit <= MAX_X_SELECTED_POSTS:
                raise ValidationError(f"block {index} has an invalid X timeline limit")
            selected = raw.get("image_ids", [])
            if not isinstance(selected, list) or len(selected) > 1:
                raise ValidationError(f"block {index} can own at most one cover image")
            if any(not isinstance(item, str) or item not in image_ids for item in selected):
                raise ValidationError(f"block {index} references an unknown cover image")
            used_images.extend(selected)
            blocks.append({
                "type": "x_timeline",
                "profile_url": normalized_profile_url,
                "username": username,
                "limit": limit,
                "image_ids": selected[:],
            })
        elif block_type == "separator":
            blocks.append({"type": "separator"})
        elif block_type == "ad":
            text = _optional_text(raw, "text", 240) or "関連広告枠"
            blocks.append({"type": "ad", "text": text})
        else:
            raise ValidationError(f"block {index} has an unknown type")

    if post_count < 1:
        raise ValidationError("the article needs at least one response")
    if len(used_images) != len(set(used_images)):
        raise ValidationError("each image can be placed only once")
    if set(used_images) != image_ids:
        missing = sorted(image_ids - set(used_images))
        raise ValidationError("all images must be placed: " + ", ".join(missing))
    return blocks


def _load_database(site_root: Path) -> list[dict[str, Any]]:
    path = site_root / "data" / "articles.json"
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def _make_metadata(payload: dict[str, Any], images: tuple[ImageAsset, ...], site_root: Path) -> dict[str, Any]:
    title = _require_text(payload, "title", 180)
    slug = _require_text(payload, "slug", 100)
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("slug must use lowercase ASCII letters, numbers, and single hyphens")
    category = _require_text(payload, "category", 40)
    summary = _optional_text(payload, "summary", 240)
    source_url = _require_text(payload, "source_url", 2048)
    published_at = _require_text(payload, "published_at", 40)
    try:
        normalized_time = published_at[:-1] + "+00:00" if published_at.endswith("Z") else published_at
        published_datetime = datetime.fromisoformat(normalized_time)
    except ValueError as exc:
        raise ValidationError("published_at must be an ISO 8601 date-time") from exc
    if published_datetime.tzinfo is None:
        raise ValidationError("published_at must include a timezone")

    comments = payload.get("comments", 0)
    if isinstance(comments, bool) or not isinstance(comments, int) or comments < 0:
        raise ValidationError("comments must be a non-negative integer")
    status = payload.get("status", "draft")
    if status not in {"draft", "published", "archived"}:
        raise ValidationError("status is invalid")

    raw_tags = payload.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
    if not isinstance(raw_tags, list):
        raise ValidationError("tags must be a list")
    if any(not isinstance(tag, str) or not tag or len(tag) > 40 for tag in raw_tags):
        raise ValidationError("tags must contain non-empty text up to 40 characters")
    tags = list(dict.fromkeys(raw_tags))

    existing = next((item for item in _load_database(site_root) if item.get("slug") == slug), None)
    article_id = str(existing["id"]) if existing else f"indanya-{slug}"
    thumbnail_id = payload.get("thumbnail_id") or images[0].image_id
    thumbnail = next((image for image in images if image.image_id == thumbnail_id), None)
    if thumbnail is None:
        raise ValidationError("thumbnail_id must reference an uploaded image")

    metadata: dict[str, Any] = {
        "id": article_id,
        "slug": slug,
        "title": title,
        "category": category,
        "status": status,
        "published_at": published_at,
        "display_date": published_datetime.astimezone(JST).strftime("%Y.%m.%d"),
        "comments": comments,
        "url": f"articles/{slug}.html",
        "thumbnail": f"assets/articles/{slug}/{thumbnail.filename}",
        "source_url": source_url,
        "images_used": len(images),
        "featured": bool(payload.get("featured", False)),
    }
    if summary:
        metadata["summary"] = summary
    if tags:
        metadata["tags"] = tags
    return validate_metadata(metadata)


def _extract_sample_assets(site_root: Path) -> tuple[str, str]:
    sample = (site_root / "articles" / "pool-look-back.html").read_text(encoding="utf-8")
    style_match = re.search(r"<style>([\s\S]*?)</style>", sample)
    script_match = re.search(r"<script>([\s\S]*?)</script>", sample)
    if not style_match or not script_match:
        raise ValidationError("the approved article template is incomplete")
    return style_match.group(1), script_match.group(1)


def _render_post_text(value: str) -> str:
    escaped = html.escape(value)
    return ANCHOR_PATTERN.sub(r'<span class="anchor">&gt;&gt;\1</span>', escaped)


def _post_datetime(base: datetime, number: int, slug: str) -> tuple[str, str]:
    current = base.astimezone(JST) + timedelta(seconds=37 * (number - 1))
    weekdays = "月火水木金土日"
    fraction = int(hashlib.sha256(f"{slug}:{number}".encode()).hexdigest()[:2], 16) % 100
    stamp = current.strftime(f"%Y/%m/%d({weekdays[current.weekday()]}) %H:%M:%S") + f".{fraction:02d}"
    post_id = hashlib.sha256(f"post:{slug}:{number}".encode()).hexdigest()[:6]
    return stamp, post_id


def _render_sidebar(site_root: Path, metadata: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    articles = [item for item in _load_database(site_root) if item.get("status") == "published"]
    articles = [item for item in articles if item.get("slug") != metadata["slug"]]
    if metadata["status"] == "published":
        articles.append(metadata)
    articles.sort(key=lambda item: int(item.get("comments", 0)), reverse=True)
    ranks = []
    for number, item in enumerate(articles[:4], start=1):
        href = Path(str(item["url"])).name
        ranks.append(
            f'<div class="rank"><span class="rank-num">{number}</span><div>'
            f'<a href="{html.escape(href, quote=True)}">{html.escape(str(item["title"]))}</a>'
            f'<span>{int(item.get("comments", 0))}コメント</span></div></div>'
        )
    if not ranks:
        ranks.append('<div class="rank"><span class="rank-num">新</span><div><a href="#">公開準備中</a><span>0コメント</span></div></div>')

    comments = [block["text"].replace("\n", " ") for block in blocks if block["type"] == "post"][:3]
    latest = []
    for number, comment in enumerate(comments, start=1):
        latest.append(
            '<div class="rank"><span class="rank-num">新</span><div>'
            f'<a href="#">{html.escape(comment[:34])}</a><span>{number:02d}:00</span></div></div>'
        )
    return (
        '<aside class="sidebar">'
        '<section class="sidebox"><h2 class="side-title">今日の人気記事</h2>'
        f'<div class="sidebody">{"".join(ranks)}</div></section>'
        '<section class="sidebox"><h2 class="side-title">最新コメント</h2>'
        f'<div class="sidebody">{"".join(latest)}</div></section>'
        '<section class="sidebox"><h2 class="side-title">PR</h2>'
        '<div class="sidebody"><div class="side-ad">関連広告枠</div></div></section>'
        '</aside>'
    )


def render_article(
    site_root: Path,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    images: tuple[ImageAsset, ...],
    blocks: list[dict[str, Any]],
    *,
    preview: bool,
) -> str:
    style, script = _extract_sample_assets(site_root)
    image_map = {image.image_id: image for image in images}
    base_time_value = metadata["published_at"]
    normalized_time = base_time_value[:-1] + "+00:00" if base_time_value.endswith("Z") else base_time_value
    base_time = datetime.fromisoformat(normalized_time)
    poster_name = _optional_text(payload, "poster_name", 80) or "風吹けば名無し"

    rendered_blocks: list[str] = []
    post_number = 0
    image_number = 0

    def render_image_group(selected_ids: list[str]) -> str:
        nonlocal image_number
        selected = [image_map[image_id] for image_id in selected_ids]
        group_class = "image-group single" if len(selected) == 1 else "image-group"
        cards: list[str] = []
        for image in selected:
            image_number += 1
            source = image.data_url if preview else f"images/{image.filename}"
            cards.append(
                f'<div class="image-card {image.orientation}"><img class="zoomable" '
                f'src="{html.escape(source, quote=True)}" alt="{html.escape(image.alt, quote=True)}">'
                f'<span class="image-count">{image_number} / {len(images)}</span></div>'
            )
        return (
            f'<div class="{group_class}">{"".join(cards)}</div>'
            '<div class="image-note">画像を押すと拡大できます</div>'
        )

    for block in blocks:
        if block["type"] == "post":
            post_number += 1
            stamp, post_id = _post_datetime(base_time, post_number, str(metadata["slug"]))
            post_class = "post highlight" if block["style"] == "highlight" else "post"
            body_class = "post-body large" if block["style"] == "large" else "post-body"
            body = _render_post_text(block["text"])
            if block["style"] == "highlight":
                body = f'<span class="red">{body}</span>'
            rendered_blocks.append(
                f'<div class="{post_class}"><div class="post-head"><span>{post_number}:</span> '
                f'<span class="post-name">{html.escape(poster_name)}</span> '
                f'<span class="post-date">{stamp}</span> <span class="post-id">ID:{post_id}</span></div>'
                f'<div class="{body_class}">{body}</div></div>'
            )
        elif block["type"] == "images":
            rendered_blocks.append(render_image_group(block["image_ids"]))
        elif block["type"] == "x_embed":
            if block["image_ids"]:
                rendered_blocks.append(render_image_group(block["image_ids"]))
            normalized_created = block["created_at"][:-1] + "+00:00" if block["created_at"].endswith("Z") else block["created_at"]
            created = datetime.fromisoformat(normalized_created).astimezone(JST)
            embed_text = html.escape(block["text"]).replace("\n", "<br>")
            rendered_blocks.append(
                '<div class="x-embed-shell"><blockquote class="twitter-tweet" data-dnt="true" data-theme="light">'
                f'<p lang="{html.escape(block["lang"], quote=True)}" dir="ltr">{embed_text}</p>'
                f'&mdash; {html.escape(block["author_name"])} (@{html.escape(block["username"])}) '
                f'<a href="{html.escape(block["post_url"], quote=True)}">'
                f'{created.strftime("%Y年%m月%d日 %H:%M")}</a></blockquote></div>'
            )
        elif block["type"] == "x_timeline":
            if block["image_ids"]:
                rendered_blocks.append(render_image_group(block["image_ids"]))
            rendered_blocks.append(
                '<div class="x-timeline-shell">'
                f'<a class="twitter-timeline" data-dnt="true" data-theme="light" '
                f'data-tweet-limit="{block["limit"]}" '
                f'href="{html.escape(block["profile_url"], quote=True)}">'
                f'@{html.escape(block["username"])}の最新投稿をXで見る</a></div>'
            )
        elif block["type"] == "separator":
            rendered_blocks.append('<div class="separator"></div>')
        elif block["type"] == "ad":
            rendered_blocks.append(f'<div class="ad">PR<br>{html.escape(block["text"])}</div>')

    source_label = _optional_text(payload, "source_label", 120) or "元記事"
    transparency = _optional_text(payload, "transparency_note", 500)
    if bool(payload.get("fictional_responses", True)):
        fixed_note = "レス本文は記事構成のための再構成です。"
        transparency = f"{transparency} {fixed_note}".strip()
    source = (
        '<div class="source">元記事：'
        f'<a href="{html.escape(str(metadata["source_url"]), quote=True)}" target="_blank" rel="noopener">'
        f'{html.escape(source_label)}</a>'
        f'{"<br>※" + html.escape(transparency) if transparency else ""}</div>'
    )
    rendered_blocks.append(source)

    logo_source = "/site/assets/common/indanya-logo.png" if preview else "../assets/common/indanya-logo.png"
    home_href = "/site/index.html" if preview else "../index.html"
    title = str(metadata["title"])
    summary = str(metadata.get("summary", title))
    sidebar = _render_sidebar(site_root, metadata, blocks)
    has_x_embeds = any(block["type"] in {"x_embed", "x_timeline"} for block in blocks)
    complete_style = style + (X_EMBED_STYLE if has_x_embeds else "")
    style_markup = '<link rel="stylesheet" href="/preview.css">' if preview else f"<style>{complete_style}</style>"
    x_widgets = (
        '<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>'
        if has_x_embeds and not preview else ""
    )
    return f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{html.escape(summary, quote=True)}">
<title>{html.escape(title)}｜淫談屋</title>
{style_markup}
</head>
<body>
<div class="topbar">PRを含む場合は記事内に表示します</div>
<header class="site-header"><div class="header-inner">
  <a class="logo" href="{home_href}"><img src="{logo_source}" alt="淫談屋"></a>
  <div class="header-copy">ネットに流れる大人向け画像・話題を、<br>余計な解説を入れずレス形式でサッと読む。</div>
</div></header>
<nav class="nav"><div class="nav-inner">
  <a href="{home_href}">新着</a><a href="#">画像</a><a href="#">SNS</a><a href="#">水着</a><a href="#">お姉さん</a><a href="#">人気記事</a>
</div></nav>
<main class="page">
  <div class="breadcrumb"><a href="{home_href}">淫談屋</a> ＞ {html.escape(str(metadata["category"]))} ＞ {html.escape(title)}</div>
  <div class="layout"><article class="article">
    <header class="article-head"><h1 class="article-title">{html.escape(title)}</h1>
      <div class="article-meta"><span>{metadata["display_date"]}</span><span>{metadata["comments"]} コメント</span><span>画像{len(images)}枚</span></div>
    </header>
    <div class="thread">{"".join(rendered_blocks)}</div>
  </article>{sidebar}</div>
</main>
<div class="lightbox" id="lightbox" aria-hidden="true"><button class="lightbox-close" id="lightboxClose" aria-label="閉じる">×</button><img id="lightboxImage" alt="拡大画像"></div>
<footer class="footer"><div class="footer-inner"><span>© 2026 淫談屋</span><span>運営者情報　広告掲載　お問い合わせ　プライバシーポリシー</span></div></footer>
<script>{script}</script>
{x_widgets}
</body>
</html>
'''


def build_article(payload: dict[str, Any], site_root: Path = SITE_ROOT, *, preview: bool = False) -> ArticleBuild:
    if not isinstance(payload, dict):
        raise ValidationError("article payload must be an object")
    images = _decode_images(payload.get("images"))
    blocks = _validate_blocks(payload.get("blocks"), images)
    metadata = _make_metadata(payload, images, site_root)
    article_html = render_article(site_root, payload, metadata, images, blocks, preview=preview)
    normalized_payload = {**payload, "blocks": blocks}
    return ArticleBuild(metadata, article_html, images, normalized_payload)


def _write_package(build: ArticleBuild, root: Path) -> tuple[Path, Path, Path]:
    metadata_path = root / "metadata.json"
    html_path = root / "article.html"
    images_path = root / "images"
    images_path.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(build.metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(build.article_html, encoding="utf-8")
    for image in build.images:
        (images_path / image.filename).write_bytes(image.data)
    return metadata_path, html_path, images_path


def add_built_article(payload: dict[str, Any], site_root: Path = SITE_ROOT) -> dict[str, Any]:
    checks = ("adult_confirmed", "rights_confirmed", "privacy_confirmed", "source_confirmed")
    missing = [field for field in checks if payload.get(field) is not True]
    if missing:
        raise ValidationError("publishing confirmations are incomplete")
    build = build_article(payload, site_root)
    existing = any(item.get("slug") == build.metadata["slug"] for item in _load_database(site_root))
    if existing and payload.get("replace_existing") is not True:
        raise ValidationError("this slug already exists; enable replace_existing to update it")

    with tempfile.TemporaryDirectory(prefix="indanya-studio-", dir=site_root) as temporary:
        metadata_path, html_path, images_path = _write_package(build, Path(temporary))
        dry_run_message = add_article(site_root, metadata_path, html_path, images_path, dry_run=True)
        completed_message = add_article(site_root, metadata_path, html_path, images_path)
    try:
        save_draft(payload, site_root)
    except OSError:
        pass
    return {
        "message": completed_message,
        "validation": dry_run_message,
        "slug": build.metadata["slug"],
        "url": build.metadata["url"],
        "status": build.metadata["status"],
    }


def make_package(payload: dict[str, Any], site_root: Path = SITE_ROOT) -> tuple[str, bytes]:
    build = build_article(payload, site_root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(build.metadata, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("article.html", build.article_html)
        for image in build.images:
            archive.writestr(f"images/{image.filename}", image.data)
    return f"{build.metadata['slug']}.zip", buffer.getvalue()


def save_draft(payload: dict[str, Any], site_root: Path = SITE_ROOT) -> str:
    slug = _require_text(payload, "slug", 100)
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("a valid slug is required to save a draft")
    draft_root = site_root / ".article-studio" / "drafts"
    draft_root.mkdir(parents=True, exist_ok=True)
    destination = draft_root / f"{slug}.json"
    temporary = draft_root / f".{slug}.{secrets.token_hex(4)}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return slug


def list_drafts(site_root: Path = SITE_ROOT) -> list[dict[str, Any]]:
    draft_root = site_root / ".article-studio" / "drafts"
    if not draft_root.exists():
        return []
    drafts = []
    for path in sorted(draft_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        drafts.append({
            "slug": path.stem,
            "title": str(payload.get("title") or path.stem)[:180],
            "status": str(payload.get("editorial_status") or payload.get("status") or "draft"),
            "rights_status": str(payload.get("rights_status") or ("confirmed" if payload.get("rights_confirmed") else "unconfirmed")),
            "source_url": str(payload.get("source_url") or "")[:2048],
            "category": str(payload.get("category") or "")[:40],
            "image_count": len(payload.get("images", [])) if isinstance(payload.get("images"), list) else 0,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, JST).isoformat(),
            "size": path.stat().st_size,
        })
    return drafts


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "IndanyaArticleStudio/1.0"

    @property
    def studio_server(self) -> "StudioServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, message: str, *args: object) -> None:
        sys.stderr.write("[studio] " + message % args + "\n")

    def _send_headers(self, status: int, content_type: str, length: int, *, download: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'",
        )
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200, *, download: str | None = None) -> None:
        self._send_headers(status, content_type, len(body), download=download)
        self.wfile.write(body)

    def _send_json(self, value: object, status: int = 200) -> None:
        body = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _read_json(self) -> dict[str, Any]:
        length_value = self.headers.get("Content-Length")
        if not length_value or not length_value.isdigit():
            raise ValidationError("Content-Length is required")
        length = int(length_value)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValidationError("request body is too large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ValidationError("request body must be an object")
        return value

    def _require_token(self) -> None:
        token = self.headers.get("X-Indanya-Token", "")
        if not secrets.compare_digest(token, self.studio_server.api_token):
            raise PermissionError("invalid studio token")

    def _serve_file(self, path: Path, allowed_root: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if allowed_root.resolve() not in resolved.parents and resolved != allowed_root.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }
        self._send_bytes(resolved.read_bytes(), content_types.get(resolved.suffix.lower(), "application/octet-stream"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/bootstrap":
                articles = _load_database(self.studio_server.site_root)
                categories = sorted({str(item.get("category")) for item in articles if item.get("category")})
                self._send_json({
                    "token": self.studio_server.api_token,
                    "articles": articles,
                    "categories": categories,
                    "drafts": list_drafts(self.studio_server.site_root),
                    "x_token_configured": bool(self.studio_server.x_bearer_token),
                })
                return
            if path.startswith("/api/drafts/"):
                self._require_token()
                slug = path.removeprefix("/api/drafts/")
                if not SLUG_PATTERN.fullmatch(slug):
                    raise ValidationError("invalid draft slug")
                draft = self.studio_server.site_root / ".article-studio" / "drafts" / f"{slug}.json"
                if not draft.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_bytes(draft.read_bytes(), "application/json; charset=utf-8")
                return
            if path.startswith("/api/x/avatar/"):
                session_id = path.removeprefix("/api/x/avatar/")
                result = self.studio_server.get_x_session(session_id)
                account = result.get("account", {})
                image_url = account.get("profile_image_url") if isinstance(account, dict) else None
                if not isinstance(image_url, str) or not image_url:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data, mime_type, _ = _download_x_image(image_url, self.studio_server.url_opener)
                self._send_bytes(data, mime_type)
                return
            if path.startswith("/api/x/media/"):
                reference = path.removeprefix("/api/x/media/")
                if "/" not in reference:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                session_id, media_key = reference.split("/", 1)
                result = self.studio_server.get_x_session(session_id)
                media = next((
                    item
                    for post in result.get("posts", []) if isinstance(post, dict)
                    for item in post.get("media", []) if isinstance(item, dict) and item.get("media_key") == media_key
                ), None)
                if not media:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data, mime_type, _ = _download_x_image(media["url"], self.studio_server.url_opener)
                self._send_bytes(data, mime_type)
                return
            if path.startswith("/api/source/media/"):
                reference = path.removeprefix("/api/source/media/")
                if "/" not in reference:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                session_id, media_id = reference.split("/", 1)
                source = self.studio_server.get_source_session(session_id)
                media = next((
                    item for item in source.get("images", [])
                    if isinstance(item, dict) and item.get("id") == media_id
                ), None)
                if not media or not isinstance(media.get("data"), bytes):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_bytes(media["data"], str(media.get("mime_type") or "application/octet-stream"))
                return
            if path.startswith("/site/"):
                relative = path.removeprefix("/site/")
                if relative != "index.html" and not relative.startswith("assets/common/"):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                self._serve_file(self.studio_server.site_root / relative, self.studio_server.site_root)
                return
            if path == "/preview.css":
                style, _ = _extract_sample_assets(self.studio_server.site_root)
                self._send_bytes((style + X_EMBED_STYLE).encode("utf-8"), "text/css; charset=utf-8")
                return

            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
            self._serve_file(STATIC_ROOT / relative, STATIC_ROOT)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_token()
            payload = self._read_json()
            if self.path == "/api/render":
                build = build_article(payload, self.studio_server.site_root, preview=True)
                self._send_json({"html": build.article_html, "metadata": build.metadata})
            elif self.path == "/api/drafts":
                slug = save_draft(payload, self.studio_server.site_root)
                self._send_json({"message": "下書きを保存しました", "slug": slug})
            elif self.path == "/api/package":
                filename, package = make_package(payload, self.studio_server.site_root)
                self._send_bytes(package, "application/zip", download=filename)
            elif self.path == "/api/articles":
                result = add_built_article(payload, self.studio_server.site_root)
                self._send_json(result, HTTPStatus.CREATED)
            elif self.path == "/api/source/analyze":
                source = analyze_source_url(payload.get("url", ""), self.studio_server.url_opener)
                session_id = self.studio_server.store_source_session(source)
                public_images = [{
                    "id": item["id"],
                    "alt": item.get("alt", ""),
                    "orientation": item.get("orientation", "landscape"),
                    "width": item.get("width", 0),
                    "height": item.get("height", 0),
                    "preview_url": f"/api/source/media/{quote(session_id)}/{quote(str(item['id']))}",
                } for item in source.get("images", []) if isinstance(item, dict)]
                self._send_json({
                    "session_id": session_id,
                    "source": {
                        "type": source["source_type"],
                        "url": source["url"],
                        "title": source["title"],
                        "description": source["description"],
                        "site_name": source["site_name"],
                        "author": source["author"],
                        "excerpts": source["excerpts"],
                    },
                    "images": public_images,
                    "recommended_image_ids": [item["id"] for item in public_images[:6]],
                    "needs_image_upload": not public_images,
                })
            elif self.path == "/api/source/draft":
                session_id = _require_text(payload, "session_id", 200)
                source = self.studio_server.get_source_session(session_id)
                draft = build_source_draft_payload(
                    source,
                    payload.get("selected_image_ids"),
                    payload.get("manual_image"),
                )
                self._send_json({"payload": draft})
            elif self.path == "/api/x/account":
                bearer_token = payload.get("bearer_token") or self.studio_server.x_bearer_token
                result = fetch_x_candidates(payload.get("username", ""), bearer_token, self.studio_server.url_opener)
                session_id = self.studio_server.store_x_session(result)
                self._send_json({"session_id": session_id, **result})
            elif self.path == "/api/x/draft":
                session_id = _require_text(payload, "session_id", 200)
                result = self.studio_server.get_x_session(session_id)
                draft = build_x_draft_payload(
                    result,
                    payload.get("selected_post_ids"),
                    _require_text(payload, "cover_media_key", 200),
                    self.studio_server.url_opener,
                )
                self._send_json({"payload": draft})
            elif self.path == "/api/x/free-draft":
                draft = build_x_free_draft_payload(
                    payload.get("post_urls"),
                    payload.get("cover_image"),
                    self.studio_server.url_opener,
                )
                self._send_json({"payload": draft})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except (OSError, UnicodeError, ValidationError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        site_root: Path,
        *,
        x_bearer_token: str | None = None,
        url_opener: Any = None,
    ) -> None:
        self.site_root = site_root.resolve()
        self.api_token = secrets.token_urlsafe(32)
        self.x_bearer_token = x_bearer_token if x_bearer_token is not None else os.environ.get("X_BEARER_TOKEN", "")
        self.url_opener = url_opener or urllib.request.build_opener()
        self.x_sessions: dict[str, tuple[float, dict[str, Any]]] = {}
        self.source_sessions: dict[str, tuple[float, dict[str, Any]]] = {}
        self.x_session_lock = threading.Lock()
        self.source_session_lock = threading.Lock()
        super().__init__(address, StudioHandler)

    def store_x_session(self, result: dict[str, Any]) -> str:
        now = time.monotonic()
        session_id = secrets.token_urlsafe(24)
        with self.x_session_lock:
            self.x_sessions = {
                key: value for key, value in self.x_sessions.items()
                if now - value[0] <= X_SESSION_SECONDS
            }
            self.x_sessions[session_id] = (now, result)
        return session_id

    def get_x_session(self, session_id: str) -> dict[str, Any]:
        now = time.monotonic()
        with self.x_session_lock:
            value = self.x_sessions.get(session_id)
        if not value or now - value[0] > X_SESSION_SECONDS:
            raise ValidationError("X import session expired; fetch the account again")
        return value[1]

    def store_source_session(self, result: dict[str, Any]) -> str:
        now = time.monotonic()
        session_id = secrets.token_urlsafe(24)
        with self.source_session_lock:
            self.source_sessions = {
                key: value for key, value in self.source_sessions.items()
                if now - value[0] <= SOURCE_SESSION_SECONDS
            }
            self.source_sessions[session_id] = (now, result)
        return session_id

    def get_source_session(self, session_id: str) -> dict[str, Any]:
        now = time.monotonic()
        with self.source_session_lock:
            value = self.source_sessions.get(session_id)
        if not value or now - value[0] > SOURCE_SESSION_SECONDS:
            raise ValidationError("URL解析の有効期限が切れました。もう一度URLを解析してください")
        return value[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--site-root", type=Path, default=SITE_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("article studio only binds to localhost")
    if not STATIC_ROOT.is_dir():
        parser.error(f"article studio assets are missing: {STATIC_ROOT}")

    server = StudioServer((args.host, args.port), args.site_root)
    url = f"http://{args.host}:{args.port}/"
    print(f"Indanya Article Studio: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
