from __future__ import annotations

import json
import base64
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from indanya_desktop.social_x import (
    JST,
    _bulk_slots,
    _copy_quality_issues,
    _copy_prompt,
    _draft_media_paths,
    _future_scheduled_time,
    _json_object,
    _assign_random_trend_templates,
    _metric_number,
    _is_official_manga_sales_url,
    _trend_text_allowed,
    _viral_reply_text_allowed,
    _x_pacing_error,
    block_x_reply_handle,
    advance_x_thread,
    canonical_x_status_url,
    choose_x_reply_link,
    generate_x_copies,
    load_x_auto_state,
    load_x_trend_state,
    list_x_posts,
    load_x_settings,
    prepare_x_candidates,
    prepare_x_contest_candidate,
    prepare_x_manga_thread,
    prepare_due_x_manga_thread,
    prepare_due_x_reply_candidate,
    mark_x_manga_replenishing,
    notify_x_manga_article_published,
    prepare_x_viral_reply,
    record_x_post_performance,
    refresh_x_reply_candidate_score,
    refresh_x_article_candidates,
    refresh_x_trend_templates,
    save_x_settings,
    save_x_posts,
    run_x_daily_cycle,
    select_x_daily_posts,
    schedule_x_posts,
    update_x_post,
    validate_x_reply_post,
    x_post_media_paths,
    x_post_intent_url,
    x_reply_intent_url,
    x_thread_intent_url,
    x_template_performance,
    x_daily_posting_status,
    x_follow_candidates,
    x_manga_schedule_status,
    x_reply_schedule_status,
    x_trend_scan_status,
)
from indanya_desktop.fanza_affiliate import save_fanza_settings


class SocialXTests(unittest.TestCase):
    @staticmethod
    def status_id_at(value: datetime) -> str:
        milliseconds = int(value.timestamp() * 1000)
        return str((milliseconds - 1_288_834_974_657) << 22)

    @staticmethod
    def trend_state() -> dict:
        return {
            "last_scan_at": "2026-08-24T08:00:00+09:00",
            "templates": [
                {
                    "template_id": "video_reaction",
                    "name": "動画の一点反応",
                    "media_kinds": ["video"],
                    "hook_style": "映像で最初に目を引く一点へ短く反応する",
                    "body_style": "説明を足さず、その一点で受けた印象だけを続ける",
                    "ending_style": "余韻のある短い口語で終える",
                    "tone": "友人へ見せる軽い口調",
                    "length_target": 70,
                    "avoid": ["記事要約"],
                },
                {
                    "template_id": "image_reaction",
                    "name": "画像の差分反応",
                    "media_kinds": ["images"],
                    "hook_style": "複数画像の中で変化がある点から書き出す",
                    "body_style": "一番印象が変わる画像への率直な反応を続ける",
                    "ending_style": "短い一言で止める",
                    "tone": "自然な口語",
                    "length_target": 65,
                    "avoid": ["宣伝口調"],
                },
            ],
        }

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "assets" / "articles" / "sample").mkdir(parents=True)
        (self.root / "assets" / "articles" / "sample" / "image-01.webp").write_bytes(b"image")
        article_assets = self.root / "assets" / "articles" / "sample-article"
        article_assets.mkdir(parents=True)
        for index in range(1, 6):
            (article_assets / f"image-{index:02d}.jpg").write_bytes(b"image")
        (self.root / "data" / "articles.json").write_text(
            json.dumps([
                {
                    "slug": "sample-article",
                    "title": "【画像】プールサイドで振り返る水着姿が強すぎる",
                    "summary": "プールで撮影された水着姿の写真を中心に紹介する画像記事です。",
                    "category": "画像",
                    "tags": ["水着", "グラビア"],
                    "status": "published",
                    "published_at": "2099-08-03T12:00:00+09:00",
                    "url": "articles/sample-article.html",
                    "thumbnail": "assets/articles/sample/image-01.webp",
                    "images_used": 5,
                    "videos_used": 0,
                },
                {
                    "slug": "not-public",
                    "title": "非公開",
                    "status": "draft",
                    "url": "articles/not-public.html",
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manga_draft(
        self,
        slug: str,
        product_id: str,
        title: str,
        published_at: str,
        trend_context: dict | None = None,
    ) -> dict:
        from PIL import Image

        source_url = (
            "https://www.dmm.co.jp/dc/doujin/-/detail/=/"
            f"cid={product_id}/"
        )
        assets = self.root / "assets" / "articles" / slug
        assets.mkdir(parents=True)
        for index in range(1, 7):
            Image.new("RGB", (640, 900), (index * 20, 60, 90)).save(
                assets / f"image-{index:02d}.jpg"
            )
        payload = {
            "slug": slug,
            "title": title,
            "summary": "FANZA同人の公式試し読みを紹介する記事です。",
            "status": "published",
            "source_url": source_url,
            "published_url": f"https://example.com/articles/{slug}.html",
            "published_at": published_at,
            "tags": ["漫画", "FANZA同人"],
            "images": [
                {"id": f"source-image-{index}"}
                for index in range(1, 7)
            ],
            "blocks": [{
                "type": "images",
                "image_ids": [
                    f"source-image-{index}" for index in range(2, 7)
                ],
            }],
            "automation_trend_context": dict(trend_context or {}),
        }
        drafts = self.root / ".article-studio" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        (drafts / f"{slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return payload

    def test_default_account_is_indanya_sns_and_password_is_never_stored(self) -> None:
        settings = load_x_settings(self.root)
        self.assertEqual("indanya_sns", settings["account_handle"])
        self.assertEqual("https://x.com/indanya_sns", settings["account_url"])
        save_x_settings(self.root, {"bulk_interval_minutes": 45})
        stored = (self.root / ".article-studio" / "x-posting-settings.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("password", stored.casefold())
        self.assertEqual(45, load_x_settings(self.root)["bulk_interval_minutes"])
        self.assertTrue(load_x_settings(self.root)["trend_scan_enabled"])
        self.assertEqual(1000, load_x_settings(self.root)["trend_min_likes"])
        self.assertTrue(load_x_settings(self.root)["safe_pacing_enabled"])
        self.assertEqual(5, load_x_settings(self.root)["daily_post_limit"])
        self.assertEqual(1, load_x_settings(self.root)["reply_daily_limit"])
        self.assertEqual(7, load_x_settings(self.root)["global_daily_action_limit"])
        self.assertEqual(90, load_x_settings(self.root)["global_min_interval_minutes"])
        self.assertEqual(0, load_x_settings(self.root)["reply_link_rate_percent"])
        self.assertFalse(load_x_settings(self.root)["manual_delivery_only"])

    def test_prepare_candidates_only_adds_published_article_once(self) -> None:
        first = prepare_x_candidates(self.root, "https://example.com/site/", limit=3)
        second = prepare_x_candidates(self.root, "https://example.com/site/", limit=3)
        self.assertEqual(1, len(first))
        self.assertEqual([], second)
        self.assertIn("utm_source=x", first[0]["article_url"])
        self.assertIn("utm_content=", first[0]["article_url"])
        self.assertEqual("copy_pending", first[0]["status"])
        self.assertEqual("", first[0]["scheduled_for"])
        self.assertEqual("images", first[0]["media_kind"])
        self.assertEqual(4, first[0]["media_count"])

    def test_safe_pacing_uses_three_spaced_normal_posts_per_day(self) -> None:
        now = datetime(2026, 8, 4, 7, 0, tzinfo=JST)
        settings = {
            "daily_slots": ["08:30", "14:30", "20:30"],
            "daily_post_limit": 3,
            "global_daily_action_limit": 4,
            "global_min_interval_minutes": 180,
        }
        slots = _bulk_slots(settings, 3, now=now)
        parsed = [datetime.fromisoformat(value) for value in slots]
        self.assertEqual(3, len(parsed))
        self.assertEqual(1, len({value.date() for value in parsed}))
        self.assertTrue(all(
            value.strftime("%H:%M") in settings["daily_slots"] for value in parsed
        ))

    def test_normal_posts_and_replies_share_one_pacing_guard(self) -> None:
        now = datetime(2026, 8, 26, 13, 0, tzinfo=JST)
        settings = load_x_settings(self.root)
        rows = []
        for index in range(7):
            timestamp = (now - timedelta(hours=3 + index)).isoformat()
            row = {
                "post_id": f"action-{index}",
                "delivery_mode": "reply" if index == 3 else "post",
                "status": "posted",
                "posted_at": timestamp,
            }
            if index == 3:
                row["reply_completed_at"] = timestamp
            rows.append(row)

        message = _x_pacing_error(settings, rows, now)

        self.assertIn("1日の上限7件", message)

    def test_safe_pacing_reports_the_configured_ninety_minute_interval(self) -> None:
        now = datetime(2026, 8, 26, 13, 0, tzinfo=JST)
        settings = load_x_settings(self.root)
        rows = [{
            "post_id": "normal-post",
            "delivery_mode": "post",
            "status": "posted",
            "posted_at": (now - timedelta(hours=1)).isoformat(),
        }]

        message = _x_pacing_error(settings, rows, now)

        self.assertIn("90分以上", message)

    def test_draft_media_prefers_video_over_images(self) -> None:
        payload = {
            "slug": "video-first",
            "videos": [{
                "kind": "direct",
                "url": "https://media.example.com/movie.mp4",
            }],
            "images": [{
                "data_url": "data:image/png;base64," + base64.b64encode(b"image").decode("ascii"),
            }],
        }

        def fake_download(_video: dict, destination: Path) -> Path:
            destination.write_bytes(b"video" * 300)
            return destination

        with patch("indanya_desktop.social_x._download_video", side_effect=fake_download):
            paths, kind = _draft_media_paths(self.root, payload)
        self.assertEqual("video", kind)
        self.assertEqual(1, len(paths))
        self.assertTrue(Path(paths[0]).is_file())

    def test_copy_prompt_rejects_news_style_and_uses_media_context(self) -> None:
        row = {
            "post_id": "post-1",
            "article_title": "動画記事",
            "article_summary": "縦動画を見た感想をまとめた記事",
            "category": "動画",
            "tags": [],
            "article_url": "https://example.com/article",
            "media_kind": "video",
            "media_count": 1,
        }
        _assign_random_trend_templates([row], self.trend_state())
        prompt = _copy_prompt([row])
        self.assertIn("ネットニュースの見出し", prompt)
        self.assertIn("友達へ共有するような短い一言", prompt)
        self.assertIn('"media_kind": "video"', prompt)
        self.assertIn('"template_id": "video_reaction"', prompt)
        self.assertIn("本文担当", prompt)

    def test_viral_metric_parser_supports_japanese_and_compact_units(self) -> None:
        self.assertEqual(12_000, _metric_number("1.2万 件のいいね"))
        self.assertEqual(3_400, _metric_number("3.4K Likes"))
        self.assertEqual(1_234, _metric_number("1,234"))

    def test_trend_filter_requires_adult_marker_and_rejects_risky_age_or_source(self) -> None:
        self.assertTrue(_trend_text_allowed("成人向けグラビアの水着動画を公開しました"))
        self.assertFalse(_trend_text_allowed("風景写真を公開しました"))
        self.assertFalse(_trend_text_allowed("女子高生の水着グラビア動画"))
        self.assertFalse(_trend_text_allowed("成人向け動画が流出したらしい"))

    def test_random_template_assignment_respects_media_kind_and_records_writers(self) -> None:
        rows = [{"media_kind": "video"}, {"media_kind": "images"}]
        _assign_random_trend_templates(rows, self.trend_state())
        self.assertEqual("video_reaction", rows[0]["trend_template_id"])
        self.assertEqual("image_reaction", rows[1]["trend_template_id"])
        self.assertEqual("Codex", rows[0]["template_writer"])
        self.assertEqual("ChatGPT", rows[0]["copy_writer"])

    def test_daily_trend_status_becomes_due_after_next_scan(self) -> None:
        state_path = self.root / ".article-studio" / "x-trend-templates.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            **self.trend_state(),
            "next_scan_at": "2026-08-25T08:00:00+09:00",
        }, ensure_ascii=False), encoding="utf-8")
        before = datetime(2026, 8, 25, 7, 59, tzinfo=JST)
        after = before + timedelta(minutes=2)
        self.assertFalse(x_trend_scan_status(self.root, before)["due"])
        self.assertTrue(x_trend_scan_status(self.root, after)["due"])

    def test_daily_status_recovers_abandoned_posting_rows(self) -> None:
        now = datetime(2026, 8, 29, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            status="posting",
            scheduled_at=(now - timedelta(hours=2)).isoformat(),
        )

        x_daily_posting_status(self.root, now=now)

        recovered = list_x_posts(self.root)[0]
        self.assertEqual("failed", recovered["status"])
        self.assertIn("完了せず停止", recovered["last_error"])

    def test_elapsed_x_reservation_becomes_posted_during_status_refresh(self) -> None:
        now = datetime(2026, 9, 5, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            status="scheduled",
            scheduled_for=(now - timedelta(hours=2)).isoformat(),
            scheduled_at=(now - timedelta(hours=3)).isoformat(),
        )

        x_daily_posting_status(self.root, now=now)

        matured = list_x_posts(self.root)[0]
        self.assertEqual("posted", matured["status"])
        self.assertEqual("x_reservation_elapsed", matured["delivery_verification"])
        self.assertEqual((now - timedelta(hours=2)).isoformat(), matured["posted_at"])

    def test_viral_reply_filter_rejects_unrelated_results_and_keeps_adult_topics(self) -> None:
        self.assertFalse(
            _viral_reply_text_allowed("投票結果発表 好きなテニス漫画のキャラ Best60")
        )
        self.assertTrue(
            _viral_reply_text_allowed("女湯で本当に女の子か疑われた結果を描いた漫画")
        )
        self.assertTrue(
            _viral_reply_text_allowed("水着グラビアの新作イラストを公開しました")
        )

    def test_follow_candidates_keep_only_relevant_accounts_for_manual_review(self) -> None:
        state_path = self.root / ".article-studio" / "x-trend-templates.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            **self.trend_state(),
            "samples": [
                {
                    "url": "https://x.com/gravure_creator/status/1234567890123456789",
                    "text": "成人向けグラビアの水着写真を公開しました",
                    "likes": 1200,
                    "views": 180000,
                },
                {
                    "url": "https://x.com/tennis_rank/status/1234567890123456790",
                    "text": "投票結果発表 好きなテニス漫画のキャラ Best60",
                    "likes": 5000,
                    "views": 900000,
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        candidates = x_follow_candidates(self.root)

        self.assertEqual(["gravure_creator"], [item["handle"] for item in candidates])
        self.assertEqual("https://x.com/gravure_creator", candidates[0]["profile_url"])

    def test_old_unrelated_viral_candidate_is_not_reused(self) -> None:
        state_path = self.root / ".article-studio" / "x-trend-templates.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            **self.trend_state(),
            "viral_reply_candidates": [{
                "url": "https://x.com/tennis_rank/status/1234567890123456790",
                "topic": "投票結果発表 好きなテニス漫画のキャラ Best60",
                "likes": 5000,
                "views": 900000,
            }],
        }, ensure_ascii=False), encoding="utf-8")

        self.assertIsNone(prepare_x_viral_reply(self.root))

    def test_failed_daily_refresh_keeps_previous_codex_templates(self) -> None:
        state_path = self.root / ".article-studio" / "x-trend-templates.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(self.trend_state(), ensure_ascii=False), encoding="utf-8")
        with patch(
            "indanya_desktop.social_x.collect_x_trend_samples",
            side_effect=RuntimeError("X unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "X unavailable"):
                refresh_x_trend_templates(self.root, force=True)
        saved = load_x_trend_state(self.root)
        self.assertEqual("stale", saved["status"])
        self.assertEqual(2, len(saved["templates"]))
        self.assertIn("X unavailable", saved["last_error"])

    def test_candidate_score_ignores_retired_local_analytics_cache(self) -> None:
        cache = self.root / ".article-studio" / "analytics-cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "articles": [{
                "slug": "sample-article",
                "page_views": 120,
                "pr_clicks": 6,
            }],
        }), encoding="utf-8")
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        self.assertNotIn("閲覧120", post["selection_reason"])
        self.assertNotIn("PRクリック6", post["selection_reason"])

    def test_normal_post_is_fixed_title_and_continue_link_without_ai(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        with patch(
            "indanya_desktop.social_x.ensure_x_trend_templates",
            return_value=self.trend_state(),
        ) as ensure_templates, patch(
            "indanya_desktop.social_x.send_chatgpt_prompt"
        ) as send_prompt:
            completed = generate_x_copies(self.root, [post["post_id"]])
        ensure_templates.assert_not_called()
        send_prompt.assert_not_called()
        self.assertEqual(1, len(completed))
        self.assertEqual("copy_ready", completed[0]["status"])
        self.assertEqual(
            post["article_title"] + "\n\n続きはこちら\n" + post["article_url"],
            completed[0]["post_text"],
        )
        self.assertLessEqual(len(completed[0]["post_text"]), 280)
        self.assertEqual("不要", completed[0]["template_writer"])
        self.assertEqual("固定文", completed[0]["copy_writer"])

    def test_daily_cycle_rewrites_legacy_normal_copy_before_automatic_delivery(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            post_text="AIっぽい古い投稿文 " + post["article_url"],
            status="copy_ready",
            scheduled_for="2026-09-01T20:30+09:00",
            copy_writer="ChatGPT",
        )
        selected = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        with patch(
            "indanya_desktop.social_x.refresh_x_ga4_learning",
            return_value={"refreshed": False},
        ), patch(
            "indanya_desktop.social_x.select_x_daily_posts",
            return_value=[selected],
        ), patch(
            "indanya_desktop.social_x.schedule_x_posts",
            return_value={
                "posted": [],
                "scheduled": [post["post_id"]],
                "failed": [],
            },
        ):
            result = run_x_daily_cycle(self.root, "https://example.com/")
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual([post["post_id"]], result["scheduled"])
        self.assertEqual(
            post["article_title"] + "\n\n続きはこちら\n" + post["article_url"],
            saved["post_text"],
        )
        self.assertEqual("固定文", saved["copy_writer"])

    def test_daily_cycle_recovers_manual_era_rows_with_reserved_times(self) -> None:
        now = datetime(2026, 9, 2, 8, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            post_text="送信待ち",
            status="copy_ready",
            scheduled_for="2026-09-02T14:30+09:00",
        )
        status = x_daily_posting_status(self.root, now=now)
        self.assertTrue(status["due"])
        selected = select_x_daily_posts(
            self.root,
            "https://example.com/",
            now=now,
        )
        self.assertEqual([post["post_id"]], [row["post_id"] for row in selected])

    def test_daily_status_does_not_start_a_second_batch_on_the_same_day(self) -> None:
        now = datetime(2026, 9, 2, 9, 0, tzinfo=JST)
        prepare_x_candidates(self.root, "https://example.com/", limit=1)
        state_path = self.root / ".article-studio" / "x-auto-posting-state.json"
        state_path.write_text(json.dumps({
            "status": "idle",
            "last_attempt_at": "2026-09-02T08:41:43+09:00",
            "last_success_at": "2026-09-02T08:42:24+09:00",
        }), encoding="utf-8")

        status = x_daily_posting_status(self.root, now=now)

        self.assertTrue(status["ran_today"])
        self.assertFalse(status["due"])
        self.assertEqual(
            [],
            select_x_daily_posts(self.root, "https://example.com/", now=now),
        )

    def test_manual_era_rows_outside_daily_batch_lose_fake_reservation(self) -> None:
        now = datetime(2026, 9, 2, 8, 0, tzinfo=JST)
        first = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        second = {**first, "post_id": "manual-era-second"}
        first.update({
            "status": "copy_ready",
            "scheduled_for": "2026-09-02T14:30+09:00",
        })
        second.update({
            "status": "copy_ready",
            "scheduled_for": "2026-09-02T20:30+09:00",
        })
        save_x_posts(self.root, [first, second])
        save_x_settings(self.root, {"daily_post_limit": 1})

        selected = select_x_daily_posts(
            self.root,
            "https://example.com/",
            now=now,
        )
        saved = {row["post_id"]: row for row in list_x_posts(self.root)}

        self.assertEqual(1, len(selected))
        self.assertTrue(saved[selected[0]["post_id"]]["scheduled_for"])
        other_id = "manual-era-second" if selected[0]["post_id"] != "manual-era-second" else first["post_id"]
        self.assertEqual("", saved[other_id]["scheduled_for"])

    def test_article_refresh_updates_stale_queue_but_preserves_manual_copy(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        generate_x_copies(self.root, [post["post_id"]])
        articles = json.loads(
            (self.root / "data" / "articles.json").read_text(encoding="utf-8")
        )
        articles[0]["title"] = "【画像＋動画】件数を出さない新しい記事タイトル"
        (self.root / "data" / "articles.json").write_text(
            json.dumps(articles, ensure_ascii=False),
            encoding="utf-8",
        )
        changed = refresh_x_article_candidates(self.root, "https://example.com/")
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual(1, changed)
        self.assertTrue(saved["post_text"].startswith(articles[0]["title"] + "\n\n"))

        update_x_post(
            self.root,
            post["post_id"],
            post_text="自分で直した投稿文",
            copy_writer="手動編集",
        )
        articles[0]["title"] = "さらに更新した記事タイトル"
        (self.root / "data" / "articles.json").write_text(
            json.dumps(articles, ensure_ascii=False),
            encoding="utf-8",
        )
        refresh_x_article_candidates(self.root, "https://example.com/")
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual("さらに更新した記事タイトル", saved["article_title"])
        self.assertEqual("自分で直した投稿文", saved["post_text"])

    def test_reply_candidate_is_prepared_once_per_day_without_posting(self) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=JST)
        prepared = {
            "post_id": "reply-ready",
            "delivery_mode": "reply",
            "status": "copy_pending",
        }
        with patch(
            "indanya_desktop.social_x.prepare_discovered_x_reply",
            return_value=prepared,
        ) as contest, patch(
            "indanya_desktop.social_x.prepare_x_viral_reply"
        ) as viral, patch(
            "indanya_desktop.social_x.x_trend_scan_status",
            return_value={"due": False},
        ):
            result = prepare_due_x_reply_candidate(
                self.root,
                "https://example.com/",
                now=now,
            )
        self.assertEqual(prepared, result)
        contest.assert_called_once()
        viral.assert_not_called()
        state = load_x_auto_state(self.root)
        self.assertEqual(now.isoformat(timespec="seconds"), state["reply_last_prepared_at"])
        status = x_reply_schedule_status(
            self.root,
            now=now + timedelta(hours=1),
        )
        self.assertFalse(status["due"])

    def test_reply_candidate_retries_later_when_trend_has_no_match(self) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=JST)
        with patch(
            "indanya_desktop.social_x.prepare_discovered_x_reply",
            return_value=None,
        ), patch(
            "indanya_desktop.social_x.prepare_x_viral_reply",
            return_value=None,
        ), patch(
            "indanya_desktop.social_x.x_trend_scan_status",
            return_value={"due": False},
        ):
            result = prepare_due_x_reply_candidate(
                self.root,
                "https://example.com/",
                now=now,
            )
        self.assertIsNone(result)
        state = load_x_auto_state(self.root)
        self.assertEqual(
            (now + timedelta(hours=6)).isoformat(timespec="seconds"),
            state["reply_next_retry_at"],
        )
        self.assertIn("候補がありません", state["reply_last_error"])

    def test_official_manga_product_builds_five_page_self_reply_thread_and_pr(self) -> None:
        from PIL import Image

        slug = "official-manga"
        source_url = "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=d_123456/"
        assets = self.root / "assets" / "articles" / slug
        assets.mkdir(parents=True)
        for index in range(1, 7):
            Image.new("RGB", (640, 900), (index * 20, 60, 90)).save(
                assets / f"image-{index:02d}.jpg"
            )
        drafts = self.root / ".article-studio" / "drafts"
        drafts.mkdir(parents=True)
        payload = {
            "slug": slug,
            "title": "【漫画】ゲームがなかなかクリアできない理由",
            "summary": "FANZA同人の公式試し読みを紹介する記事です。",
            "status": "published",
            "source_url": source_url,
            "published_url": f"https://example.com/articles/{slug}.html",
            "tags": ["漫画", "FANZA同人"],
            "images": [
                {"id": f"source-image-{index}"}
                for index in range(1, 7)
            ],
            "blocks": [
                {
                    "type": "images",
                    "image_ids": [
                        "source-image-2",
                        "source-image-3",
                        "source-image-4",
                        "source-image-5",
                        "source-image-6",
                    ],
                },
                {
                    "type": "product_cta",
                    "url": source_url,
                    "match_type": "exact_image",
                },
            ],
        }
        (drafts / f"{slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        save_fanza_settings(self.root, "test-owner-001")

        post = prepare_x_manga_thread(self.root, "https://example.com/")

        self.assertIsNotNone(post)
        self.assertEqual("thread", post["delivery_mode"])
        self.assertEqual(6, len(post["thread_steps"]))
        self.assertEqual(
            ["image-02.jpg", "image-03.jpg", "image-04.jpg", "image-05.jpg", "image-06.jpg"],
            [Path(step["media_paths"][0]).name for step in post["thread_steps"][:5]],
        )
        self.assertIn("(1/5)", post["thread_steps"][0]["text"])
        self.assertEqual("(5/5)", post["thread_steps"][4]["text"])
        self.assertIn("test-owner-001", post["thread_steps"][5]["text"])
        self.assertIn("d_123456", post["thread_steps"][5]["text"])
        self.assertIn(
            "https://example.com/articles/official-manga.html",
            post["thread_steps"][5]["text"],
        )
        self.assertEqual(
            1,
            post["thread_steps"][5]["text"].count("作品ページはこちら [PR]"),
        )

        first_intent = x_thread_intent_url(
            self.root,
            post["post_id"],
            now=datetime(2026, 9, 1, 12, 0, tzinfo=JST),
        )
        self.assertNotIn("in_reply_to=", first_intent)
        first_url = "https://x.com/indanya_sns/status/1000000000000000001"
        advance_x_thread(self.root, post["post_id"], first_url)
        second_intent = x_thread_intent_url(self.root, post["post_id"])
        self.assertIn("in_reply_to=1000000000000000001", second_intent)

        for index in range(2, 7):
            saved = advance_x_thread(
                self.root,
                post["post_id"],
                f"https://x.com/indanya_sns/status/{1000000000000000000 + index}",
            )
        self.assertEqual("posted", saved["status"])
        self.assertEqual(6, saved["thread_step_index"])
        self.assertEqual(6, len(saved["thread_post_urls"]))
        with self.assertRaisesRegex(ValueError, "送信済み"):
            x_thread_intent_url(self.root, post["post_id"])

    def test_av_product_with_manga_word_is_not_a_manga_sales_page(self) -> None:
        self.assertFalse(_is_official_manga_sales_url(
            "https://video.dmm.co.jp/av/content/?id=nima00078"
        ))
        self.assertTrue(_is_official_manga_sales_url(
            "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=d_123456/"
        ))

    def test_manga_sale_candidate_beats_newer_plain_candidate(self) -> None:
        self._manga_draft(
            "new-plain-manga",
            "d_200001",
            "【漫画】新着の幼馴染ストーリー",
            "2026-08-31T18:00:00+09:00",
            {"buzz_score": 90},
        )
        self._manga_draft(
            "sale-manga",
            "d_200002",
            "【漫画】期間限定50％OFFの同人作品",
            "2026-08-20T18:00:00+09:00",
            {"buzz_score": 20, "sale_context": True},
        )
        save_fanza_settings(self.root, "test-owner-001")

        post = prepare_x_manga_thread(
            self.root,
            "https://example.com/",
            now=datetime(2026, 8, 31, 19, 30, tzinfo=JST),
        )

        self.assertEqual("sale-manga", post["article_slug"])
        self.assertIn("セール・割引欄", post["selection_reason"])

    def test_manga_product_cooldown_and_pending_limit_prevent_duplicates(self) -> None:
        payload = self._manga_draft(
            "cooldown-manga",
            "d_300001",
            "【漫画】同じ作品はしばらく使わない",
            "2026-08-30T18:00:00+09:00",
        )
        save_fanza_settings(self.root, "test-owner-001")
        now = datetime(2026, 8, 31, 19, 30, tzinfo=JST)
        first = prepare_x_manga_thread(self.root, "https://example.com/", now=now)
        self.assertIsNotNone(first)
        self.assertIsNone(
            prepare_x_manga_thread(
                self.root,
                "https://example.com/",
                now=now + timedelta(minutes=1),
            )
        )
        update_x_post(
            self.root,
            first["post_id"],
            status="posted",
            posted_at=now.isoformat(),
        )
        self.assertIsNone(
            prepare_x_manga_thread(
                self.root,
                "https://example.com/",
                now=now + timedelta(days=89),
            )
        )
        self.assertEqual("d_300001", first["thread_product_key"])
        self.assertEqual(payload["source_url"], first["thread_source_product_url"])

    def test_manga_circle_cooldown_blocks_a_different_product_temporarily(self) -> None:
        first_payload = self._manga_draft(
            "circle-series-one",
            "d_310001",
            "【漫画】ギャル姉シリーズ 第一話",
            "2026-08-30T18:00:00+09:00",
            {
                "sale_context": True,
                "source_card_text": (
                    "コミック\nギャル姉シリーズ 第一話\n同じサークル\n"
                    "販売数：10,000\n20%OFF\n800円"
                ),
            },
        )
        self._manga_draft(
            "circle-series-two",
            "d_310002",
            "【漫画】別題の新作 第二話",
            "2026-08-31T18:00:00+09:00",
            {
                "source_card_text": (
                    "コミック\n別題の新作 第二話\n同じサークル\n"
                    "販売数：8,000\n900円"
                ),
            },
        )
        save_fanza_settings(self.root, "test-owner-001")
        now = datetime(2026, 8, 31, 19, 30, tzinfo=JST)

        first = prepare_x_manga_thread(self.root, "https://example.com/", now=now)
        self.assertEqual(first_payload["slug"], first["article_slug"])
        self.assertIn("circle:同じサークル", first["thread_title_keys"])
        update_x_post(
            self.root,
            first["post_id"],
            status="posted",
            posted_at=now.isoformat(),
        )

        self.assertIsNone(prepare_x_manga_thread(
            self.root,
            "https://example.com/",
            now=now + timedelta(days=29),
        ))
        after_cooldown = prepare_x_manga_thread(
            self.root,
            "https://example.com/",
            now=now + timedelta(days=31),
        )
        self.assertEqual("circle-series-two", after_cooldown["article_slug"])

    def test_old_manga_row_with_post_mode_is_recovered_as_thread(self) -> None:
        save_x_posts(self.root, [{
            "post_id": "old-manga-row",
            "origin": "manga_thread",
            "delivery_mode": "post",
            "status": "copy_ready",
            "thread_steps": [{"text": "(1/5)", "media_paths": []}],
        }])

        row = list_x_posts(self.root)[0]

        self.assertEqual("thread", row["delivery_mode"])

    def test_due_manga_schedule_prepares_one_manual_thread(self) -> None:
        self._manga_draft(
            "scheduled-manga",
            "d_400001",
            "【漫画】ランキング上位の漫画",
            "2026-08-31T18:00:00+09:00",
            {"popular_context": True},
        )
        save_fanza_settings(self.root, "test-owner-001")
        now = datetime(2026, 8, 31, 19, 30, tzinfo=JST)
        self.assertTrue(x_manga_schedule_status(self.root, now)["due"])

        post = prepare_due_x_manga_thread(
            self.root,
            "https://example.com/",
            now=now,
        )

        self.assertEqual("thread", post["delivery_mode"])
        status = x_manga_schedule_status(self.root, now + timedelta(minutes=1))
        self.assertFalse(status["due"])
        self.assertTrue(status["blocked_by_pending"])
        self.assertEqual(1, status["pending_count"])

    def test_automatic_manga_delivery_posts_all_pages_and_the_final_pr(self) -> None:
        self._manga_draft(
            "automatic-manga",
            "d_400099",
            "【漫画】自動送信する公式漫画",
            datetime.now(JST).isoformat(),
            {"sale_context": True},
        )
        save_fanza_settings(self.root, "test-owner-001")
        post = prepare_x_manga_thread(self.root, "https://example.com/")
        created_ids = [str(2099000000000000000 + index) for index in range(1, 7)]
        playwright_api = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright_api.chromium.launch_persistent_context.return_value = context
        context.cookies.return_value = [{"name": "auth_token"}]
        context.new_page.return_value = page
        manager = MagicMock()
        manager.__enter__.return_value = playwright_api
        with patch(
            "indanya_desktop.social_x.sync_playwright",
            return_value=manager,
        ), patch(
            "indanya_desktop.social_x._post_one",
            side_effect=created_ids,
        ) as post_one:
            result = schedule_x_posts(self.root, [post["post_id"]])
        self.assertEqual([post["post_id"]], result["posted"])
        self.assertEqual(6, post_one.call_count)
        self.assertEqual("", post_one.call_args_list[0].kwargs["reply_to_id"])
        self.assertEqual(
            created_ids[0],
            post_one.call_args_list[1].kwargs["reply_to_id"],
        )
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual("posted", saved["status"])
        self.assertEqual(6, saved["thread_step_index"])
        self.assertEqual(6, len(saved["thread_post_urls"]))

    def test_manga_replenishment_retries_then_wakes_after_publish(self) -> None:
        payload = self._manga_draft(
            "fresh-manga",
            "d_400002",
            "【漫画】セール中の新しい漫画",
            "2026-09-01T18:00:00+09:00",
            {"sale_context": True},
        )
        now = datetime(2026, 9, 1, 19, 30, tzinfo=JST)

        state = mark_x_manga_replenishing(self.root, now, retry_minutes=120)
        self.assertEqual("2026-09-01T21:30:00+09:00", state["manga_next_retry_at"])
        self.assertIn("補充", state["manga_last_error"])

        self.assertTrue(notify_x_manga_article_published(self.root, payload, now))
        status = x_manga_schedule_status(self.root, now)
        self.assertTrue(status["due"])
        self.assertEqual("", status["last_error"])

    def test_chatgpt_json_accepts_literal_newlines_inside_post_copy(self) -> None:
        value = _json_object(
            '{"posts":[{"post_id":"post-1","selected":"1行目\n2行目"}]}'
        )
        self.assertEqual("1行目\n2行目", value["posts"][0]["selected"])

    def test_past_x_schedule_is_rolled_to_the_next_day(self) -> None:
        now = datetime(2026, 8, 4, 19, 30, tzinfo=JST)
        scheduled = _future_scheduled_time("2026-08-04T08:30+09:00", now)
        self.assertEqual("2026-08-05T08:30+09:00", scheduled.isoformat(timespec="minutes"))

    def test_future_x_schedule_stays_on_the_same_day(self) -> None:
        now = datetime(2026, 8, 4, 19, 30, tzinfo=JST)
        scheduled = _future_scheduled_time("2026-08-04T20:30+09:00", now)
        self.assertEqual("2026-08-04T20:30+09:00", scheduled.isoformat(timespec="minutes"))

    def test_manual_edit_changes_status_to_ready(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            post_text="手直しした投稿文 https://example.com/",
            status="copy_ready",
        )
        saved = list_x_posts(self.root)[0]
        self.assertEqual("copy_ready", saved["status"])
        self.assertTrue(saved["post_text"].startswith("手直し"))

    def test_x_status_url_is_canonicalized_and_profile_url_is_rejected(self) -> None:
        self.assertEqual(
            "https://x.com/sample_user/status/1234567890",
            canonical_x_status_url(
                "https://twitter.com/sample_user/status/1234567890?ref_src=test"
            ),
        )
        with self.assertRaisesRegex(ValueError, "X投稿のURL"):
            canonical_x_status_url("https://x.com/sample_user")

    def test_reply_guard_accepts_recent_matching_opt_in_and_builds_intent(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        target_id = self.status_id_at(now - timedelta(hours=1))
        target_url = f"https://x.com/contest/status/{target_id}"
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url=target_url,
            reply_target_topic="水着画像選手権",
            reply_opt_in_confirmed=True,
            post_text="この振り返りは反則 https://example.com/article",
            status="copy_ready",
        )
        validated = validate_x_reply_post(self.root, post["post_id"], now=now)
        self.assertEqual(target_id, validated["target_id"])
        intent = x_reply_intent_url(self.root, post["post_id"], now=now)
        self.assertIn(f"in_reply_to={target_id}", intent)
        self.assertIn("text=", intent)

    def test_reply_guard_rejects_media_mismatch(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        target_id = self.status_id_at(now - timedelta(hours=1))
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url=f"https://x.com/contest/status/{target_id}",
            reply_target_topic="水着動画選手権",
            reply_opt_in_confirmed=True,
            post_text="これは強い https://example.com/article",
            status="copy_ready",
        )
        with self.assertRaisesRegex(ValueError, "動画ではありません"):
            validate_x_reply_post(self.root, post["post_id"], now=now)

    def test_reply_guard_enforces_interval_and_daily_limit(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        first_id = self.status_id_at(now - timedelta(hours=2))
        second_id = self.status_id_at(now - timedelta(hours=1))
        base = {
            **post,
            "delivery_mode": "reply",
            "reply_target_topic": "水着画像選手権",
            "reply_opt_in_confirmed": True,
            "post_text": "この一枚は強い https://example.com/article",
        }
        completed = {
            **base,
            "post_id": "completed-reply",
            "reply_target_url": f"https://x.com/contest/status/{first_id}",
            "status": "posted",
            "reply_completed_at": (now - timedelta(minutes=30)).isoformat(),
        }
        pending = {
            **base,
            "post_id": "pending-reply",
            "reply_target_url": f"https://x.com/contest/status/{second_id}",
            "status": "copy_ready",
            "reply_completed_at": "",
        }
        save_x_posts(self.root, [completed, pending])
        save_x_settings(self.root, {
            "safe_pacing_enabled": False,
            "reply_daily_limit": 2,
            "reply_min_interval_minutes": 60,
        })
        with self.assertRaisesRegex(ValueError, "間隔"):
            validate_x_reply_post(self.root, "pending-reply", now=now)
        save_x_settings(self.root, {
            "reply_daily_limit": 1,
            "reply_min_interval_minutes": 60,
        })
        with self.assertRaisesRegex(ValueError, "上限1件"):
            validate_x_reply_post(self.root, "pending-reply", now=now)

    def test_manual_delivery_keeps_reply_in_the_official_reply_screen(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            post_text="返信候補 https://example.com/article",
            status="copy_ready",
        )
        save_x_settings(self.root, {"manual_delivery_only": True})
        with patch("indanya_desktop.social_x.sync_playwright") as playwright:
            result = schedule_x_posts(self.root, [post["post_id"]])
        playwright.assert_not_called()
        self.assertEqual([], result["posted"])
        self.assertEqual(1, len(result["failed"]))
        self.assertIn("X公式返信画面", result["failed"][0]["error"])

    def test_manual_normal_post_uses_official_intent_without_browser_scheduler(self) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            post_text="本文を確認してから公式画面で送信する",
            status="copy_ready",
        )
        intent = x_post_intent_url(self.root, post["post_id"], now=now)
        self.assertIn("twitter.com/intent/tweet", intent)
        self.assertIn("text=", intent)
        save_x_settings(self.root, {"manual_delivery_only": True})
        with patch("indanya_desktop.social_x.sync_playwright") as playwright:
            result = schedule_x_posts(self.root, [post["post_id"]])
        playwright.assert_not_called()
        self.assertEqual([], result["scheduled"])
        self.assertIn("X公式投稿画面", result["failed"][0]["error"])

    def test_automatic_normal_post_is_sent_to_the_x_reservation_slot(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        generate_x_copies(self.root, [post["post_id"]])
        playwright_api = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright_api.chromium.launch_persistent_context.return_value = context
        context.cookies.return_value = [{"name": "auth_token"}]
        context.new_page.return_value = page
        manager = MagicMock()
        manager.__enter__.return_value = playwright_api
        with patch(
            "indanya_desktop.social_x.sync_playwright",
            return_value=manager,
        ), patch(
            "indanya_desktop.social_x._x_safe_attachment_paths",
            return_value=[],
        ), patch("indanya_desktop.social_x._schedule_one") as schedule_one:
            result = schedule_x_posts(self.root, [post["post_id"]])
        self.assertEqual([post["post_id"]], result["scheduled"])
        self.assertEqual([], result["failed"])
        schedule_one.assert_called_once()
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual("scheduled", saved["status"])

    def test_automatic_reply_is_sent_as_a_reply_and_records_its_url(self) -> None:
        now = datetime.now(JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        target_id = self.status_id_at(now - timedelta(hours=1))
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url=f"https://x.com/contest/status/{target_id}",
            reply_target_topic="水着画像選手権",
            reply_opt_in_confirmed=True,
            post_text="この振り返りは反則",
            status="copy_ready",
        )
        playwright_api = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright_api.chromium.launch_persistent_context.return_value = context
        context.cookies.return_value = [{"name": "auth_token"}]
        context.new_page.return_value = page
        manager = MagicMock()
        manager.__enter__.return_value = playwright_api
        with patch(
            "indanya_desktop.social_x.sync_playwright",
            return_value=manager,
        ), patch(
            "indanya_desktop.social_x._x_safe_attachment_paths",
            return_value=[],
        ), patch(
            "indanya_desktop.social_x._post_one",
            return_value="2099999999999999999",
        ) as post_one:
            result = schedule_x_posts(self.root, [post["post_id"]])
        self.assertEqual([post["post_id"]], result["posted"])
        self.assertEqual(target_id, post_one.call_args.kwargs["reply_to_id"])
        saved = next(
            row for row in list_x_posts(self.root)
            if row["post_id"] == post["post_id"]
        )
        self.assertEqual("posted", saved["status"])
        self.assertEqual(
            "https://x.com/indanya_sns/status/2099999999999999999",
            saved["x_post_url"],
        )

    def test_reply_candidate_is_scored_and_same_account_has_cooldown(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        first_id = self.status_id_at(now - timedelta(hours=2))
        target_id = self.status_id_at(now - timedelta(hours=1))
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url=f"https://x.com/contest_owner/status/{target_id}",
            reply_target_topic="水着画像選手権",
            reply_opt_in_confirmed=True,
            reply_media_mode="safe_card",
            reply_include_link=False,
        )
        scored = refresh_x_reply_candidate_score(self.root, post["post_id"], now=now)
        self.assertTrue(scored["recommended"])
        self.assertGreaterEqual(scored["score"], 70)

        completed = {
            **list_x_posts(self.root)[0],
            "post_id": "old-reply",
            "reply_target_url": f"https://x.com/contest_owner/status/{first_id}",
            "status": "posted",
            "reply_completed_at": (now - timedelta(days=2)).isoformat(),
        }
        save_x_posts(self.root, [completed, list_x_posts(self.root)[0]])
        rescored = refresh_x_reply_candidate_score(self.root, post["post_id"], now=now)
        self.assertFalse(rescored["recommended"])
        self.assertTrue(any("同じ相手" in value for value in rescored["blockers"]))

    def test_blocked_reply_handle_is_rejected(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=JST)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        target_id = self.status_id_at(now - timedelta(hours=1))
        target_url = f"https://x.com/NoReply/status/{target_id}"
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url=target_url,
            reply_target_topic="水着画像選手権",
            reply_opt_in_confirmed=True,
        )
        self.assertEqual("noreply", block_x_reply_handle(self.root, target_url))
        scored = refresh_x_reply_candidate_score(self.root, post["post_id"], now=now)
        self.assertTrue(any("返信対象外" in value for value in scored["blockers"]))

    def test_reply_link_rate_is_deterministic_and_can_be_disabled(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        target = "https://x.com/contest/status/1234567890123456789"
        save_x_settings(self.root, {
            "safe_pacing_enabled": False,
            "reply_link_rate_percent": 0,
        })
        self.assertEqual(0, load_x_settings(self.root)["reply_link_rate_percent"])
        self.assertFalse(choose_x_reply_link(self.root, post, target))
        save_x_settings(self.root, {"reply_link_rate_percent": 100})
        self.assertTrue(choose_x_reply_link(self.root, post, target))
        self.assertTrue(choose_x_reply_link(self.root, post, target))

    def test_reply_without_link_removes_article_url_from_generated_copy(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_target_url="https://x.com/contest/status/1234567890123456789",
            reply_target_topic="水着画像選手権",
            reply_opt_in_confirmed=True,
            reply_include_link=False,
            reply_link_decided=True,
        )
        response = {"message": json.dumps({"posts": [{
            "post_id": post["post_id"],
            "variants": [f"プールサイドで振り返る一枚、表情まで自然でかなり好き {post['article_url']}"],
            "selected": f"プールサイドで振り返る一枚、表情まで自然でかなり好き {post['article_url']}",
        }]}, ensure_ascii=False)}
        with patch(
            "indanya_desktop.social_x.ensure_x_trend_templates",
            return_value=self.trend_state(),
        ), patch("indanya_desktop.social_x.send_chatgpt_prompt", return_value=response):
            completed = generate_x_copies(self.root, [post["post_id"]])
        self.assertNotIn(post["article_url"], completed[0]["post_text"])
        self.assertEqual(
            "プールサイドで振り返る一枚、表情まで自然でかなり好き",
            completed[0]["post_text"],
        )

    def test_ai_like_abstract_copy_is_rejected(self) -> None:
        row = {"reply_kind": "contest"}
        issues = _copy_quality_issues(
            "画面はかなりスマート、でも中身はしっかり大人向け。このギャップがじわる。",
            [],
            row,
        )
        self.assertTrue(any("定型句" in value for value in issues))

    def test_high_view_viral_post_becomes_conversation_reply_without_promotion(self) -> None:
        now = datetime.now(JST)
        target_id = self.status_id_at(now - timedelta(hours=1))
        post = prepare_x_viral_reply(self.root, {
            "url": f"https://x.com/oznurodr/status/{target_id}",
            "topic": "女湯で本当に女の子か疑われた結果を描いた漫画",
            "views": 1_000_000,
            "likes": 1_700,
            "reposts": 25,
            "replies": 23,
            "target_age_hours": 1,
        })
        self.assertIsNotNone(post)
        self.assertEqual("viral_conversation", post["reply_kind"])
        self.assertFalse(post["reply_opt_in_confirmed"])
        self.assertFalse(post["reply_include_link"])
        self.assertEqual("none", post["reply_media_mode"])
        self.assertGreaterEqual(post["reply_candidate_score"], 70)

    def test_normal_post_uses_original_article_media_without_mosaic(self) -> None:
        from PIL import Image

        source = self.root / "assets" / "articles" / "sample-article" / "image-01.jpg"
        Image.new("RGB", (800, 1200), "#d9a080").save(source)
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        paths = x_post_media_paths(self.root, post["post_id"])
        self.assertEqual(4, len(paths))
        self.assertTrue(all("sns-teaser-" not in Path(path).name for path in paths))
        self.assertEqual("image-01.jpg", Path(paths[0]).name)
        with Image.open(paths[0]) as image:
            self.assertEqual((800, 1200), image.size)

    def test_safe_reply_card_is_generated_instead_of_adult_media(self) -> None:
        from PIL import Image

        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            delivery_mode="reply",
            reply_media_mode="safe_card",
            reply_target_topic="水着画像選手権",
        )
        paths = x_post_media_paths(self.root, post["post_id"])
        self.assertEqual(1, len(paths))
        self.assertIn("reply-card-", Path(paths[0]).name)
        with Image.open(paths[0]) as image:
            self.assertEqual((1200, 675), image.size)

    def test_owned_contest_uses_article_media_and_respects_cooldown(self) -> None:
        first = prepare_x_contest_candidate(self.root, "https://example.com/")
        self.assertIsNotNone(first)
        self.assertEqual("campaign", first["delivery_mode"])
        self.assertIn("選手権", first["campaign_topic"])
        self.assertEqual("images", first["media_kind"])
        self.assertIn("utm_campaign=owned_contest", first["article_url"])
        self.assertIsNone(prepare_x_contest_candidate(self.root, "https://example.com/"))

    def test_performance_record_becomes_template_learning_data(self) -> None:
        post = prepare_x_candidates(self.root, "https://example.com/", limit=1)[0]
        update_x_post(
            self.root,
            post["post_id"],
            trend_template_id="image_reaction",
            trend_template_name="画像の差分反応",
            status="posted",
            scheduled_at="2026-08-25T10:00:00+09:00",
        )
        saved = record_x_post_performance(
            self.root,
            post["post_id"],
            {
                "views": 5000,
                "likes": 180,
                "reposts": 24,
                "replies": 12,
                "link_clicks": 38,
                "post_url": "https://x.com/indanya_sns/status/1234567890123456789",
            },
            captured_at=datetime(2026, 8, 26, 12, 0, tzinfo=JST),
        )
        self.assertEqual("24時間以降", saved["performance"]["measurement"])
        learning = x_template_performance(self.root)
        self.assertEqual(1, learning["image_reaction"]["samples"])
        self.assertGreater(learning["image_reaction"]["average_score"], 0)
        row = {"media_kind": "images"}
        _assign_random_trend_templates([row], self.trend_state(), learning)
        self.assertEqual(1, row["template_learning"]["samples"])


if __name__ == "__main__":
    unittest.main()
