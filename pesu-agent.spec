# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for PESU Agent (Phase 9.9.7 onedir)."""

import os
from pathlib import Path
import sys

block_cipher = None
ROOT_DIR = os.path.abspath(SPECPATH)

# Native Assets
pesu_host_dll = os.path.join(ROOT_DIR, "build", "native", "PesuHost.dll")

# WebView2 verified asset source root
try:
    import webview.util
    webview_lib_dir = os.path.dirname(webview.util.interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
except Exception:
    webview_lib_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Programs", "Python", "Python313", "Lib", "site-packages", "webview", "lib"
    )

wv_core_dll = os.path.join(webview_lib_dir, "Microsoft.Web.WebView2.Core.dll")
wv_winforms_dll = os.path.join(webview_lib_dir, "Microsoft.Web.WebView2.WinForms.dll")
wv_loader_dll = os.path.join(webview_lib_dir, "runtimes", "win-x64", "native", "WebView2Loader.dll")

binaries = [
    (pesu_host_dll, "native"),
    (wv_core_dll, "native"),
    (wv_winforms_dll, "native"),
    (wv_loader_dll, os.path.join("native", "runtimes", "win-x64", "native")),
]

hidden_imports = [
    "pythonnet",
    "clr",
    "ctypes",
    "ctypes.wintypes",
    "pydantic",
    "pydantic_core",
    "rich",
    "httpx",
    "pesu_agent",
    "pesu_agent.cli",
    "pesu_agent.runtime.resource_resolver",
    "pesu_agent.runtime.pesu_webview_host",
    "pesu_agent.search.slack_backend_factory",
    "pesu_agent.search.webview_slack_search",
    "pesu_agent.search.webview_thread_reader",
    "pesu_agent.search.temporal",
    "pesu_agent.search.real_slack_backend",
    "pesu_agent.search.slack_query_compiler",
    "pesu_agent.search.slack_search",
    "pesu_agent.search.actor",
    "pesu_agent.investigation.orchestrator",
    "pesu_agent.investigation.models",
    "pesu_agent.investigation.planner",
    "pesu_agent.investigation.analyzer",
    "pesu_agent.investigation.selector",
    "pesu_agent.investigation.search_backend",
    "pesu_agent.lifecycle.slack_lifecycle",
    "pesu_agent.lifecycle.slack_agent_window",
    "pesu_agent.parsers.slack_permalink_parser",
    "pesu_agent.ui",
    "pesu_agent.ui.tray_app",
    "pesu_agent.ui.main_window",
    "pesu_agent.ui.webview_bridge",
    "pesu_agent.ui.messages",
    "pesu_agent.ui.investigation_service",
    "pesu_agent.ui.result_presenter",
    "pesu_agent.ui.qualification_runners",
    "pesu_agent.ui.consent_manager",
    "pesu_agent.ui.preferences",
    "pesu_agent.security",
    "pesu_agent.security.credential_store",
    "pesu_agent.platform",
    "pesu_agent.platform.windows_startup",
    "winreg",
]

ui_assets_src = os.path.join(ROOT_DIR, "src", "pesu_agent", "ui", "assets")
datas = [
    (ui_assets_src, os.path.join("pesu_agent", "ui", "assets")),
    (ui_assets_src, os.path.join("ui", "assets")),
]

a = Analysis(
    [os.path.join(ROOT_DIR, "src", "pesu_agent", "cli.py")],
    pathex=[os.path.join(ROOT_DIR, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "unittest",
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy",
        "IPython",
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
    [],
    exclude_binaries=True,
    name="PESU",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PESU",
)
