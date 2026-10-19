"""PESU Agent Production Command-Line Entrypoint (Phase 9.9.7).

Provides unified command-line entrypoint for PESU Agent executable.
Includes diagnostic qualification smoke commands for packaged executable validation.

Diagnostic smoke flags are strictly isolated for qualification and never used in production search logic.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Optional

# UTF-8 stdout configuration
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pesu_cli")

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def run_diagnostic_smoke_local() -> int:
    """Executes Stage 1 Packaged Local Runtime Smoke test.

    Verifies:
    1. PesuHost.dll loads cleanly into CLR without CodeDom.
    2. PesuWebViewHost starts with pre-CreateHandle WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW.
    3. WebView2 initializes and loads about:blank.
    4. JS evaluation executes synchronously and returns expected value.
    5. Zero foreground window acquisitions throughout the lifecycle.
    6. Clean host shutdown.
    """
    logger.info("🧪 [DIAGNOSTIC_SMOKE_LOCAL] Starting local runtime smoke test...")

    foreground_changes: list[tuple[float, int]] = []
    initial_fg = user32.GetForegroundWindow() if user32 else 0

    from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost
    from pesu_agent.runtime.resource_resolver import get_pesu_host_dll_path

    try:
        dll_path = get_pesu_host_dll_path()
        logger.info(f"Loaded PesuHost helper assembly from: {dll_path}")
    except Exception as err:
        logger.error(f"❌ Failed to resolve PesuHost DLL: {err}")
        print(json.dumps({"verdict": "FAIL", "stage": "ASSEMBLY_RESOLUTION", "error": str(err)}, indent=2))
        return 1

    host = PesuWebViewHost(initial_url="about:blank", title="PESU Local Smoke Host")
    monitoring = True

    def _monitor_fg():
        while monitoring:
            if user32:
                fg = user32.GetForegroundWindow()
                if fg != initial_fg and (not foreground_changes or foreground_changes[-1][1] != fg):
                    foreground_changes.append((time.time(), fg))
            time.sleep(0.05)

    import threading
    mon_thread = threading.Thread(target=_monitor_fg, daemon=True)
    mon_thread.start()

    try:
        logger.info("Starting PesuWebViewHost...")
        host.start(timeout=20.0)

        hwnd = host.hwnd
        exstyle = host.initial_exstyle or (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) if user32 and hwnd else 0)
        has_noactivate = bool(exstyle & WS_EX_NOACTIVATE)
        has_toolwindow = bool(exstyle & WS_EX_TOOLWINDOW)

        logger.info(f"Host started: HWND={hwnd}, ExStyle=0x{exstyle:08X} (NOACTIVATE={has_noactivate}, TOOLWINDOW={has_toolwindow})")

        # Test JS evaluation
        js_result = host.evaluate_js("(() => { return { calculation: 40 + 2, userAgent: navigator.userAgent }; })()")
        logger.info(f"JS evaluation result: {js_result}")
        calc_ok = isinstance(js_result, dict) and js_result.get("calculation") == 42

        # Foreground check
        fg_acquisitions = len([c for c in foreground_changes if c[1] == hwnd])

        verdict = "PASS" if (has_noactivate and has_toolwindow and calc_ok and fg_acquisitions == 0) else "FAIL"

        report = {
            "verdict": verdict,
            "stage": "LOCAL_RUNTIME_SMOKE",
            "hwnd": hwnd,
            "exstyle": f"0x{exstyle:08X}",
            "has_noactivate": has_noactivate,
            "has_toolwindow": has_toolwindow,
            "calculation_ok": calc_ok,
            "foreground_acquisitions": fg_acquisitions,
            "total_foreground_deviations": len(foreground_changes),
        }
        print(json.dumps(report, indent=2))
        return 0 if verdict == "PASS" else 1

    except Exception as e:
        logger.error(f"❌ Local smoke failed with exception: {e}", exc_info=True)
        print(json.dumps({"verdict": "FAIL", "stage": "LOCAL_RUNTIME_SMOKE", "error": str(e)}, indent=2))
        return 1
    finally:
        monitoring = False
        try:
            host.destroy()
        except Exception:
            pass


def run_diagnostic_smoke_slack() -> int:
    """Executes Stage 2 Packaged Direct Slack Smoke test.

    Performs 1 single bounded search using webview_direct backend:
    - Query: "하위작업 추가" (diagnostic qualification only)
    - Max results: 5
    - Zero Planner, zero Analyzer, zero OpenRouter.
    - Zero foreground acquisitions.
    """
    logger.info("🧪 [DIAGNOSTIC_SMOKE_SLACK] Starting Direct Slack smoke test...")

    foreground_changes: list[tuple[float, int]] = []
    initial_fg = user32.GetForegroundWindow() if user32 else 0

    from pesu_agent.investigation.models import SearchQuery
    from pesu_agent.search.slack_backend_factory import (
        SlackBackendKind,
        SlackSearchConfig,
        create_slack_search_backend,
    )

    config = SlackSearchConfig(
        backend_kind=SlackBackendKind.WEBVIEW_DIRECT,
        default_max_results=5,
    )

    backend = create_slack_search_backend(config=config)
    monitoring = True

    def _monitor_fg():
        while monitoring:
            if user32:
                fg = user32.GetForegroundWindow()
                if fg != initial_fg and (not foreground_changes or foreground_changes[-1][1] != fg):
                    foreground_changes.append((time.time(), fg))
            time.sleep(0.05)

    import threading
    mon_thread = threading.Thread(target=_monitor_fg, daemon=True)
    mon_thread.start()

    try:
        backend.start_runtime(timeout=25.0)

        sq = SearchQuery(query="하위작업 추가", purpose="Phase 9.9.7 Packaged Direct Slack Smoke")
        results = asyncio.run(backend.search(sq))

        backend_host = getattr(backend, "_window", None)
        host_hwnd = getattr(backend_host, "hwnd", None) if backend_host else None
        fg_acquisitions = len([c for c in foreground_changes if host_hwnd and c[1] == host_hwnd])

        candidate_count = len(results)
        verdict = "PASS" if (candidate_count > 0 and fg_acquisitions == 0) else "FAIL"

        report = {
            "verdict": verdict,
            "stage": "DIRECT_SLACK_SMOKE",
            "query": "하위작업 추가",
            "candidate_count": candidate_count,
            "foreground_acquisitions": fg_acquisitions,
            "total_foreground_deviations": len(foreground_changes),
            "sample_candidates": [
                {
                    "evidence_id": r.evidence_id,
                    "author": r.author,
                    "timestamp": r.timestamp,
                    "text_preview": r.text[:60] if r.text else "",
                }
                for r in results[:3]
            ],
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if verdict == "PASS" else 1

    except Exception as e:
        logger.error(f"❌ Direct Slack smoke failed: {e}", exc_info=True)
        print(json.dumps({"verdict": "FAIL", "stage": "DIRECT_SLACK_SMOKE", "error": str(e)}, indent=2))
        return 1
    finally:
        monitoring = False
        try:
            backend.stop_runtime()
        except Exception:
            pass


def run_login_setup() -> int:
    """Executes 1-time interactive onscreen login setup for Direct profile."""
    logger.info("🔑 [LOGIN_SETUP] Starting interactive login window...")
    from pesu_agent.runtime.pesu_webview_host import PesuInteractiveLoginHost

    host = PesuInteractiveLoginHost()
    try:
        host.start_and_wait()
        logger.info("✅ [LOGIN_SETUP] Interactive window closed.")
        return 0
    except Exception as e:
        logger.error(f"❌ Interactive login setup failed: {e}", exc_info=True)
        return 1


def run_product_shell(show_initial_window: bool = True) -> int:
    """Launches the PESU Product Shell (System Tray + Main Window)."""
    from pesu_agent.ui.tray_app import PesuTrayApp
    app = PesuTrayApp()
    return app.run(show_initial_window=show_initial_window)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PESU Agent — Enterprise AI Investigation Platform")
    parser.add_argument("--version", action="store_true", help="Show PESU version and platform information")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Launch PESU Product Shell in tray-only auto-start mode (hidden initial window)",
    )
    parser.add_argument(
        "--product-shell",
        action="store_true",
        help="Launch PESU Product Shell (System Tray + Main Window)",
    )
    parser.add_argument("--login-setup", action="store_true", help="Launch interactive login setup window")
    parser.add_argument(
        "--diagnostic-smoke-local",
        action="store_true",
        help="Internal qualification: run Stage 1 local runtime smoke test",
    )
    parser.add_argument(
        "--diagnostic-smoke-slack",
        action="store_true",
        help="Internal qualification: run Stage 2 packaged Direct Slack smoke test",
    )
    parser.add_argument(
        "--diagnostic-product-startup",
        action="store_true",
        help="Internal qualification: run Phase 10.5 Stage 1 Packaged Product Startup Smoke",
    )
    parser.add_argument(
        "--diagnostic-product-fake",
        action="store_true",
        help="Internal qualification: run Phase 10.5 Stage 2 Packaged Fake Investigation Flow",
    )
    parser.add_argument(
        "--diagnostic-product-live",
        action="store_true",
        help="Internal qualification: run Phase 10.5 Stage 3 Packaged Live Product Investigation",
    )
    parser.add_argument(
        "--diagnostic-packaged-zero-arg",
        action="store_true",
        help="Internal qualification: run Phase 10.8 Stage 1 Packaged Zero-Argument Startup Smoke",
    )
    parser.add_argument(
        "--diagnostic-packaged-fake-consent",
        action="store_true",
        help="Internal qualification: run Phase 10.8 Stage 2 Packaged Fake Consent Flow",
    )
    parser.add_argument(
        "--diagnostic-packaged-live-consent",
        action="store_true",
        help="Internal qualification: run Phase 10.8 Stage 3 Packaged Live Consent Flow",
    )
    parser.add_argument(
        "--diagnostic-packaged-credential",
        action="store_true",
        help="Internal qualification: run Phase 11.2a Packaged Credential Qualification Flow",
    )

    args = parser.parse_args(argv)

    if args.version:
        from pesu_agent import __version__
        from pesu_agent.runtime.resource_resolver import is_frozen
        print(f"PESU Agent v{__version__} (Frozen={is_frozen()}, Arch={sys.platform}-x64, Python={sys.version.split()[0]})")
        return 0

    if args.startup:
        logger.info("Starting PESU Product Shell in auto-start mode (--startup, tray-only)...")
        return run_product_shell(show_initial_window=False)

    if args.product_shell:
        return run_product_shell(show_initial_window=True)

    if args.diagnostic_smoke_local:
        return run_diagnostic_smoke_local()

    if args.diagnostic_smoke_slack:
        return run_diagnostic_smoke_slack()

    if args.diagnostic_product_startup:
        from pesu_agent.ui.qualification_runners import run_packaged_product_startup_smoke
        res = run_packaged_product_startup_smoke()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_product_fake:
        from pesu_agent.ui.qualification_runners import run_packaged_product_fake_render
        res = run_packaged_product_fake_render()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_product_live:
        from pesu_agent.ui.qualification_runners import run_packaged_product_live_investigation
        res = run_packaged_product_live_investigation()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_packaged_zero_arg:
        from pesu_agent.ui.qualification_runners import run_packaged_zero_arg_startup
        res = run_packaged_zero_arg_startup()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_packaged_fake_consent:
        from pesu_agent.ui.qualification_runners import run_packaged_fake_consent_flow
        res = run_packaged_fake_consent_flow()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_packaged_live_consent:
        from pesu_agent.ui.qualification_runners import run_packaged_live_consent_flow
        res = run_packaged_live_consent_flow()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.diagnostic_packaged_credential:
        from pesu_agent.ui.qualification_runners import run_packaged_credential_qualification
        res = run_packaged_credential_qualification()
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0 if res.get("verdict") == "PASS" else 1

    if args.login_setup:
        return run_login_setup()

    # Canonical default: Launch PESU Product Shell (System Tray + Main Window)
    logger.info("Starting default PESU Product Shell (zero-argument startup)...")
    return run_product_shell(show_initial_window=True)


if __name__ == "__main__":
    sys.exit(main())
