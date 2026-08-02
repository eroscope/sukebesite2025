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

  function send(eventType, extra = {}) {
    const payload = {
      action: "analytics_event",
      token: collectorToken,
      event_type: eventType,
      site: `${location.hostname}${location.pathname.split("/").slice(0, 2).join("/")}`,
      session_id: sessionId(),
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
