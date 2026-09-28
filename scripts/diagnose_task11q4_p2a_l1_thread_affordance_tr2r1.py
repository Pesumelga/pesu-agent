"""Phase 13.5D / Task 11 / P2A / TR2-R1: Search-Row Thread Affordance Discovery Diagnostic Retry 1.

TR2 PURPOSE:
What connected/rendered interactive structures actually exist inside the
production top-5 Slack search-result rows that might relate structurally
to message/thread navigation?

Strict Boundaries:
- DISCOVERY ONLY: It must NOT decide safe to click, qualified interaction target,
  preferred navigation mechanism, or Attempt 5 target.
- ZERO INTERACTION: clicks = 0, scroll = 0, keyboard navigation = 0,
  thread expansion = 0, composer interaction = 0, write action = 0,
  provider call = 0, retry = 0.
- ONE SEARCH, ONE ATOMIC SNAPSHOT: all top-5 mounted rows evaluated in a single evaluation.
- PARITY HARD GATES:
  * TR2_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4 = dynamically proven true
  * TR2_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4 = dynamically proven true
  * TR2_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4 = dynamically proven true
  * TR2_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4 = dynamically proven true
  * TR2_SEARCH_RESULT_ROW_ENUMERATION_PARITY = dynamically proven true
- SENSITIVE CONTENT CONTRACT:
  * USER_MESSAGE_TEXT_CAPTURED = false
  * SENDER_TEXT_CAPTURED = false
  * RAW_HREF_CAPTURED = false
  * RAW_PERMALINK_CAPTURED = false
  * RAW_ARIA_LABEL_CAPTURED = false
  * RAW_TITLE_CAPTURED = false
  * INNER_TEXT_CAPTURED = false
  * TEXT_CONTENT_CAPTURED = false
  * INNER_HTML_CAPTURED = false
  * OUTER_HTML_CAPTURED = false
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
logger = logging.getLogger("task11q4_p2a_l1_thread_affordance_tr2r1")

# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5
MAX_SERIALIZED_CANDIDATES_PER_ROW = 50

PROTOCOL_NAME = "task11q4_p2a_l1_tr2r1_thread_affordance_discovery"
DIAGNOSTIC_RUN = "TR2R1_DISCOVERY_RUN_1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_tr2r1_thread_affordance_dry_run.json"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_tr2r1_thread_affordance.json"
PROTOCOL_FREEZE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json"
ATTEMPT_STARTED_MARKER_FILE = OUTPUT_DIR / "task11q4_p2a_l1_tr2r1_attempt_started.json"
FAILED_TR2_EVIDENCE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2_failed_live_evidence.json"

CONSUMED_TR2_RUNNER_SHA256 = "6cf4b96a84689aee7829199580d6dbb445bd8deac7b6831c338b200baea71338"
CONSUMED_TR2_PROTOCOL_SHA256 = "2a2632e25654983b5e30a6fb78856d01cf0551589f9bcf2fc97d0d9b65277dcd"
CONSUMED_TR2_TASK_LOG_SHA256 = "26252ea1829edfc92cf9b39dc50b28d3a93cbd6732eb8f814aaf6c689a3b97a3"
CONSUMED_TR2_FAILED_EVIDENCE_SHA256 = "895748e464df6f97448fe805e7873f4c1b595186b880faabc34af718ca7a0bf4"

AUTHORITATIVE_ATTEMPT4_SHA256 = "b2f4c6972e1e1297045c7fe7d7f1e50492225d3bd073b311ee1dcadd967a905d"
AUTHORITATIVE_RB2_SHA256 = "affb83a14725c587b219949681919771b6012463a029e3d79b2078a2a2039d64"
AUTHORITATIVE_TR1_SHA256 = "4ccd95175ec5ff26da5d1cfbd2cb663f7f12034093ec4eccf466f4e7701cc93c"

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

SEARCH_VIEW_SELECTOR = '[data-qa="search_view"]'
SEARCH_RESULT_ROW_SELECTORS = '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

DISCOVERY_CLASSIFICATION_TOKENS = [
    "SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT",
    "THREAD_ROOT_PERMALINK_ANCHOR_PRESENT",
    "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT",
    "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT",
    "THREAD_TOKEN_DATA_QA_PRESENT",
    "THREAD_TOKEN_ARIA_DERIVED_PRESENT",
    "REPLY_TOKEN_ARIA_DERIVED_PRESENT",
    "ROLE_BUTTON_PRESENT",
    "BUTTON_PRESENT",
    "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED",
]

THREAD_RELATED_TOKENS = [
    "THREAD_ROOT_PERMALINK_ANCHOR_PRESENT",
    "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT",
    "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT",
    "THREAD_TOKEN_DATA_QA_PRESENT",
    "THREAD_TOKEN_ARIA_DERIVED_PRESENT",
    "REPLY_TOKEN_ARIA_DERIVED_PRESENT",
]

PRIMARY_DEPENDENCY_PATHS = {
    "tr2r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py",
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

# ---------------------------------------------------------------------------
# Discovery JavaScript
# ---------------------------------------------------------------------------

JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY = r"""
(searchViewSel, searchRowSel, maxRows, maxSerializedCandidates) => {
    try {
        const searchViews = document.querySelectorAll(searchViewSel);
        if (searchViews.length !== 1) {
            return {
                ok: false,
                error: "SEARCH_VIEW_COUNT_INVALID",
                observed_count: searchViews.length,
                rows: []
            };
        }
        const searchView = searchViews[0];

        // Authoritative production row enumeration
        const allRows = Array.from(searchView.querySelectorAll(searchRowSel)).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });

        const candidateRows = allRows.slice(0, maxRows);

        function normTs(raw) {
            if (!raw) return null;
            let s = String(raw).trim();
            if (s.startsWith("p") && s.length === 17 && /^\d+$/.test(s.slice(1))) {
                s = s.slice(1, 11) + "." + s.slice(11);
            }
            if (/^\d{10}\.\d{6}$/.test(s)) return s;
            if (/^\d{10}\.\d{3}$/.test(s)) return s + "000";
            if (/^\d{16}$/.test(s)) return s.slice(0, 10) + "." + s.slice(10);
            if (/^\d+$/.test(s)) {
                let f = parseFloat(s);
                if (f > 1e11) f = f / 1e6;
                return f.toFixed(6);
            }
            return s;
        }

        function extractRowIdentities(row) {
            const anchors = Array.from(row.querySelectorAll('a[href]'));
            const identities = [];
            const permalinkRegex = /\/archives\/([A-Z0-9]+)\/p(\d{10})(\d{6})(?:\?([^#\s]*))?/;

            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                const m = href.match(permalinkRegex);
                if (m) {
                    const channelId = m[1];
                    const msgTs = normTs(m[2] + '.' + m[3]);
                    const qStr = m[4] || '';
                    const thM = qStr.match(/(?:^|&)thread_ts=([0-9.]+)/);
                    const threadTs = thM ? normTs(thM[1]) : null;
                    identities.push({
                        channel_id: channelId,
                        message_ts: msgTs,
                        thread_ts: threadTs,
                        is_thread_reply: (threadTs !== null && threadTs !== msgTs)
                    });
                }
            }

            const uniqueIdentities = [];
            const seen = new Set();
            for (const id of identities) {
                const key = `${id.channel_id}:${id.message_ts}:${id.thread_ts || ''}`;
                if (!seen.has(key)) {
                    seen.add(key);
                    uniqueIdentities.push(id);
                }
            }
            return uniqueIdentities;
        }

        const evaluatedRows = [];
        let anyRowHasThreadRelatedStructure = false;
        let anyRowSerializationTruncated = false;

        for (let idx = 0; idx < candidateRows.length; idx++) {
            const row = candidateRows[idx];
            const rank = idx + 1;

            const identities = extractRowIdentities(row);
            const primaryIdentity = identities.length > 0 ? identities[0] : null;
            const isThreadReply = primaryIdentity ? primaryIdentity.is_thread_reply : false;

            // Discovery Selector Families
            const familySelectors = ['a[href]', 'button', '[role="button"]', '[data-qa]'];
            let rawFamilyMatchCount = 0;
            const seenElements = new Set();
            const uniqueCandidates = [];

            for (const sel of familySelectors) {
                const matches = Array.from(row.querySelectorAll(sel));
                rawFamilyMatchCount += matches.length;
                for (const el of matches) {
                    if (!seenElements.has(el)) {
                        seenElements.add(el);
                        uniqueCandidates.push(el);
                    }
                }
            }

            const uniqueInteractiveCandidateCount = uniqueCandidates.length;
            const duplicateFamilyMatchesRemoved = rawFamilyMatchCount - uniqueInteractiveCandidateCount;

            const allCandidateRecords = [];
            const rowClassifications = new Set();

            for (let cIdx = 0; cIdx < uniqueCandidates.length; cIdx++) {
                const el = uniqueCandidates[cIdx];
                const tagName = (el.tagName || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const dataQa = el.getAttribute('data-qa') || null;
                const isConnected = !!el.isConnected;
                const r = el.getBoundingClientRect();
                const isRendered = (r.width > 0 && r.height > 0);
                const insideComposer = !!el.closest('[data-qa="message_input"], .c-message_input, [data-qa="composer"]');
                const insideReplyBar = !!el.closest('[data-qa="reply_bar"], .c-reply_bar');

                // Depth from result row
                let depth = 0;
                let cur = el;
                while (cur && cur !== row && cur !== document.body) {
                    depth++;
                    cur = cur.parentElement;
                }

                const isAnchor = (tagName === 'a' && el.hasAttribute('href'));
                const isButton = (tagName === 'button');
                const hasRoleButton = (role === 'button');
                const hasDataQa = (dataQa !== null);

                // Derived ARIA / Title booleans (RAW STRINGS NEVER PERSISTED)
                const rawAria = (el.getAttribute('aria-label') || '').toLowerCase();
                const rawTitle = (el.getAttribute('title') || '').toLowerCase();

                const ariaLabelMentionsThread = rawAria.includes('thread') || rawAria.includes('스레드');
                const ariaLabelMentionsReply = rawAria.includes('reply') || rawAria.includes('replies') || rawAria.includes('답글') || rawAria.includes('댓글');
                const ariaLabelMentionsViewThread = rawAria.includes('view thread') || rawAria.includes('스레드 보기');
                const titleMentionsThread = rawTitle.includes('thread') || rawTitle.includes('스레드');
                const titleMentionsReply = rawTitle.includes('reply') || rawTitle.includes('replies') || rawTitle.includes('답글') || rawTitle.includes('댓글');

                // Href Handling (RAW HREF NEVER PERSISTED)
                let isSlackMessagePermalink = false;
                let parsedChannelId = null;
                let parsedMessageTs = null;
                let parsedThreadTs = null;
                let identityMatchesRow = false;
                let pointsToThreadRoot = false;
                let pointsToSameMessage = false;
                let hasThreadTsParameter = false;

                if (isAnchor) {
                    const rawHref = el.getAttribute('href') || '';
                    const m = rawHref.match(/\/archives\/([A-Z0-9]+)\/p(\d{10})(\d{6})(?:\?([^#\s]*))?/);
                    if (m) {
                        isSlackMessagePermalink = true;
                        parsedChannelId = m[1];
                        parsedMessageTs = normTs(m[2] + '.' + m[3]);
                        const qStr = m[4] || '';
                        const thM = qStr.match(/(?:^|&)thread_ts=([0-9.]+)/);
                        if (thM) {
                            parsedThreadTs = normTs(thM[1]);
                            hasThreadTsParameter = true;
                        }

                        if (primaryIdentity) {
                            identityMatchesRow = (
                                parsedChannelId === primaryIdentity.channel_id &&
                                parsedMessageTs === primaryIdentity.message_ts &&
                                (
                                    (parsedThreadTs === null && primaryIdentity.thread_ts === null) ||
                                    (parsedThreadTs === primaryIdentity.thread_ts)
                                )
                            );
                            pointsToSameMessage = (
                                parsedChannelId === primaryIdentity.channel_id &&
                                parsedMessageTs === primaryIdentity.message_ts
                            );
                            if (isThreadReply && primaryIdentity.thread_ts) {
                                pointsToThreadRoot = (
                                    parsedChannelId === primaryIdentity.channel_id &&
                                    parsedMessageTs === primaryIdentity.thread_ts
                                );
                            } else if (!isThreadReply) {
                                pointsToThreadRoot = (
                                    parsedChannelId === primaryIdentity.channel_id &&
                                    parsedMessageTs === primaryIdentity.message_ts
                                );
                            }
                        }
                    }
                }

                // Element structural classifications
                const elementClassifications = [];
                if (isSlackMessagePermalink) {
                    if (pointsToSameMessage) {
                        elementClassifications.push("SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT");
                        rowClassifications.add("SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT");
                    }
                    if (pointsToThreadRoot && !pointsToSameMessage) {
                        elementClassifications.push("THREAD_ROOT_PERMALINK_ANCHOR_PRESENT");
                        rowClassifications.add("THREAD_ROOT_PERMALINK_ANCHOR_PRESENT");
                    }
                    if (hasThreadTsParameter && parsedThreadTs !== null) {
                        elementClassifications.push("THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT");
                        rowClassifications.add("THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT");
                    }
                    if (pointsToSameMessage && hasThreadTsParameter && parsedThreadTs !== null && parsedMessageTs !== parsedThreadTs) {
                        elementClassifications.push("SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT");
                        rowClassifications.add("SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT");
                    }
                }
                if (dataQa) {
                    const lowerDqa = dataQa.toLowerCase();
                    if (lowerDqa.includes('thread') || lowerDqa.includes('reply')) {
                        elementClassifications.push("THREAD_TOKEN_DATA_QA_PRESENT");
                        rowClassifications.add("THREAD_TOKEN_DATA_QA_PRESENT");
                    }
                }
                if (ariaLabelMentionsThread || ariaLabelMentionsViewThread || titleMentionsThread) {
                    elementClassifications.push("THREAD_TOKEN_ARIA_DERIVED_PRESENT");
                    rowClassifications.add("THREAD_TOKEN_ARIA_DERIVED_PRESENT");
                }
                if (ariaLabelMentionsReply || titleMentionsReply) {
                    elementClassifications.push("REPLY_TOKEN_ARIA_DERIVED_PRESENT");
                    rowClassifications.add("REPLY_TOKEN_ARIA_DERIVED_PRESENT");
                }
                if (hasRoleButton) {
                    elementClassifications.push("ROLE_BUTTON_PRESENT");
                    rowClassifications.add("ROLE_BUTTON_PRESENT");
                }
                if (isButton) {
                    elementClassifications.push("BUTTON_PRESENT");
                    rowClassifications.add("BUTTON_PRESENT");
                }

                allCandidateRecords.push({
                    candidate_index: cIdx + 1,
                    tag_name: tagName,
                    role: role,
                    data_qa: dataQa,
                    is_connected: isConnected,
                    is_rendered: isRendered,
                    inside_composer: insideComposer,
                    inside_reply_bar: insideReplyBar,
                    depth_from_result_row: depth,
                    is_anchor: isAnchor,
                    is_button: isButton,
                    has_role_button: hasRoleButton,
                    has_data_qa: hasDataQa,
                    aria_label_mentions_thread: ariaLabelMentionsThread,
                    aria_label_mentions_reply: ariaLabelMentionsReply,
                    aria_label_mentions_view_thread: ariaLabelMentionsViewThread,
                    title_mentions_thread: titleMentionsThread,
                    title_mentions_reply: titleMentionsReply,
                    is_slack_message_permalink: isSlackMessagePermalink,
                    parsed_channel_id: parsedChannelId,
                    parsed_message_ts: parsedMessageTs,
                    parsed_thread_ts: parsedThreadTs,
                    identity_matches_row: identityMatchesRow,
                    points_to_thread_root: pointsToThreadRoot,
                    points_to_same_message: pointsToSameMessage,
                    has_thread_ts_parameter: hasThreadTsParameter,
                    element_classifications: elementClassifications
                });
            }

            const rowClassArray = Array.from(rowClassifications);
            const hasThreadRelatedStructure = (
                rowClassifications.has("THREAD_ROOT_PERMALINK_ANCHOR_PRESENT") ||
                rowClassifications.has("THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT") ||
                rowClassifications.has("SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT") ||
                rowClassifications.has("THREAD_TOKEN_DATA_QA_PRESENT") ||
                rowClassifications.has("THREAD_TOKEN_ARIA_DERIVED_PRESENT") ||
                rowClassifications.has("REPLY_TOKEN_ARIA_DERIVED_PRESENT")
            );

            if (hasThreadRelatedStructure) {
                anyRowHasThreadRelatedStructure = true;
            }

            const candidatesTruncated = (allCandidateRecords.length > maxSerializedCandidates);
            if (candidatesTruncated) {
                anyRowSerializationTruncated = true;
            }
            const serializedRecords = allCandidateRecords.slice(0, maxSerializedCandidates);

            evaluatedRows.push({
                rank: rank,
                channel_id: primaryIdentity ? primaryIdentity.channel_id : null,
                message_ts: primaryIdentity ? primaryIdentity.message_ts : null,
                thread_ts: primaryIdentity ? primaryIdentity.thread_ts : null,
                unique_identity_count: identities.length,
                is_thread_reply: isThreadReply,
                raw_family_match_count: rawFamilyMatchCount,
                unique_interactive_candidate_count: uniqueInteractiveCandidateCount,
                duplicate_family_matches_removed: duplicateFamilyMatchesRemoved,
                total_unique_candidate_count: allCandidateRecords.length,
                serialized_candidate_count: serializedRecords.length,
                candidate_serialization_truncated: candidatesTruncated,
                row_classifications: rowClassArray.length > 0 ? rowClassArray : ["NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"],
                has_thread_related_structure: hasThreadRelatedStructure,
                candidate_records: serializedRecords
            });
        }

        return {
            ok: true,
            mounted_row_count: allRows.length,
            inspected_row_count: candidateRows.length,
            any_row_has_thread_related_structure: anyRowHasThreadRelatedStructure,
            any_row_serialization_truncated: anyRowSerializationTruncated,
            discovery_complete: true,
            discovery_loss_mode: "FULL_IN_MEMORY_EVALUATION",
            rows: evaluatedRows
        };
    } catch (e) {
        return {
            ok: false,
            error: String(e),
            rows: []
        };
    }
}
"""

# ---------------------------------------------------------------------------
# Telemetry Harvester & Safety Gate (Exact RB2/TR1 Parity)
# ---------------------------------------------------------------------------

def harvest_search_telemetry(
    backend: Any,
    search_exception: Optional[Exception],
    report: dict[str, Any],
) -> None:
    """Harvests backend.last_search_telemetry whether search_messages succeeds, fails, or raises.

    Ensures authoritative telemetry extraction and tri-state (True/False/UNKNOWN) semantics.
    Exact Attempt 4 parity:
    - Primary: backend.last_search_telemetry
    - Fallback: search_exception.details
    - Strictly forbids backend.last_telemetry as search authority.
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

    # 1. Activation dispatches
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

    # 6. Pre-submit query verification (Distinct from post-search surface query!)
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

    # 7. Post-search surface query verification (Correction 1: separate from pre-submit)
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

    # 9. Supplemental composer telemetry (RD1 Run 2 semantics: missing/UNKNOWN does not block; explicit True/non-zero fails closed)
    def _clean_supp(val: Any) -> Any:
        return "UNKNOWN" if val is None or val == "UNKNOWN" else val

    safety["composer_focus"] = _clean_supp(telemetry.get("composer_focus"))
    safety["composer_text_input"] = _clean_supp(telemetry.get("composer_text_input"))
    safety["composer_send"] = _clean_supp(telemetry.get("composer_send"))


def is_supplemental_safe(val: Any) -> bool:
    """Returns True if supplemental composer telemetry observation is safe.

    Semantics:
    - missing / UNKNOWN / None / NOT_RUN -> True (allowed; does not block VERIFIED_SAFE)
    - 0 / False -> True (safe supplemental observation)
    - explicit True / non-zero / any other value -> False (triggers FAIL_CLOSED)
    """
    if val in (0, None, "UNKNOWN", False, "NOT_RUN"):
        return True
    return False


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    """Evaluates authoritative search safety and query authority before trusting results.

    Exact Attempt 4 parity:
    - safe_search_action_selected == true
    - verified_query_state_before_submit == true
    - generic_default_onenter_used == false
    - slackbot_action_invoked_by_pesu == false
    - write_capable_action_invoked_by_pesu == false
    - unsafe_non_search_action_invoked_by_pesu == false
    - onenter_invoked == true (search_submissions == 1)
    - SEARCH_SURFACE_QUERY_MATCHES_REQUESTED == true
    - Supplemental composer telemetry: explicit True/non-zero fails closed.
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

    # Supplemental composer safety violations (missing/UNKNOWN does not block; explicit True/non-zero fails closed)
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

# ---------------------------------------------------------------------------
# Dynamic Parity Verifiers (Exact Attempt 4 / RB2 Parity)
# ---------------------------------------------------------------------------

def verify_host_dom_evaluation_api_parity(runner_path: Optional[Path] = None) -> bool:
    """Verifies that:
    1) PesuWebViewHost exposes callable evaluate_js and does not expose evaluate_script.
    2) The runner calls host.evaluate_js for atomic DOM snapshot without any evaluate_script calls.
    """
    from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost

    if not hasattr(PesuWebViewHost, "evaluate_js") or not callable(getattr(PesuWebViewHost, "evaluate_js")):
        logger.error("PesuWebViewHost does not expose callable evaluate_js")
        return False
    if hasattr(PesuWebViewHost, "evaluate_script"):
        logger.error("PesuWebViewHost unexpectedly exposes evaluate_script - expected evaluate_js only")
        return False

    target = runner_path or PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"]
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "evaluate_script":
            logger.error(f"TR2-R1 runner incorrectly accesses attribute evaluate_script at line {node.lineno}")
            return False

    has_evaluate_js_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "host" and node.func.attr == "evaluate_js":
                has_evaluate_js_call = True
                break

    if not has_evaluate_js_call:
        logger.error("TR2-R1 runner missing required host.evaluate_js call")
        return False

    return True


def verify_runtime_search_path_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies that TR2 live execution path has exact constructor, start_runtime, and search parity with Attempt 4."""
    target = runner_path or PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"]
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")

    required_patterns = [
        r'PesuWebViewHost\s*\(\s*user_data_folder\s*=\s*DIRECT_SLACK_PROFILE_PATH,\s*initial_url\s*=\s*"https://app\.slack\.com/client"',
        r'WebViewSlackSearchBackend\s*\(\s*config\s*=\s*WebViewRuntimeConfig\s*\(\s*storage_path\s*=\s*Path\s*\(\s*DIRECT_SLACK_PROFILE_PATH\s*\)\s*\),\s*host\s*=\s*host',
        r'backend\.start_runtime\s*\(\s*timeout\s*=\s*timeout\s*\)',
        r'backend\.search_messages\s*\(\s*TARGET_QUERY,\s*max_results\s*=\s*SEARCH_MAX_RESULTS\s*\)',
        r'host\.destroy\s*\(\s*\)',
    ]
    for pat in required_patterns:
        if not re.search(pat, content):
            logger.error(f"Missing required runtime pattern in TR2 runner: {pat}")
            return False
    return True


def verify_search_telemetry_authority_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies AST parity between TR2 and RB2 harvest_search_telemetry."""
    rb2_file = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py"
    target = runner_path or PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"]
    if not rb2_file.exists() or not target.exists():
        return False

    def extract_func_ast(src: str, func_name: str) -> str:
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                body = node.body[1:] if (isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)) else node.body
                node_copy = ast.FunctionDef(
                    name=node.name,
                    args=node.args,
                    body=body,
                    decorator_list=node.decorator_list,
                    returns=node.returns
                )
                return ast.dump(node_copy)
        raise ValueError(f"Function {func_name} not found")

    rb2_src = rb2_file.read_text(encoding="utf-8")
    tr2_src = target.read_text(encoding="utf-8")
    return extract_func_ast(tr2_src, "harvest_search_telemetry") == extract_func_ast(rb2_src, "harvest_search_telemetry")


def verify_search_exception_order_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies that search exception is captured, harvested in finally:, and re-raised before safety check."""
    target = runner_path or PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"]
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")

    exc_block_pattern = (
        r'try:\s+'
        r'raw_results\s*=\s*backend\.search_messages\(TARGET_QUERY,\s*max_results=SEARCH_MAX_RESULTS\)\s+'
        r'except Exception as se:\s+'
        r'search_exception\s*=\s*se\s+.*?'
        r'finally:\s+'
        r'harvest_search_telemetry\(backend,\s*search_exception,\s*report\)\s+'
        r'if search_exception is not None:\s+'
        r'raise search_exception\s+'
        r'safety_ok,\s*safety_reason\s*=\s*evaluate_search_safety_and_query_authority\(report\)'
    )
    return bool(re.search(exc_block_pattern, content, re.DOTALL))


def verify_search_safety_gate_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies AST parity between TR2 and RB2 evaluate_search_safety_and_query_authority."""
    rb2_file = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py"
    target = runner_path or PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"]
    if not rb2_file.exists() or not target.exists():
        return False

    def extract_func_ast(src: str, func_name: str) -> str:
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                body = node.body[1:] if (isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)) else node.body
                node_copy = ast.FunctionDef(
                    name=node.name,
                    args=node.args,
                    body=body,
                    decorator_list=node.decorator_list,
                    returns=node.returns
                )
                return ast.dump(node_copy)
        raise ValueError(f"Function {func_name} not found")

    rb2_src = rb2_file.read_text(encoding="utf-8")
    tr2_src = target.read_text(encoding="utf-8")
    return (
        extract_func_ast(tr2_src, "evaluate_search_safety_and_query_authority") == extract_func_ast(rb2_src, "evaluate_search_safety_and_query_authority")
        and extract_func_ast(tr2_src, "is_supplemental_safe") == extract_func_ast(rb2_src, "is_supplemental_safe")
    )


def verify_search_result_row_enumeration_parity(override_tr2_js: Optional[str] = None) -> bool:
    """Dynamically verifies that TR2 JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY exact row filter
    matches production JS_EXTRACT_SEARCH_RESULTS row filter.
    """
    prod_js = PROD_JS_EXTRACT_SEARCH_RESULTS
    tr2_js = override_tr2_js or JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY

    if SEARCH_VIEW_SELECTOR != PROD_SEARCH_VIEW_SELECTOR:
        logger.error(f"SEARCH_VIEW_SELECTOR mismatch: {SEARCH_VIEW_SELECTOR} != {PROD_SEARCH_VIEW_SELECTOR}")
        return False

    if SEARCH_RESULT_ROW_SELECTORS != PROD_SEARCH_RESULT_ROW_SELECTORS:
        logger.error(f"SEARCH_RESULT_ROW_SELECTORS mismatch: {SEARCH_RESULT_ROW_SELECTORS} != {PROD_SEARCH_RESULT_ROW_SELECTORS}")
        return False

    prod_required = [
        "querySelectorAll('[data-qa=\"search_view\"]')",
        "searchViews.length !== 1",
        "querySelectorAll('[data-qa=\"search_result\"], [data-qa=\"search_message_result_item\"], .c-search_result')",
        "r.width > 0 && r.height > 0",
        "resultRoots.slice(0, maxSampleCount)",
    ]
    for snippet in prod_required:
        if snippet not in prod_js:
            logger.error(f"Missing snippet in production JS: {snippet}")
            return False

    prod_filter_m = re.search(
        r'searchView\.querySelectorAll\([^)]+\)\)\.filter\(el => \{(.*?)\}\);',
        prod_js,
        re.DOTALL,
    )
    if not prod_filter_m:
        logger.error("Failed to extract production row filter body")
        return False
    prod_filter_norm = re.sub(r'\s+', '', prod_filter_m.group(1))

    tr2_required = [
        "document.querySelectorAll(searchViewSel)",
        "searchViews.length !== 1",
        "searchView.querySelectorAll(searchRowSel)",
        "r.width > 0 && r.height > 0",
        "allRows.slice(0, maxRows)",
    ]
    for snippet in tr2_required:
        if snippet not in tr2_js:
            logger.error(f"Missing snippet in TR2 JS: {snippet}")
            return False

    tr2_filter_m = re.search(
        r'searchView\.querySelectorAll\([^)]+\)\)\.filter\(el => \{(.*?)\}\);',
        tr2_js,
        re.DOTALL,
    )
    if not tr2_filter_m:
        logger.error("Failed to extract TR2 row filter body")
        return False
    tr2_filter_norm = re.sub(r'\s+', '', tr2_filter_m.group(1))

    if prod_filter_norm != tr2_filter_norm:
        logger.error(f"Row filter mismatch:\n  prod: {prod_filter_norm}\n  tr2:  {tr2_filter_norm}")
        return False

    return True

# ---------------------------------------------------------------------------
# Utility & Audit Functions
# ---------------------------------------------------------------------------

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
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        for line in res.stdout.splitlines():
            if line.startswith(" M") or line.startswith("M "):
                return True
        return False
    except Exception:
        return False


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    return {name: get_file_sha256(path) for name, path in PRIMARY_DEPENDENCY_PATHS.items()}


def get_all_loaded_project_files() -> dict[str, str]:
    repo_root_str = os.path.normcase(str(REPO_ROOT.resolve()))
    loaded_files = {}

    for mod_name, mod in list(sys.modules.items()):
        if not mod or not hasattr(mod, "__file__") or not mod.__file__:
            continue
        fpath = mod.__file__
        try:
            norm_fpath = os.path.normcase(str(Path(fpath).resolve()))
            if norm_fpath.startswith(repo_root_str):
                rel_path = os.path.relpath(norm_fpath, repo_root_str).replace("\\", "/")
                if rel_path.startswith("src/") or rel_path.startswith("scripts/"):
                    loaded_files[rel_path] = get_file_sha256(Path(fpath))
        except Exception:
            continue

    this_script = os.path.relpath(os.path.normcase(str(Path(__file__).resolve())), repo_root_str).replace("\\", "/")
    if this_script not in loaded_files:
        loaded_files[this_script] = get_file_sha256(Path(__file__).resolve())

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
        "mismatched_runtime_dependency_count": len(mismatched_files),
        "mismatched_runtime_dependencies": mismatched_files,
        "closure_audit_passed": (len(untracked_files) == 0 and len(mismatched_files) == 0),
    }


def build_mock_dry_run_inspection() -> dict[str, Any]:
    """Builds synthetic mock atomic inspection conforming exactly to safe schema for offline dry run."""
    mock_rows = []
    for rank in range(1, 6):
        ch_id = "C042WS25QAX"
        msg_ts = f"171807927{rank}.100000"

        candidates = [
            {
                "candidate_index": 1,
                "tag_name": "a",
                "role": "link",
                "data_qa": "message_content",
                "is_connected": True,
                "is_rendered": True,
                "inside_composer": False,
                "inside_reply_bar": False,
                "depth_from_result_row": 3,
                "is_anchor": True,
                "is_button": False,
                "has_role_button": False,
                "has_data_qa": True,
                "aria_label_mentions_thread": False,
                "aria_label_mentions_reply": False,
                "aria_label_mentions_view_thread": False,
                "title_mentions_thread": False,
                "title_mentions_reply": False,
                "is_slack_message_permalink": True,
                "parsed_channel_id": ch_id,
                "parsed_message_ts": msg_ts,
                "parsed_thread_ts": None,
                "identity_matches_row": True,
                "points_to_thread_root": False,
                "points_to_same_message": True,
                "has_thread_ts_parameter": False,
                "element_classifications": ["SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT"]
            },
            {
                "candidate_index": 2,
                "tag_name": "button",
                "role": "button",
                "data_qa": "more_actions",
                "is_connected": True,
                "is_rendered": True,
                "inside_composer": False,
                "inside_reply_bar": False,
                "depth_from_result_row": 4,
                "is_anchor": False,
                "is_button": True,
                "has_role_button": True,
                "has_data_qa": True,
                "aria_label_mentions_thread": False,
                "aria_label_mentions_reply": False,
                "aria_label_mentions_view_thread": False,
                "title_mentions_thread": False,
                "title_mentions_reply": False,
                "is_slack_message_permalink": False,
                "parsed_channel_id": None,
                "parsed_message_ts": None,
                "parsed_thread_ts": None,
                "identity_matches_row": False,
                "points_to_thread_root": False,
                "points_to_same_message": False,
                "has_thread_ts_parameter": False,
                "element_classifications": ["ROLE_BUTTON_PRESENT", "BUTTON_PRESENT"]
            }
        ]

        mock_rows.append({
            "rank": rank,
            "channel_id": ch_id,
            "message_ts": msg_ts,
            "thread_ts": None,
            "unique_identity_count": 1,
            "is_thread_reply": False,
            "raw_family_match_count": 3,
            "unique_interactive_candidate_count": 2,
            "duplicate_family_matches_removed": 1,
            "total_unique_candidate_count": 2,
            "serialized_candidate_count": 2,
            "candidate_serialization_truncated": False,
            "row_classifications": ["ROLE_BUTTON_PRESENT", "BUTTON_PRESENT", "SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT"],
            "has_thread_related_structure": False,
            "candidate_records": candidates
        })

    return {
        "ok": True,
        "mounted_row_count": 20,
        "inspected_row_count": 5,
        "any_row_has_thread_related_structure": False,
        "any_row_serialization_truncated": False,
        "discovery_complete": True,
        "discovery_loss_mode": "FULL_IN_MEMORY_EVALUATION",
        "rows": mock_rows
    }

# ---------------------------------------------------------------------------
# Runner Execution
# ---------------------------------------------------------------------------

def run_tr2_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / TR2-R1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": git_commit,
        "uncommitted_diff": git_diff,
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
        },
        "live_action_inventory": {
            "search_activation_dispatches": 0,
            "search_modal_observed": False,
            "search_input_ready": "UNKNOWN",
            "search_submissions": "UNKNOWN",
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
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
            "SENDER_TEXT_CAPTURED": False,
            "RAW_HREF_CAPTURED": False,
            "RAW_PERMALINK_CAPTURED": False,
            "RAW_ARIA_LABEL_CAPTURED": False,
            "RAW_TITLE_CAPTURED": False,
            "INNER_TEXT_CAPTURED": False,
            "TEXT_CONTENT_CAPTURED": False,
            "INNER_HTML_CAPTURED": False,
            "OUTER_HTML_CAPTURED": False,
            "TR2_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": False,
            "TR2_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4": False,
            "TR2_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4": False,
            "TR2_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4": False,
            "TR2_SEARCH_RESULT_ROW_ENUMERATION_PARITY": False,
            "TR2R1_HOST_DOM_EVALUATION_API_PARITY": False,
            "historical_baselines_matched": False,
            "attempt_marker_written": False,
            "UNTRACKED_RUNTIME_DEPENDENCIES": 0,
            "ACTION_CONTRACTS_SATISFIED": True,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": "UNKNOWN",
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": "UNKNOWN",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": "UNKNOWN",
        },
        "atomic_inspection": None,
        "runtime_closure_audit": None,
        "provider_calls": 0,
        "retry_occurred": False,
        "live_execution_started": False,
        "live_execution_completed": False,
        "harness_phases": [],
        "error": None,
        "duration_seconds": 0.0,
    }

    # 1. Live Artifact & Attempt Marker Overwrite / Replay Guards
    if not dry_run:
        if LIVE_OUTPUT_FILE.exists():
            err_msg = "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
            logger.error(f"❌ {err_msg}: {LIVE_OUTPUT_FILE}")
            report["error"] = err_msg
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "LIVE_ARTIFACT_ALREADY_EXISTS"
            return report

        if ATTEMPT_STARTED_MARKER_FILE.exists():
            err_msg = "ATTEMPT_MARKER_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
            logger.error(f"❌ {err_msg}: {ATTEMPT_STARTED_MARKER_FILE}")
            report["error"] = err_msg
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
            return report

    # 2. Dynamic Parity Verifications (COMPUTED, NOT HARDCODED)
    row_enum_ok = verify_search_result_row_enumeration_parity()
    report["checks"]["TR2_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = row_enum_ok
    if not row_enum_ok:
        report["error"] = "SEARCH_RESULT_ROW_ENUMERATION_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_ROW_ENUMERATION_PARITY_MISMATCH"
        return report

    runtime_parity_ok = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["TR2_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = runtime_parity_ok
    if not runtime_parity_ok:
        report["error"] = "RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_RUNTIME_PARITY_MISMATCH"
        return report

    telemetry_parity_ok = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["TR2_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = telemetry_parity_ok
    if not telemetry_parity_ok:
        report["error"] = "SEARCH_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_TELEMETRY_PARITY_MISMATCH"
        return report

    exc_order_parity_ok = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["TR2_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = exc_order_parity_ok
    if not exc_order_parity_ok:
        report["error"] = "SEARCH_EXCEPTION_ORDER_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_EXCEPTION_ORDER_PARITY_MISMATCH"
        return report

    safety_gate_parity_ok = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["TR2_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = safety_gate_parity_ok
    if not safety_gate_parity_ok:
        report["error"] = "SEARCH_SAFETY_GATE_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_SAFETY_GATE_PARITY_MISMATCH"
        return report

    host_api_parity_ok = verify_host_dom_evaluation_api_parity()
    report["checks"]["TR2R1_HOST_DOM_EVALUATION_API_PARITY"] = host_api_parity_ok
    if not host_api_parity_ok:
        report["error"] = "HOST_DOM_EVALUATION_API_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HOST_API_PARITY_MISMATCH"
        return report

    # 3. Protocol Preflight Gate
    if not PROTOCOL_FREEZE_PATH.exists():
        err_msg = f"PROTOCOL_FREEZE_NOT_FOUND: {PROTOCOL_FREEZE_PATH}"
        logger.error(f"❌ {err_msg}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PROTOCOL_MISSING"
        return report

    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        protocol_manifest = json.load(f)

    # 4. Protocol Semantic Identity Validation
    semantic_errors = []
    if protocol_manifest.get("protocol") != PROTOCOL_NAME:
        semantic_errors.append(f"protocol mismatch: {protocol_manifest.get('protocol')} != {PROTOCOL_NAME}")
    if protocol_manifest.get("target_query") != TARGET_QUERY:
        semantic_errors.append(f"target_query mismatch: {protocol_manifest.get('target_query')} != {TARGET_QUERY}")
    if protocol_manifest.get("search_max_results") != SEARCH_MAX_RESULTS:
        semantic_errors.append(f"search_max_results mismatch: {protocol_manifest.get('search_max_results')} != {SEARCH_MAX_RESULTS}")
    if protocol_manifest.get("canonical_dry_run_verdict") != CANONICAL_DRY_RUN_VERDICT:
        semantic_errors.append(f"canonical_dry_run_verdict mismatch: {protocol_manifest.get('canonical_dry_run_verdict')} != {CANONICAL_DRY_RUN_VERDICT}")

    manifest_tokens = protocol_manifest.get("discovery_classification_tokens", [])
    if set(manifest_tokens) != set(DISCOVERY_CLASSIFICATION_TOKENS):
        semantic_errors.append(f"discovery_classification_tokens mismatch: {manifest_tokens} != {DISCOVERY_CLASSIFICATION_TOKENS}")

    manifest_thread_tokens = protocol_manifest.get("thread_related_tokens", [])
    if set(manifest_thread_tokens) != set(THREAD_RELATED_TOKENS):
        semantic_errors.append(f"thread_related_tokens mismatch: {manifest_thread_tokens} != {THREAD_RELATED_TOKENS}")

    try:
        expected_live_file = str(LIVE_OUTPUT_FILE.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        expected_live_file = protocol_manifest.get("live_output_file")
    if protocol_manifest.get("live_output_file") != expected_live_file:
        semantic_errors.append(f"live_output_file mismatch: {protocol_manifest.get('live_output_file')} != {expected_live_file}")

    try:
        expected_dry_file = str(DRY_RUN_OUTPUT_FILE.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        expected_dry_file = protocol_manifest.get("dry_run_output_file")
    if protocol_manifest.get("dry_run_output_file") != expected_dry_file:
        semantic_errors.append(f"dry_run_output_file mismatch: {protocol_manifest.get('dry_run_output_file')} != {expected_dry_file}")

    try:
        expected_marker_file = str(ATTEMPT_STARTED_MARKER_FILE.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        expected_marker_file = protocol_manifest.get("attempt_started_marker_file")
    if protocol_manifest.get("attempt_started_marker_file") != expected_marker_file:
        semantic_errors.append(f"attempt_started_marker_file mismatch: {protocol_manifest.get('attempt_started_marker_file')} != {expected_marker_file}")

    comp_auth = protocol_manifest.get("comparison_authority", {})
    if comp_auth.get("attempt4_runner_sha256") != AUTHORITATIVE_ATTEMPT4_SHA256:
        semantic_errors.append(f"attempt4_runner_sha256 mismatch: {comp_auth.get('attempt4_runner_sha256')} != {AUTHORITATIVE_ATTEMPT4_SHA256}")
    if comp_auth.get("rb2_runner_sha256") != AUTHORITATIVE_RB2_SHA256:
        semantic_errors.append(f"rb2_runner_sha256 mismatch: {comp_auth.get('rb2_runner_sha256')} != {AUTHORITATIVE_RB2_SHA256}")
    if comp_auth.get("tr1_runner_sha256") != AUTHORITATIVE_TR1_SHA256:
        semantic_errors.append(f"tr1_runner_sha256 mismatch: {comp_auth.get('tr1_runner_sha256')} != {AUTHORITATIVE_TR1_SHA256}")
    if comp_auth.get("consumed_tr2_runner_sha256") != CONSUMED_TR2_RUNNER_SHA256:
        semantic_errors.append(f"consumed_tr2_runner_sha256 mismatch: {comp_auth.get('consumed_tr2_runner_sha256')} != {CONSUMED_TR2_RUNNER_SHA256}")
    if comp_auth.get("consumed_tr2_protocol_sha256") != CONSUMED_TR2_PROTOCOL_SHA256:
        semantic_errors.append(f"consumed_tr2_protocol_sha256 mismatch: {comp_auth.get('consumed_tr2_protocol_sha256')} != {CONSUMED_TR2_PROTOCOL_SHA256}")
    if comp_auth.get("consumed_tr2_task_log_sha256") != CONSUMED_TR2_TASK_LOG_SHA256:
        semantic_errors.append(f"consumed_tr2_task_log_sha256 mismatch: {comp_auth.get('consumed_tr2_task_log_sha256')} != {CONSUMED_TR2_TASK_LOG_SHA256}")
    if comp_auth.get("consumed_tr2_derived_evidence_sha256") != CONSUMED_TR2_FAILED_EVIDENCE_SHA256:
        semantic_errors.append(f"consumed_tr2_derived_evidence_sha256 mismatch: {comp_auth.get('consumed_tr2_derived_evidence_sha256')} != {CONSUMED_TR2_FAILED_EVIDENCE_SHA256}")

    parity_inv = comp_auth.get("parity_invariants", {})
    for p_flag in [
        "TR2_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4",
        "TR2_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4",
        "TR2_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4",
        "TR2_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4",
        "TR2_SEARCH_RESULT_ROW_ENUMERATION_PARITY",
        "TR2R1_HOST_DOM_EVALUATION_API_PARITY"
    ]:
        if parity_inv.get(p_flag) is not True:
            semantic_errors.append(f"{p_flag} is not True in comparison_authority")
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

    # 5. 10 Historical Baselines Verification
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
    if not hist_matched:
        report["error"] = f"HISTORICAL_BASELINE_MISMATCH: {'; '.join(hist_errors)}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HISTORICAL_HASH_MISMATCH"
        return report

    closure_audit = audit_runtime_import_closure(protocol_manifest)
    report["runtime_closure_audit"] = closure_audit
    report["checks"]["UNTRACKED_RUNTIME_DEPENDENCIES"] = closure_audit["untracked_loaded_project_dependency_count"]

    if not closure_audit["closure_audit_passed"]:
        report["error"] = f"RUNTIME_CLOSURE_AUDIT_FAILED: untracked={closure_audit['untracked_files']}, mismatched={closure_audit['mismatched_runtime_dependencies']}"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_CLOSURE_GATE_FAILED"
        return report

    report["harness_phases"].append("PREFLIGHT_GATES_PASSED")

    if dry_run:
        logger.info("🔍 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock inspection...")
        report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = True
        report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = True
        report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = True
        report["checks"]["search_safety_gate"] = "PASS"
        report["live_action_inventory"]["search_submissions"] = 1
        report["live_action_inventory"]["pure_read_dom_extractions"] = 1

        atomic_res = build_mock_dry_run_inspection()
        report["atomic_inspection"] = atomic_res
        report["checks"]["atomic_snapshot_executed"] = True
        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"
        report["harness_phases"].append("DRY_RUN_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 Saved dry-run trace to {DRY_RUN_OUTPUT_FILE}")
        return report

    # LIVE EXECUTION PATH (Attempt 4 Parity)
    def persist_live_artifact(rep: dict[str, Any]) -> None:
        """Persists immutable live result artifact for bounded handled results or failures."""
        if not dry_run and not LIVE_OUTPUT_FILE.exists():
            try:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                with open(LIVE_OUTPUT_FILE, "x", encoding="utf-8") as lf:
                    json.dump(rep, lf, indent=2, ensure_ascii=False)
                logger.info(f"📄 Saved live diagnostic trace to {LIVE_OUTPUT_FILE}")
            except FileExistsError:
                logger.warning(f"Live artifact already exists: {LIVE_OUTPUT_FILE}; overwrite prevented.")
            except Exception as pe:
                logger.error(f"❌ Failed to persist live artifact: {pe}")

    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")

    # Write immutable attempt-consumption marker BEFORE any live WebView / search action
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    marker_content = {
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "git_commit": git_commit,
        "runner_sha256": pre_hashes.get("tr2r1_runner"),
        "protocol_sha256": get_file_sha256(PROTOCOL_FREEZE_PATH),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "attempt_consumed": True
    }
    try:
        with open(ATTEMPT_STARTED_MARKER_FILE, "x", encoding="utf-8") as mf:
            json.dump(marker_content, mf, indent=2, ensure_ascii=False)
        report["checks"]["attempt_marker_written"] = True
        logger.info(f"🔒 [ATTEMPT_CONSUMED] Wrote attempt marker to {ATTEMPT_STARTED_MARKER_FILE}")
    except FileExistsError:
        err_msg = "ATTEMPT_MARKER_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        logger.error(f"❌ {err_msg}: {ATTEMPT_STARTED_MARKER_FILE}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
        return report

    host: Optional[PesuWebViewHost] = None
    try:
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (Attempt 4 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / TR2-R1 Thread Affordance Discovery",
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
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        report["harness_phases"].append("SEARCH_SAFETY_AND_QUERY_CORRELATION_VERIFIED")

        logger.info("🔬 [ATOMIC_DOM_SNAPSHOT] Evaluating top-5 rows for thread affordances in single JS snapshot...")
        js_atomic = (
            f"({JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY})"
            f"({json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
            f"{SEARCH_MAX_RESULTS}, "
            f"{MAX_SERIALIZED_CANDIDATES_PER_ROW})"
        )
        atomic_res = host.evaluate_js(js_atomic, timeout=15.0)
        report["atomic_inspection"] = atomic_res
        report["checks"]["atomic_snapshot_executed"] = True
        report["live_action_inventory"]["pure_read_dom_extractions"] = 1

        if not isinstance(atomic_res, dict) or not atomic_res.get("ok"):
            logger.error(f"❌ Atomic DOM snapshot failed: {atomic_res}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "ATOMIC_SNAPSHOT_FAILED"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        if not atomic_res.get("discovery_complete"):
            report["verdict"] = "DISCOVERY_INCOMPLETE_TRUNCATED"
            report["diagnostic_verdict"] = "DISCOVERY_INCOMPLETE_TRUNCATED"
        elif atomic_res.get("any_row_has_thread_related_structure"):
            report["verdict"] = "THREAD_RELATED_STRUCTURE_OBSERVED"
            report["diagnostic_verdict"] = "THREAD_RELATED_STRUCTURE_OBSERVED"
        else:
            report["verdict"] = "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"
            report["diagnostic_verdict"] = "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"

        report["live_execution_completed"] = True
        report["harness_phases"].append("LIVE_EXECUTION_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        persist_live_artifact(report)
        return report

    except Exception as e:
        logger.error(f"❌ TR2-R1 execution error: {e}", exc_info=True)
        report["error"] = str(e)
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "EXECUTION_EXCEPTION"
        report["duration_seconds"] = round(time.time() - start_time, 2)
        persist_live_artifact(report)
        return report
    finally:
        if host is not None:
            try:
                host.destroy()
            except Exception as e:
                logger.warning(f"⚠️ Error destroying host: {e}")


run_tr2r1_diagnostic = run_tr2_diagnostic

def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11 / TR2-R1: Search-Row Thread Affordance Discovery Retry 1")
    parser.add_argument("--dry-run", action="store_true", help="Run in offline dry-run mode")
    parser.add_argument("--timeout", type=float, default=30.0, help="Search timeout in seconds")
    args = parser.parse_args()

    report = run_tr2_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print(f"TR2 Verdict: {report.get('verdict')} (diagnostic_verdict: {report.get('diagnostic_verdict')})")
    if report.get("verdict") in (CANONICAL_DRY_RUN_VERDICT, "THREAD_RELATED_STRUCTURE_OBSERVED", "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
