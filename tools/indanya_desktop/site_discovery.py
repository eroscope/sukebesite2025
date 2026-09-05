from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urljoin


SEO_START = "<!-- INDANYA_SEO_START -->"
SEO_END = "<!-- INDANYA_SEO_END -->"
DISCOVERY_START = "<!-- INDANYA_DISCOVERY_START -->"
DISCOVERY_END = "<!-- INDANYA_DISCOVERY_END -->"
HOME_START = "<!-- INDANYA_HOME_DISCOVERY_START -->"
HOME_END = "<!-- INDANYA_HOME_DISCOVERY_END -->"
HOME_BODY_START = "<!-- INDANYA_HOME_LINKS_START -->"
HOME_BODY_END = "<!-- INDANYA_HOME_LINKS_END -->"
DISCOVERY_VERSION = "20260825-2"

STATIC_PAGES = (
    "",
    "latest.html",
    "popular.html",
    "categories.html",
    "fanza.html",
    "tags.html",
    "people.html",
    "works.html",
    "topics.html",
    "about.html",
    "editorial.html",
    "privacy.html",
    "contact.html",
    "partners.html",
)

WORK_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{2,12}[-_ ]?\d{3,7}|\d{2,4}[A-Za-z]{2,12}[-_ ]?\d{2,7})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
JAPANESE_NAME_PATTERN = re.compile(r"^[\u3040-\u30ff\u3400-\u9fff々〆ヵヶ・]{2,18}$")
JST = timezone(timedelta(hours=9))

GENERIC_TAGS = {
    "18禁", "adult", "av", "fanza", "pr", "sns", "twitter", "x", "x投稿",
    "アダルト", "エロ", "画像", "動画", "話題", "まとめ", "おすすめ", "サンプル",
    "グラビア", "アイドル", "コスプレ", "コスプレイヤー", "ユーチューバー",
}
PERSON_BLOCKLIST = {
    "巨乳", "美乳", "爆乳", "貧乳", "水着", "競泳水着", "制服", "下着", "ランジェリー",
    "素人", "女子大生", "人妻", "熟女", "痴女", "ナース", "ol", "お天気お姉さん",
    "メイド", "黒髪", "ショートヘア", "ロングヘア", "美少女", "美女", "美人",
    "vr", "asmr", "イメージビデオ", "ハメ撮り", "グラドル", "作品", "配信",
    "エロ同人誌", "オタサーの姫", "グラマーボディ", "サキュバスバニー",
    "ヌードルストッパー", "バスツアー", "人妻秘書", "売り子", "女教師",
    "港区女子", "葬式",
}


@dataclass
class MediaEntry:
    images: list[tuple[str, str]] = field(default_factory=list)
    videos: list[dict[str, str]] = field(default_factory=list)


class ArticleMediaParser(HTMLParser):
    def __init__(self, page_url: str, slug: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.slug = slug
        self.images: list[tuple[str, str]] = []
        self.videos: list[dict[str, str]] = []
        self._video_depth = 0
        self._current_video: dict[str, str] | None = None

    @staticmethod
    def _attrs(attributes: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.casefold(): str(value or "") for key, value in attributes}

    def _absolute(self, value: str) -> str:
        return urljoin(self.page_url, html.unescape(value.strip()))

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = self._attrs(attributes)
        tag = tag.casefold()
        if tag == "img":
            source = attrs.get("src", "")
            if source and f"/assets/articles/{self.slug}/" in self._absolute(source):
                item = (self._absolute(source), attrs.get("alt", ""))
                if item[0] not in {url for url, _alt in self.images}:
                    self.images.append(item)
        elif tag == "video":
            self._video_depth += 1
            self._current_video = {
                "poster": self._absolute(attrs["poster"]) if attrs.get("poster") else "",
                "content": self._absolute(attrs["src"]) if attrs.get("src") else "",
                "player": "",
            }
        elif tag == "source" and self._video_depth and self._current_video is not None:
            source = attrs.get("src", "")
            if source and not self._current_video["content"]:
                self._current_video["content"] = self._absolute(source)
        elif tag == "iframe":
            source = attrs.get("src", "")
            classes = set(attrs.get("class", "").split())
            if source and "article-video" in classes:
                self.videos.append({
                    "poster": "",
                    "content": "",
                    "player": self._absolute(source),
                })

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "video" or not self._video_depth:
            return
        self._video_depth -= 1
        if self._current_video and (
            self._current_video.get("content") or self._current_video.get("player")
        ):
            self.videos.append(self._current_video)
        self._current_video = None


def _published_articles(articles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        item for item in articles
        if isinstance(item, dict) and item.get("status") == "published" and item.get("url")
    ]
    result.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return result


def _clean_label(value: Any, maximum: int = 60) -> str:
    return " ".join(str(value or "").replace("#", " ").split())[:maximum].strip()


def _entity_slug(kind: str, label: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    if ascii_slug and len(ascii_slug) >= 2:
        return f"{kind}-{ascii_slug[:72]}"
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def _tags(article: dict[str, Any]) -> list[str]:
    raw = article.get("tags") or []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(
        label for label in (_clean_label(item, 40) for item in raw) if label
    ))


def _work_codes(article: dict[str, Any]) -> list[str]:
    haystack = " ".join([
        str(article.get("title") or ""),
        *_tags(article),
    ])
    result: list[str] = []
    for value in article.get("_discovery_work_codes", []):
        code = _normalize_work_code(str(value))
        if code and code not in result:
            result.append(code)
    for match in WORK_CODE_PATTERN.finditer(unquote(haystack)):
        code = _normalize_work_code(match.group(0))
        if code not in result:
            result.append(code)
    return result[:4]


def _normalize_work_code(value: str) -> str:
    value = unquote(str(value or "")).strip().upper()
    value = re.sub(r"[^A-Z0-9_-]+", "", value)
    value = re.sub(r"[ _]+", "-", value).strip("-")
    match = re.fullmatch(r"1([A-Z]{2,12})[-_]?0*(\d{2,7})V?", value)
    if not match:
        match = re.fullmatch(r"([A-Z]{2,12})[-_]?0*(\d{2,7})", value)
    if match:
        digits = str(int(match.group(2))).zfill(3)
        return f"{match.group(1)}-{digits}"
    match = re.fullmatch(r"(\d{2,4}[A-Z]{2,12})[-_]?0*(\d{2,7})", value)
    if match:
        digits = str(int(match.group(2))).zfill(3)
        return f"{match.group(1)}-{digits}"
    return value


def _html_work_codes(source: str) -> list[str]:
    candidates: list[str] = []
    patterns = (
        r"(?:[?&](?:id|cid)=|/cid=)([A-Za-z0-9_-]{5,32})",
        r"/product/product_detail/([A-Za-z0-9_-]{5,32})/?",
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html.unescape(source), re.IGNORECASE))
    result: list[str] = []
    for candidate in candidates:
        code = _normalize_work_code(candidate)
        if code and code not in result:
            result.append(code)
    return result[:4]


def _enrich_articles(
    repository: Path,
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for article in articles:
        item = dict(article)
        path = repository / str(item.get("url") or "")
        if path.is_file():
            try:
                item["_discovery_work_codes"] = _html_work_codes(
                    path.read_text(encoding="utf-8")
                )
            except OSError:
                pass
        enriched.append(item)
    return enriched


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)
        except ValueError:
            pass
    for format_string in (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, format_string).replace(tzinfo=JST)
        except ValueError:
            continue
    return None


def _iso_datetime(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def _iso_date(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed else ""


def _title_subjects(title: str) -> list[str]:
    cleaned = re.sub(r"^【[^】]+】\s*", "", title.strip())
    if "、" not in cleaned:
        return []
    lead = cleaned.split("、", 1)[0].strip()
    subjects: list[str] = []
    for value in re.split(r"\s*[×＆&／/]\s*", lead):
        value = re.sub(r"[（(][^）)]*[）)]", "", value).strip()
        value = re.sub(r"^[\d.]+cmの", "", value, flags=re.IGNORECASE)
        if value:
            subjects.append(value.replace(" ", ""))
    return subjects


def _is_person_tag(tag: str, title: str) -> bool:
    compact = tag.replace(" ", "")
    folded = compact.casefold()
    subjects = _title_subjects(title)
    if not subjects or folded in GENERIC_TAGS:
        return False
    if folded in PERSON_BLOCKLIST or any(term in folded for term in ("作品", "動画", "画像", "まとめ")):
        return False
    if not any(subject == compact or subject.endswith("の" + compact) for subject in subjects):
        return False
    return bool(JAPANESE_NAME_PATTERN.fullmatch(compact))


def article_entities(article: dict[str, Any]) -> dict[str, list[str]]:
    tags = _tags(article)
    title = str(article.get("title") or "")
    works = _work_codes(article)
    people = [tag.replace(" ", "") for tag in tags if _is_person_tag(tag, title)][:3]
    topics: list[str] = []
    for tag in tags:
        folded = tag.casefold()
        if folded in GENERIC_TAGS or tag in people or any(code.casefold() == folded for code in works):
            continue
        if WORK_CODE_PATTERN.fullmatch(tag):
            continue
        topics.append(tag)
    category = _clean_label(article.get("category"), 40)
    if category and category.casefold() not in GENERIC_TAGS and category not in topics:
        topics.append(category)
    return {"people": people, "works": works, "topics": topics[:8]}


def _group_articles(
    articles: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    people: dict[str, list[dict[str, Any]]] = defaultdict(list)
    works: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        entities = article_entities(article)
        for label in entities["people"]:
            people[label].append(article)
        for label in entities["works"]:
            works[label].append(article)
        for label in entities["topics"]:
            topics[label].append(article)
    return dict(people), dict(works), dict(topics)


def _absolute(base_url: str, value: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", str(value or ""))


def _escape_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _replace_marked(source: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r"[\s\S]*?" + re.escape(end))
    if pattern.search(source):
        return pattern.sub(replacement, source, count=1)
    return source


def _media_from_html(source: str, page_url: str, slug: str) -> MediaEntry:
    parser = ArticleMediaParser(page_url, slug)
    parser.feed(source)
    parser.close()
    return MediaEntry(parser.images, parser.videos)


def _related_articles(
    current: dict[str, Any],
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_entities = article_entities(current)
    current_tags = set(_tags(current))
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for candidate in articles:
        if candidate.get("slug") == current.get("slug"):
            continue
        entities = article_entities(candidate)
        score = 0
        score += len(set(current_entities["works"]) & set(entities["works"])) * 12
        score += len(set(current_entities["people"]) & set(entities["people"])) * 8
        score += len(current_tags & set(_tags(candidate))) * 3
        if current.get("category") == candidate.get("category"):
            score += 1
        if score:
            scored.append((score, str(candidate.get("published_at") or ""), candidate))
    if not scored:
        scored = [
            (1, str(candidate.get("published_at") or ""), candidate)
            for candidate in articles
            if candidate.get("slug") != current.get("slug")
            and candidate.get("category") == current.get("category")
        ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:4]]


def _entity_href(
    kind: str,
    label: str,
    groups: dict[str, list[dict[str, Any]]],
) -> str:
    slug = _entity_slug(kind, label)
    if len(groups.get(label, [])) >= 2:
        directory = {"person": "people", "work": "works", "topic": "topics"}[kind]
        return f"../{directory}/{slug}.html"
    if kind == "person":
        return f"../people.html#entity-{slug}"
    if kind == "work":
        return f"../works.html#entity-{slug}"
    return f"../topics.html#entity-{slug}"


def _discovery_section(
    article: dict[str, Any],
    articles: list[dict[str, Any]],
    people: dict[str, list[dict[str, Any]]],
    works: dict[str, list[dict[str, Any]]],
    topics: dict[str, list[dict[str, Any]]],
) -> str:
    entities = article_entities(article)
    entity_links: list[str] = []
    for kind, labels, groups in (
        ("person", entities["people"], people),
        ("work", entities["works"], works),
        ("topic", entities["topics"], topics),
    ):
        for label in labels:
            href = _entity_href(kind, label, groups)
            entity_links.append(
                f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
            )

    cards: list[str] = []
    for candidate in _related_articles(article, articles):
        href = f'../{html.escape(str(candidate.get("url") or ""), quote=True)}'
        thumbnail = f'../{html.escape(str(candidate.get("thumbnail") or ""), quote=True)}'
        cards.append(
            '<a class="article-related-item" href="' + href + '">'
            f'<img src="{thumbnail}" alt="{html.escape(str(candidate.get("title") or ""), quote=True)}" loading="lazy">'
            '<span><strong>' + html.escape(str(candidate.get("title") or "")) + '</strong>'
            f'<small>{html.escape(str(candidate.get("display_date") or ""))}・{html.escape(str(candidate.get("category") or ""))}</small></span>'
            '</a>'
        )
    if not entity_links and not cards:
        return ""
    entity_markup = (
        '<nav class="article-entity-links" aria-label="関連する人物・作品・ジャンル">'
        + "".join(entity_links)
        + "</nav>"
        if entity_links else ""
    )
    cards_markup = (
        '<div class="article-related-list">' + "".join(cards) + "</div>"
        if cards else ""
    )
    return (
        f"{DISCOVERY_START}\n"
        '<section class="article-static-discovery">'
        '<h2>この話題を続けて見る</h2>'
        f"{entity_markup}{cards_markup}"
        '</section>\n'
        f"{DISCOVERY_END}"
    )


def _seo_markup(
    article: dict[str, Any],
    base_url: str,
    media: MediaEntry,
) -> str:
    canonical = _absolute(base_url, str(article.get("url") or ""))
    title = str(article.get("title") or "淫談屋")
    summary = str(article.get("summary") or title)[:240]
    thumbnail = _absolute(base_url, str(article.get("thumbnail") or ""))
    images = list(dict.fromkeys([thumbnail, *(url for url, _alt in media.images)]))
    published_at = _iso_datetime(
        article.get("published_at") or article.get("display_date")
    )
    tags = _tags(article)
    structured: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "headline": title,
        "description": summary,
        "image": images,
        "datePublished": published_at,
        "dateModified": published_at,
        "articleSection": str(article.get("category") or ""),
        "keywords": tags,
        "isFamilyFriendly": False,
        "contentRating": "adult",
        "author": {"@type": "Organization", "name": "淫談屋", "url": base_url.rstrip("/") + "/"},
        "publisher": {"@type": "Organization", "name": "淫談屋", "url": base_url.rstrip("/") + "/"},
    }
    video_objects: list[dict[str, Any]] = []
    for index, video in enumerate(media.videos, start=1):
        item: dict[str, Any] = {
            "@type": "VideoObject",
            "name": f"{title} 動画{index}",
            "description": summary,
            "thumbnailUrl": [video.get("poster") or thumbnail],
            "uploadDate": published_at,
            "isFamilyFriendly": False,
            "contentRating": "adult",
        }
        if video.get("content"):
            item["contentUrl"] = video["content"]
        if video.get("player"):
            item["embedUrl"] = video["player"]
        video_objects.append(item)
    if video_objects:
        structured["video"] = video_objects

    tags_markup = "\n".join(
        f'<meta property="article:tag" content="{html.escape(tag, quote=True)}">'
        for tag in tags
    )
    return f'''{SEO_START}
<meta name="robots" content="index,follow,max-image-preview:large,max-video-preview:-1,max-snippet:-1">
<meta name="rating" content="adult">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<link rel="alternate" type="application/rss+xml" title="淫談屋 新着記事" href="{html.escape(_absolute(base_url, 'feed.xml'), quote=True)}">
<meta property="og:site_name" content="淫談屋">
<meta property="og:type" content="article">
<meta property="og:title" content="{html.escape(title, quote=True)}">
<meta property="og:description" content="{html.escape(summary, quote=True)}">
<meta property="og:url" content="{html.escape(canonical, quote=True)}">
<meta property="og:image" content="{html.escape(thumbnail, quote=True)}">
<meta property="og:image:alt" content="{html.escape(title, quote=True)}">
<meta property="article:published_time" content="{html.escape(published_at, quote=True)}">
<meta property="article:section" content="{html.escape(str(article.get('category') or ''), quote=True)}">
{tags_markup}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html.escape(title, quote=True)}">
<meta name="twitter:description" content="{html.escape(summary, quote=True)}">
<meta name="twitter:image" content="{html.escape(thumbnail, quote=True)}">
<meta name="twitter:image:alt" content="{html.escape(title, quote=True)}">
<link rel="stylesheet" href="../assets/common/article-discovery.css?v={DISCOVERY_VERSION}">
<script type="application/ld+json">{_escape_json(structured)}</script>
{SEO_END}'''


def _augment_article(
    repository: Path,
    base_url: str,
    article: dict[str, Any],
    articles: list[dict[str, Any]],
    people: dict[str, list[dict[str, Any]]],
    works: dict[str, list[dict[str, Any]]],
    topics: dict[str, list[dict[str, Any]]],
) -> MediaEntry:
    path = repository / str(article.get("url") or "")
    if not path.is_file():
        return MediaEntry()
    source = path.read_text(encoding="utf-8")
    source = re.sub(
        r'(<script\b[^>]*\bsrc=["\']\.\./assets/common/article-related\.js)'
        r'(?:\?[^"\']*)?(["\'])',
        rf'\1?v={DISCOVERY_VERSION}\2',
        source,
        flags=re.IGNORECASE,
    )
    slug = str(article.get("slug") or "")
    page_url = _absolute(base_url, str(article.get("url") or ""))
    media = _media_from_html(source, page_url, slug)
    seo = _seo_markup(article, base_url, media)
    source = _replace_marked(source, SEO_START, SEO_END, seo)
    if SEO_START not in source:
        source = source.replace("</head>", seo + "\n</head>", 1)

    discovery = _discovery_section(article, articles, people, works, topics)
    source = _replace_marked(source, DISCOVERY_START, DISCOVERY_END, discovery)
    if discovery and DISCOVERY_START not in source:
        source = source.replace("</article>", discovery + "\n</article>", 1)
    original = path.read_text(encoding="utf-8")
    if source != original:
        path.write_text(source, encoding="utf-8", newline="")
    return media


ARTICLE_DISCOVERY_CSS = r'''
.article-static-discovery{margin:34px 0 4px;padding-top:24px;border-top:3px solid #171510}
.article-static-discovery h2{margin:0 0 14px;font-size:21px;letter-spacing:0}
.article-entity-links{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.article-entity-links a{display:inline-flex;padding:6px 10px;border:1px solid #b8b4aa;background:#fff;font-size:12px;font-weight:700}
.article-entity-links a:hover{border-color:#c82219;color:#c82219}
.article-related-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.article-related-item{display:grid;grid-template-columns:112px minmax(0,1fr);gap:12px;min-height:82px;border:1px solid #d7d4cb;background:#fff;padding:8px}
.article-related-item img{width:112px;height:76px;object-fit:cover;background:#eee}
.article-related-item strong{display:block;font-size:13px;line-height:1.5}
.article-related-item small{display:block;margin-top:6px;color:#737068;font-size:10px}
.home-discovery-strip{width:min(1120px,calc(100% - 30px));margin:14px auto 0;display:flex;align-items:center;gap:0;border:1px solid #d7d4cb;background:#fff;overflow:auto}
.home-discovery-strip strong,.home-discovery-strip a{padding:9px 14px;white-space:nowrap;font-size:12px;letter-spacing:0}
.home-discovery-strip strong{background:#171510;color:#fff}.home-discovery-strip a{border-right:1px solid #d7d4cb;font-weight:700}.home-discovery-strip a:hover{color:#c82219}
@media(max-width:680px){.article-related-list{grid-template-columns:1fr}.article-related-item{grid-template-columns:92px minmax(0,1fr)}.article-related-item img{width:92px;height:68px}}
'''.strip() + "\n"


HUB_STYLE = r'''
:root{--ink:#171510;--paper:#f2f0ea;--white:#fff;--line:#d7d4cb;--red:#c82219;--muted:#737068;--max:1120px}
*{box-sizing:border-box}body{margin:0;color:#222;background:var(--paper);font-family:-apple-system,BlinkMacSystemFont,"Yu Gothic",Meiryo,sans-serif;line-height:1.65}a{color:inherit;text-decoration:none}img{display:block;max-width:100%}.topbar{padding:5px 12px;background:#111;color:#eee;text-align:center;font-size:10px}.site-header{background:#fff;border-bottom:1px solid var(--ink)}.header-inner{width:min(var(--max),calc(100% - 30px));min-height:104px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:24px}.logo img{width:270px}.header-copy{max-width:430px;color:#555;font-family:"Yu Mincho",serif;font-size:13px;text-align:right}.nav{background:#fff;border-bottom:3px solid var(--ink)}.nav-inner{width:min(var(--max),calc(100% - 30px));margin:auto;display:flex;overflow:auto}.nav a{padding:11px 18px;border-right:1px solid var(--line);font-size:12px;font-weight:700;white-space:nowrap}.page{width:min(var(--max),calc(100% - 30px));margin:28px auto 52px}.page-head{padding-bottom:18px;border-bottom:3px solid var(--ink)}.eyebrow{color:var(--red);font-size:11px;font-weight:800}.page-head h1{margin:4px 0 6px;font-family:"Yu Mincho",serif;font-size:34px;letter-spacing:0}.page-head p{margin:0;color:#5c5952}.entity-index{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}.entity-index a{padding:7px 10px;border:1px solid #bdb9af;background:#fff;font-size:12px;font-weight:700}.entity-section{margin-top:30px;scroll-margin-top:18px}.entity-section h2{margin:0 0 12px;padding:9px 12px;background:#171510;color:#fff;font-size:19px;letter-spacing:0}.article-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.article-card{border:1px solid var(--line);background:#fff}.article-card img{width:100%;aspect-ratio:16/10;object-fit:cover;background:#eee}.article-card div{padding:12px}.article-card strong{display:block;font-size:14px;line-height:1.5}.article-card p{margin:8px 0 0;color:#625f58;font-size:11px}.article-card small{display:block;margin-top:8px;color:var(--muted);font-size:10px}.footer{border-top:3px solid var(--ink);background:#fff}.footer-inner{width:min(var(--max),calc(100% - 30px));margin:auto;padding:20px 0;font-size:11px}.empty{padding:28px;border:1px solid var(--line);background:#fff;color:var(--muted)}@media(max-width:820px){.article-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.header-copy{display:none}}@media(max-width:560px){.header-inner{min-height:82px}.logo img{width:220px}.page-head h1{font-size:27px}.article-grid{grid-template-columns:1fr}.nav a{padding:10px 13px}}
'''.strip()


def _hub_head(base_url: str, path: str, title: str, description: str, depth: int) -> str:
    root = "../" * depth
    canonical = _absolute(base_url, path)
    logo = _absolute(base_url, "assets/common/indanya-logo.png")
    structured = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": canonical,
        "isFamilyFriendly": False,
        "contentRating": "adult",
        "isPartOf": {"@type": "WebSite", "name": "淫談屋", "url": base_url.rstrip("/") + "/"},
    }
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{html.escape(description, quote=True)}"><meta name="robots" content="index,follow,max-image-preview:large"><meta name="rating" content="adult">
<link rel="canonical" href="{html.escape(canonical, quote=True)}"><link rel="alternate" type="application/rss+xml" title="淫談屋 新着記事" href="{html.escape(_absolute(base_url, 'feed.xml'), quote=True)}">
<meta property="og:site_name" content="淫談屋"><meta property="og:type" content="website"><meta property="og:title" content="{html.escape(title, quote=True)}"><meta property="og:description" content="{html.escape(description, quote=True)}"><meta property="og:url" content="{html.escape(canonical, quote=True)}"><meta property="og:image" content="{html.escape(logo, quote=True)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{html.escape(title, quote=True)}"><meta name="twitter:description" content="{html.escape(description, quote=True)}"><meta name="twitter:image" content="{html.escape(logo, quote=True)}">
<link rel="icon" href="{root}assets/common/favicon.ico"><title>{html.escape(title)}｜淫談屋</title><style>{HUB_STYLE}</style><script type="application/ld+json">{_escape_json(structured)}</script><script src="{root}assets/common/age-gate.js?v={DISCOVERY_VERSION}" data-site-root="{root}" defer></script></head>'''


def _hub_shell(base_url: str, path: str, title: str, description: str, body: str, depth: int) -> str:
    root = "../" * depth
    return _hub_head(base_url, path, title, description, depth) + f'''<body data-article-category="特集">
<div class="topbar">当サイトはアフィリエイト広告を利用しています。PRは記事内に表示します</div><header class="site-header"><div class="header-inner"><a class="logo" href="{root}index.html"><img src="{root}assets/common/indanya-logo.png" alt="淫談屋"></a><div class="header-copy">人物・作品・ジャンルから、<br>気になる記事へすぐ移動できます。</div></div></header>
<nav class="nav"><div class="nav-inner"><a href="{root}latest.html">新着</a><a href="{root}people.html">人物</a><a href="{root}works.html">作品</a><a href="{root}topics.html">ジャンル</a><a href="{root}popular.html">人気記事</a></div></nav><main class="page"><header class="page-head"><span class="eyebrow">DISCOVER</span><h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></header>{body}</main><footer class="footer"><div class="footer-inner">© 2026 淫談屋　<a href="{root}editorial.html">編集方針</a>　<a href="{root}partners.html">サイト運営者の方へ</a>　<a href="{root}contact.html">お問い合わせ</a></div></footer></body></html>'''


def _article_cards(articles: list[dict[str, Any]], depth: int) -> str:
    root = "../" * depth
    cards: list[str] = []
    for article in articles[:60]:
        cards.append(
            f'<a class="article-card" href="{root}{html.escape(str(article.get("url") or ""), quote=True)}">'
            f'<img src="{root}{html.escape(str(article.get("thumbnail") or ""), quote=True)}" alt="{html.escape(str(article.get("title") or ""), quote=True)}" loading="lazy">'
            '<div><strong>' + html.escape(str(article.get("title") or "")) + '</strong>'
            f'<p>{html.escape(str(article.get("summary") or "")[:120])}</p>'
            f'<small>{html.escape(str(article.get("display_date") or ""))}・{html.escape(str(article.get("category") or ""))}</small></div></a>'
        )
    return '<div class="article-grid">' + "".join(cards) + "</div>" if cards else '<p class="empty">記事を準備中です。</p>'


def _write_hubs(
    repository: Path,
    base_url: str,
    people: dict[str, list[dict[str, Any]]],
    works: dict[str, list[dict[str, Any]]],
    topics: dict[str, list[dict[str, Any]]],
) -> list[str]:
    generated: list[str] = []
    definitions = (
        ("person", "people", "people.html", "人物から探す", "出演者・配信者・投稿者ごとに淫談屋の記事をまとめています。", people),
        ("work", "works", "works.html", "作品から探す", "作品番号ごとに画像・動画・公式ページを確認できる記事へ移動できます。", works),
        ("topic", "topics", "topics.html", "ジャンルから探す", "衣装・設定・動画形式など、気になるジャンルから記事を探せます。", topics),
    )
    for kind, directory, index_path, title, description, groups in definitions:
        sorted_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
        detail_hrefs: dict[str, str] = {}
        for label, group_articles in sorted_groups:
            if len(group_articles) < 2:
                continue
            slug = _entity_slug(kind, label)
            href = f"{directory}/{slug}.html"
            detail_title = f"{label}の記事一覧"
            detail_description = f"{label}に関する画像・動画・作品情報の記事を新しい順に掲載しています。"
            detail_body = _article_cards(group_articles, 1)
            detail_html = _hub_shell(
                base_url,
                href,
                detail_title,
                detail_description,
                detail_body,
                1,
            )
            destination = repository / href
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(detail_html, encoding="utf-8", newline="")
            detail_hrefs[label] = href
            generated.append(href)
        index_links: list[str] = []
        index_sections: list[str] = []
        for label, group_articles in sorted_groups[:240]:
            slug = _entity_slug(kind, label)
            anchor = f"entity-{slug}"
            href = detail_hrefs.get(label) or str(group_articles[0].get("url") or "")
            index_links.append(
                f'<a href="#{html.escape(anchor, quote=True)}">{html.escape(label)} <small>{len(group_articles)}</small></a>'
            )
            index_sections.append(
                f'<section class="entity-section" id="{html.escape(anchor, quote=True)}"><h2><a href="{html.escape(href, quote=True)}">{html.escape(label)} <small>{len(group_articles)}件</small></a></h2>{_article_cards(group_articles[:6], 0)}</section>'
            )
        body = '<nav class="entity-index">' + "".join(index_links) + '</nav>' + "".join(index_sections)
        index_html = _hub_shell(base_url, index_path, title, description, body, 0)
        (repository / index_path).write_text(index_html, encoding="utf-8", newline="")
        generated.append(index_path)
    return generated


def _remove_stale_generated(repository: Path, current: set[str]) -> None:
    manifest = repository / "data" / "discovery.json"
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    for value in previous.get("generated_files", []) if isinstance(previous, dict) else []:
        if not isinstance(value, str) or value in current:
            continue
        path = (repository / value).resolve()
        try:
            path.relative_to(repository.resolve())
        except ValueError:
            continue
        if path.is_file():
            path.unlink()


def _write_manifest(
    repository: Path,
    generated: list[str],
    people: dict[str, list[dict[str, Any]]],
    works: dict[str, list[dict[str, Any]]],
    topics: dict[str, list[dict[str, Any]]],
) -> None:
    payload = {
        "version": DISCOVERY_VERSION,
        "generated_files": sorted(generated),
        "counts": {"people": len(people), "works": len(works), "topics": len(topics)},
        "people": [{"name": label, "articles": len(rows)} for label, rows in sorted(people.items())],
        "works": [{"code": label, "articles": len(rows)} for label, rows in sorted(works.items())],
        "topics": [{"name": label, "articles": len(rows)} for label, rows in sorted(topics.items())],
    }
    path = repository / "data" / "discovery.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")


def _xml(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _write_sitemaps(
    repository: Path,
    base_url: str,
    articles: list[dict[str, Any]],
    generated: list[str],
    media_by_slug: dict[str, MediaEntry],
) -> None:
    page_entries: list[tuple[str, str]] = [
        (_absolute(base_url, path), "") for path in STATIC_PAGES
    ]
    page_entries.extend(
        (_absolute(base_url, path), "")
        for path in generated
        if path.endswith(".html") and path not in STATIC_PAGES
    )
    page_entries.extend(
        (
            _absolute(base_url, str(article.get("url") or "")),
            _iso_date(article.get("published_at") or article.get("display_date")),
        )
        for article in articles
    )
    seen: set[str] = set()
    page_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for location, modified in page_entries:
        if location in seen:
            continue
        seen.add(location)
        page_lines.extend(["  <url>", f"    <loc>{_xml(location)}</loc>"])
        if modified:
            page_lines.append(f"    <lastmod>{_xml(modified)}</lastmod>")
        page_lines.append("  </url>")
    page_lines.append("</urlset>")
    (repository / "sitemap.xml").write_text("\n".join(page_lines) + "\n", encoding="utf-8", newline="")

    image_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">',
    ]
    for article in articles:
        media = media_by_slug.get(str(article.get("slug") or ""), MediaEntry())
        if not media.images:
            continue
        image_lines.extend(["  <url>", f"    <loc>{_xml(_absolute(base_url, str(article.get('url') or '')))}</loc>"])
        for image_url, alt in media.images[:1000]:
            image_lines.extend([
                "    <image:image>",
                f"      <image:loc>{_xml(image_url)}</image:loc>",
                "    </image:image>",
            ])
        image_lines.append("  </url>")
    image_lines.append("</urlset>")
    (repository / "sitemap-images.xml").write_text("\n".join(image_lines) + "\n", encoding="utf-8", newline="")

    video_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">',
    ]
    for article in articles:
        media = media_by_slug.get(str(article.get("slug") or ""), MediaEntry())
        if not media.videos:
            continue
        article_url = _absolute(base_url, str(article.get("url") or ""))
        thumbnail = _absolute(base_url, str(article.get("thumbnail") or ""))
        summary = str(article.get("summary") or article.get("title") or "")[:240]
        video_lines.extend(["  <url>", f"    <loc>{_xml(article_url)}</loc>"])
        for index, video in enumerate(media.videos, start=1):
            video_lines.extend([
                "    <video:video>",
                f"      <video:thumbnail_loc>{_xml(video.get('poster') or thumbnail)}</video:thumbnail_loc>",
                f"      <video:title>{_xml(str(article.get('title') or '') + ' 動画' + str(index))}</video:title>",
                f"      <video:description>{_xml(summary)}</video:description>",
            ])
            if video.get("content"):
                video_lines.append(f"      <video:content_loc>{_xml(video['content'])}</video:content_loc>")
            else:
                video_lines.append(f"      <video:player_loc>{_xml(video.get('player'))}</video:player_loc>")
            published = _iso_datetime(
                article.get("published_at") or article.get("display_date")
            )
            if published:
                video_lines.append(f"      <video:publication_date>{_xml(published)}</video:publication_date>")
            video_lines.extend(["      <video:family_friendly>no</video:family_friendly>", "    </video:video>"])
        video_lines.append("  </url>")
    video_lines.append("</urlset>")
    (repository / "sitemap-videos.xml").write_text("\n".join(video_lines) + "\n", encoding="utf-8", newline="")

    (repository / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {_absolute(base_url, 'sitemap.xml')}\n"
        f"Sitemap: {_absolute(base_url, 'sitemap-images.xml')}\n"
        f"Sitemap: {_absolute(base_url, 'sitemap-videos.xml')}\n",
        encoding="utf-8",
        newline="",
    )


def _write_feed(repository: Path, base_url: str, articles: list[dict[str, Any]]) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        "  <channel>",
        "    <title>淫談屋 新着記事</title>",
        f"    <link>{_xml(base_url.rstrip('/') + '/')}</link>",
        "    <description>淫談屋の新着画像・動画記事</description>",
        "    <language>ja</language>",
    ]
    for article in articles[:50]:
        article_url = _absolute(base_url, str(article.get("url") or ""))
        published = _parse_datetime(
            article.get("published_at") or article.get("display_date")
        )
        pub_date = format_datetime(published) if published else ""
        lines.extend([
            "    <item>",
            f"      <title>{_xml(article.get('title'))}</title>",
            f"      <link>{_xml(article_url)}</link>",
            f"      <guid isPermaLink=\"true\">{_xml(article_url)}</guid>",
            f"      <description>{_xml(article.get('summary'))}</description>",
            f"      <media:content url=\"{_xml(_absolute(base_url, str(article.get('thumbnail') or '')))}\" medium=\"image\" />",
        ])
        if pub_date:
            lines.append(f"      <pubDate>{_xml(pub_date)}</pubDate>")
        lines.append("    </item>")
    lines.extend(["  </channel>", "</rss>"])
    (repository / "feed.xml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


def _augment_home(repository: Path, base_url: str) -> None:
    path = repository / "index.html"
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    markup = (
        f"{HOME_START}\n"
        f'<link rel="alternate" type="application/rss+xml" title="淫談屋 新着記事" href="{html.escape(_absolute(base_url, "feed.xml"), quote=True)}">\n'
        f'<link rel="stylesheet" href="assets/common/article-discovery.css?v={DISCOVERY_VERSION}">\n'
        f"{HOME_END}"
    )
    source = _replace_marked(source, HOME_START, HOME_END, markup)
    if HOME_START not in source:
        source = source.replace("</head>", markup + "\n</head>", 1)
    body_markup = (
        f'{HOME_BODY_START}\n<nav class="home-discovery-strip" aria-label="人物・作品・ジャンルから探す">'
        '<strong>記事を探す</strong><a href="people.html">人物</a>'
        '<a href="works.html">作品</a><a href="topics.html">ジャンル</a>'
        '<a href="feed.xml">RSS</a></nav>\n'
        f'{HOME_BODY_END}'
    )
    source = _replace_marked(source, HOME_BODY_START, HOME_BODY_END, body_markup)
    if HOME_BODY_START not in source:
        source = source.replace("</nav>", "</nav>\n" + body_markup, 1)
    original = path.read_text(encoding="utf-8")
    if source != original:
        path.write_text(source, encoding="utf-8", newline="")


def refresh_site_discovery(
    repository: Path,
    public_url: str,
    articles: list[dict[str, Any]],
) -> dict[str, int]:
    repository = repository.resolve()
    published = _enrich_articles(repository, _published_articles(articles))
    people, works, topics = _group_articles(published)

    generated = _write_hubs(repository, public_url, people, works, topics)
    current_generated = set(generated)
    _remove_stale_generated(repository, current_generated)

    css_path = repository / "assets" / "common" / "article-discovery.css"
    css_path.parent.mkdir(parents=True, exist_ok=True)
    css_path.write_text(ARTICLE_DISCOVERY_CSS, encoding="utf-8", newline="")

    media_by_slug: dict[str, MediaEntry] = {}
    for article in published:
        slug = str(article.get("slug") or "")
        media_by_slug[slug] = _augment_article(
            repository, public_url, article, published, people, works, topics
        )

    _write_sitemaps(repository, public_url, published, generated, media_by_slug)
    _write_feed(repository, public_url, published)
    _write_manifest(repository, generated, people, works, topics)
    _augment_home(repository, public_url)
    return {
        "articles": len(published),
        "people": len(people),
        "works": len(works),
        "topics": len(topics),
        "images": sum(len(media.images) for media in media_by_slug.values()),
        "videos": sum(len(media.videos) for media in media_by_slug.values()),
    }
