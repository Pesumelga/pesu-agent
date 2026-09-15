#!/usr/bin/env python
"""Task 11R2 — Slack Search Query Application / Result Transition Forensic Diagnostic Script.

Strict Invariants:
1. QUALIFICATION_SCORE_CONTRIBUTION = 0 (Pure forensic, not a qualification run).
2. TASK_11_COMPLETE = false, TASK_12_STARTED = false, PRODUCTION_CUTOVER = false.
3. SLACK_WRITE_ACTIONS = 0 (Strictly read-only offscreen search).
4. CASE A 3 Predeclared Queries Only:
   Q1: '고객 만족도 현황 페이지 1차 배포 완료'
   Q2: '고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가'
   Q3: '고객 만족도 현황 페이지 배포'
5. Observes transition across separate query and result axes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any

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
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    compute_result_set_fingerprint,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r2_forensic")

CASE_A_QUERIES = [
    {
        "order": 1,
        "query": "고객 만족도 현황 페이지 1차 배포 완료",
        "purpose": "해당 페이지의 1차 배포 완료 여부를 직접 확인한다.",
    },
    {
        "order": 2,
        "query": "고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가",
        "purpose": "조사 질문의 실제 자연어 표현과 일치하는 논의나 답변을 찾는다.",
    },
    {
        "order": 3,
        "query": "고객 만족도 현황 페이지 배포",
        "purpose": "배포 상태, 진행 상황, 배포 이슈 등 관련 논의를 넓게 탐색한다.",
    },
]

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def run_forensic(dry_run: bool = False) -> dict[str, Any]:
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"forensic_task11r2_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    backend_path = REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py"
    script_path = Path(__file__).resolve()

    forensic_metadata = {
        "task": "TASK_11R2_FORENSIC",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "qualification_score_contribution": 0,
        "slack_write_actions": 0,
        "task_11_complete": False,
        "task_12_started": False,
        "production_cutover": False,
        "backend_sha256": compute_file_sha256(backend_path),
        "script_sha256": compute_file_sha256(script_path),
    }

    logger.info("=================================================================")
    logger.info("🔬 STARTING TASK 11R2 SLACK SEARCH QUERY TRANSITION FORENSIC")
    logger.info(f"   Timestamp: {forensic_metadata['timestamp_utc']}")
    logger.info(f"   Backend SHA-256: {forensic_metadata['backend_sha256']}")
    logger.info(f"   Output Directory: {run_dir}")
    logger.info("=================================================================")

    if dry_run:
        logger.info("🧪 [DRY_RUN] Skipping live host initialization.")
        return forensic_metadata

    logger.info("🌐 [SLACK_BACKEND_INIT] Initializing read-only WebViewSlackSearchBackend (WEBVIEW_DIRECT)...")
    search_backend = create_slack_search_backend(
        config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
    )
    host = getattr(search_backend, "_window", None)
    if host and hasattr(host, "start"):
        logger.info("🌐 [SLACK_HOST_START] Starting session-scoped offscreen PesuWebViewHost...")
        host.start(timeout=30.0)

    query_records: list[dict[str, Any]] = []

    try:
        for q_spec in CASE_A_QUERIES:
            order = q_spec["order"]
            q_text = q_spec["query"]
            purpose = q_spec["purpose"]

            logger.info(f"\n-------------------------------------------------------------")
            logger.info(f"▶ EXECUTING QUERY #{order}: '{q_text}'")
            logger.info(f"  Purpose: {purpose}")
            logger.info(f"-------------------------------------------------------------")

            t0 = time.perf_counter()
            results = search_backend.search_messages(q_text, max_results=5)
            latency = time.perf_counter() - t0

            telemetry = getattr(search_backend, "last_search_telemetry", {}) or {}
            transition = telemetry.get("forensic_transition", {})

            q_record = {
                "order": order,
                "requested_query": q_text,
                "purpose": purpose,
                "latency_seconds": latency,
                "result_count": len(results),
                "forensic_transition": transition,
                "top_results": [
                    {
                        "index": r.result_index,
                        "url": r.result_url,
                        "author": r.author,
                        "author_id": r.author_id,
                        "timestamp": r.timestamp_raw,
                        "text_snippet": (r.text or "")[:120],
                        "fingerprint": r.message_fingerprint,
                    }
                    for r in results
                ],
            }
            query_records.append(q_record)

            logger.info(f"  • Result Count: {len(results)} (Latency: {latency:.2f}s)")
            logger.info(f"  • INPUT_MATCHES_REQUESTED: {transition.get('INPUT_MATCHES_REQUESTED')}")
            logger.info(f"  • SUBMIT_TRIGGERED: {transition.get('SUBMIT_TRIGGERED')}")
            logger.info(f"  • SEARCH_SURFACE_QUERY_BEFORE: {transition.get('SEARCH_SURFACE_QUERY_BEFORE')}")
            logger.info(f"  • SEARCH_SURFACE_QUERY_AFTER: {transition.get('SEARCH_SURFACE_QUERY_AFTER')}")
            logger.info(f"  • SEARCH_SURFACE_QUERY_MATCHES_REQUESTED: {transition.get('SEARCH_SURFACE_QUERY_MATCHES_REQUESTED')}")
            logger.info(f"  • RESULT_FINGERPRINT_BEFORE: {transition.get('RESULT_FINGERPRINT_BEFORE')}")
            logger.info(f"  • RESULT_FINGERPRINT_AFTER: {transition.get('RESULT_FINGERPRINT_AFTER')}")
            logger.info(f"  • RESULT_FINGERPRINT_CHANGED: {transition.get('RESULT_FINGERPRINT_CHANGED')}")
            logger.info(f"  • TRANSITION_CLASSIFICATION: {transition.get('TRANSITION_CLASSIFICATION')}")
            for idx, r in enumerate(results[:3], 1):
                logger.info(f"    [{idx}] {r.author or 'Unknown'}: {(r.text or '')[:80]}...")

    finally:
        if host and hasattr(host, "destroy"):
            logger.info("🌐 [SLACK_HOST_STOP] Shutting down host...")
            try:
                host.destroy()
            except Exception:
                pass

    # Compare query transitions across queries
    cross_query_analysis = {
        "all_input_matches_requested": all(
            r["forensic_transition"].get("INPUT_MATCHES_REQUESTED") for r in query_records
        ),
        "all_submit_triggered": all(
            r["forensic_transition"].get("SUBMIT_TRIGGERED") for r in query_records
        ),
        "surface_queries_observed": [
            r["forensic_transition"].get("SEARCH_SURFACE_QUERY_AFTER") for r in query_records
        ],
        "fingerprints_observed": [
            r["forensic_transition"].get("RESULT_FINGERPRINT_AFTER") for r in query_records
        ],
        "all_fingerprints_identical": (
            len(set(r["forensic_transition"].get("RESULT_FINGERPRINT_AFTER") for r in query_records)) == 1
        ),
        "top_identifier_sets": [
            [it["url"] for it in r["top_results"]] for r in query_records
        ],
    }

    report = {
        "metadata": forensic_metadata,
        "cross_query_analysis": cross_query_analysis,
        "query_records": query_records,
    }

    out_file = run_dir / "task11r2_forensic_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"\n📊 Forensic report saved to: {out_file}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11R2 Search Transition Forensic")
    parser.add_argument("--dry-run", action="store_true", help="Run without launching live Slack WebView")
    args = parser.parse_args()
    run_forensic(dry_run=args.dry_run)
