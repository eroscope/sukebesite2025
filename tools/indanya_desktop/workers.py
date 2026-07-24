from __future__ import annotations

import hashlib
import json
import re
import socket
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urldefrag, urlparse

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from article_studio import (
    MAX_SELECTED_SOURCE_VIDEOS,
    MAX_VIDEO_PROXY_BYTES,
    CodexRunner,
    analyze_source_url,
    apply_codex_analysis,
    apply_codex_result,
    build_source_draft_payload,
    save_draft,
    _validate_source_url,
)
from indanya_desktop.publishing import (
    _compress_video,
    _materialize_stream_video,
    publish_article,
    unpublish_article,
)
from indanya_desktop.sites import ManagedSite
from indanya_desktop.browser_capture import capture_rendered_source, open_x_login_session
from indanya_desktop.automation import discover_candidates, mark_candidate_status


class WorkerSignals(QObject):
    progress = Signal(int, str)
    completed = Signal(dict)
    failed = Signal(str)


class XLoginRequiredError(RuntimeError):
    pass


class XLoginWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            open_x_login_session(
                lambda value, message: self.signals.progress.emit(value, message)
            )
            self.signals.completed.emit({"status": "ready"})
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)



def _mark_ready_to_publish(payload: dict[str, Any]) -> dict[str, Any]:
    payload["rights_status"] = "confirmed"
    payload["adult_confirmed"] = True
    payload["rights_confirmed"] = True
    payload["privacy_confirmed"] = True
    payload["source_confirmed"] = True
    payload["review_status"] = "unreviewed"
    return payload


MAX_DESKTOP_SOURCE_IMAGES = 10
MAX_DESKTOP_SOURCE_IMAGE_BYTES = 72 * 1024 * 1024
BAD_THUMBNAIL_TERMS = (
    "advert", "banner", "logo", "noimage", "ogp", "sns",
    "thumb", "thumbnail", "preview", "sample", "poster",
    "mosaic", "blur", "blurred", "censored",
    "広告", "バナー", "ロゴ", "サムネ", "サムネイル", "モザイク", "ぼかし",
)


def _is_transient_generation_error(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in (
        "利用上限", "usage limit", "rate limit", "時間切れ",
        "timed out", "ログインを確認", "authentication",
    ))


def _image_quality_score(item: dict[str, Any]) -> int:
    text = " ".join(
        str(item.get(key) or "").lower()
        for key in ("url", "alt", "title", "ai_reason", "reason", "class", "id")
    )
    score = 0
    if "i.imgur.com" in text:
        score += 25
    if item.get("ai_recommended"):
        score += 40
    try:
        source_score = int(item.get("source_score") or 0)
    except (TypeError, ValueError):
        source_score = 0
    score += max(-120, min(120, source_score))
    verdict = str(item.get("ai_verdict") or item.get("verdict") or "").lower()
    role = str(item.get("ai_role") or "").lower()
    recommended_use = str(item.get("ai_recommended_use") or "").lower()
    if verdict in {"main", "content", "usable", "recommended"}:
        score += 30
    if verdict in {"advertisement", "ad", "rejected", "logo", "navigation"}:
        score -= 200
    score += {
        "article_thumbnail": 65,
        "article_main": 55,
        "article_gallery": 35,
        "related_article": -220,
        "advertisement": -220,
        "site_ui": -220,
        "unrelated": -220,
    }.get(role, 0)
    score += {"thumbnail": 45, "body": 30, "thumbnail_and_body": 50, "exclude": -220}.get(recommended_use, 0)
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width and height:
        area = width * height
        if area >= 120_000:
            score += 25
        elif area >= 50_000:
            score += 12
        if width < 160 or height < 120:
            score -= 60
    if any(term in text for term in BAD_THUMBNAIL_TERMS) and recommended_use not in {"thumbnail", "thumbnail_and_body"}:
        score -= 55
    return score


def _select_article_images(source: dict[str, Any]) -> dict[str, Any]:
    images = [item for item in source.get("images") or [] if isinstance(item, dict) and item.get("id")]
    if not images:
        return {"thumbnail_id": "", "body_ids": []}
    by_id = {str(item["id"]): item for item in images}
    chosen: list[str] = []
    thumbnail_ids = [
        str(image_id) for image_id in source.get("recommended_thumbnail_ids") or []
        if str(image_id) in by_id
    ]
    body_ids = [
        str(image_id) for image_id in source.get("recommended_body_image_ids") or []
        if str(image_id) in by_id
    ]
    if thumbnail_ids or body_ids:
        thumbnail_id = max(
            thumbnail_ids or body_ids,
            key=lambda image_id: _image_quality_score(by_id[image_id]),
        )
        chosen = sorted(body_ids, key=lambda value: _image_quality_score(by_id[value]), reverse=True)
        chosen = [image_id for image_id in chosen if image_id != thumbnail_id]
        if thumbnail_id in body_ids and not chosen:
            chosen = [thumbnail_id]
        return {
            "thumbnail_id": thumbnail_id,
            "body_ids": _fit_image_selection(chosen, by_id, reserved_ids=[thumbnail_id]),
        }
    for image_id in source.get("recommended_image_ids") or []:
        image_id = str(image_id)
        if image_id in by_id and _image_quality_score(by_id[image_id]) > -100 and image_id not in chosen:
            chosen.append(image_id)
    for item in sorted(images, key=_image_quality_score, reverse=True):
        image_id = str(item["id"])
        if (
            image_id not in chosen
            and str(item.get("ai_verdict") or "") == "article"
            and int(item.get("ai_relevance_score") or 0) >= 40
        ):
            chosen.append(image_id)
        if len(chosen) >= MAX_DESKTOP_SOURCE_IMAGES:
            break
    chosen.sort(key=lambda image_id: _image_quality_score(by_id[image_id]), reverse=True)
    chosen = _fit_image_selection(chosen, by_id)
    return {
        "thumbnail_id": chosen[0] if chosen else "",
        "body_ids": chosen,
    }


def _fit_image_selection(
    image_ids: list[str],
    images_by_id: dict[str, dict[str, Any]],
    reserved_ids: list[str] | None = None,
) -> list[str]:
    fitted: list[str] = []
    reserved = set(reserved_ids or [])
    total_bytes = sum(
        len(images_by_id[image_id]["data"])
        for image_id in reserved
        if image_id in images_by_id and isinstance(images_by_id[image_id].get("data"), bytes)
    )
    maximum = max(0, MAX_DESKTOP_SOURCE_IMAGES - len(reserved))
    for image_id in image_ids:
        if image_id in reserved:
            continue
        item = images_by_id[image_id]
        data = item.get("data")
        byte_count = len(data) if isinstance(data, bytes) else 0
        if fitted and byte_count and total_bytes + byte_count > MAX_DESKTOP_SOURCE_IMAGE_BYTES:
            continue
        fitted.append(image_id)
        total_bytes += byte_count
        if len(fitted) >= maximum:
            break
    return fitted


def _capture_and_analyze_source(
    site_root: Path,
    source_url: str,
    runner: CodexRunner,
    progress: Any = None,
    editorial_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_url = _validate_source_url(source_url)
    visited: set[str] = set()
    source_chain: list[str] = []
    navigation_context: dict[str, Any] = {}
    for depth in range(3):
        normalized = urldefrag(current_url)[0]
        if normalized in visited:
            break
        visited.add(normalized)
        source_chain.append(current_url)
        if progress:
            progress(8 + depth * 8, "Chromeでページ全体とリンク先を確認しています")
        hostname = (urlparse(current_url).hostname or "").lower()
        is_x_source = hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
        semantic_source: dict[str, Any] | None = None
        if is_x_source:
            semantic_source = analyze_source_url(current_url)
        try:
            source = capture_rendered_source(
                current_url,
                (lambda value, message: progress(min(26 + depth * 8, 8 + depth * 8 + value // 4), message))
                if progress else (lambda _v, _m: None),
            )
        except Exception:
            traceback.print_exc()
            if progress:
                progress(18 + depth * 8, "ブラウザ表示に失敗したためHTML解析で回収しています")
            source = semantic_source or analyze_source_url(current_url)
        if semantic_source:
            combined_images: list[dict[str, Any]] = []
            seen_hashes: set[str] = set()
            for item in [*(source.get("images") or []), *(semantic_source.get("images") or [])]:
                if not isinstance(item, dict):
                    continue
                data = item.get("data")
                digest = hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else str(item.get("url") or "")
                if not digest or digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                combined_images.append({**item, "id": f"media-{len(combined_images) + 1}"})
            source.update({
                "source_type": semantic_source["source_type"],
                "x_info": semantic_source.get("x_info", {}),
                "x_embed": semantic_source.get("x_embed"),
                "site_name": "X",
                "author": str((semantic_source.get("x_embed") or {}).get("author_name") or ""),
                "images": combined_images,
            })
            if semantic_source.get("description"):
                source["description"] = semantic_source["description"]
        if (
            source.get("source_type") == "x_profile"
            and source.get("browser_capture")
            and not source.get("x_authenticated")
            and int(source.get("x_timeline_media_count") or 0) == 0
            and any(
                "/status/" in str(item.get("url") or "")
                for item in source.get("links", [])
                if isinstance(item, dict)
            )
        ):
            raise XLoginRequiredError(
                "このXプロフィールの投稿画像・動画はログアウト状態では非表示です。"
                "「URLから作成」のXログインを一度行ってから、もう一度作成してください"
            )
        if navigation_context:
            source["navigation_context"] = navigation_context
        if editorial_intent:
            resolved_intent = dict(editorial_intent)
            resolved_intent.pop("private_note", None)
            if str(resolved_intent.get("content_mode") or "auto") == "auto":
                resolved_intent["content_mode"] = (
                    "x_account" if source.get("source_type") == "x_profile"
                    else "x_post" if source.get("source_type") == "x_post"
                    else "web"
                )
            source["editorial_intent"] = resolved_intent
        if progress:
            progress(30 + depth * 8, "Codexがページの役割と本編素材を判定しています")
        analysis = runner.analyze(source)
        follow_url = str(analysis.get("follow_url") or "").strip()
        if analysis.get("page_role") == "gateway" and follow_url:
            allowed = {
                urldefrag(str(item.get("url") or ""))[0]
                for item in source.get("links", [])
                if isinstance(item, dict)
            }
            validated = _validate_source_url(follow_url)
            if urldefrag(validated)[0] in allowed and urldefrag(validated)[0] not in visited:
                followed_link = next(
                    (
                        item for item in source.get("links", [])
                        if isinstance(item, dict) and urldefrag(str(item.get("url") or ""))[0] == urldefrag(validated)[0]
                    ),
                    {},
                )
                navigation_context = {
                    "from_url": str(source.get("url") or current_url),
                    "from_title": str(source.get("title") or ""),
                    "followed_url": validated,
                    "followed_link_text": str(followed_link.get("text") or ""),
                    "follow_reason": str(analysis.get("follow_reason") or ""),
                }
                current_url = validated
                continue
        result = apply_codex_analysis(source, analysis)
        result["requested_url"] = source_url
        result["source_chain"] = source_chain
        return result
    raise RuntimeError("本編へのリンクを追跡できませんでした。元ページのリンク構造を確認してください")


class GenerateArticleWorker(QRunnable):
    def __init__(
        self,
        site_root: Path,
        source_url: str,
        category: str,
        reply_count: str,
        editorial_intent: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.site_root = site_root
        self.source_url = source_url
        self.category = category
        self.reply_count = reply_count
        self.editorial_intent = dict(editorial_intent or {})
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            runner = CodexRunner(self.site_root)
            status = runner.status()
            if not status.get("available"):
                raise RuntimeError(status.get("message") or "Codexへ接続できません")
            progress = lambda value, message: self.signals.progress.emit(value, message)
            source = _capture_for_manual_generation(
                self.site_root,
                self.source_url,
                runner,
                progress,
                self.editorial_intent,
            )
            selected_videos = list(source.get("recommended_video_ids") or [])[:MAX_SELECTED_SOURCE_VIDEOS]
            image_selection = _select_article_images(source)
            thumbnail_id = str(image_selection["thumbnail_id"])
            body_image_ids = list(image_selection["body_ids"])
            if selected_videos:
                body_image_ids = []
            if not thumbnail_id:
                raise RuntimeError("記事のサムネイルに使える画像が見つかりませんでした")

            options: dict[str, Any] = {
                "category": self.category,
                "reply_count": self.reply_count,
                "selected_image_ids": list(dict.fromkeys([thumbnail_id, *body_image_ids])),
                "selected_video_ids": selected_videos,
            }
            base = build_source_draft_payload(
                source, body_image_ids, None, selected_videos, thumbnail_image_id=thumbnail_id
            )
            self.signals.progress.emit(58, "Codexが画像・動画を見ながらタイトルと記事を書いています")
            generated = runner.generate(source, options)
            if self.category != "auto":
                generated["category"] = self.category
            payload = apply_codex_result(base, generated)
            _apply_editorial_metadata(payload, source, self.editorial_intent, self.site_root)
            _mark_ready_to_publish(payload)
            self.signals.progress.emit(88, "公開可能な記事として登録しています")
            slug = save_draft(payload, self.site_root)
            self.signals.progress.emit(100, "公開可能な記事が完成しました")
            self.signals.completed.emit({
                "slug": slug,
                "title": payload.get("title", ""),
                "image_count": len(payload.get("images", [])),
                "video_count": len(payload.get("videos", [])),
                "source_url": payload.get("source_url", self.source_url),
            })
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


def _capture_for_manual_generation(
    site_root: Path,
    source_url: str,
    runner: CodexRunner,
    progress: Any,
    editorial_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return _capture_and_analyze_source(
            site_root,
            source_url,
            runner,
            progress,
            editorial_intent,
        )
    except XLoginRequiredError:
        progress(12, "投稿素材を表示するためXログインを開きます")
        open_x_login_session(progress)
        progress(18, "ログイン済みのXから投稿素材を取り直しています")
        return _capture_and_analyze_source(
            site_root,
            source_url,
            runner,
            progress,
            editorial_intent,
        )


def _generate_article_payload(
    site_root: Path,
    source_url: str,
    category: str,
    reply_count: str,
    progress: Any = None,
    editorial_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runner = CodexRunner(site_root)
    status = runner.status()
    if not status.get("available"):
        raise RuntimeError(status.get("message") or "Codexへ接続できません")
    source = _capture_and_analyze_source(
        site_root, source_url, runner, progress, editorial_intent
    )
    selected_videos = list(source.get("recommended_video_ids") or [])[:MAX_SELECTED_SOURCE_VIDEOS]
    image_selection = _select_article_images(source)
    thumbnail_id = str(image_selection["thumbnail_id"])
    body_image_ids = list(image_selection["body_ids"])
    if selected_videos:
        body_image_ids = []
    if not thumbnail_id:
        raise RuntimeError("記事のサムネイルに使える画像が見つかりませんでした")

    options: dict[str, Any] = {
        "category": category,
        "reply_count": reply_count,
        "selected_image_ids": list(dict.fromkeys([thumbnail_id, *body_image_ids])),
        "selected_video_ids": selected_videos,
    }
    base = build_source_draft_payload(
        source, body_image_ids, None, selected_videos, thumbnail_image_id=thumbnail_id
    )
    if progress:
        progress(58, "Codexが画像・動画を見ながらタイトルと記事を書いています")
    generated = runner.generate(source, options)
    if category != "auto":
        generated["category"] = category
    payload = apply_codex_result(base, generated)
    _apply_editorial_metadata(payload, source, editorial_intent or {}, site_root)
    return _mark_ready_to_publish(payload)


def _apply_editorial_metadata(
    payload: dict[str, Any],
    source: dict[str, Any],
    intent: dict[str, Any],
    site_root: Path | None = None,
) -> None:
    content_mode = str(intent.get("content_mode") or "auto")
    promotion_type = str(intent.get("promotion_type") or "organic")
    if content_mode == "auto":
        content_mode = (
            "x_account" if source.get("source_type") == "x_profile"
            else "x_post" if source.get("source_type") == "x_post"
            else "web"
        )
    payload["content_mode"] = content_mode
    payload["promotion_type"] = promotion_type
    payload["editorial_brief"] = str(intent.get("editorial_brief") or "")[:1000]
    payload["private_client_note"] = str(intent.get("private_note") or "")[:2000]
    if content_mode in {"x_account", "x_post"}:
        username = str((source.get("x_info") or {}).get("username") or "")
        payload["source_label"] = f"@{username}のX" if username else "X"
    fanza = _resolve_fanza_promotion(source, intent, site_root)
    if fanza:
        payload["content_mode"] = "fanza_product"
        payload["promotion_type"] = "affiliate"
        payload["tags"] = list(dict.fromkeys(["PR", "FANZA", *payload.get("tags", [])]))[:8]
        disclosure = "この記事にはFANZAのアフィリエイト広告が含まれます。"
        existing = str(payload.get("transparency_note") or "")
        payload["transparency_note"] = f"{disclosure} {existing}".strip()[:500]
        product_block = {
            "id": "fanza-product",
            "type": "product_cta",
            "url": fanza["url"],
            "title": fanza["title"],
            "text": fanza["text"],
            "button_text": fanza["button_text"],
        }
        insert_at = _fanza_insert_index(payload["blocks"], fanza["match_level"])
        payload["blocks"].insert(insert_at, product_block)
    elif promotion_type == "sponsored":
        payload["tags"] = list(dict.fromkeys(["PR", *payload.get("tags", [])]))[:8]
        disclosure = "この記事は紹介依頼に基づくPR記事です。"
        existing = str(payload.get("transparency_note") or "")
        payload["transparency_note"] = f"{disclosure} {existing}".strip()[:500]
        payload["blocks"].insert(
            0,
            {"id": "sponsored-disclosure", "type": "ad", "text": disclosure},
        )


def _is_fanza_url(value: str) -> bool:
    try:
        hostname = (urlparse(_validate_source_url(value)).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return (
        hostname == "dmm.co.jp"
        or hostname.endswith(".dmm.co.jp")
        or hostname == "fanza.co.jp"
        or hostname.endswith(".fanza.co.jp")
    )


def _fanza_search_url(query: str) -> str:
    cleaned = " ".join(str(query or "").split())[:120]
    return (
        "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr="
        + quote(cleaned, safe="")
    )


def load_fanza_settings(site_root: Path) -> dict[str, str]:
    path = site_root / ".article-studio" / "fanza.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    return {"affiliate_id": str(value.get("affiliate_id") or "").strip()}


def save_fanza_settings(site_root: Path, affiliate_id: str) -> None:
    value = affiliate_id.strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("FANZAアフィリエイトIDの形式を確認してください")
    path = site_root / ".article-studio" / "fanza.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"affiliate_id": value}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _unwrap_external_affiliate_url(value: str) -> str:
    validated = _validate_source_url(value)
    parsed = urlparse(validated)
    if parsed.hostname == "al.dmm.co.jp":
        target = (parse_qs(parsed.query).get("lurl") or [""])[0]
        if target and _is_fanza_url(target):
            return _validate_source_url(target)
        return ""
    return validated


def _with_fanza_affiliate_id(destination: str, affiliate_id: str) -> str:
    if not affiliate_id or urlparse(destination).hostname == "al.dmm.co.jp":
        return destination
    return (
        "https://al.dmm.co.jp/?lurl="
        + quote(destination, safe="")
        + "&af_id="
        + quote(affiliate_id, safe="")
        + "&ch=api"
    )


def _fanza_topic_query(text: str) -> str:
    topic_groups = (
        (("露出", "羞恥", "野外露出"), "露出 羞恥"),
        (("制服", "JK", "女子校生"), "制服 コスプレ"),
        (("コスプレ", "レイヤー"), "コスプレ"),
        (("競泳", "スク水", "水泳部"), "競泳水着"),
        (("水着", "ビキニ"), "水着 ビキニ"),
        (("巨乳", "爆乳", "デカ乳"), "巨乳"),
        (("美尻", "デカ尻", "尻"), "美尻"),
        (("痴女",), "痴女"),
        (("人妻", "若妻"), "人妻"),
        (("素人", "一般人"), "素人"),
        (("マッサージ", "エステ"), "マッサージ"),
        (("ナンパ",), "ナンパ"),
        (("盗撮", "隠し撮り"), "盗撮"),
        (("ハメ撮り",), "ハメ撮り"),
        (("ギャル",), "ギャル"),
        (("熟女",), "熟女"),
    )
    return next(
        (query for keywords, query in topic_groups if any(keyword in text for keyword in keywords)),
        "",
    )


def _fanza_insert_index(blocks: list[dict[str, Any]], match_level: str) -> int:
    if match_level in {"exact", "strong"}:
        for index, block in enumerate(blocks):
            if block.get("type") in {"images", "videos", "x_embed", "x_timeline"}:
                return index + 1
        return max(1, len(blocks) // 2)
    for index in range(len(blocks) - 1, -1, -1):
        if blocks[index].get("type") != "ad":
            return index + 1
    return len(blocks)


def _resolve_fanza_promotion(
    source: dict[str, Any],
    intent: dict[str, Any],
    site_root: Path | None = None,
) -> dict[str, str] | None:
    explicit = str(intent.get("fanza_url") or "").strip()
    explicit_affiliate = bool(explicit and urlparse(explicit).hostname == "al.dmm.co.jp")
    if explicit:
        if not _is_fanza_url(explicit):
            raise RuntimeError("FANZA誘導URLにはDMMまたはFANZAのリンクを入力してください")
        destination = _validate_source_url(explicit)
    else:
        source_urls = [
            str(source.get("requested_url") or ""),
            str(source.get("url") or ""),
            *[
                str(item.get("url") or "")
                for item in source.get("links", [])
                if isinstance(item, dict)
            ],
        ]
        destination = next((url for url in source_urls if _is_fanza_url(url)), "")

    text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "body_text", "ai_analysis_summary")
    )
    product_code = re.search(r"(?<![A-Z0-9])([A-Z]{2,12})[-_ ]?(\d{3,7})(?!\d)", text.upper())
    ai_product_code = str(source.get("ai_fanza_product_code") or "").strip()
    ai_query = str(source.get("ai_fanza_search_query") or "").strip()
    ai_relevance = str(source.get("ai_fanza_relevance") or "none")
    topic_query = _fanza_topic_query(text)
    content_mode = str(intent.get("content_mode") or "auto")
    promotion_type = str(intent.get("promotion_type") or "organic")
    fanza_signals = (
        "FANZA", "DMM", "AV作品", "AV女優", "アダルトビデオ",
        "デビュー作", "品番", "メーカー", "作品ページ",
        "FANZA同人", "同人作品", "FANZAブックス", "成人向けコミック",
        "成人向けゲーム", "アダルトゲーム",
    )
    is_related = (
        bool(destination)
        or any(signal in text for signal in fanza_signals)
        or ai_relevance in {"related", "likely_product", "exact_product"}
        or bool(topic_query)
    )
    should_promote = (
        content_mode == "fanza_product"
        or promotion_type == "affiliate"
        or (
            content_mode in {"auto", "web", "x_account", "x_post"}
            and is_related
        )
    )
    if not should_promote:
        return None
    title = str(source.get("title") or source.get("description") or "FANZA作品").strip()[:180]
    code_query = (
        f"{product_code.group(1)}-{product_code.group(2)}"
        if product_code else ai_product_code
    )
    search_query = code_query or ai_query or topic_query or title
    if not destination:
        destination = _fanza_search_url(search_query)
    destination_parts = urlparse(_unwrap_external_affiliate_url(destination) or destination)
    destination_product_hint = (
        "/detail/" in destination_parts.path.lower()
        or "/content/" in destination_parts.path.lower()
        or "cid=" in destination_parts.query.lower()
        or "id=" in destination_parts.query.lower()
    )
    if not explicit_affiliate:
        destination = _unwrap_external_affiliate_url(destination)
        if not destination:
            destination = _fanza_search_url(title)
        affiliate_id = load_fanza_settings(site_root)["affiliate_id"] if site_root else ""
        destination = _with_fanza_affiliate_id(destination, affiliate_id)
    source_path = urlparse(str(source.get("url") or "")).path.lower()
    exact_destination = any(
        marker in source_path for marker in ("/detail/", "/content/")
    ) or destination_product_hint
    match_level = (
        "exact"
        if explicit or exact_destination or bool(code_query) or ai_relevance == "exact_product"
        else "strong" if ai_relevance == "likely_product" or bool(ai_query)
        else "related"
    )
    display_query = search_query if match_level != "exact" else title
    card_title = (
        title
        if match_level == "exact"
        else f"{display_query}の関連作品をFANZAで探す"
    )
    return {
        "url": _validate_source_url(destination),
        "title": card_title[:180],
        "text": (
            "作品ページでサンプル、価格、配信内容を確認できます。"
            if match_level == "exact"
            else "この記事の内容に近い作品をFANZAで確認できます。"
        ),
        "button_text": (
            "FANZAでこの作品を見る"
            if match_level == "exact"
            else "関連作品をFANZAで見る"
        ),
        "match_level": match_level,
    }


class CollectCandidatesWorker(QRunnable):
    def __init__(
        self,
        site_root: Path,
        per_source_limit: int,
        source_ids: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.site_root = site_root
        self.per_source_limit = per_source_limit
        self.source_ids = source_ids or []
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(15, "登録した情報源を巡回しています")
            candidates = discover_candidates(
                self.site_root,
                self.per_source_limit,
                self.source_ids,
            )
            self.signals.progress.emit(100, "候補URLの収集が完了しました")
            self.signals.completed.emit({"count": len(candidates)})
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class BatchDraftWorker(QRunnable):
    def __init__(self, site_root: Path, urls: list[str], category: str, reply_count: str) -> None:
        super().__init__()
        self.site_root = site_root
        self.urls = urls
        self.category = category
        self.reply_count = reply_count
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            created: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []
            paused_reason = ""
            deferred_count = 0
            total = max(1, len(self.urls))
            for index, source_url in enumerate(self.urls, start=1):
                base = int((index - 1) * 100 / total)
                span = max(1, int(100 / total))

                def progress(value: int, message: str) -> None:
                    self.signals.progress.emit(min(99, base + int(value * span / 100)), f"{index}/{total} {message}")

                try:
                    payload = _generate_article_payload(
                        self.site_root, source_url, self.category, self.reply_count, progress
                    )
                    slug = save_draft(payload, self.site_root)
                    mark_candidate_status(self.site_root, source_url, "drafted", slug)
                    created.append({"slug": slug, "title": payload.get("title", ""), "source_url": source_url})
                except Exception as exc:
                    traceback.print_exc()
                    message = str(exc) or exc.__class__.__name__
                    transient = _is_transient_generation_error(message)
                    mark_candidate_status(
                        self.site_root,
                        source_url,
                        "new" if transient else "failed",
                        error=message,
                    )
                    failures.append({"source_url": source_url, "message": message[:500]})
                    if transient:
                        paused_reason = message[:500]
                        deferred_count = total - index + 1
                        self.signals.progress.emit(
                            min(99, base + span),
                            f"{index}/{total} 一時停止。残りは次回の巡回で再試行します",
                        )
                        break
                    self.signals.progress.emit(
                        min(99, base + span),
                        f"{index}/{total} 生成失敗。次の候補へ進みます",
                    )
            self.signals.progress.emit(100, "記事生成が完了しました")
            self.signals.completed.emit({
                "count": len(created),
                "items": created,
                "failed_count": len(failures),
                "failures": failures,
                "paused_reason": paused_reason,
                "deferred_count": deferred_count,
            })
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class RefineDraftWorker(QRunnable):
    def __init__(self, site_root: Path, payload: dict[str, Any]) -> None:
        super().__init__()
        self.site_root = site_root
        self.payload = payload
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            source_context = None
            source_url = str(self.payload.get("source_url") or "")
            if source_url:
                self.signals.progress.emit(10, "元ページを読み直しています")
                try:
                    source_context = capture_rendered_source(
                        source_url,
                        lambda value, message: self.signals.progress.emit(min(35, 10 + value // 3), message),
                    )
                except Exception:
                    traceback.print_exc()
            self.signals.progress.emit(40, "タイトルと会話の不自然さを点検しています")
            runner = CodexRunner(self.site_root)
            refined = runner.refine_existing(self.payload, source_context)
            self.signals.progress.emit(80, "人間らしい会話へ組み直しています")
            payload = apply_codex_result(self.payload, refined)
            slug = save_draft(payload, self.site_root)
            self.signals.progress.emit(100, "推敲が完了しました")
            self.signals.completed.emit({"slug": slug, "title": payload.get("title", "")})
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class PublishArticleWorker(QRunnable):
    def __init__(self, site_root: Path, payload: dict[str, Any], site: ManagedSite) -> None:
        super().__init__()
        self.site_root = site_root
        self.payload = payload
        self.site = site
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = publish_article(
                self.payload,
                self.site_root,
                self.site,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            self.signals.completed.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class UnpublishArticleWorker(QRunnable):
    def __init__(self, site_root: Path, payload: dict[str, Any], site: ManagedSite) -> None:
        super().__init__()
        self.site_root = site_root
        self.payload = payload
        self.site = site
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = unpublish_article(
                self.payload,
                self.site_root,
                self.site,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            self.signals.completed.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class DownloadVideoWorker(QRunnable):
    def __init__(self, site_root: Path, video: dict[str, Any]) -> None:
        super().__init__()
        self.site_root = site_root
        self.video = video
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            video_url = _validate_source_url(str(self.video.get("url") or ""))
            referer = str(self.video.get("referer") or "").strip()
            cache_root = self.site_root / ".article-studio" / "video-cache"
            cache_root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(f"{video_url}\n{referer}".encode("utf-8")).hexdigest()[:24]
            suffix = ".webm" if str(self.video.get("mime_type")) == "video/webm" else ".mp4"
            destination = cache_root / f"{digest}{suffix}"
            if destination.is_file() and destination.stat().st_size > 1024:
                self.signals.completed.emit({"path": str(destination), "cached": True})
                return
            if urlparse(video_url).path.lower().endswith(".mpd"):
                self.signals.progress.emit(5, "X動画の音声と映像を準備しています")
                materialized = destination.with_suffix(".stream.mp4")
                try:
                    _materialize_stream_video(video_url, materialized, referer)
                    if materialized.stat().st_size <= MAX_VIDEO_PROXY_BYTES:
                        materialized.replace(destination)
                    else:
                        _compress_video(materialized, destination)
                finally:
                    materialized.unlink(missing_ok=True)
                self.signals.progress.emit(100, "動画を再生します")
                self.signals.completed.emit({"path": str(destination), "cached": False})
                return

            headers = {
                "Accept": "video/mp4,video/webm,video/*;q=0.9",
                "User-Agent": "Mozilla/5.0 (IndanyaArticleStudio/2.0)",
            }
            if referer:
                headers["Referer"] = _validate_source_url(referer)
            request = urllib.request.Request(video_url, headers=headers)
            temporary = destination.with_suffix(destination.suffix + ".part")
            self.signals.progress.emit(5, "元サイトから動画を準備しています")
            try:
                response = urllib.request.urlopen(request, timeout=30)
                with response, temporary.open("wb") as output:
                    content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
                    if content_type not in {"video/mp4", "video/webm", "application/octet-stream"}:
                        raise RuntimeError("動画形式を確認できませんでした")
                    total = int(response.headers.get("Content-Length") or 0)
                    if total > MAX_VIDEO_PROXY_BYTES:
                        raise RuntimeError("動画が大きすぎます")
                    received = 0
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_VIDEO_PROXY_BYTES:
                            raise RuntimeError("動画が大きすぎます")
                        output.write(chunk)
                        percent = min(95, int(received * 100 / total)) if total else 40
                        self.signals.progress.emit(percent, f"動画を準備中 {received / 1024 / 1024:.1f} MB")
                if temporary.stat().st_size < 1024:
                    raise RuntimeError("動画データが空です")
                temporary.replace(destination)
            finally:
                if temporary.exists():
                    temporary.unlink(missing_ok=True)
            self.signals.progress.emit(100, "動画を再生します")
            self.signals.completed.emit({"path": str(destination), "cached": False})
        except (OSError, TimeoutError, socket.timeout, urllib.error.HTTPError, ValueError, RuntimeError) as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or "動画を取得できませんでした")
