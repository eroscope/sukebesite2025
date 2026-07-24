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

    def test_publish_and_unpublish_round_trip(self) -> None:
        payload = make_payload()
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
        self.assertTrue((published / "assets" / "articles" / "studio-check" / "image-01.png").is_file())
        database = json.loads((published / "data" / "articles.json").read_text(encoding="utf-8"))
        self.assertEqual(["studio-check"], [item["slug"] for item in database])
        saved = json.loads((self.draft_root / ".article-studio" / "drafts" / "studio-check.json").read_text(encoding="utf-8"))
        self.assertEqual("published", saved["editorial_status"])
        self.assertEqual(result["url"], saved["published_url"])
        self.assertEqual(100, progress[-1][0])

        with patch.object(publishing, "_repository_url", return_value=str(self.remote)):
            removed = publishing.unpublish_article(saved, self.draft_root, self.site)

        self.assertEqual("draft", removed["status"])
        unpublished = self._checkout("unpublished")
        self.assertFalse((unpublished / "articles" / "studio-check.html").exists())
        self.assertFalse((unpublished / "assets" / "articles" / "studio-check").exists())
        self.assertEqual([], json.loads((unpublished / "data" / "articles.json").read_text(encoding="utf-8")))
        draft = json.loads((self.draft_root / ".article-studio" / "drafts" / "studio-check.json").read_text(encoding="utf-8"))
        self.assertEqual("draft", draft["editorial_status"])
        self.assertNotIn("published_url", draft)

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
