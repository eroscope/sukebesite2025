(() => {
  "use strict";

  const page = document.body.dataset.page || "latest";
  const params = new URLSearchParams(location.search);
  const normalize = value => String(value || "").normalize("NFKC").toLocaleLowerCase("ja").replace(/\s+/g, " ").trim();
  const rootPath = document.body.dataset.root || "";

  function articleUrl(article) {
    return `${rootPath}${article.url}`;
  }

  function imageUrl(article) {
    return `${rootPath}${article.thumbnail}`;
  }

  function createTag(tag) {
    const link = document.createElement("a");
    link.className = "tag";
    link.href = `${rootPath}search.html?tag=${encodeURIComponent(tag)}`;
    link.textContent = `#${tag}`;
    return link;
  }

  function createCard(article, index) {
    const card = document.createElement("article");
    card.className = "post-card";
    const imageLink = document.createElement("a");
    imageLink.className = "thumb";
    imageLink.href = articleUrl(article);
    const image = document.createElement("img");
    image.src = imageUrl(article);
    image.alt = article.title;
    image.loading = index < 2 ? "eager" : "lazy";
    imageLink.append(image);
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
    const link = document.createElement("a");
    link.href = articleUrl(article);
    link.textContent = article.title;
    heading.append(link);
    const summary = document.createElement("p");
    summary.textContent = article.summary || "";
    const tags = document.createElement("div");
    tags.className = "tag-row";
    (article.tags || []).slice(0, 5).forEach(tag => tags.append(createTag(tag)));
    body.append(meta, heading, summary, tags);
    card.append(imageLink, body);
    return card;
  }

  function createRank(article, index) {
    const link = document.createElement("a");
    link.className = "rank-with-thumb";
    link.href = articleUrl(article);
    const number = document.createElement("i");
    number.className = "rank-num";
    number.textContent = String(index + 1);
    const image = document.createElement("img");
    image.src = imageUrl(article);
    image.alt = "";
    image.loading = "lazy";
    const copy = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = article.title;
    const count = document.createElement("span");
    count.textContent = `${article.comments}コメント`;
    copy.append(title, count);
    link.append(number, image, copy);
    return link;
  }

  function scoreArticle(article, queryTokens, selectedTag, selectedCategory) {
    if (selectedTag && !(article.tags || []).some(tag => normalize(tag) === selectedTag)) return -1;
    if (selectedCategory && normalize(article.category) !== selectedCategory) return -1;
    if (!queryTokens.length) return 1;
    const title = normalize(article.title);
    const tags = normalize((article.tags || []).join(" "));
    const category = normalize(article.category);
    const summary = normalize(article.summary);
    const body = normalize(article.search_text);
    let score = 0;
    for (const token of queryTokens) {
      let tokenScore = 0;
      if (title.includes(token)) tokenScore += 12;
      if (tags.includes(token)) tokenScore += 9;
      if (category.includes(token)) tokenScore += 6;
      if (summary.includes(token)) tokenScore += 4;
      if (body.includes(token)) tokenScore += 2;
      if (!tokenScore) return -1;
      score += tokenScore;
    }
    return score;
  }

  function render(articles) {
    const published = articles.filter(article => article.status === "published");
    const latest = [...published].sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
    const popular = [...published].sort((a, b) => b.comments - a.comments || Date.parse(b.published_at) - Date.parse(a.published_at));
    const heading = document.getElementById("pageTitle");
    const description = document.getElementById("pageDescription");
    const grid = document.getElementById("catalogGrid");
    const ranks = document.getElementById("popularArticles");
    const cloud = document.getElementById("tagCloud");

    if (ranks) ranks.replaceChildren(...popular.slice(0, 5).map(createRank));
    const tagCounts = new Map();
    published.forEach(article => (article.tags || []).forEach(tag => tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1)));
    if (cloud) {
      [...tagCounts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ja")).forEach(([tag, count]) => {
        const link = document.createElement("a");
        link.href = `${rootPath}search.html?tag=${encodeURIComponent(tag)}`;
        link.textContent = `${tag} (${count})`;
        cloud.append(link);
      });
    }

    let selected = latest;
    if (page === "popular") selected = popular;
    if (page === "random") selected = [...published].sort(() => Math.random() - 0.5);
    if (page === "search") {
      const query = params.get("q") || "";
      const tag = normalize(params.get("tag"));
      const category = normalize(params.get("category"));
      const tokens = normalize(query).split(" ").filter(Boolean);
      selected = published
        .map(article => ({ article, score: scoreArticle(article, tokens, tag, category) }))
        .filter(item => item.score >= 0)
        .sort((a, b) => b.score - a.score || Date.parse(b.article.published_at) - Date.parse(a.article.published_at))
        .map(item => item.article);
      const label = query ? `「${query}」の検索結果` : tag ? `タグ「${params.get("tag")}」の記事` : category ? `${params.get("category")}の記事` : "記事検索";
      heading.textContent = label;
      description.textContent = `${selected.length}件の記事が見つかりました`;
      const searchInput = document.querySelector('.site-search input[name="q"]');
      if (searchInput) searchInput.value = query;
    }

    if (!selected.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML = "<h2>該当する記事はありません</h2><p>言葉を短くするか、タグ一覧から探してみてください。</p>";
      grid.replaceChildren(empty);
    } else {
      grid.replaceChildren(...selected.map(createCard));
    }
    document.documentElement.dataset.catalogLoaded = "true";
  }

  fetch(`${rootPath}data/articles.json`, { cache: "no-cache" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(render)
    .catch(error => {
      const grid = document.getElementById("catalogGrid");
      if (grid) grid.innerHTML = '<div class="empty-state"><h2>記事一覧を読み込めませんでした</h2><p>ページを再読み込みしてください。</p></div>';
      console.warn(error);
    });
})();
