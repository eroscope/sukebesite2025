from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Published before the adult-only editorial gate was introduced.
NON_ADULT_SLUGS = {
    # Strict adult-only review: general news, general entertainment, ambiguous
    # swimsuit/clothing posts, apparent privacy abuse, and minor-adjacent topics.
    "url-tyoieronews-com-18bcec34",
    "url-tyoieronews-com-20f69ded",
    "url-chaos-giga-com-dcbfac5a",
    "url-tyoieronews-com-aa6d4985",
    "url-chaos-giga-com-d4a7edb7",
    "url-chaos-giga-com-3821bc03",
    "url-hheaven-jp-0bc3b9dd",
    "url-chaos-giga-com-3f5c83ff",
    "url-chaos-giga-com-7ab169fb",
    "url-po-kaki-to-com-98c91c1f",
    "url-5ch-echiechi-doorblog-jp-c45f51e2",
    "url-chaos-giga-com-2265ce07",
    "url-chaos-giga-com-395f30a8",
    "url-chaos-giga-com-e2199e3b",
    "url-po-kaki-to-com-0b520333",
    "url-chaos-giga-com-963ce24a",
    "url-chaos-giga-com-89773eea",
    "url-chaos-giga-com-f6fd623c",
    "url-chaos-giga-com-8b7c6df0",
    "url-chaos-giga-com-582dace8",
    "url-chaos-giga-com-02ba9c5e",
    "url-chaos-giga-com-56a92da3",
    "url-bakuwaro-com-0917813a",
    "url-chaos-giga-com-0b7ac7b5",
    "pool-look-back",
    "url-anige-sokuhouvip-com-8070ed95",
    "url-anige-sokuhouvip-com-e5a10a92",
    "url-bakufu-jp-cec35e37",
    "url-chaos-giga-com-069255ac",
    "url-chaos-giga-com-086298d5",
    "url-chaos-giga-com-0a01ea39",
    "url-chaos-giga-com-0a526351",
    "url-chaos-giga-com-10810fe9",
    "url-chaos-giga-com-1979aeb3",
    "url-chaos-giga-com-19dfe709",
    "url-chaos-giga-com-1f2e798c",
    "url-chaos-giga-com-20ed77b3",
    "url-chaos-giga-com-2c042c9f",
    "url-chaos-giga-com-30bb64c6",
    "url-chaos-giga-com-33726442",
    "url-chaos-giga-com-408bea10",
    "url-chaos-giga-com-40a039b6",
    "url-chaos-giga-com-4670e979",
    "url-chaos-giga-com-64cf546c",
    "url-chaos-giga-com-6fd5e1b0",
    "url-chaos-giga-com-74262fc8",
    "url-chaos-giga-com-78401366",
    "url-chaos-giga-com-786d68d8",
    "url-chaos-giga-com-80c61edd",
    "url-chaos-giga-com-8947024e",
    "url-chaos-giga-com-905dc46c",
    "url-chaos-giga-com-953899ce",
    "url-chaos-giga-com-9a3f64bf",
    "url-chaos-giga-com-b7df555d",
    "url-chaos-giga-com-e57c3807",
    "url-chaos-giga-com-e5d0bea5",
    "url-chaos-giga-com-e8368d66",
    "url-chaos-giga-com-e8b1893f",
    "url-chaos-giga-com-f2478ae7",
    "url-eromazofu-com-180644b8",
    "url-eromazofu-com-c82b815d",
    "url-hnalady-com-30d4c39c",
    "url-hnalady-com-aeb29613",
    "url-hnalady-com-b8a27b70",
    "url-kimootoko-net-5c0fba32",
    "url-po-kaki-to-com-d9a04691",
    "url-tyoieronews-com-02010827",
    "url-tyoieronews-com-0c4c395e",
    "url-tyoieronews-com-1c1add55",
    "url-tyoieronews-com-280eb78b",
    "url-tyoieronews-com-2f787062",
    "url-tyoieronews-com-2fd0ba3a",
    "url-tyoieronews-com-31c6df4a",
    "url-tyoieronews-com-411c6771",
    "url-tyoieronews-com-52f5ef44",
    "url-tyoieronews-com-564b6d01",
    "url-tyoieronews-com-9df3d8a0",
    "url-tyoieronews-com-a2bde299",
    "url-tyoieronews-com-a9c479cc",
    "url-tyoieronews-com-bf52665c",
    "url-tyoieronews-com-dbc475a2",
    "url-tyoieronews-com-fe0abb74",
}
TEMPLATE_ONLY_SLUGS = {"pool-look-back"}

RANK_ROW_PATTERN = re.compile(
    r'<div class="rank">(?:(?!<div class="rank">).)*?'
    r'<a href="(?P<href>[^"]+)".*?</div></div>',
    re.DOTALL,
)

def rebuild_sitemap(root: Path, articles: list[dict]) -> None:
    robots_path = root / "robots.txt"
    robots = robots_path.read_text(encoding="utf-8") if robots_path.is_file() else ""
    match = re.search(r"(?im)^Sitemap:\s*(https?://.+?/)(?:sitemap\.xml)?\s*$", robots)
    base_url = match.group(1) if match else "https://eroscope.github.io/sukebesite2025/"
    from indanya_desktop.site_discovery import refresh_site_discovery

    refresh_site_discovery(root, base_url, articles)


def remove_broken_article_links(root: Path) -> int:
    changed = 0
    article_root = root / "articles"
    for path in article_root.glob("*.html"):
        source = path.read_text(encoding="utf-8")

        def keep_or_remove(match: re.Match[str]) -> str:
            href = match.group("href").split("#", 1)[0].split("?", 1)[0]
            if not href or "://" in href or not href.endswith(".html"):
                return match.group(0)
            return match.group(0) if (path.parent / href).is_file() else ""

        updated = RANK_ROW_PATTERN.sub(keep_or_remove, source)
        if updated != source:
            path.write_text(updated, encoding="utf-8", newline="")
            changed += 1
    return changed


def cleanup(root: Path, *, remove_from_git_index: bool = False) -> tuple[int, int]:
    root = root.resolve()
    data_path = root / "data" / "articles.json"
    articles = json.loads(data_path.read_text(encoding="utf-8"))
    removal_slugs = NON_ADULT_SLUGS - TEMPLATE_ONLY_SLUGS
    removed = [item for item in articles if item.get("slug") in removal_slugs]
    remaining = [item for item in articles if item.get("slug") not in removal_slugs]
    data_path.write_text(
        json.dumps(remaining, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
    rebuild_sitemap(root, remaining)
    removed_slugs = {str(item["slug"]) for item in removed}
    article_root = (root / "articles").resolve()
    asset_root = (root / "assets" / "articles").resolve()
    draft_root = (root / ".article-studio" / "drafts").resolve()
    for slug in sorted(removal_slugs):
        article_path = (article_root / f"{slug}.html").resolve()
        asset_path = (asset_root / slug).resolve()
        draft_path = (draft_root / f"{slug}.json").resolve()
        if article_path.is_file() and article_path.is_relative_to(article_root):
            article_path.unlink()
            removed_slugs.add(slug)
        if asset_path.is_dir():
            if not asset_path.is_relative_to(asset_root):
                raise RuntimeError(f"Refusing to remove assets outside workspace: {asset_path}")
            shutil.rmtree(asset_path)
        if draft_path.is_file():
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            draft["status"] = "deleted"
            draft["review_status"] = "deleted"
            draft["editorial_status"] = "removed_non_adult"
            draft.pop("published_url", None)
            draft_path.write_text(
                json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="",
            )
    if remove_from_git_index and removed_slugs:
        targets = []
        for slug in sorted(removed_slugs):
            targets.extend([
                f"articles/{slug}.html",
                f"assets/articles/{slug}",
            ])
        subprocess.run(
            ["git", "rm", "-r", "--cached", "--sparse", "--ignore-unmatch", "--", *targets],
            cwd=root,
            check=True,
        )
    remove_broken_article_links(root)
    return len(removed_slugs), len(remaining)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    removed_count, remaining_count = cleanup(
        target,
        remove_from_git_index="--git-index" in sys.argv[2:],
    )
    print(f"removed={removed_count} remaining={remaining_count}")
