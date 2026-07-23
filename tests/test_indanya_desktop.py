from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.sites import SiteRegistry  # noqa: E402
from indanya_desktop.workers import (  # noqa: E402
    _capture_and_analyze_source,
    _mark_ready_to_publish,
    _select_article_images,
)
from indanya_desktop.browser_capture import _usable_final_url, _video_canvas_frame, _video_priority  # noqa: E402


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

        self.assertEqual("thumb", selected["thumbnail_id"])
        self.assertEqual(["main-1", "main-2"], selected["body_ids"])
        self.assertNotIn("thumb", selected["body_ids"])
        self.assertNotIn("ad", selected["body_ids"])

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
        self.assertEqual(
            {"thumbnail_id": "", "body_ids": []},
            _select_article_images(source),
        )

    def test_gateway_follow_keeps_previous_link_intent(self) -> None:
        first_url = "https://example.com/entry"
        relay_url = "https://example.com/relay?id=123"
        final_url = "https://example.com/article"
        sources = {
            first_url: {
                "source_type": "web", "url": first_url, "title": "入口記事",
                "description": "本編への入口", "site_name": "入口", "author": "",
                "images": [], "videos": [],
                "links": [{"url": relay_url, "text": "目的の記事 volume184"}],
            },
            relay_url: {
                "source_type": "web", "url": relay_url, "title": "リンク集",
                "description": "新着リンク", "site_name": "中継", "author": "",
                "images": [], "videos": [],
                "links": [
                    {"url": "https://example.com/latest", "text": "先頭の別記事"},
                    {"url": final_url, "text": "目的の記事 volume184"},
                ],
            },
            final_url: {
                "source_type": "web", "url": final_url, "title": "本編",
                "description": "画像ギャラリー", "site_name": "本編", "author": "",
                "images": [], "videos": [], "links": [],
            },
        }

        class FakeRunner:
            def analyze(self, source: dict[str, object]) -> dict[str, object]:
                common = {
                    "title": str(source["title"]),
                    "description": str(source["description"]),
                    "category": "画像",
                    "analysis_summary": "テスト判定",
                    "image_decisions": [],
                    "video_decisions": [],
                }
                if source["url"] == first_url:
                    return {
                        **common, "page_role": "gateway", "follow_url": relay_url,
                        "follow_reason": "目的の記事リンク",
                    }
                if source["url"] == relay_url:
                    context = source.get("navigation_context")
                    self_outer.assertEqual("目的の記事 volume184", context["followed_link_text"])
                    return {
                        **common, "page_role": "gateway", "follow_url": final_url,
                        "follow_reason": "同じ記事の最終リンク",
                    }
                return {
                    **common, "page_role": "article", "follow_url": "",
                    "follow_reason": "",
                }

        self_outer = self
        with patch(
            "indanya_desktop.workers.capture_rendered_source",
            side_effect=lambda url, _progress: dict(sources[url]),
        ):
            result = _capture_and_analyze_source(ROOT, first_url, FakeRunner())

        self.assertEqual(final_url, result["url"])
        self.assertEqual([first_url, relay_url, final_url], result["source_chain"])

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

    def test_video_thumbnail_uses_canvas_pixels_instead_of_dom_screenshot(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (64, 96), "#663344").save(buffer, format="JPEG")

        class FakeVideo:
            def evaluate(self, _script: str) -> str:
                return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

            def screenshot(self, **_kwargs: object) -> bytes:
                raise AssertionError("DOM screenshot must not be used for video thumbnails")

        captured = _video_canvas_frame(FakeVideo())
        with Image.open(BytesIO(captured)) as image:
            self.assertEqual((64, 96), image.size)

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
