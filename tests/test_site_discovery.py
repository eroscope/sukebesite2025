from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from indanya_desktop.site_discovery import (  # noqa: E402
    _entity_slug,
    _write_hubs,
    article_entities,
    refresh_site_discovery,
)


def _article(slug: str, title: str, date: str) -> dict[str, object]:
    return {
        "slug": slug,
        "url": f"articles/{slug}.html",
        "thumbnail": f"assets/articles/{slug}/image-01.jpg",
        "title": title,
        "summary": "公式素材と公開情報を確認し、見どころを独自のコメントで紹介する記事です。",
        "status": "published",
        "published_at": date,
        "display_date": "2026.08.25",
        "category": "動画",
        "tags": ["宮下玲奈", "制服", "ABC-123"],
    }


def _article_html(slug: str, title: str, include_video: bool = False) -> str:
    video = ""
    if include_video:
        video = f'''
        <video class="article-video" poster="../assets/articles/{slug}/poster.jpg">
          <source src="../assets/articles/{slug}/sample.mp4" type="video/mp4">
        </video>
        <iframe class="article-video" src="https://www.youtube.com/embed/example"></iframe>
        <iframe class="advertisement" src="https://ads.example/frame"></iframe>
        '''
    return f'''<!doctype html><html lang="ja"><head>
    <meta charset="utf-8"><meta name="description" content="test"><title>{title}</title>
    <script src="../assets/common/article-related.js?v=analytics-v8" defer></script>
    </head><body><article><h1>{title}</h1>
    <img src="../assets/articles/{slug}/image-01.jpg" alt="{title}">
    {video}</article></body></html>'''


def test_entity_detection_ignores_generic_tags_and_finds_work_code() -> None:
    article = _article("one", "【動画】宮下玲奈、ABC-123の制服作品", "2026/08/25 1:03:12")

    entities = article_entities(article)

    assert entities["people"] == ["宮下玲奈"]
    assert entities["works"] == ["ABC-123"]
    assert "制服" in entities["topics"]
    assert "動画" not in entities["topics"]


def test_refresh_generates_seo_hubs_feeds_and_media_sitemaps() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        (site / "articles").mkdir()
        (site / "data").mkdir()
        (site / "assets" / "common").mkdir(parents=True)
        (site / "index.html").write_text(
            '<!doctype html><html><head><title>淫談屋</title></head><body><nav class="nav"><div class="nav-inner"></div></nav></body></html>',
            encoding="utf-8",
        )
        articles = [
            _article("one", "【動画】宮下玲奈、ABC-123の制服作品", "2026/08/25 1:03:12"),
            _article("two", "【画像】宮下玲奈、ABC-123の制服カット", "2026-08-24T23:00:00+09:00"),
        ]
        for index, article in enumerate(articles):
            slug = str(article["slug"])
            (site / "articles" / f"{slug}.html").write_text(
                _article_html(slug, str(article["title"]), include_video=index == 0),
                encoding="utf-8",
            )

        stats = refresh_site_discovery(site, "https://example.com/site/", articles)
        first_article = (site / "articles" / "one.html").read_text(encoding="utf-8")
        first_home = (site / "index.html").read_text(encoding="utf-8")

        assert stats == {
            "articles": 2,
            "people": 1,
            "works": 1,
            "topics": 1,
            "images": 2,
            "videos": 2,
        }
        assert '<link rel="canonical" href="https://example.com/site/articles/one.html">' in first_article
        assert '<meta property="og:title"' in first_article
        assert '<meta name="twitter:card" content="summary_large_image">' in first_article
        assert '"@type":"BlogPosting"' in first_article
        assert '"datePublished":"2026-08-25T01:03:12+09:00"' in first_article
        assert 'content="adult"' in first_article
        assert 'article-related.js?v=20260825-2' in first_article
        assert "この話題を続けて見る" in first_article
        assert "articles/two.html" in first_article
        assert (site / "people.html").is_file()
        assert (site / "works.html").is_file()
        assert (site / "topics.html").is_file()
        assert len(list((site / "people").glob("*.html"))) == 1
        assert len(list((site / "works").glob("*.html"))) == 1
        assert len(list((site / "topics").glob("*.html"))) == 1
        assert "人物" in first_home and "作品" in first_home and "ジャンル" in first_home
        assert (site / "feed.xml").is_file()
        assert (site / "assets" / "common" / "article-discovery.css").is_file()

        sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
        image_sitemap = (site / "sitemap-images.xml").read_text(encoding="utf-8")
        video_sitemap = (site / "sitemap-videos.xml").read_text(encoding="utf-8")
        robots = (site / "robots.txt").read_text(encoding="utf-8")
        assert "<lastmod>2026-08-25</lastmod>" in sitemap
        assert "https://example.com/site/assets/articles/one/image-01.jpg" in image_sitemap
        assert "https://example.com/site/assets/articles/one/sample.mp4" in video_sitemap
        assert "https://www.youtube.com/embed/example" in video_sitemap
        assert "ads.example" not in video_sitemap
        assert robots.count("Sitemap:") == 3
        for filename in ("sitemap.xml", "sitemap-images.xml", "sitemap-videos.xml", "feed.xml"):
            ET.parse(site / filename)

        refresh_site_discovery(site, "https://example.com/site/", articles)
        assert first_article == (site / "articles" / "one.html").read_text(encoding="utf-8")
        assert first_home == (site / "index.html").read_text(encoding="utf-8")


def test_detail_hubs_are_generated_beyond_the_index_display_limit() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        article = {
            "url": "articles/example.html",
            "thumbnail": "assets/articles/example/image-01.jpg",
            "title": "記事",
            "summary": "概要",
            "display_date": "2026.09.01",
            "category": "画像",
        }
        topics = {
            f"topic-{index:03d}": [dict(article), dict(article)]
            for index in range(241)
        }

        generated = _write_hubs(
            site,
            "https://example.com/site/",
            {},
            {},
            topics,
        )

        last_label = "topic-240"
        last_path = f"topics/{_entity_slug('topic', last_label)}.html"
        assert last_path in generated
        assert (site / last_path).is_file()
