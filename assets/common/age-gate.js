(() => {
  "use strict";

  const script = document.currentScript;
  const siteRoot = script?.dataset.siteRoot || "";
  const storageKey = "indanya-age-confirmed";
  const maxAge = 30 * 24 * 60 * 60 * 1000;
  const analyticsLoaderVersion = "9";

  function startAnalytics() {
    if (document.querySelector('script[data-indanya-ga4]')) return;
    const config = document.createElement("script");
    config.src = `${siteRoot}assets/common/analytics-config.js?v=${analyticsLoaderVersion}`;
    config.defer = true;
    config.dataset.indanyaGa4 = "config";
    config.addEventListener("load", () => {
      const analytics = document.createElement("script");
      const trackerVersion = encodeURIComponent(
        String(window.INDANYA_GA4?.trackingVersion || analyticsLoaderVersion)
      );
      analytics.src = `${siteRoot}assets/common/ga4.js?v=${trackerVersion}`;
      analytics.defer = true;
      analytics.dataset.indanyaGa4 = "true";
      document.head.append(analytics);
    }, { once: true });
    document.head.append(config);
  }

  function ensureBrandIcons() {
    if (document.head.querySelector('link[rel~="icon"]')) return;
    [
      ["icon", `${siteRoot}assets/common/favicon.ico`, ""],
      ["icon", `${siteRoot}assets/common/favicon.png`, "image/png"],
      ["apple-touch-icon", `${siteRoot}assets/common/apple-touch-icon.png`, ""],
    ].forEach(([rel, href, type]) => {
      const link = document.createElement("link");
      link.rel = rel;
      link.href = href;
      if (type) link.type = type;
      document.head.append(link);
    });
  }

  function enhanceSiteShell() {
    if (!document.querySelector("script[data-reader-script]")) {
      const reader = document.createElement("script");
      reader.src = `${siteRoot}assets/common/reader.js?v=20260914-reader1`;
      reader.dataset.siteRoot = siteRoot;
      reader.dataset.readerScript = "true";
      document.head.append(reader);
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = `${siteRoot}assets/common/reader.css?v=20260914-reader1`;
      document.head.append(css);
    }
    const nav = document.querySelector(".nav-inner");
    if (nav) {
      [
        ["people.html", "人物"],
        ["works.html", "作品"],
        ["topics.html", "ジャンル"],
        ["categories.html", "カテゴリ"],
        ["fanza.html", "FANZA"],
      ].forEach(([path, label]) => {
        if (nav.querySelector(`a[href="${siteRoot}${path}"]`)) return;
        const link = document.createElement("a");
        link.href = `${siteRoot}${path}`;
        link.textContent = label;
        nav.append(link);
      });
    }

    const footerLinks = document.querySelector(".footer-inner span:last-child");
    if (footerLinks) {
      [
        ["editorial.html", "編集方針"],
        ["removal.html", "削除依頼"],
        ["faq.html", "FAQ"],
        ["advertise.html", "広告掲載"],
      ].forEach(([path, label]) => {
        if (footerLinks.querySelector(`a[href="${siteRoot}${path}"]`)) return;
        footerLinks.append(document.createTextNode("　"));
        const link = document.createElement("a");
        link.href = `${siteRoot}${path}`;
        link.textContent = label;
        footerLinks.append(link);
      });
    }
  }

  ensureBrandIcons();

  const localPreview =
    (location.hostname === "127.0.0.1" || location.hostname === "localhost") &&
    new URLSearchParams(location.search).get("preview") === "1";
  if (localPreview) { enhanceSiteShell(); return; }

  // Keep the public URL crawlable while preserving the age check for visitors.
  const crawler =
    /Googlebot|Google-InspectionTool|bingbot|DuckDuckBot|Baiduspider|YandexBot/i.test(
      navigator.userAgent
    );
  if (crawler) { enhanceSiteShell(); return; }

  let confirmed = false;
  for (const name of ["localStorage", "sessionStorage"]) {
    try {
      const confirmedAt = Number(window[name].getItem(storageKey) || 0);
      if (confirmedAt > 0 && Date.now() - confirmedAt >= 0 && Date.now() - confirmedAt < maxAge) confirmed = true;
    } catch { /* Fall back to the other store. */ }
  }
  const handoff = new URL(location.href);
  const passedAt = Number(handoff.searchParams.get("age_passed") || 0);
  if (passedAt > 0 && Date.now() - passedAt >= 0 && Date.now() - passedAt < 60000) {
    confirmed = true;
    handoff.searchParams.delete("age_passed");
    history.replaceState(null, "", handoff.href);
  }
  if (confirmed) { enhanceSiteShell(); startAnalytics(); return; }

  const destination = new URL(`${siteRoot}age-check.html`, location.href);
  destination.searchParams.set("return", location.href);
  location.replace(destination.href);
})();
