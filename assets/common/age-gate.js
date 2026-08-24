(() => {
  "use strict";

  const script = document.currentScript;
  const siteRoot = script?.dataset.siteRoot || "";
  const storageKey = "indanya-age-confirmed";
  const maxAge = 30 * 24 * 60 * 60 * 1000;
  const analyticsLoaderVersion = "7";

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
    const nav = document.querySelector(".nav-inner");
    if (nav) {
      [
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
  enhanceSiteShell();

  const localPreview =
    (location.hostname === "127.0.0.1" || location.hostname === "localhost") &&
    new URLSearchParams(location.search).get("preview") === "1";
  if (localPreview) return;

  // Keep the public URL crawlable while preserving the age check for visitors.
  const crawler =
    /Googlebot|Google-InspectionTool|bingbot|DuckDuckBot|Baiduspider|YandexBot/i.test(
      navigator.userAgent
    );
  if (crawler) return;

  try {
    const confirmedAt = Number(localStorage.getItem(storageKey) || 0);
    if (confirmedAt > 0 && Date.now() - confirmedAt < maxAge) {
      startAnalytics();
      return;
    }
  } catch {
    // Continue to the age check when storage is unavailable.
  }

  const destination = new URL(`${siteRoot}age-check.html`, location.href);
  destination.searchParams.set("return", location.href);
  location.replace(destination.href);
})();
