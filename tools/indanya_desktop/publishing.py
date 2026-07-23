from __future__ import annotations

import copy
import base64
import binascii
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from article_studio import JST, add_built_article, save_draft, _validate_source_url
from indanya_desktop.sites import ManagedSite


ProgressCallback = Callable[[int, str], None]
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_PUBLISH_VIDEO_BYTES = 95 * 1024 * 1024
MAX_PUBLISH_POSTER_BYTES = 12 * 1024 * 1024


def _run_git(
    arguments: list[str],
    cwd: Path | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "***")
        if "Invalid username or token" in detail or "Authentication failed" in detail:
            raise RuntimeError(
                "GitHub認証に失敗しました。管理サイト設定のGitHub Tokenを確認してください。"
                " classic tokenならrepo権限、fine-grained tokenなら対象リポジトリのContents: Read and writeが必要です。"
            )
        raise RuntimeError(f"Git処理に失敗しました: {detail or ' '.join(arguments)}")
    return completed.stdout.strip()


def _repository_url(site: ManagedSite) -> str:
    repository = site.repository_url.strip()
    if not repository:
        raise RuntimeError("管理サイトにGitHubリポジトリURLを設定してください")
    parsed = urlparse(repository)
    if parsed.scheme not in {"https", "ssh"} and not repository.startswith("git@"):
        raise RuntimeError("GitHubリポジトリURLが正しくありません")
    return repository


def _github_token(site: ManagedSite) -> str:
    return (
        getattr(site, "github_token", "").strip()
        or os.environ.get("INDANYA_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )


def _git_environment(site: ManagedSite, temporary_root: Path) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    token = _github_token(site)
    if not token:
        return None, ()
    askpass = temporary_root / "git-askpass.cmd"
    askpass.write_text(
        "@echo off\r\n"
        "echo %~1 | findstr /i \"Username\" >nul\r\n"
        "if %errorlevel%==0 (\r\n"
        "  echo x-access-token\r\n"
        ") else (\r\n"
        f"  echo {token}\r\n"
        ")\r\n",
        encoding="utf-8",
        newline="",
    )
    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env, (token,)


def _published_payload(payload: dict[str, Any]) -> dict[str, Any]:
    published = copy.deepcopy(payload)
    published["status"] = "published"
    published["editorial_status"] = "published"
    published["published_at"] = datetime.now(JST).isoformat(timespec="seconds")
    published["adult_confirmed"] = True
    published["rights_confirmed"] = True
    published["privacy_confirmed"] = True
    published["source_confirmed"] = True
    published["replace_existing"] = True
    return published


def _download_video(video: dict[str, Any], destination: Path) -> None:
    video_url = _validate_source_url(str(video.get("url") or ""))
    referer = str(video.get("referer") or "").strip()
    headers = {
        "Accept": "video/mp4,video/webm,video/*;q=0.9,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(video_url, headers=headers)
    temporary = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_PUBLISH_VIDEO_BYTES:
                    raise RuntimeError("動画が95MBを超えるためGitHub Pagesへ公開できません")
                output.write(chunk)
        if written == 0:
            raise RuntimeError("動画データを取得できませんでした")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _localize_video_poster(
    video: dict[str, Any],
    destination_base: Path,
    article_html: str,
    local_prefix: str,
) -> str:
    poster_data = str(video.get("poster_data_url") or "").strip()
    poster_url = str(video.get("poster") or "").strip()
    data_match = re.fullmatch(
        r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=\s]+)",
        poster_data,
    )
    if data_match:
        extension = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[data_match.group(1)]
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", data_match.group(2)), validate=True)
        except (ValueError, binascii.Error):
            return article_html
        if not raw or len(raw) > MAX_PUBLISH_POSTER_BYTES:
            return article_html
        destination = destination_base.with_suffix(extension)
        destination.write_bytes(raw)
        return article_html.replace(
            html.escape(poster_data, quote=True),
            f"{local_prefix}{destination.name}",
        )
    if not poster_url.startswith(("http://", "https://")):
        return article_html
    headers = {
        "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
    }
    referer = str(video.get("referer") or "").strip()
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(_validate_source_url(poster_url), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_PUBLISH_POSTER_BYTES + 1)
            content_type = str(response.headers.get("Content-Type") or "").lower()
    except (OSError, TimeoutError):
        return article_html
    if not raw or len(raw) > MAX_PUBLISH_POSTER_BYTES:
        return article_html
    extension = ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".jpg"
    destination = destination_base.with_suffix(extension)
    destination.write_bytes(raw)
    return article_html.replace(
        html.escape(poster_url, quote=True),
        f"{local_prefix}{destination.name}",
    )


def _localize_videos(site_root: Path, payload: dict[str, Any], progress: ProgressCallback) -> None:
    videos = [item for item in payload.get("videos", []) if isinstance(item, dict)]
    direct_videos = [item for item in videos if item.get("kind") == "direct"]
    if not direct_videos:
        return
    slug = str(payload["slug"])
    article_path = site_root / "articles" / f"{slug}.html"
    article_html = article_path.read_text(encoding="utf-8")
    asset_root = site_root / "assets" / "articles" / slug
    asset_root.mkdir(parents=True, exist_ok=True)
    for index, video in enumerate(direct_videos, start=1):
        mime_type = str(video.get("mime_type") or "video/mp4")
        extension = ".webm" if mime_type == "video/webm" else ".mp4"
        destination = asset_root / f"video-{index:02d}{extension}"
        progress(35 + round(index / len(direct_videos) * 30), f"動画 {index}/{len(direct_videos)} をサイト用に保存しています")
        _download_video(video, destination)
        remote = html.escape(str(video.get("url") or ""), quote=True)
        local_prefix = f"../assets/articles/{slug}/"
        local = f"{local_prefix}{destination.name}"
        if remote not in article_html:
            raise RuntimeError(f"記事内の動画 {index} を置き換えられませんでした")
        article_html = article_html.replace(remote, local)
        article_html = _localize_video_poster(
            video,
            asset_root / f"video-{index:02d}-poster",
            article_html,
            local_prefix,
        )
    article_path.write_text(article_html, encoding="utf-8", newline="")


def _clone_site(
    site: ManagedSite,
    destination: Path,
    progress: ProgressCallback,
    git_env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
) -> tuple[Path, str]:
    progress(10, "公開サイトの最新版を取得しています")
    _run_git(["clone", "--depth", "1", _repository_url(site), str(destination)], timeout=300, env=git_env, secrets=secrets)
    branch = _run_git(["branch", "--show-current"], cwd=destination, env=git_env, secrets=secrets) or "main"
    return destination, branch


def publish_article(
    payload: dict[str, Any],
    draft_root: Path,
    site: ManagedSite,
    progress: ProgressCallback = lambda _value, _message: None,
) -> dict[str, Any]:
    if str(payload.get("rights_status") or "") != "confirmed" or payload.get("rights_confirmed") is not True:
        raise RuntimeError("許可管理を「許可済み」にしてから公開してください")
    slug = str(payload.get("slug") or "")
    if not SLUG_PATTERN.fullmatch(slug):
        raise RuntimeError("記事スラッグが不正です")

    with tempfile.TemporaryDirectory(prefix="indanya-publish-") as temporary:
        git_env, secrets = _git_environment(site, Path(temporary))
        repository, branch = _clone_site(site, Path(temporary) / "site", progress, git_env, secrets)
        published = _published_payload(payload)
        progress(28, "記事と画像をサイトへ組み込んでいます")
        result = add_built_article(published, repository)
        _localize_videos(repository, published, progress)
        progress(72, "公開内容を最終確認しています")
        _run_git(["add", "--", f"articles/{slug}.html", f"assets/articles/{slug}", "data/articles.json"], cwd=repository, env=git_env, secrets=secrets)
        changed = _run_git(["status", "--porcelain"], cwd=repository, env=git_env, secrets=secrets)
        if changed:
            _run_git(
                ["-c", "user.name=Indanya Studio", "-c", "user.email=studio@localhost", "commit", "-m", f"Publish {slug}"],
                cwd=repository,
                env=git_env,
                secrets=secrets,
            )
            progress(86, "GitHubへ記事を送信しています")
            _run_git(["push", "origin", branch], cwd=repository, timeout=300, env=git_env, secrets=secrets)

    public_url = urljoin(site.public_url.rstrip("/") + "/", str(result["url"]))
    published["published_url"] = public_url
    published["published_site_id"] = site.site_id
    published["published_site_name"] = site.name
    published["published_at"] = datetime.now(JST).isoformat(timespec="seconds")
    save_draft(published, draft_root)
    progress(100, "公開が完了しました")
    return {"slug": slug, "title": published.get("title", ""), "url": public_url, "status": "published"}


def unpublish_article(
    payload: dict[str, Any],
    draft_root: Path,
    site: ManagedSite,
    progress: ProgressCallback = lambda _value, _message: None,
) -> dict[str, Any]:
    slug = str(payload.get("slug") or "")
    if not SLUG_PATTERN.fullmatch(slug):
        raise RuntimeError("記事スラッグが不正です")
    with tempfile.TemporaryDirectory(prefix="indanya-unpublish-") as temporary:
        git_env, secrets = _git_environment(site, Path(temporary))
        repository, branch = _clone_site(site, Path(temporary) / "site", progress, git_env, secrets)
        article_path = repository / "articles" / f"{slug}.html"
        asset_path = repository / "assets" / "articles" / slug
        data_path = repository / "data" / "articles.json"
        try:
            articles = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("公開サイトの記事一覧を読み込めません") from exc
        remaining = [item for item in articles if isinstance(item, dict) and item.get("slug") != slug]
        if len(remaining) == len(articles) and not article_path.exists() and not asset_path.exists():
            raise RuntimeError("公開サイトにこの記事が見つかりません")
        progress(45, "公開記事をサイトから取り外しています")
        article_path.unlink(missing_ok=True)
        if asset_path.exists():
            shutil.rmtree(asset_path)
        data_path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
        _run_git(["add", "-A", "--", f"articles/{slug}.html", f"assets/articles/{slug}", "data/articles.json"], cwd=repository, env=git_env, secrets=secrets)
        _run_git(
            ["-c", "user.name=Indanya Studio", "-c", "user.email=studio@localhost", "commit", "-m", f"Unpublish {slug}"],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        progress(82, "GitHubへ変更を送信しています")
        _run_git(["push", "origin", branch], cwd=repository, timeout=300, env=git_env, secrets=secrets)

    draft = copy.deepcopy(payload)
    draft["status"] = "draft"
    draft["editorial_status"] = "draft"
    draft.pop("published_url", None)
    draft.pop("published_site_id", None)
    draft.pop("published_site_name", None)
    draft["unpublished_at"] = datetime.now(JST).isoformat(timespec="seconds")
    save_draft(draft, draft_root)
    progress(100, "公開を取り消しました")
    return {"slug": slug, "title": draft.get("title", ""), "status": "draft"}
