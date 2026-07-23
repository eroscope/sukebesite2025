#!/usr/bin/env python3
"""Validate and atomically add or update one article package."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from validate_article import (
    ValidationError,
    load_json_object,
    validate_database,
    validate_metadata,
)


IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
IMAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ReplaceFunction = Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]


class ArticleHTMLNormalizer(HTMLParser):
    """Normalize article image paths while preserving the document structure."""

    def __init__(self, slug: str, image_names: set[str]) -> None:
        super().__init__(convert_charrefs=False)
        self.slug = slug
        self.image_names = image_names
        self.output: list[str] = []
        self.article_image_references: list[str] = []

    def _normalize_image_source(self, source: str) -> str:
        if "\\" in source or "?" in source or "#" in source:
            raise ValidationError(f"unsafe image source in article HTML: {source}")

        parsed = urlparse(source)
        if parsed.scheme or parsed.netloc or source.startswith("/") or ".." in Path(source).parts[1:]:
            raise ValidationError(f"article images must use local paths: {source}")

        clean = source.removeprefix("./")
        common = clean.removeprefix("../")
        if common.startswith("assets/common/"):
            remainder = common.removeprefix("assets/common/")
            if not remainder or "/" in remainder or not IMAGE_NAME_PATTERN.fullmatch(remainder):
                raise ValidationError(f"unsafe common asset path: {source}")
            return f"../assets/common/{remainder}"

        name = Path(clean).name
        allowed_sources = {
            name,
            f"images/{name}",
            f"assets/articles/{self.slug}/{name}",
            f"../assets/articles/{self.slug}/{name}",
        }
        if name not in self.image_names or clean not in allowed_sources:
            raise ValidationError(f"unknown article image in HTML: {source}")

        self.article_image_references.append(name)
        return f"../assets/articles/{self.slug}/{name}"

    def _render_tag(self, tag: str, attrs: list[tuple[str, str | None]], closed: bool) -> str:
        rendered: list[str] = [f"<{tag}"]
        for key, value in attrs:
            rendered.append(f" {key}")
            if value is not None:
                rendered.append(f'="{html.escape(value, quote=True)}"')
        rendered.append("/>" if closed else ">")
        return "".join(rendered)

    def _normalize_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        if tag.lower() != "img":
            return attrs

        normalized: list[tuple[str, str | None]] = []
        found_source = False
        for key, value in attrs:
            if key.lower() == "src":
                if found_source or value is None:
                    raise ValidationError("each img element must have one src attribute")
                value = self._normalize_image_source(value)
                found_source = True
            normalized.append((key, value))
        return normalized

    def handle_decl(self, decl: str) -> None:
        self.output.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.output.append(self._render_tag(tag, self._normalize_attrs(tag, attrs), False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.output.append(self._render_tag(tag, self._normalize_attrs(tag, attrs), True))

    def handle_endtag(self, tag: str) -> None:
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.output.append(f"<!--{data}-->")

    def handle_pi(self, data: str) -> None:
        self.output.append(f"<?{data}>")


def collect_images(directory: Path) -> list[Path]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValidationError(f"image directory not found or unsafe: {directory}")

    images: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_symlink() or not entry.is_file():
            raise ValidationError(f"image directory must contain files only: {entry.name}")
        if not IMAGE_NAME_PATTERN.fullmatch(entry.name) or entry.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValidationError(f"unsupported or unsafe image filename: {entry.name}")
        images.append(entry)
    if not images:
        raise ValidationError("image directory is empty")
    return images


def normalize_article_html(
    source: str,
    slug: str,
    image_names: set[str],
    allowed_unreferenced_images: set[str] | None = None,
) -> str:
    lowered = source.lower()
    if "<!doctype" not in lowered or "<html" not in lowered or "<body" not in lowered:
        raise ValidationError("article HTML must be a complete document")

    parser = ArticleHTMLNormalizer(slug, image_names)
    try:
        parser.feed(source)
        parser.close()
    except ValueError as exc:
        raise ValidationError(f"invalid article HTML: {exc}") from exc

    references = parser.article_image_references
    if len(references) != len(set(references)):
        raise ValidationError("article HTML must not repeat an article image")
    allowed_unreferenced_images = allowed_unreferenced_images or set()
    if set(references) | allowed_unreferenced_images != image_names:
        missing = sorted(image_names - set(references) - allowed_unreferenced_images)
        extra = sorted(set(references) - image_names)
        details = []
        if missing:
            details.append(f"missing references: {', '.join(missing)}")
        if extra:
            details.append(f"unknown references: {', '.join(extra)}")
        raise ValidationError("article HTML image mismatch (" + "; ".join(details) + ")")
    return "".join(parser.output)


def load_database(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        articles = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"data/articles.json is not valid JSON: {exc}") from exc
    return validate_database(articles)


def upsert_article(
    articles: list[dict[str, object]], metadata: dict[str, object]
) -> list[dict[str, object]]:
    for article in articles:
        if article["id"] == metadata["id"] and article["slug"] != metadata["slug"]:
            raise ValidationError("an existing id cannot be changed to a different slug")
        if article["slug"] == metadata["slug"] and article["id"] != metadata["id"]:
            raise ValidationError("an existing slug cannot be assigned to a different id")

    updated: list[dict[str, object]] = []
    for article in articles:
        if article["id"] == metadata["id"]:
            continue
        if metadata.get("featured") is True and article.get("featured") is True:
            article = {**article, "featured": False}
        updated.append(article)
    updated.append(metadata)
    updated.sort(key=lambda item: str(item["published_at"]), reverse=True)
    validate_database(updated)
    return updated


def _remove_target(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def commit_staged(
    replacements: list[tuple[Path, Path]],
    backup_root: Path,
    replace: ReplaceFunction = os.replace,
) -> None:
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for index, (_, destination) in enumerate(replacements):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                backup = backup_root / f"{index}-{destination.name}"
                replace(destination, backup)
                backups.append((destination, backup))

        for staged, destination in replacements:
            replace(staged, destination)
            installed.append(destination)
    except Exception:
        for destination in reversed(installed):
            _remove_target(destination)
        for destination, backup in reversed(backups):
            if backup.exists() or backup.is_symlink():
                replace(backup, destination)
        raise


def add_article(
    site_root: Path,
    metadata_path: Path,
    html_path: Path,
    images_path: Path | None = None,
    dry_run: bool = False,
) -> str:
    site_root = site_root.resolve()
    data_path = site_root / "data" / "articles.json"
    articles_root = site_root / "articles"
    assets_root = site_root / "assets" / "articles"

    metadata = validate_metadata(load_json_object(metadata_path))
    slug = str(metadata["slug"])
    destination_html = articles_root / f"{slug}.html"
    destination_images = assets_root / slug

    if not html_path.is_file() or html_path.is_symlink():
        raise ValidationError(f"article HTML not found or unsafe: {html_path}")

    if images_path is None:
        package_images = metadata_path.parent / "images"
        images_path = package_images if package_images.is_dir() else destination_images
    source_images = collect_images(images_path)
    image_names = {image.name for image in source_images}

    if len(source_images) != metadata["images_used"]:
        raise ValidationError(
            f"images_used is {metadata['images_used']}, but {len(source_images)} image files were found"
        )
    thumbnail_name = Path(str(metadata["thumbnail"])).name
    if thumbnail_name not in image_names:
        raise ValidationError("thumbnail must reference one of the packaged images")

    article_source = html_path.read_text(encoding="utf-8")
    normalized_html = normalize_article_html(
        article_source,
        slug,
        image_names,
        allowed_unreferenced_images={thumbnail_name},
    )
    articles = load_database(data_path)
    updated_articles = upsert_article(articles, metadata)

    action = "update" if any(item["id"] == metadata["id"] for item in articles) else "add"
    if dry_run:
        return f"Dry run OK: {action} {metadata['id']} ({len(source_images)} images)"

    site_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".indanya-stage-", dir=site_root) as stage_name, tempfile.TemporaryDirectory(
        prefix=".indanya-backup-", dir=site_root
    ) as backup_name:
        stage_root = Path(stage_name)
        stage_html = stage_root / "article.html"
        stage_images = stage_root / "images"
        stage_data = stage_root / "articles.json"

        stage_html.write_text(normalized_html, encoding="utf-8", newline="")
        stage_images.mkdir()
        for image in source_images:
            shutil.copy2(image, stage_images / image.name)
        stage_data.write_text(
            json.dumps(updated_articles, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )

        commit_staged(
            [
                (stage_html, destination_html),
                (stage_images, destination_images),
                (stage_data, data_path),
            ],
            Path(backup_name),
        )

    return f"Completed: {action} {metadata['id']} ({len(source_images)} images)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path, help="article metadata JSON")
    parser.add_argument("article_html", type=Path, help="complete article HTML")
    parser.add_argument("--images", type=Path, help="directory containing all article images")
    parser.add_argument("--dry-run", action="store_true", help="validate without changing the site")
    parser.add_argument("--site-root", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        message = add_article(
            args.site_root,
            args.metadata,
            args.article_html,
            args.images,
            args.dry_run,
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        print(f"Article rejected: {exc}", file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
