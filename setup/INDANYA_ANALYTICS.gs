const INDANYA_ANALYTICS_TOKEN = "indanya-analytics-20260803-v1";
const INDANYA_ANALYTICS_READ_KEY = "7hF9uN2sK4vQ8xC1mR6bT3zW5pL0dYgA";
const INDANYA_ANALYTICS_SITE = "eroscope.github.io/sukebesite2025";

// V3 keeps one pre-aggregated file per day.  A report reads only the requested
// dates; it never enumerates Script Properties or replays individual events.
const INDANYA_ANALYTICS_PREFIX = "INDANYA_ANALYTICS_V3_";

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return jsonResponse({ ok: true, service: "indanya-analytics", version: "3.0.0", message: "ready" });
}

function doPost(e) {
  try {
    const body = e && e.postData && e.postData.contents ? JSON.parse(e.postData.contents) : {};
    const action = String(body.action || "").trim();
    if (action === "analytics_event") return jsonResponse(recordAnalyticsEvent(body));
    if (action === "analytics_summary") {
      requireReadKey(body.read_key);
      return jsonResponse(analyticsSummary(body.days, body.include_owner, body.known_revision));
    }
    if (action === "analytics_owner_config") {
      requireReadKey(body.read_key);
      return jsonResponse(configureAnalyticsOwner(body));
    }
    throw new Error("unsupported action: " + action);
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error && error.stack ? error.stack : error) });
  }
}

function authorizeAnalytics() {
  PropertiesService.getScriptProperties().setProperty(INDANYA_ANALYTICS_PREFIX + "READY", new Date().toISOString());
  analyticsFolder(PropertiesService.getScriptProperties());
  return "ready";
}

function requireReadKey(value) {
  if (String(value || "") !== INDANYA_ANALYTICS_READ_KEY) throw new Error("read key is invalid");
}

function cleanAnalyticsValue(value, limit) {
  return String(value || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, limit || 240);
}

function analyticsHash(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(value || ""), Utilities.Charset.UTF_8)
    .slice(0, 10).map(function(byte) { return (byte + 256).toString(16).slice(-2); }).join("");
}

function analyticsDayKey(date) {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

function analyticsDate(dayKey) {
  return dayKey.slice(0, 4) + "-" + dayKey.slice(4, 6) + "-" + dayKey.slice(6, 8);
}

function analyticsRead(properties, key, fallback) {
  try { return JSON.parse(properties.getProperty(key) || "") || fallback; } catch (ignore) { return fallback; }
}

function analyticsMetricKey(eventType) {
  return eventType === "page_view" ? "page_views" : eventType === "pr_impression" ? "pr_impressions" : "pr_clicks";
}

function analyticsIncrement(value, eventType, isOwner) {
  const metric = analyticsMetricKey(eventType);
  value[metric] = Number(value[metric] || 0) + 1;
  if (isOwner) value["owner_" + metric] = Number(value["owner_" + metric] || 0) + 1;
}

function analyticsFolder(properties) {
  const folderKey = INDANYA_ANALYTICS_PREFIX + "FOLDER_ID";
  const currentId = String(properties.getProperty(folderKey) || "");
  if (currentId) {
    try { return DriveApp.getFolderById(currentId); } catch (ignore) {}
  }
  const folder = DriveApp.createFolder("INDANYA analytics aggregates V3");
  properties.setProperty(folderKey, folder.getId());
  return folder;
}

function analyticsDayFileKey(dayKey) {
  return INDANYA_ANALYTICS_PREFIX + "DAY_FILE_" + dayKey;
}

function emptyAnalyticsDay(dayKey) {
  return {
    schema_version: 3,
    date: analyticsDate(dayKey),
    totals: { page_views: 0, pr_impressions: 0, pr_clicks: 0, owner_page_views: 0, owner_pr_impressions: 0, owner_pr_clicks: 0 },
    sessions: {}, owner_sessions: {}, articles: {}, categories: {}, promotions: {}, visitors: {}, devices: {}, referrers: {}
  };
}

function readAnalyticsDay(properties, dayKey) {
  const fileId = String(properties.getProperty(analyticsDayFileKey(dayKey)) || "");
  if (!fileId) return emptyAnalyticsDay(dayKey);
  try {
    const text = DriveApp.getFileById(fileId).getBlob().getDataAsString("UTF-8");
    const value = JSON.parse(text);
    return value && value.schema_version === 3 ? value : emptyAnalyticsDay(dayKey);
  } catch (ignore) {
    return emptyAnalyticsDay(dayKey);
  }
}

function writeAnalyticsDay(properties, dayKey, value) {
  const fileKey = analyticsDayFileKey(dayKey);
  const text = JSON.stringify(value);
  const fileId = String(properties.getProperty(fileKey) || "");
  if (fileId) {
    try {
      DriveApp.getFileById(fileId).setContent(text);
      return;
    } catch (ignore) {}
  }
  const file = analyticsFolder(properties).createFile("analytics-" + dayKey + ".json", text, MimeType.PLAIN_TEXT);
  properties.setProperty(fileKey, file.getId());
}

function pruneAnalyticsDay(properties, today) {
  const old = new Date(today.getTime());
  old.setUTCDate(old.getUTCDate() - 181);
  const key = analyticsDayFileKey(analyticsDayKey(old));
  const fileId = String(properties.getProperty(key) || "");
  if (!fileId) return;
  try { DriveApp.getFileById(fileId).setTrashed(true); } catch (ignore) {}
  properties.deleteProperty(key);
}

function analyticsVisitorNumber(properties, visitorId) {
  const aliasKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_" + analyticsHash(visitorId);
  let number = Number(properties.getProperty(aliasKey) || 0);
  if (number > 0) return number;
  const sequenceKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_SEQUENCE";
  number = Number(properties.getProperty(sequenceKey) || 0) + 1;
  properties.setProperty(sequenceKey, String(number));
  properties.setProperty(aliasKey, String(number));
  return number;
}

function analyticsVisitorLabel(number) {
  return "訪問者 #" + String(Math.max(1, Number(number || 1))).padStart(4, "0");
}

function configureAnalyticsOwner(body) {
  const token = cleanAnalyticsValue(body.owner_token, 160);
  if (!/^[A-Za-z0-9_-]{24,}$/.test(token)) throw new Error("owner token is invalid");
  PropertiesService.getScriptProperties().setProperty(INDANYA_ANALYTICS_PREFIX + "OWNER_TOKEN", token);
  return { ok: true };
}

function isOwnerAnalyticsEvent(properties, body) {
  const configured = String(properties.getProperty(INDANYA_ANALYTICS_PREFIX + "OWNER_TOKEN") || "");
  return configured.length >= 24 && cleanAnalyticsValue(body.owner_token, 160) === configured;
}

function analyticsCounter() {
  return { page_views: 0, pr_impressions: 0, pr_clicks: 0, owner_page_views: 0, owner_pr_impressions: 0, owner_pr_clicks: 0 };
}

function analyticsCounterItem(map, id, factory) {
  if (!map[id]) map[id] = factory();
  return map[id];
}

function incrementBreakdown(value, key, isOwner) {
  value[key] = Number(value[key] || 0) + 1;
  if (isOwner) value["owner_" + key] = Number(value["owner_" + key] || 0) + 1;
}

function recordAnalyticsEvent(body) {
  if (cleanAnalyticsValue(body.token, 80) !== INDANYA_ANALYTICS_TOKEN) throw new Error("analytics token is invalid");
  const eventType = cleanAnalyticsValue(body.event_type, 40);
  if (["page_view", "pr_impression", "pr_click"].indexOf(eventType) < 0) throw new Error("analytics event type is invalid");
  const site = cleanAnalyticsValue(body.site, 180);
  if (site.indexOf(INDANYA_ANALYTICS_SITE) !== 0) throw new Error("analytics site is invalid");

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const now = new Date();
    const dayKey = analyticsDayKey(now);
    const day = readAnalyticsDay(properties, dayKey);
    const sessionId = cleanAnalyticsValue(body.session_id, 100);
    const visitorId = cleanAnalyticsValue(body.visitor_id, 120) || sessionId || analyticsHash(now.toISOString());
    const visitorNo = analyticsVisitorNumber(properties, visitorId);
    const isOwner = isOwnerAnalyticsEvent(properties, body);
    const device = cleanAnalyticsValue(body.device_type, 40) || "other";
    const browser = cleanAnalyticsValue(body.browser_family, 40) || "unknown";
    const referrer = cleanAnalyticsValue(body.referrer_host, 160) || "direct";
    const slug = cleanAnalyticsValue(body.article_slug, 140) || "unknown";
    const title = cleanAnalyticsValue(body.article_title, 240) || slug;
    const category = cleanAnalyticsValue(body.category, 100) || "uncategorized";

    analyticsIncrement(day.totals, eventType, isOwner);
    if (sessionId) {
      day.sessions[analyticsHash(sessionId)] = true;
      if (isOwner) day.owner_sessions[analyticsHash(sessionId)] = true;
    }
    const article = analyticsCounterItem(day.articles, slug, function() {
      return Object.assign({ slug: slug, title: title, category: category }, analyticsCounter());
    });
    analyticsIncrement(article, eventType, isOwner);
    const categoryRow = analyticsCounterItem(day.categories, category, function() {
      return Object.assign({ category: category }, analyticsCounter());
    });
    analyticsIncrement(categoryRow, eventType, isOwner);
    const visitor = analyticsCounterItem(day.visitors, String(visitorNo), function() {
      return Object.assign({ visitor_no: visitorNo, devices: {}, browsers: {}, owner_devices: {}, owner_browsers: {}, is_owner: false, last_seen: "" }, analyticsCounter());
    });
    analyticsIncrement(visitor, eventType, isOwner);
    visitor.is_owner = Boolean(visitor.is_owner || isOwner);
    incrementBreakdown(visitor.devices, device, false);
    incrementBreakdown(visitor.browsers, browser, false);
    if (isOwner) {
      incrementBreakdown(visitor.owner_devices, device, false);
      incrementBreakdown(visitor.owner_browsers, browser, false);
    }
    visitor.last_seen = now.toISOString();
    const deviceRow = analyticsCounterItem(day.devices, device, function() {
      return Object.assign({ device: device }, analyticsCounter());
    });
    analyticsIncrement(deviceRow, eventType, isOwner);
    const referrerRow = analyticsCounterItem(day.referrers, referrer, function() {
      return Object.assign({ referrer: referrer }, analyticsCounter());
    });
    analyticsIncrement(referrerRow, eventType, isOwner);
    if (eventType !== "page_view") {
      const prTitle = cleanAnalyticsValue(body.pr_title, 240) || "Promotion";
      const destination = cleanAnalyticsValue(body.destination_host, 160);
      const promotionId = cleanAnalyticsValue(body.pr_id, 120) + "\n" + prTitle + "\n" + destination;
      const promotion = analyticsCounterItem(day.promotions, promotionId, function() {
        return { title: prTitle, destination_host: destination, impressions: 0, clicks: 0, owner_impressions: 0, owner_clicks: 0 };
      });
      if (eventType === "pr_impression") {
        promotion.impressions += 1;
        if (isOwner) promotion.owner_impressions += 1;
      } else {
        promotion.clicks += 1;
        if (isOwner) promotion.owner_clicks += 1;
      }
    }
    writeAnalyticsDay(properties, dayKey, day);
    const revisionKey = INDANYA_ANALYTICS_PREFIX + "REVISION";
    const revision = Number(properties.getProperty(revisionKey) || 0) + 1;
    properties.setProperties({
      [revisionKey]: String(revision),
      [INDANYA_ANALYTICS_PREFIX + "STARTED_AT"]: String(properties.getProperty(INDANYA_ANALYTICS_PREFIX + "STARTED_AT") || now.toISOString())
    });
    if (Math.random() < 0.02) pruneAnalyticsDay(properties, now);
    return { ok: true, revision: revision };
  } finally {
    lock.releaseLock();
  }
}

function mergeMetricValues(target, source) {
  ["page_views", "pr_impressions", "pr_clicks", "owner_page_views", "owner_pr_impressions", "owner_pr_clicks"].forEach(function(key) {
    target[key] = Number(target[key] || 0) + Number(source[key] || 0);
  });
}

function mergeBreakdowns(target, source) {
  Object.keys(source || {}).forEach(function(key) { target[key] = Number(target[key] || 0) + Number(source[key] || 0); });
}

function mergeNamedRows(target, source, key, factory) {
  Object.keys(source || {}).forEach(function(id) {
    const row = source[id];
    if (!target[id]) target[id] = factory(row);
    mergeMetricValues(target[id], row);
  });
}

function visibleMetric(item, metric, includeOwner) {
  return includeOwner ? Number(item[metric] || 0) : Math.max(0, Number(item[metric] || 0) - Number(item["owner_" + metric] || 0));
}

function rateRows(row) {
  row.click_rate = row.page_views ? Math.round(row.pr_clicks * 10000 / row.page_views) / 100 : 0;
  row.pr_ctr = row.pr_impressions ? Math.round(row.pr_clicks * 10000 / row.pr_impressions) / 100 : 0;
  return row;
}

function reportRow(row, includeOwner) {
  const output = Object.assign({}, row);
  ["page_views", "pr_impressions", "pr_clicks"].forEach(function(metric) { output[metric] = visibleMetric(row, metric, includeOwner); });
  return rateRows(output);
}

function analyticsSummary(requestedDays, includeOwner, knownRevision) {
  const days = Math.max(1, Math.min(180, Number(requestedDays || 30)));
  const includeOwnTraffic = includeOwner === true || String(includeOwner) === "true";
  const properties = PropertiesService.getScriptProperties();
  const revision = String(properties.getProperty(INDANYA_ANALYTICS_PREFIX + "REVISION") || "0");
  if (String(knownRevision || "") && String(knownRevision) === revision) {
    return { ok: true, schema_version: 3, days: days, revision: revision, unchanged: true, generated_at: new Date().toISOString() };
  }

  const articles = {}, categories = {}, promotions = {}, visitors = {}, devices = {}, referrers = {};
  const daily = [], visitorDaily = [], sessions = {}, ownerSessions = {};
  const allTotals = analyticsCounter();
  const now = new Date();
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const date = new Date(now.getTime());
    date.setUTCDate(date.getUTCDate() - offset);
    const day = readAnalyticsDay(properties, analyticsDayKey(date));
    mergeMetricValues(allTotals, day.totals);
    mergeBreakdowns(sessions, day.sessions);
    mergeBreakdowns(ownerSessions, day.owner_sessions);
    daily.push(Object.assign({ date: day.date }, reportRow(day.totals, includeOwnTraffic)));
    mergeNamedRows(articles, day.articles, "slug", function(row) { return Object.assign({ slug: row.slug, title: row.title, category: row.category }, analyticsCounter()); });
    mergeNamedRows(categories, day.categories, "category", function(row) { return Object.assign({ category: row.category }, analyticsCounter()); });
    mergeNamedRows(devices, day.devices, "device", function(row) { return Object.assign({ device: row.device }, analyticsCounter()); });
    mergeNamedRows(referrers, day.referrers, "referrer", function(row) { return Object.assign({ referrer: row.referrer }, analyticsCounter()); });
    Object.keys(day.promotions || {}).forEach(function(id) {
      const row = day.promotions[id];
      if (!promotions[id]) promotions[id] = { title: row.title, destination_host: row.destination_host, impressions: 0, clicks: 0, owner_impressions: 0, owner_clicks: 0 };
      ["impressions", "clicks", "owner_impressions", "owner_clicks"].forEach(function(key) { promotions[id][key] += Number(row[key] || 0); });
    });
    Object.keys(day.visitors || {}).forEach(function(id) {
      const row = day.visitors[id];
      if (!visitors[id]) visitors[id] = Object.assign({ visitor_no: row.visitor_no, devices: {}, browsers: {}, owner_devices: {}, owner_browsers: {}, is_owner: false, first_seen: day.date, last_seen: "" }, analyticsCounter());
      mergeMetricValues(visitors[id], row);
      mergeBreakdowns(visitors[id].devices, row.devices);
      mergeBreakdowns(visitors[id].browsers, row.browsers);
      mergeBreakdowns(visitors[id].owner_devices, row.owner_devices);
      mergeBreakdowns(visitors[id].owner_browsers, row.owner_browsers);
      visitors[id].is_owner = Boolean(visitors[id].is_owner || row.is_owner);
      if (String(row.last_seen || "") > String(visitors[id].last_seen || "")) visitors[id].last_seen = row.last_seen;
      const dayVisitor = reportRow(row, includeOwnTraffic);
      if (dayVisitor.page_views || dayVisitor.pr_impressions || dayVisitor.pr_clicks) {
        visitorDaily.push(Object.assign({ visitor: analyticsVisitorLabel(row.visitor_no), visitor_no: row.visitor_no, date: day.date, is_owner: Boolean(row.is_owner) }, dayVisitor));
      }
    });
  }

  function namedRows(values) {
    return Object.keys(values).map(function(key) { return reportRow(values[key], includeOwnTraffic); }).filter(function(row) {
      return row.page_views || row.pr_impressions || row.pr_clicks;
    }).sort(function(a, b) { return b.pr_clicks - a.pr_clicks || b.page_views - a.page_views; });
  }
  const visitorRows = Object.keys(visitors).map(function(id) {
    const raw = visitors[id];
    const row = reportRow(raw, includeOwnTraffic);
    const deviceCounts = {};
    const browserCounts = {};
    Object.keys(raw.devices || {}).forEach(function(key) { deviceCounts[key] = includeOwnTraffic ? raw.devices[key] : Math.max(0, Number(raw.devices[key] || 0) - Number(raw.owner_devices[key] || 0)); });
    Object.keys(raw.browsers || {}).forEach(function(key) { browserCounts[key] = includeOwnTraffic ? raw.browsers[key] : Math.max(0, Number(raw.browsers[key] || 0) - Number(raw.owner_browsers[key] || 0)); });
    const deviceNames = Object.keys(deviceCounts).filter(function(key) { return deviceCounts[key] > 0; }).sort(function(a, b) { return deviceCounts[b] - deviceCounts[a]; });
    const browserNames = Object.keys(browserCounts).filter(function(key) { return browserCounts[key] > 0; }).sort(function(a, b) { return browserCounts[b] - browserCounts[a]; });
    row.visitor = analyticsVisitorLabel(raw.visitor_no);
    row.device = deviceNames[0] || "other";
    row.browser = browserNames[0] || "unknown";
    row.browser_count = browserNames.length;
    row.identity = includeOwnTraffic && raw.is_owner ? "Owner browser" : "Browser ID";
    row.total_page_views = row.page_views;
    const visibleDays = visitorDaily.filter(function(item) { return item.visitor_no === raw.visitor_no; });
    row.today_views = visibleDays.filter(function(item) { return item.date === analyticsDate(analyticsDayKey(now)); }).reduce(function(total, item) { return total + Number(item.page_views || 0); }, 0);
    row.active_days = visibleDays.length;
    row.first_seen = raw.first_seen || "";
    row.last_seen = raw.last_seen || "";
    row.regularity = row.total_page_views >= 100 && row.active_days >= 10 ? "heavy" : row.total_page_views >= 30 && row.active_days >= 5 ? "regular" : row.total_page_views >= 10 && row.active_days >= 3 ? "returning" : row.active_days >= 2 ? "repeat" : "new";
    return row;
  }).filter(function(row) { return row.page_views || row.pr_impressions || row.pr_clicks; }).sort(function(a, b) { return b.total_page_views - a.total_page_views; });

  const totals = reportRow(allTotals, includeOwnTraffic);
  totals.unique_sessions = includeOwnTraffic ? Object.keys(sessions).length : Math.max(0, Object.keys(sessions).length - Object.keys(ownerSessions).length);
  const ownerTotals = { page_views: allTotals.owner_page_views, pr_impressions: allTotals.owner_pr_impressions, pr_clicks: allTotals.owner_pr_clicks, unique_sessions: Object.keys(ownerSessions).length };
  return {
    ok: true, schema_version: 3, days: days, revision: revision,
    tracking_started_at: String(properties.getProperty(INDANYA_ANALYTICS_PREFIX + "STARTED_AT") || ""),
    generated_at: new Date().toISOString(), totals: totals,
    all_totals: Object.assign({}, allTotals, { unique_sessions: Object.keys(sessions).length }),
    owner_totals: ownerTotals, articles: namedRows(articles).slice(0, 100), categories: namedRows(categories).slice(0, 50),
    promotions: Object.keys(promotions).map(function(key) {
      const row = Object.assign({}, promotions[key]);
      row.impressions = includeOwnTraffic ? row.impressions : Math.max(0, row.impressions - row.owner_impressions);
      row.clicks = includeOwnTraffic ? row.clicks : Math.max(0, row.clicks - row.owner_clicks);
      row.ctr = row.impressions ? Math.round(row.clicks * 10000 / row.impressions) / 100 : 0;
      return row;
    }).filter(function(row) { return row.impressions || row.clicks; }).sort(function(a, b) { return b.clicks - a.clicks || b.impressions - a.impressions; }).slice(0, 100),
    visitors: visitorRows.slice(0, 200), visitor_daily: visitorDaily, devices: namedRows(devices), referrers: namedRows(referrers).slice(0, 100), daily: daily
  };
}
