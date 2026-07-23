from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThreadPool, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
    VIDEO_EMBED_STYLE,
    X_EMBED_STYLE,
    StudioServer,
    _extract_sample_assets,
    build_article,
    list_drafts,
    save_draft,
    update_draft_rights,
)
from indanya_desktop.sites import ManagedSite, SiteRegistry
from indanya_desktop.theme import APP_STYLE
from indanya_desktop.automation import (
    add_source,
    due_crawl_runs,
    due_publish_runs,
    enqueue_article,
    list_candidates,
    load_automation_settings,
    list_sources,
    queue_position_map,
    record_automation_run,
    remove_from_queue,
    remove_source,
    save_automation_settings,
    save_candidates,
    save_sources,
    soft_delete_article,
    update_review_status,
    update_source,
)
from indanya_desktop.workers import (
    BatchDraftWorker,
    CollectCandidatesWorker,
    DownloadVideoWorker,
    GenerateArticleWorker,
    PublishArticleWorker,
    RefineDraftWorker,
    UnpublishArticleWorker,
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


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.registry = SiteRegistry(project_root)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_worker: GenerateArticleWorker | None = None
        self.refine_worker: RefineDraftWorker | None = None
        self.collect_worker: CollectCandidatesWorker | None = None
        self.batch_worker: BatchDraftWorker | None = None
        self.publish_worker: PublishArticleWorker | None = None
        self.unpublish_worker: UnpublishArticleWorker | None = None
        self.publish_progress: QProgressDialog | None = None
        self.publish_queue: list[tuple[str, str]] = []
        self.publish_batch_total = 0
        self.scheduled_collect = False
        self.scheduled_crawl_keys: list[str] = []
        self.scheduled_publish_slugs: list[str] = []
        self.scheduled_publish_key = ""
        self.scheduled_publish_active = False
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        self.review_publish_progress: dict[str, int] = {}
        self.current_slug = ""
        self.preview_videos: dict[str, dict] = {}
        self.video_windows: list[VideoPlayerDialog] = []
        self.video_downloads: list[DownloadVideoWorker] = []
        self.video_progress: list[QProgressDialog] = []
        self.preview_server: StudioServer | None = None
        self.preview_thread: threading.Thread | None = None
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("淫談屋 記事編集室")
        self.resize(1420, 900)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._start_preview_server()
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(60_000)
        self.scheduler_timer.timeout.connect(self._scheduler_tick)
        self.scheduler_timer.start()
        self._ensure_startup_launcher()
        self.switch_page("review")
        self.refresh_all()
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
            "review": self._review_page(),
            "dashboard": self._dashboard_page(),
            "create": self._create_page(),
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
            ("編集部", [("review", "▦  公開前ボード"), ("dashboard", "·  ダッシュボード")]),
            ("制作", [("create", "＋  URLから作成"), ("drafts", "□  記事下書き"), ("editor", "T  記事編集")]),
            ("編集フロー", [("rights", "✓  許可管理"), ("publishing", "↑  公開管理")]),
            ("自動化", [("sources", "◎  情報源"), ("automation", "↻  自動巡回")]),
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
        for key, title in (("drafts", "下書き"), ("rights", "許可待ち"), ("videos", "動画素材"), ("sites", "管理サイト")):
            inner = QVBoxLayout()
            label = QLabel("0", objectName="metric")
            self.metric_labels[key] = label
            inner.addWidget(label)
            inner.addWidget(QLabel(title, objectName="muted"))
            metrics.addWidget(panel(inner), 1)
        layout.addLayout(metrics)
        layout.addSpacing(14)
        quick = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("次の記事を作る", objectName="sectionTitle"))
        left.addWidget(QLabel("気になったページのURLを貼るだけで、Codexが素材選定から下書きまで担当します。", objectName="muted"))
        go = button("URLから記事を作る", "primary")
        go.clicked.connect(lambda: self.switch_page("create"))
        left.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        quick.addWidget(panel(left, True), 1)
        layout.addLayout(quick)
        return self._page_shell(body)

    def _create_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("記事にしたいページを入力", "URL先の本文・画像・動画を回収し、Codexが広告除外、素材選定、タイトル、レス構成まで処理します。"))
        form = QVBoxLayout()
        form.setContentsMargins(20, 18, 20, 18)
        form.addWidget(QLabel("ページURL"))
        row = QHBoxLayout()
        self.source_url = QLineEdit()
        self.source_url.setPlaceholderText("https://example.com/article")
        self.source_url.setMinimumHeight(46)
        row.addWidget(self.source_url, 1)
        self.generate_button = button("Codexに全部任せて作る", "primary")
        self.generate_button.clicked.connect(self.generate_article)
        row.addWidget(self.generate_button)
        form.addLayout(row)
        options = QHBoxLayout()
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
        layout.addWidget(panel(status))
        return self._page_shell(body)

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
        head.addWidget(heading("記事下書き", "Codexが生成した記事を確認して編集します。"), 1)
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
        selector_row = QHBoxLayout()
        self.editor_select = QComboBox()
        self.editor_select.currentIndexChanged.connect(self._editor_selection_changed)
        selector_row.addWidget(self.editor_select, 1)
        load = button("読み込む")
        load.clicked.connect(self.load_editor_draft)
        selector_row.addWidget(load)
        layout.addLayout(selector_row)
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
        actions = QHBoxLayout()
        save = button("変更を保存", "primary")
        save.clicked.connect(self.save_editor_draft)
        self.refine_button = button("Codexで会話を推敲")
        self.refine_button.clicked.connect(self.refine_editor_draft)
        source = button("元記事を開く")
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.editor_source.text())))
        actions.addWidget(save)
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
        details.addWidget(self.registry_path)
        details.addWidget(self.workspace_path)
        layout.addWidget(panel(details))
        return self._page_shell(body)

    def _sources_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("情報源", "自動巡回でURL候補を拾うサイトを登録します。"))
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
        self.sources_table = self._table(["巡回", "名前", "URL", "最終確認"])
        self.sources_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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
        return self._page_shell(body)

    def _automation_page(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(heading("自動巡回", "情報源から候補URLを拾い、選んだものをまとめて下書きにします。"))

        scheduler = QVBoxLayout()
        scheduler.setContentsMargins(16, 14, 16, 14)
        crawl_row = QHBoxLayout()
        self.auto_crawl_enabled = QCheckBox("自動巡回を有効にする")
        crawl_row.addWidget(self.auto_crawl_enabled)
        crawl_row.addSpacing(12)
        crawl_row.addWidget(QLabel("巡回時刻"))
        self.crawl_time_edits: list[QTimeEdit] = []
        for _index in range(3):
            editor = QTimeEdit()
            editor.setDisplayFormat("HH:mm")
            editor.setFixedWidth(86)
            self.crawl_time_edits.append(editor)
            crawl_row.addWidget(editor)
        crawl_row.addWidget(QLabel("1回の記事数"))
        self.auto_draft_limit = QSpinBox()
        self.auto_draft_limit.setRange(1, 20)
        self.auto_draft_limit.setSuffix(" 件")
        crawl_row.addWidget(self.auto_draft_limit)
        crawl_row.addStretch()
        scheduler.addLayout(crawl_row)

        publish_row = QHBoxLayout()
        self.auto_publish_enabled = QCheckBox("予約投稿を有効にする")
        publish_row.addWidget(self.auto_publish_enabled)
        publish_row.addSpacing(12)
        publish_row.addWidget(QLabel("投稿枠"))
        self.publish_slot_controls: list[tuple[QCheckBox, QTimeEdit, QSpinBox]] = []
        for label in ("枠1", "枠2"):
            enabled = QCheckBox(label)
            time_editor = QTimeEdit()
            time_editor.setDisplayFormat("HH:mm")
            time_editor.setFixedWidth(86)
            count = QSpinBox()
            count.setRange(1, 20)
            count.setSuffix(" 件")
            publish_row.addWidget(enabled)
            publish_row.addWidget(time_editor)
            publish_row.addWidget(count)
            self.publish_slot_controls.append((enabled, time_editor, count))
        publish_row.addStretch()
        save_schedule = button("設定を保存", "primary")
        save_schedule.clicked.connect(self.save_scheduler_controls)
        publish_row.addWidget(save_schedule)
        scheduler.addLayout(publish_row)
        self.automation_scheduler_note = QLabel("", objectName="muted")
        scheduler.addWidget(self.automation_scheduler_note)
        layout.addWidget(panel(scheduler, True))

        controls = QHBoxLayout()
        collect = button("候補URLを拾う", "primary")
        collect.clicked.connect(self.collect_auto_candidates)
        draft = button("ONの候補で下書き作成", "primary")
        draft.clicked.connect(self.create_auto_drafts)
        clear = button("候補を整理")
        clear.clicked.connect(self.clean_auto_candidates)
        controls.addWidget(collect)
        controls.addWidget(draft)
        controls.addWidget(clear)
        controls.addStretch()
        controls.addWidget(QLabel("最大"))
        self.collect_limit = QComboBox()
        for value in ("5", "10", "20"):
            self.collect_limit.addItem(f"{value}件/情報源", value)
        self.collect_limit.setCurrentIndex(1)
        controls.addWidget(self.collect_limit)
        layout.addLayout(controls)
        self.candidates_table = self._table(["作成", "スコア", "状態", "タイトル", "情報源", "URL"])
        self.candidates_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.candidates_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.candidates_table)
        self.auto_progress = QProgressBar()
        self.auto_progress.setRange(0, 100)
        layout.addWidget(self.auto_progress)
        self.auto_note = QLabel("候補URLを拾ってください。", objectName="muted")
        self.auto_note.setWordWrap(True)
        layout.addWidget(self.auto_note)
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
        self.stack.setCurrentWidget(self.pages[name])
        titles = {
            "review": ("REVIEW", "公開前ボード"),
            "dashboard": ("OVERVIEW", "ダッシュボード"), "create": ("CREATE", "URLから記事を作成"),
            "drafts": ("DRAFTS", "記事下書き"), "editor": ("EDIT", "記事編集"),
            "rights": ("RIGHTS", "許可管理"), "publishing": ("PUBLISH", "公開管理"),
            "sources": ("SOURCES", "情報源"), "automation": ("AUTOMATION", "自動巡回"),
            "sites": ("SITES", "管理サイト"), "settings": ("SETTINGS", "設定"),
        }
        self.eyebrow.setText(titles[name][0])
        self.page_title.setText(titles[name][1])
        for key, nav in self.nav_buttons.items():
            nav.setChecked(key == name)
        if name in {"review", "dashboard", "drafts", "rights", "publishing", "sites", "editor"}:
            self.refresh_all()

    def refresh_all(self) -> None:
        self._refresh_site_controls()
        drafts = list_drafts(self.site.root)
        self._refresh_metrics(drafts)
        self._refresh_drafts(drafts)
        self._refresh_rights(drafts)
        self._refresh_publishing(drafts)
        self._refresh_editor_selector(drafts)
        self._refresh_sites()
        self._refresh_sources()
        self._refresh_candidates()
        self._refresh_review_board(drafts)
        self.workspace_path.setText(f"現在の作業フォルダ: {self.site.root}")
        logo_path = self.site.root / "assets" / "common" / "indanya-logo.png"
        if logo_path.is_file():
            self.logo.setPixmap(QPixmap(str(logo_path)).scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

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
        destination = self.site.root / ".article-studio" / "board-thumbs" / f"{slug}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(match.group(2), validate=True))
            return QUrl.fromLocalFile(str(destination.resolve())).toString()
        except (OSError, ValueError):
            return ""

    def _refresh_review_board(self, drafts: list[dict] | None = None) -> None:
        if not hasattr(self, "review_view"):
            return
        drafts = drafts if isinstance(drafts, list) else list_drafts(self.site.root)
        positions = queue_position_map(self.site.root)
        selected_filter = str(self.review_filter.currentData() or "unreviewed")
        selected_sort = str(self.review_sort.currentData() or "newest")
        records = []
        for draft in drafts:
            try:
                payload = self._draft_payload(str(draft["slug"]))
            except Exception:
                continue
            status = str(payload.get("review_status") or "unreviewed")
            if draft["slug"] in self.review_publish_progress:
                status = "publishing"
            elif payload.get("published_url") or str(payload.get("editorial_status") or "") == "published":
                status = "published"
            elif draft["slug"] in positions:
                status = "queued"
            if status not in {"unreviewed", "queued", "publishing", "published", "deleted", "failed"}:
                status = "unreviewed"
            if selected_filter != "all" and status != selected_filter:
                continue
            records.append((draft, payload, status))
        if selected_sort == "oldest":
            records.reverse()
        elif selected_sort == "queue":
            records.sort(key=lambda item: (positions.get(item[0]["slug"], 1_000_000), item[0]["updated_at"]))

        labels = {
            "queued": "予約待機",
            "publishing": "公開済み",
            "published": "公開済み",
            "deleted": "消去済み",
            "failed": "公開失敗",
        }
        cards = []
        for draft, payload, status in records:
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
<article class="card">
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
.status-overlay.published,.status-overlay.publishing{{background:rgba(21,94,73,.72)}}.status-overlay.queued{{background:rgba(120,91,24,.72)}}.status-overlay.deleted{{background:rgba(50,50,50,.76)}}.status-overlay.failed{{background:rgba(156,36,27,.74)}}
.publish-progress{{position:relative;height:24px;background:#e5e7e5;overflow:hidden}}.publish-progress i{{position:absolute;inset:0 auto 0 0;background:#168a78;transition:width .2s}}.publish-progress span{{position:relative;z-index:1;display:block;line-height:24px;text-align:center;color:#fff;font-size:10px;font-weight:800;text-shadow:0 1px 2px #444}}
.copy{{padding:13px 14px 8px;flex:1}}.meta{{color:#77736a;font-size:10px}}h2{{margin:7px 0;font-size:17px;line-height:1.5;letter-spacing:0}}p{{margin:0;color:#5e5a52;font-size:11px;line-height:1.65}}
.tags{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}}.tags span{{padding:3px 6px;border:1px solid #d2cfc6;background:#f4f2ec;font-size:9px}}
.actions{{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px 14px;border-top:1px solid #e3e0d8}}.actions a{{padding:7px 10px;border:1px solid #aaa69d;color:#222;text-decoration:none;font-size:11px;font-weight:800}}
.actions .primary{{background:#181a1d;color:#fff;border-color:#181a1d}}.actions .queue{{background:#e7f2ef;color:#126e68;border-color:#9bc9c4}}.actions .danger{{margin-left:auto;background:#fff;color:#b0251d;border-color:#d7aaa6}}
.empty{{padding:80px 20px;background:#fff;border:1px solid #c9c6bd;text-align:center;color:#777}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class="grid">{''.join(cards)}{empty}</main></body></html>"""
        self.review_queue_label.setText(f"予約待機 {len(positions)}件")
        self.review_view.setHtml(document, QUrl.fromLocalFile(str(self.site.root.resolve()) + os.sep))

    def _review_action(self, action: str, slug: str) -> None:
        try:
            if action == "edit":
                self.edit_publish_article(slug)
            elif action == "publish":
                self.review_filter.setCurrentIndex(self.review_filter.findData("all"))
                self.start_publish(slug)
            elif action == "queue":
                position = enqueue_article(self.site.root, slug)
                self.review_filter.setCurrentIndex(self.review_filter.findData("all"))
                self.scheduler_note.setText(f"予約待機 #{position} に追加しました。")
                self.refresh_all()
            elif action == "dequeue":
                remove_from_queue(self.site.root, slug)
                self.review_filter.setCurrentIndex(self.review_filter.findData("all"))
                self.scheduler_note.setText("予約待機から外しました。")
                self.refresh_all()
            elif action == "delete":
                soft_delete_article(self.site.root, slug)
                self.review_filter.setCurrentIndex(self.review_filter.findData("all"))
                self.scheduler_note.setText("記事を消去済みに移しました。")
                self.refresh_all()
            elif action == "restore":
                update_review_status(self.site.root, slug, "unreviewed")
                self.review_filter.setCurrentIndex(self.review_filter.findData("all"))
                self.scheduler_note.setText("未判別へ戻しました。")
                self.refresh_all()
            elif action == "open":
                self.open_published_article(slug)
        except Exception as exc:
            QMessageBox.critical(self, "操作できません", str(exc))

    def _load_scheduler_controls(self) -> None:
        settings = load_automation_settings(self.site.root)
        self.auto_crawl_enabled.setChecked(bool(settings.get("auto_crawl_enabled", True)))
        crawl_times = list(settings["crawl_times"])
        defaults = ["06:00", "12:00", "18:00"]
        for index, editor in enumerate(self.crawl_time_edits):
            editor.setTime(QTime.fromString(
                crawl_times[index] if index < len(crawl_times) else defaults[index],
                "HH:mm",
            ))
        self.auto_draft_limit.setValue(int(settings["auto_draft_limit"]))
        self.auto_publish_enabled.setChecked(bool(settings.get("publish_enabled", True)))
        slots = list(settings["publish_slots"])
        slot_defaults = [
            {"time": "08:00", "count": 2},
            {"time": "20:00", "count": 2},
        ]
        for index, (enabled, time_editor, count) in enumerate(self.publish_slot_controls):
            slot = slots[index] if index < len(slots) else slot_defaults[index]
            enabled.setChecked(index < len(slots))
            time_editor.setTime(QTime.fromString(str(slot["time"]), "HH:mm"))
            count.setValue(int(slot["count"]))

    def save_scheduler_controls(self) -> None:
        crawl_times = sorted({
            editor.time().toString("HH:mm") for editor in self.crawl_time_edits
        })
        publish_slots = sorted(
            (
                {
                    "time": time_editor.time().toString("HH:mm"),
                    "count": count.value(),
                }
                for enabled, time_editor, count in self.publish_slot_controls
                if enabled.isChecked()
            ),
            key=lambda item: item["time"],
        )
        if self.auto_publish_enabled.isChecked() and not publish_slots:
            QMessageBox.warning(self, "予約投稿を確認", "予約投稿を有効にする場合は、投稿枠を1つ以上ONにしてください。")
            return
        settings = load_automation_settings(self.site.root)
        settings.update({
            "auto_crawl_enabled": self.auto_crawl_enabled.isChecked(),
            "crawl_times": crawl_times,
            "auto_draft_limit": self.auto_draft_limit.value(),
            "publish_enabled": self.auto_publish_enabled.isChecked(),
            "publish_slots": publish_slots,
        })
        save_automation_settings(self.site.root, settings)
        self.scheduler_note.setText("自動巡回と予約投稿の設定を保存しました。")
        self.automation_scheduler_note.setText("設定を保存しました。次の時刻から自動で動きます。")

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
            launcher.write_text(
                '@echo off\r\n'
                f'start "" /min "{Path(sys.executable).resolve()}" --background --site-root "{self.site.root}"\r\n',
                encoding="utf-8",
                newline="",
            )
        except OSError:
            pass

    def _scheduler_tick(self) -> None:
        if self.publish_worker or self.unpublish_worker or self.collect_worker or self.batch_worker:
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
        crawl_runs = due_crawl_runs(self.site.root)
        if crawl_runs:
            self.scheduled_collect = True
            self.scheduled_crawl_keys = crawl_runs
            self.collect_auto_candidates(scheduled=True)

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
        rights = {"unconfirmed": "未確認", "requested": "確認中", "confirmed": "許可済み", "rejected": "使用不可"}
        for row, draft in enumerate(drafts):
            values = [draft["title"], draft["category"], f"画像 {draft['image_count']} / 動画 {draft['video_count']}", rights.get(draft["rights_status"], draft["rights_status"]), draft["updated_at"][:16].replace("T", " ")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, draft["slug"])
                self.draft_table.setItem(row, col, item)

    def _refresh_rights(self, drafts: list[dict]) -> None:
        self.rights_table.setRowCount(len(drafts))
        labels = {"unconfirmed": "未確認", "requested": "確認中", "confirmed": "許可済み", "rejected": "使用不可"}
        for row, draft in enumerate(drafts):
            values = [draft["title"], labels.get(draft["rights_status"], draft["rights_status"]), draft["rights_contact"] or "未入力", draft["source_url"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, draft["slug"])
                self.rights_table.setItem(row, col, item)

    def _refresh_publishing(self, drafts: list[dict]) -> None:
        self.publish_table.setRowCount(len(drafts))
        rights_labels = {"unconfirmed": "未確認", "requested": "確認中", "confirmed": "許可済み", "rejected": "使用不可"}
        status_labels = {"draft": "下書き", "ready": "公開可能", "published": "公開済み", "archived": "非公開"}
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
        selected = self.current_slug or self.editor_select.currentData()
        self.editor_select.blockSignals(True)
        self.editor_select.clear()
        self.editor_select.addItem("下書きを選択", "")
        for draft in drafts:
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
                str(source.get("url") or ""),
                str(source.get("last_checked_at") or "未巡回")[:16].replace("T", " "),
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, source.get("source_id", ""))
                self.sources_table.setItem(row, col, item)

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
                {"new": "候補", "drafted": "下書き済み", "ignored": "除外"}.get(status, status),
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

    def collect_auto_candidates(self, scheduled: bool = False) -> None:
        if self.collect_worker:
            return
        self.save_auto_sources()
        self.scheduled_collect = scheduled
        self.auto_progress.setValue(1)
        self.auto_note.setText("自動巡回で候補URLを収集中です。" if scheduled else "候補URLを収集中です。")
        self.collect_worker = CollectCandidatesWorker(self.site.root, int(self.collect_limit.currentData()))
        self.collect_worker.signals.progress.connect(self._auto_progress_changed)
        self.collect_worker.signals.completed.connect(self._collect_completed)
        self.collect_worker.signals.failed.connect(self._auto_failed)
        self.thread_pool.start(self.collect_worker)

    def _auto_progress_changed(self, value: int, message: str) -> None:
        if hasattr(self, "auto_progress"):
            self.auto_progress.setValue(value)
            self.auto_note.setText(message)

    def _collect_completed(self, result: dict) -> None:
        self.collect_worker = None
        self.auto_progress.setValue(100)
        self.auto_note.setText(f"{result.get('count', 0)}件の候補URLを拾いました。")
        self.refresh_all()
        if not self.scheduled_collect:
            return
        for key in self.scheduled_crawl_keys:
            record_automation_run(self.site.root, "crawl", key)
        self.scheduled_crawl_keys = []
        settings = load_automation_settings(self.site.root)
        limit = int(settings.get("auto_draft_limit") or 3)
        candidates = sorted(
            (
                item for item in list_candidates(self.site.root)
                if item.get("status") == "new" and int(item.get("score") or 0) >= 22
            ),
            key=lambda item: (
                int(item.get("score") or 0),
                str(item.get("discovered_at") or ""),
            ),
            reverse=True,
        )
        urls = [str(item.get("url") or "") for item in candidates[:limit] if item.get("url")]
        if urls:
            self._start_batch_drafts(urls, scheduled=True)
            return
        self.scheduled_collect = False
        self.scheduler_note.setText("自動巡回完了。今回、新しく記事にする候補はありませんでした。")
        QTimer.singleShot(500, self._scheduler_tick)

    def create_auto_drafts(self) -> None:
        if self.batch_worker:
            return
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
            return
        self._start_batch_drafts(urls, scheduled=False)

    def _start_batch_drafts(self, urls: list[str], scheduled: bool) -> None:
        if self.batch_worker or not urls:
            return
        self.scheduled_collect = scheduled
        self.auto_progress.setValue(1)
        prefix = "自動巡回から" if scheduled else ""
        self.auto_note.setText(f"{prefix}{len(urls)}件を下書き生成します。")
        self.batch_worker = BatchDraftWorker(self.site.root, urls, "auto", "auto")
        self.batch_worker.signals.progress.connect(self._auto_progress_changed)
        self.batch_worker.signals.completed.connect(self._batch_completed)
        self.batch_worker.signals.failed.connect(self._auto_failed)
        self.thread_pool.start(self.batch_worker)

    def _batch_completed(self, result: dict) -> None:
        self.batch_worker = None
        self.auto_progress.setValue(100)
        failed = int(result.get("failed_count") or 0)
        suffix = f"（失敗 {failed}件）" if failed else ""
        self.auto_note.setText(f"{result.get('count', 0)}件の下書きを作成しました。{suffix}")
        was_scheduled = self.scheduled_collect
        self.scheduled_collect = False
        self.refresh_all()
        if was_scheduled:
            self.scheduler_note.setText(
                f"自動巡回完了。確認待ちへ {result.get('count', 0)}件追加しました。{suffix}"
            )
            QTimer.singleShot(500, self._scheduler_tick)

    def _auto_failed(self, message: str) -> None:
        self.collect_worker = None
        self.batch_worker = None
        was_scheduled = self.scheduled_collect
        if was_scheduled:
            for key in self.scheduled_crawl_keys:
                record_automation_run(self.site.root, "crawl", key)
            self.scheduled_crawl_keys = []
        self.scheduled_collect = False
        self.auto_progress.setValue(100)
        self.auto_note.setText(f"自動処理失敗: {message}")
        if was_scheduled:
            self.scheduler_note.setText("自動巡回でエラーが発生しました。次の巡回時刻に再開します。")

    def clean_auto_candidates(self) -> None:
        candidates = [item for item in list_candidates(self.site.root) if item.get("status") == "new"]
        save_candidates(self.site.root, candidates[:200])
        self.auto_note.setText("下書き済みや古い候補を整理しました。")
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

    def generate_article(self) -> None:
        url = self.source_url.text().strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URLを確認", "http:// または https:// から始まるURLを入力してください。")
            return
        self.generate_button.setEnabled(False)
        self.generate_result.setText("")
        self.generate_progress.setValue(2)
        self.generate_percent.setText("2%")
        self.active_worker = GenerateArticleWorker(self.site.root, url, str(self.category_combo.currentData()), str(self.reply_combo.currentData()))
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
        path = self.site.root / ".article-studio" / "drafts" / f"{slug}.json"
        return json.loads(path.read_text(encoding="utf-8"))

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
            self._render_preview(payload)
            self.refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

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
        for label, value in (("未確認", "unconfirmed"), ("確認中", "requested"), ("許可済み", "confirmed"), ("使用不可", "rejected")):
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
        existing = str(payload.get("editorial_status") or payload.get("status") or "") == "published"
        action = "更新" if existing else "公開"
        self.publish_note.setText(f"「{payload.get('title', slug)}」を{action}しています。")
        self._refresh_review_board()
        self.publish_worker = PublishArticleWorker(self.site.root, payload, self.site)
        self.publish_worker.signals.progress.connect(self._publish_progress_changed)
        self.publish_worker.signals.completed.connect(self._publish_completed)
        self.publish_worker.signals.failed.connect(self._publish_failed)
        self.thread_pool.start(self.publish_worker)

    def _publish_progress_changed(self, value: int, message: str) -> None:
        if self.publish_current_slug:
            self.review_publish_progress[self.publish_current_slug] = value
            self.scheduler_note.setText(message)
            self._refresh_review_board()
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
        if slug:
            remove_from_queue(self.site.root, slug, "published")
        was_scheduled = self.publish_from_schedule
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        self.refresh_all()
        if self.stack.currentWidget() == self.pages["editor"]:
            self.load_editor_draft()
        if was_scheduled:
            self.scheduler_note.setText(f"予約公開完了: {result.get('url', '')}")
            self._start_next_scheduled_publish()
        elif self.publish_queue:
            self.publish_note.setText(f"公開/更新完了: {result.get('url', '')}")
            self._start_next_publish_in_queue()
        elif self.publish_batch_total:
            self.publish_batch_total = 0
            self.publish_note.setText("まとめて公開/更新が完了しました。")
        else:
            self.publish_note.setText(f"公開/更新完了: {result.get('url', '')}")

    def _publish_failed(self, message: str) -> None:
        if self.publish_progress:
            self.publish_progress.close()
            self.publish_progress = None
        self.publish_worker = None
        slug = self.publish_current_slug
        self.review_publish_progress.pop(slug, None)
        was_scheduled = self.publish_from_schedule
        self.publish_current_slug = ""
        self.publish_from_schedule = False
        if slug:
            remove_from_queue(self.site.root, slug, "failed")
            update_review_status(self.site.root, slug, "failed", message=message)
        if was_scheduled:
            self.scheduler_note.setText(f"予約公開失敗: {message}。次の記事へ進みます。")
            self._start_next_scheduled_publish()
            return
        self.publish_queue = []
        self.publish_batch_total = 0
        self.publish_note.setText(f"公開/更新失敗: {message}")

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
            f"「{payload.get('title', slug)}」を公開サイトから削除します。\n下書きはアプリに残ります。よろしいですか？",
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
            self.publish_note.setText("非公開にしました。下書きは残っています。")

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
        self._start_preview_server()
        self.refresh_all()

    def open_public_site(self) -> None:
        QDesktopServices.openUrl(QUrl(self.site.public_url))

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
        super().closeEvent(event)
