from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from indanya_desktop.outreach import (  # noqa: E402
    bootstrap_outreach_targets,
    default_outreach_profile,
    list_outreach_targets,
    outreach_link_html,
    outreach_message,
    remove_outreach_target,
    update_outreach_status,
    upsert_outreach_target,
)


class OutreachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_bootstrap_adds_researched_starter_targets_only_once(self) -> None:
        self.assertEqual(bootstrap_outreach_targets(self.root), 3)
        self.assertEqual(bootstrap_outreach_targets(self.root), 0)
        targets = list_outreach_targets(self.root)
        self.assertEqual(len(targets), 3)
        self.assertTrue(all(target["status"] == "candidate" for target in targets))

    def test_target_lifecycle_records_contact_and_listing(self) -> None:
        target = upsert_outreach_target(self.root, {
            "name": "掲載先テスト",
            "site_url": "https://example.com/",
            "contact_url": "https://example.com/contact",
            "category": "成人向けアンテナ",
            "status": "candidate",
            "fit_reason": "読者層が近い",
            "notes": "条件確認済み",
        })
        contacted = update_outreach_status(self.root, target["target_id"], "contacted")
        self.assertTrue(contacted["contacted_at"])
        listed = update_outreach_status(self.root, target["target_id"], "listed")
        self.assertTrue(listed["listed_at"])
        remove_outreach_target(self.root, target["target_id"])
        self.assertEqual(list_outreach_targets(self.root), [])

    def test_message_is_personalized_and_contains_complete_site_kit(self) -> None:
        profile = default_outreach_profile(
            "淫談屋",
            "https://eroscope.github.io/sukebesite2025/",
        )
        target = {"name": "テストアンテナ"}
        message = outreach_message(profile, target)
        self.assertIn("テストアンテナ 運営者様", message)
        self.assertIn("淫談屋", message)
        self.assertIn("feed.xml", message)
        self.assertIn("partners.html", message)
        self.assertIn(
            'href="https://eroscope.github.io/sukebesite2025/"',
            outreach_link_html(profile),
        )

    def test_invalid_urls_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            upsert_outreach_target(self.root, {
                "name": "不正URL",
                "site_url": "javascript:alert(1)",
            })


if __name__ == "__main__":
    unittest.main()
