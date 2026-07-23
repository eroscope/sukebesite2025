from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.sites import SiteRegistry  # noqa: E402
from indanya_desktop.workers import _mark_ready_to_publish, _select_article_images  # noqa: E402
from indanya_desktop.browser_capture import _usable_final_url, _video_priority  # noqa: E402


class SiteRegistryTests(unittest.TestCase):
    def test_generated_desktop_articles_start_publishable(self) -> None:
        payload = {
            "rights_status": "unconfirmed",
            "adult_confirmed": False,
            "rights_confirmed": False,
            "privacy_confirmed": False,
            "source_confirmed": False,
        }
        updated = _mark_ready_to_publish(payload)
        self.assertEqual("confirmed", updated["rights_status"])
        self.assertTrue(updated["adult_confirmed"])
        self.assertTrue(updated["rights_confirmed"])
        self.assertTrue(updated["privacy_confirmed"])
        self.assertTrue(updated["source_confirmed"])

    def test_article_image_selection_follows_codex_and_avoids_advertisements(self) -> None:
        source = {
            "recommended_image_ids": ["thumb", "main-1", "main-2"],
            "recommended_thumbnail_ids": ["thumb"],
            "recommended_body_image_ids": ["main-1", "main-2"],
            "images": [
                {
                    "id": "thumb",
                    "url": "https://example.com/mosaic-thumbnail.jpg",
                    "width": 240,
                    "height": 135,
                    "source_score": -80,
                    "ai_verdict": "article",
                    "ai_role": "article_thumbnail",
                    "ai_recommended_use": "thumbnail",
                },
                {
                    "id": "main-1",
                    "url": "https://example.com/uploads/body-a.jpg",
                    "width": 800,
                    "height": 560,
                    "source_score": 90,
                    "ai_verdict": "article",
                    "ai_role": "article_main",
                    "ai_recommended_use": "body",
                    "ai_relevance_score": 94,
                },
                {
                    "id": "main-2",
                    "url": "https://example.com/uploads/body-b.jpg",
                    "width": 800,
                    "height": 540,
                    "source_score": 80,
                    "ai_verdict": "article",
                    "ai_role": "article_gallery",
                    "ai_recommended_use": "body",
                    "ai_relevance_score": 90,
                },
                {
                    "id": "ad",
                    "url": "https://example.com/feedly-follow-banner.webp",
                    "width": 131,
                    "height": 56,
                    "source_score": -120,
                    "ai_verdict": "advertisement",
                },
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("thumb", selected[0])
        self.assertEqual(["main-1", "main-2"], selected[1:])
        self.assertIn("main-2", selected)
        self.assertNotIn("ad", selected)

    def test_article_image_selection_does_not_replace_codex_rejection(self) -> None:
        source = {
            "recommended_image_ids": [],
            "images": [{
                "id": "large-ad",
                "url": "https://example.com/large.jpg",
                "width": 1600,
                "height": 900,
                "ai_verdict": "advertisement",
                "ai_relevance_score": 5,
            }],
        }
        self.assertEqual([], _select_article_images(source))

    def test_browser_video_candidates_prioritize_media_before_iframes(self) -> None:
        items = [
            {"kind": "iframe", "urls": ["https://ads.example/player"]},
            {"kind": "direct", "urls": ["https://cdn.example/movie.mp4"]},
            {"kind": "network", "urls": ["https://cdn.example/stream?id=1"]},
        ]
        ordered = sorted(items, key=_video_priority)
        self.assertEqual(["direct", "network", "iframe"], [item["kind"] for item in ordered])

    def test_browser_error_page_keeps_requested_url(self) -> None:
        fallback = "https://example.com/story"
        self.assertEqual(fallback, _usable_final_url("chrome-error://chromewebdata/", fallback))

    def test_default_site_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_data = Path(temporary) / "appdata"
            with patch.dict(os.environ, {"APPDATA": str(app_data)}):
                registry = SiteRegistry(ROOT)
                self.assertEqual("淫談屋", registry.active.name)
                self.assertEqual(ROOT.resolve(), registry.active.root)
                reloaded = SiteRegistry(ROOT)
                self.assertEqual(registry.active_id, reloaded.active_id)

    def test_add_switch_and_remove_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            app_data = temporary_root / "appdata"
            second_root = temporary_root / "second"
            second_root.mkdir()
            with patch.dict(os.environ, {"APPDATA": str(app_data)}):
                registry = SiteRegistry(ROOT)
                second = registry.upsert({
                    "name": "2つ目のサイト",
                    "public_url": "https://example.com/",
                    "local_path": str(second_root),
                    "repository_url": "https://github.com/example/site",
                    "provider": "GitHub Pages",
                })
                self.assertEqual(second.site_id, registry.active_id)
                self.assertEqual(2, len(registry.sites))
                registry.remove(second.site_id)
                self.assertEqual(1, len(registry.sites))
                saved = json.loads(registry.path.read_text(encoding="utf-8"))
                self.assertEqual("indanya", saved["active_id"])


if __name__ == "__main__":
    unittest.main()
