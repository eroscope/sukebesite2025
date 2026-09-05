from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.affiliate_opportunities import (
    MGS_REGISTRATION_URL,
    detect_affiliate_opportunities,
    mgs_product_page_metadata,
    normalize_affiliate_opportunities,
    registration_recommendations,
)
from indanya_desktop.workers import _apply_editorial_metadata
from article_studio import _SourcePageParser


def test_mgs_metadata_request_uses_the_public_page_without_rejected_accept_header() -> None:
    html = (
        '<meta property="og:title" content="「社内不倫の作品」：MGS動画">'
        '<meta property="og:image" '
        'content="https://image.mgstage.com/images/work/package.jpg">'
    ).encode("utf-8")

    class Headers:
        @staticmethod
        def get_content_charset() -> str:
            return "utf-8"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(_size: int) -> bytes:
            return html

    def opener(request, timeout: int):
        assert timeout == 25
        assert request.get_header("Accept") is None
        return Response()

    result = mgs_product_page_metadata(
        "https://www.mgstage.com/product/product_detail/300MIUM-1293/",
        opener=opener,
    )

    assert result["product_title"] == "社内不倫の作品"
    assert result["thumbnail_url"].endswith("/work/package.jpg")


def test_detects_exact_mgs_product_without_copying_source_affiliate_id() -> None:
    source = {
        "title": "MGSの新作AV",
        "affiliate_resources": [{
            "kind": "script",
            "url": (
                "https://static.mgstage.com/mgs/script/common/"
                "mgs_Widget_affiliate.js?c=SOURCE_OWNER_SECRET&p=300MIUM-1293"
            ),
        }],
    }

    result = detect_affiliate_opportunities(source)

    assert result == [{
        "program_id": "mgs",
        "program_name": "MGS動画",
        "network_name": "BannerBridge",
        "status": "registration_recommended",
        "registration_url": MGS_REGISTRATION_URL,
        "program_url": "https://www.mgstage.com/",
        "product_code": "300MIUM-1293",
        "product_url": (
            "https://www.mgstage.com/product/product_detail/300MIUM-1293/"
        ),
        "reason": "元ページ内のMGS商品導線を確認（記事本体との一致は未確認）",
        "evidence_type": "exact_product_widget",
        "confidence": 100,
        "article_match": False,
    }]
    assert "SOURCE_OWNER_SECRET" not in str(result)


def test_mgs_widget_title_proves_that_the_product_matches_the_article() -> None:
    source = {
        "title": "社内で一番の美人巨乳と中出し不倫…美巨乳を好き放題揉みしだき",
        "affiliate_resources": [{
            "kind": "script",
            "url": (
                "https://static.mgstage.com/mgs/script/common/"
                "mgs_Widget_affiliate.js?p=300MIUM-1293&"
                "s=%E3%80%90%E7%A4%BE%E5%86%85%E3%81%A7%E4%B8%80%E7%95%AA%E3%81%AE"
                "%E7%BE%8E%E4%BA%BA%E5%B7%A8%E4%B9%B3%E3%81%A8%E4%B8%AD%E5%87%BA%E3%81%97"
                "%E4%B8%8D%E5%80%AB%E3%80%91%E7%BE%8E%E5%B7%A8%E4%B9%B3%E3%82%92"
                "%E5%A5%BD%E3%81%8D%E6%94%BE%E9%A1%8C%E6%8F%89%E3%81%BF%E3%81%97%E3%81%A0%E3%81%8D"
            ),
        }],
    }

    result = detect_affiliate_opportunities(source)

    assert result[0]["article_match"] is True
    assert result[0]["product_code"] == "300MIUM-1293"
    assert "社内で一番の美人巨乳" in result[0]["product_title"]


def test_detects_official_mgs_product_link() -> None:
    result = detect_affiliate_opportunities({
        "links": [{
            "url": "https://www.mgstage.com/product/product_detail/300mium-1293/"
        }]
    })

    assert result[0]["product_code"] == "300MIUM-1293"
    assert result[0]["evidence_type"] == "official_product_url"
    assert result[0]["article_match"] is False


def test_direct_mgs_product_page_is_an_article_match() -> None:
    product_url = "https://www.mgstage.com/product/product_detail/300MIUM-1293/"
    result = detect_affiliate_opportunities({
        "requested_url": product_url,
        "links": [{"url": product_url}],
    })

    assert result[0]["article_match"] is True


def test_legacy_exact_mgs_opportunity_without_match_evidence_is_unconfirmed() -> None:
    result = normalize_affiliate_opportunities([{
        "program_id": "mgs",
        "product_code": "892OERO-006",
        "evidence_type": "official_product_url",
    }])

    assert result[0]["article_match"] is False


def test_exact_mgs_product_suppresses_generic_program_duplicate() -> None:
    result = detect_affiliate_opportunities({
        "affiliate_resources": [
            {"url": "https://static.mgstage.com/common.js"},
            {
                "url": (
                    "https://static.mgstage.com/mgs/script/common/"
                    "mgs_Widget_affiliate.js?p=300MIUM-1293"
                )
            },
        ]
    })

    assert len(result) == 1
    assert result[0]["product_code"] == "300MIUM-1293"


def test_static_page_parser_keeps_affiliate_resource_for_fast_capture() -> None:
    parser = _SourcePageParser()
    parser.feed(
        '<html><body><script src="https://static.mgstage.com/mgs/script/common/'
        'mgs_Widget_affiliate.js?c=SOURCE_OWNER&amp;p=300MIUM-1293"></script>'
        '</body></html>'
    )

    result = detect_affiliate_opportunities({
        "affiliate_resources": parser.affiliate_resources
    })

    assert result[0]["product_code"] == "300MIUM-1293"
    assert "SOURCE_OWNER" not in str(result)


def test_recommendations_group_articles_by_program() -> None:
    opportunities = detect_affiliate_opportunities({
        "affiliate_resources": [{
            "url": (
                "https://static.mgstage.com/mgs/script/common/"
                "mgs_Widget_affiliate.js?p=300MIUM-1293"
            )
        }]
    })
    result = registration_recommendations([
        {"slug": "article-a", "title": "A", "affiliate_opportunities": opportunities},
        {"slug": "article-b", "title": "B", "affiliate_opportunities": opportunities},
    ])

    assert len(result) == 1
    assert result[0]["article_count"] == 2
    assert result[0]["exact_product_count"] == 1
    assert result[0]["slugs"] == ["article-a", "article-b"]


def test_editorial_metadata_records_unregistered_program_and_removes_fake_ad() -> None:
    payload = {
        "tags": ["MGS", "不倫"],
        "blocks": [
            {"id": "response-1", "type": "response", "text": "本文"},
            {
                "id": "codex-ad",
                "type": "ad",
                "text": "記事内容に合う関連広告枠",
            },
        ],
    }
    source = {
        "affiliate_opportunities": detect_affiliate_opportunities({
            "affiliate_resources": [{
                "url": (
                    "https://static.mgstage.com/mgs/script/common/"
                    "mgs_Widget_affiliate.js?p=300MIUM-1293"
                )
            }]
        })
    }

    _apply_editorial_metadata(payload, source, {}, None)

    assert payload["affiliate_opportunities"][0]["product_code"] == "300MIUM-1293"
    assert payload["promotion_type"] == "organic"
    assert payload["affiliate_opportunities"][0]["article_match"] is False
    assert not any(
        block.get("url") == "https://www.mgstage.com/product/product_detail/300MIUM-1293/"
        for block in payload["blocks"]
        if isinstance(block, dict)
    )
