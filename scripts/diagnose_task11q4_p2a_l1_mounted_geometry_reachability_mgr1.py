"""Phase 13.5D / Task 11 / P2A / MGR1: Mounted Target Geometry Reachability Diagnostic 1.

MGR1 PURPOSE:
After one valid search, if the exact target is physically resident among the already-connected
mounted DOM rows, determine where its exact thread anchor lies relative to the layout viewport,
visual viewport, Slack scroll viewport, and hit-test surface.

Strict Boundaries:
- QUALIFICATION ONLY: Does NOT qualify production P2A, does NOT modify WebviewThreadReader,
  does NOT click any element, does NOT scroll any element, does NOT create Attempt 5.
- ONE ATOMIC READ-ONLY MOUNTED TARGET GEOMETRY SNAPSHOT:
  Resolves connected search_view, inspects all already-mounted rows up to the diagnostic
  cap (MAX_MOUNTED_ROWS_TO_INSPECT = 20), finds exact semantic target, resolves exact
  thread anchor, measures target row & anchor geometry, inspects layout & visual viewports,
  identifies nearest scroll container, checks clipping ancestors, and evaluates center hit-test.
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
- TARGET ANCHOR IDENTIFICATION:
  tagName == A
  isConnected == true
  valid Slack permalink matching exact target identity
  has_thread_ts_parameter == true
  qualified_target_anchor_count == 1
- SCROLL CONTAINER TELEMETRY:
  scrollHeight > clientHeight && overflowY in ('auto', 'scroll', 'overlay').
  scrollTop/scrollLeft read-only, never assigned.
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
logger = logging.getLogger("task11q4_p2a_l1_mounted_geometry_reachability_mgr1")

# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5
MAX_MOUNTED_ROWS_TO_INSPECT = 20

PROTOCOL_NAME = "task11q4_p2a_l1_mgr1_mounted_geometry_reachability"
DIAGNOSTIC_RUN = "MGR1_GEOMETRY_RUN_1"
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

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_p2a_l1_mgr1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mgr1_mounted_geometry_reachability_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mgr1_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_mgr1_mounted_geometry_reachability.json"

SEARCH_VIEW_SELECTOR = PROD_SEARCH_VIEW_SELECTOR
SEARCH_RESULT_ROW_SELECTORS = PROD_SEARCH_RESULT_ROW_SELECTORS

DIAGNOSTIC_OUTCOMES = [
    "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM",
    "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM",
    "TARGET_ROW_FOUND_ANCHOR_NOT_FOUND",
    "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE",
    "TARGET_ANCHOR_GEOMETRY_INVALID",
    "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT",
    "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR",
    "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL",
    "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT",
    "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE",
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
# JavaScript Atomic Mounted Target Geometry Snapshot
# ---------------------------------------------------------------------------

JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT = r"""
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
                target_connected: targetRecord.connected,
                target_positive_geometry: targetRecord.positive_geometry,
                mounted_row_records: mounted_row_records,
                target_row_geometry: targetRowGeometry,
                target_anchor_geometry: {
                    qualified_target_anchor_count: 0,
                    anchor_found: false,
                    anchor_unique: false,
                    anchor_rect_top: null,
                    anchor_rect_bottom: null,
                    anchor_rect_left: null,
                    anchor_rect_right: null,
                    anchor_rect_width: null,
                    anchor_rect_height: null,
                    anchor_center_x: null,
                    anchor_center_y: null,
                    anchor_positive_geometry: null,
                    anchor_center_inside_layout_viewport: null,
                    anchor_intersects_layout_viewport: null,
                    anchor_fully_inside_layout_viewport: null,
                    anchor_center_inside_scroll_viewport: null,
                    anchor_intersects_scroll_viewport: null,
                    anchor_fully_inside_scroll_viewport: null
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
                target_connected: targetRecord.connected,
                target_positive_geometry: targetRecord.positive_geometry,
                mounted_row_records: mounted_row_records,
                target_row_geometry: targetRowGeometry,
                target_anchor_geometry: {
                    qualified_target_anchor_count: anchorCount,
                    anchor_found: true,
                    anchor_unique: false,
                    anchor_rect_top: null,
                    anchor_rect_bottom: null,
                    anchor_rect_left: null,
                    anchor_rect_right: null,
                    anchor_rect_width: null,
                    anchor_rect_height: null,
                    anchor_center_x: null,
                    anchor_center_y: null,
                    anchor_positive_geometry: null,
                    anchor_center_inside_layout_viewport: null,
                    anchor_intersects_layout_viewport: null,
                    anchor_fully_inside_layout_viewport: null,
                    anchor_center_inside_scroll_viewport: null,
                    anchor_intersects_scroll_viewport: null,
                    anchor_fully_inside_scroll_viewport: null
                },
                layout_viewport: layoutViewportMetrics,
                visual_viewport: visualViewportMetrics,
                scroll_container: scrollContainerTelemetry,
                clipping: null,
                hit_test: null,
                geometry_classification: "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE"
            };
        }

        // Unique Anchor Geometry
        const anchorEl = qualifyingAnchors[0];
        const aRect = anchorEl.getBoundingClientRect();
        const aCenterX = aRect.left + aRect.width / 2.0;
        const aCenterY = aRect.top + aRect.height / 2.0;
        const aPosGeom = (aRect.width > 0 && aRect.height > 0);

        const aCenterInLayout = (aCenterX >= 0 && aCenterX < layoutVpW && aCenterY >= 0 && aCenterY < layoutVpH);
        const aIntersectsLayout = !(aRect.bottom <= 0 || aRect.top >= layoutVpH || aRect.right <= 0 || aRect.left >= layoutVpW);
        const aFullyInLayout = (aRect.top >= 0 && aRect.bottom <= layoutVpH && aRect.left >= 0 && aRect.right <= layoutVpW);

        const aCenterInScroll = scRect ? (aCenterX >= scRect.left && aCenterX <= scRect.right && aCenterY >= scRect.top && aCenterY <= scRect.bottom) : null;
        const aIntersectsScroll = scRect ? !(aRect.bottom <= scRect.top || aRect.top >= scRect.bottom || aRect.right <= scRect.left || aRect.left >= scRect.right) : null;
        const aFullyInScroll = scRect ? (aRect.top >= scRect.top && aRect.bottom <= scRect.bottom && aRect.left >= scRect.left && aRect.right <= scRect.right) : null;

        const targetAnchorGeometry = {
            qualified_target_anchor_count: 1,
            anchor_found: true,
            anchor_unique: true,
            anchor_rect_top: aRect.top,
            anchor_rect_bottom: aRect.bottom,
            anchor_rect_left: aRect.left,
            anchor_rect_right: aRect.right,
            anchor_rect_width: aRect.width,
            anchor_rect_height: aRect.height,
            anchor_center_x: aCenterX,
            anchor_center_y: aCenterY,
            anchor_positive_geometry: aPosGeom,
            anchor_center_inside_layout_viewport: aCenterInLayout,
            anchor_intersects_layout_viewport: aIntersectsLayout,
            anchor_fully_inside_layout_viewport: aFullyInLayout,
            anchor_center_inside_scroll_viewport: aCenterInScroll,
            anchor_intersects_scroll_viewport: aIntersectsScroll,
            anchor_fully_inside_scroll_viewport: aFullyInScroll
        };

        // Clipping Ancestors Check
        let clippingAncestorPresent = false;
        let anchorInsideAllClipping = true;
        let p = targetRowEl.parentElement;
        while (p && p !== document.body && p !== document.documentElement) {
            let cs = null;
            try {
                cs = window.getComputedStyle(p);
            } catch (e) {
                cs = null;
            }
            if (cs) {
                const ox = cs.overflowX;
                const oy = cs.overflowY;
                const isClip = (ox === "hidden" || ox === "clip" || oy === "hidden" || oy === "clip");
                if (isClip) {
                    clippingAncestorPresent = true;
                    const pr = p.getBoundingClientRect();
                    const inside = (aCenterX >= pr.left && aCenterX <= pr.right && aCenterY >= pr.top && aCenterY <= pr.bottom);
                    if (!inside) {
                        anchorInsideAllClipping = false;
                    }
                }
            }
            p = p.parentElement;
        }

        const clippingTelemetry = {
            clipping_ancestor_present: clippingAncestorPresent,
            anchor_center_inside_all_clipping_ancestors: anchorInsideAllClipping
        };

        // elementFromPoint Hit-Test
        let elementFromPointEvaluated = false;
        let elementFromPointIsNull = null;
        let elementFromPointIsAnchor = false;
        let elementFromPointIsAnchorDescendant = false;
        let anchorContainsElementFromPoint = false;
        let elementFromPointContainsAnchor = false;

        if (aCenterInLayout && typeof document.elementFromPoint === "function") {
            elementFromPointEvaluated = true;
            let hit = null;
            try {
                hit = document.elementFromPoint(aCenterX, aCenterY);
            } catch (e) {
                hit = null;
            }

            if (!hit) {
                elementFromPointIsNull = true;
            } else {
                elementFromPointIsNull = false;
                elementFromPointIsAnchor = (hit === anchorEl);
                elementFromPointIsAnchorDescendant = Boolean(hit !== anchorEl && anchorEl.contains(hit));
                anchorContainsElementFromPoint = Boolean(hit === anchorEl || anchorEl.contains(hit));
                elementFromPointContainsAnchor = Boolean(hit.contains && hit.contains(anchorEl));
            }
        }

        const hitTestTelemetry = {
            element_from_point_evaluated: elementFromPointEvaluated,
            element_from_point_is_null: elementFromPointIsNull,
            element_from_point_is_anchor: elementFromPointIsAnchor,
            element_from_point_is_anchor_descendant: elementFromPointIsAnchorDescendant,
            anchor_contains_element_from_point: anchorContainsElementFromPoint,
            element_from_point_contains_anchor: elementFromPointContainsAnchor
        };

        // Classification Hierarchy
        let classification = "FAIL_CLOSED";
        if (!aPosGeom) {
            classification = "TARGET_ANCHOR_GEOMETRY_INVALID";
        } else if (aCenterY >= layoutVpH) {
            classification = "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT";
        } else if (aCenterY < 0) {
            classification = "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT";
        } else if (aCenterX < 0) {
            classification = "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT";
        } else if (aCenterX >= layoutVpW) {
            classification = "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT";
        } else if (scrollContainerFound && aCenterInScroll === false) {
            classification = "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT";
        } else if (clippingAncestorPresent && !anchorInsideAllClipping) {
            classification = "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR";
        } else if (elementFromPointEvaluated && elementFromPointIsNull) {
            classification = "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL";
        } else if (elementFromPointEvaluated && !anchorContainsElementFromPoint) {
            classification = "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT";
        } else if (anchorContainsElementFromPoint) {
            classification = "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE";
        } else {
            classification = "FAIL_CLOSED";
        }

        return {
            ok: true,
            search_view_connected: true,
            mounted_row_count: mounted_row_count,
            inspected_mounted_row_count: inspected_mounted_row_count,
            connected_row_count: connected_row_count,
            positive_geometry_row_count: positive_geometry_row_count,
            target_match_count: 1,
            target_mounted_dom_position: targetDomPosition,
            target_connected: targetRecord.connected,
            target_positive_geometry: targetRecord.positive_geometry,
            mounted_row_records: mounted_row_records,
            target_row_geometry: targetRowGeometry,
            target_anchor_geometry: targetAnchorGeometry,
            layout_viewport: layoutViewportMetrics,
            visual_viewport: visualViewportMetrics,
            scroll_container: scrollContainerTelemetry,
            clipping: clippingTelemetry,
            hit_test: hitTestTelemetry,
            geometry_classification: classification
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

# ---------------------------------------------------------------------------
# Correlation & Classification Authorities
# ---------------------------------------------------------------------------

def evaluate_search_surface_current_query_correlation(
    search_view_connected: bool,
    surface_query_matches: Optional[bool],
    search_readiness: str,
) -> bool:
    """Evaluates whether the search view surface is correlated with the requested query."""
    return bool(
        search_view_connected
        and surface_query_matches is True
        and search_readiness == "RESULTS_READY"
    )


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

        parsed = parse_slack_message_permalink(plink)
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


def build_canonical_mounted_row_identity(
    row_record: dict[str, Any],
) -> Optional[tuple[str, str, Optional[str]]]:
    """Extracts canonical normalized identity from a mounted row record."""
    cid = row_record.get("channel_id")
    mts = row_record.get("message_ts")
    tts = row_record.get("thread_ts")
    if not cid or not mts:
        return None
    return (cid, normalize_slack_ts(mts), normalize_slack_ts(tts) if tts else None)


def evaluate_row_current_query_membership(
    row_connected: bool,
    surface_correlated: bool,
    row_identity: Optional[tuple[str, str, Optional[str]]],
    backend_identity_set: set[tuple[str, str, Optional[str]]],
) -> str:
    """Evaluates row-level membership attribution."""
    if not row_connected or not surface_correlated:
        return "STALE_OR_RECYCLED_POSSIBLE"
    if not row_identity:
        return "UNPROVEN"
    if row_identity in backend_identity_set:
        return "PROVEN"
    return "UNPROVEN"


def classify_mounted_geometry_reachability(
    dom_telemetry: Optional[dict[str, Any]],
) -> str:
    """Authoritative Python classification hierarchy matching the pure-read JS snapshot."""
    if not dom_telemetry or not dom_telemetry.get("ok"):
        return "MOUNTED_DOM_STATE_INSUFFICIENT"

    match_count = dom_telemetry.get("target_match_count", 0)
    if match_count == 0:
        return "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"
    if match_count > 1:
        return "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"

    anchor_geom = dom_telemetry.get("target_anchor_geometry") or {}
    q_count = anchor_geom.get("qualified_target_anchor_count", 0)
    if q_count == 0:
        return "TARGET_ROW_FOUND_ANCHOR_NOT_FOUND"
    if q_count > 1:
        return "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE"

    if not anchor_geom.get("anchor_positive_geometry"):
        return "TARGET_ANCHOR_GEOMETRY_INVALID"

    layout_vp = dom_telemetry.get("layout_viewport") or {}
    vp_w = layout_vp.get("layout_viewport_width", 0)
    vp_h = layout_vp.get("layout_viewport_height", 0)
    cx = anchor_geom.get("anchor_center_x")
    cy = anchor_geom.get("anchor_center_y")

    if cx is None or cy is None:
        return "TARGET_ANCHOR_GEOMETRY_INVALID"

    if cy >= vp_h:
        return "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"
    if cy < 0:
        return "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT"
    if cx < 0:
        return "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT"
    if cx >= vp_w:
        return "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT"

    scroll_cont = dom_telemetry.get("scroll_container") or {}
    if scroll_cont.get("scroll_container_found"):
        if anchor_geom.get("anchor_center_inside_scroll_viewport") is False:
            return "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    clipping = dom_telemetry.get("clipping") or {}
    if clipping.get("clipping_ancestor_present"):
        if not clipping.get("anchor_center_inside_all_clipping_ancestors"):
            return "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR"

    hit_test = dom_telemetry.get("hit_test") or {}
    if hit_test.get("element_from_point_evaluated"):
        if hit_test.get("element_from_point_is_null"):
            return "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"
        if not hit_test.get("anchor_contains_element_from_point"):
            return "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"
        if hit_test.get("anchor_contains_element_from_point"):
            return "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    return "FAIL_CLOSED"


# ---------------------------------------------------------------------------
# Offline Mock Builders for Dry-Run
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
    simulated_position: int = 19,
    case: str = "below_viewport",
    backend_identity_set: Optional[set[tuple[str, str, Optional[str]]]] = None,
) -> dict[str, Any]:
    """Builds a simulated mounted DOM snapshot for offline dry-run testing."""
    if backend_identity_set is None:
        backend_identity_set, _ = build_canonical_backend_identity_set(build_mock_dry_run_backend_results())

    layout_vp = {
        "window_inner_width": 1280,
        "window_inner_height": 800,
        "document_client_width": 1280,
        "document_client_height": 800,
        "layout_viewport_width": 1280,
        "layout_viewport_height": 800,
    }

    visual_vp = {
        "visual_viewport_available": True,
        "visual_viewport_width": 1280.0,
        "visual_viewport_height": 800.0,
        "visual_viewport_offset_left": 0.0,
        "visual_viewport_offset_top": 0.0,
        "visual_viewport_scale": 1.0,
        "device_pixel_ratio": 1.0,
    }

    scroll_cont = {
        "scroll_container_found": True,
        "scroll_container_is_search_view": True,
        "scroll_container_is_document_scroller": False,
        "scroll_container_client_width": 600,
        "scroll_container_client_height": 700,
        "scroll_container_scroll_width": 600,
        "scroll_container_scroll_height": 2400,
        "scroll_container_scroll_left": 0,
        "scroll_container_scroll_top": 0,
        "scroll_container_rect_top": 50.0,
        "scroll_container_rect_bottom": 750.0,
        "scroll_container_rect_left": 200.0,
        "scroll_container_rect_right": 800.0,
    }

    records = []
    for i in range(1, 21):
        is_target = (i == simulated_position)
        cid = TARGET_CHANNEL_ID if is_target else "C042WS25QAX"
        mts = TARGET_MESSAGE_TS if is_target else f"1788000000.{i:06d}"
        tts = TARGET_THREAD_TS if is_target else f"1788000000.{i:06d}"
        rec = {
            "mounted_dom_position": i,
            "channel_id": cid,
            "message_ts": mts,
            "thread_ts": tts,
            "connected": True,
            "positive_geometry": True,
            "identity_parse_success": True,
            "is_thread_reply": True if is_target else True,
            "is_target": is_target,
        }
        row_ident = build_canonical_mounted_row_identity(rec)
        rec["row_current_query_membership"] = evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=row_ident,
            backend_identity_set=backend_identity_set,
        )
        records.append(rec)

    if case == "target_absent" or simulated_position < 1 or simulated_position > 20:
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
            "mounted_row_records": records,
            "target_row_geometry": None,
            "target_anchor_geometry": None,
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": scroll_cont,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM",
        }

    if case == "target_duplicate":
        return {
            "ok": True,
            "search_view_connected": True,
            "mounted_row_count": 20,
            "inspected_mounted_row_count": 20,
            "connected_row_count": 20,
            "positive_geometry_row_count": 20,
            "target_match_count": 2,
            "target_mounted_dom_position": None,
            "target_connected": None,
            "target_positive_geometry": None,
            "mounted_row_records": records,
            "target_row_geometry": None,
            "target_anchor_geometry": None,
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": scroll_cont,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM",
        }

    # Setup row geometry
    base_top = 50.0 + (simulated_position - 1) * 70.0
    row_geom = {
        "target_row_rect_top": base_top,
        "target_row_rect_bottom": base_top + 60.0,
        "target_row_rect_left": 200.0,
        "target_row_rect_right": 800.0,
        "target_row_rect_width": 600.0,
        "target_row_rect_height": 60.0,
        "target_row_center_x": 500.0,
        "target_row_center_y": base_top + 30.0,
        "target_row_center_inside_layout_viewport": (0 <= base_top + 30.0 < 800),
        "target_row_intersects_layout_viewport": not (base_top + 60.0 <= 0 or base_top >= 800),
        "target_row_fully_inside_layout_viewport": (base_top >= 0 and base_top + 60.0 <= 800),
        "target_row_center_inside_scroll_viewport": (50.0 <= base_top + 30.0 <= 750.0),
        "target_row_intersects_scroll_viewport": not (base_top + 60.0 <= 50.0 or base_top >= 750.0),
        "target_row_fully_inside_scroll_viewport": (base_top >= 50.0 and base_top + 60.0 <= 750.0),
    }

    if case == "anchor_absent":
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
            "mounted_row_records": records,
            "target_row_geometry": row_geom,
            "target_anchor_geometry": {
                "qualified_target_anchor_count": 0,
                "anchor_found": False,
                "anchor_unique": False,
            },
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": scroll_cont,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_ROW_FOUND_ANCHOR_NOT_FOUND",
        }

    if case == "anchor_duplicate":
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
            "mounted_row_records": records,
            "target_row_geometry": row_geom,
            "target_anchor_geometry": {
                "qualified_target_anchor_count": 2,
                "anchor_found": True,
                "anchor_unique": False,
            },
            "layout_viewport": layout_vp,
            "visual_viewport": visual_vp,
            "scroll_container": scroll_cont,
            "clipping": None,
            "hit_test": None,
            "geometry_classification": "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE",
        }

    # Setup anchor coordinates depending on case
    if case == "below_viewport":
        a_top = 1300.0
        a_left = 300.0
        a_w, a_h = 100.0, 20.0
        sc_in = False
    elif case == "above_viewport":
        a_top = -100.0
        a_left = 300.0
        a_w, a_h = 100.0, 20.0
        sc_in = False
    elif case == "left_of_viewport":
        a_top = 200.0
        a_left = -150.0
        a_w, a_h = 100.0, 20.0
        sc_in = False
    elif case == "right_of_viewport":
        a_top = 200.0
        a_left = 1400.0
        a_w, a_h = 100.0, 20.0
        sc_in = False
    elif case == "outside_scroll_viewport":
        a_top = 760.0  # inside layout vp (<800), but outside scroll vp (>750)
        a_left = 300.0
        a_w, a_h = 100.0, 20.0
        sc_in = False
    elif case == "clipped_by_ancestor":
        a_top = 200.0
        a_left = 300.0
        a_w, a_h = 100.0, 20.0
        sc_in = True
    else:
        # inside both viewports
        a_top = 200.0
        a_left = 300.0
        a_w, a_h = 100.0, 20.0
        sc_in = True

    cx = a_left + a_w / 2.0
    cy = a_top + a_h / 2.0
    in_layout = (0 <= cx < 1280 and 0 <= cy < 800)

    anchor_geom = {
        "qualified_target_anchor_count": 1,
        "anchor_found": True,
        "anchor_unique": True,
        "anchor_rect_top": a_top,
        "anchor_rect_bottom": a_top + a_h,
        "anchor_rect_left": a_left,
        "anchor_rect_right": a_left + a_w,
        "anchor_rect_width": a_w,
        "anchor_rect_height": a_h,
        "anchor_center_x": cx,
        "anchor_center_y": cy,
        "anchor_positive_geometry": True,
        "anchor_center_inside_layout_viewport": in_layout,
        "anchor_intersects_layout_viewport": not (a_top + a_h <= 0 or a_top >= 800 or a_left + a_w <= 0 or a_left >= 1280),
        "anchor_fully_inside_layout_viewport": (a_top >= 0 and a_top + a_h <= 800 and a_left >= 0 and a_left + a_w <= 1280),
        "anchor_center_inside_scroll_viewport": sc_in,
        "anchor_intersects_scroll_viewport": not (a_top + a_h <= 50.0 or a_top >= 750.0 or a_left + a_w <= 200.0 or a_left >= 800.0),
        "anchor_fully_inside_scroll_viewport": (a_top >= 50.0 and a_top + a_h <= 750.0 and a_left >= 200.0 and a_left + a_w <= 800.0),
    }

    clipping_ancestor_present = (case == "clipped_by_ancestor")
    anchor_inside_all_clipping = not (case == "clipped_by_ancestor")

    clipping = {
        "clipping_ancestor_present": clipping_ancestor_present,
        "anchor_center_inside_all_clipping_ancestors": anchor_inside_all_clipping,
    }

    hit_eval = in_layout
    hit_null = (case == "hit_test_null")
    hit_anchor = (case in ("hit_test_anchor", "visible_reachable"))
    hit_descendant = (case == "hit_test_descendant")
    anchor_contains = (hit_anchor or hit_descendant)

    hit_test = {
        "element_from_point_evaluated": hit_eval,
        "element_from_point_is_null": hit_null if hit_eval else None,
        "element_from_point_is_anchor": hit_anchor,
        "element_from_point_is_anchor_descendant": hit_descendant,
        "anchor_contains_element_from_point": anchor_contains,
        "element_from_point_contains_anchor": False,
    }

    snapshot = {
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
        "mounted_row_records": records,
        "target_row_geometry": row_geom,
        "target_anchor_geometry": anchor_geom,
        "layout_viewport": layout_vp,
        "visual_viewport": visual_vp,
        "scroll_container": scroll_cont,
        "clipping": clipping,
        "hit_test": hit_test,
    }

    snapshot["geometry_classification"] = classify_mounted_geometry_reachability(snapshot)
    return snapshot


# ---------------------------------------------------------------------------
# Dependency Hashes & Parity Audits
# ---------------------------------------------------------------------------

def get_file_sha256(path: Path) -> str:
    """Computes SHA-256 for a given path."""
    if not path.exists():
        return "MISSING"
    h = hashlib.sha256()
    h.update(path.read_bytes())
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
        "mdr1_protocol": REPO_ROOT / "artifacts" / "task11q4_p2a_l1_mdr1_protocol_freeze.json",
        "mdr1_marker": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1_attempt_started.json",
        "mdr1_live": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1_mounted_dom_residency.json",
        "mdr1r1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1r1.py",
        "mdr1r1_protocol": REPO_ROOT / "artifacts" / "task11q4_p2a_l1_mdr1r1_protocol_freeze.json",
        "mdr1r1_marker": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1r1_attempt_started.json",
        "mdr1r1_live": REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1r1_mounted_dom_residency.json",
    }
    return {k: get_file_sha256(v) for k, v in deps.items()}


def extract_func_ast(tree: ast.AST, func_name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def verify_search_telemetry_authority_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "last_telemetry":
            logger.error(f"MGR1 runner incorrectly accesses prohibited attribute last_telemetry at line {node.lineno}")
            return False

    harvest_fn_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "harvest_search_telemetry":
            harvest_fn_node = node
            break

    if not harvest_fn_node:
        logger.error("MGR1 runner missing required harvest_search_telemetry function")
        return False

    accesses_last_search_telemetry = False
    for node in ast.walk(harvest_fn_node):
        if isinstance(node, ast.Attribute) and node.attr == "last_search_telemetry":
            accesses_last_search_telemetry = True
            break

    if not accesses_last_search_telemetry:
        logger.error("harvest_search_telemetry in MGR1 does not access backend.last_search_telemetry")
        return False

    return True


def verify_search_exception_order_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mgr1_diagnostic")
    if not runner_fn:
        logger.error("run_mgr1_diagnostic function not found in MGR1 runner AST")
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
            exception_raise_lineno = node.lineno

    if not search_call_lineno or not harvest_call_lineno:
        logger.error("Missing search_messages or harvest_search_telemetry call in run_mgr1_diagnostic")
        return False

    if harvest_call_lineno < search_call_lineno:
        logger.error("harvest_search_telemetry called before search_messages in run_mgr1_diagnostic")
        return False

    return True


def verify_search_safety_gate_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    eval_fn = extract_func_ast(tree, "evaluate_search_safety_and_query_authority")
    if not eval_fn:
        logger.error("evaluate_search_safety_and_query_authority function missing")
        return False

    required_check_keys = {
        "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED",
        "QUERY_STATE_VERIFIED_BEFORE_SUBMIT",
        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED",
        "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH",
        "SLACKBOT_ACTION_INVOKED_BY_PESU",
        "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU",
        "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU",
    }

    constants_found = set()
    for node in ast.walk(eval_fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants_found.add(node.value)

    missing = required_check_keys - constants_found
    if missing:
        logger.error(f"Missing required search safety check keys in MGR1 authority evaluation: {missing}")
        return False

    return True


def verify_runtime_search_path_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mgr1_diagnostic")
    if not runner_fn:
        return False

    creates_backend = False
    calls_search = False
    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "WebViewSlackSearchBackend":
                creates_backend = True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "search_messages":
                calls_search = True

    return creates_backend and calls_search


def verify_host_dom_evaluation_api_parity(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_mgr1_diagnostic")
    if not runner_fn:
        return False

    uses_eval_js = False
    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "eval_js":
                uses_eval_js = True
                break

    return uses_eval_js


def verify_search_result_row_enumeration_parity() -> bool:
    return (
        SEARCH_VIEW_SELECTOR == PROD_SEARCH_VIEW_SELECTOR
        and SEARCH_RESULT_ROW_SELECTORS == PROD_SEARCH_RESULT_ROW_SELECTORS
    )


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    """Verifies that:
    1) JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT contains no executable scroll or click actions.
    2) Runner action contract has strict zero limits for interaction.
    """
    if "JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT" not in globals():
        logger.error("JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT missing from globals")
        return False

    js = globals()["JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT"]

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
        (r"\bPointerEvent\b", "PointerEvent"),
        (r"\bInputEvent\b", "InputEvent"),
        (r"\blocation\s*=", "location assignment"),
        (r"\blocation\.href\s*=", "location.href assignment"),
        (r"\bhistory\.push", "history.push"),
        (r"\bhistory\.replace", "history.replace"),
        (r"\.submit\s*\(", ".submit() invocation"),
        (r"\brequestSubmit\s*\(", "requestSubmit() invocation"),
        (r"\bfetch\s*\(", "fetch() invocation"),
        (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
        (r"\bWebSocket\b", "WebSocket"),
        (r"\btextContent\s*=", "textContent assignment"),
        (r"\binnerHTML\s*=", "innerHTML assignment"),
        (r"\.value\s*=", "value assignment"),
    ]

    for pat, label in forbidden_js_patterns:
        if re.search(pat, js):
            logger.error(f"Forbidden pattern found in MGR1 JS: {label} ({pat})")
            return False

    if MAX_GUARDED_ANCHOR_CLICKS != 0:
        return False
    if MAX_REPLY_BAR_CLICKS != 0:
        return False
    if MAX_RESULT_CARD_CLICKS != 0:
        return False
    if MAX_THREAD_EXPANSIONS != 0:
        return False
    if MAX_SCROLL_ACTIONS != 0:
        return False
    if MAX_KEYBOARD_EVENTS != 0:
        return False
    if MAX_COMPOSER_INTERACTIONS != 0:
        return False
    if MAX_WRITE_CAPABLE_ACTIONS != 0:
        return False
    if MAX_PROVIDER_CALLS != 0:
        return False
    if MAX_RETRIES != 0:
        return False
    if MAX_RESTORATIONS != 0:
        return False
    if MAX_BACK_NAVIGATIONS != 0:
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
        return f"UNKNOWN_{e}"


def check_uncommitted_diff() -> bool:
    try:
        res = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
        )
        return res.returncode != 0
    except Exception:
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
        "missing_from_closure": missing_from_closure,
        "stale_in_closure": stale_in_closure,
        "mismatched_hashes": mismatched,
        "loaded_files": loaded_map,
    }


def harvest_search_telemetry(
    backend: Optional[WebViewSlackSearchBackend],
    search_exc: Optional[Exception],
    report: dict[str, Any],
) -> None:
    """Harvests search safety telemetry exclusively from last_search_telemetry or exception details."""
    report.setdefault("safety_telemetry", {})
    report.setdefault("search_telemetry_extracted", False)

    if backend and hasattr(backend, "last_search_telemetry"):
        tel = backend.last_search_telemetry
        if tel:
            report["safety_telemetry"] = tel
            report["search_telemetry"] = tel
            report["search_telemetry_extracted"] = True
            return

    if search_exc and hasattr(search_exc, "details") and isinstance(search_exc.details, dict):
        det = search_exc.details
        if "search_telemetry" in det:
            report["safety_telemetry"] = det["search_telemetry"]
            report["search_telemetry"] = det["search_telemetry"]
            report["search_telemetry_extracted"] = True
            return
        elif "last_search_telemetry" in det:
            report["safety_telemetry"] = det["last_search_telemetry"]
            report["search_telemetry"] = det["last_search_telemetry"]
            report["search_telemetry_extracted"] = True
            return


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    """Authoritatively validates that search executed under strict safety constraints."""
    safety_tel = report.get("safety_telemetry") or {}
    checks = report.setdefault("checks", {})

    safe_action = bool(safety_tel.get("safe_search_action_selected", False))
    query_verified = bool(safety_tel.get("verified_query_state_before_submit", False))

    ft = safety_tel.get("forensic_transition", {})
    surface_matches = bool(ft.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))

    generic_used = bool(safety_tel.get("generic_default_onenter_used", False))
    slackbot_used = bool(safety_tel.get("slackbot_action_invoked_by_pesu", False))
    write_used = bool(safety_tel.get("write_capable_action_invoked_by_pesu", False))
    unsafe_used = bool(safety_tel.get("unsafe_non_search_action_invoked_by_pesu", False))

    checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = safe_action
    checks["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = query_verified
    checks["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = surface_matches
    checks["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = generic_used
    checks["SLACKBOT_ACTION_INVOKED_BY_PESU"] = slackbot_used
    checks["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = write_used
    checks["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = unsafe_used

    passed = (
        safe_action
        and query_verified
        and surface_matches
        and not generic_used
        and not slackbot_used
        and not write_used
        and not unsafe_used
    )
    reason = "PASS" if passed else "SEARCH_SAFETY_GATE_FAILED"
    return passed, reason


def sanitize_report_for_persistence(report: dict[str, Any]) -> dict[str, Any]:
    """Recursively purges raw Slack URLs/permalinks/message text while preserving all structural safety telemetry."""
    sanitized = copy.deepcopy(report)

    if "search_telemetry" in sanitized and isinstance(sanitized["search_telemetry"], dict):
        st = sanitized["search_telemetry"]
        if "raw_items_evaluated" in st and isinstance(st["raw_items_evaluated"], list):
            cleaned_items = []
            for item in st["raw_items_evaluated"]:
                if isinstance(item, dict):
                    plink = item.get("canonical_permalink")
                    pid = parse_slack_message_permalink(plink) if plink else None
                    cid = pid.channel_id if pid else None
                    mts = normalize_slack_ts(pid.message_ts) if pid and pid.message_ts else None
                    tts = normalize_slack_ts(pid.thread_ts) if pid and pid.thread_ts else None
                    cleaned_items.append({
                        "raw_index": item.get("raw_index"),
                        "channel_id": cid,
                        "message_ts": mts,
                        "thread_ts": tts,
                        "identity_parse_success": bool(cid and mts),
                        "thread_reply_detected": item.get("thread_reply_detected"),
                        "body_valid": item.get("body_valid"),
                        "ambiguous_fragment_count": item.get("ambiguous_fragment_count"),
                        "drop_reason": item.get("drop_reason"),
                    })
                else:
                    cleaned_items.append(item)
            st["raw_items_evaluated"] = cleaned_items

        if "forensic_transition" in st and isinstance(st["forensic_transition"], dict):
            ft = st["forensic_transition"]
            if "raw_result_identifiers" in ft and isinstance(ft["raw_result_identifiers"], list):
                cleaned_identifiers = []
                for ident in ft["raw_result_identifiers"]:
                    if isinstance(ident, str):
                        pid = parse_slack_message_permalink(ident)
                        if pid and pid.channel_id and pid.message_ts:
                            cleaned_identifiers.append(f"{pid.channel_id}:{normalize_slack_ts(pid.message_ts)}")
                        else:
                            cleaned_identifiers.append("UNKNOWN_IDENTIFIER")
                    else:
                        cleaned_identifiers.append(ident)
                ft["raw_result_identifiers"] = cleaned_identifiers

    return sanitized


def run_mgr1_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / MGR1",
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
            "search_submissions": 0,
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
        },
        "dom_telemetry": None,
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
    # PHASE 1: Pre-Execution Protocol & Frozen Baselines Verification
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

    report["checks"]["VRA1_IMMUTABLE_BASELINES_MATCHED"] = True
    report["checks"]["MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = verify_search_result_row_enumeration_parity()
    report["checks"]["MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["MDR1_HOST_DOM_EVALUATION_API_PARITY"] = verify_host_dom_evaluation_api_parity()
    report["checks"]["MDR1_STATIC_ZERO_INTERACTION_AUDIT"] = verify_static_zero_interaction_contract()

    if not all([
        report["checks"]["MDR1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"],
        report["checks"]["MDR1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"],
        report["checks"]["MDR1_HOST_DOM_EVALUATION_API_PARITY"],
        report["checks"]["MDR1_STATIC_ZERO_INTERACTION_AUDIT"],
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
        report["checks"]["atomic_dom_snapshot_executed"] = True

        mock_backend_results = build_mock_dry_run_backend_results()
        backend_identity_set, backend_stats = build_canonical_backend_identity_set(mock_backend_results)
        report["backend_identity_telemetry"] = backend_stats

        mock_dom = build_mock_dry_run_dom_snapshot(simulated_position=19, case="below_viewport", backend_identity_set=backend_identity_set)
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
            row_identity=CANONICAL_TARGET_IDENTITY,
            backend_identity_set=backend_identity_set,
        )
        report["target_row_current_query_membership"] = target_membership
        classification = classify_mounted_geometry_reachability(mock_dom)
        report["geometry_classification"] = classification

        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = CANONICAL_DRY_RUN_VERDICT
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
        report["error"] = f"Attempt marker file already exists at {ATTEMPT_STARTED_MARKER_FILE}. Diagnostic consumed."
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
        logger.error("🛑 Attempt marker already created concurrently.")
        return report

    backend: Optional[WebViewSlackSearchBackend] = None
    search_exception: Optional[Exception] = None
    search_results: list[SlackSearchResult] = []

    try:
        report["harness_phases"].append("CONNECTING_WEBVIEW")
        runtime_config = WebViewRuntimeConfig(
            headless=False,
            user_data_dir=str(DIRECT_SLACK_PROFILE_PATH),
        )
        backend = WebViewSlackSearchBackend(runtime_config=runtime_config)

        report["harness_phases"].append("SUBMITTING_SEARCH")
        report["live_action_inventory"]["search_submissions"] = 1

        search_results = backend.search_messages(TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)
        report["live_action_inventory"]["search_modal_observed"] = True

    except Exception as ex:
        search_exception = ex
        logger.warning(f"Search operation produced exception: {ex}")
    finally:
        harvest_search_telemetry(backend, search_exception, report)

    safety_passed, safety_reason = evaluate_search_safety_and_query_authority(report)
    report["checks"]["search_safety_gate"] = "PASS" if safety_passed else "FAIL"

    backend_identity_set, backend_stats = build_canonical_backend_identity_set(search_results)
    report["backend_identity_telemetry"] = backend_stats

    if not safety_passed:
        logger.error(f"🛑 Search safety gate failed: {safety_reason}")
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = f"SEARCH_SAFETY_GATE_FAILED_{safety_reason}"
        report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
        report["error"] = f"Search safety gate rejected execution: {safety_reason}"
        persist_live_artifact(report)
        return report

    if not backend or not backend._host:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HOST_UNAVAILABLE_AFTER_SEARCH"
        report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
        report["error"] = "PesuWebViewHost unavailable to execute pure-read DOM snapshot."
        persist_live_artifact(report)
        return report

    # -----------------------------------------------------------------------
    # PHASE 4: Execute Atomic Pure-Read Mounted Target Geometry Snapshot
    # -----------------------------------------------------------------------
    report["harness_phases"].append("ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT")
    report["live_action_inventory"]["pure_read_dom_extractions"] = 1
    report["live_action_inventory"]["read_only_dom_snapshots"] = 1

    try:
        dom_snapshot = backend._host.eval_js(
            JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT,
            SEARCH_VIEW_SELECTOR,
            SEARCH_RESULT_ROW_SELECTORS,
            MAX_MOUNTED_ROWS_TO_INSPECT,
            TARGET_CHANNEL_ID,
            TARGET_MESSAGE_TS,
            TARGET_THREAD_TS,
            FROZEN_ALLOWED_ORIGINS,
        )
        report["dom_telemetry"] = dom_snapshot
        report["checks"]["atomic_dom_snapshot_executed"] = True

        surface_query_matches = False
        safety_tel = report.get("safety_telemetry", {})
        if safety_tel:
            ft = safety_tel.get("forensic_transition", {})
            surface_query_matches = bool(ft.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))

        readiness_val = safety_tel.get("SEARCH_RESULT_READINESS", "UNKNOWN")
        search_view_conn = bool(dom_snapshot and dom_snapshot.get("search_view_connected", False))

        surface_correlated = evaluate_search_surface_current_query_correlation(
            search_view_connected=search_view_conn,
            surface_query_matches=surface_query_matches,
            search_readiness=readiness_val,
        )
        report["search_surface_current_query_correlated"] = surface_correlated

        target_row_conn = bool(dom_snapshot and dom_snapshot.get("target_connected", False))
        target_membership = evaluate_row_current_query_membership(
            row_connected=target_row_conn,
            surface_correlated=surface_correlated,
            row_identity=CANONICAL_TARGET_IDENTITY,
            backend_identity_set=backend_identity_set,
        )
        report["target_row_current_query_membership"] = target_membership

        for rec in (dom_snapshot.get("mounted_row_records") or []):
            rec_ident = build_canonical_mounted_row_identity(rec)
            rec["row_current_query_membership"] = evaluate_row_current_query_membership(
                row_connected=rec.get("connected", False),
                surface_correlated=surface_correlated,
                row_identity=rec_ident,
                backend_identity_set=backend_identity_set,
            )

        classification = classify_mounted_geometry_reachability(dom_snapshot)
        report["geometry_classification"] = classification
        report["diagnostic_verdict"] = classification
        report["verdict"] = "DIAGNOSTIC_COMPLETED"

    except Exception as snap_ex:
        logger.error(f"Error evaluating pure-read geometry snapshot: {snap_ex}")
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "SNAPSHOT_EXECUTION_EXCEPTION"
        report["geometry_classification"] = "MOUNTED_DOM_STATE_INSUFFICIENT"
        report["error"] = str(snap_ex)

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
    parser = argparse.ArgumentParser(description="MGR1: Mounted Target Geometry Reachability Diagnostic 1")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline dry-run test")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds")
    args = parser.parse_args()

    report = run_mgr1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print("\n" + "=" * 60)
    print("MGR1 DIAGNOSTIC EXECUTION SUMMARY")
    print("=" * 60)
    print(f"VERDICT:                  {report.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT:       {report.get('diagnostic_verdict')}")
    print(f"GEOMETRY_CLASSIFICATION:  {report.get('geometry_classification')}")
    print(f"ERROR:                    {report.get('error')}")
    print("=" * 60)

    if report.get("verdict") in ("DIAGNOSTIC_COMPLETED", CANONICAL_DRY_RUN_VERDICT):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
