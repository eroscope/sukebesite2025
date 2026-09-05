from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any


REGISTRY_VERSION = 1
_LOCK = threading.RLock()


def normalize_work_title(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"(?:tv)?アニメ(?:版)?|コミカライズ版|漫画版|原作", "", text)
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", text)


def _bundled_path() -> Path:
    return Path(__file__).resolve().parents[1] / "official_work_registry.json"


def _learned_path(site_root: Path) -> Path:
    return site_root / ".article-studio" / "official-work-registry.json"


def _read_entries(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = value.get("entries") if isinstance(value, dict) else []
    return [item for item in entries if isinstance(item, dict)]


def _clean_entry(value: dict[str, Any]) -> dict[str, Any] | None:
    title = " ".join(str(value.get("title") or "").split())[:180]
    url = str(value.get("url") or "").strip()
    provider = " ".join(str(value.get("provider") or "").split())[:80]
    reason = " ".join(str(value.get("reason") or "").split())[:300]
    if not title or not url.startswith("https://") or not provider or not reason:
        return None
    aliases = list(dict.fromkeys(
        " ".join(str(item or "").split())[:180]
        for item in [title, *(value.get("aliases") or [])]
        if str(item or "").strip()
    ))
    keys = list(dict.fromkeys(filter(None, (normalize_work_title(item) for item in aliases))))
    if not keys:
        return None
    return {
        "status": "verified",
        "title": title,
        "url": url,
        "provider": provider,
        "reason": reason,
        "thumbnail_url": str(value.get("thumbnail_url") or "").strip(),
        "aliases": aliases,
        "keys": keys,
        "verified_by": str(value.get("verified_by") or "registry"),
    }


def _all_entries(site_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in [*_read_entries(_learned_path(site_root)), *_read_entries(_bundled_path())]:
        entry = _clean_entry(raw)
        if entry:
            entries.append(entry)
    return entries


def resolve_verified_official_work(
    site_root: Path,
    subject_name: Any,
) -> dict[str, Any] | None:
    key = normalize_work_title(subject_name)
    if not key:
        return None
    with _LOCK:
        for entry in _all_entries(site_root):
            if key not in entry["keys"]:
                continue
            return {
                "status": "verified",
                "title": entry["title"],
                "url": entry["url"],
                "provider": entry["provider"],
                "reason": entry["reason"],
                "thumbnail_url": entry["thumbnail_url"],
                "registry_match": True,
            }
    return None


def remember_verified_official_work(
    site_root: Path,
    subject_name: Any,
    official_work: dict[str, Any],
) -> None:
    if official_work.get("status") != "verified":
        return
    entry = _clean_entry({
        **official_work,
        "aliases": [subject_name, official_work.get("title")],
        "verified_by": "codex_web_search",
    })
    if not entry:
        return
    path = _learned_path(site_root)
    with _LOCK:
        existing = _read_entries(path)
        keys = set(entry["keys"])
        retained: list[dict[str, Any]] = []
        for raw in existing:
            current = _clean_entry(raw)
            if current and keys.intersection(current["keys"]):
                continue
            retained.append(raw)
        retained.insert(0, {
            "title": entry["title"],
            "aliases": entry["aliases"],
            "url": entry["url"],
            "provider": entry["provider"],
            "reason": entry["reason"],
            "thumbnail_url": entry["thumbnail_url"],
            "verified_by": entry["verified_by"],
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(
            json.dumps({
                "version": REGISTRY_VERSION,
                "entries": retained[:500],
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def enrich_analysis_official_work(
    site_root: Path,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    subject = analysis.get("main_subject")
    if not isinstance(subject, dict) or subject.get("kind") not in {"work", "product"}:
        return analysis
    subject_name = str(subject.get("name") or "").strip()
    if not subject_name:
        return analysis
    official_work = analysis.get("official_work")
    if isinstance(official_work, dict) and official_work.get("status") == "verified":
        remember_verified_official_work(site_root, subject_name, official_work)
        return analysis
    registered = resolve_verified_official_work(site_root, subject_name)
    if registered:
        analysis["official_work"] = registered
    return analysis
