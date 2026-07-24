from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOW_SOURCE = ROOT / "tools" / "indanya_desktop" / "window.py"


class WindowUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WINDOW_SOURCE.read_text(encoding="utf-8")

    def test_review_board_is_integrated_into_dashboard(self) -> None:
        self.assertNotIn('"review": self._review_page()', self.source)
        self.assertIn('("dashboard", "▦  ダッシュボード")', self.source)
        self.assertIn('"記事の確認と公開"', self.source)

    def test_review_actions_keep_filter_and_sort_selection(self) -> None:
        start = self.source.index("    def _review_action")
        end = self.source.index("\n    def ", start + 10)
        action_source = self.source[start:end]
        self.assertNotIn("review_filter.setCurrentIndex", action_source)
        self.assertNotIn("review_sort.setCurrentIndex", action_source)

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


if __name__ == "__main__":
    unittest.main()
