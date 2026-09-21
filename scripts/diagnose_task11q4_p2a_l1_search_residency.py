#!/usr/bin/env python
"""Task 11 / P2A-L1-RD1: Bounded Search Result Residency Diagnostic Runner (Run 2).

Pure read-only observer diagnostic to determine search result residency for the frozen target:
- Query: "26년 하반기 간담회"
- Expected Channel: "C09HL5XCTNJ"
- Expected Message TS: "1788152491.960529"
- Expected Root TS: "1787709893.004509"

Run 2 Remediation (Task 11 / P2A-L1-RD1-R2-PREP):
- Corrected backend telemetry key harvesting:
    - safe_search_action_selected -> safe_search_action_explicitly_selected
    - verified_query_state_before_submit -> query_state_verified_before_submit
    - generic_default_onenter_used -> generic_default_onenter_used
    - onenter_invoked -> search_submissions
    - forensic_transition.SEARCH_SURFACE_QUERY_MATCHES_REQUESTED -> search_surface_query_matches_requested
    - forensic_transition.SEARCH_SURFACE_QUERY_AFTER -> search_surface_query_after
    - slackbot_action_invoked_by_pesu -> slackbot_action_invoked_by_pesu
    - write_capable_action_invoked_by_pesu -> write_capable_action_invoked_by_pesu
    - unsafe_non_search_action_invoked_by_pesu -> unsafe_non_search_action_invoked_by_pesu
- Strict tri-state authority semantics: True / False / UNKNOWN.
- Pre-snapshot Query-Correlation & Safety Gate:
    JS_RD1_EXTRACT_AND_COUNT_RESIDENCY is evaluated ONLY if safety authority is VERIFIED_SAFE
    and post-search surface query matches requested query (SEARCH_SURFACE_QUERY_MATCHES_REQUESTED == True).
- Disaggregated verdict reasons:
    - ZERO_MOUNTED_ROWS
    - REQUIRED_SEARCH_TELEMETRY_MISSING
    - REQUIRED_SEARCH_TELEMETRY_FALSE
    - SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED
    - SEARCH_QUERY_MISMATCH
    - EXPLICIT_SEARCH_SAFETY_VIOLATION
    - LACKS_SAME_SNAPSHOT_COUNT_AUTHORITY
    - MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND
- Invariants:
    - MAX_DIAGNOSTIC_ROWS = 20
    - SAME_SNAPSHOT_RESIDENCY_COUNTS = true
    - Pure read-only inspection of mounted DOM
    - reply_bar_clicks = 0, result_card_clicks = 0, composer_interaction = 0,
      permalink_navigation = 0, keyboard_shortcuts = 0, CDP = 0, provider_calls = 0,
      scroll_iterations = 0 (Zero Scroll), retries = 0
    - RD1 NEVER declares P2A_L1 = PASS and NEVER sets THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED = true
    - Guaranteed native trace persistence on all terminal paths in finally block
    - Separate trace paths for DRY_RUN and LIVE modes
    - Dedicated Run 2 output files (Run 1 trace preserved immutably)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuWebViewHost,
)
from pesu_agent.search.webview_slack_search import (
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)
from pesu_agent.search.webview_thread_reader import normalize_slack_ts

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11q4_p2a_l1_rd1_search_residency")

# Frozen Target Identity
TARGET_QUERY = "26년 하반기 간담회"
EXPECTED_CHANNEL_ID = "C09HL5XCTNJ"
EXPECTED_MESSAGE_TS = "1788152491.960529"
EXPECTED_ROOT_TS = "1787709893.004509"

MAX_DIAGNOSTIC_ROWS = 20

PROTOCOL_NAME = "TASK11Q4_P2A_L1_RD1_RUN2_SEARCH_RESIDENCY"
DIAGNOSTIC_RUN = "RD1_RUN_2"

PROTOCOL_FREEZE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_rd1_run2_protocol_freeze.json"
RUN1_LIVE_TRACE_PATH = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_rd1_search_residency.json"
RUN1_LIVE_TRACE_SHA256 = "5A44F58616A5DAC21EBEB596A10E8F55BA7A2263EB177B945485C2BDD2556846"

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rd1_run2_search_residency.json"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rd1_run2_search_residency_dry_run.json"

JS_RD1_EXTRACT_AND_COUNT_RESIDENCY = r"""
((maxSampleCount) => {
    const searchViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el && el.isConnected);
    if (searchViews.length !== 1) {
        return {
            ok: false,
            error: 'SEARCH_VIEW_NOT_FOUND',
            search_view_count: searchViews.length,
            mounted_row_count: 0,
            inspected_row_count: 0,
            inspection_truncated: false,
            items: []
        };
    }
    const searchView = searchViews[0];
    const resultRoots = Array.from(searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
        if (!el || !el.isConnected) return false;
        const r = el.getBoundingClientRect();
        return (r.width > 0 && r.height > 0);
    });

    const mounted_row_count = resultRoots.length;
    const inspectedRoots = resultRoots.slice(0, maxSampleCount);
    const inspected_row_count = inspectedRoots.length;
    const inspection_truncated = (mounted_row_count > inspected_row_count);

    const items = [];
    for (let idx = 0; idx < inspectedRoots.length; idx++) {
        const root = inspectedRoots[idx];
        const rep = {
            raw_index: idx + 1,
            qualifying_permalinks: [],
            canonical_permalink: null,
            thread_reply_detected: false,
        };
        const anchors = Array.from(root.querySelectorAll('a[href]'));
        for (const a of anchors) {
            const h = (a.getAttribute('href') || '').trim();
            if (/\/archives\/[A-Z0-9]+\/p\d{16}/.test(h)) {
                rep.qualifying_permalinks.push(h);
                if (h.includes('thread_ts=')) {
                    rep.thread_reply_detected = true;
                }
            }
        }
        if (rep.qualifying_permalinks.length > 0) {
            rep.canonical_permalink = rep.qualifying_permalinks[0];
        }
        items.push(rep);
    }

    return {
        ok: true,
        same_snapshot_authority: true,
        mounted_row_count: mounted_row_count,
        inspection_bound: maxSampleCount,
        inspected_row_count: inspected_row_count,
        inspection_truncated: inspection_truncated,
        items: items
    };
})
"""

DEPENDENCY_PATHS = {
    "rd1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_search_residency.py",
    "webview_slack_search": SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py",
    "pesu_webview_host": SRC_DIR / "pesu_agent" / "runtime" / "pesu_webview_host.py",
    "slack_permalink_parser": SRC_DIR / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
}


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception as e:
        logger.warning(f"Failed to get git commit: {e}")
        return "UNKNOWN"


def check_uncommitted_diff() -> bool:
    try:
        res = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def get_file_sha256(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    return {name: get_file_sha256(p) for name, p in DEPENDENCY_PATHS.items()}


def classify_row_identity(
    raw_item: dict[str, Any],
    expected_channel: str = EXPECTED_CHANNEL_ID,
    expected_message_ts: str = EXPECTED_MESSAGE_TS,
    expected_root_ts: str = EXPECTED_ROOT_TS,
) -> dict[str, Any]:
    """Classifies a single search result row without preserving message body text."""
    rank = int(raw_item.get("raw_index") or raw_item.get("result_index") or 1)
    permalinks = raw_item.get("qualifying_permalinks") or []
    canonical_permalink = raw_item.get("canonical_permalink") or (permalinks[0] if permalinks else None)

    parsed_id: Optional[SlackMessagePermalinkIdentity] = None
    if canonical_permalink:
        parsed_id = parse_slack_message_permalink(canonical_permalink)

    if not parsed_id and permalinks:
        for pl in permalinks:
            p = parse_slack_message_permalink(pl)
            if p:
                parsed_id = p
                break

    parsed_channel_id = parsed_id.channel_id if parsed_id else None
    parsed_message_ts = parsed_id.message_ts if parsed_id else None
    parsed_thread_ts = parsed_id.thread_ts if parsed_id else None
    is_thread_reply = parsed_id.is_thread_reply if parsed_id else bool(raw_item.get("thread_reply_detected", False))

    reply_bar_present = bool(raw_item.get("thread_reply_detected", False) or is_thread_reply)

    if not parsed_id:
        classification = "PARSE_FAILED"
        c_match = False
        m_match = False
        t_match = False
    else:
        c_match = (parsed_channel_id == expected_channel)
        m_match = (normalize_slack_ts(parsed_message_ts) == normalize_slack_ts(expected_message_ts))
        t_match = (
            parsed_thread_ts is not None
            and normalize_slack_ts(parsed_thread_ts) == normalize_slack_ts(expected_root_ts)
        )

        if c_match and m_match and t_match:
            classification = "EXACT_FROZEN_IDENTITY_MATCH"
        elif c_match and m_match and not t_match:
            classification = "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD"
        elif c_match and not m_match and t_match:
            classification = "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE"
        else:
            classification = "UNRELATED"

    return {
        "rank": rank,
        "raw_permalink": canonical_permalink,
        "parsed_channel_id": parsed_channel_id,
        "parsed_message_ts": parsed_message_ts,
        "parsed_thread_ts": parsed_thread_ts,
        "is_thread_reply": is_thread_reply,
        "parse_status": "PARSED" if parsed_id else "PARSE_FAILED",
        "reply_bar_present": reply_bar_present,
        "target_classification": classification,
        "exact_frozen_match": bool(c_match and m_match and t_match),
        "same_channel_message_match": bool(c_match and m_match),
        "same_channel_thread_match": bool(c_match and t_match),
    }


def harvest_rd1_search_telemetry(
    backend: Any,
    search_exception: Optional[Exception],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Authoritatively harvests backend.last_search_telemetry into report.

    Strict tri-state mapping (True / False / UNKNOWN). Never fabricates values from silence.
    """
    telemetry = getattr(backend, "last_search_telemetry", None)
    if not isinstance(telemetry, dict) or not telemetry:
        if search_exception is not None and hasattr(search_exception, "details"):
            details = getattr(search_exception, "details", None)
            if isinstance(details, dict) and details:
                telemetry = details

    search_tel = report.setdefault("search_telemetry", {})
    safety_tel = report.setdefault("safety_telemetry", {})

    if not isinstance(telemetry, dict) or not telemetry:
        search_tel["search_activation_dispatches"] = "UNKNOWN"
        search_tel["modal_ever_observed"] = "UNKNOWN"
        search_tel["search_submissions"] = "UNKNOWN"
        search_tel["query_state_verified_before_submit"] = "UNKNOWN"
        search_tel["last_confirmed_stage"] = "UNKNOWN"
        search_tel["failure_case_classification"] = "TELEMETRY_MISSING"
        search_tel["search_surface_query_matches_requested"] = "UNKNOWN"
        search_tel["search_surface_query_after"] = "NOT_CAPTURED"

        safety_tel["safe_search_action_explicitly_selected"] = "UNKNOWN"
        safety_tel["generic_default_onenter_used"] = "UNKNOWN"
        safety_tel["slackbot_action_invoked_by_pesu"] = "UNKNOWN"
        safety_tel["write_capable_action_invoked_by_pesu"] = "UNKNOWN"
        safety_tel["unsafe_non_search_action_invoked_by_pesu"] = "UNKNOWN"
        safety_tel["composer_focus"] = "UNKNOWN"
        safety_tel["composer_input"] = "UNKNOWN"
        safety_tel["composer_send"] = "UNKNOWN"
        return telemetry or {}

    # 1. Activation dispatches
    if "activation_dispatch_count" in telemetry:
        search_tel["search_activation_dispatches"] = int(telemetry["activation_dispatch_count"])
    else:
        search_tel["search_activation_dispatches"] = "UNKNOWN"

    # 2. Modal observation
    if "modal_ever_observed" in telemetry:
        search_tel["modal_ever_observed"] = bool(telemetry["modal_ever_observed"])
    else:
        search_tel["modal_ever_observed"] = "UNKNOWN"

    # 3. Submissions
    if "onenter_invoked" in telemetry:
        search_tel["search_submissions"] = 1 if bool(telemetry["onenter_invoked"]) else 0
    else:
        search_tel["search_submissions"] = "UNKNOWN"

    # 4. Pre-submit query verification (Backend key: verified_query_state_before_submit)
    if "verified_query_state_before_submit" in telemetry:
        search_tel["query_state_verified_before_submit"] = bool(telemetry["verified_query_state_before_submit"])
    else:
        search_tel["query_state_verified_before_submit"] = "UNKNOWN"

    # 5. Last confirmed stage & failure classification
    search_tel["last_confirmed_stage"] = telemetry.get("last_confirmed_stage", "UNKNOWN")
    search_tel["failure_case_classification"] = telemetry.get("failure_case_classification", "NONE")

    # 6. Post-search forensic transition
    forensic_trans = telemetry.get("forensic_transition")
    if isinstance(forensic_trans, dict):
        if "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED" in forensic_trans:
            search_tel["search_surface_query_matches_requested"] = bool(forensic_trans["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"])
        else:
            search_tel["search_surface_query_matches_requested"] = "UNKNOWN"

        if "SEARCH_SURFACE_QUERY_AFTER" in forensic_trans and forensic_trans["SEARCH_SURFACE_QUERY_AFTER"] is not None:
            search_tel["search_surface_query_after"] = str(forensic_trans["SEARCH_SURFACE_QUERY_AFTER"])
        else:
            search_tel["search_surface_query_after"] = "NOT_CAPTURED"
    else:
        search_tel["search_surface_query_matches_requested"] = "UNKNOWN"
        search_tel["search_surface_query_after"] = "NOT_CAPTURED"

    # 7. Safety Telemetry
    # Backend key: safe_search_action_selected
    if "safe_search_action_selected" in telemetry:
        safety_tel["safe_search_action_explicitly_selected"] = bool(telemetry["safe_search_action_selected"])
    else:
        safety_tel["safe_search_action_explicitly_selected"] = "UNKNOWN"

    if "generic_default_onenter_used" in telemetry:
        safety_tel["generic_default_onenter_used"] = bool(telemetry["generic_default_onenter_used"])
    else:
        safety_tel["generic_default_onenter_used"] = "UNKNOWN"

    if "slackbot_action_invoked_by_pesu" in telemetry or "slackbot_action_invoked" in telemetry:
        safety_tel["slackbot_action_invoked_by_pesu"] = bool(
            telemetry.get("slackbot_action_invoked_by_pesu") or telemetry.get("slackbot_action_invoked")
        )
    else:
        safety_tel["slackbot_action_invoked_by_pesu"] = "UNKNOWN"

    if "write_capable_action_invoked_by_pesu" in telemetry or "write_capable_action_invoked" in telemetry:
        safety_tel["write_capable_action_invoked_by_pesu"] = bool(
            telemetry.get("write_capable_action_invoked_by_pesu") or telemetry.get("write_capable_action_invoked")
        )
    else:
        safety_tel["write_capable_action_invoked_by_pesu"] = "UNKNOWN"

    if "unsafe_non_search_action_invoked_by_pesu" in telemetry or "unsafe_non_search_action_invoked" in telemetry:
        safety_tel["unsafe_non_search_action_invoked_by_pesu"] = bool(
            telemetry.get("unsafe_non_search_action_invoked_by_pesu") or telemetry.get("unsafe_non_search_action_invoked")
        )
    else:
        safety_tel["unsafe_non_search_action_invoked_by_pesu"] = "UNKNOWN"

    safety_tel["composer_focus"] = telemetry.get("composer_focus", "UNKNOWN")
    safety_tel["composer_input"] = telemetry.get("composer_input", "UNKNOWN")
    safety_tel["composer_send"] = telemetry.get("composer_send", "UNKNOWN")

    return telemetry


def evaluate_safety_and_query_authority(
    report: dict[str, Any],
    search_exception: Optional[Exception],
) -> tuple[str, str, Optional[str], Optional[str]]:
    """Evaluates safety and query authorities strictly without silent defaults.

    Returns:
        (safety_authority, query_authority, failure_verdict, failure_reason)
    """
    st = report.get("search_telemetry", {})
    safety = report.get("safety_telemetry", {})

    safe_action = safety.get("safe_search_action_explicitly_selected")
    generic_onenter = safety.get("generic_default_onenter_used")
    slackbot = safety.get("slackbot_action_invoked_by_pesu")
    write_cap = safety.get("write_capable_action_invoked_by_pesu")
    unsafe_non_search = safety.get("unsafe_non_search_action_invoked_by_pesu")
    composer_focus = safety.get("composer_focus")
    composer_input = safety.get("composer_input")
    composer_send = safety.get("composer_send")
    composer_interaction = safety.get("composer_interaction", 0)

    sub_count = st.get("search_submissions")
    pre_sub_ok = st.get("query_state_verified_before_submit")
    surf_matches = st.get("search_surface_query_matches_requested")

    # 1. EXPLICIT SAFETY VIOLATION CHECK -> FAIL_CLOSED
    has_explicit_safety_violation = (
        generic_onenter is True
        or slackbot is True
        or write_cap is True
        or unsafe_non_search is True
        or (sub_count == 1 and safe_action is False)
        or composer_focus is True
        or composer_input is True
        or composer_send is True
        or composer_interaction > 0
    )
    if has_explicit_safety_violation:
        return "VIOLATION_PROVEN", "NOT_VERIFIED", "FAIL_CLOSED", "EXPLICIT_SEARCH_SAFETY_VIOLATION"

    # 2. EXPLICIT QUERY MISMATCH CHECK -> FAIL_CLOSED
    if surf_matches is False:
        return "NOT_VERIFIED", "QUERY_MISMATCH", "FAIL_CLOSED", "SEARCH_QUERY_MISMATCH"

    # 3. EXPLICIT SEARCH EXCEPTION -> FAIL_CLOSED / RUNTIME_EXCEPTION
    if search_exception is not None:
        return "NOT_VERIFIED", "NOT_VERIFIED", "FAIL_CLOSED", "SEARCH_PIPELINE_EXCEPTION"

    # 4. REQUIRED TELEMETRY MISSING CHECK -> SEARCH_RESULT_CAPTURE_INSUFFICIENT
    required_safety_missing = (
        safe_action == "UNKNOWN"
        or generic_onenter == "UNKNOWN"
        or slackbot == "UNKNOWN"
        or write_cap == "UNKNOWN"
        or unsafe_non_search == "UNKNOWN"
    )
    if required_safety_missing:
        return "NOT_VERIFIED", "NOT_VERIFIED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "REQUIRED_SEARCH_TELEMETRY_MISSING"

    if pre_sub_ok == "UNKNOWN" or sub_count == "UNKNOWN":
        return "NOT_VERIFIED", "NOT_VERIFIED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "REQUIRED_SEARCH_TELEMETRY_MISSING"

    if surf_matches == "UNKNOWN":
        return "NOT_VERIFIED", "NOT_VERIFIED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED"

    # 5. REQUIRED TELEMETRY FALSE CHECK (Non-safety prerequisites that failed)
    if pre_sub_ok is not True or sub_count != 1:
        return "NOT_VERIFIED", "NOT_VERIFIED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "REQUIRED_SEARCH_TELEMETRY_FALSE"

    # 6. ALL AUTHORITATIVE CRITERIA MET -> VERIFIED_SAFE & VERIFIED
    is_verified_safe = (
        safe_action is True
        and generic_onenter is False
        and slackbot is False
        and write_cap is False
        and unsafe_non_search is False
        and composer_interaction == 0
        and sub_count == 1
        and pre_sub_ok is True
        and surf_matches is True
    )
    if not is_verified_safe:
        return "NOT_VERIFIED", "NOT_VERIFIED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "REQUIRED_SEARCH_TELEMETRY_FALSE"

    return "VERIFIED_SAFE", "VERIFIED", None, None


def determine_diagnostic_verdict(
    inspected_rows: list[dict[str, Any]],
    mounted_row_count: int,
    inspected_row_count: int,
    inspection_truncated: bool,
    telemetry_ok: bool,
    same_snapshot_authority: bool = True,
    gate_failure_reason: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Determines overall diagnostic verdict using strict priority hierarchy and disaggregated reasons."""
    if not telemetry_ok:
        reason = gate_failure_reason or "REQUIRED_SEARCH_TELEMETRY_MISSING"
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", reason

    if mounted_row_count == 0:
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "ZERO_MOUNTED_ROWS"

    # 1. Exact match
    if any(r["target_classification"] == "EXACT_FROZEN_IDENTITY_MATCH" for r in inspected_rows):
        return "TARGET_EXACT_MATCH_FOUND", None

    # 2. Representation drift of target message
    if any(r["target_classification"] == "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD" for r in inspected_rows):
        return "TARGET_REPRESENTATION_DRIFT_FOUND", None

    # 3. Same thread, different search hit
    if any(r["target_classification"] == "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE" for r in inspected_rows):
        return "SAME_THREAD_DIFFERENT_SEARCH_HIT_FOUND", None

    # Conclusive absence REQUIRES same_snapshot_authority == True
    if not same_snapshot_authority:
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "LACKS_SAME_SNAPSHOT_COUNT_AUTHORITY"

    # 4 & 5. Target absent from inspected rows
    if inspection_truncated:
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND"

    return "TARGET_NOT_IN_BOUNDED_INSPECTED_SET", None


def run_rd1_diagnostic(dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    output_file = DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE

    report: dict[str, Any] = {
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "clean_diff": not check_uncommitted_diff(),
        "runner_sha256": get_file_sha256(DEPENDENCY_PATHS["rd1_runner"]),
        "protocol_sha256": get_file_sha256(PROTOCOL_FREEZE_PATH),
        "requested_query": TARGET_QUERY,
        "SAME_SNAPSHOT_RESIDENCY_COUNTS": True,
        "inspection_bound": MAX_DIAGNOSTIC_ROWS,
        "bound_definition": "RD1 inspects at most 20 currently mounted visible result rows and records whether the mounted set was truncated by that bound.",
        "mounted_row_count": 0,
        "inspected_row_count": 0,
        "inspection_truncated": False,
        "inspected_rows": [],
        "diagnostic_verdict": "FAIL_CLOSED",
        "verdict_reason": None,
        "P2A_L1": "NOT_APPLICABLE_DIAGNOSTIC_ONLY",
        "thread_root_data_extraction_live_validated": False,
        "RD1_SEARCH_SAFETY_AUTHORITY": "NOT_VERIFIED",
        "RD1_RESULT_SET_QUERY_AUTHORITY": "NOT_VERIFIED",
        "safety_telemetry": {
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
            "composer_interaction": 0,
            "permalink_navigation": 0,
            "keyboard_shortcuts": 0,
            "CDP": 0,
            "provider_calls": 0,
            "scroll_iterations": 0,
            "retries": 0,
            "safe_search_action_explicitly_selected": "UNKNOWN",
            "generic_default_onenter_used": "UNKNOWN",
            "slackbot_action_invoked_by_pesu": "UNKNOWN",
            "write_capable_action_invoked_by_pesu": "UNKNOWN",
            "unsafe_non_search_action_invoked_by_pesu": "UNKNOWN",
            "composer_focus": "UNKNOWN",
            "composer_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        },
        "search_telemetry": {
            "search_activation_dispatches": "UNKNOWN",
            "modal_ever_observed": "UNKNOWN",
            "search_submissions": "UNKNOWN",
            "query_state_verified_before_submit": "UNKNOWN",
            "last_confirmed_stage": "UNKNOWN",
            "failure_case_classification": "UNKNOWN",
            "search_surface_query_matches_requested": "UNKNOWN",
            "search_surface_query_after": "NOT_CAPTURED",
        },
        "duration_seconds": 0.0,
    }

    host: Optional[PesuWebViewHost] = None
    try:
        if dry_run:
            logger.info("🧪 [RD1_DRY_RUN] Performing offline dry-run validation (ZERO UI actions)...")
            report["mounted_row_count"] = 0
            report["inspected_row_count"] = 0
            report["inspection_truncated"] = False
            report["SAME_SNAPSHOT_RESIDENCY_COUNTS"] = True
            report["diagnostic_verdict"] = "DRY_RUN_PASS"
            report["RD1_SEARCH_SAFETY_AUTHORITY"] = "NOT_APPLICABLE_DRY_RUN"
            report["RD1_RESULT_SET_QUERY_AUTHORITY"] = "NOT_APPLICABLE_DRY_RUN"
            return report

        # ---------------------------------------------------------
        # LIVE DIAGNOSTIC EXECUTION PATH
        # ---------------------------------------------------------
        logger.info("🚀 [RD1_LIVE] Initializing dedicated WebView host for residency diagnosis...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1-RD1 Residency Diagnostic (Run 2)",
        )
        backend = WebViewSlackSearchBackend(
            config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH)),
            host=host,
        )

        logger.info("📡 [RUNTIME_START] Starting Dedicated Slack WebView Host...")
        backend.start_runtime(timeout=timeout)

        logger.info(f"🔍 [SEARCH_EXECUTE] Submitting target query: '{TARGET_QUERY}' (max_results={MAX_DIAGNOSTIC_ROWS})")
        search_exception = None
        try:
            backend.search_messages(TARGET_QUERY, max_results=MAX_DIAGNOSTIC_ROWS)
        except Exception as se:
            search_exception = se
            logger.warning(f"⚠️ search_messages raised: {se}")

        # Harvest authoritative backend telemetry directly
        harvest_rd1_search_telemetry(backend, search_exception, report)

        # Pre-snapshot Query-Correlation & Safety Gate
        safety_auth, query_auth, fail_verdict, fail_reason = evaluate_safety_and_query_authority(
            report, search_exception
        )
        report["RD1_SEARCH_SAFETY_AUTHORITY"] = safety_auth
        report["RD1_RESULT_SET_QUERY_AUTHORITY"] = query_auth

        if fail_verdict is not None:
            logger.warning(
                f"🛑 [GATE_FAIL] Pre-snapshot query correlation / safety gate rejected snapshot: "
                f"verdict={fail_verdict}, reason={fail_reason}"
            )
            report["diagnostic_verdict"] = fail_verdict
            report["verdict_reason"] = fail_reason
            return report

        # Gate passed: proceed to atomic same-snapshot residency evaluation
        logger.info("🔬 [RD1_SAME_SNAPSHOT] Evaluating atomic same-snapshot residency JS...")
        js_rd1 = f"({JS_RD1_EXTRACT_AND_COUNT_RESIDENCY})({MAX_DIAGNOSTIC_ROWS})"
        res_rd1 = backend._window.evaluate_js(js_rd1)

        same_snapshot_ok = False
        mounted_count = 0
        inspected_count = 0
        truncated = False
        raw_items = []

        if isinstance(res_rd1, dict) and res_rd1.get("ok"):
            same_snapshot_ok = bool(res_rd1.get("same_snapshot_authority", False))
            mounted_count = int(res_rd1.get("mounted_row_count", 0))
            inspected_count = int(res_rd1.get("inspected_row_count", 0))
            truncated = bool(res_rd1.get("inspection_truncated", False))
            raw_items = res_rd1.get("items", [])
        else:
            logger.error(f"Atomic same-snapshot JS extraction failed: {res_rd1}")

        report["SAME_SNAPSHOT_RESIDENCY_COUNTS"] = same_snapshot_ok
        report["mounted_row_count"] = mounted_count

        inspected_rows = []
        for itm in raw_items[:MAX_DIAGNOSTIC_ROWS]:
            classified = classify_row_identity(itm)
            inspected_rows.append(classified)

        report["inspected_rows"] = inspected_rows
        report["inspected_row_count"] = len(inspected_rows)
        report["inspection_truncated"] = truncated

        verdict, reason = determine_diagnostic_verdict(
            inspected_rows=inspected_rows,
            mounted_row_count=mounted_count,
            inspected_row_count=len(inspected_rows),
            inspection_truncated=truncated,
            telemetry_ok=True,
            same_snapshot_authority=same_snapshot_ok,
        )
        report["diagnostic_verdict"] = verdict
        report["verdict_reason"] = reason

        logger.info(f"🏁 [RD1_VERDICT] Diagnostic verdict: {verdict} (reason: {reason})")

    except Exception as ex:
        logger.error(f"❌ [EXCEPTION] RD1 diagnostic raised: {ex}", exc_info=True)
        report["error"] = str(ex)
        report["diagnostic_verdict"] = "FAIL_CLOSED"
        report["verdict_reason"] = "RUNTIME_EXCEPTION"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)
        if host is not None:
            try:
                logger.info("🛑 [HOST_SHUTDOWN] Destroying WebView host window...")
                host.destroy()
            except Exception as destroy_err:
                logger.warning(f"Error destroying host: {destroy_err}")

        # Native trace persistence guaranteed on ALL paths
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info(f"📄 Saved RD1 diagnostic trace to {output_file}")
        except Exception as write_err:
            logger.error(f"Failed to write RD1 diagnostic trace: {write_err}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P2A-L1-RD1 Bounded Search Result Residency Diagnostic (Run 2)")
    parser.add_argument("--dry-run", action="store_true", help="Perform offline dry-run (zero UI actions)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Operation timeout in seconds")
    args = parser.parse_args()

    result = run_rd1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print("\n=== P2A-L1-RD1 RUN 2 DIAGNOSTIC REPORT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["diagnostic_verdict"] in ("FAIL_CLOSED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT"):
        sys.exit(1)
    sys.exit(0)
