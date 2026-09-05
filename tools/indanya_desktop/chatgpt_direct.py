from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urldefrag, urlparse

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:  # The desktop worker installs Pillow; keep CLI tools usable without it.
    Image = ImageDraw = ImageOps = None

from article_studio import (
    _codex_analysis_prompt,
    _codex_prompt,
    _codex_refinement_prompt,
    _recent_draft_language,
    _validate_codex_analysis,
    _validate_codex_result,
    _validate_source_url,
    analyze_source_url,
    apply_codex_analysis,
)
from indanya_desktop.browser_capture import (
    ChatGptRateLimitError,
    MAX_ANALYSIS_IMAGES,
    MAX_ANALYSIS_VIDEOS,
    _sheet_attachments,
    capture_fanza_product_metadata,
    capture_rendered_source,
    send_chatgpt_prompt,
)
from indanya_desktop.editorial_policy import (
    EditorialPolicyError,
    is_fanza_product_url,
    restrict_source_to_fanza_product,
)
from indanya_desktop.site_learning import (
    get_site_plan,
    learning_prompt_context,
    prioritize_source_media,
    record_fast_path_probe,
)


ProgressCallback = Callable[[int, str], None]

CONTACT_SHEET_ITEMS = 12
CONTACT_SHEET_COLUMNS = 3
CONTACT_SHEET_CELL = (420, 330)

ANALYSIS_SCHEMA = """
返答JSONの必須構造:
{
  "title": "ページ内容を表す自然な題名",
  "description": "記事本編の説明",
  "category": "SNS|画像|動画|話題",
  "page_role": "article|gateway|index|unclear",
  "follow_url": "gatewayの場合だけ、提示されたリンク一覧にある次のURL",
  "follow_reason": "遷移理由",
  "analysis_summary": "判定根拠",
  "adult_content": true,
  "adult_reason": "成人向け／一般向けと判断した具体的根拠",
  "fanza_relevance": "none|related|likely_product|exact_product",
  "fanza_performer_name": "",
  "fanza_search_query": "",
  "fanza_product_code": "",
  "fanza_reason": "",
  "fanza_people": [{"name":"","image_ids":["media-1"],"reason":""}],
  "fanza_image_products": [{
    "product_title":"","product_code":"","product_url":"",
    "image_ids":["media-1"],"reason":""
  }],
  "fanza_recommendation_queries": [],
  "image_decisions": [{
    "image_id":"media-1","verdict":"article|advertisement|logo|navigation|unrelated|unclear",
    "role":"ページ固有の自由記述","recommended_use":"thumbnail|body|thumbnail_and_body|exclude",
    "content_group":"","relation":"","relevance_score":0,"reason":""
  }],
  "video_decisions": [{
    "video_id":"video-1","verdict":"article|advertisement|navigation|unrelated|unclear",
    "relevance_score":0,"reason":""
  }]
}
画像候補と動画候補は、件数が多くても必ず全IDを一度ずつ判定してください。
JSON以外は一切返さないでください。
"""


ARTICLE_SELF_REVIEW = """
出力前に、同じ回答の中で必ず編集者としてもう一度読み直してください。
最初の案をそのまま返さず、タイトルから記事の対象と見どころが分かるか、各レスが
画像・動画の実際の内容に合うか、会話が不自然でないか、同じ語尾や決まり文句を
繰り返していないかを点検して修正し、推敲後の完成稿だけをJSONで返してください。
"""

BATCH_ARTICLE_SCHEMA = """
返却JSONの形式:
{
  "articles": [
    {
      "request_id": "入力に記載されたIDをそのまま返す",
      "article": {
        "title": "記事タイトル",
        "summary": "記事概要",
        "category": "SNS|画像|動画|話題",
        "tags": ["タグ"],
        "responses": [
          {"text":"自然な1レス","style":"normal|large|highlight","image_ids":[],"video_ids":[]}
        ]
      }
    }
  ]
}
入力された全request_idを1回ずつ返してください。JSON以外は返さないでください。
"""

FANZA_ARTICLE_GUIDE = """あなたは成人向けサイト『淫談屋』の編集者です。
以下のFANZA個別商品を、公式パッケージと同一商品IDの公式紹介画像を見て、短い5ch風記事にしてください。

- 商品ごとに完全に独立させ、人物・作品名・画像・商品IDを別商品へ混ぜない。
- タイトルは作品名の丸写しや広告文にせず、誰のどんな作品・場面か一読できる自然な日本語にする。
- 画像で確認できる衣装、場所、構図、登場人数、場面の違いを具体的に拾う。画像にない行為・順番・感情・経歴は作らない。
- 公式説明、レビュー、評価をコピー・要約しない。視聴したふりもしない。
- レスは別々の人が画像を見て書く短い反応にする。同じ形容、語尾、身体語、「でかすぎ」「エロすぎ」などを連発しない。
- 成人作品として確認できる性的内容は不自然にぼかさず書けるが、下品さだけを競わない。
- 各レスのimage_idsには、そのレスが実際に触れる画像IDだけを最大3件入れる。無理に全画像へコメントせず、未指定画像はPC側がギャラリーへ配置する。
- summaryは作品内容を判断できる簡潔な紹介にする。購入の強要、効果保証、未確認情報は禁止。
- categoryは画像、tagsは作品固有の出演者・メーカー・ジャンルなど確認できる語だけにする。
- 指定されたレス数を守り、JSON以外を返さない。
"""


class NonAdultSourceError(RuntimeError):
    """The source is outside this adult-only site's editorial scope."""

ARTICLE_SCHEMA = """
返答JSONの必須構造:
{
  "title": "記事タイトル",
  "summary": "記事概要",
  "category": "SNS|画像|動画|話題",
  "tags": ["タグ"],
  "responses": [
    {"text":"自然な1レス","style":"normal|large|highlight","image_ids":["media-1"],"video_ids":["video-1"]}
  ]
}
video_idsを付けたレスは第三者の感想ではなく、その動画を投稿する側の文として自然にしてください。
画像はPC側が判定済みの順序で配置するため、responsesへ画像IDは不要です。
JSON以外は一切返さないでください。
"""

SINGLE_PASS_ARTICLE_SCHEMA = """
同じ1回の返答で、素材判定に加えて完成記事も作ってください。
最終JSONは解析項目をそのまま含み、さらに次のarticleを1個だけ追加します:
{
  "title": "ページ内容を表す自然な題名",
  "description": "記事本編の説明",
  "category": "SNS|画像|動画|話題",
  "page_role": "article|gateway|index|unclear",
  "follow_url": "",
  "follow_reason": "",
  "analysis_summary": "判定根拠",
  "adult_content": true,
  "adult_reason": "判定根拠",
  "fanza_relevance": "none|related|likely_product|exact_product",
  "fanza_performer_name": "",
  "fanza_search_query": "",
  "fanza_product_code": "",
  "fanza_reason": "",
  "fanza_people": [],
  "fanza_image_products": [],
  "fanza_recommendation_queries": [],
  "image_decisions": [],
  "video_decisions": [],
  "article": {
    "title": "記事タイトル",
    "summary": "記事概要",
    "category": "SNS|画像|動画|話題",
    "tags": ["記事固有のタグ"],
    "responses": [
      {"text":"自然な1レス","style":"normal|large|highlight","image_ids":[],"video_ids":[]}
    ]
  }
}
articleは、image_decisionsとvideo_decisionsでarticle判定したIDだけを使ってください。
adult_content=falseまたはpage_roleがarticle以外でもarticleキーは省略せず、確認できた事実だけで返してください。
JSON以外は一切返さないでください。
"""


def _is_fanza_product_source(source: dict[str, Any]) -> bool:
    intent = source.get("editorial_intent")
    return (
        isinstance(intent, dict)
        and intent.get("content_mode") == "fanza_product"
        and bool(source.get("fanza_product_id"))
    )


def _compact_fanza_task_prompt(
    source: dict[str, Any],
    options: dict[str, Any],
    records: list[dict[str, Any]],
) -> str:
    requested_count = options.get("reply_count", "auto")
    reply_count = int(requested_count) if str(requested_count) in {"5", "8", "10"} else 8
    attachment_numbers: dict[str, int] = {}
    image_manifest: list[dict[str, Any]] = []
    for item in records:
        filename = str(item.get("filename") or "")
        if filename not in attachment_numbers:
            attachment_numbers[filename] = len(attachment_numbers) + 1
        image_manifest.append({
            "image_id": str(item.get("id") or ""),
            "attachment": attachment_numbers[filename],
            "cell": item.get("contact_sheet_cell"),
        })
    facts = {
        "product_id": source.get("fanza_product_id"),
        "product_url": source.get("canonical_product_url") or source.get("url"),
        "official_page_title": source.get("title"),
        "reply_count": reply_count,
        "body_image_count": len(options.get("selected_image_ids") or []),
        "image_manifest": image_manifest,
        "recent_phrases_to_avoid": list(options.get("recent_language") or [])[:12],
    }
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"))


def extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.S | re.I)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", candidate):
        try:
            value, _ = decoder.raw_decode(candidate[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("ChatGPTの返答からJSONを読み取れませんでした")


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"binary_bytes": len(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def save_evidence_package(
    site_root: Path,
    request_id: str,
    depth: int,
    source: dict[str, Any],
) -> list[Path]:
    package = (
        site_root / ".article-studio" / "chatgpt-direct"
        / request_id / f"page-{depth + 1}"
    )
    package.mkdir(parents=True, exist_ok=True)
    attachment_paths: list[Path] = []
    for index, item in enumerate(source.get("browser_attachments") or [], start=1):
        if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
            continue
        filename = Path(str(item.get("filename") or f"evidence-{index}.jpg")).name
        path = package / filename
        path.write_bytes(item["data"])
        attachment_paths.append(path)
    manifest = {
        key: _json_safe(value)
        for key, value in source.items()
        if key not in {"browser_attachments"}
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return attachment_paths


def _request_validated_json(
    prompt: str,
    attachment_paths: list[Path],
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    progress: ProgressCallback,
    conversation: dict[str, str] | None = None,
) -> dict[str, Any]:
    last_error = ""
    previous = ""
    active_conversation = conversation if conversation is not None else {}
    # One article consumes at most one ChatGPT answer. Retrying a malformed JSON
    # response with the same large visual payload burns the free-plan allowance
    # faster than moving on to a fresh candidate.
    for attempt in range(1):
        request = prompt
        if attempt:
            request += (
                "\n\n前回の返答は検査に不合格でした。"
                f"\n検査エラー: {last_error}"
                "\n前回返答:\n"
                + previous[:30000]
                + "\n不足や不整合を直し、指定JSONだけを返してください。"
            )
        transport_error = ""
        result: dict[str, str] | None = None
        # One short retry handles a transient browser failure without keeping a
        # single article in a processing state for tens of minutes.
        for transport_attempt in range(2):
            try:
                result = send_chatgpt_prompt(
                    request,
                    progress,
                    attachment_paths=(
                        [] if attempt and active_conversation.get("url")
                        else attachment_paths
                    ),
                    conversation_url=str(active_conversation.get("url") or ""),
                )
                break
            except ChatGptRateLimitError:
                # A plan limit is not a malformed request. Let the queue worker
                # defer the whole run instead of burning its retry budget.
                raise
            except Exception as exc:
                transport_error = str(exc) or exc.__class__.__name__
                bounded_wait_failure = any(term in transport_error for term in (
                    "生成を開始しませんでした",
                    "返答が5分以内に完了",
                    "返答が90秒間進まなかった",
                    "45秒以内に完了",
                    "ファイルを添付できる状態",
                    "証拠画像アップロードが完了",
                ))
                if bounded_wait_failure:
                    raise RuntimeError(
                        "ChatGPT画面でこの記事を処理できませんでした: "
                        + transport_error
                    ) from exc
                if transport_attempt >= 1:
                    raise RuntimeError(
                        "ChatGPT画面でこの記事を処理できませんでした: "
                        + transport_error
                    ) from exc
                progress(12, "ChatGPT画面を1回だけ開き直しています")
                time.sleep(2)
        if result is None:
            raise RuntimeError(transport_error or "ChatGPTから返答を取得できませんでした")
        if result.get("conversation_url"):
            active_conversation["url"] = str(result["conversation_url"])
            if conversation is not None:
                conversation["url"] = active_conversation["url"]
        previous = str(result.get("message") or "")
        try:
            return validator(extract_json_object(previous))
        except Exception as exc:
            last_error = str(exc) or exc.__class__.__name__
            progress(45, f"ChatGPTの返答を保存できませんでした: {last_error}")
    raise RuntimeError(f"ChatGPTの記事判断が検査を通りませんでした: {last_error}")


def _validate_complete_analysis(
    value: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    # A visually valid answer occasionally omits one descriptive field.  Those
    # fields can be recovered from the captured page and do not justify sending
    # the same large job to ChatGPT again.  Safety decisions remain explicit:
    # absent adult_content stays False and missing media decisions are excluded.
    source_title = str(source.get("title") or "").strip()
    source_description = str(
        source.get("description") or source.get("body_text") or source_title
    ).strip()
    if not isinstance(value.get("title"), str) or not value.get("title", "").strip():
        value["title"] = source_title[:180] or "元ページの内容"
    if not isinstance(value.get("description"), str) or not value.get("description", "").strip():
        value["description"] = source_description[:500] or value["title"]
    if value.get("category") not in {"SNS", "画像", "動画", "話題"}:
        value["category"] = "動画" if source.get("videos") else "画像"
    if not isinstance(value.get("analysis_summary"), str) or not value.get("analysis_summary", "").strip():
        value["analysis_summary"] = source_description[:500] or value["title"]
    if not isinstance(value.get("adult_reason"), str) or not value.get("adult_reason", "").strip():
        value["adult_reason"] = "成人向けか判断できる説明が返されませんでした"
    value.setdefault("page_role", "article")
    value.setdefault("follow_url", "")
    value.setdefault("follow_reason", "")
    value.setdefault("fanza_relevance", "none")
    value.setdefault("fanza_performer_name", "")
    value.setdefault("fanza_search_query", "")
    value.setdefault("fanza_product_code", "")
    value.setdefault("fanza_reason", "")
    for key in (
        "fanza_people", "fanza_image_products", "fanza_recommendation_queries",
        "image_decisions", "video_decisions",
    ):
        if not isinstance(value.get(key), list):
            value[key] = []
    expected_images = {
        str(item.get("id") or "")
        for item in (source.get("images") or [])
        if isinstance(item, dict) and item.get("id")
    }
    returned_images = {
        str(item.get("image_id") or "")
        for item in (value.get("image_decisions") or [])
        if isinstance(item, dict)
    }
    expected_videos = {
        str(item.get("id") or "")
        for item in (source.get("videos") or [])
        if isinstance(item, dict) and item.get("id")
    }
    returned_videos = {
        str(item.get("video_id") or "")
        for item in (value.get("video_decisions") or [])
        if isinstance(item, dict)
    }
    missing_images = sorted(expected_images - returned_images)
    missing_videos = sorted(expected_videos - returned_videos)
    # Hallucinated IDs can be discarded locally. Sending the complete visual job
    # again only burns a second ChatGPT message without improving captured data.
    value["image_decisions"] = [
        item for item in (value.get("image_decisions") or [])
        if isinstance(item, dict) and str(item.get("image_id") or "") in expected_images
    ]
    value["video_decisions"] = [
        item for item in (value.get("video_decisions") or [])
        if isinstance(item, dict) and str(item.get("video_id") or "") in expected_videos
    ]
    image_decisions = value.setdefault("image_decisions", [])
    for image_id in missing_images:
        image_decisions.append({
            "image_id": image_id,
            "verdict": "unclear",
            "role": "AI未判定",
            "recommended_use": "exclude",
            "content_group": "",
            "relation": "",
            "relevance_score": 0,
            "reason": "応答に判定がなかったため安全側で除外",
        })
    video_decisions = value.setdefault("video_decisions", [])
    for video_id in missing_videos:
        video_decisions.append({
            "video_id": video_id,
            "verdict": "unclear",
            "relevance_score": 0,
            "reason": "応答に判定がなかったため安全側で除外",
        })
    raw_article = value.get("article")
    result = _validate_codex_analysis(value, source)
    if isinstance(raw_article, dict):
        result["article"] = raw_article
    return result


def _merge_x_semantics(source: dict[str, Any], semantic: dict[str, Any]) -> None:
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*(source.get("images") or []), *(semantic.get("images") or [])]:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        digest = (
            hashlib.sha256(data).hexdigest()
            if isinstance(data, bytes)
            else str(item.get("url") or "")
        )
        if not digest or digest in seen:
            continue
        seen.add(digest)
        combined.append({**item, "id": f"media-{len(combined) + 1}"})
    source.update({
        "source_type": semantic.get("source_type", "x_post"),
        "x_info": semantic.get("x_info", {}),
        "x_embed": semantic.get("x_embed"),
        "site_name": "X",
        "author": str((semantic.get("x_embed") or {}).get("author_name") or ""),
        "images": combined,
    })
    if semantic.get("description"):
        source["description"] = semantic["description"]


def _semantic_fast_path_ready(source: dict[str, Any], plan: dict[str, Any]) -> tuple[bool, str]:
    title = str(source.get("title") or "").strip()
    text_count = len(source.get("excerpts") or source.get("text_blocks") or [])
    image_count = len(source.get("images") or [])
    video_count = len(source.get("videos") or [])
    expected_images = float(plan.get("expected_images") or 0)
    expected_videos = float(plan.get("expected_videos") or 0)
    minimum_images = max(1, min(6, round(expected_images * 0.65))) if expected_images else 2
    if len(title) < 4:
        return False, "タイトルを取得できませんでした"
    if text_count < 1:
        return False, "本文を取得できませんでした"
    if expected_videos >= 0.5:
        return False, "動画実績のあるURL型は完全取得を優先します"
    if video_count:
        return False, "動画を検出したため完全取得へ切り替えます"
    if image_count < minimum_images:
        return False, f"画像が学習下限に不足しました（{image_count}/{minimum_images}）"
    return True, f"静的取得で画像{image_count}枚・本文{text_count}件を確認"


def _record_fast_path_probe_safely(
    site_root: Path,
    url: str,
    success: bool,
    message: str = "",
) -> None:
    try:
        record_fast_path_probe(site_root, url, success, message)
    except Exception:
        # Learning is advisory. A damaged or locked ledger must never stop an
        # article that can otherwise be completed.
        traceback.print_exc()


def _prepare_semantic_source(source: dict[str, Any], strategy: str) -> dict[str, Any]:
    result = dict(source)
    images = [item for item in (result.get("images") or []) if isinstance(item, dict)]
    article_images = [item for item in images if item.get("inside_article")]
    if article_images:
        article_urls = {str(item.get("url") or "") for item in article_images}
        metadata_images = [
            item for item in images
            if item.get("source_hint") == "metadata"
            and str(item.get("url") or "") not in article_urls
        ]
        images = article_images + metadata_images
    images = images[:MAX_ANALYSIS_IMAGES]
    videos = [item for item in (result.get("videos") or []) if isinstance(item, dict)]
    article_videos = [item for item in videos if item.get("inside_article")]
    if article_videos:
        videos = article_videos
    result["images"] = images
    result["videos"] = videos[:MAX_ANALYSIS_VIDEOS]
    result["text_blocks"] = list(result.get("excerpts") or [])
    result["body_text"] = "\n".join(str(value) for value in result["text_blocks"])
    result["browser_capture"] = False
    result["capture_strategy"] = strategy
    result["browser_attachments"] = _sheet_attachments(
        images,
        prefix="learned-site-images",
        kind="contact_sheet",
    ) if images else []
    return result


def _merge_source_candidates(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    result = dict(primary)
    for key in ("title", "description", "site_name", "author"):
        if not result.get(key) and secondary.get(key):
            result[key] = secondary[key]
    for key in ("excerpts", "text_blocks"):
        values = [
            str(value) for value in [*(result.get(key) or []), *(secondary.get(key) or [])]
            if str(value).strip()
        ]
        result[key] = list(dict.fromkeys(values))[:80]
    if not result.get("body_text"):
        result["body_text"] = "\n".join(result.get("text_blocks") or result.get("excerpts") or [])

    images: list[dict[str, Any]] = []
    image_indexes: dict[str, int] = {}
    primary_images = [
        item for item in (result.get("images") or []) if isinstance(item, dict)
    ]
    secondary_images = [
        item for item in (secondary.get("images") or []) if isinstance(item, dict)
    ]
    body_image_urls = {
        str(item.get("url") or "")
        for item in [*primary_images, *secondary_images]
        if item.get("inside_article") and str(item.get("url") or "")
    }
    secondary_has_body_video = any(
        item.get("inside_article")
        for item in (secondary.get("videos") or [])
        if isinstance(item, dict)
    )
    if body_image_urls:
        primary_images = [
            item for item in primary_images
            if item.get("inside_article") or str(item.get("url") or "") in body_image_urls
        ]
        secondary_images = [
            item for item in secondary_images
            if item.get("inside_article") or item.get("source_hint") == "metadata"
        ]
    elif secondary_has_body_video:
        metadata_urls = {
            str(item.get("url") or "")
            for item in secondary_images
            if item.get("source_hint") == "metadata" and str(item.get("url") or "")
        }
        primary_images = [
            item for item in primary_images
            if str(item.get("url") or "") in metadata_urls
        ]
        secondary_images = [
            item for item in secondary_images if item.get("source_hint") == "metadata"
        ]
    for item in [*primary_images, *secondary_images]:
        url_key = str(item.get("url") or "").split("#", 1)[0]
        data = item.get("data")
        key = url_key or (
            hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else ""
        )
        if not key:
            continue
        if key in image_indexes:
            existing = images[image_indexes[key]]
            for field, value in item.items():
                if field == "inside_article":
                    existing[field] = bool(existing.get(field) or value)
                elif field == "source_score":
                    existing[field] = max(int(existing.get(field) or 0), int(value or 0))
                elif not existing.get(field) and value:
                    existing[field] = value
            continue
        if len(images) >= MAX_ANALYSIS_IMAGES:
            continue
        image_indexes[key] = len(images)
        images.append({**item, "id": f"media-{len(images) + 1}"})
    result["images"] = images

    videos: list[dict[str, Any]] = []
    video_indexes: dict[str, int] = {}
    primary_videos = [
        item for item in (result.get("videos") or []) if isinstance(item, dict)
    ]
    secondary_videos = [
        item for item in (secondary.get("videos") or []) if isinstance(item, dict)
    ]
    body_video_urls = {
        str(item.get("url") or "")
        for item in [*primary_videos, *secondary_videos]
        if item.get("inside_article") and str(item.get("url") or "")
    }
    if body_video_urls:
        # Rendered capture can observe media requests created by JavaScript that
        # never appear in the static HTML. Those candidates have already passed
        # the browser-side ad and media checks, so the static parser must not
        # erase them merely because it found only one of several article videos.
        secondary_videos = [item for item in secondary_videos if item.get("inside_article")]
    for item in [*primary_videos, *secondary_videos]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("url") or "")
        if not key:
            continue
        if key in video_indexes:
            existing = videos[video_indexes[key]]
            for field, value in item.items():
                if field == "inside_article":
                    existing[field] = bool(existing.get(field) or value)
                elif not existing.get(field) and value:
                    existing[field] = value
            continue
        video_indexes[key] = len(videos)
        videos.append({**item, "id": f"video-{len(videos) + 1}"})
        if len(videos) >= MAX_ANALYSIS_VIDEOS:
            break
    result["videos"] = videos
    attachments = [
        item for item in (result.get("browser_attachments") or [])
        if isinstance(item, dict)
        and item.get("kind") not in {"contact_sheet", "video_contact_sheet"}
    ]
    if images:
        attachments.extend(_sheet_attachments(
            images,
            prefix="article-images",
            kind="contact_sheet",
        ))
    video_frames = [
        {"id": str(item["id"]), "data": item["frame_data"]}
        for item in videos if isinstance(item.get("frame_data"), bytes)
    ]
    if video_frames:
        attachments.extend(_sheet_attachments(
            video_frames,
            prefix="article-video-frames",
            kind="video_contact_sheet",
            chunk_size=12,
        ))
    result["browser_attachments"] = attachments
    return result


def capture_and_analyze(
    site_root: Path,
    source_url: str,
    request_id: str,
    progress: ProgressCallback,
    editorial_intent: dict[str, Any] | None = None,
    conversation: dict[str, str] | None = None,
) -> dict[str, Any]:
    current_url = _validate_source_url(source_url)
    initial_site_plan = get_site_plan(site_root, current_url)
    if is_fanza_product_url(current_url):
        source = capture_fanza_product_metadata(current_url, progress)
        if editorial_intent:
            safe_intent = dict(editorial_intent)
            safe_intent.pop("private_note", None)
            source["editorial_intent"] = safe_intent
        source["requested_url"] = source_url
        source = restrict_source_to_fanza_product(source)
        source["source_chain"] = [current_url]
        source["_chatgpt_evidence_paths"] = []
        source["capture_strategy"] = "fanza_official"
        source = prioritize_source_media(source, initial_site_plan)
        progress(35, "同じ商品の公式画像を並列取得しました")
        return source
    visited: set[str] = set()
    source_chain: list[str] = []
    navigation_context: dict[str, Any] = {}
    for depth in range(3):
        normalized = urldefrag(current_url)[0]
        if normalized in visited:
            break
        visited.add(normalized)
        source_chain.append(current_url)
        progress(8 + depth * 8, "PCがページ全体の素材を回収しています")
        hostname = (urlparse(current_url).hostname or "").lower()
        is_x = hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
        site_plan = get_site_plan(site_root, current_url)
        semantic: dict[str, Any] | None = None
        if is_x:
            semantic = analyze_source_url(current_url)
        source: dict[str, Any] | None = None
        if not is_x and site_plan.get("strategy") in {"semantic_trial", "semantic_fast"}:
            progress(10 + depth * 8, "このサイトで学習済みの高速取得を確認しています")
            try:
                semantic = analyze_source_url(current_url)
                ready, reason = _semantic_fast_path_ready(semantic, site_plan)
                if ready:
                    source = _prepare_semantic_source(semantic, "semantic_fast")
                    progress(22 + depth * 8, f"学習済みの高速取得を採用しました: {reason}")
                else:
                    _record_fast_path_probe_safely(site_root, current_url, False, reason)
                    progress(14 + depth * 8, f"高速取得では不足したため完全取得へ切り替えます: {reason}")
            except Exception as exc:
                _record_fast_path_probe_safely(site_root, current_url, False, str(exc))
                traceback.print_exc()
        if source is None:
            try:
                source = capture_rendered_source(current_url, progress)
                source["capture_strategy"] = "browser_full"
            except Exception:
                traceback.print_exc()
                source = semantic or analyze_source_url(current_url)
                source = _prepare_semantic_source(source, "semantic_fallback")
        expected_images = float(site_plan.get("expected_images") or 0)
        expected_videos = float(site_plan.get("expected_videos") or 0)
        if (
            source.get("capture_strategy") == "browser_full"
            and (
                len(source.get("images") or []) < max(1, min(6, round(expected_images * 0.5)))
                or len(source.get("videos") or []) < (1 if expected_videos >= 0.5 else 0)
            )
        ):
            try:
                semantic_recovery = semantic or analyze_source_url(current_url)
                source = _merge_source_candidates(source, semantic_recovery)
                source["capture_strategy"] = "browser_plus_semantic_recovery"
                progress(27 + depth * 8, "過去の成功数に足りない素材を別経路から補完しました")
            except Exception:
                traceback.print_exc()
        source = prioritize_source_media(source, site_plan)
        if semantic:
            _merge_x_semantics(source, semantic)
        if (
            source.get("source_type") == "x_profile"
            and source.get("browser_capture")
            and not source.get("x_authenticated")
            and int(source.get("x_timeline_media_count") or 0) == 0
        ):
            raise RuntimeError(
                "Xの投稿素材を表示できません。初回だけXログインを行ってください"
            )
        if navigation_context:
            source["navigation_context"] = navigation_context
        if editorial_intent:
            safe_intent = dict(editorial_intent)
            safe_intent.pop("private_note", None)
            source["editorial_intent"] = safe_intent
        source["requested_url"] = source_url
        attachments = save_evidence_package(
            site_root, request_id, depth, source
        )
        progress(30 + depth * 8, "ChatGPTがページ構造と全素材を判定しています")
        analysis_prompt = _codex_analysis_prompt(
            source,
            [
                {"id": path.stem, "filename": path.name, "kind": "browser_evidence"}
                for path in attachments
            ],
        )
        all_image_ids = [
            str(item.get("id") or "")
            for item in (source.get("images") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        all_video_ids = [
            str(item.get("id") or "")
            for item in (source.get("videos") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        article_prompt = _codex_prompt(
            source,
            {
                "category": str((editorial_intent or {}).get("category") or "auto"),
                "reply_count": str((editorial_intent or {}).get("reply_count") or "auto"),
                "selected_image_ids": all_image_ids,
                "selected_video_ids": all_video_ids,
                "generation_image_ids": all_image_ids,
                "generation_video_ids": all_video_ids,
                "recent_language": _recent_draft_language(site_root),
            },
            [],
            nested_article=True,
        )
        prompt = (
            analysis_prompt
            + learning_prompt_context(site_plan)
            + "\n\n"
            + ANALYSIS_SCHEMA
            + "\n\n=== 同じ返答内で作る完成記事の編集規則 ===\n"
            + article_prompt
            + "\n\n"
            + ARTICLE_SELF_REVIEW
            + "\n\n"
            + SINGLE_PASS_ARTICLE_SCHEMA
        )
        analysis = _request_validated_json(
            prompt,
            attachments,
            lambda value: _validate_complete_analysis(value, source),
            progress,
            conversation,
        )
        if analysis.get("adult_content") is not True:
            reason = str(analysis.get("adult_reason") or "一般向けの内容です")
            raise NonAdultSourceError(
                f"成人向けでないため記事を作成しませんでした: {reason}"
            )
        follow_url = str(analysis.get("follow_url") or "").strip()
        if analysis.get("page_role") == "gateway" and follow_url:
            allowed = {
                urldefrag(str(item.get("url") or ""))[0]
                for item in (source.get("links") or [])
                if isinstance(item, dict)
            }
            validated = _validate_source_url(follow_url)
            if urldefrag(validated)[0] in allowed and urldefrag(validated)[0] not in visited:
                link = next(
                    (
                        item for item in (source.get("links") or [])
                        if isinstance(item, dict)
                        and urldefrag(str(item.get("url") or ""))[0]
                        == urldefrag(validated)[0]
                    ),
                    {},
                )
                navigation_context = {
                    "from_url": str(source.get("url") or current_url),
                    "from_title": str(source.get("title") or ""),
                    "followed_url": validated,
                    "followed_link_text": str(link.get("text") or ""),
                    "follow_reason": str(analysis.get("follow_reason") or ""),
                }
                current_url = validated
                continue
        analyzed = apply_codex_analysis(source, analysis)
        analyzed["_single_pass_article"] = analysis.get("article")
        analyzed["requested_url"] = source_url
        analyzed["source_chain"] = source_chain
        analyzed["_chatgpt_evidence_paths"] = [str(path) for path in attachments]
        return analyzed
    raise RuntimeError("本編へのリンクを最後まで追跡できませんでした")


def validate_single_pass_article(
    source: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any] | None:
    raw = source.get("_single_pass_article")
    if not isinstance(raw, dict):
        return None
    return _validate_codex_result(
        raw,
        options.get("reply_count", "auto"),
        len(options.get("selected_image_ids") or []),
        selected_image_ids=list(options.get("selected_image_ids") or []),
        selected_video_ids=list(options.get("selected_video_ids") or []),
    )


def _prepare_selected_media(
    package: Path,
    source: dict[str, Any],
    options: dict[str, Any],
    filename_prefix: str = "",
) -> tuple[list[dict[str, Any]], list[Path]]:
    package.mkdir(parents=True, exist_ok=True)
    selected_image_ids = set(options.get("selected_image_ids") or [])
    content_records: list[dict[str, Any]] = []
    content_paths: list[Path] = []
    selected_images: list[dict[str, Any]] = []
    for item in (source.get("images") or []):
        if (
            not isinstance(item, dict)
            or str(item.get("id") or "") not in selected_image_ids
            or not isinstance(item.get("data"), bytes)
        ):
            continue
        selected_images.append(item)

    # Keep the package/lead image at full resolution. Remaining images are sent
    # as labelled contact sheets: the article still retains every original, but
    # browser upload and ChatGPT vision overhead become much smaller.
    if selected_images:
        lead = selected_images[0]
        extension = str(lead.get("extension") or ".jpg")
        if not extension.startswith("."):
            extension = f".{extension}"
        filename = f"{filename_prefix}{lead['id']}{extension}"
        path = package / filename
        path.write_bytes(lead["data"])
        content_paths.append(path)
        content_records.append({
            "id": str(lead["id"]),
            "filename": path.name,
            "data": lead["data"],
        })

    remaining = selected_images[1:]
    if remaining and Image is not None:
        for sheet_number, start in enumerate(
            range(0, len(remaining), CONTACT_SHEET_ITEMS), start=1
        ):
            group = remaining[start : start + CONTACT_SHEET_ITEMS]
            rows = math.ceil(len(group) / CONTACT_SHEET_COLUMNS)
            cell_width, cell_height = CONTACT_SHEET_CELL
            sheet = Image.new(
                "RGB",
                (cell_width * CONTACT_SHEET_COLUMNS, cell_height * rows),
                "white",
            )
            draw = ImageDraw.Draw(sheet)
            for cell_number, item in enumerate(group, start=1):
                column = (cell_number - 1) % CONTACT_SHEET_COLUMNS
                row = (cell_number - 1) // CONTACT_SHEET_COLUMNS
                x = column * cell_width
                y = row * cell_height
                label = f"{item['id']}  ({start + cell_number + 1}/{len(selected_images)})"
                try:
                    with Image.open(io.BytesIO(item["data"])) as opened:
                        image = ImageOps.exif_transpose(opened).convert("RGB")
                        fitted = ImageOps.contain(image, (cell_width - 16, cell_height - 42))
                        image_x = x + (cell_width - fitted.width) // 2
                        image_y = y + 34 + (cell_height - 42 - fitted.height) // 2
                        sheet.paste(fitted, (image_x, image_y))
                except Exception:
                    draw.text((x + 8, y + 42), "image decode failed", fill="red")
                draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), outline="#777777")
                draw.text((x + 8, y + 8), label, fill="black")
            sheet_path = package / f"{filename_prefix}contact-sheet-{sheet_number}.jpg"
            sheet.save(sheet_path, "JPEG", quality=90, optimize=True)
            content_paths.append(sheet_path)
            for cell_number, item in enumerate(group, start=1):
                content_records.append({
                    "id": str(item["id"]),
                    "filename": sheet_path.name,
                    "data": item["data"],
                    "contact_sheet_cell": cell_number,
                })
    else:
        for item in remaining:
            extension = str(item.get("extension") or ".jpg")
            if not extension.startswith("."):
                extension = f".{extension}"
            filename = f"{filename_prefix}{item['id']}{extension}"
            path = package / filename
            path.write_bytes(item["data"])
            content_paths.append(path)
            content_records.append({
                "id": str(item["id"]),
                "filename": path.name,
                "data": item["data"],
            })
    selected_video_ids = set(options.get("selected_video_ids") or [])
    for item in (source.get("videos") or []):
        if (
            not isinstance(item, dict)
            or str(item.get("id") or "") not in selected_video_ids
            or not isinstance(item.get("frame_data"), bytes)
        ):
            continue
        path = package / f"{filename_prefix}{item['id']}-frame.jpg"
        path.write_bytes(item["frame_data"])
        content_paths.append(path)
    return content_records, content_paths


def estimate_chatgpt_attachment_count(
    source: dict[str, Any], options: dict[str, Any]
) -> int:
    selected_image_ids = set(options.get("selected_image_ids") or [])
    image_count = sum(
        1
        for item in (source.get("images") or [])
        if isinstance(item, dict)
        and str(item.get("id") or "") in selected_image_ids
        and isinstance(item.get("data"), bytes)
    )
    if image_count <= 1 or Image is None:
        image_attachments = image_count
    else:
        image_attachments = 1 + math.ceil((image_count - 1) / CONTACT_SHEET_ITEMS)
    selected_video_ids = set(options.get("selected_video_ids") or [])
    video_attachments = sum(
        1
        for item in (source.get("videos") or [])
        if isinstance(item, dict)
        and str(item.get("id") or "") in selected_video_ids
        and isinstance(item.get("frame_data"), bytes)
    )
    return image_attachments + video_attachments


def _validate_article_batch(
    value: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {
        str(entry.get("request_id") or ""): entry
        for entry in entries
        if str(entry.get("request_id") or "")
    }
    rows = value.get("articles")
    if not isinstance(rows, list):
        raise RuntimeError("articles配列がありません")
    generated: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        request_id = str(row.get("request_id") or "")
        if request_id not in expected or request_id in seen:
            continue
        seen.add(request_id)
        entry = expected[request_id]
        options = dict(entry.get("options") or {})
        article = row.get("article")
        if not isinstance(article, dict):
            invalid[request_id] = "articleオブジェクトがありません"
            continue
        try:
            generated[request_id] = _validate_codex_result(
                article,
                options.get("reply_count", "auto"),
                len(options.get("selected_image_ids") or []),
                selected_image_ids=list(options.get("selected_image_ids") or []),
                selected_video_ids=list(options.get("selected_video_ids") or []),
            )
        except Exception as exc:
            invalid[request_id] = str(exc) or exc.__class__.__name__
    for request_id in expected.keys() - seen:
        invalid[request_id] = "返答に記事がありません"
    if not generated and invalid:
        raise RuntimeError("3記事すべてが検査不合格でした: " + "; ".join(invalid.values()))
    return {"generated": generated, "invalid": invalid}


def generate_article_text_batch(
    site_root: Path,
    entries: list[dict[str, Any]],
    progress: ProgressCallback,
) -> dict[str, Any]:
    if not entries or len(entries) > 3:
        raise ValueError("ChatGPTへまとめて送れる記事は1～3件です")
    batch_key = hashlib.sha256(
        "\n".join(str(entry.get("request_id") or "") for entry in entries).encode("utf-8")
    ).hexdigest()[:16]
    package = site_root / ".article-studio" / "chatgpt-direct" / "batches" / batch_key
    attachment_paths: list[Path] = []
    tasks: list[str] = []
    all_fanza = all(
        _is_fanza_product_source(dict(entry.get("source") or {}))
        for entry in entries
    )
    recent_language = _recent_draft_language(site_root)
    for number, entry in enumerate(entries, start=1):
        request_id = str(entry.get("request_id") or "")
        source = dict(entry.get("source") or {})
        options = dict(entry.get("options") or {})
        records, paths = _prepare_selected_media(
            package,
            source,
            options,
            filename_prefix=f"task-{number}-",
        )
        attachment_paths.extend(paths)
        prompt_options = {
            **options,
            "recent_language": recent_language,
        }
        task_body = (
            _compact_fanza_task_prompt(source, prompt_options, records)
            if all_fanza
            else _codex_prompt(source, prompt_options, records)
        )
        task_body += learning_prompt_context(dict(source.get("site_learning") or {}))
        tasks.append(f"\n===== TASK {number} / request_id={request_id} =====\n{task_body}")
    if all_fanza:
        prompt = FANZA_ARTICLE_GUIDE + "\n" + "\n".join(tasks) + "\n" + BATCH_ARTICLE_SCHEMA
    else:
        prompt = (
            "以下の複数商品は互いに独立した記事です。人物、作品、画像、タイトル、レスを"
            "別の商品へ混ぜないでください。添付ファイル名と各TASK内の対応表を照合し、"
            "各記事を個別に完成させてください。\n"
            + "\n".join(tasks)
            + "\n\n"
            + ARTICLE_SELF_REVIEW
            + "\n"
            + BATCH_ARTICLE_SCHEMA
        )
    return _request_validated_json(
        prompt,
        attachment_paths,
        lambda value: _validate_article_batch(value, entries),
        progress,
    )


def generate_article_text(
    site_root: Path,
    source: dict[str, Any],
    options: dict[str, Any],
    progress: ProgressCallback,
    conversation: dict[str, str] | None = None,
) -> dict[str, Any]:
    evidence_paths = [
        Path(value)
        for value in (source.get("_chatgpt_evidence_paths") or [])
        if Path(value).exists()
    ]
    package = (
        evidence_paths[0].parent / "selected-media"
        if evidence_paths
        else site_root / ".article-studio" / "chatgpt-direct" / "selected-media"
    )
    content_records, content_paths = _prepare_selected_media(
        package, source, options
    )
    prompt_options = {
        **options,
        "recent_language": _recent_draft_language(site_root),
    }
    if _is_fanza_product_source(source):
        prompt = (
            FANZA_ARTICLE_GUIDE
            + "\n商品情報:\n"
            + _compact_fanza_task_prompt(source, prompt_options, content_records)
            + learning_prompt_context(dict(source.get("site_learning") or {}))
            + "\n"
            + ARTICLE_SCHEMA
        )
    else:
        prompt = _codex_prompt(
            source,
            prompt_options,
            content_records,
        ) + learning_prompt_context(dict(source.get("site_learning") or {})) \
            + "\n\n" + ARTICLE_SELF_REVIEW + "\n" + ARTICLE_SCHEMA
    selected_media_count = len(options.get("selected_image_ids") or [])
    selected_video_id_list = list(options.get("selected_video_ids") or [])
    requested_count = options.get("reply_count", "auto")
    return _request_validated_json(
        prompt,
        (
            content_paths
            if conversation and conversation.get("url")
            else [*evidence_paths, *content_paths]
        ),
        lambda value: _validate_codex_result(
            value,
            requested_count,
            selected_media_count,
            selected_video_id_list,
        ),
        progress,
        conversation,
    )


def refine_article_text(
    site_root: Path,
    source: dict[str, Any],
    options: dict[str, Any],
    draft: dict[str, Any],
    progress: ProgressCallback,
    conversation: dict[str, str] | None = None,
) -> dict[str, Any]:
    prompt_options = {
        **options,
        "recent_language": _recent_draft_language(site_root),
    }
    prompt = _codex_refinement_prompt(
        source,
        prompt_options,
        draft,
    ) + "\n\n" + ARTICLE_SCHEMA
    selected_media_count = (
        len(options.get("selected_image_ids") or [])
        + len(options.get("selected_video_ids") or [])
    )
    selected_video_ids = list(options.get("selected_video_ids") or [])
    return _request_validated_json(
        prompt,
        [],
        lambda value: _validate_codex_result(
            value,
            options.get("reply_count", "auto"),
            selected_media_count,
            selected_video_ids,
        ),
        progress,
        conversation,
    )
