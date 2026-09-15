from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from article_studio import JST
from indanya_desktop import social_x as social
from indanya_desktop.sitemap_health import search_console_observation_summary
from indanya_desktop.x_search_health import (
    load_reply_scan_health, reply_scan_summary, search_page_state,
)


class GrowthDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 9, 16, 1, tzinfo=JST)

    def test_zero_age_is_not_a_day_old(self):
        fresh = {"target_age_hours": 0, "views": 400, "likes": 5}
        old = {**fresh, "target_age_hours": 24}
        self.assertTrue(social._reply_has_traffic(fresh, social.DEFAULT_X_SETTINGS))
        self.assertFalse(social._reply_has_traffic(old, social.DEFAULT_X_SETTINGS))
        self.assertGreater(social._reply_traffic_score(fresh), social._reply_traffic_score(old))

    def test_missing_age_does_not_get_fresh_post_bonus(self):
        self.assertFalse(social._reply_has_traffic({"views": 400, "likes": 5}, social.DEFAULT_X_SETTINGS))

    def test_post_url_comes_from_timestamp_not_quoted_media(self):
        tweet = MagicMock()
        timestamp = MagicMock()
        timestamp.count.return_value = 1
        timestamp.nth.return_value.get_attribute.return_value = "/owner/status/123"
        media = MagicMock()
        media.count.return_value = 1
        media.nth.return_value.get_attribute.return_value = "/someone_else/status/456/photo/1"
        tweet.locator.side_effect = lambda selector: timestamp if ":has(time)" in selector else media
        self.assertEqual("https://x.com/owner/status/123", social._tweet_status_url(tweet))
        timestamp.count.return_value = 0
        self.assertEqual("", social._tweet_status_url(tweet))

    def test_absolute_timestamp_url_is_supported(self):
        tweet = MagicMock()
        tweet.locator.return_value.count.return_value = 1
        tweet.locator.return_value.nth.return_value.get_attribute.return_value = "https://x.com/owner/status/123?s=20"
        self.assertEqual("https://x.com/owner/status/123", social._tweet_status_url(tweet))

    def test_search_errors_are_not_zero_results(self):
        cases = [
            ("https://x.com/i/flow/login", "", 0, "login_required"),
            ("https://x.com/account/access", "", 0, "login_required"),
            ("https://x.com/search", "Rate limit exceeded", 0, "rate_limited"),
            ("https://x.com/search", "問題が発生しました", 0, "load_error"),
            ("https://x.com/search", "No results for these terms", 0, "empty"),
            ("https://x.com/search", "検索結果はありません", 0, "empty"),
            ("https://x.com/search", "", 0, "unverified"),
            ("https://x.com/search", "", 2, "results"),
        ]
        for url, text, count, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, search_page_state(url, text, count))

    def test_scan_failure_persists_diagnostics(self):
        with patch.object(social, "_collect_x_contest_candidates", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                social.collect_x_contest_candidates(self.root)
        report = load_reply_scan_health(self.root)
        self.assertEqual("error", report["status"])
        self.assertEqual("RuntimeError", report["error_type"])
        self.assertIn("取得失敗", reply_scan_summary(report))
        self.assertNotIn("offline", str(report))

    def test_unreadable_search_is_not_a_successful_scan(self):
        def scan(_root, _progress, report):
            report["page_states"] = {"unverified": 2}
            return []
        with patch.object(social, "_collect_x_contest_candidates", side_effect=scan):
            with self.assertRaisesRegex(RuntimeError, "候補0件とは判定していません"):
                social.collect_x_contest_candidates(self.root)
        self.assertEqual("error", load_reply_scan_health(self.root)["status"])

    def test_verified_empty_search_is_a_successful_scan(self):
        def scan(_root, _progress, report):
            report["page_states"] = {"empty": 2}
            return []
        with patch.object(social, "_collect_x_contest_candidates", side_effect=scan):
            self.assertEqual([], social.collect_x_contest_candidates(self.root))
        self.assertEqual("checked", load_reply_scan_health(self.root)["status"])

    def test_partial_search_is_not_reported_fully_checked(self):
        def scan(_root, _progress, report):
            report["page_states"] = {"results": 1, "load_error": 1}
            report["scanned_rows"] = 5
            report["rejections"] = {"not_solicitation": 5}
            return []
        with patch.object(social, "_collect_x_contest_candidates", side_effect=scan):
            social.collect_x_contest_candidates(self.root)
        report = load_reply_scan_health(self.root)
        self.assertEqual("partial", report["status"])
        self.assertIn("募集条件外5件", reply_scan_summary(report))

    def test_logged_out_search_records_login_required(self):
        with patch.object(social, "x_login_ready", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "ログイン"):
                social.collect_x_contest_candidates(self.root)
        report = load_reply_scan_health(self.root)
        self.assertEqual(1, report["page_states"]["login_required"])

    def test_browser_closes_and_search_stops_on_rate_limit(self):
        manager = MagicMock()
        api = manager.__enter__.return_value
        context = api.chromium.launch_persistent_context.return_value
        page = MagicMock()
        context.pages = [page]
        page.url = "https://x.com/search"
        page.locator.return_value.count.return_value = 0
        page.locator.return_value.inner_text.return_value = "Rate limit exceeded"
        with ExitStack() as stack:
            for name, value in {
                "sync_playwright": manager,
                "x_login_ready": True,
                "load_x_settings": social.DEFAULT_X_SETTINGS,
                "load_x_growth_state": {},
                "_known_recruiter_queries": [],
                "require_x_page_account": None,
                "_contest_search_plans": [("photo event", "live"), ("another event", "live")],
            }.items():
                stack.enter_context(patch.object(social, name, return_value=value))
            with self.assertRaisesRegex(RuntimeError, "再検索を中断"):
                social.collect_x_contest_candidates(self.root)
        context.close.assert_called_once()
        self.assertEqual(2, page.goto.call_count)
        self.assertEqual({"rate_limited": 1}, load_reply_scan_health(self.root)["page_states"])

    def test_long_solicitation_is_not_truncated_before_revalidation(self):
        text = "Event details. " * 25 + "Send a photo in a reply."
        tweet = MagicMock()
        tweet.locator.return_value.first.inner_text.return_value = text
        tweet.inner_text.return_value = text
        with ExitStack() as stack:
            stack.enter_context(patch.object(social, "_trend_text_allowed", return_value=True))
            stack.enter_context(patch.object(social, "_reply_solicitation_text_allowed", return_value=True))
            stack.enter_context(patch.object(social, "_tweet_status_url", return_value="https://x.com/host/status/123"))
            stack.enter_context(patch.object(social, "_x_status_created_at", return_value=datetime.now(JST) - timedelta(hours=1)))
            stack.enter_context(patch.object(social, "_locator_metric", return_value=5000))
            result = social._contest_sample(tweet, social.DEFAULT_X_SETTINGS)
        self.assertIsNotNone(result)
        self.assertTrue(result["topic"].endswith("Send a photo in a reply."))

    def reach_status(self, *, health=None, state=None, entries=None, rows=None):
        with ExitStack() as stack:
            for name, value in {
                "load_effective_x_settings": {**social.DEFAULT_X_SETTINGS, "reach_interval_hours": 48},
                "load_x_settings": social.DEFAULT_X_SETTINGS,
                "load_x_health_state": health or {"classification": "healthy", "risk_level": 0},
                "load_x_auto_state": state or {},
                "list_x_posts": rows or [],
                "_latest_av_shelf_campaign_entries": entries if entries is not None else [
                    {"product_id": "fixture001", "status_url": "https://x.com/owner/status/1"},
                ],
            }.items():
                stack.enter_context(patch.object(social, name, return_value=value))
            return social.x_reach_schedule_status(self.root, self.now)

    def test_old_error_is_not_current_blocking_reason(self):
        status = self.reach_status(state={"reach_last_error": "old missing sample", "reach_next_retry_at": "2026-09-11T05:00:00+09:00"})
        self.assertTrue(status["due"])
        self.assertEqual([], status["blockers"])
        self.assertEqual("old missing sample", status["previous_error"])

    def test_reports_health_and_candidate_shortage_separately(self):
        status = self.reach_status(health={"classification": "restricted", "risk_level": 3}, entries=[])
        self.assertFalse(status["due"])
        self.assertIn("アカウント診断", status["blocking_reason"])
        self.assertIn("未使用の候補", status["blocking_reason"])

    def test_retry_time_is_not_displayed_as_ready_now(self):
        status = self.reach_status(state={"reach_next_retry_at": (self.now + timedelta(hours=3)).isoformat()})
        self.assertFalse(status["due"])
        self.assertEqual((self.now + timedelta(hours=3)).isoformat(), status["next_at"])

    def test_unverified_post_is_not_retried(self):
        status = self.reach_status(rows=[{"delivery_mode": "reach", "status": "delivery_unverified", "post_id": "pending"}])
        self.assertFalse(status["due"])
        self.assertIn("重複再送なし", status["blocking_reason"])

    def test_google_live_fetch_does_not_override_indexing_report(self):
        text = search_console_observation_summary({
            "status": "fetch_failed", "observed_at": "2026-09-16T00:30:00+09:00",
            "live_test": {"page_fetch": "successful", "tested_at": "2026-09-15T23:48:16+09:00"},
            "page_indexing": {"indexed_pages": 1, "report_date": "2026-09-04"},
        })
        self.assertIn("取得失敗表示", text)
        self.assertIn("実取得テスト: 成功", text)
        self.assertIn("2026-09-04時点", text)
        self.assertIn("現在値は未確認", text)

    def test_google_count_without_report_date_is_not_current(self):
        text = search_console_observation_summary({"page_indexing": {"indexed_pages": 1}})
        self.assertIn("集計日不明", text)
        self.assertIn("現在値は未確認", text)


if __name__ == "__main__":
    unittest.main()
