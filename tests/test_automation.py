from __future__ import annotations

import os
import tempfile
import unittest
import json
from datetime import datetime, timedelta
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
    _source_discovery_blocked,
    _source_home_url,
    add_source,
    due_continuous_crawl,
    due_crawl_runs,
    due_permission_publications,
    due_publish_runs,
    enable_continuous_crawl,
    ensure_fanza_manga_source,
    enqueue_article,
    filter_candidates_by_source_mix,
    discover_candidates,
    discover_new_sources,
    list_candidates,
    list_source_discovery_log,
    list_sources,
    is_fanza_manga_candidate,
    load_automation_settings,
    manual_crawl_run,
    manga_replenishment_run,
    mark_candidate_status,
    mark_candidates_status,
    queue_position_map,
    record_automation_run,
    record_continuous_article,
    record_continuous_crawl,
    record_continuous_rate_limit,
    record_source_outcome,
    record_source_selection,
    clear_continuous_rate_limit,
    remove_from_queue,
    save_candidates,
    save_automation_settings,
    sort_candidates_balanced,
    soft_delete_article,
    source_mix_status,
    source_discovery_due,
    source_discovery_status,
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
              <a href="/archives/77777">【動画】小学生のハプニング</a>
              <a href="/tag/cosplay">タグ一覧</a>
              <a href="/archives/cat_2/">カテゴリ一覧</a>
              <a href="/blog-category-0/">ブログ一覧</a>
              <a href="/blog-category-891.html">HTML形式のブログ一覧</a>
              <a href="/?p=2">ページ送り</a>
              <a href="/?tag=performer">タグ検索</a>
              <a href="/image.jpg">画像だけ</a>
            </body></html>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(html)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(1, len(discovered))
            self.assertEqual("https://example.com/archives/12345", discovered[0]["url"])
            self.assertEqual(discovered, list_candidates(root))

    def test_weekly_source_discovery_probes_and_auto_adds_a_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = add_source(root, "既存", "https://existing.example/")
            settings = load_automation_settings(root)
            settings["continuous_source_ids"] = [existing["source_id"]]
            settings["source_discovery_max_additions"] = 2
            save_automation_settings(root, settings)
            pages = {
                "https://fresh.example/": """
                    <html><head><title>エロ画像まとめ Fresh</title></head><body>
                    <a href="/archives/10001">グラビア水着画像その1</a>
                    <a href="/archives/10002">コスプレ動画その2</a>
                    <a href="/archives/10003">ビキニ画像その3</a>
                    </body></html>
                """,
                "https://fresh.example/archives/10001": "<img><img><img>",
                "https://fresh.example/archives/10002": "<img><video></video>",
            }

            result = discover_new_sources(
                root,
                now=datetime(2026, 8, 23, 9, 0, tzinfo=JST),
                search_results=[{
                    "url": "https://fresh.example/archives/10001",
                    "title": "エロ画像まとめ Fresh",
                }],
                fetcher=lambda url: pages[url],
            )

            self.assertTrue(result["ran"])
            self.assertEqual(1, len(result["added"]))
            added = next(item for item in list_sources(root) if item["url"] == "https://fresh.example/")
            self.assertEqual("automatic", added["origin"])
            self.assertGreaterEqual(added["discovery_score"], 60)
            saved = load_automation_settings(root)
            self.assertIn(added["source_id"], saved["continuous_source_ids"])
            self.assertEqual("added", list_source_discovery_log(root)[-1]["status"])

    def test_source_discovery_runs_every_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start = datetime(2026, 8, 23, 9, 0, tzinfo=JST)
            self.assertTrue(source_discovery_due(root, start))
            settings = load_automation_settings(root)
            settings["source_discovery_last_run_at"] = start.isoformat(timespec="seconds")
            settings["source_discovery_interval_days"] = 7
            save_automation_settings(root, settings)
            self.assertFalse(source_discovery_due(root, start + timedelta(days=6, hours=23)))
            self.assertTrue(source_discovery_due(root, start + timedelta(days=7)))
            status = source_discovery_status(root, start + timedelta(days=1))
            self.assertEqual("2026-08-30T09:00:00+09:00", status["next_run_at"])

    def test_source_discovery_preserves_hosted_blog_account_path(self) -> None:
        self.assertEqual(
            "http://blog.livedoor.jp/example/",
            _source_home_url("http://blog.livedoor.jp/example/archives/12345.html"),
        )

    def test_source_discovery_blocks_x_host_without_blocking_name_suffix(self) -> None:
        self.assertTrue(_source_discovery_blocked("https://x.com/example"))
        self.assertTrue(_source_discovery_blocked("https://mobile.x.com/example"))
        self.assertFalse(_source_discovery_blocked("https://erologx.com/"))

    def test_source_discovery_rejects_minor_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = """
                <title>エロ画像まとめ</title>
                <a href="/archives/10001">女子中学生の画像</a>
                <a href="/archives/10002">グラビア画像</a>
                <a href="/archives/10003">水着動画</a>
            """
            result = discover_new_sources(
                root,
                force=True,
                search_results=[{"url": "https://unsafe.example/", "title": "エロまとめ"}],
                fetcher=lambda _url: page,
            )
            self.assertEqual([], result["added"])
            self.assertEqual(1, result["rejected"])
            self.assertIn("未成年", list_source_discovery_log(root)[-1]["reason"])

    def test_source_discovery_rejects_commerce_sites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = """
                <title>コスプレ水着通販ショップ</title>
                <p>商品一覧・税込価格・送料無料</p>
                <button>カートに入れる</button>
                <a href="/product/10001">グラビア水着商品1</a>
                <a href="/product/10002">コスプレ商品2</a>
                <a href="/product/10003">ビキニ商品3</a>
            """
            result = discover_new_sources(
                root,
                force=True,
                search_results=[{"url": "https://shop.example/", "title": "水着まとめ"}],
                fetcher=lambda _url: page,
            )
            self.assertEqual([], result["added"])
            self.assertEqual(1, result["rejected"])
            self.assertIn("通販", list_source_discovery_log(root)[-1]["reason"])

    def test_source_discovery_finds_sites_from_a_discovery_hub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages = {
                "https://hub.example/": """
                    <a href="https://fresh.example/archives/10001">グラビア水着画像1</a>
                    <a href="https://fresh.example/archives/10002">ビキニ画像2</a>
                    <a href="https://fresh.example/archives/10003">エロ動画3</a>
                """,
                "https://fresh.example/": """
                    <title>成人向け画像まとめ</title>
                    <a href="/archives/10001">グラビア水着画像1</a>
                    <a href="/archives/10002">ビキニ画像2</a>
                    <a href="/archives/10003">エロ動画3</a>
                """,
                "https://fresh.example/archives/10001": "<img><img><img>",
                "https://fresh.example/archives/10002": "<img><video></video>",
            }
            with (
                patch("indanya_desktop.automation._search_source_results", return_value=[]),
                patch(
                    "indanya_desktop.automation.SOURCE_DISCOVERY_HUB_URLS",
                    ("https://hub.example/",),
                ),
            ):
                result = discover_new_sources(
                    root,
                    force=True,
                    fetcher=lambda url: pages[url],
                )
            self.assertEqual(1, len(result["added"]))
            self.assertEqual("https://fresh.example/", result["added"][0]["url"])

    def test_candidate_rotation_prefers_the_source_used_less_recently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = add_source(root, "A", "https://a.example/")
            second = add_source(root, "B", "https://b.example/")
            record_source_selection(root, first["source_id"])
            ordered = sort_candidates_balanced(root, [
                {"source_id": first["source_id"], "score": 99, "url": "https://a.example/1"},
                {"source_id": second["source_id"], "score": 30, "url": "https://b.example/1"},
            ])
            self.assertEqual(second["source_id"], ordered[0]["source_id"])

    def test_fanza_candidates_are_blocked_when_recent_ratio_exceeds_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = load_automation_settings(root)
            settings["continuous_fanza_max_percent"] = 20
            settings["continuous_mix_window"] = 10
            save_automation_settings(root, settings)
            draft_root = root / ".article-studio" / "drafts"
            draft_root.mkdir(parents=True)
            for index in range(10):
                source_url = (
                    f"https://video.dmm.co.jp/av/content/?id=test{index}"
                    if index < 8 else f"https://example.com/articles/{index}"
                )
                path = draft_root / f"draft-{index}.json"
                path.write_text(
                    json.dumps({"slug": f"draft-{index}", "source_url": source_url}),
                    encoding="utf-8",
                )
                os.utime(path, (1000 + index, 1000 + index))
            candidates = [
                {"url": "https://video.dmm.co.jp/av/content/?id=next", "score": 99},
                {"url": "https://example.com/articles/next", "score": 40},
            ]
            filtered, status = filter_candidates_by_source_mix(root, candidates)
            self.assertEqual(["https://example.com/articles/next"], [item["url"] for item in filtered])
            self.assertEqual(80.0, status["current_percent"])
            self.assertEqual(1, status["blocked_count"])

    def test_fanza_candidate_is_allowed_after_four_general_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = load_automation_settings(root)
            settings["continuous_fanza_max_percent"] = 20
            save_automation_settings(root, settings)
            draft_root = root / ".article-studio" / "drafts"
            draft_root.mkdir(parents=True)
            for index in range(4):
                path = draft_root / f"general-{index}.json"
                path.write_text(
                    json.dumps({
                        "slug": f"general-{index}",
                        "source_url": f"https://example.com/articles/{index}",
                    }),
                    encoding="utf-8",
                )
                os.utime(path, (1000 + index, 1000 + index))
            status = source_mix_status(root)
            self.assertTrue(status["fanza_allowed"])

    def test_zero_percent_disables_automatic_fanza_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = load_automation_settings(root)
            settings["continuous_fanza_max_percent"] = 0
            save_automation_settings(root, settings)
            filtered, status = filter_candidates_by_source_mix(root, [{
                "url": "https://video.dmm.co.jp/av/content/?id=next",
                "score": 99,
            }])
            self.assertEqual([], filtered)
            self.assertFalse(status["fanza_allowed"])

    def test_empty_source_registry_is_recovered_from_selected_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_id = "34d5fafc3101"
            settings = load_automation_settings(root)
            settings["continuous_source_ids"] = [source_id]
            save_automation_settings(root, settings)
            save_candidates(root, [{
                "url": "https://chaos-giga.com/archives/12345",
                "title": "記事",
                "source_id": source_id,
                "source_name": "混沌戯画",
                "status": "new",
            }])
            recovered = list_sources(root)
            self.assertEqual(1, len(recovered))
            self.assertEqual("https://chaos-giga.com/", recovered[0]["url"])
            self.assertIn("自動復旧", recovered[0]["discovery_note"])

    def test_failing_automatic_source_is_disabled_after_three_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = add_source(
                root,
                "自動候補",
                "https://auto.example/",
                origin="automatic",
            )
            for _ in range(3):
                record_source_outcome(root, source["source_id"], "failed")
            saved = list_sources(root)[0]
            self.assertFalse(saved["enabled"])
            self.assertEqual(3, saved["consecutive_failures"])
            self.assertIn("自動停止", saved["discovery_note"])

    def test_existing_html_category_candidate_is_structurally_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_candidates(root, [{
                "url": "http://example.com/blog-category-891.html",
                "title": "カテゴリ一覧",
                "status": "new",
            }])

            candidates = list_candidates(root)

            self.assertEqual("structure_filtered", candidates[0]["status"])
            self.assertEqual(
                "一覧・カテゴリ・ページ送りURL",
                candidates[0]["filter_reason"],
            )

    def test_known_source_only_accepts_its_real_article_url_shape(self) -> None:
        cases = [
            (
                "https://chaos-giga.com/",
                "/archives/288955",
                "/weekly-ranking",
            ),
            (
                "https://tyoieronews.com/",
                "/archives/1085847569.html",
                "/category/news",
            ),
            (
                "https://bakufu.jp/",
                "/archives/1169265",
                "/ranking",
            ),
            (
                "http://hnalady.com/",
                "/blog-entry-31119.html",
                "/blog-category-891.html",
            ),
        ]
        for source_url, article_path, navigation_path in cases:
            with self.subTest(source_url=source_url):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    add_source(root, "source", source_url)
                    page = (
                        f'<a href="{article_path}">【画像】成人向けグラビア記事</a>'
                        f'<a href="{navigation_path}">20位以降はこちら</a>'
                        '<a href="https://affiliate.example/product/99999">広告商品</a>'
                    ).encode("utf-8")
                    with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                        discovered = discover_candidates(root, per_source_limit=10)
                    self.assertEqual(
                        [source_url.rstrip("/") + article_path],
                        [item["url"] for item in discovered],
                    )

    def test_existing_cross_site_candidate_is_filtered_for_registered_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = add_source(root, "ちょいエロニュース", "https://tyoieronews.com/")
            save_candidates(root, [{
                "url": "https://dlaf.jp/maniax/product/12345.html",
                "title": "外部アフィリエイト商品",
                "source_id": source["source_id"],
                "status": "new",
            }])

            candidate = list_candidates(root)[0]

            self.assertEqual("structure_filtered", candidate["status"])
            self.assertEqual("登録元の実記事URLではないため除外", candidate["filter_reason"])

    def test_child_context_is_rejected_before_chatgpt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "混沌戯画", "https://chaos-giga.com/")
            page = (
                '<a href="/archives/288955">'
                '【画像】子連れプールのママさんが話題</a>'
            ).encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)

            self.assertEqual([], discovered)

    def test_jk_is_not_rejected_as_a_minor_by_keyword_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "テスト", "https://example.com/")
            page = '<a href="/archives/77777">【画像】JKコスプレの撮影が話題</a>'.encode()
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(
                ["https://example.com/archives/77777"],
                [item["url"] for item in discovered],
            )

    def test_explicit_minor_age_is_rejected_even_when_jk_word_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "テスト", "https://example.com/")
            page = """
            <a href="/archives/11111">【画像】JKコスプレの撮影が話題</a>
            <a href="/archives/22222">【画像】17歳のJKが話題</a>
            <a href="/archives/33333">【画像】中2の写真が話題</a>
            <a href="/archives/44444">【動画】海外のJKさん(17)が話題</a>
            """.encode()
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(
                ["https://example.com/archives/11111"],
                [item["url"] for item in discovered],
            )

    def test_repeated_card_title_is_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "テスト", "https://example.com/")
            page = (
                '<a href="/archives/12345">【動画】コスプレ配信が話題 '
                '【動画】コスプレ配信が話題 31コメント</a>'
            ).encode()
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root)
            self.assertEqual(
                "【動画】コスプレ配信が話題 31コメント",
                discovered[0]["title"],
            )

    def test_trend_history_scores_public_reaction_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "テスト", "https://example.com/")
            first_page = (
                '<h2>新着</h2><a href="/archives/12345">'
                '【動画】コスプレ配信が話題</a><span>2コメント</span>'
            ).encode()
            second_page = (
                '<h2>急上昇ランキング</h2><a href="/archives/12345">'
                '【動画】コスプレ配信が話題</a><span>30コメント</span>'
            ).encode()
            first_time = datetime(2026, 7, 31, 10, 0, tzinfo=JST)
            with patch("urllib.request.urlopen", return_value=FakeResponse(first_page)):
                discover_candidates(root, observed_at=first_time)
            first = list_candidates(root)[0]
            with patch("urllib.request.urlopen", return_value=FakeResponse(second_page)):
                discovered = discover_candidates(
                    root,
                    observed_at=first_time + timedelta(hours=1),
                )
            self.assertEqual([], discovered)
            second = list_candidates(root)[0]
            self.assertGreater(second["buzz_score"], first["buzz_score"])
            self.assertEqual(28, second["trend"]["engagement_delta"])
            self.assertTrue(second["trend"]["popular_context"])
            self.assertIn("前回から反応+28", second["trend"]["score_reasons"])
            self.assertIn("人気・急上昇欄", second["trend"]["score_reasons"])

    def test_same_topic_on_multiple_sources_is_a_trend_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "情報源A", "https://a.example/")
            add_source(root, "情報源B", "https://b.example/")
            pages = [
                FakeResponse(
                    '<a href="/archives/12345">人気コスプレイヤーの水着写真が話題</a>'.encode()
                ),
                FakeResponse(
                    '<a href="/post-67890">人気コスプレイヤー、水着写真が話題に</a>'.encode()
                ),
            ]
            with patch("urllib.request.urlopen", side_effect=pages):
                discovered = discover_candidates(root)
            self.assertEqual(2, len(discovered))
            for candidate in list_candidates(root):
                self.assertEqual(2, candidate["trend"]["cross_source_count"])
                self.assertIn("2情報源で同時話題", candidate["trend"]["score_reasons"])

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

    def test_candidate_statuses_can_be_saved_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_path = root / ".article-studio" / "candidates.json"
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_text(
                json.dumps([
                    {"url": "https://example.com/a", "status": "new"},
                    {"url": "https://example.com/b", "status": "new"},
                    {"url": "https://example.com/c", "status": "new"},
                ]),
                encoding="utf-8",
            )
            updated = mark_candidates_status(
                root,
                ["https://example.com/a", "https://example.com/b"],
                "chatgpt_queued",
            )
            self.assertEqual(2, updated)
            statuses = {
                item["url"]: item["status"]
                for item in list_candidates(root)
            }
            self.assertEqual("chatgpt_queued", statuses["https://example.com/a"])
            self.assertEqual("chatgpt_queued", statuses["https://example.com/b"])
            self.assertEqual("new", statuses["https://example.com/c"])

    def test_candidate_statuses_follow_the_durable_processing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = root / ".article-studio"
            studio.mkdir(parents=True)
            (studio / "candidates.json").write_text(
                json.dumps([
                    {"url": "https://example.com/a", "status": "chatgpt_queued"},
                    {"url": "https://example.com/b", "status": "chatgpt_queued"},
                    {"url": "https://example.com/c", "status": "chatgpt_queued"},
                ]),
                encoding="utf-8",
            )
            (studio / "chatgpt-primary-queue.json").write_text(
                json.dumps([
                    {
                        "url": "https://example.com/a",
                        "status": "completed",
                        "completed_at": "2026-09-01T12:00:00+09:00",
                        "draft_slug": "article-a",
                    },
                    {
                        "url": "https://example.com/b",
                        "status": "failed",
                        "completed_at": "2026-09-01T12:01:00+09:00",
                        "last_error": "素材を取得できませんでした",
                    },
                    {
                        "url": "https://example.com/c",
                        "status": "skipped_non_adult",
                        "completed_at": "2026-09-01T12:02:00+09:00",
                    },
                ]),
                encoding="utf-8",
            )

            candidates = {item["url"]: item for item in list_candidates(root)}

            self.assertEqual("drafted", candidates["https://example.com/a"]["status"])
            self.assertEqual("article-a", candidates["https://example.com/a"]["draft_slug"])
            self.assertEqual("failed", candidates["https://example.com/b"]["status"])
            self.assertEqual(
                "素材を取得できませんでした",
                candidates["https://example.com/b"]["last_error"],
            )
            self.assertEqual("ignored", candidates["https://example.com/c"]["status"])

    def test_orphaned_legacy_waiting_candidates_are_not_shown_as_live(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            studio = root / ".article-studio"
            studio.mkdir(parents=True)
            (studio / "candidates.json").write_text(
                json.dumps([
                    {
                        "url": "https://example.com/published",
                        "status": "chatgpt_queued",
                        "attempted_at": "2026-08-01T12:00:00+09:00",
                    },
                    {
                        "url": "https://example.com/orphan",
                        "status": "chatgpt_queued",
                        "attempted_at": "2026-08-01T12:00:00+09:00",
                    },
                ]),
                encoding="utf-8",
            )
            data = root / "data"
            data.mkdir()
            (data / "articles.json").write_text(
                json.dumps([{
                    "slug": "published-article",
                    "source_url": "https://example.com/published",
                }]),
                encoding="utf-8",
            )

            candidates = {item["url"]: item for item in list_candidates(root)}

            self.assertEqual("drafted", candidates["https://example.com/published"]["status"])
            self.assertEqual(
                "published-article",
                candidates["https://example.com/published"]["draft_slug"],
            )
            self.assertEqual("ignored", candidates["https://example.com/orphan"]["status"])
            self.assertEqual(
                "旧バージョンの待機表示を整理",
                candidates["https://example.com/orphan"]["filter_reason"],
            )

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

    def test_fanza_catalog_uses_rendered_product_links_and_canonicalizes_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "FANZA動画", "https://video.dmm.co.jp/")
            product_links = [
                {
                    "href": (
                        "https://video.dmm.co.jp/av/content/?id=savr01149"
                        "&i3_ref=recommend&i3_ord=1"
                    ),
                    "text": "独占最新作 スクワットお姉さん 尾崎えりか",
                    "context": "新着おすすめ作品",
                },
                {
                    "href": (
                        "https://video.dmm.co.jp/av/content/?id=savr01149"
                        "&i3_ref=ranking&i3_ord=2"
                    ),
                    "text": "独占最新作 スクワットお姉さん 尾崎えりか",
                    "context": "人気ランキング",
                },
                {
                    "href": "https://video.dmm.co.jp/av/list/",
                    "text": "作品一覧",
                    "context": "ナビゲーション",
                },
            ]
            with patch(
                "indanya_desktop.browser_capture.collect_rendered_links",
                return_value=product_links,
            ):
                discovered = discover_candidates(root, per_source_limit=10)

            self.assertEqual(1, len(discovered))
            self.assertEqual(
                "https://video.dmm.co.jp/av/content/?id=savr01149",
                discovered[0]["url"],
            )

    def test_fanza_manga_catalog_keeps_its_own_floor_and_prioritizes_sale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = ensure_fanza_manga_source(root)
            product_links = [{
                "href": (
                    "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=d_555001/"
                    "?i3_ref=ranking&i3_ord=1"
                ),
                "text": "人気同人コミック",
                "context": "人気ランキング 期間限定50％OFF セール",
            }]
            with patch(
                "indanya_desktop.browser_capture.collect_rendered_links",
                return_value=product_links,
            ) as collect:
                discovered = discover_candidates(root, per_source_limit=10)

            collect.assert_called_once_with(source["url"])
            self.assertEqual(1, len(discovered))
            self.assertTrue(discovered[0]["trend"]["sale_context"])
            self.assertTrue(discovered[0]["trend"]["popular_context"])
            self.assertIn("セール・割引欄", discovered[0]["trend"]["score_reasons"])
            self.assertIn("人気・急上昇欄", discovered[0]["trend"]["score_reasons"])

    def test_fanza_manga_source_does_not_join_regular_crawl_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordinary = add_source(root, "ordinary", "https://example.com/")
            settings = load_automation_settings(root)
            settings["continuous_source_ids"] = [ordinary["source_id"]]
            save_automation_settings(root, settings)

            manga = ensure_fanza_manga_source(root)
            saved = load_automation_settings(root)

            self.assertEqual([ordinary["source_id"]], saved["continuous_source_ids"])
            run = manga_replenishment_run(
                root,
                manga["source_id"],
                datetime(2026, 9, 1, 19, 30, tzinfo=JST),
            )
            self.assertTrue(run["manga_replenishment"])
            self.assertEqual([manga["source_id"]], run["source_ids"])
            self.assertEqual(1, run["count"])

    def test_manga_replenishment_excludes_other_doujin_formats(self) -> None:
        self.assertTrue(is_fanza_manga_candidate({
            "title": "コミック 人気作品 20%OFF",
            "source_card_text": "販売数 10,000",
        }))
        self.assertFalse(is_fanza_manga_candidate({
            "title": "ゲーム Live2D対応作品",
            "source_card_text": "人気ランキング",
        }))
        self.assertFalse(is_fanza_manga_candidate({
            "title": "ボイス 新作音声",
            "source_card_text": "期間限定セール",
        }))

    def test_ordinary_source_does_not_turn_dmm_ads_into_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "ordinary", "https://example.com/")
            page = """
            <a href="https://video.dmm.co.jp/av/content/?id=ad00001">
              FANZA advertisement product 12345
            </a>
            <a href="/archives/22222">cosplay video article 22222</a>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(
                ["https://example.com/archives/22222"],
                [item["url"] for item in discovered],
            )

    def test_ordinary_source_does_not_collect_external_affiliate_ads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "ordinary", "https://example.com/")
            page = """
            <a href="https://affiliate.example.net/product/12345">
              external affiliate product campaign 12345
            </a>
            <a href="/archives/22222">cosplay video article 22222</a>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(
                ["https://example.com/archives/22222"],
                [item["url"] for item in discovered],
            )

    def test_automatic_crawl_never_retries_a_url_from_processing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            add_source(root, "ordinary", "https://example.com/")
            queue_path = root / ".article-studio" / "chatgpt-primary-queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(json.dumps([{
                "request_id": "old-request",
                "url": "https://example.com/already-checked",
                "status": "skipped_non_adult",
            }]), encoding="utf-8")
            page = """
            <a href="/already-checked">popular cosplay image 12345</a>
            <a href="/fresh">new cosplay video 22222</a>
            """.encode("utf-8")
            with patch("urllib.request.urlopen", return_value=FakeResponse(page)):
                discovered = discover_candidates(root, per_source_limit=10)
            self.assertEqual(
                ["https://example.com/fresh"],
                [item["url"] for item in discovered],
            )


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
                "continuous_mode_enabled": False,
                "continuous_source_ids": ["source-a"],
                "crawl_slots": [
                    {"slot_id": "first", "time": "06:00", "count": 4, "source_ids": ["old-dmm"]},
                    {"slot_id": "second", "time": "12:00", "count": 7, "source_ids": ["old-dmm"]},
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
            self.assertEqual(["source-a"], due_crawl_runs(root, now)[1]["source_ids"])
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

    def test_manual_crawl_count_is_normalized_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = load_automation_settings(root)
            settings["manual_crawl_count"] = 999
            saved = save_automation_settings(root, settings)
            self.assertEqual(100, saved["manual_crawl_count"])

    def test_manual_crawl_uses_the_same_sources_as_continuous_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = load_automation_settings(root)
            settings["continuous_source_ids"] = ["source-a", "source-b"]
            settings["crawl_slots"] = [{
                "slot_id": "legacy",
                "time": "12:00",
                "count": 30,
                "source_ids": ["dmm-source"],
            }]
            save_automation_settings(root, settings)
            run = manual_crawl_run(root, 44)
            self.assertEqual(44, run["count"])
            self.assertEqual(["source-a", "source-b"], run["source_ids"])
            self.assertNotIn("dmm-source", run["source_ids"])

    def test_starting_now_enables_all_continuous_crawl_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = load_automation_settings(root)
            settings.update({
                "auto_crawl_enabled": False,
                "continuous_mode_enabled": False,
                "continuous_crawl_enabled": False,
                "continuous_empty_retry_until": "2026-08-15T23:00:00+09:00",
            })
            save_automation_settings(root, settings)
            enabled = enable_continuous_crawl(root)
            self.assertTrue(enabled["auto_crawl_enabled"])
            self.assertTrue(enabled["continuous_mode_enabled"])
            self.assertTrue(enabled["continuous_crawl_enabled"])
            self.assertEqual("", enabled["continuous_empty_retry_until"])

    def test_continuous_crawl_uses_one_fresh_candidate_without_a_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = load_automation_settings(root)
            settings.update({
                "continuous_mode_enabled": True,
                "continuous_crawl_enabled": True,
                "continuous_max_pending": 8,
                "continuous_empty_retry_minutes": 15,
            })
            save_automation_settings(root, settings)
            now = datetime(2026, 7, 24, 12, 0, tzinfo=JST)
            run = due_continuous_crawl(root, pending_count=0, now=now)
            self.assertIsNotNone(run)
            self.assertTrue(run["continuous"])
            self.assertEqual(1, run["count"])
            self.assertEqual(1, run["target"])
            run = due_continuous_crawl(root, pending_count=5, now=now)
            self.assertIsNone(run)
            self.assertIsNone(due_continuous_crawl(root, pending_count=1, now=now))
            record_continuous_crawl(root, False, now)
            self.assertIsNone(due_continuous_crawl(root, pending_count=0, now=now))
            self.assertIsNotNone(
                due_continuous_crawl(root, pending_count=0, now=now + timedelta(minutes=15))
            )
            record_continuous_crawl(root, True, now)
            self.assertIsNotNone(due_continuous_crawl(root, pending_count=0, now=now))

    def test_rate_limit_uses_separate_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 8, 15, 20, 0, tzinfo=JST)
            first = datetime.fromisoformat(record_continuous_rate_limit(root, now))
            self.assertEqual(now + timedelta(minutes=30), first)
            self.assertIsNone(
                due_continuous_crawl(root, pending_count=0, now=now + timedelta(minutes=15))
            )
            second = datetime.fromisoformat(
                record_continuous_rate_limit(root, now + timedelta(minutes=30))
            )
            self.assertEqual(now + timedelta(minutes=90), second)
            clear_continuous_rate_limit(root)
            settings = load_automation_settings(root)
            self.assertEqual("", settings["continuous_rate_limit_retry_until"])
            self.assertEqual(0, settings["continuous_rate_limit_level"])

    def test_permission_waiting_article_is_never_auto_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._draft(root, "permission-waiting")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.update({
                "rights_status": "requested",
                "rights_updated_at": "2026-07-23T20:59:00+09:00",
                "rights_confirmed": False,
            })
            path.write_text(json.dumps(payload), encoding="utf-8")
            after = datetime(2026, 7, 24, 21, 0, tzinfo=JST)
            self.assertEqual([], due_permission_publications(root, after))


if __name__ == "__main__":
    unittest.main()
