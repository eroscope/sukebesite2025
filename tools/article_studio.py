#!/usr/bin/env python3
"""Run the local Indanya article authoring studio."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import io
import json
import re
import secrets
import sys
import tempfile
import threading
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
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
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ANCHOR_PATTERN = re.compile(r"&gt;&gt;([0-9]+)")
ALLOWED_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
JST = ZoneInfo("Asia/Tokyo")


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
            selected = [image_map[image_id] for image_id in block["image_ids"]]
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
            rendered_blocks.append(f'<div class="{group_class}">{"".join(cards)}</div>')
            if image_number == len(selected):
                rendered_blocks.append('<div class="image-note">画像を押すと拡大できます</div>')
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
    style_markup = '<link rel="stylesheet" href="/preview.css">' if preview else f"<style>{style}</style>"
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
        drafts.append({
            "slug": path.stem,
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
            if path.startswith("/site/"):
                relative = path.removeprefix("/site/")
                if relative != "index.html" and not relative.startswith("assets/common/"):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                self._serve_file(self.studio_server.site_root / relative, self.studio_server.site_root)
                return
            if path == "/preview.css":
                style, _ = _extract_sample_assets(self.studio_server.site_root)
                self._send_bytes(style.encode("utf-8"), "text/css; charset=utf-8")
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
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except (OSError, UnicodeError, ValidationError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], site_root: Path) -> None:
        self.site_root = site_root.resolve()
        self.api_token = secrets.token_urlsafe(32)
        super().__init__(address, StudioHandler)


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
