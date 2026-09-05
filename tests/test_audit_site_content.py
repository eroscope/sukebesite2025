from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from audit_site_content import audit_site  # noqa: E402


def _write_draft(site_root: Path, name: str, payload: dict[str, object]) -> None:
    draft_root = site_root / ".article-studio" / "drafts"
    draft_root.mkdir(parents=True, exist_ok=True)
    (draft_root / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _payload(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "title": "Valid article title",
        "quality_gate": {"score": 90, "blockers": [], "warnings": []},
        "images": [],
        "blocks": [],
    }


def test_audit_ignores_backup_json_files(tmp_path: Path) -> None:
    slug = "valid-article"
    _write_draft(tmp_path, f"{slug}.json", _payload(slug))
    _write_draft(
        tmp_path,
        f"{slug}.before-source-fix.json",
        {"slug": "backup", "quality_gate": {"blockers": ["stale"]}},
    )
    article_root = tmp_path / "articles"
    article_root.mkdir()
    (article_root / f"{slug}.html").write_text("<html></html>", encoding="utf-8")

    result = audit_site(tmp_path)

    assert result["drafts"] == 1
    assert result["critical_issues"] == 0


def test_audit_does_not_require_html_for_an_unpublished_draft(tmp_path: Path) -> None:
    slug = "unpublished-draft"
    payload = _payload(slug)
    payload["status"] = "draft"
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert "missing_article_html" not in result["issues"]


def test_audit_ignores_a_removed_draft_even_if_legacy_status_is_published(
    tmp_path: Path,
) -> None:
    slug = "removed-draft"
    payload = _payload(slug)
    payload["status"] = "published"
    payload["review_status"] = "deleted"
    payload["editorial_status"] = "removed_non_adult"
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert result["critical_issues"] == 0
    assert "missing_article_html" not in result["issues"]


def test_audit_checks_official_content_and_duplicate_accounts(tmp_path: Path) -> None:
    slug = "duplicate-account"
    payload = _payload(slug)
    payload["blocks"] = [
        {
            "type": "related_link",
            "link_kind": "official_content",
            "provider": "youtube",
            "url": "https://www.youtube.com/@creator",
        },
        {
            "type": "related_link",
            "link_kind": "official_content",
            "provider": "youtube",
            "url": "https://www.youtube.com/@creator",
        },
    ]
    _write_draft(tmp_path, f"{slug}.json", payload)
    article_root = tmp_path / "articles"
    article_root.mkdir()
    (article_root / f"{slug}.html").write_text("<html></html>", encoding="utf-8")

    result = audit_site(tmp_path)

    assert result["issues"]["profile_without_local_thumbnail"] == 2
    assert result["issues"]["duplicate_official_account_url"] == 1
    assert result["critical_issues"] == 3


def test_audit_rejects_unverified_mgs_links_in_draft_and_rendered_html(
    tmp_path: Path,
) -> None:
    slug = "wrong-mgs-work"
    wrong_url = "https://www.mgstage.com/product/product_detail/892OERO-006/"
    payload = _payload(slug)
    payload["source_url"] = "https://bakufu.jp/archives/1172727"
    payload["affiliate_opportunities"] = [{
        "program_id": "mgs",
        "product_code": "892OERO-006",
        "product_url": wrong_url,
    }]
    payload["blocks"] = [{
        "type": "related_link",
        "link_kind": "exact_official_work",
        "url": wrong_url,
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)
    article_root = tmp_path / "articles"
    article_root.mkdir()
    (article_root / f"{slug}.html").write_text(
        f'<html><a href="{wrong_url}">wrong</a></html>', encoding="utf-8"
    )

    result = audit_site(tmp_path)

    assert result["issues"]["unverified_mgs_product"] == 1
    assert result["issues"]["rendered_unverified_mgs_product"] == 1


def test_audit_accepts_verified_mgs_article_match(tmp_path: Path) -> None:
    slug = "verified-mgs-work"
    product_url = "https://www.mgstage.com/product/product_detail/892OERO-006/"
    payload = _payload(slug)
    payload["affiliate_opportunities"] = [{
        "program_id": "mgs",
        "product_code": "892OERO-006",
        "product_url": product_url,
        "article_match": True,
    }]
    payload["blocks"] = [{
        "type": "related_link",
        "link_kind": "exact_official_work",
        "url": product_url,
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)
    article_root = tmp_path / "articles"
    article_root.mkdir()
    (article_root / f"{slug}.html").write_text(
        f'<html><a href="{product_url}">verified</a></html>', encoding="utf-8"
    )

    result = audit_site(tmp_path)

    assert "unverified_mgs_product" not in result["issues"]
    assert "rendered_unverified_mgs_product" not in result["issues"]


def test_audit_rejects_article_image_on_exact_official_work(tmp_path: Path) -> None:
    slug = "wrong-official-thumbnail"
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = _payload(slug)
    payload["images"] = [{"id": "article-image", "rights_basis": "article_source"}]
    payload["blocks"] = [{
        "type": "related_link",
        "link_kind": "exact_official_work",
        "url": page_url,
        "thumbnail_image_id": "article-image",
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert result["issues"]["invalid_official_work_thumbnail"] == 1


def test_audit_accepts_owned_official_page_thumbnail(tmp_path: Path) -> None:
    slug = "valid-official-thumbnail"
    page_url = "https://publisher.example.com/comics/exact-work/"
    payload = _payload(slug)
    payload["images"] = [{
        "id": "official-image",
        "related_thumbnail_only": True,
        "rights_basis": "official_page_thumbnail",
        "thumbnail_owner_url": page_url,
    }]
    payload["blocks"] = [{
        "type": "related_link",
        "link_kind": "exact_official_work",
        "url": page_url,
        "thumbnail_image_id": "official-image",
        "thumbnail_source_kind": "official_page",
        "thumbnail_owner_url": page_url,
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert "invalid_official_work_thumbnail" not in result["issues"]


def test_audit_accepts_local_fanza_package_for_exact_product(tmp_path: Path) -> None:
    slug = "direct-fanza-product"
    product_url = "https://video.dmm.co.jp/av/content/?id=jur00071"
    payload = _payload(slug)
    payload["source_url"] = product_url
    payload["images"] = [{
        "id": "package",
        "source_url": (
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/"
            "jur00071/jur00071pl.jpg"
        ),
    }]
    payload["blocks"] = [{
        "type": "product_cta",
        "url": product_url,
        "match_type": "exact_video",
        "thumbnail_image_id": "package",
        "thumbnail_source_kind": "fanza_package",
        "thumbnail_owner_url": product_url,
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert "invalid_exact_product_thumbnail" not in result["issues"]
    assert "direct_fanza_exact_product_card_count" not in result["issues"]


def test_audit_rejects_generic_card_on_direct_fanza_product(tmp_path: Path) -> None:
    slug = "direct-fanza-generic"
    payload = _payload(slug)
    payload["source_url"] = "https://video.dmm.co.jp/av/content/?id=jur00071"
    payload["blocks"] = [{
        "type": "product_cta",
        "url": "https://www.dmm.co.jp/search/=/searchstr=人妻/",
        "match_type": "inferred_topic_search",
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert result["issues"]["direct_fanza_exact_product_card_count"] == 1
    assert result["issues"]["direct_fanza_inferred_product_card"] == 1


def test_audit_requires_topic_recommendation_to_be_a_real_packaged_product(
    tmp_path: Path,
) -> None:
    slug = "unresolved-topic-card"
    payload = _payload(slug)
    payload["blocks"] = [{
        "type": "related_link",
        "url": "https://www.dmm.co.jp/search/?searchstr=%E6%B0%B4%E7%9D%80",
        "link_kind": "inferred_topic_search",
        "search_query": "水着",
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert result["issues"]["unresolved_related_fanza_product"] == 1


def test_audit_accepts_topic_product_only_with_its_own_package(tmp_path: Path) -> None:
    slug = "resolved-topic-card"
    product_url = "https://video.dmm.co.jp/av/content/?id=swim001"
    payload = _payload(slug)
    payload["images"] = [{
        "id": "related-product",
        "source_url": (
            "https://pics.dmm.co.jp/digital/video/swim001/swim001pl.jpg"
        ),
        "related_thumbnail_only": True,
    }]
    payload["blocks"] = [{
        "type": "related_link",
        "url": product_url,
        "link_kind": "inferred_topic_product",
        "search_query": "水着",
        "thumbnail_image_id": "related-product",
        "thumbnail_source_kind": "fanza_package",
        "thumbnail_owner_url": product_url,
    }]
    _write_draft(tmp_path, f"{slug}.json", payload)

    result = audit_site(tmp_path)

    assert "invalid_related_fanza_product_thumbnail" not in result["issues"]


def test_audit_requires_a_verified_performer_card_package(tmp_path: Path) -> None:
    payload = _payload("performer-without-package")
    payload["blocks"] = [{
        "type": "related_link",
        "url": "https://video.dmm.co.jp/av/list/?actress=12345",
        "link_kind": "verified_person_search",
        "person_name": "出演者A",
    }]
    _write_draft(tmp_path, "performer-without-package.json", payload)

    result = audit_site(tmp_path)

    assert result["issues"]["invalid_performer_card_thumbnail"] == 1


def test_audit_accepts_a_performer_sample_with_its_real_package(tmp_path: Path) -> None:
    product_url = "https://video.dmm.co.jp/av/content/?id=sample001"
    payload = _payload("performer-with-package")
    payload["images"] = [{
        "id": "sample-package",
        "source_url": (
            "https://pics.dmm.co.jp/digital/video/sample001/sample001pl.jpg"
        ),
        "thumbnail_owner_url": product_url,
        "related_thumbnail_only": True,
    }]
    payload["blocks"] = [{
        "type": "related_link",
        "url": "https://video.dmm.co.jp/av/list/?actress=12345",
        "link_kind": "verified_person_search",
        "person_name": "出演者A",
        "thumbnail_image_id": "sample-package",
        "thumbnail_source_kind": "fanza_performer_sample",
        "thumbnail_owner_url": product_url,
        "sample_product_url": product_url,
    }]
    _write_draft(tmp_path, "performer-with-package.json", payload)

    result = audit_site(tmp_path)

    assert "invalid_performer_card_thumbnail" not in result["issues"]
