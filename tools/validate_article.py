#!/usr/bin/env python3
"""Validate 淫談屋 article metadata with Python's standard library."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ALLOWED_FIELDS = {
    "id",
    "slug",
    "title",
    "category",
    "status",
    "published_at",
    "display_date",
    "comments",
    "url",
    "thumbnail",
    "source_url",
    "images_used",
    "summary",
    "search_text",
    "tags",
    "featured",
}
REQUIRED_FIELDS = {
    "id",
    "slug",
    "title",
    "category",
    "status",
    "published_at",
    "display_date",
    "comments",
    "url",
    "thumbnail",
    "source_url",
    "images_used",
}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DISPLAY_DATE_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$")
LOCAL_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


class ValidationError(ValueError):
    """Raised when article data violates the public data contract."""


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"metadata not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"metadata is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("metadata must be a JSON object")
    return value


def _require_string(metadata: dict[str, Any], field: str, maximum: int) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValidationError(f"{field} must be a non-empty string of at most {maximum} characters")
    return value


def _validate_local_path(value: str, field: str) -> None:
    if not LOCAL_PATH_PATTERN.fullmatch(value) or ".." in value or "//" in value or value.startswith("/"):
        raise ValidationError(f"{field} must be a safe repository-relative path")


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError("published_at must be an ISO 8601 date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("published_at must include a timezone")
    return parsed


def validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_FIELDS - metadata.keys())
    if missing:
        raise ValidationError(f"missing fields: {', '.join(missing)}")

    extras = sorted(metadata.keys() - ALLOWED_FIELDS)
    if extras:
        raise ValidationError(f"unknown fields: {', '.join(extras)}")

    _require_string(metadata, "id", 120)
    slug = _require_string(metadata, "slug", 100)
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError("slug must contain lowercase ASCII letters, numbers, and single hyphens")

    _require_string(metadata, "title", 180)
    _require_string(metadata, "category", 40)
    if metadata["status"] not in {"draft", "published", "archived"}:
        raise ValidationError("status must be draft, published, or archived")

    published_at = _require_string(metadata, "published_at", 40)
    _parse_datetime(published_at)

    display_date = _require_string(metadata, "display_date", 10)
    if not DISPLAY_DATE_PATTERN.fullmatch(display_date):
        raise ValidationError("display_date must use YYYY.MM.DD")

    comments = metadata["comments"]
    if isinstance(comments, bool) or not isinstance(comments, int) or comments < 0:
        raise ValidationError("comments must be a non-negative integer")

    expected_url = f"articles/{slug}.html"
    if metadata["url"] != expected_url:
        raise ValidationError(f"url must be {expected_url}")

    thumbnail = _require_string(metadata, "thumbnail", 260)
    _validate_local_path(thumbnail, "thumbnail")
    expected_thumbnail_prefix = f"assets/articles/{slug}/"
    if not thumbnail.startswith(expected_thumbnail_prefix):
        raise ValidationError(f"thumbnail must be inside {expected_thumbnail_prefix}")

    source_url = _require_string(metadata, "source_url", 2048)
    parsed_source = urlparse(source_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        raise ValidationError("source_url must be an absolute HTTP(S) URL")

    images_used = metadata["images_used"]
    if isinstance(images_used, bool) or not isinstance(images_used, int) or images_used < 1:
        raise ValidationError("images_used must be a positive integer")

    if "summary" in metadata:
        summary = metadata["summary"]
        if not isinstance(summary, str) or len(summary) > 240:
            raise ValidationError("summary must be a string of at most 240 characters")

    if "search_text" in metadata:
        search_text = metadata["search_text"]
        if not isinstance(search_text, str) or len(search_text) > 12000:
            raise ValidationError("search_text must be a string of at most 12000 characters")

    if "tags" in metadata:
        tags = metadata["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) or len(tag) > 40 for tag in tags):
            raise ValidationError("tags must be an array of strings of at most 40 characters")
        if len(tags) != len(set(tags)):
            raise ValidationError("tags must not contain duplicates")

    if "featured" in metadata and not isinstance(metadata["featured"], bool):
        raise ValidationError("featured must be a boolean")

    return metadata


def validate_database(articles: Any) -> list[dict[str, Any]]:
    if not isinstance(articles, list):
        raise ValidationError("data/articles.json must contain an array")

    ids: set[str] = set()
    slugs: set[str] = set()
    for index, article in enumerate(articles):
        if not isinstance(article, dict):
            raise ValidationError(f"article {index} must be an object")
        try:
            validate_metadata(article)
        except ValidationError as exc:
            raise ValidationError(f"article {index}: {exc}") from exc
        if article["id"] in ids:
            raise ValidationError(f"duplicate id: {article['id']}")
        if article["slug"] in slugs:
            raise ValidationError(f"duplicate slug: {article['slug']}")
        ids.add(article["id"])
        slugs.add(article["slug"])
    return articles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path, help="article metadata JSON")
    args = parser.parse_args()

    try:
        metadata = load_json_object(args.metadata)
        validate_metadata(metadata)
    except ValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Valid: {metadata['id']} ({metadata['slug']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
