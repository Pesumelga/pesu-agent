"""Offline Full-Chain Diagnostic Harness Contract Test.

Two-layer verification as mandated by Task 11R9:
1. Layer A: Real construction contract test
   - Real OpenRouter Stage classes (Grouper, Verifiers, Assessor)
   - Real LLMConfig instances
   - Zero network calls
   - Strict constructor / runtime config contract verification
   - All 8 preflight checks verified
2. Layer B: Full-chain harness orchestration test
   - Real semantic stage classes with deterministic fake provider injection
   - Full pipeline execution: EvidenceAssessor -> ClaimVerifier -> Stage 1A -> Stage 1B
     -> Stage 2 -> Stage 2.5 -> Reconciliation -> Traceability -> Grounded Answer Builder
     -> Harness Report Extraction
   - Zero field/config AttributeError across entire chain
"""

from typing import Any
import pytest
from pesu_agent.investigation.graph_models import AttributionStrength
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
    create_authoritative_synthesis_coordinator,
)
from pesu_agent.investigation.workitem_synthesis import (
    OpenRouterEpisodeMembershipGrouper,
    OpenRouterWorkItemRelationVerifier,
    OpenRouterWorkItemLifecycleVerifier,
    OpenRouterDecisiveLifecycleAuthorityVerifier,
    DecomposedWorkItemSynthesisCoordinator,
    WorkItemSynthesisConfig,
    EpisodeMembershipProposal,
    ProposedEpisodeMembershipWorkItem,
    WorkItemRelationProposal,
    ProposedClaimRelation,
    WorkItemClaimRelation,
    WorkItemLifecycleVerificationProposal,
    ProposedClaimLifecycleSignal,
    WorkItemLifecycleSignal,
    DecisiveLifecycleAuthorityProposal,
    DecisiveClaimVerificationResult,
    DecisiveAuthorityDecision,
)
from pesu_agent.investigation.gap_assessment import (
    InvestigationEvidenceAssessor,
    OpenRouterEvidenceAssessor,
    InvestigationAssessment,
    ProposedClaim,
    AssessmentAnswerability,
)
from pesu_agent.investigation.claim_verification import (
    InvestigationClaimVerifier,
    OpenRouterClaimVerifier,
    ClaimVerificationAssessment,
    EvidenceClaimVerdict,
    VerificationSemanticRelation,
)
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    ClaimKind,
    ClaimSemanticType,
    WorkItemKind,
    WorkItemStatus,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    ResolvedActorScope,
    ResolvedTemporalScope,
)
from pesu_agent.search.actor import ActorScopeKind, ActorResolutionStatus
from pesu_agent.search.temporal import TemporalStrictness, TemporalResolutionStatus
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
)
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import LLMMessage, LLMResponse, LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from scripts.diagnose_task11r7_full_production_pipeline import (
    build_diagnostic_semantic_components,
    extract_grounded_answer_report,
    run_harness_contract_preflight,
)


# ============================================================================
# Layer A: Real Construction Contract Test (Zero Mocking, Zero Network Calls)
# ============================================================================

def test_layer_a_real_construction_contract():
    """Validates that real OpenRouter stage classes are wired with genuine LLMConfig."""
    s_cfg = WorkItemSynthesisConfig()
    llm_cfg = LLMConfig()

    s_coord = build_diagnostic_semantic_components(
        synthesis_config=s_cfg,
        llm_config=llm_cfg,
    )
    trace_coord = ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)

    # Preflight Check Verification
    preflight = run_harness_contract_preflight(trace_coord)
    assert all(preflight.values()), f"Preflight checks failed: {preflight}"

    # Stage 1A Real Class & Contract
    g1a = s_coord.stage1a_coordinator.grouper
    assert isinstance(g1a, OpenRouterEpisodeMembershipGrouper)
    assert isinstance(g1a.config, LLMConfig)
    assert callable(getattr(g1a.config, "get_api_key_value", None))
    assert g1a.model == "openai/gpt-5.6-luna"
    assert g1a.reasoning_effort == "medium"
    assert g1a.temperature == 0.0

    # Stage 1B Real Class & Contract
    v1b = s_coord.stage1b_coordinator.verifier
    assert isinstance(v1b, OpenRouterWorkItemRelationVerifier)
    assert isinstance(v1b.config, LLMConfig)
    assert callable(getattr(v1b.config, "get_api_key_value", None))
    assert v1b.model == "openai/gpt-5.6-luna"
    assert v1b.reasoning_effort == "medium"
    assert v1b.temperature == 0.0

    # Stage 2 Real Class & Contract
    v2 = s_coord.stage2_coordinator.verifier
    assert isinstance(v2, OpenRouterWorkItemLifecycleVerifier)
    assert isinstance(v2.config, LLMConfig)
    assert callable(getattr(v2.config, "get_api_key_value", None))
    assert v2.model == "openai/gpt-5.6-luna"
    assert v2.reasoning_effort == "medium"
    assert v2.temperature == 0.0

    # Stage 2.5 Real Class & Contract
    assert s_coord.stage25_coordinator is not None
    v25 = s_coord.stage25_coordinator.verifier
    assert isinstance(v25, OpenRouterDecisiveLifecycleAuthorityVerifier)
    assert isinstance(v25.config, LLMConfig)
    assert callable(getattr(v25.config, "get_api_key_value", None))
    assert v25.model == "openai/gpt-5.6-luna"
    assert v25.reasoning_effort == "high"
    assert v25.temperature == 0.0


# ============================================================================
# Layer B: Full-Chain Harness Orchestration Test (Real Stage Classes + Fake Provider)
# ============================================================================

class DeterministicStructuredModelProvider:
    """Deterministic ModelProvider returning valid schema responses for real Stage classes."""

    provider_name: str = "deterministic_offline_provider"

    def __init__(self, responses_by_schema: dict[type, Any]):
        self.responses_by_schema = responses_by_schema
        self.calls: list[tuple[type, Any]] = []

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(content="{}", raw_payload={})

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        response_schema: type,
        model: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> tuple[Any, LLMUsageStats]:
        self.calls.append((response_schema, messages))
        if response_schema in self.responses_by_schema:
            resp = self.responses_by_schema[response_schema]
            usage = LLMUsageStats(
                provider=self.provider_name,
                model=model or "fake-model",
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                latency_ms=5.0,
                cost_source="free",
            )
            return resp, usage
        raise KeyError(f"No fake response configured for schema {response_schema}")


@pytest.mark.anyio
async def test_layer_b_full_chain_harness_orchestration():
    """Executes full diagnostic pipeline through real stage classes and asserts zero AttributeErrors."""
    # 1. Prepare deterministic canned responses for each pipeline stage
    assessment_resp = InvestigationAssessment(
        answerability=AssessmentAnswerability.ANSWERABLE,
        proposed_claims=[
            ProposedClaim(
                proposal_id="CLM-001",
                statement="선제적으로 정의한 기준 기반 1차 배포하였습니다",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.EVENT,
                supporting_evidence_ids=["E1"],
            )
        ],
    )

    claim_verification_resp = ClaimVerificationAssessment(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="CLM-001",
                evidence_id="E1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
            )
        ]
    )

    stage1a_resp = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="1차 배포",
                kind=WorkItemKind.TASK,
                member_claim_ids=["C1"],
                context_claim_ids=[],
            )
        ],
        unassigned_claim_ids=[],
    )

    stage1b_resp = WorkItemRelationProposal(
        work_item_id="WI-1",
        relations=[
            ProposedClaimRelation(
                claim_id="C1",
                relation=WorkItemClaimRelation.PRIMARY,
            )
        ],
    )

    stage2_resp = WorkItemLifecycleVerificationProposal(
        work_item_id="WI-1",
        claim_signals=[
            ProposedClaimLifecycleSignal(
                claim_id="C1",
                lifecycle_signal=WorkItemLifecycleSignal.COMPLETION,
            )
        ],
    )

    stage25_resp = DecisiveLifecycleAuthorityProposal(
        work_item_id="WI-1",
        verifications=[
            DecisiveClaimVerificationResult(
                claim_id="C1",
                proposed_signal=WorkItemLifecycleSignal.COMPLETION,
                decision=DecisiveAuthorityDecision.CONFIRMED,
            )
        ],
    )

    # 2. Inject DeterministicStructuredModelProvider into REAL Stage Classes
    provider_assessor = DeterministicStructuredModelProvider({InvestigationAssessment: assessment_resp})
    provider_verifier = DeterministicStructuredModelProvider({ClaimVerificationAssessment: claim_verification_resp})
    provider_1a = DeterministicStructuredModelProvider({EpisodeMembershipProposal: stage1a_resp})
    provider_1b = DeterministicStructuredModelProvider({WorkItemRelationProposal: stage1b_resp})
    provider_2 = DeterministicStructuredModelProvider({WorkItemLifecycleVerificationProposal: stage2_resp})
    provider_25 = DeterministicStructuredModelProvider({DecisiveLifecycleAuthorityProposal: stage25_resp})

    assessor = OpenRouterEvidenceAssessor(provider=provider_assessor)
    verifier = OpenRouterClaimVerifier(provider=provider_verifier)

    grouper = OpenRouterEpisodeMembershipGrouper(provider=provider_1a)
    rel_verifier = OpenRouterWorkItemRelationVerifier(provider=provider_1b)
    lc_verifier = OpenRouterWorkItemLifecycleVerifier(provider=provider_2)
    auth_verifier = OpenRouterDecisiveLifecycleAuthorityVerifier(
        provider=provider_25,
        reasoning_effort="high",
    )

    s_coord = DecomposedWorkItemSynthesisCoordinator(
        membership_grouper=grouper,
        relation_verifier=rel_verifier,
        lifecycle_verifier=lc_verifier,
        authority_verifier=auth_verifier,
        config=WorkItemSynthesisConfig(),
    )

    trace_coord = ProductionRealTraceCoordinator(
        evidence_assessor=assessor,
        claim_verifier=verifier,
        synthesis_coordinator=s_coord,
    )

    # 3. Candidate Evidence
    candidate = EvidenceItem(
        evidence_id="EV-001",
        text="선제적으로 정의한 기준 기반 1차 배포하였습니다 (https://slack.com/archives/C123/p1786425172544279)",
        permalink="https://slack.com/archives/C123/p1786425172544279",
        author="U12345",
        channel="C123",
        timestamp="1786425172.544279",
        source="slack",
    )

    actor_scope = ResolvedActorScope(
        kind=ActorScopeKind.UNSPECIFIED,
        status=ActorResolutionStatus.RESOLVED,
        is_implicit=False,
    )
    temporal_scope = ResolvedTemporalScope(
        strictness=TemporalStrictness.STRICT,
        status=TemporalResolutionStatus.RESOLVED,
    )

    # 4. Execute Full Pipeline through Coordinator
    trace_res: ProductionTraceResult = await trace_coord.trace_and_synthesize_async(
        question="고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가?",
        candidates=[candidate],
        actor_scope=actor_scope,
        temporal_scope=temporal_scope,
    )

    assert trace_res.success is True, f"Trace failed: {trace_res.error_message}"
    assert trace_res.stop_reason == "COMPLETED"
    assert len(trace_res.work_items) == 1
    assert len(trace_res.traceability_chains) == 1

    # 5. Grounded Answer Building (Strict contract: work_items is dict)
    answer_builder = GroundedWorkItemAnswerBuilder()
    grounded_answer = answer_builder.build_answer(
        question="고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가?",
        graph=trace_res.graph,
        work_items=trace_res.work_items,
        traceability_chains=trace_res.traceability_chains,
    )

    assert isinstance(grounded_answer, GroundedWorkItemAnswerPayload)
    assert grounded_answer.is_conclusive is True
    assert "[완료]" in grounded_answer.summary_headline

    # 6. Diagnostic Report Serialization
    report = extract_grounded_answer_report(grounded_answer)
    assert isinstance(report, dict)
    assert report["summary_headline"] == grounded_answer.summary_headline
    assert report["formatted_markdown_answer"] == grounded_answer.formatted_markdown_answer
    assert report["is_conclusive"] is True
    assert report["cards_count"] == 1

    # Final Contract Declaration
    OFFLINE_DIAGNOSTIC_FULL_CHAIN_CONTRACT_PASS = True
    assert OFFLINE_DIAGNOSTIC_FULL_CHAIN_CONTRACT_PASS is True
