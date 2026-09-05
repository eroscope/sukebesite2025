from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

def dismiss_common_overlays(page: Any) -> None:
    labels = [
        "同意する",
        "すべて同意",
        "許可する",
        "Accept all",
        "Accept",
        "I agree",
        "閉じる",
        "Close",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=800)
        except Exception:  # noqa: BLE001
            pass


def auto_scroll(page: Any) -> None:
    page.evaluate(
        """
        async () => {
          const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
          let lastHeight = 0;
          for (let i = 0; i < 28; i++) {
            const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            window.scrollTo(0, Math.min(height, window.scrollY + Math.max(700, window.innerHeight * 0.85)));
            await sleep(220);
            const nextHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            if (window.scrollY + window.innerHeight >= nextHeight - 20 && nextHeight === lastHeight) break;
            lastHeight = nextHeight;
          }
          window.scrollTo(0, 0);
          await sleep(250);
        }
        """
    )


EXTRACT_SCRIPT = r"""
() => {
  const abs = value => {
    try { return new URL(value, document.baseURI).href; } catch (_) { return ''; }
  };
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const rectData = el => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x + window.scrollX),
      y: Math.round(r.y + window.scrollY),
      width: Math.round(r.width),
      height: Math.round(r.height)
    };
  };
  const visible = el => {
    const style = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0 && r.width > 1 && r.height > 1;
  };
  const nearestText = el => {
    const texts = [];
    let node = el;
    for (let level = 0; level < 4 && node; level++, node = node.parentElement) {
      const candidates = Array.from(node.querySelectorAll(':scope > p, :scope > figcaption, :scope > h1, :scope > h2, :scope > h3, :scope > div'));
      for (const candidate of candidates) {
        const text = clean(candidate.innerText);
        if (text && text.length < 500) texts.push(text);
        if (texts.length >= 4) break;
      }
      if (texts.length) break;
    }
    return texts.slice(0, 4).join(' / ').slice(0, 700);
  };
  const ancestors = el => {
    const result = [];
    let node = el;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      let item = node.tagName ? node.tagName.toLowerCase() : '';
      if (node.id) item += '#' + node.id;
      if (node.classList && node.classList.length) item += '.' + Array.from(node.classList).slice(0, 4).join('.');
      if (item) result.push(item);
    }
    return result.join(' > ').slice(0, 500);
  };

  const rootSelectors = [
    '.entry-content', '.post-content', '.article-content', '.article-body',
    '.entry-body', '.post-body', '#article-body', '#entry-content',
    'main article', 'article', 'main'
  ];
  const rootCandidates = Array.from(new Set(
    rootSelectors.flatMap(selector => Array.from(document.querySelectorAll(selector)))
  )).filter(visible);
  const rootScore = el => {
    const signature = `${el.id || ''} ${el.className || ''}`.toLowerCase();
    if (/(?:sidebar|widget|recommend|related|ranking|pickup|advert|banner)/.test(signature)) {
      return -100000;
    }
    const specific = /(?:entry-content|post-content|article-content|article-body|entry-body|post-body)/.test(signature);
    const textLength = Math.min(30000, clean(el.innerText).length);
    const heading = el.querySelector('h1') ? 6000 : 0;
    const media = Math.min(40, el.querySelectorAll('img,video,iframe').length) * 250;
    return (specific ? 14000 : 0) + textLength + heading + media;
  };
  const preferredRoot = rootCandidates.sort((a, b) => rootScore(b) - rootScore(a))[0]
    || document.body;
  preferredRoot.setAttribute('data-indanya-article-root', 'true');

  const images = [];
  for (const el of Array.from(preferredRoot.querySelectorAll('img'))) {
    const linkUrl = el.closest('a') ? abs(el.closest('a').href) : '';
    const srcsetUrls = [el.srcset, el.getAttribute('data-srcset')]
      .flatMap(value => String(value || '').split(','))
      .map(value => abs(value.trim().split(/\s+/)[0]))
      .filter(Boolean);
    const urls = Array.from(new Set([
      linkUrl && /\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)/i.test(linkUrl) ? linkUrl : '',
      ...srcsetUrls,
      abs(el.getAttribute('data-original') || ''),
      abs(el.getAttribute('data-large') || ''),
      abs(el.getAttribute('data-full') || ''),
      abs(el.getAttribute('data-src') || ''),
      abs(el.currentSrc || ''),
      abs(el.src || '')
    ].filter(Boolean)));
    const src = urls[0] || '';
    if (!src) continue;
    images.push({
      url: src,
      urls,
      alt: clean(el.alt),
      title: clean(el.title),
      natural_width: Number(el.naturalWidth || 0),
      natural_height: Number(el.naturalHeight || 0),
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      link_url: linkUrl,
      inside_article: true
    });
  }

  // Some roundup pages publish the lead image as an empty link followed by a
  // line break. It has no rendered <img>, but the linked file is still the
  // article's primary media and must be inspected with the visible gallery.
  for (const el of Array.from(preferredRoot.querySelectorAll('a[href]'))) {
    if (el.querySelector('img')) continue;
    const url = abs(el.href || '');
    if (!/\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)/i.test(url)) continue;
    images.push({
      url,
      urls: [url],
      alt: clean(el.getAttribute('aria-label')),
      title: clean(el.title || 'article image link'),
      natural_width: 0,
      natural_height: 0,
      visible: visible(el) || Boolean(el.parentElement && visible(el.parentElement)),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      link_url: url,
      anchor_href_candidate: true,
      inside_article: true
    });
  }

  const backgroundImages = [];
  for (const el of Array.from(preferredRoot.querySelectorAll('*')).slice(0, 5000)) {
    if (!visible(el)) continue;
    const bg = getComputedStyle(el).backgroundImage || '';
    const match = bg.match(/url\(["']?(.*?)["']?\)/i);
    if (!match) continue;
    const url = abs(match[1]);
    if (!url) continue;
    backgroundImages.push({
      url,
      alt: clean(el.getAttribute('aria-label')),
      title: clean(el.getAttribute('title')),
      natural_width: 0,
      natural_height: 0,
      visible: true,
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      link_url: el.closest('a') ? abs(el.closest('a').href) : ''
    });
  }

  const videos = [];
  for (const el of Array.from(preferredRoot.querySelectorAll('video'))) {
    const sources = [el.currentSrc, el.src, ...Array.from(el.querySelectorAll('source')).map(x => x.src)].map(abs).filter(Boolean);
    videos.push({
      kind: 'direct',
      urls: Array.from(new Set(sources)),
      poster: el.poster ? abs(el.poster) : '',
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el)
    });
  }
  for (const el of Array.from(preferredRoot.querySelectorAll('iframe'))) {
    const src = abs(el.src || '');
    if (!src) continue;
    videos.push({
      kind: 'iframe',
      urls: [src],
      poster: '',
      visible: visible(el),
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      title: clean(el.title)
    });
  }

  const links = [];
  for (const el of Array.from(document.querySelectorAll('a[href]')).slice(0, 3000)) {
    const url = abs(el.href || '');
    if (!url || !/^https?:/i.test(url)) continue;
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.title);
    const image = el.querySelector('img');
    const directImageHref = /\.(?:jpe?g|png|gif|webp|avif)(?:[?#]|$)/i.test(url);
    if (!visible(el) && !directImageHref) continue;
    if (!text && !image && !directImageHref) continue;
    const style = getComputedStyle(el);
    links.push({
      url,
      text: text.slice(0, 500),
      contains_image: Boolean(image) || directImageHref,
      rect: rectData(el),
      context: nearestText(el),
      ancestors: ancestors(el),
      font_size: style.fontSize || '',
      font_weight: style.fontWeight || '',
      color: style.color || '',
      background: style.backgroundColor || ''
    });
    if (links.length >= 200) break;
  }
  const blocks = [];
  for (const el of Array.from(preferredRoot.querySelectorAll('h1,h2,h3,h4,p,li,blockquote,figcaption,pre')).slice(0, 1000)) {
    const text = clean(el.innerText);
    if (!text || text.length < 2) continue;
    blocks.push({
      tag: el.tagName.toLowerCase(),
      text: text.slice(0, 3000),
      rect: rectData(el),
      ancestors: ancestors(el)
    });
  }

  const meta = name => {
    const el = document.querySelector(`meta[property="${name}"],meta[name="${name}"]`);
    return el ? clean(el.content) : '';
  };
  const thumbnailImages = [];
  const thumbnailUrls = new Set();
  const addThumbnail = (url, alt, title, el = null) => {
    const resolved = abs(url);
    if (!resolved || thumbnailUrls.has(resolved)) return;
    thumbnailUrls.add(resolved);
    thumbnailImages.push({
      url: resolved,
      urls: [resolved],
      alt: clean(alt),
      title: clean(title || 'page thumbnail candidate'),
      natural_width: el ? Number(el.naturalWidth || 0) : 0,
      natural_height: el ? Number(el.naturalHeight || 0) : 0,
      visible: el ? visible(el) : true,
      rect: el ? rectData(el) : {},
      context: 'OGP・記事アイキャッチのサムネイル候補',
      ancestors: el ? ancestors(el) : 'head > meta[og:image]',
      link_url: '',
      thumbnail_only_candidate: true
    });
  };
  addThumbnail(meta('og:image') || meta('twitter:image') || meta('twitter:image:src'), meta('og:image:alt'), 'OGP image');
  for (const el of Array.from(document.querySelectorAll(
    'img.wp-post-image, .eye-catch img, .eyecatch img, .post-thumbnail img, .entry-header img'
  )).slice(0, 4)) {
    addThumbnail(el.currentSrc || el.src || el.getAttribute('data-src'), el.alt, el.title || 'article eye-catch', el);
  }
  const canonical = document.querySelector('link[rel="canonical"]');
  return {
    title: clean(meta('og:title') || document.title),
    description: clean(meta('og:description') || meta('description')),
    canonical_url: canonical ? abs(canonical.href) : location.href,
    final_url: location.href,
    body_text: clean(preferredRoot.innerText).slice(0, 16000),
    text_blocks: blocks.slice(0, 60),
    links: links.slice(0, 100),
    images: images.concat(backgroundImages),
    thumbnail_images: thumbnailImages,
    videos,
    page: {
      width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
      height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
    }
  };
}
"""


def image_extension(content_type: str, url: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else ".img"
