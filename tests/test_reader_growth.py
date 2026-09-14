import tempfile
import unittest
from pathlib import Path
from tools.indanya_desktop.reader_growth import provenance_markup, source_reference, write_growth_review


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


if __name__ == "__main__":
    unittest.main()
