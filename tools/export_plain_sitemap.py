"""Export the existing canonical URL set without regenerating article bodies."""
import argparse
from pathlib import Path

from indanya_desktop.sitemap_health import _parse_sitemap_bytes, validate_local_sitemaps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args()
    urls, _ = _parse_sitemap_bytes((args.repository / "sitemap.xml").read_bytes(), "sitemap.xml")
    (args.repository / "sitemap-pages.txt").write_text("\n".join(sorted(urls)) + "\n", encoding="utf-8", newline="")
    report = validate_local_sitemaps(args.repository, args.public_url)
    print(f"Validated {len(urls)} canonical URLs; {report['published_articles']} published articles")


if __name__ == "__main__":
    main()
