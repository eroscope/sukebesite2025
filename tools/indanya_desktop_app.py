#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from article_studio import CodexRunner  # noqa: E402
from indanya_desktop.window import MainWindow, VideoPlayerDialog  # noqa: E402


def default_site_root() -> Path:
    configured = os.environ.get("INDANYA_SITE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = [Path.cwd(), TOOLS_ROOT.parent]
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        candidates.extend(executable.parents[:4])
    for candidate in candidates:
        if (candidate / "index.html").is_file() and (candidate / "articles").is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="淫談屋 記事編集室")
    parser.add_argument("--site-root", type=Path, default=default_site_root())
    parser.add_argument("--screenshot", type=Path, help="画面確認用PNGを保存して終了")
    parser.add_argument("--background", action="store_true", help="自動処理用に最小化して起動")
    parser.add_argument("--video-smoke", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("淫談屋 記事編集室")
    app.setOrganizationName("Indanya")
    window = MainWindow(args.site_root)
    status = CodexRunner(window.site.root).status()
    window.codex_state.setText(
        f"Codex: 接続済み ({status.get('version', '')})" if status.get("available") else f"Codex: {status.get('message', '未接続')}"
    )
    if args.background:
        window.showMinimized()
    else:
        window.show()
    if args.video_smoke:
        import json
        from PySide6.QtCore import QTimer, QUrl

        destination = args.video_smoke.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        cached_video = next((window.site.root / ".article-studio" / "video-cache").glob("*.mp4"), None)
        if cached_video is None:
            destination.write_text(json.dumps({"ok": False, "error": "cached video not found"}), encoding="utf-8")
            window.close()
            return 2
        smoke_player: list[VideoPlayerDialog] = []

        def start_video() -> None:
            player = VideoPlayerDialog(window, "EXE動画再生テスト", QUrl.fromLocalFile(str(cached_video.resolve())))
            smoke_player.append(player)
            player.show()

        def finish_video() -> None:
            player = smoke_player[0]
            result = {
                "ok": player.player.duration() > 0 and not player.player.errorString(),
                "status": player.player.mediaStatus().name,
                "state": player.player.playbackState().name,
                "duration": player.player.duration(),
                "error": player.player.errorString(),
                "source": player.player.source().toLocalFile(),
            }
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            player.close()
            window.close()
            app.quit()

        QTimer.singleShot(500, start_video)
        QTimer.singleShot(7500, finish_video)
    if args.screenshot:
        from PySide6.QtCore import QTimer

        destination = args.screenshot.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        def capture() -> None:
            window.grab().save(str(destination), "PNG")
            window.close()
            app.quit()

        QTimer.singleShot(1800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
