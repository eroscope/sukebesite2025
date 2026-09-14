"""Local, read-only growth diagnostics. Never infer readers from affiliate clicks."""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement_generated_at": report.get("generated_at"),
        "range": [report.get("start_date"), report.get("end_date")],
        "site_summary": site,
        "article_summary": external.get("summary") or {},
        "reader_events": external.get("reader_events") or [],
        "acquisition": external.get("acquisition") or [],
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
    for name in ("latest.json", datetime.now(timezone.utc).strftime("%Y-%m-%d.json")):
        temporary = folder / (name + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(folder / name)
    return result
