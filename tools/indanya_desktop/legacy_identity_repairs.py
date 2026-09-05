from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from indanya_desktop.person_identity import (
    MIN_IDENTITY_CONFIDENCE,
    apply_verified_person_identity_to_payload,
    apply_verified_person_identity_to_source,
)
from indanya_desktop.social_profiles import normalize_person_name


TRUSTED_RESOLUTION_METHODS = {
    "codex_web_search",
    "official_page",
    "official_product",
    "source_page",
    "verified_registry",
    "verified_visual_registry",
}


def _clean_text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _same_person_name(left: Any, right: Any) -> bool:
    left_key = normalize_person_name(left)
    right_key = normalize_person_name(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 3 and shorter in longer


def _direct_official_product_source(payload: dict[str, Any]) -> bool:
    source_url = _clean_text(payload.get("source_url"), 500)
    parsed = urlparse(source_url)
    host = parsed.netloc.casefold().split(":", 1)[0]
    path = parsed.path.casefold()
    if host == "video.dmm.co.jp" and path.startswith("/av/content"):
        return True
    return host.endswith("dmm.co.jp") and "/digital/videoa/" in path


def _trusted_resolution(payload: dict[str, Any]) -> bool:
    resolution = payload.get("identity_resolution") or {}
    return bool(
        isinstance(resolution, dict)
        and str(resolution.get("status") or "").casefold() == "verified"
        and str(resolution.get("method") or "").casefold()
        in TRUSTED_RESOLUTION_METHODS
    )


def _exact_product_credit(
    payload: dict[str, Any], person_keys: set[str]
) -> bool:
    for item in payload.get("blocks") or []:
        if not isinstance(item, dict) or item.get("type") != "product_cta":
            continue
        if not str(item.get("match_type") or "").startswith("exact_"):
            continue
        if _confidence(item.get("match_confidence")) < MIN_IDENTITY_CONFIDENCE:
            continue
        title_key = normalize_person_name(item.get("title"))
        if any(key and key in title_key for key in person_keys):
            return True
    for item in payload.get("related_destinations") or []:
        if not isinstance(item, dict):
            continue
        link_kind = str(item.get("link_kind") or "").casefold()
        if link_kind not in {"exact_image", "exact_product", "exact_video"}:
            continue
        if _confidence(item.get("match_confidence")) < MIN_IDENTITY_CONFIDENCE:
            continue
        title_key = normalize_person_name(item.get("title"))
        if any(key and key in title_key for key in person_keys):
            return True
    return False


def _coherent_selected_media_credit(
    payload: dict[str, Any],
    selected_image_ids: list[str],
    selected_video_ids: list[str],
    person_keys: set[str],
    profiles: list[dict[str, Any]],
) -> bool:
    """Accept a single captured media group when text or a verified handle owns it."""
    selected_ids = {*selected_image_ids, *selected_video_ids}
    media = [
        item
        for item in [*(payload.get("images") or []), *(payload.get("videos") or [])]
        if isinstance(item, dict)
        and str(item.get("id") or "") in selected_ids
        and item.get("related_thumbnail_only") is not True
    ]
    if not media or len(media) != len(selected_ids):
        return False
    if any(
        not normalize_person_name(item.get("ai_content_group")) for item in media
    ):
        return False
    groups = {
        normalize_person_name(item.get("ai_content_group"))
        for item in media
        if normalize_person_name(item.get("ai_content_group"))
    }
    if len(groups) != 1 or any(
        str(item.get("ai_verdict") or "article").casefold()
        in {"unrelated", "advertisement", "rejected"}
        for item in media
    ):
        return False

    evidence_text = " ".join([
        unquote(str(payload.get("source_url") or "")),
        *(
            _clean_text(item.get(field), 500)
            for item in media
            for field in (
                "alt", "label", "caption", "nearby_text", "link_text",
                "ai_reason", "source_url",
            )
        ),
    ])
    evidence_key = normalize_person_name(evidence_text)
    if any(key and key in evidence_key for key in person_keys):
        return True

    group_key = next(iter(groups))
    group_tokens = {
        normalize_person_name(token)
        for item in media
        for token in re.split(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠々ー]+", str(item.get("ai_content_group") or ""))
        if len(normalize_person_name(token)) >= 4
    }
    for profile in profiles:
        if _confidence(profile.get("confidence")) < 90:
            continue
        try:
            path = unquote(urlparse(str(profile.get("url") or "")).path)
        except ValueError:
            continue
        handle = normalize_person_name(path.strip("/").split("/", 1)[0])
        if not handle:
            continue
        if (
            (len(group_key) >= 4 and group_key in handle)
            or any(token in handle or handle in token for token in group_tokens)
        ):
            return True
    return False


def _body_media_ids(payload: dict[str, Any], block_type: str) -> list[str]:
    key = "image_ids" if block_type == "images" else "video_ids"
    values: list[str] = []
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != block_type:
            continue
        values.extend(str(value) for value in block.get(key) or [] if str(value))
    return list(dict.fromkeys(values))


def _source_media(
    payload: dict[str, Any],
    collection_name: str,
    selected_ids: list[str],
    person_keys: set[str],
    *,
    allow_all_selected: bool = False,
) -> list[dict[str, Any]]:
    selected = set(selected_ids)
    source_media: list[dict[str, Any]] = []
    for raw in payload.get(collection_name) or []:
        if not isinstance(raw, dict) or str(raw.get("id") or "") not in selected:
            continue
        item = copy.deepcopy(raw)
        text = " ".join(
            _clean_text(item.get(field))
            for field in ("alt", "label", "caption", "nearby_text", "link_text")
        )
        normalized_text = normalize_person_name(text)
        if not allow_all_selected and not any(
            key in normalized_text for key in person_keys if key
        ):
            continue
        item["id"] = str(item.get("source_id") or item.get("id") or "")
        item["ai_verdict"] = "article"
        if collection_name == "videos" and not item.get("alt"):
            item["alt"] = _clean_text(item.get("label"), 180)
        source_media.append(item)
    return source_media


def backfill_verified_main_subject_identity(payload: dict[str, Any]) -> bool:
    """Backfill legacy single-subject articles without trusting facial similarity.

    The repair runs only when a named public subject, a verified official profile,
    the headline, and each selected media description all agree on the same name.
    Card-only profile/product thumbnails are never included.
    """
    subject = payload.get("main_subject")
    if not isinstance(subject, dict) or subject.get("kind") != "person":
        return False
    original_person_name = _clean_text(subject.get("name"), 80)
    all_profiles = [
        copy.deepcopy(profile)
        for profile in payload.get("verified_social_profiles") or []
        if isinstance(profile, dict)
        and _same_person_name(
            profile.get("name") or profile.get("display_name"),
            original_person_name,
        )
    ]
    high_profiles = [
        profile
        for profile in all_profiles
        if _confidence(profile.get("confidence")) >= MIN_IDENTITY_CONFIDENCE
    ]
    selected_image_ids = _body_media_ids(payload, "images")
    selected_video_ids = _body_media_ids(payload, "videos")
    initial_person_keys = {
        normalize_person_name(original_person_name),
    }
    coherent_media_credit = _coherent_selected_media_credit(
        payload,
        selected_image_ids,
        selected_video_ids,
        initial_person_keys,
        all_profiles,
    )
    if coherent_media_credit and not high_profiles:
        for profile in all_profiles:
            if _confidence(profile.get("confidence")) >= 90:
                profile["confidence"] = MIN_IDENTITY_CONFIDENCE
        high_profiles = [
            profile for profile in all_profiles
            if _confidence(profile.get("confidence")) >= MIN_IDENTITY_CONFIDENCE
        ]
    canonical_profile = max(
        high_profiles or all_profiles,
        key=lambda item: _confidence(item.get("confidence")),
        default={},
    )
    profile_name = _clean_text(
        canonical_profile.get("name") or canonical_profile.get("display_name"), 80
    )
    person_name = profile_name if profile_name else original_person_name
    person_key = normalize_person_name(person_name)
    original_person_key = normalize_person_name(original_person_name)
    person_keys = {person_key, original_person_key}
    title_key = normalize_person_name(payload.get("title"))
    if not person_key or not any(key in title_key for key in person_keys if key):
        return False

    direct_product = _direct_official_product_source(payload)
    exact_product_credit = _exact_product_credit(payload, person_keys)
    trusted_resolution = _trusted_resolution(payload)
    if (
        not high_profiles
        and not trusted_resolution
        and not direct_product
        and not exact_product_credit
    ):
        return False

    source_images = _source_media(
        payload,
        "images",
        selected_image_ids,
        person_keys,
        allow_all_selected=direct_product or coherent_media_credit or exact_product_credit,
    )
    source_videos = _source_media(
        payload,
        "videos",
        selected_video_ids,
        person_keys,
        allow_all_selected=direct_product or coherent_media_credit or exact_product_credit,
    )
    if not source_images and not source_videos:
        return False

    profile_confidence = max(
        (_confidence(profile.get("confidence")) for profile in all_profiles),
        default=0,
    )
    confidence = (
        99
        if direct_product
        else 98
        if exact_product_credit
        else max(MIN_IDENTITY_CONFIDENCE, profile_confidence)
    )
    authoritative_evidence: list[str] = []
    if high_profiles:
        authoritative_evidence.append("official_profile")
    if trusted_resolution or direct_product or exact_product_credit:
        authoritative_evidence.append("official_page")
    if direct_product or exact_product_credit:
        authoritative_evidence.append("product_credit")
    evidence_types = ["headline"]
    if not direct_product:
        evidence_types.append("alt")
    evidence_types.extend(authoritative_evidence)
    source_subject = copy.deepcopy(subject)
    source_subject["name"] = person_name
    source_subject["is_public_creator"] = True
    resolution = copy.deepcopy(payload.get("identity_resolution") or {})
    if direct_product or exact_product_credit:
        resolution.update({
            "status": "verified",
            "method": "official_product",
            "message": "公式商品ページの作品名・出演者表記・商品素材で照合",
        })
    source = {
        "title": payload.get("title"),
        "description": payload.get("summary"),
        "source_label": payload.get("source_label"),
        "ai_main_subject": source_subject,
        "identity_resolution": resolution,
        "verified_social_profiles": all_profiles,
        "fanza_performer_name": (
            person_name if direct_product or exact_product_credit else ""
        ),
        "ai_identified_people": [{
            "name": person_name,
            "role": _clean_text(subject.get("role"), 80),
            "is_public_creator": True,
            "confidence": confidence,
            "evidence_types": evidence_types,
            "reason": (
                "記事見出し、選択素材、公式商品または検証済み公式情報が"
                "同じ人物を示している"
            ),
        }],
        "images": source_images,
        "videos": source_videos,
        "ai_media_person_attributions": [{
            "person_name": person_name,
            "image_ids": [str(item["id"]) for item in source_images],
            "video_ids": [str(item["id"]) for item in source_videos],
            "confidence": confidence,
            "evidence_types": evidence_types,
            "reason": (
                "記事見出し、選択素材、公式商品または検証済み公式情報が"
                "同じ人物名で一致"
            ),
        }],
    }
    apply_verified_person_identity_to_source(source)
    if not source.get("media_person_attributions"):
        return False

    before = json.dumps(
        {
            "main_subject": payload.get("main_subject"),
            "identified_people": payload.get("identified_people"),
            "media_person_attributions": payload.get("media_person_attributions"),
            "person_identity_candidates": payload.get("person_identity_candidates"),
            "person_identity_gate": payload.get("person_identity_gate"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    apply_verified_person_identity_to_payload(payload, source)
    if person_name != original_person_name:
        payload_subject = payload.get("main_subject")
        if isinstance(payload_subject, dict):
            payload_subject["name"] = person_name
    after = json.dumps(
        {
            "main_subject": payload.get("main_subject"),
            "identified_people": payload.get("identified_people"),
            "media_person_attributions": payload.get("media_person_attributions"),
            "person_identity_candidates": payload.get("person_identity_candidates"),
            "person_identity_gate": payload.get("person_identity_gate"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return before != after
