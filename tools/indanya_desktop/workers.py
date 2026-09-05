from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urldefrag, urlparse

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from article_studio import (
    JST,
    MAX_VIDEO_PROXY_BYTES,
    CodexRunner,
    analyze_source_url,
    apply_codex_analysis,
    apply_codex_result,
    build_source_draft_payload,
    clean_article_topic_tags,
    save_draft,
    _validate_codex_result,
    _validate_source_url,
)
from indanya_desktop.publishing import (
    _compress_video,
    _materialize_stream_video,
    publish_fanza_affiliate_update,
    publish_article,
    unpublish_article,
)
from indanya_desktop.sites import ManagedSite
from indanya_desktop.browser_capture import (
    ChatGptRateLimitError,
    _media_url_key,
    _plausible_video_candidate,
    _sheet_attachments,
    capture_fanza_product_metadata,
    capture_rendered_source,
    discover_fanza_products,
    open_chatgpt_login_session,
    open_x_login_session,
    send_chatgpt_prompt,
)
from indanya_desktop.chatgpt_direct import (
    NonAdultSourceError,
    _merge_source_candidates,
    _merge_x_semantics,
    capture_and_analyze as capture_and_analyze_with_chatgpt,
    estimate_chatgpt_attachment_count,
    generate_article_text as generate_article_text_with_chatgpt,
    generate_article_text_batch as generate_article_text_batch_with_chatgpt,
    validate_single_pass_article,
)
from indanya_desktop.chatgpt_queue import (
    complete_chatgpt_request,
    fail_chatgpt_request,
    get_chatgpt_requests,
    mark_chatgpt_processing,
    record_chatgpt_event,
    skip_chatgpt_request,
    stop_pending_chatgpt_requests,
)
from indanya_desktop.social_x import (
    generate_x_copies,
    prepare_publish_x_post,
    refresh_x_trend_templates,
    run_x_daily_cycle,
    schedule_x_posts,
)
from indanya_desktop.automation import (
    discover_new_sources,
    discover_candidates,
    enqueue_article,
    list_candidates,
    remove_from_queue,
    soft_delete_article,
    update_review_status,
)
from indanya_desktop.editorial_policy import (
    FANZA_MEDIA_PROFILE,
    FANZA_TRANSPARENCY_NOTE,
    EditorialPolicyError,
    approve_generated_article,
    canonical_fanza_product_url,
    download_exact_fanza_package,
    fanza_product_id,
    is_fanza_product_url,
    require_publishable_article,
    restrict_source_to_fanza_product,
)
from indanya_desktop.fanza_affiliate import (
    load_fanza_settings,
    save_fanza_settings,
    unwrap_fanza_affiliate_url as _unwrap_external_affiliate_url,
)
from indanya_desktop.fanza_catalog import hydrate_related_fanza_products
from indanya_desktop.analytics import fetch_ga4_realtime, fetch_ga4_report
from indanya_desktop.sitemap_health import run_public_sitemap_health_check
from indanya_desktop.site_learning import (
    get_site_plan,
    learning_prompt_context,
    prioritize_source_media,
    record_site_outcome,
)
from indanya_desktop.affiliate_opportunities import (
    detect_affiliate_opportunities,
)
from indanya_desktop.related_links import (
    ensure_related_footer,
    related_link_insert_index,
    resolve_article_destinations,
    sanitize_related_destinations,
)
from indanya_desktop.related_thumbnail_assets import (
    apply_related_thumbnail_fallbacks,
    localize_related_thumbnail_assets,
)
from indanya_desktop.person_identity import (
    apply_verified_person_identity_to_payload,
    apply_verified_person_identity_to_source,
)
from indanya_desktop.visual_identity import (
    apply_known_visual_identity_matches,
    record_verified_visual_identities,
)
from indanya_desktop.social_profiles import (
    enrich_source_profile_thumbnails,
    merge_verified_social_profiles,
    registry_profiles_for_payload,
    resolve_identified_people_social_profiles,
    resolve_performer_social_profiles,
    resolve_subject_social_profiles,
)
from indanya_desktop.adaptive_quality import (
    apply_quality_gate,
    normalize_failure_code,
    record_processing_outcome,
)


def _record_site_outcome_safely(
    site_root: Path,
    url: str,
    outcome: str,
    **details: Any,
) -> None:
    try:
        record_site_outcome(site_root, url, outcome, **details)
    except Exception:
        # Learning must never turn a successfully saved article into a failure.
        traceback.print_exc()


def _record_adaptive_outcome_safely(
    site_root: Path,
    *,
    url: str,
    slug: str = "",
    outcome: str,
    stage: str,
    message: str = "",
    failure_code: str = "",
) -> None:
    try:
        record_processing_outcome(
            site_root,
            url=url,
            slug=slug,
            outcome=outcome,
            stage=stage,
            message=message,
            failure_code=failure_code,
        )
    except Exception:
        # Quality history is diagnostic data. It must never hide the original
        # article/save error from the operator.
        traceback.print_exc()


def _apply_adaptive_quality(
    site_root: Path,
    payload: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    report = apply_quality_gate(site_root, payload, source)
    if report.get("effective_decision") == "discard":
        _write_quality_block_diagnostic(site_root, payload, source, report)
        details = ", ".join(report.get("blockers") or report.get("warnings") or [])
        raise EditorialPolicyError(
            f"品質検査で記事化を中止しました: {details or '素材と記事の対応を確認できません'}"
        )
    return report


def _diagnostic_json_value(value: Any, *, depth: int = 0) -> Any:
    """Keep quality evidence readable without duplicating large media blobs."""
    if depth > 8:
        return "<max-depth>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str):
        if value.startswith("data:"):
            return f"<data-url:{len(value)}>"
        return value[:12000]
    if isinstance(value, dict):
        omitted = {"data", "frame_data", "page_screenshot", "html", "raw_html"}
        return {
            str(key): _diagnostic_json_value(item, depth=depth + 1)
            for key, item in value.items()
            if str(key) not in omitted
        }
    if isinstance(value, (list, tuple)):
        return [
            _diagnostic_json_value(item, depth=depth + 1)
            for item in list(value)[:250]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]


def _write_quality_block_diagnostic(
    site_root: Path,
    payload: dict[str, Any],
    source: dict[str, Any],
    report: dict[str, Any],
) -> None:
    try:
        root = site_root / ".article-studio" / "quality-blocks"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        slug = re.sub(r"[^a-z0-9-]+", "-", str(payload.get("slug") or "article").casefold()).strip("-")
        record = {
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_url": str(source.get("requested_url") or source.get("url") or ""),
            "report": _diagnostic_json_value(report),
            "payload": _diagnostic_json_value(payload),
            "source": _diagnostic_json_value(source),
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        path = root / f"{stamp}-{slug or 'article'}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
        latest = root / "latest.json"
        latest_temporary = latest.with_suffix(".tmp")
        latest_temporary.write_text(encoded, encoding="utf-8")
        latest_temporary.replace(latest)
    except Exception:
        traceback.print_exc()


class WorkerSignals(QObject):
    progress = Signal(int, str)
    article_saved = Signal(dict)
    completed = Signal(dict)
    failed = Signal(str)


_CHATGPT_AUTOMATION_LOCK = threading.Lock()


def _selected_generation_videos(source: dict[str, Any]) -> list[str]:
    """Keep official FANZA sample players from being dropped by AI scoring."""
    if str(source.get("source_type") or "") == "fanza_product":
        return [
            str(item.get("id"))
            for item in (source.get("videos") or [])
            if isinstance(item, dict) and item.get("id")
        ]
    return [str(item) for item in source.get("recommended_video_ids") or [] if item]


class ReviewActionWorker(QRunnable):
    def __init__(self, site_root: Path, action: str, slug: str) -> None:
        super().__init__()
        self.site_root = site_root
        self.action = action
        self.slug = slug
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            position = 0
            status = "unreviewed"
            if self.action == "queue":
                position = enqueue_article(self.site_root, self.slug)
                status = "queued"
            elif self.action == "dequeue":
                remove_from_queue(self.site_root, self.slug)
            elif self.action == "delete":
                soft_delete_article(self.site_root, self.slug)
                status = "deleted"
            elif self.action == "restore":
                update_review_status(self.site_root, self.slug, "unreviewed")
            elif self.action == "fail":
                remove_from_queue(self.site_root, self.slug, "failed")
                status = "failed"
            else:
                raise ValueError("未対応のボード操作です")
            self.signals.completed.emit({
                "slug": self.slug,
                "action": self.action,
                "status": status,
                "position": position,
            })
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class AnalyticsWorker(QRunnable):
    def __init__(self, site_root: Path, mode: str = "historical", days: int = 7) -> None:
        super().__init__()
        self.site_root = site_root
        self.mode = mode
        self.days = max(1, min(365, int(days)))
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == "realtime":
                result = fetch_ga4_realtime(self.site_root)
            else:
                result = fetch_ga4_report(
                    self.site_root,
                    start_date=f"{self.days - 1}daysAgo",
                )
            self.signals.completed.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class SitemapHealthWorker(QRunnable):
    def __init__(self, site_root: Path, public_url: str) -> None:
        super().__init__()
        self.site_root = site_root
        self.public_url = public_url
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(
                run_public_sitemap_health_check(self.site_root, self.public_url)
            )
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class ApplyFanzaAffiliateWorker(QRunnable):
    def __init__(
        self,
        site_root: Path,
        site: ManagedSite,
        affiliate_id_or_link: str,
    ) -> None:
        super().__init__()
        self.site_root = site_root
        self.site = site
        self.affiliate_id_or_link = affiliate_id_or_link
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            affiliate_id = save_fanza_settings(
                self.site_root, self.affiliate_id_or_link
            )
            result = publish_fanza_affiliate_update(
                self.site_root,
                self.site,
                affiliate_id,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            result["affiliate_id"] = affiliate_id
            self.signals.completed.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


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


class XCopyWorker(QRunnable):
    def __init__(self, site_root: Path, post_ids: list[str]) -> None:
        super().__init__()
        self.site_root = site_root
        self.post_ids = list(post_ids)
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            rows = generate_x_copies(
                self.site_root,
                self.post_ids,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            self.signals.completed.emit({"posts": rows})
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class XTrendWorker(QRunnable):
    def __init__(self, site_root: Path, force: bool = False) -> None:
        super().__init__()
        self.site_root = site_root
        self.force = force
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            state = refresh_x_trend_templates(
                self.site_root,
                force=self.force,
                progress=lambda value, message: self.signals.progress.emit(value, message),
            )
            self.signals.completed.emit(state)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class XScheduleWorker(QRunnable):
    def __init__(self, site_root: Path, post_ids: list[str]) -> None:
        super().__init__()
        self.site_root = site_root
        self.post_ids = list(post_ids)
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.completed.emit(schedule_x_posts(
                self.site_root,
                self.post_ids,
                lambda value, message: self.signals.progress.emit(value, message),
            ))
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class XDailyWorker(QRunnable):
    def __init__(self, site_root: Path, public_url: str) -> None:
        super().__init__()
        self.site_root = site_root
        self.public_url = public_url
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = run_x_daily_cycle(
                self.site_root,
                self.public_url,
                lambda value, message: self.signals.progress.emit(value, message),
            )
            self.signals.completed.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class ChatGptLoginWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            open_chatgpt_login_session(
                lambda value, message: self.signals.progress.emit(value, message)
            )
            self.signals.completed.emit({"status": "ready"})
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class ChatGptSendWorker(QRunnable):
    def __init__(self, site_root: Path, request_ids: list[str]) -> None:
        super().__init__()
        self.site_root = site_root
        # Every entry point uses this worker. Keep one article in flight even
        # if a manual action passed multiple URLs.
        self.request_ids = list(request_ids[:1])
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        with _CHATGPT_AUTOMATION_LOCK:
            self._run_serialized()

    def _run_serialized(self) -> None:
        progress = lambda value, message: self.signals.progress.emit(value, message)
        completed: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        rate_limited = False
        requests = get_chatgpt_requests(self.site_root, self.request_ids)
        for chunk_start in range(0, len(requests), 3):
            chunk = requests[chunk_start:chunk_start + 3]
            prepared: list[dict[str, Any]] = []
            for offset, request in enumerate(chunk, start=1):
                index = chunk_start + offset
                request_id = str(request.get("request_id") or "")
                source_url = str(request.get("url") or "")
                options = (
                    dict(request.get("options"))
                    if isinstance(request.get("options"), dict)
                    else {}
                )
                attempt_started = time.monotonic()
                conversation: dict[str, str] = {}
                try:
                    mark_chatgpt_processing(self.site_root, request_id)
                    is_fanza = is_fanza_product_url(source_url)
                    record_chatgpt_event(
                        self.site_root,
                        request_id,
                        "material",
                        (
                            "公式商品テンプレで素材を確認しています（ChatGPT送信前）"
                            if is_fanza
                            else "ChatGPT送信 1回：素材判定と完成記事をまとめて作成しています"
                        ),
                    )
                    progress(3, f"{index}/{len(requests)}件目のページ素材を集めています")
                    source = capture_and_analyze_with_chatgpt(
                        self.site_root,
                        source_url,
                        request_id,
                        progress,
                        options,
                        conversation,
                    )
                    _attach_verified_fanza_products(source, self.site_root, progress)
                    image_selection = _select_article_images(source)
                    thumbnail_id = str(image_selection["thumbnail_id"])
                    body_image_ids = list(image_selection["body_ids"])
                    if not thumbnail_id:
                        raise RuntimeError("本文またはサムネイルに使える画像が見つかりませんでした")
                    selected_videos = _selected_generation_videos(source)
                    generation_options = {
                        "category": str(options.get("category") or "auto"),
                        "reply_count": str(options.get("reply_count") or "auto"),
                        "selected_image_ids": list(dict.fromkeys(
                            [thumbnail_id, *body_image_ids]
                        )),
                        "selected_video_ids": selected_videos,
                    }
                    single_pass_generated = None
                    single_pass_error = ""
                    if not is_fanza:
                        try:
                            single_pass_generated = validate_single_pass_article(
                                source, generation_options
                            )
                        except Exception as exc:
                            single_pass_error = str(exc) or exc.__class__.__name__
                    prepared.append({
                        "request_id": request_id,
                        "source_url": source_url,
                        "source": source,
                        "request_options": options,
                        "options": generation_options,
                        "thumbnail_id": thumbnail_id,
                        "body_image_ids": body_image_ids,
                        "selected_videos": selected_videos,
                        "conversation": conversation,
                        "attempt_started": attempt_started,
                        "learning_plan": get_site_plan(self.site_root, source_url),
                        "learning_stage": "generation",
                        "single_pass_generated": single_pass_generated,
                        "single_pass_error": single_pass_error,
                    })
                except ChatGptRateLimitError as exc:
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        "deferred",
                        stage="material",
                        message=str(exc),
                        elapsed_seconds=time.monotonic() - attempt_started,
                    )
                    stop_pending_chatgpt_requests(
                        self.site_root,
                        "ChatGPTの利用制限のため今回の候補を停止しました。次回巡回で新しい候補を取得します",
                    )
                    rate_limited = True
                    break
                except (NonAdultSourceError, EditorialPolicyError) as exc:
                    message = str(exc) or "成人向けではないため対象外にしました"
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        "skipped",
                        stage="material",
                        message=message,
                        elapsed_seconds=time.monotonic() - attempt_started,
                    )
                    skip_chatgpt_request(self.site_root, request_id, message)
                    skipped.append({"request_id": request_id, "url": source_url, "message": message})
                except Exception as exc:
                    traceback.print_exc()
                    message = str(exc) or exc.__class__.__name__
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        "failure",
                        stage="material",
                        message=message,
                        elapsed_seconds=time.monotonic() - attempt_started,
                    )
                    fail_chatgpt_request(self.site_root, request_id, message)
                    failures.append({"request_id": request_id, "url": source_url, "message": message})

            if rate_limited:
                progress(100, "ChatGPTの利用制限のため今回の候補を停止しました")
                break
            if not prepared:
                continue
            for entry in prepared:
                entry["learning_stage"] = "generation"
                record_chatgpt_event(
                    self.site_root,
                    str(entry["request_id"]),
                    "generation",
                    (
                        "ChatGPT返答 1/1：素材判定と完成記事を受け取りました"
                        if entry.get("single_pass_generated")
                        else (
                            "ChatGPT送信 1/1：タイトルとレスを作成しています"
                            if is_fanza_product_url(str(entry["source_url"]))
                            else "完成記事が不足したため、この1件だけ追加生成しています"
                        )
                    ),
                )
            progress(58, f"{len(prepared)}件分の記事を作成・推敲しています")
            batch_generated: dict[str, dict[str, Any]] = {
                str(entry["request_id"]): dict(entry["single_pass_generated"])
                for entry in prepared
                if isinstance(entry.get("single_pass_generated"), dict)
            }
            generation_errors: dict[str, str] = {
                str(entry["request_id"]): (
                    str(entry.get("single_pass_error") or "")
                    or "1回目の返答に完成記事が含まれていませんでした"
                )
                for entry in prepared
                if not entry.get("single_pass_generated")
                and not is_fanza_product_url(str(entry.get("source_url") or ""))
            }
            generation_entries = [
                entry for entry in prepared
                if not entry.get("single_pass_generated")
                and is_fanza_product_url(str(entry.get("source_url") or ""))
            ]
            attachment_count = sum(
                estimate_chatgpt_attachment_count(entry["source"], entry["options"])
                for entry in generation_entries
            )
            # A FANZA gallery can contain many official introduction images.
            # Keep each product in its own ChatGPT request when a combined batch
            # would exceed the browser attachment budget; this also prevents
            # product identities and scenes from bleeding between articles.
            if len(generation_entries) == 1 or attachment_count > 18:
                for entry in generation_entries:
                    try:
                        generated = generate_article_text_with_chatgpt(
                            self.site_root,
                            entry["source"],
                            entry["options"],
                            progress,
                            entry["conversation"],
                        )
                        batch_generated[str(entry["request_id"])] = generated
                    except ChatGptRateLimitError as exc:
                        stop_pending_chatgpt_requests(
                            self.site_root,
                            "ChatGPTの利用制限のため今回の候補を停止しました。次回巡回で新しい候補を取得します",
                        )
                        rate_limited = True
                        break
                    except Exception:
                        traceback.print_exc()
                        generation_errors[str(entry["request_id"])] = (
                            "ChatGPTの1回の返答から記事を作成できませんでした"
                        )
            elif generation_entries:
                try:
                    batch_result = generate_article_text_batch_with_chatgpt(
                        self.site_root, generation_entries, progress
                    )
                    batch_generated = dict(batch_result.get("generated") or {})
                except ChatGptRateLimitError as exc:
                    stop_pending_chatgpt_requests(
                        self.site_root,
                        "ChatGPTの利用制限のため今回の候補を停止しました。次回巡回で新しい候補を取得します",
                    )
                    rate_limited = True
                except Exception:
                    traceback.print_exc()
                    for entry in generation_entries:
                        generation_errors[str(entry["request_id"])] = (
                            "ChatGPTの1回の返答から記事を作成できませんでした"
                        )

            if rate_limited:
                progress(100, "ChatGPTの利用制限のため今回の候補を停止しました")
                break

            for entry in prepared:
                request_id = str(entry["request_id"])
                source_url = str(entry["source_url"])
                generated = batch_generated.get(request_id)
                try:
                    if not generated:
                        raise RuntimeError(
                            generation_errors.get(request_id)
                            or "ChatGPTの1回の返答から記事を作成できませんでした"
                        )
                    record_chatgpt_event(
                        self.site_root,
                        request_id,
                        "save",
                        "画像・動画との対応を検査して公開待機へ保存しています",
                    )
                    result = _save_chatgpt_generated_article(
                        self.site_root, entry, generated, progress
                    )
                    source = dict(entry["source"])
                    quality = dict(result.get("quality_gate") or {})
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        (
                            "success"
                            if quality.get("recommendation") == "auto_ready"
                            else "deferred"
                        ),
                        stage=(
                            "save"
                            if quality.get("recommendation") == "auto_ready"
                            else "quality_review"
                        ),
                        message=" / ".join(
                            quality.get("blockers") or quality.get("warnings") or []
                        ),
                        strategy=str(source.get("capture_strategy") or "browser_full"),
                        elapsed_seconds=time.monotonic() - float(entry["attempt_started"]),
                        source=source,
                        selected_image_ids=list(dict.fromkeys([
                            str(entry.get("thumbnail_id") or ""),
                            *list(entry.get("body_image_ids") or []),
                        ])),
                        selected_video_ids=list(entry.get("selected_videos") or []),
                    )
                    completed.append(result)
                    _record_adaptive_outcome_safely(
                        self.site_root,
                        url=source_url,
                        slug=str(result.get("slug") or ""),
                        outcome=(
                            "success"
                            if quality.get("recommendation") == "auto_ready"
                            else "review"
                        ),
                        stage="chatgpt_save",
                        message=" / ".join(
                            quality.get("blockers") or quality.get("warnings") or []
                        ),
                    )
                    self.signals.article_saved.emit({
                        **result,
                        "completed_in_batch": len(completed),
                        "batch_size": len(requests),
                    })
                except ChatGptRateLimitError as exc:
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        "deferred",
                        stage=str(entry.get("learning_stage") or "generation"),
                        message=str(exc),
                        strategy=str(entry.get("source", {}).get("capture_strategy") or "browser_full"),
                        elapsed_seconds=time.monotonic() - float(entry["attempt_started"]),
                    )
                    stop_pending_chatgpt_requests(
                        self.site_root,
                        "ChatGPTの利用制限のため今回の候補を停止しました。次回巡回で新しい候補を取得します",
                    )
                    rate_limited = True
                    break
                except Exception as exc:
                    traceback.print_exc()
                    message = str(exc) or exc.__class__.__name__
                    _record_site_outcome_safely(
                        self.site_root,
                        source_url,
                        "failure",
                        stage=str(entry.get("learning_stage") or "generation"),
                        message=message,
                        strategy=str(entry.get("source", {}).get("capture_strategy") or "browser_full"),
                        elapsed_seconds=time.monotonic() - float(entry["attempt_started"]),
                    )
                    fail_chatgpt_request(self.site_root, request_id, message)
                    _record_adaptive_outcome_safely(
                        self.site_root,
                        url=source_url,
                        outcome="failure",
                        stage=str(entry.get("learning_stage") or "generation"),
                        message=message,
                        failure_code=normalize_failure_code(
                            "", message=message,
                            stage=str(entry.get("learning_stage") or "generation"),
                        ),
                    )
                    failures.append({"request_id": request_id, "url": source_url, "message": message})
            if rate_limited:
                progress(100, "ChatGPTの利用制限のため今回の候補を停止しました")
                break
        progress(100, "ChatGPTの記事処理が完了しました")
        self.signals.completed.emit({
            "count": len(completed),
            "completed": completed,
            "failed": failures,
            "skipped": skipped,
            "rate_limited": rate_limited,
        })


class CodexSendWorker(QRunnable):
    """Process one queued URL through deterministic capture and one Codex call."""

    def __init__(self, site_root: Path, request_ids: list[str]) -> None:
        super().__init__()
        self.site_root = site_root
        self.request_ids = list(request_ids[:1])
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        with _CHATGPT_AUTOMATION_LOCK:
            self._run_serialized()

    def _run_serialized(self) -> None:
        progress = lambda value, message: self.signals.progress.emit(value, message)
        completed: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        rate_limited = False
        requests = get_chatgpt_requests(self.site_root, self.request_ids)
        for request in requests:
            request_id = str(request.get("request_id") or "")
            source_url = str(request.get("url") or "")
            options = dict(request.get("options") or {})
            attempt_started = time.monotonic()
            try:
                mark_chatgpt_processing(self.site_root, request_id)
                record_chatgpt_event(
                    self.site_root,
                    request_id,
                    "material",
                    "プログラムで素材を回収し、Codexへ送る前に不足を検査しています",
                )
                progress(4, "ページ素材を回収しています")
                payload = _generate_article_payload(
                    self.site_root,
                    source_url,
                    str(options.get("category") or "auto"),
                    str(options.get("reply_count") or "auto"),
                    progress,
                    options,
                )
                payload["generation_method"] = "codex_one_pass"
                payload["chatgpt_request_id"] = request_id
                if options.get("force_duplicate"):
                    _assign_duplicate_slug(payload, request_id)
                record_chatgpt_event(
                    self.site_root,
                    request_id,
                    "save",
                    "採用素材IDと公開形式をプログラムで検査して保存しています",
                )
                progress(92, "検査済みの記事を公開前ボードへ保存しています")
                slug = save_draft(payload, self.site_root)
                queue_position = 0
                quality = dict(payload.get("quality_gate") or {})
                if (
                    options.get("automation_origin") == "crawl"
                    and quality.get("effective_decision") == "auto_ready"
                ):
                    queue_position = enqueue_article(self.site_root, slug)
                complete_chatgpt_request(self.site_root, request_id, slug)
                result = {
                    "request_id": request_id,
                    "slug": slug,
                    "title": str(payload.get("title") or ""),
                    "queue_position": queue_position,
                    "completed_in_batch": 1,
                    "batch_size": 1,
                    "quality_gate": quality,
                }
                completed.append(result)
                _record_site_outcome_safely(
                    self.site_root,
                    source_url,
                    (
                        "success"
                        if quality.get("recommendation") == "auto_ready"
                        else "deferred"
                    ),
                    stage=(
                        "save"
                        if quality.get("recommendation") == "auto_ready"
                        else "quality_review"
                    ),
                    message=" / ".join(
                        quality.get("blockers") or quality.get("warnings") or []
                    ),
                    strategy="codex_one_pass",
                    elapsed_seconds=time.monotonic() - attempt_started,
                    source={
                        "images": payload.get("images") or [],
                        "videos": payload.get("videos") or [],
                        "text_blocks": payload.get("blocks") or [],
                        "source_chain": payload.get("source_chain") or [],
                    },
                    selected_image_ids=[
                        str(item.get("id") or "")
                        for item in payload.get("images") or []
                        if isinstance(item, dict) and item.get("id")
                    ],
                    selected_video_ids=[
                        str(item.get("id") or "")
                        for item in payload.get("videos") or []
                        if isinstance(item, dict) and item.get("id")
                    ],
                )
                _record_adaptive_outcome_safely(
                    self.site_root,
                    url=source_url,
                    slug=slug,
                    outcome=(
                        "success"
                        if quality.get("recommendation") == "auto_ready"
                        else "review"
                    ),
                    stage="codex_save",
                    message=" / ".join(
                        quality.get("blockers") or quality.get("warnings") or []
                    ),
                )
                self.signals.article_saved.emit(result)
            except (NonAdultSourceError, EditorialPolicyError) as exc:
                message = str(exc) or "記事化の対象外です"
                skip_chatgpt_request(self.site_root, request_id, message)
                _record_site_outcome_safely(
                    self.site_root,
                    source_url,
                    "skipped",
                    stage="codex_review",
                    message=message,
                    strategy="codex_one_pass",
                    elapsed_seconds=time.monotonic() - attempt_started,
                )
                skipped.append({"request_id": request_id, "url": source_url, "message": message})
            except Exception as exc:
                traceback.print_exc()
                message = str(exc) or exc.__class__.__name__
                if "Codexの利用上限" in message or "usage limit" in message.casefold():
                    stop_pending_chatgpt_requests(
                        self.site_root,
                        "Codexの利用上限のため今回の候補を停止しました。次回巡回で新しい候補を取得します",
                    )
                    rate_limited = True
                else:
                    fail_chatgpt_request(self.site_root, request_id, message)
                    failures.append({"request_id": request_id, "url": source_url, "message": message})
                _record_site_outcome_safely(
                    self.site_root,
                    source_url,
                    "deferred" if rate_limited else "failure",
                    stage="codex_or_save",
                    message=message,
                    strategy="codex_one_pass",
                    elapsed_seconds=time.monotonic() - attempt_started,
                )
                _record_adaptive_outcome_safely(
                    self.site_root,
                    url=source_url,
                    outcome="deferred" if rate_limited else "failure",
                    stage="codex_or_save",
                    message=message,
                    failure_code=normalize_failure_code(
                        "", message=message, stage="codex_or_save"
                    ),
                )
        progress(100, "Codexの記事処理が完了しました")
        self.signals.completed.emit({
            "count": len(completed),
            "completed": completed,
            "failed": failures,
            "skipped": skipped,
            "rate_limited": rate_limited,
        })


def _save_chatgpt_generated_article(
    site_root: Path,
    entry: dict[str, Any],
    generated: dict[str, Any],
    progress: Any,
) -> dict[str, Any]:
    request_id = str(entry["request_id"])
    source = dict(entry["source"])
    entry["learning_stage"] = "save"
    options = dict(entry["request_options"])
    generation_options = dict(entry["options"])
    selected_videos = list(entry["selected_videos"])
    requested_category = str(options.get("category") or "auto")
    if requested_category != "auto":
        generated["category"] = requested_category
    base = build_source_draft_payload(
        source,
        list(entry["body_image_ids"]),
        None,
        selected_videos,
        thumbnail_image_id=str(entry["thumbnail_id"]),
    )
    payload_video_ids = {
        source_video_id: f"source-video-{video_index}"
        for video_index, source_video_id in enumerate(selected_videos, start=1)
    }
    for response in generated["responses"]:
        response["video_ids"] = [
            payload_video_ids[video_id]
            for video_id in response.get("video_ids", [])
            if video_id in payload_video_ids
        ]
    payload = apply_codex_result(base, generated)
    _place_videos_at_start(payload)
    payload["generation_method"] = "chatgpt_direct_batched"
    payload["chatgpt_request_id"] = request_id
    learning_plan = dict(entry.get("learning_plan") or {})
    payload["site_capture_strategy"] = str(source.get("capture_strategy") or "browser_full")
    payload["site_learning_maturity"] = str(learning_plan.get("maturity") or "未学習")
    _apply_editorial_metadata(payload, source, options, site_root)
    _mark_ready_to_publish(payload, source)
    payload["source_chain"] = list(source.get("source_chain") or [])
    payload["navigation_trace"] = list(source.get("navigation_trace") or [])
    quality = _apply_adaptive_quality(site_root, payload, source)
    if options.get("force_duplicate"):
        _assign_duplicate_slug(payload, request_id)
    progress(92, "検査済みの記事を公開前ボードへ保存しています")
    slug = save_draft(payload, site_root)
    queue_position = 0
    if (
        options.get("automation_origin") == "crawl"
        and quality.get("effective_decision") == "auto_ready"
    ):
        queue_position = enqueue_article(site_root, slug)
    complete_chatgpt_request(site_root, request_id, slug)
    return {
        "request_id": request_id,
        "slug": slug,
        "title": str(payload.get("title") or ""),
        "queue_position": queue_position,
        "quality_gate": quality,
    }


def _assign_duplicate_slug(payload: dict[str, Any], marker: str) -> None:
    base = re.sub(r"[^a-z0-9-]+", "-", str(payload.get("slug") or "article").lower())
    base = base.strip("-") or "article"
    suffix = re.sub(r"[^a-z0-9]+", "", marker.lower())[:8] or hashlib.sha256(
        marker.encode("utf-8")
    ).hexdigest()[:8]
    payload["slug"] = f"{base[:87].rstrip('-')}-dup-{suffix}"
    payload["replace_existing"] = False


def _place_videos_at_start(payload: dict[str, Any]) -> None:
    """Keep official samples attached to the opening post, never to later reactions."""
    video_ids = [
        str(video.get("id") or "")
        for video in payload.get("videos", [])
        if isinstance(video, dict) and video.get("id")
    ]
    responses = payload.get("responses")
    if not video_ids or not isinstance(responses, list) or not responses:
        return
    known_ids = set(video_ids)
    for response in responses:
        if isinstance(response, dict):
            response["video_ids"] = [
                str(video_id)
                for video_id in response.get("video_ids", [])
                if str(video_id) not in known_ids
            ]
    first = responses[0]
    if isinstance(first, dict):
        first["video_ids"] = video_ids
    payload["category"] = "動画"



def _mark_ready_to_publish(
    payload: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source is None:
        raise RuntimeError("成人向け掲載基準の判定元データがありません")
    approve_generated_article(source, payload)
    payload["rights_status"] = "confirmed"
    payload["adult_confirmed"] = True
    payload["rights_confirmed"] = True
    payload["privacy_confirmed"] = True
    payload["source_confirmed"] = True
    payload["review_status"] = "unreviewed"
    # Automatic crawl items must satisfy the same final checks as a publish
    # operation before they are allowed onto the review or scheduling board.
    require_publishable_article(payload)
    return payload


BAD_THUMBNAIL_TERMS = (
    "advert", "banner", "logo", "noimage", "ogp", "sns",
    "thumb", "thumbnail", "preview", "sample", "poster",
    "mosaic", "blur", "blurred", "censored",
    "広告", "バナー", "ロゴ", "サムネ", "サムネイル", "モザイク", "ぼかし",
)

def _fanza_product_kind(source: dict[str, Any]) -> str:
    text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "body_text", "category")
    )
    text += " " + " ".join(
        str(tag) for tag in (source.get("tags") or []) if isinstance(tag, str)
    )
    if re.search(r"漫画|マンガ|コミック|成人漫画|電子書籍", text, re.I):
        return "comic"
    if re.search(r"同人|CG集|イラスト集|同人ゲーム|美少女ゲーム", text, re.I):
        return "doujin"
    if re.search(r"アニメ|二次元|2次元|animation|anime", text, re.I):
        return "anime"
    return "video"


def _normalized_fanza_product_code(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _product_matches_exact_code(product: dict[str, Any], product_code: str) -> bool:
    expected = _normalized_fanza_product_code(product_code)
    if len(expected) < 5:
        return False
    product_id = _normalized_fanza_product_code(str(product.get("product_id") or ""))
    if product_id == expected:
        return True
    url_product_id = _normalized_fanza_product_code(
        fanza_product_id(str(product.get("url") or ""))
    )
    return url_product_id == expected


def _fanza_url_matches_exact_code(product_url: str, product_code: str) -> bool:
    expected = _normalized_fanza_product_code(product_code)
    actual = _normalized_fanza_product_code(fanza_product_id(product_url))
    return bool(len(expected) >= 5 and actual == expected)


def _official_fanza_package_url(
    source: dict[str, Any],
    destination: str,
    source_image_ids: list[str] | None = None,
    discovered_thumbnail: str = "",
) -> str:
    product_id = _normalized_fanza_product_code(fanza_product_id(destination))
    allowed_ids = {str(value) for value in source_image_ids or [] if str(value)}

    def score(url: str, item: dict[str, Any] | None = None) -> int:
        try:
            parsed = urlparse(url)
        except ValueError:
            return -1000
        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold()
        if not (
            host == "pics.dmm.co.jp"
            or host.endswith(".pics.dmm.co.jp")
            or host == "p.dmm.co.jp"
            or host.endswith(".p.dmm.co.jp")
        ):
            return -1000
        value = 30
        if product_id and product_id in _normalized_fanza_product_code(path):
            value += 50
        if re.search(r"(?:pl|ps|pr|package|jacket)\.(?:jpe?g|png|webp)$", path):
            value += 80
        if item:
            if str(item.get("rights_basis") or "") == "fanza_product_main_image":
                value += 100
            text = " ".join(
                str(item.get(key) or "")
                for key in ("alt", "title", "browser_context")
            ).casefold()
            if re.search(r"package|jacket|パッケージ|商品画像", text):
                value += 60
            if item.get("thumbnail_only_candidate"):
                value -= 100
        return value

    ranked: list[tuple[int, str]] = []
    if discovered_thumbnail:
        ranked.append((score(discovered_thumbnail), discovered_thumbnail))
    for item in source.get("images") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if allowed_ids and item_id not in allowed_ids and str(item.get("rights_basis") or "") != "fanza_product_main_image":
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if url:
            ranked.append((score(url, item), url))
    if not ranked:
        return ""
    best_score, best_url = max(ranked, key=lambda value: value[0])
    return best_url[:2048] if best_score >= 80 else ""


def _rect_position(item: dict[str, Any]) -> tuple[float, float] | None:
    rect = item.get("browser_rect") or {}
    try:
        top = float(rect.get("y"))
        height = max(0.0, float(rect.get("height") or 0))
    except (TypeError, ValueError):
        return None
    if top < 0:
        return None
    return top, top + height


def _selected_source_media_for_product(
    source: dict[str, Any],
) -> tuple[list[str], list[str], float | None, float | None]:
    """Return selected article media and the bottom of its visible gallery."""
    images = {
        str(item.get("id") or ""): item
        for item in source.get("images") or []
        if isinstance(item, dict) and item.get("id")
    }
    videos = {
        str(item.get("id") or ""): item
        for item in source.get("videos") or []
        if isinstance(item, dict) and item.get("id")
    }
    image_ids = list(dict.fromkeys(
        str(media_id)
        for key in (
            "recommended_thumbnail_ids",
            "recommended_body_image_ids",
            "recommended_image_ids",
        )
        for media_id in source.get(key) or []
        if str(media_id) in images
        and not _is_recommendation_material(images[str(media_id)])
    ))
    video_ids = list(dict.fromkeys(
        str(media_id)
        for media_id in source.get("recommended_video_ids") or []
        if str(media_id) in videos
        and not _is_recommendation_material(videos[str(media_id)])
    ))
    if not image_ids:
        image_ids = [
            media_id
            for media_id, item in images.items()
            if not _is_recommendation_material(item)
            and (
                bool(item.get("ai_recommended"))
                or str(item.get("ai_role") or "")
                in {"article_thumbnail", "article_main", "article_gallery"}
            )
        ]
    if not video_ids:
        video_ids = [
            media_id
            for media_id, item in videos.items()
            if not _is_recommendation_material(item)
            and (
                bool(item.get("ai_recommended"))
                or str(item.get("ai_role") or "")
                in {"article_main", "article_gallery", "article_video"}
            )
        ]
    positions = [
        position
        for media_id in [*image_ids, *video_ids]
        for item in [images.get(media_id) or videos.get(media_id) or {}]
        for position in [_rect_position(item)]
        if position is not None
    ]
    return (
        image_ids,
        video_ids,
        max((position[0] for position in positions), default=None),
        max((position[1] for position in positions), default=None),
    )


_MEDIA_PRODUCT_CODE_RE = re.compile(
    r"(?<![a-z0-9])([a-z]{2,12})[-_](\d{3,6})(?!\d|[xX]\d)",
    re.I,
)
_NON_PRODUCT_MEDIA_PREFIXES = {
    "ad", "advert", "archive", "banner", "blog", "content", "detail",
    "entry", "fc", "file", "image", "images", "img", "imgs", "item",
    "media", "page", "photo", "post", "sample", "sns", "thumb",
    "thumbnail", "wp",
}


def _infer_single_media_filename_fanza_product(
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Use a unique explicit product code from selected article media only."""
    if any(
        _is_fanza_product_url(str(source.get(key) or ""))
        for key in ("requested_url", "url")
    ):
        return []
    image_ids, video_ids, _, _ = _selected_source_media_for_product(source)
    selected = {
        str(item.get("id") or ""): ("image", item)
        for item in source.get("images") or []
        if isinstance(item, dict) and str(item.get("id") or "") in image_ids
    }
    selected.update({
        str(item.get("id") or ""): ("video", item)
        for item in source.get("videos") or []
        if isinstance(item, dict) and str(item.get("id") or "") in video_ids
    })
    matches: dict[str, dict[str, Any]] = {}
    for media_id, (media_kind, item) in selected.items():
        values = " ".join(
            str(item.get(key) or "")
            for key in ("url", "source_url", "poster", "thumbnail_url")
        )
        media_codes: set[str] = set()
        for match in _MEDIA_PRODUCT_CODE_RE.finditer(values):
            prefix = match.group(1).casefold()
            if (
                prefix in _NON_PRODUCT_MEDIA_PREFIXES
                or set(match.group(2)) == {"0"}
            ):
                continue
            media_codes.add(f"{prefix.upper()}-{match.group(2)}")
        if len(media_codes) != 1:
            continue
        code = next(iter(media_codes))
        entry = matches.setdefault(code, {"image_ids": [], "video_ids": []})
        entry[f"{media_kind}_ids"].append(media_id)
    if len(matches) != 1:
        return []
    product_code, media = next(iter(matches.items()))
    return [{
        "product_title": product_code,
        "product_code": product_code,
        "product_url": "",
        "image_ids": media["image_ids"],
        "video_ids": media["video_ids"],
        "reason": "本文採用メディアのファイル名にある唯一の品番を完全一致照合",
    }]


def _infer_single_embedded_fanza_product(
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Recover one exact work CTA placed immediately after the article gallery.

    Some roundup pages put the work title and FANZA link below all article
    images. Codex can occasionally omit that mapping even though the evidence
    is deterministic. This fallback deliberately requires a unique product,
    the same named subject, article-body placement, and gallery proximity.
    """
    if any(
        _is_fanza_product_url(str(source.get(key) or ""))
        for key in ("requested_url", "url")
    ):
        return []
    image_ids, video_ids, media_last_top, media_bottom = (
        _selected_source_media_for_product(source)
    )
    if (
        not (image_ids or video_ids)
        or media_last_top is None
        or media_bottom is None
    ):
        return []
    subject = source.get("ai_main_subject") or {}
    subject_name = (
        str(subject.get("name") or "").strip()
        if isinstance(subject, dict) and subject.get("kind") == "person"
        else ""
    )
    normalized_subject = _normalized_link_text(subject_name)
    source_title = str(source.get("title") or "")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in source.get("links") or []:
        if not isinstance(raw, dict):
            continue
        raw_url = str(raw.get("url") or "").strip()
        if not _is_fanza_product_url(raw_url):
            continue
        destination = canonical_fanza_product_url(raw_url)
        if not destination:
            continue
        ancestors = str(raw.get("browser_ancestors") or "").casefold()
        if re.search(
            r"sidebar|sidemenu|side[_-]?bar|recommend|related|ranking|"
            r"popular|blogroll|pickup|widget|footer|header|navigation|"
            r"関連記事|おすすめ記事|オススメ記事|人気記事|ランキング",
            ancestors,
        ):
            continue
        if not re.search(
            r"entry|post|article|#more|\.content\b|#main(?:-right)?\b|"
            r"main[_-]?content|wakupr",
            ancestors,
        ):
            continue
        position = _rect_position(raw)
        if position is None:
            continue
        # The work-title link is commonly after the image gallery but just
        # before its official sample player. Allow that ordering while still
        # requiring the link to sit in the same local media section.
        if position[0] < media_last_top - 1800 or position[0] - media_bottom > 2200:
            continue
        grouped.setdefault(destination, []).append(raw)
    if len(grouped) != 1:
        return []
    destination, links = next(iter(grouped.items()))
    evidence_text = " ".join(
        " ".join((
            str(link.get("text") or ""),
            str(link.get("browser_context") or ""),
        ))
        for link in links
    )
    if normalized_subject:
        if normalized_subject not in _normalized_link_text(evidence_text):
            return []
    elif _topic_pair_overlap(source_title, evidence_text) < 4:
        return []
    texts = [
        " ".join(str(link.get("text") or "").split())[:180]
        for link in links
        if " ".join(str(link.get("text") or "").split())
    ]
    product_title = max(
        (
            text for text in texts
            if not re.search(r"無料サンプル|サンプルを見る|作品を見る|詳細を見る", text)
        ),
        key=len,
        default=max(texts, key=len, default=""),
    )
    product_id = fanza_product_id(destination)
    if not product_id:
        return []
    source["verified_embedded_fanza_product_urls"] = [destination]
    return [{
        "product_title": product_title or str(source.get("title") or "FANZA作品")[:180],
        "product_code": product_id,
        "product_url": destination,
        "image_ids": image_ids,
        "video_ids": video_ids,
        "reason": (
            "本文ギャラリー直後の同一人物名を含む唯一のFANZA作品リンクを確認"
            if subject_name
            else "本文ギャラリー直後の題名が一致する唯一のFANZA作品リンクを確認"
        ),
    }]


def _attach_verified_fanza_products(
    source: dict[str, Any],
    site_root: Path,
    progress: Any = None,
) -> None:
    raw_media_products = [
        dict(item) for item in (source.get("ai_fanza_image_products") or [])
        if isinstance(item, dict)
    ]
    for inferred in _infer_single_embedded_fanza_product(source):
        inferred_url = canonical_fanza_product_url(str(inferred.get("product_url") or ""))
        matching = next((
            item for item in raw_media_products
            if canonical_fanza_product_url(str(item.get("product_url") or "")) == inferred_url
        ), None)
        if matching is None:
            raw_media_products.append(inferred)
            continue
        matching["image_ids"] = list(dict.fromkeys([
            *matching.get("image_ids", []), *inferred.get("image_ids", []),
        ]))
        matching["video_ids"] = list(dict.fromkeys([
            *matching.get("video_ids", []), *inferred.get("video_ids", []),
        ]))
        for key in ("product_title", "product_code", "product_url", "reason"):
            if not matching.get(key):
                matching[key] = inferred.get(key)
    for inferred in _infer_single_media_filename_fanza_product(source):
        inferred_code = _normalized_fanza_product_code(
            str(inferred.get("product_code") or "")
        )
        matching = next((
            item for item in raw_media_products
            if _normalized_fanza_product_code(str(item.get("product_code") or ""))
            == inferred_code
        ), None)
        if matching is None:
            raw_media_products.append(inferred)
            continue
        matching["image_ids"] = list(dict.fromkeys([
            *matching.get("image_ids", []), *inferred.get("image_ids", []),
        ]))
        matching["video_ids"] = list(dict.fromkeys([
            *matching.get("video_ids", []), *inferred.get("video_ids", []),
        ]))
        if not matching.get("reason"):
            matching["reason"] = inferred.get("reason")
    source["ai_fanza_image_products"] = raw_media_products
    media_products = [
        item for item in raw_media_products
        if isinstance(item, dict) and (item.get("image_ids") or item.get("video_ids"))
    ]
    product_kind = _fanza_product_kind(source)
    source["fanza_product_kind"] = product_kind
    source["verified_fanza_products"] = []
    source.pop("verified_fanza_fallback", None)
    exact_queries = list(dict.fromkeys(
        " ".join(str(item.get("product_code") or "").split())[:80]
        for item in media_products
        if not _is_fanza_product_url(str(item.get("product_url") or ""))
        and " ".join(str(item.get("product_code") or "").split())
    ))
    cache: dict[str, Any] = {}

    def cache_key(query: str) -> str:
        return f"{product_kind}:{query}"

    if exact_queries:
        cache_path = site_root / ".article-studio" / "fanza-product-cache.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        missing = [
            query for query in exact_queries
            if not isinstance(cache.get(cache_key(query)), list)
        ]
        if missing:
            if progress:
                progress(50, "画像・動画の品番とFANZA作品を照合しています")
            discovered = discover_fanza_products(
                missing,
                limit_per_query=2,
                product_kind=product_kind,
            )
            for query in missing:
                cache[cache_key(query)] = [
                    product for product in discovered
                    if product.get("matched_query") == query
                ]
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(cache_path)

    source_product_ids = {
        _normalized_fanza_product_code(fanza_product_id(str(source.get(key) or "")))
        for key in ("requested_url", "url")
        if fanza_product_id(str(source.get(key) or ""))
    }
    verified_media_products: list[dict[str, Any]] = []
    verified_index_by_url: dict[str, int] = {}
    for item in media_products:
        product_url = str(item.get("product_url") or "")
        product_code = " ".join(str(item.get("product_code") or "").split())[:40]
        product_title = " ".join(str(item.get("product_title") or "").split())[:180]
        discovered_product: dict[str, Any] = {}
        if _is_fanza_product_url(product_url):
            destination = canonical_fanza_product_url(product_url)
            destination_id = _normalized_fanza_product_code(fanza_product_id(destination))
            is_source_product = bool(destination_id and destination_id in source_product_ids)
            if not is_source_product and not _fanza_url_matches_exact_code(
                destination,
                product_code,
            ):
                continue
            evidence_type = "source_product_url" if is_source_product else "exact_product_url"
            confidence = 100 if evidence_type == "source_product_url" else 96
        else:
            matches = cache.get(cache_key(product_code), []) if product_code else []
            discovered_product = next(
                (
                    product for product in matches
                    if isinstance(product, dict)
                    and _is_fanza_product_url(str(product.get("url") or ""))
                    and _product_matches_exact_code(product, product_code)
                ),
                {},
            )
            destination = str(discovered_product.get("url") or "")
            evidence_type = "exact_product_code"
            confidence = 98
        if not _is_fanza_product_url(destination):
            continue
        image_ids = list(dict.fromkeys(
            str(image_id) for image_id in item.get("image_ids", [])
            if isinstance(image_id, str)
        ))
        video_ids = list(dict.fromkeys(
            str(video_id) for video_id in item.get("video_ids", [])
            if isinstance(video_id, str)
        ))
        if not image_ids and not video_ids:
            continue
        destination = canonical_fanza_product_url(destination)
        thumbnail_url = _official_fanza_package_url(
            source,
            destination,
            image_ids,
            str(discovered_product.get("thumbnail_url") or ""),
        )
        if not thumbnail_url:
            exact_package = download_exact_fanza_package(
                fanza_product_id(destination).casefold()
            )
            if exact_package:
                thumbnail_url = str(exact_package.get("url") or "")
        candidate = {
            "product_id": (
                str(discovered_product.get("product_id") or "").lower()
                or product_code.lower()
                or destination
            ),
            "url": destination,
            "title": (
                str(discovered_product.get("title") or "").strip()
                or product_title
                or product_code
                or str(source.get("title") or "FANZA作品")
            )[:180],
            "thumbnail_url": thumbnail_url,
            "thumbnail_source_kind": "fanza_package" if thumbnail_url else "",
            "thumbnail_owner_url": destination if thumbnail_url else "",
            "image_ids": image_ids,
            "video_ids": video_ids,
            "reason": str(item.get("reason") or "")[:300],
            "evidence_type": evidence_type,
            "match_confidence": confidence,
        }
        existing_index = verified_index_by_url.get(destination)
        if existing_index is None:
            verified_index_by_url[destination] = len(verified_media_products)
            verified_media_products.append(candidate)
            continue
        existing = verified_media_products[existing_index]
        existing["image_ids"] = list(dict.fromkeys(
            [*existing.get("image_ids", []), *image_ids]
        ))
        existing["video_ids"] = list(dict.fromkeys(
            [*existing.get("video_ids", []), *video_ids]
        ))
        reason = str(candidate.get("reason") or "")
        if reason and reason not in str(existing.get("reason") or ""):
            existing["reason"] = " / ".join(filter(None, [
                str(existing.get("reason") or ""),
                reason,
            ]))[:300]
        existing["match_confidence"] = max(
            int(existing.get("match_confidence") or 0),
            int(candidate.get("match_confidence") or 0),
        )
        if candidate.get("evidence_type") == "source_product_url":
            existing["evidence_type"] = "source_product_url"
    source["verified_fanza_media_products"] = verified_media_products
    # Keep the old key readable for drafts captured before media matching was added.
    source["verified_fanza_image_products"] = verified_media_products


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


def _is_recommendation_material(item: dict[str, Any]) -> bool:
    structure = " ".join((
        str(item.get("browser_ancestors") or ""),
        str(item.get("browser_context") or ""),
        str(item.get("ai_relation") or ""),
        str(item.get("ai_role") or ""),
    )).casefold()
    return bool(re.search(
        r"recom|recommend|related|ranking|sidebar|sidemenu|blogroll|"
        r"popular[_ -]?post|pickup[_ -]?banner|おすすめ|オススメ|関連記事|人気記事|"
        r"ランキング|広告|バナー|site[_ -]?ui|unrelated|"
        r"(?:僕|ぼく|俺|わい|ワイ|自分)(?:は|も)(?:断然)?(?:こっち|あっち)|"
        r"こっち(?:の子|の人|がいい|派|！|!|$)|この子[？?]",
        structure,
    ))


def _thread_reply_number(item: dict[str, Any]) -> int:
    try:
        explicit = int(item.get("thread_reply_number") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    match = re.search(
        r"#(?:surebody|sure|img_)(\d+)",
        str(item.get("browser_ancestors") or ""),
        re.I,
    )
    return int(match.group(1)) if match else 0


def _named_subject_media_candidates(
    images: list[dict[str, Any]],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    subject = source.get("ai_main_subject") or {}
    if not isinstance(subject, dict) or subject.get("kind") != "person":
        return images
    lead_images = [item for item in images if _thread_reply_number(item) == 1]
    if not lead_images:
        return images
    subject_name = re.sub(r"\s+", "", str(subject.get("name") or "")).casefold()
    kept: list[dict[str, Any]] = []
    for item in images:
        reply_number = _thread_reply_number(item)
        if reply_number == 1:
            kept.append(item)
            continue
        if reply_number == 0:
            # Once a rendered lead reply is available, static-parser images
            # with no DOM position cannot be proved to belong to that person.
            # Roundup pages often expose later "I prefer this one" images only
            # through cached static URLs, which otherwise mix strangers in.
            if (
                item.get("anchor_href_candidate")
                or str(item.get("browser_ancestors") or "").strip()
                or str(item.get("browser_context") or "").strip()
            ):
                kept.append(item)
            continue
        evidence = re.sub(
            r"\s+",
            "",
            str(item.get("browser_context") or ""),
        ).casefold()
        if subject_name and subject_name in evidence:
            kept.append(item)
    return kept


def _select_article_images(source: dict[str, Any]) -> dict[str, Any]:
    images = [
        item for item in source.get("images") or []
        if isinstance(item, dict)
        and item.get("id")
        and not _is_recommendation_material(item)
    ]
    images = _named_subject_media_candidates(images, source)
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
        chosen = body_ids[:]
        subject = source.get("ai_main_subject") or {}
        if isinstance(subject, dict) and subject.get("kind") == "person":
            anchor_group = str(by_id[thumbnail_id].get("ai_content_group") or "").strip()
            if not anchor_group:
                group_counts: dict[str, int] = {}
                for image_id in body_ids:
                    group = str(by_id[image_id].get("ai_content_group") or "").strip()
                    if group:
                        group_counts[group] = group_counts.get(group, 0) + 1
                if group_counts:
                    anchor_group = max(
                        group_counts,
                        key=lambda value: (group_counts[value], value),
                    )
            if anchor_group:
                chosen = [
                    image_id for image_id in chosen
                    if str(by_id[image_id].get("ai_content_group") or "").strip() == anchor_group
                ]
            elif len(chosen) > 1:
                # Multiple ungrouped images cannot be proved to show the named
                # person. Keep one reviewable image instead of mixing strangers.
                chosen = chosen[:1]
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
    for image_id in image_ids:
        if image_id in reserved:
            continue
        item = images_by_id[image_id]
        data = item.get("data")
        byte_count = len(data) if isinstance(data, bytes) else 0
        fitted.append(image_id)
        total_bytes += byte_count
    return fitted


def _normalized_link_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", str(value or "").casefold())


def _link_font_pixels(value: Any) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else 0.0


def _redirect_query_targets(value: Any) -> set[str]:
    """Extract plain or reversed destination URLs carried by relay pages."""
    parsed = urlparse(str(value or ""))
    targets: set[str] = set()
    for values in parse_qs(parsed.query, keep_blank_values=False).values():
        for raw in values:
            for candidate in (str(raw or "").strip(), str(raw or "").strip()[::-1]):
                if not re.match(r"https?://", candidate, re.I):
                    continue
                try:
                    targets.add(urldefrag(_validate_source_url(candidate))[0])
                except Exception:
                    continue
    return targets


def _topic_pair_overlap(left: Any, right: Any) -> int:
    """Count shared Japanese/ASCII character pairs without pretending to parse prose."""
    ignored = {
        "画像", "動画", "記事", "まとめ", "エロ", "美女", "美少女", "こちら",
        "見る", "続きを読む", "おすすめ", "オススメ",
    }

    def pairs(value: Any) -> set[str]:
        normalized = _normalized_link_text(value)
        for term in ignored:
            normalized = normalized.replace(_normalized_link_text(term), "")
        return {
            normalized[index:index + 2]
            for index in range(max(0, len(normalized) - 1))
        }

    return len(pairs(left) & pairs(right))


def _first_article_media_y(source: dict[str, Any]) -> float | None:
    """Return the first visible article-media position, excluding ads and chrome."""
    positions: list[float] = []
    for item in [*(source.get("images") or []), *(source.get("videos") or [])]:
        if not isinstance(item, dict):
            continue
        structure = " ".join((
            str(item.get("browser_ancestors") or ""),
            str(item.get("browser_context") or ""),
        )).casefold()
        if re.search(
            r"recom|recommend|related|ranking|sidebar|sidemenu|blogroll|"
            r"popular[_-]?post|footer|navigation|pickup[_-]?banner|\bad[s_-]",
            structure,
        ):
            continue
        rect = item.get("browser_rect") or {}
        try:
            position = float(rect.get("y"))
        except (TypeError, ValueError):
            continue
        if position >= 0:
            positions.append(position)
    return min(positions) if positions else None


def _deterministic_follow_link(
    source: dict[str, Any],
    visited: set[str],
    navigation_context: dict[str, Any] | None = None,
    *,
    learned_gateway: bool = False,
    learned_patterns: list[dict[str, Any]] | None = None,
) -> dict[str, str] | None:
    """Follow only a strongly identified article link without spending an AI call.

    A preview image or OGP thumbnail does not prove that the current page is the
    article body. Relay sites commonly carry one such image while the real
    gallery is behind a prominent text link.
    """
    previous_text = _normalized_link_text((navigation_context or {}).get("followed_link_text"))
    source_host = (urlparse(str(source.get("url") or "")).hostname or "").lower()
    source_text = "\n".join((
        str(source.get("title") or ""),
        str(source.get("description") or ""),
        str(source.get("body_text") or "")[:1800],
        *[str(item) for item in (source.get("text_blocks") or [])[:10]],
    ))
    source_title = str(source.get("title") or "")
    link_category_marker = bool(re.search(
        r"(?:カテゴリー|category)\s*[:：]?\s*(?:link|リンク)(?:\s|$)",
        source_text,
        re.I,
    ))
    encoded_targets = _redirect_query_targets(source.get("url"))
    first_media_y = _first_article_media_y(source)
    scored: list[tuple[int, int, dict[str, str], int, int]] = []
    for position, raw in enumerate(source.get("links") or []):
        if not isinstance(raw, dict):
            continue
        try:
            url = _validate_source_url(str(raw.get("url") or ""))
        except Exception:
            continue
        normalized_url = urldefrag(url)[0]
        if normalized_url in visited:
            continue
        parsed = urlparse(normalized_url)
        if re.search(r"\.(?:jpe?g|png|gif|webp|avif|svg|mp4|webm|mov)(?:$|/)", parsed.path, re.I):
            continue
        text = " ".join(str(raw.get("text") or "").split())[:500]
        normalized_text = _normalized_link_text(text)
        combined = f"{normalized_url} {text}".casefold()
        if any(term in combined for term in (
            "doubleclick", "adservice", "googlesyndication", "javascript:",
            "/tag/", "/category/", "/author/", "login", "signup", "利用規約",
            "プライバシー", "お問い合わせ",
        )):
            continue
        score = 0
        gateway_signals = 0
        intent_match = 0
        for pattern in learned_patterns or []:
            if not isinstance(pattern, dict):
                continue
            learned_text = _normalized_link_text(pattern.get("link_text"))
            learned_host = str(pattern.get("target_host") or "").casefold()
            if learned_text and normalized_text == learned_text:
                score += 230
                gateway_signals += 3
            elif learned_text and len(learned_text) >= 6 and (
                learned_text in normalized_text or normalized_text in learned_text
            ):
                score += 130
                gateway_signals += 2
            if learned_host and (parsed.hostname or "").casefold().removeprefix("www.") == learned_host:
                score += 35
        if previous_text and normalized_text == previous_text:
            score += 280
            intent_match = 2
        elif previous_text and len(previous_text) >= 6 and (
            previous_text in normalized_text or normalized_text in previous_text
        ):
            score += 170
            intent_match = 1
        if re.search(
            r"/(?:archives?|articles?|posts?|entry|contents?)/|[?&](?:p|id)=\w+",
            parsed.path + "?" + parsed.query,
            re.I,
        ):
            score += 45
        if re.search(r"本編|記事|続きを読む|続き|volume\s*\d+|vol\.?\s*\d+", text, re.I):
            score += 40
            gateway_signals += 1
        structure = " ".join((
            str(raw.get("browser_ancestors") or ""),
            str(raw.get("browser_context") or ""),
        )).casefold()
        browser_context = str(raw.get("browser_context") or "")
        context_overlap = _topic_pair_overlap(source_title, browser_context)
        destination_host = (parsed.hostname or "").lower()
        external_destination = bool(
            destination_host
            and destination_host != source_host
            and destination_host.removeprefix("www.")
            not in {
                "x.com", "twitter.com", "youtube.com", "youtu.be",
                "instagram.com", "tiktok.com", "facebook.com",
            }
        )
        article_destination = bool(re.search(
            r"/(?:archives?|articles?|posts?|entry|contents?)/|"
            r"/blog-entry-[^/]+|[?&](?:p|id)=\w+",
            parsed.path + "?" + parsed.query,
            re.I,
        ))
        article_body_link = bool(re.search(
            r"entry[_-]?content|post[_-]?content|article[_-]?(?:body|content)|"
            r"main[_-]?content|\barticle#|\barticle\.",
            structure,
        ))
        plain_url_text = bool(re.fullmatch(r"https?://\S+", text.strip(), re.I))
        rect = raw.get("browser_rect") or {}
        try:
            link_y = float(rect.get("y"))
        except (TypeError, ValueError):
            link_y = -1
        before_first_media = bool(
            first_media_y is not None and link_y >= 0 and link_y < first_media_y
        )
        # Some roundup sites copy a few generic recommendation thumbnails into
        # their own page, but expose the real article as a plain source link just
        # before those images. The surrounding paragraph still repeats the
        # requested title, which is stronger evidence than the local image count.
        if (
            external_destination
            and article_destination
            and article_body_link
            and context_overlap >= 3
            and (plain_url_text or _topic_pair_overlap(source_title, text) >= 3)
        ):
            score += 185
            gateway_signals += 3
            if before_first_media:
                score += 75
                gateway_signals += 1
        if re.search(
            r"entry[_-]?more|post[_-]?more|article[_-]?more|read[_-]?more|"
            r"more[_-]?link|continue[_-]?(?:link|button)",
            structure,
        ):
            score += 150
            gateway_signals += 2
        elif "div#more" in structure or "section#more" in structure:
            score += 45
            gateway_signals += 1
        if link_category_marker:
            score += 55
            gateway_signals += 1
        if (
            _link_font_pixels(raw.get("font_size")) >= 16
            and float(rect.get("width") or 0) >= 240
            and text
        ):
            score += 25
            gateway_signals += 1
        if _topic_pair_overlap(source_title, text) >= 2:
            score += 25
            gateway_signals += 1
        if normalized_url in encoded_targets:
            score += 100
            gateway_signals += 2
        if parsed.hostname and parsed.hostname.lower() == source_host:
            score += 12
        if raw.get("contains_image"):
            score -= 10
        distractor = bool(re.search(
            r"recom|recommend|related|ranking|sidebar|sidemenu|blogroll|"
            r"popular[_-]?post|footer|navigation|pickup[_-]?banner",
            structure,
        ))
        if distractor and intent_match < 2:
            score -= 180
        if len(normalized_text) < 3:
            score -= 30
        scored.append((
            score,
            -position,
            {"url": normalized_url, "text": text},
            gateway_signals,
            intent_match,
        ))
    if not scored:
        return None
    # A source link is often printed once above the gallery and again as a
    # footer credit. Treating those copies as two competing destinations makes
    # an otherwise unambiguous route look tied.
    unique_scored: dict[str, tuple[int, int, dict[str, str], int, int]] = {}
    for candidate in scored:
        candidate_url = candidate[2]["url"]
        current = unique_scored.get(candidate_url)
        if current is None or (candidate[0], candidate[1]) > (current[0], current[1]):
            unique_scored[candidate_url] = candidate
    scored = list(unique_scored.values())
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = scored[0]
    media_count = len(source.get("images") or []) + len(source.get("videos") or [])
    runner_up = scored[1][0] if len(scored) > 1 else -999
    margin = best[0] - runner_up
    if best[4] == 2 and best[0] >= 150:
        return best[2]
    if len(scored) == 1 and media_count == 0:
        return best[2]
    if best[0] >= 45 and best[0] - runner_up >= 20:
        if media_count == 0:
            return best[2]
        if best[3] >= 2 and best[0] >= 120 and margin >= 30:
            return best[2]
        if learned_gateway and best[3] >= 1 and best[0] >= 90 and margin >= 20:
            return best[2]
    return None


def _filter_source_videos(source: dict[str, Any]) -> dict[str, Any]:
    """Recheck merged browser/HTML videos so widgets cannot re-enter later."""
    source_url = str(
        source.get("resolved_url")
        or source.get("requested_url")
        or source.get("url")
        or ""
    )
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in source.get("videos") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        kind = "iframe" if str(item.get("kind") or "") == "iframe" else "direct"
        mime_type = str(item.get("mime_type") or "")
        if not url or url in seen:
            continue
        if not _plausible_video_candidate(url, kind, mime_type, source_url):
            continue
        seen.add(url)
        filtered.append(item)
    source["videos"] = filtered
    return source


def _capture_source_candidates(
    site_root: Path,
    current_url: str,
    progress: Any = None,
) -> dict[str, Any]:
    """Collect candidates with deterministic fallbacks before invoking Codex."""
    del site_root  # Reserved for learned per-site extractors.
    is_fanza = is_fanza_product_url(current_url)
    hostname = (urlparse(current_url).hostname or "").lower()
    is_x = hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
    callback = progress or (lambda _value, _message: None)
    if is_fanza:
        try:
            source = capture_fanza_product_metadata(current_url, callback)
        except Exception:
            traceback.print_exc()
            source = capture_rendered_source(current_url, callback)
        source["capture_strategy"] = "fanza_official"
        return restrict_source_to_fanza_product(source)

    semantic: dict[str, Any] | None = None
    if is_x:
        try:
            semantic = analyze_source_url(current_url)
        except Exception:
            semantic = None
    try:
        source = capture_rendered_source(current_url, callback)
        source["capture_strategy"] = "browser_full"
    except Exception:
        traceback.print_exc()
        source = semantic or analyze_source_url(current_url)
        source["capture_strategy"] = "semantic_fallback"

    if is_x and semantic:
        _merge_x_semantics(source, semantic)
    else:
        try:
            semantic = semantic or analyze_source_url(current_url)
            source = _merge_source_candidates(source, semantic)
            source["capture_strategy"] = "browser_plus_semantic"
        except Exception:
            pass

    if not is_x:
        source = _hydrate_embedded_x_status_media(source, callback)
    source = _filter_source_videos(source)

    if (
        source.get("source_type") == "x_profile"
        and source.get("browser_capture")
        and not source.get("x_authenticated")
        and int(source.get("x_timeline_media_count") or 0) == 0
    ):
        raise XLoginRequiredError("Xの投稿素材を表示できません。初回だけXログインを行ってください")
    return source


def _embedded_x_status_urls(source: dict[str, Any]) -> list[str]:
    """Find status links printed in the leading article body, not its sidebar."""
    leading_text = str(source.get("body_text") or "")[:5000]
    groups: dict[str, list[str]] = {}
    first_positions: dict[str, int] = {}
    seen: set[str] = set()
    for match in re.finditer(
        r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/\d+",
        leading_text,
        re.I,
    ):
        url = re.sub(r"^https?://(?:www\.)?twitter\.com/", "https://x.com/", match.group(0), flags=re.I)
        url = re.sub(r"^http://", "https://", url, flags=re.I).rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        account = match.group(1).casefold()
        groups.setdefault(account, []).append(url)
        first_positions.setdefault(account, match.start())
    if not groups:
        return []
    selected_account = min(
        groups,
        key=lambda account: (-len(groups[account]), first_positions[account]),
    )
    return groups[selected_account][:8]


def _hydrate_embedded_x_status_media(
    source: dict[str, Any],
    progress: Any = None,
) -> dict[str, Any]:
    """Replace timeline spillover with media from the explicitly linked posts."""
    status_urls = _embedded_x_status_urls(source)
    if not status_urls:
        return source

    callback = progress or (lambda _value, _message: None)
    exact_images: list[dict[str, Any]] = []
    exact_videos: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    seen_videos: set[str] = set()
    account_name = (urlparse(status_urls[0]).path.strip("/").split("/", 1)[0] or "x-account")
    owner_profile_url = f"https://x.com/{account_name}"
    content_group = f"x-account:{account_name.casefold()}"
    for index, status_url in enumerate(status_urls, start=1):
        try:
            callback(
                26,
                f"本文に指定されたX投稿を確認しています（{index}/{len(status_urls)}）",
            )
            captured = capture_rendered_source(status_url, callback)
        except Exception:
            traceback.print_exc()
            continue
        for item in captured.get("images") or []:
            if not isinstance(item, dict):
                continue
            key = _media_url_key(item.get("url"))
            if not key or key in seen_images:
                continue
            seen_images.add(key)
            exact_images.append({
                **item,
                "embedded_status_url": status_url,
                "owner_name": account_name,
                "owner_profile_url": owner_profile_url,
                "ai_decision": "article",
                "ai_role": "article_gallery",
                "ai_content_group": content_group,
                "ai_reason": "本文で明示された同一Xアカウントの個別投稿画像",
                "browser_context": (
                    f"本文で明示されたX投稿 {status_url} / "
                    + str(item.get("browser_context") or "")
                )[:700],
            })
        for item in captured.get("videos") or []:
            if not isinstance(item, dict):
                continue
            key = _media_url_key(item.get("url"))
            if not key or key in seen_videos:
                continue
            seen_videos.add(key)
            exact_videos.append({
                **item,
                "embedded_status_url": status_url,
                "owner_name": account_name,
                "owner_profile_url": owner_profile_url,
                "ai_decision": "article",
                "ai_role": "article_video",
                "ai_content_group": content_group,
                "ai_reason": "本文で明示された同一Xアカウントの個別投稿動画",
                "browser_context": (
                    f"本文で明示されたX投稿 {status_url} / "
                    + str(item.get("browser_context") or "")
                )[:700],
            })

    if not exact_images and not exact_videos:
        return source

    for number, item in enumerate(exact_images, start=1):
        item["id"] = f"media-{number}"
    for number, item in enumerate(exact_videos, start=1):
        item["id"] = f"video-{number}"
    source["images"] = exact_images
    source["videos"] = exact_videos
    source["embedded_x_status_urls"] = status_urls
    source["capture_strategy"] = "browser_plus_exact_x_status"
    full_page = [
        item for item in (source.get("browser_attachments") or [])
        if isinstance(item, dict) and item.get("kind") == "full_page"
    ][:1]
    image_sheets = _sheet_attachments(
        exact_images,
        prefix="exact-x-status-images",
        kind="contact_sheet",
    ) if exact_images else []
    video_frames = [
        {"id": item["id"], "data": item["frame_data"]}
        for item in exact_videos if isinstance(item.get("frame_data"), bytes)
    ]
    video_sheets = _sheet_attachments(
        video_frames,
        prefix="exact-x-status-videos",
        kind="video_contact_sheet",
        chunk_size=12,
    ) if video_frames else []
    source["browser_attachments"] = [*full_page, *image_sheets, *video_sheets]
    return source


def _capture_and_analyze_source(
    site_root: Path,
    source_url: str,
    runner: CodexRunner,
    progress: Any = None,
    editorial_intent: dict[str, Any] | None = None,
    category: str = "auto",
    reply_count: str = "auto",
) -> dict[str, Any]:
    validated_url = _validate_source_url(source_url)
    current_url = validated_url
    visited: set[str] = set()
    source_chain: list[str] = []
    navigation_trace: list[dict[str, str]] = []
    navigation_context: dict[str, Any] = {}
    max_chain_depth = 5
    for depth in range(max_chain_depth):
        normalized = urldefrag(current_url)[0]
        if normalized in visited:
            break
        visited.add(normalized)
        source_chain.append(current_url)
        if progress:
            progress(8 + depth * 8, "ページ本文・画像・動画をプログラムで回収しています")
        source = _capture_source_candidates(
            site_root,
            current_url,
            (lambda value, message: progress(min(28, 8 + value // 4), message))
            if progress else None,
        )
        if navigation_context:
            source["navigation_context"] = navigation_context

        current_plan = get_site_plan(site_root, current_url)
        deterministic_follow = _deterministic_follow_link(
            source,
            visited,
            navigation_context,
            learned_gateway=bool(current_plan.get("navigation_successes")),
            learned_patterns=list(current_plan.get("navigation_patterns") or []),
        )
        if deterministic_follow:
            navigation_context = {
                "from_url": str(source.get("url") or current_url),
                "from_title": str(source.get("title") or ""),
                "followed_url": deterministic_follow["url"],
                "followed_link_text": deterministic_follow["text"],
                "follow_reason": "本文内の強調導線と前ページの題名から本編リンクを特定",
            }
            navigation_trace.append(dict(navigation_context))
            current_url = deterministic_follow["url"]
            if progress:
                progress(
                    24 + depth * 8,
                    f"本編への導線を特定しました（{depth + 1}/{max_chain_depth - 1}段目）",
                )
            continue

        intent = dict(editorial_intent or {})
        intent.pop("private_note", None)
        source_type = str(source.get("source_type") or "")
        if is_fanza_product_url(current_url):
            intent.update({"content_mode": "fanza_product", "promotion_type": "affiliate"})
        else:
            if str(intent.get("content_mode") or "auto") == "auto":
                intent["content_mode"] = (
                    "x_account" if source_type == "x_profile"
                    else "x_post" if source_type == "x_post"
                    else "web"
                )
            intent.setdefault("promotion_type", "organic")
        source["editorial_intent"] = intent
        source["requested_url"] = validated_url
        source["source_chain"] = list(source_chain)
        source["navigation_trace"] = list(navigation_trace)
        if len(source_chain) > 1:
            source["capture_strategy"] = "gateway_chain"
        site_plan = get_site_plan(site_root, current_url)
        source = prioritize_source_media(source, site_plan)

        if not source.get("images"):
            for video in source.get("videos") or []:
                if not isinstance(video, dict) or not isinstance(video.get("frame_data"), bytes):
                    continue
                frame_data = video["frame_data"]
                source["images"] = [{
                    "id": "video-frame-thumbnail",
                    "url": str(video.get("poster") or video.get("url") or ""),
                    "data": frame_data,
                    "extension": ".jpg",
                    "mime_type": "image/jpeg",
                    "alt": str(source.get("title") or "動画サムネイル")[:180],
                    "orientation": "landscape",
                    "width": int(video.get("width") or 0),
                    "height": int(video.get("height") or 0),
                    "browser_context": "本編動画から取得したサムネイル候補",
                    "browser_ancestors": "video frame",
                    "browser_rect": video.get("browser_rect") or {},
                    "browser_visible": True,
                    "browser_link_url": "",
                    "thumbnail_only_candidate": True,
                }]
                break
        if not source.get("images"):
            raise RuntimeError(
                "Codexへ送る前の素材回収で、サムネイルに使える画像または動画フレームを取得できませんでした"
            )

        if progress:
            progress(38, "Codexが本編素材の判定と完成記事を1回で作成しています")
        composed = runner.compose(source, {
            "category": category,
            "reply_count": reply_count,
            "site_learning_context": learning_prompt_context(site_plan),
        })
        analysis = dict(composed["analysis"])
        if analysis.get("adult_content") is not True:
            reason = str(analysis.get("adult_reason") or "一般向けの内容です")
            raise NonAdultSourceError(f"成人向けでないため記事を作成しませんでした: {reason}")
        follow_url = str(analysis.get("follow_url") or "").strip()
        if str(analysis.get("page_role") or "") == "gateway" and follow_url:
            allowed_links = {
                urldefrag(str(item.get("url") or ""))[0]: item
                for item in (source.get("links") or [])
                if isinstance(item, dict) and item.get("url")
            }
            validated_follow = _validate_source_url(follow_url)
            follow_key = urldefrag(validated_follow)[0]
            if follow_key in allowed_links and follow_key not in visited:
                link = allowed_links[follow_key]
                navigation_context = {
                    "from_url": str(source.get("url") or current_url),
                    "from_title": str(source.get("title") or ""),
                    "followed_url": validated_follow,
                    "followed_link_text": str(link.get("text") or ""),
                    "follow_reason": str(analysis.get("follow_reason") or "Codexが本編リンクと判定"),
                }
                navigation_trace.append(dict(navigation_context))
                current_url = validated_follow
                continue
        if str(analysis.get("page_role") or "") != "article":
            detail = f" 次の候補: {follow_url}" if follow_url else ""
            raise EditorialPolicyError(
                f"記事本編ページではないためCodexは記事を書きませんでした。{analysis.get('follow_reason') or analysis.get('analysis_summary')}{detail}"
            )
        result = apply_codex_analysis(source, analysis)
        result = apply_known_visual_identity_matches(site_root, result)

        def verify_subject(subject: dict[str, Any]) -> dict[str, Any]:
            if progress:
                progress(62, f"{subject.get('name', '主役')}の公式アカウントを初回照合しています")
            return runner.verify_social_profile(subject)

        result = resolve_subject_social_profiles(
            site_root,
            result,
            verifier=verify_subject,
        )
        result = resolve_performer_social_profiles(
            site_root,
            result,
            verifier=verify_subject,
        )
        result = resolve_identified_people_social_profiles(
            site_root,
            result,
            verifier=verify_subject,
        )
        if progress:
            progress(66, "公式アカウントごとのプロフィール画像を確認しています")
        result = enrich_source_profile_thumbnails(site_root, result)
        result = apply_verified_person_identity_to_source(result)
        try:
            record_verified_visual_identities(site_root, result)
        except Exception:
            traceback.print_exc()
        result["_single_pass_article"] = dict(composed["article"])
        _attach_verified_fanza_products(result, site_root, progress)
        return result
    raise EditorialPolicyError(
        f"本編へのリンクを{max_chain_depth}ページ以内に特定できませんでした"
    )


def _article_from_composed_source(
    source: dict[str, Any],
    *,
    reply_count: str,
    thumbnail_id: str,
    body_image_ids: list[str],
    video_ids: list[str],
) -> dict[str, Any]:
    selected_image_ids = list(dict.fromkeys([thumbnail_id, *body_image_ids]))
    article = _validate_codex_result(
        source.get("_single_pass_article"),
        reply_count,
        selected_media_count=len(selected_image_ids) + len(video_ids),
        selected_image_ids=selected_image_ids,
        selected_video_ids=video_ids,
    )
    payload_image_ids = {
        source_id: f"source-image-{index}"
        for index, source_id in enumerate(selected_image_ids, start=1)
    }
    payload_video_ids = {
        source_id: f"source-video-{index}"
        for index, source_id in enumerate(video_ids, start=1)
    }
    for response in article["responses"]:
        response["image_ids"] = [
            payload_image_ids[image_id]
            for image_id in response.get("image_ids", [])
            if image_id in payload_image_ids
        ]
        response["video_ids"] = [
            payload_video_ids[video_id]
            for video_id in response.get("video_ids", [])
            if video_id in payload_video_ids
        ]
    return article


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
                self.category,
                self.reply_count,
            )
            selected_videos = _selected_generation_videos(source)
            image_selection = _select_article_images(source)
            thumbnail_id = str(image_selection["thumbnail_id"])
            body_image_ids = list(image_selection["body_ids"])
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
            self.signals.progress.emit(72, "Codexの完成稿と採用素材を照合しています")
            generated = _article_from_composed_source(
                source,
                reply_count=self.reply_count,
                thumbnail_id=thumbnail_id,
                body_image_ids=body_image_ids,
                video_ids=selected_videos,
            )
            if self.category != "auto":
                generated["category"] = self.category
            payload = apply_codex_result(base, generated)
            _place_videos_at_start(payload)
            _apply_editorial_metadata(payload, source, self.editorial_intent, self.site_root)
            _mark_ready_to_publish(payload, source)
            payload["source_chain"] = list(source.get("source_chain") or [])
            payload["navigation_trace"] = list(source.get("navigation_trace") or [])
            quality = _apply_adaptive_quality(self.site_root, payload, source)
            if self.editorial_intent.get("force_duplicate"):
                marker = hashlib.sha256(
                    f"{self.source_url}\n{datetime.now(JST).isoformat()}".encode("utf-8")
                ).hexdigest()[:8]
                _assign_duplicate_slug(payload, marker)
            self.signals.progress.emit(88, "公開可能な記事として登録しています")
            slug = save_draft(payload, self.site_root)
            _record_adaptive_outcome_safely(
                self.site_root,
                url=self.source_url,
                slug=slug,
                outcome=(
                    "success"
                    if quality.get("recommendation") == "auto_ready"
                    else "review"
                ),
                stage="manual_save",
                message=" / ".join(
                    quality.get("blockers") or quality.get("warnings") or []
                ),
            )
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
    category: str = "auto",
    reply_count: str = "auto",
) -> dict[str, Any]:
    try:
        return _capture_and_analyze_source(
            site_root,
            source_url,
            runner,
            progress,
            editorial_intent,
            category,
            reply_count,
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
            category,
            reply_count,
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
        site_root, source_url, runner, progress, editorial_intent, category, reply_count
    )
    selected_videos = _selected_generation_videos(source)
    image_selection = _select_article_images(source)
    thumbnail_id = str(image_selection["thumbnail_id"])
    body_image_ids = list(image_selection["body_ids"])
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
        progress(72, "Codexの完成稿と採用素材を照合しています")
    generated = _article_from_composed_source(
        source,
        reply_count=reply_count,
        thumbnail_id=thumbnail_id,
        body_image_ids=body_image_ids,
        video_ids=selected_videos,
    )
    if category != "auto":
        generated["category"] = category
    payload = apply_codex_result(base, generated)
    _place_videos_at_start(payload)
    _apply_editorial_metadata(payload, source, editorial_intent or {}, site_root)
    # Media blocks are assembled locally, after which the saved-article policy
    # can safely verify the one-to-one image placement itself.
    if payload.get("content_mode") == "fanza_product":
        payload["media_alignment_checked"] = True
    payload = _mark_ready_to_publish(payload, source)
    payload["source_chain"] = [
        str(value)
        for value in (source.get("source_chain") or [])
        if str(value).strip()
    ]
    payload["navigation_trace"] = [
        dict(value)
        for value in (source.get("navigation_trace") or [])
        if isinstance(value, dict)
    ]
    payload["site_capture_strategy"] = str(
        source.get("capture_strategy") or "browser_full"
    )
    _apply_adaptive_quality(site_root, payload, source)
    return payload


def _dedupe_direct_fanza_product_ctas(payload: dict[str, Any]) -> bool:
    """Keep one strongest exact-work card on a direct FANZA product article."""
    if str(payload.get("content_mode") or "") != "fanza_product":
        return False
    source_product_id = fanza_product_id(
        str(
            payload.get("fanza_product_url")
            or payload.get("source_url")
            or ""
        )
    )
    if not source_product_id:
        return False
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return False
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, block in enumerate(blocks):
        if (
            isinstance(block, dict)
            and block.get("type") == "product_cta"
            and fanza_product_id(str(block.get("url") or "")) == source_product_id
        ):
            candidates.append((index, block))
    if len(candidates) <= 1:
        return False

    match_rank = {
        "exact_video": 4,
        "exact_image": 3,
        "exact_article": 2,
        "manual_article": 1,
    }

    def rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        index, block = item
        try:
            confidence = int(block.get("match_confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0
        return (
            match_rank.get(str(block.get("match_type") or ""), 0),
            confidence,
            1 if block.get("thumbnail_url") or block.get("thumbnail_image_id") else 0,
            -index,
        )

    keep_index = max(candidates, key=rank)[0]
    duplicate_indexes = {index for index, _block in candidates if index != keep_index}
    payload["blocks"] = [
        block for index, block in enumerate(blocks) if index not in duplicate_indexes
    ]
    return True


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
            "fanza_product" if source.get("media_rights_profile") == FANZA_MEDIA_PROFILE
            else "x_account" if source.get("source_type") == "x_profile"
            else "x_post" if source.get("source_type") == "x_post"
            else "web"
        )
    payload["content_mode"] = content_mode
    payload["promotion_type"] = promotion_type
    payload["editorial_brief"] = str(intent.get("editorial_brief") or "")[:1000]
    payload["private_client_note"] = str(intent.get("private_note") or "")[:2000]
    trend_context = intent.get("trend_context") or {}
    if isinstance(trend_context, dict) and trend_context:
        payload["automation_trend_context"] = {
            "buzz_score": max(0, int(trend_context.get("buzz_score") or 0)),
            "selection_reasons": [
                str(value)[:120]
                for value in trend_context.get("selection_reasons") or []
                if str(value).strip()
            ][:8],
            "source_name": str(trend_context.get("source_name") or "")[:160],
            "source_card_text": str(
                trend_context.get("source_card_text") or ""
            )[:1000],
            "public_engagement": max(
                0, int(trend_context.get("public_engagement") or 0)
            ),
            "engagement_delta": max(
                0, int(trend_context.get("engagement_delta") or 0)
            ),
            "cross_source_count": max(
                1, int(trend_context.get("cross_source_count") or 1)
            ),
            "popular_context": bool(trend_context.get("popular_context")),
            "sale_context": bool(trend_context.get("sale_context")),
        }
    main_subject = source.get("ai_main_subject")
    if isinstance(main_subject, dict):
        payload["main_subject"] = dict(main_subject)
    identity_resolution = source.get("identity_resolution")
    if isinstance(identity_resolution, dict):
        payload["identity_resolution"] = dict(identity_resolution)
    if site_root is not None:
        cached_profiles = registry_profiles_for_payload(site_root, payload)
        merged_profiles = merge_verified_social_profiles(
            [
                item for item in (source.get("verified_social_profiles") or [])
                if isinstance(item, dict)
            ],
            cached_profiles,
        )
        if merged_profiles:
            source["verified_social_profiles"] = merged_profiles
            payload["verified_social_profiles"] = json.loads(
                json.dumps(merged_profiles, ensure_ascii=False)
            )
            payload["identity_resolution"] = {
                "status": "verified",
                "method": (
                    "source_page_and_verified_registry"
                    if cached_profiles else "source_page"
                ),
                "message": (
                    "元ページ内リンクと検証済み人物名簿を統合して公式アカウントを使用"
                    if cached_profiles
                    else "元ページ内で確認した公式アカウントを使用"
                ),
            }
    apply_verified_person_identity_to_payload(payload, source)
    payload["affiliate_opportunities"] = detect_affiliate_opportunities(source)
    if content_mode == "fanza_product":
        payload["media_rights_profile"] = FANZA_MEDIA_PROFILE
        product_url = str(source.get("requested_url") or source.get("url") or "")
        payload["fanza_product_url"] = canonical_fanza_product_url(product_url)
        payload["fanza_product_id"] = fanza_product_id(product_url)
        payload["transparency_note"] = FANZA_TRANSPARENCY_NOTE
        # Keep the deterministic product identity in the draft. This lets a
        # later local-only repair rebuild performer and social destinations
        # without spending another model call.
        for field in (
            "fanza_people",
            "ai_fanza_people",
            "fanza_performer_name",
            "ai_fanza_performer_name",
            "fanza_performer_pages",
            "verified_social_profiles",
            "performer_identity_resolution",
        ):
            value = source.get(field)
            if value not in (None, "", [], {}):
                payload[field] = json.loads(json.dumps(value, ensure_ascii=False))
    if content_mode in {"x_account", "x_post"}:
        username = str((source.get("x_info") or {}).get("username") or "")
        payload["source_label"] = f"@{username}のX" if username else "X"
    media_promotions = _resolve_fanza_media_promotions(payload, source, site_root)
    # A manually supplied product URL is trusted as an article-level CTA. All
    # automatically discovered CTAs must be tied to an exact image or video.
    explicit_promotion = (
        _resolve_fanza_promotion(source, intent, site_root)
        if not media_promotions and str(intent.get("fanza_url") or "").strip()
        else None
    )
    promotions = [explicit_promotion] if explicit_promotion else []
    all_promotions = [*media_promotions, *promotions]
    if all_promotions:
        if content_mode == "fanza_product" and media_promotions:
            payload["content_mode"] = "fanza_product"
        payload["promotion_type"] = "affiliate"
        disclosure = "この記事にはFANZAのアフィリエイト広告が含まれます。"
        existing = str(payload.get("transparency_note") or "")
        payload["transparency_note"] = (
            existing if disclosure in existing else f"{disclosure} {existing}".strip()
        )[:500]
        if media_promotions:
            _insert_media_product_promotions(payload["blocks"], media_promotions)
        for number, promotion in enumerate(promotions, start=1):
            product_block = {
                "id": f"fanza-product-{number}",
                "type": "product_cta",
                "url": promotion["url"],
                "title": promotion["title"],
                "text": promotion["text"],
                "button_text": promotion["button_text"],
                "thumbnail_url": promotion.get("thumbnail_url", ""),
                "thumbnail_source_kind": promotion.get("thumbnail_source_kind", ""),
                "thumbnail_owner_url": promotion.get(
                    "thumbnail_owner_url", promotion["url"]
                ),
                "placement_label": promotion.get("placement_label", "この記事の商品"),
                "match_type": promotion.get("match_type", "manual_article"),
                "match_evidence": promotion.get("match_evidence", "手動指定の商品URL"),
                "match_confidence": int(promotion.get("match_confidence", 100)),
            }
            if (
                not product_block["thumbnail_url"]
                and str(payload.get("content_mode") or "") == "fanza_product"
            ):
                official_image_ids = [
                    str(image.get("id") or "")
                    for image in payload.get("images", [])
                    if str(image.get("rights_basis") or "").startswith("fanza_product_")
                ]
                if official_image_ids:
                    product_block["thumbnail_image_id"] = official_image_ids[0]
            insert_at = _fanza_insert_index(payload["blocks"], "related")
            payload["blocks"].insert(insert_at, product_block)
    sanitized_payload = sanitize_related_destinations(payload)
    if sanitized_payload is not payload:
        payload.clear()
        payload.update(sanitized_payload)
    has_product_destination = any(
        isinstance(block, dict) and block.get("type") == "product_cta"
        for block in payload.get("blocks", [])
    )
    existing_destination_urls = {
        str(block.get("url") or "").strip()
        for block in payload.get("blocks", [])
        if isinstance(block, dict)
        and block.get("type") in {"product_cta", "related_link"}
        and str(block.get("url") or "").strip()
    }
    related_destinations: list[dict[str, Any]] = []
    resolved_destinations = resolve_article_destinations(
        payload,
        source,
        payload["affiliate_opportunities"],
    )
    for destination in resolved_destinations:
        destination_url = str(destination.get("url") or "").strip()
        link_kind = str(destination.get("link_kind") or "")
        if not destination_url or destination_url in existing_destination_urls:
            continue
        if has_product_destination and link_kind not in {
            "official_profile",
            "official_content",
            "verified_person_search",
        }:
            continue
        existing_destination_urls.add(destination_url)
        related_destinations.append(destination)
    if related_destinations:
        near_media = [
            item for item in related_destinations
            if item.get("link_kind") in {"official_profile", "official_content"}
        ]
        trailing = [item for item in related_destinations if item not in near_media]
        if near_media:
            insert_at = related_link_insert_index(
                payload["blocks"],
                str(near_media[0].get("link_kind") or ""),
            )
            for offset, destination in enumerate(near_media):
                payload["blocks"].insert(insert_at + offset, destination)
        for destination in trailing:
            insert_at = related_link_insert_index(
                payload["blocks"],
                str(destination.get("link_kind") or ""),
            )
            payload["blocks"].insert(insert_at, destination)
        if related_destinations:
            related_destination_summaries: list[dict[str, Any]] = []
            for destination in related_destinations:
                destination_summary = {
                    key: destination.get(key)
                    for key in (
                        "url",
                        "title",
                        "provider",
                        "link_kind",
                        "match_confidence",
                    )
                }
                person_name = str(destination.get("person_name") or "").strip()
                if person_name:
                    destination_summary["person_name"] = person_name
                related_destination_summaries.append(destination_summary)
            payload["related_destinations"] = related_destination_summaries
            affiliate_id = (
                load_fanza_settings(site_root).get("affiliate_id", "")
                if site_root is not None
                and any(
                    destination.get("affiliate_network") == "fanza"
                    for destination in related_destinations
                )
                else ""
            )
            if affiliate_id:
                payload["promotion_type"] = "affiliate"
                disclosure = "この記事にはFANZAのアフィリエイト広告が含まれます。"
                existing = str(payload.get("transparency_note") or "")
                payload["transparency_note"] = (
                    existing
                    if disclosure in existing
                    else f"{disclosure} {existing}".strip()
                )[:500]
    if promotion_type == "sponsored":
        disclosure = "この記事は紹介依頼に基づくPR記事です。"
        existing = str(payload.get("transparency_note") or "")
        payload["transparency_note"] = f"{disclosure} {existing}".strip()[:500]
        if not any(
            block.get("id") == "sponsored-disclosure"
            for block in payload["blocks"]
        ):
            payload["blocks"].insert(
                0,
                {"id": "sponsored-disclosure", "type": "ad", "text": disclosure},
            )
    if (payload["affiliate_opportunities"] or related_destinations) and not all_promotions:
        payload["blocks"] = [
            block for block in payload.get("blocks", [])
            if not (
                block.get("type") == "ad"
                and str(block.get("text") or "") == "記事内容に合う関連広告枠"
            )
        ]
    _dedupe_direct_fanza_product_ctas(payload)
    payload["tags"] = clean_article_topic_tags(payload.get("tags"))
    ensure_related_footer(payload)
    if site_root is not None:
        hydrate_related_fanza_products(
            payload,
            site_root,
            lambda query: discover_fanza_products(
                [query], limit_per_query=1, product_kind="video"
            ),
            lambda product_id: str(
                (download_exact_fanza_package(product_id.casefold()) or {}).get(
                    "url", ""
                )
            ),
        )
        # If a named performer query had no verified product, hydration removes
        # that misleading card. Rebuild the footer and hydrate its honest topic
        # fallback in the same pass.
        ensure_related_footer(payload)
        hydrate_related_fanza_products(
            payload,
            site_root,
            lambda query: discover_fanza_products(
                [query], limit_per_query=1, product_kind="video"
            ),
            lambda product_id: str(
                (download_exact_fanza_package(product_id.casefold()) or {}).get(
                    "url", ""
                )
            ),
        )
        localize_related_thumbnail_assets(payload)
        apply_related_thumbnail_fallbacks(payload)


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


def _is_fanza_product_url(value: str) -> bool:
    if not _is_fanza_url(value):
        return False
    unwrapped = _unwrap_external_affiliate_url(value) or value
    parsed = urlparse(unwrapped)
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    return (
        "/detail/" in path
        or ("/content/" in path and bool(query.get("id")))
        or "/product/" in path
        or bool(query.get("cid"))
    )


def _has_verified_fanza_evidence(source: dict[str, Any]) -> bool:
    source_urls = [
        str(source.get("requested_url") or ""),
        str(source.get("url") or ""),
        *[
            str(item.get("url") or "")
            for item in (source.get("links") or [])
            if isinstance(item, dict)
        ],
    ]
    if any(_is_fanza_product_url(url) for url in source_urls):
        return True
    if any(
        _is_fanza_url(url)
        and "/actress/" in urlparse(
            _unwrap_external_affiliate_url(url) or url
        ).path.lower()
        for url in source_urls
    ):
        return True

    source_text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "body_text")
    ).upper()
    ai_product_code = str(source.get("ai_fanza_product_code") or "").strip().upper()
    if not ai_product_code:
        return False
    normalized_text = re.sub(r"[^A-Z0-9]", "", source_text)
    normalized_code = re.sub(r"[^A-Z0-9]", "", ai_product_code)
    return bool(normalized_code and normalized_code in normalized_text)


def _fanza_search_url(query: str) -> str:
    cleaned = " ".join(str(query or "").split())[:120]
    return (
        "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr="
        + quote(cleaned, safe="")
    )


def _fanza_insert_index(blocks: list[dict[str, Any]], match_level: str) -> int:
    if match_level in {"exact", "strong"}:
        # FANZA product drafts deliberately start with the package thumbnail,
        # official sample video, and official gallery. Keep the product card
        # after that opening material instead of cutting between them.
        media_end = 0
        for index, block in enumerate(blocks):
            if block.get("type") in {"images", "videos", "x_embed", "x_timeline"}:
                media_end = index + 1
                continue
            if media_end:
                return media_end
        if media_end:
            return media_end
        return max(1, len(blocks) // 2)
    for index in range(len(blocks) - 1, -1, -1):
        if blocks[index].get("type") != "ad":
            return index + 1
    return len(blocks)


def _fanza_person_insert_index(
    blocks: list[dict[str, Any]], payload_image_ids: set[str]
) -> int:
    matching_indexes = [
        index
        for index, block in enumerate(blocks)
        if block.get("type") in {"images", "x_embed", "x_timeline"}
        and payload_image_ids.intersection(
            str(image_id) for image_id in block.get("image_ids", [])
        )
    ]
    if matching_indexes:
        insert_at = matching_indexes[-1] + 1
        while (
            insert_at < len(blocks)
            and blocks[insert_at].get("type") == "product_cta"
            and str(blocks[insert_at].get("id") or "").startswith("fanza-person-")
        ):
            insert_at += 1
        return insert_at
    return _fanza_insert_index(blocks, "related")


def _resolve_fanza_person_promotions(
    payload: dict[str, Any],
    source: dict[str, Any],
    site_root: Path | None = None,
) -> list[dict[str, Any]]:
    return []


def _payload_block_media_ids(
    blocks: list[dict[str, Any]], block_type: str, id_key: str
) -> list[str]:
    return list(dict.fromkeys(
        str(media_id)
        for block in blocks
        if block.get("type") == block_type
        for media_id in block.get(id_key, [])
        if str(media_id)
    ))


def _payload_package_image_id(payload: dict[str, Any]) -> str:
    return next((
        str(image.get("id") or "")
        for image in payload.get("images") or []
        if isinstance(image, dict)
        and image.get("id")
        and str(image.get("rights_basis") or "") == "fanza_product_main_image"
    ), "")


def _resolve_fanza_media_promotions(
    payload: dict[str, Any],
    source: dict[str, Any],
    site_root: Path | None = None,
) -> list[dict[str, Any]]:
    payload_image_id_by_source = {
        str(image.get("source_id") or ""): str(image.get("id") or "")
        for image in payload.get("images", [])
        if isinstance(image, dict) and image.get("source_id") and image.get("id")
    }
    payload_video_id_by_source: dict[str, str] = {}
    for video in payload.get("videos", []):
        if not isinstance(video, dict) or not video.get("id"):
            continue
        payload_id = str(video["id"])
        payload_video_id_by_source[payload_id] = payload_id
        if video.get("source_id"):
            payload_video_id_by_source[str(video["source_id"])] = payload_id
    promotions: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    verified_products = (
        source.get("verified_fanza_media_products")
        or source.get("verified_fanza_image_products")
        or []
    )
    for product in verified_products:
        if not isinstance(product, dict):
            continue
        destination = _unwrap_external_affiliate_url(str(product.get("url") or ""))
        image_ids = list(dict.fromkeys(
            payload_image_id_by_source.get(str(source_image_id), "")
            for source_image_id in product.get("image_ids", [])
            if payload_image_id_by_source.get(str(source_image_id), "")
        ))
        video_ids = list(dict.fromkeys(
            payload_video_id_by_source.get(str(source_video_id), "")
            for source_video_id in product.get("video_ids", [])
            if payload_video_id_by_source.get(str(source_video_id), "")
        ))
        if (
            not destination
            or destination in seen_urls
            or not _is_fanza_product_url(destination)
            or not (image_ids or video_ids)
        ):
            continue
        seen_urls.add(destination)
        title = " ".join(str(product.get("title") or "").split())[:180]
        media_word = "動画" if video_ids else "画像"
        promotions.append({
            # Drafts keep the exact product destination. article_studio binds
            # the currently configured account ID when previewing/publishing.
            "url": _validate_source_url(destination),
            "title": title or f"この{media_word}のFANZA作品",
            "text": f"上の{media_word}に対応する作品です。作品ページでサンプル、出演者、配信内容を確認できます。",
            "button_text": "FANZAでこの作品を見る",
            "match_level": "media_exact",
            "image_ids": image_ids,
            "video_ids": video_ids,
            "thumbnail_image_id": image_ids[0] if image_ids else "",
            "thumbnail_url": str(product.get("thumbnail_url") or "")[:2048],
            "thumbnail_source_kind": str(product.get("thumbnail_source_kind") or ""),
            "thumbnail_owner_url": str(
                product.get("thumbnail_owner_url") or destination
            )[:2048],
            "placement_label": f"この{media_word}の商品",
            "match_type": "exact_video" if video_ids else "exact_image",
            "match_evidence": str(product.get("reason") or "商品URLまたは品番を確認")[:300],
            "match_confidence": max(0, min(100, int(product.get("match_confidence") or 95))),
        })

    if promotions:
        return promotions

    # On a FANZA product page, the page URL itself identifies the product. Tie
    # its single CTA to the official sample video, or to the lead image when no
    # sample video exists. Links found elsewhere on a page are not accepted here.
    source_product_url = next((
        str(source.get(key) or "")
        for key in ("requested_url", "url")
        if _is_fanza_product_url(str(source.get(key) or ""))
    ), "")
    if not source_product_url:
        return []
    exact = _resolve_fanza_promotion(
        source,
        {"content_mode": "fanza_product", "promotion_type": "affiliate"},
        site_root,
    )
    if not exact:
        return []
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
    video_ids = _payload_block_media_ids(blocks, "videos", "video_ids")
    image_ids = [] if video_ids else _payload_block_media_ids(blocks, "images", "image_ids")[:1]
    if not video_ids and not image_ids:
        return []
    media_word = "動画" if video_ids else "画像"
    exact.update({
        "text": f"上の{media_word}と同じFANZA作品です。作品ページでサンプル、出演者、配信内容を確認できます。",
        "image_ids": image_ids,
        "video_ids": video_ids,
        "thumbnail_image_id": image_ids[0] if image_ids else "",
        "placement_label": f"この{media_word}の商品",
        "match_type": "exact_video" if video_ids else "exact_image",
        "match_evidence": "記事元URLとFANZA商品詳細URLが一致",
        "match_confidence": 100,
    })
    package_url = _official_fanza_package_url(source, source_product_url)
    exact["thumbnail_url"] = package_url
    package_image_id = _payload_package_image_id(payload)
    exact["thumbnail_source_kind"] = (
        "fanza_package" if package_url or package_image_id else ""
    )
    exact["thumbnail_owner_url"] = exact["url"]
    exact["thumbnail_image_id"] = "" if package_url else package_image_id
    promotions.append(exact)
    return promotions


def _insert_media_product_promotions(
    blocks: list[dict[str, Any]],
    promotions: list[dict[str, Any]],
) -> None:
    for number, promotion in enumerate(promotions, start=1):
        target_videos = set(str(item) for item in promotion.get("video_ids", []))
        target_images = set(str(item) for item in promotion.get("image_ids", []))
        location: tuple[int, int, str, str] | None = None
        # A product video is the strongest placement target. When the same
        # product also has package images, keep the CTA directly below video.
        for block_index, block in enumerate(blocks):
            if block.get("type") != "videos":
                continue
            for media_index, media_id in enumerate(block.get("video_ids", [])):
                if str(media_id) in target_videos:
                    location = (block_index, media_index, "videos", "video_ids")
        if location is None:
            for block_index, block in enumerate(blocks):
                for media_index, media_id in enumerate(block.get("image_ids", [])):
                    if str(media_id) in target_images:
                        location = (block_index, media_index, str(block.get("type") or "images"), "image_ids")
        if location is None:
            continue
        block_index, media_index, media_type, id_key = location
        media_word = "動画" if id_key == "video_ids" else "画像"
        product_block = {
            "id": f"fanza-media-product-{number}",
            "type": "product_cta",
            "url": promotion["url"],
            "title": promotion["title"],
            "text": f"上の{media_word}に対応する作品です。作品ページでサンプル、出演者、配信内容を確認できます。",
            "button_text": promotion["button_text"],
            "thumbnail_image_id": promotion.get("thumbnail_image_id", ""),
            "thumbnail_url": promotion.get("thumbnail_url", ""),
            "thumbnail_source_kind": promotion.get("thumbnail_source_kind", ""),
            "thumbnail_owner_url": promotion.get(
                "thumbnail_owner_url", promotion["url"]
            ),
            "placement_label": f"この{media_word}の商品",
            "match_type": "exact_video" if id_key == "video_ids" else "exact_image",
            "match_evidence": promotion.get("match_evidence", "商品URLまたは品番を確認"),
            "match_confidence": int(promotion.get("match_confidence", 95)),
        }
        owner = blocks[block_index]
        media_ids = list(owner.get(id_key, []))
        can_split = media_type in {"images", "videos"}
        if can_split and media_index < len(media_ids) - 1:
            prefix = {**owner, id_key: media_ids[:media_index + 1]}
            suffix = {
                **owner,
                "id": f"{owner.get('id', media_type)}-after-pr-{number}",
                id_key: media_ids[media_index + 1:],
            }
            blocks[block_index:block_index + 1] = [prefix, product_block, suffix]
        else:
            blocks.insert(block_index + 1, product_block)


# Compatibility names for old tests and saved drafts. Both now use the strict
# image/video implementation above.
_resolve_fanza_image_promotions = _resolve_fanza_media_promotions
_insert_image_product_promotions = _insert_media_product_promotions


def _resolve_verified_fanza_recommendations(
    source: dict[str, Any],
    site_root: Path | None = None,
) -> list[dict[str, str]]:
    return []


def _resolve_fanza_fallback(
    source: dict[str, Any],
    site_root: Path | None = None,
) -> dict[str, str] | None:
    return None


def _resolve_fanza_promotion(
    source: dict[str, Any],
    intent: dict[str, Any],
    site_root: Path | None = None,
) -> dict[str, Any] | None:
    explicit = str(intent.get("fanza_url") or "").strip()
    if explicit:
        if not _is_fanza_product_url(explicit):
            raise RuntimeError("FANZA誘導URLには作品の商品詳細URLを入力してください")
        destination = _validate_source_url(explicit)
    else:
        source_urls = [
            str(source.get("requested_url") or ""),
            str(source.get("url") or ""),
        ]
        destination = next(
            (url for url in source_urls if _is_fanza_product_url(url)),
            "",
        )

    content_mode = str(intent.get("content_mode") or "auto")
    promotion_type = str(intent.get("promotion_type") or "organic")
    is_related = bool(destination and _is_fanza_product_url(destination))
    should_promote = (
        is_related
        and (
            content_mode == "fanza_product"
            or promotion_type == "affiliate"
            or
            content_mode in {"auto", "web", "x_account", "x_post"}
        )
    )
    if not should_promote:
        return None
    title = str(source.get("title") or source.get("description") or "FANZA作品").strip()[:180]
    destination = _unwrap_external_affiliate_url(destination)
    if not destination or not _is_fanza_product_url(destination):
        return None
    package_url = _official_fanza_package_url(source, destination)
    return {
        "url": _validate_source_url(destination),
        "title": title,
        "text": "作品ページでサンプル、価格、出演者、配信内容を確認できます。",
        "button_text": "FANZAでこの作品を見る",
        "match_level": "exact",
        "placement_label": "この記事の商品",
        "match_type": "exact_article",
        "match_evidence": "FANZA商品詳細URLを確認",
        "match_confidence": 100,
        "thumbnail_url": package_url,
        "thumbnail_source_kind": "fanza_package" if package_url else "",
        "thumbnail_owner_url": destination if package_url else "",
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
            started_at = datetime.now(JST).isoformat(timespec="seconds")
            self.signals.progress.emit(15, "登録した情報源を巡回しています")
            candidates = discover_candidates(
                self.site_root,
                self.per_source_limit,
                self.source_ids,
            )
            observed_urls = [
                str(candidate.get("url") or "")
                for candidate in list_candidates(self.site_root)
                if str(candidate.get("last_observed_at") or "") >= started_at
            ]
            self.signals.progress.emit(100, "候補URLの収集が完了しました")
            self.signals.completed.emit({
                "count": len(candidates),
                "observed_urls": observed_urls,
            })
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class SourceDiscoveryWorker(QRunnable):
    def __init__(self, site_root: Path, *, force: bool = False) -> None:
        super().__init__()
        self.site_root = site_root
        self.force = force
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(10, "新しい情報源を検索しています")
            result = discover_new_sources(self.site_root, force=self.force)
            added = len(result.get("added") or [])
            checked = int(result.get("checked") or 0)
            self.signals.progress.emit(
                100,
                f"新規サイト候補{checked}件を確認し、{added}件を追加しました",
            )
            self.signals.completed.emit(result)
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
            x_result: dict[str, Any] = {}
            x_error = ""
            already_published = bool(self.payload.get("published_url")) or str(
                self.payload.get("editorial_status") or self.payload.get("status") or ""
            ) == "published"
            result = publish_article(
                self.payload,
                self.site_root,
                self.site,
                lambda value, message: self.signals.progress.emit(
                    min(94, 2 + value * 92 // 100), message
                ),
            )
            if not already_published:
                try:
                    self.signals.progress.emit(96, "公開記事をXの候補プールへ追加しています")
                    x_post = prepare_publish_x_post(
                        self.site_root,
                        self.payload,
                        self.site.public_url,
                    )
                    if x_post:
                        x_result = {"queued": [str(x_post.get("post_id") or "")]}
                except Exception as exc:
                    traceback.print_exc()
                    x_error = str(exc) or exc.__class__.__name__
            result["x_post"] = x_result
            result["x_error"] = x_error
            result["x_attempted"] = not already_published
            result["x_queued"] = bool(x_result.get("queued"))
            slug = str(result.get("slug") or self.payload.get("slug") or "")
            if slug:
                remove_from_queue(self.site_root, slug, "published")
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
