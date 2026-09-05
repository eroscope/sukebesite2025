from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .owner_collector import (
    COLLECTOR_BASE_URL,
    load_owner_events,
    normalize_public_url,
    site_key_for_public_url,
)


GA4_READ_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ANALYTICS_VERSION = 8
AUDIENCES = ("external", "all")

ARTICLE_EVENTS = {
    "external": ("article_view",),
    "all": ("article_view", "owner_article_view"),
}
ARTICLE_VISIT_EVENTS = {
    "external": ("article_visit",),
    "all": ("article_visit", "owner_article_visit"),
}
ARTICLE_PR_EVENTS = {
    "external": ("article_pr_impression", "article_pr_click"),
    "all": (
        "article_pr_impression",
        "article_pr_click",
        "owner_article_pr_impression",
        "owner_article_pr_click",
    ),
}
ALL_ARTICLE_EVENTS = tuple(dict.fromkeys(
    ARTICLE_EVENTS["all"] + ARTICLE_VISIT_EVENTS["all"] + ARTICLE_PR_EVENTS["all"]
))
CANONICAL_EVENTS = {
    "article_view": "article_view",
    "owner_article_view": "article_view",
    "article_visit": "article_visit",
    "owner_article_visit": "article_visit",
    "article_pr_impression": "pr_impression",
    "owner_article_pr_impression": "pr_impression",
    "article_pr_click": "pr_click",
    "owner_article_pr_click": "pr_click",
}


def _ga4_client(site_root: Path):
    """Create an authenticated Data API client for the configured property."""
    property_id = load_ga4_property_id(site_root)
    credentials = ga4_credentials_path(site_root)
    if not property_id:
        raise RuntimeError("GA4プロパティIDが未設定です")
    if not credentials.is_file():
        raise RuntimeError("GA4読み取り用JSONが未設定です")
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("GA4読み取りライブラリがインストールされていません") from exc
    creds = service_account.Credentials.from_service_account_file(
        str(credentials), scopes=[GA4_READ_SCOPE]
    )
    return BetaAnalyticsDataClient(credentials=creds), f"properties/{property_id}"


def _report_rows(response: object, dimensions: list[str], metrics: list[str]) -> list[dict]:
    result: list[dict] = []
    for row in getattr(response, "rows", []) or []:
        values = [value.value for value in row.dimension_values]
        values.extend(value.value for value in row.metric_values)
        result.append(dict(zip(dimensions + metrics, values)))
    return result


def _first_row(rows: list[dict], metrics: list[str]) -> dict[str, int]:
    source = rows[0] if rows else {}
    return {key: int(source.get(key) or 0) for key in metrics}


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _percentage(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 2) if denominator else 0.0


def _canonical_event_rows(rows: list[dict], allowed: tuple[str, ...]) -> list[dict]:
    totals: dict[str, dict[str, int | str]] = {}
    for row in rows:
        raw_name = str(row.get("eventName") or "")
        if raw_name not in allowed:
            continue
        name = CANONICAL_EVENTS.get(raw_name, raw_name)
        target = totals.setdefault(name, {"eventName": name, "eventCount": 0, "totalUsers": 0})
        target["eventCount"] = _integer(target["eventCount"]) + _integer(row.get("eventCount"))
        target["totalUsers"] = _integer(target["totalUsers"]) + _integer(row.get("totalUsers"))
    return sorted(totals.values(), key=lambda item: (-_integer(item["eventCount"]), str(item["eventName"])))


def _event_count(rows: list[dict], event_name: str) -> int:
    return sum(
        _integer(row.get("eventCount"))
        for row in rows
        if row.get("eventName") == event_name
    )


def _merge_article_rows(page_rows: list[dict], pr_rows: list[dict]) -> list[dict]:
    promotions: dict[str, dict[str, int]] = {}
    for row in pr_rows:
        path = str(row.get("pagePath") or "")
        event = CANONICAL_EVENTS.get(str(row.get("eventName") or ""), "")
        if event not in {"pr_impression", "pr_click"}:
            continue
        target = promotions.setdefault(path, {"prImpressions": 0, "prClicks": 0})
        key = "prImpressions" if event == "pr_impression" else "prClicks"
        target[key] += _integer(row.get("eventCount"))
    result: list[dict] = []
    for row in page_rows:
        item = dict(row)
        path = str(item.get("pagePath") or "")
        item.update(promotions.get(path, {"prImpressions": 0, "prClicks": 0}))
        item["clickRate"] = _percentage(_integer(item["prClicks"]), _integer(item.get("eventCount")))
        result.append(item)
    return result


def _owner_site_key(site_root: Path) -> str:
    try:
        identity = json.loads(_owner_identity_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(identity.get("site_key") or site_key_for_public_url(str(identity.get("public_url") or "")))


def _parse_event_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_range_start(value: str) -> datetime:
    now = datetime.now().astimezone()
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+)daysAgo", text)
    if match:
        target = now.date() - timedelta(days=int(match.group(1)))
        return datetime.combine(target, datetime.min.time(), tzinfo=now.tzinfo).astimezone(timezone.utc)
    if text == "today":
        return datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo).astimezone(timezone.utc)
    try:
        target = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return now.astimezone(timezone.utc) - timedelta(days=6)
    return datetime.combine(target, datetime.min.time(), tzinfo=now.tzinfo).astimezone(timezone.utc)


def _referrer_labels(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "(direct)", "(none)"
    parts = urlsplit(text)
    host = parts.hostname or "(direct)"
    if re.search(r"(^|\.)(google|bing|yahoo|duckduckgo)\.", host, re.I):
        return host, "organic"
    return host, "referral"


def _event_key(event_name: str) -> str:
    return CANONICAL_EVENTS.get(event_name, event_name.removeprefix("owner_"))


def _owner_historical_report(site_root: Path, start_date: str, end_date: str) -> dict:
    del end_date
    rows = load_owner_events(_owner_site_key(site_root), _date_range_start(start_date))
    articles: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    devices: dict[tuple[str, str, str], dict] = {}
    referrers: dict[tuple[str, str], dict] = {}
    genres: dict[str, dict] = {}
    events: dict[str, dict] = {}
    viewer_sessions: set[str] = set()
    page_views = 0
    pr_impressions = 0
    pr_clicks = 0

    for row in rows:
        raw_name = str(row.get("event_name") or "")
        name = _event_key(raw_name)
        session = str(row.get("session_hash") or "")
        event = events.setdefault(name, {"eventName": name, "eventCount": 0, "_users": set()})
        event["eventCount"] += 1
        if session:
            event["_users"].add(session)
        path = str(row.get("page_path") or "")
        article = articles.setdefault(path, {
            "pagePath": path,
            "pageTitle": str(row.get("page_title") or ""),
            "eventCount": 0,
            "prImpressions": 0,
            "prClicks": 0,
            "_users": set(),
        })
        if not article["pageTitle"]:
            article["pageTitle"] = str(row.get("page_title") or "")
        if name == "article_view":
            page_views += 1
            article["eventCount"] += 1
            if session:
                viewer_sessions.add(session)
                article["_users"].add(session)
            local_time = _parse_event_time(row.get("event_time")).astimezone()
            day = local_time.strftime("%Y%m%d")
            day_row = daily.setdefault(day, {"date": day, "eventCount": 0, "_users": set()})
            day_row["eventCount"] += 1
            if session:
                day_row["_users"].add(session)
            device_key = (
                str(row.get("device_category") or "unknown"),
                str(row.get("operating_system") or "unknown"),
                str(row.get("browser") or "unknown"),
            )
            device = devices.setdefault(device_key, {
                "deviceCategory": device_key[0], "operatingSystem": device_key[1],
                "browser": device_key[2], "eventCount": 0, "_users": set(),
            })
            device["eventCount"] += 1
            if session:
                device["_users"].add(session)
            source, medium = _referrer_labels(row.get("referrer"))
            referral = referrers.setdefault((source, medium), {
                "sessionSource": source, "sessionMedium": medium,
                "sessions": 0, "eventCount": 0, "_users": set(), "_sessions": set(),
            })
            referral["eventCount"] += 1
            if session:
                referral["_users"].add(session)
                referral["_sessions"].add(session)
            group = str(row.get("content_group") or "未分類")
            genre = genres.setdefault(group, {"contentGroup": group, "eventCount": 0, "_users": set()})
            genre["eventCount"] += 1
            if session:
                genre["_users"].add(session)
        elif name == "pr_impression":
            pr_impressions += 1
            article["prImpressions"] += 1
        elif name == "pr_click":
            pr_clicks += 1
            article["prClicks"] += 1

    article_rows: list[dict] = []
    for item in articles.values():
        users = item.pop("_users")
        item["activeUsers"] = len(users)
        item["clickRate"] = _percentage(_integer(item["prClicks"]), _integer(item["eventCount"]))
        if item["eventCount"] or item["prImpressions"] or item["prClicks"]:
            article_rows.append(item)
    event_rows = []
    for item in events.values():
        item["totalUsers"] = len(item.pop("_users"))
        event_rows.append(item)
    for item in daily.values():
        item["activeUsers"] = len(item.pop("_users"))
    for item in devices.values():
        item["activeUsers"] = len(item.pop("_users"))
    for item in referrers.values():
        item["activeUsers"] = len(item.pop("_users"))
        item["sessions"] = len(item.pop("_sessions"))
    for item in genres.values():
        item["activeUsers"] = len(item.pop("_users"))
    return {
        "summary": {
            "pageViews": page_views,
            "activeUsers": len(viewer_sessions),
            "sessions": len(viewer_sessions),
            "prImpressions": pr_impressions,
            "prClicks": pr_clicks,
            "clickRate": _percentage(pr_clicks, page_views),
            "prCtr": _percentage(pr_clicks, pr_impressions),
        },
        "articles": sorted(article_rows, key=lambda item: -_integer(item["eventCount"])),
        "daily": sorted(daily.values(), key=lambda item: str(item["date"])),
        "devices": sorted(devices.values(), key=lambda item: -_integer(item["eventCount"])),
        "referrers": sorted(referrers.values(), key=lambda item: -_integer(item["eventCount"])),
        "genres": sorted(genres.values(), key=lambda item: -_integer(item["eventCount"])),
        "events": sorted(event_rows, key=lambda item: -_integer(item["eventCount"])),
    }


def _owner_realtime_report(site_root: Path) -> dict:
    now = datetime.now(timezone.utc)
    rows = load_owner_events(_owner_site_key(site_root), now - timedelta(minutes=30), now)
    pages: dict[str, dict] = {}
    events: dict[str, dict] = {}
    minutes: dict[str, dict] = {}
    viewers: set[str] = set()
    page_views = 0
    pr_impressions = 0
    pr_clicks = 0
    for row in rows:
        name = _event_key(str(row.get("event_name") or ""))
        session = str(row.get("session_hash") or "")
        event = events.setdefault(name, {"eventName": name, "eventCount": 0, "_users": set()})
        event["eventCount"] += 1
        if session:
            event["_users"].add(session)
        age = max(0, min(29, int((now - _parse_event_time(row.get("event_time"))).total_seconds() // 60)))
        minute = str(age).zfill(2)
        minute_row = minutes.setdefault(minute, {
            "minutesAgo": minute, "pageViews": 0, "activeUsers": 0,
            "prImpressions": 0, "prClicks": 0, "_users": set(),
        })
        if name == "article_view":
            page_views += 1
            if session:
                viewers.add(session)
                minute_row["_users"].add(session)
            minute_row["pageViews"] += 1
            title = str(row.get("page_title") or row.get("page_path") or "")
            page = pages.setdefault(title, {
                "unifiedScreenName": title, "eventCount": 0, "_users": set(),
            })
            page["eventCount"] += 1
            if session:
                page["_users"].add(session)
        elif name == "pr_impression":
            pr_impressions += 1
            minute_row["prImpressions"] += 1
        elif name == "pr_click":
            pr_clicks += 1
            minute_row["prClicks"] += 1
    event_rows = []
    for item in events.values():
        item["totalUsers"] = len(item.pop("_users"))
        event_rows.append(item)
    for item in pages.values():
        item["activeUsers"] = len(item.pop("_users"))
    for item in minutes.values():
        item["activeUsers"] = len(item.pop("_users"))
    return {
        "summary": {
            "pageViews": page_views, "activeUsers": len(viewers),
            "prImpressions": pr_impressions, "prClicks": pr_clicks,
        },
        "pages": sorted(pages.values(), key=lambda item: -_integer(item["eventCount"])),
        "events": sorted(event_rows, key=lambda item: -_integer(item["eventCount"])),
        "minutes": sorted(minutes.values(), key=lambda item: _integer(item["minutesAgo"])),
    }


def _merge_rows(left: object, right: object, keys: tuple[str, ...], numbers: tuple[str, ...]) -> list[dict]:
    merged: dict[tuple[str, ...], dict] = {}
    for source in (left, right):
        for raw in source if isinstance(source, list) else []:
            if not isinstance(raw, dict):
                continue
            key = tuple(str(raw.get(name) or "") for name in keys)
            target = merged.setdefault(key, {name: raw.get(name, "") for name in keys})
            for name, value in raw.items():
                if name in numbers:
                    target[name] = _integer(target.get(name)) + _integer(value)
                elif name not in target or not target[name]:
                    target[name] = value
    return list(merged.values())


def _merged_summary(external: dict, owner: dict) -> dict:
    result = {
        key: _integer(external.get(key)) + _integer(owner.get(key))
        for key in ("pageViews", "activeUsers", "sessions", "prImpressions", "prClicks")
    }
    result["clickRate"] = _percentage(result["prClicks"], result["pageViews"])
    result["prCtr"] = _percentage(result["prClicks"], result["prImpressions"])
    return result


def _merge_historical_reports(external: dict, owner: dict) -> dict:
    result = deepcopy(external)
    result["summary"] = _merged_summary(external.get("summary", {}), owner.get("summary", {}))
    result["articles"] = _merge_rows(
        external.get("articles"), owner.get("articles"), ("pagePath",),
        ("eventCount", "activeUsers", "prImpressions", "prClicks"),
    )
    for item in result["articles"]:
        item["clickRate"] = _percentage(_integer(item.get("prClicks")), _integer(item.get("eventCount")))
    result["daily"] = _merge_rows(external.get("daily"), owner.get("daily"), ("date",), ("eventCount", "activeUsers"))
    result["devices"] = _merge_rows(
        external.get("devices"), owner.get("devices"),
        ("deviceCategory", "operatingSystem", "browser"), ("eventCount", "activeUsers"),
    )
    result["referrers"] = _merge_rows(
        external.get("referrers"), owner.get("referrers"),
        ("sessionSource", "sessionMedium"), ("sessions", "activeUsers", "eventCount"),
    )
    result["genres"] = _merge_rows(
        external.get("genres"), owner.get("genres"), ("contentGroup",), ("eventCount", "activeUsers"),
    )
    result["events"] = _merge_rows(
        external.get("events"), owner.get("events"), ("eventName",), ("eventCount", "totalUsers"),
    )
    return result


def _merge_realtime_reports(external: dict, owner: dict) -> dict:
    return {
        "summary": _merged_summary(external.get("summary", {}), owner.get("summary", {})),
        "pages": _merge_rows(
            external.get("pages"), owner.get("pages"), ("unifiedScreenName",), ("eventCount", "activeUsers"),
        ),
        "events": _merge_rows(
            external.get("events"), owner.get("events"), ("eventName",), ("eventCount", "totalUsers"),
        ),
        "minutes": _merge_rows(
            external.get("minutes"), owner.get("minutes"), ("minutesAgo",),
            ("pageViews", "activeUsers", "prImpressions", "prClicks"),
        ),
    }


def fetch_ga4_report(site_root: Path, start_date: str = "6daysAgo", end_date: str = "today") -> dict:
    """Read external GA4 traffic once, then add locally collected owner traffic."""
    try:
        from google.analytics.data_v1beta.types import (
            BatchRunReportsRequest,
            DateRange,
            Dimension,
            Filter,
            FilterExpression,
            Metric,
            RunReportRequest,
        )
    except ImportError as exc:
        raise RuntimeError("GA4読み取りライブラリがインストールされていません") from exc
    client, resource = _ga4_client(site_root)

    def event_filter(names: tuple[str, ...]) -> object:
        return FilterExpression(filter=Filter(
            field_name="eventName",
            in_list_filter=Filter.InListFilter(values=list(names), case_sensitive=True),
        ))

    def request(dimensions: list[str], metrics: list[str], names: tuple[str, ...], limit: int) -> object:
        return RunReportRequest(
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            dimensions=[Dimension(name=item) for item in dimensions],
            metrics=[Metric(name=item) for item in metrics],
            dimension_filter=event_filter(names),
            limit=limit,
            keep_empty_rows=False,
        )

    page_events = ARTICLE_EVENTS["external"]
    pr_events = ARTICLE_PR_EVENTS["external"]
    all_events = tuple(dict.fromkeys(page_events + ARTICLE_VISIT_EVENTS["external"] + pr_events))
    specs: list[tuple[str, list[str], list[str], tuple[str, ...], int]] = [
        ("summary", [], ["eventCount", "activeUsers", "sessions"], page_events, 1),
        ("articles", ["pagePath", "pageTitle"], ["eventCount", "activeUsers"], page_events, 1000),
        ("article_pr", ["pagePath", "eventName"], ["eventCount"], pr_events, 2000),
        ("daily", ["date"], ["eventCount", "activeUsers"], page_events, 400),
        ("devices", ["deviceCategory", "operatingSystem", "browser"], ["eventCount", "activeUsers"], page_events, 500),
        ("referrers", ["sessionSource", "sessionMedium"], ["sessions", "activeUsers", "eventCount"], page_events, 500),
        (
            "x_posts",
            [
                "dateHour",
                "sessionManualAdContent",
                "sessionManualCampaignName",
                "pagePath",
            ],
            ["sessions", "activeUsers", "eventCount"],
            page_events,
            5000,
        ),
        ("genres", ["contentGroup"], ["eventCount", "activeUsers"], page_events, 200),
        ("events", ["eventName"], ["eventCount", "totalUsers"], all_events, 100),
    ]
    requests = [request(dimensions, metrics, names, limit) for _, dimensions, metrics, names, limit in specs]
    responses: list[object] = []
    for offset in range(0, len(requests), 5):
        batch = client.batch_run_reports(
            BatchRunReportsRequest(property=resource, requests=requests[offset:offset + 5]),
            timeout=30,
        )
        responses.extend(list(getattr(batch, "reports", []) or []))
    if len(responses) != len(specs):
        raise RuntimeError("GA4から必要な集計結果がすべて返りませんでした")

    raw: dict[str, list[dict]] = {}
    for spec, response in zip(specs, responses):
        name, dimensions, metrics, _, _ = spec
        raw[name] = _report_rows(response, dimensions, metrics)

    result: dict[str, object] = {
        "version": ANALYTICS_VERSION,
        "mode": "historical",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "start_date": start_date,
        "end_date": end_date,
    }
    event_rows = _canonical_event_rows(raw["events"], all_events)
    summary = _first_row(raw["summary"], ["eventCount", "activeUsers", "sessions"])
    summary["pageViews"] = summary.pop("eventCount")
    summary["prImpressions"] = _event_count(event_rows, "pr_impression")
    summary["prClicks"] = _event_count(event_rows, "pr_click")
    summary["clickRate"] = _percentage(summary["prClicks"], summary["pageViews"])
    summary["prCtr"] = _percentage(summary["prClicks"], summary["prImpressions"])
    external = {
        "summary": summary,
        "articles": _merge_article_rows(raw["articles"], raw["article_pr"]),
        "daily": raw["daily"],
        "devices": raw["devices"],
        "referrers": raw["referrers"],
        "x_posts": [
            row for row in raw["x_posts"]
            if str(row.get("sessionManualAdContent") or "").strip()
            and str(row.get("sessionManualCampaignName") or "").strip()
            in {"article_post", "owned_contest"}
        ],
        "genres": raw["genres"],
        "events": event_rows,
    }
    owner = _owner_historical_report(site_root, start_date, end_date)
    result["external"] = external
    result["all"] = _merge_historical_reports(external, owner)
    save_ga4_cache(site_root, "historical", result)
    return result


def fetch_ga4_realtime(site_root: Path) -> dict:
    """Read external GA4 realtime data and merge the local owner queue."""
    try:
        from google.analytics.data_v1beta.types import (
            Dimension,
            Metric,
            RunRealtimeReportRequest,
        )
    except ImportError as exc:
        raise RuntimeError("GA4読み取りライブラリがインストールされていません") from exc
    client, resource = _ga4_client(site_root)

    specs = [
        ("pages", ["unifiedScreenName"], ["screenPageViews", "activeUsers"]),
        ("activity", ["minutesAgo", "eventName"], ["eventCount"]),
    ]

    def run(spec: tuple[str, list[str], list[str]]) -> tuple[str, list[dict]]:
        name, dimensions, metrics = spec
        response = client.run_realtime_report(RunRealtimeReportRequest(
            property=resource,
            dimensions=[Dimension(name=item) for item in dimensions],
            metrics=[Metric(name=item) for item in metrics],
            limit=2000,
            return_property_quota=True,
        ), timeout=20)
        return name, _report_rows(response, dimensions, metrics)

    with ThreadPoolExecutor(max_workers=len(specs)) as executor:
        raw = dict(executor.map(run, specs))

    result: dict[str, object] = {
        "version": ANALYTICS_VERSION,
        "mode": "realtime",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_minutes": 30,
    }
    activity = raw["activity"]
    pages = [
        {
            "unifiedScreenName": str(row.get("unifiedScreenName") or ""),
            "eventCount": _integer(row.get("screenPageViews")),
            "activeUsers": _integer(row.get("activeUsers")),
        }
        for row in raw["pages"]
    ]
    allowed = tuple(dict.fromkeys(
        ARTICLE_EVENTS["external"] + ARTICLE_VISIT_EVENTS["external"] + ARTICLE_PR_EVENTS["external"]
    ))
    event_rows = _canonical_event_rows(activity, allowed)
    minute_totals: dict[str, dict[str, int | str]] = {}
    for row in activity:
        raw_event = str(row.get("eventName") or "")
        if raw_event not in allowed:
            continue
        minute = str(row.get("minutesAgo") or "0").zfill(2)
        target = minute_totals.setdefault(minute, {
            "minutesAgo": minute, "pageViews": 0, "activeUsers": 0,
            "prImpressions": 0, "prClicks": 0,
        })
        key = {
            "article_view": "pageViews", "article_visit": "activeUsers",
            "pr_impression": "prImpressions", "pr_click": "prClicks",
        }.get(CANONICAL_EVENTS.get(raw_event, raw_event))
        if key:
            target[key] = _integer(target[key]) + _integer(row.get("eventCount"))
    summary = {
        "pageViews": _event_count(event_rows, "article_view"),
        "activeUsers": _event_count(event_rows, "article_visit"),
        "prImpressions": _event_count(event_rows, "pr_impression"),
        "prClicks": _event_count(event_rows, "pr_click"),
    }
    external = {
        "summary": summary,
        "pages": pages,
        "events": event_rows,
        "minutes": sorted(minute_totals.values(), key=lambda item: _integer(item["minutesAgo"])),
    }
    owner = _owner_realtime_report(site_root)
    result["external"] = external
    result["all"] = _merge_realtime_reports(external, owner)
    save_ga4_cache(site_root, "realtime", result)
    return result


def ga4_config_path(site_root: Path) -> Path:
    return site_root / "assets" / "common" / "analytics-config.js"


def load_ga4_measurement_id(site_root: Path) -> str:
    try:
        source = ga4_config_path(site_root).read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'measurementId\s*:\s*"([^"]*)"', source)
    return match.group(1).strip() if match else ""


def _owner_identity_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "analytics-owner-v2.json"


def _ensure_owner_record(site_root: Path) -> dict[str, object]:
    path = _owner_identity_path(site_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    token = str(data.get("token") or "")
    token_hash = str(data.get("token_hash") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token) or token_hash != hashlib.sha256(token.encode()).hexdigest():
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        data = {
            "version": ANALYTICS_VERSION,
            "token": token,
            "token_hash": token_hash,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    data["version"] = ANALYTICS_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _write_ga4_config(
    site_root: Path,
    measurement_id: str,
    owner_hash: str,
    site_key: str = "",
) -> Path:
    path = ga4_config_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "/* Managed by Indanya Studio. The owner hash is public; the registration token is not. */\n"
        "window.INDANYA_GA4 = Object.freeze({\n"
        f"  measurementId: {json.dumps(measurement_id, ensure_ascii=True)},\n"
        f"  ownerTokenHash: {json.dumps(owner_hash, ensure_ascii=True)},\n"
        f"  ownerSiteKey: {json.dumps(site_key, ensure_ascii=True)},\n"
        f"  ownerCollector: {json.dumps(COLLECTOR_BASE_URL, ensure_ascii=True)},\n"
        f"  trackingVersion: {ANALYTICS_VERSION}\n"
        "});\n",
        encoding="utf-8",
    )
    return path


def ensure_ga4_owner_identity(site_root: Path, public_url: str = "") -> dict[str, str]:
    record = _ensure_owner_record(site_root)
    normalized_url = normalize_public_url(public_url or record.get("public_url", ""))
    if normalized_url:
        record["public_url"] = normalized_url
        record["site_key"] = site_key_for_public_url(normalized_url)
        _owner_identity_path(site_root).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _write_ga4_config(
        site_root,
        load_ga4_measurement_id(site_root),
        str(record["token_hash"]),
        str(record.get("site_key") or ""),
    )
    return {str(key): str(value) for key, value in record.items()}


def owner_registration_url(site_root: Path, public_url: str) -> str:
    token = ensure_ga4_owner_identity(site_root, public_url)["token"]
    parts = urlsplit(public_url.rstrip("/") + "/")
    query = urlencode({"indanya_owner": token})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def save_ga4_measurement_id(site_root: Path, measurement_id: str) -> Path:
    value = str(measurement_id or "").strip().upper()
    if value and not re.fullmatch(r"G-[A-Z0-9]+", value):
        raise ValueError("GA4の測定IDは G- から始まる英数字です")
    record = _ensure_owner_record(site_root)
    return _write_ga4_config(
        site_root,
        value,
        str(record["token_hash"]),
        str(record.get("site_key") or ""),
    )


def ga4_credentials_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "ga4-credentials.json"


def load_ga4_property_id(site_root: Path) -> str:
    path = site_root / ".article-studio" / "ga4-settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("property_id") or "").strip()


def save_ga4_property_id(site_root: Path, property_id: str) -> Path:
    value = str(property_id or "").strip()
    if value and not re.fullmatch(r"\d+", value):
        raise ValueError("GA4のプロパティIDは数字だけで入力してください")
    path = site_root / ".article-studio" / "ga4-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"property_id": value}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_ga4_credentials(site_root: Path, source: Path) -> Path:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise ValueError("Google CloudのサービスアカウントJSONを選択してください")
    if not data.get("client_email") or not data.get("private_key"):
        raise ValueError("サービスアカウントJSONに必要な鍵がありません")
    target = ga4_credentials_path(site_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target


def ga4_cache_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "ga4-cache-v2.json"


def load_ga4_cache(site_root: Path) -> dict:
    try:
        data = json.loads(ga4_cache_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("version") != ANALYTICS_VERSION:
        return {}
    if str(data.get("property_id") or "") != load_ga4_property_id(site_root):
        return {}
    return data


def save_ga4_cache(site_root: Path, mode: str, report: dict) -> Path:
    path = ga4_cache_path(site_root)
    current = load_ga4_cache(site_root)
    data = {
        "version": ANALYTICS_VERSION,
        "property_id": load_ga4_property_id(site_root),
        "historical": current.get("historical"),
        "realtime": current.get("realtime"),
    }
    data[mode] = report
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return path


def local_content_summary(site_root: Path) -> dict[str, int]:
    articles: list[dict] = []
    for path in (site_root / ".article-studio" / "drafts").glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and (item.get("published_url") or item.get("editorial_status") == "published"):
            articles.append(item)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    def recent(item: dict) -> bool:
        try:
            published = datetime.fromisoformat(str(item.get("published_at") or "").replace("Z", "+00:00"))
            return (published if published.tzinfo else published.replace(tzinfo=timezone.utc)) >= cutoff
        except ValueError:
            return False

    return {
        "published": len(articles),
        "published_7d": sum(1 for item in articles if recent(item)),
        "images": sum(len(item.get("images") or []) for item in articles if isinstance(item.get("images"), list)),
        "videos": sum(len(item.get("videos") or []) for item in articles if isinstance(item.get("videos"), list)),
    }


def ga4_url() -> str:
    return "https://analytics.google.com/"


def search_console_url(public_url: str) -> str:
    return (
        "https://search.google.com/search-console/performance/search-analytics"
        f"?resource_id={quote(public_url, safe='')}"
    )
