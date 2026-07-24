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
import shutil
import socket
import subprocess
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
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from add_article import ValidationError, add_article  # noqa: E402
from validate_article import validate_metadata  # noqa: E402


SITE_ROOT = TOOLS_ROOT.parent
STATIC_ROOT = TOOLS_ROOT / "article_studio_app"
DRAFT_ROOT = SITE_ROOT / ".article-studio" / "drafts"
JOB_ROOT = SITE_ROOT / ".article-studio" / "jobs"
CODEX_SCHEMA_PATH = TOOLS_ROOT / "article_studio_codex_schema.json"
CODEX_ANALYSIS_SCHEMA_PATH = TOOLS_ROOT / "article_studio_codex_analysis_schema.json"
MAX_REQUEST_BYTES = 110 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGES = 50
MAX_X_POSTS = 20
MAX_X_SELECTED_POSTS = 6
X_SESSION_SECONDS = 30 * 60
MAX_SOURCE_PAGE_BYTES = 6 * 1024 * 1024
MAX_SOURCE_IMAGES = 50
MAX_SELECTED_SOURCE_IMAGES = 50
MAX_SOURCE_VIDEOS = 20
MAX_SELECTED_SOURCE_VIDEOS = 10
MAX_VIDEO_PROXY_BYTES = 160 * 1024 * 1024
SOURCE_SESSION_SECONDS = 60 * 60
CODEX_TIMEOUT_SECONDS = 12 * 60
RIGHTS_STATUSES = {"unconfirmed", "requested", "confirmed", "rejected"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ANCHOR_PATTERN = re.compile(r"&gt;&gt;([0-9]+)")
X_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_POST_ID_PATTERN = re.compile(r"^[0-9]{1,19}$")
ALLOWED_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
X_MEDIA_HOSTS = {"pbs.twimg.com"}
JST = ZoneInfo("Asia/Tokyo")

ARTICLE_DISCOVERY_STYLE = r'''
.site-search {
  background:#e7e4dc;
  border-bottom:1px solid #cbc7bd;
}
.site-search form {
  width:min(var(--max),calc(100% - 30px));
  margin:auto;
  padding:11px 0;
  display:grid;
  grid-template-columns:1fr auto;
}
.site-search input {
  min-width:0;
  padding:10px 12px;
  border:1px solid #a9a59c;
  border-radius:0;
  background:#fff;
  color:#222;
  font:inherit;
  font-size:13px;
}
.site-search button {
  padding:0 22px;
  border:1px solid var(--ink);
  background:var(--ink);
  color:#fff;
  font-weight:900;
  cursor:pointer;
}
.sr-only {
  position:absolute;
  width:1px;
  height:1px;
  padding:0;
  margin:-1px;
  overflow:hidden;
  clip:rect(0,0,0,0);
  white-space:nowrap;
  border:0;
}
.footer a { color:inherit; }
@media(max-width:620px) {
  .site-search form { width:calc(100% - 18px); }
  .site-search button { padding:0 16px; }
}
'''

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

FANZA_PRODUCT_STYLE = r'''
.fanza-product {
  margin: 26px 0;
  padding: 18px;
  border: 2px solid #1a1a1a;
  background: #fff;
}
.fanza-product-label {
  margin-bottom: 8px;
  color: #c72d22;
  font-size: 12px;
  font-weight: 800;
}
.fanza-product-title {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 800;
  line-height: 1.5;
}
.fanza-product-text { margin: 0 0 14px; color: #555; line-height: 1.7; }
.fanza-product-button {
  display: block;
  padding: 13px 18px;
  background: #17191c;
  color: #fff !important;
  font-weight: 800;
  text-align: center;
  text-decoration: none;
}
.fanza-product-button:hover { background: #c72d22; }
'''

VIDEO_EMBED_STYLE = r'''
.video-group {
  display: grid;
  gap: 14px;
  max-width: 720px;
  margin: 24px auto;
}
.video-card {
  overflow: hidden;
  border: 1px solid #cfd3d7;
  background: #0f1011;
}
.article-video {
  width: 100%;
  max-height: 82vh;
  display: block;
  background: #0f1011;
}
.video-caption {
  padding: 8px 10px;
  background: #f4f5f6;
  color: #596168;
  font-size: 12px;
  text-align: center;
}
.video-native-link {
  position: relative;
  min-height: 230px;
  display: block;
  overflow: hidden;
  background: #111315;
  color: #fff;
  text-decoration: none;
  text-align: center;
}
.video-native-thumb {
  width: 100%;
  height: 100%;
  min-height: 230px;
  max-height: 520px;
  display: block;
  object-fit: contain;
  background: #111315;
}
.video-native-placeholder {
  min-height: 230px;
  display: grid;
  place-items: center;
  padding: 24px;
  color: #b8bec4;
  background: #111315;
  font-size: 13px;
}
.video-native-link::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, .22);
}
.video-native-action {
  position: absolute;
  z-index: 1;
  inset: 0;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 10px;
  padding: 28px;
  color: #fff;
  font-size: 16px;
  font-weight: 800;
  text-shadow: 0 1px 4px rgba(0, 0, 0, .9);
}
.video-native-action span {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  border: 2px solid #fff;
  border-radius: 50%;
  font-size: 24px;
}
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
        self.videos: list[dict[str, Any]] = []
        self._active_video: dict[str, Any] | None = None
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
                    "html_class": attributes.get("class", "").strip(),
                    "html_id": attributes.get("id", "").strip(),
                })
            return
        if tag == "video":
            self._active_video = {
                "kind": "direct",
                "url": attributes.get("src", "").strip(),
                "poster": attributes.get("poster", "").strip(),
                "mime_type": attributes.get("type", "").strip().lower(),
                "width": _safe_int(attributes.get("width")),
                "height": _safe_int(attributes.get("height")),
                "html_class": attributes.get("class", "").strip(),
                "html_id": attributes.get("id", "").strip(),
                "title": attributes.get("title", "").strip(),
            }
            return
        if tag == "source" and self._active_video is not None:
            source = attributes.get("src", "").strip()
            mime_type = attributes.get("type", "").strip().lower()
            if source and not self._active_video.get("url"):
                self._active_video["url"] = source
                self._active_video["mime_type"] = mime_type
            return
        if tag == "iframe":
            source = attributes.get("src", "").strip()
            if source:
                self.videos.append({
                    "kind": "iframe",
                    "url": source,
                    "poster": "",
                    "mime_type": "text/html",
                    "width": _safe_int(attributes.get("width")),
                    "height": _safe_int(attributes.get("height")),
                    "html_class": attributes.get("class", "").strip(),
                    "html_id": attributes.get("id", "").strip(),
                    "title": attributes.get("title", "").strip(),
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
        if tag == "video" and self._active_video is not None:
            if self._active_video.get("url"):
                self.videos.append(self._active_video)
            self._active_video = None
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
    try:
        ascii_host = hostname.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValidationError("URLのホスト名またはポートが不正です") from exc
    if ":" in ascii_host and not ascii_host.startswith("["):
        ascii_host = f"[{ascii_host}]"
    netloc = f"{ascii_host}:{port}" if port is not None else ascii_host
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:;+,%@!$'()*-._~[]")
    fragment = quote(parsed.fragment, safe="=&?/:;+,%@!$'()*-._~[]")
    return urlunparse((parsed.scheme.lower(), netloc, path, parsed.params, query, fragment))


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
    except (OSError, TimeoutError, socket.timeout, UnicodeError, ValueError) as exc:
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
    except (OSError, TimeoutError, socket.timeout, urllib.error.HTTPError, UnicodeError, ValueError) as exc:
        raise ValidationError("画像を取得できませんでした") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("画像は12MB未満である必要があります")
    extension = _image_extension(data, content_type, final_url)
    if not extension:
        raise ValidationError("対応していない画像形式です")
    mime_type = "image/jpeg" if extension == ".jpg" else f"image/{extension[1:]}"
    return {"url": final_url, "data": data, "extension": extension, "mime_type": mime_type}


def _normalized_image_fingerprint(data: bytes) -> tuple[float, bytes] | None:
    try:
        from PIL import Image as PillowImage
        from PIL import ImageOps

        with PillowImage.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            if width < 1 or height < 1:
                return None
            resampling = getattr(PillowImage, "Resampling", PillowImage).LANCZOS
            normalized = image.resize((32, 32), resampling)
            return width / height, normalized.tobytes()
    except (ImportError, OSError, ValueError):
        return None


def _is_near_duplicate_image(
    fingerprint: tuple[float, bytes] | None,
    existing: list[tuple[float, bytes]],
) -> bool:
    if fingerprint is None:
        return False
    aspect_ratio, pixels = fingerprint
    for other_ratio, other_pixels in existing:
        if abs(aspect_ratio - other_ratio) / max(aspect_ratio, other_ratio) > 0.05:
            continue
        mean_square_error = sum((left - right) ** 2 for left, right in zip(pixels, other_pixels)) / len(pixels)
        if mean_square_error <= 100:
            return True
    return False


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


def _source_image_candidate_score(item: dict[str, Any]) -> int:
    url = str(item.get("url") or "").lower()
    text = " ".join(
        str(item.get(key) or "").lower()
        for key in ("url", "alt", "html_class", "html_id", "source_hint")
    )
    width = _safe_int(item.get("width"))
    height = _safe_int(item.get("height"))
    score = 0
    if item.get("source_hint") == "metadata":
        score += 35
    if "i.imgur.com" in url:
        score += 35
    if any(term in text for term in ("alignnone", "size-large", "wp-image", "eye-catch", "wp-post-image")):
        score += 45
    if width and height:
        area = width * height
        if area >= 250_000:
            score += 45
        elif area >= 90_000:
            score += 20
        if width <= 180 or height <= 120:
            score -= 90
    if re.search(r"-(?:120|135|150|180|240|300|320)x(?:120|135|150|169|180|200|210|224|225|237|245|258|277|360)\.", url):
        score -= 35
    if any(term in text for term in (
        "feedly", "follow", "logo", "favicon", "banner", "advert", "affiliate", "dmm",
        "blogparts", "ranking", "related", "recommend", "thumb120", "thumb320",
        "player_link_thumbnail", "stripchat", "counter", "web_service",
        "広告", "ランキング", "関連記事", "おすすめ", "サムネ", "サムネイル", "モザイク",
        "loli", "jk", "jc", "js",
    )):
        score -= 120
    return score


def _candidate_image_urls(parser: _SourcePageParser, base_url: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        value = parser.metadata.get(key)
        if value:
            candidates.append({
                "url": value,
                "alt": _metadata_value(parser, "og:image:alt", "twitter:image:alt"),
                "width": _safe_int(parser.metadata.get("og:image:width")),
                "height": _safe_int(parser.metadata.get("og:image:height")),
                "source_hint": "metadata",
            })
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
    unique.sort(key=_source_image_candidate_score, reverse=True)
    return unique[:MAX_SOURCE_IMAGES * 4]


def _candidate_videos(parser: _SourcePageParser, base_url: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parser.videos:
        absolute = urljoin(base_url, str(item.get("url") or ""))
        try:
            absolute = _validate_source_url(absolute)
        except ValidationError:
            continue
        if absolute in seen:
            continue
        video_text = " ".join(
            str(item.get(key) or "").lower()
            for key in ("url", "html_class", "html_id", "title")
        )
        if any(term in video_text for term in (
            "stripchat", "whitetrafsa", "blogparts", "advert", "adserver", "affiliate",
            "ranking", "widget", "counter",
        )):
            continue
        seen.add(absolute)
        kind = "iframe" if item.get("kind") == "iframe" else "direct"
        mime_type = str(item.get("mime_type") or "").split(";", 1)[0].strip().lower()
        if kind == "direct" and mime_type not in {"video/mp4", "video/webm"}:
            suffix = Path(urlparse(absolute).path).suffix.lower()
            mime_type = "video/webm" if suffix == ".webm" else "video/mp4"
        poster = urljoin(base_url, str(item.get("poster") or "")) if item.get("poster") else ""
        if poster:
            try:
                poster = _validate_source_url(poster)
            except ValidationError:
                poster = ""
        candidates.append({
            "id": f"video-{len(candidates) + 1}",
            "kind": kind,
            "url": absolute,
            "poster": poster,
            "mime_type": mime_type,
            "width": _safe_int(item.get("width")),
            "height": _safe_int(item.get("height")),
            "html_class": _trim_text(str(item.get("html_class") or ""), 160),
            "html_id": _trim_text(str(item.get("html_id") or ""), 120),
            "title": _trim_text(str(item.get("title") or ""), 180),
        })
        if len(candidates) >= MAX_SOURCE_VIDEOS:
            break
    return candidates


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
    downloaded_fingerprints: list[tuple[float, bytes]] = []
    for candidate in _candidate_image_urls(parser, final_url):
        try:
            downloaded = _download_source_image(candidate["url"], opener)
        except ValidationError:
            continue
        content_hash = hashlib.sha256(downloaded["data"]).hexdigest()
        if content_hash in downloaded_hashes:
            continue
        downloaded_hashes.add(content_hash)
        fingerprint = _normalized_image_fingerprint(downloaded["data"])
        if _is_near_duplicate_image(fingerprint, downloaded_fingerprints):
            continue
        if fingerprint is not None:
            downloaded_fingerprints.append(fingerprint)
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
            "html_class": _trim_text(str(candidate.get("html_class") or ""), 160),
            "html_id": _trim_text(str(candidate.get("html_id") or ""), 120),
            "source_score": _source_image_candidate_score(candidate),
        })
        if len(downloaded_images) >= MAX_SOURCE_IMAGES:
            break

    videos = _candidate_videos(parser, final_url)

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
        "videos": videos,
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
    selected_video_ids: Any = None,
    thumbnail_image_id: str | None = None,
) -> dict[str, Any]:
    available = {item["id"]: item for item in source.get("images", []) if isinstance(item, dict)}
    if not isinstance(selected_image_ids, list) or len(selected_image_ids) > MAX_SELECTED_SOURCE_IMAGES:
        raise ValidationError(f"画像は最大{MAX_SELECTED_SOURCE_IMAGES}枚まで選べます")
    if len(selected_image_ids) != len(set(selected_image_ids)) or any(item not in available for item in selected_image_ids):
        raise ValidationError("選択した画像が無効です")
    available_videos = {item["id"]: item for item in source.get("videos", []) if isinstance(item, dict)}
    selected_video_ids = [] if selected_video_ids is None else selected_video_ids
    if not isinstance(selected_video_ids, list) or len(selected_video_ids) > MAX_SELECTED_SOURCE_VIDEOS:
        raise ValidationError(f"動画は最大{MAX_SELECTED_SOURCE_VIDEOS}本まで選べます")
    if len(selected_video_ids) != len(set(selected_video_ids)) or any(item not in available_videos for item in selected_video_ids):
        raise ValidationError("選択した動画が無効です")
    if thumbnail_image_id is not None and thumbnail_image_id not in available:
        raise ValidationError("選択したサムネイル画像が無効です")
    ordered_image_ids = list(dict.fromkeys(
        ([thumbnail_image_id] if thumbnail_image_id else []) + selected_image_ids
    ))
    images: list[dict[str, Any]] = []
    body_image_ids: list[str] = []
    payload_ids_by_source: dict[str, str] = {}
    for index, image_id in enumerate(ordered_image_ids, start=1):
        item = available[image_id]
        payload_image_id = f"source-image-{index}"
        payload_ids_by_source[image_id] = payload_image_id
        images.append({
            "id": payload_image_id,
            "name": f"source-{index}{item['extension']}",
            "data_url": f"data:{item['mime_type']};base64,{base64.b64encode(item['data']).decode('ascii')}",
            "alt": str(item.get("alt") or source["title"])[:180],
            "orientation": item.get("orientation", "landscape"),
        })
        if image_id in selected_image_ids:
            body_image_ids.append(payload_image_id)
    if not images and isinstance(manual_image, dict):
        fallback = {**manual_image, "id": "source-image-1"}
        _decode_images([fallback])
        images.append(fallback)
    if not images:
        raise ValidationError("記事一覧のサムネイルに使う画像を1枚以上選ぶか、画像ファイルを追加してください")
    if not body_image_ids and not selected_video_ids:
        body_image_ids.append(images[0]["id"])

    videos: list[dict[str, Any]] = []
    for index, video_id in enumerate(selected_video_ids, start=1):
        item = available_videos[video_id]
        frame_data = item.get("frame_data") if isinstance(item.get("frame_data"), bytes) else b""
        videos.append({
            "id": f"source-video-{index}",
            "kind": "iframe" if item.get("kind") == "iframe" else "direct",
            "url": str(item.get("url") or "")[:2048],
            "referer": str(source.get("url") or "")[:2048],
            "mime_type": str(item.get("mime_type") or ("text/html" if item.get("kind") == "iframe" else "video/mp4"))[:80],
            "poster": str(item.get("poster") or "")[:2048],
            "poster_data_url": (
                f"data:image/jpeg;base64,{base64.b64encode(frame_data).decode('ascii')}"
                if frame_data else ""
            ),
            "label": _trim_text(str(item.get("title") or f"元記事の動画 {index}"), 180),
            "width": _safe_int(item.get("width")),
            "height": _safe_int(item.get("height")),
        })

    responses = _response_blocks(source)
    blocks: list[dict[str, Any]] = [responses[0]]
    first_image_id = (
        payload_ids_by_source.get(thumbnail_image_id or "")
        or images[0]["id"]
    )
    thumbnail_only = bool(thumbnail_image_id) and thumbnail_image_id not in selected_image_ids
    x_embed = source.get("x_embed") if isinstance(source.get("x_embed"), dict) else None
    media_blocks: list[dict[str, Any]] = []
    if videos:
        media_blocks.append({
            "id": "source-videos-1",
            "type": "videos",
            "video_ids": [video["id"] for video in videos],
        })
    if source["source_type"] == "x_post" and x_embed:
        media_blocks.append({
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
        media_blocks.append({
            "id": "x-timeline",
            "type": "x_timeline",
            "profile_url": x_embed["url"],
            "username": x_embed["username"],
            "limit": x_embed["limit"],
            "image_ids": [first_image_id],
        })
    elif body_image_ids:
        media_blocks.append({"id": "source-images-1", "type": "images", "image_ids": [body_image_ids[0]]})

    media_image_ids = {
        image_id
        for block in media_blocks
        for image_id in block.get("image_ids", [])
    }
    remaining_image_ids = [
        image_id for image_id in body_image_ids
        if image_id not in media_image_ids
    ]
    for offset in range(0, len(remaining_image_ids), 2):
        media_blocks.append({
            "id": f"source-images-{offset + 2}",
            "type": "images",
            "image_ids": remaining_image_ids[offset:offset + 2],
        })
    response_index = 1
    for media_block in media_blocks:
        blocks.append(media_block)
        if response_index < len(responses):
            blocks.append(responses[response_index])
            response_index += 1
    blocks.extend(responses[response_index:])
    blocks.append({"id": "auto-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})

    raw_title = str(source["title"])
    prefix = "【動画】" if videos else "【画像】"
    title = raw_title if raw_title.startswith("【") else f"{prefix}{raw_title}"
    source_type = str(source["source_type"])
    category = "SNS" if source_type.startswith("x_") else "動画" if videos or source_type == "youtube" else "話題"
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
        "thumbnail_only": thumbnail_only,
        "adult_confirmed": False,
        "rights_confirmed": False,
        "privacy_confirmed": False,
        "source_confirmed": False,
        "images": images,
        "videos": videos,
        "blocks": blocks,
    }


def _source_headline_samples(source: dict[str, Any], limit: int = 16) -> list[str]:
    samples: list[str] = []
    source_title = _clean_space(str(source.get("title") or ""))
    for raw in source.get("text_blocks", []) or source.get("excerpts", []):
        text = _clean_space(str(raw or ""))
        if (
            text == source_title
            or len(text) < 8
            or len(text) > 100
            or "http://" in text
            or "https://" in text
        ):
            continue
        if not re.search(r"【[^】]+】|画像|動画|写真|ｗ|w", text):
            continue
        if text not in samples:
            samples.append(text)
        if len(samples) >= limit:
            break
    return samples


def _codex_prompt(
    source: dict[str, Any],
    options: dict[str, Any],
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    requested_count = options.get("reply_count", "auto")
    reply_count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else 8
    requested_category = str(options.get("category") or "auto")
    source_facts = {
        "source_type": source.get("source_type"),
        "url": source.get("url"),
        "site_name": source.get("site_name"),
        "author": source.get("author"),
        "title": source.get("title"),
        "description": source.get("description"),
        "excerpts": source.get("excerpts", [])[:8],
        "x_post_text": (source.get("x_embed") or {}).get("text") if isinstance(source.get("x_embed"), dict) else "",
        "editorial_intent": source.get("editorial_intent", {}),
        "nearby_real_headlines_for_style_comparison": _source_headline_samples(source),
        "selected_image_context": [
            {
                "image_id": item.get("id"),
                "page_role": item.get("ai_role"),
                "relation_to_other_media": item.get("ai_relation"),
                "analysis_reason": item.get("ai_reason"),
            }
            for item in source.get("images", [])
            if isinstance(item, dict) and str(item.get("id")) in {
                str(image_id) for image_id in options.get("selected_image_ids", [])
            }
        ],
    }
    image_manifest = [
        {
            "attachment_number": index,
            "image_id": item["id"],
            "filename": item["filename"],
            "page_alt": item.get("alt", ""),
            "codex_analysis": item.get("ai_reason", ""),
        }
        for index, item in enumerate(attachments or [], start=1)
    ]
    selected_video_ids = {
        str(video_id)
        for video_id in options.get("selected_video_ids", [])
        if isinstance(video_id, str)
    }
    video_manifest = [
        {
            "video_id": item.get("id"),
            "kind": item.get("kind"),
            "source_url": item.get("url"),
            "mime_type": item.get("mime_type"),
            "html_class": item.get("html_class"),
            "html_id": item.get("html_id"),
            "title": item.get("title"),
            "codex_analysis": item.get("ai_reason", ""),
        }
        for item in source.get("videos", [])
        if isinstance(item, dict) and str(item.get("id")) in selected_video_ids
    ]
    body_image_count = 0 if video_manifest else len(image_manifest)
    selected_media_count = body_image_count + len(video_manifest)
    return f"""あなたは成人向け匿名掲示板まとめサイト『淫談屋』の編集責任者です。
語句のテンプレートを埋めるのではなく、元ページと視覚資料を読み、その題材なら人が実際にどうスレを立て、各自が何に反応するかを考えて記事を作ってください。
添付されたvideo-frames.jpgがある場合は、各マスのvideo IDと動画一覧を対応させ、映像内で実際に確認できる内容をタイトルとレスの判断材料にしてください。ページ周辺の広告や関連記事より、採用素材そのものを記事の中心にします。

出力前に内部で行う編集判断:
- 元タイトル、本文、画像、動画情報を照合し、このページ固有の見どころを具体的に把握する。
- タイトルで拾う中心を一つ選ぶ。見えている要素を全部並べず、読者が最初に気づく違和感、意外性、状況、被写体の特徴のうち最も強いものを使う。
- 各レスの書き手を別人として考え、注目点、知識量、好み、温度をずらす。
- 完成後、別の記事にもそのまま使えるタイトルやレス、同じ意味の言い換え、説明口調を見つけたら、その題材でしか出ない内容へ直す。
- 直近記事との言葉、オチ、レスの並びが似ていないか確認する。下の履歴は模倣用ではなく重複回避用である。

タイトル:
- 記事一覧で画像や動画と一緒に見たとき、何が見られる記事か一読で伝わる見出しにする。
- 内部で異なる角度の候補を少なくとも3つ考え、「画像なしでも題材が分かる」「サムネイルと矛盾しない」「日本人が説明なしで意味を取れる」の三点で最も自然な一つを選ぶ。候補や検討過程は出力しない。
- 固定の構文、文字数、語尾、俗語へ当てはめない。元ページの題名をそのまま言い換えるだけにも、内容をぼかす一般論にも逃げない。
- 複数素材がばらばらなら、無理に共通点や物語を発明しない。読者が実際に見たい人物、場面、行動、衣装などの具体物を中心にし、必要なら複数動画の記事だと率直に伝える。
- 単語を圧縮して作った造語、編集者にしか分からない抽象語、比較対象が不明な「距離感」「生活感」「強さ」などで格好を付けない。声に出して普通に読めず、説明を足さないと意味が通らない候補は捨てる。
- この編集工程へ入る素材は成人向けとして確認される前提である。裸、胸、尻、オナニー、ハメ撮りなど、実際に確認できて記事の中心になる性的内容は、成人向けまとめサイトで普通に通じる言葉で明確に書いてよい。「近距離」「接写」「雰囲気」「大胆な姿」などの無難な抽象語へ一律に言い換えて内容を隠さない。
- 卑猥な単語を入れること自体を目的やノルマにしない。映像にない行為を足さず、素材と読者の関心に最も合う具体語をその都度選ぶ。
- 目指す温度は、成人向けまとめサイトで内容が一読できる、少し下品で軽い見出しである。露骨な身体部位、体液、侮辱を重ねて過激さだけを競う文章にはしない。性的内容を隠さないことと、最大限に下品にすることを混同しない。
- 成人向け記事で慣用される属性、職業、関係性、制服などの呼び方は、読者がジャンルやフィクション上の設定として読む編集語である。個々の語を過剰に事実確認の文章へ変えず、元ページの文脈に合うなら自然な通称をそのまま使ってよい。毎回「風」「コスプレ」「設定上」「成人女性」などの注釈を足して興を削がない。
- ただしジャンル名を自然に使うことと、実在人物の具体的な年齢、学校、勤務先、資格、経歴、犯罪被害などを事実として作ることは分ける。確認できない現実の個人情報は断定せず、未成年を性的に扱わない。
- nearby_real_headlines_for_style_comparisonは元ページ周辺の実見出しである。題材や文言をコピーせず、見出しとして自然に意味が通る日本語かを比べる資料としてだけ使う。
- 【画像】か【動画】は主素材に合わせる。「まとめ」は複数素材をまとめて見せる場合だけ使う。
- 率直な表現は使ってよいが、毎回同じ身体語＋「でかすぎ」「強すぎ」「エロすぎ」「ｗｗｗ」で終わらせない。面白さが別の点にあるなら、そちらを中心にする。
- URLと視覚資料に根拠がある軽い推測は自然に扱えるが、年齢、職業、場所、国籍などを根拠なく断定しない。

レス:
- {reply_count}本の短い匿名掲示板レスとして成立させる。ただし台本のように全員が順番に会話を完成させない。
- 実際のスレでは、元投稿へ独立して反応する人、細部だけ見る人、質問する人、違う好みを言う人、少し話を外す人、前のレスへ返す人が混ざる。題材に合わせて必要なものだけ自然に選ぶ。
- 全員を親切、機転が利く、物分かりがよい人物にしない。全レスを面白くしようとせず、短い断片、素朴な反応、具体的な一言を混ぜる。
- 全員に画像や動画の説明をさせない。目に見える主役を直接言わず、状況や背景、小物、撮り方、投稿者の選び方へ反応してもよい。
- 素材について言える具体的なことがあるのに、「こういうスレ」「投稿のテンポ」「説明がない」「つい見てしまう」など記事形式そのものへのメタ感想へ逃げない。元ページ自体がその話題である場合を除き、画面の外にいる編集者の講評を書かない。
- アンカーは返答する必要が本当にある時だけ使う。使用本数のノルマはない。前後のレスが噛み合わないアンカーは使わない。
- 方言、なんJ語、古い2ch語、笑い表現を飾りとして均等に配らない。その場の書き手に自然な時だけ使い、全員を同じ口調にしない。
- 成人同士の性的内容へ反応するレスでは、実際の匿名掲示板で使われる率直な身体語や性行為の語を自然に使える。全員が上品な言い換えをする状態や、見えている性的内容を「これ」「雰囲気」「距離感」だけで済ませる状態を避ける。
- 率直さは保ちつつ、同じレス内で露骨な部位語、体液表現、侮辱語を重ねない。対象を貶めるだけの発言ではなく、見た人が実際に書きそうな軽い驚き、好み、ツッコミとして成立させる。
- 同じ形容、名詞、感想、語尾を複数人で反復しない。ただし人間らしい軽い被りまで不自然に排除する必要はない。
- video_idsはそのレスで投稿される動画を表す。動画を付けたレスは感想ではなく投稿側の発言として自然にし、動画の分け方は会話の流れから判断する。配置の帳尻より文章の自然さを優先してよい。
- 記事の中心は採用された画像・動画の中身である。元サイトの広告量、関連記事、サイドバー、運営姿勢、素材選別の是非を話題にしない。元ページ自体がそれを論じる記事の場合だけ例外とする。
- 見た目から年齢を推測しない。元資料に年齢の明記がないなら、若く見える、年齢不明、成人確認などをタイトルやレスの話題にせず、確認できる視覚内容だけを扱う。

編集上の境界:
- 元ページの文章を長くコピーしない。画像や本文にない特徴、本人の感情や経歴、個人情報、犯罪事実を作らない。
- 誹謗中傷や差別語へ頼って刺激を作らない。成人向けの俗語や卑猥な語は、成人素材として確認でき、題材と話者に合う場合は遠慮せず使える。
- categoryの希望がauto以外なら原則として従う。
- JSONスキーマに必要なtitle、summary、category、tags、responsesを返し、Markdownや講評は付けない。

X記事の目的:
- editorial_intent.content_modeがx_accountなら、単発投稿の煽り記事ではなく、そのアカウントを読者へおすすめする紹介記事にする。プロフィール、公開投稿、添付画像、紹介ポイントから「どんな投稿が見られるか」「何が魅力か」が伝わるタイトルとレスにし、本文の公式タイムラインへ自然につなげる。
- x_accountでも記事形式は最初から最後まで5ch風を守る。運営者が説明する紹介文、商品カタログ、取材記事、プレスリリースの口調にはしない。スレ主がアカウントや投稿を貼り、住民が画像、衣装、表情、撮り方、投稿内容など実際に確認できる部分へ自然に反応することで、結果として本人の良さが伝わる構成にする。
- おすすめ記事は好意的なスレにするが、全員に宣伝係のような絶賛をさせない。「この衣装ええな」「こういう表情好き」「この写真かなり強い」など、その場で素材を見た人が口にする具体的で短い反応を中心にし、好みの違い、驚き、軽いツッコミも混ぜる。同じ褒め言葉や語尾を反復しない。
- 「おすすめです」「魅力的です」「要チェックです」「フォローしたい」「フォローして損はない」「推せる」「今後に期待」のような広告文、勧誘、締めの定型句を使わない。フォロー、購入、登録、拡散などの行動を読者へ促さない。実際の5chレスとして自然な語彙と不揃いさを保つ。
- 素材から読み取れない内面、努力、人柄、ファン対応などを褒めるために作らない。見えている具体的な良さを話題にする。
- x_accountではアカウント名または@usernameが分かるタイトルにする。確認できない本名、経歴、人気度、フォロワー数、実績、投稿頻度、性格、依頼関係は作らない。
- editorial_intent.content_modeがx_postなら、指定された投稿の内容と添付素材を中心にする。アカウント全体を勝手に評価せず、その投稿の見どころと反応で記事を組む。
- editorial_intent.content_modeがfanza_productなら、作品名、出演者、メーカー、品番、見どころなど元ページで確認できる作品情報を軸にする。単なる広告文や商品カタログにはせず、作品の具体的な場面や特徴に住民が自然に反応する5ch風記事にする。購入を強要する文、効果保証、未確認の内容は作らない。FANZAへの購入ボタンとPR表示はアプリ側で付ける。
- editorial_intent.editorial_briefは編集者が希望する紹介角度であり、事実資料ではない。公開情報で裏付けられる範囲だけ反映する。
- promotion_typeがsponsoredでも不自然な絶賛や効果保証を作らない。PR表示はアプリ側で付けるため、タイトルへ毎回PRと入れる必要はない。

カテゴリー希望: {requested_category}
レス数: {reply_count}
本文画像数: {body_image_count}
サムネイル参考画像数: {len(image_manifest)}
採用動画数: {len(video_manifest)}
元ページから抽出した情報:
{json.dumps(source_facts, ensure_ascii=False, indent=2)}

サムネイル・内容把握用の添付画像:
{json.dumps(image_manifest, ensure_ascii=False, indent=2)}

記事に使用する動画:
{json.dumps(video_manifest, ensure_ascii=False, indent=2)}

直近記事の表現（コピー禁止・重複回避用）:
{json.dumps(options.get("recent_language", []), ensure_ascii=False, indent=2)}
"""


def _codex_refinement_prompt(
    source: dict[str, Any],
    options: dict[str, Any],
    draft: dict[str, Any],
) -> str:
    requested_count = options.get("reply_count", "auto")
    reply_count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else len(draft["responses"])
    selected_video_ids = [
        str(video_id) for video_id in options.get("selected_video_ids", []) if isinstance(video_id, str)
    ]
    source_facts = {
        "url": source.get("url"),
        "page_title": source.get("title"),
        "description": source.get("description"),
        "excerpts": source.get("excerpts", [])[:5],
        "nearby_real_headlines_for_style_comparison": _source_headline_samples(source),
        "selected_video_ids": selected_video_ids,
    }
    return f"""あなたは匿名掲示板の実ログと創作された「5ch風」の違いを見分ける最終編集者です。
下書きを規則へ機械的に合わせず、元資料に対する複数人の書き込みとして自然かを点検し、必要ならタイトルもレスも構成から書き直してください。

点検の観点:
- タイトルだけを初見で読んで、何が映る記事か具体的に想像できるか。意味が曖昧な造語、抽象的な共通点、編集者の分析文になっていたら、素材中の人物、場面、行動、衣装など普通の言葉へ戻す。
- 複数素材へ無理に一つの共通テーマを被せていないか。ばらばらなら、ばらばらな複数動画として自然に紹介し、存在しない物語を作らない。
- 成人向け素材の中心が裸、身体、自慰、性交などなのに、無難な抽象語だけで隠していないか。確認できる性的内容は普通に通じる具体語へ戻す。ただし映像にない行為や特徴は足さない。
- 反対に、露骨な部位語や体液表現を重ね、下品さ自体が主役になっていないか。成人向けまとめの軽い見出しとして読める範囲へ戻す。
- 成人向けジャンルの慣用表現を、不自然な注釈や婉曲表現へ直して興を削いでいないか。読者がフィクション上の設定として理解する通称は自然に使い、毎回「風」「コスプレ」「設定上」などを付けない。
- 一方で、実在人物の具体的な年齢、学校、勤務先、資格、経歴、犯罪被害など、確認できない現実の情報を事実として作っていないか。未成年を性的に扱わない。
- タイトルはこの素材固有の一番強い点を拾っているか。身体語、強調語、笑い表現を足しただけの既視感ある見出しなら別の角度を探す。
- 全員が元投稿を正確に理解し、同じ順序で褒め、前のレスへ律儀に返していないか。実際のスレらしく、独立した反応、疑問、温度差、軽い脱線を必要な範囲で残す。
- 各レスが別人の視点になっているか。同じ内容の言い換えや、全員が画像説明をする状態をなくす。
- 一つの懸念や評価を全員が繰り返していないか。同意の言い換えが続くなら、採用素材の別の具体点を見る人、単純な反応をする人、違う好みの人へ戻す。
- 短文を作るために定型句へ逃げていないか。単独ではどの記事にも置ける文が続くなら、素材の具体点へ反応させるか、説明しすぎない素朴な一言へ直す。
- 「こういうスレ」「投稿のテンポ」「説明がない」「結局全部見る」など、素材でなく記事形式を評するメタ発言は原則として外す。具体情報が乏しい時の埋め草にしない。
- 元サイトの広告、関連記事、UI、運営姿勢、素材選別の是非は、元記事自体の主題でない限り会話から外す。見た目だけで年齢を推測せず、年齢不明や成人確認を話題の代用品にしない。
- アンカーは会話を成立させる時だけ残す。会話を続けるためだけの同意アンカーや、参照先と噛み合わない返答は外す。
- 方言、スラング、俗語、笑い表現が全員へ均一に配られていないか。同じ癖を持つ一人の自作自演に見えないよう話者差を作る。
- 全員が性的内容を上品に迂回していないか。成人素材に合う書き手は率直な俗語を使ってよいが、同じ卑猥語を全員で反復したり、単語だけでレスを水増ししたりしない。
- video_ids付きレスは動画を投稿する側として読めるか。配置規則の帳尻のために文章を不自然にしない。
- 直近記事と同じタイトル構造、決まり文句、レス順を再利用していないか。履歴の文言はコピーせず、重複発見のためだけに使う。
- 見えていない特徴や経歴を作らず、元ページの長文をコピーせず、誹謗中傷や個人情報へ頼らない。

文章の巧さより、その場で別々の人が思いついて書いた不揃いさを優先してください。全レスに役割やオチを与える必要はありません。
title、summary、category、tags、responsesを{reply_count}本で指定スキーマどおり返し、Markdownや講評は付けないでください。

元ページ情報:
{json.dumps(source_facts, ensure_ascii=False, indent=2)}

推敲前の下書き:
{json.dumps(draft, ensure_ascii=False, indent=2)}

直近記事の表現（コピー禁止・重複回避用）:
{json.dumps(options.get("recent_language", []), ensure_ascii=False, indent=2)}
"""


def _codex_analysis_prompt(source: dict[str, Any], attachments: list[dict[str, Any]]) -> str:
    navigation = source.get("navigation_context", {})
    has_navigation_context = isinstance(navigation, dict) and bool(navigation)
    body_limit = 2500 if has_navigation_context else 6000
    block_limit = 10 if has_navigation_context else 20
    source_facts = {
        "source_type": source.get("source_type"),
        "url": source.get("url"),
        "site_name": source.get("site_name"),
        "author": source.get("author"),
        "extracted_title": source.get("title"),
        "extracted_description": source.get("description"),
        "text_candidates": source.get("excerpts", [])[:8],
        "rendered_body_text": str(source.get("body_text") or "")[:body_limit],
        "rendered_text_blocks": [
            str(item)[:350] for item in source.get("text_blocks", [])[:block_limit]
        ],
        "browser_capture": bool(source.get("browser_capture")),
        "page_dimensions": source.get("page_dimensions", {}),
        "image_candidate_count": len(source.get("images", [])),
        "video_candidate_count": len(source.get("videos", [])),
        "selection_policy": (
            "画像と動画は競合する選択肢ではない。ページ全体の主題と構成を理解し、"
            "本編に必要な画像と本編に必要な動画をそれぞれ独立して判定する。"
            "動画があることを理由に本文画像を除外せず、画像があることを理由に動画を除外しない。"
        ),
        "navigation_context": source.get("navigation_context", {}),
    }
    raw_links = [item for item in source.get("links", []) if isinstance(item, dict)]
    navigation_text = " ".join(
        str(navigation.get(key) or "")
        for key in ("from_title", "followed_link_text", "follow_reason")
    ) if isinstance(navigation, dict) else ""
    navigation_pairs = {
        navigation_text[index:index + 2]
        for index in range(max(0, len(navigation_text) - 1))
        if not navigation_text[index:index + 2].isspace()
    }

    def link_priority(item: dict[str, Any]) -> tuple[int, int]:
        text = str(item.get("text") or "")
        overlap = sum(1 for pair in navigation_pairs if pair in text)
        y = int((item.get("browser_rect") or {}).get("y") or 0)
        return overlap, -y

    if navigation_pairs:
        prioritized_links = sorted(raw_links, key=link_priority, reverse=True)[:60]
        seen_link_urls = {str(item.get("url") or "") for item in prioritized_links}
        prioritized_links.extend(
            item for item in raw_links
            if str(item.get("url") or "") not in seen_link_urls
        )
    else:
        prioritized_links = raw_links
    link_manifest = [
        {
            "url": item.get("url", ""),
            "text": str(item.get("text", ""))[:240],
            "contains_image": item.get("contains_image", False),
            "page_rect": item.get("browser_rect", {}),
            "surrounding_text": str(item.get("browser_context", ""))[:180],
            "dom_ancestors": str(item.get("browser_ancestors", ""))[:180],
            "font_size": item.get("font_size", ""),
            "font_weight": item.get("font_weight", ""),
            "color": item.get("color", ""),
            "background": item.get("background", ""),
        }
        for item in prioritized_links
    ][:25 if has_navigation_context else 50]
    manifest = [
        {
            "image_id": item.get("id"), "source_url": item.get("url", ""),
            "html_alt": item.get("alt", ""), "declared_width": item.get("width", 0),
            "declared_height": item.get("height", 0), "visible": item.get("browser_visible"),
            "page_rect": item.get("browser_rect", {}), "surrounding_text": str(item.get("browser_context", ""))[:220],
            "dom_ancestors": str(item.get("browser_ancestors", ""))[:180], "link_target": item.get("browser_link_url", ""),
        }
        for item in source.get("images", []) if isinstance(item, dict)
    ]
    evidence = [
        {"attachment_number": index, "id": item.get("id"), "filename": item.get("filename"), "kind": item.get("kind", "candidate")}
        for index, item in enumerate(attachments, start=1)
    ]
    video_manifest = [
        {
            "video_id": item.get("id"),
            "kind": item.get("kind"),
            "source_url": item.get("url"),
            "mime_type": item.get("mime_type"),
            "html_class": item.get("html_class"),
            "html_id": item.get("html_id"),
            "title": item.get("title"),
            "declared_width": item.get("width", 0),
            "declared_height": item.get("height", 0),
            "page_rect": item.get("browser_rect", {}),
            "surrounding_text": str(item.get("browser_context", ""))[:220],
            "dom_ancestors": str(item.get("browser_ancestors", ""))[:180],
        }
        for item in source.get("videos", [])
        if isinstance(item, dict)
    ]
    return f"""あなたは、URL先を実ブラウザで調査して記事素材を決めるCodex編集責任者です。
プログラムが意味で候補を選んだとは考えず、レンダリング後のページ全体と添付証拠をあなた自身で見て判断してください。

あなたの役割:
- ページ全景画像でヘッダー、記事本文、広告、関連記事、ランキング、フッターの境界を把握する。
- 番号付き候補一覧とDOM上の座標・周辺文・祖先要素・リンク先を照合し、本文素材を選ぶ。
- video/source/iframeだけでなく、ブラウザ通信で検出した動画も照合し、本編を漏らさない。
- 静的HTML、WordPress、JavaScript遅延読込、画像ギャラリー、動画中心、SNS埋め込みなど構造が違っても同じ目的で判断する。
- ファイル名やドメインだけで決めず、画面上の位置と記事主題との関係を最優先する。
- プログラム側は観測と保存しか担当しない。何が記事本文か、何を採用するかはあなたが責任を持って決める。
- 最初に、現在のページが本編そのものか、本編への入口・紹介カード・中継ページかを判断する。
- 現在ページが少数のプレビューと目立つ記事リンクだけを示し、その先にギャラリー、動画、本文がある構造ならpage_roleをgatewayにする。follow_urlには、提示されたリンク一覧から本編へ進むURLを一字も変えずに入れる。
- 中継は一段とは限らない。リンク先も入口なら後工程が再解析するため、その時点で最も妥当な次の本編導線を選ぶ。
- navigation_contextがある場合は、前ページで実際に選んだリンク文と目的を引き継いでいる。リンク集のページタイトルや先頭記事が別内容でも、それへ横滑りせず、前ページで選ばれた主題・リンク文・遷移URLに対応する続きを探す。
- リンク集やアンテナでは、受け取ったURLのクエリに転送先が符号化・逆順化されている場合がある。リンク文との一致も使い、同じ記事を指す最終リンクを選ぶ。単に画面の先頭、最大文字、最大画像という理由だけでは選ばない。
- 広告、購入誘導、無関係な関連記事、サイトナビゲーションは追わない。URLの文字だけでなく、リンク文、強調表示、本文との位置関係、前後の説明、遷移先の目的を総合する。
- page_roleがarticle、index、unclearならfollow_urlを空文字にする。gateway以外では追跡を要求しない。

目的:
- ページの本編素材が何を扱い、何を見せる記事かを自然な日本語のtitleとdescriptionにまとめる。descriptionには広告、関連記事、UIの説明を混ぜず、それらの判別結果はanalysis_summaryだけに書く。
- 各画像がそのページ内で実際に何をしているかを、ページ固有の言葉で把握する。その理解をもとにサムネイル・本文・両方・除外のどこで使うか決める。
- ページから回収した動画・埋め込み候補を、記事本編か広告・導線・無関係か判定する。
- 記事画像だけを後工程の初期選択候補にするため、厳しめに分類する。

FANZA関連判定:
- 記事の主題、人物名、作品名、品番、衣装、行為、ジャンル、動画周辺文から、FANZA作品への関連度を判定する。
- 特定の商品URLまたは品番が確認できる場合だけexact_product、ページ本文・画像周辺文・投稿者情報などから出演者名が確認でき、記事素材との対応も強い場合はlikely_productにする。ジャンルや体型だけしか分からない場合はrelatedにできるが、検索語とPRは作らない。成人向け商品と結びつかない場合はnoneにする。
- 各画像について、同一人物の連続カットか、別人が混ざるか、名前を示す見出し・キャプション・リンク文が近くにあるかを確認する。記事の中心人物をページ上の根拠から特定できた場合だけfanza_performer_nameへ正式な出演者名を入れる。複数人で誰の画像か対応できない場合は空文字にする。
- fanza_search_queryには、確認できた出演者名、作品名、品番のいずれかを使った実際に検索可能な短い語句だけを入れる。「Gカップ 爆乳 AV女優」「制服 巨乳」など体型・衣装・ジャンルを並べただけの語句は禁止し、特定情報がなければ空文字にする。
- 人物の顔だけから本名や出演作品を推測しない。ページ本文、画像のalt・キャプション、投稿本文、作品情報などで名前が確認できる場合だけ使う。
- fanza_product_codeはページ内で確認できた場合だけ返す。fanza_reasonには判定根拠を簡潔に書く。

画像判定ルール:
- roleは固定分類ではなく自由記述である。「一覧用サムネイル」「本文の主画像」「同一人物の追加カット」「関連記事カード」「広告」などは例にすぎない。これらに当てはまらない役割を発見したら、そのページに合う名前を自分で付ける。
- recommended_useは画像の意味を分類する欄ではなく、理解した後の配置指示だけを表す。thumbnailは一覧用、bodyは本文用、thumbnail_and_bodyは両方、excludeは不採用である。
- article: ページの主題に直接関係する人物、作品、商品、出来事、投稿画像。
- advertisement: 広告バナー、アフィリエイト、別商品の宣伝、スポンサー枠。
- logo: サイトロゴ、サービスロゴ、ブランドだけの画像。
- navigation: Feedly、SNSフォロー、ランキング、関連記事、ボタン、アイコン、UI画像。
- unrelated: 記事テーマと無関係な写真や別記事のサムネイル。
- unclear: 小さすぎる、内容を判断できないもの。
- 人物画像という理由だけでarticleにせず、本文の主題との一致を確認する。
- ページ内の座標、DOMの親要素、リンク先、前後の文章から、記事カード用画像と本文内画像と関連記事カードを区別する。
- 同じ人物・場面・素材の画像には同じcontent_groupを付ける。モザイク版、切り抜き、縮小版、無修正版などの関係はrelationへ具体的に書く。
- 一覧表示にはモザイク版が適切で本文には鮮明版がある場合、前者をarticle_thumbnailかつthumbnail、後者をarticle_mainかつbodyにする。モザイクという語だけで機械的に除外しない。
- 同じ被写体のモザイク・ぼかし・トリミング版と鮮明版が併存し、前者が一覧や入口、後者が本文で使われているなら、モザイク版を本文用にしてはならない。本文の1枚目は鮮明版にする。
- OGPや記事先頭の画像でも、本文画像の縮小・加工版ならサムネイル専用にできる。反対に関連記事へのリンク画像は見た目が主題に近くてもrelated_articleかつexcludeにする。
- 本文の話の流れと複数画像の共通点から、誰・何をどんな魅力で紹介するページかを判断し、title、description、roleへ反映する。
- 同じ用途の完全な重複がある場合は最も鮮明な1枚だけを採用し、他はexcludeにする。ただしサムネイル版と本文版で役割が異なる重複は両方残せる。
- relevance_scoreは記事との直接的な関連度を0から100で付ける。
- image_idは画像一覧にある値を一字も変えずに返す。
- 画像一覧にある全画像についてimage_decisionsを1件ずつ返す。
- 動画はタグ種別だけで決めない。direct動画でも広告の場合があり、iframeでも記事本編の場合がある。URLのドメイン、パス、HTMLのclass/id/title、ページ本文との一致から判断する。
- videoタグ内のMP4、投稿本文と同じ場所にあるプレイヤー、記事タイトルと一致する動画はarticle候補。ライブチャット広告、ランキング、ブログパーツ、別サイト誘導はadvertisementかnavigationにする。
- 動画一覧にある全候補についてvideo_decisionsを1件ずつ返し、video_idを一字も変えない。
- ページに素材が見えているのに候補一覧へ存在しない場合は、analysis_summaryへ「回収漏れ」と対象を明記し、無関係候補で代用しない。
- Markdown、HTML、前置き、解説を返さず、指定スキーマのJSONだけを返す。

ページ情報:
{json.dumps(source_facts, ensure_ascii=False, indent=2)}

添付したブラウザ証拠:
{json.dumps(evidence, ensure_ascii=False, indent=2)}

添付画像一覧:
{json.dumps(manifest, ensure_ascii=False, indent=2)}

動画・埋め込み候補一覧:
{json.dumps(video_manifest, ensure_ascii=False, indent=2)}

画面内リンク候補一覧:
{json.dumps(link_manifest, ensure_ascii=False, indent=2)}
"""


def _validate_codex_analysis(value: Any, source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Codexの解析結果がJSONオブジェクトではありません")
    title = _require_text(value, "title", 180)
    description = _require_text(value, "description", 500)
    category = _require_text(value, "category", 40)
    page_role = str(value.get("page_role") or "unclear")
    if page_role not in {"article", "gateway", "index", "unclear"}:
        page_role = "unclear"
    follow_url = _optional_text(value, "follow_url", 2048)
    follow_reason = _optional_text(value, "follow_reason", 300)
    available_links = {
        str(item.get("url") or "")
        for item in source.get("links", [])
        if isinstance(item, dict) and item.get("url")
    }
    if page_role != "gateway":
        follow_url = ""
    elif follow_url not in available_links:
        page_role = "unclear"
        follow_url = ""
        follow_reason = "候補一覧にないリンクが返されたため追跡を中止しました"
    summary = _require_text(value, "analysis_summary", 500)
    fanza_relevance = str(value.get("fanza_relevance") or "none")
    if fanza_relevance not in {"none", "related", "likely_product", "exact_product"}:
        fanza_relevance = "none"
    fanza_performer_name = _optional_text(value, "fanza_performer_name", 80)
    fanza_search_query = _optional_text(value, "fanza_search_query", 120)
    fanza_product_code = _optional_text(value, "fanza_product_code", 40)
    fanza_reason = _optional_text(value, "fanza_reason", 240)
    if category not in {"SNS", "画像", "動画", "話題"}:
        raise ValidationError("Codexが未対応のカテゴリーを返しました")
    available = {
        str(item.get("id")): item
        for item in source.get("images", [])
        if isinstance(item, dict) and item.get("id")
    }
    raw_decisions = value.get("image_decisions")
    if not isinstance(raw_decisions, list):
        raise ValidationError("Codexの画像判定が不正です")
    decisions: dict[str, dict[str, Any]] = {}
    verdicts = {"article", "advertisement", "logo", "navigation", "unrelated", "unclear"}
    uses = {"thumbnail", "body", "thumbnail_and_body", "exclude"}
    for item in raw_decisions:
        if not isinstance(item, dict):
            continue
        image_id = str(item.get("image_id") or "")
        if image_id not in available or image_id in decisions:
            continue
        verdict = str(item.get("verdict") or "unclear")
        if verdict not in verdicts:
            verdict = "unclear"
        role = _trim_text(str(item.get("role") or "役割不明"), 80)
        recommended_use = str(item.get("recommended_use") or "exclude")
        if recommended_use not in uses:
            recommended_use = "exclude"
        try:
            score = max(0, min(100, int(item.get("relevance_score", 0))))
        except (TypeError, ValueError):
            score = 0
        reason = _trim_text(str(item.get("reason") or "判定理由なし"), 160)
        decisions[image_id] = {
            "image_id": image_id,
            "verdict": verdict,
            "role": role,
            "recommended_use": recommended_use,
            "content_group": _trim_text(str(item.get("content_group") or ""), 80),
            "relation": _trim_text(str(item.get("relation") or ""), 160),
            "relevance_score": score,
            "reason": reason,
        }
    for image_id in available:
        decisions.setdefault(image_id, {
            "image_id": image_id,
            "verdict": "unclear",
            "role": "unclear",
            "recommended_use": "exclude",
            "content_group": "",
            "relation": "",
            "relevance_score": 0,
            "reason": "Codexが判定を返しませんでした",
        })
    available_videos = {
        str(item.get("id")): item
        for item in source.get("videos", [])
        if isinstance(item, dict) and item.get("id")
    }
    raw_video_decisions = value.get("video_decisions")
    if not isinstance(raw_video_decisions, list):
        raise ValidationError("Codexの動画判定が不正です")
    video_decisions: dict[str, dict[str, Any]] = {}
    video_verdicts = {"article", "advertisement", "navigation", "unrelated", "unclear"}
    for item in raw_video_decisions:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("video_id") or "")
        if video_id not in available_videos or video_id in video_decisions:
            continue
        verdict = str(item.get("verdict") or "unclear")
        if verdict not in video_verdicts:
            verdict = "unclear"
        try:
            score = max(0, min(100, int(item.get("relevance_score", 0))))
        except (TypeError, ValueError):
            score = 0
        video_decisions[video_id] = {
            "video_id": video_id,
            "verdict": verdict,
            "relevance_score": score,
            "reason": _trim_text(str(item.get("reason") or "判定理由なし"), 160),
        }
    for video_id in available_videos:
        video_decisions.setdefault(video_id, {
            "video_id": video_id,
            "verdict": "unclear",
            "relevance_score": 0,
            "reason": "Codexが判定を返しませんでした",
        })
    return {
        "title": title,
        "description": description,
        "category": category,
        "page_role": page_role,
        "follow_url": follow_url,
        "follow_reason": follow_reason,
        "analysis_summary": summary,
        "fanza_relevance": fanza_relevance,
        "fanza_performer_name": fanza_performer_name,
        "fanza_search_query": fanza_search_query,
        "fanza_product_code": fanza_product_code,
        "fanza_reason": fanza_reason,
        "image_decisions": list(decisions.values()),
        "video_decisions": list(video_decisions.values()),
    }


def apply_codex_analysis(source: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    result = {**source}
    result["title"] = analysis["title"]
    result["description"] = analysis["description"]
    result["ai_category"] = analysis["category"]
    result["ai_page_role"] = analysis.get("page_role", "article")
    result["ai_follow_url"] = analysis.get("follow_url", "")
    result["ai_follow_reason"] = analysis.get("follow_reason", "")
    result["ai_analysis_summary"] = analysis["analysis_summary"]
    result["ai_fanza_relevance"] = analysis.get("fanza_relevance", "none")
    result["ai_fanza_performer_name"] = analysis.get("fanza_performer_name", "")
    result["ai_fanza_search_query"] = analysis.get("fanza_search_query", "")
    result["ai_fanza_product_code"] = analysis.get("fanza_product_code", "")
    result["ai_fanza_reason"] = analysis.get("fanza_reason", "")
    result["analysis_method"] = "codex_vision"
    decisions = {item["image_id"]: item for item in analysis["image_decisions"]}
    images: list[dict[str, Any]] = []
    recommended: list[str] = []
    recommended_thumbnails: list[str] = []
    recommended_body: list[str] = []
    for image in source.get("images", []):
        if not isinstance(image, dict):
            continue
        decision = decisions.get(str(image.get("id")), {})
        enriched = {
            **image,
            "ai_verdict": decision.get("verdict", "unclear"),
            "ai_role": decision.get("role", "unclear"),
            "ai_recommended_use": decision.get("recommended_use", "exclude"),
            "ai_content_group": str(decision.get("content_group") or ""),
            "ai_relation": str(decision.get("relation") or ""),
            "ai_relevance_score": int(decision.get("relevance_score", 0)),
            "ai_reason": str(decision.get("reason") or "判定理由なし"),
        }
        enriched["ai_recommended"] = (
            enriched["ai_verdict"] == "article"
            and enriched["ai_recommended_use"] != "exclude"
            and enriched["ai_relevance_score"] >= 40
        )
        if enriched["ai_recommended_use"] in {"thumbnail", "thumbnail_and_body"}:
            recommended_thumbnails.append(str(enriched["id"]))
        if enriched["ai_recommended_use"] in {"body", "thumbnail_and_body"}:
            recommended_body.append(str(enriched["id"]))
        if enriched["ai_recommended"] and len(recommended) < MAX_SELECTED_SOURCE_IMAGES:
            recommended.append(str(enriched["id"]))
        images.append(enriched)
    result["images"] = images
    result["recommended_image_ids"] = recommended
    result["recommended_thumbnail_ids"] = recommended_thumbnails
    result["recommended_body_image_ids"] = recommended_body
    video_decisions = {item["video_id"]: item for item in analysis.get("video_decisions", [])}
    videos: list[dict[str, Any]] = []
    recommended_videos: list[str] = []
    for video in source.get("videos", []):
        if not isinstance(video, dict):
            continue
        decision = video_decisions.get(str(video.get("id")), {})
        enriched = {
            **video,
            "ai_verdict": decision.get("verdict", "unclear"),
            "ai_relevance_score": int(decision.get("relevance_score", 0)),
            "ai_reason": str(decision.get("reason") or "判定理由なし"),
        }
        enriched["ai_recommended"] = (
            enriched["ai_verdict"] == "article" and enriched["ai_relevance_score"] >= 55
        )
        if enriched["ai_recommended"] and len(recommended_videos) < MAX_SELECTED_SOURCE_VIDEOS:
            recommended_videos.append(str(enriched["id"]))
        videos.append(enriched)
    result["videos"] = videos
    result["recommended_video_ids"] = recommended_videos
    return result


def _codex_image_attachments(
    source: dict[str, Any],
    selected_image_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for index, item in enumerate(source.get("images", []), start=1):
        if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
            continue
        image_id = str(item.get("id") or f"media-{index}")
        if selected_image_ids is not None and image_id not in selected_image_ids:
            continue
        original_extension = str(item.get("extension") or ".jpg")
        filename = f"attachment-{index:02d}-{re.sub(r'[^a-zA-Z0-9-]', '-', image_id)}{original_extension}"
        data = item["data"]
        try:
            from PIL import Image as PillowImage

            with PillowImage.open(io.BytesIO(data)) as opened:
                opened.seek(0)
                image = opened.convert("RGB")
                image.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=86, optimize=True)
                data = buffer.getvalue()
                filename = f"attachment-{index:02d}-{re.sub(r'[^a-zA-Z0-9-]', '-', image_id)}.jpg"
        except (ImportError, OSError, ValueError):
            pass
        attachments.append({
            "id": image_id,
            "filename": filename,
            "data": data,
            "url": str(item.get("url") or ""),
            "alt": str(item.get("alt") or ""),
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
            "ai_reason": str(item.get("ai_reason") or ""),
        })
        if len(attachments) >= MAX_SELECTED_SOURCE_IMAGES:
            break
    return attachments


def _codex_visual_attachments(
    source: dict[str, Any],
    content_attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    context = [
        item
        for item in source.get("browser_attachments", [])
        if isinstance(item, dict) and isinstance(item.get("data"), bytes)
    ]
    combined: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in [*context[:3], *content_attachments]:
        filename = str(item.get("filename") or "")
        if not filename or filename in seen_names:
            continue
        seen_names.add(filename)
        combined.append(item)
    return combined


def _codex_generation_attachments(
    source: dict[str, Any],
    content_attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    video_evidence = [
        item
        for item in source.get("browser_attachments", [])
        if isinstance(item, dict)
        and item.get("id") == "video-frame-sheet"
        and isinstance(item.get("data"), bytes)
    ]
    return _codex_visual_attachments({"browser_attachments": video_evidence}, content_attachments)


def _recent_draft_language(site_root: Path, limit: int = 12) -> list[dict[str, Any]]:
    draft_root = site_root / ".article-studio" / "drafts"
    if not draft_root.exists():
        return []
    history: list[dict[str, Any]] = []
    paths = sorted(draft_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        responses = [
            _clean_space(str(block.get("text") or ""))
            for block in payload.get("blocks", [])
            if isinstance(block, dict) and block.get("type") == "post" and _clean_space(str(block.get("text") or ""))
        ]
        history.append({
            "title": _trim_text(str(payload.get("title") or ""), 180),
            "responses": responses[:10],
        })
        if len(history) >= limit:
            break
    return history


def _normalize_codex_title(title: str, category: str, selected_media_count: int | None) -> str:
    normalized = _clean_space(title)
    generic_suffixes = (
        "をめぐり匿名掲示板で反応集まる",
        "をめぐり5ch民が反応",
        "に5ch民が反応",
        "に反応集まる",
        "に注目集まる",
        "が話題に",
        "が話題",
    )
    for suffix in generic_suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].rstrip(" 、。")
            break
    if selected_media_count is not None and selected_media_count <= 1:
        normalized = re.sub(r"(画像|写真|動画)まとめ", r"\1", normalized)
        normalized = normalized.replace("まとめ", "").rstrip(" 、。")
    if normalized and not normalized.startswith("【"):
        prefix = "【動画】" if category == "動画" else "【画像】" if selected_media_count else ""
        normalized = f"{prefix}{normalized}"
    return _trim_text(normalized, 180)


def _validate_codex_result(
    value: Any,
    requested_count: Any = "auto",
    selected_media_count: int | None = None,
    selected_video_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Codexの生成結果がJSONオブジェクトではありません")
    raw_title = _require_text(value, "title", 180)
    summary = _require_text(value, "summary", 240)
    category = _require_text(value, "category", 40)
    if category not in {"SNS", "画像", "動画", "話題"}:
        raise ValidationError("Codexが未対応のカテゴリーを返しました")
    title = _normalize_codex_title(raw_title, category, selected_media_count)
    if not title:
        raise ValidationError("Codexの記事タイトルが不正です")
    raw_tags = value.get("tags")
    if not isinstance(raw_tags, list):
        raise ValidationError("Codexの記事タグが不正です")
    tags = list(dict.fromkeys(_clean_space(str(tag)) for tag in raw_tags if _clean_space(str(tag))))[:8]
    if not tags or any(len(tag) > 40 for tag in tags):
        raise ValidationError("Codexの記事タグが不正です")
    raw_responses = value.get("responses")
    if not isinstance(raw_responses, list) or not 3 <= len(raw_responses) <= 12:
        raise ValidationError("Codexのレス数が不正です")
    ordered_video_ids = list(dict.fromkeys(selected_video_ids or []))
    available_video_ids = set(ordered_video_ids)
    responses: list[dict[str, Any]] = []
    for item in raw_responses:
        if not isinstance(item, dict):
            raise ValidationError("Codexのレス形式が不正です")
        text = _require_text(item, "text", 500)
        style = str(item.get("style") or "normal")
        if style not in {"normal", "large", "highlight"}:
            style = "normal"
        response_video_ids = item.get("video_ids")
        if not isinstance(response_video_ids, list):
            response_video_ids = []
        responses.append({
            "text": text,
            "style": style,
            "video_ids": [
                video_id for video_id in response_video_ids
                if isinstance(video_id, str) and video_id in available_video_ids
            ][:2],
        })
    count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else len(responses)
    responses = responses[:count]
    if len(responses) < min(3, count):
        raise ValidationError("Codexが必要なレス数を返しませんでした")
    seen_video_ids: set[str] = set()
    for response in responses:
        unique_video_ids: list[str] = []
        for video_id in response["video_ids"]:
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            unique_video_ids.append(video_id)
        response["video_ids"] = unique_video_ids

    if ordered_video_ids:
        first_video_id = ordered_video_ids[0]
        for response in responses:
            if response is not responses[0] and first_video_id in response["video_ids"]:
                response["video_ids"].remove(first_video_id)
        if first_video_id not in responses[0]["video_ids"]:
            if len(responses[0]["video_ids"]) >= 2:
                responses[0]["video_ids"].pop()
            responses[0]["video_ids"].insert(0, first_video_id)

        used_video_ids = {
            video_id for response in responses for video_id in response["video_ids"]
        }
        missing_video_ids = [video_id for video_id in ordered_video_ids if video_id not in used_video_ids]
        posting_texts = ("次これも置いとく", "あとこれ", "最後これも")
        for video_id in missing_video_ids:
            candidates = [response for response in responses if response["video_ids"] and len(response["video_ids"]) < 2]
            if not candidates:
                candidates = [response for response in responses if not response["video_ids"]]
            if not candidates:
                raise ValidationError("動画を配置できるレスが不足しています")
            target = candidates[0]
            if not target["video_ids"] and target is not responses[0]:
                target["text"] = posting_texts[min(responses.index(target) - 1, len(posting_texts) - 1)]
            target["video_ids"].append(video_id)
    return {"title": title, "summary": summary, "category": category, "tags": tags, "responses": responses}


def apply_codex_result(base_payload: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    payload = {**base_payload}
    payload["title"] = generated["title"]
    payload["summary"] = generated["summary"]
    payload["category"] = generated["category"]
    payload["tags"] = generated["tags"]
    payload["comments"] = len(generated["responses"])
    payload["generation_method"] = "codex"
    payload["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")

    media_blocks = [
        {**block}
        for block in base_payload.get("blocks", [])
        if isinstance(block, dict) and block.get("type") in {"images", "videos", "x_embed", "x_timeline"}
    ]
    base_video_ids = {
        str(video.get("id"))
        for video in base_payload.get("videos", [])
        if isinstance(video, dict) and video.get("id")
    }
    response_blocks = [
        {
            "id": f"codex-post-{index}",
            "type": "post",
            "text": item["text"],
            "style": item["style"],
        }
        for index, item in enumerate(generated["responses"], start=1)
    ]
    blocks: list[dict[str, Any]] = []
    if base_video_ids:
        non_video_media = [
            block
            for block in media_blocks
            if block.get("type") in {"images", "x_embed", "x_timeline"}
        ]
        media_index = 0
        for index, (response, generated_response) in enumerate(zip(response_blocks, generated["responses"]), start=1):
            blocks.append(response)
            attached_video_ids = generated_response.get("video_ids", [])
            if attached_video_ids:
                blocks.append({
                    "id": f"codex-videos-{index}",
                    "type": "videos",
                    "video_ids": attached_video_ids[:],
                })
            if media_index < len(non_video_media):
                blocks.append(non_video_media[media_index])
                media_index += 1
        blocks.extend(non_video_media[media_index:])
    else:
        response_index = 0
        if response_blocks:
            blocks.append(response_blocks[0])
            response_index = 1
        for media in media_blocks:
            blocks.append(media)
            if response_index < len(response_blocks):
                blocks.append(response_blocks[response_index])
                response_index += 1
        blocks.extend(response_blocks[response_index:])
    blocks.append({"id": "codex-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})
    payload["blocks"] = blocks
    return payload


class CodexRunner:
    def __init__(self, site_root: Path, executable: str | Path | None = None) -> None:
        self.site_root = site_root.resolve()
        self.executable = Path(executable).resolve() if executable else self._find_executable()
        self._status: dict[str, Any] | None = None

    @staticmethod
    def _find_executable() -> Path | None:
        candidates: list[Path] = []
        configured = os.environ.get("CODEX_CLI", "").strip()
        if configured:
            candidates.append(Path(configured))
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            app_bin = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
            if app_bin.is_dir():
                candidates.extend(sorted(app_bin.glob("*/codex.exe"), key=lambda path: path.stat().st_mtime, reverse=True))
        on_path = shutil.which("codex")
        if on_path:
            candidates.append(Path(on_path))
        return next((path.resolve() for path in candidates if path.is_file()), None)

    def status(self) -> dict[str, Any]:
        if self._status is not None:
            return dict(self._status)
        if not self.executable:
            self._status = {"available": False, "version": "", "message": "Codex CLIが見つかりません"}
            return dict(self._status)
        try:
            completed = subprocess.run(
                [str(self.executable), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._status = {"available": False, "version": "", "message": f"Codexを起動できません: {exc}"}
            return dict(self._status)
        version = _clean_space(completed.stdout or completed.stderr)
        available = completed.returncode == 0
        self._status = {
            "available": available,
            "version": version if available else "",
            "message": "Codex接続済み" if available else "Codexを起動できません",
        }
        return dict(self._status)

    def _execute(
        self,
        prompt: str,
        schema_path: Path,
        *,
        attachments: list[dict[str, Any]] | None = None,
        run_prefix: str = "run-",
    ) -> Any:
        status = self.status()
        if not status["available"] or not self.executable:
            raise ValidationError(status["message"])
        if not schema_path.is_file():
            raise ValidationError("Codex出力スキーマが見つかりません")
        work_root = self.site_root / ".article-studio" / "codex-runs"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=run_prefix, dir=work_root) as temporary:
            temporary_root = Path(temporary)
            output_path = temporary_root / "result.json"
            command = [
                str(self.executable), "exec", "--ephemeral", "--ignore-rules",
                "--sandbox", "read-only", "--skip-git-repo-check",
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
                "--color", "never", "--cd", str(self.site_root), "-",
            ]
            for attachment in attachments or []:
                image_path = temporary_root / str(attachment["filename"])
                image_path.write_bytes(attachment["data"])
                command[2:2] = ["--image", str(image_path)]
            environment = os.environ.copy()
            for name in ("CODEX_CI", "CODEX_THREAD_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
                environment.pop(name, None)
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=CODEX_TIMEOUT_SECONDS,
                    check=False,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise ValidationError("Codexの処理が時間切れになりました。もう一度実行してください") from exc
            except OSError as exc:
                raise ValidationError(f"Codexを起動できません: {exc}") from exc
            if completed.returncode != 0:
                raw_detail = completed.stderr or completed.stdout or "unknown error"
                detail = _trim_text(raw_detail[-4000:], 1000)
                lowered_detail = raw_detail.lower()
                if "rate limit" in lowered_detail or "usage limit" in lowered_detail:
                    raise ValidationError("Codexの利用上限に達しました。時間を置いて再実行してください")
                if any(message in lowered_detail for message in (
                    "not logged in", "login required", "authentication required",
                    "unauthorized", "oauth token",
                )):
                    raise ValidationError("Codexのログインを確認してください")
                raise ValidationError(f"Codex処理に失敗しました: {detail}")
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationError("Codexの結果を読み込めませんでした") from exc

    def analyze(self, source: dict[str, Any]) -> dict[str, Any]:
        browser_attachments = [
            item for item in source.get("browser_attachments", [])
            if isinstance(item, dict) and isinstance(item.get("data"), bytes)
        ]
        attachments = browser_attachments or _codex_image_attachments(source)
        value = self._execute(
            _codex_analysis_prompt(source, attachments),
            CODEX_ANALYSIS_SCHEMA_PATH,
            attachments=attachments,
            run_prefix="analysis-",
        )
        return _validate_codex_analysis(value, source)

    def generate(self, source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        prompt_options = {
            **options,
            "recent_language": _recent_draft_language(self.site_root),
        }
        selected_ids = {
            str(image_id)
            for image_id in prompt_options.get("selected_image_ids", [])
            if isinstance(image_id, str)
        }
        content_attachments = _codex_image_attachments(source, selected_ids)
        visual_attachments = _codex_generation_attachments(source, content_attachments)
        selected_video_ids = list(dict.fromkeys(
            str(video_id)
            for video_id in prompt_options.get("selected_video_ids", [])
            if isinstance(video_id, str)
        ))
        selected_video_count = len(selected_video_ids)
        value = self._execute(
            _codex_prompt(source, prompt_options, content_attachments),
            CODEX_SCHEMA_PATH,
            attachments=visual_attachments,
            run_prefix="article-",
        )
        result = _validate_codex_result(
            value,
            prompt_options.get("reply_count", "auto"),
            selected_media_count=selected_video_count + len(content_attachments),
            selected_video_ids=selected_video_ids,
        )
        refined_value = self._execute(
            _codex_refinement_prompt(source, prompt_options, result),
            CODEX_SCHEMA_PATH,
            attachments=visual_attachments,
            run_prefix="refine-",
        )
        result = _validate_codex_result(
            refined_value,
            prompt_options.get("reply_count", "auto"),
            selected_media_count=selected_video_count + len(content_attachments),
            selected_video_ids=selected_video_ids,
        )
        payload_video_ids = {
            source_video_id: f"source-video-{index}"
            for index, source_video_id in enumerate(selected_video_ids, start=1)
        }
        for response in result["responses"]:
            response["video_ids"] = [payload_video_ids[video_id] for video_id in response["video_ids"]]
        return result

    def refine_existing(
        self,
        payload: dict[str, Any],
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        for block in payload.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "post":
                responses.append({
                    "text": str(block.get("text") or ""),
                    "style": str(block.get("style") or "normal"),
                    "video_ids": [],
                })
            elif block.get("type") == "videos" and responses:
                responses[-1]["video_ids"].extend(
                    str(video_id) for video_id in block.get("video_ids", []) if isinstance(video_id, str)
                )
        if len(responses) < 3:
            raise ValidationError("推敲できるレスが不足しています")
        selected_video_ids = [
            str(item.get("id")) for item in payload.get("videos", [])
            if isinstance(item, dict) and item.get("id")
        ]
        draft = {
            "title": str(payload.get("title") or ""),
            "summary": str(payload.get("summary") or ""),
            "category": str(payload.get("category") or "話題"),
            "tags": list(payload.get("tags") or []),
            "responses": responses,
        }
        source = {
            "url": str(payload.get("source_url") or ""),
            "title": draft["title"],
            "description": draft["summary"],
            "excerpts": [],
        }
        visual_attachments: list[dict[str, Any]] = []
        if source_context:
            source.update({
                "url": str(source_context.get("url") or source["url"]),
                "title": str(source_context.get("title") or source["title"]),
                "description": str(source_context.get("description") or source["description"]),
                "excerpts": list(source_context.get("text_blocks") or source_context.get("excerpts") or [])[:12],
                "body_text": str(source_context.get("body_text") or "")[:16000],
            })
            visual_attachments = _codex_generation_attachments(source_context, [])
        options = {
            "reply_count": "auto",
            "selected_video_ids": selected_video_ids,
            "recent_language": _recent_draft_language(self.site_root),
        }
        refined_value = self._execute(
            _codex_refinement_prompt(source, options, draft),
            CODEX_SCHEMA_PATH,
            attachments=visual_attachments,
            run_prefix="refine-existing-",
        )
        return _validate_codex_result(
            refined_value,
            "auto",
            selected_media_count=len(selected_video_ids) + len(payload.get("images", [])),
            selected_video_ids=selected_video_ids,
        )


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


def _validate_videos(raw_videos: Any) -> list[dict[str, Any]]:
    if raw_videos is None:
        return []
    if not isinstance(raw_videos, list) or len(raw_videos) > MAX_SELECTED_SOURCE_VIDEOS:
        raise ValidationError(f"videos must contain 0 to {MAX_SELECTED_SOURCE_VIDEOS} items")
    videos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_videos, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"video {index} must be an object")
        video_id = _require_text(raw, "id", 120)
        if video_id in seen_ids:
            raise ValidationError(f"duplicate video id: {video_id}")
        seen_ids.add(video_id)
        kind = str(raw.get("kind") or "direct")
        if kind not in {"direct", "iframe"}:
            raise ValidationError(f"video {index} kind is invalid")
        video_url = _validate_source_url(_require_text(raw, "url", 2048))
        poster_url = _validate_source_url(_optional_text(raw, "poster", 2048)) if raw.get("poster") else ""
        poster_data_url = str(raw.get("poster_data_url") or "")
        if poster_data_url:
            if not poster_data_url.startswith("data:image/jpeg;base64,"):
                raise ValidationError(f"video {index} poster must be a JPEG data URL")
            try:
                poster_data = base64.b64decode(poster_data_url.split(",", 1)[1], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValidationError(f"video {index} poster contains invalid base64") from exc
            if not poster_data or len(poster_data) > 3 * 1024 * 1024 or not _validate_magic(".jpg", poster_data):
                raise ValidationError(f"video {index} poster is invalid")
            poster_data_url = f"data:image/jpeg;base64,{base64.b64encode(poster_data).decode('ascii')}"
        mime_type = _optional_text(raw, "mime_type", 80) or ("text/html" if kind == "iframe" else "video/mp4")
        if kind == "direct" and mime_type not in {"video/mp4", "video/webm"}:
            raise ValidationError(f"video {index} type is unsupported")
        label = _optional_text(raw, "label", 180) or f"元記事の動画 {index}"
        videos.append({
            "id": video_id,
            "kind": kind,
            "url": video_url,
            "referer": _validate_source_url(_optional_text(raw, "referer", 2048)) if raw.get("referer") else "",
            "poster": poster_url,
            "poster_data_url": poster_data_url,
            "mime_type": mime_type,
            "label": label,
            "width": _safe_int(raw.get("width")),
            "height": _safe_int(raw.get("height")),
        })
    return videos


def _validate_blocks(
    raw_blocks: Any,
    images: tuple[ImageAsset, ...],
    videos: list[dict[str, Any]] | None = None,
    thumbnail_only_image_id: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(raw_blocks, list) or not raw_blocks or len(raw_blocks) > 120:
        raise ValidationError("blocks must contain 1 to 120 items")

    image_ids = {image.image_id for image in images}
    video_ids = {str(video["id"]) for video in videos or []}
    used_images: list[str] = []
    used_videos: list[str] = []
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
        elif block_type == "videos":
            selected = raw.get("video_ids")
            if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_SELECTED_SOURCE_VIDEOS:
                raise ValidationError(
                    f"block {index} must contain 1 to {MAX_SELECTED_SOURCE_VIDEOS} videos"
                )
            if any(not isinstance(item, str) or item not in video_ids for item in selected):
                raise ValidationError(f"block {index} references an unknown video")
            used_videos.extend(selected)
            blocks.append({"type": "videos", "video_ids": selected[:]})
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
        elif block_type == "product_cta":
            url = _require_text(raw, "url", 2048)
            parsed = urlparse(_validate_source_url(url))
            hostname = (parsed.hostname or "").lower()
            if not (
                hostname == "dmm.co.jp"
                or hostname.endswith(".dmm.co.jp")
                or hostname == "fanza.co.jp"
                or hostname.endswith(".fanza.co.jp")
            ):
                raise ValidationError(f"block {index} product URL must point to DMM or FANZA")
            blocks.append({
                "type": "product_cta",
                "url": url,
                "title": _require_text(raw, "title", 180),
                "text": _optional_text(raw, "text", 300),
                "button_text": _optional_text(raw, "button_text", 80) or "FANZAで作品を見る",
            })
        else:
            raise ValidationError(f"block {index} has an unknown type")

    if post_count < 1:
        raise ValidationError("the article needs at least one response")
    if len(used_images) != len(set(used_images)):
        raise ValidationError("each image can be placed only once")
    optional_image_ids = (
        {thumbnail_only_image_id} if thumbnail_only_image_id in image_ids else set()
    )
    missing = sorted(image_ids - set(used_images) - optional_image_ids)
    if missing:
        raise ValidationError("all images must be placed: " + ", ".join(missing))
    if len(used_videos) != len(set(used_videos)):
        raise ValidationError("each video can be placed only once")
    if set(used_videos) != video_ids:
        missing = sorted(video_ids - set(used_videos))
        raise ValidationError("all videos must be placed: " + ", ".join(missing))
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
    search_parts = [title, summary, category, *tags]
    for block in payload.get("blocks") or []:
        if isinstance(block, dict):
            for field in ("text", "label", "author_name", "username"):
                value = block.get(field)
                if isinstance(value, str):
                    search_parts.append(value)
    metadata["search_text"] = " ".join(part.strip() for part in search_parts if part and part.strip())[:12000]
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
    videos: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    *,
    preview: bool,
) -> str:
    style, script = _extract_sample_assets(site_root)
    image_map = {image.image_id: image for image in images}
    video_map = {str(video["id"]): video for video in videos}
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

    def render_video_group(selected_ids: list[str]) -> str:
        cards: list[str] = []
        for video_id in selected_ids:
            video = video_map[video_id]
            video_url = str(video["url"])
            referer = str(video.get("referer") or metadata.get("source_url") or "")
            proxy_query = f"url={quote(video_url, safe='')}&referer={quote(referer, safe='')}"
            playable_url = f"/api/video-proxy?{proxy_query}" if preview and video["kind"] == "direct" else video_url
            source = html.escape(playable_url, quote=True)
            label = html.escape(str(video["label"]))
            if video["kind"] == "iframe":
                player = (
                    f'<iframe class="article-video" src="{source}" title="{label}" loading="lazy" '
                    'sandbox="allow-scripts allow-same-origin allow-presentation" allowfullscreen></iframe>'
                )
            elif preview:
                poster_url = str(video.get("poster_data_url") or "")
                poster_markup = (
                    f'<img class="video-native-thumb" src="{html.escape(poster_url, quote=True)}" alt="{label}">'
                    if poster_url
                    else '<div class="video-native-placeholder">この動画のサムネイルを取得できませんでした</div>'
                )
                player = (
                    f'<a class="video-native-link" href="indanya-video://play/{quote(video_id, safe="")}">'
                    f'{poster_markup}'
                    '<span class="video-native-action"><span>▶</span>動画を再生</span></a>'
                )
            else:
                mime_type = html.escape(str(video["mime_type"]), quote=True)
                poster_source = str(video.get("poster_data_url") or "")
                poster_attribute = (
                    f' poster="{html.escape(poster_source, quote=True)}"' if poster_source else ""
                )
                player = (
                    f'<video class="article-video" controls playsinline preload="metadata"{poster_attribute}>'
                    f'<source src="{source}" type="{mime_type}">動画を再生できません。</video>'
                )
            cards.append(f'<div class="video-card">{player}<div class="video-caption">{label}</div></div>')
        return f'<div class="video-group">{"".join(cards)}</div>'

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
        elif block["type"] == "videos":
            rendered_blocks.append(render_video_group(block["video_ids"]))
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
        elif block["type"] == "product_cta":
            rendered_blocks.append(
                '<aside class="fanza-product">'
                '<div class="fanza-product-label">PR / FANZA</div>'
                f'<p class="fanza-product-title">{html.escape(block["title"])}</p>'
                f'<p class="fanza-product-text">{html.escape(block["text"])}</p>'
                f'<a class="fanza-product-button" href="{html.escape(block["url"], quote=True)}" '
                'target="_blank" rel="sponsored noopener noreferrer">'
                f'{html.escape(block["button_text"])}</a></aside>'
            )

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
    page_root = "/site/" if preview else "../"
    title = str(metadata["title"])
    summary = str(metadata.get("summary", title))
    sidebar = _render_sidebar(site_root, metadata, blocks)
    has_x_embeds = any(block["type"] in {"x_embed", "x_timeline"} for block in blocks)
    complete_style = style + ARTICLE_DISCOVERY_STYLE + VIDEO_EMBED_STYLE + FANZA_PRODUCT_STYLE + (X_EMBED_STYLE if has_x_embeds else "")
    style_markup = '<link rel="stylesheet" href="/preview.css">' if preview else f"<style>{complete_style}</style>"
    media_count_label = f"動画{len(videos)}本" if videos else f"画像{len(images)}枚"
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
  <a href="{page_root}latest.html">新着</a><a href="{page_root}search.html?category=画像">画像</a><a href="{page_root}search.html?category=SNS">SNS</a><a href="{page_root}tags.html">タグ</a><a href="{page_root}popular.html">人気記事</a><a href="{page_root}random.html">ランダム</a>
</div></nav>
<div class="site-search"><form action="{page_root}search.html" method="get"><label class="sr-only" for="articleSearch">記事を検索</label><input id="articleSearch" name="q" type="search" placeholder="タイトル・本文・タグから検索"><button type="submit">検索</button></form></div>
<main class="page">
  <div class="breadcrumb"><a href="{home_href}">淫談屋</a> ＞ {html.escape(str(metadata["category"]))} ＞ {html.escape(title)}</div>
  <div class="layout"><article class="article">
    <header class="article-head"><h1 class="article-title">{html.escape(title)}</h1>
      <div class="article-meta"><span>{metadata["display_date"]}</span><span>{metadata["comments"]} コメント</span><span>{media_count_label}</span></div>
    </header>
    <div class="thread">{"".join(rendered_blocks)}</div>
  </article>{sidebar}</div>
</main>
<div class="lightbox" id="lightbox" aria-hidden="true"><button class="lightbox-close" id="lightboxClose" aria-label="閉じる">×</button><img id="lightboxImage" alt="拡大画像"></div>
<footer class="footer"><div class="footer-inner"><span>© 2026 淫談屋</span><span><a href="{page_root}about.html">運営者情報</a>　<a href="{page_root}contact.html">お問い合わせ</a>　<a href="{page_root}privacy.html">プライバシーポリシー</a></span></div></footer>
<script>{script}</script>
{x_widgets}
</body>
</html>
'''


def build_article(payload: dict[str, Any], site_root: Path = SITE_ROOT, *, preview: bool = False) -> ArticleBuild:
    if not isinstance(payload, dict):
        raise ValidationError("article payload must be an object")
    images = _decode_images(payload.get("images"))
    videos = _validate_videos(payload.get("videos"))
    thumbnail_only_image_id = (
        str(payload.get("thumbnail_id") or images[0].image_id)
        if videos or payload.get("thumbnail_only") is True
        else ""
    )
    blocks = _validate_blocks(payload.get("blocks"), images, videos, thumbnail_only_image_id)
    metadata = _make_metadata(payload, images, site_root)
    article_html = render_article(site_root, payload, metadata, images, videos, blocks, preview=preview)
    normalized_payload = {**payload, "videos": videos, "blocks": blocks}
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
            "rights_contact": str(payload.get("rights_contact") or "")[:200],
            "rights_note": str(payload.get("rights_note") or "")[:500],
            "source_url": str(payload.get("source_url") or "")[:2048],
            "category": str(payload.get("category") or "")[:40],
            "generation_method": str(payload.get("generation_method") or "manual")[:40],
            "published_url": str(payload.get("published_url") or "")[:2048],
            "published_site_id": str(payload.get("published_site_id") or "")[:120],
            "published_site_name": str(payload.get("published_site_name") or "")[:120],
            "published_at": str(payload.get("published_at") or "")[:40],
            "review_status": str(payload.get("review_status") or (
                "published" if payload.get("published_url") else "unreviewed"
            ))[:40],
            "review_message": str(payload.get("review_message") or "")[:500],
            "summary": str(payload.get("summary") or "")[:240],
            "tags": [
                str(tag)[:40] for tag in payload.get("tags", []) if isinstance(tag, str)
            ][:12] if isinstance(payload.get("tags"), list) else [],
            "image_count": len(payload.get("images", [])) if isinstance(payload.get("images"), list) else 0,
            "video_count": len(payload.get("videos", [])) if isinstance(payload.get("videos"), list) else 0,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, JST).isoformat(),
            "size": path.stat().st_size,
        })
    return drafts


def update_draft_rights(
    slug: str,
    rights_status: Any,
    rights_contact: Any = "",
    rights_note: Any = "",
    site_root: Path = SITE_ROOT,
) -> dict[str, Any]:
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("invalid draft slug")
    status = str(rights_status or "").strip()
    if status not in RIGHTS_STATUSES:
        raise ValidationError("画像利用の状態が不正です")
    contact = _clean_space(str(rights_contact or ""))
    note = str(rights_note or "").strip()
    if len(contact) > 200 or len(note) > 500:
        raise ValidationError("許可管理のメモが長すぎます")
    draft_path = site_root / ".article-studio" / "drafts" / f"{slug}.json"
    if not draft_path.is_file():
        raise ValidationError("下書きが見つかりません")
    try:
        payload = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("下書きを読み込めませんでした") from exc
    if not isinstance(payload, dict):
        raise ValidationError("下書きの形式が不正です")
    payload["rights_status"] = status
    payload["rights_confirmed"] = status == "confirmed"
    payload["rights_contact"] = contact
    payload["rights_note"] = note
    payload["rights_updated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    save_draft(payload, site_root)
    return {
        "slug": slug,
        "rights_status": status,
        "rights_contact": contact,
        "rights_note": note,
        "message": "許可状態を更新しました",
    }


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
            "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self' https:",
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

    def _send_video_proxy(self, video_url: str, referer: str = "") -> None:
        normalized_url = _validate_source_url(video_url)
        headers = {
            "Accept": "video/mp4,video/webm,video/*;q=0.9",
            "User-Agent": "Mozilla/5.0 (IndanyaArticleStudio/2.0)",
        }
        if referer:
            headers["Referer"] = _validate_source_url(referer)
        range_header = self.headers.get("Range", "").strip()
        if range_header and re.fullmatch(r"bytes=[0-9]*-[0-9]*", range_header):
            headers["Range"] = range_header
        request = urllib.request.Request(normalized_url, headers=headers)
        try:
            response = self.studio_server.url_opener.open(request, timeout=30) if self.studio_server.url_opener else urllib.request.urlopen(request, timeout=30)
        except (OSError, TimeoutError, socket.timeout, urllib.error.HTTPError) as exc:
            raise ValidationError("動画を取得できませんでした") from exc
        with response:
            final_url = _validate_source_url(response.geturl() if hasattr(response, "geturl") else normalized_url)
            del final_url
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
            if content_type not in {"video/mp4", "video/webm"}:
                raise ValidationError("動画形式を確認できませんでした")
            content_length = _safe_int(response.headers.get("Content-Length"))
            if content_length > MAX_VIDEO_PROXY_BYTES:
                raise ValidationError("動画が大きすぎます")
            status = getattr(response, "status", None) or (response.getcode() if hasattr(response, "getcode") else 200)
            self.send_response(status if status in {200, 206} else 200)
            self.send_header("Content-Type", content_type)
            if content_length:
                self.send_header("Content-Length", str(content_length))
            content_range = response.headers.get("Content-Range")
            if content_range:
                self.send_header("Content-Range", content_range)
            self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges", "bytes"))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            sent = 0
            try:
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if sent > MAX_VIDEO_PROXY_BYTES:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

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
                    "codex": self.studio_server.codex_runner.status(),
                    "jobs": self.studio_server.list_jobs(),
                })
                return
            if path.startswith("/api/jobs/"):
                self._require_token()
                job_id = path.removeprefix("/api/jobs/")
                self._send_json({"job": self.studio_server.get_job(job_id)})
                return
            if path == "/api/video-proxy":
                values = parse_qs(parsed.query)
                video_url = str((values.get("url") or [""])[0])
                referer = str((values.get("referer") or [""])[0])
                if not video_url:
                    raise ValidationError("動画URLがありません")
                self._send_video_proxy(video_url, referer)
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
                self._send_bytes(
                    (style + ARTICLE_DISCOVERY_STYLE + X_EMBED_STYLE + VIDEO_EMBED_STYLE + FANZA_PRODUCT_STYLE).encode("utf-8"),
                    "text/css; charset=utf-8",
                )
                return
            if path == "/desktop-preview.html":
                preview_html = self.studio_server.desktop_preview_html
                if not preview_html:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_bytes(preview_html.encode("utf-8"), "text/html; charset=utf-8")
                return

            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
            self._serve_file(STATIC_ROOT / relative, STATIC_ROOT)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
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
                source = apply_codex_analysis(source, self.studio_server.codex_runner.analyze(source))
                session_id = self.studio_server.store_source_session(source)
                public_images = [{
                    "id": item["id"],
                    "alt": item.get("alt", ""),
                    "orientation": item.get("orientation", "landscape"),
                    "width": item.get("width", 0),
                    "height": item.get("height", 0),
                    "ai_verdict": item.get("ai_verdict", "unclear"),
                    "ai_relevance_score": item.get("ai_relevance_score", 0),
                    "ai_reason": item.get("ai_reason", ""),
                    "ai_recommended": bool(item.get("ai_recommended")),
                    "preview_url": f"/api/source/media/{quote(session_id)}/{quote(str(item['id']))}",
                } for item in source.get("images", []) if isinstance(item, dict)]
                public_videos = [{
                    "id": item["id"],
                    "kind": item.get("kind", "direct"),
                    "url": item.get("url", ""),
                    "mime_type": item.get("mime_type", "video/mp4"),
                    "width": item.get("width", 0),
                    "height": item.get("height", 0),
                    "title": item.get("title", ""),
                    "html_class": item.get("html_class", ""),
                    "ai_verdict": item.get("ai_verdict", "unclear"),
                    "ai_relevance_score": item.get("ai_relevance_score", 0),
                    "ai_reason": item.get("ai_reason", ""),
                    "ai_recommended": bool(item.get("ai_recommended")),
                    "preview_url": (
                        f"/api/video-proxy?url={quote(str(item.get('url') or ''), safe='')}"
                        f"&referer={quote(str(source.get('url') or ''), safe='')}"
                    ) if item.get("kind") != "iframe" else item.get("url", ""),
                } for item in source.get("videos", []) if isinstance(item, dict)]
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
                        "category": source["ai_category"],
                        "analysis_summary": source["ai_analysis_summary"],
                        "analysis_method": source["analysis_method"],
                    },
                    "images": public_images,
                    "videos": public_videos,
                    "recommended_image_ids": source["recommended_image_ids"],
                    "recommended_video_ids": source.get("recommended_video_ids", []),
                    "needs_image_upload": not source["recommended_image_ids"],
                })
            elif self.path == "/api/source/draft":
                session_id = _require_text(payload, "session_id", 200)
                source = self.studio_server.get_source_session(session_id)
                draft = build_source_draft_payload(
                    source,
                    payload.get("selected_image_ids"),
                    payload.get("manual_image"),
                    payload.get("selected_video_ids"),
                )
                self._send_json({"payload": draft})
            elif self.path == "/api/source/generate":
                session_id = _require_text(payload, "session_id", 200)
                source = self.studio_server.get_source_session(session_id)
                job = self.studio_server.create_source_job(
                    source,
                    payload.get("selected_image_ids"),
                    payload.get("selected_video_ids"),
                    payload.get("manual_image"),
                    {
                        "category": payload.get("category", "auto"),
                        "reply_count": payload.get("reply_count", "auto"),
                        "tone": payload.get("tone", "thread"),
                    },
                )
                self._send_json({"job": job}, HTTPStatus.ACCEPTED)
            elif self.path.startswith("/api/rights/"):
                slug = self.path.removeprefix("/api/rights/")
                result = update_draft_rights(
                    slug,
                    payload.get("rights_status"),
                    payload.get("rights_contact"),
                    payload.get("rights_note"),
                    self.studio_server.site_root,
                )
                self._send_json(result)
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
        codex_runner: Any = None,
    ) -> None:
        self.site_root = site_root.resolve()
        self.api_token = secrets.token_urlsafe(32)
        self.x_bearer_token = x_bearer_token if x_bearer_token is not None else os.environ.get("X_BEARER_TOKEN", "")
        self.url_opener = url_opener or urllib.request.build_opener()
        self.codex_runner = codex_runner or CodexRunner(self.site_root)
        self.x_sessions: dict[str, tuple[float, dict[str, Any]]] = {}
        self.source_sessions: dict[str, tuple[float, dict[str, Any]]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.desktop_preview_html = ""
        self.x_session_lock = threading.Lock()
        self.source_session_lock = threading.Lock()
        self.job_lock = threading.Lock()
        self._recover_jobs()
        super().__init__(address, StudioHandler)

    @property
    def job_root(self) -> Path:
        return self.site_root / ".article-studio" / "jobs"

    def _write_job(self, job: dict[str, Any]) -> None:
        self.job_root.mkdir(parents=True, exist_ok=True)
        destination = self.job_root / f"{job['id']}.json"
        temporary = self.job_root / f".{job['id']}.{secrets.token_hex(3)}.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)

    def _recover_jobs(self) -> None:
        if not self.job_root.exists():
            return
        for path in self.job_root.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"queued", "running"}:
                job.update({
                    "status": "failed",
                    "stage": "stopped",
                    "message": "記事編集室が終了したため生成が中断されました",
                    "error": "URLをもう一度解析して再実行してください",
                    "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
                })
                self._write_job(job)
            self.jobs[job["id"]] = job

    def _update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self.job_lock:
            job = self.jobs[job_id]
            job.update(changes)
            job["updated_at"] = datetime.now(JST).isoformat(timespec="seconds")
            snapshot = dict(job)
            self._write_job(snapshot)
        return snapshot

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.job_lock:
            jobs = [dict(job) for job in self.jobs.values()]
        return sorted(jobs, key=lambda job: str(job.get("created_at", "")), reverse=True)[:20]

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{24}", job_id):
            raise ValidationError("invalid job id")
        with self.job_lock:
            job = self.jobs.get(job_id)
        if not job:
            raise ValidationError("生成ジョブが見つかりません")
        return dict(job)

    def create_source_job(
        self,
        source: dict[str, Any],
        selected_image_ids: Any,
        selected_video_ids: Any,
        manual_image: Any,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.codex_runner.status().get("available"):
            raise ValidationError(self.codex_runner.status().get("message") or "Codexへ接続できません")
        category = str(options.get("category") or "auto")
        reply_count = str(options.get("reply_count") or "auto")
        if category not in {"auto", "SNS", "画像", "動画", "話題"}:
            raise ValidationError("カテゴリー設定が不正です")
        if reply_count not in {"auto", "5", "8", "10"}:
            raise ValidationError("レス数の設定が不正です")
        base_payload = build_source_draft_payload(
            source,
            selected_image_ids,
            manual_image,
            selected_video_ids,
        )
        normalized_selected_video_ids = [str(video_id) for video_id in (selected_video_ids or [])]
        normalized_selected_ids = [str(image_id) for image_id in selected_image_ids]
        if normalized_selected_video_ids:
            normalized_selected_ids = normalized_selected_ids[:1]
        job_id = secrets.token_hex(12)
        now = datetime.now(JST).isoformat(timespec="seconds")
        job = {
            "id": job_id,
            "kind": "source_article",
            "status": "queued",
            "stage": "queued",
            "progress": 5,
            "message": "生成待ち",
            "error": "",
            "source_title": str(source.get("title") or "")[:180],
            "source_url": str(source.get("url") or "")[:2048],
            "slug": "",
            "created_at": now,
            "updated_at": now,
        }
        with self.job_lock:
            self.jobs[job_id] = job
            self._write_job(job)
        worker = threading.Thread(
            target=self._run_source_job,
            args=(job_id, source, base_payload, {
                **options,
                "category": category,
                "reply_count": reply_count,
                "selected_image_ids": normalized_selected_ids,
                "selected_video_ids": normalized_selected_video_ids,
            }),
            daemon=True,
            name=f"codex-article-{job_id[:8]}",
        )
        worker.start()
        return dict(job)

    def _run_source_job(
        self,
        job_id: str,
        source: dict[str, Any],
        base_payload: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        try:
            self._update_job(
                job_id,
                status="running",
                stage="writing",
                progress=25,
                message="Codexが記事を書いています",
            )
            generated = self.codex_runner.generate(source, options)
            if options["category"] != "auto":
                generated["category"] = options["category"]
            self._update_job(job_id, stage="saving", progress=85, message="下書きへ保存しています")
            payload = apply_codex_result(base_payload, generated)
            slug = save_draft(payload, self.site_root)
            self._update_job(
                job_id,
                status="completed",
                stage="completed",
                progress=100,
                message="記事下書きが完成しました",
                slug=slug,
                error="",
            )
        except Exception as exc:  # Worker failures are returned through the job API.
            message = str(exc) or exc.__class__.__name__
            self._update_job(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                message="記事生成に失敗しました",
                error=_trim_text(message, 800),
            )

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
