"""Verify deployment without running analytics or following affiliate links."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import URLError
import xml.etree.ElementTree as ET


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def verify(root, base, slugs):
    class SiteRedirects(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            if not newurl.startswith(base):
                raise ValueError("Redirect left the site")
            return super().redirect_request(request, fp, code, message, headers, newurl)

    opener = build_opener(SiteRedirects())
    paths = ["index.html", "latest.html", "latest-2.html", "saved.html", "privacy.html",
             "assets/common/age-gate.js", "assets/common/ga4.js", "assets/common/reader.js",
             "assets/common/reader.css", "assets/common/catalog.js", "assets/common/article-related.js",
             "data/reader/catalog.json", "data/reader/hubs.json", "sitemap.xml"]
    paths += [f"articles/{slug}.html" for slug in slugs]
    results = []
    for path in paths:
        request = Request(urljoin(base, path), headers={"User-Agent": "Indanya-Release-Check/1.0"})
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read()
        except (URLError, TimeoutError, ValueError) as exc:
            results.append({"path": path, "matches": False, "error": str(exc)})
            continue
        expected = (root / path).read_bytes()
        results.append({"path": path, "matches": digest(raw) == digest(expected), "bytes": len(raw)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--implementation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args()
    base = args.public_url.rstrip("/") + "/"
    implementation = json.loads(args.implementation.read_text(encoding="utf-8"))
    slugs = [row["slug"] for row in implementation["fixed"]]
    started = time.monotonic()
    while True:
        checks = verify(args.repository, base, slugs)
        if all(row["matches"] for row in checks) or time.monotonic() - started >= args.wait_seconds:
            break
        time.sleep(15)
    xml = ET.parse(args.repository / "sitemap.xml")
    locations = {node.text for node in xml.findall(".//{*}loc")}
    pages = ["latest.html"] + [path.name for path in args.repository.glob("latest-*.html")]
    missing_pages = [path for path in pages if urljoin(base, path) not in locations]
    result = {"checks": checks, "missing_sitemap_pages": missing_pages,
              "all_match": all(row["matches"] for row in checks) and not missing_pages}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"verified_files": len(checks), "all_match": result["all_match"],
                      "sitemap_list_pages": len(pages), "missing_sitemap_pages": missing_pages}))
    raise SystemExit(0 if result["all_match"] else 1)


if __name__ == "__main__":
    main()
