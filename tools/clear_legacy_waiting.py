from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def clear_legacy_waiting(site_root: Path) -> dict[str, int | str]:
    studio = site_root / ".article-studio"
    drafts_root = studio / "drafts"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = studio / "cleanup-backups" / stamp
    backup_drafts = backup / "drafts"
    backup_drafts.mkdir(parents=True, exist_ok=True)

    removed: set[str] = set()
    for path in drafts_root.glob("*.json"):
        payload = _read(path, {})
        if not isinstance(payload, dict):
            continue
        published = str(
            payload.get("editorial_status") or payload.get("status") or ""
        ) == "published"
        if published:
            continue
        shutil.copy2(path, backup_drafts / path.name)
        removed.add(path.stem)
        path.unlink()

    settings_path = studio / "automation-settings.json"
    settings = _read(settings_path, {})
    removed_queue = 0
    if isinstance(settings, dict):
        shutil.copy2(settings_path, backup / settings_path.name)
        queue = settings.get("queue", [])
        if isinstance(queue, list):
            kept = [
                item for item in queue
                if not isinstance(item, dict) or str(item.get("slug") or "") not in removed
            ]
            removed_queue = len(queue) - len(kept)
            settings["queue"] = kept
            _write(settings_path, settings)

    queue_path = studio / "chatgpt-primary-queue.json"
    rows = _read(queue_path, [])
    archived_requests = 0
    if isinstance(rows, list):
        shutil.copy2(queue_path, backup / queue_path.name)
        for item in rows:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("draft_slug") or "")
            if item.get("status") in {"queued", "sent", "processing"} or slug in removed:
                if item.get("status") not in {"completed", "archived_quality_reset"}:
                    archived_requests += 1
                item["status"] = "archived_quality_reset"
                item["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                item["last_error"] = "旧品質基準の待機データを整理しました"
                item["draft_slug"] = ""
        _write(queue_path, rows[-500:])

    candidates_path = studio / "candidates.json"
    candidates = _read(candidates_path, [])
    reset_candidates = 0
    if isinstance(candidates, list):
        shutil.copy2(candidates_path, backup / candidates_path.name)
        for item in candidates:
            if isinstance(item, dict) and item.get("status") == "chatgpt_queued":
                item["status"] = "discarded_quality_reset"
                reset_candidates += 1
        _write(candidates_path, candidates[-500:])

    removed_jobs = 0
    jobs_backup = backup / "jobs"
    for path in (studio / "jobs").glob("*.json"):
        payload = _read(path, {})
        if isinstance(payload, dict) and str(payload.get("slug") or "") in removed:
            jobs_backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, jobs_backup / path.name)
            path.unlink()
            removed_jobs += 1

    removed_thumbs = 0
    thumbs_backup = backup / "board-thumbs"
    for path in (studio / "board-thumbs").glob("*"):
        if path.is_file() and any(path.name.startswith(slug) for slug in removed):
            thumbs_backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, thumbs_backup / path.name)
            path.unlink()
            removed_thumbs += 1

    return {
        "removed_drafts": len(removed),
        "removed_schedule_entries": removed_queue,
        "archived_requests": archived_requests,
        "reset_candidates": reset_candidates,
        "removed_jobs": removed_jobs,
        "removed_thumbnails": removed_thumbs,
        "backup": str(backup),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(clear_legacy_waiting(args.site_root.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
