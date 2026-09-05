from __future__ import annotations

import json
import re
import secrets
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse


REGISTRY_VERSION = 2
NEGATIVE_CACHE_DAYS = 30
PUBLIC_CREATOR_ROLES = {
    "インフルエンサー",
    "tiktoker",
    "youtuber",
    "配信者",
    "コスプレイヤー",
    "モデル",
    "グラビアアイドル",
    "グラビアモデル",
    "アイドル",
    "俳優",
    "女優",
    "av女優",
    "クリエイター",
    "漫画家",
}
SERVICE_HOSTS = {
    "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
    "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com"},
    "instagram": {"instagram.com", "www.instagram.com"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "myfans": {"myfans.jp", "www.myfans.jp"},
    "fantia": {"fantia.jp", "www.fantia.jp"},
}
X_RESERVED_ROUTES = {
    "about", "compose", "download", "explore", "hashtag", "home", "i",
    "intent", "login", "messages", "notifications", "privacy", "search",
    "settings", "share", "signup", "tos",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_person_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value, 100)).casefold()
    text = re.sub(r"^[＠@]+", "", text)
    return re.sub(r"[\s\u3000・･_\-―—,.、。!?！？「」『』【】()（）]+", "", text)


def _registry_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "verified-social-profiles.json"


def load_social_profile_registry(site_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_registry_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    people = value.get("people") if isinstance(value, dict) else None
    return {
        "version": REGISTRY_VERSION,
        "updated_at": str(value.get("updated_at") or "") if isinstance(value, dict) else "",
        "people": [item for item in (people or []) if isinstance(item, dict)],
    }


def save_social_profile_registry(site_root: Path, registry: dict[str, Any]) -> None:
    path = _registry_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": REGISTRY_VERSION,
        "updated_at": _now_iso(),
        "people": [
            item for item in (registry.get("people") or []) if isinstance(item, dict)
        ],
    }
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def canonical_social_profile_url(service: Any, value: Any) -> str:
    provider = _clean_text(service, 20).casefold()
    candidate = str(value or "").strip()
    if provider not in SERVICE_HOSTS or not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or host not in SERVICE_HOSTS[provider]:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if provider == "x":
        if not parts or parts[0].casefold() in X_RESERVED_ROUTES:
            return ""
        username = parts[0]
        return f"https://x.com/{username}" if re.fullmatch(r"[A-Za-z0-9_]{1,15}", username) else ""
    if provider == "tiktok":
        if not parts or not re.fullmatch(r"@[A-Za-z0-9_.]{2,30}", parts[0]):
            return ""
        return f"https://www.tiktok.com/{parts[0]}"
    if provider == "instagram":
        if (
            not parts
            or parts[0].casefold() in {"p", "reel", "reels", "stories", "explore"}
            or not re.fullmatch(r"[A-Za-z0-9_.]{2,30}", parts[0])
        ):
            return ""
        return f"https://www.instagram.com/{parts[0]}/"
    if provider == "youtube":
        if host == "youtu.be" or not parts:
            return ""
        if parts[0].startswith("@") or parts[0].casefold() in {"channel", "user", "c"}:
            return candidate.split("?", 1)[0].rstrip("/")
    if provider == "myfans":
        if not parts or parts[0].casefold() in {"login", "register", "posts", "search"}:
            return ""
        return f"https://myfans.jp/{'/'.join(parts)}".rstrip("/")
    if provider == "fantia":
        if len(parts) >= 2 and parts[0].casefold() == "fanclubs" and parts[1].isdigit():
            return f"https://fantia.jp/fanclubs/{parts[1]}"
    return ""


def _safe_thumbnail_url(value: Any) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return candidate


_META_IMAGE_RE = re.compile(
    r"<meta\b[^>]*(?:property|name)\s*=\s*['\"](?:og:image(?::secure_url)?|twitter:image(?::src)?)['\"][^>]*>",
    re.IGNORECASE,
)
_META_CONTENT_RE = re.compile(
    r"\bcontent\s*=\s*(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)


def fetch_profile_thumbnail(url: str, timeout: float = 6.0) -> str:
    """Return the profile page's own public OGP image, never an article image."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    page = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if "html" in content_type:
                raw = response.read(2_000_000)
                charset = response.headers.get_content_charset() or "utf-8"
                page = raw.decode(charset, errors="replace")
    except (OSError, ValueError):
        pass
    for match in _META_IMAGE_RE.finditer(page):
        content = _META_CONTENT_RE.search(match.group(0))
        if not content:
            continue
        thumbnail = _safe_thumbnail_url(urljoin(url, content.group(2).strip()))
        if thumbnail:
            return thumbnail
    rendered = fetch_rendered_profile_thumbnail(url)
    if rendered:
        return rendered
    canonical_x = canonical_social_profile_url("x", url)
    if canonical_x:
        username = urlparse(canonical_x).path.strip("/").split("/", 1)[0]
        if username:
            # X often withholds profile metadata from logged-out clients.
            # This URL proxies the avatar owned by the exact verified handle.
            return f"https://unavatar.io/x/{username}?fallback=false"
    return ""


def _rendered_profile_thumbnail_score(
    service: str,
    username: str,
    item: dict[str, Any],
) -> int:
    src = _safe_thumbnail_url(item.get("src"))
    if not src:
        return -1000
    alt = _clean_text(item.get("alt"), 300).casefold()
    src_key = src.casefold()
    username_key = username.lstrip("@").casefold()
    try:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    score = 0
    if width >= 80 and height >= 80:
        score += 25
    if width and height and abs(width - height) <= max(width, height) * 0.12:
        score += 35
    if username_key and username_key in alt:
        score += 120
    if re.search(r"profile|avatar|プロフィール写真|プロフィール画像|アイコン", alt):
        score += 150
    if service == "instagram":
        if "cdninstagram.com" in src_key and re.search(r"/t[^/]*-19/", src_key):
            score += 240
        if "profile_pic" in unquote(src_key):
            score += 120
    elif service == "x":
        if "pbs.twimg.com/profile_images/" in src_key:
            score += 320
        elif "pbs.twimg.com/profile_banners/" in src_key:
            score += 220
    elif service == "youtube" and "yt3." in src_key:
        score += 260
    elif service == "tiktok" and re.search(r"avatar|tos-maliva-avt", src_key):
        score += 220
    elif service in {"myfans", "fantia"} and re.search(r"profile|avatar|icon", src_key):
        score += 180
    if re.search(r"post|photo by|ストーリーズ|highlight|サムネイル", alt):
        score -= 180
    if width >= 400 and height >= 400 and abs(width - height) > max(width, height) * 0.2:
        score -= 100
    return score


def fetch_rendered_profile_thumbnail(url: str, timeout: float = 15.0) -> str:
    """Read the visible profile avatar when a social site omits static OGP."""
    service = ""
    username = ""
    for candidate in SERVICE_HOSTS:
        canonical = canonical_social_profile_url(candidate, url)
        if not canonical:
            continue
        service = candidate
        parts = [part for part in urlparse(canonical).path.split("/") if part]
        if candidate == "fantia" and len(parts) >= 2:
            username = parts[1]
        elif parts:
            username = parts[-1] if candidate == "youtube" else parts[0]
        break
    if not service:
        return ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1200, "height": 900},
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/136 Safari/537.36"
                ),
            )
            try:
                page = context.new_page()
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(5000, int(timeout * 1000)),
                )
                page.wait_for_timeout(3500)
                candidates = page.locator("img").evaluate_all(
                    """elements => elements.slice(0, 250).map(element => ({
                        src: element.currentSrc || element.src || '',
                        alt: element.alt || '',
                        width: element.naturalWidth || 0,
                        height: element.naturalHeight || 0
                    }))"""
                )
            finally:
                context.close()
                browser.close()
    except Exception:
        return ""
    ranked = [
        (_rendered_profile_thumbnail_score(service, username, item), item)
        for item in candidates
        if isinstance(item, dict)
    ]
    if not ranked:
        return ""
    best_score, best = max(ranked, key=lambda value: value[0])
    return _safe_thumbnail_url(best.get("src")) if best_score >= 200 else ""


def _safe_evidence(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    kind = _clean_text(item.get("kind"), 40)
    claim = _clean_text(item.get("claim"), 240)
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not kind or not claim:
        return None
    if kind not in {
        "official_profile", "official_hub", "independent_directory", "published_article"
    }:
        return None
    return {"url": url, "kind": kind, "claim": claim}


def validate_social_verification(
    value: Any,
    expected_name: str,
    expected_role: str = "",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("本人アカウント照合結果が不正です")
    returned_name = _clean_text(value.get("subject_name"), 80)
    if normalize_person_name(returned_name) != normalize_person_name(expected_name):
        raise ValueError("検索対象と異なる人物名が返されました")
    status = _clean_text(value.get("status"), 20)
    if status not in {"verified", "ambiguous", "not_found"}:
        raise ValueError("本人アカウントの照合状態が不正です")
    try:
        confidence = max(0, min(100, int(value.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0
    evidence = [
        clean for item in (value.get("evidence") or [])
        if (clean := _safe_evidence(item)) is not None
    ][:8]
    profiles: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value.get("profiles") or []:
        if not isinstance(item, dict):
            continue
        service = _clean_text(item.get("service"), 20).casefold()
        url = canonical_social_profile_url(service, item.get("url"))
        display_name = _clean_text(item.get("display_name"), 120)
        key = (service, url)
        if not url or not display_name or key in seen:
            continue
        seen.add(key)
        profile = {"service": service, "url": url, "display_name": display_name}
        thumbnail_url = _safe_thumbnail_url(item.get("thumbnail_url"))
        if thumbnail_url:
            profile["thumbnail_url"] = thumbnail_url
            thumbnail_owner_url = _safe_thumbnail_url(item.get("thumbnail_owner_url")) or url
            profile["thumbnail_owner_url"] = thumbnail_owner_url
            source_kind = _clean_text(item.get("thumbnail_source_kind"), 40)
            profile["thumbnail_source_kind"] = source_kind or "profile"
        profiles.append(profile)
    evidence_hosts = {
        (urlparse(item["url"]).hostname or "").casefold() for item in evidence
    }
    has_official_evidence = any(
        item["kind"] in {"official_profile", "official_hub"} for item in evidence
    )
    verified = (
        status == "verified"
        and confidence >= 85
        and bool(profiles)
        and has_official_evidence
        and len(evidence_hosts) >= 2
    )
    if not verified:
        profiles = []
        if status == "verified":
            status = "ambiguous"
            confidence = min(confidence, 79)
    return {
        "canonical_name": _clean_text(expected_name, 80),
        "aliases": list(dict.fromkeys(filter(None, [
            _clean_text(expected_name, 80), returned_name,
        ]))),
        "role": _clean_text(value.get("subject_role") or expected_role, 80),
        "status": status,
        "confidence": confidence,
        "profiles": profiles,
        "evidence": evidence,
        "reason": _clean_text(value.get("reason"), 400) or "照合理由なし",
        "verified_at": _now_iso(),
        "retry_after": (
            "" if status == "verified" else
            (datetime.now(timezone.utc) + timedelta(days=NEGATIVE_CACHE_DAYS)).isoformat(timespec="seconds")
        ),
        "verification_method": "codex_web_search",
    }


def find_social_profile_record(site_root: Path, name: Any) -> dict[str, Any] | None:
    key = normalize_person_name(name)
    if not key:
        return None
    registry = load_social_profile_registry(site_root)
    for item in registry["people"]:
        aliases = [item.get("canonical_name"), *(item.get("aliases") or [])]
        if key in {normalize_person_name(alias) for alias in aliases}:
            return dict(item)
    return None


def upsert_social_profile_record(site_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    registry = load_social_profile_registry(site_root)
    key = normalize_person_name(record.get("canonical_name"))
    people = registry["people"]
    index = next((
        position for position, item in enumerate(people)
        if key and key in {
            normalize_person_name(item.get("canonical_name")),
            *[normalize_person_name(alias) for alias in (item.get("aliases") or [])],
        }
    ), None)
    if index is None:
        people.append(dict(record))
    else:
        previous = dict(people[index])
        if previous.get("status") == "verified" and record.get("status") != "verified":
            record = previous
        elif previous.get("status") == "verified" and record.get("status") == "verified":
            merged_profiles: list[dict[str, Any]] = []
            profile_indexes: dict[tuple[str, str], int] = {}
            for profile in [*(previous.get("profiles") or []), *(record.get("profiles") or [])]:
                if not isinstance(profile, dict):
                    continue
                service = _clean_text(profile.get("service"), 20).casefold()
                url = canonical_social_profile_url(service, profile.get("url"))
                key_profile = (service, url)
                if not url:
                    continue
                clean_profile = {
                    "service": service,
                    "url": url,
                    "display_name": _clean_text(profile.get("display_name"), 120)
                    or _clean_text(record.get("canonical_name"), 80),
                    **(
                        {"thumbnail_url": _safe_thumbnail_url(profile.get("thumbnail_url"))}
                        if _safe_thumbnail_url(profile.get("thumbnail_url"))
                        else {}
                    ),
                }
                if clean_profile.get("thumbnail_url"):
                    clean_profile["thumbnail_source_kind"] = (
                        _clean_text(profile.get("thumbnail_source_kind"), 40)
                        or "profile"
                    )
                    clean_profile["thumbnail_owner_url"] = (
                        _safe_thumbnail_url(profile.get("thumbnail_owner_url"))
                        or url
                    )
                if key_profile in profile_indexes:
                    existing_profile = merged_profiles[profile_indexes[key_profile]]
                    existing_profile.update({
                        key: value for key, value in clean_profile.items()
                        if value or key not in existing_profile
                    })
                    continue
                profile_indexes[key_profile] = len(merged_profiles)
                merged_profiles.append(clean_profile)
            merged_evidence: list[dict[str, Any]] = []
            seen_evidence: set[str] = set()
            for evidence in [*(previous.get("evidence") or []), *(record.get("evidence") or [])]:
                clean = _safe_evidence(evidence)
                if clean is None or clean["url"] in seen_evidence:
                    continue
                seen_evidence.add(clean["url"])
                merged_evidence.append(clean)
            record = {
                **previous,
                **record,
                "canonical_name": previous.get("canonical_name") or record.get("canonical_name"),
                "aliases": list(dict.fromkeys([
                    *(previous.get("aliases") or []),
                    *(record.get("aliases") or []),
                ])),
                "role": record.get("role") or previous.get("role") or "",
                "confidence": max(
                    int(previous.get("confidence") or 0),
                    int(record.get("confidence") or 0),
                ),
                "profiles": merged_profiles,
                "evidence": merged_evidence,
            }
        people[index] = dict(record)
    registry["people"] = sorted(
        people,
        key=lambda item: normalize_person_name(item.get("canonical_name")),
    )
    save_social_profile_registry(site_root, registry)
    return dict(record)


def _record_profiles_for_source(record: dict[str, Any]) -> list[dict[str, Any]]:
    if record.get("status") != "verified":
        return []
    evidence = record.get("evidence") or []
    evidence_text = " / ".join(
        _clean_text(item.get("claim"), 100)
        for item in evidence[:2]
        if isinstance(item, dict) and item.get("claim")
    )
    return [
        {
            "name": _clean_text(record.get("canonical_name"), 80),
            "role": _clean_text(record.get("role"), 80),
            "service": profile["service"],
            "url": profile["url"],
            "is_main_subject": True,
            "reason": evidence_text or _clean_text(record.get("reason"), 240),
            "verification_source": "verified_registry",
            "confidence": int(record.get("confidence") or 0),
            **(
                {"thumbnail_url": _safe_thumbnail_url(profile.get("thumbnail_url"))}
                if _safe_thumbnail_url(profile.get("thumbnail_url"))
                else {}
            ),
            **(
                {
                    "thumbnail_source_kind": (
                        _clean_text(profile.get("thumbnail_source_kind"), 40)
                        or "profile"
                    ),
                    "thumbnail_owner_url": (
                        _safe_thumbnail_url(profile.get("thumbnail_owner_url"))
                        or profile["url"]
                    ),
                }
                if _safe_thumbnail_url(profile.get("thumbnail_url"))
                else {}
            ),
        }
        for profile in (record.get("profiles") or [])
        if isinstance(profile, dict)
        and canonical_social_profile_url(profile.get("service"), profile.get("url"))
    ]


def merge_verified_social_profiles(
    *profile_groups: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Merge verified destinations without dropping richer duplicate metadata."""
    merged: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for group in profile_groups:
        for raw in group or []:
            if not isinstance(raw, dict):
                continue
            profile = dict(raw)
            service = _clean_text(profile.get("service"), 20).casefold()
            url = canonical_social_profile_url(service, profile.get("url"))
            if not url:
                continue
            profile["service"] = service
            profile["url"] = url
            key = (service, url)
            if key not in positions:
                positions[key] = len(merged)
                merged.append(profile)
                continue

            existing = merged[positions[key]]
            for field in (
                "name", "display_name", "role", "reason", "verification_source",
                "thumbnail_url", "thumbnail_source_kind", "thumbnail_owner_url",
            ):
                if not existing.get(field) and profile.get(field):
                    existing[field] = profile[field]
            existing["is_main_subject"] = bool(
                existing.get("is_main_subject") or profile.get("is_main_subject")
            )
            try:
                existing["confidence"] = max(
                    int(existing.get("confidence") or 0),
                    int(profile.get("confidence") or 0),
                )
            except (TypeError, ValueError):
                pass
    return merged


def _retry_is_due(record: dict[str, Any]) -> bool:
    retry_after = str(record.get("retry_after") or "")
    if not retry_after:
        return True
    try:
        value = datetime.fromisoformat(retry_after.replace("Z", "+00:00"))
    except ValueError:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def is_public_creator_subject(subject: dict[str, Any]) -> bool:
    if subject.get("is_public_creator") is True:
        return True
    role = _clean_text(subject.get("role"), 80).casefold()
    return any(term in role for term in PUBLIC_CREATOR_ROLES)


def _profiles_have_own_thumbnails(profiles: list[dict[str, Any]]) -> bool:
    return bool(profiles) and all(
        _safe_thumbnail_url(item.get("thumbnail_url"))
        for item in profiles
        if isinstance(item, dict) and item.get("url")
    )


def _refresh_verified_profile_images(
    site_root: Path,
    subject: dict[str, Any],
    cached: dict[str, Any],
    verifier: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    profiles = _record_profiles_for_source(cached)
    if verifier is None or _profiles_have_own_thumbnails(profiles):
        return cached
    try:
        refreshed = verifier(subject)
    except Exception:
        return cached
    if refreshed.get("status") != "verified":
        return cached
    refreshed_by_key = {
        (
            _clean_text(item.get("service"), 20).casefold(),
            canonical_social_profile_url(item.get("service"), item.get("url")),
        ): item
        for item in refreshed.get("profiles") or []
        if isinstance(item, dict)
    }
    merged_profiles: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in [*(cached.get("profiles") or []), *(refreshed.get("profiles") or [])]:
        if not isinstance(raw, dict):
            continue
        profile = dict(raw)
        service = _clean_text(profile.get("service"), 20).casefold()
        url = canonical_social_profile_url(service, profile.get("url"))
        if not url or (service, url) in seen:
            continue
        newer = refreshed_by_key.get((service, url), {})
        thumbnail = _safe_thumbnail_url(newer.get("thumbnail_url")) or _safe_thumbnail_url(
            profile.get("thumbnail_url")
        )
        if thumbnail:
            profile["thumbnail_url"] = thumbnail
        seen.add((service, url))
        merged_profiles.append(profile)
    merged = {
        **cached,
        "profiles": merged_profiles,
        "evidence": refreshed.get("evidence") or cached.get("evidence") or [],
        "reason": refreshed.get("reason") or cached.get("reason") or "",
        "verified_at": refreshed.get("verified_at") or _now_iso(),
        "verification_method": "codex_web_search_thumbnail_refresh",
    }
    upsert_social_profile_record(site_root, merged)
    return merged


def resolve_subject_social_profiles(
    site_root: Path,
    source: dict[str, Any],
    verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    existing = [
        item for item in (source.get("ai_social_profiles") or [])
        if isinstance(item, dict) and item.get("is_main_subject") is True
    ]
    if existing:
        existing = merge_verified_social_profiles(
            existing,
            registry_profiles_for_payload(site_root, {
                "title": source.get("title"),
                "tags": source.get("ai_tags") or source.get("tags") or [],
                "main_subject": source.get("ai_main_subject") or {},
            }),
        )
        source["verified_social_profiles"] = existing
        first = existing[0]
        canonical_name = _clean_text(first.get("name"), 80)
        subject = source.get("ai_main_subject")
        role = (
            _clean_text(subject.get("role"), 80)
            if isinstance(subject, dict) else ""
        )
        stored_profiles: list[dict[str, str]] = []
        for item in existing:
            service = _clean_text(item.get("service"), 20).casefold()
            url = canonical_social_profile_url(service, item.get("url"))
            if url:
                stored_profile = {
                    "service": service,
                    "url": url,
                    "display_name": _clean_text(item.get("name"), 120) or canonical_name,
                }
                thumbnail_url = _safe_thumbnail_url(item.get("thumbnail_url"))
                if thumbnail_url:
                    stored_profile["thumbnail_url"] = thumbnail_url
                    stored_profile["thumbnail_source_kind"] = (
                        _clean_text(item.get("thumbnail_source_kind"), 40)
                        or "profile"
                    )
                    stored_profile["thumbnail_owner_url"] = (
                        _safe_thumbnail_url(item.get("thumbnail_owner_url"))
                        or url
                    )
                stored_profiles.append(stored_profile)
        if canonical_name and stored_profiles:
            source_url = str(
                source.get("requested_url") or source.get("url") or ""
            ).strip()
            evidence = [{
                "url": stored_profiles[0]["url"],
                "kind": "official_profile",
                "claim": _clean_text(first.get("reason"), 240)
                or "元ページ内で本人アカウントとして確認",
            }]
            try:
                parsed_source = urlparse(source_url)
            except ValueError:
                parsed_source = None
            if parsed_source and parsed_source.scheme in {"http", "https"} and parsed_source.hostname:
                evidence.append({
                    "url": source_url,
                    "kind": "published_article",
                    "claim": "人物名と公式プロフィールの対応を確認した記事ページ",
                })
            upsert_social_profile_record(site_root, {
                "canonical_name": canonical_name,
                "aliases": [canonical_name],
                "role": role,
                "status": "verified",
                "confidence": 100 if source_url == stored_profiles[0]["url"] else 95,
                "profiles": stored_profiles,
                "evidence": evidence,
                "reason": "元ページ内リンクとCodexの主役判定を照合",
                "verified_at": _now_iso(),
                "retry_after": "",
                "verification_method": "codex_source_page",
            })
        source["identity_resolution"] = {
            "status": "verified",
            "method": "source_page",
            "message": "元ページ内の本人アカウントを確認",
        }
        return source

    subject = source.get("ai_main_subject")
    if not isinstance(subject, dict):
        subject = {}
    name = _clean_text(subject.get("name"), 80)
    if not name or not is_public_creator_subject(subject):
        source["identity_resolution"] = {
            "status": "not_applicable",
            "method": "subject_classification",
            "message": "公式アカウントを案内する人物中心の記事ではありません",
        }
        return source

    cached = find_social_profile_record(site_root, name)
    if cached and cached.get("status") == "verified":
        cached = _refresh_verified_profile_images(
            site_root, subject, cached, verifier
        )
        source["verified_social_profiles"] = _record_profiles_for_source(cached)
        source["identity_resolution"] = {
            "status": "verified",
            "method": "verified_registry",
            "message": f"検証済み人物名簿から{name}の公式アカウントを使用",
        }
        return source
    if cached and not _retry_is_due(cached):
        source["identity_resolution"] = {
            "status": str(cached.get("status") or "not_found"),
            "method": "negative_cache",
            "message": _clean_text(cached.get("reason"), 300),
            "retry_after": str(cached.get("retry_after") or ""),
        }
        return source
    if verifier is None:
        source["identity_resolution"] = {
            "status": "pending",
            "method": "not_checked",
            "message": f"{name}の公式アカウント照合が必要です",
        }
        return source

    try:
        record = verifier(subject)
        upsert_social_profile_record(site_root, record)
    except Exception as exc:
        source["identity_resolution"] = {
            "status": "error",
            "method": "codex_web_search",
            "message": _clean_text(exc, 300) or exc.__class__.__name__,
        }
        return source
    source["verified_social_profiles"] = _record_profiles_for_source(record)
    source["identity_resolution"] = {
        "status": str(record.get("status") or "not_found"),
        "method": "codex_web_search",
        "message": _clean_text(record.get("reason"), 300),
        "retry_after": str(record.get("retry_after") or ""),
    }
    return source


def resolve_performer_social_profiles(
    site_root: Path,
    source: dict[str, Any],
    verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve verified profiles for explicitly named FANZA performers too."""
    performer_names: list[str] = []
    seen_names: set[str] = set()
    for group in (source.get("ai_fanza_people") or [], source.get("fanza_people") or []):
        for item in group:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"), 80)
            key = normalize_person_name(name)
            if not key or key in seen_names:
                continue
            seen_names.add(key)
            performer_names.append(name)
    explicit = _clean_text(
        source.get("ai_fanza_performer_name") or source.get("fanza_performer_name"),
        80,
    )
    explicit_key = normalize_person_name(explicit)
    if explicit_key and explicit_key not in seen_names:
        seen_names.add(explicit_key)
        performer_names.append(explicit)
    if not performer_names:
        return source

    merged_profiles = merge_verified_social_profiles(
        [
            item for item in (source.get("verified_social_profiles") or [])
            if isinstance(item, dict)
        ]
    )
    resolved_names = {
        normalize_person_name(item.get("name") or item.get("display_name"))
        for item in merged_profiles
        if normalize_person_name(item.get("name") or item.get("display_name"))
    }
    resolved_count = 0
    for name in performer_names:
        key = normalize_person_name(name)
        if key in resolved_names:
            resolved_count += 1
            continue
        subject = {
            "name": name,
            "kind": "person",
            "role": "AV女優",
            "is_public_creator": True,
        }
        record = find_social_profile_record(site_root, name)
        if record and record.get("status") == "verified":
            record = _refresh_verified_profile_images(
                site_root, subject, record, verifier
            )
        elif record and not _retry_is_due(record):
            continue
        elif verifier is not None:
            try:
                record = verifier(subject)
                upsert_social_profile_record(site_root, record)
            except Exception:
                continue
        else:
            continue
        profiles = _record_profiles_for_source(record or {})
        if not profiles:
            continue
        merged_profiles = merge_verified_social_profiles(merged_profiles, profiles)
        resolved_names.add(key)
        resolved_count += 1
    if merged_profiles:
        source["verified_social_profiles"] = merged_profiles
    source["performer_identity_resolution"] = {
        "requested": len(performer_names),
        "resolved": resolved_count,
        "message": f"出演者{len(performer_names)}名中{resolved_count}名の公式アカウントを確認",
    }
    return source


def resolve_identified_people_social_profiles(
    site_root: Path,
    source: dict[str, Any],
    verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve accounts only for people already tied to media at 95% precision."""
    def safe_confidence(value: Any) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 0

    attributed_names = {
        normalize_person_name(item.get("person_name"))
        for item in source.get("ai_media_person_attributions") or []
        if isinstance(item, dict)
        and safe_confidence(item.get("confidence")) >= 95
        and normalize_person_name(item.get("person_name"))
    }
    people = [
        item for item in source.get("ai_identified_people") or []
        if isinstance(item, dict)
        and item.get("is_public_creator") is True
        and safe_confidence(item.get("confidence")) >= 95
        and normalize_person_name(item.get("name")) in attributed_names
    ]
    if not people:
        return source

    merged = merge_verified_social_profiles(
        item for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict)
    )
    resolved_names = {
        normalize_person_name(item.get("name") or item.get("display_name"))
        for item in merged
        if normalize_person_name(item.get("name") or item.get("display_name"))
    }
    resolved = 0
    for person in people:
        name = _clean_text(person.get("name"), 80)
        key = normalize_person_name(name)
        if key in resolved_names:
            resolved += 1
            continue
        subject = {
            "name": name,
            "kind": "person",
            "role": _clean_text(person.get("role"), 80),
            "is_public_creator": True,
        }
        record = find_social_profile_record(site_root, name)
        if record and record.get("status") == "verified":
            record = _refresh_verified_profile_images(
                site_root, subject, record, verifier
            )
        elif record and not _retry_is_due(record):
            continue
        elif verifier is not None:
            try:
                record = verifier(subject)
                upsert_social_profile_record(site_root, record)
            except Exception:
                continue
        else:
            continue
        profiles = _record_profiles_for_source(record or {})
        if not profiles:
            continue
        merged = merge_verified_social_profiles(merged, profiles)
        resolved_names.add(key)
        resolved += 1
    if merged:
        source["verified_social_profiles"] = merged
    source["identified_people_identity_resolution"] = {
        "requested": len(people),
        "resolved": resolved,
        "minimum_confidence": 95,
        "message": f"画像対応済み人物{len(people)}名中{resolved}名の公式アカウントを確認",
    }
    return source


def enrich_source_profile_thumbnails(
    site_root: Path,
    source: dict[str, Any],
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Attach each destination page's own image to verified profile records."""
    fetch = fetcher or fetch_profile_thumbnail
    x_info = source.get("x_info") if isinstance(source.get("x_info"), dict) else {}
    x_username = _clean_text(x_info.get("username"), 30).lstrip("@").casefold()
    x_thumbnail = _safe_thumbnail_url(x_info.get("profile_image_url"))
    fetched: dict[str, str] = {}
    hub_thumbnails: dict[str, tuple[str, str]] = {}
    changed_names: set[str] = set()

    for field in ("ai_social_profiles", "verified_social_profiles"):
        raw_profiles = source.get(field)
        if not isinstance(raw_profiles, list):
            continue
        enriched: list[Any] = []
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                enriched.append(raw)
                continue
            profile = dict(raw)
            service = _clean_text(profile.get("service"), 20).casefold()
            url = canonical_social_profile_url(service, profile.get("url"))
            thumbnail = _safe_thumbnail_url(profile.get("thumbnail_url"))
            thumbnail_source_kind = _clean_text(
                profile.get("thumbnail_source_kind"), 40
            )
            thumbnail_owner_url = _safe_thumbnail_url(
                profile.get("thumbnail_owner_url")
            )
            if not thumbnail and service == "x" and x_thumbnail:
                parsed = urlparse(url) if url else None
                username = (
                    parsed.path.strip("/").split("/", 1)[0].casefold()
                    if parsed else ""
                )
                if not x_username or username == x_username:
                    thumbnail = x_thumbnail
                    thumbnail_source_kind = "profile"
                    thumbnail_owner_url = url
            if not thumbnail and url:
                if url not in fetched:
                    fetched[url] = _safe_thumbnail_url(fetch(url))
                thumbnail = fetched[url]
                if thumbnail:
                    thumbnail_source_kind = "profile"
                    thumbnail_owner_url = url
            name = _clean_text(profile.get("name"), 80)
            if not thumbnail and name:
                name_key = normalize_person_name(name)
                if name_key not in hub_thumbnails:
                    hub_thumbnail = ""
                    hub_url = ""
                    record = find_social_profile_record(site_root, name)
                    for evidence in (record or {}).get("evidence") or []:
                        if not isinstance(evidence, dict) or evidence.get("kind") != "official_hub":
                            continue
                        candidate_url = _safe_thumbnail_url(evidence.get("url"))
                        if not candidate_url:
                            continue
                        if candidate_url not in fetched:
                            fetched[candidate_url] = _safe_thumbnail_url(fetch(candidate_url))
                        if fetched[candidate_url]:
                            hub_thumbnail = fetched[candidate_url]
                            hub_url = candidate_url
                            break
                    hub_thumbnails[name_key] = (hub_thumbnail, hub_url)
                thumbnail, thumbnail_owner_url = hub_thumbnails[name_key]
                if thumbnail:
                    thumbnail_source_kind = "official_hub_profile"
            if thumbnail:
                profile["thumbnail_url"] = thumbnail
                profile["thumbnail_source_kind"] = thumbnail_source_kind or "profile"
                profile["thumbnail_owner_url"] = thumbnail_owner_url or url
                changed_names.add(name)
            enriched.append(profile)
        source[field] = enriched

    for name in filter(None, changed_names):
        record = find_social_profile_record(site_root, name)
        if not record or record.get("status") != "verified":
            continue
        profile_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for field in ("ai_social_profiles", "verified_social_profiles"):
            for profile in source.get(field) or []:
                if not isinstance(profile, dict):
                    continue
                service = _clean_text(profile.get("service"), 20).casefold()
                url = canonical_social_profile_url(service, profile.get("url"))
                if url:
                    profile_by_key[(service, url)] = profile
        updated_profiles: list[dict[str, Any]] = []
        for raw in record.get("profiles") or []:
            if not isinstance(raw, dict):
                continue
            profile = dict(raw)
            service = _clean_text(profile.get("service"), 20).casefold()
            url = canonical_social_profile_url(service, profile.get("url"))
            enriched = profile_by_key.get((service, url), {})
            thumbnail = _safe_thumbnail_url(enriched.get("thumbnail_url"))
            if thumbnail:
                profile["thumbnail_url"] = thumbnail
                profile["thumbnail_source_kind"] = (
                    _clean_text(enriched.get("thumbnail_source_kind"), 40)
                    or "profile"
                )
                profile["thumbnail_owner_url"] = (
                    _safe_thumbnail_url(enriched.get("thumbnail_owner_url"))
                    or url
                )
            updated_profiles.append(profile)
        record["profiles"] = updated_profiles
        upsert_social_profile_record(site_root, record)
    return source


def registry_profiles_for_payload(site_root: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    registry = load_social_profile_registry(site_root)
    tags = {
        normalize_person_name(tag) for tag in (payload.get("tags") or [])
        if normalize_person_name(tag)
    }
    title_key = normalize_person_name(payload.get("title"))
    subject = payload.get("main_subject")
    subject_name = (
        normalize_person_name(subject.get("name")) if isinstance(subject, dict) else ""
    )
    matched_profiles: list[dict[str, Any]] = []
    for record in registry["people"]:
        aliases = {
            normalize_person_name(alias)
            for alias in [record.get("canonical_name"), *(record.get("aliases") or [])]
            if normalize_person_name(alias)
        }
        matched = bool(subject_name and subject_name in aliases) or bool(tags & aliases)
        if not matched:
            matched = any(len(alias) >= 3 and alias in title_key for alias in aliases)
        if not matched:
            continue
        matched_profiles.extend(_record_profiles_for_source(record))
    return merge_verified_social_profiles(matched_profiles)
