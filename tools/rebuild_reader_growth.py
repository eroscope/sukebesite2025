"""Apply shared reader changes and a bounded set of existing entry-page repairs."""
import argparse
import json
import re
from pathlib import Path

from indanya_desktop.reader_growth import provenance_markup, write_growth_review
from indanya_desktop.reader_experience import refresh_reader_experience
from indanya_desktop.site_discovery import _enrich_articles, _published_articles, _group_articles, _write_hubs


def repair_entrypoints(repository: Path, draft_root: Path, slugs: list[str]) -> dict:
    fixed, missing = [], []
    for slug in slugs[:10]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            continue
        page = repository / "articles" / (slug + ".html")
        draft = draft_root / ".article-studio" / "drafts" / (slug + ".json")
        if not page.exists() or not draft.exists():
            missing.append(slug)
            continue
        payload = json.loads(draft.read_text(encoding="utf-8"))
        source = page.read_text(encoding="utf-8")
        reference = provenance_markup(payload)
        if reference and 'class="article-source-reference"' not in source:
            anchor = '<div class="editorial-note">'
            if anchor in source:
                source = source.replace(anchor, reference + anchor, 1)
            else:
                source = source.replace('<!-- INDANYA_DISCOVERY_START -->', reference + '<!-- INDANYA_DISCOVERY_START -->', 1)
        source = source.replace("今日の人気記事", "掲載記事")
        source = re.sub(r'<span>\d+\s*コメント</span>', "", source)
        source = re.sub(r'<section class="sidebox"><h2 class="side-title">最新コメント</h2>[\s\S]*?</section>', '<section class="sidebox"><h2 class="side-title">また読む</h2><div class="sidebody"><a href="../saved.html">保存・閲覧履歴</a><br><a href="../feed.xml">RSS</a></div></section>', source)
        source = source.replace("編集用のレスとして再構成した下書きです。公開前に内容と画像利用許可を確認してください。", "編集上の再構成であり、読者による投稿や実際の口コミではありません。")
        page.write_text(source, encoding="utf-8", newline="")
        fixed.append({"slug": slug, "source_reference": bool(reference), "body_regenerated": False})
    return {"fixed": fixed, "missing": missing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args()
    repo = args.repository.resolve()
    review = write_growth_review(args.runtime)
    result = repair_entrypoints(repo, args.runtime, review["priority_articles"])
    articles = json.loads((repo / "data/articles.json").read_text(encoding="utf-8"))
    published = _enrich_articles(repo, _published_articles(articles))
    _write_hubs(repo, args.public_url, *_group_articles(published))
    result["static_pages"] = len(refresh_reader_experience(repo, args.public_url, published))
    result["featured_hubs"] = json.loads((repo / "data/reader/hubs.json").read_text(encoding="utf-8"))
    output = args.runtime / ".article-studio/reader-growth/implementation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"entrypages": len(result["fixed"]), "missing": result["missing"], "static_pages": result["static_pages"], "featured_hubs": len(result["featured_hubs"])}))


if __name__ == "__main__":
    main()
