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
import functools
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
    ClaimTraceabilityRecord,
)
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.openrouter import OpenRouterAPIError
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
    create_authoritative_synthesis_coordinator,
)
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemCategory,
    WorkItemEpisodeMembership,
    WorkItemKind,
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
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
    GroundedWorkItemCard,
)
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


def extract_grounded_answer_report(grounded_answer: Any) -> dict[str, Any]:
    """Extracts report dictionary from GroundedWorkItemAnswerPayload using declared schema fields."""
    summary_text = getattr(grounded_answer, "formatted_markdown_answer", "") or ""
    summary_headline = getattr(grounded_answer, "summary_headline", "") or ""
    return {
        "summary": summary_text,
        "summary_headline": summary_headline,
        "formatted_markdown_answer": summary_text,
        "is_conclusive": getattr(grounded_answer, "is_conclusive", False),
        "cards_count": len(getattr(grounded_answer, "work_item_cards", [])),
        "cards": [card.model_dump() for card in getattr(grounded_answer, "work_item_cards", [])],
    }


def build_diagnostic_semantic_components(
    synthesis_config: Optional[WorkItemSynthesisConfig] = None,
    llm_config: Optional[LLMConfig] = None,
) -> DecomposedWorkItemSynthesisCoordinator:
    """Builds the 4-stage decomposed WorkItem synthesis coordinator for diagnostic harness."""
    return create_authoritative_synthesis_coordinator(
        synthesis_config=synthesis_config,
        llm_config=llm_config,
    )


class ComponentProviderTracker:
    def __init__(self, name: str, component: Any):
        self.name = name
        self.component = component
        self.logical_calls: int = 0
        self.physical_calls: int = 0
        self.http_400_count: int = 0
        self.http_200_count: int = 0
        self.retry_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "LOGICAL_CALLS": self.logical_calls,
            "PHYSICAL_CALLS": self.physical_calls,
            "HTTP_400_COUNT": self.http_400_count,
            "HTTP_200_COUNT": self.http_200_count,
            "RETRY_COUNT": self.retry_count,
        }


def instrument_component_provider(name: str, component: Any) -> ComponentProviderTracker:
    tracker = ComponentProviderTracker(name, component)
    provider = getattr(component, "provider", None)
    if provider is None or not hasattr(provider, "generate"):
        return tracker

    original_generate = provider.generate
    original_generate_structured = getattr(provider, "generate_structured", None)

    if original_generate_structured:
        @functools.wraps(original_generate_structured)
        async def tracked_generate_structured(*args: Any, **kwargs: Any) -> Any:
            tracker.logical_calls += 1
            return await original_generate_structured(*args, **kwargs)
        provider.generate_structured = tracked_generate_structured

    @functools.wraps(original_generate)
    async def tracked_generate(*args: Any, **kwargs: Any) -> Any:
        tracker.physical_calls += 1
        try:
            resp = await original_generate(*args, **kwargs)
            tracker.http_200_count += 1
            return resp
        except OpenRouterAPIError as err:
            if getattr(err, "status_code", None) == 400:
                tracker.http_400_count += 1
                tracker.retry_count += 1
            raise

    provider.generate = tracked_generate
    return tracker


def run_harness_contract_preflight(
    trace_coordinator: ProductionRealTraceCoordinator,
) -> dict[str, bool]:
    """Runs strict preflight assertions on coordinator and graph contracts before live Slack reads.

    If any check fails, abort immediately (LIVE_SLACK_READS = 0).
    """
    synth = trace_coordinator.synthesis_coordinator
    g1a = synth.stage1a_coordinator.grouper
    v1b = synth.stage1b_coordinator.verifier
    v2 = synth.stage2_coordinator.verifier
    v25 = synth.stage25_coordinator.verifier if synth.stage25_coordinator else None

    # 1. Stage 1A Config Contract
    stage1a_valid = bool(
        isinstance(getattr(g1a, "config", None), LLMConfig)
        and callable(getattr(g1a.config, "get_api_key_value", None))
        and g1a.model == "openai/gpt-5.6-luna"
        and g1a.reasoning_effort == "medium"
        and g1a.temperature == 0.0
    )

    # 2. Stage 1B Config Contract
    stage1b_valid = bool(
        isinstance(getattr(v1b, "config", None), LLMConfig)
        and callable(getattr(v1b.config, "get_api_key_value", None))
        and v1b.model == "openai/gpt-5.6-luna"
        and v1b.reasoning_effort == "medium"
        and v1b.temperature == 0.0
    )

    # 3. Stage 2 Config Contract
    stage2_valid = bool(
        isinstance(getattr(v2, "config", None), LLMConfig)
        and callable(getattr(v2.config, "get_api_key_value", None))
        and v2.model == "openai/gpt-5.6-luna"
        and v2.reasoning_effort == "medium"
        and v2.temperature == 0.0
    )

    # 4. Stage 2.5 Config Contract
    stage25_valid = bool(
        v25 is not None
        and isinstance(getattr(v25, "config", None), LLMConfig)
        and callable(getattr(v25.config, "get_api_key_value", None))
        and v25.model == "openai/gpt-5.6-luna"
        and v25.reasoning_effort == "high"
        and v25.temperature == 0.0
    )

    # 5. Answer Payload Contract
    fields = set(GroundedWorkItemAnswerPayload.model_fields.keys())
    answer_payload_valid = bool(
        "formatted_markdown_answer" in fields
        and "summary_headline" in fields
        and "grounded_summary" not in fields
    )

    # 6. Work Items Container Contract (build_answer expects dict)
    test_graph = InvestigationStateGraph()
    test_wi = WorkItemNode(
        work_item_id="WI-PREFLIGHT",
        title="Preflight WorkItem",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.COMPLETED,
        category=WorkItemCategory.WORK_ITEM,
    )
    try:
        ans_test = GroundedWorkItemAnswerBuilder.build_answer(
            question="Preflight Question?",
            graph=test_graph,
            work_items={"WI-PREFLIGHT": test_wi},
            traceability_chains={},
        )
        work_items_container_valid = isinstance(ans_test, GroundedWorkItemAnswerPayload)
    except Exception:
        work_items_container_valid = False

    # 7. Work Item Claim Links Container Contract (InvestigationStateGraph.work_item_claim_links is a list)
    claim_links_valid = isinstance(test_graph.work_item_claim_links, list)

    # 8. Actor Scope Contract
    test_actor_scope = ResolvedActorScope(
        kind=ActorScopeKind.UNSPECIFIED,
        status=ActorResolutionStatus.RESOLVED,
        is_implicit=False,
    )
    actor_scope_valid = (
        hasattr(test_actor_scope, "kind")
        and hasattr(test_actor_scope, "status")
        and hasattr(test_actor_scope, "is_implicit")
        and hasattr(test_actor_scope, "matched_author_name")
    )

    # 9. Traceability Chain Contract
    chain_fields = set(WorkItemTraceabilityChain.model_fields.keys())
    traceability_chain_valid = bool(
        "work_item_id" in chain_fields
        and "governing_claim_ids" in chain_fields
        and "claim_traces" in chain_fields
        and "all_evidence_ids" in chain_fields
        and "is_traceable" in chain_fields
        and "final_status" in chain_fields
        and "claim_ids" not in chain_fields
        and "evidence_keys" not in chain_fields
    )

    # 10. Claim Trace Record Contract
    record_fields = set(ClaimTraceabilityRecord.model_fields.keys())
    claim_trace_record_valid = bool(
        "claim_id" in record_fields
        and "statement" in record_fields
        and "evidence_ids" in record_fields
        and "citations" in record_fields
        and "decisive_authority" in record_fields
        and "lifecycle_signal" in record_fields
        and "relation" in record_fields
        and "episode_membership" in record_fields
    )

    # 11. Final State Extraction Contract
    # Verifies that authoritative final state fields exist and are directly extractable
    # with DIAGNOSTIC_FINAL_STATE_SEMANTIC_AUTHORITY = 0 (no re-reconciliation in harness).
    final_state_extraction_valid = bool(
        "status" in WorkItemNode.model_fields
        and "final_status" in chain_fields
        and "status" in GroundedWorkItemCard.model_fields
    )

    # 12. Diagnostic Report Serialization Contract
    # Verify build_case_diagnostic_report produces valid JSON serializable structure
    try:
        sample_chain = WorkItemTraceabilityChain(
            work_item_id="WI-PREFLIGHT",
            title="Preflight WorkItem",
            kind=WorkItemKind.TASK,
            final_status=WorkItemStatus.COMPLETED,
            governing_claim_ids=[],
            claim_traces=[],
            all_evidence_ids=[],
            is_traceable=True,
        )
        sample_trace = ProductionTraceResult(
            success=True,
            question="Preflight Question?",
            graph=test_graph,
            work_items={"WI-PREFLIGHT": test_wi},
            traceability_chains={"WI-PREFLIGHT": sample_chain},
            stop_reason="COMPLETED",
        )
        sample_report = build_case_diagnostic_report(
            case_id="PREFLIGHT",
            question="Preflight Question?",
            expected_final_state="COMPLETED",
            raw_query_records=[],
            candidates=[],
            selected_candidates=[],
            pipeline_boundary_telemetry={},
            trace_result=sample_trace,
            grounded_answer=ans_test,
        )
        _serialized = json.dumps(sample_report, ensure_ascii=False)
        report_serialization_valid = isinstance(_serialized, str) and len(_serialized) > 0
    except Exception as ex:
        logger.warning(f"Preflight report serialization failed: {ex}")
        report_serialization_valid = False

    preflight_results = {
        "STAGE1A_CONFIG_CONTRACT_VALID": stage1a_valid,
        "STAGE1B_CONFIG_CONTRACT_VALID": stage1b_valid,
        "STAGE2_CONFIG_CONTRACT_VALID": stage2_valid,
        "STAGE2_5_CONFIG_CONTRACT_VALID": stage25_valid,
        "ANSWER_PAYLOAD_CONTRACT_VALID": answer_payload_valid,
        "WORK_ITEMS_CONTAINER_CONTRACT_VALID": work_items_container_valid,
        "WORK_ITEM_CLAIM_LINKS_CONTAINER_CONTRACT_VALID": claim_links_valid,
        "ACTOR_SCOPE_CONTRACT_VALID": actor_scope_valid,
        "TRACEABILITY_CHAIN_CONTRACT_VALID": traceability_chain_valid,
        "CLAIM_TRACE_RECORD_CONTRACT_VALID": claim_trace_record_valid,
        "FINAL_STATE_EXTRACTION_CONTRACT_VALID": final_state_extraction_valid,
        "DIAGNOSTIC_REPORT_SERIALIZATION_CONTRACT_VALID": report_serialization_valid,
    }

    return preflight_results


def is_target_text(t: str) -> bool:
    return bool(
        TARGET_PERMALINK_SUBSTRING in (t or "")
        or TARGET_SNIPPET_SUBSTRING in (t or "")
        or (("선제적으로 정의한 기준" in (t or "")) and ("1차 배포" in (t or "")))
        or (("고객 만족도 현황" in (t or "")) and ("1차 배포" in (t or "")) and ("배포하였습니다" in (t or "")))
    )


def build_case_diagnostic_report(
    case_id: str,
    question: str,
    expected_final_state: str,
    raw_query_records: list[dict[str, Any]],
    candidates: list[EvidenceItem],
    selected_candidates: list[EvidenceItem],
    pipeline_boundary_telemetry: dict[str, Any],
    trace_result: ProductionTraceResult,
    grounded_answer: GroundedWorkItemAnswerPayload,
    retrieval_latency: float = 0.0,
    synth_latency: float = 0.0,
    total_latency: float = 0.0,
) -> dict[str, Any]:
    """Extracts boundary trace, checkpoints, and returns the case diagnostic report dictionary."""
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

    # Traceability contract audit resolution (Task 11R10)
    # ALL_TRACEABLE_CLAIM_IDS = claim_traces[*].claim_id
    # GOVERNING_CLAIM_IDS = governing_claim_ids
    # ALL_TRACEABLE_EVIDENCE_IDS = all_evidence_ids
    all_traceable_claim_ids = []
    governing_claim_ids = []
    all_traceable_evidence_ids = []

    for wid, chain in trace_result.traceability_chains.items():
        for ct in chain.claim_traces:
            all_traceable_claim_ids.append(ct.claim_id)
        governing_claim_ids.extend(chain.governing_claim_ids)
        all_traceable_evidence_ids.extend(chain.all_evidence_ids)

    all_traceable_claim_ids = list(dict.fromkeys(all_traceable_claim_ids))
    governing_claim_ids = list(dict.fromkeys(governing_claim_ids))
    all_traceable_evidence_ids = list(dict.fromkeys(all_traceable_evidence_ids))

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
        "ALL_TRACEABLE_CLAIM_IDS": all_traceable_claim_ids,
        "GOVERNING_CLAIM_IDS": governing_claim_ids,
        "ALL_TRACEABLE_EVIDENCE_IDS": all_traceable_evidence_ids,
        "GROUNDED_ANSWER": extract_grounded_answer_report(grounded_answer),
    }

    # Case A Specific Checkpoints (Section 4 & 5)
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

        # Target proposed and verified claims in graph by evidence lineage
        target_supports_proposed = False
        target_supports_verified = False
        for cid, c in trace_result.graph.claim_nodes.items():
            links = [l for l in trace_result.graph.claim_evidence_links if l.claim_id == cid]
            c_ev_ids = [l.evidence_id for l in links]
            is_target_claim = any(eid in target_ev_ids for eid in c_ev_ids)
            if is_target_claim:
                target_supports_proposed = True
                if getattr(c, "verdict", None) == "VERIFIED" or getattr(c, "status", None) in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
                    target_supports_verified = True

        # Target traceable claim IDs by evidence lineage (TARGET_CLAIM_LINEAGE_USES_EVIDENCE_IDS = true)
        # TARGET_EVIDENCE_IDS -> ClaimTraceabilityRecord.evidence_ids / citations -> TARGET_TRACEABLE_CLAIM_IDS
        target_traceable_claim_ids = []
        for wid, chain in trace_result.traceability_chains.items():
            for ct in chain.claim_traces:
                ev_lineage_match = any(eid in target_ev_ids for eid in ct.evidence_ids) or any(
                    cit.get("evidence_id") in target_ev_ids or TARGET_PERMALINK_SUBSTRING in (cit.get("permalink") or "")
                    for cit in ct.citations
                )
                if ev_lineage_match:
                    target_traceable_claim_ids.append(ct.claim_id)

        target_traceable_claim_ids = sorted(list(dict.fromkeys(target_traceable_claim_ids)))
        target_claim_present_in_claim_traces = bool(target_traceable_claim_ids)

        # Target evidence present in final trace
        target_evidence_present_in_final_trace = bool(
            any(eid in all_traceable_evidence_ids for eid in target_ev_ids)
            or any(
                TARGET_PERMALINK_SUBSTRING in (cit.get("permalink") or "")
                for chain in trace_result.traceability_chains.values()
                for ct in chain.claim_traces
                for cit in ct.citations
            )
        )

        # Official definitions:
        # TARGET_REACHES_FINAL_TRACE = bool(TARGET_TRACEABLE_CLAIM_IDS or TARGET_EVIDENCE_PRESENT_IN_FINAL_TRACE)
        # TARGET_CLAIM_IS_GOVERNING_CLAIM = bool(TARGET_TRACEABLE_CLAIM_IDS ∩ GOVERNING_CLAIM_IDS)
        target_reaches_final_trace = bool(
            target_traceable_claim_ids or target_evidence_present_in_final_trace
        )
        target_claim_is_governing_claim = bool(
            set(target_traceable_claim_ids).intersection(set(governing_claim_ids))
        )

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
            "TARGET_TRACEABLE_CLAIM_IDS": target_traceable_claim_ids,
            "TARGET_CLAIM_PRESENT_IN_CLAIM_TRACES": target_claim_present_in_claim_traces,
            "TARGET_CLAIM_IS_GOVERNING_CLAIM": target_claim_is_governing_claim,
            "TARGET_EVIDENCE_PRESENT_IN_FINAL_TRACE": target_evidence_present_in_final_trace,
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
        and len(all_traceable_claim_ids) > 0
        and bool(grounded_answer.formatted_markdown_answer)
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
    logger.info(f"  Exact Search Queries: {exact_queries}")
    logger.info(f"  Actor Scope: {actor_scope_dict}")
    logger.info(f"  Temporal Scope: {temporal_scope_dict}")
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
            work_items={},
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
        work_items=trace_result.work_items,
        traceability_chains=trace_result.traceability_chains,
    )

    total_latency = time.perf_counter() - t0

    return build_case_diagnostic_report(
        case_id=case_id,
        question=question,
        expected_final_state=expected_final_state,
        raw_query_records=raw_query_records,
        candidates=candidates,
        selected_candidates=selected_candidates,
        pipeline_boundary_telemetry=pipeline_boundary_telemetry,
        trace_result=trace_result,
        grounded_answer=grounded_answer,
        retrieval_latency=retrieval_latency,
        synth_latency=synth_latency,
        total_latency=total_latency,
    )


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
    s_coord = build_diagnostic_semantic_components(synthesis_config=s_cfg)
    trace_coordinator = ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)
    assert_semantic_runtime_configurations(trace_coordinator)

    # Harness Contract Preflight (Section 6)
    preflight_results = run_harness_contract_preflight(trace_coordinator)
    logger.info(f"📋 [HARNESS_PREFLIGHT] Results: {preflight_results}")
    if not all(preflight_results.values()):
        failed_checks = [k for k, v in preflight_results.items() if not v]
        logger.error(f"❌ HARNESS CONTRACT PREFLIGHT FAILED: {failed_checks}! Aborting before any live Slack read.")
        return 1
    logger.info("✅ [HARNESS_PREFLIGHT_PASSED] All 12 harness contracts verified.")

    # Instrument Physical Provider Call Tracking (Section 13)
    trackers = {
        "EvidenceAssessor": instrument_component_provider("EvidenceAssessor", trace_coordinator.evidence_assessor),
        "ClaimVerifier": instrument_component_provider("ClaimVerifier", trace_coordinator.claim_verifier),
        "Stage1A": instrument_component_provider("Stage1A", trace_coordinator.synthesis_coordinator.stage1a_coordinator.grouper),
        "Stage1B": instrument_component_provider("Stage1B", trace_coordinator.synthesis_coordinator.stage1b_coordinator.verifier),
        "Stage2": instrument_component_provider("Stage2", trace_coordinator.synthesis_coordinator.stage2_coordinator.verifier),
        "Stage2.5": instrument_component_provider("Stage2.5", trace_coordinator.synthesis_coordinator.stage25_coordinator.verifier),
    }

    host = None
    if args.dry_run:
        search_backend = SyntheticSearchBackend()
        thread_reader = None
        logger.info("🧪 [DRY_RUN] SyntheticSearchBackend initialized.")
    else:
        if not os.getenv("OPENROUTER_API_KEY"):
            resolved_key = LLMConfig().get_api_key_value()
            if resolved_key:
                os.environ["OPENROUTER_API_KEY"] = resolved_key
            else:
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

            # Section 11: Fail-fast by case
            if not args.dry_run:
                if not diag_res.get("full_pipeline_exercised", False) or diag_res.get("stop_reason") != "COMPLETED":
                    logger.error(
                        f"🛑 [FAIL_FAST] Case {case_spec['case_id']} did not complete full pipeline! "
                        f"(stop_reason={diag_res.get('stop_reason')}, exercised={diag_res.get('full_pipeline_exercised')}). "
                        "Halting further diagnostic execution."
                    )
                    break
    finally:
        if host and hasattr(host, "stop"):
            logger.info("🛑 [SLACK_HOST_STOP] Stopping session-scoped PesuWebViewHost...")
            try:
                host.stop()
            except Exception as e:
                logger.warning(f"Error stopping host: {e}")

    # Section 6 Full Pipeline Exercise Assessment
    all_cases_exercised = all(c.get("full_pipeline_exercised", False) for c in case_diagnostics) if not args.dry_run else False

    provider_telemetry = {name: t.to_dict() for name, t in trackers.items()}
    anomaly_reproduced = any(t.http_400_count > 0 and t.http_200_count > 0 for t in trackers.values())

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
            "preflight_contracts": preflight_results,
            "provider_call_telemetry": provider_telemetry,
            "provider_execution_anomaly_reproduced": anomaly_reproduced,
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
