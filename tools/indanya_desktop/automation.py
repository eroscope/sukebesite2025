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
NAVIGATION_TITLES = {
    "home", "top", "next", "previous", "more", "read more",
    "ホーム", "トップ", "次へ", "前へ", "もっと見る", "続きを読む",
    "お問い合わせ", "利用規約", "プライバシーポリシー", "サイトマップ",
}
DEFAULT_AUTOMATION_SETTINGS = {
    "start_with_windows": True,
    "auto_crawl_enabled": True,
    "crawl_times": ["06:00", "12:00", "18:00"],
    "auto_draft_limit": 3,
    "crawl_slots": [
        {"slot_id": "morning", "time": "06:00", "count": 3, "source_ids": []},
        {"slot_id": "noon", "time": "12:00", "count": 3, "source_ids": []},
        {"slot_id": "evening", "time": "18:00", "count": 3, "source_ids": []},
    ],
    "publish_enabled": True,
    "publish_slots": [
        {"time": "08:00", "count": 2},
        {"time": "20:00", "count": 2},
    ],
    "queue": [],
    "completed_crawl_runs": [],
    "completed_publish_runs": [],
}
REVIEW_STATUSES = {"unreviewed", "queued", "published", "deleted", "failed"}


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
        elif tag.lower() == "img" and self._anchor_href:
            alternative = values.get("alt", "").strip()
            if alternative:
                self._anchor_text.append(alternative)
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


def _automation_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "automation-settings.json"


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


def _valid_clock(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


def load_automation_settings(site_root: Path) -> dict[str, Any]:
    raw = _read_json(_automation_path(site_root), {})
    raw = raw if isinstance(raw, dict) else {}
    settings = {
        **DEFAULT_AUTOMATION_SETTINGS,
        **raw,
    }
    settings["crawl_times"] = sorted({
        value for value in settings.get("crawl_times", []) if _valid_clock(value)
    }) or list(DEFAULT_AUTOMATION_SETTINGS["crawl_times"])
    raw_crawl_slots = raw.get("crawl_slots")
    if not isinstance(raw_crawl_slots, list):
        raw_crawl_slots = [
            {
                "slot_id": f"legacy-{index + 1}",
                "time": clock,
                "count": settings.get("auto_draft_limit", 3),
                "source_ids": [],
            }
            for index, clock in enumerate(settings["crawl_times"])
        ]
    crawl_slots = []
    seen_slot_ids: set[str] = set()
    for index, item in enumerate(raw_crawl_slots):
        if not isinstance(item, dict) or not _valid_clock(item.get("time")):
            continue
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            continue
        slot_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("slot_id") or ""))[:40]
        if not slot_id or slot_id in seen_slot_ids:
            slot_id = f"slot-{index + 1}-{hashlib.sha1(str(item).encode()).hexdigest()[:6]}"
        seen_slot_ids.add(slot_id)
        source_ids = list(dict.fromkeys(
            str(value) for value in item.get("source_ids", [])
            if isinstance(value, str) and value
        ))
        crawl_slots.append({
            "slot_id": slot_id,
            "time": item["time"],
            "count": count,
            "source_ids": source_ids,
        })
    settings["crawl_slots"] = sorted(crawl_slots, key=lambda item: item["time"]) or list(
        DEFAULT_AUTOMATION_SETTINGS["crawl_slots"]
    )
    settings["crawl_times"] = [item["time"] for item in settings["crawl_slots"]]
    slots = []
    for item in settings.get("publish_slots", []):
        if not isinstance(item, dict) or not _valid_clock(item.get("time")):
            continue
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
            continue
        slots.append({"time": item["time"], "count": count})
    settings["publish_slots"] = sorted(slots, key=lambda item: item["time"]) or list(
        DEFAULT_AUTOMATION_SETTINGS["publish_slots"]
    )
    queue = []
    seen: set[str] = set()
    for item in settings.get("queue", []):
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug in seen:
            continue
        seen.add(slug)
        queue.append({
            "slug": slug,
            "queued_at": str(item.get("queued_at") or ""),
        })
    settings["queue"] = queue
    settings["auto_draft_limit"] = max(1, min(20, int(settings.get("auto_draft_limit") or 3)))
    settings["completed_crawl_runs"] = [
        str(value) for value in settings.get("completed_crawl_runs", []) if isinstance(value, str)
    ][-120:]
    settings["completed_publish_runs"] = [
        str(value) for value in settings.get("completed_publish_runs", []) if isinstance(value, str)
    ][-120:]
    return settings


def save_automation_settings(site_root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    normalized = load_automation_settings(site_root)
    normalized.update(settings)
    # Revalidate the merged value through the same normalization path.
    _write_json(_automation_path(site_root), normalized)
    normalized = load_automation_settings(site_root)
    _write_json(_automation_path(site_root), normalized)
    return normalized


def _draft_path(site_root: Path, slug: str) -> Path:
    return _studio_root(site_root) / "drafts" / f"{slug}.json"


def update_review_status(
    site_root: Path,
    slug: str,
    status: str,
    *,
    message: str = "",
) -> dict[str, Any]:
    if status not in REVIEW_STATUSES:
        raise ValueError("記事状態が不正です")
    path = _draft_path(site_root, slug)
    payload = _read_json(path, {})
    if not isinstance(payload, dict) or not payload:
        raise ValueError("下書きが見つかりません")
    payload["review_status"] = status
    payload["review_status_at"] = datetime.now(JST).isoformat(timespec="seconds")
    if message:
        payload["review_message"] = message[:500]
    elif status != "failed":
        payload.pop("review_message", None)
    _write_json(path, payload)
    return payload


def enqueue_article(site_root: Path, slug: str) -> int:
    path = _draft_path(site_root, slug)
    if not path.is_file():
        raise ValueError("下書きが見つかりません")
    settings = load_automation_settings(site_root)
    existing = next(
        (index for index, item in enumerate(settings["queue"], start=1) if item["slug"] == slug),
        0,
    )
    if existing:
        update_review_status(site_root, slug, "queued")
        return existing
    settings["queue"].append({
        "slug": slug,
        "queued_at": datetime.now(JST).isoformat(timespec="seconds"),
    })
    save_automation_settings(site_root, settings)
    update_review_status(site_root, slug, "queued")
    return len(settings["queue"])


def remove_from_queue(site_root: Path, slug: str, next_status: str = "unreviewed") -> None:
    settings = load_automation_settings(site_root)
    settings["queue"] = [item for item in settings["queue"] if item["slug"] != slug]
    save_automation_settings(site_root, settings)
    if _draft_path(site_root, slug).is_file():
        update_review_status(site_root, slug, next_status)


def soft_delete_article(site_root: Path, slug: str) -> None:
    remove_from_queue(site_root, slug, "deleted")


def queue_position_map(site_root: Path) -> dict[str, int]:
    settings = load_automation_settings(site_root)
    return {item["slug"]: index for index, item in enumerate(settings["queue"], start=1)}


def due_crawl_runs(site_root: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    current = (now or datetime.now(JST)).astimezone(JST)
    settings = load_automation_settings(site_root)
    if not settings.get("auto_crawl_enabled", True):
        return []
    completed = set(settings["completed_crawl_runs"])
    runs = []
    for slot in settings["crawl_slots"]:
        key = f"{current:%Y-%m-%d}@{slot['time']}#{slot['slot_id']}"
        if current.strftime("%H:%M") >= slot["time"] and key not in completed:
            runs.append({**slot, "key": key})
    return runs


def due_publish_runs(site_root: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    current = (now or datetime.now(JST)).astimezone(JST)
    settings = load_automation_settings(site_root)
    if not settings.get("publish_enabled", True):
        return []
    completed = set(settings["completed_publish_runs"])
    queue = [item["slug"] for item in settings["queue"]]
    offset = 0
    runs = []
    for slot in settings["publish_slots"]:
        key = f"{current:%Y-%m-%d}@{slot['time']}"
        if current.strftime("%H:%M") < slot["time"] or key in completed:
            continue
        count = int(slot["count"])
        runs.append({"key": key, "time": slot["time"], "slugs": queue[offset:offset + count]})
        offset += count
    return runs


def record_automation_run(site_root: Path, kind: str, key: str) -> None:
    field = "completed_crawl_runs" if kind == "crawl" else "completed_publish_runs"
    settings = load_automation_settings(site_root)
    values = [value for value in settings[field] if value != key]
    values.append(key)
    settings[field] = values[-120:]
    save_automation_settings(site_root, settings)


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


def mark_candidate_status(
    site_root: Path,
    url: str,
    status: str,
    slug: str = "",
    error: str = "",
) -> None:
    target = normalize_candidate_url(url)
    candidates = list_candidates(site_root)
    for candidate in candidates:
        if normalize_candidate_url(str(candidate.get("url") or "")) == target:
            candidate["status"] = status
            candidate["attempted_at"] = datetime.now(JST).isoformat(timespec="seconds")
            if error:
                candidate["last_error"] = error[:500]
            elif status == "drafted":
                candidate.pop("last_error", None)
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
        if candidate.get("status") in {"drafted", "failed", "ignored"}:
            try:
                urls.add(normalize_candidate_url(str(candidate.get("url") or "")))
            except ValueError:
                pass
    database = _read_json(site_root / "data" / "articles.json", [])
    for article in database if isinstance(database, list) else []:
        if not isinstance(article, dict):
            continue
        value = str(article.get("source_url") or "")
        if value:
            try:
                urls.add(normalize_candidate_url(value))
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
    compact_title = re.sub(r"\s+", " ", html.unescape(title)).strip().lower()
    if compact_title in NAVIGATION_TITLES or len(compact_title) < 4:
        return -100
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


def discover_candidates(
    site_root: Path,
    per_source_limit: int = 12,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    existing = _existing_urls(site_root)
    known = {
        normalize_candidate_url(str(item.get("url") or ""))
        for item in list_candidates(site_root)
        if item.get("url") and item.get("status") != "deleted"
    }
    known_titles = {
        re.sub(r"[\W_]+", "", str(item.get("title") or "").lower())
        for item in list_candidates(site_root)
        if item.get("status") != "deleted"
    }
    discovered: list[dict[str, Any]] = []
    sources = list_sources(site_root)
    selected_sources = set(source_ids or [])
    now = datetime.now(JST).isoformat(timespec="seconds")
    for source in sources:
        if not source.get("enabled", True):
            continue
        if selected_sources and str(source.get("source_id") or "") not in selected_sources:
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
            title_key = re.sub(r"[\W_]+", "", title.lower())
            if title_key and title_key in known_titles:
                continue
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
            if title_key:
                known_titles.add(title_key)
        scored.sort(key=lambda item: int(item["score"]), reverse=True)
        discovered.extend(scored[:per_source_limit])
        source["last_checked_at"] = now
    candidates = list_candidates(site_root)
    candidates = discovered + candidates
    save_sources(site_root, sources)
    save_candidates(site_root, candidates[:300])
    return discovered
