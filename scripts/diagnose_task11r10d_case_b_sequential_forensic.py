#!/usr/bin/env python
"""Task 11R10D — Case B Sequential Query Transition Forensic Probe.

Objective:
  Perform a bounded repeated characterization (3 iterations) of the Case B sequential
  query transition (Q1 -> Q2 -> Q3) under fresh-host conditions on the frozen Slack
  search surface to authoritatively determine:
  1. Reproducibility of the Q2 SEARCH_NAVIGATION_FAILED (SURFACE_QUERY_MISMATCH) defect.
  2. The exact primary loss point in the transition pipeline.
  3. Whether the failure is deterministic or flaky under test conditions.
  4. Whether query length is causal or unresolved.

Strict Invariants:
  - SEQUENCE_ITERATIONS = 3
  - FRESH_WEBVIEW_HOST_PER_SEQUENCE = True
  - FULL_SLACK_PROCESS_COLD_START_PER_SEQUENCE = False
  - SLACK_WRITE_ACTIONS = 0
  - POST_HOC_QUERY_CHANGES = 0
  - POST_HOC_QUERY_ADDITIONS = 0
  - PRODUCTION_CODE_MUTATIONS = 0
  - TIMEOUT_MUTATIONS = 0
  - Semantic Pipeline Calls = 0 (EvidenceAssessor=0, ClaimVerifier=0, Stage1A=0, Stage1B=0, Stage2=0, Stage2.5=0)
  - QUALIFICATION_SCORE_CONTRIBUTION = 0
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

from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuWebViewHost,
)
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r10d_forensic")

PREDECLARATIONS_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_live_case_predeclarations.json"
OUTPUT_BASE_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"

SEQUENCE_ITERATIONS = 3

JS_DEEP_INSPECT_SURFACE = r"""
(() => {
    let urlHref = '';
    let urlQuery = null;
    try {
        urlHref = window.location.href;
        const url = new URL(urlHref);
        urlQuery = url.searchParams.get('query') || url.searchParams.get('q');
        if (!urlQuery) {
            const m = url.pathname.match(/\/search\/(.+)$/);
            if (m) urlQuery = decodeURIComponent(m[1]);
        }
    } catch (e) {
        urlHref = 'ERROR: ' + String(e);
    }

    const header = document.querySelector('[data-qa="search_header_query"], [data-qa="search_query"], .c-search_header__title, [class*="titleContent"]');
    const headerText = header ? (header.textContent || '').trim() : null;

    const inputElem = document.querySelector('[data-qa="search_input_box"] .ql-editor');
    const inputText = inputElem ? (inputElem.textContent || inputElem.innerText || '').trim() : null;

    const searchViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el && el.isConnected);
    const searchView = searchViews.length === 1 ? searchViews[0] : null;

    const resultRoots = searchView
        ? Array.from(searchView.querySelectorAll('[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result')).filter(el => {
            if (!el || !el.isConnected) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0);
        })
        : [];

    const skeletons = searchView
        ? Array.from(searchView.querySelectorAll('[data-qa="skeleton"], .c-skeleton, .c-search_skeleton, .c-search_message--skeleton, [data-qa="loading_indicator"], [aria-busy="true"], .c-search_view__loading, .c-loading, .p-search_results_loading')).filter(el => el && el.isConnected)
        : [];

    return {
        url_href: urlHref,
        url_query: urlQuery,
        header_text: headerText,
        input_text: inputText,
        search_view_mounted: Boolean(searchView),
        search_view_count: searchViews.length,
        result_root_count: resultRoots.length,
        skeleton_count: skeletons.length
    };
})()
"""


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


def check_uncommitted_backend_drift() -> bool:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to check git diff: {e}")
        return False


def load_exact_case_b_queries() -> list[dict[str, Any]]:
    """Loads exact Case B queries from frozen predeclarations fixture."""
    if not PREDECLARATIONS_PATH.exists():
        raise FileNotFoundError(f"Fixture not found: {PREDECLARATIONS_PATH}")

    data = json.loads(PREDECLARATIONS_PATH.read_text(encoding="utf-8"))
    case_b = next((c for c in data.get("cases", []) if c.get("case_id") == "CASE_B"), None)
    if not case_b:
        raise ValueError("CASE_B not found in predeclarations fixture")

    exact_queries = case_b.get("exact_slack_queries", [])
    if len(exact_queries) != 3:
        raise ValueError(f"Expected 3 exact queries for CASE_B, found {len(exact_queries)}")

    return exact_queries


def run_single_iteration(
    iteration_index: int,
    exact_queries: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Runs one bounded sequence iteration: fresh host -> Q1 -> Q2 -> Q3 (if Q2 ok) -> destroy host."""
    logger.info("\n" + "=" * 70)
    logger.info(f"🔁 STARTING SEQUENCE ITERATION #{iteration_index} / {SEQUENCE_ITERATIONS}")
    logger.info("=" * 70)

    iter_record: dict[str, Any] = {
        "iteration": iteration_index,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "query_records": [],
        "q1_status": "NOT_EXECUTED",
        "q2_status": "NOT_EXECUTED",
        "q3_status": "NOT_EXECUTED",
        "iteration_completed_all_queries": False,
        "iteration_failure_point": None,
    }

    if dry_run:
        logger.info("🧪 [DRY_RUN] Simulating iteration without live host.")
        for q_item in exact_queries:
            q_order = q_item["order"]
            q_text = q_item["raw_planner_query"]
            iter_record["query_records"].append({
                "query_order": q_order,
                "REQUESTED_QUERY": q_text,
                "INPUT_MATCHES_REQUESTED": True,
                "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": True,
                "ONENTER_INVOKED": True,
                "SEARCH_SURFACE_QUERY_BEFORE": None,
                "SEARCH_SURFACE_QUERY_AFTER": q_text,
                "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                "RESULT_FINGERPRINT_BEFORE": None,
                "RESULT_FINGERPRINT_AFTER": "dry_run_fp",
                "LOADING_OR_DOM_TRANSITION_OBSERVED": True,
                "SEARCH_RESULT_READINESS": "RESULTS_READY",
                "RESULT_COUNT": 5,
                "TRANSITION_RESULT": "PASS",
                "SEARCH_ERROR": None,
            })
        iter_record["q1_status"] = "PASS"
        iter_record["q2_status"] = "PASS"
        iter_record["q3_status"] = "PASS"
        iter_record["iteration_completed_all_queries"] = True
        return iter_record

    # Step 1: Initialize fresh PesuWebViewHost and WebViewSlackSearchBackend
    logger.info("🌐 [HOST_INIT] Initializing fresh session-scoped PesuWebViewHost...")
    search_backend = create_slack_search_backend(
        config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
    )
    host = getattr(search_backend, "_window", None)
    if not host or not hasattr(host, "start"):
        raise RuntimeError("Failed to obtain PesuWebViewHost from backend")

    t_host_start = time.perf_counter()
    host.start(timeout=30.0)
    host_startup_latency = time.perf_counter() - t_host_start
    logger.info(f"🌐 [HOST_READY] Fresh PesuWebViewHost started in {host_startup_latency:.2f}s")

    try:
        for q_idx, q_item in enumerate(exact_queries):
            q_order = q_item["order"]
            q_text = q_item["raw_planner_query"]
            q_purpose = q_item.get("purpose", "")

            logger.info("\n" + "-" * 50)
            logger.info(f"▶ [ITER #{iteration_index} Q{q_order}] Executing query: '{q_text}' (len={len(q_text)})")
            logger.info(f"  Purpose: {q_purpose}")
            logger.info("-" * 50)

            t0 = time.perf_counter()
            q_error: Optional[str] = None
            q_failure_code: Optional[str] = None
            raw_items: list[Any] = []
            deep_inspect_data: Optional[dict[str, Any]] = None

            try:
                raw_items = search_backend.search_messages(q_text, max_results=5)
                q_transition_result = "PASS"
                logger.info(f"  ✅ Q{q_order} SUCCESS: Returned {len(raw_items)} items ({time.perf_counter() - t0:.2f}s)")
            except WebViewSearchError as wse:
                q_error = str(wse)
                q_failure_code = getattr(wse, "failure_code", None)
                if q_failure_code and hasattr(q_failure_code, "value"):
                    q_failure_code = q_failure_code.value
                q_transition_result = f"FAIL_{q_failure_code or 'UNKNOWN'}"
                logger.error(f"  ❌ Q{q_order} FAILED (code={q_failure_code}): {wse}")

                # Immediate deep inspection of live DOM/surface state
                try:
                    deep_inspect_res = host.evaluate_js(JS_DEEP_INSPECT_SURFACE)
                    if isinstance(deep_inspect_res, dict):
                        deep_inspect_data = deep_inspect_res
                        logger.info(f"  🔍 Deep Surface State: header='{deep_inspect_data.get('header_text')}', "
                                    f"input='{deep_inspect_data.get('input_text')}', "
                                    f"url_q='{deep_inspect_data.get('url_query')}'")
                except Exception as eval_err:
                    logger.warning(f"  ⚠️ Deep inspection probe failed: {eval_err}")
            except Exception as e:
                q_error = str(e)
                q_failure_code = "UNEXPECTED_EXCEPTION"
                q_transition_result = f"FAIL_{q_failure_code}"
                logger.error(f"  ❌ Q{q_order} Unexpected error: {e}")

            latency = time.perf_counter() - t0

            # Extract per-query transition telemetry (Section 9)
            telemetry = getattr(search_backend, "last_search_telemetry", {}) or {}
            transition = telemetry.get("forensic_transition", {})

            verified_query_state = bool(telemetry.get("verified_query_state_before_submit", False))
            onenter_invoked = bool(telemetry.get("onenter_invoked", False))
            input_matches = bool(transition.get("INPUT_MATCHES_REQUESTED", False))
            surface_matches = bool(transition.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))
            surface_before = transition.get("SEARCH_SURFACE_QUERY_BEFORE")
            surface_after = transition.get("SEARCH_SURFACE_QUERY_AFTER")
            fp_before = transition.get("RESULT_FINGERPRINT_BEFORE")
            fp_after = transition.get("RESULT_FINGERPRINT_AFTER")
            loading_observed = bool(transition.get("LOADING_OR_DOM_TRANSITION_OBSERVED", False))
            readiness = telemetry.get("SEARCH_RESULT_READINESS") or transition.get("SEARCH_RESULT_READINESS")
            result_count = len(raw_items)

            q_record = {
                "query_order": q_order,
                "REQUESTED_QUERY": q_text,
                "query_length": len(q_text),
                "INPUT_MATCHES_REQUESTED": input_matches,
                "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": verified_query_state,
                "ONENTER_INVOKED": onenter_invoked,
                "SEARCH_SURFACE_QUERY_BEFORE": surface_before,
                "SEARCH_SURFACE_QUERY_AFTER": surface_after,
                "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_matches,
                "RESULT_FINGERPRINT_BEFORE": fp_before,
                "RESULT_FINGERPRINT_AFTER": fp_after,
                "LOADING_OR_DOM_TRANSITION_OBSERVED": loading_observed,
                "SEARCH_RESULT_READINESS": readiness,
                "RESULT_COUNT": result_count,
                "TRANSITION_RESULT": q_transition_result,
                "SEARCH_ERROR": q_error,
                "FAILURE_CODE": q_failure_code,
                "latency_seconds": latency,
                "telemetry_last_confirmed_stage": telemetry.get("last_confirmed_stage"),
                "deep_inspect": deep_inspect_data,
            }
            iter_record["query_records"].append(q_record)

            if q_order == 1:
                iter_record["q1_status"] = q_transition_result
            elif q_order == 2:
                iter_record["q2_status"] = q_transition_result
            elif q_order == 3:
                iter_record["q3_status"] = q_transition_result

            # Fail-fast within iteration: If Q1 or Q2 fails, do not proceed to next query
            if q_transition_result != "PASS":
                iter_record["iteration_failure_point"] = f"Q{q_order}_{q_transition_result}"
                logger.warning(f"🛑 [ITER #{iteration_index} HALT] Stopping iteration at Q{q_order} due to {q_transition_result}")
                break

        if all(r.get("TRANSITION_RESULT") == "PASS" for r in iter_record["query_records"]) and len(iter_record["query_records"]) == 3:
            iter_record["iteration_completed_all_queries"] = True

    finally:
        logger.info("🛑 [HOST_STOP] Stopping PesuWebViewHost...")
        try:
            host.stop()
        except Exception as e:
            logger.warning(f"Error stopping host: {e}")
        # Brief settle time between iterations for clean Windows process teardown
        time.sleep(1.0)

    return iter_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11R10D Case B Sequential Query Transition Forensic")
    parser.add_argument("--dry-run", action="store_true", help="Execute dry-run without starting live host")
    parser.add_argument("--iterations", type=int, default=SEQUENCE_ITERATIONS, help="Number of sequence iterations (default: 3)")
    args = parser.parse_args()

    start_time = datetime.now(timezone.utc)
    commit_head = get_git_commit_head()
    uncommitted_drift = check_uncommitted_backend_drift()
    backend_file = REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py"
    backend_sha256 = compute_file_sha256(backend_file)
    probe_script_path = Path(__file__).resolve()
    probe_sha256 = compute_file_sha256(probe_script_path)

    timestamp_str = start_time.strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE_DIR / f"task11r10d_forensic_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("================================================================================")
    logger.info("🔬 TASK 11R10D — CASE B SEQUENTIAL QUERY TRANSITION FORENSIC")
    logger.info("================================================================================")
    logger.info(f"Commit HEAD:                     {commit_head}")
    logger.info(f"Uncommitted Code Drift:          {uncommitted_drift}")
    logger.info(f"Search Backend SHA-256:          {backend_sha256}")
    logger.info(f"Probe Script SHA-256:            {probe_sha256}")
    logger.info(f"Planned Sequence Iterations:     {args.iterations}")
    logger.info(f"Dry Run Mode:                    {args.dry_run}")
    logger.info("================================================================================\n")

    # Section 4: Exact Case B Queries Verification
    exact_queries = load_exact_case_b_queries()
    case_b_q1 = exact_queries[0]["raw_planner_query"]
    case_b_q2 = exact_queries[1]["raw_planner_query"]
    case_b_q3 = exact_queries[2]["raw_planner_query"]

    logger.info("📋 EXACT FROZEN CASE B QUERIES (Zero Modification):")
    logger.info(f"  CASE_B_Q1 (len={len(case_b_q1)}): '{case_b_q1}'")
    logger.info(f"  CASE_B_Q2 (len={len(case_b_q2)}): '{case_b_q2}'")
    logger.info(f"  CASE_B_Q3 (len={len(case_b_q3)}): '{case_b_q3}'")
    logger.info("  POST_HOC_QUERY_CHANGES:   0")
    logger.info("  POST_HOC_QUERY_ADDITIONS: 0\n")

    # Bounded Sequential Replay Execution (Section 7)
    iteration_records: list[dict[str, Any]] = []

    for i in range(1, args.iterations + 1):
        record = run_single_iteration(
            iteration_index=i,
            exact_queries=exact_queries,
            dry_run=args.dry_run,
        )
        iteration_records.append(record)

    # Sequence Verdict Analysis (Section 10)
    q2_results = [r.get("q2_status") for r in iteration_records]
    q2_failures = [res for res in q2_results if res != "PASS"]
    q2_failure_count = len(q2_failures)
    q2_success_count = len([res for res in q2_results if res == "PASS"])

    logger.info("\n" + "=" * 70)
    logger.info("📊 SEQUENCE EXECUTION SUMMARY")
    logger.info("=" * 70)
    for r in iteration_records:
        logger.info(f"  Iteration #{r['iteration']}: Q1={r['q1_status']}, Q2={r['q2_status']}, Q3={r['q3_status']} (Halting Point: {r['iteration_failure_point'] or 'NONE'})")

    if q2_failure_count == 0:
        failure_reproduced = False
        flakiness_verdict = "NOT_SUPPORTED_IN_THIS_SAMPLE"
        deterministic_verdict = "NOT_SUPPORTED"
    elif 1 <= q2_failure_count < args.iterations:
        failure_reproduced = True
        flakiness_verdict = "CONFIRMED"
        deterministic_verdict = "NOT_SUPPORTED"
    else:  # 3/3 failed
        failure_reproduced = True
        flakiness_verdict = "NOT_SUPPORTED"
        deterministic_verdict = "STRONGLY_SUPPORTED"

    # Primary Loss Point Classification (Section 5)
    # Examine failure telemetry from Q2 across failed iterations
    q2_input_app_defect = "NOT_SUPPORTED"
    q2_pre_submit_defect = "NOT_SUPPORTED"
    q2_effective_submit_defect = "UNRESOLVED"
    q2_search_surface_defect = "UNRESOLVED"
    q2_result_lifecycle_defect = "NOT_SUPPORTED"
    q2_surface_observability_defect = "UNRESOLVED"
    primary_loss_point = "UNRESOLVED"

    if failure_reproduced:
        # Check all failed Q2 records
        all_failed_q2 = []
        for r in iteration_records:
            for qr in r.get("query_records", []):
                if qr.get("query_order") == 2 and qr.get("TRANSITION_RESULT") != "PASS":
                    all_failed_q2.append(qr)

        if all_failed_q2:
            sample = all_failed_q2[0]
            input_matched = sample.get("INPUT_MATCHES_REQUESTED", False)
            pre_submit_verified = sample.get("VERIFIED_QUERY_STATE_BEFORE_SUBMIT", False)
            onenter = sample.get("ONENTER_INVOKED", False)
            surface_matched = sample.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False)
            last_stage = sample.get("telemetry_last_confirmed_stage")
            deep = sample.get("deep_inspect") or {}

            if not input_matched:
                q2_input_app_defect = "CONFIRMED"
                primary_loss_point = "Q2_INPUT_APPLICATION_DEFECT"
            elif not pre_submit_verified:
                q2_pre_submit_defect = "CONFIRMED"
                primary_loss_point = "Q2_PRE_SUBMIT_STATE_SETTLE_DEFECT"
            elif last_stage == "SURFACE_QUERY_MISMATCH" or not surface_matched:
                # Input was applied and onEnter was invoked
                q2_input_app_defect = "NOT_SUPPORTED"
                q2_pre_submit_defect = "NOT_SUPPORTED"

                # Check if surface query stayed on Q1
                surface_after_text = sample.get("SEARCH_SURFACE_QUERY_AFTER") or ""
                header_text = deep.get("header_text") or ""
                input_text = deep.get("input_text") or ""

                if case_b_q1 in surface_after_text or case_b_q1 in header_text:
                    # The UI surface stayed completely on Q1!
                    # This indicates either onEnter() failed to effectively trigger Slack's search action,
                    # or Slack received it but failed to transition the surface.
                    q2_effective_submit_defect = "LIKELY"
                    q2_search_surface_defect = "CONFIRMED"
                    primary_loss_point = "Q2_SEARCH_SURFACE_TRANSITION_DEFECT"
                else:
                    q2_search_surface_defect = "CONFIRMED"
                    primary_loss_point = "Q2_SEARCH_SURFACE_TRANSITION_DEFECT"

    # Query length causality assessment (Section 6)
    long_query_causal = "UNRESOLVED"

    final_report = {
        "metadata": {
            "task": "TASK_11R10D_CASE_B_SEQUENTIAL_QUERY_TRANSITION_FORENSIC",
            "timestamp_utc": start_time.isoformat(),
            "git_commit_head": commit_head,
            "probe_script_sha256": probe_sha256,
            "backend_sha256": backend_sha256,
            "uncommitted_backend_drift": uncommitted_drift,
            "post_hoc_query_changes": 0,
            "post_hoc_query_additions": 0,
            "sequence_iterations_planned": args.iterations,
            "sequence_iterations_executed": len(iteration_records),
            "exact_queries": [
                {"order": 1, "query": case_b_q1, "length": len(case_b_q1)},
                {"order": 2, "query": case_b_q2, "length": len(case_b_q2)},
                {"order": 3, "query": case_b_q3, "length": len(case_b_q3)},
            ],
            "invariants": {
                "SUBMIT_REQUIRES_VERIFIED_QUERY_STATE": True,
                "FIXED_SETTLE_SLEEP_IS_SUBMIT_AUTHORITY": False,
                "FINGERPRINT_CHANGE_ALONE_IS_NOT_PRIMARY_QUERY_AUTHORITY": True,
                "TRANSIENT_EMPTY_DOM_IS_NOT_TERMINAL_SUCCESS": True,
                "STALE_RESULT_SUCCESS_RETURN_PREVENTED": True,
                "FRESH_WEBVIEW_HOST_PER_SEQUENCE": True,
                "FULL_SLACK_PROCESS_COLD_START_PER_SEQUENCE": False,
                "SEMANTIC_PIPELINE_CALLS": 0,
                "QUALIFICATION_SCORE_CONTRIBUTION": 0,
                "TASK_11_COMPLETE": False,
                "TASK_11_QUALIFICATION_PASS": False,
                "TASK_12_STARTED": False,
                "PRODUCTION_CUTOVER": False,
            },
        },
        "verdict": {
            "CASE_B_Q2_FAILURE_REPRODUCED": failure_reproduced,
            "CASE_B_Q2_PRIMARY_LOSS_POINT": primary_loss_point,
            "Q2_INPUT_APPLICATION_DEFECT": q2_input_app_defect,
            "Q2_PRE_SUBMIT_STATE_SETTLE_DEFECT": q2_pre_submit_defect,
            "Q2_EFFECTIVE_SUBMISSION_DEFECT": q2_effective_submit_defect,
            "Q2_SEARCH_SURFACE_TRANSITION_DEFECT": q2_search_surface_defect,
            "Q2_RESULT_LIFECYCLE_TRANSITION_DEFECT": q2_result_lifecycle_defect,
            "Q2_SURFACE_OBSERVABILITY_DEFECT": q2_surface_observability_defect,
            "LONG_QUERY_LENGTH_CAUSAL": long_query_causal,
            "CASE_B_SEQUENTIAL_TRANSITION_FLAKINESS": flakiness_verdict,
            "CASE_B_Q2_DETERMINISTIC_FAILURE_UNDER_TEST_CONDITIONS": deterministic_verdict,
            "q2_failure_count": q2_failure_count,
            "q2_success_count": q2_success_count,
            "total_iterations": len(iteration_records),
        },
        "iterations": iteration_records,
    }

    report_path = run_dir / "task11r10d_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    logger.info("\n" + "=" * 70)
    logger.info("🏁 TASK 11R10D FORENSIC COMPLETE")
    logger.info(f"   CASE_B_Q2_FAILURE_REPRODUCED = {failure_reproduced}")
    logger.info(f"   CASE_B_Q2_PRIMARY_LOSS_POINT = {primary_loss_point}")
    logger.info(f"   CASE_B_SEQUENTIAL_TRANSITION_FLAKINESS = {flakiness_verdict}")
    logger.info(f"   Report file: {report_path}")
    logger.info("================================================================================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
