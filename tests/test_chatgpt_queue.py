from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.chatgpt_queue import (  # noqa: E402
    complete_chatgpt_request,
    enqueue_chatgpt_request,
    fail_chatgpt_request,
    find_duplicate_drafts,
    get_chatgpt_requests,
    latest_chatgpt_batch_summary,
    mark_chatgpt_processing,
    next_chatgpt_retry_after,
    pending_chatgpt_count,
    queued_chatgpt_request_ids,
    recent_chatgpt_activity,
    reconcile_chatgpt_requests,
    requeue_chatgpt_requests,
    restore_recoverable_validation_failures,
    skip_chatgpt_request,
)


class ChatGptQueueTests(unittest.TestCase):
    @staticmethod
    def product_url(product_id: str) -> str:
        return f"https://video.dmm.co.jp/av/content/?id={product_id}"

    def test_enqueue_deduplicates_pending_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = enqueue_chatgpt_request(root, self.product_url("post") + "&i3_ref=top")
            second = enqueue_chatgpt_request(root, self.product_url("post"))
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertEqual(1, pending_chatgpt_count(root))

    def test_enqueue_deduplicates_completed_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = enqueue_chatgpt_request(root, self.product_url("post"))
            complete_chatgpt_request(root, first["request_id"], "already-made")
            second = enqueue_chatgpt_request(root, self.product_url("post"))
            self.assertEqual(first["request_id"], second["request_id"])
            rows = get_chatgpt_requests(root, [first["request_id"]])
            self.assertEqual(1, len(rows))

    def test_manual_duplicate_request_is_kept_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = enqueue_chatgpt_request(root, self.product_url("post"))
            second = enqueue_chatgpt_request(
                root,
                self.product_url("post"),
                {"force_duplicate": True},
            )
            self.assertNotEqual(first["request_id"], second["request_id"])
            self.assertEqual(2, pending_chatgpt_count(root))

    def test_direct_queue_can_retry_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(3):
                enqueue_chatgpt_request(root, self.product_url(f"auto{index}"))
            request_ids = queued_chatgpt_request_ids(root, limit=2)
            for request_id in request_ids:
                mark_chatgpt_processing(root, request_id)
                fail_chatgpt_request(root, request_id, "temporary failure")
            self.assertEqual(1, len(queued_chatgpt_request_ids(root, limit=10)))
            self.assertEqual(2, requeue_chatgpt_requests(root, request_ids))
            self.assertEqual(3, len(queued_chatgpt_request_ids(root, limit=10)))

    def test_transient_failure_stops_without_a_retry_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("retry"))
            request_id = request["request_id"]
            mark_chatgpt_processing(root, request_id)
            fail_chatgpt_request(root, request_id, "ChatGPT timeout")
            self.assertEqual(0, pending_chatgpt_count(root))
            self.assertEqual("failed", get_chatgpt_requests(root, [request_id])[0]["status"])

    def test_rate_limit_stops_without_retaining_an_old_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("limited"))
            request_id = request["request_id"]
            mark_chatgpt_processing(root, request_id)
            fail_chatgpt_request(root, request_id, "CHATGPT_RATE_LIMIT")
            row = get_chatgpt_requests(root, [request_id])[0]
            self.assertEqual("failed", row["status"])
            self.assertFalse(row.get("retry_after"))
            self.assertEqual(0, len(queued_chatgpt_request_ids(root, limit=1)))

    def test_queue_records_article_stage_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("history"))
            mark_chatgpt_processing(root, request["request_id"])
            events = recent_chatgpt_activity(root)
            browser_event = next(event for event in events if event["phase"] == "browser")
            self.assertIn("記事処理を開始", browser_event["message"])

    def test_fixed_validation_failure_is_requeued_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("validation"))
            mark_chatgpt_processing(root, request["request_id"])
            fail_chatgpt_request(root, request["request_id"], "レスと画像の対応検査が完了していません")
            self.assertEqual(1, restore_recoverable_validation_failures(root))
            self.assertEqual(0, restore_recoverable_validation_failures(root))
            self.assertEqual(1, pending_chatgpt_count(root))

            queue_path = root / ".article-studio" / "chatgpt-primary-queue.json"
            rows = json.loads(queue_path.read_text(encoding="utf-8"))
            rows[0].update({
                "status": "failed",
                "last_error": "レスと画像の対応検査が完了していません",
                "validation_recovery_version": 1,
            })
            queue_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(1, restore_recoverable_validation_failures(root))

    def test_closed_browser_stops_without_retaining_an_old_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("browser"))
            request_id = request["request_id"]
            mark_chatgpt_processing(root, request_id)
            fail_chatgpt_request(root, request_id, "Target page or browser has been closed")
            row = get_chatgpt_requests(root, [request_id])[0]
            self.assertEqual("failed", row["status"])
            self.assertFalse(row.get("retry_after"))

    def test_old_sent_rows_are_archived_not_left_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("old"))
            queue_path = root / ".article-studio" / "chatgpt-primary-queue.json"
            rows = json.loads(queue_path.read_text(encoding="utf-8"))
            rows[0]["status"] = "sent"
            queue_path.write_text(json.dumps(rows), encoding="utf-8")
            reconciled = reconcile_chatgpt_requests(root)
            self.assertEqual(request["request_id"], reconciled[0]["request_id"])
            self.assertEqual("legacy_archived", reconciled[0]["status"])
            self.assertEqual(0, pending_chatgpt_count(root))

    def test_reconcile_marks_matching_draft_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enqueue_chatgpt_request(root, self.product_url("post"))
            drafts = root / ".article-studio" / "drafts"
            drafts.mkdir(parents=True)
            (drafts / "example.json").write_text(
                json.dumps(
                    {
                        "slug": "example",
                        "title": "十分な長さのテスト記事タイトルです",
                        "summary": "十分な長さを持つテスト用の記事概要です。",
                        "category": "画像",
                        "source_url": self.product_url("post"),
                        "images": [],
                        "videos": [],
                        "responses": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = reconcile_chatgpt_requests(root)
            self.assertEqual("archived_duplicate", rows[0]["status"])
            self.assertEqual("example", rows[0]["draft_slug"])

    def test_reconcile_archives_old_duplicate_attempts_for_one_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = enqueue_chatgpt_request(root, self.product_url("post"))
            queue_path = root / ".article-studio" / "chatgpt-primary-queue.json"
            rows = json.loads(queue_path.read_text(encoding="utf-8"))
            duplicate = dict(rows[0])
            duplicate["request_id"] = "old-duplicate"
            duplicate["status"] = "processing"
            rows.append(duplicate)
            queue_path.write_text(json.dumps(rows), encoding="utf-8")
            drafts = root / ".article-studio" / "drafts"
            drafts.mkdir(parents=True)
            (drafts / "example.json").write_text(
                json.dumps({
                    "slug": "example",
                    "title": "Existing",
                    "source_url": self.product_url("post"),
                }),
                encoding="utf-8",
            )
            reconciled = reconcile_chatgpt_requests(root)
            statuses = {item["request_id"]: item["status"] for item in reconciled}
            self.assertEqual("archived_duplicate", statuses[first["request_id"]])
            self.assertEqual("archived_duplicate", statuses["old-duplicate"])

    def test_manual_duplicate_is_not_completed_by_existing_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(
                root,
                self.product_url("post"),
                {"force_duplicate": True},
            )
            drafts = root / ".article-studio" / "drafts"
            drafts.mkdir(parents=True)
            (drafts / "existing.json").write_text(
                json.dumps(
                    {
                        "slug": "existing",
                        "title": "Existing",
                        "source_url": self.product_url("post"),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(1, len(find_duplicate_drafts(root, self.product_url("post"))))
            rows = reconcile_chatgpt_requests(root)
            row = next(item for item in rows if item["request_id"] == request["request_id"])
            self.assertEqual("queued", row["status"])

    def test_direct_processing_lifecycle_records_result_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = enqueue_chatgpt_request(root, self.product_url("first"))
            second = enqueue_chatgpt_request(root, self.product_url("second"))
            mark_chatgpt_processing(root, first["request_id"])
            complete_chatgpt_request(root, first["request_id"], "first-draft")
            mark_chatgpt_processing(root, second["request_id"])
            fail_chatgpt_request(root, second["request_id"], "素材検査エラー")
            rows = get_chatgpt_requests(
                root,
                [first["request_id"], second["request_id"]],
            )
            by_id = {row["request_id"]: row for row in rows}
            self.assertEqual("completed", by_id[first["request_id"]]["status"])
            self.assertEqual("first-draft", by_id[first["request_id"]]["draft_slug"])
            self.assertEqual("failed", by_id[second["request_id"]]["status"])
            self.assertIn("素材検査エラー", by_id[second["request_id"]]["last_error"])

    def test_interrupted_direct_request_is_resumed_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("resume"))
            mark_chatgpt_processing(root, request["request_id"])
            self.assertEqual(
                [request["request_id"]],
                queued_chatgpt_request_ids(root, limit=1),
            )

    def test_latest_automation_batch_summary_tracks_whole_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requests = [
                enqueue_chatgpt_request(
                    root,
                    self.product_url(f"batch{index}"),
                    {"automation_origin": "crawl"},
                )
                for index in range(4)
            ]
            complete_chatgpt_request(root, requests[0]["request_id"], "done")
            fail_chatgpt_request(root, requests[1]["request_id"], "failed")
            skip_chatgpt_request(root, requests[2]["request_id"], "skipped")

            self.assertEqual(
                {
                    "total": 4,
                    "completed": 1,
                    "failed": 1,
                    "skipped": 1,
                    "pending": 1,
                    "processed": 3,
                },
                latest_chatgpt_batch_summary(root),
            )

    def test_non_adult_request_is_recorded_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = enqueue_chatgpt_request(root, self.product_url("skip"))
            skip_chatgpt_request(root, request["request_id"], "一般ニュース")
            row = get_chatgpt_requests(root, [request["request_id"]])[0]
            self.assertEqual("skipped_non_adult", row["status"])
            self.assertIn("一般ニュース", row["last_error"])
            self.assertEqual(0, pending_chatgpt_count(root))

    def test_accepts_general_article_url_without_forcing_fanza_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            row = enqueue_chatgpt_request(
                Path(temp),
                "https://example.com/article",
                {"content_mode": "auto"},
            )
            self.assertEqual("queued", row["status"])
            self.assertEqual("auto", row["options"]["content_mode"])
            self.assertEqual("organic", row["options"]["promotion_type"])

    def test_reconcile_keeps_general_site_request_queued(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue_path = root / ".article-studio" / "chatgpt-primary-queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(json.dumps([{
                "request_id": "legacy",
                "url": "https://example.com/article",
                "status": "queued",
            }]), encoding="utf-8")
            row = reconcile_chatgpt_requests(root)[0]
            self.assertEqual("queued", row["status"])


if __name__ == "__main__":
    unittest.main()
