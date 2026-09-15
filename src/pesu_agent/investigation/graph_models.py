"""Phase 13.1: Investigation State & Bounded Typed Evidence/Claim Graph Models.

Core Architectural Contracts:
1. BOUNDED_TYPED_INVESTIGATION_GRAPH:
   - Not globally a DAG. Domain/semantic relations may form cycles.
   - Only provenance/lineage relations (DISCOVERED_FROM, DERIVED_FROM) are strictly acyclic.
   - GLOBAL_GRAPH_DAG_REQUIRED = False
   - PROVENANCE_EDGE_CYCLES_ALLOWED = False
2. Evidence Representation:
   - EvidenceNode represents source material and retrieval scope, NOT claim-relative truth roles.
   - CLAIM_RELATIVE_EVIDENCE_ROLE_STORED_ON_EVIDENCE_NODE = False
3. Edge-Level Claim-Evidence Relations:
   - Claim-to-evidence links carry relation (SUPPORTS, CORROBORATES, CONTRADICTS, CONTEXT_FOR)
     and categorical attribution_strength (DIRECT, INDIRECT, CONTEXT_ONLY).
   - One evidence node may independently support claim A and contradict claim B.
4. Epistemic Separation:
   - ClaimKind (FACT, DERIVED, INFERENCE) is separated from ClaimStatus (SUPPORTED, PARTIALLY_SUPPORTED, CONFLICTED, UNRESOLVED).
   - No silent type mutation upon contradiction.
5. Qualitative Confidence:
   - FLOAT_CONFIDENCE_REQUIRED = False. Uses categorical ConfidenceLevel (HIGH, MEDIUM, LOW).
6. Deterministic SELF Action Attribution Invariant:
   - SUPPORTED SELF-action claims require qualifying DIRECT support from evidence with actor_relation == SELF
     and temporal eligibility within authoritative temporal scope.
   - OTHER_AUTHOR_CONTEXT_ALONE_CAN_SUPPORT_SELF_ACTION = False
   - UNKNOWN_AUTHOR_CONTEXT_ALONE_CAN_SUPPORT_SELF_ACTION = False
7. Scope Isolation:
   - CONTEXT_SCOPE_MUTATES_AUTHORITATIVE_ACTOR = False
   - CONTEXT_SCOPE_MUTATES_AUTHORITATIVE_TEMPORAL = False
8. Privacy & Persistence:
   - CANONICAL_SLACK_MEMBER_ID_IN_SERIALIZED_GRAPH = False
   - FREEFORM_CHAIN_OF_THOUGHT_IN_GRAPH = False
   - RAW_GRAPH_PERSISTED = False
   - HISTORY_SCHEMA_CHANGED = False
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from enum import Enum
import json
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pesu_agent.investigation.models import (
    ResolvedActorScope,
    ResolvedTemporalScope,
    TemporalRelation,
)

# Architectural Flag Constants
GLOBAL_GRAPH_DAG_REQUIRED: bool = False
PROVENANCE_EDGE_CYCLES_ALLOWED: bool = False
CLAIM_RELATIVE_EVIDENCE_ROLE_STORED_ON_EVIDENCE_NODE: bool = False
FLOAT_CONFIDENCE_REQUIRED: bool = False
OTHER_AUTHOR_CONTEXT_ALONE_CAN_SUPPORT_SELF_ACTION: bool = False
UNKNOWN_AUTHOR_CONTEXT_ALONE_CAN_SUPPORT_SELF_ACTION: bool = False
PRESENTATION_AUTHOR_USED_AS_CANONICAL_IDENTITY: bool = False
CONTEXT_SCOPE_MUTATES_AUTHORITATIVE_ACTOR: bool = False
CONTEXT_SCOPE_MUTATES_AUTHORITATIVE_TEMPORAL: bool = False
FOLLOWUP_QUERY_REQUIRES_INFORMATION_GAP: bool = True
CANONICAL_SLACK_MEMBER_ID_IN_SERIALIZED_GRAPH: bool = False
FREEFORM_CHAIN_OF_THOUGHT_IN_GRAPH: bool = False
RAW_GRAPH_PERSISTED: bool = False
HISTORY_SCHEMA_CHANGED: bool = False
PHASE_13_1_CHANGES_PRODUCTION_SEARCH_BUDGET: bool = False
SELF_ACTION_VALIDATOR_APPLIES_TO_ALL_SELF_SUBJECT_CLAIMS: bool = False

# Phase 13.5B WorkItem Synthesis Architectural Constants
SYNTHESIZER_HAS_FINAL_WORKITEM_STATUS_AUTHORITY: bool = False
SYNTHESIZER_PROPOSES_LIFECYCLE_SEMANTICS: bool = True
RUNTIME_VALIDATES_LIFECYCLE_SIGNAL_INVARIANTS: bool = True
WORKITEM_CLAIM_RELATION_SOURCE_OF_TRUTH: str = "WORKITEM_CLAIM_LINK"
WORKITEM_NODE_DUPLICATES_RELATION_CLAIM_LISTS: bool = False
WORKITEM_EVIDENCE_PROJECTION_IS_DERIVED_VIEW: bool = True
WORKITEM_NODE_STORES_DUPLICATED_EVIDENCE_PROJECTION: bool = False
WORKITEM_REQUIRES_SUPPORTED_PRIMARY_CLAIM: bool = True
CONFLICTED_CLAIM_CAN_BE_PRIMARY_WORKITEM_FACT: bool = False
UNRESOLVED_CLAIM_CAN_ENTER_AUTHORITATIVE_WORKITEM: bool = False
INFERENCE_ALONE_CAN_DETERMINE_WORKITEM_LIFECYCLE: bool = False
WORKITEM_IDENTITY_BASED_ON_MODEL_LOCAL_ID: bool = False
WORKITEM_CANONICAL_SIGNATURE_USES_PRIMARY_CLAIMS: bool = True
WORKITEM_WITH_CONTRADICTION_LINK_STATUS: str = "UNKNOWN"
WORKITEM_CONTRADICTION_SILENTLY_RESOLVED: bool = False
WORKITEM_INPUT_TRUNCATION_SILENT: bool = False
WORKITEM_INPUT_SELECTION_DETERMINISTIC: bool = True
OVER_LIMIT_MODEL_OUTPUT_SILENTLY_TRUNCATED: bool = False
GRAPH_MODELS_IMPORT_SYNTHESIS_COORDINATOR_MODULE: bool = False
DEFAULT_RUNTIME_WORKITEM_SYNTHESIS: bool = False
PHASE_13_5B_CHANGES_USER_VISIBLE_ANSWER: bool = False
FINAL_USER_ANSWER_IN_PHASE_13_5B: bool = False
WORKITEM_SYNTHESIS_MUTATES_INVESTIGATION_TRUTH_STATE: bool = False

# Phase 13.5C-AR3-R2 Architectural Constants
UNVERIFIED_RELATION_IS_NOT_SEMANTIC_RELATION: bool = True
UNVERIFIED_RELATION_IS_NOT_BACKGROUND: bool = True
UNVERIFIED_RELATION_CAN_ESTABLISH_LIFECYCLE: bool = False
STAGE2_REQUIRES_ALL_RELATIONS_VERIFIED: bool = True
DECISIVE_AUTHORITY_VERIFICATION_MUTATES_LIFECYCLE_SIGNAL: bool = False
REJECTED_DECISIVE_SIGNAL_HAS_LIFECYCLE_AUTHORITY: bool = False
DECISIVE_AUTHORITY_MATERIALITY_IS_DETERMINISTIC: bool = True
MODEL_DOES_NOT_DECIDE_MATERIALITY: bool = True
RELATION_VERIFICATION_COMMIT_IS_ATOMIC_PER_WORKITEM: bool = True
DECISIVE_AUTHORITY_CANDIDATE_PREFILTER_IS_DETERMINISTIC: bool = True

# Stage 2.5 Hardening Constants
DECISIVE_AUTHORITY_VERIFICATION_REQUIRES_EXACT_CANDIDATE_COVERAGE: bool = True
DECISIVE_AUTHORITY_COMMIT_IS_ATOMIC_PER_WORKITEM: bool = True
ELIGIBLE_DECISIVE_SIGNAL_WITH_UNVERIFIED_AUTHORITY_CAN_ESTABLISH_DECISIVE_STATUS: bool = False
DECISIVE_AUTHORITY_FILTER_IS_SEPARATE_FROM_BASE_STATUS_RESOLVER: bool = True
BASE_DETERMINISTIC_STATUS_RESOLVER_SEMANTICS_UNCHANGED: bool = True
UNCERTAIN_MATERIALITY_IS_ORDER_INDEPENDENT: bool = True


class EvidenceRetrievalRole(str, Enum):
    """Describes why/how the evidence was retrieved during investigation."""

    PRIMARY_SCOPE = "PRIMARY_SCOPE"
    CONTEXT_SCOPE = "CONTEXT_SCOPE"


class ClaimEvidenceRelation(str, Enum):
    """Edge-level relation from a claim to a piece of evidence."""

    SUPPORTS = "SUPPORTS"
    CORROBORATES = "CORROBORATES"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT_FOR = "CONTEXT_FOR"


class AttributionStrength(str, Enum):
    """Categorical attribution strength for claim-evidence linkage."""

    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class ClaimKind(str, Enum):
    """How the proposition is epistemically formed."""

    FACT = "FACT"
    DERIVED = "DERIVED"
    INFERENCE = "INFERENCE"


class ClaimStatus(str, Enum):
    """How well current evidence supports the claim."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONFLICTED = "CONFLICTED"
    UNRESOLVED = "UNRESOLVED"


class ConfidenceLevel(str, Enum):
    """Categorical confidence level (avoids floating-point pseudo-precision)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ClaimSemanticType(str, Enum):
    """Generic semantic marker for claim propositions."""

    ACTION = "ACTION"
    STATE = "STATE"
    EVENT = "EVENT"
    RELATION = "RELATION"
    CAUSE = "CAUSE"
    OUTCOME = "OUTCOME"
    OTHER = "OTHER"


class GapOriginType(str, Enum):
    """Origin kind for an information gap."""

    QUESTION = "QUESTION"
    CLAIM = "CLAIM"
    EVIDENCE = "EVIDENCE"


class InformationGapType(str, Enum):
    """Generic category of missing information driving follow-up retrieval."""

    MISSING_FACT = "MISSING_FACT"
    MISSING_RELATION = "MISSING_RELATION"
    MISSING_CAUSE = "MISSING_CAUSE"
    MISSING_OUTCOME = "MISSING_OUTCOME"
    MISSING_TIMELINE = "MISSING_TIMELINE"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


class GapImportance(str, Enum):
    """Priority level for resolving an information gap."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class GapStatus(str, Enum):
    """Lifecycle status of an information gap."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    UNRESOLVABLE = "UNRESOLVABLE"


class RetrievalType(str, Enum):
    """Type of follow-up retrieval action."""

    SEARCH = "SEARCH"
    THREAD_EXPANSION = "THREAD_EXPANSION"


class ActorRetrievalPolicy(str, Enum):
    """Actor constraint policy for follow-up retrieval."""

    PRESERVE_AUTHORITATIVE_ACTOR = "PRESERVE_AUTHORITATIVE_ACTOR"
    CONTEXTUAL_EXPANSION = "CONTEXTUAL_EXPANSION"


class TemporalRetrievalPolicy(str, Enum):
    """Temporal constraint policy for follow-up retrieval."""

    STRICT_WITHIN_SCOPE = "STRICT_WITHIN_SCOPE"
    BOUNDED_HISTORICAL_CONTEXT = "BOUNDED_HISTORICAL_CONTEXT"


class WorkItemCategory(str, Enum):
    """Generic semantic categorization of synthesized work items."""

    WORK_ITEM = "WORK_ITEM"
    ISSUE = "ISSUE"
    DECISION = "DECISION"
    PROCESS_CHANGE = "PROCESS_CHANGE"
    FOLLOW_UP = "FOLLOW_UP"


class WorkItemKind(str, Enum):
    """Semantic/epistemic kind for synthesized work items."""

    TASK = "TASK"
    ISSUE = "ISSUE"
    DECISION = "DECISION"
    PROCESS_CHANGE = "PROCESS_CHANGE"
    EVENT = "EVENT"


class WorkItemStatus(str, Enum):
    """Deterministic runtime status for synthesized work items."""

    COMPLETED = "COMPLETED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    PLANNED = "PLANNED"
    UNKNOWN = "UNKNOWN"


class WorkItemClaimRelation(str, Enum):
    """Typed relation from a WorkItem to an associated ClaimNode."""

    PRIMARY = "PRIMARY"
    OUTCOME = "OUTCOME"
    BACKGROUND = "BACKGROUND"
    CONTRADICTION = "CONTRADICTION"
    FOLLOW_UP = "FOLLOW_UP"


class WorkItemEpisodeMembership(str, Enum):
    """Structural episode membership distinguishing core episode members from context."""

    MEMBER = "MEMBER"
    CONTEXT = "CONTEXT"


def get_legacy_episode_membership_for_relation(
    relation: WorkItemClaimRelation,
) -> WorkItemEpisodeMembership:
    """Legacy fallback helper mapping semantic relation to default structural episode membership.

    NOTE: In Phase 13.5C-AR1-R1, episode_membership is an authoritative independent field.
    This helper exists purely for legacy compatibility when episode_membership is not explicitly specified.
    """
    if relation in (
        WorkItemClaimRelation.PRIMARY,
        WorkItemClaimRelation.OUTCOME,
        WorkItemClaimRelation.FOLLOW_UP,
    ):
        return WorkItemEpisodeMembership.MEMBER
    return WorkItemEpisodeMembership.CONTEXT


# Compatibility alias
get_episode_membership_for_relation = get_legacy_episode_membership_for_relation


class WorkItemLifecycleSignal(str, Enum):
    """Claim-relative synthesis signal for work item lifecycle."""

    COMPLETION = "COMPLETION"
    PLAN = "PLAN"
    PROGRESS = "PROGRESS"
    BLOCKER = "BLOCKER"
    NONE = "NONE"


class DecisiveAuthorityDecision(str, Enum):
    """Stage 2.5 decisive lifecycle authority verification verdict."""

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class ProvenanceRelation(str, Enum):
    """Strictly acyclic lineage relations."""

    DISCOVERED_FROM = "DISCOVERED_FROM"
    DERIVED_FROM = "DERIVED_FROM"


class ClaimGapRelation(str, Enum):
    """Relation between a claim and an information gap it addresses."""

    ADDRESSES = "ADDRESSES"


class ClaimTemporalRequirement(str, Enum):
    """Claim temporal requirement distinguishing request period from historical context."""

    AUTHORITATIVE_REQUEST_SCOPE = "AUTHORITATIVE_REQUEST_SCOPE"
    HISTORICAL_CONTEXT = "HISTORICAL_CONTEXT"
    TIME_UNSCOPED = "TIME_UNSCOPED"


class ClaimCoverageRequirement(str, Enum):
    """Claim coverage requirement."""

    LOCAL_EVIDENCE = "LOCAL_EVIDENCE"
    EXHAUSTIVE_RECALL = "EXHAUSTIVE_RECALL"
    COMPLETE_THREAD = "COMPLETE_THREAD"


# Domain Exceptions
class InvestigationGraphError(Exception):
    """Base error for investigation graph operations."""


class DuplicateNodeError(InvestigationGraphError):
    """Raised when attempting to add a node with an existing identifier."""


class DanglingReferenceError(InvestigationGraphError):
    """Raised when an edge or action references a non-existent node."""


class ProvenanceCycleError(InvestigationGraphError):
    """Raised when a provenance edge introduces a cycle in lineage."""


class GraphInvariantViolationError(InvestigationGraphError):
    """Raised when graph invariants (e.g. SELF action attribution) are violated."""


class InvalidActionError(InvestigationGraphError):
    """Raised when a retrieval action violates preconditions."""


# Nodes & Link Models
class EvidenceNode(BaseModel):
    """Represents raw or thread-expanded source material in the investigation pool."""

    evidence_id: str = Field(description="Unique evidence identifier within the investigation pool (e.g. 'E1', 'E1-R2')")
    source_type: str = Field(default="slack", description="Source channel/platform type")
    canonical_permalink: Optional[str] = Field(default=None, description="Validated canonical message permalink")
    presentation_author: Optional[str] = Field(default=None, description="Display/presentation author name")
    actor_relation: str = Field(
        default="UNKNOWN",
        description="Relation to investigated actor: 'SELF', 'OTHER', or 'UNKNOWN' (e.g. unverified thread author)",
    )
    timestamp: Optional[str] = Field(default=None, description="Source authored timestamp string")
    authored_at: Optional[datetime] = Field(default=None, description="Parsed UTC/timezone-aware authored datetime")
    temporal_relation: Optional[str] = Field(default=None, description="Categorical relation to temporal scope")
    text: str = Field(description="Message body snippet or text content")
    thread_root_id: Optional[str] = Field(default=None, description="Parent/root message identifier if thread item")
    retrieval_round: int = Field(default=1, description="Investigation round in which this evidence was discovered")
    source_query: Optional[str] = Field(default=None, description="Executed query that retrieved this evidence")
    retrieval_role: EvidenceRetrievalRole = Field(
        default=EvidenceRetrievalRole.PRIMARY_SCOPE,
        description="Reason why evidence was retrieved (PRIMARY_SCOPE or CONTEXT_SCOPE)",
    )


class ClaimEvidenceLink(BaseModel):
    """Directed edge linking a claim to a piece of evidence with relation and attribution strength."""

    link_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique link identifier")
    claim_id: str = Field(description="Target claim identifier")
    evidence_id: str = Field(description="Target evidence identifier")
    relation: ClaimEvidenceRelation = Field(description="Relation: SUPPORTS, CORROBORATES, CONTRADICTS, or CONTEXT_FOR")
    attribution_strength: AttributionStrength = Field(
        default=AttributionStrength.DIRECT,
        description="Categorical strength: DIRECT, INDIRECT, or CONTEXT_ONLY",
    )


class ClaimGapLink(BaseModel):
    """Directed edge linking a claim to an information gap it addresses."""

    link_id: str = Field(description="Unique link identifier (e.g. 'CGL1')")
    claim_id: str = Field(description="Referenced claim identifier")
    gap_id: str = Field(description="Referenced information gap identifier")
    relation: ClaimGapRelation = Field(default=ClaimGapRelation.ADDRESSES)


class ClaimNode(BaseModel):
    """Represents an atomic proposition extracted or synthesized from evidence."""

    claim_id: str = Field(description="Unique claim identifier (e.g. 'C1', 'C2')")
    statement: str = Field(description="Claim statement proposition")
    kind: ClaimKind = Field(description="Epistemic formation kind: FACT, DERIVED, or INFERENCE")
    status: ClaimStatus = Field(
        default=ClaimStatus.UNRESOLVED,
        description="Evidence support status: SUPPORTED, PARTIALLY_SUPPORTED, CONFLICTED, or UNRESOLVED",
    )
    confidence: ConfidenceLevel = Field(
        default=ConfidenceLevel.MEDIUM,
        description="Categorical confidence: HIGH, MEDIUM, or LOW",
    )
    semantic_type: Optional[ClaimSemanticType] = Field(
        default=None,
        description="Generic semantic marker (ACTION, STATE, EVENT, RELATION, CAUSE, OUTCOME, OTHER)",
    )
    subject_relation: Optional[str] = Field(
        default=None,
        description="Relation to investigated actor: 'SELF', 'OTHER', 'ORGANIZATION', etc.",
    )
    temporal_scope_relation: Optional[str] = Field(
        default=None,
        description="Relation to authoritative temporal scope: 'WITHIN_SCOPE', 'OUT_OF_SCOPE', 'UNKNOWN'",
    )
    temporal_requirement: Optional[ClaimTemporalRequirement] = Field(
        default=ClaimTemporalRequirement.AUTHORITATIVE_REQUEST_SCOPE,
        description="Temporal requirement: AUTHORITATIVE_REQUEST_SCOPE, HISTORICAL_CONTEXT, or TIME_UNSCOPED",
    )
    coverage_requirement: Optional[ClaimCoverageRequirement] = Field(
        default=ClaimCoverageRequirement.LOCAL_EVIDENCE,
        description="Coverage requirement: LOCAL_EVIDENCE, EXHAUSTIVE_RECALL, or COMPLETE_THREAD",
    )
    addresses_gap_ids: list[str] = Field(
        default_factory=list,
        description="IDs of InformationGaps this claim addresses",
    )
    support_link_ids: list[str] = Field(
        default_factory=list,
        description="IDs of ClaimEvidenceLink edges supporting or corroborating this claim",
    )
    contradiction_link_ids: list[str] = Field(
        default_factory=list,
        description="IDs of ClaimEvidenceLink edges contradicting this claim",
    )


class InformationGap(BaseModel):
    """Structured, user-safe representation of missing information driving follow-up retrieval."""

    gap_id: str = Field(description="Unique information gap identifier (e.g. 'GAP-1')")
    description: str = Field(description="Succinct description of the missing fact or unverified relation")
    importance: GapImportance = Field(default=GapImportance.HIGH, description="Priority: HIGH, MEDIUM, or LOW")
    gap_type: InformationGapType = Field(
        default=InformationGapType.MISSING_FACT,
        description="Generic gap classification",
    )
    origin_type: Optional[GapOriginType] = Field(
        default=None,
        description="Origin classification: QUESTION, CLAIM, or EVIDENCE",
    )
    proposed_query_intent: str = Field(description="Semantic intent formulation for resolving this gap")
    originating_claim_ids: list[str] = Field(
        default_factory=list,
        description="IDs of claims whose uncertainty or conflict originated this gap",
    )
    originating_evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs of evidence whose uncertainty or context gap originated this gap",
    )
    status: GapStatus = Field(default=GapStatus.OPEN, description="Lifecycle status: OPEN, RESOLVED, or UNRESOLVABLE")
    target_actor: Optional[str] = Field(default=None, description="Optional target actor hint")
    target_timeframe: Optional[str] = Field(default=None, description="Optional target timeframe hint")


class RetrievalAction(BaseModel):
    """Planned follow-up retrieval operation bound to an open InformationGap."""

    action_id: str = Field(description="Unique retrieval action identifier (e.g. 'ACT-1')")
    originating_gap_id: str = Field(description="Must reference an existing OPEN InformationGap")
    objective: str = Field(description="Clear retrieval objective")
    retrieval_type: RetrievalType = Field(default=RetrievalType.SEARCH, description="SEARCH or THREAD_EXPANSION")
    semantic_query: Optional[str] = Field(default=None, description="Clean candidate query string")
    target_evidence_id: Optional[str] = Field(
        default=None,
        description="Target candidate evidence ID if retrieval_type is THREAD_EXPANSION",
    )
    actor_policy: ActorRetrievalPolicy = Field(
        default=ActorRetrievalPolicy.PRESERVE_AUTHORITATIVE_ACTOR,
        description="Actor policy for the query",
    )
    temporal_policy: TemporalRetrievalPolicy = Field(
        default=TemporalRetrievalPolicy.STRICT_WITHIN_SCOPE,
        description="Temporal policy for the query",
    )
    round: int = Field(default=1, description="Follow-up hop / round number")


class WorkItemClaimLink(BaseModel):
    """Directed edge linking a WorkItem to a ClaimNode with typed relation, independent episode membership, and lifecycle signal."""

    link_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique link identifier")
    work_item_id: str = Field(description="Target work item identifier")
    claim_id: str = Field(description="Target claim identifier")
    episode_membership: WorkItemEpisodeMembership = Field(
        default=WorkItemEpisodeMembership.MEMBER,
        description="Independent structural episode membership: MEMBER or CONTEXT",
    )
    relation: Optional[WorkItemClaimRelation] = Field(
        default=None,
        description="Semantic relation: PRIMARY, OUTCOME, BACKGROUND, CONTRADICTION, FOLLOW_UP (None = UNVERIFIED)",
    )
    lifecycle_signal: Optional[WorkItemLifecycleSignal] = Field(
        default=None,
        description="Claim-relative synthesis signal: COMPLETION, PLAN, PROGRESS, BLOCKER, NONE (None = UNVERIFIED)",
    )
    decisive_authority: Optional[DecisiveAuthorityDecision] = Field(
        default=None,
        description="Stage 2.5 decisive authority verdict: CONFIRMED, REJECTED, UNCERTAIN (None = not decisive candidate / unverified)",
    )

    @model_validator(mode="before")
    @classmethod
    def _resolve_legacy_membership(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "episode_membership" not in data and "relation" in data and data["relation"] is not None:
                rel = data["relation"]
                if isinstance(rel, str):
                    try:
                        rel_enum = WorkItemClaimRelation(rel)
                        data["episode_membership"] = get_legacy_episode_membership_for_relation(rel_enum)
                    except ValueError:
                        pass
                elif isinstance(rel, WorkItemClaimRelation):
                    data["episode_membership"] = get_legacy_episode_membership_for_relation(rel)
        return data


class WorkItemNode(BaseModel):
    """Coarse-grained semantic work cluster grouping related claims and evidence."""

    work_item_id: str = Field(description="Unique work item identifier (e.g. 'WI-1')")
    title: str = Field(description="Semantic topic or work item title")
    description: str = Field(default="", description="Summary description")
    category: WorkItemCategory = Field(default=WorkItemCategory.WORK_ITEM, description="Generic work item category")
    kind: WorkItemKind = Field(default=WorkItemKind.TASK, description="Epistemic work item kind")
    status: Optional[WorkItemStatus] = Field(
        default=WorkItemStatus.UNKNOWN,
        description="Authoritative runtime status: COMPLETED, IN_PROGRESS, BLOCKED, PLANNED, UNKNOWN",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="IDs of claims grouped under this work item (legacy/compat; authoritative links are WorkItemClaimLinks)",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs of evidence grouped under this work item (legacy/compat; derived via WorkItemEvidenceProjection)",
    )


class ProvenanceEdge(BaseModel):
    """Directed provenance/lineage edge (must be strictly acyclic)."""

    source_id: str = Field(description="Child or discovered entity ID")
    target_id: str = Field(description="Parent or originating entity ID")
    relation: ProvenanceRelation = Field(
        default=ProvenanceRelation.DISCOVERED_FROM,
        description="Provenance relation: DISCOVERED_FROM or DERIVED_FROM",
    )


class InvestigationBudgetConfig(BaseModel):
    """Centralized configuration of candidate bounded limits for Investigation Engine v2."""

    max_seed_searches: int = Field(default=3, description="Maximum initial seed searches (Hop 0)")
    max_followup_searches: int = Field(default=3, description="Maximum follow-up searches across hops")
    max_investigation_hops: int = Field(default=2, description="Maximum multi-hop investigation depth")
    max_thread_expansions: int = Field(default=4, description="Maximum thread expansions in investigation pool")
    max_investigation_evidence: int = Field(default=30, description="Maximum in-memory evidence pool capacity")
    max_synthesized_work_items: int = Field(default=10, description="Maximum synthesized work items")
    max_final_citations: int = Field(default=8, description="Maximum presentation citations attached to answer")
    max_external_llm_calls: int = Field(default=4, description="Maximum external model provider calls")
    max_cost_usd: Optional[float] = Field(default=None, description="Optional dollar cost budget limit")


class InvestigationStateGraph(BaseModel):
    """Bounded, typed in-memory investigation state graph.

    Graph semantics:
    - Domain relations may legitimately form cycles (mutual corroboration, bidirectional links).
    - Provenance/lineage edges (DISCOVERED_FROM, DERIVED_FROM) are strictly acyclic.
    - Authoritative request scopes remain immutable; context retrieval cannot mutate them.
    - Zero persistence of raw graph or canonical Slack member IDs.
    """

    evidence_nodes: dict[str, EvidenceNode] = Field(default_factory=dict)
    claim_nodes: dict[str, ClaimNode] = Field(default_factory=dict)
    information_gaps: dict[str, InformationGap] = Field(default_factory=dict)
    work_items: dict[str, WorkItemNode] = Field(default_factory=dict)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    claim_gap_links: list[ClaimGapLink] = Field(default_factory=list)
    work_item_claim_links: list[WorkItemClaimLink] = Field(default_factory=list)
    provenance_edges: list[ProvenanceEdge] = Field(default_factory=list)
    retrieval_actions: dict[str, RetrievalAction] = Field(default_factory=dict)

    authoritative_actor_scope: Optional[ResolvedActorScope] = None
    authoritative_temporal_scope: Optional[ResolvedTemporalScope] = None

    def add_evidence(self, node: EvidenceNode) -> None:
        """Adds an evidence node to the in-memory pool, rejecting duplicates."""
        if node.evidence_id in self.evidence_nodes:
            raise DuplicateNodeError(f"EvidenceNode with id '{node.evidence_id}' already exists in graph.")
        self.evidence_nodes[node.evidence_id] = node

    def add_claim(self, node: ClaimNode) -> None:
        """Adds a claim node, rejecting duplicates."""
        if node.claim_id in self.claim_nodes:
            raise DuplicateNodeError(f"ClaimNode with id '{node.claim_id}' already exists in graph.")
        self.claim_nodes[node.claim_id] = node

    def add_gap(self, gap: InformationGap) -> None:
        """Adds an information gap, rejecting duplicates."""
        if gap.gap_id in self.information_gaps:
            raise DuplicateNodeError(f"InformationGap with id '{gap.gap_id}' already exists in graph.")
        # Validate originating claims exist if provided
        for cid in gap.originating_claim_ids:
            if cid not in self.claim_nodes:
                raise DanglingReferenceError(f"InformationGap '{gap.gap_id}' references non-existent claim '{cid}'.")
        # Validate originating evidence exist if provided
        for eid in gap.originating_evidence_ids:
            if eid not in self.evidence_nodes:
                raise DanglingReferenceError(f"InformationGap '{gap.gap_id}' references non-existent evidence '{eid}'.")
        self.information_gaps[gap.gap_id] = gap

    def add_work_item(self, item: WorkItemNode) -> None:
        """Adds a synthesized work item, rejecting duplicates."""
        if item.work_item_id in self.work_items:
            raise DuplicateNodeError(f"WorkItemNode with id '{item.work_item_id}' already exists in graph.")
        # Validate referenced claims and evidence exist
        for cid in item.claim_ids:
            if cid not in self.claim_nodes:
                raise DanglingReferenceError(f"WorkItemNode '{item.work_item_id}' references non-existent claim '{cid}'.")
        for eid in item.evidence_ids:
            if eid not in self.evidence_nodes:
                raise DanglingReferenceError(f"WorkItemNode '{item.work_item_id}' references non-existent evidence '{eid}'.")
        self.work_items[item.work_item_id] = item

    def add_claim_evidence_link(self, link: ClaimEvidenceLink) -> None:
        """Adds a claim-to-evidence relation edge, verifying endpoints exist."""
        if link.claim_id not in self.claim_nodes:
            raise DanglingReferenceError(f"ClaimEvidenceLink references non-existent claim '{link.claim_id}'.")
        if link.evidence_id not in self.evidence_nodes:
            raise DanglingReferenceError(f"ClaimEvidenceLink references non-existent evidence '{link.evidence_id}'.")

        self.claim_evidence_links.append(link)

        # Update claim's link indexes
        claim = self.claim_nodes[link.claim_id]
        if link.relation in (ClaimEvidenceRelation.SUPPORTS, ClaimEvidenceRelation.CORROBORATES):
            if link.link_id not in claim.support_link_ids:
                claim.support_link_ids.append(link.link_id)
        elif link.relation == ClaimEvidenceRelation.CONTRADICTS:
            if link.link_id not in claim.contradiction_link_ids:
                claim.contradiction_link_ids.append(link.link_id)

    def add_claim_gap_link(self, link: ClaimGapLink) -> None:
        """Adds a claim-to-gap link, verifying endpoints exist."""
        if link.claim_id not in self.claim_nodes:
            raise DanglingReferenceError(f"ClaimGapLink references non-existent claim '{link.claim_id}'.")
        if link.gap_id not in self.information_gaps:
            raise DanglingReferenceError(f"ClaimGapLink references non-existent gap '{link.gap_id}'.")
        self.claim_gap_links.append(link)

    def add_work_item_claim_link(self, link: WorkItemClaimLink) -> None:
        """Adds a work item-to-claim relation edge, verifying endpoints exist."""
        if link.work_item_id not in self.work_items:
            raise DanglingReferenceError(f"WorkItemClaimLink references non-existent work item '{link.work_item_id}'.")
        if link.claim_id not in self.claim_nodes:
            raise DanglingReferenceError(f"WorkItemClaimLink references non-existent claim '{link.claim_id}'.")
        self.work_item_claim_links.append(link)

    def get_work_item_claim_links(self, work_item_id: str) -> list[WorkItemClaimLink]:
        """Returns all WorkItemClaimLinks associated with the given work_item_id."""
        return [link for link in self.work_item_claim_links if link.work_item_id == work_item_id]

    def add_provenance_edge(self, edge: ProvenanceEdge) -> None:
        """Adds a provenance edge, rejecting cycles in the provenance graph."""
        known_ids = (
            set(self.evidence_nodes.keys())
            | set(self.claim_nodes.keys())
            | set(self.information_gaps.keys())
            | set(self.work_items.keys())
            | set(self.retrieval_actions.keys())
        )
        if edge.source_id not in known_ids:
            raise DanglingReferenceError(f"Provenance edge source '{edge.source_id}' does not exist in graph.")
        if edge.target_id not in known_ids:
            raise DanglingReferenceError(f"Provenance edge target '{edge.target_id}' does not exist in graph.")

        # Cycle detection on provenance:
        # Check if a path already exists from target_id to source_id
        # (which means adding source_id -> target_id would form a cycle)
        if self._path_exists_in_provenance(start_id=edge.target_id, destination_id=edge.source_id):
            raise ProvenanceCycleError(
                f"Adding provenance edge from '{edge.source_id}' to '{edge.target_id}' introduces a cycle."
            )

        self.provenance_edges.append(edge)

    def _path_exists_in_provenance(self, start_id: str, destination_id: str) -> bool:
        """Breadth-first search to check if destination_id is reachable from start_id via provenance edges."""
        if start_id == destination_id:
            return True

        adj: dict[str, list[str]] = {}
        for pe in self.provenance_edges:
            adj.setdefault(pe.source_id, []).append(pe.target_id)

        queue = deque([start_id])
        visited = {start_id}

        while queue:
            curr = queue.popleft()
            for neighbor in adj.get(curr, []):
                if neighbor == destination_id:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        return False

    def add_retrieval_action(self, action: RetrievalAction) -> None:
        """Adds a planned follow-up retrieval action, requiring an existing OPEN gap."""
        if action.originating_gap_id not in self.information_gaps:
            raise DanglingReferenceError(f"RetrievalAction references non-existent gap '{action.originating_gap_id}'.")

        gap = self.information_gaps[action.originating_gap_id]
        if gap.status != GapStatus.OPEN:
            raise InvalidActionError(
                f"RetrievalAction '{action.action_id}' requires an OPEN gap, but gap '{action.originating_gap_id}' "
                f"has status '{gap.status}'."
            )

        if action.action_id in self.retrieval_actions:
            raise DuplicateNodeError(f"RetrievalAction with id '{action.action_id}' already exists.")

        self.retrieval_actions[action.action_id] = action

    def get_open_gaps(self) -> list[InformationGap]:
        """Returns all open information gaps."""
        return [g for g in self.information_gaps.values() if g.status == GapStatus.OPEN]

    def validate_self_action_claim(self, claim: ClaimNode) -> tuple[bool, Optional[str]]:
        """Deterministic validator for claims that attribute an action to SELF.

        Rules:
        A SUPPORTED SELF-action claim (subject_relation == 'SELF') must have at least one
        qualifying direct support link to evidence satisfying:
        1. evidence.actor_relation == 'SELF'
        2. evidence is temporally eligible for the authoritative requested activity scope
        3. link.relation == ClaimEvidenceRelation.SUPPORTS
        4. link.attribution_strength == AttributionStrength.DIRECT

        Returns:
            (True, None) if valid or not a SUPPORTED SELF-action claim.
            (False, error_reason) if the invariant is violated.
        """
        if claim.subject_relation != "SELF" or claim.status != ClaimStatus.SUPPORTED:
            return True, None

        # Phase 13.3A Correction B:
        # SELF action validator applies ONLY to explicit ACTION claims.
        # STATE, EVENT, RELATION, CAUSE, OUTCOME, OTHER, and None do not invoke the validator.
        if claim.semantic_type != ClaimSemanticType.ACTION:
            return True, None

        qualifying_direct_links = []
        for link in self.claim_evidence_links:
            if link.claim_id != claim.claim_id:
                continue

            if link.relation != ClaimEvidenceRelation.SUPPORTS:
                continue

            if link.attribution_strength != AttributionStrength.DIRECT:
                continue

            ev = self.evidence_nodes.get(link.evidence_id)
            if not ev:
                continue

            # Must satisfy actor_relation == 'SELF' (not 'OTHER', not 'UNKNOWN')
            if ev.actor_relation != "SELF":
                continue

            # Check temporal eligibility against authoritative scope if resolved
            if self.authoritative_temporal_scope and self.authoritative_temporal_scope.intent_present:
                if self.authoritative_temporal_scope.status.value == "RESOLVED":
                    if ev.authored_at is not None:
                        if not self.authoritative_temporal_scope.is_in_range(ev.authored_at):
                            continue
                    elif ev.temporal_relation not in (
                        TemporalRelation.DIRECT_TEMPORAL_MATCH.value,
                        TemporalRelation.POSSIBLE_TEMPORAL_MATCH.value,
                    ):
                        continue

            qualifying_direct_links.append(link)

        if not qualifying_direct_links:
            return (
                False,
                f"SUPPORTED SELF-action claim '{claim.claim_id}' lacks direct qualifying evidence "
                f"authored by SELF within authoritative temporal scope.",
            )

        return True, None

    def validate_invariants(self) -> None:
        """Validates reference integrity and domain invariants across the graph."""
        # 1. Links reference existing nodes
        for link in self.claim_evidence_links:
            if link.claim_id not in self.claim_nodes:
                raise DanglingReferenceError(f"Link '{link.link_id}' references missing claim '{link.claim_id}'.")
            if link.evidence_id not in self.evidence_nodes:
                raise DanglingReferenceError(f"Link '{link.link_id}' references missing evidence '{link.evidence_id}'.")

        # 2. Retrieval actions reference open gaps
        for act in self.retrieval_actions.values():
            if act.originating_gap_id not in self.information_gaps:
                raise DanglingReferenceError(f"Action '{act.action_id}' references missing gap '{act.originating_gap_id}'.")
            if self.information_gaps[act.originating_gap_id].status != GapStatus.OPEN:
                raise InvalidActionError(f"Action '{act.action_id}' references non-open gap '{act.originating_gap_id}'.")

        # 3. SELF action attribution invariant
        for claim in self.claim_nodes.values():
            valid, reason = self.validate_self_action_claim(claim)
            if not valid:
                raise GraphInvariantViolationError(reason)

        # 4. WorkItem claim links reference existing nodes
        for wlink in self.work_item_claim_links:
            if wlink.work_item_id not in self.work_items:
                raise DanglingReferenceError(
                    f"WorkItemClaimLink '{wlink.link_id}' references missing work item '{wlink.work_item_id}'."
                )
            if wlink.claim_id not in self.claim_nodes:
                raise DanglingReferenceError(
                    f"WorkItemClaimLink '{wlink.link_id}' references missing claim '{wlink.claim_id}'."
                )
