from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from indanya_desktop.affiliate_opportunities import (
    mgs_product_code_from_url,
    normalize_affiliate_opportunities,
)
from indanya_desktop.social_profiles import canonical_social_profile_url


_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
_X_RESERVED_ROUTES = {
    "about",
    "compose",
    "download",
    "explore",
    "hashtag",
    "home",
    "i",
    "intent",
    "login",
    "messages",
    "notifications",
    "privacy",
    "search",
    "settings",
    "share",
    "signup",
    "tos",
}
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}
_TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
_MYFANS_HOSTS = {"myfans.jp", "www.myfans.jp"}
_FANTIA_HOSTS = {"fantia.jp", "www.fantia.jp"}
_GENERIC_TAGS = {
    "18禁",
    "adult",
    "av",
    "fanza",
    "mgs",
    "pr",
    "sns",
    "x",
    "まとめ",
    "動画",
    "成人向け",
    "未分類",
    "画像",
    "話題",
}
_EMPTY_RELATED_AD_TEXTS = {
    "関連広告枠",
    "記事内容に合う関連広告枠",
}
_FOOTER_RECOMMENDATION_KINDS = {
    "inferred_topic_product",
    "inferred_topic_search",
    "person_search",
    "verified_person_search",
}
_OFFICIAL_ACCOUNT_KINDS = {"official_profile", "official_content"}
_FOOTER_PROFILE_ID_PREFIX = "article-related-footer-profile-"
_FOOTER_PRODUCT_ID = "article-related-footer-product"
_RELATED_FOOTER_VERSION = 9

_PUBLIC_PERSON_ROLE_TERMS = (
    "av女優", "av出演者", "セクシー女優", "fanza作品の出演者",
    "グラビア", "グラドル", "アイドル", "女優", "俳優", "声優",
    "アナウンサー", "キャスター", "モデル", "コスプレイヤー",
    "youtuber", "tiktoker", "配信者", "インフルエンサー",
)
_PRIVATE_PERSON_ROLE_TERMS = (
    "自称", "奥さん", "妻", "一般人", "素人", "店員として紹介",
    "記事の被写体", "登場人物", "ヒロイン", "キャラクター",
)

# Unknown articles must never turn arbitrary tags (especially person names) into
# a product-looking FANZA query.  Only these editorially safe genre concepts may
# be used for an inferred recommendation.  Longer labels come first so
# "competition swimsuit" is not reduced to the broader "swimsuit" label.
_INFERRED_FANZA_TOPICS = (
    "マイクロビキニ", "競泳水着", "スクール水着", "バニーガール", "レースクイーン",
    "セーラー服", "ブルマ", "ランジェリー", "下着", "水着", "ビキニ", "制服",
    "メイド", "ナース", "スーツ", "コスプレ", "バニー", "チャイナドレス", "パンスト",
    "タイツ", "ストッキング", "パンツ", "パンチラ", "巨乳", "爆乳", "美乳", "貧乳",
    "下乳", "乳首", "乳輪", "胸元", "尻", "美尻", "太もも", "脚",
    "人妻", "熟女", "女子大生", "女教師", "女上司", "OL", "素人", "グラドル",
    "アイドル", "痴女", "ギャル", "幼馴染", "後輩", "先輩", "秘書", "保育士",
    "デリヘル", "ソープランド", "メンズエステ", "エステ", "マッサージ", "温泉", "混浴",
    "ホテル", "オフィス", "部屋着", "同棲", "寝取られ", "NTR", "不倫", "ハーレム",
    "逆3P", "3P", "4P", "複数プレイ", "乱交", "拘束", "緊縛", "露出", "盗撮",
    "ハメ撮り", "主観", "8KVR", "3DVR", "VR", "ASMR", "濃厚キス", "キス",
    "フェラチオ", "フェラ", "パイズリ", "手コキ", "中出し", "顔射", "騎乗位",
    "後背位", "バック", "開脚", "オナニー", "セックス", "性交", "レズ", "アナル",
    "ぶっかけ", "口内射精", "クンニ", "AI", "口淫", "眼鏡", "黒髪",
    "ショートヘア", "ロングヘア", "美少女", "美女",
)
_INFERRED_FANZA_TOPIC_KEYS = tuple(
    (re.sub(r"[\s_-]+", "", label).casefold(), label)
    for label in _INFERRED_FANZA_TOPICS
)
_FANZA_MONTHLY_RANKING_URL = (
    "https://www.dmm.co.jp/digital/videoa/-/ranking/=/term=monthly/"
)


def _clean_text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_public_url(value: Any) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return candidate


def _source_urls(source: dict[str, Any]) -> list[str]:
    values = [
        source.get("requested_url"),
        source.get("url"),
        source.get("canonical_url"),
        source.get("profile_url"),
        source.get("author_url"),
        source.get("creator_url"),
    ]
    return list(dict.fromkeys(
        url for value in values if (url := _safe_public_url(value))
    ))


def _x_username_from_url(value: Any) -> str:
    candidate = _safe_public_url(value)
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if (parsed.hostname or "").casefold() not in _X_HOSTS:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    username = parts[0]
    if username.casefold() in _X_RESERVED_ROUTES:
        return ""
    return username if re.fullmatch(r"[A-Za-z0-9_]{1,15}", username) else ""


def _x_profile_url(source: dict[str, Any]) -> str:
    username = _clean_text((source.get("x_info") or {}).get("username"), 30).lstrip("@")
    if (
        username
        and username.casefold() not in _X_RESERVED_ROUTES
        and re.fullmatch(r"[A-Za-z0-9_]{1,15}", username)
    ):
        return f"https://x.com/{username}"
    for candidate in _source_urls(source):
        username = _x_username_from_url(candidate)
        if username:
            return f"https://x.com/{username}"
    return ""


def _youtube_source_url(source: dict[str, Any]) -> str:
    for candidate in _source_urls(source):
        if (urlparse(candidate).hostname or "").casefold() in _YOUTUBE_HOSTS:
            return candidate
    return ""


def _tiktok_profile_url(source: dict[str, Any]) -> str:
    for candidate in _source_urls(source):
        parsed = urlparse(candidate)
        if (parsed.hostname or "").casefold() not in _TIKTOK_HOSTS:
            continue
        match = re.match(r"^/(@[A-Za-z0-9_.]{2,30})", parsed.path)
        if match:
            return f"https://www.tiktok.com/{match.group(1)}"
    return ""


def _instagram_profile_url(source: dict[str, Any]) -> str:
    for candidate in _source_urls(source):
        parsed = urlparse(candidate)
        if (parsed.hostname or "").casefold() not in _INSTAGRAM_HOSTS:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parts
            and parts[0].casefold() not in {"p", "reel", "reels", "stories", "explore"}
            and re.fullmatch(r"[A-Za-z0-9_.]{2,30}", parts[0])
        ):
            return f"https://www.instagram.com/{parts[0]}/"
    return ""


def _instagram_content_url(source: dict[str, Any]) -> str:
    candidates = [*_source_urls(source)]
    candidates.extend(
        _safe_public_url(item.get("url"))
        for item in (source.get("links") or [])
        if isinstance(item, dict)
    )
    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if (parsed.hostname or "").casefold() not in _INSTAGRAM_HOSTS:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0].casefold() in {"p", "reel", "reels"}:
            kind = "reel" if parts[0].casefold() in {"reel", "reels"} else "p"
            return f"https://www.instagram.com/{kind}/{parts[1]}/"
    return ""


def _verified_social_links(source: dict[str, Any]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for item in source.get("links") or []:
        if not isinstance(item, dict):
            continue
        candidate = _safe_public_url(item.get("url"))
        if not candidate:
            continue
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold()
        label = _clean_text(item.get("text"), 120).casefold()
        if host in _X_HOSTS:
            parts = [part for part in parsed.path.split("/") if part]
            username = _x_username_from_url(candidate)
            is_profile_path = (
                len(parts) == 1
                and bool(username)
            )
            if (
                username
                and ("公式" in label or "x" == label or "twitter" in label or is_profile_path)
            ):
                results.append((f"https://x.com/{username}", "x"))
        if host in _YOUTUBE_HOSTS and (
            "公式" in label or "youtube" in label or "/@" in parsed.path
            or "/channel/" in parsed.path or "/user/" in parsed.path
        ):
            results.append((candidate, "youtube"))
        if host in _TIKTOK_HOSTS and (
            "公式" in label or "tiktok" in label or parsed.path.startswith("/@")
        ):
            match = re.match(r"^/(@[A-Za-z0-9_.]{2,30})", parsed.path)
            if match:
                results.append((f"https://www.tiktok.com/{match.group(1)}", "tiktok"))
        if host in _INSTAGRAM_HOSTS:
            parts = [part for part in parsed.path.split("/") if part]
            if (
                parts
                and parts[0].casefold() not in {"p", "reel", "reels", "stories", "explore"}
                and (
                    "公式" in label
                    or "instagram" in label
                    or "インスタ" in label
                    or len(parts) == 1
                )
                and re.fullmatch(r"[A-Za-z0-9_.]{2,30}", parts[0])
            ):
                results.append((f"https://www.instagram.com/{parts[0]}/", "instagram"))
        if host in _MYFANS_HOSTS:
            profile = canonical_social_profile_url("myfans", candidate)
            if profile and ("公式" in label or "myfans" in label or len(parsed.path.strip("/").split("/")) <= 2):
                results.append((profile, "myfans"))
        if host in _FANTIA_HOSTS:
            profile = canonical_social_profile_url("fantia", candidate)
            if profile and ("公式" in label or "fantia" in label or "ファンティア" in label):
                results.append((profile, "fantia"))
    deduplicated: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, provider in results:
        if url in seen:
            continue
        seen.add(url)
        deduplicated.append((url, provider))
    return deduplicated


def sanitize_related_destinations(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop unverified product cards and social navigation mislabeled as content."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return payload

    matched_mgs_codes = {
        str(item.get("product_code") or "").upper()
        for item in normalize_affiliate_opportunities(
            payload.get("affiliate_opportunities")
        )
        if item.get("article_match") is True and item.get("product_code")
    }
    direct_mgs_code = mgs_product_code_from_url(payload.get("source_url"))
    if direct_mgs_code:
        matched_mgs_codes.add(direct_mgs_code)

    removed_urls: set[str] = set()
    changed_blocks = False
    images_by_id = {
        str(item.get("id") or ""): item
        for item in payload.get("images") or []
        if isinstance(item, dict) and item.get("id")
    }
    sanitized_blocks: list[Any] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            sanitized_blocks.append(block)
            continue
        url = _safe_public_url(block.get("url"))
        parsed = urlparse(url) if url else None
        is_x_link = bool(
            parsed and (parsed.hostname or "").casefold() in _X_HOSTS
        )
        is_official_social = str(block.get("link_kind") or "") in {
            "official_profile",
            "official_content",
        }
        mgs_code = mgs_product_code_from_url(url)
        if mgs_code and mgs_code not in matched_mgs_codes:
            removed_urls.add(url)
            continue
        if is_x_link and is_official_social and not _x_username_from_url(url):
            removed_urls.add(url)
            continue
        if str(block.get("link_kind") or "") == "exact_official_work":
            thumbnail_id = str(block.get("thumbnail_image_id") or "")
            thumbnail = images_by_id.get(thumbnail_id)
            local_thumbnail_valid = bool(
                isinstance(thumbnail, dict)
                and thumbnail.get("related_thumbnail_only") is True
                and str(thumbnail.get("rights_basis") or "")
                == "official_page_thumbnail"
                and str(thumbnail.get("thumbnail_owner_url") or "").rstrip("/")
                == url.rstrip("/")
            )
            remote_thumbnail_valid = bool(
                block.get("thumbnail_url")
                and str(block.get("thumbnail_source_kind") or "")
                == "official_page"
                and str(block.get("thumbnail_owner_url") or "").rstrip("/")
                == url.rstrip("/")
            )
            if not (local_thumbnail_valid or remote_thumbnail_valid):
                block = dict(block)
                block.pop("thumbnail_image_id", None)
                block.pop("thumbnail_url", None)
                block.pop("thumbnail_source_kind", None)
                block.pop("thumbnail_owner_url", None)
                changed_blocks = True
        sanitized_blocks.append(block)

    if not removed_urls and not changed_blocks:
        return payload

    destinations = payload.get("related_destinations")
    sanitized_destinations = (
        [
            item for item in destinations
            if not isinstance(item, dict)
            or str(item.get("url") or "") not in removed_urls
        ]
        if isinstance(destinations, list)
        else destinations
    )
    result = {**payload, "blocks": sanitized_blocks}
    if isinstance(destinations, list):
        result["related_destinations"] = sanitized_destinations
    return result


def _verified_social_link(source: dict[str, Any]) -> tuple[str, str]:
    links = _verified_social_links(source)
    return links[0] if links else ("", "")


def _analysis_social_links(
    source: dict[str, Any],
) -> list[tuple[str, str, str, str, str, int]]:
    results: list[tuple[str, str, str, str, str, int]] = []
    raw_profiles = [
        *(source.get("ai_social_profiles") or []),
        *(source.get("verified_social_profiles") or []),
    ]
    for item in raw_profiles:
        if not isinstance(item, dict) or item.get("is_main_subject") is not True:
            continue
        name = _clean_text(item.get("name"), 80)
        provider = _clean_text(item.get("service"), 20).casefold()
        url = _safe_public_url(item.get("url"))
        evidence = _clean_text(item.get("reason"), 240) or "本人の公式アカウントを確認"
        try:
            confidence = max(0, min(100, int(item.get("confidence") or 95)))
        except (TypeError, ValueError):
            confidence = 95
        if not name or not url:
            continue
        if provider == "x":
            username = _x_username_from_url(url)
            if username:
                results.append((
                    f"https://x.com/{username}", provider, name, "official_profile",
                    evidence, confidence,
                ))
        elif provider == "tiktok":
            profile = _tiktok_profile_url({"url": url})
            if profile:
                results.append((profile, provider, name, "official_profile", evidence, confidence))
        elif provider == "instagram":
            profile = _instagram_profile_url({"url": url})
            if profile:
                results.append((profile, provider, name, "official_profile", evidence, confidence))
                continue
            content = _instagram_content_url({"url": url})
            if content:
                results.append((content, provider, name, "official_content", evidence, confidence))
        elif provider == "youtube" and (
            (urlparse(url).hostname or "").casefold() in _YOUTUBE_HOSTS
        ):
            results.append((url, provider, name, "official_content", evidence, confidence))
        elif provider in {"myfans", "fantia"}:
            profile = canonical_social_profile_url(provider, url)
            if profile:
                results.append((profile, provider, name, "official_profile", evidence, confidence))
    return results


def _profile_thumbnail_for_url(
    source: dict[str, Any], profile_url: str
) -> tuple[str, str, str]:
    target = profile_url.rstrip("/")
    for item in [
        *(source.get("ai_social_profiles") or []),
        *(source.get("verified_social_profiles") or []),
    ]:
        if not isinstance(item, dict):
            continue
        service = _clean_text(item.get("service"), 20).casefold()
        item_url = canonical_social_profile_url(service, item.get("url"))
        if item_url.rstrip("/") != target:
            continue
        thumbnail = _safe_public_url(item.get("thumbnail_url"))
        if thumbnail:
            return (
                thumbnail,
                _clean_text(item.get("thumbnail_source_kind"), 40) or "profile",
                _safe_public_url(item.get("thumbnail_owner_url")) or profile_url,
            )
    username = _x_username_from_url(profile_url)
    x_info = source.get("x_info") if isinstance(source.get("x_info"), dict) else {}
    source_username = _clean_text(x_info.get("username"), 30).lstrip("@").casefold()
    if username and (not source_username or username.casefold() == source_username):
        thumbnail = _safe_public_url(x_info.get("profile_image_url"))
        if thumbnail:
            return thumbnail, "profile", profile_url
    return "", "", ""


def _verified_performer_name(source: dict[str, Any]) -> str:
    for item in source.get("ai_fanza_people") or source.get("fanza_people") or []:
        if isinstance(item, dict):
            name = _clean_text(item.get("name"), 60)
            if name:
                return name
    explicit = _clean_text(source.get("ai_fanza_performer_name"), 60)
    if explicit:
        return explicit
    subject = source.get("ai_main_subject")
    if not isinstance(subject, dict):
        return ""
    role = _clean_text(subject.get("role"), 80).casefold()
    if any(term in role for term in ("av女優", "av actress", "出演者", "セクシー女優")):
        return _clean_text(subject.get("name"), 60)
    return ""


def _named_creator(source: dict[str, Any], payload: dict[str, Any]) -> str:
    del source
    subject = payload.get("main_subject")
    if not isinstance(subject, dict):
        return ""
    if (
        subject.get("is_public_creator") is not True
        and person_destination_mode(payload) != "official_search"
    ):
        return ""
    kind = _clean_text(subject.get("kind"), 30).casefold()
    if kind in {"", "group", "unknown", "anonymous", "unidentified"}:
        return ""
    name = _clean_text(subject.get("name"), 60)
    if (
        name
        and len(name) >= 2
        and name.casefold() not in _GENERIC_TAGS
        and name.casefold() not in {"admin", "管理人", "名無し"}
    ):
        return name
    return ""


def _person_search_matches_creator(block: dict[str, Any], creator: str) -> bool:
    creator_key = re.sub(r"[\s_-]+", "", creator).casefold()
    if not creator_key:
        return False
    haystack = unquote(
        " ".join(
            _clean_text(block.get(key), 500)
            for key in ("title", "url", "match_evidence")
        )
    )
    haystack_key = re.sub(r"[\s_-]+", "", haystack).casefold()
    return creator_key in haystack_key


def _topic_query(payload: dict[str, Any], source: dict[str, Any]) -> str:
    del source
    subject = payload.get("main_subject")
    subject_key = (
        re.sub(
            r"[\s_-]+",
            "",
            _clean_text(subject.get("name"), 80),
        ).casefold()
        if isinstance(subject, dict)
        else ""
    )
    candidates = [
        *(payload.get("tags") or []),
        payload.get("title"),
        payload.get("summary"),
    ]
    combined = " ".join(_clean_text(raw, 300) for raw in candidates)
    combined_key = re.sub(r"[\s_-]+", "", combined).casefold()
    if not combined_key or combined_key == subject_key:
        return ""

    # FANZA検索には一度に一つの、実際にヒットしやすい語だけを渡す。
    # 人名・グループ名・媒体名を足し合わせると検索結果が消えるため、
    # 視覚的な衣装や行為を優先する。
    canonical_rules = (
        ("口内射精", ("口内射精", "口の中に射精", "口に精子")),
        ("顔射", ("顔射", "顔に精子", "顔へ射精")),
        ("中出し", ("中出し", "膣内射精", "精子が垂れ", "精液が垂れ")),
        ("デリヘル", ("デリヘル", "デリバリーヘルス")),
        ("コスプレ", ("コスプレ", "コスプレイヤー")),
        ("バニー", ("バニーガール", "バニー", "うさ耳", "兎耳")),
        ("AI", ("ai生成", "ai画像", "ai美女", "生成ai", "人工知能")),
        ("競泳水着", ("競泳水着",)),
        ("マイクロビキニ", ("マイクロビキニ",)),
        ("ブルマ", ("ドルフィンパンツ", "ブルマ")),
        ("ランジェリー", ("ランジェリー", "下着姿")),
        ("クンニ", ("おまんこ", "女性器", "まんこ")),
        ("ぶっかけ", ("ぶっかけ", "精子まみれ", "精液まみれ")),
    )
    for label, aliases in canonical_rules:
        if any(re.sub(r"[\s_-]+", "", alias).casefold() in combined_key for alias in aliases):
            return label
    for topic_key, topic_label in _INFERRED_FANZA_TOPIC_KEYS:
        if topic_key in combined_key and topic_key != subject_key:
            return topic_label
    return ""


def is_empty_related_ad(block: Any) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "ad"
        and _clean_text(block.get("text"), 240) in _EMPTY_RELATED_AD_TEXTS
    )


def _fanza_search_url(query: str) -> str:
    return (
        "https://www.dmm.co.jp/digital/videoa/-/list/search/=/?searchstr="
        + quote(_clean_text(query, 120), safe="")
    )


def _fanza_performer_url(source: dict[str, Any], name: str) -> str:
    target = re.sub(r"[\s・･_-]+", "", _clean_text(name, 80)).casefold()
    for item in source.get("fanza_performer_pages") or []:
        if not isinstance(item, dict):
            continue
        item_name = re.sub(
            r"[\s・･_-]+", "", _clean_text(item.get("name"), 80)
        ).casefold()
        url = _safe_public_url(item.get("url"))
        if not target or item_name != target or not url:
            continue
        parsed = urlparse(url)
        if (
            (parsed.hostname or "").casefold() == "video.dmm.co.jp"
            and parsed.path.rstrip("/") == "/av/list"
            and parse_qs(parsed.query).get("actress")
        ):
            return url
    return _fanza_search_url(name)


def person_destination_mode(payload: dict[str, Any]) -> str:
    subject = payload.get("main_subject")
    if not isinstance(subject, dict) or subject.get("kind") != "person":
        return ""
    name = _clean_text(subject.get("name"), 60)
    if not name or name.casefold() in _GENERIC_TAGS:
        return ""
    role = _clean_text(subject.get("role"), 120).casefold()
    is_public = subject.get("is_public_creator") is True
    if not is_public and any(term in role for term in _PRIVATE_PERSON_ROLE_TERMS):
        return ""
    source_is_fanza = bool(_fanza_product_key(payload.get("source_url"))) or (
        payload.get("content_mode") == "fanza_product"
    )
    adult_performer = any(
        term in role
        for term in ("av女優", "av出演者", "セクシー女優", "fanza作品の出演者")
    ) or (source_is_fanza and "出演者" in role)
    if adult_performer:
        return "fanza_performer"
    if is_public or any(term in role for term in _PUBLIC_PERSON_ROLE_TERMS):
        return "official_search"
    return ""


def ensure_person_destination(payload: dict[str, Any]) -> bool:
    """Give a verified public subject a useful next step without inventing a profile."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return False
    mode = person_destination_mode(payload)
    if not mode:
        return False
    subject = payload.get("main_subject") or {}
    name = _clean_text(subject.get("name"), 60)
    existing_kinds = {
        str(block.get("link_kind") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "related_link"
    }
    if mode == "fanza_performer":
        suppressed_names = {
            _clean_text(value, 60).casefold()
            for value in payload.get("unresolved_fanza_performer_names") or []
            if _clean_text(value, 60)
        }
        if name.casefold() in suppressed_names:
            return False
        if "verified_person_search" in existing_kinds:
            return False
        block = _related_block(
            url=_fanza_search_url(name),
            title=f"{name}の出演作品",
            text="記事で確認できた出演者名から、FANZA内の関連作品を表示します。",
            button_text="出演作品をFANZAで見る",
            label="この人物の関連作品",
            provider="fanza",
            link_kind="verified_person_search",
            evidence="作品ページで出演者名を確認。特定作品とは別の出演者検索",
            confidence=85,
            affiliate_network="fanza",
        )
    else:
        if existing_kinds.intersection({
            "official_profile", "official_content", "exact_official_work", "person_search",
        }):
            return False
        block = _related_block(
            url="https://www.google.com/search?q=" + quote(f"{name} 公式", safe=""),
            title=f"{name}の公式情報を探す",
            text="人物名から公式サイトや公式アカウントを探す検索です。移動先が本人公式か確認して閲覧できます。",
            button_text="公式情報を探す",
            label="この人物の関連ページ",
            provider="web_search",
            link_kind="person_search",
            evidence="公開活動者の人物名は確認済み。公式URLは未特定",
            confidence=60,
        )
    block["id"] = "article-related-destination-person"
    blocks.append(block)
    return True


def _related_block(
    *,
    url: str,
    title: str,
    text: str,
    button_text: str,
    label: str,
    provider: str,
    link_kind: str,
    evidence: str,
    confidence: int,
    affiliate_network: str = "",
    thumbnail_url: str = "",
    thumbnail_source_kind: str = "",
    thumbnail_owner_url: str = "",
) -> dict[str, Any]:
    block = {
        "id": "article-related-destination",
        "type": "related_link",
        "url": url,
        "title": title,
        "text": text,
        "button_text": button_text,
        "placement_label": label,
        "provider": provider,
        "link_kind": link_kind,
        "match_evidence": evidence,
        "match_confidence": max(0, min(100, confidence)),
        "affiliate_network": affiliate_network,
        "affiliate_eligible": bool(affiliate_network),
    }
    safe_thumbnail = _safe_public_url(thumbnail_url)
    if safe_thumbnail:
        block["thumbnail_url"] = safe_thumbnail
        block["thumbnail_source_kind"] = thumbnail_source_kind or "official_page"
        block["thumbnail_owner_url"] = _safe_public_url(thumbnail_owner_url) or url
    return block


def _verified_official_work(source: dict[str, Any]) -> dict[str, Any] | None:
    for item in source.get("verified_work_destinations") or []:
        if not isinstance(item, dict):
            continue
        url = _safe_public_url(item.get("url"))
        title = _clean_text(item.get("title"), 180)
        provider = _clean_text(item.get("provider"), 80)
        reason = _clean_text(item.get("reason"), 300)
        if not url or not title or not provider or not reason:
            continue
        return {
            "url": url,
            "title": title,
            "provider": provider,
            "reason": reason,
            "confidence": max(0, min(100, int(item.get("confidence") or 95))),
            "thumbnail_url": _safe_public_url(item.get("thumbnail_url")),
        }
    return None


def _fallback_footer_recommendation(payload: dict[str, Any]) -> dict[str, Any]:
    query = _topic_query(payload, {})
    title_query = query
    if query:
        url = _fanza_search_url(query)
        title = f"{title_query}系の作品を探す"
        text = (
            "この記事で紹介した人物や投稿と同一作品ではありません。"
            "題材が近いFANZA作品の検索結果です。"
        )
        button_text = "FANZAで関連ジャンルを見る"
        evidence = "記事から安全なジャンル語だけを抽出した関連検索"
        confidence = 40
    else:
        url = _FANZA_MONTHLY_RANKING_URL
        title = "FANZAの人気作品を見る"
        text = (
            "人物・作品・題材を安全に特定できなかったため、"
            "特定作品とは結び付けずFANZAの月間ランキングを案内します。"
        )
        button_text = "FANZAの人気作品を見る"
        evidence = "安全なジャンル語を抽出できなかったため一般ランキングへ案内"
        confidence = 20
    block = _related_block(
        url=url,
        title=title,
        text=text,
        button_text=button_text,
        label="記事の題材から選ぶ",
        provider="fanza",
        link_kind="inferred_topic_search",
        evidence=evidence,
        confidence=confidence,
        affiliate_network="fanza",
    )
    block["search_query"] = query or "人気作品"
    block["id"] = "article-related-footer-recommendation"
    return block


def _preferred_thumbnail_image_id(payload: dict[str, Any]) -> str:
    images = payload.get("images")
    if not isinstance(images, list):
        return ""
    available = {
        _clean_text(image.get("id"), 80)
        for image in images
        if isinstance(image, dict) and _clean_text(image.get("id"), 80)
    }
    if not available:
        return ""
    thumbnail_id = _clean_text(payload.get("thumbnail_id"), 80)
    if thumbnail_id in available:
        return thumbnail_id
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "images":
            continue
        for image_id in block.get("image_ids") or []:
            image_id = _clean_text(image_id, 80)
            if image_id in available:
                return image_id
    for image in images:
        if not isinstance(image, dict):
            continue
        image_id = _clean_text(image.get("id"), 80)
        if image_id in available:
            return image_id
    return ""


def _fanza_product_key(value: Any, *, depth: int = 0) -> str:
    if depth > 1:
        return ""
    candidate = _safe_public_url(value)
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    hostname = (parsed.hostname or "").casefold()
    if hostname in {"al.dmm.com", "al.dmm.co.jp", "al.fanza.co.jp"}:
        destination = str((query.get("lurl") or [""])[-1])
        return _fanza_product_key(unquote(destination), depth=depth + 1)
    if not (
        hostname == "dmm.co.jp"
        or hostname.endswith(".dmm.co.jp")
        or hostname == "fanza.co.jp"
        or hostname.endswith(".fanza.co.jp")
    ):
        return ""
    for key in ("id", "cid"):
        product_id = _clean_text((query.get(key) or [""])[-1], 80)
        normalized = re.sub(r"[^a-z0-9]", "", product_id.casefold())
        if normalized:
            return normalized
    path_match = re.search(
        r"(?:^|[/=])(?:cid|id)[=/]([a-z0-9_-]{4,80})(?:[/]|$)",
        parsed.path.casefold(),
    )
    return re.sub(r"[^a-z0-9]", "", path_match.group(1)) if path_match else ""


def _exact_product_footer(
    payload: dict[str, Any], blocks: list[Any], thumbnail_image_id: str
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    source_url = _safe_public_url(payload.get("source_url"))
    source_product_key = _fanza_product_key(source_url)
    for block in blocks:
        if (
            not isinstance(block, dict)
            or block.get("type") != "product_cta"
            or str(block.get("id") or "") == _FOOTER_PRODUCT_ID
            or not _safe_public_url(block.get("url"))
        ):
            continue
        match_type = _clean_text(block.get("match_type"), 40).casefold()
        same_source_product = bool(source_product_key) and (
            _fanza_product_key(block.get("url")) == source_product_key
        )
        if same_source_product or match_type.startswith("exact_"):
            candidates.append(block)
    if not candidates:
        return None
    def product_rank(item: dict[str, Any]) -> tuple[int, bool]:
        try:
            confidence = max(
                0, min(100, int(item.get("match_confidence") or 0))
            )
        except (TypeError, ValueError):
            confidence = 0
        return (
            confidence,
            bool(item.get("thumbnail_image_id") or item.get("thumbnail_url")),
        )

    exact = max(candidates, key=product_rank)
    footer = dict(exact)
    footer["id"] = _FOOTER_PRODUCT_ID
    footer["placement_label"] = "この記事で紹介している作品"
    footer["text"] = (
        "この記事で紹介した作品そのものです。作品ページでサンプル、出演者、"
        "配信内容を確認できます。"
    )
    footer["button_text"] = "FANZAでこの作品を見る"
    return footer


def ensure_related_footer(payload: dict[str, Any]) -> bool:
    """Replace empty ad shells and finish the article with a useful next step."""
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or not payload.get("slug"):
        return False

    person_destination_added = ensure_person_destination(payload)
    raw_blocks = payload.get("blocks")

    thumbnail_image_id = _preferred_thumbnail_image_id(payload)
    related_thumbnail_ids = {
        str(image.get("id") or "")
        for image in payload.get("images") or []
        if isinstance(image, dict)
        and image.get("related_thumbnail_only") is True
        and str(image.get("id") or "")
    }
    blocks = [dict(block) if isinstance(block, dict) else block for block in raw_blocks]
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            continue
        if str(block.get("link_kind") or "") == "inferred_topic_search":
            # A generic search is not the person or work in the article. Using
            # article media here makes the destination look like an exact
            # product, so an unresolved search card must remain text-only.
            block.pop("thumbnail_image_id", None)
            block.pop("thumbnail_url", None)
            block.pop("thumbnail_source_kind", None)
            block.pop("thumbnail_owner_url", None)
    exact_product_footer = _exact_product_footer(
        payload, blocks, thumbnail_image_id
    )
    official_account_candidates = [
        block for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and str(block.get("link_kind") or "") in _OFFICIAL_ACCOUNT_KINDS
        and _safe_public_url(block.get("url"))
    ]
    official_accounts_by_person_provider: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, block in enumerate(official_account_candidates):
        provider = _clean_text(block.get("provider"), 40).casefold()
        person_name = _clean_text(block.get("person_name"), 80).casefold()
        key = (
            f"{person_name}|{provider}"
            if person_name and provider
            else provider or _safe_public_url(block.get("url")).casefold()
        )
        existing = official_accounts_by_person_provider.get(key)
        rank = 2 if str(block.get("link_kind") or "") == "official_profile" else 1
        if existing is None or rank > (
            2 if str(existing[1].get("link_kind") or "") == "official_profile" else 1
        ):
            official_accounts_by_person_provider[key] = (index, block)
    official_profiles = [
        block
        for _index, block in sorted(
            official_accounts_by_person_provider.values(), key=lambda item: item[0]
        )
    ]
    named_creator = _named_creator({}, payload)
    recommendations = [
        block for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "related_link"
        and str(block.get("link_kind") or "") in _FOOTER_RECOMMENDATION_KINDS
        and (
            str(block.get("link_kind") or "") != "person_search"
            or _person_search_matches_creator(block, named_creator)
        )
    ]
    has_exact_official_work = any(
        isinstance(block, dict)
        and block.get("type") == "related_link"
        and block.get("link_kind") == "exact_official_work"
        for block in blocks
    )
    suppress_generic_recommendation = bool(
        payload.get("suppress_generic_related_recommendation")
        or has_exact_official_work
        or exact_product_footer is not None
    )
    if suppress_generic_recommendation:
        # Suppress inferred topic/person searches only. A performer verified on
        # the product page is a concrete destination and must survive this
        # guard even when the exact work already has its own CTA.
        recommendations = [
            block for block in recommendations
            if str(block.get("link_kind") or "") == "verified_person_search"
        ]
    if exact_product_footer is None and any(
        str(block.get("link_kind") or "") == "inferred_topic_search"
        for block in recommendations
    ):
        recommendations = [
            block for block in recommendations
            if str(block.get("link_kind") or "") != "inferred_topic_search"
        ]
        recommendations.append(_fallback_footer_recommendation(payload))
    if exact_product_footer is None and _topic_query(payload, {}) and any(
        str(block.get("link_kind") or "") == "person_search"
        and (
            "公式グッズ" in _clean_text(block.get("title"), 200)
            or "公式物販" in _clean_text(block.get("text"), 300)
            or "公式物販" in _clean_text(block.get("match_evidence"), 300)
        )
        for block in recommendations
    ):
        # Older drafts used a broad "person + official goods" Google query in
        # the monetization slot. Official accounts now have their own cards;
        # replace that stale query with one strong article-theme keyword.
        recommendations = [
            block for block in recommendations
            if not (
                str(block.get("link_kind") or "") == "person_search"
                and (
                    "公式グッズ" in _clean_text(block.get("title"), 200)
                    or "公式物販" in _clean_text(block.get("text"), 300)
                    or "公式物販" in _clean_text(block.get("match_evidence"), 300)
                )
            )
        ]
        if not any(
            str(block.get("link_kind") or "") == "inferred_topic_search"
            for block in recommendations
        ):
            recommendations.append(_fallback_footer_recommendation(payload))
    if (
        exact_product_footer is None
        and not suppress_generic_recommendation
        and _topic_query(payload, {})
        and any(
            str(block.get("id") or "") == "article-related-destination-person"
            and str(block.get("link_kind") or "") == "person_search"
            for block in recommendations
        )
        and not any(
            str(block.get("link_kind") or "") == "inferred_topic_search"
            for block in recommendations
        )
    ):
        # Keep the person's official-information route and use the second slot
        # for an article-theme recommendation. They serve different intents.
        recommendations.append(_fallback_footer_recommendation(payload))

    retained: list[Any] = []
    for block in blocks:
        if is_empty_related_ad(block):
            continue
        if not isinstance(block, dict):
            retained.append(block)
            continue
        block_id = str(block.get("id") or "")
        if block_id.startswith(_FOOTER_PROFILE_ID_PREFIX):
            continue
        if block_id == _FOOTER_PRODUCT_ID:
            continue
        if (
            block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in _OFFICIAL_ACCOUNT_KINDS
        ):
            # Official accounts are rendered once, together after the related
            # recommendation. Keeping their original block would duplicate the
            # same destination near the top and again in the footer.
            continue
        if (
            block.get("type") == "related_link"
            and str(block.get("link_kind") or "") in _FOOTER_RECOMMENDATION_KINDS
        ):
            continue
        retained.append(block)

    footer_recommendations: list[dict[str, Any]] = []
    # The exact work already has a CTA directly below its image/video. Repeating
    # that same product at the footer wastes the last click opportunity. Prefer
    # the identified performer, otherwise show one honest topic recommendation.
    if exact_product_footer is not None:
        recommendations = [
            item for item in recommendations
            if str(item.get("link_kind") or "") == "verified_person_search"
        ]
    if not recommendations and not suppress_generic_recommendation:
        recommendations = [_fallback_footer_recommendation(payload)]
    seen_recommendation_urls: set[str] = set()
    generic_recommendation_count = 0
    for recommendation in recommendations:
        url = _safe_public_url(recommendation.get("url"))
        if not url or url in seen_recommendation_urls:
            continue
        link_kind = str(recommendation.get("link_kind") or "")
        if link_kind != "verified_person_search" and generic_recommendation_count >= 2:
            continue
        seen_recommendation_urls.add(url)
        footer = dict(recommendation)
        footer["id"] = (
            _clean_text(footer.get("id"), 80)
            or f"article-related-footer-recommendation-{len(footer_recommendations) + 1}"
        )
        footer_recommendations.append(footer)
        if link_kind != "verified_person_search":
            generic_recommendation_count += 1

    footer_profiles: list[dict[str, Any]] = []
    seen_profile_urls: set[str] = set()
    for profile in official_profiles:
        url = _safe_public_url(profile.get("url"))
        if not url or url in seen_profile_urls:
            continue
        seen_profile_urls.add(url)
        footer = dict(profile)
        footer["id"] = f"{_FOOTER_PROFILE_ID_PREFIX}{len(footer_profiles) + 1}"
        if str(footer.get("thumbnail_image_id") or "") not in related_thumbnail_ids:
            footer.pop("thumbnail_image_id", None)
        footer["placement_label"] = "本人の公式アカウント"
        footer["text"] = (
            "この記事が気に入った人向けに、本人の公式アカウントをもう一度案内します。"
            "最新の投稿やプロフィールを確認できます。"
        )
        footer_profiles.append(footer)

    updated_blocks = [*retained, *footer_recommendations, *footer_profiles]
    changed = (
        updated_blocks != raw_blocks
        or payload.get("related_footer_version") != _RELATED_FOOTER_VERSION
        or person_destination_added
    )
    payload["blocks"] = updated_blocks
    payload["related_footer_version"] = _RELATED_FOOTER_VERSION

    destinations = [
        dict(item) for item in (payload.get("related_destinations") or [])
        if isinstance(item, dict)
    ]
    if exact_product_footer is not None:
        destinations = [
            item for item in destinations
            if str(item.get("link_kind") or "") not in _FOOTER_RECOMMENDATION_KINDS
        ]
    else:
        destinations = [
            item for item in destinations
            if str(item.get("link_kind") or "") != "inferred_topic_search"
            and not (
                str(item.get("link_kind") or "") == "person_search"
                and not _person_search_matches_creator(item, named_creator)
            )
        ]
    destination_urls = {str(item.get("url") or "") for item in destinations}
    for item in [*footer_recommendations, *official_profiles]:
        url = str(item.get("url") or "")
        if not url or url in destination_urls:
            continue
        destination_urls.add(url)
        destination = {
            key: item.get(key)
            for key in (
                "url", "title", "provider", "link_kind", "match_confidence",
            )
        }
        person_name = _clean_text(item.get("person_name"), 80)
        if person_name:
            destination["person_name"] = person_name
        if item.get("type") == "product_cta":
            destination["provider"] = "fanza"
            destination["link_kind"] = item.get("match_type") or "exact_article"
        destinations.append(destination)
    payload["related_destinations"] = destinations
    return changed


def _official_social_destinations(
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_names_by_url = {
        _safe_public_url(item.get("url")): _clean_text(item.get("name"), 80)
        for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict)
        and _safe_public_url(item.get("url"))
        and _clean_text(item.get("name"), 80)
    }
    candidates: list[tuple[str, str, str, str, str, str, int]] = []
    for url, provider, name, link_kind, evidence, confidence in _analysis_social_links(source):
        service = {
            "x": "X",
            "youtube": "YouTube",
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "myfans": "MyFans",
            "fantia": "Fantia",
        }[provider]
        candidates.append((
            url,
            provider,
            f"{name}の{service}",
            f"{service}で見る",
            link_kind,
            evidence,
            confidence,
        ))
    x_profile = _x_profile_url(source)
    if x_profile:
        username = x_profile.rstrip("/").rsplit("/", 1)[-1]
        candidates.append((
            x_profile, "x", f"@{username} のX", "Xプロフィールを見る",
            "official_profile",
            "記事元のXアカウントを確認",
            100,
        ))
    youtube_url = _youtube_source_url(source)
    if youtube_url:
        candidates.append((
            youtube_url,
            "youtube",
            _clean_text(source.get("title"), 120) or "YouTube公式ページ",
            "YouTubeで見る",
            "official_content",
            "記事元のYouTube URLを確認",
            100,
        ))
    tiktok_url = _tiktok_profile_url(source)
    if tiktok_url:
        candidates.append((
            tiktok_url,
            "tiktok",
            "紹介した人物のTikTok",
            "TikTokで見る",
            "official_profile",
            "記事元のTikTok URLを確認",
            100,
        ))
    instagram_url = _instagram_profile_url(source)
    if instagram_url:
        candidates.append((
            instagram_url,
            "instagram",
            "紹介した人物のInstagram",
            "Instagramで見る",
            "official_profile",
            "記事元のInstagram URLを確認",
            100,
        ))
    service_labels = {
        "x": ("紹介した人物のX", "Xプロフィールを見る"),
        "youtube": ("紹介した人物のYouTube", "YouTubeで見る"),
        "tiktok": ("紹介した人物のTikTok", "TikTokで見る"),
        "instagram": ("紹介した人物のInstagram", "Instagramで見る"),
        "myfans": ("紹介した人物のMyFans", "MyFansで見る"),
        "fantia": ("紹介した人物のFantia", "Fantiaで見る"),
    }
    for url, provider in _verified_social_links(source):
        title, button_text = service_labels[provider]
        candidates.append((
            url,
            provider,
            title,
            button_text,
            "official_content" if provider == "youtube" else "official_profile",
            f"元ページ内の{title.rsplit('の', 1)[-1]}リンクを確認",
            95,
        ))

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url, provider, title, button_text, link_kind, evidence, confidence in candidates:
        if url in seen:
            continue
        seen.add(url)
        service = {
            "x": "X",
            "youtube": "YouTube",
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "myfans": "MyFans",
            "fantia": "Fantia",
        }[provider]
        thumbnail_url, thumbnail_source_kind, thumbnail_owner_url = (
            _profile_thumbnail_for_url(source, url)
        )
        block = _related_block(
            url=url,
            title=title,
            text=f"記事で紹介した本人の{service}です。最新の投稿やプロフィールを確認できます。",
            button_text=button_text,
            label="本人の公式アカウント",
            provider=provider,
            link_kind=link_kind,
            evidence=evidence,
            confidence=confidence,
            thumbnail_url=thumbnail_url,
            thumbnail_source_kind=thumbnail_source_kind,
            thumbnail_owner_url=thumbnail_owner_url,
        )
        person_name = profile_names_by_url.get(url, "")
        if person_name:
            block["person_name"] = person_name
        results.append(block)
    instagram_content = _instagram_content_url(source)
    if instagram_content and instagram_content not in seen:
        results.append(_related_block(
            url=instagram_content,
            title="紹介した人物のInstagram投稿",
            text="記事で紹介したInstagramの元投稿です。投稿画面から本人のプロフィールも確認できます。",
            button_text="Instagramで見る",
            label="本人の公式投稿",
            provider="instagram",
            link_kind="official_content",
            evidence="元ページ内のInstagram投稿リンクを確認",
            confidence=90,
        ))
    return results


def resolve_article_destination(
    payload: dict[str, Any],
    source: dict[str, Any],
    opportunities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Choose one honest next destination without inventing an exact match."""
    matched_opportunities = [
        item for item in opportunities
        if not isinstance(item, dict) or item.get("article_match") is not False
    ]
    for item in matched_opportunities:
        product_url = _safe_public_url(item.get("product_url"))
        product_code = _clean_text(item.get("product_code"), 60)
        if not product_url or not product_code:
            continue
        program = _clean_text(item.get("program_name"), 80) or "公式サイト"
        product_title = _clean_text(item.get("product_title"), 300)
        return _related_block(
            url=product_url,
            title=product_title or f"{program} {product_code}",
            text="記事内で特定できた作品の公式ページです。サンプルや作品情報を確認できます。",
            button_text="公式作品ページを見る",
            label="この作品の公式ページ",
            provider=_clean_text(item.get("program_id"), 30) or "official",
            link_kind="exact_official_work",
            evidence=_clean_text(item.get("reason"), 300) or "作品番号を確認",
            confidence=int(item.get("confidence") or 100),
            thumbnail_url=_safe_public_url(item.get("thumbnail_url")),
            thumbnail_source_kind="official_page",
            thumbnail_owner_url=product_url,
        )

    official_work = _verified_official_work(source)
    if official_work:
        block = _related_block(
            url=official_work["url"],
            title=official_work["title"],
            text=(
                "記事で紹介した作品そのものの公式・正規販売ページです。"
                "作品情報や販売内容を確認できます。"
            ),
            button_text="この作品の公式ページを見る",
            label="この記事で紹介している作品",
            provider=official_work["provider"],
            link_kind="exact_official_work",
            evidence=official_work["reason"],
            confidence=official_work["confidence"],
            thumbnail_url=official_work.get("thumbnail_url", ""),
            thumbnail_source_kind="official_page",
            thumbnail_owner_url=official_work["url"],
        )
        return block

    if source.get("official_work_required"):
        payload["suppress_generic_related_recommendation"] = True
        return None

    x_profile = _x_profile_url(source)
    if x_profile:
        username = x_profile.rstrip("/").rsplit("/", 1)[-1]
        return _related_block(
            url=x_profile,
            title=f"@{username} のX",
            text="記事で紹介した本人のXプロフィールです。最新投稿やプロフィールを確認できます。",
            button_text="Xプロフィールを見る",
            label="本人の公式アカウント",
            provider="x",
            link_kind="official_profile",
            evidence="記事元のXアカウントを確認",
            confidence=100,
            thumbnail_url=_profile_thumbnail_for_url(source, x_profile),
            thumbnail_source_kind="profile",
            thumbnail_owner_url=x_profile,
        )

    youtube_url = _youtube_source_url(source)
    if youtube_url:
        return _related_block(
            url=youtube_url,
            title=_clean_text(source.get("title"), 120) or "YouTube公式ページ",
            text="記事で紹介したYouTubeの公式ページです。動画やチャンネル情報を確認できます。",
            button_text="YouTubeで見る",
            label="YouTube公式ページ",
            provider="youtube",
            link_kind="official_content",
            evidence="記事元のYouTube URLを確認",
            confidence=100,
        )

    social_url, social_provider = _verified_social_link(source)
    if social_url:
        service = {
            "x": "X",
            "youtube": "YouTube",
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "myfans": "MyFans",
            "fantia": "Fantia",
        }.get(social_provider, "公式SNS")
        return _related_block(
            url=social_url,
            title=f"紹介した人物の{service}",
            text=f"ページ内で確認できた{service}アカウントです。本人の投稿を確認できます。",
            button_text=f"{service}で見る",
            label="本人の公式アカウント",
            provider=social_provider,
            link_kind="official_profile",
            evidence="ページ内の公式アカウント表記を確認",
            confidence=95,
            thumbnail_url=_profile_thumbnail_for_url(source, social_url),
            thumbnail_source_kind="profile",
            thumbnail_owner_url=social_url,
        )

    performer = _verified_performer_name(source)
    if performer:
        return _related_block(
            url=_fanza_performer_url(source, performer),
            title=f"{performer}の出演作品",
            text="記事で確認できた出演者名から、FANZA内の関連作品を表示します。",
            button_text="出演作品をFANZAで見る",
            label="この人物の関連作品",
            provider="fanza",
            link_kind="verified_person_search",
            evidence="記事内で出演者名を確認。特定作品ではなく出演者検索",
            confidence=85,
            affiliate_network="fanza",
        )

    creator = _named_creator(source, payload)
    text = " ".join(
        _clean_text(value, 300)
        for value in (source.get("title"), source.get("description"), payload.get("title"))
    ).casefold()
    if creator and ("youtube" in text or "youtuber" in text or "ユーチューバー" in text):
        return _related_block(
            url="https://www.youtube.com/results?search_query=" + quote(creator, safe=""),
            title=f"{creator}のYouTubeを探す",
            text="人物名を使ったYouTube検索です。公式チャンネルか確認してから閲覧できます。",
            button_text="YouTubeで探す",
            label="関連ページ",
            provider="youtube",
            link_kind="person_search",
            evidence="人物名は確認済み。公式チャンネルURLは未特定",
            confidence=65,
        )
    if creator and "グラビア" in text:
        # Official social profiles are emitted as separate cards. Use the ad
        # slot for one concrete visual/theme keyword when available instead of
        # sending readers to a vague Google merchandise search.
        if _topic_query(payload, source):
            block = _fallback_footer_recommendation(payload)
            block["id"] = "article-related-destination"
            return block
        return _related_block(
            url="https://www.google.com/search?q=" + quote(f"{creator} 公式 グッズ", safe=""),
            title=f"{creator}の公式グッズを探す",
            text="人物名から公式物販を探す検索ページです。販売元を確認して閲覧できます。",
            button_text="公式グッズを探す",
            label="関連ページ",
            provider="web_search",
            link_kind="person_search",
            evidence="人物名は確認済み。公式物販URLは未特定",
            confidence=60,
        )

    if not payload.get("slug"):
        return None
    fallback = _fallback_footer_recommendation(payload)
    fallback["id"] = "article-related-destination"
    return fallback


def resolve_article_destinations(
    payload: dict[str, Any],
    source: dict[str, Any],
    opportunities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if source.get("official_work_required"):
        payload["suppress_generic_related_recommendation"] = True
    first = resolve_article_destination(payload, source, opportunities)
    matched_opportunities = [
        item for item in opportunities
        if not isinstance(item, dict) or item.get("article_match") is not False
    ]
    destinations: list[dict[str, Any]] = _official_social_destinations(source)
    seen_urls = {str(item.get("url") or "") for item in destinations}
    if first is None:
        # A direct FANZA article already has its exact product CTA next to the
        # media, so official_work_required can legitimately suppress another
        # work card. That must not suppress the verified performer's catalogue.
        performer_names: list[str] = []
        verified_performer = _verified_performer_name(source)
        if verified_performer:
            performer_names.append(verified_performer)
        performer_names.extend(
            _clean_text(item.get("name"), 60)
            for item in source.get("ai_fanza_people") or source.get("fanza_people") or []
            if isinstance(item, dict)
        )
        seen_names: set[str] = set()
        for performer in performer_names:
            performer_key = performer.casefold()
            if not performer or performer_key in seen_names:
                continue
            seen_names.add(performer_key)
            performer_url = _fanza_performer_url(source, performer)
            if performer_url in seen_urls:
                continue
            seen_urls.add(performer_url)
            performer_block = _related_block(
                    url=performer_url,
                    title=f"{performer}の出演作品",
                    text="記事で確認できた出演者名から、FANZA内の関連作品を表示します。",
                    button_text="出演作品をFANZAで見る",
                    label="この人物の関連作品",
                    provider="fanza",
                    link_kind="verified_person_search",
                    evidence="記事内で出演者名を確認。特定作品とは別の出演作品一覧",
                    confidence=85,
                    affiliate_network="fanza",
                )
            performer_block["person_name"] = performer
            destinations.append(performer_block)
        for index, block in enumerate(destinations, start=1):
            block["id"] = f"article-related-destination-{index}"
        return destinations

    if first.get("link_kind") == "exact_official_work":
        first_url = str(first.get("url") or "")
        if first_url and first_url not in seen_urls:
            seen_urls.add(first_url)
            destinations.append(first)
        for item in matched_opportunities:
            product_url = _safe_public_url(item.get("product_url"))
            product_code = _clean_text(item.get("product_code"), 60)
            if not product_url or not product_code or product_url in seen_urls:
                continue
            seen_urls.add(product_url)
            program = _clean_text(item.get("program_name"), 80) or "公式サイト"
            product_title = _clean_text(item.get("product_title"), 300)
            destinations.append(_related_block(
                url=product_url,
                title=product_title or f"{program} {product_code}",
                text="記事内で特定できた作品の公式ページです。サンプルや作品情報を確認できます。",
                button_text="公式作品ページを見る",
                label="この作品の公式ページ",
                provider=_clean_text(item.get("program_id"), 30) or "official",
                link_kind="exact_official_work",
                evidence=_clean_text(item.get("reason"), 300) or "作品番号を確認",
                confidence=int(item.get("confidence") or 100),
                thumbnail_url=_safe_public_url(item.get("thumbnail_url")),
                thumbnail_source_kind="official_page",
                thumbnail_owner_url=product_url,
            ))
        performer_names: list[str] = []
        verified_performer = _verified_performer_name(source)
        if verified_performer:
            performer_names.append(verified_performer)
        performer_names.extend(
            _clean_text(item.get("name"), 60)
            for item in source.get("ai_fanza_people") or source.get("fanza_people") or []
            if isinstance(item, dict)
        )
        seen_names: set[str] = set()
        for name in performer_names:
            if not name or name.casefold() in seen_names:
                continue
            seen_names.add(name.casefold())
            performer_url = _fanza_performer_url(source, name)
            if performer_url in seen_urls:
                continue
            seen_urls.add(performer_url)
            performer_block = _related_block(
                url=performer_url,
                title=f"{name}の出演作品",
                text="記事で確認できた出演者名から、FANZA内の関連作品を表示します。",
                button_text="出演作品をFANZAで見る",
                label="この人物の関連作品",
                provider="fanza",
                link_kind="verified_person_search",
                evidence="記事内で出演者名を確認。特定作品とは別の出演作品一覧",
                confidence=85,
                affiliate_network="fanza",
            )
            performer_block["person_name"] = name
            destinations.append(performer_block)
    elif first.get("link_kind") == "verified_person_search":
        initial_count = len(destinations)
        seen_names: set[str] = set()
        for item in source.get("ai_fanza_people") or source.get("fanza_people") or []:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"), 60)
            if not name or name.casefold() in seen_names:
                continue
            seen_names.add(name.casefold())
            performer_block = _related_block(
                url=_fanza_performer_url(source, name),
                title=f"{name}の出演作品",
                text="記事で確認できた出演者名から、FANZA内の関連作品を表示します。",
                button_text="出演作品をFANZAで見る",
                label="この人物の関連作品",
                provider="fanza",
                link_kind="verified_person_search",
                evidence=_clean_text(item.get("reason"), 300)
                or "記事内で出演者名を確認。特定作品ではなく出演者検索",
                confidence=85,
                affiliate_network="fanza",
            )
            performer_block["person_name"] = name
            destinations.append(performer_block)
        if len(destinations) == initial_count:
            destinations.append(first)
    else:
        first_url = str(first.get("url") or "")
        if first_url not in seen_urls:
            destinations.append(first)

    for index, block in enumerate(destinations, start=1):
        block["id"] = f"article-related-destination-{index}"
    return destinations


def apply_official_social_destinations(
    payload: dict[str, Any],
    profiles: list[dict[str, Any]],
) -> bool:
    """Insert newly verified official profiles without disturbing article media."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not profiles:
        return False
    destinations = _official_social_destinations({"verified_social_profiles": profiles})
    destinations_by_url = {
        str(item.get("url") or "").strip(): item
        for item in destinations
        if str(item.get("url") or "").strip()
    }
    local_thumbnails = {
        str(image.get("id") or ""): image
        for image in payload.get("images") or []
        if isinstance(image, dict)
        and image.get("related_thumbnail_only") is True
        and str(image.get("id") or "")
    }
    changed = False
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "related_link":
            continue
        destination = destinations_by_url.get(str(block.get("url") or "").strip())
        if destination is None:
            continue
        local_thumbnail = local_thumbnails.get(
            str(block.get("thumbnail_image_id") or "")
        )
        expected_owner = str(
            block.get("thumbnail_owner_url") or block.get("url") or ""
        ).rstrip("/")
        has_local_thumbnail = bool(
            local_thumbnail
            and str(local_thumbnail.get("thumbnail_owner_url") or "").rstrip("/")
            == expected_owner
        )
        for field in (
            "title", "provider", "link_kind", "match_evidence",
            "match_confidence",
        ):
            value = destination.get(field)
            if value and block.get(field) != value:
                block[field] = value
                changed = True
        if has_local_thumbnail:
            if block.pop("thumbnail_url", None):
                changed = True
        else:
            if block.pop("thumbnail_image_id", None):
                changed = True
            for field in (
                "thumbnail_url", "thumbnail_source_kind", "thumbnail_owner_url",
            ):
                value = destination.get(field)
                if value and block.get(field) != value:
                    block[field] = value
                    changed = True
                elif not value and block.pop(field, None):
                    changed = True
    existing_urls = {
        str(block.get("url") or "").strip()
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") in {"related_link", "product_cta"}
    }
    additions = [
        item for item in destinations
        if str(item.get("url") or "").strip() not in existing_urls
    ]
    if not additions:
        return changed
    insert_at = related_link_insert_index(blocks, "official_profile")
    for offset, destination in enumerate(additions):
        destination["id"] = f"article-related-destination-profile-{offset + 1}"
        blocks.insert(insert_at + offset, destination)
    changed = True
    existing_destinations = [
        item for item in (payload.get("related_destinations") or [])
        if isinstance(item, dict)
    ]
    destination_urls = {
        str(item.get("url") or "").strip() for item in existing_destinations
    }
    for destination in additions:
        url = str(destination.get("url") or "").strip()
        if url in destination_urls:
            continue
        destination_urls.add(url)
        destination_summary = {
            key: destination.get(key)
            for key in (
                "url", "title", "provider", "link_kind", "match_confidence",
            )
        }
        person_name = _clean_text(destination.get("person_name"), 80)
        if person_name:
            destination_summary["person_name"] = person_name
        existing_destinations.append(destination_summary)
    payload["related_destinations"] = existing_destinations
    return changed


def related_link_insert_index(
    blocks: list[dict[str, Any]], link_kind: str
) -> int:
    if link_kind in {"exact_official_work", "official_content", "official_profile"}:
        for index, block in enumerate(blocks):
            if block.get("type") in {"images", "videos", "x_posts", "x_profile"}:
                return index + 1
    for index in range(len(blocks) - 1, -1, -1):
        if blocks[index].get("type") != "ad":
            return index + 1
    return len(blocks)
