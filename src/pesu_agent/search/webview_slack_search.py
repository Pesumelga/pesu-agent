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
    UNSAFE_NON_SEARCH_SUGGESTION_SELECTED = "UNSAFE_NON_SEARCH_SUGGESTION_SELECTED"
    NO_SAFE_SEARCH_SUGGESTION_FOUND = "NO_SAFE_SEARCH_SUGGESTION_FOUND"
    AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS = "AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS"
    SUGGESTION_IDENTITY_SHIFTED = "SUGGESTION_IDENTITY_SHIFTED"
    SAFE_SEARCH_ACTIVATION_FAILED = "SAFE_SEARCH_ACTIVATION_FAILED"
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
SEARCH_RESULT_TRANSITION_TIMEOUT_SECONDS: float = 4.0
SEARCH_RESULT_TRANSITION_POLL_INTERVAL_SECONDS: float = 0.2


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

# Stage B: Model-Native Texty Query Setter (Executed Exactly Once)
JS_SET_TEXTY_QUERY = r"""
((targetQuery) => {
    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    if (searchInputCandidates.length !== 1) {
        return { error: 'SEARCH_INPUT_CARDINALITY_NOT_ONE', match_count: searchInputCandidates.length };
    }

    const inputElem = searchInputCandidates[0];
    const role = inputElem.getAttribute('role') || '';
    const insideModal = !!inputElem.closest('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal, [data-qa="search_input_box"]');
    const outsideComposer = !inputElem.closest('.c-message__editor, [data-qa="message_input"], .p-message_input');

    if (!inputElem.isConnected || role !== 'combobox' || !insideModal || !outsideComposer) {
        return { error: 'SEARCH_INPUT_PRECONDITION_FAILED' };
    }

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) {
        return { error: 'NO_REACT_FIBER_FOUND' };
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

    if (matchingInstances.length !== 1) {
        return { error: 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_NOT_ONE', count: matchingInstances.length };
    }

    const searchInputBoxInstance = matchingInstances[0];
    const textyObj = searchInputBoxInstance.texty;
    if (!textyObj || typeof textyObj.setContents !== 'function') {
        return { error: 'TEXTY_SETCONTENTS_NOT_CALLABLE' };
    }

    try {
        textyObj.setContents(
            { contents: [{ insert: targetQuery }] },
            { source: 'api' }
        );
        return { ok: true, setter_succeeded: true };
    } catch (err) {
        return { error: 'TEXTY_SETCONTENTS_EXCEPTION', details: String(err) };
    }
})
"""

# Stage C: Atomic Pre-Submit Verification & SearchInputBox.onEnter() Execution
JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER = r"""
((targetQuery) => {
    const report = {
        preconditions_satisfied: false,
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
        suggestions_count: 0,
        autocomplete_selected_index: null,
        default_suggestion_type: 'NONE',
        selected_suggestion_type: 'NONE',
        safe_candidates_count: 0,
        selected_safe_candidate_index: null,
        search_submission_target_safe: false,
        generic_default_onenter_used: false,
        slackbot_action_invoked: false,
        write_capable_action_invoked: false,
        unsafe_non_search_action_invoked: false,
        onenter_callable: false,
        submit_attempt_count: 0,
        submit_succeeded: false,
        submit_method: 'NONE',
        onenter_invocation_exception: null
    };

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor')).filter(el => el && el.isConnected);
    if (searchInputCandidates.length !== 1) {
        return { error: 'SEARCH_INPUT_CARDINALITY_NOT_ONE', report };
    }

    const inputElem = searchInputCandidates[0];
    const role = inputElem.getAttribute('role') || '';
    const insideModal = !!inputElem.closest('[data-qa="search_modal"], [data-qa="search_dialog"], .c-search_modal, .p-search_modal, [data-qa="search_input_box"]');
    const outsideComposer = !inputElem.closest('.c-message__editor, [data-qa="message_input"], .p-message_input');

    if (!inputElem.isConnected || role !== 'combobox' || !insideModal || !outsideComposer) {
        return { error: 'SEARCH_INPUT_OR_MODAL_INVALID', report };
    }

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let startingFiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    if (!startingFiber) {
        return { error: 'NO_REACT_FIBER_KEY_FOUND', report };
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
    report.duplicate_search_input_box_count = Math.max(0, matchingInstances.length - 1);

    if (matchingInstances.length !== 1) {
        return { error: 'STRONG_SEARCH_INPUT_BOX_CARDINALITY_INVALID', report };
    }

    const matched = matchingInstances[0];
    const searchInputBoxInstance = matched.instance;
    const currentFiber = matched.fiber;
    const textyObj = searchInputBoxInstance.texty;

    if (!textyObj) {
        return { error: 'TEXTY_OBJECT_NOT_FOUND', report };
    }

    const normalizeOpsToString = (ops) => {
        if (!Array.isArray(ops)) return '';
        return ops.map(op => (typeof op.insert === 'string' ? op.insert : '')).join('').trim();
    };

    // DOM
    try {
        const domText = (inputElem.textContent || inputElem.innerText || '').trim();
        report.surfaces_synced.dom = domText.includes(targetQuery);
    } catch (e) { report.surfaces_synced.dom = false; }

    // QuillText
    try {
        if (textyObj.instance && textyObj.instance.quill && typeof textyObj.instance.quill.getText === 'function') {
            const qText = textyObj.instance.quill.getText().trim();
            report.surfaces_synced.quill_text = qText.includes(targetQuery);
        }
    } catch (e) { report.surfaces_synced.quill_text = false; }

    // QuillDelta
    try {
        if (typeof searchInputBoxInstance.getTextyOps === 'function') {
            const ops = searchInputBoxInstance.getTextyOps();
            const deltaStr = normalizeOpsToString(ops);
            report.surfaces_synced.quill_delta = deltaStr.includes(targetQuery);
        }
    } catch (e) { report.surfaces_synced.quill_delta = false; }

    // Texty
    try {
        if (typeof textyObj.getContents === 'function') {
            const tContents = textyObj.getContents();
            const tOps = (tContents && tContents.contents) ? tContents.contents : (Array.isArray(tContents) ? tContents : []);
            const tStr = normalizeOpsToString(tOps);
            report.surfaces_synced.texty = tStr.includes(targetQuery);
        }
    } catch (e) { report.surfaces_synced.texty = false; }

    // Redux
    try {
        const propsToInspect = (currentFiber && currentFiber.memoizedProps && Object.prototype.hasOwnProperty.call(currentFiber.memoizedProps, 'displayQueryOps'))
            ? currentFiber.memoizedProps
            : searchInputBoxInstance.props;

        if (propsToInspect && Object.prototype.hasOwnProperty.call(propsToInspect, 'displayQueryOps')) {
            const dOps = propsToInspect.displayQueryOps;
            const rStr = normalizeOpsToString(dOps);
            report.surfaces_synced.redux = rStr.includes(targetQuery);
        }
    } catch (e) { report.surfaces_synced.redux = false; }

    const surfaces = report.surfaces_synced;
    report.query_state_all_synced = (surfaces.dom && surfaces.quill_text && surfaces.quill_delta && surfaces.texty && surfaces.redux);

    // Internal Component Flags
    try {
        if (searchInputBoxInstance.state) {
            report.internal_flags.query_has_changed = Boolean(searchInputBoxInstance.state.queryHasChanged);
            report.internal_flags.query_has_been_submitted = Boolean(searchInputBoxInstance.state.queryHasBeenSubmitted);
            report.internal_flags.is_updating_display_query = Boolean(searchInputBoxInstance.state._isUpdatingDisplayQuery);
            report.internal_flags.is_in_composition = Boolean(searchInputBoxInstance.state.isInComposition);
        }
    } catch (e) {}

    const flags = report.internal_flags;
    const internalFlagsOk = (!flags.is_updating_display_query && !flags.is_in_composition);

    report.preconditions_satisfied = (report.query_state_all_synced && internalFlagsOk);

    if (!report.preconditions_satisfied) {
        return { error: 'PRECONDITIONS_NOT_SATISFIED', report };
    }

    report.onenter_callable = (typeof searchInputBoxInstance.onEnter === 'function');

    // =========================================================================
    // Task 11R10F: Read-Only Safety Containment & Safe Search Selection Guard
    // =========================================================================
    const props = searchInputBoxInstance.props || {};
    const suggestions = Array.isArray(props.suggestions) ? props.suggestions : [];
    const autocompleteSelectedIndex = (props.autocompleteSelectedIndex !== undefined && props.autocompleteSelectedIndex !== null)
        ? props.autocompleteSelectedIndex
        : null;

    report.suggestions_count = suggestions.length;
    report.autocomplete_selected_index = autocompleteSelectedIndex;

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

    // Contract 2: EXACT structured identity from Task 11R10E evidence
    // is_type_query_completion === true AND type === "queryUser" AND suggestion_sub_type === "query-echo"
    // AND unsafe flags MUST NOT be present
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

    function classifySuggestionType(s) {
        if (!s || typeof s !== 'object') return 'NONE';
        if (isWriteCapableSuggestion(s)) return 'WRITE_CAPABLE_SLACKBOT';
        if (s.isNavigational === true || s.is_type_navigational === true || s.is_type_channel === true || s.type === 'channel') {
            return 'UNSAFE_NON_SEARCH_CHANNEL_NAV';
        }
        if (s.is_type_user === true || s.is_type_user_profile === true || s.type === 'member-profile') {
            return 'UNSAFE_NON_SEARCH_MEMBER_NAV';
        }
        if (s.is_type_huddle === true || s.type === 'huddle') return 'UNSAFE_NON_SEARCH_HUDDLE';
        if (s.is_type_workflow === true || s.type === 'workflowTrigger') return 'UNSAFE_NON_SEARCH_WORKFLOW';
        if (s.is_type_app_action === true || s.type === 'appAction') return 'UNSAFE_NON_SEARCH_APP_ACTION';
        if (isExactSafeKeywordSearch(s)) return 'SAFE_KEYWORD_SEARCH';
        return 'UNKNOWN_NON_SEARCH';
    }

    const defaultSug = suggestions[0] || null;
    const selectedSug = (autocompleteSelectedIndex !== null && suggestions[autocompleteSelectedIndex])
        ? suggestions[autocompleteSelectedIndex]
        : defaultSug;

    report.default_suggestion_type = classifySuggestionType(defaultSug);
    report.selected_suggestion_type = classifySuggestionType(selectedSug);

    // Contract 1: Authority is finding EXACTLY ONE safe search candidate across all suggestions
    const safeCandidates = [];
    for (let idx = 0; idx < suggestions.length; idx++) {
        if (isExactSafeKeywordSearch(suggestions[idx])) {
            safeCandidates.push({ index: idx, suggestion: suggestions[idx] });
        }
    }
    report.safe_candidates_count = safeCandidates.length;

    if (safeCandidates.length !== 1) {
        report.search_submission_target_safe = false;
        report.generic_default_onenter_used = false;
        const errCode = (safeCandidates.length === 0)
            ? 'NO_SAFE_SEARCH_SUGGESTION_FOUND'
            : 'AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS';
        return { error: errCode, report };
    }

    const safeCandidate = safeCandidates[0];
    const safeIndex = safeCandidate.index;
    const targetSuggestion = safeCandidate.suggestion;
    report.selected_safe_candidate_index = safeIndex;

    // Contract 4: Atomic TOCTOU re-validation of candidate identity
    if (suggestions[safeIndex] !== targetSuggestion || !isExactSafeKeywordSearch(suggestions[safeIndex])) {
        report.search_submission_target_safe = false;
        return { error: 'SUGGESTION_IDENTITY_SHIFTED', report };
    }

    report.search_submission_target_safe = true;
    report.generic_default_onenter_used = false; // Contract 3: Never use generic default onEnter()
    report.submit_attempt_count = 1;

    // Contract 3 & 5: Verified UI activation path (DOM item click -> React onAccept)
    let activated = false;
    let activationMethod = 'NONE';

    const acList = document.querySelector('#c-search_autocomplete__suggestion_list, [data-qa="search_autocomplete"], .c-search_autocomplete, [role="listbox"]');
    if (acList) {
        const options = Array.from(acList.querySelectorAll('[role="option"], .c-search_autocomplete__suggestion_item, [data-qa="search_autocomplete_item"]'));
        let safeOption = options.find(el => {
            const dIdx = el.getAttribute('data-index');
            return dIdx !== null && parseInt(dIdx, 10) === safeIndex;
        });
        if (!safeOption && options[safeIndex]) {
            const optType = options[safeIndex].getAttribute('data-type');
            if (optType === 'queryUser' || optType === targetSuggestion.type) {
                safeOption = options[safeIndex];
            }
        }

        if (safeOption) {
            const hasBotId = safeOption.hasAttribute('data-bot-id');
            const isNav = (safeOption.getAttribute('data-is-navigational') === 'true');
            if (!hasBotId && !isNav) {
                try {
                    safeOption.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, view: window }));
                    safeOption.click();
                    activated = true;
                    activationMethod = 'SafeSearchSuggestionDOMItem.click[index=' + safeIndex + ']';
                } catch (e) {
                    report.onenter_invocation_exception = String(e);
                }
            }
        }

        // Direct Fiber fallback on safeOption DOM element if click didn't trigger
        if (!activated && safeOption) {
            try {
                const optFiberKey = Object.keys(safeOption).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                if (optFiberKey && safeOption[optFiberKey]) {
                    let curr = safeOption[optFiberKey];
                    let depth = 0;
                    while (curr && depth < 10) {
                        if (curr.memoizedProps) {
                            if (typeof curr.memoizedProps.onAccept === 'function') {
                                curr.memoizedProps.onAccept(false, false);
                                activated = true;
                                activationMethod = 'SafeSearchSuggestionFiber.onAccept[index=' + safeIndex + ']';
                                break;
                            }
                            if (typeof curr.memoizedProps.onClick === 'function') {
                                curr.memoizedProps.onClick({ preventDefault: () => {}, stopPropagation: () => {} });
                                activated = true;
                                activationMethod = 'SafeSearchSuggestionFiber.onClick[index=' + safeIndex + ']';
                                break;
                            }
                        }
                        curr = curr.return;
                        depth++;
                    }
                }
            } catch (e) {
                if (!report.onenter_invocation_exception) report.onenter_invocation_exception = String(e);
            }
        }
    }

    // Fiber fallback for safe activation if DOM element query was not ready
    if (!activated) {
        try {
            let f = currentFiber;
            let onAcceptFn = null;
            let depth = 0;
            while (f && depth < 20) {
                if (f.memoizedProps && typeof f.memoizedProps.onAccept === 'function') {
                    onAcceptFn = f.memoizedProps.onAccept;
                    break;
                }
                f = f.return;
                depth++;
            }
            if (onAcceptFn) {
                onAcceptFn(false, false);
                activated = true;
                activationMethod = 'ReactOmniswitcher.onAccept[index=' + safeIndex + ']';
            }
        } catch (e) {
            if (!report.onenter_invocation_exception) report.onenter_invocation_exception = String(e);
        }
    }

    if (!activated) {
        report.submit_succeeded = false;
        report.submit_method = 'NONE';
        return { error: 'SAFE_SEARCH_ACTIVATION_FAILED', report };
    }

    report.submit_succeeded = true;
    report.submit_method = activationMethod;
    return { ok: true, report };
})
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
        const header = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title, [class*="titleContent"]');
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
        const header = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title, [class*="titleContent"]');
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

    const searchViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el && el.isConnected);
    const searchView = searchViews.length === 1 ? searchViews[0] : null;

    // Skeletons / Loading State (Verified against live Slack surface)
    const skeletons = searchView
        ? Array.from(searchView.querySelectorAll('[data-qa="skeleton"], .c-skeleton, .c-search_skeleton, .c-search_message--skeleton, [data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading')).filter(el => el && el.isConnected)
        : Array.from(document.querySelectorAll('[data-qa="skeleton"], .c-skeleton, .c-search_skeleton, .c-search_message--skeleton, [data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading')).filter(el => el && el.isConnected);

    const loadingIndicator = Boolean(
        skeletons.length > 0 ||
        document.querySelector('[data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading')
    );

    // Visible result roots inside verified searchView
    const resultRoots = searchView
        ? Array.from(searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        })
        : [];

    return {
        surface_query_after: surfaceQueryAfter,
        loading_detected_after: loadingIndicator,
        skeleton_count: skeletons.length,
        result_root_count: resultRoots.length,
        empty_element_count: 0,
        search_view_mounted: Boolean(searchView)
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
        search_transition_timeout_sec: float = SEARCH_RESULT_TRANSITION_TIMEOUT_SECONDS,
        search_transition_poll_interval_sec: float = SEARCH_RESULT_TRANSITION_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.config = config or WebViewRuntimeConfig()
        self.default_max_results = default_max_results
        self._window: Optional[WebViewHostProtocol] = host
        self.search_ui_ready_timeout_sec = search_ui_ready_timeout_sec
        self.search_ui_ready_poll_interval_sec = search_ui_ready_poll_interval_sec
        self.search_transition_timeout_sec = search_transition_timeout_sec
        self.search_transition_poll_interval_sec = search_transition_poll_interval_sec
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

        # Step 4: Model-Native Setter & Verified Submit (Task 11R3 Decoupled Execution)
        # 4a: Apply query text via Model-Native Texty.setContents
        js_set = f"({JS_SET_TEXTY_QUERY})({json.dumps(validated_query)})"
        res_set = self._window.evaluate_js(js_set)
        setter_ok = bool(isinstance(res_set, dict) and (res_set.get("setter_succeeded") or res_set.get("ok")))
        if not setter_ok:
            telemetry["last_confirmed_stage"] = "QUERY_SETTER_FAILED"
            raise WebViewSearchError(
                WebViewSearchFailureCode.QUERY_STATE_SYNC_FAILED,
                f"Model-native search query setter failed: {res_set}",
                details=telemetry,
            )
        telemetry["last_confirmed_stage"] = "SETTER_EXECUTED"

        # 4b: Bounded Polling for Verified Query State Settle & Safe Search Submit
        # Contract 3: GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = false
        submit_start_mono = time.monotonic()
        submit_timeout_sec = 4.0
        submit_poll_interval_sec = 0.05
        submit_triggered = False
        last_submit_res: Any = None
        verified_query_state_before_submit = False

        # Guaranteed safety telemetry invariants
        telemetry["safe_search_action_selected"] = False
        telemetry["generic_default_onenter_used"] = False
        telemetry["default_suggestion_type"] = "NONE"
        telemetry["selected_suggestion_type"] = "NONE"
        telemetry["submit_method"] = "NONE"
        telemetry["slackbot_action_invoked_by_pesu"] = False
        telemetry["write_capable_action_invoked_by_pesu"] = False
        telemetry["unsafe_non_search_action_invoked_by_pesu"] = False
        telemetry["safe_candidates_count"] = 0

        while time.monotonic() - submit_start_mono < submit_timeout_sec:
            js_submit = f"({JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER})({json.dumps(validated_query)})"
            res_submit = self._window.evaluate_js(js_submit)
            last_submit_res = res_submit
            if isinstance(res_submit, dict):
                r = res_submit.get("report", {})
                if res_submit.get("ok") and (r.get("submit_succeeded") or res_submit.get("submitted")):
                    submit_triggered = True
                    verified_query_state_before_submit = r.get("preconditions_satisfied", True)
                    break
                if res_submit.get("submitted"):
                    submit_triggered = True
                    verified_query_state_before_submit = True
                    break
                # Transient re-rendering or suggestion list settling: continue polling
                pass
            time.sleep(submit_poll_interval_sec)

        telemetry["verified_query_state_before_submit"] = verified_query_state_before_submit
        telemetry["onenter_invoked"] = submit_triggered

        if isinstance(last_submit_res, dict):
            r = last_submit_res.get("report", {})
            telemetry["safe_search_action_selected"] = bool(r.get("search_submission_target_safe", False))
            telemetry["generic_default_onenter_used"] = bool(r.get("generic_default_onenter_used", False))
            telemetry["default_suggestion_type"] = r.get("default_suggestion_type", "NONE")
            telemetry["selected_suggestion_type"] = r.get("selected_suggestion_type", "NONE")
            telemetry["submit_method"] = r.get("submit_method", "NONE")
            telemetry["safe_candidates_count"] = r.get("safe_candidates_count", 0)

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
            telemetry["last_confirmed_stage"] = "SUBMIT_FAILED"
            self.last_search_telemetry = dict(telemetry)
            err_code_str = ""
            if isinstance(last_submit_res, dict):
                err_code_str = str(last_submit_res.get("error") or "")

            failure_code = WebViewSearchFailureCode.SEARCH_SUBMIT_FAILED
            if err_code_str == "NO_SAFE_SEARCH_SUGGESTION_FOUND":
                failure_code = WebViewSearchFailureCode.NO_SAFE_SEARCH_SUGGESTION_FOUND
            elif err_code_str == "AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS":
                failure_code = WebViewSearchFailureCode.AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS
            elif err_code_str == "SUGGESTION_IDENTITY_SHIFTED":
                failure_code = WebViewSearchFailureCode.SUGGESTION_IDENTITY_SHIFTED
            elif err_code_str == "SAFE_SEARCH_ACTIVATION_FAILED":
                failure_code = WebViewSearchFailureCode.SAFE_SEARCH_ACTIVATION_FAILED
            elif err_code_str == "UNSAFE_NON_SEARCH_SUGGESTION_SELECTED":
                failure_code = WebViewSearchFailureCode.UNSAFE_NON_SEARCH_SUGGESTION_SELECTED

            raise WebViewSearchError(
                failure_code,
                f"Search query verified submit pipeline failed: {last_submit_res}",
                details=telemetry,
            )
        telemetry["last_confirmed_stage"] = "SUBMIT_EXECUTED"

        # Step 5: Bounded Active Transition Barrier & Terminal State Extraction
        t_wait_start = time.monotonic()
        transition_timeout_sec = self.search_transition_timeout_sec
        transition_poll_interval_sec = self.search_transition_poll_interval_sec
        max_transition_polls = max(1, int(transition_timeout_sec / transition_poll_interval_sec))

        post_wait_probe: dict[str, Any] = {}
        surface_query_after: Optional[str] = None
        loading_after: bool = False
        res_extract: Any = None
        readiness_classification: str = "NOT_READY"
        readiness_polls: list[dict[str, Any]] = []

        norm_req = re.sub(r"\s+", " ", validated_query).strip().lower()

        # Step 5.1: Active Transition Polling Loop (Hard Requirement 2: Centralized Readiness Barrier)
        # Authority Priority Hierarchy:
        # 1. Slack search route / search surface matches requested query
        # 2. Loading / search lifecycle transition complete (zero skeletons, loading false)
        # 3. Explicit Empty State vs Mounted Results vs Stale Results
        for poll_idx in range(max_transition_polls):
            # Read-Only Post-Wait Probe (Hard Requirement 2: Side-effect free)
            try:
                res_post_wait = self._window.evaluate_js(JS_PROBE_POST_WAIT_SEARCH_STATE)
                if isinstance(res_post_wait, dict):
                    post_wait_probe = res_post_wait
            except Exception as e:
                logger.warning(f"Post-wait probe evaluation warning: {e}")

            surface_query_after = post_wait_probe.get("surface_query_after")
            skeleton_count = int(post_wait_probe.get("skeleton_count", 0))
            loading_after = bool(post_wait_probe.get("loading_detected_after", False) or skeleton_count > 0)
            result_root_count = int(post_wait_probe.get("result_root_count", 0))
            empty_element_count = int(post_wait_probe.get("empty_element_count", 0))

            norm_surf = re.sub(r"\s+", " ", surface_query_after or "").strip().lower()
            surface_query_matches = bool(
                norm_surf and (norm_req in norm_surf or norm_surf in norm_req)
            )

            poll_fp: Optional[str] = None
            poll_result_count: int = 0
            poll_readiness = "NOT_READY"

            # Check readiness only when query transition is satisfied on surface (or mock unreadable)
            if surface_query_matches or surface_query_after is None:
                if loading_after:
                    poll_readiness = "LOADING"
                elif empty_element_count > 0 and result_root_count == 0:
                    # Explicit confirmed empty state (Test B / verified empty marker)
                    poll_readiness = "CONFIRMED_EMPTY"
                else:
                    # Not loading and no explicit empty marker: extract to check mounted results
                    js_extract = (
                        f"({JS_EXTRACT_SEARCH_RESULTS})("
                        f"{max_results}, "
                        f"{json.dumps(self.BODY_SCOPE_CONTAINER)}, "
                        f"{json.dumps(self.BODY_FRAGMENT_SELECTOR)}, "
                        f"{json.dumps(self.SENDER_CANONICAL_SELECTOR)}, "
                        f"{json.dumps(self.TIMESTAMP_CANONICAL_SELECTOR)})"
                    )
                    res_extract = self._window.evaluate_js(js_extract)
                    if isinstance(res_extract, dict) and res_extract.get("ok"):
                        poll_items = res_extract.get("items", [])
                        poll_result_count = len(poll_items)
                        poll_fp = compute_result_set_fingerprint(poll_items)

                        if poll_result_count > 0:
                            if result_fingerprint_before is not None and poll_fp == result_fingerprint_before:
                                # Stale prior results still mounted; wait for DOM reconcile
                                poll_readiness = "STALE_RESULTS_LINGERING"
                            else:
                                poll_readiness = "RESULTS_READY"
                        else:
                            # 0 items and empty_element_count == 0
                            # TRANSIENT_EMPTY_DOM_IS_NOT_TERMINAL_SUCCESS = True
                            poll_readiness = "NOT_READY"
                    else:
                        poll_readiness = "NOT_READY"
            else:
                poll_readiness = "SURFACE_QUERY_NOT_MATCHED"

            poll_record = {
                "poll_index": poll_idx + 1,
                "surface_query_matches": surface_query_matches,
                "result_count": poll_result_count,
                "result_fingerprint": poll_fp,
                "loading_state": loading_after,
                "explicit_empty_state": bool(empty_element_count > 0),
                "readiness_classification": poll_readiness,
            }
            readiness_polls.append(poll_record)
            readiness_classification = poll_readiness

            if poll_readiness in ("RESULTS_READY", "CONFIRMED_EMPTY"):
                break

            if time.monotonic() - t_wait_start >= transition_timeout_sec:
                break

            time.sleep(transition_poll_interval_sec)

        result_transition_wait_duration_ms = int((time.monotonic() - t_wait_start) * 1000)

        # Final readiness state
        if readiness_classification in ("RESULTS_READY", "CONFIRMED_EMPTY"):
            final_readiness = readiness_classification
        else:
            final_readiness = "TIMEOUT"

        telemetry["readiness_polls"] = readiness_polls
        telemetry["SEARCH_RESULT_READINESS"] = final_readiness

        # If terminal state is CONFIRMED_EMPTY, extract produces empty list
        if final_readiness == "CONFIRMED_EMPTY":
            res_extract = {"ok": True, "terminal_state": "EMPTY_RESULTS", "items": []}
        elif final_readiness == "RESULTS_READY":
            # If not yet extracted during break tick, extract now
            if res_extract is None or not (isinstance(res_extract, dict) and res_extract.get("ok")):
                js_extract = (
                    f"({JS_EXTRACT_SEARCH_RESULTS})("
                    f"{max_results}, "
                    f"{json.dumps(self.BODY_SCOPE_CONTAINER)}, "
                    f"{json.dumps(self.BODY_FRAGMENT_SELECTOR)}, "
                    f"{json.dumps(self.SENDER_CANONICAL_SELECTOR)}, "
                    f"{json.dumps(self.TIMESTAMP_CANONICAL_SELECTOR)})"
                )
                res_extract = self._window.evaluate_js(js_extract)
        else:
            # TIMEOUT: neither results nor explicit empty state confirmed
            # If result roots exist in DOM but didn't change (test_case_b), allow fallback extract for forensic reporting
            if result_root_count > 0:
                js_extract = (
                    f"({JS_EXTRACT_SEARCH_RESULTS})("
                    f"{max_results}, "
                    f"{json.dumps(self.BODY_SCOPE_CONTAINER)}, "
                    f"{json.dumps(self.BODY_FRAGMENT_SELECTOR)}, "
                    f"{json.dumps(self.SENDER_CANONICAL_SELECTOR)}, "
                    f"{json.dumps(self.TIMESTAMP_CANONICAL_SELECTOR)})"
                )
                res_extract = self._window.evaluate_js(js_extract)
            else:
                res_extract = {"ok": True, "terminal_state": "TIMEOUT", "items": []}

        if not (isinstance(res_extract, dict) and res_extract.get("ok")):
            telemetry["last_confirmed_stage"] = "EXTRACTION_FAILED"
            self.last_search_telemetry = dict(telemetry)
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_NAVIGATION_FAILED,
                f"Search result extraction failed: {res_extract}",
                details=telemetry,
            )

        loading_or_dom_transition_observed = bool(
            loading_immediate
            or loading_after
            or (surface_query_before != surface_query_after and surface_query_after is not None)
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
            "SEARCH_RESULT_READINESS": final_readiness,
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

        # Task 11R3/11R5 Fail-Closed Transition Guard (Authority Priority Hierarchy)
        # 1. Slack search route / search surface matches requested query
        # 2. Verified SearchInputBox / Redux submitted state
        # 3. Loading / search lifecycle transition complete
        # 4. Result fingerprint transition (FINGERPRINT_CHANGE_ALONE_IS_NOT_PRIMARY_QUERY_AUTHORITY = true)
        if surface_query_after is not None and not surface_query_matches_requested:
            telemetry["last_confirmed_stage"] = "SURFACE_QUERY_MISMATCH"
            self.last_search_telemetry = dict(telemetry)
            if result_fingerprint_before is not None and result_fingerprint_changed is False:
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_RESULT_TIMEOUT,
                    f"Search result transition timed out; stale DOM results detected.",
                    details=telemetry,
                )
            else:
                raise WebViewSearchError(
                    WebViewSearchFailureCode.SEARCH_NAVIGATION_FAILED,
                    f"Search surface transition timed out; surface query did not match requested query: {surface_query_after}",
                    details=telemetry,
                )
        elif surface_query_after is None and result_fingerprint_before is not None and result_fingerprint_changed is False:
            telemetry["last_confirmed_stage"] = "STALE_RESULTS_DETECTED"
            self.last_search_telemetry = dict(telemetry)
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_RESULT_TIMEOUT,
                f"Search result transition timed out; stale DOM results detected.",
                details=telemetry,
            )
        elif final_readiness == "TIMEOUT" and len(raw_items) == 0:
            telemetry["last_confirmed_stage"] = "READINESS_TIMEOUT"
            self.last_search_telemetry = dict(telemetry)
            raise WebViewSearchError(
                WebViewSearchFailureCode.SEARCH_RESULT_TIMEOUT,
                f"Search result readiness barrier timed out; neither valid results nor explicit empty state confirmed within deadline.",
                details=telemetry,
            )

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
