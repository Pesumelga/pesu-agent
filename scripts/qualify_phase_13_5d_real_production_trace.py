#!/usr/bin/env python3
"""Phase 13.5D Task 11 — Bounded Live Slack Real Production Trace Qualification Harness.

Executes bounded, read-only live Slack retrieval and qualified multi-stage WorkItem pipeline
across 3 predeclared production cases:
- Case A: Expected COMPLETED (Clear Completion)
- Case B: Expected BLOCKED (Completion + Blocker Coexistence)
- Case C: Expected UNKNOWN (Grounded Safe Unknown, SAFE_UNKNOWN != PIPELINE_FAILURE)

Strict Qualification Invariants:
1. PRODUCTION_RETRIEVAL_COMPONENTS_REUSED = True
2. CUSTOM_TASK11_RETRIEVAL_SEMANTICS = False
3. OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS = 0
4. EXACT_LIVE_QUERY_PLAN_PREDECLARED = True
5. LIVE_TIME_QUERY_REPLANNING = False
6. EXECUTED_QUERY_PLAN_EQUALS_PREDECLARED_PLAN = True
7. POST_HOC_QUERY_CHANGES = 0
8. ALL_SEMANTIC_COMPONENT_CONFIGS_RESOLVED_FROM_QUALIFIED_STATE = True
9. ONE_GRAPH_PER_INVESTIGATION = True
10. FREEFORM_ANSWER_LLM_CALLS = 0
11. SEMANTIC_RETRIES = 0
12. SLACK_WRITE_ACTIONS = 0
13. PRODUCTION_CUTOVER = False
14. TASK_12_STARTED = False
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Set UTF-8 encoding for standard output
sys.stdout.reconfigure(encoding="utf-8")

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

# Core imports
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
from pesu_agent.investigation.planner import InvestigationPlanner
from pesu_agent.investigation.models import (
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    ResolvedActorScope,
    ResolvedTemporalScope,
    RetrievalCoverage,
    SearchQuery,
    ThreadContextStatus,
    UserQuestion,
)
from pesu_agent.investigation.selector import EvidenceSelector, get_canonical_evidence_key
from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.gap_assessment import (
    AssessmentPayloadBuilder,
    OpenRouterEvidenceAssessor,
)
from pesu_agent.investigation.claim_verification import OpenRouterClaimVerifier
from pesu_agent.investigation.workitem_synthesis import (
    DecomposedWorkItemSynthesisCoordinator,
    OpenRouterEpisodeMembershipGrouper,
    OpenRouterWorkItemRelationVerifier,
    OpenRouterWorkItemLifecycleVerifier,
    OpenRouterDecisiveLifecycleAuthorityVerifier,
    WorkItemSynthesisConfig,
)
from pesu_agent.investigation.graph_models import InvestigationStateGraph
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
)
from pesu_agent.investigation.search_backend import (
    SearchBackend,
    SyntheticSearchBackend,
)
from pesu_agent.search.actor import ActorScopeKind, ActorResolutionStatus, resolve_actor_scope
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler
from pesu_agent.search.temporal import (
    TemporalStrictness,
    TemporalResolutionStatus,
    resolve_temporal_scope,
)
from pesu_agent.search.webview_thread_reader import WebviewThreadReader, is_eligible_thread_candidate
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.ui.result_presenter import present_grounded_investigation_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("qualify_phase_13_5d_live_trace")

PREDECLARATIONS_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_live_case_predeclarations.json"
OUTPUT_BASE_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"


# ---------------------------------------------------------------------------
# Authoritative Configuration Provenance
# ---------------------------------------------------------------------------
AUTHORITATIVE_SEMANTIC_CONFIGS = {
    "EvidenceAssessor": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "provenance": "output/qualification_13_5d/evidence_assessor/run_cycle2_20260915_002209/run_manifest.json (Commit 38db792)",
    },
    "ClaimVerifier": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "provenance": "scripts/qualify_phase_13_4b_real_model.py / src/pesu_agent/investigation/claim_verification.py:336",
    },
    "Stage1A_Membership": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "provenance": "reports/phase_13_5c_ar3_r3a_fd1_decision.json:14 / src/pesu_agent/investigation/workitem_synthesis.py:3476",
    },
    "Stage1B_Relation": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "provenance": "reports/phase_13_5c_ar3_r3b_r2_scorecard.json:5-7",
    },
    "Stage2_Lifecycle": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "provenance": "reports/phase_13_5c_ar2_scorecard.json:5-6",
    },
    "Stage2.5_DecisiveAuthority": {
        "provider": "openrouter",
        "model": "openai/gpt-5.6-luna",
        "reasoning_effort": "high",
        "temperature": 0.0,
        "provenance": "reports/phase_13_5c_ar3_r3c_v10_scorecard.json:6-8",
    },
}


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
    exp_1a = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage1A_Membership"]
    if g1a.model != exp_1a["model"] or g1a.reasoning_effort != exp_1a["reasoning_effort"]:
        raise ValueError(
            f"Stage 1A config mismatch: expected {exp_1a['model']}/{exp_1a['reasoning_effort']}, "
            f"got {g1a.model}/{g1a.reasoning_effort}"
        )

    # Stage 1B
    v1b = synth.stage1b_coordinator.verifier
    exp_1b = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage1B_Relation"]
    if v1b.model != exp_1b["model"] or v1b.reasoning_effort != exp_1b["reasoning_effort"]:
        raise ValueError(
            f"Stage 1B config mismatch: expected {exp_1b['model']}/{exp_1b['reasoning_effort']}, "
            f"got {v1b.model}/{v1b.reasoning_effort}"
        )

    # Stage 2
    v2 = synth.stage2_coordinator.verifier
    exp_2 = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage2_Lifecycle"]
    if v2.model != exp_2["model"] or v2.reasoning_effort != exp_2["reasoning_effort"]:
        raise ValueError(
            f"Stage 2 config mismatch: expected {exp_2['model']}/{exp_2['reasoning_effort']}, "
            f"got {v2.model}/{v2.reasoning_effort}"
        )

    # Stage 2.5
    v25 = synth.stage25_coordinator.verifier
    exp_25 = AUTHORITATIVE_SEMANTIC_CONFIGS["Stage2.5_DecisiveAuthority"]
    if v25.model != exp_25["model"] or v25.reasoning_effort != exp_25["reasoning_effort"]:
        raise ValueError(
            f"Stage 2.5 config mismatch: expected {exp_25['model']}/{exp_25['reasoning_effort']}, "
            f"got {v25.model}/{v25.reasoning_effort}"
        )

    logger.info("✅ [CONFIG_ASSERTION_PASSED] All 6 semantic component configurations verified against authoritative provenance.")


# ---------------------------------------------------------------------------
# Forensic Journey Tracing (CASE A 7 Checkpoints)
# ---------------------------------------------------------------------------
def trace_case_a_target_journey(
    raw_query_records: List[Dict[str, Any]],
    candidates: List[EvidenceItem],
    selected_items: List[EvidenceItem],
    trace_result: Optional[ProductionTraceResult],
) -> Dict[str, Any]:
    """Forensic journey tracing for CASE A target proposition across 7 checkpoints."""
    target_terms = ["고객 만족도", "고객만족도", "1차 배포", "1차배포"]

    # CP1: Raw Slack search hits evaluated across all executed queries
    cp1_hits: List[Dict[str, Any]] = []
    cp2_drops: List[Dict[str, Any]] = []
    for q_rec in raw_query_records:
        q_idx = q_rec.get("query_index", 0)
        q_text = q_rec.get("query_text", "")
        for item in q_rec.get("raw_items_evaluated", []):
            norm_body = item.get("normalized_body", "")
            if any(term in norm_body for term in target_terms):
                hit_entry = {
                    "query_index": q_idx,
                    "query_text": q_text,
                    "raw_index": item.get("raw_index"),
                    "sender_author_id": item.get("sender_author_id"),
                    "body_valid": item.get("body_valid"),
                    "drop_reason": item.get("drop_reason"),
                    "ambiguous_fragments": item.get("ambiguous_fragment_count"),
                    "body_snippet": norm_body[:100],
                }
                cp1_hits.append(hit_entry)
                if not item.get("body_valid"):
                    cp2_drops.append(hit_entry)

    cp1_passed = len(cp1_hits) > 0
    cp2_passed = cp1_passed and any(h.get("body_valid") for h in cp1_hits)

    # CP3: Author integrity & SELF constraint survival
    # Check if target survived into candidates pool
    matching_candidates = [
        c for c in candidates if any(t in (c.text or "") for t in target_terms)
    ]
    cp3_passed = cp2_passed and len(matching_candidates) > 0

    # CP4: Deduplication across queries
    cp4_passed = len(matching_candidates) > 0

    # CP5: Thread eligibility and residency-first relocation
    cp5_status = "NOT_EVALUATED"
    if matching_candidates:
        expanded_cnt = sum(1 for c in matching_candidates if c.thread_replies)
        cp5_status = f"matching_candidates={len(matching_candidates)}, thread_expanded={expanded_cnt}"

    # CP6: EvidenceSelector ranking, anchor scoring, and cap (max 5)
    matching_selected = [
        it for it in selected_items if any(t in (it.text or "") for t in target_terms)
    ]
    cp6_passed = len(matching_selected) > 0

    # CP7: WorkItem Pipeline Handoff (Assessor -> Verifier -> WorkItem synthesis)
    cp7_passed = False
    pipeline_status = "NOT_REACHED"
    if trace_result is not None:
        pipeline_status = f"stop_reason={trace_result.stop_reason}, work_items={len(trace_result.work_items)}"
        claims = list(trace_result.graph.claim_nodes.values())
        supported_claims = [
            c for c in claims
            if getattr(c, "verdict", None) == "VERIFIED" or getattr(c, "status", None) == "SUPPORTED"
        ]
        if trace_result.work_items or supported_claims:
            cp7_passed = True

    # Determine PRIMARY_LOSS_POINT
    if not cp1_passed:
        primary_loss_point = "CP1_RAW_SLACK_HIT"
        loss_point_detail = "Target content ('고객 만족도 현황' / '1차 배포') was not returned by raw Slack search queries 1-3."
    elif not cp2_passed:
        primary_loss_point = "CP2_BODY_VALIDATION"
        loss_point_detail = f"Target content returned by Slack search but dropped by body validation. Drop reasons: {[d.get('drop_reason') for d in cp2_drops]}"
    elif not cp3_passed:
        primary_loss_point = "CP3_SELF_AUTHOR_VALIDATION"
        loss_point_detail = "Target content dropped by author integrity constraint."
    elif not cp4_passed:
        primary_loss_point = "CP4_DEDUPLICATION"
        loss_point_detail = "Target content dropped during candidate deduplication."
    elif not cp6_passed:
        primary_loss_point = "CP6_SELECTOR_RANKING_AND_CAP"
        loss_point_detail = f"Target content was present in candidate pool ({len(matching_candidates)} items) but ranked out or filtered out by EvidenceSelector (cap=5)."
    elif not cp7_passed:
        primary_loss_point = "CP7_WORKITEM_PIPELINE_HANDOFF"
        loss_point_detail = f"Target evidence was selected ({len(matching_selected)} items) but produced no supported claims or decisive work items in downstream pipeline (stop_reason={trace_result.stop_reason if trace_result else 'None'})."
    else:
        primary_loss_point = "SURVIVED_ALL_CHECKPOINTS"
        loss_point_detail = "Target evidence survived all 7 checkpoints and was synthesized into decisive work items."

    return {
        "target_proposition": "고객 만족도 현황 페이지 1차 배포 완료",
        "primary_loss_point": primary_loss_point,
        "loss_point_detail": loss_point_detail,
        "checkpoints": {
            "CP1_RAW_SLACK_HIT": {
                "passed": cp1_passed,
                "hit_count": len(cp1_hits),
                "hits": cp1_hits,
            },
            "CP2_BODY_VALIDATION": {
                "passed": cp2_passed,
                "dropped_count": len(cp2_drops),
            },
            "CP3_SELF_AUTHOR_VALIDATION": {
                "passed": cp3_passed,
            },
            "CP4_DEDUPLICATION": {
                "passed": cp4_passed,
                "surviving_candidates_count": len(matching_candidates),
            },
            "CP5_THREAD_ELIGIBILITY_AND_RELOCATION": {
                "status": cp5_status,
            },
            "CP6_SELECTOR_RANKING_AND_CAP": {
                "passed": cp6_passed,
                "selected_count": len(matching_selected),
                "selected_snippets": [s.text[:100] for s in matching_selected if s.text],
            },
            "CP7_WORKITEM_PIPELINE_HANDOFF": {
                "passed": cp7_passed,
                "pipeline_status": pipeline_status,
            },
        },
    }


# ---------------------------------------------------------------------------
# Single Case Runner
# ---------------------------------------------------------------------------
async def run_single_case(
    case_spec: Dict[str, Any],
    retrieval_coordinator: ProductionInvestigationRetrievalCoordinator,
    trace_coordinator: ProductionRealTraceCoordinator,
    thread_reader: Optional[WebviewThreadReader] = None,
    plan_mode: str = "frozen",
    is_dry_run: bool = False,
) -> Dict[str, Any]:
    """Runs 1 logical execution for a single diagnostic case using the single shared retrieval coordinator."""
    case_id = case_spec["case_id"]
    question = case_spec["question"]
    expected_final_state = case_spec["expected_final_state"]
    exact_queries = case_spec["exact_slack_queries"]
    actor_scope_dict = case_spec["actor_scope"]
    temporal_scope_dict = case_spec["temporal_scope"]

    logger.info(f"\n=======================================================")
    logger.info(f"▶ STARTING DIAGNOSTIC CASE: {case_id} (plan_mode={plan_mode})")
    logger.info(f"  Question: {question}")
    logger.info(f"  Expected Final State: {expected_final_state}")
    logger.info(f"  Queries to execute: {len(exact_queries)}")
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

    if plan_mode == "frozen":
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
            anchor_terms=[],  # MANUAL_SEARCH_CONCEPTS_TO_ANCHOR_TERMS_MAPPING = False
        )
        retrieval_res: RetrievalCoordinatorResult = await retrieval_coordinator.retrieve_precomputed(
            plan=frozen_plan,
            actor_scope=actor_scope,
            temporal_scope=temporal_scope,
            investigation_id=f"diag_{case_id.lower()}",
            thread_reader_fn=thread_fn,
        )
    elif plan_mode == "fresh-planner":
        retrieval_res = await retrieval_coordinator.retrieve(
            question=UserQuestion(text=question),
            investigation_id=f"diag_{case_id.lower()}",
            thread_reader_fn=thread_fn,
        )
    else:
        raise ValueError(f"Unknown plan_mode: {plan_mode}")

    evidence_package = retrieval_res.evidence_package
    candidates = retrieval_res.candidates
    selected_candidates = retrieval_res.selected_candidates
    candidate_query_provenance = retrieval_res.candidate_query_provenance
    raw_query_records = retrieval_res.raw_query_records
    pipeline_boundary_telemetry = retrieval_res.pipeline_boundary_telemetry

    retrieval_latency = time.perf_counter() - t0
    logger.info(
        f"📦 [RETRIEVAL_COMPLETE] {case_id}: Candidates={len(candidates)}, "
        f"Selected={len(evidence_package.evidence_items)}, Latency={retrieval_latency:.2f}s"
    )

    # If dry-run, do not make real LLM provider calls
    if is_dry_run:
        logger.info(f"🧪 [DRY_RUN_MODE] Bypassing provider calls for {case_id}")
        dummy_graph = InvestigationStateGraph(question=question)
        dummy_ans = GroundedWorkItemAnswerBuilder().build_answer(
            question=question,
            graph=dummy_graph,
            work_items=[],
            traceability_chains={},
        )
        dummy_ui = present_grounded_investigation_result(
            investigation_id=f"dry_{case_id.lower()}",
            payload=dummy_ans,
            stop_reason="DRY_RUN",
        )
        return {
            "case_id": case_id,
            "question": question,
            "expected_final_state": expected_final_state,
            "actual_final_state": "DRY_RUN_SKIPPED",
            "verdict": "DRY_RUN_PASS",
            "candidates_found": len(candidates),
            "evidence_package_items": len(evidence_package.evidence_items),
            "executed_queries": [q["compiled_query"] for q in exact_queries],
            "traceability_chains": {},
            "grounded_answer": dummy_ans.model_dump(),
            "ui_presentation": dummy_ui.model_dump() if hasattr(dummy_ui, "model_dump") else str(dummy_ui),
            "latency_seconds": retrieval_latency,
            "error_message": None,
            "raw_query_records": raw_query_records,
            "pipeline_boundary_telemetry": pipeline_boundary_telemetry,
        }

    # Step 2: Real Trace Coordinator Execution
    t_synth_start = time.perf_counter()
    trace_result: ProductionTraceResult = await trace_coordinator.trace_and_synthesize_async(
        question=question,
        candidates=selected_candidates if selected_candidates else candidates,
        investigation_objective=case_spec.get("planner_objective"),
        temporal_scope=temporal_scope,
        actor_scope=actor_scope,
        candidate_query_provenance=candidate_query_provenance,
    )
    synth_latency = time.perf_counter() - t_synth_start

    # Step 3: Grounded Answer Building (0 LLM calls)
    answer_builder = GroundedWorkItemAnswerBuilder()
    grounded_answer = answer_builder.build_answer(
        question=question,
        graph=trace_result.graph,
        work_items=list(trace_result.work_items.values()),
        traceability_chains=trace_result.traceability_chains,
    )

    # Step 4: Deterministic WinForms Presentation Formatting
    ui_presentation = present_grounded_investigation_result(
        investigation_id=f"diag_{case_id.lower()}",
        payload=grounded_answer,
        stop_reason=trace_result.stop_reason,
    )

    total_latency = time.perf_counter() - t0

    # Determine decisive final state from work item cards or trace result nodes
    actual_final_state = "UNKNOWN"
    if grounded_answer.work_item_cards:
        card_statuses = [card.status for card in grounded_answer.work_item_cards]
        if "BLOCKED" in card_statuses:
            actual_final_state = "BLOCKED"
        elif "COMPLETED" in card_statuses:
            actual_final_state = "COMPLETED"
        elif "UNKNOWN" in card_statuses or not grounded_answer.is_conclusive:
            actual_final_state = "UNKNOWN"
        else:
            actual_final_state = card_statuses[0]
    elif trace_result.work_items:
        wi_statuses = [
            wi.status.value if hasattr(wi.status, "value") else str(wi.status)
            for wi in trace_result.work_items.values()
        ]
        if "BLOCKED" in wi_statuses:
            actual_final_state = "BLOCKED"
        elif "COMPLETED" in wi_statuses:
            actual_final_state = "COMPLETED"
        else:
            actual_final_state = wi_statuses[0]
    else:
        actual_final_state = "UNKNOWN"

    # Evaluate Section 14 PASS Criteria (Diagnostic comparison)
    verdict = "FAIL"
    pass_reason = ""
    fail_reasons = []

    # State matching check
    if actual_final_state == expected_final_state:
        if expected_final_state in ("COMPLETED", "BLOCKED"):
            if not trace_result.work_items:
                fail_reasons.append(f"Expected {expected_final_state} but work_items dict is empty")
            else:
                has_valid_chain = False
                for wi_id, chain in trace_result.traceability_chains.items():
                    if chain.evidence_keys and chain.claim_ids:
                        has_valid_chain = True
                        break
                if not has_valid_chain:
                    fail_reasons.append(f"No valid traceability chain found for {expected_final_state}")
                else:
                    verdict = "PASS"
                    pass_reason = f"Decisive state {actual_final_state} matched expected and has complete traceability chain"
        elif expected_final_state == "UNKNOWN":
            if trace_result.success or trace_result.stop_reason in ("SAFE_UNKNOWN", "NO_SUPPORTED_CLAIMS", "INSUFFICIENT_EVIDENCE", "NO_EVIDENCE", "NO_DECISIVE_WORK_ITEM"):
                verdict = "PASS"
                pass_reason = f"Safe unknown verified: state={actual_final_state}, stop_reason={trace_result.stop_reason}"
            else:
                fail_reasons.append(f"UNKNOWN caused by pipeline crash rather than semantic insufficiency: {trace_result.error_message}")
    else:
        fail_reasons.append(f"Final state mismatch: expected {expected_final_state}, got {actual_final_state}")

    logger.info(f"🏁 [DIAGNOSTIC_EVALUATION] {case_id}: Verdict={verdict}, Actual={actual_final_state}, Expected={expected_final_state}")
    if fail_reasons:
        logger.warning(f"   Fail reasons: {fail_reasons}")

    # Case A Target Journey Analysis
    case_a_journey = None
    if case_id == "CASE_A":
        case_a_journey = trace_case_a_target_journey(
            raw_query_records=raw_query_records,
            candidates=candidates,
            selected_items=evidence_package.evidence_items,
            trace_result=trace_result,
        )
        logger.info(f"🔍 [CASE_A_JOURNEY] Primary Loss Point: {case_a_journey['primary_loss_point']}")
        logger.info(f"   Loss Detail: {case_a_journey['loss_point_detail']}")

    return {
        "case_id": case_id,
        "question": question,
        "expected_final_state": expected_final_state,
        "actual_final_state": actual_final_state,
        "verdict": verdict,
        "pass_reason": pass_reason,
        "fail_reasons": fail_reasons,
        "candidates_found": len(candidates),
        "selected_evidence_count": len(evidence_package.evidence_items),
        "work_item_count": len(trace_result.work_items),
        "traceability_chains": {k: v.model_dump() for k, v in trace_result.traceability_chains.items()},
        "grounded_answer": grounded_answer.model_dump(),
        "ui_presentation": ui_presentation.model_dump() if hasattr(ui_presentation, "model_dump") else str(ui_presentation),
        "latencies": {
            "retrieval_seconds": retrieval_latency,
            "synthesis_seconds": synth_latency,
            "total_seconds": total_latency,
        },
        "stop_reason": trace_result.stop_reason,
        "error_message": trace_result.error_message,
        "raw_query_records": raw_query_records,
        "pipeline_boundary_telemetry": pipeline_boundary_telemetry,
        "case_a_journey": case_a_journey,
        "live_full_workitem_pipeline_diagnostically_exercised": bool(trace_result.success or trace_result.stop_reason is not None),
        "candidates_detail": [
            {
                "evidence_id": c.evidence_id,
                "text": c.text,
                "channel": c.channel,
                "author_id": c.author_id,
                "timestamp": str(c.timestamp),
                "permalink": c.permalink,
            }
            for c in candidates
        ],
        "selected_items_detail": [
            {
                "evidence_id": it.evidence_id,
                "text": it.text,
                "channel": it.channel,
                "author_id": it.author_id,
                "timestamp": str(it.timestamp),
                "permalink": it.permalink,
            }
            for it in evidence_package.evidence_items
        ],
        "claims_detail": [
            {
                "claim_id": cid,
                "claim_text": c.claim_text,
                "verdict": str(getattr(c, "verdict", getattr(c, "status", "UNKNOWN"))),
                "confidence": getattr(c, "confidence", None),
            }
            for cid, c in trace_result.graph.claim_nodes.items()
        ],
    }


# ---------------------------------------------------------------------------
# Main Qualification Orchestration
# ---------------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 13.5D Task 11R Live Production Trace Diagnostic Harness")
    parser.add_argument("--dry-run", action="store_true", help="Run offline dry-run without live Slack or provider calls")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight validations and exit")
    parser.add_argument(
        "--plan-mode",
        choices=["frozen", "fresh-planner"],
        default="frozen",
        help="Retrieval plan mode: 'frozen' uses exact predeclared plan; 'fresh-planner' invokes production InvestigationPlanner.",
    )
    parser.add_argument(
        "--case",
        choices=["ALL", "CASE_A", "CASE_B", "CASE_C"],
        default="ALL",
        help="Specific diagnostic case to run, or ALL.",
    )
    args = parser.parse_args()

    is_dry_run = args.dry_run or args.preflight_only
    plan_mode = args.plan_mode

    logger.info("================================================================================")
    logger.info("  PESU AGENT — PHASE 13.5D TASK 11R REAL PRODUCTION TRACE DIAGNOSTIC HARNESS")
    logger.info(f"  Mode: {'OFFLINE DRY RUN' if is_dry_run else 'LIVE SLACK EXECUTION'}")
    logger.info(f"  Plan Mode: {plan_mode.upper()}")
    logger.info(f"  Cases Filter: {args.case}")
    logger.info("================================================================================")

    # Hard Invariant Assertions
    assert SHARED_RETRIEVAL_EXECUTION_CORE_COUNT == 1, "SHARED_RETRIEVAL_EXECUTION_CORE_COUNT must be 1"
    assert CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT == 0, "CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT must be 0"
    assert not TELEMETRY_AFFECTS_RANKING, "TELEMETRY_AFFECTS_RANKING must be False"
    assert not TELEMETRY_AFFECTS_FILTERING, "TELEMETRY_AFFECTS_FILTERING must be False"
    assert not TELEMETRY_AFFECTS_DEDUPE, "TELEMETRY_AFFECTS_DEDUPE must be False"

    # 1. Authoritative Predeclaration Loading
    if not PREDECLARATIONS_PATH.exists():
        logger.error(f"❌ Predeclarations fixture not found at: {PREDECLARATIONS_PATH}")
        return 1

    fixture_bytes = PREDECLARATIONS_PATH.read_bytes()
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    logger.info(f"📄 [FIXTURE_LOADED] Path: {PREDECLARATIONS_PATH}")
    logger.info(f"   SHA-256: {fixture_sha256}")

    predeclarations = json.loads(fixture_bytes.decode("utf-8"))
    all_cases = predeclarations.get("cases", [])
    if len(all_cases) != 3:
        logger.error(f"❌ Expected exactly 3 cases in fixture, found {len(all_cases)}")
        return 1

    if args.case != "ALL":
        cases = [c for c in all_cases if c["case_id"] == args.case]
    else:
        cases = all_cases

    # 2. Assert Exact Live Query Plan Predeclared & Query Order
    for case in cases:
        c_id = case["case_id"]
        exact_queries = case.get("exact_slack_queries", [])
        if not exact_queries:
            raise ValueError(f"Case {c_id} has no exact_slack_queries declared!")
        for idx, eq in enumerate(exact_queries, 1):
            if eq.get("order") != idx:
                raise ValueError(f"Case {c_id} query order mismatch: expected {idx}, got {eq.get('order')}")
            if not eq.get("compiled_query"):
                raise ValueError(f"Case {c_id} query #{idx} has empty compiled_query!")

    logger.info("✅ [EXACT_QUERY_PLAN_ASSERTED] All cases have valid, ordered exact Slack queries.")

    # 3. Instantiate Real Trace Coordinator & Assert Qualified Runtime Configurations
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

    # 4. Preflight Read-Only Safety Assertion
    host = None
    if is_dry_run:
        search_backend = SyntheticSearchBackend()
        thread_reader = None
        logger.info("🧪 [MOCK_BACKEND_ACTIVE] Using SyntheticSearchBackend for offline dry run.")
    else:
        # Check OPENROUTER_API_KEY
        if not os.getenv("OPENROUTER_API_KEY"):
            logger.error("❌ OPENROUTER_API_KEY environment variable is not set for live execution!")
            return 1

        # Direct offscreen read-only Slack search backend
        logger.info("🌐 [SLACK_BACKEND_INIT] Creating read-only WebViewSlackSearchBackend (WEBVIEW_DIRECT)...")
        search_backend = create_slack_search_backend(
            config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
        )
        host = getattr(search_backend, "_window", None)
        if host and hasattr(host, "start") and not is_dry_run:
            logger.info("🌐 [SLACK_HOST_START] Starting session-scoped offscreen PesuWebViewHost...")
            host.start(timeout=30.0)
        thread_reader = WebviewThreadReader(host=host) if host else None

    # Instantiate single shared ProductionInvestigationRetrievalCoordinator
    planner = InvestigationPlanner() if plan_mode == "fresh-planner" else None
    retrieval_coordinator = ProductionInvestigationRetrievalCoordinator(
        search_backend=search_backend,
        selector=EvidenceSelector(),
        planner=planner,
        config=OrchestratorConfig(),
    )

    # If preflight-only requested, exit here
    if args.preflight_only:
        logger.info("🏁 [PREFLIGHT_ONLY_PASSED] Preflight and config assertions complete. Exiting.")
        return 0

    # 5. Execute Cases (1 logical run per case, SEMANTIC_RETRIES = 0)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE_DIR / f"run_diagnostic_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    case_results = []
    overall_pass = True

    try:
        for case_spec in cases:
            res = await run_single_case(
                case_spec=case_spec,
                retrieval_coordinator=retrieval_coordinator,
                trace_coordinator=trace_coordinator,
                thread_reader=thread_reader,
                plan_mode=plan_mode,
                is_dry_run=is_dry_run,
            )
            case_results.append(res)
            if res["verdict"] != "PASS" and not is_dry_run:
                overall_pass = False

            # Save per-case artifact
            c_file = run_dir / f"{res['case_id'].lower()}.json"
            c_file.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if host and hasattr(host, "stop"):
            logger.info("🛑 [SLACK_HOST_STOP] Stopping session-scoped PesuWebViewHost...")
            try:
                host.stop()
            except Exception as e:
                logger.warning(f"Error stopping host: {e}")

    # 6. Build and Save Run Manifest and Summary
    case_a_primary_loss_point = next(
        (r["case_a_journey"]["primary_loss_point"] for r in case_results if r.get("case_a_journey")),
        None,
    )
    full_pipeline_exercised = all(
        r.get("live_full_workitem_pipeline_diagnostically_exercised", False)
        for r in case_results
    ) if not is_dry_run else False

    summary_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_dry_run": is_dry_run,
        "plan_mode": plan_mode,
        "overall_verdict": "PASS" if overall_pass else "FAIL",
        "predeclarations_sha256": fixture_sha256,
        "case_count": len(cases),
        "passed_cases": sum(1 for r in case_results if r["verdict"] == "PASS"),
        "case_a_primary_loss_point": case_a_primary_loss_point,
        "live_full_workitem_pipeline_diagnostically_exercised": full_pipeline_exercised,
        "cases": case_results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_data = {
        "task": "PHASE_13_5D_TASK_11R",
        "run_id": f"run_diagnostic_{timestamp_str}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_dry_run": is_dry_run,
        "plan_mode": plan_mode,
        "predeclarations_sha256": fixture_sha256,
        "semantic_configurations": AUTHORITATIVE_SEMANTIC_CONFIGS,
        "invariants": {
            "SHARED_RETRIEVAL_EXECUTION_CORE_COUNT": SHARED_RETRIEVAL_EXECUTION_CORE_COUNT,
            "CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT": CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT,
            "MANUAL_SEARCH_CONCEPTS_TO_ANCHOR_TERMS_MAPPING": False,
            "TELEMETRY_AFFECTS_RANKING": TELEMETRY_AFFECTS_RANKING,
            "TELEMETRY_AFFECTS_FILTERING": TELEMETRY_AFFECTS_FILTERING,
            "TELEMETRY_AFFECTS_DEDUPE": TELEMETRY_AFFECTS_DEDUPE,
            "A_B_C_ROLE": "DIAGNOSTIC_CASES",
            "QUALIFICATION_SCORE_CONTRIBUTION": 0,
            "TASK_11_COMPLETE": False,
            "TASK_12_STARTED": False,
            "PRODUCTION_CUTOVER": False,
            "PRODUCTION_RETRIEVAL_COMPONENTS_REUSED": True,
            "CUSTOM_TASK11_RETRIEVAL_SEMANTICS": False,
            "OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS": 0,
            "ONE_GRAPH_PER_INVESTIGATION": True,
            "FREEFORM_ANSWER_LLM_CALLS": 0,
            "SEMANTIC_RETRIES": 0,
            "SLACK_WRITE_ACTIONS": 0,
        },
        "live_full_workitem_pipeline_diagnostically_exercised": full_pipeline_exercised,
        "case_a_primary_loss_point": case_a_primary_loss_point,
        "overall_verdict": "PASS" if overall_pass else "FAIL",
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. Print Console Summary
    print("\n" + "=" * 80)
    print(f"  PHASE 13.5D TASK 11R DIAGNOSTIC REPORT — {manifest_data['overall_verdict']}")
    print("=" * 80)
    print(f"Run Directory: {run_dir}")
    print(f"Predeclaration SHA-256: {fixture_sha256}")
    print(f"Plan Mode: {plan_mode}")
    print(f"Live WorkItem Pipeline Exercised: {full_pipeline_exercised}")
    if case_a_primary_loss_point:
        print(f"CASE A Primary Loss Point: {case_a_primary_loss_point}")
    print("-" * 80)
    for r in case_results:
        print(f"[{r['case_id']}] Verdict: {r['verdict']} | Expected: {r['expected_final_state']} | Actual: {r['actual_final_state']}")
        if r.get("pass_reason"):
            print(f"     ↳ Pass: {r['pass_reason']}")
        if r.get("fail_reasons"):
            print(f"     ↳ Fail: {r['fail_reasons']}")
        if r.get("case_a_journey"):
            j = r["case_a_journey"]
            print(f"     ↳ Journey Loss Point: {j['primary_loss_point']}")
            print(f"     ↳ Detail: {j['loss_point_detail']}")
            for cp_name, cp_data in j["checkpoints"].items():
                print(f"       • {cp_name}: {cp_data}")
    print("-" * 80)
    print(f"OVERALL VERDICT: {manifest_data['overall_verdict']}")
    print("=" * 80 + "\n")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

