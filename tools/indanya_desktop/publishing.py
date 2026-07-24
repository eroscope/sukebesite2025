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
TARGET_PUBLISH_VIDEO_BYTES = 88 * 1024 * 1024
MAX_SOURCE_VIDEO_BYTES = 750 * 1024 * 1024
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


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError) as exc:
        raise RuntimeError("動画圧縮機能を準備できませんでした。アプリを最新版へ更新してください") from exc


def _video_duration(source: Path, ffmpeg: str) -> float:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr or "")
    if not match:
        raise RuntimeError("動画の長さを確認できないため圧縮できませんでした")
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _compress_video(source: Path, destination: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    duration = _video_duration(source, ffmpeg)
    if duration <= 0:
        raise RuntimeError("動画の長さが不正なため圧縮できませんでした")
    total_kbps = max(260, int(TARGET_PUBLISH_VIDEO_BYTES * 8 / duration / 1000))
    attempts = (
        (1280, max(180, total_kbps - 80)),
        (960, max(160, int((total_kbps - 80) * 0.78))),
        (720, max(140, int((total_kbps - 80) * 0.58))),
    )
    for width, video_kbps in attempts:
        destination.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", f"scale={width}:-2:force_original_aspect_ratio=decrease",
                "-c:v", "libx264", "-preset", "medium",
                "-b:v", f"{video_kbps}k",
                "-maxrate", f"{video_kbps}k",
                "-bufsize", f"{video_kbps * 2}k",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
                str(destination),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0 and destination.is_file() and 0 < destination.stat().st_size <= MAX_PUBLISH_VIDEO_BYTES:
            return
    destination.unlink(missing_ok=True)
    raise RuntimeError("動画をGitHub Pagesの上限内まで小さくできませんでした")


def _materialize_stream_video(url: str, destination: Path, referer: str = "") -> Path:
    ffmpeg = _ffmpeg_executable()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    header_lines = [
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
    ]
    if referer:
        header_lines.append(f"Referer: {_validate_source_url(referer)}")
    completed = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-headers", "\r\n".join(header_lines) + "\r\n",
            "-i", _validate_source_url(url),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", "-movflags", "+faststart",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"X動画の音声と映像を結合できませんでした: {detail[-300:]}")
    return destination


def _download_video(video: dict[str, Any], destination: Path) -> Path:
    video_url = _validate_source_url(str(video.get("url") or ""))
    referer = str(video.get("referer") or "").strip()
    if urlparse(video_url).path.lower().endswith(".mpd"):
        materialized = destination.with_suffix(".stream.mp4")
        try:
            _materialize_stream_video(video_url, materialized, referer)
            if materialized.stat().st_size <= MAX_PUBLISH_VIDEO_BYTES:
                materialized.replace(destination)
                return destination
            compressed = destination.with_suffix(".mp4")
            _compress_video(materialized, compressed)
            return compressed
        finally:
            materialized.unlink(missing_ok=True)
    headers = {
        "Accept": "video/mp4,video/webm,video/*;q=0.9,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(video_url, headers=headers)
    temporary = destination.with_suffix(destination.suffix + ".source")
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_SOURCE_VIDEO_BYTES:
                    raise RuntimeError("元動画が750MBを超えるため回収できません")
                output.write(chunk)
        if written == 0:
            raise RuntimeError("動画データを取得できませんでした")
        if written <= MAX_PUBLISH_VIDEO_BYTES:
            temporary.replace(destination)
            return destination
        compressed = destination.with_suffix(".mp4")
        _compress_video(temporary, compressed)
        return compressed
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
    asset_root = site_root / "assets" / "articles" / slug
    asset_root.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[int, dict[str, Any], Path]] = []
    skipped_ids: set[str] = set()
    for index, video in enumerate(direct_videos, start=1):
        mime_type = str(video.get("mime_type") or "video/mp4")
        extension = ".webm" if mime_type == "video/webm" else ".mp4"
        destination = asset_root / f"video-{index:02d}{extension}"
        progress(35 + round(index / len(direct_videos) * 30), f"動画 {index}/{len(direct_videos)} をサイト用に保存しています")
        try:
            destination = _download_video(video, destination)
        except RuntimeError as exc:
            message = str(exc)
            if not any(term in message for term in ("大きすぎ", "750MB", "上限内まで小さく", "圧縮できません")):
                raise
            skipped_ids.add(str(video.get("id") or ""))
            progress(35 + round(index / len(direct_videos) * 30), f"動画 {index} は容量超過のため外しました")
            continue
        prepared.append((index, video, destination))

    if skipped_ids:
        payload["videos"] = [
            video for video in videos
            if str(video.get("id") or "") not in skipped_ids
        ]
        filtered_blocks: list[dict[str, Any]] = []
        for block in payload.get("blocks", []):
            if not isinstance(block, dict) or block.get("type") != "videos":
                filtered_blocks.append(block)
                continue
            kept_ids = [
                video_id for video_id in block.get("video_ids", [])
                if str(video_id) not in skipped_ids
            ]
            if kept_ids:
                filtered_blocks.append({**block, "video_ids": kept_ids})
        payload["blocks"] = filtered_blocks
        add_built_article(payload, site_root)

    article_html = article_path.read_text(encoding="utf-8")
    for index, video, destination in prepared:
        remote = html.escape(str(video.get("url") or ""), quote=True)
        local_prefix = f"../assets/articles/{slug}/"
        local = f"{local_prefix}{destination.name}"
        if remote not in article_html:
            raise RuntimeError(f"記事内の動画 {index} を置き換えられませんでした")
        source_pattern = re.compile(
            r'(<source\b[^>]*\bsrc=["\'])' + re.escape(remote) + r'(["\'][^>]*>)',
            re.IGNORECASE,
        )
        if source_pattern.search(article_html):
            def replace_source(match: re.Match[str]) -> str:
                tag = f"{match.group(1)}{local}{match.group(2)}"
                if destination.suffix.lower() == ".mp4":
                    tag = re.sub(
                        r'\btype=(["\'])video/[^"\']+\1',
                        'type="video/mp4"',
                        tag,
                        flags=re.IGNORECASE,
                    )
                return tag
            article_html = source_pattern.sub(replace_source, article_html)
        else:
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
