"""Phase 13.5D / Task 11 / P2A / TAIQ1: Thread Anchor Interaction Qualification 1.

TAIQ1 PURPOSE:
When the exact known thread-reply search-row anchor is clicked once under strict guards,
does Slack expose the corresponding thread context while preserving the production search surface?

Strict Boundaries:
- QUALIFICATION ONLY: Does NOT qualify production P2A, does NOT modify WebviewThreadReader,
  does NOT perform root hydration, does NOT perform restoration, does NOT create Attempt 5.
- ZERO INTERACTION OUTSIDE BUDGET:
  search operations <= 1, search submissions <= 1, guarded anchor clicks <= 1.
  STRICT ZERO: reply_bar clicks = 0, generic result-card clicks = 0, scroll = 0,
  keyboard navigation = 0, composer interaction = 0, write actions = 0,
  provider calls = 0, retries = 0, restoration = 0, back navigation = 0, second search = 0.
- ONE ATOMIC GUARD AND CLICK: pre-click structural/URL/target safety validation and click
  occur within exactly ONE JavaScript evaluation on the retained validated DOM node.
- TARGET IDENTITY (NOT HARDCODED TO RANK 5):
  1 <= observed_rank <= 5
  channel_id == C042WS25QAX
  message_ts == 1718079276.126469
  thread_ts == 1718058683.912369
  is_thread_reply == true
  exact_target_row_count == 1
- ANCHOR CARDINALITY CONTRACT:
  qualified_anchor_count == 1 (0 or >1 fails closed).
- URL / NEW-WINDOW SAFETY (IN MEMORY ONLY):
  protocol == "https:"
  origin in explicit frozen allowlist ["https://app.slack.com", "https://heumlabs.slack.com"]
  wildcard *.slack.com strictly forbidden
  canonical archive permalink path matching expected channel and message ts
  thread_ts query param matching expected root ts
  target absent, empty, or "_self" (reject _blank, _parent, _top, named context)
  download attribute absent
- POST-CLICK BOUNDED OBSERVATION:
  MAX_POST_CLICK_OBSERVATION_SECONDS <= 5.0
  MAX_POST_CLICK_DOM_READS <= 10
  Raw location URL never persisted.
- SUCCESS REQUIRES IDENTITY, NOT JUST FLEXPANE:
  search_view_connected == true
  search_surface_query_still_matches_requested == true
  threads_flexpane_connected == true
  target_root_identity_observable == true
  target_reply_identity_observable == true
- RESTORATION PROHIBITION:
  If search_view is lost or main navigation occurs:
  ANCHOR_NAVIGATED_MAIN_SURFACE (SEARCH_SURFACE_PRESERVED = false) and STOP.
  Zero history.back(), zero location restoration, zero re-search.
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
logger = logging.getLogger("task11q4_p2a_l1_thread_anchor_interaction_taiq1")

# ---------------------------------------------------------------------------
# Constants & Contracts
# ---------------------------------------------------------------------------

TARGET_QUERY = "26년 하반기 간담회"
SEARCH_MAX_RESULTS = 5

PROTOCOL_NAME = "task11q4_p2a_l1_taiq1_thread_anchor_interaction"
DIAGNOSTIC_RUN = "TAIQ1_INTERACTION_RUN_1"
CANONICAL_DRY_RUN_VERDICT = "DRY_RUN_PASS"

TARGET_CHANNEL_ID = "C042WS25QAX"
TARGET_MESSAGE_TS = "1718079276.126469"
TARGET_THREAD_TS = "1718058683.912369"
TARGET_IS_THREAD_REPLY = True

FROZEN_ALLOWED_ORIGINS = [
    "https://app.slack.com",
    "https://heumlabs.slack.com",
]

MAX_POST_CLICK_OBSERVATION_SECONDS = 5.0
MAX_POST_CLICK_DOM_READS = 10
POLL_INTERVAL_SECONDS = 0.5

MAX_SLACK_SEARCH_OPERATIONS = 1
MAX_SEARCH_SUBMISSIONS = 1
MAX_GUARDED_ANCHOR_CLICKS = 1
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

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
PROTOCOL_FREEZE_PATH = ARTIFACTS_DIR / "task11q4_p2a_l1_taiq1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_TRACE_DIR = OUTPUT_DIR
DRY_RUN_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_taiq1_anchor_interaction_dry_run.json"
ATTEMPT_STARTED_MARKER_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_taiq1_attempt_started.json"
LIVE_OUTPUT_FILE = LIVE_TRACE_DIR / "task11q4_p2a_l1_taiq1_anchor_interaction.json"

SEARCH_VIEW_SELECTOR = PROD_SEARCH_VIEW_SELECTOR
SEARCH_RESULT_ROW_SELECTORS = PROD_SEARCH_RESULT_ROW_SELECTORS

# ---------------------------------------------------------------------------
# JavaScript: Atomic Guard and Click (Strictly ONE evaluation)
# ---------------------------------------------------------------------------

JS_ATOMIC_GUARD_AND_CLICK = r"""
((searchViewSelector, rowSelectors, maxRows, targetChannelId, targetMessageTs, targetThreadTs, allowedOrigins) => {
    try {
        const searchView = document.querySelector(searchViewSelector);
        if (!searchView || !searchView.isConnected) {
            return {
                ok: false,
                guard_failure_reason: "SEARCH_VIEW_NOT_FOUND",
                click_executed: false
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

        const topRows = mountedRows.slice(0, maxRows);

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

        if (matchedTargetRows.length !== 1) {
            return {
                ok: false,
                guard_failure_reason: "TARGET_ROW_IDENTITY_NOT_UNIQUE",
                target_row_count: matchedTargetRows.length,
                top_rows_evaluated: topRows.length,
                click_executed: false
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

            // Center-point hit-test / occlusion guard
            if (document.elementFromPoint) {
                const cx = aRect.left + aRect.width / 2;
                const cy = aRect.top + aRect.height / 2;
                const hit = document.elementFromPoint(cx, cy);
                if (!hit) {
                    guardFailures.push("ANCHOR_HIT_TEST_NULL");
                    continue;
                }
                const hitValid = (hit === a || a.contains(hit));
                if (!hitValid) {
                    guardFailures.push("ANCHOR_HIT_TEST_OCCLUDED");
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
            let specificReason = "ANCHOR_IDENTITY_NOT_UNIQUE";
            if (qualifyingAnchors.length === 0 && guardFailures.length > 0) {
                if (guardFailures.every(f => f === "ANCHOR_HIT_TEST_OCCLUDED" || f === "ANCHOR_HIT_TEST_NULL")) {
                    specificReason = "ANCHOR_HIT_TEST_FAILED";
                }
            }
            return {
                ok: false,
                guard_failure_reason: specificReason,
                qualified_anchor_count: qualifyingAnchors.length,
                guard_failures: guardFailures,
                observed_rank: observedRank,
                click_executed: false
            };
        }

        const anchorToClick = qualifyingAnchors[0];
        anchorToClick.click();

        return {
            ok: true,
            guard_passed: true,
            click_executed: true,
            observed_rank: observedRank,
            qualified_anchor_count: 1,
            target_self_safe: true,
            download_attribute_absent: true,
            origin_allowed: true,
            anchor_hit_test_passed: true,
            has_thread_ts_parameter: true,
            points_to_same_message: true,
            points_to_thread_root: false
        };
    } catch (err) {
        return {
            ok: false,
            guard_failure_reason: "ATOMIC_EVALUATION_EXCEPTION",
            error: String(err),
            click_executed: false
        };
    }
})
"""

# ---------------------------------------------------------------------------
# JavaScript: Bounded Post-Click DOM Observation
# ---------------------------------------------------------------------------

JS_OBSERVE_POST_CLICK_DOM_STATE = r"""
((searchViewSelector, expectedQueryText, targetChannelId, targetMessageTs, targetThreadTs, allowedOrigins, initialUrlOrigin, initialUrlPath) => {
    try {
        const normTs = (ts) => {
            if (!ts) return null;
            const str = String(ts).trim();
            const m = str.match(/^(\d+)(?:\.(\d+))?$/);
            if (!m) return null;
            return m[1] + "." + (m[2] || "").padEnd(6, "0").slice(0, 6);
        };

        const targetMsgNorm = normTs(targetMessageTs);
        const targetThrNorm = normTs(targetThreadTs);

        const sv = document.querySelector(searchViewSelector);
        const search_view_connected = Boolean(sv && sv.isConnected && sv.getBoundingClientRect().width > 0);

        let search_surface_query_still_matches_requested = false;
        if (search_view_connected) {
            const inputs = Array.from(document.querySelectorAll('[data-qa="search_input"], input[type="search"], .c-search__input_box'));
            for (const inp of inputs) {
                if (inp && typeof inp.value === "string" && inp.value.includes(expectedQueryText)) {
                    search_surface_query_still_matches_requested = true;
                    break;
                }
            }
            if (!search_surface_query_still_matches_requested) {
                const headerTitles = Array.from(document.querySelectorAll('[data-qa="search_view"] [data-qa="search_title"], .c-search__header_title'));
                for (const ht of headerTitles) {
                    if (ht && ht.textContent && ht.textContent.includes(expectedQueryText)) {
                        search_surface_query_still_matches_requested = true;
                        break;
                    }
                }
            }
        }

        const flexpane = document.querySelector('div[data-qa="threads_flexpane"]');
        const threads_flexpane_connected = Boolean(flexpane && flexpane.isConnected && flexpane.getBoundingClientRect().width > 0);

        let root_message_container_count = 0;
        let reply_message_container_count = 0;
        let target_root_identity_observable = false;
        let target_reply_identity_observable = false;
        let root_reply_distinct_dom_nodes = false;

        if (threads_flexpane_connected && flexpane) {
            const rawContainers = Array.from(flexpane.querySelectorAll(
                '[data-qa="virtual-list-item"], [data-qa="message_container"], .c-virtual_list__item, .c-message_kit__message, [data-qa="message_pane_item"]'
            )).filter(el => {
                if (!el || !el.isConnected) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });

            const rootContainers = [];
            const replyContainers = [];

            for (const container of rawContainers) {
                // Composer row exclusion (mandatory)
                const hasComposerInput = Boolean(container.querySelector('[data-qa="message_input"], .c-message_input'));
                const hasContentEditable = Boolean(container.querySelector('[contenteditable="true"], textarea, [role="textbox"]'));
                const isComposerClosest = Boolean(container.closest('[data-qa="message_input"], .c-message_input'));
                if (hasComposerInput || hasContentEditable || isComposerClosest) {
                    continue;
                }

                // Check timestamp anchors and message permalinks inside container
                const tsAnchors = Array.from(container.querySelectorAll('a.c-timestamp, a[href*="/archives/"]')).filter(a => {
                    if (!a || !a.isConnected) return false;
                    const insideBlocks = Boolean(a.closest('.c-message_kit__blocks'));
                    const insideRichBlock = Boolean(a.closest('.p-rich_text_block'));
                    const insideRichSection = Boolean(a.closest('.p-rich_text_section'));
                    const insideMsgBody = Boolean(a.closest('.c-message__body'));
                    return !(insideBlocks || insideRichBlock || insideRichSection || insideMsgBody);
                });

                const candidateAnchors = tsAnchors.length > 0 ? tsAnchors : Array.from(container.querySelectorAll('a[href*="/archives/"]'));

                let containerIsRoot = false;
                let containerIsReply = false;

                for (const a of candidateAnchors) {
                    const href = (a.getAttribute('href') || a.href || '').trim();
                    const m = href.match(/\/archives\/([A-Z0-9]+)\/p(\d+)(?:[?&].*thread_ts=(\d+\.\d+))?/);
                    if (!m) continue;

                    const ch = m[1];
                    const rawP = m[2];
                    const msgTs = normTs(rawP.slice(0, 10) + '.' + rawP.slice(10));
                    const thrTs = normTs(m[3]);

                    if (ch !== targetChannelId) continue;

                    // STRICT ROOT SEMANTICS:
                    // Actual message container whose own message_ts matches target root ts (targetThrNorm)
                    // AND (thread_ts is null OR matches root ts)
                    if (msgTs === targetThrNorm && (!thrTs || thrTs === targetThrNorm)) {
                        containerIsRoot = true;
                    }

                    // STRICT REPLY SEMANTICS:
                    // Actual message container whose own message_ts matches target reply ts (targetMsgNorm)
                    // AND thread_ts matches target root ts (targetThrNorm)
                    if (msgTs === targetMsgNorm && thrTs === targetThrNorm) {
                        containerIsReply = true;
                    }
                }

                if (containerIsRoot && !rootContainers.includes(container)) {
                    rootContainers.push(container);
                }
                if (containerIsReply && !replyContainers.includes(container)) {
                    replyContainers.push(container);
                }
            }

            root_message_container_count = rootContainers.length;
            reply_message_container_count = replyContainers.length;
            target_root_identity_observable = (root_message_container_count === 1);
            target_reply_identity_observable = (reply_message_container_count >= 1);
            root_reply_distinct_dom_nodes = (
                rootContainers.length > 0 &&
                replyContainers.length > 0 &&
                !rootContainers.some(rc => replyContainers.includes(rc))
            );
        }

        let location_changed = false;
        let location_classification = "UNKNOWN";

        try {
            const curOrigin = window.location.origin;
            const curPath = window.location.pathname;

            location_changed = (curOrigin !== initialUrlOrigin || curPath !== initialUrlPath);

            if (!allowedOrigins.includes(curOrigin)) {
                location_classification = "EXTERNAL_ORIGIN";
            } else if (!location_changed) {
                location_classification = "UNCHANGED";
            } else if (curPath.includes("/thread/")) {
                location_classification = "SLACK_CLIENT_THREAD_ROUTE";
            } else if (curPath.startsWith("/archives/")) {
                location_classification = "SLACK_ARCHIVE_MESSAGE_ROUTE";
            } else if (allowedOrigins.includes(curOrigin)) {
                location_classification = "OTHER_SAME_ORIGIN_SLACK_ROUTE";
            }
        } catch (e) {
            location_classification = "UNKNOWN";
        }

        const main_surface_changed = !search_view_connected || (location_changed && location_classification !== "UNCHANGED");

        const active = document.activeElement;
        const composer_focus_observed = Boolean(active && active.closest('[contenteditable="true"], [data-qa="message_input"], textarea, [role="textbox"]'));

        return {
            ok: true,
            search_view_connected: search_view_connected,
            search_surface_query_still_matches_requested: search_surface_query_still_matches_requested,
            threads_flexpane_connected: threads_flexpane_connected,
            root_message_container_count: root_message_container_count,
            reply_message_container_count: reply_message_container_count,
            target_root_identity_observable: target_root_identity_observable,
            target_reply_identity_observable: target_reply_identity_observable,
            root_reply_distinct_dom_nodes: root_reply_distinct_dom_nodes,
            location_changed: location_changed,
            location_classification: location_classification,
            main_surface_changed: main_surface_changed,
            composer_focus_observed: composer_focus_observed
        };
    } catch (err) {
        return {
            ok: false,
            error: String(err),
            search_view_connected: false,
            search_surface_query_still_matches_requested: false,
            threads_flexpane_connected: false,
            root_message_container_count: 0,
            reply_message_container_count: 0,
            target_root_identity_observable: false,
            target_reply_identity_observable: false,
            root_reply_distinct_dom_nodes: false,
            location_changed: false,
            location_classification: "UNKNOWN",
            main_surface_changed: true,
            composer_focus_observed: false
        };
    }
})
"""

# ---------------------------------------------------------------------------
# Telemetry Harvester & Safety Gates (Exact RB2 / TR1 / TR2-R1 Parity)
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


def verify_search_telemetry_authority_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies AST parity between TAIQ1 and RB2 harvest_search_telemetry."""
    rb2_file = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py"
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py")
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
    taiq1_src = target.read_text(encoding="utf-8")
    return extract_func_ast(taiq1_src, "harvest_search_telemetry") == extract_func_ast(rb2_src, "harvest_search_telemetry")

def verify_search_exception_order_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    target_path = runner_path or Path(__file__).resolve()
    try:
        src = target_path.read_text(encoding="utf-8")
        norm_src = re.sub(r"\s+", " ", src)
        pattern = re.compile(
            r"search_messages.*harvest_search_telemetry.*if\s+search_exception\s+is\s+not\s+None:\s+raise\s+search_exception.*evaluate_search_safety_and_query_authority"
        )
        if not pattern.search(norm_src):
            logger.error("❌ Search exception ordering parity failed.")
            return False
        return True
    except Exception as e:
        logger.error(f"❌ Error verifying search exception order parity: {e}")
        return False


def verify_search_safety_gate_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies AST parity between TAIQ1 and RB2 evaluate_search_safety_and_query_authority."""
    rb2_file = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py"
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py")
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
    taiq1_src = target.read_text(encoding="utf-8")
    return (
        extract_func_ast(taiq1_src, "evaluate_search_safety_and_query_authority") == extract_func_ast(rb2_src, "evaluate_search_safety_and_query_authority")
        and extract_func_ast(taiq1_src, "is_supplemental_safe") == extract_func_ast(rb2_src, "is_supplemental_safe")
    )

def verify_runtime_search_path_parity_with_attempt4(runner_path: Optional[Path] = None) -> bool:
    """Verifies that TAIQ1 live execution path has exact constructor, start_runtime, and search parity with Attempt 4."""
    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py")
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
            logger.error(f"Missing required runtime pattern in TAIQ1 runner: {pat}")
            return False
    return True


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

    target = runner_path or (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py")
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "evaluate_script":
            logger.error(f"TAIQ1 runner incorrectly accesses attribute evaluate_script at line {node.lineno}")
            return False

    has_evaluate_js_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "host" and node.func.attr == "evaluate_js":
                has_evaluate_js_call = True
                break

    if not has_evaluate_js_call:
        logger.error("TAIQ1 runner missing required host.evaluate_js call")
        return False

    return True


def verify_search_result_row_enumeration_parity() -> bool:
    try:
        js_extract = PROD_JS_EXTRACT_SEARCH_RESULTS
        if "searchView.querySelectorAll(" not in js_extract:
            logger.error("❌ Production JS_EXTRACT_SEARCH_RESULTS missing searchView.querySelectorAll.")
            return False

        if "JS_ATOMIC_GUARD_AND_CLICK" not in globals():
            logger.error("❌ JS_ATOMIC_GUARD_AND_CLICK missing from runner globals.")
            return False

        taiq1_js = globals()["JS_ATOMIC_GUARD_AND_CLICK"]

        norm_taiq1 = re.sub(r"\s+", "", taiq1_js)
        if "searchView.querySelectorAll(rowSelectorStr)" not in norm_taiq1:
            logger.error("❌ TAIQ1 JS missing scoped row querySelectorAll.")
            return False

        if "topRows=mountedRows.slice(0,maxRows)" not in norm_taiq1:
            logger.error("❌ TAIQ1 JS missing exact mounted row slice(0, maxRows).")
            return False

        return True
    except Exception as e:
        logger.error(f"❌ Error verifying row enumeration parity: {e}")
        return False

# ---------------------------------------------------------------------------
# Preflight Hashes, Baselines, and Closure Audit
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
            ["git", "diff", "--quiet", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        return res.returncode != 0
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    deps = {
        "taiq1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1.py",
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
    return {k: get_file_sha256(v) for k, v in deps.items() if v.exists()}


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

    passed = (
        len(missing_from_closure) == 0
        and len(stale_in_closure) == 0
        and len(mismatched) == 0
    )

    return {
        "loaded_project_module_count": len(loaded_map),
        "loaded_project_module_paths": list(loaded_map.keys()),
        "manifest_runtime_closure_count": len(manifest_closure),
        "manifest_runtime_closure_paths": list(manifest_closure.keys()),
        "missing_from_runtime_closure_count": len(missing_from_closure),
        "missing_from_runtime_closure": missing_from_closure,
        "stale_runtime_closure_entry_count": len(stale_in_closure),
        "stale_runtime_closure_entries": stale_in_closure,
        "mismatched_runtime_dependency_count": len(mismatched),
        "mismatched_runtime_dependencies": mismatched,
        "closure_audit_passed": passed,
    }


def build_mock_dry_run_inspection() -> dict[str, Any]:
    return {
        "ok": True,
        "atomic_guard_and_click": {
            "ok": True,
            "guard_passed": True,
            "click_executed": True,
            "observed_rank": 5,
            "qualified_anchor_count": 1,
            "target_self_safe": True,
            "download_attribute_absent": True,
            "origin_allowed": True,
            "anchor_hit_test_passed": True,
            "has_thread_ts_parameter": True,
            "points_to_same_message": True,
            "points_to_thread_root": False,
        },
        "post_click_observation": {
            "ok": True,
            "search_view_connected": True,
            "search_surface_query_still_matches_requested": True,
            "threads_flexpane_connected": True,
            "root_message_container_count": 1,
            "reply_message_container_count": 1,
            "target_root_identity_observable": True,
            "target_reply_identity_observable": True,
            "root_reply_distinct_dom_nodes": True,
            "location_changed": False,
            "location_classification": "UNCHANGED",
            "main_surface_changed": False,
            "composer_focus_observed": False,
        },
        "observation_poll_count": 1,
        "target_identity": {
            "channel_id": TARGET_CHANNEL_ID,
            "message_ts": TARGET_MESSAGE_TS,
            "thread_ts": TARGET_THREAD_TS,
            "is_thread_reply": TARGET_IS_THREAD_REPLY,
            "observed_rank": 5,
        },
    }

# ---------------------------------------------------------------------------
# Runner Execution
# ---------------------------------------------------------------------------

def run_taiq1_diagnostic(*, dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    git_commit = get_git_commit()
    git_diff = check_uncommitted_diff()

    report: dict[str, Any] = {
        "task_id": "Phase 13.5D / Task 11 / P2A-L1 / TAIQ1",
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "verdict": "UNKNOWN",
        "diagnostic_verdict": "UNKNOWN",
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
            "search_submissions": "UNKNOWN",
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
            "guarded_anchor_clicks": 0,
            "pure_read_dom_extractions": 0,
            "restorations_attempted": 0,
            "back_navigations_attempted": 0,
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
            "search_safety_gate": "NOT_EVALUATED",
            "atomic_guard_and_click_executed": False,
            "zero_clicks_outside_budget": True,
            "USER_MESSAGE_TEXT_CAPTURED": False,
            "SENDER_TEXT_CAPTURED": False,
            "RAW_HREF_CAPTURED": False,
            "RAW_PERMALINK_CAPTURED": False,
            "RAW_LOCATION_URL_CAPTURED": False,
            "RAW_ARIA_LABEL_CAPTURED": False,
            "RAW_TITLE_CAPTURED": False,
            "INNER_TEXT_CAPTURED": False,
            "TEXT_CONTENT_CAPTURED": False,
            "INNER_HTML_CAPTURED": False,
            "OUTER_HTML_CAPTURED": False,
            "TAIQ1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4": False,
            "TAIQ1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4": False,
            "TAIQ1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4": False,
            "TAIQ1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4": False,
            "TAIQ1_SEARCH_RESULT_ROW_ENUMERATION_PARITY": False,
            "TAIQ1_HOST_DOM_EVALUATION_API_PARITY": False,
            "historical_baselines_matched": False,
            "attempt_marker_written": False,
            "ACTION_CONTRACTS_SATISFIED": True,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": "UNKNOWN",
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": "UNKNOWN",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": "UNKNOWN",
        },
        "interaction_inspection": None,
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
    report["checks"]["TAIQ1_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] = row_enum_ok
    if not row_enum_ok:
        report["error"] = "SEARCH_RESULT_ROW_ENUMERATION_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_ROW_ENUMERATION_PARITY_MISMATCH"
        return report

    runtime_parity_ok = verify_runtime_search_path_parity_with_attempt4()
    report["checks"]["TAIQ1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] = runtime_parity_ok
    if not runtime_parity_ok:
        report["error"] = "RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_RUNTIME_SEARCH_PATH_PARITY_MISMATCH"
        return report

    telem_auth_ok = verify_search_telemetry_authority_parity_with_attempt4()
    report["checks"]["TAIQ1_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] = telem_auth_ok
    if not telem_auth_ok:
        report["error"] = "SEARCH_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_TELEMETRY_AUTHORITY_PARITY_MISMATCH"
        return report

    exc_order_parity_ok = verify_search_exception_order_parity_with_attempt4()
    report["checks"]["TAIQ1_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] = exc_order_parity_ok
    if not exc_order_parity_ok:
        report["error"] = "SEARCH_EXCEPTION_ORDER_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_EXCEPTION_ORDER_PARITY_MISMATCH"
        return report

    safety_gate_parity_ok = verify_search_safety_gate_parity_with_attempt4()
    report["checks"]["TAIQ1_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] = safety_gate_parity_ok
    if not safety_gate_parity_ok:
        report["error"] = "SEARCH_SAFETY_GATE_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_SAFETY_GATE_PARITY_MISMATCH"
        return report

    host_api_parity_ok = verify_host_dom_evaluation_api_parity()
    report["checks"]["TAIQ1_HOST_DOM_EVALUATION_API_PARITY"] = host_api_parity_ok
    if not host_api_parity_ok:
        report["error"] = "HOST_DOM_EVALUATION_API_PARITY_MISMATCH"
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PREFLIGHT_HOST_API_PARITY_MISMATCH"
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
        report["checks"]["atomic_guard_and_click_executed"] = True

        # STRICT ZERO ACTUAL LIVE ACTIONS IN DRY RUN
        report["live_action_inventory"]["search_activation_dispatches"] = 0
        report["live_action_inventory"]["search_modal_observed"] = False
        report["live_action_inventory"]["search_input_ready"] = "UNKNOWN"
        report["live_action_inventory"]["search_submissions"] = 0
        report["live_action_inventory"]["pre_click_location_reads"] = 0
        report["live_action_inventory"]["reply_bar_clicks"] = 0
        report["live_action_inventory"]["result_card_clicks"] = 0
        report["live_action_inventory"]["guarded_anchor_clicks"] = 0
        report["live_action_inventory"]["pure_read_dom_extractions"] = 0
        report["live_action_inventory"]["restorations_attempted"] = 0
        report["live_action_inventory"]["back_navigations_attempted"] = 0

        # Partition simulated outcome
        mock_res = build_mock_dry_run_inspection()
        report["interaction_inspection"] = mock_res
        report["dry_run_simulation"] = {
            "simulated_guard_passed": True,
            "simulated_click_executed": True,
            "simulated_post_click_outcome": "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED",
            "simulated_root_message_container_count": 1,
            "simulated_reply_message_container_count": 1,
            "simulated_target_root_identity_observable": True,
            "simulated_target_reply_identity_observable": True,
            "simulated_root_reply_distinct_dom_nodes": True,
            "simulated_anchor_hit_test_passed": True,
        }

        report["verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["diagnostic_verdict"] = CANONICAL_DRY_RUN_VERDICT
        report["harness_phases"].append("DRY_RUN_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 Saved dry-run trace to {DRY_RUN_OUTPUT_FILE}")
        return report

    def persist_live_artifact(rep: dict[str, Any]) -> None:
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    marker_content = {
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "git_commit": git_commit,
        "runner_sha256": pre_hashes.get("taiq1_runner"),
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
        logger.info("🌐 [RUNTIME_START] Initializing Dedicated Slack WebView Host (TAIQ1 Parity)...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1 / TAIQ1 Thread Anchor Interaction",
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

        logger.info("📍 [LOCATION_BASELINE] Capturing pre-click baseline location...")
        init_loc = host.evaluate_js(
            "(() => ({ origin: window.location.origin, pathname: window.location.pathname }))()",
            timeout=5.0,
        )
        report["live_action_inventory"]["pure_read_dom_extractions"] += 1
        report["live_action_inventory"]["pre_click_location_reads"] = 1

        valid_baseline = (
            isinstance(init_loc, dict)
            and isinstance(init_loc.get("origin"), str)
            and bool(init_loc.get("origin").strip())
            and init_loc.get("origin") in FROZEN_ALLOWED_ORIGINS
            and isinstance(init_loc.get("pathname"), str)
            and bool(init_loc.get("pathname").strip())
        )

        report["checks"]["pre_click_location_baseline_valid"] = valid_baseline

        if not valid_baseline:
            logger.error(
                f"❌ Pre-click location baseline unavailable or invalid: "
                f"init_loc={init_loc}"
            )
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "PRE_CLICK_LOCATION_BASELINE_UNAVAILABLE"
            report["live_action_inventory"]["guarded_anchor_clicks"] = 0
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        init_origin = init_loc["origin"]
        init_path = init_loc["pathname"]
        report["harness_phases"].append("PRE_CLICK_LOCATION_BASELINE_CAPTURED")

        logger.info("⚡ [ATOMIC_GUARD_AND_CLICK] Evaluating target row, anchor guards, and dispatching click...")
        js_atomic_click = (
            f"({JS_ATOMIC_GUARD_AND_CLICK})("
            f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(SEARCH_RESULT_ROW_SELECTORS)}, "
            f"{SEARCH_MAX_RESULTS}, "
            f"{json.dumps(TARGET_CHANNEL_ID)}, "
            f"{json.dumps(TARGET_MESSAGE_TS)}, "
            f"{json.dumps(TARGET_THREAD_TS)}, "
            f"{json.dumps(FROZEN_ALLOWED_ORIGINS)})"
        )
        atomic_click_res = host.evaluate_js(js_atomic_click, timeout=10.0)

        report["checks"]["atomic_guard_and_click_executed"] = True
        interaction_rep: dict[str, Any] = {
            "atomic_guard_and_click": atomic_click_res,
            "observations": [],
            "final_observation": None,
        }
        report["interaction_inspection"] = interaction_rep

        if not isinstance(atomic_click_res, dict) or not atomic_click_res.get("ok"):
            fail_reason = (
                atomic_click_res.get("guard_failure_reason")
                if isinstance(atomic_click_res, dict)
                else "ATOMIC_EVALUATION_FAILED"
            )
            logger.error(f"❌ Atomic guard/click failed closed: {fail_reason}")
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = fail_reason or "FAIL_CLOSED"
            report["duration_seconds"] = round(time.time() - start_time, 2)
            persist_live_artifact(report)
            return report

        report["live_action_inventory"]["guarded_anchor_clicks"] = 1
        report["harness_phases"].append("ATOMIC_GUARD_AND_CLICK_EXECUTED")

        logger.info(f"🔬 [POST_CLICK_OBSERVATION] Polling DOM state (max {MAX_POST_CLICK_OBSERVATION_SECONDS}s)...")
        obs_deadline = time.time() + MAX_POST_CLICK_OBSERVATION_SECONDS
        obs_count = 0
        final_obs: Optional[dict[str, Any]] = None

        js_observe = (
            f"({JS_OBSERVE_POST_CLICK_DOM_STATE})("
            f"{json.dumps(SEARCH_VIEW_SELECTOR)}, "
            f"{json.dumps(TARGET_QUERY)}, "
            f"{json.dumps(TARGET_CHANNEL_ID)}, "
            f"{json.dumps(TARGET_MESSAGE_TS)}, "
            f"{json.dumps(TARGET_THREAD_TS)}, "
            f"{json.dumps(FROZEN_ALLOWED_ORIGINS)}, "
            f"{json.dumps(init_origin)}, "
            f"{json.dumps(init_path)})"
        )

        while time.time() < obs_deadline and obs_count < MAX_POST_CLICK_DOM_READS:
            obs_count += 1
            cur_obs = host.evaluate_js(js_observe, timeout=3.0)
            report["live_action_inventory"]["pure_read_dom_extractions"] += 1

            if isinstance(cur_obs, dict):
                interaction_rep["observations"].append(cur_obs)
                final_obs = cur_obs

                if (
                    cur_obs.get("threads_flexpane_connected") is True
                    and cur_obs.get("root_message_container_count") == 1
                    and cur_obs.get("target_root_identity_observable") is True
                    and cur_obs.get("reply_message_container_count", 0) >= 1
                    and cur_obs.get("target_reply_identity_observable") is True
                    and cur_obs.get("root_reply_distinct_dom_nodes") is True
                ):
                    logger.info(f"✨ Confirmed thread flexpane with distinct root and reply identities on poll #{obs_count}!")
                    break

                if cur_obs.get("main_surface_changed") is True or not cur_obs.get("search_view_connected"):
                    logger.warning(f"⚠️ Main surface navigation observed on poll #{obs_count}!")
                    break

            time.sleep(POLL_INTERVAL_SECONDS)

        interaction_rep["final_observation"] = final_obs

        if not final_obs or not isinstance(final_obs, dict):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "POST_CLICK_OBSERVATION_FAILED"
        elif (
            final_obs.get("search_view_connected") is True
            and final_obs.get("search_surface_query_still_matches_requested") is True
            and final_obs.get("threads_flexpane_connected") is True
            and final_obs.get("root_message_container_count") == 1
            and final_obs.get("target_root_identity_observable") is True
            and final_obs.get("reply_message_container_count", 0) >= 1
            and final_obs.get("target_reply_identity_observable") is True
            and final_obs.get("root_reply_distinct_dom_nodes") is True
            and not final_obs.get("composer_focus_observed")
        ):
            report["verdict"] = "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED"
            report["diagnostic_verdict"] = "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED"
        elif (
            final_obs.get("threads_flexpane_connected") is True
            and final_obs.get("search_view_connected") is True
        ):
            report["verdict"] = "ANCHOR_OPENED_THREAD_FLEXPANE_IDENTITY_INCOMPLETE"
            report["diagnostic_verdict"] = "ANCHOR_OPENED_THREAD_FLEXPANE_IDENTITY_INCOMPLETE"
        elif (
            not final_obs.get("search_view_connected")
            or final_obs.get("main_surface_changed") is True
            or final_obs.get("location_classification") in ("SLACK_CLIENT_THREAD_ROUTE", "SLACK_ARCHIVE_MESSAGE_ROUTE", "EXTERNAL_ORIGIN")
        ):
            report["verdict"] = "ANCHOR_NAVIGATED_MAIN_SURFACE"
            report["diagnostic_verdict"] = "ANCHOR_NAVIGATED_MAIN_SURFACE"
        else:
            report["verdict"] = "ANCHOR_CLICK_NO_OBSERVABLE_THREAD_CONTEXT"
            report["diagnostic_verdict"] = "ANCHOR_CLICK_NO_OBSERVABLE_THREAD_CONTEXT"

        report["live_execution_completed"] = True
        report["harness_phases"].append("LIVE_EXECUTION_COMPLETED")
        report["duration_seconds"] = round(time.time() - start_time, 2)

        persist_live_artifact(report)
        return report

    except Exception as e:
        logger.error(f"❌ TAIQ1 execution error: {e}", exc_info=True)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11 / TAIQ1: Thread Anchor Interaction Qualification 1")
    parser.add_argument("--dry-run", action="store_true", help="Run in offline dry-run mode")
    parser.add_argument("--timeout", type=float, default=30.0, help="Search timeout in seconds")
    args = parser.parse_args()

    report = run_taiq1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print(f"TAIQ1 Verdict: {report.get('verdict')} (diagnostic_verdict: {report.get('diagnostic_verdict')})")
    if report.get("verdict") in (CANONICAL_DRY_RUN_VERDICT, "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED", "ANCHOR_NAVIGATED_MAIN_SURFACE"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
