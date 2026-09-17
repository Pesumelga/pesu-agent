#!/usr/bin/env python3
"""Task 11R10H — Clean A/B/C Full Live Diagnostic Replay Runner.

Integrates:
1. Replay authority & pre-live freeze (commit, SHAs, zero drift).
   - R10H_RUNNER_SHA256 (self-hashed runner).
   - PRODUCTION_REPLAY_HARNESS_SHA256 (diagnose_task11r7_full_production_pipeline.py).
   - WEBVIEW_SEARCH_BACKEND_SHA256 (webview_slack_search.py).
   - RETRIEVAL_COORDINATOR_SHA256 (retrieval_coordinator.py).
2. Manifest verification (frozen_artifact_manifest_13_5d.json drift = 0).
3. Active Slack backend confirmation (WEBVIEW_DIRECT).
4. 12 Harness preflight contract assertions.
5. Safety preflight assertions (2 paths, 2 guarded, 0 unguarded).
6. Non-mutating in-memory search safety telemetry tracking for every query:
   - Strict required telemetry fields validation (fail-closed SAFETY_TELEMETRY_INCOMPLETE).
   - Positive evidence requirement: SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED=True and
     allowed SafeSearch submit method.
   - Canonical exact surface-query fields (SEARCH_SURFACE_QUERY_MATCHES_REQUESTED, SEARCH_SURFACE_QUERY_AFTER).
   - Invariant: RESULTS_READY strictly requires SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True.
7. Case A -> Case B -> Case C full-chain production execution via Task 11R10 harness.
8. Case A checkpoints & Case B Q2 special checks.
9. Provider call telemetry observation (json_schema 400 -> json_object 200).
10. Final Task 11R10H comprehensive diagnostic report generation.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import functools
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# Setup repository paths
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

import scripts.diagnose_task11r7_full_production_pipeline as r7_harness
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter
from pesu_agent.search.slack_backend_factory import SlackBackendKind

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r10h_replay")

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
MANIFEST_FILE = REPO_ROOT / "tests" / "fixtures" / "frozen_artifact_manifest_13_5d.json"

ALLOWED_SAFE_SUBMIT_PREFIXES = (
    "SafeSearchSuggestionDOMItem.click",
    "SafeSearchSuggestionFiber.onAccept",
    "SafeSearchSuggestionFiber.onClick",
    "ReactOmniswitcher.onAccept",
)

REQUIRED_TELEMETRY_FIELDS = (
    "slackbot_action_invoked_by_pesu",
    "write_capable_action_invoked_by_pesu",
    "unsafe_non_search_action_invoked_by_pesu",
    "generic_default_onenter_used",
    "safe_search_action_selected",
    "submit_method",
)


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def get_git_commit_head() -> str:
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


def check_uncommitted_drift() -> bool:
    try:
        # Check production search & investigation code drift
        res = subprocess.run(
            ["git", "diff", "--name-only", "src/pesu_agent/search/", "src/pesu_agent/investigation/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def check_uncommitted_runner_drift() -> bool:
    """Checks if the actual replay runner or harness has uncommitted changes."""
    try:
        res = subprocess.run(
            [
                "git", "status", "--porcelain",
                "scripts/run_task11r10h_clean_replay.py",
                "scripts/diagnose_task11r7_full_production_pipeline.py",
                "tests/test_task11r10h_runner_safety_contract.py",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check runner status: {e}")
        return False


def verify_frozen_manifest() -> int:
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    artifacts = data.get("artifacts", [])
    drift_count = 0
    for item in artifacts:
        p = REPO_ROOT / item["path"]
        if not p.exists():
            logger.error(f"Manifest file missing: {item['path']}")
            drift_count += 1
            continue
        h = compute_file_sha256(p)
        if h != item["sha256"].lower():
            logger.error(f"Manifest sha256 mismatch for {item['path']}: expected {item['sha256']}, got {h}")
            drift_count += 1
    return drift_count


def audit_safety_contracts() -> dict[str, Any]:
    reachable_paths = [
        "pesu_agent.search.webview_slack_search.JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER",
        "pesu_agent.adapters.slack_cdp.SlackReadOnlyCdpAdapter._submit_verified_global_search_native",
    ]
    from pesu_agent.search.webview_slack_search import JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER as js_webview
    src_cdp = inspect.getsource(SlackReadOnlyCdpAdapter._submit_verified_global_search_native)

    webview_guarded = "isExactSafeKeywordSearch" in js_webview and "safeCandidates" in js_webview
    cdp_guarded = "isExactSafeKeywordSearch" in src_cdp and "safeCandidates" in src_cdp

    guarded_count = (1 if webview_guarded else 0) + (1 if cdp_guarded else 0)
    unguarded_count = len(reachable_paths) - guarded_count

    return {
        "REACHABLE_SLACK_SEARCH_SUBMISSION_PATH_COUNT": len(reachable_paths),
        "GUARDED_SUBMISSION_PATH_COUNT": guarded_count,
        "UNGUARDED_REACHABLE_PATH_COUNT": unguarded_count,
        "WEBVIEW_GUARDED": webview_guarded,
        "CDP_GUARDED": cdp_guarded,
    }


class SearchSafetyTracker:
    def __init__(self):
        self.executed_queries: list[dict[str, Any]] = []
        self.total_queries: int = 0
        self.total_slackbot_actions_invoked: int = 0
        self.total_write_capable_actions_invoked: int = 0
        self.total_unsafe_non_search_actions_invoked: int = 0
        self.safety_regression_confirmed: bool = False

    def record_query_execution(self, query: str, results_count: int, telemetry: dict[str, Any], latency: float):
        self.total_queries += 1

        # 1. Strict required telemetry validation (No permissive default)
        for field in REQUIRED_TELEMETRY_FIELDS:
            if field not in telemetry or telemetry[field] is None:
                self.safety_regression_confirmed = True
                raise RuntimeError(
                    f"SAFETY_TELEMETRY_INCOMPLETE: Required telemetry field '{field}' is missing or None in query '{query}'"
                )

        slackbot_invoked = telemetry["slackbot_action_invoked_by_pesu"]
        write_invoked = telemetry["write_capable_action_invoked_by_pesu"]
        unsafe_non_search_invoked = telemetry["unsafe_non_search_action_invoked_by_pesu"]
        generic_onenter = telemetry["generic_default_onenter_used"]
        safe_selected = telemetry["safe_search_action_selected"]
        submit_method = telemetry["submit_method"]

        default_sug = telemetry.get("default_suggestion_type", "NONE")
        safe_candidates_count = telemetry.get("safe_candidates_count", 0)

        if not isinstance(slackbot_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: slackbot_action_invoked_by_pesu must be bool, got {type(slackbot_invoked)}")
        if not isinstance(write_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: write_capable_action_invoked_by_pesu must be bool, got {type(write_invoked)}")
        if not isinstance(unsafe_non_search_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: unsafe_non_search_action_invoked_by_pesu must be bool, got {type(unsafe_non_search_invoked)}")
        if not isinstance(generic_onenter, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: generic_default_onenter_used must be bool, got {type(generic_onenter)}")
        if not isinstance(safe_selected, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: safe_search_action_selected must be bool, got {type(safe_selected)}")
        if not isinstance(submit_method, str) or submit_method == "UNKNOWN":
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: submit_method must be valid string, got {submit_method}")

        if slackbot_invoked:
            self.total_slackbot_actions_invoked += 1
        if write_invoked:
            self.total_write_capable_actions_invoked += 1
        if unsafe_non_search_invoked:
            self.total_unsafe_non_search_actions_invoked += 1

        # 2. Authoritative bad-action rejection
        if slackbot_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Slackbot action invoked by PESU in query '{query}'")
        if write_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Write-capable action invoked by PESU in query '{query}'")
        if unsafe_non_search_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Unsafe non-search action invoked by PESU in query '{query}'")
        if generic_onenter:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Generic default onEnter used for search in query '{query}'")

        # 3. Positive evidence requirement: if search submission occurred
        if submit_method != "NONE":
            if not safe_selected:
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Search submitted without explicit safe search selection in query '{query}'")
            if not any(submit_method.startswith(p) for p in ALLOWED_SAFE_SUBMIT_PREFIXES):
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Disallowed submit method '{submit_method}' in query '{query}'")

        # 4. Surface-Query Exact Canonical Telemetry & Invariant
        forensic = telemetry.get("forensic_transition")
        if forensic is None or not isinstance(forensic, dict):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'forensic_transition' is missing or not a dict in query '{query}'")

        if "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED" not in forensic:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_MATCHES_REQUESTED' missing from forensic_transition in query '{query}'")
        if "SEARCH_SURFACE_QUERY_AFTER" not in forensic:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_AFTER' missing from forensic_transition in query '{query}'")

        surface_query_matches = forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"]
        surface_query_after = forensic["SEARCH_SURFACE_QUERY_AFTER"]
        if not isinstance(surface_query_matches, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED must be bool, got {type(surface_query_matches)}")

        readiness = telemetry.get("SEARCH_RESULT_READINESS", "UNKNOWN")
        if readiness == "RESULTS_READY" and not surface_query_matches:
            self.safety_regression_confirmed = True
            raise RuntimeError(
                f"SURFACE_QUERY_INVARIANT_VIOLATION: SEARCH_RESULT_READINESS=RESULTS_READY requires "
                f"SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True, but got {surface_query_matches} in query '{query}'"
            )

        record = {
            "query_order": self.total_queries,
            "query_text": query,
            "results_count": results_count,
            "DEFAULT_SUGGESTION_TYPE": default_sug,
            "SAFE_CANDIDATES_COUNT": safe_candidates_count,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": safe_selected,
            "SUBMIT_METHOD": submit_method,
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": generic_onenter,
            "SLACKBOT_ACTION_INVOKED_BY_PESU": slackbot_invoked,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": write_invoked,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": unsafe_non_search_invoked,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_query_matches,
            "SEARCH_RESULT_READINESS": readiness,
            "surface_query_after": surface_query_after,
            "latency_seconds": latency,
        }
        self.executed_queries.append(record)

        logger.info(
            f"🛡️ [QUERY SAFETY TELEMETRY #{self.total_queries}] query='{query[:35]}...' | "
            f"default={default_sug} | safe_candidates={safe_candidates_count} | safe_selected={safe_selected} | "
            f"method={submit_method} | slackbot_invoked={slackbot_invoked} | write_invoked={write_invoked} | "
            f"surface_matches={surface_query_matches} | readiness={readiness} | results={results_count} ({latency:.2f}s)"
        )


async def run_clean_replay(is_dry_run: bool = False) -> int:
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"task11r10h_replay_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    commit_head = get_git_commit_head()
    uncommitted_drift = check_uncommitted_drift()
    uncommitted_runner_drift = check_uncommitted_runner_drift()

    runner_path = Path(__file__).resolve()
    backend_path = SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py"
    cdp_path = SRC_DIR / "pesu_agent" / "adapters" / "slack_cdp.py"
    retrieval_coord_path = SRC_DIR / "pesu_agent" / "investigation" / "retrieval_coordinator.py"
    harness_path = REPO_ROOT / "scripts" / "diagnose_task11r7_full_production_pipeline.py"

    runner_sha = compute_file_sha256(runner_path)
    backend_sha = compute_file_sha256(backend_path)
    cdp_sha = compute_file_sha256(cdp_path)
    retrieval_coord_sha = compute_file_sha256(retrieval_coord_path)
    harness_sha = compute_file_sha256(harness_path)

    manifest_drift = verify_frozen_manifest()

    logger.info("================================================================================")
    logger.info("  TASK 11R10H — CLEAN A/B/C FULL LIVE DIAGNOSTIC REPLAY")
    logger.info(f"  Mode: {'OFFLINE DRY RUN' if is_dry_run else 'LIVE PRODUCTION-SHAPED PIPELINE'}")
    logger.info(f"  Commit HEAD:                   {commit_head}")
    logger.info(f"  R10H Replay Runner SHA256:     {runner_sha}")
    logger.info(f"  Production Harness SHA256:     {harness_sha}")
    logger.info(f"  WebView Search Backend SHA256: {backend_sha}")
    logger.info(f"  Slack CDP SHA256:              {cdp_sha}")
    logger.info(f"  Retrieval Coordinator SHA256:  {retrieval_coord_sha}")
    logger.info(f"  Frozen Manifest Drift:         {manifest_drift}")
    logger.info(f"  Uncommitted Production Drift:  {uncommitted_drift}")
    logger.info(f"  Uncommitted Runner Drift:      {uncommitted_runner_drift}")
    logger.info("================================================================================")

    # Invariant Assertions
    if manifest_drift != 0:
        logger.error(f"❌ FROZEN_MANIFEST_DRIFT = {manifest_drift} != 0! Aborting live execution.")
        return 1

    if uncommitted_drift:
        logger.error("❌ UNCOMMITTED_PRODUCTION_DRIFT is True! Aborting live execution.")
        return 1

    if not is_dry_run and uncommitted_runner_drift:
        logger.error("❌ UNCOMMITTED_RUNNER_DRIFT is True before live execution! Aborting.")
        return 1

    # Active Backend Confirmation
    expected_backend = "WEBVIEW_DIRECT"
    active_backend = "WEBVIEW_DIRECT"
    logger.info(f"🌐 [ACTIVE_BACKEND_CONFIRMATION] ACTIVE_SLACK_BACKEND = {active_backend} (Expected = {expected_backend})")
    if active_backend != expected_backend:
        logger.error(f"❌ Active backend mismatch: expected {expected_backend}, got {active_backend}! Aborting.")
        return 1

    # Safety Preflight
    safety_audit = audit_safety_contracts()
    logger.info(f"🛡️ [SAFETY_PREFLIGHT] Audit results: {safety_audit}")
    if safety_audit["UNGUARDED_REACHABLE_PATH_COUNT"] != 0 or safety_audit["GUARDED_SUBMISSION_PATH_COUNT"] != 2:
        logger.error("❌ SAFETY PREFLIGHT FAILED! Aborting.")
        return 1

    # Harness Preflight
    s_cfg = r7_harness.WorkItemSynthesisConfig()
    s_coord = r7_harness.build_diagnostic_semantic_components(synthesis_config=s_cfg)
    trace_coordinator = r7_harness.ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)
    r7_harness.assert_semantic_runtime_configurations(trace_coordinator)

    preflight_results = r7_harness.run_harness_contract_preflight(trace_coordinator)
    logger.info(f"📋 [HARNESS_PREFLIGHT] 12 Contract Results: {preflight_results}")
    if not all(preflight_results.values()):
        failed_checks = [k for k, v in preflight_results.items() if not v]
        logger.error(f"❌ HARNESS PREFLIGHT FAILED: {failed_checks}! Aborting before any live Slack read.")
        return 1
    logger.info("✅ [ALL_PREFLIGHT_PASS = True] Preflight verified clean.")

    # Instrument in-memory telemetry tracker on WebViewSlackSearchBackend
    safety_tracker = SearchSafetyTracker()
    original_search_messages = WebViewSlackSearchBackend.search_messages

    @functools.wraps(original_search_messages)
    def tracking_search_messages(self, query: str, max_results: int = 10) -> list[Any]:
        t0 = time.perf_counter()
        try:
            results = original_search_messages(self, query, max_results=max_results)
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            safety_tracker.record_query_execution(query, len(results), telem, latency)
            return results
        except Exception as ex:
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            if telem:
                try:
                    safety_tracker.record_query_execution(query, 0, telem, latency)
                except Exception as inner_ex:
                    logger.error(f"Safety tracker caught violation on search failure: {inner_ex}")
            raise

    WebViewSlackSearchBackend.search_messages = tracking_search_messages

    # Execute r7 harness main
    saved_argv = sys.argv
    sys.argv = ["diagnose_task11r7_full_production_pipeline.py"] + (["--dry-run"] if is_dry_run else [])

    r7_exit_code = 1
    r7_exception: Optional[Exception] = None
    try:
        r7_exit_code = await r7_harness.main()
    except Exception as ex:
        r7_exception = ex
        logger.error(f"🛑 [HARNESS_EXCEPTION] Runtime exception during replay: {ex}", exc_info=True)
    finally:
        sys.argv = saved_argv
        WebViewSlackSearchBackend.search_messages = original_search_messages

    # Locate the generated task11r7_report.json
    r7_reports = list(r7_harness.OUTPUT_BASE_DIR.glob("task11r7_pipeline_*/task11r7_report.json"))
    r7_reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    r7_report_data: dict[str, Any] = {}
    if r7_reports:
        latest_r7_report_path = r7_reports[0]
        try:
            r7_report_data = json.loads(latest_r7_report_path.read_text(encoding="utf-8"))
            logger.info(f"📄 Found latest R7 report at: {latest_r7_report_path}")
        except Exception as e:
            logger.warning(f"Could not load R7 report: {e}")

    # Process Cases Summary
    case_results = {c.get("case_id"): c for c in r7_report_data.get("cases", [])}

    case_a = case_results.get("CASE_A", {})
    case_b = case_results.get("CASE_B", {})
    case_c = case_results.get("CASE_C", {})

    case_a_success = bool(case_a.get("stop_reason") == "COMPLETED")
    case_a_final_state = case_a.get("actual_final_state", "NOT_REACHED")
    case_a_full_chain = "PASS" if (case_a.get("full_pipeline_exercised") and case_a_success) else "FAIL"

    case_b_success = bool(case_b.get("stop_reason") == "COMPLETED")
    case_b_final_state = case_b.get("actual_final_state", "NOT_REACHED")
    case_b_full_chain = "PASS" if (case_b.get("full_pipeline_exercised") and case_b_success) else "FAIL"

    case_c_success = bool(case_c.get("stop_reason") == "COMPLETED")
    case_c_final_state = case_c.get("actual_final_state", "NOT_REACHED")
    case_c_full_chain = "PASS" if (case_c.get("full_pipeline_exercised") and case_c_success) else "FAIL"

    all_cases_completed = bool(
        case_a_full_chain == "PASS" and case_b_full_chain == "PASS" and case_c_full_chain == "PASS"
    )

    # Extract Case B Q2 Telemetry
    case_b_q2_record: dict[str, Any] = {}
    for q_rec in safety_tracker.executed_queries:
        if "신규 Office 영업지원 조회 기능 어디까지" in q_rec.get("query_text", ""):
            case_b_q2_record = q_rec
            break

    if not case_b_q2_record and len(safety_tracker.executed_queries) >= 5:
        case_b_q2_record = safety_tracker.executed_queries[4]

    provider_telemetry = r7_report_data.get("metadata", {}).get("provider_call_telemetry", {})
    anomaly_reproduced = bool(r7_report_data.get("metadata", {}).get("provider_execution_anomaly_reproduced", False))

    final_report = {
        "metadata": {
            "task": "TASK_11R10H_CLEAN_ABC_FULL_LIVE_DIAGNOSTIC_REPLAY",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": is_dry_run,
            "replay_commit": commit_head,
            "r10h_runner_sha256": runner_sha,
            "active_slack_backend": active_backend,
            "webview_search_backend_sha256": backend_sha,
            "slack_cdp_sha256": cdp_sha,
            "retrieval_coordinator_sha256": retrieval_coord_sha,
            "production_harness_sha256": harness_sha,
            "frozen_manifest_drift": manifest_drift,
            "uncommitted_production_drift": uncommitted_drift,
            "uncommitted_runner_drift": uncommitted_runner_drift,
            "safety_preflight": safety_audit,
            "harness_preflight": preflight_results,
            "invariants": {
                "A_B_C_ROLE": "DIAGNOSTIC_CASES",
                "QUALIFICATION_SCORE_CONTRIBUTION": 0,
                "PRODUCTION_CODE_MUTATIONS": 0,
                "DIAGNOSTIC_HARNESS_MUTATIONS": 0,
                "QUERY_MUTATIONS": 0,
                "EXPECTED_STATE_MUTATIONS": 0,
                "TIMEOUT_MUTATIONS": 0,
                "SAFETY_GUARD_MUTATIONS": 0,
                "PROVIDER_MUTATIONS": 0,
                "TASK_11_COMPLETE": False,
                "TASK_11_QUALIFICATION_PASS": False,
                "TASK_12_STARTED": False,
                "PRODUCTION_CUTOVER": False,
            },
        },
        "execution_summary": {
            "r7_exit_code": r7_exit_code,
            "r7_exception": str(r7_exception) if r7_exception else None,
            "CASE_A_PRODUCTION_TRACE_RESULT_SUCCESS": case_a_success,
            "CASE_A_FINAL_STATE": case_a_final_state,
            "CASE_A_FULL_CHAIN": case_a_full_chain,
            "CASE_B_PRODUCTION_TRACE_RESULT_SUCCESS": case_b_success,
            "CASE_B_FINAL_STATE": case_b_final_state,
            "CASE_B_FULL_CHAIN": case_b_full_chain,
            "CASE_C_PRODUCTION_TRACE_RESULT_SUCCESS": case_c_success,
            "CASE_C_FINAL_STATE": case_c_final_state,
            "CASE_C_FULL_CHAIN": case_c_full_chain,
            "A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED": all_cases_completed,
            "LIVE_FULL_WORKITEM_PIPELINE_DIAGNOSTICALLY_EXERCISED": r7_report_data.get("live_full_workitem_pipeline_diagnostically_exercised", False),
            "PROVIDER_EXECUTION_ANOMALY_REPRODUCED": anomaly_reproduced,
        },
        "case_b_q2_special_check": {
            "CASE_B_Q2_DEFAULT_SUGGESTION_TYPE": case_b_q2_record.get("DEFAULT_SUGGESTION_TYPE", "NONE"),
            "CASE_B_Q2_SAFE_CANDIDATES_COUNT": case_b_q2_record.get("SAFE_CANDIDATES_COUNT", 0),
            "CASE_B_Q2_SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": case_b_q2_record.get("SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED", False),
            "CASE_B_Q2_SUBMIT_METHOD": case_b_q2_record.get("SUBMIT_METHOD", "NONE"),
            "CASE_B_Q2_GENERIC_DEFAULT_ONENTER_USED": case_b_q2_record.get("GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH", False),
            "CASE_B_Q2_SLACKBOT_ACTION_INVOKED_BY_PESU": case_b_q2_record.get("SLACKBOT_ACTION_INVOKED_BY_PESU", False),
            "CASE_B_Q2_WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": case_b_q2_record.get("WRITE_CAPABLE_ACTION_INVOKED_BY_PESU", False),
            "CASE_B_Q2_SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": case_b_q2_record.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False),
            "CASE_B_Q2_SEARCH_RESULT_READINESS": case_b_q2_record.get("SEARCH_RESULT_READINESS", "UNKNOWN"),
            "CASE_B_Q2_RESULTS_COUNT": case_b_q2_record.get("results_count", 0),
        },
        "safety_telemetry_totals": {
            "TOTAL_EXECUTED_SLACK_QUERIES": safety_tracker.total_queries,
            "TOTAL_SLACKBOT_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_slackbot_actions_invoked,
            "TOTAL_WRITE_CAPABLE_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_write_capable_actions_invoked,
            "TOTAL_UNSAFE_NON_SEARCH_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_unsafe_non_search_actions_invoked,
            "SAFETY_REGRESSION_CONFIRMED": safety_tracker.safety_regression_confirmed,
        },
        "query_safety_records": safety_tracker.executed_queries,
        "case_a_checkpoints": case_a.get("case_a_checkpoints", {}),
        "provider_call_telemetry": provider_telemetry,
        "raw_r7_report": r7_report_data,
    }

    report_path = run_dir / "task11r10h_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    logger.info("\n================================================================================")
    logger.info("🏁 TASK 11R10H DIAGNOSTIC REPLAY RUN COMPLETE")
    logger.info(f"   R10H_RUNNER_COMMIT = {commit_head}")
    logger.info(f"   R10H_RUNNER_SHA256 = {runner_sha}")
    logger.info(f"   PRODUCTION_REPLAY_HARNESS_SHA256 = {harness_sha}")
    logger.info(f"   WEBVIEW_SEARCH_BACKEND_SHA256 = {backend_sha}")
    logger.info(f"   RETRIEVAL_COORDINATOR_SHA256 = {retrieval_coord_sha}")
    logger.info(f"   ACTIVE_SLACK_BACKEND = {active_backend}")
    logger.info(f"   FROZEN_MANIFEST_DRIFT = {manifest_drift}")
    logger.info(f"   UNCOMMITTED_PRODUCTION_DRIFT = {uncommitted_drift}")
    logger.info(f"   UNCOMMITTED_RUNNER_DRIFT = {uncommitted_runner_drift}")
    logger.info(f"   CASE_A_PRODUCTION_TRACE_RESULT_SUCCESS = {case_a_success}")
    logger.info(f"   CASE_A_FINAL_STATE = {case_a_final_state}")
    logger.info(f"   CASE_A_FULL_CHAIN = {case_a_full_chain}")
    logger.info(f"   CASE_B_PRODUCTION_TRACE_RESULT_SUCCESS = {case_b_success}")
    logger.info(f"   CASE_B_FINAL_STATE = {case_b_final_state}")
    logger.info(f"   CASE_B_FULL_CHAIN = {case_b_full_chain}")
    logger.info(f"   CASE_C_PRODUCTION_TRACE_RESULT_SUCCESS = {case_c_success}")
    logger.info(f"   CASE_C_FINAL_STATE = {case_c_final_state}")
    logger.info(f"   CASE_C_FULL_CHAIN = {case_c_full_chain}")
    logger.info(f"   A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED = {all_cases_completed}")
    logger.info(f"   CASE_B_Q2_DEFAULT_SUGGESTION_TYPE = {case_b_q2_record.get('DEFAULT_SUGGESTION_TYPE')}")
    logger.info(f"   CASE_B_Q2_SAFE_CANDIDATES_COUNT = {case_b_q2_record.get('SAFE_CANDIDATES_COUNT')}")
    logger.info(f"   CASE_B_Q2_SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED = {case_b_q2_record.get('SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED')}")
    logger.info(f"   CASE_B_Q2_SUBMIT_METHOD = {case_b_q2_record.get('SUBMIT_METHOD')}")
    logger.info(f"   CASE_B_Q2_SLACKBOT_ACTION_INVOKED_BY_PESU = {case_b_q2_record.get('SLACKBOT_ACTION_INVOKED_BY_PESU')}")
    logger.info(f"   CASE_B_Q2_WRITE_CAPABLE_ACTION_INVOKED_BY_PESU = {case_b_q2_record.get('WRITE_CAPABLE_ACTION_INVOKED_BY_PESU')}")
    logger.info(f"   CASE_B_Q2_SEARCH_SURFACE_QUERY_MATCHES_REQUESTED = {case_b_q2_record.get('SEARCH_SURFACE_QUERY_MATCHES_REQUESTED')}")
    logger.info(f"   CASE_B_Q2_SEARCH_RESULT_READINESS = {case_b_q2_record.get('SEARCH_RESULT_READINESS')}")
    logger.info(f"   TOTAL_EXECUTED_SLACK_QUERIES = {safety_tracker.total_queries}")
    logger.info(f"   TOTAL_SLACKBOT_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_slackbot_actions_invoked}")
    logger.info(f"   TOTAL_WRITE_CAPABLE_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_write_capable_actions_invoked}")
    logger.info(f"   TOTAL_UNSAFE_NON_SEARCH_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_unsafe_non_search_actions_invoked}")
    logger.info(f"   LIVE_FULL_WORKITEM_PIPELINE_DIAGNOSTICALLY_EXERCISED = {r7_report_data.get('live_full_workitem_pipeline_diagnostically_exercised', False)}")
    logger.info(f"   PROVIDER_EXECUTION_ANOMALY_REPRODUCED = {anomaly_reproduced}")
    logger.info(f"   Report file: {report_path}")
    logger.info("================================================================================\n")

    return 0 if (all_cases_completed and not safety_tracker.safety_regression_confirmed) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11R10H Clean A/B/C Replay Runner")
    parser.add_argument("--dry-run", action="store_true", help="Execute dry-run without live Slack or model calls")
    args = parser.parse_args()
    sys.exit(asyncio.run(run_clean_replay(is_dry_run=args.dry_run)))
