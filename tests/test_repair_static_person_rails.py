from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.social_profiles import save_social_profile_registry  # noqa: E402
from repair_static_person_rails import (  # noqa: E402
    migrate_article_html,
    migrate_site_person_rails,
)


def _legacy_card(
    kind: str,
    title: str,
    url: str,
    *,
    image: str = "",
    confidence: int = 95,
) -> str:
    classes = "article-destination fanza-product" if kind == "verified_person_search" else "article-destination"
    thumbnail = (
        f'<img class="fanza-product-thumb" src="{image}" alt="{title}">' if image else ""
    )
    return (
        f'<aside class="{classes}" data-link-kind="{kind}" '
        f'data-link-confidence="{confidence}">'
        f'<div class="fanza-product-media">{thumbnail}'
        '<div class="fanza-product-content">'
        f'<p class="fanza-product-title">{title}</p>'
        f'<a class="article-destination-button" href="{url}">開く</a>'
        '</div></div></aside>'
    )


def _site_with_registry(tmp_path: Path) -> Path:
    (tmp_path / "articles").mkdir(parents=True)
    save_social_profile_registry(tmp_path, {
        "people": [{
            "canonical_name": "石原希望",
            "aliases": ["石原希望"],
            "role": "AV女優",
            "status": "verified",
            "confidence": 100,
            "profiles": [
                {"service": "x", "url": "https://x.com/Nozomi_Ishihara"},
                {
                    "service": "instagram",
                    "url": "https://www.instagram.com/nozomi_ishihara.official/",
                },
            ],
        }],
    })
    return tmp_path


def test_legacy_links_are_grouped_by_person_and_use_profile_thumbnail(
    tmp_path: Path,
) -> None:
    site_root = _site_with_registry(tmp_path)
    source = "".join([
        '<html><body><div class="thread"><p>本文</p>',
        _legacy_card(
            "verified_person_search",
            "石原希望の出演作品",
            "https://al.dmm.com/?lurl=performer",
            image="../assets/articles/sample/package.jpg",
            confidence=85,
        ),
        _legacy_card(
            "official_profile",
            "AV作品のX",
            "https://x.com/Nozomi_Ishihara",
            image="../assets/articles/sample/x-profile.jpg",
        ),
        _legacy_card(
            "official_profile",
            "AV作品のInstagram",
            "https://www.instagram.com/nozomi_ishihara.official/",
            image="../assets/articles/sample/instagram-profile.jpg",
        ),
        _legacy_card(
            "official_profile",
            "紹介した人物のX",
            "https://x.com/kimimo_purin",
            image="../assets/articles/sample/other-profile.jpg",
        ),
        '<div class="editorial-note">注記</div></div></body></html>',
    ])

    updated, detail = migrate_article_html(source, site_root)

    assert detail["legacy_cards"] == 4
    assert detail["person_cards"] == 2
    assert updated.count('class="person-discovery"') == 1
    assert updated.count('class="person-discovery-card"') == 2
    assert "<strong>石原希望</strong>" in updated
    assert "<strong>@kimimo_purin</strong>" in updated
    assert ">X</a>" in updated
    assert ">Instagram</a>" in updated
    assert ">FANZA出演作</a>" in updated
    assert "x-profile.jpg" in updated
    assert "package.jpg" not in updated
    assert 'data-link-kind="official_profile"' not in updated
    assert 'data-link-kind="verified_person_search"' not in updated

    second, second_detail = migrate_article_html(updated, site_root)
    assert second == updated
    assert second_detail["legacy_cards"] == 0


def test_site_migration_dry_run_and_apply_are_reported(tmp_path: Path) -> None:
    site_root = _site_with_registry(tmp_path)
    article = site_root / "articles" / "sample-article.html"
    article.write_text(
        '<div class="thread">'
        + _legacy_card(
            "official_profile",
            "石原希望のX",
            "https://x.com/Nozomi_Ishihara",
            image="../assets/articles/sample/x-profile.jpg",
        )
        + '</div>',
        encoding="utf-8",
    )

    dry_run = migrate_site_person_rails(site_root)
    assert dry_run["articles_changed"] == 1
    assert 'class="article-destination"' in article.read_text(encoding="utf-8")

    applied = migrate_site_person_rails(site_root, apply=True)
    assert applied["articles_changed"] == 1
    rendered = article.read_text(encoding="utf-8")
    assert 'class="person-discovery"' in rendered
    assert 'class="article-destination"' not in rendered
