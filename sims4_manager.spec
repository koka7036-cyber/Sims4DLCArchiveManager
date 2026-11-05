# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec for Sims 4 DLC Archive & Install Manager (White Label Edition)
# Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 3, 4.1, 5.1, 5.2, 10, and 12.2.
#
# CRITICAL REQUIREMENTS:
# 1. AsInvoker execution level manifest (no UAC elevation at startup)
# 2. console = False (Windowed CustomTkinter application)
# 3. Explicit exclusion of 'owner_tools' (zero leak of private keys, tokens, or builder code)
# 4. Strict White Label store data isolation (only target store packaged)

import os
import sys
from pathlib import Path

block_cipher = None

# Determine target store (default: store_alpha, override via S4_STORE_ID env)
store_id = os.environ.get("S4_STORE_ID", "store_alpha")

# Store-specific data files (only target store is bundled)
store_data = [
    (f"configs/stores/{store_id}", f"configs/stores/{store_id}"),
]

# Excluded packages: STRICT ZERO-LEAKAGE of owner tools and development code
forbidden_excludes = [
    "owner_tools",
    "owner_tools.key_vault",
    "owner_tools.token_manager",
    "owner_tools.build_manager",
    "owner_tools.release_manager",
    "owner_tools.signing",
    "owner_tools.config_validator",
    "owner_tools.builder_cli",
    "owner_tools.owner_gui",
    "owner_tools.bundle_inspector",
    "owner_tools.packaging",
    "pytest",
    "unittest",
    "tests",
]

# Windows Manifest specifying asInvoker execution level per Section 3
as_invoker_manifest = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="amd64" name="Sims4DLCArchiveManager" type="win32"/>
  <description>Sims 4 DLC Archive &amp; Install Manager (White Label Edition)</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>
    </application>
  </compatibility>
</assembly>
"""

exe_name = f"Sims4_{store_id.replace('-', '_').title()}"

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=store_data,
    hiddenimports=[
        "customtkinter",
        "tkinter",
        "PIL",
        "cryptography",
        "py7zr",
        "rarfile",
        "requests",
        "urllib3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=forbidden_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
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
    manifest=as_invoker_manifest,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=exe_name,
)
