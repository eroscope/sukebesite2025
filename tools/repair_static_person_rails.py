#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import sys
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from article_studio import _render_person_discovery_rail  # noqa: E402
from indanya_desktop.social_profiles import (  # noqa: E402
    canonical_social_profile_url,
    load_social_profile_registry,
    normalize_person_name,
)


PERSON_LINK_KINDS = {
    "official_profile",
    "official_content",
    "verified_person_search",
}
SERVICE_LABELS = {
    "x": "X",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "myfans": "MyFans",
    "fantia": "Fantia",
    "fanza": "FANZA出演作",
}
SERVICE_ORDER = {
    service: index
    for index, service in enumerate(SERVICE_LABELS)
}
GENERIC_PERSON_NAMES = {
    normalize_person_name(value)
    for value in (
        "本人",
        "紹介した人物",
        "記事の人物",
        "登場人物",
        "AV作品",
        "公式アカウント",
        "公式ページ",
    )
}
ASIDE_RE = re.compile(r"<aside\b[^>]*>[\s\S]*?</aside>", re.IGNORECASE)
PERSON_RAIL_RE = re.compile(
    r'<section class="person-discovery"(?:\s|>)[\s\S]*?</section>',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PersonIdentity:
    name: str
    role: str = ""


@dataclass
class LegacyPersonCard:
    link_kind: str = ""
    confidence: int = 0
    title: str = ""
    url: str = ""
    image_src: str = ""
    image_alt: str = ""
    action_text: str = ""
    root_classes: set[str] = field(default_factory=set)


class _LegacyCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.card = LegacyPersonCard()
        self._inside_title = 0
        self._inside_link = 0
        self._title_parts: list[str] = []
        self._link_parts: list[str] = []
        self._saw_root = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key.casefold(): str(value or "") for key, value in attrs}
        if tag.casefold() == "aside" and not self._saw_root:
            self._saw_root = True
            self.card.root_classes = set(values.get("class", "").split())
            self.card.link_kind = values.get("data-link-kind", "")
            try:
                self.card.confidence = max(
                    0, min(100, int(values.get("data-link-confidence", "0")))
                )
            except ValueError:
                self.card.confidence = 0
        elif tag.casefold() == "img" and not self.card.image_src:
            self.card.image_src = values.get("src", "")
            self.card.image_alt = values.get("alt", "")
        elif tag.casefold() == "p" and "fanza-product-title" in values.get(
            "class", ""
        ).split():
            self._inside_title += 1
        elif tag.casefold() == "a" and not self.card.url:
            self.card.url = values.get("href", "")
            self._inside_link += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "p" and self._inside_title:
            self._inside_title -= 1
        elif tag.casefold() == "a" and self._inside_link:
            self._inside_link -= 1

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_parts.append(data)
        if self._inside_link:
            self._link_parts.append(data)

    def finish(self) -> LegacyPersonCard:
        self.card.title = " ".join("".join(self._title_parts).split())
        self.card.action_text = " ".join("".join(self._link_parts).split())
        return self.card


def parse_legacy_person_card(fragment: str) -> LegacyPersonCard | None:
    parser = _LegacyCardParser()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        return None
    card = parser.finish()
    if (
        "article-destination" not in card.root_classes
        or card.link_kind not in PERSON_LINK_KINDS
        or not card.url
    ):
        return None
    return card


def _service_for_card(card: LegacyPersonCard) -> str:
    if card.link_kind == "verified_person_search":
        return "fanza"
    hostname = (urlparse(card.url).hostname or "").casefold()
    if hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return "x"
    if hostname in {"instagram.com", "www.instagram.com"}:
        return "instagram"
    if hostname in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return "tiktok"
    if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return "youtube"
    if hostname in {"myfans.jp", "www.myfans.jp"}:
        return "myfans"
    if hostname in {"fantia.jp", "www.fantia.jp"}:
        return "fantia"
    if hostname in {"al.dmm.com", "al.fanza.co.jp"} or hostname.endswith(
        (".dmm.co.jp", ".fanza.co.jp")
    ):
        return "fanza"
    action = card.action_text.casefold()
    return next(
        (service for service, label in SERVICE_LABELS.items() if label.casefold() in action),
        "",
    )


def _canonical_card_url(service: str, url: str) -> str:
    if service in SERVICE_LABELS and service != "fanza":
        return canonical_social_profile_url(service, url) or url.rstrip("/")
    return url


def _registry_indexes(
    site_root: Path,
) -> tuple[dict[tuple[str, str], PersonIdentity], dict[str, PersonIdentity]]:
    by_url: dict[tuple[str, str], PersonIdentity] = {}
    by_name: dict[str, PersonIdentity] = {}
    for person in load_social_profile_registry(site_root).get("people") or []:
        name = str(person.get("canonical_name") or "").strip()
        if not name:
            continue
        identity = PersonIdentity(name=name, role=str(person.get("role") or "").strip())
        for alias in [name, *(person.get("aliases") or [])]:
            key = normalize_person_name(alias)
            if key:
                by_name[key] = identity
        for profile in person.get("profiles") or []:
            if not isinstance(profile, dict):
                continue
            service = str(profile.get("service") or "").casefold()
            canonical = _canonical_card_url(service, str(profile.get("url") or ""))
            if service and canonical:
                by_url[(service, canonical.rstrip("/").casefold())] = identity
    return by_url, by_name


def _title_person_name(title: str) -> str:
    for suffix in (
        "の出演作品一覧",
        "の出演作品",
        "のFANZA出演作",
        "の作品を見る",
        "のX",
        "のInstagram",
        "のTikTok",
        "のYouTube",
        "のMyFans",
        "のFantia",
        "の公式アカウント",
        "の公式ページ",
    ):
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return ""


def _profile_handle(service: str, url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if not parts:
        return ""
    handle = parts[-1] if service == "youtube" else parts[0]
    if handle.casefold() in {"channel", "user", "c"}:
        return ""
    return handle if handle.startswith("@") else f"@{handle}"


def _identity_for_card(
    card: LegacyPersonCard,
    service: str,
    by_url: dict[tuple[str, str], PersonIdentity],
    by_name: dict[str, PersonIdentity],
) -> PersonIdentity:
    canonical = _canonical_card_url(service, card.url)
    registered = by_url.get((service, canonical.rstrip("/").casefold()))
    if registered:
        return registered
    candidate = _title_person_name(card.title) or _title_person_name(card.image_alt)
    candidate_key = normalize_person_name(candidate)
    registered = by_name.get(candidate_key)
    if registered:
        return registered
    if candidate_key and candidate_key not in GENERIC_PERSON_NAMES:
        return PersonIdentity(
            name=candidate,
            role="AV出演者" if service == "fanza" else "公式アカウント",
        )
    handle = _profile_handle(service, card.url) if service != "fanza" else ""
    return PersonIdentity(
        name=handle or candidate or "登場人物",
        role="AV出演者" if service == "fanza" else "公式アカウント",
    )


def _render_compact_rail(
    cards: list[LegacyPersonCard],
    site_root: Path,
) -> tuple[str, int, list[str]]:
    by_url, by_name = _registry_indexes(site_root)
    resolved: list[tuple[LegacyPersonCard, str, PersonIdentity]] = []
    fanza_images = {
        card.image_src
        for card in cards
        if _service_for_card(card) == "fanza" and card.image_src
    }
    unresolved: list[str] = []
    for card in cards:
        service = _service_for_card(card)
        if service not in SERVICE_LABELS:
            unresolved.append(card.url)
            continue
        identity = _identity_for_card(card, service, by_url, by_name)
        resolved.append((card, service, identity))

    identities: dict[str, PersonIdentity] = {}
    for _card, _service, identity in resolved:
        key = normalize_person_name(identity.name)
        identities.setdefault(key, identity)

    payload: dict[str, Any] = {
        "title": "",
        "identified_people": [
            {
                "name": identity.name,
                "role": identity.role,
                "confidence": 100,
            }
            for identity in identities.values()
        ],
        "verified_social_profiles": [],
    }
    blocks: list[dict[str, Any]] = []
    seen_links: set[tuple[str, str, str]] = set()
    for card, service, identity in sorted(
        resolved,
        key=lambda item: SERVICE_ORDER.get(item[1], 99),
    ):
        url = _canonical_card_url(service, card.url)
        identity_key = normalize_person_name(identity.name)
        link_key = (identity_key, service, url.rstrip("/").casefold())
        if link_key in seen_links:
            continue
        seen_links.add(link_key)
        thumbnail = ""
        if service != "fanza" and card.image_src not in fanza_images:
            thumbnail = card.image_src
        block = {
            "type": "related_link",
            "url": url,
            "title": card.title or f"{identity.name}の{SERVICE_LABELS[service]}",
            "provider": service,
            "person_name": identity.name,
            "link_kind": card.link_kind,
            "match_confidence": max(95, card.confidence),
        }
        if thumbnail:
            block.update({
                "thumbnail_url": thumbnail,
                "thumbnail_source_kind": "profile",
                "thumbnail_owner_url": url,
            })
        blocks.append(block)
        if service != "fanza":
            payload["verified_social_profiles"].append({
                "name": identity.name,
                "role": identity.role,
                "service": service,
                "url": url,
                "confidence": max(95, card.confidence),
                "verification_status": "verified",
            })
    rail = _render_person_discovery_rail(payload, blocks, {}, preview=False)
    return rail, len(identities), unresolved


def migrate_article_html(
    source: str,
    site_root: Path,
) -> tuple[str, dict[str, Any]]:
    matches: list[tuple[re.Match[str], LegacyPersonCard]] = []
    for match in ASIDE_RE.finditer(source):
        card = parse_legacy_person_card(match.group(0))
        if card is not None:
            matches.append((match, card))
    if not matches:
        return source, {
            "legacy_cards": 0,
            "person_cards": 0,
            "unresolved": [],
        }
    if PERSON_RAIL_RE.search(source):
        return source, {
            "legacy_cards": len(matches),
            "person_cards": 0,
            "unresolved": [],
            "mixed_layout": True,
        }
    rail, person_cards, unresolved = _render_compact_rail(
        [card for _match, card in matches], site_root
    )
    if not rail:
        return source, {
            "legacy_cards": len(matches),
            "person_cards": 0,
            "unresolved": unresolved,
        }
    parts: list[str] = []
    cursor = 0
    for index, (match, _card) in enumerate(matches):
        parts.append(source[cursor:match.start()])
        if index == 0:
            parts.append(rail)
        cursor = match.end()
    parts.append(source[cursor:])
    updated = "".join(parts)
    return updated, {
        "legacy_cards": len(matches),
        "person_cards": person_cards,
        "unresolved": unresolved,
    }


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    temporary.replace(path)


def migrate_site_person_rails(
    site_root: Path,
    *,
    apply: bool = False,
    slugs: set[str] | None = None,
) -> dict[str, Any]:
    stats: Counter[str] = Counter()
    changed: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for path in sorted((site_root / "articles").glob("*.html")):
        if slugs and path.stem not in slugs:
            continue
        stats["articles_scanned"] += 1
        source = path.read_text(encoding="utf-8")
        updated, detail = migrate_article_html(source, site_root)
        legacy_cards = int(detail.get("legacy_cards") or 0)
        if not legacy_cards:
            continue
        stats["articles_with_legacy_cards"] += 1
        stats["legacy_cards_found"] += legacy_cards
        stats["person_cards_built"] += int(detail.get("person_cards") or 0)
        if detail.get("mixed_layout"):
            stats["mixed_layout_skipped"] += 1
        if detail.get("unresolved"):
            unresolved.append({
                "slug": path.stem,
                "urls": detail["unresolved"],
            })
        if updated == source:
            continue
        stats["articles_changed"] += 1
        changed.append(path.stem)
        if apply:
            _write_atomic(path, updated)
    return {
        **dict(stats),
        "applied": apply,
        "changed_slugs": changed,
        "unresolved": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="旧式の人物リンクを人物単位の横カードへ統一します"
    )
    parser.add_argument("--site-root", type=Path, default=TOOLS_ROOT.parent)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--slug", action="append", default=[])
    args = parser.parse_args()
    result = migrate_site_person_rails(
        args.site_root.resolve(),
        apply=args.apply,
        slugs=set(args.slug) or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("mixed_layout_skipped") or result.get("unresolved") else 0


if __name__ == "__main__":
    raise SystemExit(main())
