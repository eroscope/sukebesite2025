(() => {
  "use strict";

  const script = document.currentScript;
  const siteRoot = script?.dataset.siteRoot || "";
  const storageKey = "indanya-age-confirmed";
  const maxAge = 30 * 24 * 60 * 60 * 1000;

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
