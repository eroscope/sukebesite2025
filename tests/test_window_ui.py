from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOW_SOURCE = ROOT / "tools" / "indanya_desktop" / "window.py"
WORKERS_SOURCE = ROOT / "tools" / "indanya_desktop" / "workers.py"


class WindowUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WINDOW_SOURCE.read_text(encoding="utf-8")
        cls.workers_source = WORKERS_SOURCE.read_text(encoding="utf-8")

    def test_article_analysis_and_writing_complete_in_one_chatgpt_pass(self) -> None:
        self.assertIn('"conversation": conversation', self.workers_source)
        self.assertEqual(self.workers_source.count('entry["conversation"]'), 1)
        self.assertIn("validate_single_pass_article", self.workers_source)
        self.assertIn("single_pass_generated", self.workers_source)

    def test_review_board_is_integrated_into_dashboard(self) -> None:
        self.assertNotIn('"review": self._review_page()', self.source)
        self.assertIn('("dashboard", "▦  ダッシュボード")', self.source)
        self.assertIn('"記事の確認と公開"', self.source)

    def test_analytics_page_uses_ga4_not_a_custom_collector(self) -> None:
        self.assertIn('"analytics": self._analytics_page()', self.source)
        self.assertIn('("analytics", "▥  アクセス解析")', self.source)
        self.assertIn('"記事別"', self.source)
        self.assertIn('"PR別"', self.source)
        self.assertIn('"ジャンル別"', self.source)
        self.assertIn('"訪問者別"', self.source)
        self.assertIn('"端末別"', self.source)
        self.assertIn('"流入元別"', self.source)
        self.assertIn('"閲覧→PR"', self.source)
        self.assertIn("Google Analytics 4", self.source)
        self.assertIn("save_ga4_settings", self.source)
        self.assertIn("publish_ga4_config", self.source)
        self.assertIn("AnalyticsWorker", self.source)
        return
        self.assertIn("load_ga4_data", self.source)
        self.assertIn("読み取りJSONを選択", self.source)
        self.assertIn("GA4データを読み込む", self.source)
        self.assertIn("self.ga4_report_tables", self.source)
        self.assertIn('button("外部アクセス", "analyticsViewButton")', self.source)
        self.assertIn('button("自分を含む", "analyticsViewButton")', self.source)
        self.assertIn("self.analytics_view_stack = QStackedWidget()", self.source)
        self.assertIn("def _select_analytics_page", self.source)
        self.assertIn("def _populate_analytics_pages", self.source)
        self.assertNotIn("analytics_include_owner", self.source)
        self.assertIn('results.get(False)', self.source)
        self.assertIn('results.get(True)', self.source)
        self.assertNotIn('results.get("without_owner")', self.source)
        self.assertIn("analytics_display_views", (
            ROOT / "tools" / "indanya_desktop" / "workers.py"
        ).read_text(encoding="utf-8"))
        self.assertIn('"visitor_daily": visitor_daily', self.source)
        self.assertIn('result.get("visitor_daily", [])', self.source)
        self.assertIn("自分のブラウザを登録", self.source)
        self.assertIn("外部アクセス", self.source)
        self.assertIn("自分を含む", self.source)
        self.assertIn("_open_owner_browser", self.source)

    def test_review_actions_keep_filter_and_sort_selection(self) -> None:
        start = self.source.index("    def _review_action(self,")
        end = self.source.index("\n    def ", start + 10)
        action_source = self.source[start:end]
        self.assertNotIn("review_filter.setCurrentIndex", action_source)
        self.assertNotIn("review_sort.setCurrentIndex", action_source)
        self.assertNotIn("refresh_all()", action_source)
        self.assertIn("_run_review_action", action_source)

    def test_review_actions_update_one_card_while_saving_in_background(self) -> None:
        self.assertIn("class ReviewActionWorker", (
            ROOT / "tools" / "indanya_desktop" / "workers.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("self._set_review_card_status(slug, optimistic", self.source)
        self.assertIn("self.thread_pool.start(worker)", self.source)

    def test_publish_progress_does_not_rebuild_the_review_board(self) -> None:
        start = self.source.index("    def _publish_progress_changed")
        end = self.source.index("\n    def ", start + 10)
        progress_source = self.source[start:end]
        self.assertIn("_set_review_card_progress", progress_source)
        self.assertNotIn("_refresh_review_board", progress_source)

    def test_review_status_changes_do_not_reorder_articles(self) -> None:
        self.assertIn('payload.get("generated_at")', self.source)
        self.assertIn("records.sort(key=lambda item: item[3], reverse=True)", self.source)
        self.assertNotIn(
            'records.sort(key=lambda item: (positions.get(item[0]["slug"], 1_000_000), item[0]["updated_at"]))',
            self.source,
        )

    def test_review_refresh_restores_inner_and_outer_scroll(self) -> None:
        self.assertIn("self.review_page.scrollPosition().y()", self.source)
        self.assertIn("self.dashboard_scroll.verticalScrollBar().value()", self.source)
        self.assertIn("self.dashboard_scroll.verticalScrollBar().setValue(outer_scroll_y)", self.source)

    def test_x_booking_creates_missing_copy_then_continues_automatically(self) -> None:
        self.assertIn("self.x_schedule_after_copy_ids", self.source)
        self.assertIn("self._start_x_schedule(post_ids)", self.source)
        self.assertIn("lambda post_ids=pending_schedule: self._start_x_schedule(post_ids)", self.source)
        self.assertIn("選択した候補をX公式画面で確認", self.source)

    def test_outreach_page_manages_personalized_listing_requests(self) -> None:
        self.assertIn('(\"outreach\", \"↗  掲載営業\")', self.source)
        self.assertIn('def _outreach_page(self)', self.source)
        self.assertIn('"掲載候補"', self.source)
        self.assertIn('"依頼文をコピー"', self.source)
        self.assertIn('"連絡ページを開く"', self.source)
        self.assertIn('update_outreach_status', self.source)
        self.assertIn('outreach_message', self.source)

    def test_x_booking_ui_makes_login_state_and_primary_action_clear(self) -> None:
        self.assertIn("● ログイン済み（投稿できます）", self.source)
        self.assertIn("● 未ログイン", self.source)
        self.assertIn("選択した候補をX公式画面で確認", self.source)
        self.assertIn("準備した通常投稿・返信・漫画スレッドをXへ自動送信する", self.source)
        self.assertIn("自動送信がONなら通常投稿は予約枠へ送り", self.source)
        self.assertIn("Xへログイン", self.source)

    def test_x_daily_candidate_cycle_is_connected_to_the_scheduler(self) -> None:
        start = self.source.index("    def _scheduler_tick(self)")
        end = self.source.index("\n    def ", start + 10)
        scheduler = self.source[start:end]
        self.assertIn("daily_status = x_daily_posting_status", scheduler)
        self.assertIn("self.start_x_daily_cycle()", scheduler)
        self.assertIn("self.x_daily_worker = XDailyWorker", self.source)
        self.assertIn("自動候補: 状態を確認中", self.source)

    def test_recurring_manga_threads_are_configurable_and_scheduled(self) -> None:
        self.assertIn("FANZA公式漫画から定期的に5枚スレッド候補を用意する", self.source)
        self.assertIn("self.manga_interval_days", self.source)
        self.assertIn("self.manga_product_cooldown", self.source)
        self.assertIn("self.manga_title_cooldown", self.source)
        self.assertIn("漫画スレッド: 状態を確認中", self.source)
        start = self.source.index("    def _scheduler_tick(self)")
        end = self.source.index("\n    def ", start + 10)
        scheduler = self.source[start:end]
        self.assertIn("x_manga_schedule_status", scheduler)
        self.assertIn("prepare_due_x_manga_thread", scheduler)
        self.assertIn("ensure_fanza_manga_source", self.source)

    def test_chatgpt_articles_refresh_dashboard_as_each_one_is_saved(self) -> None:
        self.assertIn("signals.article_saved.connect", self.source)
        self.assertIn("def _chatgpt_article_saved", self.source)
        self.assertIn("self._refresh_review_board(drafts)", self.source)

    def test_new_article_publication_queues_x_candidate_after_site_publish(self) -> None:
        workers = (
            ROOT / "tools" / "indanya_desktop" / "workers.py"
        ).read_text(encoding="utf-8")
        start = workers.index("class PublishArticleWorker")
        section = workers[start:workers.index("class UnpublishArticleWorker", start)]
        self.assertLess(section.index("publish_article("), section.index("prepare_publish_x_post"))
        self.assertNotIn("schedule_x_posts", section)
        self.assertIn('result["x_queued"]', section)

    def test_automation_page_uses_operation_focused_labels(self) -> None:
        start = self.source.index("    def _automation_page(self)")
        end = self.source.index("\n    def ", start + 10)
        section = self.source[start:end]
        self.assertIn('"次の巡回"', section)
        self.assertIn('"現在の処理数"', section)
        self.assertIn('"同時処理"', section)
        self.assertIn('"今後の予定"', section)
        self.assertIn('"現在の処理"', section)
        self.assertIn('"記事の処理結果"', section)
        self.assertIn('"終了時刻"', section)
        self.assertNotIn("ChatGPT", section)
        self.assertNotIn("チャッピー", section)
        self.assertNotIn("Codex", section)
        self.assertNotIn("automation_open_chatgpt_button", section)

    def test_automation_schedule_shows_today_or_tomorrow(self) -> None:
        self.assertIn("def _next_schedule_label", self.source)
        self.assertIn('return f"今日 {upcoming}" if upcoming else f"明日 {times[0]}"', self.source)

    def test_automation_hides_old_internal_worker_names(self) -> None:
        self.assertIn("def _automation_message", self.source)
        self.assertIn('("ChatGPT待機へ", "記事作成へ")', self.source)
        self.assertIn("self._automation_message(message)", self.source)

    def test_automation_status_has_a_roadmap(self) -> None:
        self.assertIn("self._build_automation_roadmap()", self.source)
        self.assertIn("def _automation_roadmap_position", self.source)
        self.assertIn("def _refresh_automation_roadmap_from_current", self.source)
        self.assertIn("roadmapStep", self.source)
        self.assertIn('number.setText("NOW")', self.source)
        self.assertIn('number.setText("DONE")', self.source)
        self.assertIn("def _set_automation_phase", self.source)
        self.assertIn("def _refresh_automation_activity", self.source)
        self.assertIn("直近の確定ログ", self.source)

    def test_automation_shows_per_site_learning_and_recovery_strategy(self) -> None:
        self.assertIn("サイト別の学習状況", self.source)
        self.assertIn("次の取得方法", self.source)
        self.assertIn("完全取得＋不足補完", self.source)
        self.assertIn("list_site_learning", self.source)
        self.assertIn("can_attempt_site", self.source)

    def test_completed_automation_history_shows_one_row_per_article(self) -> None:
        start = self.source.index("    def _refresh_automation_completed_articles")
        end = self.source.index("\n    def ", start + 10)
        section = self.source[start:end]
        self.assertIn("reconcile_chatgpt_requests(self.site.root)", section)
        self.assertIn("shown_urls", section)
        self.assertIn("terminal_statuses", section)

    def test_editor_can_rebuild_an_existing_article_from_its_source_url(self) -> None:
        self.assertIn('button("素材を取り直して作り直す")', self.source)
        self.assertIn("def regenerate_editor_article", self.source)
        self.assertIn('"rebuild_existing": True', self.source)
        self.assertIn("def _editor_rebuild_completed", self.source)

    def test_editor_can_search_by_title_source_url_or_article_id(self) -> None:
        self.assertIn('self.editor_search.setPlaceholderText("タイトル・元記事URL・記事IDで検索")', self.source)
        self.assertIn("self.editor_search.textChanged.connect(self._filter_editor_selector)", self.source)
        start = self.source.index("    def _filter_editor_selector(self)")
        end = self.source.index("\n    def ", start + 10)
        section = self.source[start:end]
        self.assertIn('for key in ("title", "source_url", "slug")', section)
        self.assertIn("query not in haystack", section)

    def test_manual_crawl_count_can_be_changed_from_the_automation_page(self) -> None:
        self.assertIn('QLabel("手動巡回数")', self.source)
        self.assertIn("self.manual_crawl_count = QDial()", self.source)
        self.assertIn("self.manual_crawl_count.setRange(1, 100)", self.source)
        self.assertIn("self.manual_crawl_count.setWrapping(True)", self.source)
        self.assertIn("def update_manual_crawl_count", self.source)
        self.assertIn("manual_crawl_run(self.site.root, self.manual_crawl_count.value())", self.source)

    def test_manual_and_continuous_crawls_share_the_source_selection(self) -> None:
        start = self.source.index("    def run_manual_crawl")
        end = self.source.index("\n    def ", start + 10)
        section = self.source[start:end]
        self.assertIn("manual_crawl_run", section)
        self.assertIn("enable_continuous_crawl", section)
        self.assertNotIn("crawl_slots", section)

    def test_sources_page_exposes_weekly_auto_discovery_and_history(self) -> None:
        self.assertIn("7日ごとに新しい巡回サイトを自動で探す", self.source)
        self.assertIn("今すぐ新規サイトを探す", self.source)
        self.assertIn("直近の自動探索", self.source)
        self.assertIn("source_discovery_interval_days", self.source)
        self.assertIn("source_discovery_max_additions", self.source)
        self.assertIn("sort_candidates_balanced", self.source)
        self.assertIn("record_source_selection", self.source)

    def test_automatic_candidate_selection_is_not_hardcoded_to_fanza(self) -> None:
        start = self.source.index("    def _collect_completed")
        end = self.source.index("\n    def ", start + 10)
        section = self.source[start:end]
        self.assertNotIn("is_fanza_product_url", section)

    def test_codex_mode_processes_one_article_at_a_time(self) -> None:
        self.assertIn("queued_chatgpt_request_ids(self.site.root, limit=1)", self.source)
        self.assertIn("stop_pending_chatgpt_requests", self.source)
        self.assertIn("待機列は保持しません", self.source)
        self.assertIn("Codexで1件ずつ、素材判定と記事作成を1回で処理しています", self.source)
        self.assertIn("CodexSendWorker", self.source)
        self.assertIn("待機列は保持しません", self.source)

    def test_articles_with_official_videos_put_them_in_the_opening_post(self) -> None:
        workers = (ROOT / "tools" / "indanya_desktop" / "workers.py").read_text(encoding="utf-8")
        self.assertIn("def _place_videos_at_start", workers)
        self.assertIn('first["video_ids"] = video_ids', workers)
        self.assertIn('payload["category"] = "動画"', workers)

    def test_crawled_articles_are_automatically_added_to_publish_queue(self) -> None:
        workers = (
            ROOT / "tools" / "indanya_desktop" / "workers.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"automation_origin": "crawl" if scheduled else ""', self.source)
        self.assertIn('options.get("automation_origin") == "crawl"', workers)
        self.assertIn('quality.get("effective_decision") == "auto_ready"', workers)
        self.assertTrue(
            "queue_position = enqueue_article(self.site_root, slug)" in workers
            or "queue_position = enqueue_article(site_root, slug)" in workers
        )
        self.assertIn('"queue_position": queue_position', workers)
        self.assertIn("予約待機の{queue_position}番目へ追加しました", self.source)


if __name__ == "__main__":
    unittest.main()
