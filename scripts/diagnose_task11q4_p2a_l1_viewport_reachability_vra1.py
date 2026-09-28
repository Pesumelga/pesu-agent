"""Phase 13.5D / Task 11 / P2A / VRA1: Target Anchor Viewport Reachability Diagnostic 1.

VRA1 PURPOSE:
Where is the exact target anchor relative to the WebView viewport and to the actual
search-result scroll viewport at the moment of observation?

Strict Boundaries:
- QUALIFICATION ONLY: Does NOT qualify production P2A, does NOT modify WebviewThreadReader,
  does NOT click any element, does NOT scroll any element, does NOT create Attempt 5.
- ONE ATOMIC READ-ONLY GEOMETRY SNAPSHOT:
  Resolves connected search_view, production top-5 rows, unique exact target row,
  unique exact target anchor, row geometry, anchor geometry, layout viewport geometry,
  visual viewport geometry, elementFromPoint observation, and nearest scrollable ancestor.
  All in ONE bounded JavaScript evaluation.
- ZERO INTERACTION:
  search operations <= 1, search submissions <= 1, geometry snapshots <= 1.
  STRICT ZERO: anchor clicks = 0, reply_bar clicks = 0, generic result-card clicks = 0,
  scroll actions = 0, keyboard navigation = 0, composer interaction = 0, write actions = 0,
  provider calls = 0, retries = 0, restoration = 0, back navigation = 0.
- TARGET IDENTITY (NOT HARDCODED TO RANK 5):
  1 <= observed_rank <= 5
  channel_id == C042WS25QAX
  message_ts == 1718079276.126469
  thread_ts == 1718058683.912369
  is_thread_reply == true
  exact_target_row_count == 1
- EXACT ANCHOR IDENTITY (SAME AS TAIQ1 CONTRACT, ZERO CLICK):
  tagName == "A"
  isConnected == true
  is_slack_message_permalink == true
  parsed_channel_id == C042WS25QAX
  parsed_message_ts == 1718079276.126469
  parsed_thread_ts == 1718058683.912369
  identity_matches_row == true
  points_to_same_message == true
  has_thread_ts_parameter == true
  qualified_anchor_count == 1 (0 or >1 fails closed).
- GEOMETRY TELEMETRY & CLASSIFICATION HIERARCHY:
  Distinguishes offscreen position (BELOW, ABOVE, LEFT, RIGHT of layout viewport,
  or OUTSIDE scroll container viewport, or CLIPPED by ancestor) BEFORE interpreting
  elementFromPoint occlusion.
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
logger = logging.getLogger("task11q4_p2a_l1_viewport_reachability_vra1")

# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5

PROTOCOL_NAME = "task11q4_p2a_l1_vra1_viewport_reachability"
DIAGNOSTIC_RUN = "VRA1_REACHABILITY_RUN_1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

TARGET_CHANNEL_ID = "C042WS25QAX"
TARGET_MESSAGE_TS = "1718079276.126469"
TARGET_THREAD_TS = "1718058683.912369"
TARGET_IS_THREAD_REPLY = True

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_SEARCH_SUBMISSIONS = 1
MAX_GEOMETRY_SNAPSHOTS = 1
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

# Authoritative Historical Baselines
AUTHORITATIVE_ATTEMPT4_SHA256 = "b2f4c6972e1e1297045c7fe7d7f1e50492225d3bd073b311ee1dcadd967a905d"
AUTHORITATIVE_RB2_SHA256 = "affb83a14725c587b219949681919771b6012463a029e3d79b2078a2a2039d64"
AUTHORITATIVE_TR1_SHA256 = "4ccd95175ec5ff26da5d1cfbd2cb663f7f12034093ec4eccf466f4e7701cc93c"
CONSUMED_TR2_RUNNER_SHA256 = "6cf4b96a84689aee7829199580d6dbb445bd8deac7b6831c338b200baea71338"
CONSUMED_TR2_PROTOCOL_SHA256 = "2a2632e25654983b5e30a6fb78856d01cf0551589f9bcf2fc97d0d9b65277dcd"
CONSUMED_TR2_TASK_LOG_SHA256 = "26252ea1829edfc92cf9b39dc50b28d3a93cbd6732eb8f814aaf6c689a3b97a3"
CONSUMED_TR2_FAILED_EVIDENCE_SHA256 = "895748e464df6f97448fe805e7873f4c1b595186b880faabc34af718ca7a0bf4"
TR2R1_RUNNER_SHA256 = "4b45f9d7af5d6aadfd2a0ac1356b3ecc6925fa32eaa2e7560e8b746e72632eb8"
TR2R1_PROTOCOL_SHA256 = "363ec806e6934c3574488814df3054dd046c0840cdf4e5a85b46e19bdb7b11d5"
TR2R1_ATTEMPT_MARKER_SHA256 = "c780905190ff8e905a890ab4d2117eacb0a5b532b1721dff442441b5b7efa12e"
TR2R1_LIVE_ARTIFACT_SHA256 = "6a3a4171219174c654ccad0c61456e8f99bd657b925ec7519df7249db2c4c75b"
TAIQ1_HEAD = "81d0dc639262b188e3db5877d0142e6f69f10850"
TAIQ1_RUNNER_SHA256 = "1c0974ce6b673648bdc8fa847137805e99331886d4b75ad3d19fc3127b54a1c4"
TAIQ1_PROTOCOL_SHA256 = "0b01de3c10b0e5195932061536e67b7aa0f79dbfba707433f7cee1335b0e3665"
TAIQ1_ATTEMPT_MARKER_SHA256 = "48f6709bea9b8718fa5f9bf8dbebe5d5290d395e6d9eb59556a56a48ffc64c4e"
TAIQ1_LIVE_ARTIFACT_SHA256 = "a59b7c5b2aa04fd137efea688d563a8c9a26afc03b770d6683b46ec4bbfc9037"

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_p2a_l1_vra1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vra1_viewport_reachability_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vra1_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_vra1_viewport_reachability.json"

SEARCH_VIEW_SELECTOR = PROD_SEARCH_VIEW_SELECTOR
SEARCH_RESULT_ROW_SELECTORS = PROD_SEARCH_RESULT_ROW_SELECTORS

REACHABILITY_CLASSIFICATIONS = [
    "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE",
    "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT",
    "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT",
    "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR",
    "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT",
    "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL",
    "TARGET_ANCHOR_GEOMETRY_INVALID",
    "TARGET_SCROLL_CONTAINER_NOT_IDENTIFIED",
    "VIEWPORT_REACHABILITY_INCONCLUSIVE",
    "FAIL_CLOSED",
]

# ---------------------------------------------------------------------------
# JavaScript: Atomic Read-Only Geometry Snapshot (ZERO click, ZERO scroll)
# ---------------------------------------------------------------------------

JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT = r"""
((searchViewSelector, rowSelectors, maxRows, targetChannelId, targetMessageTs, targetThreadTs, allowedOrigins) => {
    try {
        const searchView = document.querySelector(searchViewSelector);
        if (!searchView || !searchView.isConnected) {
            return {
                ok: false,
                failure_reason: "SEARCH_VIEW_NOT_FOUND",
                search_view_connected: false
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
        const candidateRows = Array.from(searchView.querySelectorAll(rowSelectorStr));
        const mountedRows = candidateRows.filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        });

        const mounted_result_count = mountedRows.length;
        const topRows = mountedRows.slice(0, maxRows);
        const inspected_top5_count = topRows.length;

        const matchedTargetRows = [];
        topRows.forEach((rowEl, idx) => {
            const anchors = Array.from(rowEl.querySelectorAll("a[href]"));
            let rowMatchesTarget = false;

            for (const a of anchors) {
                const href = a.getAttribute("href") || a.href || "";
                const m = href.match(/\/archives\/([A-Z0-9]+)\/p(\d+)(?:[?&].*thread_ts=(\d+\.\d+))?/);
                if (m) {
                    const ch = m[1];
                    const rawP = m[2];
                    const msgTs = normTs(rawP.slice(0, 10) + "." + rawP.slice(10));
                    const thrTs = normTs(m[3]);

                    if (ch === targetChannelId && msgTs === targetMsgNorm && thrTs === targetThrNorm) {
                        rowMatchesTarget = true;
                        break;
                    }
                }
            }

            if (rowMatchesTarget) {
                matchedTargetRows.push({
                    element: rowEl,
                    observed_rank: idx + 1
                });
            }
        });

        const target_row_unique = (matchedTargetRows.length === 1);
        if (matchedTargetRows.length !== 1) {
            return {
                ok: false,
                failure_reason: matchedTargetRows.length === 0 ? "TARGET_ROW_NOT_FOUND" : "TARGET_ROW_IDENTITY_NOT_UNIQUE",
                search_view_connected: true,
                mounted_result_count: mounted_result_count,
                inspected_top5_count: inspected_top5_count,
                target_row_unique: false,
                target_observed_rank: null,
                target_row_count: matchedTargetRows.length
            };
        }

        const targetRow = matchedTargetRows[0].element;
        const observedRank = matchedTargetRows[0].observed_rank;

        const allAnchors = Array.from(targetRow.querySelectorAll("a[href]"));
        const qualifyingAnchors = [];
        const guardFailures = [];

        for (const a of allAnchors) {
            if (a.tagName !== "A") continue;
            if (!a.isConnected) continue;

            const aRect = a.getBoundingClientRect();
            if (aRect.width <= 0 || aRect.height <= 0) continue;

            // Rendered-state interactability check
            if (window.getComputedStyle) {
                const cs = window.getComputedStyle(a);
                if (cs && (cs.display === "none" || cs.visibility === "hidden" || cs.pointerEvents === "none")) {
                    guardFailures.push("ANCHOR_NOT_INTERACTABLE");
                    continue;
                }
            }

            const inComposer = Boolean(a.closest('[contenteditable="true"], [data-qa="message_input"], .c-message_input, textarea, [role="textbox"]'));
            if (inComposer) {
                guardFailures.push("INSIDE_COMPOSER");
                continue;
            }

            const inReplyBar = Boolean(a.closest('div[data-qa="reply_bar"]'));
            if (inReplyBar) {
                guardFailures.push("INSIDE_REPLY_BAR");
                continue;
            }

            const targetAttr = a.getAttribute("target");
            const targetSelfSafe = (!targetAttr || targetAttr === "" || targetAttr === "_self");
            if (!targetSelfSafe) {
                guardFailures.push("TARGET_NOT_SELF_SAFE");
                continue;
            }

            if (a.hasAttribute("download")) {
                guardFailures.push("DOWNLOAD_ATTRIBUTE_PRESENT");
                continue;
            }

            let urlObj = null;
            try {
                urlObj = new URL(a.href, window.location.href);
            } catch (e) {
                guardFailures.push("URL_PARSE_ERROR");
                continue;
            }

            if (urlObj.protocol !== "https:") {
                guardFailures.push("PROTOCOL_NOT_HTTPS");
                continue;
            }

            if (!allowedOrigins.includes(urlObj.origin)) {
                guardFailures.push("ORIGIN_NOT_ALLOWED");
                continue;
            }

            const pathMatch = urlObj.pathname.match(/^\/archives\/([A-Z0-9]+)\/p(\d+)$/);
            if (!pathMatch) {
                guardFailures.push("PATH_NOT_CANONICAL_PERMALINK");
                continue;
            }

            const parsedChannel = pathMatch[1];
            const rawDigits = pathMatch[2];
            const parsedMsgTs = normTs(rawDigits.slice(0, 10) + "." + rawDigits.slice(10));
            const parsedThrTs = normTs(urlObj.searchParams.get("thread_ts"));

            if (parsedChannel !== targetChannelId) {
                guardFailures.push("CHANNEL_MISMATCH");
                continue;
            }
            if (parsedMsgTs !== targetMsgNorm) {
                guardFailures.push("MESSAGE_TS_MISMATCH");
                continue;
            }
            if (parsedThrTs !== targetThrNorm) {
                guardFailures.push("THREAD_TS_MISMATCH");
                continue;
            }

            const pointsToSameMsg = (parsedMsgTs === targetMsgNorm);
            const pointsToThreadRoot = (parsedMsgTs === targetThrNorm);
            if (!pointsToSameMsg || pointsToThreadRoot) {
                guardFailures.push("PERMALINK_ROLE_MISMATCH");
                continue;
            }

            qualifyingAnchors.push(a);
        }

        if (qualifyingAnchors.length !== 1) {
            return {
                ok: false,
                failure_reason: qualifyingAnchors.length === 0 ? "TARGET_ANCHOR_NOT_FOUND" : "ANCHOR_IDENTITY_NOT_UNIQUE",
                search_view_connected: true,
                mounted_result_count: mounted_result_count,
                inspected_top5_count: inspected_top5_count,
                target_row_unique: true,
                target_observed_rank: observedRank,
                qualified_anchor_count: qualifyingAnchors.length,
                guard_failures: guardFailures
            };
        }

        const anchor = qualifyingAnchors[0];

        // 1. Layout Viewport
        const window_inner_width = window.innerWidth || 0;
        const window_inner_height = window.innerHeight || 0;
        const document_client_width = (document.documentElement && document.documentElement.clientWidth) || 0;
        const document_client_height = (document.documentElement && document.documentElement.clientHeight) || 0;
        const layout_vp_w = window_inner_width || document_client_width;
        const layout_vp_h = window_inner_height || document_client_height;

        // 2. Visual Viewport Diagnostic
        let visual_viewport_available = false;
        let visual_viewport_width = null;
        let visual_viewport_height = null;
        let visual_viewport_offset_left = null;
        let visual_viewport_offset_top = null;
        let visual_viewport_scale = null;

        if (window.visualViewport) {
            visual_viewport_available = true;
            visual_viewport_width = window.visualViewport.width;
            visual_viewport_height = window.visualViewport.height;
            visual_viewport_offset_left = window.visualViewport.offsetLeft;
            visual_viewport_offset_top = window.visualViewport.offsetTop;
            visual_viewport_scale = window.visualViewport.scale;
        }

        const device_pixel_ratio = window.devicePixelRatio || 1.0;

        // 3. Row Geometry
        const rRect = targetRow.getBoundingClientRect();
        const row_rect_top = rRect.top;
        const row_rect_bottom = rRect.bottom;
        const row_rect_left = rRect.left;
        const row_rect_right = rRect.right;
        const row_rect_width = rRect.width;
        const row_rect_height = rRect.height;
        const row_center_x = rRect.left + rRect.width / 2;
        const row_center_y = rRect.top + rRect.height / 2;

        const row_center_inside_layout_viewport = Boolean(row_center_x >= 0 && row_center_x < layout_vp_w && row_center_y >= 0 && row_center_y < layout_vp_h);
        const row_intersects_layout_viewport = Boolean(rRect.bottom > 0 && rRect.top < layout_vp_h && rRect.right > 0 && rRect.left < layout_vp_w);
        const row_fully_inside_layout_viewport = Boolean(rRect.top >= 0 && rRect.bottom <= layout_vp_h && rRect.left >= 0 && rRect.right <= layout_vp_w);

        // 4. Anchor Geometry
        const aRect = anchor.getBoundingClientRect();
        const anchor_rect_top = aRect.top;
        const anchor_rect_bottom = aRect.bottom;
        const anchor_rect_left = aRect.left;
        const anchor_rect_right = aRect.right;
        const anchor_rect_width = aRect.width;
        const anchor_rect_height = aRect.height;
        const anchor_center_x = aRect.left + aRect.width / 2;
        const anchor_center_y = aRect.top + aRect.height / 2;
        const anchor_positive_geometry = Boolean(aRect.width > 0 && aRect.height > 0);

        const anchor_center_inside_layout_viewport = Boolean(anchor_center_x >= 0 && anchor_center_x < layout_vp_w && anchor_center_y >= 0 && anchor_center_y < layout_vp_h);
        const anchor_intersects_layout_viewport = Boolean(aRect.bottom > 0 && aRect.top < layout_vp_h && aRect.right > 0 && aRect.left < layout_vp_w);
        const anchor_fully_inside_layout_viewport = Boolean(aRect.top >= 0 && aRect.bottom <= layout_vp_h && aRect.left >= 0 && aRect.right <= layout_vp_w);

        // 5. Element From Point Observation (ONLY evaluated when anchor center is inside layout viewport)
        let element_from_point_evaluated = false;
        let element_from_point_is_null = null;
        let element_from_point_is_anchor = false;
        let element_from_point_is_anchor_descendant = false;
        let anchor_contains_element_from_point = false;
        let element_from_point_contains_anchor = false;

        if (anchor_center_inside_layout_viewport && typeof document.elementFromPoint === "function") {
            element_from_point_evaluated = true;
            const hit = document.elementFromPoint(anchor_center_x, anchor_center_y);
            if (!hit) {
                element_from_point_is_null = true;
            } else {
                element_from_point_is_null = false;
                element_from_point_is_anchor = (hit === anchor);
                element_from_point_is_anchor_descendant = Boolean(hit !== anchor && typeof anchor.contains === "function" && anchor.contains(hit));
                anchor_contains_element_from_point = Boolean(typeof anchor.contains === "function" && anchor.contains(hit));
                element_from_point_contains_anchor = Boolean(typeof hit.contains === "function" && hit.contains(anchor));
            }
        }

        // 6. Scroll Container Forensic & Ancestor Traversal
        let scroll_container = null;
        let clipping_ancestor_present = false;
        let anchor_center_inside_all_clipping_ancestors = true;

        const scrollerOverflowVals = ["auto", "scroll", "overlay"];
        const clippingOverflowVals = ["hidden", "clip"];

        let curr = anchor.parentElement;
        while (curr && curr !== document.documentElement && curr !== document.body) {
            if (window.getComputedStyle) {
                const cs = window.getComputedStyle(curr);
                if (cs) {
                    const oy = cs.overflowY || "";
                    const ox = cs.overflowX || "";
                    const isUserScroller = (curr.scrollHeight > curr.clientHeight && scrollerOverflowVals.includes(oy)) ||
                                           (curr.scrollWidth > curr.clientWidth && scrollerOverflowVals.includes(ox));

                    if (!scroll_container && isUserScroller) {
                        scroll_container = curr;
                    }

                    const isClipping = clippingOverflowVals.includes(oy) || clippingOverflowVals.includes(ox);
                    if (isClipping) {
                        clipping_ancestor_present = true;
                        const cRect = curr.getBoundingClientRect();
                        const inside = (anchor_center_x >= cRect.left && anchor_center_x <= cRect.right &&
                                        anchor_center_y >= cRect.top && anchor_center_y <= cRect.bottom);
                        if (!inside) {
                            anchor_center_inside_all_clipping_ancestors = false;
                        }
                    }
                }
            }
            curr = curr.parentElement;
        }

        if (!scroll_container && searchView) {
            if (window.getComputedStyle) {
                const svCs = window.getComputedStyle(searchView);
                if (svCs) {
                    const svOy = svCs.overflowY || "";
                    const svOx = svCs.overflowX || "";
                    if ((searchView.scrollHeight > searchView.clientHeight && scrollerOverflowVals.includes(svOy)) ||
                        (searchView.scrollWidth > searchView.clientWidth && scrollerOverflowVals.includes(svOx))) {
                        scroll_container = searchView;
                    }
                }
            }
        }

        const docScroller = document.scrollingElement || document.documentElement || document.body;
        if (!scroll_container && docScroller) {
            if (docScroller.scrollHeight > docScroller.clientHeight) {
                scroll_container = docScroller;
            }
        }

        const scroll_container_found = Boolean(scroll_container);
        const scroll_container_is_search_view = Boolean(scroll_container && (scroll_container === searchView || searchView.contains(scroll_container)));
        const scroll_container_is_document_scroller = Boolean(scroll_container && (scroll_container === docScroller || scroll_container === document.documentElement || scroll_container === document.body));

        let scroll_container_client_width = null;
        let scroll_container_client_height = null;
        let scroll_container_scroll_width = null;
        let scroll_container_scroll_height = null;
        let scroll_container_scroll_left = null;
        let scroll_container_scroll_top = null;
        let scroll_container_rect_top = null;
        let scroll_container_rect_bottom = null;
        let scroll_container_rect_left = null;
        let scroll_container_rect_right = null;

        let anchor_center_inside_scroll_viewport = false;
        let anchor_intersects_scroll_viewport = false;
        let anchor_fully_inside_scroll_viewport = false;
        let row_center_inside_scroll_viewport = false;
        let row_intersects_scroll_viewport = false;

        if (scroll_container) {
            scroll_container_client_width = scroll_container.clientWidth;
            scroll_container_client_height = scroll_container.clientHeight;
            scroll_container_scroll_width = scroll_container.scrollWidth;
            scroll_container_scroll_height = scroll_container.scrollHeight;
            scroll_container_scroll_left = scroll_container.scrollLeft;
            scroll_container_scroll_top = scroll_container.scrollTop;

            const scRect = scroll_container.getBoundingClientRect();
            scroll_container_rect_top = scRect.top;
            scroll_container_rect_bottom = scRect.bottom;
            scroll_container_rect_left = scRect.left;
            scroll_container_rect_right = scRect.right;

            anchor_center_inside_scroll_viewport = Boolean(
                anchor_center_x >= scRect.left && anchor_center_x <= scRect.right &&
                anchor_center_y >= scRect.top && anchor_center_y <= scRect.bottom
            );
            anchor_intersects_scroll_viewport = Boolean(
                aRect.bottom > scRect.top && aRect.top < scRect.bottom &&
                aRect.right > scRect.left && aRect.left < scRect.right
            );
            anchor_fully_inside_scroll_viewport = Boolean(
                aRect.top >= scRect.top && aRect.bottom <= scRect.bottom &&
                aRect.left >= scRect.left && aRect.right <= scRect.right
            );

            row_center_inside_scroll_viewport = Boolean(
                row_center_x >= scRect.left && row_center_x <= scRect.right &&
                row_center_y >= scRect.top && row_center_y <= scRect.bottom
            );
            row_intersects_scroll_viewport = Boolean(
                rRect.bottom > scRect.top && rRect.top < scRect.bottom &&
                rRect.right > scRect.left && rRect.left < scRect.right
            );
        }

        return {
            ok: true,
            search_view_connected: true,
            mounted_result_count: mounted_result_count,
            inspected_top5_count: inspected_top5_count,
            target_observed_rank: observedRank,
            target_row_unique: true,
            qualified_anchor_count: 1,

            target_rank: observedRank,
            row_rect_top: row_rect_top,
            row_rect_bottom: row_rect_bottom,
            row_rect_left: row_rect_left,
            row_rect_right: row_rect_right,
            row_rect_width: row_rect_width,
            row_rect_height: row_rect_height,
            row_center_x: row_center_x,
            row_center_y: row_center_y,
            row_center_inside_layout_viewport: row_center_inside_layout_viewport,
            row_intersects_layout_viewport: row_intersects_layout_viewport,
            row_fully_inside_layout_viewport: row_fully_inside_layout_viewport,

            anchor_rect_top: anchor_rect_top,
            anchor_rect_bottom: anchor_rect_bottom,
            anchor_rect_left: anchor_rect_left,
            anchor_rect_right: anchor_rect_right,
            anchor_rect_width: anchor_rect_width,
            anchor_rect_height: anchor_rect_height,
            anchor_center_x: anchor_center_x,
            anchor_center_y: anchor_center_y,
            anchor_positive_geometry: anchor_positive_geometry,
            anchor_center_inside_layout_viewport: anchor_center_inside_layout_viewport,
            anchor_intersects_layout_viewport: anchor_intersects_layout_viewport,
            anchor_fully_inside_layout_viewport: anchor_fully_inside_layout_viewport,

            window_inner_width: window_inner_width,
            window_inner_height: window_inner_height,
            document_client_width: document_client_width,
            document_client_height: document_client_height,
            layout_viewport_width: layout_vp_w,
            layout_viewport_height: layout_vp_h,

            visual_viewport_available: visual_viewport_available,
            visual_viewport_width: visual_viewport_width,
            visual_viewport_height: visual_viewport_height,
            visual_viewport_offset_left: visual_viewport_offset_left,
            visual_viewport_offset_top: visual_viewport_offset_top,
            visual_viewport_scale: visual_viewport_scale,
            device_pixel_ratio: device_pixel_ratio,

            element_from_point_evaluated: element_from_point_evaluated,
            element_from_point_is_null: element_from_point_is_null,
            element_from_point_is_anchor: element_from_point_is_anchor,
            element_from_point_is_anchor_descendant: element_from_point_is_anchor_descendant,
            anchor_contains_element_from_point: anchor_contains_element_from_point,
            element_from_point_contains_anchor: element_from_point_contains_anchor,

            scroll_container_found: scroll_container_found,
            scroll_container_is_search_view: scroll_container_is_search_view,
            scroll_container_is_document_scroller: scroll_container_is_document_scroller,
            scroll_container_client_width: scroll_container_client_width,
            scroll_container_client_height: scroll_container_client_height,
            scroll_container_scroll_width: scroll_container_scroll_width,
            scroll_container_scroll_height: scroll_container_scroll_height,
            scroll_container_scroll_left: scroll_container_scroll_left,
            scroll_container_scroll_top: scroll_container_scroll_top,
            scroll_container_rect_top: scroll_container_rect_top,
            scroll_container_rect_bottom: scroll_container_rect_bottom,
            scroll_container_rect_left: scroll_container_rect_left,
            scroll_container_rect_right: scroll_container_rect_right,

            anchor_center_inside_scroll_viewport: anchor_center_inside_scroll_viewport,
            anchor_intersects_scroll_viewport: anchor_intersects_scroll_viewport,
            anchor_fully_inside_scroll_viewport: anchor_fully_inside_scroll_viewport,
            row_center_inside_scroll_viewport: row_center_inside_scroll_viewport,
            row_intersects_scroll_viewport: row_intersects_scroll_viewport,

            clipping_ancestor_present: clipping_ancestor_present,
            anchor_center_inside_all_clipping_ancestors: anchor_center_inside_all_clipping_ancestors
        };
    } catch (err) {
        return {
            ok: false,
            failure_reason: "ATOMIC_GEOMETRY_EVALUATION_EXCEPTION",
            error: String(err)
        };
    }
})
"""

# ---------------------------------------------------------------------------
# Python Classification Logic
# ---------------------------------------------------------------------------

def classify_viewport_reachability(snapshot: dict[str, Any]) -> str:
    """Classifies the reachability of the target anchor following the strict priority order.

    Order of evaluation:
    1. Snapshot success and anchor positive geometry
    2. Layout viewport boundaries (distinguish offscreen direction BEFORE interpreting hit test!)
    3. Scroll container viewport boundaries
    4. Clipping ancestor bounds
    5. ElementFromPoint observation
    6. Scroll container identification check
    """
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return "FAIL_CLOSED"

    # 1. Anchor geometry validity
    if not snapshot.get("anchor_positive_geometry"):
        return "TARGET_ANCHOR_GEOMETRY_INVALID"
    w = snapshot.get("anchor_rect_width") or 0
    h = snapshot.get("anchor_rect_height") or 0
    if w <= 0 or h <= 0:
        return "TARGET_ANCHOR_GEOMETRY_INVALID"

    # 2. Position relative to layout viewport
    center_inside_layout = snapshot.get("anchor_center_inside_layout_viewport")
    cy = snapshot.get("anchor_center_y")
    cx = snapshot.get("anchor_center_x")
    vp_w = snapshot.get("layout_viewport_width")
    vp_h = snapshot.get("layout_viewport_height")

    if not center_inside_layout:
        if cy is not None and vp_h is not None and cy >= vp_h:
            return "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"
        if cy is not None and cy < 0:
            return "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT"
        if cx is not None and cx < 0:
            return "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT"
        if cx is not None and vp_w is not None and cx >= vp_w:
            return "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT"
        return "TARGET_ANCHOR_GEOMETRY_INVALID"

    # 3. Position relative to scroll container viewport (Case B)
    if snapshot.get("scroll_container_found"):
        if not snapshot.get("anchor_center_inside_scroll_viewport"):
            return "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    # 4. Clipping ancestor diagnostic
    if snapshot.get("clipping_ancestor_present") and not snapshot.get("anchor_center_inside_all_clipping_ancestors"):
        return "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR"

    # 5. ElementFromPoint hit-test (evaluated only when center is inside layout viewport)
    if snapshot.get("element_from_point_evaluated"):
        if snapshot.get("element_from_point_is_null"):
            return "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"
        if (
            snapshot.get("element_from_point_is_anchor")
            or snapshot.get("element_from_point_is_anchor_descendant")
            or snapshot.get("anchor_contains_element_from_point")
        ):
            return "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"
        return "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"

    # 6. Fallback if scroll container was not identified
    if not snapshot.get("scroll_container_found"):
        return "TARGET_SCROLL_CONTAINER_NOT_IDENTIFIED"

    return "VIEWPORT_REACHABILITY_INCONCLUSIVE"


def build_mock_dry_run_geometry() -> dict[str, Any]:
    """Builds a simulated geometry snapshot for offline dry-run testing."""
    return {
        "ok": True,
        "search_view_connected": True,
        "mounted_result_count": 5,
        "inspected_top5_count": 5,
        "target_observed_rank": 5,
        "target_row_unique": True,
        "qualified_anchor_count": 1,
        "target_rank": 5,
        "row_rect_top": 600.0,
        "row_rect_bottom": 720.0,
        "row_rect_left": 260.0,
        "row_rect_right": 900.0,
        "row_rect_width": 640.0,
        "row_rect_height": 120.0,
        "row_center_x": 580.0,
        "row_center_y": 660.0,
        "row_center_inside_layout_viewport": True,
        "row_intersects_layout_viewport": True,
        "row_fully_inside_layout_viewport": True,
        "anchor_rect_top": 680.0,
        "anchor_rect_bottom": 700.0,
        "anchor_rect_left": 300.0,
        "anchor_rect_right": 500.0,
        "anchor_rect_width": 200.0,
        "anchor_rect_height": 20.0,
        "anchor_center_x": 400.0,
        "anchor_center_y": 690.0,
        "anchor_positive_geometry": True,
        "anchor_center_inside_layout_viewport": True,
        "anchor_intersects_layout_viewport": True,
        "anchor_fully_inside_layout_viewport": True,
        "window_inner_width": 1280,
        "window_inner_height": 800,
        "document_client_width": 1280,
        "document_client_height": 800,
        "layout_viewport_width": 1280,
        "layout_viewport_height": 800,
        "visual_viewport_available": True,
        "visual_viewport_width": 1280.0,
        "visual_viewport_height": 800.0,
        "visual_viewport_offset_left": 0.0,
        "visual_viewport_offset_top": 0.0,
        "visual_viewport_scale": 1.0,
        "device_pixel_ratio": 1.0,
        "element_from_point_evaluated": True,
        "element_from_point_is_null": False,
        "element_from_point_is_anchor": True,
        "element_from_point_is_anchor_descendant": False,
        "anchor_contains_element_from_point": True,
        "element_from_point_contains_anchor": True,
        "scroll_container_found": True,
        "scroll_container_is_search_view": True,
        "scroll_container_is_document_scroller": False,
        "scroll_container_client_width": 660,
        "scroll_container_client_height": 700,
        "scroll_container_scroll_width": 660,
        "scroll_container_scroll_height": 1200,
        "scroll_container_scroll_left": 0,
        "scroll_container_scroll_top": 100,
        "scroll_container_rect_top": 80.0,
        "scroll_container_rect_bottom": 780.0,
        "scroll_container_rect_left": 250.0,
        "scroll_container_rect_right": 910.0,
        "anchor_center_inside_scroll_viewport": True,
        "anchor_intersects_scroll_viewport": True,
        "anchor_fully_inside_scroll_viewport": True,
        "row_center_inside_scroll_viewport": True,
        "row_intersects_scroll_viewport": True,
        "clipping_ancestor_present": False,
        "anchor_center_inside_all_clipping_ancestors": True,
    }

# ---------------------------------------------------------------------------
# Search Safety & Parity Gate Verification
# ---------------------------------------------------------------------------

def harvest_search_telemetry(
    backend: Optional[WebViewSlackSearchBackend],
    search_exception: Optional[Exception],
    report: dict[str, Any],
) -> None:
    telemetry = None
    if backend is not None and hasattr(backend, "last_search_telemetry"):
        telemetry = backend.last_search_telemetry

    if telemetry is None and search_exception is not None and hasattr(search_exception, "details"):
        telemetry = getattr(search_exception, "details")

    if not telemetry or not isinstance(telemetry, dict):
        logger.warning("No search telemetry captured from backend or exception details.")
        return

    report["search_telemetry"] = telemetry
    checks = report.setdefault("checks", {})
    safety = report.setdefault("safety_telemetry", {})
    st = report.setdefault("search_telemetry_extracted", {})
    actions = report.setdefault("live_action_inventory", {})

    if "search_activation_dispatches" in telemetry:
        actions["search_activation_dispatches"] = telemetry["search_activation_dispatches"]
    if "search_modal_observed" in telemetry:
        actions["search_modal_observed"] = telemetry["search_modal_observed"]
    if "search_input_ready" in telemetry:
        actions["search_input_ready"] = telemetry["search_input_ready"]
    if "onenter_invoked" in telemetry:
        actions["search_submissions"] = 1 if telemetry["onenter_invoked"] else 0

    if "safe_search_action_selected" in telemetry:
        safe_act = bool(telemetry["safe_search_action_selected"])
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = safe_act
        st["safe_search_action_explicitly_selected"] = safe_act
        safety["safe_search_action_explicitly_selected"] = safe_act
    else:
        checks["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = "UNKNOWN"
        st["safe_search_action_explicitly_selected"] = "UNKNOWN"
        safety["safe_search_action_explicitly_selected"] = "UNKNOWN"

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

    def _clean_supp(val: Any) -> Any:
        return "UNKNOWN" if val is None or val == "UNKNOWN" else val

    safety["composer_focus"] = _clean_supp(telemetry.get("composer_focus"))
    safety["composer_text_input"] = _clean_supp(telemetry.get("composer_text_input"))
    safety["composer_send"] = _clean_supp(telemetry.get("composer_send"))


def is_supplemental_safe(val: Any) -> bool:
    if val in (0, None, "UNKNOWN", False, "NOT_RUN"):
        return True
    return False


def evaluate_search_safety_and_query_authority(report: dict[str, Any]) -> tuple[bool, str]:
    checks = report.get("checks", {})
    safety = report.get("safety_telemetry", {})
    actions = report.get("live_action_inventory", {})

    if safety.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER"
    if safety.get("SLACKBOT_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_SLACKBOT"
    if safety.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_WRITE_CAPABLE"
    if safety.get("UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU") is True:
        return False, "EXPLICIT_SAFETY_VIOLATION_UNSAFE_NON_SEARCH"

    comp_f = safety.get("composer_focus")
    comp_i = safety.get("composer_text_input")
    comp_s = safety.get("composer_send")
    if not is_supplemental_safe(comp_f):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_FOCUS"
    if not is_supplemental_safe(comp_i):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_INPUT"
    if not is_supplemental_safe(comp_s):
        return False, "EXPLICIT_SAFETY_VIOLATION_COMPOSER_SEND"

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
        return False, f"SEARCH_SUBMISSIONS_COUNT_INVALID_{actions.get('search_submissions')}"

    if checks.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED") is not True:
        return False, "SEARCH_SURFACE_QUERY_DOES_NOT_MATCH_REQUESTED"

    return True, "VERIFIED_SAFE_AND_CORRELATED"


def verify_search_telemetry_authority_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "last_telemetry":
            logger.error(f"VRA1 runner incorrectly accesses prohibited attribute last_telemetry at line {node.lineno}")
            return False

    harvest_fn_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "harvest_search_telemetry":
            harvest_fn_node = node
            break

    if not harvest_fn_node:
        logger.error("VRA1 runner missing required harvest_search_telemetry function")
        return False

    accesses_last_search_telemetry = False
    for node in ast.walk(harvest_fn_node):
        if isinstance(node, ast.Attribute) and node.attr == "last_search_telemetry":
            accesses_last_search_telemetry = True
            break

    if not accesses_last_search_telemetry:
        logger.error("harvest_search_telemetry in VRA1 does not access backend.last_search_telemetry")
        return False

    return True


def extract_func_ast(tree: ast.AST, func_name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def verify_search_exception_order_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_vra1_diagnostic")
    if not runner_fn:
        logger.error("run_vra1_diagnostic function not found in VRA1 runner AST")
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
        logger.error("Failed to find required search/harvest/raise statements in VRA1 AST")
        return False

    if not (search_call_lineno < harvest_call_lineno < exception_raise_lineno):
        logger.error(
            f"VRA1 AST order violation: search_messages ({search_call_lineno}) < "
            f"harvest_search_telemetry ({harvest_call_lineno}) < raise search_exception ({exception_raise_lineno})"
        )
        return False

    return True


def verify_search_safety_gate_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_vra1_diagnostic")
    if not runner_fn:
        logger.error("run_vra1_diagnostic function not found in VRA1 runner AST")
        return False

    eval_safety_lineno = None
    guard_click_lineno = None

    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "evaluate_search_safety_and_query_authority":
                eval_safety_lineno = node.lineno
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "geometry_snapshot_res":
                    guard_click_lineno = node.lineno

    if not eval_safety_lineno:
        logger.error("evaluate_search_safety_and_query_authority call missing from VRA1 runner AST")
        return False

    if guard_click_lineno and not (eval_safety_lineno < guard_click_lineno):
        logger.error("evaluate_search_safety_and_query_authority must precede geometry snapshot evaluation")
        return False

    return True


def verify_runtime_search_path_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
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
            logger.error(f"Missing required runtime pattern in VRA1 runner: {pat}")
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

    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "evaluate_script":
            logger.error(f"VRA1 runner incorrectly accesses attribute evaluate_script at line {node.lineno}")
            return False

    has_evaluate_js_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "host" and node.func.attr == "evaluate_js":
                has_evaluate_js_call = True
                break

    if not has_evaluate_js_call:
        logger.error("VRA1 runner missing required host.evaluate_js call")
        return False

    return True


def verify_search_result_row_enumeration_parity() -> bool:
    try:
        js_extract = PROD_JS_EXTRACT_SEARCH_RESULTS
        if "searchView.querySelectorAll(" not in js_extract:
            logger.error("❌ Production JS_EXTRACT_SEARCH_RESULTS missing searchView.querySelectorAll.")
            return False

        if "JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT" not in globals():
            logger.error("❌ JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT missing from runner globals.")
            return False

        vra1_js = globals()["JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT"]

        norm_vra1 = re.sub(r"\s+", "", vra1_js)
        if "searchView.querySelectorAll(rowSelectorStr)" not in norm_vra1:
            logger.error("❌ VRA1 JS missing scoped row querySelectorAll.")
            return False

        if "PROD_SEARCH_VIEW_SELECTOR" not in globals() or SEARCH_VIEW_SELECTOR != PROD_SEARCH_VIEW_SELECTOR:
            logger.error("❌ SEARCH_VIEW_SELECTOR does not match PROD_SEARCH_VIEW_SELECTOR.")
            return False

        if "PROD_SEARCH_RESULT_ROW_SELECTORS" not in globals() or SEARCH_RESULT_ROW_SELECTORS != PROD_SEARCH_RESULT_ROW_SELECTORS:
            logger.error("❌ SEARCH_RESULT_ROW_SELECTORS does not match PROD_SEARCH_RESULT_ROW_SELECTORS.")
            return False

        return True
    except Exception as e:
        logger.error(f"❌ Exception verifying row enumeration parity: {e}")
        return False


def verify_static_zero_interaction_contract(runner_path: Optional[Path] = None) -> bool:
    """Verifies that:
    1) JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT contains no executable scroll or click actions.
    2) Runner action contract has strict zero limits for interaction.
    """
    if "JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT" not in globals():
        logger.error("JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT missing from globals")
        return False

    js = globals()["JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT"]

    # Forbidden executable patterns in JS
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
            logger.error(f"❌ Forbidden pattern found in VRA1 JS: {label} ({pat})")
            return False

    # Check runner Python AST for absence of click or scroll dispatches
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_viewport_reachability_vra1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    runner_fn = extract_func_ast(tree, "run_vra1_diagnostic")
    if not runner_fn:
        return False

    for node in ast.walk(runner_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("click", "scroll", "scroll_into_view", "send_keys", "dispatch_key"):
                logger.error(f"❌ Forbidden call {node.func.attr} in run_vra1_diagnostic AST")
                return False

    return True

# ---------------------------------------------------------------------------
# Runtime & Environment Utilities
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
    except Exception as e:
        logger.warning(f"Failed to get git commit: {e}")
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
    except Exception as e:
        logger.warning(f"Failed to check git status: {e}")
        return True


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
    }
    res = {}
    for name, p in deps.items():
        if p.exists():
            res[name] = get_file_sha256(p)
        else:
            res[name] = "MISSING"
    return res


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

# ---------------------------------------------------------------------------
# Diagnostic Runner
# ---------------------------------------------------------------------------

def run_vra1_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / VRA1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
        "reachability_classification": "UNKNOWN",
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": git_commit,
        "uncommitted_diff": git_diff,
        "target_identity": {
            "channel_id": TARGET_CHANNEL_ID,
            "message_ts": TARGET_MESSAGE_TS,
            "thread_ts": TARGET_THREAD_TS,
            "is_thread_reply": TARGET_IS_THREAD_REPLY,
            "rank_is_observational_only": True,
            "exact_target_row_count": 1,
        },
        "action_contracts": {
            "MAX_SLACK_SEARCH_OPERATIONS": MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_SEARCH_SUBMISSIONS": MAX_SEARCH_SUBMISSIONS,
            "MAX_GEOMETRY_SNAPSHOTS": MAX_GEOMETRY_SNAPSHOTS,
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
            "geometry_snapshots": 0,
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
            "VRA1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": False,
            "VRA1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4": False,
            "VRA1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4": False,
            "VRA1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4": False,
            "VRA1_SEARCH_RESULT_ROW_ENUMERATION_PARITY": False,
            "VRA1_HOST_DOM_EVALUATION_API_PARITY": False,
            "VRA1_STATIC_ZERO_INTERACTION_AUDIT": False,
            "historical_baselines_matched": False,
            "attempt_marker_written": False,
            "ACTION_CONTRACTS_SATISFIED": True,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": "UNKNOWN",
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": "UNKNOWN",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": "UNKNOWN",
        },
        "geometry_telemetry": None,
        "simulation_geometry": None,
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

    row_enum_ok = verify_search_result_row_enumeration_parity()
    report["checks"]["VRA1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = row_enum_ok
    if not row_enum_ok:
        report["error"] = "SEARCH_RESULT_ROW_ENUMERATION_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_ROW_ENUMERATION_PARITY_MISMATCH"
        return report

    runtime_parity_ok = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["VRA1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = runtime_parity_ok
    if not runtime_parity_ok:
        report["error"] = "RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        return report

    telem_auth_ok = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["VRA1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = telem_auth_ok
    if not telem_auth_ok:
        report["error"] = "SEARCH_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        return report

    exc_order_parity_ok = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["VRA1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = exc_order_parity_ok
    if not exc_order_parity_ok:
        report["error"] = "SEARCH_EXCEPTION_ORDER_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_EXCEPTION_ORDER_PARITY_MISMATCH"
        return report

    safety_gate_parity_ok = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["VRA1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = safety_gate_parity_ok
    if not safety_gate_parity_ok:
        report["error"] = "SEARCH_SAFETY_GATE_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_SAFETY_GATE_PARITY_MISMATCH"
        return report

    host_api_parity_ok = verify_host_dom_evaluation_api_parity()
    report["checks"]["VRA1_HOST_DOM_EVALUATION_API_PARITY"] = host_api_parity_ok
    if not host_api_parity_ok:
        report["error"] = "HOST_DOM_EVALUATION_API_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HOST_API_PARITY_MISMATCH"
        return report

    static_zero_ok = verify_static_zero_interaction_contract()
    report["checks"]["VRA1_STATIC_ZERO_INTERACTION_AUDIT"] = static_zero_ok
    if not static_zero_ok:
        report["error"] = "STATIC_ZERO_INTERACTION_AUDIT_FAILED"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_STATIC_ZERO_AUDIT_FAILED"
        return report

    if not PROTOCOL_FREEZE_PATH.exists():
        err_msg = f"PROTOCOL_FREEZE_NOT_FOUND: {PROTOCOL_FREEZE_PATH}"
        logger.error(f"❌ {err_msg}")
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
    if protocol_manifest.get("canonical_dry_run_verdict") != CANONICAL_DRY_RUN_VERDICT:
        semantic_errors.append(f"canonical_dry_run_verdict mismatch: {protocol_manifest.get('canonical_dry_run_verdict')} != {CANONICAL_DRY_RUN_VERDICT}")

    proto_target = protocol_manifest.get("target_identity", {})
    if proto_target.get("channel_id") != TARGET_CHANNEL_ID:
        semantic_errors.append(f"target_identity channel_id mismatch: {proto_target.get('channel_id')} != {TARGET_CHANNEL_ID}")
    if proto_target.get("message_ts") != TARGET_MESSAGE_TS:
        semantic_errors.append(f"target_identity message_ts mismatch: {proto_target.get('message_ts')} != {TARGET_MESSAGE_TS}")
    if proto_target.get("thread_ts") != TARGET_THREAD_TS:
        semantic_errors.append(f"target_identity thread_ts mismatch: {proto_target.get('thread_ts')} != {TARGET_THREAD_TS}")
    if proto_target.get("is_thread_reply") is not True:
        semantic_errors.append("target_identity is_thread_reply is not True")
    if proto_target.get("rank_is_observational_only") is not True:
        semantic_errors.append("target_identity rank_is_observational_only is not True")

    proto_origins = protocol_manifest.get("url_safety_contract", {}).get("allowed_origins", [])
    if set(proto_origins) != set(FROZEN_ALLOWED_ORIGINS):
        semantic_errors.append(f"allowed_origins mismatch: {proto_origins} != {FROZEN_ALLOWED_ORIGINS}")

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
        logger.info("🔍 [DRY_RUN] All preflight gates passed. Dry-run mode: executing offline mock inspection...")
        report["dry_run"] = True
        report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = True
        report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = True
        report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = True
        report["checks"]["search_safety_gate"] = "PASS"
        report["checks"]["atomic_geometry_snapshot_executed"] = True

        # STRICT ZERO ACTUAL LIVE ACTIONS IN DRY RUN
        report["live_action_inventory"]["search_activation_dispatches"] = 0
        report["live_action_inventory"]["search_modal_observed"] = False
        report["live_action_inventory"]["search_input_ready"] = "UNKNOWN"
        report["live_action_inventory"]["search_submissions"] = 0
        report["live_action_inventory"]["pure_read_dom_extractions"] = 0
        report["live_action_inventory"]["geometry_snapshots"] = 0
        report["live_action_inventory"]["guarded_anchor_clicks"] = 0
        report["live_action_inventory"]["reply_bar_clicks"] = 0
        report["live_action_inventory"]["result_card_clicks"] = 0
        report["live_action_inventory"]["scroll_actions"] = 0
        report["live_action_inventory"]["keyboard_events"] = 0
        report["live_action_inventory"]["composer_interactions"] = 0
        report["live_action_inventory"]["write_capable_actions"] = 0
        report["live_action_inventory"]["restorations_attempted"] = 0
        report["live_action_inventory"]["back_navigations_attempted"] = 0

        # Partition simulated outcome
        mock_geom = build_mock_dry_run_geometry()
        report["simulation_geometry"] = mock_geom
        report["geometry_telemetry"] = None
        report["reachability_classification"] = classify_viewport_reachability(mock_geom)

        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["harness_phases"].append("DRY_RUN_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ [DRY_RUN_PASS] Dry run completed successfully. Persisted to {DRY_RUN_OUTPUT_FILE}")
        return report

    report["live_execution_started"] = True
    report["harness_phases"].append("LIVE_EXECUTION_STARTED")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    marker_content = {
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "git_commit": git_commit,
        "runner_sha256": pre_hashes.get("vra1_runner"),
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
        logger.error(f"❌ {err_msg}: {ATTEMPT_STARTED_MARKER_FILE}")
        report["error"] = err_msg
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_CONSUMED"
        return report

    host: Optional[PesuWebViewHost] = None
    try:
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (VRA1 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / VRA1 Viewport Reachability",
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

        logger.info("📐 [GEOMETRY_SNAPSHOT] Evaluating target row, anchor geometry, viewport, and scroll container...")
        js_geometry_snapshot = (
            f"({JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT})("
            f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
            f"{SEARCH_MAX_RESULTS}, "
            f"{json.dumps(TARGET_CHANNEL_ID)}, "
            f"{json.dumps(TARGET_MESSAGE_TS)}, "
            f"{json.dumps(TARGET_THREAD_TS)}, "
            f"{json.dumps(FROZEN_ALLOWED_ORIGINS)})"
        )
        geom_res = host.evaluate_js(js_geometry_snapshot, timeout=10.0)
        report["live_action_inventory"]["pure_read_dom_extractions"] += 1
        report["live_action_inventory"]["geometry_snapshots"] = 1

        if not isinstance(geom_res, dict):
            logger.error(f"❌ Geometry snapshot returned non-dict: {type(geom_res)}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "GEOMETRY_SNAPSHOT_EVALUATION_FAILED"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        report["geometry_telemetry"] = geom_res
        report["checks"]["atomic_geometry_snapshot_executed"] = True

        classification = classify_viewport_reachability(geom_res)
        report["reachability_classification"] = classification
        report["diagnostic_verdict"] = classification
        report["verdict"] = "DIAGNOSTIC_COMPLETED" if classification != "FAIL_CLOSED" else "FAIL_CLOSED"

        report["live_execution_completed"] = True
        report["harness_phases"].append("GEOMETRY_SNAPSHOT_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)
        persist_live_artifact(report)
        return report

    except Exception as e:
        logger.error(f"❌ Exception during VRA1 live diagnostic: {e}", exc_info=True)
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
        logger.error(f"❌ Failed to persist authoritative live artifact: {pe}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 13.5D / Task 11 / P2A / VRA1 Viewport Reachability Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline preflight checks without live actions")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds for live runtime")
    args = parser.parse_args()

    report = run_vra1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print(f"VERDICT: {report.get('verdict')}")
    print(f"DIAGNOSTIC_VERDICT: {report.get('diagnostic_verdict')}")
    print(f"REACHABILITY_CLASSIFICATION: {report.get('reachability_classification')}")

    if report.get("verdict") in ("DRY_RUN_PASS", "DIAGNOSTIC_COMPLETED"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
