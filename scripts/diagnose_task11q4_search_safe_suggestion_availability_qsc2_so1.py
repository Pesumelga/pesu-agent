#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PESU Agent - Phase 13.5D / Task 11 / Search Safety / SSA1-R2
Safe Search Suggestion Availability Diagnostic 1 Retry 2 (Zero-Submit, Zero-Click, Zero-Scroll)

Authoritative Protocol:
  TASK_11_Q4_SEARCH_QSC2_SO1_READ_ONLY_OBSERVER

Purpose:
  After using the already-qualified production query-entry primitives for the exact query
  ("26년 하반기 간담회"), determine whether a safe explicit search suggestion becomes available
  within the exact production submit observation window (4000ms deadline, 50ms interval, <= 80 polls),
  and characterize autocomplete candidate structure without submitting anything.

Strict Behavioral Invariants:
  - ZERO search submissions (no onEnter, no enter key events, no submit form dispatch)
  - ZERO suggestion clicks
  - ZERO navigation actions (no channel, user, or permalink navigations)
  - ZERO scroll mutations
  - ZERO LLM provider invocations
  - ZERO query retries
  - MAXIMUM ONE query entry (Texty sync audit)
  - BOUNDED suggestion surface observation: monotonic deadline <= 4000ms, max 80 polls, 50ms interval
  - EARLY TERMINATION permitted upon safe candidate observation
  - STRICT SENSITIVE DATA SCRUBBING: Zero raw suggestion text, zero URLs, zero tokens persisted.

Cadence & Envelope:
  SUGGESTION_OBSERVATION_TIMEOUT_MS = 4000
  POLL_INTERVAL_MS = 50
  MAX_SUGGESTION_OBSERVATION_POLLS = 80
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
from typing import Any, Callable, Optional

# Ensure project root is in path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.runtime.pesu_webview_host import (
    PesuWebViewHost,
    DIRECT_SLACK_PROFILE_PATH,
)
from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    SEARCH_INPUT_READINESS_TIMEOUT_SECONDS,
    SEARCH_INPUT_POLL_INTERVAL_SECONDS,
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1")


def normalize_slack_ts(ts: Optional[str]) -> Optional[str]:
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    if "." in s:
        parts = s.split(".", 1)
        return f"{parts[0]}.{parts[1].ljust(6, '0')[:6]}"
    return f"{s}.000000"


# ---------------------------------------------------------------------------
# Authoritative SSA1 Constants
# ---------------------------------------------------------------------------

TASK_ID = "TASK_11_Q4_SEARCH_QSC2_SO1"
PROTOCOL_NAME = "TASK_11_Q4_SEARCH_QSC2_SO1_READ_ONLY_OBSERVER"
DIAGNOSTIC_RUN = "QSC2_SO1"

TARGET_QUERY = "26년 하반기 간담회"

SUGGESTION_OBSERVATION_TIMEOUT_MS = 4000
POLL_INTERVAL_MS = 50
MAX_SUGGESTION_OBSERVATION_POLLS = 80

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
ATTEMPT_STARTED_MARKER_FILE = OUTPUT_DIR / "task11q4_search_qsc2_so1_attempt_started.json"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_search_qsc2_so1_query_state_convergence.json"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_search_qsc2_so1_query_state_convergence_dry_run.json"
PROTOCOL_FREEZE_FILE = REPO_ROOT / "artifacts" / "task11q4_search_qsc2_so1_protocol_freeze.json"


# ---------------------------------------------------------------------------
# Pure Read-Only Autocomplete Suggestion Surface Inspector JS
# STRICT ZERO SUBMIT: No click(), no onEnter(), no submit event dispatched
# ---------------------------------------------------------------------------

JS_OBSERVE_SEARCH_SUGGESTION_SURFACE = r"""
((targetQuery) => {
    const report = {
        observation_ok: false,
        observation_valid: false,
        validity_failure_reason: null,

        strong_search_input_box_match_count: 0,
        duplicate_search_input_box_count: 0,
        search_input_preconditions_satisfied: false,

        apis_accessible: {
            dom_element: false,
            react_fiber: false,
            search_input_instance: false,
            texty_object: false,
            quill_instance: false,
            get_texty_ops_callable: false,
            texty_get_contents_callable: false,
            display_query_ops_present: false
        },

        surfaces_synced: {
            dom: false,
            quill_text: false,
            quill_delta: false,
            texty: false,
            display_query_ops: false,
            redux: false
        },
        query_state_all_synced: false,

        internal_flags: {
            query_has_changed: false,
            query_has_been_submitted: false,
            is_updating_display_query: false,
            is_in_composition: false
        },
        internal_flags_ok: false,

        suggestion_surface_present: false,
        connected_candidate_count: 0,
        safe_candidate_count: 0,
        unsafe_candidate_count: 0,
        safe_search_candidate_present: false,
        explicit_search_candidate_present: false,

        candidate_classifications: [],
        normalized_candidate_classifications: [],
        default_candidate_classification: 'NONE',
        selected_candidate_classification: 'NONE',
        candidate_summaries: [],

        safe_candidate_exact_query_correlation: 'UNPROVEN',
        coexistence_observed: false,
        error: null
    };

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    if (searchInputCandidates.length !== 1) {
        report.validity_failure_reason = 'SEARCH_INPUT_CARDINALITY_NOT_ONE';
        report.error = 'SEARCH_INPUT_CARDINALITY_NOT_ONE';
        return report;
    }

    const inputElem = searchInputCandidates[0];
    const role = inputElem.getAttribute('role') || '';
    const insideModal = !!inputElem.closest('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal, [data-qa="search_input_box"]');
    const outsideComposer = !inputElem.closest('.c-message__editor, [data-qa="message_input"], .p-message_input');

    if (!inputElem.isConnected || role !== 'combobox' || !insideModal || !outsideComposer) {
        report.validity_failure_reason = 'SEARCH_INPUT_OR_MODAL_INVALID';
        report.error = 'SEARCH_INPUT_OR_MODAL_INVALID';
        return report;
    }
    report.apis_accessible.dom_element = true;

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) {
        report.validity_failure_reason = 'NO_REACT_FIBER_KEY_FOUND';
        report.error = 'NO_REACT_FIBER_KEY_FOUND';
        return report;
    }
    report.apis_accessible.react_fiber = true;

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
    report.duplicate_search_input_box_count = Math.max(0, matchingInstances.length - 1);

    if (matchingInstances.length !== 1) {
        report.validity_failure_reason = 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_INVALID';
        report.error = 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_INVALID';
        return report;
    }
    report.apis_accessible.search_input_instance = true;
    report.search_input_preconditions_satisfied = true;

    const matched = matchingInstances[0];
    const searchInputBoxInstance = matched.instance;
    const currentFiber = matched.fiber;
    const textyObj = searchInputBoxInstance.texty;

    if (!textyObj) {
        report.validity_failure_reason = 'TEXTY_OBJECT_NOT_FOUND';
        report.error = 'TEXTY_OBJECT_NOT_FOUND';
        return report;
    }
    report.apis_accessible.texty_object = true;

    if (textyObj.instance && textyObj.instance.quill && typeof textyObj.instance.quill.getText === 'function') {
        report.apis_accessible.quill_instance = true;
    }
    if (typeof searchInputBoxInstance.getTextyOps === 'function') {
        report.apis_accessible.get_texty_ops_callable = true;
    }
    if (typeof textyObj.getContents === 'function') {
        report.apis_accessible.texty_get_contents_callable = true;
    }

    const propsToInspect = (currentFiber && currentFiber.memoizedProps && Object.prototype.hasOwnProperty.call(currentFiber.memoizedProps, 'displayQueryOps'))
        ? currentFiber.memoizedProps
        : searchInputBoxInstance.props;
    if (propsToInspect && Object.prototype.hasOwnProperty.call(propsToInspect, 'displayQueryOps')) {
        report.apis_accessible.display_query_ops_present = true;
    }

    if (!report.apis_accessible.quill_instance ||
        !report.apis_accessible.get_texty_ops_callable ||
        !report.apis_accessible.texty_get_contents_callable ||
        !report.apis_accessible.display_query_ops_present) {
        report.validity_failure_reason = 'REQUIRED_INTERNAL_APIS_NOT_ACCESSIBLE';
        report.error = 'REQUIRED_INTERNAL_APIS_NOT_ACCESSIBLE';
        return report;
    }

    const normalizeOpsToString = (ops) => {
        if (!Array.isArray(ops)) return '';
        return ops.map(op => (typeof op.insert === 'string' ? op.insert : '')).join('').trim();
    };

    // DOM text observation
    try {
        const domText = (inputElem.textContent || inputElem.innerText || '').trim();
        report.surfaces_synced.dom = domText.includes(targetQuery);
    } catch (e) {
        report.observation_ok = false;
        report.observation_valid = false;
        report.validity_failure_reason = 'DOM_GETTER_EXCEPTION';
        report.error = 'DOM_GETTER_EXCEPTION';
        return report;
    }

    // QuillText observation
    try {
        const qText = textyObj.instance.quill.getText().trim();
        report.surfaces_synced.quill_text = qText.includes(targetQuery);
    } catch (e) {
        report.observation_ok = false;
        report.observation_valid = false;
        report.validity_failure_reason = 'QUILL_TEXT_GETTER_EXCEPTION';
        report.error = 'QUILL_TEXT_GETTER_EXCEPTION';
        return report;
    }

    // QuillDelta observation
    try {
        const ops = searchInputBoxInstance.getTextyOps();
        const deltaStr = normalizeOpsToString(ops);
        report.surfaces_synced.quill_delta = deltaStr.includes(targetQuery);
    } catch (e) {
        report.observation_ok = false;
        report.observation_valid = false;
        report.validity_failure_reason = 'QUILL_DELTA_GETTER_EXCEPTION';
        report.error = 'QUILL_DELTA_GETTER_EXCEPTION';
        return report;
    }

    // Texty model observation
    try {
        const tContents = textyObj.getContents();
        const tOps = (tContents && tContents.contents) ? tContents.contents : (Array.isArray(tContents) ? tContents : []);
        const tStr = normalizeOpsToString(tOps);
        report.surfaces_synced.texty = tStr.includes(targetQuery);
    } catch (e) {
        report.observation_ok = false;
        report.observation_valid = false;
        report.validity_failure_reason = 'TEXTY_GETTER_EXCEPTION';
        report.error = 'TEXTY_GETTER_EXCEPTION';
        return report;
    }

    // DisplayQueryOps (Redux state in props) observation
    try {
        const dOps = propsToInspect.displayQueryOps;
        const rStr = normalizeOpsToString(dOps);
        report.surfaces_synced.display_query_ops = rStr.includes(targetQuery);
        report.surfaces_synced.redux = report.surfaces_synced.display_query_ops;
    } catch (e) {
        report.observation_ok = false;
        report.observation_valid = false;
        report.validity_failure_reason = 'DISPLAY_QUERY_OPS_GETTER_EXCEPTION';
        report.error = 'DISPLAY_QUERY_OPS_GETTER_EXCEPTION';
        return report;
    }

    // Observation successfully retrieved all query-state surfaces without getter exception
    report.observation_ok = true;
    report.observation_valid = true;

    const surfaces = report.surfaces_synced;
    report.query_state_all_synced = (
        surfaces.dom &&
        surfaces.quill_text &&
        surfaces.quill_delta &&
        surfaces.texty &&
        surfaces.display_query_ops
    );

    // Component state and flags
    let componentStatePresent = false;
    let flagsReadable = false;
    try {
        if (searchInputBoxInstance.state && typeof searchInputBoxInstance.state === 'object') {
            componentStatePresent = true;
            report.internal_flags.query_has_changed = Boolean(searchInputBoxInstance.state.queryHasChanged);
            report.internal_flags.query_has_been_submitted = Boolean(searchInputBoxInstance.state.queryHasBeenSubmitted);
            if ('_isUpdatingDisplayQuery' in searchInputBoxInstance.state && 'isInComposition' in searchInputBoxInstance.state) {
                flagsReadable = true;
                report.internal_flags.is_updating_display_query = Boolean(searchInputBoxInstance.state._isUpdatingDisplayQuery);
                report.internal_flags.is_in_composition = Boolean(searchInputBoxInstance.state.isInComposition);
            }
        }
    } catch (e) {}

    const flags = report.internal_flags;
    report.internal_flags_ok = Boolean(componentStatePresent && flagsReadable && (!flags.is_updating_display_query && !flags.is_in_composition));

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
        if (s.is_type_channel === true || s.type === 'channel') return 'UNSAFE_NON_SEARCH_CHANNEL_NAV';
        if (s.is_type_user === true || s.is_type_user_profile === true || s.type === 'member-profile') return 'UNSAFE_PERSON_NAV';
        if (s.isNavigational === true || s.is_type_navigational === true || s.is_type_workflow === true || s.is_type_app_action === true || s.is_type_huddle === true || s.type === 'queryConvoSearch' || s.type === 'querySalesforce') return 'UNSAFE_OTHER_NAV';
        return 'UNKNOWN_CANDIDATE';
    }

    const propsSuggestions = (searchInputBoxInstance.props && Array.isArray(searchInputBoxInstance.props.suggestions))
        ? searchInputBoxInstance.props.suggestions
        : null;

    const popover = document.querySelector('.c-search_autocomplete, [data-qa="search_autocomplete"], [role="listbox"]');
    if (popover && popover.isConnected) {
        report.suggestion_surface_present = true;
    }

    const suggestionItems = Array.from(document.querySelectorAll('[role="option"], [data-qa="search_autocomplete_item"], .c-search_autocomplete__item')).filter(el => el && el.isConnected);

    let candidateObjects = [];
    if (propsSuggestions && propsSuggestions.length > 0) {
        candidateObjects = propsSuggestions;
        report.suggestion_surface_present = true;
    } else if (suggestionItems.length > 0) {
        report.suggestion_surface_present = true;
        for (let idx = 0; idx < suggestionItems.length; idx++) {
            const item = suggestionItems[idx];
            let itemData = null;
            try {
                const keys = Object.keys(item);
                const reactKey = keys.find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                if (reactKey) {
                    let f = item[reactKey];
                    while (f) {
                        if (f.memoizedProps && (f.memoizedProps.suggestion || f.memoizedProps.item)) {
                            itemData = f.memoizedProps.suggestion || f.memoizedProps.item;
                            break;
                        }
                        f = f.return;
                    }
                }
            } catch (_) {}
            candidateObjects.push(itemData || {
                type: item.getAttribute('data-type') || 'unknown',
                suggestion_sub_type: item.getAttribute('data-sub-type') || 'unknown',
                is_type_query_completion: item.getAttribute('data-is-query-completion') === 'true',
                string: item.textContent || item.innerText || null
            });
        }
    }

    report.connected_candidate_count = candidateObjects.length;

    let safeCandidateFound = null;

    for (let idx = 0; idx < candidateObjects.length; idx++) {
        const candidateObj = candidateObjects[idx];
        const classification = classifyCandidate(candidateObj);
        report.candidate_classifications.push(classification);
        report.normalized_candidate_classifications.push(classification);

        const isSafe = (classification === 'SAFE_EXPLICIT_SEARCH');
        const isUnsafe = isUnsafeNonSearchSuggestion(candidateObj);

        report.candidate_summaries.push({
            index: idx,
            type_tag: String(candidateObj.type || 'unknown'),
            sub_type_tag: String(candidateObj.suggestion_sub_type || 'unknown'),
            classification: classification,
            is_safe: isSafe,
            is_unsafe: isUnsafe,
            is_selected: false
        });

        if (isSafe) {
            report.safe_candidate_count++;
            report.safe_search_candidate_present = true;
            report.explicit_search_candidate_present = true;
            if (!safeCandidateFound) {
                safeCandidateFound = candidateObj;
            }
        } else if (isUnsafe) {
            report.unsafe_candidate_count++;
        }
    }

    if (report.candidate_classifications.length > 0) {
        report.default_candidate_classification = report.candidate_classifications[0];
        report.selected_candidate_classification = report.candidate_classifications[0];
    }

    if (safeCandidateFound) {
        let textCandidate = null;
        if (typeof safeCandidateFound.string === 'string') textCandidate = safeCandidateFound.string;
        else if (typeof safeCandidateFound.query === 'string') textCandidate = safeCandidateFound.query;
        else if (typeof safeCandidateFound.text === 'string') textCandidate = safeCandidateFound.text;
        else if (typeof safeCandidateFound.label === 'string') textCandidate = safeCandidateFound.label;

        if (textCandidate !== null && textCandidate !== undefined) {
            const trimmedCandidate = String(textCandidate).trim();
            const trimmedTarget = String(targetQuery).trim();
            if (trimmedCandidate === trimmedTarget) {
                report.safe_candidate_exact_query_correlation = 'EXACT_MATCH';
            } else {
                report.safe_candidate_exact_query_correlation = 'MISMATCH';
            }
        } else {
            report.safe_candidate_exact_query_correlation = 'UNPROVEN';
        }
    } else {
        report.safe_candidate_exact_query_correlation = 'UNPROVEN';
    }

    report.coexistence_observed = Boolean(
        report.observation_valid &&
        report.query_state_all_synced &&
        report.internal_flags_ok &&
        report.safe_search_candidate_present
    );

    return report;
})
"""


# ---------------------------------------------------------------------------
# Terminal Diagnostic Classification
# ---------------------------------------------------------------------------

def analyze_candidate_observational_persistence(polls: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyzes first-occurrence and observational persistence metrics across bounded polls."""
    first_safe_idx = None
    first_safe_elapsed = None
    first_sync_idx = None
    first_sync_elapsed = None
    first_coexist_idx = None
    first_coexist_elapsed = None

    for p in polls:
        idx = p.get("poll_index")
        elapsed = p.get("elapsed_ms")
        is_safe = bool(p.get("safe_search_candidate_present", False))
        is_sync = bool(p.get("query_state_all_synced", False))

        if is_safe and first_safe_idx is None:
            first_safe_idx = idx
            first_safe_elapsed = elapsed

        if is_sync and first_sync_idx is None:
            first_sync_idx = idx
            first_sync_elapsed = elapsed

        if is_safe and is_sync and first_coexist_idx is None:
            first_coexist_idx = idx
            first_coexist_elapsed = elapsed

    appeared_before_convergence = False
    persisted_until_convergence = False
    disappeared_before_convergence = False
    reappeared_after_convergence = False

    if first_safe_idx is not None:
        if first_sync_idx is not None:
            appeared_before_convergence = (first_safe_idx < first_sync_idx)
        else:
            appeared_before_convergence = True

    if first_sync_idx is not None:
        polls_at_or_after_sync = [p for p in polls if p.get("poll_index", 0) >= first_sync_idx]
        sync_poll = next((p for p in polls if p.get("poll_index") == first_sync_idx), None)
        safe_at_sync = bool(sync_poll.get("safe_search_candidate_present", False)) if sync_poll else False

        if appeared_before_convergence:
            intermediate_polls = [p for p in polls if first_safe_idx <= p.get("poll_index", 0) <= first_sync_idx]
            persisted_until_convergence = (all(p.get("safe_search_candidate_present", False) for p in intermediate_polls) and safe_at_sync)
            disappeared_before_convergence = any(not p.get("safe_search_candidate_present", False) for p in intermediate_polls)

        if not safe_at_sync:
            reappeared_after_convergence = any(
                p.get("safe_search_candidate_present", False)
                for p in polls_at_or_after_sync
                if p.get("poll_index", 0) > first_sync_idx
            )
        else:
            dropped = False
            for p in polls_at_or_after_sync:
                if p.get("poll_index", 0) > first_sync_idx:
                    if not p.get("safe_search_candidate_present", False):
                        dropped = True
                    elif dropped and p.get("safe_search_candidate_present", False):
                        reappeared_after_convergence = True
                        break
    else:
        if appeared_before_convergence and first_safe_idx is not None:
            subsequent_polls = [p for p in polls if p.get("poll_index", 0) > first_safe_idx]
            disappeared_before_convergence = any(not p.get("safe_search_candidate_present", False) for p in subsequent_polls)

    return {
        "first_safe_candidate_poll_index": first_safe_idx,
        "first_safe_candidate_elapsed_ms": first_safe_elapsed,
        "first_query_state_synced_poll_index": first_sync_idx,
        "first_query_state_synced_elapsed_ms": first_sync_elapsed,
        "first_coexistence_poll_index": first_coexist_idx,
        "first_coexistence_elapsed_ms": first_coexist_elapsed,
        "safe_candidate_appeared_before_convergence": appeared_before_convergence,
        "safe_candidate_persisted_until_convergence": persisted_until_convergence,
        "safe_candidate_disappeared_before_convergence": disappeared_before_convergence,
        "safe_candidate_reappeared_after_convergence": reappeared_after_convergence,
    }


def classify_qsc2_so1_terminal_state(
    polls: list[dict[str, Any]],
    error: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Deterministically classifies the terminal diagnostic state of QSC2-SO1 observation."""
    persistence = analyze_candidate_observational_persistence(polls)
    details: dict[str, Any] = {
        "poll_count": len(polls),
        **persistence,
        "valid_poll_count": 0,
        "invalid_poll_count": 0,
        "final_observation_valid": False,
        "final_validity_failure_reason": None,
        "final_safe_candidate_count": 0,
        "final_connected_candidate_count": 0,
        "final_default_candidate_classification": "NONE",
        "final_candidate_classifications": [],
        "final_query_state_all_synced": False,
        "final_internal_flags_ok": False,
        "final_safe_candidate_exact_query_correlation": "UNPROVEN",
        "error": error,
    }

    if error is not None:
        return "FAIL_CLOSED", details

    if not polls:
        return "OBSERVATION_INVALID", details

    valid_polls = [p for p in polls if p.get("observation_valid") is True]
    invalid_polls = [p for p in polls if p.get("observation_valid") is not True]
    details["valid_poll_count"] = len(valid_polls)
    details["invalid_poll_count"] = len(invalid_polls)

    final_poll = polls[-1]
    details["final_observation_valid"] = bool(final_poll.get("observation_valid", False))
    details["final_validity_failure_reason"] = final_poll.get("validity_failure_reason")
    details["final_safe_candidate_count"] = final_poll.get("safe_candidate_count", 0)
    details["final_connected_candidate_count"] = final_poll.get("connected_candidate_count", 0)
    details["final_default_candidate_classification"] = final_poll.get("default_candidate_classification", "NONE")
    details["final_candidate_classifications"] = final_poll.get("candidate_classifications", [])
    details["final_query_state_all_synced"] = final_poll.get("query_state_all_synced", False)
    details["final_internal_flags_ok"] = final_poll.get("internal_flags_ok", False)
    details["final_safe_candidate_exact_query_correlation"] = final_poll.get("safe_candidate_exact_query_correlation", "UNPROVEN")

    if not valid_polls:
        return "OBSERVATION_INVALID", details

    coexist_polls = [
        p for p in valid_polls
        if p.get("query_state_all_synced")
        and p.get("safe_search_candidate_present")
        and p.get("internal_flags_ok")
        and p.get("safe_candidate_exact_query_correlation") != "MISMATCH"
    ]
    if coexist_polls:
        return "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION", details

    mismatch_coexist_polls = [
        p for p in valid_polls
        if p.get("query_state_all_synced")
        and p.get("safe_search_candidate_present")
        and p.get("internal_flags_ok")
        and p.get("safe_candidate_exact_query_correlation") == "MISMATCH"
    ]
    if mismatch_coexist_polls:
        return "QUERY_STATE_SYNCED_WITH_CANDIDATE_QUERY_MISMATCH", details

    any_sync = any(p.get("query_state_all_synced") and p.get("internal_flags_ok") for p in valid_polls)
    any_safe = any(p.get("safe_search_candidate_present") for p in valid_polls)

    all_candidates = []
    for p in valid_polls:
        all_candidates.extend(p.get("candidate_classifications", []))

    if any_sync and any_safe:
        return "QUERY_STATE_AND_SAFE_SUGGESTION_OBSERVED_WITHOUT_COEXISTENCE", details

    if any_sync and not any_safe:
        if all_candidates and all(c == "UNKNOWN_CANDIDATE" for c in all_candidates):
            return "SUGGESTION_STRUCTURE_UNRECOGNIZED", details
        return "QUERY_STATE_SYNCED_SAFE_SUGGESTION_NEVER_OBSERVED", details

    if not any_sync and any_safe:
        return "SAFE_SUGGESTION_OBSERVED_BUT_QUERY_STATE_NEVER_SYNCED", details

    return "QUERY_STATE_NEVER_SYNCED_AND_NO_SAFE_SUGGESTION", details


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
    """Recursively scrub raw suggestion text, URLs, permalinks, and tokens."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if k in {
                "raw_text", "raw_suggestion", "text", "query_text", "permalink",
                "href", "url", "innerText", "textContent", "html", "innerHTML",
                "aria_label", "title", "email", "token", "auth_token", "cookie"
            }:
                continue
            sanitized[k] = sanitize_report_for_persistence(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_report_for_persistence(item) for item in data]
    elif isinstance(data, str):
        for pat in SENSITIVE_TEXT_PATTERNS:
            if pat.search(data):
                return "[REDACTED_SENSITIVE_STRING]"
        return data
    else:
        return data


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    deps = {
        "ssa1r2_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1.py",
        "ssa1r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_ssa1r1.py",
        "ssa1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_ssa1.py",
        "vsa2_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_vsa2_bounded_partial_scroll.py",
        "vsa1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_vsa1_bounded_scroll_safety.py",
        "mgr1r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mgr1_r1_clean_room.py",
        "webview_slack_search": REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py",
        "pesu_webview_host": REPO_ROOT / "src" / "pesu_agent" / "runtime" / "pesu_webview_host.py",
        "slack_permalink_parser": REPO_ROOT / "src" / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
    }
    return {k: get_file_sha256(p) for k, p in deps.items() if p.exists()}


# ---------------------------------------------------------------------------
# Static & Runtime Security Hard Gates
# ---------------------------------------------------------------------------

def verify_static_zero_submit_contract(runner_path: Optional[Path] = None) -> bool:
    """Verifies that SSA1 observation JS strictly executes ZERO submit, onEnter, or click actions."""
    p = runner_path or Path(__file__)
    src = p.read_text(encoding="utf-8")

    tree = ast.parse(src)
    js_observe_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE":
                    if isinstance(node.value, ast.Constant):
                        js_observe_node = node.value.value

    if not js_observe_node or not isinstance(js_observe_node, str):
        logger.error("JS_OBSERVE_SEARCH_SUGGESTION_SURFACE snippet not found as ast Constant")
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
            logger.error(f"Forbidden mutation pattern found in observation JS: {pat}")
            return False

    return True


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    """Audits the runner AST for zero executable clicks, keyboard events, or navigations."""
    p = runner_path or Path(__file__)
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in ("click", "click_element", "dispatch_mouse_event", "send_keys", "press_enter"):
                    logger.error(f"Forbidden interaction call found in runner: {attr}")
                    return False
    return True


def extract_js_function(source: str, fn_name: str) -> Optional[str]:
    """Extracts a JavaScript function definition (both standard and arrow functions) from source string."""
    pattern = rf"(?:function\s+{fn_name}\s*\([^)]*\)|(?:const|let|var)\s+{fn_name}\s*=\s*(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>|(?:const|let|var)\s+{fn_name}\s*=\s*function\s*\([^)]*\))\s*\{{"
    m = re.search(pattern, source)
    if not m:
        return None
    start = m.start()
    depth = 0
    in_fn = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            in_fn = True
        elif source[i] == "}":
            depth -= 1
            if in_fn and depth == 0:
                return source[start : i + 1]
    return None


def normalize_js_code(code: str) -> str:
    """Normalizes JS code by stripping comments and collapsing whitespace."""
    code = re.sub(r"//.*", "", code)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return " ".join(code.split())


def verify_production_candidate_classifier_parity() -> tuple[bool, str]:
    """Hard gate proving exact semantic parity between production submit guard and SSA1 classifier.
    
    Extracts isWriteCapableSuggestion, isUnsafeNonSearchSuggestion, and isExactSafeKeywordSearch
    from JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER and JS_OBSERVE_SEARCH_SUGGESTION_SURFACE,
    proves normalized equivalence, and validates all positive safe predicates and unsafe exclusions.
    """
    fn_names = [
        "isWriteCapableSuggestion",
        "isUnsafeNonSearchSuggestion",
        "isExactSafeKeywordSearch",
    ]
    
    for fn in fn_names:
        prod_fn = extract_js_function(JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER, fn)
        ssa1_fn = extract_js_function(JS_OBSERVE_SEARCH_SUGGESTION_SURFACE, fn)
        if not prod_fn:
            return False, f"Missing {fn} in production submit guard constant"
        if not ssa1_fn:
            return False, f"Missing {fn} in SSA1 suggestion observation constant"
        
        norm_prod = normalize_js_code(prod_fn)
        norm_ssa1 = normalize_js_code(ssa1_fn)
        if norm_prod != norm_ssa1:
            return False, f"Classifier function body mismatch for {fn}"

    # Explicit positive predicate checks
    norm_exact = normalize_js_code(extract_js_function(JS_OBSERVE_SEARCH_SUGGESTION_SURFACE, "isExactSafeKeywordSearch"))
    if "s.is_type_query_completion === true" not in norm_exact:
        return False, "Missing positive safe predicate: is_type_query_completion === true"
    if "s.type === 'queryUser'" not in norm_exact:
        return False, "Missing positive safe predicate: type === 'queryUser'"
    if "s.suggestion_sub_type === 'query-echo'" not in norm_exact:
        return False, "Missing positive safe predicate: suggestion_sub_type === 'query-echo'"

    # Explicit unsafe exclusions checks
    norm_unsafe = normalize_js_code(extract_js_function(JS_OBSERVE_SEARCH_SUGGESTION_SURFACE, "isUnsafeNonSearchSuggestion"))
    required_unsafe_tokens = [
        "isWriteCapableSuggestion(s)",
        "s.isNavigational === true",
        "s.is_type_navigational === true",
        "s.is_type_channel === true",
        "s.type === 'channel'",
        "s.is_type_user === true",
        "s.is_type_user_profile === true",
        "s.type === 'member-profile'",
        "s.is_type_workflow === true",
        "s.type === 'workflowTrigger'",
        "s.is_type_app_action === true",
        "s.type === 'appAction'",
        "s.is_type_huddle === true",
        "s.type === 'huddle'",
        "s.type === 'queryConvoSearch'",
        "s.type === 'querySalesforce'",
    ]
    for token in required_unsafe_tokens:
        if token not in norm_unsafe:
            return False, f"Missing unsafe exclusion predicate: {token}"

    return True, "EXACT_PARITY_PROVEN"



def verify_production_read_only_query_state_parity() -> tuple[bool, str]:
    """Hard gate proving exact parity between production submit guard and QSC2 observer query-state read paths."""
    prod_strong = extract_js_function(JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER, "isSearchInputBoxStrong")
    obs_strong = extract_js_function(JS_OBSERVE_SEARCH_SUGGESTION_SURFACE, "isSearchInputBoxStrong")
    if not prod_strong or not obs_strong:
        return False, "Missing isSearchInputBoxStrong function definition"
    if normalize_js_code(prod_strong) != normalize_js_code(obs_strong):
        return False, "isSearchInputBoxStrong function definition mismatch"

    prod_norm_ops = extract_js_function(JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER, "normalizeOpsToString")
    obs_norm_ops = extract_js_function(JS_OBSERVE_SEARCH_SUGGESTION_SURFACE, "normalizeOpsToString")
    if not prod_norm_ops or not obs_norm_ops:
        return False, "Missing normalizeOpsToString function definition"
    if normalize_js_code(prod_norm_ops) != normalize_js_code(obs_norm_ops):
        return False, "normalizeOpsToString function definition mismatch"

    obs_src = JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    required_paths = [
        "domText.includes(targetQuery)",
        "textyObj.instance.quill.getText()",
        "searchInputBoxInstance.getTextyOps()",
        "textyObj.getContents()",
        "displayQueryOps",
    ]
    for p in required_paths:
        if p not in obs_src:
            return False, f"Missing required production read path in observer: {p}"

    forbidden_obsolete = [
        "window.reduxStore",
        "stateNode.quill",
        "textyComp.state.text",
    ]
    for fob in forbidden_obsolete:
        if fob in obs_src:
            return False, f"Obsolete/invalid property access detected in observer: {fob}"

    return True, "PRODUCTION_READ_ONLY_QUERY_STATE_PARITY_PROVEN"


def verify_production_component_flags_parity() -> tuple[bool, str]:
    """Hard gate proving exact parity between production internal flags and QSC2 observer flags."""
    obs_src = JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    if "_isUpdatingDisplayQuery" not in obs_src:
        return False, "Missing _isUpdatingDisplayQuery check in observer"
    if "isInComposition" not in obs_src:
        return False, "Missing isInComposition check in observer"
    if "(!flags.is_updating_display_query && !flags.is_in_composition)" not in obs_src:
        return False, "Missing internal_flags_ok evaluation in observer"
    return True, "COMPONENT_FLAGS_PARITY_PROVEN"


def validate_query_setter_response(result: Any) -> bool:
    """Authoritative validation of production model-native texty query setter response.
    
    Production JS_SET_TEXTY_QUERY returns:
        { ok: true, setter_succeeded: true } on success.
    Both 'ok' and 'setter_succeeded' must be strictly True (boolean).
    Generic 'success' key or partial payloads are strictly rejected.
    """
    if not isinstance(result, dict):
        return False
    if result.get("ok") is not True:
        return False
    if result.get("setter_succeeded") is not True:
        return False
    return True


def verify_query_setter_response_contract_parity(runner_path: Optional[Path] = None) -> tuple[bool, str]:
    """Pre-marker hard gate: proves the query-setter response contract matches production JS_SET_TEXTY_QUERY.
    
    Validates:
      1. Accepts actual live production payload: {"ok": True, "setter_succeeded": True}
      2. Rejects generic success payload: {"success": True}
      3. Rejects partial shapes: {"ok": True}, {"setter_succeeded": True}, etc.
      4. Rejects None and non-dict inputs
      5. Runner AST contains zero live acceptance check for .get("success") on query setter response
      6. Production JS_SET_TEXTY_QUERY contains exact success return: 'return { ok: true, setter_succeeded: true };'
    """
    # 1. Payload validation tests
    if not validate_query_setter_response({"ok": True, "setter_succeeded": True}):
        return False, "Validator failed to accept valid production setter payload {'ok': True, 'setter_succeeded': True}"

    rejected_cases = [
        {"success": True},
        {"ok": True},
        {"setter_succeeded": True},
        {"ok": False, "setter_succeeded": True},
        {"ok": True, "setter_succeeded": False},
        {"ok": 1, "setter_succeeded": 1},
        {"ok": "true", "setter_succeeded": "true"},
        {},
        None,
        "success",
        [{"ok": True, "setter_succeeded": True}],
    ]
    for case in rejected_cases:
        if validate_query_setter_response(case):
            return False, f"Validator incorrectly accepted invalid payload: {case}"

    # 2. AST audit of runner source: ensure no live check for .get("success") on query setter
    p = runner_path or Path(__file__)
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "success":
                    return False, "Forbidden live acceptance check for .get('success') found in runner AST"

    # 3. Verify production primitive contract
    from pesu_agent.search.webview_slack_search import JS_SET_TEXTY_QUERY as PROD_SETTER
    if "return { ok: true, setter_succeeded: true };" not in PROD_SETTER:
        return False, "Production JS_SET_TEXTY_QUERY does not contain expected return payload contract"

    return True, "EXACT_QUERY_SETTER_RESPONSE_CONTRACT_PARITY_PROVEN"


def verify_query_entry_primitive_parity() -> tuple[bool, str]:
    """Proves that the SSA1 runner directly reuses exact production query-entry JS primitives."""
    from pesu_agent.search import webview_slack_search
    primitives = [
        ("JS_CHECK_AUTH_AND_READINESS", JS_CHECK_AUTH_AND_READINESS),
        ("JS_ACTIVATE_SEARCH", JS_ACTIVATE_SEARCH),
        ("JS_CHECK_SEARCH_INPUT_READINESS", JS_CHECK_SEARCH_INPUT_READINESS),
        ("JS_SET_TEXTY_QUERY", JS_SET_TEXTY_QUERY),
    ]
    for name, imported_val in primitives:
        prod_val = getattr(webview_slack_search, name, None)
        if prod_val is None:
            return False, f"Missing production query-entry primitive: {name}"
        if imported_val is not prod_val:
            return False, f"Primitive reference mismatch for {name}: not direct production import"
        if not isinstance(imported_val, str) or len(imported_val.strip()) == 0:
            return False, f"Primitive {name} is empty"
    return True, "EXACT_PRODUCTION_JS_QUERY_ENTRY_PRIMITIVES_REUSED"


def build_qsc2_so1_runtime_call_spec(timeout: float = 60.0, profile_path: Optional[str] = None) -> dict[str, Any]:
    """Builds a unified runtime call specification shared between signature parity verification and live execution."""
    prof = str(profile_path or DIRECT_SLACK_PROFILE_PATH)
    return {
        "host_cls": PesuWebViewHost,
        "host_kwargs": {
            "user_data_folder": prof,
            "initial_url": "https://app.slack.com/client",
            "title": "PESU Task 11 / Search Safety / SSA1-R2 Safe Suggestion Availability",
        },
        "config_cls": WebViewRuntimeConfig,
        "config_kwargs": {
            "storage_path": Path(prof),
        },
        "backend_cls": WebViewSlackSearchBackend,
        "backend_kwargs_factory": lambda config, host: {
            "config": config,
            "host": host,
        },
        "start_runtime_method": "start_runtime",
        "start_runtime_kwargs": {
            "timeout": timeout,
        },
        "evaluate_js_method": "evaluate_js",
        "evaluate_js_sample_kwargs": {
            "script": "return true;",
            "timeout": 5.0,
        },
    }


def verify_runtime_api_signature_parity(spec: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    """Verifies that all runtime constructors and methods match planned call signatures."""
    import inspect
    call_spec = spec or build_qsc2_so1_runtime_call_spec(timeout=60.0)

    # 1. PesuWebViewHost.__init__
    try:
        sig_host = inspect.signature(call_spec["host_cls"].__init__)
        sig_host.bind(None, **call_spec["host_kwargs"])
    except Exception as ex:
        return False, f"PesuWebViewHost.__init__ signature bind failed: {ex}"

    # 2. WebViewRuntimeConfig.__init__
    try:
        sig_cfg = inspect.signature(call_spec["config_cls"].__init__)
        sig_cfg.bind(None, **call_spec["config_kwargs"])
    except Exception as ex:
        return False, f"WebViewRuntimeConfig.__init__ signature bind failed: {ex}"

    # 3. WebViewSlackSearchBackend.__init__
    try:
        sig_backend = inspect.signature(call_spec["backend_cls"].__init__)
        dummy_cfg = object()
        dummy_host = object()
        b_kwargs = call_spec["backend_kwargs_factory"](dummy_cfg, dummy_host)
        sig_backend.bind(None, **b_kwargs)
    except Exception as ex:
        return False, f"WebViewSlackSearchBackend.__init__ signature bind failed: {ex}"

    # 4. WebViewSlackSearchBackend.start_runtime
    try:
        method = getattr(call_spec["backend_cls"], call_spec["start_runtime_method"])
        sig_start = inspect.signature(method)
        sig_start.bind(None, **call_spec["start_runtime_kwargs"])
    except Exception as ex:
        return False, f"WebViewSlackSearchBackend.start_runtime signature bind failed: {ex}"

    # 5. PesuWebViewHost.evaluate_js
    try:
        eval_method = getattr(call_spec["host_cls"], call_spec["evaluate_js_method"])
        sig_eval = inspect.signature(eval_method)
        sig_eval.bind(None, **call_spec["evaluate_js_sample_kwargs"])
    except Exception as ex:
        return False, f"PesuWebViewHost.evaluate_js signature bind failed: {ex}"

    return True, "PASS"


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


def get_all_loaded_project_files() -> list[Path]:
    loaded = []
    for mod_name, mod in list(sys.modules.items()):
        if mod and hasattr(mod, "__file__") and mod.__file__:
            p = Path(mod.__file__).resolve()
            if str(p).startswith(str(REPO_ROOT / "src")):
                loaded.append(p)
    this_file = Path(__file__).resolve()
    if this_file not in loaded:
        loaded.append(this_file)
    return sorted(list(set(loaded)))


def audit_runtime_import_closure(expected_closure: dict[str, str]) -> tuple[bool, list[str]]:
    actual_files = get_all_loaded_project_files()
    errors = []
    actual_rel_set = set()
    for f in actual_files:
        try:
            rel = str(f.relative_to(REPO_ROOT)).replace(chr(92), "/")
            actual_rel_set.add(rel)
            sha = get_file_sha256(f)
            if rel not in expected_closure:
                errors.append(f"Unexpected module loaded outside frozen closure: {rel}")
            elif expected_closure[rel] != sha:
                errors.append(f"SHA mismatch for module {rel}: expected {expected_closure[rel]}, got {sha}")
        except Exception as ex:
            errors.append(f"Error checking {f}: {ex}")

    missing = set(expected_closure.keys()) - actual_rel_set
    if missing:
        errors.append(f"Missing modules from runtime closure ({len(missing)}): {sorted(list(missing))[:5]}")

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Mock Observations for Dry Run Mode
# ---------------------------------------------------------------------------

def build_mock_dry_run_observation_polls() -> list[dict[str, Any]]:
    """Constructs synthetic observation polls for offline dry-run verification."""
    return [
        {
            "poll_index": 1,
            "elapsed_ms": 10.0,
            "observation_valid": True,
            "validity_failure_reason": None,
            "search_input_preconditions_satisfied": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "search_input_query_matches_requested": True,
            "quill_text_synced": True,
            "quill_delta_synced": True,
            "texty_model_synced": True,
            "display_query_ops_synced": True,
            "redux_state_synced": True,
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": True,
            "explicit_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "normalized_candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "safe_candidate_exact_query_correlation": "EXACT_MATCH",
            "coexistence_observed": True,
            "candidate_summaries": [
                {
                    "index": 0,
                    "type_tag": "queryUser",
                    "sub_type_tag": "query-echo",
                    "classification": "SAFE_EXPLICIT_SEARCH",
                    "is_safe": True,
                    "is_unsafe": False,
                    "is_selected": False,
                },
                {
                    "index": 1,
                    "type_tag": "querySlackbot",
                    "sub_type_tag": "query-slackbot",
                    "classification": "UNSAFE_WRITE_CAPABLE_SLACKBOT",
                    "is_safe": False,
                    "is_unsafe": True,
                    "is_selected": False,
                },
            ],
        }
    ]


def run_qsc2_so1_qualification(
    dry_run: bool = False,
    timeout: float = 60.0,
    marker_file_override: Optional[Path] = None,
    output_file_override: Optional[Path] = None,
    host_factory: Optional[Callable[..., Any]] = None,
    backend_factory: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Executes the SSA1 safe suggestion availability diagnostic harness."""
    run_timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_commit = get_git_commit()

    marker_file = marker_file_override or ATTEMPT_STARTED_MARKER_FILE
    output_file = output_file_override or (DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE)

    report: dict[str, Any] = {
        "task_id": TASK_ID,
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "timestamp_utc": run_timestamp_utc,
        "git_commit": git_commit,
        "dry_run": dry_run,
        "verdict": "UNEVALUATED",
        "diagnostic_verdict": "UNSET",
        "terminal_classification": "UNSET",
        "terminal_details": {},
        "target_query": TARGET_QUERY,
        "observation_envelope": {
            "observation_timeout_ms": SUGGESTION_OBSERVATION_TIMEOUT_MS,
            "poll_interval_ms": POLL_INTERVAL_MS,
            "max_polls": MAX_SUGGESTION_OBSERVATION_POLLS,
        },
        "observation_telemetry": {},
        "checks": {
            "HEAD_EXACT": False,
            "HISTORICAL_BASELINES_EXACT": False,
            "RUNTIME_IMPORT_CLOSURE_EXACT": False,
            "PRODUCTION_CANDIDATE_CLASSIFIER_PARITY": False,
            "PRODUCTION_READ_ONLY_QUERY_STATE_PARITY": False,
            "PRODUCTION_COMPONENT_FLAGS_PARITY": False,
            "QUERY_ENTRY_PRIMITIVE_PARITY": False,
            "QUERY_SETTER_RESPONSE_CONTRACT_PARITY": False,
            "QSC2_SO1_RUNTIME_API_SIGNATURE_PARITY": False,
            "QSC2_SO1_STATIC_ZERO_SUBMIT_AUDIT": False,
            "QSC2_SO1_STATIC_ZERO_INTERACTION_AUDIT": False,
            "attempt_marker_written": False,
            "query_entry_executed": False,
            "query_state_all_synced": False,
            "bounded_observation_executed": False,
        },
        "live_action_inventory": {
            "search_operations": 0,
            "query_entries": 0,
            "safe_submit_decisions": 0,
            "actual_submit_attempts": 0,
            "actual_submit_successes": 0,
            "enter_key_events": 0,
            "suggestion_clicks": 0,
            "navigation_actions": 0,
            "scroll_mutations": 0,
            "provider_invocations": 0,
            "retries": 0,
        },
        "observation_polls": [],
        "harness_phases": [],
        "error": None,
    }

    logger.info("============================================================")
    logger.info("PESU AGENT — PHASE 13.5D / TASK 11 / SSA1 PREP / SEARCH SAFETY")
    logger.info(f"Protocol: {PROTOCOL_NAME} | Mode: {'DRY_RUN' if dry_run else 'LIVE'}")
    logger.info("============================================================")

    # -----------------------------------------------------------------------
    # PREFLIGHT HARD GATES
    # -----------------------------------------------------------------------
    report["harness_phases"].append("PREFLIGHT_VERIFICATION")

    if not PROTOCOL_FREEZE_FILE.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "MISSING_PROTOCOL_FREEZE"
        report["error"] = f"Protocol freeze artifact missing at {PROTOCOL_FREEZE_FILE}"
        return report

    try:
        with open(PROTOCOL_FREEZE_FILE, "r", encoding="utf-8") as pf:
            protocol_manifest = json.load(pf)
    except Exception as ex:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "CORRUPT_PROTOCOL_FREEZE"
        report["error"] = f"Failed to load protocol freeze manifest: {ex}"
        return report

    frozen_head = protocol_manifest.get("git_commit")
    if frozen_head and frozen_head not in ("UNSET", "None") and git_commit != frozen_head:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "GIT_HEAD_MISMATCH"
        report["error"] = f"Active git commit {git_commit} != frozen HEAD {frozen_head}"
        return report
    report["checks"]["HEAD_EXACT"] = (git_commit != "UNKNOWN" and len(git_commit) == 40)

    # Audit 32 Historical Baselines
    historical_baselines = protocol_manifest.get("historical_baselines", {})
    if len(historical_baselines) != 34:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_COUNT_MISMATCH"
        report["error"] = f"Expected 34 historical baselines, found {len(historical_baselines)}"
        return report

    baseline_mismatches = []
    for base_name, base_info in historical_baselines.items():
        rel_path = base_info.get("path") if isinstance(base_info, dict) else base_name
        expected_sha = base_info.get("sha256") if isinstance(base_info, dict) else base_info
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            baseline_mismatches.append(f"Missing historical baseline {base_name}: {rel_path}")
            continue
        actual_sha = get_file_sha256(full_path)
        if actual_sha != expected_sha:
            baseline_mismatches.append(f"Historical baseline SHA mismatch for {base_name} ({rel_path}): {actual_sha} != {expected_sha}")

    if baseline_mismatches:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_SHA_MISMATCH"
        report["error"] = f"Historical baseline verification failed: {baseline_mismatches}"
        return report
    report["checks"]["HISTORICAL_BASELINES_EXACT"] = True

    # Audit Runtime Import Closure
    frozen_closure = protocol_manifest.get("runtime_closure_hashes", {})
    closure_ok, closure_errors = audit_runtime_import_closure(frozen_closure)
    if not closure_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_IMPORT_CLOSURE_DRIFT"
        report["error"] = f"Runtime import closure audit failed: {closure_errors}"
        return report
    report["checks"]["RUNTIME_IMPORT_CLOSURE_EXACT"] = True

    # Check Static Audits
    if not verify_static_zero_submit_contract():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "STATIC_ZERO_SUBMIT_AUDIT_FAILED"
        report["error"] = "Static AST audit detected potential search submit / onEnter invocations"
        return report
    report["checks"]["QSC2_SO1_STATIC_ZERO_SUBMIT_AUDIT"] = True

    if not verify_static_zero_interaction_contract():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "STATIC_ZERO_INTERACTION_AUDIT_FAILED"
        report["error"] = "Static AST audit detected potential click, navigation, or scroll invocations"
        return report
    report["checks"]["QSC2_SO1_STATIC_ZERO_INTERACTION_AUDIT"] = True

    # Classifier Parity Hard Gate
    classifier_parity_ok, classifier_msg = verify_production_candidate_classifier_parity()
    if not classifier_parity_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PRODUCTION_CANDIDATE_CLASSIFIER_PARITY_FAILED"
        report["error"] = f"Production candidate classifier parity verification failed: {classifier_msg}"
        return report
    report["checks"]["PRODUCTION_CANDIDATE_CLASSIFIER_PARITY"] = True

    query_state_parity_ok, query_state_msg = verify_production_read_only_query_state_parity()
    if not query_state_parity_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PRODUCTION_READ_ONLY_QUERY_STATE_PARITY_FAILED"
        report["error"] = f"Query state read-only parity failed: {query_state_msg}"
        return report
    report["checks"]["PRODUCTION_READ_ONLY_QUERY_STATE_PARITY"] = True

    flags_parity_ok, flags_msg = verify_production_component_flags_parity()
    if not flags_parity_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PRODUCTION_COMPONENT_FLAGS_PARITY_FAILED"
        report["error"] = f"Component flags parity failed: {flags_msg}"
        return report
    report["checks"]["PRODUCTION_COMPONENT_FLAGS_PARITY"] = True

    # Query Entry Primitive Parity Hard Gate
    query_primitive_ok, query_msg = verify_query_entry_primitive_parity()
    if not query_primitive_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "QUERY_ENTRY_PRIMITIVE_PARITY_FAILED"
        report["error"] = f"Query entry primitive parity verification failed: {query_msg}"
        return report
    report["checks"]["QUERY_ENTRY_PRIMITIVE_PARITY"] = True

    # Query Setter Response Contract Parity Hard Gate
    setter_contract_ok, setter_contract_msg = verify_query_setter_response_contract_parity()
    if not setter_contract_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "QUERY_SETTER_RESPONSE_CONTRACT_PARITY_FAILED"
        report["error"] = f"Query setter response contract parity verification failed: {setter_contract_msg}"
        return report
    report["checks"]["QUERY_SETTER_RESPONSE_CONTRACT_PARITY"] = True

    # Runtime API Signature Parity Hard Gate
    runtime_sig_ok, runtime_msg = verify_runtime_api_signature_parity()
    if not runtime_sig_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "SSA1_RUNTIME_API_SIGNATURE_PARITY_FAILED"
        report["error"] = f"Runtime API signature parity check failed: {runtime_msg}"
        return report
    report["checks"]["QSC2_SO1_RUNTIME_API_SIGNATURE_PARITY"] = True

    # Verify Protocol Manifest Configuration Match
    semantic_errors = []
    if protocol_manifest.get("target_query") != TARGET_QUERY:
        semantic_errors.append(f"Target query mismatch: {protocol_manifest.get('target_query')} != {TARGET_QUERY}")
    if protocol_manifest.get("max_suggestion_observation_polls") != MAX_SUGGESTION_OBSERVATION_POLLS:
        semantic_errors.append(f"max_suggestion_observation_polls mismatch: {protocol_manifest.get('max_suggestion_observation_polls')} != {MAX_SUGGESTION_OBSERVATION_POLLS}")
    if protocol_manifest.get("poll_interval_ms") != POLL_INTERVAL_MS:
        semantic_errors.append(f"poll_interval_ms mismatch: {protocol_manifest.get('poll_interval_ms')} != {POLL_INTERVAL_MS}")
    if protocol_manifest.get("suggestion_observation_timeout_ms") != SUGGESTION_OBSERVATION_TIMEOUT_MS:
        semantic_errors.append(f"suggestion_observation_timeout_ms mismatch: {protocol_manifest.get('suggestion_observation_timeout_ms')} != {SUGGESTION_OBSERVATION_TIMEOUT_MS}")

    if semantic_errors:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_SEMANTIC_PARAMETER_MISMATCH"
        report["error"] = f"Protocol semantic parameter mismatches: {semantic_errors}"
        return report

    # -----------------------------------------------------------------------
    # DRY RUN PATH
    # -----------------------------------------------------------------------
    if dry_run:
        report["harness_phases"].append("DRY_RUN_EXECUTION")
        logger.info("🔬 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock suggestion observation...")
        report["checks"]["query_entry_executed"] = True
        report["checks"]["query_state_all_synced"] = True
        report["checks"]["bounded_observation_executed"] = True

        mock_polls = build_mock_dry_run_observation_polls()
        persistence_telemetry = analyze_candidate_observational_persistence(mock_polls)
        report["observation_polls"] = mock_polls
        report["observation_telemetry"] = {
            "observation_timeout_ms": SUGGESTION_OBSERVATION_TIMEOUT_MS,
            "poll_interval_ms": POLL_INTERVAL_MS,
            "max_polls": MAX_SUGGESTION_OBSERVATION_POLLS,
            "actual_poll_count": len(mock_polls),
            "actual_observation_elapsed_ms": 60.0,
            **persistence_telemetry,
        }

        term_cls, term_det = classify_qsc2_so1_terminal_state(mock_polls, error=None)
        report["terminal_classification"] = term_cls
        report["diagnostic_verdict"] = term_cls
        report["terminal_details"] = term_det
        report["verdict"] = "DRY_RUN_PASS"

        sanitized_report = sanitize_report_for_persistence(report)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as df:
            json.dump(sanitized_report, df, indent=2, ensure_ascii=False)
        logger.info(f"💾 [DRY_RUN_PASS] Dry run completed successfully. Persisted to {output_file}")
        return report

    # -----------------------------------------------------------------------
    # LIVE EXECUTION PATH (Guarded One-Shot Consumption)
    # -----------------------------------------------------------------------
    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "LIVE_ARTIFACT_ALREADY_EXISTS"
        report["error"] = f"Live artifact already exists at {output_file}. Replay forbidden."
        return report

    if marker_file.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_MARKER_ALREADY_EXISTS"
        report["error"] = f"Attempt marker already exists at {marker_file}. One-shot consumption violated."
        return report

    # Atomically write attempt marker
    try:
        fd = os.open(str(marker_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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
        logger.info(f"🔒 [ONE_SHOT_CONSUMED] Wrote attempt marker to {marker_file}")
    except FileExistsError:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED_RACE"
        report["error"] = "Attempt marker already created concurrently. Aborting."
        return report

    host: Optional[Any] = None
    backend: Optional[Any] = None

    try:
        report["harness_phases"].append("CONNECTING_WEBVIEW")
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (SSA1)...")

        call_spec = build_qsc2_so1_runtime_call_spec(timeout=timeout)

        if host_factory:
            host = host_factory(**call_spec["host_kwargs"])
        else:
            host = call_spec["host_cls"](**call_spec["host_kwargs"])

        config = call_spec["config_cls"](**call_spec["config_kwargs"])

        if backend_factory:
            backend = backend_factory(**call_spec["backend_kwargs_factory"](config, host))
        else:
            backend = call_spec["backend_cls"](**call_spec["backend_kwargs_factory"](config, host))

        start_method = getattr(backend, call_spec["start_runtime_method"])
        start_method(**call_spec["start_runtime_kwargs"])
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

        # Step 4: Model-Native Texty Query Setter
        report["live_action_inventory"]["query_entries"] = 1
        js_set = f"({JS_SET_TEXTY_QUERY})({json.dumps(TARGET_QUERY)})"
        res_set = host.evaluate_js(js_set, timeout=10.0)
        if not validate_query_setter_response(res_set):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "QUERY_SETTER_RESPONSE_CONTRACT_INVALID"
            report["error"] = f"Model-native texty query setter failed contract validation: {res_set}"
            raise RuntimeError(report["error"])
        report["checks"]["query_entry_executed"] = True

        # Step 4b: Bounded Pure Read-Only Suggestion Surface Observation
        # STRICT ZERO SUBMIT: NO onEnter, NO click, NO enter key event
        report["harness_phases"].append("BOUNDED_SUGGESTION_OBSERVATION")
        report["checks"]["bounded_observation_executed"] = True

        polls = []
        js_obs = f"({JS_OBSERVE_SEARCH_SUGGESTION_SURFACE})({json.dumps(TARGET_QUERY)})"

        obs_start_mono = time.monotonic()
        deadline_mono = obs_start_mono + (SUGGESTION_OBSERVATION_TIMEOUT_MS / 1000.0)

        for poll_idx in range(1, MAX_SUGGESTION_OBSERVATION_POLLS + 1):
            now_mono = time.monotonic()
            if now_mono >= deadline_mono:
                break

            elapsed_ms = round((now_mono - obs_start_mono) * 1000.0, 2)
            poll_res = host.evaluate_js(js_obs, timeout=5.0)

            if isinstance(poll_res, dict):
                obs_valid = bool(poll_res.get("observation_valid", False))
                is_safe = bool(poll_res.get("safe_search_candidate_present", False))
                is_sync = bool(poll_res.get("query_state_all_synced", False))
                flags_ok = bool(poll_res.get("internal_flags_ok", False))
                surfaces = poll_res.get("surfaces_synced", {})

                poll_rec = {
                    "poll_index": poll_idx,
                    "elapsed_ms": elapsed_ms,
                    "observation_valid": obs_valid,
                    "validity_failure_reason": poll_res.get("validity_failure_reason"),
                    "search_input_preconditions_satisfied": bool(poll_res.get("search_input_preconditions_satisfied", False)),
                    "query_state_all_synced": is_sync if obs_valid else None,
                    "internal_flags_ok": flags_ok if obs_valid else None,
                    "search_input_query_matches_requested": bool(surfaces.get("dom", False)) if obs_valid else None,
                    "quill_text_synced": bool(surfaces.get("quill_text", False)) if obs_valid else None,
                    "quill_delta_synced": bool(surfaces.get("quill_delta", False)) if obs_valid else None,
                    "texty_model_synced": bool(surfaces.get("texty", False)) if obs_valid else None,
                    "display_query_ops_synced": bool(surfaces.get("display_query_ops", surfaces.get("redux", False))) if obs_valid else None,
                    "redux_state_synced": bool(surfaces.get("display_query_ops", surfaces.get("redux", False))) if obs_valid else None,
                    "suggestion_surface_present": bool(poll_res.get("suggestion_surface_present", False)),
                    "connected_candidate_count": int(poll_res.get("connected_candidate_count", 0)),
                    "safe_candidate_count": int(poll_res.get("safe_candidate_count", 0)),
                    "unsafe_candidate_count": int(poll_res.get("unsafe_candidate_count", 0)),
                    "safe_search_candidate_present": is_safe,
                    "explicit_search_candidate_present": bool(poll_res.get("explicit_search_candidate_present", False)),
                    "candidate_classifications": poll_res.get("candidate_classifications", []),
                    "normalized_candidate_classifications": poll_res.get("normalized_candidate_classifications", []),
                    "default_candidate_classification": poll_res.get("default_candidate_classification", "NONE"),
                    "selected_candidate_classification": poll_res.get("selected_candidate_classification", "NONE"),
                    "safe_candidate_exact_query_correlation": poll_res.get("safe_candidate_exact_query_correlation", "UNPROVEN"),
                    "coexistence_observed": bool(poll_res.get("coexistence_observed", False)),
                    "candidate_summaries": poll_res.get("candidate_summaries", []),
                }
                polls.append(poll_rec)

                # Coexistence early stop: observation_valid AND is_sync AND flags_ok AND is_safe AND not mismatch in SAME poll
                if obs_valid and is_sync and flags_ok and is_safe and poll_res.get("safe_candidate_exact_query_correlation") != "MISMATCH":
                    break
            else:
                polls.append({
                    "poll_index": poll_idx,
                    "elapsed_ms": elapsed_ms,
                    "observation_valid": False,
                    "validity_failure_reason": f"NON_DICT_EVALUATE_JS_RETURN: {type(poll_res)}",
                    "query_state_all_synced": None,
                    "internal_flags_ok": None,
                    "safe_search_candidate_present": False,
                })

            remaining = deadline_mono - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(POLL_INTERVAL_MS / 1000.0, remaining))

        total_obs_elapsed_ms = round((time.monotonic() - obs_start_mono) * 1000.0, 2)
        persistence_telemetry = analyze_candidate_observational_persistence(polls)

        report["observation_polls"] = polls
        report["checks"]["query_state_all_synced"] = any(p.get("query_state_all_synced") for p in polls)
        report["observation_telemetry"] = {
            "observation_timeout_ms": SUGGESTION_OBSERVATION_TIMEOUT_MS,
            "poll_interval_ms": POLL_INTERVAL_MS,
            "max_polls": MAX_SUGGESTION_OBSERVATION_POLLS,
            "actual_poll_count": len(polls),
            "actual_observation_elapsed_ms": total_obs_elapsed_ms,
            **persistence_telemetry,
        }

        term_cls, term_det = classify_qsc2_so1_terminal_state(polls, error=None)
        report["terminal_classification"] = term_cls
        report["diagnostic_verdict"] = term_cls
        report["terminal_details"] = term_det
        report["verdict"] = "QUALIFICATION_COMPLETED"

    except Exception as ex:
        logger.error(f"SSA1-R2 diagnostic execution exception: {ex}")
        report["verdict"] = "FAIL_CLOSED"
        if report.get("diagnostic_verdict") in ("UNSET", None):
            report["diagnostic_verdict"] = "SEARCH_EXECUTION_EXCEPTION"
        report["error"] = str(ex)

    finally:
        report["harness_phases"].append("CLEANUP_AND_SHUTDOWN")
        if host:
            try:
                logger.info("🛑 [SHUTDOWN] Releasing WebView host...")
                if hasattr(host, "close"):
                    host.close()
            except Exception as e:
                logger.warning(f"Error during host shutdown: {e}")

    # Persist Sanitized Live Artifact
    sanitized = sanitize_report_for_persistence(report)
    with open(output_file, "w", encoding="utf-8") as out:
        json.dump(sanitized, out, indent=2, ensure_ascii=False)
    logger.info(f"💾 [SAVED_TRACE] Written SSA1 live trace to {output_file}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="PESU QSC2-SO1 Safe Search Suggestion Availability Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Execute dry run with synthetic candidate observations")
    parser.add_argument("--timeout", type=float, default=60.0, help="Runtime startup timeout in seconds")
    args = parser.parse_args()

    res = run_qsc2_so1_qualification(dry_run=args.dry_run, timeout=args.timeout)

    print("\n============================================================")
    print("QSC2-SO1 QUALIFICATION EXECUTION SUMMARY")
    print("============================================================")
    print(f"VERDICT:                  {res.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT:       {res.get('diagnostic_verdict')}")
    print(f"TERMINAL_CLASSIFICATION:  {res.get('terminal_classification')}")
    print(f"ERROR:                    {res.get('error')}")
    print("============================================================\n")

    if res.get("verdict") not in {"QUALIFICATION_COMPLETED", "DRY_RUN_PASS"}:
        sys.exit(1)


if __name__ == "__main__":
    main()
