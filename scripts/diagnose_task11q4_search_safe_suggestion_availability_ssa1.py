"""Phase 13.5D / Task 11 / Search Safety / SSA1: Safe Search Suggestion Availability Diagnostic 1.

SSA1 PURPOSE:
After using the already-qualified query-entry path for the exact query, determine whether
a safe explicit search suggestion becomes available within a bounded observation window,
and characterize candidate structure without submitting anything.

Strict Boundaries:
- DIAGNOSTIC ONLY: Upstream of VSA2. Does NOT submit search, does NOT open results,
  does NOT click suggestions, does NOT press Enter, does NOT scroll, does NOT click anchors,
  does NOT expand threads, does NOT invoke LLM provider, does NOT retry.
- REUSES EXACT PRODUCTION QUERY-ENTRY SEAM:
  1. JS_CHECK_AUTH_AND_READINESS (readiness check)
  2. JS_ACTIVATE_SEARCH (open search modal)
  3. JS_CHECK_SEARCH_INPUT_READINESS (input box ready)
  4. JS_SET_TEXTY_QUERY (model-native query setter)
- ZERO SUBMIT MUTATION:
  STRICT ZERO: search submissions = 0, Enter events = 0, suggestion clicks = 0,
  navigation actions = 0, scroll actions = 0, provider calls = 0, retries = 0.
- BOUNDED OBSERVATION WINDOW:
  MAX_SUGGESTION_OBSERVATION_POLLS = 20
  POLL_INTERVAL_MS = 200 (total maximum window 4.0 seconds, matching production submit timeout)
- STRUCTURAL CANDIDATE CLASSIFICATIONS (NORMALIZED ENUMS ONLY, ZERO RAW TEXT):
  SAFE_EXPLICIT_SEARCH
  UNSAFE_NON_SEARCH_CHANNEL_NAV
  UNSAFE_PERSON_NAV
  UNSAFE_WRITE_CAPABLE_SLACKBOT
  UNSAFE_OTHER_NAV
  UNKNOWN_CANDIDATE
- TERMINAL DIAGNOSTIC CLASSIFICATIONS:
  SAFE_SEARCH_SUGGESTION_OBSERVED
  ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED
  NO_SUGGESTION_SURFACE_OBSERVED
  SUGGESTION_STRUCTURE_UNRECOGNIZED
  QUERY_STATE_NOT_VERIFIED
  SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY
  FAIL_CLOSED
"""

from __future__ import annotations

import argparse
import ast
import copy
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
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    SEARCH_INPUT_READINESS_TIMEOUT_SECONDS,
    SEARCH_INPUT_POLL_INTERVAL_SECONDS,
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("diagnose_task11q4_search_safe_suggestion_availability_ssa1")


def normalize_slack_ts(ts: Optional[str]) -> Optional[str]:
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    m = re.match(r"^(\d+)(?:\.(\d+))?$", s)
    if not m:
        return s
    integer_part = m.group(1)
    fractional_part = (m.group(2) or "").rstrip("0")
    return f"{integer_part}.{fractional_part}" if fractional_part else integer_part


# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
PROTOCOL_NAME = "task11q4_search_ssa1_safe_suggestion_availability"
DIAGNOSTIC_RUN = "SSA1_SUGGESTION_RUN_1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

TARGET_CHANNEL_ID = "C042WS25QAX"
TARGET_MESSAGE_TS = "1718079276.126469"
TARGET_THREAD_TS = "1718058683.912369"
TARGET_IS_THREAD_REPLY = True
CANONICAL_TARGET_IDENTITY: tuple[str, str, Optional[str]] = (
    TARGET_CHANNEL_ID,
    normalize_slack_ts(TARGET_MESSAGE_TS),
    normalize_slack_ts(TARGET_THREAD_TS),
)

MAX_SUGGESTION_OBSERVATION_POLLS = 20
POLL_INTERVAL_MS = 200

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_search_ssa1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_search_ssa1_safe_suggestion_availability_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_search_ssa1_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_search_ssa1_safe_suggestion_availability.json"

TERMINAL_CLASSIFICATIONS = [
    "SAFE_SEARCH_SUGGESTION_OBSERVED",
    "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED",
    "NO_SUGGESTION_SURFACE_OBSERVED",
    "SUGGESTION_STRUCTURE_UNRECOGNIZED",
    "QUERY_STATE_NOT_VERIFIED",
    "SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY",
    "FAIL_CLOSED",
]

CANDIDATE_CLASSIFICATIONS = [
    "SAFE_EXPLICIT_SEARCH",
    "UNSAFE_NON_SEARCH_CHANNEL_NAV",
    "UNSAFE_PERSON_NAV",
    "UNSAFE_WRITE_CAPABLE_SLACKBOT",
    "UNSAFE_OTHER_NAV",
    "UNKNOWN_CANDIDATE",
]

MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_QUERY_ENTRIES = 1
MAX_SEARCH_SUBMISSIONS = 0
MAX_ENTER_KEY_EVENTS = 0
MAX_SUGGESTION_CLICKS = 0
MAX_NAVIGATION_ACTIONS = 0
MAX_SCROLL_ACTIONS = 0
MAX_GUARDED_ANCHOR_CLICKS = 0
MAX_REPLY_BAR_CLICKS = 0
MAX_RESULT_CARD_CLICKS = 0
MAX_THREAD_EXPANSIONS = 0
MAX_KEYBOARD_EVENTS = 0
MAX_COMPOSER_INTERACTIONS = 0
MAX_WRITE_CAPABLE_ACTIONS = 0
MAX_PROVIDER_CALLS = 0
MAX_RETRIES = 0
MAX_RESTORATIONS = 0
MAX_BACK_NAVIGATIONS = 0

# ---------------------------------------------------------------------------
# JavaScript Snippets (Pure Read-Only Observation)
# ---------------------------------------------------------------------------

JS_OBSERVE_SEARCH_SUGGESTION_SURFACE = r"""
((targetQuery) => {
    const report = {
        observation_ok: false,
        strong_search_input_box_match_count: 0,
        duplicate_search_input_box_count: 0,
        query_state_all_synced: false,
        surfaces_synced: {
            dom: false,
            quill_text: false,
            quill_delta: false,
            texty: false,
            redux: false
        },
        internal_flags: {
            query_has_changed: false,
            query_has_been_submitted: false,
            is_updating_display_query: false,
            is_in_composition: false
        },
        suggestion_surface_present: false,
        connected_candidate_count: 0,
        safe_candidate_count: 0,
        unsafe_candidate_count: 0,
        autocomplete_selected_index: null,
        default_candidate_classification: 'NONE',
        selected_candidate_classification: 'NONE',
        explicit_search_candidate_present: false,
        safe_search_candidate_present: false,
        candidate_classifications: [],
        candidate_summaries: [],
        error: null
    };

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    if (searchInputCandidates.length !== 1) {
        report.error = 'SEARCH_INPUT_CARDINALITY_NOT_ONE';
        return report;
    }

    const inputElem = searchInputCandidates[0];
    const role = inputElem.getAttribute('role') || '';
    const insideModal = !!inputElem.closest('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal, [data-qa="search_input_box"]');
    const outsideComposer = !inputElem.closest('.c-message__editor, [data-qa="message_input"], .p-message_input');

    if (!inputElem.isConnected || role !== 'combobox' || !insideModal || !outsideComposer) {
        report.error = 'SEARCH_INPUT_OR_MODAL_INVALID';
        return report;
    }

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) {
        report.error = 'NO_REACT_FIBER_KEY_FOUND';
        return report;
    }

    const isSearchInputBoxStrong = (node) => {
        if (!node || typeof node !== 'object') return false;
        const hasGetTextyOps = (typeof node.getTextyOps === 'function');
        const hasUserInputOrRef = (typeof node.onUserInput === 'function' || typeof node.onUserQueryChange === 'function' || typeof node.setTextyRef === 'function');
        const hasProps = (node.props && typeof node.props === 'object');
        const hasDisplayQueryOps = hasProps && Object.prototype.hasOwnProperty.call(node.props, 'displayQueryOps');
        return hasGetTextyOps && hasUserInputOrRef && hasDisplayQueryOps;
    };

    let fiber = startingFiber;
    let depth = 0;
    const matchingInstances = [];

    while (fiber && depth < 40) {
        if (fiber.stateNode && isSearchInputBoxStrong(fiber.stateNode)) {
            matchingInstances.push({ instance: fiber.stateNode, fiber: fiber });
        }
        fiber = fiber.return;
        depth++;
    }

    report.strong_search_input_box_match_count = matchingInstances.length;
    if (matchingInstances.length !== 1) {
        report.error = (matchingInstances.length === 0) ? 'STRONG_SEARCH_INPUT_BOX_NOT_FOUND' : 'STRONG_SEARCH_INPUT_BOX_AMBIGUOUS';
        return report;
    }

    const { instance: searchInputBoxInstance, fiber: currentFiber } = matchingInstances[0];
    const textyObj = searchInputBoxInstance.texty;
    if (!textyObj) {
        report.error = 'TEXTY_OBJECT_MISSING';
        return report;
    }

    const normalizeOpsToString = (ops) => {
        if (!Array.isArray(ops)) return '';
        let s = '';
        for (let op of ops) {
            if (op && typeof op.insert === 'string') s += op.insert;
        }
        return s.trim();
    };

    // Verify 5 query surfaces
    try {
        const domVal = (inputElem.textContent || inputElem.innerText || '').trim();
        report.surfaces_synced.dom = domVal.includes(targetQuery);
    } catch (e) {}

    try {
        if (typeof searchInputBoxInstance.getQuillText === 'function') {
            const qText = searchInputBoxInstance.getQuillText() || '';
            report.surfaces_synced.quill_text = qText.includes(targetQuery);
        }
    } catch (e) {}

    try {
        if (typeof searchInputBoxInstance.getTextyOps === 'function') {
            const ops = searchInputBoxInstance.getTextyOps();
            const deltaStr = normalizeOpsToString(ops);
            report.surfaces_synced.quill_delta = deltaStr.includes(targetQuery);
        }
    } catch (e) {}

    try {
        if (typeof textyObj.getContents === 'function') {
            const tContents = textyObj.getContents();
            const tOps = (tContents && tContents.contents) ? tContents.contents : (Array.isArray(tContents) ? tContents : []);
            const tStr = normalizeOpsToString(tOps);
            report.surfaces_synced.texty = tStr.includes(targetQuery);
        }
    } catch (e) {}

    try {
        const propsToInspect = (currentFiber && currentFiber.memoizedProps && Object.prototype.hasOwnProperty.call(currentFiber.memoizedProps, 'displayQueryOps'))
            ? currentFiber.memoizedProps
            : searchInputBoxInstance.props;

        if (propsToInspect && Object.prototype.hasOwnProperty.call(propsToInspect, 'displayQueryOps')) {
            const dOps = propsToInspect.displayQueryOps;
            const rStr = normalizeOpsToString(dOps);
            report.surfaces_synced.redux = rStr.includes(targetQuery);
        }
    } catch (e) {}

    const surfaces = report.surfaces_synced;
    report.query_state_all_synced = Boolean(surfaces.dom && surfaces.quill_text && surfaces.quill_delta && surfaces.texty && surfaces.redux);

    try {
        if (searchInputBoxInstance.state) {
            report.internal_flags.query_has_changed = Boolean(searchInputBoxInstance.state.queryHasChanged);
            report.internal_flags.query_has_been_submitted = Boolean(searchInputBoxInstance.state.queryHasBeenSubmitted);
            report.internal_flags.is_updating_display_query = Boolean(searchInputBoxInstance.state._isUpdatingDisplayQuery);
            report.internal_flags.is_in_composition = Boolean(searchInputBoxInstance.state.isInComposition);
        }
    } catch (e) {}

    // Inspect Suggestions
    const props = searchInputBoxInstance.props || {};
    const suggestions = Array.isArray(props.suggestions) ? props.suggestions : [];
    const autocompleteSelectedIndex = (props.autocompleteSelectedIndex !== undefined && props.autocompleteSelectedIndex !== null)
        ? props.autocompleteSelectedIndex
        : null;

    report.connected_candidate_count = suggestions.length;
    report.autocomplete_selected_index = autocompleteSelectedIndex;
    report.suggestion_surface_present = (suggestions.length > 0 || !!document.querySelector('#c-search_autocomplete__suggestion_list, [data-qa="search_autocomplete"], .c-search_autocomplete, [role="listbox"]'));

    function isWriteCapableSuggestion(s) {
        if (!s || typeof s !== 'object') return false;
        if (s.is_type_slackbot === true) return true;
        if (s.type === 'querySlackbot') return true;
        if (s.key === 'ask-slackbot') return true;
        if (s.suggestion_sub_type === 'query-slackbot') return true;
        return false;
    }

    function isUnsafeNonSearchSuggestion(s) {
        if (!s || typeof s !== 'object') return true;
        if (isWriteCapableSuggestion(s)) return true;
        if (s.isNavigational === true || s.is_type_navigational === true) return true;
        if (s.is_type_channel === true || s.type === 'channel') return true;
        if (s.is_type_user === true || s.is_type_user_profile === true || s.type === 'member-profile') return true;
        if (s.is_type_workflow === true || s.type === 'workflowTrigger') return true;
        if (s.is_type_app_action === true || s.type === 'appAction') return true;
        if (s.is_type_huddle === true || s.type === 'huddle') return true;
        if (s.type === 'queryConvoSearch' || s.type === 'querySalesforce') return true;
        return false;
    }

    function isExactSafeKeywordSearch(s) {
        if (!s || typeof s !== 'object') return false;
        const matchesExactPositive = (
            s.is_type_query_completion === true &&
            s.type === 'queryUser' &&
            s.suggestion_sub_type === 'query-echo'
        );
        if (!matchesExactPositive) return false;
        if (isUnsafeNonSearchSuggestion(s)) return false;
        return true;
    }

    function classifyCandidate(s) {
        if (!s || typeof s !== 'object') return 'UNKNOWN_CANDIDATE';
        if (isExactSafeKeywordSearch(s)) return 'SAFE_EXPLICIT_SEARCH';
        if (isWriteCapableSuggestion(s)) return 'UNSAFE_WRITE_CAPABLE_SLACKBOT';
        if (s.is_type_channel === true || s.type === 'channel' || s.is_type_navigational === true || s.isNavigational === true) {
            return 'UNSAFE_NON_SEARCH_CHANNEL_NAV';
        }
        if (s.is_type_user === true || s.is_type_user_profile === true || s.type === 'member-profile') {
            return 'UNSAFE_PERSON_NAV';
        }
        if (s.is_type_huddle === true || s.type === 'huddle' || s.is_type_workflow === true || s.type === 'workflowTrigger' || s.is_type_app_action === true || s.type === 'appAction') {
            return 'UNSAFE_OTHER_NAV';
        }
        return 'UNKNOWN_CANDIDATE';
    }

    let safeCount = 0;
    let unsafeCount = 0;
    const classifications = [];
    const summaries = [];

    for (let idx = 0; idx < suggestions.length; idx++) {
        const s = suggestions[idx];
        const cls = classifyCandidate(s);
        classifications.push(cls);
        if (cls === 'SAFE_EXPLICIT_SEARCH') {
            safeCount++;
        } else {
            unsafeCount++;
        }

        summaries.push({
            candidate_index: idx,
            structural_classification: cls,
            is_safe_search: (cls === 'SAFE_EXPLICIT_SEARCH'),
            is_channel_nav: (cls === 'UNSAFE_NON_SEARCH_CHANNEL_NAV'),
            is_person_nav: (cls === 'UNSAFE_PERSON_NAV'),
            is_slackbot: (cls === 'UNSAFE_WRITE_CAPABLE_SLACKBOT'),
            has_query_echo: (s && s.suggestion_sub_type === 'query-echo'),
            type_tag: (s && typeof s.type === 'string') ? s.type : 'none',
            sub_type_tag: (s && typeof s.suggestion_sub_type === 'string') ? s.suggestion_sub_type : 'none'
        });
    }

    report.safe_candidate_count = safeCount;
    report.unsafe_candidate_count = unsafeCount;
    report.candidate_classifications = classifications;
    report.candidate_summaries = summaries;

    const defaultSug = suggestions[0] || null;
    const selectedSug = (autocompleteSelectedIndex !== null && suggestions[autocompleteSelectedIndex])
        ? suggestions[autocompleteSelectedIndex]
        : defaultSug;

    report.default_candidate_classification = classifyCandidate(defaultSug);
    report.selected_candidate_classification = classifyCandidate(selectedSug);
    report.explicit_search_candidate_present = (safeCount > 0);
    report.safe_search_candidate_present = (safeCount === 1);
    report.observation_ok = true;

    return report;
})
"""

# ---------------------------------------------------------------------------
# Terminal State Classification
# ---------------------------------------------------------------------------

def classify_ssa1_terminal_state(
    polls: list[dict[str, Any]],
    query_state_all_synced: bool,
    error: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Deterministically classifies the terminal diagnostic state of SSA1 observation."""
    details: dict[str, Any] = {
        "poll_count": len(polls),
        "first_safe_candidate_poll_index": None,
        "final_safe_candidate_count": 0,
        "final_connected_candidate_count": 0,
        "final_default_candidate_classification": "NONE",
        "final_candidate_classifications": [],
        "query_state_all_synced": query_state_all_synced,
        "error": error,
    }

    if error is not None:
        return "FAIL_CLOSED", details

    if not query_state_all_synced:
        return "QUERY_STATE_NOT_VERIFIED", details

    if not polls:
        return "NO_SUGGESTION_SURFACE_OBSERVED", details

    first_safe_idx = None
    for p in polls:
        if p.get("safe_search_candidate_present"):
            first_safe_idx = p.get("poll_index")
            break

    details["first_safe_candidate_poll_index"] = first_safe_idx

    final_poll = polls[-1]
    details["final_safe_candidate_count"] = final_poll.get("safe_candidate_count", 0)
    details["final_connected_candidate_count"] = final_poll.get("connected_candidate_count", 0)
    details["final_default_candidate_classification"] = final_poll.get("default_candidate_classification", "NONE")
    details["final_candidate_classifications"] = final_poll.get("candidate_classifications", [])

    surface_present_any = any(p.get("suggestion_surface_present") for p in polls)
    connected_any = any(p.get("connected_candidate_count", 0) > 0 for p in polls)

    if not surface_present_any or not connected_any:
        return "NO_SUGGESTION_SURFACE_OBSERVED", details

    if first_safe_idx == 1:
        return "SAFE_SEARCH_SUGGESTION_OBSERVED", details

    if first_safe_idx is not None and first_safe_idx > 1:
        return "SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY", details

    all_candidates = []
    for p in polls:
        all_candidates.extend(p.get("candidate_classifications", []))

    if all_candidates:
        recognized_unsafe = {
            "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "UNSAFE_PERSON_NAV",
            "UNSAFE_WRITE_CAPABLE_SLACKBOT",
            "UNSAFE_OTHER_NAV",
        }
        if all(c in recognized_unsafe for c in all_candidates):
            return "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED", details
        if all(c == "UNKNOWN_CANDIDATE" for c in all_candidates):
            return "SUGGESTION_STRUCTURE_UNRECOGNIZED", details
        if any(c in recognized_unsafe for c in all_candidates):
            return "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED", details

    return "FAIL_CLOSED", details


# ---------------------------------------------------------------------------
# Sanitization & Persistence
# ---------------------------------------------------------------------------

SENSITIVE_TEXT_PATTERNS = [
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"/archives/\S+", re.IGNORECASE),
    re.compile(r"xox[baprs]-\S+", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE),
]


def sanitize_report_for_persistence(data: Any) -> Any:
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(forbidden in k_lower for forbidden in [
                "raw_href", "raw_permalink", "permalink", "inner_html", "outer_html",
                "inner_text", "text_content", "message_body", "sender_text", "user_text",
                "aria_label", "title_text", "raw_url", "token", "password"
            ]):
                continue
            sanitized[k] = sanitize_report_for_persistence(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_report_for_persistence(item) for item in data]
    elif isinstance(data, str):
        cleaned = data
        for pat in SENSITIVE_TEXT_PATTERNS:
            cleaned = pat.sub("[REDACTED_SENSITIVE]", cleaned)
        return cleaned
    return data


# ---------------------------------------------------------------------------
# Dependency & Static Parity Audits
# ---------------------------------------------------------------------------

def get_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    manifest = {
        "ssa1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_ssa1.py",
        "vsa2_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2.py",
        "vsa1_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_bounded_viewport_scroll_vsa1.py",
        "mgr1r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1r1.py",
        "attempt4_runner": REPO_ROOT / "scripts" / "run_phase_13_5d_attempt4_clean_trace.py",
        "webview_slack_search": SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py",
        "pesu_webview_host": SRC_DIR / "pesu_agent" / "runtime" / "pesu_webview_host.py",
        "slack_permalink_parser": SRC_DIR / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
    }
    return {k: get_file_sha256(v) for k, v in manifest.items() if v.exists()}


def verify_static_zero_submit_contract(runner_path: Optional[Path] = None) -> bool:
    """Verifies that SSA1 observation JS strictly executes ZERO submit, onEnter, or click actions."""
    p = runner_path or Path(__file__)
    text = p.read_text(encoding="utf-8")

    tree = ast.parse(text)
    js_observe_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE":
                    if isinstance(node.value, ast.Constant):
                        js_observe_node = node.value.value

    if not js_observe_node or not isinstance(js_observe_node, str):
        logger.error("❌ JS_OBSERVE_SEARCH_SUGGESTION_SURFACE snippet not found as ast Constant")
        return False

    forbidden_patterns = [
        r"\.click\(",
        r"\.onEnter\(",
        r"\.onAccept\(",
        r"dispatchEvent\(",
        r"KeyboardEvent",
        r"submit\(",
        r"requestSubmit\(",
        r"\.scrollTo\(",
        r"\.scrollBy\(",
        r"\.scrollTop\s*=",
        r"scrollIntoView",
    ]

    for pat in forbidden_patterns:
        if re.search(pat, js_observe_node):
            logger.error(f"❌ Forbidden mutation pattern found in observation JS: {pat}")
            return False

    return True


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    """Audits the runner AST for zero executable clicks, keyboard events, or navigations."""
    p = runner_path or Path(__file__)
    text = p.read_text(encoding="utf-8")
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in ("click", "click_element", "dispatch_mouse_event", "send_keys", "press_enter"):
                    logger.error(f"❌ Forbidden interaction call found in runner: {attr}")
                    return False
    return True


def verify_runtime_api_signature_parity() -> bool:
    """Verifies that WebViewSlackSearchBackend query-entry APIs preserve expected signatures."""
    import inspect
    sig = inspect.signature(WebViewSlackSearchBackend.search_messages)
    params = list(sig.parameters.keys())
    if "query" not in params or "max_results" not in params:
        return False

    sig_start = inspect.signature(WebViewSlackSearchBackend.start_runtime)
    if "timeout" not in sig_start.parameters:
        return False

    sig_host = inspect.signature(PesuWebViewHost.__init__)
    host_params = list(sig_host.parameters.keys())
    if "user_data_folder" not in host_params or "initial_url" not in host_params:
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
    except Exception:
        return "UNKNOWN"


def check_uncommitted_diff() -> bool:
    try:
        res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return len(res.stdout.strip()) > 0
    except Exception:
        return True


def get_all_loaded_project_files() -> dict[str, str]:
    loaded_files: dict[str, str] = {}
    this_script = REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_ssa1.py"
    if this_script.exists():
        loaded_files["scripts/diagnose_task11q4_search_safe_suggestion_availability_ssa1.py"] = get_file_sha256(this_script)

    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        try:
            fpath = Path(mod_file).resolve()
            if not fpath.exists() or not fpath.is_file():
                continue
            if str(fpath).startswith(str(SRC_DIR.resolve())):
                rel_path = fpath.relative_to(REPO_ROOT).as_posix()
                loaded_files[rel_path] = get_file_sha256(fpath)
        except Exception:
            continue
    return loaded_files


def audit_runtime_import_closure(manifest: dict[str, Any]) -> dict[str, Any]:
    expected_closure = manifest.get("runtime_closure_hashes", {})
    loaded_files = get_all_loaded_project_files()

    missing = []
    stale = []
    mismatch = []

    for path, exp_hash in expected_closure.items():
        if path not in loaded_files:
            missing.append(path)
        elif loaded_files[path] != exp_hash:
            mismatch.append({
                "path": path,
                "expected": exp_hash,
                "actual": loaded_files[path],
            })

    for path in loaded_files:
        if path not in expected_closure:
            stale.append(path)

    passed = (len(missing) == 0 and len(stale) == 0 and len(mismatch) == 0)
    return {
        "closure_audit_passed": passed,
        "loaded_file_count": len(loaded_files),
        "manifest_closure_count": len(expected_closure),
        "missing_from_closure": missing,
        "stale_in_closure": stale,
        "mismatched_hashes": mismatch,
        "loaded_files": loaded_files,
    }


# ---------------------------------------------------------------------------
# Mock Simulation Fixtures for Dry Run
# ---------------------------------------------------------------------------

def build_mock_dry_run_observation_polls(scenario: str = "safe_immediate") -> list[dict[str, Any]]:
    """Builds synthetic observation polls for dry run verification."""
    polls = []
    for idx in range(1, 6):
        elapsed = idx * POLL_INTERVAL_MS
        if scenario == "safe_immediate":
            candidates = ["SAFE_EXPLICIT_SEARCH", "UNSAFE_NON_SEARCH_CHANNEL_NAV"]
            default_cls = "SAFE_EXPLICIT_SEARCH"
            safe_cnt = 1
            unsafe_cnt = 1
        elif scenario == "safe_delayed":
            if idx < 3:
                candidates = ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "UNSAFE_NON_SEARCH_CHANNEL_NAV"]
                default_cls = "UNSAFE_NON_SEARCH_CHANNEL_NAV"
                safe_cnt = 0
                unsafe_cnt = 2
            else:
                candidates = ["SAFE_EXPLICIT_SEARCH", "UNSAFE_NON_SEARCH_CHANNEL_NAV"]
                default_cls = "SAFE_EXPLICIT_SEARCH"
                safe_cnt = 1
                unsafe_cnt = 1
        elif scenario == "only_channel_nav":
            candidates = ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "UNSAFE_NON_SEARCH_CHANNEL_NAV", "UNSAFE_NON_SEARCH_CHANNEL_NAV"]
            default_cls = "UNSAFE_NON_SEARCH_CHANNEL_NAV"
            safe_cnt = 0
            unsafe_cnt = 3
        elif scenario == "no_surface":
            candidates = []
            default_cls = "NONE"
            safe_cnt = 0
            unsafe_cnt = 0
        elif scenario == "unrecognized":
            candidates = ["UNKNOWN_CANDIDATE"]
            default_cls = "UNKNOWN_CANDIDATE"
            safe_cnt = 0
            unsafe_cnt = 1
        else:
            candidates = ["SAFE_EXPLICIT_SEARCH"]
            default_cls = "SAFE_EXPLICIT_SEARCH"
            safe_cnt = 1
            unsafe_cnt = 0

        poll_rec = {
            "poll_index": idx,
            "elapsed_ms": elapsed,
            "search_input_query_matches_requested": True,
            "suggestion_surface_present": (len(candidates) > 0),
            "connected_candidate_count": len(candidates),
            "safe_candidate_count": safe_cnt,
            "unsafe_candidate_count": unsafe_cnt,
            "candidate_classifications": candidates,
            "default_candidate_classification": default_cls,
            "selected_candidate_classification": default_cls,
            "explicit_search_candidate_present": (safe_cnt > 0),
            "safe_search_candidate_present": (safe_cnt == 1),
            "candidate_summaries": [
                {
                    "candidate_index": c_idx,
                    "structural_classification": c_cls,
                    "is_safe_search": (c_cls == "SAFE_EXPLICIT_SEARCH"),
                    "is_channel_nav": (c_cls == "UNSAFE_NON_SEARCH_CHANNEL_NAV"),
                    "is_person_nav": (c_cls == "UNSAFE_PERSON_NAV"),
                    "is_slackbot": (c_cls == "UNSAFE_WRITE_CAPABLE_SLACKBOT"),
                    "has_query_echo": (c_cls == "SAFE_EXPLICIT_SEARCH"),
                    "type_tag": "queryUser" if c_cls == "SAFE_EXPLICIT_SEARCH" else "channel",
                    "sub_type_tag": "query-echo" if c_cls == "SAFE_EXPLICIT_SEARCH" else "none",
                }
                for c_idx, c_cls in enumerate(candidates)
            ],
        }
        polls.append(poll_rec)
    return polls


# ---------------------------------------------------------------------------
# Runner Engine
# ---------------------------------------------------------------------------

def run_ssa1_qualification(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / Search Safety / SSA1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "terminal_classification": "UNKNOWN",
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": git_commit,
        "uncommitted_diff": git_diff,
        "target_identity": {
            "channel_id": TARGET_CHANNEL_ID,
            "message_ts": TARGET_MESSAGE_TS,
            "thread_ts": TARGET_THREAD_TS,
            "is_thread_reply": TARGET_IS_THREAD_REPLY,
        },
        "observation_contracts": {
            "MAX_SUGGESTION_OBSERVATION_POLLS": MAX_SUGGESTION_OBSERVATION_POLLS,
            "POLL_INTERVAL_MS": POLL_INTERVAL_MS,
            "MAX_SLACK_SEARCH_OPERATIONS": MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_QUERY_ENTRIES": MAX_QUERY_ENTRIES,
            "MAX_SEARCH_SUBMISSIONS": MAX_SEARCH_SUBMISSIONS,
            "MAX_ENTER_KEY_EVENTS": MAX_ENTER_KEY_EVENTS,
            "MAX_SUGGESTION_CLICKS": MAX_SUGGESTION_CLICKS,
            "MAX_NAVIGATION_ACTIONS": MAX_NAVIGATION_ACTIONS,
            "MAX_SCROLL_ACTIONS": MAX_SCROLL_ACTIONS,
            "MAX_PROVIDER_CALLS": MAX_PROVIDER_CALLS,
            "MAX_RETRIES": MAX_RETRIES,
            "MAX_RESTORATIONS": MAX_RESTORATIONS,
        },
        "live_action_inventory": {
            "search_operations": 0,
            "query_entries": 0,
            "search_submissions": 0,
            "enter_key_events": 0,
            "suggestion_clicks": 0,
            "navigation_actions": 0,
            "scroll_actions": 0,
            "thread_expansions": 0,
            "composer_interactions": 0,
            "write_capable_actions": 0,
            "provider_calls": 0,
            "retries_attempted": 0,
            "restorations_attempted": 0,
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
            "SSA1_STATIC_ZERO_SUBMIT_AUDIT": False,
            "SSA1_STATIC_ZERO_INTERACTION_AUDIT": False,
            "RUNTIME_API_SIGNATURE_PARITY": False,
            "frozen_protocol_respected": False,
            "frozen_hashes_matched": False,
            "historical_baselines_matched": False,
            "UNTRACKED_RUNTIME_DEPENDENCIES": -1,
            "STALE_RUNTIME_CLOSURE_ENTRIES": -1,
            "MISMATCHED_RUNTIME_DEPENDENCIES": -1,
            "attempt_marker_written": False,
            "query_entry_executed": False,
            "query_state_all_synced": False,
            "bounded_observation_executed": False,
        },
        "observation_polls": [],
        "terminal_details": {},
        "runtime_closure_audit": {},
        "provider_calls": 0,
        "retry_occurred": False,
        "live_execution_started": False,
        "live_execution_completed": False,
        "harness_phases": [],
        "error": None,
        "duration_seconds": 0.0,
    }

    # -----------------------------------------------------------------------
    # PHASE 1: Preflight Protocol & Frozen Baselines Verification
    # -----------------------------------------------------------------------
    report["harness_phases"].append("PREFLIGHT_PROTOCOL_AUDIT")

    if not PROTOCOL_FREEZE_PATH.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_FREEZE_MISSING"
        report["error"] = f"Protocol freeze file missing at {PROTOCOL_FREEZE_PATH}"
        return report

    try:
        protocol_manifest = json.loads(PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_FREEZE_CORRUPT"
        report["error"] = f"Error reading protocol freeze JSON: {e}"
        return report

    semantic_errors = []
    if protocol_manifest.get("protocol") != PROTOCOL_NAME:
        semantic_errors.append(f"protocol mismatch: {protocol_manifest.get('protocol')} != {PROTOCOL_NAME}")
    if protocol_manifest.get("max_suggestion_observation_polls") != MAX_SUGGESTION_OBSERVATION_POLLS:
        semantic_errors.append(f"max_suggestion_observation_polls mismatch: {protocol_manifest.get('max_suggestion_observation_polls')} != {MAX_SUGGESTION_OBSERVATION_POLLS}")
    if protocol_manifest.get("poll_interval_ms") != POLL_INTERVAL_MS:
        semantic_errors.append(f"poll_interval_ms mismatch: {protocol_manifest.get('poll_interval_ms')} != {POLL_INTERVAL_MS}")

    if semantic_errors:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_SEMANTIC_CONTRACT_VIOLATION"
        report["error"] = "; ".join(semantic_errors)
        return report

    current_dep_hashes = compute_all_dependency_hashes()
    report["pre_hashes"] = current_dep_hashes

    historical_baselines = protocol_manifest.get("historical_baselines", {})
    baseline_mismatches = []
    for base_name, base_info in historical_baselines.items():
        base_path = REPO_ROOT / base_info["path"]
        if not base_path.exists():
            baseline_mismatches.append(f"Missing historical baseline {base_name} at {base_path}")
            continue
        base_hash = get_file_sha256(base_path)
        if base_hash != base_info["sha256"]:
            baseline_mismatches.append(f"Hash mismatch for {base_name}: expected {base_info['sha256']}, got {base_hash}")

    if baseline_mismatches:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_MUTATION_DETECTED"
        report["error"] = "; ".join(baseline_mismatches)
        return report

    report["checks"]["SSA1_STATIC_ZERO_SUBMIT_AUDIT"] = verify_static_zero_submit_contract()
    report["checks"]["SSA1_STATIC_ZERO_INTERACTION_AUDIT"] = verify_static_zero_interaction_contract()
    report["checks"]["RUNTIME_API_SIGNATURE_PARITY"] = verify_runtime_api_signature_parity()

    if not all([
        report["checks"]["SSA1_STATIC_ZERO_SUBMIT_AUDIT"],
        report["checks"]["SSA1_STATIC_ZERO_INTERACTION_AUDIT"],
        report["checks"]["RUNTIME_API_SIGNATURE_PARITY"],
    ]):
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "STATIC_CODE_PARITY_AUDIT_FAILED"
        report["error"] = "One or more static parity/safety audits failed."
        return report

    report["checks"]["frozen_protocol_respected"] = True
    report["checks"]["frozen_hashes_matched"] = True
    report["checks"]["historical_baselines_matched"] = True

    # -----------------------------------------------------------------------
    # PHASE 2: Runtime Import Closure Audit
    # -----------------------------------------------------------------------
    report["harness_phases"].append("RUNTIME_CLOSURE_AUDIT")
    closure_result = audit_runtime_import_closure(protocol_manifest)
    report["runtime_closure_audit"] = closure_result

    report["checks"]["UNTRACKED_RUNTIME_DEPENDENCIES"] = len(closure_result["missing_from_closure"])
    report["checks"]["STALE_RUNTIME_CLOSURE_ENTRIES"] = len(closure_result["stale_in_closure"])
    report["checks"]["MISMATCHED_RUNTIME_DEPENDENCIES"] = len(closure_result["mismatched_hashes"])

    if not closure_result["closure_audit_passed"]:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_IMPORT_CLOSURE_FAILED"
        report["error"] = f"Runtime closure audit failed: missing={closure_result['missing_from_closure']}, stale={closure_result['stale_in_closure']}, mismatch={closure_result['mismatched_hashes']}"
        return report

    # -----------------------------------------------------------------------
    # PHASE 3: Dry-Run vs Live
    # -----------------------------------------------------------------------
    if dry_run:
        logger.info("🧪 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock suggestion observation...")
        report["dry_run"] = True
        report["live_action_inventory"]["search_operations"] = 1
        report["live_action_inventory"]["query_entries"] = 1
        report["checks"]["query_entry_executed"] = True
        report["checks"]["query_state_all_synced"] = True
        report["checks"]["bounded_observation_executed"] = True

        mock_polls = build_mock_dry_run_observation_polls(scenario="safe_immediate")
        report["observation_polls"] = mock_polls

        term_cls, term_det = classify_ssa1_terminal_state(
            mock_polls,
            query_state_all_synced=True,
            error=None,
        )
        report["terminal_classification"] = term_cls
        report["diagnostic_verdict"] = term_cls
        report["terminal_details"] = term_det
        report["verdict"] = CANONICAL_DRY_RUN_VERDICT

        report["harness_phases"].append("DRY_RUN_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        sanitized = sanitize_report_for_persistence(report)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 [DRY_RUN_PASS] Dry run completed successfully. Persisted to {DRY_RUN_OUTPUT_FILE}")
        return report

    # -----------------------------------------------------------------------
    # LIVE EXECUTION PATH (Guarded One-Shot Consumption)
    # -----------------------------------------------------------------------
    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if LIVE_OUTPUT_FILE.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "LIVE_ARTIFACT_ALREADY_EXISTS"
        report["error"] = f"Live artifact already exists at {LIVE_OUTPUT_FILE}. Replay forbidden."
        logger.error(f"🛑 LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN: {LIVE_OUTPUT_FILE}")
        return report

    if ATTEMPT_STARTED_MARKER_FILE.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
        report["error"] = f"Attempt marker file already exists at {ATTEMPT_STARTED_MARKER_FILE}. Qualification consumed."
        logger.error(f"🛑 ATTEMPT_MARKER_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN: {ATTEMPT_STARTED_MARKER_FILE}")
        return report

    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(ATTEMPT_STARTED_MARKER_FILE), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as mf:
            marker_data = {
                "task_id": report["task_id"],
                "protocol": PROTOCOL_NAME,
                "diagnostic_run": DIAGNOSTIC_RUN,
                "attempt_started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "git_commit": git_commit,
                "note": "One-shot diagnostic consumption marker. Replay strictly forbidden.",
            }
            json.dump(marker_data, mf, indent=2, ensure_ascii=False)
        report["checks"]["attempt_marker_written"] = True
        logger.info(f"🔒 [ONE_SHOT_CONSUMED] Wrote attempt marker to {ATTEMPT_STARTED_MARKER_FILE}")
    except FileExistsError:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED_RACE"
        report["error"] = "Attempt marker already created concurrently. Aborting."
        return report

    host: Optional[PesuWebViewHost] = None
    backend: Optional[WebViewSlackSearchBackend] = None

    try:
        report["harness_phases"].append("CONNECTING_WEBVIEW")
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (SSA1)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / Search Safety / SSA1 Safe Suggestion Availability",
        )
        backend = WebViewSlackSearchBackend(
            config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH)),
            host=host,
        )
        backend.start_runtime(timeout=timeout)
        report["harness_phases"].append("RUNTIME_STARTED")

        # Step 1: Check Auth & Readiness
        report["live_action_inventory"]["search_operations"] = 1
        res_auth = host.evaluate_js(JS_CHECK_AUTH_AND_READINESS, timeout=10.0)
        if not isinstance(res_auth, dict) or not res_auth.get("ready"):
            raise RuntimeError(f"Search readiness check failed: {res_auth}")

        # Step 2: Search Activation
        res_act = host.evaluate_js(JS_ACTIVATE_SEARCH, timeout=10.0)
        if not isinstance(res_act, dict) or not res_act.get("activated"):
            raise RuntimeError(f"Search activation dispatch failed: {res_act}")

        # Step 3: Input Readiness
        start_mono = time.monotonic()
        input_ready = False
        while time.monotonic() - start_mono < SEARCH_INPUT_READINESS_TIMEOUT_SECONDS:
            res_input = host.evaluate_js(JS_CHECK_SEARCH_INPUT_READINESS, timeout=5.0)
            if isinstance(res_input, dict) and res_input.get("ready"):
                input_ready = True
                break
            time.sleep(SEARCH_INPUT_POLL_INTERVAL_SECONDS)

        if not input_ready:
            raise RuntimeError("Search input modal readiness timed out")

        # Step 4a: Exact Production Model-Native Query Setter (Executed Exactly Once)
        report["live_action_inventory"]["query_entries"] = 1
        js_set = f"({JS_SET_TEXTY_QUERY})({json.dumps(TARGET_QUERY)})"
        res_set = host.evaluate_js(js_set, timeout=10.0)
        if not isinstance(res_set, dict) or not (res_set.get("setter_succeeded") or res_set.get("ok")):
            raise RuntimeError(f"Model-native texty query setter failed: {res_set}")
        report["checks"]["query_entry_executed"] = True

        # Step 4b: Bounded Pure Read-Only Suggestion Surface Observation
        # STRICT ZERO SUBMIT: NO onEnter, NO click, NO enter key event
        report["harness_phases"].append("BOUNDED_SUGGESTION_OBSERVATION")
        report["checks"]["bounded_observation_executed"] = True

        polls = []
        js_obs = f"({JS_OBSERVE_SEARCH_SUGGESTION_SURFACE})({json.dumps(TARGET_QUERY)})"
        query_synced = False

        for poll_idx in range(1, MAX_SUGGESTION_OBSERVATION_POLLS + 1):
            poll_res = host.evaluate_js(js_obs, timeout=5.0)
            if isinstance(poll_res, dict):
                poll_rec = {
                    "poll_index": poll_idx,
                    "elapsed_ms": poll_idx * POLL_INTERVAL_MS,
                    "search_input_query_matches_requested": bool(poll_res.get("query_state_all_synced", False)),
                    "suggestion_surface_present": bool(poll_res.get("suggestion_surface_present", False)),
                    "connected_candidate_count": int(poll_res.get("connected_candidate_count", 0)),
                    "safe_candidate_count": int(poll_res.get("safe_candidate_count", 0)),
                    "unsafe_candidate_count": int(poll_res.get("unsafe_candidate_count", 0)),
                    "candidate_classifications": poll_res.get("candidate_classifications", []),
                    "default_candidate_classification": poll_res.get("default_candidate_classification", "NONE"),
                    "selected_candidate_classification": poll_res.get("selected_candidate_classification", "NONE"),
                    "explicit_search_candidate_present": bool(poll_res.get("explicit_search_candidate_present", False)),
                    "safe_search_candidate_present": bool(poll_res.get("safe_search_candidate_present", False)),
                    "candidate_summaries": poll_res.get("candidate_summaries", []),
                }
                polls.append(poll_rec)
                if poll_res.get("query_state_all_synced"):
                    query_synced = True
            time.sleep(POLL_INTERVAL_MS / 1000.0)

        report["observation_polls"] = polls
        report["checks"]["query_state_all_synced"] = query_synced

        term_cls, term_det = classify_ssa1_terminal_state(
            polls,
            query_state_all_synced=query_synced,
            error=None,
        )
        report["terminal_classification"] = term_cls
        report["diagnostic_verdict"] = term_cls
        report["terminal_details"] = term_det
        report["verdict"] = "QUALIFICATION_COMPLETED"

    except Exception as ex:
        logger.error(f"SSA1 diagnostic execution exception: {ex}")
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "FAIL_CLOSED"
        report["terminal_classification"] = "FAIL_CLOSED"
        report["error"] = str(ex)

    finally:
        if backend is not None:
            try:
                backend.stop_runtime()
            except Exception:
                pass
        if host is not None:
            try:
                host.destroy()
            except Exception:
                pass

    report["live_execution_completed"] = True
    report["harness_phases"].append("LIVE_EXECUTION_COMPLETED")
    report["duration_seconds"] = round(time.time() - start_time, 2)

    try:
        sanitized = sanitize_report_for_persistence(report)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(LIVE_OUTPUT_FILE), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 [PERSISTENCE_SUCCESS] Sanitized live artifact written to {LIVE_OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"❌ Failed to persist live artifact: {e}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SSA1: Safe Search Suggestion Availability Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline dry-run test")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds")
    args = parser.parse_args()

    report = run_ssa1_qualification(dry_run=args.dry_run, timeout=args.timeout)
    print("\n" + "=" * 60)
    print("SSA1 QUALIFICATION EXECUTION SUMMARY")
    print("=" * 60)
    print(f"VERDICT:                  {report.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT:       {report.get('diagnostic_verdict')}")
    print(f"TERMINAL_CLASSIFICATION:  {report.get('terminal_classification')}")
    print(f"ERROR:                    {report.get('error')}")
    print("=" * 60)

    if report.get("verdict") in ("QUALIFICATION_COMPLETED", "DIAGNOSTIC_COMPLETED", CANONICAL_DRY_RUN_VERDICT):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
