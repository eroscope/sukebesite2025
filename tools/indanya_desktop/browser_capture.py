from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from article_studio import MAX_IMAGE_BYTES, _validate_source_url
from chatgpt_worker import EXTRACT_SCRIPT, auto_scroll, dismiss_common_overlays, image_extension


ProgressCallback = Callable[[int, str], None]
MAX_BROWSER_IMAGES = 32
MAX_BROWSER_VIDEOS = 24
MAX_X_SCROLL_STEPS = 24


def x_browser_profile_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / "IndanyaStudio" / "x-browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_x_login_session(progress: ProgressCallback = lambda _v, _m: None) -> None:
    progress(10, "Xログイン用Chromeを開いています")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(x_browser_profile_path()),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 850},
            locale="ja-JP",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        progress(50, "Xへログインし、終わったらChromeを閉じてください")
        while context.pages:
            try:
                context.pages[0].wait_for_timeout(500)
            except Exception:
                break
        try:
            context.close()
        except Exception:
            pass
    progress(100, "Xログイン情報を保存しました")


def _usable_final_url(value: Any, fallback: str) -> str:
    try:
        return _validate_source_url(str(value or ""))
    except Exception:
        return fallback


def _video_priority(item: dict[str, Any]) -> int:
    kind = str(item.get("kind") or "")
    urls = " ".join(str(value) for value in (item.get("urls") or []))
    if kind == "network" or re.search(r"\.(?:mp4|webm|m4v|mov)(?:[?#]|$)", urls, re.I):
        return 0
    if kind != "iframe":
        return 1
    return 2


def _sheet(images: list[dict[str, Any]]) -> bytes:
    cells = []
    for record in images:
        try:
            with Image.open(io.BytesIO(record["data"])) as opened:
                thumb = opened.convert("RGB")
                thumb.thumbnail((300, 220))
                cell = Image.new("RGB", (320, 260), "white")
                cell.paste(thumb, ((320 - thumb.width) // 2, 28 + (220 - thumb.height) // 2))
                draw = ImageDraw.Draw(cell)
                draw.text((8, 7), record["id"], fill="black", font=ImageFont.load_default())
                cells.append(cell)
        except Exception:
            continue
    columns = 4
    rows = max(1, (len(cells) + columns - 1) // columns)
    result = Image.new("RGB", (columns * 320, rows * 260), "white")
    for index, cell in enumerate(cells):
        result.paste(cell, ((index % columns) * 320, (index // columns) * 260))
    output = io.BytesIO()
    result.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()


def _screenshot_bytes(page: Any) -> bytes:
    try:
        raw = page.screenshot(full_page=True, type="jpeg", quality=72, timeout=30000)
    except Exception:
        raw = page.screenshot(full_page=False, type="jpeg", quality=72, timeout=30000)
    with Image.open(io.BytesIO(raw)) as opened:
        image = opened.convert("RGB")
        image.thumbnail((1800, 12000))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=76, optimize=True)
        return output.getvalue()


def _media_url_key(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    return f"{parsed.netloc.lower()}{parsed.path}" if parsed.netloc and parsed.path else str(value or "")


def _merge_snapshot(target: dict[str, Any], snapshot: dict[str, Any]) -> None:
    for key in ("images", "videos", "links"):
        existing = target.setdefault(key, [])
        signatures = {
            (
                str(item.get("url") or ""),
                tuple(str(value) for value in item.get("urls", [])),
                str(item.get("text") or ""),
            )
            for item in existing
            if isinstance(item, dict)
        }
        for item in snapshot.get(key) or []:
            if not isinstance(item, dict):
                continue
            signature = (
                str(item.get("url") or ""),
                tuple(str(value) for value in item.get("urls", [])),
                str(item.get("text") or ""),
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            existing.append(item)
    for key in ("text_blocks",):
        existing_text = target.setdefault(key, [])
        known = {str(value) for value in existing_text}
        for value in snapshot.get(key) or []:
            text = str(value or "").strip()
            if text and text not in known:
                known.add(text)
                existing_text.append(text)
    body_text = str(snapshot.get("body_text") or "").strip()
    if body_text and body_text not in str(target.get("body_text") or ""):
        target["body_text"] = "\n".join(
            value for value in (str(target.get("body_text") or "").strip(), body_text) if value
        )
    for key in ("title", "description", "final_url", "page"):
        if not target.get(key) and snapshot.get(key):
            target[key] = snapshot[key]


def _reveal_x_media(page: Any) -> None:
    try:
        buttons = page.get_by_role(
            "button",
            name=re.compile(r"(?:センシティブ.*表示|表示する|Show|View)", re.I),
        )
        for index in range(min(buttons.count(), 12)):
            button = buttons.nth(index)
            if button.is_visible():
                button.click(timeout=700)
    except Exception:
        pass


def _collect_x_timeline(page: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    collected: dict[str, Any] = {}
    frames: dict[str, bytes] = {}
    unchanged_rounds = 0
    previous_count = -1
    for _ in range(MAX_X_SCROLL_STEPS):
        _reveal_x_media(page)
        snapshot = page.evaluate(EXTRACT_SCRIPT)
        _merge_snapshot(collected, snapshot)
        frames.update(_capture_video_frames(page))
        current_count = sum(
            len(collected.get(key) or [])
            for key in ("images", "videos", "links", "text_blocks")
        )
        unchanged_rounds = unchanged_rounds + 1 if current_count == previous_count else 0
        previous_count = current_count
        at_bottom = bool(page.evaluate(
            "() => window.scrollY + window.innerHeight >= "
            "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) - 30"
        ))
        if unchanged_rounds >= 4 and at_bottom:
            break
        page.evaluate(
            "() => window.scrollBy(0, Math.max(760, Math.floor(window.innerHeight * 0.82)))"
        )
        page.wait_for_timeout(550)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(250)
    return collected, frames


def _find_x_media_urls(value: Any, image_urls: set[str], video_urls: set[str]) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _find_x_media_urls(nested, image_urls, video_urls)
        return
    if isinstance(value, list):
        for nested in value:
            _find_x_media_urls(nested, image_urls, video_urls)
        return
    if not isinstance(value, str) or not value.startswith("https://"):
        return
    normalized = value.replace("\\/", "/")
    if "pbs.twimg.com/media/" in normalized:
        image_urls.add(normalized)
    elif "video.twimg.com/" in normalized and re.search(r"\.mp4(?:[?#]|$)", normalized, re.I):
        video_urls.add(normalized)


def _video_canvas_frame(video: Any) -> bytes:
    """Read the decoded video pixels without capturing DOM overlays."""
    data_url = video.evaluate("""(element) => {
        if (!element.videoWidth || !element.videoHeight || element.readyState < 2) return "";
        const canvas = document.createElement("canvas");
        canvas.width = element.videoWidth;
        canvas.height = element.videoHeight;
        const context = canvas.getContext("2d", {alpha: false});
        if (!context) return "";
        try {
            context.drawImage(element, 0, 0, canvas.width, canvas.height);
            return canvas.toDataURL("image/jpeg", 0.84);
        } catch (_) {
            return "";
        }
    }""")
    prefix = "data:image/jpeg;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        return b""
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width < 16 or opened.height < 16:
                return b""
            output = io.BytesIO()
            opened.convert("RGB").save(output, "JPEG", quality=84, optimize=True)
            return output.getvalue()
    except Exception:
        return b""


def _capture_video_frames(page: Any) -> dict[str, bytes]:
    frames: dict[str, bytes] = {}
    videos = page.locator("video")
    for index in range(min(videos.count(), 12)):
        video = videos.nth(index)
        try:
            if not video.is_visible() or not video.bounding_box():
                continue
            urls = video.evaluate("""async (element) => {
                const urls = [element.currentSrc, element.src, ...Array.from(element.querySelectorAll('source')).map(node => node.src)].filter(Boolean);
                element.muted = true;
                element.preload = 'auto';
                element.controls = false;
                element.removeAttribute('controls');
                if (element.readyState < 1) {
                    await Promise.race([
                        new Promise(resolve => element.addEventListener('loadedmetadata', resolve, {once: true})),
                        new Promise(resolve => setTimeout(resolve, 2500)),
                    ]);
                }
                const duration = Number.isFinite(element.duration) ? element.duration : 0;
                if (duration > 0.4) {
                    const target = Math.min(Math.max(duration * 0.35, 0.2), duration - 0.1);
                    element.currentTime = target;
                    await Promise.race([
                        new Promise(resolve => element.addEventListener('seeked', resolve, {once: true})),
                        new Promise(resolve => setTimeout(resolve, 2500)),
                    ]);
                    await new Promise(resolve => setTimeout(resolve, 250));
                }
                try {
                    await element.play();
                    await new Promise(resolve => setTimeout(resolve, 180));
                } catch (_) {}
                return urls;
            }""")
            raw = _video_canvas_frame(video)
            video.evaluate("(element) => element.pause()")
            if not raw:
                continue
            for video_url in urls or []:
                frames[str(video_url)] = raw
                frames[_media_url_key(video_url)] = raw
        except Exception:
            continue
    return frames


def _capture_isolated_video_frame(context: Any, video_url: str, referer: str) -> bytes:
    """Render one direct video on an otherwise empty page so source-page ads cannot overlap it."""
    isolated = context.new_page()
    try:
        if referer:
            isolated.set_extra_http_headers({"Referer": referer})
        isolated.set_content(
            '<!doctype html><meta charset="utf-8"><style>'
            'html,body{margin:0;background:#08090a}video{display:block;max-width:100vw;max-height:100vh}'
            '</style><video id="frameVideo" muted playsinline preload="auto"></video>',
            wait_until="domcontentloaded",
            timeout=10000,
        )
        video = isolated.locator("#frameVideo")
        video.evaluate("(element, source) => { element.src = source; element.load(); }", video_url)
        ready = video.evaluate("""async (element) => {
            if (element.readyState < 2) {
                await Promise.race([
                    new Promise(resolve => element.addEventListener("loadeddata", resolve, {once: true})),
                    new Promise(resolve => element.addEventListener("error", resolve, {once: true})),
                    new Promise(resolve => setTimeout(resolve, 7000)),
                ]);
            }
            const duration = Number.isFinite(element.duration) ? element.duration : 0;
            if (duration > 0.5) {
                element.currentTime = Math.min(Math.max(duration * 0.35, 0.2), duration - 0.1);
                await Promise.race([
                    new Promise(resolve => element.addEventListener("seeked", resolve, {once: true})),
                    new Promise(resolve => setTimeout(resolve, 3500)),
                ]);
            }
            return element.readyState >= 2 && element.videoWidth > 0 && element.videoHeight > 0;
        }""")
        if not ready or not video.bounding_box():
            return b""
        raw = video.screenshot(type="jpeg", quality=84, timeout=10000)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width < 16 or opened.height < 16:
                return b""
            output = io.BytesIO()
            opened.convert("RGB").save(output, "JPEG", quality=84, optimize=True)
            return output.getvalue()
    except Exception:
        return b""
    finally:
        isolated.close()


def capture_rendered_source(url: str, progress: ProgressCallback = lambda _v, _m: None) -> dict[str, Any]:
    source_url = _validate_source_url(url)
    source_hostname = (urlparse(source_url).hostname or "").lower()
    is_x_source = source_hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
    network_videos: dict[str, dict[str, str]] = {}
    network_x_images: set[str] = set()
    network_x_videos: set[str] = set()
    inspected_x_json = 0
    progress(10, "Chromeでページ全体を開いています")
    with sync_playwright() as playwright:
        browser = None
        context_options = {
            "viewport": {"width": 1365, "height": 900},
            "locale": "ja-JP",
            "ignore_https_errors": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136 Safari/537.36",
        }
        if is_x_source:
            context = playwright.chromium.launch_persistent_context(
                str(x_browser_profile_path()),
                channel="chrome",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                **context_options,
            )
        else:
            browser = playwright.chromium.launch(
                channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(**context_options)
        page = context.pages[0] if is_x_source and context.pages else context.new_page()

        def on_response(response: Any) -> None:
            nonlocal inspected_x_json
            try:
                content_type = str(response.headers.get("content-type") or "").lower()
                response_url = str(response.url)
                if content_type.startswith("video/") or re.search(r"\.(?:mp4|webm)(?:[?#]|$)", response_url, re.I):
                    try:
                        frame_url = str(response.request.frame.url)
                    except Exception:
                        frame_url = ""
                    network_videos[response_url] = {
                        "frame_url": frame_url,
                        "resource_type": str(response.request.resource_type or ""),
                        "content_type": content_type.split(";", 1)[0],
                    }
                if (
                    is_x_source
                    and inspected_x_json < 40
                    and ("json" in content_type or "/graphql/" in response_url)
                    and ("x.com/" in response_url or "twitter.com/" in response_url)
                ):
                    inspected_x_json += 1
                    try:
                        _find_x_media_urls(response.json(), network_x_images, network_x_videos)
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        dismiss_common_overlays(page)
        if is_x_source:
            extracted, video_frames = _collect_x_timeline(page)
        else:
            auto_scroll(page)
            extracted = page.evaluate(EXTRACT_SCRIPT)
            video_frames = _capture_video_frames(page)
        progress(24, "遅れて表示される画像と動画を確認しています")
        screenshot = _screenshot_bytes(page)
        # Chrome may expose chrome-error://chromewebdata for a blocked navigation.
        # Keep the requested URL so the caller can still use captured evidence.
        final_url = _usable_final_url(extracted.get("final_url") or page.url, source_url)
        request_context = context.request
        images: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        raw_images = list(extracted.get("images") or [])
        raw_images.extend({
            "url": image_url,
            "alt": str(extracted.get("title") or "X投稿画像"),
            "title": "X timeline media",
            "natural_width": 0,
            "natural_height": 0,
            "visible": True,
            "rect": {},
            "context": "Xプロフィールの公開投稿で読み込まれた画像",
            "ancestors": "X timeline network response",
            "link_url": "",
        } for image_url in sorted(network_x_images))
        for video in extracted.get("videos") or []:
            poster = str(video.get("poster") or "").strip()
            if poster:
                raw_images.append({
                    "url": poster,
                    "alt": str(video.get("title") or extracted.get("title") or ""),
                    "title": "video poster",
                    "natural_width": int((video.get("rect") or {}).get("width") or 0),
                    "natural_height": int((video.get("rect") or {}).get("height") or 0),
                    "visible": bool(video.get("visible")),
                    "rect": video.get("rect") or {},
                    "context": str(video.get("context") or ""),
                    "ancestors": str(video.get("ancestors") or ""),
                    "link_url": "",
                })
        for raw in raw_images:
            if len(images) >= MAX_BROWSER_IMAGES:
                break
            candidate_url = str(raw.get("url") or "").strip()
            if not candidate_url or candidate_url in seen_urls:
                continue
            seen_urls.add(candidate_url)
            try:
                response = request_context.get(candidate_url, headers={"Referer": final_url}, timeout=30000, fail_on_status_code=False)
                data = response.body() if response.ok else b""
                content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            except Exception:
                continue
            if not data or len(data) > MAX_IMAGE_BYTES or not content_type.startswith("image/"):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            width = int(raw.get("natural_width") or (raw.get("rect") or {}).get("width") or 0)
            height = int(raw.get("natural_height") or (raw.get("rect") or {}).get("height") or 0)
            images.append({
                "id": f"media-{len(images) + 1}", "url": candidate_url, "data": data,
                "extension": image_extension(content_type, candidate_url), "mime_type": content_type,
                "alt": str(raw.get("alt") or raw.get("title") or extracted.get("title") or "")[:180],
                "orientation": "portrait" if height > width > 0 else "landscape", "width": width, "height": height,
                "browser_context": str(raw.get("context") or "")[:700],
                "browser_ancestors": str(raw.get("ancestors") or "")[:500],
                "browser_rect": raw.get("rect") or {}, "browser_visible": bool(raw.get("visible")),
                "browser_link_url": str(raw.get("link_url") or "")[:2048],
            })
        progress(38, "動画通信とプレイヤーを照合しています")
        raw_videos = list(extracted.get("videos") or [])
        raw_videos.extend({
            "kind": "network",
            "urls": [network_url],
            "context": f"network response; frame={details['frame_url']}; resource_type={details['resource_type']}",
            "ancestors": f"network-frame:{details['frame_url']}",
            "mime_type": details["content_type"],
        } for network_url, details in sorted(network_videos.items()))
        raw_videos.extend({
            "kind": "network",
            "urls": [video_url],
            "context": "Xプロフィールの公開投稿で読み込まれた動画",
            "ancestors": "X timeline network response",
            "mime_type": "video/mp4",
        } for video_url in sorted(network_x_videos))

        raw_videos.sort(key=_video_priority)
        videos: list[dict[str, Any]] = []
        seen_video_urls: set[str] = set()
        isolated_frame_attempts = 0
        for raw in raw_videos:
            for candidate_url in raw.get("urls") or []:
                candidate_url = str(candidate_url or "").strip()
                if not candidate_url or candidate_url.startswith("blob:") or candidate_url in seen_video_urls:
                    continue
                if len(videos) >= MAX_BROWSER_VIDEOS:
                    break
                seen_video_urls.add(candidate_url)
                kind = "iframe" if raw.get("kind") == "iframe" else "direct"
                suffix = Path(candidate_url.split("?", 1)[0]).suffix.lower()
                try:
                    validated_url = _validate_source_url(candidate_url)
                except Exception:
                    continue
                frame_data = video_frames.get(candidate_url) or video_frames.get(_media_url_key(candidate_url))
                if not frame_data and kind == "direct" and isolated_frame_attempts < 8:
                    isolated_frame_attempts += 1
                    frame_data = _capture_isolated_video_frame(context, validated_url, final_url)
                videos.append({
                    "id": f"video-{len(videos) + 1}", "kind": kind, "url": validated_url,
                    "poster": str(raw.get("poster") or ""),
                    "mime_type": "text/html" if kind == "iframe" else str(
                        raw.get("mime_type") or ("video/webm" if suffix == ".webm" else "video/mp4")
                    ),
                    "width": int((raw.get("rect") or {}).get("width") or 0), "height": int((raw.get("rect") or {}).get("height") or 0),
                    "title": str(raw.get("title") or "")[:180], "html_class": "", "html_id": "",
                    "browser_context": str(raw.get("context") or "")[:700],
                    "browser_ancestors": str(raw.get("ancestors") or "")[:500], "browser_rect": raw.get("rect") or {},
                    "frame_data": frame_data,
                })
        attachments = [{"id": "page-screenshot", "filename": "page-full.jpg", "data": screenshot, "kind": "full_page"}]
        if images:
            attachments.append({"id": "candidate-sheet", "filename": "candidate-images.jpg", "data": _sheet(images), "kind": "contact_sheet"})
        video_frame_records = [
            {"id": str(item["id"]), "data": item["frame_data"]}
            for item in videos if isinstance(item.get("frame_data"), bytes)
        ]
        if video_frame_records:
            attachments.append({
                "id": "video-frame-sheet", "filename": "video-frames.jpg",
                "data": _sheet(video_frame_records), "kind": "video_contact_sheet",
            })
        text_blocks = [str(item.get("text") or "")[:1000] for item in (extracted.get("text_blocks") or []) if item.get("text")][:80]
        x_authenticated = any(
            str(cookie.get("name") or "") == "auth_token"
            for cookie in (context.cookies("https://x.com") if is_x_source else [])
        )
        context.close()
        if browser is not None:
            browser.close()
    x_timeline_media_count = sum(
        1 for item in images
        if "pbs.twimg.com/media/" in str(item.get("url") or "")
    ) + sum(1 for item in videos if item.get("kind") != "iframe")
    return {
        "source_type": "web", "url": final_url, "requested_url": source_url,
        "title": str(extracted.get("title") or "")[:180], "description": str(extracted.get("description") or "")[:500],
        "site_name": urlparse(final_url).hostname or "元ページ", "author": "", "excerpts": text_blocks[:8],
        "body_text": str(extracted.get("body_text") or "")[:30000], "text_blocks": text_blocks,
        "links": [
            {
                "url": str(item.get("url") or "")[:2048],
                "text": str(item.get("text") or "")[:500],
                "contains_image": bool(item.get("contains_image")),
                "browser_rect": item.get("rect") or {},
                "browser_context": str(item.get("context") or "")[:700],
                "browser_ancestors": str(item.get("ancestors") or "")[:500],
                "font_size": str(item.get("font_size") or "")[:40],
                "font_weight": str(item.get("font_weight") or "")[:40],
                "color": str(item.get("color") or "")[:80],
                "background": str(item.get("background") or "")[:80],
            }
            for item in (extracted.get("links") or [])
            if isinstance(item, dict) and item.get("url")
        ][:200],
        "images": images, "videos": videos, "browser_attachments": attachments,
        "browser_capture": True, "page_dimensions": extracted.get("page") or {},
        "x_authenticated": x_authenticated if is_x_source else False,
        "x_timeline_media_count": x_timeline_media_count if is_x_source else 0,
    }
