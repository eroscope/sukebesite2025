from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

from PySide6.QtCore import Qt, QThreadPool, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDial,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QSpinBox,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from article_studio import (
    JST,
    VIDEO_EMBED_STYLE,
    X_EMBED_STYLE,
    StudioServer,
    _extract_sample_assets,
    build_article,
    list_drafts,
    load_draft_payload,
    save_draft,
    update_draft_rights,
)
from indanya_desktop.sites import ManagedSite, SiteRegistry
from indanya_desktop.theme import APP_STYLE
from indanya_desktop.automation import (
    add_source,
    due_continuous_crawl,
    due_crawl_runs,
    due_publish_runs,
    enable_continuous_crawl,
    ensure_fanza_manga_source,
    enqueue_article,
    filter_candidates_by_source_mix,
    list_candidates,
    list_source_discovery_log,
    load_automation_settings,
    list_sources,
    is_fanza_manga_candidate,
    manga_replenishment_run,
    manual_crawl_run,
    mark_candidates_status,
    queue_position_map,
    record_automation_run,
    clear_continuous_rate_limit,
    record_continuous_article,
    record_continuous_crawl,
    record_continuous_rate_limit,
    record_source_selection,
    remove_from_queue,
    remove_source,
    save_automation_settings,
    save_candidates,
    save_sources,
    sort_candidates_balanced,
    soft_delete_article,
    source_discovery_due,
    source_discovery_status,
    update_review_status,
    update_source,
)
from indanya_desktop.workers import (
    ApplyFanzaAffiliateWorker,
    ChatGptLoginWorker,
    ChatGptSendWorker,
    CodexSendWorker,
    CollectCandidatesWorker,
    DownloadVideoWorker,
    GenerateArticleWorker,
    PublishArticleWorker,
    ReviewActionWorker,
    RefineDraftWorker,
    SitemapHealthWorker,
    SourceDiscoveryWorker,
    load_fanza_settings,
    UnpublishArticleWorker,
    XLoginWorker,
    XCopyWorker,
    XDailyWorker,
    XScheduleWorker,
    XTrendWorker,
    AnalyticsWorker,
)
from indanya_desktop.analytics import (
    ensure_ga4_owner_identity,
    ga4_credentials_path,
    ga4_url, load_ga4_cache, load_ga4_measurement_id, local_content_summary,
    owner_registration_url, save_ga4_measurement_id,
    load_ga4_property_id, save_ga4_property_id, save_ga4_credentials,
)
from indanya_desktop.owner_collector import (
    OwnerCollectorHandle,
    collector_available,
    start_owner_collector,
)
from indanya_desktop.publishing import publish_ga4_config
from indanya_desktop.chatgpt_queue import (
    enqueue_chatgpt_request,
    find_duplicate_drafts,
    latest_chatgpt_batch_summary,
    list_chatgpt_requests,
    pending_chatgpt_count,
    queued_chatgpt_request_ids,
    recent_chatgpt_activity,
    reconcile_chatgpt_requests,
    stop_pending_chatgpt_requests,
)
from indanya_desktop.browser_capture import chatgpt_login_ready, x_login_ready
from indanya_desktop.editorial_policy import is_fanza_product_url
from indanya_desktop.site_learning import (
    bootstrap_site_learning,
    can_attempt_site,
    list_site_learning,
)
from indanya_desktop.social_x import (
    block_x_reply_handle,
    canonical_x_status_url,
    choose_x_reply_link,
    list_x_posts,
    load_x_trend_state,
    load_x_settings,
    prepare_discovered_x_reply,
    prepare_x_contest_candidate,
    prepare_x_candidates,
    prepare_x_manga_thread,
    prepare_due_x_manga_thread,
    prepare_due_x_reply_candidate,
    mark_x_manga_replenishing,
    notify_x_manga_article_published,
    prepare_x_viral_reply,
    record_x_post_performance,
    refresh_x_reply_candidate_score,
    save_x_settings,
    score_x_reply_candidate,
    update_x_post,
    x_post_media_paths,
    x_post_intent_url,
    x_daily_posting_status,
    x_follow_candidates,
    x_manga_schedule_status,
    x_reply_schedule_status,
    x_reply_intent_url,
    x_thread_intent_url,
    advance_x_thread,
    x_template_performance,
    x_trend_scan_status,
)
from indanya_desktop.sitemap_health import (
    load_sitemap_health,
    search_console_sitemaps_url,
)
from indanya_desktop.affiliate_opportunities import (
    normalize_affiliate_opportunities,
    registration_recommendations,
)
from indanya_desktop.adaptive_quality import (
    FAILURE_LABELS as QUALITY_FAILURE_LABELS,
    article_quality_report,
    changed_fields,
    record_editorial_feedback,
    run_quality_routines,
    sync_ga4_performance,
)
from indanya_desktop.outreach import (
    STATUS_LABELS as OUTREACH_STATUS_LABELS,
    TARGET_CATEGORIES as OUTREACH_TARGET_CATEGORIES,
    bootstrap_outreach_targets,
    list_outreach_targets,
    load_outreach_profile,
    outreach_link_html,
    outreach_message,
    outreach_profile_text,
    remove_outreach_target,
    update_outreach_status,
    upsert_outreach_target,
)


def button(text: str, kind: str = "secondary") -> QPushButton:
    result = QPushButton(text)
    result.setObjectName(kind)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    return result


def panel(layout: QVBoxLayout | QHBoxLayout, accent: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("accentPanel" if accent else "panel")
    frame.setLayout(layout)
    return frame


def heading(title: str, description: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 8)
    label = QLabel(title)
    label.setObjectName("sectionTitle")
    layout.addWidget(label)
    if description:
        sub = QLabel(description)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return box


class PreviewPage(QWebEnginePage):
    video_requested = Signal(str)

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:  # noqa: N802
        if url.scheme() == "indanya-video":
            video_id = url.path().strip("/")
            if video_id:
                self.video_requested.emit(video_id)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class ReviewActionPage(QWebEnginePage):
    action_requested = Signal(str, str)

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:  # noqa: N802
        if url.scheme() == "indanya-action":
            action = url.host()
            slug = url.path().strip("/")
            if action and slug:
                self.action_requested.emit(action, slug)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class TimeWheel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hours = QListWidget()
        self.minutes = QListWidget()
        for value in range(24):
            self.hours.addItem(f"{value:02d}")
        for value in range(0, 60, 5):
            self.minutes.addItem(f"{value:02d}")
        for widget, label in ((self.hours, "時"), (self.minutes, "分")):
            widget.setFixedSize(92, 166)
            widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            column = QVBoxLayout()
            column.addWidget(QLabel(label), 0, Qt.AlignmentFlag.AlignCenter)
            column.addWidget(widget)
            layout.addLayout(column)

    def set_value(self, value: str) -> None:
        parsed = QTime.fromString(value, "HH:mm")
        if not parsed.isValid():
            parsed = QTime(0, 0)
        self.hours.setCurrentRow(parsed.hour())
        self.minutes.setCurrentRow(round(parsed.minute() / 5) % 12)
        self.hours.scrollToItem(self.hours.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.minutes.scrollToItem(self.minutes.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)

    def value(self) -> str:
        hour = max(0, self.hours.currentRow())
        minute = max(0, self.minutes.currentRow()) * 5
        return f"{hour:02d}:{minute:02d}"


class ScheduleEditor(QWidget):
    def __init__(self, slots: list[dict], sources: list[dict] | None = None) -> None:
        super().__init__()
        self.sources = sources
        self.slots = [dict(item) for item in slots]
        self.loading = False
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        self.slot_list = QListWidget()
        self.slot_list.setMinimumWidth(280)
        self.slot_list.currentRowChanged.connect(self._load_current)
        left.addWidget(self.slot_list, 1)
        slot_buttons = QHBoxLayout()
        add = button("+", "primary")
        add.setToolTip("時刻を追加")
        add.clicked.connect(self.add_slot)
        remove = button("−", "danger")
        remove.setToolTip("選択した時刻を削除")
        remove.clicked.connect(self.remove_slot)
        slot_buttons.addWidget(add)
        slot_buttons.addWidget(remove)
        slot_buttons.addStretch()
        left.addLayout(slot_buttons)
        layout.addLayout(left, 4)

        right = QVBoxLayout()
        self.time_wheel = TimeWheel()
        self.time_wheel.hours.currentRowChanged.connect(self._save_current)
        self.time_wheel.minutes.currentRowChanged.connect(self._save_current)
        right.addWidget(self.time_wheel, 0, Qt.AlignmentFlag.AlignCenter)
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("1回の記事数"))
        self.count_slider = QSlider(Qt.Orientation.Horizontal)
        self.count_slider.setRange(1, 30)
        self.count_slider.valueChanged.connect(self._count_changed)
        count_row.addWidget(self.count_slider, 1)
        self.count_label = QLabel("3件", objectName="success")
        self.count_label.setFixedWidth(46)
        count_row.addWidget(self.count_label)
        right.addLayout(count_row)

        self.source_list: QListWidget | None = None
        if sources is not None:
            right.addWidget(QLabel("巡回する情報源"))
            self.source_list = QListWidget()
            all_item = QListWidgetItem("すべての有効な情報源")
            all_item.setData(Qt.ItemDataRole.UserRole, "")
            all_item.setFlags(all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.source_list.addItem(all_item)
            for source in sources:
                item = QListWidgetItem(str(source.get("name") or source.get("url") or "情報源"))
                item.setData(Qt.ItemDataRole.UserRole, str(source.get("source_id") or ""))
                item.setToolTip(str(source.get("url") or ""))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                self.source_list.addItem(item)
            self.source_list.itemChanged.connect(self._sources_changed)
            right.addWidget(self.source_list, 1)
        else:
            right.addStretch()
        layout.addLayout(right, 6)
        self._refresh_list()

    def _slot_label(self, slot: dict) -> str:
        source_ids = slot.get("source_ids", [])
        source_label = "全情報源" if self.sources is not None and not source_ids else (
            f"{len(source_ids)}情報源" if self.sources is not None else ""
        )
        return f"{slot.get('time', '00:00')}　{slot.get('count', 1)}件　{source_label}".rstrip()

    def _refresh_list(self, selected: int | None = None) -> None:
        current = self.slot_list.currentRow() if selected is None else selected
        self.slot_list.blockSignals(True)
        self.slot_list.clear()
        for slot in self.slots:
            self.slot_list.addItem(self._slot_label(slot))
        self.slot_list.blockSignals(False)
        if self.slots:
            self.slot_list.setCurrentRow(max(0, min(current, len(self.slots) - 1)))
            self._load_current(self.slot_list.currentRow())

    def _load_current(self, row: int) -> None:
        if row < 0 or row >= len(self.slots):
            return
        self.loading = True
        slot = self.slots[row]
        self.time_wheel.set_value(str(slot.get("time") or "00:00"))
        self.count_slider.setValue(int(slot.get("count") or 1))
        self.count_label.setText(f"{self.count_slider.value()}件")
        if self.source_list is not None:
            selected = set(slot.get("source_ids") or [])
            for index in range(self.source_list.count()):
                item = self.source_list.item(index)
                source_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
                item.setCheckState(
                    Qt.CheckState.Checked
                    if (not source_id and not selected) or source_id in selected
                    else Qt.CheckState.Unchecked
                )
        self.loading = False

    def _save_current(self, _value: int = 0) -> None:
        row = self.slot_list.currentRow()
        if self.loading or row < 0 or row >= len(self.slots):
            return
        self.slots[row]["time"] = self.time_wheel.value()
        self.slot_list.item(row).setText(self._slot_label(self.slots[row]))

    def _count_changed(self, value: int) -> None:
        self.count_label.setText(f"{value}件")
        row = self.slot_list.currentRow()
        if self.loading or row < 0 or row >= len(self.slots):
            return
        self.slots[row]["count"] = value
        self.slot_list.item(row).setText(self._slot_label(self.slots[row]))

    def _sources_changed(self, changed_item: QListWidgetItem) -> None:
        row = self.slot_list.currentRow()
        if self.loading or self.source_list is None or row < 0:
            return
        changed_index = self.source_list.row(changed_item)
        self.loading = True
        if changed_index == 0 and changed_item.checkState() == Qt.CheckState.Checked:
            for index in range(1, self.source_list.count()):
                self.source_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        elif changed_index > 0 and changed_item.checkState() == Qt.CheckState.Checked:
            self.source_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        if not any(
            self.source_list.item(index).checkState() == Qt.CheckState.Checked
            for index in range(self.source_list.count())
        ):
            self.source_list.item(0).setCheckState(Qt.CheckState.Checked)
        self.loading = False
        all_checked = self.source_list.item(0).checkState() == Qt.CheckState.Checked
        selected = []
        if not all_checked:
            for index in range(1, self.source_list.count()):
                item = self.source_list.item(index)
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        self.slots[row]["source_ids"] = selected
        self.slot_list.item(row).setText(self._slot_label(self.slots[row]))

    def add_slot(self) -> None:
        slot = {
            "slot_id": f"slot-{time.time_ns()}",
            "time": "12:00",
            "count": 3,
            "source_ids": [],
        }
        self.slots.append(slot)
        self._refresh_list(len(self.slots) - 1)

    def remove_slot(self) -> None:
        row = self.slot_list.currentRow()
        if row < 0:
            return
        self.slots.pop(row)
        self._refresh_list(max(0, row - 1))

    def values(self) -> list[dict]:
        self._save_current()
        return [dict(item) for item in self.slots]


class AutomationSettingsDialog(QDialog):
    def __init__(self, parent: QWidget, settings: dict, sources: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle("常時運転・予約投稿の設定")
        self.resize(840, 600)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        crawl_page = QWidget()
        crawl_layout = QVBoxLayout(crawl_page)
        self.crawl_enabled = QCheckBox("情報源の巡回を有効にする")
        self.crawl_enabled.setChecked(bool(settings.get("auto_crawl_enabled", True)))
        crawl_layout.addWidget(self.crawl_enabled)
        self.continuous_enabled = QCheckBox("記事作成を常時運転する")
        self.continuous_enabled.setChecked(bool(settings.get("continuous_mode_enabled", True)))
        crawl_layout.addWidget(self.continuous_enabled)
        form = QFormLayout()
        self.continuous_max_pending = QSpinBox()
        self.continuous_max_pending.setRange(1, 1)
        self.continuous_max_pending.setSuffix(" 件")
        self.continuous_max_pending.setValue(1)
        form.addRow("同時に処理する記事数（待機なし）", self.continuous_max_pending)
        self.continuous_empty_retry = QSpinBox()
        self.continuous_empty_retry.setRange(1, 120)
        self.continuous_empty_retry.setSuffix(" 分")
        self.continuous_empty_retry.setValue(int(settings.get("continuous_empty_retry_minutes") or 15))
        form.addRow("候補が見つからない時の再確認", self.continuous_empty_retry)
        self.continuous_fanza_max_percent = QSpinBox()
        self.continuous_fanza_max_percent.setRange(0, 100)
        self.continuous_fanza_max_percent.setSuffix(" %")
        self.continuous_fanza_max_percent.setValue(
            int(settings.get("continuous_fanza_max_percent", 20))
        )
        form.addRow("FANZA記事の上限（直近10記事）", self.continuous_fanza_max_percent)
        fanza_mix_note = QLabel(
            "初期値20%。上限に達したら一般記事を優先し、0%ならFANZA記事を自動作成しません。",
            objectName="muted",
        )
        fanza_mix_note.setWordWrap(True)
        crawl_layout.addLayout(form)
        crawl_layout.addWidget(fanza_mix_note)
        self.source_discovery_enabled = QCheckBox("7日ごとに新しい巡回サイトを自動で探す")
        self.source_discovery_enabled.setChecked(
            bool(settings.get("source_discovery_enabled", True))
        )
        crawl_layout.addWidget(self.source_discovery_enabled)
        discovery_form = QFormLayout()
        self.source_discovery_interval = QSpinBox()
        self.source_discovery_interval.setRange(1, 30)
        self.source_discovery_interval.setSuffix(" 日")
        self.source_discovery_interval.setValue(
            int(settings.get("source_discovery_interval_days") or 7)
        )
        discovery_form.addRow("新規サイトを探す間隔", self.source_discovery_interval)
        self.source_discovery_max_additions = QSpinBox()
        self.source_discovery_max_additions.setRange(1, 10)
        self.source_discovery_max_additions.setSuffix(" サイト")
        self.source_discovery_max_additions.setValue(
            int(settings.get("source_discovery_max_additions") or 2)
        )
        discovery_form.addRow("1回に自動追加する上限", self.source_discovery_max_additions)
        crawl_layout.addLayout(discovery_form)
        crawl_layout.addWidget(QLabel("巡回に使う情報源（未選択なら有効な全情報源）", objectName="muted"))
        self.continuous_sources = QListWidget()
        selected = set(settings.get("continuous_source_ids") or [])
        for source in sources:
            item = QListWidgetItem(str(source.get("name") or source.get("url") or "情報源"))
            item.setData(Qt.ItemDataRole.UserRole, str(source.get("source_id") or ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in selected else Qt.CheckState.Unchecked)
            self.continuous_sources.addItem(item)
        crawl_layout.addWidget(self.continuous_sources, 1)
        tabs.addTab(crawl_page, "常時運転")

        publish_page = QWidget()
        publish_layout = QVBoxLayout(publish_page)
        self.publish_enabled = QCheckBox("予約投稿を有効にする")
        self.publish_enabled.setChecked(bool(settings.get("publish_enabled", True)))
        publish_layout.addWidget(self.publish_enabled)
        self.publish_editor = ScheduleEditor(list(settings.get("publish_slots") or []))
        publish_layout.addWidget(self.publish_editor, 1)
        tabs.addTab(publish_page, "予約投稿")
        layout.addWidget(tabs, 1)

        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def values(self) -> dict:
        return {
            "auto_crawl_enabled": self.crawl_enabled.isChecked(),
            "continuous_mode_enabled": self.continuous_enabled.isChecked(),
            "continuous_crawl_enabled": self.crawl_enabled.isChecked(),
            "continuous_max_pending": self.continuous_max_pending.value(),
            "continuous_empty_retry_minutes": self.continuous_empty_retry.value(),
            "continuous_fanza_max_percent": self.continuous_fanza_max_percent.value(),
            "source_discovery_enabled": self.source_discovery_enabled.isChecked(),
            "source_discovery_interval_days": self.source_discovery_interval.value(),
            "source_discovery_max_additions": self.source_discovery_max_additions.value(),
            "continuous_source_ids": [
                str(self.continuous_sources.item(index).data(Qt.ItemDataRole.UserRole) or "")
                for index in range(self.continuous_sources.count())
                if self.continuous_sources.item(index).checkState() == Qt.CheckState.Checked
            ],
            "publish_enabled": self.publish_enabled.isChecked(),
            "publish_slots": [
                {"time": item["time"], "count": item["count"]}
                for item in self.publish_editor.values()
            ],
        }


class XPostingSettingsDialog(QDialog):
    def __init__(self, parent: QWidget, settings: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("X投稿設定")
        self.resize(720, 820)
        layout = QVBoxLayout(self)
        account = QFormLayout()
        self.handle = QLineEdit(str(settings.get("account_handle") or "indanya_sns"))
        self.handle.setPlaceholderText("@を除いたXユーザー名")
        self.count = QSpinBox()
        self.count.setRange(1, 20)
        self.count.setValue(int(settings.get("candidate_count") or 3))
        self.attach = QCheckBox("記事本文の動画・画像を投稿へ添付する")
        self.attach.setChecked(bool(settings.get("attach_thumbnail", True)))
        self.automatic_posting = QCheckBox("上位記事から毎日の送信候補を自動で用意する")
        self.automatic_posting.setChecked(
            bool(settings.get("automatic_posting_enabled", True))
        )
        self.automatic_delivery = QCheckBox(
            "準備した通常投稿・返信・漫画スレッドをXへ自動送信する"
        )
        self.automatic_delivery.setChecked(
            not bool(settings.get("manual_delivery_only", False))
        )
        self.safe_pacing = QCheckBox(
            "安定運用（通常3件＋募集返信1件＋漫画1スレッド・最低3時間間隔）"
        )
        self.safe_pacing.setChecked(bool(settings.get("safe_pacing_enabled", True)))
        self.daily_post_limit = QSpinBox()
        self.daily_post_limit.setRange(1, 3)
        self.daily_post_limit.setSuffix(" 件 / 日")
        self.daily_post_limit.setValue(int(settings.get("daily_post_limit") or 1))
        raw_slots = list(settings.get("daily_slots") or ["08:30", "14:30", "20:30"])
        while len(raw_slots) < 3:
            raw_slots.append(["08:30", "14:30", "20:30"][len(raw_slots)])
        self.daily_slots: list[QTimeEdit] = []
        for value in raw_slots[:3]:
            field = QTimeEdit()
            field.setDisplayFormat("HH:mm")
            parsed = QTime.fromString(str(value), "HH:mm")
            field.setTime(parsed if parsed.isValid() else QTime(8, 30))
            self.daily_slots.append(field)
        self.trend_enabled = QCheckBox("1日1回、Xのバズった成人向け投稿から流行を調査する")
        self.trend_enabled.setChecked(bool(settings.get("trend_scan_enabled", True)))
        self.trend_min_likes = QSpinBox()
        self.trend_min_likes.setRange(100, 1_000_000)
        self.trend_min_likes.setSingleStep(500)
        self.trend_min_likes.setSuffix(" いいね以上")
        self.trend_min_likes.setValue(int(settings.get("trend_min_likes") or 1000))
        self.trend_sample_limit = QSpinBox()
        self.trend_sample_limit.setRange(8, 40)
        self.trend_sample_limit.setSuffix(" 件まで")
        self.trend_sample_limit.setValue(int(settings.get("trend_sample_limit") or 24))
        self.reply_daily_limit = QSpinBox()
        self.reply_daily_limit.setRange(1, 5)
        self.reply_daily_limit.setSuffix(" 件 / 日")
        self.reply_daily_limit.setValue(int(settings.get("reply_daily_limit") or 1))
        self.reply_auto_prepare = QCheckBox(
            "流行調査後、外部リプ候補を1件だけ自動で送信待ちにする"
        )
        self.reply_auto_prepare.setChecked(
            bool(settings.get("reply_auto_prepare_enabled", True))
        )
        self.reply_interval = QSpinBox()
        self.reply_interval.setRange(60, 1440)
        self.reply_interval.setSingleStep(60)
        self.reply_interval.setSuffix(" 分")
        self.reply_interval.setValue(
            int(settings.get("reply_min_interval_minutes") or 480)
        )
        self.reply_max_age = QSpinBox()
        self.reply_max_age.setRange(24, 168)
        self.reply_max_age.setSingleStep(24)
        self.reply_max_age.setSuffix(" 時間以内")
        self.reply_max_age.setValue(
            int(settings.get("reply_target_max_age_hours") or 72)
        )
        self.reply_account_cooldown = QSpinBox()
        self.reply_account_cooldown.setRange(1, 365)
        self.reply_account_cooldown.setSuffix(" 日")
        self.reply_account_cooldown.setValue(
            int(settings.get("reply_account_cooldown_days") or 30)
        )
        self.reply_link_rate = QSpinBox()
        self.reply_link_rate.setRange(0, 100)
        self.reply_link_rate.setSingleStep(10)
        self.reply_link_rate.setSuffix(" %")
        self.reply_link_rate.setValue(
            int(settings.get("reply_link_rate_percent") or 0)
        )
        self.reply_media_mode = QComboBox()
        self.reply_media_mode.addItem("記事の元素材（標準）", "original")
        self.reply_media_mode.addItem("安全カード", "safe_card")
        self.reply_media_mode.addItem("添付なし", "none")
        media_index = self.reply_media_mode.findData(
            str(settings.get("reply_default_media_mode") or "original")
        )
        self.reply_media_mode.setCurrentIndex(max(0, media_index))
        self.reply_blocked_handles = QLineEdit(
            ", ".join(str(value) for value in settings.get("reply_blocked_handles") or [])
        )
        self.reply_blocked_handles.setPlaceholderText("返信しないXユーザー名をカンマ区切り")
        self.owned_contest_cooldown = QSpinBox()
        self.owned_contest_cooldown.setRange(1, 90)
        self.owned_contest_cooldown.setSuffix(" 日")
        self.owned_contest_cooldown.setValue(
            int(settings.get("owned_contest_cooldown_days") or 7)
        )
        self.manga_recurring_enabled = QCheckBox(
            "FANZA公式漫画から定期的に5枚スレッド候補を用意する"
        )
        self.manga_recurring_enabled.setChecked(
            bool(settings.get("manga_recurring_enabled", True))
        )
        self.manga_interval_days = QSpinBox()
        self.manga_interval_days.setRange(1, 14)
        self.manga_interval_days.setSuffix(" 日ごと")
        self.manga_interval_days.setValue(
            int(settings.get("manga_interval_days") or 1)
        )
        self.manga_slot = QTimeEdit()
        self.manga_slot.setDisplayFormat("HH:mm")
        manga_time = QTime.fromString(str(settings.get("manga_slot") or "19:30"), "HH:mm")
        self.manga_slot.setTime(manga_time if manga_time.isValid() else QTime(19, 30))
        self.manga_product_cooldown = QSpinBox()
        self.manga_product_cooldown.setRange(30, 365)
        self.manga_product_cooldown.setSuffix(" 日")
        self.manga_product_cooldown.setValue(
            int(settings.get("manga_product_cooldown_days") or 90)
        )
        self.manga_title_cooldown = QSpinBox()
        self.manga_title_cooldown.setRange(7, 90)
        self.manga_title_cooldown.setSuffix(" 日")
        self.manga_title_cooldown.setValue(
            int(settings.get("manga_title_cooldown_days") or 30)
        )
        account.addRow("投稿先", self.handle)
        account.addRow("1回の候補数", self.count)
        account.addRow("添付", self.attach)
        account.addRow("候補の自動準備", self.automatic_posting)
        account.addRow("Xへの送信", self.automatic_delivery)
        account.addRow("頻度保護", self.safe_pacing)
        account.addRow("通常投稿の上限", self.daily_post_limit)
        for index, field in enumerate(self.daily_slots, start=1):
            account.addRow(f"投稿枠 {index}", field)
        account.addRow("流行調査", self.trend_enabled)
        account.addRow("バズ判定", self.trend_min_likes)
        account.addRow("調査数", self.trend_sample_limit)
        account.addRow("外部投稿への返信上限", self.reply_daily_limit)
        account.addRow("外部リプ候補", self.reply_auto_prepare)
        account.addRow("返信間隔", self.reply_interval)
        account.addRow("返信できる募集", self.reply_max_age)
        account.addRow("同じ相手への間隔", self.reply_account_cooldown)
        account.addRow("記事リンクを入れる割合", self.reply_link_rate)
        account.addRow("返信の標準添付", self.reply_media_mode)
        account.addRow("返信対象外", self.reply_blocked_handles)
        account.addRow("自分主催で同じ記事を使う間隔", self.owned_contest_cooldown)
        account.addRow("漫画スレッド", self.manga_recurring_enabled)
        account.addRow("漫画の間隔", self.manga_interval_days)
        account.addRow("漫画の準備時刻", self.manga_slot)
        account.addRow("同じ漫画作品を空ける期間", self.manga_product_cooldown)
        account.addRow("似た題名・同じサークルを空ける期間", self.manga_title_cooldown)
        self.safe_pacing.toggled.connect(self._sync_safe_pacing)
        self._sync_safe_pacing(self.safe_pacing.isChecked())
        layout.addLayout(account)
        note = QLabel(
            "安定運用では通常投稿3件、募集返信1件、漫画1スレッドまでを最低3時間空けます。自動送信がONなら通常投稿は予約枠へ送り、返信は対象投稿へ、漫画は公式試し読み5枚・淫談屋の記事・作品PRをひとつのスレッドとして送ります。途中で止まった漫画は、送信済みの続きから再開します。",
            objectName="muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def _sync_safe_pacing(self, enabled: bool) -> None:
        self.daily_post_limit.setMaximum(3)
        self.reply_daily_limit.setMaximum(1 if enabled else 5)
        if enabled:
            self.reply_daily_limit.setValue(1)
            self.reply_interval.setMinimum(180)
            self.reply_interval.setValue(max(180, self.reply_interval.value()))
            self.reply_link_rate.setValue(0)
        else:
            self.reply_interval.setMinimum(60)

    def values(self) -> dict:
        return {
            "account_handle": self.handle.text().strip().lstrip("@"),
            "candidate_count": self.count.value(),
            "attach_thumbnail": self.attach.isChecked(),
            "automatic_posting_enabled": self.automatic_posting.isChecked(),
            "manual_delivery_only": not self.automatic_delivery.isChecked(),
            "safe_pacing_enabled": self.safe_pacing.isChecked(),
            "daily_post_limit": self.daily_post_limit.value(),
            "global_daily_action_limit": 5,
            "global_min_interval_minutes": (
                180 if self.safe_pacing.isChecked() else 60
            ),
            "daily_slots": [field.time().toString("HH:mm") for field in self.daily_slots],
            "trend_scan_enabled": self.trend_enabled.isChecked(),
            "trend_min_likes": self.trend_min_likes.value(),
            "trend_sample_limit": self.trend_sample_limit.value(),
            "reply_daily_limit": self.reply_daily_limit.value(),
            "reply_auto_prepare_enabled": self.reply_auto_prepare.isChecked(),
            "reply_min_interval_minutes": self.reply_interval.value(),
            "reply_target_max_age_hours": self.reply_max_age.value(),
            "reply_account_cooldown_days": self.reply_account_cooldown.value(),
            "reply_link_rate_percent": self.reply_link_rate.value(),
            "reply_default_media_mode": str(self.reply_media_mode.currentData()),
            "reply_blocked_handles": self.reply_blocked_handles.text(),
            "owned_contest_cooldown_days": self.owned_contest_cooldown.value(),
            "manga_recurring_enabled": self.manga_recurring_enabled.isChecked(),
            "manga_interval_days": self.manga_interval_days.value(),
            "manga_slot": self.manga_slot.time().toString("HH:mm"),
            "manga_product_cooldown_days": self.manga_product_cooldown.value(),
            "manga_title_cooldown_days": self.manga_title_cooldown.value(),
            "manga_max_pending": 1,
            "manga_prefer_sale": True,
            "manga_prefer_popular": True,
        }


class XPerformanceDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("X投稿の24時間反応を記録")
        self.resize(520, 470)
        layout = QVBoxLayout(self)
        description = QLabel(
            "投稿から約24時間後の数字を入れると、次回から反応の良いCodexテンプレを優先します。",
            objectName="muted",
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        performance = row.get("performance") or {}
        self.post_url = QLineEdit(str(row.get("x_post_url") or ""))
        self.post_url.setPlaceholderText("https://x.com/ユーザー/status/数字")
        form.addRow("投稿URL", self.post_url)
        self.metrics: dict[str, QSpinBox] = {}
        labels = {
            "views": "表示",
            "likes": "いいね",
            "reposts": "リポスト",
            "replies": "返信",
            "link_clicks": "記事への流入",
        }
        for key, label in labels.items():
            field = QSpinBox()
            field.setRange(0, 2_000_000_000)
            field.setGroupSeparatorShown(True)
            field.setValue(int(performance.get(key) or 0))
            form.addRow(label, field)
            self.metrics[key] = field
        layout.addLayout(form)
        layout.addStretch()
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Save).setText("記録して学習")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def values(self) -> dict:
        return {
            "post_url": self.post_url.text().strip(),
            **{key: field.value() for key, field in self.metrics.items()},
        }


class VideoPlayerDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, source: QUrl) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 650)
        self.setMinimumSize(640, 460)
        layout = QVBoxLayout(self)
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background:#08090a;")
        layout.addWidget(self.video_widget, 1)

        controls = QHBoxLayout()
        self.play_button = button("▶", "primary")
        self.play_button.setFixedWidth(48)
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.seek)
        controls.addWidget(self.position_slider, 1)
        self.time_label = QLabel("0:00 / 0:00", objectName="muted")
        controls.addWidget(self.time_label)
        layout.addLayout(controls)
        self.status_label = QLabel("動画を読み込んでいます…", objectName="muted")
        layout.addWidget(self.status_label)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._error)
        self.player.setSource(source)
        self.player.play()

    @staticmethod
    def _clock(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek(self, value: int) -> None:
        self.player.setPosition(value)

    def _state_changed(self, state) -> None:
        self.play_button.setText("Ⅱ" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _position_changed(self, position: int) -> None:
        self.position_slider.setValue(position)
        self.time_label.setText(f"{self._clock(position)} / {self._clock(self.player.duration())}")

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self.time_label.setText(f"0:00 / {self._clock(duration)}")

    def _media_status_changed(self, status) -> None:
        if status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            self.status_label.setText("再生できます")

    def _error(self, error, error_text: str) -> None:
        self.status_label.setText(f"動画を再生できません: {error_text}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)


class SiteDialog(QDialog):
    def __init__(self, parent: QWidget, site: ManagedSite | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("管理サイトを編集" if site else "管理サイトを追加")
        self.setMinimumWidth(590)
        form = QFormLayout(self)
        self.name = QLineEdit(site.name if site else "")
        self.public_url = QLineEdit(site.public_url if site else "")
        self.local_path = QLineEdit(site.local_path if site else "")
        path_row = QHBoxLayout()
        path_row.addWidget(self.local_path)
        choose = button("選択")
        choose.clicked.connect(self.choose_folder)
        path_row.addWidget(choose)
        self.repository_url = QLineEdit(site.repository_url if site else "")
        self.github_token = QLineEdit(getattr(site, "github_token", "") if site else "")
        self.github_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.github_token.setPlaceholderText("GitHub Personal Access Token")
        self.provider = QComboBox()
        self.provider.addItems(["GitHub Pages", "その他"])
        self.provider.setCurrentText(site.provider if site else "GitHub Pages")
        form.addRow("サイト名", self.name)
        form.addRow("公開URL", self.public_url)
        form.addRow("作業フォルダ", path_row)
        form.addRow("リポジトリURL", self.repository_url)
        form.addRow("GitHub Token", self.github_token)
        form.addRow("公開方式", self.provider)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        form.addRow(actions)

    def choose_folder(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "サイトの作業フォルダを選択", self.local_path.text())
        if value:
            self.local_path.setText(value)

    def values(self) -> dict[str, str]:
        return {
            "name": self.name.text(), "public_url": self.public_url.text(),
            "local_path": self.local_path.text(), "repository_url": self.repository_url.text(),
            "github_token": self.github_token.text(),
            "provider": self.provider.currentText(),
        }


class OutreachTargetDialog(QDialog):
    def __init__(self, parent: QWidget, target: dict | None = None) -> None:
        super().__init__(parent)
        target = target or {}
        self.setWindowTitle("掲載先を編集" if target else "掲載先を追加")
        self.setMinimumWidth(620)
        form = QFormLayout(self)
        self.name = QLineEdit(str(target.get("name") or ""))
        self.site_url = QLineEdit(str(target.get("site_url") or ""))
        self.contact_url = QLineEdit(str(target.get("contact_url") or ""))
        self.category = QComboBox()
        self.category.addItems(list(OUTREACH_TARGET_CATEGORIES))
        self.category.setCurrentText(str(target.get("category") or "その他"))
        self.status = QComboBox()
        for key, label in OUTREACH_STATUS_LABELS.items():
            self.status.addItem(label, key)
        status_index = self.status.findData(str(target.get("status") or "candidate"))
        self.status.setCurrentIndex(max(0, status_index))
        self.fit_reason = QLineEdit(str(target.get("fit_reason") or ""))
        self.fit_reason.setPlaceholderText("例：成人向け三次元サイトの相互リンクを募集")
        self.notes = QPlainTextEdit(str(target.get("notes") or ""))
        self.notes.setMaximumHeight(110)
        form.addRow("掲載先名", self.name)
        form.addRow("サイトURL", self.site_url)
        form.addRow("連絡・申請URL", self.contact_url)
        form.addRow("種類", self.category)
        form.addRow("状態", self.status)
        form.addRow("候補にした理由", self.fit_reason)
        form.addRow("確認事項・返答", self.notes)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        form.addRow(actions)

    def values(self) -> dict[str, str]:
        return {
            "name": self.name.text(),
            "site_url": self.site_url.text(),
            "contact_url": self.contact_url.text(),
            "category": self.category.currentText(),
            "status": str(self.status.currentData() or "candidate"),
            "fit_reason": self.fit_reason.text(),
            "notes": self.notes.toPlainText(),
        }


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.registry = SiteRegistry(project_root)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_worker: GenerateArticleWorker | None = None
        self.x_login_worker: XLoginWorker | None = None
        self.x_copy_worker: XCopyWorker | None = None
        self.x_schedule_worker: XScheduleWorker | None = None
        self.x_daily_worker: XDailyWorker | None = None
        self.x_trend_worker: XTrendWorker | None = None
        self.x_schedule_after_copy_ids: list[str] = []
        self.chatgpt_login_worker: ChatGptLoginWorker | None = None
        self.chatgpt_send_worker: CodexSendWorker | None = None
        self.analytics_worker: AnalyticsWorker | None = None
        self.analytics_realtime_worker: AnalyticsWorker | None = None
        self.sitemap_health_worker: SitemapHealthWorker | None = None
        self.ga4_history_loaded_once = False
        self.ga4_reports: dict[str, dict] = {}
        self.fanza_affiliate_worker: ApplyFanzaAffiliateWorker | None = None
        self.fanza_affiliate_progress: QProgressDialog | None = None
        self.chatgpt_sending_request_ids: list[str] = []
        self.refine_worker: RefineDraftWorker | None = None
        self.collect_worker: CollectCandidatesWorker | None = None
        self.source_discovery_worker: SourceDiscoveryWorker | None = None
        self.publish_worker: PublishArticleWorker | None = None
        self.unpublish_worker: UnpublishArticleWorker | None = None
        self.publish_progress: QProgressDialog | None = None
        self.publish_queue: list[tuple[str, str]] = []
        self.publish_batch_total = 0
        self.scheduled_collect = False
        self.scheduled_crawl_keys: list[str] = []
        self.scheduled_crawl_run: dict = {}
        self.scheduled_publish_slugs: list[str] = []
        self.scheduled_publish_key = ""
        self.scheduled_publish_active = False
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        self.review_publish_progress: dict[str, int] = {}
        self.publish_queued_slugs: set[str] = set()
        self.review_action_workers: dict[str, ReviewActionWorker] = {}
        self.review_status_cache: dict[str, str] = {}
        self.review_queue_count = 0
        self.automation_completed_signature: tuple[tuple[str, str], ...] = ()
        self.automation_roadmap_widgets: list[dict[str, QLabel | QFrame]] = []
        self.automation_roadmap_high_watermark = -1
        self.automation_phase_index = -1
        self.automation_progress_value = 0
        self.review_scroll_y = 0
        self.review_outer_scroll_y = 0
        self.current_slug = ""
        self.preview_videos: dict[str, dict] = {}
        self.video_windows: list[VideoPlayerDialog] = []
        self.video_downloads: list[DownloadVideoWorker] = []
        self.video_progress: list[QProgressDialog] = []
        self.preview_server: StudioServer | None = None
        self.preview_thread: threading.Thread | None = None
        self.owner_collector: OwnerCollectorHandle | None = None
        self.owner_collector_error = ""
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("淫談屋 記事編集室")
        self.resize(1420, 900)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(APP_STYLE)
        try:
            self.fanza_manga_source = ensure_fanza_manga_source(self.site.root)
        except Exception:
            traceback.print_exc()
            self.fanza_manga_source = {}
        self.stopped_stale_requests = stop_pending_chatgpt_requests(
            self.site.root,
            "最新情報を優先するため、前回から待機していた候補を停止しました",
        )
        try:
            self.site_learning_bootstrap_count = bootstrap_site_learning(self.site.root)
        except Exception:
            traceback.print_exc()
            self.site_learning_bootstrap_count = 0
        try:
            run_quality_routines(self.site.root)
        except Exception:
            traceback.print_exc()
        self._build_ui()
        screenshot_mode = os.environ.get("INDANYA_SCREENSHOT_MODE") == "1"
        if not screenshot_mode:
            self._start_owner_collector()
            self._start_preview_server()
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(60_000)
        self.scheduler_timer.timeout.connect(self._scheduler_tick)
        if not screenshot_mode:
            self.scheduler_timer.start()
        self.sitemap_health_timer = QTimer(self)
        self.sitemap_health_timer.setInterval(30 * 60_000)
        self.sitemap_health_timer.timeout.connect(self._start_sitemap_health_check)
        if not screenshot_mode:
            self.sitemap_health_timer.start()
            self._ensure_startup_launcher()
        self.switch_page("dashboard")
        self.refresh_all()
        if not screenshot_mode:
            QTimer.singleShot(1_500, self._start_sitemap_health_check)
            QTimer.singleShot(4_000, self._scheduler_tick)

    @property
    def site(self) -> ManagedSite:
        return self.registry.active

    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.pages = {
            "dashboard": self._dashboard_page(),
            "analytics": self._analytics_page(),
            "create": self._create_page(),
            "social_x": self._x_posting_page(),
            "outreach": self._outreach_page(),
            "drafts": self._drafts_page(),
            "editor": self._editor_page(),
            "rights": self._rights_page(),
            "publishing": self._publishing_page(),
            "sources": self._sources_page(),
            "automation": self._automation_page(),
            "sites": self._sites_page(),
            "settings": self._settings_page(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        body_layout.addWidget(self.stack, 1)
        outer.addWidget(body, 1)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QFrame:
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(226)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 18, 14, 14)
        logo_row = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setFixedSize(42, 42)
        logo_row.addWidget(self.logo)
        names = QVBoxLayout()
        brand = QLabel("淫談屋", objectName="brandName")
        sub = QLabel("ARTICLE STUDIO", objectName="brandSub")
        names.addWidget(brand)
        names.addWidget(sub)
        logo_row.addLayout(names, 1)
        layout.addLayout(logo_row)
        layout.addSpacing(18)
        groups = [
            ("編集部", [("dashboard", "▦  ダッシュボード"), ("analytics", "▥  アクセス解析")]),
            ("制作", [("create", "＋  URLから作成"), ("editor", "T  記事編集")]),
            ("編集フロー", [("rights", "✓  許可管理"), ("publishing", "↑  公開管理")]),
            ("自動化", [("sources", "◎  情報源"), ("automation", "↻  自動巡回")]),
            ("集客", [("social_x", "X  X投稿管理"), ("outreach", "↗  掲載営業")]),
            ("サイト", [("sites", "◇  管理サイト"), ("settings", "⚙  設定")]),
        ]
        for group, items in groups:
            label = QLabel(group, objectName="sideLabel")
            layout.addWidget(label)
            for key, text in items:
                nav = QPushButton(text, objectName="navButton")
                nav.setCheckable(True)
                nav.setCursor(Qt.CursorShape.PointingHandCursor)
                nav.clicked.connect(lambda checked=False, name=key: self.switch_page(name))
                self.nav_buttons[key] = nav
                layout.addWidget(nav)
            layout.addSpacing(8)
        layout.addStretch()
        self.side_site = QLabel(objectName="sideFoot")
        self.side_site.setWordWrap(True)
        layout.addWidget(self.side_site)
        return side

    def _build_topbar(self) -> QFrame:
        bar = QFrame(objectName="topbar")
        bar.setFixedHeight(76)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 10, 28, 10)
        title_box = QVBoxLayout()
        self.eyebrow = QLabel("OVERVIEW", objectName="eyebrow")
        self.page_title = QLabel("ダッシュボード", objectName="pageTitle")
        title_box.addWidget(self.eyebrow)
        title_box.addWidget(self.page_title)
        layout.addLayout(title_box)
        layout.addStretch()
        self.site_combo = QComboBox()
        self.site_combo.setMinimumWidth(180)
        self.site_combo.currentIndexChanged.connect(self._site_combo_changed)
        layout.addWidget(self.site_combo)
        self.site_link = QPushButton(objectName="siteLink")
        self.site_link.clicked.connect(self.open_public_site)
        layout.addWidget(self.site_link)
        return bar

    def _page_shell(self, body: QWidget) -> QScrollArea:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(28, 25, 28, 30)
        layout.addWidget(body)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(wrap)
        return scroll

    def _dashboard_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("制作状況", "URLから作った記事、許可待ち、公開準備をまとめて確認できます。"))
        metrics = QHBoxLayout()
        self.metric_labels = {}
        for key, title in (("drafts", "記事"), ("rights", "許可待ち"), ("videos", "動画素材"), ("sites", "管理サイト")):
            inner = QVBoxLayout()
            label = QLabel("0", objectName="metric")
            self.metric_labels[key] = label
            inner.addWidget(label)
            inner.addWidget(QLabel(title, objectName="muted"))
            metrics.addWidget(panel(inner), 1)
        layout.addLayout(metrics)
        layout.addSpacing(14)
        sitemap_row = QHBoxLayout()
        sitemap_copy = QVBoxLayout()
        sitemap_copy.setContentsMargins(0, 0, 0, 0)
        sitemap_copy.addWidget(QLabel("Googleへの記事入口", objectName="sectionTitle"))
        self.sitemap_health_label = QLabel("未確認", objectName="muted")
        self.sitemap_health_label.setWordWrap(True)
        sitemap_copy.addWidget(self.sitemap_health_label)
        self.sitemap_health_detail = QLabel(
            "3本のサイトマップ、robots.txt、最新記事の公開状態を確認します。",
            objectName="muted",
        )
        self.sitemap_health_detail.setWordWrap(True)
        sitemap_copy.addWidget(self.sitemap_health_detail)
        sitemap_row.addLayout(sitemap_copy, 1)
        self.sitemap_health_button = button("今すぐ確認")
        self.sitemap_health_button.clicked.connect(self._start_sitemap_health_check)
        sitemap_row.addWidget(self.sitemap_health_button)
        open_sitemaps = button("Search Consoleを開く", "primary")
        open_sitemaps.clicked.connect(self._open_search_console_sitemaps)
        sitemap_row.addWidget(open_sitemaps)
        layout.addWidget(panel(sitemap_row))
        self._refresh_sitemap_health_panel()
        layout.addSpacing(14)
        self.affiliate_recommendation_panel = QFrame()
        self.affiliate_recommendation_panel.setObjectName("warningPanel")
        self.affiliate_recommendation_layout = QVBoxLayout(
            self.affiliate_recommendation_panel
        )
        layout.addWidget(self.affiliate_recommendation_panel)
        self.affiliate_recommendation_panel.hide()
        quick = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("次の記事を作る", objectName="sectionTitle"))
        left.addWidget(QLabel(
            "PCが素材を回収・検査し、内容判断と完成稿だけをCodexが1回で処理します。",
            objectName="muted",
        ))
        go = button("URLから記事を作る", "primary")
        go.clicked.connect(lambda: self.switch_page("create"))
        left.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        quick.addWidget(panel(left, True), 1)
        layout.addLayout(quick)
        layout.addSpacing(20)

        head = QHBoxLayout()
        head.addWidget(heading(
            "記事の確認と公開",
            "自動生成された記事をサイトと同じ見た目で確認し、公開・予約待機・消去を選びます。",
        ), 1)
        refresh = button("更新")
        refresh.clicked.connect(self._refresh_review_board)
        head.addWidget(refresh)
        layout.addLayout(head)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("表示"))
        self.review_filter = QComboBox()
        for label, value in (
            ("すべて", "all"),
            ("未判別", "unreviewed"),
            ("予約待機", "queued"),
            ("公開済み", "published"),
            ("消去済み", "deleted"),
            ("公開失敗", "failed"),
        ):
            self.review_filter.addItem(label, value)
        self.review_filter.currentIndexChanged.connect(self._refresh_review_board)
        filters.addWidget(self.review_filter)
        filters.addWidget(QLabel("並び順"))
        self.review_sort = QComboBox()
        self.review_sort.addItem("新しい順", "newest")
        self.review_sort.addItem("古い順", "oldest")
        self.review_sort.addItem("待機順", "queue")
        self.review_sort.currentIndexChanged.connect(self._refresh_review_board)
        filters.addWidget(self.review_sort)
        filters.addStretch()
        self.review_queue_label = QLabel("予約待機 0件", objectName="success")
        filters.addWidget(self.review_queue_label)
        layout.addLayout(filters)

        self.scheduler_note = QLabel("", objectName="muted")
        layout.addWidget(self.scheduler_note)

        self.review_view = QWebEngineView()
        self.review_view.setMinimumHeight(620)
        self.review_page = ReviewActionPage(self.review_view)
        self.review_page.action_requested.connect(self._review_action)
        self.review_view.setPage(self.review_page)
        self.review_view.loadFinished.connect(self._restore_review_scroll)
        self.review_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        layout.addWidget(self.review_view, 1)
        self.dashboard_scroll = self._page_shell(body)
        return self.dashboard_scroll

    def _refresh_sitemap_health_panel(self, report: dict | None = None) -> None:
        if not hasattr(self, "sitemap_health_label"):
            return
        report = dict(report or load_sitemap_health(self.site.root))
        status = str(report.get("status") or "unknown")
        labels = {
            "healthy": "正常: Googleへ渡す3本の入口が公開済み",
            "pending": "公開反映待ち: 記事は公開済み、入口を再確認中",
            "local_only": "ローカル検査済み: 公開先は未確認",
            "error": "要修正: サイトマップの生成検査に失敗",
            "unknown": "未確認",
        }
        self.sitemap_health_label.setText(labels.get(status, status))
        public = report.get("public") or {}
        local = report.get("local") or {}
        sitemap_rows = public.get("sitemaps") or local.get("sitemaps") or {}
        counts = [
            int((sitemap_rows.get(name) or {}).get("url_count") or 0)
            for name in ("sitemap.xml", "sitemap-images.xml", "sitemap-videos.xml")
        ]
        checked = str(report.get("checked_at") or "").replace("T", " ")[:19]
        errors = [str(value) for value in (public.get("errors") or local.get("errors") or [])]
        detail = (
            f"通常URL {counts[0]:,}件 / 画像ページ {counts[1]:,}件 / "
            f"動画ページ {counts[2]:,}件"
        )
        if checked:
            detail += f" / 最終確認 {checked}"
        if errors:
            detail += " / " + errors[0]
        elif status == "healthy":
            detail += " / Search Consoleへ再送信できます"
        self.sitemap_health_detail.setText(detail)

    def _start_sitemap_health_check(self) -> None:
        if self.sitemap_health_worker is not None:
            return
        self.sitemap_health_button.setEnabled(False)
        self.sitemap_health_label.setText("公開中のサイトマップを確認しています…")
        self.sitemap_health_worker = SitemapHealthWorker(
            self.site.root,
            self.site.public_url,
        )
        self.sitemap_health_worker.signals.completed.connect(
            self._sitemap_health_completed
        )
        self.sitemap_health_worker.signals.failed.connect(
            self._sitemap_health_failed
        )
        self.thread_pool.start(self.sitemap_health_worker)

    def _sitemap_health_completed(self, report: dict) -> None:
        self.sitemap_health_worker = None
        self.sitemap_health_button.setEnabled(True)
        self._refresh_sitemap_health_panel(report)

    def _sitemap_health_failed(self, message: str) -> None:
        self.sitemap_health_worker = None
        self.sitemap_health_button.setEnabled(True)
        self.sitemap_health_label.setText("公開先を確認できませんでした")
        self.sitemap_health_detail.setText(message)

    def _open_search_console_sitemaps(self) -> None:
        QDesktopServices.openUrl(QUrl(search_console_sitemaps_url(self.site.public_url)))

    def _analytics_page(self) -> QWidget:
        ensure_ga4_owner_identity(self.site.root, self.site.public_url)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "アクセス解析",
            "記事閲覧・訪問者・PR操作を実測します。外部アクセスと管理者を含む全アクセスは別ページです。",
        ))

        connection = QVBoxLayout()
        connection.setContentsMargins(18, 14, 18, 14)
        connection_header = QHBoxLayout()
        connection_header.addWidget(QLabel("計測接続", objectName="sectionTitle"))
        connection_header.addStretch(1)
        open_ga4 = button("Google Analyticsを開く")
        open_ga4.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ga4_url())))
        connection_header.addWidget(open_ga4)
        connection.addLayout(connection_header)

        send_row = QHBoxLayout()
        self.ga4_measurement_id_input = QLineEdit(load_ga4_measurement_id(self.site.root))
        self.ga4_measurement_id_input.setPlaceholderText("測定ID  G-XXXXXXXXXX")
        send_row.addWidget(self.ga4_measurement_id_input, 1)
        self.ga4_property_id_input = QLineEdit(load_ga4_property_id(self.site.root))
        self.ga4_property_id_input.setPlaceholderText("プロパティID（数字）")
        send_row.addWidget(self.ga4_property_id_input, 1)
        choose_credentials = button("読み取りJSON")
        choose_credentials.clicked.connect(self.choose_ga4_credentials)
        send_row.addWidget(choose_credentials)
        save_read = button("接続を保存")
        save_read.clicked.connect(self.save_ga4_read_settings)
        send_row.addWidget(save_read)
        save_public = button("計測コードを公開", "primary")
        save_public.clicked.connect(self.save_ga4_settings)
        send_row.addWidget(save_public)
        connection.addLayout(send_row)

        owner_row = QHBoxLayout()
        owner_row.addWidget(QLabel("自分のブラウザ", objectName="muted"))
        register_chrome = button("Chromeを登録")
        register_chrome.clicked.connect(lambda: self.register_owner_browser("chrome"))
        owner_row.addWidget(register_chrome)
        register_edge = button("Edgeを登録")
        register_edge.clicked.connect(lambda: self.register_owner_browser("edge"))
        owner_row.addWidget(register_edge)
        owner_row.addWidget(QLabel(
            "各ブラウザで一度だけ登録します。以後、そのブラウザの閲覧とPR操作を管理者側へ分離します。",
            objectName="muted",
        ), 1)
        connection.addLayout(owner_row)
        self.ga4_status = QLabel(objectName="muted")
        connection.addWidget(self.ga4_status)
        layout.addWidget(panel(connection))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("集計画面", objectName="sectionTitle"))
        controls.addStretch(1)
        controls.addWidget(QLabel("期間", objectName="muted"))
        self.ga4_period_days = QComboBox()
        for days in (7, 30, 90):
            self.ga4_period_days.addItem(f"過去{days}日", days)
        controls.addWidget(self.ga4_period_days)
        self.ga4_auto_refresh = QCheckBox("30秒ごとにリアルタイム更新")
        self.ga4_auto_refresh.setChecked(True)
        controls.addWidget(self.ga4_auto_refresh)
        self.ga4_realtime_button = button("直近30分を更新")
        self.ga4_realtime_button.clicked.connect(self.load_ga4_realtime_data)
        controls.addWidget(self.ga4_realtime_button)
        self.ga4_history_button = button("期間集計を更新", "primary")
        self.ga4_history_button.clicked.connect(self.load_ga4_data)
        controls.addWidget(self.ga4_history_button)
        layout.addLayout(controls)

        self.ga4_audience_views: dict[str, dict[str, object]] = {}
        self.ga4_audience_tabs = QTabWidget()
        for audience, title, description in (
            ("external", "外部アクセス", "登録済みの自分のブラウザを除外"),
            ("all", "自分を含む", "管理者の確認操作を含む全アクセス"),
        ):
            view = self._build_ga4_audience_page(title, description)
            self.ga4_audience_views[audience] = view
            self.ga4_audience_tabs.addTab(view["page"], title)
        layout.addWidget(self.ga4_audience_tabs)

        local = local_content_summary(self.site.root)
        local_layout = QHBoxLayout()
        self.ga4_local_metrics: dict[str, QLabel] = {}
        for key, label in (
            ("published", "公開記事"),
            ("published_7d", "7日間の新規記事"),
            ("images", "掲載画像"),
            ("videos", "掲載動画"),
        ):
            inner = QVBoxLayout()
            value = QLabel(f"{int(local.get(key, 0)):,}", objectName="metric")
            self.ga4_local_metrics[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(label, objectName="muted"))
            local_layout.addWidget(panel(inner), 1)
        layout.addWidget(QLabel("公開内容（アクセス数とは別集計）", objectName="sectionTitle"))
        layout.addLayout(local_layout)

        sales = QHBoxLayout()
        sales.addWidget(QLabel("FANZA確定成果", objectName="sectionTitle"))
        self.ga4_dmm_sales = QLabel("—  DMM側の確定レポートのみを成果として扱います", objectName="muted")
        sales.addWidget(self.ga4_dmm_sales, 1)
        open_dmm = button("DMMレポートを開く")
        open_dmm.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://affiliate.dmm.com/")))
        sales.addWidget(open_dmm)
        layout.addWidget(panel(sales))

        self.ga4_realtime_timer = QTimer(self)
        self.ga4_realtime_timer.setInterval(30_000)
        self.ga4_realtime_timer.timeout.connect(self._analytics_realtime_tick)
        self.ga4_realtime_timer.start()
        self._refresh_ga4_status()
        QTimer.singleShot(0, self._load_ga4_cached_data)
        return self._page_shell(body)

    def _build_ga4_audience_page(self, title: str, description: str) -> dict[str, object]:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        note = QLabel(
            f"{title}: {description}。訪問者はGA4が同じブラウザを重複除外した利用者数です。",
            objectName="muted",
        )
        note.setWordWrap(True)
        page_layout.addWidget(note)
        mode_tabs = QTabWidget()

        live_page = QWidget()
        live_layout = QVBoxLayout(live_page)
        live_status = QLabel("直近30分はまだ取得していません。", objectName="muted")
        live_layout.addWidget(live_status)
        live_metrics: dict[str, QLabel] = {}
        metric_grid = QGridLayout()
        for index, (key, label) in enumerate((
            ("activeUsers", "直近30分の訪問者"),
            ("pageViews", "直近30分の記事閲覧"),
            ("prImpressions", "直近30分のPR表示"),
            ("prClicks", "直近30分のPRクリック"),
        )):
            inner = QVBoxLayout()
            value = QLabel("—", objectName="metric")
            live_metrics[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(label, objectName="muted"))
            metric_grid.addWidget(panel(inner), 0, index)
        live_layout.addLayout(metric_grid)
        live_layout.addWidget(QLabel("いま見られている記事", objectName="sectionTitle"))
        live_tables = {
            "pages": self._table(["記事タイトル", "閲覧", "訪問者"]),
            "events": self._table(["種類", "回数"]),
            "minutes": self._table(["何分前", "記事閲覧", "PR表示", "PRクリック"]),
        }
        live_tables["pages"].setMinimumHeight(250)
        live_layout.addWidget(live_tables["pages"])
        live_details = QTabWidget()
        live_details.addTab(live_tables["events"], "イベント")
        live_details.addTab(live_tables["minutes"], "分ごとの動き")
        live_layout.addWidget(live_details)
        mode_tabs.addTab(live_page, "直近30分")

        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_status = QLabel("期間集計はまだ取得していません。", objectName="muted")
        report_layout.addWidget(report_status)
        report_metrics: dict[str, QLabel] = {}
        report_grid = QGridLayout()
        for index, (key, label) in enumerate((
            ("pageViews", "記事閲覧"),
            ("activeUsers", "訪問者（重複除外）"),
            ("prImpressions", "PR表示"),
            ("prClicks", "PRクリック"),
            ("clickRate", "閲覧→PRクリック"),
            ("prCtr", "PRクリック率"),
        )):
            inner = QVBoxLayout()
            value = QLabel("—", objectName="metric")
            report_metrics[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(label, objectName="muted"))
            report_grid.addWidget(panel(inner), index // 3, index % 3)
        report_layout.addLayout(report_grid)
        report_layout.addWidget(QLabel("記事別", objectName="sectionTitle"))
        report_tables = {
            "articles": self._table(["ページ", "タイトル", "閲覧", "訪問者", "PR表示", "PRクリック", "閲覧→PR"]),
            "events": self._table(["種類", "回数", "訪問者"]),
            "devices": self._table(["端末", "OS", "ブラウザ", "閲覧", "訪問者"]),
            "referrers": self._table(["流入元", "メディア", "セッション", "訪問者", "閲覧"]),
            "genres": self._table(["ジャンル", "閲覧", "訪問者"]),
            "daily": self._table(["日付", "閲覧", "訪問者"]),
        }
        report_tables["articles"].setMinimumHeight(300)
        report_layout.addWidget(report_tables["articles"])
        details = QTabWidget()
        details.addTab(report_tables["events"], "PR・イベント")
        details.addTab(report_tables["devices"], "端末")
        details.addTab(report_tables["referrers"], "流入元")
        details.addTab(report_tables["genres"], "ジャンル")
        details.addTab(report_tables["daily"], "日別")
        report_layout.addWidget(details)
        mode_tabs.addTab(report_page, "期間集計")
        page_layout.addWidget(mode_tabs)
        return {
            "page": page,
            "mode_tabs": mode_tabs,
            "live_status": live_status,
            "live_metrics": live_metrics,
            "live_tables": live_tables,
            "report_status": report_status,
            "report_metrics": report_metrics,
            "report_tables": report_tables,
        }

    def _legacy_analytics_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "アクセス解析",
            "Google Analytics 4の実測値を、直近30分と期間集計に分けて表示します。",
        ))
        guide = QVBoxLayout()
        guide.setContentsMargins(18, 16, 18, 16)
        guide.addWidget(QLabel("GA4接続設定", objectName="sectionTitle"))
        guide.addWidget(QLabel(
            "測定IDはサイトからの送信用、プロパティIDと読み取りJSONはアプリでの表示用です。",
            objectName="muted",
        ))
        row = QHBoxLayout()
        self.ga4_measurement_id_input = QLineEdit(load_ga4_measurement_id(self.site.root))
        self.ga4_measurement_id_input.setPlaceholderText("G-XXXXXXXXXX")
        row.addWidget(self.ga4_measurement_id_input, 1)
        save_ga4 = button("保存して公開", "primary")
        save_ga4.clicked.connect(self.save_ga4_settings)
        row.addWidget(save_ga4)
        open_ga4 = button("GA4を開く")
        open_ga4.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ga4_url())))
        row.addWidget(open_ga4)
        guide.addLayout(row)
        read_row = QHBoxLayout()
        self.ga4_property_id_input = QLineEdit(load_ga4_property_id(self.site.root))
        self.ga4_property_id_input.setPlaceholderText("GA4プロパティID（数字）")
        read_row.addWidget(self.ga4_property_id_input, 1)
        choose_credentials = button("読み取りJSONを選択")
        choose_credentials.clicked.connect(self.choose_ga4_credentials)
        read_row.addWidget(choose_credentials)
        save_property = button("読み取り設定を保存")
        save_property.clicked.connect(self.save_ga4_read_settings)
        read_row.addWidget(save_property)
        guide.addLayout(read_row)
        self.ga4_status = QLabel(objectName="muted")
        guide.addWidget(self.ga4_status)
        layout.addWidget(panel(guide))

        live_controls = QHBoxLayout()
        live_controls.addWidget(QLabel("リアルタイム", objectName="sectionTitle"))
        live_controls.addStretch(1)
        self.ga4_auto_refresh = QCheckBox("30秒ごとに自動更新")
        self.ga4_auto_refresh.setChecked(True)
        live_controls.addWidget(self.ga4_auto_refresh)
        self.ga4_realtime_button = button("今すぐ更新", "primary")
        self.ga4_realtime_button.clicked.connect(self.load_ga4_realtime_data)
        live_controls.addWidget(self.ga4_realtime_button)
        layout.addLayout(live_controls)
        self.ga4_live_status = QLabel(
            "直近30分のデータはまだ読み込んでいません。",
            objectName="muted",
        )
        layout.addWidget(self.ga4_live_status)

        self.ga4_data_tabs = QTabWidget()
        live_page = QWidget()
        live_layout = QVBoxLayout(live_page)
        self.ga4_realtime_metrics = {}
        live_metrics = QGridLayout()
        for index, (key, label) in enumerate((
            ("activeUsers", "現在の利用者"),
            ("pageViews", "直近30分の閲覧"),
            ("prImpressions", "直近30分のPR表示"),
            ("prClicks", "直近30分のPRクリック"),
        )):
            value = QLabel("—", objectName="metric")
            self.ga4_realtime_metrics[key] = value
            box = QVBoxLayout()
            box.addWidget(value)
            box.addWidget(QLabel(label, objectName="muted"))
            live_metrics.addWidget(panel(box), 0, index)
        live_layout.addLayout(live_metrics)
        live_layout.addWidget(QLabel("いま見られているページ", objectName="sectionTitle"))
        self.ga4_realtime_tables = {
            "pages": self._table(["ページタイトル", "閲覧", "利用者"]),
            "events": self._table(["イベント", "回数"]),
            "minutes": self._table(["何分前", "閲覧", "イベント"]),
        }
        self.ga4_realtime_tables["pages"].setMinimumHeight(260)
        live_layout.addWidget(self.ga4_realtime_tables["pages"])
        live_detail_tabs = QTabWidget()
        live_detail_tabs.addTab(self.ga4_realtime_tables["events"], "イベント")
        live_detail_tabs.addTab(self.ga4_realtime_tables["minutes"], "分ごとの動き")
        live_layout.addWidget(live_detail_tabs)
        self.ga4_data_tabs.addTab(live_page, "直近30分")

        history_page = QWidget()
        report = QVBoxLayout(history_page)
        history_header = QHBoxLayout()
        history_header.addWidget(QLabel("過去7日間の記事データ", objectName="sectionTitle"))
        history_header.addStretch(1)
        self.ga4_history_button = button("期間集計を更新")
        self.ga4_history_button.clicked.connect(self.load_ga4_data)
        history_header.addWidget(self.ga4_history_button)
        report.addLayout(history_header)
        self.ga4_report_status = QLabel(
            "期間集計はまだ読み込んでいません。",
            objectName="muted",
        )
        report.addWidget(self.ga4_report_status)
        self.ga4_report_tables = {}
        self.ga4_report_metrics = {}
        metrics = QGridLayout()
        for index, (key, label) in enumerate((
            ("pageViews", "記事閲覧"), ("activeUsers", "利用者（重複除外）"),
            ("prImpressions", "PR表示"), ("prClicks", "PRクリック"),
        )):
            value = QLabel("—", objectName="metric")
            self.ga4_report_metrics[key] = value
            box = QVBoxLayout(); box.addWidget(value); box.addWidget(QLabel(label, objectName="muted"))
            metrics.addWidget(panel(box), 0, index)
        report.addLayout(metrics)
        report.addWidget(QLabel("記事別閲覧", objectName="sectionTitle"))
        self.ga4_report_tables["articles"] = self._table(["ページ", "タイトル", "閲覧", "訪問者"])
        report.addWidget(self.ga4_report_tables["articles"])
        report.addWidget(QLabel("PR・端末・流入元・日別", objectName="sectionTitle"))
        self.ga4_report_tables["events"] = self._table(["イベント", "回数", "ユーザー"])
        self.ga4_report_tables["devices"] = self._table(["端末", "OS", "ブラウザ", "閲覧", "訪問者"])
        self.ga4_report_tables["referrers"] = self._table(["流入元", "メディア", "セッション", "訪問者"])
        self.ga4_report_tables["daily"] = self._table(["日付", "閲覧", "訪問者", "イベント"])
        history_detail_tabs = QTabWidget()
        history_detail_tabs.addTab(self.ga4_report_tables["events"], "イベント")
        history_detail_tabs.addTab(self.ga4_report_tables["devices"], "端末")
        history_detail_tabs.addTab(self.ga4_report_tables["referrers"], "流入元")
        history_detail_tabs.addTab(self.ga4_report_tables["daily"], "日別")
        report.addWidget(history_detail_tabs)
        self.ga4_data_tabs.addTab(history_page, "期間集計")
        layout.addWidget(self.ga4_data_tabs)

        self.ga4_realtime_timer = QTimer(self)
        self.ga4_realtime_timer.setInterval(30_000)
        self.ga4_realtime_timer.timeout.connect(self._analytics_realtime_tick)
        self.ga4_realtime_timer.start()
        self._refresh_ga4_status()
        layout.addStretch(1)
        return self._page_shell(body)

    def _build_analytics_view_page(self, title: str, description: str) -> dict[str, object]:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(f"{title}　{description}", objectName="muted")
        layout.addWidget(note)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        metric_labels: dict[str, QLabel] = {}
        for index, (key, label) in enumerate((
            ("page_views", "記事閲覧"),
            ("unique_sessions", "訪問者数"),
            ("pr_impressions", "PR表示"),
            ("pr_clicks", "PRクリック"),
            ("click_rate", "閲覧→PR"),
            ("pr_ctr", "PRクリック率"),
        )):
            inner = QVBoxLayout()
            value = QLabel("—", objectName="metric")
            metric_labels[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(label, objectName="muted"))
            metrics.addWidget(panel(inner), index // 3, index % 3)
            metrics.setColumnStretch(index % 3, 1)
        layout.addLayout(metrics)

        local_metrics = QHBoxLayout()
        local_labels: dict[str, QLabel] = {}
        for key, label in (
            ("published", "公開記事"),
            ("published_7d", "7日間の新規記事"),
            ("images", "掲載画像"),
            ("videos", "掲載動画"),
        ):
            inner = QVBoxLayout()
            value = QLabel("0", objectName="metric")
            local_labels[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(label, objectName="muted"))
            local_metrics.addWidget(panel(inner), 1)
        layout.addLayout(local_metrics)

        tabs = QTabWidget()
        articles = self._table([
            "記事", "カテゴリー", "閲覧", "PR表示", "PRクリック", "閲覧→PR",
        ])
        promotions = self._table([
            "PR作品・リンク", "移動先", "表示", "クリック", "CTR",
        ])
        categories = self._table([
            "カテゴリー", "閲覧", "PR表示", "PRクリック", "閲覧→PR",
        ])
        daily = self._table(["日付", "閲覧", "PR表示", "PRクリック"])
        visitors = self._table([
            "訪問者ID", "識別", "常連度", "今日", "期間内閲覧", "利用日数", "PRクリック", "主な端末", "主ブラウザ", "ブラウザ数", "初回", "最終",
        ])
        visitor_daily = self._table([
            "日付", "訪問者ID", "閲覧", "PR表示", "PRクリック",
        ])
        devices = self._table([
            "端末", "閲覧", "PR表示", "PRクリック", "閲覧→PR",
        ])
        referrers = self._table([
            "流入元", "閲覧", "PR表示", "PRクリック", "閲覧→PR",
        ])
        tabs.addTab(articles, "記事別")
        tabs.addTab(promotions, "PR別")
        tabs.addTab(visitors, "訪問者別")
        tabs.addTab(visitor_daily, "訪問者履歴")
        tabs.addTab(devices, "端末別")
        tabs.addTab(referrers, "流入元別")
        tabs.addTab(categories, "ジャンル別")
        tabs.addTab(daily, "日別")
        layout.addWidget(tabs)
        return {
            "page": page,
            "note": note,
            "metrics": metric_labels,
            "local": local_labels,
            "articles": articles,
            "promotions": promotions,
            "categories": categories,
            "daily": daily,
            "visitors": visitors,
            "visitor_daily": visitor_daily,
            "devices": devices,
            "referrers": referrers,
        }

    def _x_posting_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        head = QHBoxLayout()
        head.addWidget(heading(
            "X投稿管理",
            "通常投稿・返信・漫画スレッドを条件別に候補化し、X公式画面で確認して送信します。",
        ), 1)
        self.x_account_label = QLabel("@indanya_sns", objectName="muted")
        head.addWidget(self.x_account_label)
        open_account = button("アカウントを開く")
        open_account.clicked.connect(self.open_x_account)
        head.addWidget(open_account)
        settings_button = button("投稿設定")
        settings_button.clicked.connect(self.open_x_posting_settings)
        head.addWidget(settings_button)
        layout.addLayout(head)

        login_row = QHBoxLayout()
        self.x_login_state = QLabel("● ログイン状態を確認中", objectName="muted")
        login_row.addWidget(self.x_login_state)
        login_row.addStretch()
        self.x_management_login_button = button("Xへログイン")
        self.x_management_login_button.clicked.connect(self.open_x_login)
        login_row.addWidget(self.x_management_login_button)
        layout.addLayout(login_row)

        auto_status = QVBoxLayout()
        auto_status.setContentsMargins(18, 14, 18, 14)
        self.x_auto_state_label = QLabel("自動候補: 状態を確認中", objectName="sectionTitle")
        self.x_auto_schedule_label = QLabel("", objectName="muted")
        self.x_auto_schedule_label.setWordWrap(True)
        self.x_manga_schedule_label = QLabel("漫画スレッド: 状態を確認中", objectName="muted")
        self.x_manga_schedule_label.setWordWrap(True)
        self.x_reply_schedule_label = QLabel("外部リプ: 状態を確認中", objectName="muted")
        self.x_reply_schedule_label.setWordWrap(True)
        auto_status.addWidget(self.x_auto_state_label)
        auto_status.addWidget(self.x_auto_schedule_label)
        auto_status.addWidget(self.x_reply_schedule_label)
        auto_status.addWidget(self.x_manga_schedule_label)
        layout.addWidget(panel(auto_status))

        trend_row = QHBoxLayout()
        trend_copy = QVBoxLayout()
        trend_copy.setContentsMargins(0, 0, 0, 0)
        self.x_trend_state_label = QLabel("Codexテンプレ: 状態を確認中", objectName="sectionTitle")
        self.x_trend_schedule_label = QLabel("", objectName="muted")
        self.x_trend_schedule_label.setWordWrap(True)
        self.x_learning_label = QLabel("反応学習: 記録待ち", objectName="muted")
        self.x_learning_label.setWordWrap(True)
        trend_copy.addWidget(self.x_trend_state_label)
        trend_copy.addWidget(self.x_trend_schedule_label)
        trend_copy.addWidget(self.x_learning_label)
        trend_row.addLayout(trend_copy, 1)
        self.x_trend_scan_button = button("今すぐ流行を調査")
        self.x_trend_scan_button.clicked.connect(lambda: self.start_x_trend_scan(force=True))
        trend_row.addWidget(self.x_trend_scan_button)
        layout.addWidget(panel(trend_row))

        follow_box = QVBoxLayout()
        follow_box.setContentsMargins(14, 10, 14, 10)
        follow_box.addWidget(QLabel("今日のフォロー候補", objectName="sectionTitle"))
        self.x_follow_table = QTableWidget(0, 4)
        self.x_follow_table.setHorizontalHeaderLabels([
            "アカウント", "反応", "選定理由", "確認",
        ])
        self.x_follow_table.verticalHeader().setVisible(False)
        self.x_follow_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.x_follow_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.x_follow_table.setFixedHeight(142)
        self.x_follow_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.x_follow_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.x_follow_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.x_follow_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        follow_box.addWidget(self.x_follow_table)
        layout.addWidget(panel(follow_box))

        article_actions = QHBoxLayout()
        prepare = button("今日の候補を選ぶ")
        prepare.clicked.connect(self.prepare_x_posts)
        article_actions.addWidget(prepare)
        self.x_manga_thread_button = button("漫画5枚スレッドを作る")
        self.x_manga_thread_button.clicked.connect(self.prepare_manga_x_thread)
        article_actions.addWidget(self.x_manga_thread_button)
        self.x_copy_button = button("投稿文を作る")
        self.x_copy_button.clicked.connect(self.generate_x_post_copies)
        self.x_copy_button.hide()
        self.x_schedule_button = button("選択した候補をX公式画面で確認", "primary")
        self.x_schedule_button.clicked.connect(self.schedule_selected_x_posts)
        article_actions.addWidget(self.x_schedule_button)
        article_actions.addStretch()
        layout.addLayout(article_actions)

        reply_actions = QHBoxLayout()
        self.x_discovered_reply_button = button("選手権リプ候補を作る")
        self.x_discovered_reply_button.clicked.connect(
            self.prepare_discovered_x_contest_reply
        )
        reply_actions.addWidget(self.x_discovered_reply_button)
        self.x_contest_button = button("自分の選手権を作る")
        self.x_contest_button.clicked.connect(self.prepare_owned_x_contest)
        reply_actions.addWidget(self.x_contest_button)
        self.x_viral_reply_button = button("バズ投稿へ会話返信")
        self.x_viral_reply_button.clicked.connect(self.prepare_viral_x_reply)
        reply_actions.addWidget(self.x_viral_reply_button)
        reply_actions.addStretch()
        layout.addLayout(reply_actions)

        self.x_posts_table = QTableWidget(0, 7)
        self.x_posts_table.setHorizontalHeaderLabels([
            "選択", "送信方法", "投稿予定", "記事", "おすすめ度", "投稿文", "状態",
        ])
        self.x_posts_table.verticalHeader().setVisible(False)
        self.x_posts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.x_posts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.x_posts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.x_posts_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.x_posts_table.itemSelectionChanged.connect(self._load_x_post_editor)
        layout.addWidget(self.x_posts_table, 1)

        editor = QVBoxLayout()
        editor.setContentsMargins(16, 14, 16, 14)
        editor.addWidget(QLabel("選択中の投稿文", objectName="sectionTitle"))
        delivery_row = QHBoxLayout()
        delivery_row.addWidget(QLabel("送信方法"))
        self.x_delivery_mode = QComboBox()
        self.x_delivery_mode.addItem("通常投稿", "post")
        self.x_delivery_mode.addItem("外部投稿へ返信", "reply")
        self.x_delivery_mode.addItem("自分主催の選手権", "campaign")
        self.x_delivery_mode.addItem("漫画5枚＋作品PRスレッド", "thread")
        self.x_delivery_mode.currentIndexChanged.connect(
            self._x_delivery_mode_changed
        )
        delivery_row.addWidget(self.x_delivery_mode)
        delivery_row.addStretch()
        editor.addLayout(delivery_row)

        self.x_reply_fields = QWidget()
        reply_layout = QGridLayout(self.x_reply_fields)
        reply_layout.setContentsMargins(0, 4, 0, 8)
        reply_layout.addWidget(QLabel("返信先URL"), 0, 0)
        self.x_reply_target_url = QLineEdit()
        self.x_reply_target_url.setPlaceholderText(
            "https://x.com/ユーザー/status/数字"
        )
        reply_layout.addWidget(self.x_reply_target_url, 0, 1, 1, 3)
        self.x_reply_topic_label = QLabel("選手権のお題")
        reply_layout.addWidget(self.x_reply_topic_label, 1, 0)
        self.x_reply_target_topic = QLineEdit()
        self.x_reply_target_topic.setPlaceholderText("例：水着動画選手権")
        reply_layout.addWidget(self.x_reply_target_topic, 1, 1, 1, 3)
        self.x_reply_opt_in = QCheckBox("画像・動画の返信募集を確認済み")
        reply_layout.addWidget(self.x_reply_opt_in, 2, 1, 1, 3)
        reply_layout.addWidget(QLabel("添付"), 3, 0)
        self.x_reply_media_mode = QComboBox()
        self.x_reply_media_mode.addItem("安全カード", "safe_card")
        self.x_reply_media_mode.addItem("記事の元素材", "original")
        self.x_reply_media_mode.addItem("添付なし", "none")
        reply_layout.addWidget(self.x_reply_media_mode, 3, 1)
        self.x_reply_include_link = QCheckBox("記事リンクを入れる")
        reply_layout.addWidget(self.x_reply_include_link, 3, 2, 1, 2)
        self.x_reply_score_label = QLabel("返信適合度: 未採点", objectName="sectionTitle")
        reply_layout.addWidget(self.x_reply_score_label, 4, 1, 1, 3)
        self.x_reply_score_reason = QLabel("返信先を保存すると自動採点します。", objectName="muted")
        self.x_reply_score_reason.setWordWrap(True)
        reply_layout.addWidget(self.x_reply_score_reason, 5, 1, 1, 3)
        save_reply = button("返信先を保存")
        save_reply.clicked.connect(self.save_x_reply_settings)
        reply_layout.addWidget(save_reply, 6, 1)
        remake_reply = button("返信文を作り直す")
        remake_reply.clicked.connect(self.generate_current_x_reply_copy)
        reply_layout.addWidget(remake_reply, 6, 2)
        open_media = button("素材フォルダを開く")
        open_media.clicked.connect(self.open_x_reply_media)
        reply_layout.addWidget(open_media, 6, 3)
        block_reply = button("この相手を対象外", "danger")
        block_reply.clicked.connect(self.block_current_x_reply_target)
        reply_layout.addWidget(block_reply, 7, 1)
        self.x_reply_open_button = button("X公式返信画面を開く", "primary")
        self.x_reply_open_button.clicked.connect(self.open_x_reply_intent)
        reply_layout.addWidget(self.x_reply_open_button, 7, 2, 1, 2)
        editor.addWidget(self.x_reply_fields)

        self.x_campaign_fields = QWidget()
        campaign_layout = QGridLayout(self.x_campaign_fields)
        campaign_layout.setContentsMargins(0, 4, 0, 8)
        campaign_layout.addWidget(QLabel("選手権名"), 0, 0)
        self.x_campaign_topic = QLineEdit()
        self.x_campaign_topic.setPlaceholderText("例：今週の水着画像選手権")
        campaign_layout.addWidget(self.x_campaign_topic, 0, 1, 1, 3)
        campaign_note = QLabel(
            "参加方法を投稿文に明記し、この投稿へ反応した人だけを返信候補として扱います。",
            objectName="muted",
        )
        campaign_note.setWordWrap(True)
        campaign_layout.addWidget(campaign_note, 1, 1, 1, 3)
        save_campaign = button("選手権名を保存")
        save_campaign.clicked.connect(self.save_x_delivery_settings)
        campaign_layout.addWidget(save_campaign, 2, 1)
        remake_campaign = button("募集文を作り直す")
        remake_campaign.clicked.connect(self.generate_current_x_reply_copy)
        campaign_layout.addWidget(remake_campaign, 2, 2)
        editor.addWidget(self.x_campaign_fields)

        self.x_thread_fields = QWidget()
        thread_layout = QHBoxLayout(self.x_thread_fields)
        thread_layout.setContentsMargins(0, 4, 0, 8)
        self.x_thread_progress_label = QLabel(
            "漫画スレッド: 1/6",
            objectName="sectionTitle",
        )
        thread_layout.addWidget(self.x_thread_progress_label)
        self.x_thread_note = QLabel(
            "5枚を自分への返信でつなぎ、最後の返信に作品PRを入れます。",
            objectName="muted",
        )
        self.x_thread_note.setWordWrap(True)
        thread_layout.addWidget(self.x_thread_note, 1)
        thread_media = button("現在の画像を開く")
        thread_media.clicked.connect(self.open_x_reply_media)
        thread_layout.addWidget(thread_media)
        editor.addWidget(self.x_thread_fields)

        self.x_template_label = QLabel("使用テンプレ: 未割当", objectName="muted")
        editor.addWidget(self.x_template_label)
        self.x_post_editor = QPlainTextEdit()
        self.x_post_editor.setPlaceholderText("自動作成後、必要な部分だけここで直せます")
        self.x_post_editor.setMaximumHeight(120)
        editor.addWidget(self.x_post_editor)
        edit_actions = QHBoxLayout()
        self.x_post_length = QLabel("0 / 280", objectName="muted")
        self.x_post_editor.textChanged.connect(self._update_x_post_length)
        edit_actions.addWidget(self.x_post_length)
        edit_actions.addStretch()
        save_copy = button("文章を保存", "primary")
        save_copy.clicked.connect(self.save_x_post_copy)
        edit_actions.addWidget(save_copy)
        performance = button("24時間の反応を記録")
        performance.clicked.connect(self.record_current_x_performance)
        edit_actions.addWidget(performance)
        skip = button("候補から外す", "danger")
        skip.clicked.connect(self.skip_selected_x_post)
        edit_actions.addWidget(skip)
        editor.addLayout(edit_actions)
        layout.addWidget(panel(editor))

        self.x_post_progress = QProgressBar()
        self.x_post_progress.setRange(0, 100)
        layout.addWidget(self.x_post_progress)
        self.x_post_status = QLabel("候補を選ぶと、ここに処理状況が表示されます。", objectName="muted")
        self.x_post_status.setWordWrap(True)
        layout.addWidget(self.x_post_status)
        return self._page_shell(body)

    def _create_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "URLから5ch風記事を作る",
            "一般Web、X、FANZAのURLから素材を回収し、必要な判断と文章だけをCodexが1回で作ります。",
        ))
        form = QVBoxLayout()
        form.setContentsMargins(20, 18, 20, 18)
        form.addWidget(QLabel("ページURL"))
        row = QHBoxLayout()
        self.source_url = QLineEdit()
        self.source_url.setPlaceholderText("https://example.com/article")
        self.source_url.setMinimumHeight(46)
        row.addWidget(self.source_url, 1)
        self.chatgpt_queue_button = button("Codex待機へ追加", "primary")
        self.chatgpt_queue_button.clicked.connect(self.queue_article_for_chatgpt)
        row.addWidget(self.chatgpt_queue_button)
        self.chatgpt_batch_button = button("複数URLを追加")
        self.chatgpt_batch_button.setToolTip("一般Web、X、FANZAのURLを1行に1件、まとめて追加します")
        self.chatgpt_batch_button.clicked.connect(self.queue_fanza_batch)
        row.addWidget(self.chatgpt_batch_button)
        self.generate_button = button("Codexで今すぐ作る")
        self.generate_button.setToolTip("このURLを素材回収から保存までCodex方式で処理します")
        self.generate_button.clicked.connect(self.generate_article)
        row.addWidget(self.generate_button)
        form.addLayout(row)
        options = QHBoxLayout()
        options.addWidget(QLabel("記事タイプ"))
        self.content_mode_combo = QComboBox()
        self.content_mode_combo.addItem("URLから自動判定", "auto")
        self.content_mode_combo.addItem("FANZA作品を5ch風に紹介", "fanza_product")
        options.addWidget(self.content_mode_combo)
        options.addWidget(QLabel("掲載区分"))
        self.promotion_combo = QComboBox()
        self.promotion_combo.addItem("通常記事", "organic")
        self.promotion_combo.addItem("アフィリエイト紹介", "affiliate")
        options.addWidget(self.promotion_combo)
        options.addWidget(QLabel("カテゴリー"))
        self.category_combo = QComboBox()
        self.category_combo.addItem("自動判定", "auto")
        for value in ("SNS", "画像", "動画", "話題"):
            self.category_combo.addItem(value, value)
        options.addWidget(self.category_combo)
        options.addWidget(QLabel("レス数"))
        self.reply_combo = QComboBox()
        self.reply_combo.addItem("自動", "auto")
        for value in ("5", "8", "10"):
            self.reply_combo.addItem(f"{value}本", value)
        options.addWidget(self.reply_combo)
        options.addStretch()
        form.addLayout(options)
        form.addWidget(QLabel("紹介ポイント（任意）"))
        self.editorial_brief_input = QLineEdit()
        self.editorial_brief_input.setPlaceholderText("例：作品名とパッケージから確認できる制服ジャンルを中心にする")
        form.addWidget(self.editorial_brief_input)
        form.addWidget(QLabel("FANZA誘導URL（通常は空欄）"))
        self.fanza_url_input = QLineEdit()
        self.fanza_url_input.setPlaceholderText(
            "別の正式な商品リンクを指定する場合だけ入力します"
        )
        form.addWidget(self.fanza_url_input)
        form.addWidget(QLabel("編集メモ（非公開）"))
        self.private_client_note_input = QLineEdit()
        self.private_client_note_input.setPlaceholderText("料金、連絡先、掲載条件など。記事本文やCodexには送りません")
        form.addWidget(self.private_client_note_input)
        layout.addWidget(panel(form, True))
        status = QVBoxLayout()
        status.setContentsMargins(20, 16, 20, 16)
        status_head = QHBoxLayout()
        self.generate_status = QLabel("URLを入力してください", objectName="muted")
        self.generate_percent = QLabel("0%", objectName="success")
        status_head.addWidget(self.generate_status)
        status_head.addStretch()
        status_head.addWidget(self.generate_percent)
        status.addLayout(status_head)
        self.generate_progress = QProgressBar()
        self.generate_progress.setRange(0, 100)
        status.addWidget(self.generate_progress)
        self.generate_result = QLabel("")
        self.generate_result.setWordWrap(True)
        status.addWidget(self.generate_result)
        queue_row = QHBoxLayout()
        self.chatgpt_queue_label = QLabel("記事処理 0件", objectName="muted")
        queue_row.addWidget(self.chatgpt_queue_label)
        queue_row.addStretch()
        self.chatgpt_login_button = button("Codexアプリ認証を使用")
        self.chatgpt_login_button.setToolTip("Codexデスクトップアプリに保存済みの認証をそのまま使います")
        self.chatgpt_login_button.setEnabled(False)
        queue_row.addWidget(self.chatgpt_login_button)
        self.open_chatgpt_button = button("現在の記事を処理", "primary")
        self.open_chatgpt_button.setToolTip(
            "現在の1記事を素材判定から公開前ボード保存まで自動処理します"
        )
        self.open_chatgpt_button.clicked.connect(self.start_chatgpt_auto_processing)
        queue_row.addWidget(self.open_chatgpt_button)
        status.addLayout(queue_row)
        layout.addWidget(panel(status))
        return self._page_shell(body)

    def open_x_login(self) -> None:
        if self.x_login_worker is not None:
            return
        for widget_name in ("x_login_button", "x_management_login_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(False)
                widget.setText("ログイン待機中")
        self.x_login_worker = XLoginWorker()
        self.x_login_worker.signals.progress.connect(self._x_login_progress)
        self.x_login_worker.signals.completed.connect(self._x_login_completed)
        self.x_login_worker.signals.failed.connect(self._x_login_failed)
        self.thread_pool.start(self.x_login_worker)

    def _x_login_progress(self, value: int, message: str) -> None:
        if hasattr(self, "generate_status"):
            self.generate_status.setText(message)
        if hasattr(self, "x_post_status"):
            self.x_post_progress.setValue(value)
            self.x_post_status.setText(message)

    def _reset_x_login_buttons(self) -> None:
        for widget_name in ("x_login_button", "x_management_login_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(True)
                widget.setText(
                    "Xログイン（必要時のみ）"
                    if widget_name == "x_login_button"
                    else "Xへログイン"
                )

    def _x_login_completed(self, _result: dict) -> None:
        self.x_login_worker = None
        self._reset_x_login_buttons()
        self.generate_status.setText("Xログイン情報を保存しました。プロフィール記事を作成できます")
        if hasattr(self, "x_post_status"):
            self.x_post_progress.setValue(100)
            self.x_post_status.setText("Xログインを確認しました。投稿できます。")
            self._update_x_login_state()
            self._refresh_x_trend_status()
            QTimer.singleShot(500, self._scheduler_tick)

    def _x_login_failed(self, message: str) -> None:
        self.x_login_worker = None
        self._reset_x_login_buttons()
        self.generate_status.setText(f"Xログインに失敗しました: {message}")
        if hasattr(self, "x_post_status"):
            self.x_post_status.setText(f"Xログインに失敗しました: {message}")
            self._update_x_login_state()

    def _update_x_login_state(self) -> bool:
        ready = x_login_ready()
        if hasattr(self, "x_login_state"):
            self.x_login_state.setText(
                "● ログイン済み（投稿できます）"
                if ready
                else "● 未ログイン"
            )
            self.x_login_state.setObjectName("success" if ready else "danger")
            self.x_login_state.style().unpolish(self.x_login_state)
            self.x_login_state.style().polish(self.x_login_state)
        return ready

    def open_x_account(self) -> None:
        QDesktopServices.openUrl(QUrl(load_x_settings(self.site.root)["account_url"]))

    def open_x_posting_settings(self) -> None:
        dialog = XPostingSettingsDialog(self, load_x_settings(self.site.root))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save_x_settings(self.site.root, dialog.values())
        except ValueError as exc:
            QMessageBox.warning(self, "X投稿設定を確認", str(exc))
            return
        self._refresh_x_posts()
        self.x_post_status.setText("X投稿設定を保存しました。")
        QTimer.singleShot(100, self._scheduler_tick)

    @staticmethod
    def _x_trend_time(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=JST)
            return parsed.astimezone(JST).strftime("%m/%d %H:%M")
        except ValueError:
            return "未実行"

    def _refresh_x_auto_status(self) -> None:
        if not hasattr(self, "x_auto_state_label"):
            return
        state = x_daily_posting_status(self.site.root)
        settings = load_x_settings(self.site.root)
        status = str(state.get("status") or "idle")
        pause_until = str(state.get("pause_until") or "")
        pause_at: datetime | None = None
        try:
            pause_at = datetime.fromisoformat(pause_until) if pause_until else None
            if pause_at is not None and pause_at.tzinfo is None:
                pause_at = pause_at.replace(tzinfo=JST)
        except ValueError:
            pause_at = None
        now = datetime.now(JST)
        if self.x_daily_worker is not None or status == "running":
            headline = "自動候補: 作成中"
        elif pause_at is not None and now < pause_at.astimezone(JST):
            headline = f"自動候補: {pause_at.astimezone(JST).strftime('%m/%d %H:%M')}まで休止"
        elif not state.get("enabled"):
            headline = "自動候補: 停止中"
        elif status == "ready_for_manual":
            headline = "自動候補: X公式画面で送信待ち"
        elif state.get("due"):
            headline = "自動候補: まもなく作成"
        else:
            slots = list(state.get("next_slots") or [])
            next_time = self._x_trend_time(str(slots[0])) if slots else "候補待ち"
            headline = f"自動候補: 次回 {next_time}"
        delivery = (
            "X公式画面で確認して送信"
            if settings.get("manual_delivery_only", True)
            else "自動送信"
        )
        detail = (
            f"候補 {int(state.get('candidate_count') or 0)}件 / "
            f"上限 {int(state.get('daily_post_limit') or 1)}件/日 / 送信方式: {delivery}"
        )
        error = str(state.get("last_error") or "").strip()
        if error:
            detail += f" / 理由: {error[:180]}"
        self.x_auto_state_label.setText(headline)
        self.x_auto_schedule_label.setText(detail)
        self.x_auto_schedule_label.setToolTip(error)
        if hasattr(self, "x_manga_schedule_label"):
            manga = x_manga_schedule_status(self.site.root)
            if not manga.get("enabled"):
                manga_detail = "漫画スレッド: 停止中"
            elif manga.get("blocked_by_pending"):
                manga_detail = (
                    f"漫画スレッド: 送信待ち {int(manga.get('pending_count') or 0)}件"
                    "（完了後に次を選びます）"
                )
            elif manga.get("due"):
                manga_detail = "漫画スレッド: 人気・セール作品から候補を選択中"
            else:
                next_manga = self._x_trend_time(str(manga.get("next_at") or ""))
                manga_detail = f"漫画スレッド: 次回 {next_manga}"
            manga_detail += (
                f" / {int(manga.get('interval_days') or 3)}日ごと"
                f" / 同一作品{int(manga.get('product_cooldown_days') or 90)}日除外"
            )
            manga_error = str(manga.get("last_error") or "").strip()
            if manga_error:
                manga_detail += f" / {manga_error[:140]}"
            self.x_manga_schedule_label.setText(manga_detail)
            self.x_manga_schedule_label.setToolTip(manga_error)
        if hasattr(self, "x_reply_schedule_label"):
            reply = x_reply_schedule_status(self.site.root)
            if not reply.get("enabled"):
                reply_detail = "外部リプ: 自動候補作成は停止中"
            elif reply.get("pending_count"):
                reply_detail = (
                    "外部リプ: X公式画面で送信待ち "
                    f"{int(reply.get('pending_count') or 0)}件"
                )
            elif reply.get("waiting_for_trend"):
                reply_detail = "外部リプ: 今日の流行調査を待っています"
            elif reply.get("due"):
                reply_detail = "外部リプ: 条件に合う候補を選んでいます"
            else:
                next_reply = self._x_trend_time(str(reply.get("next_at") or ""))
                reply_detail = f"外部リプ: 次回候補確認 {next_reply}"
            reply_detail += (
                f" / 選手権{int(reply.get('contest_candidate_count') or 0)}件"
                f" / バズ会話{int(reply.get('viral_candidate_count') or 0)}件"
                f" / 今日{int(reply.get('completed_today') or 0)}"
                f"/{int(reply.get('daily_limit') or 1)}件"
            )
            reply_error = str(reply.get("last_error") or "").strip()
            if reply_error:
                reply_detail += f" / {reply_error[:140]}"
            self.x_reply_schedule_label.setText(reply_detail)
            self.x_reply_schedule_label.setToolTip(reply_error)

    def _refresh_x_trend_status(self) -> None:
        if not hasattr(self, "x_trend_state_label"):
            return
        state = x_trend_scan_status(self.site.root)
        templates = len(state.get("templates") or [])
        samples = int(state.get("sample_count") or 0)
        status = str(state.get("status") or "never")
        if self.x_trend_worker is not None or status == "running":
            headline = "Codexテンプレ: 更新中"
        elif templates:
            headline = f"Codexテンプレ: {templates}本"
        else:
            headline = "Codexテンプレ: 未作成"
        self.x_trend_state_label.setText(headline)
        last_scan = self._x_trend_time(str(state.get("last_scan_at") or ""))
        next_scan = self._x_trend_time(str(state.get("next_scan_at") or ""))
        minimum = int(state.get("minimum_likes") or load_x_settings(self.site.root)["trend_min_likes"])
        detail = (
            f"最終調査 {last_scan} / 次回 {next_scan} / "
            f"いいね{minimum:,}件以上を{samples}件採用 / テンプレ担当 Codex / 本文担当 ChatGPT"
        )
        contests = len(state.get("reply_candidates") or [])
        viral = len(state.get("viral_reply_candidates") or [])
        detail += f" / 返信募集 {contests}件 / バズ会話候補 {viral}件"
        error = str(state.get("last_error") or "").strip()
        if error:
            detail += f" / 前回失敗: {error[:160]}"
        elif not state.get("enabled"):
            detail += " / 自動調査は停止中"
        self.x_trend_schedule_label.setText(detail)
        self.x_trend_schedule_label.setToolTip(error)
        learning = x_template_performance(self.site.root)
        samples = sum(int(item.get("samples") or 0) for item in learning.values())
        if samples:
            best = max(
                learning.values(),
                key=lambda item: float(item.get("average_score") or 0),
            )
            self.x_learning_label.setText(
                f"反応学習: {samples}件を記録 / 現在の上位「{best.get('name')}」"
                f" 平均{float(best.get('average_score') or 0):.1f}点"
            )
        else:
            self.x_learning_label.setText("反応学習: 投稿後の数字を記録すると自動で優先度へ反映")
        self.x_trend_scan_button.setEnabled(
            self.x_trend_worker is None and self._update_x_login_state()
        )
        self._refresh_x_follow_candidates()

    def _refresh_x_follow_candidates(self) -> None:
        if not hasattr(self, "x_follow_table"):
            return
        candidates = x_follow_candidates(self.site.root, limit=3)
        self.x_follow_table.setRowCount(len(candidates))
        for row_index, candidate in enumerate(candidates):
            reactions = (
                f"表示 {int(candidate.get('views') or 0):,} / "
                f"いいね {int(candidate.get('likes') or 0):,}"
            )
            for column, value in enumerate((
                f"@{candidate.get('handle', '')}",
                reactions,
                str(candidate.get("reason") or ""),
            )):
                self.x_follow_table.setItem(
                    row_index,
                    column,
                    QTableWidgetItem(str(value)),
                )
            open_button = button("開く")
            profile_url = str(candidate.get("profile_url") or "")
            open_button.clicked.connect(
                lambda _checked=False, url=profile_url: QDesktopServices.openUrl(QUrl(url))
            )
            self.x_follow_table.setCellWidget(row_index, 3, open_button)
            self.x_follow_table.setRowHeight(row_index, 34)

    def start_x_trend_scan(self, force: bool = True) -> None:
        if self.x_trend_worker is not None:
            self.x_post_status.setText("Xの流行調査はすでに動いています。")
            return
        if (
            self.chatgpt_send_worker is not None
            or self.active_worker is not None
            or self.x_copy_worker is not None
            or self.x_daily_worker is not None
        ):
            self.x_post_status.setText("現在の記事・投稿文処理が終わってから流行調査を開始します。")
            return
        if not self._update_x_login_state():
            self.x_post_status.setText("先に「Xへログイン」を押してください。")
            return
        self.x_trend_worker = XTrendWorker(self.site.root, force=force)
        self.x_trend_worker.signals.progress.connect(self._x_trend_progress_changed)
        self.x_trend_worker.signals.completed.connect(self._x_trend_completed)
        self.x_trend_worker.signals.failed.connect(self._x_trend_failed)
        self.x_trend_scan_button.setEnabled(False)
        self.x_post_progress.setValue(0)
        self.x_post_status.setText("Xの流行調査を開始します。")
        self._refresh_x_trend_status()
        self.thread_pool.start(self.x_trend_worker)

    def _x_trend_progress_changed(self, value: int, message: str) -> None:
        self.x_post_progress.setValue(value)
        self.x_post_status.setText(message)

    def _x_trend_completed(self, state: dict) -> None:
        self.x_trend_worker = None
        self._refresh_x_trend_status()
        self.x_post_progress.setValue(100)
        self.x_post_status.setText(
            f"Xの流行調査が完了しました。Codexテンプレ "
            f"{len(state.get('templates') or [])}本 / バズ会話候補 "
            f"{len(state.get('viral_reply_candidates') or [])}件です。"
        )
        QTimer.singleShot(500, self._scheduler_tick)

    def _x_trend_failed(self, message: str) -> None:
        self.x_trend_worker = None
        state = load_x_trend_state(self.site.root)
        self._refresh_x_trend_status()
        if state.get("templates"):
            self.x_post_status.setText(
                f"今日の流行調査は失敗しました。前回のCodexテンプレを維持します: {message}"
            )
        else:
            self.x_post_status.setText(f"Xの流行調査に失敗しました: {message}")
        QTimer.singleShot(500, self._scheduler_tick)

    def start_x_daily_cycle(self) -> None:
        if self.x_daily_worker is not None:
            return
        state = x_daily_posting_status(self.site.root)
        if not state.get("due"):
            self._refresh_x_auto_status()
            return
        if not self._update_x_login_state():
            self.x_post_status.setText("Xへログインすると自動候補作成を再開します。")
            return
        self.x_daily_worker = XDailyWorker(self.site.root, self.site.public_url)
        self.x_daily_worker.signals.progress.connect(self._x_daily_progress_changed)
        self.x_daily_worker.signals.completed.connect(self._x_daily_completed)
        self.x_daily_worker.signals.failed.connect(self._x_daily_failed)
        self.x_post_progress.setValue(0)
        self.x_post_status.setText("今日のX投稿候補を作成しています。")
        self._refresh_x_auto_status()
        self.thread_pool.start(self.x_daily_worker)

    def _x_daily_progress_changed(self, value: int, message: str) -> None:
        self.x_post_progress.setValue(value)
        self.x_post_status.setText(message)

    def _x_daily_completed(self, result: dict) -> None:
        self.x_daily_worker = None
        self._refresh_x_posts()
        ready = len(result.get("ready_for_manual") or [])
        posted = len(result.get("posted") or [])
        scheduled = len(result.get("scheduled") or [])
        failed = len(result.get("failed") or [])
        self.x_post_progress.setValue(100)
        if ready:
            message = f"{ready}件の投稿文を用意しました。X公式画面で確認して送信できます。"
        elif posted or scheduled:
            message = f"X投稿済み {posted}件 / 予約済み {scheduled}件。"
        elif failed:
            message = f"X候補処理で{failed}件失敗しました。状態欄を確認してください。"
        else:
            message = "今回は投稿時刻・上限・休止条件により候補を作成しませんでした。"
        self.x_post_status.setText(message)
        QTimer.singleShot(500, self._scheduler_tick)

    def _x_daily_failed(self, message: str) -> None:
        self.x_daily_worker = None
        self._refresh_x_posts()
        self.x_post_status.setText(f"Xの自動候補を作成できませんでした: {message}")
        QTimer.singleShot(500, self._scheduler_tick)

    @staticmethod
    def _x_status_label(status: str) -> str:
        return {
            "copy_pending": "文章待ち",
            "copy_ready": "投稿可能",
            "posting": "投稿中",
            "posted": "投稿済み",
            "scheduling": "予約中",
            "scheduled": "予約済み",
            "failed": "要確認",
            "skipped": "除外",
        }.get(status, status)

    def _refresh_x_posts(self) -> None:
        settings = load_x_settings(self.site.root)
        self.x_account_label.setText(f"@{settings['account_handle']}")
        self._update_x_login_state()
        self._refresh_x_auto_status()
        self._refresh_x_trend_status()
        selected_id = ""
        current = self.x_posts_table.currentRow()
        if current >= 0 and self.x_posts_table.item(current, 0):
            selected_id = str(
                self.x_posts_table.item(current, 0).data(Qt.ItemDataRole.UserRole) or ""
            )
        rows = list(reversed(list_x_posts(self.site.root)))
        self.x_posts_table.setRowCount(len(rows))
        selected_row = -1
        for row_index, row in enumerate(rows):
            chooser = QTableWidgetItem("")
            chooser.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            chooser.setCheckState(
                Qt.CheckState.Checked
                if row.get("status") in {"copy_pending", "copy_ready", "failed"}
                else Qt.CheckState.Unchecked
            )
            chooser.setData(Qt.ItemDataRole.UserRole, str(row.get("post_id") or ""))
            self.x_posts_table.setItem(row_index, 0, chooser)
            schedule_text = str(row.get("scheduled_for") or "").replace("T", " ")[:16]
            if not schedule_text:
                if row.get("status") == "posted":
                    schedule_text = "送信済み"
                elif row.get("delivery_mode") == "thread":
                    step_count = len(row.get("thread_steps") or [])
                    step_index = int(row.get("thread_step_index") or 0)
                    schedule_text = f"{min(step_count, step_index + 1)}/{step_count}を送信"
                elif row.get("delivery_mode") == "reply":
                    schedule_text = "確認して送信"
                else:
                    schedule_text = "次の空き枠"
            delivery_text = {
                "reply": (
                    "バズ会話返信"
                    if row.get("reply_kind") == "viral_conversation"
                    else "選手権返信"
                ),
                "campaign": "自分主催",
                "thread": "漫画スレッド",
            }.get(str(row.get("delivery_mode") or "post"), "通常投稿")
            if row.get("delivery_mode") == "reply":
                score_text = (
                    f"{float(row.get('reply_candidate_score') or 0):.0f} "
                    f"{row.get('reply_candidate_level') or '未採点'}"
                )
            else:
                score_text = f"{float(row.get('score') or 0):.1f}"
            status_text = self._x_status_label(str(row.get("status") or ""))
            if row.get("performance"):
                status_text += " / 学習済み"
            elif row.get("status") in {"posted", "scheduled"}:
                status_text += " / 24h記録待ち"
            if row.get("delivery_mode") == "thread":
                status_text += (
                    f" / {len(row.get('thread_post_urls') or [])}"
                    f"/{len(row.get('thread_steps') or [])}通"
                )
            values = (
                delivery_text,
                schedule_text,
                str(row.get("article_title") or ""),
                score_text,
                str(row.get("post_text") or "未作成").replace("\n", " "),
                status_text,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 1 and row.get("delivery_mode") == "reply":
                    item.setToolTip(str(row.get("reply_target_url") or ""))
                if column == 5:
                    item.setToolTip(str(row.get("post_text") or ""))
                if column == 4 and row.get("delivery_mode") == "reply":
                    item.setToolTip(" / ".join([
                        *[str(value) for value in row.get("reply_candidate_reasons") or []],
                        *[str(value) for value in row.get("reply_candidate_blockers") or []],
                    ]))
                if column == 6:
                    item.setToolTip(
                        str(row.get("last_error") or "")
                        or str((row.get("performance") or {}).get("measurement") or "")
                    )
                self.x_posts_table.setItem(row_index, column, item)
            if str(row.get("post_id") or "") == selected_id:
                selected_row = row_index
        if selected_row >= 0:
            self.x_posts_table.selectRow(selected_row)
        elif rows:
            self.x_posts_table.selectRow(0)
        self.x_copy_button.setEnabled(self.x_copy_worker is None)
        self.x_schedule_button.setEnabled(self.x_schedule_worker is None)

    def _checked_x_post_ids(self) -> list[str]:
        result: list[str] = []
        for row in range(self.x_posts_table.rowCount()):
            item = self.x_posts_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                post_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if post_id:
                    result.append(post_id)
        return result

    def _current_x_post_id(self) -> str:
        row = self.x_posts_table.currentRow()
        if row < 0:
            return ""
        item = self.x_posts_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _load_x_post_editor(self) -> None:
        post_id = self._current_x_post_id()
        row = next(
            (item for item in list_x_posts(self.site.root) if item.get("post_id") == post_id),
            {},
        )
        self.x_post_editor.blockSignals(True)
        self.x_post_editor.setPlainText(str(row.get("post_text") or ""))
        self.x_post_editor.blockSignals(False)
        self.x_delivery_mode.blockSignals(True)
        mode_index = self.x_delivery_mode.findData(
            str(row.get("delivery_mode") or "post")
        )
        self.x_delivery_mode.setCurrentIndex(max(0, mode_index))
        self.x_delivery_mode.blockSignals(False)
        is_thread = str(row.get("delivery_mode") or "post") == "thread"
        self.x_delivery_mode.setEnabled(not is_thread)
        self.x_reply_target_url.setText(str(row.get("reply_target_url") or ""))
        self.x_reply_target_topic.setText(str(row.get("reply_target_topic") or ""))
        self.x_reply_opt_in.setChecked(bool(row.get("reply_opt_in_confirmed", False)))
        media_index = self.x_reply_media_mode.findData(
            str(row.get("reply_media_mode") or "safe_card")
        )
        self.x_reply_media_mode.setCurrentIndex(max(0, media_index))
        self.x_reply_include_link.setChecked(bool(row.get("reply_include_link", False)))
        viral_reply = str(row.get("reply_kind") or "contest") == "viral_conversation"
        self.x_reply_topic_label.setText(
            "返信先の投稿本文" if viral_reply else "選手権のお題"
        )
        self.x_reply_target_topic.setPlaceholderText(
            "返信先から取得した投稿本文"
            if viral_reply
            else "例：水着動画選手権"
        )
        self.x_reply_opt_in.setVisible(not viral_reply)
        self.x_reply_media_mode.setEnabled(not viral_reply)
        self.x_reply_include_link.setEnabled(not viral_reply)
        self.x_campaign_topic.setText(str(row.get("campaign_topic") or ""))
        score = float(row.get("reply_candidate_score") or 0)
        level = str(row.get("reply_candidate_level") or "未採点")
        self.x_reply_score_label.setText(f"返信適合度: {score:.0f}点 / {level}")
        score_details = [
            *[str(value) for value in row.get("reply_candidate_reasons") or []],
            *[str(value) for value in row.get("reply_candidate_blockers") or []],
        ]
        self.x_reply_score_reason.setText(
            " / ".join(score_details) if score_details else "返信先を保存すると自動採点します。"
        )
        thread_steps = row.get("thread_steps") or []
        thread_index = int(row.get("thread_step_index") or 0)
        thread_sent = len(row.get("thread_post_urls") or [])
        if is_thread:
            if row.get("status") == "posted" or thread_index >= len(thread_steps):
                self.x_thread_progress_label.setText(
                    f"漫画スレッド: 完了 ({thread_sent}/{len(thread_steps)})"
                )
                self.x_thread_note.setText(
                    "漫画5枚と最後の作品PRまでXへの送信記録が完了しています。"
                )
            else:
                step = thread_steps[thread_index] if thread_steps else {}
                self.x_thread_progress_label.setText(
                    f"漫画スレッド: {thread_index + 1}/{len(thread_steps)}"
                )
                self.x_thread_note.setText(
                    "現在は作品PRを送ります。"
                    if step.get("kind") == "pr"
                    else "現在の漫画画像を送り、投稿URLを記録して次の返信へ進みます。"
                )
        self._x_delivery_mode_changed()
        template_name = str(row.get("trend_template_name") or "")
        writer = str(row.get("copy_writer") or "ChatGPT")
        self.x_template_label.setText(
            f"使用テンプレ: {template_name}（Codex） / 本文: {writer}"
            if template_name
            else f"使用テンプレ: 旧方式（未使用） / 本文: {writer}"
        )
        self._update_x_post_length()

    def _x_delivery_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.x_delivery_mode.currentData() or "post")
        is_reply = mode == "reply"
        self.x_reply_fields.setVisible(is_reply)
        self.x_campaign_fields.setVisible(mode == "campaign")
        self.x_thread_fields.setVisible(mode == "thread")

    def _save_x_reply_fields(self, *, for_send: bool = False) -> bool:
        post_id = self._current_x_post_id()
        if not post_id:
            return False
        mode = str(self.x_delivery_mode.currentData() or "post")
        rows = list_x_posts(self.site.root)
        current = next(
            (row for row in rows if str(row.get("post_id") or "") == post_id),
            {},
        )
        target_url = self.x_reply_target_url.text().strip()
        topic = self.x_reply_target_topic.text().strip()
        campaign_topic = self.x_campaign_topic.text().strip()
        viral_reply = str(current.get("reply_kind") or "contest") == "viral_conversation"
        if mode == "reply":
            try:
                target_url = canonical_x_status_url(target_url)
            except ValueError as exc:
                QMessageBox.warning(self, "返信先を確認", str(exc))
                return False
            if len(topic) < 4:
                QMessageBox.warning(
                    self,
                    "返信内容を確認",
                    "返信先の投稿本文を4文字以上で入力してください。",
                )
                return False
        if mode == "campaign" and len(campaign_topic) < 4:
            QMessageBox.warning(
                self,
                "選手権名を確認",
                "自分で開催する選手権名を4文字以上で入力してください。",
            )
            return False
        target_changed = str(current.get("reply_target_url") or "") != target_url
        include_link = self.x_reply_include_link.isChecked()
        if mode == "reply" and viral_reply:
            include_link = False
            self.x_reply_include_link.setChecked(False)
            media_mode = "none"
        else:
            media_mode = str(self.x_reply_media_mode.currentData() or "safe_card")
        if mode == "reply" and target_changed and not viral_reply:
            try:
                include_link = choose_x_reply_link(self.site.root, current, target_url)
                self.x_reply_include_link.setChecked(include_link)
            except ValueError:
                include_link = False
        changed = any((
            str(current.get("delivery_mode") or "post") != mode,
            target_changed,
            str(current.get("reply_target_topic") or "") != topic,
            str(current.get("reply_media_mode") or "safe_card")
            != media_mode,
            bool(current.get("reply_include_link", False)) != include_link,
            str(current.get("campaign_topic") or "") != campaign_topic,
        ))
        changes = {
            "delivery_mode": mode,
            "reply_target_url": target_url,
            "reply_target_topic": topic,
            "reply_opt_in_confirmed": (
                False if viral_reply else self.x_reply_opt_in.isChecked()
            ),
            "reply_media_mode": media_mode,
            "reply_include_link": include_link,
            "reply_link_decided": bool(mode == "reply" and target_url),
            "campaign_topic": campaign_topic,
            "last_error": "",
        }
        if changed and not for_send:
            changes["status"] = "copy_pending"
        if for_send:
            text = self.x_post_editor.toPlainText().strip()
            if not text or len(text) > 280:
                QMessageBox.warning(
                    self,
                    "返信文を確認",
                    "返信文は1～280文字にしてください。",
                )
                return False
            changes.update({"post_text": text, "status": "copy_ready"})
        update_x_post(self.site.root, post_id, **changes)
        if mode == "reply":
            evaluation = refresh_x_reply_candidate_score(self.site.root, post_id)
            self.x_reply_score_label.setText(
                f"返信適合度: {float(evaluation['score']):.0f}点 / {evaluation['level']}"
            )
            self.x_reply_score_reason.setText(" / ".join([
                *[str(value) for value in evaluation["reasons"]],
                *[str(value) for value in evaluation["blockers"]],
            ]))
        return True

    def save_x_reply_settings(self) -> None:
        post_id = self._current_x_post_id()
        if not self._save_x_reply_fields():
            return
        self._refresh_x_posts()
        if self.x_delivery_mode.currentData() == "reply":
            for row_index in range(self.x_posts_table.rowCount()):
                item = self.x_posts_table.item(row_index, 0)
                if item:
                    current_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
                    item.setCheckState(
                        Qt.CheckState.Checked
                        if current_id == post_id
                        else Qt.CheckState.Unchecked
                    )
        self.x_post_status.setText("返信先と内容を保存しました。")

    def save_x_delivery_settings(self) -> None:
        if not self._save_x_reply_fields():
            return
        self._refresh_x_posts()
        mode = str(self.x_delivery_mode.currentData() or "post")
        self.x_post_status.setText(
            "選手権名を保存しました。"
            if mode == "campaign"
            else "送信方法を保存しました。"
        )

    def generate_current_x_reply_copy(self) -> None:
        if self.x_copy_worker is not None:
            return
        post_id = self._current_x_post_id()
        if not post_id or not self._save_x_reply_fields():
            return
        self.x_copy_worker = XCopyWorker(self.site.root, [post_id])
        self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
        self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
        self.x_copy_button.setEnabled(False)
        self.thread_pool.start(self.x_copy_worker)

    def open_x_reply_media(self) -> None:
        post_id = self._current_x_post_id()
        paths = x_post_media_paths(self.site.root, post_id) if post_id else []
        if not paths:
            QMessageBox.information(
                self,
                "素材なし",
                "この候補にはXへ添付できる画像・動画がありません。",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))

    def block_current_x_reply_target(self) -> None:
        target_url = self.x_reply_target_url.text().strip()
        if not target_url:
            return
        answer = QMessageBox.question(
            self,
            "返信対象外へ追加",
            "この相手には今後返信しない設定にしますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            handle = block_x_reply_handle(self.site.root, target_url)
        except ValueError as exc:
            QMessageBox.warning(self, "返信先を確認", str(exc))
            return
        post_id = self._current_x_post_id()
        if post_id:
            refresh_x_reply_candidate_score(self.site.root, post_id)
        self._refresh_x_posts()
        self.x_post_status.setText(f"@{handle} を返信対象外へ追加しました。")

    def open_x_reply_intent(self) -> None:
        post_id = self._current_x_post_id()
        if post_id:
            self._open_x_reply_post(post_id)

    def _open_x_reply_post(self, post_id: str) -> None:
        if post_id != self._current_x_post_id():
            for row_index in range(self.x_posts_table.rowCount()):
                item = self.x_posts_table.item(row_index, 0)
                if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == post_id:
                    self.x_posts_table.selectRow(row_index)
                    break
        if not self._save_x_reply_fields(for_send=True):
            return
        try:
            intent_url = x_reply_intent_url(self.site.root, post_id)
        except ValueError as exc:
            QMessageBox.warning(self, "返信できません", str(exc))
            return
        QApplication.clipboard().setText(self.x_post_editor.toPlainText().strip())
        paths = x_post_media_paths(self.site.root, post_id)
        if paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))
        opened_at = datetime.now(JST).isoformat(timespec="seconds")
        update_x_post(self.site.root, post_id, reply_opened_at=opened_at)
        if not QDesktopServices.openUrl(QUrl(intent_url)):
            QMessageBox.warning(self, "Xを開けません", "X公式返信画面を開けませんでした。")
            return
        answer = QMessageBox.question(
            self,
            "返信結果",
            "Xで素材を添付して、返信を送信できましたか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            completed_at = datetime.now(JST).isoformat(timespec="seconds")
            update_x_post(
                self.site.root,
                post_id,
                status="posted",
                reply_completed_at=completed_at,
                scheduled_at=completed_at,
                last_error="",
            )
            self.x_post_status.setText("Xへの返信を送信済みにしました。")
        else:
            self.x_post_status.setText("未送信のまま残しました。後で再開できます。")
        self._refresh_x_posts()

    def _open_x_normal_post(self, post_id: str) -> None:
        if post_id != self._current_x_post_id():
            for row_index in range(self.x_posts_table.rowCount()):
                item = self.x_posts_table.item(row_index, 0)
                if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == post_id:
                    self.x_posts_table.selectRow(row_index)
                    break
        text = self.x_post_editor.toPlainText().strip()
        if not text or len(text) > 280:
            QMessageBox.warning(self, "投稿文を確認", "投稿文を1～280文字で用意してください。")
            return
        update_x_post(
            self.site.root,
            post_id,
            post_text=text,
            status="copy_ready",
            copy_writer="手動編集",
            manual_edited_at=datetime.now(JST).isoformat(timespec="seconds"),
            last_error="",
        )
        try:
            intent_url = x_post_intent_url(self.site.root, post_id)
        except ValueError as exc:
            QMessageBox.warning(self, "投稿できません", str(exc))
            return
        QApplication.clipboard().setText(text)
        paths = x_post_media_paths(self.site.root, post_id)
        if paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))
        if not QDesktopServices.openUrl(QUrl(intent_url)):
            QMessageBox.warning(self, "Xを開けません", "X公式投稿画面を開けませんでした。")
            return
        answer = QMessageBox.question(
            self,
            "投稿結果",
            "X公式画面で素材を添付し、投稿できましたか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            completed_at = datetime.now(JST).isoformat(timespec="seconds")
            update_x_post(
                self.site.root,
                post_id,
                status="posted",
                posted_at=completed_at,
                scheduled_at=completed_at,
                last_error="",
            )
            self.x_post_status.setText("Xへの投稿を送信済みにしました。")
        else:
            self.x_post_status.setText("未送信のまま残しました。後で再開できます。")
        self._refresh_x_posts()

    def _update_x_post_length(self) -> None:
        length = len(self.x_post_editor.toPlainText())
        self.x_post_length.setText(f"{length} / 280")
        self.x_post_length.setObjectName("success" if 0 < length <= 280 else "muted")
        self.x_post_length.style().unpolish(self.x_post_length)
        self.x_post_length.style().polish(self.x_post_length)

    def prepare_x_posts(self) -> None:
        added = prepare_x_candidates(self.site.root, self.site.public_url)
        self._refresh_x_posts()
        if not added:
            self.x_post_status.setText("未投稿の公開記事がありません。")
            return
        self.x_post_progress.setValue(20)
        self.x_post_status.setText(
            f"{len(added)}件を選びました。投稿文の作成を開始します。"
        )
        QTimer.singleShot(100, self.generate_x_post_copies)

    def _select_only_x_post(self, post_id: str) -> None:
        for row_index in range(self.x_posts_table.rowCount()):
            item = self.x_posts_table.item(row_index, 0)
            if not item:
                continue
            current_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            item.setCheckState(
                Qt.CheckState.Checked
                if current_id == post_id
                else Qt.CheckState.Unchecked
            )
            if current_id == post_id:
                self.x_posts_table.selectRow(row_index)

    def prepare_discovered_x_contest_reply(self) -> None:
        if self.x_copy_worker is not None:
            return
        post = prepare_discovered_x_reply(self.site.root, self.site.public_url)
        if not post:
            self.x_post_status.setText(
                "未使用の選手権候補がありません。先に「今すぐ流行を調査」を実行してください。"
            )
            return
        post_id = str(post.get("post_id") or "")
        self._refresh_x_posts()
        self._select_only_x_post(post_id)
        self.x_post_status.setText(
            "画像・動画の返信募集と記事素材が一致した候補です。返信文を作成しています。"
        )
        self.x_copy_worker = XCopyWorker(self.site.root, [post_id])
        self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
        self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
        self.x_copy_button.setEnabled(False)
        self.thread_pool.start(self.x_copy_worker)

    def prepare_manga_x_thread(self) -> None:
        try:
            post = prepare_x_manga_thread(self.site.root, self.site.public_url)
        except RuntimeError as exc:
            QMessageBox.warning(self, "漫画スレッドを作れません", str(exc))
            return
        if not post:
            self.x_post_status.setText(
                "未使用の漫画記事がありません。FANZA公式商品ページ・公開済み・試し読み5枚以上が必要です。"
            )
            return
        post_id = str(post.get("post_id") or "")
        self._refresh_x_posts()
        self._select_only_x_post(post_id)
        self.x_post_status.setText(
            "公式試し読み5ページと同じ作品の販売URLを、6通の自分宛てスレッドに用意しました。"
            f" 選定: {post.get('selection_reason', '')}"
        )

    def prepare_owned_x_contest(self) -> None:
        if self.x_copy_worker is not None:
            return
        post = prepare_x_contest_candidate(self.site.root, self.site.public_url)
        if not post:
            settings = load_x_settings(self.site.root)
            self.x_post_status.setText(
                "選手権に使える別の記事がありません。"
                f"同じ記事は{settings['owned_contest_cooldown_days']}日空けます。"
            )
            return
        post_id = str(post.get("post_id") or "")
        self._refresh_x_posts()
        for row_index in range(self.x_posts_table.rowCount()):
            item = self.x_posts_table.item(row_index, 0)
            if not item:
                continue
            current_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            item.setCheckState(
                Qt.CheckState.Checked
                if current_id == post_id
                else Qt.CheckState.Unchecked
            )
            if current_id == post_id:
                self.x_posts_table.selectRow(row_index)
        self.x_post_status.setText(
            f"「{post.get('campaign_topic')}」の募集文を作成しています。"
        )
        self.x_copy_worker = XCopyWorker(self.site.root, [post_id])
        self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
        self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
        self.x_copy_button.setEnabled(False)
        self.thread_pool.start(self.x_copy_worker)

    def prepare_viral_x_reply(self) -> None:
        if self.x_copy_worker is not None:
            return
        post = prepare_x_viral_reply(self.site.root)
        if not post:
            self.x_post_status.setText(
                "未使用のバズ会話候補がありません。先に「今すぐ流行を調査」を実行してください。"
            )
            return
        post_id = str(post.get("post_id") or "")
        self._refresh_x_posts()
        for row_index in range(self.x_posts_table.rowCount()):
            item = self.x_posts_table.item(row_index, 0)
            if not item:
                continue
            current_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            item.setCheckState(
                Qt.CheckState.Checked
                if current_id == post_id
                else Qt.CheckState.Unchecked
            )
            if current_id == post_id:
                self.x_posts_table.selectRow(row_index)
        self.x_post_status.setText(
            "高インプレ投稿へ送る、リンクなし・画像なしの会話返信を作成しています。"
        )
        self.x_copy_worker = XCopyWorker(self.site.root, [post_id])
        self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
        self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
        self.x_copy_button.setEnabled(False)
        self.thread_pool.start(self.x_copy_worker)

    def record_current_x_performance(self) -> None:
        post_id = self._current_x_post_id()
        row = next(
            (
                item for item in list_x_posts(self.site.root)
                if str(item.get("post_id") or "") == post_id
            ),
            None,
        )
        if row is None:
            return
        if row.get("status") not in {"posted", "scheduled"}:
            QMessageBox.information(
                self,
                "投稿後に記録",
                "この候補はまだXへ投稿されていません。",
            )
            return
        dialog = XPerformanceDialog(self, row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            saved = record_x_post_performance(
                self.site.root,
                post_id,
                dialog.values(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "反応を記録できません", str(exc))
            return
        performance = saved.get("performance") or {}
        self._refresh_x_posts()
        self.x_post_status.setText(
            f"反応を記録しました。テンプレ評価 {float(performance.get('score') or 0):.1f}点"
        )

    def generate_x_post_copies(self) -> None:
        if self.x_copy_worker is not None:
            return
        post_ids = self._checked_x_post_ids()
        if not post_ids:
            self.x_post_status.setText("投稿文を作る候補にチェックを入れてください。")
            return
        self.x_copy_worker = XCopyWorker(self.site.root, post_ids)
        self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
        self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
        self.x_copy_button.setEnabled(False)
        self.thread_pool.start(self.x_copy_worker)

    def _x_post_progress_changed(self, value: int, message: str) -> None:
        self.x_post_progress.setValue(value)
        self.x_post_status.setText(message)

    def _x_copy_completed(self, result: dict) -> None:
        self.x_copy_worker = None
        self._refresh_x_posts()
        count = len(result.get("posts", []))
        self.x_post_progress.setValue(100)
        self.x_post_status.setText(
            f"{count}件の投稿文を作成しました。内容を確認してXへ投稿できます。"
        )
        pending_schedule = list(self.x_schedule_after_copy_ids)
        self.x_schedule_after_copy_ids = []
        if pending_schedule:
            QTimer.singleShot(
                100,
                lambda post_ids=pending_schedule: self._start_x_schedule(post_ids),
            )

    def _x_copy_failed(self, message: str) -> None:
        self.x_copy_worker = None
        self.x_schedule_after_copy_ids = []
        self._refresh_x_posts()
        self.x_post_status.setText(f"投稿文を作成できませんでした: {message}")

    def save_x_post_copy(self) -> None:
        post_id = self._current_x_post_id()
        text = self.x_post_editor.toPlainText().strip()
        if not post_id:
            return
        if not text or len(text) > 280:
            QMessageBox.warning(self, "投稿文を確認", "投稿文は1～280文字にしてください。")
            return
        update_x_post(
            self.site.root,
            post_id,
            post_text=text,
            status="copy_ready",
            copy_writer="手動編集",
            manual_edited_at=datetime.now(JST).isoformat(timespec="seconds"),
            last_error="",
        )
        self._refresh_x_posts()
        self.x_post_status.setText("投稿文を保存しました。")

    def skip_selected_x_post(self) -> None:
        post_id = self._current_x_post_id()
        if not post_id:
            return
        update_x_post(self.site.root, post_id, status="skipped", last_error="")
        self._refresh_x_posts()
        self.x_post_status.setText("候補から外しました。")

    def schedule_selected_x_posts(self) -> None:
        if self.x_schedule_worker is not None:
            return
        post_ids = self._checked_x_post_ids()
        if not post_ids:
            self.x_post_status.setText("投稿する記事にチェックを入れてください。")
            return
        current_id = self._current_x_post_id()
        if (
            current_id in post_ids
            and self.x_delivery_mode.currentData() in {"reply", "campaign"}
        ):
            if not self._save_x_reply_fields():
                return
            post_ids = [current_id]
        rows = {
            str(row.get("post_id") or ""): row
            for row in list_x_posts(self.site.root)
        }
        thread_ids = [
            post_id for post_id in post_ids
            if rows.get(post_id, {}).get("delivery_mode") == "thread"
        ]
        if thread_ids and (len(post_ids) != 1 or len(thread_ids) != 1):
            self.x_post_status.setText(
                "漫画スレッドは1件だけ選び、1通ずつX公式画面で送信してください。"
            )
            return
        reply_ids = [
            post_id for post_id in post_ids
            if rows.get(post_id, {}).get("delivery_mode") == "reply"
        ]
        normal_ids = [post_id for post_id in post_ids if post_id not in reply_ids]
        if reply_ids and (normal_ids or len(reply_ids) > 1):
            self.x_post_status.setText(
                "選手権返信は1件だけチェックして送信してください。"
            )
            return
        copy_needed = [
            post_id for post_id in post_ids
            if not rows.get(post_id, {}).get("post_text")
            or rows.get(post_id, {}).get("status") == "copy_pending"
        ]
        if copy_needed:
            if self.x_copy_worker is not None:
                self.x_post_status.setText("投稿文を作成中です。完了後にX公式画面を開きます。")
                return
            self.x_schedule_after_copy_ids = post_ids
            self.x_post_status.setText("投稿文を作成しています。完了後にX公式画面を開きます。")
            self.x_copy_worker = XCopyWorker(self.site.root, copy_needed)
            self.x_copy_worker.signals.progress.connect(self._x_post_progress_changed)
            self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
            self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
            self.x_copy_button.setEnabled(False)
            self.x_schedule_button.setEnabled(False)
            self.thread_pool.start(self.x_copy_worker)
            return
        self._start_x_schedule(post_ids)

    def _start_x_schedule(self, post_ids: list[str]) -> None:
        if self.x_schedule_worker is not None:
            return
        settings = load_x_settings(self.site.root)
        manual_delivery = bool(settings.get("manual_delivery_only", False))
        rows = {
            str(row.get("post_id") or ""): row
            for row in list_x_posts(self.site.root)
        }
        thread_ids = [
            post_id for post_id in post_ids
            if rows.get(post_id, {}).get("delivery_mode") == "thread"
        ]
        if thread_ids:
            if len(post_ids) != 1:
                self.x_post_status.setText(
                    "漫画スレッドは1件だけ選び、1通ずつX公式画面で送信してください。"
                )
                return
            if manual_delivery:
                self._open_x_thread_step(thread_ids[0])
                return
        reply_ids = [
            post_id for post_id in post_ids
            if rows.get(post_id, {}).get("delivery_mode") == "reply"
        ]
        if reply_ids:
            if len(post_ids) != 1:
                self.x_post_status.setText(
                    "選手権返信は1件だけチェックして送信してください。"
                )
                return
            if manual_delivery:
                self._open_x_reply_post(reply_ids[0])
                return
        ready = {
            str(row.get("post_id") or "")
            for row in rows.values()
            if (not manual_delivery or row.get("delivery_mode") != "reply")
            and row.get("status") in {"copy_ready", "failed"}
            and row.get("post_text")
        }
        post_ids = [post_id for post_id in post_ids if post_id in ready]
        if not post_ids:
            self.x_post_status.setText("投稿文を作成できた候補がありません。状態欄を確認してください。")
            return
        if manual_delivery:
            if len(post_ids) != 1:
                self.x_post_status.setText(
                    "アカウント保護中は1件だけ選び、X公式画面で確認して送信してください。"
                )
                return
            self._open_x_normal_post(post_ids[0])
            return
        self.x_schedule_worker = XScheduleWorker(self.site.root, post_ids)
        self.x_schedule_worker.signals.progress.connect(self._x_post_progress_changed)
        self.x_schedule_worker.signals.completed.connect(self._x_schedule_completed)
        self.x_schedule_worker.signals.failed.connect(self._x_schedule_failed)
        self.x_schedule_button.setEnabled(False)
        self.thread_pool.start(self.x_schedule_worker)

    def _open_x_thread_step(self, post_id: str) -> None:
        if post_id != self._current_x_post_id():
            self._select_only_x_post(post_id)
        rows = list_x_posts(self.site.root)
        row = next(
            (
                item for item in rows
                if str(item.get("post_id") or "") == post_id
            ),
            None,
        )
        if not row:
            self.x_post_status.setText("漫画スレッド候補が見つかりません。")
            return
        if row.get("status") == "posted":
            self.x_post_status.setText("この漫画スレッドは最後の作品PRまで送信済みです。")
            return
        text = self.x_post_editor.toPlainText().strip()
        if not text or len(text) > 280:
            QMessageBox.warning(
                self,
                "スレッド文を確認",
                "現在のスレッド文を1～280文字で用意してください。",
            )
            return
        update_x_post(
            self.site.root,
            post_id,
            post_text=text,
            status="copy_ready",
            last_error="",
        )
        try:
            intent_url = x_thread_intent_url(self.site.root, post_id)
        except ValueError as exc:
            QMessageBox.warning(self, "漫画スレッドを送信できません", str(exc))
            return
        QApplication.clipboard().setText(text)
        paths = x_post_media_paths(self.site.root, post_id)
        if paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))
        if not QDesktopServices.openUrl(QUrl(intent_url)):
            QMessageBox.warning(self, "Xを開けません", "X公式投稿画面を開けませんでした。")
            return
        answer = QMessageBox.question(
            self,
            "送信前の確認",
            "X公式画面で現在の画像を添付し、この1通を送信しましたか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.x_post_status.setText("未送信のまま残しました。同じ段階から再開できます。")
            return
        status_url, accepted = QInputDialog.getText(
            self,
            "投稿URLを記録",
            "今送信したX投稿を開き、アドレス欄の /status/ を含むURLを貼ってください。",
        )
        if not accepted or not status_url.strip():
            self.x_post_status.setText(
                "投稿URLが未記録です。次の返信へ進まず、この段階で止めています。"
            )
            return
        try:
            saved = advance_x_thread(self.site.root, post_id, status_url.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "投稿URLを記録できません", str(exc))
            return
        self._refresh_x_posts()
        self._select_only_x_post(post_id)
        if saved.get("status") == "posted":
            self.x_post_progress.setValue(100)
            self.x_post_status.setText("漫画5枚と最後の作品PRまで送信記録が完了しました。")
        else:
            steps = saved.get("thread_steps") or []
            next_index = int(saved.get("thread_step_index") or 0)
            self.x_post_progress.setValue(
                int(len(saved.get("thread_post_urls") or []) * 100 / max(1, len(steps)))
            )
            self.x_post_status.setText(
                f"{next_index}/{len(steps)}通を記録しました。次は{next_index + 1}通目です。"
            )

    def _x_schedule_completed(self, result: dict) -> None:
        self.x_schedule_worker = None
        self._refresh_x_posts()
        posted = len(result.get("posted", []))
        scheduled = len(result.get("scheduled", []))
        failures = len(result.get("failed", []))
        self.x_post_progress.setValue(100)
        self.x_post_status.setText(
            f"X投稿済み {posted}件 / 予約済み {scheduled}件 / 要確認 {failures}件。"
            "失敗分は状態欄にカーソルを合わせると確認できます。"
        )

    def _x_schedule_failed(self, message: str) -> None:
        self.x_schedule_worker = None
        self._refresh_x_posts()
        self.x_post_status.setText(f"X投稿を開始できませんでした: {message}")

    def open_chatgpt_login(self) -> None:
        if self.chatgpt_login_worker is not None:
            return
        self.chatgpt_login_button.setEnabled(False)
        self.chatgpt_login_button.setText("ログイン待機中")
        self.chatgpt_login_worker = ChatGptLoginWorker()
        self.chatgpt_login_worker.signals.progress.connect(
            self._chatgpt_browser_progress
        )
        self.chatgpt_login_worker.signals.completed.connect(
            self._chatgpt_login_completed
        )
        self.chatgpt_login_worker.signals.failed.connect(
            self._chatgpt_login_failed
        )
        self.thread_pool.start(self.chatgpt_login_worker)

    def _chatgpt_login_completed(self, _result: dict) -> None:
        self.chatgpt_login_worker = None
        self.chatgpt_login_button.setEnabled(True)
        self.chatgpt_login_button.setText("ChatGPTログイン済み")
        self.generate_status.setText("ChatGPTログインを保存しました")
        self.start_chatgpt_auto_processing()

    def _chatgpt_login_failed(self, message: str) -> None:
        self.chatgpt_login_worker = None
        self.chatgpt_login_button.setEnabled(True)
        self.chatgpt_login_button.setText("ChatGPTログイン")
        self.generate_status.setText("ChatGPTログインに失敗しました")
        self.generate_result.setText(message)

    def _chatgpt_browser_progress(self, value: int, message: str) -> None:
        value = max(0, min(100, value))
        self.generate_progress.setValue(value)
        self.generate_percent.setText(f"{value}%")
        self.generate_status.setText(message)
        if hasattr(self, "auto_progress"):
            self._set_automation_phase(
                self._chatgpt_phase_for_message(message),
                self._automation_message(message),
                progress=value,
            )

    def start_chatgpt_auto_processing(self) -> None:
        if self.chatgpt_send_worker is not None or self.chatgpt_login_worker is not None:
            return
        # Keep one complete article in flight. Deterministic collection and
        # validation happen locally; Codex is called only once for editorial work.
        request_ids = queued_chatgpt_request_ids(self.site.root, limit=1)
        if not request_ids:
            self.generate_status.setText("現在処理するURLはありません")
            self.generate_result.setText(
                "待機列は保持しません。次の巡回で見つけた新しい候補から処理します。"
            )
            self._refresh_chatgpt_queue_status()
            return
        self.chatgpt_sending_request_ids = request_ids
        # A roadmap is one article's path. Reset only when the next article
        # actually starts, never when an in-flight progress message changes.
        self._set_automation_phase(
            3,
            "素材回収を開始します。この記事が保存されるまで候補収集へは戻りません。",
            reset=True,
        )
        self.chatgpt_send_worker = CodexSendWorker(
            self.site.root,
            request_ids,
        )
        self.chatgpt_send_worker.signals.progress.connect(
            self._chatgpt_browser_progress
        )
        self.chatgpt_send_worker.signals.article_saved.connect(
            self._chatgpt_article_saved
        )
        self.chatgpt_send_worker.signals.completed.connect(
            self._chatgpt_send_completed
        )
        self.chatgpt_send_worker.signals.failed.connect(
            self._chatgpt_send_failed
        )
        self.generate_status.setText(
            "Codexで1件ずつ、素材判定と記事作成を1回で処理しています"
        )
        self.generate_result.setText(
            "ChatGPT画面は使いません。取得・重複排除・検査・保存はプログラムが担当します。"
        )
        self._refresh_chatgpt_queue_status()
        self.thread_pool.start(self.chatgpt_send_worker)

    def _chatgpt_article_saved(self, result: dict) -> None:
        record_continuous_article(self.site.root)
        drafts = list_drafts(self.site.root)
        self._refresh_metrics(drafts)
        self._refresh_drafts(drafts)
        self._refresh_rights(drafts)
        self._refresh_publishing(drafts)
        self._refresh_editor_selector(drafts)
        self._refresh_review_board(drafts)
        self._refresh_chatgpt_queue_status()
        title = str(result.get("title") or "記事")
        current = int(result.get("completed_in_batch") or 0)
        total = int(result.get("batch_size") or 0)
        self.generate_status.setText(
            f"{current}/{total}件保存済み: {title}"
        )
        queue_position = int(result.get("queue_position") or 0)
        if queue_position and hasattr(self, "auto_note"):
            self._set_automation_phase(
                7,
                f"記事を完成し、予約待機の{queue_position}番目へ追加しました。",
                progress=100,
            )

    def _chatgpt_send_completed(self, result: dict) -> None:
        completed = int(result.get("count") or 0)
        failed = result.get("failed") if isinstance(result.get("failed"), list) else []
        rate_limited = bool(result.get("rate_limited"))
        self.chatgpt_send_worker = None
        self.chatgpt_sending_request_ids = []
        if rate_limited:
            retry_at = record_continuous_rate_limit(self.site.root)
            retry_label = self._continuous_retry_label(retry_at)
            message = (
                "Codexの利用上限で今回の記事を停止しました。"
                f"次の自動再試行は {retry_label} です。"
            )
            self.generate_status.setText(message)
            self.generate_result.setText(
                "Codexは利用上限の解除時刻を返さないため、ここはアプリが次に試す時刻です。"
                "古い候補は残さず、その時点の新しい候補を1件だけ処理します。"
            )
            self._set_automation_phase(7, message, progress=100)
            self._record_crawl_result("stopped", message, created_count=completed, failed_count=0)
        else:
            if completed:
                clear_continuous_rate_limit(self.site.root)
            self.generate_status.setText(f"{completed}件の記事を完成・保存しました")
        if not rate_limited and failed:
            first = str((failed[0] or {}).get("message") or "")
            self.generate_result.setText(
                f"{len(failed)}件は検査不合格または取得失敗です。最初の理由: {first}"
            )
        elif not rate_limited:
            self.generate_result.setText(
                "公開前ボードへ保存しました。続きがあれば自動で処理します。"
            )
        self.refresh_all()
        self._refresh_chatgpt_queue_status()
        QTimer.singleShot(1500, self._scheduler_tick)

    def _chatgpt_send_failed(self, message: str) -> None:
        stop_pending_chatgpt_requests(
            self.site.root,
            "処理が中断したため今回の候補を停止しました。次回巡回で新しい候補を取得します",
        )
        self.chatgpt_send_worker = None
        self.chatgpt_sending_request_ids = []
        self.generate_status.setText("Codex記事処理に失敗しました")
        self.generate_result.setText(message)
        self._refresh_chatgpt_queue_status()
        # A worker-level failure must not turn always-on operation into an
        # indefinite idle state. The next tick selects a fresh candidate.
        QTimer.singleShot(1500, self._scheduler_tick)

    def _review_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        head = QHBoxLayout()
        head.addWidget(heading(
            "公開前ボード",
            "自動生成された記事をサイトと同じ見た目で確認し、公開・予約待機・消去を選びます。",
        ), 1)
        refresh = button("更新")
        refresh.clicked.connect(self._refresh_review_board)
        head.addWidget(refresh)
        layout.addLayout(head)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("表示"))
        self.review_filter = QComboBox()
        for label, value in (
            ("すべて", "all"),
            ("未判別", "unreviewed"),
            ("予約待機", "queued"),
            ("公開済み", "published"),
            ("消去済み", "deleted"),
            ("公開失敗", "failed"),
        ):
            self.review_filter.addItem(label, value)
        self.review_filter.currentIndexChanged.connect(self._refresh_review_board)
        filters.addWidget(self.review_filter)
        filters.addWidget(QLabel("並び順"))
        self.review_sort = QComboBox()
        self.review_sort.addItem("新しい順", "newest")
        self.review_sort.addItem("古い順", "oldest")
        self.review_sort.addItem("待機順", "queue")
        self.review_sort.currentIndexChanged.connect(self._refresh_review_board)
        filters.addWidget(self.review_sort)
        filters.addStretch()
        self.review_queue_label = QLabel("予約待機 0件", objectName="success")
        filters.addWidget(self.review_queue_label)
        layout.addLayout(filters)

        self.scheduler_note = QLabel("", objectName="muted")
        layout.addWidget(self.scheduler_note)

        self.review_view = QWebEngineView()
        self.review_view.setMinimumHeight(560)
        self.review_page = ReviewActionPage(self.review_view)
        self.review_page.action_requested.connect(self._review_action)
        self.review_view.setPage(self.review_page)
        self.review_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        layout.addWidget(self.review_view, 1)
        return self._page_shell(body)

    def _drafts_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        head = QHBoxLayout()
        head.addWidget(heading("記事一覧", "自動生成した記事を確認して編集します。"), 1)
        new = button("URLから新規作成", "primary")
        new.clicked.connect(lambda: self.switch_page("create"))
        head.addWidget(new)
        layout.addLayout(head)
        self.draft_table = self._table(["タイトル", "カテゴリー", "素材", "許可", "更新日時"])
        self.draft_table.doubleClicked.connect(self.open_selected_draft)
        layout.addWidget(self.draft_table)
        open_button = button("選択した記事を編集", "primary")
        open_button.clicked.connect(self.open_selected_draft)
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignRight)
        return self._page_shell(body)

    def _editor_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("記事編集", "内容を直して保存すると、プレビューも更新されます。"))
        self.editor_search = QLineEdit()
        self.editor_search.setPlaceholderText("タイトル・元記事URL・記事IDで検索")
        self.editor_search.setClearButtonEnabled(True)
        self.editor_search.textChanged.connect(self._filter_editor_selector)
        layout.addWidget(self.editor_search)
        selector_row = QHBoxLayout()
        self.editor_select = QComboBox()
        self.editor_select.currentIndexChanged.connect(self._editor_selection_changed)
        selector_row.addWidget(self.editor_select, 1)
        load = button("読み込む")
        load.clicked.connect(self.load_editor_draft)
        selector_row.addWidget(load)
        layout.addLayout(selector_row)
        self.editor_affiliate_panel = QFrame()
        self.editor_affiliate_panel.setObjectName("warningPanel")
        self.editor_affiliate_layout = QVBoxLayout(self.editor_affiliate_panel)
        layout.addWidget(self.editor_affiliate_panel)
        self.editor_affiliate_panel.hide()
        columns = QHBoxLayout()
        form_layout = QVBoxLayout()
        form_layout.addWidget(QLabel("タイトル"))
        self.editor_title = QLineEdit()
        form_layout.addWidget(self.editor_title)
        form_layout.addWidget(QLabel("概要"))
        self.editor_summary = QPlainTextEdit()
        self.editor_summary.setMaximumHeight(110)
        form_layout.addWidget(self.editor_summary)
        form_layout.addWidget(QLabel("カテゴリー"))
        self.editor_category = QLineEdit()
        form_layout.addWidget(self.editor_category)
        form_layout.addWidget(QLabel("元記事URL"))
        self.editor_source = QLineEdit()
        form_layout.addWidget(self.editor_source)
        self.editor_media = QLabel("画像 0枚 / 動画 0本", objectName="muted")
        form_layout.addWidget(self.editor_media)
        self.editor_identity = QLabel("本人リンク: 判定前", objectName="muted")
        self.editor_identity.setWordWrap(True)
        form_layout.addWidget(self.editor_identity)
        self.editor_quality = QLabel("品質判定: 読み込み前", objectName="muted")
        self.editor_quality.setWordWrap(True)
        form_layout.addWidget(self.editor_quality)
        form_layout.addWidget(QLabel("修正理由（次回生成へ反映）"))
        self.editor_feedback_reason = QComboBox()
        self.editor_feedback_reason.addItem("変更内容から自動判定", "")
        for code in (
            "wrong_source", "wrong_media", "wrong_person", "wrong_title",
            "wrong_pr", "wrong_card_image", "missing_official_link",
            "missing_video", "media_count_mismatch", "unnatural_copy",
            "duplicate_content", "other",
        ):
            self.editor_feedback_reason.addItem(QUALITY_FAILURE_LABELS[code], code)
        form_layout.addWidget(self.editor_feedback_reason)
        actions = QHBoxLayout()
        save = button("変更を保存", "primary")
        save.clicked.connect(self.save_editor_draft)
        self.rebuild_media_button = button("素材を取り直して作り直す")
        self.rebuild_media_button.clicked.connect(self.regenerate_editor_article)
        self.refine_button = button("Codexで会話を推敲")
        self.refine_button.clicked.connect(self.refine_editor_draft)
        source = button("元記事を開く")
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.editor_source.text())))
        actions.addWidget(save)
        actions.addWidget(self.rebuild_media_button)
        actions.addWidget(self.refine_button)
        actions.addWidget(source)
        self.editor_publish = button("サイトへ公開", "primary")
        self.editor_publish.clicked.connect(lambda: self.start_publish(self.current_slug))
        actions.addWidget(self.editor_publish)
        self.editor_open_published = button("公開記事を開く")
        self.editor_open_published.clicked.connect(lambda: self.open_published_article(self.current_slug))
        actions.addWidget(self.editor_open_published)
        actions.addStretch()
        form_layout.addLayout(actions)
        columns.addWidget(panel(form_layout), 4)
        self.preview = QWebEngineView()
        self.preview.setMinimumHeight(560)
        self.preview_page = PreviewPage(self.preview)
        self.preview_page.video_requested.connect(self.open_video_player)
        self.preview.setPage(self.preview_page)
        self.preview.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        columns.addWidget(self.preview, 6)
        layout.addLayout(columns)
        return self._page_shell(body)

    def _rights_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("許可管理", "記事を確認した後、素材提供者への連絡と許可状況を記録します。"))
        self.rights_table = self._table(["タイトル", "状態", "連絡先", "元記事"])
        layout.addWidget(self.rights_table)
        edit = button("選択した記事の許可状態を更新", "primary")
        edit.clicked.connect(self.edit_rights)
        layout.addWidget(edit, 0, Qt.AlignmentFlag.AlignRight)
        return self._page_shell(body)

    def _publishing_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "公開管理",
            "許可済みの記事を選び、記事・画像・動画をサイトへ組み込んでGitHub Pagesへ公開します。",
        ))
        self.publish_table = self._table(["タイトル", "公開", "状態", "許可", "素材", "公開先", "公開URL"])
        self.publish_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5):
            self.publish_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.publish_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.publish_table.doubleClicked.connect(self.edit_selected_publish_article)
        layout.addWidget(self.publish_table)
        controls = QHBoxLayout()
        sync = button("公開更新を反映", "primary")
        sync.clicked.connect(self.sync_publish_switches)
        edit = button("選択を編集")
        edit.clicked.connect(self.edit_selected_publish_article)
        open_button = button("選択を開く")
        open_button.clicked.connect(self.open_selected_published_article)
        controls.addWidget(sync)
        controls.addWidget(edit)
        controls.addWidget(open_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.publish_note = QLabel("公開ON/OFFを選び、公開更新を反映してください", objectName="muted")
        self.publish_note.setWordWrap(True)
        layout.addWidget(self.publish_note)
        return self._page_shell(body)

    def _outreach_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "掲載営業",
            "おすすめサイト、アンテナ、相互リンク先への掲載依頼を、重複なく管理します。",
        ))

        metrics = QHBoxLayout()
        self.outreach_metrics = {}
        for key, title in (
            ("candidates", "未送信"),
            ("contacted", "送信済み"),
            ("replied", "返答あり"),
            ("listed", "掲載済み"),
        ):
            inner = QVBoxLayout()
            value = QLabel("0", objectName="metric")
            self.outreach_metrics[key] = value
            inner.addWidget(value)
            inner.addWidget(QLabel(title, objectName="muted"))
            metrics.addWidget(panel(inner), 1)
        layout.addLayout(metrics)
        layout.addSpacing(12)

        profile_layout = QHBoxLayout()
        profile_copy = QVBoxLayout()
        profile_copy.addWidget(QLabel("送付するサイト情報", objectName="sectionTitle"))
        self.outreach_profile_label = QLabel(objectName="muted")
        self.outreach_profile_label.setWordWrap(True)
        profile_copy.addWidget(self.outreach_profile_label)
        profile_layout.addLayout(profile_copy, 1)
        copy_profile = button("サイト情報をコピー")
        copy_profile.clicked.connect(self.copy_outreach_profile)
        copy_link = button("リンクHTMLをコピー")
        copy_link.clicked.connect(self.copy_outreach_link_html)
        open_operator = button("運営者向けページを開く", "primary")
        open_operator.clicked.connect(self.open_outreach_operator_page)
        profile_layout.addWidget(copy_profile)
        profile_layout.addWidget(copy_link)
        profile_layout.addWidget(open_operator)
        layout.addWidget(panel(profile_layout, True))
        layout.addSpacing(18)

        target_head = QHBoxLayout()
        target_head.addWidget(heading(
            "掲載候補",
            "掲載条件を確認し、相手ごとの依頼文をコピーして送ります。",
        ), 1)
        search = button("新しい候補を探す")
        search.clicked.connect(self.open_outreach_search)
        add = button("掲載先を追加", "primary")
        add.clicked.connect(self.add_outreach_target)
        target_head.addWidget(search)
        target_head.addWidget(add)
        layout.addLayout(target_head)

        self.outreach_table = self._table([
            "状態", "掲載先", "種類", "候補にした理由", "連絡先", "最終更新",
        ])
        self.outreach_table.setMinimumHeight(360)
        self.outreach_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.outreach_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.outreach_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.outreach_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.outreach_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.outreach_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.outreach_table.itemSelectionChanged.connect(self._outreach_selection_changed)
        self.outreach_table.doubleClicked.connect(self.edit_selected_outreach_target)
        layout.addWidget(self.outreach_table)

        controls = QHBoxLayout()
        open_contact = button("連絡ページを開く")
        open_contact.clicked.connect(self.open_selected_outreach_contact)
        edit = button("編集")
        edit.clicked.connect(self.edit_selected_outreach_target)
        self.outreach_status_combo = QComboBox()
        for key, label in OUTREACH_STATUS_LABELS.items():
            self.outreach_status_combo.addItem(label, key)
        update_status = button("状態を更新")
        update_status.clicked.connect(self.update_selected_outreach_status)
        copy_message = button("依頼文をコピー", "primary")
        copy_message.clicked.connect(self.copy_selected_outreach_message)
        remove = button("削除", "danger")
        remove.clicked.connect(self.remove_selected_outreach_target)
        controls.addWidget(open_contact)
        controls.addWidget(edit)
        controls.addWidget(self.outreach_status_combo)
        controls.addWidget(update_status)
        controls.addStretch()
        controls.addWidget(copy_message)
        controls.addWidget(remove)
        layout.addLayout(controls)

        layout.addSpacing(14)
        preview_head = QHBoxLayout()
        preview_head.addWidget(QLabel("送信前の依頼文", objectName="sectionTitle"))
        preview_head.addStretch()
        self.outreach_selection_note = QLabel("掲載先を選択してください", objectName="muted")
        preview_head.addWidget(self.outreach_selection_note)
        layout.addLayout(preview_head)
        self.outreach_message_preview = QPlainTextEdit()
        self.outreach_message_preview.setReadOnly(True)
        self.outreach_message_preview.setMinimumHeight(230)
        layout.addWidget(self.outreach_message_preview)
        return self._page_shell(body)

    def _outreach_profile(self) -> dict[str, str]:
        return load_outreach_profile(self.site.root, self.site.name, self.site.public_url)

    def _selected_outreach_target_id(self) -> str:
        row = self.outreach_table.currentRow()
        if row < 0:
            return ""
        item = self.outreach_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _selected_outreach_target(self) -> dict | None:
        target_id = self._selected_outreach_target_id()
        return next(
            (item for item in list_outreach_targets(self.site.root) if item.get("target_id") == target_id),
            None,
        )

    def _refresh_outreach(self) -> None:
        if not hasattr(self, "outreach_table"):
            return
        bootstrap_outreach_targets(self.site.root)
        profile = self._outreach_profile()
        self.outreach_profile_label.setText(
            f"{profile['site_name']}　{profile['public_url']}\n"
            f"RSS: {profile['rss_url']}　更新: {profile['update_frequency']}"
        )
        selected_id = self._selected_outreach_target_id()
        targets = list_outreach_targets(self.site.root)
        counts = {
            "candidates": sum(item.get("status") in {"candidate", "ready"} for item in targets),
            "contacted": sum(item.get("status") == "contacted" for item in targets),
            "replied": sum(item.get("status") == "replied" for item in targets),
            "listed": sum(item.get("status") == "listed" for item in targets),
        }
        for key, label in self.outreach_metrics.items():
            label.setText(str(counts[key]))
        self.outreach_table.setRowCount(len(targets))
        selected_row = -1
        for row, target in enumerate(targets):
            target_id = str(target.get("target_id") or "")
            values = (
                OUTREACH_STATUS_LABELS.get(str(target.get("status") or ""), "候補"),
                str(target.get("name") or ""),
                str(target.get("category") or ""),
                str(target.get("fit_reason") or ""),
                str(target.get("contact_url") or target.get("site_url") or ""),
                str(target.get("updated_at") or "")[:16].replace("T", " "),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, target_id)
                self.outreach_table.setItem(row, column, item)
            if target_id == selected_id:
                selected_row = row
        if selected_row < 0 and targets:
            selected_row = 0
        if selected_row >= 0:
            self.outreach_table.selectRow(selected_row)
        else:
            self.outreach_message_preview.clear()
            self.outreach_selection_note.setText("掲載先を追加してください")

    def _outreach_selection_changed(self) -> None:
        target = self._selected_outreach_target()
        if target is None:
            self.outreach_message_preview.clear()
            self.outreach_selection_note.setText("掲載先を選択してください")
            return
        self.outreach_message_preview.setPlainText(
            outreach_message(self._outreach_profile(), target)
        )
        status = str(target.get("status") or "candidate")
        status_index = self.outreach_status_combo.findData(status)
        self.outreach_status_combo.setCurrentIndex(max(0, status_index))
        self.outreach_selection_note.setText(
            f"{target.get('name', '')}／{OUTREACH_STATUS_LABELS.get(status, status)}"
        )

    def add_outreach_target(self) -> None:
        dialog = OutreachTargetDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            upsert_outreach_target(self.site.root, dialog.values())
        except ValueError as exc:
            QMessageBox.warning(self, "掲載先を保存できません", str(exc))
            return
        self._refresh_outreach()

    def edit_selected_outreach_target(self) -> None:
        target = self._selected_outreach_target()
        if target is None:
            QMessageBox.information(self, "掲載先を選択", "編集する掲載先を選択してください。")
            return
        dialog = OutreachTargetDialog(self, target)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            upsert_outreach_target(
                self.site.root,
                dialog.values(),
                str(target.get("target_id") or ""),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "掲載先を保存できません", str(exc))
            return
        self._refresh_outreach()

    def update_selected_outreach_status(self) -> None:
        target_id = self._selected_outreach_target_id()
        if not target_id:
            QMessageBox.information(self, "掲載先を選択", "状態を変更する掲載先を選択してください。")
            return
        try:
            update_outreach_status(
                self.site.root,
                target_id,
                str(self.outreach_status_combo.currentData() or "candidate"),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "状態を更新できません", str(exc))
            return
        self._refresh_outreach()

    def copy_selected_outreach_message(self) -> None:
        target = self._selected_outreach_target()
        if target is None:
            QMessageBox.information(self, "掲載先を選択", "依頼文を作る掲載先を選択してください。")
            return
        QApplication.clipboard().setText(outreach_message(self._outreach_profile(), target))
        if str(target.get("status") or "candidate") == "candidate":
            update_outreach_status(
                self.site.root,
                str(target.get("target_id") or ""),
                "ready",
            )
            self._refresh_outreach()
        self.outreach_selection_note.setText(
            f"{target.get('name', '')}向け依頼文をコピーしました。送信後に状態を『送信済み』へ変更してください。"
        )

    def copy_outreach_profile(self) -> None:
        QApplication.clipboard().setText(outreach_profile_text(self._outreach_profile()))
        self.outreach_selection_note.setText("サイト情報をコピーしました。")

    def copy_outreach_link_html(self) -> None:
        QApplication.clipboard().setText(outreach_link_html(self._outreach_profile()))
        self.outreach_selection_note.setText("淫談屋へのリンクHTMLをコピーしました。")

    def open_outreach_operator_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._outreach_profile()["operator_url"]))

    def open_outreach_search(self) -> None:
        query = "成人向け まとめ アンテナ 相互リンク 募集 サイト運営者"
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={quote_plus(query)}"))

    def open_selected_outreach_contact(self) -> None:
        target = self._selected_outreach_target()
        if target is None:
            QMessageBox.information(self, "掲載先を選択", "連絡ページを開く掲載先を選択してください。")
            return
        url = str(target.get("contact_url") or target.get("site_url") or "")
        QDesktopServices.openUrl(QUrl(url))

    def remove_selected_outreach_target(self) -> None:
        target = self._selected_outreach_target()
        if target is None:
            return
        if QMessageBox.question(
            self,
            "掲載先を削除",
            f"{target.get('name', 'この掲載先')}を管理一覧から削除しますか？",
        ) != QMessageBox.StandardButton.Yes:
            return
        remove_outreach_target(self.site.root, str(target.get("target_id") or ""))
        self._refresh_outreach()

    def _sites_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        head = QHBoxLayout()
        head.addWidget(heading("管理サイト", "サイトを増やしても、ここから追加・切り替えできます。公開URLは上部からいつでも開けます。"), 1)
        add = button("サイトを追加", "primary")
        add.clicked.connect(self.add_site)
        head.addWidget(add)
        layout.addLayout(head)
        self.site_table = self._table(["サイト名", "公開URL", "公開方式", "作業フォルダ"])
        self.site_table.doubleClicked.connect(self.activate_selected_site)
        layout.addWidget(self.site_table)
        controls = QHBoxLayout()
        activate = button("このサイトへ切り替え", "primary")
        activate.clicked.connect(self.activate_selected_site)
        edit = button("編集")
        edit.clicked.connect(self.edit_site)
        remove = button("削除", "danger")
        remove.clicked.connect(self.remove_site)
        controls.addWidget(activate)
        controls.addWidget(edit)
        controls.addStretch()
        controls.addWidget(remove)
        layout.addLayout(controls)
        return self._page_shell(body)

    def _settings_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("設定", "接続状況と保存場所を確認できます。"))
        details = QVBoxLayout()
        self.codex_state = QLabel("Codex: 確認中")
        self.registry_path = QLabel(f"サイト設定: {self.registry.path}", objectName="muted")
        self.workspace_path = QLabel(objectName="muted")
        self.workspace_path.setWordWrap(True)
        details.addWidget(self.codex_state)
        self.hybrid_state = QLabel(objectName="muted")
        self.hybrid_state.setWordWrap(True)
        details.addWidget(self.hybrid_state)
        details.addWidget(self.registry_path)
        details.addWidget(self.workspace_path)
        layout.addWidget(panel(details))
        self.hybrid_state.setText(
            "ハイブリッド運用: 取得・重複排除・動画確認・保存・公開検査はPC、"
            "本編素材の意味判断・タイトル・概要・会話・タグはCodexが1記事1回で担当します。"
            f" 現在の処理待ち {pending_chatgpt_count(self.site.root)}件"
        )
        fanza = QVBoxLayout()
        fanza.setContentsMargins(18, 16, 18, 16)
        fanza.addWidget(QLabel("FANZAアフィリエイト", objectName="sectionTitle"))
        fanza.addWidget(QLabel(
            "IDは最初に1回だけ保存します。以後は手動作成・自動巡回・再生成・公開のすべてで、"
            "採用した商品URLからあなたの広告リンクを自動生成します。",
            objectName="muted",
        ))
        fanza_row = QHBoxLayout()
        self.fanza_affiliate_id_input = QLineEdit()
        self.fanza_affiliate_id_input.setPlaceholderText(
            "アフィリエイトID、または af_id= を含むDMM生成リンク"
        )
        self.fanza_affiliate_id_input.setText(
            load_fanza_settings(self.site.root).get("affiliate_id", "")
        )
        fanza_row.addWidget(self.fanza_affiliate_id_input, 1)
        save_fanza = button("保存して全PRに適用", "primary")
        save_fanza.clicked.connect(self.save_fanza_affiliate_settings)
        fanza_row.addWidget(save_fanza)
        open_fanza = button("DMMアフィリエイトを開く")
        open_fanza.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://affiliate.dmm.com/"))
        )
        fanza_row.addWidget(open_fanza)
        fanza.addLayout(fanza_row)
        self.fanza_affiliate_status = QLabel(objectName="muted")
        self.fanza_affiliate_status.setWordWrap(True)
        current_affiliate_id = load_fanza_settings(self.site.root).get("affiliate_id", "")
        self.fanza_affiliate_status.setText(
            "設定済み。すべての記事で自動生成します。"
            if current_affiliate_id
            else "未設定。FANZAのPRは通常の商品URLのまま公開せず、ID設定まで停止します。"
        )
        fanza.addWidget(self.fanza_affiliate_status)
        layout.addWidget(panel(fanza))
        return self._page_shell(body)

    def save_fanza_affiliate_settings(self) -> None:
        if self.fanza_affiliate_worker is not None:
            QMessageBox.information(self, "FANZA設定", "現在、既存PRへ反映しています。")
            return
        value = self.fanza_affiliate_id_input.text().strip()
        if not value:
            QMessageBox.warning(
                self,
                "FANZA設定を確認",
                "アフィリエイトID、または af_id= を含むDMM生成リンクを入力してください。",
            )
            return
        self.fanza_affiliate_progress = QProgressDialog(
            "アフィリエイトIDを保存しています", "", 0, 100, self
        )
        self.fanza_affiliate_progress.setWindowTitle("FANZAアフィリエイト")
        self.fanza_affiliate_progress.setCancelButton(None)
        self.fanza_affiliate_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.fanza_affiliate_progress.show()
        self.fanza_affiliate_worker = ApplyFanzaAffiliateWorker(
            self.site.root, self.site, value
        )
        self.fanza_affiliate_worker.signals.progress.connect(
            self._fanza_affiliate_progress_changed
        )
        self.fanza_affiliate_worker.signals.completed.connect(
            self._fanza_affiliate_applied
        )
        self.fanza_affiliate_worker.signals.failed.connect(
            self._fanza_affiliate_failed
        )
        self.thread_pool.start(self.fanza_affiliate_worker)

    def _fanza_affiliate_progress_changed(self, value: int, message: str) -> None:
        self.fanza_affiliate_status.setText(message)
        if self.fanza_affiliate_progress:
            self.fanza_affiliate_progress.setLabelText(message)
            self.fanza_affiliate_progress.setValue(max(0, min(100, int(value))))

    def _fanza_affiliate_applied(self, result: dict) -> None:
        if self.fanza_affiliate_progress:
            self.fanza_affiliate_progress.setValue(100)
            self.fanza_affiliate_progress.close()
            self.fanza_affiliate_progress = None
        self.fanza_affiliate_worker = None
        affiliate_id = str(result.get("affiliate_id") or "")
        self.fanza_affiliate_id_input.setText(affiliate_id)
        published_links = int(result.get("published_links") or 0)
        self.fanza_affiliate_status.setText(
            f"設定済み。既存の公開PR {published_links}件を更新し、今後は全記事で自動生成します。"
        )
        QMessageBox.information(
            self,
            "FANZAアフィリエイトを反映しました",
            "既存の公開記事と、今後作るすべての記事へ同じアフィリエイトIDを適用しました。",
        )

    def _fanza_affiliate_failed(self, message: str) -> None:
        if self.fanza_affiliate_progress:
            self.fanza_affiliate_progress.close()
            self.fanza_affiliate_progress = None
        self.fanza_affiliate_worker = None
        saved = load_fanza_settings(self.site.root).get("affiliate_id", "")
        self.fanza_affiliate_id_input.setText(saved)
        self.fanza_affiliate_status.setText(
            "IDは保存済みですが、既存の公開記事への一括反映に失敗しました。"
            if saved else "アフィリエイトIDを保存できませんでした。"
        )
        QMessageBox.warning(self, "FANZAアフィリエイトを反映できません", message)

    def _sources_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "情報源",
            "登録済みサイトを巡回し、7日ごとに取得テスト済みの新規サイトも補充します。",
        ))
        discovery_controls = QHBoxLayout()
        self.source_discovery_status_label = QLabel("自動補充の状態を確認中", objectName="muted")
        discovery_controls.addWidget(self.source_discovery_status_label, 1)
        discovery_settings = button("自動補充設定")
        discovery_settings.clicked.connect(self.open_automation_settings)
        discovery_now = button("今すぐ新規サイトを探す", "primary")
        discovery_now.clicked.connect(self.run_source_discovery_now)
        discovery_controls.addWidget(discovery_settings)
        discovery_controls.addWidget(discovery_now)
        layout.addLayout(discovery_controls)
        form = QHBoxLayout()
        self.source_name_input = QLineEdit()
        self.source_name_input.setPlaceholderText("表示名")
        self.source_feed_input = QLineEdit()
        self.source_feed_input.setPlaceholderText("https://example.com/")
        add = button("情報源を追加", "primary")
        add.clicked.connect(self.add_auto_source)
        form.addWidget(self.source_name_input)
        form.addWidget(self.source_feed_input, 1)
        form.addWidget(add)
        layout.addLayout(form)
        self.sources_table = self._table([
            "巡回", "名前", "追加元", "URL", "最終確認", "成功 / 連続失敗",
        ])
        self.sources_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.sources_table)
        controls = QHBoxLayout()
        save = button("ON/OFFを保存", "primary")
        save.clicked.connect(self.save_auto_sources)
        remove = button("選択を削除", "danger")
        remove.clicked.connect(self.remove_auto_source)
        controls.addWidget(save)
        controls.addStretch()
        controls.addWidget(remove)
        layout.addLayout(controls)
        self.sources_note = QLabel("情報源を登録すると、自動巡回で候補URLを拾えます。", objectName="muted")
        layout.addWidget(self.sources_note)
        layout.addWidget(heading(
            "直近の自動探索",
            "合格・不合格と、その判定理由を新しい順に表示します。",
        ))
        self.source_discovery_table = self._table(["判定", "サイト", "理由", "確認時刻"])
        self.source_discovery_table.setMinimumHeight(190)
        self.source_discovery_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.source_discovery_table)
        return self._page_shell(body)

    def _automation_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(
            "常時運転",
            "待機列は作らず、前の記事の終了後に最新候補を1件だけ拾って処理します。",
        ))
        controls = QHBoxLayout()
        run_now = button("今すぐ開始", "primary")
        run_now.setToolTip("常時運転をONにして、選択中の情報源を今すぐ巡回します")
        run_now.clicked.connect(self.run_manual_crawl)
        count_control = QWidget()
        count_layout = QHBoxLayout(count_control)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.setSpacing(8)
        count_layout.addWidget(QLabel("手動巡回数"))
        self.manual_crawl_value = QLabel("30件", objectName="crawlCountValue")
        count_layout.addWidget(self.manual_crawl_value)
        self.manual_crawl_count = QDial()
        self.manual_crawl_count.setRange(1, 100)
        self.manual_crawl_count.setWrapping(True)
        self.manual_crawl_count.setNotchesVisible(True)
        self.manual_crawl_count.setFixedSize(48, 48)
        self.manual_crawl_count.setValue(
            int(load_automation_settings(self.site.root).get("manual_crawl_count") or 30)
        )
        self.manual_crawl_count.valueChanged.connect(self.update_manual_crawl_count)
        count_layout.addWidget(self.manual_crawl_count)
        controls.addWidget(count_control)
        settings_button = button("常時運転・予約設定")
        settings_button.clicked.connect(self.open_automation_settings)
        controls.addWidget(run_now)
        controls.addWidget(settings_button)
        controls.addStretch()
        layout.addLayout(controls)

        metrics = QHBoxLayout()
        self.automation_state_label = QLabel("確認中", objectName="metric")
        self.automation_next_label = QLabel("--:--", objectName="metric")
        self.automation_batch_label = QLabel("0件", objectName="metric")
        self.automation_pending_label = QLabel("0件", objectName="metric")
        for value, title in (
            (self.automation_state_label, "常時運転"),
            (self.automation_next_label, "次の巡回"),
            (self.automation_batch_label, "現在の処理数"),
            (self.automation_pending_label, "同時処理"),
        ):
            inner = QVBoxLayout()
            inner.addWidget(value)
            inner.addWidget(QLabel(title, objectName="muted"))
            metrics.addWidget(panel(inner), 1)
        layout.addLayout(metrics)

        current = QVBoxLayout()
        current.setContentsMargins(16, 14, 16, 14)
        current.addWidget(QLabel("現在の処理", objectName="muted"))
        self.automation_stage_label = QLabel("待機中", objectName="sectionTitle")
        current.addWidget(self.automation_stage_label)
        self.auto_progress = QProgressBar()
        self.auto_progress.setRange(0, 100)
        current.addWidget(self.auto_progress)
        self.auto_note = QLabel("待機中です。", objectName="muted")
        self.auto_note.setWordWrap(True)
        current.addWidget(self.auto_note)
        current.addLayout(self._build_automation_roadmap())
        self.automation_batch_note = QLabel("")
        self.automation_batch_note.setWordWrap(True)
        current.addWidget(self.automation_batch_note)
        layout.addWidget(panel(current, True))

        learning_head = QHBoxLayout()
        learning_head.addWidget(QLabel("サイト別の学習状況", objectName="sectionTitle"))
        learning_head.addStretch()
        learning_head.addWidget(QLabel(
            "成功した取得方法を次回のテンプレとして再利用",
            objectName="muted",
        ))
        layout.addLayout(learning_head)
        self.site_learning_table = QTableWidget(0, 7)
        self.site_learning_table.setHorizontalHeaderLabels(
            ["サイト", "習熟度", "成功 / 失敗", "成功率", "次の取得方法", "平均時間", "直近の問題"]
        )
        self.site_learning_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3, 4, 5):
            self.site_learning_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.site_learning_table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self.site_learning_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.site_learning_table.setAlternatingRowColors(True)
        self.site_learning_table.verticalHeader().setVisible(False)
        self.site_learning_table.setMinimumHeight(190)
        self.site_learning_table.setMaximumHeight(300)
        layout.addWidget(self.site_learning_table)

        completed_head = QHBoxLayout()
        completed_head.addWidget(QLabel("記事の処理結果", objectName="sectionTitle"))
        completed_head.addStretch()
        completed_head.addWidget(QLabel("成功・失敗・停止を新しい順に表示", objectName="muted"))
        layout.addLayout(completed_head)
        self.automation_completed_table = QTableWidget(0, 4)
        self.automation_completed_table.setHorizontalHeaderLabels(
            ["終了時刻", "記事 / URL", "結果", "内容"]
        )
        self.automation_completed_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.automation_completed_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.automation_completed_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.automation_completed_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.automation_completed_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.automation_completed_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.automation_completed_table.setAlternatingRowColors(True)
        self.automation_completed_table.verticalHeader().setDefaultSectionSize(34)
        self.automation_completed_table.setMinimumHeight(430)
        self.automation_completed_table.cellDoubleClicked.connect(
            self._open_completed_automation_article
        )
        layout.addWidget(self.automation_completed_table)

        activity_head = QHBoxLayout()
        activity_head.addWidget(QLabel("直近の確定ログ", objectName="sectionTitle"))
        activity_head.addStretch()
        activity_head.addWidget(QLabel("成功・失敗・停止だけを表示", objectName="muted"))
        layout.addLayout(activity_head)
        self.automation_activity_table = QTableWidget(0, 4)
        self.automation_activity_table.setHorizontalHeaderLabels(
            ["時刻", "記事URL", "結果", "内容"]
        )
        self.automation_activity_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.automation_activity_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.automation_activity_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.automation_activity_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.automation_activity_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.automation_activity_table.setAlternatingRowColors(True)
        self.automation_activity_table.setMinimumHeight(190)
        layout.addWidget(self.automation_activity_table)

        summary = QVBoxLayout()
        summary.setContentsMargins(16, 14, 16, 14)
        summary.addWidget(QLabel("今後の予定", objectName="sectionTitle"))
        self.automation_crawl_schedule = QLabel("")
        self.automation_crawl_schedule.setWordWrap(True)
        summary.addWidget(self.automation_crawl_schedule)
        self.automation_publish_schedule = QLabel("")
        self.automation_publish_schedule.setWordWrap(True)
        summary.addWidget(self.automation_publish_schedule)
        self.automation_permission_schedule = QLabel("権利確認が必要な記事は自動公開しません")
        summary.addWidget(self.automation_permission_schedule)
        self.automation_scheduler_note = QLabel("", objectName="muted")
        self.automation_scheduler_note.setWordWrap(True)
        summary.addWidget(self.automation_scheduler_note)
        layout.addWidget(panel(summary))
        layout.addStretch()
        self._load_scheduler_controls()
        return self._page_shell(body)

    def _coming_page(self, title: str, text: str) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading(title, text))
        note = QVBoxLayout()
        note.addWidget(QLabel("準備中", objectName="sectionTitle"))
        note.addWidget(QLabel("画面とデータの置き場所は先に用意してあります。現在は手動URLからの記事生成を優先して実装しています。", objectName="muted"))
        layout.addWidget(panel(note, True))
        return self._page_shell(body)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setMinimumHeight(420)
        return table

    def switch_page(self, name: str) -> None:
        if name == "review":
            name = "dashboard"
        self.stack.setCurrentWidget(self.pages[name])
        titles = {
            "dashboard": ("OVERVIEW", "ダッシュボード"), "create": ("CREATE", "URLから記事を作成"),
            "analytics": ("ANALYTICS", "アクセス解析"),
            "social_x": ("SOCIAL", "X投稿管理"),
            "outreach": ("OUTREACH", "掲載営業"),
            "drafts": ("ARTICLES", "記事一覧"), "editor": ("EDIT", "記事編集"),
            "rights": ("RIGHTS", "許可管理"), "publishing": ("PUBLISH", "公開管理"),
            "sources": ("SOURCES", "情報源"), "automation": ("AUTOMATION", "自動巡回"),
            "sites": ("SITES", "管理サイト"), "settings": ("SETTINGS", "設定"),
        }
        self.eyebrow.setText(titles[name][0])
        self.page_title.setText(titles[name][1])
        for key, nav in self.nav_buttons.items():
            nav.setChecked(key == name)
        if name in {"dashboard", "drafts", "rights", "publishing", "sites", "editor", "social_x", "outreach"}:
            self.refresh_all()
        if name == "analytics":
            self._refresh_ga4_status()
            QTimer.singleShot(0, self._load_analytics_on_open)

    def _ga4_read_ready(self) -> bool:
        return bool(
            load_ga4_property_id(self.site.root)
            and ga4_credentials_path(self.site.root).is_file()
        )

    def _load_analytics_on_open(self) -> None:
        if not self._ga4_read_ready():
            return
        self.load_ga4_realtime_data()
        if not self.ga4_history_loaded_once:
            self.load_ga4_data()

    def _analytics_realtime_tick(self) -> None:
        analytics_page = self.pages.get("analytics")
        if (
            analytics_page is not None
            and self.stack.currentWidget() is analytics_page
            and self.ga4_auto_refresh.isChecked()
            and self._ga4_read_ready()
        ):
            self.load_ga4_realtime_data()

    def _refresh_ga4_status(self) -> None:
        measurement_id = load_ga4_measurement_id(self.site.root)
        if measurement_id:
            property_id = load_ga4_property_id(self.site.root)
            credentials_ready = ga4_credentials_path(self.site.root).is_file()
            self.ga4_status.setText(
                f"送信: {measurement_id} / 読み取り: "
                f"{'接続済み' if property_id and credentials_ready else '未接続'}"
            )
            if not property_id or not credentials_ready:
                self.ga4_live_status.setText(
                    "リアルタイム表示には、数字のGA4プロパティIDと読み取りJSONが必要です。"
                )
        else:
            self.ga4_status.setText("未設定: 測定IDを保存するまで、サイトはアクセス解析を行いません。")

    def choose_ga4_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "GA4読み取り用サービスアカウントJSONを選択", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            save_ga4_credentials(self.site.root, Path(path))
            self.ga4_report_status.setText("読み取りJSONを保存しました。プロパティIDを入力して保存してください。")
            self._refresh_ga4_status()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "GA4読み取り設定", str(exc))

    def save_ga4_read_settings(self) -> None:
        try:
            save_ga4_property_id(self.site.root, self.ga4_property_id_input.text())
        except ValueError as exc:
            QMessageBox.warning(self, "GA4読み取り設定", str(exc))
            return
        self._refresh_ga4_status()
        if self._ga4_read_ready():
            self.ga4_report_status.setText("読み取り設定を保存しました。実測データを取得します。")
            self._load_analytics_on_open()
        else:
            self.ga4_report_status.setText("プロパティIDを保存しました。読み取りJSONも選択してください。")

    def load_ga4_data(self) -> None:
        if self.analytics_worker is not None:
            return
        if not self._ga4_read_ready():
            self._refresh_ga4_status()
            self.ga4_report_status.setText("読み取り設定が未完成です。プロパティIDとJSONを設定してください。")
            return
        self.analytics_worker = AnalyticsWorker(self.site.root, "historical")
        self.ga4_history_button.setEnabled(False)
        self.ga4_report_status.setText("GA4から過去7日間を集計しています…")
        self.analytics_worker.signals.completed.connect(self._ga4_loaded)
        self.analytics_worker.signals.failed.connect(self._ga4_failed)
        self.thread_pool.start(self.analytics_worker)

    def load_ga4_realtime_data(self) -> None:
        if self.analytics_realtime_worker is not None:
            return
        if not self._ga4_read_ready():
            self._refresh_ga4_status()
            return
        self.analytics_realtime_worker = AnalyticsWorker(self.site.root, "realtime")
        self.ga4_realtime_button.setEnabled(False)
        self.ga4_live_status.setText("GA4から直近30分を取得しています…")
        self.analytics_realtime_worker.signals.completed.connect(self._ga4_realtime_loaded)
        self.analytics_realtime_worker.signals.failed.connect(self._ga4_realtime_failed)
        self.thread_pool.start(self.analytics_realtime_worker)

    @staticmethod
    def _ga4_event_count(rows: object, name: str) -> int:
        if not isinstance(rows, list):
            return 0
        for row in rows:
            if isinstance(row, dict) and row.get("eventName") == name:
                return int(row.get("eventCount") or 0)
        return 0

    @staticmethod
    def _ga4_time_label(value: object) -> str:
        try:
            stamp = datetime.fromisoformat(str(value))
            return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return str(value or "不明")

    def _ga4_realtime_loaded(self, data: dict) -> None:
        self.analytics_realtime_worker = None
        self.ga4_realtime_button.setEnabled(True)
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        self.ga4_realtime_metrics["activeUsers"].setText(
            f"{int(summary.get('activeUsers') or 0):,}"
        )
        self.ga4_realtime_metrics["pageViews"].setText(
            f"{int(summary.get('screenPageViews') or 0):,}"
        )
        self.ga4_realtime_metrics["prImpressions"].setText(
            f"{self._ga4_event_count(data.get('events'), 'pr_impression'):,}"
        )
        self.ga4_realtime_metrics["prClicks"].setText(
            f"{self._ga4_event_count(data.get('events'), 'pr_click'):,}"
        )
        self._fill_analytics_table(
            self.ga4_realtime_tables["pages"],
            data.get("pages"),
            ["unifiedScreenName", "screenPageViews", "activeUsers"],
            {"screenPageViews", "activeUsers"},
            set(),
        )
        self._fill_analytics_table(
            self.ga4_realtime_tables["events"],
            data.get("events"),
            ["eventName", "eventCount"],
            {"eventCount"},
            set(),
        )
        self._fill_analytics_table(
            self.ga4_realtime_tables["minutes"],
            data.get("minutes"),
            ["minutesAgo", "screenPageViews", "eventCount"],
            {"minutesAgo", "screenPageViews", "eventCount"},
            set(),
        )
        stamp = self._ga4_time_label(data.get("generated_at"))
        self.ga4_live_status.setText(
            f"取得成功 {stamp} / 直近30分の実測値 / 次回は30秒以内"
        )

    def _ga4_realtime_failed(self, message: str) -> None:
        self.analytics_realtime_worker = None
        self.ga4_realtime_button.setEnabled(True)
        self.ga4_live_status.setText(
            f"リアルタイム更新に失敗しました。前回の正常値を保持しています: {message}"
        )

    def _ga4_loaded(self, data: dict) -> None:
        self.analytics_worker = None
        self.ga4_history_button.setEnabled(True)
        self.ga4_history_loaded_once = True
        stamp = self._ga4_time_label(data.get("generated_at"))
        self.ga4_report_status.setText(
            f"取得成功 {stamp} / 集計期間 {data.get('start_date')}〜{data.get('end_date')}"
        )
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        self.ga4_report_metrics["pageViews"].setText(
            f"{int(summary.get('screenPageViews') or 0):,}"
        )
        self.ga4_report_metrics["activeUsers"].setText(
            f"{int(summary.get('activeUsers') or 0):,}"
        )
        self.ga4_report_metrics["prImpressions"].setText(
            f"{self._ga4_event_count(data.get('events'), 'pr_impression'):,}"
        )
        self.ga4_report_metrics["prClicks"].setText(
            f"{self._ga4_event_count(data.get('events'), 'pr_click'):,}"
        )
        self._fill_analytics_table(self.ga4_report_tables["articles"], data.get("articles"), ["pagePath", "pageTitle", "screenPageViews", "activeUsers"], {"screenPageViews", "activeUsers"}, set())
        self._fill_analytics_table(self.ga4_report_tables["events"], data.get("events"), ["eventName", "eventCount", "totalUsers"], {"eventCount", "totalUsers"}, set())
        self._fill_analytics_table(self.ga4_report_tables["devices"], data.get("devices"), ["deviceCategory", "operatingSystem", "browser", "screenPageViews", "activeUsers"], {"screenPageViews", "activeUsers"}, set())
        self._fill_analytics_table(self.ga4_report_tables["referrers"], data.get("referrers"), ["sessionSource", "sessionMedium", "sessions", "activeUsers"], {"sessions", "activeUsers"}, set())
        self._fill_analytics_table(self.ga4_report_tables["daily"], data.get("daily"), ["date", "screenPageViews", "activeUsers", "eventCount"], {"screenPageViews", "activeUsers", "eventCount"}, set())

    def _ga4_failed(self, message: str) -> None:
        self.analytics_worker = None
        self.ga4_history_button.setEnabled(True)
        self.ga4_report_status.setText(
            f"期間集計の更新に失敗しました。前回の正常値を保持しています: {message}"
        )

    def save_ga4_settings(self) -> None:
        try:
            save_ga4_measurement_id(self.site.root, self.ga4_measurement_id_input.text())
        except ValueError as exc:
            QMessageBox.warning(self, "GA4設定を確認", str(exc))
            return
        progress = QProgressDialog("GA4設定を公開しています…", "", 0, 0, self)
        progress.setWindowTitle("GA4設定")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        try:
            publish_ga4_config(self.site.root, self.site)
        except Exception as exc:
            QMessageBox.warning(self, "GA4設定を公開できません", str(exc))
            return
        finally:
            progress.close()
        self._refresh_ga4_status()
        QMessageBox.information(self, "GA4設定を公開しました", "公開サイトにGA4設定を反映しました。")

    def _fill_analytics_table(
        self,
        table: QTableWidget,
        rows: object,
        fields: list[str],
        integer_fields: set[str],
        percent_fields: set[str],
    ) -> None:
        values = [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
        table.setRowCount(len(values))
        for row_index, row in enumerate(values):
            for column, field in enumerate(fields):
                value = row.get(field, "")
                if field in integer_fields:
                    text = f"{int(value or 0):,}"
                elif field in percent_fields:
                    text = f"{float(value or 0):.2f}%"
                else:
                    text = str(value or "")
                item = QTableWidgetItem(text)
                if field in integer_fields or field in percent_fields:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row_index, column, item)

    # Analytics v2 overrides the legacy Apps Script-era methods above. Both
    # audience pages are filled from the same response, so switching is local.
    def _ga4_read_ready(self) -> bool:
        return bool(
            load_ga4_property_id(self.site.root)
            and ga4_credentials_path(self.site.root).is_file()
        )

    def _load_analytics_on_open(self) -> None:
        self._load_ga4_cached_data()
        if not self._ga4_read_ready():
            self._refresh_ga4_status()
            return
        self.load_ga4_realtime_data()
        if not self.ga4_history_loaded_once:
            self.load_ga4_data()

    def _analytics_realtime_tick(self) -> None:
        analytics_page = self.pages.get("analytics")
        if (
            analytics_page is not None
            and self.stack.currentWidget() is analytics_page
            and self.ga4_auto_refresh.isChecked()
            and self._ga4_read_ready()
        ):
            self.load_ga4_realtime_data()

    def _refresh_ga4_status(self) -> None:
        measurement_id = load_ga4_measurement_id(self.site.root)
        property_id = load_ga4_property_id(self.site.root)
        credentials_ready = ga4_credentials_path(self.site.root).is_file()
        if measurement_id and property_id and credentials_ready:
            self.ga4_status.setText(
                f"接続済み　送信 {measurement_id} / 読み取りプロパティ {property_id} / "
                f"管理者ローカル計測 {'接続済み' if collector_available() else '停止中'}"
            )
            return
        missing = []
        if not measurement_id:
            missing.append("測定ID")
        if not property_id:
            missing.append("プロパティID")
        if not credentials_ready:
            missing.append("読み取りJSON")
        message = "未接続: " + "・".join(missing)
        self.ga4_status.setText(message)
        if hasattr(self, "ga4_audience_views"):
            for view in self.ga4_audience_views.values():
                if not self.ga4_reports.get("realtime"):
                    view["live_status"].setText(message)
                if not self.ga4_reports.get("historical"):
                    view["report_status"].setText(message)

    def choose_ga4_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "GA4読み取り用サービスアカウントJSONを選択", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            save_ga4_credentials(self.site.root, Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "GA4読み取り設定", str(exc))
            return
        self._refresh_ga4_status()
        if self.ga4_property_id_input.text().strip():
            self.save_ga4_read_settings()

    def save_ga4_read_settings(self) -> None:
        try:
            save_ga4_property_id(self.site.root, self.ga4_property_id_input.text())
        except ValueError as exc:
            QMessageBox.warning(self, "GA4読み取り設定", str(exc))
            return
        self.ga4_history_loaded_once = False
        self.ga4_reports.clear()
        self._refresh_ga4_status()
        if self._ga4_read_ready():
            self.load_ga4_realtime_data()
            self.load_ga4_data()

    def load_ga4_data(self) -> None:
        if self.analytics_worker is not None:
            return
        if not self._ga4_read_ready():
            self._refresh_ga4_status()
            return
        days = int(self.ga4_period_days.currentData() or 7)
        self.analytics_worker = AnalyticsWorker(self.site.root, "historical", days)
        self.ga4_history_button.setEnabled(False)
        for view in self.ga4_audience_views.values():
            view["report_status"].setText(f"GA4から過去{days}日間を取得しています…")
        self.analytics_worker.signals.completed.connect(self._ga4_loaded)
        self.analytics_worker.signals.failed.connect(self._ga4_failed)
        self.thread_pool.start(self.analytics_worker)

    def load_ga4_realtime_data(self) -> None:
        if self.analytics_realtime_worker is not None:
            return
        if not self._ga4_read_ready():
            self._refresh_ga4_status()
            return
        self.analytics_realtime_worker = AnalyticsWorker(self.site.root, "realtime")
        self.ga4_realtime_button.setEnabled(False)
        for view in self.ga4_audience_views.values():
            view["live_status"].setText("GA4から直近30分を取得しています…")
        self.analytics_realtime_worker.signals.completed.connect(self._ga4_realtime_loaded)
        self.analytics_realtime_worker.signals.failed.connect(self._ga4_realtime_failed)
        self.thread_pool.start(self.analytics_realtime_worker)

    @staticmethod
    def _analytics_event_labels(rows: object) -> list[dict]:
        labels = {
            "article_view": "記事閲覧",
            "pr_impression": "PR表示",
            "pr_click": "PRクリック",
        }
        result: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["eventName"] = labels.get(str(item.get("eventName") or ""), str(item.get("eventName") or ""))
            result.append(item)
        return result

    def _load_ga4_cached_data(self) -> None:
        cache = load_ga4_cache(self.site.root)
        for mode in ("realtime", "historical"):
            report = cache.get(mode)
            if not isinstance(report, dict):
                continue
            self.ga4_reports[mode] = report
            if mode == "realtime":
                self._render_ga4_realtime(report, cached=True)
            else:
                self.ga4_history_loaded_once = True
                try:
                    sync_ga4_performance(
                        self.site.root,
                        report,
                        audience="external",
                    )
                except Exception:
                    traceback.print_exc()
                self._render_ga4_report(report, cached=True)

    def _render_ga4_realtime(self, data: dict, *, cached: bool = False) -> None:
        stamp = self._ga4_time_label(data.get("generated_at"))
        source = "前回の正常値" if cached else "取得成功"
        for audience, view in self.ga4_audience_views.items():
            report = data.get(audience) if isinstance(data.get(audience), dict) else {}
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            for key in ("activeUsers", "pageViews", "prImpressions", "prClicks"):
                view["live_metrics"][key].setText(f"{int(summary.get(key) or 0):,}")
            self._fill_analytics_table(
                view["live_tables"]["pages"], report.get("pages"),
                ["unifiedScreenName", "eventCount", "activeUsers"],
                {"eventCount", "activeUsers"}, set(),
            )
            self._fill_analytics_table(
                view["live_tables"]["events"], self._analytics_event_labels(report.get("events")),
                ["eventName", "eventCount"], {"eventCount"}, set(),
            )
            self._fill_analytics_table(
                view["live_tables"]["minutes"], report.get("minutes"),
                ["minutesAgo", "pageViews", "prImpressions", "prClicks"],
                {"minutesAgo", "pageViews", "prImpressions", "prClicks"}, set(),
            )
            view["live_status"].setText(
                f"{source} {stamp} / 直近30分 / 自動更新は30秒ごと"
            )

    def _render_ga4_report(self, data: dict, *, cached: bool = False) -> None:
        stamp = self._ga4_time_label(data.get("generated_at"))
        source = "前回の正常値" if cached else "取得成功"
        for audience, view in self.ga4_audience_views.items():
            report = data.get(audience) if isinstance(data.get(audience), dict) else {}
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            for key in ("pageViews", "activeUsers", "prImpressions", "prClicks"):
                view["report_metrics"][key].setText(f"{int(summary.get(key) or 0):,}")
            for key in ("clickRate", "prCtr"):
                view["report_metrics"][key].setText(f"{float(summary.get(key) or 0):.2f}%")
            tables = view["report_tables"]
            self._fill_analytics_table(
                tables["articles"], report.get("articles"),
                ["pagePath", "pageTitle", "eventCount", "activeUsers", "prImpressions", "prClicks", "clickRate"],
                {"eventCount", "activeUsers", "prImpressions", "prClicks"}, {"clickRate"},
            )
            self._fill_analytics_table(
                tables["events"], self._analytics_event_labels(report.get("events")),
                ["eventName", "eventCount", "totalUsers"], {"eventCount", "totalUsers"}, set(),
            )
            self._fill_analytics_table(
                tables["devices"], report.get("devices"),
                ["deviceCategory", "operatingSystem", "browser", "eventCount", "activeUsers"],
                {"eventCount", "activeUsers"}, set(),
            )
            self._fill_analytics_table(
                tables["referrers"], report.get("referrers"),
                ["sessionSource", "sessionMedium", "sessions", "activeUsers", "eventCount"],
                {"sessions", "activeUsers", "eventCount"}, set(),
            )
            self._fill_analytics_table(
                tables["genres"], report.get("genres"),
                ["contentGroup", "eventCount", "activeUsers"], {"eventCount", "activeUsers"}, set(),
            )
            self._fill_analytics_table(
                tables["daily"], report.get("daily"),
                ["date", "eventCount", "activeUsers"], {"eventCount", "activeUsers"}, set(),
            )
            view["report_status"].setText(
                f"{source} {stamp} / {data.get('start_date')}〜{data.get('end_date')}"
            )

    def _ga4_realtime_loaded(self, data: dict) -> None:
        self.analytics_realtime_worker = None
        self.ga4_realtime_button.setEnabled(True)
        self.ga4_reports["realtime"] = data
        self._render_ga4_realtime(data)

    def _ga4_realtime_failed(self, message: str) -> None:
        self.analytics_realtime_worker = None
        self.ga4_realtime_button.setEnabled(True)
        for view in self.ga4_audience_views.values():
            prefix = "前回の正常値を表示中。" if self.ga4_reports.get("realtime") else "表示できる正常値がありません。"
            view["live_status"].setText(f"{prefix} 更新失敗: {message}")

    def _ga4_loaded(self, data: dict) -> None:
        self.analytics_worker = None
        self.ga4_history_button.setEnabled(True)
        self.ga4_history_loaded_once = True
        self.ga4_reports["historical"] = data
        try:
            sync_ga4_performance(
                self.site.root,
                data,
                audience="external",
            )
        except Exception:
            traceback.print_exc()
        self._render_ga4_report(data)

    def _ga4_failed(self, message: str) -> None:
        self.analytics_worker = None
        self.ga4_history_button.setEnabled(True)
        for view in self.ga4_audience_views.values():
            prefix = "前回の正常値を表示中。" if self.ga4_reports.get("historical") else "表示できる正常値がありません。"
            view["report_status"].setText(f"{prefix} 更新失敗: {message}")

    def save_ga4_settings(self) -> None:
        try:
            save_ga4_measurement_id(self.site.root, self.ga4_measurement_id_input.text())
            ensure_ga4_owner_identity(self.site.root, self.site.public_url)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "GA4設定を確認", str(exc))
            return
        progress = QProgressDialog("計測コードを公開しています…", "", 0, 0, self)
        progress.setWindowTitle("アクセス解析")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        try:
            publish_ga4_config(self.site.root, self.site)
        except Exception as exc:
            QMessageBox.warning(self, "計測コードを公開できません", str(exc))
            return
        finally:
            progress.close()
        self._refresh_ga4_status()
        QMessageBox.information(
            self,
            "計測コードを公開しました",
            "公開サイトへ反映しました。ChromeとEdgeはそれぞれ一度だけ登録してください。",
        )

    @staticmethod
    def _browser_program(browser: str) -> Path | None:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        program_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        candidates = (
            [
                local / "Google/Chrome/Application/chrome.exe",
                program_files / "Google/Chrome/Application/chrome.exe",
                program_x86 / "Google/Chrome/Application/chrome.exe",
            ]
            if browser == "chrome"
            else [
                program_x86 / "Microsoft/Edge/Application/msedge.exe",
                program_files / "Microsoft/Edge/Application/msedge.exe",
                local / "Microsoft/Edge/Application/msedge.exe",
            ]
        )
        return next((path for path in candidates if path.is_file()), None)

    def register_owner_browser(self, browser: str) -> None:
        if not collector_available():
            self._start_owner_collector()
        if not collector_available():
            QMessageBox.warning(
                self,
                "管理者計測を開始できません",
                "ローカル計測サーバーを開始できませんでした。記事編集室を再起動してから登録してください。",
            )
            return
        url = owner_registration_url(self.site.root, self.site.public_url)
        program = self._browser_program(browser)
        try:
            if program:
                subprocess.Popen([str(program), url])
            else:
                QDesktopServices.openUrl(QUrl(url))
        except OSError as exc:
            QMessageBox.warning(self, "ブラウザを開けません", str(exc))
            return
        name = "Chrome" if browser == "chrome" else "Edge"
        QMessageBox.information(
            self,
            f"{name}を開きました",
            "公開サイトに『このブラウザを管理者として登録しました』と表示されれば完了です。以後は外部アクセスから除外され、自分を含む画面に即時反映されます。",
        )

    def refresh_all(self) -> None:
        self._refresh_site_controls()
        self._refresh_chatgpt_queue_status()
        drafts = list_drafts(self.site.root)
        self._refresh_metrics(drafts)
        self._refresh_affiliate_recommendations(drafts)
        self._refresh_drafts(drafts)
        self._refresh_rights(drafts)
        self._refresh_publishing(drafts)
        self._refresh_editor_selector(drafts)
        self._refresh_sites()
        self._refresh_outreach()
        self._refresh_sources()
        self._refresh_candidates()
        self._refresh_review_board(drafts)
        self._refresh_site_learning()
        self._refresh_sitemap_health_panel()
        if hasattr(self, "x_posts_table"):
            self._refresh_x_posts()
        self.workspace_path.setText(f"現在の作業フォルダ: {self.site.root}")
        logo_path = self.site.root / "assets" / "common" / "indanya-logo.png"
        if logo_path.is_file():
            self.logo.setPixmap(QPixmap(str(logo_path)).scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    @staticmethod
    def _clear_layout(layout: QVBoxLayout | QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                MainWindow._clear_layout(child_layout)

    def _affiliate_recommendation_row(
        self,
        item: dict,
        *,
        include_article_count: bool,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.addWidget(QLabel(
            f"{item.get('program_name', '広告サービス')}・登録推奨",
            objectName="warningTitle",
        ))
        product_code = str(item.get("product_code") or "")
        exact_count = int(item.get("exact_product_count") or bool(product_code))
        article_count = int(item.get("article_count") or 1)
        if include_article_count:
            detail = (
                f"{article_count}記事で広告機会を検出（正確な作品 {exact_count}件）。"
                "未登録中も公式ページで案内し、提携後に同じ作品の収益リンクへ差し替えます。"
            )
        else:
            detail = (
                f"正確な作品番号 {product_code} を検出しました。"
                if product_code else "MGS作品への導線を検出しました。"
            )
            detail += "公式ページを記事に掲載中です。提携後は同じ枠を収益リンクへ差し替えられます。"
        note = QLabel(detail, objectName="muted")
        note.setWordWrap(True)
        copy.addWidget(note)
        row.addLayout(copy, 1)
        register_button = button("登録ページを開く", "primary")
        register_url = str(item.get("registration_url") or "")
        register_button.clicked.connect(
            lambda _checked=False, url=register_url: QDesktopServices.openUrl(QUrl(url))
        )
        row.addWidget(register_button)
        product_url = str(item.get("product_url") or "")
        if product_url:
            product_button = button("対象商品を確認")
            product_button.clicked.connect(
                lambda _checked=False, url=product_url: QDesktopServices.openUrl(QUrl(url))
            )
            row.addWidget(product_button)
        return row

    def _refresh_affiliate_recommendations(self, drafts: list[dict]) -> None:
        if not hasattr(self, "affiliate_recommendation_layout"):
            return
        self._clear_layout(self.affiliate_recommendation_layout)
        recommendations = registration_recommendations(drafts)
        if not recommendations:
            self.affiliate_recommendation_panel.hide()
            return
        for item in recommendations:
            self.affiliate_recommendation_layout.addLayout(
                self._affiliate_recommendation_row(
                    item,
                    include_article_count=True,
                )
            )
        self.affiliate_recommendation_panel.show()

    def _refresh_editor_affiliate_recommendation(self, payload: dict) -> None:
        if not hasattr(self, "editor_affiliate_layout"):
            return
        self._clear_layout(self.editor_affiliate_layout)
        opportunities = normalize_affiliate_opportunities(
            payload.get("affiliate_opportunities")
        )
        if not opportunities:
            self.editor_affiliate_panel.hide()
            return
        for item in opportunities:
            self.editor_affiliate_layout.addLayout(
                self._affiliate_recommendation_row(
                    item,
                    include_article_count=False,
                )
            )
        self.editor_affiliate_panel.show()

    def _refresh_chatgpt_queue_status(self) -> None:
        count = pending_chatgpt_count(self.site.root)
        batch = latest_chatgpt_batch_summary(self.site.root)
        if hasattr(self, "chatgpt_queue_label"):
            sending = self.chatgpt_send_worker is not None
            queue_state = (
                "素材回収・記事作成中"
                if sending
                else "次の巡回を待機"
            )
            self.chatgpt_queue_label.setText(
                f"現在の処理 {count}件　"
                f"{queue_state}"
            )
            self.open_chatgpt_button.setEnabled(count > 0 and not sending)
            self.open_chatgpt_button.setText(
                "記事を自動処理中" if sending else "現在の記事を処理"
            )
            self.chatgpt_login_button.setText("Codexアプリ認証を使用")
            self.chatgpt_login_button.setEnabled(False)
        if hasattr(self, "automation_batch_note"):
            total = batch["total"]
            settings = load_automation_settings(self.site.root)
            target = 1
            self.automation_batch_label.setText(f"{count}件")
            self.automation_pending_label.setText(f"{target}件")
            if total:
                self.automation_batch_note.setText(
                    f"直近の処理：{total}件中 {batch['completed']}件完成　"
                    f"失敗 {batch['failed']}件　 対象外 {batch['skipped']}件"
                )
            else:
                self.automation_batch_note.setText("まだ記事作成実績はありません。")
            self.automation_stage_label.setText(self._automation_stage(count))
            self._refresh_automation_roadmap(count, batch)
            self._refresh_automation_completed_articles()
            self._refresh_automation_activity()
            self._refresh_site_learning()

    def _refresh_site_learning(self) -> None:
        if not hasattr(self, "site_learning_table"):
            return
        strategies = {
            "fanza_official": "公式商品テンプレ",
            "browser_full": "完全取得",
            "semantic_trial": "高速取得を試す",
            "semantic_fast": "高速テンプレ",
            "semantic_fallback": "軽量取得",
            "browser_plus_semantic_recovery": "完全取得＋不足補完",
        }
        rows = list_site_learning(self.site.root)[:30]
        table = self.site_learning_table
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            average = float(row.get("average_seconds") or 0)
            values = (
                str(row.get("host") or ""),
                str(row.get("maturity") or "未学習"),
                (
                    f"{int(row.get('successes') or 0)} / {int(row.get('failures') or 0)}"
                    + (
                        f"（既存{int(row.get('historical_successes') or 0)}）"
                        if int(row.get("historical_successes") or 0)
                        else ""
                    )
                ),
                (
                    f"{float(row.get('success_rate') or 0):.1f}%"
                    if int(row.get("successes") or 0) + int(row.get("failures") or 0)
                    else "--"
                ),
                strategies.get(str(row.get("strategy") or ""), str(row.get("strategy") or "完全取得")),
                f"{average:.0f}秒" if average else "--",
                str(row.get("last_error") or "なし"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 6:
                    item.setToolTip(value)
                table.setItem(row_index, column, item)
        table.resizeRowsToContents()

    def _automation_stage(self, pending_count: int) -> str:
        if self.collect_worker is not None:
            return "候補を選別中" if self.automation_phase_index == 1 else "候補URLを収集中"
        if self.chatgpt_send_worker is None:
            settings = load_automation_settings(self.site.root)
            rate_limited = bool(settings.get("continuous_rate_limit_retry_until"))
            retry_label = self._continuous_retry_label(
                self._continuous_retry_value(settings)
            )
            if retry_label:
                reason = "利用制限の解除確認" if rate_limited else "候補の再確認"
                return f"{reason}を待機（{retry_label}）"
            return "現在の記事を開始待ち" if pending_count else "次の巡回を待機"
        labels = (
            "送信用ブラウザを準備中",
            "商品情報と画像を確認中",
            "記事を作成中",
            "主役と公式リンクを照合中",
            "記事を検査・保存中",
        )
        return labels[max(0, min(4, self.automation_phase_index - 3))]

    def _build_automation_roadmap(self) -> QHBoxLayout:
        roadmap = QHBoxLayout()
        roadmap.setContentsMargins(0, 6, 0, 4)
        roadmap.setSpacing(8)
        steps = (
            ("候補収集", "情報源からURLを拾う"),
            ("候補選別", "重複・対象外を外す"),
            ("作成待機", "URLを作成キューへ入れる"),
            ("ブラウザ準備", "送信用Chromeを起動"),
            ("素材解析", "本文・画像・動画を確認"),
            ("記事生成", "タイトルとレスを作る"),
            ("本人照合", "主役と公式リンクを確認"),
            ("検査・保存", "公開待機へ追加"),
        )
        self.automation_roadmap_widgets = []
        for index, (title, detail) in enumerate(steps):
            frame = QFrame(objectName="roadmapStep")
            box = QVBoxLayout(frame)
            box.setContentsMargins(10, 8, 10, 8)
            box.setSpacing(3)
            number = QLabel(f"{index + 1:02}", objectName="roadmapIndex")
            title_label = QLabel(title, objectName="roadmapTitle")
            detail_label = QLabel(detail, objectName="roadmapDetail")
            detail_label.setWordWrap(True)
            box.addWidget(number)
            box.addWidget(title_label)
            box.addWidget(detail_label)
            roadmap.addWidget(frame, 1)
            if index < len(steps) - 1:
                arrow = QLabel("→")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setStyleSheet("color: #687078; font-size: 18px; font-weight: 900;")
                roadmap.addWidget(arrow)
            self.automation_roadmap_widgets.append(
                {
                    "frame": frame,
                    "number": number,
                    "title": title_label,
                    "detail": detail_label,
                }
            )
        self._paint_automation_roadmap(-1, -1)
        return roadmap

    def _automation_roadmap_position(self, pending_count: int, batch: dict) -> tuple[int, int]:
        del batch
        if self.collect_worker is not None:
            phase = max(0, min(1, self.automation_phase_index))
            return (phase, phase)
        if self.chatgpt_send_worker is not None:
            phase = max(3, min(7, self.automation_phase_index))
            return (phase, phase)
        if pending_count:
            return (2, 2)
        return (-1, -1)

    def _refresh_automation_roadmap(self, pending_count: int, batch: dict) -> None:
        active_index, completed_until = self._automation_roadmap_position(
            pending_count, batch
        )
        self._paint_automation_roadmap(active_index, completed_until)

    @staticmethod
    def _chatgpt_phase_for_message(message: str) -> int:
        value = str(message)
        if any(term in value for term in ("検査", "保存", "公開前ボード")):
            return 7
        if any(term in value for term in ("公式アカウント", "本人照合", "人物名簿")):
            return 6
        if any(term in value for term in ("記事", "推敲", "生成", "タイトル", "レス")):
            return 5
        if any(term in value for term in ("素材", "画像", "動画", "商品", "本文", "解析", "ページ")):
            return 4
        return 3

    def _set_automation_phase(
        self,
        phase: int,
        message: str,
        *,
        progress: int | None = None,
        reset: bool = False,
    ) -> None:
        phase = max(0, min(7, int(phase)))
        phase_starts = (3, 15, 27, 39, 51, 64, 78, 91)
        phase_ends = (13, 25, 37, 49, 62, 76, 89, 100)
        if reset:
            self.automation_phase_index = phase
            self.automation_progress_value = phase_starts[phase]
        elif phase >= self.automation_phase_index:
            self.automation_phase_index = phase
            self.automation_progress_value = max(
                self.automation_progress_value, phase_starts[phase]
            )
        if progress is not None:
            scaled = phase_starts[phase] + round(
                (phase_ends[phase] - phase_starts[phase])
                * max(0, min(100, int(progress))) / 100
            )
            self.automation_progress_value = max(self.automation_progress_value, scaled)
        if hasattr(self, "auto_progress"):
            self.auto_progress.setValue(self.automation_progress_value)
        if hasattr(self, "auto_note"):
            self.auto_note.setText(message)
        self._refresh_automation_roadmap_from_current()

    def _refresh_automation_roadmap_from_current(self) -> None:
        if not hasattr(self, "automation_roadmap_widgets"):
            return
        try:
            pending_count = pending_chatgpt_count(self.site.root)
            batch = latest_chatgpt_batch_summary(self.site.root)
        except OSError:
            pending_count = 0
            batch = {"total": 0, "processed": 0}
        self._refresh_automation_roadmap(pending_count, batch)

    def _paint_automation_roadmap(self, active_index: int, completed_until: int) -> None:
        for index, widgets in enumerate(self.automation_roadmap_widgets):
            frame = widgets["frame"]
            number = widgets["number"]
            title = widgets["title"]
            detail = widgets["detail"]
            if index < completed_until:
                frame.setStyleSheet(
                    "QFrame#roadmapStep { background: #e5f4f2; border: 1px solid #9bc9c4; border-radius: 4px; }"
                )
                number.setText("DONE")
                number.setStyleSheet("color: #137f78; font-size: 10px; font-weight: 900;")
                title.setStyleSheet("color: #17191c; font-size: 12px; font-weight: 900;")
                detail.setStyleSheet("color: #4f6865; font-size: 10px;")
            elif index == active_index:
                frame.setStyleSheet(
                    "QFrame#roadmapStep { background: #181a1d; border: 1px solid #181a1d; border-radius: 4px; }"
                )
                number.setText("NOW")
                number.setStyleSheet("color: #f0c34d; font-size: 10px; font-weight: 900;")
                title.setStyleSheet("color: white; font-size: 12px; font-weight: 900;")
                detail.setStyleSheet("color: #c9ced2; font-size: 10px;")
            else:
                frame.setStyleSheet(
                    "QFrame#roadmapStep { background: #f7f8f8; border: 1px solid #d8dde0; border-radius: 4px; }"
                )
                number.setText(f"{index + 1:02}")
                number.setStyleSheet("color: #687078; font-size: 10px; font-weight: 900;")
                title.setStyleSheet("color: #17191c; font-size: 12px; font-weight: 900;")
                detail.setStyleSheet("color: #687078; font-size: 10px;")

    def _refresh_automation_activity(self) -> None:
        if not hasattr(self, "automation_activity_table"):
            return
        phase_labels = {
            "completed": "完成",
            "failed": "失敗",
            "stopped": "停止",
            "skipped": "対象外",
        }
        table = self.automation_activity_table
        rows = [
            event for event in recent_chatgpt_activity(self.site.root, limit=120)
            if str(event.get("phase") or "") in phase_labels
        ][:40]
        table.setRowCount(len(rows))
        for row_index, event in enumerate(rows):
            occurred_at = self._automation_completed_time(str(event.get("at") or ""))
            url = str(event.get("url") or "")
            phase = phase_labels.get(str(event.get("phase") or ""), str(event.get("phase") or ""))
            message = str(event.get("message") or "")
            for column, value in enumerate((occurred_at, url, phase, message)):
                item = QTableWidgetItem(value)
                if column in {1, 3}:
                    item.setToolTip(value)
                table.setItem(row_index, column, item)
        table.resizeRowsToContents()

    def _refresh_automation_completed_articles(self) -> None:
        if not hasattr(self, "automation_completed_table"):
            return
        terminal_statuses = {
            "completed", "failed", "stopped_stale", "skipped_non_adult", "archived_duplicate",
        }
        rows = [
            item for item in reconcile_chatgpt_requests(self.site.root)
            if item.get("status") in terminal_statuses
            and isinstance(item.get("options"), dict)
            and item["options"].get("automation_origin") == "crawl"
        ]
        rows.sort(key=lambda item: str(item.get("completed_at") or ""), reverse=True)
        # Show only the latest terminal outcome for each source URL.
        unique_rows: list[dict[str, Any]] = []
        shown_urls: set[str] = set()
        for item in rows:
            source_url = str(item.get("url") or "")
            if not source_url or source_url in shown_urls:
                continue
            shown_urls.add(source_url)
            unique_rows.append(item)
        rows = unique_rows
        rows = rows[:50]
        signature = ((str(self.site.root), ""),) + tuple(
            (
                str(item.get("request_id") or ""),
                str(item.get("status") or ""),
                str(item.get("completed_at") or ""),
                str(item.get("last_error") or ""),
            )
            for item in rows
        )
        if signature == self.automation_completed_signature:
            return
        self.automation_completed_signature = signature
        drafts = {str(item.get("slug") or ""): item for item in list_drafts(self.site.root)}
        table = self.automation_completed_table
        table.setRowCount(len(rows))
        for row_index, request in enumerate(rows):
            slug = str(request.get("draft_slug") or "")
            draft = drafts.get(slug, {})
            title = str(draft.get("title") or request.get("url") or "記事")
            completed_at = self._automation_completed_time(
                str(request.get("completed_at") or "")
            )
            status = str(request.get("status") or "")
            result, detail = {
                "completed": ("成功", "公開待機へ追加済み"),
                "failed": ("失敗", str(request.get("last_error") or "処理に失敗しました")),
                "stopped_stale": ("停止", str(request.get("last_error") or "新しい候補を優先しました")),
                "skipped_non_adult": ("対象外", str(request.get("last_error") or "対象外として停止しました")),
                "archived_duplicate": ("重複", str(request.get("last_error") or "既存記事のため新規作成していません")),
            }.get(status, (status, str(request.get("last_error") or "")))
            values = (completed_at, title, result, detail)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, slug)
                if column in {1, 3}:
                    item.setToolTip(value)
                table.setItem(row_index, column, item)
        table.resizeRowsToContents()

    @staticmethod
    def _automation_completed_time(value: str) -> str:
        try:
            completed = datetime.fromisoformat(value)
            now = datetime.now(completed.tzinfo)
            if completed.date() == now.date():
                return completed.strftime("今日 %H:%M:%S")
            return completed.strftime("%m/%d %H:%M:%S")
        except ValueError:
            return value or "--:--"

    def _open_completed_automation_article(self, row: int, _column: int) -> None:
        item = self.automation_completed_table.item(row, 1)
        slug = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if slug:
            self.edit_publish_article(slug)

    def _board_thumbnail_url(self, slug: str, payload: dict) -> str:
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        thumbnail_id = str(payload.get("thumbnail_id") or "")
        selected = next(
            (item for item in images if isinstance(item, dict) and str(item.get("id")) == thumbnail_id),
            None,
        )
        if selected is None:
            selected = next((item for item in images if isinstance(item, dict)), None)
        data_url = str(selected.get("data_url") or "") if selected else ""
        if not data_url:
            videos = payload.get("videos") if isinstance(payload.get("videos"), list) else []
            data_url = next(
                (
                    str(item.get("poster_data_url") or "")
                    for item in videos
                    if isinstance(item, dict) and item.get("poster_data_url")
                ),
                "",
            )
        match = re.fullmatch(r"data:image/(jpeg|png|webp|gif);base64,([A-Za-z0-9+/=]+)", data_url)
        if not match:
            return ""
        suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}[match.group(1)]
        digest = hashlib.sha256(match.group(2).encode("ascii")).hexdigest()[:12]
        destination = self.site.root / ".article-studio" / "board-thumbs" / f"{slug}-{digest}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file():
                destination.write_bytes(base64.b64decode(match.group(2), validate=True))
            return QUrl.fromLocalFile(str(destination.resolve())).toString()
        except (OSError, ValueError):
            return ""

    def _restore_review_scroll(self, loaded: bool) -> None:
        if not loaded or not hasattr(self, "review_view"):
            return
        scroll_y = max(0, int(self.review_scroll_y))
        outer_scroll_y = max(0, int(self.review_outer_scroll_y))

        def restore() -> None:
            self.review_view.page().runJavaScript(
                f"window.scrollTo({{top:{scroll_y},left:0,behavior:'instant'}});"
            )
            if hasattr(self, "dashboard_scroll"):
                self.dashboard_scroll.verticalScrollBar().setValue(outer_scroll_y)

        QTimer.singleShot(0, restore)

    def _refresh_review_board(self, drafts: list[dict] | None = None) -> None:
        if not hasattr(self, "review_view"):
            return
        try:
            self.review_scroll_y = max(0, int(self.review_page.scrollPosition().y()))
        except (AttributeError, TypeError, ValueError):
            pass
        if hasattr(self, "dashboard_scroll"):
            self.review_outer_scroll_y = self.dashboard_scroll.verticalScrollBar().value()
        drafts = drafts if isinstance(drafts, list) else list_drafts(self.site.root)
        positions = queue_position_map(self.site.root)
        selected_filter = str(self.review_filter.currentData() or "unreviewed")
        selected_sort = str(self.review_sort.currentData() or "newest")
        records = []
        self.review_status_cache = {}
        for draft in drafts:
            try:
                payload = self._draft_payload(str(draft["slug"]))
            except Exception:
                continue
            status = str(payload.get("review_status") or "unreviewed")
            if draft["slug"] in self.review_publish_progress:
                status = "publishing"
            elif any(
                action == "publish" and queued_slug == draft["slug"]
                for action, queued_slug in self.publish_queue
            ):
                status = "publish_waiting"
            elif payload.get("published_url") or str(payload.get("editorial_status") or "") == "published":
                status = "published"
            elif draft["slug"] in positions:
                status = "queued"
            if status not in {"unreviewed", "queued", "publish_waiting", "publishing", "published", "deleted", "failed"}:
                status = "unreviewed"
            self.review_status_cache[str(draft["slug"])] = status
            if selected_filter != "all" and status != selected_filter:
                continue
            article_order_at = str(
                payload.get("generated_at")
                or payload.get("created_at")
                or payload.get("published_at")
                or draft["updated_at"]
            )
            records.append((draft, payload, status, article_order_at))
        if selected_sort == "oldest":
            records.sort(key=lambda item: item[3])
        elif selected_sort == "queue":
            records.sort(key=lambda item: (positions.get(item[0]["slug"], 1_000_000), item[3]))
        else:
            records.sort(key=lambda item: item[3], reverse=True)

        labels = {
            "queued": "予約待機",
            "publishing": "サイトへ反映中",
            "publish_waiting": "公開処理待ち",
            "published": "公開済み",
            "deleted": "消去済み",
            "failed": "公開失敗",
        }
        cards = []
        for draft, payload, status, _article_order_at in records:
            slug = str(draft["slug"])
            title = html.escape(str(payload.get("title") or slug))
            summary = html.escape(str(payload.get("summary") or ""))
            category = html.escape(str(payload.get("category") or "話題"))
            tags = "".join(
                f"<span>#{html.escape(str(tag))}</span>"
                for tag in (payload.get("tags") or [])[:6]
                if isinstance(tag, str)
            )
            thumb = html.escape(self._board_thumbnail_url(slug, payload), quote=True)
            media = f'<img src="{thumb}" alt="">' if thumb else '<div class="no-image">NO IMAGE</div>'
            progress = self.review_publish_progress.get(slug)
            progress_markup = (
                f'<div class="publish-progress"><i style="width:{max(2, min(100, progress))}%"></i>'
                f'<span>サイトへ反映中 {progress}%</span></div>'
                if progress is not None else ""
            )
            overlay = ""
            if status != "unreviewed":
                label = labels[status]
                if status == "queued":
                    label += f" #{positions.get(slug, 0)}"
                overlay = f'<div class="status-overlay {status}">{label}</div>'
            actions = []
            if status in {"unreviewed", "failed"}:
                actions.extend([
                    ("publish", "すぐ公開", "primary"),
                    ("queue", "予約待機へ", "queue"),
                    ("delete", "消去", "danger"),
                ])
            elif status == "queued":
                actions.extend([
                    ("publish", "すぐ公開", "primary"),
                    ("dequeue", "待機から外す", "queue"),
                    ("delete", "消去", "danger"),
                ])
            elif status == "published":
                actions.append(("open", "公開記事を開く", "primary"))
            elif status == "deleted":
                actions.append(("restore", "未判別へ戻す", "queue"))
            if status != "publishing":
                actions.append(("edit", "編集", "plain"))
            action_markup = "".join(
                f'<a class="{kind}" href="indanya-action://{action}/{slug}">{label}</a>'
                for action, label, kind in actions
            )
            cards.append(f"""
<article class="card" data-slug="{html.escape(slug, quote=True)}">
  <a class="media" href="indanya-action://edit/{slug}">{media}{overlay}</a>
  {progress_markup}
  <div class="copy">
    <div class="meta">{category}　{html.escape(draft["updated_at"][:10])}　画像{draft["image_count"]} / 動画{draft["video_count"]}</div>
    <h2>{title}</h2><p>{summary}</p><div class="tags">{tags}</div>
  </div>
  <div class="actions">{action_markup}</div>
</article>""")
        empty = '<div class="empty">この状態の記事はありません。</div>' if not cards else ""
        document = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;padding:18px;background:#f2f0ea;color:#211f1a;font-family:"Yu Gothic UI",Meiryo,sans-serif}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;max-width:1180px;margin:auto}}
.card{{position:relative;display:flex;flex-direction:column;background:#fff;border:1px solid #c9c6bd;min-width:0}}
.media{{position:relative;display:block;aspect-ratio:16/10;overflow:hidden;background:#171717}}
.media img{{width:100%;height:100%;display:block;object-fit:cover}}.no-image{{height:100%;display:grid;place-items:center;color:#888}}
.status-overlay{{position:absolute;inset:0;display:grid;place-items:center;background:rgba(35,35,35,.67);color:#fff;font-size:26px;font-weight:900}}
.status-overlay.published,.status-overlay.publishing{{background:rgba(21,94,73,.72)}}.status-overlay.publish_waiting{{background:rgba(31,80,111,.72)}}.status-overlay.queued{{background:rgba(120,91,24,.72)}}.status-overlay.deleted{{background:rgba(50,50,50,.76)}}.status-overlay.failed{{background:rgba(156,36,27,.74)}}
.publish-progress{{position:relative;height:24px;background:#e5e7e5;overflow:hidden}}.publish-progress i{{position:absolute;inset:0 auto 0 0;background:#168a78;transition:width .2s}}.publish-progress span{{position:relative;z-index:1;display:block;line-height:24px;text-align:center;color:#fff;font-size:10px;font-weight:800;text-shadow:0 1px 2px #444}}
.copy{{padding:13px 14px 8px;flex:1}}.meta{{color:#77736a;font-size:10px}}h2{{margin:7px 0;font-size:17px;line-height:1.5;letter-spacing:0}}p{{margin:0;color:#5e5a52;font-size:11px;line-height:1.65}}
.tags{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}}.tags span{{padding:3px 6px;border:1px solid #d2cfc6;background:#f4f2ec;font-size:9px}}
.actions{{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px 14px;border-top:1px solid #e3e0d8}}.actions a{{padding:7px 10px;border:1px solid #aaa69d;color:#222;text-decoration:none;font-size:11px;font-weight:800}}
.actions .primary{{background:#181a1d;color:#fff;border-color:#181a1d}}.actions .queue{{background:#e7f2ef;color:#126e68;border-color:#9bc9c4}}.actions .danger{{margin-left:auto;background:#fff;color:#b0251d;border-color:#d7aaa6}}
.empty{{padding:80px 20px;background:#fff;border:1px solid #c9c6bd;text-align:center;color:#777}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class="grid">{''.join(cards)}{empty}</main></body></html>"""
        self.review_queue_count = len(positions)
        self.review_queue_label.setText(f"予約待機 {self.review_queue_count}件")
        self.review_view.setHtml(document, QUrl.fromLocalFile(str(self.site.root.resolve()) + os.sep))

    def _review_action_markup(self, slug: str, status: str) -> str:
        actions: list[tuple[str, str, str]] = []
        if status in {"unreviewed", "failed"}:
            actions.extend([
                ("publish", "すぐ公開", "primary"),
                ("queue", "予約待機へ", "queue"),
                ("delete", "消去", "danger"),
            ])
        elif status == "queued":
            actions.extend([
                ("publish", "すぐ公開", "primary"),
                ("dequeue", "待機から外す", "queue"),
                ("delete", "消去", "danger"),
            ])
        elif status == "published":
            actions.append(("open", "公開記事を開く", "primary"))
        elif status == "deleted":
            actions.append(("restore", "未判別へ戻す", "queue"))
        if status != "publishing":
            actions.append(("edit", "編集", "plain"))
        return "".join(
            f'<a class="{kind}" href="indanya-action://{action}/{slug}">{label}</a>'
            for action, label, kind in actions
        )

    def _set_review_card_status(
        self,
        slug: str,
        status: str,
        *,
        position: int = 0,
    ) -> None:
        self.review_status_cache[slug] = status
        labels = {
            "queued": f"予約待機 #{position}" if position else "予約待機",
            "publishing": "サイトへ反映中",
            "publish_waiting": "公開処理待ち",
            "published": "公開済み",
            "deleted": "消去済み",
            "failed": "公開失敗",
        }
        overlay = (
            f'<div class="status-overlay {status}">{labels[status]}</div>'
            if status in labels else ""
        )
        actions = self._review_action_markup(slug, status)
        script = f"""
(() => {{
  const slug = {json.dumps(slug)};
  const card = Array.from(document.querySelectorAll("article.card"))
    .find(item => item.dataset.slug === slug);
  if (!card) return;
  const media = card.querySelector(".media");
  const old = media && media.querySelector(".status-overlay");
  if (old) old.remove();
  if (media && {json.dumps(bool(overlay))}) media.insertAdjacentHTML("beforeend", {json.dumps(overlay)});
  const actionBox = card.querySelector(".actions");
  if (actionBox) actionBox.innerHTML = {json.dumps(actions)};
}})();
"""
        self.review_view.page().runJavaScript(script)

    def _set_review_card_progress(self, slug: str, value: int, message: str) -> None:
        progress = max(1, min(100, int(value)))
        script = f"""
(() => {{
  const slug = {json.dumps(slug)};
  const card = Array.from(document.querySelectorAll("article.card"))
    .find(item => item.dataset.slug === slug);
  if (!card) return;
  let box = card.querySelector(".publish-progress");
  if (!box) {{
    box = document.createElement("div");
    box.className = "publish-progress";
    box.innerHTML = "<i></i><span></span>";
    const copy = card.querySelector(".copy");
    card.insertBefore(box, copy);
  }}
  box.querySelector("i").style.width = {json.dumps(str(progress) + "%")};
  box.querySelector("span").textContent = {json.dumps(message)};
}})();
"""
        self.review_view.page().runJavaScript(script)

    def _set_review_queue_count(self, value: int) -> None:
        self.review_queue_count = max(0, int(value))
        self.review_queue_label.setText(f"予約待機 {self.review_queue_count}件")

    def _run_review_action(self, action: str, slug: str) -> None:
        if slug in self.review_action_workers:
            return
        previous = self.review_status_cache.get(slug, "unreviewed")
        optimistic = {
            "queue": "queued",
            "dequeue": "unreviewed",
            "delete": "deleted",
            "restore": "unreviewed",
        }[action]
        estimated_position = self.review_queue_count + 1 if action == "queue" else 0
        if action == "queue" and previous != "queued":
            self._set_review_queue_count(self.review_queue_count + 1)
        elif action in {"dequeue", "delete"} and previous == "queued":
            self._set_review_queue_count(self.review_queue_count - 1)
        self._set_review_card_status(slug, optimistic, position=estimated_position)
        self.scheduler_note.setText("予約状態を保存しています。" if action in {"queue", "dequeue"} else "記事状態を保存しています。")
        worker = ReviewActionWorker(self.site.root, action, slug)
        self.review_action_workers[slug] = worker
        worker.signals.completed.connect(
            lambda result, previous=previous: self._review_action_completed(result, previous)
        )
        worker.signals.failed.connect(
            lambda message, slug=slug, previous=previous, action=action: self._review_action_failed(
                slug, action, previous, message
            )
        )
        self.thread_pool.start(worker)

    def _review_action_completed(self, result: dict, previous: str) -> None:
        slug = str(result.get("slug") or "")
        self.review_action_workers.pop(slug, None)
        status = str(result.get("status") or "unreviewed")
        position = int(result.get("position") or 0)
        self._set_review_card_status(slug, status, position=position)
        messages = {
            "queue": f"予約待機 #{position} に追加しました。",
            "dequeue": "予約待機から外しました。",
            "delete": "記事を消去済みに移しました。",
            "restore": "未判別へ戻しました。",
        }
        self.scheduler_note.setText(messages.get(str(result.get("action") or ""), "保存しました。"))

    def _review_action_failed(
        self,
        slug: str,
        action: str,
        previous: str,
        message: str,
    ) -> None:
        self.review_action_workers.pop(slug, None)
        if action == "queue" and previous != "queued":
            self._set_review_queue_count(self.review_queue_count - 1)
        elif action in {"dequeue", "delete"} and previous == "queued":
            self._set_review_queue_count(self.review_queue_count + 1)
        self._set_review_card_status(slug, previous)
        self.scheduler_note.setText(f"保存できませんでした: {message}")

    def _review_action(self, action: str, slug: str) -> None:
        if action == "edit":
            self.edit_publish_article(slug)
        elif action == "publish":
            self.start_publish(slug)
        elif action in {"queue", "dequeue", "delete", "restore"}:
            self._run_review_action(action, slug)
        elif action == "open":
            self.open_published_article(slug)

    def _load_scheduler_controls(self) -> None:
        settings = load_automation_settings(self.site.root)
        source_names = {
            str(item.get("source_id") or ""): str(item.get("name") or item.get("url") or "")
            for item in list_sources(self.site.root)
        }
        continuous = bool(settings.get("continuous_mode_enabled", True))
        selected = [
            source_names.get(source_id, source_id)
            for source_id in settings.get("continuous_source_ids", [])
        ]
        target = "全情報源" if not selected else "・".join(selected)
        publish_parts = [
            f"{slot['time']}　{slot['count']}件" for slot in settings["publish_slots"]
        ]
        crawl_enabled = bool(settings.get("auto_crawl_enabled", True))
        publish_enabled = bool(settings.get("publish_enabled", True))
        waiting = pending_chatgpt_count(self.site.root)
        target_count = 1
        self.automation_state_label.setText(
            "稼働中" if continuous and crawl_enabled else "停止中"
        )
        retry_label = self._continuous_retry_label(
            self._continuous_retry_value(settings)
        )
        rate_limited = bool(settings.get("continuous_rate_limit_retry_until"))
        self.automation_next_label.setText(
            (
                f"{'利用制限の解除確認' if rate_limited else '自動再試行'} {retry_label}"
                if retry_label
                else (
                    "次の巡回で新規候補を取得"
                    if waiting < target_count else "現在の記事を処理中"
                )
            )
            if continuous and crawl_enabled else "停止中"
        )
        self.automation_crawl_schedule.setText(
            "常時補充　" + (
                f"待機列なしで最新候補を1件処理／{target}／FANZA上限"
                f"{int(settings.get('continuous_fanza_max_percent', 20))}%"
                if continuous and crawl_enabled else "停止中"
            )
        )
        self.automation_publish_schedule.setText(
            "公開　" + ("　｜　".join(publish_parts) if publish_enabled and publish_parts else "停止中")
        )
        last_result = settings.get("last_crawl_result")
        if isinstance(last_result, dict) and last_result:
            self.automation_scheduler_note.setText(
                self._automation_message(str(last_result.get("message") or ""))
            )
        elif not self.automation_scheduler_note.text():
            self.automation_scheduler_note.setText("まだ巡回結果はありません。")

    @staticmethod
    def _continuous_retry_value(settings: dict[str, Any]) -> str:
        values: list[datetime] = []
        for key in (
            "continuous_empty_retry_until",
            "continuous_rate_limit_retry_until",
        ):
            value = str(settings.get(key) or "")
            if not value:
                continue
            try:
                values.append(datetime.fromisoformat(value))
            except ValueError:
                continue
        return max(values).isoformat(timespec="seconds") if values else ""

    @staticmethod
    def _continuous_retry_label(value: str) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).strftime("%m/%d %H:%M")
        except ValueError:
            return ""

    @staticmethod
    def _next_schedule_label(slots: list[dict]) -> str:
        times = sorted(
            str(slot.get("time") or "")
            for slot in slots
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(slot.get("time") or ""))
        )
        if not times:
            return "未設定"
        now = time.strftime("%H:%M")
        upcoming = next((value for value in times if value > now), None)
        return f"今日 {upcoming}" if upcoming else f"明日 {times[0]}"

    @staticmethod
    def _automation_message(message: str) -> str:
        result = message
        replacements = (
            ("通常ChatGPT", "記事作成"),
            ("ChatGPT待機へ", "記事作成へ"),
            ("ChatGPT未完了", "記事作成待ち"),
            ("ChatGPTが", ""),
            ("ChatGPTの", ""),
            ("ChatGPTへ", "記事作成へ"),
            ("ChatGPT", "記事作成"),
            ("チャッピー", "記事作成"),
            ("Codex", "記事作成"),
        )
        for old, new in replacements:
            result = result.replace(old, new)
        return result

    def _record_crawl_result(self, status: str, message: str, **counts: int) -> None:
        settings = load_automation_settings(self.site.root)
        settings["last_crawl_result"] = {
            "status": status,
            "message": f"{time.strftime('%Y-%m-%d %H:%M')}　{message}",
            **counts,
        }
        save_automation_settings(self.site.root, settings)
        self._load_scheduler_controls()

    def open_automation_settings(self) -> None:
        dialog = AutomationSettingsDialog(
            self,
            load_automation_settings(self.site.root),
            [item for item in list_sources(self.site.root) if item.get("enabled", True)],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values["publish_enabled"] and not values["publish_slots"]:
            QMessageBox.warning(self, "予約投稿を確認", "予約投稿を有効にする場合は、投稿時刻を1つ以上追加してください。")
            return
        settings = load_automation_settings(self.site.root)
        settings.update(values)
        save_automation_settings(self.site.root, settings)
        self.scheduler_note.setText("常時運転と予約投稿の設定を保存しました。")
        self.automation_scheduler_note.setText("設定を保存しました。条件がそろい次第、自動で動きます。")
        self._load_scheduler_controls()

    def _ensure_startup_launcher(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        settings = load_automation_settings(self.site.root)
        if not settings.get("start_with_windows", True):
            return
        app_data = os.environ.get("APPDATA", "").strip()
        if not app_data:
            return
        startup = Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        launcher = startup / "IndanyaStudioBackground.cmd"
        try:
            startup.mkdir(parents=True, exist_ok=True)
            watchdog = self.site.root / "tools" / "indanya_watchdog.ps1"
            launcher.write_text(
                '@echo off\r\n'
                'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
                f'-File "{watchdog}" -Executable "{Path(sys.executable).resolve()}" '
                f'-SiteRoot "{self.site.root}"\r\n',
                encoding="utf-8",
                newline="",
            )
        except OSError:
            pass

    def _scheduler_tick(self) -> None:
        try:
            run_quality_routines(self.site.root)
        except Exception:
            traceback.print_exc()
        if (
            self.publish_worker
            or self.unpublish_worker
            or self.collect_worker
            or self.source_discovery_worker
            or self.x_daily_worker
            or self.x_trend_worker
            or self.x_copy_worker
            or self.x_schedule_worker
        ):
            return
        publish_runs = due_publish_runs(self.site.root)
        if publish_runs:
            run = publish_runs[0]
            if not run["slugs"]:
                record_automation_run(self.site.root, "publish", str(run["key"]))
            else:
                self.scheduled_publish_key = str(run["key"])
                self.scheduled_publish_slugs = list(run["slugs"])
                self.scheduler_note.setText(
                    f"{run['time']} の予約投稿を開始します（{len(run['slugs'])}件）"
                )
                self._start_next_scheduled_publish()
                return
        x_settings = load_x_settings(self.site.root)
        if (
            not x_settings.get("manual_delivery_only", False)
            and x_login_ready()
        ):
            now = datetime.now(JST)
            for row in reversed(list_x_posts(self.site.root)):
                if row.get("delivery_mode") not in {"reply", "thread"}:
                    continue
                if row.get("status") not in {"copy_ready", "failed"}:
                    continue
                if not str(row.get("post_text") or "").strip():
                    continue
                if row.get("requires_manual_check"):
                    continue
                retry_value = str(row.get("auto_retry_after") or "")
                if retry_value:
                    try:
                        if now < datetime.fromisoformat(retry_value).astimezone(JST):
                            continue
                    except ValueError:
                        pass
                self._start_x_schedule([str(row.get("post_id") or "")])
                return
        manga_status = x_manga_schedule_status(self.site.root)
        if manga_status.get("due"):
            manga_post = prepare_due_x_manga_thread(
                self.site.root,
                self.site.public_url,
            )
            if manga_post:
                self._refresh_x_posts()
                post_id = str(manga_post.get("post_id") or "")
                if not x_settings.get("manual_delivery_only", False) and x_login_ready():
                    self.x_post_status.setText(
                        "FANZAの人気・セール候補から漫画スレッドを作り、Xへ送信します。"
                    )
                    self._start_x_schedule([post_id])
                    return
                self.x_post_status.setText(
                    "FANZAの人気・セール候補から漫画5枚スレッドを用意しました。"
                )
            elif (
                not self.chatgpt_send_worker
                and not self.active_worker
                and pending_chatgpt_count(self.site.root) == 0
            ):
                source_id = str(self.fanza_manga_source.get("source_id") or "")
                if source_id:
                    mark_x_manga_replenishing(self.site.root)
                    run = manga_replenishment_run(self.site.root, source_id)
                    self.scheduled_collect = True
                    self.scheduled_crawl_run = run
                    self.x_post_status.setText(
                        "漫画スレッド用にFANZA公式漫画を1作品だけ補充しています。"
                    )
                    self.collect_auto_candidates(scheduled=True, run=run)
                    return
            self._refresh_x_auto_status()
        trend_status = x_trend_scan_status(self.site.root)
        if (
            trend_status.get("due")
            and x_login_ready()
            and self.chatgpt_send_worker is None
            and self.active_worker is None
            and self.x_daily_worker is None
            and self.x_copy_worker is None
            and self.x_schedule_worker is None
        ):
            self.start_x_trend_scan(force=False)
            return
        reply_status = x_reply_schedule_status(self.site.root)
        if (
            reply_status.get("due")
            and self.chatgpt_send_worker is None
            and self.active_worker is None
            and self.x_daily_worker is None
            and self.x_copy_worker is None
            and self.x_schedule_worker is None
        ):
            reply_post = prepare_due_x_reply_candidate(
                self.site.root,
                self.site.public_url,
            )
            if reply_post:
                post_id = str(reply_post.get("post_id") or "")
                self._refresh_x_posts()
                self._select_only_x_post(post_id)
                if not x_settings.get("manual_delivery_only", False):
                    self.x_schedule_after_copy_ids = [post_id]
                self.x_copy_worker = XCopyWorker(self.site.root, [post_id])
                self.x_copy_worker.signals.progress.connect(
                    self._x_post_progress_changed
                )
                self.x_copy_worker.signals.completed.connect(self._x_copy_completed)
                self.x_copy_worker.signals.failed.connect(self._x_copy_failed)
                self.x_post_status.setText(
                    (
                        "外部リプの候補文を作成し、条件を再確認してXへ返信します。"
                        if not x_settings.get("manual_delivery_only", False)
                        else "外部リプの候補文を作成しています。送信はX公式画面で確認します。"
                    )
                )
                self.thread_pool.start(self.x_copy_worker)
                return
            self._refresh_x_auto_status()
        daily_status = x_daily_posting_status(self.site.root)
        if (
            daily_status.get("due")
            and x_login_ready()
            and self.chatgpt_send_worker is None
            and self.active_worker is None
            and self.x_copy_worker is None
            and self.x_schedule_worker is None
        ):
            self.start_x_daily_cycle()
            return
        if source_discovery_due(self.site.root):
            self._start_source_discovery(force=False)
            return
        if (
            self.chatgpt_send_worker is None
            and self.chatgpt_login_worker is None
            and queued_chatgpt_request_ids(self.site.root, limit=1)
        ):
            self.start_chatgpt_auto_processing()
            return
        pending_count = pending_chatgpt_count(self.site.root)
        continuous_run = due_continuous_crawl(
            self.site.root,
            pending_count,
        )
        if continuous_run and not pending_count:
            self.scheduled_collect = True
            self.scheduled_crawl_run = continuous_run
            self.collect_auto_candidates(scheduled=True, run=continuous_run)
            return
        crawl_runs = due_crawl_runs(self.site.root)
        if crawl_runs:
            self.scheduled_collect = True
            self.scheduled_crawl_run = dict(crawl_runs[0])
            self.collect_auto_candidates(scheduled=True, run=self.scheduled_crawl_run)
            return
    def _start_next_scheduled_publish(self) -> None:
        if not self.scheduled_publish_slugs:
            if self.scheduled_publish_key:
                record_automation_run(self.site.root, "publish", self.scheduled_publish_key)
            self.scheduled_publish_key = ""
            self.scheduled_publish_active = False
            self.scheduler_note.setText("予約投稿が完了しました。")
            self.refresh_all()
            QTimer.singleShot(500, self._scheduler_tick)
            return
        slug = self.scheduled_publish_slugs.pop(0)
        self.start_publish(slug, scheduled=True)

    def _refresh_metrics(self, drafts: list[dict]) -> None:
        self.metric_labels["drafts"].setText(str(len(drafts)))
        self.metric_labels["rights"].setText(str(sum(1 for item in drafts if item["rights_status"] != "confirmed")))
        self.metric_labels["videos"].setText(str(sum(int(item["video_count"]) for item in drafts)))
        self.metric_labels["sites"].setText(str(len(self.registry.sites)))

    def _refresh_drafts(self, drafts: list[dict]) -> None:
        self.draft_table.setRowCount(len(drafts))
        rights = {"unconfirmed": "未確認", "requested": "許可待ち", "confirmed": "許可済み", "rejected": "使用不可"}
        for row, draft in enumerate(drafts):
            values = [draft["title"], draft["category"], f"画像 {draft['image_count']} / 動画 {draft['video_count']}", rights.get(draft["rights_status"], draft["rights_status"]), draft["updated_at"][:16].replace("T", " ")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, draft["slug"])
                self.draft_table.setItem(row, col, item)

    def _refresh_rights(self, drafts: list[dict]) -> None:
        self.rights_table.setRowCount(len(drafts))
        labels = {"unconfirmed": "未確認", "requested": "許可待ち", "confirmed": "許可済み", "rejected": "使用不可"}
        for row, draft in enumerate(drafts):
            values = [draft["title"], labels.get(draft["rights_status"], draft["rights_status"]), draft["rights_contact"] or "未入力", draft["source_url"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, draft["slug"])
                self.rights_table.setItem(row, col, item)

    def _refresh_publishing(self, drafts: list[dict]) -> None:
        self.publish_table.setRowCount(len(drafts))
        rights_labels = {"unconfirmed": "未確認", "requested": "許可待ち", "confirmed": "許可済み", "rejected": "使用不可"}
        status_labels = {"draft": "確認待ち", "ready": "公開可能", "published": "公開済み", "archived": "非公開"}
        for row, draft in enumerate(drafts):
            status = str(draft.get("status") or "draft")
            if status != "published" and draft.get("rights_status") == "confirmed":
                status = "ready"
            values = [
                draft["title"],
                "",
                status_labels.get(status, status),
                rights_labels.get(draft["rights_status"], draft["rights_status"]),
                f"画像 {draft['image_count']} / 動画 {draft['video_count']}",
                draft.get("published_site_name") or self.site.name,
                draft.get("published_url") or "未公開",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, draft["slug"])
                self.publish_table.setItem(row, col, item)
            switch_cell = QWidget()
            switch_layout = QHBoxLayout(switch_cell)
            switch_layout.setContentsMargins(0, 0, 0, 0)
            switch_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            publish_switch = QCheckBox("")
            publish_switch.setChecked(status == "published")
            publish_switch.setEnabled(draft.get("rights_status") == "confirmed" or status == "published")
            publish_switch.setProperty("slug", draft["slug"])
            publish_switch.setProperty("currently_published", status == "published")
            switch_layout.addWidget(publish_switch)
            self.publish_table.setCellWidget(row, 1, switch_cell)
            self.publish_table.setRowHeight(row, 40)

    def _refresh_editor_selector(self, drafts: list[dict]) -> None:
        self._editor_drafts = [dict(draft) for draft in drafts]
        self._filter_editor_selector()

    def _filter_editor_selector(self) -> None:
        selected = self.current_slug or self.editor_select.currentData()
        query = (
            self.editor_search.text().strip().casefold()
            if hasattr(self, "editor_search") else ""
        )
        drafts = getattr(self, "_editor_drafts", [])
        self.editor_select.blockSignals(True)
        self.editor_select.clear()
        self.editor_select.addItem("記事を選択", "")
        for draft in drafts:
            haystack = " ".join(
                str(draft.get(key) or "")
                for key in ("title", "source_url", "slug")
            ).casefold()
            if query and query not in haystack and str(draft.get("slug") or "") != selected:
                continue
            self.editor_select.addItem(draft["title"], draft["slug"])
        index = self.editor_select.findData(selected)
        self.editor_select.setCurrentIndex(max(0, index))
        self.editor_select.blockSignals(False)

    def _refresh_sites(self) -> None:
        self.site_table.setRowCount(len(self.registry.sites))
        for row, site in enumerate(self.registry.sites):
            name = f"● {site.name}" if site.site_id == self.registry.active_id else site.name
            for col, value in enumerate((name, site.public_url, site.provider, site.local_path)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, site.site_id)
                self.site_table.setItem(row, col, item)

    def _refresh_sources(self) -> None:
        if not hasattr(self, "sources_table"):
            return
        sources = list_sources(self.site.root)
        self.sources_table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            enabled_cell = QWidget()
            enabled_layout = QHBoxLayout(enabled_cell)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            enabled_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            enabled = QCheckBox("")
            enabled.setChecked(bool(source.get("enabled", True)))
            enabled.setProperty("source_id", source.get("source_id", ""))
            enabled_layout.addWidget(enabled)
            self.sources_table.setCellWidget(row, 0, enabled_cell)
            values = [
                str(source.get("name") or ""),
                "自動追加" if source.get("origin") == "automatic" else "手動登録",
                str(source.get("url") or ""),
                str(source.get("last_checked_at") or "未巡回")[:16].replace("T", " "),
                f"{int(source.get('success_count') or 0)} / {int(source.get('consecutive_failures') or 0)}",
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, source.get("source_id", ""))
                self.sources_table.setItem(row, col, item)
        status = source_discovery_status(self.site.root)
        if hasattr(self, "source_discovery_status_label"):
            next_at = str(status.get("next_run_at") or "")[:16].replace("T", " ")
            if not status.get("enabled"):
                label = "新規サイトの自動補充はOFFです"
            elif status.get("due"):
                label = "新規サイトの自動補充：実行待ち"
            else:
                label = f"新規サイトの自動補充：次回 {next_at}"
            label += f"／自動追加済み {int(status.get('automatic_source_count') or 0)}サイト"
            self.source_discovery_status_label.setText(label)
        if hasattr(self, "source_discovery_table"):
            rows = list(reversed(list_source_discovery_log(self.site.root)[-20:]))
            self.source_discovery_table.setRowCount(len(rows))
            labels = {
                "added": "追加",
                "accepted": "合格",
                "rejected": "不合格",
                "error": "通信失敗",
            }
            for row, record in enumerate(rows):
                values = [
                    labels.get(str(record.get("status") or ""), str(record.get("status") or "")),
                    str(record.get("name") or record.get("url") or "検索処理"),
                    str(record.get("reason") or ""),
                    str(record.get("checked_at") or "")[:16].replace("T", " "),
                ]
                for col, value in enumerate(values):
                    self.source_discovery_table.setItem(row, col, QTableWidgetItem(value))

    def _refresh_candidates(self) -> None:
        if not hasattr(self, "candidates_table"):
            return
        candidates = list_candidates(self.site.root)
        self.candidates_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            status = str(candidate.get("status") or "new")
            check_cell = QWidget()
            check_layout = QHBoxLayout(check_cell)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            selected = QCheckBox("")
            selected.setChecked(status == "new")
            selected.setEnabled(status == "new")
            selected.setProperty("url", candidate.get("url", ""))
            check_layout.addWidget(selected)
            self.candidates_table.setCellWidget(row, 0, check_cell)
            values = [
                str(candidate.get("score") or ""),
                {
                    "new": "候補",
                    "chatgpt_queued": "Codex処理待ち",
                    "drafted": "記事作成済み",
                    "failed": "作成失敗",
                    "ignored": "除外",
                    "safety_filtered": "対象外",
                }.get(status, status),
                str(candidate.get("title") or ""),
                str(candidate.get("source_name") or ""),
                str(candidate.get("url") or ""),
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate.get("url", ""))
                self.candidates_table.setItem(row, col, item)

    def add_auto_source(self) -> None:
        try:
            source = add_source(self.site.root, self.source_name_input.text(), self.source_feed_input.text())
        except Exception as exc:
            QMessageBox.warning(self, "情報源を追加できません", str(exc))
            return
        self.source_name_input.clear()
        self.source_feed_input.clear()
        self.sources_note.setText(f"情報源を追加しました: {source.get('name', '')}")
        self._refresh_sources()

    def run_source_discovery_now(self) -> None:
        self._start_source_discovery(force=True)

    def _start_source_discovery(self, *, force: bool) -> None:
        if self.source_discovery_worker or self.collect_worker:
            return
        if hasattr(self, "source_discovery_status_label"):
            self.source_discovery_status_label.setText("新しい巡回サイトを確認しています...")
        worker = SourceDiscoveryWorker(self.site.root, force=force)
        self.source_discovery_worker = worker
        worker.signals.progress.connect(self._source_discovery_progress)
        worker.signals.completed.connect(self._source_discovery_completed)
        worker.signals.failed.connect(self._source_discovery_failed)
        self.thread_pool.start(worker)

    def _source_discovery_progress(self, _value: int, message: str) -> None:
        if hasattr(self, "source_discovery_status_label"):
            self.source_discovery_status_label.setText(message)

    def _source_discovery_completed(self, result: dict) -> None:
        self.source_discovery_worker = None
        added = list(result.get("added") or [])
        checked = int(result.get("checked") or 0)
        if hasattr(self, "sources_note"):
            self.sources_note.setText(
                f"新規サイト候補を{checked}件検査し、{len(added)}サイトを自動追加しました。"
            )
        self._refresh_sources()
        self._load_scheduler_controls()
        QTimer.singleShot(500, self._scheduler_tick)

    def _source_discovery_failed(self, message: str) -> None:
        self.source_discovery_worker = None
        if hasattr(self, "source_discovery_status_label"):
            self.source_discovery_status_label.setText(
                f"新規サイトの確認に失敗しました: {message[:160]}"
            )
        QTimer.singleShot(500, self._scheduler_tick)

    def save_auto_sources(self) -> None:
        for row in range(self.sources_table.rowCount()):
            item = self.sources_table.item(row, 1)
            cell = self.sources_table.cellWidget(row, 0)
            switch = cell.findChild(QCheckBox) if cell else None
            if item is None or switch is None:
                continue
            update_source(self.site.root, str(item.data(Qt.ItemDataRole.UserRole) or ""), switch.isChecked())
        self.sources_note.setText("情報源のON/OFFを保存しました。")
        self._refresh_sources()

    def remove_auto_source(self) -> None:
        row = self.sources_table.currentRow()
        item = self.sources_table.item(row, 1) if row >= 0 else None
        if item is None:
            QMessageBox.information(self, "情報源を選択", "削除する情報源を選んでください。")
            return
        remove_source(self.site.root, str(item.data(Qt.ItemDataRole.UserRole) or ""))
        self.sources_note.setText("情報源を削除しました。")
        self._refresh_sources()

    def run_manual_crawl(self) -> None:
        enable_continuous_crawl(self.site.root)
        self._load_scheduler_controls()
        self.scheduler_note.setText("常時運転を開始し、今すぐ巡回しています。")
        run = manual_crawl_run(self.site.root, self.manual_crawl_count.value())
        self.collect_auto_candidates(scheduled=True, run=run)

    def update_manual_crawl_count(self, value: int) -> None:
        if hasattr(self, "manual_crawl_value"):
            self.manual_crawl_value.setText(f"{value}件")
        settings = load_automation_settings(self.site.root)
        settings["manual_crawl_count"] = max(1, min(100, int(value)))
        save_automation_settings(self.site.root, settings)

    def collect_auto_candidates(self, scheduled: bool = False, run: dict | None = None) -> None:
        if self.collect_worker or self.chatgpt_send_worker or self.source_discovery_worker:
            return
        self.save_auto_sources()
        self.scheduled_collect = scheduled
        if run is not None:
            self.scheduled_crawl_run = dict(run)
        self._set_automation_phase(
            0,
            "自動巡回で候補URLを収集中です。" if scheduled else "候補URLを収集中です。",
            reset=True,
        )
        article_count = int(self.scheduled_crawl_run.get("count") or 3) if scheduled else 10
        source_ids = list(self.scheduled_crawl_run.get("source_ids") or []) if scheduled else []
        self.collect_worker = CollectCandidatesWorker(
            self.site.root,
            max(5, min(30, article_count * 3)),
            source_ids,
        )
        self.collect_worker.signals.progress.connect(self._auto_progress_changed)
        self.collect_worker.signals.completed.connect(self._collect_completed)
        self.collect_worker.signals.failed.connect(self._auto_failed)
        self.thread_pool.start(self.collect_worker)

    def _auto_progress_changed(self, value: int, message: str) -> None:
        if hasattr(self, "auto_progress"):
            phase = 1 if any(term in message for term in ("選別", "重複", "対象外", "整理")) else 0
            self._set_automation_phase(phase, self._automation_message(message), progress=value)

    def _collect_completed(self, result: dict) -> None:
        self.collect_worker = None
        self._set_automation_phase(1, f"{result.get('count', 0)}件の候補URLを拾いました。", progress=100)
        self.refresh_all()
        if not self.scheduled_collect:
            return
        run_key = str(self.scheduled_crawl_run.get("key") or "")
        if not self.scheduled_crawl_run.get("continuous") and run_key:
            record_automation_run(self.site.root, "crawl", run_key)
        self.scheduled_crawl_keys = []
        # Manual, scheduled, and always-on crawls all use one shared article
        # pipeline. The requested count changes discovery breadth only.
        limit = 1
        source_ids = set(self.scheduled_crawl_run.get("source_ids") or [])
        observed_urls = set(result.get("observed_urls") or [])
        candidates = sort_candidates_balanced(
            self.site.root,
            [
                item for item in list_candidates(self.site.root)
                if item.get("status") == "new" and int(item.get("score") or 0) >= 22
                and str(item.get("url") or "") in observed_urls
                and (not source_ids or str(item.get("source_id") or "") in source_ids)
            ],
        )
        quality_filtered = sum(
            1
            for item in candidates
            if not bool((item.get("quality_eligibility") or {}).get("eligible"))
        )
        candidates = [
            item
            for item in candidates
            if bool((item.get("quality_eligibility") or {}).get("eligible"))
        ]
        manga_replenishment = bool(
            self.scheduled_crawl_run.get("manga_replenishment")
        )
        if manga_replenishment:
            candidates = [
                item for item in candidates
                if is_fanza_manga_candidate(item)
            ]
            mix_status = {}
        else:
            candidates, mix_status = filter_candidates_by_source_mix(
                self.site.root,
                candidates,
            )
        urls: list[str] = []
        cooling_down = 0
        for item in candidates:
            url = str(item.get("url") or "")
            if not url:
                continue
            allowed, _reason = can_attempt_site(self.site.root, url)
            if not allowed:
                cooling_down += 1
                continue
            urls.append(url)
            record_source_selection(
                self.site.root,
                str(item.get("source_id") or ""),
            )
            if len(urls) >= limit:
                break
        if self.scheduled_crawl_run.get("continuous"):
            record_continuous_crawl(self.site.root, bool(urls))
        if urls:
            self._start_batch_drafts(urls, scheduled=True)
            return
        self.scheduled_collect = False
        self.scheduled_crawl_run = {}
        cooldown_note = (
            f" 同じ失敗が続いた{cooling_down}件は、攻略法を切り替えるまで一時休止しています。"
            if cooling_down else ""
        )
        fanza_mix_note = ""
        if int(mix_status.get("blocked_count") or 0):
            fanza_mix_note = (
                f" FANZAは直近{int(mix_status.get('window') or 10)}記事で"
                f"{mix_status.get('current_percent', 0)}%のため、設定上限"
                f"{int(mix_status.get('maximum_percent') or 0)}%を下回るまで一般記事を優先します。"
            )
        quality_note = (
            f" 品質判定で{quality_filtered}件をAI送信前に保留しました。"
            if quality_filtered else ""
        )
        self.scheduler_note.setText(
            "自動巡回完了。今回、新しく記事にする候補はありませんでした。"
            + cooldown_note + fanza_mix_note + quality_note
        )
        self.auto_note.setText(
            "候補URLの確認は完了しました。今回は記事化する対象がありません。"
            + cooldown_note + fanza_mix_note + quality_note
        )
        self._refresh_automation_roadmap_from_current()
        self._record_crawl_result(
            "completed",
            f"巡回完了：候補 {result.get('count', 0)}件／新しく作る記事なし",
            candidate_count=int(result.get("count") or 0),
            created_count=0,
            failed_count=0,
        )
        QTimer.singleShot(500, self._scheduler_tick)

    def create_auto_drafts(self) -> None:
        urls: list[str] = []
        for row in range(self.candidates_table.rowCount()):
            cell = self.candidates_table.cellWidget(row, 0)
            switch = cell.findChild(QCheckBox) if cell else None
            if switch is not None and switch.isEnabled() and switch.isChecked():
                url = str(switch.property("url") or "")
                if url:
                    urls.append(url)
        if not urls:
            self.auto_note.setText("下書きにする候補が選ばれていません。")
            self._refresh_automation_roadmap_from_current()
            return
        self._start_batch_drafts(urls, scheduled=False)

    def _start_batch_drafts(self, urls: list[str], scheduled: bool) -> None:
        if not urls:
            return
        # A selected list contains candidates to inspect, not a batch to run.
        # Keep only the best current URL and never accumulate a stale backlog.
        urls = urls[:1]
        queued = 0
        queued_urls: list[str] = []
        candidates_by_url = {
            str(candidate.get("url") or ""): candidate
            for candidate in list_candidates(self.site.root)
        }
        for url in urls:
            try:
                candidate = candidates_by_url.get(url, {})
                trend = candidate.get("trend") if isinstance(candidate.get("trend"), dict) else {}
                trend_context = {
                    "buzz_score": int(candidate.get("buzz_score") or candidate.get("score") or 0),
                    "selection_reasons": list(trend.get("score_reasons") or [])[:8],
                    "source_name": str(candidate.get("source_name") or ""),
                    "source_card_text": str(candidate.get("source_card_text") or "")[:1000],
                    "public_engagement": int(trend.get("engagement") or 0),
                    "engagement_delta": int(trend.get("engagement_delta") or 0),
                    "cross_source_count": int(trend.get("cross_source_count") or 1),
                    "popular_context": bool(trend.get("popular_context")),
                    "sale_context": bool(trend.get("sale_context")),
                }
                request = enqueue_chatgpt_request(
                    self.site.root,
                    url,
                    {
                        "content_mode": "auto",
                        "category": "auto",
                        "reply_count": "auto",
                        "automation_origin": "crawl" if scheduled else "",
                        "automation_purpose": (
                            "manga_replenishment"
                            if self.scheduled_crawl_run.get("manga_replenishment")
                            else ""
                        ),
                        "trend_context": trend_context,
                    },
                )
                if str(request.get("status") or "") == "queued":
                    queued += 1
                    queued_urls.append(url)
            except Exception:
                continue
        status_warning = ""
        try:
            mark_candidates_status(self.site.root, queued_urls, "chatgpt_queued")
        except OSError:
            status_warning = "（候補一覧の表示更新は次回再試行します）"
        self._set_automation_phase(
            2,
            f"{queued}件の記事処理を開始します。待機列には残しません。{status_warning}",
            progress=100,
        )
        was_scheduled = scheduled
        self.scheduled_collect = False
        self.scheduled_crawl_run = {}
        self._refresh_candidates()
        self._refresh_chatgpt_queue_status()
        if queued:
            QTimer.singleShot(100, self.start_chatgpt_auto_processing)
        if was_scheduled:
            self.scheduler_note.setText(
                f"自動巡回完了。{queued}件の記事処理を開始しました。"
            )
            self._record_crawl_result(
                "completed",
                f"巡回完了：{queued}件の記事作成を開始",
                queued_count=queued,
                created_count=0,
                failed_count=max(0, len(urls) - queued),
            )
            QTimer.singleShot(500, self._scheduler_tick)

    def _auto_failed(self, message: str) -> None:
        self.collect_worker = None
        was_scheduled = self.scheduled_collect
        if was_scheduled:
            run_key = str(self.scheduled_crawl_run.get("key") or "")
            if self.scheduled_crawl_run.get("continuous"):
                record_continuous_crawl(self.site.root, False)
            elif run_key:
                record_automation_run(self.site.root, "crawl", run_key)
            self.scheduled_crawl_keys = []
        self.scheduled_collect = False
        self.scheduled_crawl_run = {}
        self._set_automation_phase(0, f"候補収集で失敗: {message}", progress=100)
        if was_scheduled:
            self.scheduler_note.setText("自動巡回でエラーが発生しました。次の巡回時刻に再開します。")
            self._record_crawl_result(
                "failed",
                f"巡回失敗：{message}。次の時刻に再試行します",
                created_count=0,
                failed_count=1,
            )

    def clean_auto_candidates(self) -> None:
        candidates = [item for item in list_candidates(self.site.root) if item.get("status") == "new"]
        save_candidates(self.site.root, candidates[:200])
        self.auto_note.setText("下書き済みや古い候補を整理しました。")
        self._refresh_automation_roadmap_from_current()
        self._refresh_candidates()

    def _refresh_site_controls(self) -> None:
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        for site in self.registry.sites:
            self.site_combo.addItem(site.name, site.site_id)
        self.site_combo.setCurrentIndex(self.site_combo.findData(self.registry.active_id))
        self.site_combo.blockSignals(False)
        self.site_link.setText(f"{self.site.name}を開く  ↗")
        self.site_link.setToolTip(self.site.public_url)
        self.side_site.setText(f"● {self.site.name}\n{self.site.provider}")
        if hasattr(self, "fanza_affiliate_id_input"):
            affiliate_id = load_fanza_settings(self.site.root).get("affiliate_id", "")
            self.fanza_affiliate_id_input.setText(affiliate_id)
            self.fanza_affiliate_status.setText(
                "設定済み。すべての記事で自動生成します。"
                if affiliate_id
                else "未設定。FANZAのPRは通常の商品URLのまま公開せず、ID設定まで停止します。"
            )

    def queue_fanza_batch(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("記事URLをまとめて追加")
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "一般Web、X、FANZAのURLを1行に1件入力してください。重複URLは追加しません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(
            "https://example.com/article\n"
            "https://x.com/example/status/...\n"
            "https://video.dmm.co.jp/av/content/?id=..."
        )
        layout.addWidget(editor, 1)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("待機へ追加")
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        raw_urls = list(dict.fromkeys(
            line.strip() for line in editor.toPlainText().splitlines() if line.strip()
        ))
        if not raw_urls:
            return
        invalid = [url for url in raw_urls if not url.startswith(("http://", "https://"))]
        if invalid:
            QMessageBox.warning(
                self,
                "URLを確認",
                "http:// または https:// で始まらない行があります。\n\n"
                + "\n".join(invalid[:8]),
            )
            return
        existing_urls = {
            str(item.get("url") or "")
            for item in list_chatgpt_requests(self.site.root)
            if item.get("status") in {"queued", "processing"}
        }
        added = 0
        for url in raw_urls[:150]:
            fanza = is_fanza_product_url(url)
            options = {
                "content_mode": "fanza_product" if fanza else "auto",
                "promotion_type": "affiliate" if fanza else "organic",
                "category": "auto",
                "reply_count": "auto",
            }
            item = enqueue_chatgpt_request(self.site.root, url, options)
            normalized = str(item.get("url") or "")
            if normalized not in existing_urls:
                added += 1
                existing_urls.add(normalized)
        self.generate_progress.setValue(100)
        self.generate_percent.setText("待機")
        self.generate_status.setText(f"URL {added}件をCodex処理待ちへ追加しました")
        self.generate_result.setText(
            "素材回収と事前検査に通ったURLだけCodexへ送り、完成後に公開形式を再検査します。"
        )
        self._refresh_chatgpt_queue_status()

    def queue_article_for_chatgpt(self) -> None:
        url = self.source_url.text().strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(
                self,
                "URLを確認",
                "http:// または https:// から始まるURLを入力してください。",
            )
            return
        is_fanza = is_fanza_product_url(url)
        options = {
            "content_mode": "fanza_product" if is_fanza else "auto",
            "promotion_type": "affiliate" if is_fanza else "organic",
            "category": str(self.category_combo.currentData()),
            "reply_count": str(self.reply_combo.currentData()),
            "editorial_brief": self.editorial_brief_input.text().strip(),
            "fanza_url": self.fanza_url_input.text().strip(),
        }
        duplicates = find_duplicate_drafts(self.site.root, url)
        if duplicates:
            answer = QMessageBox.question(
                self,
                "重複記事",
                "同じURLの記事があります。\n重複しますが、新しく作りますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            options["force_duplicate"] = True
            options["duplicate_of"] = duplicates[0]["slug"]
        try:
            enqueue_chatgpt_request(self.site.root, url, options)
        except ValueError as exc:
            QMessageBox.warning(self, "URLを追加できません", str(exc))
            return
        self.generate_progress.setValue(100)
        self.generate_percent.setText("待機")
        self.generate_status.setText("Codexの記事処理待ちへ追加しました")
        self.generate_result.setText(
            "素材回収後、Codexを1回だけ使って判定と記事作成を行います。"
        )
        self._refresh_chatgpt_queue_status()
        QTimer.singleShot(100, self.start_chatgpt_auto_processing)

    def open_chatgpt_batch(self) -> None:
        count = len(queued_chatgpt_request_ids(self.site.root, limit=20))
        if not count:
            QMessageBox.information(
                self, "待機記事なし", "Codexの処理を待っているURLはありません。"
            )
            self._refresh_chatgpt_queue_status()
            return
        self.generate_status.setText(f"{count}件の自動処理を開始します")
        self.generate_result.setText(
            "取得と検査はプログラム、内容判断と文章作成だけをCodexが担当します。"
        )
        self._refresh_chatgpt_queue_status()
        QTimer.singleShot(100, self.start_chatgpt_auto_processing)

    def generate_article(self) -> None:
        url = self.source_url.text().strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URLを確認", "http:// または https:// から始まるURLを入力してください。")
            return
        self.generate_button.setEnabled(False)
        self.generate_result.setText("")
        self.generate_progress.setValue(2)
        self.generate_percent.setText("2%")
        is_fanza = is_fanza_product_url(url)
        editorial_intent = {
            "content_mode": "fanza_product" if is_fanza else "auto",
            "promotion_type": "affiliate" if is_fanza else "organic",
            "editorial_brief": self.editorial_brief_input.text().strip(),
            "fanza_url": self.fanza_url_input.text().strip(),
            "private_note": self.private_client_note_input.text().strip(),
        }
        duplicates = find_duplicate_drafts(self.site.root, url)
        if duplicates:
            answer = QMessageBox.question(
                self,
                "重複記事",
                "同じURLの記事があります。\n重複しますが、新しく作りますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.generate_button.setEnabled(True)
                return
            editorial_intent["force_duplicate"] = True
            editorial_intent["duplicate_of"] = duplicates[0]["slug"]
        self.active_worker = GenerateArticleWorker(
            self.site.root,
            url,
            str(self.category_combo.currentData()),
            str(self.reply_combo.currentData()),
            editorial_intent,
        )
        self.active_worker.signals.progress.connect(self._generation_progress)
        self.active_worker.signals.completed.connect(self._generation_completed)
        self.active_worker.signals.failed.connect(self._generation_failed)
        self.thread_pool.start(self.active_worker)

    def _generation_progress(self, value: int, message: str) -> None:
        self.generate_progress.setValue(value)
        self.generate_percent.setText(f"{value}%")
        self.generate_status.setText(message)

    def _generation_completed(self, result: dict) -> None:
        self.generate_button.setEnabled(True)
        self.current_slug = str(result["slug"])
        self.generate_result.setText(f"完成: {result['title']}  |  画像 {result['image_count']}枚 / 動画 {result['video_count']}本")
        self.refresh_all()
        self.switch_page("editor")
        self.load_editor_draft()

    def _generation_failed(self, message: str) -> None:
        self.generate_button.setEnabled(True)
        self.generate_status.setText("記事生成に失敗しました")
        self.generate_result.setText(message)
        self.generate_progress.setValue(100)
        self.generate_percent.setText("失敗")
        QMessageBox.critical(self, "記事生成エラー", message)

    def _selected_slug(self, table: QTableWidget) -> str:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def open_selected_draft(self) -> None:
        slug = self._selected_slug(self.draft_table)
        if not slug:
            QMessageBox.information(self, "記事を選択", "編集する記事を選んでください。")
            return
        self.current_slug = slug
        self.switch_page("editor")
        self.load_editor_draft()

    def _editor_selection_changed(self) -> None:
        value = self.editor_select.currentData()
        if value:
            self.current_slug = str(value)

    def _draft_payload(self, slug: str) -> dict:
        return load_draft_payload(slug, self.site.root)

    def load_editor_draft(self) -> None:
        slug = self.current_slug or str(self.editor_select.currentData() or "")
        if not slug:
            return
        try:
            payload = self._draft_payload(slug)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        self.current_slug = slug
        self._editor_loaded_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        index = self.editor_select.findData(slug)
        if index >= 0:
            self.editor_select.blockSignals(True)
            self.editor_select.setCurrentIndex(index)
            self.editor_select.blockSignals(False)
        self.editor_title.setText(str(payload.get("title") or ""))
        self.editor_summary.setPlainText(str(payload.get("summary") or ""))
        self.editor_category.setText(str(payload.get("category") or ""))
        self.editor_source.setText(str(payload.get("source_url") or ""))
        self.editor_media.setText(f"画像 {len(payload.get('images', []))}枚 / 動画 {len(payload.get('videos', []))}本")
        identity = payload.get("identity_resolution")
        subject = payload.get("main_subject")
        identity = identity if isinstance(identity, dict) else {}
        subject = subject if isinstance(subject, dict) else {}
        identity_labels = {
            "verified": "確認済み",
            "not_applicable": "人物中心の記事ではない",
            "not_found": "公式アカウント未特定",
            "ambiguous": "同名候補があり未確定",
            "pending": "照合待ち",
            "error": "照合失敗",
        }
        identity_status = str(identity.get("status") or "pending")
        subject_name = str(subject.get("name") or "主役名なし")
        subject_role = str(subject.get("role") or "区分不明")
        identity_message = str(identity.get("message") or "次回の生成・修復処理で照合します")
        self.editor_identity.setText(
            f"本人リンク: {identity_labels.get(identity_status, identity_status)} | "
            f"主役 {subject_name}（{subject_role}）\n{identity_message}"
        )
        quality = article_quality_report(payload)
        issues = [*quality.get("blockers", []), *quality.get("warnings", [])]
        self.editor_quality.setText(
            f"品質判定: {quality.get('score', 0)}点 / {quality.get('recommendation', 'review')}"
            + (f"\n確認項目: {' / '.join(issues)}" if issues else "\n構造上の問題は検出されていません")
        )
        self.editor_feedback_reason.setCurrentIndex(0)
        self._refresh_editor_affiliate_recommendation(payload)
        is_published = str(payload.get("editorial_status") or payload.get("status") or "") == "published"
        self.editor_publish.setText("サイトの記事を更新" if is_published else "サイトへ公開")
        self.editor_open_published.setEnabled(bool(payload.get("published_url")))
        self._render_preview(payload)

    def save_editor_draft(self) -> None:
        if not self.current_slug:
            return
        try:
            payload = self._draft_payload(self.current_slug)
            payload["title"] = self.editor_title.text().strip()
            payload["summary"] = self.editor_summary.toPlainText().strip()
            payload["category"] = self.editor_category.text().strip()
            payload["source_url"] = self.editor_source.text().strip()
            save_draft(payload, self.site.root)
            before = getattr(self, "_editor_loaded_snapshot", {})
            if isinstance(before, dict) and changed_fields(before, payload):
                record_editorial_feedback(
                    self.site.root,
                    before,
                    payload,
                    self.editor_feedback_reason.currentData() or "",
                    note="記事編集室で手動修正",
                )
            self._editor_loaded_snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
            self._render_preview(payload)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def regenerate_editor_article(self) -> None:
        if not self.current_slug:
            QMessageBox.information(self, "記事を選択", "素材を取り直す記事を読み込んでください。")
            return
        try:
            payload = self._draft_payload(self.current_slug)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        source_url = str(payload.get("source_url") or "")
        if not is_fanza_product_url(source_url):
            QMessageBox.warning(self, "商品URLを確認", "FANZAの個別商品URLを確認できませんでした。")
            return
        answer = QMessageBox.question(
            self,
            "記事を作り直す",
            "元ページから画像と動画を取り直し、タイトル・レス・素材を更新します。\n"
            "同じ記事を上書きします。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.rebuild_media_button.setEnabled(False)
        self.rebuild_media_button.setText("素材を取り直しています…")
        self.active_worker = GenerateArticleWorker(
            self.site.root,
            source_url,
            str(payload.get("category") or "auto"),
            str(payload.get("comments") or "auto"),
            {
                "content_mode": "fanza_product" if is_fanza_product_url(source_url) else "auto",
                "promotion_type": "affiliate" if is_fanza_product_url(source_url) else "organic",
                "rebuild_existing": True,
            },
        )
        self.active_worker.signals.completed.connect(self._editor_rebuild_completed)
        self.active_worker.signals.failed.connect(self._editor_rebuild_failed)
        self.thread_pool.start(self.active_worker)

    def _editor_rebuild_completed(self, result: dict) -> None:
        self.rebuild_media_button.setEnabled(True)
        self.rebuild_media_button.setText("素材を取り直して作り直す")
        self.current_slug = str(result.get("slug") or self.current_slug)
        self.load_editor_draft()
        self.refresh_all()
        QMessageBox.information(
            self,
            "素材を更新しました",
            f"画像 {result.get('image_count', 0)}枚 / 動画 {result.get('video_count', 0)}本で作り直しました。\n"
            "内容を確認して「サイトの記事を更新」を押してください。",
        )

    def _editor_rebuild_failed(self, message: str) -> None:
        self.rebuild_media_button.setEnabled(True)
        self.rebuild_media_button.setText("素材を取り直して作り直す")
        QMessageBox.critical(self, "素材の取り直しに失敗", message)

    def refine_editor_draft(self) -> None:
        if not self.current_slug:
            QMessageBox.information(self, "記事を選択", "推敲する記事を読み込んでください。")
            return
        try:
            payload = self._draft_payload(self.current_slug)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        self.refine_button.setEnabled(False)
        self.refine_button.setText("推敲中…")
        self.refine_worker = RefineDraftWorker(self.site.root, payload)
        self.refine_worker.signals.completed.connect(self._refine_completed)
        self.refine_worker.signals.failed.connect(self._refine_failed)
        self.thread_pool.start(self.refine_worker)

    def _refine_completed(self, result: dict) -> None:
        self.refine_button.setEnabled(True)
        self.refine_button.setText("Codexで会話を推敲")
        self.current_slug = str(result["slug"])
        self.load_editor_draft()
        self.refresh_all()
        QMessageBox.information(self, "推敲完了", "タイトルとレスを人間らしい会話へ書き直しました。")

    def _refine_failed(self, message: str) -> None:
        self.refine_button.setEnabled(True)
        self.refine_button.setText("Codexで会話を推敲")
        QMessageBox.critical(self, "推敲エラー", message)

    def _render_preview(self, payload: dict) -> None:
        try:
            self.preview_videos = {
                str(item.get("id")): item for item in payload.get("videos", []) if isinstance(item, dict)
            }
            article = build_article(payload, self.site.root, preview=True)
            if not self.preview_server:
                raise RuntimeError("プレビューサーバーが起動していません")
            port = self.preview_server.server_address[1]
            style, _ = _extract_sample_assets(self.site.root)
            preview_html = article.article_html.replace(
                '<link rel="stylesheet" href="/preview.css">',
                f"<style>{style}{X_EMBED_STYLE}{VIDEO_EMBED_STYLE}</style>",
            )
            logo_path = self.site.root / "assets" / "common" / "indanya-logo.png"
            if logo_path.is_file():
                logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
                preview_html = preview_html.replace(
                    "/site/assets/common/indanya-logo.png",
                    f"data:image/png;base64,{logo_data}",
                )
            preview_html = preview_html.replace(
                'href="/site/index.html"',
                f'href="{self.site.public_url}"',
            ).replace(
                'src="/api/video-proxy?',
                f'src="http://127.0.0.1:{port}/api/video-proxy?',
            )
            preview_root = self.site.root / ".article-studio" / "preview"
            preview_root.mkdir(parents=True, exist_ok=True)
            preview_path = preview_root / "article.html"
            preview_path.write_text(preview_html, encoding="utf-8")
            preview_url = QUrl.fromLocalFile(str(preview_path))
            preview_url.setQuery(f"v={time.time_ns()}")
            self.preview.setUrl(preview_url)
        except Exception as exc:
            self.preview.setHtml(f"<meta charset='utf-8'><p>プレビューを表示できません: {exc}</p>")

    def open_video_player(self, video_id: str) -> None:
        video = self.preview_videos.get(video_id)
        if not video:
            QMessageBox.warning(self, "動画が見つかりません", "この動画の情報を読み込めませんでした。")
            return
        progress = QProgressDialog("動画を準備しています…", "", 0, 100, self)
        progress.setWindowTitle("動画を準備中")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        worker = DownloadVideoWorker(self.site.root, video)
        self.video_downloads.append(worker)
        self.video_progress.append(progress)
        worker.signals.progress.connect(lambda value, message, dialog=progress: (dialog.setValue(value), dialog.setLabelText(message)))
        worker.signals.completed.connect(
            lambda result, item=video, task=worker, dialog=progress: self._video_downloaded(result, item, task, dialog)
        )
        worker.signals.failed.connect(
            lambda message, task=worker, dialog=progress: self._video_download_failed(message, task, dialog)
        )
        self.thread_pool.start(worker)

    def _video_downloaded(self, result: dict, video: dict, worker: DownloadVideoWorker, progress: QProgressDialog) -> None:
        progress.close()
        if worker in self.video_downloads:
            self.video_downloads.remove(worker)
        if progress in self.video_progress:
            self.video_progress.remove(progress)
        source = QUrl.fromLocalFile(str(result["path"]))
        title = str(video.get("label") or "記事動画")
        player = VideoPlayerDialog(self, title, source)
        self.video_windows.append(player)
        player.finished.connect(lambda _result, window=player: self.video_windows.remove(window) if window in self.video_windows else None)
        player.show()

    def _video_download_failed(self, message: str, worker: DownloadVideoWorker, progress: QProgressDialog) -> None:
        progress.close()
        if worker in self.video_downloads:
            self.video_downloads.remove(worker)
        if progress in self.video_progress:
            self.video_progress.remove(progress)
        QMessageBox.critical(self, "動画を準備できません", message)

    def edit_rights(self) -> None:
        slug = self._selected_slug(self.rights_table)
        if not slug:
            QMessageBox.information(self, "記事を選択", "許可状態を更新する記事を選んでください。")
            return
        payload = self._draft_payload(slug)
        dialog = QDialog(self)
        dialog.setWindowTitle("許可状態を更新")
        dialog.setMinimumWidth(520)
        form = QFormLayout(dialog)
        state = QComboBox()
        for label, value in (("未確認", "unconfirmed"), ("確認待ち", "requested"), ("許可済み", "confirmed"), ("使用不可", "rejected")):
            state.addItem(label, value)
        state.setCurrentIndex(max(0, state.findData(payload.get("rights_status", "unconfirmed"))))
        contact = QLineEdit(str(payload.get("rights_contact") or ""))
        note = QPlainTextEdit(str(payload.get("rights_note") or ""))
        form.addRow("状態", state)
        form.addRow("連絡先", contact)
        form.addRow("メモ", note)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        form.addRow(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                update_draft_rights(slug, state.currentData(), contact.text(), note.toPlainText(), self.site.root)
                self.refresh_all()
            except Exception as exc:
                QMessageBox.critical(self, "更新エラー", str(exc))

    def _selected_publish_slug(self) -> str:
        return self._selected_slug(self.publish_table)

    def edit_selected_publish_article(self) -> None:
        slug = self._selected_publish_slug()
        if not slug:
            QMessageBox.information(self, "記事を選択", "編集する記事を選んでください。")
            return
        self.edit_publish_article(slug)

    def edit_publish_article(self, slug: str) -> None:
        self.current_slug = slug
        self.switch_page("editor")
        self.load_editor_draft()

    def publish_selected_article(self) -> None:
        self.start_publish(self._selected_publish_slug())

    def sync_publish_switches(self) -> None:
        queue: list[tuple[str, str]] = []
        for row in range(self.publish_table.rowCount()):
            item = self.publish_table.item(row, 0)
            if item is None:
                continue
            slug = str(item.data(Qt.ItemDataRole.UserRole) or "")
            cell = self.publish_table.cellWidget(row, 1)
            switch = cell.findChild(QCheckBox) if cell else None
            if not slug or switch is None or not switch.isEnabled():
                continue
            desired_published = switch.isChecked()
            try:
                payload = self._draft_payload(slug)
            except Exception:
                continue
            current_published = bool(payload.get("published_url")) or str(payload.get("editorial_status") or payload.get("status") or "") == "published"
            if desired_published:
                queue.append(("publish", slug))
            elif current_published:
                queue.append(("unpublish", slug))
        if not queue:
            self.publish_note.setText("反映する変更がありません。")
            return
        self.publish_queue = queue
        self.publish_batch_total = len(queue)
        self.publish_note.setText(f"{len(queue)}件の公開ON/OFFを反映します。")
        self._start_next_publish_in_queue()

    def _start_next_publish_in_queue(self) -> None:
        if not self.publish_queue:
            self.publish_batch_total = 0
            self.publish_note.setText("公開ON/OFFの反映が完了しました。")
            self.refresh_all()
            return
        action, current = self.publish_queue.pop(0)
        done = self.publish_batch_total - len(self.publish_queue)
        label = "公開/更新" if action == "publish" else "非公開"
        self.publish_note.setText(f"{done}/{self.publish_batch_total}件目を{label}にしています。")
        if action == "publish":
            self.start_publish(current, from_queue=True)
        else:
            self.start_unpublish(current, confirm=False, from_queue=True)

    def start_publish(
        self,
        slug: str,
        from_queue: bool = False,
        scheduled: bool = False,
    ) -> None:
        if self.publish_worker:
            if not slug or slug == self.publish_current_slug:
                return
            already_waiting = any(
                action == "publish" and queued_slug == slug
                for action, queued_slug in self.publish_queue
            )
            if not already_waiting:
                self.publish_queue.append(("publish", slug))
                self.publish_batch_total = max(
                    self.publish_batch_total,
                    len(self.publish_queue) + 1,
                )
                self._set_review_card_status(slug, "publish_waiting")
                self.publish_note.setText(
                    f"公開処理へ追加しました。現在の記事の次に反映します（待ち{len(self.publish_queue)}件）"
                )
            return
        if not slug:
            if scheduled:
                self._start_next_scheduled_publish()
            else:
                QMessageBox.information(self, "記事を選択", "公開する記事を選んでください。")
            return
        try:
            payload = self._draft_payload(slug)
        except Exception as exc:
            if scheduled:
                remove_from_queue(self.site.root, slug, "failed")
                self.scheduler_note.setText(f"{slug} を読めないため予約から外しました。")
                self._start_next_scheduled_publish()
            else:
                QMessageBox.critical(self, "読込エラー", str(exc))
            return
        if payload.get("rights_status") != "confirmed" or payload.get("rights_confirmed") is not True:
            if scheduled:
                remove_from_queue(self.site.root, slug, "failed")
                update_review_status(self.site.root, slug, "failed", message="画像・動画の使用許可が未確認です")
                self.scheduler_note.setText(f"「{payload.get('title', slug)}」は許可未確認のため予約から外しました。")
                self._start_next_scheduled_publish()
            else:
                QMessageBox.warning(self, "許可確認が必要です", "許可管理でこの記事を「許可済み」にしてから公開してください。")
            return
        if not self.site.repository_url.strip():
            if scheduled:
                remove_from_queue(self.site.root, slug, "failed")
                update_review_status(self.site.root, slug, "failed", message="公開先リポジトリが未設定です")
                self._start_next_scheduled_publish()
            else:
                QMessageBox.warning(self, "公開先を確認", "管理サイトにGitHubリポジトリURLを設定してください。")
            return
        self.publish_current_slug = slug
        self.publish_from_schedule = scheduled
        self.scheduled_publish_active = scheduled
        self.review_publish_progress[slug] = 1
        if self.review_status_cache.get(slug) == "queued":
            self.publish_queued_slugs.add(slug)
        existing = str(payload.get("editorial_status") or payload.get("status") or "") == "published"
        action = "更新" if existing else "公開"
        self.publish_note.setText(f"「{payload.get('title', slug)}」を{action}しています。")
        self._set_review_card_status(slug, "publishing")
        self._set_review_card_progress(slug, 1, "サイトへ反映中 1%")
        self.publish_worker = PublishArticleWorker(self.site.root, payload, self.site)
        self.publish_worker.signals.progress.connect(self._publish_progress_changed)
        self.publish_worker.signals.completed.connect(self._publish_completed)
        self.publish_worker.signals.failed.connect(self._publish_failed)
        self.thread_pool.start(self.publish_worker)

    def _publish_progress_changed(self, value: int, message: str) -> None:
        if self.publish_current_slug:
            self.review_publish_progress[self.publish_current_slug] = value
            self.scheduler_note.setText(message)
            self._set_review_card_progress(
                self.publish_current_slug,
                value,
                f"サイトへ反映中 {max(1, min(100, int(value)))}%",
            )
        if self.publish_progress:
            self.publish_progress.setLabelText(message)
            self.publish_progress.setValue(value)

    def _publish_completed(self, result: dict) -> None:
        if self.publish_progress:
            self.publish_progress.setValue(100)
            self.publish_progress.close()
            self.publish_progress = None
        self.publish_worker = None
        slug = str(result.get("slug") or self.publish_current_slug or self.current_slug)
        self.current_slug = slug
        self.review_publish_progress.pop(slug, None)
        was_queued = slug in self.publish_queued_slugs
        self.publish_queued_slugs.discard(slug)
        if was_queued:
            self._set_review_queue_count(self.review_queue_count - 1)
        if slug:
            self._set_review_card_status(slug, "published")
        was_scheduled = self.publish_from_schedule
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        if self.stack.currentWidget() == self.pages["editor"]:
            self.load_editor_draft()
        x_note = ""
        if result.get("x_attempted"):
            x_note = (
                f" / X投稿は要確認: {result.get('x_error')}"
                if result.get("x_error")
                else " / X投稿候補に追加"
            )
        if slug:
            try:
                notify_x_manga_article_published(
                    self.site.root,
                    self._draft_payload(slug),
                )
            except Exception:
                traceback.print_exc()
        if was_scheduled:
            self.scheduler_note.setText(f"予約公開完了: {result.get('url', '')}{x_note}")
            self._start_next_scheduled_publish()
        elif self.publish_queue:
            self.publish_note.setText(f"公開/更新完了: {result.get('url', '')}{x_note}")
            self._start_next_publish_in_queue()
        elif self.publish_batch_total:
            self.publish_batch_total = 0
            self.publish_note.setText("まとめて公開/更新が完了しました。")
        else:
            self.publish_note.setText(f"公開/更新完了: {result.get('url', '')}{x_note}")

    def _publish_failed(self, message: str) -> None:
        if self.publish_progress:
            self.publish_progress.close()
            self.publish_progress = None
        self.publish_worker = None
        slug = self.publish_current_slug
        was_queued = slug in self.publish_queued_slugs
        self.publish_queued_slugs.discard(slug)
        self.review_publish_progress.pop(slug, None)
        was_scheduled = self.publish_from_schedule
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        if slug:
            if was_queued:
                self._set_review_queue_count(self.review_queue_count - 1)
            self._set_review_card_status(slug, "failed")
            worker = ReviewActionWorker(self.site.root, "fail", slug)
            self.review_action_workers[slug] = worker
            worker.signals.completed.connect(
                lambda _result, slug=slug: self.review_action_workers.pop(slug, None)
            )
            worker.signals.failed.connect(
                lambda _error, slug=slug: self.review_action_workers.pop(slug, None)
            )
            self.thread_pool.start(worker)
        if was_scheduled:
            self.scheduler_note.setText(f"予約公開失敗: {message}。次の記事へ進みます。")
            self._start_next_scheduled_publish()
            return
        self.publish_note.setText(f"公開/更新失敗: {message}")
        if self.publish_queue:
            self._start_next_publish_in_queue()
        else:
            self.publish_batch_total = 0

    def open_selected_published_article(self) -> None:
        self.open_published_article(self._selected_publish_slug())

    def open_published_article(self, slug: str) -> None:
        if not slug:
            QMessageBox.information(self, "記事を選択", "公開記事を選んでください。")
            return
        try:
            payload = self._draft_payload(slug)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        url = str(payload.get("published_url") or "")
        if not url:
            QMessageBox.information(self, "未公開です", "この記事はまだ公開されていません。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def unpublish_selected_article(self) -> None:
        slug = self._selected_publish_slug()
        self.start_unpublish(slug, confirm=True)

    def start_unpublish(self, slug: str, confirm: bool = True, from_queue: bool = False) -> None:
        if not slug:
            QMessageBox.information(self, "記事を選択", "公開を取り消す記事を選んでください。")
            return
        try:
            payload = self._draft_payload(slug)
        except Exception as exc:
            QMessageBox.critical(self, "読込エラー", str(exc))
            return
        if not payload.get("published_url"):
            QMessageBox.information(self, "未公開です", "この記事はまだ公開されていません。")
            return
        published_site_id = str(payload.get("published_site_id") or self.site.site_id)
        published_site = next((item for item in self.registry.sites if item.site_id == published_site_id), None)
        if not published_site:
            QMessageBox.warning(self, "公開先が見つかりません", "記事を公開した管理サイトが登録されていません。")
            return
        if confirm and QMessageBox.question(
            self,
            "公開を取り消す",
            f"「{payload.get('title', slug)}」を公開サイトから削除します。\n記事データはアプリに残ります。よろしいですか？",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.publish_progress = QProgressDialog("公開取り消しを準備しています", "", 0, 100, self)
        self.publish_progress.setWindowTitle("公開を取り消す")
        self.publish_progress.setCancelButton(None)
        self.publish_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.publish_progress.setMinimumDuration(0)
        self.publish_progress.setValue(1)
        self.unpublish_worker = UnpublishArticleWorker(self.site.root, payload, published_site)
        self.unpublish_worker.signals.progress.connect(self._publish_progress_changed)
        self.unpublish_worker.signals.completed.connect(self._unpublish_completed)
        self.unpublish_worker.signals.failed.connect(self._unpublish_failed)
        self.thread_pool.start(self.unpublish_worker)

    def _unpublish_completed(self, result: dict) -> None:
        if self.publish_progress:
            self.publish_progress.setValue(100)
            self.publish_progress.close()
            self.publish_progress = None
        self.unpublish_worker = None
        self.refresh_all()
        if self.publish_queue:
            self.publish_note.setText("非公開にしました。")
            self._start_next_publish_in_queue()
        elif self.publish_batch_total:
            self.publish_batch_total = 0
            self.publish_note.setText("公開ON/OFFの反映が完了しました。")
        else:
            self.publish_note.setText("非公開にしました。記事データは残っています。")

    def _unpublish_failed(self, message: str) -> None:
        if self.publish_progress:
            self.publish_progress.close()
            self.publish_progress = None
        self.unpublish_worker = None
        self.publish_queue = []
        self.publish_batch_total = 0
        self.publish_note.setText(f"公開取り消し失敗: {message}")

    def _selected_site_id(self) -> str:
        row = self.site_table.currentRow()
        item = self.site_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def add_site(self) -> None:
        dialog = SiteDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.registry.upsert(dialog.values())
                self._after_site_change()
            except ValueError as exc:
                QMessageBox.warning(self, "入力を確認", str(exc))

    def edit_site(self) -> None:
        site_id = self._selected_site_id()
        site = next((item for item in self.registry.sites if item.site_id == site_id), None)
        if not site:
            return
        dialog = SiteDialog(self, site)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.registry.upsert(dialog.values(), site_id)
                self._after_site_change()
            except ValueError as exc:
                QMessageBox.warning(self, "入力を確認", str(exc))

    def remove_site(self) -> None:
        site_id = self._selected_site_id()
        if not site_id:
            return
        if QMessageBox.question(self, "管理サイトを削除", "このサイトをアプリの一覧から外しますか？\nサイト本体のファイルは削除しません。") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.registry.remove(site_id)
            self._after_site_change()
        except ValueError as exc:
            QMessageBox.warning(self, "削除できません", str(exc))

    def activate_selected_site(self) -> None:
        site_id = self._selected_site_id()
        if site_id:
            self.registry.set_active(site_id)
            self._after_site_change()

    def _site_combo_changed(self, index: int) -> None:
        site_id = self.site_combo.itemData(index)
        if site_id and site_id != self.registry.active_id:
            self.registry.set_active(str(site_id))
            self._after_site_change()

    def _after_site_change(self) -> None:
        self.current_slug = ""
        try:
            bootstrap_site_learning(self.site.root)
            run_quality_routines(self.site.root)
        except Exception:
            traceback.print_exc()
        self._start_preview_server()
        self.refresh_all()

    def open_public_site(self) -> None:
        QDesktopServices.openUrl(QUrl(self.site.public_url))

    def _start_owner_collector(self) -> None:
        if self.owner_collector or collector_available():
            self.owner_collector_error = ""
            return
        try:
            self.owner_collector = start_owner_collector(self.site.root)
            self.owner_collector_error = ""
        except OSError as exc:
            self.owner_collector_error = str(exc)

    def _start_preview_server(self) -> None:
        if self.preview_server:
            self.preview_server.shutdown()
            self.preview_server.server_close()
        self.preview_server = StudioServer(("127.0.0.1", 0), self.site.root)
        self.preview_thread = threading.Thread(target=self.preview_server.serve_forever, daemon=True, name="indanya-preview")
        self.preview_thread.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.preview_server:
            self.preview_server.shutdown()
            self.preview_server.server_close()
        if self.owner_collector:
            self.owner_collector.close()
            self.owner_collector = None
        super().closeEvent(event)
