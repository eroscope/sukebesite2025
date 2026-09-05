# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import PySide6
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH).resolve()
tools = root / "tools"
playwright_data = collect_data_files("playwright")
imageio_ffmpeg_data = collect_data_files("imageio_ffmpeg")
imageio_ffmpeg_binaries = collect_dynamic_libs("imageio_ffmpeg")

a = Analysis(
    [str(tools / "indanya_desktop_app.py")],
    pathex=[str(tools)],
    binaries=imageio_ffmpeg_binaries,
    datas=[
        (str(tools / "article_studio_app"), "article_studio_app"),
        (str(tools / "article_studio_codex_schema.json"), "."),
        (str(tools / "article_studio_codex_analysis_schema.json"), "."),
        (str(tools / "social_profile_verification_schema.json"), "."),
        (str(tools / "x_trend_templates_schema.json"), "."),
    ] + playwright_data + imageio_ffmpeg_data,
    hiddenimports=["PIL", "playwright.sync_api", "playwright._impl._driver", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "imageio_ffmpeg", "google.analytics.data_v1beta", "google.oauth2.service_account"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# PySide 6.11 is built with a newer MSVC runtime than the Python 3.12
# distribution.  PyInstaller otherwise puts Python's older VCRUNTIME DLLs at
# the bundle root, where Windows loads them before PySide's compatible copies.
pyside_dir = Path(PySide6.__file__).resolve().parent
msvc_runtime_names = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}
incompatible_path_dlls = {
    # These are picked up from Codex's Poppler runtime through PATH. QtCore
    # expects the unversioned Windows ICU exports instead.
    "icudt78.dll",
    "icuuc.dll",
}
a.binaries = [
    entry
    for entry in a.binaries
    if not (
        len(Path(entry[0]).parts) == 1
        and Path(entry[0]).name.lower() in (msvc_runtime_names | incompatible_path_dlls)
    )
]
for runtime_name in sorted(msvc_runtime_names):
    runtime_path = pyside_dir / runtime_name
    if runtime_path.exists():
        a.binaries.append((runtime_name, str(runtime_path), "BINARY"))

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="IndanyaStudio", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="IndanyaStudio")
