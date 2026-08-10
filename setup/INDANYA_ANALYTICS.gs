const INDANYA_ANALYTICS_TOKEN = "indanya-analytics-20260803-v1";
const INDANYA_ANALYTICS_READ_KEY = "7hF9uN2sK4vQ8xC1mR6bT3zW5pL0dYgA";
const INDANYA_ANALYTICS_SITE = "eroscope.github.io/sukebesite2025";
const INDANYA_ANALYTICS_PREFIX = "INDANYA_ANALYTICS_";

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return jsonResponse({
    ok: true,
    service: "indanya-analytics",
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
    if (action === "analytics_event") {
      return jsonResponse(recordAnalyticsEvent(body));
    }
    if (action === "analytics_summary") {
      requireReadKey(body.read_key);
      return jsonResponse(analyticsSummary(body.days));
    }
    throw new Error("unsupported action: " + action);
  } catch (error) {
    return jsonResponse({
      ok: false,
      error: String(error && error.stack ? error.stack : error)
    });
  }
}

function authorizeAnalytics() {
  PropertiesService.getScriptProperties().setProperty(
    INDANYA_ANALYTICS_PREFIX + "READY",
    new Date().toISOString()
  );
  return "ready";
}

function requireReadKey(value) {
  if (String(value || "") !== INDANYA_ANALYTICS_READ_KEY) {
    throw new Error("read key is invalid");
  }
}

function cleanAnalyticsValue(value, limit) {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit || 240);
}

function analyticsHash(value) {
  return Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(value || ""),
    Utilities.Charset.UTF_8
  ).slice(0, 10).map(function(byte) {
    return (byte + 256).toString(16).slice(-2);
  }).join("");
}

function analyticsRead(properties, key, fallback) {
  try {
    return JSON.parse(properties.getProperty(key) || "") || fallback;
  } catch (ignore) {
    return fallback;
  }
}

function analyticsWrite(properties, key, value) {
  properties.setProperty(key, JSON.stringify(value));
}

function analyticsDayKey(date) {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

function analyticsPrune(properties, todayKey) {
  const cutoff = new Date();
  cutoff.setUTCDate(cutoff.getUTCDate() - 185);
  const cutoffKey = analyticsDayKey(cutoff);
  const keys = Object.keys(properties.getProperties());
  keys.forEach(function(key) {
    const match = key.match(/^INDANYA_ANALYTICS_[DAPCVER]_(\d{8})_/);
    if (match && match[1] < cutoffKey) properties.deleteProperty(key);
  });
}

function analyticsMetricKey(eventType) {
  return eventType === "page_view" ? "page_views" :
    eventType === "pr_impression" ? "pr_impressions" : "pr_clicks";
}

function analyticsVisitorNumber(properties, visitorId, deviceId) {
  const aliasKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_" + analyticsHash(visitorId);
  let number = Number(properties.getProperty(aliasKey) || 0);
  if (number > 0) return number;
  const deviceAliasKey = deviceId
    ? INDANYA_ANALYTICS_PREFIX + "DEVICE_VISITOR_" + analyticsHash(deviceId)
    : "";
  if (deviceAliasKey) {
    number = Number(properties.getProperty(deviceAliasKey) || 0);
    if (number > 0) {
      properties.setProperty(aliasKey, String(number));
      return number;
    }
  }
  const sequenceKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_SEQUENCE";
  number = Number(properties.getProperty(sequenceKey) || 0) + 1;
  properties.setProperty(sequenceKey, String(number));
  properties.setProperty(aliasKey, String(number));
  if (deviceAliasKey) properties.setProperty(deviceAliasKey, String(number));
  return number;
}

function analyticsVisitorLabel(number) {
  const value = Math.max(1, Number(number || 1));
  return "訪問者 #" + String(value).padStart(4, "0");
}

function recordAnalyticsEvent(body) {
  if (cleanAnalyticsValue(body.token, 80) !== INDANYA_ANALYTICS_TOKEN) {
    throw new Error("analytics token is invalid");
  }
  const eventType = cleanAnalyticsValue(body.event_type, 40);
  if (["page_view", "pr_impression", "pr_click"].indexOf(eventType) < 0) {
    throw new Error("analytics event type is invalid");
  }
  const site = cleanAnalyticsValue(body.site, 180);
  if (site.indexOf(INDANYA_ANALYTICS_SITE) !== 0) {
    throw new Error("analytics site is invalid");
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const now = new Date();
    const day = analyticsDayKey(now);
    const sessionId = cleanAnalyticsValue(body.session_id, 100);
    const visitorId = cleanAnalyticsValue(body.visitor_id, 120) || sessionId;
    const deviceId = cleanAnalyticsValue(body.device_id, 120);
    const browserFamily = cleanAnalyticsValue(body.browser_family, 40) || "unknown";
    const visitorNo = analyticsVisitorNumber(properties, visitorId, deviceId);
    const deviceType = cleanAnalyticsValue(body.device_type, 40) || "不明";
    const referrerHost = cleanAnalyticsValue(body.referrer_host, 160) || "直接アクセス";
    const slug = cleanAnalyticsValue(body.article_slug, 140) || "unknown";
    const title = cleanAnalyticsValue(body.article_title, 240) || slug;
    const category = cleanAnalyticsValue(body.category, 100) || "未分類";
    const prTitle = cleanAnalyticsValue(body.pr_title, 240) || "PRリンク";
    const destination = cleanAnalyticsValue(body.destination_host, 160);
    const profileKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_PROFILE_" + visitorNo;
    const profile = analyticsRead(properties, profileKey, {
      visitor_no: visitorNo, page_views: 0, pr_impressions: 0, pr_clicks: 0,
      active_days: 0, last_active_day: "", first_seen: now.toISOString(),
      last_seen: "", devices: {}, browsers: {}, visitor_ids: {}, device_ids: {}
    });
    profile[analyticsMetricKey(eventType)] += 1;
    if (profile.last_active_day !== day) {
      profile.active_days = Number(profile.active_days || 0) + 1;
      profile.last_active_day = day;
    }
    profile.first_seen = String(profile.first_seen || now.toISOString());
    profile.last_seen = now.toISOString();
    profile.devices[deviceType] = Number(profile.devices[deviceType] || 0) + 1;
    profile.browsers[browserFamily] = Number(profile.browsers[browserFamily] || 0) + 1;
    profile.visitor_ids[analyticsHash(visitorId)] = true;
    if (deviceId) profile.device_ids[analyticsHash(deviceId)] = true;
    analyticsWrite(properties, profileKey, profile);
    const dayKey = INDANYA_ANALYTICS_PREFIX + "D_" + day + "_TOTAL";
    const dayValue = analyticsRead(properties, dayKey, {
      page_views: 0, pr_impressions: 0, pr_clicks: 0, sessions: []
    });
    if (sessionId && dayValue.sessions.indexOf(sessionId) < 0 && dayValue.sessions.length < 500) {
      dayValue.sessions.push(sessionId);
    }
    dayValue[analyticsMetricKey(eventType)] += 1;
    analyticsWrite(properties, dayKey, dayValue);

    const articleKey = INDANYA_ANALYTICS_PREFIX + "A_" + day + "_" + analyticsHash(slug);
    const article = analyticsRead(properties, articleKey, {
      slug: slug, title: title, category: category,
      page_views: 0, pr_impressions: 0, pr_clicks: 0
    });
    article[analyticsMetricKey(eventType)] += 1;
    analyticsWrite(properties, articleKey, article);

    const categoryKey = INDANYA_ANALYTICS_PREFIX + "C_" + day + "_" + analyticsHash(category);
    const categoryValue = analyticsRead(properties, categoryKey, {
      category: category, page_views: 0, pr_impressions: 0, pr_clicks: 0
    });
    categoryValue[analyticsMetricKey(eventType)] += 1;
    analyticsWrite(properties, categoryKey, categoryValue);

    const visitorKey = INDANYA_ANALYTICS_PREFIX + "V_" + day + "_" + analyticsHash(visitorId);
    const visitor = analyticsRead(properties, visitorKey, {
      visitor_no: visitorNo, page_views: 0, pr_impressions: 0, pr_clicks: 0,
      devices: {}, browsers: {}, browser_count: 0, last_seen: ""
    });
    visitor[analyticsMetricKey(eventType)] += 1;
    visitor.devices[deviceType] = Number(visitor.devices[deviceType] || 0) + 1;
    visitor.browsers[browserFamily] = Number(visitor.browsers[browserFamily] || 0) + 1;
    visitor.browser_count = Object.keys(visitor.browsers || {}).length;
    visitor.last_seen = now.toISOString();
    analyticsWrite(properties, visitorKey, visitor);

    const deviceKey = INDANYA_ANALYTICS_PREFIX + "E_" + day + "_" + analyticsHash(deviceType);
    const device = analyticsRead(properties, deviceKey, {
      device: deviceType, page_views: 0, pr_impressions: 0, pr_clicks: 0
    });
    device[analyticsMetricKey(eventType)] += 1;
    analyticsWrite(properties, deviceKey, device);

    const referrerKey = INDANYA_ANALYTICS_PREFIX + "R_" + day + "_" + analyticsHash(referrerHost);
    const referrer = analyticsRead(properties, referrerKey, {
      referrer: referrerHost, page_views: 0, pr_impressions: 0, pr_clicks: 0
    });
    referrer[analyticsMetricKey(eventType)] += 1;
    analyticsWrite(properties, referrerKey, referrer);

    if (eventType !== "page_view") {
      const promotionId = cleanAnalyticsValue(body.pr_id, 120) + "\n" + prTitle + "\n" + destination;
      const promotionKey = INDANYA_ANALYTICS_PREFIX + "P_" + day + "_" + analyticsHash(promotionId);
      const promotion = analyticsRead(properties, promotionKey, {
        title: prTitle, destination_host: destination, impressions: 0, clicks: 0
      });
      if (eventType === "pr_impression") promotion.impressions += 1;
      if (eventType === "pr_click") promotion.clicks += 1;
      analyticsWrite(properties, promotionKey, promotion);
    }
    if (Math.random() < 0.02) analyticsPrune(properties, day);
  } finally {
    lock.releaseLock();
  }
  return { ok: true };
}

function analyticsSummary(requestedDays) {
  const days = Math.max(1, Math.min(180, Number(requestedDays || 30)));
  const cutoff = new Date();
  cutoff.setUTCDate(cutoff.getUTCDate() - days + 1);
  const cutoffKey = analyticsDayKey(cutoff);
  const properties = PropertiesService.getScriptProperties().getProperties();
  const articles = {};
  const categories = {};
  const promotions = {};
  const visitors = {};
  const visitorDaily = [];
  const devices = {};
  const referrers = {};
  const daily = {};
  const totals = { page_views: 0, unique_sessions: 0, pr_impressions: 0, pr_clicks: 0 };

  Object.keys(properties).forEach(function(key) {
    const match = key.match(/^INDANYA_ANALYTICS_([DAPCVER])_(\d{8})_/);
    if (!match || match[2] < cutoffKey) return;
    const value = analyticsRead({ getProperty: function() { return properties[key]; } }, key, {});
    const date = match[2].slice(0, 4) + "-" + match[2].slice(4, 6) + "-" + match[2].slice(6, 8);
    if (match[1] === "D") {
      totals.page_views += Number(value.page_views || 0);
      totals.pr_impressions += Number(value.pr_impressions || 0);
      totals.pr_clicks += Number(value.pr_clicks || 0);
      totals.unique_sessions += Array.isArray(value.sessions) ? value.sessions.length : 0;
      daily[date] = {
        date: date,
        page_views: Number(value.page_views || 0),
        pr_impressions: Number(value.pr_impressions || 0),
        pr_clicks: Number(value.pr_clicks || 0)
      };
    } else if (match[1] === "A") {
      const id = String(value.slug || "unknown");
      if (!articles[id]) articles[id] = {
        slug: id, title: String(value.title || id), category: String(value.category || "未分類"),
        page_views: 0, pr_impressions: 0, pr_clicks: 0
      };
      articles[id].page_views += Number(value.page_views || 0);
      articles[id].pr_impressions += Number(value.pr_impressions || 0);
      articles[id].pr_clicks += Number(value.pr_clicks || 0);
    } else if (match[1] === "C") {
      const id = String(value.category || "未分類");
      if (!categories[id]) categories[id] = {
        category: id, page_views: 0, pr_impressions: 0, pr_clicks: 0
      };
      categories[id].page_views += Number(value.page_views || 0);
      categories[id].pr_impressions += Number(value.pr_impressions || 0);
      categories[id].pr_clicks += Number(value.pr_clicks || 0);
    } else if (match[1] === "P") {
      const id = String(value.title || "PRリンク") + "\n" + String(value.destination_host || "");
      if (!promotions[id]) promotions[id] = {
        title: String(value.title || "PRリンク"),
        destination_host: String(value.destination_host || ""), impressions: 0, clicks: 0
      };
      promotions[id].impressions += Number(value.impressions || 0);
      promotions[id].clicks += Number(value.clicks || 0);
    } else if (match[1] === "V") {
      const id = String(value.visitor_no || "0");
      if (!visitors[id]) visitors[id] = {
        visitor_no: Number(value.visitor_no || 0), page_views: 0,
        pr_impressions: 0, pr_clicks: 0, devices: {}, browsers: {}, last_seen: ""
      };
      visitors[id].page_views += Number(value.page_views || 0);
      visitors[id].pr_impressions += Number(value.pr_impressions || 0);
      visitors[id].pr_clicks += Number(value.pr_clicks || 0);
      Object.keys(value.devices || {}).forEach(function(deviceName) {
        visitors[id].devices[deviceName] = Number(visitors[id].devices[deviceName] || 0) +
          Number(value.devices[deviceName] || 0);
      });
      Object.keys(value.browsers || {}).forEach(function(browserName) {
        visitors[id].browsers[browserName] = Number(visitors[id].browsers[browserName] || 0) +
          Number(value.browsers[browserName] || 0);
      });
      if (String(value.last_seen || "") > visitors[id].last_seen) {
        visitors[id].last_seen = String(value.last_seen || "");
      }
      const visitorDailyKey = date + "_" + id;
      let dailyItem = visitorDaily.filter(function(item) {
        return item._key === visitorDailyKey;
      })[0];
      if (!dailyItem) {
        dailyItem = {
          _key: visitorDailyKey,
          visitor_no: Number(value.visitor_no || 0),
          date: date,
          page_views: 0,
          pr_impressions: 0,
          pr_clicks: 0
        };
        visitorDaily.push(dailyItem);
      }
      dailyItem.page_views += Number(value.page_views || 0);
      dailyItem.pr_impressions += Number(value.pr_impressions || 0);
      dailyItem.pr_clicks += Number(value.pr_clicks || 0);
    } else if (match[1] === "E") {
      const id = String(value.device || "不明");
      if (!devices[id]) devices[id] = {
        device: id, page_views: 0, pr_impressions: 0, pr_clicks: 0
      };
      devices[id].page_views += Number(value.page_views || 0);
      devices[id].pr_impressions += Number(value.pr_impressions || 0);
      devices[id].pr_clicks += Number(value.pr_clicks || 0);
    } else if (match[1] === "R") {
      const id = String(value.referrer || "直接アクセス");
      if (!referrers[id]) referrers[id] = {
        referrer: id, page_views: 0, pr_impressions: 0, pr_clicks: 0
      };
      referrers[id].page_views += Number(value.page_views || 0);
      referrers[id].pr_impressions += Number(value.pr_impressions || 0);
      referrers[id].pr_clicks += Number(value.pr_clicks || 0);
    }
  });

  function withRates(item) {
    item.click_rate = item.page_views
      ? Math.round(item.pr_clicks * 10000 / item.page_views) / 100 : 0;
    item.pr_ctr = item.pr_impressions
      ? Math.round(item.pr_clicks * 10000 / item.pr_impressions) / 100 : 0;
    return item;
  }
  const articleRows = Object.keys(articles).map(function(key) {
    return withRates(articles[key]);
  }).sort(function(a, b) {
    return b.pr_clicks - a.pr_clicks || b.page_views - a.page_views;
  });
  const categoryRows = Object.keys(categories).map(function(key) {
    return withRates(categories[key]);
  }).sort(function(a, b) {
    return b.pr_clicks - a.pr_clicks || b.page_views - a.page_views;
  });
  const promotionRows = Object.keys(promotions).map(function(key) {
    const item = promotions[key];
    item.ctr = item.impressions
      ? Math.round(item.clicks * 10000 / item.impressions) / 100 : 0;
    return item;
  }).sort(function(a, b) {
    return b.clicks - a.clicks || b.impressions - a.impressions;
  });
  const visitorRows = Object.keys(visitors).map(function(key) {
    const item = withRates(visitors[key]);
    const profileKey = INDANYA_ANALYTICS_PREFIX + "VISITOR_PROFILE_" + item.visitor_no;
    const profile = analyticsRead({ getProperty: function() { return properties[profileKey]; } }, profileKey, {});
    item.visitor = analyticsVisitorLabel(item.visitor_no);
    item.device = Object.keys(item.devices).sort(function(a, b) {
      return item.devices[b] - item.devices[a];
    })[0] || "不明";
    const browserNames = Object.keys(item.browsers || {}).sort(function(a, b) {
      return item.browsers[b] - item.browsers[a];
    });
    item.browser_count = Math.max(
      browserNames.length,
      Object.keys(profile.visitor_ids || {}).length,
      Number(item.browser_count || 0)
    );
    item.browser = browserNames[0] || "unknown";
    item.identity = Object.keys(profile.device_ids || {}).length ? "端末推定" : "ブラウザID";
    item.today_views = visitorDaily.filter(function(day) {
      return day.visitor_no === item.visitor_no && day.date === new Date().toISOString().slice(0, 10);
    }).reduce(function(total, day) { return total + day.page_views; }, 0);
    item.total_page_views = Math.max(
      Number(profile.page_views || 0), Number(item.page_views || 0)
    );
    item.active_days = Number(profile.active_days || 1);
    item.first_seen = String(profile.first_seen || item.last_seen || "").replace("T", " ").slice(0, 16);
    item.last_seen = String(profile.last_seen || item.last_seen || "").replace("T", " ").slice(0, 16);
    item.regularity = item.total_page_views >= 100 && item.active_days >= 10 ? "ヘビー常連" :
      item.total_page_views >= 30 && item.active_days >= 5 ? "常連" :
      item.total_page_views >= 10 && item.active_days >= 3 ? "よく見る" :
      item.active_days >= 2 ? "再訪" : "新規";
    delete item.devices;
    delete item.browsers;
    return item;
  }).sort(function(a, b) {
    return b.total_page_views - a.total_page_views || b.active_days - a.active_days;
  });
  visitorDaily.forEach(function(item) {
    item.visitor = analyticsVisitorLabel(item.visitor_no);
    delete item._key;
  });
  visitorDaily.sort(function(a, b) {
    return b.date.localeCompare(a.date) || a.visitor_no - b.visitor_no;
  });
  const deviceRows = Object.keys(devices).map(function(key) {
    return withRates(devices[key]);
  }).sort(function(a, b) { return b.page_views - a.page_views; });
  const referrerRows = Object.keys(referrers).map(function(key) {
    return withRates(referrers[key]);
  }).sort(function(a, b) { return b.page_views - a.page_views; });

  return {
    ok: true,
    days: days,
    generated_at: new Date().toISOString(),
    totals: {
      page_views: totals.page_views,
      unique_sessions: totals.unique_sessions,
      pr_impressions: totals.pr_impressions,
      pr_clicks: totals.pr_clicks,
      click_rate: totals.page_views
        ? Math.round(totals.pr_clicks * 10000 / totals.page_views) / 100 : 0,
      pr_ctr: totals.pr_impressions
        ? Math.round(totals.pr_clicks * 10000 / totals.pr_impressions) / 100 : 0
    },
    articles: articleRows.slice(0, 100),
    categories: categoryRows.slice(0, 50),
    promotions: promotionRows.slice(0, 100),
    visitors: visitorRows.slice(0, 200),
    visitor_daily: visitorDaily.slice(0, 2000),
    devices: deviceRows,
    referrers: referrerRows.slice(0, 100),
    daily: Object.keys(daily).sort().map(function(key) { return daily[key]; })
  };
}
