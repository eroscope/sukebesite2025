from __future__ import annotations

import copy
import base64
import binascii
import html
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from article_studio import JST, add_built_article, save_draft, _validate_source_url
from indanya_desktop.analytics import ANALYTICS_VERSION
from indanya_desktop.sites import ManagedSite
from indanya_desktop.editorial_policy import require_publishable_article
from indanya_desktop.site_discovery import refresh_site_discovery
from indanya_desktop.sitemap_health import (
    combined_sitemap_health,
    save_sitemap_health,
    validate_local_sitemaps,
    wait_for_public_sitemaps,
)
from indanya_desktop.fanza_affiliate import (
    load_fanza_settings,
    normalize_fanza_affiliate_id,
    rewrite_published_fanza_links,
    save_fanza_settings,
)


ProgressCallback = Callable[[int, str], None]
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_PUBLISH_VIDEO_BYTES = 95 * 1024 * 1024
TARGET_PUBLISH_VIDEO_BYTES = 88 * 1024 * 1024
MAX_SOURCE_VIDEO_BYTES = 750 * 1024 * 1024
MAX_PUBLISH_POSTER_BYTES = 12 * 1024 * 1024
SITEMAP_STATIC_PAGES = (
    "",
    "latest.html",
    "popular.html",
    "categories.html",
    "fanza.html",
    "tags.html",
    "about.html",
    "editorial.html",
    "privacy.html",
    "contact.html",
    "partners.html",
)


@contextmanager
def _temporary_render_template(repository: Path, draft_root: Path):
    target = repository / "articles" / "pool-look-back.html"
    created = False
    if not target.is_file():
        source = draft_root / "articles" / "pool-look-back.html"
        if not source.is_file():
            raise RuntimeError("記事生成テンプレートが見つかりません")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        created = True
    try:
        yield
    finally:
        if created:
            target.unlink(missing_ok=True)


def _write_search_files(
    repository: Path,
    public_url: str,
    articles: list[dict[str, Any]],
) -> None:
    refresh_site_discovery(repository, public_url, articles)


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
    now = datetime.now(JST).isoformat(timespec="seconds")
    published["status"] = "published"
    published["editorial_status"] = "published"
    published["review_status"] = "published"
    published["review_status_at"] = now
    published.pop("review_message", None)
    published["published_at"] = now
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
    if poster_data and html.escape(poster_data, quote=True) not in article_html:
        poster_data = ""
    if poster_url and html.escape(poster_url, quote=True) not in article_html:
        poster_url = ""
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


def _extend_sparse_checkout_if_enabled(
    repository: Path,
    slug: str,
    *,
    git_env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    try:
        _run_git(
            [
                "sparse-checkout", "add",
                "/index.html", "/articles/*.html",
                f"/articles/{slug}.html", f"/assets/articles/{slug}/",
                "/people.html", "/works.html", "/topics.html",
                "/people/", "/works/", "/topics/",
                "/data/discovery.json", "/feed.xml",
                "/sitemap-images.xml", "/sitemap-videos.xml",
                "/assets/common/article-discovery.css",
                "/assets/common/indanya-logo.png", "/assets/common/favicon.ico",
                "/assets/common/analytics-config.js", "/assets/common/ga4.js",
                "/assets/common/age-gate.js", "/privacy.html", "/partners.html",
            ],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
    except RuntimeError as exc:
        if "no sparse-checkout" not in str(exc).lower():
            raise


def _prepare_cached_site(
    site: ManagedSite,
    cache_root: Path,
    slug: str,
    progress: ProgressCallback,
    git_env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
) -> tuple[Path, str]:
    cache_root = cache_root.resolve()
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    repository = cache_root
    git_dir = repository / ".git"
    def remove_cache() -> None:
        def make_writable(function: Any, path: str, _error: Any) -> None:
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(repository, onerror=make_writable)

    if git_dir.is_dir():
        changed = _run_git(
            ["status", "--porcelain"], cwd=repository, env=git_env, secrets=secrets
        )
        if changed:
            remove_cache()
    if not git_dir.is_dir():
        progress(7, "初回用の軽量な公開キャッシュを準備しています")
        _run_git(
            [
                "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                "--no-checkout", _repository_url(site), str(repository),
            ],
            timeout=300,
            env=git_env,
            secrets=secrets,
        )
        _run_git(
            [
                "sparse-checkout", "set", "--no-cone",
                "/data/articles.json", "/data/discovery.json",
                "/sitemap.xml", "/sitemap-images.xml", "/sitemap-videos.xml",
                "/feed.xml", "/robots.txt", "/index.html",
                "/articles/*.html", "/articles/pool-look-back.html",
                f"/articles/{slug}.html", f"/assets/articles/{slug}/",
                "/people.html", "/works.html", "/topics.html",
                "/people/", "/works/", "/topics/",
                "/assets/common/article-discovery.css",
                "/assets/common/indanya-logo.png", "/assets/common/favicon.ico",
                "/assets/common/analytics-config.js", "/assets/common/ga4.js",
                "/assets/common/age-gate.js", "/privacy.html", "/partners.html",
            ],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        _run_git(["checkout"], cwd=repository, env=git_env, secrets=secrets)
    else:
        progress(7, "公開サイトの差分だけを取得しています")
        _extend_sparse_checkout_if_enabled(
            repository,
            slug,
            git_env=git_env,
            secrets=secrets,
        )
        _run_git(
            ["pull", "--ff-only"], cwd=repository, env=git_env, secrets=secrets
        )
    (repository / "articles").mkdir(parents=True, exist_ok=True)
    (repository / "assets" / "articles").mkdir(parents=True, exist_ok=True)
    branch = _run_git(
        ["branch", "--show-current"], cwd=repository, env=git_env, secrets=secrets
    ) or "main"
    return repository, branch


def publish_ga4_config(
    site_root: Path,
    site: ManagedSite,
    progress: ProgressCallback = lambda _value, _message: None,
) -> None:
    """Publish only the shared GA4 configuration and tracking files."""
    with tempfile.TemporaryDirectory(prefix="indanya-ga4-") as temporary:
        git_env, secrets = _git_environment(site, Path(temporary))
        repository, branch = _prepare_cached_site(
            site,
            site_root / ".article-studio" / "publish-cache" / site.site_id,
            "pool-look-back",
            progress,
            git_env,
            secrets,
        )
        for relative in (
            "assets/common/analytics-config.js",
            "assets/common/ga4.js",
            "assets/common/age-gate.js",
            "privacy.html",
        ):
            source = site_root / relative
            destination = repository / relative
            if not source.is_file():
                raise RuntimeError(f"GA4公開ファイルが見つかりません: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        age_gate_pattern = re.compile(
            r'(?P<path>(?:\.\./)?assets/common/age-gate\.js)(?:\?v=[^"\']*)?'
        )
        for html_path in list(repository.glob("*.html")) + list((repository / "articles").glob("*.html")):
            source = html_path.read_text(encoding="utf-8")
            updated = age_gate_pattern.sub(
                rf"\g<path>?v=analytics-v{ANALYTICS_VERSION}", source
            )
            if updated != source:
                html_path.write_text(updated, encoding="utf-8", newline="")
        progress(60, "GA4設定を公開サイトへ反映しています")
        _run_git(
            [
                "add", "--",
                "assets/common/analytics-config.js", "assets/common/ga4.js",
                "assets/common/age-gate.js", "privacy.html",
                "*.html", "articles/*.html",
            ],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        if _run_git(["status", "--porcelain"], cwd=repository, env=git_env, secrets=secrets):
            _run_git(
                ["-c", "user.name=Indanya Studio", "-c", "user.email=studio@localhost", "commit", "-m", "Configure GA4 analytics"],
                cwd=repository,
                env=git_env,
                secrets=secrets,
            )
            _push_with_remote_retry(repository, branch, git_env=git_env, secrets=secrets)
    progress(100, "GA4設定の公開が完了しました")


def publish_fanza_affiliate_update(
    site_root: Path,
    site: ManagedSite,
    affiliate_id_or_link: str,
    progress: ProgressCallback = lambda _value, _message: None,
) -> dict[str, int]:
    """Replace every published FANZA button with the site's current account ID."""
    affiliate_id = normalize_fanza_affiliate_id(affiliate_id_or_link)
    progress(5, "ローカルの公開記事をあなたの広告リンクへ更新しています")
    local_stats = rewrite_published_fanza_links(site_root, affiliate_id)

    with tempfile.TemporaryDirectory(prefix="indanya-fanza-links-") as temporary:
        git_env, secrets = _git_environment(site, Path(temporary))
        repository, branch = _prepare_cached_site(
            site,
            site_root / ".article-studio" / "publish-cache" / site.site_id,
            "pool-look-back",
            lambda value, message: progress(min(40, 8 + value * 32 // 100), message),
            git_env,
            secrets,
        )
        progress(42, "公開済みの記事ページを取得しています")
        try:
            _run_git(
                ["sparse-checkout", "add", "/articles/*.html"],
                cwd=repository,
                env=git_env,
                secrets=secrets,
            )
        except RuntimeError as exc:
            if "no sparse-checkout" not in str(exc).lower():
                raise

        progress(58, "公開済みPRをあなたの広告リンクへ差し替えています")
        remote_stats = rewrite_published_fanza_links(repository, affiliate_id)
        _run_git(
            ["add", "--", "articles"],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        changed = _run_git(
            ["status", "--porcelain"], cwd=repository, env=git_env, secrets=secrets
        )
        if changed:
            _run_git(
                [
                    "-c", "user.name=Indanya Studio",
                    "-c", "user.email=studio@localhost",
                    "commit", "-m", "Apply FANZA affiliate links",
                ],
                cwd=repository,
                env=git_env,
                secrets=secrets,
            )
            progress(82, "更新した広告リンクをGitHubへ送信しています")
            _push_with_remote_retry(
                repository,
                branch,
                git_env=git_env,
                secrets=secrets,
            )
    progress(100, "既存記事と今後の記事へアフィリエイトIDを適用しました")
    return {
        "local_files": local_stats["changed_files"],
        "local_links": local_stats["changed_links"],
        "published_files": remote_stats["changed_files"],
        "published_links": remote_stats["changed_links"],
    }


def _push_with_remote_retry(
    repository: Path,
    branch: str,
    *,
    git_env: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    try:
        _run_git(
            ["push", "origin", branch],
            cwd=repository,
            timeout=300,
            env=git_env,
            secrets=secrets,
        )
        return
    except RuntimeError as exc:
        message = str(exc).lower()
        if "fetch first" not in message and "non-fast-forward" not in message:
            raise
    _run_git(
        ["pull", "--rebase", "origin", branch],
        cwd=repository,
        timeout=300,
        env=git_env,
        secrets=secrets,
    )
    _run_git(
        ["push", "origin", branch],
        cwd=repository,
        timeout=300,
        env=git_env,
        secrets=secrets,
    )


def publish_article(
    payload: dict[str, Any],
    draft_root: Path,
    site: ManagedSite,
    progress: ProgressCallback = lambda _value, _message: None,
) -> dict[str, Any]:
    if str(payload.get("rights_status") or "") != "confirmed" or payload.get("rights_confirmed") is not True:
        raise RuntimeError("許可管理を「許可済み」にしてから公開してください")
    affiliate_id = load_fanza_settings(draft_root).get("affiliate_id", "")
    has_fanza_promotion = any(
        isinstance(block, dict) and block.get("type") == "product_cta"
        for block in payload.get("blocks", [])
    ) if isinstance(payload.get("blocks"), list) else False
    if has_fanza_promotion and not affiliate_id:
        raise RuntimeError(
            "FANZAアフィリエイトIDが未設定です。設定画面で一度保存してから公開してください"
        )
    require_publishable_article(payload)
    slug = str(payload.get("slug") or "")
    if not SLUG_PATTERN.fullmatch(slug):
        raise RuntimeError("記事スラッグが不正です")

    with tempfile.TemporaryDirectory(prefix="indanya-publish-") as temporary:
        git_env, secrets = _git_environment(site, Path(temporary))
        repository, branch = _prepare_cached_site(
            site,
            draft_root / ".article-studio" / "publish-cache" / site.site_id,
            slug,
            progress,
            git_env,
            secrets,
        )
        published = _published_payload(payload)
        progress(28, "記事と画像をサイトへ組み込んでいます")
        if affiliate_id:
            save_fanza_settings(repository, affiliate_id)
        with _temporary_render_template(repository, draft_root):
            result = add_built_article(published, repository)
        _localize_videos(repository, published, progress)
        build_state = repository / ".article-studio"
        if build_state.is_dir():
            shutil.rmtree(build_state)
        articles = json.loads(
            (repository / "data" / "articles.json").read_text(encoding="utf-8")
        )
        _write_search_files(repository, site.public_url, articles)
        progress(68, "サイトマップに全公開記事が入っているか検査しています")
        local_sitemap_health = validate_local_sitemaps(
            repository,
            site.public_url,
        )
        save_sitemap_health(
            draft_root,
            combined_sitemap_health(local_sitemap_health, None),
        )
        progress(72, "公開内容を最終確認しています")
        _run_git(
            ["add", "-A", "--", "."],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        changed = _run_git(["status", "--porcelain"], cwd=repository, env=git_env, secrets=secrets)
        if changed:
            _run_git(
                ["-c", "user.name=Indanya Studio", "-c", "user.email=studio@localhost", "commit", "-m", f"Publish {slug}"],
                cwd=repository,
                env=git_env,
                secrets=secrets,
            )
            progress(86, "GitHubへ記事を送信しています")
            _push_with_remote_retry(
                repository,
                branch,
                git_env=git_env,
                secrets=secrets,
            )

    public_sitemap_health = wait_for_public_sitemaps(
        site.public_url,
        local_sitemap_health,
        progress=progress,
    )
    sitemap_health = combined_sitemap_health(
        local_sitemap_health,
        public_sitemap_health,
    )
    save_sitemap_health(draft_root, sitemap_health)
    public_url = urljoin(site.public_url.rstrip("/") + "/", str(result["url"]))
    published["published_url"] = public_url
    published["published_site_id"] = site.site_id
    published["published_site_name"] = site.name
    published["published_at"] = datetime.now(JST).isoformat(timespec="seconds")
    save_draft(published, draft_root)
    if sitemap_health.get("status") == "healthy":
        progress(100, "公開とGoogle入口の反映確認が完了しました")
    else:
        progress(100, "記事は公開済みです。サイトマップは公開反映を継続確認します")
    return {
        "slug": slug,
        "title": published.get("title", ""),
        "url": public_url,
        "status": "published",
        "sitemap_health": sitemap_health,
    }


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
        repository, branch = _prepare_cached_site(
            site,
            draft_root / ".article-studio" / "publish-cache" / site.site_id,
            slug,
            progress,
            git_env,
            secrets,
        )
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
        _write_search_files(repository, site.public_url, remaining)
        local_sitemap_health = validate_local_sitemaps(
            repository,
            site.public_url,
        )
        _run_git(
            ["add", "-A", "--", "."],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        _run_git(
            ["-c", "user.name=Indanya Studio", "-c", "user.email=studio@localhost", "commit", "-m", f"Unpublish {slug}"],
            cwd=repository,
            env=git_env,
            secrets=secrets,
        )
        progress(82, "GitHubへ変更を送信しています")
        _push_with_remote_retry(
            repository,
            branch,
            git_env=git_env,
            secrets=secrets,
        )

    public_sitemap_health = wait_for_public_sitemaps(
        site.public_url,
        local_sitemap_health,
        progress=progress,
    )
    save_sitemap_health(
        draft_root,
        combined_sitemap_health(local_sitemap_health, public_sitemap_health),
    )
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
