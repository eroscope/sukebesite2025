(async () => {
  "use strict";

  const config = window.INDANYA_GA4 || {};
  const measurementId = String(config.measurementId || "").trim();
  if (!/^G-[A-Z0-9]+$/i.test(measurementId)) return;

  // Some generated article pages load this script before <body> starts.
  if (!document.body) {
    await new Promise(resolve => {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", resolve, { once: true });
      } else {
        resolve();
      }
    });
  }
  if (!document.body) return;

  const ownerStorageKey = "indanya-ga4-owner-v2";
  const ownerParameter = "indanya_owner";
  const visitWindowMs = 30 * 60 * 1000;

  async function sha256(value) {
    if (!window.crypto?.subtle || typeof TextEncoder === "undefined") return "";
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  }

  function showRegistrationNotice(message, success) {
    const notice = document.createElement("div");
    notice.textContent = message;
    notice.setAttribute("role", "status");
    notice.style.cssText = [
      "position:fixed", "right:14px", "bottom:14px", "z-index:2147483647",
      "padding:11px 15px", `background:${success ? "#171510" : "#a51d17"}`,
      "color:#fff", "font:700 13px/1.4 sans-serif", "box-shadow:0 3px 14px #0005",
    ].join(";");
    const display = () => {
      document.body.append(notice);
      window.setTimeout(() => notice.remove(), 5000);
    };
    if (document.body) display();
    else document.addEventListener("DOMContentLoaded", display, { once: true });
  }

  async function ownerBrowser() {
    try {
      const url = new URL(location.href);
      const registration = String(url.searchParams.get(ownerParameter) || "").trim();
      if (registration === "clear") {
        localStorage.removeItem(ownerStorageKey);
        url.searchParams.delete(ownerParameter);
        history.replaceState(null, "", url.href);
        showRegistrationNotice("このブラウザの管理者登録を解除しました", true);
      } else if (/^[A-Za-z0-9_-]{32,}$/.test(registration)) {
        const expected = String(config.ownerTokenHash || "").toLowerCase();
        const actual = await sha256(registration);
        url.searchParams.delete(ownerParameter);
        history.replaceState(null, "", url.href);
        if (expected && actual === expected) {
          localStorage.setItem(ownerStorageKey, "1");
          showRegistrationNotice("このブラウザを管理者として登録しました", true);
        } else {
          showRegistrationNotice("管理者登録URLを確認できませんでした", false);
        }
      }
      return localStorage.getItem(ownerStorageKey) === "1";
    } catch {
      return false;
    }
  }

  const isOwner = await ownerBrowser();
  const isArticle = /\/articles\/[^/]+\.html$/i.test(location.pathname);
  document.documentElement.dataset.indanyaAnalytics = isOwner ? "owner-v2" : "external-v2";
  const articleSlug = document.body.dataset.articleSlug
    || location.pathname.replace(/^.*\//, "").replace(/\.html$/, "")
    || "home";
  const articleTitle = String(document.title || "")
    .replace(/\s*[｜|].*$/, "")
    .trim()
    .slice(0, 100);
  const articleCategory = String(document.body.dataset.articleCategory || (() => {
    const breadcrumb = document.querySelector(".breadcrumb");
    if (!breadcrumb) return "";
    const parts = breadcrumb.textContent.split("›").map(value => value.trim()).filter(Boolean);
    return parts.length > 1 ? parts[1] : "";
  })()).slice(0, 100);
  const seenPromotions = new WeakSet();
  const pendingPromotions = new WeakMap();

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
  document.head.append(script);

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;
  gtag("js", new Date());
  gtag("config", measurementId, { send_page_view: false });

  function eventName(name) {
    return isOwner ? `owner_${name}` : name;
  }

  function beginArticleVisit() {
    if (!isArticle) return false;
    const key = `indanya-ga4-visit-v3:${isOwner ? "owner" : "external"}`;
    try {
      const previous = Number(localStorage.getItem(key) || 0);
      const now = Date.now();
      if (Number.isFinite(previous) && now - previous < visitWindowMs) return false;
      localStorage.setItem(key, String(now));
      return true;
    } catch {
      return true;
    }
  }

  function commonDetails() {
    return {
      article_slug: articleSlug,
      article_title: articleTitle,
      content_group: articleCategory || "未分類",
      tracking_version: String(config.trackingVersion || "3"),
      transport_type: "beacon",
    };
  }

  function promotionDetails(element) {
    const promotion = element.closest(".fanza-product");
    const link = element.matches("a") ? element : promotion?.querySelector("a[href]");
    let linkDomain = "";
    try { linkDomain = link ? new URL(link.href).hostname : ""; } catch { /* ignored */ }
    return {
      ...commonDetails(),
      promotion_id: promotion?.dataset.prId || "legacy-pr",
      promotion_name: String(
        promotion?.querySelector(".fanza-product-title")?.textContent || link?.textContent || ""
      ).trim().slice(0, 100),
      pr_kind: promotion?.dataset.prKind || "affiliate",
      link_domain: linkDomain,
    };
  }

  function isPromotionLink(link) {
    if (link.classList.contains("fanza-product-button")) return true;
    if ((link.rel || "").split(/\s+/).includes("sponsored")) return true;
    try { return /(^|\.)(?:dmm|fanza)\.co\.jp$/i.test(new URL(link.href).hostname); }
    catch { return false; }
  }

  gtag("event", eventName("page_view"), commonDetails());
  if (isArticle) {
    gtag("event", eventName("article_view"), commonDetails());
    if (beginArticleVisit()) gtag("event", eventName("article_visit"), commonDetails());
  }

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          const previous = pendingPromotions.get(entry.target);
          if (previous) {
            window.clearTimeout(previous);
            pendingPromotions.delete(entry.target);
          }
          if (!entry.isIntersecting || entry.intersectionRatio < 0.5 || seenPromotions.has(entry.target)) {
            continue;
          }
          const timer = window.setTimeout(() => {
            if (seenPromotions.has(entry.target)) return;
            seenPromotions.add(entry.target);
            const base = isArticle ? "article_pr_impression" : "pr_impression";
            gtag("event", eventName(base), promotionDetails(entry.target));
            observer.unobserve(entry.target);
            pendingPromotions.delete(entry.target);
          }, 1000);
          pendingPromotions.set(entry.target, timer);
        }
      }, { threshold: [0, 0.5] })
    : null;

  if (observer) document.querySelectorAll(".fanza-product").forEach(item => observer.observe(item));
  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!link || !isPromotionLink(link)) return;
    const base = isArticle ? "article_pr_click" : "pr_click";
    gtag("event", eventName(base), {
      ...promotionDetails(link),
      link_url: link.href,
      event_timeout: 1000,
    });
  }, { capture: true });
})();
