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


if __name__ == "__main__":
    unittest.main()
