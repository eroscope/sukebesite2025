from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.indanya_desktop.analytics import (
    _owner_realtime_report,
    ensure_ga4_owner_identity,
    fetch_ga4_realtime,
    fetch_ga4_report,
    load_ga4_cache,
    load_ga4_measurement_id,
    owner_registration_url,
    save_ga4_measurement_id,
    save_ga4_property_id,
)
from tools.indanya_desktop.owner_collector import (
    record_events,
    register_browser,
    validate_preflight_origin,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL = "https://example.com/site/"


class AnalyticsTests(unittest.TestCase):
    @staticmethod
    def response(rows: list[tuple[list[str], list[str]]]) -> SimpleNamespace:
        return SimpleNamespace(rows=[
            SimpleNamespace(
                dimension_values=[SimpleNamespace(value=value) for value in dimensions],
                metric_values=[SimpleNamespace(value=value) for value in metrics],
            )
            for dimensions, metrics in rows
        ])

    @staticmethod
    def prepare_owner(root: Path, app_data: Path) -> tuple[dict[str, str], str]:
        record = ensure_ga4_owner_identity(root, PUBLIC_URL)
        app_data.mkdir(parents=True, exist_ok=True)
        (app_data / "sites.json").write_text(json.dumps({
            "active_id": "test",
            "sites": [{
                "site_id": "test",
                "name": "Test",
                "public_url": PUBLIC_URL,
                "local_path": str(root),
            }],
        }), encoding="utf-8")
        session = register_browser(record["site_key"], record["token"], "Chrome / Windows", root)
        return record, session

    @staticmethod
    def owner_event(event_id: str, name: str) -> dict[str, str]:
        return {
            "eventId": event_id,
            "eventName": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pagePath": "/site/articles/a.html",
            "pageTitle": "記事A",
            "contentGroup": "画像",
            "deviceCategory": "desktop",
            "operatingSystem": "Windows",
            "browser": "Chrome",
        }

    def test_ga4_script_routes_owner_locally_and_external_to_ga4(self) -> None:
        script = (ROOT / "assets" / "common" / "ga4.js").read_text(encoding="utf-8")
        self.assertIn('send_page_view: false', script)
        self.assertIn('sendEvent("article_view"', script)
        self.assertIn('sendEvent("article_visit"', script)
        self.assertIn("visitWindowMs", script)
        self.assertIn('`owner_${eventName}`', script)
        self.assertIn("ownerCollector", script)
        self.assertIn("owner-local-sent", script)
        self.assertIn("owner-local-queued", script)
        self.assertIn('targetAddressSpace: "loopback"', script)
        self.assertIn("controller.abort()", script)
        self.assertIn("setInterval(() => void flushOwnerQueue(), 15000)", script)
        self.assertIn("intersectionRatio < 0.5", script)
        self.assertIn("}, 1000)", script)
        self.assertIn('dataset.indanyaAnalytics', script)
        self.assertIn('indanyaAnalyticsStatus = "gtag-loaded"', script)
        self.assertIn('document.addEventListener("DOMContentLoaded"', script)
        self.assertLess(
            script.index('document.addEventListener("DOMContentLoaded"'),
            script.index("document.body.dataset.articleSlug"),
        )
        self.assertNotIn("script.google.com", script)

    def test_age_gate_preserves_registration_url_and_loads_ga4(self) -> None:
        gate = (ROOT / "assets" / "common" / "age-gate.js").read_text(encoding="utf-8")
        age_page = (ROOT / "age-check.html").read_text(encoding="utf-8")
        self.assertIn("analytics-config.js", gate)
        self.assertIn("ga4.js", gate)
        self.assertIn('analyticsLoaderVersion = "8"', gate)
        self.assertIn('destination.searchParams.set("return", location.href)', gate)
        self.assertIn('new URLSearchParams(location.search).get("return")', age_page)
        self.assertNotIn("site-events.js", gate)

    def test_owner_token_is_secret_but_hash_and_collector_are_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_ga4_measurement_id(root, "g-test123")
            record = ensure_ga4_owner_identity(root, PUBLIC_URL)
            config = (root / "assets/common/analytics-config.js").read_text(encoding="utf-8")
            self.assertEqual(load_ga4_measurement_id(root), "G-TEST123")
            self.assertNotIn(record["token"], config)
            self.assertIn(hashlib.sha256(record["token"].encode()).hexdigest(), config)
            self.assertIn(record["site_key"], config)
            self.assertIn("http://127.0.0.1:18770/v1", config)
            registration = owner_registration_url(root, PUBLIC_URL)
            self.assertTrue(registration.startswith(f"{PUBLIC_URL}?indanya_owner="))
            self.assertIn(record["token"], registration)

    def test_ga4_measurement_id_requires_g_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                save_ga4_measurement_id(Path(temporary), "UA-123")

    def test_owner_store_deduplicates_and_keeps_one_browser_as_one_visitor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "site"
            root.mkdir()
            app_data = Path(temporary) / "appdata"
            database = Path(temporary) / "owner.sqlite3"
            with patch.dict(os.environ, {
                "INDANYA_APP_DATA": str(app_data),
                "INDANYA_OWNER_ANALYTICS_DB": str(database),
            }):
                record, session = self.prepare_owner(root, app_data)
                events = [
                    self.owner_event("viewevent001", "owner_article_view"),
                    self.owner_event("viewevent002", "owner_article_view"),
                ]
                record_events(record["site_key"], session, events)
                record_events(record["site_key"], session, events)
                report = _owner_realtime_report(root)
            self.assertEqual(report["summary"]["pageViews"], 2)
            self.assertEqual(report["summary"]["activeUsers"], 1)

    def test_preflight_accepts_managed_origin_without_site_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "site"
            root.mkdir()
            app_data = Path(temporary) / "appdata"
            database = Path(temporary) / "owner.sqlite3"
            with patch.dict(os.environ, {
                "INDANYA_APP_DATA": str(app_data),
                "INDANYA_OWNER_ANALYTICS_DB": str(database),
            }):
                self.prepare_owner(root, app_data)
                self.assertEqual(validate_preflight_origin("https://example.com", root), "https://example.com")
                with self.assertRaises(PermissionError):
                    validate_preflight_origin("https://not-managed.example", root)

    def test_historical_report_fetches_external_once_and_merges_local_owner(self) -> None:
        test = self

        class Client:
            def __init__(self) -> None:
                self.calls = 0

            def batch_run_reports(self, request, timeout: int):
                self.calls += 1
                test.assertEqual(timeout, 30)
                reports = []
                for item in request.requests:
                    dimensions = [dimension.name for dimension in item.dimensions]
                    events = set(item.dimension_filter.filter.in_list_filter.values)
                    test.assertFalse(any(name.startswith("owner_") for name in events))
                    if not dimensions:
                        reports.append(test.response([([], ["5", "2", "3"])]))
                    elif dimensions == ["pagePath", "pageTitle"]:
                        reports.append(test.response([(["/site/articles/a.html", "記事A"], ["5", "2"])]))
                    elif dimensions == ["pagePath", "eventName"]:
                        reports.append(test.response([
                            (["/site/articles/a.html", "article_pr_impression"], ["4"]),
                            (["/site/articles/a.html", "article_pr_click"], ["1"]),
                        ]))
                    elif dimensions == ["eventName"]:
                        reports.append(test.response([
                            (["article_view"], ["5", "2"]),
                            (["article_visit"], ["2", "2"]),
                            (["article_pr_impression"], ["4", "2"]),
                            (["article_pr_click"], ["1", "1"]),
                        ]))
                    else:
                        reports.append(test.response([]))
                return SimpleNamespace(reports=reports)

        client = Client()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "site"
            root.mkdir()
            app_data = Path(temporary) / "appdata"
            database = Path(temporary) / "owner.sqlite3"
            with patch.dict(os.environ, {
                "INDANYA_APP_DATA": str(app_data),
                "INDANYA_OWNER_ANALYTICS_DB": str(database),
            }):
                record, session = self.prepare_owner(root, app_data)
                record_events(record["site_key"], session, [
                    self.owner_event("viewevent101", "owner_article_view"),
                    self.owner_event("viewevent102", "owner_article_view"),
                    self.owner_event("imprevent101", "owner_article_pr_impression"),
                    self.owner_event("clickevent101", "owner_article_pr_click"),
                ])
                save_ga4_property_id(root, "1")
                with patch("tools.indanya_desktop.analytics._ga4_client", return_value=(client, "properties/1")):
                    data = fetch_ga4_report(root)
                cache = load_ga4_cache(root)
        self.assertEqual(client.calls, 2)
        self.assertEqual(data["external"]["summary"]["pageViews"], 5)
        self.assertEqual(data["all"]["summary"]["pageViews"], 7)
        self.assertEqual(data["external"]["summary"]["activeUsers"], 2)
        self.assertEqual(data["all"]["summary"]["activeUsers"], 3)
        self.assertEqual(data["external"]["summary"]["prClicks"], 1)
        self.assertEqual(data["all"]["summary"]["prClicks"], 2)
        self.assertEqual(data["all"]["articles"][0]["prImpressions"], 5)
        self.assertEqual(cache["historical"]["version"], 8)

    def test_realtime_report_switches_pages_without_second_fetch(self) -> None:
        test = self

        class Client:
            def run_realtime_report(self, request, timeout: int):
                test.assertEqual(timeout, 20)
                dimensions = [dimension.name for dimension in request.dimensions]
                if dimensions == ["unifiedScreenName"]:
                    return test.response([(["記事A"], ["2", "1"])])
                return test.response([
                    (["00", "article_view"], ["2"]),
                    (["00", "article_visit"], ["1"]),
                    (["00", "article_pr_impression"], ["2"]),
                    (["01", "article_pr_click"], ["1"]),
                ])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "site"
            root.mkdir()
            app_data = Path(temporary) / "appdata"
            database = Path(temporary) / "owner.sqlite3"
            with patch.dict(os.environ, {
                "INDANYA_APP_DATA": str(app_data),
                "INDANYA_OWNER_ANALYTICS_DB": str(database),
            }):
                record, session = self.prepare_owner(root, app_data)
                record_events(record["site_key"], session, [
                    self.owner_event("viewevent201", "owner_article_view"),
                    self.owner_event("imprevent201", "owner_article_pr_impression"),
                    self.owner_event("clickevent201", "owner_article_pr_click"),
                ])
                save_ga4_property_id(root, "1")
                with patch("tools.indanya_desktop.analytics._ga4_client", return_value=(Client(), "properties/1")):
                    data = fetch_ga4_realtime(root)
        self.assertEqual(data["external"]["summary"]["pageViews"], 2)
        self.assertEqual(data["all"]["summary"]["pageViews"], 3)
        self.assertEqual(data["external"]["summary"]["activeUsers"], 1)
        self.assertEqual(data["all"]["summary"]["activeUsers"], 2)
        self.assertEqual(data["external"]["summary"]["prImpressions"], 2)
        self.assertEqual(data["all"]["summary"]["prImpressions"], 3)
        self.assertEqual(data["external"]["summary"]["prClicks"], 1)
        self.assertEqual(data["all"]["summary"]["prClicks"], 2)


if __name__ == "__main__":
    unittest.main()
