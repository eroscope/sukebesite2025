from __future__ import annotations

import json
import hashlib
import time
import urllib.error
import urllib.request
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse

from article_studio import JST


SITEMAP_FILES = (
    "sitemap.xml",
    "sitemap-images.xml",
    "sitemap-videos.xml",
)
MAX_SITEMAP_BYTES = 50 * 1024 * 1024
MAX_SITEMAP_URLS = 50_000
HEALTH_VERSION = 1


def _health_path(site_root: Path) -> Path:
    return Path(site_root) / ".article-studio" / "sitemap-health.json"


def _timestamp() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _url_digest(locations: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(locations)).encode("utf-8")).hexdigest()


def _read_articles(repository: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(
            (Path(repository) / "data" / "articles.json").read_text(
                encoding="utf-8-sig"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("公開記事一覧 data/articles.json を読み込めません") from exc
    if isinstance(raw, dict):
        raw = raw.get("articles", [])
    if not isinstance(raw, list):
        raise RuntimeError("公開記事一覧 data/articles.json の形式が正しくありません")
    return [
        dict(item)
        for item in raw
        if isinstance(item, dict) and str(item.get("status") or "published") == "published"
    ]


def _parse_sitemap_bytes(payload: bytes, name: str) -> tuple[list[str], str]:
    if len(payload) > MAX_SITEMAP_BYTES:
        raise RuntimeError(f"{name} がGoogleの50MB上限を超えています")
    if name.endswith(".txt"):
        try:
            locations = payload.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"{name} is not UTF-8") from exc
        if len(locations) > MAX_SITEMAP_URLS or len(locations) != len(set(locations)):
            raise RuntimeError(f"{name} has duplicate URLs or exceeds the URL limit")
        for location in locations:
            parsed = urlparse(location)
            if (location != location.strip() or not location or parsed.scheme != "https"
                    or not parsed.netloc or parsed.username or parsed.password or parsed.fragment):
                raise RuntimeError(f"{name} contains an invalid canonical URL")
        return locations, "text"
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError(f"{name} のXMLが壊れています: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "urlset":
        raise RuntimeError(f"{name} のルート要素がurlsetではありません")
    locations: list[str] = []
    for url_node in root:
        if url_node.tag.rsplit("}", 1)[-1] != "url":
            continue
        location = next(
            (
                str(child.text or "").strip()
                for child in url_node
                if child.tag.rsplit("}", 1)[-1] == "loc"
            ),
            "",
        )
        if not location:
            raise RuntimeError(f"{name} にURLのないurl要素があります")
        parsed = urlparse(location)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError(f"{name} に絶対HTTPS URLではない項目があります: {location}")
        locations.append(location)
    if len(locations) > MAX_SITEMAP_URLS:
        raise RuntimeError(f"{name} がGoogleの50,000 URL上限を超えています")
    duplicate_count = len(locations) - len(set(locations))
    if duplicate_count:
        raise RuntimeError(f"{name} に重複URLが{duplicate_count}件あります")
    return locations, root.tag


def _parse_sitemap_file(path: Path) -> tuple[list[str], str, int]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"{path.name} が見つかりません") from exc
    locations, root_tag = _parse_sitemap_bytes(payload, path.name)
    return locations, root_tag, len(payload)


def validate_local_sitemaps(repository: Path, public_url: str) -> dict[str, Any]:
    repository = Path(repository)
    checked_at = _timestamp()
    errors: list[str] = []
    sitemap_rows: dict[str, dict[str, Any]] = {}
    locations_by_name: dict[str, list[str]] = {}
    names = (*SITEMAP_FILES, "sitemap-pages.txt") if (repository / "sitemap-pages.txt").exists() else SITEMAP_FILES
    for name in names:
        try:
            locations, _root_tag, size = _parse_sitemap_file(repository / name)
            locations_by_name[name] = locations
            sitemap_rows[name] = {
                "url_count": len(locations),
                "bytes": size,
                "url_digest": _url_digest(locations),
                "status": "healthy",
            }
        except RuntimeError as exc:
            errors.append(str(exc))
            sitemap_rows[name] = {
                "url_count": 0,
                "bytes": 0,
                "status": "error",
                "error": str(exc),
            }

    try:
        articles = _read_articles(repository)
    except RuntimeError as exc:
        articles = []
        errors.append(str(exc))
    article_urls = [
        urljoin(public_url.rstrip("/") + "/", str(item.get("url") or ""))
        for item in articles
        if str(item.get("url") or "").strip()
    ]
    main_locations = set(locations_by_name.get("sitemap.xml", []))
    if "sitemap-pages.txt" in locations_by_name and set(locations_by_name["sitemap-pages.txt"]) != main_locations:
        errors.append("sitemap-pages.txt and sitemap.xml contain different URLs")
    missing = [value for value in article_urls if value not in main_locations]
    if missing:
        errors.append(
            f"公開記事{len(article_urls)}件のうち{len(missing)}件がsitemap.xmlにありません"
        )

    robots_path = repository / "robots.txt"
    try:
        robots = robots_path.read_text(encoding="utf-8-sig")
    except OSError:
        robots = ""
        errors.append("robots.txt が見つかりません")
    for name in SITEMAP_FILES:
        target = urljoin(public_url.rstrip("/") + "/", name)
        if target not in robots:
            errors.append(f"robots.txt に {name} の案内がありません")

    report = {
        "version": HEALTH_VERSION,
        "scope": "local",
        "checked_at": checked_at,
        "status": "healthy" if not errors else "error",
        "public_url": public_url.rstrip("/") + "/",
        "published_articles": len(article_urls),
        "sample_article_url": article_urls[-1] if article_urls else "",
        "missing_article_count": len(missing),
        "missing_article_samples": missing[:10],
        "sitemaps": sitemap_rows,
        "errors": errors,
    }
    if errors:
        raise RuntimeError("サイトマップ検査に失敗しました: " + " / ".join(errors))
    return report


def _request_bytes(url: str, timeout: float) -> tuple[int, bytes]:
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{separator}_indanya_health={int(time.time())}",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; IndanyaSitemapHealth/1.0)",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200) or 200)
        payload = response.read(MAX_SITEMAP_BYTES + 1)
    return status, payload


def check_public_sitemaps(
    public_url: str,
    expected: dict[str, Any] | None = None,
    *,
    timeout: float = 12.0,
) -> dict[str, Any]:
    checked_at = _timestamp()
    base = public_url.rstrip("/") + "/"
    errors: list[str] = []
    sitemap_rows: dict[str, dict[str, Any]] = {}
    locations_by_name: dict[str, list[str]] = {}
    expected_sitemaps = (expected or {}).get("sitemaps") or {}
    names = (*SITEMAP_FILES, "sitemap-pages.txt") if "sitemap-pages.txt" in expected_sitemaps else SITEMAP_FILES
    for name in names:
        url = urljoin(base, name)
        try:
            status, payload = _request_bytes(url, timeout)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            locations, _root_tag = _parse_sitemap_bytes(payload, name)
            locations_by_name[name] = locations
            expected_row = expected_sitemaps.get(name)
            expected_count = int((expected_row or {}).get("url_count") or 0)
            if isinstance(expected_row, dict) and len(locations) != expected_count:
                raise RuntimeError(
                    f"公開先は{len(locations)}件、今回生成は{expected_count}件で未反映です"
                )
            digest = _url_digest(locations)
            if isinstance(expected_row, dict) and expected_row.get("url_digest") and expected_row["url_digest"] != digest:
                raise RuntimeError("URL件数は同じですが、公開先の記事URL一覧が今回生成した内容と異なります")
            sitemap_rows[name] = {
                "url": url,
                "http_status": status,
                "url_count": len(locations),
                "bytes": len(payload),
                "url_digest": digest,
                "status": "healthy",
            }
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            message = f"{name}: {exc}"
            errors.append(message)
            sitemap_rows[name] = {
                "url": url,
                "http_status": 0,
                "url_count": 0,
                "bytes": 0,
                "status": "pending",
                "error": str(exc),
            }

    if "sitemap-pages.txt" in locations_by_name and set(locations_by_name["sitemap-pages.txt"]) != set(locations_by_name.get("sitemap.xml", [])):
        errors.append("公開中のXML版とURL一覧版のサイトマップが一致しません")

    # Crawlers use the origin root, not a project/subdirectory robots.txt.
    robots_url = urljoin(base, "/robots.txt")
    warnings: list[str] = []
    try:
        robots_status, robots_payload = _request_bytes(robots_url, timeout)
        robots = robots_payload.decode("utf-8-sig", errors="replace")
        if robots_status != 200 and not (400 <= robots_status < 500 and robots_status != 429):
            raise RuntimeError(f"HTTP {robots_status}")
        parser = RobotFileParser(robots_url)
        parser.parse(robots.splitlines() if robots_status == 200 else [])
        allowed = all(parser.can_fetch("Googlebot", url) for url in
                      [base, *locations_by_name.get("sitemap.xml", [])])
        if not allowed:
            raise RuntimeError("Googlebotによるサイトまたは記事の取得が禁止されています")
        discovered = set(parser.site_maps() or [])
        missing_robots = [name for name in SITEMAP_FILES if urljoin(base, name) not in discovered]
        robots_row = {"url": robots_url, "http_status": robots_status, "status": "healthy" if robots_status == 200 else "missing_discovery",
                      "allows_crawl": True, "sitemap_discovery": not missing_robots}
        if missing_robots:
            warnings.append("ドメイン直下のrobots.txtにサイトマップ案内がありません。Search Consoleへの直接送信が必要です")
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code != 429:
            robots_row = {"url": robots_url, "http_status": exc.code, "status": "missing_discovery",
                          "allows_crawl": True, "sitemap_discovery": False}
            warnings.append("ドメイン直下のrobots.txtがありません。クロール禁止ではありませんが、自動発見の案内がありません")
        else:
            errors.append(f"robots.txt: HTTP {exc.code}")
            robots_row = {"url": robots_url, "http_status": exc.code, "status": "pending"}
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        errors.append(f"robots.txt: {exc}")
        robots_row = {
            "url": robots_url,
            "http_status": 0,
            "status": "pending",
            "error": str(exc),
        }

    sample_article_url = str((expected or {}).get("sample_article_url") or "")
    if sample_article_url:
        if sample_article_url not in set(locations_by_name.get("sitemap.xml", [])):
            errors.append("今回公開した最新記事が公開中のsitemap.xmlにまだありません")
        try:
            article_status, _payload = _request_bytes(sample_article_url, timeout)
            if article_status != 200:
                raise RuntimeError(f"HTTP {article_status}")
            article_row = {
                "url": sample_article_url,
                "http_status": article_status,
                "status": "healthy",
            }
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            errors.append(f"最新記事: {exc}")
            article_row = {
                "url": sample_article_url,
                "http_status": 0,
                "status": "pending",
                "error": str(exc),
            }
    else:
        article_row = {"url": "", "http_status": 0, "status": "not_checked"}

    return {
        "version": HEALTH_VERSION,
        "scope": "public",
        "checked_at": checked_at,
        "status": "healthy" if not errors else "pending",
        "public_url": base,
        "sitemaps": sitemap_rows,
        "robots": robots_row,
        "sample_article": article_row,
        "errors": errors,
        "warnings": warnings,
    }


def wait_for_public_sitemaps(
    public_url: str,
    expected: dict[str, Any],
    *,
    max_wait_seconds: float = 75.0,
    interval_seconds: float = 6.0,
    progress: Callable[[int, str], None] = lambda _value, _message: None,
) -> dict[str, Any]:
    hostname = (urlparse(public_url).hostname or "").casefold()
    if hostname in {"example.com", "www.example.com"} or hostname.endswith(".example"):
        return {
            "version": HEALTH_VERSION,
            "scope": "public",
            "checked_at": _timestamp(),
            "status": "not_checked",
            "public_url": public_url,
            "sitemaps": {},
            "errors": ["テスト用URLのため公開先検査を省略しました"],
        }
    started = time.monotonic()
    report: dict[str, Any] = {}
    while True:
        report = check_public_sitemaps(public_url, expected)
        if report.get("status") == "healthy":
            return report
        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            return report
        progress(
            min(99, 92 + int(elapsed / max(1.0, max_wait_seconds) * 7)),
            "GitHub Pagesへサイトマップが反映されるのを確認しています",
        )
        time.sleep(min(interval_seconds, max_wait_seconds - elapsed))


def load_sitemap_health(site_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_health_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def save_sitemap_health(site_root: Path, report: dict[str, Any]) -> None:
    path = _health_path(site_root)
    observed = (load_sitemap_health(site_root).get("search_console") or {})
    if observed.get("observed_at") and not (report.get("search_console") or {}).get("observed_at"):
        report = {**report, "search_console": observed}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def record_search_console_observation(site_root: Path, observation: dict[str, Any]) -> None:
    if not observation.get("observed_at") or observation.get("source") not in {"ui", "api"}:
        raise ValueError("Search Console observation must include its time and source")
    report = load_sitemap_health(site_root)
    report["search_console"] = dict(observation)
    save_sitemap_health(site_root, report)


def search_console_observation_summary(observation: dict[str, Any]) -> str:
    labels = {
        "fetch_failed": "Googleサイトマップ一覧: 取得失敗表示",
        "unverified": "Google側の取得・登録は未確認",
        "waiting_for_public": "Google側の取得・登録は未確認",
    }
    detail = labels.get(str(observation.get("status") or ""), "Google側は確認記録を参照")
    observed = str(observation.get("observed_at") or "").replace("T", " ")[:19]
    if observed:
        detail += f"（画面確認 {observed}）"
    live = observation.get("live_test") or {}
    if live.get("page_fetch") == "successful":
        tested = str(live.get("tested_at") or "").replace("T", " ")[:19]
        detail += f" / Google実取得テスト: 成功（{tested or '日時未記録'}、登録成功とは別）"
    indexing = observation.get("page_indexing") or {}
    if "indexed_pages" in indexing:
        report_date = str(indexing.get("report_date") or "")[:10]
        date_label = f"{report_date}時点" if report_date else "集計日不明"
        detail += f" / 登録数の記録: {indexing['indexed_pages']}件（{date_label}、現在値は未確認）"
    return detail


def combined_sitemap_health(
    local: dict[str, Any] | None,
    public: dict[str, Any] | None,
) -> dict[str, Any]:
    local = dict(local or {})
    public = dict(public or {})
    if local.get("status") == "error":
        status = "error"
    elif public.get("status") == "healthy":
        status = "healthy"
    elif public.get("status") == "not_checked":
        status = "local_only"
    else:
        status = "pending"
    return {
        "version": HEALTH_VERSION,
        "checked_at": str(public.get("checked_at") or local.get("checked_at") or _timestamp()),
        "status": status,
        "local": local,
        "public": public,
        "search_console": {
            "status": "unverified" if status == "healthy" else "waiting_for_public",
        },
    }


def run_public_sitemap_health_check(site_root: Path, public_url: str) -> dict[str, Any]:
    previous = load_sitemap_health(site_root)
    expected = previous.get("local") or None
    public = check_public_sitemaps(public_url, expected)
    report = combined_sitemap_health(expected, public)
    previous_search = previous.get("search_console") or {}
    report["search_console"] = {
        **report["search_console"],
        **(dict(previous_search) if isinstance(previous_search, dict) else {}),
    }
    save_sitemap_health(site_root, report)
    return report


def search_console_sitemaps_url(public_url: str) -> str:
    return (
        "https://search.google.com/search-console/sitemaps?resource_id="
        + quote(public_url.rstrip("/") + "/", safe="")
    )
