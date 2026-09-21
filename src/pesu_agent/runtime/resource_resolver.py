"""PESU Runtime Resource Resolution (Phase 9.9.7).

Centralized asset resolution for PESU Host DLL and WebView2 runtime assemblies
across both development source execution and PyInstaller frozen bundle execution.

Invariants:
1. sys._MEIPASS encapsulation: Single point of truth for frozen runtime paths.
2. Zero runtime CodeDom fallback: Missing PesuHost.dll raises typed PesuHostAssemblyError.
3. Verified WebView2 asset resolution: Core, WinForms, and x64 WebView2Loader.
"""

from __future__ import annotations

from enum import Enum
import logging
import os
from pathlib import Path
import sys
from typing import Optional

logger = logging.getLogger(__name__)


class PesuHostFailureCode(str, Enum):
    """Failure codes for PESU Native Host loading."""
    PESU_HOST_ASSEMBLY_NOT_AVAILABLE = "PESU_HOST_ASSEMBLY_NOT_AVAILABLE"
    PESU_HOST_ASSEMBLY_LOAD_FAILED = "PESU_HOST_ASSEMBLY_LOAD_FAILED"
    WEBVIEW2_DEPENDENCY_NOT_FOUND = "WEBVIEW2_DEPENDENCY_NOT_FOUND"


class PesuHostAssemblyError(Exception):
    """Typed exception raised when PesuHost assembly or WebView2 dependencies cannot be loaded."""

    def __init__(
        self,
        failure_code: PesuHostFailureCode,
        message: str = "",
        details: Optional[dict] = None,
    ) -> None:
        self.failure_code = failure_code
        self.details = details or {}
        super().__init__(message or f"PESU Host Assembly Error ({failure_code.value})")


def is_frozen() -> bool:
    """Returns True if running in a PyInstaller frozen bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_bundle_root() -> Path:
    """Returns root directory for asset resolution (MEIPASS if frozen, project root if dev)."""
    if is_frozen():
        return Path(sys._MEIPASS)
    # Project root (where build/ is located)
    return Path(__file__).resolve().parent.parent.parent.parent


def get_pesu_host_dll_path() -> Path:
    """Resolves the precompiled PesuHost.dll helper assembly.

    Searches:
    1. In frozen bundle: _MEIPASS/native/PesuHost.dll or _MEIPASS/PesuHost.dll
    2. In development: <project_root>/build/native/PesuHost.dll

    Fails closed if the DLL is missing.
    """
    root = get_bundle_root()
    candidates = [
        root / "native" / "PesuHost.dll",
        root / "build" / "native" / "PesuHost.dll",
        root / "PesuHost.dll",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            logger.debug(f"Resolved PesuHost.dll at: {candidate}")
            return candidate

    raise PesuHostAssemblyError(
        PesuHostFailureCode.PESU_HOST_ASSEMBLY_NOT_AVAILABLE,
        f"Precompiled PesuHost.dll not found in candidate paths: {[str(c) for c in candidates]}. "
        "Runtime CodeDom compilation is strictly disabled. Run 'python scripts/build_pesu_host.py' first.",
        details={"candidates": [str(c) for c in candidates]},
    )


def get_webview2_dependencies() -> tuple[Path, Path, Path]:
    """Resolves Microsoft.Web.WebView2.Core.dll, WinForms.dll, and x64 WebView2Loader directory.

    Returns:
        tuple[core_dll_path, winforms_dll_path, x64_loader_dir_path]
    """
    root = get_bundle_root()

    # 1. Check frozen / bundled layout
    frozen_core = root / "native" / "Microsoft.Web.WebView2.Core.dll"
    if not frozen_core.exists():
        frozen_core = root / "Microsoft.Web.WebView2.Core.dll"

    frozen_wf = root / "native" / "Microsoft.Web.WebView2.WinForms.dll"
    if not frozen_wf.exists():
        frozen_wf = root / "Microsoft.Web.WebView2.WinForms.dll"

    frozen_loader_dir = root / "native" / "runtimes" / "win-x64" / "native"
    if not frozen_loader_dir.exists():
        frozen_loader_dir = root / "runtimes" / "win-x64" / "native"

    if frozen_core.exists() and frozen_wf.exists() and (frozen_loader_dir / "WebView2Loader.dll").exists():
        return frozen_core, frozen_wf, frozen_loader_dir

    # 2. In development mode, discover from pywebview installation root
    try:
        from webview.util import interop_dll_path
        core_path = Path(interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
        wf_path = Path(interop_dll_path("Microsoft.Web.WebView2.WinForms.dll"))
        loader_dir = Path(interop_dll_path("win-x64"))
        loader_file = loader_dir / "WebView2Loader.dll"

        if core_path.exists() and wf_path.exists() and loader_file.exists():
            return core_path, wf_path, loader_dir
    except Exception as e:
        logger.debug(f"pywebview interop_dll_path resolution failed: {e}")

    # Fallback to direct candidates
    candidates_core = [
        root / "build" / "native" / "Microsoft.Web.WebView2.Core.dll",
    ]
    for c in candidates_core:
        if c.exists():
            wf_cand = c.parent / "Microsoft.Web.WebView2.WinForms.dll"
            loader_cand = c.parent / "runtimes" / "win-x64" / "native"
            if wf_cand.exists() and (loader_cand / "WebView2Loader.dll").exists():
                return c, wf_cand, loader_cand

    raise PesuHostAssemblyError(
        PesuHostFailureCode.WEBVIEW2_DEPENDENCY_NOT_FOUND,
        "Failed to resolve consistent WebView2 assemblies (Core, WinForms, and win-x64 Loader).",
    )


def setup_webview2_runtime_environment() -> tuple[Path, Path]:
    """Loads WebView2 dependencies into CLR and configures PATH for win-x64 WebView2Loader.dll."""
    import clr

    core_dll, winforms_dll, loader_dir = get_webview2_dependencies()

    clr.AddReference(str(core_dll))
    clr.AddReference(str(winforms_dll))

    loader_dir_str = str(loader_dir)
    current_path = os.environ.get("Path", "")
    if loader_dir_str not in current_path:
        os.environ["Path"] = loader_dir_str + ";" + current_path

    return core_dll, winforms_dll
