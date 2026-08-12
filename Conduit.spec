# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import FILE_VERSION, FILE_VERSION_STRING, PRODUCT_VERSION


ASSETS = ROOT / "app" / "assets"
NOTICES = ROOT / "build" / "THIRD_PARTY_NOTICES.txt"
VERSION_FILE = ROOT / "build" / "Conduit-version.txt"
VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
VERSION_FILE.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={FILE_VERSION},
    prodvers={FILE_VERSION},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Conduit contributors'),
          StringStruct('FileDescription', 'Conduit'),
          StringStruct('FileVersion', '{FILE_VERSION_STRING}'),
          StringStruct('InternalName', 'Conduit'),
          StringStruct('LegalCopyright', 'Copyright (C) 2026 Conduit contributors'),
          StringStruct('OriginalFilename', f'Conduit-v{PRODUCT_VERSION}.exe'),
          StringStruct('ProductName', 'Conduit'),
          StringStruct('ProductVersion', f'{PRODUCT_VERSION}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)""",
    encoding="utf-8",
)

hidden_imports = [
    "app.firewall_helper",
    "app.windows_firewall",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32com.client",
    "win32com.shell.shell",
    "win32com.shell.shellcon",
    "win32con",
    "win32event",
    "win32process",
]

datas = collect_data_files("customtkinter") + [
    (str(ASSETS), "app/assets"),
    (str(ROOT / "LICENSE"), "."),
    (str(NOTICES), "."),
]

a = Analysis(
    ["run.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"Conduit-v{PRODUCT_VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "app_icon.ico"),
    version=str(VERSION_FILE),
)
