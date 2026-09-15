"""Dedicated WebView Slack Production Search Backend (Phase 9.9.4a).

Implements a hardened, offscreen, read-only SearchBackend backed by a dedicated WebView2
Slack runtime instance.

Architecture & Security Boundaries:
1. Isolated Dedicated WebView2 Runtime:
   - Dedicated profile: %LOCALAPPDATA%\\PESU\\slack-runtime (independent from Desktop Slack)
   - Offscreen host window (-32000, -32000) with WS_EX_TOOLWINDOW and WS_EX_NOACTIVATE
   - Interactive login only (zero automated credential/cookie/session copying)
   - Unauthenticated state returns typed failure: AUTH_REQUIRED
2. Read-Only Typed Capability Surface:
   - Public API exposes strictly typed search methods: search() / search_messages()
   - Arbitrary JS evaluation, raw keyboard input, and generic DOM click APIs are FORBIDDEN
3. Verified Model-Native Search Pipeline (Phase 9.9.3 Frozen Contract):
   - Search button synthetic mousedown (max 1)
   - React Fiber -> SearchInputBox -> Texty.setContents({ contents: [{ insert: q }] }, { source: 'api' }) (max 1)
   - Atomic pre-submit sync verification: DOM, QuillText, QuillDelta, Texty, Redux all synced
   - SearchInputBox.onEnter() invocation (max 1, zero submit retries)
   - Strict search route & [data-qa="search_view"] mount verification
   - Terminal state gate (RESULTS or EMPTY_RESULTS)
4. Multi-Fragment Rich-Text Body Extraction Contract (Phase 9.9.3 Frozen Contract):
   - Scope: exactly 1 valid .c-search_message__content container per result root
   - Fragment query strictly scoped to scopeContainer (.p-rich_text_section)
   - Structural boundary guards: exclude attachments, thread metadata, actions, reactions, sender, timestamp
   - Fail-closed on ambiguous fragments (ambiguous > 0 fails extraction)
   - DOM document order aggregation with "\\n" block separator
   - Normalization: re.sub(r'\\s+', ' ', text).strip() -> SlackSearchResult.text
5. Zero Mutation of User's Slack Desktop:
   - Zero CDP calls to user's running Slack Desktop processes
   - Zero window or process state mutations
6. Strict Privacy Invariants:
   - Zero raw query, raw body, raw sender, raw channel, or raw identity logging
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import uuid
from typing import Any, Optional, Protocol, runtime_checkable

from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.search.slack_query_compiler import (
    SlackQueryCompiler,
    SlackQueryCompilationError,
    CompiledSlackQuery,
)
from pesu_agent.search.slack_search import (
    SlackSearchResult,
    SlackSearchSession,
)

logger = logging.getLogger(__name__)
user32 = ctypes.windll.user32 if sys.platform == "win32" else None

# Win32 Constants
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


@runtime_checkable
class WebViewHostProtocol(Protocol):
    """Minimal protocol for pluggable WebView host backends (Phase 9.9.5d.2)."""

    def evaluate_js(self, script: str, timeout: float = 10.0) -> Any:
        """Executes JavaScript synchronously and returns parsed result."""
        ...

    def destroy(self) -> None:
        """Gracefully shuts down the host window and message loop."""
        ...


class WebViewRuntimeState(str, Enum):
    """Dedicated WebView runtime lifecycle states."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


class WebViewSearchFailureCode(str, Enum):
    """Structured, typed failure codes for WebView search operations."""
    RUNTIME_NOT_READY = "RUNTIME_NOT_READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    EMPTY_QUERY_INVALID = "EMPTY_QUERY_INVALID"
    SEARCH_UI_NOT_FOUND = "SEARCH_UI_NOT_FOUND"
    SEARCH_UI_AMBIGUOUS = "SEARCH_UI_AMBIGUOUS"
    SEARCH_INPUT_READINESS_TIMEOUT = "SEARCH_INPUT_READINESS_TIMEOUT"
    QUERY_STATE_SYNC_FAILED = "QUERY_STATE_SYNC_FAILED"
    SEARCH_SUBMIT_FAILED = "SEARCH_SUBMIT_FAILED"
    SEARCH_NAVIGATION_FAILED = "SEARCH_NAVIGATION_FAILED"
    SEARCH_RESULT_TIMEOUT = "SEARCH_RESULT_TIMEOUT"
    SEARCH_RESULT_STRUCTURE_CHANGED = "SEARCH_RESULT_STRUCTURE_CHANGED"
    RESULT_IDENTITY_INVALID = "RESULT_IDENTITY_INVALID"
    RESULT_BODY_INVALID = "RESULT_BODY_INVALID"
    CONTEXT_COLLECTION_NOT_QUALIFIED = "CONTEXT_COLLECTION_NOT_QUALIFIED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class WebViewSearchError(Exception):
    """Typed exception raised during WebView search operations."""

    def __init__(
        self,
        failure_code: WebViewSearchFailureCode,
        message: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.failure_code = failure_code
        self.details = details or {}
        super().__init__(message or f"WebView Search Error ({failure_code.value})")


def get_default_webview_storage_path() -> Path:
    """Returns the dedicated PESU Slack WebView2 profile storage path."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        base_dir = Path.home() / "AppData" / "Local"
    profile_dir = base_dir / "PESU" / "slack-runtime"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


class WebViewRuntimeConfig:
    """Configuration for Dedicated WebView Slack Runtime."""

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        target_url: str = "https://app.slack.com/client",
        window_title: str = "PESU Dedicated Slack Runtime",
        offscreen_x: int = -32000,
        offscreen_y: int = -32000,
        width: int = 1200,
        height: int = 800,
    ) -> None:
        self.storage_path = storage_path or get_default_webview_storage_path()
        self.target_url = target_url
        self.window_title = window_title
        self.offscreen_x = offscreen_x
        self.offscreen_y = offscreen_y
        self.width = width
        self.height = height


# Polling Timing Constants (Phase 12.3h-R2 Canonical Search Readiness Gate)
SEARCH_UI_READY_TIMEOUT_SECONDS: float = 5.0
SEARCH_UI_READY_POLL_INTERVAL_SECONDS: float = 0.1
SEARCH_INPUT_READINESS_TIMEOUT_SECONDS: float = 5.0
SEARCH_INPUT_POLL_INTERVAL_SECONDS: float = 0.1


# JS Primitives (Frozen from Phase 9.9.3)
JS_CHECK_AUTH_AND_READINESS = r"""
(() => {
    const isLoginScreen = Boolean(document.querySelector('input[type="email"], input[name="email"], [data-qa="signin_email_btn"]'));
    if (isLoginScreen) return { ready: false, auth_required: true };
    const searchButtons = document.querySelectorAll('button[data-qa="top_nav_search"]');
    return {
        ready: searchButtons.length === 1,
        auth_required: false,
        search_button_count: searchButtons.length
    };
})()
"""

JS_ACTIVATE_SEARCH = r"""
(() => {
    const searchButtons = document.querySelectorAll('button[data-qa="top_nav_search"]');
    if (searchButtons.length !== 1) return { error: 'BUTTON_CARDINALITY_NOT_ONE', count: searchButtons.length };
    const btn = searchButtons[0];
    try {
        const mousedownEvt = new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, ctrlKey: false });
        return { activated: btn.dispatchEvent(mousedownEvt) };
    } catch (e) {
        return { error: 'MOUSEDOWN_DISPATCH_EXCEPTION', details: String(e) };
    }
})()
"""

JS_CHECK_SEARCH_INPUT_READINESS = r"""
(() => {
    const searchModalCandidates = Array.from(document.querySelectorAll('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal')).filter(el => el && el.isConnected);
    const modalCount = searchModalCandidates.length;
    const visibleModalCount = searchModalCandidates.filter(el => el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0).length;

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    const inputCount = searchInputCandidates.length;
    const visibleInputCount = searchInputCandidates.filter(el => el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0).length;

    const docVisibility = document.visibilityState || 'unknown';
    const docHidden = typeof document.hidden === 'boolean' ? document.hidden : false;
    const docHasFocus = typeof document.hasFocus === 'function' ? document.hasFocus() : false;

    const baseTelemetry = {
        modal_count: modalCount,
        modal_visible_count: visibleModalCount,
        input_count: inputCount,
        input_visible_count: visibleInputCount,
        document_visibility_state: docVisibility,
        document_hidden: docHidden,
        document_has_focus: docHasFocus
    };

    if (inputCount === 0) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 0,
            ambiguous: false,
            error: 'SEARCH_INPUT_NOT_MOUNTED'
        };
    }

    if (inputCount > 1) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 0,
            ambiguous: true,
            error: 'SEARCH_INPUT_CARDINALITY_NOT_ONE'
        };
    }

    const inputElem = searchInputCandidates[0];

    // Verify input is NOT a message composer
    const isComposer = Boolean(inputElem.closest('[data-qa="message_editor"], [data-qa="message_input"], .c-message_input'));
    if (isComposer) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 0,
            ambiguous: true,
            error: 'SEARCH_INPUT_IS_COMPOSER'
        };
    }

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 0,
            ambiguous: false,
            error: 'NO_REACT_FIBER_FOUND'
        };
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
            matchingInstances.push(fiber.stateNode);
        }
        fiber = fiber.return;
        depth++;
    }

    if (matchingInstances.length === 0) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 0,
            ambiguous: false,
            error: 'STRONG_SEARCH_INPUT_BOX_NOT_READY'
        };
    }

    if (matchingInstances.length > 1) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: matchingInstances.length,
            ambiguous: true,
            error: 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_NOT_ONE'
        };
    }

    const searchInputBoxInstance = matchingInstances[0];
    const textyObj = searchInputBoxInstance.texty;
    const isCallable = Boolean(textyObj && typeof textyObj.setContents === 'function' && typeof searchInputBoxInstance.onEnter === 'function');

    if (!isCallable) {
        return {
            ready: false,
            ...baseTelemetry,
            strong_match_count: 1,
            ambiguous: false,
            error: 'SETTER_OR_SUBMIT_NOT_CALLABLE'
        };
    }

    return {
        ready: true,
        ...baseTelemetry,
        strong_match_count: 1,
        ambiguous: false
    };
})()
"""

JS_SET_QUERY_AND_SUBMIT = r"""
((targetQuery) => {
    const searchInputCandidates = document.querySelectorAll('[data-qa="search_input_box"] .ql-editor');
    if (searchInputCandidates.length !== 1) return { error: 'SEARCH_INPUT_CARDINALITY_NOT_ONE', count: searchInputCandidates.length };

    const inputElem = searchInputCandidates[0];
    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) return { error: 'NO_REACT_FIBER_FOUND' };

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
            matchingInstances.push(fiber.stateNode);
        }
        fiber = fiber.return;
        depth++;
    }

    if (matchingInstances.length !== 1) return { error: 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_NOT_ONE', count: matchingInstances.length };

    const searchInputBoxInstance = matchingInstances[0];
    const textyObj = searchInputBoxInstance.texty;
    if (!textyObj || typeof textyObj.setContents !== 'function' || typeof searchInputBoxInstance.onEnter !== 'function') {
        return { error: 'SETTER_OR_SUBMIT_NOT_CALLABLE' };
    }

    try {
        textyObj.setContents({ contents: [{ insert: targetQuery }] }, { source: 'api' });
        searchInputBoxInstance.onEnter();
        return { ok: true, submitted: true };
    } catch (e) {
        return { error: 'PIPELINE_INVOCATION_EXCEPTION', details: String(e) };
    }
})
"""


def compute_result_set_fingerprint(items: list[dict[str, Any]]) -> str:
    """Computes deterministic SHA-256 fingerprint for a list of result items."""
    if not items:
        return "EMPTY_RESULTS"
    signatures: list[str] = []
    for idx, itm in enumerate(items):
        plink = (
            itm.get("canonical_permalink")
            or (itm.get("qualifying_permalinks") or [None])[0]
            or "NO_PERMALINK"
        )
        author = itm.get("sender_author_id") or itm.get("sender_text") or "NO_AUTHOR"
        ts = itm.get("timestamp_raw") or "NO_TS"
        body = itm.get("normalized_body") or itm.get("body_sample") or ""
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        signatures.append(f"{idx}:{plink}:{author}:{ts}:{body_hash}")
    return hashlib.sha256("|".join(signatures).encode("utf-8")).hexdigest()[:16]


# Forensic Transition JS Probes (Phase 13.5D Task 11R2 Pure Read-Only Observability)
JS_PROBE_PRE_SUBMIT_SEARCH_STATE = r"""
(() => {
    const inputElem = document.querySelector('[data-qa="search_input_box"] .ql-editor');
    const inputValueBefore = inputElem ? (inputElem.textContent || inputElem.innerText || '').trim() : '';

    let surfaceQueryBefore = null;
    try {
        const url = new URL(window.location.href);
        const qUrl = url.searchParams.get('query') || url.searchParams.get('q');
        if (qUrl) {
            surfaceQueryBefore = qUrl;
        } else {
            const m = url.pathname.match(/\/search\/(.+)$/);
            if (m) surfaceQueryBefore = decodeURIComponent(m[1]);
        }
    } catch (e) {}

    if (!surfaceQueryBefore) {
        const header = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title');
        if (header && (header.textContent || '').trim()) {
            surfaceQueryBefore = header.textContent.trim();
        }
    }

    const searchViews = document.querySelectorAll('[data-qa="search_view"]');
    const searchViewMounted = searchViews.length === 1;
    let preSubmitItems = [];

    if (searchViewMounted) {
        const roots = Array.from(searchViews[0].querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });
        preSubmitItems = roots.slice(0, 10).map((r, idx) => {
            const a = r.querySelector('a[href*="/archives/"]');
            const href = a ? a.getAttribute('href') : '';
            const body = (r.querySelector('.c-search_message__content') || r).textContent || '';
            const sender = r.querySelector('[data-qa="message_sender_name"]');
            const ts = r.querySelector('.c-timestamp');
            return {
                canonical_permalink: href,
                sender_text: sender ? (sender.textContent || '').trim() : null,
                timestamp_raw: ts ? (ts.textContent || '').trim() : null,
                normalized_body: body.replace(/\s+/g, ' ').trim()
            };
        });
    }

    const loadingIndicator = Boolean(document.querySelector('[data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading'));

    return {
        input_value_before: inputValueBefore,
        surface_query_before: surfaceQueryBefore,
        search_view_mounted: searchViewMounted,
        pre_submit_item_count: preSubmitItems.length,
        pre_submit_items: preSubmitItems,
        loading_detected_before: loadingIndicator
    };
})()
"""

JS_PROBE_POST_SUBMIT_INPUT_STATE = r"""
(() => {
    const inputElem = document.querySelector('[data-qa="search_input_box"] .ql-editor');
    const inputValueAfterSubmit = inputElem ? (inputElem.textContent || inputElem.innerText || '').trim() : '';

    let surfaceQueryImmediate = null;
    try {
        const url = new URL(window.location.href);
        const qUrl = url.searchParams.get('query') || url.searchParams.get('q');
        if (qUrl) {
            surfaceQueryImmediate = qUrl;
        } else {
            const m = url.pathname.match(/\/search\/(.+)$/);
            if (m) surfaceQueryImmediate = decodeURIComponent(m[1]);
        }
    } catch (e) {}

    const loadingIndicator = Boolean(document.querySelector('[data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading'));

    return {
        input_value_after_submit: inputValueAfterSubmit,
        surface_query_immediate: surfaceQueryImmediate,
        loading_detected_immediate: loadingIndicator
    };
})()
"""

JS_PROBE_POST_WAIT_SEARCH_STATE = r"""
(() => {
    let surfaceQueryAfter = null;
    try {
        const url = new URL(window.location.href);
        const qUrl = url.searchParams.get('query') || url.searchParams.get('q');
        if (qUrl) {
            surfaceQueryAfter = qUrl;
        } else {
            const m = url.pathname.match(/\/search\/(.+)$/);
            if (m) surfaceQueryAfter = decodeURIComponent(m[1]);
        }
    } catch (e) {}

    if (!surfaceQueryAfter) {
        const header = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title');
        if (header && (header.textContent || '').trim()) {
            surfaceQueryAfter = header.textContent.trim();
        }
    }

    if (!surfaceQueryAfter) {
        const inputElem = document.querySelector('[data-qa="search_input_box"] .ql-editor');
        if (inputElem && (inputElem.textContent || '').trim()) {
            surfaceQueryAfter = inputElem.textContent.trim();
        }
    }

    const loadingIndicator = Boolean(document.querySelector('[data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading'));

    return {
        surface_query_after: surfaceQueryAfter,
        loading_detected_after: loadingIndicator
    };
})()
"""

SEARCH_VIEW_SELECTOR = '[data-qa="search_view"]'
SEARCH_RESULT_ROW_SELECTORS = '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

JS_EXTRACT_SEARCH_RESULTS = r"""
((maxSampleCount, bodyScopeSel, bodyFragSel, senderSel, timestampSel) => {
    const searchViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el && el.isConnected);
    if (searchViews.length !== 1) {
        const emptyViews = Array.from(document.querySelectorAll('[data-qa="search_empty_view"], .c-search_empty_view')).filter(el => el && el.isConnected);
        if (emptyViews.length >= 1) {
            return { ok: true, terminal_state: 'EMPTY_RESULTS', items: [] };
        }
        return { error: 'SEARCH_VIEW_NOT_FOUND', count: searchViews.length };
    }

    const searchView = searchViews[0];
    const resultRoots = Array.from(searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
        if (!el || !el.isConnected) return false;
        const r = el.getBoundingClientRect();
        return (r.width > 0 && r.height > 0);
    });

    if (resultRoots.length === 0) {
        return { ok: true, terminal_state: 'EMPTY_RESULTS', items: [] };
    }

    const inspectedRoots = resultRoots.slice(0, maxSampleCount);
    const itemReports = [];

    for (let idx = 0; idx < inspectedRoots.length; idx++) {
        const root = inspectedRoots[idx];
        const rep = {
            result_index: idx + 1,
            qualifying_permalinks: [],
            sender_text: null,
            sender_author_id: null,
            sender_valid: false,
            timestamp_raw: null,
            timestamp_valid: false,
            thread_reply_detected: false,
            // Scoped Multi-Fragment Body
            primary_fragments: [],
            ambiguous_fragment_count: 0,
            normalized_body: "",
            body_valid: false
        };

        // 1. Anchors for canonical permalink
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

        // 2. Sender
        const senderNodes = Array.from(root.querySelectorAll(senderSel)).filter(e => e.isConnected);
        if (senderNodes.length === 1) {
            const sn = senderNodes[0];
            const sRect = sn.getBoundingClientRect();
            if (sRect.width > 0 && sRect.height > 0) {
                const sText = (sn.textContent || '').trim();
                if (sText.length > 0) {
                    rep.sender_text = sText;
                    rep.sender_valid = true;
                }
                const sAuthorId = sn.getAttribute('data-message-sender') || null;
                if (sAuthorId) {
                    rep.sender_author_id = sAuthorId;
                }
            }
        }

        // 3. Timestamp
        const timestampNodes = Array.from(root.querySelectorAll(timestampSel)).filter(e => e.isConnected);
        if (timestampNodes.length >= 1) {
            const tn = timestampNodes[0];
            const tRect = tn.getBoundingClientRect();
            if (tRect.width > 0 && tRect.height > 0) {
                const tText = (tn.textContent || '').trim();
                if (tText.length > 0) {
                    rep.timestamp_raw = tText;
                    rep.timestamp_valid = true;
                }
            }
        }

        // 4. Scoped Body Extraction (Representation A & Representation B)
        const scopeContainers = Array.from(root.querySelectorAll(bodyScopeSel)).filter(e => e.isConnected);
        const scopeContainer = (scopeContainers.length === 1) ? scopeContainers[0] : null;

        const actionNodes = Array.from(root.querySelectorAll('.c-message_actions, [data-qa="message_actions"], [data-qa="message_action_menu"]')).filter(e => e.isConnected);
        const reactionNodes = Array.from(root.querySelectorAll('.c-reaction, [data-qa="reaction_bar"]')).filter(e => e.isConnected);
        const threadNodes = Array.from(root.querySelectorAll('[data-qa="search_message_thread"], [data-qa="thread_reply_count"], .c-search_message__thread')).filter(e => e.isConnected);

        let representation = 'NONE';
        let bodyValid = false;
        let normalizedBody = '';
        let failureReason = null;
        let ambiguousCount = 0;

        if (!scopeContainer) {
            failureReason = 'NO_BODY_SCOPE';
            ambiguousCount = 1;
        } else {
            // Check Representation A (.p-rich_text_section)
            const allFragmentsA = Array.from(scopeContainer.querySelectorAll(bodyFragSel)).filter(e => e.isConnected);
            const verifiedFragmentsA = [];
            let ambA = 0;

            for (let fIdx = 0; fIdx < allFragmentsA.length; fIdx++) {
                const sec = allFragmentsA[fIdx];
                const r = sec.getBoundingClientRect();
                const isVisible = (r.width > 0 && r.height > 0);
                const textLen = (sec.textContent || '').trim().length;
                const isNonEmpty = (textLen > 0);

                const insideAttachment = Boolean(sec.closest('.c-message_attachment, [data-qa="message_attachment"]'));
                const insideThread = threadNodes.some(th => th.contains(sec));
                const insideAction = actionNodes.some(a => a.contains(sec));
                const insideReaction = reactionNodes.some(rc => rc.contains(sec));
                const insideSender = senderNodes.some(sn => sn.contains(sec));
                const insideTimestamp = timestampNodes.some(tn => tn.contains(sec));

                if (!insideAttachment && !insideThread && !insideAction && !insideReaction && !insideSender && !insideTimestamp && isVisible && isNonEmpty) {
                    verifiedFragmentsA.push((sec.textContent || '').trim());
                } else if (insideAttachment || insideThread || insideAction || insideReaction || insideSender || insideTimestamp) {
                    // Non-body fragment: excluded safely
                } else {
                    ambA++;
                }
            }

            // Check Representation B (div.p-section_block inside .c-message__message_blocks)
            const messageBlocks = scopeContainer.querySelector('.c-message__message_blocks');
            const sectionBlocks = messageBlocks ? Array.from(messageBlocks.querySelectorAll('.p-section_block')).filter(e => e.isConnected) : [];
            const verifiedFragmentsB = [];
            let ambB = 0;
            let secFailureReason = null;

            if (sectionBlocks.length > 0) {
                for (let sIdx = 0; sIdx < sectionBlocks.length; sIdx++) {
                    const sb = sectionBlocks[sIdx];
                    const sbChildren = Array.from(sb.children).filter(c => c && c.isConnected);
                    const bodyChildren = [];
                    let unknownTextCount = 0;

                    for (let cIdx = 0; cIdx < sbChildren.length; cIdx++) {
                        const ch = sbChildren[cIdx];
                        const cCls = ch.className || '';
                        const cQa = ch.getAttribute('data-qa') || '';
                        const cTag = ch.tagName.toLowerCase();
                        const cText = (ch.textContent || '').trim();
                        const cRect = ch.getBoundingClientRect();
                        const cVis = (cRect.width > 0 && cRect.height > 0);

                        const isAccessory = (
                            cCls.includes('p-section_block__accessory') ||
                            cQa.includes('section_accessory') ||
                            cTag === 'button' ||
                            cCls.includes('c-button') ||
                            cTag === 'img'
                        );

                        const isQualifiedBody = cCls.includes('p-section_block_text_content');

                        if (isAccessory) {
                            // 1. Qualified Accessory: excluded structurally regardless of text content
                            continue;
                        } else if (isQualifiedBody && cVis && cText.length > 0) {
                            // 2. Qualified Body: visible, non-empty, matching exact observed structural signature
                            bodyChildren.push(cText);
                        } else if (cText.length > 0) {
                            // 3. Unknown text-bearing child (or non-visible body) -> triggers fail-closed
                            unknownTextCount++;
                        }
                    }

                    if (bodyChildren.length === 1 && unknownTextCount === 0) {
                        verifiedFragmentsB.push(bodyChildren[0]);
                    } else if (bodyChildren.length === 0) {
                        ambB++;
                        if (!secFailureReason) {
                            secFailureReason = (unknownTextCount > 0) ? 'SECTION_BLOCK_UNKNOWN_TEXT_BEARING_CHILD' : 'SECTION_BLOCK_NO_BODY_CHILD';
                        }
                    } else if (bodyChildren.length > 1) {
                        ambB++;
                        if (!secFailureReason) secFailureReason = 'SECTION_BLOCK_MULTIPLE_BODY_CHILDREN';
                    } else if (unknownTextCount > 0) {
                        ambB++;
                        if (!secFailureReason) secFailureReason = 'SECTION_BLOCK_UNKNOWN_TEXT_BEARING_CHILD';
                    }
                }
            }

            const isRepAValid = (allFragmentsA.length > 0 && verifiedFragmentsA.length >= 1 && ambA === 0);
            const isRepBValid = (sectionBlocks.length > 0 && verifiedFragmentsB.length === sectionBlocks.length && ambB === 0);

            if (isRepAValid && sectionBlocks.length === 0) {
                representation = 'REPRESENTATION_A';
                const joined = verifiedFragmentsA.join('\n');
                normalizedBody = joined.replace(/\s+/g, ' ').trim();
                bodyValid = (normalizedBody.length > 0);
                ambiguousCount = ambA;
            } else if (allFragmentsA.length === 0 && isRepBValid) {
                representation = 'REPRESENTATION_B';
                const joined = verifiedFragmentsB.join('\n');
                normalizedBody = joined.replace(/\s+/g, ' ').trim();
                bodyValid = (normalizedBody.length > 0);
                ambiguousCount = ambB;
            } else if (isRepAValid && isRepBValid) {
                representation = 'CONFLICT';
                bodyValid = false;
                failureReason = 'BODY_REPRESENTATION_CONFLICT';
                ambiguousCount = 1;
            } else if (sectionBlocks.length > 0 && !isRepBValid) {
                representation = 'REPRESENTATION_B';
                bodyValid = false;
                failureReason = secFailureReason || 'SECTION_BLOCK_AMBIGUOUS';
                ambiguousCount = Math.max(1, ambB);
            } else if (allFragmentsA.length > 0 && !isRepAValid) {
                representation = 'REPRESENTATION_A';
                bodyValid = false;
                failureReason = 'REPRESENTATION_A_AMBIGUOUS';
                ambiguousCount = Math.max(1, ambA);
            } else {
                representation = 'NONE';
                bodyValid = false;
                failureReason = 'NO_BODY_REPRESENTATION';
                ambiguousCount = 1;
            }
        }

        rep.body_representation = representation;
        rep.body_failure_reason = failureReason;
        rep.ambiguous_fragment_count = ambiguousCount;
        rep.normalized_body = normalizedBody;
        rep.body_valid = bodyValid;

        itemReports.push(rep);
    }

    return {
        ok: true,
        terminal_state: 'RESULTS',
        items: itemReports
    };
})
"""


from pesu_agent.investigation.models import EvidenceItem, SearchQuery
from pesu_agent.investigation.search_backend import SearchBackend


class WebViewSlackSearchBackend(SearchBackend):
    """Production SearchBackend implementation backed by dedicated offscreen WebView2 Slack instance.

    Conforms to SearchBackend Protocol while completely isolating Slack execution from
    user desktop interactions.
    """

    BODY_SCOPE_CONTAINER = ".c-search_message__content"
    BODY_FRAGMENT_SELECTOR = ".p-rich_text_section"
    SENDER_CANONICAL_SELECTOR = '[data-qa="message_sender_name"]'
    TIMESTAMP_CANONICAL_SELECTOR = ".c-timestamp"

    def __init__(
        self,
        config: Optional[WebViewRuntimeConfig] = None,
        default_max_results: int = 10,
        host: Optional[WebViewHostProtocol] = None,
        search_ui_ready_timeout_sec: float = SEARCH_UI_READY_TIMEOUT_SECONDS,
        search_ui_ready_poll_interval_sec: float = SEARCH_UI_READY_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.config = config or WebViewRuntimeConfig()
        self.default_max_results = default_max_results
        self._window: Optional[WebViewHostProtocol] = host
        self.search_ui_ready_timeout_sec = search_ui_ready_timeout_sec
        self.search_ui_ready_poll_interval_sec = search_ui_ready_poll_interval_sec
        self.last_search_telemetry: dict[str, Any] = {}
        self._state = (
            WebViewRuntimeState.READY if host is not None else WebViewRuntimeState.STOPPED
        )

    @property
    def state(self) -> WebViewRuntimeState:
        """Returns the current runtime lifecycle state."""
        return self._state

    def attach_host(self, host: WebViewHostProtocol) -> None:
        """Pluggably attaches an active WebView host conforming to WebViewHostProtocol."""
        self._window = host
        self._state = WebViewRuntimeState.READY

    def get_active_team_id(self) -> Optional[str]:
        """Returns the active authenticated Slack team ID from the attached host."""
        if self._window is not None and hasattr(self._window, "get_active_team_id"):
            return self._window.get_active_team_id()
        return None

    def validate_query(self, query: str) -> str:
        """Validates and compiles a search query string via SlackQueryCompiler.

        Guarantees query is purely data (never executable JS, empty, or unprintable).
        """
        try:
            compiled = SlackQueryCompiler().compile(query)
            return compiled.compiled_query
        except SlackQueryCompilationError as err:
            raise WebViewSearchError(
                WebViewSearchFailureCode.EMPTY_QUERY_INVALID,
                str(err),
            ) from err

    def start_runtime(self, timeout: float = 25.0) -> None:
        """Lifecycle contract: initializes the dedicated WebView runtime if not already active."""
        if self._state == WebViewRuntimeState.READY and (
            self._window is None or getattr(self._window, "is_ready", True)
        ):
            return
        self._state = WebViewRuntimeState.STARTING
        if self._window is not None and hasattr(self._window, "start"):
            self._window.start(timeout=timeout)
        self._state = WebViewRuntimeState.READY

    def stop_runtime(self) -> None:
        """Lifecycle contract: gracefully terminates the dedicated WebView runtime."""
        if self._state == WebViewRuntimeState.STOPPED:
            return
        self._state = WebViewRuntimeState.STOPPING
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        self._state = WebViewRuntimeState.STOPPED

    def _execute_search_pipeline(
        self, validated_query: str, max_results: int = 10
    ) -> list[SlackSearchResult]:
        """Internal synchronous execution engine for search pipeline."""
        telemetry: dict[str, Any] = {
            "last_confirmed_stage": "NONE",
            "top_nav_search_count": 0,
            "search_ui_poll_count": 0,
            "activation_dispatch_count": 0,
            "activation_result": "NOT_DISPATCHED",
            "readiness_poll_count": 0,
            "final_modal_count": 0,
            "final_modal_visible_count": 0,
            "final_input_count": 0,
            "final_input_visible_count": 0,
            "modal_ever_observed": False,
            "input_ever_observed": False,
            "activation_to_first_modal_ms": None,
            "activation_to_first_input_ms": None,
            "elapsed_ms": 0,
            "timeout_ms": 5000,
            "document_visibility_state": "unknown",
            "document_hidden": False,
            "document_has_focus": False,
            "failure_case_classification": "NONE",
        }
        self.last_search_telemetry = telemetry

        if self._state != WebViewRuntimeState.READY:
            raise WebViewSearchError(
                WebViewSearchFailureCode.RUNTIME_NOT_READY,
                f"WebView runtime is not ready (state={self._state.value}).",
                details=telemetry,
            )

        if self._window is None:
            raise WebViewSearchError(
                WebViewSearchFailureCode.RUNTIME_NOT_READY,
                "WebView window instance is not attached.",
                details=telemetry,
            )

        # Step 1: Check Auth & Search UI Readiness (Bounded Monotonic Polling)
        telemetry["last_confirmed_stage"] = "AUTH_WAIT_STARTED"
        start_ready_mono = time.monotonic()
        res_auth: Any = None
        search_ui_ready = False

        while True:
            telemetry["search_ui_poll_count"] += 1
            try:
                res_auth = self._window.evaluate_js(JS_CHECK_AUTH_AND_READINESS)
            except Exception as eval_err:
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                    f"Search readiness JS evaluation failed: {eval_err}",
                    details=telemetry,
                ) from eval_err

            if not isinstance(res_auth, dict):
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                    f"Malformed search readiness response: {res_auth}",
                    details=telemetry,
                )

            if res_auth.get("auth_required"):
                telemetry["top_nav_search_count"] = res_auth.get("search_button_count", 0)
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.AUTH_REQUIRED,
                    "Slack authentication required in dedicated WebView profile.",
                    details=telemetry,
                )

            btn_count = res_auth.get("search_button_count")
            if not isinstance(btn_count, int) or btn_count < 0:
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                    f"Malformed search readiness payload (invalid search_button_count): {res_auth}",
                    details=telemetry,
                )

            telemetry["top_nav_search_count"] = btn_count

            if btn_count > 1:
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_UI_AMBIGUOUS,
                    f"Ambiguous top navigation search controls detected (count={btn_count}): {res_auth}",
                    details=telemetry,
                )

            if btn_count == 1:
                if res_auth.get("ready") is True:
                    search_ui_ready = True
                    telemetry["last_confirmed_stage"] = "TOP_NAV_RESOLVED"
                    break
                else:
                    telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                    raise WebViewSearchError(
                        WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                        f"Inconsistent readiness state: {res_auth}",
                        details=telemetry,
                    )

            # btn_count == 0 (UI not mounted yet)
            if res_auth.get("ready") is True:
                telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                    f"Inconsistent readiness state: {res_auth}",
                    details=telemetry,
                )

            now_mono = time.monotonic()
            if now_mono - start_ready_mono >= self.search_ui_ready_timeout_sec:
                break

            time.sleep(self.search_ui_ready_poll_interval_sec)

        if not search_ui_ready:
            telemetry["last_confirmed_stage"] = "AUTH_CHECKED"
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                f"Search button not found or readiness timed out: {res_auth}",
                details=telemetry,
            )

        # Step 2: Search Activation (Dispatched exactly once)
        telemetry["activation_dispatch_count"] += 1
        res_act = self._window.evaluate_js(JS_ACTIVATE_SEARCH)
        activation_ok = isinstance(res_act, dict) and bool(res_act.get("activated"))
        telemetry["activation_result"] = "activated:true" if activation_ok else str(res_act)

        if not activation_ok:
            telemetry["failure_case_classification"] = "CASE_A_ACTIVATION_FAILED"
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND,
                f"Search activation dispatch failed: {res_act}",
                details=telemetry,
            )
        telemetry["last_confirmed_stage"] = "ACTIVATION_DISPATCHED"

        # Step 3: Bounded Search Modal & Input Readiness Polling (Pure Observation)
        telemetry["last_confirmed_stage"] = "MODAL_WAIT_STARTED"
        input_readiness_timeout_sec = SEARCH_INPUT_READINESS_TIMEOUT_SECONDS
        input_poll_interval_sec = SEARCH_INPUT_POLL_INTERVAL_SECONDS
        start_poll_time = time.time()
        input_ready = False
        first_modal_time: Optional[float] = None
        first_input_time: Optional[float] = None

        while time.time() - start_poll_time < input_readiness_timeout_sec:
            telemetry["readiness_poll_count"] += 1
            res_poll = self._window.evaluate_js(JS_CHECK_SEARCH_INPUT_READINESS)
            if isinstance(res_poll, dict):
                telemetry["final_modal_count"] = res_poll.get("modal_count", 0)
                telemetry["final_modal_visible_count"] = res_poll.get("modal_visible_count", 0)
                telemetry["final_input_count"] = res_poll.get("input_count", 0)
                telemetry["final_input_visible_count"] = res_poll.get("input_visible_count", 0)
                telemetry["document_visibility_state"] = res_poll.get("document_visibility_state", "unknown")
                telemetry["document_hidden"] = res_poll.get("document_hidden", False)
                telemetry["document_has_focus"] = res_poll.get("document_has_focus", False)

                if telemetry["final_modal_count"] > 0:
                    telemetry["modal_ever_observed"] = True
                    if first_modal_time is None:
                        first_modal_time = time.time()
                        telemetry["activation_to_first_modal_ms"] = int((first_modal_time - start_poll_time) * 1000)
                        telemetry["last_confirmed_stage"] = "MODAL_OBSERVED"

                if telemetry["final_input_count"] > 0:
                    telemetry["input_ever_observed"] = True
                    if first_input_time is None:
                        first_input_time = time.time()
                        telemetry["activation_to_first_input_ms"] = int((first_input_time - start_poll_time) * 1000)
                        telemetry["last_confirmed_stage"] = "INPUT_OBSERVED"

                if res_poll.get("ambiguous"):
                    telemetry["failure_case_classification"] = "AMBIGUOUS"
                    telemetry["elapsed_ms"] = int((time.time() - start_poll_time) * 1000)
                    raise WebViewSearchError(
                        WebViewSearchFailureCode.SEARCH_UI_AMBIGUOUS,
                        f"Search input readiness ambiguous: {res_poll.get('error', 'AMBIGUOUS')}",
                        details=telemetry,
                    )
                if res_poll.get("ready"):
                    input_ready = True
                    break
            time.sleep(input_poll_interval_sec)

        telemetry["elapsed_ms"] = int((time.time() - start_poll_time) * 1000)

        if not input_ready:
            if not telemetry["modal_ever_observed"]:
                telemetry["failure_case_classification"] = "CASE_B_MODAL_NOT_MOUNTED"
            else:
                telemetry["failure_case_classification"] = "CASE_C_INPUT_NOT_MOUNTED"
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT,
                f"Search input readiness timed out after {input_readiness_timeout_sec}s: {telemetry['failure_case_classification']}",
                details=telemetry,
            )

        # Step 3.5: Read-Only Pre-Submit Probe (Hard Requirement 2: Side-effect free)
        pre_probe: dict[str, Any] = {}
        try:
            res_pre = self._window.evaluate_js(JS_PROBE_PRE_SUBMIT_SEARCH_STATE)
            if isinstance(res_pre, dict):
                pre_probe = res_pre
        except Exception as e:
            logger.warning(f"Pre-submit probe evaluation warning: {e}")

        search_input_value_before = pre_probe.get("input_value_before", "")
        surface_query_before = pre_probe.get("surface_query_before")
        pre_submit_items = pre_probe.get("pre_submit_items", [])
        search_view_mounted_before = pre_probe.get("search_view_mounted", False)
        loading_before = pre_probe.get("loading_detected_before", False)

        result_fingerprint_before: Optional[str] = None
        if search_view_mounted_before:
            result_fingerprint_before = compute_result_set_fingerprint(pre_submit_items)

        # Step 4: Model-Native Setter & Submit (Atomic, invoked at most once)
        # Query is serialized safely via json.dumps (never formatted as raw string into JS)
        js_pipe = f"({JS_SET_QUERY_AND_SUBMIT})({json.dumps(validated_query)})"
        res_pipe = self._window.evaluate_js(js_pipe)
        submit_triggered = bool(isinstance(res_pipe, dict) and res_pipe.get("submitted"))

        # Step 4.5: Read-Only Post-Submit Probe (Hard Requirement 2: Side-effect free)
        post_submit_probe: dict[str, Any] = {}
        try:
            res_post_sub = self._window.evaluate_js(JS_PROBE_POST_SUBMIT_INPUT_STATE)
            if isinstance(res_post_sub, dict):
                post_submit_probe = res_post_sub
        except Exception as e:
            logger.warning(f"Post-submit probe evaluation warning: {e}")

        search_input_value_after_submit = post_submit_probe.get("input_value_after_submit", "")
        loading_immediate = post_submit_probe.get("loading_detected_immediate", False)

        if not submit_triggered:
            telemetry["last_confirmed_stage"] = "SETTER_EXECUTED" if "onEnter" in str(res_pipe) else "INPUT_OBSERVED"
            err_code = (
                WebViewSearchFailureCode.QUERY_STATE_SYNC_FAILED
                if "SYNC" in str(res_pipe)
                else WebViewSearchFailureCode.SEARCH_SUBMIT_FAILED
            )
            raise WebViewSearchError(
                err_code,
                f"Search query setter / submit pipeline failed: {res_pipe}",
                details=telemetry,
            )
        telemetry["last_confirmed_stage"] = "SUBMIT_EXECUTED"

        # Step 5: Bounded Wait & Terminal State Extraction
        t_wait_start = time.monotonic()
        time.sleep(3.5)
        result_transition_wait_duration_ms = int((time.monotonic() - t_wait_start) * 1000)

        # Step 5.1: Read-Only Post-Wait Probe (Hard Requirement 2: Side-effect free)
        post_wait_probe: dict[str, Any] = {}
        try:
            res_post_wait = self._window.evaluate_js(JS_PROBE_POST_WAIT_SEARCH_STATE)
            if isinstance(res_post_wait, dict):
                post_wait_probe = res_post_wait
        except Exception as e:
            logger.warning(f"Post-wait probe evaluation warning: {e}")

        surface_query_after = post_wait_probe.get("surface_query_after")
        loading_after = post_wait_probe.get("loading_detected_after", False)

        loading_or_dom_transition_observed = bool(
            loading_immediate
            or loading_after
            or (surface_query_before != surface_query_after and surface_query_after is not None)
        )

        js_extract = (
            f"({JS_EXTRACT_SEARCH_RESULTS})("
            f"{max_results}, "
            f"{json.dumps(self.BODY_SCOPE_CONTAINER)}, "
            f"{json.dumps(self.BODY_FRAGMENT_SELECTOR)}, "
            f"{json.dumps(self.SENDER_CANONICAL_SELECTOR)}, "
            f"{json.dumps(self.TIMESTAMP_CANONICAL_SELECTOR)})"
        )
        res_extract = self._window.evaluate_js(js_extract)
        if not (isinstance(res_extract, dict) and res_extract.get("ok")):
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_NAVIGATION_FAILED,
                f"Search result extraction failed: {res_extract}",
                details=telemetry,
            )
        raw_items = res_extract.get("items", [])
        parsed_results: list[SlackSearchResult] = []
        raw_items_evaluated: list[dict[str, Any]] = []
        invalid_items_skipped = 0
        invalid_identity_count = 0
        invalid_body_count = 0

        for idx, itm in enumerate(raw_items):
            permalinks = itm.get("qualifying_permalinks") or []
            eval_record = {
                "raw_index": idx + 1,
                "canonical_permalink": permalinks[0] if permalinks else None,
                "qualifying_permalinks": permalinks,
                "sender_author_id": itm.get("sender_author_id"),
                "sender_text": itm.get("sender_text"),
                "timestamp_raw": itm.get("timestamp_raw"),
                "normalized_body": itm.get("normalized_body", ""),
                "body_valid": bool(itm.get("body_valid")),
                "ambiguous_fragment_count": itm.get("ambiguous_fragment_count", 0),
                "thread_reply_detected": itm.get("thread_reply_detected", False),
                "drop_reason": None,
            }
            canonical_identity = None

            for plink in permalinks:
                parsed_id = parse_slack_message_permalink(plink)
                if parsed_id:
                    canonical_identity = parsed_id
                    break

            if not canonical_identity:
                logger.warning(
                    f"⚠️ [RESULT_IDENTITY_INVALID_SKIPPED] Result item #{idx+1} lacks valid canonical permalink identity. Skipping item."
                )
                eval_record["drop_reason"] = "RESULT_IDENTITY_INVALID"
                raw_items_evaluated.append(eval_record)
                invalid_items_skipped += 1
                invalid_identity_count += 1
                continue

            # Body validation
            if not itm.get("body_valid") or itm.get("ambiguous_fragment_count", 0) > 0:
                logger.warning(
                    f"⚠️ [RESULT_BODY_INVALID_SKIPPED] Result item #{idx+1} has invalid or ambiguous body extraction "
                    f"(body_valid={itm.get('body_valid')}, ambiguous_fragments={itm.get('ambiguous_fragment_count', 0)}). Skipping item."
                )
                eval_record["drop_reason"] = (
                    f"RESULT_BODY_INVALID (body_valid={itm.get('body_valid')}, "
                    f"ambiguous_fragments={itm.get('ambiguous_fragment_count', 0)})"
                )
                raw_items_evaluated.append(eval_record)
                invalid_items_skipped += 1
                invalid_body_count += 1
                continue

            eval_record["drop_reason"] = None
            raw_items_evaluated.append(eval_record)

            body_text = itm.get("normalized_body", "")
            author = itm.get("sender_text")
            author_id = itm.get("sender_author_id")
            timestamp_raw = itm.get("timestamp_raw")
            channel_id = canonical_identity.channel_id
            message_ts = canonical_identity.message_ts

            # Compute deduplication fingerprint
            fp_payload = f"{channel_id}|{message_ts}|{body_text}".encode("utf-8")
            fingerprint = hashlib.sha256(fp_payload).hexdigest()

            result_obj = SlackSearchResult(
                result_index=idx + 1,
                query=validated_query,
                author=author,
                author_id=author_id,
                timestamp_raw=timestamp_raw,
                text=body_text,
                channel_name=None,  # Channel name optional in DOM; resolved via channel_id
                channel_id=channel_id,
                context="search_result",
                result_url=permalinks[0] if permalinks else None,
                gold_message_ts=message_ts,
                gold_thread_ts=canonical_identity.thread_ts if canonical_identity else None,
                has_thread=itm.get("thread_reply_detected", False) or bool(canonical_identity and canonical_identity.thread_ts),
                message_fingerprint=fingerprint,
            )
            parsed_results.append(result_obj)

        telemetry["invalid_items_skipped"] = invalid_items_skipped
        telemetry["invalid_identity_count"] = invalid_identity_count
        telemetry["invalid_body_count"] = invalid_body_count
        telemetry["raw_items_count"] = len(raw_items)
        telemetry["valid_items_count"] = len(parsed_results)
        telemetry["raw_items_evaluated"] = raw_items_evaluated

        result_fingerprint_after = compute_result_set_fingerprint(raw_items_evaluated)

        # Forensic transition evaluations (Hard Requirement 1: Separate Query & Result Axes)
        norm_req = re.sub(r"\s+", " ", validated_query).strip().lower()
        norm_inp = re.sub(r"\s+", " ", search_input_value_after_submit).strip().lower()
        input_matches_requested = bool(
            norm_req and (norm_req == norm_inp or norm_req in norm_inp or norm_inp in norm_req)
        )

        norm_surf = re.sub(r"\s+", " ", surface_query_after or "").strip().lower()
        surface_query_matches_requested = bool(
            norm_surf and (norm_req in norm_surf or norm_surf in norm_req)
        )

        norm_before = re.sub(r"\s+", " ", surface_query_before or "").strip().lower()
        surface_query_changed = (
            (norm_before != norm_surf) if (norm_before and norm_surf) else bool(norm_surf)
        )

        result_fingerprint_changed: Optional[bool] = None
        if result_fingerprint_before is not None:
            result_fingerprint_changed = (result_fingerprint_before != result_fingerprint_after)

        # Classification ladder strictly conforming to Hard Requirement 1
        if not input_matches_requested:
            transition_classification = "QUERY_NOT_APPLIED"
        elif not submit_triggered:
            transition_classification = "QUERY_APPLIED_BUT_NOT_SUBMITTED"
        elif not surface_query_matches_requested:
            transition_classification = "QUERY_SUBMITTED_BUT_SURFACE_NOT_TRANSITIONED"
        elif result_fingerprint_changed is False:
            transition_classification = "RESULT_SET_UNCHANGED_AFTER_QUERY_CHANGE"
        elif result_fingerprint_changed is True:
            transition_classification = "RESULT_TRANSITIONED_AND_DIFFERENT_RESULTS"
        else:
            transition_classification = (
                "RESULT_TRANSITIONED_AND_DIFFERENT_RESULTS"
                if len(raw_items) > 0
                else "EMPTY_RESULTS"
            )

        forensic_transition = {
            "INPUT_MATCHES_REQUESTED": input_matches_requested,
            "SUBMIT_TRIGGERED": submit_triggered,
            "SEARCH_SURFACE_QUERY_BEFORE": surface_query_before,
            "SEARCH_SURFACE_QUERY_AFTER": surface_query_after,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_query_matches_requested,
            "SEARCH_SURFACE_QUERY_CHANGED": surface_query_changed,
            "RESULT_FINGERPRINT_BEFORE": result_fingerprint_before,
            "RESULT_FINGERPRINT_AFTER": result_fingerprint_after,
            "RESULT_FINGERPRINT_CHANGED": result_fingerprint_changed,
            "LOADING_OR_DOM_TRANSITION_OBSERVED": loading_or_dom_transition_observed,
            "TRANSITION_CLASSIFICATION": transition_classification,
            "requested_query": validated_query,
            "search_input_value_before": search_input_value_before,
            "search_input_value_after_submit": search_input_value_after_submit,
            "query_submit_triggered": submit_triggered,
            "result_fingerprint_before_submit": result_fingerprint_before,
            "result_fingerprint_after_wait": result_fingerprint_after,
            "result_change_detected": result_fingerprint_changed,
            "result_transition_wait_duration_ms": result_transition_wait_duration_ms,
            "current_search_surface_query": surface_query_after,
            "raw_result_count": len(raw_items),
            "raw_result_identifiers": [item.get("canonical_permalink") for item in raw_items_evaluated],
            "raw_result_text_hashes": [
                hashlib.sha256((item.get("normalized_body") or "").encode("utf-8")).hexdigest()[:12]
                for item in raw_items_evaluated
            ],
            "input_query_changed_but_result_fingerprint_unchanged": bool(
                result_fingerprint_changed is False
            ),
        }
        telemetry["forensic_transition"] = forensic_transition

        # Mandatory all-invalid escalation:
        # If raw result rows were returned by Slack, but not a single row could be qualified,
        # this is not proven NO_EVIDENCE. It indicates a systemic/structural DOM extraction anomaly.
        # It unconditionally escalates to fatal SEARCH_RESULT_STRUCTURE_CHANGED.
        # Row-level failure codes (RESULT_BODY_INVALID, RESULT_IDENTITY_INVALID) remain strictly item-local.
        if len(raw_items) > 0 and len(parsed_results) == 0:
            fatal_code = WebViewSearchFailureCode.SEARCH_RESULT_STRUCTURE_CHANGED
            telemetry["fatal_search_error"] = fatal_code.value
            self.last_search_telemetry = dict(telemetry)
            raise WebViewSearchError(
                fatal_code,
                f"All {len(raw_items)} search result rows failed strict validation "
                f"({invalid_items_skipped} skipped: {invalid_body_count} body invalid, "
                f"{invalid_identity_count} identity invalid).",
                details=telemetry,
            )

        self.last_search_telemetry = dict(telemetry)
        return parsed_results

    def search_messages(
        self, query: str, max_results: int = 10
    ) -> list[SlackSearchResult]:
        """Executes a typed search query and returns structured SlackSearchResult objects."""
        validated = self.validate_query(query)
        logger.info(
            f"🔍 [WEBVIEW_SLACK_SEARCH] Initiating search query (length={len(validated)}, max_results={max_results})"
        )
        return self._execute_search_pipeline(validated, max_results=max_results)

    def execute_search_session(
        self, query: str, max_results: int = 10
    ) -> SlackSearchSession:
        """Executes search and wraps results into a structured SlackSearchSession model."""
        validated = self.validate_query(query)
        results = self.search_messages(validated, max_results=max_results)

        session_id = str(uuid.uuid4())
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Generate viewport signature
        sig_raw = f"{session_id}:{len(results)}".encode("utf-8")
        sig = hashlib.sha256(sig_raw).hexdigest()[:16]

        return SlackSearchSession(
            search_session_id=session_id,
            searched_at=now_iso,
            requested_query=validated,
            observed_query=validated,
            query_verified=True,
            result_freshness_verified=True,
            result_signature=sig,
            result_count=len(results),
            collected_result_count=len(results),
            unique_collected_result_count=len(results),
            unique_result_count=len(results),
            results=results,
        )

    # SearchBackend Protocol Implementation
    async def search(
        self, query: SearchQuery, max_results: Optional[int] = None, **kwargs: Any
    ) -> list[EvidenceItem]:
        """Asynchronous SearchBackend protocol implementation returning EvidenceItems."""
        limit = max_results or self.default_max_results
        validated_q = self.validate_query(query.query)

        results = self.search_messages(validated_q, max_results=limit)
        evidence_items: list[EvidenceItem] = []
        seen_ids: set[str] = set()

        for res in results:
            canonical_id = res.result_url or res.gold_message_ts or res.message_fingerprint
            if canonical_id and canonical_id in seen_ids:
                continue
            if canonical_id:
                seen_ids.add(canonical_id)

            item = EvidenceItem(
                evidence_id=f"webview_ev_{len(evidence_items)+1:02d}",
                source="slack",
                author=res.author,
                author_id=res.author_id,
                timestamp=res.timestamp_raw,
                text=res.text,
                channel=res.channel_name or res.channel_id,
                message_identifier=canonical_id,
                thread_identifier=res.gold_thread_ts,
                has_thread=res.has_thread,
                has_attachments=False,
                attachment_count=0,
                attachment_names=[],
            )
            evidence_items.append(item)

        return evidence_items

    async def read_thread_result(
        self, candidate_item: EvidenceItem
    ) -> Any:
        """Reads mounted thread replies returning typed ThreadReadResult."""
        from pesu_agent.search.webview_thread_reader import WebviewThreadReader
        reader = WebviewThreadReader(host=self._window)
        result = reader.read_thread_result(candidate_item)
        self.last_thread_navigation_telemetry = reader.last_navigation_telemetry
        self.last_thread_read_result = result
        if not hasattr(self, "thread_navigation_telemetry_history"):
            self.thread_navigation_telemetry_history = []
        if reader.last_navigation_telemetry:
            self.thread_navigation_telemetry_history.append(dict(reader.last_navigation_telemetry))
        return result

    async def read_thread_context(
        self, candidate_item: EvidenceItem
    ) -> tuple[list[Any], Any]:
        """Reads mounted thread replies for an eligible thread candidate (backward compatible).

        Invariants:
        - GENERIC_NAVIGATE_URL_API_EXPOSED = false
        - MODEL_SUPPLIED_URL_ALLOWED = false
        - WILDCARD_SLACK_ORIGIN_ALLOWED = false
        - THREAD_AUTOMATED_SCROLL = false
        - NO_COMPLETENESS_PROOF_MEANS_PARTIAL = true
        """
        result = await self.read_thread_result(candidate_item)
        return list(result.replies), result.status

    async def collect_context(
        self, message_identifier: str, **kwargs: Any
    ) -> list[EvidenceItem]:
        """SearchBackend protocol context collection stub.

        Note: Navigation, message opening, and thread expansion in WebView have not yet
        undergone requalification. To avoid unverified DOM mutation or unverified navigation,
        this method raises a typed CONTEXT_COLLECTION_NOT_QUALIFIED error rather than
        silently returning an empty list as a false success.
        """
        logger.warning(
            f"⚠️ [WEBVIEW_COLLECT_CONTEXT] Context collection is not yet qualified in Dedicated WebView backend."
        )
        raise WebViewSearchError(
            WebViewSearchFailureCode.CONTEXT_COLLECTION_NOT_QUALIFIED,
            "Context collection is not yet qualified in Dedicated WebView backend.",
        )
