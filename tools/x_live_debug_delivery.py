from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page, sync_playwright

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from article_studio import JST  # noqa: E402
from indanya_desktop.social_x import (  # noqa: E402
    advance_x_thread,
    canonical_x_status_url,
    list_x_posts,
    save_x_posts,
    update_x_post,
    x_browser_profile_path,
    x_status_id,
)


def _row(site_root: Path, post_id: str) -> dict[str, Any]:
    row = next(
        (
            item
            for item in list_x_posts(site_root)
            if str(item.get("post_id") or "") == post_id
        ),
        None,
    )
    if row is None:
        raise RuntimeError(f"X queue item not found: {post_id}")
    return row


def _click_submit(page: Page) -> None:
    buttons = page.locator(
        '[data-testid="tweetButton"], [data-testid="tweetButtonInline"]'
    )
    for _ in range(240):
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if (
                button.is_visible()
                and button.is_enabled()
                and button.get_attribute("aria-disabled") != "true"
            ):
                button.click(timeout=10_000)
                return
        page.wait_for_timeout(500)
    raise RuntimeError("X submit button did not become enabled")


def _posted_url(page: Page) -> str:
    alert_link = page.locator('[role="alert"] a[href*="/status/"]').first
    try:
        alert_link.wait_for(state="visible", timeout=30_000)
        href = str(alert_link.get_attribute("href") or "")
        if href.startswith("/"):
            href = "https://x.com" + href
        return canonical_x_status_url(href)
    except Exception:
        own_links = page.locator('a[href^="/indanya_sns/status/"]')
        for index in range(own_links.count()):
            href = str(own_links.nth(index).get_attribute("href") or "")
            try:
                return canonical_x_status_url("https://x.com" + href)
            except ValueError:
                continue
    raise RuntimeError("X accepted the submit action but the new status URL was not found")


def _send(
    page: Page,
    text: str,
    media_paths: list[str],
    reply_to: str = "",
) -> str:
    if reply_to:
        target = canonical_x_status_url(reply_to)
        url = "https://twitter.com/intent/tweet?" + urlencode(
            {"in_reply_to": x_status_id(target), "text": text}
        )
    else:
        url = "https://x.com/compose/post"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    composer = page.locator(
        '[data-testid="tweetTextarea_0"], div[role="textbox"]'
    ).first
    composer.wait_for(state="visible", timeout=30_000)
    composer.click()
    composer.fill(text)

    files = [str(Path(value).resolve()) for value in media_paths if Path(value).is_file()]
    if len(files) != len(media_paths):
        raise RuntimeError("One or more X media files are missing")
    if files:
        upload = page.locator(
            'input[data-testid="fileInput"], input[type="file"]'
        ).first
        upload.wait_for(state="attached", timeout=15_000)
        upload.set_input_files(files)
        page.wait_for_timeout(5_000)

    _click_submit(page)
    page.wait_for_timeout(2_000)
    return _posted_url(page)


def _send_normal(site_root: Path, post_id: str, page: Page) -> str:
    row = _row(site_root, post_id)
    if str(row.get("delivery_mode") or "post") == "reply":
        raise RuntimeError("Use the reply flow for reply items")
    if row.get("status") == "posted":
        raise RuntimeError("This X queue item is already posted")
    posted_url = _send(
        page,
        str(row.get("post_text") or "").strip(),
        [str(value) for value in row.get("media_paths") or []],
    )
    completed_at = datetime.now(JST).isoformat(timespec="seconds")
    update_x_post(
        site_root,
        post_id,
        status="posted",
        posted_at=completed_at,
        scheduled_at=completed_at,
        x_post_url=posted_url,
        last_error="",
    )
    return posted_url


def _send_thread_step(site_root: Path, post_id: str, page: Page) -> str:
    row = _row(site_root, post_id)
    steps = row.get("thread_steps") or []
    index = int(row.get("thread_step_index") or 0)
    if not steps or index >= len(steps):
        raise RuntimeError("This manga thread is already complete or has no steps")
    step = steps[index]
    posted_urls = [str(value) for value in row.get("thread_post_urls") or []]
    reply_to = posted_urls[-1] if posted_urls else ""
    posted_url = _send(
        page,
        str(step.get("text") or "").strip(),
        [str(value) for value in step.get("media_paths") or []],
        reply_to=reply_to,
    )
    advance_x_thread(site_root, post_id, posted_url)
    return posted_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("post_id")
    parser.add_argument("--thread-step", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        raise SystemExit("Refusing live X delivery without --confirm-live")

    site_root = Path.cwd().resolve()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(x_browser_profile_path()),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 850},
            locale="ja-JP",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            cookies = context.cookies("https://x.com")
            if not any(str(cookie.get("name") or "") == "auth_token" for cookie in cookies):
                raise RuntimeError("The IndanyaStudio X profile is not logged in")
            page = context.pages[0] if context.pages else context.new_page()
            if args.thread_step:
                posted_url = _send_thread_step(site_root, args.post_id, page)
            else:
                posted_url = _send_normal(site_root, args.post_id, page)
            print(posted_url, flush=True)
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
