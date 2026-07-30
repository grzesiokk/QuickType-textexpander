# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent
source_root = project_root / "src"
icon_path = project_root / "build_assets" / "quicktype.ico"
version_path = project_root / "packaging" / "windows_version_info.txt"
resource_root = source_root / "quicktype" / "resources"

hidden_imports = collect_submodules("comtypes.gen")
if "comtypes.gen.UIAutomationClient" not in hidden_imports:
    hidden_imports.append("comtypes.gen.UIAutomationClient")

a = Analysis(
    [str(project_root / "scripts" / "quicktype_entry.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        (str(path), "quicktype/resources")
        for path in resource_root.iterdir()
        if path.is_file()
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QuickType",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(version_path),
)
