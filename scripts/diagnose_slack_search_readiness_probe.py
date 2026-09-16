#!/usr/bin/env python
"""Task 11R10A — Slack Search UI Readiness / Modal Mount Timeout Forensic Probe.

Objective:
  Diagnostically probe whether search modal and search input readiness
  reliably mount upon top nav search button activation in the dedicated
  PesuWebViewHost direct runtime, without performing searches or queries.

Strict Invariants:
  - PROBE_ITERATIONS = 1
  - SLACK_WRITE_ACTIONS = 0
  - QUERY_MUTATION = 0
  - TEXTY_SET_CONTENTS_CALLS = 0
  - SUBMIT_DISPATCHED = 0
  - SEARCH_RESULTS_RETRIEVED = False
  - SEARCH_UI_ACTIVATION_DISPATCHED <= 1
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Optional

# Project root
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(project_root / "src") not in sys.path:
    sys.path.insert(0, str(project_root / "src"))

from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuWebViewHost,
)
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    SEARCH_UI_READY_TIMEOUT_SECONDS,
    SEARCH_INPUT_READINESS_TIMEOUT_SECONDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slack_readiness_probe")

# Invariants
PROBE_ITERATIONS = 1
SLACK_WRITE_ACTIONS = 0
QUERY_MUTATION = 0
TEXTY_SET_CONTENTS_CALLS = 0
SUBMIT_DISPATCHED = 0
SEARCH_RESULTS_RETRIEVED = False

JS_PRE_ACTIVATION_INSPECTION = r"""
(() => {
    const btn = document.querySelector('button[data-qa="top_nav_search"]');
    if (!btn) {
        return {
            exists: false,
            connected: false,
            visible: false,
            has_react_fiber: false,
            has_react_props: false,
            modal_count_before: 0,
            input_count_before: 0
        };
    }
    const isConnected = Boolean(btn.isConnected);
    const rect = btn.getBoundingClientRect();
    const isVisible = (rect.width > 0 && rect.height > 0) || (btn.offsetWidth > 0 || btn.offsetHeight > 0);
    const fiberKey = Object.keys(btn).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    const fiber = fiberKey ? btn[fiberKey] : null;
    const propsKey = Object.keys(btn).find(k => k.startsWith('__reactProps$'));
    const hasFiber = Boolean(fiber);
    const hasProps = Boolean(propsKey || (fiber && fiber.memoizedProps));

    const modalCandidates = document.querySelectorAll('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal');
    const inputCandidates = document.querySelectorAll('[data-qa="search_input_box"] .ql-editor');

    return {
        exists: true,
        connected: isConnected,
        visible: isVisible,
        has_react_fiber: hasFiber,
        has_react_props: hasProps,
        modal_count_before: modalCandidates.length,
        input_count_before: inputCandidates.length
    };
})()
"""

JS_INSPECT_SHELL_STATE = r"""
(() => {
    const isLoginScreen = Boolean(document.querySelector('input[type="email"], input[name="email"], [data-qa="signin_email_btn"]'));
    if (isLoginScreen) return { ready: false, auth_required: true, search_button_count: 0 };
    const searchButtons = document.querySelectorAll('button[data-qa="top_nav_search"]');
    return {
        ready: searchButtons.length === 1,
        auth_required: false,
        search_button_count: searchButtons.length,
        document_ready_state: document.readyState || 'unknown',
        document_visibility_state: document.visibilityState || 'unknown',
        document_has_focus: typeof document.hasFocus === 'function' ? document.hasFocus() : false
    };
})()
"""


def compute_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    h.update(filepath.read_bytes())
    return h.hexdigest()


def run_probe() -> dict[str, Any]:
    logger.info("================================================================================")
    logger.info("  TASK 11R10A — SLACK SEARCH UI READINESS FORENSIC PROBE")
    logger.info(f"  Profile: {DIRECT_SLACK_PROFILE_PATH}")
    logger.info(f"  Probe Iterations: {PROBE_ITERATIONS}")
    logger.info(f"  Slack Write Actions: {SLACK_WRITE_ACTIONS}")
    logger.info(f"  Query Mutation: {QUERY_MUTATION}")
    logger.info(f"  Submit Dispatched: {SUBMIT_DISPATCHED}")
    logger.info("================================================================================")

    telemetry: dict[str, Any] = {
        "HOST_ATTACHED": False,
        "APP_SHELL_READY": False,
        "TOP_NAV_ELEMENT_CONNECTED": False,
        "TOP_NAV_ELEMENT_VISIBLE": False,
        "TOP_NAV_REACT_FIBER_PRESENT": False,
        "TOP_NAV_REACT_PROPS_PRESENT": False,
        "SEARCH_ACTIVATION_EVENT_DISPATCHED": False,
        "SEARCH_ACTIVATION_EFFECT_OBSERVED": False,
        "MODAL_EVER_OBSERVED": False,
        "INPUT_EVER_OBSERVED": False,
        "VERIFIED_SEARCH_INPUT_READY": False,
        "HOST_START_TO_NAVIGATION_COMPLETE_MS": None,
        "NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS": None,
        "APP_SHELL_READY_TO_ACTIVATION_MS": None,
        "ACTIVATION_TO_FIRST_MODAL_MS": None,
        "ACTIVATION_TO_FIRST_INPUT_MS": None,
        "ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS": None,
        "READINESS_POLL_COUNT": 0,
        "FINAL_MODAL_COUNT": 0,
        "FINAL_INPUT_COUNT": 0,
        "MODAL_COUNT_BEFORE_ACTIVATION": 0,
        "INPUT_COUNT_BEFORE_ACTIVATION": 0,
        "SEARCH_UI_ACTIVATION_DISPATCHED": 0,
        "SEARCH_RESULTS_RETRIEVED": False,
        "PRIMARY_LOSS_POINT": "NONE",
        "ERROR": None,
    }

    host: Optional[PesuWebViewHost] = None
    t0_start = time.perf_counter()

    try:
        # Step 1: Instantiate & Start Host
        logger.info("🌐 [PROBE_STEP_1] Starting offscreen PesuWebViewHost...")
        host = PesuWebViewHost(
            user_data_folder=str(DIRECT_SLACK_PROFILE_PATH),
            initial_url="https://app.slack.com/client",
            title="PESU Search UI Readiness Probe (Forensic)",
        )

        host.start(timeout=30.0)
        t_nav_complete = time.perf_counter()
        nav_ms = round((t_nav_complete - t0_start) * 1000, 1)
        telemetry["HOST_START_TO_NAVIGATION_COMPLETE_MS"] = nav_ms
        telemetry["HOST_ATTACHED"] = bool(host.hwnd and host.is_ready)
        logger.info(f"✔ Host attached: hwnd={host.hwnd}, is_ready={host.is_ready} (nav_latency={nav_ms}ms)")

        # Step 2: Poll for App Shell Readiness (top_nav_search cardinality == 1)
        logger.info("🔍 [PROBE_STEP_2] Polling for Slack Workspace Shell readiness...")
        app_shell_start = time.perf_counter()
        app_shell_ready = False
        res_shell: Any = None

        while time.perf_counter() - app_shell_start < SEARCH_UI_READY_TIMEOUT_SECONDS:
            try:
                res_shell = host.evaluate_js(JS_INSPECT_SHELL_STATE)
            except Exception as e:
                logger.warning(f"JS evaluation error during shell probe: {e}")
                res_shell = None

            if isinstance(res_shell, dict):
                if res_shell.get("auth_required"):
                    telemetry["PRIMARY_LOSS_POINT"] = "SLACK_AUTHENTICATION_REQUIRED"
                    telemetry["ERROR"] = "Slack authentication required in profile."
                    logger.error(f"❌ {telemetry['ERROR']}")
                    return telemetry

                if res_shell.get("ready"):
                    app_shell_ready = True
                    break

            time.sleep(0.1)

        t_shell_ready = time.perf_counter()
        shell_ms = round((t_shell_ready - t_nav_complete) * 1000, 1)
        telemetry["NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS"] = shell_ms
        telemetry["APP_SHELL_READY"] = app_shell_ready

        if not app_shell_ready:
            telemetry["PRIMARY_LOSS_POINT"] = "APP_SHELL_READY"
            telemetry["ERROR"] = f"Slack app shell search button not ready after {SEARCH_UI_READY_TIMEOUT_SECONDS}s: {res_shell}"
            logger.error(f"❌ {telemetry['ERROR']}")
            return telemetry

        logger.info(f"✔ App shell ready: button_count=1, state={res_shell} (latency_after_nav={shell_ms}ms)")

        # Step 3: Pre-Activation Read-Only Inspection
        logger.info("🔬 [PROBE_STEP_3] Pre-activation read-only inspection of top_nav_search...")
        pre_insp = host.evaluate_js(JS_PRE_ACTIVATION_INSPECTION)
        if isinstance(pre_insp, dict):
            telemetry["TOP_NAV_ELEMENT_CONNECTED"] = bool(pre_insp.get("connected"))
            telemetry["TOP_NAV_ELEMENT_VISIBLE"] = bool(pre_insp.get("visible"))
            telemetry["TOP_NAV_REACT_FIBER_PRESENT"] = bool(pre_insp.get("has_react_fiber"))
            telemetry["TOP_NAV_REACT_PROPS_PRESENT"] = bool(pre_insp.get("has_react_props"))
            telemetry["MODAL_COUNT_BEFORE_ACTIVATION"] = pre_insp.get("modal_count_before", 0)
            telemetry["INPUT_COUNT_BEFORE_ACTIVATION"] = pre_insp.get("input_count_before", 0)
            logger.info(f"  • Pre-activation inspection: {pre_insp}")

        # Step 4: Dispatch Search Activation (Max 1 Attempt)
        t_before_act = time.perf_counter()
        telemetry["APP_SHELL_READY_TO_ACTIVATION_MS"] = round((t_before_act - t_shell_ready) * 1000, 1)

        logger.info("⚡ [PROBE_STEP_4] Dispatching single synthetic mousedown activation...")
        res_act = host.evaluate_js(JS_ACTIVATE_SEARCH)
        telemetry["SEARCH_UI_ACTIVATION_DISPATCHED"] = 1
        activation_ok = bool(isinstance(res_act, dict) and res_act.get("activated"))
        telemetry["SEARCH_ACTIVATION_EVENT_DISPATCHED"] = activation_ok
        t_activation = time.perf_counter()
        logger.info(f"  • Activation dispatch result: {res_act} (dispatched={activation_ok})")

        if not activation_ok:
            telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_ACTIVATION_EVENT_DISPATCH"
            telemetry["ERROR"] = f"Activation dispatch failed: {res_act}"
            logger.error(f"❌ {telemetry['ERROR']}")
            return telemetry

        # Step 5: Bounded Passive Polling for Search Modal & Input Readiness
        logger.info(f"⏳ [PROBE_STEP_5] Passively polling search modal & input readiness (timeout={SEARCH_INPUT_READINESS_TIMEOUT_SECONDS}s)...")
        poll_start = time.perf_counter()
        poll_interval = 0.1
        first_modal_time: Optional[float] = None
        first_input_time: Optional[float] = None
        verified_ready_time: Optional[float] = None

        while time.perf_counter() - poll_start < SEARCH_INPUT_READINESS_TIMEOUT_SECONDS:
            telemetry["READINESS_POLL_COUNT"] += 1
            res_poll = host.evaluate_js(JS_CHECK_SEARCH_INPUT_READINESS)

            if isinstance(res_poll, dict):
                m_cnt = res_poll.get("modal_count", 0)
                i_cnt = res_poll.get("input_count", 0)
                telemetry["FINAL_MODAL_COUNT"] = m_cnt
                telemetry["FINAL_INPUT_COUNT"] = i_cnt

                if m_cnt > 0 and not telemetry["MODAL_EVER_OBSERVED"]:
                    telemetry["MODAL_EVER_OBSERVED"] = True
                    first_modal_time = time.perf_counter()
                    telemetry["ACTIVATION_TO_FIRST_MODAL_MS"] = round((first_modal_time - t_activation) * 1000, 1)
                    logger.info(f"  ✨ First modal observed at {telemetry['ACTIVATION_TO_FIRST_MODAL_MS']}ms")

                if i_cnt > 0 and not telemetry["INPUT_EVER_OBSERVED"]:
                    telemetry["INPUT_EVER_OBSERVED"] = True
                    first_input_time = time.perf_counter()
                    telemetry["ACTIVATION_TO_FIRST_INPUT_MS"] = round((first_input_time - t_activation) * 1000, 1)
                    logger.info(f"  ✨ First input observed at {telemetry['ACTIVATION_TO_FIRST_INPUT_MS']}ms")

                if res_poll.get("ready"):
                    verified_ready_time = time.perf_counter()
                    telemetry["VERIFIED_SEARCH_INPUT_READY"] = True
                    telemetry["ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS"] = round((verified_ready_time - t_activation) * 1000, 1)
                    logger.info(f"  🎉 Verified search input ready at {telemetry['ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS']}ms")
                    break

            time.sleep(poll_interval)

        telemetry["SEARCH_ACTIVATION_EFFECT_OBSERVED"] = bool(
            telemetry["MODAL_EVER_OBSERVED"] or telemetry["INPUT_EVER_OBSERVED"]
        )

        if not telemetry["VERIFIED_SEARCH_INPUT_READY"]:
            if not telemetry["MODAL_EVER_OBSERVED"]:
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_MODAL_MOUNT"
                telemetry["ERROR"] = "Search modal never mounted within 5.0s (CASE_B_MODAL_NOT_MOUNTED reproduced)."
            elif not telemetry["INPUT_EVER_OBSERVED"]:
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_INPUT_MOUNT"
                telemetry["ERROR"] = "Modal mounted, but search input box never mounted within 5.0s."
            else:
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_INPUT_FIBER_VERIFICATION"
                telemetry["ERROR"] = f"Input mounted, but strong SearchInputBox fiber verification failed: {res_poll}"
            logger.error(f"❌ {telemetry['ERROR']}")
        else:
            telemetry["PRIMARY_LOSS_POINT"] = "NONE"
            logger.info("✅ Search input readiness verified successfully!")

    except Exception as ex:
        telemetry["ERROR"] = str(ex)
        logger.exception(f"Unexpected probe exception: {ex}")
    finally:
        if host:
            logger.info("🛑 [PROBE_CLEANUP] Destroying PesuWebViewHost...")
            try:
                host.destroy()
            except Exception as e:
                logger.warning(f"Error destroying host: {e}")

    return telemetry


if __name__ == "__main__":
    result = run_probe()
    print("\n--- FORENSIC PROBE RAW RESULT ---")
    print(json.dumps(result, indent=2))
