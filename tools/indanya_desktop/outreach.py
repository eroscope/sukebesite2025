from __future__ import annotations

import html
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


JST = timezone(timedelta(hours=9))
STATUS_LABELS = {
    "candidate": "候補",
    "ready": "送信準備済み",
    "contacted": "送信済み",
    "replied": "返答あり",
    "listed": "掲載済み",
    "declined": "見送り",
}
TARGET_CATEGORIES = (
    "成人向けアンテナ",
    "成人向けまとめ",
    "相互リンク",
    "ランキング・リンク集",
    "成人向けメディア",
    "その他",
)

STARTER_TARGETS = (
    {
        "name": "エロムビ",
        "site_url": "https://eromovie-s.com/",
        "contact_url": "https://eromovie-s.com/page/webmaster.php",
        "category": "相互リンク",
        "fit_reason": "成人向け三次元サイトの相互リンクを募集",
        "notes": "掲載条件を確認し、条件に合う場合だけ連絡する",
    },
    {
        "name": "Heyuri",
        "site_url": "https://heyuri.net/index.php?p=japanese",
        "contact_url": "https://heyuri.net/index.php?p=japanese",
        "category": "ランキング・リンク集",
        "fit_reason": "日本語ページで成人向けサイトとの相互リンクを募集",
        "notes": "先方へのリンク設置条件を確認してから連絡する",
    },
    {
        "name": "リファインテイスト",
        "site_url": "https://refinetaste.jp/",
        "contact_url": "https://refinetaste.jp/",
        "category": "成人向けメディア",
        "fit_reason": "成人向けサイトとの寄稿・リンク交換を案内",
        "notes": "有料提案か相互紹介かを確認してから連絡する",
    },
)


def _state_dir(site_root: Path) -> Path:
    path = Path(site_root).resolve() / ".article-studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _targets_path(site_root: Path) -> Path:
    return _state_dir(site_root) / "outreach-targets.json"


def _profile_path(site_root: Path) -> Path:
    return _state_dir(site_root) / "outreach-profile.json"


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _http_url(value: str, field_name: str, *, required: bool = True) -> str:
    value = str(value or "").strip()
    if not value and not required:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name}は http:// または https:// から入力してください")
    return value


def default_outreach_profile(site_name: str, public_url: str) -> dict[str, str]:
    base = _http_url(public_url, "公開URL").rstrip("/") + "/"
    return {
        "site_name": str(site_name or "").strip() or "淫談屋",
        "public_url": base,
        "rss_url": base + "feed.xml",
        "operator_url": base + "partners.html",
        "contact_url": base + "contact.html",
        "logo_url": base + "assets/common/indanya-logo.png",
        "category": "成人向け画像・動画まとめ",
        "update_frequency": "毎日更新",
        "short_description": "成人向けの画像・動画・作品情報を、短いレス形式で読みやすく紹介する18歳以上限定サイトです。",
    }


def load_outreach_profile(
    site_root: Path,
    site_name: str,
    public_url: str,
) -> dict[str, str]:
    profile = default_outreach_profile(site_name, public_url)
    saved = _read_json(_profile_path(site_root), {})
    if isinstance(saved, dict):
        for key in profile:
            value = str(saved.get(key) or "").strip()
            if value:
                profile[key] = value
    return profile


def save_outreach_profile(site_root: Path, profile: dict[str, Any]) -> dict[str, str]:
    normalized = {
        "site_name": str(profile.get("site_name") or "").strip(),
        "public_url": _http_url(str(profile.get("public_url") or ""), "公開URL").rstrip("/") + "/",
        "rss_url": _http_url(str(profile.get("rss_url") or ""), "RSS URL"),
        "operator_url": _http_url(str(profile.get("operator_url") or ""), "運営者向けページ"),
        "contact_url": _http_url(str(profile.get("contact_url") or ""), "連絡先ページ"),
        "logo_url": _http_url(str(profile.get("logo_url") or ""), "ロゴURL"),
        "category": str(profile.get("category") or "").strip(),
        "update_frequency": str(profile.get("update_frequency") or "").strip(),
        "short_description": str(profile.get("short_description") or "").strip(),
    }
    for key in ("site_name", "category", "update_frequency", "short_description"):
        if not normalized[key]:
            raise ValueError("サイト名・カテゴリ・更新頻度・紹介文は必須です")
    _write_json(_profile_path(site_root), normalized)
    return normalized


def list_outreach_targets(site_root: Path) -> list[dict[str, Any]]:
    payload = _read_json(_targets_path(site_root), [])
    if not isinstance(payload, list):
        return []
    rows = [item for item in payload if isinstance(item, dict) and item.get("target_id")]
    status_order = {
        "ready": 0,
        "candidate": 1,
        "replied": 2,
        "contacted": 3,
        "listed": 4,
        "declined": 5,
    }
    return sorted(
        rows,
        key=lambda item: (
            status_order.get(str(item.get("status") or "candidate"), 9),
            str(item.get("name") or ""),
        ),
    )


def save_outreach_targets(site_root: Path, targets: list[dict[str, Any]]) -> None:
    _write_json(_targets_path(site_root), targets)


def bootstrap_outreach_targets(site_root: Path) -> int:
    path = _targets_path(site_root)
    if path.exists():
        return 0
    now = _now()
    targets = []
    for values in STARTER_TARGETS:
        targets.append({
            "target_id": secrets.token_hex(6),
            **values,
            "status": "candidate",
            "created_at": now,
            "updated_at": now,
            "contacted_at": "",
            "listed_at": "",
        })
    save_outreach_targets(site_root, targets)
    return len(targets)


def upsert_outreach_target(
    site_root: Path,
    values: dict[str, Any],
    target_id: str = "",
) -> dict[str, Any]:
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("掲載先の名前を入力してください")
    status = str(values.get("status") or "candidate")
    if status not in STATUS_LABELS:
        raise ValueError("掲載先の状態が不正です")
    category = str(values.get("category") or "その他").strip()
    if category not in TARGET_CATEGORIES:
        category = "その他"
    targets = list_outreach_targets(site_root)
    current = next((item for item in targets if item.get("target_id") == target_id), None)
    now = _now()
    if current is None:
        current = {
            "target_id": secrets.token_hex(6),
            "created_at": now,
            "contacted_at": "",
            "listed_at": "",
        }
        targets.append(current)
    current.update({
        "name": name,
        "site_url": _http_url(str(values.get("site_url") or ""), "サイトURL"),
        "contact_url": _http_url(
            str(values.get("contact_url") or ""),
            "連絡先URL",
            required=False,
        ),
        "category": category,
        "fit_reason": str(values.get("fit_reason") or "").strip(),
        "notes": str(values.get("notes") or "").strip(),
        "status": status,
        "updated_at": now,
    })
    if status in {"contacted", "replied", "listed"} and not current.get("contacted_at"):
        current["contacted_at"] = now
    if status == "listed" and not current.get("listed_at"):
        current["listed_at"] = now
    save_outreach_targets(site_root, targets)
    return current


def update_outreach_status(site_root: Path, target_id: str, status: str) -> dict[str, Any]:
    current = next(
        (item for item in list_outreach_targets(site_root) if item.get("target_id") == target_id),
        None,
    )
    if current is None:
        raise ValueError("掲載先が見つかりません")
    values = dict(current)
    values["status"] = status
    return upsert_outreach_target(site_root, values, target_id)


def remove_outreach_target(site_root: Path, target_id: str) -> None:
    targets = [
        item
        for item in list_outreach_targets(site_root)
        if item.get("target_id") != target_id
    ]
    save_outreach_targets(site_root, targets)


def outreach_message(profile: dict[str, str], target: dict[str, Any]) -> str:
    target_name = str(target.get("name") or "掲載先").strip()
    return (
        f"{target_name} 運営者様\n\n"
        "突然のご連絡失礼いたします。\n"
        f"成人向けまとめサイト「{profile['site_name']}」を運営しています。\n"
        "読者層が近いため、おすすめサイト欄または相互紹介への掲載をご相談したくご連絡しました。\n\n"
        f"サイト名：{profile['site_name']}\n"
        f"URL：{profile['public_url']}\n"
        f"RSS：{profile['rss_url']}\n"
        f"内容：{profile['short_description']}\n"
        f"更新：{profile['update_frequency']}\n"
        f"運営者向け情報：{profile['operator_url']}\n\n"
        "掲載条件に合うようでしたら、ご検討いただけますと幸いです。\n"
        "事前に必要な対応や掲載条件がありましたらお知らせください。\n"
    )


def outreach_profile_text(profile: dict[str, str]) -> str:
    return (
        f"サイト名：{profile['site_name']}\n"
        f"URL：{profile['public_url']}\n"
        f"RSS：{profile['rss_url']}\n"
        f"カテゴリ：{profile['category']}\n"
        f"更新頻度：{profile['update_frequency']}\n"
        f"紹介文：{profile['short_description']}\n"
        f"ロゴ：{profile['logo_url']}\n"
        f"連絡先：{profile['contact_url']}\n"
    )


def outreach_link_html(profile: dict[str, str]) -> str:
    name = html.escape(profile["site_name"], quote=True)
    url = html.escape(profile["public_url"], quote=True)
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>'
