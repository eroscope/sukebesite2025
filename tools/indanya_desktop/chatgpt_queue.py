from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from article_studio import JST, _validate_source_url, list_drafts, load_draft_payload
from indanya_desktop.editorial_policy import (
    canonical_fanza_product_url,
    is_fanza_product_url,
)


CUSTOM_GPT_URL = (
    "https://chatgpt.com/g/"
    "g-6a5f857c00288191b126f61393683154-yin-tan-wu-zi-dong-ji-shi-zuo-cheng"
)


_QUEUE_LOCK = threading.RLock()

_RECOVERABLE_VALIDATION_ERRORS = (
    "レスと画像の対応検査が完了していません",
)

_VALIDATION_RECOVERY_VERSION = 2

_TRANSIENT_FAILURE_MARKERS = (
    "timeout", "timed out", "時間以内", "chatgpt", "添付", "入力欄",
    "アップロード", "browser", "target page", "signal source",
    "アクセスが拒否", "temporarily", "一時エラー",
)


def _is_rate_limited_error(message: str) -> bool:
    normalized = str(message or "").casefold()
    return "rate_limit" in normalized or "利用制限" in normalized


def _is_waitable_browser_error(message: str) -> bool:
    normalized = str(message or "").casefold()
    return any(marker in normalized for marker in (
        "target page", "browser has been closed", "launch_persistent_context",
    ))


def _queue_locked(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _QUEUE_LOCK:
            return function(*args, **kwargs)
    return wrapped


def _queue_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "chatgpt-primary-queue.json"


def _write_queue(site_root: Path, rows: list[dict[str, Any]]) -> None:
    path = _queue_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    last_error: OSError | None = None
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    temporary.unlink(missing_ok=True)
    if last_error is not None:
        raise last_error


def _append_activity(item: dict[str, Any], phase: str, message: str) -> None:
    events = item.get("events")
    if not isinstance(events, list):
        events = []
    event = {
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "phase": str(phase)[:40],
        "message": str(message)[:500],
    }
    if events and all(events[-1].get(key) == event[key] for key in ("phase", "message")):
        return
    events.append(event)
    item["events"] = events[-30:]


def list_chatgpt_requests(site_root: Path) -> list[dict[str, Any]]:
    with _QUEUE_LOCK:
        try:
            raw = json.loads(_queue_path(site_root).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]


def find_duplicate_drafts(site_root: Path, url: str) -> list[dict[str, str]]:
    validated = urldefrag(_validate_source_url(url))[0]
    normalized = canonical_fanza_product_url(validated) or validated
    matches: list[dict[str, str]] = []
    for draft in list_drafts(site_root):
        slug = str(draft.get("slug") or "")
        if not slug:
            continue
        try:
            payload = load_draft_payload(slug, site_root)
            source_url = urldefrag(
                _validate_source_url(str(payload.get("source_url") or ""))
            )[0]
            source_url = canonical_fanza_product_url(source_url) or source_url
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if source_url == normalized:
            matches.append({
                "slug": slug,
                "title": str(payload.get("title") or slug),
            })
    return matches


@_queue_locked
def enqueue_chatgpt_request(
    site_root: Path,
    url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validated = urldefrag(_validate_source_url(url))[0]
    fanza_product = is_fanza_product_url(validated)
    normalized = canonical_fanza_product_url(validated) if fanza_product else validated
    request_options = dict(options or {})
    if fanza_product:
        request_options["content_mode"] = "fanza_product"
        request_options["promotion_type"] = "affiliate"
    else:
        request_options.setdefault("content_mode", "auto")
        request_options.setdefault("promotion_type", "organic")
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    if request_options.get("automation_origin") == "crawl":
        # The old display grouped a run by exact seconds. A crawl crossing a
        # second boundary was therefore shown as two different runs.
        request_options.setdefault("automation_batch_id", now[:16])
    existing_drafts = [] if request_options.get("force_duplicate") else find_duplicate_drafts(
        site_root, normalized
    )
    if existing_drafts:
        existing = existing_drafts[0]
        request_id = hashlib.sha256(
            f"duplicate\n{normalized}\n{now}\n{len(rows)}".encode()
        ).hexdigest()[:16]
        item = {
            "request_id": request_id,
            "url": normalized,
            "options": request_options,
            "status": "archived_duplicate",
            "created_at": now,
            "sent_at": "",
            "completed_at": now,
            "draft_slug": existing["slug"],
            "attempt_count": 0,
            "last_error": "同じ元URLの記事が既にあるため、新規作成はしていません",
        }
        _append_activity(item, "duplicate", item["last_error"])
        _write_queue(site_root, [*rows, item][-500:])
        return item
    if not request_options.get("force_duplicate"):
        existing = next(
            (
                item
                for item in rows
                if item.get("url") == normalized
                and item.get("status") in {
                    "queued", "sent", "processing", "completed",
                }
            ),
            None,
        )
        if existing:
            return existing
    request_id = hashlib.sha256(
        f"{normalized}\n{now}\n{len(rows)}".encode()
    ).hexdigest()[:16]
    item = {
        "request_id": request_id,
        "url": normalized,
        "options": request_options,
        "status": "queued",
        "created_at": now,
        "sent_at": "",
        "completed_at": "",
        "draft_slug": "",
        "attempt_count": 0,
    }
    _append_activity(item, "queued", "記事作成の待機列へ追加しました")
    rows.append(item)
    _write_queue(site_root, rows[-500:])
    return item


@_queue_locked
def reconcile_chatgpt_requests(site_root: Path) -> list[dict[str, Any]]:
    rows = list_chatgpt_requests(site_root)
    if not rows:
        return rows
    draft_urls: dict[str, str] = {}
    for draft in list_drafts(site_root):
        slug = str(draft.get("slug") or "")
        if not slug:
            continue
        try:
            payload = load_draft_payload(slug, site_root)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        source_url = str(payload.get("source_url") or "")
        if source_url:
            try:
                source_url = urldefrag(_validate_source_url(source_url))[0]
            except ValueError:
                continue
            source_url = canonical_fanza_product_url(source_url) or source_url
            draft_urls[source_url] = slug
    changed = False
    now = datetime.now(JST).isoformat(timespec="seconds")
    completed_urls: set[str] = set()
    for item in rows:
        item_url = str(item.get("url") or "")
        if item.get("status") == "sent":
            item["status"] = "legacy_archived"
            item["completed_at"] = now
            item["last_error"] = "旧手動送信経路の待機データを整理しました"
            changed = True
            continue
        if item.get("status") == "processing":
            try:
                started = datetime.fromisoformat(str(item.get("sent_at") or ""))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=JST)
            except ValueError:
                started = datetime.now(JST) - timedelta(hours=1)
            if datetime.now(JST) - started.astimezone(JST) >= timedelta(minutes=45):
                item["status"] = "queued"
                item["sent_at"] = ""
                item["last_error"] = "中断された処理を自動で待機へ戻しました"
                changed = True
        options = item.get("options")
        if isinstance(options, dict) and options.get("force_duplicate"):
            continue
        canonical_item_url = canonical_fanza_product_url(item_url) or item_url
        if canonical_item_url != item_url:
            item["url"] = canonical_item_url
            changed = True
        slug = draft_urls.get(canonical_item_url)
        # Matching a source URL is not sufficient: an old published draft can
        # have the same URL.  Only recover this request when the saved payload
        # explicitly records its request id.
        draft_request_id = ""
        if slug:
            try:
                payload = load_draft_payload(slug, site_root)
                draft_request_id = str(payload.get("chatgpt_request_id") or "")
            except (OSError, ValueError, json.JSONDecodeError):
                draft_request_id = ""
        if slug and draft_request_id == str(item.get("request_id") or ""):
            if canonical_item_url in completed_urls:
                if item.get("status") != "archived_duplicate":
                    item["status"] = "archived_duplicate"
                    item["completed_at"] = now
                    item["draft_slug"] = slug
                    item["last_error"] = "同じ元URLの重複処理を統合しました"
                    changed = True
                continue
            completed_urls.add(canonical_item_url)
            if item.get("status") != "completed" or item.get("draft_slug") != slug:
                item["status"] = "completed"
                item["completed_at"] = now
                item["draft_slug"] = slug
            item["last_error"] = ""
            changed = True
        elif slug and item.get("status") == "completed" and not draft_request_id:
            # Migrate the former false-success records.  A real save happens
            # within seconds of its queue completion; a much older draft is a
            # pre-existing article that must be reported as a duplicate.
            generated_at = ""
            try:
                generated_at = str(load_draft_payload(slug, site_root).get("generated_at") or "")
                generated = datetime.fromisoformat(generated_at)
                completed = datetime.fromisoformat(str(item.get("completed_at") or ""))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=JST)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=JST)
            except (OSError, ValueError, json.JSONDecodeError):
                generated = completed = None
            if generated is not None and completed is not None and abs((completed - generated).total_seconds()) > 300:
                item["status"] = "archived_duplicate"
                item["last_error"] = "同じ元URLの既存記事を成功として表示していました。新規作成はしていません"
                _append_activity(item, "duplicate", item["last_error"])
                changed = True
        elif slug and item.get("status") in {"queued", "processing"}:
            item["status"] = "archived_duplicate"
            item["completed_at"] = now
            item["draft_slug"] = slug
            item["last_error"] = "同じ元URLの記事が既にあるため、新規作成はしていません"
            _append_activity(item, "duplicate", item["last_error"])
            changed = True
    if changed:
        _write_queue(site_root, rows)
    return rows


def pending_chatgpt_count(site_root: Path) -> int:
    return sum(
        item.get("status") in {"queued", "processing"}
        for item in list_chatgpt_requests(site_root)
    )


def latest_chatgpt_batch_summary(site_root: Path) -> dict[str, int]:
    rows = [
        item for item in list_chatgpt_requests(site_root)
        if isinstance(item.get("options"), dict)
        and item["options"].get("automation_origin") == "crawl"
        and item.get("created_at")
    ]
    if not rows:
        return {
            "total": 0, "completed": 0, "failed": 0,
            "skipped": 0, "pending": 0, "processed": 0,
        }
    def batch_id(item: dict[str, Any]) -> str:
        options = item.get("options") if isinstance(item.get("options"), dict) else {}
        # Legacy rows do not have an id. Group those by minute rather than an
        # exact second so an old multi-URL run remains one visible result.
        return str(options.get("automation_batch_id") or str(item.get("created_at") or "")[:16])

    latest_batch_id = max(batch_id(item) for item in rows)
    batch = [
        item for item in rows
        if batch_id(item) == latest_batch_id
    ]
    completed = sum(item.get("status") == "completed" for item in batch)
    failed = sum(item.get("status") == "failed" for item in batch)
    skipped = sum(item.get("status") == "skipped_non_adult" for item in batch)
    pending = sum(item.get("status") in {"queued", "processing"} for item in batch)
    return {
        "total": len(batch),
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "pending": pending,
        "processed": completed + failed + skipped,
    }


def queued_chatgpt_request_ids(site_root: Path, limit: int = 5) -> list[str]:
    now = datetime.now(JST)
    def ready(item: dict[str, Any]) -> bool:
        retry_after = str(item.get("retry_after") or "")
        if not retry_after:
            return True
        try:
            value = datetime.fromisoformat(retry_after)
            if value.tzinfo is None:
                value = value.replace(tzinfo=JST)
            return value.astimezone(JST) <= now
        except ValueError:
            return True
    return [
        str(item.get("request_id") or "")
        for item in reconcile_chatgpt_requests(site_root)
        if item.get("status") in {"queued", "processing"} and ready(item)
    ][:max(1, min(20, int(limit)))]


def next_chatgpt_retry_after(site_root: Path) -> datetime | None:
    """Return the earliest future retry time for a pending request."""
    now = datetime.now(JST)
    retry_times: list[datetime] = []
    for item in reconcile_chatgpt_requests(site_root):
        if item.get("status") not in {"queued", "processing"}:
            continue
        raw_value = str(item.get("retry_after") or "")
        if not raw_value:
            continue
        try:
            value = datetime.fromisoformat(raw_value)
        except ValueError:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=JST)
        value = value.astimezone(JST)
        if value > now:
            retry_times.append(value)
    return min(retry_times) if retry_times else None


@_queue_locked
def record_chatgpt_event(
    site_root: Path,
    request_id: str,
    phase: str,
    message: str,
) -> None:
    rows = list_chatgpt_requests(site_root)
    for item in rows:
        if str(item.get("request_id") or "") == request_id:
            _append_activity(item, phase, message)
            _write_queue(site_root, rows)
            return


def recent_chatgpt_activity(site_root: Path, limit: int = 40) -> list[dict[str, str]]:
    activity: list[dict[str, str]] = []
    for item in list_chatgpt_requests(site_root):
        request_id = str(item.get("request_id") or "")
        url = str(item.get("url") or "")
        events = item.get("events")
        if isinstance(events, list) and events:
            for event in events:
                if not isinstance(event, dict):
                    continue
                activity.append({
                    "at": str(event.get("at") or ""),
                    "phase": str(event.get("phase") or ""),
                    "message": str(event.get("message") or ""),
                    "request_id": request_id,
                    "url": url,
                })
            continue
        activity.append({
            "at": str(item.get("completed_at") or item.get("sent_at") or item.get("created_at") or ""),
            "phase": str(item.get("status") or "queued"),
            "message": str(item.get("last_error") or "待機列の既存データ"),
            "request_id": request_id,
            "url": url,
        })
    activity.sort(key=lambda event: event["at"], reverse=True)
    return activity[:max(1, min(100, int(limit)))]


def get_chatgpt_requests(
    site_root: Path,
    request_ids: list[str],
) -> list[dict[str, Any]]:
    targets = set(request_ids)
    return [
        item
        for item in reconcile_chatgpt_requests(site_root)
        if str(item.get("request_id") or "") in targets
    ]


@_queue_locked
def mark_chatgpt_processing(
    site_root: Path,
    request_id: str,
) -> None:
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    for item in rows:
        if str(item.get("request_id") or "") == request_id:
            item["status"] = "processing"
            item.pop("retry_after", None)
            item["sent_at"] = now
            item["last_error"] = ""
            item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
            _append_activity(item, "browser", "記事処理を開始しました")
            break
    _write_queue(site_root, rows)


@_queue_locked
def complete_chatgpt_request(
    site_root: Path,
    request_id: str,
    slug: str,
) -> None:
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    for item in rows:
        if str(item.get("request_id") or "") == request_id:
            item["status"] = "completed"
            item.pop("retry_after", None)
            item["completed_at"] = now
            item["draft_slug"] = slug
            item["last_error"] = ""
            _append_activity(item, "completed", "検査を通過し、公開待機へ保存しました")
            break
    _write_queue(site_root, rows)


@_queue_locked
def fail_chatgpt_request(
    site_root: Path,
    request_id: str,
    message: str,
) -> None:
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    for item in rows:
        if str(item.get("request_id") or "") == request_id:
            error = str(message)[:1000]
            item["last_error"] = error
            item.pop("retry_after", None)
            item["status"] = "failed"
            item["completed_at"] = now
            _append_activity(item, "failed", error)
            break
    _write_queue(site_root, rows)


@_queue_locked
def skip_chatgpt_request(
    site_root: Path,
    request_id: str,
    message: str,
) -> None:
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    for item in rows:
        if str(item.get("request_id") or "") == request_id:
            item["status"] = "skipped_non_adult"
            item["completed_at"] = now
            item["last_error"] = str(message)[:1000]
            _append_activity(item, "skipped", str(message))
            break
    _write_queue(site_root, rows)


@_queue_locked
def requeue_chatgpt_requests(site_root: Path, request_ids: list[str]) -> int:
    targets = set(request_ids)
    if not targets:
        return 0
    rows = list_chatgpt_requests(site_root)
    updated = 0
    for item in rows:
        if (
            item.get("request_id") in targets
            and item.get("status") in {"processing", "failed"}
        ):
            item["status"] = "queued"
            item["sent_at"] = ""
            item["completed_at"] = ""
            updated += 1
    if updated:
        _write_queue(site_root, rows)
    return updated


@_queue_locked
def stop_pending_chatgpt_requests(site_root: Path, message: str) -> int:
    """Stop stale queue entries instead of carrying them into a later crawl."""
    rows = list_chatgpt_requests(site_root)
    now = datetime.now(JST).isoformat(timespec="seconds")
    stopped = 0
    for item in rows:
        if item.get("status") not in {"queued", "processing"}:
            continue
        item["status"] = "stopped_stale"
        item["completed_at"] = now
        item["last_error"] = str(message)[:1000]
        item.pop("retry_after", None)
        _append_activity(item, "stopped", str(message))
        stopped += 1
    if stopped:
        _write_queue(site_root, rows)
    return stopped


@_queue_locked
def restore_recoverable_validation_failures(site_root: Path) -> int:
    """Retry once for each deployed fix to a local validation bug."""
    rows = list_chatgpt_requests(site_root)
    restored = 0
    for item in rows:
        error = str(item.get("last_error") or "")
        if (
            item.get("status") == "failed"
            and int(item.get("validation_recovery_version") or 0) < _VALIDATION_RECOVERY_VERSION
            and any(marker in error for marker in _RECOVERABLE_VALIDATION_ERRORS)
        ):
            item["status"] = "queued"
            item["sent_at"] = ""
            item["completed_at"] = ""
            item["last_error"] = ""
            item["validation_recovery_attempted"] = True
            item["validation_recovery_version"] = _VALIDATION_RECOVERY_VERSION
            _append_activity(item, "retry", "保存検査の修正を反映して再試行待ちへ戻しました")
            restored += 1
    if restored:
        _write_queue(site_root, rows)
    return restored


@_queue_locked
def defer_chatgpt_requests(
    site_root: Path,
    request_ids: list[str],
    minutes: int = 5,
    message: str = "ChatGPTの利用制限中です。数分後に自動再開します",
) -> int:
    targets = set(request_ids)
    if not targets:
        return 0
    rows = list_chatgpt_requests(site_root)
    retry_after = (
        datetime.now(JST) + timedelta(minutes=max(1, int(minutes)))
    ).isoformat(timespec="seconds")
    updated = 0
    for item in rows:
        if item.get("request_id") in targets and item.get("status") in {"queued", "processing"}:
            item["status"] = "queued"
            item["sent_at"] = ""
            item["completed_at"] = ""
            item["retry_after"] = retry_after
            item["last_error"] = message
            updated += 1
    if updated:
        _write_queue(site_root, rows)
    return updated


def defer_all_chatgpt_requests(
    site_root: Path,
    minutes: int = 5,
    message: str = "ChatGPTの利用制限中です。数分後に自動再開します",
) -> int:
    request_ids = [
        str(item.get("request_id") or "")
        for item in list_chatgpt_requests(site_root)
        if item.get("status") in {"queued", "processing"}
    ]
    return defer_chatgpt_requests(site_root, request_ids, minutes, message)


@_queue_locked
def restore_latest_rate_limited_batch(site_root: Path, minutes: int = 10) -> int:
    """Return only the newest crawl batch from a plan-limit failure to waiting."""
    rows = list_chatgpt_requests(site_root)
    crawl_rows = [
        item for item in rows
        if isinstance(item.get("options"), dict)
        and item["options"].get("automation_origin") == "crawl"
        and item.get("created_at")
    ]
    if not crawl_rows:
        return 0
    latest_created_at = max(str(item.get("created_at") or "") for item in crawl_rows)
    retry_after = (
        datetime.now(JST) + timedelta(minutes=max(1, int(minutes)))
    ).isoformat(timespec="seconds")
    restored = 0
    for item in rows:
        error = str(item.get("last_error") or "").casefold()
        if (
            str(item.get("created_at") or "") == latest_created_at
            and item.get("status") == "failed"
            and _is_rate_limited_error(error)
        ):
            item["status"] = "queued"
            item["sent_at"] = ""
            item["completed_at"] = ""
            item["retry_after"] = retry_after
            item["last_error"] = "ChatGPTの利用制限のため待機中です。解除後に1件ずつ再開します"
            restored += 1
    if restored:
        _write_queue(site_root, rows)
    return restored
