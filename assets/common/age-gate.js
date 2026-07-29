(() => {
  "use strict";

  const script = document.currentScript;
  const siteRoot = script?.dataset.siteRoot || "";
  const storageKey = "indanya-age-confirmed";
  const maxAge = 30 * 24 * 60 * 60 * 1000;

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

  enhanceSiteShell();

  const localPreview =
    (location.hostname === "127.0.0.1" || location.hostname === "localhost") &&
    new URLSearchParams(location.search).get("preview") === "1";
  if (localPreview) return;

  try {
    const confirmedAt = Number(localStorage.getItem(storageKey) || 0);
    if (confirmedAt > 0 && Date.now() - confirmedAt < maxAge) return;
  } catch {
    // Continue to the age check when storage is unavailable.
  }

  const destination = new URL(`${siteRoot}age-check.html`, location.href);
  destination.searchParams.set("return", location.href);
  location.replace(destination.href);
})();
