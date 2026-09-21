#!/usr/bin/env python
"""Task 11 / P2A-L1-RD1: Bounded Search Result Residency Diagnostic Runner.

Pure read-only observer diagnostic to determine search result residency for the frozen target:
- Query: "26년 하반기 간담회"
- Expected Channel: "C09HL5XCTNJ"
- Expected Message TS: "1788152491.960529"
- Expected Root TS: "1787709893.004509"

Invariants:
- MAX_DIAGNOSTIC_ROWS = 20: RD1 inspects at most 20 currently mounted visible result rows
  and records whether the mounted set was truncated by that bound.
- Pure read-only inspection of mounted DOM.
- reply_bar_clicks = 0, result_card_clicks = 0, composer_interaction = 0,
  permalink_navigation = 0, keyboard_shortcuts = 0, CDP = 0, provider_calls = 0,
  scroll_iterations = 0 (Zero Scroll), retries = 0.
- RD1 NEVER declares P2A_L1 = PASS and NEVER sets THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED = true.
- Guaranteed native trace persistence on all terminal paths in finally block.
- Separate trace paths for DRY_RUN and LIVE modes.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuWebViewHost,
)
from pesu_agent.search.webview_slack_search import (
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)
from pesu_agent.search.webview_thread_reader import normalize_slack_ts

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
logger = logging.getLogger("task11q4_p2a_l1_rd1_search_residency")

# Frozen Target Identity
TARGET_QUERY = "26년 하반기 간담회"
EXPECTED_CHANNEL_ID = "C09HL5XCTNJ"
EXPECTED_MESSAGE_TS = "1788152491.960529"
EXPECTED_ROOT_TS = "1787709893.004509"

MAX_DIAGNOSTIC_ROWS = 20

PROTOCOL_FREEZE_PATH = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_rd1_protocol_freeze.json"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
LIVE_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rd1_search_residency.json"
DRY_RUN_OUTPUT_FILE = OUTPUT_DIR / "task11q4_p2a_l1_rd1_search_residency_dry_run.json"

JS_COUNT_MOUNTED_RESULT_ROOTS = r"""
(() => {
    const searchViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el && el.isConnected);
    if (searchViews.length !== 1) return 0;
    const searchView = searchViews[0];
    const roots = Array.from(searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
        if (!el || !el.isConnected) return false;
        const r = el.getBoundingClientRect();
        return (r.width > 0 && r.height > 0);
    });
    return roots.length;
})()
"""

DEPENDENCY_PATHS = {
    "rd1_runner": REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_search_residency.py",
    "webview_slack_search": SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py",
    "pesu_webview_host": SRC_DIR / "pesu_agent" / "runtime" / "pesu_webview_host.py",
    "slack_permalink_parser": SRC_DIR / "pesu_agent" / "parsers" / "slack_permalink_parser.py",
}


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
            ["git", "diff", "--stat"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def get_file_sha256(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_all_dependency_hashes() -> dict[str, str]:
    return {name: get_file_sha256(p) for name, p in DEPENDENCY_PATHS.items()}


def classify_row_identity(
    raw_item: dict[str, Any],
    expected_channel: str = EXPECTED_CHANNEL_ID,
    expected_message_ts: str = EXPECTED_MESSAGE_TS,
    expected_root_ts: str = EXPECTED_ROOT_TS,
) -> dict[str, Any]:
    """Classifies a single search result row without preserving message body text."""
    rank = int(raw_item.get("raw_index") or raw_item.get("result_index") or 1)
    permalinks = raw_item.get("qualifying_permalinks") or []
    canonical_permalink = raw_item.get("canonical_permalink") or (permalinks[0] if permalinks else None)

    parsed_id: Optional[SlackMessagePermalinkIdentity] = None
    if canonical_permalink:
        parsed_id = parse_slack_message_permalink(canonical_permalink)

    if not parsed_id and permalinks:
        for pl in permalinks:
            p = parse_slack_message_permalink(pl)
            if p:
                parsed_id = p
                break

    parsed_channel_id = parsed_id.channel_id if parsed_id else None
    parsed_message_ts = parsed_id.message_ts if parsed_id else None
    parsed_thread_ts = parsed_id.thread_ts if parsed_id else None
    is_thread_reply = parsed_id.is_thread_reply if parsed_id else bool(raw_item.get("thread_reply_detected", False))

    reply_bar_present = bool(raw_item.get("thread_reply_detected", False) or is_thread_reply)

    if not parsed_id:
        classification = "PARSE_FAILED"
        c_match = False
        m_match = False
        t_match = False
    else:
        c_match = (parsed_channel_id == expected_channel)
        m_match = (normalize_slack_ts(parsed_message_ts) == normalize_slack_ts(expected_message_ts))
        t_match = (
            parsed_thread_ts is not None
            and normalize_slack_ts(parsed_thread_ts) == normalize_slack_ts(expected_root_ts)
        )

        if c_match and m_match and t_match:
            classification = "EXACT_FROZEN_IDENTITY_MATCH"
        elif c_match and m_match and not t_match:
            classification = "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD"
        elif c_match and not m_match and t_match:
            classification = "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE"
        else:
            classification = "UNRELATED"

    return {
        "rank": rank,
        "raw_permalink": canonical_permalink,
        "parsed_channel_id": parsed_channel_id,
        "parsed_message_ts": parsed_message_ts,
        "parsed_thread_ts": parsed_thread_ts,
        "is_thread_reply": is_thread_reply,
        "parse_status": "PARSED" if parsed_id else "PARSE_FAILED",
        "reply_bar_present": reply_bar_present,
        "target_classification": classification,
        "exact_frozen_match": bool(c_match and m_match and t_match),
        "same_channel_message_match": bool(c_match and m_match),
        "same_channel_thread_match": bool(c_match and t_match),
    }


def determine_diagnostic_verdict(
    inspected_rows: list[dict[str, Any]],
    mounted_row_count: int,
    inspected_row_count: int,
    inspection_truncated: bool,
    telemetry_ok: bool,
) -> tuple[str, Optional[str]]:
    """Determines the overall diagnostic verdict using strict priority hierarchy."""
    if not telemetry_ok or mounted_row_count == 0:
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "ZERO_MOUNTED_ROWS_OR_MISSING_TELEMETRY"

    # 1. Exact match
    if any(r["target_classification"] == "EXACT_FROZEN_IDENTITY_MATCH" for r in inspected_rows):
        return "TARGET_EXACT_MATCH_FOUND", None

    # 2. Representation drift of target message
    if any(r["target_classification"] == "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD" for r in inspected_rows):
        return "TARGET_REPRESENTATION_DRIFT_FOUND", None

    # 3. Same thread, different search hit
    if any(r["target_classification"] == "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE" for r in inspected_rows):
        return "SAME_THREAD_DIFFERENT_SEARCH_HIT_FOUND", None

    # 4 & 5. Target absent from inspected rows
    if inspection_truncated:
        return "SEARCH_RESULT_CAPTURE_INSUFFICIENT", "MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND"

    return "TARGET_NOT_IN_BOUNDED_INSPECTED_SET", None


def run_rd1_diagnostic(dry_run: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    start_time = time.time()
    output_file = DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE

    report: dict[str, Any] = {
        "protocol": "TASK11Q4_P2A_L1_RD1_SEARCH_RESIDENCY",
        "execution_mode": "DRY_RUN" if dry_run else "LIVE",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "clean_diff": not check_uncommitted_diff(),
        "inspection_bound": MAX_DIAGNOSTIC_ROWS,
        "bound_definition": "RD1 inspects at most 20 currently mounted visible result rows and records whether the mounted set was truncated by that bound.",
        "mounted_row_count": 0,
        "inspected_row_count": 0,
        "inspection_truncated": False,
        "inspected_rows": [],
        "diagnostic_verdict": "FAIL_CLOSED",
        "verdict_reason": None,
        "P2A_L1": "NOT_APPLICABLE_DIAGNOSTIC_ONLY",
        "thread_root_data_extraction_live_validated": False,
        "safety_telemetry": {
            "reply_bar_clicks": 0,
            "result_card_clicks": 0,
            "composer_interaction": 0,
            "permalink_navigation": 0,
            "keyboard_shortcuts": 0,
            "CDP": 0,
            "provider_calls": 0,
            "scroll_iterations": 0,
            "retries": 0,
        },
        "search_telemetry": {},
        "duration_seconds": 0.0,
    }

    host: Optional[PesuWebViewHost] = None
    try:
        if dry_run:
            logger.info("🧪 [RD1_DRY_RUN] Performing offline dry-run validation (ZERO UI actions)...")
            report["mounted_row_count"] = 0
            report["inspected_row_count"] = 0
            report["inspection_truncated"] = False
            report["diagnostic_verdict"] = "DRY_RUN_PASS"
            return report

        # ---------------------------------------------------------
        # LIVE DIAGNOSTIC EXECUTION PATH
        # ---------------------------------------------------------
        logger.info("🚀 [RD1_LIVE] Initializing dedicated WebView host for residency diagnosis...")
        host = PesuWebViewHost(
            user_data_folder=DIRECT_SLACK_PROFILE_PATH,
            initial_url="https://app.slack.com/client",
            title="PESU Task 11 / P2A-L1-RD1 Residency Diagnostic",
        )
        backend = WebViewSlackSearchBackend(
            config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH)),
            host=host,
        )

        logger.info("📡 [RUNTIME_START] Starting Dedicated Slack WebView Host...")
        backend.start_runtime(timeout=timeout)

        logger.info(f"🔍 [SEARCH_EXECUTE] Submitting target query: '{TARGET_QUERY}' (max_results={MAX_DIAGNOSTIC_ROWS})")
        search_exception = None
        try:
            backend.search_messages(TARGET_QUERY, max_results=MAX_DIAGNOSTIC_ROWS)
        except Exception as se:
            search_exception = se
            logger.warning(f"⚠️ search_messages raised: {se}")

        telemetry = getattr(backend, "last_search_telemetry", {}) or {}
        report["search_telemetry"] = {
            "search_activation_dispatches": telemetry.get("activation_dispatch_count", 0),
            "modal_ever_observed": telemetry.get("modal_ever_observed", False),
            "safe_search_action_explicitly_selected": telemetry.get("safe_search_action_explicitly_selected", False),
            "query_state_verified_before_submit": telemetry.get("query_state_verified_before_submit", False),
            "last_confirmed_stage": telemetry.get("last_confirmed_stage", "UNKNOWN"),
        }

        # Query authoritative mounted count from DOM
        mounted_count = 0
        try:
            res_mounted = backend._window.evaluate_js(JS_COUNT_MOUNTED_RESULT_ROOTS)
            if isinstance(res_mounted, (int, float)):
                mounted_count = int(res_mounted)
        except Exception as e:
            logger.warning(f"Failed to query mounted count via JS: {e}")
            mounted_count = int(telemetry.get("raw_items_count", 0))

        report["mounted_row_count"] = mounted_count

        # Extract evaluated raw items
        raw_items = telemetry.get("raw_items_evaluated", [])
        inspected_rows = []
        for itm in raw_items[:MAX_DIAGNOSTIC_ROWS]:
            classified = classify_row_identity(itm)
            inspected_rows.append(classified)

        report["inspected_rows"] = inspected_rows
        report["inspected_row_count"] = len(inspected_rows)
        report["inspection_truncated"] = bool(mounted_count > len(inspected_rows))

        telemetry_ok = bool(
            telemetry.get("modal_ever_observed")
            and telemetry.get("safe_search_action_explicitly_selected")
            and telemetry.get("query_state_verified_before_submit")
            and search_exception is None
        )

        verdict, reason = determine_diagnostic_verdict(
            inspected_rows=inspected_rows,
            mounted_row_count=mounted_count,
            inspected_row_count=len(inspected_rows),
            inspection_truncated=report["inspection_truncated"],
            telemetry_ok=telemetry_ok,
        )
        report["diagnostic_verdict"] = verdict
        report["verdict_reason"] = reason

        logger.info(f"🏁 [RD1_VERDICT] Diagnostic verdict: {verdict} (reason: {reason})")

    except Exception as ex:
        logger.error(f"❌ [EXCEPTION] RD1 diagnostic raised: {ex}", exc_info=True)
        report["error"] = str(ex)
        report["diagnostic_verdict"] = "FAIL_CLOSED"
        report["verdict_reason"] = "RUNTIME_EXCEPTION"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)
        if host is not None:
            try:
                logger.info("🛑 [HOST_SHUTDOWN] Destroying WebView host window...")
                host.destroy()
            except Exception as destroy_err:
                logger.warning(f"Error destroying host: {destroy_err}")

        # Native trace persistence guaranteed on ALL paths
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info(f"📄 Saved RD1 diagnostic trace to {output_file}")
        except Exception as write_err:
            logger.error(f"Failed to write RD1 diagnostic trace: {write_err}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P2A-L1-RD1 Bounded Search Result Residency Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Perform offline dry-run (zero UI actions)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Operation timeout in seconds")
    args = parser.parse_args()

    result = run_rd1_diagnostic(dry_run=args.dry_run, timeout=args.timeout)
    print("\n=== P2A-L1-RD1 DIAGNOSTIC REPORT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["diagnostic_verdict"] in ("FAIL_CLOSED", "SEARCH_RESULT_CAPTURE_INSUFFICIENT"):
        sys.exit(1)
    sys.exit(0)
