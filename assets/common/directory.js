(() => {
  "use strict";
  const directory = document.querySelector("[data-entity-directory]");
  const form = document.getElementById("directorySearch");
  const input = form?.querySelector("input");
  const status = document.getElementById("directoryCount");
  if (!directory || !form || !input || !status) return;
  const normalize = value => String(value || "").normalize("NFKC").toLocaleLowerCase("ja").trim();
  const links = [...directory.querySelectorAll("a[data-entity-label]")];
  const entries = links.map(link => ({ link, key: normalize(link.dataset.entityLabel).replace(/\s+/g, "") }));
  const previews = [...document.querySelectorAll(".entity-section")];
  const empty = document.getElementById("directoryEmpty");
  function render() {
    const tokens = normalize(input.value).split(/\s+/).filter(Boolean);
    let visible = 0;
    entries.forEach(({ link, key }) => {
      link.hidden = !tokens.every(token => key.includes(token));
      if (!link.hidden) visible += 1;
    });
    previews.forEach(section => { section.hidden = tokens.length > 0; });
    status.textContent = `${visible} / ${entries.length}件`;
    if (empty) empty.hidden = visible !== 0;
    const url = new URL(location.href);
    if (input.value.trim()) url.searchParams.set("q", input.value.trim());
    else url.searchParams.delete("q");
    history.replaceState(null, "", url.href);
  }
  input.value = new URLSearchParams(location.search).get("q") || "";
  form.hidden = false;
  input.addEventListener("input", render);
  form.addEventListener("submit", event => { event.preventDefault(); render(); });
  render();
})();
