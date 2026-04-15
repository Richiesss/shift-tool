# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for SDU-Shift
Windows EXE / macOS .app 共用ビルド設定

ビルド方法（手動）:
  pip install -r requirements.txt
  pyinstaller shift_tool.spec --clean --noconfirm
"""
import sys
import os
from pathlib import Path
import PyQt6
import ortools
import reportlab
import openpyxl

SITE_PACKAGES = Path(PyQt6.__file__).parent.parent
QT6_DIR       = Path(PyQt6.__file__).parent / "Qt6"
ORTOOLS_DIR   = Path(ortools.__file__).parent
REPORTLAB_DIR = Path(reportlab.__file__).parent
OPENPYXL_DIR  = Path(openpyxl.__file__).parent

IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

ICON_WIN = "assets/icon.ico"  if os.path.exists("assets/icon.ico")  else None
ICON_MAC = "assets/icon.icns" if os.path.exists("assets/icon.icns") else None

block_cipher = None

# ── 追加データ ────────────────────────────────────────────────────────
added_datas = [
    (str(ORTOOLS_DIR),   "ortools"),
    (str(REPORTLAB_DIR), "reportlab"),
    (str(OPENPYXL_DIR),  "openpyxl"),
]

# macOS: Qt6 フレームワーク・プラグインを明示的に含める
# （PyInstallerのフックだけでは cocoa プラグイン等が欠落することがある）
if IS_MAC and QT6_DIR.exists():
    added_datas.append((str(QT6_DIR), "PyQt6/Qt6"))

# ── 非表示インポート ───────────────────────────────────────────────────
hidden_imports = [
    "ortools.sat.python.cp_model",
    "ortools.sat",
    "ortools.sat.python",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.sip",
    "sqlite3",
    "openpyxl",
    "openpyxl.styles",
    "openpyxl.utils",
    "openpyxl.utils.cell",
    "reportlab",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfbase.pdfmetrics",
    "reportlab.lib.pagesizes",
    "reportlab.platypus",
    "reportlab.lib.styles",
    "reportlab.lib.enums",
    "PIL",
    "PIL.Image",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtNetwork",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
        "test",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SDU-Shift",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        "vcruntime140.dll",
        "python*.dll",
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
    ],
    runtime_tmpdir=None,
    console=False,           # コンソールウィンドウを表示しない
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_MAC if IS_MAC else ICON_WIN,

    # Windows バージョン情報
    version=None,            # version_info.txt を用意した場合に指定
)

# ── macOS: .app バンドルを生成 ────────────────────────────────────────────
if IS_MAC:
    app = BUNDLE(
        exe,
        name="SDU-Shift.app",
        icon=ICON_MAC,
        bundle_identifier="com.sdu.shift",
        info_plist={
            "CFBundleShortVersionString": "1.0",
            "CFBundleName": "SDU-Shift",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # ダークモード対応
        },
    )
