#!/usr/bin/env python3
"""PC worker for the Indanya custom GPT workflow.

The custom GPT talks to Google Apps Script. This worker polls the Apps Script
queue, opens source pages in a real Chrome browser, saves candidate media and
builds/publishes articles without using the OpenAI API.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
STATE_ROOT = ROOT / ".article-studio" / "chatgpt-worker"
CONFIG_PATH = STATE_ROOT / "config.json"
CAPTURE_ROOT = STATE_ROOT / "captures"
LOG_PATH = STATE_ROOT / "worker.log"
DEFAULT_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbziQQz8KKzgYzrm4qpcErujVEHe1y4RC9qq4ChhpmqgN5sMHSkp1ECteOrf6xK9RyKWiA/exec"
DEFAULT_WORKER_KEY = "AeugyJTkfhQW7HnUeyXROo9EcCJyNcSt"
DEFAULT_REPOSITORY = "https://github.com/eroscope/sukebesite2025.git"
DEFAULT_PUBLIC_BASE = "https://eroscope.github.io/sukebesite2025/"
MAX_IMAGES = 100
MAX_VIDEOS = 30
MAX_TEXT_BLOCKS = 320
MAX_TEXT_CHARS = 70000
MAX_IMAGE_BYTES = 18 * 1024 * 1024


@dataclass
class Config:
    web_app_url: str
    worker_key: str
    repository_url: str
    public_base_url: str
    poll_seconds: float = 2.0
    worker_name: str = "windows-pc"


def log(message: str) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        raise RuntimeError("設定がありません。SETUP_CHATGPT_WORKER.cmdを先に実行してください。")
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return Config(
        web_app_url=str(raw.get("web_app_url") or "").strip(),
        worker_key=str(raw.get("worker_key") or "").strip(),
        repository_url=str(raw.get("repository_url") or DEFAULT_REPOSITORY).strip(),
        public_base_url=str(raw.get("public_base_url") or DEFAULT_PUBLIC_BASE).strip(),
        poll_seconds=float(raw.get("poll_seconds") or 2.0),
        worker_name=str(raw.get("worker_name") or os.environ.get("COMPUTERNAME") or "windows-pc"),
    )


def configure() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    print("淫談屋 ChatGPTワーカー初期設定")
    print("Enterだけなら表示中の値を使います。")
    web_app_url = input(f"Apps Script URL [{DEFAULT_WEB_APP_URL}]: ").strip() or DEFAULT_WEB_APP_URL
    worker_key = input(f"Worker key [{DEFAULT_WORKER_KEY}]: ").strip() or DEFAULT_WORKER_KEY
    repository_url = input(f"GitHub repository [{DEFAULT_REPOSITORY}]: ").strip() or DEFAULT_REPOSITORY
    public_base_url = input(f"公開サイトURL [{DEFAULT_PUBLIC_BASE}]: ").strip() or DEFAULT_PUBLIC_BASE
    payload = {
        "web_app_url": web_app_url,
        "worker_key": worker_key,
        "repository_url": repository_url,
        "public_base_url": public_base_url,
        "poll_seconds": 2,
        "worker_name": os.environ.get("COMPUTERNAME") or "windows-pc",
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"保存しました: {CONFIG_PATH}")


def api_post(config: Config, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.web_app_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "IndanyaWorker/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Apps Script HTTP {exc.code}: {raw[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Apps Scriptへ接続できません: {exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Apps Scriptの返答がJSONではありません: {raw[:1000]}") from exc
    if result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "Apps Scriptエラー"))
    return result


def worker_progress(config: Config, job_id: str, progress: int, message: str) -> None:
    api_post(
        config,
        {
            "action": "worker_progress",
            "worker_key": config.worker_key,
            "job_id": job_id,
            "progress": progress,
            "message": message,
        },
    )


def worker_complete(config: Config, job_id: str, result: dict[str, Any], message: str = "完了") -> None:
    api_post(
        config,
        {
            "action": "worker_complete",
            "worker_key": config.worker_key,
            "job_id": job_id,
            "message": message,
            "result": result,
        },
        timeout=120,
    )


def worker_fail(config: Config, job_id: str, error: str) -> None:
    try:
        api_post(
            config,
            {
                "action": "worker_fail",
                "worker_key": config.worker_key,
                "job_id": job_id,
                "error": error[-12000:],
            },
        )
    except Exception as exc:  # noqa: BLE001
        log(f"失敗報告も送れませんでした: {exc}")


def safe_slug(value: str, fallback: str = "article") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:70] or fallback


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"コマンド失敗 ({completed.returncode}): {' '.join(args)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def import_runtime() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise RuntimeError("必要な部品がありません。SETUP_CHATGPT_WORKER.cmdを再実行してください。") from exc
    return (Image, ImageDraw, ImageFont), sync_playwright


def dismiss_common_overlays(page: Any) -> None:
    labels = [
        "同意する",
        "すべて同意",
        "許可する",
        "Accept all",
        "Accept",
        "I agree",
        "閉じる",
        "Close",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=800)
        except Exception:  # noqa: BLE001
            pass


def auto_scroll(page: Any) -> None:
    page.evaluate(
        """
        async () => {
          const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
          let lastHeight = 0;
          for (let i = 0; i < 28; i++) {
            const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            window.scrollTo(0, Math.min(height, window.scrollY + Math.max(700, window.innerHeight * 0.85)));
            await sleep(220);
            const nextHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            if (window.scrollY + window.innerHeight >= nextHeight - 20 && nextHeight === lastHeight) break;
            lastHeight = nextHeight;
          }
          window.scrollTo(0, 0);
          await sleep(250);
        }
        """
    )


EXTRACT_SCRIPT = r"""
() => {
  const abs = value => {
    try { return new URL(value, document.baseURI).href; } catch (_) { return ''; }
  };
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const rectData = el => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x + window.scrollX),
      y: Math.round(r.y + window.scrollY),
      width: Math.round(r.width),
      height: Math.round(r.height)
    };
  };
  const visible = el => {
    const style = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0 && r.width > 1 && r.height > 1;
  };
  const nearestText = el => {
    const texts = [];
    let node = el;
    for (let level = 0; level < 4 && node; level++, node = node.parentElement) {
      const candidates = Array.from(node.querySelectorAll(':scope > p, :scope > figcaption, :scope > h1, :scope > h2, :scope > h3, :scope > div'));
      for (const candidate of candidates) {
        const text = clean(candidate.innerText);
        if (text && text.length < 500) texts.push(text);
        if (texts.length >= 4) break;
      }
      if (texts.length) break;
    }
    return texts.slice(0, 4).join(' / ').slice(0, 700);
  };
  const ancestors = el => {
    const result = [];
    let node = el;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      let item = node.tagName ? node.tagName.toLowerCase() : '';
      if (node.id) item += '#' + node.id;
      if (node.classList && node.classList.length) item += '.' + Array.from(node.classList).slice(0, 4).join('.');
      if (item) result.push(item);
    }
    return result.join(' > ').slice(0, 500);
  };

  const images = [];
  for (const el of Array.from(document.images)) {
    const linkUrl = el.closest('a') ? abs(el.closest('a').href) : '';
    const srcsetUrls = [el.srcset, el.getAttribute('data-srcset')]
      .flatMap(value => String(value || '').split(','))
      .map(value => abs(value.trim().split(/\s+/)[0]))
      .filter(Boolean);
    const urls = Array.from(new Set([
      linkUrl && /\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)/i.test(linkUrl) ? linkUrl : '',
      ...srcsetUrls,
      abs(el.getAttribute('data-original') || ''),
      abs(el.getAttribute('data-large') || ''),
      abs(el.getAttribute('data-full') || ''),
      abs(el.getAttribute('data-src') || ''),
      abs(el.currentSrc || ''),
      abs(el.src || '')
    ].filter(Boolean)));
    const src = urls[0] || '';
    if (!src) continue;
    images.push({
      url: src,
      urls,
      alt: clean(el.alt),
      title: clean(el.title),
      natural_width: Number(el.naturalWidth || 0),
      natural_height: Number(el.naturalHeight || 0),
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      link_url: linkUrl
    });
  }

  const backgroundImages = [];
  for (const el of Array.from(document.querySelectorAll('article *, main *')).slice(0, 5000)) {
    if (!visible(el)) continue;
    const bg = getComputedStyle(el).backgroundImage || '';
    const match = bg.match(/url\(["']?(.*?)["']?\)/i);
    if (!match) continue;
    const url = abs(match[1]);
    if (!url) continue;
    backgroundImages.push({
      url,
      alt: clean(el.getAttribute('aria-label')),
      title: clean(el.getAttribute('title')),
      natural_width: 0,
      natural_height: 0,
      visible: true,
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      link_url: el.closest('a') ? abs(el.closest('a').href) : ''
    });
  }

  const videos = [];
  for (const el of Array.from(document.querySelectorAll('video'))) {
    const sources = [el.currentSrc, el.src, ...Array.from(el.querySelectorAll('source')).map(x => x.src)].map(abs).filter(Boolean);
    videos.push({
      kind: 'direct',
      urls: Array.from(new Set(sources)),
      poster: abs(el.poster || ''),
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el)
    });
  }
  for (const el of Array.from(document.querySelectorAll('iframe'))) {
    const src = abs(el.src || '');
    if (!src) continue;
    videos.push({
      kind: 'iframe',
      urls: [src],
      poster: '',
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      title: clean(el.title)
    });
  }

  const preferredRoot = document.querySelector('article') || document.querySelector('main') || document.body;
  const links = [];
  for (const el of Array.from(document.querySelectorAll('a[href]')).slice(0, 3000)) {
    if (!visible(el)) continue;
    const url = abs(el.href || '');
    if (!url || !/^https?:/i.test(url)) continue;
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.title);
    const image = el.querySelector('img');
    if (!text && !image) continue;
    const style = getComputedStyle(el);
    links.push({
      url,
      text: text.slice(0, 500),
      contains_image: Boolean(image),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      font_size: style.fontSize || '',
      font_weight: style.fontWeight || '',
      color: style.color || '',
      background: style.backgroundColor || ''
    });
    if (links.length >= 200) break;
  }
  const blocks = [];
  for (const el of Array.from(preferredRoot.querySelectorAll('h1,h2,h3,h4,p,li,blockquote,figcaption,pre')).slice(0, 1000)) {
    const text = clean(el.innerText);
    if (!text || text.length < 2) continue;
    blocks.push({
      tag: el.tagName.toLowerCase(),
      text: text.slice(0, 3000),
      rect: rectData(el),
      ancestors: ancestors(el)
    });
  }

  const meta = name => {
    const el = document.querySelector(`meta[property="${name}"],meta[name="${name}"]`);
    return el ? clean(el.content) : '';
  };
  const canonical = document.querySelector('link[rel="canonical"]');
  return {
    title: clean(meta('og:title') || document.title),
    description: clean(meta('og:description') || meta('description')),
    canonical_url: canonical ? abs(canonical.href) : location.href,
    final_url: location.href,
    body_text: clean(preferredRoot.innerText).slice(0, 100000),
    text_blocks: blocks,
    links,
    images: images.concat(backgroundImages),
    videos,
    page: {
      width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
      height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
    }
  };
}
"""


def download_candidate(request_context: Any, url: str, referer: str) -> tuple[bytes, str] | None:
    if not url.startswith(("http://", "https://")):
        return None
    try:
        response = request_context.get(url, headers={"Referer": referer}, timeout=30000, fail_on_status_code=False)
        if not response.ok:
            return None
        data = response.body()
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            return None
        return data, content_type
    except Exception:  # noqa: BLE001
        return None


def image_extension(content_type: str, url: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else ".img"


def create_contact_sheet(image_records: list[dict[str, Any]], destination: Path, pil: tuple[Any, Any, Any]) -> None:
    Image, ImageDraw, ImageFont = pil
    thumbs: list[tuple[Any, str]] = []
    for record in image_records:
        local_path = Path(record["local_path"])
        try:
            with Image.open(local_path) as source:
                source.seek(0)
                converted = source.convert("RGB")
                converted.thumbnail((300, 240))
                canvas = Image.new("RGB", (320, 290), "white")
                x = (320 - converted.width) // 2
                y = 30 + (240 - converted.height) // 2
                canvas.paste(converted, (x, y))
                draw = ImageDraw.Draw(canvas)
                draw.text((10, 8), record["id"], fill="black", font=ImageFont.load_default())
                context = str(record.get("context") or "")[:44]
                draw.text((10, 274), context, fill="black", font=ImageFont.load_default())
                thumbs.append((canvas, record["id"]))
        except Exception:  # noqa: BLE001
            continue
    if not thumbs:
        return
    columns = 4
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 320, rows * 290), "white")
    for index, (thumb, _) in enumerate(thumbs):
        sheet.paste(thumb, ((index % columns) * 320, (index // columns) * 290))
    sheet.save(destination, "JPEG", quality=88, optimize=True)


def normalize_candidate_url(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query
    if any(key in query.lower() for key in ("w=", "width=", "resize=", "quality=")):
        query = ""
    return parsed._replace(fragment="", query=query).geturl()


def capture_article(config: Config, job: dict[str, Any]) -> dict[str, Any]:
    pil, sync_playwright = import_runtime()
    job_id = str(job["id"])
    source_url = str(job.get("payload", {}).get("url") or "")
    capture_dir = CAPTURE_ROOT / job_id
    media_dir = capture_dir / "media"
    capture_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    worker_progress(config, job_id, 5, "Chromeで記事を開いています")

    network_videos: set[str] = set()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"])
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        page = context.new_page()

        def on_response(response: Any) -> None:
            try:
                content_type = str(response.headers.get("content-type") or "").lower()
                url = str(response.url)
                if content_type.startswith("video/") or re.search(r"\.(?:mp4|webm|m3u8)(?:[?#]|$)", url, re.I):
                    network_videos.add(url)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        dismiss_common_overlays(page)
        auto_scroll(page)
        worker_progress(config, job_id, 28, "記事本文と素材候補を読み取っています")
        extracted = page.evaluate(EXTRACT_SCRIPT)
        screenshot_path = capture_dir / "page.jpg"
        try:
            page.screenshot(path=str(screenshot_path), full_page=True, type="jpeg", quality=72, timeout=30000)
        except Exception:  # noqa: BLE001
            page.screenshot(path=str(screenshot_path), type="jpeg", quality=72)

        final_url = str(extracted.get("final_url") or page.url)
        request_context = context.request
        raw_images = list(extracted.get("images") or [])
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        images: list[dict[str, Any]] = []
        for raw in raw_images:
            if len(images) >= MAX_IMAGES:
                break
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            key = normalize_candidate_url(url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            downloaded = download_candidate(request_context, url, final_url)
            if not downloaded:
                continue
            data, content_type = downloaded
            digest = sha256_bytes(data)
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            extension = image_extension(content_type, url)
            image_id = f"img_{len(images) + 1:03d}"
            filename = f"{image_id}{extension}"
            local_path = media_dir / filename
            local_path.write_bytes(data)
            record = {
                "id": image_id,
                "source_url": url,
                "filename": filename,
                "local_path": str(local_path),
                "alt": str(raw.get("alt") or "")[:400],
                "title": str(raw.get("title") or "")[:400],
                "context": str(raw.get("context") or "")[:1000],
                "ancestors": str(raw.get("ancestors") or "")[:700],
                "link_url": str(raw.get("link_url") or "")[:2000],
                "visible": bool(raw.get("visible")),
                "rect": raw.get("rect") or {},
                "natural_width": int(raw.get("natural_width") or 0),
                "natural_height": int(raw.get("natural_height") or 0),
                "sha256": digest,
                "content_type": content_type,
            }
            images.append(record)

        raw_videos = list(extracted.get("videos") or [])
        for url in sorted(network_videos):
            raw_videos.append({"kind": "network", "urls": [url], "poster": "", "context": "", "ancestors": "", "rect": {}})
        seen_video_urls: set[str] = set()
        videos: list[dict[str, Any]] = []
        for raw in raw_videos:
            for video_url in raw.get("urls") or []:
                video_url = str(video_url or "").strip()
                if not video_url or video_url in seen_video_urls:
                    continue
                if len(videos) >= MAX_VIDEOS:
                    break
                seen_video_urls.add(video_url)
                videos.append({
                    "id": f"vid_{len(videos) + 1:03d}",
                    "kind": str(raw.get("kind") or "direct"),
                    "url": video_url,
                    "poster_url": str(raw.get("poster") or ""),
                    "context": str(raw.get("context") or "")[:1000],
                    "ancestors": str(raw.get("ancestors") or "")[:700],
                    "rect": raw.get("rect") or {},
                    "title": str(raw.get("title") or "")[:400],
                })

        contact_sheet_path = capture_dir / "contact-sheet.jpg"
        create_contact_sheet(images, contact_sheet_path, pil)
        context.close()
        browser.close()

    worker_progress(config, job_id, 62, "AI確認用の一覧を準備しています")
    public = publish_capture_artifacts(config, job_id, capture_dir, images, screenshot_path, contact_sheet_path)
    for record in images:
        record.pop("local_path", None)
        record["preview_url"] = public["media_urls"].get(record["filename"], "")
    text_blocks = list(extracted.get("text_blocks") or [])[:MAX_TEXT_BLOCKS]
    body_text = str(extracted.get("body_text") or "")[:MAX_TEXT_CHARS]
    manifest = {
        "job_id": job_id,
        "source_url": source_url,
        "final_url": str(extracted.get("final_url") or source_url),
        "canonical_url": str(extracted.get("canonical_url") or ""),
        "title": str(extracted.get("title") or "")[:1000],
        "description": str(extracted.get("description") or "")[:4000],
        "body_text": body_text,
        "text_blocks": text_blocks,
        "images": images,
        "videos": videos,
        "contact_sheet_url": public.get("contact_sheet_url", ""),
        "page_screenshot_url": public.get("page_screenshot_url", ""),
        "capture_manifest_url": public.get("manifest_url", ""),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }
    (capture_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-publish manifest after public URLs were inserted.
    public = publish_capture_artifacts(config, job_id, capture_dir, images, screenshot_path, contact_sheet_path)
    manifest["capture_manifest_url"] = public.get("manifest_url", "")
    worker_progress(config, job_id, 92, "記事全体の解析データを返しています")
    return manifest


def prepare_data_repo(config: Config) -> Path:
    repo = STATE_ROOT / "public-repo"
    if not repo.exists():
        run_command(["git", "clone", config.repository_url, str(repo)], timeout=300)
    run_command(["git", "fetch", "origin"], cwd=repo, timeout=180)
    branches = run_command(["git", "branch", "-r"], cwd=repo)
    if "origin/gpt-worker-data" in branches:
        run_command(["git", "checkout", "-B", "gpt-worker-data", "origin/gpt-worker-data"], cwd=repo)
    else:
        run_command(["git", "checkout", "-B", "gpt-worker-data", "origin/main"], cwd=repo)
    return repo


def publish_capture_artifacts(
    config: Config,
    job_id: str,
    capture_dir: Path,
    images: list[dict[str, Any]],
    screenshot_path: Path,
    contact_sheet_path: Path,
) -> dict[str, Any]:
    repo = prepare_data_repo(config)
    destination = repo / "gpt-review" / job_id
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "media").mkdir(parents=True, exist_ok=True)
    media_urls: dict[str, str] = {}
    for record in images:
        filename = str(record["filename"])
        source = capture_dir / "media" / filename
        shutil.copy2(source, destination / "media" / filename)
        media_urls[filename] = (
            "https://raw.githubusercontent.com/eroscope/sukebesite2025/gpt-worker-data/"
            f"gpt-review/{job_id}/media/{quote(filename)}"
        )
    if screenshot_path.exists():
        shutil.copy2(screenshot_path, destination / "page.jpg")
    if contact_sheet_path.exists():
        shutil.copy2(contact_sheet_path, destination / "contact-sheet.jpg")
    manifest_path = capture_dir / "manifest.json"
    if manifest_path.exists():
        shutil.copy2(manifest_path, destination / "manifest.json")
    else:
        (destination / "manifest.json").write_text("{}\n", encoding="utf-8")
    readme = [f"# GPT review {job_id}", "", "Generated automatically by the local Indanya worker."]
    (destination / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    run_command(["git", "add", f"gpt-review/{job_id}"], cwd=repo)
    status = run_command(["git", "status", "--porcelain"], cwd=repo)
    if status:
        run_command(["git", "-c", "user.name=Indanya Worker", "-c", "user.email=worker@localhost", "commit", "-m", f"Add GPT review {job_id}"], cwd=repo)
        run_command(["git", "push", "origin", "gpt-worker-data"], cwd=repo, timeout=300)
    base = f"https://raw.githubusercontent.com/eroscope/sukebesite2025/gpt-worker-data/gpt-review/{job_id}"
    return {
        "media_urls": media_urls,
        "contact_sheet_url": f"{base}/contact-sheet.jpg" if contact_sheet_path.exists() else "",
        "page_screenshot_url": f"{base}/page.jpg" if screenshot_path.exists() else "",
        "manifest_url": f"{base}/manifest.json",
    }


def load_capture(capture_job_id: str) -> tuple[Path, dict[str, Any]]:
    capture_dir = CAPTURE_ROOT / capture_job_id
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"元の解析データがPCにありません: {capture_job_id}")
    return capture_dir, json.loads(manifest_path.read_text(encoding="utf-8"))


def normalize_article(article: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(article, dict):
        raise RuntimeError("articleはオブジェクトで指定してください")
    title = str(article.get("title") or "").strip()
    if not title:
        raise RuntimeError("記事タイトルがありません")
    slug = safe_slug(str(article.get("slug") or title), fallback=f"article-{int(time.time())}")
    responses = article.get("responses")
    if not isinstance(responses, list) or not responses:
        raise RuntimeError("responsesがありません")
    image_map = {str(item["id"]): item for item in manifest.get("images") or []}
    video_map = {str(item["id"]): item for item in manifest.get("videos") or []}
    used_images: list[str] = []
    used_videos: list[str] = []
    normalized_responses: list[dict[str, Any]] = []
    for raw in responses:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        image_ids = [str(item) for item in raw.get("image_ids") or []]
        video_ids = [str(item) for item in raw.get("video_ids") or []]
        unknown_images = [item for item in image_ids if item not in image_map]
        unknown_videos = [item for item in video_ids if item not in video_map]
        if unknown_images or unknown_videos:
            raise RuntimeError(f"存在しない素材IDがあります: {unknown_images + unknown_videos}")
        used_images.extend(image_ids)
        used_videos.extend(video_ids)
        normalized_responses.append({
            "text": text[:1000],
            "image_ids": image_ids,
            "video_ids": video_ids,
            "style": str(raw.get("style") or "normal"),
        })
    if len(used_images) != len(set(used_images)):
        raise RuntimeError("同じ画像IDが重複しています")
    if len(used_videos) != len(set(used_videos)):
        raise RuntimeError("同じ動画IDが重複しています")
    if not used_images:
        raise RuntimeError("採用画像が0枚です")
    return {
        "title": title,
        "slug": slug,
        "category": str(article.get("category") or "画像")[:30],
        "summary": str(article.get("summary") or title)[:300],
        "tags": [str(item)[:30] for item in (article.get("tags") or ["画像"])][:12],
        "comments": int(article.get("comments") or max(12, len(normalized_responses) * 3)),
        "featured": bool(article.get("featured", False)),
        "thumbnail_id": str(article.get("thumbnail_id") or used_images[0]),
        "responses": normalized_responses,
        "selected_image_ids": used_images,
        "selected_video_ids": used_videos,
    }


def convert_image_for_article(source: Path, destination: Path, pil: tuple[Any, Any, Any]) -> None:
    Image, _, _ = pil
    with Image.open(source) as image:
        image.seek(0)
        converted = image.convert("RGB")
        converted.thumbnail((1800, 1800))
        destination.parent.mkdir(parents=True, exist_ok=True)
        converted.save(destination, "WEBP", quality=88, method=6)


def article_payload_from_capture(
    capture_dir: Path,
    manifest: dict[str, Any],
    article: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    pil, _ = import_runtime()
    image_manifest = {str(item["id"]): item for item in manifest.get("images") or []}
    images: list[dict[str, Any]] = []
    temp_root = capture_dir / "article-images"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    for index, image_id in enumerate(article["selected_image_ids"], start=1):
        record = image_manifest[image_id]
        source = capture_dir / "media" / str(record["filename"])
        converted_path = temp_root / f"{image_id}.webp"
        convert_image_for_article(source, converted_path, pil)
        data = converted_path.read_bytes()
        width = int(record.get("natural_width") or 0)
        height = int(record.get("natural_height") or 0)
        images.append({
            "id": image_id,
            "name": f"image-{index:02d}.webp",
            "data_url": "data:image/webp;base64," + base64.b64encode(data).decode("ascii"),
            "alt": str(record.get("alt") or article["title"])[:180],
            "orientation": "landscape" if width > height and height > 0 else "portrait",
        })
    video_manifest = {str(item["id"]): item for item in manifest.get("videos") or []}
    videos: list[dict[str, Any]] = []
    for index, video_id in enumerate(article["selected_video_ids"], start=1):
        record = video_manifest[video_id]
        url = str(record.get("url") or "")
        kind = "iframe" if str(record.get("kind")) == "iframe" else "direct"
        mime_type = "text/html" if kind == "iframe" else ("video/webm" if ".webm" in url.lower() else "video/mp4")
        videos.append({
            "id": video_id,
            "kind": kind,
            "url": url,
            "referer": str(manifest.get("final_url") or manifest.get("source_url") or ""),
            "mime_type": mime_type,
            "label": f"元記事の動画 {index}",
        })
    blocks: list[dict[str, Any]] = []
    for response in article["responses"]:
        blocks.append({"type": "post", "text": response["text"], "style": response["style"]})
        image_ids = list(response["image_ids"])
        while image_ids:
            blocks.append({"type": "images", "image_ids": image_ids[:4]})
            image_ids = image_ids[4:]
        if response["video_ids"]:
            blocks.append({"type": "videos", "video_ids": list(response["video_ids"])})
    now = datetime.now().astimezone()
    return {
        "title": article["title"],
        "slug": article["slug"],
        "category": article["category"],
        "summary": article["summary"],
        "published_at": now.isoformat(timespec="seconds"),
        "status": status,
        "comments": article["comments"],
        "poster_name": "風吹けば名無し",
        "tags": article["tags"],
        "featured": article["featured"],
        "fictional_responses": True,
        "replace_existing": False,
        "source_url": str(manifest.get("final_url") or manifest.get("source_url") or ""),
        "source_label": "元記事",
        "transparency_note": "元記事の事実と素材を確認し、文章と構成を再作成しています。",
        "thumbnail_id": article["thumbnail_id"],
        "adult_confirmed": True,
        "rights_confirmed": status == "published",
        "privacy_confirmed": True,
        "source_confirmed": True,
        "images": images,
        "videos": videos,
        "blocks": blocks,
    }


def create_draft(config: Config, job: dict[str, Any], publish: bool) -> dict[str, Any]:
    job_id = str(job["id"])
    payload = job.get("payload") or {}
    capture_job_id = str(payload.get("capture_job_id") or "")
    capture_dir, manifest = load_capture(capture_job_id)
    article = normalize_article(payload.get("article") or {}, manifest)
    if publish and payload.get("rights_confirmed") is not True:
        raise RuntimeError("公開にはrights_confirmed=trueが必要です。許可確認前は下書きだけ作成します。")
    worker_progress(config, job_id, 18, "GPTの記事データを検査しています")
    article_payload = article_payload_from_capture(capture_dir, manifest, article, "published" if publish else "draft")

    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from article_studio import add_built_article, build_article, save_draft  # type: ignore

    if not publish:
        save_draft(article_payload, ROOT)
        preview = build_article(article_payload, ROOT, preview=True)
        preview_path = capture_dir / "draft-preview.html"
        preview_path.write_text(preview.article_html, encoding="utf-8")
        return {
            "capture_job_id": capture_job_id,
            "slug": article["slug"],
            "title": article["title"],
            "status": "draft",
            "draft_file": str(ROOT / ".article-studio" / "drafts" / f"{article['slug']}.json"),
            "preview_file": str(preview_path),
            "message": "下書きをPCへ保存しました。公開はしていません。",
        }

    worker_progress(config, job_id, 52, "記事HTMLと画像を作成しています")
    publish_repo = prepare_publish_repo(config)
    result = add_built_article(article_payload, publish_repo)
    localize_videos(publish_repo, article["slug"], manifest, article["selected_video_ids"])
    worker_progress(config, job_id, 78, "GitHub Pagesへ反映しています")
    run_command(["git", "add", "articles", "assets", "data/articles.json"], cwd=publish_repo)
    status = run_command(["git", "status", "--porcelain"], cwd=publish_repo)
    if status:
        run_command(["git", "-c", "user.name=Indanya Worker", "-c", "user.email=worker@localhost", "commit", "-m", f"Publish {article['slug']}"], cwd=publish_repo)
        run_command(["git", "push", "origin", "main"], cwd=publish_repo, timeout=300)
    public_url = urljoin(config.public_base_url.rstrip("/") + "/", str(result["url"]))
    return {
        "capture_job_id": capture_job_id,
        "slug": article["slug"],
        "title": article["title"],
        "status": "published",
        "url": public_url,
        "message": "記事を公開しました。",
    }


def prepare_publish_repo(config: Config) -> Path:
    repo = STATE_ROOT / "publish-repo"
    if not repo.exists():
        run_command(["git", "clone", config.repository_url, str(repo)], timeout=300)
    run_command(["git", "fetch", "origin"], cwd=repo)
    run_command(["git", "checkout", "-B", "main", "origin/main"], cwd=repo)
    return repo


def download_binary(url: str, referer: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def localize_videos(repo: Path, slug: str, manifest: dict[str, Any], video_ids: list[str]) -> None:
    if not video_ids:
        return
    video_map = {str(item["id"]): item for item in manifest.get("videos") or []}
    article_path = repo / "articles" / f"{slug}.html"
    article_html = article_path.read_text(encoding="utf-8")
    asset_root = repo / "assets" / "articles" / slug
    asset_root.mkdir(parents=True, exist_ok=True)
    referer = str(manifest.get("final_url") or manifest.get("source_url") or "")
    for index, video_id in enumerate(video_ids, start=1):
        record = video_map.get(video_id) or {}
        if str(record.get("kind")) == "iframe":
            continue
        url = str(record.get("url") or "")
        if not re.search(r"\.(?:mp4|webm)(?:[?#]|$)", url, re.I):
            continue
        extension = ".webm" if ".webm" in url.lower() else ".mp4"
        destination = asset_root / f"video-{index:02d}{extension}"
        try:
            data = download_binary(url, referer)
            if not data or len(data) > 95 * 1024 * 1024:
                continue
            destination.write_bytes(data)
            relative = f"../assets/articles/{slug}/{destination.name}"
            article_html = article_html.replace(html.escape(url, quote=True), relative)
        except Exception as exc:  # noqa: BLE001
            log(f"動画のローカル保存をスキップ: {url} ({exc})")
    article_path.write_text(article_html, encoding="utf-8")


def handle_job(config: Config, job: dict[str, Any]) -> dict[str, Any]:
    job_type = str(job.get("type") or "")
    if job_type == "capture":
        return capture_article(config, job)
    if job_type == "draft":
        return create_draft(config, job, publish=False)
    if job_type == "publish":
        return create_draft(config, job, publish=True)
    raise RuntimeError(f"未対応のジョブ種類です: {job_type}")


def run_worker(once: bool = False) -> None:
    config = load_config()
    log("ChatGPTワーカーを開始しました。黒い画面は閉じないでください。")
    while True:
        try:
            claimed = api_post(
                config,
                {
                    "action": "worker_claim",
                    "worker_key": config.worker_key,
                    "worker_name": config.worker_name,
                },
            )
            job = claimed.get("job")
            if not job:
                if once:
                    log("待機中のジョブはありません。")
                    return
                time.sleep(config.poll_seconds)
                continue
            job_id = str(job.get("id") or "")
            log(f"ジョブ開始: {job_id} ({job.get('type')})")
            try:
                result = handle_job(config, job)
                worker_complete(config, job_id, result)
                log(f"ジョブ完了: {job_id}")
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                log(f"ジョブ失敗: {job_id}: {exc}\n{detail}")
                worker_fail(config, job_id, detail)
            if once:
                return
        except KeyboardInterrupt:
            log("停止しました。")
            return
        except Exception as exc:  # noqa: BLE001
            log(f"待機処理エラー: {exc}")
            if once:
                raise
            time.sleep(max(5.0, config.poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.configure:
        configure()
        return
    run_worker(once=args.once)


if __name__ == "__main__":
    main()
