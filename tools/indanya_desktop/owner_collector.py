from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


COLLECTOR_HOST = "127.0.0.1"
COLLECTOR_PORT = 18770
COLLECTOR_BASE_URL = f"http://{COLLECTOR_HOST}:{COLLECTOR_PORT}/v1"
COLLECTOR_SERVICE = "indanya-owner-analytics"
MAX_REQUEST_BYTES = 96 * 1024
MAX_EVENT_AGE = timedelta(days=370)
ALLOWED_EVENTS = {
    "owner_article_view",
    "owner_article_visit",
    "owner_article_pr_impression",
    "owner_article_pr_click",
}


def _app_data_dir() -> Path:
    configured = os.environ.get("INDANYA_APP_DATA", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    app_data = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return app_data / "IndanyaStudio"


def owner_database_path() -> Path:
    configured = os.environ.get("INDANYA_OWNER_ANALYTICS_DB", "").strip()
    return Path(configured).expanduser().resolve() if configured else _app_data_dir() / "owner-analytics-v1.sqlite3"


def normalize_public_url(public_url: str) -> str:
    parts = urlsplit(str(public_url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = "/" + parts.path.strip("/") if parts.path.strip("/") else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}/"


def site_key_for_public_url(public_url: str) -> str:
    normalized = normalize_public_url(public_url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24] if normalized else ""


def _origin_for_url(public_url: str) -> str:
    parts = urlsplit(normalize_public_url(public_url))
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _registry_sites(default_root: Path | None = None) -> list[dict[str, str]]:
    path = _app_data_dir() / "sites.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    result: list[dict[str, str]] = []
    for item in payload.get("sites", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        public_url = normalize_public_url(str(item.get("public_url") or ""))
        local_path = str(item.get("local_path") or "").strip()
        site_key = site_key_for_public_url(public_url)
        if public_url and local_path and site_key:
            result.append({"site_key": site_key, "public_url": public_url, "local_path": local_path})
    if default_root:
        identity_path = default_root / ".article-studio" / "analytics-owner-v2.json"
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            identity = {}
        public_url = normalize_public_url(str(identity.get("public_url") or ""))
        site_key = site_key_for_public_url(public_url)
        if public_url and site_key and not any(item["site_key"] == site_key for item in result):
            result.append({"site_key": site_key, "public_url": public_url, "local_path": str(default_root)})
    return result


def _site_record(site_key: str, default_root: Path | None = None) -> dict[str, str] | None:
    return next((item for item in _registry_sites(default_root) if item["site_key"] == site_key), None)


def validate_preflight_origin(origin: str, default_root: Path | None = None) -> str:
    cleaned = _clean(origin, 300)
    if not cleaned or not any(
        cleaned == _origin_for_url(site["public_url"])
        for site in _registry_sites(default_root)
    ):
        raise PermissionError("送信元サイトを確認できません")
    return cleaned


def _connect() -> sqlite3.Connection:
    path = owner_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS browser_sessions (
            session_hash TEXT PRIMARY KEY,
            site_key TEXT NOT NULL,
            browser_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS browser_sessions_site_idx
            ON browser_sessions(site_key, last_seen_at);
        CREATE TABLE IF NOT EXISTS owner_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_key TEXT NOT NULL,
            event_id TEXT NOT NULL,
            session_hash TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_time TEXT NOT NULL,
            page_path TEXT NOT NULL DEFAULT '',
            page_title TEXT NOT NULL DEFAULT '',
            content_group TEXT NOT NULL DEFAULT '',
            promotion_id TEXT NOT NULL DEFAULT '',
            promotion_name TEXT NOT NULL DEFAULT '',
            pr_kind TEXT NOT NULL DEFAULT '',
            link_domain TEXT NOT NULL DEFAULT '',
            link_url TEXT NOT NULL DEFAULT '',
            referrer TEXT NOT NULL DEFAULT '',
            device_category TEXT NOT NULL DEFAULT '',
            operating_system TEXT NOT NULL DEFAULT '',
            browser TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL,
            UNIQUE(site_key, event_id)
        );
        CREATE INDEX IF NOT EXISTS owner_events_site_time_idx
            ON owner_events(site_key, event_time);
        CREATE INDEX IF NOT EXISTS owner_events_site_name_idx
            ON owner_events(site_key, event_name, event_time);
        """
    )
    return connection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = _utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    now = _utc_now()
    if parsed > now + timedelta(minutes=5) or parsed < now - MAX_EVENT_AGE:
        parsed = now
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean(value: object, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]


def register_browser(site_key: str, owner_token: str, browser_label: str, default_root: Path | None = None) -> str:
    site = _site_record(site_key, default_root)
    if not site:
        raise PermissionError("管理サイトを確認できません")
    identity_path = Path(site["local_path"]) / ".article-studio" / "analytics-owner-v2.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermissionError("管理者設定を確認できません") from exc
    expected = str(identity.get("token_hash") or "")
    actual = hashlib.sha256(str(owner_token or "").encode("utf-8")).hexdigest()
    if not expected or not secrets.compare_digest(expected, actual):
        raise PermissionError("管理者登録URLを確認できません")
    session_token = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    now = _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO browser_sessions(session_hash, site_key, browser_label, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_hash, site_key, _clean(browser_label, 120), now, now),
        )
        connection.commit()
    finally:
        connection.close()
    return session_token


def record_events(site_key: str, session_token: str, events: object) -> tuple[int, list[str]]:
    session_hash = hashlib.sha256(str(session_token or "").encode("utf-8")).hexdigest()
    if not isinstance(events, list) or not events or len(events) > 100:
        raise ValueError("送信イベントを確認できません")
    now = _utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
    accepted: list[str] = []
    connection = _connect()
    try:
        session = connection.execute(
            "SELECT site_key FROM browser_sessions WHERE session_hash = ?", (session_hash,)
        ).fetchone()
        if not session or session["site_key"] != site_key:
            raise PermissionError("このブラウザは管理者登録されていません")
        connection.execute(
            "UPDATE browser_sessions SET last_seen_at = ? WHERE session_hash = ?", (now, session_hash)
        )
        for item in events:
            if not isinstance(item, dict):
                continue
            event_id = _clean(item.get("eventId"), 80)
            event_name = _clean(item.get("eventName"), 80)
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", event_id) or event_name not in ALLOWED_EVENTS:
                continue
            values = (
                site_key,
                event_id,
                session_hash,
                event_name,
                _iso_utc(item.get("timestamp")),
                _clean(item.get("pagePath"), 500),
                _clean(item.get("pageTitle"), 200),
                _clean(item.get("contentGroup"), 100),
                _clean(item.get("promotionId"), 160),
                _clean(item.get("promotionName"), 200),
                _clean(item.get("prKind"), 80),
                _clean(item.get("linkDomain"), 200),
                _clean(item.get("linkUrl"), 1500),
                _clean(item.get("referrer"), 1500),
                _clean(item.get("deviceCategory"), 30),
                _clean(item.get("operatingSystem"), 80),
                _clean(item.get("browser"), 80),
                now,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO owner_events(
                    site_key, event_id, session_hash, event_name, event_time,
                    page_path, page_title, content_group, promotion_id, promotion_name,
                    pr_kind, link_domain, link_url, referrer, device_category,
                    operating_system, browser, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            accepted.append(event_id)
        connection.execute(
            "DELETE FROM owner_events WHERE event_time < ?",
            ((_utc_now() - MAX_EVENT_AGE).isoformat(timespec="seconds").replace("+00:00", "Z"),),
        )
        connection.commit()
    finally:
        connection.close()
    return len(accepted), accepted


def load_owner_events(site_key: str, start: datetime, end: datetime | None = None) -> list[dict[str, Any]]:
    if not site_key:
        return []
    start_utc = start.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    end_value = end or _utc_now()
    end_utc = end_value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        connection = _connect()
        try:
            rows = connection.execute(
                "SELECT * FROM owner_events WHERE site_key = ? AND event_time >= ? AND event_time <= ? "
                "ORDER BY event_time ASC, id ASC",
                (site_key, start_utc, end_utc),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


class _CollectorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], default_root: Path | None = None) -> None:
        self.default_root = default_root.resolve() if default_root else None
        super().__init__(address, _CollectorHandler)


class _CollectorHandler(BaseHTTPRequestHandler):
    server_version = "IndanyaOwnerAnalytics/1.0"

    @property
    def collector_server(self) -> _CollectorServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _message: str, *_args: object) -> None:
        return

    def _site_and_origin(self) -> tuple[dict[str, str], str]:
        site_key = _clean(self.headers.get("X-Indanya-Site"), 40)
        site = _site_record(site_key, self.collector_server.default_root)
        origin = _clean(self.headers.get("Origin"), 300)
        if not site or not origin or origin != _origin_for_url(site["public_url"]):
            raise PermissionError("送信元サイトを確認できません")
        return site, origin

    def _cors_headers(self, origin: str) -> None:
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Indanya-Site")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")

    def _json(self, payload: object, status: int = 200, origin: str = "") -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if origin:
            self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/health":
            self._json({"ok": True, "service": COLLECTOR_SERVICE, "version": 1})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self.path != "/v1/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            # CORS preflight lists custom header names but does not send their
            # values. Validate the managed origin here; POST validates site ID.
            origin = validate_preflight_origin(
                self.headers.get("Origin", ""), self.collector_server.default_root
            )
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        origin = ""
        try:
            site, origin = self._site_and_origin()
            length_text = self.headers.get("Content-Length", "")
            if not length_text.isdigit() or not 0 < int(length_text) <= MAX_REQUEST_BYTES:
                raise ValueError("送信サイズを確認できません")
            payload = json.loads(self.rfile.read(int(length_text)).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("送信内容を確認できません")
            action = str(payload.get("action") or "")
            if action == "register":
                token = register_browser(
                    site["site_key"],
                    str(payload.get("ownerToken") or ""),
                    str(payload.get("browserLabel") or ""),
                    self.collector_server.default_root,
                )
                self._json({"ok": True, "sessionToken": token}, origin=origin)
                return
            if action == "events":
                count, accepted = record_events(
                    site["site_key"], str(payload.get("sessionToken") or ""), payload.get("events")
                )
                self._json({"ok": True, "accepted": accepted, "count": count}, HTTPStatus.ACCEPTED, origin)
                return
            raise ValueError("未対応の処理です")
        except PermissionError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.FORBIDDEN, origin)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST, origin)
        except (BrokenPipeError, ConnectionResetError):
            return


@dataclass
class OwnerCollectorHandle:
    server: _CollectorServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


def collector_available(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{COLLECTOR_BASE_URL}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok") and payload.get("service") == COLLECTOR_SERVICE)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def start_owner_collector(default_root: Path | None = None) -> OwnerCollectorHandle | None:
    try:
        server = _CollectorServer((COLLECTOR_HOST, COLLECTOR_PORT), default_root)
    except OSError:
        if collector_available():
            return None
        raise
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="indanya-owner-analytics")
    thread.start()
    return OwnerCollectorHandle(server, thread)
