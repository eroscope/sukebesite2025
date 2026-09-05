from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urldefrag, urljoin, urlparse, urlunparse

from article_studio import JST, _validate_source_url, list_drafts
from indanya_desktop.adaptive_quality import (
    candidate_eligibility,
    source_performance,
)
from indanya_desktop.editorial_policy import (
    canonical_fanza_product_url,
    is_fanza_product_url,
)
from indanya_desktop.site_learning import get_site_plan


BLOCKED_TERMS = (
    "女子中学生", "小学生", "未成年", "児童", "幼女",
)
GOOD_TERMS = (
    "動画", "画像", "コスプレ", "グラビア", "水着", "ビキニ", "配信",
    "sns", "twitter", "x.com", "話題", "炎上", "まとめ", "アイドル",
    "女優", "露出", "ハプニング", "流出",
)
POPULAR_CONTEXT_TERMS = (
    "人気", "ランキング", "急上昇", "話題", "注目", "hot", "popular", "rank",
)
NEW_CONTEXT_TERMS = ("新着", "最新", "new", "recent")
SALE_CONTEXT_TERMS = (
    "セール", "割引", "値下げ", "特価", "キャンペーン", "%off", "％off",
    "sale", "discount", "期間限定", "クーポン",
)
FANZA_MANGA_SOURCE_NAME = "FANZA同人 人気・セール"
FANZA_MANGA_SOURCE_URL = "https://www.dmm.co.jp/dc/doujin/"
FANZA_MANGA_FORMAT_MARKERS = ("コミック", "漫画")
FANZA_MANGA_NON_COMIC_MARKERS = ("ゲーム", "ボイス", "音声", "cg集")
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
KNOWN_ARTICLE_PATH_PATTERNS = {
    "chaos-giga.com": re.compile(r"^/archives/\d+/?$"),
    "tyoieronews.com": re.compile(r"^/archives/\d+\.html/?$"),
    "bakufu.jp": re.compile(r"^/archives/\d+/?$"),
    "hnalady.com": re.compile(r"^/blog-entry-\d+\.html/?$"),
}
SOURCE_DISCOVERY_QUERIES = (
    "エロ動画 まとめ アンテナ",
    "グラビア 水着 画像 まとめ ブログ",
    "コスプレ 話題 画像 動画 まとめ",
)
SOURCE_DISCOVERY_HUB_URLS = (
    "https://antenna.eroterest.net/",
)
SOURCE_DISCOVERY_ADULT_TERMS = (
    "エロ", "アダルト", "18禁", "グラビア", "水着", "ビキニ", "ヌード",
    "裸", "av", "エッチ", "おっぱい", "巨乳", "尻", "フェチ", "コスプレ",
)
SOURCE_DISCOVERY_STRONG_TERMS = ("エロ", "アダルト", "18禁", "ヌード", "av", "エッチ")
SOURCE_DISCOVERY_BLOCKED_HOST_PARTS = (
    "bing.com", "google.", "yahoo.", "wikipedia.org", "youtube.com", "youtu.be",
    "x.com", "twitter.com", "instagram.com", "facebook.com", "tiktok.com",
    "pornhub.", "xvideos.", "xhamster.", "spankbang.", "theporndude.",
    "dmm.co.jp", "fanza.co.jp", "mgstage.com", "amazon.", "rakuten.",
    "x.gd", "bit.ly", "t.co",
)
SOURCE_DISCOVERY_COMMERCE_PATHS = (
    "/product/", "/products/", "/item/", "/items/", "/goods/", "/shop/",
    "/shopping/", "/collections/", "/cart/", "/detail/",
)
SOURCE_DISCOVERY_COMMERCE_TERMS = (
    "カートに入れる", "買い物かご", "商品一覧", "税込", "送料無料", "在庫",
    "ご購入", "お支払い", "add to cart", "shopping cart",
)
DEFAULT_AUTOMATION_SETTINGS = {
    "start_with_windows": True,
    "auto_crawl_enabled": True,
    "crawl_times": ["06:00", "12:00", "18:00"],
    "auto_draft_limit": 3,
    "manual_crawl_count": 30,
    "continuous_mode_enabled": True,
    "continuous_crawl_enabled": True,
    "continuous_max_pending": 1,
    "continuous_empty_retry_minutes": 15,
    "continuous_fanza_max_percent": 20,
    "continuous_mix_window": 10,
    "continuous_source_ids": [],
    "fanza_manga_source_decoupled": False,
    "continuous_empty_retry_until": "",
    "continuous_rate_limit_retry_until": "",
    "continuous_rate_limit_level": 0,
    "source_discovery_enabled": True,
    "source_discovery_interval_days": 7,
    "source_discovery_max_additions": 2,
    "source_discovery_last_run_at": "",
    "crawl_slots": [
        {"slot_id": "morning", "time": "06:00", "count": 3, "source_ids": []},
        {"slot_id": "noon", "time": "12:00", "count": 3, "source_ids": []},
        {"slot_id": "evening", "time": "18:00", "count": 3, "source_ids": []},
    ],
    "publish_enabled": True,
    "auto_publish_requested_after_hours": 0,
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
    origin: str = "manual"
    discovery_score: int = 0
    discovery_note: str = ""
    last_selected_at: str = ""
    selected_count: int = 0
    success_count: int = 0
    consecutive_failures: int = 0


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.title_parts: list[str] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self._in_title = False
        self._heading_tag = ""
        self._heading_text: list[str] = []
        self._last_heading = ""
        self._anchor_context = ""
        self._last_link_index = -1
        self._tail_context_length = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        lowered = tag.lower()
        if lowered == "a":
            self._last_link_index = -1
            self._tail_context_length = 0
            self._anchor_href = values.get("href", "")
            self._anchor_text = []
            self._anchor_context = " ".join(
                value for value in (
                    self._last_heading,
                    values.get("class", ""),
                    values.get("id", ""),
                    values.get("title", ""),
                    values.get("aria-label", ""),
                )
                if value
            )
        elif lowered == "img" and self._anchor_href:
            alternative = values.get("alt", "").strip()
            if alternative:
                self._anchor_text.append(alternative)
        elif lowered == "title":
            self._in_title = True
        elif lowered in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = lowered
            self._heading_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_text.append(data)
        elif self._last_link_index >= 0 and self._tail_context_length < 160:
            tail = re.sub(r"\s+", " ", data).strip()
            if tail:
                current = self.links[self._last_link_index]["context"]
                combined = f"{current} {tail}".strip()[:240]
                self.links[self._last_link_index]["context"] = combined
                self._tail_context_length += len(tail)
        if self._in_title:
            self.title_parts.append(data)
        if self._heading_tag:
            self._heading_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a" and self._anchor_href:
            text = re.sub(r"\s+", " ", " ".join(self._anchor_text)).strip()
            self.links.append({
                "href": self._anchor_href,
                "text": html.unescape(text),
                "context": html.unescape(self._anchor_context),
            })
            self._last_link_index = len(self.links) - 1
            self._tail_context_length = 0
            self._anchor_href = ""
            self._anchor_text = []
            self._anchor_context = ""
        elif lowered == "title":
            self._in_title = False
        elif lowered == self._heading_tag:
            self._last_heading = re.sub(r"\s+", " ", " ".join(self._heading_text)).strip()
            self._heading_tag = ""
            self._heading_text = []


def _studio_root(site_root: Path) -> Path:
    root = site_root / ".article-studio"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sources_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "sources.json"


def _candidates_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "candidates.json"


def _trend_history_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "trend-history.json"


def _automation_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "automation-settings.json"


def _source_discovery_path(site_root: Path) -> Path:
    return _studio_root(site_root) / "source-discovery.json"


def _read_json(path: Path, fallback: Any) -> Any:
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(0.03)
    return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_clock(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


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
    settings["manual_crawl_count"] = max(
        1, min(100, int(settings.get("manual_crawl_count") or settings["auto_draft_limit"]))
    )
    settings["continuous_mode_enabled"] = bool(settings.get("continuous_mode_enabled", True))
    settings["continuous_crawl_enabled"] = bool(settings.get("continuous_crawl_enabled", True))
    settings["continuous_max_pending"] = _bounded_int(
        settings.get("continuous_max_pending"), 1, 100, 20
    )
    settings["continuous_empty_retry_minutes"] = _bounded_int(
        settings.get("continuous_empty_retry_minutes"), 1, 120, 15
    )
    settings["continuous_fanza_max_percent"] = _bounded_int(
        settings.get("continuous_fanza_max_percent"), 0, 100, 20
    )
    settings["continuous_mix_window"] = _bounded_int(
        settings.get("continuous_mix_window"), 5, 50, 10
    )
    settings["continuous_source_ids"] = list(dict.fromkeys(
        str(value) for value in settings.get("continuous_source_ids", [])
        if isinstance(value, str) and value
    ))
    settings["fanza_manga_source_decoupled"] = bool(
        settings.get("fanza_manga_source_decoupled", False)
    )
    settings["continuous_empty_retry_until"] = str(
        settings.get("continuous_empty_retry_until") or ""
    )
    settings["continuous_rate_limit_retry_until"] = str(
        settings.get("continuous_rate_limit_retry_until") or ""
    )
    settings["continuous_rate_limit_level"] = _bounded_int(
        settings.get("continuous_rate_limit_level"), 0, 4, 0
    )
    settings["source_discovery_enabled"] = bool(
        settings.get("source_discovery_enabled", True)
    )
    settings["source_discovery_interval_days"] = _bounded_int(
        settings.get("source_discovery_interval_days"), 1, 30, 7
    )
    settings["source_discovery_max_additions"] = _bounded_int(
        settings.get("source_discovery_max_additions"), 1, 10, 2
    )
    settings["source_discovery_last_run_at"] = str(
        settings.get("source_discovery_last_run_at") or ""
    )
    settings["auto_publish_requested_after_hours"] = max(
        0, min(168, int(settings.get("auto_publish_requested_after_hours") or 0))
    )
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
    if settings.get("continuous_mode_enabled", True):
        return []
    if not settings.get("auto_crawl_enabled", True):
        return []
    completed = set(settings["completed_crawl_runs"])
    runs = []
    for slot in settings["crawl_slots"]:
        key = f"{current:%Y-%m-%d}@{slot['time']}#{slot['slot_id']}"
        if current.strftime("%H:%M") >= slot["time"] and key not in completed:
            # Older builds stored a separate source selection per time slot.
            # Every crawl entry point now follows the always-on source list.
            runs.append({
                **slot,
                "source_ids": list(settings["continuous_source_ids"]),
                "key": key,
            })
    return runs


def due_continuous_crawl(
    site_root: Path,
    pending_count: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return one fresh candidate only when no article is being processed."""
    current = (now or datetime.now(JST)).astimezone(JST)
    settings = load_automation_settings(site_root)
    if not (
        settings.get("continuous_mode_enabled", True)
        and settings.get("continuous_crawl_enabled", True)
        and settings.get("auto_crawl_enabled", True)
    ):
        return None
    # Always-on mode intentionally keeps no stale backlog. It discovers one
    # current candidate only after the preceding article has finished.
    target = 1
    count = max(0, target - max(0, int(pending_count)))
    if not count:
        return None
    for retry_key in (
        "continuous_empty_retry_until",
        "continuous_rate_limit_retry_until",
    ):
        retry_value = str(settings.get(retry_key) or "")
        try:
            retry_until = datetime.fromisoformat(retry_value)
            if retry_until.tzinfo is None:
                retry_until = retry_until.replace(tzinfo=JST)
            if current < retry_until.astimezone(JST):
                return None
        except ValueError:
            pass
    return {
        "continuous": True,
        "count": count,
        "target": target,
        "source_ids": list(settings["continuous_source_ids"]),
        "key": f"continuous@{current.isoformat(timespec='minutes')}",
    }


def manual_crawl_run(site_root: Path, count: int) -> dict[str, Any]:
    """Build an immediate crawl from the same sources as always-on mode."""
    settings = load_automation_settings(site_root)
    return {
        "manual": True,
        "count": max(1, min(100, int(count))),
        "source_ids": list(settings["continuous_source_ids"]),
        "key": "",
    }


def enable_continuous_crawl(site_root: Path) -> dict[str, Any]:
    """Enable every switch required for uninterrupted article crawling."""
    settings = load_automation_settings(site_root)
    settings.update({
        "auto_crawl_enabled": True,
        "continuous_mode_enabled": True,
        "continuous_crawl_enabled": True,
        "continuous_empty_retry_until": "",
        "continuous_rate_limit_retry_until": "",
        "continuous_rate_limit_level": 0,
    })
    return save_automation_settings(site_root, settings)


def record_continuous_crawl(
    site_root: Path,
    found_replenishable_candidates: bool,
    now: datetime | None = None,
) -> str:
    settings = load_automation_settings(site_root)
    current = (now or datetime.now(JST)).astimezone(JST)
    if found_replenishable_candidates:
        settings["continuous_empty_retry_until"] = ""
    else:
        settings["continuous_empty_retry_until"] = (
            current + timedelta(minutes=int(settings["continuous_empty_retry_minutes"]))
        ).isoformat(timespec="seconds")
    save_automation_settings(site_root, settings)
    return str(settings["continuous_empty_retry_until"])


def record_continuous_rate_limit(
    site_root: Path,
    now: datetime | None = None,
) -> str:
    """Back off repeated plan-limit checks without treating them as site failures."""
    settings = load_automation_settings(site_root)
    current = (now or datetime.now(JST)).astimezone(JST)
    level = min(4, max(0, int(settings.get("continuous_rate_limit_level") or 0)) + 1)
    # A short first probe catches a quickly released limit. Repeated blocks back
    # off aggressively so an all-day limit cannot consume dozens of requests.
    retry_minutes = (30, 60, 120, 240)[level - 1]
    settings["continuous_rate_limit_level"] = level
    settings["continuous_rate_limit_retry_until"] = (
        current + timedelta(minutes=retry_minutes)
    ).isoformat(timespec="seconds")
    save_automation_settings(site_root, settings)
    return str(settings["continuous_rate_limit_retry_until"])


def clear_continuous_rate_limit(site_root: Path) -> None:
    settings = load_automation_settings(site_root)
    if not (
        settings.get("continuous_rate_limit_retry_until")
        or settings.get("continuous_rate_limit_level")
    ):
        return
    settings["continuous_rate_limit_retry_until"] = ""
    settings["continuous_rate_limit_level"] = 0
    save_automation_settings(site_root, settings)


def record_continuous_article(site_root: Path) -> None:
    """Kept as a no-op for callers from older desktop builds.

    The buffer is now refilled from the actual queue length, not after a timer
    or an article counter reaches an arbitrary threshold.
    """
    return None


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


def due_permission_publications(
    site_root: Path,
    now: datetime | None = None,
) -> list[str]:
    """Return permission-waiting drafts whose automatic publish time has arrived."""
    current = (now or datetime.now(JST)).astimezone(JST)
    settings = load_automation_settings(site_root)
    wait_hours = int(settings["auto_publish_requested_after_hours"])
    if wait_hours <= 0:
        return []
    cutoff = current - timedelta(hours=wait_hours)
    draft_root = _studio_root(site_root) / "drafts"
    due: list[tuple[datetime, str]] = []
    for path in draft_root.glob("*.json"):
        payload = _read_json(path, {})
        if not isinstance(payload, dict) or payload.get("rights_status") != "requested":
            continue
        if payload.get("published_url") or payload.get("review_status") in {"deleted", "published"}:
            continue
        timestamp = str(payload.get("rights_updated_at") or "").strip()
        try:
            requested_at = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=JST)
        requested_at = requested_at.astimezone(JST)
        if requested_at <= cutoff:
            due.append((requested_at, path.stem))
    return [slug for _requested_at, slug in sorted(due)]


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
    fanza_product = canonical_fanza_product_url(normalized)
    if fanza_product:
        return fanza_product
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def _is_fanza_source_url(value: Any) -> bool:
    url = str(value or "").strip()
    if not url:
        return False
    try:
        if is_fanza_product_url(url):
            return True
    except (TypeError, ValueError):
        pass
    hostname = (urlparse(url).hostname or "").lower()
    return (
        hostname == "dmm.co.jp"
        or hostname.endswith(".dmm.co.jp")
        or hostname == "fanza.co.jp"
        or hostname.endswith(".fanza.co.jp")
    )


def candidate_source_kind(candidate: dict[str, Any]) -> str:
    if _is_fanza_source_url(candidate.get("url")):
        return "fanza"
    source_name = str(candidate.get("source_name") or "").lower()
    return "fanza" if "fanza" in source_name or source_name == "dmm" else "general"


def repair_sources_from_candidates(site_root: Path) -> list[dict[str, Any]]:
    """Recover an accidentally emptied source registry from retained candidates."""
    raw_settings = _read_json(_automation_path(site_root), {})
    selected_ids = {
        str(value) for value in (raw_settings.get("continuous_source_ids") or [])
        if isinstance(value, str) and value
    } if isinstance(raw_settings, dict) else set()
    if not selected_ids:
        return []
    raw_candidates = _read_json(_candidates_path(site_root), [])
    candidates_by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in raw_candidates if isinstance(raw_candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        source_id = str(candidate.get("source_id") or "")
        if source_id not in selected_ids:
            continue
        candidates_by_source.setdefault(source_id, []).append(candidate)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for source_candidates in candidates_by_source.values():
        candidates_by_home: dict[str, list[dict[str, Any]]] = {}
        for candidate in source_candidates:
            try:
                home_url = _source_home_url(str(candidate.get("url") or ""))
            except ValueError:
                continue
            if _is_fanza_source_url(home_url):
                home_url = "https://video.dmm.co.jp/"
            candidates_by_home.setdefault(home_url, []).append(candidate)
        if not candidates_by_home:
            continue
        # Old candidates can contain an occasional redirected or syndicated URL.
        # Recover the dominant home for each registered source instead of turning
        # every linked host into a new source.
        home_url, home_candidates = max(
            candidates_by_home.items(),
            key=lambda item: len(item[1]),
        )
        grouped.setdefault(home_url, []).extend(home_candidates)

    recovered: list[dict[str, Any]] = []
    recovered_at = datetime.now(JST).isoformat(timespec="seconds")
    for home_url, candidates in grouped.items():
        expected_id = hashlib.sha1(home_url.encode("utf-8")).hexdigest()[:12]
        representative = next(
            (
                item for item in candidates
                if str(item.get("source_id") or "") == expected_id
            ),
            candidates[0],
        )
        recovered.append({
            **asdict(AutoSource(
                source_id=expected_id,
                name=str(representative.get("source_name") or urlparse(home_url).netloc),
                url=home_url,
                created_at=recovered_at,
            )),
            "discovery_note": "候補履歴から情報源を自動復旧",
        })
    if not recovered:
        return []
    save_sources(site_root, recovered)
    if isinstance(raw_settings, dict):
        raw_settings["continuous_source_ids"] = [
            str(item.get("source_id") or "") for item in recovered
        ]
        _write_json(_automation_path(site_root), raw_settings)
    return recovered


def list_sources(site_root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_sources_path(site_root), [])
    sources = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return sources or repair_sources_from_candidates(site_root)


def save_sources(site_root: Path, sources: list[dict[str, Any]]) -> None:
    _write_json(_sources_path(site_root), sources)


def add_source(
    site_root: Path,
    name: str,
    url: str,
    *,
    origin: str = "manual",
    discovery_score: int = 0,
    discovery_note: str = "",
) -> dict[str, Any]:
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
        origin="automatic" if origin == "automatic" else "manual",
        discovery_score=max(0, int(discovery_score or 0)),
        discovery_note=str(discovery_note or "")[:300],
    )
    payload = asdict(source)
    sources.append(payload)
    save_sources(site_root, sources)
    return payload


def ensure_fanza_manga_source(site_root: Path) -> dict[str, Any]:
    """Keep the official FANZA manga floor available for dedicated manga runs."""
    source = add_source(
        site_root,
        FANZA_MANGA_SOURCE_NAME,
        FANZA_MANGA_SOURCE_URL,
        origin="automatic",
        discovery_score=100,
        discovery_note="漫画スレッド用。人気・ランキング・セール文脈を優先",
    )
    settings = load_automation_settings(site_root)
    source_id = str(source.get("source_id") or "")
    # Older builds silently appended this source to the ordinary always-on
    # crawl. Remove that legacy insertion once. A later explicit selection in
    # the settings dialog remains untouched.
    if not settings.get("fanza_manga_source_decoupled", False):
        settings["continuous_source_ids"] = [
            str(value)
            for value in settings.get("continuous_source_ids") or []
            if str(value) and str(value) != source_id
        ]
        settings["fanza_manga_source_decoupled"] = True
        save_automation_settings(site_root, settings)
    return source


def manga_replenishment_run(
    site_root: Path,
    source_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a one-article crawl that never changes the ordinary source list."""
    current = (now or datetime.now(JST)).astimezone(JST)
    return {
        "manga_replenishment": True,
        "count": 1,
        "source_ids": [str(source_id)] if str(source_id) else [],
        "key": f"manga@{current.isoformat(timespec='minutes')}",
    }


def is_fanza_manga_candidate(candidate: dict[str, Any]) -> bool:
    """Accept comic products only from the mixed FANZA doujin floor."""
    value = " ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("source_card_text") or ""),
    ]).casefold()
    if any(marker.casefold() in value for marker in FANZA_MANGA_NON_COMIC_MARKERS):
        return False
    return any(marker.casefold() in value for marker in FANZA_MANGA_FORMAT_MARKERS)


def remove_source(site_root: Path, source_id: str) -> None:
    save_sources(site_root, [item for item in list_sources(site_root) if item.get("source_id") != source_id])


def update_source(site_root: Path, source_id: str, enabled: bool) -> None:
    sources = list_sources(site_root)
    for source in sources:
        if source.get("source_id") == source_id:
            source["enabled"] = enabled
            source["last_checked_at"] = str(source.get("last_checked_at") or "")
    save_sources(site_root, sources)


def record_source_selection(site_root: Path, source_id: str) -> None:
    if not source_id:
        return
    sources = list_sources(site_root)
    for source in sources:
        if str(source.get("source_id") or "") != source_id:
            continue
        source["last_selected_at"] = datetime.now(JST).isoformat(timespec="seconds")
        source["selected_count"] = int(source.get("selected_count") or 0) + 1
        break
    save_sources(site_root, sources)


def evaluate_candidate_quality(
    site_root: Path,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Score a crawl candidate using local learning and saved GA4 results."""
    url = str(candidate.get("url") or "")
    return candidate_eligibility(
        candidate,
        site_plan=get_site_plan(site_root, url),
        source_performance=source_performance(site_root, url),
    )


def sort_candidates_balanced(
    site_root: Path,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer the least recently used source, then the strongest article."""
    sources = {
        str(item.get("source_id") or ""): item
        for item in list_sources(site_root)
    }
    for candidate in candidates:
        candidate["quality_eligibility"] = evaluate_candidate_quality(
            site_root,
            candidate,
        )
    return sorted(
        candidates,
        key=lambda item: (
            not bool((item.get("quality_eligibility") or {}).get("eligible")),
            str((sources.get(str(item.get("source_id") or "")) or {}).get("last_selected_at") or ""),
            -int((item.get("quality_eligibility") or {}).get("score") or 0),
            -int(item.get("score") or 0),
            str(item.get("discovered_at") or ""),
        ),
    )


def _recent_draft_source_kinds(site_root: Path, limit: int) -> list[str]:
    draft_root = _studio_root(site_root) / "drafts"
    if not draft_root.is_dir():
        return []
    try:
        paths = sorted(
            draft_root.glob("*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )[:max(1, limit)]
    except OSError:
        return []
    kinds: list[str] = []
    source_pattern = re.compile(
        r'"source_url"\s*:\s*("(?:\\.|[^"\\])*")'
    )
    for path in paths:
        source_url = ""
        try:
            with path.open("r", encoding="utf-8") as handle:
                head = handle.read(65536)
            match = source_pattern.search(head)
            if match:
                source_url = str(json.loads(match.group(1)) or "")
        except (OSError, json.JSONDecodeError):
            pass
        if not source_url and "video-dmm-co-jp" in path.stem:
            source_url = "https://video.dmm.co.jp/av/content/"
        kinds.append("fanza" if _is_fanza_source_url(source_url) else "general")
    return kinds


def source_mix_status(site_root: Path) -> dict[str, Any]:
    settings = load_automation_settings(site_root)
    maximum = int(settings.get("continuous_fanza_max_percent") or 0)
    window = int(settings.get("continuous_mix_window") or 10)
    history = _recent_draft_source_kinds(site_root, window)
    recent_fanza = sum(1 for kind in history if kind == "fanza")
    current_percent = round(recent_fanza * 100 / len(history), 1) if history else 0.0
    if maximum <= 0:
        allowed = False
        minimum_gap = window
    elif maximum >= 100:
        allowed = True
        minimum_gap = 0
    else:
        minimum_gap = max(1, math.ceil(100 / maximum) - 1)
        recent_for_projection = history[:max(0, window - 1)]
        projected_fanza = sum(1 for kind in recent_for_projection if kind == "fanza") + 1
        projected_percent = projected_fanza * 100 / (len(recent_for_projection) + 1)
        allowed = (
            "fanza" not in history[:minimum_gap]
            and projected_percent <= maximum + 1e-9
        )
    return {
        "maximum_percent": maximum,
        "window": window,
        "history_count": len(history),
        "fanza_count": recent_fanza,
        "current_percent": current_percent,
        "minimum_gap": minimum_gap,
        "fanza_allowed": allowed,
    }


def filter_candidates_by_source_mix(
    site_root: Path,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = source_mix_status(site_root)
    if status["fanza_allowed"]:
        return list(candidates), status
    filtered = [
        candidate for candidate in candidates
        if candidate_source_kind(candidate) != "fanza"
    ]
    status["blocked_count"] = len(candidates) - len(filtered)
    return filtered, status


def record_source_outcome(site_root: Path, source_id: str, status: str) -> None:
    if not source_id or status not in {"drafted", "failed"}:
        return
    sources = list_sources(site_root)
    changed = False
    for source in sources:
        if str(source.get("source_id") or "") != source_id:
            continue
        changed = True
        if status == "drafted":
            source["success_count"] = int(source.get("success_count") or 0) + 1
            source["consecutive_failures"] = 0
            source["last_success_at"] = datetime.now(JST).isoformat(timespec="seconds")
        else:
            failures = int(source.get("consecutive_failures") or 0) + 1
            source["consecutive_failures"] = failures
            source["last_failure_at"] = datetime.now(JST).isoformat(timespec="seconds")
            if source.get("origin") == "automatic" and failures >= 3:
                source["enabled"] = False
                source["discovery_note"] = "記事生成が3回連続で失敗したため自動停止"
        break
    if changed:
        save_sources(site_root, sources)


def list_source_discovery_log(site_root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_source_discovery_path(site_root), [])
    return [item for item in raw if isinstance(item, dict)][-200:]


def _save_source_discovery_log(site_root: Path, rows: list[dict[str, Any]]) -> None:
    _write_json(_source_discovery_path(site_root), rows[-200:])


def source_discovery_status(
    site_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(JST)).astimezone(JST)
    settings = load_automation_settings(site_root)
    last_value = str(settings.get("source_discovery_last_run_at") or "")
    last_run: datetime | None = None
    try:
        last_run = datetime.fromisoformat(last_value)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=JST)
        last_run = last_run.astimezone(JST)
    except ValueError:
        pass
    interval = int(settings.get("source_discovery_interval_days") or 7)
    next_run = last_run + timedelta(days=interval) if last_run else current
    enabled = bool(settings.get("source_discovery_enabled", True))
    return {
        "enabled": enabled,
        "due": enabled and current >= next_run,
        "last_run_at": last_run.isoformat(timespec="seconds") if last_run else "",
        "next_run_at": next_run.isoformat(timespec="seconds"),
        "interval_days": interval,
        "max_additions": int(settings.get("source_discovery_max_additions") or 2),
        "automatic_source_count": sum(
            1 for item in list_sources(site_root) if item.get("origin") == "automatic"
        ),
    }


def source_discovery_due(site_root: Path, now: datetime | None = None) -> bool:
    return bool(source_discovery_status(site_root, now).get("due"))


def _source_home_url(value: str) -> str:
    normalized = normalize_candidate_url(value)
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("情報源のドメインを確認できません")
    path = "/"
    # Hosted blog services need the account path to identify one site.
    if hostname in {"ameblo.jp", "note.com", "blog.livedoor.jp"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            path = f"/{parts[0]}/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def _source_discovery_blocked(value: str) -> bool:
    host = _normalized_host(value)
    if not host:
        return True
    for marker in SOURCE_DISCOVERY_BLOCKED_HOST_PARTS:
        marker = marker.casefold()
        if marker.endswith("."):
            token = marker.rstrip(".")
            if (
                host == token
                or host.startswith(token + ".")
                or f".{token}." in host
            ):
                return True
            continue
        if host == marker or host.endswith("." + marker):
            return True
    return False


def _search_source_results(query: str) -> list[dict[str, str]]:
    url = (
        "https://www.bing.com/search?format=rss&adlt=off&q="
        + quote_plus(query)
    )
    document = _fetch_text(url)
    root = ET.fromstring(document)
    rows: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        link = str(item.findtext("link") or "").strip()
        title = _clean_candidate_title(str(item.findtext("title") or ""))
        if link:
            rows.append({"url": link, "title": title, "query": query})
    return rows[:12]


def _source_hub_results(
    hub_url: str,
    fetcher: Callable[[str], str],
) -> list[dict[str, str]]:
    document = fetcher(hub_url)
    parser = _LinkParser()
    parser.feed(document)
    hub_host = _normalized_host(hub_url)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        href = str(item.get("href") or "").strip()
        title = _clean_candidate_title(str(item.get("text") or ""))
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        candidate_url = urljoin(hub_url, href)
        host = _normalized_host(candidate_url)
        if not host or host == hub_host or _source_discovery_blocked(candidate_url):
            continue
        if _contains_minor_signal(title) or candidate_url in seen:
            continue
        seen.add(candidate_url)
        rows.append({"url": candidate_url, "title": title, "query": "情報源ハブ"})
        if len(rows) >= 30:
            break
    return rows


def _looks_like_discovery_article(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(part in path for part in SOURCE_DISCOVERY_COMMERCE_PATHS):
        return False
    if re.search(r"/(?:archives?|posts?|articles?|entries?)[/-]?[^/]+", path):
        return True
    if re.search(r"(?:blog-entry-|entry-)[a-z0-9_-]*\d+", path):
        return True
    if re.search(r"\d{4,}", path):
        return True
    values = parse_qs(parsed.query).get("p") or []
    return any(value.isdigit() and int(value) > 100 for value in values)


def _html_media_count(document: str) -> int:
    return (
        len(re.findall(r"<img\b", document, re.I))
        + len(re.findall(r"<(?:video|source)\b", document, re.I))
        + len(re.findall(r"https?://[^\"'\s>]+\.(?:mp4|webm)(?:[?\"'\s>]|$)", document, re.I))
    )


def _probe_source_candidate(
    value: str,
    search_title: str,
    fetcher: Callable[[str], str],
) -> dict[str, Any]:
    checked_at = datetime.now(JST).isoformat(timespec="seconds")
    try:
        source_url = _source_home_url(value)
    except ValueError as exc:
        return {"status": "rejected", "url": value, "reason": str(exc), "checked_at": checked_at}
    if _source_discovery_blocked(source_url):
        return {
            "status": "rejected", "url": source_url,
            "reason": "検索・SNS・通販・動画投稿サイトは情報源にしません", "checked_at": checked_at,
        }
    try:
        document = fetcher(source_url)
    except Exception as exc:
        return {
            "status": "rejected", "url": source_url,
            "reason": f"トップページを取得できません: {str(exc)[:120]}", "checked_at": checked_at,
        }
    parser = _LinkParser()
    try:
        parser.feed(document)
    except Exception:
        return {
            "status": "rejected", "url": source_url,
            "reason": "HTML構造を確認できません", "checked_at": checked_at,
        }
    page_title = _clean_candidate_title(" ".join(parser.title_parts))
    link_text = " ".join(str(item.get("text") or "") for item in parser.links)
    page_text = html.unescape(re.sub(r"<[^>]+>", " ", document[:400_000]))
    relevance_text = f"{search_title} {page_title} {link_text} {page_text}".lower()
    commerce_signals = sum(
        1 for term in SOURCE_DISCOVERY_COMMERCE_TERMS if term.lower() in relevance_text
    )
    if commerce_signals >= 2:
        return {
            "status": "rejected", "url": source_url,
            "reason": "通販・商品販売サイトと判断しました", "checked_at": checked_at,
        }
    if _contains_minor_signal(relevance_text):
        return {
            "status": "rejected", "url": source_url,
            "reason": "未成年を示す表現を検出", "checked_at": checked_at,
        }
    adult_terms = {
        term for term in SOURCE_DISCOVERY_ADULT_TERMS if term.lower() in relevance_text
    }
    strong = any(term.lower() in relevance_text for term in SOURCE_DISCOVERY_STRONG_TERMS)
    if not strong and len(adult_terms) < 2:
        return {
            "status": "rejected", "url": source_url,
            "reason": "成人向け情報源と判断できる材料が不足", "checked_at": checked_at,
        }
    article_urls: list[str] = []
    source_host = _normalized_host(source_url)
    for item in parser.links:
        href = str(item.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        try:
            candidate_url = normalize_candidate_url(urljoin(source_url, href))
        except ValueError:
            continue
        if _normalized_host(candidate_url) != source_host:
            continue
        if candidate_url == source_url or _is_listing_candidate_url(candidate_url):
            continue
        if not _looks_like_discovery_article(candidate_url):
            continue
        title = _clean_candidate_title(str(item.get("text") or ""))
        if _contains_minor_signal(title) or _score_candidate(candidate_url, title, source_url) < 15:
            continue
        if candidate_url not in article_urls:
            article_urls.append(candidate_url)
    if len(article_urls) < 3:
        return {
            "status": "rejected", "url": source_url,
            "reason": f"実記事URLを3件以上確認できません（{len(article_urls)}件）",
            "checked_at": checked_at,
        }
    media_count = 0
    media_probe_url = ""
    for article_url in article_urls[:2]:
        try:
            article_document = fetcher(article_url)
        except Exception:
            continue
        article_text = re.sub(r"<[^>]+>", " ", article_document[:200_000])
        if _contains_minor_signal(article_text):
            continue
        count = _html_media_count(article_document)
        if count > media_count:
            media_count = count
            media_probe_url = article_url
    if media_count < 2:
        return {
            "status": "rejected", "url": source_url,
            "reason": "実記事から画像・動画を2点以上確認できません", "checked_at": checked_at,
        }
    score = min(100, 35 + len(article_urls) * 4 + media_count * 3 + len(adult_terms) * 4)
    name = page_title or search_title or source_host
    name = re.split(r"[|｜–—]", name, maxsplit=1)[0].strip()[:80] or source_host
    return {
        "status": "accepted",
        "url": source_url,
        "name": name,
        "score": score,
        "reason": f"実記事{len(article_urls)}件・素材{media_count}点を確認",
        "sample_url": media_probe_url,
        "checked_at": checked_at,
    }


def discover_new_sources(
    site_root: Path,
    *,
    now: datetime | None = None,
    force: bool = False,
    search_results: list[dict[str, str]] | None = None,
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(JST)).astimezone(JST)
    status = source_discovery_status(site_root, current)
    if not force and not status["due"]:
        return {"ran": False, "added": [], "checked": 0, "rejected": 0, **status}
    settings = load_automation_settings(site_root)
    limit = int(settings.get("source_discovery_max_additions") or 2)
    load_page = fetcher or _fetch_text
    rows = list(search_results or [])
    search_errors: list[str] = []
    if search_results is None:
        for query in SOURCE_DISCOVERY_QUERIES:
            try:
                rows.extend(_search_source_results(query))
            except Exception as exc:
                search_errors.append(f"{query}: {str(exc)[:120]}")
        for hub_url in SOURCE_DISCOVERY_HUB_URLS:
            try:
                rows.extend(_source_hub_results(hub_url, load_page))
            except Exception as exc:
                search_errors.append(f"{hub_url}: {str(exc)[:120]}")
    existing_hosts = {
        _normalized_host(str(source.get("url") or ""))
        for source in list_sources(site_root)
    }
    seen_roots: set[str] = set()
    checked_rows: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    for row in rows:
        try:
            root_url = _source_home_url(str(row.get("url") or ""))
        except ValueError:
            continue
        host = _normalized_host(root_url)
        if not host or root_url in seen_roots:
            continue
        seen_roots.add(root_url)
        if host in existing_hosts:
            continue
        probe = _probe_source_candidate(root_url, str(row.get("title") or ""), load_page)
        checked_rows.append(probe)
        if probe.get("status") != "accepted":
            continue
        source = add_source(
            site_root,
            str(probe.get("name") or host),
            str(probe["url"]),
            origin="automatic",
            discovery_score=int(probe.get("score") or 0),
            discovery_note=str(probe.get("reason") or ""),
        )
        source["discovered_at"] = current.isoformat(timespec="seconds")
        sources = list_sources(site_root)
        for item in sources:
            if item.get("source_id") == source.get("source_id"):
                item.update(source)
                break
        save_sources(site_root, sources)
        added.append(source)
        existing_hosts.add(host)
        probe["status"] = "added"
        probe["source_id"] = source.get("source_id", "")
        if len(added) >= limit:
            break
    source_ids = list(settings.get("continuous_source_ids") or [])
    if source_ids:
        source_ids.extend(str(item.get("source_id") or "") for item in added)
        settings["continuous_source_ids"] = list(dict.fromkeys(value for value in source_ids if value))
    settings["source_discovery_last_run_at"] = current.isoformat(timespec="seconds")
    save_automation_settings(site_root, settings)
    history = list_source_discovery_log(site_root)
    history.extend(checked_rows)
    if search_errors:
        history.append({
            "status": "error",
            "url": "",
            "reason": " / ".join(search_errors)[:500],
            "checked_at": current.isoformat(timespec="seconds"),
        })
    _save_source_discovery_log(site_root, history)
    return {
        "ran": True,
        "added": added,
        "checked": len(checked_rows),
        "rejected": sum(1 for item in checked_rows if item.get("status") == "rejected"),
        "errors": search_errors,
        **source_discovery_status(site_root, current),
    }


def list_candidates(site_root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_candidates_path(site_root), [])
    candidates = [item for item in raw if isinstance(item, dict)]
    queue = _read_json(
        site_root / ".article-studio" / "chatgpt-primary-queue.json",
        [],
    )
    queue_by_url: dict[str, dict[str, Any]] = {}
    for index, request in enumerate(queue if isinstance(queue, list) else []):
        if not isinstance(request, dict):
            continue
        try:
            normalized = normalize_candidate_url(str(request.get("url") or ""))
        except ValueError:
            continue
        timestamp = str(
            request.get("completed_at")
            or request.get("sent_at")
            or request.get("created_at")
            or ""
        )
        previous = queue_by_url.get(normalized)
        previous_key = (
            str((previous or {}).get("_candidate_sync_timestamp") or ""),
            int((previous or {}).get("_candidate_sync_index") or -1),
        )
        if previous is not None and (timestamp, index) < previous_key:
            continue
        queue_by_url[normalized] = {
            **request,
            "_candidate_sync_timestamp": timestamp,
            "_candidate_sync_index": index,
        }
    articles = _read_json(site_root / "data" / "articles.json", [])
    article_by_url: dict[str, dict[str, Any]] = {}
    for article in articles if isinstance(articles, list) else []:
        if not isinstance(article, dict):
            continue
        try:
            normalized = normalize_candidate_url(str(article.get("source_url") or ""))
        except ValueError:
            continue
        article_by_url[normalized] = article
    sources_by_id = {
        str(source.get("source_id") or ""): source
        for source in list_sources(site_root)
        if str(source.get("source_id") or "")
    }
    changed = False
    for candidate in candidates:
        try:
            normalized = normalize_candidate_url(str(candidate.get("url") or ""))
        except ValueError:
            normalized = ""
        request = queue_by_url.get(normalized)
        request_status = str((request or {}).get("status") or "")
        synchronized_status = {
            "queued": "chatgpt_queued",
            "processing": "chatgpt_queued",
            "retry_wait": "chatgpt_queued",
            "completed": "drafted",
            "failed": "failed",
            "skipped_non_adult": "ignored",
            "stopped_stale": "ignored",
            "archived_duplicate": "drafted",
            "legacy_archived": "ignored",
        }.get(request_status)
        attempted_value = str(candidate.get("attempted_at") or "")
        stale_waiting = False
        try:
            attempted = datetime.fromisoformat(attempted_value)
            if attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=JST)
            stale_waiting = (
                datetime.now(JST) - attempted.astimezone(JST)
                >= timedelta(minutes=10)
            )
        except ValueError:
            pass
        if synchronized_status and candidate.get("status") != synchronized_status:
            candidate["status"] = synchronized_status
            attempted_at = str(
                (request or {}).get("completed_at")
                or (request or {}).get("sent_at")
                or (request or {}).get("created_at")
                or ""
            )
            if attempted_at:
                candidate["attempted_at"] = attempted_at
            draft_slug = str((request or {}).get("draft_slug") or "")
            if draft_slug:
                candidate["draft_slug"] = draft_slug
            last_error = str((request or {}).get("last_error") or "")
            if last_error and synchronized_status == "failed":
                candidate["last_error"] = last_error[:500]
            elif synchronized_status in {"drafted", "ignored"}:
                candidate.pop("last_error", None)
            changed = True
        elif (
            candidate.get("status") == "chatgpt_queued"
            and request is None
            and stale_waiting
        ):
            article = article_by_url.get(normalized)
            if article is not None:
                candidate["status"] = "drafted"
                slug = str(article.get("slug") or "")
                if slug:
                    candidate["draft_slug"] = slug
                candidate.pop("last_error", None)
            else:
                # Old builds could mark a candidate as queued before its
                # durable processing row was written. It is not a live wait.
                candidate["status"] = "ignored"
                candidate["filter_reason"] = "旧バージョンの待機表示を整理"
            changed = True
        if candidate.get("status") not in {"new", "chatgpt_queued"}:
            continue
        source = sources_by_id.get(str(candidate.get("source_id") or ""))
        source_url = str((source or {}).get("url") or "")
        candidate_url = str(candidate.get("url") or "")
        if source_url and not _is_article_candidate_for_source(candidate_url, source_url):
            candidate["status"] = "structure_filtered"
            candidate["filter_reason"] = "登録元の実記事URLではないため除外"
            changed = True
        elif _is_listing_candidate_url(candidate_url):
            candidate["status"] = "structure_filtered"
            candidate["filter_reason"] = "一覧・カテゴリ・ページ送りURL"
            changed = True
        elif _contains_minor_signal(str(candidate.get("title") or "")):
            candidate["status"] = "safety_filtered"
            candidate["filter_reason"] = "子ども・未成年を示す表現を検出"
            changed = True
    if changed:
        save_candidates(site_root, candidates)
    return candidates


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
    outcome_source_id = ""
    previous_status = ""
    for candidate in candidates:
        if normalize_candidate_url(str(candidate.get("url") or "")) == target:
            previous_status = str(candidate.get("status") or "")
            outcome_source_id = str(candidate.get("source_id") or "")
            candidate["status"] = status
            candidate["attempted_at"] = datetime.now(JST).isoformat(timespec="seconds")
            if error:
                candidate["last_error"] = error[:500]
            elif status == "drafted":
                candidate.pop("last_error", None)
            if slug:
                candidate["draft_slug"] = slug
    save_candidates(site_root, candidates)
    if status != previous_status:
        record_source_outcome(site_root, outcome_source_id, status)


def mark_candidates_status(
    site_root: Path,
    urls: list[str],
    status: str,
) -> int:
    targets: set[str] = set()
    for url in urls:
        try:
            targets.add(normalize_candidate_url(url))
        except ValueError:
            continue
    if not targets:
        return 0
    candidates = list_candidates(site_root)
    attempted_at = datetime.now(JST).isoformat(timespec="seconds")
    updated = 0
    for candidate in candidates:
        try:
            normalized = normalize_candidate_url(str(candidate.get("url") or ""))
        except ValueError:
            continue
        if normalized not in targets:
            continue
        candidate["status"] = status
        candidate["attempted_at"] = attempted_at
        if status in {"chatgpt_queued", "drafted"}:
            candidate.pop("last_error", None)
        updated += 1
    if updated:
        save_candidates(site_root, candidates)
    return updated


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
        if candidate.get("status") in {
            "chatgpt_queued",
            "drafted",
            "failed",
            "ignored",
        }:
            try:
                urls.add(normalize_candidate_url(str(candidate.get("url") or "")))
            except ValueError:
                pass
    # Candidate rows can be compacted or rebuilt, while processing history is
    # durable. Never feed a URL that already finished, failed, was skipped, or
    # was stopped back into the automatic crawl.
    queue = _read_json(
        site_root / ".article-studio" / "chatgpt-primary-queue.json",
        [],
    )
    for request in queue if isinstance(queue, list) else []:
        if not isinstance(request, dict):
            continue
        value = str(request.get("url") or "")
        if not value:
            continue
        try:
            urls.add(normalize_candidate_url(value))
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
        header_charset = response.headers.get_content_charset()
        data = response.read(2_000_000)
    head = data[:8192].decode("ascii", errors="ignore")
    meta_match = re.search(
        r"(?:charset\s*=\s*[\"']?\s*|content\s*=\s*[\"'][^\"']*charset\s*=\s*)([a-z0-9._-]+)",
        head,
        re.I,
    )
    declared_charset = meta_match.group(1) if meta_match else ""
    preferred = declared_charset or header_charset or "utf-8"
    try:
        decoded = data.decode(preferred, errors="replace")
    except LookupError:
        decoded = data.decode("utf-8", errors="replace")
    replacement_limit = max(2, len(decoded) // 5000)
    if decoded.count("\ufffd") <= replacement_limit:
        return decoded
    candidates: list[str] = []
    for charset in (declared_charset, header_charset, "utf-8", "cp932", "shift_jis", "euc_jp"):
        if charset and charset.lower() not in {item.lower() for item in candidates}:
            candidates.append(charset)
    decoded_candidates: list[str] = []
    for charset in candidates:
        try:
            decoded_candidates.append(data.decode(charset, errors="replace"))
        except LookupError:
            continue
    return min(decoded_candidates or [decoded], key=lambda value: value.count("\ufffd"))


def _score_candidate(url: str, title: str, source_url: str) -> int:
    """Return only a structural eligibility score.

    The final ordering is calculated from observations by _buzz_score().
    """
    haystack = f"{url} {title}".lower()
    compact_title = re.sub(r"\s+", " ", html.unescape(title)).strip().lower()
    if (
        compact_title in NAVIGATION_TITLES
        or re.fullmatch(r"\d+位以降はこちら", compact_title)
        or len(compact_title) < 4
    ):
        return -100
    if any(term.lower() in haystack for term in BLOCKED_TERMS) or _contains_minor_signal(title):
        return -100
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()
    if any(part in netloc for part in BAD_HOST_PARTS) or (netloc.endswith("google.com") and path.startswith("/maps")):
        return -100
    if parsed.path.lower().endswith(BAD_EXTENSIONS):
        return -100
    if _is_listing_candidate_url(url):
        return -20
    score = 10
    if urlparse(source_url).netloc == parsed.netloc:
        score += 8
    score += min(12, sum(3 for term in GOOD_TERMS if term.lower() in haystack))
    if re.search(r"/(archives|post|article|entry)[/-]?\d+", parsed.path.lower()) or re.search(r"\d{4,}", parsed.path):
        score += 12
    if len(title) >= 12:
        score += 4
    return score


def _is_listing_candidate_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    path = parsed.path.lower()
    normalized_path = "/" + path.strip("/") if path.strip("/") else "/"
    if re.fullmatch(
        r"/(?:weekly-|daily-|monthly-)?(?:ranking|rankings|popular|latest|recent|new)/?",
        normalized_path,
    ):
        return True
    if normalized_path in {"/archive", "/archives", "/search", "/feed", "/sitemap"}:
        return True
    if any(part in path for part in (
        "/tag/", "/category/", "/page/", "/author/", "/login", "/privacy",
    )):
        return True
    if re.search(
        r"/(?:archives/cat_\d+|blog-category-\d+)(?:\.html?)?(?:/|$)",
        path,
    ):
        return True
    query = parse_qs(parsed.query)
    if any(key in query for key in ("page", "paged", "category", "cat", "tag")):
        return True
    # A root WordPress URL such as ?p=448192 is an article. Small values are
    # archive pagination on the sites currently managed by this app.
    page_values = query.get("p") or []
    return path.rstrip("/") == "" and any(
        value.isdigit() and int(value) <= 100 for value in page_values
    )


def _normalized_host(value: str) -> str:
    return (urlparse(value).hostname or "").lower().removeprefix("www.")


def _is_article_candidate_for_source(url: str, source_url: str) -> bool:
    """Accept only a real article belonging to the selected information source."""
    try:
        candidate_host = _normalized_host(url)
        source_host = _normalized_host(source_url)
    except Exception:
        return False
    if not candidate_host or not source_host:
        return False
    if source_host == "dmm.co.jp" or source_host.endswith(".dmm.co.jp"):
        return is_fanza_product_url(url)
    if candidate_host != source_host:
        return False
    pattern = KNOWN_ARTICLE_PATH_PATTERNS.get(source_host)
    if pattern is not None:
        return bool(pattern.fullmatch(urlparse(url).path.lower()))
    return not _is_listing_candidate_url(url)


def _number_value(value: str) -> int:
    cleaned = value.replace(",", "").strip().lower()
    multiplier = 10_000 if cleaned.endswith("万") else 1
    if multiplier != 1:
        cleaned = cleaned[:-1]
    try:
        return max(0, int(float(cleaned) * multiplier))
    except ValueError:
        return 0


def _engagement_count(text: str) -> int:
    values = []
    for match in re.finditer(
        r"(\d[\d,.]*万?)\s*(?:件?コメント|comments?|views?|回(?:視聴|再生)?|"
        r"いいね|likes?|shares?|反応)",
        text,
        re.I,
    ):
        values.append(_number_value(match.group(1)))
    return max(values, default=0)


def _contains_minor_signal(value: str) -> bool:
    compact = re.sub(r"\s+", "", html.unescape(value)).lower()
    if re.search(r"(?:^|[^\d])(?:[0-9]|1[0-7])(?:歳|才)(?:[^\d]|$)", compact):
        return True
    if re.search(r"[\(（](?:[0-9]|1[0-7])[\)）]", compact):
        return True
    return bool(re.search(
        r"(?:中[1-3]|中学[1-3]?年?|女子中学生|男子中学生|"
        r"子連れ|子ども|こども|子供|乳幼児|幼児|園児|赤ちゃん|キッズ|未成年)",
        compact,
    ))


def _clean_candidate_title(value: str) -> str:
    title = re.sub(r"\s+", " ", html.unescape(value)).strip()[:240]
    # Some cards repeat the same title in image alt, heading and link text.
    for _ in range(2):
        duplicate_at = next(
            (
                index
                for index in range(8, min(140, len(title) // 2 + 1))
                if title[index:].lstrip().startswith(title[:index].rstrip())
            ),
            -1,
        )
        if duplicate_at < 0:
            break
        prefix = title[:duplicate_at].rstrip()
        repeated = title[duplicate_at:].lstrip()
        title = (prefix + " " + repeated[len(prefix):].lstrip()).strip()
    return title[:160].strip()


def _topic_text(value: str) -> str:
    text = html.unescape(value).lower()
    text = re.sub(r"【[^】]{1,12}】|\[[^\]]{1,12}\]", " ", text)
    text = re.sub(r"\b(?:画像|動画|まとめ|話題|速報|悲報|朗報)\b", " ", text)
    text = re.sub(r"[ｗw]{2,}", " ", text)
    text = re.sub(r"\d+\s*(?:コメント|件|views?|回)", " ", text, flags=re.I)
    return re.sub(r"[\W_]+", "", text)


def _same_topic(left: str, right: str) -> bool:
    a = _topic_text(left)
    b = _topic_text(right)
    if len(a) < 6 or len(b) < 6:
        return False
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 10 and shorter in longer:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.58


def _parse_observed_at(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone(JST) if parsed.tzinfo else parsed.replace(tzinfo=JST)
    except (TypeError, ValueError):
        return fallback


def _buzz_score(
    *,
    title: str,
    first_seen_at: datetime,
    observed_at: datetime,
    rank: int,
    appearances: int,
    engagement: int,
    engagement_delta: int,
    rank_improvement: int,
    cross_source_count: int,
    popular_context: bool,
    new_context: bool,
    sale_context: bool,
) -> tuple[int, list[str], float]:
    score = 10
    reasons: list[str] = []
    age_hours = max(0.0, (observed_at - first_seen_at).total_seconds() / 3600)
    if age_hours <= 6:
        score += 22
        reasons.append("発見6時間以内")
    elif age_hours <= 24:
        score += 16
        reasons.append("発見24時間以内")
    elif age_hours <= 72:
        score += 9
        reasons.append("発見3日以内")
    elif age_hours <= 168:
        score += 3
    else:
        score -= min(18, int((age_hours - 168) / 24))

    placement = max(0, 18 - max(0, rank - 1))
    score += placement
    if rank <= 5:
        reasons.append(f"掲載位置{rank}位")
    if popular_context:
        score += 20
        reasons.append("人気・急上昇欄")
    if new_context:
        score += 6
        reasons.append("新着欄")
    if sale_context:
        score += 24
        reasons.append("セール・割引欄")

    if appearances > 1:
        score += min(12, (appearances - 1) * 3)
        reasons.append(f"同一ページ内{appearances}回掲載")
    if engagement:
        score += min(20, round(math.log2(engagement + 1) * 3))
        reasons.append(f"公開反応数{engagement}")
    velocity = float(max(0, engagement_delta))
    if engagement_delta > 0:
        score += min(30, round(math.log2(engagement_delta + 1) * 8))
        reasons.append(f"前回から反応+{engagement_delta}")
    if rank_improvement > 0:
        score += min(15, rank_improvement * 2)
        velocity += rank_improvement * 0.5
        reasons.append(f"掲載順位+{rank_improvement}")
    if cross_source_count > 1:
        score += min(30, (cross_source_count - 1) * 12)
        reasons.append(f"{cross_source_count}情報源で同時話題")

    audience_matches = [
        term for term in GOOD_TERMS if term.lower() in title.lower()
    ]
    if audience_matches:
        score += min(16, len(audience_matches) * 4)
        reasons.append("読者向け: " + "・".join(audience_matches[:3]))
    return max(0, score), reasons, velocity


def discover_candidates(
    site_root: Path,
    per_source_limit: int = 12,
    source_ids: list[str] | None = None,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = (observed_at or datetime.now(JST)).astimezone(JST)
    now = current_time.isoformat(timespec="seconds")
    existing = _existing_urls(site_root)
    previous_candidates = list_candidates(site_root)
    candidate_by_url: dict[str, dict[str, Any]] = {}
    for candidate in previous_candidates:
        try:
            candidate_by_url[normalize_candidate_url(str(candidate.get("url") or ""))] = candidate
        except ValueError:
            continue
    raw_history = _read_json(_trend_history_path(site_root), {})
    history = raw_history if isinstance(raw_history, dict) else {}
    observed_rows: list[dict[str, Any]] = []
    rendered_link_cache: dict[str, list[dict[str, str]]] = {}
    sources = list_sources(site_root)
    selected_sources = set(source_ids or [])
    for source in sources:
        if not source.get("enabled", True):
            continue
        if selected_sources and str(source.get("source_id") or "") not in selected_sources:
            continue
        source_url = normalize_candidate_url(str(source.get("url") or ""))
        source_host = (urlparse(source_url).hostname or "").lower()
        is_dmm_catalog = (
            source_host == "dmm.co.jp" or source_host.endswith(".dmm.co.jp")
        ) and not is_fanza_product_url(source_url)
        if is_dmm_catalog:
            rendered_url = source_url
            try:
                if rendered_url not in rendered_link_cache:
                    from indanya_desktop.browser_capture import collect_rendered_links

                    rendered_link_cache[rendered_url] = collect_rendered_links(rendered_url)
                source_links = rendered_link_cache[rendered_url]
            except Exception:
                source_links = []
        else:
            try:
                text = _fetch_text(source_url)
            except (OSError, TimeoutError, urllib.error.URLError, ValueError):
                continue
            parser = _LinkParser()
            parser.feed(text)
            source_links = parser.links
        aggregated: dict[str, dict[str, Any]] = {}
        for position, link in enumerate(source_links, start=1):
            href = str(link.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            try:
                url = normalize_candidate_url(urljoin(source_url, href))
            except ValueError:
                continue
            candidate_host = (urlparse(url).hostname or "").lower()
            same_site = candidate_host.removeprefix("www.") == source_host.removeprefix("www.")
            if not is_dmm_catalog and not same_site:
                continue
            # DMM links found inside an ordinary source are usually ads. Only
            # a selected DMM/FANZA catalog source may produce FANZA products.
            if is_fanza_product_url(url) and not is_dmm_catalog:
                continue
            if is_dmm_catalog and not is_fanza_product_url(url):
                continue
            if url in existing or url == source_url:
                continue
            raw_link_text = str(link.get("text") or "").strip()
            title = _clean_candidate_title(
                raw_link_text
                or urlparse(url).path.rsplit("/", 1)[-1]
            )
            if not _is_article_candidate_for_source(url, source_url):
                continue
            if _score_candidate(url, title, source_url) < 15:
                continue
            context = str(link.get("context") or "")
            row = aggregated.setdefault(url, {
                "url": url,
                "title": title,
                "source_id": str(source.get("source_id") or ""),
                "source_name": str(source.get("name") or ""),
                "position": position,
                "appearances": 0,
                "contexts": [],
                "engagement": 0,
                "source_card_text": raw_link_text[:1000],
            })
            if len(title) > len(str(row["title"])):
                row["title"] = title
            if len(raw_link_text) > len(str(row.get("source_card_text") or "")):
                row["source_card_text"] = raw_link_text[:1000]
            row["position"] = min(int(row["position"]), position)
            row["appearances"] = int(row["appearances"]) + 1
            if context and context not in row["contexts"]:
                row["contexts"].append(context[:120])
            row["engagement"] = max(
                int(row["engagement"]),
                _engagement_count(f"{title} {context}"),
            )
        ranked = sorted(aggregated.values(), key=lambda item: int(item["position"]))
        for rank, row in enumerate(ranked[:max(1, per_source_limit)], start=1):
            row["rank"] = rank
            observed_rows.append(row)
        source["last_checked_at"] = now

    for row in observed_rows:
        sources_for_topic = {
            str(other.get("source_id") or "")
            for other in observed_rows
            if str(other.get("source_id") or "")
            and _same_topic(str(row.get("title") or ""), str(other.get("title") or ""))
        }
        row["cross_source_count"] = max(1, len(sources_for_topic))

    newly_discovered: list[dict[str, Any]] = []
    for row in observed_rows:
        url = str(row["url"])
        record = history.get(url) if isinstance(history.get(url), dict) else {}
        observations = [
            item for item in record.get("observations", [])
            if isinstance(item, dict)
        ][-23:]
        previous = observations[-1] if observations else {}
        previous_time = _parse_observed_at(previous.get("at"), current_time)
        elapsed_hours = max(
            1 / 60,
            (current_time - previous_time).total_seconds() / 3600,
        )
        engagement = int(row.get("engagement") or 0)
        engagement_delta = (
            max(0, engagement - int(previous.get("engagement") or 0))
            if previous
            else 0
        )
        rank_improvement = max(0, int(previous.get("rank") or 0) - int(row["rank"]))
        first_seen_at = _parse_observed_at(record.get("first_seen_at"), current_time)
        context_text = " ".join(str(value) for value in row.get("contexts", []))
        popular_context = any(
            term.lower() in context_text.lower() for term in POPULAR_CONTEXT_TERMS
        )
        new_context = any(
            term.lower() in context_text.lower() for term in NEW_CONTEXT_TERMS
        )
        sale_context = any(
            term.lower() in context_text.lower() for term in SALE_CONTEXT_TERMS
        )
        score, reasons, velocity = _buzz_score(
            title=str(row["title"]),
            first_seen_at=first_seen_at,
            observed_at=current_time,
            rank=int(row["rank"]),
            appearances=int(row["appearances"]),
            engagement=engagement,
            engagement_delta=engagement_delta,
            rank_improvement=rank_improvement,
            cross_source_count=int(row["cross_source_count"]),
            popular_context=popular_context,
            new_context=new_context,
            sale_context=sale_context,
        )
        observation = {
            "at": now,
            "source_id": row["source_id"],
            "rank": int(row["rank"]),
            "appearances": int(row["appearances"]),
            "engagement": engagement,
            "engagement_delta": engagement_delta,
            "velocity_per_hour": round(velocity / elapsed_hours, 3),
            "popular_context": popular_context,
            "new_context": new_context,
            "sale_context": sale_context,
        }
        observations.append(observation)
        history[url] = {
            "first_seen_at": record.get("first_seen_at") or now,
            "last_seen_at": now,
            "title": row["title"],
            "observations": observations,
        }
        existing_candidate = candidate_by_url.get(url)
        candidate = dict(existing_candidate or {})
        structural_score = 4
        if len(str(row.get("source_card_text") or "").strip()) >= 12:
            structural_score += 5
        if row.get("contexts"):
            structural_score += 3
        if int(row.get("appearances") or 0) > 1:
            structural_score += 2
        if int(row.get("cross_source_count") or 0) > 1:
            structural_score += 4
        if engagement > 0:
            structural_score += 3
        candidate.update({
            "candidate_id": candidate.get("candidate_id")
            or hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
            "url": url,
            "title": row["title"],
            "source_id": row["source_id"],
            "source_name": row["source_name"],
            "source_card_text": str(row.get("source_card_text") or "")[:1000],
            "score": score,
            "buzz_score": score,
            "structural_score": min(18, structural_score),
            "status": str(candidate.get("status") or "new"),
            "discovered_at": candidate.get("discovered_at") or now,
            "last_observed_at": now,
            "trend": {
                **observation,
                "cross_source_count": int(row["cross_source_count"]),
                "score_reasons": reasons,
            },
        })
        candidate["quality_eligibility"] = evaluate_candidate_quality(
            site_root,
            candidate,
        )
        candidate_by_url[url] = candidate
        if existing_candidate is None:
            newly_discovered.append(candidate)

    candidates = list(candidate_by_url.values())
    for candidate in candidates:
        if (
            candidate.get("status") == "new"
            and _contains_minor_signal(str(candidate.get("title") or ""))
        ):
            candidate["status"] = "safety_filtered"
            candidate["filter_reason"] = "実年齢または中学生を示す表現を検出"
    candidates.sort(
        key=lambda item: (
            item.get("status") == "new",
            int(item.get("buzz_score") or item.get("score") or 0),
            str(item.get("last_observed_at") or item.get("discovered_at") or ""),
        ),
        reverse=True,
    )
    history_items = sorted(
        history.items(),
        key=lambda pair: str((pair[1] or {}).get("last_seen_at") or ""),
        reverse=True,
    )[:1500]
    save_sources(site_root, sources)
    save_candidates(site_root, candidates[:500])
    _write_json(_trend_history_path(site_root), dict(history_items))
    return newly_discovered
