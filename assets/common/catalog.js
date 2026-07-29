(() => {
  "use strict";

  const page = document.body.dataset.page || "latest";
  const params = new URLSearchParams(location.search);
  const rootPath = document.body.dataset.root || "";
  const normalize = value =>
    String(value || "").normalize("NFKC").toLocaleLowerCase("ja").replace(/\s+/g, " ").trim();

  const articleUrl = article => `${rootPath}${article.url}`;
  const imageUrl = article => `${rootPath}${article.thumbnail}`;

  function createTag(tag) {
    const link = document.createElement("a");
    link.className = "tag";
    link.href = `${rootPath}search.html?tag=${encodeURIComponent(tag)}`;
    link.textContent = `#${tag}`;
    return link;
  }

  function createCard(article, index = 99) {
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

  function createRank(article) {
    const link = document.createElement("a");
    link.className = "rank-with-thumb";
    link.href = articleUrl(article);
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
    link.append(image, copy);
    return link;
  }

  function isFanzaArticle(article) {
    const tags = (article.tags || []).map(normalize);
    if (tags.includes("fanza") || tags.includes("pr")) return true;
    try {
      return /(?:^|\.)dmm\.co\.jp$|(?:^|\.)fanza\.co\.jp$/i.test(
        new URL(article.source_url || "").hostname
      );
    } catch {
      return false;
    }
  }

  function scoreArticle(article, queryTokens, selectedTag, selectedCategory) {
    if (selectedTag && !(article.tags || []).some(tag => normalize(tag) === selectedTag)) return -1;
    if (selectedCategory && normalize(article.category) !== selectedCategory) return -1;
    if (!queryTokens.length) return 1;
    const fields = {
      title: normalize(article.title),
      tags: normalize((article.tags || []).join(" ")),
      category: normalize(article.category),
      summary: normalize(article.summary),
      body: normalize(article.search_text),
    };
    let score = 0;
    for (const token of queryTokens) {
      let tokenScore = 0;
      if (fields.title.includes(token)) tokenScore += 12;
      if (fields.tags.includes(token)) tokenScore += 9;
      if (fields.category.includes(token)) tokenScore += 6;
      if (fields.summary.includes(token)) tokenScore += 4;
      if (fields.body.includes(token)) tokenScore += 2;
      if (!tokenScore) return -1;
      score += tokenScore;
    }
    return score;
  }

  function createFeatureSection(title, description, articles, href) {
    const section = document.createElement("section");
    section.className = "catalog-feature-section";
    const head = document.createElement("header");
    const copy = document.createElement("div");
    const heading = document.createElement("h2");
    heading.textContent = title;
    const paragraph = document.createElement("p");
    paragraph.textContent = description;
    copy.append(heading, paragraph);
    head.append(copy);
    if (href) {
      const more = document.createElement("a");
      more.href = href;
      more.textContent = "すべて見る";
      head.append(more);
    }
    const grid = document.createElement("div");
    grid.className = "catalog-feature-grid";
    grid.append(...articles.slice(0, 6).map(createCard));
    section.append(head, grid);
    return section;
  }

  function renderCategoryHub(published, latest) {
    const grid = document.getElementById("catalogGrid");
    document.getElementById("pageTitle").textContent = "カテゴリから探す";
    document.getElementById("pageDescription").textContent =
      "画像・動画・SNS・人物・ジャンルごとに記事をまとめています";
    const groups = [
      ["動画", "動画を中心に見たい人向け", item => normalize(item.category).includes("動画"), "search.html?category=動画"],
      ["画像・グラビア", "画像をまとめて見られる記事", item => normalize(item.category).includes("画像"), "search.html?category=画像"],
      ["SNS・X", "SNSで話題の人物や投稿", item => normalize(item.category).includes("sns") || (item.tags || []).some(tag => /X|SNS|Twitter/i.test(tag)), "search.html?category=SNS"],
      ["コスプレ・衣装", "衣装やシチュエーションを軸にした記事", item => (item.tags || []).some(tag => /コスプレ|制服|水着|下着/.test(tag)), "search.html?q=コスプレ"],
      ["FANZA", "作品ページや関連作品へ進める記事", isFanzaArticle, "fanza.html"],
      ["二次元・AI", "イラスト、同人、AI画像の記事", item => /二次|同人|AI|アニメ/.test(`${item.title} ${(item.tags || []).join(" ")}`), "search.html?q=AI"],
    ];
    grid.className = "catalog-hub";
    grid.replaceChildren(...groups.map(([title, copy, predicate, href]) =>
      createFeatureSection(title, copy, latest.filter(predicate), href)
    ));
  }

  function renderPagination(total, currentPage, pageSize) {
    const holder = document.getElementById("catalogPagination");
    if (!holder) return;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (totalPages <= 1) {
      holder.replaceChildren();
      return;
    }
    const makeLink = (label, target, active = false) => {
      const link = document.createElement("a");
      const query = new URLSearchParams(params);
      query.set("page", String(target));
      link.href = `${location.pathname}?${query.toString()}`;
      link.textContent = label;
      if (active) {
        link.className = "active";
        link.setAttribute("aria-current", "page");
      }
      return link;
    };
    const nodes = [];
    if (currentPage > 1) nodes.push(makeLink("前へ", currentPage - 1));
    const start = Math.max(1, Math.min(currentPage - 2, Math.max(1, totalPages - 4)));
    const end = Math.min(totalPages, start + 4);
    for (let number = start; number <= end; number += 1) {
      nodes.push(makeLink(String(number), number, number === currentPage));
    }
    if (currentPage < totalPages) nodes.push(makeLink("次へ", currentPage + 1));
    holder.replaceChildren(...nodes);
  }

  function render(articles) {
    const published = articles.filter(article => article.status === "published");
    const latest = [...published].sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
    const popular = [...published].sort((a, b) =>
      b.comments - a.comments || Date.parse(b.published_at) - Date.parse(a.published_at)
    );
    const heading = document.getElementById("pageTitle");
    const description = document.getElementById("pageDescription");
    const grid = document.getElementById("catalogGrid");
    const ranks = document.getElementById("popularArticles");
    const cloud = document.getElementById("tagCloud");

    if (ranks) ranks.replaceChildren(...popular.slice(0, 7).map(createRank));
    const tagCounts = new Map();
    published.forEach(article =>
      (article.tags || []).forEach(tag => tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1))
    );
    if (cloud) {
      cloud.replaceChildren();
      [...tagCounts]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ja"))
        .slice(0, 30)
        .forEach(([tag, count]) => {
          const link = document.createElement("a");
          link.href = `${rootPath}search.html?tag=${encodeURIComponent(tag)}`;
          link.textContent = `${tag} (${count})`;
          cloud.append(link);
        });
    }

    if (page === "categories") {
      renderCategoryHub(published, latest);
      document.documentElement.dataset.catalogLoaded = "true";
      return;
    }

    let selected = latest;
    if (page === "popular") selected = popular;
    if (page === "random") selected = [...published].sort(() => Math.random() - 0.5);
    if (page === "fanza") {
      selected = latest.filter(isFanzaArticle);
      heading.textContent = "FANZA作品・関連作品";
      description.textContent =
        "作品紹介、サンプル、記事内容に近いFANZA作品をまとめています";
    }
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
      heading.textContent = query
        ? `「${query}」の検索結果`
        : tag
          ? `タグ「${params.get("tag")}」の記事`
          : category
            ? `${params.get("category")}の記事`
            : "記事検索";
      description.textContent = `${selected.length}件の記事が見つかりました`;
      const searchInput = document.querySelector('.site-search input[name="q"]');
      if (searchInput) searchInput.value = query;
    }

    const pageSize = 24;
    const totalPages = Math.max(1, Math.ceil(selected.length / pageSize));
    const currentPage = Math.min(
      Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1),
      totalPages
    );
    const pageItems = selected.slice((currentPage - 1) * pageSize, currentPage * pageSize);
    if (pageItems.length) {
      grid.replaceChildren(...pageItems.map(createCard));
    } else {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML =
        "<h2>該当する記事はありません</h2><p>言葉を短くするか、タグ一覧から探してみてください。</p>";
      grid.replaceChildren(empty);
    }
    renderPagination(selected.length, currentPage, pageSize);
    document.documentElement.dataset.catalogLoaded = "true";
  }

  fetch(`${rootPath}data/articles.json`, { cache: "no-cache" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(value => render(Array.isArray(value) ? value : value.articles || []))
    .catch(error => {
      const grid = document.getElementById("catalogGrid");
      if (grid) {
        grid.innerHTML =
          '<div class="empty-state"><h2>記事一覧を読み込めませんでした</h2><p>ページを再読み込みしてください。</p></div>';
      }
      console.warn(error);
    });
})();
