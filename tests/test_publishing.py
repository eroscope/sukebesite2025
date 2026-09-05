from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from indanya_desktop import publishing  # noqa: E402
from article_studio import _load_database  # noqa: E402
from add_article import load_database as load_publish_database  # noqa: E402
from indanya_desktop.editorial_policy import FANZA_MEDIA_PROFILE, POLICY_VERSION  # noqa: E402
from indanya_desktop.fanza_affiliate import save_fanza_settings  # noqa: E402
from indanya_desktop.sites import ManagedSite  # noqa: E402
from test_article_studio import make_payload  # noqa: E402


def git(*arguments: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


class PublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.seed = self.root / "seed"
        (self.seed / "articles").mkdir(parents=True)
        (self.seed / "data").mkdir(parents=True)
        shutil.copy2(ROOT / "articles" / "pool-look-back.html", self.seed / "articles" / "pool-look-back.html")
        shutil.copytree(ROOT / "assets" / "common", self.seed / "assets" / "common")
        (self.seed / "data" / "articles.json").write_text("[]\n", encoding="utf-8")
        git("init", "-b", "main", cwd=self.seed)
        git("add", ".", cwd=self.seed)
        git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Seed", cwd=self.seed)
        self.remote = self.root / "remote.git"
        git("clone", "--bare", str(self.seed), str(self.remote))
        self.draft_root = self.root / "drafts"
        self.draft_root.mkdir()
        save_fanza_settings(self.draft_root, "publish-owner-001")
        self.site = ManagedSite(
            site_id="test-site",
            name="テストサイト",
            public_url="https://example.com/site/",
            local_path=str(self.draft_root),
            repository_url="https://github.com/example/site",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _checkout(self, name: str) -> Path:
        destination = self.root / name
        git("clone", str(self.remote), str(destination))
        return destination

    def _publishable_product_payload(self) -> dict[str, object]:
        payload = make_payload()
        product_url = "https://video.dmm.co.jp/av/content/?id=abc001"
        payload.update({
            "title": "【画像】成人向けテスト作品、公式商品画像で内容を確認",
            "summary": "成人向けテスト作品の公開処理を確認するため、同一商品IDの公式パッケージと商品紹介画像を掲載するテスト記事です。",
            "source_url": product_url,
            "content_mode": "fanza_product",
            "fanza_product_id": "abc001",
            "media_rights_profile": FANZA_MEDIA_PROFILE,
            "editorial_policy_version": POLICY_VERSION,
            "editorial_policy_status": "adult_approved",
            "originality_checked": True,
            "media_alignment_checked": True,
        })
        payload["images"][0].update({
            "source_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
            "rights_basis": "fanza_product_main_image",
        })
        second_image = dict(payload["images"][0])
        second_image.update({
            "id": "source-image-2",
            "name": "sample.png",
            "source_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001jp-1.jpg",
            "rights_basis": "fanza_product_sample_image",
        })
        payload["images"].append(second_image)
        payload["blocks"].insert(-1, {
            "id": "sample-image",
            "type": "images",
            "image_ids": ["source-image-2"],
        })
        payload["blocks"].insert(-1, {
            "id": "post-c",
            "type": "post",
            "text": "公式商品画像まで確認できた",
            "style": "normal",
        })
        payload["blocks"].insert(-1, {
            "id": "product-cta",
            "type": "product_cta",
            "url": product_url,
            "title": "成人向けテスト作品",
            "text": "FANZAで作品情報を見る",
            "button_text": "FANZAで作品を見る",
        })
        return payload

    def test_dash_manifest_is_materialized_as_a_playable_mp4(self) -> None:
        destination = self.root / "x-video.mp4"

        def fake_materialize(url: str, output: Path, referer: str = "") -> Path:
            self.assertTrue(url.endswith("/stream.mpd"))
            self.assertEqual("https://x.com/Test_User/status/1", referer)
            output.write_bytes(b"x-video" * 200)
            return output

        with patch.object(publishing, "_materialize_stream_video", side_effect=fake_materialize):
            result = publishing._download_video(
                {
                    "url": "https://video.twimg.com/amplify_video/1/pl/stream.mpd",
                    "referer": "https://x.com/Test_User/status/1",
                },
                destination,
            )

        self.assertEqual(destination, result)
        self.assertEqual(b"x-video" * 200, destination.read_bytes())

    def test_full_checkout_cache_does_not_fail_sparse_extension(self) -> None:
        error = RuntimeError("Git処理に失敗しました: fatal: no sparse-checkout to add to")
        with patch.object(publishing, "_run_git", side_effect=error) as run_git:
            publishing._extend_sparse_checkout_if_enabled(self.root, "article-slug")

        run_git.assert_called_once()

    def test_sparse_extension_keeps_unrelated_git_errors(self) -> None:
        with patch.object(
            publishing,
            "_run_git",
            side_effect=RuntimeError("Git処理に失敗しました: fatal: repository not found"),
        ):
            with self.assertRaisesRegex(RuntimeError, "repository not found"):
                publishing._extend_sparse_checkout_if_enabled(self.root, "article-slug")

    def test_article_database_accepts_utf8_bom(self) -> None:
        database = self.root / "bom-site" / "data" / "articles.json"
        database.parent.mkdir(parents=True)
        database.write_text("[]\n", encoding="utf-8-sig")

        self.assertEqual([], _load_database(database.parents[1]))
        self.assertEqual([], load_publish_database(database))

    def test_missing_public_template_is_borrowed_only_while_rendering(self) -> None:
        repository = self.root / "template-free-public"
        source = self.draft_root / "articles" / "pool-look-back.html"
        source.parent.mkdir(parents=True)
        source.write_text("template", encoding="utf-8")
        target = repository / "articles" / "pool-look-back.html"

        with publishing._temporary_render_template(repository, self.draft_root):
            self.assertEqual("template", target.read_text(encoding="utf-8"))

        self.assertFalse(target.exists())

    def test_publish_and_unpublish_round_trip(self) -> None:
        payload = self._publishable_product_payload()
        payload["rights_status"] = "confirmed"
        progress: list[tuple[int, str]] = []
        with patch.object(publishing, "_repository_url", return_value=str(self.remote)):
            result = publishing.publish_article(
                payload,
                self.draft_root,
                self.site,
                lambda value, message: progress.append((value, message)),
            )

        self.assertEqual("published", result["status"])
        self.assertEqual("https://example.com/site/articles/studio-check.html", result["url"])
        published = self._checkout("published")
        self.assertTrue((published / "articles" / "studio-check.html").is_file())
        rendered = (published / "articles" / "studio-check.html").read_text(encoding="utf-8")
        self.assertIn("https://al.dmm.com/?lurl=", rendered)
        self.assertIn("af_id=publish-owner-001", rendered)
        self.assertIn(
            '<link rel="canonical" href="https://example.com/site/articles/studio-check.html">',
            rendered,
        )
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', rendered)
        self.assertTrue((published / "assets" / "articles" / "studio-check" / "image-01.png").is_file())
        database = json.loads((published / "data" / "articles.json").read_text(encoding="utf-8"))
        self.assertEqual(["studio-check"], [item["slug"] for item in database])
        sitemap = (published / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn(
            "https://example.com/site/articles/studio-check.html",
            sitemap,
        )
        self.assertIn(
            "Sitemap: https://example.com/site/sitemap.xml",
            (published / "robots.txt").read_text(encoding="utf-8"),
        )
        self.assertTrue((published / "sitemap-images.xml").is_file())
        self.assertTrue((published / "sitemap-videos.xml").is_file())
        self.assertTrue((published / "feed.xml").is_file())
        self.assertTrue((published / "people.html").is_file())
        self.assertTrue((published / "works.html").is_file())
        self.assertTrue((published / "topics.html").is_file())
        self.assertEqual(
            3,
            (published / "robots.txt").read_text(encoding="utf-8").count("Sitemap:"),
        )
        saved = json.loads((self.draft_root / ".article-studio" / "drafts" / "studio-check.json").read_text(encoding="utf-8"))
        saved_product = next(block for block in saved["blocks"] if block["type"] == "product_cta")
        self.assertEqual(
            "https://video.dmm.co.jp/av/content/?id=abc001",
            saved_product["url"],
        )
        self.assertEqual("published", saved["editorial_status"])
        self.assertEqual("published", saved["review_status"])
        self.assertEqual(result["url"], saved["published_url"])
        self.assertEqual(100, progress[-1][0])

        with patch.object(publishing, "_repository_url", return_value=str(self.remote)):
            removed = publishing.unpublish_article(saved, self.draft_root, self.site)

        self.assertEqual("draft", removed["status"])
        unpublished = self._checkout("unpublished")
        self.assertNotIn(
            "articles/studio-check.html",
            (unpublished / "sitemap.xml").read_text(encoding="utf-8"),
        )
        self.assertFalse((unpublished / "articles" / "studio-check.html").exists())
        self.assertFalse((unpublished / "assets" / "articles" / "studio-check").exists())
        self.assertEqual([], json.loads((unpublished / "data" / "articles.json").read_text(encoding="utf-8")))
        draft = json.loads((self.draft_root / ".article-studio" / "drafts" / "studio-check.json").read_text(encoding="utf-8"))
        self.assertEqual("draft", draft["editorial_status"])
        self.assertNotIn("published_url", draft)

    def test_publish_rejects_product_pr_when_affiliate_id_is_missing(self) -> None:
        (self.draft_root / ".article-studio" / "fanza.json").unlink()
        payload = self._publishable_product_payload()
        payload["rights_status"] = "confirmed"

        with self.assertRaisesRegex(RuntimeError, "アフィリエイトIDが未設定"):
            publishing.publish_article(payload, self.draft_root, self.site)

    def test_existing_published_pr_is_replaced_after_id_change(self) -> None:
        payload = self._publishable_product_payload()
        payload["rights_status"] = "confirmed"
        with patch.object(publishing, "_repository_url", return_value=str(self.remote)):
            publishing.publish_article(payload, self.draft_root, self.site)
            result = publishing.publish_fanza_affiliate_update(
                self.draft_root,
                self.site,
                "replacement-owner-002",
            )

        self.assertGreaterEqual(result["published_links"], 1)
        published = self._checkout("affiliate-replaced")
        rendered = (published / "articles" / "studio-check.html").read_text(encoding="utf-8")
        self.assertIn("af_id=replacement-owner-002", rendered)
        self.assertNotIn("af_id=publish-owner-001", rendered)

    def test_publish_requires_confirmed_rights(self) -> None:
        payload = make_payload()
        payload["rights_status"] = "unconfirmed"
        with self.assertRaisesRegex(RuntimeError, "許可管理"):
            publishing.publish_article(payload, self.draft_root, self.site)

    def test_localize_videos_downloads_and_rewrites_article(self) -> None:
        site_root = self.root / "video-site"
        article_root = site_root / "articles"
        article_root.mkdir(parents=True)
        source_url = "https://media.example.com/movie.mp4?token=1&part=2"
        escaped_url = "https://media.example.com/movie.mp4?token=1&amp;part=2"
        article_path = article_root / "video-check.html"
        article_path.write_text(f'<video><source src="{escaped_url}"></video>', encoding="utf-8")
        payload = {
            "slug": "video-check",
            "videos": [{
                "id": "video-1",
                "kind": "direct",
                "url": source_url,
                "referer": "https://example.com/article",
                "mime_type": "video/mp4",
                "poster_data_url": "data:image/jpeg;base64," + base64.b64encode(b"poster-data").decode("ascii"),
            }],
        }
        article_path.write_text(
            (
                f'<video poster="{payload["videos"][0]["poster_data_url"]}">'
                f'<source src="{escaped_url}"></video>'
            ),
            encoding="utf-8",
        )

        with patch("urllib.request.urlopen", return_value=BytesIO(b"test-video-data")):
            publishing._localize_videos(site_root, payload, lambda _value, _message: None)

        localized = site_root / "assets" / "articles" / "video-check" / "video-01.mp4"
        self.assertEqual(b"test-video-data", localized.read_bytes())
        poster = site_root / "assets" / "articles" / "video-check" / "video-01-poster.jpg"
        self.assertEqual(b"poster-data", poster.read_bytes())
        rendered = article_path.read_text(encoding="utf-8")
        self.assertIn("../assets/articles/video-check/video-01.mp4", rendered)
        self.assertIn("../assets/articles/video-check/video-01-poster.jpg", rendered)
        self.assertNotIn("data:image/", rendered)
        self.assertNotIn("media.example.com", rendered)

    def test_large_video_is_compressed_and_mime_is_rewritten(self) -> None:
        site_root = self.root / "large-video-site"
        article_root = site_root / "articles"
        article_root.mkdir(parents=True)
        source_url = "https://media.example.com/movie.webm"
        article_path = article_root / "large-video.html"
        article_path.write_text(
            f'<video><source src="{source_url}" type="video/webm"></video>',
            encoding="utf-8",
        )
        payload = {
            "slug": "large-video",
            "videos": [{
                "id": "video-1",
                "kind": "direct",
                "url": source_url,
                "referer": "https://example.com/article",
                "mime_type": "video/webm",
            }],
        }

        class LargeResponse(BytesIO):
            pass

        compressed_bytes = b"compressed-mp4"

        def fake_compress(source: Path, destination: Path) -> None:
            self.assertTrue(source.stat().st_size > publishing.MAX_PUBLISH_VIDEO_BYTES)
            destination.write_bytes(compressed_bytes)

        oversized = b"x" * 11
        with (
            patch("urllib.request.urlopen", return_value=LargeResponse(oversized)),
            patch.object(publishing, "_compress_video", side_effect=fake_compress),
            patch.object(publishing, "MAX_PUBLISH_VIDEO_BYTES", 10),
        ):
            publishing._localize_videos(site_root, payload, lambda _value, _message: None)

        localized = site_root / "assets" / "articles" / "large-video" / "video-01.mp4"
        self.assertEqual(compressed_bytes, localized.read_bytes())
        rendered = article_path.read_text(encoding="utf-8")
        self.assertIn('src="../assets/articles/large-video/video-01.mp4"', rendered)
        self.assertIn('type="video/mp4"', rendered)

    def test_publish_drops_only_video_that_cannot_fit_size_limit(self) -> None:
        payload = make_payload()
        payload["rights_status"] = "confirmed"
        payload["category"] = "動画"
        payload["videos"] = [
            {
                "id": "video-too-large",
                "kind": "direct",
                "url": "https://media.example.com/too-large.mp4",
                "mime_type": "video/mp4",
                "label": "大容量動画",
            },
            {
                "id": "video-usable",
                "kind": "direct",
                "url": "https://media.example.com/usable.mp4",
                "mime_type": "video/mp4",
                "label": "使用可能な動画",
            },
        ]
        payload["blocks"].insert(
            1,
            {
                "id": "videos-a",
                "type": "videos",
                "video_ids": ["video-too-large", "video-usable"],
            },
        )

        def fake_download(video: dict[str, object], destination: Path) -> Path:
            if video["id"] == "video-too-large":
                raise RuntimeError("動画をGitHub Pagesの上限内まで小さくできませんでした")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"usable-video")
            return destination

        with (
            patch.object(publishing, "_repository_url", return_value=str(self.remote)),
            patch.object(publishing, "_download_video", side_effect=fake_download),
            patch.object(publishing, "require_publishable_article", return_value=None),
        ):
            publishing.publish_article(payload, self.draft_root, self.site)

        published = self._checkout("video-size-fallback")
        rendered = (published / "articles" / "studio-check.html").read_text(encoding="utf-8")
        self.assertNotIn("too-large.mp4", rendered)
        self.assertIn("../assets/articles/studio-check/video-02.mp4", rendered)
        saved = json.loads(
            (self.draft_root / ".article-studio" / "drafts" / "studio-check.json").read_text(encoding="utf-8")
        )
        self.assertEqual(["video-usable"], [video["id"] for video in saved["videos"]])


if __name__ == "__main__":
    unittest.main()
