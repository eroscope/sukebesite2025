"""Local, read-only growth diagnostics. Never infer readers from affiliate clicks."""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .fanza_affiliate import unwrap_fanza_affiliate_url


def source_reference(payload: dict) -> str:
    raw = str(payload.get("resolved_source_url") or payload.get("source_url") or "")
    try:
        parts = urlsplit(raw)
        if parts.hostname in {"al.dmm.com", "al.fanza.co.jp"} or (parts.hostname or "").endswith(".dmm.co.jp"):
            parts = urlsplit(unwrap_fanza_affiliate_url(raw))
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            return ""
        if parts.hostname in {"al.dmm.com", "al.fanza.co.jp"}:
            return ""
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"affiliate_id", "aff_id", "indanya_owner", "token"}]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except (ValueError, TypeError):
        return ""


def provenance_markup(payload: dict) -> str:
    url = source_reference(payload)
    if not url:
        return ""
    host = urlsplit(url).hostname or ""
    return ('<aside class="article-source-reference"><h2>参照情報</h2>'
            f'<a href="{html.escape(url, quote=True)}" rel="nofollow noopener" target="_blank">{html.escape(host)}の参照ページ</a>'
            '<p>リンク先の掲載内容・公開状況は変更される場合があります。</p></aside>')


def _read(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _report_date(value: str, today: date) -> date:
    offsets = {"today": 0, "yesterday": 1}
    match = re.fullmatch(r"(\d+)daysAgo", value)
    if value in offsets or match:
        return today - timedelta(days=offsets[value] if value in offsets else int(match[1]))
    return date.fromisoformat(value)


def build_growth_comparison(report: dict) -> dict:
    """Compare equal full weeks. Event counts are not user-level conversions."""
    result = {"status": "measurement_unavailable", "signals": [], "automatic_frequency_change": False}
    external = report.get("external") or {}
    quality = report.get("measurement_quality") or {}
    if not all(key in external and key in quality for key in ("daily_site", "daily_funnel")):
        return result
    for key in ("daily_site", "daily_funnel"):
        if any(quality[key].get(flag) for flag in ("thresholded", "sampled", "data_loss", "empty_reason", "truncated")):
            return {**result, "status": "measurement_limited"}
    try:
        zone = ZoneInfo(quality["daily_site"]["time_zone"])
        generated = datetime.fromisoformat(report["generated_at"])
        if generated.tzinfo is None:
            return result
        today = generated.astimezone(zone).date()
        start = _report_date(report["start_date"], today)
        end = min(_report_date(report["end_date"], today), today - timedelta(days=3))
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        return result
    first = end - timedelta(days=13)
    if start > first:
        return {**result, "status": "insufficient_period"}
    windows = []
    for beginning in (first, first + timedelta(days=7)):
        ending = beginning + timedelta(days=6)
        dates = {(beginning + timedelta(days=index)).strftime("%Y%m%d") for index in range(7)}
        metrics = {key: 0 for key in ("sessions", "screenPageViews", "engagedSessions")}
        for row in external["daily_site"]:
            if row.get("date") in dates:
                for key in metrics:
                    metrics[key] += int(row.get(key) or 0)
        events: dict[str, int] = {}
        for row in external["daily_funnel"]:
            if row.get("date") in dates:
                key = str(row.get("eventName") or "")
                events[key] = events.get(key, 0) + int(row.get("eventCount") or 0)
        def rate(numerator, denominator):
            return round(100 * numerator / denominator, 2) if numerator is not None and denominator else None
        windows.append({
            "start": beginning.isoformat(), "end": ending.isoformat(), **metrics, "events": events,
            "engagement_rate": rate(metrics["engagedSessions"], metrics["sessions"]),
            "related_actions_per_100_pageviews": rate(events.get("related_article_click"), metrics["screenPageViews"]),
            "pr_clicks_per_100_impressions": rate(events.get("pr_click"), events.get("pr_impression")),
        })
    previous, current = windows
    # A review threshold is not a statistical significance test or a causal claim.
    ready = min(previous["sessions"], current["sessions"]) >= 100
    result.update(status="ready_for_review" if ready else "small_sample", previous=previous, current=current, time_zone=str(zone))
    for key, label in (("sessions", "acquisition"), ("related_actions_per_100_pageviews", "recirculation"), ("pr_clicks_per_100_impressions", "outbound")):
        old, new = previous.get(key), current.get(key)
        change = round((new - old) / old * 100, 2) if old and new is not None else None
        result.setdefault("changes_percent", {})[key] = change
        enough_events = (key == "sessions" or min(previous["events"].get("related_article_click" if label == "recirculation" else "pr_impression", 0), current["events"].get("related_article_click" if label == "recirculation" else "pr_impression", 0)) >= 30)
        if ready and enough_events and change is not None and change <= -20:
            result["signals"].append(label + "_decline_observed")
    return result


def growth_comparison_text(comparison: dict) -> str:
    labels = {"measurement_unavailable": "比較用データ未取得", "measurement_limited": "集計制限のため比較保留",
              "insufficient_period": "比較期間不足", "small_sample": "少数データのため原因判定保留", "ready_for_review": "比較可能・因果関係は未確定"}
    status = labels.get(comparison.get("status"), "比較用データ未取得")
    current, previous = comparison.get("current"), comparison.get("previous")
    if not current or not previous:
        return "読者動向: " + status
    return (f"読者動向: {previous['start']}〜{previous['end']} {previous['sessions']}訪問 → "
            f"{current['start']}〜{current['end']} {current['sessions']}訪問 / {comparison.get('time_zone', '')} / {status}")


def write_growth_review(site_root: Path, report: dict | None = None) -> dict:
    report = report or (_read(site_root / ".article-studio/ga4-cache-v2.json", {}).get("historical") or {})
    external = report.get("external") or {}
    site = external.get("site_summary") or {}
    sessions = site.get("sessions")
    rows = external.get("articles") or []
    targets = []
    for row in sorted(rows, key=lambda item: int(item.get("eventCount") or 0), reverse=True):
        match = re.search(r"/articles/([a-z0-9-]+)\.html$", str(row.get("pagePath") or ""))
        if match and match[1] not in targets:
            targets.append(match[1])
    articles = _read(site_root / "data/articles.json", [])
    if isinstance(articles, dict):
        articles = articles.get("articles", [])
    for row in sorted(articles, key=lambda item: str(item.get("published_at") or ""), reverse=True):
        if row.get("status") == "published" and row.get("slug") not in targets:
            targets.append(row["slug"])
    result = {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement_generated_at": report.get("generated_at"),
        "range": [report.get("start_date"), report.get("end_date")],
        "site_summary": site,
        "article_summary": external.get("summary") or {},
        "reader_events": external.get("reader_events") or [],
        "acquisition": external.get("acquisition") or [],
        "comparison": report.get("growth_comparison") or build_growth_comparison(report),
        "status": "measurement_unavailable" if sessions is None else "small_sample" if int(sessions) < 100 else "ready_for_review",
        "priority_articles": targets[:10],
        "automatic_frequency_change": False,
        "limitations": [
            "Affiliate clicks are not audience visits; owner/QA clicks and direct social referrals may differ.",
            "Browser blocking and age-gate timing can undercount. Unknown is not zero.",
            "100 sessions is a review trigger, not statistical proof of causation.",
            "Change one factor per comparison; do not infer ban causes from a third-party checker alone.",
        ],
    }
    folder = site_root / ".article-studio/reader-growth"
    folder.mkdir(parents=True, exist_ok=True)
    names = ["latest.json", datetime.now(timezone.utc).strftime("%Y-%m-%d.json")]
    if result["comparison"].get("current"):
        names.append("comparison-latest.json")
    for name in names:
        temporary = folder / (name + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(folder / name)
    return result
