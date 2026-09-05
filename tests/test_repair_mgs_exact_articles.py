from __future__ import annotations

import base64
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from repair_mgs_exact_articles import repair_payload  # noqa: E402


def test_repairs_matching_mgs_widget_as_exact_official_work() -> None:
    jpg = base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
    )
    payload = {
        "slug": "article",
        "title": "【画像】社内で一番の美人巨乳と中出し不倫",
        "summary": "作品のサンプル画像",
        "source_url": "https://source.example/article",
        "images": [],
        "blocks": [{
            "id": "old-related",
            "type": "related_link",
            "link_kind": "inferred_topic_search",
            "url": "https://example.com/search",
            "title": "ランジェリー系の作品を探す",
        }],
        "affiliate_opportunities": [{
            "program_id": "mgs",
            "product_code": "300MIUM-1293",
            "evidence_type": "exact_product_widget",
        }],
    }
    html = """
    <html><head><meta property="og:title" content="社内で一番の美人巨乳と中出し不倫"></head>
    <body><script src="https://static.mgstage.com/mgs/script/common/mgs_Widget_affiliate.js?p=300MIUM-1293&amp;s=%E7%A4%BE%E5%86%85%E3%81%A7%E4%B8%80%E7%95%AA%E3%81%AE%E7%BE%8E%E4%BA%BA%E5%B7%A8%E4%B9%B3%E3%81%A8%E4%B8%AD%E5%87%BA%E3%81%97%E4%B8%8D%E5%80%AB"></script></body></html>
    """

    changed = repair_payload(
        payload,
        html,
        thumbnail_downloader=lambda _url: (jpg, ".jpg", "image/jpeg"),
        mgs_metadata_resolver=lambda _url: {
            "product_title": "社内で一番の美人巨乳と中出し不倫",
            "thumbnail_url": "https://image.mgstage.com/exact-package.jpg",
        },
    )

    assert changed is True
    exact = next(
        block for block in payload["blocks"]
        if isinstance(block, dict) and block.get("link_kind") == "exact_official_work"
    )
    assert exact["url"].endswith("/300MIUM-1293/")
    assert exact["title"] == "社内で一番の美人巨乳と中出し不倫"
    assert exact["thumbnail_image_id"].startswith("related-official-")
    assert not any(
        isinstance(block, dict) and block.get("link_kind") == "inferred_topic_search"
        for block in payload["blocks"]
    )
