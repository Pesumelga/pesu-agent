"""Phase 13.3B: Deterministic Follow-up Retrieval Compilation & Execution.

Transforms validated RetrievalActions into bounded read-only retrieval
and ingests resulting EvidenceNodes into the Investigation v2 graph.

Architectural and Domain Invariants:
- RELOCATION_BUDGET_SOURCE_OF_TRUTH = "SHARED_SLACK_OPERATION_BUDGET"
- CONTEXT_COORDINATOR_HAS_INDEPENDENT_RELOCATION_ALLOWANCE = False
- DUPLICATE_BACKEND_QUERY_EXECUTED = False
- DUPLICATE_QUERY_DIFFERENT_POLICY_CAN_REUSE_RAW_SNAPSHOT = True
- RAW_SEARCH_SNAPSHOT_PERSISTED = False
- FOLLOWUP_CAN_BOOTSTRAP_SELF_ID = False
- CONTEXT_SEARCH_CAN_BOOTSTRAP_SELF_ID = False
- ACTIVE_TEAM_STABILITY_REQUIRED_FOR_ALL_FOLLOWUP_ACTIONS = True
- INTEGRITY_FAILURE_CONTINUATION_ALLOWED = False
- RAW_EXCEPTION_STORED_IN_EXECUTION_RESULT = False
- FOLLOWUP_MAX_RESULTS_PER_SEARCH = 5
- HISTORICAL_CONTEXT_ELIGIBILITY_QUALIFIED = True
- HISTORICAL_CONTEXT_RECALL_QUALIFIED = False
- FOLLOWUP_SEARCH_AUTOMATICALLY_RESOLVES_GAP = False
- FOLLOWUP_CAN_EXCEED_GRAPH_EVIDENCE_BUDGET = False
- DEFAULT_RUNTIME_FOLLOWUP_SEARCHES = 0
- DEFAULT_RUNTIME_EXTRA_THREAD_READS = 0
- PHASE_13_3B_CHANGES_USER_VISIBLE_ANSWER = False
- PHASE_13_3B_FEEDS_CURRENT_ANALYZER = False
- FOLLOWUP_EXECUTOR_LLM_CALLS = 0
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesu_agent.investigation.context_expansion import ThreadContextExpansionCoordinator
from pesu_agent.investigation.gap_assessment import (
    SLACK_OPERATOR_PATTERN,
    SlackOperationBudget,
)
from pesu_agent.investigation.graph_models import (
    ActorRetrievalPolicy,
    EvidenceNode,
    EvidenceRetrievalRole,
    GapStatus,
    InformationGap,
    InvestigationStateGraph,
    ProvenanceEdge,
    ProvenanceRelation,
    RetrievalAction,
    RetrievalType,
    TemporalRetrievalPolicy,
)
from pesu_agent.investigation.models import (
    ActorScopeKind,
    EvidenceItem,
    SearchQuery,
    TemporalResolutionStatus,
    TemporalStrictness,
)
from pesu_agent.investigation.selector import (
    get_canonical_evidence_key,
    normalize_text_for_matching,
)
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler
from pesu_agent.search.temporal import resolve_candidate_authored_datetime

logger = logging.getLogger(__name__)

# Architectural Constants
RELOCATION_BUDGET_SOURCE_OF_TRUTH: str = "SHARED_SLACK_OPERATION_BUDGET"
CONTEXT_COORDINATOR_HAS_INDEPENDENT_RELOCATION_ALLOWANCE: bool = False
DUPLICATE_BACKEND_QUERY_EXECUTED: bool = False
DUPLICATE_QUERY_DIFFERENT_POLICY_CAN_REUSE_RAW_SNAPSHOT: bool = True
RAW_SEARCH_SNAPSHOT_PERSISTED: bool = False
FOLLOWUP_CAN_BOOTSTRAP_SELF_ID: bool = False
CONTEXT_SEARCH_CAN_BOOTSTRAP_SELF_ID: bool = False
ACTIVE_TEAM_STABILITY_REQUIRED_FOR_ALL_FOLLOWUP_ACTIONS: bool = True
INTEGRITY_FAILURE_CONTINUATION_ALLOWED: bool = False
RAW_EXCEPTION_STORED_IN_EXECUTION_RESULT: bool = False
FOLLOWUP_MAX_RESULTS_PER_SEARCH: int = 5
HISTORICAL_CONTEXT_ELIGIBILITY_QUALIFIED: bool = True
HISTORICAL_CONTEXT_RECALL_QUALIFIED: bool = False
FOLLOWUP_SEARCH_AUTOMATICALLY_RESOLVES_GAP: bool = False
FOLLOWUP_CAN_EXCEED_GRAPH_EVIDENCE_BUDGET: bool = False
DEFAULT_RUNTIME_FOLLOWUP_SEARCHES: int = 0
DEFAULT_RUNTIME_EXTRA_THREAD_READS: int = 0
PHASE_13_3B_CHANGES_USER_VISIBLE_ANSWER: bool = False
PHASE_13_3B_FEEDS_CURRENT_ANALYZER: bool = False
FOLLOWUP_EXECUTOR_LLM_CALLS: int = 0


def _run_coroutine_sync(coro: Any) -> Any:
    """Executes a coroutine from synchronous context without event loop deprecation warnings."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


class FollowupRetrievalConfig(BaseModel):
    """Centralized configuration of actual runtime limits for follow-up retrieval."""

    max_followup_searches: int = Field(default=3, description="Maximum follow-up searches allowed")
    max_results_per_search: int = Field(
        default=FOLLOWUP_MAX_RESULTS_PER_SEARCH,
        description="Strict limit of candidates requested per search backend call",
    )
    max_context_lookback_days: int = Field(
        default=365,
        description="Maximum historical eligibility ceiling for bounded historical context",
    )
    max_relocation_searches: int = Field(
        default=1,
        description="Maximum thread context relocation searches allowed",
    )
    max_investigation_evidence: int = Field(
        default=30,
        description="Hard cap on total EvidenceNodes admitted to the investigation graph",
    )


@dataclass(frozen=True)
class InvestigationIdentityContext:
    """Immutable identity context bound to the active investigation session."""

    active_team_id: str
    investigation_local_self_id: Optional[str] = None


class RetrievalExecutionStatus(str, Enum):
    """Execution outcome status distinguishing infrastructure and integrity failures."""

    EXECUTED = "EXECUTED"
    NO_NEW_EVIDENCE = "NO_NEW_EVIDENCE"
    DUPLICATE_QUERY = "DUPLICATE_QUERY"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INVALID_ACTION = "INVALID_ACTION"
    SEARCH_FAILURE = "SEARCH_FAILURE"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    THREAD_EXPANSION_SKIPPED = "THREAD_EXPANSION_SKIPPED"


class RetrievalExecutionResult(BaseModel):
    """Typed result of executing a RetrievalAction against Search or ThreadReader.

    Security Invariant:
    - RAW_EXCEPTION_STORED_IN_EXECUTION_RESULT = False:
      Never stores raw traceback, canonical Slack member ID, token, or unredacted exceptions.
    """

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(description="Action identifier executed")
    status: RetrievalExecutionStatus = Field(description="Outcome status")
    compiled_query: Optional[str] = Field(
        default=None,
        description="Executable query string dispatched to backend (after compilation & from:me injection)",
    )
    backend_calls: int = Field(default=0, description="Number of low-level backend search/read calls made")
    raw_candidate_count: int = Field(default=0, description="Raw items returned before filtering")
    admitted_evidence_count: int = Field(default=0, description="EvidenceNodes newly ingested into graph")
    duplicate_evidence_count: int = Field(default=0, description="Candidates skipped due to existing evidence")
    filtered_temporal_count: int = Field(default=0, description="Candidates filtered out by temporal policy")
    unknown_timestamp_count: int = Field(default=0, description="Candidates excluded due to unknown timestamp")
    budget_remaining: int = Field(default=0, description="Remaining follow-up search budget")
    error_code: Optional[str] = Field(default=None, description="Bounded safe error code")
    safe_detail: Optional[str] = Field(default=None, description="Redacted, safe human-readable detail")
    admitted_evidence_ids: list[str] = Field(default_factory=list, description="IDs of admitted EvidenceNodes")


class RawSearchSnapshot:
    """In-memory snapshot of raw candidates returned for a compiled executable query."""

    def __init__(self, compiled_query: str, raw_candidates: list[EvidenceItem]) -> None:
        self.compiled_query = compiled_query
        self.raw_candidates = list(raw_candidates)
        self.executed_policies: set[tuple[ActorRetrievalPolicy, TemporalRetrievalPolicy]] = set()


class RawSearchSnapshotCache:
    """Investigation-local in-memory cache for raw search results.

    Invariants:
    - Keyed by fully compiled executable query string (after from:me injection).
    - Memory-only, not serialized, not persisted, not sent to Analyzer/UI/History.
    - If the exact same compiled query is requested under a different policy:
      backend call = 0, reuses cached raw snapshot, applies new policy deterministically.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, RawSearchSnapshot] = {}

    def get(self, query_key: str) -> Optional[RawSearchSnapshot]:
        return self._snapshots.get(query_key)

    def put(self, query_key: str, raw_candidates: list[EvidenceItem]) -> RawSearchSnapshot:
        snapshot = RawSearchSnapshot(query_key, raw_candidates)
        self._snapshots[query_key] = snapshot
        return snapshot

    def has_identical_execution(
        self,
        query_key: str,
        actor_policy: ActorRetrievalPolicy,
        temporal_policy: TemporalRetrievalPolicy,
    ) -> bool:
        snapshot = self._snapshots.get(query_key)
        if snapshot is None:
            return False
        return (actor_policy, temporal_policy) in snapshot.executed_policies

    def record_execution(
        self,
        query_key: str,
        actor_policy: ActorRetrievalPolicy,
        temporal_policy: TemporalRetrievalPolicy,
    ) -> None:
        snapshot = self._snapshots.get(query_key)
        if snapshot is not None:
            snapshot.executed_policies.add((actor_policy, temporal_policy))


class FollowupRetrievalExecutor:
    """Deterministic executor for Investigation v2 follow-up retrieval actions."""

    def __init__(
        self,
        config: Optional[FollowupRetrievalConfig] = None,
        identity_context: Optional[InvestigationIdentityContext] = None,
        operation_budget: Optional[SlackOperationBudget] = None,
    ) -> None:
        self.config = config or FollowupRetrievalConfig()
        self.identity_context = identity_context
        self.operation_budget = operation_budget or SlackOperationBudget(
            max_followup_searches=self.config.max_followup_searches,
            max_relocation_searches=self.config.max_relocation_searches,
        )
        self.snapshot_cache = RawSearchSnapshotCache()
        self.has_integrity_failure: bool = False

    def _verify_active_team_stability(
        self, search_backend: Optional[Any], thread_reader: Optional[Any]
    ) -> bool:
        """Verifies active team stability across all follow-up actions."""
        if not self.identity_context or not self.identity_context.active_team_id:
            return True

        expected_team = self.identity_context.active_team_id

        if search_backend is not None and hasattr(search_backend, "get_active_team_id"):
            current_team = search_backend.get_active_team_id()
            if current_team and current_team != expected_team:
                return False

        if thread_reader is not None and hasattr(thread_reader, "get_active_team_id"):
            current_team = thread_reader.get_active_team_id()
            if current_team and current_team != expected_team:
                return False

        return True

    def _is_duplicate_in_graph(
        self, item: EvidenceItem, graph: InvestigationStateGraph
    ) -> bool:
        """Determines whether a candidate item already exists canonically in graph evidence."""
        item_key = get_canonical_evidence_key(item)
        for node in graph.evidence_nodes.values():
            if node.canonical_permalink and item.permalink and node.canonical_permalink == item.permalink:
                return True
            node_key = (
                node.canonical_permalink
                or f"author:{node.presentation_author}|ts:{node.timestamp}|snip:{normalize_text_for_matching(node.text)[:60]}"
            )
            if item_key == node_key:
                return True
        return False

    def execute_action(
        self,
        graph: InvestigationStateGraph,
        action: RetrievalAction,
        search_backend: Optional[Any] = None,
        thread_reader: Optional[Any] = None,
    ) -> RetrievalExecutionResult:
        """Synchronously executes a validated RetrievalAction."""
        # 1. Integrity check: fail-closed if prior integrity failure occurred
        if self.has_integrity_failure:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INTEGRITY_FAILURE,
                error_code="INTEGRITY_FAILURE_PREVIOUSLY_OCCURRED",
                safe_detail="Subsequent retrieval blocked due to previous integrity failure",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        # 2. Precondition: originating gap must exist and be OPEN
        if action.originating_gap_id not in graph.information_gaps:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INVALID_ACTION,
                error_code="GAP_NOT_FOUND",
                safe_detail="Referenced gap not found in graph",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        gap = graph.information_gaps[action.originating_gap_id]
        if gap.status != GapStatus.OPEN:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INVALID_ACTION,
                error_code="GAP_NOT_OPEN",
                safe_detail=f"Referenced gap is not OPEN (status={gap.status.value})",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        # 3. Active team stability verification for ALL follow-up actions
        if not self._verify_active_team_stability(search_backend, thread_reader):
            self.has_integrity_failure = True
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INTEGRITY_FAILURE,
                error_code="ACTIVE_TEAM_MISMATCH",
                safe_detail="Active team changed during investigation",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        # 4. Dispatch by retrieval type
        if action.retrieval_type == RetrievalType.THREAD_EXPANSION:
            return self._execute_thread_expansion(
                graph=graph,
                action=action,
                thread_reader=thread_reader,
                search_backend=search_backend,
            )

        return self._execute_semantic_search(
            graph=graph,
            action=action,
            search_backend=search_backend,
        )

    def _execute_thread_expansion(
        self,
        graph: InvestigationStateGraph,
        action: RetrievalAction,
        thread_reader: Optional[Any],
        search_backend: Optional[Any],
    ) -> RetrievalExecutionResult:
        """Executes thread expansion context retrieval using the shared operation budget."""
        if thread_reader is None:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.THREAD_EXPANSION_SKIPPED,
                error_code="NO_THREAD_READER",
                safe_detail="No thread reader available for thread expansion",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        # Locate target evidence node
        target_node = None
        if action.target_evidence_id and action.target_evidence_id in graph.evidence_nodes:
            target_node = graph.evidence_nodes[action.target_evidence_id]

        if target_node is None:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.THREAD_EXPANSION_SKIPPED,
                error_code="TARGET_EVIDENCE_NOT_FOUND",
                safe_detail="Target evidence node not found in graph",
                backend_calls=0,
                budget_remaining=max(
                    0,
                    self.config.max_followup_searches - self.operation_budget.followup_searches,
                ),
            )

        target_item = EvidenceItem(
            evidence_id=target_node.evidence_id,
            source=target_node.source_type,
            timestamp=target_node.timestamp,
            permalink=target_node.canonical_permalink,
            author=target_node.presentation_author,
            text=target_node.text,
            thread_identifier=target_node.thread_root_id,
        )

        coordinator = ThreadContextExpansionCoordinator(
            operation_budget=self.operation_budget,
            max_context_thread_expansions=self.operation_budget.max_thread_expansions,
            max_context_relocations=self.operation_budget.max_relocation_searches,
        )

        telemetry = coordinator.expand_threads_sync(
            graph=graph,
            candidates=[target_item],
            thread_reader=thread_reader,
            search_backend=search_backend,
            operation_budget=self.operation_budget,
        )

        admitted = telemetry.get("context_replies_admitted", 0)
        relocations = telemetry.get("relocation_searches_performed", 0)
        status = (
            RetrievalExecutionStatus.EXECUTED
            if admitted > 0
            else RetrievalExecutionStatus.NO_NEW_EVIDENCE
        )

        return RetrievalExecutionResult(
            action_id=action.action_id,
            status=status,
            backend_calls=relocations,
            admitted_evidence_count=admitted,
            safe_detail=f"Context thread expansion complete ({admitted} replies admitted)",
            budget_remaining=max(
                0,
                self.config.max_followup_searches - self.operation_budget.followup_searches,
            ),
        )

    def _execute_semantic_search(
        self,
        graph: InvestigationStateGraph,
        action: RetrievalAction,
        search_backend: Optional[Any],
    ) -> RetrievalExecutionResult:
        """Executes semantic follow-up search with policy-aware snapshot reuse."""
        remaining_budget = max(
            0, self.config.max_followup_searches - self.operation_budget.followup_searches
        )

        if search_backend is None:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.SEARCH_FAILURE,
                error_code="NO_SEARCH_BACKEND",
                safe_detail="No search backend available",
                backend_calls=0,
                budget_remaining=remaining_budget,
            )

        # Check for prohibited model-provided Slack operators (fail-closed)
        query_intent = action.semantic_query or ""
        if SLACK_OPERATOR_PATTERN.search(query_intent):
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INVALID_ACTION,
                error_code="PROHIBITED_SLACK_OPERATOR",
                safe_detail="Model query contained prohibited explicit Slack operators",
                backend_calls=0,
                budget_remaining=remaining_budget,
            )

        # Deterministic query compilation
        try:
            compiled = SlackQueryCompiler.compile(query_intent)
            base_compiled = compiled.compiled_query
        except Exception:
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.INVALID_ACTION,
                error_code="QUERY_COMPILATION_ERROR",
                safe_detail="Query compilation failed",
                backend_calls=0,
                budget_remaining=remaining_budget,
            )

        # Actor Policy: check authoritative SELF requirement
        authoritative_actor = graph.authoritative_actor_scope
        is_auth_self = (
            authoritative_actor is not None
            and authoritative_actor.kind == ActorScopeKind.SELF
        )

        if (
            action.actor_policy == ActorRetrievalPolicy.PRESERVE_AUTHORITATIVE_ACTOR
            and is_auth_self
        ):
            if not self.identity_context or not self.identity_context.investigation_local_self_id:
                self.has_integrity_failure = True
                return RetrievalExecutionResult(
                    action_id=action.action_id,
                    status=RetrievalExecutionStatus.INTEGRITY_FAILURE,
                    error_code="MISSING_SELF_IDENTITY",
                    safe_detail="Cannot preserve authoritative SELF actor without established investigation_local_self_id",
                    backend_calls=0,
                    budget_remaining=remaining_budget,
                )

            if not re.search(r"(?:^|\s)from:me(?:\s|$)", base_compiled):
                executable_query = f"from:me {base_compiled}".strip()
            else:
                executable_query = base_compiled.strip()
        else:
            executable_query = base_compiled.strip()

        # Deduplication key is computed AFTER deterministic compilation and from:me injection
        dedupe_key = executable_query

        # Check snapshot cache for identical execution
        if self.snapshot_cache.has_identical_execution(
            dedupe_key, action.actor_policy, action.temporal_policy
        ):
            return RetrievalExecutionResult(
                action_id=action.action_id,
                status=RetrievalExecutionStatus.DUPLICATE_QUERY,
                compiled_query=dedupe_key,
                backend_calls=0,
                safe_detail="Exact duplicate action and identical policy already executed",
                budget_remaining=remaining_budget,
            )

        # Check snapshot cache for raw candidate reuse under a different policy
        snapshot = self.snapshot_cache.get(dedupe_key)
        backend_calls = 0

        if snapshot is not None:
            # Reusing cached raw snapshot without backend call
            raw_candidates = snapshot.raw_candidates
            self.snapshot_cache.record_execution(
                dedupe_key, action.actor_policy, action.temporal_policy
            )
        else:
            # Check follow-up search budget before backend call
            if not self.operation_budget.can_plan_followup_search():
                return RetrievalExecutionResult(
                    action_id=action.action_id,
                    status=RetrievalExecutionStatus.BUDGET_EXHAUSTED,
                    compiled_query=dedupe_key,
                    backend_calls=0,
                    safe_detail="Follow-up search budget exhausted",
                    budget_remaining=0,
                )

            # Execute backend search with explicit max_results cap (FOLLOWUP_MAX_RESULTS_PER_SEARCH = 5)
            search_query_obj = SearchQuery(
                query=dedupe_key,
                purpose=action.objective,
            )

            try:
                search_call = search_backend.search(
                    search_query_obj,
                    max_results=self.config.max_results_per_search,
                )
                if asyncio.iscoroutine(search_call) or hasattr(search_call, "__await__"):
                    search_res = _run_coroutine_sync(search_call)
                else:
                    search_res = search_call

                self.operation_budget.record_followup_search()
                backend_calls = 1

                if hasattr(search_res, "candidates"):
                    raw_candidates = list(search_res.candidates)
                elif hasattr(search_res, "items"):
                    raw_candidates = list(search_res.items)
                elif isinstance(search_res, list):
                    raw_candidates = list(search_res)
                else:
                    raw_candidates = []
            except Exception:
                # Separate infrastructure failure from integrity failure
                # RAW_EXCEPTION_STORED_IN_EXECUTION_RESULT = False
                return RetrievalExecutionResult(
                    action_id=action.action_id,
                    status=RetrievalExecutionStatus.SEARCH_FAILURE,
                    compiled_query=dedupe_key,
                    backend_calls=1,
                    error_code="SEARCH_BACKEND_ERROR",
                    safe_detail="Search backend failed to execute query safely",
                    budget_remaining=max(
                        0,
                        self.config.max_followup_searches
                        - self.operation_budget.followup_searches,
                    ),
                )

            # Populate in-memory snapshot cache
            self.snapshot_cache.put(dedupe_key, raw_candidates)
            self.snapshot_cache.record_execution(
                dedupe_key, action.actor_policy, action.temporal_policy
            )

        # 5. Evaluate raw candidates under the current action policy
        raw_candidate_count = len(raw_candidates)
        duplicate_evidence_count = 0
        filtered_temporal_count = 0
        unknown_timestamp_count = 0
        admitted_nodes: list[EvidenceNode] = []

        # Determine retrieval role: PRIMARY_SCOPE only if PRESERVE_ACTOR + STRICT_TEMPORAL
        if (
            action.actor_policy == ActorRetrievalPolicy.PRESERVE_AUTHORITATIVE_ACTOR
            and action.temporal_policy == TemporalRetrievalPolicy.STRICT_WITHIN_SCOPE
        ):
            retrieval_role = EvidenceRetrievalRole.PRIMARY_SCOPE
        else:
            retrieval_role = EvidenceRetrievalRole.CONTEXT_SCOPE

        for item in raw_candidates:
            # Author integrity and relation determination
            item_author_id = getattr(item, "author_id", None)

            if (
                action.actor_policy == ActorRetrievalPolicy.PRESERVE_AUTHORITATIVE_ACTOR
                and is_auth_self
            ):
                # Query was from:me -> author must match established self ID
                if (
                    item_author_id is not None
                    and self.identity_context
                    and self.identity_context.investigation_local_self_id
                    and item_author_id != self.identity_context.investigation_local_self_id
                ):
                    self.has_integrity_failure = True
                    return RetrievalExecutionResult(
                        action_id=action.action_id,
                        status=RetrievalExecutionStatus.INTEGRITY_FAILURE,
                        compiled_query=dedupe_key,
                        backend_calls=backend_calls,
                        raw_candidate_count=raw_candidate_count,
                        error_code="SELF_AUTHOR_MISMATCH",
                        safe_detail="Candidate author does not match established self ID",
                        budget_remaining=max(
                            0,
                            self.config.max_followup_searches
                            - self.operation_budget.followup_searches,
                        ),
                    )
                actor_relation = "SELF"
            else:
                # Context Search CANNOT bootstrap identity
                # FOLLOWUP_CAN_BOOTSTRAP_SELF_ID = False
                # CONTEXT_SEARCH_CAN_BOOTSTRAP_SELF_ID = False
                if (
                    self.identity_context
                    and self.identity_context.investigation_local_self_id
                ):
                    if item_author_id == self.identity_context.investigation_local_self_id:
                        actor_relation = "SELF"
                    elif item_author_id is not None:
                        actor_relation = "OTHER"
                    else:
                        actor_relation = "UNKNOWN"
                else:
                    actor_relation = "UNKNOWN"

            # Temporal filtering
            authored_dt = resolve_candidate_authored_datetime(item)

            if action.temporal_policy == TemporalRetrievalPolicy.STRICT_WITHIN_SCOPE:
                if authored_dt is None or authored_dt.tzinfo is None:
                    unknown_timestamp_count += 1
                    continue

                auth_temporal = graph.authoritative_temporal_scope
                if (
                    auth_temporal
                    and auth_temporal.strictness == TemporalStrictness.STRICT
                    and auth_temporal.status == TemporalResolutionStatus.RESOLVED
                ):
                    if not auth_temporal.is_in_range(authored_dt):
                        filtered_temporal_count += 1
                        continue

            elif (
                action.temporal_policy
                == TemporalRetrievalPolicy.BOUNDED_HISTORICAL_CONTEXT
            ):
                # Unknown timestamps excluded
                if authored_dt is None or authored_dt.tzinfo is None:
                    unknown_timestamp_count += 1
                    continue

                auth_temporal = graph.authoritative_temporal_scope
                if auth_temporal and auth_temporal.start_at is not None:
                    ref_tz = auth_temporal.start_at.tzinfo
                    hist_lower = auth_temporal.start_at - timedelta(
                        days=self.config.max_context_lookback_days
                    )
                    hist_upper = (
                        auth_temporal.end_at
                        if auth_temporal.end_at is not None
                        else datetime.now(ref_tz)
                    )
                else:
                    ref_tz = timezone.utc
                    now_ref = datetime.now(ref_tz)
                    hist_lower = now_ref - timedelta(
                        days=self.config.max_context_lookback_days
                    )
                    hist_upper = now_ref

                norm_authored = authored_dt.astimezone(ref_tz)
                if not (hist_lower <= norm_authored < hist_upper):
                    filtered_temporal_count += 1
                    continue

            # Canonical deduplication against existing graph evidence
            if self._is_duplicate_in_graph(item, graph):
                duplicate_evidence_count += 1
                continue

            # Graph evidence budget enforcement (capped at max_investigation_evidence = 30)
            if len(graph.evidence_nodes) >= self.config.max_investigation_evidence:
                break

            # Ingestion into graph
            canonical_eid = f"E{len(graph.evidence_nodes) + 1}"
            node = EvidenceNode(
                evidence_id=canonical_eid,
                source_type=item.source or "slack",
                canonical_permalink=item.permalink,
                presentation_author=item.author,
                actor_relation=actor_relation,
                timestamp=item.timestamp,
                authored_at=authored_dt,
                temporal_relation=item.temporal_relation,
                text=item.text,
                thread_root_id=item.thread_identifier,
                retrieval_round=action.round + 1,
                source_query=dedupe_key,
                retrieval_role=retrieval_role,
            )
            graph.add_evidence(node)

            # Provenance: Evidence DISCOVERED_FROM Action (or Gap)
            provenance_target = (
                action.action_id
                if action.action_id in graph.retrieval_actions
                else action.originating_gap_id
            )
            if not graph._path_exists_in_provenance(node.evidence_id, provenance_target):
                graph.add_provenance_edge(
                    ProvenanceEdge(
                        source_id=node.evidence_id,
                        target_id=provenance_target,
                        relation=ProvenanceRelation.DISCOVERED_FROM,
                    )
                )

            admitted_nodes.append(node)

        # FOLLOWUP_SEARCH_AUTOMATICALLY_RESOLVES_GAP = False
        # Gap remains OPEN; semantic claim verification is deferred to Phase 13.4

        status = (
            RetrievalExecutionStatus.EXECUTED
            if len(admitted_nodes) > 0
            else RetrievalExecutionStatus.NO_NEW_EVIDENCE
        )

        return RetrievalExecutionResult(
            action_id=action.action_id,
            status=status,
            compiled_query=dedupe_key,
            backend_calls=backend_calls,
            raw_candidate_count=raw_candidate_count,
            admitted_evidence_count=len(admitted_nodes),
            duplicate_evidence_count=duplicate_evidence_count,
            filtered_temporal_count=filtered_temporal_count,
            unknown_timestamp_count=unknown_timestamp_count,
            budget_remaining=max(
                0,
                self.config.max_followup_searches
                - self.operation_budget.followup_searches,
            ),
            admitted_evidence_ids=[n.evidence_id for n in admitted_nodes],
            safe_detail=f"Admitted {len(admitted_nodes)} items ({retrieval_role.value})",
        )

    async def execute_action_async(
        self,
        graph: InvestigationStateGraph,
        action: RetrievalAction,
        search_backend: Optional[Any] = None,
        thread_reader: Optional[Any] = None,
    ) -> RetrievalExecutionResult:
        """Asynchronously executes a validated RetrievalAction."""
        return self.execute_action(
            graph=graph,
            action=action,
            search_backend=search_backend,
            thread_reader=thread_reader,
        )
