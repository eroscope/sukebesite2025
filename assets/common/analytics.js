(() => {
  "use strict";

  const endpoint = "https://script.google.com/macros/s/AKfycbx9Wzb1WqWr_iQPZsKkMjtxM28bMGwr5EzD2Di1Hm8kI3XIT3cXo2lXyu6Tw417srpk/exec";
  const collectorToken = "indanya-analytics-20260803-v1";
  const articleSlug = document.body.dataset.articleSlug || pageSlug();
  const category = document.body.dataset.articleCategory || pageCategory();
  const articleTitle = cleanTitle(document.title);
  const viewedPromotions = new WeakSet();

  function pageSlug() {
    const match = location.pathname.match(/\/articles\/([a-z0-9-]+)\.html$/i);
    if (match) return match[1];
    const name = location.pathname.split("/").filter(Boolean).pop() || "home";
    return name.replace(/\.html$/i, "") || "home";
  }

  function pageCategory() {
    const breadcrumb = document.querySelector(".breadcrumb");
    if (!breadcrumb) return articleSlug === "home" ? "トップ" : "";
    const values = breadcrumb.textContent.split("›").map(value => value.trim()).filter(Boolean);
    return values.length > 1 ? values[1] : "";
  }

  function cleanTitle(value) {
    return String(value || "").replace(/\s*[｜|].*$/, "").trim().slice(0, 180);
  }

  function sessionId() {
    const key = "indanya-analytics-session";
    try {
      let value = sessionStorage.getItem(key);
      if (!value) {
        value = `${Date.now().toString(36)}-${crypto.getRandomValues(new Uint32Array(2)).join("")}`;
        sessionStorage.setItem(key, value);
      }
      return value;
    } catch {
      return `${Date.now().toString(36)}-limited`;
    }
  }

  function visitorId() {
    const key = "indanya-analytics-visitor";
    try {
      let value = localStorage.getItem(key);
      if (!value) {
        value = `${Date.now().toString(36)}-${crypto.getRandomValues(new Uint32Array(3)).join("")}`;
        localStorage.setItem(key, value);
      }
      return value;
    } catch {
      return sessionId();
    }
  }

  function shortHash(value) {
    let h1 = 0xdeadbeef;
    let h2 = 0x41c6ce57;
    const text = String(value || "");
    for (let index = 0; index < text.length; index += 1) {
      const code = text.charCodeAt(index);
      h1 = Math.imul(h1 ^ code, 2654435761);
      h2 = Math.imul(h2 ^ code, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return `${(h2 >>> 0).toString(36)}${(h1 >>> 0).toString(36)}`;
  }

  function deviceType() {
    const value = navigator.userAgent || "";
    if (/iPad|Tablet|Android(?!.*Mobile)/i.test(value)) return "タブレット";
    if (/Mobile|Android|iPhone|iPod/i.test(value)) return "スマホ";
    return "パソコン";
  }

  function deviceId() {
    const values = [
      navigator.platform || "",
      navigator.language || "",
      Array.isArray(navigator.languages) ? navigator.languages.join(",") : "",
      Intl.DateTimeFormat().resolvedOptions().timeZone || "",
      String(new Date().getTimezoneOffset()),
      `${screen.width}x${screen.height}`,
      `${screen.availWidth}x${screen.availHeight}`,
      String(screen.colorDepth || ""),
      String(devicePixelRatio || ""),
      String(navigator.hardwareConcurrency || ""),
      String(navigator.deviceMemory || ""),
      String(navigator.maxTouchPoints || 0),
      deviceType(),
    ];
    return shortHash(values.join("|"));
  }

  function browserFamily() {
    const value = navigator.userAgent || "";
    if (/Edg\//.test(value)) return "Edge";
    if (/OPR\//.test(value)) return "Opera";
    if (/Firefox\//.test(value)) return "Firefox";
    if (/Chrome\//.test(value)) return "Chrome";
    if (/Safari\//.test(value)) return "Safari";
    return "unknown";
  }

  function send(eventType, extra = {}) {
    const payload = {
      action: "analytics_event",
      token: collectorToken,
      event_type: eventType,
      site: `${location.hostname}${location.pathname.split("/").slice(0, 2).join("/")}`,
      visitor_id: visitorId(),
      device_id: deviceId(),
      session_id: sessionId(),
      device_type: deviceType(),
      browser_family: browserFamily(),
      page_path: location.pathname,
      article_slug: articleSlug,
      article_title: articleTitle,
      category,
      referrer_host: (() => {
        try { return document.referrer ? new URL(document.referrer).hostname : ""; }
        catch { return ""; }
      })(),
      ...extra,
    };
    try {
      fetch(endpoint, {
        method: "POST",
        mode: "no-cors",
        keepalive: true,
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: JSON.stringify(payload),
      }).catch(() => {});
    } catch {
      // Analytics must never interfere with reading an article or opening a PR link.
    }
  }

  function promotionDetails(element) {
    const promotion = element.closest(".fanza-product");
    const link = element.matches("a") ? element : promotion?.querySelector("a[href]");
    let destinationHost = "";
    try { destinationHost = link ? new URL(link.href).hostname : ""; }
    catch { destinationHost = ""; }
    return {
      pr_id: promotion?.dataset.prId || "legacy-pr",
      pr_kind: promotion?.dataset.prKind || "unknown",
      pr_title: cleanTitle(
        promotion?.querySelector(".fanza-product-title")?.textContent || link?.textContent || ""
      ),
      destination_host: destinationHost,
    };
  }

  function isPromotionLink(link) {
    if (link.classList.contains("fanza-product-button")) return true;
    if ((link.rel || "").split(/\s+/).includes("sponsored")) return true;
    try {
      return /(^|\.)(?:dmm|fanza)\.co\.jp$|^(?:al\.)?(?:dmm|fanza)\.co\.jp$/i.test(
        new URL(link.href).hostname
      );
    } catch {
      return false;
    }
  }

  send("page_view");

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting || viewedPromotions.has(entry.target)) return;
          viewedPromotions.add(entry.target);
          send("pr_impression", promotionDetails(entry.target));
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.35 })
    : null;

  document.querySelectorAll(".fanza-product").forEach(promotion => {
    if (observer) observer.observe(promotion);
  });

  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!link || !isPromotionLink(link)) return;
    send("pr_click", promotionDetails(link));
  }, { capture: true });
})();
