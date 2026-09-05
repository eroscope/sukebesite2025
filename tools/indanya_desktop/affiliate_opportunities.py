from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import urllib.request


MGS_PROGRAM_ID = "mgs"
MGS_PROGRAM_NAME = "MGS動画"
MGS_NETWORK_NAME = "BannerBridge"
MGS_REGISTRATION_URL = "https://www.bannerbridge.net/info/topic/new/9285/"
MGS_HOME_URL = "https://www.mgstage.com/"
_MGS_PRODUCT_PATH = re.compile(
    r"/product/product_detail/([^/?#]+)", re.IGNORECASE
)
_MGS_PRODUCT_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{3,39}$", re.IGNORECASE)
_MGS_PRODUCT_PAGE_BYTES = 2 * 1024 * 1024
_MGS_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
)


class _MgsMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.casefold() != "meta":
            return
        values = {
            str(key or "").casefold(): str(value or "")
            for key, value in attrs
        }
        name = values.get("property") or values.get("name") or ""
        if name in {"og:title", "og:image"} and values.get("content"):
            self.metadata[name] = values["content"]


def _mgs_widget_product_title(value: str) -> str:
    try:
        parsed = urlparse(unescape(str(value or "")))
    except ValueError:
        return ""
    if "mgs_widget_affiliate" not in parsed.path.casefold():
        return ""
    title = parse_qs(parsed.query).get("s", [""])[0]
    return " ".join(unquote(title).split())[:500]


def _compact_match_text(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _mgs_title_matches_source(source: dict[str, Any], product_title: str) -> bool:
    product = _compact_match_text(product_title)
    if len(product) < 12:
        return False
    source_titles = [
        _compact_match_text(source.get(key))
        for key in ("title", "description")
        if source.get(key)
    ]
    for title in source_titles:
        if len(title) < 12:
            continue
        if title in product or product in title:
            return True
        matcher = SequenceMatcher(None, title, product, autojunk=False)
        if matcher.find_longest_match().size >= 14 and matcher.ratio() >= 0.34:
            return True
    return False


def mgs_product_page_metadata(
    product_url: Any,
    opener: Any | None = None,
) -> dict[str, str]:
    """Read an exact MGS page after age confirmation without affiliate data."""
    product_code = mgs_product_code_from_url(product_url)
    if not product_code:
        return {}
    canonical_url = (
        f"https://www.mgstage.com/product/product_detail/{product_code}/"
    )
    request = urllib.request.Request(
        canonical_url + "?agef=1",
        headers={
            "User-Agent": _MGS_BROWSER_USER_AGENT,
            "Cookie": "adc=1",
        },
    )
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(request, timeout=25) as response:
            data = response.read(_MGS_PRODUCT_PAGE_BYTES + 1)
            if not data or len(data) > _MGS_PRODUCT_PAGE_BYTES:
                return {}
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception:
        # MGS currently rejects Python's TLS/HTTP fingerprint with 406 while
        # serving the same public page to browser-compatible clients. Windows
        # includes curl, so use it only as a bounded read-only fallback.
        if opener is not None:
            return {}
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            return {}
        try:
            result = subprocess.run(
                [
                    curl,
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "25",
                    "--max-filesize",
                    str(_MGS_PRODUCT_PAGE_BYTES),
                    "--user-agent",
                    _MGS_BROWSER_USER_AGENT,
                    "--header",
                    "Cookie: adc=1",
                    canonical_url + "?agef=1",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        data = result.stdout
        if not data or len(data) > _MGS_PRODUCT_PAGE_BYTES:
            return {}
        charset = "utf-8"
    parser = _MgsMetaParser()
    try:
        parser.feed(data.decode(charset, errors="replace"))
    except Exception:
        return {}
    raw_title = " ".join(parser.metadata.get("og:title", "").split())
    title_match = re.match(r"^「(.+?)」：MGS動画", raw_title)
    title = title_match.group(1) if title_match else raw_title.partition("：MGS動画")[0]
    image_url = unescape(parser.metadata.get("og:image", "")).strip()
    image_host = (urlparse(image_url).hostname or "").casefold() if image_url else ""
    if image_host != "image.mgstage.com":
        image_url = ""
    return {
        "product_code": product_code,
        "product_url": canonical_url,
        "product_title": title[:500],
        "thumbnail_url": image_url,
    }


def _safe_mgs_product_code(value: Any) -> str:
    candidate = unquote(str(value or "")).strip().strip("/")
    if not _MGS_PRODUCT_CODE.fullmatch(candidate):
        return ""
    return candidate.upper()


def _resource_urls(source: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for item in source.get("affiliate_resources") or []:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            kind = str(item.get("kind") or item.get("tag") or "resource")
            metadata = item
        else:
            url = str(item or "").strip()
            kind = "resource"
            metadata = {}
        if url:
            yield url, kind, metadata
    for item in source.get("links") or []:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url:
                yield url, "link", item


def _mgs_code_from_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlparse(value)
    except ValueError:
        return "", ""
    hostname = (parsed.hostname or "").lower()
    if hostname != "mgstage.com" and not hostname.endswith(".mgstage.com"):
        return "", ""
    path_match = _MGS_PRODUCT_PATH.search(parsed.path)
    if path_match:
        return _safe_mgs_product_code(path_match.group(1)), "official_product_url"
    query = parse_qs(parsed.query)
    if "mgs_widget_affiliate" in parsed.path.lower():
        for key in ("p", "product_id", "product"):
            for candidate in query.get(key, []):
                code = _safe_mgs_product_code(candidate)
                if code:
                    return code, "exact_product_widget"
    return "", "mgs_resource"


def mgs_product_code_from_url(value: Any) -> str:
    """Return a normalized MGS product code without exposing affiliate data."""
    product_code, _evidence_type = _mgs_code_from_url(str(value or ""))
    return product_code


def _article_match_for_mgs_product(
    source: dict[str, Any],
    product_code: str,
    resource: dict[str, Any],
) -> bool:
    if not product_code:
        return False
    code_key = re.sub(r"[^A-Z0-9]", "", product_code.upper())
    for key in ("requested_url", "url", "canonical_url"):
        source_code, _ = _mgs_code_from_url(str(source.get(key) or ""))
        if source_code == product_code:
            return True
    source_text = " ".join(
        str(source.get(key) or "")
        for key in (
            "title",
            "description",
            "body_text",
            "ai_fanza_product_code",
        )
    )
    if code_key and code_key in re.sub(r"[^A-Z0-9]", "", source_text.upper()):
        return True

    widget_title = _mgs_widget_product_title(resource.get("resource_url", ""))
    if widget_title and _mgs_title_matches_source(source, widget_title):
        return True

    subject_names: list[str] = []
    for key in ("ai_main_subject", "main_subject"):
        subject = source.get(key)
        if isinstance(subject, dict):
            subject_names.append(str(subject.get("name") or ""))
    for key in ("ai_fanza_people", "fanza_people"):
        for person in source.get(key) or []:
            if isinstance(person, dict):
                subject_names.append(str(person.get("name") or ""))
    link_text = " ".join(
        str(resource.get(key) or "")
        for key in ("text", "browser_context", "title", "alt")
    )
    compact_link_text = re.sub(r"[\W_]+", "", link_text, flags=re.UNICODE).casefold()
    return any(
        len(compact_name) >= 2 and compact_name in compact_link_text
        for name in subject_names
        if (compact_name := re.sub(r"[\W_]+", "", name, flags=re.UNICODE).casefold())
    )


def _mgs_opportunity(
    product_code: str,
    evidence_type: str,
    *,
    article_match: bool | None = None,
    product_title: str = "",
    thumbnail_url: str = "",
) -> dict[str, Any]:
    exact = bool(product_code)
    # Legacy drafts did not store article_match. A sidebar widget is not proof
    # that the article subject appears in that product, so missing evidence is
    # deliberately treated as unconfirmed.
    matched = bool(article_match) if article_match is not None else False
    result = {
        "program_id": MGS_PROGRAM_ID,
        "program_name": MGS_PROGRAM_NAME,
        "network_name": MGS_NETWORK_NAME,
        "status": "registration_recommended",
        "registration_url": MGS_REGISTRATION_URL,
        "program_url": MGS_HOME_URL,
        "product_code": product_code,
        "product_url": (
            f"https://www.mgstage.com/product/product_detail/{product_code}/"
            if exact else ""
        ),
        "reason": (
            "元ページ内のMGS商品導線を確認（記事本体との一致は未確認）"
            if exact and not matched
            else "記事本体と一致するMGS広告ウィジェットから作品番号を確認"
            if evidence_type == "exact_product_widget"
            else "記事本体と一致するMGS公式商品URLから作品番号を確認"
            if evidence_type == "official_product_url"
            else "記事内にMGS作品への導線を確認"
        ),
        "evidence_type": evidence_type,
        "confidence": 100 if exact else 70,
        "article_match": matched,
    }
    if product_title:
        result["product_title"] = " ".join(product_title.split())[:500]
    if thumbnail_url:
        result["thumbnail_url"] = thumbnail_url
    return result


def normalize_affiliate_opportunities(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        program_id = str(raw.get("program_id") or "").strip().lower()
        product_code = _safe_mgs_product_code(raw.get("product_code"))
        if program_id != MGS_PROGRAM_ID:
            continue
        key = (program_id, product_code)
        if key in seen:
            continue
        seen.add(key)
        evidence_type = str(raw.get("evidence_type") or "mgs_resource")
        raw_match = raw.get("article_match")
        normalized.append(_mgs_opportunity(
            product_code,
            evidence_type,
            article_match=bool(raw_match) if raw_match is not None else False,
            product_title=str(raw.get("product_title") or ""),
            thumbnail_url=str(raw.get("thumbnail_url") or ""),
        ))
    return normalized


def detect_affiliate_opportunities(source: dict[str, Any]) -> list[dict[str, Any]]:
    opportunities = normalize_affiliate_opportunities(
        source.get("affiliate_opportunities")
    )
    existing_by_key = {
        (item["program_id"], item.get("product_code") or ""): item
        for item in opportunities
    }
    saw_mgs_resource = False
    for resource_url, _kind, metadata in _resource_urls(source):
        product_code, evidence_type = _mgs_code_from_url(resource_url)
        if not evidence_type:
            continue
        saw_mgs_resource = True
        key = (MGS_PROGRAM_ID, product_code)
        resource_metadata = {**metadata, "resource_url": resource_url}
        product_title = _mgs_widget_product_title(resource_url)
        article_match = _article_match_for_mgs_product(
            source, product_code, resource_metadata
        )
        if key in existing_by_key:
            if (
                article_match and not existing_by_key[key].get("article_match")
            ) or (
                product_title and not existing_by_key[key].get("product_title")
            ):
                existing_by_key[key].update(_mgs_opportunity(
                    product_code,
                    evidence_type,
                    article_match=(
                        article_match
                        or bool(existing_by_key[key].get("article_match"))
                    ),
                    product_title=(
                        product_title
                        or str(existing_by_key[key].get("product_title") or "")
                    ),
                ))
            continue
        opportunity = _mgs_opportunity(
            product_code,
            evidence_type,
            article_match=article_match,
            product_title=product_title,
        )
        existing_by_key[key] = opportunity
        opportunities.append(opportunity)

    if not saw_mgs_resource:
        source_text = " ".join(
            str(source.get(key) or "")
            for key in ("title", "description", "body_text")
        )
        if re.search(r"(?:MGS|MGSTAGE|エムジーエス)", source_text, re.IGNORECASE):
            key = (MGS_PROGRAM_ID, "")
            if key not in existing_by_key:
                opportunities.append(_mgs_opportunity("", "mgs_text_reference"))

    if any(
        item.get("program_id") == MGS_PROGRAM_ID and item.get("product_code")
        for item in opportunities
    ):
        opportunities = [
            item for item in opportunities
            if item.get("program_id") != MGS_PROGRAM_ID or item.get("product_code")
        ]

    opportunities.sort(
        key=lambda item: (
            -int(item.get("confidence") or 0),
            str(item.get("program_id") or ""),
            str(item.get("product_code") or ""),
        )
    )
    return opportunities


def registration_recommendations(
    drafts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        slug = str(draft.get("slug") or "")
        title = str(draft.get("title") or slug)
        for item in normalize_affiliate_opportunities(
            draft.get("affiliate_opportunities")
        ):
            if item.get("status") != "registration_recommended":
                continue
            program_id = str(item["program_id"])
            recommendation = grouped.setdefault(
                program_id,
                {
                    **item,
                    "article_count": 0,
                    "exact_product_count": 0,
                    "slugs": [],
                    "titles": [],
                    "products": [],
                },
            )
            if slug and slug not in recommendation["slugs"]:
                recommendation["slugs"].append(slug)
                recommendation["titles"].append(title)
                recommendation["article_count"] += 1
            product_code = str(item.get("product_code") or "")
            if product_code and product_code not in recommendation["products"]:
                recommendation["products"].append(product_code)
                recommendation["exact_product_count"] += 1
                if not recommendation.get("product_url"):
                    recommendation["product_url"] = item.get("product_url") or ""
    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["article_count"]), str(item["program_name"])),
    )
