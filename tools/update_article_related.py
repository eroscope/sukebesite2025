from __future__ import annotations

from pathlib import Path


STYLE = '<link rel="stylesheet" href="../assets/common/article-related.css">'
SCRIPT = (
    '<script src="../assets/common/article-related.js" '
    'data-site-root="../"></script>'
)


def update_articles(site_root: Path) -> int:
    changed = 0
    for path in sorted((site_root / "articles").glob("*.html")):
        source = path.read_text(encoding="utf-8")
        updated = source
        if "assets/common/article-related.css" not in updated:
            updated = updated.replace("</head>", f"{STYLE}\n</head>", 1)
        if "assets/common/article-related.js" not in updated:
            updated = updated.replace("</body>", f"{SCRIPT}\n</body>", 1)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(f"updated {update_articles(root)} articles")
