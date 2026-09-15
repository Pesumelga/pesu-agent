"""Dedicated WebView Slack Read-Only Thread Context Reader (Phase 12.3l).

Implements bounded, read-only Thread Context Expansion v1 for PESU Slack runtime
using live-qualified native reply_bar click and click-correlated flexpane lifecycle.

Strict Invariants:
- GENERIC_NAVIGATE_URL_API_EXPOSED = false
- THREAD_READER_URL_NAVIGATION_FALLBACK = false
- MODEL_SUPPLIED_URL_ALLOWED = false
- WILDCARD_SLACK_ORIGIN_ALLOWED = false
- THREAD_AUTOMATED_SCROLL = false
- UNQUALIFIED_COMPLETENESS_PATH_DISABLED = true
- THREAD_EXISTENCE_INFERRED_FROM_ROOT_PERMALINK_ONLY = false
- PESU_COMPOSER_INTERACTION_EVENTS = 0 (zero blur, zero click, zero keyboard, zero focus)
- COMPOSER_ROW_CAN_BECOME_THREAD_REPLY = false
- EXACT_SEARCH_ROW_ELEMENT_REFERENCE_RETAINED = true
- SEARCH_ROW_REENUMERATED_BEFORE_CLICK = false
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
import re
import time
from typing import Any, Optional

from pesu_agent.investigation.models import (
    EvidenceItem,
    ThreadContextStatus,
    ThreadReplyItem,
)
from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)

logger = logging.getLogger(__name__)


class ThreadReadFailureReason(str, Enum):
    """Typed terminal outcome reasons for thread reading and Policy A residency."""

    TARGET_ROW_NOT_IN_CURRENT_DOM = "TARGET_ROW_NOT_IN_CURRENT_DOM"
    ROW_IDENTITY_AMBIGUOUS = "ROW_IDENTITY_AMBIGUOUS"
    REPLY_BAR_UNAVAILABLE = "REPLY_BAR_UNAVAILABLE"
    CLICK_CORRELATED_ROOT_FAILED = "CLICK_CORRELATED_ROOT_FAILED"
    MESSAGE_REGION_UNAVAILABLE = "MESSAGE_REGION_UNAVAILABLE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    ELIGIBILITY_FAILED = "ELIGIBILITY_FAILED"
    IDENTITY_PARSE_FAILED = "IDENTITY_PARSE_FAILED"


@dataclass(frozen=True)
class ThreadReadResult:
    """Immutable outcome of a thread context reading operation."""

    replies: tuple[ThreadReplyItem, ...]
    status: ThreadContextStatus
    failure_reason: Optional[ThreadReadFailureReason] = None
    resident_match_count: int = 0

# Shared Search Selectors (Single Source of Truth)
SEARCH_VIEW_SELECTOR: str = '[data-qa="search_view"]'
SEARCH_RESULT_ROW_SELECTORS: str = '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

# Bounded Resource Limits
MAX_ACCEPTED_REPLIES: int = 10
MAX_ROW_CANDIDATES: int = 15
MAX_BODY_CHARS_PER_ROW: int = 3000
MAX_FINAL_THREAD_CHARS: int = 3000
ROOT_DELTA_POLL_INTERVAL: float = 0.1
ROOT_DELTA_TIMEOUT: float = 3.0

THREAD_READER_URL_NAVIGATION_FALLBACK: bool = False
COMPOSER_ROW_CAN_BECOME_THREAD_REPLY: bool = False
UNQUALIFIED_COMPLETENESS_PATH_DISABLED: bool = True

# Script 1: State Cleanup / Sanitation
JS_CLEANUP_THREAD_OP_STATE = r"""
(() => {
    try {
        if (window.__pesu_thread_op) {
            delete window.__pesu_thread_op;
        }
    } catch (e) {}
})()
"""

# Script 2: Policy A Resident Search Row Inspector (Single Source of Truth)
JS_INSPECT_RESIDENT_SEARCH_ROWS = r"""
((searchViewSel, searchRowSel) => {
    try {
        window.__pesu_thread_op = {
            search_rows: [],
            before_roots: [],
            correlated_root: null
        };

        const searchViews = Array.from(document.querySelectorAll(searchViewSel)).filter(el => el && el.isConnected);
        if (searchViews.length !== 1) {
            return {
                ok: false,
                error: searchViews.length === 0 ? "SEARCH_VIEW_NOT_FOUND" : "SEARCH_VIEW_CARDINALITY_NOT_ONE",
                search_view_count: searchViews.length
            };
        }

        const searchView = searchViews[0];
        const resultRows = Array.from(searchView.querySelectorAll(searchRowSel)).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });

        if (resultRows.length === 0) {
            return { ok: true, rows: [] };
        }

        // Retain exact Element references in namespace (no fresh re-enumeration before click)
        window.__pesu_thread_op.search_rows = resultRows;

        const rowReports = [];
        for (let idx = 0; idx < resultRows.length; idx++) {
            const row = resultRows[idx];
            const anchors = Array.from(row.querySelectorAll('a[href]'));
            const permalinks = [];
            for (const a of anchors) {
                const h = (a.getAttribute('href') || '').trim();
                if (/\/archives\/[A-Z0-9]+\/p\d{16}/.test(h)) {
                    permalinks.push(h);
                }
            }
            rowReports.push({
                row_index: idx,
                permalinks: permalinks
            });
        }

        return {
            ok: true,
            rows: rowReports
        };
    } catch (err) {
        return { ok: false, error: String(err) };
    }
})
"""

# Script 3: Reply Bar Guard & Bounded Native Click on Retained Element Reference
JS_CLICK_RESIDENT_ROW_REPLY_BAR = r"""
((selectedRowIndex) => {
    try {
        const op = window.__pesu_thread_op;
        if (!op || !op.search_rows || !Array.isArray(op.search_rows)) {
            return { ok: false, error: "OPERATION_STATE_MISSING", click_executed: false };
        }

        const targetRow = op.search_rows[selectedRowIndex];
        if (!targetRow || !targetRow.isConnected) {
            return { ok: false, error: "TARGET_ROW_DISCONNECTED", click_executed: false };
        }

        // Exact reply-bar structural guard (Phase 12.3h-R3 / Phase 12.3k-R2 parity)
        const replyBars = Array.from(targetRow.querySelectorAll('div[data-qa="reply_bar"]')).filter(el => el && el.isConnected);
        const replyBarCount = replyBars.length;
        if (replyBarCount !== 1) {
            return {
                ok: false,
                guard_passed: false,
                guard_failure_reason: "REPLY_BAR_COUNT_INVALID",
                reply_bar_count: replyBarCount,
                click_executed: false
            };
        }

        const replyBar = replyBars[0];
        const countEl = replyBar.querySelector('[data-qa="reply_bar_count"]');
        const viewThreadEl = replyBar.querySelector('[data-qa="reply_bar_view_thread"]');
        if (!countEl || !viewThreadEl) {
            return {
                ok: false,
                guard_passed: false,
                guard_failure_reason: "REPLY_BAR_REQUIRED_CHILDREN_MISSING",
                reply_bar_count: replyBarCount,
                click_executed: false
            };
        }

        const rect = replyBar.getBoundingClientRect();
        const isVisible = (rect.width > 0 && rect.height > 0);
        if (!isVisible) {
            return {
                ok: false,
                guard_passed: false,
                guard_failure_reason: "REPLY_BAR_NOT_VISIBLE",
                reply_bar_count: replyBarCount,
                click_executed: false
            };
        }

        const inContentEditable = Boolean(replyBar.closest('[contenteditable="true"]'));
        const inComposer = Boolean(replyBar.closest('[data-qa="message_input"], .c-message_input, textarea, [role="textbox"]'));
        if (inContentEditable || inComposer) {
            return {
                ok: false,
                guard_passed: false,
                guard_failure_reason: "REPLY_BAR_INSIDE_COMPOSER",
                reply_bar_count: replyBarCount,
                click_executed: false
            };
        }

        // Capture pre-click roots
        const beforeRoots = Array.from(document.querySelectorAll('div[data-qa="threads_flexpane"]'));
        op.before_roots = beforeRoots;

        // Bounded native DOM click exactly once
        replyBar.click();

        return {
            ok: true,
            guard_passed: true,
            click_executed: true,
            pre_click_root_count: beforeRoots.length
        };
    } catch (err) {
        return { ok: false, error: String(err), click_executed: false };
    }
})
"""

# Script 4: State-Based Root Delta Poller (IIFE)
JS_POLL_ROOT_DELTA = r"""
(function() {
    try {
        const op = window.__pesu_thread_op;
        const beforeRoots = (op && op.before_roots) ? op.before_roots : [];
        const afterRoots = Array.from(document.querySelectorAll('div[data-qa="threads_flexpane"]'));
        const newRoots = afterRoots.filter(r => !beforeRoots.includes(r));

        if (newRoots.length === 1) {
            const clickCorrelatedRoot = newRoots[0];
            const r = clickCorrelatedRoot.getBoundingClientRect();
            if (op) {
                op.correlated_root = clickCorrelatedRoot;
            }
            return {
                ready: true,
                new_root_count: 1,
                post_click_count: afterRoots.length,
                pre_click_count: beforeRoots.length,
                rect: { width: Math.round(r.width), height: Math.round(r.height) }
            };
        }

        return {
            ready: false,
            new_root_count: newRoots.length,
            post_click_count: afterRoots.length,
            pre_click_count: beforeRoots.length
        };
    } catch (err) {
        return { ready: false, error: String(err) };
    }
})()
"""

# Script 5: Mounted Thread Reply Extractor (Two-Tier Bounded Limits)
JS_EXTRACT_MOUNTED_THREAD_REPLIES = r"""
((maxCandidateRows, maxBodyCharsPerRow, maxFinalChars, maxAcceptedReplies) => {
    try {
        const op = window.__pesu_thread_op;
        const clickCorrelatedRoot = op ? op.correlated_root : null;
        if (!clickCorrelatedRoot || !clickCorrelatedRoot.isConnected) {
            return { ok: false, error: "CLICK_CORRELATED_ROOT_LOST" };
        }

        // 1. Message region guard
        const messageRegions = Array.from(clickCorrelatedRoot.querySelectorAll(
            'div[data-qa="slack_kit_list"][role="list"]'
        )).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        });

        if (messageRegions.length !== 1) {
            return {
                ok: false,
                error: messageRegions.length === 0 ? "MESSAGE_REGION_NOT_FOUND" : "MESSAGE_REGION_CARDINALITY_NOT_ONE",
                message_region_count: messageRegions.length
            };
        }
        const messageRegion = messageRegions[0];

        // 2. Candidate virtual list items bounded to maxCandidateRows
        const rawRows = Array.from(messageRegion.querySelectorAll(
            'div[data-qa="virtual-list-item"][role="listitem"]'
        )).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        }).slice(0, maxCandidateRows);

        if (rawRows.length === 0) {
            return { ok: false, error: "NO_MOUNTED_ROWS_FOUND", rows_count: 0 };
        }

        const extractedReplies = [];
        let totalChars = 0;
        let composerRowsExcluded = 0;
        let rowsSkippedNoTimestamp = 0;
        let rowsSkippedAmbiguousTimestamp = 0;
        let multiBlockObserved = false;

        for (let rowIdx = 0; rowIdx < rawRows.length; rowIdx++) {
            const row = rawRows[rowIdx];

            // Composer row exclusion (mandatory)
            const hasComposerInput = Boolean(row.querySelector('[data-qa="message_input"], .c-message_input'));
            const hasContentEditable = Boolean(row.querySelector('[contenteditable="true"], textarea, [role="textbox"]'));
            const isComposerClosest = Boolean(row.closest('[data-qa="message_input"], .c-message_input'));
            if (hasComposerInput || hasContentEditable || isComposerClosest) {
                composerRowsExcluded++;
                continue;
            }

            // Timestamp anchor isolation (outside message body blocks)
            const rawTsAnchors = Array.from(row.querySelectorAll('a.c-timestamp')).filter(a => {
                if (!a || !a.isConnected) return false;
                const insideBlocks = Boolean(a.closest('.c-message_kit__blocks'));
                const insideRichBlock = Boolean(a.closest('.p-rich_text_block'));
                const insideRichSection = Boolean(a.closest('.p-rich_text_section'));
                const insideMsgBody = Boolean(a.closest('.c-message__body'));
                return !(insideBlocks || insideRichBlock || insideRichSection || insideMsgBody);
            });

            if (rawTsAnchors.length !== 1) {
                if (rawTsAnchors.length === 0) {
                    rowsSkippedNoTimestamp++;
                } else {
                    rowsSkippedAmbiguousTimestamp++;
                }
                continue;
            }
            const ownAnchor = rawTsAnchors[0];
            const href = (ownAnchor.getAttribute('href') || '').trim();

            // Scoped body extraction: .c-message_kit__blocks -> all .p-rich_text_section in DOM order
            const blocks = row.querySelector('.c-message_kit__blocks');
            if (!blocks) {
                continue;
            }
            const sections = Array.from(blocks.querySelectorAll('.p-rich_text_section'));
            if (sections.length > 1) {
                multiBlockObserved = true;
            }
            const texts = sections.map(s => (s.textContent || '').trim()).filter(Boolean);
            let bodyText = texts.join('\n');
            if (!bodyText) {
                continue;
            }

            if (bodyText.length > maxBodyCharsPerRow) {
                bodyText = bodyText.slice(0, maxBodyCharsPerRow);
            }

            // Enforce cumulative char bound
            if (totalChars >= maxFinalChars) {
                break;
            }
            if (totalChars + bodyText.length > maxFinalChars) {
                bodyText = bodyText.slice(0, maxFinalChars - totalChars);
            }

            // Optional author extraction: [data-qa="message_sender_name"]
            const senderNodes = Array.from(row.querySelectorAll('[data-qa="message_sender_name"]')).filter(e => e && e.isConnected);
            let author = null;
            if (senderNodes.length === 1) {
                const s = (senderNodes[0].textContent || '').trim();
                if (s.length > 0) {
                    author = s;
                }
            }

            extractedReplies.push({
                href: href,
                text: bodyText,
                author: author
            });
            totalChars += bodyText.length;

            if (extractedReplies.length >= maxAcceptedReplies) {
                break;
            }
        }

        // Clean up operation state on successful completion
        delete window.__pesu_thread_op;

        return {
            ok: true,
            replies: extractedReplies,
            virtual_items_total: rawRows.length,
            composer_rows_excluded: composerRowsExcluded,
            rows_skipped_no_timestamp: rowsSkippedNoTimestamp,
            rows_skipped_ambiguous_timestamp: rowsSkippedAmbiguousTimestamp,
            multi_block_observed: multiBlockObserved
        };
    } catch (err) {
        return { ok: false, error: String(err) };
    }
})
"""

JS_EXTRACT_THREAD_REPLIES = JS_EXTRACT_MOUNTED_THREAD_REPLIES


def normalize_slack_ts(ts: Optional[str]) -> Optional[str]:
    """Normalizes Slack timestamp to canonical 'seconds.microseconds' string format."""
    if not ts or not isinstance(ts, str):
        return None
    ts = ts.strip()
    m = re.match(r"^(\d+)(?:\.(\d+))?$", ts)
    if not m:
        return None
    sec = m.group(1)
    usec = (m.group(2) or "").ljust(6, "0")[:6]
    return f"{sec}.{usec}"


def classify_message_thread_role(
    message_ts: Optional[str],
    thread_ts: Optional[str],
) -> str:
    """Classifies message role within thread hierarchy (Parent / Reply Identity Rule).

    Invariants:
    - thread_ts absent -> STANDALONE_OR_ROOT
    - thread_ts present + message_ts == thread_ts -> THREAD_ROOT
    - thread_ts present + message_ts != thread_ts -> THREAD_REPLY
    - Timestamp normalization before comparison.
    - Ambiguity fails closed.
    """
    norm_thr = normalize_slack_ts(thread_ts)
    if norm_thr is None:
        return "STANDALONE_OR_ROOT"

    norm_msg = normalize_slack_ts(message_ts)
    if norm_msg is None:
        return "AMBIGUOUS"

    if norm_msg == norm_thr:
        return "THREAD_ROOT"
    return "THREAD_REPLY"


def is_eligible_thread_candidate(item: EvidenceItem) -> bool:
    """Checks whether an evidence candidate represents a discussion thread eligible for expansion.

    Invariants:
    - THREAD_EXISTENCE_INFERRED_FROM_ROOT_PERMALINK_ONLY = false.
    - Expansion eligible only if validated thread_ts exists OR qualified search metadata proves thread existence.
    """
    # 1. Validated thread_identifier exists
    if item.thread_identifier and normalize_slack_ts(item.thread_identifier):
        return True

    # 2. Check permalink parser for thread_ts
    permalink = (
        getattr(item, "permalink", None)
        or getattr(item, "result_url", None)
        or getattr(item, "message_identifier", None)
    )
    if permalink:
        identity = parse_slack_message_permalink(permalink)
        if identity and identity.thread_ts and normalize_slack_ts(identity.thread_ts):
            role = classify_message_thread_role(identity.message_ts, identity.thread_ts)
            if role == "THREAD_REPLY":
                # Validated reply inside thread: proves thread existence
                return True
            if role == "THREAD_ROOT":
                # Invariant 7: Root permalink only is insufficient to infer replies exist
                # Must be verified by qualified search metadata (e.g. reply count > 0 / has_thread)
                if getattr(item, "has_thread", False) is True and getattr(item, "thread_reply_count", 1) > 0:
                    return True
                return False

    # 3. Qualified Search metadata explicitly proves thread existence (has_thread == True and reply count > 0)
    if getattr(item, "has_thread", False) is True and getattr(item, "thread_reply_count", 1) > 0:
        return True

    return False


class WebviewThreadReader:
    """Bounded, read-only Slack thread context reader backed by WebViewHost.

    Implements native thread opening via qualified reply_bar click and click-correlated
    flexpane acquisition without URL navigation or synthetic route construction.
    """

    def __init__(
        self,
        host: Any,
        max_replies: int = MAX_ACCEPTED_REPLIES,
        max_context_chars: int = MAX_FINAL_THREAD_CHARS,
    ) -> None:
        self.host = host
        self.max_replies = min(max_replies, MAX_ACCEPTED_REPLIES)
        self.max_context_chars = min(max_context_chars, MAX_FINAL_THREAD_CHARS)
        self.last_telemetry: Optional[dict[str, Any]] = None
        self.telemetry_history: list[dict[str, Any]] = []

    def _record_telemetry(self, data: dict[str, Any]) -> None:
        """Records telemetry entry and appends to telemetry history."""
        self.last_telemetry = data
        self.telemetry_history.append(dict(data))

    @property
    def last_navigation_telemetry(self) -> Optional[dict[str, Any]]:
        """Backward-compatibility telemetry alias."""
        return self.last_telemetry

    def _cleanup_thread_op_state(self) -> None:
        """Sanitizes namespaced DOM operation state on host."""
        if hasattr(self.host, "evaluate_js"):
            try:
                self.host.evaluate_js(JS_CLEANUP_THREAD_OP_STATE, timeout=2.0)
            except Exception as ex:
                logger.debug(f"Operation state cleanup warning: {ex}")

    def read_thread_result(
        self,
        candidate_item: EvidenceItem,
        timeout: float = 20.0,
    ) -> ThreadReadResult:
        """Reads mounted replies for an eligible thread candidate via native reply_bar click, returning typed ThreadReadResult.

        Invariants:
        - THREAD_READER_URL_NAVIGATION_FALLBACK = false
        - THREAD_AUTOMATED_SCROLL = false (reads mounted replies only)
        - UNQUALIFIED_COMPLETENESS_PATH_DISABLED = true (returns PARTIAL upon success)
        - FAIL CLOSED on structural ambiguity -> UNAVAILABLE
        - Rejects wrong channel replies, wrong thread replies, invalid permalinks.
        - ZERO composer interactions.
        - Returns immutable tuple of ThreadReplyItem and typed ThreadReadFailureReason.
        """
        try:
            # 1. State Pre-Sanitation
            self._cleanup_thread_op_state()

            # 2. Eligibility verification
            if not is_eligible_thread_candidate(candidate_item):
                logger.info("Candidate is not an eligible thread candidate (root permalink alone is insufficient).")
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.NOT_ATTEMPTED,
                    failure_reason=ThreadReadFailureReason.ELIGIBILITY_FAILED,
                )

            # 3. Parse candidate permalink identity
            permalink = (
                getattr(candidate_item, "permalink", None)
                or getattr(candidate_item, "result_url", None)
                or getattr(candidate_item, "message_identifier", None)
            )
            if not permalink:
                logger.warning("Candidate lacks permalink for thread context expansion.")
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.IDENTITY_PARSE_FAILED,
                )

            identity = parse_slack_message_permalink(permalink)
            if not identity or not identity.channel_id:
                logger.warning(f"Failed to parse canonical permalink identity from '{permalink}'.")
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.IDENTITY_PARSE_FAILED,
                )

            target_channel_id = identity.channel_id
            target_message_ts = identity.message_ts
            target_root_ts = identity.thread_ts or identity.message_ts
            norm_target_root_ts = normalize_slack_ts(target_root_ts)
            norm_target_message_ts = normalize_slack_ts(target_message_ts)

            if not norm_target_root_ts:
                logger.warning("Target root thread timestamp cannot be normalized.")
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.IDENTITY_PARSE_FAILED,
                )

            # 4. Policy A (Post-Search DOM Residency) Verification
            js_inspect = (
                f"({JS_INSPECT_RESIDENT_SEARCH_ROWS})("
                f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
                f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)})"
            )
            res_inspect = self.host.evaluate_js(js_inspect, timeout=5.0)

            if not isinstance(res_inspect, dict) or not res_inspect.get("ok"):
                logger.warning(f"Resident search row inspection failed closed: {res_inspect}")
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
                )

            resident_rows = res_inspect.get("rows", [])
            matching_row_indices: list[int] = []

            for row_meta in resident_rows:
                idx = row_meta.get("row_index")
                permalinks = row_meta.get("permalinks", [])
                matched_in_row = False

                for plink in permalinks:
                    parsed_link = parse_slack_message_permalink(plink)
                    if not parsed_link:
                        continue
                    if parsed_link.channel_id != target_channel_id:
                        continue

                    link_msg_norm = normalize_slack_ts(parsed_link.message_ts)
                    link_thr_norm = normalize_slack_ts(parsed_link.thread_ts) if parsed_link.thread_ts else None

                    # Verify message identity and thread affiliation
                    msg_match = (link_msg_norm == norm_target_message_ts)
                    thr_match = (
                        link_thr_norm == norm_target_root_ts
                        or (link_thr_norm is None and norm_target_root_ts == norm_target_message_ts)
                    )

                    if msg_match and thr_match:
                        matched_in_row = True
                        break

                if matched_in_row and isinstance(idx, int):
                    matching_row_indices.append(idx)

            # Policy A Cardinality Decision:
            # 0 matching rows -> TARGET_ROW_NOT_FOUND -> NOT_ATTEMPTED (0 clicks, parent preserved)
            # >1 matching rows -> ROW_IDENTITY_AMBIGUOUS -> UNAVAILABLE (0 clicks)
            if len(matching_row_indices) == 0:
                logger.info("Target candidate is not resident in current mounted Search DOM rows.")
                self._record_telemetry({
                    "residency_matched": False,
                    "resident_matches": 0,
                    "click_executed": False,
                })
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.NOT_ATTEMPTED,
                    failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
                    resident_match_count=0,
                )

            if len(matching_row_indices) > 1:
                logger.warning(f"Ambiguous resident rows ({len(matching_row_indices)}) matching candidate identity.")
                self._record_telemetry({
                    "residency_matched": False,
                    "resident_matches": len(matching_row_indices),
                    "click_executed": False,
                })
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.ROW_IDENTITY_AMBIGUOUS,
                    resident_match_count=len(matching_row_indices),
                )

            selected_row_index = matching_row_indices[0]

            # 5. Bounded reply_bar Click on Retained Element Reference
            js_click = f"({JS_CLICK_RESIDENT_ROW_REPLY_BAR})({selected_row_index})"
            res_click = self.host.evaluate_js(js_click, timeout=5.0)

            if not isinstance(res_click, dict) or not res_click.get("click_executed"):
                err = res_click.get("guard_failure_reason") or res_click.get("error", "CLICK_FAILED")
                logger.warning(f"reply_bar click failed closed: {err}")
                self._record_telemetry({
                    "residency_matched": True,
                    "resident_matches": 1,
                    "click_executed": False,
                    "click_failure_reason": err,
                })
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.REPLY_BAR_UNAVAILABLE,
                    resident_match_count=1,
                )

            # 6. State-Based Root Delta Polling
            poll_start = time.monotonic()
            poll_deadline = poll_start + min(timeout, ROOT_DELTA_TIMEOUT)
            correlated_acquired = False
            last_poll: dict[str, Any] = {}

            while time.monotonic() < poll_deadline:
                poll_res = self.host.evaluate_js(JS_POLL_ROOT_DELTA, timeout=3.0)
                if isinstance(poll_res, dict):
                    last_poll = poll_res
                    if poll_res.get("ready"):
                        correlated_acquired = True
                        break
                time.sleep(ROOT_DELTA_POLL_INTERVAL)

            root_delta_resolution_ms = int((time.monotonic() - poll_start) * 1000)

            if not correlated_acquired or last_poll.get("new_root_count") != 1:
                logger.warning(f"Click-correlated thread root acquisition failed closed: {last_poll}")
                self._record_telemetry({
                    "residency_matched": True,
                    "resident_matches": 1,
                    "click_executed": True,
                    "correlated_root_acquired": False,
                    "poll_result": last_poll,
                })
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=ThreadReadFailureReason.CLICK_CORRELATED_ROOT_FAILED,
                    resident_match_count=1,
                )

            # 7. Extract Mounted Thread Replies (Two-Tier Bounds)
            js_extract = (
                f"({JS_EXTRACT_MOUNTED_THREAD_REPLIES})("
                f"{MAX_ROW_CANDIDATES}, "
                f"{MAX_BODY_CHARS_PER_ROW}, "
                f"{self.max_context_chars}, "
                f"{self.max_replies})"
            )
            res_extract = self.host.evaluate_js(js_extract, timeout=5.0)

            if not isinstance(res_extract, dict) or not res_extract.get("ok"):
                err = res_extract.get("error", "EXTRACTION_FAILED") if isinstance(res_extract, dict) else "INVALID_PAYLOAD"
                logger.warning(f"Mounted thread reply extraction failed closed: {err}")
                self._record_telemetry({
                    "residency_matched": True,
                    "resident_matches": 1,
                    "click_executed": True,
                    "correlated_root_acquired": True,
                    "extraction_ok": False,
                    "error": err,
                })
                reason = (
                    ThreadReadFailureReason.MESSAGE_REGION_UNAVAILABLE
                    if (isinstance(res_extract, dict) and "MESSAGE_REGION" in str(err))
                    else ThreadReadFailureReason.EXTRACTION_FAILED
                )
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.UNAVAILABLE,
                    failure_reason=reason,
                    resident_match_count=1,
                )

            raw_replies = res_extract.get("replies", [])

            # 8. Python Canonical Validation & Strict Thread Identity Rule
            validated_replies: list[ThreadReplyItem] = []
            cumulative_chars = 0
            root_rows_excluded = 0
            identity_mismatch_excluded = 0

            for raw_rep in raw_replies:
                href = raw_rep.get("href")
                if not href:
                    continue

                msg_ident = parse_slack_message_permalink(href)
                if not msg_ident or not msg_ident.message_ts:
                    identity_mismatch_excluded += 1
                    continue

                if msg_ident.channel_id != target_channel_id:
                    identity_mismatch_excluded += 1
                    continue

                msg_ts_norm = normalize_slack_ts(msg_ident.message_ts)
                thr_ts_norm = normalize_slack_ts(msg_ident.thread_ts) if msg_ident.thread_ts else None

                # Strict Identity Rule:
                # ROOT: message_ts == target_root_ts AND (thread_ts is None OR thread_ts == target_root_ts)
                if msg_ts_norm == norm_target_root_ts and (thr_ts_norm is None or thr_ts_norm == norm_target_root_ts):
                    root_rows_excluded += 1
                    continue

                # REPLY: message_ts != target_root_ts AND thread_ts is not None AND thread_ts == target_root_ts
                # Missing thread_ts or wrong thread_ts is strictly rejected
                if msg_ts_norm == norm_target_root_ts or thr_ts_norm is None or thr_ts_norm != norm_target_root_ts:
                    identity_mismatch_excluded += 1
                    continue

                body_text = (raw_rep.get("text") or "").strip()
                if not body_text:
                    continue

                reply_idx = len(validated_replies) + 1
                reply_id = f"R{reply_idx}"

                # Enforce Python-side character budget
                remaining_chars = self.max_context_chars - cumulative_chars
                if remaining_chars <= 0:
                    break
                if len(body_text) > remaining_chars:
                    trunc_len = max(0, remaining_chars - 3)
                    body_text = body_text[:trunc_len].rstrip() + "..."

                reply_item = ThreadReplyItem(
                    reply_id=reply_id,
                    author=raw_rep.get("author"),
                    timestamp=None,
                    text=body_text,
                    permalink=href,
                    channel_id=target_channel_id,
                    message_ts=msg_ident.message_ts,
                    thread_ts=target_root_ts,
                )
                validated_replies.append(reply_item)
                cumulative_chars += len(body_text)

                if len(validated_replies) >= self.max_replies:
                    break

            # 9. Conservative Completeness Decision
            # UNQUALIFIED_COMPLETENESS_PATH_DISABLED = true
            # In current Web Slack, no live-qualified completeness proof exists + automated scroll = 0
            if not validated_replies:
                status = ThreadContextStatus.UNAVAILABLE
                failure_reason = ThreadReadFailureReason.EXTRACTION_FAILED
            else:
                status = ThreadContextStatus.PARTIAL
                failure_reason = None

            self._record_telemetry({
                "candidate_permalink": permalink,
                "candidate_identifier": getattr(candidate_item, "message_identifier", None),
                "candidate_snippet": (getattr(candidate_item, "text", "") or "")[:120],
                "residency_matched": True,
                "resident_matches": len(matching_row_indices),
                "click_executed": True,
                "pre_click_root_count": res_click.get("pre_click_root_count", 0),
                "new_root_count": last_poll.get("new_root_count", 1),
                "correlated_root_acquired": True,
                "root_delta_resolution_ms": root_delta_resolution_ms,
                "message_region_qualified": True,
                "virtual_items_total": res_extract.get("virtual_items_total", len(raw_replies)),
                "composer_rows_excluded": res_extract.get("composer_rows_excluded", 0),
                "message_row_candidates": res_extract.get("virtual_items_total", 0) - res_extract.get("composer_rows_excluded", 0),
                "rows_skipped_no_timestamp": res_extract.get("rows_skipped_no_timestamp", 0),
                "rows_skipped_ambiguous_timestamp": res_extract.get("rows_skipped_ambiguous_timestamp", 0),
                "rows_skipped_identity_mismatch": identity_mismatch_excluded,
                "root_rows_excluded": root_rows_excluded,
                "actual_reply_rows_accepted": len(validated_replies),
                "accepted_reply_count": len(validated_replies),
                "accepted_reply_total_characters": cumulative_chars,
                "multi_block_reply_observed": bool(res_extract.get("multi_block_observed", False)),
                "extraction_ok": True,
                "validated_replies_count": len(validated_replies),
                "status": status.value,
            })
            logger.info(
                f"🧵 [THREAD_EXPANSION_COMPLETE] channel={target_channel_id} replies={len(validated_replies)} "
                f"status={status.value}"
            )
            return ThreadReadResult(
                replies=tuple(validated_replies),
                status=status,
                failure_reason=failure_reason,
                resident_match_count=1,
            )

        finally:
            # Guaranteed cleanup on all terminal paths
            self._cleanup_thread_op_state()

    def read_thread_context(
        self,
        candidate_item: EvidenceItem,
        timeout: float = 20.0,
    ) -> tuple[list[ThreadReplyItem], ThreadContextStatus]:
        """Backward-compatibility wrapper returning mutable list and status."""
        result = self.read_thread_result(candidate_item, timeout=timeout)
        return list(result.replies), result.status


__all__ = [
    "WebviewThreadReader",
    "ThreadReadResult",
    "ThreadReadFailureReason",
    "is_eligible_thread_candidate",
    "classify_message_thread_role",
    "normalize_slack_ts",
]
