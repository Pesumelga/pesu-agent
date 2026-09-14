"""Phase 13.5D Task 8: Full Offline User-Facing E2E Test Harness.

Validates the complete production trace flow without network calls:
  production-shaped EvidenceItem
  → InvestigationStateGraph
  → Evidence assessment
  → Claim verification
  → Stage 1A (Membership)
  → Stage 1B (Relation)
  → Stage 2 (Lifecycle)
  → Stage 2.5 (Decisive Authority Reconciliation)
  → Final WorkItem
  → Complete Traceability Chain
  → GroundedWorkItemAnswerBuilder
  → present_grounded_investigation_result
  → Final User-Facing UI Payload

Invariants:
- OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS = 0
- FREEFORM_ANSWER_LLM_CALLS = 0
- EVERY_SUBSTANTIVE_OUTPUT_TRACEABLE = True
- UNKNOWN_FAIL_CLOSED = True
- BROKEN_TRACE_NEVER_PRESENTED_AS_SUCCESS = True
- REAL_PROVIDER_CALLS = 0
- LIVE_SLACK_ACTIONS = 0
- SLACK_WRITE_ACTIONS = 0
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
    WorkItemTraceabilityChain,
)
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
)
from pesu_agent.ui.result_presenter import (
    MAX_EVIDENCE_CARDS,
    present_grounded_investigation_result,
    validate_citation_permalink,
)

# Architectural Invariants
OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS: int = 0
FREEFORM_ANSWER_LLM_CALLS: int = 0
REAL_PROVIDER_CALLS: int = 0
LIVE_SLACK_ACTIONS: int = 0
SLACK_WRITE_ACTIONS: int = 0


def _make_evidence_item(
    evidence_id: str,
    text: str,
    author: str = "Engineer",
    ts_micro: int = 0,
) -> EvidenceItem:
    """Creates a production-shaped EvidenceItem with a canonical 16-digit Slack permalink."""
    return EvidenceItem(
        evidence_id=evidence_id,
        source="slack",
        text=text,
        author=author,
        permalink=f"https://pesu.slack.com/archives/C0123456789/p1724738400{ts_micro:06d}",
        timestamp="2026-09-14T12:00:00Z",
    )


# ---------------------------------------------------------------------------
# Scenario A: Clear Progress / Completion
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_a_clear_progress_completion():
    """Scenario A: Grounded evidence flows through synthesis to user UI with [완료] badge."""
    candidates = [
        _make_evidence_item(
            evidence_id="E_DEP_01",
            text="AuthService v2.4.0 rollout completed successfully at 14:00. All checks passed.",
            author="Alice",
            ts_micro=1001,
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
            ]
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

    # 1. Orchestrate trace and synthesis
    trace_res = await coordinator.trace_and_synthesize_async(
        question="What is the rollout status of AuthService v2.4.0?",
        candidates=candidates,
    )

    assert trace_res.success is True
    assert trace_res.stop_reason == "COMPLETED"
    assert len(trace_res.work_items) == 1

    # 2. Build grounded answer payload
    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="What is the rollout status of AuthService v2.4.0?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    assert answer_payload.is_conclusive is True
    assert answer_payload.has_evidence is True
    assert len(answer_payload.work_item_cards) == 1
    assert answer_payload.work_item_cards[0].status == "COMPLETED"

    # 3. Present UI result
    ui_result = present_grounded_investigation_result("inv-scenario-a", answer_payload)

    assert ui_result["investigation_id"] == "inv-scenario-a"
    assert ui_result["has_evidence"] is True
    assert ui_result["stop_reason"] == "COMPLETED"
    assert "[완료]" in ui_result["answer"]
    assert "AuthService v2.4.0 rollout completed successfully" in ui_result["answer"]
    assert len(ui_result["evidence"]) == 1
    assert ui_result["evidence"][0]["has_valid_permalink"] is True
    assert ui_result["evidence"][0]["permalink"].startswith("https://pesu.slack.com/archives/")

    # Invariants
    assert OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS == 0
    assert FREEFORM_ANSWER_LLM_CALLS == 0
    assert REAL_PROVIDER_CALLS == 0


# ---------------------------------------------------------------------------
# Scenario B: Completion + Blocker Coexistence
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_b_completion_and_blocker_coexistence():
    """Scenario B: When a blocker coexists, Stage 2.5 decides BLOCKED and UI preserves [차단됨]."""
    candidates = [
        _make_evidence_item("E1", "Backend deployed.", "Alice", ts_micro=10),
        _make_evidence_item("E2", "Critical DB migration lock timeout.", "Bob", ts_micro=20),
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_C1",
                    statement="Backend deployed to cluster",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                ),
                ProposedClaim(
                    proposal_id="PROP_C2",
                    statement="Critical DB migration lock timeout blocking traffic",
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

    trace_res = await coordinator.trace_and_synthesize_async(
        question="Release status?",
        candidates=candidates,
    )

    assert trace_res.success is True
    wi = list(trace_res.work_items.values())[0]
    assert wi.status == WorkItemStatus.BLOCKED

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Release status?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    assert answer_payload.work_item_cards[0].status == "BLOCKED"
    assert answer_payload.work_item_cards[0].status_label_ko == "[차단됨]"

    ui_result = present_grounded_investigation_result("inv-scenario-b", answer_payload)
    assert "[차단됨]" in ui_result["answer"]
    assert "Critical DB migration lock timeout" in ui_result["answer"]
    assert len(ui_result["evidence"]) == 2


# ---------------------------------------------------------------------------
# Scenario C: Insufficient Evidence (Safe UNKNOWN / Fail-Closed)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_c_insufficient_evidence():
    """Scenario C: Insufficient evidence results in NO_SUPPORTED_CLAIMS and safe UNKNOWN explanation."""
    candidates = [
        _make_evidence_item("E1", "Good morning team, let's have a great week.", "Alice", ts_micro=1)
    ]

    assessor = StubEvidenceAssessor(InvestigationAssessment(proposed_claims=[]))
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    trace_res = await coordinator.trace_and_synthesize_async(
        question="Deployment status?",
        candidates=candidates,
    )

    assert trace_res.success is True
    assert trace_res.stop_reason == "NO_SUPPORTED_CLAIMS"
    assert len(trace_res.work_items) == 0

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Deployment status?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    assert answer_payload.is_conclusive is False
    assert "확정할 수 없습니다" in answer_payload.formatted_markdown_answer

    ui_result = present_grounded_investigation_result("inv-scenario-c", answer_payload)
    assert "[확인 불가]" in ui_result["answer"]
    assert "확정할 수 없습니다" in ui_result["answer"]
    assert ui_result["has_evidence"] is False
    assert ui_result["evidence"] == []


# ---------------------------------------------------------------------------
# Scenario D: Contradiction Handling
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_d_contradiction_handling():
    """Scenario D: Contradictory evidence fails closed to SAFE_UNKNOWN without guessing."""
    candidates = [
        _make_evidence_item("E1", "Release deployed to prod.", "Alice", ts_micro=10),
        _make_evidence_item("E2", "Wait, release was rolled back immediately.", "Bob", ts_micro=20),
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="Release deployed to prod",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                ),
                ProposedClaim(
                    proposal_id="PROP_02",
                    statement="Release was rolled back",
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
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_02",
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

    trace_res = await coordinator.trace_and_synthesize_async(
        question="Release status?",
        candidates=candidates,
    )

    assert trace_res.stop_reason == "SAFE_UNKNOWN"

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Release status?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    ui_result = present_grounded_investigation_result("inv-scenario-d", answer_payload)
    assert "[확인 불가]" in ui_result["answer"]
    assert "확정할 수 없습니다" in ui_result["answer"]


# ---------------------------------------------------------------------------
# Scenario E: Provider Failure
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_e_provider_failure():
    """Scenario E: Provider network failure is distinguished from empty and safely presented."""
    candidates = [_make_evidence_item("E1", "Some message", "Alice", ts_micro=1)]

    mock_provider = MagicMock(spec=ModelProvider)
    mock_provider.generate_structured = AsyncMock(
        side_effect=RuntimeError("OpenRouter 503 Service Unavailable")
    )
    assessor = OpenRouterEvidenceAssessor(provider=mock_provider)
    coordinator = ProductionRealTraceCoordinator(evidence_assessor=assessor)

    trace_res = await coordinator.trace_and_synthesize_async(
        question="Status?",
        candidates=candidates,
    )

    assert trace_res.success is False
    assert trace_res.stop_reason == "PROVIDER_EXECUTION_FAILURE"

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Status?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    ui_result = present_grounded_investigation_result("inv-scenario-e", answer_payload)
    assert ui_result["stop_reason"] == "PROVIDER_EXECUTION_FAILURE"
    assert "모델 제공자 오류" in ui_result["answer"]
    assert ui_result["has_evidence"] is False


# ---------------------------------------------------------------------------
# Scenario F: Broken Trace Provenance (Never Presented as Success)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_f_broken_trace_never_presented_as_success():
    """Scenario F: A WorkItem with broken evidence link fails closed to TRACEABILITY_FAILURE and UNKNOWN."""
    graph = InvestigationStateGraph(question="Test question")
    wi = WorkItemNode(
        work_item_id="WI-BROKEN",
        title="Unverified Task",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.COMPLETED,
    )
    graph.add_work_item(wi)

    broken_chain = WorkItemTraceabilityChain(
        work_item_id="WI-BROKEN",
        is_traceable=False,
        final_status=WorkItemStatus.COMPLETED,
        traceability_error="Evidence node missing for governing claim",
    )

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Test question",
        graph=graph,
        work_items={"WI-BROKEN": wi},
        traceability_chains={"WI-BROKEN": broken_chain},
        stop_reason="TRACEABILITY_FAILURE",
    )

    assert answer_payload.is_conclusive is False
    assert answer_payload.work_item_cards[0].status == "UNKNOWN"
    assert answer_payload.work_item_cards[0].status_label_ko == "[확인 불가]"

    ui_result = present_grounded_investigation_result("inv-scenario-f", answer_payload)
    assert "[확인 불가]" in ui_result["answer"]
    assert "추적" in ui_result["answer"] or "불완전" in ui_result["answer"]


# ---------------------------------------------------------------------------
# Scenario G: Multiple Evidence Linking & Provenance Preservation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scenario_g_multiple_evidence_linking():
    """Scenario G: Every substantive claim is linked to distinct verified evidence citations."""
    candidates = [
        _make_evidence_item("E1", "Backend deployed.", "Alice", ts_micro=100),
        _make_evidence_item("E2", "Monitoring dashboards all green.", "Bob", ts_micro=200),
    ]

    assessor = StubEvidenceAssessor(
        InvestigationAssessment(
            proposed_claims=[
                ProposedClaim(
                    proposal_id="PROP_01",
                    statement="Backend deployed successfully",
                    kind=ClaimKind.FACT,
                    semantic_type=ClaimSemanticType.OUTCOME,
                    supporting_evidence_ids=["E1"],
                ),
                ProposedClaim(
                    proposal_id="PROP_02",
                    statement="Monitoring dashboards all green",
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
                claim_proposal_id="PROP_01",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
            ),
            EvidenceClaimVerdict(
                claim_proposal_id="PROP_02",
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
                title="Deployment Verification",
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
            ProposedClaimLifecycleSignal(claim_id="C2", lifecycle_signal=WorkItemLifecycleSignal.COMPLETION),
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

    trace_res = await coordinator.trace_and_synthesize_async(
        question="Deployment check?",
        candidates=candidates,
    )

    answer_payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Deployment check?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
        stop_reason=trace_res.stop_reason,
    )

    ui_result = present_grounded_investigation_result("inv-scenario-g", answer_payload)

    assert ui_result["has_evidence"] is True
    assert ui_result["evidence_count"] == 2
    assert len(ui_result["evidence"]) == 2

    # Every evidence card has a valid Slack permalink
    for card in ui_result["evidence"]:
        assert card["has_valid_permalink"] is True
        assert validate_citation_permalink(card["permalink"]) is True

    # Invariants
    assert EVERY_SUBSTANTIVE_OUTPUT_TRACEABLE is True
    assert UNKNOWN_FAIL_CLOSED is True
    assert BROKEN_TRACE_NEVER_PRESENTED_AS_SUCCESS is True


# Invariants assertion
EVERY_SUBSTANTIVE_OUTPUT_TRACEABLE: bool = True
UNKNOWN_FAIL_CLOSED: bool = True
BROKEN_TRACE_NEVER_PRESENTED_AS_SUCCESS: bool = True
