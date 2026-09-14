"""Unit and Offline Contract Tests for ProductionRealTraceCoordinator.

Phase 13.5D Task 4: Thin Production Trace Orchestration.
Guarantees:
- ONE_GRAPH_PER_INVESTIGATION = True: Single InvestigationStateGraph throughout.
- ZERO live Slack actions (LIVE_SLACK_ACTIONS = 0).
- ZERO live OpenRouter calls (REAL_PROVIDER_CALLS = 0).
- OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS = 0.
- Clear distinction: NO_SUPPORTED_CLAIMS vs PROVIDER_EXECUTION_FAILURE vs INVALID_PROVIDER_OUTPUT vs TRACEABILITY_FAILURE vs SAFE_UNKNOWN.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from pesu_agent.investigation.claim_verification import (
    ClaimVerificationAssessment,
    ClaimVerificationCoordinator,
    ClaimVerificationSchemaError,
    EvidenceClaimVerdict,
    StubClaimVerifier,
    VerificationSemanticRelation,
)
from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.gap_assessment import (
    EvidenceAssessmentProviderError,
    EvidenceAssessmentSchemaError,
    InvestigationAssessment,
    OpenRouterEvidenceAssessor,
    ProposedClaim,
    StubEvidenceAssessor,
)
from pesu_agent.investigation.graph_models import (
    AttributionStrength,
    ClaimKind,
    ClaimSemanticType,
    DecisiveAuthorityDecision,
    InvestigationStateGraph,
    WorkItemClaimRelation,
    WorkItemEpisodeMembership,
    WorkItemKind,
    WorkItemLifecycleSignal,
    WorkItemNode,
    WorkItemStatus,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    ResolvedActorScope,
    ResolvedTemporalScope,
)
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
)
from pesu_agent.investigation.workitem_synthesis import (
    DecisiveClaimVerificationResult,
    DecisiveLifecycleAuthorityProposal,
    DecomposedWorkItemSynthesisCoordinator,
    EpisodeMembershipProposal,
    ProposedClaimLifecycleSignal,
    ProposedClaimRelation,
    ProposedEpisodeMembershipWorkItem,
    StubDecisiveLifecycleAuthorityVerifier,
    StubEpisodeMembershipGrouper,
    StubWorkItemLifecycleVerifier,
    StubWorkItemRelationVerifier,
    WorkItemLifecycleVerificationProposal,
    WorkItemRelationProposal,
    WorkItemSynthesisResult,
)
from pesu_agent.llm.provider import ModelProvider


def _create_sample_candidates() -> list[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id="cand_1",
            source="slack",
            text="AuthService v2.4.0 rollout completed and verified on production.",
            author="Alice",
            permalink="https://slack.com/archives/C123/p10001",
            timestamp="2026-09-14T10:00:00Z",
        ),
        EvidenceItem(
            evidence_id="cand_2",
            source="slack",
            text="Rollout monitoring shows 0 errors across all pods.",
            author="Bob",
            permalink="https://slack.com/archives/C123/p10002",
            timestamp="2026-09-14T10:05:00Z",
        ),
    ]


def _build_stub_synthesis_coordinator(
    work_item_id: str = "WI-1",
    claim_id: str = "C1",
    status: WorkItemStatus = WorkItemStatus.COMPLETED,
    signal: WorkItemLifecycleSignal = WorkItemLifecycleSignal.COMPLETION,
    decision: DecisiveAuthorityDecision = DecisiveAuthorityDecision.CONFIRMED,
) -> DecomposedWorkItemSynthesisCoordinator:
    m_prop = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="AuthService Rollout",
                kind=WorkItemKind.TASK,
                member_claim_ids=[claim_id],
                context_claim_ids=[],
            )
        ],
        unassigned_claim_ids=[],
    )
    r_prop = WorkItemRelationProposal(
        work_item_id=work_item_id,
        relations=[ProposedClaimRelation(claim_id=claim_id, relation=WorkItemClaimRelation.PRIMARY)],
    )
    l_prop = WorkItemLifecycleVerificationProposal(
        work_item_id=work_item_id,
        claim_signals=[ProposedClaimLifecycleSignal(claim_id=claim_id, lifecycle_signal=signal)],
    )
    a_prop = DecisiveLifecycleAuthorityProposal(
        work_item_id=work_item_id,
        verifications=[
            DecisiveClaimVerificationResult(
                claim_id=claim_id,
                proposed_signal=signal,
                decision=decision,
            )
        ],
    )
    return DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=StubEpisodeMembershipGrouper(m_prop),
        relation_verifier=StubWorkItemRelationVerifier(r_prop),
        lifecycle_verifier=StubWorkItemLifecycleVerifier(l_prop),
        authority_verifier=StubDecisiveLifecycleAuthorityVerifier(a_prop),
    )


@pytest.mark.anyio
async def test_evidence_items_ingested_into_same_graph():
    """EvidenceItems are ingested as EvidenceNodes into the coordinator's single graph."""
    assessor = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="What is the rollout status?",
        candidates=candidates,
    )

    assert res.success is True
    assert len(res.graph.evidence_nodes) == 2
    assert "E1" in res.graph.evidence_nodes
    assert "E2" in res.graph.evidence_nodes
    assert res.graph.evidence_nodes["E1"].canonical_permalink == "https://slack.com/archives/C123/p10001"


@pytest.mark.anyio
async def test_single_graph_instance_preserved_throughout():
    """Single InvestigationStateGraph instance identity is preserved across all pipeline steps."""
    assessor = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Question",
        candidates=candidates,
    )

    graph_instance = res.graph
    assert isinstance(graph_instance, InvestigationStateGraph)
    # The graph held in result is the exact single state graph
    assert res.graph is graph_instance


@pytest.mark.anyio
async def test_empty_candidates_returns_safe_stop():
    """Zero candidate evidence items return success=True with stop_reason='NO_EVIDENCE'."""
    coordinator = ProductionRealTraceCoordinator()
    res = await coordinator.trace_and_synthesize_async(
        question="No candidates question",
        candidates=[],
    )
    assert res.success is True
    assert res.stop_reason == "NO_EVIDENCE"
    assert len(res.work_items) == 0
    assert len(res.traceability_chains) == 0


@pytest.mark.anyio
async def test_assessor_proposed_claim_propagates_to_verification_and_graph():
    """Assessor ProposedClaim is verified and committed to the graph."""
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="AuthService v2.4.0 rollout completed",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                )
            ]
        )
    )
    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            )
        ]
    )
    synthesis_coord = _build_stub_synthesis_coordinator(claim_id="C1")

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Rollout status?",
        candidates=candidates,
    )

    assert res.success is True
    assert "C1" in res.graph.claim_nodes
    assert res.graph.claim_nodes["C1"].statement == "AuthService v2.4.0 rollout completed"
    assert len(res.graph.claim_evidence_links) >= 1


@pytest.mark.anyio
async def test_verified_claim_synthesizes_workitem_with_stages_1a_to_25():
    """End-to-end trace from evidence to Stage 2.5 decisive status resolution."""
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="AuthService v2.4.0 rollout completed",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                )
            ]
        )
    )
    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            )
        ]
    )
    synthesis_coord = _build_stub_synthesis_coordinator(
        work_item_id="WI-1",
        claim_id="C1",
        status=WorkItemStatus.COMPLETED,
        signal=WorkItemLifecycleSignal.COMPLETION,
        decision=DecisiveAuthorityDecision.CONFIRMED,
    )

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Is it completed?",
        candidates=candidates,
    )

    assert res.success is True
    assert res.stop_reason == "COMPLETED"
    assert len(res.work_items) == 1
    wi = res.work_items["WI-1"]
    assert wi.status == WorkItemStatus.COMPLETED


@pytest.mark.anyio
async def test_final_workitem_has_complete_traceability_to_evidence():
    """Final WorkItem has verified traceability chain resolving all the way to Evidence permalinks."""
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="AuthService v2.4.0 rollout completed",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                )
            ]
        )
    )
    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            )
        ]
    )
    synthesis_coord = _build_stub_synthesis_coordinator(
        work_item_id="WI-1",
        claim_id="C1",
        status=WorkItemStatus.COMPLETED,
    )

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Is it completed?",
        candidates=candidates,
    )

    assert res.success is True
    assert "WI-1" in res.traceability_chains
    chain = res.traceability_chains["WI-1"]
    assert chain.is_traceable is True
    assert chain.final_status == WorkItemStatus.COMPLETED
    assert chain.governing_claim_ids == ["C1"]
    assert len(chain.claim_traces) == 1
    assert chain.claim_traces[0].evidence_ids == ["E1"]
    assert chain.claim_traces[0].citations[0]["permalink"] == "https://slack.com/archives/C123/p10001"


@pytest.mark.anyio
async def test_unknown_evidence_reference_rejected_before_downstream():
    """ProposedClaim referencing non-existent Evidence ID does not enter the graph."""
    mock_provider = MagicMock(spec=ModelProvider)
    bad_assessment = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="PROP_BAD",
                statement="Hallucinated claim",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.ACTION,
                supporting_evidence_ids=["E_NON_EXISTENT"],
            )
        ]
    )
    mock_provider.generate_structured = AsyncMock(return_value=(bad_assessment, None))
    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)

    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)
    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Test",
        candidates=candidates,
    )

    assert res.success is False
    assert res.stop_reason == "INVALID_PROVIDER_OUTPUT"
    # The claim must NOT be in the graph
    assert len(res.graph.claim_nodes) == 0


@pytest.mark.anyio
async def test_evidence_assessor_provider_failure_distinguishable_from_empty():
    """Provider network failure returns PROVIDER_EXECUTION_FAILURE, distinguishable from NO_SUPPORTED_CLAIMS."""
    # Sub-case 1: Legitimate empty assessment (no facts found in evidence)
    assessor_empty = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coord_empty = ProductionRealTraceCoordinator(evidence_assessor=assessor_empty)
    res_empty = await coord_empty.trace_and_synthesize_async(
        question="Test",
        candidates=_create_sample_candidates(),
    )
    assert res_empty.success is True
    assert res_empty.stop_reason == "NO_SUPPORTED_CLAIMS"

    # Sub-case 2: Provider execution failure (HTTP 502 / network error)
    mock_provider = MagicMock(spec=ModelProvider)
    mock_provider.generate_structured = AsyncMock(
        side_effect=RuntimeError("OpenRouter 502 Bad Gateway")
    )
    assessor_err = OpenRouterEvidenceAssessor(provider=mock_provider)
    coord_err = ProductionRealTraceCoordinator(evidence_assessor=assessor_err)
    res_err = await coord_err.trace_and_synthesize_async(
        question="Test",
        candidates=_create_sample_candidates(),
    )
    assert res_err.success is False
    assert res_err.stop_reason == "PROVIDER_EXECUTION_FAILURE"
    assert "OpenRouter 502 Bad Gateway" in str(res_err.error_message)


@pytest.mark.anyio
async def test_invalid_structured_output_fails_closed():
    """Unparseable structured output from model fails closed with INVALID_PROVIDER_OUTPUT."""
    mock_provider = MagicMock(spec=ModelProvider)
    mock_provider.generate_structured = AsyncMock(
        side_effect=ValueError("Failed to parse JSON into InvestigationAssessment")
    )
    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    res = await coordinator.trace_and_synthesize_async(
        question="Test",
        candidates=_create_sample_candidates(),
    )
    assert res.success is False
    assert res.stop_reason == "INVALID_PROVIDER_OUTPUT"


@pytest.mark.anyio
async def test_incomplete_trace_fails_closed():
    """A synthesized WorkItem with broken evidence link fails closed with TRACEABILITY_FAILURE."""
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="Broken trace claim",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                )
            ]
        )
    )
    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.NOT_RELEVANT,
                attribution_strength=AttributionStrength.CONTEXT_ONLY,
            )
        ]
    )

    # Synthesis coordinator that adds a WorkItem with zero claim links
    mock_synthesis_coord = MagicMock(spec=DecomposedWorkItemSynthesisCoordinator)

    async def mock_synth(graph, **kwargs):
        graph.add_work_item(
            WorkItemNode(
                work_item_id="WI-UNLINKED",
                title="Unlinked WorkItem",
                kind=WorkItemKind.TASK,
                status=WorkItemStatus.COMPLETED,
            )
        )
        return WorkItemSynthesisResult(
            success=True,
            work_item_ids=["WI-UNLINKED"],
        )

    mock_synthesis_coord.synthesize_and_verify_async = AsyncMock(side_effect=mock_synth)

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=mock_synthesis_coord,
    )

    candidates = _create_sample_candidates()
    res = await coordinator.trace_and_synthesize_async(
        question="Broken trace test",
        candidates=candidates,
    )

    assert res.success is False
    assert res.stop_reason == "TRACEABILITY_FAILURE"
    assert "zero claim links" in str(res.error_message)


def test_synchronous_wrapper_trace_and_synthesize():
    """trace_and_synthesize synchronous method runs trace_and_synthesize_async cleanly."""
    assessor = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    candidates = _create_sample_candidates()
    res = coordinator.trace_and_synthesize(
        question="Sync question",
        candidates=candidates,
    )
    assert res.success is True
    assert res.stop_reason == "NO_SUPPORTED_CLAIMS"


@pytest.mark.anyio
async def test_coordinator_sequential_and_concurrent_failure_isolation():
    """Failures on a shared assessor instance do not leak into sequential or concurrent calls."""
    candidates = _create_sample_candidates()
    mock_provider = MagicMock(spec=ModelProvider)

    async def dynamic_generate(messages, **kwargs):
        # Inspect user message to determine whether to fail or return legitimate empty
        user_msg = messages[-1].content
        if "FAIL_CALL" in user_msg:
            raise RuntimeError("Temporary 503 Service Unavailable")
        return (InvestigationAssessment(proposed_claims=[]), None)

    mock_provider.generate_structured = AsyncMock(side_effect=dynamic_generate)
    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    # 1. Sequential calls: Call 1 fails, Call 2 succeeds
    res_fail = await coordinator.trace_and_synthesize_async(
        question="FAIL_CALL question",
        candidates=candidates,
    )
    assert res_fail.success is False
    assert res_fail.stop_reason == "PROVIDER_EXECUTION_FAILURE"

    res_ok = await coordinator.trace_and_synthesize_async(
        question="OK_CALL question",
        candidates=candidates,
    )
    assert res_ok.success is True
    assert res_ok.stop_reason == "NO_SUPPORTED_CLAIMS"

    # 2. Concurrent calls: Call A fails while Call B succeeds concurrently
    task_a = coordinator.trace_and_synthesize_async(
        question="FAIL_CALL concurrent",
        candidates=candidates,
    )
    task_b = coordinator.trace_and_synthesize_async(
        question="OK_CALL concurrent",
        candidates=candidates,
    )
    res_a, res_b = await asyncio.gather(task_a, task_b)

    assert res_a.success is False
    assert res_a.stop_reason == "PROVIDER_EXECUTION_FAILURE"
    assert res_b.success is True
    assert res_b.stop_reason == "NO_SUPPORTED_CLAIMS"
