from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

from article_studio import JST, _validate_source_url, list_drafts


BLOCKED_TERMS = (
    "jk", "jc", "js", "女子高生", "女子校生", "女子中学生", "小学生",
    "未成年", "児童", "ロリ", "幼女",
)
GOOD_TERMS = (
    "動画", "画像", "コスプレ", "グラビア", "水着", "ビキニ", "配信",
    "sns", "twitter", "x.com", "話題", "炎上", "まとめ",
)
BAD_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".zip", ".pdf")
BAD_HOST_PARTS = (
    "accounts.google.",
    "maps.google.",
    "policies.google.",
    "support.google.",
)


@dataclass
class AutoSource:
    source_id: str
    name: str
    url: str
    enabled: bool = True
    kind: str = "web"
    created_at: str = ""
    last_checked_at: str = ""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.title_parts: list[str] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_text = []
        elif tag.lower() == "title":
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_text.append(data)
        if self._in_title:
            self.title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchor_href:
            text = re.sub(r"\s+", " ", " ".join(self._anchor_text)).strip()
            self.links.append({"href": self._anchor_href, "text": html.unescape(text)})
            self._anchor_href = ""
            self._anchor_text = []
        elif tag.lower() == "title":
            self._in_title = False


def _studio_root(site_root: Path) -> Path:
    root = site_root / ".article-studio"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sources_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "sources.json"


def _candidates_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "candidates.json"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_candidate_url(value: str) -> str:
    normalized = _validate_source_url(value)
    normalized, _fragment = urldefrag(normalized)
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def list_sources(site_root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_sources_path(site_root), [])
    return [item for item in raw if isinstance(item, dict)]


def save_sources(site_root: Path, sources: list[dict[str, Any]]) -> None:
    _write_json(_sources_path(site_root), sources)


def add_source(site_root: Path, name: str, url: str) -> dict[str, Any]:
    source_url = normalize_candidate_url(url)
    sources = list_sources(site_root)
    for source in sources:
        if normalize_candidate_url(str(source.get("url") or "")) == source_url:
            source["name"] = name.strip() or source["name"]
            source["enabled"] = True
            save_sources(site_root, sources)
            return source
    source = AutoSource(
        source_id=hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12],
        name=name.strip() or urlparse(source_url).netloc,
        url=source_url,
        created_at=datetime.now(JST).isoformat(timespec="seconds"),
    )
    payload = asdict(source)
    sources.append(payload)
    save_sources(site_root, sources)
    return payload


def remove_source(site_root: Path, source_id: str) -> None:
    save_sources(site_root, [item for item in list_sources(site_root) if item.get("source_id") != source_id])


def update_source(site_root: Path, source_id: str, enabled: bool) -> None:
    sources = list_sources(site_root)
    for source in sources:
        if source.get("source_id") == source_id:
            source["enabled"] = enabled
            source["last_checked_at"] = str(source.get("last_checked_at") or "")
    save_sources(site_root, sources)


def list_candidates(site_root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_candidates_path(site_root), [])
    return [item for item in raw if isinstance(item, dict)]


def save_candidates(site_root: Path, candidates: list[dict[str, Any]]) -> None:
    _write_json(_candidates_path(site_root), candidates)


def mark_candidate_status(site_root: Path, url: str, status: str, slug: str = "") -> None:
    target = normalize_candidate_url(url)
    candidates = list_candidates(site_root)
    for candidate in candidates:
        if normalize_candidate_url(str(candidate.get("url") or "")) == target:
            candidate["status"] = status
            if slug:
                candidate["draft_slug"] = slug
    save_candidates(site_root, candidates)


def _existing_urls(site_root: Path) -> set[str]:
    urls: set[str] = set()
    for draft in list_drafts(site_root):
        value = str(draft.get("source_url") or "")
        if value:
            try:
                urls.add(normalize_candidate_url(value))
            except ValueError:
                pass
    for candidate in list_candidates(site_root):
        if candidate.get("status") == "drafted":
            try:
                urls.add(normalize_candidate_url(str(candidate.get("url") or "")))
            except ValueError:
                pass
    return urls


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.4",
            "User-Agent": "Mozilla/5.0 (IndanyaArticleStudio/2.0)",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if not any(part in content_type for part in ("html", "xml", "text", "rss")):
            return ""
        charset = response.headers.get_content_charset() or "utf-8"
        data = response.read(2_000_000)
    return data.decode(charset, errors="replace")


def _score_candidate(url: str, title: str, source_url: str) -> int:
    haystack = f"{url} {title}".lower()
    if any(term.lower() in haystack for term in BLOCKED_TERMS):
        return -100
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()
    if any(part in netloc for part in BAD_HOST_PARTS) or (netloc.endswith("google.com") and path.startswith("/maps")):
        return -100
    if parsed.path.lower().endswith(BAD_EXTENSIONS):
        return -100
    if any(part in path for part in ("/tag/", "/category/", "/page/", "/author/", "/login", "/privacy")):
        return -20
    score = 10
    if urlparse(source_url).netloc == parsed.netloc:
        score += 8
    score += sum(8 for term in GOOD_TERMS if term.lower() in haystack)
    if re.search(r"/(archives|post|article|entry)[/-]?\d+", parsed.path.lower()) or re.search(r"\d{4,}", parsed.path):
        score += 12
    if len(title) >= 12:
        score += 4
    return score


def discover_candidates(site_root: Path, per_source_limit: int = 12) -> list[dict[str, Any]]:
    existing = _existing_urls(site_root)
    known = {normalize_candidate_url(str(item.get("url") or "")) for item in list_candidates(site_root) if item.get("url")}
    discovered: list[dict[str, Any]] = []
    sources = list_sources(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    for source in sources:
        if not source.get("enabled", True):
            continue
        source_url = normalize_candidate_url(str(source.get("url") or ""))
        try:
            text = _fetch_text(source_url)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            continue
        parser = _LinkParser()
        parser.feed(text)
        scored: list[dict[str, Any]] = []
        for link in parser.links:
            href = str(link.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            try:
                url = normalize_candidate_url(urljoin(source_url, href))
            except ValueError:
                continue
            if url in existing or url in known or url == source_url:
                continue
            title = str(link.get("text") or "").strip()[:160] or urlparse(url).path.rsplit("/", 1)[-1]
            score = _score_candidate(url, title, source_url)
            if score < 15:
                continue
            scored.append({
                "candidate_id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
                "url": url,
                "title": title,
                "source_id": source.get("source_id", ""),
                "source_name": source.get("name", ""),
                "score": score,
                "status": "new",
                "discovered_at": now,
            })
            known.add(url)
        scored.sort(key=lambda item: int(item["score"]), reverse=True)
        discovered.extend(scored[:per_source_limit])
        source["last_checked_at"] = now
    candidates = list_candidates(site_root)
    candidates = discovered + candidates
    save_sources(site_root, sources)
    save_candidates(site_root, candidates[:300])
    return discovered
