from __future__ import annotations

import html
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from indanya_desktop.fanza_affiliate import (  # noqa: E402
    FanzaAffiliateConfigurationError,
    bind_payload_fanza_affiliate_links,
    build_fanza_affiliate_url,
    canonicalize_payload_fanza_links,
    load_fanza_settings,
    normalize_fanza_affiliate_id,
    rewrite_published_fanza_links,
    save_fanza_settings,
    unwrap_fanza_affiliate_url,
)


PRODUCT_URL = "https://video.dmm.co.jp/av/content/?id=abc001"


class FanzaAffiliateTests(unittest.TestCase):
    def test_one_time_setting_accepts_id_or_complete_generated_link(self) -> None:
        generated = (
            "https://al.dmm.com/?lurl=" + quote(PRODUCT_URL, safe="")
            + "&af_id=owner-name-001&ch=toolbar&ch_id=link"
        )

        self.assertEqual("owner-name-001", normalize_fanza_affiliate_id("owner-name-001"))
        self.assertEqual("owner-name-001", normalize_fanza_affiliate_id(generated))

    def test_link_generation_replaces_other_publishers_id(self) -> None:
        copied = (
            "https://al.dmm.co.jp/?lurl=" + quote(PRODUCT_URL, safe="")
            + "&af_id=someone-else-999&ch=api"
        )

        linked = build_fanza_affiliate_url(copied, "site-owner-001")
        parsed = urlparse(linked)
        query = parse_qs(parsed.query)

        self.assertEqual("al.dmm.com", parsed.hostname)
        self.assertEqual(["site-owner-001"], query["af_id"])
        self.assertEqual([PRODUCT_URL], query["lurl"])
        self.assertEqual(["link_tool"], query["ch"])
        self.assertEqual(["link"], query["ch_id"])
        self.assertNotIn("someone-else-999", linked)

    def test_draft_storage_removes_account_specific_tracking(self) -> None:
        copied = (
            "https://al.dmm.com/?lurl=" + quote(PRODUCT_URL, safe="")
            + "&af_id=someone-else-999&ch=toolbar&ch_id=link"
        )
        payload = {"blocks": [{"type": "product_cta", "url": copied}]}

        normalized = canonicalize_payload_fanza_links(payload)

        self.assertEqual(PRODUCT_URL, normalized["blocks"][0]["url"])
        self.assertNotIn("af_id", normalized["blocks"][0]["url"])

    def test_render_binding_uses_current_setting_without_changing_draft(self) -> None:
        payload = {"blocks": [{"type": "product_cta", "url": PRODUCT_URL}]}

        bound = bind_payload_fanza_affiliate_links(
            payload, "current-owner-002", require_configured=True
        )

        self.assertEqual(PRODUCT_URL, payload["blocks"][0]["url"])
        self.assertIn("af_id=current-owner-002", bound["blocks"][0]["url"])
        self.assertEqual("configured", bound["blocks"][0]["affiliate_status"])

    def test_missing_setting_keeps_preview_non_clickable_state(self) -> None:
        payload = {"blocks": [{"type": "product_cta", "url": PRODUCT_URL}]}

        preview = bind_payload_fanza_affiliate_links(
            payload, "", require_configured=False
        )

        self.assertEqual("missing", preview["blocks"][0]["affiliate_status"])
        self.assertEqual(PRODUCT_URL, preview["blocks"][0]["url"])
        with self.assertRaises(FanzaAffiliateConfigurationError):
            bind_payload_fanza_affiliate_links(payload, "", require_configured=True)

    def test_related_search_is_direct_without_id_and_affiliate_when_configured(self) -> None:
        search_url = (
            "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr=制服"
        )
        payload = {
            "blocks": [{
                "type": "related_link",
                "url": search_url,
                "affiliate_network": "fanza",
                "affiliate_eligible": True,
            }]
        }

        direct = bind_payload_fanza_affiliate_links(
            payload, "", require_configured=True
        )
        linked = bind_payload_fanza_affiliate_links(
            payload, "owner-005", require_configured=True
        )

        self.assertEqual("direct", direct["blocks"][0]["affiliate_status"])
        self.assertEqual(
            parse_qs(urlparse(search_url).query),
            parse_qs(urlparse(direct["blocks"][0]["url"]).query),
        )
        self.assertEqual("configured", linked["blocks"][0]["affiliate_status"])
        self.assertIn("af_id=owner-005", linked["blocks"][0]["url"])

    def test_invalid_product_destination_is_never_publishable(self) -> None:
        payload = {
            "blocks": [{
                "type": "product_cta",
                "url": "https://al.dmm.co.jp/?lurl=not-a-product&af_id=other-001",
            }]
        }

        preview = bind_payload_fanza_affiliate_links(
            payload, "owner-001", require_configured=False
        )
        self.assertEqual("invalid", preview["blocks"][0]["affiliate_status"])
        with self.assertRaises(FanzaAffiliateConfigurationError):
            bind_payload_fanza_affiliate_links(
                payload, "owner-001", require_configured=True
            )

    def test_settings_round_trip_stores_only_normalized_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = (
                "https://al.dmm.com/?lurl=" + quote(PRODUCT_URL, safe="")
                + "&af_id=round-trip-003&ch=toolbar&ch_id=link"
            )
            saved = save_fanza_settings(root, generated)

            self.assertEqual("round-trip-003", saved)
            self.assertEqual(
                {"affiliate_id": "round-trip-003"}, load_fanza_settings(root)
            )

    def test_existing_public_buttons_are_rewritten_without_touching_other_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            article_root = root / "articles"
            article_root.mkdir()
            article = article_root / "sample.html"
            article.write_text(
                '<a class="fanza-product-button" href="'
                + html.escape(PRODUCT_URL, quote=True)
                + '">商品</a><a href="https://example.com/">通常リンク</a>',
                encoding="utf-8",
            )

            result = rewrite_published_fanza_links(root, "rewrite-owner-004")
            rendered = article.read_text(encoding="utf-8")

        self.assertEqual(1, result["changed_files"])
        self.assertEqual(1, result["changed_links"])
        self.assertIn("af_id=rewrite-owner-004", rendered)
        self.assertIn('href="https://example.com/"', rendered)
        self.assertEqual(PRODUCT_URL, unwrap_fanza_affiliate_url(
            html.unescape(rendered.split('href="', 1)[1].split('"', 1)[0])
        ))


if __name__ == "__main__":
    unittest.main()
