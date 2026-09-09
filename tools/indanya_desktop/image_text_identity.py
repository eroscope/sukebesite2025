from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from indanya_desktop.social_profiles import (
    load_social_profile_registry,
    normalize_person_name,
)


CACHE_VERSION = 1
MAX_CACHE_ENTRIES = 5000
MAX_OCR_IMAGES = 60
MAX_OCR_LINES = 48
_CACHE_LOCK = threading.Lock()
_ENGINE_LOCK = threading.Lock()
_ENGINE: Any = None

_EXPLICIT_HANDLE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])@([A-Za-z0-9](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9])?)"
)
_BARE_HANDLE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z0-9](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9]))(?![A-Za-z0-9_.])"
)
_DOMAIN_ENDINGS = {
    ".com", ".jp", ".net", ".org", ".html", ".jpg", ".jpeg", ".png", ".webp",
}
_GENERIC_HANDLE_WORDS = {
    "instagram", "twitter", "tiktok", "youtube", "official", "profile",
    "digital", "limited", "university", "photographed", "campus",
}


def _cache_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "local-image-ocr.json"


def _load_cache(site_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_cache_path(site_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    entries = value.get("entries") if isinstance(value, dict) else None
    return {
        "version": CACHE_VERSION,
        "entries": entries if isinstance(entries, dict) else {},
    }


def _save_cache(site_root: Path, cache: dict[str, Any]) -> None:
    path = _cache_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = cache.get("entries") if isinstance(cache, dict) else {}
    if not isinstance(entries, dict):
        entries = {}
    if len(entries) > MAX_CACHE_ENTRIES:
        entries = dict(list(entries.items())[-MAX_CACHE_ENTRIES:])
    payload = {"version": CACHE_VERSION, "entries": entries}
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _ocr_engine() -> Any:
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def _clean_line(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufffd", " ").split())[:300]


def _safe_score(value: Any) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    if score <= 1:
        score *= 100
    return max(0, min(100, round(score)))


def _run_ocr(data: bytes, engine_factory: Callable[[], Any] | None) -> list[dict[str, Any]]:
    engine = engine_factory() if engine_factory is not None else _ocr_engine()
    with _ENGINE_LOCK:
        result = engine(data)
    texts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    lines: list[dict[str, Any]] = []
    for index, raw_text in enumerate(texts[:MAX_OCR_LINES]):
        text = _clean_line(raw_text)
        if not text:
            continue
        confidence = _safe_score(scores[index] if index < len(scores) else 0)
        if confidence < 35:
            continue
        lines.append({"text": text, "confidence": confidence})
    return lines


def extract_public_handle_candidates(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract written public-account clues without treating them as identity proof."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    joined = " ".join(_clean_line(item.get("text")) for item in lines).casefold()
    service_hint = next((
        service for service in ("instagram", "tiktok", "youtube", "twitter")
        if service in joined
    ), "unknown")
    for line in lines:
        text = _clean_line(line.get("text"))
        confidence = _safe_score(line.get("confidence"))
        candidates = [(match.group(1), True) for match in _EXPLICIT_HANDLE_RE.finditer(text)]
        candidates.extend((match.group(1), False) for match in _BARE_HANDLE_RE.finditer(text))
        for raw_handle, explicit in candidates:
            handle = raw_handle.strip("._")
            key = handle.casefold()
            if not handle or key in seen or key in _GENERIC_HANDLE_WORDS:
                continue
            if not re.search(r"[A-Za-z]", handle):
                continue
            if not explicit and "_" not in handle and "." not in handle:
                continue
            if re.fullmatch(r"(?:no|vol|ver|part|ep|page)\.?\d+", key):
                continue
            if any(key.endswith(ending) for ending in _DOMAIN_ENDINGS):
                continue
            seen.add(key)
            results.append({
                "handle": handle,
                "written_as": f"@{handle}" if explicit else handle,
                "service_hint": service_hint,
                "confidence": confidence,
                "evidence_type": "watermark_ocr",
            })
    return results


def _profile_handle(service: Any, url: Any) -> str:
    provider = str(service or "").casefold()
    try:
        path = [part for part in urlparse(str(url or "")).path.split("/") if part]
    except ValueError:
        return ""
    if not path:
        return ""
    if provider == "fantia" and len(path) >= 2:
        return path[1].casefold()
    if provider == "youtube" and path[0].startswith("@"):
        return path[0][1:].casefold()
    return path[0].lstrip("@").casefold()


def _known_people_for_clues(
    registry: dict[str, Any],
    handles: list[dict[str, Any]],
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    wanted = {str(item.get("handle") or "").casefold() for item in handles}
    ocr_key = normalize_person_name(" ".join(
        _clean_line(item.get("text")) for item in lines
    ))
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in registry.get("people") or []:
        if not isinstance(record, dict) or record.get("status") != "verified":
            continue
        matched_profiles = [
            profile for profile in record.get("profiles") or []
            if isinstance(profile, dict)
            and _profile_handle(profile.get("service"), profile.get("url")) in wanted
        ]
        aliases = [record.get("canonical_name"), *(record.get("aliases") or [])]
        matched_names = [
            _clean_line(alias)
            for alias in aliases
            if len(normalize_person_name(alias)) >= 3
            and normalize_person_name(alias) in ocr_key
        ]
        name = _clean_line(record.get("canonical_name"))
        if not name or (not matched_profiles and not matched_names) or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        matches.append({
            "name": name,
            "role": _clean_line(record.get("role")),
            "confidence": max(95, _safe_score(record.get("confidence"))),
            "matched_profiles": [
                {"service": item.get("service"), "url": item.get("url")}
                for item in matched_profiles
            ],
            "matched_ocr_names": list(dict.fromkeys(matched_names)),
            "profiles": [
                dict(item) for item in record.get("profiles") or []
                if isinstance(item, dict) and item.get("service") and item.get("url")
            ],
        })
    return matches


def enrich_source_with_local_image_text(
    site_root: Path,
    source: dict[str, Any],
    *,
    max_images: int = MAX_OCR_IMAGES,
    engine_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Attach cached OCR clues before the single model pass."""
    result = {**source}
    images = [dict(item) if isinstance(item, dict) else item for item in source.get("images") or []]
    result["images"] = images
    registry = load_social_profile_registry(site_root)
    clues: list[dict[str, Any]] = []
    processed = 0
    cache_hits = 0
    failures = 0
    changed_cache = False

    with _CACHE_LOCK:
        cache = _load_cache(site_root)
        entries = cache["entries"]
        for image in images:
            if processed >= max(0, max_images) or not isinstance(image, dict):
                continue
            data = image.get("data")
            if not isinstance(data, bytes) or len(data) < 256:
                continue
            processed += 1
            digest = hashlib.sha256(data).hexdigest()
            cached = entries.get(digest)
            if isinstance(cached, dict) and isinstance(cached.get("lines"), list):
                lines = [dict(item) for item in cached["lines"] if isinstance(item, dict)]
                cache_hits += 1
            else:
                try:
                    lines = _run_ocr(data, engine_factory)
                except Exception as exc:
                    lines = []
                    failures += 1
                    image["local_ocr_error"] = _clean_line(exc)
                entries[digest] = {"lines": lines}
                changed_cache = True
            if not lines:
                continue
            handles = extract_public_handle_candidates(lines)
            known_people = _known_people_for_clues(registry, handles, lines)
            image["local_ocr_lines"] = lines
            image["local_ocr_text"] = " / ".join(item["text"] for item in lines)[:1800]
            if handles:
                image["local_public_handle_candidates"] = handles
            if known_people:
                image["local_known_identity_matches"] = known_people
            clues.append({
                "image_id": str(image.get("id") or ""),
                "ocr_text": image["local_ocr_text"],
                "public_handle_candidates": handles,
                "known_identity_matches": known_people,
            })
        if changed_cache:
            _save_cache(site_root, cache)

    result["local_identity_clues"] = clues
    result["local_ocr"] = {
        "status": "completed" if processed else "no_images",
        "processed_images": processed,
        "images_with_text": len(clues),
        "cache_hits": cache_hits,
        "failures": failures,
        "model_calls": 0,
    }
    return result


def apply_local_identity_matches_to_analysis(
    source: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Reuse exact, previously verified handles even when the model overlooks them."""
    result = {**analysis}
    matches_by_person: dict[str, dict[str, Any]] = {}
    for clue in source.get("local_identity_clues") or []:
        if not isinstance(clue, dict):
            continue
        image_id = str(clue.get("image_id") or "")
        if not image_id:
            continue
        for match in clue.get("known_identity_matches") or []:
            if not isinstance(match, dict):
                continue
            name = _clean_line(match.get("name"))
            key = normalize_person_name(name)
            if not key:
                continue
            person = matches_by_person.setdefault(key, {
                "name": name,
                "role": _clean_line(match.get("role")),
                "confidence": max(95, _safe_score(match.get("confidence"))),
                "image_ids": [],
                "profiles": [],
            })
            person["image_ids"].append(image_id)
            for profile in match.get("profiles") or []:
                if not isinstance(profile, dict):
                    continue
                profile_key = (
                    str(profile.get("service") or "").casefold(),
                    str(profile.get("url") or "").rstrip("/").casefold(),
                )
                if not profile_key[0] or not profile_key[1]:
                    continue
                existing_profile_keys = {
                    (
                        str(item.get("service") or "").casefold(),
                        str(item.get("url") or "").rstrip("/").casefold(),
                    )
                    for item in person["profiles"]
                }
                if profile_key not in existing_profile_keys:
                    person["profiles"].append(dict(profile))

    if not matches_by_person:
        return result

    subject = result.get("main_subject")
    if not isinstance(subject, dict):
        subject = {}
    if len(matches_by_person) == 1 and not str(subject.get("name") or "").strip():
        person = next(iter(matches_by_person.values()))
        subject = {
            "name": person["name"],
            "kind": "person",
            "role": person["role"],
            "is_public_creator": True,
            "reason": "画像内の公開IDが検証済み人物名簿の公式プロフィールと完全一致",
        }
        result["main_subject"] = subject
    subject_key = normalize_person_name(subject.get("name"))

    identified = [
        dict(item) for item in result.get("identified_people") or []
        if isinstance(item, dict)
    ]
    identified_by_name = {
        normalize_person_name(item.get("name")): index
        for index, item in enumerate(identified)
        if normalize_person_name(item.get("name"))
    }
    attributions = [
        dict(item) for item in result.get("media_person_attributions") or []
        if isinstance(item, dict)
    ]
    attribution_by_name = {
        normalize_person_name(item.get("person_name")): index
        for index, item in enumerate(attributions)
        if normalize_person_name(item.get("person_name"))
    }
    social_profiles = [
        dict(item) for item in result.get("social_profiles") or []
        if isinstance(item, dict)
    ]
    social_keys = {
        (
            str(item.get("service") or "").casefold(),
            str(item.get("url") or "").rstrip("/").casefold(),
        )
        for item in social_profiles
    }

    for key, person in matches_by_person.items():
        identified_person = {
            "name": person["name"],
            "role": person["role"],
            "is_public_creator": True,
            "confidence": person["confidence"],
            "evidence_types": ["watermark_ocr", "official_profile"],
            "reason": "画像内の公開IDと検証済み公式プロフィールURLが完全一致",
        }
        if key in identified_by_name:
            identified[identified_by_name[key]] = identified_person
        else:
            identified_by_name[key] = len(identified)
            identified.append(identified_person)

        image_ids = list(dict.fromkeys(str(value) for value in person["image_ids"] if value))
        attribution = {
            "person_name": person["name"],
            "image_ids": image_ids,
            "video_ids": [],
            "confidence": person["confidence"],
            "evidence_types": ["watermark_ocr", "official_profile"],
            "reason": "画像内の公開IDと検証済み公式プロフィールURLが完全一致",
        }
        if key in attribution_by_name:
            previous = attributions[attribution_by_name[key]]
            attribution["image_ids"] = list(dict.fromkeys([
                *(previous.get("image_ids") or []), *image_ids,
            ]))
            attribution["video_ids"] = list(previous.get("video_ids") or [])
            attributions[attribution_by_name[key]] = attribution
        else:
            attribution_by_name[key] = len(attributions)
            attributions.append(attribution)

        for profile in person["profiles"]:
            service = str(profile.get("service") or "").casefold()
            url = str(profile.get("url") or "").strip()
            profile_key = (service, url.rstrip("/").casefold())
            if not service or not url or profile_key in social_keys:
                continue
            social_keys.add(profile_key)
            social_profiles.append({
                "name": person["name"],
                "service": service,
                "url": url,
                "is_main_subject": key == subject_key,
                "reason": "画像内IDと検証済み人物名簿の公式プロフィールが一致",
                "thumbnail_url": str(profile.get("thumbnail_url") or ""),
            })

    result["identified_people"] = identified[:20]
    result["media_person_attributions"] = attributions[:20]
    result["social_profiles"] = social_profiles[:8]
    matched_media = {
        image_id
        for person in matches_by_person.values()
        for image_id in person["image_ids"]
    }
    result["person_identity_candidates"] = [
        item for item in result.get("person_identity_candidates") or []
        if not isinstance(item, dict)
        or str(item.get("media_type") or "").casefold() != "image"
        or str(item.get("media_id") or "") not in matched_media
    ]
    return result
