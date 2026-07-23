from __future__ import annotations

import hashlib
import socket
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from article_studio import (
    MAX_VIDEO_PROXY_BYTES,
    CodexRunner,
    analyze_source_url,
    apply_codex_analysis,
    apply_codex_result,
    build_source_draft_payload,
    save_draft,
    _validate_source_url,
)
from indanya_desktop.publishing import publish_article, unpublish_article
from indanya_desktop.sites import ManagedSite
from indanya_desktop.browser_capture import capture_rendered_source
from indanya_desktop.automation import discover_candidates, mark_candidate_status


class WorkerSignals(QObject):
    progress = Signal(int, str)
    completed = Signal(dict)
    failed = Signal(str)



def _mark_ready_to_publish(payload: dict[str, Any]) -> dict[str, Any]:
    payload["rights_status"] = "confirmed"
    payload["adult_confirmed"] = True
    payload["rights_confirmed"] = True
    payload["privacy_confirmed"] = True
    payload["source_confirmed"] = True
    payload["review_status"] = "unreviewed"
    return payload


MAX_DESKTOP_SOURCE_IMAGES = 8
BAD_THUMBNAIL_TERMS = (
    "advert", "banner", "logo", "noimage", "ogp", "sns",
    "thumb", "thumbnail", "preview", "sample", "poster",
    "mosaic", "blur", "blurred", "censored",
    "広告", "バナー", "ロゴ", "サムネ", "サムネイル", "モザイク", "ぼかし",
)


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
            "body_ids": chosen[:MAX_DESKTOP_SOURCE_IMAGES],
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
    chosen = chosen[:MAX_DESKTOP_SOURCE_IMAGES]
    return {
        "thumbnail_id": chosen[0] if chosen else "",
        "body_ids": chosen,
    }


def _capture_and_analyze_source(
    site_root: Path,
    source_url: str,
    runner: CodexRunner,
    progress: Any = None,
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
            source = analyze_source_url(current_url)
        if navigation_context:
            source["navigation_context"] = navigation_context
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
    def __init__(self, site_root: Path, source_url: str, category: str, reply_count: str) -> None:
        super().__init__()
        self.site_root = site_root
        self.source_url = source_url
        self.category = category
        self.reply_count = reply_count
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            runner = CodexRunner(self.site_root)
            status = runner.status()
            if not status.get("available"):
                raise RuntimeError(status.get("message") or "Codexへ接続できません")
            source = _capture_and_analyze_source(
                self.site_root,
                self.source_url,
                runner,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            selected_videos = list(source.get("recommended_video_ids") or [])
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


def _generate_article_payload(site_root: Path, source_url: str, category: str, reply_count: str, progress: Any = None) -> dict[str, Any]:
    runner = CodexRunner(site_root)
    status = runner.status()
    if not status.get("available"):
        raise RuntimeError(status.get("message") or "Codexへ接続できません")
    source = _capture_and_analyze_source(site_root, source_url, runner, progress)
    selected_videos = list(source.get("recommended_video_ids") or [])
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
    return _mark_ready_to_publish(payload)


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
                    mark_candidate_status(self.site_root, source_url, "failed")
                    failures.append({"source_url": source_url, "message": message[:500]})
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
