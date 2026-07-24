"use strict";

const state = {
  token: "",
  articles: [],
  existingSlugs: new Set(),
  images: [],
  videos: [],
  blocks: [],
  thumbnailId: "",
  dirty: false,
  busy: false,
  toastTimer: null,
  xSession: "",
  xAccount: null,
  xPosts: [],
  xTokenConfigured: false,
  xFreeCover: null,
  sourceSession: "",
  sourceData: null,
  sourceImages: [],
  sourceSelectedIds: new Set(),
  sourceVideos: [],
  sourceSelectedVideoIds: new Set(),
  showExcludedImages: false,
  sourceFallback: null,
  drafts: [],
  jobs: [],
  codex: { available: false, version: "", message: "Codexを確認中" },
  activeJobId: "",
  generationRunning: false,
  generationPollTimer: null,
  currentView: "source",
  editorialStatus: "draft",
  rightsStatus: "unconfirmed",
  rightsContact: "",
  rightsNote: "",
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

function newVideoBlock(videoIds = []) {
  return { id: uid("videos"), type: "videos", video_ids: videoIds.slice() };
}

function resetState() {
  state.images = [];
  state.videos = [];
  state.blocks = [newPost("")];
  state.thumbnailId = "";
  state.dirty = false;
  elements.titleInput.value = "";
  elements.slugInput.value = "";
  elements.categoryInput.value = "画像";
  elements.summaryInput.value = "";
  elements.publishedAtInput.value = localDateTimeValue();
  elements.statusInput.value = "draft";
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
  elements.editorArticleTitle.textContent = "新規記事";
  state.editorialStatus = "draft";
  state.rightsStatus = "unconfirmed";
  state.rightsContact = "";
  state.rightsNote = "";
  renderImages();
  renderVideos();
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
    elements.xImportButton,
    elements.xFreeBuildButton,
    elements.xFetchButton,
    elements.xBuildButton,
    elements.sourceAnalyzeButton,
  ].forEach((button) => {
    if (button) button.disabled = busy;
  });
  updateGenerateAvailability();
}

function updateGenerateAvailability() {
  if (!elements.sourceGenerateButton) return;
  elements.sourceGenerateButton.disabled = state.busy || state.generationRunning || !state.codex.available;
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

const VIEW_META = {
  source: ["CREATE", "URLから記事を作成"],
  drafts: ["DRAFTS", "記事下書き"],
  rights: ["RIGHTS", "許可管理"],
  editor: ["EDITOR", "記事を編集"],
  future: ["WORKFLOW", "準備中"],
};

function showView(name, futureTitle = "") {
  const resolved = ["source", "drafts", "rights", "editor"].includes(name) ? name : "future";
  state.currentView = resolved;
  elements.sourceView.hidden = resolved !== "source";
  elements.draftsView.hidden = resolved !== "drafts";
  elements.rightsView.hidden = resolved !== "rights";
  elements.editorView.hidden = resolved !== "editor";
  elements.futureView.hidden = resolved !== "future";
  elements.editorDraftTools.hidden = resolved !== "editor";
  const [eyebrow, title] = VIEW_META[resolved];
  elements.viewEyebrow.textContent = eyebrow;
  elements.viewTitle.textContent = resolved === "future" && futureTitle ? futureTitle : title;
  if (resolved === "future") elements.futureTitle.textContent = futureTitle || "準備中";
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === resolved || (resolved === "future" && button.dataset.title === futureTitle);
    button.classList.toggle("active", active);
  });
  window.scrollTo({ top: 0, behavior: "auto" });
}

function sourceTypeLabel(type) {
  return ({ x_post: "X POST", x_profile: "X PROFILE", youtube: "VIDEO", web: "WEB" })[type] || "WEB";
}

function selectedSourceImageIds() {
  return Array.from(state.sourceSelectedIds);
}

function selectedSourceVideoIds() {
  return Array.from(state.sourceSelectedVideoIds);
}

function updateSourceSelection() {
  const selected = new Set(selectedSourceImageIds());
  elements.sourceImageGrid.querySelectorAll(".source-image-choice").forEach((choice) => {
    choice.classList.toggle("selected", selected.has(choice.dataset.imageId));
  });
  const selectedVideos = new Set(selectedSourceVideoIds());
  elements.sourceVideoGrid.querySelectorAll(".source-video-choice").forEach((choice) => {
    choice.classList.toggle("selected", selectedVideos.has(choice.dataset.videoId));
  });
  const fallbackSelected = state.sourceFallback ? 1 : 0;
  elements.sourceSelectedCount.textContent = `${selected.size + fallbackSelected}画像・${selectedVideos.size}動画`;
}

function renderSourceImages() {
  const excludedImages = state.sourceImages.filter((image) => !image.ai_recommended);
  const excludedVideos = state.sourceVideos.filter((video) => !video.ai_recommended);
  const excludedCount = excludedImages.length + excludedVideos.length;
  const visibleImages = state.showExcludedImages
    ? state.sourceImages
    : state.sourceImages.filter((image) => image.ai_recommended);
  elements.sourceImageGrid.replaceChildren();
  elements.sourceExcludedToggleButton.hidden = excludedCount === 0;
  elements.sourceExcludedToggleButton.textContent = state.showExcludedImages
    ? "除外素材を隠す"
    : `除外素材 ${excludedCount}`;
  if (!visibleImages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-media";
    empty.textContent = state.sourceImages.length
      ? "Codexが記事画像を選べませんでした"
      : "ページ内の画像を取得できませんでした";
    elements.sourceImageGrid.append(empty);
  }
  visibleImages.forEach((image) => {
    const index = state.sourceImages.findIndex((candidate) => candidate.id === image.id);
    const choice = document.createElement("label");
    choice.className = `source-image-choice${image.ai_recommended ? "" : " excluded"}`;
    choice.dataset.imageId = image.id;
    choice.title = image.ai_reason || "";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = image.id;
    checkbox.checked = state.sourceSelectedIds.has(image.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (state.sourceSelectedIds.size >= 10) {
          checkbox.checked = false;
          showToast("画像は最大10枚まで選べます", "error");
          return;
        }
        state.sourceSelectedIds.add(image.id);
      } else {
        state.sourceSelectedIds.delete(image.id);
      }
      updateSourceSelection();
    });
    const preview = document.createElement("img");
    preview.src = image.preview_url;
    preview.alt = image.alt || `画像候補 ${index + 1}`;
    preview.loading = "lazy";
    const count = document.createElement("span");
    count.className = "source-image-index";
    count.textContent = String(index + 1).padStart(2, "0");
    choice.append(checkbox, preview, count);
    if (!image.ai_recommended) {
      const verdict = document.createElement("strong");
      verdict.className = `source-image-verdict ${image.ai_verdict || "unclear"}`;
      verdict.textContent = ({
        advertisement: "広告", logo: "ロゴ", navigation: "導線", unrelated: "無関係", unclear: "要確認", article: "低関連",
      })[image.ai_verdict] || "除外";
      choice.append(verdict);
    }
    elements.sourceImageGrid.append(choice);
  });
  elements.sourceFallbackBox.hidden = state.sourceImages.some((image) => image.ai_recommended);
  renderSourceVideos();
  updateSourceSelection();
}

function renderSourceVideos() {
  const visibleVideos = state.showExcludedImages
    ? state.sourceVideos
    : state.sourceVideos.filter((video) => video.ai_recommended);
  elements.sourceVideoGrid.replaceChildren();
  if (!visibleVideos.length) {
    const empty = document.createElement("div");
    empty.className = "empty-media";
    empty.textContent = state.sourceVideos.length
      ? "Codexが本編動画を選びませんでした"
      : "ページ内の動画候補はありません";
    elements.sourceVideoGrid.append(empty);
    return;
  }
  visibleVideos.forEach((video) => {
    const index = state.sourceVideos.findIndex((candidate) => candidate.id === video.id);
    const choice = document.createElement("article");
    choice.className = `source-video-choice${video.ai_recommended ? "" : " excluded"}`;
    choice.dataset.videoId = video.id;
    choice.title = video.ai_reason || "";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = video.id;
    checkbox.checked = state.sourceSelectedVideoIds.has(video.id);
    checkbox.setAttribute("aria-label", `動画候補 ${index + 1}を使用`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (state.sourceSelectedVideoIds.size >= 5) {
          checkbox.checked = false;
          showToast("動画は最大5本まで選べます", "error");
          return;
        }
        state.sourceSelectedVideoIds.add(video.id);
      } else {
        state.sourceSelectedVideoIds.delete(video.id);
      }
      updateSourceSelection();
    });
    let preview;
    if (video.kind === "direct") {
      preview = document.createElement("video");
      preview.src = video.preview_url;
      preview.controls = true;
      preview.muted = true;
      preview.playsInline = true;
      preview.preload = "metadata";
    } else {
      preview = document.createElement("div");
      preview.className = "source-video-placeholder";
      preview.textContent = `埋め込み候補\n${new URL(video.url).hostname}`;
    }
    const copy = document.createElement("div");
    copy.className = "source-video-copy";
    const title = document.createElement("strong");
    title.textContent = video.title || `${video.kind === "direct" ? "動画" : "埋め込み"} ${index + 1}`;
    const detail = document.createElement("span");
    detail.textContent = video.ai_reason || video.url;
    copy.append(title, detail);
    choice.append(checkbox, preview, copy);
    if (!video.ai_recommended) {
      const verdict = document.createElement("strong");
      verdict.className = `source-image-verdict ${video.ai_verdict || "unclear"}`;
      verdict.textContent = ({ advertisement: "広告", navigation: "導線", unrelated: "無関係", unclear: "要確認", article: "低関連" })[video.ai_verdict] || "除外";
      choice.append(verdict);
    }
    elements.sourceVideoGrid.append(choice);
  });
}

function renderSourceResult(result) {
  state.sourceSession = result.session_id;
  state.sourceData = result.source;
  state.sourceImages = Array.isArray(result.images) ? result.images : [];
  state.sourceVideos = Array.isArray(result.videos) ? result.videos : [];
  state.sourceSelectedVideoIds = new Set(result.recommended_video_ids || []);
  const recommendedImages = result.recommended_image_ids || [];
  state.sourceSelectedIds = new Set(state.sourceSelectedVideoIds.size ? recommendedImages.slice(0, 1) : recommendedImages);
  state.showExcludedImages = false;
  state.sourceFallback = null;
  elements.sourceFallbackInput.value = "";
  elements.sourceFallbackPreview.replaceChildren();
  elements.sourceTypeBadge.textContent = sourceTypeLabel(result.source.type);
  elements.sourceSiteName.textContent = result.source.site_name || "元ページ";
  elements.sourceOpenLink.href = result.source.url;
  elements.sourceResultTitle.textContent = result.source.title || "タイトルを取得できませんでした";
  elements.sourceResultDescription.textContent = result.source.description || "概要は記事編集画面で追加できます。";
  elements.sourceAnalysisLabel.textContent = result.source.analysis_method === "codex_vision" ? "CODEX ANALYZED" : "EXTRACTED";
  if (["SNS", "画像", "動画", "話題"].includes(result.source.category)) {
    elements.sourceCategoryInput.value = result.source.category;
  }
  elements.sourceImageMetric.textContent = `${state.sourceSelectedIds.size}/${state.sourceImages.length}`;
  elements.sourceVideoMetric.textContent = `${state.sourceSelectedVideoIds.size}/${state.sourceVideos.length}`;
  elements.sourceTextMetric.textContent = String(Array.isArray(result.source.excerpts) ? result.source.excerpts.length : 0);
  elements.sourceResult.hidden = false;
  renderSourceImages();
  elements.sourceResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function analyzeSource({ autoGenerate = true } = {}) {
  const url = elements.sourceAnalyzerInput.value.trim();
  if (!url) {
    elements.sourceAnalyzerInput.focus();
    showToast("記事にしたいURLを入力してください", "error");
    return;
  }
  state.sourceSession = "";
  state.sourceSelectedIds = new Set();
  state.sourceSelectedVideoIds = new Set();
  state.showExcludedImages = false;
  elements.sourceProgress.hidden = false;
  elements.sourceResult.hidden = true;
  setBusy(true);
  try {
    const result = await apiJson("/api/source/analyze", { method: "POST", body: { url } });
    renderSourceResult(result);
    if (autoGenerate) {
      showToast("Codexが本編素材を選びました。記事を作成します", "success");
      await buildSourceDraft();
    } else {
      showToast("ページの解析が完了しました", "success");
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    elements.sourceProgress.hidden = true;
    setBusy(false);
  }
}

async function pasteSourceUrl() {
  try {
    const text = await navigator.clipboard.readText();
    elements.sourceAnalyzerInput.value = text.trim();
    elements.sourceAnalyzerInput.focus();
  } catch (_error) {
    elements.sourceAnalyzerInput.focus();
    showToast("URL欄へ貼り付けてください", "error");
  }
}

async function selectSourceFallback() {
  const file = elements.sourceFallbackInput.files[0];
  state.sourceFallback = null;
  elements.sourceFallbackPreview.replaceChildren();
  if (!file) {
    updateSourceSelection();
    return;
  }
  try {
    const dataUrl = await readFileAsDataUrl(file);
    const dimensions = await inspectImage(dataUrl);
    state.sourceFallback = {
      name: file.name,
      data_url: dataUrl,
      alt: state.sourceData?.title || "記事の画像",
      orientation: dimensions.width >= dimensions.height ? "landscape" : "portrait",
    };
    const preview = document.createElement("img");
    preview.src = dataUrl;
    preview.alt = "追加画像のプレビュー";
    elements.sourceFallbackPreview.append(preview);
    updateSourceSelection();
  } catch (error) {
    showToast(error.message || "画像を読み込めませんでした", "error");
  }
}

function applySourceOptions(payload) {
  if (elements.sourceCategoryInput.value !== "auto") payload.category = elements.sourceCategoryInput.value;
  const requestedPosts = Number(elements.sourceReplyCountInput.value);
  if (Number.isInteger(requestedPosts) && requestedPosts > 0) {
    let postCount = 0;
    payload.blocks = payload.blocks.filter((block) => {
      if (block.type !== "post") return true;
      postCount += 1;
      return postCount <= requestedPosts;
    });
    payload.comments = Math.min(postCount, requestedPosts);
  }
  return payload;
}

function renderCodexStatus() {
  const available = Boolean(state.codex.available);
  elements.codexStatus.classList.toggle("offline", !available);
  elements.codexStatusTitle.textContent = available ? "Codex 接続済み" : "Codex 未接続";
  elements.codexStatusDetail.textContent = available
    ? (state.codex.version || "生成できます")
    : (state.codex.message || "Codex CLIを確認してください");
  updateGenerateAvailability();
}

function renderGenerationJob(job) {
  if (!job) {
    elements.generationJob.hidden = true;
    return;
  }
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const labels = { queued: "生成待ち", running: "Codex生成中", completed: "生成完了", failed: "生成失敗" };
  elements.generationJob.hidden = false;
  elements.generationJob.dataset.status = job.status;
  elements.generationJobTitle.textContent = labels[job.status] || "生成処理";
  elements.generationJobProgress.textContent = `${progress}%`;
  elements.generationProgressBar.value = progress;
  elements.generationJobMessage.textContent = job.error || job.message || "";
  elements.generationRetryButton.hidden = job.status !== "failed";
}

async function completeGeneration(job) {
  state.generationRunning = false;
  state.activeJobId = "";
  updateGenerateAvailability();
  const bootstrap = await apiJson("/api/bootstrap");
  state.jobs = bootstrap.jobs || [];
  refreshDraftSelect(bootstrap.drafts || []);
  const payload = await apiJson(`/api/drafts/${encodeURIComponent(job.slug)}`);
  applyPayload(payload);
  elements.draftSelect.value = job.slug;
  elements.saveState.textContent = "Codex生成・保存済み";
  showView("editor");
  showToast("Codexが記事を作成し、許可管理へ登録しました", "success");
  await refreshPreview();
}

async function pollGenerationJob(jobId) {
  window.clearTimeout(state.generationPollTimer);
  if (!jobId) return;
  try {
    const result = await apiJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    const job = result.job;
    state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
    renderGenerationJob(job);
    if (job.status === "completed") {
      await completeGeneration(job);
      return;
    }
    if (job.status === "failed") {
      state.generationRunning = false;
      state.activeJobId = "";
      updateGenerateAvailability();
      showToast(job.error || "記事生成に失敗しました", "error");
      return;
    }
    state.generationPollTimer = window.setTimeout(() => pollGenerationJob(jobId), 1400);
  } catch (error) {
    state.generationPollTimer = window.setTimeout(() => pollGenerationJob(jobId), 2500);
    showToast(error.message, "error");
  }
}

async function buildSourceDraft() {
  if (!state.sourceSession) {
    showToast("先にURLを解析してください", "error");
    return;
  }
  const selectedImageIds = selectedSourceImageIds();
  const selectedVideoIds = selectedSourceVideoIds();
  if (!selectedImageIds.length && !state.sourceFallback) {
    showToast("記事一覧のサムネイルに使う画像を選択してください", "error");
    return;
  }
  if (!state.codex.available) {
    showToast(state.codex.message || "Codexへ接続できません", "error");
    return;
  }
  setBusy(true);
  try {
    const result = await apiJson("/api/source/generate", {
      method: "POST",
      body: {
        session_id: state.sourceSession,
        selected_image_ids: selectedImageIds,
        selected_video_ids: selectedVideoIds,
        manual_image: state.sourceFallback,
        tone: elements.sourceToneInput.value,
        category: elements.sourceCategoryInput.value,
        reply_count: elements.sourceReplyCountInput.value,
      },
    });
    state.activeJobId = result.job.id;
    state.generationRunning = true;
    state.jobs = [result.job, ...state.jobs.filter((job) => job.id !== result.job.id)];
    renderGenerationJob(result.job);
    showToast("Codexへ記事生成を依頼しました", "success");
    pollGenerationJob(result.job.id);
  } catch (error) {
    state.generationRunning = false;
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function formatDraftDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "---";
  return new Intl.DateTimeFormat("ja-JP", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

async function openDraftSlug(slug) {
  elements.draftSelect.value = slug;
  await loadDraft();
  if (elements.slugInput.value === slug) showView("editor");
}

function draftMiniCard(draft) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "draft-mini";
  const mark = document.createElement("span");
  mark.className = "draft-mini-mark";
  mark.textContent = String(draft.category || "記").slice(0, 1);
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = draft.title;
  const meta = document.createElement("span");
  meta.textContent = `${formatDraftDate(draft.updated_at)}・画像${draft.image_count}枚・動画${draft.video_count || 0}本`;
  copy.append(title, meta);
  const arrow = document.createElement("em");
  arrow.textContent = "↗";
  button.append(mark, copy, arrow);
  button.addEventListener("click", () => openDraftSlug(draft.slug));
  return button;
}

function renderDraftViews(drafts) {
  state.drafts = Array.isArray(drafts) ? drafts : [];
  elements.draftNavCount.textContent = String(state.drafts.length);
  elements.rightsNavCount.textContent = String(state.drafts.filter((draft) => draft.rights_status !== "confirmed").length);
  elements.draftTotalCount.textContent = String(state.drafts.length);
  elements.draftRightsCount.textContent = String(state.drafts.filter((draft) => draft.rights_status !== "confirmed").length);
  elements.recentDrafts.replaceChildren();
  state.drafts.slice(0, 3).forEach((draft) => elements.recentDrafts.append(draftMiniCard(draft)));
  if (!state.drafts.length) {
    const empty = document.createElement("div");
    empty.className = "draft-empty";
    empty.textContent = "保存済みの下書きはありません";
    elements.recentDrafts.append(empty);
  }

  elements.draftsList.replaceChildren();
  const head = document.createElement("div");
  head.className = "draft-table-head";
  ["記事", "状態", "画像利用", "画像", "更新", ""].forEach((label) => {
    const cell = document.createElement("span");
    cell.textContent = label;
    head.append(cell);
  });
  elements.draftsList.append(head);
  state.drafts.forEach((draft) => {
    const row = document.createElement("div");
    row.className = "draft-row";
    const titleCell = document.createElement("div");
    titleCell.className = "draft-title-cell";
    const title = document.createElement("strong");
    title.textContent = draft.title;
    const source = document.createElement("span");
    source.textContent = draft.source_url || draft.slug;
    titleCell.append(title, source);
    const status = document.createElement("span");
    status.className = "status-pill";
    status.textContent = draft.status === "published" ? "公開" : "下書き";
    const rights = document.createElement("span");
    rights.className = `status-pill${draft.rights_status === "confirmed" ? "" : " warning"}`;
    rights.textContent = ({
      unconfirmed: "未連絡", requested: "依頼済み", confirmed: "許可済み", rejected: "使用不可",
    })[draft.rights_status] || "未連絡";
    const images = document.createElement("span");
    images.textContent = `画${draft.image_count}・動${draft.video_count || 0}`;
    const updated = document.createElement("span");
    updated.textContent = formatDraftDate(draft.updated_at);
    const open = document.createElement("button");
    open.type = "button";
    open.className = "draft-open";
    open.title = "下書きを開く";
    open.setAttribute("aria-label", "下書きを開く");
    open.textContent = "↗";
    open.addEventListener("click", () => openDraftSlug(draft.slug));
    row.append(titleCell, status, rights, images, updated, open);
    elements.draftsList.append(row);
  });
  if (!state.drafts.length) {
    const empty = document.createElement("div");
    empty.className = "draft-empty";
    empty.textContent = "URLから最初の記事を作成してください";
    elements.draftsList.append(empty);
  }
  renderRightsView();
}

const RIGHTS_LABELS = {
  unconfirmed: "未連絡",
  requested: "依頼済み",
  confirmed: "許可済み",
  rejected: "使用不可",
};

async function saveRightsRow(draft, statusSelect, contactInput) {
  statusSelect.disabled = true;
  contactInput.disabled = true;
  try {
    await apiJson(`/api/rights/${encodeURIComponent(draft.slug)}`, {
      method: "POST",
      body: {
        rights_status: statusSelect.value,
        rights_contact: contactInput.value.trim(),
        rights_note: draft.rights_note || "",
      },
    });
    const bootstrap = await apiJson("/api/bootstrap");
    refreshDraftSelect(bootstrap.drafts || []);
    showToast("許可状態を更新しました", "success");
  } catch (error) {
    statusSelect.disabled = false;
    contactInput.disabled = false;
    showToast(error.message, "error");
  }
}

function renderRightsView() {
  const counts = { unconfirmed: 0, requested: 0, confirmed: 0, rejected: 0 };
  state.drafts.forEach((draft) => {
    const status = RIGHTS_LABELS[draft.rights_status] ? draft.rights_status : "unconfirmed";
    counts[status] += 1;
  });
  elements.rightsUnconfirmedCount.textContent = String(counts.unconfirmed);
  elements.rightsRequestedCount.textContent = String(counts.requested);
  elements.rightsConfirmedCount.textContent = String(counts.confirmed);
  elements.rightsRejectedCount.textContent = String(counts.rejected);
  elements.rightsList.replaceChildren();

  const head = document.createElement("div");
  head.className = "rights-table-head";
  ["記事", "出典", "連絡先", "画像利用", "更新", ""].forEach((label) => {
    const cell = document.createElement("span");
    cell.textContent = label;
    head.append(cell);
  });
  elements.rightsList.append(head);

  state.drafts.forEach((draft) => {
    const row = document.createElement("div");
    row.className = "rights-row";
    const title = document.createElement("strong");
    title.textContent = draft.title;
    const source = document.createElement("a");
    source.href = draft.source_url || "#";
    source.target = "_blank";
    source.rel = "noopener";
    source.textContent = draft.source_url ? "元ページ ↗" : "出典なし";
    if (!draft.source_url) source.removeAttribute("target");
    const contact = document.createElement("input");
    contact.type = "text";
    contact.maxLength = 200;
    contact.placeholder = "@アカウント / 連絡先";
    contact.value = draft.rights_contact || "";
    contact.setAttribute("aria-label", `${draft.title}の連絡先`);
    const select = document.createElement("select");
    select.setAttribute("aria-label", `${draft.title}の許可状態`);
    Object.entries(RIGHTS_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    });
    select.value = RIGHTS_LABELS[draft.rights_status] ? draft.rights_status : "unconfirmed";
    select.className = `rights-status ${select.value}`;
    select.addEventListener("change", () => saveRightsRow(draft, select, contact));
    contact.addEventListener("change", () => saveRightsRow(draft, select, contact));
    const updated = document.createElement("span");
    updated.textContent = formatDraftDate(draft.updated_at);
    const open = document.createElement("button");
    open.type = "button";
    open.className = "draft-open";
    open.title = "記事を開く";
    open.setAttribute("aria-label", "記事を開く");
    open.textContent = "↗";
    open.addEventListener("click", () => openDraftSlug(draft.slug));
    row.append(title, source, contact, select, updated, open);
    elements.rightsList.append(row);
  });
  if (!state.drafts.length) {
    const empty = document.createElement("div");
    empty.className = "draft-empty";
    empty.textContent = "許可確認待ちの記事はありません";
    elements.rightsList.append(empty);
  }
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
    editorial_status: state.editorialStatus,
    rights_status: elements.rightsConfirmed.checked ? "confirmed" : state.rightsStatus,
    rights_contact: state.rightsContact,
    rights_note: state.rightsNote,
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
    videos: state.videos.map((video) => ({ ...video })),
    blocks: state.blocks.map((block) => ({
      ...block,
      ...(block.image_ids ? { image_ids: block.image_ids.slice() } : {}),
      ...(block.video_ids ? { video_ids: block.video_ids.slice() } : {}),
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
  const used = state.blocks.flatMap((block) => ["images", "x_embed", "x_timeline"].includes(block.type) ? (block.image_ids || []) : []);
  const requiredImageIds = state.images
    .map((image) => image.id)
    .filter((imageId) => state.videos.length === 0 || imageId !== state.thumbnailId);
  if (new Set(used).size !== used.length || requiredImageIds.some((imageId) => !used.includes(imageId))) {
    showToast("すべての画像を重複なしで記事へ配置してください", "error");
    return false;
  }
  const usedVideos = state.blocks.flatMap((block) => block.type === "videos" ? (block.video_ids || []) : []);
  if (new Set(usedVideos).size !== usedVideos.length || usedVideos.length !== state.videos.length) {
    showToast("すべての動画を重複なしで記事へ配置してください", "error");
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

function renderVideos() {
  elements.videoList.replaceChildren();
  elements.videoCount.textContent = `${state.videos.length} / 6`;
  state.videos.forEach((video, index) => {
    const item = document.createElement("article");
    item.className = "video-item";
    let preview;
    if (video.kind === "direct") {
      preview = document.createElement("video");
      const referer = video.referer || elements.sourceUrlInput.value.trim();
      preview.src = `/api/video-proxy?url=${encodeURIComponent(video.url)}&referer=${encodeURIComponent(referer)}`;
      preview.controls = true;
      preview.muted = true;
      preview.playsInline = true;
      preview.preload = "metadata";
    } else {
      preview = document.createElement("div");
      preview.className = "source-video-placeholder";
      preview.textContent = "外部埋め込み";
    }
    const fields = document.createElement("div");
    fields.className = "video-fields";
    const name = document.createElement("strong");
    name.textContent = `${index + 1}. ${video.kind === "direct" ? "動画" : "埋め込み"}`;
    const label = document.createElement("input");
    label.type = "text";
    label.maxLength = 180;
    label.value = video.label || `元記事の動画 ${index + 1}`;
    label.setAttribute("aria-label", `動画 ${index + 1}の表示名`);
    label.addEventListener("input", () => {
      video.label = label.value;
      markDirty();
    });
    const url = document.createElement("span");
    url.textContent = video.url;
    fields.append(name, label, url);
    const remove = document.createElement("button");
    remove.className = "icon-button";
    remove.type = "button";
    remove.title = "動画を削除";
    remove.setAttribute("aria-label", `動画 ${index + 1}を削除`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.videos = state.videos.filter((candidate) => candidate.id !== video.id);
      state.blocks.forEach((block) => {
        if (block.type === "videos") {
          block.video_ids = block.video_ids.filter((videoId) => videoId !== video.id);
        }
      });
      state.blocks = state.blocks.filter((block) => block.type !== "videos" || block.video_ids.length > 0);
      renderVideos();
      renderBlocks();
      markDirty();
    });
    item.append(preview, fields, remove);
    elements.videoList.append(item);
  });
}

function blockLabel(type) {
  return { post: "R", images: "画", videos: "動", x_embed: "X", x_timeline: "X", separator: "線", ad: "PR" }[type] || "?";
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
  const assignedVideos = new Map();
  state.blocks.forEach((block) => {
    if (["images", "x_embed", "x_timeline"].includes(block.type)) {
      (block.image_ids || []).forEach((imageId) => assigned.set(imageId, block.id));
    }
    if (block.type === "videos") {
      (block.video_ids || []).forEach((videoId) => assignedVideos.set(videoId, block.id));
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
    } else if (block.type === "videos") {
      const picker = document.createElement("div");
      picker.className = "image-picker";
      state.videos.forEach((video, videoIndex) => {
        const label = document.createElement("label");
        label.className = "image-choice";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = block.video_ids.includes(video.id);
        checkbox.disabled = assignedVideos.has(video.id) && assignedVideos.get(video.id) !== block.id;
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            if (block.video_ids.length >= 5) {
              checkbox.checked = false;
              showToast("1ブロックは動画5本までです", "error");
              return;
            }
            block.video_ids.push(video.id);
          } else {
            block.video_ids = block.video_ids.filter((videoId) => videoId !== video.id);
          }
          renderBlocks();
          markDirty();
        });
        const text = document.createElement("span");
        text.textContent = `${videoIndex + 1}. ${video.label || "元記事の動画"}`;
        label.append(checkbox, text);
        picker.append(label);
      });
      if (state.videos.length === 0) {
        const empty = document.createElement("span");
        empty.textContent = "動画なし";
        picker.append(empty);
      }
      body.append(picker);
    } else if (block.type === "x_embed") {
      const summary = document.createElement("div");
      summary.className = "x-block-summary";
      const badge = document.createElement("span");
      badge.className = "x-block-badge";
      badge.textContent = "公式埋め込み";
      const author = document.createElement("strong");
      author.textContent = `${block.author_name}（@${block.username}）`;
      const text = document.createElement("p");
      text.textContent = block.text;
      const link = document.createElement("a");
      link.href = block.post_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Xで投稿を確認";
      summary.append(badge, author, text, link);
      body.append(summary);
    } else if (block.type === "x_timeline") {
      const summary = document.createElement("div");
      summary.className = "x-block-summary";
      const badge = document.createElement("span");
      badge.className = "x-block-badge";
      badge.textContent = "公式タイムライン";
      const author = document.createElement("strong");
      author.textContent = `@${block.username}`;
      const text = document.createElement("p");
      text.textContent = `最新${block.limit}件を表示`;
      const link = document.createElement("a");
      link.href = block.profile_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Xでプロフィールを確認";
      summary.append(badge, author, text, link);
      body.append(summary);
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
  if (type === "videos") state.blocks.push(newVideoBlock());
  if (type === "separator") state.blocks.push({ id: uid("separator"), type: "separator" });
  if (type === "ad") state.blocks.push({ id: uid("ad"), type: "ad", text: "関連広告枠" });
  renderBlocks();
  markDirty();
  elements.blockList.scrollTop = elements.blockList.scrollHeight;
}

function autoArrange() {
  if (state.blocks.some((block) => ["x_embed", "x_timeline"].includes(block.type))) {
    showToast("X投稿を含む記事はすでに自動構成されています", "error");
    return;
  }
  if (state.images.length === 0 && state.videos.length === 0) {
    showToast("先に画像か動画を追加してください", "error");
    return;
  }
  const posts = state.blocks.filter((block) => block.type === "post");
  if (posts.length === 0) posts.push(newPost());
  const mediaBlocks = [];
  if (state.videos.length > 0) {
    mediaBlocks.push(newVideoBlock(state.videos.map((video) => video.id)));
  } else {
    for (let index = 0; index < state.images.length; index += 2) {
      mediaBlocks.push(newImageBlock(state.images.slice(index, index + 2).map((image) => image.id)));
    }
  }
  const arranged = [];
  arranged.push(posts[0]);
  mediaBlocks.forEach((mediaBlock, index) => {
    arranged.push(mediaBlock);
    const followingPost = posts[index + 1];
    if (followingPost) {
      arranged.push(followingPost);
    } else if (index < mediaBlocks.length - 1) {
      arranged.push(newPost());
    }
    if (index === 0 && mediaBlocks.length > 1) {
      arranged.push({ id: uid("separator"), type: "separator" });
    }
  });
  posts.slice(mediaBlocks.length + 1).forEach((post) => arranged.push(post));
  arranged.push({ id: uid("ad"), type: "ad", text: "記事内容に合う関連広告枠" });
  state.blocks = arranged;
  renderBlocks();
  markDirty();
  showToast("画像と動画を記事内へ配置しました", "success");
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

function formatXDate(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "日時不明";
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

function chooseFirstAvailableXCover() {
  const selectedIds = new Set(Array.from(elements.xPostList.querySelectorAll(".x-post-checkbox:checked"), (input) => input.value));
  const current = elements.xPostList.querySelector('input[name="x-cover"]:checked');
  if (current && selectedIds.has(current.dataset.postId)) return;
  const next = Array.from(elements.xPostList.querySelectorAll('input[name="x-cover"]'))
    .find((input) => selectedIds.has(input.dataset.postId));
  if (next) next.checked = true;
}

function renderXResults() {
  const account = state.xAccount;
  elements.xAccountSummary.replaceChildren();
  if (account.profile_image_url) {
    const avatar = document.createElement("img");
    avatar.src = `/api/x/avatar/${encodeURIComponent(state.xSession)}`;
    avatar.alt = `${account.name}のプロフィール画像`;
    elements.xAccountSummary.append(avatar);
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "x-account-avatar-placeholder";
    placeholder.textContent = "X";
    elements.xAccountSummary.append(placeholder);
  }
  const main = document.createElement("div");
  main.className = "x-account-main";
  const name = document.createElement("strong");
  name.textContent = account.name;
  const handle = document.createElement("span");
  handle.textContent = `@${account.username}`;
  const description = document.createElement("p");
  description.textContent = account.description || "プロフィール文なし";
  main.append(name, handle, description);
  const metric = document.createElement("div");
  metric.className = "x-account-metric";
  metric.textContent = `${Number(account.followers_count || 0).toLocaleString("ja-JP")} フォロワー`;
  elements.xAccountSummary.append(main, metric);

  elements.xPostList.replaceChildren();
  state.xPosts.forEach((post, postIndex) => {
    const item = document.createElement("article");
    item.className = "x-post-candidate";
    item.dataset.xPostId = post.id;

    const selectCell = document.createElement("div");
    selectCell.className = "x-post-select";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "x-post-checkbox";
    checkbox.value = post.id;
    checkbox.checked = postIndex < 3;
    checkbox.setAttribute("aria-label", `${postIndex + 1}件目の投稿を記事に使う`);
    checkbox.addEventListener("change", chooseFirstAvailableXCover);
    selectCell.append(checkbox);

    const copy = document.createElement("div");
    copy.className = "x-post-copy";
    const text = document.createElement("p");
    text.textContent = post.text;
    const meta = document.createElement("div");
    meta.className = "x-post-meta";
    [
      formatXDate(post.created_at),
      `いいね ${Number(post.metrics.like_count || 0).toLocaleString("ja-JP")}`,
      `リポスト ${Number(post.metrics.retweet_count || 0).toLocaleString("ja-JP")}`,
      `返信 ${Number(post.metrics.reply_count || 0).toLocaleString("ja-JP")}`,
    ].forEach((value) => {
      const span = document.createElement("span");
      span.textContent = value;
      meta.append(span);
    });
    if (post.possibly_sensitive) {
      const sensitive = document.createElement("span");
      sensitive.className = "x-sensitive";
      sensitive.textContent = "センシティブ設定あり";
      meta.append(sensitive);
    }
    copy.append(text, meta);

    const mediaList = document.createElement("div");
    mediaList.className = "x-media-list";
    post.media.forEach((media, mediaIndex) => {
      const choice = document.createElement("label");
      choice.className = "x-media-choice";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "x-cover";
      radio.value = media.media_key;
      radio.dataset.postId = post.id;
      radio.checked = postIndex === 0 && mediaIndex === 0;
      radio.setAttribute("aria-label", "この記事の一覧画像にする");
      radio.addEventListener("change", () => {
        if (radio.checked) checkbox.checked = true;
      });
      const image = document.createElement("img");
      image.src = `/api/x/media/${encodeURIComponent(state.xSession)}/${encodeURIComponent(media.media_key)}`;
      image.alt = media.alt_text || `${account.name}の投稿画像`;
      image.loading = "lazy";
      const label = document.createElement("span");
      label.textContent = "一覧画像";
      choice.append(radio, image, label);
      mediaList.append(choice);
    });

    item.append(selectCell, copy, mediaList);
    elements.xPostList.append(item);
  });
  elements.xPostCount.textContent = `${state.xPosts.length}件`;
  elements.xResults.hidden = false;
}

function setXImportMode(mode) {
  const free = mode === "free";
  elements.xFreePanel.hidden = !free;
  elements.xApiPanel.hidden = free;
  elements.xFreeModeButton.classList.toggle("active", free);
  elements.xApiModeButton.classList.toggle("active", !free);
  elements.xFreeModeButton.setAttribute("aria-selected", String(free));
  elements.xApiModeButton.setAttribute("aria-selected", String(!free));
  if (elements.xImportDialog.open) {
    (free ? elements.xPostUrlsInput : elements.xUsernameInput).focus();
  }
}

async function selectXFreeCover() {
  const file = elements.xCoverInput.files[0];
  if (!file) {
    state.xFreeCover = null;
    elements.xCoverPreview.replaceChildren(Object.assign(document.createElement("span"), { textContent: "画像未選択" }));
    return;
  }
  setBusy(true);
  try {
    if (file.size > 12 * 1024 * 1024) throw new Error(`${file.name} は12MBを超えています`);
    const dataUrl = await readFileAsDataUrl(file);
    const dimensions = await inspectImage(dataUrl);
    state.xFreeCover = {
      id: "x-cover",
      name: file.name,
      data_url: dataUrl,
      alt: altFromName(file.name),
      orientation: dimensions.width >= dimensions.height ? "landscape" : "portrait",
    };
    const preview = document.createElement("img");
    preview.src = dataUrl;
    preview.alt = "選択した一覧画像";
    elements.xCoverPreview.replaceChildren(preview);
  } catch (error) {
    state.xFreeCover = null;
    elements.xCoverInput.value = "";
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function buildXFreeDraft() {
  const postUrls = elements.xPostUrlsInput.value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (postUrls.length < 1 || postUrls.length > 6) {
    showToast("XプロフィールURLを1件、または投稿URLを1件から6件入力してください", "error");
    elements.xPostUrlsInput.focus();
    return;
  }
  if (!state.xFreeCover) {
    showToast("一覧に使う本人画像を選んでください", "error");
    elements.xCoverInput.focus();
    return;
  }
  if (state.dirty && !window.confirm("編集中の記事を破棄してXの下書きを作成しますか？")) return;
  setBusy(true);
  try {
    const result = await apiJson("/api/x/free-draft", {
      method: "POST",
      body: { post_urls: postUrls, cover_image: state.xFreeCover },
    });
    applyPayload(result.payload);
    state.dirty = true;
    elements.saveState.textContent = "X無料モード・未保存";
    elements.xImportDialog.close();
    showToast("X投稿URLから記事の下書きを作成しました", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function openXImporter() {
  elements.xTokenHelp.textContent = state.xTokenConfigured
    ? "環境変数 X_BEARER_TOKEN を使用できます。入力したTokenは保存しません。"
    : "Tokenはブラウザーや下書きへ保存せず、今回のX公式API通信だけに使用します。";
  setXImportMode("free");
  elements.xImportDialog.showModal();
  elements.xPostUrlsInput.focus();
}

async function fetchXAccount() {
  const username = elements.xUsernameInput.value.trim();
  const bearerToken = elements.xTokenInput.value.trim();
  if (!username) {
    elements.xUsernameInput.focus();
    showToast("Xのユーザー名を入力してください", "error");
    return;
  }
  if (!bearerToken && !state.xTokenConfigured) {
    elements.xTokenInput.focus();
    showToast("X API Bearer Tokenを入力してください", "error");
    return;
  }
  setBusy(true);
  try {
    const result = await apiJson("/api/x/account", {
      method: "POST",
      body: { username, bearer_token: bearerToken },
    });
    state.xSession = result.session_id;
    state.xAccount = result.account;
    state.xPosts = result.posts;
    elements.xTokenInput.value = "";
    renderXResults();
    showToast(`${result.posts.length}件の画像付き投稿を取得しました`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function buildXDraft() {
  const selectedPostIds = Array.from(elements.xPostList.querySelectorAll(".x-post-checkbox:checked"), (input) => input.value);
  const cover = elements.xPostList.querySelector('input[name="x-cover"]:checked');
  if (selectedPostIds.length < 1 || selectedPostIds.length > 6) {
    showToast("記事に使う投稿を1件から6件選んでください", "error");
    return;
  }
  if (!cover || !selectedPostIds.includes(cover.dataset.postId)) {
    showToast("選択した投稿の画像を一覧画像に指定してください", "error");
    return;
  }
  if (state.dirty && !window.confirm("編集中の記事を破棄してXの下書きを作成しますか？")) return;
  setBusy(true);
  try {
    const result = await apiJson("/api/x/draft", {
      method: "POST",
      body: {
        session_id: state.xSession,
        selected_post_ids: selectedPostIds,
        cover_media_key: cover.value,
      },
    });
    applyPayload(result.payload);
    state.dirty = true;
    elements.saveState.textContent = "Xから作成・未保存";
    elements.xImportDialog.close();
    showToast("X投稿から記事の下書きを作成しました", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
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
  renderDraftViews(drafts);
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
  state.videos = Array.isArray(payload.videos) ? payload.videos.map((video) => ({ ...video })) : [];
  state.blocks = Array.isArray(payload.blocks) ? payload.blocks.map((block) => ({
    ...block,
    id: block.id || uid(block.type || "block"),
    ...(block.image_ids ? { image_ids: block.image_ids.slice() } : {}),
    ...(block.video_ids ? { video_ids: block.video_ids.slice() } : {}),
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
  state.editorialStatus = payload.editorial_status || "draft";
  state.rightsStatus = payload.rights_status || (payload.rights_confirmed ? "confirmed" : "unconfirmed");
  state.rightsContact = payload.rights_contact || "";
  state.rightsNote = payload.rights_note || "";
  state.dirty = false;
  elements.saveState.textContent = "下書きを開きました";
  elements.editorArticleTitle.textContent = payload.title || "新規記事";
  elements.previewFrame.removeAttribute("srcdoc");
  elements.previewEmpty.hidden = false;
  renderImages();
  renderVideos();
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
    showView("editor");
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
    "imageList", "videoCount", "videoList", "autoArrangeButton", "blockList", "desktopPreviewButton", "mobilePreviewButton",
    "refreshPreviewButton", "previewStage", "previewFrame", "previewEmpty", "adultConfirmed",
    "rightsConfirmed", "privacyConfirmed", "sourceConfirmed", "saveDraftButton", "downloadPackageButton",
    "addToSiteButton", "confirmDialog", "confirmMessage", "xImportButton", "xImportDialog", "xCloseButton",
    "xFreeModeButton", "xApiModeButton", "xFreePanel", "xApiPanel", "xPostUrlsInput", "xCoverInput",
    "xCoverPreview", "xFreeBuildButton",
    "xUsernameInput", "xTokenInput", "xTokenHelp", "xFetchButton", "xResults", "xAccountSummary",
    "xPostCount", "xPostList", "xBuildButton", "toast",
    "viewEyebrow", "viewTitle", "editorDraftTools", "sourceView", "draftsView", "rightsView", "editorView",
    "futureView", "futureTitle", "draftNavCount", "sourceAnalyzerInput", "sourcePasteButton",
    "sourceAnalyzeButton", "sourceProgress", "sourceResult", "sourceTypeBadge", "sourceSiteName",
    "sourceOpenLink", "sourceResultTitle", "sourceResultDescription", "sourceAnalysisLabel", "sourceImageMetric", "sourceVideoMetric",
    "sourceTextMetric", "sourceSelectedCount", "sourceSelectAllButton", "sourceClearImagesButton",
    "sourceExcludedToggleButton", "sourceImageGrid", "sourceVideoGrid", "sourceFallbackBox", "sourceFallbackInput", "sourceFallbackPreview",
    "sourceToneInput", "sourceCategoryInput", "sourceReplyCountInput", "sourceGenerateButton",
    "codexStatus", "codexStatusTitle", "codexStatusDetail", "generationJob", "generationJobTitle",
    "generationJobProgress", "generationProgressBar", "generationJobMessage", "generationRetryButton",
    "recentDrafts", "draftsList", "draftTotalCount", "draftRightsCount", "publishedCount",
    "rightsNavCount", "rightsList", "rightsUnconfirmedCount", "rightsRequestedCount",
    "rightsConfirmedCount", "rightsRejectedCount", "editorArticleTitle",
  ].forEach((id) => {
    elements[id] = document.getElementById(id);
  });
}

function bindEvents() {
  elements.sourceAnalyzeButton.addEventListener("click", () => analyzeSource());
  elements.sourcePasteButton.addEventListener("click", pasteSourceUrl);
  elements.sourceAnalyzerInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      analyzeSource();
    }
  });
  elements.sourceSelectAllButton.addEventListener("click", () => {
    const visibleIds = Array.from(elements.sourceImageGrid.querySelectorAll(".source-image-choice"), (choice) => choice.dataset.imageId);
    state.sourceSelectedIds = new Set(visibleIds.slice(0, 10));
    const visibleVideoIds = Array.from(elements.sourceVideoGrid.querySelectorAll(".source-video-choice"), (choice) => choice.dataset.videoId);
    state.sourceSelectedVideoIds = new Set(visibleVideoIds.slice(0, 5));
    renderSourceImages();
  });
  elements.sourceClearImagesButton.addEventListener("click", () => {
    state.sourceSelectedIds.clear();
    state.sourceSelectedVideoIds.clear();
    renderSourceImages();
  });
  elements.sourceExcludedToggleButton.addEventListener("click", () => {
    state.showExcludedImages = !state.showExcludedImages;
    renderSourceImages();
  });
  elements.sourceFallbackInput.addEventListener("change", selectSourceFallback);
  elements.sourceGenerateButton.addEventListener("click", buildSourceDraft);
  elements.generationRetryButton.addEventListener("click", buildSourceDraft);
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  document.querySelectorAll("[data-future]").forEach((button) => {
    button.addEventListener("click", () => showView("future", button.dataset.title));
  });
  document.querySelectorAll("[data-open-drafts]").forEach((button) => {
    button.addEventListener("click", () => showView("drafts"));
  });
  document.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
  elements.xImportButton.addEventListener("click", openXImporter);
  elements.xCloseButton.addEventListener("click", () => elements.xImportDialog.close());
  elements.xFreeModeButton.addEventListener("click", () => setXImportMode("free"));
  elements.xApiModeButton.addEventListener("click", () => setXImportMode("api"));
  elements.xCoverInput.addEventListener("change", selectXFreeCover);
  elements.xFreeBuildButton.addEventListener("click", buildXFreeDraft);
  elements.xFetchButton.addEventListener("click", fetchXAccount);
  elements.xBuildButton.addEventListener("click", buildXDraft);
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
    showView("editor");
  });
  elements.slugInput.addEventListener("input", updateExistingState);
  document.querySelectorAll("input, textarea, select").forEach((control) => {
    if (![elements.draftSelect, elements.imageInput, elements.xUsernameInput, elements.xTokenInput,
      elements.xPostUrlsInput, elements.xCoverInput, elements.sourceAnalyzerInput, elements.sourceFallbackInput,
      elements.sourceToneInput, elements.sourceCategoryInput, elements.sourceReplyCountInput].includes(control)) {
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
    state.xTokenConfigured = Boolean(bootstrap.x_token_configured);
    state.codex = bootstrap.codex || state.codex;
    state.jobs = Array.isArray(bootstrap.jobs) ? bootstrap.jobs : [];
    renderCodexStatus();
    const activeJob = state.jobs.find((job) => ["queued", "running"].includes(job.status));
    const latestJob = activeJob || state.jobs[0];
    if (latestJob) renderGenerationJob(latestJob);
    if (activeJob) {
      state.activeJobId = activeJob.id;
      state.generationRunning = true;
      updateGenerateAvailability();
      pollGenerationJob(activeJob.id);
    }
    elements.publishedCount.textContent = String(bootstrap.articles.filter((article) => article.status === "published").length);
    bootstrap.categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category;
      elements.categoryList.append(option);
    });
    refreshDraftSelect(bootstrap.drafts);
    updateExistingState();
    showView("source");
  } catch (error) {
    showToast(error.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", initialize);
