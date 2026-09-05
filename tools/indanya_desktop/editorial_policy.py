from __future__ import annotations

import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse


POLICY_VERSION = "adult-editorial-v7-source-aware"
FANZA_MEDIA_PROFILE = "fanza-official-product-v2"
# Keep only samples whose product ID and official DMM/FANZA delivery host match.
FANZA_REVIEW_SAFE_MODE = False
FANZA_TRANSPARENCY_NOTE = (
    "この記事にはFANZAのアフィリエイト広告が含まれます。"
    "FANZA公式の商品情報と、商品IDを照合した公式パッケージ・商品紹介画像をもとに、"
    "編集部が独自に構成しています。レス本文は記事表現のための再構成です。"
)
FANZA_ID_MISMATCH_MESSAGE = (
    "商品URL・公式商品画像・商品リンクの商品ID照合に失敗しました。"
    "再生成が必要です。"
)

FANZA_HOSTS = {
    "dmm.co.jp",
    "www.dmm.co.jp",
    "video.dmm.co.jp",
    "book.dmm.co.jp",
    "dlsoft.dmm.co.jp",
    "al.dmm.com",
    "al.dmm.co.jp",
    "al.fanza.co.jp",
}
FANZA_IMAGE_HOST_SUFFIXES = (
    ".dmm.co.jp",
    ".dmm.com",
)
FANZA_VIDEO_HOST_SUFFIXES = (
    ".dmm.co.jp",
    ".dmm.com",
    ".fanza.co.jp",
)

# These words are not sufficient individually; they are used only as concrete
# evidence after the page-level AI review has also classified the main content.
ADULT_MARKERS = (
    "18禁", "成人向け", "アダルト", "FANZA", "AV", "エロ", "セックス",
    "性交", "濡れ場", "オナニー", "自慰", "全裸", "ヌード", "裸", "乳首",
    "おっぱい", "巨乳", "爆乳", "胸元", "下乳", "尻", "パンツ", "下着",
    "ランジェリー", "痴漢", "中出し", "フェラ", "クンニ", "手コキ",
    "足コキ", "パイズリ", "騎乗位", "正常位", "ハメ撮り", "射精", "性器",
    "風俗", "ソープ", "デリヘル", "NTR", "寝取", "乱交", "緊縛", "脱衣",
    "脱い", "着エロ", "百合H", "初エッチ", "ポルノ", "グラドル",
)

# Ambiguous material is rejected even if it could be made suggestive by the title.
GENERAL_TOPIC_MARKERS = (
    "特殊詐欺", "感謝状", "クラウドファンディング", "クラファン", "発売決定",
    "対応機種", "Switch", "PlayStation", "スマホに登場", "子トラ", "動物園",
    "一般ニュース", "スポーツ結果", "選挙", "事故ニュース",
)

# A strict affiliate-review profile should not present apparent real-world abuse,
# privacy violations, or sexual material involving minors as entertainment.
DISALLOWED_RISK_MARKERS = (
    "小学生", "中学生", "未成年", "児童", "女児", "男児", "幼児", "幼少期",
    "盗撮", "流出風", "隠し撮り", "本人の許可なく", "無断撮影", "児童ポルノ",
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reasons: tuple[str, ...]

    @property
    def message(self) -> str:
        return " / ".join(self.reasons)


class EditorialPolicyError(RuntimeError):
    """The source or generated article is outside the strict publishing policy."""


def _unwrapped_url(value: str) -> str:
    """Return the DMM destination from a generated affiliate URL when present."""
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() not in {
        "al.dmm.com", "al.dmm.co.jp", "al.fanza.co.jp"
    }:
        return str(value or "")
    query = parse_qs(parsed.query)
    for key in ("lurl", "url"):
        if query.get(key):
            return unquote(str(query[key][0]))
    return ""


def is_fanza_product_url(value: str) -> bool:
    """Accept individual official product pages, never searches or performer lists."""
    destination = _unwrapped_url(value)
    try:
        parsed = urlparse(destination)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in FANZA_HOSTS:
        return False
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    if host == "video.dmm.co.jp":
        return "/content/" in path and bool(query.get("id"))
    if "/detail/" in path and ("cid=" in path or bool(query.get("cid"))):
        return True
    if host == "book.dmm.co.jp":
        return "/product/" in path and bool(re.search(r"[a-z0-9]", path))
    if host == "dlsoft.dmm.co.jp":
        return "/detail/" in path and bool(re.search(r"[a-z0-9]", path))
    return False


def canonical_fanza_product_url(value: str) -> str:
    """Return a stable product URL without recommendation/tracking parameters."""
    destination = _unwrapped_url(value)
    if not is_fanza_product_url(destination):
        return ""
    parsed = urlparse(destination)
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if host == "video.dmm.co.jp":
        product_id = str((query.get("id") or [""])[0]).strip()
        return f"https://video.dmm.co.jp/av/content/?id={quote(product_id, safe='')}"
    return parsed._replace(query="", fragment="").geturl()


def fanza_product_id(value: str) -> str:
    """Extract the stable product id from an individual FANZA product URL."""
    destination = _unwrapped_url(value)
    if not is_fanza_product_url(destination):
        return ""
    parsed = urlparse(destination)
    query = parse_qs(parsed.query)
    host = (parsed.hostname or "").lower()
    if host == "video.dmm.co.jp":
        return unquote(str((query.get("id") or [""])[0])).strip().casefold()
    if query.get("cid"):
        return unquote(str(query["cid"][0])).strip().casefold()
    match = re.search(r"(?:^|/)cid[=/]([^/?#]+)", parsed.path, re.IGNORECASE)
    if match:
        return unquote(match.group(1)).strip().casefold()
    match = re.search(r"/(?:product|detail)/([^/?#]+)", parsed.path, re.IGNORECASE)
    return unquote(match.group(1)).strip().casefold() if match else ""


def fanza_image_product_id(value: str) -> str:
    """Extract the product id encoded in a DMM-hosted package image URL."""
    if not is_fanza_official_image_url(value):
        return ""
    path = unquote(urlparse(str(value or "")).path).casefold()
    match = re.search(r"/digital/video/([^/]+)/", path)
    if match:
        return match.group(1).strip()
    filename = path.rsplit("/", 1)[-1]
    match = re.match(r"(.+?)(?:pl|ps|pr|jp-?\d+|js-?\d+)\.(?:jpe?g|png|webp)$", filename)
    return match.group(1).strip() if match else ""


def is_same_fanza_product(source_url: str, image_url: str) -> bool:
    product_id = fanza_product_id(source_url)
    return bool(product_id and product_id == fanza_image_product_id(image_url))


def _download_fanza_product_image(
    product_id: str,
    suffix: str,
    *,
    image_id: str,
    alt: str,
    rights_basis: str,
) -> dict[str, Any] | None:
    for url in _fanza_product_image_urls(product_id, suffix):
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read(12 * 1024 * 1024 + 1)
                content_type = str(response.headers.get("Content-Type") or "").lower()
                final_url = str(response.geturl() or url)
        except (OSError, urllib.error.URLError, TimeoutError):
            continue
        if (
            not data
            or len(data) > 12 * 1024 * 1024
            or not content_type.startswith("image/")
            or fanza_image_product_id(final_url) != product_id
        ):
            continue
        return {
            "id": image_id,
            "url": final_url,
            "rights_source_url": final_url,
            "alt": alt,
            "extension": ".jpg",
            "mime_type": "image/jpeg",
            "data": data,
            "width": 0,
            "height": 0,
            "orientation": "portrait",
            "rights_basis": rights_basis,
            "product_id": product_id,
        }
    return None


def _fanza_product_image_urls(product_id: str, suffix: str) -> list[str]:
    urls: list[str] = []
    if product_id.startswith("d_"):
        doujin_suffix = suffix
        sample = re.fullmatch(r"jp-(\d+)", suffix)
        if sample:
            doujin_suffix = f"jp-{int(sample.group(1)):03d}"
        for media_type in ("comic", "cg", "voice"):
            urls.append(
                "https://doujin-assets.dmm.co.jp/digital/"
                f"{media_type}/{product_id}/{product_id}{doujin_suffix}.jpg"
            )
    urls.append(
        "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/"
        f"{product_id}/{product_id}{suffix}.jpg"
    )
    return urls


def _download_exact_fanza_package(product_id: str) -> dict[str, Any] | None:
    """Fetch only the official large package image encoded with the requested id."""
    if not re.fullmatch(r"[a-z0-9_]+", product_id):
        return None
    for suffix in ("pl", "ps"):
        image = _download_fanza_product_image(
            product_id,
            suffix,
            image_id="fanza-exact-package",
            alt="パッケージ画像",
            rights_basis="fanza_product_main_image",
        )
        if image:
            return image
    return None


def download_exact_fanza_package(product_id: str) -> dict[str, Any] | None:
    """Return the official package image for one exact FANZA product id."""
    return _download_exact_fanza_package(product_id)


def _download_exact_fanza_samples(product_id: str) -> list[dict[str, Any]]:
    """Fetch the official product-introduction gallery for exactly one product."""
    if not re.fullmatch(r"[a-z0-9_]+", product_id):
        return []
    samples: list[dict[str, Any]] = []
    missing_streak = 0

    def download(number: int) -> tuple[int, dict[str, Any] | None]:
        return number, _download_fanza_product_image(
            product_id,
            f"jp-{number}",
            image_id=f"fanza-exact-sample-{number}",
            alt=f"公式商品紹介画像 {number}",
            rights_basis="fanza_product_sample_image",
        )

    # FANZA sample numbers are contiguous. Fetch a window concurrently, then
    # stop at the first three missing numbers just as the old serial loop did.
    with ThreadPoolExecutor(max_workers=10) as executor:
        for start in range(1, 101, 20):
            end = min(101, start + 20)
            results = dict(executor.map(download, range(start, end)))
            should_stop = False
            for number in range(start, end):
                image = results.get(number)
                if image:
                    samples.append(image)
                    missing_streak = 0
                    continue
                missing_streak += 1
                if missing_streak >= 3:
                    should_stop = True
                    break
            if should_stop:
                break
    return samples


def is_fanza_official_image_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and bool(host)
        and (
            host in {"dmm.co.jp", "dmm.com"}
            or host.endswith(FANZA_IMAGE_HOST_SUFFIXES)
        )
    )


def is_fanza_package_image(
    image: dict[str, Any],
    expected_product_id: str = "",
) -> bool:
    """Identify the single product/package image, excluding samples and recommendations."""
    url = str(image.get("url") or "")
    if not is_fanza_official_image_url(url):
        return False
    alt = str(image.get("alt") or "").casefold()
    path = urlparse(url).path.casefold()
    if any(term in alt for term in ("サンプル画像", "女優", "おすすめ", "ランキング")):
        return False
    if re.search(r"j[pn]-?\d+\.(?:jpe?g|png|webp)$", path):
        return False
    looks_like_package = "パッケージ画像" in alt or bool(
        re.search(r"(?:pl|ps|pr)\.(?:jpe?g|png|webp)$", path)
    )
    if not looks_like_package:
        return False
    if expected_product_id:
        return fanza_image_product_id(url) == expected_product_id.casefold()
    return True


def is_fanza_product_sample_image(
    image: dict[str, Any],
    expected_product_id: str = "",
) -> bool:
    url = str(image.get("url") or image.get("source_url") or "")
    if not is_fanza_official_image_url(url):
        return False
    path = unquote(urlparse(url).path).casefold()
    if not re.search(r"jp-?\d+\.(?:jpe?g|png|webp)$", path):
        return False
    return not expected_product_id or fanza_image_product_id(url) == expected_product_id.casefold()


def is_fanza_official_sample_video_url(value: str, expected_product_id: str = "") -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host not in {"dmm.co.jp", "dmm.com", "fanza.co.jp"} and not host.endswith(FANZA_VIDEO_HOST_SUFFIXES):
        return False
    lowered_url = unquote(str(value or "")).casefold()
    if any(marker in lowered_url for marker in (
        "doubleclick", "adservice", "googlesyndication", "ad-stir", "openx", "popin",
    )):
        return False
    looks_like_sample = (
        re.search(r"\.(?:mp4|webm|m4v)(?:[?#]|$)", parsed.path, re.IGNORECASE)
        or "html5_player" in lowered_url
        or "sample" in lowered_url
        or "freepv" in lowered_url
        or "/pv/" in lowered_url
        or "/litevideo/" in lowered_url
    )
    if not looks_like_sample:
        return False
    if not expected_product_id:
        return True
    normalized_product = re.sub(r"[^a-z0-9]", "", expected_product_id.casefold())
    normalized_url = re.sub(r"[^a-z0-9]", "", lowered_url)
    return bool(normalized_product and normalized_product in normalized_url)


def _fanza_sample_videos(source: dict[str, Any], product_id: str) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in source.get("videos") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not is_fanza_official_sample_video_url(url, product_id):
            continue
        if url in seen:
            continue
        seen.add(url)
        video = dict(item)
        video.update({
            "id": f"video-{len(videos) + 1}",
            "rights_basis": "fanza_official_share_embed",
            "rights_source_url": url,
            "ai_verdict": "article",
            "ai_role": "article_main",
            "ai_relation": "official FANZA sample video",
            "ai_recommended_use": "body",
            "ai_relevance_score": 100,
            "ai_reason": "FANZA公式の商品ページから取得した同一商品のサンプル動画です。",
            "ai_recommended": True,
        })
        videos.append(video)
    return videos


def _fanza_free_video_embed(product_id: str) -> dict[str, Any] | None:
    """Return a verified official Free Video Tool embed for one product."""
    if not re.fullmatch(r"[a-z0-9_]+", product_id):
        return None
    tool_url = (
        "https://www.dmm.co.jp/litevideo/-/part/=/cid="
        f"{quote(product_id, safe='')}/size=720_480/"
    )
    request = urllib.request.Request(tool_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if int(getattr(response, "status", response.getcode()) or 0) != 200:
                return None
            html = response.read(256 * 1024).decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return None
    if product_id.casefold() not in html.casefold():
        return None
    player = re.search(r'<iframe[^>]+src=["\']([^"\']+)', html, re.IGNORECASE)
    embed_url = player.group(1).strip() if player else tool_url
    if not is_fanza_official_sample_video_url(embed_url, product_id):
        return None
    return {
        "id": "video-1",
        "kind": "iframe",
        "url": embed_url,
        "mime_type": "text/html",
        "width": 720,
        "height": 480,
        "title": "FANZA公式サンプル動画",
        "rights_basis": "fanza_free_video_tool_embed",
        "rights_source_url": tool_url,
        "ai_verdict": "article",
        "ai_role": "article_main",
        "ai_relation": "official FANZA sample video",
        "ai_recommended_use": "body",
        "ai_relevance_score": 100,
        "ai_reason": "FANZA無料動画ツールが同一商品に提供している公式サンプル動画です。",
        "ai_recommended": True,
    }


def restrict_source_to_fanza_product(source: dict[str, Any]) -> dict[str, Any]:
    """Reduce a FANZA page to the material DMM explicitly exposes as product media."""
    source_url = str(source.get("requested_url") or source.get("url") or "")
    if not is_fanza_product_url(source_url):
        raise EditorialPolicyError("FANZAの個別商品URLだけを記事にできます")
    product_id = fanza_product_id(source_url)
    if not product_id:
        raise EditorialPolicyError("FANZAの商品IDを確認できません")

    candidates = [
        item for item in source.get("images") or []
        if isinstance(item, dict) and is_fanza_package_image(item, product_id)
    ]
    exact_package = _download_exact_fanza_package(product_id)
    if exact_package:
        candidates.insert(0, exact_package)
    if not candidates:
        raise EditorialPolicyError(
            f"商品ID {product_id} と一致する公式パッケージ画像を確認できません"
        )

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        url = str(item.get("url") or "")
        data = item.get("data")
        area = int(item.get("width") or 0) * int(item.get("height") or 0)
        return (
            1 if "パッケージ画像" in str(item.get("alt") or "") else 0,
            area,
            len(data) if isinstance(data, bytes) else 0,
        )

    selected = dict(exact_package or max(candidates, key=score))
    selected.update({
        "id": "media-1",
        "rights_basis": "fanza_product_main_image",
        "rights_source_url": str(selected.get("url") or ""),
        "product_id": product_id,
        "ai_verdict": "article",
        "ai_role": "article_main",
        "ai_relation": "official product package",
        "ai_recommended_use": "thumbnail_and_body",
        "ai_relevance_score": 100,
        "ai_reason": "FANZA個別商品ページのパッケージ画像",
        "ai_recommended": True,
    })
    official_samples = _download_exact_fanza_samples(product_id)
    for number, sample in enumerate(official_samples, start=2):
        sample.update({
            "id": f"media-{number}",
            "ai_verdict": "article",
            "ai_role": "article_supporting",
            "ai_relation": "official product introduction image",
            "ai_recommended_use": "body",
            "ai_relevance_score": 100,
            "ai_reason": "同一商品のFANZA公式商品紹介画像",
            "ai_recommended": True,
        })
    selected_images = [selected, *official_samples]
    selected_image_ids = [str(item["id"]) for item in selected_images]
    official_people = []
    for item in source.get("fanza_people") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        official_people.append({
            **item,
            "image_ids": selected_image_ids,
            "reason": str(item.get("reason") or "FANZA商品詳細の出演者欄で確認"),
        })
    performer_links = [
        {
            "url": str(item.get("url") or ""),
            "text": f"{str(item.get('name') or '').strip()} 出演作品",
            "link_kind": "fanza_performer_page",
        }
        for item in source.get("fanza_performer_pages") or []
        if isinstance(item, dict)
        and str(item.get("name") or "").strip()
        and str(item.get("url") or "").strip()
    ]
    # Prefer the per-product Free Video Tool iframe over a raw MP4 URL.
    # A 404 means that this product has no official embeddable sample.
    embed_video = _fanza_free_video_embed(product_id)
    official_videos = [embed_video] if embed_video else _fanza_sample_videos(source, product_id)
    copyright_reference = _text(
        source.get("body_text"),
        *(source.get("text_blocks") or []),
        *(source.get("excerpts") or []),
        source.get("description"),
    )
    source.update({
        "images": selected_images,
        "videos": official_videos,
        "links": [
            {"url": source_url, "text": "FANZA商品ページ"},
            *performer_links,
        ],
        "browser_attachments": [],
        "excerpts": [],
        "text_blocks": [],
        "body_text": "",
        "description": "",
        "_copyright_reference_text": copyright_reference,
        "media_rights_profile": FANZA_MEDIA_PROFILE,
        "fanza_product_id": product_id,
        "canonical_product_url": canonical_fanza_product_url(source_url),
        "ai_adult_content": True,
        "ai_adult_reason": "FANZAの成人向け個別商品ページと商品パッケージを確認しました。",
        "ai_analysis_summary": "FANZAの個別商品ページを、同じ商品IDの公式パッケージ・商品紹介画像で紹介します。",
        "ai_fanza_relevance": "exact_product",
        "ai_fanza_product_code": str(
            source.get("fanza_maker_code")
            or source.get("fanza_distribution_code")
            or product_id
        ),
        "fanza_people": official_people,
        "ai_fanza_people": official_people,
        "ai_fanza_performer_name": (
            str(official_people[0].get("name") or "")
            if len(official_people) == 1 else ""
        ),
        "recommended_image_ids": [item["id"] for item in selected_images],
        "recommended_thumbnail_ids": ["media-1"],
        "recommended_body_image_ids": [item["id"] for item in selected_images],
        "recommended_video_ids": [item["id"] for item in official_videos],
    })
    intent = dict(source.get("editorial_intent") or {})
    intent.update({"content_mode": "fanza_product", "promotion_type": "affiliate"})
    source["editorial_intent"] = intent
    return source


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def _contains_any(value: str, markers: tuple[str, ...]) -> list[str]:
    folded = value.casefold()
    return [marker for marker in markers if marker.casefold() in folded]


def _main_media_count(source: dict[str, Any]) -> int:
    image_ids = source.get("recommended_image_ids") or []
    video_ids = source.get("recommended_video_ids") or []
    return len(image_ids) + len(video_ids)


def assess_analyzed_source(source: dict[str, Any]) -> PolicyDecision:
    reasons: list[str] = []
    reviewed_text = _text(
        source.get("title"),
        source.get("description"),
        source.get("ai_analysis_summary"),
        source.get("ai_adult_reason"),
    )
    analysis_text = _text(
        reviewed_text,
        *(source.get("excerpts") or [])[:8],
    )

    if source.get("ai_adult_content") is not True:
        reasons.append("本編を成人向けと確認できません")

    # Capture excerpts can include sidebars and unrelated recommendation titles.
    # Risk rejection must use the page title plus Codex's reviewed description,
    # not unscoped footer text from the source site.
    risk_hits = _contains_any(reviewed_text, DISALLOWED_RISK_MARKERS)
    if risk_hits:
        reasons.append("審査上扱わない題材を含みます: " + "、".join(risk_hits[:4]))

    adult_hits = _contains_any(analysis_text, ADULT_MARKERS)
    general_hits = _contains_any(analysis_text, GENERAL_TOPIC_MARKERS)
    exact_adult_product = (
        source.get("ai_fanza_relevance") in {"exact_product", "likely_product"}
        and bool(source.get("ai_fanza_product_code") or source.get("verified_fanza_products"))
    )
    if not adult_hits and not exact_adult_product:
        reasons.append("本編に具体的な成人向け根拠がありません")
    if general_hits and not exact_adult_product:
        reasons.append("一般記事の可能性が高い題材です: " + "、".join(general_hits[:3]))

    if _main_media_count(source) == 0 and not exact_adult_product:
        reasons.append("本編として確認済みの画像・動画・成人向け商品がありません")

    adult_reason = str(source.get("ai_adult_reason") or "").strip()
    if len(adult_reason) < 12:
        reasons.append("成人向け判定の具体的根拠が不足しています")

    return PolicyDecision(not reasons, tuple(dict.fromkeys(reasons)))


def _normalize_for_overlap(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _article_authored_text(payload: dict[str, Any]) -> str:
    blocks = payload.get("blocks") or []
    responses = [
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "post"
    ]
    return _text(payload.get("summary"), *responses)


def check_originality(source: dict[str, Any], payload: dict[str, Any]) -> PolicyDecision:
    reasons: list[str] = []
    authored = _normalize_for_overlap(_article_authored_text(payload))
    source_text = _normalize_for_overlap(_text(
        source.get("_copyright_reference_text"),
        source.get("body_text"),
        *(source.get("text_blocks") or []),
        *(source.get("excerpts") or []),
        source.get("description"),
    ))

    if len(authored) < 120:
        reasons.append("独自に書かれた本文量が不足しています")
    if not str(payload.get("source_url") or source.get("url") or "").startswith(("http://", "https://")):
        reasons.append("参照元URLがありません")

    # Exact long passages are rejected. Names and short factual phrases can still
    # match, while copied paragraphs cannot pass this check.
    if source_text and authored:
        chunk_size = 72
        for start in range(0, max(1, len(authored) - chunk_size + 1), 12):
            chunk = authored[start:start + chunk_size]
            if len(chunk) == chunk_size and chunk in source_text:
                reasons.append("元ページと長く一致する文章があります")
                break

    return PolicyDecision(not reasons, tuple(dict.fromkeys(reasons)))


def approve_generated_article(source: dict[str, Any], payload: dict[str, Any]) -> None:
    adult = assess_analyzed_source(source)
    original = check_originality(source, payload)
    rights_reasons: list[str] = []
    source_url = str(source.get("requested_url") or source.get("url") or "")
    product_id = fanza_product_id(source_url)
    is_fanza_article = (
        str(payload.get("content_mode") or "") == "fanza_product"
        or bool(product_id)
    )
    if not product_id:
        rights_reasons.append("FANZAの個別商品ページを元にした記事ではありません")
    if str(payload.get("fanza_product_id") or "").casefold() != product_id:
        rights_reasons.append("記事の商品IDが参照元の商品IDと一致しません")
    if str(payload.get("content_mode") or "") != "fanza_product":
        rights_reasons.append("FANZA作品紹介モードではありません")
    if str(payload.get("media_rights_profile") or "") != FANZA_MEDIA_PROFILE:
        rights_reasons.append("公式商品素材の利用記録がありません")
    images = [item for item in payload.get("images") or [] if isinstance(item, dict)]
    if not images:
        rights_reasons.append("許可範囲の商品メイン画像がありません")
    allowed_image_rights = {
        "fanza_product_main_image",
        "fanza_product_sample_image",
    }
    for image in images:
        if image.get("rights_basis") not in allowed_image_rights:
            rights_reasons.append("FANZA公式の商品画像以外が含まれています")
            break
        if not is_fanza_official_image_url(str(image.get("source_url") or "")):
            rights_reasons.append("画像の配信元がDMM公式ではありません")
            break
        if fanza_image_product_id(str(image.get("source_url") or "")) != product_id:
            rights_reasons.append("別商品のパッケージ画像が含まれています")
            break
    videos = [item for item in payload.get("videos") or [] if isinstance(item, dict)]
    allowed_video_rights = {
        "fanza_official_share_embed",
        "fanza_free_video_tool_embed",
    }
    for video in videos:
        if video.get("rights_basis") not in allowed_video_rights:
            rights_reasons.append("FANZA共有機能以外の動画が含まれています")
            break
    product_ctas = [
        block for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "product_cta"
    ]
    if not product_ctas:
        rights_reasons.append("紹介作品そのものへの商品リンクがありません")
    elif any(fanza_product_id(str(block.get("url") or "")) != product_id for block in product_ctas):
        rights_reasons.append("商品リンクが参照元とは別の商品を指しています")
    if not is_fanza_article:
        # Ordinary web/X articles have their own review path.  They must pass
        # adult-content and originality checks, but must not be rejected for
        # lacking a FANZA product ID, official gallery or product CTA.
        rights_reasons.clear()
        if str(payload.get("content_mode") or "") not in {"web", "x_post", "x_account"}:
            rights_reasons.append("通常記事の掲載形式を判定できません")
        if not (payload.get("images") or payload.get("videos") or payload.get("x_embed")):
            rights_reasons.append("記事に使える画像または動画がありません")
        payload["media_rights_profile"] = str(
            payload.get("media_rights_profile") or "source-page-reviewed"
        )
    reasons = (*adult.reasons, *original.reasons, *rights_reasons)
    payload["editorial_policy_version"] = POLICY_VERSION
    payload["editorial_policy_status"] = "adult_approved" if not reasons else "rejected"
    payload["editorial_policy_reasons"] = list(dict.fromkeys(reasons))
    payload["originality_checked"] = original.allowed
    payload["ai_adult_content"] = source.get("ai_adult_content") is True
    payload["ai_adult_reason"] = str(source.get("ai_adult_reason") or "")[:500]
    if reasons:
        raise EditorialPolicyError(
            "成人向け専用サイトの掲載基準を満たしません: " + " / ".join(reasons)
        )


def assess_saved_article(payload: dict[str, Any]) -> PolicyDecision:
    text = _text(
        payload.get("title"), payload.get("summary"), payload.get("description"),
        payload.get("ai_adult_reason"), *(payload.get("tags") or []),
    )
    reasons: list[str] = []
    risk_hits = _contains_any(text, DISALLOWED_RISK_MARKERS)
    if risk_hits:
        reasons.append("審査上扱わない題材を含みます: " + "、".join(risk_hits[:4]))
    if not _contains_any(text, ADULT_MARKERS):
        reasons.append("成人向け記事だと明確に確認できません")
    if _contains_any(text, GENERAL_TOPIC_MARKERS):
        reasons.append("一般記事の可能性が高い題材です")
    if payload.get("adult_confirmed") is not True:
        reasons.append("成人向け確認が完了していません")
    if not str(payload.get("source_url") or "").startswith(("http://", "https://")):
        reasons.append("参照元URLがありません")
    common_reason_count = len(reasons)
    source_url = str(payload.get("source_url") or "")
    product_id = fanza_product_id(source_url)
    is_fanza_article = payload.get("content_mode") == "fanza_product" or bool(product_id)
    if not product_id:
        reasons.append("FANZAの個別商品ページ以外を参照しています")
    if str(payload.get("fanza_product_id") or "").casefold() != product_id:
        reasons.append("記事の商品IDが参照元の商品IDと一致しません")
    if payload.get("content_mode") != "fanza_product":
        reasons.append("FANZA作品紹介記事ではありません")
    if payload.get("media_rights_profile") != FANZA_MEDIA_PROFILE:
        reasons.append("公式商品素材だけで作られた記録がありません")
    allowed_image_rights = {
        "fanza_product_main_image",
        "fanza_product_sample_image",
    }
    for image in payload.get("images") or []:
        if not isinstance(image, dict) or image.get("rights_basis") not in allowed_image_rights:
            reasons.append("許可範囲外の画像が含まれています")
            break
        if not is_fanza_official_image_url(str(image.get("source_url") or "")):
            reasons.append("画像の配信元がDMM公式ではありません")
            break
        if fanza_image_product_id(str(image.get("source_url") or "")) != product_id:
            reasons.append("別商品のパッケージ画像が含まれています")
            break
    allowed_video_rights = {
        "fanza_official_share_embed",
        "fanza_free_video_tool_embed",
    }
    for video in payload.get("videos") or []:
        if not isinstance(video, dict) or video.get("rights_basis") not in allowed_video_rights:
            reasons.append("許可範囲外の動画が含まれています")
            break
    product_ctas = [
        block for block in payload.get("blocks") or []
        if isinstance(block, dict) and block.get("type") == "product_cta"
    ]
    if not product_ctas:
        reasons.append("紹介作品そのものへの商品リンクがありません")
    elif any(fanza_product_id(str(block.get("url") or "")) != product_id for block in product_ctas):
        reasons.append("商品リンクが参照元とは別の商品を指しています")
    if not is_fanza_article:
        reasons = reasons[:common_reason_count]
        if str(payload.get("content_mode") or "") not in {"web", "x_post", "x_account"}:
            reasons.append("通常記事の掲載形式を判定できません")
        if not (payload.get("images") or payload.get("videos") or payload.get("x_embed")):
            reasons.append("記事に使える画像または動画がありません")
    return PolicyDecision(not reasons, tuple(dict.fromkeys(reasons)))


def require_publishable_article(payload: dict[str, Any]) -> None:
    decision = assess_saved_article(payload)
    if not decision.allowed:
        raise RuntimeError("公開前の成人向け審査に通りません: " + decision.message)
    if payload.get("editorial_policy_version") != POLICY_VERSION:
        raise RuntimeError("現行のFANZA審査向け生成基準を通っていない記事です")
    if payload.get("editorial_policy_status") != "adult_approved":
        raise RuntimeError("生成時の成人向け審査が完了していません")
    if payload.get("originality_checked") is not True:
        raise RuntimeError("元ページとの文章重複検査が完了していません")
    if payload.get("content_mode") == "fanza_product":
        title = str(payload.get("title") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        vague_title_phrases = (
            "情報量が多い",
            "パッケージが強い",
            "パケが強い",
            "全力すぎる",
            "直球すぎる",
            "押し切るパッケージ",
            "パッケージ全面で渋滞",
        )
        if len(title) < 18 or any(phrase in title for phrase in vague_title_phrases):
            raise RuntimeError("作品内容が一読できない曖昧なタイトルです")
        if len(summary) < 50:
            raise RuntimeError("作品内容を判断できる概要が不足しています")
        response_texts = [
            str(block.get("text") or "").strip()
            for block in payload.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "post"
        ]
        if len(response_texts) < 3 or len(set(response_texts)) != len(response_texts):
            raise RuntimeError("本文のレスが不足または重複しています")
        image_ids = {
            str(image.get("id")) for image in payload.get("images") or []
            if isinstance(image, dict) and image.get("id")
        }
        placed_image_ids = [
            str(image_id)
            for block in payload.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "images"
            for image_id in block.get("image_ids") or []
            if isinstance(image_id, str)
        ]
        if len(image_ids) < 2:
            raise RuntimeError("作品内容が分かる公式商品紹介画像が不足しています")
        if set(placed_image_ids) != image_ids or len(placed_image_ids) != len(set(placed_image_ids)):
            raise RuntimeError("本文画像の欠落または重複があります")
        if payload.get("media_alignment_checked") is not True:
            raise RuntimeError("レスと画像の対応検査が完了していません")
