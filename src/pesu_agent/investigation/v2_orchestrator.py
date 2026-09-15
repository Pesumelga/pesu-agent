"""PESU — Phase 13.5A: Dedicated End-to-End Investigation v2 Loop Orchestrator.

Integrates InvestigationStateGraph, InvestigationSeedPoolBuilder,
ThreadContextExpansionCoordinator, GapAssessmentCoordinator,
FollowupRetrievalExecutor, and ClaimVerificationCoordinator into one bounded,
deterministic state machine.

Guarantees:
- LIVE_SLACK_ACTIONS = 0
- OPENROUTER_ACTIONS = 0
- PACKAGING_ACTIONS = 0
- DEFAULT_RUNTIME_INVESTIGATION_V2 = False
- RC4_ORCHESTRATOR_REPLACED = False
- PHASE_13_5A_CHANGES_USER_VISIBLE_ANSWER = False
- INVESTIGATION_STATE_TRANSITIONS_EXPLICIT = True
- AUTHORITATIVE_SCOPE_RECOMPUTED_DURING_LOOP = False
- MAX_INVESTIGATION_HOPS = 2
- UNBOUNDED_INVESTIGATION_LOOP = False
- ALL_V2_SLACK_OPERATIONS_ACCOUNTED_GLOBALLY = True
- MODEL_CALLS_BOUNDED_PER_INVESTIGATION = True
- ZERO_INFORMATION_GAIN_TRIGGERS_REASSESSMENT = False
- NO_INFORMATION_GAIN_STOPS_LOOP = True
- CYCLIC_DUPLICATE_RETRIEVAL_ALLOWED = False
- MODEL_STOP_RECOMMENDATION_IS_AUTHORITATIVE = False
- COMPLETE_IMPLIES_EXHAUSTIVE_RECALL = False
- INTEGRITY_FAILURE_IS_TERMINAL = True
- INVESTIGATION_GRAPH_IS_SHARED_STATE_AUTHORITY = True
- FINAL_SYNTHESIS_IN_PHASE_13_5A = False
- WORK_ITEM_SYNTHESIS_IN_PHASE_13_5A = False
- V2_GRAPH_FLATTENED_INTO_RC4_ANALYZER = False
- V2_GRAPH_PERSISTED = False
- V2_OUTCOME_PERSISTED_TO_HISTORY = False
- OFFLINE_V2_ORCHESTRATION_DETERMINISTIC = True
- UNVERIFIED_PROPOSED_CLAIM_CAN_ENABLE_COMPLETE = False
- FOLLOWUP_NEW_EVIDENCE_REASSESSED_BEFORE_VERIFICATION = True
- RAW_NEW_EVIDENCE_ALONE_COUNTS_AS_CONTINUATION_GAIN = False
- ZERO_SEMANTIC_INFORMATION_GAIN_STOPS_LOOP = True
- PREMATERIALIZED_SEED_OPERATIONS_IMPORTED_INTO_SHARED_BUDGET = True
- INITIAL_EXECUTED_QUERIES_VISIBLE_TO_DUPLICATE_GUARD = True
- REASSESSMENT_DUPLICATES_EXISTING_GAP = False
- MODEL_GAP_IMPORTANCE_CAN_ENABLE_COMPLETE = False
- OPEN_GAP_CAN_EXIST_AT_COMPLETE = False
- ILLEGAL_STATE_TRANSITION_ALLOWED = False
- TERMINAL_STATE_IS_ABSORBING = True
- OFFLINE_TRANSITION_CLOCK_DETERMINISTIC = True
- RAW_EXCEPTION_STORED_IN_V2_OUTCOME = False
- CANONICAL_SLACK_ID_STORED_IN_V2_OUTCOME = False
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import re
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesu_agent.investigation.claim_verification import (
    ClaimVerificationConfig,
    ClaimVerificationCoordinator,
    InvestigationClaimVerifier,
    StubClaimVerifier,
)
from pesu_agent.investigation.context_expansion import (
    InvestigationSeedPoolBuilder,
    ThreadContextExpansionCoordinator,
)
from pesu_agent.investigation.followup_retrieval import (
    FollowupRetrievalConfig,
    FollowupRetrievalExecutor,
    InvestigationIdentityContext,
    RetrievalExecutionStatus,
)
from pesu_agent.investigation.gap_assessment import (
    AssessmentAnswerability,
    AssessmentPayloadBuilder,
    GapAssessmentCoordinator,
    InvestigationAssessment,
    InvestigationEvidenceAssessor,
    ProposedClaim,
    SlackOperationBudget,
    StubEvidenceAssessor,
)
from pesu_agent.investigation.graph_models import (
    ClaimCoverageRequirement,
    ClaimStatus,
    GapStatus,
    InformationGap,
    InvestigationBudgetConfig,
    InvestigationStateGraph,
    RetrievalAction,
    RetrievalType,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    ResolvedActorScope,
    ResolvedTemporalScope,
)
from pesu_agent.search.actor import resolve_actor_scope
from pesu_agent.search.temporal import resolve_temporal_scope

logger = logging.getLogger(__name__)

# Core Architectural Boundaries
MAX_INVESTIGATION_HOPS: int = 2
INITIAL_SEMANTIC_SEARCHES_MAX: int = 3
PLANNED_FOLLOWUP_SEARCHES_MAX: int = 3
MAX_CONTEXT_THREAD_EXPANSIONS: int = 4
CONTEXT_RELOCATION_SEARCH_BUDGET: int = 1
MAX_INVESTIGATION_EVIDENCE: int = 30


class InvestigationStage(str, Enum):
    """Explicit lifecycle stages of Investigation v2 State Machine."""

    INITIALIZE = "INITIALIZE"
    SEED_COLLECTED = "SEED_COLLECTED"
    CONTEXT_EXPANDED = "CONTEXT_EXPANDED"
    ASSESSED = "ASSESSED"
    CLAIMS_VERIFIED = "CLAIMS_VERIFIED"
    FOLLOWUP_PLANNED = "FOLLOWUP_PLANNED"
    FOLLOWUP_RETRIEVED = "FOLLOWUP_RETRIEVED"
    REASSESSED = "REASSESSED"
    COMPLETE = "COMPLETE"
    INCONCLUSIVE = "INCONCLUSIVE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    FAILED = "FAILED"


class InvestigationTerminalStatus(str, Enum):
    """Authoritative terminal classification of Investigation v2."""

    COMPLETE = "COMPLETE"
    INCONCLUSIVE = "INCONCLUSIVE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    FAILED = "FAILED"


class IllegalStateTransitionError(RuntimeError):
    """Raised when an illegal or non-deterministic state transition is attempted."""

    pass


# Allowed State Transitions Table (Correction G)
# Terminal states have an empty set of outgoing transitions (strictly absorbing).
ALLOWED_TRANSITIONS: dict[InvestigationStage, set[InvestigationStage]] = {
    InvestigationStage.INITIALIZE: {
        InvestigationStage.SEED_COLLECTED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.SEED_COLLECTED: {
        InvestigationStage.CONTEXT_EXPANDED,
        InvestigationStage.ASSESSED,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.CONTEXT_EXPANDED: {
        InvestigationStage.ASSESSED,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.ASSESSED: {
        InvestigationStage.CLAIMS_VERIFIED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.CLAIMS_VERIFIED: {
        InvestigationStage.COMPLETE,
        InvestigationStage.FOLLOWUP_PLANNED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.BUDGET_EXHAUSTED,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.FOLLOWUP_PLANNED: {
        InvestigationStage.FOLLOWUP_RETRIEVED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.BUDGET_EXHAUSTED,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.FOLLOWUP_RETRIEVED: {
        InvestigationStage.REASSESSED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    InvestigationStage.REASSESSED: {
        InvestigationStage.CLAIMS_VERIFIED,
        InvestigationStage.INCONCLUSIVE,
        InvestigationStage.INTEGRITY_FAILURE,
        InvestigationStage.FAILED,
    },
    # Absorbing terminal states (no transitions allowed out)
    InvestigationStage.COMPLETE: set(),
    InvestigationStage.INCONCLUSIVE: set(),
    InvestigationStage.BUDGET_EXHAUSTED: set(),
    InvestigationStage.INTEGRITY_FAILURE: set(),
    InvestigationStage.FAILED: set(),
}

TERMINAL_STAGES: frozenset[InvestigationStage] = frozenset({
    InvestigationStage.COMPLETE,
    InvestigationStage.INCONCLUSIVE,
    InvestigationStage.BUDGET_EXHAUSTED,
    InvestigationStage.INTEGRITY_FAILURE,
    InvestigationStage.FAILED,
})


class StageTransitionRecord(BaseModel):
    """Audit record capturing an explicit state transition."""

    model_config = ConfigDict(frozen=True)

    from_stage: InvestigationStage
    to_stage: InvestigationStage
    timestamp: datetime
    reason: Optional[str] = None


class RetrievalNovelty(BaseModel):
    """Tracks physical evidence addition in a follow-up hop (Correction C)."""

    new_evidence_count: int = 0


class SemanticInformationGain(BaseModel):
    """Tracks genuine semantic gain across claims, links, and gaps (Correction C)."""

    new_evidence_links_count: int = 0
    status_changed_claims_count: int = 0
    status_changed_gaps_count: int = 0
    new_material_contradictions: int = 0
    new_actionable_gaps: int = 0
    total_semantic_gain: int = 0

    def has_gain(self) -> bool:
        return self.total_semantic_gain > 0


class SeedAcquisitionMetadata(BaseModel):
    """Tracks operations and queries consumed in acquiring pre-materialized seeds (Correction D)."""

    initial_planner_calls: int = Field(default=0)
    initial_search_calls: int = Field(default=0)
    initial_executed_queries: list[str] = Field(default_factory=list)
    synthetic_zero_cost: bool = Field(default=False)


class InvestigationModelBudget(BaseModel):
    """Investigation-level budget bounding LLM calls across all v2 components."""

    planner_calls: int = Field(default=0)
    gap_assessment_calls: int = Field(default=0)
    claim_verification_calls: int = Field(default=0)

    max_planner_calls: int = Field(default=1)
    max_gap_assessment_calls: int = Field(default=3)
    max_claim_verification_calls: int = Field(default=3)

    def can_call_planner(self) -> bool:
        return self.planner_calls < self.max_planner_calls

    def record_planner_call(self) -> None:
        self.planner_calls += 1

    def can_call_assessor(self) -> bool:
        return self.gap_assessment_calls < self.max_gap_assessment_calls

    def record_assessor_call(self) -> None:
        self.gap_assessment_calls += 1

    def can_call_verifier(self) -> bool:
        return self.claim_verification_calls < self.max_claim_verification_calls

    def record_verifier_call(self) -> None:
        self.claim_verification_calls += 1

    def is_exhausted(self) -> bool:
        return not (
            self.can_call_planner()
            or self.can_call_assessor()
            or self.can_call_verifier()
        )


class DeterministicStepClock:
    """Injectable clock producing deterministic sequential timestamps for offline testing."""

    def __init__(self, base_time: Optional[datetime] = None) -> None:
        self.current_time = base_time or datetime(2026, 9, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.step_count: int = 0

    def __call__(self) -> datetime:
        self.step_count += 1
        return self.current_time + timedelta(seconds=self.step_count)


class GraphStateSnapshot(BaseModel):
    """Snapshot of graph state before a retrieval or verification step."""

    evidence_count: int
    links_count: int
    claim_statuses: dict[str, ClaimStatus]
    gap_statuses: dict[str, GapStatus]
    action_count: int


def _sanitize_output_text(text: str) -> str:
    """Sanitizes text to ensure no raw exceptions, member IDs, or secret tokens leak (Correction H)."""
    if not text:
        return ""

    s = text
    # 1. Remove raw tracebacks
    if "Traceback (most recent call last):" in s:
        s = s.split("Traceback (most recent call last):")[0] + " [TRACEBACK_TRUNCATED]"

    # 2. Mask canonical Slack user IDs (U or W followed by alphanumeric containing at least one digit)
    s = re.sub(r"\b[UW][0-9A-Z]{8,}\b", "[REDACTED_SLACK_ID]", s)

    # 3. Mask Windows local absolute paths
    s = re.sub(r"[a-zA-Z]:\\Users\\[^\s]+", "[REDACTED_PATH]", s)

    # 4. Mask OpenRouter API keys / Bearer tokens
    s = re.sub(r"sk-or-[a-zA-Z0-9_-]{20,}", "[REDACTED_KEY]", s)
    s = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED_TOKEN]", s)

    return s.strip()


class InvestigationOutcome(BaseModel):
    """Authoritative, structured outcome of Investigation v2 (No raw user synthesis, No WorkItems)."""

    model_config = ConfigDict(extra="forbid")

    terminal_status: InvestigationTerminalStatus
    terminal_reason_code: str
    safe_terminal_detail: str
    question: str
    verified_claim_ids: list[str]
    conflicted_claim_ids: list[str]
    unresolved_gap_ids: list[str]
    resolved_gap_ids: list[str]
    hops_used: int
    evidence_count: int
    stages_traversed: list[str]
    operation_budget_summary: dict[str, int]
    model_budget_summary: dict[str, int]
    has_integrity_failure: bool
    integrity_error_code: Optional[str] = None


class InvestigationState:
    """Authoritative runtime state container for a single Investigation v2 session."""

    def __init__(
        self,
        question: str,
        authoritative_actor_scope: Optional[ResolvedActorScope],
        authoritative_temporal_scope: Optional[ResolvedTemporalScope],
        captured_reference_time: datetime,
        identity_context: InvestigationIdentityContext,
        graph: InvestigationStateGraph,
        operation_budget: SlackOperationBudget,
        model_budget: InvestigationModelBudget,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.question = question
        self.authoritative_actor_scope = authoritative_actor_scope
        self.authoritative_temporal_scope = authoritative_temporal_scope
        self.captured_reference_time = captured_reference_time
        self.identity_context = identity_context
        self.graph = graph
        self.operation_budget = operation_budget
        self.model_budget = model_budget
        self.clock = clock or DeterministicStepClock(captured_reference_time)

        self.current_hop: int = 0
        self.stage: InvestigationStage = InvestigationStage.INITIALIZE
        self.stage_history: list[StageTransitionRecord] = []
        self.novelty_history: list[RetrievalNovelty] = []
        self.gain_history: list[SemanticInformationGain] = []

        self.executed_action_ids: list[str] = []
        self.prior_action_intents: set[str] = set()
        self.terminal_status: Optional[InvestigationTerminalStatus] = None
        self.terminal_reason_code: Optional[str] = None
        self.safe_terminal_detail: Optional[str] = None
        self.has_integrity_failure: bool = False
        self.integrity_error_code: Optional[str] = None

        # Record initial stage
        self.stage_history.append(
            StageTransitionRecord(
                from_stage=InvestigationStage.INITIALIZE,
                to_stage=InvestigationStage.INITIALIZE,
                timestamp=self.clock(),
                reason="Investigation initialized with authoritative scopes",
            )
        )

    @property
    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    def transition_to(
        self, target_stage: InvestigationStage, reason: Optional[str] = None
    ) -> None:
        """Transitions state to target_stage using strict allowed transition matrix."""
        # 1. Absorbing terminal stage check
        if self.is_terminal:
            raise IllegalStateTransitionError(
                f"Cannot transition from terminal absorbing stage '{self.stage}' to '{target_stage}'"
            )

        # 2. Allowed transition check
        allowed = ALLOWED_TRANSITIONS.get(self.stage, set())
        if target_stage not in allowed:
            raise IllegalStateTransitionError(
                f"Illegal state transition attempted: '{self.stage}' -> '{target_stage}'. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

        now = self.clock()
        rec = StageTransitionRecord(
            from_stage=self.stage,
            to_stage=target_stage,
            timestamp=now,
            reason=_sanitize_output_text(reason or ""),
        )
        self.stage_history.append(rec)
        self.stage = target_stage

    def capture_snapshot(self) -> GraphStateSnapshot:
        """Captures a snapshot of current graph state for information gain tracking."""
        return GraphStateSnapshot(
            evidence_count=len(self.graph.evidence_nodes),
            links_count=len(self.graph.claim_evidence_links),
            claim_statuses={cid: c.status for cid, c in self.graph.claim_nodes.items()},
            gap_statuses={gid: g.status for gid, g in self.graph.information_gaps.items()},
            action_count=len(self.graph.retrieval_actions),
        )

    def calculate_novelty_and_gain(
        self, prev: GraphStateSnapshot
    ) -> tuple[RetrievalNovelty, SemanticInformationGain]:
        """Calculates retrieval novelty and semantic information gain (Correction C)."""
        # 1. Retrieval novelty: purely new canonical evidence admitted
        novelty = RetrievalNovelty(
            new_evidence_count=max(0, len(self.graph.evidence_nodes) - prev.evidence_count)
        )

        # 2. Semantic information gain: validated links, claim status shifts, gap status shifts
        new_links = max(0, len(self.graph.claim_evidence_links) - prev.links_count)

        changed_claims = 0
        new_conflicts = 0
        for cid, c in self.graph.claim_nodes.items():
            prev_stat = prev.claim_statuses.get(cid)
            if prev_stat != c.status:
                changed_claims += 1
            if c.status == ClaimStatus.CONFLICTED and prev_stat != ClaimStatus.CONFLICTED:
                new_conflicts += 1

        changed_gaps = 0
        for gid, g in self.graph.information_gaps.items():
            prev_stat = prev.gap_statuses.get(gid)
            if prev_stat != g.status:
                changed_gaps += 1

        new_actionable_gaps = max(
            0, len(self.graph.information_gaps) - len(prev.gap_statuses)
        )

        total_gain = (
            new_links
            + changed_claims
            + changed_gaps
            + new_conflicts
            + new_actionable_gaps
        )

        gain = SemanticInformationGain(
            new_evidence_links_count=new_links,
            status_changed_claims_count=changed_claims,
            status_changed_gaps_count=changed_gaps,
            new_material_contradictions=new_conflicts,
            new_actionable_gaps=new_actionable_gaps,
            total_semantic_gain=total_gain,
        )

        self.novelty_history.append(novelty)
        self.gain_history.append(gain)
        return novelty, gain

    def set_terminal(
        self,
        status: InvestigationTerminalStatus,
        reason_code: str,
        safe_detail: str,
        integrity_code: Optional[str] = None,
    ) -> None:
        """Sets the terminal status and transitions to the corresponding terminal stage."""
        self.terminal_status = status
        self.terminal_reason_code = reason_code
        self.safe_terminal_detail = _sanitize_output_text(safe_detail)
        if integrity_code:
            self.has_integrity_failure = True
            self.integrity_error_code = integrity_code

        stage_target = InvestigationStage(status.value)
        if not self.is_terminal:
            self.transition_to(stage_target, reason=f"Terminal: {reason_code} - {safe_detail}")


class InvestigationV2Orchestrator:
    """Dedicated Investigation v2 Loop Orchestrator."""

    def __init__(
        self,
        seed_pool_builder: Optional[InvestigationSeedPoolBuilder] = None,
        context_expansion_coordinator: Optional[ThreadContextExpansionCoordinator] = None,
        gap_assessment_coordinator: Optional[GapAssessmentCoordinator] = None,
        followup_retrieval_executor: Optional[FollowupRetrievalExecutor] = None,
        claim_verification_coordinator: Optional[ClaimVerificationCoordinator] = None,
        budget_config: Optional[InvestigationBudgetConfig] = None,
        max_hops: int = MAX_INVESTIGATION_HOPS,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.budget_config = budget_config or InvestigationBudgetConfig()
        self.max_hops = min(max_hops, MAX_INVESTIGATION_HOPS)
        self.clock = clock

        self.seed_pool_builder = seed_pool_builder or InvestigationSeedPoolBuilder(
            budget_config=self.budget_config
        )
        self.context_expansion_coordinator = (
            context_expansion_coordinator
            or ThreadContextExpansionCoordinator(budget_config=self.budget_config)
        )
        self.gap_assessment_coordinator = (
            gap_assessment_coordinator
            or GapAssessmentCoordinator(max_followup_searches=PLANNED_FOLLOWUP_SEARCHES_MAX)
        )
        self.followup_retrieval_executor = (
            followup_retrieval_executor or FollowupRetrievalExecutor()
        )
        self.claim_verification_coordinator = (
            claim_verification_coordinator or ClaimVerificationCoordinator()
        )

    def _evaluate_completion_status(
        self, state: InvestigationState
    ) -> tuple[bool, Optional[str]]:
        """Evaluates whether state satisfies strict COMPLETE requirements (Correction F)."""
        # 1. Dominating integrity failure
        if state.has_integrity_failure:
            return False, "INTEGRITY_FAILURE"

        # 2. Check for conflicting claims
        conflicted_claims = [
            c for c in state.graph.claim_nodes.values() if c.status == ClaimStatus.CONFLICTED
        ]
        if conflicted_claims:
            return False, f"BLOCKING_CONFLICT_PRESENT: {len(conflicted_claims)} claims conflicted"

        # 3. Check for any OPEN gaps (All open gaps are strictly blocking in 13.5A)
        open_gaps = [
            g for g in state.graph.information_gaps.values() if g.status == GapStatus.OPEN
        ]
        if open_gaps:
            return False, f"OPEN_GAPS_REMAIN: {len(open_gaps)} open gaps remain"

        # 4. Check for >= 1 authoritatively SUPPORTED claim
        supported_claims = [
            c for c in state.graph.claim_nodes.values() if c.status == ClaimStatus.SUPPORTED
        ]
        if not supported_claims:
            return False, "NO_SUPPORTED_CLAIMS: at least 1 authoritatively supported claim required"

        # 5. Coverage check: EXHAUSTIVE_RECALL claim alone cannot produce COMPLETE
        non_exhaustive_supported = [
            c
            for c in supported_claims
            if c.coverage_requirement != ClaimCoverageRequirement.EXHAUSTIVE_RECALL
        ]
        if not non_exhaustive_supported:
            return False, "ONLY_EXHAUSTIVE_RECALL_CLAIMS: exhaustive recall cannot establish complete"

        return True, None

    def _assemble_outcome(self, state: InvestigationState) -> InvestigationOutcome:
        """Assembles the sanitized, structured InvestigationOutcome."""
        verified_ids = sorted([
            cid for cid, c in state.graph.claim_nodes.items() if c.status == ClaimStatus.SUPPORTED
        ])
        conflicted_ids = sorted([
            cid for cid, c in state.graph.claim_nodes.items() if c.status == ClaimStatus.CONFLICTED
        ])
        unresolved_gap_ids = sorted([
            gid for gid, g in state.graph.information_gaps.items() if g.status == GapStatus.OPEN
        ])
        resolved_gap_ids = sorted([
            gid for gid, g in state.graph.information_gaps.items() if g.status == GapStatus.RESOLVED
        ])

        stages = [rec.to_stage.value for rec in state.stage_history]

        op_summary = {
            "planned_semantic_searches": state.operation_budget.planned_semantic_searches,
            "followup_searches": state.operation_budget.followup_searches,
            "relocation_searches": state.operation_budget.relocation_searches,
            "thread_expansions": state.operation_budget.thread_expansions,
            "max_total_searches": state.operation_budget.max_total_searches,
            "max_followup_searches": state.operation_budget.max_followup_searches,
            "max_relocation_searches": state.operation_budget.max_relocation_searches,
            "max_thread_expansions": state.operation_budget.max_thread_expansions,
        }

        model_summary = {
            "planner_calls": state.model_budget.planner_calls,
            "gap_assessment_calls": state.model_budget.gap_assessment_calls,
            "claim_verification_calls": state.model_budget.claim_verification_calls,
            "max_planner_calls": state.model_budget.max_planner_calls,
            "max_gap_assessment_calls": state.model_budget.max_gap_assessment_calls,
            "max_claim_verification_calls": state.model_budget.max_claim_verification_calls,
        }

        status = state.terminal_status or InvestigationTerminalStatus.INCONCLUSIVE
        reason_code = state.terminal_reason_code or "TERMINATED_WITHOUT_EXPLICIT_CODE"
        detail = state.safe_terminal_detail or "Execution completed."

        return InvestigationOutcome(
            terminal_status=status,
            terminal_reason_code=reason_code,
            safe_terminal_detail=detail,
            question=state.question,
            verified_claim_ids=verified_ids,
            conflicted_claim_ids=conflicted_ids,
            unresolved_gap_ids=unresolved_gap_ids,
            resolved_gap_ids=resolved_gap_ids,
            hops_used=state.current_hop,
            evidence_count=len(state.graph.evidence_nodes),
            stages_traversed=stages,
            operation_budget_summary=op_summary,
            model_budget_summary=model_summary,
            has_integrity_failure=state.has_integrity_failure,
            integrity_error_code=state.integrity_error_code,
        )

    def run_investigation(
        self,
        question: str,
        seed_candidates: Optional[list[EvidenceItem]] = None,
        seed_acquisition_metadata: Optional[SeedAcquisitionMetadata] = None,
        search_backend: Optional[Any] = None,
        thread_reader: Optional[Any] = None,
        assessor: Optional[InvestigationEvidenceAssessor] = None,
        verifier: Optional[InvestigationClaimVerifier] = None,
        reference_time: Optional[datetime] = None,
        active_team_id: Optional[str] = None,
        self_user_id: Optional[str] = None,
        actor_scope_override: Optional[ResolvedActorScope] = None,
        temporal_scope_override: Optional[ResolvedTemporalScope] = None,
    ) -> InvestigationOutcome:
        """Synchronous end-to-end Investigation v2 execution."""
        return asyncio.run(
            self.run_investigation_async(
                question=question,
                seed_candidates=seed_candidates,
                seed_acquisition_metadata=seed_acquisition_metadata,
                search_backend=search_backend,
                thread_reader=thread_reader,
                assessor=assessor,
                verifier=verifier,
                reference_time=reference_time,
                active_team_id=active_team_id,
                self_user_id=self_user_id,
                actor_scope_override=actor_scope_override,
                temporal_scope_override=temporal_scope_override,
            )
        )

    async def run_investigation_async(
        self,
        question: str,
        seed_candidates: Optional[list[EvidenceItem]] = None,
        seed_acquisition_metadata: Optional[SeedAcquisitionMetadata] = None,
        search_backend: Optional[Any] = None,
        thread_reader: Optional[Any] = None,
        assessor: Optional[InvestigationEvidenceAssessor] = None,
        verifier: Optional[InvestigationClaimVerifier] = None,
        reference_time: Optional[datetime] = None,
        active_team_id: Optional[str] = None,
        self_user_id: Optional[str] = None,
        actor_scope_override: Optional[ResolvedActorScope] = None,
        temporal_scope_override: Optional[ResolvedTemporalScope] = None,
    ) -> InvestigationOutcome:
        """Asynchronous end-to-end Investigation v2 execution."""
        ref_clock = self.clock or DeterministicStepClock(reference_time)
        captured_time = reference_time or datetime(2026, 9, 8, 9, 0, 0, tzinfo=timezone.utc)

        # 1. Authoritative scope resolution (Resolution occurs exactly once per investigation)
        authoritative_temporal = temporal_scope_override or resolve_temporal_scope(
            text=question,
            reference_time=captured_time,
        )
        authoritative_actor = actor_scope_override or resolve_actor_scope(question)

        # 2. Identity context lock (ephemeral SELF and active team locked)
        resolved_team_id = active_team_id
        if not resolved_team_id and search_backend is not None and hasattr(search_backend, "get_active_team_id"):
            try:
                resolved_team_id = search_backend.get_active_team_id()
            except Exception:
                pass
        if not resolved_team_id and thread_reader is not None and hasattr(thread_reader, "get_active_team_id"):
            try:
                resolved_team_id = thread_reader.get_active_team_id()
            except Exception:
                pass

        identity_context = InvestigationIdentityContext(
            active_team_id=resolved_team_id or "T_DEFAULT",
            investigation_local_self_id=self_user_id,
        )

        # Reset per-run coordinator state
        self.gap_assessment_coordinator.claim_id_map.clear()
        self.gap_assessment_coordinator.gap_id_map.clear()
        self.gap_assessment_coordinator.action_id_map.clear()
        self.claim_verification_coordinator.proposal_to_canonical_claim_id.clear()

        # 3. Investigation State & Graph setup
        graph = InvestigationStateGraph(
            authoritative_actor_scope=authoritative_actor,
            authoritative_temporal_scope=authoritative_temporal,
            budget_config=self.budget_config,
        )
        operation_budget = SlackOperationBudget(
            max_followup_searches=PLANNED_FOLLOWUP_SEARCHES_MAX,
            max_relocation_searches=CONTEXT_RELOCATION_SEARCH_BUDGET,
            max_thread_expansions=MAX_CONTEXT_THREAD_EXPANSIONS,
        )
        model_budget = InvestigationModelBudget()

        # 4. Import seed acquisition metadata into shared budgets (Correction D)
        if seed_acquisition_metadata is None:
            raise ValueError(
                "Seed acquisition metadata is required. For offline synthetic zero-cost tests, "
                "pass explicit SeedAcquisitionMetadata(synthetic_zero_cost=True)."
            )

        if not seed_acquisition_metadata.synthetic_zero_cost:
            for _ in range(seed_acquisition_metadata.initial_planner_calls):
                model_budget.record_planner_call()
            for _ in range(seed_acquisition_metadata.initial_search_calls):
                operation_budget.record_followup_search()
            operation_budget.planned_semantic_searches += (
                seed_acquisition_metadata.initial_search_calls
            )

        state = InvestigationState(
            question=question,
            authoritative_actor_scope=authoritative_actor,
            authoritative_temporal_scope=authoritative_temporal,
            captured_reference_time=captured_time,
            identity_context=identity_context,
            graph=graph,
            operation_budget=operation_budget,
            model_budget=model_budget,
            clock=ref_clock,
        )

        # Register initial executed queries with duplicate guard
        for q in seed_acquisition_metadata.initial_executed_queries:
            state.prior_action_intents.add(self.gap_assessment_coordinator._normalize_query_intent(q))

        # Re-attach shared budgets to coordinators
        self.context_expansion_coordinator.operation_budget = state.operation_budget
        self.gap_assessment_coordinator.budget = state.operation_budget
        self.followup_retrieval_executor.operation_budget = state.operation_budget
        self.followup_retrieval_executor.identity_context = state.identity_context

        # Instantiate default stubs if not provided
        active_assessor = assessor or StubEvidenceAssessor()
        active_verifier = verifier or StubClaimVerifier()

        # =========================================================================
        # HOP 0: INITIALIZE -> SEED_COLLECTED -> CONTEXT_EXPANDED -> ASSESSED -> CLAIMS_VERIFIED
        # =========================================================================
        state.current_hop = 0

        # Step 1: Ingest Seeds
        raw_candidates = seed_candidates or []
        state.transition_to(
            InvestigationStage.SEED_COLLECTED,
            reason=f"Ingesting {len(raw_candidates)} candidate seeds into graph",
        )
        self.seed_pool_builder.populate_seed_graph(
            graph=state.graph,
            candidates=raw_candidates,
            temporal_scope=state.authoritative_temporal_scope,
            actor_scope=state.authoritative_actor_scope,
        )

        # Verify active team stability and SELF constraints
        if not self.followup_retrieval_executor._verify_active_team_stability(
            search_backend, thread_reader
        ):
            state.set_terminal(
                InvestigationTerminalStatus.INTEGRITY_FAILURE,
                reason_code="ACTIVE_TEAM_MISMATCH",
                safe_detail="Active Slack team ID shifted during execution",
                integrity_code="ACTIVE_TEAM_STABILITY_VIOLATED",
            )
            return self._assemble_outcome(state)

        # Step 2: Thread Context Expansion
        if raw_candidates and thread_reader is not None:
            state.transition_to(
                InvestigationStage.CONTEXT_EXPANDED,
                reason="Expanding context on eligible threads",
            )
            self.context_expansion_coordinator.expand_threads_sync(
                graph=state.graph,
                candidates=raw_candidates,
                thread_reader=thread_reader,
                search_backend=search_backend,
                temporal_scope=state.authoritative_temporal_scope,
                actor_scope=state.authoritative_actor_scope,
                operation_budget=state.operation_budget,
            )

        # Step 3: Evidence Assessment (Hop 0)
        if not state.model_budget.can_call_assessor():
            state.set_terminal(
                InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                reason_code="MODEL_BUDGET_EXHAUSTED",
                safe_detail="Assessor model call budget reached limit",
            )
            return self._assemble_outcome(state)

        state.transition_to(
            InvestigationStage.ASSESSED,
            reason="Generating structured assessment of available evidence",
        )
        state.model_budget.record_assessor_call()

        payload_0 = AssessmentPayloadBuilder.build_payload(question=question, graph=state.graph)
        try:
            assessment_0 = await active_assessor.assess(payload_0)
        except Exception:
            state.set_terminal(
                InvestigationTerminalStatus.FAILED,
                reason_code="ASSESSOR_INVOCATION_FAILED",
                safe_detail="Assessor model failed during execution",
            )
            return self._assemble_outcome(state)

        eval_res_0 = self.gap_assessment_coordinator.evaluate_and_apply_assessment(
            graph=state.graph,
            assessment=assessment_0,
            question=question,
            prior_queries=list(state.prior_action_intents),
        )

        # Step 4: Claim Verification (Hop 0 - Correction A: verify before completion decision!)
        if assessment_0.proposed_claims:
            if not state.model_budget.can_call_verifier():
                state.set_terminal(
                    InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                    reason_code="MODEL_BUDGET_EXHAUSTED",
                    safe_detail="Claim verifier model call budget reached limit",
                )
                return self._assemble_outcome(state)

            state.transition_to(
                InvestigationStage.CLAIMS_VERIFIED,
                reason="Authoritatively verifying proposed claims against evidence",
            )
            state.model_budget.record_verifier_call()

            try:
                self.claim_verification_coordinator.proposal_to_canonical_claim_id.update(
                    self.gap_assessment_coordinator.claim_id_map
                )
                _ = await self.claim_verification_coordinator.coordinate_verification_async(
                    graph=state.graph,
                    proposed_claims=assessment_0.proposed_claims,
                    verifier=active_verifier,
                )
            except Exception:
                state.set_terminal(
                    InvestigationTerminalStatus.INTEGRITY_FAILURE,
                    reason_code="VERIFIER_SCHEMA_ERROR",
                    safe_detail="Verifier structured output schema or integrity error",
                    integrity_code="VERIFIER_SCHEMA_FAIL_CLOSED",
                )
                return self._assemble_outcome(state)
        else:
            state.transition_to(
                InvestigationStage.CLAIMS_VERIFIED,
                reason="Zero proposed claims; evaluating state",
            )

        # Step 5: Hop 0 Completion Evaluation
        is_complete, comp_reason = self._evaluate_completion_status(state)
        if is_complete:
            state.set_terminal(
                InvestigationTerminalStatus.COMPLETE,
                reason_code="INVESTIGATION_COMPLETE_HOP_0",
                safe_detail="Sufficient verified evidence exists with zero open gaps",
            )
            return self._assemble_outcome(state)

        open_gaps_0 = [g for g in state.graph.information_gaps.values() if g.status == GapStatus.OPEN]
        planned_actions_0 = [
            a for a in state.graph.retrieval_actions.values() if a.action_id not in state.executed_action_ids
        ]

        if open_gaps_0 and not planned_actions_0:
            if not state.operation_budget.can_plan_followup_search():
                state.set_terminal(
                    InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                    reason_code="FOLLOWUP_SEARCH_BUDGET_EXHAUSTED",
                    safe_detail="Follow-up search budget exhausted during planning",
                )
                return self._assemble_outcome(state)

        if not open_gaps_0 or not planned_actions_0:
            state.set_terminal(
                InvestigationTerminalStatus.INCONCLUSIVE,
                reason_code="NO_RETRIEVAL_ACTIONS_PLANNED",
                safe_detail=f"Inconclusive after Hop 0: {comp_reason}",
            )
            return self._assemble_outcome(state)

        # =========================================================================
        # FOLLOW-UP HOPS (Hops 1 to MAX_INVESTIGATION_HOPS)
        # =========================================================================
        while state.current_hop < self.max_hops:
            state.current_hop += 1

            # Check open gaps and available actions
            open_gaps = [g for g in state.graph.information_gaps.values() if g.status == GapStatus.OPEN]
            if not open_gaps:
                break

            candidate_actions = [
                a
                for a in state.graph.retrieval_actions.values()
                if a.action_id not in state.executed_action_ids
                and a.originating_gap_id in state.graph.information_gaps
                and state.graph.information_gaps[a.originating_gap_id].status == GapStatus.OPEN
            ]

            if not candidate_actions:
                break

            # Filter out duplicate action intents (Correction E)
            actions_to_execute: list[RetrievalAction] = []
            for act in candidate_actions:
                norm_q = self.gap_assessment_coordinator._normalize_query_intent(act.semantic_query)
                if norm_q in state.prior_action_intents:
                    continue
                state.prior_action_intents.add(norm_q)
                actions_to_execute.append(act)

            if not actions_to_execute:
                # All candidate actions were duplicates
                break

            # Check budgets before execution
            if not state.operation_budget.can_plan_followup_search():
                state.set_terminal(
                    InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                    reason_code="FOLLOWUP_SEARCH_BUDGET_EXHAUSTED",
                    safe_detail="Follow-up search budget reached limit (max 3)",
                )
                return self._assemble_outcome(state)

            # Stage: FOLLOWUP_PLANNED
            state.transition_to(
                InvestigationStage.FOLLOWUP_PLANNED,
                reason=f"Hop {state.current_hop}: Planned {len(actions_to_execute)} retrieval actions",
            )

            # Stage: FOLLOWUP_RETRIEVED
            state.transition_to(
                InvestigationStage.FOLLOWUP_RETRIEVED,
                reason=f"Hop {state.current_hop}: Executing follow-up retrieval actions",
            )

            snapshot_before = state.capture_snapshot()

            for act in actions_to_execute:
                exec_res = self.followup_retrieval_executor.execute_action(
                    graph=state.graph,
                    action=act,
                    search_backend=search_backend,
                    thread_reader=thread_reader,
                )
                state.executed_action_ids.append(act.action_id)

                if exec_res.status == RetrievalExecutionStatus.INTEGRITY_FAILURE:
                    state.set_terminal(
                        InvestigationTerminalStatus.INTEGRITY_FAILURE,
                        reason_code="RETRIEVAL_INTEGRITY_FAILURE",
                        safe_detail=exec_res.safe_detail,
                        integrity_code=exec_res.error_code,
                    )
                    return self._assemble_outcome(state)

            # Stage: REASSESSED (Correction B: Follow-up evidence reassessed BEFORE verification)
            if not state.model_budget.can_call_assessor():
                state.set_terminal(
                    InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                    reason_code="MODEL_BUDGET_EXHAUSTED",
                    safe_detail="Assessor model call budget reached limit for reassessment",
                )
                return self._assemble_outcome(state)

            state.transition_to(
                InvestigationStage.REASSESSED,
                reason=f"Hop {state.current_hop}: Reassessing graph with newly retrieved evidence",
            )
            state.model_budget.record_assessor_call()

            payload_re = AssessmentPayloadBuilder.build_payload(question=question, graph=state.graph)
            try:
                assessment_re = await active_assessor.assess(payload_re)
            except Exception:
                state.set_terminal(
                    InvestigationTerminalStatus.FAILED,
                    reason_code="ASSESSOR_INVOCATION_FAILED",
                    safe_detail="Assessor model failed during reassessment",
                )
                return self._assemble_outcome(state)

            _ = self.gap_assessment_coordinator.evaluate_and_apply_assessment(
                graph=state.graph,
                assessment=assessment_re,
                question=question,
                prior_queries=list(state.prior_action_intents),
            )

            # Stage: CLAIMS_VERIFIED
            claims_to_verify: list[ProposedClaim] = []
            if assessment_re.proposed_claims:
                claims_to_verify.extend(assessment_re.proposed_claims)

            if claims_to_verify:
                if not state.model_budget.can_call_verifier():
                    state.set_terminal(
                        InvestigationTerminalStatus.BUDGET_EXHAUSTED,
                        reason_code="MODEL_BUDGET_EXHAUSTED",
                        safe_detail="Verifier model call budget reached limit",
                    )
                    return self._assemble_outcome(state)

                state.transition_to(
                    InvestigationStage.CLAIMS_VERIFIED,
                    reason=f"Hop {state.current_hop}: Verifying claims with newly updated evidence",
                )
                state.model_budget.record_verifier_call()

                try:
                    self.claim_verification_coordinator.proposal_to_canonical_claim_id.update(
                        self.gap_assessment_coordinator.claim_id_map
                    )
                    _ = await self.claim_verification_coordinator.coordinate_verification_async(
                        graph=state.graph,
                        proposed_claims=claims_to_verify,
                        verifier=active_verifier,
                    )
                except Exception:
                    state.set_terminal(
                        InvestigationTerminalStatus.INTEGRITY_FAILURE,
                        reason_code="VERIFIER_SCHEMA_ERROR",
                        safe_detail="Verifier structured output schema or integrity error",
                        integrity_code="VERIFIER_SCHEMA_FAIL_CLOSED",
                    )
                    return self._assemble_outcome(state)
            else:
                state.transition_to(
                    InvestigationStage.CLAIMS_VERIFIED,
                    reason=f"Hop {state.current_hop}: No new proposed claims to verify",
                )

            # Calculate novelty and semantic information gain (Correction C)
            novelty, gain = state.calculate_novelty_and_gain(snapshot_before)

            # Check semantic information gain
            if not gain.has_gain():
                # ZERO_SEMANTIC_INFORMATION_GAIN_STOPS_LOOP = True
                state.set_terminal(
                    InvestigationTerminalStatus.INCONCLUSIVE,
                    reason_code="ZERO_SEMANTIC_INFORMATION_GAIN",
                    safe_detail=f"Hop {state.current_hop} produced zero semantic information gain",
                )
                return self._assemble_outcome(state)

            # Check completion
            is_comp, _ = self._evaluate_completion_status(state)
            if is_comp:
                state.set_terminal(
                    InvestigationTerminalStatus.COMPLETE,
                    reason_code=f"INVESTIGATION_COMPLETE_HOP_{state.current_hop}",
                    safe_detail="Sufficient verified evidence exists with zero open gaps",
                )
                return self._assemble_outcome(state)

        # End of loop: determine final terminal status
        if not state.is_terminal:
            is_comp, comp_detail = self._evaluate_completion_status(state)
            if is_comp:
                state.set_terminal(
                    InvestigationTerminalStatus.COMPLETE,
                    reason_code="INVESTIGATION_COMPLETE",
                    safe_detail="All blocking gaps resolved with supported claims",
                )
            else:
                if state.current_hop >= self.max_hops:
                    state.set_terminal(
                        InvestigationTerminalStatus.INCONCLUSIVE,
                        reason_code="MAX_HOPS_EXHAUSTED",
                        safe_detail=f"Reached maximum investigation hops ({self.max_hops}): {comp_detail}",
                    )
                else:
                    state.set_terminal(
                        InvestigationTerminalStatus.INCONCLUSIVE,
                        reason_code="INCONCLUSIVE_NO_ACTION",
                        safe_detail=f"Investigation concluded without further actions: {comp_detail}",
                    )

        return self._assemble_outcome(state)
