from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from indanya_desktop.related_links import person_destination_mode
from indanya_desktop.person_identity import person_identity_issues


JST = ZoneInfo("Asia/Tokyo")
QUALITY_VERSION = 1
SHADOW_DAYS = 7
MIN_ENFORCEMENT_SAMPLES = 20
MAX_EVENTS = 1200
MAX_FEEDBACK = 600
_LOCK = threading.RLock()


FAILURE_LABELS = {
    "wrong_source": "参照先が本編ではない",
    "wrong_media": "記事と画像・動画が一致しない",
    "wrong_person": "人物の特定が違う",
    "wrong_title": "タイトルが内容と一致しない",
    "wrong_pr": "PR・公式リンクが内容と一致しない",
    "wrong_card_image": "カード画像とリンク先が一致しない",
    "missing_official_link": "人物・作品の公式導線がない",
    "missing_video": "本編動画を回収できていない",
    "media_count_mismatch": "素材数・配置が一致しない",
    "unnatural_copy": "タイトル・レスが不自然",
    "duplicate_content": "重複記事・重複導線",
    "not_adult": "成人向け記事ではない",
    "rights_policy": "権利・掲載基準に合わない",
    "timeout": "処理時間切れ",
    "network": "取得先への接続失敗",
    "rate_limit": "Codex利用制限",
    "schema": "AI応答・保存形式が不正",
    "publish": "公開処理に失敗",
    "other": "その他",
}

SEVERE_BLOCKERS = {
    "missing_source_url",
    "missing_article_media",
    "unknown_media_reference",
    "duplicate_media_reference",
    "exact_product_mismatch",
    "product_without_destination",
    "cross_product_media",
    "cross_subject_media",
    "missing_embedded_exact_product_cta",
    "missing_verified_official_work",
    "official_work_card_missing_thumbnail",
    "named_person_identity_unverified",
    "person_identity_below_precision_gate",
    "related_product_card_mismatch",
    "topic_search_uses_article_image",
}


def _now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        return current.replace(tzinfo=JST)
    return current.astimezone(JST)


def _quality_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "adaptive-quality.json"


def _empty_state(now: datetime | None = None) -> dict[str, Any]:
    stamp = _now(now).isoformat(timespec="seconds")
    return {
        "version": QUALITY_VERSION,
        "created_at": stamp,
        "shadow_started_at": stamp,
        "mode_override": "auto",
        "events": [],
        "feedback": [],
        "routes": {},
        "sources": {},
        "articles": {},
        "performance": {"articles": {}, "sources": {}, "updated_at": ""},
        "routines": {"daily": "", "weekly": "", "monthly": ""},
        "snapshots": [],
        "updated_at": stamp,
    }


def _read(site_root: Path, now: datetime | None = None) -> dict[str, Any]:
    try:
        value = json.loads(_quality_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = _empty_state(now)
    if not isinstance(value, dict):
        value = _empty_state(now)
    defaults = _empty_state(now)
    for key, default in defaults.items():
        value.setdefault(key, copy.deepcopy(default))
    value["version"] = QUALITY_VERSION
    return value


def _write(site_root: Path, value: dict[str, Any]) -> None:
    path = _quality_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    value["version"] = QUALITY_VERSION
    value["updated_at"] = _now().isoformat(timespec="seconds")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _host(url: Any) -> str:
    return (urlparse(str(url or "")).hostname or "unknown").lower().removeprefix("www.")


def route_template(url: Any) -> str:
    parsed = urlparse(str(url or ""))
    path = unquote(parsed.path or "/").casefold()
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 4 and re.fullmatch(r"20\d{2}", parts[0]) and all(
        re.fullmatch(r"\d{1,2}", part) for part in parts[1:3]
    ):
        parts = ["{date}", "{slug}", *parts[4:]]
    normalized: list[str] = []
    for part in parts:
        part = re.sub(r"(?<![a-z])\d{3,}(?![a-z])", "{id}", part)
        part = re.sub(r"[a-f0-9]{12,}", "{key}", part)
        normalized.append(part[:80])
    route = "/" + "/".join(normalized)
    query_keys = sorted({key.casefold() for key in parse_qs(parsed.query) if key})
    if query_keys:
        route += "?" + "&".join(query_keys[:8])
    return (route or "/")[:240]


def normalize_failure_code(value: Any, *, message: str = "", stage: str = "") -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "source": "wrong_source",
        "media": "wrong_media",
        "person": "wrong_person",
        "title": "wrong_title",
        "pr": "wrong_pr",
        "card": "wrong_card_image",
        "official_link": "missing_official_link",
        "video": "missing_video",
        "copy": "unnatural_copy",
        "duplicate": "duplicate_content",
        "policy": "rights_policy",
        "invalid_media": "wrong_media",
        "ai_validation": "schema",
    }
    raw = aliases.get(raw, raw)
    if raw in FAILURE_LABELS:
        return raw
    text = f"{stage} {message}".casefold()
    rules = (
        ("rate_limit", ("利用制限", "usage limit", "rate_limit")),
        ("timeout", ("timeout", "timed out", "時間切れ")),
        ("network", ("http error", "connection", "network", "接続")),
        ("not_adult", ("成人向けではない", "non-adult", "一般向け")),
        ("rights_policy", ("権利", "許可", "policy", "ポリシー")),
        ("wrong_source", ("本編", "gateway", "follow_url", "参照先")),
        ("missing_video", ("動画が見つ", "動画を取得", "missing video")),
        ("wrong_media", ("image mismatch", "media mismatch", "素材id", "別作品")),
        ("schema", ("schema", "json", "形式が不正", "必要なレス")),
        ("publish", ("公開", "github", "git ", "push")),
    )
    for code, markers in rules:
        if any(marker in text for marker in markers):
            return code
    return "other"


def _product_key(value: Any, *, depth: int = 0) -> str:
    if depth > 1:
        return ""
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    if (parsed.hostname or "").casefold() in {"al.dmm.com", "al.dmm.co.jp", "al.fanza.co.jp"}:
        return _product_key(unquote(str((query.get("lurl") or [""])[-1])), depth=depth + 1)
    for key in ("id", "cid"):
        product_id = str((query.get(key) or [""])[-1])
        normalized = re.sub(r"[^a-z0-9]", "", product_id.casefold())
        if normalized:
            return normalized
    match = re.search(r"(?:cid|id)[=/]([a-z0-9_-]{4,80})", parsed.path.casefold())
    return re.sub(r"[^a-z0-9]", "", match.group(1)) if match else ""


def _payload_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
    return {
        "slug": str(payload.get("slug") or ""),
        "source_url": str(payload.get("source_url") or ""),
        "title": str(payload.get("title") or ""),
        "summary": str(payload.get("summary") or ""),
        "category": str(payload.get("category") or ""),
        "tags": list(payload.get("tags") or []),
        "thumbnail_id": str(payload.get("thumbnail_id") or ""),
        "image_ids": [str(item.get("id") or "") for item in payload.get("images") or [] if isinstance(item, dict)],
        "video_ids": [str(item.get("id") or "") for item in payload.get("videos") or [] if isinstance(item, dict)],
        "destinations": [
            {
                "type": str(block.get("type") or ""),
                "url": str(block.get("url") or ""),
                "title": str(block.get("title") or ""),
                "link_kind": str(block.get("link_kind") or block.get("match_type") or ""),
                "thumbnail_url": str(block.get("thumbnail_url") or ""),
                "thumbnail_image_id": str(block.get("thumbnail_image_id") or ""),
            }
            for block in blocks
            if isinstance(block, dict) and block.get("type") in {"product_cta", "related_link"}
        ],
    }


def _snapshot_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_payload_snapshot(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def quality_mode(site_root: Path, *, now: datetime | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    current = _now(now)
    value = state if isinstance(state, dict) else _read(site_root, current)
    override = str(value.get("mode_override") or "auto")
    try:
        started = datetime.fromisoformat(str(value.get("shadow_started_at") or ""))
        if started.tzinfo is None:
            started = started.replace(tzinfo=JST)
        started = started.astimezone(JST)
    except ValueError:
        started = current
    age = max(0, (current - started).days)
    assessed = sum(
        1 for event in value.get("events") or []
        if isinstance(event, dict) and event.get("kind") in {"quality", "editor_feedback", "publish"}
    )
    if override in {"shadow", "advisory", "enforced"}:
        mode = override
    elif age < SHADOW_DAYS:
        mode = "shadow"
    elif assessed < MIN_ENFORCEMENT_SAMPLES:
        mode = "advisory"
    else:
        mode = "enforced"
    return {
        "mode": mode,
        "shadow_days_elapsed": age,
        "shadow_days_remaining": max(0, SHADOW_DAYS - age),
        "assessed_samples": assessed,
        "minimum_samples": MIN_ENFORCEMENT_SAMPLES,
        "started_at": started.isoformat(timespec="seconds"),
    }


def candidate_eligibility(
    candidate: dict[str, Any],
    *,
    site_plan: dict[str, Any] | None = None,
    source_performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []
    buzz = max(0, min(100, int(candidate.get("buzz_score") or candidate.get("score") or 0)))
    structural = int(candidate.get("structural_score") or 0)
    score = 34 + min(30, round(buzz * 0.3)) + min(18, max(0, structural))
    if buzz >= 60:
        reasons.append("話題性が高い")
    plan = site_plan or {}
    successes = int(plan.get("site_successes") or 0)
    failures = int(plan.get("site_failures") or 0)
    decided = successes + failures
    if decided >= 3:
        rate = successes / decided
        score += round((rate - 0.5) * 24)
        reasons.append(f"取得実績 {successes}/{decided}")
    if plan.get("cooldown_active"):
        blockers.append("same_route_cooldown")
        score -= 35
    performance = source_performance or {}
    views = int(performance.get("page_views") or 0)
    if views >= 20:
        ctr = float(performance.get("pr_ctr") or 0)
        score += min(12, round(ctr * 0.8))
        reasons.append(f"公開後実績 {views}閲覧")
    title = str(candidate.get("title") or "")
    if len(title.strip()) < 6:
        blockers.append("insufficient_title")
        score -= 30
    return {
        "score": max(0, min(100, score)),
        "eligible": not blockers and score >= 50,
        "reasons": reasons,
        "blockers": blockers,
        "version": QUALITY_VERSION,
    }


def article_quality_report(
    payload: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = source or {}
    blockers: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    source_url = str(payload.get("source_url") or source.get("url") or "").strip()
    images = [item for item in payload.get("images") or [] if isinstance(item, dict)]
    related_thumbnail_ids = {
        str(item.get("id") or "")
        for item in images
        if item.get("id") and item.get("related_thumbnail_only") is True
    }
    article_images = [
        item for item in images if item.get("related_thumbnail_only") is not True
    ]
    videos = [item for item in payload.get("videos") or [] if isinstance(item, dict)]
    blocks = [item for item in payload.get("blocks") or [] if isinstance(item, dict)]
    image_ids = {str(item.get("id") or "") for item in images if item.get("id")}
    package_image_ids = {
        str(item.get("id") or "")
        for item in images
        if item.get("id")
        and str(item.get("rights_basis") or "") == "fanza_product_main_image"
    }
    video_ids = {str(item.get("id") or "") for item in videos if item.get("id")}
    thumbnail_id = str(payload.get("thumbnail_id") or "")

    if len(title) < 10:
        warnings.append("title_too_short")
    if len(summary) < 30:
        warnings.append("summary_too_short")
    if not source_url.startswith(("http://", "https://")):
        blockers.append("missing_source_url")
    if not article_images and not videos and not payload.get("x_embed"):
        blockers.append("missing_article_media")
    if image_ids and thumbnail_id not in image_ids:
        blockers.append("unknown_thumbnail")

    placed_images: list[str] = []
    placed_videos: list[str] = []
    for block in blocks:
        if block.get("type") == "images":
            placed_images.extend(str(value) for value in block.get("image_ids") or [])
        elif block.get("type") == "videos":
            placed_videos.extend(str(value) for value in block.get("video_ids") or [])
    if any(value not in image_ids for value in placed_images) or any(value not in video_ids for value in placed_videos):
        blockers.append("unknown_media_reference")
    if len(placed_images) != len(set(placed_images)) or len(placed_videos) != len(set(placed_videos)):
        blockers.append("duplicate_media_reference")
    if related_thumbnail_ids.intersection(placed_images):
        blockers.append("related_thumbnail_in_article_body")
    missing_body_images = image_ids.difference(related_thumbnail_ids).difference(placed_images)
    if missing_body_images and not (
        payload.get("thumbnail_only") is True and missing_body_images == {thumbnail_id}
    ):
        warnings.append("unplaced_image")
    if video_ids.difference(placed_videos):
        warnings.append("unplaced_video")

    source_product = _product_key(payload.get("source_url"))
    exact_product_urls: list[str] = []
    exact_official_work_urls: list[str] = []
    exact_official_work_has_thumbnail = False
    profile_services: set[str] = set()
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type == "product_cta":
            destination = str(block.get("url") or "")
            product = _product_key(destination)
            match_type = str(block.get("match_type") or "")
            if not product:
                blockers.append("product_without_destination")
            if match_type.startswith("exact_"):
                exact_product_urls.append(destination)
                if source_product and product != source_product and payload.get("content_mode") == "fanza_product":
                    blockers.append("exact_product_mismatch")
                package_thumbnail = (
                    str(block.get("thumbnail_source_kind") or "") == "fanza_package"
                    and (
                        bool(block.get("thumbnail_url"))
                        or str(block.get("thumbnail_image_id") or "") in package_image_ids
                    )
                )
                if not package_thumbnail:
                    blockers.append("product_card_not_package")
                thumbnail_owner = _product_key(block.get("thumbnail_owner_url"))
                if thumbnail_owner and product and thumbnail_owner != product:
                    blockers.append("product_card_image_mismatch")
        elif block_type == "related_link" and block.get("link_kind") == "official_profile":
            profile_services.add(str(block.get("provider") or "").casefold())
            local_thumbnail_id = str(block.get("thumbnail_image_id") or "")
            local_thumbnail = next(
                (
                    item for item in images
                    if str(item.get("id") or "") == local_thumbnail_id
                ),
                None,
            )
            local_thumbnail_valid = bool(
                isinstance(local_thumbnail, dict)
                and local_thumbnail.get("related_thumbnail_only") is True
                and str(local_thumbnail.get("thumbnail_owner_url") or "").rstrip("/")
                == str(block.get("thumbnail_owner_url") or "").rstrip("/")
            )
            if (
                not (block.get("thumbnail_url") or local_thumbnail_valid)
                or block.get("thumbnail_source_kind")
                not in {
                    "profile", "official_hub_profile",
                    "official_identity_fallback",
                }
            ):
                blockers.append("profile_card_image_mismatch")
        elif block_type == "related_link" and block.get("link_kind") == "inferred_topic_search":
            if block.get("thumbnail_url") or block.get("thumbnail_image_id"):
                blockers.append("topic_search_uses_article_image")
        elif block_type == "related_link" and block.get("link_kind") == "inferred_topic_product":
            destination = str(block.get("url") or "")
            product = _product_key(destination)
            thumbnail_owner = _product_key(block.get("thumbnail_owner_url"))
            local_thumbnail = next(
                (
                    item for item in images
                    if str(item.get("id") or "")
                    == str(block.get("thumbnail_image_id") or "")
                ),
                None,
            )
            has_matching_local_package = bool(
                isinstance(local_thumbnail, dict)
                and local_thumbnail.get("related_thumbnail_only") is True
                and str(local_thumbnail.get("rights_basis") or "")
                == "fanza_product_main_image"
                and _product_key(local_thumbnail.get("thumbnail_owner_url")) == product
            )
            has_matching_remote_package = bool(
                block.get("thumbnail_url")
                and str(block.get("thumbnail_source_kind") or "") == "fanza_package"
                and thumbnail_owner == product
            )
            if not product or not (
                has_matching_local_package or has_matching_remote_package
            ):
                blockers.append("related_product_card_mismatch")
        elif block_type == "related_link" and block.get("link_kind") == "exact_official_work":
            destination = str(block.get("url") or "").strip()
            if destination:
                exact_official_work_urls.append(destination)
            local_thumbnail = next(
                (
                    item for item in images
                    if str(item.get("id") or "")
                    == str(block.get("thumbnail_image_id") or "")
                ),
                None,
            )
            local_thumbnail_valid = bool(
                destination
                and isinstance(local_thumbnail, dict)
                and local_thumbnail.get("related_thumbnail_only") is True
                and str(local_thumbnail.get("rights_basis") or "")
                == "official_page_thumbnail"
                and str(local_thumbnail.get("thumbnail_owner_url") or "").rstrip("/")
                == destination.rstrip("/")
            )
            remote_thumbnail_valid = bool(
                destination
                and block.get("thumbnail_url")
                and str(block.get("thumbnail_source_kind") or "")
                == "official_page"
                and str(block.get("thumbnail_owner_url") or "").rstrip("/")
                == destination.rstrip("/")
            )
            if local_thumbnail_valid or remote_thumbnail_valid:
                exact_official_work_has_thumbnail = True
            else:
                blockers.append("official_work_card_image_mismatch")

    exact_keys = [_product_key(url) for url in exact_product_urls if _product_key(url)]
    if len(exact_keys) != len(set(exact_keys)):
        warnings.append("duplicate_exact_product_cta")
    if payload.get("content_mode") == "fanza_product" and source_product and source_product not in exact_keys:
        blockers.append("missing_exact_product_cta")
    embedded_exact_keys = {
        _product_key(url)
        for url in source.get("verified_embedded_fanza_product_urls") or []
        if _product_key(url)
    }
    if embedded_exact_keys.difference(exact_keys):
        blockers.append("missing_embedded_exact_product_cta")

    official_required = bool(
        source.get("official_work_required")
        or payload.get("official_work_required")
    )
    verified_work_urls = {
        str(item.get("url") or "").strip()
        for item in (
            source.get("verified_work_destinations")
            or payload.get("verified_work_destinations")
            or []
        )
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    }
    if official_required and not exact_keys:
        if not verified_work_urls:
            blockers.append("missing_verified_official_work")
        elif verified_work_urls.difference(exact_official_work_urls):
            blockers.append("missing_verified_official_work")
        elif not exact_official_work_has_thumbnail:
            blockers.append("official_work_card_missing_thumbnail")
        else:
            evidence.append("作品名と一致する公式・正規販売ページを照合")

    blockers.extend(person_identity_issues(payload))
    subject = payload.get("main_subject") if isinstance(payload.get("main_subject"), dict) else {}
    subject_name = str(subject.get("name") or "").strip()
    if subject_name and subject.get("kind") == "person":
        if subject.get("is_public_creator") is True:
            gate = payload.get("person_identity_gate")
            subject_key = re.sub(
                r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", subject_name.casefold()
            )
            attributed_names = {
                re.sub(
                    r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]",
                    "",
                    str(item.get("person_name") or "").casefold(),
                )
                for item in payload.get("media_person_attributions") or []
                if isinstance(item, dict)
            }
            if (
                not isinstance(gate, dict)
                or gate.get("status") != "verified"
                or subject_key not in attributed_names
            ):
                blockers.append("named_person_identity_unverified")
        source_images = {
            str(item.get("id") or ""): item
            for item in source.get("images") or []
            if isinstance(item, dict) and item.get("id")
        }
        selected_images = [item for item in images if item.get("source_id")]

        def selected_group(item: dict[str, Any]) -> str:
            source_id = str(item.get("source_id") or "")
            source_group = str(
                (source_images.get(source_id) or {}).get("ai_content_group") or ""
            ).strip()
            return source_group or str(item.get("ai_content_group") or "").strip()

        selected_groups = {
            group
            for item in selected_images
            if (group := selected_group(item))
        }
        if len(selected_groups) > 1:
            blockers.append("cross_subject_media")
        if len(selected_images) > 1 and any(not selected_group(item) for item in selected_images):
            warnings.append("unverified_subject_media")
        elif len(selected_images) > 1 and len(selected_groups) == 1:
            evidence.append("保存済みの素材所有者グループが全画像で一致")
    people = source.get("ai_fanza_people") or source.get("fanza_people") or []
    known_person = bool(person_destination_mode(payload)) or bool(people)
    has_person_work = any(
        block.get("type") == "related_link"
        and block.get("link_kind") in {
            "verified_person_search", "person_search", "official_content",
            "exact_official_work",
        }
        for block in blocks
    )
    if known_person and not profile_services and not has_person_work:
        warnings.append("missing_person_destination")

    analysis_confidence = source.get("ai_editorial_confidence")
    try:
        luna_confidence = max(0, min(100, int(analysis_confidence)))
    except (TypeError, ValueError):
        luna_confidence = 70
    score = 100 - len(set(blockers)) * 30 - len(set(warnings)) * 7
    score = round(score * 0.75 + luna_confidence * 0.25)
    score = max(0, min(100, score))
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    if not blockers:
        evidence.append("素材IDと本文配置の参照整合を確認")
    if exact_keys:
        evidence.append("FANZA商品IDをURLから照合")
    if embedded_exact_keys and not embedded_exact_keys.difference(exact_keys):
        evidence.append("本文ギャラリー直後の確定作品PRを照合")
    severe = any(code in SEVERE_BLOCKERS for code in blockers)
    recommendation = "auto_ready" if not blockers and score >= 82 else "review" if score >= 55 and not severe else "discard"
    return {
        "version": QUALITY_VERSION,
        "score": score,
        "recommendation": recommendation,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": evidence,
        "luna_confidence": luna_confidence,
        "snapshot_hash": _snapshot_hash(payload),
    }


def apply_quality_gate(
    site_root: Path,
    payload: dict[str, Any],
    source: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    report = article_quality_report(payload, source)
    with _LOCK:
        state = _read(site_root, now)
        mode = quality_mode(site_root, now=now, state=state)
        effective = report["recommendation"]
        if mode["mode"] in {"shadow", "advisory"} and effective != "discard":
            effective = "auto_ready"
        report["mode"] = mode["mode"]
        report["effective_decision"] = effective
        report["assessed_at"] = _now(now).isoformat(timespec="seconds")
        payload["quality_gate"] = report
        if effective == "auto_ready":
            payload["review_status"] = "unreviewed"
        elif effective == "review":
            payload["review_status"] = "needs_review"
            payload["review_message"] = " / ".join(report["blockers"] + report["warnings"])[:500]
        else:
            payload["review_status"] = "rejected"
            payload["review_message"] = " / ".join(report["blockers"] + report["warnings"])[:500]
        if persist:
            event = {
                "at": report["assessed_at"],
                "kind": "quality",
                "slug": str(payload.get("slug") or ""),
                "url": str(payload.get("source_url") or ""),
                "host": _host(payload.get("source_url")),
                "route": route_template(payload.get("source_url")),
                "score": report["score"],
                "recommendation": report["recommendation"],
                "effective_decision": effective,
                "blockers": report["blockers"],
                "warnings": report["warnings"],
                "snapshot_hash": report["snapshot_hash"],
            }
            state.setdefault("events", []).append(event)
            state["events"] = state["events"][-MAX_EVENTS:]
            article = state.setdefault("articles", {}).setdefault(
                str(payload.get("slug") or report["snapshot_hash"]), {}
            )
            article.update(event)
            _write(site_root, state)
    return report


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    first = _payload_snapshot(before)
    second = _payload_snapshot(after)
    return [key for key in first if first.get(key) != second.get(key)]


def record_editorial_feedback(
    site_root: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    failure_code: Any = "",
    *,
    note: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    changes = changed_fields(before, after)
    code = normalize_failure_code(failure_code)
    if not failure_code:
        if "source_url" in changes:
            code = "wrong_source"
        elif "destinations" in changes:
            code = "wrong_pr"
        elif "image_ids" in changes or "video_ids" in changes or "thumbnail_id" in changes:
            code = "wrong_media"
        elif "title" in changes:
            code = "wrong_title"
        elif "summary" in changes:
            code = "unnatural_copy"
        else:
            code = "other"
    event = {
        "at": _now(now).isoformat(timespec="seconds"),
        "kind": "editor_feedback",
        "slug": str(after.get("slug") or before.get("slug") or ""),
        "url": str(after.get("source_url") or before.get("source_url") or ""),
        "host": _host(after.get("source_url") or before.get("source_url")),
        "route": route_template(after.get("source_url") or before.get("source_url")),
        "failure_code": code,
        "label": FAILURE_LABELS[code],
        "changed_fields": changes,
        "before_hash": _snapshot_hash(before),
        "after_hash": _snapshot_hash(after),
        "note": str(note or "")[:500],
    }
    with _LOCK:
        state = _read(site_root, now)
        state.setdefault("feedback", []).append(event)
        state["feedback"] = state["feedback"][-MAX_FEEDBACK:]
        state.setdefault("events", []).append(event)
        state["events"] = state["events"][-MAX_EVENTS:]
        route = state.setdefault("routes", {}).setdefault(
            f"{event['host']}|{event['route']}",
            {"host": event["host"], "route": event["route"], "feedback": {}, "corrections": 0},
        )
        route["corrections"] = int(route.get("corrections") or 0) + 1
        feedback = route.setdefault("feedback", {})
        feedback[code] = int(feedback.get(code) or 0) + 1
        route["last_feedback_at"] = event["at"]
        route["last_changed_fields"] = changes
        _write(site_root, state)
    return event


def record_processing_outcome(
    site_root: Path,
    *,
    url: str,
    slug: str = "",
    outcome: str,
    stage: str,
    failure_code: Any = "",
    message: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    code = "" if outcome == "success" else normalize_failure_code(
        failure_code, message=message, stage=stage
    )
    event = {
        "at": _now(now).isoformat(timespec="seconds"),
        "kind": "processing",
        "url": str(url or "")[:2048],
        "slug": str(slug or "")[:120],
        "host": _host(url),
        "route": route_template(url),
        "outcome": str(outcome or "")[:30],
        "stage": str(stage or "")[:60],
        "failure_code": code,
        "message": str(message or "")[:500],
    }
    with _LOCK:
        state = _read(site_root, now)
        state.setdefault("events", []).append(event)
        state["events"] = state["events"][-MAX_EVENTS:]
        source = state.setdefault("sources", {}).setdefault(
            event["host"], {"successes": 0, "failures": 0, "failure_codes": {}}
        )
        if outcome == "success":
            source["successes"] = int(source.get("successes") or 0) + 1
        elif outcome in {"failure", "discarded", "publish_failed"}:
            source["failures"] = int(source.get("failures") or 0) + 1
            codes = source.setdefault("failure_codes", {})
            codes[code] = int(codes.get(code) or 0) + 1
        source["last_at"] = event["at"]
        _write(site_root, state)
    return event


def record_publish_outcome(
    site_root: Path,
    payload: dict[str, Any],
    outcome: str,
    *,
    message: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    return record_processing_outcome(
        site_root,
        url=str(payload.get("source_url") or ""),
        slug=str(payload.get("slug") or ""),
        outcome="success" if outcome == "success" else "publish_failed",
        stage="publish",
        failure_code="publish",
        message=message,
        now=now,
    )


def sync_ga4_performance(
    site_root: Path,
    report: dict[str, Any],
    *,
    audience: str = "external",
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = report.get(audience) if isinstance(report.get(audience), dict) else {}
    rows = selected.get("articles") if isinstance(selected, dict) else []
    article_metrics: dict[str, dict[str, Any]] = {}
    source_totals: dict[str, dict[str, float]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("pagePath") or "")
        match = re.search(r"/articles/([a-z0-9-]+)\.html", path)
        if not match:
            continue
        slug = match.group(1)
        views = int(row.get("eventCount") or 0)
        pr_impressions = int(row.get("prImpressions") or 0)
        pr_clicks = int(row.get("prClicks") or 0)
        metrics = {
            "slug": slug,
            "page_views": views,
            "active_users": int(row.get("activeUsers") or 0),
            "pr_impressions": pr_impressions,
            "pr_clicks": pr_clicks,
            "pr_ctr": round(pr_clicks * 100 / pr_impressions, 2) if pr_impressions else 0.0,
            "updated_at": _now(now).isoformat(timespec="seconds"),
        }
        article_metrics[slug] = metrics
        try:
            payload = json.loads(
                (site_root / ".article-studio" / "drafts" / f"{slug}.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = {}
        host = _host(payload.get("source_url"))
        if host == "unknown":
            continue
        totals = source_totals.setdefault(
            host, {"page_views": 0.0, "pr_impressions": 0.0, "pr_clicks": 0.0, "articles": 0.0}
        )
        totals["page_views"] += views
        totals["pr_impressions"] += pr_impressions
        totals["pr_clicks"] += pr_clicks
        totals["articles"] += 1
    for totals in source_totals.values():
        impressions = totals["pr_impressions"]
        totals["pr_ctr"] = round(totals["pr_clicks"] * 100 / impressions, 2) if impressions else 0.0
        totals["eligible_for_weighting"] = totals["page_views"] >= 20
    with _LOCK:
        state = _read(site_root, now)
        performance = state.setdefault("performance", {"articles": {}, "sources": {}})
        performance.setdefault("articles", {}).update(article_metrics)
        performance["sources"] = source_totals
        performance["updated_at"] = _now(now).isoformat(timespec="seconds")
        _write(site_root, state)
    return {"articles": article_metrics, "sources": source_totals}


def source_performance(site_root: Path, url: str) -> dict[str, Any]:
    state = _read(site_root)
    value = (state.get("performance") or {}).get("sources", {}).get(_host(url), {})
    return dict(value) if isinstance(value, dict) else {}


def run_quality_routines(site_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    current = _now(now)
    with _LOCK:
        state = _read(site_root, current)
        routines = state.setdefault("routines", {})
        ran: list[str] = []
        if routines.get("daily") != current.date().isoformat():
            routines["daily"] = current.date().isoformat()
            ran.append("daily")
        week = f"{current.isocalendar().year}-W{current.isocalendar().week:02d}"
        if routines.get("weekly") != week:
            routines["weekly"] = week
            ran.append("weekly")
            snapshot = {
                "at": current.isoformat(timespec="seconds"),
                "week": week,
                "routes": copy.deepcopy(state.get("routes") or {}),
                "sources": copy.deepcopy(state.get("sources") or {}),
            }
            state.setdefault("snapshots", []).append(snapshot)
            state["snapshots"] = state["snapshots"][-8:]
        month = current.strftime("%Y-%m")
        if routines.get("monthly") != month:
            routines["monthly"] = month
            ran.append("monthly")
        if ran:
            _write(site_root, state)
    return {"ran": ran, "mode": quality_mode(site_root, now=current)}


def rollback_learning_snapshot(site_root: Path, index: int = -1) -> dict[str, Any]:
    with _LOCK:
        state = _read(site_root)
        snapshots = state.get("snapshots") or []
        if not snapshots:
            raise RuntimeError("戻せる学習スナップショットがありません")
        snapshot = snapshots[index]
        state["routes"] = copy.deepcopy(snapshot.get("routes") or {})
        state["sources"] = copy.deepcopy(snapshot.get("sources") or {})
        state["rolled_back_at"] = _now().isoformat(timespec="seconds")
        state["rolled_back_from"] = str(snapshot.get("at") or "")
        _write(site_root, state)
        return {"restored_at": state["rolled_back_from"]}


def quality_summary(site_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    state = _read(site_root, now)
    events = [event for event in state.get("events") or [] if isinstance(event, dict)]
    feedback = [event for event in state.get("feedback") or [] if isinstance(event, dict)]
    recent_quality = [event for event in events if event.get("kind") == "quality"]
    decisions = {"auto_ready": 0, "review": 0, "discard": 0}
    for event in recent_quality:
        key = str(event.get("recommendation") or "")
        if key in decisions:
            decisions[key] += 1
    return {
        "mode": quality_mode(site_root, now=now, state=state),
        "quality_count": len(recent_quality),
        "feedback_count": len(feedback),
        "decisions": decisions,
        "recent_feedback": feedback[-10:][::-1],
        "performance_updated_at": str((state.get("performance") or {}).get("updated_at") or ""),
        "version": QUALITY_VERSION,
    }
