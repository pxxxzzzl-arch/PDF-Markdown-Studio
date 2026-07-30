# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

if sys.platform != "win32":
    raise SystemExit("scripts/windows_app.spec must be built on Windows")

PROJECT_ROOT = Path(SPECPATH).resolve().parent
BUILD_ROOT = Path(
    os.environ.get("PDFMD_WINDOWS_BUILD_ROOT", PROJECT_ROOT / "build" / "windows-app")
).resolve()
EDITION = os.environ.get("PDFMD_WINDOWS_EDITION", "full").strip().lower()
if EDITION not in {"full", "lite"}:
    raise SystemExit("PDFMD_WINDOWS_EDITION must be full or lite")

GENERATED_ROOT = BUILD_ROOT / "generated"
ENTRY_SCRIPT = PROJECT_ROOT / "scripts" / "windows_app_entry.py"
ICON_PATH = GENERATED_ROOT / "PDF Markdown Studio.ico"
VERSION_INFO = GENERATED_ROOT / "windows_version_info.txt"
APP_MANIFEST = PROJECT_ROOT / "desktop" / "windows" / "app.manifest"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

for required_path in (ENTRY_SCRIPT, ICON_PATH, VERSION_INFO, APP_MANIFEST, FRONTEND_DIST):
    if not required_path.exists():
        raise SystemExit(f"required Windows build asset is missing: {required_path}")

datas = [
    (str(FRONTEND_DIST), "pdfmd/web"),
    *collect_data_files("webview"),
]
binaries = [*collect_dynamic_libs("webview")]
hiddenimports = [
    "clr",
    "pythonnet",
    "webview.platforms.edgechromium",
    "webview.platforms.win32",
    "webview.platforms.winforms",
    *collect_submodules("uvicorn"),
]

for distribution in ("pywebview",):
    datas += copy_metadata(distribution)

if EDITION == "full":
    hiddenimports += [
        "docling.models.plugins.defaults",
        "rapidocr.inference_engine.pytorch",
        "rapidocr.main",
        "torchvision._C",
        "transformers.models.idefics3",
        "transformers.models.rt_detr_v2",
        *collect_submodules("scipy._external.array_api_compat"),
        *collect_submodules("scipy._lib.array_api_compat"),
    ]
    for distribution in (
        "docling",
        "docling-slim",
        "docling-core",
        "docling-ibm-models",
        "docling-parse",
        "rapidocr",
        "torch",
        "torchvision",
        "transformers",
    ):
        datas += copy_metadata(distribution)
    for package in (
        "docling",
        "docling_core",
        "docling_ibm_models",
        "docling_parse",
        "pypdfium2",
        "rapidocr",
        "rtree",
        "safetensors",
        "tokenizers",
        "torchvision",
        "transformers",
    ):
        package_datas, package_binaries, package_hiddenimports = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hiddenimports

analysis = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "gi",
        "gtk",
        "tkinter",
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
        "webview.platforms.qt",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PDF Markdown Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    icon=str(ICON_PATH),
    version=str(VERSION_INFO),
    manifest=str(APP_MANIFEST),
    uac_admin=False,
    uac_uiaccess=False,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDF Markdown Studio",
)
