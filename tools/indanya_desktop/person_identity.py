from __future__ import annotations

import re
from typing import Any

from indanya_desktop.social_profiles import normalize_person_name


MIN_IDENTITY_CONFIDENCE = 95
ALLOWED_EVIDENCE_TYPES = {
    "headline",
    "caption",
    "alt",
    "link_text",
    "official_profile",
    "official_page",
    "product_credit",
    "source_metadata",
    "visual_exact_match",
    "visual_near_match",
    "verified_visual_registry",
}
AUTHORITATIVE_EVIDENCE_TYPES = {
    "official_profile",
    "official_page",
    "product_credit",
    "visual_exact_match",
    "visual_near_match",
    "verified_visual_registry",
}
CANDIDATE_EVIDENCE_TYPES = {
    "headline",
    "caption",
    "alt",
    "link_text",
    "source_metadata",
    "watermark_ocr",
    "filename_clue",
    "web_search_result",
    "reverse_image_result",
    "video_frame_match",
}


def _clean_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _contains_name(value: Any, person_key: str) -> bool:
    return bool(person_key and person_key in normalize_person_name(value))


def _person_name_keys(value: Any) -> set[str]:
    """Return a full name and explicit parenthetical aliases only."""
    raw = _clean_text(value, 120)
    keys = {normalize_person_name(raw)}
    base = re.sub(r"[（(［\[].*?[）)］\]]", "", raw).strip()
    if normalize_person_name(base):
        keys.add(normalize_person_name(base))
    for alias in re.findall(r"[（(［\[]([^）)］\]]+)[）)］\]]", raw):
        key = normalize_person_name(alias)
        if len(key) >= 2:
            keys.add(key)
    return {key for key in keys if key}


def _contains_person_name(value: Any, person_name: Any) -> bool:
    haystack = normalize_person_name(value)
    return bool(haystack and any(key in haystack for key in _person_name_keys(person_name)))


def _verified_account_groups(source: dict[str, Any], person_name: Any) -> set[str]:
    person_keys = _person_name_keys(person_name)
    groups: set[str] = set()
    for profile in source.get("verified_social_profiles") or []:
        if not isinstance(profile, dict) or _confidence(profile.get("confidence")) < 95:
            continue
        if not person_keys.intersection(
            _person_name_keys(profile.get("name") or profile.get("display_name"))
        ):
            continue
        service = str(profile.get("service") or "").casefold()
        url = str(profile.get("url") or "").strip()
        if service != "x":
            continue
        match = re.search(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([^/?#]+)", url, re.I)
        if match:
            groups.add(f"x-account:{match.group(1).casefold()}")
    return groups


def _actual_evidence_types(
    source: dict[str, Any],
    person_name: str,
    image_ids: list[str],
    video_ids: list[str],
) -> set[str]:
    """Re-check model claims against captured text and verified records."""
    person_key = normalize_person_name(person_name)
    if not person_key:
        return set()
    evidence: set[str] = set()
    if _contains_person_name(source.get("title"), person_name):
        evidence.add("headline")

    selected_images = {
        str(item.get("id") or ""): item
        for item in source.get("images") or []
        if isinstance(item, dict) and str(item.get("id") or "") in image_ids
    }
    selected_videos = {
        str(item.get("id") or ""): item
        for item in source.get("videos") or []
        if isinstance(item, dict) and str(item.get("id") or "") in video_ids
    }
    account_groups = _verified_account_groups(source, person_name)
    for item in [*selected_images.values(), *selected_videos.values()]:
        if _contains_person_name(item.get("alt"), person_name):
            evidence.add("alt")
        if _contains_person_name(
            item.get("caption") or item.get("nearby_text"), person_name
        ):
            evidence.add("caption")
        if _contains_person_name(item.get("link_text"), person_name):
            evidence.add("link_text")
        if str(item.get("ai_content_group") or "").casefold() in account_groups:
            evidence.add("official_profile")
            evidence.add("source_metadata")

    if any(
        normalize_person_name(item.get("name") or item.get("display_name"))
        == person_key
        and _confidence(item.get("confidence")) >= MIN_IDENTITY_CONFIDENCE
        for item in source.get("verified_social_profiles") or []
        if isinstance(item, dict)
    ):
        evidence.add("official_profile")

    subject = source.get("ai_main_subject") or source.get("main_subject") or {}
    resolution = source.get("identity_resolution") or {}
    if (
        isinstance(subject, dict)
        and isinstance(resolution, dict)
        and normalize_person_name(subject.get("name")) == person_key
        and str(resolution.get("status") or "").casefold() == "verified"
    ):
        evidence.add("official_page")

    credited_names = [
        source.get("fanza_performer_name"),
        source.get("ai_fanza_performer_name"),
        *(source.get("fanza_people") or []),
        *(source.get("ai_fanza_people") or []),
    ]
    for item in credited_names:
        name = item.get("name") if isinstance(item, dict) else item
        if normalize_person_name(name) == person_key:
            evidence.add("product_credit")
            break

    if any(
        _contains_name(source.get(field), person_key)
        for field in ("description", "author", "source_label")
    ):
        evidence.add("source_metadata")

    selected_media = {*(str(value) for value in image_ids), *(str(value) for value in video_ids)}
    for match in source.get("visual_identity_matches") or []:
        if not isinstance(match, dict):
            continue
        if (
            normalize_person_name(match.get("person_name")) != person_key
            or str(match.get("media_id") or "") not in selected_media
            or _confidence(match.get("confidence")) < MIN_IDENTITY_CONFIDENCE
        ):
            continue
        match_type = str(match.get("match_type") or "").casefold()
        if match_type in {"visual_exact_match", "visual_near_match"}:
            evidence.add(match_type)
            evidence.add("verified_visual_registry")
    return evidence


def _profile_people(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for profile in source.get("verified_social_profiles") or []:
        if not isinstance(profile, dict):
            continue
        name = _clean_text(profile.get("name") or profile.get("display_name"), 80)
        key = normalize_person_name(name)
        confidence = _confidence(profile.get("confidence"))
        if not key or confidence < MIN_IDENTITY_CONFIDENCE:
            continue
        current = people.get(key)
        if current is None or confidence > current["confidence"]:
            people[key] = {
                "name": name,
                "role": _clean_text(profile.get("role"), 80),
                "is_public_creator": True,
                "confidence": confidence,
                "evidence_types": ["official_profile"],
                "reason": _clean_text(profile.get("reason"), 300),
            }
    return people


def _validated_people(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    people = _profile_people(source)
    for raw in source.get("ai_identified_people") or source.get("identified_people") or []:
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("name"), 80)
        key = normalize_person_name(name)
        confidence = _confidence(raw.get("confidence"))
        evidence_types = list(dict.fromkeys(
            str(value or "").casefold()
            for value in raw.get("evidence_types") or []
            if str(value or "").casefold() in ALLOWED_EVIDENCE_TYPES
        ))
        if (
            not key
            or confidence < MIN_IDENTITY_CONFIDENCE
            or len(evidence_types) < 2
        ):
            continue
        person = {
            "name": name,
            "role": _clean_text(raw.get("role"), 80),
            "is_public_creator": raw.get("is_public_creator") is True,
            "confidence": confidence,
            "evidence_types": evidence_types,
            "reason": _clean_text(raw.get("reason"), 300),
        }
        current = people.get(key)
        if current is None or confidence >= current["confidence"]:
            people[key] = person
    return people


def _main_subject_attributions(
    source: dict[str, Any],
    people: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    subject = source.get("ai_main_subject") or source.get("main_subject") or {}
    if not isinstance(subject, dict) or subject.get("kind") != "person":
        return []
    name = _clean_text(subject.get("name"), 80)
    key = normalize_person_name(name)
    person = people.get(key)
    if person is None:
        subject_keys = _person_name_keys(name)
        person = next(
            (
                candidate
                for candidate in people.values()
                if subject_keys.intersection(_person_name_keys(candidate.get("name")))
            ),
            None,
        )
    if not person or not person.get("is_public_creator"):
        return []

    if not key or not _contains_person_name(source.get("title"), name):
        return []
    image_ids: list[str] = []
    account_groups = _verified_account_groups(source, person["name"])
    for image in source.get("images") or []:
        if not isinstance(image, dict):
            continue
        image_id = _clean_text(image.get("id"), 80)
        if not image_id or str(image.get("ai_verdict") or "article") != "article":
            continue
        adjacent = " ".join(
            _clean_text(image.get(field), 500)
            for field in ("alt", "caption", "nearby_text", "link_text")
        )
        if (
            _contains_person_name(adjacent, name)
            or str(image.get("ai_content_group") or "").casefold() in account_groups
        ):
            image_ids.append(image_id)
    if not image_ids:
        return []
    evidence_types = ["headline", "alt", "official_profile", "source_metadata"]
    return [{
        "person_name": person["name"],
        "image_ids": list(dict.fromkeys(image_ids)),
        "video_ids": [],
        "confidence": min(100, max(MIN_IDENTITY_CONFIDENCE, person["confidence"])),
        "evidence_types": evidence_types,
        "reason": "記事見出し、各画像の隣接テキスト、検証済み公式プロフィールが同じ人物名で一致",
    }]


def _validated_attributions(
    source: dict[str, Any],
    people: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    image_ids = {
        str(item.get("id") or "")
        for item in source.get("images") or []
        if isinstance(item, dict) and item.get("id")
    }
    video_ids = {
        str(item.get("id") or "")
        for item in source.get("videos") or []
        if isinstance(item, dict) and item.get("id")
    }
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    raw_items = [
        *(source.get("ai_media_person_attributions") or []),
        *(source.get("media_person_attributions") or []),
        *_main_subject_attributions(source, people),
    ]
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        person_name = _clean_text(raw.get("person_name"), 80)
        person_key = normalize_person_name(person_name)
        person = people.get(person_key)
        confidence = _confidence(raw.get("confidence"))
        claimed_evidence_types = list(dict.fromkeys(
            str(value or "").casefold()
            for value in raw.get("evidence_types") or []
            if str(value or "").casefold() in ALLOWED_EVIDENCE_TYPES
        ))
        if (
            not person
            or confidence < MIN_IDENTITY_CONFIDENCE
            or len(claimed_evidence_types) < 2
        ):
            continue
        matched_images = list(dict.fromkeys(
            str(value)
            for value in raw.get("image_ids") or []
            if str(value) in image_ids
        ))
        matched_videos = list(dict.fromkeys(
            str(value)
            for value in raw.get("video_ids") or []
            if str(value) in video_ids
        ))
        if not matched_images and not matched_videos:
            continue
        actual_evidence_types = _actual_evidence_types(
            source,
            person["name"],
            matched_images,
            matched_videos,
        )
        evidence_types = [
            evidence_type
            for evidence_type in claimed_evidence_types
            if evidence_type in actual_evidence_types
        ]
        if (
            len(evidence_types) < 2
            or not AUTHORITATIVE_EVIDENCE_TYPES.intersection(evidence_types)
        ):
            continue
        key = (person_key, tuple(matched_images), tuple(matched_videos))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "person_name": person["name"],
            "image_ids": matched_images,
            "video_ids": matched_videos,
            "confidence": min(confidence, person["confidence"]),
            "evidence_types": evidence_types,
            "reason": _clean_text(raw.get("reason"), 300),
        })
    return results


def apply_verified_person_identity_to_source(source: dict[str, Any]) -> dict[str, Any]:
    """Keep only identities supported by two evidence types at 95% or above."""
    people = _validated_people(source)
    attributions = _validated_attributions(source, people)
    attributed_people = {
        normalize_person_name(item["person_name"]) for item in attributions
    }
    source["identified_people"] = [
        person for key, person in people.items() if key in attributed_people
    ]
    source["media_person_attributions"] = attributions

    names_by_image: dict[str, list[str]] = {}
    for attribution in attributions:
        for image_id in attribution["image_ids"]:
            names_by_image.setdefault(image_id, []).append(attribution["person_name"])
    for image in source.get("images") or []:
        if not isinstance(image, dict):
            continue
        image_id = str(image.get("id") or "")
        names = list(dict.fromkeys(names_by_image.get(image_id, [])))
        if names:
            image["identified_people"] = names
            image["identity_confidence"] = min(
                item["confidence"]
                for item in attributions
                if image_id in item["image_ids"]
            )
        else:
            image.pop("identified_people", None)
            image.pop("identity_confidence", None)
    return source


def apply_verified_person_identity_to_payload(
    payload: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    source_to_payload_images = {
        str(item.get("source_id") or ""): str(item.get("id") or "")
        for item in payload.get("images") or []
        if isinstance(item, dict) and item.get("source_id") and item.get("id")
    }
    source_to_payload_videos = {
        str(item.get("source_id") or ""): str(item.get("id") or "")
        for item in payload.get("videos") or []
        if isinstance(item, dict) and item.get("source_id") and item.get("id")
    }
    people = [
        dict(item) for item in source.get("identified_people") or []
        if isinstance(item, dict)
        and _confidence(item.get("confidence")) >= MIN_IDENTITY_CONFIDENCE
    ]
    mapped: list[dict[str, Any]] = []
    for item in source.get("media_person_attributions") or []:
        if not isinstance(item, dict):
            continue
        image_ids = list(dict.fromkeys(
            source_to_payload_images.get(str(value), "")
            for value in item.get("image_ids") or []
            if source_to_payload_images.get(str(value), "")
        ))
        video_ids = list(dict.fromkeys(
            source_to_payload_videos.get(str(value), "")
            for value in item.get("video_ids") or []
            if source_to_payload_videos.get(str(value), "")
        ))
        if image_ids or video_ids:
            mapped.append({**item, "image_ids": image_ids, "video_ids": video_ids})

    verified_media = {
        ("image", str(media_id))
        for item in mapped
        for media_id in item.get("image_ids") or []
    } | {
        ("video", str(media_id))
        for item in mapped
        for media_id in item.get("video_ids") or []
    }
    candidate_groups: list[dict[str, Any]] = []
    seen_candidate_media: set[tuple[str, str]] = set()
    for raw_group in source.get("ai_person_identity_candidates") or source.get(
        "person_identity_candidates"
    ) or []:
        if not isinstance(raw_group, dict):
            continue
        media_type = str(raw_group.get("media_type") or "").casefold()
        source_media_id = str(raw_group.get("media_id") or "")
        payload_media_id = (
            source_to_payload_images.get(source_media_id, "")
            if media_type == "image"
            else source_to_payload_videos.get(source_media_id, "")
            if media_type == "video"
            else ""
        )
        media_key = (media_type, payload_media_id)
        if (
            not payload_media_id
            or media_key in verified_media
            or media_key in seen_candidate_media
        ):
            continue
        seen_candidate_media.add(media_key)
        candidates: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for raw_candidate in (raw_group.get("candidates") or [])[:3]:
            if not isinstance(raw_candidate, dict):
                continue
            name = _clean_text(raw_candidate.get("name"), 80)
            name_key = normalize_person_name(name)
            confidence = min(94, _confidence(raw_candidate.get("confidence")))
            evidence_types = list(dict.fromkeys(
                str(value or "").casefold()
                for value in raw_candidate.get("evidence_types") or []
                if str(value or "").casefold() in CANDIDATE_EVIDENCE_TYPES
            ))
            evidence_urls = list(dict.fromkeys(
                str(value or "").strip()
                for value in raw_candidate.get("evidence_urls") or []
                if str(value or "").strip().startswith("https://")
            ))[:4]
            reason = _clean_text(raw_candidate.get("reason"), 300)
            if (
                not name_key
                or name_key in seen_names
                or confidence < 1
                or not evidence_types
                or not reason
            ):
                continue
            seen_names.add(name_key)
            candidates.append({
                "name": name,
                "role": _clean_text(raw_candidate.get("role"), 80),
                "confidence": confidence,
                "evidence_types": evidence_types,
                "evidence_urls": evidence_urls,
                "reason": reason,
            })
        candidates.sort(key=lambda item: int(item["confidence"]), reverse=True)
        unresolved_reason = _clean_text(raw_group.get("unresolved_reason"), 300)
        if candidates or unresolved_reason:
            candidate_groups.append({
                "media_type": media_type,
                "media_id": payload_media_id,
                "candidates": candidates,
                "unresolved_reason": unresolved_reason,
            })

    subject = payload.get("main_subject")
    subject_name = _clean_text(
        subject.get("name") if isinstance(subject, dict) else "", 80
    )
    subject_key = normalize_person_name(subject_name)
    verified_subject = next(
        (
            item for item in people
            if normalize_person_name(item.get("name")) == subject_key
            and item.get("is_public_creator") is True
        ),
        None,
    )
    if (
        isinstance(subject, dict)
        and subject.get("kind") == "person"
        and verified_subject is not None
        and _contains_person_name(source.get("title"), subject_name)
    ):
        evidence_urls = list(dict.fromkeys(
            str(profile.get("url") or "").strip()
            for profile in source.get("verified_social_profiles") or []
            if isinstance(profile, dict)
            and _confidence(profile.get("confidence")) >= MIN_IDENTITY_CONFIDENCE
            and _person_name_keys(subject_name).intersection(
                _person_name_keys(profile.get("name") or profile.get("display_name"))
            )
            and str(profile.get("url") or "").strip().startswith("https://")
        ))[:4]
        source_media = [
            ("image", item, source_to_payload_images)
            for item in source.get("images") or []
            if isinstance(item, dict)
        ] + [
            ("video", item, source_to_payload_videos)
            for item in source.get("videos") or []
            if isinstance(item, dict)
        ]
        for media_type, media, id_map in source_media:
            if str(media.get("ai_verdict") or "article") != "article":
                continue
            payload_media_id = id_map.get(str(media.get("id") or ""), "")
            media_key = (media_type, payload_media_id)
            if (
                not payload_media_id
                or media_key in verified_media
                or media_key in seen_candidate_media
            ):
                continue
            seen_candidate_media.add(media_key)
            candidate_groups.append({
                "media_type": media_type,
                "media_id": payload_media_id,
                "candidates": [{
                    "name": str(verified_subject.get("name") or subject_name),
                    "role": _clean_text(verified_subject.get("role"), 80),
                    "confidence": 80,
                    "evidence_types": ["headline", "source_metadata"],
                    "evidence_urls": evidence_urls,
                    "reason": (
                        "単独人物の記事見出しと検証済み公式プロフィールは一致していますが、"
                        "この素材単体では本人と断定できる別の根拠が不足しています"
                    ),
                }],
                "unresolved_reason": "素材単体の公式表記または同一画像照合が未確認",
            })

    payload["identified_people"] = people
    payload["media_person_attributions"] = mapped
    payload["person_identity_candidates"] = candidate_groups
    names_by_image: dict[str, list[str]] = {}
    for item in mapped:
        for image_id in item.get("image_ids") or []:
            names_by_image.setdefault(image_id, []).append(str(item["person_name"]))
    for image in payload.get("images") or []:
        if not isinstance(image, dict):
            continue
        names = list(dict.fromkeys(names_by_image.get(str(image.get("id") or ""), [])))
        if names:
            image["identified_people"] = names
            image["identity_confidence"] = min(
                _confidence(item.get("confidence"))
                for item in mapped
                if str(image.get("id") or "") in item.get("image_ids", [])
            )
        else:
            image.pop("identified_people", None)
            image.pop("identity_confidence", None)

    if isinstance(subject, dict) and subject.get("kind") == "person":
        subject_key = normalize_person_name(subject.get("name"))
        if any(
            normalize_person_name(item.get("name")) == subject_key
            and item.get("is_public_creator") is True
            for item in people
        ):
            subject["is_public_creator"] = True
    payload["person_identity_gate"] = {
        "status": "verified" if mapped else "unverified",
        "minimum_confidence": MIN_IDENTITY_CONFIDENCE,
        "verified_people": len(people),
        "attributed_media": len({
            media_id
            for item in mapped
            for media_id in [*(item.get("image_ids") or []), *(item.get("video_ids") or [])]
        }),
        "method": "captured_metadata_plus_authoritative_source",
        "requires_authoritative_evidence": True,
    }
    return payload


def person_identity_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    known_people = {
        normalize_person_name(item.get("name")): item
        for item in payload.get("identified_people") or []
        if isinstance(item, dict) and normalize_person_name(item.get("name"))
    }
    for item in payload.get("media_person_attributions") or []:
        if not isinstance(item, dict):
            issues.append("invalid_person_attribution")
            continue
        key = normalize_person_name(item.get("person_name"))
        evidence_types = {
            str(value or "").casefold() for value in item.get("evidence_types") or []
            if str(value or "").casefold() in ALLOWED_EVIDENCE_TYPES
        }
        if (
            key not in known_people
            or _confidence(item.get("confidence")) < MIN_IDENTITY_CONFIDENCE
            or len(evidence_types) < 2
            or not AUTHORITATIVE_EVIDENCE_TYPES.intersection(evidence_types)
        ):
            issues.append("person_identity_below_precision_gate")
    return list(dict.fromkeys(issues))
