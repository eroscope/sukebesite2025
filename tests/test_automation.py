from __future__ import annotations

import os
import tempfile
import unittest
import json
from datetime import datetime
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from article_studio import JST  # noqa: E402
from indanya_desktop.automation import (  # noqa: E402
    add_source,
    due_crawl_runs,
    due_publish_runs,
    enqueue_article,
    discover_candidates,
    list_candidates,
    list_sources,
    load_automation_settings,
    mark_candidate_status,
    queue_position_map,
    record_automation_run,
    remove_from_queue,
    save_automation_settings,
    soft_delete_article,
)


class FakeResponse(BytesIO):
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class AutomationTests(unittest.TestCase):
    def _draft(self, root: Path, slug: str) -> Path:
        path = root / ".article-studio" / "drafts" / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"slug": slug, "title": slug, "review_status": "unreviewed"}),
            encoding="utf-8",
        )
        return path

    def test_add_source_and_discover_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = add_source(root, "テストまとめ", "https://example.com/")
            self.assertEqual("テストまとめ", source["name"])
            self.assertEqual(1, len(list_sources(root)))
            html = """
            <html><head><title>home</title></head><body>
              <a href="/archives/12345">【動画】コスプレ配信が話題ｗｗｗ</a>
              <a href="/archives/77777">【動画】JKのハプニング</a>
              <a href="/tag/cosplay">タグ一覧</a>
              <a href="/image.jpg">画像だけ</a>
            </body></html>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(html)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(1, len(discovered))
            self.assertEqual("https://example.com/archives/12345", discovered[0]["url"])
            self.assertEqual(discovered, list_candidates(root))

    def test_candidate_error_is_saved_and_can_be_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_path = root / ".article-studio" / "candidates.json"
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_text(
                json.dumps([{
                    "url": "https://example.com/archives/12345",
                    "title": "候補記事",
                    "status": "new",
                }]),
                encoding="utf-8",
            )

            mark_candidate_status(
                root,
                "https://example.com/archives/12345",
                "new",
                error="Codexの利用上限に達しました",
            )

            candidate = list_candidates(root)[0]
            self.assertEqual("new", candidate["status"])
            self.assertEqual("Codexの利用上限に達しました", candidate["last_error"])
            self.assertTrue(candidate["attempted_at"])

    def test_discover_deduplicates_existing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "", "https://example.com/")
            html = '<a href="/post-1111">画像まとめが話題</a>'.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(html)):
                first = discover_candidates(root, per_source_limit=10)
                second = discover_candidates(root, per_source_limit=10)
            self.assertEqual(1, len(first))
            self.assertEqual(0, len(second))


    def test_discover_skips_google_maps_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "", "https://example.com/")
            html = """
            <a href="https://maps.google.com/?q=test">popular map link 12345</a>
            <a href="/archives/22222">cosplay video article 22222</a>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(html)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(["https://example.com/archives/22222"], [item["url"] for item in discovered])

    def test_queue_is_fifo_and_status_tracks_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._draft(root, "first-article")
            second = self._draft(root, "second-article")
            self.assertEqual(1, enqueue_article(root, "first-article"))
            self.assertEqual(2, enqueue_article(root, "second-article"))
            self.assertEqual(
                {"first-article": 1, "second-article": 2},
                queue_position_map(root),
            )
            self.assertEqual("queued", json.loads(first.read_text(encoding="utf-8"))["review_status"])
            remove_from_queue(root, "first-article", "published")
            self.assertEqual({"second-article": 1}, queue_position_map(root))
            self.assertEqual("published", json.loads(first.read_text(encoding="utf-8"))["review_status"])
            soft_delete_article(root, "second-article")
            self.assertEqual({}, queue_position_map(root))
            self.assertEqual("deleted", json.loads(second.read_text(encoding="utf-8"))["review_status"])

    def test_due_runs_respect_times_completion_and_slot_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for slug in ("one", "two", "three", "four", "five"):
                self._draft(root, slug)
                enqueue_article(root, slug)
            settings = load_automation_settings(root)
            settings.update({
                "crawl_slots": [
                    {"slot_id": "first", "time": "06:00", "count": 4, "source_ids": ["source-a"]},
                    {"slot_id": "second", "time": "12:00", "count": 7, "source_ids": []},
                ],
                "publish_slots": [
                    {"time": "08:00", "count": 2},
                    {"time": "20:00", "count": 2},
                ],
            })
            save_automation_settings(root, settings)
            now = datetime(2026, 7, 24, 21, 0, tzinfo=JST)
            self.assertEqual(
                ["2026-07-24@06:00#first", "2026-07-24@12:00#second"],
                [item["key"] for item in due_crawl_runs(root, now)],
            )
            self.assertEqual(4, due_crawl_runs(root, now)[0]["count"])
            self.assertEqual(["source-a"], due_crawl_runs(root, now)[0]["source_ids"])
            runs = due_publish_runs(root, now)
            self.assertEqual(["one", "two"], runs[0]["slugs"])
            self.assertEqual(["three", "four"], runs[1]["slugs"])
            record_automation_run(root, "crawl", "2026-07-24@06:00#first")
            record_automation_run(root, "publish", "2026-07-24@08:00")
            self.assertEqual(
                ["2026-07-24@12:00#second"],
                [item["key"] for item in due_crawl_runs(root, now)],
            )
            self.assertEqual(["2026-07-24@20:00"], [item["key"] for item in due_publish_runs(root, now)])


if __name__ == "__main__":
    unittest.main()
