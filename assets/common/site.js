(() => {
  "use strict";

  const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const localPathPattern = /^[A-Za-z0-9._/-]+$/;

  function isSafeLocalPath(value, prefix) {
    return typeof value === "string" &&
      value.startsWith(prefix) &&
      localPathPattern.test(value) &&
      !value.includes("..") &&
      !value.includes("//");
  }

  function isValidArticle(article) {
    if (!article || typeof article !== "object" || Array.isArray(article)) return false;
    if (typeof article.id !== "string" || article.id.length === 0) return false;
    if (typeof article.slug !== "string" || !slugPattern.test(article.slug)) return false;
    if (typeof article.title !== "string" || article.title.length === 0 || article.title.length > 180) return false;
    if (typeof article.category !== "string" || article.category.length === 0 || article.category.length > 40) return false;
    if (!["draft", "published", "archived"].includes(article.status)) return false;
    if (typeof article.published_at !== "string" || Number.isNaN(Date.parse(article.published_at))) return false;
    if (typeof article.display_date !== "string" || !/^\d{4}\.\d{2}\.\d{2}$/.test(article.display_date)) return false;
    if (!Number.isInteger(article.comments) || article.comments < 0) return false;
    if (article.url !== `articles/${article.slug}.html`) return false;
    if (!isSafeLocalPath(article.thumbnail, "assets/")) return false;
    if (!Number.isInteger(article.images_used) || article.images_used < 1) return false;
    if (article.summary !== undefined && (typeof article.summary !== "string" || article.summary.length > 240)) return false;
    if (article.search_text !== undefined && (typeof article.search_text !== "string" || article.search_text.length > 12000)) return false;
    if (article.tags !== undefined && (!Array.isArray(article.tags) || article.tags.some(tag => typeof tag !== "string"))) return false;

    try {
      const source = new URL(article.source_url);
      if (source.protocol !== "http:" && source.protocol !== "https:") return false;
    } catch {
      return false;
    }

    return true;
  }

  function setLink(element, article) {
    element.href = article.url;
    element.setAttribute("aria-label", article.title);
  }

  function createCard(article, index) {
    const card = document.createElement("article");
    card.className = "post-card";

    const thumbLink = document.createElement("a");
    thumbLink.className = "thumb";
    setLink(thumbLink, article);

    const image = document.createElement("img");
    image.src = article.thumbnail;
    image.alt = article.title;
    image.loading = index > 1 ? "lazy" : "eager";
    thumbLink.append(image);

    if (index === 0) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "NEW";
      thumbLink.append(badge);
    }

    const body = document.createElement("div");
    body.className = "card-body";

    const meta = document.createElement("div");
    meta.className = "card-meta";
    [article.category, article.display_date, `${article.comments}コメント`].forEach(value => {
      const item = document.createElement("span");
      item.textContent = value;
      meta.append(item);
    });

    const heading = document.createElement("h2");
    const titleLink = document.createElement("a");
    titleLink.href = article.url;
    titleLink.textContent = article.title;
    heading.append(titleLink);
    body.append(meta, heading);
    card.append(thumbLink, body);
    return card;
  }

  function createRank(article, index) {
    const row = document.createElement("a");
    row.className = "rank rank-with-thumb";
    row.href = article.url;

    const number = document.createElement("span");
    number.className = "rank-num";
    number.textContent = String(index + 1);

    const image = document.createElement("img");
    image.src = article.thumbnail;
    image.alt = "";
    image.loading = "lazy";

    const details = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = article.title;
    const comments = document.createElement("span");
    comments.textContent = `${article.comments}コメント`;
    details.append(title, comments);
    row.append(number, image, details);
    return row;
  }

  function render(articles) {
    const featured = articles.find(article => article.featured === true) || articles[0];
    const breakingLink = document.getElementById("breakingLink");
    const featureThumbLink = document.getElementById("featureThumbLink");
    const featureImage = document.getElementById("featureImage");
    const featureBadge = document.getElementById("featureBadge");
    const featureTitleLink = document.getElementById("featureTitleLink");
    const featureSummary = document.getElementById("featureSummary");
    const featureReadMore = document.getElementById("featureReadMore");
    const articleGrid = document.getElementById("articleGrid");
    const popularArticles = document.getElementById("popularArticles");
    const listTitle = document.getElementById("listTitle");
    const listMore = document.getElementById("listMore");

    [breakingLink, featureThumbLink, featureTitleLink, featureReadMore].forEach(link => setLink(link, featured));
    breakingLink.textContent = featured.title;
    featureImage.src = featured.thumbnail;
    featureImage.alt = featured.title;
    featureBadge.textContent = `画像${featured.images_used}枚`;
    featureTitleLink.textContent = featured.title;
    featureSummary.textContent = featured.summary || `${featured.images_used}枚の画像をレスの流れでまとめています。`;

    function selectArticles(mode) {
      if (mode === "popular") {
        return [...articles].sort((left, right) => right.comments - left.comments || Date.parse(right.published_at) - Date.parse(left.published_at));
      }
      if (mode === "random") {
        return [...articles].sort(() => Math.random() - 0.5);
      }
      return [...articles];
    }

    function showMode(mode) {
      const selected = selectArticles(mode);
      articleGrid.replaceChildren(...selected.slice(0, 8).map(createCard));
      const labels = { latest: "新着記事", popular: "人気記事", random: "ランダム記事" };
      const links = { latest: "latest.html", popular: "popular.html", random: "random.html" };
      listTitle.textContent = labels[mode];
      listMore.href = links[mode];
      document.querySelectorAll("[data-list-mode]").forEach(button => {
        button.classList.toggle("active", button.dataset.listMode === mode);
      });
    }

    document.querySelectorAll("[data-list-mode]").forEach(button => {
      button.addEventListener("click", () => showMode(button.dataset.listMode));
    });
    showMode("latest");

    const ranking = [...articles]
      .sort((left, right) => right.comments - left.comments || Date.parse(right.published_at) - Date.parse(left.published_at))
      .slice(0, 5);
    popularArticles.replaceChildren(...ranking.map(createRank));
    document.documentElement.dataset.articlesLoaded = "true";
  }

  fetch("data/articles.json", { cache: "no-cache" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(data => {
      if (!Array.isArray(data)) throw new Error("記事一覧が配列ではありません");
      const published = data
        .filter(isValidArticle)
        .filter(article => article.status === "published")
        .sort((left, right) => Date.parse(right.published_at) - Date.parse(left.published_at));
      if (published.length === 0) throw new Error("公開記事がありません");
      render(published);
    })
    .catch(error => {
      document.documentElement.dataset.articlesLoaded = "fallback";
      console.warn("記事一覧を読み込めなかったため、静的表示を使用します。", error);
    });
})();
