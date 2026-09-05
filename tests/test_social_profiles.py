from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from indanya_desktop.social_profiles import (
    _rendered_profile_thumbnail_score,
    canonical_social_profile_url,
    enrich_source_profile_thumbnails,
    find_social_profile_record,
    merge_verified_social_profiles,
    registry_profiles_for_payload,
    resolve_performer_social_profiles,
    resolve_subject_social_profiles,
    upsert_social_profile_record,
    validate_social_verification,
)


def _verified_yanyan() -> dict[str, object]:
    return validate_social_verification(
        {
            "subject_name": "やんやん",
            "subject_role": "コスプレイヤー",
            "status": "verified",
            "confidence": 98,
            "profiles": [{
                "service": "x",
                "url": "https://twitter.com/yanyan_cos/status/123",
                "display_name": "やんやん",
                "thumbnail_url": "https://pbs.twimg.com/profile_images/yanyan.jpg",
            }],
            "evidence": [
                {
                    "url": "https://x.com/yanyan_cos",
                    "kind": "official_profile",
                    "claim": "本人名とコスプレ活動を確認",
                },
                {
                    "url": "https://linkfly.to/yanyancos",
                    "kind": "official_hub",
                    "claim": "本人のリンク集でcosplayer表記を確認",
                },
            ],
            "reason": "人物名、活動区分、公式Xが一致した",
        },
        "やんやん",
        "コスプレイヤー",
    )


def test_x_thumbnail_falls_back_to_verified_handle_avatar_proxy(monkeypatch) -> None:
    import indanya_desktop.social_profiles as module

    def blocked(*_args, **_kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(module.urllib.request, "urlopen", blocked)
    monkeypatch.setattr(module, "fetch_rendered_profile_thumbnail", lambda *_a, **_k: "")

    assert module.fetch_profile_thumbnail("https://x.com/kazame_kotori") == (
        "https://unavatar.io/x/kazame_kotori?fallback=false"
    )


def test_verification_requires_independent_evidence() -> None:
    result = validate_social_verification(
        {
            "subject_name": "同名さん",
            "subject_role": "配信者",
            "status": "verified",
            "confidence": 99,
            "profiles": [{
                "service": "x",
                "url": "https://x.com/same_name",
                "display_name": "同名さん",
            }],
            "evidence": [{
                "url": "https://x.com/same_name",
                "kind": "official_profile",
                "claim": "プロフィールを確認",
            }],
            "reason": "根拠が1系統だけ",
        },
        "同名さん",
        "配信者",
    )

    assert result["status"] == "ambiguous"
    assert result["profiles"] == []


def test_registry_reuses_verified_profile_without_calling_verifier(tmp_path: Path) -> None:
    upsert_social_profile_record(tmp_path, _verified_yanyan())
    called = False

    def verifier(_subject: dict[str, object]) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    source = {
        "ai_main_subject": {
            "name": "やんやん",
            "kind": "person",
            "role": "コスプレイヤー",
            "is_public_creator": True,
            "reason": "記事見出しに活動名を確認",
        },
        "ai_social_profiles": [],
    }
    result = resolve_subject_social_profiles(tmp_path, source, verifier)

    assert called is False
    assert result["verified_social_profiles"][0]["url"] == "https://x.com/yanyan_cos"
    assert result["identity_resolution"]["method"] == "verified_registry"


def test_named_fanza_performer_reuses_verified_social_profile(tmp_path: Path) -> None:
    upsert_social_profile_record(tmp_path, _verified_yanyan())
    called = False

    def verifier(_subject: dict[str, object]) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("verified performer must use the registry")

    source = resolve_performer_social_profiles(
        tmp_path,
        {
            "ai_main_subject": {"kind": "work", "name": "作品名"},
            "ai_fanza_people": [{"name": "やんやん", "reason": "出演者表記"}],
        },
        verifier=verifier,
    )

    assert called is False
    assert [item["url"] for item in source["verified_social_profiles"]] == [
        "https://x.com/yanyan_cos"
    ]
    assert source["performer_identity_resolution"]["resolved"] == 1


def test_registry_refreshes_only_profiles_missing_their_own_image(tmp_path: Path) -> None:
    record = _verified_yanyan()
    record["profiles"][0].pop("thumbnail_url", None)
    upsert_social_profile_record(tmp_path, record)
    calls = 0

    def verifier(_subject: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _verified_yanyan()

    source = {
        "ai_main_subject": {
            "name": "やんやん",
            "kind": "person",
            "role": "コスプレイヤー",
            "is_public_creator": True,
            "reason": "記事見出しに活動名を確認",
        },
        "ai_social_profiles": [],
    }
    result = resolve_subject_social_profiles(tmp_path, source, verifier)

    assert calls == 1
    assert result["verified_social_profiles"][0]["thumbnail_url"].endswith("yanyan.jpg")
    cached = find_social_profile_record(tmp_path, "やんやん")
    assert cached["verification_method"] == "codex_web_search_thumbnail_refresh"


def test_registry_profile_is_found_in_an_existing_article_title(tmp_path: Path) -> None:
    upsert_social_profile_record(tmp_path, _verified_yanyan())

    profiles = registry_profiles_for_payload(tmp_path, {
        "title": "【画像17枚】やんやん、競泳水着で見せるコスプレ",
        "tags": ["コスプレ", "競泳水着"],
    })

    assert [item["url"] for item in profiles] == ["https://x.com/yanyan_cos"]
    assert profiles[0]["role"] == "コスプレイヤー"


def test_direct_profile_is_merged_with_other_verified_registry_services(tmp_path: Path) -> None:
    record = _verified_yanyan()
    record["profiles"].extend([
        {
            "service": "instagram",
            "url": "https://www.instagram.com/yanyan_cos/",
            "display_name": "やんやん",
        },
        {
            "service": "tiktok",
            "url": "https://www.tiktok.com/@yanyan_cos",
            "display_name": "やんやん",
        },
    ])
    upsert_social_profile_record(tmp_path, record)
    direct = [{
        "name": "やんやん",
        "service": "x",
        "url": "https://x.com/yanyan_cos",
        "thumbnail_url": "https://pbs.twimg.com/profile_images/direct.jpg",
        "is_main_subject": True,
    }]

    merged = merge_verified_social_profiles(
        direct,
        registry_profiles_for_payload(tmp_path, {
            "title": "【画像】やんやんの新作コスプレ",
            "main_subject": {"name": "やんやん"},
        }),
    )

    assert [item["service"] for item in merged] == ["x", "instagram", "tiktok"]
    assert merged[0]["thumbnail_url"].endswith("direct.jpg")


def test_duplicate_registry_profile_keeps_thumbnail_from_richer_record(tmp_path: Path) -> None:
    base = _verified_yanyan()
    base["profiles"][0].pop("thumbnail_url", None)
    upsert_social_profile_record(tmp_path, base)
    richer = _verified_yanyan()
    richer["canonical_name"] = "やんやん（公式）"
    richer["aliases"] = ["やんやん（公式）"]
    upsert_social_profile_record(tmp_path, richer)

    profiles = registry_profiles_for_payload(tmp_path, {
        "title": "やんやん（公式）のコスプレ写真",
        "tags": ["やんやん"],
    })

    assert len([item for item in profiles if item["service"] == "x"]) == 1
    assert profiles[0]["thumbnail_url"].endswith("yanyan.jpg")


def test_share_and_content_urls_are_not_saved_as_profiles() -> None:
    assert canonical_social_profile_url("x", "https://x.com/intent/tweet") == ""
    assert canonical_social_profile_url("instagram", "https://instagram.com/p/abc/") == ""
    assert canonical_social_profile_url("youtube", "https://youtube.com/watch?v=abc") == ""


def test_registry_can_find_alias(tmp_path: Path) -> None:
    record = _verified_yanyan()
    record["aliases"] = ["やんやん", "YANYAN"]
    upsert_social_profile_record(tmp_path, record)

    assert find_social_profile_record(tmp_path, "yanyan")["canonical_name"] == "やんやん"


def test_myfans_and_fantia_profiles_are_canonicalized() -> None:
    assert canonical_social_profile_url("myfans", "https://www.myfans.jp/c/example?from=x") == "https://myfans.jp/c/example"
    assert canonical_social_profile_url("fantia", "https://fantia.jp/fanclubs/12345/posts") == "https://fantia.jp/fanclubs/12345"


def test_profile_thumbnail_is_kept_with_its_destination(tmp_path: Path) -> None:
    record = _verified_yanyan()
    upsert_social_profile_record(tmp_path, record)
    source = {
        "x_info": {
            "username": "yanyan_cos",
            "profile_image_url": "https://pbs.twimg.com/profile_images/yanyan.jpg",
        },
        "verified_social_profiles": registry_profiles_for_payload(tmp_path, {
            "main_subject": {"name": "やんやん"},
        }),
    }

    enrich_source_profile_thumbnails(tmp_path, source, fetcher=lambda _url: "")

    profile = source["verified_social_profiles"][0]
    assert profile["thumbnail_url"] == "https://pbs.twimg.com/profile_images/yanyan.jpg"
    cached = find_social_profile_record(tmp_path, "やんやん")
    assert cached["profiles"][0]["thumbnail_url"] == profile["thumbnail_url"]


def test_official_hub_image_is_used_when_profile_page_has_no_image(tmp_path: Path) -> None:
    record = _verified_yanyan()
    record["profiles"] = [{
        "service": "instagram",
        "url": "https://www.instagram.com/yanyan_cos/",
        "display_name": "やんやん",
    }]
    record["evidence"].insert(0, {
        "url": "https://linktr.ee/yanyan_cos",
        "kind": "official_hub",
        "claim": "本人の公式リンク集",
    })
    upsert_social_profile_record(tmp_path, record)
    source = {
        "verified_social_profiles": registry_profiles_for_payload(tmp_path, {
            "main_subject": {"name": "やんやん"},
        }),
    }

    enrich_source_profile_thumbnails(
        tmp_path,
        source,
        fetcher=lambda url: (
            "https://linktr.ee/og/image/yanyan_cos.jpg"
            if "linktr.ee" in url else ""
        ),
    )

    profile = source["verified_social_profiles"][0]
    assert profile["thumbnail_source_kind"] == "official_hub_profile"
    assert profile["thumbnail_owner_url"] == "https://linktr.ee/yanyan_cos"
    assert profile["thumbnail_url"].endswith("yanyan_cos.jpg")


def test_instagram_profile_photo_beats_post_and_highlight_images() -> None:
    profile = {
        "src": "https://scontent-nrt.cdninstagram.com/v/t51.75761-19/avatar.jpg",
        "alt": "momota_mitsukiのプロフィール写真",
        "width": 150,
        "height": 150,
    }
    post = {
        "src": "https://scontent-nrt.cdninstagram.com/v/t51.82787-15/post.jpg",
        "alt": "Photo by 百田光稀 Mitsuki Momota",
        "width": 480,
        "height": 640,
    }
    highlight = {
        "src": "https://scontent-nrt.cdninstagram.com/v/t51.71878-15/highlight.jpg",
        "alt": "momota_mitsukiのストーリーズ写真のハイライト",
        "width": 150,
        "height": 150,
    }

    profile_score = _rendered_profile_thumbnail_score("instagram", "momota_mitsuki", profile)
    assert profile_score > _rendered_profile_thumbnail_score("instagram", "momota_mitsuki", post)
    assert profile_score > _rendered_profile_thumbnail_score("instagram", "momota_mitsuki", highlight)
