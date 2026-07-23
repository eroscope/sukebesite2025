# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).resolve()
tools = root / "tools"
playwright_data = collect_data_files("playwright")

a = Analysis(
    [str(tools / "indanya_desktop_app.py")],
    pathex=[str(tools)],
    binaries=[],
    datas=[
        (str(tools / "article_studio_app"), "article_studio_app"),
        (str(tools / "article_studio_codex_schema.json"), "."),
        (str(tools / "article_studio_codex_analysis_schema.json"), "."),
    ] + playwright_data,
    hiddenimports=["PIL", "playwright.sync_api", "playwright._impl._driver", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="IndanyaStudio", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="IndanyaStudio")
