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
    XLoginRequiredError,
    _apply_editorial_metadata,
    _capture_and_analyze_source,
    _capture_for_manual_generation,
    _is_transient_generation_error,
    _mark_ready_to_publish,
    _select_article_images,
)
from indanya_desktop.browser_capture import (  # noqa: E402
    _find_x_media_urls,
    _merge_snapshot,
    _plausible_video_candidate,
    _usable_final_url,
    _video_canvas_frame,
    _video_priority,
    _x_video_asset_key,
)


class SiteRegistryTests(unittest.TestCase):
    def test_transient_generation_errors_are_deferred(self) -> None:
        self.assertTrue(_is_transient_generation_error("Codexの利用上限に達しました"))
        self.assertTrue(_is_transient_generation_error("You've hit your usage limit"))
        self.assertFalse(_is_transient_generation_error("本文画像が見つかりません"))

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

    def test_article_image_selection_uses_at_most_ten_images_total(self) -> None:
        source = {
            "recommended_thumbnail_ids": ["thumb"],
            "recommended_body_image_ids": [f"body-{index}" for index in range(12)],
            "images": [
                {
                    "id": "thumb",
                    "data": b"thumbnail",
                    "ai_recommended_use": "thumbnail",
                    "ai_verdict": "article",
                },
                *[
                    {
                        "id": f"body-{index}",
                        "data": bytes([index]) * 32,
                        "ai_recommended_use": "body",
                        "ai_verdict": "article",
                        "ai_relevance_score": 100 - index,
                    }
                    for index in range(12)
                ],
            ],
        }

        selected = _select_article_images(source)

        self.assertEqual("thumb", selected["thumbnail_id"])
        self.assertEqual(9, len(selected["body_ids"]))
        self.assertEqual(10, len({selected["thumbnail_id"], *selected["body_ids"]}))

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

    def test_x_account_intent_reaches_codex_without_private_sales_note(self) -> None:
        profile_url = "https://x.com/Test_User"
        browser_source = {
            "source_type": "web",
            "url": profile_url,
            "title": "Test User (@Test_User) / X",
            "description": "",
            "site_name": "X",
            "author": "",
            "images": [],
            "videos": [],
            "links": [],
            "browser_capture": True,
            "x_authenticated": True,
        }
        semantic_source = {
            **browser_source,
            "source_type": "x_profile",
            "description": "公開プロフィール",
            "x_info": {"username": "Test_User"},
            "x_embed": {"author_name": "Test User", "text": "公開投稿"},
        }

        class FakeRunner:
            def analyze(self, source: dict[str, object]) -> dict[str, object]:
                intent = source["editorial_intent"]
                self_outer.assertEqual("x_account", intent["content_mode"])
                self_outer.assertNotIn("private_note", intent)
                return {
                    "title": str(source["title"]),
                    "description": str(source["description"]),
                    "category": "SNS",
                    "analysis_summary": "Xアカウント",
                    "image_decisions": [],
                    "video_decisions": [],
                    "page_role": "article",
                    "follow_url": "",
                    "follow_reason": "",
                }

        self_outer = self
        with (
            patch("indanya_desktop.workers.capture_rendered_source", return_value=browser_source),
            patch("indanya_desktop.workers.analyze_source_url", return_value=semantic_source),
        ):
            result = _capture_and_analyze_source(
                ROOT,
                profile_url,
                FakeRunner(),
                editorial_intent={
                    "content_mode": "auto",
                    "promotion_type": "organic",
                    "editorial_brief": "衣装を中心に",
                    "private_note": "料金と連絡先",
                },
            )

        self.assertEqual("x_profile", result["source_type"])
        self.assertEqual("x_account", result["editorial_intent"]["content_mode"])
        self.assertNotIn("private_note", result["editorial_intent"])

    def test_x_profile_without_login_stops_before_creating_incomplete_article(self) -> None:
        profile_url = "https://x.com/Test_User"
        browser_source = {
            "source_type": "web",
            "url": profile_url,
            "title": "Test User (@Test_User) / X",
            "description": "",
            "site_name": "X",
            "author": "",
            "images": [],
            "videos": [],
            "links": [{"url": f"{profile_url}/status/1", "text": "投稿"}],
            "browser_capture": True,
            "x_authenticated": False,
            "x_timeline_media_count": 0,
        }
        semantic_source = {
            **browser_source,
            "source_type": "x_profile",
            "x_info": {"username": "Test_User"},
            "x_embed": {"author_name": "Test User", "text": "公開投稿"},
        }

        with (
            patch("indanya_desktop.workers.capture_rendered_source", return_value=browser_source),
            patch("indanya_desktop.workers.analyze_source_url", return_value=semantic_source),
        ):
            with self.assertRaisesRegex(RuntimeError, "ログアウト状態では非表示"):
                _capture_and_analyze_source(ROOT, profile_url, object())

    def test_manual_x_generation_logs_in_once_and_retries_automatically(self) -> None:
        completed = {"source_type": "x_profile", "x_authenticated": True}
        progress: list[str] = []
        with (
            patch(
                "indanya_desktop.workers._capture_and_analyze_source",
                side_effect=[RuntimeError("unexpected")],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                _capture_for_manual_generation(
                    ROOT,
                    "https://x.com/Test_User",
                    object(),
                    lambda _value, message: progress.append(message),
                )

        with (
            patch(
                "indanya_desktop.workers._capture_and_analyze_source",
                side_effect=[
                    XLoginRequiredError("login"),
                    completed,
                ],
            ) as capture,
            patch("indanya_desktop.workers.open_x_login_session") as login,
        ):
            result = _capture_for_manual_generation(
                ROOT,
                "https://x.com/Test_User",
                object(),
                lambda _value, message: progress.append(message),
            )

        self.assertIs(completed, result)
        self.assertEqual(2, capture.call_count)
        login.assert_called_once()

    def test_sponsored_metadata_is_disclosed_but_keeps_sales_note_private(self) -> None:
        payload = {"tags": ["SNS"], "blocks": []}
        source = {"source_type": "x_profile", "x_info": {"username": "Test_User"}}
        _apply_editorial_metadata(
            payload,
            source,
            {
                "content_mode": "x_account",
                "promotion_type": "sponsored",
                "editorial_brief": "写真の雰囲気",
                "private_note": "依頼者の連絡先",
            },
        )

        self.assertEqual("x_account", payload["content_mode"])
        self.assertEqual("@Test_UserのX", payload["source_label"])
        self.assertEqual("依頼者の連絡先", payload["private_client_note"])
        self.assertIn("PR", payload["tags"])
        self.assertEqual("sponsored-disclosure", payload["blocks"][0]["id"])

    def test_browser_video_candidates_prioritize_media_before_iframes(self) -> None:
        items = [
            {"kind": "iframe", "urls": ["https://ads.example/player"]},
            {"kind": "direct", "urls": ["https://cdn.example/movie.mp4"]},
            {"kind": "network", "urls": ["https://cdn.example/stream?id=1"]},
        ]
        ordered = sorted(items, key=_video_priority)
        self.assertEqual(["direct", "network", "iframe"], [item["kind"] for item in ordered])

    def test_x_video_variants_prefer_highest_quality_and_share_an_asset_key(self) -> None:
        low = "https://video.twimg.com/amplify_video/123/vid/avc1/320x568/low.mp4"
        high = "https://video.twimg.com/amplify_video/123/vid/avc1/720x1280/high.mp4"
        ordered = sorted(
            [{"kind": "network", "urls": [low]}, {"kind": "network", "urls": [high]}],
            key=_video_priority,
        )
        self.assertEqual(high, ordered[0]["urls"][0])
        self.assertEqual("123", _x_video_asset_key(low))
        self.assertEqual(_x_video_asset_key(low), _x_video_asset_key(high))

    def test_html_page_url_is_not_accepted_as_a_direct_video(self) -> None:
        page_url = "https://example.com/article/"
        self.assertFalse(_plausible_video_candidate(page_url, "direct", "", page_url))
        self.assertFalse(_plausible_video_candidate(
            "https://example.com/player",
            "direct",
            "text/html",
            page_url,
        ))
        self.assertTrue(_plausible_video_candidate(
            "https://media.example.com/movie.mp4",
            "direct",
            "",
            page_url,
        ))

    def test_x_dash_manifest_is_one_video_and_fragments_are_rejected(self) -> None:
        page_url = "https://x.com/Test_User/status/1"
        self.assertTrue(_plausible_video_candidate(
            "https://video.twimg.com/amplify_video/1/pl/abc.mpd?tag=14",
            "direct",
            "application/dash+xml",
            page_url,
        ))
        for fragment in (
            "https://video.twimg.com/amplify_video/1/aud/mp4a/0/0/init.mp4",
            "https://video.twimg.com/amplify_video/1/aud/mp4a/128000/segment.m4s",
            "https://video.twimg.com/amplify_video/1/vid/avc1/720x1280/segment.m4s",
            "https://video.twimg.com/amplify_video/1/vid/avc1/0/0/init.mp4",
        ):
            self.assertFalse(_plausible_video_candidate(
                fragment,
                "direct",
                "video/mp4",
                page_url,
            ))

    def test_x_scroll_snapshots_keep_media_removed_from_later_dom(self) -> None:
        collected: dict[str, object] = {}
        _merge_snapshot(
            collected,
            {
                "images": [{"url": "https://pbs.twimg.com/media/first.jpg"}],
                "videos": [{"urls": ["https://video.twimg.com/first.mp4"]}],
                "links": [{"url": "https://x.com/Test_User/status/1", "text": "投稿1"}],
                "text_blocks": ["最初の投稿"],
            },
        )
        _merge_snapshot(
            collected,
            {
                "images": [{"url": "https://pbs.twimg.com/media/second.jpg"}],
                "videos": [],
                "links": [{"url": "https://x.com/Test_User/status/2", "text": "投稿2"}],
                "text_blocks": ["次の投稿"],
            },
        )

        self.assertEqual(2, len(collected["images"]))
        self.assertEqual(1, len(collected["videos"]))
        self.assertEqual(["最初の投稿", "次の投稿"], collected["text_blocks"])

    def test_x_graphql_media_urls_are_collected(self) -> None:
        images: set[str] = set()
        videos: set[str] = set()
        _find_x_media_urls(
            {
                "media": [
                    {"url": "https://pbs.twimg.com/media/photo.jpg?format=jpg&name=large"},
                    {"variants": [{"url": "https://video.twimg.com/ext_tw_video/clip/vid/720x1280/movie.mp4?tag=12"}]},
                ],
            },
            images,
            videos,
        )
        self.assertEqual(1, len(images))
        self.assertEqual(1, len(videos))

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
