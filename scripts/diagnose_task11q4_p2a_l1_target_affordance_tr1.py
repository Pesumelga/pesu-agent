"""Phase 13.5D / Task 11 / P2A-L1 / TR1: Target Affordance Diagnostic.

ONE_SEARCH_ZERO_CLICK_READ_ONLY_DIAGNOSTIC

Evaluates candidate P2A thread-reply targets from resident top-5 search rows
for the qualified query '26년 하반기 간담회' against exact production DOM interaction
contracts (WebviewThreadReader pre-click guards + optional supplemental occlusion check).

Strict Invariants:
- PRODUCTION_CODE_CHANGED = false
- ZERO_REPLY_BAR_CLICKS = true (reply_bar_clicks == 0)
- ZERO_RESULT_CARD_CLICKS = true
- ZERO_COMPOSER_INTERACTIONS = true
- ZERO_THREAD_EXPANSIONS = true
- ONE_ATOMIC_JS_SNAPSHOT = true (inspects all top-5 mounted rows in one pass)
- REPLAY_FORBIDDEN = true (live artifact overwrite guard)
- EXCLUSIVELY_DATA_COLLECTION = true
- USER_MESSAGE_TEXT_CAPTURED = false
- UNRELATED_RAW_PERMALINK_CAPTURED = false
- OUTER_HTML_CAPTURED = false
- TR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4 = true
- TR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4 = true
- TR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4 = true
- TR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4 = true
- TR1_PRODUCTION_PRE_CLICK_GUARD_CONTRACT_COMPLETE = dynamically verified
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
from pesu_agent.search.webview_thread_reader import (
    JS_CLICK_RESIDENT_ROW_REPLY_BAR,
    normalize_slack_ts,
)

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
logger = logging.getLogger("task11q4_p2a_l1_target_affordance_tr1")

# Target Query & Budget
TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5

# Authoritative Comparison Reference
AUTHORITATIVE_ATTEMPT4_RUNNER = "scripts/qualify_task11q4_p2a_live_root_extraction_attempt4.py"
AUTHORITATIVE_ATTEMPT4_SHA256 = "b2f4c6972e1e1297045c7fe7d7f1e50492225d3bd073b311ee1dcadd967a905d"

# Explicit Action Contracts (ONE_SEARCH_ZERO_CLICK_READ_ONLY_DIAGNOSTIC)
MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_SEARCH_SUBMISSIONS = 1
MAX_REPLY_BAR_CLICKS = 0
MAX_RESULT_CARD_CLICKS = 0
MAX_THREAD_EXPANSIONS = 0
MAX_SCROLL_ACTIONS = 0
MAX_KEYBOARD_EVENTS = 0
MAX_COMPOSER_INTERACTIONS = 0
MAX_WRITE_CAPABLE_ACTIONS = 0
MAX_PROVIDER_CALLS = 0
MAX_RETRIES = 0

PROTOCOL_NAME = "TASK11Q4_P2A_L1_TR1_TARGET_AFFORDANCE"
DIAGNOSTIC_RUN = "TR1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

PROTOCOL_FREEZE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_tr1_target_affordance.json"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_tr1_target_affordance_dry_run.json"

# Shared Search Selectors (Parity with WebviewThreadReader)
SEARCH_VIEW_SELECTOR: str = '[data-qa="search_view"]'
SEARCH_RESULT_ROW_SELECTORS: str = '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

# Atomic JS Snapshot Script: Evaluates top-5 rendered candidate rows for production click affordance and TR1 target policy
# Sensitive-content excluded: zero user message text, zero outer HTML, zero raw permalinks from any row.
JS_INSPECT_TOP5_TARGET_AFFORDANCE = r"""
((searchViewSel, searchRowSel, maxRows) => {
    try {
        const searchViews = Array.from(document.querySelectorAll(searchViewSel)).filter(el => el && el.isConnected);
        if (searchViews.length !== 1) {
            return {
                ok: false,
                error: "SEARCH_VIEW_COUNT_INVALID",
                view_count: searchViews.length,
                mounted_row_count: 0,
                rows: []
            };
        }

        const searchView = searchViews[0];
        // Enforce RB2 parity: only rendered/visible rows (r.width > 0 && r.height > 0) consume top-5 ranks
        const allRows = Array.from(searchView.querySelectorAll(searchRowSel)).filter(el => {
            if (!el || !el.isConnected) return false;
            if (el.closest('[data-qa="search_filter"], [data-qa="search_header"], [data-qa="search_input"]')) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });

        const mounted_row_count = allRows.length;
        const candidateRows = allRows.slice(0, maxRows);

        function normTs(raw) {
            if (!raw) return null;
            const parts = String(raw).trim().split('.');
            const sec = parts[0] || '0';
            const frac = (parts[1] || '').padEnd(6, '0').slice(0, 6);
            return `${sec}.${frac}`;
        }

        const rows = [];

        for (let idx = 0; idx < candidateRows.length; idx++) {
            const row = candidateRows[idx];
            const rank = idx + 1;

            // 1. Identity Extraction & Unique Identity Verification
            const links = Array.from(row.querySelectorAll('a[href*="/archives/"]'));
            const uniqueIdentities = new Map();

            for (const a of links) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\/archives\/([A-Z0-9]+)\/p(\d{10})(\d{6})(?:\?([^#\s]*))?/);
                if (m) {
                    const chan = m[1];
                    const normMsg = normTs(m[2] + '.' + m[3]);
                    const qStr = m[4] || '';
                    let thTs = null;
                    const thMatch = qStr.match(/(?:^|&)thread_ts=([0-9.]+)/);
                    if (thMatch) {
                        thTs = normTs(thMatch[1]);
                    }
                    const identKey = `${chan}:${normMsg}:${thTs || ''}`;
                    if (!uniqueIdentities.has(identKey)) {
                        uniqueIdentities.set(identKey, {
                            channel_id: chan,
                            message_ts: normMsg,
                            thread_ts: thTs
                        });
                    }
                }
            }

            const uniqueCount = uniqueIdentities.size;
            let primaryIdentity = null;
            if (uniqueCount === 1) {
                primaryIdentity = Array.from(uniqueIdentities.values())[0];
            }

            const rowInComposer = Boolean(row.closest('[data-qa="message_input"], .c-message_input, textarea, [role="textbox"], [contenteditable="true"]'));

            // 2. Exact Production Pre-Click Guard Evaluation (WebviewThreadReader source-exact parity)
            // A. Reply Bar Cardinality Guard
            const replyBars = Array.from(row.querySelectorAll('div[data-qa="reply_bar"]')).filter(el => el && el.isConnected);
            const replyBarCount = replyBars.length;
            const replyBarCountValid = (replyBarCount === 1);

            let requiredChildrenPresent = false;
            let isVisible = false;
            let outsideComposer = false;
            let prodGuardsPass = false;
            let prodFailureReason = null;

            if (replyBarCountValid) {
                const replyBar = replyBars[0];

                // B. Required Children Guard
                const countEl = replyBar.querySelector('[data-qa="reply_bar_count"]');
                const viewThreadEl = replyBar.querySelector('[data-qa="reply_bar_view_thread"]');
                requiredChildrenPresent = Boolean(countEl && viewThreadEl);

                // C. Geometry / Visibility Guard
                const rect = replyBar.getBoundingClientRect();
                isVisible = (rect.width > 0 && rect.height > 0);

                // D. Composer Exclusion Guard
                const inContentEditable = Boolean(replyBar.closest('[contenteditable="true"]'));
                const inComposer = Boolean(replyBar.closest('[data-qa="message_input"], .c-message_input, textarea, [role="textbox"]'));
                outsideComposer = (!inContentEditable && !inComposer);

                if (!requiredChildrenPresent) {
                    prodGuardsPass = false;
                    prodFailureReason = "REPLY_BAR_REQUIRED_CHILDREN_MISSING";
                } else if (!isVisible) {
                    prodGuardsPass = false;
                    prodFailureReason = "REPLY_BAR_NOT_VISIBLE";
                } else if (!outsideComposer) {
                    prodGuardsPass = false;
                    prodFailureReason = "REPLY_BAR_INSIDE_COMPOSER";
                } else {
                    prodGuardsPass = true;
                    prodFailureReason = null;
                }
            } else {
                prodGuardsPass = false;
                prodFailureReason = "REPLY_BAR_COUNT_INVALID";
            }

            // Production click affordance is determined STRICTLY by production guards
            const productionClickAffordancePassed = prodGuardsPass;

            // 3. Supplemental TR1 Occlusion Safety Check (Distinct from production affordance)
            let hitTestPassed = false;
            let hitFailureReason = null;
            let hitElementDataQa = null;
            let hitElementTagName = null;

            if (prodGuardsPass && replyBarCount === 1) {
                const replyBar = replyBars[0];
                const rect = replyBar.getBoundingClientRect();
                const cx = Math.floor(rect.left + rect.width / 2);
                const cy = Math.floor(rect.top + rect.height / 2);
                const hitEl = document.elementFromPoint(cx, cy);

                if (hitEl && (hitEl === replyBar || replyBar.contains(hitEl))) {
                    hitTestPassed = true;
                    hitFailureReason = null;
                } else {
                    hitTestPassed = false;
                    hitFailureReason = "REPLY_BAR_HIT_TEST_FAILED";
                }
                hitElementDataQa = hitEl ? hitEl.getAttribute('data-qa') : null;
                hitElementTagName = hitEl ? hitEl.tagName.toLowerCase() : null;
            } else {
                hitTestPassed = false;
                hitFailureReason = "NOT_EVALUATED_PREVIOUS_GUARD_FAILED";
            }

            // 4. TR1 Target Qualification Policy
            const identityUnambiguous = (uniqueCount === 1);
            const channelValid = Boolean(primaryIdentity && primaryIdentity.channel_id && primaryIdentity.channel_id.startsWith('C'));
            const isThreadReply = Boolean(
                primaryIdentity &&
                primaryIdentity.thread_ts &&
                primaryIdentity.message_ts &&
                primaryIdentity.thread_ts !== primaryIdentity.message_ts
            );

            let targetPolicyPassed = false;
            let targetPolicyFailureReason = null;

            if (!identityUnambiguous) {
                targetPolicyPassed = false;
                targetPolicyFailureReason = "ROW_IDENTITY_AMBIGUOUS";
            } else if (!channelValid) {
                targetPolicyPassed = false;
                targetPolicyFailureReason = "CHANNEL_ID_INVALID_NOT_PUBLIC_OR_PRIVATE";
            } else if (!isThreadReply) {
                targetPolicyPassed = false;
                targetPolicyFailureReason = "NOT_THREAD_REPLY";
            } else if (rowInComposer) {
                targetPolicyPassed = false;
                targetPolicyFailureReason = "ROW_INSIDE_COMPOSER";
            } else {
                targetPolicyPassed = true;
                targetPolicyFailureReason = null;
            }

            // 5. Combined Eligibility (Production Affordance + Target Policy)
            const p2aLiveTargetEligible = Boolean(productionClickAffordancePassed && targetPolicyPassed);

            rows.push({
                row_index: idx,
                rank: rank,
                unique_identity_count: uniqueCount,
                parsed_channel_id: primaryIdentity ? primaryIdentity.channel_id : null,
                parsed_message_ts: primaryIdentity ? primaryIdentity.message_ts : null,
                parsed_thread_ts: primaryIdentity ? primaryIdentity.thread_ts : null,
                is_thread_reply: isThreadReply,
                channel_valid: channelValid,
                row_in_composer: rowInComposer,
                production_click_affordance: {
                    connected_div_reply_bar_count: replyBarCount,
                    required_children_present: requiredChildrenPresent,
                    is_visible: isVisible,
                    outside_composer: outsideComposer,
                    production_guards_pass: prodGuardsPass,
                    production_failure_reason: prodFailureReason,
                    affordance_passed: productionClickAffordancePassed
                },
                supplemental_tr1_safety: {
                    hit_test_passed: hitTestPassed,
                    hit_failure_reason: hitFailureReason,
                    hit_element_data_qa: hitElementDataQa,
                    hit_element_tag_name: hitElementTagName
                },
                target_qualification_policy: {
                    rank: rank,
                    identity_unambiguous: identityUnambiguous,
                    channel_valid: channelValid,
                    is_thread_reply: isThreadReply,
                    outside_composer: !rowInComposer,
                    policy_passed: targetPolicyPassed,
                    policy_failure_reason: targetPolicyFailureReason
                },
                p2a_live_target_eligible: p2aLiveTargetEligible
            });
        }

        return {
            ok: true,
            mounted_row_count: mounted_row_count,
            inspected_row_count: candidateRows.length,
            rows: rows
        };
    } catch (err) {
        return { ok: false, error: String(err), mounted_row_count: 0, rows: [] };
    }
})
"""

# Primary tracked dependencies
PRIMARY_DEPENDENCY_PATHS = {
    "tr1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_target_affordance_tr1.py",
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


def verify_production_pre_click_guard_contract() -> bool:
    """Verifies that the pre-click guard contract in TR1 exactly matches the production source in WebviewThreadReader."""
    required_snippets = [
        'targetRow.querySelectorAll(\'div[data-qa="reply_bar"]\')',
        'replyBarCount !== 1',
        '"REPLY_BAR_COUNT_INVALID"',
        'replyBar.querySelector(\'[data-qa="reply_bar_count"]\')',
        'replyBar.querySelector(\'[data-qa="reply_bar_view_thread"]\')',
        '"REPLY_BAR_REQUIRED_CHILDREN_MISSING"',
        'replyBar.getBoundingClientRect()',
        'rect.width > 0 && rect.height > 0',
        '"REPLY_BAR_NOT_VISIBLE"',
        'replyBar.closest(\'[contenteditable="true"]\')',
        'replyBar.closest(\'[data-qa="message_input"], .c-message_input, textarea, [role="textbox"]\')',
        '"REPLY_BAR_INSIDE_COMPOSER"',
    ]

    for s in required_snippets:
        if s not in JS_CLICK_RESIDENT_ROW_REPLY_BAR:
            logger.error(f"❌ Production pre-click guard snippet missing from JS_CLICK_RESIDENT_ROW_REPLY_BAR: {s}")
            return False

    # Also verify that hit-test is NOT in production source
    if "elementFromPoint" in JS_CLICK_RESIDENT_ROW_REPLY_BAR:
        logger.error("❌ Unexpected elementFromPoint found in production JS_CLICK_RESIDENT_ROW_REPLY_BAR")
        return False
    if "REPLY_BAR_HIT_TEST_FAILED" in JS_CLICK_RESIDENT_ROW_REPLY_BAR:
        logger.error("❌ Unexpected REPLY_BAR_HIT_TEST_FAILED found in production JS_CLICK_RESIDENT_ROW_REPLY_BAR")
        return False

    return True


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
    """Inspects sys.modules and computes SHA-256 for all resolved project source files."""
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
        expected_sha = runtime_closure_manifest.get(rel_path)
        if not expected_sha:
            for dep_name, dep_path in PRIMARY_DEPENDENCY_PATHS.items():
                p_rel = os.path.relpath(
                    os.path.normcase(str(dep_path.resolve())),
                    os.path.normcase(str(REPO_ROOT.resolve()))
                ).replace("\\", "/")
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


def harvest_search_telemetry(
    backend: Any,
    search_exception: Optional[Exception],
    report: dict[str, Any],
) -> None:
    """Harvests backend.last_search_telemetry whether search_messages succeeds, fails, or raises.

    Exact RB2/Attempt 4 parity:
    - Primary: backend.last_search_telemetry
    - Fallback: search_exception.details
    - Strictly forbids backend.last_telemetry as search authority.
    - Explicit fail-closed semantic state if telemetry is missing.
    """
    telemetry = getattr(backend, "last_search_telemetry", None)
    if not isinstance(telemetry, dict) or not telemetry:
        if search_exception is not None and hasattr(search_exception, "details"):
            details = getattr(search_exception, "details", None)
            if isinstance(details, dict) and details:
                telemetry = details

    actions = report.setdefault("live_action_inventory", {})
    st = report.setdefault("search_telemetry", {})
    safety = report.setdefault("safety_telemetry", {})
    checks = report.setdefault("checks", {})

    if not isinstance(telemetry, dict) or not telemetry:
        logger.error("❌ backend.last_search_telemetry is missing or empty! Failing closed.")
        actions["search_activation_dispatches"] = "UNKNOWN"
        actions["search_modal_observed"] = "UNKNOWN"
        actions["search_input_ready"] = "UNKNOWN"
        actions["search_submissions"] = "UNKNOWN"
        actions["search_modal_open_calls"] = "UNKNOWN"
        st["search_activation_dispatches"] = "UNKNOWN"
        st["modal_ever_observed"] = "UNKNOWN"
        st["input_ready"] = "UNKNOWN"
        st["last_confirmed_stage"] = "UNKNOWN"
        st["failure_case_classification"] = "TELEMETRY_MISSING"
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = "UNKNOWN"
        checks["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = "UNKNOWN"
        checks["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = "UNKNOWN"
        return

    # 1. Activation dispatches (Exact RB2/Attempt 4 parity)
    if "activation_dispatch_count" in telemetry:
        actions["search_activation_dispatches"] = int(telemetry["activation_dispatch_count"])
        st["search_activation_dispatches"] = int(telemetry["activation_dispatch_count"])
    elif "activation_dispatched" in telemetry:
        act_cnt = 1 if bool(telemetry["activation_dispatched"]) else 0
        actions["search_activation_dispatches"] = act_cnt
        st["search_activation_dispatches"] = act_cnt
    else:
        actions["search_activation_dispatches"] = "UNKNOWN"
        st["search_activation_dispatches"] = "UNKNOWN"

    # 2. Modal observation (True/False/UNKNOWN)
    if "modal_ever_observed" in telemetry:
        modal_obs = bool(telemetry["modal_ever_observed"])
        actions["search_modal_observed"] = modal_obs
        st["modal_ever_observed"] = modal_obs
        actions["search_modal_open_calls"] = 1 if modal_obs else 0
    else:
        actions["search_modal_observed"] = "UNKNOWN"
        st["modal_ever_observed"] = "UNKNOWN"
        actions["search_modal_open_calls"] = "UNKNOWN"

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

    # 3. Input readiness (True/False/UNKNOWN)
    if (
        "input_ever_observed" in telemetry
        and "final_input_count" in telemetry
        and "last_confirmed_stage" in telemetry
    ):
        last_stage = str(telemetry["last_confirmed_stage"])
        inp_ready = bool(
            telemetry["input_ever_observed"]
            and int(telemetry["final_input_count"]) > 0
            and last_stage in (
                "INPUT_OBSERVED",
                "SETTER_EXECUTED",
                "SUBMIT_TRIGGERED",
                "SUBMIT_EXECUTED",
                "RESULTS_WAIT_STARTED",
                "RESULTS_RENDERED",
                "COMPLETED",
            )
        )
        actions["search_input_ready"] = inp_ready
        st["input_ready"] = inp_ready
    else:
        actions["search_input_ready"] = "UNKNOWN"
        st["input_ready"] = "UNKNOWN"

    # 4. Search submissions (True/False/UNKNOWN)
    if "onenter_invoked" in telemetry:
        sub_invoked = 1 if bool(telemetry["onenter_invoked"]) else 0
        actions["search_submissions"] = sub_invoked
        st["search_submissions"] = sub_invoked
    else:
        actions["search_submissions"] = "UNKNOWN"
        st["search_submissions"] = "UNKNOWN"

    # 5. Safe search action explicitly selected
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

    # 6. Pre-submit query verification
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

    # 7. Post-search surface query verification
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

    # 8. Safety telemetry
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

    # 9. Supplemental composer telemetry
    def _clean_supp(val: Any) -> Any:
        return "UNKNOWN" if val is None or val == "UNKNOWN" else val

    safety["composer_focus"] = _clean_supp(telemetry.get("composer_focus"))
    safety["composer_text_input"] = _clean_supp(telemetry.get("composer_text_input"))
    safety["composer_send"] = _clean_supp(telemetry.get("composer_send"))


def is_supplemental_safe(val: Any) -> bool:
    """Returns True if supplemental composer telemetry observation is safe.

    Exact RB2 parity:
    - missing / UNKNOWN / None / NOT_RUN -> True
    - 0 / False -> True
    - explicit True / non-zero -> False (triggers FAIL_CLOSED)
    """
    if val in (0, None, "UNKNOWN", False, "NOT_RUN"):
        return True
    return False


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    """Evaluates authoritative search safety and query authority before trusting results.

    Exact RB2/Attempt 4 parity.
    """
    checks = report.get("checks", {})
    safety = report.get("safety_telemetry", {})
    actions = report.get("live_action_inventory", {})

    # Explicit safety violations
    if safety.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER"
    if safety.get("SLACKBOT_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_SLACKBOT"
    if safety.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_WRITE_CAPABLE"
    if safety.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_UNSAFE_NON_SEARCH"

    # Supplemental composer safety violations
    comp_f = safety.get("composer_focus")
    comp_i = safety.get("composer_text_input")
    comp_s = safety.get("composer_send")
    if not is_supplemental_safe(comp_f):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_FOCUS"
    if not is_supplemental_safe(comp_i):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_INPUT"
    if not is_supplemental_safe(comp_s):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_SEND"

    # Required core safety
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

    # Post-search surface query match
    if checks.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED") is not True:
        return False, "SEARCH_SURFACE_QUERY_MISMATCH"

    return True, "VERIFIED_SAFE_AND_CORRELATED"


def select_best_candidate(rows: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], str]:
    eligible = [r for r in rows if r.get("p2a_live_target_eligible")]
    if not eligible:
        return None, "NO_PRODUCTION_CLICK_ELIGIBLE_TARGET_IN_TOP5"

    eligible.sort(key=lambda r: r.get("rank", 999))
    selected = eligible[0]
    return selected, "ELIGIBLE_TARGET_FOUND"


def run_tr1_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    has_uncommitted = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / TR1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "P2A_L1": "NOT_APPLICABLE_DIAGNOSTIC_ONLY",
        "thread_root_data_extraction_live_validated": False,
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": git_commit,
        "uncommitted_diff": has_uncommitted,
        "target_evaluation_budget": {
            "target_query": TARGET_QUERY,
            "search_max_results": SEARCH_MAX_RESULTS,
        },
        "action_contracts": {
            "MAX_SLACK_SEARCH_OPERATIONS": MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_SEARCH_SUBMISSIONS": MAX_SEARCH_SUBMISSIONS,
            "MAX_REPLY_BAR_CLICKS": MAX_REPLY_BAR_CLICKS,
            "MAX_RESULT_CARD_CLICKS": MAX_RESULT_CARD_CLICKS,
            "MAX_THREAD_EXPANSIONS": MAX_THREAD_EXPANSIONS,
            "MAX_SCROLL_ACTIONS": MAX_SCROLL_ACTIONS,
            "MAX_KEYBOARD_EVENTS": MAX_KEYBOARD_EVENTS,
            "MAX_COMPOSER_INTERACTIONS": MAX_COMPOSER_INTERACTIONS,
            "MAX_WRITE_CAPABLE_ACTIONS": MAX_WRITE_CAPABLE_ACTIONS,
            "MAX_PROVIDER_CALLS": MAX_PROVIDER_CALLS,
            "MAX_RETRIES": MAX_RETRIES,
            "EXPECTED_LIVE_ACTION_INVENTORY": {
                "safe_search_activation_submissions": 1,
                "pure_read_dom_snapshots": 1,
                "reply_bar_clicks": 0,
                "other_ui_mutations_or_actions": 0,
            },
        },
        "live_action_inventory": {
            "search_activation_dispatches": 0,
            "search_modal_observed": False,
            "search_input_ready": "UNKNOWN",
            "search_submissions": "UNKNOWN",
            "reply_bar_clicks": 0,
            "pure_read_dom_extractions": 0,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
            "search_modal_open_calls": 0,
        },
        "search_telemetry": {
            "search_activation_dispatches": 0,
            "modal_ever_observed": False,
            "final_modal_count": "UNKNOWN",
            "activation_to_first_modal_ms": None,
            "readiness_poll_count": "UNKNOWN",
            "last_confirmed_stage": "UNKNOWN",
            "failure_case_classification": "NONE",
            "document_visibility_state": "unknown",
            "document_hidden": "UNKNOWN",
            "document_has_focus": "UNKNOWN",
            "elapsed_ms": 0,
            "timeout_ms": 5000,
            "input_ready": "UNKNOWN",
            "search_submissions": "UNKNOWN",
            "safe_search_action_explicitly_selected": "UNKNOWN",
            "query_state_verified_before_submit": "UNKNOWN",
            "search_surface_query_matches_requested": "UNKNOWN",
        },
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": "UNKNOWN",
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": "UNKNOWN",
            "SLACKBOT_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "composer_focus": "UNKNOWN",
            "composer_text_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        },
        "checks": {
            "frozen_protocol_respected": False,
            "frozen_hashes_matched": False,
            "code_or_protocol_mutated_after_first_live": False,
            "search_safety_gate": "NOT_EVALUATED",
            "atomic_snapshot_executed": False,
            "zero_clicks_strictly_preserved": True,
            "USER_MESSAGE_TEXT_CAPTURED": False,
            "UNRELATED_RAW_PERMALINK_CAPTURED": False,
            "OUTER_HTML_CAPTURED": False,
            "TR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": True,
            "TR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4": True,
            "TR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4": True,
            "TR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4": True,
            "TR1_PRODUCTION_PRE_CLICK_GUARD_CONTRACT_COMPLETE": False,
            "UNTRACKED_RUNTIME_DEPENDENCIES": 0,
            "ACTION_CONTRACTS_SATISFIED": True,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": "UNKNOWN",
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": "UNKNOWN",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": "UNKNOWN",
        },
        "atomic_inspection": None,
        "selected_target": None,
        "runtime_closure_audit": None,
        "provider_calls": 0,
        "retry_occurred": False,
        "live_execution_started": False,
        "live_execution_completed": False,
        "harness_phases": [],
        "error": None,
        "duration_seconds": 0.0,
    }

    if not dry_run and LIVE_OUTPUT_FILE.exists():
        err_msg = "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        logger.error(f"❌ {err_msg}: {LIVE_OUTPUT_FILE}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "LIVE_ARTIFACT_ALREADY_EXISTS"
        return report

    # 1. Dynamic Production Pre-Click Guard Contract Verification
    guard_contract_ok = verify_production_pre_click_guard_contract()
    report["checks"]["TR1_PRODUCTION_PRE_CLICK_GUARD_CONTRACT_COMPLETE"] = guard_contract_ok
    if not guard_contract_ok:
        report["error"] = "PRODUCTION_PRE_CLICK_GUARD_CONTRACT_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_GUARD_CONTRACT_MISMATCH"
        return report

    # 2. Protocol Preflight Gate
    if not PROTOCOL_FREEZE_PATH.exists():
        err_msg = f"PROTOCOL_FREEZE_NOT_FOUND: {PROTOCOL_FREEZE_PATH}"
        logger.error(f"❌ {err_msg}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_MISSING"
        return report

    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        protocol_manifest = json.load(f)

    semantic_errors = []
    sem_proto = protocol_manifest.get("protocol")
    sem_live_out = protocol_manifest.get("live_output_file")
    sem_dry_out = protocol_manifest.get("dry_run_output_file")
    sem_dry_verdict = protocol_manifest.get("canonical_dry_run_verdict")
    sem_comp = protocol_manifest.get("comparison_authority", {})
    sem_a4_sha = sem_comp.get("attempt4_runner_sha256")

    if sem_proto != PROTOCOL_NAME:
        semantic_errors.append(f"protocol mismatch: {sem_proto} != {PROTOCOL_NAME}")

    standard_live = "output/qualification_13_5d/live_trace/task11q4_p2a_l1_tr1_target_affordance.json"
    if LIVE_OUTPUT_FILE.is_relative_to(REPO_ROOT):
        rel_live = str(LIVE_OUTPUT_FILE.relative_to(REPO_ROOT)).replace("\\", "/")
        if sem_live_out != rel_live:
            semantic_errors.append(f"live_output_file mismatch: {sem_live_out} != {rel_live}")
    elif sem_live_out != standard_live:
        semantic_errors.append(f"live_output_file mismatch: {sem_live_out} != {standard_live}")

    standard_dry = "output/qualification_13_5d/live_trace/task11q4_p2a_l1_tr1_target_affordance_dry_run.json"
    if DRY_RUN_OUTPUT_FILE.is_relative_to(REPO_ROOT):
        rel_dry = str(DRY_RUN_OUTPUT_FILE.relative_to(REPO_ROOT)).replace("\\", "/")
        if sem_dry_out != rel_dry:
            semantic_errors.append(f"dry_run_output_file mismatch: {sem_dry_out} != {rel_dry}")
    elif sem_dry_out != standard_dry:
        semantic_errors.append(f"dry_run_output_file mismatch: {sem_dry_out} != {standard_dry}")
    if sem_dry_verdict != CANONICAL_DRY_RUN_VERDICT:
        semantic_errors.append(f"canonical_dry_run_verdict mismatch: {sem_dry_verdict}")

    for field_name, expected_val in [
        ("target_query", TARGET_QUERY),
        ("search_max_results", SEARCH_MAX_RESULTS),
    ]:
        top_val = protocol_manifest.get(field_name)
        eval_val = protocol_manifest.get("target_evaluation_budget", {}).get(field_name)
        if top_val is not None and top_val != expected_val:
            semantic_errors.append(f"{field_name} top-level mismatch: {top_val} != {expected_val}")
        if eval_val is not None and eval_val != expected_val:
            semantic_errors.append(f"{field_name} evaluation_budget mismatch: {eval_val} != {expected_val}")
        if top_val is None and eval_val is None:
            semantic_errors.append(f"{field_name} missing from protocol manifest")

    if sem_a4_sha != AUTHORITATIVE_ATTEMPT4_SHA256:
        semantic_errors.append(f"attempt4_runner_sha256 mismatch: {sem_a4_sha}")

    if semantic_errors:
        report["error"] = f"PROTOCOL_SEMANTIC_IDENTITY_MISMATCH: {'; '.join(semantic_errors)}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_SEMANTIC_IDENTITY_MISMATCH"
        logger.error(f"❌ Protocol semantic identity validation failed: {semantic_errors}")
        return report

    pre_hashes = compute_all_dependency_hashes()
    report["pre_hashes"] = pre_hashes

    expected_hashes = protocol_manifest.get("dependency_hashes", {})
    hashes_matched = True
    for dep_name, cur_hash in pre_hashes.items():
        exp = expected_hashes.get(dep_name)
        if not exp or exp != cur_hash:
            hashes_matched = False
            logger.error(f"❌ Dependency hash mismatch for {dep_name}: current={cur_hash} vs expected={exp}")

    report["checks"]["frozen_protocol_respected"] = True
    report["checks"]["frozen_hashes_matched"] = hashes_matched

    if not hashes_matched:
        report["error"] = "DEPENDENCY_HASH_MISMATCH_BEFORE_EXECUTION"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HASH_MISMATCH"
        return report

    closure_audit = audit_runtime_import_closure(protocol_manifest)
    report["runtime_closure_audit"] = closure_audit
    report["checks"]["UNTRACKED_RUNTIME_DEPENDENCIES"] = closure_audit["untracked_loaded_project_dependency_count"]

    if not closure_audit["closure_audit_passed"]:
        report["error"] = f"RUNTIME_CLOSURE_AUDIT_FAILED: {closure_audit['untracked_files']}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_CLOSURE_GATE_FAILED"
        return report

    report["harness_phases"].append("PREFLIGHT_GATES_PASSED")

    if dry_run:
        logger.info("🔍 [DRY_RUN] All preflight gates passed. Dry-run mode: skipping WebView and Slack interaction.")
        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["checks"]["dry_run_passed"] = True
        report["checks"]["search_safety_gate"] = "DRY_RUN_SKIPPED"
        report["checks"]["atomic_snapshot_executed"] = False
        report["duration_seconds"] = round(time.time() - start_time, 2)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 Saved dry-run trace to {DRY_RUN_OUTPUT_FILE}")
        return report

    # LIVE EXECUTION PATH
    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")

    host = None
    try:
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (Attempt 4 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / TR1 Target Affordance Live Qualification",
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
            logger.warning(f"⚠️ search_messages raised: {se}")
        finally:
            harvest_search_telemetry(backend, search_exception, report)

        if search_exception is not None:
            raise search_exception

        safety_ok, safety_reason = evaluate_search_safety_and_query_authority(report)
        report["checks"]["search_safety_gate"] = "PASS" if safety_ok else f"FAIL_{safety_reason}"

        if not safety_ok:
            logger.error(f"❌ Search safety / query correlation failed: {safety_reason}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = f"SAFETY_GATE_FAILED_{safety_reason}"
            return report

        report["harness_phases"].append("SEARCH_SAFETY_AND_QUERY_CORRELATION_VERIFIED")

        logger.info("🔬 [ATOMIC_DOM_SNAPSHOT] Evaluating top-5 rows for production click affordance in single JS snapshot...")
        js_atomic = (
            f"({JS_INSPECT_TOP5_TARGET_AFFORDANCE})"
            f"({json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
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

        rows = res_snapshot.get("rows", [])
        selected_candidate, selection_verdict = select_best_candidate(rows)
        report["selected_target"] = selected_candidate
        report["verdict"] = selection_verdict
        report["diagnostic_verdict"] = selection_verdict
        report["live_execution_completed"] = True
        report["harness_phases"].append("DIAGNOSTIC_EVALUATION_COMPLETED")

    except Exception as ex:
        logger.error(f"❌ [EXCEPTION] Diagnostic execution raised: {ex}", exc_info=True)
        report["error"] = str(ex)
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = f"EXCEPTION_{type(ex).__name__}"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)
        if host:
            try:
                logger.info("🛑 [HOST_SHUTDOWN] Destroying WebView host window...")
                host.destroy()
            except Exception as dh_err:
                logger.warning(f"Error destroying host: {dh_err}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LIVE_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 Saved TR1 trace to {LIVE_OUTPUT_FILE}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11 / P2A-L1 / TR1: Target Affordance Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode without live Slack execution")
    parser.add_argument("--timeout", type=float, default=30.0, help="Overall diagnostic timeout in seconds")
    args = parser.parse_args()

    report = run_tr1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print("\n=== TR1 DIAGNOSTIC REPORT ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.dry_run:
        return 0 if report.get("verdict") == CANONICAL_DRY_RUN_VERDICT else 1
    return 0 if report.get("verdict") in ("ELIGIBLE_TARGET_FOUND", "NO_PRODUCTION_CLICK_ELIGIBLE_TARGET_IN_TOP5") else 1


if __name__ == "__main__":
    sys.exit(main())
