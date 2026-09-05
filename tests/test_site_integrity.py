from __future__ import annotations

import json
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from validate_article import validate_database  # noqa: E402


class ReferenceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if value and key.lower() in {"href", "src", "poster"}:
                self.references.append(value)


class SiteIntegrityTests(unittest.TestCase):
    def test_home_declares_brand_favicon(self) -> None:
        source = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon" href="assets/common/favicon.ico"', source)
        self.assertTrue((ROOT / "assets" / "common" / "favicon.ico").is_file())
        self.assertTrue((ROOT / "assets" / "common" / "favicon.png").is_file())
        self.assertTrue((ROOT / "assets" / "common" / "apple-touch-icon.png").is_file())

    def test_article_template_stacks_images_without_black_frames(self) -> None:
        template = (ROOT / "articles" / "pool-look-back.html").read_text(encoding="utf-8")
        image_style = template[template.index(".image-group {"):template.index(".highlight {")]
        self.assertIn("flex-direction:column", image_style)
        self.assertIn("background:transparent", image_style)
        self.assertIn("aspect-ratio:auto", image_style)
        self.assertNotIn("grid-template-columns", image_style)

    def test_published_articles_and_images_exist(self) -> None:
        database = validate_database(
            json.loads((ROOT / "data" / "articles.json").read_text(encoding="utf-8"))
        )
        published = [article for article in database if article["status"] == "published"]
        self.assertTrue(published)
        self.assertEqual(
            published,
            sorted(published, key=lambda article: article["published_at"], reverse=True),
        )

        for article in published:
            self.assertTrue((ROOT / article["url"]).is_file())
            self.assertTrue((ROOT / article["thumbnail"]).is_file())
            image_directory = ROOT / "assets" / "articles" / article["slug"]
            image_files = [
                path
                for path in image_directory.iterdir()
                if (
                    path.is_file()
                    and path.name.startswith("image-")
                    and path.suffix.lower() in {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
                )
            ]
            self.assertEqual(len(image_files), article["images_used"])

    def test_home_catalog_pages_and_articles_have_no_broken_local_references(self) -> None:
        pages = [
            ROOT / "index.html",
            ROOT / "latest.html",
            ROOT / "popular.html",
            ROOT / "random.html",
            ROOT / "search.html",
            ROOT / "tags.html",
            ROOT / "categories.html",
            ROOT / "fanza.html",
            ROOT / "about.html",
            ROOT / "advertising.html",
            ROOT / "editorial.html",
            ROOT / "removal.html",
            ROOT / "faq.html",
            ROOT / "advertise.html",
            ROOT / "age-check.html",
            ROOT / "privacy.html",
            ROOT / "contact.html",
            ROOT / "partners.html",
            *sorted((ROOT / "articles").glob("*.html")),
        ]
        for page in pages:
            source = page.read_text(encoding="utf-8")
            self.assertNotIn("data:image/", source)
            collector = ReferenceCollector()
            collector.feed(source)
            for reference in collector.references:
                parsed = urlparse(reference)
                if parsed.scheme or parsed.netloc or reference.startswith("#"):
                    continue
                local = (page.parent / parsed.path).resolve()
                self.assertTrue(local.is_relative_to(ROOT.resolve()), reference)
                self.assertTrue(local.exists(), f"{page.name}: {reference}")

    def test_home_renderer_avoids_html_string_insertion(self) -> None:
        script = (ROOT / "assets" / "common" / "site.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn('article.status === "published"', script)

    def test_home_feature_rotates_without_changing_on_every_reload(self) -> None:
        script = (ROOT / "assets" / "common" / "site.js").read_text(encoding="utf-8")
        self.assertIn("const featureRotationDays = 1", script)
        self.assertIn("selectFeaturedArticle(articles)", script)
        self.assertIn("articles.filter(isAdultFeatureCandidate)", script)
        self.assertIn("const latestSlugs = new Set(articles.slice(0, 8)", script)
        self.assertIn("article.slug !== featured.slug", script)
        self.assertIn("featureBadge.textContent = featured.category", script)
        self.assertNotIn("`${featured.category} / ${featuredImageCount}枚`", script)
        self.assertNotIn("articles.find(article => article.featured === true)", script)
        self.assertIn('classList.add("home-ready")', script)
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("html:not(.home-ready) .feature", index)
        self.assertIn("注目記事", index)
        self.assertNotIn("TODAY'S PICK", index)

    def test_catalog_pages_share_search_and_article_data(self) -> None:
        catalog_script = (ROOT / "assets" / "common" / "catalog.js").read_text(encoding="utf-8")
        self.assertIn("article.search_text", catalog_script)
        self.assertIn("article.tags", catalog_script)
        for filename in (
            "latest.html",
            "popular.html",
            "random.html",
            "search.html",
            "tags.html",
            "categories.html",
            "fanza.html",
        ):
            source = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn('action="search.html"', source)
            self.assertIn("assets/common/catalog.js", source)
        for filename in ("latest.html", "popular.html", "random.html", "search.html", "tags.html", "fanza.html"):
            source = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn('id="catalogPagination"', source)
        self.assertIn("const pageSize = 24", catalog_script)
        self.assertIn('page === "categories"', catalog_script)
        self.assertIn('page === "fanza"', catalog_script)

    def test_articles_load_related_content_assets(self) -> None:
        self.assertTrue((ROOT / "assets" / "common" / "article-related.css").is_file())
        script_path = ROOT / "assets" / "common" / "article-related.js"
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")
        self.assertIn("この記事に近い記事", script)
        self.assertIn("同じ人物・テーマの記事", script)
        self.assertIn("おすすめ記事", script)
        self.assertNotIn("関連するおすすめAV記事", script)
        self.assertIn(".filter(entry => entry.relation > 0)", script)
        self.assertIn("article.append(discovery)", script)
        self.assertIn("relationScore", script)
        for path in sorted((ROOT / "articles").glob("*.html")):
            source = path.read_text(encoding="utf-8")
            self.assertIn("../assets/common/article-related.css", source, path.name)
            self.assertIn("../assets/common/article-related.js", source, path.name)

    def test_adult_disclosure_and_age_gate_cover_the_site(self) -> None:
        required_pages = [
            ROOT / "index.html",
            ROOT / "latest.html",
            ROOT / "popular.html",
            ROOT / "random.html",
            ROOT / "search.html",
            ROOT / "tags.html",
            ROOT / "categories.html",
            ROOT / "fanza.html",
            ROOT / "about.html",
            ROOT / "advertising.html",
            ROOT / "editorial.html",
            ROOT / "removal.html",
            ROOT / "faq.html",
            ROOT / "advertise.html",
            ROOT / "privacy.html",
            ROOT / "contact.html",
            ROOT / "partners.html",
        ]
        self.assertTrue((ROOT / "age-check.html").is_file())
        self.assertTrue((ROOT / "assets" / "common" / "age-gate.js").is_file())
        for path in required_pages:
            source = path.read_text(encoding="utf-8")
            self.assertIn("アフィリエイト広告を利用しています", source, path.name)
            self.assertIn("assets/common/age-gate.js", source, path.name)
            self.assertIn("advertising.html", source, path.name)
        for path in sorted((ROOT / "articles").glob("*.html")):
            source = path.read_text(encoding="utf-8")
            self.assertIn("アフィリエイト広告を利用しています", source, path.name)
            self.assertIn("../assets/common/age-gate.js", source, path.name)
            self.assertIn("../advertising.html", source, path.name)

    def test_required_affiliate_application_pages_have_substantive_content(self) -> None:
        about = (ROOT / "about.html").read_text(encoding="utf-8")
        privacy = (ROOT / "privacy.html").read_text(encoding="utf-8")
        contact = (ROOT / "contact.html").read_text(encoding="utf-8")
        advertising = (ROOT / "advertising.html").read_text(encoding="utf-8")
        age_check = (ROOT / "age-check.html").read_text(encoding="utf-8")
        self.assertIn("淫談屋編集部", about)
        self.assertIn("DMM・FANZA", privacy)
        self.assertIn("issues/new", contact)
        self.assertNotIn("準備中", contact)
        self.assertIn("成果報酬", advertising)
        self.assertIn("18歳未満の方は閲覧できません", age_check)

    def test_partner_page_has_complete_listing_kit(self) -> None:
        source = (ROOT / "partners.html").read_text(encoding="utf-8")
        self.assertIn("サイト運営者の方へ", source)
        self.assertIn("feed.xml", source)
        self.assertIn("サイト情報をコピー", source)
        self.assertIn("リンクHTMLをコピー", source)
        self.assertIn("indanya-logo.png", source)
        self.assertIn("contact.html", source)
        self.assertTrue((ROOT / "feed.xml").is_file())


if __name__ == "__main__":
    unittest.main()
