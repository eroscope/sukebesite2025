from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from article_studio import MAX_IMAGE_BYTES, _validate_source_url
from indanya_desktop.page_extract import (
    EXTRACT_SCRIPT,
    auto_scroll,
    dismiss_common_overlays,
    image_extension,
)
from indanya_desktop.affiliate_opportunities import detect_affiliate_opportunities


ProgressCallback = Callable[[int, str], None]
MAX_X_SCROLL_STEPS = 24
MAX_ANALYSIS_IMAGES = 36
MAX_ANALYSIS_VIDEOS = 16
CHATGPT_URL = "https://chatgpt.com/"
CHATGPT_RESPONSE_TIMEOUT_SECONDS = 5 * 60
CHATGPT_ATTACHMENT_READY_TIMEOUT_SECONDS = 30
CHATGPT_ATTACHMENT_UPLOAD_TIMEOUT_SECONDS = 45
CHATGPT_SUBMIT_TIMEOUT_SECONDS = 45
CHATGPT_PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a6f4a2cd5648191adebf83157266980-yin-tan-wu-ji-shi-zuo-cheng/project"
)


def _register_capture_cleanup(
    cleanup: ExitStack,
    browser: Any,
    context: Any,
) -> None:
    """Close only browser objects created by the current capture."""
    if browser is not None:
        cleanup.callback(browser.close)
    cleanup.callback(context.close)


def parse_fanza_product_identity(
    body_text: Any,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract product facts that FANZA prints explicitly on its detail page."""
    text = str(body_text or "").replace("\u00a0", " ")
    performer_by_name: dict[str, dict[str, str]] = {}
    for item in links or []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("text") or "").split())[:80]
        url = str(item.get("url") or "").strip()
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        query = parse_qs(parsed.query)
        is_actress_list = (
            (parsed.hostname or "").casefold() == "video.dmm.co.jp"
            and parsed.path.rstrip("/") == "/av/list"
            and bool(query.get("actress"))
        )
        if not name or not is_actress_list:
            continue
        performer_by_name.setdefault(name.casefold(), {
            "name": name,
            "url": url,
            "reason": "FANZA商品詳細の出演者欄で確認",
        })

    def field(label: str, limit: int = 160) -> str:
        match = re.search(
            rf"(?:^|\n)\s*{re.escape(label)}\s*[：:]\s*(?:\n\s*)?([^\n]+)",
            text,
            re.IGNORECASE,
        )
        return " ".join(match.group(1).split())[:limit] if match else ""

    # Some product templates render the performer as plain text instead of a
    # link. Use it only when the explicit field is present on the detail page.
    plain_performer = field("出演者", 240)
    explicit_performer_keys: set[str] = set()
    if plain_performer and plain_performer not in {"----", "-"}:
        plain_performer = re.sub(
            r"\s*(?:すべて表示(?:する)?|もっと見る)\s*$",
            "",
            plain_performer,
        ).strip()
        for name in re.split(r"\s*[,、／/|｜・]\s*", plain_performer):
            clean = " ".join(name.split())[:80]
            if clean:
                explicit_performer_keys.add(clean.casefold())
                performer_by_name.setdefault(clean.casefold(), {
                    "name": clean,
                    "url": "",
                    "reason": "FANZA商品詳細の出演者欄で確認",
                })
    # The global navigation also contains links to currently popular actresses.
    # They are not cast members of this product, so the explicit detail field is
    # the authority and every unrelated actress link is discarded.
    compact_field = re.sub(r"[\s\u3000,、／/|｜・]+", "", plain_performer).casefold()
    linked_performers = {
        key: item
        for key, item in performer_by_name.items()
        if key in explicit_performer_keys
        or (
            bool(compact_field)
            and len(re.sub(r"\s+", "", str(item.get("name") or ""))) >= 2
            and re.sub(r"\s+", "", str(item.get("name") or "")).casefold()
            in compact_field
        )
    }
    # FANZA's collapsed performer list can concatenate every linked name into
    # one line. Prefer the individual actress links in that case; otherwise
    # keep the explicit plain-text field for products without linked profiles.
    if linked_performers and any(item.get("url") for item in linked_performers.values()):
        performer_by_name = {
            key: item for key, item in linked_performers.items() if item.get("url")
        }
    else:
        performer_by_name = linked_performers

    return {
        "performers": list(performer_by_name.values()),
        "distribution_code": field("配信品番", 80),
        "maker_code": field("メーカー品番", 80),
        "maker": field("メーカー", 120),
        "label": field("レーベル", 120),
        "series": field("シリーズ", 120),
    }


def collect_rendered_links(url: str) -> list[dict[str, str]]:
    """Collect links from a JavaScript-rendered catalog without downloading media."""
    source_url = _validate_source_url(url)
    hostname = (urlparse(source_url).hostname or "").lower()
    is_dmm_source = hostname == "dmm.co.jp" or hostname.endswith(".dmm.co.jp")
    source_path = urlparse(source_url).path.lower()
    expected_selector = ""
    if hostname == "video.dmm.co.jp":
        expected_selector = 'a[href*="/av/content/"]'
    elif hostname == "book.dmm.co.jp":
        expected_selector = 'a[href*="/product/"]'
    elif is_dmm_source and "/dc/doujin/" in source_path:
        expected_selector = 'a[href*="/dc/doujin/-/detail/"]'

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="ja-JP",
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/136 Safari/537.36"
            ),
        )
        if is_dmm_source:
            context.add_cookies([{
                "name": "age_check_done",
                "value": "1",
                "domain": ".dmm.co.jp",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }])
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )
        try:
            page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            if expected_selector:
                try:
                    page.wait_for_selector(expected_selector, timeout=20000)
                except Exception:
                    # Keep the collected anchors available for diagnostics when
                    # a catalog changes its markup instead of rewriting the
                    # requested FANZA floor to an unrelated video catalog.
                    pass
            rows = page.locator("a[href]").evaluate_all("""elements => elements.map(link => {
                const imageAlt = (link.querySelector('img[alt]')?.getAttribute('alt') || '').trim();
                const visibleText = (link.innerText || '').trim();
                const contentText = (link.textContent || '').trim();
                const text = visibleText
                    || contentText
                    || (link.getAttribute('aria-label') || '').trim()
                    || (link.getAttribute('title') || '').trim()
                    || imageAlt;
                const nearby = [];
                let parent = link.parentElement;
                let context = '';
                for (let depth = 0; parent && depth < 7; depth += 1) {
                    const value = (parent.innerText || '').trim();
                    if (value.length > context.length && value.length <= 1800) context = value;
                    const previous = (parent.previousElementSibling?.innerText || '').trim();
                    if (previous && previous.length <= 300) nearby.push(previous);
                    parent = parent.parentElement;
                }
                const headings = Array.from(document.querySelectorAll('h2, h3, h4'));
                const previousHeading = headings.filter(heading => (
                    heading.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING
                )).pop();
                if (previousHeading) nearby.push((previousHeading.innerText || '').trim());
                const combinedContext = [text, imageAlt, context, ...nearby]
                    .filter(Boolean)
                    .filter((value, index, values) => values.indexOf(value) === index)
                    .join('\\n');
                return {
                    href: link.href || '',
                    text,
                    context: combinedContext,
                    isRotationBanner: Boolean(link.closest('.RotationBnrList')),
                };
            }).filter(item => !item.isRotationBanner)""")
            return [
                {
                    "href": str(item.get("href") or ""),
                    "text": str(item.get("text") or "")[:500],
                    "context": str(item.get("context") or "")[:1000],
                }
                for item in rows
                if isinstance(item, dict) and item.get("href")
            ]
        finally:
            context.close()
            browser.close()


def x_browser_profile_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / "IndanyaStudio" / "x-browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def x_login_ready() -> bool:
    """Return whether the dedicated X profile contains an authenticated session."""
    profile = x_browser_profile_path()
    cookie_files = (
        profile / "Default" / "Network" / "Cookies",
        profile / "Network" / "Cookies",
    )
    cookie_store_read = False
    for cookie_file in cookie_files:
        if not cookie_file.exists():
            continue
        try:
            connection = sqlite3.connect(
                f"file:{cookie_file.as_posix()}?mode=ro",
                uri=True,
                timeout=1,
            )
            try:
                row = connection.execute(
                    "SELECT 1 FROM cookies "
                    "WHERE name = 'auth_token' AND host_key LIKE '%x.com' LIMIT 1"
                ).fetchone()
            finally:
                connection.close()
            cookie_store_read = True
            if row:
                return True
        except sqlite3.Error:
            continue
    if cookie_store_read:
        return False
    return (profile / ".indanya-login-ready").exists()


def chatgpt_browser_profile_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / "IndanyaStudio" / "chatgpt-browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chatgpt_login_ready() -> bool:
    return (chatgpt_browser_profile_path() / ".indanya-login-ready").exists()


def _chatgpt_conversation_target(conversation_url: str) -> str:
    candidate = str(conversation_url or "").strip()
    if not candidate:
        return CHATGPT_PROJECT_URL
    parsed = urlparse(candidate)
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "chatgpt.com"
        and parsed.path.startswith("/c/")
    ):
        return candidate
    return CHATGPT_PROJECT_URL


def _chatgpt_composer(page: Any) -> Any:
    candidates = page.locator(
        '#prompt-textarea, textarea[placeholder*="質問"], '
        'div[contenteditable="true"][data-virtualkeyboard], '
        'div[contenteditable="true"].ProseMirror'
    )
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return candidates.first


class ChatGptRateLimitError(RuntimeError):
    pass


def _looks_like_chatgpt_rate_limit(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return any(term in normalized for term in (
        "リクエストが多すぎます",
        "数分待ってから",
        "利用制限",
        "利用上限",
        "上限に達",
        "メッセージ上限",
        "too many requests",
        "rate limit",
        "usage limit",
        "free plan limit",
        "reached your limit",
        "limit resets",
    ))


def _raise_for_chatgpt_blocking_dialog(page: Any) -> None:
    dialogs = page.locator(
        '[role="dialog"], [role="alert"], '
        '[data-testid*="limit" i], [data-testid*="rate" i]'
    )
    for index in range(dialogs.count()):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            text = dialog.inner_text(timeout=1500).strip()
        except Exception:
            continue
        if _looks_like_chatgpt_rate_limit(text):
            close_buttons = dialog.locator(
                'button:has-text("了解"), button:has-text("OK"), '
                'button:has-text("Got it")'
            )
            if close_buttons.count():
                try:
                    close_buttons.first.click(timeout=1500)
                except Exception:
                    pass
            raise ChatGptRateLimitError(
                "CHATGPT_RATE_LIMIT: ChatGPTの利用制限中です。数分後に自動再開します"
            )


def _wait_for_chatgpt_response(
    page: Any,
    before_count: int,
    progress: ProgressCallback,
    before_text: str = "",
) -> str:
    started = time.monotonic()
    deadline = started + CHATGPT_RESPONSE_TIMEOUT_SECONDS
    completed_since = 0.0
    while time.monotonic() < deadline:
        page.wait_for_timeout(1800)
        # A plan-limit notice often appears only after the send button was
        # pressed. Detect it inside the wait loop so it is not misreported as a
        # 90-second response timeout.
        _raise_for_chatgpt_blocking_dialog(page)
        stop_visible = any(
            locator.count() and locator.first.is_visible()
            for locator in (
                page.locator('button[data-testid="stop-button"]'),
                page.locator('button[aria-label*="停止"]'),
                page.locator('button[aria-label*="Stop"]'),
            )
        )
        assistant = page.locator('[data-message-author-role="assistant"]')
        assistant_count = assistant.count()
        current_text = (
            assistant.last.inner_text().strip()
            if assistant_count
            else ""
        )
        has_new_response = (
            assistant_count > before_count
            or (
                bool(current_text)
                and current_text != before_text
            )
        )
        error_box = page.locator(
            '[data-testid="conversation-turn-error"], .text-token-text-error, '
            '[role="alert"]:has-text("Something went wrong"), '
            '[role="alert"]:has-text("error generating")'
        )
        for index in range(error_box.count()):
            candidate = error_box.nth(index)
            try:
                if candidate.is_visible():
                    detail = candidate.inner_text().strip()
                    raise RuntimeError(
                        f"ChatGPT側で生成エラーになりました: {detail[:240]}"
                    )
            except RuntimeError:
                raise
            except Exception:
                continue
        if has_new_response and not stop_visible:
            if not completed_since:
                completed_since = time.monotonic()
            elif time.monotonic() - completed_since >= 4:
                if current_text:
                    return current_text
        else:
            completed_since = 0.0
        elapsed_seconds = int(time.monotonic() - started)
        phase = "返答を受信中" if has_new_response else "返答開始待ち"
        progress(
            min(92, 20 + elapsed_seconds // 30),
            f"ChatGPT {phase}（{elapsed_seconds // 60}分{elapsed_seconds % 60:02d}秒）",
        )
    raise RuntimeError("ChatGPTの返答が5分以内に完了しませんでした")


def _upload_chatgpt_files(page: Any, paths: list[Path]) -> None:
    if not paths:
        return
    resolved_paths = [str(path.resolve()) for path in paths]
    attachment_ready_deadline = (
        time.monotonic() + CHATGPT_ATTACHMENT_READY_TIMEOUT_SECONDS
    )
    last_plus_click = 0.0
    uploaded = False
    while time.monotonic() < attachment_ready_deadline and not uploaded:
        file_inputs = page.locator('input[type="file"]')
        if file_inputs.count():
            file_inputs.last.set_input_files(resolved_paths)
            uploaded = True
            break

        plus_buttons = page.locator(
            'button[data-testid="composer-plus-btn"], '
            'button[aria-label*="Attach"], button[aria-label*="添付"], '
            'button[aria-label*="ファイル"], button[aria-label*="Add"]'
        )
        visible_plus = None
        for index in range(plus_buttons.count()):
            candidate = plus_buttons.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    visible_plus = candidate
                    break
            except Exception:
                continue
        now = time.monotonic()
        if visible_plus is not None and now - last_plus_click >= 2:
            visible_plus.click(timeout=10000)
            last_plus_click = now
            page.wait_for_timeout(500)

        file_inputs = page.locator('input[type="file"]')
        if file_inputs.count():
            file_inputs.last.set_input_files(resolved_paths)
            uploaded = True
            break

        upload_items = page.locator(
            '[role="menuitem"]:has-text("写真とファイルを追加"), '
            '[role="menuitem"]:has-text("Add photos & files"), '
            '[role="menuitem"]:has-text("Upload from computer"), '
            'button:has-text("写真とファイルを追加"), '
            'button:has-text("Add photos & files")'
        )
        visible_upload_item = None
        for index in range(upload_items.count()):
            candidate = upload_items.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    visible_upload_item = candidate
                    break
            except Exception:
                continue
        if visible_upload_item is not None:
            try:
                with page.expect_file_chooser(timeout=10000) as chooser_info:
                    visible_upload_item.click(timeout=10000)
                chooser_info.value.set_files(resolved_paths)
                uploaded = True
                break
            except Exception:
                file_inputs = page.locator('input[type="file"]')
                if file_inputs.count():
                    file_inputs.last.set_input_files(resolved_paths)
                    uploaded = True
                    break
        page.wait_for_timeout(800)
    if not uploaded:
        raise RuntimeError("ChatGPTが次のファイルを添付できる状態に戻りませんでした")
    page.wait_for_timeout(1200)
    deadline = time.monotonic() + CHATGPT_ATTACHMENT_UPLOAD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        uploading = page.locator(
            '[data-testid*="upload"][aria-busy="true"], '
            '[data-testid*="attachment"][aria-busy="true"], '
            '[data-testid*="file"][aria-busy="true"]'
        )
        busy = False
        for index in range(uploading.count()):
            try:
                if uploading.nth(index).is_visible():
                    busy = True
                    break
            except Exception:
                continue
        if not busy:
            return
        _raise_for_chatgpt_blocking_dialog(page)
        page.wait_for_timeout(700)
    raise RuntimeError("ChatGPTへの証拠画像アップロードが完了しませんでした")


def _set_chatgpt_prompt(page: Any, composer: Any, prompt: str) -> None:
    inserted = composer.evaluate(
        """
        (el, text) => {
          el.focus();
          if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
            const prototype = el instanceof HTMLTextAreaElement
              ? HTMLTextAreaElement.prototype
              : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
            setter.call(el, text);
            el.dispatchEvent(new InputEvent('input', {
              bubbles: true,
              data: text,
              inputType: 'insertText'
            }));
            return true;
          }
          document.execCommand('selectAll', false, null);
          return document.execCommand('insertText', false, text);
        }
        """,
        prompt,
    )
    if not inserted:
        composer.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt)
    page.wait_for_timeout(250)
    composer = _chatgpt_composer(page)
    try:
        current = composer.input_value(timeout=5000)
    except Exception:
        current = composer.inner_text(timeout=5000)
    compact_expected = re.sub(r"\s+", "", prompt)
    compact_current = re.sub(r"\s+", "", current)
    if (
        len(compact_current) < max(1, int(len(compact_expected) * 0.95))
        or compact_current[:80] != compact_expected[:80]
        or compact_current[-80:] != compact_expected[-80:]
    ):
        composer.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(250)
        composer = _chatgpt_composer(page)
        try:
            current = composer.input_value(timeout=5000)
        except Exception:
            current = composer.inner_text(timeout=5000)
        compact_current = re.sub(r"\s+", "", current)
        if (
            len(compact_current) < max(1, int(len(compact_expected) * 0.95))
            or compact_current[:80] != compact_expected[:80]
            or compact_current[-80:] != compact_expected[-80:]
        ):
            raise RuntimeError("ChatGPTの入力欄へ指示文を正しく入力できませんでした")


def _visible_chatgpt_send_button(page: Any) -> Any:
    candidates = page.locator(
        'button[data-testid="send-button"], '
        'button[aria-label*="送信"], button[aria-label*="Send"]'
    )
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return candidates.first


def _submit_chatgpt_message(
    page: Any,
    before_user_count: int,
    progress: ProgressCallback,
) -> None:
    deadline = time.monotonic() + CHATGPT_SUBMIT_TIMEOUT_SECONDS
    last_click = 0.0
    while time.monotonic() < deadline:
        _raise_for_chatgpt_blocking_dialog(page)
        if page.locator('[data-message-author-role="user"]').count() > before_user_count:
            return
        send_button = _visible_chatgpt_send_button(page)
        try:
            can_click = (
                send_button.count()
                and send_button.is_visible()
                and send_button.is_enabled()
                and send_button.get_attribute("aria-disabled") != "true"
            )
        except Exception:
            can_click = False
        now = time.monotonic()
        if can_click and now - last_click >= 2:
            send_button.click(timeout=10000)
            last_click = now
            page.wait_for_timeout(700)
            continue
        progress(18, "画像の準備完了を待ってChatGPTへ送信しています")
        page.wait_for_timeout(800)
    raise RuntimeError(
        "ChatGPTへの画像アップロードまたはメッセージ送信が45秒以内に完了しませんでした"
    )


def _send_chatgpt_message(
    page: Any,
    prompt: str,
    progress: ProgressCallback,
    paths: list[Path] | None = None,
) -> str:
    _raise_for_chatgpt_blocking_dialog(page)
    _upload_chatgpt_files(page, list(paths or []))
    _raise_for_chatgpt_blocking_dialog(page)
    composer = _chatgpt_composer(page)
    composer.wait_for(state="visible", timeout=60000)
    assistant_before = page.locator('[data-message-author-role="assistant"]')
    before_count = assistant_before.count()
    before_text = (
        assistant_before.last.inner_text().strip()
        if before_count
        else ""
    )
    before_user_count = page.locator('[data-message-author-role="user"]').count()
    _set_chatgpt_prompt(page, composer, prompt)
    _submit_chatgpt_message(page, before_user_count, progress)
    return _wait_for_chatgpt_response(
        page,
        before_count,
        progress,
        before_text,
    )


def open_chatgpt_login_session(
    progress: ProgressCallback = lambda _v, _m: None,
) -> None:
    progress(10, "ChatGPTログイン用Chromeを開いています")
    profile = chatgpt_browser_profile_path()
    authenticated = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 850},
            locale="ja-JP",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        progress(45, "ChatGPTへログインしてください。確認後は自動で閉じます")
        while context.pages:
            try:
                for candidate in context.pages:
                    composer = _chatgpt_composer(candidate)
                    if composer.count() and composer.is_visible():
                        authenticated = True
                        page = candidate
                        break
                if authenticated:
                    progress(90, "ChatGPTログインを確認しました")
                    page.wait_for_timeout(1200)
                    break
                context.pages[0].wait_for_timeout(600)
            except Exception:
                break
        try:
            context.close()
        except Exception:
            pass
    if not authenticated:
        raise RuntimeError("ChatGPTへのログインを確認できませんでした")
    (profile / ".indanya-login-ready").write_text(
        "ready\n",
        encoding="utf-8",
    )
    progress(100, "ChatGPTログイン情報を保存しました")


def send_chatgpt_prompt(
    prompt: str,
    progress: ProgressCallback = lambda _v, _m: None,
    attachment_paths: list[Path] | None = None,
    conversation_url: str = "",
) -> dict[str, str]:
    if not chatgpt_login_ready():
        raise RuntimeError("ChatGPT初回ログインが必要です")
    progress(5, "ChatGPT送信用Chromeを起動しています")
    profile = chatgpt_browser_profile_path()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 850},
            locale="ja-JP",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-minimized",
                "--window-position=-10000,-10000",
                "--window-size=1280,850",
            ],
        )
        existing_pages = set(context.pages)
        page = context.new_page()
        try:
            page.goto(
                _chatgpt_conversation_target(conversation_url),
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(700)
            _raise_for_chatgpt_blocking_dialog(page)
            progress(15, "通常のChatGPTへ素材と指示を送っています")
            paths = [
                Path(path) for path in (attachment_paths or [])
                if Path(path).exists()
            ]
            batches = [
                paths[index:index + 8]
                for index in range(0, len(paths), 8)
            ]
            for index, batch in enumerate(batches[:-1], start=1):
                names = "、".join(path.name for path in batch)
                _send_chatgpt_message(
                    page,
                    (
                        f"記事判定用の証拠画像 第{index}組です: {names}\n"
                        "候補番号、ページ上の位置、広告や関連記事との境界、人物・場面・"
                        "動画フレームを確認し、この会話中だけ保持してください。"
                        "まだ記事は書かず、EVIDENCE_OK のみ返してください。"
                    ),
                    progress,
                    batch,
                )
            text = _send_chatgpt_message(
                page,
                prompt,
                progress,
                batches[-1] if batches else [],
            )
            lowered = text.casefold()
            if "action" in lowered and any(
                term in lowered for term in ("allow", "許可", "承認")
            ):
                raise RuntimeError("外部アクション許可が要求されたため処理を中止しました")
            progress(100, "通常のChatGPTから記事判断を受け取りました")
            active_conversation_url = (
                page.url
                if _chatgpt_conversation_target(page.url) == page.url
                else ""
            )
            return {
                "message": text,
                "conversation_url": active_conversation_url,
            }
        finally:
            # Only close pages opened by this operation. Existing login/user tabs
            # in the dedicated profile are deliberately left untouched.
            for owned_page in list(context.pages):
                if owned_page in existing_pages:
                    continue
                try:
                    owned_page.close()
                except Exception:
                    pass
            context.close()


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
        authenticated = False
        while context.pages:
            try:
                authenticated = authenticated or any(
                    str(cookie.get("name") or "") == "auth_token"
                    for cookie in context.cookies("https://x.com")
                )
                if authenticated:
                    progress(85, "Xログインを確認しました。Chromeを閉じてください")
                context.pages[0].wait_for_timeout(500)
            except Exception:
                break
        try:
            context.close()
        except Exception:
            pass
    if not authenticated:
        raise RuntimeError("Xへのログイン完了を確認できませんでした。ログイン後にChromeを閉じてください")
    (x_browser_profile_path() / ".indanya-login-ready").write_text(
        "ready\n",
        encoding="utf-8",
    )
    progress(100, "Xログイン情報を保存しました")


def discover_fanza_products(
    queries: list[str],
    *,
    limit_per_query: int = 2,
    product_kind: str = "video",
    max_queries: int = 4,
    dedupe_across_queries: bool = True,
) -> list[dict[str, str]]:
    cleaned_queries = list(dict.fromkeys(
        " ".join(str(query or "").split())[:80]
        for query in queries
        if " ".join(str(query or "").split())
    ))[:max(1, max_queries)]
    if not cleaned_queries:
        return []
    products: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="ja-JP",
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/136 Safari/537.36",
        )
        page = context.new_page()
        for query in cleaned_queries:
            is_monthly_ranking = query == "人気作品"
            category = {
                "anime": "digital_anime",
                "comic": "comic",
                "doujin": "doujin",
            }.get(product_kind, "digital_videoa")
            search_url = (
                "https://www.dmm.co.jp/digital/videoa/-/ranking/=/term=monthly/"
                if is_monthly_ranking
                else (
                    "https://www.dmm.co.jp/search/?redirect=1&enc=UTF-8&category="
                    + category
                    + "&searchstr="
                    + quote(query, safe="")
                )
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            age_link = page.locator('a[href*="/age_check/=/declared=yes/"]')
            if age_link.count():
                age_link.first.click()
            selectors = {
                "anime": 'a[href*="/anime/content/"]',
                "comic": 'a[href*="book.dmm.co.jp/product/"]',
                "doujin": 'a[href*="/dc/doujin/-/detail/"]',
            }
            product_selector = selectors.get(product_kind, 'a[href*="/av/content/"]')
            try:
                page.wait_for_selector(product_selector, timeout=20000)
            except Exception:
                continue
            links = page.locator(product_selector)
            query_products: dict[str, dict[str, str]] = {}
            for index in range(min(links.count(), 160)):
                link = links.nth(index)
                href = str(link.get_attribute("href") or "")
                absolute = urljoin(page.url, href)
                parsed = urlparse(absolute)
                query_values = parse_qs(parsed.query)
                content_id = str(
                    (query_values.get("id") or query_values.get("cid") or [""])[0]
                )
                if product_kind == "comic":
                    match = re.search(r"/product/\d+/([A-Za-z0-9_-]+)/?$", parsed.path)
                    content_id = match.group(1) if match else ""
                    if "/tachiyomi/" in parsed.path:
                        continue
                elif product_kind == "doujin" and not content_id:
                    match = re.search(r"/cid=([A-Za-z0-9_-]+)", parsed.path)
                    content_id = match.group(1) if match else ""
                if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", content_id):
                    continue
                title = " ".join(link.inner_text().split())[:240]
                image = link.locator("img")
                if not title and image.count():
                    title = " ".join(
                        str(image.first.get_attribute("alt") or "").split()
                    )[:240]
                canonical_url = absolute
                if product_kind in {"video", "anime"}:
                    floor = "anime" if product_kind == "anime" else "av"
                    canonical_url = (
                        f"https://video.dmm.co.jp/{floor}/content/?id="
                        + quote(content_id, safe="")
                    )
                product = query_products.setdefault(content_id.lower(), {
                    "product_id": content_id,
                    "url": canonical_url,
                    "title": "",
                    "thumbnail_url": "",
                    "matched_query": query,
                    "product_kind": product_kind,
                })
                if len(title) > len(product["title"]):
                    product["title"] = title
                if image.count() and not product["thumbnail_url"]:
                    raw_thumbnail = str(
                        image.first.get_attribute("src")
                        or image.first.get_attribute("data-src")
                        or ""
                    )
                    if raw_thumbnail:
                        product["thumbnail_url"] = urljoin(page.url, raw_thumbnail)
            added = 0
            for product_id, product in query_products.items():
                query_terms = [
                    term.casefold()
                    for term in re.findall(r"[A-Za-z0-9一-龥ぁ-んァ-ヶー]+", query)
                    if term.casefold() not in {"av", "動画", "作品", "おすすめ"}
                ]
                normalized_title = product["title"].casefold()
                matched_terms = sum(term in normalized_title for term in query_terms)
                if (
                    (dedupe_across_queries and product_id in seen_ids)
                    or not product["title"]
                    or (not is_monthly_ranking and not query_terms)
                    or (not is_monthly_ranking and matched_terms == 0)
                ):
                    continue
                if dedupe_across_queries:
                    seen_ids.add(product_id)
                products.append(product)
                added += 1
                if added >= max(1, limit_per_query):
                    break
        context.close()
        browser.close()
    return products


def capture_fanza_product_metadata(
    url: str,
    progress: ProgressCallback = lambda _v, _m: None,
) -> dict[str, Any]:
    """Read only product identity text; official media is fetched separately by product id."""
    source_url = _validate_source_url(url)
    progress(10, "FANZAの商品名を確認しています")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1100, "height": 700},
            locale="ja-JP",
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/136 Safari/537.36"
            ),
        )
        context.add_cookies([{
            "name": "age_check_done",
            "value": "1",
            "domain": ".dmm.co.jp",
            "path": "/",
            "secure": True,
            "sameSite": "Lax",
        }])
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font", "stylesheet"}
            else route.continue_(),
        )
        try:
            page.goto(source_url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_function(
                    "() => document.querySelector('h1')?.innerText?.trim().length > 5",
                    timeout=8000,
                )
            except Exception:
                pass
            headings = page.locator("h1").all_inner_texts()
            title = next((text.strip() for text in headings if text.strip()), "")
            if not title:
                title = page.title().strip()
            try:
                body_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body_text = ""
            try:
                page_links = page.locator("a").evaluate_all(
                    """elements => elements.slice(0, 2000).map(element => ({
                        text: (element.innerText || element.textContent || '').trim(),
                        url: element.href || ''
                    }))"""
                )
            except Exception:
                page_links = []
            identity = parse_fanza_product_identity(body_text, page_links)
            performers = [
                item for item in identity.get("performers", [])
                if isinstance(item, dict) and item.get("name")
            ]
            performer_names = [str(item["name"]) for item in performers]
            performer_links = [
                {
                    "url": str(item.get("url") or ""),
                    "text": f"{item['name']} 出演作品",
                    "link_kind": "fanza_performer_page",
                }
                for item in performers if item.get("url")
            ]
            return {
                "url": source_url,
                "requested_url": source_url,
                "title": title,
                "site_name": "FANZA",
                "source_type": "fanza_product",
                "description": "",
                "images": [],
                "videos": [],
                "links": [
                    {"url": source_url, "text": "FANZA商品ページ"},
                    *performer_links,
                ],
                "fanza_people": [
                    {
                        "name": name,
                        "image_ids": [],
                        "reason": "FANZA商品詳細の出演者欄で確認",
                    }
                    for name in performer_names
                ],
                "fanza_performer_name": performer_names[0] if len(performer_names) == 1 else "",
                "fanza_performer_pages": performers,
                "fanza_distribution_code": identity.get("distribution_code", ""),
                "fanza_maker_code": identity.get("maker_code", ""),
                "fanza_maker": identity.get("maker", ""),
                "fanza_label": identity.get("label", ""),
                "fanza_series": identity.get("series", ""),
            }
        finally:
            context.close()
            browser.close()
    return products


def _usable_final_url(value: Any, fallback: str) -> str:
    try:
        return _validate_source_url(str(value or ""))
    except Exception:
        return fallback


def _video_priority(item: dict[str, Any]) -> int:
    kind = str(item.get("kind") or "")
    urls = " ".join(str(value) for value in (item.get("urls") or []))
    if re.search(r"\.mpd(?:[?#]|$)", urls, re.I):
        return -1
    if "video.twimg.com/" in urls:
        match = re.search(r"/(\d+)x(\d+)/", urls)
        if match:
            return -max(int(match.group(1)), int(match.group(2)))
    if kind == "network" or re.search(r"\.(?:mp4|webm|m4v|mov)(?:[?#]|$)", urls, re.I):
        return 0
    if kind != "iframe":
        return 1
    return 2


def _plausible_video_candidate(
    url: str,
    kind: str,
    mime_type: str,
    source_url: str,
) -> bool:
    if _media_url_key(url) == _media_url_key(source_url):
        return False
    if kind == "iframe":
        hostname = (urlparse(url).hostname or "").lower()
        lowered_url = url.lower()
        if any(term in hostname for term in (
            "doubleclick", "adservice", "adnxs", "ladsp", "casalemedia",
            "openx", "ad-stir", "googlesyndication", "recaptcha",
            "comment.blogcms", "platform.twitter", "platform.x.com",
        )):
            return False
        if any(term in lowered_url for term in (
            "google.com/recaptcha/", "/like_frame", "/comment_frame",
            "facebook.com/plugins/", "/share_button", "/embed/comments",
        )):
            return False
        return True
    parsed = urlparse(url)
    path = parsed.path.lower()
    filename = Path(path).name
    # HLS/DASH fragments are not standalone videos. Treating each fragment as
    # one movie created several duplicate, unplayable entries in an article.
    if re.search(r"_(?:init|\d{3,5})_[^/]+\.mp4$", filename):
        return False
    if parsed.hostname == "video.twimg.com":
        if "/aud/" in path or "/mp4a/" in path or path.endswith(".m4s"):
            return False
        if re.search(r"/vid/(?:avc1|hvc1)/0/0/", path):
            return False
        if path.endswith(".mpd"):
            return True
    if re.search(r"\.(?:mp4|webm|m4v|mov)(?:$|/)", path):
        return True
    return mime_type.lower().startswith("video/")


def _image_candidate_urls(raw: dict[str, Any]) -> list[str]:
    values = list(raw.get("urls") or [])
    link_url = str(raw.get("link_url") or "").strip()
    recovered_twitter_url = ""
    for value in [link_url, *values, str(raw.get("url") or "")]:
        match = re.match(
            r"https?://(?:www\.)?ohayua\.cyou/twimg/([A-Za-z0-9_-]+)\.(jpe?g|png|webp)(?:[?#]|$)",
            str(value or "").strip(),
            re.I,
        )
        if match:
            recovered_twitter_url = (
                "https://pbs.twimg.com/media/"
                f"{match.group(1)}?format={match.group(2).lower().replace('jpeg', 'jpg')}&name=large"
            )
            break
    if link_url and re.search(r"\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)", link_url, re.I):
        values.insert(0, link_url)
    values.append(str(raw.get("url") or ""))
    if recovered_twitter_url:
        values.insert(0, recovered_twitter_url)
    return list(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


_NON_ARTICLE_MEDIA_TERMS = (
    "advert", "banner", "sidebar", "widget", "recommend", "related",
    "ranking", "pickup", "feedly", "social", "share", "header", "footer",
    "ninja-recommend", "ac-link", "popular-post", "blogroll", "antenna",
    "広告", "関連記事", "おすすめ", "ランキング",
)


def _likely_article_media(raw: dict[str, Any], source_url: str) -> bool:
    signature = " ".join((
        str(raw.get("ancestors") or ""),
        str(raw.get("context") or ""),
        str(raw.get("title") or ""),
    )).casefold()
    if any(term.casefold() in signature for term in _NON_ARTICLE_MEDIA_TERMS):
        return False
    link_url = str(raw.get("link_url") or "").strip()
    if not link_url:
        return True
    if re.search(r"\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)", link_url, re.I):
        return True
    try:
        linked = urlparse(link_url)
        source = urlparse(source_url)
    except Exception:
        return False
    # Images wrapped by another HTML article or an external antenna link are
    # thumbnails, not material belonging to the current article.
    same_page = (
        linked.netloc.casefold().removeprefix("www.")
        == source.netloc.casefold().removeprefix("www.")
        and linked.path.rstrip("/") == source.path.rstrip("/")
    )
    return same_page


def _dmm_content_id(source_url: str) -> str:
    parsed = urlparse(source_url)
    query_id = str((parse_qs(parsed.query).get("id") or [""])[0]).lower()
    if re.fullmatch(r"[a-z0-9]+", query_id):
        return query_id
    match = re.search(r"(?:cid|id)[=/]([a-z0-9]+)", source_url, re.I)
    return match.group(1).lower() if match else ""


def _redundant_dmm_player(
    candidate_url: str,
    kind: str,
    source_url: str,
    direct_video_urls: list[str],
) -> bool:
    if kind != "iframe" or "html5_player" not in candidate_url.lower():
        return False
    content_id = _dmm_content_id(source_url) or _dmm_content_id(candidate_url)
    return bool(
        content_id
        and content_id in candidate_url.lower()
        and any(content_id in url.lower() for url in direct_video_urls)
    )


def _x_video_asset_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname != "video.twimg.com":
        return ""
    match = re.search(r"/(?:amplify_video|ext_tw_video)/(\d+)/", parsed.path)
    return match.group(1) if match else ""


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


def _sheet_attachments(
    records: list[dict[str, Any]],
    *,
    prefix: str,
    kind: str,
    chunk_size: int = 30,
) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for offset in range(0, len(records), chunk_size):
        chunk = records[offset:offset + chunk_size]
        number = offset // chunk_size + 1
        attachments.append({
            "id": f"{prefix}-{number}",
            "filename": f"{prefix}-{number}.jpg",
            "data": _sheet(chunk),
            "kind": kind,
            "media_ids": [str(item["id"]) for item in chunk],
        })
    return attachments


def _screenshot_bytes(page: Any) -> bytes:
    try:
        article_root = page.locator('[data-indanya-article-root="true"]').first
        if article_root.count() and article_root.is_visible():
            raw = article_root.screenshot(type="jpeg", quality=72, timeout=30000)
        else:
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


def _normalized_text_blocks(values: Any) -> list[str]:
    """Return extractor text for regular pages and merged X snapshots."""
    normalized: list[str] = []
    for item in values or []:
        value = item.get("text") if isinstance(item, dict) else item
        text = str(value or "").strip()
        if text:
            normalized.append(text[:1000])
    return normalized[:80]


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


def _x_capture_scroll_steps(url: Any) -> int:
    """Keep a status capture on that post; profiles may load their timeline."""
    try:
        path = urlparse(str(url or "")).path
    except ValueError:
        path = ""
    return 1 if re.search(r"/status/\d+(?:/|$)", path, re.I) else MAX_X_SCROLL_STEPS


def _collect_x_timeline(page: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    collected: dict[str, Any] = {}
    frames: dict[str, bytes] = {}
    unchanged_rounds = 0
    previous_count = -1
    for _ in range(_x_capture_scroll_steps(page.url)):
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
    is_dmm_source = source_hostname == "dmm.co.jp" or source_hostname.endswith(".dmm.co.jp")
    network_videos: dict[str, dict[str, str]] = {}
    network_x_images: set[str] = set()
    network_x_videos: set[str] = set()
    inspected_x_json = 0
    progress(10, "Chromeでページ全体を開いています")
    with sync_playwright() as playwright, ExitStack() as cleanup:
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
        _register_capture_cleanup(cleanup, browser, context)
        if is_dmm_source:
            context.add_cookies([{
                "name": "age_check_done",
                "value": "1",
                "domain": ".dmm.co.jp",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }])
        page = context.pages[0] if is_x_source and context.pages else context.new_page()

        def on_response(response: Any) -> None:
            nonlocal inspected_x_json
            try:
                content_type = str(response.headers.get("content-type") or "").lower()
                response_url = str(response.url)
                if (
                    content_type.startswith("video/")
                    or "dash+xml" in content_type
                    or re.search(r"\.(?:mp4|webm|mpd)(?:[?#]|$)", response_url, re.I)
                ):
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
        try:
            affiliate_resources = page.locator("script[src], iframe[src]").evaluate_all(
                """
                elements => elements.slice(0, 300).map(element => ({
                  kind: element.tagName.toLowerCase(),
                  url: element.src || element.getAttribute('src') || ''
                })).filter(item => item.url)
                """
            )
        except Exception:
            affiliate_resources = []
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
        raw_images.extend(
            item for item in (extracted.get("thumbnail_images") or [])
            if isinstance(item, dict)
        )
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
        raw_images = [
            raw for raw in raw_images
            if isinstance(raw, dict) and _likely_article_media(raw, final_url)
        ][:MAX_ANALYSIS_IMAGES]
        for raw in raw_images:
            candidate_urls = _image_candidate_urls(raw)
            if not candidate_urls or all(url in seen_urls for url in candidate_urls):
                continue
            best: tuple[int, int, int, str, bytes, str] | None = None
            for candidate_url in candidate_urls:
                if candidate_url in seen_urls:
                    continue
                try:
                    response = request_context.get(
                        candidate_url,
                        headers={"Referer": final_url},
                        timeout=30000,
                        fail_on_status_code=False,
                    )
                    data = response.body() if response.ok else b""
                    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
                    if not data or len(data) > MAX_IMAGE_BYTES or not content_type.startswith("image/"):
                        continue
                    with Image.open(io.BytesIO(data)) as opened:
                        actual_width, actual_height = opened.size
                    if actual_width < 80 or actual_height < 80:
                        continue
                    score = actual_width * actual_height
                    candidate = (score, len(data), actual_width, candidate_url, data, content_type)
                    if best is None or candidate[:2] > best[:2]:
                        best = candidate
                except Exception:
                    continue
            seen_urls.update(candidate_urls)
            if best is None:
                continue
            _, _, width, candidate_url, data, content_type = best
            with Image.open(io.BytesIO(data)) as opened:
                width, height = opened.size
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            images.append({
                "id": f"media-{len(images) + 1}", "url": candidate_url, "data": data,
                "extension": image_extension(content_type, candidate_url), "mime_type": content_type,
                "alt": str(raw.get("alt") or raw.get("title") or extracted.get("title") or "")[:180],
                "orientation": "portrait" if height > width > 0 else "landscape", "width": width, "height": height,
                "browser_context": str(raw.get("context") or "")[:700],
                "browser_ancestors": str(raw.get("ancestors") or "")[:500],
                "browser_rect": raw.get("rect") or {}, "browser_visible": bool(raw.get("visible")),
                "browser_link_url": str(raw.get("link_url") or "")[:2048],
                "inside_article": bool(raw.get("inside_article")),
                "thumbnail_only_candidate": bool(raw.get("thumbnail_only_candidate")),
                "anchor_href_candidate": bool(raw.get("anchor_href_candidate")),
                "thread_reply_number": int(
                    (
                        re.search(
                            r"#(?:surebody|sure|img_)(\d+)",
                            str(raw.get("ancestors") or ""),
                            re.I,
                        )
                        or [None, 0]
                    )[1]
                    or 0
                ),
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

        raw_videos = [
            raw for raw in raw_videos
            if isinstance(raw, dict)
            and (
                raw.get("kind") == "network"
                or _likely_article_media(raw, final_url)
            )
        ]
        raw_videos.sort(key=_video_priority)
        raw_videos = raw_videos[:MAX_ANALYSIS_VIDEOS]
        videos: list[dict[str, Any]] = []
        direct_video_urls = [
            str(candidate_url or "")
            for raw in raw_videos
            if raw.get("kind") != "iframe"
            for candidate_url in raw.get("urls") or []
        ]
        seen_video_urls: set[str] = set()
        seen_x_video_assets: set[str] = set()
        isolated_frame_attempts = 0
        for raw in raw_videos:
            for candidate_url in raw.get("urls") or []:
                candidate_url = str(candidate_url or "").strip()
                if not candidate_url or candidate_url.startswith("blob:") or candidate_url in seen_video_urls:
                    continue
                seen_video_urls.add(candidate_url)
                kind = "iframe" if raw.get("kind") == "iframe" else "direct"
                if is_dmm_source and _redundant_dmm_player(
                    candidate_url, kind, final_url, direct_video_urls
                ):
                    continue
                suffix = Path(candidate_url.split("?", 1)[0]).suffix.lower()
                declared_mime = str(raw.get("mime_type") or "")
                try:
                    validated_url = _validate_source_url(candidate_url)
                except Exception:
                    continue
                if not _plausible_video_candidate(
                    validated_url,
                    kind,
                    declared_mime,
                    final_url,
                ):
                    continue
                x_asset_key = _x_video_asset_key(validated_url)
                if x_asset_key and x_asset_key in seen_x_video_assets:
                    continue
                if x_asset_key:
                    seen_x_video_assets.add(x_asset_key)
                frame_data = video_frames.get(candidate_url) or video_frames.get(_media_url_key(candidate_url))
                if not frame_data and kind == "direct" and isolated_frame_attempts < 8:
                    isolated_frame_attempts += 1
                    frame_data = _capture_isolated_video_frame(context, validated_url, final_url)
                videos.append({
                    "id": f"video-{len(videos) + 1}", "kind": kind, "url": validated_url,
                    "poster": str(raw.get("poster") or ""),
                    "mime_type": "text/html" if kind == "iframe" else str(
                        "video/mp4" if suffix == ".mpd"
                        else declared_mime or ("video/webm" if suffix == ".webm" else "video/mp4")
                    ),
                    "width": int((raw.get("rect") or {}).get("width") or 0), "height": int((raw.get("rect") or {}).get("height") or 0),
                    "title": str(raw.get("title") or "")[:180], "html_class": "", "html_id": "",
                    "browser_context": str(raw.get("context") or "")[:700],
                    "browser_ancestors": str(raw.get("ancestors") or "")[:500], "browser_rect": raw.get("rect") or {},
                    "frame_data": frame_data,
                })
        attachments = [{"id": "page-screenshot", "filename": "page-full.jpg", "data": screenshot, "kind": "full_page"}]
        if images:
            attachments.extend(_sheet_attachments(
                images,
                prefix="candidate-images",
                kind="contact_sheet",
            ))
        video_frame_records = [
            {"id": str(item["id"]), "data": item["frame_data"]}
            for item in videos if isinstance(item.get("frame_data"), bytes)
        ]
        if video_frame_records:
            attachments.extend(_sheet_attachments(
                video_frame_records,
                prefix="video-frames",
                kind="video_contact_sheet",
                chunk_size=12,
            ))
        text_blocks = _normalized_text_blocks(extracted.get("text_blocks"))
        x_authenticated = any(
            str(cookie.get("name") or "") == "auth_token"
            for cookie in (context.cookies("https://x.com") if is_x_source else [])
        )
    x_timeline_media_count = sum(
        1 for item in images
        if "pbs.twimg.com/media/" in str(item.get("url") or "")
    ) + sum(1 for item in videos if item.get("kind") != "iframe")
    result = {
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
    # Keep only the program and exact product evidence. Source-site affiliate
    # IDs embedded in script query strings must never enter a draft or prompt.
    result["affiliate_opportunities"] = detect_affiliate_opportunities({
        **result,
        "affiliate_resources": affiliate_resources,
    })
    return result
