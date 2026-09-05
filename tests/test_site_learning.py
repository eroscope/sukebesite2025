from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.chatgpt_direct import (  # noqa: E402
    _merge_source_candidates,
    _semantic_fast_path_ready,
    capture_and_analyze,
)
from indanya_desktop.site_learning import (  # noqa: E402
    bootstrap_site_learning,
    can_attempt_site,
    classify_site_failure,
    get_site_plan,
    learning_prompt_context,
    list_site_learning,
    prioritize_source_media,
    record_site_outcome,
    route_template,
)


class SiteLearningTests(unittest.TestCase):
    def test_route_template_groups_article_ids_and_keeps_query_keys(self) -> None:
        self.assertEqual(
            "/archives/{id}",
            route_template("https://www.chaos-giga.com/archives/287311"),
        )
        self.assertEqual(
            "/av/content?id",
            route_template("https://video.dmm.co.jp/av/content/?id=abc00123"),
        )
        self.assertEqual(
            "/?p",
            route_template("https://www.po-kaki-to.com/?p=448192"),
        )

    def test_failure_types_are_classified_for_next_attempt(self) -> None:
        self.assertEqual(
            "no_video",
            classify_site_failure("動画が見つかりませんでした", "material"),
        )
        self.assertEqual(
            "ai_validation",
            classify_site_failure("JSON schemaの検査を通りません", "generation"),
        )
        self.assertEqual(
            "timeout",
            classify_site_failure("The read operation timed out", "material"),
        )

    def test_success_builds_a_reusable_fast_extraction_recipe(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/archives/12345"
            source = {
                "images": [
                    {"id": "image-1", "url": "https://cdn.example.net/a.jpg"},
                    {"id": "image-2", "url": "https://cdn.example.net/b.jpg"},
                    {"id": "image-3", "url": "https://other.example.net/c.jpg"},
                ],
                "videos": [],
                "text_blocks": ["本文A", "本文B"],
            }
            first = record_site_outcome(
                site_root,
                url,
                "success",
                strategy="browser_full",
                elapsed_seconds=50,
                source=source,
                selected_image_ids=["image-1", "image-2"],
            )
            self.assertEqual("semantic_trial", first["strategy"])
            self.assertEqual(2.0, first["expected_images"])
            self.assertEqual(["cdn.example.net"], first["preferred_image_hosts"])

            second = record_site_outcome(
                site_root,
                "https://example.com/archives/99999",
                "success",
                strategy="semantic_fast",
                elapsed_seconds=8,
                source=source,
                selected_image_ids=["image-1", "image-2"],
            )
            self.assertEqual("semantic_fast", second["strategy"])
            self.assertEqual(2, second["successes"])

            rows = list_site_learning(site_root)
            self.assertEqual(1, len(rows))
            self.assertEqual(2, rows[0]["successes"])
            self.assertEqual(29.0, rows[0]["average_seconds"])

    def test_successful_gateway_chain_is_reused_for_the_same_site(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            source_url = "https://gateway.example/blog-entry-123.html"
            source = {
                "images": [{"id": "image-1", "url": "https://final.example/1.jpg"}],
                "videos": [],
                "text_blocks": ["本編本文"],
                "source_chain": [
                    source_url,
                    "https://relay.example/archives/456.html",
                    "https://final.example/archives/789/",
                ],
            }
            record_site_outcome(
                site_root,
                source_url,
                "success",
                strategy="gateway_chain",
                elapsed_seconds=20,
                source=source,
                selected_image_ids=["image-1"],
            )

            plan = get_site_plan(site_root, "https://gateway.example/blog-entry-999.html")
            self.assertEqual(1, plan["navigation_successes"])
            self.assertEqual(2.0, plan["average_navigation_hops"])
            self.assertEqual(["final.example"], plan["navigation_target_hosts"])
            self.assertIn("入口ページの実績あり", learning_prompt_context(plan))

    def test_failure_changes_strategy_and_adds_specific_recovery_note(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/watch/12345"
            record_site_outcome(
                site_root,
                url,
                "failure",
                stage="material",
                strategy="semantic_fast",
                message="動画が見つかりませんでした",
            )
            plan = get_site_plan(site_root, "https://example.com/watch/99999")
            self.assertEqual("browser_full", plan["strategy"])
            self.assertIn("動画を取りこぼした", learning_prompt_context(plan))

            record_site_outcome(
                site_root,
                url,
                "failure",
                stage="material",
                strategy="browser_full",
                message="動画が見つかりませんでした",
            )
            allowed, reason = can_attempt_site(site_root, url)
            self.assertFalse(allowed)
            self.assertIn("自動調整中", reason)

    def test_rate_limit_is_deferred_without_lowering_site_success_rate(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/articles/12345"
            record_site_outcome(
                site_root,
                url,
                "deferred",
                stage="generation",
                message="ChatGPTの利用制限",
            )
            plan = get_site_plan(site_root, url)
            self.assertEqual(0, plan["failures"])
            rows = list_site_learning(site_root)
            self.assertEqual(0, rows[0]["failures"])

    def test_generation_failure_does_not_disable_a_working_extraction_template(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/archives/12345"
            source = {
                "images": [{"id": "image-1"}, {"id": "image-2"}],
                "videos": [],
                "text_blocks": ["本文"],
            }
            record_site_outcome(
                site_root,
                url,
                "success",
                stage="save",
                strategy="semantic_fast",
                source=source,
            )
            record_site_outcome(
                site_root,
                url,
                "failure",
                stage="generation",
                strategy="semantic_fast",
                message="JSON schemaの検査を通りません",
            )
            plan = get_site_plan(site_root, url)
            self.assertEqual("semantic_fast", plan["strategy"])

    def test_existing_drafts_seed_learning_without_faking_live_successes(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            draft_root = site_root / ".article-studio" / "drafts"
            draft_root.mkdir(parents=True)
            (draft_root / "one.json").write_text(
                json.dumps(
                    {
                        "source_url": "https://example.com/archives/12345",
                        "images": [
                            {"id": "image-1", "source_url": "https://cdn.example.com/a.jpg"},
                            {"id": "image-2", "source_url": "https://cdn.example.com/b.jpg"},
                        ],
                        "videos": [],
                        "blocks": [{"type": "post", "text": "本文"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(1, bootstrap_site_learning(site_root))
            self.assertEqual(0, bootstrap_site_learning(site_root))
            plan = get_site_plan(site_root, "https://example.com/archives/99999")
            self.assertEqual("semantic_trial", plan["strategy"])
            self.assertEqual(1, plan["historical_successes"])
            rows = list_site_learning(site_root)
            self.assertEqual(0, rows[0]["successes"])
            self.assertEqual(1, rows[0]["historical_successes"])

    def test_media_hosts_from_successes_are_prioritized(self) -> None:
        source = {
            "images": [
                {"id": "bad", "url": "https://ads.example/a.jpg", "source_score": 90},
                {"id": "good", "url": "https://media.example/b.jpg", "source_score": 10},
            ],
            "videos": [],
        }
        result = prioritize_source_media(
            source,
            {
                "preferred_image_hosts": ["media.example"],
                "preferred_video_hosts": [],
            },
        )
        self.assertEqual("good", result["images"][0]["id"])

    def test_fast_path_requires_expected_images_and_rejects_video_pages(self) -> None:
        ready, _reason = _semantic_fast_path_ready(
            {
                "title": "十分な記事タイトル",
                "excerpts": ["本文があります"],
                "images": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
                "videos": [],
            },
            {"expected_images": 3, "expected_videos": 0},
        )
        self.assertTrue(ready)
        video_ready, reason = _semantic_fast_path_ready(
            {
                "title": "十分な記事タイトル",
                "excerpts": ["本文があります"],
                "images": [{"id": "1"}, {"id": "2"}],
                "videos": [{"id": "video-1"}],
            },
            {"expected_images": 2, "expected_videos": 0},
        )
        self.assertFalse(video_ready)
        self.assertIn("完全取得", reason)

    def test_browser_and_semantic_results_are_merged_without_duplicate_media(self) -> None:
        merged = _merge_source_candidates(
            {
                "title": "記事",
                "images": [{"id": "a", "url": "https://cdn.example/a.jpg"}],
                "videos": [{"id": "v1", "url": "https://cdn.example/a.mp4"}],
            },
            {
                "images": [
                    {"id": "b", "url": "https://cdn.example/a.jpg"},
                    {"id": "c", "url": "https://cdn.example/b.jpg"},
                ],
                "videos": [
                    {"id": "v2", "url": "https://cdn.example/a.mp4"},
                    {"id": "v3", "url": "https://cdn.example/b.mp4"},
                ],
            },
        )
        self.assertEqual(2, len(merged["images"]))
        self.assertEqual(2, len(merged["videos"]))
        self.assertEqual(["media-1", "media-2"], [item["id"] for item in merged["images"]])

    def test_learned_static_route_skips_the_heavy_browser_capture(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/archives/12345"
            historical_source = {
                "images": [
                    {"id": "media-1", "url": "https://cdn.example/a.jpg"},
                    {"id": "media-2", "url": "https://cdn.example/b.jpg"},
                ],
                "videos": [],
                "excerpts": ["十分な本文です"],
            }
            record_site_outcome(
                site_root,
                url,
                "success",
                strategy="historical",
                source=historical_source,
                historical=True,
            )
            semantic_source = {
                "source_type": "web",
                "url": url,
                "title": "学習済みサイトの記事タイトル",
                "description": "記事の説明",
                "excerpts": ["記事本文として十分な長さを持つテキストです"],
                "images": [
                    {"id": "media-1", "url": "https://cdn.example/a.jpg", "data": b"a"},
                    {"id": "media-2", "url": "https://cdn.example/b.jpg", "data": b"b"},
                ],
                "videos": [],
            }

            def apply_analysis(source: dict, _analysis: dict) -> dict:
                return {
                    **source,
                    "recommended_thumbnail_ids": ["media-1"],
                    "recommended_body_image_ids": ["media-2"],
                    "recommended_video_ids": [],
                }

            with patch(
                "indanya_desktop.chatgpt_direct.analyze_source_url",
                return_value=semantic_source,
            ) as semantic, patch(
                "indanya_desktop.chatgpt_direct.capture_rendered_source"
            ) as browser, patch(
                "indanya_desktop.chatgpt_direct.save_evidence_package",
                return_value=[],
            ), patch(
                "indanya_desktop.chatgpt_direct._request_validated_json",
                return_value={"adult_content": True, "page_role": "article"},
            ), patch(
                "indanya_desktop.chatgpt_direct.apply_codex_analysis",
                side_effect=apply_analysis,
            ):
                result = capture_and_analyze(
                    site_root,
                    url,
                    "request-1",
                    lambda _value, _message: None,
                )

            semantic.assert_called_once_with(url)
            browser.assert_not_called()
            self.assertEqual("semantic_fast", result["capture_strategy"])

    def test_video_route_keeps_full_browser_capture(self) -> None:
        with TemporaryDirectory() as directory:
            site_root = Path(directory)
            url = "https://example.com/videos/12345"
            source = {
                "source_type": "web",
                "url": url,
                "title": "動画記事タイトル",
                "description": "記事の説明",
                "text_blocks": ["記事本文"],
                "images": [
                    {"id": "media-1", "url": "https://cdn.example/a.jpg", "data": b"a"},
                ],
                "videos": [
                    {"id": "video-1", "url": "https://cdn.example/a.mp4"},
                ],
            }
            record_site_outcome(
                site_root,
                url,
                "success",
                strategy="browser_full",
                source=source,
                historical=True,
            )

            def apply_analysis(captured: dict, _analysis: dict) -> dict:
                return {
                    **captured,
                    "recommended_thumbnail_ids": ["media-1"],
                    "recommended_body_image_ids": [],
                    "recommended_video_ids": ["video-1"],
                }

            with patch(
                "indanya_desktop.chatgpt_direct.capture_rendered_source",
                return_value=source,
            ) as browser, patch(
                "indanya_desktop.chatgpt_direct.analyze_source_url"
            ) as semantic, patch(
                "indanya_desktop.chatgpt_direct.save_evidence_package",
                return_value=[],
            ), patch(
                "indanya_desktop.chatgpt_direct._request_validated_json",
                return_value={"adult_content": True, "page_role": "article"},
            ), patch(
                "indanya_desktop.chatgpt_direct.apply_codex_analysis",
                side_effect=apply_analysis,
            ):
                result = capture_and_analyze(
                    site_root,
                    url,
                    "request-2",
                    lambda _value, _message: None,
                )

            browser.assert_called_once()
            semantic.assert_not_called()
            self.assertEqual("browser_full", result["capture_strategy"])


if __name__ == "__main__":
    unittest.main()
