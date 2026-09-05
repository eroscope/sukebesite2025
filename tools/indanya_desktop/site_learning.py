from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
LEARNING_VERSION = 2
BOOTSTRAP_VERSION = 3
BOOTSTRAP_DRAFT_MAX_BYTES = 2_000_000
_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(JST)


def _learning_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "site-learning.json"


def _read(site_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_learning_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["version"] = LEARNING_VERSION
    value.setdefault("bootstrap_version", 0)
    value.setdefault("sites", {})
    return value


def _write(site_root: Path, value: dict[str, Any]) -> None:
    path = _learning_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def site_host(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "unknown").lower().removeprefix("www.")


def route_template(url: str) -> str:
    parsed = urlparse(str(url or ""))
    path = unquote(parsed.path or "/").lower()
    raw_parts = [part for part in path.split("/") if part]
    if len(raw_parts) >= 4 and re.fullmatch(r"20\d{2}", raw_parts[0]) and all(
        re.fullmatch(r"\d{1,2}", part) for part in raw_parts[1:3]
    ):
        raw_parts = ["{date}", "{slug}", *raw_parts[4:]]
    parts: list[str] = []
    for part in raw_parts:
        part = re.sub(r"(?<![a-z])\d{3,}(?![a-z])", "{id}", part)
        part = re.sub(r"[a-f0-9]{12,}", "{key}", part)
        parts.append(part[:80])
    route = "/" + "/".join(parts)
    query_keys = sorted({
        key.lower()
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        if key
    })
    if query_keys:
        route += "?" + "&".join(query_keys[:8])
    return route[:240] or "/"


def classify_site_failure(message: str, stage: str = "") -> str:
    text = f"{stage} {message}".casefold()
    rules = (
        ("rate_limit", ("rate_limit", "利用制限", "usage limit")),
        ("not_adult", ("成人向けではない", "一般向け", "non-adult")),
        ("policy", ("ポリシー", "許可", "policy", "rights")),
        ("no_image", ("画像が見つかりません", "画像を取得できません", "使える画像", "thumbnail")),
        ("no_video", ("動画を取得できません", "動画が見つかりません", "video")),
        ("navigation", ("本編へのリンク", "gateway", "follow_url", "リダイレクト")),
        ("timeout", ("timeout", "timed out", "時間切れ")),
        ("network", ("http ", "接続", "network", "dns", "connection")),
        ("ai_validation", ("検査を通りません", "json", "schema", "必要なレス数")),
        ("invalid_media", ("image mismatch", "block ", "media", "素材id")),
        ("duplicate", ("重複", "already exists", "duplicate")),
        ("publish", ("公開", "push", "git", "github")),
    )
    for code, markers in rules:
        if any(marker in text for marker in markers):
            return code
    return "unknown"


def _new_recipe(route: str) -> dict[str, Any]:
    return {
        "route": route,
        "attempts": 0,
        "successes": 0,
        "historical_successes": 0,
        "historical_keys": [],
        "failures": 0,
        "consecutive_failures": 0,
        "strategies": {},
        "expected_images": 0.0,
        "expected_videos": 0.0,
        "expected_text_blocks": 0.0,
        "preferred_image_hosts": {},
        "preferred_video_hosts": {},
        "navigation_successes": 0,
        "average_navigation_hops": 0.0,
        "navigation_target_hosts": {},
        "navigation_patterns": {},
        "editorial_failure_types": {},
        "fast_path_successes": 0,
        "fast_path_failures": 0,
        "fast_path_disabled_until": "",
        "cooldown_until": "",
        "last_success_at": "",
        "last_failure_at": "",
        "last_failure_code": "",
        "last_error": "",
    }


def _new_site(host: str) -> dict[str, Any]:
    return {
        "host": host,
        "attempts": 0,
        "successes": 0,
        "historical_successes": 0,
        "failures": 0,
        "skipped": 0,
        "deferred": 0,
        "total_seconds": 0.0,
        "consecutive_successes": 0,
        "consecutive_failures": 0,
        "failure_types": {},
        "recipes": {},
        "recent": [],
        "updated_at": "",
    }


def _running_average(current: Any, count_before: int, value: int) -> float:
    current_value = float(current or 0)
    return round((current_value * count_before + max(0, value)) / (count_before + 1), 2)


def _media_host_counts(items: Any, selected_ids: set[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if selected_ids is not None and item_id not in selected_ids:
            continue
        host = site_host(str(item.get("url") or item.get("source_url") or ""))
        if host != "unknown":
            counts[host] = counts.get(host, 0) + 1
    return counts


def _merge_counts(target: dict[str, Any], additions: dict[str, int], limit: int = 12) -> None:
    for key, value in additions.items():
        target[key] = int(target.get(key) or 0) + int(value)
    ranked = sorted(target.items(), key=lambda item: (-int(item[1]), item[0]))[:limit]
    target.clear()
    target.update(ranked)


def record_site_outcome(
    site_root: Path,
    url: str,
    outcome: str,
    *,
    stage: str = "",
    message: str = "",
    strategy: str = "browser_full",
    elapsed_seconds: float = 0.0,
    source: dict[str, Any] | None = None,
    selected_image_ids: list[str] | None = None,
    selected_video_ids: list[str] | None = None,
    failure_code: str = "",
    navigation_trace: list[dict[str, Any]] | None = None,
    quality_passed: bool | None = None,
    historical: bool = False,
) -> dict[str, Any]:
    host = site_host(url)
    route = route_template(url)
    now = _now()
    with _LOCK:
        data = _read(site_root)
        sites = data.setdefault("sites", {})
        site = sites.setdefault(host, _new_site(host))
        recipes = site.setdefault("recipes", {})
        recipe = recipes.setdefault(route, _new_recipe(route))
        if historical:
            historical_key = hashlib.sha256(
                str(url or "").strip().encode("utf-8")
            ).hexdigest()[:20]
            historical_keys = recipe.setdefault("historical_keys", [])
            if historical_key in historical_keys:
                return get_site_plan(site_root, url, data=data)
            historical_keys.append(historical_key)
            recipe["historical_keys"] = historical_keys[-500:]
        if outcome == "success" and quality_passed is False:
            outcome = "deferred"
            stage = stage or "quality_review"
        resolved_failure_code = (
            str(failure_code or "").strip()
            or classify_site_failure(message, stage)
        )
        if outcome in {"success", "failure", "skipped"} and not historical:
            site["attempts"] = int(site.get("attempts") or 0) + 1
            recipe["attempts"] = int(recipe.get("attempts") or 0) + 1
        elif outcome == "deferred":
            site["deferred"] = int(site.get("deferred") or 0) + 1

        if outcome == "success":
            previous_successes = int(recipe.get("successes") or 0)
            if historical:
                site["historical_successes"] = int(site.get("historical_successes") or 0) + 1
                recipe["historical_successes"] = int(recipe.get("historical_successes") or 0) + 1
            else:
                site["successes"] = int(site.get("successes") or 0) + 1
                site["consecutive_successes"] = int(site.get("consecutive_successes") or 0) + 1
                site["consecutive_failures"] = 0
                site["total_seconds"] = round(
                    float(site.get("total_seconds") or 0) + max(0.0, elapsed_seconds),
                    2,
                )
            recipe["successes"] = previous_successes + 1
            recipe["consecutive_failures"] = 0
            recipe["cooldown_until"] = ""
            recipe["last_success_at"] = now.isoformat(timespec="seconds")
            strategies = recipe.setdefault("strategies", {})
            stats = strategies.setdefault(strategy, {"attempts": 0, "successes": 0, "total_seconds": 0.0})
            stats["attempts"] = int(stats.get("attempts") or 0) + 1
            stats["successes"] = int(stats.get("successes") or 0) + 1
            stats["total_seconds"] = round(float(stats.get("total_seconds") or 0) + max(0.0, elapsed_seconds), 2)
            if strategy == "semantic_fast":
                recipe["fast_path_successes"] = int(recipe.get("fast_path_successes") or 0) + 1
            source = source or {}
            source_chain = [
                str(value).strip()
                for value in (source.get("source_chain") or [])
                if str(value).strip()
            ]
            if len(source_chain) > 1:
                previous_navigation = int(recipe.get("navigation_successes") or 0)
                hop_count = len(source_chain) - 1
                recipe["navigation_successes"] = previous_navigation + 1
                recipe["average_navigation_hops"] = _running_average(
                    recipe.get("average_navigation_hops"),
                    previous_navigation,
                    hop_count,
                )
                final_host = site_host(source_chain[-1])
                if final_host != "unknown":
                    _merge_counts(
                        recipe.setdefault("navigation_target_hosts", {}),
                        {final_host: 1},
                    )
                trace = navigation_trace or source.get("navigation_trace") or []
                patterns = recipe.setdefault("navigation_patterns", {})
                for hop in trace:
                    if not isinstance(hop, dict):
                        continue
                    link_text = " ".join(
                        str(hop.get("followed_link_text") or "").split()
                    )[:160]
                    followed_url = str(hop.get("followed_url") or "")
                    if not followed_url:
                        continue
                    target_host = site_host(followed_url)
                    target_route = route_template(followed_url)
                    pattern_key = hashlib.sha256(
                        f"{link_text.casefold()}|{target_host}|{target_route}".encode("utf-8")
                    ).hexdigest()[:16]
                    pattern = patterns.setdefault(pattern_key, {
                        "link_text": link_text,
                        "target_host": target_host,
                        "target_route": target_route,
                        "count": 0,
                    })
                    pattern["count"] = int(pattern.get("count") or 0) + 1
                    pattern["last_at"] = now.isoformat(timespec="seconds")
                ranked_patterns = sorted(
                    patterns.items(),
                    key=lambda item: (-int(item[1].get("count") or 0), item[0]),
                )[:20]
                recipe["navigation_patterns"] = dict(ranked_patterns)
            image_ids = set(selected_image_ids or [])
            video_ids = set(selected_video_ids or [])
            image_count = len(image_ids) if image_ids else len(source.get("images") or [])
            video_count = len(video_ids) if video_ids else len(source.get("videos") or [])
            text_count = len(source.get("text_blocks") or source.get("excerpts") or [])
            recipe["expected_images"] = _running_average(recipe.get("expected_images"), previous_successes, image_count)
            recipe["expected_videos"] = _running_average(recipe.get("expected_videos"), previous_successes, video_count)
            recipe["expected_text_blocks"] = _running_average(recipe.get("expected_text_blocks"), previous_successes, text_count)
            _merge_counts(
                recipe.setdefault("preferred_image_hosts", {}),
                _media_host_counts(source.get("images"), image_ids or None),
            )
            _merge_counts(
                recipe.setdefault("preferred_video_hosts", {}),
                _media_host_counts(source.get("videos"), video_ids or None),
            )
        elif outcome == "failure":
            site["failures"] = int(site.get("failures") or 0) + 1
            site["consecutive_failures"] = int(site.get("consecutive_failures") or 0) + 1
            site["consecutive_successes"] = 0
            recipe["failures"] = int(recipe.get("failures") or 0) + 1
            recipe["consecutive_failures"] = int(recipe.get("consecutive_failures") or 0) + 1
            recipe["last_failure_at"] = now.isoformat(timespec="seconds")
            recipe["last_failure_code"] = resolved_failure_code
            recipe["last_error"] = str(message)[:500]
            failures = site.setdefault("failure_types", {})
            failure = failures.setdefault(resolved_failure_code, {"count": 0, "last_at": "", "last_message": "", "stage": ""})
            failure["count"] = int(failure.get("count") or 0) + 1
            failure["last_at"] = now.isoformat(timespec="seconds")
            failure["last_message"] = str(message)[:500]
            failure["stage"] = str(stage)[:60]
            strategies = recipe.setdefault("strategies", {})
            stats = strategies.setdefault(
                strategy,
                {"attempts": 0, "successes": 0, "failures": 0, "total_seconds": 0.0},
            )
            stats["attempts"] = int(stats.get("attempts") or 0) + 1
            stats["failures"] = int(stats.get("failures") or 0) + 1
            stats["total_seconds"] = round(
                float(stats.get("total_seconds") or 0) + max(0.0, elapsed_seconds),
                2,
            )
            if strategy == "semantic_fast" and stage == "material":
                recipe["fast_path_failures"] = int(recipe.get("fast_path_failures") or 0) + 1
                recipe["fast_path_disabled_until"] = (now + timedelta(days=1)).isoformat(timespec="seconds")
            if resolved_failure_code in {
                "wrong_source", "wrong_media", "wrong_person", "wrong_pr",
                "wrong_card_image", "missing_official_link", "missing_video",
            }:
                editorial = recipe.setdefault("editorial_failure_types", {})
                editorial[resolved_failure_code] = int(
                    editorial.get(resolved_failure_code) or 0
                ) + 1
            repeated = int(recipe.get("consecutive_failures") or 0)
            if repeated >= 2:
                # Keep continuous operation moving to other candidates, then retry
                # this URL shape soon with its adjusted extraction strategy.
                wait_minutes = min(15, 5 * (2 ** min(2, repeated - 2)))
                recipe["cooldown_until"] = (now + timedelta(minutes=wait_minutes)).isoformat(timespec="seconds")
        elif outcome == "skipped":
            site["skipped"] = int(site.get("skipped") or 0) + 1

        event = {
            "at": now.isoformat(timespec="seconds"),
            "route": route,
            "outcome": outcome,
            "stage": str(stage)[:60],
            "strategy": strategy,
            "failure_code": resolved_failure_code if outcome == "failure" else "",
            "message": str(message)[:300],
            "seconds": round(max(0.0, elapsed_seconds), 1),
            "historical": bool(historical),
        }
        if not historical:
            recent = site.setdefault("recent", [])
            recent.append(event)
            site["recent"] = recent[-30:]
        site["updated_at"] = now.isoformat(timespec="seconds")
        data["updated_at"] = site["updated_at"]
        _write(site_root, data)
        return get_site_plan(site_root, url, data=data)


def record_fast_path_probe(site_root: Path, url: str, success: bool, message: str = "") -> None:
    host = site_host(url)
    route = route_template(url)
    now = _now()
    with _LOCK:
        data = _read(site_root)
        site = data.setdefault("sites", {}).setdefault(host, _new_site(host))
        recipe = site.setdefault("recipes", {}).setdefault(route, _new_recipe(route))
        key = "fast_path_successes" if success else "fast_path_failures"
        recipe[key] = int(recipe.get(key) or 0) + 1
        if not success and int(recipe.get("fast_path_failures") or 0) >= 2:
            recipe["fast_path_disabled_until"] = (now + timedelta(days=3)).isoformat(timespec="seconds")
        recipe["last_fast_path_message"] = str(message)[:300]
        site["updated_at"] = now.isoformat(timespec="seconds")
        data["updated_at"] = site["updated_at"]
        _write(site_root, data)


def _future(value: Any, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JST)
        return parsed.astimezone(JST) > now
    except ValueError:
        return False


def get_site_plan(site_root: Path, url: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    host = site_host(url)
    route = route_template(url)
    now = _now()
    value = data if isinstance(data, dict) else _read(site_root)
    site = (value.get("sites") or {}).get(host, {})
    recipe = (site.get("recipes") or {}).get(route, {})
    successes = int(recipe.get("successes") or 0)
    failures = int(recipe.get("failures") or 0)
    expected_images = float(recipe.get("expected_images") or 0)
    expected_videos = float(recipe.get("expected_videos") or 0)
    fast_disabled = _future(recipe.get("fast_path_disabled_until"), now)
    hostname = host.casefold()
    is_fanza = hostname == "dmm.co.jp" or hostname.endswith(".dmm.co.jp") or hostname.endswith(".fanza.co.jp")
    if is_fanza:
        strategy = "fanza_official"
    elif expected_videos >= 0.5 or fast_disabled or recipe.get("last_failure_code") in {"no_image", "no_video", "invalid_media"}:
        strategy = "browser_full"
    elif int(recipe.get("fast_path_successes") or 0) >= 1:
        strategy = "semantic_fast"
    elif successes >= 1:
        strategy = "semantic_trial"
    elif recipe.get("last_failure_code") in {"timeout", "network"}:
        strategy = "semantic_trial"
    else:
        strategy = "browser_full"
    site_successes = int(site.get("successes") or 0)
    historical_successes = int(site.get("historical_successes") or 0)
    site_failures = int(site.get("failures") or 0)
    decided = site_successes + site_failures
    rate = round(site_successes * 100 / decided, 1) if decided else 0.0
    learned_successes = site_successes + historical_successes
    if learned_successes >= 5 and (not decided or rate >= 80):
        maturity = "安定"
    elif learned_successes >= 2:
        maturity = "習熟"
    elif learned_successes:
        maturity = "学習中"
    else:
        maturity = "未学習"
    return {
        "host": host,
        "route": route,
        "strategy": strategy,
        "maturity": maturity,
        "successes": successes,
        "failures": failures,
        "site_successes": site_successes,
        "historical_successes": historical_successes,
        "site_failures": site_failures,
        "success_rate": rate,
        "expected_images": expected_images,
        "expected_videos": expected_videos,
        "expected_text_blocks": float(recipe.get("expected_text_blocks") or 0),
        "preferred_image_hosts": list((recipe.get("preferred_image_hosts") or {}).keys()),
        "preferred_video_hosts": list((recipe.get("preferred_video_hosts") or {}).keys()),
        "navigation_successes": int(recipe.get("navigation_successes") or 0),
        "average_navigation_hops": float(recipe.get("average_navigation_hops") or 0),
        "navigation_target_hosts": list((recipe.get("navigation_target_hosts") or {}).keys()),
        "navigation_patterns": sorted(
            [dict(value) for value in (recipe.get("navigation_patterns") or {}).values()],
            key=lambda item: -int(item.get("count") or 0),
        )[:12],
        "editorial_failure_types": dict(recipe.get("editorial_failure_types") or {}),
        "last_failure_code": str(recipe.get("last_failure_code") or ""),
        "last_error": str(recipe.get("last_error") or ""),
        "cooldown_until": str(recipe.get("cooldown_until") or ""),
        "cooldown_active": _future(recipe.get("cooldown_until"), now),
    }


def can_attempt_site(site_root: Path, url: str) -> tuple[bool, str]:
    plan = get_site_plan(site_root, url)
    if plan["cooldown_active"]:
        return False, f"同じ形式の連続失敗を避けるため {plan['cooldown_until']} まで自動調整中"
    return True, ""


def prioritize_source_media(source: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    preferred_images = set(plan.get("preferred_image_hosts") or [])
    preferred_videos = set(plan.get("preferred_video_hosts") or [])

    def rank(item: dict[str, Any], preferred: set[str]) -> tuple[int, int]:
        host = site_host(str(item.get("url") or ""))
        score = int(item.get("source_score") or item.get("ai_relevance_score") or 0)
        return (1 if host in preferred else 0, score)

    result = dict(source)
    result["images"] = sorted(
        [item for item in (source.get("images") or []) if isinstance(item, dict)],
        key=lambda item: rank(item, preferred_images),
        reverse=True,
    )
    result["videos"] = sorted(
        [item for item in (source.get("videos") or []) if isinstance(item, dict)],
        key=lambda item: rank(item, preferred_videos),
        reverse=True,
    )
    result["site_learning"] = plan
    return result


def learning_prompt_context(plan: dict[str, Any]) -> str:
    if not plan.get("successes") and not plan.get("failures"):
        return ""
    image_hosts = "、".join(plan.get("preferred_image_hosts") or []) or "未確定"
    video_hosts = "、".join(plan.get("preferred_video_hosts") or []) or "未確定"
    navigation_hosts = "、".join(plan.get("navigation_target_hosts") or []) or "未確定"
    navigation_patterns = plan.get("navigation_patterns") or []
    navigation_examples = " / ".join(
        f"{item.get('link_text') or '(無題)'} → {item.get('target_host')}"
        for item in navigation_patterns[:3]
    )
    recovery_rules = {
        "no_image": "前回は本編画像不足。サムネイルだけで済ませず、本文に属する画像IDを必要数確認すること。",
        "no_video": "前回は動画を取りこぼした。video/sourceと公式プレイヤーを確認し、動画IDを静止画扱いしないこと。",
        "navigation": "前回は中継ページで止まった。本編ページへの導線と遷移後の題材を優先すること。",
        "timeout": "前回は時間切れ。重複説明を省き、確認済み素材だけで簡潔かつ完全なJSONを返すこと。",
        "network": "前回は通信失敗。今回取得できた実物だけを使い、欠けた情報を推測で埋めないこと。",
        "ai_validation": "前回は記事形式の検査失敗。指定JSON、レス数、タイトル条件を最後に再確認すること。",
        "invalid_media": "前回は素材対応の検査失敗。存在する素材IDだけを使い、同一素材の不要な重複配置を避けること。",
        "duplicate": "前回は重複判定。既存記事と同じ切り口や定型タイトルを避けること。",
    }
    recovery = recovery_rules.get(str(plan.get("last_failure_code") or ""), "")
    return (
        "\n\nサイト別学習メモ（過去の成功結果を補助情報として使い、今回の実物を優先すること）:\n"
        f"- URL型: {plan.get('route')}\n"
        f"- 過去: 成功{plan.get('successes', 0)}件 / 失敗{plan.get('failures', 0)}件\n"
        f"- 典型素材: 画像約{plan.get('expected_images', 0):.1f}枚 / 動画約{plan.get('expected_videos', 0):.1f}本\n"
        f"- 採用実績のある画像配信元: {image_hosts}\n"
        f"- 採用実績のある動画配信元: {video_hosts}\n"
        + (
            f"- このURL型は入口ページの実績あり: "
            f"平均{plan.get('average_navigation_hops', 0):.1f}段、最終サイト {navigation_hosts}\n"
            if plan.get("navigation_successes") else ""
        )
        + (
            f"- 成功済みの本編導線: {navigation_examples}\n"
            if navigation_examples else ""
        )
        +
        f"- 前回の失敗分類: {plan.get('last_failure_code') or 'なし'}\n"
        + (f"- 次回対策: {recovery}\n" if recovery else "")
        +
        "広告・ランキング・関連記事は、過去に採用実績があっても今回の記事本体と一致しなければ除外すること。"
    )


def list_site_learning(site_root: Path) -> list[dict[str, Any]]:
    with _LOCK:
        data = _read(site_root)
    rows: list[dict[str, Any]] = []
    for host, site in (data.get("sites") or {}).items():
        successes = int(site.get("successes") or 0)
        historical_successes = int(site.get("historical_successes") or 0)
        failures = int(site.get("failures") or 0)
        decided = successes + failures
        rate = round(successes * 100 / decided, 1) if decided else 0.0
        recipes = list((site.get("recipes") or {}).values())
        best = max(recipes, key=lambda item: (int(item.get("successes") or 0), -int(item.get("failures") or 0)), default={})
        total_seconds = float(site.get("total_seconds") or 0)
        average_seconds = round(total_seconds / successes, 1) if successes else 0.0
        plan = get_site_plan(site_root, f"https://{host}{best.get('route') or '/'}", data=data)
        rows.append({
            "host": host,
            "maturity": plan["maturity"],
            "successes": successes,
            "historical_successes": historical_successes,
            "failures": failures,
            "success_rate": rate,
            "average_seconds": average_seconds,
            "strategy": plan["strategy"],
            "route_count": len(recipes),
            "last_error": str(best.get("last_error") or ""),
            "updated_at": str(site.get("updated_at") or ""),
        })
    rows.sort(
        key=lambda item: (
            -(item["successes"] + item["historical_successes"]),
            item["host"],
        )
    )
    return rows


def bootstrap_site_learning(site_root: Path) -> int:
    with _LOCK:
        data = _read(site_root)
        if int(data.get("bootstrap_version") or 0) >= BOOTSTRAP_VERSION:
            return 0

    records: dict[str, dict[str, Any]] = {}
    queue_path = site_root / ".article-studio" / "chatgpt-primary-queue.json"
    try:
        queue_rows = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        queue_rows = []
    # Queue rows prove that an article existed, but they do not contain its
    # selected media. Treating those rows as zero-image/zero-video successes
    # poisoned the learned recipe. Catalog and draft records below are the
    # only historical observations precise enough to update media averages.

    catalog_path = site_root / "data" / "articles.json"
    try:
        catalog_rows = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalog_rows = []
    for row in catalog_rows if isinstance(catalog_rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("source_url") or "")
        if not urlparse(url).hostname:
            continue
        image_count = max(
            0,
            int(row.get("body_images_used", row.get("images_used")) or 0),
        )
        video_count = max(0, int(row.get("videos_used") or 0))
        if not video_count and str(row.get("category") or "") == "動画":
            video_count = 1
        records[url] = {
            "images": [{"id": f"historical-image-{index + 1}"} for index in range(image_count)],
            "videos": [{"id": f"historical-video-{index + 1}"} for index in range(video_count)],
            "text_blocks": [{} for _index in range(max(0, int(row.get("comments") or 0)))],
        }

    # Read only compact drafts. They add older site varieties and exact media
    # counts while avoiding the multi-gigabyte base64 draft library.
    draft_root = site_root / ".article-studio" / "drafts"
    draft_paths = (
        [
            path
            for path in draft_root.glob("*.json")
            if path.stat().st_size <= BOOTSTRAP_DRAFT_MAX_BYTES
        ]
        if draft_root.exists()
        else []
    )
    for path in draft_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        url = str(payload.get("source_url") or "")
        if not urlparse(url).hostname:
            continue
        records[url] = {
            "images": payload.get("images") or [],
            "videos": payload.get("videos") or [],
            "text_blocks": [block for block in (payload.get("blocks") or []) if isinstance(block, dict) and block.get("type") == "post"],
        }

    count = 0
    for url, source in records.items():
        record_site_outcome(
            site_root,
            url,
            "success",
            strategy="historical",
            source=source,
            historical=True,
        )
        count += 1
    with _LOCK:
        data = _read(site_root)
        data["bootstrap_version"] = BOOTSTRAP_VERSION
        _write(site_root, data)
    return count
