from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, ImageStat

from indanya_desktop.social_profiles import normalize_person_name


REGISTRY_VERSION = 1
REGISTRY_RELATIVE_PATH = Path(".article-studio") / "person-identity-visual-registry.json"
MAX_RECORDS = 20_000


def _registry_path(site_root: Path) -> Path:
    return Path(site_root) / REGISTRY_RELATIVE_PATH


def _load_registry(site_root: Path) -> dict[str, Any]:
    path = _registry_path(site_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    records = value.get("records") if isinstance(value, dict) else []
    return {
        "version": REGISTRY_VERSION,
        "updated_at": str(value.get("updated_at") or "") if isinstance(value, dict) else "",
        "records": [item for item in (records or []) if isinstance(item, dict)],
    }


def _save_registry(site_root: Path, registry: dict[str, Any]) -> None:
    path = _registry_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["version"] = REGISTRY_VERSION
    registry["updated_at"] = datetime.now(timezone.utc).isoformat()
    registry["records"] = list(registry.get("records") or [])[-MAX_RECORDS:]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _bits_to_hex(bits: list[bool]) -> str:
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _pixel_values(image: Image.Image) -> list[int]:
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if callable(flattened) else image.getdata())


def _fingerprint(data: bytes) -> dict[str, Any] | None:
    if not isinstance(data, bytes) or len(data) < 32:
        return None
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, ValueError):
        return None
    if image.width < 32 or image.height < 32:
        return None

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    normalized = image.resize((96, 96), resampling)
    gray = normalized.convert("L")
    dhash_image = gray.resize((9, 8), resampling)
    dhash_pixels = _pixel_values(dhash_image)
    dhash_bits = [
        dhash_pixels[row * 9 + column] > dhash_pixels[row * 9 + column + 1]
        for row in range(8)
        for column in range(8)
    ]
    ahash_image = gray.resize((8, 8), resampling)
    ahash_pixels = _pixel_values(ahash_image)
    ahash_mean = sum(ahash_pixels) / len(ahash_pixels)
    ahash_bits = [value >= ahash_mean for value in ahash_pixels]
    rgb_mean = [round(value, 2) for value in ImageStat.Stat(normalized).mean[:3]]
    gray_stddev = round(float(ImageStat.Stat(gray).stddev[0]), 2)
    return {
        "normalized_sha256": hashlib.sha256(normalized.tobytes()).hexdigest(),
        "dhash": _bits_to_hex(dhash_bits),
        "ahash": _bits_to_hex(ahash_bits),
        "aspect_ratio": round(image.width / image.height, 5),
        "rgb_mean": rgb_mean,
        "gray_stddev": gray_stddev,
        "width": image.width,
        "height": image.height,
    }


def _hamming(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def _match_score(
    current: dict[str, Any],
    known: dict[str, Any],
) -> tuple[int, str, tuple[float, ...]] | None:
    if current.get("normalized_sha256") == known.get("normalized_sha256"):
        return 100, "visual_exact_match", (0.0, 0.0, 0.0, 0.0)
    current_aspect = float(current.get("aspect_ratio") or 0)
    known_aspect = float(known.get("aspect_ratio") or 0)
    aspect_delta = abs(current_aspect - known_aspect) / max(current_aspect, known_aspect, 0.01)
    dhash_delta = _hamming(str(current.get("dhash") or ""), str(known.get("dhash") or ""))
    ahash_delta = _hamming(str(current.get("ahash") or ""), str(known.get("ahash") or ""))
    current_rgb = current.get("rgb_mean") or []
    known_rgb = known.get("rgb_mean") or []
    rgb_delta = (
        sum(abs(float(left) - float(right)) for left, right in zip(current_rgb, known_rgb))
        if len(current_rgb) == len(known_rgb) == 3
        else 999.0
    )
    current_stddev = float(current.get("gray_stddev") or 0)
    known_stddev = float(known.get("gray_stddev") or 0)
    stddev_delta = abs(current_stddev - known_stddev)
    if (
        min(current_stddev, known_stddev) >= 12
        and aspect_delta <= 0.012
        and dhash_delta <= 1
        and ahash_delta <= 2
        and rgb_delta <= 12
        and stddev_delta <= 7
    ):
        return 98, "visual_near_match", (
            float(dhash_delta), float(ahash_delta), rgb_delta, aspect_delta
        )
    return None


def _source_media(source: dict[str, Any]) -> list[tuple[str, str, bytes]]:
    media: list[tuple[str, str, bytes]] = []
    for item in source.get("images") or []:
        if isinstance(item, dict) and isinstance(item.get("data"), bytes):
            media.append(("image", str(item.get("id") or ""), item["data"]))
    for item in source.get("videos") or []:
        if isinstance(item, dict) and isinstance(item.get("frame_data"), bytes):
            media.append(("video", str(item.get("id") or ""), item["frame_data"]))
    return [item for item in media if item[1]]


def _best_record(
    fingerprint: dict[str, Any], records: list[dict[str, Any]]
) -> tuple[dict[str, Any], int, str] | None:
    matches: list[tuple[tuple[float, ...], dict[str, Any], int, str]] = []
    for record in records:
        known = record.get("fingerprint")
        if not isinstance(known, dict):
            continue
        score = _match_score(fingerprint, known)
        if score is None:
            continue
        confidence, match_type, distance = score
        matches.append((distance, record, confidence, match_type))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    best_distance, best_record, confidence, match_type = matches[0]
    tied = [item for item in matches if item[0] == best_distance]
    tied_people = {
        tuple(sorted(
            normalize_person_name(person.get("name"))
            for person in item[1].get("people") or []
            if isinstance(person, dict) and normalize_person_name(person.get("name"))
        ))
        for item in tied
    }
    if len(tied_people) > 1:
        return None
    return best_record, confidence, match_type


def apply_known_visual_identity_matches(
    site_root: Path,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Reuse only whole-image or whole-frame matches from prior verified media."""
    registry = _load_registry(site_root)
    records = registry.get("records") or []
    if not records:
        return source

    matches: list[dict[str, Any]] = []
    people = [
        dict(item) for item in source.get("ai_identified_people") or []
        if isinstance(item, dict)
    ]
    attributions = [
        dict(item) for item in source.get("ai_media_person_attributions") or []
        if isinstance(item, dict)
    ]
    profiles = [
        dict(item) for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict)
    ]
    people_keys = {normalize_person_name(item.get("name")) for item in people}
    profile_keys = {
        (normalize_person_name(item.get("name") or item.get("display_name")), str(item.get("service") or ""))
        for item in profiles
    }

    for media_type, media_id, data in _source_media(source):
        fingerprint = _fingerprint(data)
        if fingerprint is None:
            continue
        matched = _best_record(fingerprint, records)
        if matched is None:
            continue
        record, confidence, match_type = matched
        for person in record.get("people") or []:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            person_key = normalize_person_name(name)
            if not person_key:
                continue
            evidence_types = [match_type, "verified_visual_registry"]
            matches.append({
                "media_type": media_type,
                "media_id": media_id,
                "person_name": name,
                "confidence": confidence,
                "match_type": match_type,
                "registry_record_id": str(record.get("id") or ""),
            })
            if person_key not in people_keys:
                people_keys.add(person_key)
                people.append({
                    "name": name,
                    "role": str(person.get("role") or ""),
                    "is_public_creator": person.get("is_public_creator") is True,
                    "confidence": confidence,
                    "evidence_types": evidence_types,
                    "reason": "過去に公式情報で確定した同一素材の画像指紋が一致",
                })
            attributions.append({
                "person_name": name,
                "image_ids": [media_id] if media_type == "image" else [],
                "video_ids": [media_id] if media_type == "video" else [],
                "confidence": confidence,
                "evidence_types": evidence_types,
                "reason": "過去に公式情報で確定した同一素材の画像指紋が一致",
            })
            for profile in record.get("profiles") or []:
                if not isinstance(profile, dict):
                    continue
                profile_key = (
                    normalize_person_name(profile.get("name") or profile.get("display_name")),
                    str(profile.get("service") or ""),
                )
                if profile_key[0] == person_key and profile_key not in profile_keys:
                    profile_keys.add(profile_key)
                    profiles.append(dict(profile))

    if matches:
        source["visual_identity_matches"] = matches
        source["ai_identified_people"] = people
        source["ai_media_person_attributions"] = attributions
        source["verified_social_profiles"] = profiles
    return source


def record_verified_visual_identities(
    site_root: Path,
    source: dict[str, Any],
) -> int:
    """Store fingerprints only after the 95% identity gate has accepted them."""
    attributions = [
        item for item in source.get("media_person_attributions") or []
        if isinstance(item, dict) and int(item.get("confidence") or 0) >= 95
    ]
    if not attributions:
        return 0
    people_by_key = {
        normalize_person_name(item.get("name")): item
        for item in source.get("identified_people") or []
        if isinstance(item, dict) and normalize_person_name(item.get("name"))
    }
    profiles = [
        dict(item) for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict) and int(item.get("confidence") or 0) >= 95
    ]
    attributed_by_media: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for attribution in attributions:
        person = people_by_key.get(normalize_person_name(attribution.get("person_name")))
        if not person:
            continue
        for image_id in attribution.get("image_ids") or []:
            attributed_by_media.setdefault(("image", str(image_id)), []).append(person)
        for video_id in attribution.get("video_ids") or []:
            attributed_by_media.setdefault(("video", str(video_id)), []).append(person)
    if not attributed_by_media:
        return 0

    registry = _load_registry(site_root)
    records = registry.get("records") or []
    records_by_sha = {
        str((item.get("fingerprint") or {}).get("normalized_sha256") or ""): item
        for item in records
        if isinstance(item, dict)
    }
    recorded = 0
    for media_type, media_id, data in _source_media(source):
        media_people = attributed_by_media.get((media_type, media_id), [])
        if not media_people:
            continue
        fingerprint = _fingerprint(data)
        if fingerprint is None:
            continue
        digest = str(fingerprint["normalized_sha256"])
        record = records_by_sha.get(digest)
        if record is None:
            record = {
                "id": hashlib.sha1(digest.encode("ascii")).hexdigest()[:16],
                "fingerprint": fingerprint,
                "people": [],
                "profiles": [],
                "source_urls": [],
                "updated_at": "",
            }
            records.append(record)
            records_by_sha[digest] = record
        record_people = {
            normalize_person_name(item.get("name")): item
            for item in record.get("people") or []
            if isinstance(item, dict) and normalize_person_name(item.get("name"))
        }
        for person in media_people:
            key = normalize_person_name(person.get("name"))
            record_people[key] = {
                "name": str(person.get("name") or ""),
                "role": str(person.get("role") or ""),
                "is_public_creator": person.get("is_public_creator") is True,
            }
        record["people"] = list(record_people.values())
        person_keys = set(record_people)
        record_profiles = {
            (normalize_person_name(item.get("name") or item.get("display_name")), str(item.get("service") or "")): item
            for item in record.get("profiles") or []
            if isinstance(item, dict)
        }
        for profile in profiles:
            key = normalize_person_name(profile.get("name") or profile.get("display_name"))
            if key in person_keys:
                record_profiles[(key, str(profile.get("service") or ""))] = profile
        record["profiles"] = list(record_profiles.values())
        source_url = str(source.get("url") or source.get("requested_url") or "").strip()
        record["source_urls"] = list(dict.fromkeys([
            *(record.get("source_urls") or []),
            *([source_url] if source_url else []),
        ]))[-10:]
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        recorded += 1
    if recorded:
        registry["records"] = records
        _save_registry(site_root, registry)
    return recorded


__all__ = [
    "apply_known_visual_identity_matches",
    "record_verified_visual_identities",
]
