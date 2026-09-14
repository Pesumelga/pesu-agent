"""Phase 13.5D Task 5: Offline Production-Shaped Composed E2E Harness.

Validates the full end-to-end integration:
  production-shaped EvidenceItem
  → InvestigationStateGraph (EvidenceNode)
  → Real ProposedClaim formation
  → Real Claim verification
  → Stage 1A (Membership)
  → Stage 1B (Relation)
  → Stage 2 (Lifecycle)
  → Stage 2.5 (Decisive Authority)
  → Deterministic Reconciliation
  → Final WorkItem
  → Traceability Chain

Guarantees:
- ZERO live Slack actions (LIVE_SLACK_ACTIONS = 0).
- ZERO live OpenRouter network calls (REAL_PROVIDER_CALLS = 0).
- SLACK_WRITE_ACTIONS = 0.
- ONE_GRAPH_PER_INVESTIGATION = True: Single state graph throughout.
- EVERY_SUCCESSFUL_FINAL_STATE_HAS_COMPLETE_TRACE = True.
- Provider failure is strictly distinguished from legitimate no-supported-claims.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from pesu_agent.investigation.claim_verification import (
    ClaimVerificationAssessment,
    ClaimVerificationCoordinator,
    EvidenceClaimVerdict,
    StubClaimVerifier,
    VerificationSemanticRelation,
)
from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.gap_assessment import (
    AssessmentAnswerability,
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
    build_work_item_traceability_chain,
)
from pesu_agent.llm.provider import ModelProvider


# ---------------------------------------------------------------------------
# Test Fixture Helpers
# ---------------------------------------------------------------------------

def _make_evidence_item(
    evidence_id: str,
    text: str,
    author: str = "Engineer",
    permalink: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        source="slack",
        text=text,
        author=author,
        permalink=permalink or f"https://slack.com/archives/C_PROD/{evidence_id}",
        timestamp="2026-09-14T12:00:00Z",
    )


# ---------------------------------------------------------------------------
# Scenario A: Clear Progress / Completion
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_a_clear_progress_completion():
    """Scenario A: Clear evidence results in verified claim, COMPLETED WorkItem, and complete trace."""
    candidates = [
        _make_evidence_item(
            evidence_id="E_DEP_01",
            text="AuthService v2.4.0 rollout completed successfully at 14:00. All checks passed.",
            author="Alice",
            permalink="https://slack.com/archives/C_PROD/p1400",
        )
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_DEP_01",
                    statement="AuthService v2.4.0 rollout completed successfully",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                )
            ],
            answerability=AssessmentAnswerability.ANSWERABLE,
        )
    )

    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_DEP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            )
        ]
    )

    m_prop = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="AuthService v2.4.0 Rollout",
                kind=WorkItemKind.TASK,
                member_claim_ids=["C1"],
                context_claim_ids=[],
            )
        ]
    )
    r_prop = WorkItemRelationProposal(
        work_item_id="WI-1",
        relations=[ProposedClaimRelation(claim_id="C1", relation=WorkItemClaimRelation.PRIMARY)],
    )
    l_prop = WorkItemLifecycleVerificationProposal(
        work_item_id="WI-1",
        claim_signals=[
            ProposedClaimLifecycleSignal(claim_id="C1", lifecycle_signal=WorkItemLifecycleSignal.COMPLETION)
        ],
    )
    a_prop = DecisiveLifecycleAuthorityProposal(
        work_item_id="WI-1",
        verifications=[
            DecisiveClaimVerificationResult(
                claim_id="C1",
                proposed_signal=WorkItemLifecycleSignal.COMPLETION,
                decision=DecisiveAuthorityDecision.CONFIRMED,
            )
        ],
    )

    synthesis_coord = DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=StubEpisodeMembershipGrouper(m_prop),
        relation_verifier=StubWorkItemRelationVerifier(r_prop),
        lifecycle_verifier=StubWorkItemLifecycleVerifier(l_prop),
        authority_verifier=StubDecisiveLifecycleAuthorityVerifier(a_prop),
    )

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    res = await coordinator.trace_and_synthesize_async(
        question="AuthService v2.4.0 배포 상태는?",
        candidates=candidates,
    )

    assert res.success is True
    assert res.stop_reason == "COMPLETED"
    assert len(res.work_items) == 1
    wi = res.work_items["WI-1"]
    assert wi.status == WorkItemStatus.COMPLETED

    # Complete Traceability Verification
    chain = res.traceability_chains["WI-1"]
    assert chain.is_traceable is True
    assert chain.final_status == WorkItemStatus.COMPLETED
    assert chain.governing_claim_ids == ["C1"]
    assert len(chain.claim_traces) == 1
    assert chain.claim_traces[0].evidence_ids == ["E1"]
    assert chain.claim_traces[0].citations[0]["permalink"] == "https://slack.com/archives/C_PROD/p1400"


# ---------------------------------------------------------------------------
# Scenario B: Completion + Blocker Coexistence
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_b_completion_and_blocker_coexistence():
    """Scenario B: Completion and Blocker coexist; frozen resolver establishes BLOCKER > COMPLETION."""
    candidates = [
        _make_evidence_item(
            evidence_id="E_COMP_01",
            text="Service rollout finished.",
            author="Dev",
            permalink="https://slack.com/archives/C_PROD/p101",
        ),
        _make_evidence_item(
            evidence_id="E_BLOCK_02",
            text="Critical DB outage is blocking user login traffic.",
            author="SRE",
            permalink="https://slack.com/archives/C_PROD/p102",
        ),
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_COMP",
                    statement="Service rollout finished",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                ),
                ProposedClaim(
                    proposal_id="PROP_BLOCK",
                    statement="Critical DB outage blocks traffic",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.STATE,
                    supporting_evidence_ids=["E2"],
                ),
            ]
        )
    )

    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_COMP",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_BLOCK",
                evidence_id="E2",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
        ]
    )

    m_prop = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="Service Release",
                kind=WorkItemKind.TASK,
                member_claim_ids=["C1", "C2"],
                context_claim_ids=[],
            )
        ]
    )
    r_prop = WorkItemRelationProposal(
        work_item_id="WI-1",
        relations=[
            ProposedClaimRelation(claim_id="C1", relation=WorkItemClaimRelation.PRIMARY),
            ProposedClaimRelation(claim_id="C2", relation=WorkItemClaimRelation.PRIMARY),
        ],
    )
    l_prop = WorkItemLifecycleVerificationProposal(
        work_item_id="WI-1",
        claim_signals=[
            ProposedClaimLifecycleSignal(claim_id="C1", lifecycle_signal=WorkItemLifecycleSignal.COMPLETION),
            ProposedClaimLifecycleSignal(claim_id="C2", lifecycle_signal=WorkItemLifecycleSignal.BLOCKER),
        ],
    )
    a_prop = DecisiveLifecycleAuthorityProposal(
        work_item_id="WI-1",
        verifications=[
            DecisiveClaimVerificationResult(
                claim_id="C1",
                proposed_signal=WorkItemLifecycleSignal.COMPLETION,
                decision=DecisiveAuthorityDecision.CONFIRMED,
            ),
            DecisiveClaimVerificationResult(
                claim_id="C2",
                proposed_signal=WorkItemLifecycleSignal.BLOCKER,
                decision=DecisiveAuthorityDecision.CONFIRMED,
            ),
        ],
    )

    synthesis_coord = DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=StubEpisodeMembershipGrouper(m_prop),
        relation_verifier=StubWorkItemRelationVerifier(r_prop),
        lifecycle_verifier=StubWorkItemLifecycleVerifier(l_prop),
        authority_verifier=StubDecisiveLifecycleAuthorityVerifier(a_prop),
    )

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    res = await coordinator.trace_and_synthesize_async(
        question="서비스 릴리즈 최종 상태는?",
        candidates=candidates,
    )

    assert res.success is True
    assert res.stop_reason == "COMPLETED"
    assert len(res.work_items) == 1
    wi = res.work_items["WI-1"]
    # Invariant: BLOCKER takes precedence over COMPLETION in frozen resolver!
    assert wi.status == WorkItemStatus.BLOCKED

    # Traceability confirms C2 (the BLOCKER) is governing
    chain = res.traceability_chains["WI-1"]
    assert chain.is_traceable is True
    assert chain.final_status == WorkItemStatus.BLOCKED
    assert chain.governing_claim_ids == ["C2"]


# ---------------------------------------------------------------------------
# Scenario C: Insufficient Evidence (Zero Supported Claims)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_c_insufficient_evidence_fail_closed():
    """Scenario C: Non-factual casual chatter emits 0 claims and fails closed safely."""
    candidates = [
        _make_evidence_item(evidence_id="E_CHAT_01", text="오늘 점심 피자 드실 분?"),
        _make_evidence_item(evidence_id="E_CHAT_02", text="저요! 로비에서 봐요."),
    ]

    # Assessor extracts 0 claims from non-factual chatter
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[],
            answerability=AssessmentAnswerability.NEEDS_MORE_EVIDENCE,
        )
    )

    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    res = await coordinator.trace_and_synthesize_async(
        question="결제 모듈 릴리즈 상태는?",
        candidates=candidates,
    )

    assert res.success is True
    assert res.stop_reason == "NO_SUPPORTED_CLAIMS"
    assert len(res.work_items) == 0
    assert len(res.traceability_chains) == 0
    # Zero unsupported factual claims added to graph
    assert len(res.graph.claim_nodes) == 0


# ---------------------------------------------------------------------------
# Scenario D: Contradictory Evidence
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_d_contradictory_evidence_safe_unknown():
    """Scenario D: Material unresolved contradiction forces final WorkItem to SAFE_UNKNOWN."""
    candidates = [
        _make_evidence_item(evidence_id="E_DISP_01", text="릴리즈가 완전히 완료되었습니다."),
        _make_evidence_item(evidence_id="E_DISP_02", text="아닙니다. 릴리즈 실패했고 취소되었습니다."),
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_C1",
                    statement="릴리즈 완료",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                ),
                ProposedClaim(
                    proposal_id="PROP_C2",
                    statement="릴리즈 취소",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E2"],
                ),
            ]
        )
    )

    verifier = StubClaimVerifier(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_C1",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_C2",
                evidence_id="E2",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
        ]
    )

    # Stage 1A groups them together; Stage 1B identifies CONTRADICTION
    m_prop = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="Release Outcome",
                kind=WorkItemKind.TASK,
                member_claim_ids=["C1"],
                context_claim_ids=["C2"],
            )
        ],
        unassigned_claim_ids=[],
    )
    r_prop = WorkItemRelationProposal(
        work_item_id="WI-1",
        relations=[
            ProposedClaimRelation(claim_id="C1", relation=WorkItemClaimRelation.PRIMARY),
            ProposedClaimRelation(claim_id="C2", relation=WorkItemClaimRelation.CONTRADICTION),
        ],
    )
    l_prop = WorkItemLifecycleVerificationProposal(
        work_item_id="WI-1",
        claim_signals=[
            ProposedClaimLifecycleSignal(claim_id="C1", lifecycle_signal=WorkItemLifecycleSignal.COMPLETION),
            ProposedClaimLifecycleSignal(claim_id="C2", lifecycle_signal=WorkItemLifecycleSignal.NONE),
        ],
    )
    # Stage 2.5 only evaluates eligible candidates (PRIMARY C1)
    a_prop = DecisiveLifecycleAuthorityProposal(
        work_item_id="WI-1",
        verifications=[
            DecisiveClaimVerificationResult(
                claim_id="C1",
                proposed_signal=WorkItemLifecycleSignal.COMPLETION,
                decision=DecisiveAuthorityDecision.CONFIRMED,
            ),
        ],
    )

    synthesis_coord = DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=StubEpisodeMembershipGrouper(m_prop),
        relation_verifier=StubWorkItemRelationVerifier(r_prop),
        lifecycle_verifier=StubWorkItemLifecycleVerifier(l_prop),
        authority_verifier=StubDecisiveLifecycleAuthorityVerifier(a_prop),
    )

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=synthesis_coord,
    )

    res = await coordinator.trace_and_synthesize_async(
        question="릴리즈 결과는?",
        candidates=candidates,
    )

    assert res.success is True
    # Invariant: Contradiction forces SAFE_UNKNOWN!
    assert res.stop_reason == "SAFE_UNKNOWN"
    assert len(res.work_items) == 1
    wi = res.work_items["WI-1"]
    assert wi.status == WorkItemStatus.UNKNOWN

    chain = res.traceability_chains["WI-1"]
    assert chain.is_traceable is True
    assert chain.final_status == WorkItemStatus.UNKNOWN
    assert "C2" in chain.governing_claim_ids


# ---------------------------------------------------------------------------
# Scenario E: Invalid EvidenceAssessor Output
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_e_invalid_assessor_output_fail_closed():
    """Scenario E: Malformed / unparseable output from model fails closed with INVALID_PROVIDER_OUTPUT."""
    mock_provider = MagicMock(spec=ModelProvider)
    mock_provider.generate_structured = AsyncMock(
        side_effect=ValueError("Invalid JSON schema from OpenRouter")
    )

    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    candidates = [_make_evidence_item(evidence_id="E_01", text="Sample text")]
    res = await coordinator.trace_and_synthesize_async(
        question="Question",
        candidates=candidates,
    )

    assert res.success is False
    assert res.stop_reason == "INVALID_PROVIDER_OUTPUT"
    assert len(res.work_items) == 0
    assert len(res.graph.claim_nodes) == 0


# ---------------------------------------------------------------------------
# Scenario F: Unknown Evidence Reference
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_f_unknown_evidence_reference_rejected():
    """Scenario F: ProposedClaim referencing non-existent Evidence ID is rejected before graph commit."""
    mock_provider = MagicMock(spec=ModelProvider)
    bad_assessment = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="PROP_HALLUCINATED",
                statement="Factual claim with hallucinated evidence citation",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.ACTION,
                supporting_evidence_ids=["E_NON_EXISTENT_ID"],
            )
        ]
    )
    mock_provider.generate_structured = AsyncMock(return_value=(bad_assessment, None))

    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    candidates = [_make_evidence_item(evidence_id="E_REAL_01", text="Real evidence text")]
    res = await coordinator.trace_and_synthesize_async(
        question="Question",
        candidates=candidates,
    )

    assert res.success is False
    assert res.stop_reason == "INVALID_PROVIDER_OUTPUT"
    # The hallucinated claim was NOT committed to the graph
    assert len(res.graph.claim_nodes) == 0


# ---------------------------------------------------------------------------
# Scenario G: Broken Trace Provenance Gate
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_g_broken_trace_fails_closed():
    """Scenario G: Synthesized WorkItem lacking complete evidence traceability fails TRACEABILITY_FAILURE."""
    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="Some factual claim",
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

    # Mock synthesis that synthesizes an orphan work item without claim links
    mock_synthesis_coord = MagicMock(spec=DecomposedWorkItemSynthesisCoordinator)

    async def mock_synth(graph, **kwargs):
        graph.add_work_item(
            WorkItemNode(
                work_item_id="WI-ORPHAN",
                title="Orphan WorkItem",
                kind=WorkItemKind.TASK,
                status=WorkItemStatus.COMPLETED,
            )
        )
        return WorkItemSynthesisResult(
            success=True,
            work_item_ids=["WI-ORPHAN"],
        )

    mock_synthesis_coord.synthesize_and_verify_async = AsyncMock(side_effect=mock_synth)

    coordinator = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=mock_synthesis_coord,
    )

    candidates = [_make_evidence_item(evidence_id="E1", text="Some evidence")]
    res = await coordinator.trace_and_synthesize_async(
        question="Test",
        candidates=candidates,
    )

    # Invariant: EVERY_SUCCESSFUL_FINAL_STATE_HAS_COMPLETE_TRACE = True
    assert res.success is False
    assert res.stop_reason == "TRACEABILITY_FAILURE"
    assert "zero claim links" in str(res.error_message)


# ---------------------------------------------------------------------------
# Scenario H: Provider Failure vs Legitimate Empty Result Distinction
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_h_distinguishable_provider_failure_and_legitimate_empty():
    """Scenario H: Strict distinction between legitimate zero-claim assessment and provider failure."""
    candidates = [_make_evidence_item(evidence_id="E1", text="Ordinary discussion")]

    # Path 1: Legitimate empty assessment (no factual propositions in evidence)
    assessor_empty = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coord_empty = ProductionRealTraceCoordinator(evidence_assessor=assessor_empty)
    res_empty = await coord_empty.trace_and_synthesize_async(
        question="Status?",
        candidates=candidates,
    )
    assert res_empty.success is True
    assert res_empty.stop_reason == "NO_SUPPORTED_CLAIMS"
    assert res_empty.error_message is None

    # Path 2: Provider execution failure (HTTP 502 / network timeout)
    mock_provider = MagicMock(spec=ModelProvider)
    mock_provider.generate_structured = AsyncMock(
        side_effect=RuntimeError("OpenRouter 502 Bad Gateway")
    )
    assessor_fail = OpenRouterEvidenceAssessor(provider=mock_provider)
    coord_fail = ProductionRealTraceCoordinator(evidence_assessor=assessor_fail)
    res_fail = await coord_fail.trace_and_synthesize_async(
        question="Status?",
        candidates=candidates,
    )
    assert res_fail.success is False
    assert res_fail.stop_reason == "PROVIDER_EXECUTION_FAILURE"
    assert "OpenRouter 502 Bad Gateway" in str(res_fail.error_message)

    # Both paths strictly uphold the safety invariant: zero unsupported claims committed!
    assert len(res_empty.graph.claim_nodes) == 0
    assert len(res_fail.graph.claim_nodes) == 0
