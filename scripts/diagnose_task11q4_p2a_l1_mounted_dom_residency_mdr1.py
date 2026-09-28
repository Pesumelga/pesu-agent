"""Phase 13.5D / Task 11 / P2A / MDR1: Mounted DOM Exact Target Residency Diagnostic 1.

MDR1 PURPOSE:
After one valid search, is the exact known target currently resident anywhere among
the already-mounted search-result DOM rows, without scrolling or interacting?

Strict Boundaries:
- QUALIFICATION ONLY: Does NOT qualify production P2A, does NOT modify WebviewThreadReader,
  does NOT click any element, does NOT scroll any element, does NOT create Attempt 5.
- ONE ATOMIC READ-ONLY MOUNTED DOM SNAPSHOT:
  Resolves connected search_view, inspects all already-mounted rows up to the diagnostic
  cap (MAX_MOUNTED_ROWS_TO_INSPECT = 20), extracts identity metadata using safe permalink
  parsing, and verifies target residency.
  All in ONE bounded JavaScript evaluation.
- CRITICAL BOUNDARY:
  Backend search remains max_results = 5.
  This is a DIAGNOSTIC_MOUNTED_DOM_OBSERVATION, NOT PRODUCTION_TOP20_SELECTION.
  Mounted DOM index/position is NOT production search rank.
- ZERO INTERACTION:
  search operations <= 1, search submissions <= 1, dom snapshots <= 1.
  STRICT ZERO: anchor clicks = 0, reply_bar clicks = 0, generic result-card clicks = 0,
  scroll actions = 0, keyboard navigation = 0, composer interaction = 0, write actions = 0,
  provider calls = 0, retries = 0, restoration = 0, back navigation = 0.
- TARGET IDENTITY:
  channel_id == C042WS25QAX
  normalize(message_ts) == normalize(1718079276.126469)
  normalize(thread_ts) == normalize(1718058683.912369)
  is_thread_reply == true
- MOUNTED ROW DEFINITION:
  Explicitly distinguishes DOM_CONNECTED (isConnected) from
  RENDERED_POSITIVE_GEOMETRY (width > 0 && height > 0).
- CURRENT-QUERY CORRELATION:
  Evaluates whether mounted rows can be reliably attributed to the current query:
  CURRENT_SEARCH_SURFACE_CORRELATED vs
  MOUNTED_BUT_CURRENT_QUERY_CORRELATION_UNPROVEN vs
  STALE_OR_RECYCLED_ROW_POSSIBLE.
"""

from __future__ import annotations

import argparse
import ast
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
    JS_EXTRACT_SEARCH_RESULTS as PROD_JS_EXTRACT_SEARCH_RESULTS,
    SEARCH_RESULT_ROW_SELECTORS as PROD_SEARCH_RESULT_ROW_SELECTORS,
    SEARCH_VIEW_SELECTOR as PROD_SEARCH_VIEW_SELECTOR,
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)
from pesu_agent.search.webview_thread_reader import (
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
logger = logging.getLogger("task11q4_p2a_l1_mounted_dom_residency_mdr1")

# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5
MAX_MOUNTED_ROWS_TO_INSPECT = 20

PROTOCOL_NAME = "task11q4_p2a_l1_mdr1_mounted_dom_residency"
DIAGNOSTIC_RUN = "MDR1_RESIDENCY_RUN_1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

TARGET_CHANNEL_ID = "C042WS25QAX"
TARGET_MESSAGE_TS = "1718079276.126469"
TARGET_THREAD_TS = "1718058683.912369"
TARGET_IS_THREAD_REPLY = True

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_p2a_l1_mdr1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mdr1_mounted_dom_residency_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mdr1_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mdr1_mounted_dom_residency.json"

SEARCH_VIEW_SELECTOR = PROD_SEARCH_VIEW_SELECTOR
SEARCH_RESULT_ROW_SELECTORS = PROD_SEARCH_RESULT_ROW_SELECTORS

DIAGNOSTIC_OUTCOMES = [
    "TARGET_FOUND_IN_MOUNTED_DOM_CURRENT_QUERY_CORRELATED",
    "TARGET_FOUND_IN_MOUNTED_DOM_QUERY_CORRELATION_UNPROVEN",
    "TARGET_NOT_FOUND_IN_MOUNTED_DOM",
    "TARGET_IDENTITY_DUPLICATED_IN_MOUNTED_DOM",
    "MOUNTED_DOM_STATE_INSUFFICIENT",
    "FAIL_CLOSED",
]

MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_SEARCH_SUBMISSIONS = 1
MAX_READ_ONLY_DOM_SNAPSHOTS = 1
MAX_GUARDED_ANCHOR_CLICKS = 0
MAX_REPLY_BAR_CLICKS = 0
MAX_RESULT_CARD_CLICKS = 0
MAX_THREAD_EXPANSIONS = 0
MAX_SCROLL_ACTIONS = 0
MAX_KEYBOARD_EVENTS = 0
MAX_COMPOSER_INTERACTIONS = 0
MAX_WRITE_CAPABLE_ACTIONS = 0
MAX_PROVIDER_CALLS = 0
MAX_RETRIES = 0
MAX_RESTORATIONS = 0
MAX_BACK_NAVIGATIONS = 0

# ---------------------------------------------------------------------------
# JavaScript Atomic Mounted DOM Snapshot
# ---------------------------------------------------------------------------

JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT = r"""
((searchViewSelector, rowSelectors, maxMountedRows, targetChannelId, targetMessageTs, targetThreadTs, allowedOrigins) => {
    try {
        const searchView = document.querySelector(searchViewSelector);
        if (!searchView || !searchView.isConnected) {
            return {
                ok: false,
                failure_reason: "SEARCH_VIEW_NOT_FOUND",
                search_view_connected: false,
                mounted_row_count: 0,
                connected_row_count: 0,
                positive_geometry_row_count: 0,
                inspected_mounted_row_count: 0,
                target_match_count: 0,
                target_mounted_dom_position: null,
                target_connected: null,
                target_positive_geometry: null,
                mounted_row_records: []
            };
        }

        const normTs = (ts) => {
            if (!ts) return null;
            const str = String(ts).trim();
            const m = str.match(/^(\d+)(?:\.(\d+))?$/);
            if (!m) return null;
            return m[1] + "." + (m[2] || "").padEnd(6, "0").slice(0, 6);
        };

        const targetMsgNorm = normTs(targetMessageTs);
        const targetThrNorm = normTs(targetThreadTs);

        const rowSelectorStr = Array.isArray(rowSelectors) ? rowSelectors.join(", ") : rowSelectors;
        const allRows = Array.from(searchView.querySelectorAll(rowSelectorStr));
        const mounted_row_count = allRows.length;

        const inspectedRows = allRows.slice(0, maxMountedRows);
        const inspected_mounted_row_count = inspectedRows.length;

        let connected_row_count = 0;
        let positive_geometry_row_count = 0;

        const targetMatches = [];
        const mounted_row_records = [];

        for (let idx = 0; idx < inspectedRows.length; idx++) {
            const rowEl = inspectedRows[idx];
            const isConn = Boolean(rowEl && rowEl.isConnected);
            if (isConn) {
                connected_row_count++;
            }

            let hasPosGeom = false;
            try {
                const r = rowEl.getBoundingClientRect();
                hasPosGeom = (r.width > 0 && r.height > 0);
            } catch (e) {
                hasPosGeom = false;
            }
            if (hasPosGeom) {
                positive_geometry_row_count++;
            }

            const domPosition = idx + 1; // 1-indexed mounted position

            // Safe permalink parsing across anchors in this row
            const anchors = Array.from(rowEl.querySelectorAll("a[href]"));
            let rowParsedChannel = null;
            let rowParsedMsgTs = null;
            let rowParsedThrTs = null;
            let parseSuccess = false;
            let rowMatchesTarget = false;

            for (const a of anchors) {
                if (!a || !a.isConnected) continue;
                let urlObj = null;
                try {
                    urlObj = new URL(a.href, window.location.href);
                } catch (e) {
                    continue;
                }

                if (urlObj.protocol !== "https:") continue;
                if (!allowedOrigins.includes(urlObj.origin)) continue;

                const pathMatch = urlObj.pathname.match(/^\/archives\/([A-Z0-9]+)\/p(\d+)$/);
                if (!pathMatch) continue;

                const parsedChan = pathMatch[1];
                const rawDigits = pathMatch[2];
                const parsedMsg = normTs(rawDigits.slice(0, 10) + "." + rawDigits.slice(10));
                const parsedThr = normTs(urlObj.searchParams.get("thread_ts"));

                if (parsedChan && parsedMsg) {
                    rowParsedChannel = parsedChan;
                    rowParsedMsgTs = parsedMsg;
                    rowParsedThrTs = parsedThr;
                    parseSuccess = true;

                    if (parsedChan === targetChannelId && parsedMsg === targetMsgNorm && parsedThr === targetThrNorm) {
                        rowMatchesTarget = true;
                        break;
                    }
                }
            }

            const isThreadReply = Boolean(rowParsedThrTs && rowParsedMsgTs && rowParsedMsgTs !== rowParsedThrTs);

            const record = {
                mounted_dom_position: domPosition,
                channel_id: rowParsedChannel,
                message_ts: rowParsedMsgTs,
                thread_ts: rowParsedThrTs,
                connected: isConn,
                positive_geometry: hasPosGeom,
                identity_parse_success: parseSuccess,
                is_thread_reply: isThreadReply,
                is_target: rowMatchesTarget
            };
            mounted_row_records.push(record);

            if (rowMatchesTarget) {
                targetMatches.push(record);
            }
        }

        return {
            ok: true,
            search_view_connected: true,
            mounted_row_count: mounted_row_count,
            inspected_mounted_row_count: inspected_mounted_row_count,
            connected_row_count: connected_row_count,
            positive_geometry_row_count: positive_geometry_row_count,
            target_match_count: targetMatches.length,
            target_mounted_dom_position: targetMatches.length === 1 ? targetMatches[0].mounted_dom_position : null,
            target_connected: targetMatches.length === 1 ? targetMatches[0].connected : null,
            target_positive_geometry: targetMatches.length === 1 ? targetMatches[0].positive_geometry : null,
            mounted_row_records: mounted_row_records
        };
    } catch (err) {
        return {
            ok: false,
            failure_reason: "SNAPSHOT_EXCEPTION: " + String(err),
            search_view_connected: false,
            mounted_row_count: 0,
            connected_row_count: 0,
            positive_geometry_row_count: 0,
            inspected_mounted_row_count: 0,
            target_match_count: 0,
            target_mounted_dom_position: null,
            target_connected: null,
            target_positive_geometry: null,
            mounted_row_records: []
        };
    }
})
"""

# ---------------------------------------------------------------------------
# Correlation & Classification Authorities
# ---------------------------------------------------------------------------

def evaluate_search_surface_current_query_correlation(
    search_view_connected: bool,
    surface_query_matches: Optional[bool],
    search_readiness: str,
) -> bool:
    """Evaluates whether the active search surface is correlated with the requested query.

    Authority A: True when connected search_view, RESULTS_READY, and surface query matches requested.
    """
    if not search_view_connected:
        return False
    if surface_query_matches is not True:
        return False
    if search_readiness != "RESULTS_READY":
        return False
    return True


def evaluate_row_current_query_membership(
    row_connected: bool,
    surface_correlated: bool,
    is_in_backend_top5: bool = False,
) -> str:
    """Classifies individual mounted row query membership.

    Authority B:
    PROVEN: Connected row on correlated surface matching backend-returned identity.
    UNPROVEN: Connected row on correlated surface outside backend top 5.
    STALE_OR_RECYCLED_POSSIBLE: Disconnected row or uncorrelated search surface.
    """
    if not row_connected or not surface_correlated:
        return "STALE_OR_RECYCLED_POSSIBLE"
    if is_in_backend_top5:
        return "PROVEN"
    return "UNPROVEN"


def classify_mounted_dom_residency(
    dom_snapshot: Optional[dict[str, Any]],
) -> str:
    """Classifies exact target residency based on atomic DOM snapshot.

    Answers strictly: Is the exact target resident in the connected search_view DOM?
    Does not conflate physical DOM residency with production rank or query membership.
    """
    if not dom_snapshot or not isinstance(dom_snapshot, dict):
        return "FAIL_CLOSED"
    if not dom_snapshot.get("ok") or not dom_snapshot.get("search_view_connected"):
        return "FAIL_CLOSED"

    target_matches = dom_snapshot.get("target_match_count", 0)
    mounted_count = dom_snapshot.get("mounted_row_count", 0)

    if target_matches > 1:
        return "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"
    elif target_matches == 1:
        return "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
    else:  # target_matches == 0
        if mounted_count == 0:
            return "MOUNTED_DOM_STATE_INSUFFICIENT"
        else:
            return "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"


def build_mock_dry_run_dom_snapshot(simulated_position: int = 6) -> dict[str, Any]:
    """Builds a simulated mounted DOM snapshot for offline dry-run testing."""
    records = []
    for i in range(1, 21):
        is_target = (i == simulated_position)
        records.append({
            "mounted_dom_position": i,
            "channel_id": TARGET_CHANNEL_ID if is_target else f"C{i:08d}",
            "message_ts": TARGET_MESSAGE_TS if is_target else f"1788000000.{i:06d}",
            "thread_ts": TARGET_THREAD_TS if is_target else f"1788000000.{i:06d}",
            "connected": True,
            "positive_geometry": True,
            "identity_parse_success": True,
            "is_thread_reply": True if is_target else False,
            "is_target": is_target,
            "row_current_query_membership": "PROVEN" if (i <= 5) else "UNPROVEN",
        })
    return {
        "ok": True,
        "search_view_connected": True,
        "mounted_row_count": 20,
        "inspected_mounted_row_count": 20,
        "connected_row_count": 20,
        "positive_geometry_row_count": 20,
        "target_match_count": 1 if (1 <= simulated_position <= 20) else 0,
        "target_mounted_dom_position": simulated_position if (1 <= simulated_position <= 20) else None,
        "target_connected": True if (1 <= simulated_position <= 20) else None,
        "target_positive_geometry": True if (1 <= simulated_position <= 20) else None,
        "mounted_row_records": records,
    }

# ---------------------------------------------------------------------------
# Preflight & Contract Verification Functions
# ---------------------------------------------------------------------------

def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    deps = {
        "attempt4_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_thread_resolution_attempt4.py",
        "rb2_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py",
        "tr1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr1.py",
        "tr2_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2.py",
        "tr2r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py",
        "taiq1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py",
        "taiq1_protocol": REPO_ROOT / "artifacts" / "task11q4_p2a_l1_taiq1_protocol_freeze.json",
        "taiq1_marker": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_attempt_started.json",
        "taiq1_live": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_anchor_interaction.json",
        "vra1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py",
        "vra1_protocol": REPO_ROOT / "artifacts" / "task11q4_p2a_l1_vra1_protocol_freeze.json",
        "vra1_marker": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_attempt_started.json",
        "vra1_live": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_viewport_reachability.json",
        "mdr1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py",
    }
    res = {}
    for name, p in deps.items():
        if p.exists():
            res[name] = get_file_sha256(p)
        else:
            res[name] = "MISSING"
    return res


def extract_func_ast(tree: ast.AST, func_name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def verify_search_telemetry_authority_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "last_telemetry":
            logger.error(f"MDR1 runner incorrectly accesses prohibited attribute last_telemetry at line {node.lineno}")
            return False

    harvest_fn_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "harvest_search_telemetry":
            harvest_fn_node = node
            break

    if not harvest_fn_node:
        logger.error("MDR1 runner missing required harvest_search_telemetry function")
        return False

    accesses_last_search_telemetry = False
    for node in ast.walk(harvest_fn_node):
        if isinstance(node, ast.Attribute) and node.attr == "last_search_telemetry":
            accesses_last_search_telemetry = True
            break

    if not accesses_last_search_telemetry:
        logger.error("harvest_search_telemetry in MDR1 does not access backend.last_search_telemetry")
        return False

    return True


def verify_search_exception_order_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mdr1_diagnostic")
    if not runner_fn:
        logger.error("run_mdr1_diagnostic function not found in MDR1 runner AST")
        return False

    search_call_lineno = None
    harvest_call_lineno = None
    exception_raise_lineno = None

    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "search_messages":
                search_call_lineno = node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "harvest_search_telemetry":
                harvest_call_lineno = node.lineno
        if isinstance(node, ast.Raise):
            if isinstance(node.exc, ast.Name) and node.exc.id == "search_exception":
                exception_raise_lineno = node.lineno

    if not (search_call_lineno and harvest_call_lineno and exception_raise_lineno):
        logger.error("Failed to find required search/harvest/raise statements in MDR1 AST")
        return False

    if not (search_call_lineno < harvest_call_lineno < exception_raise_lineno):
        logger.error(
            f"MDR1 AST order violation: search_messages ({search_call_lineno}) < "
            f"harvest_search_telemetry ({harvest_call_lineno}) < raise search_exception ({exception_raise_lineno})"
        )
        return False

    return True


def verify_search_safety_gate_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mdr1_diagnostic")
    if not runner_fn:
        logger.error("run_mdr1_diagnostic function not found in MDR1 runner AST")
        return False

    eval_safety_lineno = None
    dom_snapshot_lineno = None

    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "evaluate_search_safety_and_query_authority":
                eval_safety_lineno = node.lineno
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "dom_res":
                    dom_snapshot_lineno = node.lineno

    if not eval_safety_lineno:
        logger.error("evaluate_search_safety_and_query_authority call missing from MDR1 runner AST")
        return False

    if dom_snapshot_lineno and not (eval_safety_lineno < dom_snapshot_lineno):
        logger.error("evaluate_search_safety_and_query_authority must precede dom snapshot evaluation")
        return False

    return True


def verify_runtime_search_path_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    required_patterns = [
        r'PesuWebViewHost\s*\(',
        r'user_data_folder\s*=\s*DIRECT_SLACK_PROFILE_PATH',
        r'initial_url\s*=\s*["\']https://app\.slack\.com/client["\']',
        r'WebViewSlackSearchBackend\s*\(',
        r'backend\.start_runtime\s*\(',
        r'backend\.search_messages\s*\(',
        r'harvest_search_telemetry\s*\(',
        r'host\.destroy\s*\(\s*\)',
    ]
    for pat in required_patterns:
        if not re.search(pat, content):
            logger.error(f"Missing required runtime pattern in MDR1 runner: {pat}")
            return False
    return True


def verify_host_dom_evaluation_api_parity(runner_path: Optional[Path] = None) -> bool:
    from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost

    if not hasattr(PesuWebViewHost, "evaluate_js") or not callable(getattr(PesuWebViewHost, "evaluate_js")):
        logger.error("PesuWebViewHost does not expose callable evaluate_js")
        return False
    if hasattr(PesuWebViewHost, "evaluate_script"):
        logger.error("PesuWebViewHost unexpectedly exposes evaluate_script - expected evaluate_js only")
        return False

    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "evaluate_script":
            logger.error(f"MDR1 runner incorrectly accesses attribute evaluate_script at line {node.lineno}")
            return False

    has_evaluate_js_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "host" and node.func.attr == "evaluate_js":
                has_evaluate_js_call = True
                break

    if not has_evaluate_js_call:
        logger.error("MDR1 runner missing required host.evaluate_js call")
        return False

    return True


def verify_search_result_row_enumeration_parity() -> bool:
    try:
        js_extract = PROD_JS_EXTRACT_SEARCH_RESULTS
        if "searchView.querySelectorAll(" not in js_extract:
            logger.error("Production JS_EXTRACT_SEARCH_RESULTS missing searchView.querySelectorAll.")
            return False

        if "JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT" not in globals():
            logger.error("JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT missing from runner globals.")
            return False

        mdr1_js = globals()["JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT"]

        norm_mdr1 = re.sub(r"\s+", "", mdr1_js)
        if "searchView.querySelectorAll(rowSelectorStr)" not in norm_mdr1:
            logger.error("MDR1 JS missing scoped row querySelectorAll.")
            return False

        if "PROD_SEARCH_VIEW_SELECTOR" not in globals() or SEARCH_VIEW_SELECTOR != PROD_SEARCH_VIEW_SELECTOR:
            logger.error("SEARCH_VIEW_SELECTOR does not match PROD_SEARCH_VIEW_SELECTOR.")
            return False

        if "PROD_SEARCH_RESULT_ROW_SELECTORS" not in globals() or SEARCH_RESULT_ROW_SELECTORS != PROD_SEARCH_RESULT_ROW_SELECTORS:
            logger.error("SEARCH_RESULT_ROW_SELECTORS does not match PROD_SEARCH_RESULT_ROW_SELECTORS.")
            return False

        return True
    except Exception as e:
        logger.error(f"Exception verifying row enumeration parity: {e}")
        return False


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    """Verifies that:
    1) JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT contains no executable scroll or click actions.
    2) Runner action contract has strict zero limits for interaction.
    """
    if "JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT" not in globals():
        logger.error("JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT missing from globals")
        return False

    js = globals()["JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT"]

    forbidden_js_patterns = [
        (r"\bscrollIntoView\b", "scrollIntoView"),
        (r"\bscrollTo\b", "scrollTo"),
        (r"\bscrollBy\b", "scrollBy"),
        (r"\bscrollTop\s*=", "scrollTop assignment"),
        (r"\bscrollLeft\s*=", "scrollLeft assignment"),
        (r"\bwheel\b", "wheel event"),
        (r"\bKeyboardEvent\b", "KeyboardEvent"),
        (r"\bdispatchEvent\b", "dispatchEvent"),
        (r"\.click\s*\(", ".click() invocation"),
        (r"\bMouseEvent\b", "MouseEvent"),
        (r"\bInputEvent\b", "InputEvent"),
        (r"\blocation\s*=", "location assignment"),
        (r"\blocation\.href\s*=", "location.href assignment"),
        (r"\bhistory\.push", "history.push"),
        (r"\bhistory\.replace", "history.replace"),
        (r"\.submit\s*\(", ".submit() invocation"),
    ]

    for pat, label in forbidden_js_patterns:
        if re.search(pat, js):
            logger.error(f"Forbidden pattern found in MDR1 JS: {label} ({pat})")
            return False

    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mdr1_diagnostic")
    if not runner_fn:
        return False

    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("click", "scroll", "scroll_into_view", "send_keys", "dispatch_key"):
                logger.error(f"Forbidden call {node.func.attr} in run_mdr1_diagnostic AST")
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
            ["git", "diff", "--quiet"],
            cwd=str(REPO_ROOT),
            capture_output=True,
        )
        return res.returncode != 0
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return True


def get_all_loaded_project_files() -> dict[str, str]:
    loaded_files: dict[str, str] = {}
    this_file = Path(__file__).resolve()
    rel_runner = this_file.relative_to(REPO_ROOT).as_posix()
    loaded_files[rel_runner] = get_file_sha256(this_file)

    for mod_name, mod in list(sys.modules.items()):
        if not mod or not hasattr(mod, "__file__") or not mod.__file__:
            continue
        try:
            mod_path = Path(mod.__file__).resolve()
            if mod_path.is_relative_to(SRC_DIR):
                rel_path = mod_path.relative_to(REPO_ROOT).as_posix()
                if rel_path.endswith(".py"):
                    loaded_files[rel_path] = get_file_sha256(mod_path)
        except Exception:
            continue

    return dict(sorted(loaded_files.items()))


def audit_runtime_import_closure(manifest: dict[str, Any]) -> dict[str, Any]:
    loaded_map = get_all_loaded_project_files()
    manifest_closure = manifest.get("runtime_closure_hashes", {})

    missing_from_closure = [p for p in loaded_map if p not in manifest_closure]
    stale_in_closure = [p for p in manifest_closure if p not in loaded_map]
    mismatched = [p for p in loaded_map if p in manifest_closure and loaded_map[p] != manifest_closure[p]]

    audit_passed = (len(missing_from_closure) == 0 and len(stale_in_closure) == 0 and len(mismatched) == 0)

    return {
        "closure_audit_passed": audit_passed,
        "loaded_file_count": len(loaded_map),
        "manifest_closure_count": len(manifest_closure),
        "missing_from_runtime_closure": missing_from_closure,
        "missing_from_runtime_closure_count": len(missing_from_closure),
        "stale_runtime_closure_entries": stale_in_closure,
        "stale_runtime_closure_entry_count": len(stale_in_closure),
        "mismatched_runtime_dependencies": mismatched,
        "mismatched_runtime_dependency_count": len(mismatched),
        "active_closure_hashes": loaded_map,
    }


def harvest_search_telemetry(
    backend: Optional[WebViewSlackSearchBackend],
    search_exception: Optional[Exception],
    report: dict[str, Any],
) -> None:
    telem = None
    if backend is not None and getattr(backend, "last_search_telemetry", None):
        telem = backend.last_search_telemetry
    elif search_exception is not None and getattr(search_exception, "details", None):
        telem = search_exception.details

    if telem:
        report["search_telemetry"] = telem
        if "forensic_transition" in telem:
            ft = telem["forensic_transition"]
            report["live_action_inventory"]["search_submissions"] = 1 if ft.get("query_submit_triggered") else 0
        if "safe_search_action_selected" in telem:
            report["safety_telemetry"]["safe_search_action_explicitly_selected"] = telem.get("safe_search_action_selected")
        if "generic_default_onenter_used" in telem:
            report["safety_telemetry"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = telem.get("generic_default_onenter_used")
        if "slackbot_action_invoked_by_pesu" in telem:
            report["safety_telemetry"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] = telem.get("slackbot_action_invoked_by_pesu")
        if "write_capable_action_invoked_by_pesu" in telem:
            report["safety_telemetry"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = telem.get("write_capable_action_invoked_by_pesu")
        if "unsafe_non_search_action_invoked_by_pesu" in telem:
            report["safety_telemetry"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = telem.get("unsafe_non_search_action_invoked_by_pesu")

        report["search_telemetry_extracted"] = {
            "safe_search_action_explicitly_selected": telem.get("safe_search_action_selected"),
            "query_state_verified_before_submit": telem.get("verified_query_state_before_submit"),
            "search_surface_query_matches_requested": telem.get("forensic_transition", {}).get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"),
            "search_surface_query_after": telem.get("forensic_transition", {}).get("SEARCH_SURFACE_QUERY_AFTER"),
        }


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    st = report.get("search_telemetry", {})
    ft = st.get("forensic_transition", {})
    st_extr = report.get("search_telemetry_extracted", {})

    safe_selected = st_extr.get("safe_search_action_explicitly_selected")
    if safe_selected is not True:
        return False, "SAFE_SEARCH_ACTION_NOT_SELECTED"

    query_verified = st_extr.get("query_state_verified_before_submit")
    if query_verified is not True:
        return False, "QUERY_STATE_NOT_VERIFIED_BEFORE_SUBMIT"

    surface_matched = st_extr.get("search_surface_query_matches_requested")
    if surface_matched is not True:
        return False, "SURFACE_QUERY_NOT_MATCHED"

    safe_telem = report.get("safety_telemetry", {})
    if safe_telem.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH") is True:
        return False, "GENERIC_DEFAULT_ONENTER_USED"
    if safe_telem.get("SLACKBOT_ACTION_INVOKED_BY_PESU") is True:
        return False, "SLACKBOT_ACTION_INVOKED"
    if safe_telem.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU") is True:
        return False, "WRITE_CAPABLE_ACTION_INVOKED"
    if safe_telem.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU") is True:
        return False, "UNSAFE_ACTION_INVOKED"

    return True, "SAFE"

# ---------------------------------------------------------------------------
# Diagnostic Runner
# ---------------------------------------------------------------------------

def run_mdr1_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / MDR1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "residency_classification": "UNKNOWN",
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": git_commit,
        "uncommitted_diff": git_diff,
        "target_identity": {
            "channel_id": TARGET_CHANNEL_ID,
            "message_ts": TARGET_MESSAGE_TS,
            "thread_ts": TARGET_THREAD_TS,
            "is_thread_reply": TARGET_IS_THREAD_REPLY,
            "exact_target_match_required": True,
        },
        "action_contracts": {
            "MAX_SLACK_SEARCH_OPERATIONS": MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_SEARCH_SUBMISSIONS": MAX_SEARCH_SUBMISSIONS,
            "MAX_READ_ONLY_DOM_SNAPSHOTS": MAX_READ_ONLY_DOM_SNAPSHOTS,
            "MAX_GUARDED_ANCHOR_CLICKS": MAX_GUARDED_ANCHOR_CLICKS,
            "MAX_REPLY_BAR_CLICKS": MAX_REPLY_BAR_CLICKS,
            "MAX_RESULT_CARD_CLICKS": MAX_RESULT_CARD_CLICKS,
            "MAX_THREAD_EXPANSIONS": MAX_THREAD_EXPANSIONS,
            "MAX_SCROLL_ACTIONS": MAX_SCROLL_ACTIONS,
            "MAX_KEYBOARD_EVENTS": MAX_KEYBOARD_EVENTS,
            "MAX_COMPOSER_INTERACTIONS": MAX_COMPOSER_INTERACTIONS,
            "MAX_WRITE_CAPABLE_ACTIONS": MAX_WRITE_CAPABLE_ACTIONS,
            "MAX_PROVIDER_CALLS": MAX_PROVIDER_CALLS,
            "MAX_RETRIES": MAX_RETRIES,
            "MAX_RESTORATIONS": MAX_RESTORATIONS,
            "MAX_BACK_NAVIGATIONS": MAX_BACK_NAVIGATIONS,
        },
        "live_action_inventory": {
            "search_activation_dispatches": 0,
            "search_modal_observed": False,
            "search_input_ready": "UNKNOWN",
            "search_submissions": 0 if dry_run else "UNKNOWN",
            "pure_read_dom_extractions": 0,
            "read_only_dom_snapshots": 0,
            "guarded_anchor_clicks": 0,
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
            "scroll_actions": 0,
            "keyboard_events": 0,
            "composer_interactions": 0,
            "write_capable_actions": 0,
            "restorations_attempted": 0,
            "back_navigations_attempted": 0,
        },
        "checks": {
            "USER_MESSAGE_TEXT_CAPTURED": False,
            "SENDER_TEXT_CAPTURED": False,
            "RAW_HREF_CAPTURED": False,
            "RAW_PERMALINK_CAPTURED": False,
            "RAW_ARIA_LABEL_CAPTURED": False,
            "RAW_TITLE_CAPTURED": False,
            "INNER_TEXT_CAPTURED": False,
            "TEXT_CONTENT_CAPTURED": False,
            "INNER_HTML_CAPTURED": False,
            "OUTER_HTML_CAPTURED": False,
            "VRA1_IMMUTABLE_BASELINES_MATCHED": False,
        },
        "dom_telemetry": None,
        "simulation_dom": None,
        "search_surface_current_query_correlated": "UNKNOWN",
        "target_row_current_query_membership": "UNKNOWN",
        "runtime_closure_audit": None,
        "provider_calls": 0,
        "retry_occurred": False,
        "live_execution_started": False,
        "live_execution_completed": False,
        "harness_phases": [],
        "error": None,
        "duration_seconds": 0.0,
    }

    if not dry_run:
        if LIVE_OUTPUT_FILE.exists():
            err_msg = "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
            logger.error(f"🛑 {err_msg}: {LIVE_OUTPUT_FILE}")
            report["error"] = err_msg
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "LIVE_ARTIFACT_ALREADY_EXISTS"
            return report

        if ATTEMPT_STARTED_MARKER_FILE.exists():
            err_msg = "ATTEMPT_MARKER_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
            logger.error(f"🛑 {err_msg}: {ATTEMPT_STARTED_MARKER_FILE}")
            report["error"] = err_msg
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
            return report

    row_enum_ok = verify_search_result_row_enumeration_parity()
    report["checks"]["MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = row_enum_ok
    if not row_enum_ok:
        report["error"] = "SEARCH_RESULT_ROW_ENUMERATION_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_ROW_ENUMERATION_PARITY_MISMATCH"
        return report

    runtime_parity_ok = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = runtime_parity_ok
    if not runtime_parity_ok:
        report["error"] = "RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        return report

    telem_auth_ok = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = telem_auth_ok
    if not telem_auth_ok:
        report["error"] = "SEARCH_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        return report

    exc_order_parity_ok = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = exc_order_parity_ok
    if not exc_order_parity_ok:
        report["error"] = "SEARCH_EXCEPTION_ORDER_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_EXCEPTION_ORDER_PARITY_MISMATCH"
        return report

    safety_gate_parity_ok = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = safety_gate_parity_ok
    if not safety_gate_parity_ok:
        report["error"] = "SEARCH_SAFETY_GATE_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_SAFETY_GATE_PARITY_MISMATCH"
        return report

    host_api_parity_ok = verify_host_dom_evaluation_api_parity()
    report["checks"]["MDR1_HOST_DOM_EVALUATION_API_PARITY"] = host_api_parity_ok
    if not host_api_parity_ok:
        report["error"] = "HOST_DOM_EVALUATION_API_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HOST_API_PARITY_MISMATCH"
        return report

    static_zero_ok = verify_static_zero_interaction_contract()
    report["checks"]["MDR1_STATIC_ZERO_INTERACTION_AUDIT"] = static_zero_ok
    if not static_zero_ok:
        report["error"] = "STATIC_ZERO_INTERACTION_AUDIT_FAILED"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_STATIC_ZERO_AUDIT_FAILED"
        return report

    if not PROTOCOL_FREEZE_PATH.exists():
        err_msg = f"PROTOCOL_FREEZE_NOT_FOUND: {PROTOCOL_FREEZE_PATH}"
        logger.error(f"🛑 {err_msg}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_MISSING"
        return report

    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        protocol_manifest = json.load(f)

    semantic_errors = []
    if protocol_manifest.get("protocol") != PROTOCOL_NAME:
        semantic_errors.append(f"protocol mismatch: {protocol_manifest.get('protocol')} != {PROTOCOL_NAME}")
    if protocol_manifest.get("target_query") != TARGET_QUERY:
        semantic_errors.append(f"target_query mismatch: {protocol_manifest.get('target_query')} != {TARGET_QUERY}")
    if protocol_manifest.get("search_max_results") != SEARCH_MAX_RESULTS:
        semantic_errors.append(f"search_max_results mismatch: {protocol_manifest.get('search_max_results')} != {SEARCH_MAX_RESULTS}")
    if protocol_manifest.get("max_mounted_rows_to_inspect") != MAX_MOUNTED_ROWS_TO_INSPECT:
        semantic_errors.append(f"max_mounted_rows_to_inspect mismatch: {protocol_manifest.get('max_mounted_rows_to_inspect')} != {MAX_MOUNTED_ROWS_TO_INSPECT}")
    if protocol_manifest.get("canonical_dry_run_verdict") != CANONICAL_DRY_RUN_VERDICT:
        semantic_errors.append(f"canonical_dry_run_verdict mismatch: {protocol_manifest.get('canonical_dry_run_verdict')} != {CANONICAL_DRY_RUN_VERDICT}")

    proto_target = protocol_manifest.get("target_identity", {})
    if proto_target.get("channel_id") != TARGET_CHANNEL_ID:
        semantic_errors.append(f"target_identity channel_id mismatch: {proto_target.get('channel_id')} != {TARGET_CHANNEL_ID}")
    if proto_target.get("message_ts") != TARGET_MESSAGE_TS:
        semantic_errors.append(f"target_identity message_ts mismatch: {proto_target.get('message_ts')} != {TARGET_MESSAGE_TS}")
    if proto_target.get("thread_ts") != TARGET_THREAD_TS:
        semantic_errors.append(f"target_identity thread_ts mismatch: {proto_target.get('thread_ts')} != {TARGET_THREAD_TS}")

    if semantic_errors:
        report["error"] = f"PROTOCOL_SEMANTIC_IDENTITY_MISMATCH: {'; '.join(semantic_errors)}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_SEMANTIC_IDENTITY_MISMATCH"
        logger.error(f"🛑 Protocol semantic identity validation failed: {semantic_errors}")
        return report

    pre_hashes = compute_all_dependency_hashes()
    report["pre_hashes"] = pre_hashes

    expected_hashes = protocol_manifest.get("dependency_hashes", {})
    hashes_matched = True
    for dep_name, cur_hash in pre_hashes.items():
        exp = expected_hashes.get(dep_name)
        if not exp or exp != cur_hash:
            hashes_matched = False
            logger.error(f"🛑 Dependency hash mismatch for {dep_name}: current={cur_hash} vs expected={exp}")

    report["checks"]["frozen_protocol_respected"] = True
    report["checks"]["frozen_hashes_matched"] = hashes_matched

    if not hashes_matched:
        report["error"] = "DEPENDENCY_HASH_MISMATCH_BEFORE_EXECUTION"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HASH_MISMATCH"
        return report

    historical_baselines = protocol_manifest.get("historical_baselines", {})
    hist_matched = True
    hist_errors = []
    for h_name, h_info in historical_baselines.items():
        h_path = REPO_ROOT / h_info["path"]
        if not h_path.exists():
            hist_matched = False
            hist_errors.append(f"{h_name}: missing {h_info['path']}")
        else:
            act_h = get_file_sha256(h_path)
            if act_h != h_info["sha256"]:
                hist_matched = False
                hist_errors.append(f"{h_name}: expected {h_info['sha256']} but got {act_h}")

    report["checks"]["historical_baselines_matched"] = hist_matched
    report["checks"]["VRA1_IMMUTABLE_BASELINES_MATCHED"] = (
        hist_matched
        and "vra1_marker" in historical_baselines
        and "vra1_live" in historical_baselines
    )
    if not hist_matched:
        report["error"] = f"HISTORICAL_BASELINE_MISMATCH: {'; '.join(hist_errors)}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HISTORICAL_HASH_MISMATCH"
        return report

    closure_audit = audit_runtime_import_closure(protocol_manifest)
    report["runtime_closure_audit"] = closure_audit
    report["checks"]["UNTRACKED_RUNTIME_DEPENDENCIES"] = closure_audit["missing_from_runtime_closure_count"]
    report["checks"]["STALE_RUNTIME_CLOSURE_ENTRIES"] = closure_audit["stale_runtime_closure_entry_count"]
    report["checks"]["MISMATCHED_RUNTIME_DEPENDENCIES"] = closure_audit["mismatched_runtime_dependency_count"]

    if not closure_audit["closure_audit_passed"]:
        report["error"] = (
            f"RUNTIME_CLOSURE_AUDIT_FAILED: "
            f"missing={closure_audit['missing_from_runtime_closure']}, "
            f"stale={closure_audit['stale_runtime_closure_entries']}, "
            f"mismatched={closure_audit['mismatched_runtime_dependencies']}"
        )
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_CLOSURE_GATE_FAILED"
        return report

    report["harness_phases"].append("PREFLIGHT_GATES_PASSED")

    if dry_run:
        logger.info("🧪 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock inspection...")
        report["dry_run"] = True
        report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = True
        report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = True
        report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = True
        report["checks"]["search_safety_gate"] = "PASS"
        report["checks"]["atomic_dom_snapshot_executed"] = True

        # STRICT ZERO ACTUAL LIVE ACTIONS IN DRY RUN
        report["live_action_inventory"]["search_activation_dispatches"] = 0
        report["live_action_inventory"]["search_modal_observed"] = False
        report["live_action_inventory"]["search_input_ready"] = "UNKNOWN"
        report["live_action_inventory"]["search_submissions"] = 0
        report["live_action_inventory"]["pure_read_dom_extractions"] = 0
        report["live_action_inventory"]["read_only_dom_snapshots"] = 0
        report["live_action_inventory"]["guarded_anchor_clicks"] = 0
        report["live_action_inventory"]["reply_bar_clicks"] = 0
        report["live_action_inventory"]["result_card_clicks"] = 0
        report["live_action_inventory"]["scroll_actions"] = 0
        report["live_action_inventory"]["keyboard_events"] = 0
        report["live_action_inventory"]["composer_interactions"] = 0
        report["live_action_inventory"]["write_capable_actions"] = 0
        report["live_action_inventory"]["restorations_attempted"] = 0
        report["live_action_inventory"]["back_navigations_attempted"] = 0

        mock_dom = build_mock_dry_run_dom_snapshot(simulated_position=6)
        report["simulation_dom"] = mock_dom
        report["dom_telemetry"] = None

        surface_correlated = evaluate_search_surface_current_query_correlation(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
        )
        report["search_surface_current_query_correlated"] = surface_correlated
        target_membership = evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=surface_correlated,
            is_in_backend_top5=False,  # Position 6 is outside backend top 5
        )
        report["target_row_current_query_membership"] = target_membership
        classification = classify_mounted_dom_residency(mock_dom)
        report["residency_classification"] = classification

        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["harness_phases"].append("DRY_RUN_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 [DRY_RUN_PASS] Dry run completed successfully. Persisted to {DRY_RUN_OUTPUT_FILE}")
        return report

    # -----------------------------------------------------------------------
    # LIVE EXECUTION PATH (Exclusive One-Shot Consumption)
    # -----------------------------------------------------------------------
    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    marker_content = {
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "git_commit": git_commit,
        "runner_sha256": pre_hashes.get("mdr1_runner"),
        "protocol_sha256": get_file_sha256(PROTOCOL_FREEZE_PATH),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "attempt_consumed": True,
    }
    try:
        with open(ATTEMPT_STARTED_MARKER_FILE, "x", encoding="utf-8") as mf:
            json.dump(marker_content, mf, indent=2, ensure_ascii=False)
        report["checks"]["attempt_marker_written"] = True
        logger.info(f"🔒 [ATTEMPT_CONSUMED] Wrote attempt marker to {ATTEMPT_STARTED_MARKER_FILE}")
    except FileExistsError:
        err_msg = "ATTEMPT_MARKER_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        logger.error(f"🛑 {err_msg}: {ATTEMPT_STARTED_MARKER_FILE}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
        return report

    host: Optional[PesuWebViewHost] = None
    try:
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (MDR1 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / MDR1 Mounted DOM Residency",
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
            logger.error(f"🛑 Search safety / query correlation failed: {safety_reason}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = f"SEARCH_SAFETY_FAILED_{safety_reason}"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        report["harness_phases"].append("SEARCH_SAFETY_AND_QUERY_CORRELATION_VERIFIED")

        logger.info("📐 [DOM_SNAPSHOT] Evaluating mounted DOM rows for target residency...")
        js_dom_snapshot = (
            f"({JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT})("
            f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
            f"{MAX_MOUNTED_ROWS_TO_INSPECT}, "
            f"{json.dumps(TARGET_CHANNEL_ID)}, "
            f"{json.dumps(TARGET_MESSAGE_TS)}, "
            f"{json.dumps(TARGET_THREAD_TS)}, "
            f"{json.dumps(FROZEN_ALLOWED_ORIGINS)})"
        )
        dom_res = host.evaluate_js(js_dom_snapshot, timeout=10.0)
        report["live_action_inventory"]["pure_read_dom_extractions"] += 1
        report["live_action_inventory"]["read_only_dom_snapshots"] = 1

        if not isinstance(dom_res, dict):
            logger.error(f"🛑 DOM snapshot returned non-dict: {type(dom_res)}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "DOM_SNAPSHOT_EVALUATION_FAILED"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        report["dom_telemetry"] = dom_res
        report["checks"]["atomic_dom_snapshot_executed"] = True

        st = report.get("search_telemetry", {})
        ft = st.get("forensic_transition", {})
        surface_query_matches = bool(ft.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"))
        readiness = str(st.get("SEARCH_RESULT_READINESS"))
        mounted_count = dom_res.get("mounted_row_count", 0)

        surface_correlated = evaluate_search_surface_current_query_correlation(
            search_view_connected=bool(dom_res.get("search_view_connected")),
            surface_query_matches=surface_query_matches,
            search_readiness=readiness,
        )
        report["search_surface_current_query_correlated"] = surface_correlated

        target_in_backend_top5 = False
        if raw_results:
            for item in raw_results:
                cid = getattr(item, "channel_id", None) or (item.get("channel_id") if isinstance(item, dict) else None)
                mts = getattr(item, "message_ts", None) or (item.get("message_ts") if isinstance(item, dict) else None)
                tts = getattr(item, "thread_ts", None) or (item.get("thread_ts") if isinstance(item, dict) else None)
                if (
                    cid == TARGET_CHANNEL_ID
                    and normalize_ts(str(mts)) == normalize_ts(TARGET_MESSAGE_TS)
                    and normalize_ts(str(tts or "")) == normalize_ts(TARGET_THREAD_TS)
                ):
                    target_in_backend_top5 = True
                    break

        target_matches = dom_res.get("target_match_count", 0)
        target_connected = bool(dom_res.get("target_connected"))
        if target_matches == 1:
            target_membership = evaluate_row_current_query_membership(
                row_connected=target_connected,
                surface_correlated=surface_correlated,
                is_in_backend_top5=target_in_backend_top5,
            )
        else:
            target_membership = "NOT_APPLICABLE" if target_matches == 0 else "AMBIGUOUS_DUPLICATE"

        report["target_row_current_query_membership"] = target_membership

        if "mounted_row_records" in dom_res and isinstance(dom_res["mounted_row_records"], list):
            for row_rec in dom_res["mounted_row_records"]:
                r_pos = row_rec.get("mounted_dom_position", 0)
                r_conn = bool(row_rec.get("connected"))
                r_in_b5 = (r_pos <= (len(raw_results) if raw_results else 0))
                row_rec["row_current_query_membership"] = evaluate_row_current_query_membership(
                    row_connected=r_conn,
                    surface_correlated=surface_correlated,
                    is_in_backend_top5=r_in_b5,
                )

        classification = classify_mounted_dom_residency(dom_res)
        report["residency_classification"] = classification
        report["diagnostic_verdict"] = classification
        report["verdict"] = "DIAGNOSTIC_COMPLETED" if classification != "FAIL_CLOSED" else "FAIL_CLOSED"

        report["live_execution_completed"] = True
        report["harness_phases"].append("DOM_SNAPSHOT_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)
        persist_live_artifact(report)
        return report

    except Exception as e:
        logger.error(f"🛑 Exception during MDR1 live diagnostic: {e}", exc_info=True)
        report["error"] = str(e)
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "EXECUTION_EXCEPTION"
        report["duration_seconds"] = round(time.time() - start_time, 2)
        persist_live_artifact(report)
        return report

    finally:
        if host is not None:
            try:
                logger.info("🛑 [CLEANUP] Destroying PesuWebViewHost...")
                host.destroy()
            except Exception as de:
                logger.warning(f"⚠️ Exception destroying host: {de}")


def persist_live_artifact(report: dict[str, Any]) -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LIVE_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Persisted authoritative live artifact to {LIVE_OUTPUT_FILE}")
    except Exception as pe:
        logger.error(f"🛑 Failed to persist authoritative live artifact: {pe}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 13.5D / Task 11 / P2A / MDR1 Mounted DOM Residency Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline preflight checks without live actions")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds for live runtime")
    args = parser.parse_args()

    report = run_mdr1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print(f"VERDICT: {report.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT: {report.get('diagnostic_verdict')}")
    print(f"RESIDENCY_CLASSIFICATION: {report.get('residency_classification')}")

    if report.get("verdict") in ("DRY_RUN_PASS", "DIAGNOSTIC_COMPLETED"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
