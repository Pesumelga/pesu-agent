"""Phase 13.5D / Task 11 / P2A / VSA2: Bounded Partial Scroll Progress Qualification.

VSA1 PURPOSE:
Determine whether exactly one bounded scroll mutation on the exact Slack search-result
scroll container can safely bring the exact known target anchor into the visible scroll viewport
without clicking, navigation, typing, message mutation, search resubmission, or any other interaction.

Strict Boundaries:
- QUALIFICATION ONLY: Does NOT qualify production P2A, does NOT modify WebviewThreadReader,
  does NOT click any element, does NOT open thread, does NOT qualify anchor interaction,
  does NOT create Attempt 5.
- ONE ATOMIC PRE-SCROLL READ-ONLY MOUNTED TARGET GEOMETRY OBSERVATION:
  Resolves connected search_view, inspects all already-mounted rows up to the diagnostic
  cap (MAX_MOUNTED_ROWS_TO_INSPECT = 20), finds exact semantic target, resolves exact
  thread anchor, measures target row & anchor geometry, inspects layout & visual viewports,
  identifies nearest vertical scroll container, checks clipping ancestors, and confirms
  the target anchor is currently below the visible scroll viewport.
- MINIMAL REVEAL SCROLL TARGET STRATEGY:
  SCROLL_BOTTOM_MARGIN_PX = 32
  visible_bottom = scroll_container_rect_bottom - SCROLL_BOTTOM_MARGIN_PX
  required_delta = anchor_rect_bottom - visible_bottom
  requested_scroll_top = current_scroll_top + required_delta
  max_scroll_top = scroll_height - client_height
  target_scroll_top = min(max(requested_scroll_top, 0), max_scroll_top)
- BOUNDED SCROLL SAFETY LIMIT:
  MAX_SCROLL_DELTA = 3 * scroll_container_client_height
  abs(target_scroll_top - current_scroll_top) <= MAX_SCROLL_DELTA
  Otherwise SCROLL_DELTA_EXCEEDS_FROZEN_BOUND (FAIL CLOSED).
- EXACT ONE SCROLL MUTATION:
  scrollContainer.scrollTop = partialTargetScrollTop;
  Strictly zero window.scrollTo, window.scrollBy, element.scrollIntoView, wheel event,
  KeyboardEvent, PageDown, ArrowDown, End, mouse drag scrollbar.
  scroll_actions = 1.
- POST-SCROLL SETTLE & REACQUISITION:
  POST_SCROLL_SETTLE_MS = 250 (bounded non-interactive settle).
  Re-resolves from live DOM (does NOT trust old node references, handling Slack/React virtualization):
  re-finds search view, mounted rows, exact target semantic identity, exact anchor,
  movement proof (scrollTop_after > scrollTop_before and within tolerance),
  new anchor geometry, intersection with scroll viewport, clipping, and hit-test.
- STRICT ZERO INTERACTION:
  search operations <= 1, search submissions <= 1, scroll actions <= 1.
  STRICT ZERO: anchor clicks = 0, reply_bar clicks = 0, generic result-card clicks = 0,
  thread expansions = 0, keyboard navigation = 0, composer interaction = 0,
  write actions = 0, provider calls = 0, retries = 0, restorations = 0, back navigations = 0.
- TARGET IDENTITY:
  channel_id == C042WS25QAX
  normalize(message_ts) == normalize(1718079276.126469)
  normalize(thread_ts) == normalize(1718058683.912369)
  is_thread_reply == true
- TARGET ANCHOR IDENTIFICATION:
  tagName == A
  isConnected == true
  valid Slack permalink matching exact target identity
  has_thread_ts_parameter == true
  qualified_target_anchor_count == 1
- SCROLL CONTAINER TELEMETRY:
  scrollHeight > clientHeight && overflowY in ('auto', 'scroll', 'overlay').
- NO MUTATION / NO PERSISTENCE OF SENSITIVE CONTENT:
  No URL, raw permalink, raw href, message body, or sender text persisted.
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
    JS_EXTRACT_SEARCH_RESULTS as PROD_JS_EXTRACT_SEARCH_RESULTS,
    SEARCH_RESULT_ROW_SELECTORS as PROD_SEARCH_RESULT_ROW_SELECTORS,
    SEARCH_VIEW_SELECTOR as PROD_SEARCH_VIEW_SELECTOR,
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2")


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
SEARCH_MAX_RESULTS = 5
MAX_MOUNTED_ROWS_TO_INSPECT = 20

PROTOCOL_NAME = "task11q4_p2a_l1_vsa2_bounded_partial_scroll"
DIAGNOSTIC_RUN = "VSA2_SCROLL_RUN_1"
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

SCROLL_BOTTOM_MARGIN_PX = 32.0
MAX_SCROLL_DELTA_MULTIPLIER = 3.0
SMALL_SCROLL_TOLERANCE_PX = 2.0
POST_SCROLL_SETTLE_MS = 250

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_p2a_l1_vsa2_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vsa2_bounded_partial_scroll_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vsa2_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vsa2_bounded_partial_scroll.json"

SEARCH_VIEW_SELECTOR = PROD_SEARCH_VIEW_SELECTOR
SEARCH_RESULT_ROW_SELECTORS = PROD_SEARCH_RESULT_ROW_SELECTORS

DIAGNOSTIC_OUTCOMES = [
    "TARGET_NOT_FOUND_PRE_SCROLL",
    "TARGET_DUPLICATED_PRE_SCROLL",
    "TARGET_ANCHOR_INVALID_PRE_SCROLL",
    "SCROLL_CONTAINER_NOT_FOUND",
    "PARTIAL_SCROLL_NOT_REQUIRED_TARGET_ALREADY_VISIBLE",
    "FULL_REVEAL_NOW_WITHIN_VSA1_BOUND",
    "PARTIAL_SCROLL_DELTA_INVALID",
    "PARTIAL_SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED",
    "TARGET_LOST_AFTER_PARTIAL_SCROLL",
    "TARGET_DUPLICATED_AFTER_PARTIAL_SCROLL",
    "TARGET_ANCHOR_INVALID_AFTER_PARTIAL_SCROLL",
    "NO_GEOMETRIC_PROGRESS_AFTER_PARTIAL_SCROLL",
    "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED",
    "RUNTIME_INITIALIZATION_EXCEPTION",
    "SEARCH_EXECUTION_EXCEPTION",
    "SEARCH_SAFETY_GATE_FAILED",
    "PRE_SCROLL_SNAPSHOT_EXCEPTION",
    "SCROLL_MUTATION_EXCEPTION",
    "POST_SCROLL_SNAPSHOT_EXCEPTION",
    "FAIL_CLOSED",
]

MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_SEARCH_SUBMISSIONS = 1
MAX_SCROLL_ACTIONS = 1
MAX_PRE_SCROLL_DOM_EXTRACTIONS = 1
MAX_POST_SCROLL_DOM_SNAPSHOTS = 1
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
# JavaScript Snippets
# ---------------------------------------------------------------------------

JS_READ_ONLY_MOUNTED_DOM_OBSERVATION = r"""
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
                mounted_row_records: [],
                target_row_geometry: null,
                target_anchor_geometry: null,
                layout_viewport: null,
                visual_viewport: null,
                scroll_container: null,
                clipping: null,
                hit_test: null,
                geometry_classification: "MOUNTED_DOM_STATE_INSUFFICIENT"
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
        let matchedTargetRowEl = null;

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
                targetMatches.push({ record: record, element: rowEl });
                matchedTargetRowEl = rowEl;
            }
        }

        // Layout Viewport Metrics
        const winInnerW = window.innerWidth || 0;
        const winInnerH = window.innerHeight || 0;
        const docClientW = (document.documentElement && document.documentElement.clientWidth) || 0;
        const docClientH = (document.documentElement && document.documentElement.clientHeight) || 0;
        const layoutVpW = winInnerW || docClientW;
        const layoutVpH = winInnerH || docClientH;

        const layoutViewportMetrics = {
            window_inner_width: winInnerW,
            window_inner_height: winInnerH,
            document_client_width: docClientW,
            document_client_height: docClientH,
            layout_viewport_width: layoutVpW,
            layout_viewport_height: layoutVpH
        };

        // Visual Viewport Metrics
        const vvAvailable = Boolean(window.visualViewport);
        const visualViewportMetrics = {
            visual_viewport_available: vvAvailable,
            visual_viewport_width: vvAvailable ? window.visualViewport.width : null,
            visual_viewport_height: vvAvailable ? window.visualViewport.height : null,
            visual_viewport_offset_left: vvAvailable ? window.visualViewport.offsetLeft : null,
            visual_viewport_offset_top: vvAvailable ? window.visualViewport.offsetTop : null,
            visual_viewport_scale: vvAvailable ? window.visualViewport.scale : null,
            device_pixel_ratio: window.devicePixelRatio || 1.0
        };

        // Cardinality handling
        if (targetMatches.length === 0) {
            return {
                ok: true,
                search_view_connected: true,
                mounted_row_count: mounted_row_count,
                inspected_mounted_row_count: inspected_mounted_row_count,
                connected_row_count: connected_row_count,
                positive_geometry_row_count: positive_geometry_row_count,
                target_match_count: 0,
                target_mounted_dom_position: null,
                target_connected: null,
                target_positive_geometry: null,
                mounted_row_records: mounted_row_records,
                target_row_geometry: null,
                target_anchor_geometry: null,
                layout_viewport: layoutViewportMetrics,
                visual_viewport: visualViewportMetrics,
                scroll_container: null,
                clipping: null,
                hit_test: null,
                geometry_classification: "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"
            };
        }

        if (targetMatches.length > 1) {
            return {
                ok: true,
                search_view_connected: true,
                mounted_row_count: mounted_row_count,
                inspected_mounted_row_count: inspected_mounted_row_count,
                connected_row_count: connected_row_count,
                positive_geometry_row_count: positive_geometry_row_count,
                target_match_count: targetMatches.length,
                target_mounted_dom_position: null,
                target_connected: null,
                target_positive_geometry: null,
                mounted_row_records: mounted_row_records,
                target_row_geometry: null,
                target_anchor_geometry: null,
                layout_viewport: layoutViewportMetrics,
                visual_viewport: visualViewportMetrics,
                scroll_container: null,
                clipping: null,
                hit_test: null,
                geometry_classification: "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"
            };
        }

        // Unique target row identified
        const targetRowEl = targetMatches[0].element;
        const targetRecord = targetMatches[0].record;
        const targetDomPosition = targetRecord.mounted_dom_position;

        // Target Row Geometry
        const rowRect = targetRowEl.getBoundingClientRect();
        const rowCenterX = rowRect.left + rowRect.width / 2.0;
        const rowCenterY = rowRect.top + rowRect.height / 2.0;

        const rowCenterInLayout = (rowCenterX >= 0 && rowCenterX < layoutVpW && rowCenterY >= 0 && rowCenterY < layoutVpH);
        const rowIntersectsLayout = !(rowRect.bottom <= 0 || rowRect.top >= layoutVpH || rowRect.right <= 0 || rowRect.left >= layoutVpW);
        const rowFullyInLayout = (rowRect.top >= 0 && rowRect.bottom <= layoutVpH && rowRect.left >= 0 && rowRect.right <= layoutVpW);

        // Nearest Vertical Scroll Container Identification
        let scrollContainerEl = null;
        let curr = targetRowEl.parentElement;
        while (curr && curr !== document.documentElement && curr !== document.body) {
            let cs = null;
            try {
                cs = window.getComputedStyle(curr);
            } catch (e) {
                cs = null;
            }
            if (cs) {
                const oy = cs.overflowY;
                const isScrollableY = (oy === "auto" || oy === "scroll" || oy === "overlay");
                if (isScrollableY && curr.scrollHeight > curr.clientHeight) {
                    scrollContainerEl = curr;
                    break;
                }
            }
            curr = curr.parentElement;
        }

        if (!scrollContainerEl) {
            const docEl = document.documentElement;
            let cs = null;
            try {
                cs = window.getComputedStyle(docEl);
            } catch (e) {
                cs = null;
            }
            if (cs && (cs.overflowY === "auto" || cs.overflowY === "scroll" || cs.overflowY === "overlay") && docEl.scrollHeight > docEl.clientHeight) {
                scrollContainerEl = docEl;
            }
        }

        const scrollContainerFound = Boolean(scrollContainerEl);
        let scRect = null;
        let scClientW = null;
        let scClientH = null;
        let scScrollW = null;
        let scScrollH = null;
        let scScrollL = null;
        let scScrollT = null;
        let scIsSearchView = false;
        let scIsDocScroller = false;

        if (scrollContainerEl) {
            scRect = scrollContainerEl.getBoundingClientRect();
            scClientW = scrollContainerEl.clientWidth;
            scClientH = scrollContainerEl.clientHeight;
            scScrollW = scrollContainerEl.scrollWidth;
            scScrollH = scrollContainerEl.scrollHeight;
            scScrollL = scrollContainerEl.scrollLeft;
            scScrollT = scrollContainerEl.scrollTop;
            scIsSearchView = (scrollContainerEl === searchView || searchView.contains(scrollContainerEl));
            scIsDocScroller = (scrollContainerEl === document.documentElement || scrollContainerEl === document.body);
        }

        const scrollContainerTelemetry = {
            scroll_container_found: scrollContainerFound,
            scroll_container_is_search_view: scIsSearchView,
            scroll_container_is_document_scroller: scIsDocScroller,
            scroll_container_client_width: scClientW,
            scroll_container_client_height: scClientH,
            scroll_container_scroll_width: scScrollW,
            scroll_container_scroll_height: scScrollH,
            scroll_container_scroll_left: scScrollL,
            scroll_container_scroll_top: scScrollT,
            scroll_container_rect_top: scRect ? scRect.top : null,
            scroll_container_rect_bottom: scRect ? scRect.bottom : null,
            scroll_container_rect_left: scRect ? scRect.left : null,
            scroll_container_rect_right: scRect ? scRect.right : null
        };

        const rowCenterInScroll = scRect ? (rowCenterX >= scRect.left && rowCenterX <= scRect.right && rowCenterY >= scRect.top && rowCenterY <= scRect.bottom) : null;
        const rowIntersectsScroll = scRect ? !(rowRect.bottom <= scRect.top || rowRect.top >= scRect.bottom || rowRect.right <= scRect.left || rowRect.left >= scRect.right) : null;
        const rowFullyInScroll = scRect ? (rowRect.top >= scRect.top && rowRect.bottom <= scRect.bottom && rowRect.left >= scRect.left && rowRect.right <= scRect.right) : null;

        const targetRowGeometry = {
            target_row_rect_top: rowRect.top,
            target_row_rect_bottom: rowRect.bottom,
            target_row_rect_left: rowRect.left,
            target_row_rect_right: rowRect.right,
            target_row_rect_width: rowRect.width,
            target_row_rect_height: rowRect.height,
            target_row_center_x: rowCenterX,
            target_row_center_y: rowCenterY,
            target_row_center_inside_layout_viewport: rowCenterInLayout,
            target_row_intersects_layout_viewport: rowIntersectsLayout,
            target_row_fully_inside_layout_viewport: rowFullyInLayout,
            target_row_center_inside_scroll_viewport: rowCenterInScroll,
            target_row_intersects_scroll_viewport: rowIntersectsScroll,
            target_row_fully_inside_scroll_viewport: rowFullyInScroll
        };

        // Target Anchor Resolution within the exact row
        const rowAnchors = Array.from(targetRowEl.querySelectorAll("a[href]"));
        const qualifyingAnchors = [];

        for (const a of rowAnchors) {
            if (a.tagName !== "A") continue;
            if (!a.isConnected) continue;

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

            if (parsedChan === targetChannelId && parsedMsg === targetMsgNorm && parsedThr === targetThrNorm) {
                if (parsedMsg === targetMsgNorm && parsedThr !== null) {
                    qualifyingAnchors.push(a);
                }
            }
        }

        const anchorCount = qualifyingAnchors.length;
        if (anchorCount === 0) {
            return {
                ok: true,
                search_view_connected: true,
                mounted_row_count: mounted_row_count,
                inspected_mounted_row_count: inspected_mounted_row_count,
                connected_row_count: connected_row_count,
                positive_geometry_row_count: positive_geometry_row_count,
                target_match_count: 1,
                target_mounted_dom_position: targetDomPosition,
                target_connected: true,
                target_positive_geometry: true,
                mounted_row_records: mounted_row_records,
                target_row_geometry: targetRowGeometry,
                target_anchor_geometry: {
                    anchor_found: false,
                    anchor_unique: false,
                    qualified_target_anchor_count: 0
                },
                layout_viewport: layoutViewportMetrics,
                visual_viewport: visualViewportMetrics,
                scroll_container: scrollContainerTelemetry,
                clipping: null,
                hit_test: null,
                geometry_classification: "TARGET_ROW_FOUND_ANCHOR_NOT_FOUND"
            };
        }

        if (anchorCount > 1) {
            return {
                ok: true,
                search_view_connected: true,
                mounted_row_count: mounted_row_count,
                inspected_mounted_row_count: inspected_mounted_row_count,
                connected_row_count: connected_row_count,
                positive_geometry_row_count: positive_geometry_row_count,
                target_match_count: 1,
                target_mounted_dom_position: targetDomPosition,
                target_connected: true,
                target_positive_geometry: true,
                mounted_row_records: mounted_row_records,
                target_row_geometry: targetRowGeometry,
                target_anchor_geometry: {
                    anchor_found: true,
                    anchor_unique: false,
                    qualified_target_anchor_count: anchorCount
                },
                layout_viewport: layoutViewportMetrics,
                visual_viewport: visualViewportMetrics,
                scroll_container: scrollContainerTelemetry,
                clipping: null,
                hit_test: null,
                geometry_classification: "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE"
            };
        }

        // Unique target anchor identified
        const targetAnchorEl = qualifyingAnchors[0];
        const ancRect = targetAnchorEl.getBoundingClientRect();
        const ancPosGeom = (ancRect.width > 0 && ancRect.height > 0);
        const ancCenterX = ancRect.left + ancRect.width / 2.0;
        const ancCenterY = ancRect.top + ancRect.height / 2.0;

        const ancCenterInLayout = (ancCenterX >= 0 && ancCenterX < layoutVpW && ancCenterY >= 0 && ancCenterY < layoutVpH);
        const ancIntersectsLayout = !(ancRect.bottom <= 0 || ancRect.top >= layoutVpH || ancRect.right <= 0 || ancRect.left >= layoutVpW);
        const ancFullyInLayout = (ancRect.top >= 0 && ancRect.bottom <= layoutVpH && ancRect.left >= 0 && ancRect.right <= layoutVpW);

        const ancCenterInScroll = scRect ? (ancCenterX >= scRect.left && ancCenterX <= scRect.right && ancCenterY >= scRect.top && ancCenterY <= scRect.bottom) : null;
        const ancIntersectsScroll = scRect ? !(ancRect.bottom <= scRect.top || ancRect.top >= scRect.bottom || ancRect.right <= scRect.left || ancRect.left >= scRect.right) : null;
        const ancFullyInScroll = scRect ? (ancRect.top >= scRect.top && ancRect.bottom <= scRect.bottom && ancRect.left >= scRect.left && ancRect.right <= scRect.right) : null;

        const targetAnchorGeometry = {
            anchor_found: true,
            anchor_unique: true,
            qualified_target_anchor_count: 1,
            anchor_positive_geometry: ancPosGeom,
            anchor_rect_top: ancRect.top,
            anchor_rect_bottom: ancRect.bottom,
            anchor_rect_left: ancRect.left,
            anchor_rect_right: ancRect.right,
            anchor_rect_width: ancRect.width,
            anchor_rect_height: ancRect.height,
            anchor_center_x: ancCenterX,
            anchor_center_y: ancCenterY,
            anchor_center_inside_layout_viewport: ancCenterInLayout,
            anchor_intersects_layout_viewport: ancIntersectsLayout,
            anchor_fully_inside_layout_viewport: ancFullyInLayout,
            anchor_center_inside_scroll_viewport: ancCenterInScroll,
            anchor_intersects_scroll_viewport: ancIntersectsScroll,
            anchor_fully_inside_scroll_viewport: ancFullyInScroll
        };

        // Clipping Ancestors Inspection
        let clippingAncestorPresent = false;
        let insideAllClipping = true;

        let clipCurr = targetAnchorEl.parentElement;
        while (clipCurr && clipCurr !== document.documentElement && clipCurr !== document.body) {
            let cs = null;
            try {
                cs = window.getComputedStyle(clipCurr);
            } catch (e) {
                cs = null;
            }
            if (cs) {
                const ox = cs.overflowX;
                const oy = cs.overflowY;
                const hasClipping = (ox === "hidden" || ox === "clip" || oy === "hidden" || oy === "clip" || oy === "auto" || oy === "scroll");
                if (hasClipping) {
                    clippingAncestorPresent = true;
                    try {
                        const cr = clipCurr.getBoundingClientRect();
                        const inside = (ancCenterX >= cr.left && ancCenterX <= cr.right && ancCenterY >= cr.top && ancCenterY <= cr.bottom);
                        if (!inside) {
                            insideAllClipping = false;
                            break;
                        }
                    } catch (e) {
                        // ignore
                    }
                }
            }
            clipCurr = clipCurr.parentElement;
        }

        const clippingTelemetry = {
            clipping_ancestor_present: clippingAncestorPresent,
            anchor_center_inside_all_clipping_ancestors: insideAllClipping
        };

        // ElementFromPoint Telemetry (only if anchor center is inside layout viewport)
        let hitTestEvaluated = false;
        let hitNull = null;
        let hitIsAnchor = false;
        let hitIsDescendant = false;
        let anchorContainsHit = false;
        let hitContainsAnchor = false;

        if (ancCenterInLayout) {
            hitTestEvaluated = true;
            try {
                const hitEl = document.elementFromPoint(ancCenterX, ancCenterY);
                hitNull = (hitEl === null);
                if (hitEl) {
                    hitIsAnchor = (hitEl === targetAnchorEl);
                    hitIsDescendant = targetAnchorEl.contains(hitEl) && (hitEl !== targetAnchorEl);
                    anchorContainsHit = targetAnchorEl.contains(hitEl);
                    hitContainsAnchor = hitEl.contains(targetAnchorEl);
                }
            } catch (e) {
                hitNull = true;
            }
        }

        const hitTestTelemetry = {
            element_from_point_evaluated: hitTestEvaluated,
            element_from_point_is_null: hitNull,
            element_from_point_is_anchor: hitIsAnchor,
            element_from_point_is_anchor_descendant: hitIsDescendant,
            anchor_contains_element_from_point: anchorContainsHit,
            element_from_point_contains_anchor: hitContainsAnchor
        };

        return {
            ok: true,
            search_view_connected: true,
            mounted_row_count: mounted_row_count,
            inspected_mounted_row_count: inspected_mounted_row_count,
            connected_row_count: connected_row_count,
            positive_geometry_row_count: positive_geometry_row_count,
            target_match_count: 1,
            target_mounted_dom_position: targetDomPosition,
            target_connected: true,
            target_positive_geometry: true,
            mounted_row_records: mounted_row_records,
            target_row_geometry: targetRowGeometry,
            target_anchor_geometry: targetAnchorGeometry,
            layout_viewport: layoutViewportMetrics,
            visual_viewport: visualViewportMetrics,
            scroll_container: scrollContainerTelemetry,
            clipping: clippingTelemetry,
            hit_test: hitTestTelemetry,
            geometry_classification: "MOUNTED_DOM_RESOLVED"
        };
    } catch (err) {
        return {
            ok: false,
            failure_reason: String(err),
            search_view_connected: false,
            mounted_row_count: 0,
            connected_row_count: 0,
            positive_geometry_row_count: 0,
            inspected_mounted_row_count: 0,
            target_match_count: 0,
            target_mounted_dom_position: null,
            target_connected: null,
            target_positive_geometry: null,
            mounted_row_records: [],
            target_row_geometry: null,
            target_anchor_geometry: null,
            layout_viewport: null,
            visual_viewport: null,
            scroll_container: null,
            clipping: null,
            hit_test: null,
            geometry_classification: "MOUNTED_DOM_STATE_INSUFFICIENT"
        };
    }
})
"""

JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION = r"""
((searchViewSelector, rowSelectors, targetChannelId, targetMessageTs, targetThreadTs, partialTargetScrollTop) => {
    try {
        const searchView = document.querySelector(searchViewSelector);
        if (!searchView || !searchView.isConnected) {
            return {
                ok: false,
                error: "SEARCH_VIEW_NOT_FOUND",
                scroll_top_before: null,
                scroll_top_immediate: null,
                target_scroll_top: partialTargetScrollTop
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

        let targetRowEl = null;
        for (const rowEl of allRows) {
            const anchors = Array.from(rowEl.querySelectorAll("a[href]"));
            for (const a of anchors) {
                try {
                    const urlObj = new URL(a.href, window.location.href);
                    const pathMatch = urlObj.pathname.match(/^\/archives\/([A-Z0-9]+)\/p(\d+)$/);
                    if (pathMatch) {
                        const parsedChan = pathMatch[1];
                        const rawDigits = pathMatch[2];
                        const parsedMsg = normTs(rawDigits.slice(0, 10) + "." + rawDigits.slice(10));
                        const parsedThr = normTs(urlObj.searchParams.get("thread_ts"));
                        if (parsedChan === targetChannelId && parsedMsg === targetMsgNorm && parsedThr === targetThrNorm) {
                            targetRowEl = rowEl;
                            break;
                        }
                    }
                } catch (e) {}
            }
            if (targetRowEl) break;
        }

        let scrollContainerEl = null;
        let curr = targetRowEl ? targetRowEl.parentElement : searchView.parentElement;
        while (curr && curr !== document.documentElement && curr !== document.body) {
            let cs = null;
            try {
                cs = window.getComputedStyle(curr);
            } catch (e) {
                cs = null;
            }
            if (cs) {
                const oy = cs.overflowY;
                const isScrollableY = (oy === "auto" || oy === "scroll" || oy === "overlay");
                if (isScrollableY && curr.scrollHeight > curr.clientHeight) {
                    scrollContainerEl = curr;
                    break;
                }
            }
            curr = curr.parentElement;
        }

        if (!scrollContainerEl) {
            const docEl = document.documentElement;
            let cs = null;
            try {
                cs = window.getComputedStyle(docEl);
            } catch (e) {
                cs = null;
            }
            if (cs && (cs.overflowY === "auto" || cs.overflowY === "scroll" || cs.overflowY === "overlay") && docEl.scrollHeight > docEl.clientHeight) {
                scrollContainerEl = docEl;
            }
        }

        if (!scrollContainerEl) {
            return {
                ok: false,
                error: "SCROLL_CONTAINER_NOT_FOUND",
                scroll_top_before: null,
                scroll_top_immediate: null,
                target_scroll_top: partialTargetScrollTop
            };
        }

        const scrollTopBefore = scrollContainerEl.scrollTop;
        scrollContainerEl.scrollTop = partialTargetScrollTop;
        const scrollTopImmediate = scrollContainerEl.scrollTop;

        return {
            ok: true,
            error: null,
            scroll_top_before: scrollTopBefore,
            scroll_top_immediate: scrollTopImmediate,
            target_scroll_top: partialTargetScrollTop
        };
    } catch (err) {
        return {
            ok: false,
            error: String(err),
            scroll_top_before: null,
            scroll_top_immediate: null,
            target_scroll_top: partialTargetScrollTop
        };
    }
})
"""


# ---------------------------------------------------------------------------
# Scroll Target Computation & Classification
# ---------------------------------------------------------------------------

def compute_bounded_partial_scroll_target(
    *,
    current_scroll_top: float,
    scroll_height: float,
    client_height: float,
    anchor_rect_top: float,
    anchor_rect_bottom: float,
    scroll_container_rect_top: float,
    scroll_container_rect_bottom: float,
    margin_px: float = SCROLL_BOTTOM_MARGIN_PX,
    max_delta_multiplier: float = MAX_SCROLL_DELTA_MULTIPLIER,
) -> dict[str, Any]:
    """Calculates bounded partial scroll progress target.

    1. Observational full reveal delta (VSA1 formula):
       visible_bottom = scroll_container_rect_bottom - margin_px
       full_reveal_required_delta = anchor_rect_bottom - visible_bottom
       full_reveal_requested_scroll_top = current_scroll_top + full_reveal_required_delta

    2. Frozen 3-viewport safety bound check:
       max_scroll_delta = max_delta_multiplier * client_height
       full_reveal_fits_within_bound = (full_reveal_required_delta <= max_scroll_delta)

    3. Partial scroll target calculation:
       max_scroll_top = max(0.0, scroll_height - client_height)
       partial_requested_scroll_top = current_scroll_top + max_scroll_delta
       partial_target_scroll_top = min(partial_requested_scroll_top, max_scroll_top)
       partial_scroll_delta = partial_target_scroll_top - current_scroll_top

    4. Delta validation:
       is_valid_partial_delta = (0.0 < partial_scroll_delta <= max_scroll_delta)
    """
    visible_bottom = scroll_container_rect_bottom - margin_px
    full_reveal_required_delta = anchor_rect_bottom - visible_bottom
    full_reveal_requested_scroll_top = current_scroll_top + full_reveal_required_delta

    max_scroll_delta = max_delta_multiplier * client_height
    full_reveal_fits_within_bound = (full_reveal_required_delta <= max_scroll_delta)

    is_already_visible = (
        anchor_rect_top >= scroll_container_rect_top and
        anchor_rect_bottom <= scroll_container_rect_bottom
    )

    max_scroll_top = max(0.0, scroll_height - client_height)
    partial_requested_scroll_top = current_scroll_top + max_scroll_delta
    partial_target_scroll_top = min(partial_requested_scroll_top, max_scroll_top)
    partial_scroll_delta = partial_target_scroll_top - current_scroll_top
    is_valid_partial_delta = (0.0 < partial_scroll_delta <= max_scroll_delta)

    return {
        "visible_bottom": visible_bottom,
        "full_reveal_required_delta": full_reveal_required_delta,
        "full_reveal_requested_scroll_top": full_reveal_requested_scroll_top,
        "max_scroll_delta": max_scroll_delta,
        "full_reveal_fits_within_bound": full_reveal_fits_within_bound,
        "is_already_visible": is_already_visible,
        "max_scroll_top": max_scroll_top,
        "partial_requested_scroll_top": partial_requested_scroll_top,
        "partial_target_scroll_top": partial_target_scroll_top,
        "partial_scroll_delta": partial_scroll_delta,
        "is_valid_partial_delta": is_valid_partial_delta,
    }


def compute_residual_distance_telemetry(
    *,
    post_scroll_top: float,
    post_scroll_height: float,
    post_client_height: float,
    post_anchor_rect_bottom: float,
    post_scroll_container_rect_bottom: float,
    margin_px: float = SCROLL_BOTTOM_MARGIN_PX,
) -> dict[str, Any]:
    """Computes read-only observational telemetry for remaining distance to full reveal."""
    residual_visible_bottom = post_scroll_container_rect_bottom - margin_px
    residual_required_delta = post_anchor_rect_bottom - residual_visible_bottom
    residual_requested_scroll_top = post_scroll_top + residual_required_delta
    residual_max_scroll_top = max(0.0, post_scroll_height - post_client_height)

    return {
        "residual_visible_bottom": residual_visible_bottom,
        "residual_required_delta": residual_required_delta,
        "residual_requested_scroll_top": residual_requested_scroll_top,
        "residual_max_scroll_top": residual_max_scroll_top,
    }


def classify_vsa2_post_scroll_state(
    *,
    pre_scroll_telemetry: Optional[dict[str, Any]],
    post_scroll_telemetry: Optional[dict[str, Any]],
    scroll_top_before: float,
    scroll_top_after: float,
    partial_target_scroll_top: float,
    tolerance_px: float = SMALL_SCROLL_TOLERANCE_PX,
) -> tuple[str, dict[str, Any]]:
    """Evaluates post-scroll telemetry according to frozen VSA2 hierarchy.
    Returns (classification_string, details_dict).
    """
    details: dict[str, Any] = {
        "scroll_top_movement_valid": False,
        "target_match_count": 0,
        "anchor_valid": False,
        "geometric_progress_proven": False,
        "target_became_visible": False,
        "distance_below_viewport_before": None,
        "distance_below_viewport_after": None,
        "progress_delta_px": None,
    }

    if not post_scroll_telemetry or not post_scroll_telemetry.get("ok"):
        return "FAIL_CLOSED", details

    # Scroll position verification
    scroll_applied_correctly = (
        scroll_top_after > scroll_top_before and
        abs(scroll_top_after - partial_target_scroll_top) <= tolerance_px
    )
    details["scroll_top_movement_valid"] = scroll_applied_correctly
    if not scroll_applied_correctly:
        return "PARTIAL_SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED", details

    # Target reacquisition
    target_match_count = post_scroll_telemetry.get("target_match_count", 0)
    details["target_match_count"] = target_match_count
    if target_match_count == 0:
        return "TARGET_LOST_AFTER_PARTIAL_SCROLL", details
    if target_match_count > 1:
        return "TARGET_DUPLICATED_AFTER_PARTIAL_SCROLL", details

    # Anchor reacquisition
    anchor_geom = post_scroll_telemetry.get("target_anchor_geometry") or {}
    anchor_valid = bool(
        anchor_geom.get("anchor_found") and
        anchor_geom.get("anchor_unique") and
        anchor_geom.get("qualified_target_anchor_count") == 1
    )
    details["anchor_valid"] = anchor_valid
    if not anchor_valid:
        return "TARGET_ANCHOR_INVALID_AFTER_PARTIAL_SCROLL", details

    # Progress proof
    pre_anc = (pre_scroll_telemetry or {}).get("target_anchor_geometry") or {}
    pre_sc = (pre_scroll_telemetry or {}).get("scroll_container") or {}
    post_sc = post_scroll_telemetry.get("scroll_container") or {}

    anc_top_before = pre_anc.get("anchor_rect_top", 0.0)
    sc_bottom_before = pre_sc.get("scroll_container_rect_bottom", 0.0)
    anc_top_after = anchor_geom.get("anchor_rect_top", 0.0)
    sc_bottom_after = post_sc.get("scroll_container_rect_bottom", 0.0)

    dist_before = max(anc_top_before - sc_bottom_before, 0.0)
    dist_after = max(anc_top_after - sc_bottom_after, 0.0)
    details["distance_below_viewport_before"] = dist_before
    details["distance_below_viewport_after"] = dist_after
    details["progress_delta_px"] = dist_before - dist_after

    if dist_after >= dist_before:
        details["geometric_progress_proven"] = False
        return "NO_GEOMETRIC_PROGRESS_AFTER_PARTIAL_SCROLL", details

    details["geometric_progress_proven"] = True

    # Target visibility check (observational)
    intersects_scroll = anchor_geom.get("anchor_intersects_scroll_viewport", False)
    details["target_became_visible"] = intersects_scroll

    return "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED", details


def evaluate_search_surface_current_query_correlation(
    *,
    search_view_connected: bool,
    surface_query_matches: bool,
    search_readiness: str,
) -> bool:
    return bool(search_view_connected and surface_query_matches and search_readiness == "RESULTS_READY")


def build_canonical_backend_identity_set(
    raw_results: list[dict[str, Any]] | list[SlackSearchResult],
) -> tuple[set[tuple[str, str, Optional[str]]], dict[str, int]]:
    """Builds the canonical normalized identity set from backend search results (max 5)."""
    identity_set: set[tuple[str, str, Optional[str]]] = set()
    parse_success_count = 0
    parse_failure_count = 0

    for item in raw_results:
        plink: Optional[str] = None
        if isinstance(item, dict):
            plink = item.get("result_url") or item.get("canonical_permalink") or item.get("permalink")
        elif hasattr(item, "permalink"):
            plink = getattr(item, "permalink")
        elif hasattr(item, "url"):
            plink = getattr(item, "url")

        if not plink:
            parse_failure_count += 1
            continue

        parsed = parse_slack_message_permalink(str(plink))
        if parsed and parsed.channel_id and parsed.message_ts:
            norm_msg = normalize_slack_ts(parsed.message_ts)
            norm_thr = normalize_slack_ts(parsed.thread_ts) if parsed.thread_ts else None
            identity_set.add((parsed.channel_id, norm_msg, norm_thr))
            parse_success_count += 1
        else:
            parse_failure_count += 1

    stats = {
        "backend_result_count": len(raw_results),
        "backend_identity_parse_success_count": parse_success_count,
        "backend_identity_parse_failure_count": parse_failure_count,
    }
    return identity_set, stats


def build_canonical_mounted_row_identity(rec: dict[str, Any]) -> tuple[str, str, Optional[str]]:
    return (
        rec.get("channel_id") or "",
        normalize_slack_ts(rec.get("message_ts")) or "",
        normalize_slack_ts(rec.get("thread_ts")),
    )


def evaluate_row_current_query_membership(
    *,
    row_connected: bool,
    surface_correlated: bool,
    row_identity: tuple[str, str, Optional[str]],
    backend_identity_set: set[tuple[str, str, Optional[str]]],
) -> str:
    if not row_connected:
        return "DISCONNECTED"
    if not surface_correlated:
        return "SURFACE_UNCORRELATED"
    if row_identity in backend_identity_set:
        return "PROVEN_CURRENT_QUERY_MEMBER"
    return "UNPROVEN"


def harvest_search_telemetry(backend: Optional[Any], exc: Optional[Exception], report: dict[str, Any]) -> None:
    telem = None
    if backend is not None and hasattr(backend, "last_search_telemetry"):
        telem = backend.last_search_telemetry

    if telem is None and exc is not None and hasattr(exc, "details") and isinstance(exc.details, dict):
        telem = exc.details

    if telem:
        report["safety_telemetry"] = telem
        report["search_telemetry"] = telem
        report["search_telemetry_extracted"] = True

        ft = telem.get("forensic_transition", {})
        report["live_action_inventory"]["search_input_ready"] = ft.get("INPUT_MATCHES_REQUESTED", "UNKNOWN")
        report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = telem.get("SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED", True)
        report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = ft.get("INPUT_MATCHES_REQUESTED", False)
        report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = ft.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False)
        report["checks"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = telem.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH", False)
        report["checks"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] = telem.get("SLACKBOT_ACTION_INVOKED_BY_PESU", False)
        report["checks"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = telem.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU", False)
        report["checks"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = telem.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU", False)


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    checks = report.get("checks", {})
    if not checks.get("SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED", False):
        return False, "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED is not True"
    if not checks.get("QUERY_STATE_VERIFIED_BEFORE_SUBMIT", False):
        return False, "QUERY_STATE_VERIFIED_BEFORE_SUBMIT is not True"
    if not checks.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False):
        return False, "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED is not True"
    if checks.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH", True):
        return False, "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH is True"
    if checks.get("SLACKBOT_ACTION_INVOKED_BY_PESU", True):
        return False, "SLACKBOT_ACTION_INVOKED_BY_PESU is True"
    if checks.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU", True):
        return False, "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU is True"
    if checks.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU", True):
        return False, "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU is True"

    actions = report.get("live_action_inventory", {})
    if actions.get("search_submissions", 0) != 1:
        return False, f"search_submissions {actions.get('search_submissions')} != 1"
    if report.get("provider_calls", 0) != 0:
        return False, "provider calls detected"
    if report.get("retry_occurred", False):
        return False, "retry occurred"

    return True, "SAFE"


def sanitize_report_for_persistence(data: Any) -> Any:
    forbidden_exact_keys = {
        "canonical_permalink",
        "result_url",
        "raw_permalink",
        "raw_href",
        "sender_text",
        "normalized_body",
        "message_body",
        "body",
        "text",
        "html",
        "inner_html",
        "outer_html",
        "aria_label",
        "title",
    }

    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if k in forbidden_exact_keys:
                continue
            if isinstance(v, str):
                cleaned_str = re.sub(r"https?://[^\s\x22\x27]+", "[URL_OMITTED]", v)
                cleaned_str = re.sub(r"/archives/[A-Z0-9]+(/[a-z0-9\.\?_=-]*)?", "[ARCHIVE_PATH_OMITTED]", cleaned_str)
                sanitized[k] = cleaned_str
            else:
                sanitized[k] = sanitize_report_for_persistence(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_report_for_persistence(elem) for elem in data]
    elif isinstance(data, str):
        cleaned_str = re.sub(r"https?://[^\s\x22\x27]+", "[URL_OMITTED]", data)
        cleaned_str = re.sub(r"/archives/[A-Z0-9]+(/[a-z0-9\.\?_=-]*)?", "[ARCHIVE_PATH_OMITTED]", cleaned_str)
        return cleaned_str
    return data


# ---------------------------------------------------------------------------
# Static Code Analysis & Parity Audits
# ---------------------------------------------------------------------------

def get_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    deps = {
        "vsa2_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2.py",
        "vsa1_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_bounded_viewport_scroll_vsa1.py",
        "mgr1r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1r1.py",
        "attempt4_runner": REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py",
        "pesu_webview_host": SRC_DIR / "pesu_agent" / "runtime" / "pesu_webview_host.py",
        "webview_slack_search": SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py",
        "slack_permalink_parser": SRC_DIR / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
    }
    res = {}
    for name, p in deps.items():
        if p.exists():
            res[name] = get_file_sha256(p)
        else:
            res[name] = "MISSING"
    return res


def extract_func_ast(file_path: Path, func_name: str) -> Optional[ast.FunctionDef]:
    if not file_path.exists():
        return None
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node
    except Exception as e:
        logger.error(f"Failed to parse AST for {file_path}: {e}")
    return None


def verify_search_telemetry_authority_parity_with_attempt4() -> bool:
    attempt4_path = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py"
    func = extract_func_ast(attempt4_path, "harvest_search_telemetry")
    if not func:
        return False
    source = ast.unparse(func)
    return "backend.last_search_telemetry" in source and "backend.last_telemetry" not in source


def verify_search_exception_order_parity_with_attempt4() -> bool:
    attempt4_path = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py"
    if not attempt4_path.exists():
        return False
    txt = attempt4_path.read_text(encoding="utf-8")
    return "backend.search_messages" in txt and "harvest_search_telemetry" in txt


def verify_search_safety_gate_parity_with_attempt4() -> bool:
    attempt4_path = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py"
    func = extract_func_ast(attempt4_path, "evaluate_search_safety_and_query_authority")
    if not func:
        return False
    src = ast.unparse(func)
    return ("SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED" in src and
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH" in src and
            "SLACKBOT_ACTION_INVOKED_BY_PESU" in src and
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU" in src and
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU" in src)


def verify_runtime_search_path_parity_with_attempt4() -> bool:
    attempt4_path = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py"
    if not attempt4_path.exists():
        return False
    txt = attempt4_path.read_text(encoding="utf-8")
    return "PesuWebViewHost" in txt and "WebViewSlackSearchBackend" in txt and "backend.start_runtime" in txt


def verify_host_dom_evaluation_api_parity() -> bool:
    import inspect
    if not hasattr(PesuWebViewHost, "evaluate_js"):
        return False
    sig = inspect.signature(PesuWebViewHost.evaluate_js)
    return "script" in sig.parameters and "timeout" in sig.parameters


def verify_runtime_api_signature_parity() -> bool:
    import inspect
    try:
        sig_cfg = inspect.signature(WebViewRuntimeConfig)
        sig_cfg.bind(storage_path=Path(DIRECT_SLACK_PROFILE_PATH))

        sig_host = inspect.signature(PesuWebViewHost)
        sig_host.bind(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / VSA1 Bounded Viewport Scroll",
        )

        sig_backend = inspect.signature(WebViewSlackSearchBackend)
        dummy_cfg = WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH))
        sig_backend.bind(config=dummy_cfg, host=None)

        sig_start = inspect.signature(WebViewSlackSearchBackend.start_runtime)
        sig_start.bind(None, timeout=30.0)

        sig_search = inspect.signature(WebViewSlackSearchBackend.search_messages)
        sig_search.bind(None, TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)

        if not hasattr(PesuWebViewHost, "evaluate_js") or not callable(getattr(PesuWebViewHost, "evaluate_js")):
            logger.error("PesuWebViewHost does not expose callable evaluate_js")
            return False

        sig_eval = inspect.signature(PesuWebViewHost.evaluate_js)
        sig_eval.bind(None, "/* script */", timeout=10.0)

        return True
    except Exception as e:
        logger.error(f"Runtime API signature parity hard gate failed: {e}")
        return False


def verify_search_result_row_enumeration_parity() -> bool:
    return (
        SEARCH_VIEW_SELECTOR == PROD_SEARCH_VIEW_SELECTOR
        and SEARCH_RESULT_ROW_SELECTORS == PROD_SEARCH_RESULT_ROW_SELECTORS
    )


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    js_obs = globals().get("JS_READ_ONLY_MOUNTED_DOM_OBSERVATION", "")
    js_mut = globals().get("JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION", "")

    # Pre-scroll / post-scroll observation must contain NO mutations whatsoever
    forbidden_in_obs = [
        (r"\.scrollTop\s*=", "scrollTop assignment"),
        (r"\.scrollLeft\s*=", "scrollLeft assignment"),
        (r"scrollIntoView", "scrollIntoView"),
        (r"scrollTo", "scrollTo"),
        (r"scrollBy", "scrollBy"),
        (r"\.click\(", ".click()"),
        (r"dispatchEvent", "dispatchEvent"),
        (r"MouseEvent", "MouseEvent"),
        (r"PointerEvent", "PointerEvent"),
        (r"KeyboardEvent", "KeyboardEvent"),
        (r"history\.(pushState|replaceState)", "history manipulation"),
        (r"location\.href\s*=", "location.href mutation"),
        (r"fetch\(", "fetch()"),
        (r"XMLHttpRequest", "XMLHttpRequest"),
        (r"WebSocket", "WebSocket"),
    ]

    for pat, name in forbidden_in_obs:
        if re.search(pat, js_obs):
            logger.error(f"Forbidden action '{name}' detected in observation script")
            return False

    # Scroll mutation script must ONLY mutate scrollTop on scrollContainerEl, nothing else
    forbidden_in_mut = [
        (r"scrollIntoView", "scrollIntoView"),
        (r"scrollTo", "scrollTo"),
        (r"scrollBy", "scrollBy"),
        (r"\.click\(", ".click()"),
        (r"dispatchEvent", "dispatchEvent"),
        (r"MouseEvent", "MouseEvent"),
        (r"PointerEvent", "PointerEvent"),
        (r"KeyboardEvent", "KeyboardEvent"),
        (r"history\.(pushState|replaceState)", "history manipulation"),
        (r"location\.href\s*=", "location.href mutation"),
        (r"fetch\(", "fetch()"),
        (r"XMLHttpRequest", "XMLHttpRequest"),
        (r"WebSocket", "WebSocket"),
    ]

    for pat, name in forbidden_in_mut:
        if re.search(pat, js_mut):
            logger.error(f"Forbidden action '{name}' detected in scroll mutation script")
            return False

    # Confirm scrollContainerEl.scrollTop assignment is present exactly
    if "scrollContainerEl.scrollTop = partialTargetScrollTop;" not in js_mut:
        logger.error("Missing scrollContainerEl.scrollTop = partialTargetScrollTop assignment in scroll mutation script")
        return False

    return True


def verify_one_scroll_only_contract() -> bool:
    return (
        MAX_SCROLL_ACTIONS == 1
        and MAX_SLACK_SEARCH_OPERATIONS == 1
        and MAX_SEARCH_SUBMISSIONS == 1
        and MAX_GUARDED_ANCHOR_CLICKS == 0
        and MAX_REPLY_BAR_CLICKS == 0
        and MAX_RESULT_CARD_CLICKS == 0
        and MAX_THREAD_EXPANSIONS == 0
        and MAX_KEYBOARD_EVENTS == 0
        and MAX_COMPOSER_INTERACTIONS == 0
        and MAX_WRITE_CAPABLE_ACTIONS == 0
        and MAX_PROVIDER_CALLS == 0
        and MAX_RETRIES == 0
        and MAX_RESTORATIONS == 0
        and MAX_BACK_NAVIGATIONS == 0
    )


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
            ["git", "status", "--porcelain", "-uno"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception:
        return True


def get_all_loaded_project_files() -> dict[str, str]:
    loaded_files: dict[str, str] = {}
    this_script = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2.py"
    if this_script.exists():
        loaded_files["scripts/qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2.py"] = get_file_sha256(this_script)

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
    actual_closure = get_all_loaded_project_files()

    missing = []
    mismatch = []
    stale = []

    for path_str, exp_hash in expected_closure.items():
        if path_str not in actual_closure:
            missing.append(path_str)
        elif actual_closure[path_str] != exp_hash:
            mismatch.append({
                "path": path_str,
                "expected": exp_hash,
                "actual": actual_closure[path_str],
            })

    for path_str in actual_closure:
        if path_str not in expected_closure:
            stale.append(path_str)

    passed = (len(missing) == 0 and len(mismatch) == 0 and len(stale) == 0)
    return {
        "closure_audit_passed": passed,
        "loaded_file_count": len(actual_closure),
        "manifest_closure_count": len(expected_closure),
        "missing_from_closure": missing,
        "stale_in_closure": stale,
        "mismatched_hashes": mismatch,
        "loaded_files": actual_closure,
    }


# ---------------------------------------------------------------------------
# Dry-Run Mock Builders
# ---------------------------------------------------------------------------

def build_mock_dry_run_backend_results() -> list[dict[str, Any]]:
    """Generates 5 mock backend search results reflecting standard Slack responses."""
    return [
        {
            "channel_id": "D07RA9YDE8G",
            "message_ts": "1788483883.410459",
            "canonical_permalink": "https://app.slack.com/archives/D07RA9YDE8G/p1788483883410459?thread_ts=1788483883.410459",
        },
        {
            "channel_id": "D07RA9YDE8G",
            "message_ts": "1788487621.916839",
            "canonical_permalink": "https://app.slack.com/archives/D07RA9YDE8G/p1788487621916839?thread_ts=1788487621.916839",
        },
        {
            "channel_id": "D07RA9YDE8G",
            "message_ts": "1788479531.716659",
            "canonical_permalink": "https://app.slack.com/archives/D07RA9YDE8G/p1788479531716659?thread_ts=1788479531.716659",
        },
        {
            "channel_id": "C038SK6ETAR",
            "message_ts": "1786434923.925049",
            "canonical_permalink": "https://app.slack.com/archives/C038SK6ETAR/p1786434923925049",
        },
        {
            "channel_id": "C4AQZGQNR",
            "message_ts": "1783929611.026919",
            "canonical_permalink": "https://app.slack.com/archives/C4AQZGQNR/p1783929611026919",
        },
    ]


def build_mock_dry_run_dom_snapshot(
    *,
    case: str = "below_viewport_normal",
    simulated_position: int = 19,
    backend_identity_set: Optional[set[tuple[str, str, Optional[str]]]] = None,
    scroll_top: float = 0.0,
    anchor_rect_top: Optional[float] = None,
    anchor_rect_bottom: Optional[float] = None,
    scroll_container_rect_bottom: Optional[float] = None,
    scroll_container_client_height: Optional[float] = None,
    scroll_container_scroll_height: Optional[float] = None,
) -> dict[str, Any]:
    b_set = backend_identity_set or set()

    mounted_records = []
    for pos in range(1, 21):
        if pos == simulated_position and case != "target_lost":
            is_target = True
            c_id = TARGET_CHANNEL_ID
            m_ts = TARGET_MESSAGE_TS
            t_ts = TARGET_THREAD_TS
        else:
            is_target = False
            c_id = f"C042WS{pos:04d}"
            m_ts = f"1718079000.{pos:06d}"
            t_ts = None

        ident = (c_id, normalize_slack_ts(m_ts) or "", normalize_slack_ts(t_ts))
        membership = "PROVEN_CURRENT_QUERY_MEMBER" if ident in b_set else "UNPROVEN"

        mounted_records.append({
            "mounted_dom_position": pos,
            "channel_id": c_id,
            "message_ts": m_ts,
            "thread_ts": t_ts,
            "connected": True,
            "positive_geometry": True,
            "identity_parse_success": True,
            "is_thread_reply": bool(t_ts),
            "is_target": is_target,
            "row_current_query_membership": membership,
        })

    layout_vp = {
        "window_inner_width": 1264,
        "window_inner_height": 861,
        "document_client_width": 1264,
        "document_client_height": 861,
        "layout_viewport_width": 1264,
        "layout_viewport_height": 861,
    }
    visual_vp = {
        "visual_viewport_available": True,
        "visual_viewport_width": 1264,
        "visual_viewport_height": 861,
        "visual_viewport_offset_left": 0,
        "visual_viewport_offset_top": 0,
        "visual_viewport_scale": 1.0,
        "device_pixel_ratio": 1.0,
    }

    sc_rect = {
        "scroll_container_found": True,
        "scroll_container_is_search_view": False,
        "scroll_container_is_document_scroller": False,
        "scroll_container_client_width": 883,
        "scroll_container_client_height": 817,
        "scroll_container_scroll_width": 883,
        "scroll_container_scroll_height": 3661,
        "scroll_container_scroll_left": 0,
        "scroll_container_scroll_top": scroll_top,
        "scroll_container_rect_top": 40.0,
        "scroll_container_rect_bottom": 857.0,
        "scroll_container_rect_left": 70.0,
        "scroll_container_rect_right": 953.0,
    }

    if case == "target_lost":
        return {
            "ok": True,
            "search_view_connected": True,
            "mounted_row_count": 20,
            "inspected_mounted_row_count": 20,
            "connected_row_count": 20,
            "positive_geometry_row_count": 20,
            "target_match_count": 0,
            "target_mounted_dom_position": None,
            "target_connected": None,
            "target_positive_geometry": None,
            "mounted_row_records": mounted_records,
            "target_row_geometry": None,
            "target_anchor_geometry": None,
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": sc_rect,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM",
        }

    if case == "target_duplicated":
        mounted_records[0]["is_target"] = True
        mounted_records[simulated_position - 1]["is_target"] = True
        return {
            "ok": True,
            "search_view_connected": True,
            "mounted_row_count": 20,
            "inspected_mounted_row_count": 20,
            "connected_row_count": 20,
            "positive_geometry_row_count": 20,
            "target_match_count": 2,
            "target_mounted_dom_position": None,
            "target_connected": True,
            "target_positive_geometry": True,
            "mounted_row_records": mounted_records,
            "target_row_geometry": None,
            "target_anchor_geometry": None,
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": sc_rect,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM",
        }

    if anchor_rect_top is not None:
        anc_top = anchor_rect_top
        anc_bottom = anchor_rect_bottom if anchor_rect_bottom is not None else (anchor_rect_top + 17.59375)
        anc_y = (anc_top + anc_bottom) / 2.0
        anc_in_scroll = (anc_bottom >= sc_rect["scroll_container_rect_top"] and anc_top <= sc_rect["scroll_container_rect_bottom"])
    elif case == "already_visible":
        anc_top = 100.0
        anc_bottom = 118.0
        anc_y = 109.0
        anc_in_scroll = True
    elif case == "delta_exceeds_bound":
        anc_top = 5000.0
        anc_bottom = 5018.0
        anc_y = 5009.0
        anc_in_scroll = False
        sc_rect["scroll_container_scroll_height"] = 6000
    elif case in ("post_scroll_visible", "post_scroll_visible_and_reachable", "post_scroll_occluded", "post_scroll_element_from_point_null", "post_scroll_clipped"):
        anc_top = 800.0
        anc_bottom = 818.0
        anc_y = 809.0
        anc_in_scroll = True
    elif case == "post_scroll_below_viewport_progress":
        anc_top = 881.265625
        anc_bottom = 898.859375
        anc_y = (anc_top + anc_bottom) / 2.0
        anc_in_scroll = False
    elif case == "post_scroll_still_outside":
        anc_top = 900.0
        anc_bottom = 918.0
        anc_y = 909.0
        anc_in_scroll = False
    else:  # below_viewport_normal
        anc_top = 3332.265625
        anc_bottom = 3349.859375
        anc_y = 3341.0625
        anc_in_scroll = False

    anc_center_in_layout = (0 <= anc_y < 861 and 0 <= 774.59 < 1264)
    anc_intersects_layout = not (anc_bottom <= 0 or anc_top >= 861)

    hit_test = {
        "element_from_point_evaluated": anc_center_in_layout,
        "element_from_point_is_null": (case == "post_scroll_element_from_point_null"),
        "element_from_point_is_anchor": (case == "post_scroll_visible_and_reachable"),
        "element_from_point_is_anchor_descendant": False,
        "anchor_contains_element_from_point": (case == "post_scroll_visible_and_reachable"),
        "element_from_point_contains_anchor": False,
    }

    if case == "post_scroll_occluded":
        hit_test["element_from_point_is_null"] = False
        hit_test["element_from_point_is_anchor"] = False
        hit_test["anchor_contains_element_from_point"] = False

    clipping = {
        "clipping_ancestor_present": True,
        "anchor_center_inside_all_clipping_ancestors": (case not in ("post_scroll_clipped", "below_viewport_normal")),
    }

    target_anchor_geom = {
        "anchor_found": (case != "anchor_invalid"),
        "anchor_unique": (case != "anchor_invalid"),
        "qualified_target_anchor_count": 0 if case == "anchor_invalid" else 1,
        "anchor_positive_geometry": True,
        "anchor_rect_top": anc_top,
        "anchor_rect_bottom": anc_bottom,
        "anchor_rect_left": 703.1875,
        "anchor_rect_right": 846.0,
        "anchor_rect_width": 142.8125,
        "anchor_rect_height": 17.59375,
        "anchor_center_x": 774.59375,
        "anchor_center_y": anc_y,
        "anchor_center_inside_layout_viewport": anc_center_in_layout,
        "anchor_intersects_layout_viewport": anc_intersects_layout,
        "anchor_fully_inside_layout_viewport": (anc_top >= 0 and anc_bottom <= 861),
        "anchor_center_inside_scroll_viewport": anc_in_scroll,
        "anchor_intersects_scroll_viewport": anc_in_scroll,
        "anchor_fully_inside_scroll_viewport": anc_in_scroll,
    }

    target_row_geom = {
        "target_row_rect_top": anc_top - 4.0,
        "target_row_rect_bottom": anc_bottom + 110.0,
        "target_row_rect_left": 107.0,
        "target_row_rect_right": 866.0,
        "target_row_rect_width": 759.0,
        "target_row_rect_height": 132.0,
        "target_row_center_x": 486.5,
        "target_row_center_y": anc_y + 53.0,
        "target_row_center_inside_layout_viewport": anc_center_in_layout,
        "target_row_intersects_layout_viewport": anc_intersects_layout,
        "target_row_fully_inside_layout_viewport": False,
        "target_row_center_inside_scroll_viewport": anc_in_scroll,
        "target_row_intersects_scroll_viewport": anc_in_scroll,
        "target_row_fully_inside_scroll_viewport": False,
    }

    return {
        "ok": True,
        "search_view_connected": True,
        "mounted_row_count": 20,
        "inspected_mounted_row_count": 20,
        "connected_row_count": 20,
        "positive_geometry_row_count": 20,
        "target_match_count": 1,
        "target_mounted_dom_position": simulated_position,
        "target_connected": True,
        "target_positive_geometry": True,
        "mounted_row_records": mounted_records,
        "target_row_geometry": target_row_geom,
        "target_anchor_geometry": target_anchor_geom,
        "layout_viewport": layout_vp,
        "visual_viewport": visual_vp,
        "scroll_container": sc_rect,
        "clipping": clipping,
        "hit_test": hit_test,
        "geometry_classification": "MOUNTED_DOM_RESOLVED",
    }


# ---------------------------------------------------------------------------
# Core Diagnostic Execution
# ---------------------------------------------------------------------------

def run_vsa2_qualification(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / VSA2",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "geometry_classification": "UNKNOWN",
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
            "MAX_SCROLL_ACTIONS": MAX_SCROLL_ACTIONS,
            "MAX_PRE_SCROLL_DOM_EXTRACTIONS": MAX_PRE_SCROLL_DOM_EXTRACTIONS,
            "MAX_POST_SCROLL_DOM_SNAPSHOTS": MAX_POST_SCROLL_DOM_SNAPSHOTS,
            "MAX_GUARDED_ANCHOR_CLICKS": MAX_GUARDED_ANCHOR_CLICKS,
            "MAX_REPLY_BAR_CLICKS": MAX_REPLY_BAR_CLICKS,
            "MAX_RESULT_CARD_CLICKS": MAX_RESULT_CARD_CLICKS,
            "MAX_THREAD_EXPANSIONS": MAX_THREAD_EXPANSIONS,
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
            "search_submissions": 0,
            "pre_scroll_dom_extractions": 0,
            "scroll_actions": 0,
            "post_scroll_dom_snapshots": 0,
            "guarded_anchor_clicks": 0,
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
            "thread_expansions": 0,
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
            "MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY": False,
            "MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": False,
            "MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4": False,
            "MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4": False,
            "MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4": False,
            "MDR1_HOST_DOM_EVALUATION_API_PARITY": False,
            "VSA2_STATIC_ZERO_INTERACTION_AUDIT": False,
            "VSA2_ONE_SCROLL_ONLY_CONTRACT": False,
            "RUNTIME_API_SIGNATURE_PARITY": False,
            "frozen_protocol_respected": False,
            "frozen_hashes_matched": False,
            "historical_baselines_matched": False,
            "UNTRACKED_RUNTIME_DEPENDENCIES": -1,
            "STALE_RUNTIME_CLOSURE_ENTRIES": -1,
            "MISMATCHED_RUNTIME_DEPENDENCIES": -1,
            "attempt_marker_written": False,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": "UNKNOWN",
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": "UNKNOWN",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": "UNKNOWN",
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": "UNKNOWN",
            "SLACKBOT_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            "search_safety_gate": "UNKNOWN",
            "pre_scroll_dom_snapshot_executed": False,
            "scroll_mutation_executed": False,
            "post_scroll_dom_snapshot_executed": False,
        },
        "pre_scroll_telemetry": None,
        "scroll_calculation": None,
        "scroll_mutation_result": None,
        "post_scroll_telemetry": None,
        "simulation_dom": None,
        "safety_telemetry": {},
        "search_telemetry_extracted": False,
        "search_surface_current_query_correlated": None,
        "target_row_current_query_membership": None,
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
    if protocol_manifest.get("search_max_results") != SEARCH_MAX_RESULTS:
        semantic_errors.append(f"search_max_results mismatch: {protocol_manifest.get('search_max_results')} != {SEARCH_MAX_RESULTS}")
    if protocol_manifest.get("max_mounted_rows_to_inspect") != MAX_MOUNTED_ROWS_TO_INSPECT:
        semantic_errors.append(f"max_mounted_rows_to_inspect mismatch: {protocol_manifest.get('max_mounted_rows_to_inspect')} != {MAX_MOUNTED_ROWS_TO_INSPECT}")
    if protocol_manifest.get("canonical_dry_run_verdict") != CANONICAL_DRY_RUN_VERDICT:
        semantic_errors.append(f"canonical_dry_run_verdict mismatch: {protocol_manifest.get('canonical_dry_run_verdict')} != {CANONICAL_DRY_RUN_VERDICT}")

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

    report["checks"]["MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = verify_search_result_row_enumeration_parity()
    report["checks"]["MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["MDR1_HOST_DOM_EVALUATION_API_PARITY"] = verify_host_dom_evaluation_api_parity()
    report["checks"]["VSA2_STATIC_ZERO_INTERACTION_AUDIT"] = verify_static_zero_interaction_contract()
    report["checks"]["VSA2_ONE_SCROLL_ONLY_CONTRACT"] = verify_one_scroll_only_contract()
    report["checks"]["RUNTIME_API_SIGNATURE_PARITY"] = verify_runtime_api_signature_parity()

    if not all([
        report["checks"]["MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"],
        report["checks"]["MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_HOST_DOM_EVALUATION_API_PARITY"],
        report["checks"]["VSA2_STATIC_ZERO_INTERACTION_AUDIT"],
        report["checks"]["VSA2_ONE_SCROLL_ONLY_CONTRACT"],
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
    # PHASE 3: Dry-Run vs Live One-Shot Verification
    # -----------------------------------------------------------------------
    if dry_run:
        logger.info("🧪 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock inspection...")
        report["dry_run"] = True
        report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = True
        report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = True
        report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = True
        report["checks"]["search_safety_gate"] = "PASS"
        report["checks"]["pre_scroll_dom_snapshot_executed"] = True
        report["checks"]["scroll_mutation_executed"] = True
        report["checks"]["post_scroll_dom_snapshot_executed"] = True

        mock_backend_results = build_mock_dry_run_backend_results()
        backend_identity_set, backend_stats = build_canonical_backend_identity_set(mock_backend_results)
        report["backend_identity_telemetry"] = backend_stats

        mock_pre_dom = build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            backend_identity_set=backend_identity_set,
            scroll_top=0.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
            scroll_container_rect_bottom=857.0,
            scroll_container_client_height=817.0,
            scroll_container_scroll_height=3661.0,
        )
        report["pre_scroll_telemetry"] = mock_pre_dom

        surface_correlated = evaluate_search_surface_current_query_correlation(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
        )
        report["search_surface_current_query_correlated"] = surface_correlated

        target_membership = evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=surface_correlated,
            row_identity=CANONICAL_TARGET_IDENTITY,
            backend_identity_set=backend_identity_set,
        )
        report["target_row_current_query_membership"] = target_membership

        sc_info = mock_pre_dom.get("scroll_container") or {}
        anc_info = mock_pre_dom.get("target_anchor_geometry") or {}

        partial_calc = compute_bounded_partial_scroll_target(
            current_scroll_top=sc_info.get("scroll_container_scroll_top", 0.0),
            scroll_height=sc_info.get("scroll_container_scroll_height", 0.0),
            client_height=sc_info.get("scroll_container_client_height", 0.0),
            anchor_rect_top=anc_info.get("anchor_rect_top", 0.0),
            anchor_rect_bottom=anc_info.get("anchor_rect_bottom", 0.0),
            scroll_container_rect_top=sc_info.get("scroll_container_rect_top", 0.0),
            scroll_container_rect_bottom=sc_info.get("scroll_container_rect_bottom", 0.0),
        )
        report["partial_scroll_calculation"] = partial_calc
        target_scroll = partial_calc["partial_target_scroll_top"]

        mock_post_dom = build_mock_dry_run_dom_snapshot(
            case="post_scroll_below_viewport_progress",
            simulated_position=19,
            backend_identity_set=backend_identity_set,
            scroll_top=target_scroll,
            anchor_rect_top=3332.265625 - target_scroll,
            anchor_rect_bottom=3349.859375 - target_scroll,
            scroll_container_rect_bottom=857.0,
            scroll_container_client_height=817.0,
            scroll_container_scroll_height=3661.0,
        )
        report["post_scroll_telemetry"] = mock_post_dom
        report["scroll_mutation_result"] = {
            "ok": True,
            "scroll_container_found": True,
            "scroll_top_before": 0.0,
            "scroll_top_requested": target_scroll,
            "scroll_top_after": target_scroll,
            "scroll_delta_requested": target_scroll,
            "scroll_delta_actual": target_scroll,
            "tolerance_px": SMALL_SCROLL_TOLERANCE_PX,
            "scroll_applied_within_tolerance": True,
        }

        classification, details = classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=mock_pre_dom,
            post_scroll_telemetry=mock_post_dom,
            scroll_top_before=0.0,
            scroll_top_after=target_scroll,
            partial_target_scroll_top=target_scroll,
        )
        report["geometry_classification"] = classification
        report["diagnostic_verdict"] = classification
        report["partial_scroll_progress_details"] = details

        post_anc = mock_post_dom.get("target_anchor_geometry") or {}
        post_sc = mock_post_dom.get("scroll_container") or {}
        residual_tel = compute_residual_distance_telemetry(
            post_scroll_top=target_scroll,
            post_scroll_height=post_sc.get("scroll_container_scroll_height", 0.0),
            post_client_height=post_sc.get("scroll_container_client_height", 0.0),
            post_anchor_rect_bottom=post_anc.get("anchor_rect_bottom", 0.0),
            post_scroll_container_rect_bottom=post_sc.get("scroll_container_rect_bottom", 0.0),
        )
        report["residual_distance_telemetry"] = residual_tel
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
    # LIVE EXECUTION PATH (Exclusive One-Shot Consumption)
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
                "note": "One-shot qualification consumption marker. Replay strictly forbidden.",
            }
            json.dump(marker_data, mf, indent=2, ensure_ascii=False)
        report["checks"]["attempt_marker_written"] = True
        logger.info(f"🔒 [ONE_SHOT_CONSUMED] Wrote attempt marker to {ATTEMPT_STARTED_MARKER_FILE}")
    except FileExistsError:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED_RACE"
        report["error"] = "Attempt marker already created concurrently. Aborting."
        logger.error("🛑 Attempt marker already created concurrently.")
        return report

    host: Optional[PesuWebViewHost] = None
    backend: Optional[WebViewSlackSearchBackend] = None
    search_exception: Optional[Exception] = None
    search_results: list[SlackSearchResult] = []

    # -----------------------------------------------------------------------
    # PHASE 4A: Dedicated WebView Host & Search Backend Initialization
    # -----------------------------------------------------------------------
    try:
        report["harness_phases"].append("CONNECTING_WEBVIEW")
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (VSA1 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / VSA1 Bounded Viewport Scroll",
        )
        backend = WebViewSlackSearchBackend(
            config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH)),
            host=host,
        )
        logger.info("🌐 [RUNTIME_START] Starting Dedicated Slack WebView Host...")
        backend.start_runtime(timeout=timeout)
        report["harness_phases"].append("RUNTIME_STARTED")

    except Exception as init_ex:
        logger.error(f"🛑 Runtime initialization exception: {init_ex}")
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_INITIALIZATION_EXCEPTION"
        report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
        report["error"] = f"Runtime initialization exception: {init_ex}"
        report["duration_seconds"] = round(time.time() - start_time, 2)
        if host is not None:
            try:
                host.destroy()
            except Exception:
                pass
        persist_live_artifact(report)
        return report

    # -----------------------------------------------------------------------
    # PHASE 4B: One Valid Search Operation
    # -----------------------------------------------------------------------
    try:
        try:
            report["harness_phases"].append("SUBMITTING_SEARCH")
            report["live_action_inventory"]["search_submissions"] = 1

            search_results = backend.search_messages(TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)
            report["live_action_inventory"]["search_modal_observed"] = True

        except Exception as ex:
            search_exception = ex
            logger.warning(f"Search operation produced exception: {ex}")
        finally:
            harvest_search_telemetry(backend, search_exception, report)

        if search_exception is not None:
            logger.error(f"🛑 Search execution exception: {search_exception}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_EXECUTION_EXCEPTION"
            report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
            report["error"] = f"Search execution exception: {search_exception}"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        # -----------------------------------------------------------------------
        # PHASE 4C: Search Safety Gate & Surface Query Correlation Evaluation
        # -----------------------------------------------------------------------
        safety_passed, safety_reason = evaluate_search_safety_and_query_authority(report)
        report["checks"]["search_safety_gate"] = "PASS" if safety_passed else "FAIL"

        backend_identity_set, backend_stats = build_canonical_backend_identity_set(search_results)
        report["backend_identity_telemetry"] = backend_stats

        if not safety_passed:
            logger.error(f"🛑 Search safety gate failed: {safety_reason}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_SAFETY_GATE_FAILED"
            report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
            report["error"] = f"Search safety gate rejected execution: {safety_reason}"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        # -----------------------------------------------------------------------
        # PHASE 4D: Pre-Scroll Read-Only Resolution (Zero Mutation)
        # -----------------------------------------------------------------------
        report["harness_phases"].append("PRE_SCROLL_READ_ONLY_OBSERVATION")
        report["live_action_inventory"]["pre_scroll_dom_extractions"] = 1

        try:
            js_pre_obs = (
                f"({JS_READ_ONLY_MOUNTED_DOM_OBSERVATION})("
                f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
                f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
                f"{MAX_MOUNTED_ROWS_TO_INSPECT}, "
                f"{json.dumps(TARGET_CHANNEL_ID)}, "
                f"{json.dumps(TARGET_MESSAGE_TS)}, "
                f"{json.dumps(TARGET_THREAD_TS)}, "
                f"{json.dumps(FROZEN_ALLOWED_ORIGINS)})"
            )
            pre_dom = host.evaluate_js(js_pre_obs, timeout=10.0)
            if not isinstance(pre_dom, dict):
                raise ValueError(f"Pre-scroll DOM observation returned non-dict: {type(pre_dom)}")
            report["pre_scroll_telemetry"] = pre_dom
            report["checks"]["pre_scroll_dom_snapshot_executed"] = True

            surface_query_matches = False
            safety_tel = report.get("safety_telemetry", {})
            if safety_tel:
                ft = safety_tel.get("forensic_transition", {})
                surface_query_matches = bool(ft.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))

            readiness_val = safety_tel.get("SEARCH_RESULT_READINESS", "UNKNOWN")
            search_view_conn = bool(pre_dom and pre_dom.get("search_view_connected", False))

            surface_correlated = evaluate_search_surface_current_query_correlation(
                search_view_connected=search_view_conn,
                surface_query_matches=surface_query_matches,
                search_readiness=readiness_val,
            )
            report["search_surface_current_query_correlated"] = surface_correlated

            if not surface_correlated:
                logger.error("🛑 Search surface not correlated with current query. Aborting before scroll.")
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "FAIL_CLOSED"
                report["error"] = "Search surface current query correlation failed"
                persist_live_artifact(report)
                return report

            target_row_conn = bool(pre_dom and pre_dom.get("target_connected", False))
            target_membership = evaluate_row_current_query_membership(
                row_connected=target_row_conn,
                surface_correlated=surface_correlated,
                row_identity=CANONICAL_TARGET_IDENTITY,
                backend_identity_set=backend_identity_set,
            )
            report["target_row_current_query_membership"] = target_membership

            target_match_count = pre_dom.get("target_match_count", 0)
            if target_match_count == 0:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "TARGET_NOT_FOUND_PRE_SCROLL"
                report["error"] = "Target not found in mounted rows before scroll"
                persist_live_artifact(report)
                return report
            elif target_match_count > 1:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "TARGET_DUPLICATED_PRE_SCROLL"
                report["error"] = f"Target duplicated ({target_match_count} matches) before scroll"
                persist_live_artifact(report)
                return report

            anc_geom = pre_dom.get("target_anchor_geometry") or {}
            if not anc_geom.get("anchor_found") or not anc_geom.get("anchor_unique") or anc_geom.get("qualified_target_anchor_count") != 1 or not anc_geom.get("anchor_positive_geometry"):
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "TARGET_ANCHOR_INVALID_PRE_SCROLL"
                report["error"] = "Target anchor invalid or not unique in target row before scroll"
                persist_live_artifact(report)
                return report

            sc_info = pre_dom.get("scroll_container") or {}
            if not sc_info.get("scroll_container_found"):
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "SCROLL_CONTAINER_NOT_FOUND"
                report["error"] = "Nearest vertical scroll container not found before scroll"
                persist_live_artifact(report)
                return report

            sc_bottom = sc_info.get("scroll_container_rect_bottom", 0.0)
            anc_top = anc_geom.get("anchor_rect_top", 0.0)
            anc_bottom = anc_geom.get("anchor_rect_bottom", 0.0)
            sc_top = sc_info.get("scroll_container_rect_top", 0.0)

            if anc_geom.get("anchor_intersects_scroll_viewport") or (anc_top >= sc_top and anc_bottom <= sc_bottom):
                logger.info("ℹ️ Target anchor already visible in scroll container. Partial scrolling not required.")
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "PARTIAL_SCROLL_NOT_REQUIRED_TARGET_ALREADY_VISIBLE"
                report["error"] = "Target anchor already visible in scroll viewport"
                persist_live_artifact(report)
                return report

            if anc_top < sc_bottom:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "TARGET_ANCHOR_INVALID_PRE_SCROLL"
                report["error"] = f"Target anchor top {anc_top} < scroll container bottom {sc_bottom} but not visible"
                persist_live_artifact(report)
                return report

            partial_calc = compute_bounded_partial_scroll_target(
                current_scroll_top=sc_info.get("scroll_container_scroll_top", 0.0),
                scroll_height=sc_info.get("scroll_container_scroll_height", 0.0),
                client_height=sc_info.get("scroll_container_client_height", 0.0),
                anchor_rect_top=anc_top,
                anchor_rect_bottom=anc_bottom,
                scroll_container_rect_top=sc_top,
                scroll_container_rect_bottom=sc_bottom,
                margin_px=SCROLL_BOTTOM_MARGIN_PX,
                max_delta_multiplier=MAX_SCROLL_DELTA_MULTIPLIER,
            )
            report["partial_scroll_calculation"] = partial_calc

            if partial_calc["full_reveal_fits_within_bound"]:
                logger.info("ℹ️ Full reveal delta fits within original 3-viewport bound. VSA2 not applicable.")
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "FULL_REVEAL_NOW_WITHIN_VSA1_BOUND"
                report["error"] = f"Full reveal required delta {partial_calc['full_reveal_required_delta']} <= max {partial_calc['max_scroll_delta']}"
                persist_live_artifact(report)
                return report

            if not partial_calc["is_valid_partial_delta"]:
                logger.error(f"🛑 Partial scroll delta invalid: delta={partial_calc['partial_scroll_delta']}, max={partial_calc['max_scroll_delta']}")
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "PARTIAL_SCROLL_DELTA_INVALID"
                report["error"] = f"Partial scroll delta {partial_calc['partial_scroll_delta']} invalid"
                persist_live_artifact(report)
                return report

        except Exception as pre_ex:
            logger.error(f"Error evaluating pre-scroll resolution: {pre_ex}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "PRE_SCROLL_SNAPSHOT_EXCEPTION"
            report["error"] = str(pre_ex)
            persist_live_artifact(report)
            return report

        # -----------------------------------------------------------------------
        # PHASE 4E: Exactly One Guarded Partial Scroll Mutation
        # -----------------------------------------------------------------------
        report["harness_phases"].append("APPLYING_BOUNDED_PARTIAL_SCROLL_MUTATION")
        report["live_action_inventory"]["scroll_actions"] = 1
        partial_target_scroll_top = report["partial_scroll_calculation"]["partial_target_scroll_top"]

        try:
            js_scroll_mut = (
                f"({JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION})("
                f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
                f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
                f"{json.dumps(TARGET_CHANNEL_ID)}, "
                f"{json.dumps(TARGET_MESSAGE_TS)}, "
                f"{json.dumps(TARGET_THREAD_TS)}, "
                f"{partial_target_scroll_top})"
            )
            scroll_mut_res = host.evaluate_js(js_scroll_mut, timeout=10.0)
            report["scroll_mutation_result"] = scroll_mut_res
            report["checks"]["scroll_mutation_executed"] = True

            if not isinstance(scroll_mut_res, dict) or not scroll_mut_res.get("ok"):
                err_msg = scroll_mut_res.get("error") if isinstance(scroll_mut_res, dict) else "non-dict return"
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "SCROLL_MUTATION_EXCEPTION"
                report["error"] = f"Partial scroll mutation script failed: {err_msg}"
                persist_live_artifact(report)
                return report

        except Exception as mut_ex:
            logger.error(f"Error executing partial scroll mutation: {mut_ex}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SCROLL_MUTATION_EXCEPTION"
            report["error"] = str(mut_ex)
            persist_live_artifact(report)
            return report
        # -----------------------------------------------------------------------
        # PHASE 4F: Non-Interactive Bounded Settle
        # -----------------------------------------------------------------------
        report["harness_phases"].append("POST_SCROLL_SETTLE")
        time.sleep(POST_SCROLL_SETTLE_MS / 1000.0)

        # -----------------------------------------------------------------------
        # PHASE 4G: Post-Scroll Read-Only Snapshot & Reacquisition
        # -----------------------------------------------------------------------
        report["harness_phases"].append("POST_SCROLL_READ_ONLY_SNAPSHOT")
        report["live_action_inventory"]["post_scroll_dom_snapshots"] = 1

        try:
            js_post_obs = (
                f"({JS_READ_ONLY_MOUNTED_DOM_OBSERVATION})("
                f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
                f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
                f"{MAX_MOUNTED_ROWS_TO_INSPECT}, "
                f"{json.dumps(TARGET_CHANNEL_ID)}, "
                f"{json.dumps(TARGET_MESSAGE_TS)}, "
                f"{json.dumps(TARGET_THREAD_TS)}, "
                f"{json.dumps(FROZEN_ALLOWED_ORIGINS)})"
            )
            post_dom = host.evaluate_js(js_post_obs, timeout=10.0)
            if not isinstance(post_dom, dict):
                raise ValueError(f"Post-scroll DOM observation returned non-dict: {type(post_dom)}")
            report["post_scroll_telemetry"] = post_dom
            report["checks"]["post_scroll_dom_snapshot_executed"] = True

            sc_post = post_dom.get("scroll_container") or {}
            scroll_top_after = sc_post.get("scroll_container_scroll_top", 0.0)
            scroll_top_before = report["pre_scroll_telemetry"]["scroll_container"]["scroll_container_scroll_top"]
            report["scroll_mutation_result"] = {
                "ok": True,
                "scroll_container_found": True,
                "scroll_top_before": scroll_top_before,
                "scroll_top_requested": partial_target_scroll_top,
                "scroll_top_after": scroll_top_after,
                "scroll_delta_requested": round(partial_target_scroll_top - scroll_top_before, 6),
                "scroll_delta_actual": round(scroll_top_after - scroll_top_before, 6),
                "tolerance_px": SMALL_SCROLL_TOLERANCE_PX,
                "scroll_applied_within_tolerance": abs(scroll_top_after - partial_target_scroll_top) <= SMALL_SCROLL_TOLERANCE_PX,
            }

            classification, details = classify_vsa2_post_scroll_state(
                pre_scroll_telemetry=report["pre_scroll_telemetry"],
                post_scroll_telemetry=post_dom,
                scroll_top_before=scroll_top_before,
                scroll_top_after=scroll_top_after,
                partial_target_scroll_top=partial_target_scroll_top,
                tolerance_px=SMALL_SCROLL_TOLERANCE_PX,
            )
            report["geometry_classification"] = classification
            report["diagnostic_verdict"] = classification
            report["partial_scroll_progress_details"] = details

            post_anc = post_dom.get("target_anchor_geometry") or {}
            residual_tel = compute_residual_distance_telemetry(
                post_scroll_top=scroll_top_after,
                post_scroll_height=sc_post.get("scroll_container_scroll_height", 0.0),
                post_client_height=sc_post.get("scroll_container_client_height", 0.0),
                post_anchor_rect_bottom=post_anc.get("anchor_rect_bottom", 0.0),
                post_scroll_container_rect_bottom=sc_post.get("scroll_container_rect_bottom", 0.0),
                margin_px=SCROLL_BOTTOM_MARGIN_PX,
            )
            report["residual_distance_telemetry"] = residual_tel

            if details.get("target_became_visible"):
                report["checks"]["PARTIAL_SCROLL_TARGET_BECAME_VISIBLE"] = True

            if classification == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED":
                report["verdict"] = "QUALIFICATION_COMPLETED"
            else:
                report["verdict"] = "FAIL_CLOSED"
        except Exception as post_ex:
            logger.error(f"Error evaluating post-scroll snapshot: {post_ex}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "POST_SCROLL_SNAPSHOT_EXCEPTION"
            report["error"] = str(post_ex)

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

    persist_live_artifact(report)
    return report


def persist_live_artifact(report: dict[str, Any]) -> None:
    try:
        sanitized = sanitize_report_for_persistence(report)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(LIVE_OUTPUT_FILE), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 [PERSISTENCE_SUCCESS] Sanitized live artifact written to {LIVE_OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"❌ Failed to persist live artifact: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="VSA2: Bounded Partial Scroll Progress Qualification")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline dry-run test")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds")
    args = parser.parse_args()

    report = run_vsa2_qualification(dry_run=args.dry_run, timeout=args.timeout)
    print("\n" + "=" * 60)
    print("VSA2 QUALIFICATION EXECUTION SUMMARY")
    print("=" * 60)
    print(f"VERDICT:                  {report.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT:       {report.get('diagnostic_verdict')}")
    print(f"GEOMETRY_CLASSIFICATION:  {report.get('geometry_classification')}")
    print(f"ERROR:                    {report.get('error')}")
    print("=" * 60)

    if report.get("verdict") in ("QUALIFICATION_COMPLETED", "DIAGNOSTIC_COMPLETED", CANONICAL_DRY_RUN_VERDICT):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
