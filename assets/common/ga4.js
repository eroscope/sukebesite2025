(() => {
  "use strict";

  const measurementId = String(window.INDANYA_GA4?.measurementId || "").trim();
  if (!/^G-[A-Z0-9]+$/i.test(measurementId)) return;

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
  document.head.append(script);

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;
  gtag("js", new Date());
  gtag("config", measurementId, { send_page_view: true });

  const articleSlug = document.body.dataset.articleSlug || location.pathname.replace(/^.*\//, "").replace(/\.html$/, "") || "home";
  const articleTitle = String(document.title || "").replace(/\s*[｜|].*$/, "").trim().slice(0, 100);
  const seenPromotions = new WeakSet();

  function details(element) {
    const promotion = element.closest(".fanza-product");
    const link = element.matches("a") ? element : promotion?.querySelector("a[href]");
    let linkDomain = "";
    try { linkDomain = link ? new URL(link.href).hostname : ""; } catch { /* ignored */ }
    return {
      article_slug: articleSlug,
      article_title: articleTitle,
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

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting || seenPromotions.has(entry.target)) continue;
          seenPromotions.add(entry.target);
          gtag("event", "pr_impression", details(entry.target));
          observer.unobserve(entry.target);
        }
      }, { threshold: 0.35 })
    : null;

  if (observer) document.querySelectorAll(".fanza-product").forEach(item => observer.observe(item));
  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!link || !isPromotionLink(link)) return;
    gtag("event", "pr_click", { ...details(link), link_url: link.href });
  }, { capture: true });
})();
