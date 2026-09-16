#!/usr/bin/env python
"""Task 11R10B — Repeated Cold-Start Slack Search UI Readiness Characterization.

Objective:
  Perform a bounded repeated characterization (5 iterations) of Slack search UI
  readiness under fresh-host startup conditions to determine whether
  CASE_B_MODAL_NOT_MOUNTED reproduces and whether readiness is intermittent/flaky.

Strict Invariants:
  - PROBE_ITERATIONS = 5
  - FRESH_PESU_WEBVIEW_HOST_PER_ITERATION = True
  - FULL_SLACK_PROCESS_COLD_START_PER_ITERATION = False
  - SAME_SLACK_PROFILE_RUNTIME = True
  - SLACK_RESTART_BETWEEN_ITERATIONS = False
  - HOST_INSTANCE_REUSED = False
  - SLACK_WRITE_ACTIONS = 0
  - QUERY_MUTATION = 0
  - TEXTY_SET_CONTENTS_CALLS = 0
  - SUBMIT_DISPATCHED = 0
  - SEARCH_RESULTS_RETRIEVED = False
  - MAX_SEARCH_UI_ACTIVATION_DISPATCHES_PER_ITERATION = 1
  - PROBE_ACTIVATION_LOGIC_DIVERGES_FROM_PRODUCTION = False
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import statistics
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
logger = logging.getLogger("slack_repeated_probe")

# Invariants
PROBE_ITERATIONS = 5
FRESH_PESU_WEBVIEW_HOST_PER_ITERATION = True
FULL_SLACK_PROCESS_COLD_START_PER_ITERATION = False
SAME_SLACK_PROFILE_RUNTIME = True
SLACK_RESTART_BETWEEN_ITERATIONS = False
SLACK_WRITE_ACTIONS = 0
QUERY_MUTATION = 0
TEXTY_SET_CONTENTS_CALLS = 0
SUBMIT_DISPATCHED = 0
SEARCH_RESULTS_RETRIEVED = False
MAX_SEARCH_UI_ACTIVATION_DISPATCHES_PER_ITERATION = 1
PROBE_ACTIVATION_LOGIC_DIVERGES_FROM_PRODUCTION = False

JS_INSPECT_DOC_STATE = r"""
(() => ({
    document_ready_state: document.readyState || 'unknown',
    document_visibility_state: document.visibilityState || 'unknown'
}))()
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


def compute_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    h.update(filepath.read_bytes())
    return h.hexdigest()


def verify_host_teardown(
    prev_host: PesuWebViewHost,
    prev_thread: Any,
    prev_hwnd: Optional[int],
    prev_destroy_returned: bool,
) -> dict[str, Any]:
    """Deterministically verifies host teardown completeness as a hard gate."""
    is_hwnd_valid = False
    if prev_hwnd:
        try:
            user32 = ctypes.windll.user32
            is_hwnd_valid = bool(user32.IsWindow(prev_hwnd))
        except Exception as e:
            logger.warning(f"Error checking IsWindow: {e}")
            is_hwnd_valid = False

    thread_alive = False
    if prev_thread:
        try:
            thread_alive = bool(
                prev_thread.IsAlive if hasattr(prev_thread, "IsAlive") else prev_thread.is_alive()
            )
        except Exception as e:
            logger.warning(f"Error checking thread is_alive: {e}")
            thread_alive = False

    teardown_confirmed = (
        prev_destroy_returned
        and not thread_alive
        and not is_hwnd_valid
        and prev_host._form is None
        and prev_host._webview is None
        and not prev_host.is_ready
    )

    return {
        "PREVIOUS_HOST_DESTROY_RETURNED": prev_destroy_returned,
        "PREVIOUS_HOST_MESSAGE_LOOP_THREAD_ALIVE": thread_alive,
        "PREVIOUS_HOST_HWND_VALID": is_hwnd_valid,
        "PREVIOUS_HOST_FORM_NONE": prev_host._form is None,
        "PREVIOUS_HOST_WEBVIEW_NONE": prev_host._webview is None,
        "PREVIOUS_HOST_IS_READY_FALSE": not prev_host.is_ready,
        "PREVIOUS_HOST_TEARDOWN_CONFIRMED": teardown_confirmed,
    }


def run_iteration(iteration_id: int) -> dict[str, Any]:
    """Runs a single cold-start readiness probe iteration on a fresh PesuWebViewHost."""
    logger.info(f"================================================================================")
    logger.info(f"  TASK 11R10B — ITERATION {iteration_id} / {PROBE_ITERATIONS}")
    logger.info(f"  Fresh Host Instance: HOST_INSTANCE_REUSED = false")
    logger.info(f"================================================================================")

    now_utc = lambda: datetime.now(timezone.utc).isoformat()

    telemetry: dict[str, Any] = {
        "ITERATION_ID": iteration_id,
        "HOST_INSTANCE_REUSED": False,
        "HOST_START_TIMESTAMP": None,
        "NAVIGATION_COMPLETE_TIMESTAMP": None,
        "HOST_ATTACHED": False,
        "DOCUMENT_READY_STATE": None,
        "DOCUMENT_VISIBILITY_STATE": None,
        "HOST_START_TO_NAVIGATION_COMPLETE_MS": None,
        "APP_SHELL_READY_TIMESTAMP": None,
        "SEARCH_BUTTON_COUNT": 0,
        "TOP_NAV_ELEMENT_CONNECTED": False,
        "TOP_NAV_ELEMENT_VISIBLE": False,
        "TOP_NAV_REACT_FIBER_PRESENT": False,
        "TOP_NAV_REACT_PROPS_PRESENT": False,
        "NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS": None,
        "APP_SHELL_READY_TO_ACTIVATION_MS": None,
        "ACTIVATION_TIMESTAMP": None,
        "SEARCH_ACTIVATION_EVENT_DISPATCHED": False,
        "ACTIVATION_DISPATCH_RETURN_VALUE": None,
        "FIRST_MODAL_OBSERVED_TIMESTAMP": None,
        "FIRST_INPUT_OBSERVED_TIMESTAMP": None,
        "VERIFIED_INPUT_TIMESTAMP": None,
        "MODAL_EVER_OBSERVED": False,
        "INPUT_EVER_OBSERVED": False,
        "VERIFIED_SEARCH_INPUT_READY": False,
        "ACTIVATION_TO_FIRST_MODAL_MS": None,
        "ACTIVATION_TO_FIRST_INPUT_MS": None,
        "ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS": None,
        "RESULT": "FAIL_HOST_ATTACH",
        "PRIMARY_LOSS_POINT": "HOST_ATTACH",
        "FAILURE_STAGE": None,
        "TIME_AT_FAILURE_MS": None,
        "READINESS_POLL_COUNT": 0,
        "FINAL_MODAL_COUNT": 0,
        "FINAL_INPUT_COUNT": 0,
        "MODAL_COUNT_BEFORE_ACTIVATION": 0,
        "INPUT_COUNT_BEFORE_ACTIVATION": 0,
        "ERROR": None,
        # Teardown audit references
        "_teardown_meta": {
            "prev_thread": None,
            "prev_hwnd": None,
            "prev_destroy_returned": False,
            "host_ref": None,
        },
    }

    host: Optional[PesuWebViewHost] = None
    t0_start = time.perf_counter()
    telemetry["HOST_START_TIMESTAMP"] = now_utc()

    try:
        # Step 1: Instantiate & Start Host
        logger.info(f"🌐 [IT_{iteration_id}_STEP_1] Starting fresh offscreen PesuWebViewHost...")
        host = PesuWebViewHost(
            user_data_folder=str(DIRECT_SLACK_PROFILE_PATH),
            initial_url="https://app.slack.com/client",
            title=f"PESU Search UI Readiness Probe (It {iteration_id})",
        )

        host.start(timeout=30.0)
        t_nav_complete = time.perf_counter()
        telemetry["NAVIGATION_COMPLETE_TIMESTAMP"] = now_utc()
        nav_ms = round((t_nav_complete - t0_start) * 1000, 1)
        telemetry["HOST_START_TO_NAVIGATION_COMPLETE_MS"] = nav_ms
        telemetry["HOST_ATTACHED"] = bool(host.hwnd and host.is_ready)
        logger.info(f"✔ Host attached: hwnd={host.hwnd}, is_ready={host.is_ready} (nav_latency={nav_ms}ms)")

        # Inspect immediate doc state
        try:
            doc_state = host.evaluate_js(JS_INSPECT_DOC_STATE)
            if isinstance(doc_state, dict):
                telemetry["DOCUMENT_READY_STATE"] = doc_state.get("document_ready_state")
                telemetry["DOCUMENT_VISIBILITY_STATE"] = doc_state.get("document_visibility_state")
        except Exception as e:
            logger.warning(f"Error querying immediate document state: {e}")

        if not telemetry["HOST_ATTACHED"]:
            telemetry["RESULT"] = "FAIL_HOST_ATTACH"
            telemetry["PRIMARY_LOSS_POINT"] = "HOST_ATTACH"
            telemetry["FAILURE_STAGE"] = "HOST_START"
            telemetry["TIME_AT_FAILURE_MS"] = nav_ms
            telemetry["ERROR"] = "Host failed to attach or become ready."
            logger.error(f"❌ {telemetry['ERROR']}")
            return telemetry

        # Step 2: Poll for App Shell Readiness
        logger.info(f"🔍 [IT_{iteration_id}_STEP_2] Polling for Slack Workspace Shell readiness...")
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
                    telemetry["RESULT"] = "FAIL_APP_SHELL_READY"
                    telemetry["PRIMARY_LOSS_POINT"] = "SLACK_AUTHENTICATION_REQUIRED"
                    telemetry["FAILURE_STAGE"] = "APP_SHELL_POLL"
                    telemetry["TIME_AT_FAILURE_MS"] = round((time.perf_counter() - t0_start) * 1000, 1)
                    telemetry["ERROR"] = "Slack authentication required in profile."
                    logger.error(f"❌ {telemetry['ERROR']}")
                    return telemetry

                if res_shell.get("ready"):
                    app_shell_ready = True
                    telemetry["SEARCH_BUTTON_COUNT"] = res_shell.get("search_button_count", 1)
                    if not telemetry["DOCUMENT_READY_STATE"]:
                        telemetry["DOCUMENT_READY_STATE"] = res_shell.get("document_ready_state")
                    if not telemetry["DOCUMENT_VISIBILITY_STATE"]:
                        telemetry["DOCUMENT_VISIBILITY_STATE"] = res_shell.get("document_visibility_state")
                    break

            time.sleep(0.1)

        t_shell_ready = time.perf_counter()
        telemetry["APP_SHELL_READY_TIMESTAMP"] = now_utc()
        shell_ms = round((t_shell_ready - t_nav_complete) * 1000, 1)
        telemetry["NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS"] = shell_ms

        if not app_shell_ready:
            telemetry["RESULT"] = "FAIL_APP_SHELL_READY"
            telemetry["PRIMARY_LOSS_POINT"] = "APP_SHELL_READY"
            telemetry["FAILURE_STAGE"] = "APP_SHELL_POLL"
            telemetry["TIME_AT_FAILURE_MS"] = round((time.perf_counter() - t0_start) * 1000, 1)
            telemetry["ERROR"] = f"Slack app shell search button not ready after {SEARCH_UI_READY_TIMEOUT_SECONDS}s: {res_shell}"
            logger.error(f"❌ {telemetry['ERROR']}")
            return telemetry

        logger.info(f"✔ App shell ready: button_count={telemetry['SEARCH_BUTTON_COUNT']} (latency_after_nav={shell_ms}ms)")

        # Step 3: Pre-Activation Read-Only Inspection
        logger.info(f"🔬 [IT_{iteration_id}_STEP_3] Pre-activation read-only inspection of top_nav_search...")
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
        act_lead_ms = round((t_before_act - t_shell_ready) * 1000, 1)
        telemetry["APP_SHELL_READY_TO_ACTIVATION_MS"] = act_lead_ms
        telemetry["ACTIVATION_TIMESTAMP"] = now_utc()

        logger.info(f"⚡ [IT_{iteration_id}_STEP_4] Dispatching single synthetic mousedown activation (lead={act_lead_ms}ms)...")
        res_act = host.evaluate_js(JS_ACTIVATE_SEARCH)
        telemetry["ACTIVATION_DISPATCH_RETURN_VALUE"] = res_act
        activation_ok = bool(isinstance(res_act, dict) and res_act.get("activated"))
        telemetry["SEARCH_ACTIVATION_EVENT_DISPATCHED"] = activation_ok
        t_activation = time.perf_counter()
        logger.info(f"  • Activation dispatch result: {res_act} (dispatched={activation_ok})")

        if not activation_ok:
            telemetry["RESULT"] = "FAIL_ACTIVATION_DISPATCH"
            telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_ACTIVATION_EVENT_DISPATCH"
            telemetry["FAILURE_STAGE"] = "ACTIVATION_DISPATCH"
            telemetry["TIME_AT_FAILURE_MS"] = round((time.perf_counter() - t0_start) * 1000, 1)
            telemetry["ERROR"] = f"Activation dispatch failed: {res_act}"
            logger.error(f"❌ {telemetry['ERROR']}")
            return telemetry

        # Step 5: Bounded Passive Polling for Search Modal & Input Readiness
        logger.info(f"⏳ [IT_{iteration_id}_STEP_5] Passively polling search modal & input readiness (timeout={SEARCH_INPUT_READINESS_TIMEOUT_SECONDS}s)...")
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
                    telemetry["FIRST_MODAL_OBSERVED_TIMESTAMP"] = now_utc()
                    telemetry["ACTIVATION_TO_FIRST_MODAL_MS"] = round((first_modal_time - t_activation) * 1000, 1)
                    logger.info(f"  ✨ First modal observed at {telemetry['ACTIVATION_TO_FIRST_MODAL_MS']}ms")

                if i_cnt > 0 and not telemetry["INPUT_EVER_OBSERVED"]:
                    telemetry["INPUT_EVER_OBSERVED"] = True
                    first_input_time = time.perf_counter()
                    telemetry["FIRST_INPUT_OBSERVED_TIMESTAMP"] = now_utc()
                    telemetry["ACTIVATION_TO_FIRST_INPUT_MS"] = round((first_input_time - t_activation) * 1000, 1)
                    logger.info(f"  ✨ First input observed at {telemetry['ACTIVATION_TO_FIRST_INPUT_MS']}ms")

                if res_poll.get("ready"):
                    verified_ready_time = time.perf_counter()
                    telemetry["VERIFIED_SEARCH_INPUT_READY"] = True
                    telemetry["VERIFIED_INPUT_TIMESTAMP"] = now_utc()
                    telemetry["ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS"] = round((verified_ready_time - t_activation) * 1000, 1)
                    logger.info(f"  🎉 Verified search input ready at {telemetry['ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS']}ms")
                    break

            time.sleep(poll_interval)

        if not telemetry["VERIFIED_SEARCH_INPUT_READY"]:
            fail_elapsed_ms = round((time.perf_counter() - t_activation) * 1000, 1)
            telemetry["TIME_AT_FAILURE_MS"] = fail_elapsed_ms
            if not telemetry["MODAL_EVER_OBSERVED"]:
                telemetry["RESULT"] = "FAIL_MODAL_NOT_MOUNTED"
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_MODAL_MOUNT"
                telemetry["FAILURE_STAGE"] = "SEARCH_MODAL_POLL"
                telemetry["ERROR"] = "Search modal never mounted within 5.0s (CASE_B_MODAL_NOT_MOUNTED reproduced)."
            elif not telemetry["INPUT_EVER_OBSERVED"]:
                telemetry["RESULT"] = "FAIL_INPUT_NOT_MOUNTED"
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_INPUT_MOUNT"
                telemetry["FAILURE_STAGE"] = "SEARCH_INPUT_POLL"
                telemetry["ERROR"] = "Modal mounted, but search input box never mounted within 5.0s."
            else:
                telemetry["RESULT"] = "FAIL_INPUT_NOT_VERIFIED"
                telemetry["PRIMARY_LOSS_POINT"] = "SEARCH_INPUT_FIBER_VERIFICATION"
                telemetry["FAILURE_STAGE"] = "SEARCH_INPUT_VERIFICATION"
                telemetry["ERROR"] = "Input mounted, but strong SearchInputBox fiber verification failed."
            logger.error(f"❌ {telemetry['ERROR']}")
        else:
            telemetry["RESULT"] = "PASS_VERIFIED_INPUT"
            telemetry["PRIMARY_LOSS_POINT"] = "NONE"
            telemetry["FAILURE_STAGE"] = None
            telemetry["TIME_AT_FAILURE_MS"] = None
            logger.info(f"✅ Iteration {iteration_id} passed!")

    except Exception as ex:
        telemetry["ERROR"] = str(ex)
        telemetry["RESULT"] = "FAIL_HOST_ATTACH" if not telemetry["HOST_ATTACHED"] else "FAIL_APP_SHELL_READY"
        telemetry["PRIMARY_LOSS_POINT"] = "EXCEPTION"
        telemetry["FAILURE_STAGE"] = "EXCEPTION"
        telemetry["TIME_AT_FAILURE_MS"] = round((time.perf_counter() - t0_start) * 1000, 1)
        logger.exception(f"Unexpected probe exception: {ex}")
    finally:
        if host:
            prev_thread = host._thread
            prev_hwnd = host._hwnd
            logger.info(f"🛑 [IT_{iteration_id}_CLEANUP] Destroying PesuWebViewHost...")
            try:
                host.destroy()
                destroy_returned = True
            except Exception as e:
                logger.warning(f"Error destroying host: {e}")
                destroy_returned = False

            telemetry["_teardown_meta"] = {
                "prev_thread": prev_thread,
                "prev_hwnd": prev_hwnd,
                "prev_destroy_returned": destroy_returned,
                "host_ref": host,
            }

    return telemetry


def run_all_iterations() -> dict[str, Any]:
    """Executes the full 5-iteration experiment with hard gate teardown checks between iterations."""
    logger.info("================================================================================")
    logger.info("  TASK 11R10B — REPEATED COLD-START SLACK SEARCH UI READINESS CHARACTERIZATION")
    logger.info(f"  Profile: {DIRECT_SLACK_PROFILE_PATH}")
    logger.info(f"  Iterations: {PROBE_ITERATIONS}")
    logger.info(f"  Fresh Host Per Iteration: {FRESH_PESU_WEBVIEW_HOST_PER_ITERATION}")
    logger.info(f"  Full Slack Cold Start: {FULL_SLACK_PROCESS_COLD_START_PER_ITERATION}")
    logger.info(f"  Slack Restart Between Iterations: {SLACK_RESTART_BETWEEN_ITERATIONS}")
    logger.info(f"  Write Actions: {SLACK_WRITE_ACTIONS}")
    logger.info(f"  Query Mutation: {QUERY_MUTATION}")
    logger.info(f"  Submit Dispatched: {SUBMIT_DISPATCHED}")
    logger.info("================================================================================")

    experiment_report: dict[str, Any] = {
        "PROBE_ITERATIONS": PROBE_ITERATIONS,
        "FRESH_PESU_WEBVIEW_HOST_PER_ITERATION": FRESH_PESU_WEBVIEW_HOST_PER_ITERATION,
        "FULL_SLACK_PROCESS_COLD_START_PER_ITERATION": FULL_SLACK_PROCESS_COLD_START_PER_ITERATION,
        "SAME_SLACK_PROFILE_RUNTIME": SAME_SLACK_PROFILE_RUNTIME,
        "SLACK_RESTART_BETWEEN_ITERATIONS": SLACK_RESTART_BETWEEN_ITERATIONS,
        "SLACK_WRITE_ACTIONS": SLACK_WRITE_ACTIONS,
        "QUERY_MUTATION": QUERY_MUTATION,
        "TEXTY_SET_CONTENTS_CALLS": TEXTY_SET_CONTENTS_CALLS,
        "SUBMIT_DISPATCHED": SUBMIT_DISPATCHED,
        "SEARCH_RESULTS_RETRIEVED": SEARCH_RESULTS_RETRIEVED,
        "MAX_SEARCH_UI_ACTIVATION_DISPATCHES_PER_ITERATION": MAX_SEARCH_UI_ACTIVATION_DISPATCHES_PER_ITERATION,
        "PROBE_ACTIVATION_LOGIC_DIVERGES_FROM_PRODUCTION": PROBE_ACTIVATION_LOGIC_DIVERGES_FROM_PRODUCTION,
        "EXPERIMENT_ENVIRONMENT_INVALID": False,
        "TEARDOWN_AUDIT": {
            "ITERATION_1_PREVIOUS_HOST_TEARDOWN_CONFIRMED": "N/A",
            "ITERATION_2_PREVIOUS_HOST_TEARDOWN_CONFIRMED": None,
            "ITERATION_3_PREVIOUS_HOST_TEARDOWN_CONFIRMED": None,
            "ITERATION_4_PREVIOUS_HOST_TEARDOWN_CONFIRMED": None,
            "ITERATION_5_PREVIOUS_HOST_TEARDOWN_CONFIRMED": None,
        },
        "ITERATIONS": [],
        "AGGREGATES": {},
        "VERDICTS": {},
    }

    last_teardown_meta: Optional[dict[str, Any]] = None

    for it_id in range(1, PROBE_ITERATIONS + 1):
        # Hard Gate: Verify previous host teardown completeness before iteration 2..5
        if it_id > 1:
            if not last_teardown_meta:
                logger.critical(f"❌ Teardown metadata missing before iteration {it_id}!")
                experiment_report["EXPERIMENT_ENVIRONMENT_INVALID"] = True
                break

            teardown_check = verify_host_teardown(
                prev_host=last_teardown_meta["host_ref"],
                prev_thread=last_teardown_meta["prev_thread"],
                prev_hwnd=last_teardown_meta["prev_hwnd"],
                prev_destroy_returned=last_teardown_meta["prev_destroy_returned"],
            )

            confirmed = teardown_check["PREVIOUS_HOST_TEARDOWN_CONFIRMED"]
            experiment_report["TEARDOWN_AUDIT"][f"ITERATION_{it_id}_PREVIOUS_HOST_TEARDOWN_CONFIRMED"] = confirmed
            logger.info(f"🔒 [TEARDOWN_HARD_GATE] Iteration {it_id} pre-check: {teardown_check}")

            if not confirmed:
                logger.critical(
                    f"❌ [TEARDOWN_HARD_GATE_FAILURE] Previous host was NOT completely torn down before iteration {it_id}! "
                    f"Aborting experiment to preserve environment purity."
                )
                experiment_report["EXPERIMENT_ENVIRONMENT_INVALID"] = True
                break

        # Run iteration
        it_telemetry = run_iteration(it_id)
        last_teardown_meta = it_telemetry.pop("_teardown_meta", None)
        experiment_report["ITERATIONS"].append(it_telemetry)

        # Check for safety / environment violation
        if it_telemetry.get("ERROR") == "Slack authentication required in profile.":
            logger.critical("❌ Environment intervention required: auth screen detected.")
            experiment_report["EXPERIMENT_ENVIRONMENT_INVALID"] = True
            break

    # If all ran or stopped
    completed_iterations = experiment_report["ITERATIONS"]
    pass_count = sum(1 for it in completed_iterations if it["RESULT"] == "PASS_VERIFIED_INPUT")
    fail_count = len(completed_iterations) - pass_count
    total_completed = len(completed_iterations)
    success_rate = round(pass_count / total_completed, 4) if total_completed > 0 else 0.0

    successful_latencies = [
        it["ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS"]
        for it in completed_iterations
        if it["RESULT"] == "PASS_VERIFIED_INPUT" and it["ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS"] is not None
    ]

    aggregates: dict[str, Any] = {
        "COMPLETED_ITERATION_COUNT": total_completed,
        "READINESS_PASS_COUNT": pass_count,
        "READINESS_FAILURE_COUNT": fail_count,
        "SUCCESS_RATE": success_rate,
        "MIN_ACTIVATION_TO_VERIFIED_MS": min(successful_latencies) if successful_latencies else None,
        "MEDIAN_ACTIVATION_TO_VERIFIED_MS": round(statistics.median(successful_latencies), 1) if successful_latencies else None,
        "MAX_ACTIVATION_TO_VERIFIED_MS": max(successful_latencies) if successful_latencies else None,
        "FAILURES": [
            {
                "ITERATION_ID": it["ITERATION_ID"],
                "FAILURE_STAGE": it["FAILURE_STAGE"],
                "TIME_AT_FAILURE_MS": it["TIME_AT_FAILURE_MS"],
                "PRIMARY_LOSS_POINT": it["PRIMARY_LOSS_POINT"],
                "ERROR": it["ERROR"],
            }
            for it in completed_iterations
            if it["RESULT"] != "PASS_VERIFIED_INPUT"
        ],
    }
    experiment_report["AGGREGATES"] = aggregates

    # Evaluate Predeclared Verdict Rules
    verdicts: dict[str, Any] = {}
    if total_completed == PROBE_ITERATIONS and not experiment_report["EXPERIMENT_ENVIRONMENT_INVALID"]:
        if pass_count == 5:
            verdicts["SEARCH_UI_READINESS_FAILURE_REPRODUCED"] = False
            verdicts["SEARCH_UI_READINESS_FLAKINESS"] = "NOT_SUPPORTED_IN_THIS_SAMPLE"
            verdicts["FLAKINESS_DISPROVEN"] = False
            verdicts["SEARCH_UI_ACTIVATION_DEFECT"] = "NOT_SUPPORTED"
            verdicts["SLACK_WEBVIEW_SESSION_LIFECYCLE_DEFECT"] = "UNRESOLVED"
            verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "NOT_SUPPORTED"
            verdicts["TIMEOUT_TOO_SHORT_HYPOTHESIS"] = "NOT_SUPPORTED"
        elif 1 <= fail_count <= 4:
            verdicts["SEARCH_UI_READINESS_FAILURE_REPRODUCED"] = True
            verdicts["SEARCH_UI_READINESS_FLAKINESS"] = "CONFIRMED"
            verdicts["SEARCH_UI_ACTIVATION_DEFECT"] = "UNRESOLVED"
            verdicts["SLACK_WEBVIEW_SESSION_LIFECYCLE_DEFECT"] = "UNRESOLVED"
            # Adjudicate timing race strictly by comparing timings between pass and fail
            pass_lead_times = [
                it["APP_SHELL_READY_TO_ACTIVATION_MS"]
                for it in completed_iterations
                if it["RESULT"] == "PASS_VERIFIED_INPUT" and it["APP_SHELL_READY_TO_ACTIVATION_MS"] is not None
            ]
            fail_lead_times = [
                it["APP_SHELL_READY_TO_ACTIVATION_MS"]
                for it in completed_iterations
                if it["RESULT"] != "PASS_VERIFIED_INPUT" and it["APP_SHELL_READY_TO_ACTIVATION_MS"] is not None
            ]
            if pass_lead_times and fail_lead_times:
                diff = abs(statistics.mean(pass_lead_times) - statistics.mean(fail_lead_times))
                if diff > 500.0:
                    verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "LIKELY"
                else:
                    verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "NOT_SUPPORTED"
            else:
                verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "UNRESOLVED"
            verdicts["TIMEOUT_TOO_SHORT_HYPOTHESIS"] = "NOT_SUPPORTED"
        elif fail_count == 5:
            verdicts["SEARCH_UI_READINESS_FAILURE_REPRODUCED"] = True
            verdicts["DETERMINISTIC_READINESS_FAILURE_UNDER_PROBE_CONDITIONS"] = "STRONGLY_SUPPORTED"
            verdicts["SEARCH_UI_ACTIVATION_DEFECT"] = "UNRESOLVED"
            verdicts["SLACK_WEBVIEW_SESSION_LIFECYCLE_DEFECT"] = "UNRESOLVED"
            verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "UNRESOLVED"
            verdicts["SEARCH_UI_READINESS_FLAKINESS"] = "NOT_SUPPORTED"
            verdicts["TIMEOUT_TOO_SHORT_HYPOTHESIS"] = "NOT_SUPPORTED"
    else:
        verdicts["SEARCH_UI_READINESS_FAILURE_REPRODUCED"] = "UNRESOLVED"
        verdicts["SEARCH_UI_READINESS_FLAKINESS"] = "UNRESOLVED"
        verdicts["SEARCH_UI_ACTIVATION_DEFECT"] = "UNRESOLVED"
        verdicts["SLACK_WEBVIEW_SESSION_LIFECYCLE_DEFECT"] = "UNRESOLVED"
        verdicts["EARLY_POST_NAVIGATION_ACTIVATION_RACE"] = "UNRESOLVED"
        verdicts["TIMEOUT_TOO_SHORT_HYPOTHESIS"] = "UNRESOLVED"

    experiment_report["VERDICTS"] = verdicts
    return experiment_report


def print_summary_table(report: dict[str, Any]) -> None:
    """Prints the required ASCII summary table and key invariants."""
    print("\n" + "=" * 115)
    print("  TASK 11R10B — REPEATED COLD-START SLACK SEARCH UI READINESS PROBE REPORT")
    print("=" * 115)
    headers = [
        "ITERATION",
        "HOST NAV MS",
        "APP SHELL MS",
        "ACT LEAD MS",
        "EVENT DISPATCH",
        "MODAL MS",
        "INPUT MS",
        "VERIFIED MS",
        "RESULT",
    ]
    row_fmt = "{:<10} | {:<12} | {:<13} | {:<12} | {:<15} | {:<9} | {:<9} | {:<12} | {:<22}"
    print(row_fmt.format(*headers))
    print("-" * 115)

    for it in report.get("ITERATIONS", []):
        it_id = str(it.get("ITERATION_ID"))
        nav_ms = f"{it.get('HOST_START_TO_NAVIGATION_COMPLETE_MS')} ms" if it.get("HOST_START_TO_NAVIGATION_COMPLETE_MS") is not None else "null"
        shell_ms = f"{it.get('NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS')} ms" if it.get("NAVIGATION_COMPLETE_TO_APP_SHELL_READY_MS") is not None else "null"
        act_lead = f"{it.get('APP_SHELL_READY_TO_ACTIVATION_MS')} ms" if it.get("APP_SHELL_READY_TO_ACTIVATION_MS") is not None else "null"
        dispatched = "DISPATCHED" if it.get("SEARCH_ACTIVATION_EVENT_DISPATCHED") else "FAILED"
        modal_ms = f"{it.get('ACTIVATION_TO_FIRST_MODAL_MS')} ms" if it.get("ACTIVATION_TO_FIRST_MODAL_MS") is not None else "UNOBSERVED"
        input_ms = f"{it.get('ACTIVATION_TO_FIRST_INPUT_MS')} ms" if it.get("ACTIVATION_TO_FIRST_INPUT_MS") is not None else "UNOBSERVED"
        verif_ms = f"{it.get('ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS')} ms" if it.get("ACTIVATION_TO_VERIFIED_SEARCH_INPUT_MS") is not None else "UNOBSERVED"
        res = it.get("RESULT", "UNKNOWN")
        print(row_fmt.format(it_id, nav_ms, shell_ms, act_lead, dispatched, modal_ms, input_ms, verif_ms, res))

    print("-" * 115)
    aggr = report.get("AGGREGATES", {})
    print(f"PROBE_ITERATIONS = {report.get('PROBE_ITERATIONS')}")
    print(f"READINESS_PASS_COUNT = {aggr.get('READINESS_PASS_COUNT')}")
    print(f"READINESS_FAILURE_COUNT = {aggr.get('READINESS_FAILURE_COUNT')}")
    print(f"SUCCESS_RATE = {aggr.get('SUCCESS_RATE')}")
    print(f"MIN_ACTIVATION_TO_VERIFIED_MS = {aggr.get('MIN_ACTIVATION_TO_VERIFIED_MS')}")
    print(f"MEDIAN_ACTIVATION_TO_VERIFIED_MS = {aggr.get('MEDIAN_ACTIVATION_TO_VERIFIED_MS')}")
    print(f"MAX_ACTIVATION_TO_VERIFIED_MS = {aggr.get('MAX_ACTIVATION_TO_VERIFIED_MS')}")
    print()
    verdicts = report.get("VERDICTS", {})
    for k, v in verdicts.items():
        print(f"{k} = {v}")
    print()
    print("TEARDOWN AUDIT:")
    for k, v in report.get("TEARDOWN_AUDIT", {}).items():
        print(f"  {k} = {v}")
    print(f"EXPERIMENT_ENVIRONMENT_INVALID = {report.get('EXPERIMENT_ENVIRONMENT_INVALID')}")
    print("=" * 115 + "\n")


if __name__ == "__main__":
    report = run_all_iterations()
    print_summary_table(report)
    print("\n--- RAW REPORT JSON ---")
    print(json.dumps(report, indent=2))
