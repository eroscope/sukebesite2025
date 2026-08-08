(() => {
  "use strict";

  const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const localPathPattern = /^[A-Za-z0-9._/-]+$/;

  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "assets/common/home-sections.css";
  document.head.append(style);

  function isSafeLocalPath(value, prefix) {
    return typeof value === "string" &&
      value.startsWith(prefix) &&
      localPathPattern.test(value) &&
      !value.includes("..") &&
      !value.includes("//");
  }

  function isValidArticle(article) {
    if (!article || typeof article !== "object" || Array.isArray(article)) return false;
    if (typeof article.slug !== "string" || !slugPattern.test(article.slug)) return false;
    if (typeof article.title !== "string" || !article.title) return false;
    if (!(article.status === "published")) return false;
    if (article.url !== `articles/${article.slug}.html`) return false;
    if (!isSafeLocalPath(article.thumbnail, "assets/")) return false;
    return !Number.isNaN(Date.parse(article.published_at));
  }

  function setLink(element, article) {
    element.href = article.url;
    element.setAttribute("aria-label", article.title);
  }

  function createCard(article, index = 99, compact = false) {
    const card = document.createElement("article");
    card.className = compact ? "post-card portal-card" : "post-card";
    const thumbLink = document.createElement("a");
    thumbLink.className = "thumb";
    setLink(thumbLink, article);
    const image = document.createElement("img");
    image.src = article.thumbnail;
    image.alt = article.title;
    image.loading = index > 1 ? "lazy" : "eager";
    thumbLink.append(image);
    if (index === 0 && !compact) {
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
    if (!compact && article.summary) {
      const summary = document.createElement("p");
      summary.textContent = article.summary;
      body.append(summary);
    }
    card.append(thumbLink, body);
    return card;
  }

  function createRank(article) {
    const row = document.createElement("a");
    row.className = "rank rank-with-thumb";
    row.href = article.url;
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
    row.append(image, details);
    return row;
  }

  function isFanzaArticle(article) {
    const tags = (article.tags || []).map(tag => String(tag).toLowerCase());
    if (tags.includes("fanza") || tags.includes("pr")) return true;
    try {
      return /(?:^|\.)dmm\.co\.jp$|(?:^|\.)fanza\.co\.jp$/i.test(
        new URL(article.source_url || "").hostname
      );
    } catch {
      return false;
    }
  }

  function createPortalSection(title, copy, articles, href) {
    if (!articles.length) return null;
    const section = document.createElement("section");
    section.className = "home-portal-section";
    const head = document.createElement("header");
    const headingCopy = document.createElement("div");
    const heading = document.createElement("h2");
    heading.textContent = title;
    const description = document.createElement("p");
    description.textContent = copy;
    headingCopy.append(heading, description);
    const more = document.createElement("a");
    more.href = href;
    more.textContent = "すべて見る";
    head.append(headingCopy, more);
    const grid = document.createElement("div");
    grid.className = "home-portal-grid";
    grid.append(...articles.slice(0, 4).map((item, index) => createCard(item, index, true)));
    section.append(head, grid);
    return section;
  }

  function createDiscovery(articles) {
    const shell = document.createElement("div");
    shell.className = "home-discovery";
    const latest = [...articles].sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
    const popular = [...articles].sort((a, b) =>
      b.comments - a.comments || Date.parse(b.published_at) - Date.parse(a.published_at)
    );
    const sections = [
      ["急上昇", "コメントが集まっている記事", popular, "popular.html"],
      ["動画記事", "動きで見たい記事をまとめてチェック", latest.filter(item => String(item.category).includes("動画")), "search.html?category=動画"],
      ["画像・グラビア", "画像をじっくり見られる記事", latest.filter(item => String(item.category).includes("画像")), "search.html?category=画像"],
      ["SNS・X", "SNSで話題の人物と投稿", latest.filter(item => String(item.category).toUpperCase().includes("SNS") || (item.tags || []).some(tag => /X|SNS|Twitter/i.test(tag))), "search.html?category=SNS"],
      ["FANZA作品", "作品紹介と記事に近い関連作品", latest.filter(isFanzaArticle), "fanza.html"],
      ["編集部おすすめ", "新着以外からもう一度読みたい記事", popular.slice(4), "random.html"],
    ];
    sections.forEach(([title, copy, items, href]) => {
      const section = createPortalSection(title, copy, items, href);
      if (section) shell.append(section);
    });

    const counts = new Map();
    articles.forEach(item => {
      [item.category, ...(item.tags || []).slice(0, 5)].filter(Boolean).forEach(label =>
        counts.set(label, (counts.get(label) || 0) + 1)
      );
    });
    const tagSection = document.createElement("section");
    tagSection.className = "home-topic-section";
    const tagHeader = document.createElement("header");
    const tagCopy = document.createElement("div");
    const tagHeading = document.createElement("h2");
    tagHeading.textContent = "人気ジャンル";
    const tagDescription = document.createElement("p");
    tagDescription.textContent = "気分に合うタグから記事を探す";
    const categoryLink = document.createElement("a");
    categoryLink.href = "categories.html";
    categoryLink.textContent = "カテゴリ一覧";
    tagCopy.append(tagHeading, tagDescription);
    tagHeader.append(tagCopy, categoryLink);
    tagSection.append(tagHeader);
    const tags = document.createElement("div");
    tags.className = "home-topic-list";
    [...counts]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 18)
      .forEach(([label, count]) => {
        const link = document.createElement("a");
        link.href = `search.html?tag=${encodeURIComponent(label)}`;
        const name = document.createElement("strong");
        name.textContent = label;
        const amount = document.createElement("span");
        amount.textContent = `${count}記事`;
        link.append(name, amount);
        tags.append(link);
      });
    tagSection.append(tags);
    shell.append(tagSection);
    return shell;
  }

  const featureRotationDays = 3;
  const adultFeaturePattern =
    /成人|18禁|AV|エロ|ヌード|裸|乳|胸|尻|セックス|オナ|パンツ|下着|ランジェリー|水着|コスプレ|風俗|痴漢|露出|巨乳|爆乳|乳首|おっぱい|自慰|フェラ|3P|NTR/i;

  function isAdultFeatureCandidate(article) {
    return adultFeaturePattern.test(
      [article.title, article.summary, article.category, ...(article.tags || [])].join(" ")
    );
  }

  function selectFeaturedArticle(articles) {
    const adultCandidates = articles.filter(isAdultFeatureCandidate);
    const candidates = (adultCandidates.length ? adultCandidates : articles).slice(0, 12);
    if (candidates.length <= 1) return candidates[0];

    const rotation = Math.floor(
      Date.now() / (featureRotationDays * 24 * 60 * 60 * 1000)
    );
    const candidateKey = candidates.map(article => article.slug).join("|");
    let offset = 0;
    for (let index = 0; index < candidateKey.length; index += 1) {
      offset = (offset * 31 + candidateKey.charCodeAt(index)) >>> 0;
    }
    return candidates[(offset + rotation) % candidates.length];
  }

  function render(articles) {
    const featured = selectFeaturedArticle(articles);
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
    featureBadge.textContent = `${featured.category} / ${featured.images_used || 1}枚`;
    featureTitleLink.textContent = featured.title;
    featureSummary.textContent =
      featured.summary || `${featured.images_used || 1}枚の素材をレスの流れでまとめています。`;
    document.documentElement.classList.add("home-ready");

    function selectArticles(mode) {
      if (mode === "popular") {
        return [...articles].sort((left, right) =>
          right.comments - left.comments || Date.parse(right.published_at) - Date.parse(left.published_at)
        );
      }
      if (mode === "random") return [...articles].sort(() => Math.random() - 0.5);
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
      .sort((left, right) =>
        right.comments - left.comments || Date.parse(right.published_at) - Date.parse(left.published_at)
      )
      .slice(0, 7);
    popularArticles.replaceChildren(...ranking.map(createRank));

    const moreRow = document.querySelector(".more-row");
    if (moreRow && !document.querySelector(".home-discovery")) {
      moreRow.before(createDiscovery(articles));
    }
    document.documentElement.dataset.articlesLoaded = "true";
  }

  function renderEmpty() {
    const articleGrid = document.getElementById("articleGrid");
    const popularArticles = document.getElementById("popularArticles");
    if (articleGrid) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML = "<h2>作品記事を準備中です</h2><p>公開可能な商品記事から順に追加します。</p>";
      articleGrid.replaceChildren(empty);
    }
    if (popularArticles) {
      const note = document.createElement("p");
      note.textContent = "記事公開後に表示されます。";
      popularArticles.replaceChildren(note);
    }
    document.documentElement.classList.add("home-ready");
    document.documentElement.dataset.articlesLoaded = "empty";
  }

  fetch("data/articles.json", { cache: "no-cache" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(value => {
      const data = Array.isArray(value) ? value : value.articles || [];
      const published = data
        .filter(isValidArticle)
        .sort((left, right) => Date.parse(right.published_at) - Date.parse(left.published_at));
      if (published.length) render(published);
      else renderEmpty();
    })
    .catch(error => {
      document.documentElement.dataset.articlesLoaded = "fallback";
      console.warn("記事一覧を読み込めなかったため、静的表示を使用します。", error);
    });
})();
