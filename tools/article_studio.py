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
from indanya_desktop.analytics import ANALYTICS_VERSION  # noqa: E402
from indanya_desktop.affiliate_opportunities import (  # noqa: E402
    detect_affiliate_opportunities,
)
from indanya_desktop.fanza_affiliate import (  # noqa: E402
    FanzaAffiliateConfigurationError,
    bind_payload_fanza_affiliate_links,
    canonicalize_payload_fanza_links,
    load_fanza_settings,
    unwrap_fanza_affiliate_url,
)
from indanya_desktop.official_work_registry import (  # noqa: E402
    enrich_analysis_official_work,
)
from indanya_desktop.related_links import (  # noqa: E402
    ensure_related_footer,
    is_empty_related_ad,
    sanitize_related_destinations,
)
from indanya_desktop.social_profiles import (  # noqa: E402
    validate_social_verification,
)


SITE_ROOT = TOOLS_ROOT.parent
STATIC_ROOT = TOOLS_ROOT / "article_studio_app"
DRAFT_ROOT = SITE_ROOT / ".article-studio" / "drafts"
JOB_ROOT = SITE_ROOT / ".article-studio" / "jobs"
CODEX_SCHEMA_PATH = TOOLS_ROOT / "article_studio_codex_schema.json"
CODEX_ANALYSIS_SCHEMA_PATH = TOOLS_ROOT / "article_studio_codex_analysis_schema.json"
SOCIAL_PROFILE_VERIFICATION_SCHEMA_PATH = TOOLS_ROOT / "social_profile_verification_schema.json"
X_TREND_TEMPLATE_SCHEMA_PATH = TOOLS_ROOT / "x_trend_templates_schema.json"
CODEX_COMBINED_SCHEMA_NAME = "codex-analysis-and-article-schema.json"
CODEX_ANALYSIS_CACHE_VERSION = "2026-09-01-person-identity-2"
_DRAFT_PAYLOAD_CACHE: dict[Path, tuple[int, int, dict[str, Any]]] = {}
_DRAFT_PAYLOAD_CACHE_LOCK = threading.Lock()
MAX_REQUEST_BYTES = 110 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_X_POSTS = 20
MAX_X_SELECTED_POSTS = 6
X_SESSION_SECONDS = 30 * 60
MAX_SOURCE_PAGE_BYTES = 6 * 1024 * 1024
MAX_SOURCE_IMAGES = 36
MAX_VIDEO_PROXY_BYTES = 160 * 1024 * 1024
SOURCE_SESSION_SECONDS = 60 * 60
CODEX_TIMEOUT_SECONDS = 12 * 60
CODEX_ARTICLE_MODEL = "gpt-5.6-luna"
CODEX_ARTICLE_REASONING_EFFORT = "high"
CODEX_ANALYSIS_IMAGE_BATCH = 30
CODEX_ANALYSIS_VIDEO_BATCH = 12
CODEX_GENERATION_IMAGE_SAMPLE = 16
CODEX_GENERATION_VIDEO_SAMPLE = 12
CODEX_DIRECT_IMAGE_LIMIT = 6
CODEX_CONTACT_SHEET_ITEMS = 12
CODEX_CONTACT_SHEET_COLUMNS = 3
CODEX_CONTACT_SHEET_CELL = (420, 330)
RIGHTS_STATUSES = {"unconfirmed", "requested", "confirmed", "rejected"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ANCHOR_PATTERN = re.compile(r"&gt;&gt;([0-9]+)")
X_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_POST_ID_PATTERN = re.compile(r"^[0-9]{1,19}$")
ALLOWED_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
X_MEDIA_HOSTS = {"pbs.twimg.com"}
JST = ZoneInfo("Asia/Tokyo")


def _is_dmm_fanza_host(hostname: str) -> bool:
    return (
        hostname == "dmm.co.jp"
        or hostname.endswith(".dmm.co.jp")
        or hostname == "dmm.com"
        or hostname.endswith(".dmm.com")
        or hostname == "fanza.co.jp"
        or hostname.endswith(".fanza.co.jp")
    )

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
  border: 0;
  border-left: 4px solid #c72d22;
  background: #f7f7f5;
}
.fanza-product-media {
  display: grid;
  grid-template-columns: minmax(120px, 220px) 1fr;
  gap: 18px;
  align-items: center;
}
.fanza-product-media.no-thumb { display: block; }
.fanza-product-thumb {
  display: block;
  width: 100%;
  max-height: 260px;
  object-fit: contain;
  background: #fff;
}
.fanza-product-label {
  margin-bottom: 8px;
  color: #c72d22;
  font-size: 12px;
  font-weight: 800;
}
.fanza-product-audit {
  margin: -2px 0 10px;
  padding: 7px 9px;
  border: 1px solid #d8d5ce;
  color: #555;
  font-size: 11px;
  line-height: 1.5;
  background: #fff;
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
.fanza-product-button.is-disabled {
  background: #6b6b68;
  cursor: not-allowed;
}
.article-destination {
  margin: 26px 0;
  padding: 18px;
  border: 0;
  border-left: 4px solid #14877d;
  background: #f7f7f5;
}
.article-destination .fanza-product-label { color: #0b746c; }
.article-destination-button {
  display: block;
  padding: 13px 18px;
  background: #17191c;
  color: #fff !important;
  font-weight: 800;
  text-align: center;
  text-decoration: none;
}
.article-destination-button:hover { background: #14877d; }
.side-ad.side-ad-link {
  display: block;
  min-height: 0;
  padding: 13px 14px;
  border: 0;
  border-left: 3px solid #c72d22;
  background: #f7f7f5;
  color: #17191c;
  text-align: left;
  text-decoration: none;
}
.side-ad-link-thumb {
  display: block;
  width: 100%;
  max-height: 220px;
  margin-bottom: 10px;
  object-fit: contain;
  background: #fff;
}
.side-ad-link:hover { background: #ececea; }
.side-ad-link-label {
  display: block;
  margin-bottom: 5px;
  color: #c72d22;
  font-size: 10px;
  font-weight: 800;
}
.side-ad-link-title {
  display: block;
  color: #17191c;
  font-size: 13px;
  font-weight: 800;
  line-height: 1.55;
}
.side-ad-link-action {
  display: block;
  margin-top: 8px;
  color: #555;
  font-size: 11px;
}
@media (max-width: 620px) {
  .fanza-product-media { grid-template-columns: 1fr; }
  .fanza-product-thumb { max-height: 320px; }
}
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
iframe.article-video {
  border: 0;
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
    related_thumbnail_only: bool = False
    thumbnail_owner_url: str = ""
    rights_basis: str = ""


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
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
    ARTICLE_BODY_HINTS = (
        "article-body", "article-content", "article__body", "article__content",
        "blog-entry-body", "content-body", "entry-body", "entry-content",
        "entry_body", "entry_text", "main-article-content", "post-body",
        "post-content", "post-text", "post_body", "post_text", "single-post-content",
        "story-body",
    )
    EXCLUDED_MEDIA_HINTS = (
        "above-content", "ad-area", "advert", "affiliate", "breadcrumb", "comment",
        "feedly", "footer", "header", "mobile-ad", "mobile_ad", "navigation",
        "player-link", "popular", "ranking", "recommend", "related", "share",
        "sidebar", "social", "under-entry", "widget",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.canonical_url = ""
        self.text_items: list[tuple[str, str]] = []
        self.article_text_items: list[tuple[str, str]] = []
        self.images: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []
        self.affiliate_resources: list[dict[str, str]] = []
        self._context_stack: list[dict[str, Any]] = []
        self._active_video: dict[str, Any] | None = None
        self._ignored_depth = 0
        self._capture_tag = ""
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        self._context_stack.append(self._context_entry(tag, attributes))
        if tag in {"script", "iframe"} and attributes.get("src", "").strip():
            self.affiliate_resources.append({
                "kind": tag,
                "url": attributes["src"].strip(),
            })
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            if tag in self.VOID_TAGS:
                self._pop_context(tag)
            return
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            value = attributes.get("content", "").strip()
            if key and value and key not in self.metadata:
                self.metadata[key] = value
            self._pop_context(tag)
            return
        if tag == "link" and "canonical" in attributes.get("rel", "").lower():
            self.canonical_url = attributes.get("href", "").strip()
            self._pop_context(tag)
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
                    **self._media_context(),
                })
            self._pop_context(tag)
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
                **self._media_context(),
            }
            return
        if tag == "source" and self._active_video is not None:
            source = attributes.get("src", "").strip()
            mime_type = attributes.get("type", "").strip().lower()
            if source and not self._active_video.get("url"):
                self._active_video["url"] = source
                self._active_video["mime_type"] = mime_type
            self._pop_context(tag)
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
                    **self._media_context(),
                })
            return
        if tag in self.TEXT_TAGS and not self._capture_tag:
            self._capture_tag = tag
            self._capture_parts = []
        if tag in self.VOID_TAGS:
            self._pop_context(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            self._pop_context(tag)
            return
        if self._ignored_depth:
            self._pop_context(tag)
            return
        if tag == "video" and self._active_video is not None:
            if self._active_video.get("url"):
                self.videos.append(self._active_video)
            self._active_video = None
        if tag == self._capture_tag:
            value = _clean_space("".join(self._capture_parts))
            if value:
                self.text_items.append((tag, value))
                if self._media_context()["inside_article"]:
                    self.article_text_items.append((tag, value))
            self._capture_tag = ""
            self._capture_parts = []
        self._pop_context(tag)

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._capture_tag:
            self._capture_parts.append(data)

    @classmethod
    def _context_entry(cls, tag: str, attributes: dict[str, str]) -> dict[str, Any]:
        html_id = attributes.get("id", "").strip().lower()
        html_class = attributes.get("class", "").strip().lower()
        itemprop = attributes.get("itemprop", "").strip().lower()
        label = " ".join(value for value in (html_id, html_class, itemprop) if value)
        body_hint = any(hint in label for hint in cls.ARTICLE_BODY_HINTS)
        if re.fullmatch(r"e\d+", html_id) and "content" in re.split(r"\s+", html_class):
            body_hint = True
        if itemprop == "articlebody":
            body_hint = True
        context_tokens = [
            token.replace("_", "-")
            for token in re.split(r"\s+", label)
            if token
        ]
        excluded = tag not in {"html", "body"} and any(
            token == hint
            or token.startswith(f"{hint}-")
            or token.endswith(f"-{hint}")
            for token in context_tokens
            for hint in cls.EXCLUDED_MEDIA_HINTS
        )
        return {
            "tag": tag,
            "label": _trim_text(label, 240),
            "article_body": body_hint,
            "excluded": excluded,
        }

    def _media_context(self) -> dict[str, Any]:
        in_article_body = any(bool(item["article_body"]) for item in self._context_stack)
        excluded = any(bool(item["excluded"]) for item in self._context_stack)
        context = " > ".join(
            f"{item['tag']}#{item['label']}" if item["label"] else str(item["tag"])
            for item in self._context_stack[-8:]
        )
        return {
            "inside_article": bool(in_article_body and not excluded),
            "source_context": _trim_text(context, 600),
        }

    def _pop_context(self, tag: str) -> None:
        for index in range(len(self._context_stack) - 1, -1, -1):
            if self._context_stack[index]["tag"] == tag:
                del self._context_stack[index:]
                return


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
    if item.get("inside_article"):
        score += 180
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
    seen: dict[str, dict[str, Any]] = {}
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
            existing = seen[absolute]
            if item.get("inside_article"):
                existing["inside_article"] = True
                existing["source_context"] = item.get("source_context", "")
            continue
        normalized = {**item, "url": absolute}
        seen[absolute] = normalized
        unique.append(normalized)
    unique.sort(key=_source_image_candidate_score, reverse=True)
    article_images = [item for item in unique if item.get("inside_article")]
    if article_images:
        article_urls = {str(item.get("url") or "") for item in article_images}
        metadata_images = [
            item for item in unique
            if item.get("source_hint") == "metadata" and str(item.get("url") or "") not in article_urls
        ]
        unique = article_images + metadata_images
    elif any(item.get("inside_article") for item in parser.videos):
        # Video-led articles often have no body <img>. In that case the page
        # thumbnail is useful, but filling the gap with sidebar images wastes
        # vision budget and can make an unrelated thumbnail look like content.
        unique = [item for item in unique if item.get("source_hint") == "metadata"]
    return unique


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
            "inside_article": bool(item.get("inside_article")),
            "source_context": _trim_text(str(item.get("source_context") or ""), 600),
        })
    article_videos = [item for item in candidates if item.get("inside_article")]
    if article_videos:
        candidates = article_videos
        for index, item in enumerate(candidates, start=1):
            item["id"] = f"video-{index}"
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
    source_text_items = parser.article_text_items or parser.text_items
    for tag, text_value in source_text_items:
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
            "inside_article": bool(candidate.get("inside_article")),
            "source_context": _trim_text(str(candidate.get("source_context") or ""), 600),
            "source_hint": _trim_text(str(candidate.get("source_hint") or ""), 80),
            "source_score": _source_image_candidate_score(candidate),
        })
        if len(downloaded_images) >= MAX_SOURCE_IMAGES:
            break

    videos = _candidate_videos(parser, final_url)

    result = {
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
    result["affiliate_opportunities"] = detect_affiliate_opportunities({
        **result,
        "affiliate_resources": parser.affiliate_resources,
    })
    return result


def _source_identity_url(source: dict[str, Any]) -> str:
    requested = str(source.get("requested_url") or "").strip()
    return requested if requested.startswith(("http://", "https://")) else str(source["url"])


def _source_slug(source: dict[str, Any]) -> str:
    if source["source_type"] == "x_post":
        username = str(source["x_info"]["username"]).lower().replace("_", "-")
        return f"x-{username}-{str(source['x_info']['post_id'])[-8:]}"
    if source["source_type"] == "x_profile":
        username = str(source["x_info"]["username"]).lower().replace("_", "-")
        return f"x-{username}-profile"
    identity_url = _source_identity_url(source)
    host = (urlparse(identity_url).hostname or "page").removeprefix("www.")
    host_slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:36] or "page"
    digest = hashlib.sha256(identity_url.encode("utf-8")).hexdigest()[:8]
    return f"url-{host_slug}-{digest}"


def _response_blocks(source: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(source["title"])
    description = _trim_text(str(source.get("description") or ""), 300)
    excerpts = [str(value) for value in (source.get("excerpts") or []) if isinstance(value, str)]
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
    available = {item["id"]: item for item in (source.get("images") or []) if isinstance(item, dict)}
    if not isinstance(selected_image_ids, list):
        raise ValidationError("画像の選択内容が不正です")
    if len(selected_image_ids) != len(set(selected_image_ids)) or any(item not in available for item in selected_image_ids):
        raise ValidationError("選択した画像が無効です")
    available_videos = {item["id"]: item for item in (source.get("videos") or []) if isinstance(item, dict)}
    selected_video_ids = [] if selected_video_ids is None else selected_video_ids
    if not isinstance(selected_video_ids, list):
        raise ValidationError("動画の選択内容が不正です")
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
            "source_id": image_id,
            "name": f"source-{index}{item['extension']}",
            "data_url": f"data:{item['mime_type']};base64,{base64.b64encode(item['data']).decode('ascii')}",
            "alt": str(item.get("alt") or source["title"])[:180],
            "orientation": item.get("orientation", "landscape"),
            "source_url": str(item.get("rights_source_url") or item.get("url") or "")[:2048],
            "rights_basis": str(item.get("rights_basis") or "")[:80],
            "embedded_status_url": str(item.get("embedded_status_url") or "")[:2048],
            "owner_name": str(item.get("owner_name") or "")[:120],
            "owner_profile_url": str(item.get("owner_profile_url") or "")[:2048],
            "ai_content_group": str(item.get("ai_content_group") or "")[:120],
            "ai_role": str(item.get("ai_role") or "")[:80],
            "ai_reason": str(item.get("ai_reason") or "")[:500],
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
            "source_id": video_id,
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
            "rights_basis": str(item.get("rights_basis") or "")[:80],
            "rights_source_url": str(item.get("rights_source_url") or item.get("url") or "")[:2048],
            "width": _safe_int(item.get("width")),
            "height": _safe_int(item.get("height")),
        })

    responses = _response_blocks(source)
    first_image_id = (
        payload_ids_by_source.get(thumbnail_image_id or "")
        or images[0]["id"]
    )
    thumbnail_only = bool(thumbnail_image_id) and thumbnail_image_id not in selected_image_ids
    x_embed = source.get("x_embed") if isinstance(source.get("x_embed"), dict) else None
    media_blocks: list[dict[str, Any]] = []
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
    else:
        media_blocks.append({
            "id": "source-lead-image",
            "type": "images",
            "image_ids": [first_image_id],
            "lead": True,
        })

    # FANZA product articles use a fixed material order at the article head:
    # package thumbnail, official sample video, then official image gallery.
    fanza_product = (
        source.get("source_type") == "fanza_product"
        or str(source.get("media_rights_profile") or "").startswith("fanza_")
    )
    if videos:
        media_blocks.append({
            "id": "source-videos-1",
            "type": "videos",
            "video_ids": [video["id"] for video in videos],
            "lead": True,
        })

    media_image_ids = {
        image_id
        for block in media_blocks
        for image_id in block.get("image_ids", [])
    }
    remaining_image_ids = [
        image_id for image_id in body_image_ids
        if image_id not in media_image_ids
    ]
    if fanza_product and remaining_image_ids:
        # FANZA product pages often have more than four official sample
        # images. The saved article schema permits four per gallery block.
        for offset in range(0, len(remaining_image_ids), 4):
            media_blocks.append({
                "id": f"source-fanza-gallery-{offset // 4 + 1}",
                "type": "images",
                "image_ids": remaining_image_ids[offset:offset + 4],
                "lead": True,
            })
        remaining_image_ids = []
    for offset in range(0, len(remaining_image_ids), 2):
        media_blocks.append({
            "id": f"source-images-{offset + 2}",
            "type": "images",
            "image_ids": remaining_image_ids[offset:offset + 2],
        })
    lead_blocks = [block for block in media_blocks if block.get("lead")]
    media_blocks = [block for block in media_blocks if not block.get("lead")]
    blocks: list[dict[str, Any]] = [*lead_blocks]
    response_index = 0
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
    identity_url = _source_identity_url(source)
    identity_host = (urlparse(identity_url).hostname or "").removeprefix("www.")
    is_gateway_source = identity_url != str(source["url"])
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
        "source_url": identity_url,
        "source_label": identity_host if is_gateway_source and identity_host else str(source["site_name"]),
        "resolved_source_url": str(source["url"]),
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
    for raw in (source.get("text_blocks") or source.get("excerpts") or []):
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
    *,
    nested_article: bool = False,
) -> str:
    requested_count = options.get("reply_count", "auto")
    reply_count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else 8
    requested_category = str(options.get("category") or "auto")
    output_instruction = (
        "最終JSONのarticleオブジェクトに必要なtitle、summary、category、tags、responsesを"
        "入れ、Markdownや講評は付けない。"
        if nested_article
        else "JSONスキーマに必要なtitle、summary、category、tags、responsesを返し、"
        "Markdownや講評は付けない。"
    )
    generation_image_ids = {
        str(image_id)
        for image_id in options.get("generation_image_ids", options.get("selected_image_ids", []))
        if isinstance(image_id, str)
    }
    editorial_intent = source.get("editorial_intent", {})
    fanza_product_mode = (
        isinstance(editorial_intent, dict)
        and editorial_intent.get("content_mode") == "fanza_product"
    )
    source_facts = {
        "source_type": source.get("source_type"),
        "url": source.get("url"),
        "site_name": source.get("site_name"),
        "author": source.get("author"),
        "title": source.get("title"),
        "description": "" if fanza_product_mode else source.get("description"),
        "excerpts": [] if fanza_product_mode else source.get("excerpts", [])[:8],
        "x_post_text": (source.get("x_embed") or {}).get("text") if isinstance(source.get("x_embed"), dict) else "",
        "editorial_intent": editorial_intent,
        "fanza_rights_mode": (
            "official product page, exact package image, and exact official product-introduction images only"
            if fanza_product_mode else ""
        ),
        "nearby_real_headlines_for_style_comparison": _source_headline_samples(source),
        "selected_image_context": [
            {
                "image_id": item.get("id"),
                "page_role": item.get("ai_role"),
                "relation_to_other_media": item.get("ai_relation"),
                "analysis_reason": item.get("ai_reason"),
            }
            for item in (source.get("images") or [])
            if isinstance(item, dict) and str(item.get("id")) in generation_image_ids
        ],
    }
    attachment_numbers: dict[str, int] = {}
    image_manifest = []
    for item in attachments or []:
        filename = str(item["filename"])
        if filename not in attachment_numbers:
            attachment_numbers[filename] = len(attachment_numbers) + 1
        image_manifest.append({
            "attachment_number": attachment_numbers[filename],
            "image_id": item["id"],
            "filename": filename,
            "contact_sheet_cell": item.get("contact_sheet_cell"),
            "page_alt": item.get("alt", ""),
            "codex_analysis": item.get("ai_reason", ""),
        })
    selected_video_ids = {
        str(video_id)
        for video_id in options.get("generation_video_ids", options.get("selected_video_ids", []))
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
        for item in (source.get("videos") or [])
        if isinstance(item, dict) and str(item.get("id")) in selected_video_ids
    ]
    body_image_count = len(options.get("selected_image_ids", []))
    selected_media_count = body_image_count + len(options.get("selected_video_ids", []))
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
- image_idsは、そのレスが具体的に扱っている添付画像のIDだけを入れる。画像を見ずに順番を推測して割り当てず、レスの内容と画像が一致しないなら空配列にする。同じ画像を複数レスへ重複割り当てしない。
- video_idsはそのレスで投稿される動画を表す。動画を付けたレスは感想ではなく投稿側の発言として自然にし、動画の分け方は会話の流れから判断する。配置の帳尻より文章の自然さを優先してよい。
- 記事の中心は採用された画像・動画の中身である。元サイトの広告量、関連記事、サイドバー、運営姿勢、素材選別の是非を話題にしない。元ページ自体がそれを論じる記事の場合だけ例外とする。
- 見た目から年齢を推測しない。元資料に年齢の明記がないなら、若く見える、年齢不明、成人確認などをタイトルやレスの話題にせず、確認できる視覚内容だけを扱う。

編集上の境界:
- 元ページの文章を長くコピーしない。画像や本文にない特徴、本人の感情や経歴、個人情報、犯罪事実を作らない。
- 元ページの見出し、導入、コメント、並び順を言い換えただけで再現しない。採用素材を自分で見たうえで、独自のタイトル、短い要約、レス同士の会話、素材の再配置として組み直す。媒体名と元URLは別途表示されるため、出典の事実は隠さない。
- summaryと各レスには、元ページの本文、見出し、コメントから連続24文字以上をそのまま移さない。固有名詞や短い事実表現を除き、元文の語順を保った言い換えも避け、添付素材を見た別人の反応として一から書く。
- 出力直前にsummaryと全レスを元ページの文章と照合し、一致する長い一節が一つでもあれば、その一節を削るだけで済ませずレス全体を独自の言葉へ書き直す。
- 誹謗中傷や差別語へ頼って刺激を作らない。成人向けの俗語や卑猥な語は、成人素材として確認でき、題材と話者に合う場合は遠慮せず使える。
- categoryの希望がauto以外なら原則として従う。
- {output_instruction}

X記事の目的:
- editorial_intent.content_modeがx_accountなら、単発投稿の煽り記事ではなく、そのアカウントを読者へおすすめする紹介記事にする。プロフィール、公開投稿、添付画像、紹介ポイントから「どんな投稿が見られるか」「何が魅力か」が伝わるタイトルとレスにし、本文の公式タイムラインへ自然につなげる。
- x_accountでも記事形式は最初から最後まで5ch風を守る。運営者が説明する紹介文、商品カタログ、取材記事、プレスリリースの口調にはしない。スレ主がアカウントや投稿を貼り、住民が画像、衣装、表情、撮り方、投稿内容など実際に確認できる部分へ自然に反応することで、結果として本人の良さが伝わる構成にする。
- おすすめ記事は好意的なスレにするが、全員に宣伝係のような絶賛をさせない。「この衣装ええな」「こういう表情好き」「この写真かなり強い」など、その場で素材を見た人が口にする具体的で短い反応を中心にし、好みの違い、驚き、軽いツッコミも混ぜる。同じ褒め言葉や語尾を反復しない。
- 「おすすめです」「魅力的です」「要チェックです」「フォローしたい」「フォローして損はない」「推せる」「今後に期待」のような広告文、勧誘、締めの定型句を使わない。フォロー、購入、登録、拡散などの行動を読者へ促さない。実際の5chレスとして自然な語彙と不揃いさを保つ。
- 素材から読み取れない内面、努力、人柄、ファン対応などを褒めるために作らない。見えている具体的な良さを話題にする。
- x_accountではアカウント名または@usernameが分かるタイトルにする。確認できない本名、経歴、人気度、フォロワー数、実績、投稿頻度、性格、依頼関係は作らない。
- editorial_intent.content_modeがx_postなら、指定された投稿の内容と添付素材を中心にする。アカウント全体を勝手に評価せず、その投稿の見どころと反応で記事を組む。
- editorial_intent.content_modeがfanza_productなら、作品名、出演者、メーカー、品番、見どころなど元ページで確認できる作品情報を軸にする。単なる広告文や商品カタログにはせず、作品の具体的な場面や特徴に住民が自然に反応する5ch風記事にする。購入を強要する文、効果保証、未確認の内容は作らない。FANZAへの購入ボタンとPR表示はアプリ側で付ける。
- fanza_productでは、権利確認済みの商品パッケージと、同じ商品IDのFANZA公式商品紹介画像が添付される。パッケージだけで記事を組まず、紹介画像で実際に確認できる衣装、場所、構図、登場人数、場面の違いを具体的に拾い、読者が作品内容を判断できるようにする。画像から確認できない行為の順番、出演者の感情、視聴体験は作らない。
- fanza_productのレスで個別画像に言及するときは、必ず対応するimage_idsを付ける。パッケージの文字やレイアウトへの反応を商品紹介画像へ割り当てたり、別場面の説明を直後へ置いたりしない。
- FANZAの商品紹介文、ユーザーレビュー、レビュー点数を引用・要約・言い換え再利用しない。「見た」「抜いた」「本編では」など実際に視聴したと誤認させる体験談も書かない。
- fanza_productでも記事形式は5ch風を保つ。スレ主が作品ページとパッケージを貼り、住民が作品名、パッケージ、確認できる出演者・メーカー・ジャンルへ不揃いに反応する。全員を購入へ誘導する宣伝係にしない。
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
        "canonical_product_url": source.get("canonical_product_url"),
        "fanza_product_id": source.get("fanza_product_id"),
        "page_title": source.get("title"),
        "description": source.get("description"),
        "excerpts": source.get("excerpts", [])[:5],
        "nearby_real_headlines_for_style_comparison": _source_headline_samples(source),
        "selected_video_ids": selected_video_ids,
        "selected_official_image_urls": [
            str(item.get("rights_source_url") or item.get("url") or "")
            for item in (source.get("images") or [])
            if isinstance(item, dict)
        ],
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


def _normalized_article_overlap_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _codex_article_overlap_chunks(
    source: dict[str, Any],
    article: dict[str, Any],
    chunk_size: int = 72,
) -> list[str]:
    """Return long copied passages using the same threshold as publish policy."""
    authored = _normalized_article_overlap_text("\n".join([
        str(article.get("summary") or ""),
        *[
            str(item.get("text") or "")
            for item in (article.get("responses") or [])
            if isinstance(item, dict)
        ],
    ]))
    source_text = _normalized_article_overlap_text("\n".join([
        str(source.get("_copyright_reference_text") or ""),
        str(source.get("body_text") or ""),
        *[str(item) for item in (source.get("text_blocks") or [])],
        *[str(item) for item in (source.get("excerpts") or [])],
        str(source.get("description") or ""),
    ]))
    if not authored or not source_text or len(authored) < chunk_size:
        return []
    matches: list[str] = []
    for start in range(0, len(authored) - chunk_size + 1, 12):
        chunk = authored[start:start + chunk_size]
        if chunk not in source_text:
            continue
        if not any(chunk in existing or existing in chunk for existing in matches):
            matches.append(chunk)
        if len(matches) >= 6:
            break
    return matches


def _codex_originality_repair_prompt(
    source: dict[str, Any],
    options: dict[str, Any],
    draft: dict[str, Any],
    overlaps: list[str],
) -> str:
    requested_count = options.get("reply_count", "auto")
    reply_count = (
        int(requested_count)
        if str(requested_count) in {"5", "8", "10"}
        else len(draft.get("responses") or [])
    )
    return f"""あなたは成人向け匿名掲示板まとめ記事の最終編集者です。
下書きが、参照ページの文章を長く再利用しているため独自性検査で不合格になりました。
画像・動画の判定は完了済みなので、事実関係と素材IDを保ちつつ、文章だけを一から書き直してください。

必須:
- title、summary、各responsesのtextを、別の編集者と別々の掲示板利用者が素材を見て書いた自然な日本語へ再構成する。
- 下記の一致箇所と同じ語順を使わず、元ページの導入、コメント、見出しを要約・逐語言い換えしない。
- summaryとレスに、参照文から連続24文字以上を再利用しない。
- 画像・動画から確認できない人物情報、経歴、感情、出来事を追加しない。
- responsesは{reply_count}本を維持し、既存のimage_idsとvideo_idsだけを使う。存在しないIDを作らない。
- 指定スキーマのJSONだけを返し、説明やMarkdownを付けない。

記事の題材: {json.dumps(str(source.get("title") or ""), ensure_ascii=False)}
再利用禁止になった一致箇所:
{json.dumps(overlaps, ensure_ascii=False, indent=2)}

書き直す下書き:
{json.dumps(draft, ensure_ascii=False, indent=2)}
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
            str(item)[:350] for item in (source.get("text_blocks") or [])[:block_limit]
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
    raw_links = [item for item in (source.get("links") or []) if isinstance(item, dict)]
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
            "thumbnail_only_candidate": bool(item.get("thumbnail_only_candidate")),
        }
        for item in (source.get("images") or []) if isinstance(item, dict)
    ]
    attachment_numbers: dict[str, int] = {}
    evidence = []
    for item in attachments:
        filename = str(item.get("filename") or "")
        if filename not in attachment_numbers:
            attachment_numbers[filename] = len(attachment_numbers) + 1
        evidence.append({
            "attachment_number": attachment_numbers[filename],
            "id": item.get("id"),
            "filename": filename,
            "kind": item.get("kind", "candidate"),
            "contact_sheet_cell": item.get("contact_sheet_cell"),
        })
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
        for item in (source.get("videos") or [])
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
- 淫談屋は成人向け専用サイトであり、一般コンテンツを混在させない。ページ本編が成人向けかを、文章、画像、動画、商品区分、ページの主目的を総合して最初に判定する。
- 性的な行為・露出・裸体・成人向け作品・成人向け配信・成人向けグラビア・明確に性的鑑賞を主目的とする画像や動画はadult_content=trueにできる。
- 一般ニュース、通常の芸能情報、スポーツ、一般YouTube、商品機能が中心の水着レビュー、模型・玩具、一般アニメ、普通のSNS近況などは、女性や水着が写るだけでは成人向けにしない。一方、成人モデル・グラビア・コスプレイヤーの身体や性的魅力を見せることが本編の主目的で、露出度、ポーズ、画像構成、本文からそれが具体的に確認できる水着・下着・グラビア・コスプレはadult_content=trueにできる。
- 判断が割れる場合はページ全体の主目的を見る。成人向け要素が本編ではなく広告や関連記事にしかない場合はadult_content=falseにする。adult_reasonには本編のどの要素から判断したかを書く。
- 衣装名や刺激的な見出しだけで決めず、本編画像・動画の構図、露出、ポーズ、ページの主目的を確認する。裸体や性行為がなくても、成人モデルの身体的・性的魅力を鑑賞させるグラビアが本編の中心なら対象にする。普通のファッション、競技水着、日常写真は対象外にする。
- 一般コンテンツと成人向けコンテンツが同じページにある場合、成人向け部分が記事本編の明確な主目的でなければadult_content=falseにする。迷う場合、素材不足の場合、広告だけが成人向けの場合もfalseにする。
- 未成年を示す明記、盗撮、無断撮影、実在人物の流出をうたう素材など、審査上の危険性が高い題材は記事化しない。フィクションや成人作品だと元ページで明確に確認できない限りadult_content=falseにする。
- ページの本編素材が何を扱い、何を見せる記事かを自然な日本語のtitleとdescriptionにまとめる。descriptionには広告、関連記事、UIの説明を混ぜず、それらの判別結果はanalysis_summaryだけに書く。
- 各画像がそのページ内で実際に何をしているかを、ページ固有の言葉で把握する。その理解をもとにサムネイル・本文・両方・除外のどこで使うか決める。
- ページから回収した動画・埋め込み候補を、記事本編か広告・導線・無関係か判定する。
- 記事画像だけを後工程の初期選択候補にするため、厳しめに分類する。

記事の主役判定:
- main_subjectには、広告や関連記事ではなく記事本編が中心に扱う人物・団体・作品・商品・話題を1件だけ入れる。
- 実名・活動名が本文、見出し、投稿者名、画像キャプションなどで確認できる場合だけnameへ入れ、不明なら空文字にする。顔や外見から名前を推測しない。
- kindはperson/group/work/product/topic/unknownから選ぶ。roleには「コスプレイヤー」「TikToker」「AV女優」「漫画家」など、ページ上で確認できる立場だけを書く。
- is_public_creatorは、本人が公開アカウントや公式ページを持つ活動者として確認できる人物・団体だけtrueにする。匿名の素人、作品内だけの架空人物、名前不明の人物はfalseにする。
- reasonには、主役名・種別・立場を判断したページ上の根拠を書く。主役が複数で1人に絞れない場合はkind=groupとし、確認できる団体名がなければnameを空文字にする。

作品・商品の公式ページ確認:
- main_subject.kindがworkまたはproductで、作品名・商品名を確認できた場合だけofficial_workを調査する。
- 画面内リンクに作品そのものの公式ページがあればそれを優先する。見当たらない場合は同じ1回の処理内でWeb検索を使い、出版社・制作者の公式作品ページ、次に作品名が完全一致する正規販売ページを探す。
- status=verifiedにできるのは、ページタイトル・作品名・商品名がmain_subject.nameと一致し、そのURLがその作品単体の詳細ページだと確認できた場合だけである。
- 出版社や販売サイトのトップ、検索結果、ランキング、カテゴリ、タグ、関連記事、まとめ記事、紹介ブログは公式作品ページとして返さない。
- 作品名が似ているだけ、版やシリーズを区別できない、同名作品が複数ある、検索結果の抜粋しか確認できない場合はambiguousにし、url、provider、thumbnail_urlを空文字にする。
- 見つからなければnot_found、主役が人物・話題など作品や商品でなければnot_applicableにする。verified以外ではurlを必ず空文字にする。
- reasonには、どの表示から作品名とURLが一致したと確認したかを書く。thumbnail_urlは同じ公式作品ページのOGPまたは公式パッケージ画像を直接確認できた場合だけ入れ、記事画像や別作品画像を代用しない。

本人アカウント判定:
- 記事の中心人物がインフルエンサー、TikToker、YouTuber、配信者、コスプレイヤー、モデル、グラビアアイドルなどで、ページ情報またはリンク一覧に本人のSNSだと確認できるURLがある場合、social_profilesへ入れる。
- nameはページ本文・投稿者名・見出し・リンク周辺文で確認できた人物名、serviceはx/tiktok/instagram/youtube/myfans/fantia、urlは提示された現在URLまたはリンク一覧に実在するURLを一字も変えずに返す。
- 記事の中心人物に対応するものだけis_main_subject=trueにする。複数人物の記事では、各人物との対応がページ上で確認できる公式アカウントもis_main_subject=falseで入れる。アカウントと人物の対応が不明なら入れない。
- X/Twitterのintent、share、search、home、compose、ログイン、共有ボタン、サイト運営者のSNS、記事を共有するためのリンクは本人アカウントではない。TikTokやInstagramも共有・検索・ログイン導線を除外する。
- 名前からユーザー名を推測したり、画像の顔だけで人物を特定したり、候補一覧にないURLを作ったりしない。確認できなければ空配列を返す。

人物と画像の対応判定:
- identified_peopleには、見出し、画像直前の説明、alt/caption、リンク文、作品の出演者表記、公式ページのうち独立した2種類以上の根拠が同じ名前を示す公開活動者だけを入れる。
- confidenceは表示名の正確さを表す。95未満の人物はidentified_peopleにもmedia_person_attributionsにも入れない。人数を埋めるために推測せず、分からない人物は未特定のままにする。
- evidence_typesは実際に使った根拠だけをheadline/caption/alt/link_text/official_profile/official_page/product_credit/source_metadataから2種類以上選ぶ。似た顔、体型、衣装、雰囲気は根拠に含めない。
- media_person_attributionsには、どの画像・動画に誰が写るかを対応付ける。image_idsとvideo_idsは候補一覧のIDを一字も変えずに使い、少なくともどちらか一方を入れる。
- 1人を特集する記事でも、全画像を自動的に同一人物とみなさない。記事見出しと各画像のalt/captionまたは直前の説明が同じ人物名を示す画像だけ対応付ける。
- 各画像・動画の画面内に表示される投稿者名、チャンネル名、透かし、@ハンドルを必ず読む。確認できた文字列はimage_decisionsまたはvideo_decisionsのvisible_creator_handleへそのまま入れ、主役または根拠付きの別人物と一致するかをsubject_matchで判定する。
- visible_creator_handleが主役の確認済み公式ハンドルと異なり、その別ハンドルの人物が本編の共演者・別の特集対象だとページ上で確認できない場合はsubject_match=mismatch、verdict=unrelated、recommended_use=excludeにする。その素材をtitle、description、レスの根拠に使わない。
- 記事タイトルが主役名を示していても、関連記事・おすすめ欄・次の記事から混入した別ハンドルの素材を主役本人だと扱わない。ハンドル不一致は、顔や衣装が似ているという理由では覆せない。
- 複数人物のまとめでは、画像ごとの隣接説明や作品出演者表記がない画像を顔だけで振り分けない。1枚に複数人が写り、全員を根拠付きで確認できる場合は同じimage_idを複数人物へ割り当ててよい。
- グラビアアイドル、コスプレイヤー、配信者をAV女優と推定しない。FANZA出演は作品ページまたは出演者表記で別に確認する。
- 名前が書かれていない画像や動画も人物調査の対象から外さない。画像内の透かし・ロゴ・文字、ファイル名、リンク先、元ページから本編までの遷移履歴、動画の代表フレーム、同じ画像を掲載するWebページを検索し、候補名を調べる。
- 無記名素材で95以上まで裏取りできなかった場合は、person_identity_candidatesへ素材ごとに最大3人を確率順で返す。候補がなければcandidatesを空配列にしてunresolved_reasonへ不足した根拠を書く。
- person_identity_candidatesのconfidenceは推測の強さではなく、素材とその候補が同一人物である確からしさを1～94で付ける。顔や体型が似るだけなら40以下、透かしや画像検索結果が一致しても公式情報まで結べなければ94以下にする。
- evidence_urlsには実際に調査したページだけを入れ、URLやアカウントを作らない。検索結果の一覧URLではなく、候補名と素材の関係を確認した掲載ページを優先する。
- 95以上の確定人物は従来どおりidentified_peopleとmedia_person_attributionsへ入れ、person_identity_candidatesで確定扱いにしない。

FANZA関連判定:
- 記事の主題、人物名、作品名、品番、衣装、行為、ジャンル、動画周辺文から、FANZA作品への関連度を判定する。
- ページにFANZA/DMMの商品詳細URL、作品品番、出演者としての明示など、記事内容と作品を直接結び付ける根拠がある場合だけFANZA関連として扱う。単にページ内にFANZAの広告、バナー、クーポン、汎用リンクがあるだけでは関連ありにしない。
- YouTuber、配信者、コスプレイヤー、モデル、アイドルなどの人物名を、成人向けの画像や話題があるという理由だけでFANZA出演者だと推測しない。FANZA上の出演作品または元ページ内の明確な出演根拠を確認できなければ、fanza_relevanceはnone、fanza_peopleは空配列にする。
- 特定の商品URLまたは品番が確認できる場合だけexact_product、ページ本文・画像周辺文・投稿者情報などから出演者名が確認でき、記事素材との対応も強い場合はlikely_productにする。ジャンルや体型だけしか分からない場合はrelatedにできるが、検索語とPRは作らない。成人向け商品と結びつかない場合はnoneにする。
- 各画像について、同一人物の連続カットか、別人が混ざるか、名前を示す見出し・キャプション・リンク文が近くにあるかを確認する。記事の中心人物をページ上の根拠から特定できた場合だけfanza_performer_nameへ正式な出演者名を入れる。複数人で誰の画像か対応できない場合は空文字にする。
- fanza_search_queryには、確認できた出演者名、作品名、品番のいずれかを使った実際に検索可能な短い語句だけを入れる。「Gカップ 爆乳 AV女優」「制服 巨乳」など体型・衣装・ジャンルを並べただけの語句は禁止し、特定情報がなければ空文字にする。
- 人物の顔だけから本名や出演作品を推測しない。ページ本文、画像のalt・キャプション、投稿本文、作品情報などで名前が確認できる場合だけ使う。
- fanza_peopleには、ページ上の根拠からFANZA出演者であることと名前を確認でき、どの画像がその人物かまで対応できた人物だけを入れる。nameは正式な出演者名、image_idsはその人物が写る画像ID、reasonはFANZA出演の根拠と名前・画像を対応付けた根拠にする。同じ人物の画像は1件へまとめ、複数人の記事では人物ごとに分ける。
- 名前だけ確認できても画像との対応が不明ならfanza_peopleへ入れない。顔だけの照合、体型、衣装、雰囲気から人物名を推測しない。対応できた人物がいなければ空配列を返す。
- fanza_image_productsは名前にimageとあるが、画像と動画の両方について、その素材自体が特定のAV作品の場面・パッケージ・公式サンプルだとページ上の根拠から確認できる場合だけ返す。作品ごとにproduct_title、product_code、product_url、対応する全image_ids、全video_ids、対応根拠をまとめる。同じ作品を複数項目へ分けない。
- 作品Aの画像・動画と作品Bの画像・動画が混在する場合は、人物や雰囲気で一括りにせず作品ごとに分ける。素人投稿、X投稿、一般コスプレ、出典不明素材は、見た目がAV風でもfanza_image_productsへ入れない。
- product_urlは画面内リンク候補または現在ページURLに存在する、その作品の商品詳細URLだけを一字も変えずに使う。商品URLがなくても品番が本文や周辺文で確認できるならproduct_codeへ入れられる。URLも品番も確認できない作品は登録しない。
- fanza_image_productsに入れた素材へ別の関連作品を割り当てない。後工程は、その作品に対応する画像または動画の直後へ同じ作品のPRを置く。対応しない素材の近くへ置かない。
- fanza_product_codeはページ内で確認できた場合だけ返す。fanza_reasonには判定根拠を簡潔に書く。
- 特定作品へ結び付かない場合はFANZA商品を推測して薦めない。サイト内の関連記事を後工程で選ぶため、fanza_recommendation_queriesは常に空配列にする。

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
- visible_creator_handleは画像内で実際に読めた投稿者名・@ハンドルだけを入れ、見えなければ空文字にする。subject_matchは主役または根拠付きの特集人物と一致すればmatched、明確に別人ならmismatch、判断不能ならunknownにする。
- 動画はタグ種別だけで決めない。direct動画でも広告の場合があり、iframeでも記事本編の場合がある。URLのドメイン、パス、HTMLのclass/id/title、ページ本文との一致から判断する。
- videoタグ内のMP4、投稿本文と同じ場所にあるプレイヤー、記事タイトルと一致する動画はarticle候補。ライブチャット広告、ランキング、ブログパーツ、別サイト誘導はadvertisementかnavigationにする。
- 動画一覧にある全候補についてvideo_decisionsを1件ずつ返し、video_idを一字も変えない。
- 動画のポスター、代表フレーム、プレイヤー内に投稿者名・@ハンドル・作品名が見える場合もvisible_creator_handleとsubject_matchを同じ基準で返す。
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
        for item in (source.get("links") or [])
        if isinstance(item, dict) and item.get("url")
    }
    if page_role != "gateway":
        follow_url = ""
    elif follow_url not in available_links:
        page_role = "unclear"
        follow_url = ""
        follow_reason = "候補一覧にないリンクが返されたため追跡を中止しました"
    summary = _require_text(value, "analysis_summary", 500)
    adult_content = value.get("adult_content") is True
    adult_reason = _require_text(value, "adult_reason", 300)
    raw_main_subject = value.get("main_subject")
    if not isinstance(raw_main_subject, dict):
        raw_main_subject = {
            "name": "",
            "kind": "unknown",
            "role": "",
            "is_public_creator": False,
            "reason": "主役判定が返されませんでした",
        }
    main_subject_kind = str(raw_main_subject.get("kind") or "unknown")
    if main_subject_kind not in {"person", "group", "work", "product", "topic", "unknown"}:
        main_subject_kind = "unknown"
    main_subject = {
        "name": _trim_text(str(raw_main_subject.get("name") or ""), 80),
        "kind": main_subject_kind,
        "role": _trim_text(str(raw_main_subject.get("role") or ""), 80),
        "is_public_creator": raw_main_subject.get("is_public_creator") is True,
        "reason": _trim_text(
            str(raw_main_subject.get("reason") or "主役を特定できませんでした"),
            240,
        ),
    }
    if main_subject["kind"] not in {"person", "group"}:
        main_subject["is_public_creator"] = False

    raw_official_work = value.get("official_work")
    if not isinstance(raw_official_work, dict):
        raw_official_work = {
            "status": "not_applicable",
            "title": "",
            "url": "",
            "provider": "",
            "reason": "公式作品ページの判定が返されませんでした",
            "thumbnail_url": "",
        }
    official_status = str(raw_official_work.get("status") or "not_applicable")
    if official_status not in {"verified", "ambiguous", "not_found", "not_applicable"}:
        official_status = "not_found"
    official_work = {
        "status": official_status,
        "title": "",
        "url": "",
        "provider": "",
        "reason": _trim_text(
            str(raw_official_work.get("reason") or "公式作品ページを確認できませんでした"),
            300,
        ),
        "thumbnail_url": "",
    }
    named_work = (
        main_subject["kind"] in {"work", "product"}
        and bool(main_subject["name"])
    )
    if not named_work:
        official_work["status"] = "not_applicable"
    elif official_status == "verified":
        official_title = _trim_text(str(raw_official_work.get("title") or ""), 180)
        official_url = str(raw_official_work.get("url") or "").strip()
        official_provider = _trim_text(str(raw_official_work.get("provider") or ""), 80)
        blocked_hosts = {
            "google.com", "www.google.com", "bing.com", "www.bing.com",
            "search.yahoo.co.jp", "duckduckgo.com", "www.duckduckgo.com",
        }
        try:
            parsed_official = urlparse(_validate_source_url(official_url))
        except (TypeError, ValueError):
            parsed_official = urlparse("")
        official_host = (parsed_official.hostname or "").casefold()
        path_parts = {
            part.casefold() for part in parsed_official.path.split("/") if part
        }
        query_keys = {key.casefold() for key in parse_qs(parsed_official.query)}
        generic_route = bool(
            path_parts.intersection({
                "search", "ranking", "rank", "category", "categories",
                "tag", "tags", "author", "authors", "list", "results",
            })
            or query_keys.intersection({"q", "query", "keyword", "search", "searchstr"})
            or parsed_official.path in {"", "/"}
        )

        def work_key(raw: Any) -> str:
            return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", str(raw or "").casefold())

        subject_key = work_key(main_subject["name"])
        title_key = work_key(official_title)
        if subject_key and title_key and (subject_key in title_key or title_key in subject_key):
            title_matches = True
        else:
            subject_pairs = {
                subject_key[index:index + 2]
                for index in range(max(0, len(subject_key) - 1))
            }
            title_pairs = {
                title_key[index:index + 2]
                for index in range(max(0, len(title_key) - 1))
            }
            title_matches = len(subject_pairs.intersection(title_pairs)) >= min(
                6, max(3, len(subject_pairs) // 3)
            )
        if (
            parsed_official.scheme == "https"
            and official_host
            and official_host not in blocked_hosts
            and not generic_route
            and official_title
            and official_provider
            and official_work["reason"]
            and title_matches
        ):
            official_work.update({
                "title": official_title,
                "url": official_url,
                "provider": official_provider,
            })
            thumbnail_url = str(raw_official_work.get("thumbnail_url") or "").strip()
            if thumbnail_url:
                try:
                    parsed_thumbnail = urlparse(_validate_source_url(thumbnail_url))
                except (TypeError, ValueError):
                    parsed_thumbnail = urlparse("")
                if parsed_thumbnail.scheme == "https" and parsed_thumbnail.hostname:
                    official_work["thumbnail_url"] = thumbnail_url
        else:
            official_work["status"] = "ambiguous"
            official_work["reason"] = (
                "作品名が一致する単体の公式・正規販売ページとして検証できませんでした"
            )
    elif official_status == "not_applicable":
        official_work["status"] = "not_found"
    evidence_urls = {
        str(source.get(key) or "").strip()
        for key in (
            "requested_url",
            "url",
            "canonical_url",
            "profile_url",
            "author_url",
            "creator_url",
        )
        if str(source.get(key) or "").strip()
    } | available_links
    service_hosts = {
        "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
        "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com"},
        "instagram": {"instagram.com", "www.instagram.com"},
        "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
        "myfans": {"myfans.jp", "www.myfans.jp"},
        "fantia": {"fantia.jp", "www.fantia.jp"},
    }
    blocked_x_routes = {
        "compose", "explore", "home", "i", "intent", "login", "messages",
        "notifications", "search", "settings", "share", "signup",
    }
    allowed_identity_evidence = {
        "headline", "caption", "alt", "link_text", "official_profile",
        "official_page", "product_credit", "source_metadata",
    }
    identified_people: list[dict[str, Any]] = []
    identified_names: set[str] = set()
    raw_identified_people = value.get("identified_people", [])
    if not isinstance(raw_identified_people, list):
        raise ValidationError("Codexの人物特定結果が不正です")
    for item in raw_identified_people:
        if not isinstance(item, dict):
            continue
        name = _trim_text(str(item.get("name") or ""), 80)
        normalized_name = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold())
        try:
            confidence = max(0, min(100, int(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        evidence_types = list(dict.fromkeys(
            str(evidence or "").casefold()
            for evidence in item.get("evidence_types") or []
            if str(evidence or "").casefold() in allowed_identity_evidence
        ))
        reason = _trim_text(str(item.get("reason") or ""), 300)
        if (
            not name
            or not normalized_name
            or normalized_name in identified_names
            or confidence < 95
            or len(evidence_types) < 2
            or not reason
        ):
            continue
        identified_names.add(normalized_name)
        identified_people.append({
            "name": name,
            "role": _trim_text(str(item.get("role") or ""), 80),
            "is_public_creator": item.get("is_public_creator") is True,
            "confidence": confidence,
            "evidence_types": evidence_types,
            "reason": reason,
        })
    social_profiles: list[dict[str, Any]] = []
    seen_social: set[tuple[str, str]] = set()
    raw_social_profiles = value.get("social_profiles", [])
    if not isinstance(raw_social_profiles, list):
        raise ValidationError("Codexの本人SNS対応が不正です")
    for item in raw_social_profiles:
        if not isinstance(item, dict):
            continue
        name = _trim_text(str(item.get("name") or ""), 80)
        is_main_subject = item.get("is_main_subject") is True
        normalized_name = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold())
        service = str(item.get("service") or "").casefold()
        url = str(item.get("url") or "").strip()
        reason = _trim_text(str(item.get("reason") or ""), 240)
        if (
            not name
            or service not in service_hosts
            or url not in evidence_urls
            or not reason
            or (
                not is_main_subject
                and normalized_name not in identified_names
            )
        ):
            continue
        try:
            parsed_social = urlparse(_validate_source_url(url))
        except (TypeError, ValueError):
            continue
        if (parsed_social.hostname or "").casefold() not in service_hosts[service]:
            continue
        path_parts = [part for part in parsed_social.path.split("/") if part]
        if service == "x" and (
            not path_parts or path_parts[0].casefold() in blocked_x_routes
        ):
            continue
        key = (service, url)
        if key in seen_social:
            continue
        seen_social.add(key)
        profile = {
            "name": name,
            "service": service,
            "url": url,
            "is_main_subject": is_main_subject,
            "reason": reason,
        }
        thumbnail_url = str(item.get("thumbnail_url") or "").strip()
        if thumbnail_url:
            try:
                profile["thumbnail_url"] = _validate_source_url(thumbnail_url)
            except (TypeError, ValueError):
                pass
        social_profiles.append(profile)

    def normalized_visible_handle(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        explicit = re.search(r"@([A-Za-z0-9_.-]{2,})", raw)
        if explicit:
            raw = explicit.group(1)
        elif raw.startswith(("https://", "http://")):
            parsed = urlparse(raw)
            parts = [part for part in parsed.path.split("/") if part]
            raw = parts[0].lstrip("@") if parts else ""
        return re.sub(r"[^a-z0-9_.-]", "", raw.casefold().lstrip("@"))

    main_subject_handles = {
        normalized_visible_handle(str(profile.get("url") or ""))
        for profile in social_profiles
        if profile.get("is_main_subject") is True
    }
    main_subject_handles.discard("")
    has_multiple_identified_people = len(identified_people) > 1
    fanza_relevance = str(value.get("fanza_relevance") or "none")
    if fanza_relevance not in {"none", "related", "likely_product", "exact_product"}:
        fanza_relevance = "none"
    fanza_performer_name = _optional_text(value, "fanza_performer_name", 80)
    fanza_search_query = _optional_text(value, "fanza_search_query", 120)
    fanza_product_code = _optional_text(value, "fanza_product_code", 40)
    fanza_reason = _optional_text(value, "fanza_reason", 240)
    raw_recommendation_queries = value.get("fanza_recommendation_queries", [])
    if not isinstance(raw_recommendation_queries, list):
        raise ValidationError("CodexのFANZA推薦調査語が不正です")
    fanza_recommendation_queries = list(dict.fromkeys(
        _trim_text(str(query), 80)
        for query in raw_recommendation_queries
        if _trim_text(str(query), 80)
    ))[:4]
    if category not in {"SNS", "画像", "動画", "話題"}:
        raise ValidationError("Codexが未対応のカテゴリーを返しました")
    available = {
        str(item.get("id")): item
        for item in (source.get("images") or [])
        if isinstance(item, dict) and item.get("id")
    }
    fanza_people: list[dict[str, Any]] = []
    claimed_person_images: set[str] = set()
    seen_people: set[str] = set()
    raw_people = value.get("fanza_people", [])
    if not isinstance(raw_people, list):
        raise ValidationError("CodexのFANZA人物対応が不正です")
    for item in raw_people:
        if not isinstance(item, dict):
            continue
        name = _trim_text(str(item.get("name") or ""), 80)
        reason = _trim_text(str(item.get("reason") or ""), 240)
        raw_image_ids = item.get("image_ids")
        if not name or not reason or not isinstance(raw_image_ids, list):
            continue
        image_ids = [
            str(image_id)
            for image_id in raw_image_ids
            if str(image_id) in available and str(image_id) not in claimed_person_images
        ]
        image_ids = list(dict.fromkeys(image_ids))
        normalized_name = name.casefold()
        if not image_ids or normalized_name in seen_people:
            continue
        seen_people.add(normalized_name)
        claimed_person_images.update(image_ids)
        fanza_people.append({
            "name": name,
            "image_ids": image_ids,
            "reason": reason,
        })
    available_videos = {
        str(item.get("id")): item
        for item in (source.get("videos") or [])
        if isinstance(item, dict) and item.get("id")
    }
    media_person_attributions: list[dict[str, Any]] = []
    raw_attributions = value.get("media_person_attributions", [])
    if not isinstance(raw_attributions, list):
        raise ValidationError("Codexの人物と素材の対応が不正です")
    for item in raw_attributions:
        if not isinstance(item, dict):
            continue
        person_name = _trim_text(str(item.get("person_name") or ""), 80)
        person_key = re.sub(
            r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", person_name.casefold()
        )
        try:
            confidence = max(0, min(100, int(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        evidence_types = list(dict.fromkeys(
            str(evidence or "").casefold()
            for evidence in item.get("evidence_types") or []
            if str(evidence or "").casefold() in allowed_identity_evidence
        ))
        image_ids = list(dict.fromkeys(
            str(image_id)
            for image_id in item.get("image_ids") or []
            if str(image_id) in available
        ))
        video_ids_for_person = list(dict.fromkeys(
            str(video_id)
            for video_id in item.get("video_ids") or []
            if str(video_id) in available_videos
        ))
        reason = _trim_text(str(item.get("reason") or ""), 300)
        if (
            person_key not in identified_names
            or confidence < 95
            or len(evidence_types) < 2
            or not (image_ids or video_ids_for_person)
            or not reason
        ):
            continue
        media_person_attributions.append({
            "person_name": person_name,
            "image_ids": image_ids,
            "video_ids": video_ids_for_person,
            "confidence": confidence,
            "evidence_types": evidence_types,
            "reason": reason,
        })
    candidate_evidence = {
        "headline", "caption", "alt", "link_text", "source_metadata",
        "watermark_ocr", "filename_clue", "web_search_result",
        "reverse_image_result", "video_frame_match",
    }
    person_identity_candidates: list[dict[str, Any]] = []
    raw_candidate_groups = value.get("person_identity_candidates", [])
    if not isinstance(raw_candidate_groups, list):
        raise ValidationError("Codexの人物候補ランキングが不正です")
    seen_candidate_media: set[tuple[str, str]] = set()
    for group in raw_candidate_groups:
        if not isinstance(group, dict):
            continue
        media_type = str(group.get("media_type") or "").casefold()
        media_id = _trim_text(str(group.get("media_id") or ""), 40)
        if (
            media_type == "image" and media_id not in available
            or media_type == "video" and media_id not in available_videos
            or media_type not in {"image", "video"}
            or (media_type, media_id) in seen_candidate_media
        ):
            continue
        seen_candidate_media.add((media_type, media_id))
        candidates: list[dict[str, Any]] = []
        seen_candidate_names: set[str] = set()
        for raw_candidate in (group.get("candidates") or [])[:3]:
            if not isinstance(raw_candidate, dict):
                continue
            name = _trim_text(str(raw_candidate.get("name") or ""), 80)
            name_key = re.sub(
                r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold()
            )
            try:
                confidence = max(
                    1, min(94, int(raw_candidate.get("confidence") or 0))
                )
            except (TypeError, ValueError):
                confidence = 0
            evidence_types = list(dict.fromkeys(
                str(evidence or "").casefold()
                for evidence in raw_candidate.get("evidence_types") or []
                if str(evidence or "").casefold() in candidate_evidence
            ))
            reason = _trim_text(str(raw_candidate.get("reason") or ""), 300)
            evidence_urls: list[str] = []
            for raw_url in (raw_candidate.get("evidence_urls") or [])[:4]:
                try:
                    parsed_url = _validate_source_url(str(raw_url or ""))
                except (TypeError, ValueError):
                    continue
                if urlparse(parsed_url).scheme == "https":
                    evidence_urls.append(parsed_url)
            if (
                not name_key
                or name_key in seen_candidate_names
                or not confidence
                or not evidence_types
                or not reason
            ):
                continue
            seen_candidate_names.add(name_key)
            candidates.append({
                "name": name,
                "role": _trim_text(str(raw_candidate.get("role") or ""), 80),
                "confidence": confidence,
                "evidence_types": evidence_types,
                "evidence_urls": list(dict.fromkeys(evidence_urls)),
                "reason": reason,
            })
        candidates.sort(key=lambda item: int(item["confidence"]), reverse=True)
        unresolved_reason = _trim_text(
            str(group.get("unresolved_reason") or ""), 300
        )
        if candidates or unresolved_reason:
            person_identity_candidates.append({
                "media_type": media_type,
                "media_id": media_id,
                "candidates": candidates,
                "unresolved_reason": unresolved_reason,
            })
    fanza_image_products: list[dict[str, Any]] = []
    claimed_product_images: set[str] = set()
    claimed_product_videos: set[str] = set()
    seen_products: set[str] = set()
    source_product_urls = {
        str(source.get("url") or ""),
        str(source.get("requested_url") or ""),
        *available_links,
    }
    raw_image_products = value.get("fanza_image_products", [])
    if not isinstance(raw_image_products, list):
        raise ValidationError("CodexのFANZA作品画像対応が不正です")
    for item in raw_image_products:
        if not isinstance(item, dict):
            continue
        product_title = _trim_text(str(item.get("product_title") or ""), 180)
        product_code = _trim_text(str(item.get("product_code") or ""), 40)
        product_url = _trim_text(str(item.get("product_url") or ""), 2048)
        reason = _trim_text(str(item.get("reason") or ""), 300)
        raw_image_ids = item.get("image_ids")
        raw_video_ids = item.get("video_ids", [])
        if (
            not reason
            or not isinstance(raw_image_ids, list)
            or not isinstance(raw_video_ids, list)
        ):
            continue
        if product_url not in source_product_urls:
            product_url = ""
        if not product_url and not product_code:
            continue
        image_ids = list(dict.fromkeys(
            str(image_id)
            for image_id in raw_image_ids
            if str(image_id) in available and str(image_id) not in claimed_product_images
        ))
        video_ids = list(dict.fromkeys(
            str(video_id)
            for video_id in raw_video_ids
            if str(video_id) in available_videos
            and str(video_id) not in claimed_product_videos
        ))
        product_key = (
            re.sub(r"[^a-z0-9]", "", product_code.casefold())
            or product_url.casefold()
        )
        if not (image_ids or video_ids) or not product_key or product_key in seen_products:
            continue
        seen_products.add(product_key)
        claimed_product_images.update(image_ids)
        claimed_product_videos.update(video_ids)
        fanza_image_products.append({
            "product_title": product_title,
            "product_code": product_code,
            "product_url": product_url,
            "image_ids": image_ids,
            "video_ids": video_ids,
            "reason": reason,
        })
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
        visible_creator_handle = _trim_text(
            str(item.get("visible_creator_handle") or ""), 80
        )
        subject_match = str(item.get("subject_match") or "unknown")
        if subject_match not in {"matched", "mismatch", "unknown"}:
            subject_match = "unknown"
        normalized_handle = normalized_visible_handle(visible_creator_handle)
        explicit_handle = bool(re.search(r"@[A-Za-z0-9_.-]{2,}", visible_creator_handle))
        if (
            main_subject.get("kind") == "person"
            and not has_multiple_identified_people
            and main_subject_handles
            and explicit_handle
            and normalized_handle
            and normalized_handle not in main_subject_handles
        ):
            subject_match = "mismatch"
            reason = _trim_text(
                f"画面内ハンドル{visible_creator_handle}が主役の確認済み公式ハンドルと一致しないため除外。{reason}",
                160,
            )
        if subject_match == "mismatch":
            verdict = "unrelated"
            recommended_use = "exclude"
            score = 0
        decisions[image_id] = {
            "image_id": image_id,
            "verdict": verdict,
            "role": role,
            "recommended_use": recommended_use,
            "content_group": _trim_text(str(item.get("content_group") or ""), 80),
            "relation": _trim_text(str(item.get("relation") or ""), 160),
            "visible_creator_handle": visible_creator_handle,
            "subject_match": subject_match,
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
            "visible_creator_handle": "",
            "subject_match": "unknown",
            "relevance_score": 0,
            "reason": "Codexが判定を返しませんでした",
        })
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
        visible_creator_handle = _trim_text(
            str(item.get("visible_creator_handle") or ""), 80
        )
        subject_match = str(item.get("subject_match") or "unknown")
        if subject_match not in {"matched", "mismatch", "unknown"}:
            subject_match = "unknown"
        normalized_handle = normalized_visible_handle(visible_creator_handle)
        explicit_handle = bool(re.search(r"@[A-Za-z0-9_.-]{2,}", visible_creator_handle))
        if (
            main_subject.get("kind") == "person"
            and not has_multiple_identified_people
            and main_subject_handles
            and explicit_handle
            and normalized_handle
            and normalized_handle not in main_subject_handles
        ):
            subject_match = "mismatch"
        if subject_match == "mismatch":
            verdict = "unrelated"
            score = 0
        video_decisions[video_id] = {
            "video_id": video_id,
            "verdict": verdict,
            "visible_creator_handle": visible_creator_handle,
            "subject_match": subject_match,
            "relevance_score": score,
            "reason": _trim_text(str(item.get("reason") or "判定理由なし"), 160),
        }
    for video_id in available_videos:
        video_decisions.setdefault(video_id, {
            "video_id": video_id,
            "verdict": "unclear",
            "visible_creator_handle": "",
            "subject_match": "unknown",
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
        "adult_content": adult_content,
        "adult_reason": adult_reason,
        "main_subject": main_subject,
        "official_work": official_work,
        "social_profiles": social_profiles,
        "identified_people": identified_people,
        "media_person_attributions": media_person_attributions,
        "person_identity_candidates": person_identity_candidates,
        "fanza_relevance": fanza_relevance,
        "fanza_performer_name": fanza_performer_name,
        "fanza_search_query": fanza_search_query,
        "fanza_product_code": fanza_product_code,
        "fanza_reason": fanza_reason,
        "fanza_people": fanza_people,
        "fanza_image_products": fanza_image_products,
        "fanza_recommendation_queries": [],
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
    result["ai_adult_content"] = analysis.get("adult_content") is True
    result["ai_adult_reason"] = analysis.get("adult_reason", "")
    result["ai_main_subject"] = analysis.get("main_subject", {})
    result["ai_official_work"] = analysis.get("official_work", {})
    official_work = analysis.get("official_work")
    result["verified_work_destinations"] = []
    if isinstance(official_work, dict) and official_work.get("status") == "verified":
        result["verified_work_destinations"] = [{
            "url": str(official_work.get("url") or ""),
            "title": str(official_work.get("title") or ""),
            "provider": str(official_work.get("provider") or ""),
            "reason": str(official_work.get("reason") or ""),
            "thumbnail_url": str(official_work.get("thumbnail_url") or ""),
            "confidence": 95,
        }]
    subject = analysis.get("main_subject")
    result["official_work_required"] = bool(
        isinstance(subject, dict)
        and subject.get("kind") in {"work", "product"}
        and str(subject.get("name") or "").strip()
    )
    result["ai_social_profiles"] = analysis.get("social_profiles", [])
    result["ai_identified_people"] = analysis.get("identified_people", [])
    result["ai_media_person_attributions"] = analysis.get(
        "media_person_attributions", []
    )
    result["ai_person_identity_candidates"] = analysis.get(
        "person_identity_candidates", []
    )
    official_fanza_people = [
        dict(item) for item in (source.get("fanza_people") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    analyzed_fanza_people = [
        dict(item) for item in (analysis.get("fanza_people") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if str(source.get("source_type") or "") == "fanza_product" and official_fanza_people:
        merged_fanza_people = official_fanza_people
    else:
        people_by_name: dict[str, dict[str, Any]] = {}
        for item in [*official_fanza_people, *analyzed_fanza_people]:
            key = str(item.get("name") or "").strip().casefold()
            if key and key not in people_by_name:
                people_by_name[key] = item
        merged_fanza_people = list(people_by_name.values())
    official_performer_name = str(source.get("fanza_performer_name") or "").strip()
    result["ai_fanza_relevance"] = (
        "exact_product"
        if str(source.get("source_type") or "") == "fanza_product"
        else analysis.get("fanza_relevance", "none")
    )
    result["ai_fanza_performer_name"] = (
        official_performer_name
        or str(analysis.get("fanza_performer_name") or "").strip()
    )
    result["ai_fanza_search_query"] = analysis.get("fanza_search_query", "")
    result["ai_fanza_product_code"] = (
        str(source.get("fanza_maker_code") or source.get("fanza_distribution_code") or "").strip()
        or analysis.get("fanza_product_code", "")
    )
    result["ai_fanza_reason"] = analysis.get("fanza_reason", "")
    result["ai_fanza_people"] = merged_fanza_people
    result["ai_fanza_image_products"] = analysis.get("fanza_image_products", [])
    result["ai_fanza_recommendation_queries"] = analysis.get("fanza_recommendation_queries", [])
    result["analysis_method"] = "codex_vision"
    decisions = {item["image_id"]: item for item in analysis["image_decisions"]}
    images: list[dict[str, Any]] = []
    recommended: list[str] = []
    recommended_thumbnails: list[str] = []
    recommended_body: list[str] = []
    for image in (source.get("images") or []):
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
        if enriched["ai_recommended"]:
            recommended.append(str(enriched["id"]))
        images.append(enriched)
    result["images"] = images
    result["recommended_image_ids"] = recommended
    result["recommended_thumbnail_ids"] = recommended_thumbnails
    result["recommended_body_image_ids"] = recommended_body
    video_decisions = {item["video_id"]: item for item in (analysis.get("video_decisions") or [])}
    videos: list[dict[str, Any]] = []
    recommended_videos: list[str] = []
    for video in (source.get("videos") or []):
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
        if enriched["ai_recommended"]:
            recommended_videos.append(str(enriched["id"]))
        videos.append(enriched)
    result["videos"] = videos
    result["recommended_video_ids"] = recommended_videos
    return result


def _codex_image_attachments(
    source: dict[str, Any],
    selected_image_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(source.get("images", []), start=1):
        if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
            continue
        image_id = str(item.get("id") or f"media-{index}")
        if selected_image_ids is not None and image_id not in selected_image_ids:
            continue
        selected.append((index, item))

    def individual(index: int, item: dict[str, Any]) -> dict[str, Any]:
        image_id = str(item.get("id") or f"media-{index}")
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
        return {
            "id": image_id,
            "filename": filename,
            "data": data,
            "url": str(item.get("url") or ""),
            "alt": str(item.get("alt") or ""),
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
            "ai_reason": str(item.get("ai_reason") or ""),
        }

    if len(selected) <= CODEX_DIRECT_IMAGE_LIMIT:
        return [individual(index, item) for index, item in selected]

    attachments: list[dict[str, Any]] = [individual(*selected[0])]
    try:
        from PIL import Image as PillowImage
        from PIL import ImageDraw, ImageOps
    except ImportError:
        return [individual(index, item) for index, item in selected]

    remaining = selected[1:]
    for sheet_number, start in enumerate(
        range(0, len(remaining), CODEX_CONTACT_SHEET_ITEMS), start=1
    ):
        group = remaining[start:start + CODEX_CONTACT_SHEET_ITEMS]
        rows = (
            len(group) + CODEX_CONTACT_SHEET_COLUMNS - 1
        ) // CODEX_CONTACT_SHEET_COLUMNS
        cell_width, cell_height = CODEX_CONTACT_SHEET_CELL
        sheet = PillowImage.new(
            "RGB",
            (cell_width * CODEX_CONTACT_SHEET_COLUMNS, cell_height * rows),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        group_ids = [
            str(item.get("id") or f"media-{index}")
            for index, item in group
        ]
        for cell_number, (index, item) in enumerate(group, start=1):
            column = (cell_number - 1) % CODEX_CONTACT_SHEET_COLUMNS
            row = (cell_number - 1) // CODEX_CONTACT_SHEET_COLUMNS
            x = column * cell_width
            y = row * cell_height
            image_id = str(item.get("id") or f"media-{index}")
            label = f"{image_id} ({start + cell_number + 1}/{len(selected)})"
            try:
                with PillowImage.open(io.BytesIO(item["data"])) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    fitted = ImageOps.contain(
                        image,
                        (cell_width - 16, cell_height - 42),
                    )
                    image_x = x + (cell_width - fitted.width) // 2
                    image_y = y + 34 + (cell_height - 42 - fitted.height) // 2
                    sheet.paste(fitted, (image_x, image_y))
            except (OSError, ValueError):
                draw.text((x + 8, y + 42), "image decode failed", fill="red")
            draw.rectangle(
                (x, y, x + cell_width - 1, y + cell_height - 1),
                outline="#777777",
            )
            draw.text((x + 8, y + 8), label[:56], fill="black")
        buffer = io.BytesIO()
        sheet.save(buffer, format="JPEG", quality=88, optimize=True)
        sheet_data = buffer.getvalue()
        filename = f"attachment-contact-sheet-{sheet_number:02d}.jpg"
        for cell_number, (index, item) in enumerate(group, start=1):
            image_id = str(item.get("id") or f"media-{index}")
            attachments.append({
                "id": image_id,
                "filename": filename,
                "data": sheet_data,
                "url": str(item.get("url") or ""),
                "alt": str(item.get("alt") or ""),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "ai_reason": str(item.get("ai_reason") or ""),
                "kind": "contact_sheet",
                "media_ids": group_ids,
                "contact_sheet_cell": cell_number,
            })
    return attachments


def _codex_visual_attachments(
    source: dict[str, Any],
    content_attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    context = [
        item
        for item in (source.get("browser_attachments") or [])
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
        for item in (source.get("browser_attachments") or [])
        if isinstance(item, dict)
        and str(item.get("id") or "").startswith("video-frame")
        and isinstance(item.get("data"), bytes)
    ]
    return _codex_visual_attachments({"browser_attachments": video_evidence}, content_attachments)


def _analysis_attachments_for_chunk(
    source: dict[str, Any],
    *,
    include_page: bool,
) -> list[dict[str, Any]]:
    media_ids = {
        str(item.get("id"))
        for kind in ("images", "videos")
        for item in (source.get(kind) or [])
        if isinstance(item, dict) and item.get("id")
    }
    attachments: list[dict[str, Any]] = []
    covered_image_ids: set[str] = set()
    for item in (source.get("browser_attachments") or []):
        if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
            continue
        kind = str(item.get("kind") or "")
        if kind == "full_page":
            if include_page:
                attachments.append(item)
            continue
        attachment_media_ids = {
            str(media_id) for media_id in item.get("media_ids", [])
            if isinstance(media_id, str)
        }
        if attachment_media_ids and attachment_media_ids.intersection(media_ids):
            attachments.append(item)
            if kind == "contact_sheet":
                covered_image_ids.update(attachment_media_ids)
    missing_image_ids = {
        str(item.get("id"))
        for item in (source.get("images") or [])
        if isinstance(item, dict) and item.get("id")
    } - covered_image_ids
    if missing_image_ids:
        attachments.extend(_codex_image_attachments(source, missing_image_ids))
    return attachments


def _merge_codex_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    if not analyses:
        raise ValidationError("Codex analysis returned no results")
    merged = {**analyses[0]}
    merged["image_decisions"] = [
        item
        for analysis in analyses
        for item in (analysis.get("image_decisions") or [])
        if isinstance(item, dict)
    ]
    merged["video_decisions"] = [
        item
        for analysis in analyses
        for item in (analysis.get("video_decisions") or [])
        if isinstance(item, dict)
    ]
    relevance_rank = {"none": 0, "related": 1, "likely_product": 2, "exact_product": 3}
    best_fanza = max(
        analyses,
        key=lambda item: relevance_rank.get(str(item.get("fanza_relevance") or "none"), 0),
    )
    for key in (
        "fanza_relevance", "fanza_performer_name", "fanza_search_query",
        "fanza_product_code", "fanza_reason",
    ):
        merged[key] = best_fanza.get(key, merged.get(key, ""))
    people_by_name: dict[str, dict[str, Any]] = {}
    for analysis in analyses:
        for person in (analysis.get("fanza_people") or []):
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            existing = people_by_name.setdefault(key, {
                "name": name,
                "image_ids": [],
                "reason": str(person.get("reason") or ""),
            })
            existing["image_ids"] = list(dict.fromkeys([
                *existing["image_ids"],
                *[
                    str(image_id) for image_id in person.get("image_ids", [])
                    if isinstance(image_id, str)
                ],
            ]))
    merged["fanza_people"] = list(people_by_name.values())
    social_by_url: dict[str, dict[str, Any]] = {}
    for analysis in analyses:
        for profile in (analysis.get("social_profiles") or []):
            if not isinstance(profile, dict):
                continue
            url = str(profile.get("url") or "").strip()
            if url and url not in social_by_url:
                social_by_url[url] = profile
    merged["social_profiles"] = list(social_by_url.values())
    products_by_key: dict[str, dict[str, Any]] = {}
    claimed_product_images: set[str] = set()
    claimed_product_videos: set[str] = set()
    for analysis in analyses:
        for product in (analysis.get("fanza_image_products") or []):
            if not isinstance(product, dict):
                continue
            product_code = str(product.get("product_code") or "").strip()
            product_url = str(product.get("product_url") or "").strip()
            key = re.sub(r"[^a-z0-9]", "", product_code.casefold()) or product_url.casefold()
            if not key:
                continue
            existing = products_by_key.setdefault(key, {
                "product_title": str(product.get("product_title") or ""),
                "product_code": product_code,
                "product_url": product_url,
                "image_ids": [],
                "video_ids": [],
                "reason": str(product.get("reason") or ""),
            })
            new_image_ids = [
                str(image_id)
                for image_id in product.get("image_ids", [])
                if isinstance(image_id, str) and image_id not in claimed_product_images
            ]
            existing["image_ids"] = list(dict.fromkeys([
                *existing["image_ids"], *new_image_ids,
            ]))
            claimed_product_images.update(new_image_ids)
            new_video_ids = [
                str(video_id)
                for video_id in product.get("video_ids", [])
                if isinstance(video_id, str) and video_id not in claimed_product_videos
            ]
            existing["video_ids"] = list(dict.fromkeys([
                *existing["video_ids"], *new_video_ids,
            ]))
            claimed_product_videos.update(new_video_ids)
    merged["fanza_image_products"] = [
        product for product in products_by_key.values()
        if product["image_ids"] or product["video_ids"]
    ]
    merged["fanza_recommendation_queries"] = []
    return merged


def _representative_image_ids(source: dict[str, Any], selected_ids: list[str]) -> set[str]:
    if len(selected_ids) <= CODEX_GENERATION_IMAGE_SAMPLE:
        return set(selected_ids)
    images = {
        str(item.get("id")): item
        for item in (source.get("images") or [])
        if isinstance(item, dict) and item.get("id")
    }
    ranked = sorted(
        selected_ids,
        key=lambda image_id: (
            int(images.get(image_id, {}).get("ai_relevance_score") or 0),
            images.get(image_id, {}).get("ai_recommended_use") in {"thumbnail", "thumbnail_and_body"},
        ),
        reverse=True,
    )
    chosen = ranked[:CODEX_GENERATION_IMAGE_SAMPLE // 2]
    remaining = [image_id for image_id in selected_ids if image_id not in chosen]
    slots = CODEX_GENERATION_IMAGE_SAMPLE - len(chosen)
    if remaining and slots:
        step = len(remaining) / slots
        chosen.extend(remaining[min(int(index * step), len(remaining) - 1)] for index in range(slots))
    return set(chosen)


def _representative_video_ids(source: dict[str, Any], selected_ids: list[str]) -> set[str]:
    if len(selected_ids) <= CODEX_GENERATION_VIDEO_SAMPLE:
        return set(selected_ids)
    videos = {
        str(item.get("id")): item
        for item in (source.get("videos") or [])
        if isinstance(item, dict) and item.get("id")
    }
    ranked = sorted(
        selected_ids,
        key=lambda video_id: int(videos.get(video_id, {}).get("ai_relevance_score") or 0),
        reverse=True,
    )
    chosen = ranked[:CODEX_GENERATION_VIDEO_SAMPLE // 2]
    remaining = [video_id for video_id in selected_ids if video_id not in chosen]
    slots = CODEX_GENERATION_VIDEO_SAMPLE - len(chosen)
    if remaining and slots:
        step = len(remaining) / slots
        chosen.extend(remaining[min(int(index * step), len(remaining) - 1)] for index in range(slots))
    return set(chosen)


def _recent_draft_language(site_root: Path, limit: int = 6) -> list[dict[str, Any]]:
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
    selected_image_ids: list[str] | None = None,
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
    ordered_image_ids = list(dict.fromkeys(selected_image_ids or []))
    available_image_ids = set(ordered_image_ids)
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
        response_image_ids = item.get("image_ids")
        if not isinstance(response_image_ids, list):
            response_image_ids = []
        response_video_ids = item.get("video_ids")
        if not isinstance(response_video_ids, list):
            response_video_ids = []
        responses.append({
            "text": text,
            "style": style,
            "image_ids": [
                image_id for image_id in response_image_ids
                if isinstance(image_id, str) and image_id in available_image_ids
            ][:3],
            "video_ids": [
                video_id for video_id in response_video_ids
                if isinstance(video_id, str) and video_id in available_video_ids
            ][:2],
        })
    count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else len(responses)
    responses = responses[:count]
    if len(responses) < min(3, count):
        raise ValidationError("Codexが必要なレス数を返しませんでした")
    seen_image_ids: set[str] = set()
    for response in responses:
        unique_image_ids: list[str] = []
        for image_id in response["image_ids"]:
            if image_id in seen_image_ids:
                continue
            seen_image_ids.add(image_id)
            unique_image_ids.append(image_id)
        response["image_ids"] = unique_image_ids
    seen_video_ids: set[str] = set()
    for response in responses:
        unique_video_ids: list[str] = []
        for video_id in response["video_ids"]:
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            unique_video_ids.append(video_id)
        response["video_ids"] = unique_video_ids

    return {"title": title, "summary": summary, "category": category, "tags": tags, "responses": responses}


NON_TOPIC_ARTICLE_TAGS = {
    "pr",
    "pr記事",
    "fanza",
    "dmm",
    "広告",
    "アフィリエイト",
    "成人向け",
    "成人向け作品",
    "成人向け画像",
    "成人向け動画",
    "成人動画",
    "アダルト",
    "アダルト作品",
    "アダルト画像",
    "アダルト動画",
    "18禁",
    "r18",
    "r-18",
}
NORMALIZED_NON_TOPIC_ARTICLE_TAGS = {
    re.sub(r"[\s_-]+", "", tag).casefold()
    for tag in NON_TOPIC_ARTICLE_TAGS
}


def clean_article_topic_tags(values: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip().lstrip("#").strip()
        key = re.sub(r"[\s_-]+", "", tag).casefold()
        if not tag or key in NORMALIZED_NON_TOPIC_ARTICLE_TAGS:
            continue
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag[:40])
        if len(tags) >= limit:
            break
    return tags


def normalize_article_title_label(value: Any) -> str:
    title = _trim_text(str(value or ""), 180)
    match = re.match(r"^\s*【([^】]{1,50})】\s*(.*)$", title)
    if not match:
        normalized = title
    else:
        label, remainder = match.groups()
        has_image = bool(re.search(r"画像(?:\s*\d+\s*枚?)?", label))
        has_video = bool(re.search(r"動画(?:\s*\d+\s*本?)?", label))
        has_gif = bool(re.search(r"GIF(?:\s*\d+\s*本?)?", label, re.IGNORECASE))
        if not (has_image or has_video or has_gif):
            normalized = title
        else:
            residue = re.sub(r"画像(?:\s*\d+\s*枚?)?", "", label)
            residue = re.sub(r"動画(?:\s*\d+\s*本?)?", "", residue)
            residue = re.sub(
                r"GIF(?:\s*\d+\s*本?)?", "", residue, flags=re.IGNORECASE
            )
            extra_labels = [
                re.sub(r"^[\s:：,，、-]+|[\s:：,，、-]+$", "", item)
                for item in re.split(r"[＋+＆&・/／|]+", residue)
            ]
            clean_labels = [
                *(["画像"] if has_image else []),
                *(["動画"] if has_video else []),
                *(["GIF"] if has_gif else []),
                *[item for item in extra_labels if item],
            ]
            clean_label = "＋".join(dict.fromkeys(clean_labels))
            normalized = f"【{clean_label}】{remainder.strip()}"
    normalized = re.sub(
        r"(画像|動画|GIF)\s*\d+\s*(?:枚|本)",
        lambda match: match.group(1).upper() if match.group(1).casefold() == "gif" else match.group(1),
        normalized,
        flags=re.IGNORECASE,
    )
    return _trim_text(normalized, 180)


def apply_codex_result(base_payload: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    payload = {**base_payload}
    payload["title"] = normalize_article_title_label(generated["title"])
    payload["summary"] = generated["summary"]
    payload["category"] = generated["category"]
    payload["tags"] = clean_article_topic_tags(generated.get("tags"))
    payload["comments"] = len(generated["responses"])
    payload["generation_method"] = "codex"
    payload["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")

    media_blocks = [
        {**block}
        for block in base_payload.get("blocks", [])
        if isinstance(block, dict) and block.get("type") in {"images", "videos", "x_embed", "x_timeline"}
    ]
    lead_media = [block for block in media_blocks if block.get("lead")]
    media_blocks = [block for block in media_blocks if not block.get("lead")]
    lead_image_ids = {
        str(image_id)
        for block in lead_media
        for image_id in block.get("image_ids", [])
        if isinstance(image_id, str)
    }
    lead_video_ids = {
        str(video_id)
        for block in lead_media
        for video_id in block.get("video_ids", [])
        if isinstance(video_id, str)
    }
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
    if any(
        item.get("image_ids") or item.get("video_ids")
        for item in generated["responses"]
    ):
        image_ids = [
            str(image.get("id")) for image in base_payload.get("images", [])
            if isinstance(image, dict) and image.get("id")
        ]
        video_ids = [
            str(video.get("id")) for video in base_payload.get("videos", [])
            if isinstance(video, dict) and video.get("id")
        ]
        assigned_images: set[str] = set(lead_image_ids)
        assigned_videos: set[str] = set(lead_video_ids)
        blocks: list[dict[str, Any]] = [*lead_media]
        embeds = [block for block in media_blocks if block.get("type") in {"x_embed", "x_timeline"}]
        for index, (response, generated_response) in enumerate(zip(response_blocks, generated["responses"]), start=1):
            blocks.append(response)
            attached_images = [
                image_id for image_id in generated_response.get("image_ids", [])
                if image_id in image_ids and image_id not in assigned_images
            ]
            if attached_images:
                blocks.append({"id": f"codex-images-{index}", "type": "images", "image_ids": attached_images})
                assigned_images.update(attached_images)
            attached_videos = [
                video_id for video_id in generated_response.get("video_ids", [])
                if video_id in video_ids and video_id not in assigned_videos
            ]
            if attached_videos:
                blocks.append({"id": f"codex-videos-{index}", "type": "videos", "video_ids": attached_videos})
                assigned_videos.update(attached_videos)
            if index == 1:
                blocks.extend(embeds)
        remaining_images = [image_id for image_id in image_ids if image_id not in assigned_images]
        remaining_videos = [video_id for video_id in video_ids if video_id not in assigned_videos]
        if remaining_images or remaining_videos:
            blocks.append({"id": "codex-gallery-separator", "type": "separator"})
        for index, image_id in enumerate(remaining_images, start=1):
            blocks.append({"id": f"codex-gallery-image-{index}", "type": "images", "image_ids": [image_id]})
        for index in range(0, len(remaining_videos), 2):
            blocks.append({
                "id": f"codex-gallery-videos-{index // 2 + 1}",
                "type": "videos",
                "video_ids": remaining_videos[index:index + 2],
            })
        blocks.append({"id": "codex-ad", "type": "ad", "text": "記事内容に合う関連広告枠"})
        payload["blocks"] = blocks
        payload["media_alignment_checked"] = True
        return payload
    blocks: list[dict[str, Any]] = [*lead_media]
    if base_video_ids:
        non_video_media = [
            block
            for block in media_blocks
            if block.get("type") in {"images", "x_embed", "x_timeline"}
        ]
        media_index = 0
        assigned_video_ids = {
            str(video_id)
            for generated_response in generated["responses"]
            for video_id in generated_response.get("video_ids", [])
            if isinstance(video_id, str)
        } | lead_video_ids
        remaining_video_ids = [
            str(video.get("id"))
            for video in base_payload.get("videos", [])
            if isinstance(video, dict)
            and video.get("id")
            and str(video.get("id")) not in assigned_video_ids
        ]
        for index, (response, generated_response) in enumerate(zip(response_blocks, generated["responses"]), start=1):
            blocks.append(response)
            attached_video_ids = [
                video_id for video_id in generated_response.get("video_ids", [])
                if video_id not in lead_video_ids
            ]
            if attached_video_ids:
                blocks.append({
                    "id": f"codex-videos-{index}",
                    "type": "videos",
                    "video_ids": attached_video_ids[:],
                })
            elif remaining_video_ids:
                blocks.append({
                    "id": f"codex-videos-unassigned-{index}",
                    "type": "videos",
                    "video_ids": remaining_video_ids[:2],
                })
                del remaining_video_ids[:2]
            if media_index < len(non_video_media):
                blocks.append(non_video_media[media_index])
                media_index += 1
        blocks.extend(non_video_media[media_index:])
        for index in range(0, len(remaining_video_ids), 2):
            blocks.append({
                "id": f"codex-videos-remaining-{index // 2 + 1}",
                "type": "videos",
                "video_ids": remaining_video_ids[index:index + 2],
            })
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
    # The app places every selected media item after the replies are assembled.
    # Editorial metadata (including content_mode) is applied by the caller later.
    payload["media_alignment_checked"] = True
    return payload


def _codex_analysis_cache_key(
    prompt: str,
    attachments: list[dict[str, Any]],
) -> str:
    digest = hashlib.sha256()
    digest.update(CODEX_ANALYSIS_CACHE_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(prompt.encode("utf-8"))
    for attachment in attachments:
        digest.update(b"\0")
        digest.update(str(attachment.get("id") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(attachment.get("filename") or "").encode("utf-8"))
        data = attachment.get("data")
        if isinstance(data, bytes):
            digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _read_codex_analysis_cache(
    site_root: Path,
    cache_key: str,
) -> dict[str, Any] | None:
    path = site_root / ".article-studio" / "analysis-cache" / f"{cache_key}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != CODEX_ANALYSIS_CACHE_VERSION
        or not isinstance(value.get("analysis"), dict)
    ):
        return None
    return value["analysis"]


def _write_codex_analysis_cache(
    site_root: Path,
    cache_key: str,
    analysis: dict[str, Any],
) -> None:
    root = site_root / ".article-studio" / "analysis-cache"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{cache_key}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({
            "version": CODEX_ANALYSIS_CACHE_VERSION,
            "analysis": analysis,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _combined_codex_schema_path(site_root: Path) -> Path:
    """Build the one-pass schema from the two checked-in source schemas."""
    try:
        analysis_schema = json.loads(CODEX_ANALYSIS_SCHEMA_PATH.read_text(encoding="utf-8"))
        article_schema = json.loads(CODEX_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Codex出力スキーマを準備できません") from exc
    if not isinstance(analysis_schema, dict) or not isinstance(article_schema, dict):
        raise ValidationError("Codex出力スキーマが不正です")
    combined = json.loads(json.dumps(analysis_schema))
    combined.setdefault("required", []).append("article")
    article_schema.pop("$schema", None)
    combined.setdefault("properties", {})["article"] = article_schema
    path = site_root / ".article-studio" / CODEX_COMBINED_SCHEMA_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(combined, ensure_ascii=False, indent=2) + "\n"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != serialized:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    return path


def _validate_x_trend_template_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("X流行テンプレの結果が正しくありません")
    observations = value.get("observations")
    templates = value.get("templates")
    if not isinstance(observations, list) or not isinstance(templates, list):
        raise ValidationError("X流行テンプレの項目が不足しています")

    clean_observations: list[dict[str, str]] = []
    for item in observations[:8]:
        if not isinstance(item, dict):
            continue
        label = _clean_space(item.get("label"))[:30]
        finding = _clean_space(item.get("finding"))[:180]
        confidence = str(item.get("confidence") or "").lower()
        if label and finding and confidence in {"high", "medium", "low"}:
            clean_observations.append({
                "label": label,
                "finding": finding,
                "confidence": confidence,
            })
    if len(clean_observations) < 3:
        raise ValidationError("X流行の観察結果が不足しています")

    clean_templates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in templates[:16]:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("template_id") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{3,32}", template_id) or template_id in seen_ids:
            continue
        media_kinds = list(dict.fromkeys(
            str(kind) for kind in (item.get("media_kinds") or [])
            if str(kind) in {"video", "images", "none"}
        ))
        name = _clean_space(item.get("name"))[:30]
        hook_style = _clean_space(item.get("hook_style"))[:120]
        body_style = _clean_space(item.get("body_style"))[:160]
        ending_style = _clean_space(item.get("ending_style"))[:100]
        tone = _clean_space(item.get("tone"))[:60]
        avoid = [
            _clean_space(entry)[:60]
            for entry in (item.get("avoid") or [])[:8]
            if _clean_space(entry)
        ]
        try:
            length_target = max(25, min(180, int(item.get("length_target") or 80)))
        except (TypeError, ValueError):
            length_target = 80
        combined = " ".join((name, hook_style, body_style, ending_style, tone, *avoid))
        if re.search(r"https?://|(?:^|\s)@[A-Za-z0-9_]+|(?:^|\s)#[^\s]+", combined):
            continue
        if not all((name, media_kinds, hook_style, body_style, ending_style, tone, avoid)):
            continue
        seen_ids.add(template_id)
        clean_templates.append({
            "template_id": template_id,
            "name": name,
            "media_kinds": media_kinds,
            "hook_style": hook_style,
            "body_style": body_style,
            "ending_style": ending_style,
            "tone": tone,
            "length_target": length_target,
            "avoid": avoid,
        })
    if len(clean_templates) < 8:
        raise ValidationError("使えるX投稿テンプレが8本未満です")
    return {"observations": clean_observations, "templates": clean_templates}


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

    def compose_x_trend_templates(self, samples: list[dict[str, Any]]) -> dict[str, Any]:
        compact_samples = []
        for item in samples[:40]:
            if not isinstance(item, dict):
                continue
            compact_samples.append({
                "text": _trim_text(_clean_space(item.get("text")), 420),
                "likes": max(0, int(item.get("likes") or 0)),
                "reposts": max(0, int(item.get("reposts") or 0)),
                "replies": max(0, int(item.get("replies") or 0)),
                "views": max(0, int(item.get("views") or 0)),
                "media_kind": str(item.get("media_kind") or "none"),
            })
        if len(compact_samples) < 3:
            raise ValidationError("Codexが傾向を判断できるX投稿が3件未満です")
        prompt = f"""あなたは淫談屋のX運用を担当する編集者です。
以下は、X上で現在高い反応を得ている成人による成人向け投稿だけを、
アプリ側で年齢・反応数・危険語により選別した観測データです。

目的:
- 投稿内容や固有表現を転載せず、反応を得ている「書き出し・視点・間・締め方」だけを抽象化する。
- 後段の通常ChatGPTが個別記事に合わせて文章を書くための、構造テンプレを8～16本作る。
- video、images、noneの媒体差を考慮し、同じ煽りや同じ語尾ばかりにならないよう十分に散らす。

厳守:
- 観測文の言い回し、固有名詞、ユーザー名、URL、ハッシュタグをテンプレへ写さない。
- テンプレ自体を完成投稿文にしない。hook_style等は日本語の編集指示として書く。
- 未成年、年齢不明を未成年風に扱う表現、盗撮、流出、不同意、違法性を売りにする型を作らない。
- 人気・数字・人物名など、個別記事で確認できない事実を要求しない。
- ニュース見出し、広告、記事要約に見える型を避け、友人へ見せる短い反応の型にする。
- observationsも個別投稿を特定できない抽象的な傾向だけを書く。
- 指定JSONスキーマだけを返し、説明やMarkdownを付けない。

観測データ:
{json.dumps(compact_samples, ensure_ascii=False)}"""
        value = self._execute(
            prompt,
            X_TREND_TEMPLATE_SCHEMA_PATH,
            run_prefix="x-trends-",
        )
        return _validate_x_trend_template_result(value)

    def _build_command(
        self,
        schema_path: Path,
        output_path: Path,
        *,
        web_search: bool = False,
        reasoning_effort: str = CODEX_ARTICLE_REASONING_EFFORT,
    ) -> list[str]:
        if not self.executable:
            raise ValidationError("Codex CLIが見つかりません")
        command = [
            str(self.executable), "exec",
            "--model", CODEX_ARTICLE_MODEL,
            "--config", f'model_reasoning_effort="{reasoning_effort}"',
            "--ephemeral", "--ignore-rules",
            "--sandbox", "read-only", "--skip-git-repo-check",
            "--output-schema", str(schema_path),
            "--output-last-message", str(output_path),
            "--color", "never", "--cd", str(self.site_root), "-",
        ]
        if web_search:
            command[2:2] = ["--enable", "standalone_web_search"]
        return command

    def _execute(
        self,
        prompt: str,
        schema_path: Path,
        *,
        attachments: list[dict[str, Any]] | None = None,
        run_prefix: str = "run-",
        web_search: bool = False,
        reasoning_effort: str = CODEX_ARTICLE_REASONING_EFFORT,
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
            command = self._build_command(
                schema_path,
                output_path,
                web_search=web_search,
                reasoning_effort=reasoning_effort,
            )
            seen_attachment_names: set[str] = set()
            for attachment in attachments or []:
                filename = str(attachment["filename"])
                if filename in seen_attachment_names:
                    continue
                seen_attachment_names.add(filename)
                image_path = temporary_root / filename
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
                error_position = max(
                    raw_detail.rfind("ERROR:"),
                    raw_detail.rfind('"type": "error"'),
                )
                useful_detail = (
                    raw_detail[error_position:]
                    if error_position >= 0
                    else raw_detail[-2000:]
                )
                detail = _trim_text(useful_detail, 1000)
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
        images = [item for item in (source.get("images") or []) if isinstance(item, dict)]
        videos = [item for item in (source.get("videos") or []) if isinstance(item, dict)]
        batch_count = max(
            1,
            (len(images) + CODEX_ANALYSIS_IMAGE_BATCH - 1) // CODEX_ANALYSIS_IMAGE_BATCH,
            (len(videos) + CODEX_ANALYSIS_VIDEO_BATCH - 1) // CODEX_ANALYSIS_VIDEO_BATCH,
        )
        analyses: list[dict[str, Any]] = []
        for index in range(batch_count):
            chunk = {
                **source,
                "images": images[
                    index * CODEX_ANALYSIS_IMAGE_BATCH:
                    (index + 1) * CODEX_ANALYSIS_IMAGE_BATCH
                ],
                "videos": videos[
                    index * CODEX_ANALYSIS_VIDEO_BATCH:
                    (index + 1) * CODEX_ANALYSIS_VIDEO_BATCH
                ],
            }
            attachments = _analysis_attachments_for_chunk(chunk, include_page=index == 0)
            prompt = _codex_analysis_prompt(chunk, attachments)
            cache_key = _codex_analysis_cache_key(prompt, attachments)
            cached = _read_codex_analysis_cache(self.site_root, cache_key)
            if cached is not None:
                try:
                    analyses.append(enrich_analysis_official_work(
                        self.site_root,
                        _validate_codex_analysis(cached, chunk),
                    ))
                    continue
                except ValidationError:
                    pass
            value = self._execute(
                prompt,
                CODEX_ANALYSIS_SCHEMA_PATH,
                attachments=attachments,
                run_prefix=f"analysis-{index + 1}-",
            )
            validated = enrich_analysis_official_work(
                self.site_root,
                _validate_codex_analysis(value, chunk),
            )
            _write_codex_analysis_cache(self.site_root, cache_key, validated)
            analyses.append(validated)
        return _merge_codex_analyses(analyses)

    def verify_social_profile(self, subject: dict[str, Any]) -> dict[str, Any]:
        name = _trim_text(str(subject.get("name") or ""), 80)
        role = _trim_text(str(subject.get("role") or ""), 80)
        subject_reason = _trim_text(str(subject.get("reason") or ""), 240)
        if not name:
            raise ValidationError("公式アカウントを調べる人物名がありません")
        prompt = f"""あなたは記事で紹介する実在の公開活動者について、本人の公式SNSを照合する調査担当です。
Web検索を使い、次の人物だけを調べてください。

人物名: {name}
活動区分: {role or '不明'}
記事内の根拠: {subject_reason or '人物名のみ確認済み'}

調査目的:
- 本人の公式X、TikTok、Instagram、YouTube、MyFans、Fantiaプロフィールを特定する。
- 同姓同名、転載アカウント、ファンアカウント、まとめサイト運営者を除外する。
- 記事で紹介した人物とプロフィールが同一人物だと検証する。

必須条件:
- 推測でユーザー名やURLを作らない。
- status=verifiedにするには、公式プロフィールまたは本人の公式リンク集を含む根拠と、別ドメインの独立した根拠の最低2系統を確認する。
- 検索結果の見出しだけでなく、人物名・活動区分・プロフィールURLの対応を確認する。
- 根拠が1系統だけ、候補が複数、別人の可能性が残る場合はambiguousにする。
- 確認できなければnot_foundにする。verified以外ではprofilesを空配列にする。
- evidenceには実際に確認したページURL、種類、何を確認できたかを書く。
- thumbnail_urlには、確認できた場合だけ、そのプロフィールページ自身のアイコンまたはOGP画像URLを書く。記事画像やAV場面画像を代用しない。
- subject_nameは「{name}」を一字も変えずに返す。
- 指定JSONスキーマだけを返し、説明やMarkdownを付けない。
"""
        value = self._execute(
            prompt,
            SOCIAL_PROFILE_VERIFICATION_SCHEMA_PATH,
            run_prefix="social-profile-",
            web_search=True,
            reasoning_effort="low",
        )
        try:
            return validate_social_verification(value, name, role)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def compose(self, source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        """Classify every candidate and write the final article in one Codex call."""
        image_ids = list(dict.fromkeys(
            str(item.get("id"))
            for item in (source.get("images") or [])
            if isinstance(item, dict) and item.get("id")
        ))
        video_ids = list(dict.fromkeys(
            str(item.get("id"))
            for item in (source.get("videos") or [])
            if isinstance(item, dict) and item.get("id")
        ))
        prompt_options = {
            **options,
            "selected_image_ids": image_ids,
            "selected_video_ids": video_ids,
            "generation_image_ids": image_ids,
            "generation_video_ids": video_ids,
            "recent_language": _recent_draft_language(self.site_root),
        }
        evidence = _analysis_attachments_for_chunk(source, include_page=True)
        representative_ids = _representative_image_ids(source, image_ids)
        content_attachments = _codex_image_attachments(source, representative_ids)
        attachments: list[dict[str, Any]] = []
        seen_filenames: set[str] = set()
        for item in [*evidence, *content_attachments]:
            filename = str(item.get("filename") or "")
            if not filename or filename in seen_filenames:
                continue
            seen_filenames.add(filename)
            attachments.append(item)

        analysis_prompt = _codex_analysis_prompt(source, evidence)
        article_prompt = _codex_prompt(
            source,
            prompt_options,
            content_attachments,
            nested_article=True,
        )
        learning_context = str(options.get("site_learning_context") or "").strip()
        prompt = f"""{analysis_prompt}

{learning_context}

=== 同じ1回の実行で作る完成記事 ===
{article_prompt}

解析後、同じ回答内でタイトルと全レスを編集者として読み直してください。
画像・動画の内容と文章が一致するか、会話が不自然でないか、同じ語尾や定型句を
繰り返していないかを内部で修正し、推敲後の完成稿だけを返してください。
最終JSONは解析項目をすべてトップレベルに置き、完成記事をarticleへ入れます。
articleでは、image_decisionsとvideo_decisionsでarticleと判定したIDだけを使います。
adult_content=falseまたはpage_roleがarticle以外でもarticleは省略しません。
Markdown、説明、途中案は返さず、指定スキーマのJSONだけを返してください。
"""
        value = self._execute(
            prompt,
            _combined_codex_schema_path(self.site_root),
            attachments=attachments,
            run_prefix="compose-",
            web_search=True,
        )
        analysis = enrich_analysis_official_work(
            self.site_root,
            _validate_codex_analysis(value, source),
        )
        raw_article = value.get("article") if isinstance(value, dict) else None
        article = _validate_codex_result(
            raw_article,
            prompt_options.get("reply_count", "auto"),
            selected_media_count=len(image_ids) + len(video_ids),
            selected_image_ids=image_ids,
            selected_video_ids=video_ids,
        )
        overlap_chunks = _codex_article_overlap_chunks(source, article)
        if overlap_chunks:
            repaired_value = self._execute(
                _codex_originality_repair_prompt(
                    source,
                    prompt_options,
                    article,
                    overlap_chunks,
                ),
                CODEX_SCHEMA_PATH,
                attachments=[],
                run_prefix="originality-",
            )
            article = _validate_codex_result(
                repaired_value,
                prompt_options.get("reply_count", "auto"),
                selected_media_count=len(image_ids) + len(video_ids),
                selected_image_ids=image_ids,
                selected_video_ids=video_ids,
            )
            if _codex_article_overlap_chunks(source, article):
                raise ValidationError(
                    "Codexの文章修正後も元ページと長く一致する箇所が残りました"
                )
        return {"analysis": analysis, "article": article}

    def generate(self, source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        prompt_options = {
            **options,
            "recent_language": _recent_draft_language(self.site_root),
        }
        selected_ids = list(dict.fromkeys(
            str(image_id)
            for image_id in prompt_options.get("selected_image_ids", [])
            if isinstance(image_id, str)
        ))
        representative_ids = _representative_image_ids(source, selected_ids)
        content_attachments = _codex_image_attachments(source, representative_ids)
        selected_video_ids = list(dict.fromkeys(
            str(video_id)
            for video_id in prompt_options.get("selected_video_ids", [])
            if isinstance(video_id, str)
        ))
        representative_video_ids = _representative_video_ids(source, selected_video_ids)
        prompt_options["generation_image_ids"] = list(representative_ids)
        prompt_options["generation_video_ids"] = list(representative_video_ids)
        visual_source = {
            **source,
            "browser_attachments": [
                item
                for item in (source.get("browser_attachments") or [])
                if isinstance(item, dict)
                and (
                    not item.get("media_ids")
                    or set(item.get("media_ids", [])).intersection(representative_video_ids)
                )
            ],
        }
        visual_attachments = _codex_generation_attachments(visual_source, content_attachments)
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
            selected_media_count=selected_video_count + len(selected_ids),
            selected_image_ids=selected_ids,
            selected_video_ids=selected_video_ids,
        )
        refined_value = self._execute(
            _codex_refinement_prompt(source, prompt_options, result),
            CODEX_SCHEMA_PATH,
            attachments=[],
            run_prefix="refine-",
        )
        result = _validate_codex_result(
            refined_value,
            prompt_options.get("reply_count", "auto"),
            selected_media_count=selected_video_count + len(selected_ids),
            selected_image_ids=selected_ids,
            selected_video_ids=selected_video_ids,
        )
        payload_image_ids = {
            source_image_id: f"source-image-{index}"
            for index, source_image_id in enumerate(selected_ids, start=1)
        }
        payload_video_ids = {
            source_video_id: f"source-video-{index}"
            for index, source_video_id in enumerate(selected_video_ids, start=1)
        }
        for response in result["responses"]:
            response["image_ids"] = [
                payload_image_ids[image_id]
                for image_id in response["image_ids"]
                if image_id in payload_image_ids
            ]
            response["video_ids"] = [
                payload_video_ids[video_id]
                for video_id in response["video_ids"]
                if video_id in payload_video_ids
            ]
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
                    "image_ids": [],
                    "video_ids": [],
                })
            elif block.get("type") == "images" and responses:
                responses[-1]["image_ids"].extend(
                    str(image_id) for image_id in block.get("image_ids", []) if isinstance(image_id, str)
                )
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
        selected_image_ids = [
            str(item.get("id")) for item in payload.get("images", [])
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
            "selected_image_ids": selected_image_ids,
            "selected_video_ids": selected_video_ids,
            "recent_language": _recent_draft_language(self.site_root),
        }
        refined_value = self._execute(
            _codex_refinement_prompt(source, options, draft),
            CODEX_SCHEMA_PATH,
            attachments=[],
            run_prefix="refine-existing-",
        )
        return _validate_codex_result(
            refined_value,
            "auto",
            selected_media_count=len(selected_video_ids) + len(payload.get("images", [])),
            selected_image_ids=selected_image_ids,
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
    if not isinstance(raw_images, list) or not raw_images:
        raise ValidationError("images must contain at least one file")

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
                related_thumbnail_only=raw.get("related_thumbnail_only") is True,
                thumbnail_owner_url=_optional_text(raw, "thumbnail_owner_url", 2048),
                rights_basis=_optional_text(raw, "rights_basis", 80),
            )
        )
    return tuple(assets)


def _validate_videos(raw_videos: Any) -> list[dict[str, Any]]:
    if raw_videos is None:
        return []
    if not isinstance(raw_videos, list):
        raise ValidationError("videos must be a list")
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
        rights_basis = _optional_text(raw, "rights_basis", 80)
        if rights_basis in {"fanza_official_embed", "fanza_free_video_tool_embed"}:
            parsed_video = urlparse(video_url)
            if kind != "iframe" or parsed_video.scheme != "https" or not _is_dmm_fanza_host((parsed_video.hostname or "").lower()):
                raise ValidationError(f"video {index} must be an official FANZA/DMM iframe embed")
        label = _optional_text(raw, "label", 180) or f"元記事の動画 {index}"
        videos.append({
            "id": video_id,
            "kind": kind,
            "url": video_url,
            "referer": _validate_source_url(_optional_text(raw, "referer", 2048)) if raw.get("referer") else "",
            "poster": poster_url,
            "poster_data_url": poster_data_url,
            "poster_filename": f"video-poster-{index:02d}.jpg" if poster_data_url else "",
            "mime_type": mime_type,
            "label": label,
            "rights_basis": rights_basis,
            "rights_source_url": _validate_source_url(_optional_text(raw, "rights_source_url", 2048)) if raw.get("rights_source_url") else "",
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
    image_map = {image.image_id: image for image in images}
    related_thumbnail_image_ids = {
        image.image_id for image in images if image.related_thumbnail_only
    }
    video_ids = {str(video["id"]) for video in videos or []}
    used_images: list[str] = []
    used_related_thumbnail_images: set[str] = set()
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
            if any(item in related_thumbnail_image_ids for item in selected):
                raise ValidationError(f"block {index} cannot place a related-card thumbnail in the article body")
            used_images.extend(selected)
            blocks.append({"type": "images", "image_ids": selected[:]})
        elif block_type == "videos":
            selected = raw.get("video_ids")
            if not isinstance(selected, list) or not selected:
                raise ValidationError(f"block {index} must contain at least one video")
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
            if any(item in related_thumbnail_image_ids for item in selected):
                raise ValidationError(f"block {index} cannot use a related-card thumbnail as its cover")
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
            if any(item in related_thumbnail_image_ids for item in selected):
                raise ValidationError(f"block {index} cannot use a related-card thumbnail as its cover")
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
            if not _is_dmm_fanza_host(hostname):
                raise ValidationError(f"block {index} product URL must point to DMM or FANZA")
            thumbnail_image_id = _optional_text(raw, "thumbnail_image_id", 80)
            if thumbnail_image_id and thumbnail_image_id not in image_ids:
                thumbnail_image_id = ""
            thumbnail_url = _optional_text(raw, "thumbnail_url", 2048)
            if thumbnail_url:
                try:
                    thumbnail_host = (
                        urlparse(_validate_source_url(thumbnail_url)).hostname or ""
                    ).lower()
                except (TypeError, ValueError):
                    thumbnail_host = ""
                if not _is_dmm_fanza_host(thumbnail_host):
                    thumbnail_url = ""
            thumbnail_source_kind = _optional_text(raw, "thumbnail_source_kind", 40)
            thumbnail_owner_url = _optional_text(raw, "thumbnail_owner_url", 2048)
            if thumbnail_owner_url:
                try:
                    owner_host = (
                        urlparse(_validate_source_url(thumbnail_owner_url)).hostname or ""
                    ).lower()
                except (TypeError, ValueError):
                    owner_host = ""
                if not _is_dmm_fanza_host(owner_host):
                    thumbnail_owner_url = ""
            if thumbnail_source_kind == "fanza_package" and (
                thumbnail_url or thumbnail_image_id
            ):
                thumbnail_owner_url = thumbnail_owner_url or url
                owner_product_id = _draft_fanza_product_id(thumbnail_owner_url)
                destination_product_id = _draft_fanza_product_id(url)
                if (
                    owner_product_id
                    and destination_product_id
                    and owner_product_id != destination_product_id
                ):
                    raise ValidationError(
                        f"block {index} package thumbnail does not match its FANZA product"
                    )
            else:
                thumbnail_source_kind = ""
                thumbnail_owner_url = ""
            if thumbnail_image_id in related_thumbnail_image_ids:
                used_related_thumbnail_images.add(thumbnail_image_id)
            blocks.append({
                "id": _optional_text(raw, "id", 80),
                "type": "product_cta",
                "url": url,
                "title": _require_text(raw, "title", 180),
                "text": _optional_text(raw, "text", 300),
                "button_text": _optional_text(raw, "button_text", 80) or "FANZAで作品を見る",
                "thumbnail_image_id": thumbnail_image_id,
                "thumbnail_url": thumbnail_url,
                "thumbnail_source_kind": thumbnail_source_kind,
                "thumbnail_owner_url": thumbnail_owner_url,
                "placement_label": _optional_text(raw, "placement_label", 60) or "この記事の商品",
                "match_type": _optional_text(raw, "match_type", 40) or "exact_article",
                "match_evidence": _optional_text(raw, "match_evidence", 300),
                "match_confidence": max(0, min(100, _safe_int(raw.get("match_confidence")))),
                "affiliate_status": _optional_text(raw, "affiliate_status", 20),
                "affiliate_destination": _optional_text(raw, "affiliate_destination", 2048),
            })
        elif block_type == "related_link":
            url = _validate_source_url(_require_text(raw, "url", 2048))
            thumbnail_image_id = _optional_text(raw, "thumbnail_image_id", 80)
            if thumbnail_image_id and thumbnail_image_id not in image_ids:
                thumbnail_image_id = ""
            link_kind = _optional_text(raw, "link_kind", 40) or "related"
            thumbnail_url = _optional_text(raw, "thumbnail_url", 2048)
            if thumbnail_url:
                try:
                    thumbnail_url = _validate_source_url(thumbnail_url)
                except (TypeError, ValueError):
                    thumbnail_url = ""
            thumbnail_source_kind = _optional_text(raw, "thumbnail_source_kind", 40)
            thumbnail_owner_url = _optional_text(raw, "thumbnail_owner_url", 2048)
            if thumbnail_owner_url:
                try:
                    thumbnail_owner_url = _validate_source_url(thumbnail_owner_url)
                except (TypeError, ValueError):
                    thumbnail_owner_url = ""
            if link_kind in {"official_profile", "official_content"}:
                # A social/profile card must visually identify its destination,
                # never reuse an unrelated image from the article body.
                local_thumbnail = image_map.get(thumbnail_image_id)
                valid_local_thumbnail = bool(
                    local_thumbnail
                    and local_thumbnail.related_thumbnail_only
                    and local_thumbnail.thumbnail_owner_url.rstrip("/")
                    == thumbnail_owner_url.rstrip("/")
                )
                valid_profile_thumbnail = (
                    thumbnail_source_kind == "profile"
                    and thumbnail_owner_url.rstrip("/") == url.rstrip("/")
                    and bool(thumbnail_url or valid_local_thumbnail)
                )
                valid_official_hub_thumbnail = (
                    thumbnail_source_kind == "official_hub_profile"
                    and bool(thumbnail_owner_url)
                    and bool(thumbnail_url or valid_local_thumbnail)
                )
                valid_identity_fallback = (
                    thumbnail_source_kind == "official_identity_fallback"
                    and thumbnail_owner_url.rstrip("/") == url.rstrip("/")
                    and valid_local_thumbnail
                )
                if not (
                    valid_profile_thumbnail
                    or valid_official_hub_thumbnail
                    or valid_identity_fallback
                ):
                    thumbnail_image_id = ""
                    thumbnail_url = ""
                    thumbnail_source_kind = ""
                    thumbnail_owner_url = ""
            if thumbnail_image_id in related_thumbnail_image_ids:
                used_related_thumbnail_images.add(thumbnail_image_id)
            affiliate_network = _optional_text(raw, "affiliate_network", 30).casefold()
            if affiliate_network not in {"", "fanza"}:
                affiliate_network = ""
            blocks.append({
                "id": _optional_text(raw, "id", 80) or f"related-link-{index}",
                "type": "related_link",
                "url": url,
                "title": _require_text(raw, "title", 180),
                "text": _optional_text(raw, "text", 300),
                "button_text": _optional_text(raw, "button_text", 80) or "関連ページを見る",
                "thumbnail_image_id": thumbnail_image_id,
                "thumbnail_url": thumbnail_url,
                "thumbnail_source_kind": thumbnail_source_kind,
                "thumbnail_owner_url": thumbnail_owner_url,
                "placement_label": _optional_text(raw, "placement_label", 60) or "関連ページ",
                "provider": _optional_text(raw, "provider", 40),
                "link_kind": link_kind,
                "match_evidence": _optional_text(raw, "match_evidence", 300),
                "match_confidence": max(0, min(100, _safe_int(raw.get("match_confidence")))),
                "affiliate_network": affiliate_network,
                "affiliate_eligible": bool(raw.get("affiliate_eligible")),
                "affiliate_status": _optional_text(raw, "affiliate_status", 20),
                "affiliate_destination": _optional_text(raw, "affiliate_destination", 2048),
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
    missing = sorted(
        image_ids
        - set(used_images)
        - used_related_thumbnail_images
        - optional_image_ids
    )
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
    value = json.loads(path.read_text(encoding="utf-8-sig"))
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
        # images_used is the package file count used by add_article integrity
        # checks. body_images_used is the public editorial-media count.
        "images_used": len(images),
        "body_images_used": len({
            str(image_id)
            for block in payload.get("blocks") or []
            if isinstance(block, dict)
            and block.get("type") in {"images", "x_embed", "x_timeline"}
            for image_id in block.get("image_ids") or []
        }),
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


def _related_thumbnail_source(
    block: dict[str, Any],
    image_map: dict[str, ImageAsset],
    *,
    preview: bool,
) -> str:
    thumbnail_url = str(block.get("thumbnail_url") or "").strip()
    if thumbnail_url and str(block.get("thumbnail_source_kind") or "") in {
        "fanza_package", "profile", "official_hub_profile",
        "official_identity_fallback", "official_page", "fanza_performer_sample",
    }:
        return thumbnail_url
    thumbnail_image_id = str(block.get("thumbnail_image_id") or "")
    if thumbnail_image_id in image_map:
        image = image_map[thumbnail_image_id]
        return image.data_url if preview else f"images/{image.filename}"
    return thumbnail_url


def _render_product_cta_block(
    block: dict[str, Any],
    image_map: dict[str, ImageAsset],
    *,
    preview: bool,
) -> str:
    thumbnail_source = _related_thumbnail_source(
        block, image_map, preview=preview
    )
    thumbnail = (
        f'<img class="fanza-product-thumb" '
        f'src="{html.escape(thumbnail_source, quote=True)}" '
        f'alt="{html.escape(str(block.get("title") or ""), quote=True)}" '
        'loading="lazy" referrerpolicy="no-referrer">'
        if thumbnail_source else ""
    )
    placement_label = str(block.get("placement_label") or "この記事の商品")
    match_type = str(block.get("match_type") or "exact_article")
    match_evidence = str(block.get("match_evidence") or "")
    match_confidence = max(0, min(100, int(block.get("match_confidence") or 0)))
    audit = (
        '<div class="fanza-product-audit">'
        f'配置: {html.escape(placement_label)} / 一致度: {match_confidence}%'
        f'{" / 根拠: " + html.escape(match_evidence) if match_evidence else ""}'
        '</div>'
        if preview else ""
    )
    affiliate_status = str(block.get("affiliate_status") or "")
    affiliate_unavailable = affiliate_status != "configured"
    disabled_text = (
        "商品URLを確認できないため、このPRは公開できません"
        if affiliate_status == "invalid"
        else "設定画面でアフィリエイトIDを保存すると自動反映されます"
    )
    product_action = (
        '<span class="fanza-product-button is-disabled">'
        f'{html.escape(disabled_text)}</span>'
        if affiliate_unavailable
        else (
            f'<a class="fanza-product-button" href="{html.escape(str(block.get("url") or ""), quote=True)}" '
            'target="_blank" rel="sponsored noopener noreferrer">'
            f'{html.escape(str(block.get("button_text") or "FANZAで作品を見る"))}</a>'
        )
    )
    content = (
        '<div class="fanza-product-content">'
        f'<div class="fanza-product-label">{html.escape(placement_label)} / PR</div>'
        f'{audit}'
        f'<p class="fanza-product-title">{html.escape(str(block.get("title") or ""))}</p>'
        f'<p class="fanza-product-text">{html.escape(str(block.get("text") or ""))}</p>'
        f'{product_action}</div>'
    )
    product_block_id = str(block.get("id") or "fanza-product")
    return (
        f'<aside class="fanza-product" data-pr-id="{html.escape(product_block_id, quote=True)}" '
        f'data-pr-kind="{html.escape(match_type, quote=True)}" '
        f'data-pr-confidence="{match_confidence}" '
        f'data-pr-evidence="{html.escape(match_evidence, quote=True)}">'
        f'<div class="fanza-product-media{" no-thumb" if not thumbnail else ""}">'
        f'{thumbnail}{content}</div></aside>'
    )


def _render_related_link_block(
    block: dict[str, Any],
    image_map: dict[str, ImageAsset],
    *,
    preview: bool,
) -> str:
    thumbnail_source = _related_thumbnail_source(
        block, image_map, preview=preview
    )
    thumbnail = (
        f'<img class="fanza-product-thumb" '
        f'src="{html.escape(thumbnail_source, quote=True)}" '
        f'alt="{html.escape(str(block.get("title") or ""), quote=True)}" loading="lazy">'
        if thumbnail_source else ""
    )
    label = str(block.get("placement_label") or "関連ページ")
    link_kind = str(block.get("link_kind") or "related")
    evidence = str(block.get("match_evidence") or "")
    confidence = max(0, min(100, int(block.get("match_confidence") or 0)))
    affiliate_status = str(block.get("affiliate_status") or "")
    is_affiliate = affiliate_status == "configured"
    label_suffix = " / PR" if is_affiliate else ""
    audit = (
        '<div class="fanza-product-audit">'
        f'種別: {html.escape(link_kind)} / 確度: {confidence}%'
        f'{" / 根拠: " + html.escape(evidence) if evidence else ""}'
        '</div>'
        if preview else ""
    )
    button_classes = "article-destination-button"
    aside_classes = "article-destination"
    data_attributes = (
        f'data-link-kind="{html.escape(link_kind, quote=True)}" '
        f'data-link-confidence="{confidence}"'
    )
    rel = "noopener noreferrer"
    if is_affiliate:
        button_classes += " fanza-product-button"
        aside_classes += " fanza-product"
        rel = "sponsored noopener noreferrer"
        data_attributes += (
            f' data-pr-id="{html.escape(str(block.get("id") or "related-pr"), quote=True)}"'
            f' data-pr-kind="{html.escape(link_kind, quote=True)}"'
            f' data-pr-confidence="{confidence}"'
            f' data-pr-evidence="{html.escape(evidence, quote=True)}"'
        )
    action = (
        '<span class="article-destination-button">リンクを確認できません</span>'
        if affiliate_status == "invalid"
        else (
            f'<a class="{button_classes}" href="{html.escape(str(block.get("url") or ""), quote=True)}" '
            f'target="_blank" rel="{rel}">{html.escape(str(block.get("button_text") or "関連ページを見る"))}</a>'
        )
    )
    return (
        f'<aside class="{aside_classes}" {data_attributes}>'
        f'<div class="fanza-product-media{" no-thumb" if not thumbnail else ""}">'
        f'{thumbnail}<div class="fanza-product-content">'
        f'<div class="fanza-product-label">{html.escape(label)}{label_suffix}</div>'
        f'{audit}<p class="fanza-product-title">{html.escape(str(block.get("title") or "関連ページ"))}</p>'
        f'<p class="fanza-product-text">{html.escape(str(block.get("text") or ""))}</p>'
        f'{action}</div></div></aside>'
    )


def _render_sidebar_related_section(
    recommendation: dict[str, Any] | None,
    image_map: dict[str, ImageAsset] | None = None,
    *,
    preview: bool = False,
) -> str:
    if recommendation is None:
        return ""
    is_affiliate = str(recommendation.get("affiliate_status") or "") == "configured"
    section_title = "PR" if is_affiliate else "関連リンク"
    label = str(
        recommendation.get("placement_label")
        or ("この記事の商品" if recommendation.get("type") == "product_cta" else "関連記事から探す")
    )
    title = str(recommendation.get("title") or "関連ページを見る")
    link_kind = str(
        recommendation.get("link_kind")
        or recommendation.get("match_type")
        or "related"
    )
    confidence = max(0, min(100, int(recommendation.get("match_confidence") or 0)))
    rel = "sponsored noopener noreferrer" if is_affiliate else "noopener noreferrer"
    promotion_class = " fanza-product" if is_affiliate else ""
    button_class = " fanza-product-button" if is_affiliate else ""
    data_attributes = (
        f' data-pr-id="{html.escape(str(recommendation.get("id") or "sidebar-related"), quote=True)}"'
        f' data-pr-kind="{html.escape(link_kind, quote=True)}"'
        f' data-pr-confidence="{confidence}"'
        if is_affiliate else ""
    )
    thumbnail_source = _related_thumbnail_source(
        recommendation, image_map or {}, preview=preview
    )
    thumbnail = (
        f'<img class="side-ad-link-thumb" '
        f'src="{html.escape(thumbnail_source, quote=True)}" '
        f'alt="{html.escape(title, quote=True)}" loading="lazy" '
        'referrerpolicy="no-referrer">'
        if thumbnail_source else ""
    )
    return (
        f'<section class="sidebox{promotion_class}"{data_attributes}>'
        f'<h2 class="side-title">{html.escape(section_title)}</h2><div class="sidebody">'
        f'<a class="side-ad side-ad-link{button_class}" '
        f'href="{html.escape(str(recommendation.get("url") or ""), quote=True)}" '
        f'target="_blank" rel="{rel}">'
        f'{thumbnail}'
        f'<span class="side-ad-link-label">{html.escape(label)}</span>'
        f'<span class="side-ad-link-title">{html.escape(title)}</span>'
        '<span class="side-ad-link-action">内容を確認する →</span>'
        '</a></div></section>'
    )


def _render_person_discovery_rail(
    payload: dict[str, Any],
    blocks: list[dict[str, Any]],
    image_map: dict[str, ImageAsset],
    *,
    preview: bool,
) -> str:
    def safe_confidence(value: Any) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 0

    verified_people = {
        re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", str(item.get("name") or "").casefold()): item
        for item in payload.get("identified_people") or []
        if isinstance(item, dict)
        and safe_confidence(item.get("confidence")) >= 95
        and str(item.get("name") or "").strip()
    }
    for item in payload.get("fanza_people") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        name = str(item["name"]).strip()
        key = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold())
        verified_people.setdefault(key, {
            "name": name,
            "role": "AV出演者",
            "confidence": 100,
        })
    if not verified_people:
        return ""
    profile_blocks = {
        str(block.get("url") or "").rstrip("/"): block
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and str(block.get("link_kind") or "") in {
            "official_profile", "official_content", "verified_person_search",
        }
        and str(block.get("url") or "").strip()
    }
    service_labels = {
        "x": "X",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "myfans": "MyFans",
        "fantia": "Fantia",
        "fanza": "FANZA出演作",
    }
    grouped: dict[str, dict[str, Any]] = {}
    for profile in payload.get("verified_social_profiles") or []:
        if not isinstance(profile, dict) or safe_confidence(profile.get("confidence")) < 95:
            continue
        name = str(profile.get("name") or profile.get("display_name") or "").strip()
        key = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold())
        if key not in verified_people:
            continue
        service = str(profile.get("service") or "").casefold()
        url = str(profile.get("url") or "").strip()
        if service not in service_labels or not url:
            continue
        group = grouped.setdefault(key, {
            "name": verified_people[key].get("name") or name,
            "role": verified_people[key].get("role") or profile.get("role") or "",
            "profiles": [],
            "thumbnail": "",
        })
        block = profile_blocks.get(url.rstrip("/"))
        if block and not group["thumbnail"]:
            group["thumbnail"] = _related_thumbnail_source(
                block, image_map, preview=preview
            )
        group["profiles"].append((service, url))

    for block in blocks:
        if (
            not isinstance(block, dict)
            or block.get("type") != "related_link"
            or block.get("link_kind") != "verified_person_search"
        ):
            continue
        name = str(block.get("person_name") or "").strip()
        if not name:
            name = re.sub(
                r"の出演作品(?:一覧)?$", "", str(block.get("title") or "")
            ).strip()
        key = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", name.casefold())
        url = str(block.get("url") or "").strip()
        if key not in verified_people or not url:
            continue
        group = grouped.setdefault(key, {
            "name": verified_people[key].get("name") or name,
            "role": verified_people[key].get("role") or "AV出演者",
            "profiles": [],
            "thumbnail": "",
        })
        if not group["thumbnail"]:
            group["thumbnail"] = _related_thumbnail_source(
                block, image_map, preview=preview
            )
        destination = ("fanza", url)
        if destination not in group["profiles"]:
            group["profiles"].append(destination)

    cards: list[str] = []
    for group in grouped.values():
        links = "".join(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(service_labels[service])}</a>'
            for service, url in group["profiles"]
        )
        if not links:
            continue
        thumbnail = (
            f'<img src="{html.escape(group["thumbnail"], quote=True)}" '
            f'alt="{html.escape(str(group["name"]), quote=True)}の公式プロフィール画像" loading="lazy">'
            if group["thumbnail"] else '<div class="person-discovery-placeholder" aria-hidden="true">公式</div>'
        )
        role = (
            f'<span>{html.escape(str(group["role"]))}</span>'
            if str(group["role"]).strip() else ""
        )
        cards.append(
            '<article class="person-discovery-card">'
            f'{thumbnail}<div class="person-discovery-body">'
            f'<strong>{html.escape(str(group["name"]))}</strong>{role}'
            f'<div class="person-discovery-links">{links}</div>'
            '</div></article>'
        )
    if not cards:
        return ""
    return (
        '<section class="person-discovery" aria-label="登場人物の公式リンク">'
        '<div class="person-discovery-head"><span>登場人物</span>'
        '<h2>気になった人の公式ページ</h2></div>'
        f'<div class="person-discovery-rail">{"".join(cards)}</div></section>'
    )


def _render_sidebar(
    site_root: Path,
    metadata: dict[str, Any],
    blocks: list[dict[str, Any]],
    image_map: dict[str, ImageAsset],
    *,
    preview: bool,
    suppress_person_destinations: bool = False,
) -> str:
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

    recommendation = next((
        block for block in reversed(blocks)
        if block.get("type") == "related_link"
        and str(block.get("link_kind") or "") in {
            "inferred_topic_search",
            "inferred_topic_product",
            "person_search",
            "verified_person_search",
        }
        and not (
            suppress_person_destinations
            and str(block.get("link_kind") or "") in {
                "person_search", "verified_person_search",
            }
        )
    ), None)
    related_section = _render_sidebar_related_section(
        recommendation, image_map, preview=preview
    )
    return (
        '<aside class="sidebar">'
        '<section class="sidebox"><h2 class="side-title">今日の人気記事</h2>'
        f'<div class="sidebody">{"".join(ranks)}</div></section>'
        '<section class="sidebox"><h2 class="side-title">最新コメント</h2>'
        f'<div class="sidebody">{"".join(latest)}</div></section>'
        f'{related_section}'
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
    person_rail = _render_person_discovery_rail(
        payload, blocks, image_map, preview=preview
    )

    rendered_blocks: list[str] = []
    post_number = 0
    image_number = 0
    body_display_image_count = sum(
        len(block.get("image_ids") or [])
        for block in blocks
        if isinstance(block, dict) and block.get("type") in {"images", "x_embed", "x_timeline"}
    )
    people_by_image: dict[str, list[str]] = {}
    people_by_video: dict[str, list[str]] = {}
    for attribution in payload.get("media_person_attributions") or []:
        if not isinstance(attribution, dict) or _safe_int(attribution.get("confidence")) < 95:
            continue
        person_name = str(attribution.get("person_name") or "").strip()
        if not person_name:
            continue
        for image_id in attribution.get("image_ids") or []:
            people_by_image.setdefault(str(image_id), []).append(person_name)
        for video_id in attribution.get("video_ids") or []:
            people_by_video.setdefault(str(video_id), []).append(person_name)

    candidate_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for group in payload.get("person_identity_candidates") or []:
        if not isinstance(group, dict):
            continue
        media_type = str(group.get("media_type") or "").casefold()
        media_id = str(group.get("media_id") or "")
        candidates = [
            item for item in group.get("candidates") or []
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and 1 <= _safe_int(item.get("confidence")) <= 94
        ][:3]
        if media_type in {"image", "video"} and media_id and candidates:
            candidate_groups[(media_type, media_id)] = {**group, "candidates": candidates}

    identity_dialogs: list[str] = []
    rendered_identity_dialogs: set[str] = set()
    evidence_labels = {
        "headline": "記事見出し",
        "caption": "画像説明",
        "alt": "画像代替文",
        "link_text": "リンク文",
        "source_metadata": "ページ情報",
        "watermark_ocr": "透かし・文字",
        "filename_clue": "ファイル名",
        "web_search_result": "ウェブ照合",
        "reverse_image_result": "同一画像の照合",
        "video_frame_match": "動画フレーム照合",
    }

    def render_person_identity(media_type: str, media_id: str) -> str:
        verified_names = list(dict.fromkeys(
            people_by_image.get(media_id, [])
            if media_type == "image"
            else people_by_video.get(media_id, [])
        ))
        if verified_names:
            return (
                '<div class="image-person-label image-person-verified">'
                f'<strong>{html.escape(" / ".join(verified_names))}</strong></div>'
            )

        group = candidate_groups.get((media_type, media_id))
        candidates = group.get("candidates", []) if group else []
        if not candidates:
            return ""
        top = candidates[0]
        dialog_id = "person-candidates-" + hashlib.sha1(
            f"{media_type}:{media_id}".encode("utf-8")
        ).hexdigest()[:12]
        if dialog_id not in rendered_identity_dialogs:
            rendered_identity_dialogs.add(dialog_id)
            candidate_items: list[str] = []
            for rank, candidate in enumerate(candidates, start=1):
                evidence_types = [
                    evidence_labels.get(str(value), str(value))
                    for value in candidate.get("evidence_types") or []
                    if str(value).strip()
                ]
                evidence_copy = (
                    '<p class="person-candidate-evidence">根拠: '
                    f'{html.escape("・".join(evidence_types))}</p>'
                    if evidence_types else ""
                )
                evidence_links = "".join(
                    '<a href="{}" target="_blank" rel="noopener noreferrer">根拠ページ{}</a>'.format(
                        html.escape(str(url), quote=True), link_index
                    )
                    for link_index, url in enumerate(
                        candidate.get("evidence_urls") or [], start=1
                    )
                    if str(url).startswith("https://")
                )
                role = str(candidate.get("role") or "").strip()
                role_markup = f'<span>{html.escape(role)}</span>' if role else ""
                candidate_items.append(
                    '<li class="person-candidate-row">'
                    f'<span class="person-candidate-rank">{rank}</span>'
                    '<div class="person-candidate-copy">'
                    f'<div><strong>{html.escape(str(candidate["name"]))}</strong>{role_markup}'
                    f'<b>{_safe_int(candidate.get("confidence"))}%</b></div>'
                    f'<p>{html.escape(str(candidate.get("reason") or ""))}</p>'
                    f'{evidence_copy}'
                    f'<div class="person-candidate-links">{evidence_links}</div>'
                    '</div></li>'
                )
            media_label = "画像" if media_type == "image" else "動画"
            identity_dialogs.append(
                f'<dialog class="person-candidate-dialog" id="{dialog_id}">'
                '<div class="person-candidate-dialog-head">'
                f'<div><span>{media_label}の人物候補</span><strong>特定候補ランキング</strong></div>'
                '<button type="button" data-person-candidates-close>閉じる</button></div>'
                '<p class="person-candidate-notice">95%未満のため確定表示にはしていません。根拠を確認できます。</p>'
                f'<ol>{"".join(candidate_items)}</ol>'
                '</dialog>'
            )
        return (
            f'<button type="button" class="image-person-label image-person-candidate" '
            f'data-person-candidates-open="{dialog_id}" aria-haspopup="dialog">'
            f'<strong>推定：{html.escape(str(top["name"]))}</strong>'
            f'<span>特定確率 {_safe_int(top.get("confidence"))}%</span></button>'
        )

    def render_image_group(selected_ids: list[str]) -> str:
        nonlocal image_number
        selected = [image_map[image_id] for image_id in selected_ids]
        group_class = "image-group single" if len(selected) == 1 else "image-group"
        cards: list[str] = []
        for image in selected:
            image_number += 1
            source = image.data_url if preview else f"images/{image.filename}"
            person_label = render_person_identity("image", image.image_id)
            cards.append(
                '<div class="image-person-entry">'
                f'<div class="image-card {image.orientation}"><img class="zoomable" '
                f'src="{html.escape(source, quote=True)}" alt="{html.escape(image.alt, quote=True)}">'
                f'<span class="image-count">{image_number} / {body_display_image_count}</span></div>'
                f'{person_label}</div>'
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
                frame_width = int(video.get("width") or 720)
                frame_height = int(video.get("height") or 480)
                player = (
                    f'<iframe class="article-video" src="{source}" title="{label}" loading="lazy" '
                    f'style="aspect-ratio: {frame_width} / {frame_height}; height: auto;" scrolling="no" '
                    'sandbox="allow-scripts allow-same-origin allow-presentation allow-popups" '
                    'allow="fullscreen; encrypted-media; picture-in-picture" allowfullscreen></iframe>'
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
                poster_filename = str(video.get("poster_filename") or "")
                poster_source = (
                    f"images/{poster_filename}"
                    if poster_filename
                    else str(video.get("poster") or "")
                )
                poster_attribute = (
                    f' poster="{html.escape(poster_source, quote=True)}"' if poster_source else ""
                )
                player = (
                    f'<video class="article-video" controls playsinline preload="metadata"{poster_attribute}>'
                    f'<source src="{source}" type="{mime_type}">動画を再生できません。</video>'
                )
            person_label = render_person_identity("video", str(video_id))
            cards.append(
                f'<div class="video-card">{player}<div class="video-caption">{label}</div>'
                f'{person_label}</div>'
            )
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
            if not is_empty_related_ad(block):
                rendered_blocks.append(f'<div class="ad">PR<br>{html.escape(block["text"])}</div>')
        elif block["type"] == "product_cta":
            rendered_blocks.append(
                _render_product_cta_block(block, image_map, preview=preview)
            )
        elif block["type"] == "related_link":
            is_grouped_profile = bool(person_rail) and str(
                block.get("link_kind") or ""
            ) in {"official_profile", "official_content", "verified_person_search"}
            if not is_grouped_profile:
                rendered_blocks.append(
                    _render_related_link_block(block, image_map, preview=preview)
                )

    if identity_dialogs:
        rendered_blocks.extend(identity_dialogs)

    if person_rail:
        rendered_blocks.append(person_rail)

    transparency = _optional_text(payload, "transparency_note", 500)
    if bool(payload.get("fictional_responses", True)):
        fixed_note = "レス本文は記事構成のための再構成です。"
        if "再構成" not in transparency:
            transparency = f"{transparency} {fixed_note}".strip()
    # Keep source_url in private draft metadata for duplicate detection,
    # auditing and media downloads. The public article must not expose the
    # scraped page or look like a repost with a mandatory source footer.
    if transparency:
        rendered_blocks.append(
            f'<div class="editorial-note">※{html.escape(transparency)}</div>'
        )

    logo_source = "/site/assets/common/indanya-logo.png" if preview else "../assets/common/indanya-logo.png"
    home_href = "/site/index.html" if preview else "../index.html"
    page_root = "/site/" if preview else "../"
    title = str(metadata["title"])
    summary = str(metadata.get("summary", title))
    sidebar = _render_sidebar(
        site_root,
        metadata,
        blocks,
        image_map,
        preview=preview,
        suppress_person_destinations=bool(person_rail),
    )
    has_x_embeds = any(block["type"] in {"x_embed", "x_timeline"} for block in blocks)
    complete_style = style + ARTICLE_DISCOVERY_STYLE + VIDEO_EMBED_STYLE + FANZA_PRODUCT_STYLE + (X_EMBED_STYLE if has_x_embeds else "")
    asset_version = f"analytics-v{ANALYTICS_VERSION}"
    related_style = (
        f'<link rel="stylesheet" '
        f'href="{page_root}assets/common/article-related.css?v={asset_version}">'
    )
    style_markup = (
        f'<link rel="stylesheet" href="/preview.css">{related_style}'
        if preview else f"<style>{complete_style}</style>{related_style}"
    )
    media_count_label = (
        "画像＋動画" if images and videos
        else "動画" if videos
        else "画像"
    )
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
<link rel="icon" href="{page_root}assets/common/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="{page_root}assets/common/favicon.png" sizes="512x512">
<link rel="apple-touch-icon" href="{page_root}assets/common/apple-touch-icon.png">
<title>{html.escape(title)}｜淫談屋</title>
{style_markup}
<script src="{page_root}assets/common/age-gate.js?v={asset_version}" data-site-root="{page_root}" defer></script>
</head>
<body data-article-slug="{html.escape(str(metadata["slug"]), quote=True)}" data-article-category="{html.escape(str(metadata["category"]), quote=True)}">
<div class="topbar">当サイトはアフィリエイト広告を利用しています。PRは記事内に表示します</div>
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
<footer class="footer"><div class="footer-inner"><span>© 2026 淫談屋</span><span><a href="{page_root}about.html">運営者情報</a>　<a href="{page_root}advertising.html">広告について</a>　<a href="{page_root}contact.html">お問い合わせ</a>　<a href="{page_root}privacy.html">プライバシーポリシー</a></span></div></footer>
<script>{script}</script>
<script src="{page_root}assets/common/article-related.js?v={asset_version}" data-site-root="{page_root}"></script>
{x_widgets}
</body>
</html>
'''


def build_article(payload: dict[str, Any], site_root: Path = SITE_ROOT, *, preview: bool = False) -> ArticleBuild:
    if not isinstance(payload, dict):
        raise ValidationError("article payload must be an object")
    payload = sanitize_related_destinations(
        canonicalize_payload_fanza_links(_sanitize_legacy_product_ctas(payload))
    )
    ensure_related_footer(payload)
    affiliate_id = load_fanza_settings(site_root).get("affiliate_id", "")
    try:
        payload = bind_payload_fanza_affiliate_links(
            payload,
            affiliate_id,
            require_configured=not preview,
        )
    except FanzaAffiliateConfigurationError as exc:
        raise ValidationError(str(exc)) from exc
    if any(
        isinstance(block, dict)
        and block.get("type") == "related_link"
        and block.get("affiliate_status") == "configured"
        for block in payload.get("blocks", [])
    ):
        disclosure = "この記事にはFANZAのアフィリエイト広告が含まれます。"
        existing = str(payload.get("transparency_note") or "")
        if disclosure not in existing:
            payload = {
                **payload,
                "transparency_note": f"{disclosure} {existing}".strip()[:500],
                "promotion_type": "affiliate",
            }
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
    packaged_images = images
    if not preview:
        thumbnail_filename = Path(str(metadata["thumbnail"])).name
        packaged_images = tuple(
            image for image in images
            if image.filename == thumbnail_filename
            or f"images/{image.filename}" in article_html
        )
        if len(packaged_images) != len(images):
            metadata["images_used"] = len(packaged_images)
            metadata = validate_metadata(metadata)
    normalized_payload = {**payload, "videos": videos, "blocks": blocks}
    return ArticleBuild(metadata, article_html, packaged_images, normalized_payload)


def _write_package(build: ArticleBuild, root: Path) -> tuple[Path, Path, Path]:
    metadata_path = root / "metadata.json"
    html_path = root / "article.html"
    images_path = root / "images"
    images_path.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(build.metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(build.article_html, encoding="utf-8")
    for image in build.images:
        (images_path / image.filename).write_bytes(image.data)
    for video in build.payload.get("videos", []):
        if not isinstance(video, dict):
            continue
        poster_data_url = str(video.get("poster_data_url") or "")
        poster_filename = str(video.get("poster_filename") or "")
        if poster_data_url and poster_filename:
            (images_path / poster_filename).write_bytes(
                base64.b64decode(poster_data_url.split(",", 1)[1], validate=True)
            )
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
        for video in build.payload.get("videos", []):
            if not isinstance(video, dict):
                continue
            poster_data_url = str(video.get("poster_data_url") or "")
            poster_filename = str(video.get("poster_filename") or "")
            if poster_data_url and poster_filename:
                archive.writestr(
                    f"images/{poster_filename}",
                    base64.b64decode(
                        poster_data_url.split(",", 1)[1], validate=True
                    ),
                )
    return f"{build.metadata['slug']}.zip", buffer.getvalue()


def _draft_fanza_product_id(value: str) -> str:
    value = unwrap_fanza_affiliate_url(value) or str(value or "")
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return ""
    query = parse_qs(parsed.query)
    product_id = str((query.get("id") or query.get("cid") or [""])[0]).strip()
    if not product_id:
        match = re.search(r"(?:^|/)cid[=/]([^/?#]+)", parsed.path, re.IGNORECASE)
        if match:
            product_id = match.group(1)
    if not product_id:
        match = re.search(r"/(?:product|detail)/([^/?#]+)", parsed.path, re.IGNORECASE)
        if match:
            product_id = match.group(1)
    return re.sub(r"[^a-z0-9]", "", unquote(product_id).casefold())


def _sanitize_legacy_product_ctas(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not any(
        isinstance(block, dict)
        and block.get("type") == "product_cta"
        and not block.get("match_type")
        for block in blocks
    ):
        return payload

    source_product_id = _draft_fanza_product_id(str(payload.get("source_url") or ""))
    sanitized: list[dict[str, Any]] = []
    source_exact: dict[str, Any] | None = None
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "product_cta":
            sanitized.append(block)
            continue
        if block.get("match_type"):
            sanitized.append(block)
            continue
        block_product_id = _draft_fanza_product_id(str(block.get("url") or ""))
        if source_exact is not None or not (
            source_product_id and block_product_id == source_product_id
        ):
            continue
        source_exact = {
            **block,
            "placement_label": "この記事の商品",
            "match_type": "exact_article",
            "match_evidence": "記事元URLとFANZA商品詳細URLの品番が一致",
            "match_confidence": 100,
        }

    if source_exact is None:
        return {**payload, "blocks": sanitized}

    target_index = next((
        index for index, block in enumerate(sanitized)
        if isinstance(block, dict) and block.get("type") == "videos"
    ), -1)
    media_word = "動画"
    if target_index < 0:
        target_index = next((
            index for index, block in enumerate(sanitized)
            if isinstance(block, dict) and block.get("type") == "images"
        ), -1)
        media_word = "画像"
    source_exact.update({
        "placement_label": f"この{media_word}の商品" if target_index >= 0 else "この記事の商品",
        "match_type": (
            "exact_video" if media_word == "動画" and target_index >= 0
            else "exact_image" if target_index >= 0
            else "exact_article"
        ),
        "text": (
            f"上の{media_word}と同じFANZA作品です。"
            "作品ページでサンプル、出演者、配信内容を確認できます。"
            if target_index >= 0
            else str(source_exact.get("text") or "")
        ),
    })
    insert_at = target_index + 1 if target_index >= 0 else next((
        index for index, block in enumerate(sanitized)
        if isinstance(block, dict) and block.get("type") == "ad"
    ), len(sanitized))
    sanitized.insert(insert_at, source_exact)
    return {**payload, "blocks": sanitized}


def save_draft(payload: dict[str, Any], site_root: Path = SITE_ROOT) -> str:
    payload = {**payload, "title": normalize_article_title_label(payload.get("title"))}
    payload = sanitize_related_destinations(
        canonicalize_payload_fanza_links(_sanitize_legacy_product_ctas(payload))
    )
    ensure_related_footer(payload)
    slug = _require_text(payload, "slug", 100)
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("a valid slug is required to save a draft")
    draft_root = site_root / ".article-studio" / "drafts"
    draft_root.mkdir(parents=True, exist_ok=True)
    destination = draft_root / f"{slug}.json"
    temporary = draft_root / f".{slug}.{secrets.token_hex(4)}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    with _DRAFT_PAYLOAD_CACHE_LOCK:
        _DRAFT_PAYLOAD_CACHE.pop(destination.resolve(), None)
    return slug


def load_draft_payload(slug: str, site_root: Path = SITE_ROOT) -> dict[str, Any]:
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("invalid draft slug")
    path = (site_root / ".article-studio" / "drafts" / f"{slug}.json").resolve()
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    with _DRAFT_PAYLOAD_CACHE_LOCK:
        cached = _DRAFT_PAYLOAD_CACHE.get(path)
        if cached and cached[:2] == signature:
            return cached[2]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("draft payload must be an object")
    payload = sanitize_related_destinations(
        canonicalize_payload_fanza_links(_sanitize_legacy_product_ctas(payload))
    )
    payload["title"] = normalize_article_title_label(payload.get("title"))
    ensure_related_footer(payload)
    with _DRAFT_PAYLOAD_CACHE_LOCK:
        _DRAFT_PAYLOAD_CACHE[path] = (signature[0], signature[1], payload)
    return payload


def list_drafts(site_root: Path = SITE_ROOT) -> list[dict[str, Any]]:
    draft_root = site_root / ".article-studio" / "drafts"
    if not draft_root.exists():
        return []
    drafts = []
    for path in sorted(draft_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        # Repair backups sometimes live beside drafts and deliberately include
        # suffixes such as ".before-source-fix". They are not article records.
        if not SLUG_PATTERN.fullmatch(path.stem):
            continue
        try:
            payload = load_draft_payload(path.stem, site_root)
        except (OSError, json.JSONDecodeError, ValidationError):
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
            "image_count": sum(
                1
                for image in payload.get("images", [])
                if isinstance(image, dict) and image.get("related_thumbnail_only") is not True
            ) if isinstance(payload.get("images"), list) else 0,
            "video_count": len(payload.get("videos", [])) if isinstance(payload.get("videos"), list) else 0,
            "affiliate_opportunities": payload.get("affiliate_opportunities", [])
            if isinstance(payload.get("affiliate_opportunities"), list) else [],
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
                    item for item in (source.get("images") or [])
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
                analysis = self.studio_server.codex_runner.analyze(source)
                if analysis.get("adult_content") is not True:
                    reason = str(analysis.get("adult_reason") or "一般向けの内容です")
                    raise ValidationError(
                        f"成人向けでないため記事を作成しませんでした: {reason}"
                    )
                source = apply_codex_analysis(source, analysis)
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
                } for item in (source.get("images") or []) if isinstance(item, dict)]
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
                } for item in (source.get("videos") or []) if isinstance(item, dict)]
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
