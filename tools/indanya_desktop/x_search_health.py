from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from article_studio import JST


def search_page_state(url: str, text: str, result_count: int) -> str:
    """Do not interpret an X error screen as a successful empty search."""
    lowered = text.casefold()
    if "/i/flow/login" in url or "/account/access" in url:
        return "login_required"
    if any(value in lowered for value in (
        "rate limit exceeded", "you are rate limited", "利用制限に達", "アクセス回数の制限",
    )):
        return "rate_limited"
    if result_count > 0:
        return "results"
    if any(value in lowered for value in (
        "something went wrong", "try reloading", "問題が発生しました", "再読み込みしてください",
    )):
        return "load_error"
    if any(value in lowered for value in (
        "no results for", "検索結果はありません", "検索結果がありません", "一致する結果はありません",
    )):
        return "empty"
    return "unverified"


def load_reply_scan_health(site_root: Path) -> dict[str, Any]:
    path = Path(site_root) / ".article-studio" / "x-reply-scan-health.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_reply_scan_health(site_root: Path, report: dict[str, Any]) -> None:
    path = Path(site_root) / ".article-studio" / "x-reply-scan-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {**report, "checked_at": datetime.now(JST).isoformat(timespec="seconds")}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def reply_scan_summary(report: dict[str, Any]) -> str:
    if not report:
        return "検索の内訳は次回調査から記録"
    states = report.get("page_states") or {}
    failures = sum(int(states.get(key) or 0) for key in (
        "login_required", "rate_limited", "load_error", "unverified",
    ))
    summary = (
        f"読取{int(report.get('scanned_rows') or 0)}件"
        f"・条件一致{int(report.get('qualified_count') or 0)}件"
    )
    if failures or report.get("status") == "error":
        summary += f"・取得失敗/未確認{failures}画面"
    reasons = report.get("rejections") or {}
    labels = {
        "not_solicitation": "募集条件外", "not_topic": "対象外",
        "inactive": "期限/反応不足", "low_traffic": "流入条件未達",
        "unreadable": "本文読取失敗", "missing_url": "投稿URL不明",
        "invalid_time": "日時不明", "self_or_blocked": "対象外アカウント",
        "promoted": "広告",
    }
    top = sorted(reasons.items(), key=lambda item: int(item[1]), reverse=True)[:3]
    for key, count in top:
        summary += f"・{labels.get(key, key)}{int(count)}件"
    return summary
