from __future__ import annotations

import html
import json
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse


AFFILIATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
AFFILIATE_LINK_HOSTS = {
    "al.dmm.com",
    "al.dmm.co.jp",
    "al.fanza.co.jp",
}
TRACKING_QUERY_KEYS = {
    "af_id",
    "affiliate_id",
    "ch",
    "ch_id",
    "_gl",
    "_fplc",
}
FANZA_AFFILIATE_HOME = "https://affiliate.dmm.com/"


class FanzaAffiliateConfigurationError(ValueError):
    """The site cannot publish a FANZA promotion without its own affiliate ID."""


def _is_dmm_fanza_host(hostname: str) -> bool:
    hostname = hostname.casefold()
    return (
        hostname == "dmm.co.jp"
        or hostname.endswith(".dmm.co.jp")
        or hostname == "fanza.co.jp"
        or hostname.endswith(".fanza.co.jp")
    )


def normalize_fanza_affiliate_id(value: str, *, allow_empty: bool = False) -> str:
    """Accept either an affiliate ID or a complete DMM/FANZA affiliate link."""
    candidate = html.unescape(str(value or "").strip())
    if not candidate:
        if allow_empty:
            return ""
        raise FanzaAffiliateConfigurationError("FANZAアフィリエイトIDを入力してください")

    matches = re.findall(r"(?:[?&]|&amp;)af_id=([^&#\"'<>\s]+)", candidate, re.IGNORECASE)
    if matches:
        candidate = unquote(matches[-1]).strip()
    elif "://" in candidate:
        try:
            parsed = urlparse(candidate)
            candidate = str((parse_qs(parsed.query).get("af_id") or [""])[-1]).strip()
        except ValueError:
            candidate = ""

    if not AFFILIATE_ID_PATTERN.fullmatch(candidate):
        raise FanzaAffiliateConfigurationError(
            "アフィリエイトID、または af_id= を含むDMM生成リンクを入力してください"
        )
    return candidate


def fanza_settings_path(site_root: Path) -> Path:
    return Path(site_root) / ".article-studio" / "fanza.json"


def load_fanza_settings(site_root: Path) -> dict[str, str]:
    path = fanza_settings_path(site_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    try:
        affiliate_id = normalize_fanza_affiliate_id(
            str(value.get("affiliate_id") or ""), allow_empty=True
        )
    except FanzaAffiliateConfigurationError:
        affiliate_id = ""
    return {"affiliate_id": affiliate_id}


def save_fanza_settings(site_root: Path, affiliate_id_or_link: str) -> str:
    affiliate_id = normalize_fanza_affiliate_id(affiliate_id_or_link)
    path = fanza_settings_path(site_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps({"affiliate_id": affiliate_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return affiliate_id


def unwrap_fanza_affiliate_url(value: str) -> str:
    """Remove any publisher's tracking wrapper and keep the DMM/FANZA destination."""
    destination = html.unescape(str(value or "").strip())
    for _ in range(3):
        try:
            parsed = urlparse(destination)
        except ValueError:
            return ""
        hostname = (parsed.hostname or "").casefold()
        if hostname not in AFFILIATE_LINK_HOSTS:
            break
        target = ""
        query = parse_qs(parsed.query)
        for key in ("lurl", "url"):
            if query.get(key):
                target = unquote(str(query[key][0])).strip()
                break
        if not target or target == destination:
            return ""
        destination = target

    try:
        parsed = urlparse(destination)
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not _is_dmm_fanza_host(hostname):
        return ""

    query_items = [
        (key, item)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.casefold() not in TRACKING_QUERY_KEYS
        for item in values
    ]
    return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True), fragment=""))


def build_fanza_affiliate_url(destination: str, affiliate_id_or_link: str) -> str:
    affiliate_id = normalize_fanza_affiliate_id(affiliate_id_or_link)
    canonical = unwrap_fanza_affiliate_url(destination)
    if not canonical:
        raise FanzaAffiliateConfigurationError("FANZAの商品URLを広告リンクへ変換できません")
    return (
        "https://al.dmm.com/?lurl="
        + quote(canonical, safe="")
        + "&af_id="
        + quote(affiliate_id, safe="")
        + "&ch=link_tool&ch_id=link"
    )


def canonicalize_payload_fanza_links(payload: dict[str, Any]) -> dict[str, Any]:
    """Store product destinations without an account-specific affiliate ID."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return payload
    changed = False
    normalized_blocks: list[Any] = []
    for block in blocks:
        is_fanza_related = (
            isinstance(block, dict)
            and block.get("type") == "related_link"
            and str(block.get("affiliate_network") or "").casefold() == "fanza"
            and block.get("affiliate_eligible") is True
        )
        if not isinstance(block, dict) or not (
            block.get("type") == "product_cta" or is_fanza_related
        ):
            normalized_blocks.append(block)
            continue
        destination = unwrap_fanza_affiliate_url(str(block.get("url") or ""))
        if not destination:
            normalized_blocks.append(block)
            continue
        normalized = dict(block)
        normalized["url"] = destination
        normalized.pop("affiliate_id", None)
        normalized.pop("affiliate_status", None)
        normalized.pop("affiliate_destination", None)
        normalized_blocks.append(normalized)
        changed = changed or normalized != block
    return {**payload, "blocks": normalized_blocks} if changed else payload


def bind_payload_fanza_affiliate_links(
    payload: dict[str, Any],
    affiliate_id_or_link: str,
    *,
    require_configured: bool,
) -> dict[str, Any]:
    """Create account-owned links at render time so every generation path agrees."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return payload
    product_count = sum(
        1 for block in blocks
        if isinstance(block, dict) and block.get("type") == "product_cta"
    )
    eligible_count = sum(
        1 for block in blocks
        if isinstance(block, dict)
        and (
            block.get("type") == "product_cta"
            or (
                block.get("type") == "related_link"
                and str(block.get("affiliate_network") or "").casefold() == "fanza"
                and block.get("affiliate_eligible") is True
            )
        )
    )
    if not eligible_count:
        return payload
    affiliate_id = normalize_fanza_affiliate_id(
        affiliate_id_or_link, allow_empty=True
    )
    if product_count and require_configured and not affiliate_id:
        raise FanzaAffiliateConfigurationError(
            "FANZAアフィリエイトIDが未設定です。設定画面で一度保存してから公開してください"
        )

    bound_blocks: list[Any] = []
    changed = False
    for block in blocks:
        is_product = isinstance(block, dict) and block.get("type") == "product_cta"
        is_fanza_related = (
            isinstance(block, dict)
            and block.get("type") == "related_link"
            and str(block.get("affiliate_network") or "").casefold() == "fanza"
            and block.get("affiliate_eligible") is True
        )
        if not (is_product or is_fanza_related):
            bound_blocks.append(block)
            continue
        destination = unwrap_fanza_affiliate_url(str(block.get("url") or ""))
        if not destination:
            if require_configured and is_product:
                raise FanzaAffiliateConfigurationError(
                    "FANZAの商品URLを広告リンクへ変換できません"
                )
            invalid = dict(block)
            invalid.pop("affiliate_id", None)
            invalid.pop("affiliate_destination", None)
            invalid["affiliate_status"] = "invalid"
            bound_blocks.append(invalid)
            changed = changed or invalid != block
            continue
        bound = dict(block)
        bound["affiliate_destination"] = destination
        if affiliate_id:
            bound["url"] = build_fanza_affiliate_url(destination, affiliate_id)
            bound["affiliate_id"] = affiliate_id
            bound["affiliate_status"] = "configured"
        else:
            bound["url"] = destination
            bound.pop("affiliate_id", None)
            bound["affiliate_status"] = "missing" if is_product else "direct"
        bound_blocks.append(bound)
        changed = changed or bound != block
    return {**payload, "blocks": bound_blocks} if changed else payload


_PRODUCT_BUTTON_PATTERN = re.compile(
    r'(<a\b(?=[^>]*\bclass="[^"]*\bfanza-product-button\b[^"]*")[^>]*\bhref=")([^"]+)(")',
    re.IGNORECASE,
)


def rewrite_published_fanza_links(root: Path, affiliate_id_or_link: str) -> dict[str, int]:
    """Rewrite already-rendered product buttons without touching article content."""
    affiliate_id = normalize_fanza_affiliate_id(affiliate_id_or_link)
    article_root = Path(root) / "articles"
    scanned = changed_files = changed_links = 0
    if not article_root.is_dir():
        return {"scanned": 0, "changed_files": 0, "changed_links": 0}

    for path in article_root.glob("*.html"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "fanza-product-button" not in source:
            continue
        scanned += 1
        link_count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal link_count
            destination = unwrap_fanza_affiliate_url(html.unescape(match.group(2)))
            if not destination:
                return match.group(0)
            link_count += 1
            linked = html.escape(
                build_fanza_affiliate_url(destination, affiliate_id), quote=True
            )
            return match.group(1) + linked + match.group(3)

        updated = _PRODUCT_BUTTON_PATTERN.sub(replace, source)
        if updated == source:
            continue
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(path)
        changed_files += 1
        changed_links += link_count
    return {
        "scanned": scanned,
        "changed_files": changed_files,
        "changed_links": changed_links,
    }
