(() => {
  "use strict";

  const script = document.currentScript;
  const root = script?.dataset.siteRoot || "../";
  const article = document.querySelector(".article");
  if (!article) return;

  const normalize = value =>
    String(value || "")
      .toLocaleLowerCase("ja")
      .replace(/[\s\u3000【】「」『』（）()［］\[\]、。・!！?？ｗw]+/g, "");

  const bigrams = value => {
    const text = normalize(value);
    const result = new Set();
    for (let index = 0; index < text.length - 1; index += 1) {
      result.add(text.slice(index, index + 2));
    }
    return result;
  };

  const overlap = (left, right) => {
    if (!left.size || !right.size) return 0;
    let shared = 0;
    left.forEach(value => {
      if (right.has(value)) shared += 1;
    });
    return shared / Math.max(left.size, right.size);
  };

  const toRootUrl = value => {
    const cleanRoot = root.endsWith("/") ? root : `${root}/`;
    return `${cleanRoot}${String(value || "").replace(/^\/+/, "")}`;
  };

  const currentPath = location.pathname.replace(/^\/+/, "");
  const isCurrent = item => {
    const itemPath = String(item.url || "").replace(/^\/+/, "");
    return currentPath.endsWith(itemPath);
  };

  const sharedTags = (left, right) => {
    const rightTags = new Set((right.tags || []).map(normalize));
    return (left.tags || []).filter(tag => rightTags.has(normalize(tag))).length;
  };

  const relationScore = (current, candidate) => {
    let score = sharedTags(current, candidate) * 12;
    if (normalize(current.category) === normalize(candidate.category)) score += 7;
    score += overlap(bigrams(current.title), bigrams(candidate.title)) * 24;
    score += overlap(bigrams(current.summary), bigrams(candidate.summary)) * 8;
    return score;
  };

  const popularityScore = (item, index) =>
    Number(item.comments || 0) +
    (item.featured ? 30 : 0) +
    Math.max(0, 20 - index);

  const isFanzaArticle = item => {
    const tags = (item.tags || []).map(normalize);
    if (!tags.includes(normalize("FANZA"))) return false;
    try {
      const host = new URL(item.source_url || "").hostname.toLocaleLowerCase("ja");
      return host === "dmm.co.jp" ||
        host.endsWith(".dmm.co.jp") ||
        host === "fanza.co.jp" ||
        host.endsWith(".fanza.co.jp");
    } catch {
      return false;
    }
  };

  const card = item => {
    const link = document.createElement("a");
    link.className = "article-related-card";
    link.href = toRootUrl(item.url);

    const image = document.createElement("img");
    image.src = toRootUrl(item.thumbnail);
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";

    const body = document.createElement("span");
    body.className = "article-related-card-body";

    const meta = document.createElement("span");
    meta.className = "article-related-meta";
    meta.textContent = `${item.category || "記事"}  ${Number(item.comments || 0)}コメント`;

    const title = document.createElement("strong");
    title.textContent = item.title || "記事を読む";

    const summary = document.createElement("span");
    summary.className = "article-related-summary";
    summary.textContent = item.summary || "";

    body.append(meta, title, summary);
    link.append(image, body);
    return link;
  };

  const section = (title, items, className = "") => {
    if (!items.length) return null;
    const shell = document.createElement("section");
    shell.className = `article-related-section ${className}`.trim();

    const heading = document.createElement("h2");
    heading.textContent = title;

    const grid = document.createElement("div");
    grid.className = "article-related-grid";
    items.forEach(item => grid.append(card(item)));
    shell.append(heading, grid);
    return shell;
  };

  const tagSection = item => {
    const tags = (item.tags || []).filter(Boolean).slice(0, 8);
    if (!tags.length) return null;
    const shell = document.createElement("section");
    shell.className = "article-related-tags";
    const heading = document.createElement("h2");
    heading.textContent = "関連タグから探す";
    const list = document.createElement("div");
    tags.forEach(tag => {
      const link = document.createElement("a");
      link.href = `${toRootUrl("search.html")}?tag=${encodeURIComponent(tag)}`;
      link.textContent = `#${tag}`;
      list.append(link);
    });
    shell.append(heading, list);
    return shell;
  };

  fetch(toRootUrl("data/articles.json"), { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`articles: ${response.status}`);
      return response.json();
    })
    .then(value => {
      const items = Array.isArray(value) ? value : value.articles;
      if (!Array.isArray(items)) return;
      const published = items.filter(item =>
        item && item.status === "published" && item.url && item.thumbnail
      );
      const current = published.find(isCurrent);
      if (!current) return;

      const others = published.filter(item => !isCurrent(item));
      const avArticles = [...others]
        .filter(isFanzaArticle)
        .map(item => ({ item, score: relationScore(current, item) }))
        .sort((left, right) => right.score - left.score)
        .slice(0, 4)
        .map(entry => entry.item);

      const used = new Set(avArticles.map(item => item.slug || item.url));
      const related = [...others]
        .filter(item => !used.has(item.slug || item.url))
        .map(item => ({ item, score: relationScore(current, item) }))
        .filter(entry => entry.score > 0)
        .sort((left, right) => right.score - left.score)
        .slice(0, 4)
        .map(entry => entry.item);
      related.forEach(item => used.add(item.slug || item.url));
      const recommended = [...others]
        .filter(item => !used.has(item.slug || item.url))
        .map((item, index) => ({ item, score: popularityScore(item, index) }))
        .sort((left, right) => right.score - left.score)
        .slice(0, 4)
        .map(entry => entry.item);

      const discovery = document.createElement("div");
      discovery.className = "article-related";
      [
        section("この記事に近い記事", related),
        section("おすすめ記事", recommended),
        section("関連するおすすめAV記事", avArticles, "article-related-av"),
        tagSection(current),
      ].forEach(node => {
        if (node) discovery.append(node);
      });

      if (discovery.children.length) article.append(discovery);
    })
    .catch(() => {
      // Related content is supplementary; the article remains usable if it fails.
    });
})();
