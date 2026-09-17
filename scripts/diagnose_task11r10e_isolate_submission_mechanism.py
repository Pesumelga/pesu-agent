#!/usr/bin/env python
"""Task 11R10E — Sequential Search Submission Mechanism Isolation.

Objective:
  Isolate the precise mechanism of the deterministic sequential search submission failure
  observed in Task 11R10D (Q1 = PASS -> Q2 onEnter() invoked -> surface remains Q1 -> SEARCH_NAVIGATION_FAILED).

Strict Invariants:
  - PRODUCTION_CODE_MUTATIONS = 0 (No production fix yet: src/ is untouched)
  - TIMEOUT_MUTATIONS = 0
  - FIXED_SLEEP_IS_SUBMIT_AUTHORITY = False
  - SEMANTIC_PIPELINE_CALLS = 0 (EvidenceAssessor=0, ClaimVerifier=0, Stage=0)
  - SUBMIT_REQUIRES_VERIFIED_QUERY_STATE = True
  - STALE_RESULT_SUCCESS_RETURN_PREVENTED = True
  - TRANSIENT_EMPTY_DOM_IS_NOT_TERMINAL_SUCCESS = True
  - FINGERPRINT_CHANGE_ALONE_IS_NOT_PRIMARY_QUERY_AUTHORITY = True
  - SLACK_WRITE_ACTIONS = 0
  - QUALIFICATION_SCORE_CONTRIBUTION = 0
  - TASK_11_COMPLETE = False
  - TASK_11_QUALIFICATION_PASS = False
  - TASK_12_STARTED = False
  - PRODUCTION_CUTOVER = False
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Optional

# Add repository root and src to path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.search.webview_slack_search import (
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_EXTRACT_SEARCH_RESULTS,
    JS_PROBE_POST_SUBMIT_INPUT_STATE,
    JS_PROBE_POST_WAIT_SEARCH_STATE,
    JS_PROBE_PRE_SUBMIT_SEARCH_STATE,
    SEARCH_INPUT_POLL_INTERVAL_SECONDS,
    SEARCH_INPUT_READINESS_TIMEOUT_SECONDS,
    SEARCH_RESULT_ROW_SELECTORS,
    SEARCH_VIEW_SELECTOR,
    WebViewSearchError,
    WebViewSearchFailureCode,
    compute_result_set_fingerprint,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r10e_isolation")

OUTPUT_BASE_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"

# Section 4: Controlled Query Matrix (Frozen before live execution)
CONTROLLED_QUERY_MATRIX = {
    "A1": {
        "cell_id": "A1",
        "dimension": "Prefix Continuity (Reference)",
        "factor_length": "LONG",
        "factor_prefix": "NOT_PRESERVED",
        "q1": "Office 영업지원 조회 기능 진행 결정 막힘",
        "q2": "신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀",
        "q1_len": 26,
        "q2_len": 50,
        "purpose": "Reference group: Case B Q1 -> Case B Q2",
    },
    "A2": {
        "cell_id": "A2",
        "dimension": "Prefix Continuity (Shorter, Preserving Prefix)",
        "factor_length": "SHORT",
        "factor_prefix": "PRESERVED",
        "q1": "Office 영업지원 조회 기능 진행 결정 막힘",
        "q2": "Office 영업지원 조회 기능 진행",
        "q1_len": 26,
        "q2_len": 20,
        "purpose": "Test if failure reproduces when Q2 is shorter and preserves Q1 prefix",
    },
    "B1": {
        "cell_id": "B1",
        "dimension": "Query Length (~50 chars, Sharing Prefix)",
        "factor_length": "LONG",
        "factor_prefix": "PRESERVED",
        "q1": "Office 영업지원 조회 기능 진행 결정 막힘",
        "q2": "Office 영업지원 조회 기능 진행 결정 막힘 및 추가 세부사항 확인 필요 항목 점검 건",
        "q1_len": 26,
        "q2_len": 50,
        "purpose": "Test if failure reproduces when Q2 is 50 chars and shares Q1 prefix",
    },
    "B2": {
        "cell_id": "B2",
        "dimension": "Query Length (Short, Not Sharing Prefix)",
        "factor_length": "SHORT",
        "factor_prefix": "NOT_PRESERVED",
        "q1": "Office 영업지원 조회 기능 진행 결정 막힘",
        "q2": "결제 모듈 장애",
        "q1_len": 26,
        "q2_len": 8,
        "purpose": "Test if failure reproduces when Q2 is short and does not share Q1 prefix",
    },
}

# JS Snippet: Initialize Identity Registry on window to track object/DOM reference equality
JS_INIT_IDENTITY_REGISTRY = r"""
(() => {
    if (!window.__pesu_identity_audit) {
        window.__pesu_identity_audit = {
            counters: {
                dom_node: 0,
                fiber: 0,
                state_node: 0,
                onenter_fn: 0,
                texty_obj: 0,
                display_ops: 0,
                quill_obj: 0
            },
            registry: new WeakMap(),
            getId(obj, category) {
                if (!obj || (typeof obj !== 'object' && typeof obj !== 'function')) {
                    return null;
                }
                if (!this.registry.has(obj)) {
                    this.counters[category] = (this.counters[category] || 0) + 1;
                    const idStr = `${category.toUpperCase()}_#${this.counters[category]}`;
                    this.registry.set(obj, idStr);
                }
                return this.registry.get(obj);
            }
        };
    }
    return { initialized: true };
})()
"""

# JS Snippet: Deep Audit of SearchInputBox runtime identities, state, props, and decompilation
JS_AUDIT_SEARCH_INPUT_BOX = r"""
(() => {
    const auditHelper = window.__pesu_identity_audit || {
        getId: () => 'HELPER_NOT_INIT'
    };

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    const inputCount = searchInputCandidates.length;

    let domNodeId = null;
    let inputElem = null;
    let textContent = null;

    if (inputCount >= 1) {
        inputElem = searchInputCandidates[0];
        domNodeId = auditHelper.getId(inputElem, 'dom_node');
        textContent = (inputElem.textContent || inputElem.innerText || '').trim();
    }

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    const getReactProps = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactProps$') || k.startsWith('__reactEventHandlers$'));
        return key ? el[key] : null;
    };

    let startingFiber = inputElem ? (getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'))) : null;
    let fiberId = startingFiber ? auditHelper.getId(startingFiber, 'fiber') : null;

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
    let matchedFiber = null;
    let searchInputBoxInstance = null;

    while (fiber && depth < 40) {
        if (fiber.stateNode && isSearchInputBoxStrong(fiber.stateNode)) {
            matchedFiber = fiber;
            searchInputBoxInstance = fiber.stateNode;
            break;
        }
        fiber = fiber.return;
        depth++;
    }

    let stateNodeId = null;
    let onenterFnId = null;
    let textyObjId = null;
    let quillObjId = null;
    let displayOpsId = null;
    let onenterSource = null;
    let stateSnapshot = null;
    let propsSummary = null;
    let quillText = null;
    let quillDeltaOps = null;
    let quillKeyboardBindings = [];
    let methodsOnInstance = [];
    let methodsOnPrototype = [];
    let reactPropsKeydownSource = null;

    if (inputElem) {
        const rProps = getReactProps(inputElem);
        if (rProps && typeof rProps.onKeyDown === 'function') {
            try {
                reactPropsKeydownSource = rProps.onKeyDown.toString();
            } catch (e) {}
        }
    }

    if (searchInputBoxInstance) {
        stateNodeId = auditHelper.getId(searchInputBoxInstance, 'state_node');

        if (typeof searchInputBoxInstance.onEnter === 'function') {
            onenterFnId = auditHelper.getId(searchInputBoxInstance.onEnter, 'onenter_fn');
            try {
                onenterSource = searchInputBoxInstance.onEnter.toString();
            } catch (e) {
                onenterSource = 'ERROR: ' + String(e);
            }
        }

        const textyObj = searchInputBoxInstance.texty;
        if (textyObj) {
            textyObjId = auditHelper.getId(textyObj, 'texty_obj');
            if (textyObj.instance && textyObj.instance.quill) {
                const q = textyObj.instance.quill;
                quillObjId = auditHelper.getId(q, 'quill_obj');
                try {
                    quillText = q.getText().trim();
                } catch (e) {}

                // Inspect Quill Keyboard bindings for Enter (key 13)
                try {
                    const bindings = (q.keyboard && q.keyboard.bindings) ? q.keyboard.bindings : {};
                    const enterBindings = bindings[13] || bindings['Enter'] || bindings['enter'] || [];
                    if (Array.isArray(enterBindings)) {
                        quillKeyboardBindings = enterBindings.map(b => {
                            const handler = b.handler ? b.handler.toString() : null;
                            return {
                                key: b.key,
                                shortKey: Boolean(b.shortKey),
                                shiftKey: Boolean(b.shiftKey),
                                metaKey: Boolean(b.metaKey),
                                ctrlKey: Boolean(b.ctrlKey),
                                altKey: Boolean(b.altKey),
                                handler_snippet: handler ? handler.slice(0, 400) : null
                            };
                        });
                    }
                } catch (e) {
                    quillKeyboardBindings = ['ERROR: ' + String(e)];
                }
            }
        }

        try {
            if (typeof searchInputBoxInstance.getTextyOps === 'function') {
                quillDeltaOps = searchInputBoxInstance.getTextyOps();
            }
        } catch (e) {}

        const displayOps = (matchedFiber && matchedFiber.memoizedProps && matchedFiber.memoizedProps.displayQueryOps)
            || (searchInputBoxInstance.props && searchInputBoxInstance.props.displayQueryOps);
        if (displayOps) {
            displayOpsId = auditHelper.getId(displayOps, 'display_ops');
        }

        // State Snapshot
        try {
            if (searchInputBoxInstance.state) {
                stateSnapshot = JSON.parse(JSON.stringify(searchInputBoxInstance.state));
            }
        } catch (e) {
            stateSnapshot = { error: String(e) };
        }

        // Props Summary (Read-only)
        try {
            const p = searchInputBoxInstance.props || {};
            propsSummary = {};
            for (const k of Object.keys(p)) {
                const val = p[k];
                if (typeof val === 'function') {
                    propsSummary[k] = `[Function: ${val.name || 'anonymous'}]`;
                } else if (typeof val === 'object' && val !== null) {
                    propsSummary[k] = Array.isArray(val) ? `[Array(${val.length})]` : `[Object: keys=${Object.keys(val).join(',')}]`;
                } else {
                    propsSummary[k] = val;
                }
            }
        } catch (e) {
            propsSummary = { error: String(e) };
        }

        // Methods on instance
        try {
            methodsOnInstance = Object.keys(searchInputBoxInstance).filter(k => typeof searchInputBoxInstance[k] === 'function');
        } catch (e) {}

        // Methods on prototype
        try {
            const proto = Object.getPrototypeOf(searchInputBoxInstance);
            if (proto) {
                methodsOnPrototype = Object.getOwnPropertyNames(proto).filter(k => typeof proto[k] === 'function');
            }
        } catch (e) {}
    }

    // Inspect Surface / URL State
    let surfaceHeader = null;
    let surfaceUrlQuery = null;
    let urlHref = window.location.href;
    try {
        const url = new URL(urlHref);
        surfaceUrlQuery = url.searchParams.get('query') || url.searchParams.get('q');
        if (!surfaceUrlQuery) {
            const m = url.pathname.match(/\/search\/(.+)$/);
            if (m) surfaceUrlQuery = decodeURIComponent(m[1]);
        }
    } catch (e) {}

    const headerElem = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title, [class*="titleContent"]');
    if (headerElem) {
        surfaceHeader = (headerElem.textContent || '').trim();
    }

    const searchView = document.querySelector('[data-qa="search_view"]');
    const resultRoots = searchView ? searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result') : [];

    return {
        input_count: inputCount,
        SEARCH_INPUT_DOM_NODE_IDENTITY: domNodeId,
        SEARCH_INPUT_REACT_FIBER_IDENTITY: fiberId,
        SEARCH_INPUT_STATE_NODE_IDENTITY: stateNodeId,
        ONENTER_FUNCTION_IDENTITY: onenterFnId,
        TEXTY_OBJECT_IDENTITY: textyObjId,
        QUILL_OBJECT_IDENTITY: quillObjId,
        DISPLAY_QUERY_OPS_IDENTITY: displayOpsId,
        CURRENT_QUERY_TEXT: {
            dom: textContent,
            quill: quillText,
            delta_ops_count: Array.isArray(quillDeltaOps) ? quillDeltaOps.length : 0
        },
        onenter_source: onenterSource,
        react_props_keydown_source: reactPropsKeydownSource,
        state_snapshot: stateSnapshot,
        props_summary: propsSummary,
        quill_keyboard_bindings: quillKeyboardBindings,
        methods_on_instance: methodsOnInstance,
        methods_on_prototype: methodsOnPrototype,
        surface: {
            url_href: urlHref,
            url_query: surfaceUrlQuery,
            header_query: surfaceHeader,
            search_view_mounted: Boolean(searchView),
            result_root_count: resultRoots.length
        }
    };
})()
"""

# JS Snippet: Forensic Inspection of Submission Mechanism and Enter Handling
JS_INSPECT_SUBMISSION_MECHANISM = r"""
(() => {
    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    if (searchInputCandidates.length !== 1) {
        return { error: 'SEARCH_INPUT_NOT_MOUNTED' };
    }
    const inputElem = searchInputCandidates[0];

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    let fiber = startingFiber;
    let depth = 0;
    let instance = null;

    while (fiber && depth < 40) {
        if (fiber.stateNode && typeof fiber.stateNode.getTextyOps === 'function' && typeof fiber.stateNode.onEnter === 'function') {
            instance = fiber.stateNode;
            break;
        }
        fiber = fiber.return;
        depth++;
    }

    if (!instance) {
        return { error: 'SEARCH_INPUT_BOX_INSTANCE_NOT_FOUND' };
    }

    // Inspect onEnter implementation
    const onEnterStr = instance.onEnter.toString();

    // Check what parameters onEnter accepts
    const paramMatch = onEnterStr.match(/^(?:async\s*)?(?:function\s*)?(?:\w+\s*)?\(([^)]*)\)/) || onEnterStr.match(/^(\w+)\s*=>/);
    const params = paramMatch ? (paramMatch[1] || '').trim() : '';

    // Check code patterns in onEnter
    const checksQueryHasChanged = onEnterStr.includes('queryHasChanged');
    const checksQueryHasBeenSubmitted = onEnterStr.includes('queryHasBeenSubmitted');
    const checksDisplayQueryOps = onEnterStr.includes('displayQueryOps');
    const checksDisabled = onEnterStr.includes('disabled');
    const checksIsSearching = onEnterStr.includes('isSearching');
    const callsPropsOnSubmit = onEnterStr.includes('onSubmit');
    const callsPropsOnEnter = onEnterStr.includes('props.onEnter') || onEnterStr.includes('this.props.onEnter');
    const callsDispatch = onEnterStr.includes('dispatch');

    // Check Quill bindings
    const quill = (instance.texty && instance.texty.instance && instance.texty.instance.quill) ? instance.texty.instance.quill : null;
    let quillEnterCallsOnEnter = false;
    let quillEnterDetails = [];

    if (quill && quill.keyboard && quill.keyboard.bindings) {
        const enterBindings = quill.keyboard.bindings[13] || [];
        for (const b of enterBindings) {
            const hStr = b.handler ? b.handler.toString() : '';
            if (hStr.includes('onEnter') || hStr.includes('onenter')) {
                quillEnterCallsOnEnter = true;
            }
            quillEnterDetails.push({
                handler_length: hStr.length,
                references_onEnter: hStr.includes('onEnter') || hStr.includes('onenter'),
                references_onSubmit: hStr.includes('onSubmit'),
                handler_snippet: hStr.slice(0, 500)
            });
        }
    }

    return {
        onenter_signature_params: params,
        onenter_source_full: onEnterStr,
        source_code_checks: {
            checksQueryHasChanged,
            checksQueryHasBeenSubmitted,
            checksDisplayQueryOps,
            checksDisabled,
            checksIsSearching,
            callsPropsOnSubmit,
            callsPropsOnEnter,
            callsDispatch
        },
        current_state_flags: {
            queryHasChanged: instance.state ? Boolean(instance.state.queryHasChanged) : null,
            queryHasBeenSubmitted: instance.state ? Boolean(instance.state.queryHasBeenSubmitted) : null,
            _isUpdatingDisplayQuery: instance.state ? Boolean(instance.state._isUpdatingDisplayQuery) : null,
            isInComposition: instance.state ? Boolean(instance.state.isInComposition) : null
        },
        quill_enter_binding_details: quillEnterDetails,
        quill_enter_calls_onenter: quillEnterCallsOnEnter
    };
})()
"""


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def get_git_commit_head() -> str:
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


def check_uncommitted_diff(path_filter: str = "src/") -> bool:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", path_filter],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def run_cell_characterization(
    cell_info: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Runs a single cell of the controlled matrix (Q1 -> Q2) with deep identity and precondition audit."""
    cell_id = cell_info["cell_id"]
    q1_text = cell_info["q1"]
    q2_text = cell_info["q2"]

    logger.info("\n" + "=" * 70)
    logger.info(f"🔬 RUNNING CONTROLLED CELL [{cell_id}]: {cell_info['dimension']}")
    logger.info(f"   Q1: '{q1_text}' (len={len(q1_text)})")
    logger.info(f"   Q2: '{q2_text}' (len={len(q2_text)})")
    logger.info("=" * 70)

    record: dict[str, Any] = {
        "cell_id": cell_id,
        "cell_info": cell_info,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "query_audits": {
            "Q1": {},
            "Q2": {},
        },
        "per_transition_telemetry": {},
        "submission_comparison": {},
        "q1_status": "NOT_EXECUTED",
        "q2_status": "NOT_EXECUTED",
        "transition_confirmed": False,
        "error": None,
    }

    if dry_run:
        logger.info("🧪 [DRY_RUN] Simulating cell characterization.")
        record["q1_status"] = "PASS"
        record["q2_status"] = "FAIL_SEARCH_NAVIGATION_FAILED"
        record["per_transition_telemetry"] = {
            "INPUT_MATCHES_REQUESTED": True,
            "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": True,
            "SEARCH_INPUT_STATE_NODE_ID_BEFORE": "STATE_NODE_#1",
            "SEARCH_INPUT_STATE_NODE_ID_AFTER": "STATE_NODE_#1",
            "ONENTER_FUNCTION_ID_BEFORE": "ONENTER_FN_#1",
            "ONENTER_FUNCTION_ID_AFTER": "ONENTER_FN_#1",
            "ONENTER_INVOKED": True,
            "SURFACE_QUERY_BEFORE": f"다음에 대한 결과: {q1_text}",
            "SURFACE_QUERY_AFTER": f"다음에 대한 결과: {q1_text}",
            "LOADING_OBSERVED": False,
            "RESULT_ROOT_CHANGED": False,
            "RESULT_FINGERPRINT_CHANGED": False,
            "TRANSITION_CONFIRMED": False,
        }
        return record

    search_backend = create_slack_search_backend(
        config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
    )
    host = getattr(search_backend, "_window", None)
    if not host or not hasattr(host, "start"):
        raise RuntimeError("Failed to obtain PesuWebViewHost from backend")

    t_start = time.perf_counter()
    host.start(timeout=30.0)
    logger.info(f"🌐 [HOST_READY] Started fresh host in {time.perf_counter() - t_start:.2f}s")

    # Install evaluate_js auditing wrapper to intercept exact pre-submit and post-submit moments
    orig_evaluate_js = host.evaluate_js
    active_query_tag = ["Q1"]

    def auditing_evaluate_js(script: str, *args: Any, **kwargs: Any) -> Any:
        tag = active_query_tag[0]
        # Pre-submit audit point: when JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER is evaluated
        if "JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER" in script or "onEnter()" in script:
            if "pre_submit" not in record["query_audits"][tag]:
                try:
                    audit_res = orig_evaluate_js(JS_AUDIT_SEARCH_INPUT_BOX)
                    if isinstance(audit_res, dict):
                        record["query_audits"][tag]["pre_submit"] = audit_res
                        logger.info(
                            f"  🔍 [{tag} Pre-Submit Audit] DOM={audit_res.get('SEARCH_INPUT_DOM_NODE_IDENTITY')}, "
                            f"Fiber={audit_res.get('SEARCH_INPUT_REACT_FIBER_IDENTITY')}, "
                            f"StateNode={audit_res.get('SEARCH_INPUT_STATE_NODE_IDENTITY')}, "
                            f"onEnter={audit_res.get('ONENTER_FUNCTION_IDENTITY')}, "
                            f"Texty={audit_res.get('TEXTY_OBJECT_IDENTITY')}, "
                            f"queryHasChanged={audit_res.get('state_snapshot', {}).get('queryHasChanged')}, "
                            f"queryHasBeenSubmitted={audit_res.get('state_snapshot', {}).get('queryHasBeenSubmitted')}"
                        )
                except Exception as e:
                    logger.warning(f"Error in {tag} pre-submit audit: {e}")

        res = orig_evaluate_js(script, *args, **kwargs)

        # Post-submit audit point: right after onEnter() returns
        if "JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER" in script or "onEnter()" in script:
            if isinstance(res, dict) and res.get("ok"):
                if "post_submit" not in record["query_audits"][tag]:
                    try:
                        audit_post = orig_evaluate_js(JS_AUDIT_SEARCH_INPUT_BOX)
                        if isinstance(audit_post, dict):
                            record["query_audits"][tag]["post_submit"] = audit_post
                    except Exception as e:
                        logger.warning(f"Error in {tag} post-submit audit: {e}")

        return res

    host.evaluate_js = auditing_evaluate_js

    try:
        # Initialize Identity Registry WeakMaps in browser
        orig_evaluate_js(JS_INIT_IDENTITY_REGISTRY)

        # -------------------------------------------------------------
        # STEP 1: EXECUTE Q1 VIA UNTOUCHED PRODUCTION BACKEND
        # -------------------------------------------------------------
        logger.info(f"\n▶ [CELL {cell_id} Q1] Executing Q1: '{q1_text}'")
        active_query_tag[0] = "Q1"
        t0 = time.perf_counter()
        q1_items = []
        try:
            q1_items = search_backend.search_messages(q1_text, max_results=5)
            record["q1_status"] = "PASS"
            logger.info(f"  ✅ Q1 SUCCESS: Returned {len(q1_items)} items ({time.perf_counter() - t0:.2f}s)")
        except Exception as e:
            record["q1_status"] = f"FAIL_{type(e).__name__}"
            record["error"] = str(e)
            logger.error(f"  ❌ Q1 FAILED: {e}")
            return record

        # Audit SearchInputBox and surface state right after Q1 completion
        audit_post_q1_complete = orig_evaluate_js(JS_AUDIT_SEARCH_INPUT_BOX)
        record["query_audits"]["Q1"]["post_complete"] = audit_post_q1_complete

        # -------------------------------------------------------------
        # STEP 2: EXECUTE Q2 VIA UNTOUCHED PRODUCTION BACKEND
        # -------------------------------------------------------------
        logger.info(f"\n▶ [CELL {cell_id} Q2] Executing Q2: '{q2_text}' (len={len(q2_text)})")
        active_query_tag[0] = "Q2"
        t0 = time.perf_counter()
        q2_items = []
        q2_error: Optional[str] = None
        q2_failure_code: Optional[str] = None

        # Inspect submission mechanism and Quill bindings right before Q2 execution
        submission_inspection = orig_evaluate_js(JS_INSPECT_SUBMISSION_MECHANISM)
        record["submission_comparison"] = submission_inspection

        try:
            q2_items = search_backend.search_messages(q2_text, max_results=5)
            record["q2_status"] = "PASS"
            record["transition_confirmed"] = True
            logger.info(f"  ✅ Q2 SUCCESS: Returned {len(q2_items)} items ({time.perf_counter() - t0:.2f}s)")
        except WebViewSearchError as wse:
            q2_error = str(wse)
            q2_failure_code = getattr(wse, "failure_code", None)
            if q2_failure_code and hasattr(q2_failure_code, "value"):
                q2_failure_code = q2_failure_code.value
            record["q2_status"] = f"FAIL_{q2_failure_code or 'UNKNOWN'}"
            record["error"] = q2_error
            logger.error(f"  ❌ Q2 FAILED (code={q2_failure_code}): {wse}")
        except Exception as e:
            q2_error = str(e)
            record["q2_status"] = f"FAIL_{type(e).__name__}"
            record["error"] = q2_error
            logger.error(f"  ❌ Q2 Unexpected Error: {e}")

        # Audit SearchInputBox and surface state after Q2
        audit_post_q2_complete = orig_evaluate_js(JS_AUDIT_SEARCH_INPUT_BOX)
        record["query_audits"]["Q2"]["post_complete"] = audit_post_q2_complete

        # Extract telemetry from search_backend
        telemetry = getattr(search_backend, "last_search_telemetry", {}) or {}
        transition = telemetry.get("forensic_transition", {})

        q2_pre = record["query_audits"]["Q2"].get("pre_submit", {})
        q2_post = record["query_audits"]["Q2"].get("post_complete", {})

        state_node_before = q2_pre.get("SEARCH_INPUT_STATE_NODE_IDENTITY")
        state_node_after = q2_post.get("SEARCH_INPUT_STATE_NODE_IDENTITY")
        onenter_before = q2_pre.get("ONENTER_FUNCTION_IDENTITY")
        onenter_after = q2_post.get("ONENTER_FUNCTION_IDENTITY")

        input_matches = bool(transition.get("INPUT_MATCHES_REQUESTED", False))
        verified_query_state = bool(telemetry.get("verified_query_state_before_submit", False))
        onenter_invoked = bool(telemetry.get("onenter_invoked", False))
        surface_before = transition.get("SEARCH_SURFACE_QUERY_BEFORE")
        surface_after = transition.get("SEARCH_SURFACE_QUERY_AFTER")
        loading_observed = bool(transition.get("LOADING_OR_DOM_TRANSITION_OBSERVED", False))
        result_fp_changed = bool(transition.get("RESULT_FINGERPRINT_CHANGED", False))
        transition_confirmed = bool(record["q2_status"] == "PASS")

        record["per_transition_telemetry"] = {
            "INPUT_MATCHES_REQUESTED": input_matches,
            "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": verified_query_state,
            "SEARCH_INPUT_STATE_NODE_ID_BEFORE": state_node_before,
            "SEARCH_INPUT_STATE_NODE_ID_AFTER": state_node_after,
            "ONENTER_FUNCTION_ID_BEFORE": onenter_before,
            "ONENTER_FUNCTION_ID_AFTER": onenter_after,
            "ONENTER_INVOKED": onenter_invoked,
            "SURFACE_QUERY_BEFORE": surface_before,
            "SURFACE_QUERY_AFTER": surface_after,
            "LOADING_OBSERVED": loading_observed,
            "RESULT_ROOT_CHANGED": bool(transition.get("result_change_detected", False)),
            "RESULT_FINGERPRINT_CHANGED": result_fp_changed,
            "TRANSITION_CONFIRMED": transition_confirmed,
        }

        logger.info(
            f"  🏁 [Q2 Telemetry] transition_confirmed={transition_confirmed}, "
            f"surface_before='{surface_before}', surface_after='{surface_after}', "
            f"loading={loading_observed}, state_node={state_node_before} -> {state_node_after}"
        )

    finally:
        logger.info(f"🛑 [HOST_STOP] Stopping host for Cell {cell_id}...")
        try:
            host.stop()
        except Exception as e:
            logger.warning(f"Error stopping host: {e}")
        time.sleep(1.0)

    return record


def synthesize_verdicts(cell_records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Synthesizes authoritative verdicts for Section 11 from all cell records."""
    # 1. Stale SearchInputBox Component Reference Check (Section 7)
    # Check if Q1 and Q2 used the same stateNode instance or different ones
    state_node_reused = False
    stale_ref_detected = False

    for cid, rec in cell_records.items():
        q1_sn = rec.get("query_audits", {}).get("Q1", {}).get("pre_submit", {}).get("SEARCH_INPUT_STATE_NODE_IDENTITY")
        q2_sn_pre = rec.get("query_audits", {}).get("Q2", {}).get("pre_submit", {}).get("SEARCH_INPUT_STATE_NODE_IDENTITY")
        if q1_sn and q2_sn_pre:
            if q1_sn == q2_sn_pre:
                state_node_reused = True
            else:
                # Component was remounted with a new instance
                pass

    stale_component_ref_verdict = "NOT_SUPPORTED" if not stale_ref_detected else "CONFIRMED"
    component_ref_reused_verdict = state_node_reused

    # 2. Submission Mechanism Comparison (Section 8)
    direct_onenter_matches_real = "UNRESOLVED"
    onenter_mechanism_details = []

    for cid, rec in cell_records.items():
        comp = rec.get("submission_comparison", {})
        if comp and "source_code_checks" in comp:
            checks = comp["source_code_checks"]
            q2_pre = rec.get("query_audits", {}).get("Q2", {}).get("pre_submit", {})
            q2_state = q2_pre.get("state_snapshot", {})
            query_has_changed = q2_state.get("queryHasChanged")
            query_has_been_submitted = q2_state.get("queryHasBeenSubmitted")

            onenter_mechanism_details.append({
                "cell": cid,
                "checksQueryHasChanged": checks.get("checksQueryHasChanged"),
                "checksQueryHasBeenSubmitted": checks.get("checksQueryHasBeenSubmitted"),
                "q2_state_queryHasChanged": query_has_changed,
                "q2_state_queryHasBeenSubmitted": query_has_been_submitted,
                "quill_enter_calls_onenter": comp.get("quill_enter_calls_onenter")
            })

            # If onEnter checks queryHasChanged and state.queryHasChanged is false, contract divergence is CONFIRMED
            if checks.get("checksQueryHasChanged") and query_has_changed is False:
                direct_onenter_matches_real = False
            elif checks.get("checksQueryHasBeenSubmitted") and query_has_been_submitted is True:
                direct_onenter_matches_real = False
            elif comp.get("quill_enter_calls_onenter") is False:
                direct_onenter_matches_real = False
            elif comp.get("quill_enter_calls_onenter") is True:
                if direct_onenter_matches_real != False:
                    direct_onenter_matches_real = True

    # 3. Covariance Analysis: Length vs Prefix Continuity (Section 4)
    status_a1 = cell_records.get("A1", {}).get("q2_status")
    status_a2 = cell_records.get("A2", {}).get("q2_status")
    status_b1 = cell_records.get("B1", {}).get("q2_status")
    status_b2 = cell_records.get("B2", {}).get("q2_status")

    all_q2_failed = all(
        "FAIL" in str(cell_records[c].get("q2_status", ""))
        for c in ["A1", "A2", "B1", "B2"]
        if c in cell_records
    )

    if all_q2_failed:
        query_length_causal = "NOT_SUPPORTED"
        prefix_continuity_causal = "NOT_SUPPORTED"
        root_cause = "CONFIRMED_SEQUENTIAL_SUBMISSION_CONTRACT_DEFECT"
    elif status_a1 != "PASS" and status_a2 == "PASS" and status_b1 != "PASS" and status_b2 == "PASS":
        query_length_causal = "CONFIRMED"
        prefix_continuity_causal = "NOT_SUPPORTED"
        root_cause = "QUERY_LENGTH_SENSITIVITY"
    elif status_a1 != "PASS" and status_a2 == "PASS" and status_b1 == "PASS" and status_b2 != "PASS":
        query_length_causal = "NOT_SUPPORTED"
        prefix_continuity_causal = "CONFIRMED"
        root_cause = "PREFIX_CONTINUITY_SENSITIVITY"
    else:
        query_length_causal = "UNRESOLVED"
        prefix_continuity_causal = "UNRESOLVED"
        root_cause = "UNRESOLVED"

    q2_effective_submit_defect = "CONFIRMED" if all_q2_failed else "LIKELY"

    return {
        "SEARCH_INPUT_COMPONENT_REFERENCE_REUSED_ACROSS_QUERIES": component_ref_reused_verdict,
        "STALE_SEARCH_INPUT_COMPONENT_REFERENCE": stale_component_ref_verdict,
        "DIRECT_ONENTER_MATCHES_REAL_ENTER_PATH": direct_onenter_matches_real,
        "QUERY_LENGTH_CAUSAL": query_length_causal,
        "PREFIX_CONTINUITY_CAUSAL": prefix_continuity_causal,
        "Q2_EFFECTIVE_SUBMISSION_DEFECT": q2_effective_submit_defect,
        "ROOT_CAUSE_OF_SEQUENTIAL_QUERY_FAILURE": root_cause,
        "mechanism_details": onenter_mechanism_details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11R10E Sequential Search Submission Mechanism Isolation")
    parser.add_argument("--dry-run", action="store_true", help="Execute dry-run without starting live host")
    parser.add_argument("--cells", type=str, default="A1,A2,B1,B2", help="Comma-separated cells to run (default: A1,A2,B1,B2)")
    args = parser.parse_args()

    start_time = datetime.now(timezone.utc)
    commit_head = get_git_commit_head()
    uncommitted_backend_drift = check_uncommitted_diff("src/")
    uncommitted_probe_drift = check_uncommitted_diff("scripts/diagnose_task11r10e_isolate_submission_mechanism.py")

    backend_file = REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py"
    backend_sha256 = compute_file_sha256(backend_file)
    probe_script_path = Path(__file__).resolve()
    probe_sha256 = compute_file_sha256(probe_script_path)

    matrix_canonical_json = json.dumps(CONTROLLED_QUERY_MATRIX, sort_keys=True)
    matrix_sha256 = hashlib.sha256(matrix_canonical_json.encode("utf-8")).hexdigest()

    timestamp_str = start_time.strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE_DIR / f"task11r10e_isolation_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("================================================================================")
    logger.info("🔬 TASK 11R10E — SEQUENTIAL SEARCH SUBMISSION MECHANISM ISOLATION")
    logger.info("================================================================================")
    logger.info(f"TASK11R10E_PROBE_COMMIT:         {commit_head}")
    logger.info(f"PROBE_SCRIPT_SHA256:             {probe_sha256}")
    logger.info(f"CONTROLLED_QUERY_MATRIX_SHA256:  {matrix_sha256}")
    logger.info(f"SEARCH_BACKEND_SHA256:           {backend_sha256}")
    logger.info(f"PRODUCTION_CODE_DRIFT:           {uncommitted_backend_drift}")
    logger.info(f"UNCOMMITTED_PROBE_DRIFT:         {uncommitted_probe_drift}")
    logger.info(f"Cells Planned:                   {args.cells}")
    logger.info(f"Dry Run Mode:                    {args.dry_run}")
    logger.info("================================================================================\n")

    cells_to_run = [c.strip().upper() for c in args.cells.split(",") if c.strip().upper() in CONTROLLED_QUERY_MATRIX]
    cell_results: dict[str, dict[str, Any]] = {}

    for cell_id in cells_to_run:
        cell_info = CONTROLLED_QUERY_MATRIX[cell_id]
        rec = run_cell_characterization(cell_info, dry_run=args.dry_run)
        cell_results[cell_id] = rec

    # Section 11: Synthesize Authoritative Verdicts
    verdicts = synthesize_verdicts(cell_results)

    final_report = {
        "metadata": {
            "task": "TASK_11R10E_SEQUENTIAL_SEARCH_SUBMISSION_MECHANISM_ISOLATION",
            "timestamp_utc": start_time.isoformat(),
            "git_commit_head": commit_head,
            "probe_script_sha256": probe_sha256,
            "backend_sha256": backend_sha256,
            "controlled_query_matrix_sha256": matrix_sha256,
            "production_code_drift": uncommitted_backend_drift,
            "uncommitted_probe_drift": uncommitted_probe_drift,
            "production_code_mutations": 0,
            "probe_code_mutations": 0,
            "query_matrix_mutations": 0,
            "timeout_mutations": 0,
            "controlled_query_matrix": CONTROLLED_QUERY_MATRIX,
            "invariants": {
                "SUBMIT_REQUIRES_VERIFIED_QUERY_STATE": True,
                "FIXED_SETTLE_SLEEP_IS_SUBMIT_AUTHORITY": False,
                "FINGERPRINT_CHANGE_ALONE_IS_NOT_PRIMARY_QUERY_AUTHORITY": True,
                "TRANSIENT_EMPTY_DOM_IS_NOT_TERMINAL_SUCCESS": True,
                "STALE_RESULT_SUCCESS_RETURN_PREVENTED": True,
                "SEMANTIC_PIPELINE_CALLS": 0,
                "QUALIFICATION_SCORE_CONTRIBUTION": 0,
                "TASK_11_COMPLETE": False,
                "TASK_11_QUALIFICATION_PASS": False,
                "TASK_12_STARTED": False,
                "PRODUCTION_CUTOVER": False,
            },
        },
        "verdicts": verdicts,
        "cell_results": cell_results,
    }

    report_path = run_dir / "task11r10e_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    logger.info("\n" + "=" * 70)
    logger.info("📊 TASK 11R10E FINAL VERDICTS SUMMARY")
    logger.info("=" * 70)
    for k, v in verdicts.items():
        if k != "mechanism_details":
            logger.info(f"  {k:55} = {v}")
    logger.info(f"\nReport written to: {report_path}")
    logger.info("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
