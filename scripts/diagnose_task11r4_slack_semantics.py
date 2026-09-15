#!/usr/bin/env python
"""Task 11R4 — Slack Known-Target Search Semantics / Index Coverage Diagnostic Script.

Strict Invariants:
1. QUALIFICATION_SCORE_CONTRIBUTION = 0 (Pure forensic diagnostic, not a qualification run).
2. TASK_11_COMPLETE = false, TASK_12_STARTED = false, PRODUCTION_CUTOVER = false.
3. SLACK_WRITE_ACTIONS = 0 (Strictly read-only offscreen search).
4. Pre-Declared Immutable Query Ladder (Freeze before live execution):
   C1: '세무사 지점 합류' (Positive Control)
   T1: '고객 만족도'
   T2: '고객 만족도 현황'
   T3: '1차 배포'
   T4: '배포하였습니다'
   T5: '선제적으로 정의한 기준'
   T6: '고객 만족도 현황 1차 배포'
   T7: '"고객 만족도" "1차 배포"'
5. Zero Production Search Semantics Modifications:
   Does NOT alter planner, compiler, submission logic, transition barrier, or candidate caps.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Any, Optional

# Add repository root to path
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
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    compute_result_set_fingerprint,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r4_semantics")

# Pre-declared authoritative query set
POSITIVE_CONTROL_QUERY = {
    "id": "C1",
    "query": "세무사 지점 합류",
    "purpose": "Confirm baseline search backend health and positive result return in live workspace.",
    "is_positive_control": True,
}

TARGET_LADDER_QUERIES = [
    {
        "id": "T1",
        "query": "고객 만족도",
        "purpose": "Broadest domain keyword anchor.",
        "level": "broad",
    },
    {
        "id": "T2",
        "query": "고객 만족도 현황",
        "purpose": "Narrower feature name anchor.",
        "level": "intermediate",
    },
    {
        "id": "T3",
        "query": "1차 배포",
        "purpose": "Action and milestone anchor.",
        "level": "action",
    },
    {
        "id": "T4",
        "query": "배포하였습니다",
        "purpose": "Exact verb conjugation anchor from known message.",
        "level": "exact_verb",
    },
    {
        "id": "T5",
        "query": "선제적으로 정의한 기준",
        "purpose": "Distinctive qualification phrase anchor from known message.",
        "level": "distinctive_phrase",
    },
    {
        "id": "T6",
        "query": "고객 만족도 현황 1차 배포",
        "purpose": "Target feature + milestone combined phrase.",
        "level": "compound",
    },
    {
        "id": "T7",
        "query": '"고객 만족도" "1차 배포"',
        "purpose": "Multi-quoted phrase conjunction (verified quote-preserving).",
        "level": "quoted_conjunction",
    },
]

TARGET_ANCHORS = [
    "고객 만족도",
    "고객 만족도 현황",
    "1차 배포",
    "배포하였습니다",
    "선제적으로 정의한 기준",
]

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_query_set_sha256() -> str:
    canonical = json.dumps(
        {"c1": POSITIVE_CONTROL_QUERY, "ladder": TARGET_LADDER_QUERIES},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_t7_offline_syntax() -> dict[str, Any]:
    """Offline check to verify T7 quote preservation through SlackQueryCompiler."""
    q_t7 = TARGET_LADDER_QUERIES[-1]["query"]
    compiled = SlackQueryCompiler.compile(q_t7)
    quotes_preserved = (
        compiled.compiled_query == q_t7 and not compiled.outer_quotes_removed
    )
    return {
        "T7_REQUESTED_QUERY": q_t7,
        "T7_EFFECTIVE_INPUT_QUERY": compiled.compiled_query,
        "T7_QUOTES_PRESERVED": quotes_preserved,
        "T7_SYNTAX_SUPPORTED": quotes_preserved,
    }


def evaluate_target_in_text(text: str) -> dict[str, Any]:
    """Check text against the 5 semantic/text anchors."""
    matched_anchors = [a for a in TARGET_ANCHORS if a in (text or "")]
    # Target presence requires matching substantive anchors
    is_target = bool(
        ("선제적으로 정의한 기준" in (text or ""))
        or ("배포하였습니다" in (text or ""))
        or (("고객 만족도" in (text or "")) and ("1차 배포" in (text or "")))
        or ("고객 만족도 현황 페이지" in (text or ""))
    )
    return {
        "is_target": is_target,
        "matched_anchors": matched_anchors,
    }


def run_diagnostic(dry_run: bool = False) -> dict[str, Any]:
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"task11r4_semantics_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    backend_path = REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py"
    script_path = Path(__file__).resolve()

    t7_syntax_check = verify_t7_offline_syntax()
    query_set_hash = compute_query_set_sha256()

    diagnostic_metadata = {
        "task": "TASK_11R4_SEMANTICS_INDEX_DIAGNOSTIC",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "qualification_score_contribution": 0,
        "slack_write_actions": 0,
        "task_11_complete": False,
        "task_11_qualification_pass": False,
        "task_12_started": False,
        "production_cutover": False,
        "backend_sha256": compute_file_sha256(backend_path),
        "script_sha256": compute_file_sha256(script_path),
        "query_set_sha256": query_set_hash,
        "post_hoc_query_additions": 0,
        "post_hoc_query_replacements": 0,
        "t7_syntax_check": t7_syntax_check,
    }

    logger.info("=================================================================")
    logger.info("🔬 STARTING TASK 11R4 SLACK SEARCH SEMANTICS & INDEX DIAGNOSTIC")
    logger.info(f"   Timestamp: {diagnostic_metadata['timestamp_utc']}")
    logger.info(f"   Backend SHA-256: {diagnostic_metadata['backend_sha256']}")
    logger.info(f"   Query Set SHA-256: {diagnostic_metadata['query_set_sha256']}")
    logger.info(f"   T7 Syntax Supported: {t7_syntax_check['T7_SYNTAX_SUPPORTED']}")
    logger.info(f"   Output Directory: {run_dir}")
    logger.info("=================================================================")

    if dry_run:
        logger.info("🧪 [DRY_RUN] Skipping live host initialization.")
        return diagnostic_metadata

    logger.info("🌐 [SLACK_BACKEND_INIT] Initializing read-only WebViewSlackSearchBackend (WEBVIEW_DIRECT)...")
    search_backend = create_slack_search_backend(
        config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
    )
    host = getattr(search_backend, "_window", None)
    if host and hasattr(host, "start"):
        logger.info("🌐 [SLACK_HOST_START] Starting session-scoped offscreen PesuWebViewHost...")
        host.start(timeout=30.0)

    query_records: list[dict[str, Any]] = []

    # =========================================================================
    # STEP 1: Positive Control Query (C1: '세무사 지점 합류')
    # =========================================================================
    c1_spec = POSITIVE_CONTROL_QUERY
    logger.info("\n-------------------------------------------------------------")
    logger.info(f"▶ [POSITIVE_CONTROL] EXECUTING C1: '{c1_spec['query']}'")
    logger.info(f"  Purpose: {c1_spec['purpose']}")
    logger.info("-------------------------------------------------------------")

    t0 = time.perf_counter()
    c1_error = None
    try:
        c1_results = search_backend.search_messages(c1_spec["query"], max_results=5)
    except Exception as e:
        c1_results = []
        c1_error = str(e)
        logger.error(f"  ❌ C1 Search raised exception: {e}")
    c1_latency = time.perf_counter() - t0

    c1_telemetry = getattr(search_backend, "last_search_telemetry", {}) or {}
    c1_transition = c1_telemetry.get("forensic_transition", {})

    c1_verified_query_state = bool(c1_telemetry.get("verified_query_state_before_submit", False))
    c1_onenter_invoked = bool(c1_telemetry.get("onenter_invoked", False))
    c1_surface_matches = bool(c1_transition.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))
    c1_transition_confirmed = bool(c1_surface_matches and c1_verified_query_state and c1_onenter_invoked)
    c1_readiness = c1_telemetry.get("SEARCH_RESULT_READINESS") or c1_transition.get("SEARCH_RESULT_READINESS")
    c1_top_identifiers = [r.result_url for r in c1_results if r.result_url]
    c1_result_count = len(c1_results)

    c1_record = {
        "id": "C1",
        "REQUESTED_QUERY": c1_spec["query"],
        "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": c1_verified_query_state,
        "ONENTER_INVOKED": c1_onenter_invoked,
        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": c1_surface_matches,
        "RESULT_TRANSITION_CONFIRMED": c1_transition_confirmed,
        "SEARCH_RESULT_READINESS": c1_readiness,
        "SEARCH_ERROR": c1_error,
        "RESULT_COUNT": c1_result_count,
        "TOP_RESULT_IDENTIFIERS": c1_top_identifiers,
        "NORMALIZED_RESULT_SNIPPETS": [
            {
                "author": r.author,
                "channel": getattr(r, "channel_name", None) or getattr(r, "channel_id", None),
                "url": r.result_url,
                "snippet": (r.text or "")[:120],
            }
            for r in c1_results
        ],
        "latency_seconds": c1_latency,
        "is_positive_control": True,
    }
    query_records.append(c1_record)

    logger.info(f"  • C1 Result Count: {c1_result_count} (Latency: {c1_latency:.2f}s)")
    logger.info(f"  • C1 Transition Confirmed: {c1_transition_confirmed}")
    for idx, r in enumerate(c1_results[:3], 1):
        logger.info(f"    [{idx}] {r.author or 'Unknown'}: {(r.text or '')[:80]}...")

    positive_control_search_works = c1_result_count > 0 and c1_transition_confirmed

    # Strict Gate Rule (Section 2): If positive control fails, STOP immediately!
    if not positive_control_search_works:
        logger.error("❌ POSITIVE CONTROL FAILED (C1 Result Count <= 0 or transition failed).")
        logger.error("   Classifying as SEARCH_BACKEND_OR_SESSION_REGRESSION and STOPPING.")

        verdict = {
            "POSITIVE_CONTROL_SEARCH_WORKS": "NOT_SUPPORTED",
            "TARGET_FOUND_WITH_BROAD_QUERY": "NOT_SUPPORTED",
            "TARGET_FOUND_WITH_PRODUCTION_QUERY": False,
            "QUERY_PLANNING_OR_QUERY_FORMULATION_DEFECT": "UNRESOLVED",
            "SLACK_SEARCH_INDEX_DEFECT": "UNRESOLVED",
            "SLACK_PROFILE_ACCESS_DEFECT": "UNRESOLVED",
            "RESULT_LIMIT_OR_RANKING_DEFECT": "UNRESOLVED",
            "RESULT_LIMIT_OR_RANKING_PRESSURE_POSSIBLE": False,
            "MESSAGE_EXISTENCE_STATUS": "UNRESOLVED",
            "SEARCH_BACKEND_OR_SESSION_REGRESSION": "CONFIRMED",
            "GATE_STOP_TRIGGERED": True,
            "GATE_STOP_REASON": "Positive control query returned 0 results; aborted before Case A ladder.",
        }

        full_report = {
            "metadata": diagnostic_metadata,
            "c1_positive_control": c1_record,
            "query_records": query_records,
            "verdict": verdict,
        }

        report_file = run_dir / "task11r4_semantics_report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(full_report, f, indent=2, ensure_ascii=False)

        logger.info(f"📄 Report written to {report_file}")
        return full_report

    logger.info("✅ POSITIVE CONTROL PASSED: C1 returned results > 0. Proceeding to Case A ladder.")

    # =========================================================================
    # STEP 2: CASE A Known-Target Ladder (T1–T7)
    # =========================================================================
    ladder_results_summary: list[dict[str, Any]] = []

    for q_spec in TARGET_LADDER_QUERIES:
        q_id = q_spec["id"]
        q_text = q_spec["query"]
        purpose = q_spec["purpose"]

        logger.info("\n-------------------------------------------------------------")
        logger.info(f"▶ EXECUTING LADDER QUERY {q_id}: '{q_text}'")
        logger.info(f"  Purpose: {purpose}")
        logger.info("-------------------------------------------------------------")

        t0 = time.perf_counter()
        q_error = None
        try:
            results = search_backend.search_messages(q_text, max_results=5)
        except Exception as e:
            results = []
            q_error = str(e)
            logger.error(f"  ❌ {q_id} Search raised exception: {e}")
        latency = time.perf_counter() - t0

        telemetry = getattr(search_backend, "last_search_telemetry", {}) or {}
        transition = telemetry.get("forensic_transition", {})

        verified_query_state = bool(telemetry.get("verified_query_state_before_submit", False))
        onenter_invoked = bool(telemetry.get("onenter_invoked", False))
        surface_matches = bool(transition.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED", False))
        transition_confirmed = bool(surface_matches and verified_query_state and onenter_invoked)
        readiness = telemetry.get("SEARCH_RESULT_READINESS") or transition.get("SEARCH_RESULT_READINESS")
        top_identifiers = [r.result_url for r in results if r.result_url]
        result_count = len(results)

        # Target detection analysis
        target_found_in_query = False
        target_item_details: Optional[dict[str, Any]] = None

        normalized_snippets = []
        for r in results:
            text = r.text or ""
            eval_res = evaluate_target_in_text(text)
            snippet_record = {
                "author": r.author,
                "channel": getattr(r, "channel_name", None) or getattr(r, "channel_id", None),
                "url": r.result_url,
                "snippet": text[:120],
                "matched_anchors": eval_res["matched_anchors"],
                "is_target": eval_res["is_target"],
            }
            normalized_snippets.append(snippet_record)

            if eval_res["is_target"] and not target_found_in_query:
                target_found_in_query = True
                target_item_details = {
                    "TARGET_PRESENT_IN_RAW": True,
                    "TARGET_RESULT_IDENTIFIER": r.result_url or f"{getattr(r, 'channel_id', None)}:{getattr(r, 'gold_message_ts', None) or getattr(r, 'timestamp_raw', None)}",
                    "TARGET_RESULT_CHANNEL": getattr(r, "channel_name", None) or getattr(r, "channel_id", None),
                    "TARGET_RESULT_PERMALINK": r.result_url,
                    "TARGET_RESULT_SNIPPET": text[:200],
                    "MATCHED_ANCHORS": eval_res["matched_anchors"],
                }

        if not target_found_in_query:
            target_item_details = {
                "TARGET_PRESENT_IN_RAW": False,
                "TARGET_RESULT_IDENTIFIER": None,
                "TARGET_RESULT_CHANNEL": None,
                "TARGET_RESULT_PERMALINK": None,
                "TARGET_RESULT_SNIPPET": None,
                "MATCHED_ANCHORS": [],
            }

        q_record = {
            "id": q_id,
            "REQUESTED_QUERY": q_text,
            "VERIFIED_QUERY_STATE_BEFORE_SUBMIT": verified_query_state,
            "ONENTER_INVOKED": onenter_invoked,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_matches,
            "RESULT_TRANSITION_CONFIRMED": transition_confirmed,
            "SEARCH_RESULT_READINESS": readiness,
            "SEARCH_ERROR": q_error,
            "RESULT_COUNT": result_count,
            "TOP_RESULT_IDENTIFIERS": top_identifiers,
            "NORMALIZED_RESULT_SNIPPETS": normalized_snippets,
            "target_detection": target_item_details,
            "latency_seconds": latency,
            "purpose": purpose,
            "level": q_spec.get("level"),
        }
        query_records.append(q_record)
        ladder_results_summary.append(q_record)

        logger.info(f"  • Result Count: {result_count} (Latency: {latency:.2f}s)")
        logger.info(f"  • Transition Confirmed: {transition_confirmed}")
        logger.info(f"  • Target Detected in Raw: {target_found_in_query}")
        for idx, r in enumerate(results[:3], 1):
            logger.info(f"    [{idx}] {r.author or 'Unknown'}: {(r.text or '')[:80]}...")

    # =========================================================================
    # STEP 3: Matrix Evaluation & Root Cause Classification
    # =========================================================================
    any_target_found_in_ladder = any(
        r["target_detection"]["TARGET_PRESENT_IN_RAW"] for r in ladder_results_summary
    )
    broad_queries_target_found = any(
        r["target_detection"]["TARGET_PRESENT_IN_RAW"]
        for r in ladder_results_summary
        if r["id"] in ("T1", "T2")
    )
    any_results_in_ladder = any(r["RESULT_COUNT"] > 0 for r in ladder_results_summary)
    t1_count = next((r["RESULT_COUNT"] for r in ladder_results_summary if r["id"] == "T1"), 0)
    t2_count = next((r["RESULT_COUNT"] for r in ladder_results_summary if r["id"] == "T2"), 0)

    # Ranking / cap pressure evaluation (Correction 1: Forbid CONFIRMED on Scenario D)
    # If broad query hit cap (5) but target was absent from top 5:
    cap_hit_without_target = (t1_count >= 5 or t2_count >= 5) and not broad_queries_target_found

    # Classify Scenarios
    if broad_queries_target_found:
        # Scenario A: Broad query found target, production queries returned 0
        scenario = "SCENARIO_A"
        query_planning_defect = "CONFIRMED"
        slack_index_defect = "NOT_SUPPORTED"
        slack_profile_access_defect = "NOT_SUPPORTED"
        message_existence_status = "CONFIRMED"
        ranking_defect = "NOT_SUPPORTED"
        ranking_possible = False
    elif not any_target_found_in_ladder:
        # Scenario B: Target absent even in broad queries
        scenario = "SCENARIO_B"
        query_planning_defect = "NOT_SUPPORTED"  # Do NOT blame planner when even broad words find nothing
        slack_index_defect = "UNRESOLVED"
        slack_profile_access_defect = "UNRESOLVED"
        message_existence_status = "UNRESOLVED"

        if cap_hit_without_target:
            # Scenario D nuance: result cap of 5 reached, but target not in top 5
            ranking_defect = "UNRESOLVED"
            ranking_possible = True
        else:
            ranking_defect = "NOT_SUPPORTED"
            ranking_possible = False
    else:
        # Target found in specific ladder query (T3-T7) but not broad T1/T2
        scenario = "SCENARIO_A_SPECIFIC"
        query_planning_defect = "CONFIRMED"
        slack_index_defect = "NOT_SUPPORTED"
        slack_profile_access_defect = "NOT_SUPPORTED"
        message_existence_status = "CONFIRMED"
        ranking_defect = "NOT_SUPPORTED"
        ranking_possible = False

    verdict = {
        "SCENARIO_CLASSIFICATION": scenario,
        "POSITIVE_CONTROL_SEARCH_WORKS": "CONFIRMED" if positive_control_search_works else "NOT_SUPPORTED",
        "TARGET_FOUND_WITH_BROAD_QUERY": "CONFIRMED" if broad_queries_target_found else "NOT_SUPPORTED",
        "TARGET_FOUND_ANYWHERE_IN_LADDER": "CONFIRMED" if any_target_found_in_ladder else "NOT_SUPPORTED",
        "TARGET_FOUND_WITH_PRODUCTION_QUERY": False,  # Authoritative from Task 11R3
        "QUERY_PLANNING_OR_QUERY_FORMULATION_DEFECT": query_planning_defect,
        "SLACK_SEARCH_INDEX_DEFECT": slack_index_defect,
        "SLACK_PROFILE_ACCESS_DEFECT": slack_profile_access_defect,
        "RESULT_LIMIT_OR_RANKING_DEFECT": ranking_defect,
        "RESULT_LIMIT_OR_RANKING_PRESSURE_POSSIBLE": ranking_possible,
        "MESSAGE_EXISTENCE_STATUS": message_existence_status,
        "SEARCH_BACKEND_OR_SESSION_REGRESSION": "NOT_SUPPORTED",
        "GATE_STOP_TRIGGERED": False,
    }

    full_report = {
        "metadata": diagnostic_metadata,
        "c1_positive_control": c1_record,
        "ladder_results": ladder_results_summary,
        "verdict": verdict,
    }

    report_file = run_dir / "task11r4_semantics_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)

    logger.info("\n=============================================================")
    logger.info("🏁 TASK 11R4 DIAGNOSTIC COMPLETE")
    logger.info(f"   Scenario Classification: {verdict['SCENARIO_CLASSIFICATION']}")
    logger.info(f"   Positive Control Works: {verdict['POSITIVE_CONTROL_SEARCH_WORKS']}")
    logger.info(f"   Target Found in Broad Query: {verdict['TARGET_FOUND_WITH_BROAD_QUERY']}")
    logger.info(f"   Target Found in Ladder: {verdict['TARGET_FOUND_ANYWHERE_IN_LADDER']}")
    logger.info(f"   Query Planning Defect: {verdict['QUERY_PLANNING_OR_QUERY_FORMULATION_DEFECT']}")
    logger.info(f"   Slack Index Defect: {verdict['SLACK_SEARCH_INDEX_DEFECT']}")
    logger.info(f"   Slack Profile Access Defect: {verdict['SLACK_PROFILE_ACCESS_DEFECT']}")
    logger.info(f"   Result Limit/Ranking Defect: {verdict['RESULT_LIMIT_OR_RANKING_DEFECT']}")
    logger.info(f"   Ranking Pressure Possible: {verdict['RESULT_LIMIT_OR_RANKING_PRESSURE_POSSIBLE']}")
    logger.info(f"   Message Existence Status: {verdict['MESSAGE_EXISTENCE_STATUS']}")
    logger.info(f"   Report File: {report_file}")
    logger.info("=============================================================")

    return full_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11R4 Slack Search Semantics Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without live Slack execution")
    args = parser.parse_args()

    run_diagnostic(dry_run=args.dry_run)
