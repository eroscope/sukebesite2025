from __future__ import annotations

import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.chatgpt_direct import (  # noqa: E402
    _compact_fanza_task_prompt,
    _merge_source_candidates,
    _prepare_selected_media,
    _request_validated_json,
    _validate_article_batch,
    _validate_complete_analysis,
    estimate_chatgpt_attachment_count,
    extract_json_object,
    validate_single_pass_article,
)
from indanya_desktop.browser_capture import (  # noqa: E402
    CHATGPT_PROJECT_URL,
    _chatgpt_conversation_target,
    _looks_like_chatgpt_rate_limit,
)
from indanya_desktop.workers import ChatGptSendWorker  # noqa: E402


class ChatGptDirectTests(unittest.TestCase):
    def test_site_recovery_caps_candidates_and_rebuilds_one_current_sheet(self) -> None:
        primary = {
            "images": [
                {"id": f"media-{index + 1}", "url": f"https://main.example/{index}.jpg"}
                for index in range(30)
            ],
            "videos": [],
            "browser_attachments": [{"id": "original-sheet", "kind": "contact_sheet"}],
        }
        secondary = {
            "images": [
                {"id": f"old-{index}", "url": f"https://main.example/{index}.jpg"}
                for index in range(3)
            ] + [
                {"id": f"new-{index}", "url": f"https://recovery.example/{index}.jpg"}
                for index in range(20)
            ],
            "videos": [],
        }
        with patch(
            "indanya_desktop.chatgpt_direct._sheet_attachments",
            side_effect=lambda records, **_kwargs: [{
                "id": "recovered-sheet",
                "media_ids": [item["id"] for item in records],
            }],
        ) as sheets:
            merged = _merge_source_candidates(primary, secondary)

        self.assertEqual(36, len(merged["images"]))
        self.assertEqual(1, len(merged["browser_attachments"]))
        self.assertEqual(1, sheets.call_count)
        sheet_images = sheets.call_args.args[0]
        self.assertEqual(36, len(sheet_images))
        self.assertEqual(
            [item["id"] for item in merged["images"]],
            [item["id"] for item in sheet_images],
        )

    def test_semantic_article_body_removes_unrelated_browser_images(self) -> None:
        primary = {
            "images": [
                {"id": "browser-body", "url": "https://site.example/body.jpg", "data": b"body"},
                {"id": "browser-ad", "url": "https://ads.example/banner.jpg", "data": b"ad"},
            ],
            "videos": [],
            "browser_attachments": [
                {"id": "page", "kind": "full_page"},
                {"id": "stale", "kind": "contact_sheet"},
            ],
        }
        secondary = {
            "images": [
                {
                    "id": "semantic-body",
                    "url": "https://site.example/body.jpg",
                    "inside_article": True,
                    "source_score": 180,
                },
                {
                    "id": "metadata",
                    "url": "https://site.example/cover.jpg",
                    "source_hint": "metadata",
                },
                {"id": "related", "url": "https://site.example/related.jpg"},
            ],
            "videos": [],
        }
        with patch(
            "indanya_desktop.chatgpt_direct._sheet_attachments",
            side_effect=lambda records, **kwargs: [{
                "id": kwargs["prefix"],
                "kind": kwargs["kind"],
                "media_ids": [item["id"] for item in records],
            }],
        ):
            merged = _merge_source_candidates(primary, secondary)

        self.assertEqual(
            ["https://site.example/body.jpg", "https://site.example/cover.jpg"],
            [item["url"] for item in merged["images"]],
        )
        self.assertTrue(merged["images"][0]["inside_article"])
        self.assertEqual(["full_page", "contact_sheet"], [item["kind"] for item in merged["browser_attachments"]])

    def test_browser_only_article_anchor_survives_semantic_body_filter(self) -> None:
        primary = {
            "images": [
                {
                    "id": "browser-lead",
                    "url": "https://pbs.twimg.com/media/lead?format=jpg&name=large",
                    "inside_article": True,
                    "anchor_href_candidate": True,
                    "thread_reply_number": 1,
                    "data": b"lead",
                },
                {
                    "id": "browser-sidebar",
                    "url": "https://site.example/sidebar.jpg",
                    "data": b"sidebar",
                },
            ],
            "videos": [],
            "browser_attachments": [],
        }
        secondary = {
            "images": [{
                "id": "semantic-later-reply",
                "url": "https://site.example/later-reply.jpg",
                "inside_article": True,
                "source_score": 180,
            }],
            "videos": [],
        }
        with patch(
            "indanya_desktop.chatgpt_direct._sheet_attachments",
            return_value=[],
        ):
            merged = _merge_source_candidates(primary, secondary)

        self.assertEqual(
            [
                "https://pbs.twimg.com/media/lead?format=jpg&name=large",
                "https://site.example/later-reply.jpg",
            ],
            [item["url"] for item in merged["images"]],
        )
        self.assertTrue(merged["images"][0]["anchor_href_candidate"])
        self.assertEqual(1, merged["images"][0]["thread_reply_number"])

    def test_semantic_body_video_keeps_only_metadata_thumbnail(self) -> None:
        primary = {
            "images": [
                {"url": "https://site.example/cover.jpg", "data": b"cover"},
                {"url": "https://site.example/sidebar.jpg", "data": b"sidebar"},
            ],
            "videos": [
                {"url": "https://site.example/body.mp4", "frame_data": b"frame"},
                {"url": "https://site.example/js-only.mp4", "frame_data": b"js-frame"},
            ],
            "browser_attachments": [],
        }
        secondary = {
            "images": [{
                "url": "https://site.example/cover.jpg",
                "source_hint": "metadata",
            }],
            "videos": [{
                "url": "https://site.example/body.mp4",
                "inside_article": True,
            }],
        }
        with patch(
            "indanya_desktop.chatgpt_direct._sheet_attachments",
            return_value=[],
        ):
            merged = _merge_source_candidates(primary, secondary)

        self.assertEqual(
            ["https://site.example/cover.jpg"],
            [item["url"] for item in merged["images"]],
        )
        self.assertEqual(2, len(merged["videos"]))
        self.assertTrue(merged["videos"][0]["inside_article"])
        self.assertEqual(b"frame", merged["videos"][0]["frame_data"])
        self.assertEqual("https://site.example/js-only.mp4", merged["videos"][1]["url"])

    def test_rate_limit_text_recognizes_current_chatgpt_notices(self) -> None:
        self.assertTrue(_looks_like_chatgpt_rate_limit("無料プランの利用上限に達しました"))
        self.assertTrue(_looks_like_chatgpt_rate_limit("You've reached your Free plan limit"))
        self.assertFalse(_looks_like_chatgpt_rate_limit("記事を生成しています"))

    def test_large_gallery_uses_labelled_contact_sheets_without_losing_images(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (800, 600), "red").save(buffer, "JPEG")
        image_data = buffer.getvalue()
        source = {
            "images": [
                {"id": f"image-{index}", "data": image_data, "extension": ".jpg"}
                for index in range(18)
            ],
            "videos": [],
        }
        options = {"selected_image_ids": [f"image-{index}" for index in range(18)]}

        with TemporaryDirectory() as directory:
            records, paths = _prepare_selected_media(Path(directory), source, options)

            self.assertEqual(18, len(records))
            self.assertEqual(3, len(paths))
            self.assertEqual(3, estimate_chatgpt_attachment_count(source, options))
            self.assertEqual("image-0.jpg", paths[0].name)
            self.assertTrue(all(path.exists() for path in paths))
            self.assertEqual(1, records[1]["contact_sheet_cell"])
            self.assertEqual("contact-sheet-1.jpg", records[1]["filename"])

    def test_fanza_task_prompt_is_compact_and_keeps_exact_product_identity(self) -> None:
        prompt = _compact_fanza_task_prompt(
            {
                "title": "作品固有タイトル",
                "fanza_product_id": "abc00123",
                "canonical_product_url": "https://video.dmm.co.jp/av/content/?id=abc00123",
            },
            {"selected_image_ids": ["media-1", "media-2"], "reply_count": "8"},
            [
                {"id": "media-1", "filename": "package.jpg"},
                {"id": "media-2", "filename": "sheet.jpg", "contact_sheet_cell": 1},
            ],
        )
        self.assertIn('"product_id":"abc00123"', prompt)
        self.assertIn('"official_page_title":"作品固有タイトル"', prompt)
        self.assertLess(len(prompt), 1200)

    def test_batch_validation_preserves_image_and_video_assignments(self) -> None:
        entry = {
            "request_id": "media",
            "options": {
                "reply_count": "auto",
                "selected_image_ids": ["media-1"],
                "selected_video_ids": ["video-1"],
            },
        }
        article = {
            "title": "作品内容が分かる自然な紹介タイトル",
            "summary": "作品の画像と映像を見て内容を紹介する記事概要です。",
            "category": "画像",
            "tags": ["作品紹介"],
            "responses": [
                {"text": "パッケージの雰囲気ええな", "image_ids": ["media-1"], "video_ids": []},
                {"text": "この場面はちょっと気になる", "image_ids": [], "video_ids": ["video-1"]},
                {"text": "衣装も作品に合ってる", "image_ids": [], "video_ids": []},
            ],
        }
        result = _validate_article_batch(
            {"articles": [{"request_id": "media", "article": article}]},
            [entry],
        )
        responses = result["generated"]["media"]["responses"]
        self.assertEqual(["media-1"], responses[0]["image_ids"])
        self.assertEqual(["video-1"], responses[1]["video_ids"])

    def test_transient_browser_failure_is_retried(self) -> None:
        with patch(
            "indanya_desktop.chatgpt_direct.send_chatgpt_prompt",
            side_effect=[
                RuntimeError("temporary browser failure"),
                {"message": '{"ok": true}', "conversation_url": ""},
            ],
        ) as send, patch("indanya_desktop.chatgpt_direct.time.sleep"):
            result = _request_validated_json(
                "prompt", [], lambda value: value, lambda _value, _message: None
            )
        self.assertTrue(result["ok"])
        self.assertEqual(2, send.call_count)

    def test_bounded_chatgpt_wait_failure_is_not_retried(self) -> None:
        with patch(
            "indanya_desktop.chatgpt_direct.send_chatgpt_prompt",
            side_effect=RuntimeError("ChatGPTの返答が90秒間進まなかったため自動再試行します"),
        ) as send:
            with self.assertRaisesRegex(RuntimeError, "90秒間進まなかった"):
                _request_validated_json(
                    "prompt", [], lambda value: value, lambda _value, _message: None
                )
        self.assertEqual(1, send.call_count)

    def test_invalid_json_shape_is_not_resent_to_chatgpt(self) -> None:
        with patch(
            "indanya_desktop.chatgpt_direct.send_chatgpt_prompt",
            return_value={"message": '{"wrong": true}', "conversation_url": ""},
        ) as send:
            with self.assertRaises(RuntimeError):
                _request_validated_json(
                    "prompt",
                    [],
                    lambda _value: (_ for _ in ()).throw(RuntimeError("invalid shape")),
                    lambda _value, _message: None,
                )
        self.assertEqual(1, send.call_count)

    def test_new_article_chat_starts_inside_indanya_project(self) -> None:
        self.assertEqual(CHATGPT_PROJECT_URL, _chatgpt_conversation_target(""))
        self.assertEqual(
            "https://chatgpt.com/c/article-chat",
            _chatgpt_conversation_target("https://chatgpt.com/c/article-chat"),
        )

    def test_article_steps_reuse_one_chatgpt_conversation(self) -> None:
        conversation: dict[str, str] = {}
        responses = [
            {
                "message": '{"step": 1}',
                "conversation_url": "https://chatgpt.com/c/article-chat",
            },
            {
                "message": '{"step": 2}',
                "conversation_url": "https://chatgpt.com/c/article-chat",
            },
        ]
        with patch(
            "indanya_desktop.chatgpt_direct.send_chatgpt_prompt",
            side_effect=responses,
        ) as send:
            first = _request_validated_json(
                "first",
                [],
                lambda value: value,
                lambda _value, _message: None,
                conversation,
            )
            second = _request_validated_json(
                "second",
                [],
                lambda value: value,
                lambda _value, _message: None,
                conversation,
            )

        self.assertEqual(1, first["step"])
        self.assertEqual(2, second["step"])
        self.assertEqual(
            "",
            send.call_args_list[0].kwargs["conversation_url"],
        )
        self.assertEqual(
            "https://chatgpt.com/c/article-chat",
            send.call_args_list[1].kwargs["conversation_url"],
        )
    def test_extracts_json_from_fenced_or_surrounded_response(self) -> None:
        self.assertEqual(
            {"ok": True},
            extract_json_object('```json\n{"ok": true}\n```'),
        )
        self.assertEqual(
            {"ok": True},
            extract_json_object('説明文 {"ok": true} 続き'),
        )

    def test_unreviewed_media_candidate_is_excluded_without_retry(self) -> None:
        source = {
            "images": [{"id": "media-1"}, {"id": "media-2"}],
            "videos": [{"id": "video-1"}],
        }
        with patch(
            "indanya_desktop.chatgpt_direct._validate_codex_analysis",
            side_effect=lambda value, _source: value,
        ):
            result = _validate_complete_analysis(
                {
                    "image_decisions": [{"image_id": "media-1"}],
                    "video_decisions": [{"video_id": "video-1"}],
                },
                source,
            )
        missing = next(
            item for item in result["image_decisions"]
            if item["image_id"] == "media-2"
        )
        self.assertEqual("exclude", missing["recommended_use"])
        self.assertEqual("unclear", missing["verdict"])

    def test_analysis_preserves_embedded_article_for_one_message_generation(self) -> None:
        source = {
            "title": "元ページ",
            "description": "説明",
            "images": [{"id": "media-1"}],
            "videos": [{"id": "video-1"}],
            "links": [],
        }
        article = {
            "title": "画像と動画の内容が分かる自然な記事タイトル",
            "summary": "画像と動画を見て内容を独自に紹介する概要です。",
            "category": "動画",
            "tags": ["作品紹介"],
            "responses": [
                {"text": "まずこれ見てくれ", "image_ids": ["media-1"], "video_ids": []},
                {"text": "映像もあるんか", "image_ids": [], "video_ids": ["video-1"]},
                {"text": "この構成は見やすいな", "image_ids": [], "video_ids": []},
            ],
        }
        analysis = {
            "title": "元ページ",
            "description": "説明",
            "category": "動画",
            "page_role": "article",
            "follow_url": "",
            "follow_reason": "",
            "analysis_summary": "本編素材を確認",
            "adult_content": True,
            "adult_reason": "成人向け作品",
            "fanza_relevance": "none",
            "fanza_performer_name": "",
            "fanza_search_query": "",
            "fanza_product_code": "",
            "fanza_reason": "",
            "fanza_people": [],
            "fanza_image_products": [],
            "fanza_recommendation_queries": [],
            "image_decisions": [{
                "image_id": "media-1",
                "verdict": "article",
                "role": "本文画像",
                "recommended_use": "thumbnail_and_body",
                "content_group": "main",
                "relation": "",
                "relevance_score": 100,
                "reason": "本編",
            }],
            "video_decisions": [{
                "video_id": "video-1",
                "verdict": "article",
                "relevance_score": 100,
                "reason": "本編",
            }],
            "article": article,
        }

        validated_analysis = _validate_complete_analysis(analysis, source)
        source["_single_pass_article"] = validated_analysis["article"]
        generated = validate_single_pass_article(
            source,
            {
                "reply_count": "auto",
                "selected_image_ids": ["media-1"],
                "selected_video_ids": ["video-1"],
            },
        )

        self.assertEqual("【動画】" + article["title"], generated["title"])
        self.assertEqual(["media-1"], generated["responses"][0]["image_ids"])
        self.assertEqual(["video-1"], generated["responses"][1]["video_ids"])

    def test_unknown_media_ids_are_discarded_without_request_retry(self) -> None:
        source = {"images": [{"id": "media-1"}], "videos": [], "links": []}
        with patch(
            "indanya_desktop.chatgpt_direct._validate_codex_analysis",
            side_effect=lambda value, _source: value,
        ):
            result = _validate_complete_analysis(
                {
                    "image_decisions": [
                        {"image_id": "made-up-id"},
                        {"image_id": "media-1"},
                    ],
                    "video_decisions": [],
                },
                source,
            )
        self.assertEqual(
            ["media-1"],
            [item["image_id"] for item in result["image_decisions"]],
        )

    def test_worker_does_not_send_generation_message_when_first_reply_has_article(self) -> None:
        request = {
            "request_id": "one-pass",
            "url": "https://example.com/archives/12345",
            "options": {"category": "auto", "reply_count": "auto"},
        }
        source = {
            "images": [{"id": "media-1"}],
            "videos": [],
            "_single_pass_article": {"title": "included"},
        }
        generated = {
            "title": "【画像】1回の返答で完成した記事",
            "summary": "素材判定と記事本文を一度に受け取った記事です。",
            "category": "画像",
            "tags": ["作品紹介"],
            "responses": [
                {"text": "まず画像見てくれ", "style": "normal", "image_ids": [], "video_ids": []},
                {"text": "これ一枚で分かるな", "style": "normal", "image_ids": [], "video_ids": []},
                {"text": "続きも気になる", "style": "normal", "image_ids": [], "video_ids": []},
            ],
        }
        worker = ChatGptSendWorker(Path("."), ["one-pass"])
        with patch("indanya_desktop.workers.get_chatgpt_requests", return_value=[request]), \
             patch("indanya_desktop.workers.mark_chatgpt_processing"), \
             patch("indanya_desktop.workers.record_chatgpt_event"), \
             patch("indanya_desktop.workers.capture_and_analyze_with_chatgpt", return_value=source), \
             patch("indanya_desktop.workers._attach_verified_fanza_products"), \
             patch("indanya_desktop.workers._select_article_images", return_value={
                 "thumbnail_id": "media-1", "body_ids": ["media-1"]
             }), \
             patch("indanya_desktop.workers._selected_generation_videos", return_value=[]), \
             patch("indanya_desktop.workers.validate_single_pass_article", return_value=generated), \
             patch("indanya_desktop.workers.get_site_plan", return_value={}), \
             patch("indanya_desktop.workers._record_site_outcome_safely"), \
             patch("indanya_desktop.workers._save_chatgpt_generated_article", return_value={
                 "request_id": "one-pass", "slug": "saved", "title": generated["title"]
             }) as save, \
             patch("indanya_desktop.workers.generate_article_text_with_chatgpt") as second_send:
            worker._run_serialized()

        second_send.assert_not_called()
        save.assert_called_once()

    def test_worker_does_not_resend_generic_article_when_first_reply_is_invalid(self) -> None:
        request = {
            "request_id": "one-pass-invalid",
            "url": "https://example.com/archives/54321",
            "options": {"category": "auto", "reply_count": "auto"},
        }
        source = {"images": [{"id": "media-1"}], "videos": []}
        worker = ChatGptSendWorker(Path("."), ["one-pass-invalid"])
        with patch("indanya_desktop.workers.get_chatgpt_requests", return_value=[request]), \
             patch("indanya_desktop.workers.mark_chatgpt_processing"), \
             patch("indanya_desktop.workers.record_chatgpt_event"), \
             patch("indanya_desktop.workers.capture_and_analyze_with_chatgpt", return_value=source), \
             patch("indanya_desktop.workers._attach_verified_fanza_products"), \
             patch("indanya_desktop.workers._select_article_images", return_value={
                 "thumbnail_id": "media-1", "body_ids": ["media-1"]
             }), \
             patch("indanya_desktop.workers._selected_generation_videos", return_value=[]), \
             patch("indanya_desktop.workers.validate_single_pass_article", return_value=None), \
             patch("indanya_desktop.workers.get_site_plan", return_value={}), \
             patch("indanya_desktop.workers._record_site_outcome_safely"), \
             patch("indanya_desktop.workers.fail_chatgpt_request") as fail, \
             patch("indanya_desktop.workers._save_chatgpt_generated_article") as save, \
             patch("indanya_desktop.workers.generate_article_text_with_chatgpt") as second_send:
            worker._run_serialized()

        second_send.assert_not_called()
        save.assert_not_called()
        fail.assert_called_once()

    def test_batch_validation_keeps_valid_articles_and_isolates_bad_one(self) -> None:
        entries = [
            {
                "request_id": request_id,
                "options": {
                    "reply_count": "auto",
                    "selected_image_ids": ["media-1"],
                    "selected_video_ids": [],
                },
            }
            for request_id in ("a", "b", "c")
        ]
        valid = {
            "title": "作品の見どころを紹介するタイトル",
            "summary": "作品内容を分かりやすく紹介する概要です。",
            "category": "画像",
            "tags": ["FANZA"],
            "responses": [
                {"text": "このパッケージかなり目を引くな", "style": "normal", "video_ids": []},
                {"text": "衣装の雰囲気も作品に合ってる", "style": "normal", "video_ids": []},
                {"text": "サンプルを見ると内容が分かりやすい", "style": "normal", "video_ids": []},
            ],
        }
        result = _validate_article_batch(
            {
                "articles": [
                    {"request_id": "a", "article": valid},
                    {"request_id": "b", "article": {"title": ""}},
                    {"request_id": "c", "article": valid},
                ]
            },
            entries,
        )
        self.assertEqual({"a", "c"}, set(result["generated"]))
        self.assertIn("b", result["invalid"])

    def test_batch_validation_marks_missing_article_for_single_retry(self) -> None:
        entries = [
            {
                "request_id": "missing",
                "options": {"selected_image_ids": ["media-1"]},
            },
            {
                "request_id": "present",
                "options": {"selected_image_ids": ["media-1"]},
            },
        ]
        article = {
            "title": "作品内容が分かる自然な紹介タイトル",
            "summary": "作品の特徴を簡潔にまとめた記事概要です。",
            "category": "画像",
            "tags": ["作品紹介"],
            "responses": [
                {"text": "雰囲気が伝わるパッケージだな", "style": "normal", "video_ids": []},
                {"text": "表情が自然で内容も気になる", "style": "normal", "video_ids": []},
                {"text": "作品ページでサンプルを確認したい", "style": "normal", "video_ids": []},
            ],
        }
        result = _validate_article_batch(
            {"articles": [{"request_id": "present", "article": article}]},
            entries,
        )
        self.assertIn("present", result["generated"])
        self.assertEqual("返答に記事がありません", result["invalid"]["missing"])


if __name__ == "__main__":
    unittest.main()
