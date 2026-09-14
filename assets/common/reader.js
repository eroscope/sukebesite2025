(() => {
  "use strict";
  const root = new URL(document.currentScript?.dataset.siteRoot || "./", location.href);
  const key = `indanya-reader-v1:${root.pathname}`;
  const limit = 100;
  document.querySelectorAll('.nav-inner a[href$="popular.html"]').forEach(link => {link.textContent = "記事一覧";});
  document.querySelectorAll('.article-meta span, .sidebar .rank span:not(.rank-num)').forEach(span => {
    if (/^\s*\d+\s*コメント\s*$/.test(span.textContent)) span.remove();
  });
  document.querySelectorAll('.sidebar .side-title').forEach(heading => {
    if (/人気|急上昇/.test(heading.textContent)) heading.textContent = "掲載記事";
    if (heading.textContent.trim() !== "最新コメント") return;
    heading.textContent = "また読む";
    const body = heading.parentElement.querySelector('.sidebody');
    if (!body) return;
    body.replaceChildren();
    for (const [path, text] of [["saved.html", "保存した記事・閲覧履歴"], ["feed.xml", "RSS"]]) {
      const paragraph = document.createElement("p");
      const link = document.createElement("a"); link.href = new URL(path, root).href; link.textContent = text;
      paragraph.append(link); body.append(paragraph);
    }
  });
  let state = {saved: [], recent: []};
  const safe = item => {
    if (!item || typeof item.title !== "string") return false;
    try {
      const url = new URL(item.url, root);
      return url.origin === root.origin && url.pathname.startsWith(`${root.pathname}articles/`)
        && /^[a-z0-9]+(?:-[a-z0-9]+)*\.html$/.test(url.pathname.split("/").pop());
    } catch { return false; }
  };
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    ["saved", "recent"].forEach(name => {state[name] = (Array.isArray(value[name]) ? value[name] : []).filter(safe).slice(0, limit);});
  } catch { /* Private browsing may disable storage. */ }
  const persist = () => {
    try { localStorage.setItem(key, JSON.stringify(state)); return true; }
    catch { return false; }
  };
  const emit = name => document.dispatchEvent(new CustomEvent("indanya-reader-event", {detail: {name}}));
  const article = document.querySelector(".article");
  const title = article?.querySelector("h1")?.textContent?.trim() || document.title.split(/[｜|]/)[0].trim();
  const current = {url: new URL(location.pathname, location.origin).href, title, date: new Date().toISOString()};
  if (article && safe(current)) {
    state.recent = [current, ...state.recent.filter(item => item.url !== current.url)].slice(0, limit);
    persist();
    const bar = document.createElement("nav");
    bar.className = "reader-actions";
    bar.setAttribute("aria-label", "記事を保存");
    const button = document.createElement("button"); button.type = "button";
    const status = document.createElement("span"); status.className = "reader-status"; status.setAttribute("role", "status");
    const update = () => {
      const saved = state.saved.some(item => item.url === current.url);
      button.setAttribute("aria-pressed", String(saved)); button.textContent = saved ? "保存済み" : "この記事を保存";
    };
    button.addEventListener("click", () => {
      const old = state.saved;
      const saved = old.some(item => item.url === current.url);
      state.saved = saved ? old.filter(item => item.url !== current.url) : [current, ...old].slice(0, limit);
      if (!persist()) {state.saved = old; status.textContent = "このブラウザでは保存できません。"; return;}
      status.textContent = ""; update(); emit(saved ? "article_unsave" : "article_save");
    });
    update();
    const saved = document.createElement("a"); saved.href = new URL("saved.html", root).href; saved.textContent = "保存した記事";
    bar.append(button, saved, status);
    const heading = article.querySelector("h1"); (heading || article.firstElementChild)?.after(bar);
  }
  const nav = document.querySelector(".nav-inner");
  if (nav && !nav.querySelector('[data-reader-library]')) {
    const link = document.createElement("a"); link.href = new URL("saved.html", root).href;
    link.dataset.readerLibrary = "true"; link.textContent = "保存・履歴"; nav.append(link);
  }
  function render(name) {
    const list = document.querySelector(`[data-reader-list="${name}"]`);
    if (!list) return;
    list.replaceChildren();
    if (!state[name].length) {const li = document.createElement("li"); li.textContent = name === "saved" ? "保存した記事はありません。" : "閲覧履歴はありません。"; list.append(li);}
    state[name].forEach(item => {
      const li = document.createElement("li");
      const a = document.createElement("a"); a.href = item.url; a.textContent = item.title; a.dataset.readerReturn = name;
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "削除";
      remove.setAttribute("aria-label", `${item.title}を${name === "saved" ? "保存一覧" : "履歴"}から削除`);
      remove.addEventListener("click", () => {
        const old = state[name]; state[name] = old.filter(row => row.url !== item.url);
        if (!persist()) {state[name] = old; return;}
        render(name);
      });
      li.append(a, remove); list.append(li);
    });
  }
  render("saved"); render("recent");
  document.querySelector("[data-reader-clear]")?.addEventListener("click", () => {
    const old = state; state = {saved: [], recent: []};
    if (!persist()) {state = old; return;}
    render("saved"); render("recent");
  });
})();
