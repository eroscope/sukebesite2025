"use strict";

const state = {
  token: "",
  articles: [],
  existingSlugs: new Set(),
  images: [],
  blocks: [],
  thumbnailId: "",
  dirty: false,
  busy: false,
  toastTimer: null,
};

const elements = {};

function uid(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function localDateTimeValue(date = new Date()) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function newPost(text = "") {
  return { id: uid("post"), type: "post", text, style: "normal" };
}

function newImageBlock(imageIds = []) {
  return { id: uid("images"), type: "images", image_ids: imageIds.slice() };
}

function resetState() {
  state.images = [];
  state.blocks = [newPost("")];
  state.thumbnailId = "";
  state.dirty = false;
  elements.titleInput.value = "";
  elements.slugInput.value = "";
  elements.categoryInput.value = "画像";
  elements.summaryInput.value = "";
  elements.publishedAtInput.value = localDateTimeValue();
  elements.statusInput.value = "published";
  elements.commentsInput.value = "0";
  elements.posterNameInput.value = "風吹けば名無し";
  elements.tagsInput.value = "";
  elements.featuredInput.checked = false;
  elements.fictionalInput.checked = true;
  elements.replaceInput.checked = false;
  elements.sourceUrlInput.value = "";
  elements.sourceLabelInput.value = "元記事";
  elements.transparencyInput.value = "";
  elements.adultConfirmed.checked = false;
  elements.rightsConfirmed.checked = false;
  elements.privacyConfirmed.checked = false;
  elements.sourceConfirmed.checked = false;
  elements.previewFrame.removeAttribute("srcdoc");
  elements.previewEmpty.hidden = false;
  elements.saveState.textContent = "新規記事";
  renderImages();
  renderBlocks();
  updateExistingState();
}

function markDirty() {
  if (state.busy) return;
  state.dirty = true;
  elements.saveState.textContent = "未保存";
}

function showToast(message, kind = "") {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast show ${kind}`.trim();
  state.toastTimer = window.setTimeout(() => {
    elements.toast.className = "toast";
  }, 4200);
}

function setBusy(busy) {
  state.busy = busy;
  [
    elements.saveDraftButton,
    elements.downloadPackageButton,
    elements.addToSiteButton,
    elements.refreshPreviewButton,
    elements.uploadButton,
  ].forEach((button) => {
    button.disabled = busy;
  });
}

async function apiJson(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(state.token ? { "X-Indanya-Token": state.token } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  const result = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

function splitTags(value) {
  return value
    .split(/[,、]/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function collectPayload() {
  const publishedValue = elements.publishedAtInput.value;
  const publishedAt = publishedValue ? new Date(publishedValue).toISOString() : "";
  return {
    title: elements.titleInput.value.trim(),
    slug: elements.slugInput.value.trim(),
    category: elements.categoryInput.value.trim(),
    summary: elements.summaryInput.value.trim(),
    published_at: publishedAt,
    status: elements.statusInput.value,
    comments: Number(elements.commentsInput.value || 0),
    poster_name: elements.posterNameInput.value.trim(),
    tags: splitTags(elements.tagsInput.value),
    featured: elements.featuredInput.checked,
    fictional_responses: elements.fictionalInput.checked,
    replace_existing: elements.replaceInput.checked,
    source_url: elements.sourceUrlInput.value.trim(),
    source_label: elements.sourceLabelInput.value.trim(),
    transparency_note: elements.transparencyInput.value.trim(),
    thumbnail_id: state.thumbnailId,
    adult_confirmed: elements.adultConfirmed.checked,
    rights_confirmed: elements.rightsConfirmed.checked,
    privacy_confirmed: elements.privacyConfirmed.checked,
    source_confirmed: elements.sourceConfirmed.checked,
    images: state.images.map((image) => ({ ...image })),
    blocks: state.blocks.map((block) => ({
      ...block,
      ...(block.image_ids ? { image_ids: block.image_ids.slice() } : {}),
    })),
  };
}

function validateBasics({ requireSafety = false } = {}) {
  const requiredFields = [
    elements.titleInput,
    elements.slugInput,
    elements.categoryInput,
    elements.publishedAtInput,
    elements.sourceUrlInput,
  ];
  for (const field of requiredFields) {
    if (!field.value.trim()) {
      field.focus();
      showToast("必須項目を入力してください", "error");
      return false;
    }
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(elements.slugInput.value.trim())) {
    elements.slugInput.focus();
    showToast("スラッグは半角英小文字・数字・ハイフンで入力してください", "error");
    return false;
  }
  if (state.images.length === 0) {
    showToast("画像を1枚以上追加してください", "error");
    return false;
  }
  const emptyPost = state.blocks.find((block) => block.type === "post" && !block.text.trim());
  if (emptyPost) {
    const textarea = elements.blockList.querySelector(`[data-block-id="${emptyPost.id}"] textarea`);
    textarea?.focus();
    showToast("空のレスを入力してください", "error");
    return false;
  }
  const used = state.blocks.flatMap((block) => block.type === "images" ? block.image_ids : []);
  if (new Set(used).size !== used.length || used.length !== state.images.length) {
    showToast("すべての画像を重複なしで記事へ配置してください", "error");
    return false;
  }
  if (requireSafety && ![
    elements.adultConfirmed,
    elements.rightsConfirmed,
    elements.privacyConfirmed,
    elements.sourceConfirmed,
  ].every((input) => input.checked)) {
    showToast("公開確認の4項目を確認してください", "error");
    return false;
  }
  return true;
}

function updateExistingState() {
  const exists = state.existingSlugs.has(elements.slugInput.value.trim());
  elements.replaceField.hidden = !exists;
  if (!exists) elements.replaceInput.checked = false;
}

function renderImages() {
  elements.imageList.replaceChildren();
  elements.imageCount.textContent = `${state.images.length} / 20`;
  if (!state.thumbnailId && state.images.length) state.thumbnailId = state.images[0].id;
  if (state.thumbnailId && !state.images.some((image) => image.id === state.thumbnailId)) {
    state.thumbnailId = state.images[0]?.id || "";
  }

  state.images.forEach((image, index) => {
    const item = document.createElement("article");
    item.className = "image-item";

    const preview = document.createElement("img");
    preview.src = image.data_url;
    preview.alt = image.alt;

    const fields = document.createElement("div");
    fields.className = "image-fields";
    const name = document.createElement("div");
    name.className = "image-name";
    name.textContent = `${index + 1}. ${image.name}`;
    fields.append(name);

    const alt = document.createElement("input");
    alt.type = "text";
    alt.maxLength = 180;
    alt.value = image.alt;
    alt.setAttribute("aria-label", `${image.name}の代替テキスト`);
    alt.addEventListener("input", () => {
      image.alt = alt.value;
      preview.alt = image.alt;
      markDirty();
    });
    fields.append(alt);

    const orientation = document.createElement("div");
    orientation.className = "orientation";
    [
      ["portrait", "縦"],
      ["landscape", "横"],
    ].forEach(([value, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.className = image.orientation === value ? "active" : "";
      button.addEventListener("click", () => {
        image.orientation = value;
        renderImages();
        markDirty();
      });
      orientation.append(button);
    });
    fields.append(orientation);

    const thumbnailLabel = document.createElement("label");
    thumbnailLabel.className = "image-choice";
    const thumbnail = document.createElement("input");
    thumbnail.type = "radio";
    thumbnail.name = "thumbnail";
    thumbnail.checked = state.thumbnailId === image.id;
    thumbnail.addEventListener("change", () => {
      state.thumbnailId = image.id;
      markDirty();
    });
    const thumbnailText = document.createElement("span");
    thumbnailText.textContent = "サムネイル";
    thumbnailLabel.append(thumbnail, thumbnailText);
    fields.append(thumbnailLabel);

    const remove = document.createElement("button");
    remove.className = "icon-button";
    remove.type = "button";
    remove.title = "画像を削除";
    remove.setAttribute("aria-label", `${image.name}を削除`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.images = state.images.filter((candidate) => candidate.id !== image.id);
      state.blocks.forEach((block) => {
        if (block.type === "images") {
          block.image_ids = block.image_ids.filter((imageId) => imageId !== image.id);
        }
      });
      state.blocks = state.blocks.filter((block) => block.type !== "images" || block.image_ids.length > 0);
      renderImages();
      renderBlocks();
      markDirty();
    });

    item.append(preview, fields, remove);
    elements.imageList.append(item);
  });
}

function blockLabel(type) {
  return { post: "R", images: "画", separator: "線", ad: "PR" }[type] || "?";
}

function moveBlock(index, offset) {
  const next = index + offset;
  if (next < 0 || next >= state.blocks.length) return;
  const [block] = state.blocks.splice(index, 1);
  state.blocks.splice(next, 0, block);
  renderBlocks();
  markDirty();
}

function renderBlocks() {
  elements.blockList.replaceChildren();
  const assigned = new Map();
  state.blocks.forEach((block) => {
    if (block.type === "images") {
      block.image_ids.forEach((imageId) => assigned.set(imageId, block.id));
    }
  });

  state.blocks.forEach((block, index) => {
    const item = document.createElement("article");
    item.className = "content-block";
    item.dataset.blockId = block.id;

    const kind = document.createElement("div");
    kind.className = "block-kind";
    kind.textContent = blockLabel(block.type);

    const body = document.createElement("div");
    body.className = "block-body";
    if (block.type === "post") {
      const textarea = document.createElement("textarea");
      textarea.value = block.text;
      textarea.maxLength = 1000;
      textarea.placeholder = "レス本文";
      textarea.setAttribute("aria-label", `レス ${index + 1}`);
      textarea.addEventListener("input", () => {
        block.text = textarea.value;
        markDirty();
      });
      const options = document.createElement("div");
      options.className = "block-options";
      const style = document.createElement("select");
      style.setAttribute("aria-label", "レスの表示スタイル");
      [["normal", "通常"], ["large", "大きく"], ["highlight", "強調"]].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = block.style === value;
        style.append(option);
      });
      style.addEventListener("change", () => {
        block.style = style.value;
        markDirty();
      });
      options.append(style);
      body.append(textarea, options);
    } else if (block.type === "images") {
      const picker = document.createElement("div");
      picker.className = "image-picker";
      state.images.forEach((image, imageIndex) => {
        const label = document.createElement("label");
        label.className = "image-choice";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = block.image_ids.includes(image.id);
        checkbox.disabled = assigned.has(image.id) && assigned.get(image.id) !== block.id;
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            if (block.image_ids.length >= 4) {
              checkbox.checked = false;
              showToast("1ブロックは画像4枚までです", "error");
              return;
            }
            block.image_ids.push(image.id);
          } else {
            block.image_ids = block.image_ids.filter((imageId) => imageId !== image.id);
          }
          renderBlocks();
          markDirty();
        });
        const text = document.createElement("span");
        text.textContent = `${imageIndex + 1}. ${image.alt || image.name}`;
        label.append(checkbox, text);
        picker.append(label);
      });
      if (state.images.length === 0) {
        const empty = document.createElement("span");
        empty.textContent = "画像なし";
        picker.append(empty);
      }
      body.append(picker);
    } else if (block.type === "separator") {
      const line = document.createElement("div");
      line.className = "section-rule";
      const label = document.createElement("span");
      label.textContent = "区切り線";
      line.append(label);
      body.append(line);
    } else if (block.type === "ad") {
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 240;
      input.value = block.text;
      input.placeholder = "関連広告枠";
      input.setAttribute("aria-label", "PR枠のテキスト");
      input.addEventListener("input", () => {
        block.text = input.value;
        markDirty();
      });
      body.append(input);
    }

    const controls = document.createElement("div");
    controls.className = "block-controls";
    [["↑", "上へ", -1], ["↓", "下へ", 1]].forEach(([symbol, label, offset]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = symbol;
      button.title = label;
      button.setAttribute("aria-label", `${block.type}を${label}`);
      button.disabled = (offset === -1 && index === 0) || (offset === 1 && index === state.blocks.length - 1);
      button.addEventListener("click", () => moveBlock(index, Number(offset)));
      controls.append(button);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "削除";
    remove.setAttribute("aria-label", `${block.type}を削除`);
    remove.addEventListener("click", () => {
      state.blocks.splice(index, 1);
      renderBlocks();
      markDirty();
    });
    controls.append(remove);

    item.append(kind, body, controls);
    elements.blockList.append(item);
  });
}

function addBlock(type) {
  if (type === "post") state.blocks.push(newPost());
  if (type === "images") state.blocks.push(newImageBlock());
  if (type === "separator") state.blocks.push({ id: uid("separator"), type: "separator" });
  if (type === "ad") state.blocks.push({ id: uid("ad"), type: "ad", text: "関連広告枠" });
  renderBlocks();
  markDirty();
  elements.blockList.scrollTop = elements.blockList.scrollHeight;
}

function autoArrange() {
  if (state.images.length === 0) {
    showToast("先に画像を追加してください", "error");
    return;
  }
  const posts = state.blocks.filter((block) => block.type === "post");
  if (posts.length === 0) posts.push(newPost());
  const groups = [];
  for (let index = 0; index < state.images.length; index += 2) {
    groups.push(state.images.slice(index, index + 2).map((image) => image.id));
  }
  const arranged = [];
  arranged.push(posts[0]);
  groups.forEach((group, index) => {
    arranged.push(newImageBlock(group));
    const followingPost = posts[index + 1];
    if (followingPost) {
      arranged.push(followingPost);
    } else if (index < groups.length - 1) {
      arranged.push(newPost());
    }
    if (index === 0 && groups.length > 1) {
      arranged.push({ id: uid("separator"), type: "separator" });
    }
  });
  posts.slice(groups.length + 1).forEach((post) => arranged.push(post));
  arranged.push({ id: uid("ad"), type: "ad", text: "記事内容に合う関連広告枠" });
  state.blocks = arranged;
  renderBlocks();
  markDirty();
  showToast("画像を記事内へ配置しました", "success");
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error("画像を読み込めませんでした"));
    reader.readAsDataURL(file);
  });
}

function inspectImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error("画像を開けませんでした"));
    image.src = dataUrl;
  });
}

function altFromName(name) {
  return name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ").trim() || "記事画像";
}

async function addImages(files) {
  const remaining = Math.max(0, 20 - state.images.length);
  const selected = Array.from(files).slice(0, remaining);
  if (selected.length === 0) return;
  setBusy(true);
  try {
    for (const file of selected) {
      if (file.size > 12 * 1024 * 1024) throw new Error(`${file.name} は12MBを超えています`);
      const dataUrl = await readFileAsDataUrl(file);
      const dimensions = await inspectImage(dataUrl);
      state.images.push({
        id: uid("image"),
        name: file.name,
        data_url: dataUrl,
        alt: altFromName(file.name),
        orientation: dimensions.width >= dimensions.height ? "landscape" : "portrait",
      });
    }
    if (!state.thumbnailId) state.thumbnailId = state.images[0]?.id || "";
    renderImages();
    renderBlocks();
    markDirty();
    showToast(`${selected.length}枚の画像を追加しました`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
    elements.imageInput.value = "";
  }
}

async function refreshPreview() {
  if (!validateBasics()) return;
  setBusy(true);
  try {
    const result = await apiJson("/api/render", { method: "POST", body: collectPayload() });
    elements.previewFrame.srcdoc = result.html;
    elements.previewEmpty.hidden = true;
    showToast("プレビューを更新しました", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function refreshDraftSelect(drafts) {
  const current = elements.draftSelect.value;
  elements.draftSelect.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "下書きを選択";
  elements.draftSelect.append(placeholder);
  drafts.forEach((draft) => {
    const option = document.createElement("option");
    option.value = draft.slug;
    option.textContent = draft.slug;
    elements.draftSelect.append(option);
  });
  if (drafts.some((draft) => draft.slug === current)) elements.draftSelect.value = current;
}

async function saveDraft() {
  const slug = elements.slugInput.value.trim();
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
    elements.slugInput.focus();
    showToast("下書き保存には有効なスラッグが必要です", "error");
    return;
  }
  setBusy(true);
  try {
    const result = await apiJson("/api/drafts", { method: "POST", body: collectPayload() });
    state.dirty = false;
    elements.saveState.textContent = "下書き保存済み";
    const bootstrap = await apiJson("/api/bootstrap");
    refreshDraftSelect(bootstrap.drafts);
    elements.draftSelect.value = result.slug;
    showToast(result.message, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function applyPayload(payload) {
  state.images = Array.isArray(payload.images) ? payload.images.map((image) => ({ ...image })) : [];
  state.blocks = Array.isArray(payload.blocks) ? payload.blocks.map((block) => ({
    ...block,
    id: block.id || uid(block.type || "block"),
    ...(block.image_ids ? { image_ids: block.image_ids.slice() } : {}),
  })) : [newPost()];
  state.thumbnailId = payload.thumbnail_id || state.images[0]?.id || "";
  elements.titleInput.value = payload.title || "";
  elements.slugInput.value = payload.slug || "";
  elements.categoryInput.value = payload.category || "画像";
  elements.summaryInput.value = payload.summary || "";
  elements.publishedAtInput.value = payload.published_at ? localDateTimeValue(new Date(payload.published_at)) : localDateTimeValue();
  elements.statusInput.value = payload.status || "draft";
  elements.commentsInput.value = String(payload.comments ?? 0);
  elements.posterNameInput.value = payload.poster_name || "風吹けば名無し";
  elements.tagsInput.value = Array.isArray(payload.tags) ? payload.tags.join(", ") : "";
  elements.featuredInput.checked = Boolean(payload.featured);
  elements.fictionalInput.checked = payload.fictional_responses !== false;
  elements.replaceInput.checked = Boolean(payload.replace_existing);
  elements.sourceUrlInput.value = payload.source_url || "";
  elements.sourceLabelInput.value = payload.source_label || "元記事";
  elements.transparencyInput.value = payload.transparency_note || "";
  elements.adultConfirmed.checked = Boolean(payload.adult_confirmed);
  elements.rightsConfirmed.checked = Boolean(payload.rights_confirmed);
  elements.privacyConfirmed.checked = Boolean(payload.privacy_confirmed);
  elements.sourceConfirmed.checked = Boolean(payload.source_confirmed);
  state.dirty = false;
  elements.saveState.textContent = "下書きを開きました";
  elements.previewFrame.removeAttribute("srcdoc");
  elements.previewEmpty.hidden = false;
  renderImages();
  renderBlocks();
  updateExistingState();
}

async function loadDraft() {
  const slug = elements.draftSelect.value;
  if (!slug) {
    showToast("下書きを選択してください", "error");
    return;
  }
  if (state.dirty && !window.confirm("未保存の変更を破棄しますか？")) return;
  setBusy(true);
  try {
    const payload = await apiJson(`/api/drafts/${encodeURIComponent(slug)}`);
    applyPayload(payload);
    showToast("下書きを開きました", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function downloadPackage() {
  if (!validateBasics()) return;
  setBusy(true);
  try {
    const response = await fetch("/api/package", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Indanya-Token": state.token },
      body: JSON.stringify(collectPayload()),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match?.[1] || `${elements.slugInput.value.trim()}.zip`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("記事パッケージを書き出しました", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function confirmAdd() {
  elements.confirmMessage.textContent = state.existingSlugs.has(elements.slugInput.value.trim())
    ? "既存の記事HTML・画像・記事一覧を更新します。"
    : "記事HTML・画像・記事一覧へ新しい記事を追加します。";
  elements.confirmDialog.showModal();
  return new Promise((resolve) => {
    elements.confirmDialog.addEventListener("close", () => {
      resolve(elements.confirmDialog.returnValue === "confirm");
    }, { once: true });
  });
}

async function addToSite() {
  if (!validateBasics({ requireSafety: true })) return;
  if (state.existingSlugs.has(elements.slugInput.value.trim()) && !elements.replaceInput.checked) {
    showToast("既存記事を更新する確認をオンにしてください", "error");
    return;
  }
  if (!await confirmAdd()) return;
  setBusy(true);
  try {
    const result = await apiJson("/api/articles", { method: "POST", body: collectPayload() });
    state.existingSlugs.add(result.slug);
    state.dirty = false;
    elements.saveState.textContent = "追加済み・公開待ち";
    updateExistingState();
    showToast(`${result.message} / 公開待ち`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function setPreviewMode(mode) {
  const mobile = mode === "mobile";
  elements.previewStage.classList.toggle("mobile", mobile);
  elements.mobilePreviewButton.classList.toggle("active", mobile);
  elements.desktopPreviewButton.classList.toggle("active", !mobile);
  elements.mobilePreviewButton.setAttribute("aria-pressed", String(mobile));
  elements.desktopPreviewButton.setAttribute("aria-pressed", String(!mobile));
}

function cacheElements() {
  [
    "saveState", "draftSelect", "loadDraftButton", "newArticleButton", "titleInput", "slugInput",
    "categoryInput", "categoryList", "summaryInput", "publishedAtInput", "statusInput", "commentsInput",
    "posterNameInput", "tagsInput", "featuredInput", "fictionalInput", "replaceField", "replaceInput",
    "sourceUrlInput", "sourceLabelInput", "transparencyInput", "imageCount", "uploadButton", "imageInput",
    "imageList", "autoArrangeButton", "blockList", "desktopPreviewButton", "mobilePreviewButton",
    "refreshPreviewButton", "previewStage", "previewFrame", "previewEmpty", "adultConfirmed",
    "rightsConfirmed", "privacyConfirmed", "sourceConfirmed", "saveDraftButton", "downloadPackageButton",
    "addToSiteButton", "confirmDialog", "confirmMessage", "toast",
  ].forEach((id) => {
    elements[id] = document.getElementById(id);
  });
}

function bindEvents() {
  elements.uploadButton.addEventListener("click", () => elements.imageInput.click());
  elements.imageInput.addEventListener("change", () => addImages(elements.imageInput.files));
  elements.autoArrangeButton.addEventListener("click", autoArrange);
  document.querySelectorAll("[data-add-block]").forEach((button) => {
    button.addEventListener("click", () => addBlock(button.dataset.addBlock));
  });
  elements.refreshPreviewButton.addEventListener("click", refreshPreview);
  elements.desktopPreviewButton.addEventListener("click", () => setPreviewMode("desktop"));
  elements.mobilePreviewButton.addEventListener("click", () => setPreviewMode("mobile"));
  elements.saveDraftButton.addEventListener("click", saveDraft);
  elements.downloadPackageButton.addEventListener("click", downloadPackage);
  elements.addToSiteButton.addEventListener("click", addToSite);
  elements.loadDraftButton.addEventListener("click", loadDraft);
  elements.newArticleButton.addEventListener("click", () => {
    if (state.dirty && !window.confirm("未保存の変更を破棄しますか？")) return;
    resetState();
  });
  elements.slugInput.addEventListener("input", updateExistingState);
  document.querySelectorAll("input, textarea, select").forEach((control) => {
    if (![elements.draftSelect, elements.imageInput].includes(control)) {
      control.addEventListener("input", markDirty);
      control.addEventListener("change", markDirty);
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function initialize() {
  cacheElements();
  bindEvents();
  resetState();
  try {
    const bootstrap = await apiJson("/api/bootstrap");
    state.token = bootstrap.token;
    state.articles = bootstrap.articles;
    state.existingSlugs = new Set(bootstrap.articles.map((article) => article.slug));
    bootstrap.categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category;
      elements.categoryList.append(option);
    });
    refreshDraftSelect(bootstrap.drafts);
    updateExistingState();
  } catch (error) {
    showToast(error.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", initialize);
