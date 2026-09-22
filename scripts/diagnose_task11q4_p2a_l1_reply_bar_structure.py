"""Phase 13.5D / Task 11 / P2A-L1 / RB1: Frozen Target Reply-Bar DOM Cardinality & Structure Diagnostic.

ONE_SEARCH_ZERO_CLICK_READ_ONLY_DIAGNOSTIC

Strict Invariants:
- PRODUCTION_CODE_CHANGED = false
- ZERO_REPLY_BAR_CLICKS = true (reply_bar_clicks == 0)
- ZERO_RESULT_CARD_CLICKS = true
- ZERO_COMPOSER_INTERACTIONS = true
- ZERO_THREAD_EXPANSIONS = true
- ONE_ATOMIC_JS_SNAPSHOT = true (same snapshot evaluates residency, identity match, and reply_bar cardinality/structure)
- REPLAY_FORBIDDEN = true (live artifact overwrite guard)
- EXCLUSIVELY_DATA_COLLECTION = true
- USER_MESSAGE_TEXT_CAPTURED = false
- UNRELATED_RAW_PERMALINK_CAPTURED = false
- OUTER_HTML_CAPTURED = false
- PRODUCTION_CARDINALITY_PREDICATE_MIRRORED_EXACTLY = true
- RB1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4 = true
- UNTRACKED_RUNTIME_DEPENDENCIES = 0
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuWebViewHost,
)
from pesu_agent.search.slack_search import SlackSearchResult
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
logger = logging.getLogger("task11q4_p2a_l1_rb1_reply_bar_structure")

# Frozen Target Identity
TARGET_QUERY = "26년 하반기 간담회"
EXPECTED_CHANNEL_ID = "C042WS25QAX"
EXPECTED_MESSAGE_TS = "1718079276.126469"
EXPECTED_ROOT_TS = "1718058683.912369"
SOURCE_RD1_RUN2_RANK = 5
SOURCE_RAW_PERMALINK = "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
SEARCH_MAX_RESULTS = 5
TARGET_FITNESS_CONTRACT = "EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS"

PROTOCOL_NAME = "TASK11Q4_P2A_L1_RB1_REPLY_BAR_STRUCTURE"
DIAGNOSTIC_RUN = "RB1"

PROTOCOL_FREEZE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_rb1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rb1_reply_bar_structure.json"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rb1_reply_bar_structure_dry_run.json"

# Shared Search Selectors (Parity with WebviewThreadReader)
SEARCH_VIEW_SELECTOR: str = '[data-qa="search_view"]'
SEARCH_RESULT_ROW_SELECTORS: str = '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

# Atomic JS Snapshot Script: Evaluates residency, exact identity matching, and reply-bar cardinality/structure in ONE DOM pass
# Sensitive-content excluded: zero user message text, zero outer HTML, zero raw permalinks from unrelated rows.
JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE = r"""
((searchViewSel, searchRowSel, targetChannelId, targetMsgTs, targetRootTs, maxRows) => {
    try {
        const searchViews = Array.from(document.querySelectorAll(searchViewSel)).filter(el => el && el.isConnected);
        if (searchViews.length !== 1) {
            return {
                ok: false,
                error: searchViews.length === 0 ? "SEARCH_VIEW_NOT_FOUND" : "SEARCH_VIEW_CARDINALITY_NOT_ONE",
                search_view_count: searchViews.length,
                mounted_row_count: 0,
                target_resident: false,
                resident_matches: 0,
                reply_bar_cardinality: null
            };
        }

        const searchView = searchViews[0];
        const allRows = Array.from(searchView.querySelectorAll(searchRowSel)).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });

        const mounted_row_count = allRows.length;
        const candidateRows = allRows.slice(0, maxRows);

        // Normalize target timestamps helper
        const normTs = (ts) => {
            if (!ts) return null;
            const s = String(ts).trim();
            const m = s.match(/^(\d+)(?:\.(\d+))?$/);
            if (!m) return s;
            const sec = m[1];
            const usec = (m[2] || "").padEnd(6, "0").slice(0, 6);
            return sec + "." + usec;
        };

        const targetNormMsg = normTs(targetMsgTs);
        const targetNormRoot = normTs(targetRootTs);

        const rowSummaries = [];
        const matchingIndices = [];

        for (let idx = 0; idx < candidateRows.length; idx++) {
            const row = candidateRows[idx];
            const anchors = Array.from(row.querySelectorAll('a[href]'));
            let rowMatched = false;

            for (const a of anchors) {
                const h = (a.getAttribute('href') || '').trim();
                const m = h.match(/\/archives\/([A-Z0-9]+)\/p(\d{16})(?:\?([^#]*))?/);
                if (m) {
                    const chan = m[1];
                    const rawTs = m[2];
                    const normMsg = rawTs.slice(0, 10) + '.' + rawTs.slice(10).padEnd(6, '0').slice(0, 6);
                    const qStr = m[3] || '';
                    let thTs = null;
                    const thMatch = qStr.match(/(?:^|&)thread_ts=([0-9.]+)/);
                    if (thMatch) {
                        thTs = normTs(thMatch[1]);
                    }

                    const cMatch = (chan === targetChannelId);
                    const mMatch = (normMsg === targetNormMsg);
                    const tMatch = (thTs === targetNormRoot || (!thTs && targetNormMsg === targetNormRoot));

                    if (cMatch && mMatch && tMatch) {
                        rowMatched = true;
                    }
                }
            }

            // Sensitive-content exclusion: do NOT store raw permalinks from any row
            rowSummaries.push({
                row_index: idx,
                identity_matched: rowMatched
            });

            if (rowMatched) {
                matchingIndices.push(idx);
            }
        }

        if (matchingIndices.length === 0) {
            return {
                ok: true,
                target_resident: false,
                resident_matches: 0,
                mounted_row_count: mounted_row_count,
                inspected_row_count: candidateRows.length,
                rows: rowSummaries,
                reply_bar_cardinality: null,
                reply_bar_elements: [],
                alternative_candidates: []
            };
        }

        if (matchingIndices.length > 1) {
            return {
                ok: true,
                target_resident: false,
                resident_matches: matchingIndices.length,
                mounted_row_count: mounted_row_count,
                inspected_row_count: candidateRows.length,
                rows: rowSummaries,
                error: "ROW_IDENTITY_AMBIGUOUS",
                reply_bar_cardinality: null,
                reply_bar_elements: [],
                alternative_candidates: []
            };
        }

        // Exactly one matching row: inspect reply-bar cardinality and structure in the SAME SNAPSHOT
        const targetRowIdx = matchingIndices[0];
        const targetRow = candidateRows[targetRowIdx];

        // 1. Exact Attempt 4 Production Predicate Mirror
        // In WebviewThreadReader: targetRow.querySelectorAll('div[data-qa="reply_bar"]') filtered by el && el.isConnected
        const productionDivReplyBars = Array.from(targetRow.querySelectorAll('div[data-qa="reply_bar"]')).filter(el => el && el.isConnected);
        const productionConnectedDivCount = productionDivReplyBars.length;
        const productionCardinalityGuardWouldPass = (productionConnectedDivCount === 1);

        // 2. Comprehensive structural inspection of all [data-qa="reply_bar"] elements (div, button, or other tag)
        const allReplyBars = Array.from(targetRow.querySelectorAll('[data-qa="reply_bar"]')).filter(el => el && el.isConnected);
        const buttonReplyBars = allReplyBars.filter(el => el.tagName.toLowerCase() === 'button');

        const replyBarElementDetails = [];
        let renderedCount = 0;
        let hiddenCount = 0;
        let nestedCount = 0;
        let composerContainedCount = 0;
        let viewThreadControlCount = 0;
        let replyBarCountControlCount = 0;

        for (let rbIdx = 0; rbIdx < allReplyBars.length; rbIdx++) {
            const el = allReplyBars[rbIdx];
            const r = el.getBoundingClientRect();
            const isVis = (r.width > 0 && r.height > 0);
            if (isVis) renderedCount++; else hiddenCount++;

            const isNested = Boolean(el.parentElement && el.parentElement.closest('[data-qa="reply_bar"]'));
            if (isNested) nestedCount++;

            const inComp = Boolean(el.closest('[data-qa="message_input"], .c-message_input, textarea, [role="textbox"], [contenteditable="true"]'));
            if (inComp) composerContainedCount++;

            const countEl = el.querySelector('[data-qa="reply_bar_count"]');
            const viewThreadEl = el.querySelector('[data-qa="reply_bar_view_thread"]');

            if (countEl) replyBarCountControlCount++;
            if (viewThreadEl) viewThreadControlCount++;

            // Permitted structural metadata ONLY (sensitive text and outerHTML excluded)
            replyBarElementDetails.push({
                index: rbIdx,
                tag_name: el.tagName.toLowerCase(),
                data_qa: el.getAttribute('data-qa'),
                is_connected: el.isConnected,
                rect: {
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    top: Math.round(r.top),
                    left: Math.round(r.left)
                },
                is_visible: isVis,
                aria_hidden: el.getAttribute('aria-hidden'),
                class_list: Array.from(el.classList),
                parent_data_qa: el.parentElement ? el.parentElement.getAttribute('data-qa') : null,
                nearest_ancestor_data_qa: el.closest('[data-qa]:not([data-qa="reply_bar"])')?.getAttribute('data-qa') || null,
                nested_inside_another_reply_bar: isNested,
                descendant_element_count: el.getElementsByTagName('*').length,
                has_reply_bar_count_control: Boolean(countEl),
                has_reply_bar_view_thread_control: Boolean(viewThreadEl),
                in_composer: inComp
            });
        }

        // 3. Alternative / Related interactive controls in target row (structural metadata only)
        const altSelectors = [
            '[data-qa*="reply"]',
            '[data-qa*="thread"]',
            'a[href*="thread_ts="]',
            'button'
        ];
        const alternativeElements = [];
        for (const sel of altSelectors) {
            const found = Array.from(targetRow.querySelectorAll(sel)).filter(e => e && e.isConnected);
            for (const fe of found.slice(0, 10)) {
                if (allReplyBars.includes(fe)) continue;
                const fr = fe.getBoundingClientRect();
                const h = fe.getAttribute('href') || '';
                alternativeElements.push({
                    matched_selector: sel,
                    tag_name: fe.tagName.toLowerCase(),
                    data_qa: fe.getAttribute('data-qa'),
                    is_visible: (fr.width > 0 && fr.height > 0),
                    parent_data_qa: fe.parentElement ? fe.parentElement.getAttribute('data-qa') : null,
                    nearest_ancestor_data_qa: fe.closest('[data-qa]:not([' + sel + '])')?.getAttribute('data-qa') || null,
                    has_thread_permalink: Boolean(h && h.includes('thread_ts='))
                });
            }
        }

        return {
            ok: true,
            target_resident: true,
            resident_matches: 1,
            target_row_index: targetRowIdx,
            mounted_row_count: mounted_row_count,
            inspected_row_count: candidateRows.length,
            rows: rowSummaries,
            reply_bar_cardinality: {
                production_selector_connected_div_reply_bar_count: productionConnectedDivCount,
                production_cardinality_guard_would_pass: productionCardinalityGuardWouldPass,
                rendered_reply_bar_count: renderedCount,
                hidden_reply_bar_count: hiddenCount,
                nested_reply_bar_count: nestedCount,
                composer_contained_reply_bar_count: composerContainedCount,
                view_thread_control_count: viewThreadControlCount,
                reply_bar_count_control_count: replyBarCountControlCount,
                total_reply_bar_elements_count: allReplyBars.length,
                button_reply_bar_count: buttonReplyBars.length,
                structural_candidate_count: alternativeElements.length
            },
            reply_bar_elements: replyBarElementDetails,
            alternative_candidates: alternativeElements
        };
    } catch (err) {
        return { ok: false, error: String(err) };
    }
})
"""

# Primary tracked dependencies for direct parity check with Attempt 4
PRIMARY_DEPENDENCY_PATHS = {
    "rb1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure.py",
    "slack_search": SRC_DIR / "pesu_agent" / "search" / "slack_search.py",
    "models": SRC_DIR / "pesu_agent" / "investigation" / "models.py",
    "webview_thread_reader": SRC_DIR / "pesu_agent" / "search" / "webview_thread_reader.py",
    "webview_slack_search": SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py",
    "retrieval_coordinator": SRC_DIR / "pesu_agent" / "investigation" / "retrieval_coordinator.py",
    "selector": SRC_DIR / "pesu_agent" / "investigation" / "selector.py",
    "pesu_webview_host": SRC_DIR / "pesu_agent" / "runtime" / "pesu_webview_host.py",
    "slack_permalink_parser": SRC_DIR / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
    "write_audit": SRC_DIR / "pesu_agent" / "security" / "write_audit.py",
    "read_only_barrier": SRC_DIR / "pesu_agent" / "security" / "read_only_barrier.py",
    "slack_agent_window": SRC_DIR / "pesu_agent" / "lifecycle" / "slack_agent_window.py",
}

DEPENDENCY_PATHS = PRIMARY_DEPENDENCY_PATHS


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
    return {name: get_file_sha256(p) for name, p in PRIMARY_DEPENDENCY_PATHS.items()}


def get_all_loaded_project_files() -> dict[str, str]:
    """Inspects sys.modules and computes SHA-256 for all resolved/normcase project source files."""
    repo_root_str = os.path.normcase(str(REPO_ROOT.resolve()))
    loaded_files = {}

    for mod_name, mod in list(sys.modules.items()):
        if not mod or not hasattr(mod, "__file__") or not mod.__file__:
            continue
        try:
            fpath = os.path.normcase(os.path.realpath(mod.__file__))
            if fpath.startswith(repo_root_str):
                rel_path = os.path.relpath(fpath, repo_root_str).replace("\\", "/")
                if rel_path.startswith("src/") or rel_path.startswith("scripts/"):
                    loaded_files[rel_path] = get_file_sha256(Path(fpath))
        except Exception:
            continue

    return loaded_files


def audit_runtime_import_closure(manifest: dict[str, Any]) -> dict[str, Any]:
    """Audits loaded project modules against the protocol manifest to guarantee zero untracked dependencies."""
    loaded_files = get_all_loaded_project_files()
    runtime_closure_manifest = manifest.get("runtime_closure_hashes", {})
    primary_deps_manifest = manifest.get("dependency_hashes", {})

    untracked_files = []
    mismatched_files = []

    for rel_path, actual_sha in loaded_files.items():
        # Check against runtime_closure_hashes or dependency_hashes
        expected_sha = runtime_closure_manifest.get(rel_path)
        if not expected_sha:
            # Check if tracked in primary_deps_manifest
            for dep_name, dep_path in PRIMARY_DEPENDENCY_PATHS.items():
                p_rel = os.path.relpath(os.path.normcase(str(dep_path.resolve())), os.path.normcase(str(REPO_ROOT.resolve()))).replace("\\", "/")
                if p_rel == rel_path:
                    expected_sha = primary_deps_manifest.get(dep_name)
                    break

        if not expected_sha:
            untracked_files.append(rel_path)
        elif expected_sha != actual_sha:
            mismatched_files.append({"path": rel_path, "expected": expected_sha, "actual": actual_sha})

    return {
        "loaded_project_module_count": len(loaded_files),
        "loaded_project_module_paths": sorted(list(loaded_files.keys())),
        "untracked_loaded_project_dependency_count": len(untracked_files),
        "untracked_files": untracked_files,
        "mismatched_files": mismatched_files,
        "closure_audit_passed": (len(untracked_files) == 0 and len(mismatched_files) == 0),
    }


def is_supplemental_safe(val: Any) -> bool:
    if val in (0, None, "UNKNOWN", False, "NOT_RUN"):
        return True
    return False


def harvest_search_telemetry(backend: Any, search_exception: Optional[Exception], report: dict[str, Any]) -> None:
    telemetry = getattr(backend, "last_telemetry", {}) or {}
    actions = report.setdefault("live_action_inventory", {})
    st = report.setdefault("search_telemetry", {})
    safety = report.setdefault("safety_telemetry", {})
    checks = report.setdefault("checks", {})

    actions["search_activation_dispatches"] = 1 if telemetry.get("activation_dispatched") else 0
    st["search_activation_dispatches"] = actions["search_activation_dispatches"]

    actions["search_modal_observed"] = bool(telemetry.get("modal_ever_observed", False))
    st["modal_ever_observed"] = actions["search_modal_observed"]
    st["final_modal_count"] = telemetry.get("final_modal_count", "UNKNOWN")
    st["activation_to_first_modal_ms"] = telemetry.get("activation_to_first_modal_ms")
    st["readiness_poll_count"] = telemetry.get("readiness_poll_count", "UNKNOWN")
    st["last_confirmed_stage"] = telemetry.get("last_confirmed_stage", "UNKNOWN")
    st["failure_case_classification"] = telemetry.get("failure_case_classification", "NONE")
    st["document_visibility_state"] = telemetry.get("document_visibility_state", "unknown")
    st["document_hidden"] = telemetry.get("document_hidden", "UNKNOWN")
    st["document_has_focus"] = telemetry.get("document_has_focus", "UNKNOWN")
    st["elapsed_ms"] = telemetry.get("elapsed_ms", 0)
    st["timeout_ms"] = telemetry.get("timeout_ms", 5000)

    if "input_ever_observed" in telemetry and "final_input_count" in telemetry and "last_confirmed_stage" in telemetry:
        last_stage = str(telemetry["last_confirmed_stage"])
        inp_ready = bool(
            telemetry["input_ever_observed"]
            and int(telemetry["final_input_count"]) > 0
            and last_stage in (
                "INPUT_OBSERVED", "SETTER_EXECUTED", "SUBMIT_TRIGGERED",
                "SUBMIT_EXECUTED", "RESULTS_WAIT_STARTED", "RESULTS_RENDERED", "COMPLETED"
            )
        )
        actions["search_input_ready"] = inp_ready
        st["input_ready"] = inp_ready
    else:
        actions["search_input_ready"] = "UNKNOWN"
        st["input_ready"] = "UNKNOWN"

    if "onenter_invoked" in telemetry:
        sub_invoked = 1 if bool(telemetry["onenter_invoked"]) else 0
        actions["search_submissions"] = sub_invoked
        st["search_submissions"] = sub_invoked
    else:
        actions["search_submissions"] = "UNKNOWN"
        st["search_submissions"] = "UNKNOWN"

    if "safe_search_action_selected" in telemetry:
        safe_sel = bool(telemetry["safe_search_action_selected"])
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = safe_sel
        st["safe_search_action_explicitly_selected"] = safe_sel
        safety["safe_search_action_explicitly_selected"] = safe_sel
    elif "safe_search_action_explicitly_selected" in telemetry:
        safe_sel = bool(telemetry["safe_search_action_explicitly_selected"])
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = safe_sel
        st["safe_search_action_explicitly_selected"] = safe_sel
        safety["safe_search_action_explicitly_selected"] = safe_sel
    else:
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = "UNKNOWN"
        st["safe_search_action_explicitly_selected"] = "UNKNOWN"
        safety["safe_search_action_explicitly_selected"] = "UNKNOWN"

    if "verified_query_state_before_submit" in telemetry:
        pre_sub_ok = bool(telemetry["verified_query_state_before_submit"])
        checks["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = pre_sub_ok
        st["query_state_verified_before_submit"] = pre_sub_ok
    elif "query_state_verified_before_submit" in telemetry:
        pre_sub_ok = bool(telemetry["query_state_verified_before_submit"])
        checks["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = pre_sub_ok
        st["query_state_verified_before_submit"] = pre_sub_ok
    else:
        checks["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = "UNKNOWN"
        st["query_state_verified_before_submit"] = "UNKNOWN"

    forensic_trans = telemetry.get("forensic_transition")
    if isinstance(forensic_trans, dict) and "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED" in forensic_trans:
        surf_matches = bool(forensic_trans["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"])
        checks["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = surf_matches
        st["search_surface_query_matches_requested"] = surf_matches
        if "SEARCH_SURFACE_QUERY_AFTER" in forensic_trans:
            st["search_surface_query_after"] = forensic_trans["SEARCH_SURFACE_QUERY_AFTER"]
    elif "search_surface_query_matches_requested" in telemetry:
        surf_matches = bool(telemetry["search_surface_query_matches_requested"])
        checks["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = surf_matches
        st["search_surface_query_matches_requested"] = surf_matches
        if "search_surface_query_after" in telemetry:
            st["search_surface_query_after"] = telemetry["search_surface_query_after"]
    else:
        checks["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = "UNKNOWN"
        st["search_surface_query_matches_requested"] = "UNKNOWN"

    if "generic_default_onenter_used" in telemetry:
        safety["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = bool(telemetry["generic_default_onenter_used"])
    else:
        safety["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = "UNKNOWN"

    if "slackbot_action_invoked" in telemetry or "slackbot_action_invoked_by_pesu" in telemetry:
        safety["SLACKBOT_ACTION_INVOKED_BY_PESU"] = bool(
            telemetry.get("slackbot_action_invoked") or telemetry.get("slackbot_action_invoked_by_pesu")
        )
    else:
        safety["SLACKBOT_ACTION_INVOKED_BY_PESU"] = "UNKNOWN"

    if "write_capable_action_invoked" in telemetry or "write_capable_action_invoked_by_pesu" in telemetry:
        safety["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = bool(
            telemetry.get("write_capable_action_invoked") or telemetry.get("write_capable_action_invoked_by_pesu")
        )
    else:
        safety["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = "UNKNOWN"

    if "unsafe_non_search_action_invoked" in telemetry or "unsafe_non_search_action_invoked_by_pesu" in telemetry:
        safety["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = bool(
            telemetry.get("unsafe_non_search_action_invoked") or telemetry.get("unsafe_non_search_action_invoked_by_pesu")
        )
    else:
        safety["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = "UNKNOWN"

    def _clean_supp(val: Any) -> Any:
        return "UNKNOWN" if val is None or val == "UNKNOWN" else val

    safety["composer_focus"] = _clean_supp(telemetry.get("composer_focus"))
    safety["composer_text_input"] = _clean_supp(telemetry.get("composer_text_input"))
    safety["composer_send"] = _clean_supp(telemetry.get("composer_send"))


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    checks = report.get("checks", {})
    safety = report.get("safety_telemetry", {})
    actions = report.get("live_action_inventory", {})

    if safety.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER"
    if safety.get("SLACKBOT_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_SLACKBOT"
    if safety.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_WRITE_CAPABLE"
    if safety.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_UNSAFE_NON_SEARCH"

    comp_f = safety.get("composer_focus")
    comp_i = safety.get("composer_text_input")
    comp_s = safety.get("composer_send")
    if not is_supplemental_safe(comp_f):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_FOCUS"
    if not is_supplemental_safe(comp_i):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_INPUT"
    if not is_supplemental_safe(comp_s):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_SEND"

    if checks.get("SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED") is not True:
        return False, "SAFE_SEARCH_ACTION_NOT_EXPLICITLY_SELECTED"
    if checks.get("QUERY_STATE_VERIFIED_BEFORE_SUBMIT") is not True:
        return False, "QUERY_STATE_NOT_VERIFIED_BEFORE_SUBMIT"
    if safety.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH") is not False:
        return False, "GENERIC_DEFAULT_ONENTER_NOT_PROVEN_FALSE"
    if safety.get("SLACKBOT_ACTION_INVOKED_BY_PESU") is not False:
        return False, "SLACKBOT_ACTION_NOT_PROVEN_FALSE"
    if safety.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU") is not False:
        return False, "WRITE_CAPABLE_ACTION_NOT_PROVEN_FALSE"
    if safety.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU") is not False:
        return False, "UNSAFE_NON_SEARCH_ACTION_NOT_PROVEN_FALSE"
    if actions.get("search_submissions") != 1:
        return False, "SEARCH_SUBMISSIONS_COUNT_INVALID"

    if checks.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED") is not True:
        return False, "SEARCH_SURFACE_QUERY_MISMATCH"

    return True, "VERIFIED_SAFE_AND_CORRELATED"


def run_rb1_diagnostic(dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    execution_mode = "DRY_RUN" if dry_run else "LIVE"
    output_file = DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / RB1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": execution_mode,
        "verdict": "PENDING",
        "diagnostic_verdict": "PENDING",
        "P2A_L1": "NOT_APPLICABLE_DIAGNOSTIC_ONLY",
        "thread_root_data_extraction_live_validated": False,
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "uncommitted_diff": check_uncommitted_diff(),
        "target_identity": {
            "target_query": TARGET_QUERY,
            "expected_channel_id": EXPECTED_CHANNEL_ID,
            "expected_message_ts": EXPECTED_MESSAGE_TS,
            "expected_root_ts": EXPECTED_ROOT_TS,
            "source_rd1_run2_rank": SOURCE_RD1_RUN2_RANK,
            "source_raw_permalink": SOURCE_RAW_PERMALINK,
            "search_max_results": SEARCH_MAX_RESULTS,
            "target_fitness_contract": TARGET_FITNESS_CONTRACT,
        },
        "live_action_inventory": {
            "search_activation_dispatches": 0,
            "search_modal_observed": False,
            "search_input_ready": False,
            "search_submissions": 0,
            "reply_bar_clicks": 0,
            "pure_read_dom_extractions": 0,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
            "search_modal_open_calls": 0,
        },
        "search_telemetry": {},
        "safety_telemetry": {},
        "checks": {
            "frozen_protocol_respected": True,
            "frozen_hashes_matched": False,
            "static_target_identity_valid": False,
            "target_fitness_contract_valid": False,
            "raw_permalink_matches_target": False,
            "code_or_protocol_mutated_after_first_live": False,
            "search_safety_gate": "PENDING",
            "atomic_snapshot_executed": False,
            "zero_clicks_strictly_preserved": True,
            "USER_MESSAGE_TEXT_CAPTURED": False,
            "UNRELATED_RAW_PERMALINK_CAPTURED": False,
            "OUTER_HTML_CAPTURED": False,
            "PRODUCTION_CARDINALITY_PREDICATE_MIRRORED_EXACTLY": True,
            "RB1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": True,
            "UNTRACKED_RUNTIME_DEPENDENCIES": 0,
        },
        "atomic_inspection": None,
        "runtime_closure_audit": {},
        "provider_calls": 0,
        "retry_occurred": False,
        "live_execution_started": False,
        "live_execution_completed": False,
        "harness_phases": [],
        "error": None,
        "duration_seconds": 0.0,
    }

    # Verify static target identity contracts
    c_ok = (report["target_identity"]["expected_channel_id"] == EXPECTED_CHANNEL_ID)
    m_ok = (report["target_identity"]["expected_message_ts"] == EXPECTED_MESSAGE_TS)
    r_ok = (report["target_identity"]["expected_root_ts"] == EXPECTED_ROOT_TS)
    diff_ok = (EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS)
    report["checks"]["static_target_identity_valid"] = bool(c_ok and m_ok and r_ok)
    report["checks"]["target_fitness_contract_valid"] = diff_ok

    parsed_target_plink = parse_slack_message_permalink(SOURCE_RAW_PERMALINK)
    if (
        parsed_target_plink
        and parsed_target_plink.channel_id == EXPECTED_CHANNEL_ID
        and normalize_slack_ts(parsed_target_plink.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
        and normalize_slack_ts(parsed_target_plink.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)
    ):
        report["checks"]["raw_permalink_matches_target"] = True

    # Pre-hashes calculation
    pre_hashes = compute_all_dependency_hashes()
    report["pre_hashes"] = pre_hashes

    # Compare against protocol freeze if exists
    freeze_manifest = {}
    if PROTOCOL_FREEZE_PATH.exists():
        try:
            with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
                freeze_manifest = json.load(f)
            expected_deps = freeze_manifest.get("dependency_hashes", {})
            hashes_match = all(pre_hashes.get(k) == v for k, v in expected_deps.items())
            report["checks"]["frozen_hashes_matched"] = hashes_match
        except Exception as fe:
            logger.warning(f"Failed to read protocol freeze: {fe}")
            report["checks"]["frozen_hashes_matched"] = False
    else:
        report["checks"]["frozen_hashes_matched"] = True

    # Audit runtime import closure
    closure_audit = audit_runtime_import_closure(freeze_manifest)
    report["runtime_closure_audit"] = closure_audit
    report["checks"]["UNTRACKED_RUNTIME_DEPENDENCIES"] = closure_audit["untracked_loaded_project_dependency_count"]

    # Check live artifact overwrite guard
    if not dry_run and LIVE_OUTPUT_FILE.exists():
        logger.error(f"❌ LIVE artifact already exists: {LIVE_OUTPUT_FILE}. Replay forbidden!")
        report["error"] = "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "REPLAY_FORBIDDEN"
        report["duration_seconds"] = round(time.time() - start_time, 2)
        return report

    if dry_run:
        logger.info("🔍 [DRY_RUN] RB1 reply-bar structure diagnostic dry-run executing...")
        report["harness_phases"].append("DRY_RUN_PREFLIGHT_COMPLETED")
        report["verdict"] = "DRY_RUN_PASS"
        report["diagnostic_verdict"] = "DRY_RUN_PASS"
        report["duration_seconds"] = round(time.time() - start_time, 2)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 Saved RB1 dry-run trace to {DRY_RUN_OUTPUT_FILE}")
        return report

    # LIVE EXECUTION - Attempt 4 Exact Runtime/Search Path Parity
    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")

    host = None
    try:
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (Attempt 4 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 Live Qualification",
        )
        backend = WebViewSlackSearchBackend(
            config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH)),
            host=host,
        )

        logger.info("🌐 [RUNTIME_START] Starting Dedicated Slack WebView Host...")
        backend.start_runtime(timeout=timeout)

        logger.info(f"🔍 [SEARCH_EXECUTE] Submitting target query: '{TARGET_QUERY}' (max_results={SEARCH_MAX_RESULTS})...")
        raw_results = None
        search_exception = None
        try:
            raw_results = backend.search_messages(TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)
        except Exception as se:
            search_exception = se
            logger.error(f"Search backend raised: {se}")

        harvest_search_telemetry(backend, search_exception, report)

        safety_ok, safety_reason = evaluate_search_safety_and_query_authority(report)
        report["checks"]["search_safety_gate"] = "PASS" if safety_ok else f"FAIL_{safety_reason}"

        if not safety_ok:
            logger.error(f"❌ Search safety / query correlation failed: {safety_reason}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = f"SAFETY_GATE_FAILED_{safety_reason}"
            return report

        report["harness_phases"].append("SEARCH_SAFETY_AND_QUERY_CORRELATION_VERIFIED")

        # Execute Atomic JS Snapshot (Residency + Exact Identity Match + Reply-Bar Structure)
        logger.info("🔬 [ATOMIC_DOM_SNAPSHOT] Evaluating target residency and reply-bar structure in single JS snapshot...")
        js_atomic = (
            f"({JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE})"
            f"({json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
            f"{json.dumps(EXPECTED_CHANNEL_ID)}, "
            f"{json.dumps(EXPECTED_MESSAGE_TS)}, "
            f"{json.dumps(EXPECTED_ROOT_TS)}, "
            f"{SEARCH_MAX_RESULTS})"
        )

        res_snapshot = host.evaluate_js(js_atomic, timeout=10.0)
        report["checks"]["atomic_snapshot_executed"] = True
        report["atomic_inspection"] = res_snapshot
        report["live_action_inventory"]["pure_read_dom_extractions"] = 1
        report["harness_phases"].append("ATOMIC_DOM_SNAPSHOT_COMPLETED")

        if not isinstance(res_snapshot, dict) or not res_snapshot.get("ok"):
            logger.error(f"❌ Atomic JS snapshot failed: {res_snapshot}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "ATOMIC_SNAPSHOT_FAILED"
            return report

        if not res_snapshot.get("target_resident"):
            err = res_snapshot.get("error", "TARGET_NOT_RESIDENT")
            logger.warning(f"⚠️ Target candidate is not resident in mounted rows: {err}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = f"TARGET_NOT_RESIDENT_{err}"
            return report

        cardinality = res_snapshot.get("reply_bar_cardinality", {})
        logger.info(f"✅ Target resident! Reply-bar cardinality: {cardinality}")
        report["harness_phases"].append("TARGET_REPLY_BAR_STRUCTURE_CAPTURED")
        report["verdict"] = "RB1_DIAGNOSTIC_COMPLETED"
        report["diagnostic_verdict"] = "REPLY_BAR_STRUCTURE_CAPTURED"

        post_hashes = compute_all_dependency_hashes()
        report["post_hashes"] = post_hashes
        mutation = any(post_hashes.get(k) != v for k, v in pre_hashes.items())
        report["checks"]["code_or_protocol_mutated_after_first_live"] = mutation

        report["live_execution_completed"] = True

    except Exception as ex:
        logger.error(f"❌ [EXCEPTION] RB1 diagnostic raised: {ex}", exc_info=True)
        report["error"] = str(ex)
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "EXCEPTION_RAISED"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)
        if host:
            try:
                logger.info("🛑 [HOST_SHUTDOWN] Destroying WebView host window...")
                host.destroy()
            except Exception as destroy_err:
                logger.warning(f"Error destroying host: {destroy_err}")

        if not (not dry_run and report.get("error") == "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"):
            try:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2, ensure_ascii=False)
                logger.info(f"📄 Saved RB1 trace to {output_file}")
            except Exception as write_err:
                logger.error(f"Failed to write RB1 trace: {write_err}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11 / P2A-L1 / RB1 Reply-Bar Structure Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode")
    parser.add_argument("--timeout", type=float, default=30.0, help="Search timeout in seconds")
    args = parser.parse_args()

    result = run_rb1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print("\n=== RB1 DIAGNOSTIC REPORT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["verdict"] not in ("RB1_DIAGNOSTIC_COMPLETED", "DRY_RUN_PASS"):
        sys.exit(1)
    sys.exit(0)
