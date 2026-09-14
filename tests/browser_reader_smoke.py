"""Offline browser checks; external network requests and affiliate links are blocked."""
import functools
import http.server
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parents[1] / "outputs" / "reader-growth-qa"
FIXTURE = '''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="../assets/common/age-gate.js?v=9" data-site-root="../" defer></script></head>
<body><nav class="nav-inner"></nav><article class="article"><h1>テスト記事</h1><p>保存と履歴のテスト。</p></article></body></html>'''


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    checks = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chrome")
            try:
                context = browser.new_context()
                requests = []
                def route(request):
                    url = request.request.url
                    requests.append(url)
                    if urlsplit(url).hostname != "127.0.0.1":
                        request.abort()
                    elif urlsplit(url).path.endswith("/reader-qa-fixture.html"):
                        request.fulfill(status=200, content_type="text/html", body=FIXTURE)
                    else:
                        request.continue_()
                context.route("**/*", route)
                context.add_init_script("localStorage.setItem('indanya-age-confirmed', String(Date.now()))")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for width, height in [(360, 800), (390, 844), (1280, 900)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(base + "/index.html", wait_until="networkidle")
                    assert page.locator('#articleGrid a[href^="articles/"]').count() >= 12
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"overflow {width}"
                    assert page.locator("#featureReadMore").inner_text() == "記事を読む"
                    assert page.locator("#featureImage").evaluate("e => e.complete && e.naturalWidth > 0")
                    # Screenshots use a neutral local brand asset, not remote research media.
                    page.locator('img[src*="assets/articles/"]').evaluate_all("els => els.forEach(e => {e.src='assets/common/indanya-logo.png'})")
                    page.screenshot(path=str(OUTPUT / f"home-{width}.png"))
                    checks.append(f"home-{width}: static links, no horizontal overflow")
                assert not any("data/articles.json" in url for url in requests)
                page.goto(base + "/latest-2.html", wait_until="networkidle")
                assert page.locator("#catalogGrid .post-card").count() == 24
                assert page.locator('#catalogPagination a[rel="next"]').get_attribute("href") == "latest-3.html"
                page.goto(base + "/articles/reader-qa-fixture.html", wait_until="networkidle")
                page.get_by_role("button", name="この記事を保存", exact=True).click()
                assert page.get_by_role("button", name="保存済み", exact=True).get_attribute("aria-pressed") == "true"
                page.get_by_role("link", name="保存した記事", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.locator('[data-reader-list="saved"] a').count() == 1
                assert page.locator('[data-reader-list="recent"] a').count() == 1
                page.screenshot(path=str(OUTPUT / "saved-mobile.png"))
                page.locator('[data-reader-list="saved"] button').click()
                assert page.locator('[data-reader-list="saved"] a').count() == 0
                checks.append("save, recent, remove: passed")
                context.close()

                blocked = browser.new_context()
                blocked.route("**/*", route)
                blocked.add_init_script("for (const name of ['localStorage','sessionStorage']) Object.defineProperty(window,name,{get(){throw new Error('blocked')}})")
                page = blocked.new_page()
                page.goto(base + "/articles/reader-qa-fixture.html?utm_source=x", wait_until="networkidle")
                assert "/age-check.html" in page.url
                page.locator("#ageEnter").click()
                page.wait_for_url("**/articles/reader-qa-fixture.html**")
                page.wait_for_load_state("networkidle")
                assert "/age-check.html" not in page.url and "age_passed" not in page.url
                assert "utm_source=x" in page.url
                page.get_by_role("button", name="この記事を保存", exact=True).click()
                assert "保存できません" in page.locator(".reader-status").inner_text()
                checks.append("storage blocked: age handoff and save error passed")
                blocked.close()

                nojs = browser.new_context(java_script_enabled=False)
                nojs.route("**/*", route)
                page = nojs.new_page()
                page.goto(base + "/index.html")
                assert page.locator("#featureTitleLink").is_visible()
                page.goto(base + "/latest-2.html")
                assert page.locator("#catalogGrid .post-card").count() == 24
                checks.append("JavaScript disabled: home and page 2 readable")
                nojs.close()
                analytics = browser.new_context()
                analytics_html = '''<!doctype html><html><head><script>window.INDANYA_GA4={measurementId:'G-TESTONLY',trackingVersion:9};</script>
                <script src="/ga4.js" defer></script><script src="/reader.js" data-site-root="/" defer></script></head><body>
                <article class="article"><h1>Test article</h1><div class="person-discovery-links"><a id="official" href="https://profiles.example/person">Official</a></div>
                <div class="article-static-discovery"><a id="related" href="/articles/second.html">Related</a></div>
                <a id="pr" rel="sponsored" href="https://shop.example/product">Product</a></article>
                <script>document.addEventListener('click',e=>{if(e.target.closest('a'))e.preventDefault()});</script></body></html>'''
                def offline_analytics(route):
                    path = urlsplit(route.request.url).path
                    if urlsplit(route.request.url).hostname == "qa.example":
                        if path in {"/ga4.js", "/reader.js"}:
                            route.fulfill(content_type="text/javascript", body=(ROOT / "assets/common" / path.lstrip("/")).read_text(encoding="utf-8"))
                        else:
                            route.fulfill(content_type="text/html", body=analytics_html)
                    elif "googletagmanager.com/gtag/js" in route.request.url:
                        route.fulfill(content_type="text/javascript", body="")
                    else:
                        route.abort()
                analytics.route("**/*", offline_analytics)
                page = analytics.new_page()
                page.goto("https://qa.example/articles/reader-qa-fixture.html?utm_campaign=av_shelf&indanya_owner=secret", wait_until="networkidle")
                page.locator("#official").click()
                page.locator("#related").click()
                page.locator("#pr").click()
                page.locator('.reader-actions button').click()
                layer = page.evaluate("dataLayer.map(x=>Array.from(x))")
                names = [row[1] for row in layer if row[0] == "event"]
                assert {"page_view", "article_view", "official_link_click", "related_article_click", "article_pr_click", "article_save"}.issubset(names), names
                serialized = page.evaluate("JSON.stringify(dataLayer)")
                assert "indanya_owner" not in serialized and "secret" not in serialized
                checks.append("offline GA4: page/article/official/related/PR/save events; no owner token leakage")
                analytics.close()
                assert not errors, errors
                (OUTPUT / "results.json").write_text(json.dumps({"checks": checks, "errors": errors}, indent=2), encoding="utf-8")
                print(json.dumps(checks))
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
