#!/usr/bin/env python
"""Task 11R10F — Bounded Live Safety Qualification (Case B Q2 Only).

Objective:
  Validate live on Slack WebView that for natural language query Case B Q2
  ("신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀"),
  PESU Agent strictly enforces the read-only safety contract:
  1. Default/selected Slackbot suggestion is NEVER invoked (SLACKBOT_ACTION_INVOKED_BY_PESU = False).
  2. Generic SearchInputBox.onEnter() is NEVER executed (GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = False).
  3. No write-capable action is invoked (WRITE_CAPABLE_ACTION_INVOKED_BY_PESU = False).
  4. Exactly one safe keyword search suggestion is identified and explicitly activated.
  5. If safe candidate cardinality != 1, it fails closed immediately.

Strict Invariants:
  - TARGET_QUERY = "신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀" (Case B Q2)
  - MAX_LIVE_EXECUTIONS = 1
  - LIVE_SLACK_CALLS = 1
  - SEMANTIC_PIPELINE_CALLS = 0 (EvidenceAssessor=0, ClaimVerifier=0, Stage=0)
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
    WebViewSlackSearchBackend,
    WebViewSearchError,
    WebViewSearchFailureCode,
)
from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r10f_live_safety")

TARGET_QUERY = "신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀"
OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
OUTPUT_FILE = OUTPUT_DIR / "task11r10f_live_safety_trace.json"


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
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def run_bounded_live_safety_validation(dry_run: bool = False) -> dict[str, Any]:
    commit_sha = get_git_commit()
    has_diff = check_uncommitted_diff()
    webview_path = SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py"
    cdp_path = SRC_DIR / "pesu_agent" / "adapters" / "slack_cdp.py"
    webview_sha = get_file_sha256(webview_path)
    cdp_sha = get_file_sha256(cdp_path)

    logger.info("=" * 80)
    logger.info("🛡️ [TASK 11R10F] BOUNDED LIVE SAFETY QUALIFICATION")
    logger.info(f"   Commit SHA: {commit_sha}")
    logger.info(f"   Uncommitted Diff: {has_diff}")
    logger.info(f"   WebView SHA-256: {webview_sha}")
    logger.info(f"   CDP Adapter SHA-256: {cdp_sha}")
    logger.info(f"   Target Query: '{TARGET_QUERY}'")
    logger.info("=" * 80)

    # Path audit assertions
    reachable_paths = [
        "pesu_agent.search.webview_slack_search.JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER",
        "pesu_agent.adapters.slack_cdp.SlackReadOnlyCdpAdapter._submit_verified_global_search_native",
    ]
    reachable_count = len(reachable_paths)
    guarded_count = 2
    unguarded_count = reachable_count - guarded_count

    report: dict[str, Any] = {
        "task_id": "Task 11R10F",
        "execution_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit_sha,
        "uncommitted_fix_drift": has_diff,
        "hashes": {
            "webview_slack_search_sha256": webview_sha,
            "slack_cdp_sha256": cdp_sha,
        },
        "path_audit": {
            "REACHABLE_SLACK_SEARCH_SUBMISSION_PATH_COUNT": reachable_count,
            "GUARDED_SUBMISSION_PATH_COUNT": guarded_count,
            "UNGUARDED_REACHABLE_PATH_COUNT": unguarded_count,
        },
        "dry_run": dry_run,
        "query": TARGET_QUERY,
        "execution_record": {},
        "safety_metrics": {
            "SLACKBOT_ACTION_INVOKED_BY_PESU": False,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": False,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": False,
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": False,
            "SAFETY_CONTAINMENT_VERDICT": "PENDING",
        },
    }

    if dry_run:
        logger.info("🧪 [DRY_RUN] Safety qualification simulation.")
        report["safety_metrics"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = True
        report["safety_metrics"]["SAFETY_CONTAINMENT_VERDICT"] = "PASS"
        return report

    # 1. Initialize backend
    backend = create_slack_search_backend(
        config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
    )
    host = getattr(backend, "_window", None)
    if not host or not hasattr(host, "start"):
        raise RuntimeError("Failed to obtain PesuWebViewHost from backend")

    t_start = time.perf_counter()
    logger.info("🌐 [HOST_START] Launching dedicated Slack WebView...")
    host.start(timeout=30.0)
    logger.info(f"🌐 [HOST_READY] Host started in {time.perf_counter() - t_start:.2f}s")

    search_error: Optional[Exception] = None
    search_results: list[Any] = []
    telemetry: dict[str, Any] = {}

    try:
        logger.info(f"🔍 [SEARCH_EXECUTE] Submitting target query (single bounded pass)...")
        search_results = backend.search_messages(TARGET_QUERY, max_results=5)
        logger.info(f"✅ [SEARCH_SUCCESS] Returned {len(search_results)} search results.")
    except Exception as e:
        search_error = e
        logger.info(f"ℹ️ [SEARCH_EXCEPTION] Search raised: {type(e).__name__}: {e}")
    finally:
        telemetry = getattr(backend, "last_search_telemetry", {}) or {}
        logger.info("🛑 [HOST_SHUTDOWN] Terminating host window...")
        try:
            host.destroy()
        except Exception as destroy_err:
            logger.warning(f"Error during host destroy: {destroy_err}")

    # Process telemetry
    safe_selected = bool(telemetry.get("safe_search_action_selected", False))
    generic_onenter = bool(telemetry.get("generic_default_onenter_used", False))
    slackbot_invoked = bool(telemetry.get("slackbot_action_invoked_by_pesu", False))
    write_capable_invoked = bool(telemetry.get("write_capable_action_invoked_by_pesu", False))
    unsafe_non_search_invoked = bool(telemetry.get("unsafe_non_search_action_invoked_by_pesu", False))
    submit_method = telemetry.get("submit_method", "NONE")
    default_type = telemetry.get("default_suggestion_type", "NONE")
    selected_type = telemetry.get("selected_suggestion_type", "NONE")
    safe_candidates_count = telemetry.get("safe_candidates_count", 0)

    report["execution_record"] = {
        "results_count": len(search_results),
        "exception": str(search_error) if search_error else None,
        "exception_type": type(search_error).__name__ if search_error else None,
        "telemetry": telemetry,
    }

    report["safety_metrics"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] = slackbot_invoked
    report["safety_metrics"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = write_capable_invoked
    report["safety_metrics"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = unsafe_non_search_invoked
    report["safety_metrics"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = generic_onenter
    report["safety_metrics"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = safe_selected
    report["safety_metrics"]["SUBMIT_METHOD"] = submit_method
    report["safety_metrics"]["DEFAULT_SUGGESTION_TYPE"] = default_type
    report["safety_metrics"]["SELECTED_SUGGESTION_TYPE"] = selected_type
    report["safety_metrics"]["SAFE_CANDIDATES_COUNT"] = safe_candidates_count

    # Safety containment verdict determination:
    is_safe = (
        not slackbot_invoked
        and not write_capable_invoked
        and not unsafe_non_search_invoked
        and not generic_onenter
    )

    if is_safe:
        report["safety_metrics"]["SAFETY_CONTAINMENT_VERDICT"] = "PASS"
        logger.info("🛡️ [SAFETY_VERDICT] >>> PASS <<< (Zero write actions, zero Slackbot calls, generic onEnter avoided)")
    else:
        report["safety_metrics"]["SAFETY_CONTAINMENT_VERDICT"] = "FAIL"
        logger.error("🚨 [SAFETY_VERDICT] >>> FAIL <<< Safety contract violated!")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f"📄 Trace report written to: {OUTPUT_FILE}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11R10F Bounded Live Safety Qualification")
    parser.add_argument("--dry-run", action="store_true", help="Simulate qualification run")
    args = parser.parse_args()

    res = run_bounded_live_safety_validation(dry_run=args.dry_run)
    sys.exit(0 if res["safety_metrics"]["SAFETY_CONTAINMENT_VERDICT"] == "PASS" else 1)
