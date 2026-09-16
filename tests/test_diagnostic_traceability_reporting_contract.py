# -*- coding: utf-8 -*-
"""Contract tests for diagnostic harness traceability reporting."""

import pytest
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    EvidenceNode,
    ClaimNode,
    ClaimKind,
    ClaimStatus,
    ClaimEvidenceRelation,
    WorkItemNode,
    WorkItemKind,
    WorkItemStatus,
    WorkItemCategory,
    WorkItemClaimLink,
    ClaimEvidenceLink,
    WorkItemEpisodeMembership,
    WorkItemClaimRelation,
    WorkItemLifecycleSignal,
    DecisiveAuthorityDecision,
)
from pesu_agent.investigation.workitem_synthesis import (
    WorkItemTraceabilityChain,
    ClaimTraceabilityRecord,
)
from pesu_agent.investigation.production_trace_coordinator import ProductionTraceResult
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
)
from pesu_agent.investigation.models import EvidenceItem
from scripts.diagnose_task11r7_full_production_pipeline import build_case_diagnostic_report


def _build_test_trace_result() -> tuple[ProductionTraceResult, GroundedWorkItemAnswerPayload, list[EvidenceItem]]:
    graph = InvestigationStateGraph(question="1차 배포가 완료된 상태인가?")
    ev = EvidenceNode(
        evidence_id="E1",
        text="선제적으로 정의한 기준 기반 1차 배포하였습니다 (https://slack.com/archives/C123/p1786425172544279)",
        canonical_permalink="https://slack.com/archives/C123/p1786425172544279",
        presentation_author="U12345",
        channel="C123",
        timestamp="1786425172.544279",
    )
    graph.add_evidence(ev)

    claim = ClaimNode(
        claim_id="C1",
        statement="선제적으로 정의한 기준 기반 1차 배포하였습니다",
        kind=ClaimKind.FACT,
        status=ClaimStatus.SUPPORTED,
    )
    graph.add_claim(claim)
    graph.add_claim_evidence_link(
        ClaimEvidenceLink(
            link_id="CEL-1",
            claim_id="C1",
            evidence_id="E1",
            relation=ClaimEvidenceRelation.SUPPORTS,
        )
    )

    wi = WorkItemNode(
        work_item_id="WI-1",
        title="1차 배포",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.COMPLETED,
        category=WorkItemCategory.WORK_ITEM,
    )
    graph.work_items["WI-1"] = wi
    graph.add_work_item_claim_link(
        WorkItemClaimLink(
            link_id="WICL-1",
            work_item_id="WI-1",
            claim_id="C1",
            episode_membership=WorkItemEpisodeMembership.MEMBER,
            relation=WorkItemClaimRelation.PRIMARY,
            lifecycle_signal=WorkItemLifecycleSignal.COMPLETION,
            decisive_authority=DecisiveAuthorityDecision.CONFIRMED,
        )
    )

    # Genuine production-shaped WorkItemTraceabilityChain (BaseModel)
    # Notice: NO claim_ids or evidence_keys attribute!
    chain = WorkItemTraceabilityChain(
        work_item_id="WI-1",
        title="1차 배포",
        kind=WorkItemKind.TASK,
        final_status=WorkItemStatus.COMPLETED,
        governing_claim_ids=["C1"],
        claim_traces=[
            ClaimTraceabilityRecord(
                claim_id="C1",
                statement="선제적으로 정의한 기준 기반 1차 배포하였습니다",
                episode_membership=WorkItemEpisodeMembership.MEMBER,
                relation=WorkItemClaimRelation.PRIMARY,
                lifecycle_signal=WorkItemLifecycleSignal.COMPLETION,
                decisive_authority=DecisiveAuthorityDecision.CONFIRMED,
                evidence_ids=["E1"],
                citations=[{
                    "evidence_id": "E1",
                    "permalink": "https://slack.com/archives/C123/p1786425172544279",
                    "author": "U12345",
                    "timestamp": "1786425172.544279",
                    "snippet": "선제적으로 정의한 기준 기반 1차 배포하였습니다",
                }],
            )
        ],
        all_evidence_ids=["E1"],
        is_traceable=True,
    )

    trace_result = ProductionTraceResult(
        success=True,
        question="1차 배포가 완료된 상태인가?",
        graph=graph,
        work_items={"WI-1": wi},
        traceability_chains={"WI-1": chain},
        stop_reason="COMPLETED",
    )

    answer_builder = GroundedWorkItemAnswerBuilder()
    grounded_answer = answer_builder.build_answer(
        question="1차 배포가 완료된 상태인가?",
        graph=graph,
        work_items={"WI-1": wi},
        traceability_chains={"WI-1": chain},
    )

    cand = EvidenceItem(
        evidence_id="E1",
        text="선제적으로 정의한 기준 기반 1차 배포하였습니다 (https://slack.com/archives/C123/p1786425172544279)",
        permalink="https://slack.com/archives/C123/p1786425172544279",
        author="U12345",
        channel="C123",
        timestamp="1786425172.544279",
        source="slack",
    )

    return trace_result, grounded_answer, [cand]


def test_production_workitem_traceability_chain_lacks_claim_ids():
    """Confirms production WorkItemTraceabilityChain has no claim_ids attribute."""
    trace_result, _, _ = _build_test_trace_result()
    chain = trace_result.traceability_chains["WI-1"]

    assert hasattr(chain, "governing_claim_ids")
    assert hasattr(chain, "claim_traces")
    assert hasattr(chain, "all_evidence_ids")
    assert not hasattr(chain, "claim_ids"), "Production model must not have ad-hoc claim_ids"
    assert not hasattr(chain, "evidence_keys"), "Production model must not have ad-hoc evidence_keys"

    with pytest.raises(AttributeError):
        _ = chain.claim_ids


def test_diagnostic_reporting_path_contract():
    """TDD test: build_case_diagnostic_report must execute without AttributeError and produce canonical trace fields."""
    trace_result, grounded_answer, candidates = _build_test_trace_result()

    report = build_case_diagnostic_report(
        case_id="CASE_A",
        question="1차 배포가 완료된 상태인가?",
        expected_final_state="COMPLETED",
        raw_query_records=[{"raw_items": [{"url": "https://slack.com/archives/C123/p1786425172544279", "body_valid": True}]}],
        candidates=candidates,
        selected_candidates=candidates,
        pipeline_boundary_telemetry={"post_dedupe_keys": ["E1"]},
        trace_result=trace_result,
        grounded_answer=grounded_answer,
    )

    # Verification of canonical reporting fields
    assert report["case_id"] == "CASE_A"
    assert report["actual_final_state"] == "COMPLETED"
    assert report["full_pipeline_exercised"] is True

    boundary = report["pipeline_boundary_trace"]
    assert "ALL_TRACEABLE_CLAIM_IDS" in boundary
    assert "GOVERNING_CLAIM_IDS" in boundary
    assert "ALL_TRACEABLE_EVIDENCE_IDS" in boundary
    assert boundary["ALL_TRACEABLE_CLAIM_IDS"] == ["C1"]
    assert boundary["GOVERNING_CLAIM_IDS"] == ["C1"]
    assert boundary["ALL_TRACEABLE_EVIDENCE_IDS"] == ["E1"]

    checkpoints = report["case_a_checkpoints"]
    assert checkpoints["TARGET_CLAIM_PRESENT_IN_CLAIM_TRACES"] is True
    assert checkpoints["TARGET_CLAIM_IS_GOVERNING_CLAIM"] is True
    assert checkpoints["TARGET_EVIDENCE_PRESENT_IN_FINAL_TRACE"] is True
    assert checkpoints["TARGET_REACHES_FINAL_TRACE"] is True
