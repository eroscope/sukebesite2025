from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ManagedSite:
    site_id: str
    name: str
    public_url: str
    local_path: str
    repository_url: str = ""
    provider: str = "GitHub Pages"
    github_token: str = ""

    @property
    def root(self) -> Path:
        return Path(self.local_path).expanduser().resolve()


class SiteRegistry:
    def __init__(self, default_root: Path) -> None:
        app_data = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        self.path = app_data / "IndanyaStudio" / "sites.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_root = default_root.resolve()
        self.sites: list[ManagedSite] = []
        self.active_id = ""
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        for item in raw.get("sites", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                self.sites.append(ManagedSite(**item))
            except TypeError:
                continue
        if not self.sites:
            self.sites = [ManagedSite(
                site_id="indanya",
                name="淫談屋",
                public_url="https://eroscope.github.io/sukebesite2025/",
                local_path=str(self.default_root),
                repository_url="https://github.com/eroscope/sukebesite2025",
            )]
        self.active_id = str(raw.get("active_id") or self.sites[0].site_id) if isinstance(raw, dict) else self.sites[0].site_id
        if not any(site.site_id == self.active_id for site in self.sites):
            self.active_id = self.sites[0].site_id
        self.save()

    def save(self) -> None:
        payload = {"active_id": self.active_id, "sites": [asdict(site) for site in self.sites]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @property
    def active(self) -> ManagedSite:
        return next(site for site in self.sites if site.site_id == self.active_id)

    def set_active(self, site_id: str) -> ManagedSite:
        site = next((item for item in self.sites if item.site_id == site_id), None)
        if site is None:
            raise ValueError("管理サイトが見つかりません")
        self.active_id = site_id
        self.save()
        return site

    def upsert(self, values: dict[str, str], site_id: str = "") -> ManagedSite:
        name = values.get("name", "").strip()
        public_url = values.get("public_url", "").strip()
        local_path = values.get("local_path", "").strip()
        if not name or not public_url or not local_path:
            raise ValueError("サイト名・公開URL・作業フォルダは必須です")
        root = Path(local_path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("作業フォルダが見つかりません")
        existing = next((site for site in self.sites if site.site_id == site_id), None)
        if existing:
            existing.name = name
            existing.public_url = public_url
            existing.local_path = str(root)
            existing.repository_url = values.get("repository_url", "").strip()
            existing.provider = values.get("provider", "").strip() or "GitHub Pages"
            existing.github_token = values.get("github_token", "").strip()
            site = existing
        else:
            site = ManagedSite(
                site_id=secrets.token_hex(5),
                name=name,
                public_url=public_url,
                local_path=str(root),
                repository_url=values.get("repository_url", "").strip(),
                provider=values.get("provider", "").strip() or "GitHub Pages",
                github_token=values.get("github_token", "").strip(),
            )
            self.sites.append(site)
        self.active_id = site.site_id
        self.save()
        return site

    def remove(self, site_id: str) -> None:
        if len(self.sites) == 1:
            raise ValueError("管理サイトを最低1件は残してください")
        self.sites = [site for site in self.sites if site.site_id != site_id]
        if self.active_id == site_id:
            self.active_id = self.sites[0].site_id
        self.save()
