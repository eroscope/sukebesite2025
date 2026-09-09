from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

from article_studio import JST
from indanya_desktop.browser_capture import (
    require_x_page_account,
    x_browser_profile_path,
    x_login_ready,
)


ProgressCallback = Callable[[int, str], None]
SHADOWBAN_CHECKER_URL = (
    "https://socialcal-media-proxy.jan-orsula1.workers.dev/twitter-shadowban"
)
_ACCOUNT_WARNING_MARKERS = (
    "account is suspended",
    "account suspended",
    "temporarily limited",
    "temporarily locked",
    "unusual activity",
    "アカウントは凍結",
    "アカウントが凍結",
    "一時的に制限",
    "一時的にロック",
    "不審な操作",
)


def _root(site_root: Path) -> Path:
    result = site_root / ".article-studio"
    result.mkdir(parents=True, exist_ok=True)
    return result


def _state_path(site_root: Path) -> Path:
    return _root(site_root) / "x-account-health.json"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _clean_handle(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", str(value or "")).casefold()[:15]


def _as_jst(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def load_x_health_state(
    site_root: Path,
    account_handle: Any = "",
) -> dict[str, Any]:
    raw = _read_json(_state_path(site_root), {})
    raw = raw if isinstance(raw, dict) else {}
    requested = _clean_handle(account_handle)
    stored = _clean_handle(raw.get("account_handle"))
    if requested and stored and requested != stored:
        raw = {}
        stored = ""
    history = [
        dict(item) for item in (raw.get("history") or [])
        if isinstance(item, dict)
    ][-90:]
    return {
        "version": 1,
        "account_handle": requested or stored,
        "status": str(raw.get("status") or "never"),
        "classification": str(raw.get("classification") or "unknown"),
        "risk_level": max(0, min(3, int(raw.get("risk_level") or 0))),
        "healthy_streak": max(0, int(raw.get("healthy_streak") or 0)),
        "caution_streak": max(0, int(raw.get("caution_streak") or 0)),
        "recovery_streak": max(0, int(raw.get("recovery_streak") or 0)),
        "last_checked_at": str(raw.get("last_checked_at") or ""),
        "next_check_at": str(raw.get("next_check_at") or ""),
        "last_error": str(raw.get("last_error") or ""),
        "external": dict(raw.get("external") or {}),
        "first_party": dict(raw.get("first_party") or {}),
        "activity": dict(raw.get("activity") or {}),
        "inferred_causes": [
            str(value) for value in (raw.get("inferred_causes") or []) if value
        ][:6],
        "effective_limits": dict(raw.get("effective_limits") or {}),
        "history": history,
    }


def save_x_health_state(site_root: Path, state: dict[str, Any]) -> None:
    _write_json(_state_path(site_root), state)


def apply_x_health_limits(
    settings: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Apply learned safety limits without overwriting the user's base settings."""
    result = dict(settings)
    if not bool(settings.get("adaptive_pacing_enabled", True)):
        return result
    level = max(0, min(3, int(state.get("risk_level") or 0)))
    healthy_streak = max(0, int(state.get("healthy_streak") or 0))
    configured_posts = max(1, int(settings.get("daily_post_limit") or 1))
    configured_replies = max(0, int(settings.get("reply_daily_limit") or 0))
    configured_follows = max(0, int(settings.get("follow_daily_limit") or 0))
    if level == 0:
        posts = configured_posts
        replies = configured_replies
        # Increase targeted follows only after repeated clean visibility checks.
        # A caution/restriction resets the streak, so the next trial starts low again.
        if healthy_streak >= 28:
            learned_follow_limit = 5
            learned_follow_interval = 4
        elif healthy_streak >= 14:
            learned_follow_limit = 4
            learned_follow_interval = 5
        elif healthy_streak >= 6:
            learned_follow_limit = 3
            learned_follow_interval = 6
        else:
            learned_follow_limit = min(configured_follows, 2)
            learned_follow_interval = 6
        follows = min(5, max(configured_follows, learned_follow_limit))
        global_limit = int(settings.get("global_daily_action_limit") or 8)
        minimum_interval = int(settings.get("global_min_interval_minutes") or 90)
        reply_interval = int(settings.get("reply_min_interval_minutes") or 360)
        configured_follow_interval = int(
            settings.get("follow_min_interval_hours") or 6
        )
        follow_interval = (
            min(configured_follow_interval, learned_follow_interval)
            if healthy_streak >= 6
            else configured_follow_interval
        )
    elif level == 1:
        posts = min(configured_posts, 3)
        replies = min(configured_replies, 1)
        follows = min(configured_follows, 1)
        global_limit = min(int(settings.get("global_daily_action_limit") or 8), 5)
        minimum_interval = max(int(settings.get("global_min_interval_minutes") or 90), 180)
        reply_interval = max(int(settings.get("reply_min_interval_minutes") or 360), 720)
        follow_interval = max(int(settings.get("follow_min_interval_hours") or 6), 12)
    elif level == 2:
        posts = min(configured_posts, 1)
        replies = 0
        follows = 0
        global_limit = 1
        minimum_interval = 720
        reply_interval = 1440
        follow_interval = 24
    else:
        posts = 0
        replies = 0
        follows = 0
        global_limit = 0
        minimum_interval = 1440
        reply_interval = 1440
        follow_interval = 48
    result.update({
        "daily_post_limit": posts,
        "reply_daily_limit": replies,
        "follow_daily_limit": follows,
        "global_daily_action_limit": global_limit,
        "global_min_interval_minutes": minimum_interval,
        "reply_min_interval_minutes": reply_interval,
        "follow_min_interval_hours": follow_interval,
        "health_risk_level": level,
        "health_classification": str(state.get("classification") or "unknown"),
    })
    return result


def effective_x_settings(site_root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    state = load_x_health_state(site_root, settings.get("account_handle"))
    return apply_x_health_limits(settings, state)


def x_health_schedule_status(
    site_root: Path,
    settings: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(JST)).astimezone(JST)
    state = load_x_health_state(site_root, settings.get("account_handle"))
    interval = max(12, min(48, int(settings.get("health_check_interval_hours") or 12)))
    last_checked = _as_jst(state.get("last_checked_at"))
    next_at = last_checked + timedelta(hours=interval) if last_checked else current
    enabled = bool(settings.get("health_check_enabled", True))
    return {
        **state,
        "enabled": enabled,
        "due": bool(enabled and current >= next_at),
        "next_at": next_at.isoformat(timespec="seconds"),
        "interval_hours": interval,
    }


def _metric_number(value: Any) -> int:
    text = str(value or "").replace(",", "").strip().casefold()
    match = re.search(r"(\d+(?:\.\d+)?)\s*([km万]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2)
    multiplier = {"k": 1_000, "m": 1_000_000, "万": 10_000}.get(unit, 1)
    return int(number * multiplier)


def _tweet_views(tweet: Any) -> int:
    selectors = (
        'a[href$="/analytics"]',
        'a[aria-label*="view" i]',
        'a[aria-label*="表示"]',
    )
    for selector in selectors:
        locator = tweet.locator(selector)
        if not locator.count():
            continue
        try:
            label = locator.first.get_attribute("aria-label") or locator.first.inner_text()
        except Exception:
            continue
        value = _metric_number(label)
        if value:
            return value
    return 0


def _status_rows(page: Any, handle: str, scrolls: int = 2) -> dict[str, int]:
    rows: dict[str, int] = {}
    pattern = re.compile(rf"^/{re.escape(handle)}/status/(\d+)", re.I)
    for _ in range(max(1, scrolls)):
        tweets = page.locator('article[data-testid="tweet"]')
        for index in range(tweets.count()):
            tweet = tweets.nth(index)
            links = tweet.locator('a[href*="/status/"]')
            status_id = ""
            for link_index in range(links.count()):
                href = str(links.nth(link_index).get_attribute("href") or "")
                match = pattern.match(href)
                if match:
                    status_id = match.group(1)
                    break
            if status_id:
                rows[status_id] = max(rows.get(status_id, 0), _tweet_views(tweet))
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(700)
    return rows


def _first_party_visibility(handle: str) -> dict[str, Any]:
    if not x_login_ready():
        raise RuntimeError("Xのログイン情報がありません")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(x_browser_profile_path()),
            channel="chrome",
            headless=True,
            viewport={"width": 1365, "height": 900},
            locale="ja-JP",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                f"https://x.com/{handle}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.wait_for_timeout(2500)
            active_handle = require_x_page_account(page, handle)
            body = str(page.locator("body").inner_text() or "")
            lowered = body.casefold()
            warning = next(
                (marker for marker in _ACCOUNT_WARNING_MARKERS if marker in lowered),
                "",
            )
            profile_rows = _status_rows(page, handle, 3)
            query = quote(f"from:{handle} -filter:replies")
            page.goto(
                f"https://x.com/search?q={query}&src=typed_query&f=live",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.wait_for_timeout(2500)
            search_body = str(page.locator("body").inner_text() or "").casefold()
            search_available = not any(
                marker in search_body
                for marker in (
                    "something went wrong",
                    "try reloading",
                    "問題が発生しました",
                    "再読み込みしてください",
                    "rate limit",
                )
            )
            search_rows = _status_rows(page, handle, 3) if search_available else {}
            profile_ids = set(profile_rows)
            found = len(profile_ids.intersection(search_rows))
            ratio = round(found / len(profile_ids), 3) if profile_ids else None
            views = sorted(value for value in profile_rows.values() if value > 0)
            median_views = views[len(views) // 2] if views else 0
            return {
                "active_handle": active_handle,
                "profile_accessible": not bool(warning),
                "account_warning": warning,
                "profile_post_count": len(profile_rows),
                "search_post_count": len(search_rows),
                "profile_posts_found_in_search": found,
                "search_ratio": ratio,
                "search_available": search_available,
                "median_visible_views": median_views,
            }
        finally:
            context.close()


def _external_shadowban_check(handle: str) -> dict[str, Any]:
    payload = json.dumps({"handle": handle}).encode("utf-8")
    request = Request(
        SHADOWBAN_CHECKER_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "IndanyaStudio/1.0 account-health-monitor",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "error": str(exc)[:300], "checks": {}}
    checks: dict[str, str] = {}
    for key, value in dict(result.get("checks") or {}).items():
        raw_status = value.get("status") if isinstance(value, dict) else value
        status = str(raw_status or "unknown").casefold()
        checks[str(key)] = status if status in {"ok", "banned", "unknown"} else "unknown"
    banned = sorted(key for key, value in checks.items() if value == "banned")
    verified = sum(1 for value in checks.values() if value in {"ok", "banned"})
    return {
        "status": "banned" if banned else ("clean" if verified else "unknown"),
        "checks": checks,
        "banned_signals": banned,
        "verified_signals": verified,
        "checked_at": str(result.get("checkedAt") or ""),
        "cached": bool(result.get("cached", False)),
        "error": str(result.get("error") or "")[:300],
    }


def _activity_snapshot(site_root: Path, handle: str, now: datetime) -> dict[str, Any]:
    rows = _read_json(_root(site_root) / "x-posting-queue.json", [])
    rows = rows if isinstance(rows, list) else []
    since = now - timedelta(hours=24)
    posts = replies = thread_steps = 0
    texts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        owner = _clean_handle(row.get("account_handle"))
        if owner and owner != handle:
            continue
        stamp = next(
            (
                parsed for key in (
                    "reply_completed_at",
                    "posted_at",
                    "scheduled_for",
                    "scheduled_at",
                )
                if (parsed := _as_jst(row.get(key))) is not None
            ),
            None,
        )
        if stamp is None or not (since <= stamp <= now):
            continue
        mode = str(row.get("delivery_mode") or "post")
        if mode == "reply":
            replies += 1
        elif mode == "thread":
            count = max(1, len(row.get("thread_post_urls") or []))
            thread_steps += count
            posts += count
        else:
            posts += 1
        text = re.sub(r"https?://\S+", "", str(row.get("post_text") or ""))
        text = re.sub(r"\s+", " ", text).strip().casefold()
        if text:
            texts.append(text)
    growth = _read_json(_root(site_root) / "x-growth-accounts.json", {})
    history = growth.get("follow_history") if isinstance(growth, dict) else []
    follows = 0
    for item in history or []:
        if not isinstance(item, dict) or str(item.get("result") or "") != "followed":
            continue
        owner = _clean_handle(item.get("account_handle"))
        if owner and owner != handle:
            continue
        stamp = _as_jst(item.get("attempted_at"))
        if stamp is not None and since <= stamp <= now:
            follows += 1
    duplicates = len(texts) - len(set(texts))
    duplicate_ratio = round(duplicates / len(texts), 3) if texts else 0.0
    total = posts + replies + follows
    return {
        "posts_24h": posts,
        "replies_24h": replies,
        "thread_steps_24h": thread_steps,
        "follows_24h": follows,
        "total_actions_24h": total,
        "duplicate_text_ratio": duplicate_ratio,
    }


def _classify_health(
    external: dict[str, Any],
    first_party: dict[str, Any],
) -> str:
    if external.get("banned_signals") or first_party.get("account_warning"):
        return "restricted"
    if not first_party.get("profile_accessible", False):
        return "restricted"
    profile_count = int(first_party.get("profile_post_count") or 0)
    search_available = bool(first_party.get("search_available", False))
    ratio = first_party.get("search_ratio")
    if profile_count >= 2 and search_available and ratio is not None and float(ratio) < 0.5:
        return "caution"
    if profile_count and (not search_available or ratio is None):
        return "unknown"
    if profile_count or external.get("status") == "clean":
        return "healthy"
    return "unknown"


def _infer_causes(activity: dict[str, Any], classification: str) -> list[str]:
    if classification not in {"caution", "restricted"}:
        return []
    causes: list[str] = []
    if float(activity.get("duplicate_text_ratio") or 0) >= 0.25:
        causes.append("似た文面の重複")
    if int(activity.get("replies_24h") or 0) >= 2:
        causes.append("募集リプの密度")
    if int(activity.get("follows_24h") or 0) >= 3:
        causes.append("フォローの密度")
    if int(activity.get("total_actions_24h") or 0) >= 7:
        causes.append("24時間の総操作数")
    return causes or ["検索表示または投稿内容の品質判定"]


def _transition_state(previous: dict[str, Any], classification: str) -> dict[str, int]:
    risk = max(0, min(3, int(previous.get("risk_level") or 0)))
    healthy = int(previous.get("healthy_streak") or 0)
    caution = int(previous.get("caution_streak") or 0)
    recovery = int(previous.get("recovery_streak") or 0)
    if classification == "restricted":
        return {
            "risk_level": min(3, max(2, risk + 1)),
            "healthy_streak": 0,
            "caution_streak": caution + 1,
            "recovery_streak": 0,
        }
    if classification == "caution":
        caution += 1
        return {
            "risk_level": min(2, max(1, risk + (1 if caution >= 2 else 0))),
            "healthy_streak": 0,
            "caution_streak": caution,
            "recovery_streak": 0,
        }
    if classification == "healthy":
        healthy += 1
        recovery += 1
        if risk and recovery >= 3:
            risk -= 1
            recovery = 0
        return {
            "risk_level": risk,
            "healthy_streak": healthy,
            "caution_streak": 0,
            "recovery_streak": recovery,
        }
    return {
        "risk_level": risk,
        "healthy_streak": 0,
        "caution_streak": caution,
        "recovery_streak": recovery,
    }


def run_due_x_health_check(
    site_root: Path,
    settings: dict[str, Any],
    progress: ProgressCallback = lambda _value, _message: None,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    current = (now or datetime.now(JST)).astimezone(JST)
    handle = _clean_handle(settings.get("account_handle"))
    schedule = x_health_schedule_status(site_root, settings, current)
    if not force and not schedule.get("due"):
        return {"result": "not_due", **schedule}
    progress(10, "Xアカウントの公開状態を確認しています")
    previous = load_x_health_state(site_root, handle)
    external = _external_shadowban_check(handle)
    progress(40, "外部チェッカーの結果を確認しました")
    first_party: dict[str, Any]
    error = ""
    try:
        first_party = _first_party_visibility(handle)
    except Exception as exc:
        error = str(exc)[:500]
        first_party = {
            "active_handle": "",
            "profile_accessible": False if "ログイン先が違います" in error else True,
            "account_warning": "account_mismatch" if "ログイン先が違います" in error else "",
            "profile_post_count": 0,
            "search_post_count": 0,
            "profile_posts_found_in_search": 0,
            "search_ratio": None,
            "search_available": False,
            "median_visible_views": 0,
        }
    progress(75, "X内のプロフィールと検索表示を照合しました")
    activity = _activity_snapshot(site_root, handle, current)
    classification = _classify_health(external, first_party)
    transition = _transition_state(previous, classification)
    causes = _infer_causes(activity, classification)
    interval = max(12, min(48, int(settings.get("health_check_interval_hours") or 12)))
    checked_at = current.isoformat(timespec="seconds")
    state = {
        **previous,
        **transition,
        "account_handle": handle,
        "status": "checked" if not error else "partial",
        "classification": classification,
        "last_checked_at": checked_at,
        "next_check_at": (current + timedelta(hours=interval)).isoformat(timespec="seconds"),
        "last_error": error,
        "external": external,
        "first_party": first_party,
        "activity": activity,
        "inferred_causes": causes,
    }
    effective = apply_x_health_limits(settings, state)
    state["effective_limits"] = {
        "daily_posts": int(effective.get("daily_post_limit") or 0),
        "daily_replies": int(effective.get("reply_daily_limit") or 0),
        "daily_follows": int(effective.get("follow_daily_limit") or 0),
        "daily_actions": int(effective.get("global_daily_action_limit") or 0),
        "minimum_interval_minutes": int(effective.get("global_min_interval_minutes") or 0),
        "follow_interval_hours": int(effective.get("follow_min_interval_hours") or 0),
    }
    history = list(previous.get("history") or [])
    history.append({
        "checked_at": checked_at,
        "classification": classification,
        "risk_level": int(state["risk_level"]),
        "external_status": str(external.get("status") or "unknown"),
        "search_ratio": first_party.get("search_ratio"),
        "activity": activity,
        "inferred_causes": causes,
        "effective_limits": state["effective_limits"],
    })
    state["history"] = history[-90:]
    save_x_health_state(site_root, state)
    progress(100, "Xアカウント診断と投稿ペースの更新が完了しました")
    return {"result": "checked", **state}
