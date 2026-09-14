import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from tools.indanya_desktop.reader_growth import build_growth_comparison, growth_comparison_text, provenance_markup, source_reference, write_growth_review


class ReaderGrowthTests(unittest.TestCase):
    def test_resolved_source_wins_and_secrets_are_not_published(self):
        payload = {"source_url": "https://relay.example/old", "resolved_source_url": "https://source.example/story?id=1&utm_source=qa&token=secret#fragment"}
        self.assertEqual(source_reference(payload), "https://source.example/story?id=1")
        self.assertNotIn("secret", provenance_markup(payload))
        self.assertEqual(provenance_markup({"source_url": "javascript:alert(1)"}), "")
        self.assertEqual(provenance_markup({"source_url": "https://user:password@example.com/"}), "")

    def test_missing_measurement_is_not_zero_and_never_changes_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = write_growth_review(root)
            self.assertEqual(unknown["status"], "measurement_unavailable")
            self.assertEqual(unknown["site_summary"], {})
            small = write_growth_review(root, {"external": {"site_summary": {"sessions": 15}, "summary": {"pageViews": 20}}})
            self.assertEqual(small["status"], "small_sample")
            self.assertFalse(small["automatic_frequency_change"])

    def comparison_report(self):
        days = [(date(2026, 8, 29) + timedelta(days=i)).strftime("%Y%m%d") for i in range(14)]
        return {
            "generated_at": "2026-09-14T12:00:00+09:00", "start_date": "16daysAgo", "end_date": "3daysAgo",
            "measurement_quality": {key: {"time_zone": "Asia/Tokyo"} for key in ("daily_site", "daily_funnel")},
            "external": {"daily_site": [{"date": day, "sessions": 100 if i < 7 else 50, "screenPageViews": 200, "engagedSessions": 20} for i, day in enumerate(days)],
                         "daily_funnel": [{"date": day, "eventName": "related_article_click", "eventCount": 10} for day in days]},
        }

    def test_aligned_weeks_exclude_recent_partial_data(self):
        report = self.comparison_report()
        report["external"]["daily_site"].append({"date": "20260914", "sessions": 99999})
        comparison = build_growth_comparison(report)
        self.assertEqual(comparison["current"]["end"], "2026-09-11")
        self.assertEqual(comparison["previous"]["sessions"], 700)
        self.assertEqual(comparison["current"]["sessions"], 350)
        self.assertEqual(comparison["changes_percent"]["sessions"], -50)
        self.assertEqual(comparison["signals"], ["acquisition_decline_observed"])
        self.assertIsNone(comparison["current"]["pr_clicks_per_100_impressions"])
        self.assertIn("350", growth_comparison_text(comparison))

    def test_unknown_or_limited_data_does_not_claim_cause(self):
        self.assertEqual(build_growth_comparison({})["status"], "measurement_unavailable")
        for flag in ("thresholded", "sampled", "data_loss", "truncated", "empty_reason"):
            report = self.comparison_report()
            report["measurement_quality"]["daily_funnel"][flag] = True
            result = build_growth_comparison(report)
            self.assertEqual(result["status"], "measurement_limited")
            self.assertFalse(result["signals"])
        report = self.comparison_report()
        report["start_date"] = "7daysAgo"
        self.assertEqual(build_growth_comparison(report)["status"], "insufficient_period")

    def test_small_sample_and_zero_baseline_are_not_evidence(self):
        report = self.comparison_report()
        for row in report["external"]["daily_site"]:
            row["sessions"] = 0
        result = build_growth_comparison(report)
        self.assertEqual(result["status"], "small_sample")
        self.assertIsNone(result["changes_percent"]["sessions"])
        self.assertFalse(result["signals"])

    def test_short_manual_reports_do_not_replace_daily_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_growth_review(root, self.comparison_report())
            path = root / ".article-studio/reader-growth/comparison-latest.json"
            saved = path.read_bytes()
            write_growth_review(root, {"external": {"site_summary": {"sessions": 1}}})
            self.assertEqual(saved, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
