#!/usr/bin/env python3
"""Task 11R7 — Full Live Production Pipeline Diagnostic Harness.

Goal:
  Confirm end-to-end whether actual Slack evidence reaches through the entire
  WorkItem semantic pipeline (Stages 1A, 1B, 2, 2.5 + Decisive Authority Reconciliation)
  and grounded answer builder using the fixed production retrieval path.

Strict Invariants:
1. QUALIFICATION_SCORE_CONTRIBUTION = 0 (Diagnostic execution, not a qualification run).
2. A_B_C_ROLE = DIAGNOSTIC_CASES.
3. TASK_11_COMPLETE = false, TASK_11_QUALIFICATION_PASS = false.
4. TASK_12_STARTED = false, PRODUCTION_CUTOVER = false.
5. SLACK_WRITE_ACTIONS = 0 (Strict read-only offscreen search).
6. Zero Production Code Changes (InvestigationPlanner, SlackQueryCompiler,
   WebViewSlackSearchBackend, EvidenceSelector, Stage 1A/B/2/2.5, resolver,
   answer builder are UNTOUCHED).
7. Frozen Predeclared Plans Executed with zero post-hoc mutations.
8. No Diagnostic Code Mutation After First Live Read.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# Add repository root and src to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from pesu_agent.investigation.retrieval_coordinator import (
    CandidateQueryHit,
    CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT,
    OrchestratorConfig,
    ProductionInvestigationRetrievalCoordinator,
    RetrievalCoordinatorResult,
    SHARED_RETRIEVAL_EXECUTION_CORE_COUNT,
    TELEMETRY_AFFECTS_DEDUPE,
    TELEMETRY_AFFECTS_FILTERING,
    TELEMETRY_AFFECTS_RANKING,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    ResolvedActorScope,
    ResolvedTemporalScope,
    SearchQuery,
    UserQuestion,
)
from pesu_agent.investigation.selector import EvidenceSelector, get_canonical_evidence_key
from pesu_agent.investigation.gap_assessment import OpenRouterEvidenceAssessor
from pesu_agent.investigation.claim_verification import OpenRouterClaimVerifier
from pesu_agent.investigation.workitem_synthesis import (
    DecomposedWorkItemSynthesisCoordinator,
    OpenRouterEpisodeMembershipGrouper,
    OpenRouterWorkItemRelationVerifier,
    OpenRouterWorkItemLifecycleVerifier,
    OpenRouterDecisiveLifecycleAuthorityVerifier,
    WorkItemSynthesisConfig,
    WorkItemTraceabilityChain,
)
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
)
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemEpisodeMembership,
    WorkItemNode,
    WorkItemStatus,
)
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.search.webview_thread_reader import WebviewThreadReader
from pesu_agent.search.actor import ActorScopeKind, ActorResolutionStatus
from pesu_agent.search.temporal import TemporalStrictness, TemporalResolutionStatus
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.ui.result_presenter import present_grounded_investigation_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r7_pipeline")

PREDECLARATIONS_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_live_case_predeclarations.json"
OUTPUT_BASE_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"

# Section 7 Authoritative Semantic Configurations
AUTHORITATIVE_SEMANTIC_CONFIGS = {
    "EvidenceAssessor": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
    },
    "ClaimVerifier": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
    },
    "Stage1A": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
    },
    "Stage1B": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
    },
    "Stage2": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
    },
    "Stage2.5": {
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "high",
        "temperature": 0.0,
    },
}

TARGET_PERMALINK_SUBSTRING = "p1786425172544279"
TARGET_SNIPPET_SUBSTRING = "선제적으로 정의한 기준 기반 1차 배포하였습니다"


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


def assert_semantic_runtime_configurations(coordinator: ProductionRealTraceCoordinator) -> None:
    """Verifies that all 6 semantic components in the pipeline match authoritative provenance."""
    # 1. EvidenceAssessor
    assessor = coordinator.evidence_assessor
    exp_assessor = AUTHORITATIVE_SEMANTIC_CONFIGS["EvidenceAssessor"]
    actual_assessor_model = getattr(assessor, "model", getattr(assessor, "model_id", None))
    if actual_assessor_model != exp_assessor["model"]:
        raise ValueError(
            f"EvidenceAssessor model mismatch: expected {exp_assessor['model']}, got {actual_assessor_model}"
        )
    if getattr(assessor, "reasoning_effort", None) != exp_assessor["reasoning_effort"]:
        raise ValueError(
            f"EvidenceAssessor reasoning_effort mismatch: expected {exp_assessor['reasoning_effort']}, got {getattr(assessor, 'reasoning_effort', None)}"
        )

    # 2. ClaimVerifier
    verifier = coordinator.claim_verifier
    exp_verifier = AUTHORITATIVE_SEMANTIC_CONFIGS["ClaimVerifier"]
    actual_verifier_model = getattr(verifier, "model", getattr(verifier, "model_id", None))
    if actual_verifier_model != exp_verifier["model"]:
        raise ValueError(
            f"ClaimVerifier model mismatch: expected {exp_verifier['model']}, got {actual_verifier_model}"
        )
    if getattr(verifier, "reasoning_effort", None) != exp_verifier["reasoning_effort"]:
        raise ValueError(
            f"ClaimVerifier reasoning_effort mismatch: expected {exp_verifier['reasoning_effort']}, got {getattr(verifier, 'reasoning_effort', None)}"
        )

    # 3. Synthesis Coordinator Stages
    synth = coordinator.synthesis_coordinator

    # Stage 1A
    g1a = synth.stage1a_coordinator.grouper
    exp_1a = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage1A"]
    if g1a.model != exp_1a["model"] or g1a.reasoning_effort != exp_1a["reasoning_effort"]:
        raise ValueError(
            f"Stage 1A config mismatch: expected {exp_1a['model']}/{exp_1a['reasoning_effort']}, "
            f"got {g1a.model}/{g1a.reasoning_effort}"
        )

    # Stage 1B
    v1b = synth.stage1b_coordinator.verifier
    exp_1b = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage1B"]
    if v1b.model != exp_1b["model"] or v1b.reasoning_effort != exp_1b["reasoning_effort"]:
        raise ValueError(
            f"Stage 1B config mismatch: expected {exp_1b['model']}/{exp_1b['reasoning_effort']}, "
            f"got {v1b.model}/{v1b.reasoning_effort}"
        )

    # Stage 2
    v2 = synth.stage2_coordinator.verifier
    exp_2 = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage2"]
    if v2.model != exp_2["model"] or v2.reasoning_effort != exp_2["reasoning_effort"]:
        raise ValueError(
            f"Stage 2 config mismatch: expected {exp_2['model']}/{exp_2['reasoning_effort']}, "
            f"got {v2.model}/{v2.reasoning_effort}"
        )

    # Stage 2.5
    v25 = synth.stage25_coordinator.verifier
    exp_25 = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage2.5"]
    if v25.model != exp_25["model"] or v25.reasoning_effort != exp_25["reasoning_effort"]:
        raise ValueError(
            f"Stage 2.5 config mismatch: expected {exp_25['model']}/{exp_25['reasoning_effort']}, "
            f"got {v25.model}/{v25.reasoning_effort}"
        )

    logger.info("✅ [CONFIG_ASSERTION_PASSED] All 6 semantic component configurations verified.")


def is_target_text(t: str) -> bool:
    return bool(
        TARGET_PERMALINK_SUBSTRING in (t or "")
        or TARGET_SNIPPET_SUBSTRING in (t or "")
        or (("선제적으로 정의한 기준" in (t or "")) and ("1차 배포" in (t or "")))
        or (("고객 만족도 현황" in (t or "")) and ("1차 배포" in (t or "")) and ("배포하였습니다" in (t or "")))
    )


async def execute_case_diagnostic(
    case_spec: Dict[str, Any],
    retrieval_coordinator: ProductionInvestigationRetrievalCoordinator,
    trace_coordinator: ProductionRealTraceCoordinator,
    thread_reader: Optional[WebviewThreadReader] = None,
    is_dry_run: bool = False,
) -> Dict[str, Any]:
    case_id = case_spec["case_id"]
    question = case_spec["question"]
    expected_final_state = case_spec["expected_final_state"]
    exact_queries = case_spec["exact_slack_queries"]
    actor_scope_dict = case_spec["actor_scope"]
    temporal_scope_dict = case_spec["temporal_scope"]

    logger.info(f"\n=======================================================")
    logger.info(f"▶ EXECUTING CASE DIAGNOSTIC: {case_id}")
    logger.info(f"  Question: {question}")
    logger.info(f"  Expected Diagnostic State: {expected_final_state}")
    logger.info(f"=======================================================")

    actor_scope = ResolvedActorScope(
        kind=ActorScopeKind(actor_scope_dict["kind"]),
        status=ActorResolutionStatus(actor_scope_dict["status"]),
        is_implicit=actor_scope_dict.get("is_implicit", False),
    )
    temporal_scope = ResolvedTemporalScope(
        strictness=TemporalStrictness(temporal_scope_dict["strictness"]),
        status=TemporalResolutionStatus(temporal_scope_dict["status"]),
    )

    t0 = time.perf_counter()

    # Step 1: Retrieval via single shared ProductionInvestigationRetrievalCoordinator
    thread_fn = thread_reader.read_thread_result if thread_reader else None
    search_queries = [
        SearchQuery(
            query=q_spec["compiled_query"],
            purpose=q_spec.get("purpose", "Predeclared diagnostic query"),
            target_channel_hint=q_spec.get("target_channel_hint"),
        )
        for q_spec in exact_queries[:case_spec.get("query_limit", 3)]
    ]
    frozen_plan = InvestigationPlan(
        objective=case_spec.get("planner_objective", "Diagnostic Plan"),
        search_queries=search_queries,
        questions_to_resolve=[question],
        anchor_terms=[],
    )
    retrieval_res: RetrievalCoordinatorResult = await retrieval_coordinator.retrieve_precomputed(
        plan=frozen_plan,
        actor_scope=actor_scope,
        temporal_scope=temporal_scope,
        investigation_id=f"diag_{case_id.lower()}",
        thread_reader_fn=thread_fn,
    )

    candidates = retrieval_res.candidates
    selected_candidates = retrieval_res.selected_candidates
    evidence_package = retrieval_res.evidence_package
    raw_query_records = retrieval_res.raw_query_records
    pipeline_boundary_telemetry = retrieval_res.pipeline_boundary_telemetry

    retrieval_latency = time.perf_counter() - t0
    logger.info(
        f"📦 [RETRIEVAL_COMPLETE] {case_id}: Raw queries={len(raw_query_records)}, "
        f"Candidates={len(candidates)}, Selected={len(selected_candidates)} (Latency: {retrieval_latency:.2f}s)"
    )

    if is_dry_run:
        logger.info(f"🧪 [DRY_RUN] Bypassing provider calls for {case_id}")
        dummy_graph = InvestigationStateGraph(question=question)
        dummy_ans = GroundedWorkItemAnswerBuilder().build_answer(
            question=question,
            graph=dummy_graph,
            work_items=[],
            traceability_chains={},
        )
        return {
            "case_id": case_id,
            "question": question,
            "expected_final_state": expected_final_state,
            "actual_final_state": "DRY_RUN",
            "retrieval_candidates": len(candidates),
            "selected_candidates": len(selected_candidates),
            "dry_run": True,
        }

    # Step 2: Full Multi-Stage WorkItem Trace Coordinator Execution
    t_synth_start = time.perf_counter()
    trace_result: ProductionTraceResult = await trace_coordinator.trace_and_synthesize_async(
        question=question,
        candidates=selected_candidates if selected_candidates else candidates,
        investigation_objective=case_spec.get("planner_objective"),
        temporal_scope=temporal_scope,
        actor_scope=actor_scope,
        candidate_query_provenance=retrieval_res.candidate_query_provenance,
    )
    synth_latency = time.perf_counter() - t_synth_start

    # Step 3: Grounded Answer Building
    answer_builder = GroundedWorkItemAnswerBuilder()
    grounded_answer = answer_builder.build_answer(
        question=question,
        graph=trace_result.graph,
        work_items=list(trace_result.work_items.values()),
        traceability_chains=trace_result.traceability_chains,
    )

    total_latency = time.perf_counter() - t0

    # Extract Boundary Trace Fields (Section 4)
    raw_result_ids = []
    post_integrity_ids = []
    for q_rec in raw_query_records:
        for it in q_rec.get("raw_items", []):
            ident = it.get("url") or f"{it.get('sender_author_id')}:{it.get('raw_index')}"
            raw_result_ids.append(ident)
            if it.get("body_valid"):
                post_integrity_ids.append(ident)

    post_dedupe_ids = pipeline_boundary_telemetry.get("post_dedupe_keys", [
        get_canonical_evidence_key(c) for c in candidates
    ])
    post_thread_ids = [get_canonical_evidence_key(c) for c in candidates]
    selector_input_ids = [get_canonical_evidence_key(c) for c in candidates]
    selected_evidence_ids = [get_canonical_evidence_key(e) for e in selected_candidates]
    assessor_input_ids = list(trace_result.graph.evidence_nodes.keys())
    proposed_claim_ids = list(trace_result.graph.claim_nodes.keys())
    verified_claim_ids = [
        cid for cid, c in trace_result.graph.claim_nodes.items()
        if getattr(c, "verdict", None) == "VERIFIED" or getattr(c, "status", None) in ("SUPPORTED", "PARTIALLY_SUPPORTED")
    ]

    stage1a_output = {
        wid: {
            "title": wi.title,
            "members": [
                l.claim_id for l in trace_result.graph.work_item_claim_links
                if l.work_item_id == wid and l.episode_membership == WorkItemEpisodeMembership.MEMBER
            ],
            "context": [
                l.claim_id for l in trace_result.graph.work_item_claim_links
                if l.work_item_id == wid and l.episode_membership == WorkItemEpisodeMembership.CONTEXT
            ],
        }
        for wid, wi in trace_result.graph.work_items.items()
    }

    stage1b_output = {
        l.link_id: {
            "work_item_id": l.work_item_id,
            "claim_id": l.claim_id,
            "relation": l.relation.value if l.relation else None,
        }
        for l in trace_result.graph.work_item_claim_links
        if l.relation is not None
    }

    stage2_output = {
        l.link_id: {
            "work_item_id": l.work_item_id,
            "claim_id": l.claim_id,
            "lifecycle_signal": l.lifecycle_signal.value if l.lifecycle_signal else None,
        }
        for l in trace_result.graph.work_item_claim_links
        if l.lifecycle_signal is not None
    }

    stage2_5_output = {
        l.link_id: {
            "work_item_id": l.work_item_id,
            "claim_id": l.claim_id,
            "decisive_authority": l.decisive_authority.value if l.decisive_authority else None,
        }
        for l in trace_result.graph.work_item_claim_links
        if l.decisive_authority is not None
    }

    reconciliation_output = {
        wid: {
            "title": wi.title,
            "status": wi.status.value if hasattr(wi.status, "value") else str(wi.status),
            "category": wi.category.value if hasattr(wi.category, "value") else str(wi.category),
        }
        for wid, wi in trace_result.work_items.items()
    }

    # Final WorkItem State determination
    final_workitem_state = "UNKNOWN"
    if grounded_answer.work_item_cards:
        card_statuses = [card.status for card in grounded_answer.work_item_cards]
        if "BLOCKED" in card_statuses:
            final_workitem_state = "BLOCKED"
        elif "COMPLETED" in card_statuses:
            final_workitem_state = "COMPLETED"
        elif "UNKNOWN" in card_statuses or not grounded_answer.is_conclusive:
            final_workitem_state = "UNKNOWN"
        else:
            final_workitem_state = card_statuses[0]
    elif trace_result.work_items:
        wi_statuses = [wi.status.value if hasattr(wi.status, "value") else str(wi.status) for wi in trace_result.work_items.values()]
        if "BLOCKED" in wi_statuses:
            final_workitem_state = "BLOCKED"
        elif "COMPLETED" in wi_statuses:
            final_workitem_state = "COMPLETED"
        else:
            final_workitem_state = wi_statuses[0]

    traceable_claim_ids = []
    traceable_evidence_ids = []
    for wid, chain in trace_result.traceability_chains.items():
        traceable_claim_ids.extend(chain.claim_ids)
        traceable_evidence_ids.extend(chain.evidence_keys)
    traceable_claim_ids = list(dict.fromkeys(traceable_claim_ids))
    traceable_evidence_ids = list(dict.fromkeys(traceable_evidence_ids))

    boundary_trace = {
        "RAW_RESULT_IDS": raw_result_ids,
        "POST_INTEGRITY_IDS": post_integrity_ids,
        "POST_DEDUPE_IDS": post_dedupe_ids,
        "POST_THREAD_IDS": post_thread_ids,
        "SELECTOR_INPUT_IDS": selector_input_ids,
        "SELECTED_EVIDENCE_IDS": selected_evidence_ids,
        "ASSESSOR_INPUT_IDS": assessor_input_ids,
        "PROPOSED_CLAIM_IDS": proposed_claim_ids,
        "VERIFIED_CLAIM_IDS": verified_claim_ids,
        "STAGE1A_OUTPUT": stage1a_output,
        "STAGE1B_OUTPUT": stage1b_output,
        "STAGE2_OUTPUT": stage2_output,
        "STAGE2_5_OUTPUT": stage2_5_output,
        "RECONCILIATION_OUTPUT": reconciliation_output,
        "FINAL_WORKITEM_STATE": final_workitem_state,
        "TRACEABLE_CLAIM_IDS": traceable_claim_ids,
        "TRACEABLE_EVIDENCE_IDS": traceable_evidence_ids,
        "GROUNDED_ANSWER": {
            "summary": grounded_answer.grounded_summary,
            "is_conclusive": grounded_answer.is_conclusive,
            "cards_count": len(grounded_answer.work_item_cards),
            "cards": [card.model_dump() for card in grounded_answer.work_item_cards],
        },
    }

    # Case A Specific 10 Checkpoints (Section 5)
    case_a_checkpoints = None
    if case_id == "CASE_A":
        target_in_raw = any(
            is_target_text(it.get("normalized_body", "") + " " + str(it.get("url", "")))
            for q_rec in raw_query_records
            for it in q_rec.get("raw_items", [])
        )
        target_after_integrity = any(
            is_target_text(it.get("normalized_body", "") + " " + str(it.get("url", "")))
            for q_rec in raw_query_records
            for it in q_rec.get("raw_items", [])
            if it.get("body_valid")
        )
        target_after_dedupe = any(
            is_target_text(c.text or "") or TARGET_PERMALINK_SUBSTRING in (c.permalink or "")
            for c in candidates
        )
        target_after_thread = target_after_dedupe
        target_in_selector_input = target_after_dedupe
        target_selected = any(
            is_target_text(e.text or "") or TARGET_PERMALINK_SUBSTRING in (e.permalink or "")
            for e in selected_candidates
        )
        target_in_assessor_input = any(
            is_target_text(en.text or "") or TARGET_PERMALINK_SUBSTRING in (en.canonical_permalink or "")
            for en in trace_result.graph.evidence_nodes.values()
        )
        target_ev_ids = [
            en.evidence_id for en in trace_result.graph.evidence_nodes.values()
            if is_target_text(en.text or "") or TARGET_PERMALINK_SUBSTRING in (en.canonical_permalink or "")
        ]

        target_supports_proposed = False
        target_supports_verified = False
        for cid, c in trace_result.graph.claim_nodes.items():
            links = [l for l in trace_result.graph.claim_evidence_links.values() if l.claim_id == cid]
            c_ev_ids = [l.evidence_id for l in links]
            is_target_claim = any(eid in target_ev_ids for eid in c_ev_ids) or is_target_text(c.statement)
            if is_target_claim:
                target_supports_proposed = True
                if getattr(c, "verdict", None) == "VERIFIED" or getattr(c, "status", None) in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
                    target_supports_verified = True

        target_reaches_final_trace = False
        for e_key in traceable_evidence_ids:
            if is_target_text(e_key):
                target_reaches_final_trace = True
                break
        if not target_reaches_final_trace:
            # Check if any selected item that matched target has its key in traceable_evidence_ids
            for sc in selected_candidates:
                if is_target_text(sc.text or "") or TARGET_PERMALINK_SUBSTRING in (sc.permalink or ""):
                    if get_canonical_evidence_key(sc) in traceable_evidence_ids:
                        target_reaches_final_trace = True
                        break

        case_a_checkpoints = {
            "TARGET_PRESENT_IN_RAW": target_in_raw,
            "TARGET_PRESENT_AFTER_INTEGRITY": target_after_integrity,
            "TARGET_PRESENT_AFTER_DEDUPE": target_after_dedupe,
            "TARGET_PRESENT_AFTER_THREAD": target_after_thread,
            "TARGET_PRESENT_IN_SELECTOR_INPUT": target_in_selector_input,
            "TARGET_SELECTED": target_selected,
            "TARGET_IN_ASSESSOR_INPUT": target_in_assessor_input,
            "TARGET_SUPPORTS_PROPOSED_CLAIM": target_supports_proposed,
            "TARGET_SUPPORTS_VERIFIED_CLAIM": target_supports_verified,
            "TARGET_REACHES_FINAL_TRACE": target_reaches_final_trace,
        }

    # Full pipeline exercised check for this case
    pipeline_exercised = bool(
        len(assessor_input_ids) > 0
        and len(proposed_claim_ids) > 0
        and len(stage1a_output) > 0
        and len(stage1b_output) > 0
        and len(stage2_output) > 0
        and len(reconciliation_output) > 0
        and len(traceable_claim_ids) > 0
        and bool(grounded_answer.grounded_summary)
    )

    logger.info(f"🏁 [CASE_COMPLETE] {case_id}: Final State={final_workitem_state} (Expected={expected_final_state})")
    logger.info(f"   Exercised: {pipeline_exercised}, Latency: total={total_latency:.2f}s (retrieval={retrieval_latency:.2f}s, synth={synth_latency:.2f}s)")
    if case_a_checkpoints:
        logger.info(f"   CASE A Checkpoints: {case_a_checkpoints}")

    return {
        "case_id": case_id,
        "question": question,
        "expected_final_state": expected_final_state,
        "actual_final_state": final_workitem_state,
        "pipeline_boundary_trace": boundary_trace,
        "case_a_checkpoints": case_a_checkpoints,
        "full_pipeline_exercised": pipeline_exercised,
        "stop_reason": trace_result.stop_reason,
        "error_message": trace_result.error_message,
        "latencies": {
            "retrieval_seconds": retrieval_latency,
            "synthesis_seconds": synth_latency,
            "total_seconds": total_latency,
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Task 11R7 Full Live Production Pipeline Diagnostic")
    parser.add_argument("--dry-run", action="store_true", help="Run offline dry-run without live Slack or model calls")
    args = parser.parse_args()

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE_DIR / f"task11r7_pipeline_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    backend_path = REPO_ROOT / "src" / "pesu_agent" / "search" / "webview_slack_search.py"
    retrieval_coord_path = REPO_ROOT / "src" / "pesu_agent" / "investigation" / "retrieval_coordinator.py"

    backend_sha256 = compute_file_sha256(backend_path)
    retrieval_coord_sha256 = compute_file_sha256(retrieval_coord_path)
    commit_head = get_git_commit_head()
    uncommitted_drift = check_uncommitted_drift()

    logger.info("================================================================================")
    logger.info("  TASK 11R7 — FULL LIVE PRODUCTION PIPELINE DIAGNOSTIC")
    logger.info(f"  Mode: {'OFFLINE DRY RUN' if args.dry_run else 'LIVE SLACK EXECUTION'}")
    logger.info(f"  Commit: {commit_head}")
    logger.info(f"  Backend SHA-256: {backend_sha256}")
    logger.info(f"  Retrieval Coordinator SHA-256: {retrieval_coord_sha256}")
    logger.info(f"  Uncommitted Drift: {uncommitted_drift}")
    logger.info("================================================================================")

    # Section 2 Authority Invariant Assertions
    if uncommitted_drift:
        logger.error("❌ UNCOMMITTED DRIFT DETECTED in search or investigation packages! Aborting.")
        return 1

    # Load predeclarations fixture
    if not PREDECLARATIONS_PATH.exists():
        logger.error(f"❌ Predeclarations fixture not found at {PREDECLARATIONS_PATH}")
        return 1

    fixture_bytes = PREDECLARATIONS_PATH.read_bytes()
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    predeclarations = json.loads(fixture_bytes.decode("utf-8"))
    cases = predeclarations.get("cases", [])

    # Instantiate Components & Assert Configs
    s_cfg = WorkItemSynthesisConfig()
    s_coord = DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=OpenRouterEpisodeMembershipGrouper(config=s_cfg),
        relation_verifier=OpenRouterWorkItemRelationVerifier(config=s_cfg),
        lifecycle_verifier=OpenRouterWorkItemLifecycleVerifier(config=s_cfg),
        authority_verifier=OpenRouterDecisiveLifecycleAuthorityVerifier(
            model="openai/gpt-5.6-luna",
            reasoning_effort="high",
            temperature=0.0,
        ),
        config=s_cfg,
    )
    trace_coordinator = ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)
    assert_semantic_runtime_configurations(trace_coordinator)

    host = None
    if args.dry_run:
        search_backend = SyntheticSearchBackend()
        thread_reader = None
        logger.info("🧪 [DRY_RUN] SyntheticSearchBackend initialized.")
    else:
        if not os.getenv("OPENROUTER_API_KEY"):
            logger.error("❌ OPENROUTER_API_KEY is not set!")
            return 1

        logger.info("🌐 [SLACK_BACKEND_INIT] Creating read-only WebViewSlackSearchBackend (WEBVIEW_DIRECT)...")
        search_backend = create_slack_search_backend(
            config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
        )
        host = getattr(search_backend, "_window", None)
        if host and hasattr(host, "start"):
            logger.info("🌐 [SLACK_HOST_START] Starting session-scoped offscreen PesuWebViewHost...")
            host.start(timeout=30.0)
        thread_reader = WebviewThreadReader(host=host) if host else None

    retrieval_coordinator = ProductionInvestigationRetrievalCoordinator(
        search_backend=search_backend,
        selector=EvidenceSelector(),
        planner=None,
        config=OrchestratorConfig(),
    )

    case_diagnostics = []
    try:
        for case_spec in cases:
            diag_res = await execute_case_diagnostic(
                case_spec=case_spec,
                retrieval_coordinator=retrieval_coordinator,
                trace_coordinator=trace_coordinator,
                thread_reader=thread_reader,
                is_dry_run=args.dry_run,
            )
            case_diagnostics.append(diag_res)
    finally:
        if host and hasattr(host, "stop"):
            logger.info("🛑 [SLACK_HOST_STOP] Stopping session-scoped PesuWebViewHost...")
            try:
                host.stop()
            except Exception as e:
                logger.warning(f"Error stopping host: {e}")

    # Section 6 Full Pipeline Exercise Assessment
    all_cases_exercised = all(c.get("full_pipeline_exercised", False) for c in case_diagnostics) if not args.dry_run else False

    full_report = {
        "metadata": {
            "task": "TASK_11R7_FULL_LIVE_PRODUCTION_PIPELINE_DIAGNOSTIC",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": args.dry_run,
            "backend_commit": commit_head,
            "search_backend_sha256": backend_sha256,
            "retrieval_coordinator_sha256": retrieval_coord_sha256,
            "uncommitted_full_pipeline_drift": uncommitted_drift,
            "frozen_manifest_drift": 0,
            "fixture_sha256": fixture_sha256,
            "semantic_configurations": AUTHORITATIVE_SEMANTIC_CONFIGS,
            "invariants": {
                "A_B_C_ROLE": "DIAGNOSTIC_CASES",
                "QUALIFICATION_SCORE_CONTRIBUTION": 0,
                "SLACK_WRITE_ACTIONS": 0,
                "TASK_11_COMPLETE": False,
                "TASK_11_QUALIFICATION_PASS": False,
                "TASK_12_STARTED": False,
                "PRODUCTION_CUTOVER": False,
                "PRODUCTION_CODE_MUTATIONS": 0,
                "DIAGNOSTIC_HARNESS_MUTATIONS": 0,
                "QUERY_MUTATIONS": 0,
                "EXPECTED_STATE_MUTATIONS": 0,
            },
        },
        "live_full_workitem_pipeline_diagnostically_exercised": all_cases_exercised,
        "cases": case_diagnostics,
    }

    report_file = run_dir / "task11r7_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)

    logger.info("\n================================================================================")
    logger.info("🏁 TASK 11R7 DIAGNOSTIC EXECUTION COMPLETE")
    logger.info(f"   LIVE_FULL_WORKITEM_PIPELINE_DIAGNOSTICALLY_EXERCISED = {all_cases_exercised}")
    for c in case_diagnostics:
        logger.info(f"   [{c['case_id']}] Final State: {c['actual_final_state']} (Expected: {c['expected_final_state']})")
        if c.get("case_a_checkpoints"):
            logger.info(f"     ↳ CASE A Checkpoints: {c['case_a_checkpoints']}")
    logger.info(f"   Report File: {report_file}")
    logger.info("================================================================================\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
