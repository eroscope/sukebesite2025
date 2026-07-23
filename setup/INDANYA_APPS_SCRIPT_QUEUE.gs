const INDANYA_CLIENT_KEY = "V_HDntDXDk5UmtljnKiTI3n2-grJuLvX";
const INDANYA_WORKER_KEY = "AeugyJTkfhQW7HnUeyXROo9EcCJyNcSt";
const INDANYA_JOB_FOLDER = "INDANYA_CHATGPT_JOBS";
const INDANYA_MAX_WAIT_SECONDS = 35;

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return jsonResponse({
    ok: true,
    service: "indanya-chatgpt-bridge",
    version: "1.0.0",
    message: "ready"
  });
}

function doPost(e) {
  try {
    const body = e && e.postData && e.postData.contents
      ? JSON.parse(e.postData.contents)
      : {};
    const action = String(body.action || "").trim();

    if (action === "health") {
      return jsonResponse({ ok: true, service: "indanya-chatgpt-bridge", message: "ready" });
    }

    if (action.indexOf("worker_") === 0) {
      requireWorkerKey(body.worker_key);
      return jsonResponse(handleWorkerAction(action, body));
    }

    requireClientKey(body.client_key);

    if (action === "capture_article") {
      const sourceUrl = String(body.url || "").trim();
      if (!/^https?:\/\//i.test(sourceUrl)) {
        throw new Error("正しい記事URLを指定してください");
      }
      const job = createJob("capture", { url: sourceUrl });
      return jsonResponse(waitForJob(job.id, body.wait_seconds));
    }

    if (action === "create_draft") {
      if (!body.capture_job_id || !body.article) {
        throw new Error("capture_job_idとarticleが必要です");
      }
      const job = createJob("draft", {
        capture_job_id: String(body.capture_job_id),
        article: body.article
      });
      return jsonResponse(waitForJob(job.id, body.wait_seconds));
    }

    if (action === "publish_article") {
      if (!body.capture_job_id || !body.article) {
        throw new Error("capture_job_idとarticleが必要です");
      }
      const job = createJob("publish", {
        capture_job_id: String(body.capture_job_id),
        article: body.article,
        rights_confirmed: body.rights_confirmed === true
      });
      return jsonResponse(waitForJob(job.id, body.wait_seconds));
    }

    if (action === "get_job") {
      const jobId = String(body.job_id || "").trim();
      if (!jobId) throw new Error("job_idが必要です");
      return jsonResponse(publicJob(readJob(jobId)));
    }

    throw new Error("未対応のactionです: " + action);
  } catch (error) {
    return jsonResponse({
      ok: false,
      error: String(error && error.stack ? error.stack : error)
    });
  }
}

function handleWorkerAction(action, body) {
  if (action === "worker_claim") {
    return claimNextJob(String(body.worker_name || "windows-pc"));
  }

  if (action === "worker_progress") {
    const job = readJob(String(body.job_id || ""));
    job.status = "processing";
    job.progress = clampNumber(body.progress, 0, 100);
    job.message = String(body.message || "処理中").slice(0, 500);
    job.updated_at = nowIso();
    saveJob(job);
    return { ok: true, job_id: job.id };
  }

  if (action === "worker_complete") {
    const job = readJob(String(body.job_id || ""));
    job.status = "completed";
    job.progress = 100;
    job.message = String(body.message || "完了").slice(0, 500);
    job.result = body.result || {};
    job.updated_at = nowIso();
    job.completed_at = nowIso();
    saveJob(job);
    return { ok: true, job_id: job.id };
  }

  if (action === "worker_fail") {
    const job = readJob(String(body.job_id || ""));
    job.status = "failed";
    job.message = "失敗";
    job.error = String(body.error || "不明なエラー").slice(0, 12000);
    job.updated_at = nowIso();
    job.completed_at = nowIso();
    saveJob(job);
    return { ok: true, job_id: job.id };
  }

  throw new Error("未対応のworker actionです: " + action);
}

function requireClientKey(value) {
  if (String(value || "") !== INDANYA_CLIENT_KEY) {
    throw new Error("client_keyが正しくありません");
  }
}

function requireWorkerKey(value) {
  if (String(value || "") !== INDANYA_WORKER_KEY) {
    throw new Error("worker_keyが正しくありません");
  }
}

function createJob(type, payload) {
  const job = {
    id: Utilities.getUuid().replace(/-/g, ""),
    type: type,
    status: "queued",
    progress: 0,
    message: "PCの処理待ち",
    payload: payload,
    result: null,
    error: "",
    worker_name: "",
    created_at: nowIso(),
    updated_at: nowIso()
  };
  saveJob(job);
  return job;
}

function waitForJob(jobId, requestedSeconds) {
  const seconds = Math.max(
    0,
    Math.min(INDANYA_MAX_WAIT_SECONDS, Number(requestedSeconds || INDANYA_MAX_WAIT_SECONDS))
  );
  const deadline = Date.now() + seconds * 1000;
  let job = readJob(jobId);

  while (Date.now() < deadline && job.status !== "completed" && job.status !== "failed") {
    Utilities.sleep(1000);
    job = readJob(jobId);
  }
  return publicJob(job);
}

function publicJob(job) {
  return {
    ok: job.status !== "failed",
    job_id: job.id,
    job_type: job.type,
    status: job.status,
    progress: Number(job.progress || 0),
    message: String(job.message || ""),
    result: job.status === "completed" ? (job.result || {}) : null,
    error: job.status === "failed" ? String(job.error || "") : "",
    created_at: job.created_at,
    updated_at: job.updated_at
  };
}

function claimNextJob(workerName) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const folder = getJobFolder();
    const files = folder.getFiles();
    let selected = null;

    while (files.hasNext()) {
      const file = files.next();
      if (!/\.json$/i.test(file.getName())) continue;
      try {
        const job = JSON.parse(file.getBlob().getDataAsString("UTF-8"));
        if (job.status !== "queued") continue;
        if (!selected || String(job.created_at) < String(selected.created_at)) {
          selected = job;
        }
      } catch (ignore) {
        // 壊れたファイルは無視
      }
    }

    if (!selected) {
      return { ok: true, job: null };
    }

    selected.status = "processing";
    selected.progress = 1;
    selected.message = "PCが処理を開始しました";
    selected.worker_name = workerName;
    selected.updated_at = nowIso();
    saveJob(selected);
    return { ok: true, job: selected };
  } finally {
    lock.releaseLock();
  }
}

function getJobFolder() {
  const properties = PropertiesService.getScriptProperties();
  const savedId = properties.getProperty("INDANYA_JOB_FOLDER_ID");
  if (savedId) {
    try {
      return DriveApp.getFolderById(savedId);
    } catch (ignore) {
      properties.deleteProperty("INDANYA_JOB_FOLDER_ID");
    }
  }

  const folders = DriveApp.getFoldersByName(INDANYA_JOB_FOLDER);
  const folder = folders.hasNext() ? folders.next() : DriveApp.createFolder(INDANYA_JOB_FOLDER);
  properties.setProperty("INDANYA_JOB_FOLDER_ID", folder.getId());
  return folder;
}

function jobFileName(jobId) {
  return "job-" + String(jobId) + ".json";
}

function saveJob(job) {
  const folder = getJobFolder();
  const name = jobFileName(job.id);
  const content = JSON.stringify(job);
  const files = folder.getFilesByName(name);
  if (files.hasNext()) {
    files.next().setContent(content);
  } else {
    folder.createFile(name, content, MimeType.PLAIN_TEXT);
  }
}

function readJob(jobId) {
  if (!jobId) throw new Error("job_idが必要です");
  const files = getJobFolder().getFilesByName(jobFileName(jobId));
  if (!files.hasNext()) throw new Error("ジョブが見つかりません: " + jobId);
  return JSON.parse(files.next().getBlob().getDataAsString("UTF-8"));
}

function nowIso() {
  return new Date().toISOString();
}

function clampNumber(value, minimum, maximum) {
  const number = Number(value || 0);
  return Math.max(minimum, Math.min(maximum, isFinite(number) ? number : 0));
}
