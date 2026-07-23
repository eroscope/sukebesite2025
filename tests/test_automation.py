from __future__ import annotations

import os
import tempfile
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.automation import add_source, discover_candidates, list_candidates, list_sources  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
