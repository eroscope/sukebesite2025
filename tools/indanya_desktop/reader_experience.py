"""Small public indexes and crawlable lists, rebuilt by the publishing pipeline."""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

VERSION = "20260914-reader1"
PAGE_SIZE = 24
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class _Element(HTMLParser):
    def __init__(self, source: str, target: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source, self.target = source, target
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.depth = 0
        self.tag = ""
        self.bounds: tuple[int, int] | None = None

    def position(self) -> int:
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self.bounds:
            return
        if not self.depth and dict(attrs).get("id") == self.target:
            self.tag, self.depth = tag, 1
            self.start = self.position() + len(self.get_starttag_text())
        elif self.depth and tag == self.tag:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == self.tag:
            self.depth -= 1
            if not self.depth:
                self.bounds = self.start, self.position()


def replace_content(source: str, element_id: str, content: str) -> str:
    parser = _Element(source, element_id)
    parser.feed(source)
    if not parser.bounds:
        return source
    start, end = parser.bounds
    return source[:start] + content + source[end:]


def _write(path: Path, source: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == source:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(source, encoding="utf-8", newline="")
    temporary.replace(path)


def _json(path: Path, value: Any) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compact_articles(articles: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in sorted(articles, key=lambda row: str(row.get("published_at") or ""), reverse=True):
        slug = str(item.get("slug") or "")
        if not SLUG.fullmatch(slug) or slug in seen or item.get("status") != "published":
            continue
        if item.get("url") != f"articles/{slug}.html":
            continue
        thumb = str(item.get("thumbnail") or "")
        if not thumb.startswith("assets/") or ".." in thumb or "//" in thumb:
            thumb = "assets/common/indanya-logo.png"
        seen.add(slug)
        result.append({
            "slug": slug, "url": item["url"], "title": str(item.get("title") or slug),
            "thumbnail": thumb, "status": "published",
            "published_at": str(item.get("published_at") or ""),
            "display_date": str(item.get("display_date") or ""),
            "category": str(item.get("category") or "記事"),
            "summary": str(item.get("summary") or "")[:160],
            "tags": [str(tag) for tag in item.get("tags", []) if tag][:12],
        })
    return result


def _card(item: dict, *, eager: bool = False) -> str:
    e = html.escape
    return (
        f'<article class="post-card"><a class="thumb" href="{e(item["url"], quote=True)}">'
        f'<img src="{e(item["thumbnail"], quote=True)}" alt="" loading="{"eager" if eager else "lazy"}" decoding="async"></a>'
        '<div class="card-body"><div class="card-meta">'
        f'<span>{e(item["category"])}</span><span>{e(item["display_date"])}</span></div>'
        f'<h2><a href="{e(item["url"], quote=True)}">{e(item["title"])}</a></h2></div></article>'
    )


def _rank(item: dict) -> str:
    e = html.escape
    return (f'<a class="rank rank-with-thumb" href="{e(item["url"], quote=True)}">'
            f'<img src="{e(item["thumbnail"], quote=True)}" alt="" loading="lazy">'
            f'<div><b>{e(item["title"])}</b><span>{e(item["display_date"])}</span></div></a>')


def _page_url(number: int) -> str:
    return "latest.html" if number == 1 else f"latest-{number}.html"


def _pager(current: int, total: int) -> str:
    nodes = []
    numbers = sorted({1, total, *range(max(1, current - 2), min(total, current + 2) + 1)})
    if current > 1:
        nodes.append(f'<a rel="prev" href="{_page_url(current - 1)}">前へ</a>')
    for number in numbers:
        active = ' aria-current="page" class="active"' if number == current else ""
        nodes.append(f'<a href="{_page_url(number)}"{active}>{number}</a>')
    if current < total:
        nodes.append(f'<a rel="next" href="{_page_url(current + 1)}">次へ</a>')
    return "".join(nodes)


def _shell(source: str) -> str:
    source = re.sub(r'\s+data-reader-static="[^"]*"', "", source)
    source = source.replace("<body", '<body data-reader-static="true"', 1)
    source = source.replace("今日の人気記事", "新着記事").replace("人気記事", "記事一覧")
    source = re.sub(r'<section class="sidebox">\s*<h2 class="side-title">最新コメント</h2>[\s\S]*?</section>', '<section class="sidebox"><h2 class="side-title">また読む</h2><div class="sidebody"><a href="saved.html">保存・閲覧履歴</a><br><a href="feed.xml">新着記事のRSS</a></div></section>', source)
    source = re.sub(r'<script\b[^>]*src="assets/common/catalog\.js[^\"]*"[^>]*></script>', "", source)
    source = re.sub(r'<link\b[^>]*rel="canonical"[^>]*>\n?', "", source)
    source = re.sub(r'<link\b[^>]*href="assets/common/reader\.css[^\"]*"[^>]*>\n?', "", source)
    source = source.replace("</head>", f'<link rel="stylesheet" href="assets/common/reader.css?v={VERSION}">\n</head>', 1)
    return source


def refresh_reader_experience(repository: Path, public_url: str, articles: list[dict]) -> list[str]:
    items = compact_articles(articles)
    data = repository / "data" / "reader"
    _json(data / "catalog.json", items)
    _json(data / "home.json", items[:48])
    search_text = {str(row.get("slug")): str(row.get("search_text") or "") for row in articles}
    _json(data / "search.json", [{**item, "search_text": search_text.get(item["slug"], "")} for item in items])
    template_path = repository / "latest.html"
    generated = []
    if template_path.exists():
        template = _shell(template_path.read_text(encoding="utf-8"))
        count = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        for number in range(1, count + 1):
            source = template
            title = "新着記事" if number == 1 else f"新着記事 {number}ページ"
            source = re.sub(r"<title>.*?</title>", f"<title>{title}｜淫談屋</title>", source, count=1)
            source = replace_content(source, "pageTitle", title)
            source = replace_content(source, "pageDescription", f"{len(items)}記事 / {number}ページ目")
            source = replace_content(source, "catalogGrid", "".join(_card(item, eager=i < 2) for i, item in enumerate(items[(number - 1) * PAGE_SIZE:number * PAGE_SIZE])))
            source = replace_content(source, "popularArticles", "".join(map(_rank, items[:5])))
            source = replace_content(source, "catalogPagination", _pager(number, count))
            path = _page_url(number)
            canonical = html.escape(urljoin(public_url.rstrip("/") + "/", path), quote=True)
            source = source.replace("</head>", f'<link rel="canonical" href="{canonical}">\n</head>', 1)
            _write(repository / path, source)
            generated.append(path)
        # Only these exact generated filenames are managed here.
        for stale in repository.glob("latest-*.html"):
            if re.fullmatch(r"latest-\d+\.html", stale.name) and stale.name not in generated:
                stale.unlink()
    home_path = repository / "index.html"
    if home_path.exists() and items:
        source = _shell(home_path.read_text(encoding="utf-8"))
        try:
            hubs = json.loads((data / "hubs.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hubs = []
        links = ''.join(f'<a href="{html.escape(str(hub["url"]), quote=True)}">{html.escape(str(hub["label"]))}</a>' for hub in hubs[:5] if re.fullmatch(r'(?:people|topics)/[a-z0-9-]+\.html', str(hub.get("url", ""))))
        if links:
            if 'id="readerFeaturedHubs"' not in source:
                source = source.replace('<section class="card-grid"', '<nav class="reader-hubs" id="readerFeaturedHubs" aria-label="人物・テーマから探す"></nav><section class="card-grid"', 1)
            source = replace_content(source, "readerFeaturedHubs", links)
        source = source.replace('<html lang="ja">', '<html lang="ja" class="home-ready">')
        source = re.sub(r'<script\b[^>]*src="assets/common/site\.js[^\"]*"[^>]*></script>', "", source)
        feature = items[0]
        for element_id in ("breakingLink", "featureTitleLink"):
            source = replace_content(source, element_id, html.escape(feature["title"]))
        source = replace_content(source, "featureSummary", html.escape(feature["summary"]))
        source = replace_content(source, "featureReadMore", "記事を読む")
        source = replace_content(source, "featureBadge", html.escape(feature["category"]))
        for element_id in ("breakingLink", "featureTitleLink", "featureThumbLink", "featureReadMore"):
            source = re.sub(rf'(<a\b[^>]*\bid="{element_id}"[^>]*\bhref=")[^"]*', lambda m: m[1] + html.escape(feature["url"], quote=True), source)
        source = re.sub(r'(<img\b[^>]*\bid="featureImage"[^>]*\bsrc=")[^"]*', lambda m: m[1] + html.escape(feature["thumbnail"], quote=True), source)
        source = re.sub(r'(<img\b[^>]*\bid="featureImage"[^>]*\balt=")[^"]*', lambda m: m[1] + html.escape(feature["title"], quote=True), source)
        source = replace_content(source, "articleGrid", "".join(map(_card, items[1:13])))
        source = replace_content(source, "popularArticles", "".join(map(_rank, items[13:18])))
        source = re.sub(r'<button\b[^>]*data-list-mode="([^"]+)"[^>]*>.*?</button>', lambda m: f'<a href="{"random.html" if m[1] == "random" else "latest.html"}">{"ランダム" if m[1] == "random" else "新着"}</a>' if m[1] != "popular" else "", source)
        source = re.sub(r'(<img\b[^>]*id="featureImage"[^>]*?)\sstyle="[^"]*"', r'\1', source)
        source = source.replace("</head>", f'<link rel="canonical" href="{html.escape(public_url.rstrip("/") + "/", quote=True)}">\n</head>', 1)
        _write(home_path, re.sub(r"(?m)[ \t]+$", "", source))
    return generated
