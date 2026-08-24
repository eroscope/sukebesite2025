(async () => {
  "use strict";

  const config = window.INDANYA_GA4 || {};
  const measurementId = String(config.measurementId || "").trim();
  const siteKey = String(config.ownerSiteKey || "").trim();
  const collectorBase = String(config.ownerCollector || "").replace(/\/$/, "");
  if (!/^G-[A-Z0-9]+$/i.test(measurementId)) return;

  // Some generated article pages load this script before <body> starts.
  document.documentElement.dataset.indanyaAnalyticsStatus = "waiting-body";
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
  document.documentElement.dataset.indanyaAnalyticsStatus = "body-ready";

  const ownerStorageKey = "indanya-ga4-owner-v2";
  const ownerSessionKey = `indanya-owner-session-v1:${siteKey}`;
  const ownerQueueKey = `indanya-owner-queue-v1:${siteKey}`;
  const ownerParameter = "indanya_owner";
  const visitWindowMs = 30 * 60 * 1000;
  const queueMaxAgeMs = 8 * 24 * 60 * 60 * 1000;
  let flushingOwnerQueue = false;

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
    document.body.append(notice);
    window.setTimeout(() => notice.remove(), 6000);
  }

  function browserLabel() {
    const ua = navigator.userAgent || "";
    const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : "Other";
    const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android"
      : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : "Other";
    return `${browser} / ${os}`;
  }

  function deviceDetails() {
    const ua = navigator.userAgent || "";
    return {
      deviceCategory: /Mobi|Android|iPhone/i.test(ua) ? "mobile" : "desktop",
      operatingSystem: /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android"
        : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : "Other",
      browser: /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome"
        : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Other",
    };
  }

  async function collectorRequest(payload) {
    if (!siteKey || !collectorBase) throw new Error("collector-not-configured");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(`${collectorBase}/events`, {
        method: "POST",
        mode: "cors",
        cache: "no-store",
        keepalive: true,
        targetAddressSpace: "local",
        signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Indanya-Site": siteKey },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result?.ok) throw new Error(result?.error || `collector-${response.status}`);
      return result;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function registerLocalCollector(ownerToken) {
    const result = await collectorRequest({
      action: "register",
      ownerToken,
      browserLabel: browserLabel(),
    });
    const sessionToken = String(result.sessionToken || "");
    if (!/^[A-Za-z0-9_-]{32,}$/.test(sessionToken)) throw new Error("collector-session-invalid");
    localStorage.setItem(ownerSessionKey, sessionToken);
  }

  async function ownerBrowser() {
    try {
      const url = new URL(location.href);
      const registration = String(url.searchParams.get(ownerParameter) || "").trim();
      if (registration === "clear") {
        localStorage.removeItem(ownerStorageKey);
        localStorage.removeItem(ownerSessionKey);
        localStorage.removeItem(ownerQueueKey);
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
          try {
            await registerLocalCollector(registration);
            showRegistrationNotice("このブラウザを管理者として登録しました", true);
          } catch {
            localStorage.removeItem(ownerSessionKey);
            showRegistrationNotice("管理者登録は確認済みです。記事編集室を起動して、もう一度登録してください", false);
          }
        } else {
          showRegistrationNotice("管理者登録URLを確認できませんでした", false);
        }
      }
      return localStorage.getItem(ownerStorageKey) === "1";
    } catch {
      return false;
    }
  }

  function readOwnerQueue() {
    try {
      const now = Date.now();
      const queue = JSON.parse(localStorage.getItem(ownerQueueKey) || "[]");
      if (!Array.isArray(queue)) return [];
      return queue.filter(item => item && now - Date.parse(item.timestamp || "") < queueMaxAgeMs).slice(-500);
    } catch {
      return [];
    }
  }

  function writeOwnerQueue(queue) {
    try { localStorage.setItem(ownerQueueKey, JSON.stringify(queue.slice(-500))); }
    catch { /* Storage can be unavailable in private browsing. */ }
  }

  function newEventId() {
    if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
  }

  async function flushOwnerQueue() {
    if (flushingOwnerQueue) return;
    const sessionToken = localStorage.getItem(ownerSessionKey) || "";
    const queue = readOwnerQueue();
    if (!sessionToken || !queue.length) return;
    flushingOwnerQueue = true;
    try {
      const batch = queue.slice(0, 100);
      const result = await collectorRequest({ action: "events", sessionToken, events: batch });
      const accepted = new Set(Array.isArray(result.accepted) ? result.accepted : []);
      writeOwnerQueue(queue.filter(item => !accepted.has(item.eventId)));
      document.documentElement.dataset.indanyaAnalyticsStatus = "owner-local-sent";
    } catch {
      document.documentElement.dataset.indanyaAnalyticsStatus = "owner-local-queued";
    } finally {
      flushingOwnerQueue = false;
    }
  }

  function queueOwnerEvent(eventName, details) {
    const queue = readOwnerQueue();
    queue.push({
      eventId: newEventId(),
      eventName: `owner_${eventName}`,
      timestamp: new Date().toISOString(),
      pagePath: location.pathname,
      pageTitle: details.article_title || document.title || "",
      contentGroup: details.content_group || "未分類",
      promotionId: details.promotion_id || "",
      promotionName: details.promotion_name || "",
      prKind: details.pr_kind || "",
      linkDomain: details.link_domain || "",
      linkUrl: details.link_url || "",
      referrer: document.referrer || "",
      ...deviceDetails(),
    });
    writeOwnerQueue(queue);
    document.documentElement.dataset.indanyaAnalyticsStatus = "owner-local-queued";
    void flushOwnerQueue();
  }

  const isOwner = await ownerBrowser();
  const isArticle = /\/articles\/[^/]+\.html$/i.test(location.pathname);
  document.documentElement.dataset.indanyaAnalytics = isOwner ? "owner-local-v1" : "external-ga4-v1";
  document.documentElement.dataset.indanyaAnalyticsStatus = "identity-ready";
  const articleSlug = document.body.dataset.articleSlug
    || location.pathname.replace(/^.*\//, "").replace(/\.html$/, "")
    || "home";
  const articleTitle = String(document.title || "").replace(/\s*[｜|].*$/, "").trim().slice(0, 100);
  const articleCategory = String(document.body.dataset.articleCategory || (() => {
    const breadcrumb = document.querySelector(".breadcrumb");
    if (!breadcrumb) return "";
    const parts = breadcrumb.textContent.split("›").map(value => value.trim()).filter(Boolean);
    return parts.length > 1 ? parts[1] : "";
  })()).slice(0, 100);
  const seenPromotions = new WeakSet();
  const pendingPromotions = new WeakMap();

  function beginArticleVisit() {
    if (!isArticle) return false;
    const key = `indanya-ga4-visit-v4:${isOwner ? "owner" : "external"}`;
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
      tracking_version: String(config.trackingVersion || "7"),
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

  let gtag = null;
  if (!isOwner) {
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    script.addEventListener("load", () => {
      document.documentElement.dataset.indanyaAnalyticsStatus = "gtag-loaded";
    }, { once: true });
    script.addEventListener("error", () => {
      document.documentElement.dataset.indanyaAnalyticsStatus = "gtag-blocked";
    }, { once: true });
    document.head.append(script);
    window.dataLayer = window.dataLayer || [];
    gtag = function () { window.dataLayer.push(arguments); };
    window.gtag = window.gtag || gtag;
    gtag("js", new Date());
    gtag("config", measurementId, { send_page_view: false });
  }

  function sendEvent(name, details) {
    if (isOwner) queueOwnerEvent(name, details);
    else gtag("event", name, details);
  }

  if (!isOwner) sendEvent("page_view", commonDetails());
  if (isArticle) {
    sendEvent("article_view", commonDetails());
    if (beginArticleVisit()) sendEvent("article_visit", commonDetails());
  }
  if (!isOwner) document.documentElement.dataset.indanyaAnalyticsStatus = "events-queued";
  else {
    void flushOwnerQueue();
    window.setInterval(() => void flushOwnerQueue(), 15000);
  }

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          const previous = pendingPromotions.get(entry.target);
          if (previous) {
            window.clearTimeout(previous);
            pendingPromotions.delete(entry.target);
          }
          if (!entry.isIntersecting || entry.intersectionRatio < 0.5 || seenPromotions.has(entry.target)) continue;
          const timer = window.setTimeout(() => {
            if (seenPromotions.has(entry.target)) return;
            seenPromotions.add(entry.target);
            sendEvent(isArticle ? "article_pr_impression" : "pr_impression", promotionDetails(entry.target));
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
    sendEvent(isArticle ? "article_pr_click" : "pr_click", {
      ...promotionDetails(link),
      link_url: link.href,
      event_timeout: 1000,
    });
  }, { capture: true });
})().catch(error => {
  document.documentElement.dataset.indanyaAnalyticsStatus = "error";
  document.documentElement.dataset.indanyaAnalyticsError = String(error?.name || "Error");
});
