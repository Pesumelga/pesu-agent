"""Phase 13.2: Investigation Seed Pool & Multi-Thread Context Expansion Coordinator.

Separates Investigation Evidence Collection from Final Answer Evidence:
- RC4_FINAL_EVIDENCE_CAP = 5 (strictly preserved for final answer presentation)
- RC4_EXISTING_THREAD_ENRICHMENT_MAX = 1 (strictly preserved on existing RC4 path)
- MAX_CONTEXT_THREAD_EXPANSIONS = 4 (Investigation v2 multi-thread context expansion)
- INVESTIGATION_SEED_POOL_CAN_EXCEED_FINAL_EVIDENCE_CAP = True
- INVESTIGATION_SEED_DEDUPLICATION = "CANONICAL"
- SAME_THREAD_EXPANDED_MORE_THAN_ONCE = False
- THREAD_READER_NEW_DOM_PRIMITIVES = 0
- THREAD_AUTOMATED_SCROLL = False
- THREAD_URL_NAVIGATION_FALLBACK = False
- THREAD_CONTEXT_AUTOMATICALLY_PRIMARY = False
- THREAD_CONTEXT_AUTOMATICALLY_SUPPORTS_CLAIM = False
- THREAD_REPLY_PRESENTATION_AUTHOR_USED_AS_IDENTITY = False
- CONTEXT_TEMPORAL_WIDENING_IMPLEMENTED = False
- INVESTIGATION_GRAPH_EVIDENCE_BUDGET = 30
- INVESTIGATION_GRAPH_EVIDENCE_BUDGET_ENFORCED = True
- THREAD_READER_REPLY_CAP_CHANGED = False
- CONTEXT_RELOCATION_SEARCH_BUDGET = 1
- CONTEXT_RELOCATION_PRESERVES_ACTOR_CONSTRAINT = True
- THREAD_COMPLETENESS_PROMOTED = False
- CLAIM_GENERATION_IN_PHASE_13_2 = False
- SELF_ACTION_VALIDATOR_APPLIES_TO_ALL_SELF_SUBJECT_CLAIMS = False
- INVESTIGATION_GRAPH_FEEDS_ANALYZER = False
- INVESTIGATION_GRAPH_FEEDS_UI = False
- INVESTIGATION_GRAPH_PERSISTED_TO_HISTORY = False
- RAW_CONTEXT_GRAPH_WRITTEN_TO_DISK = False
- DEFAULT_RC4_RUNTIME_EXTRA_THREAD_READS = 0
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from pesu_agent.investigation.graph_models import (
    EvidenceNode,
    EvidenceRetrievalRole,
    InvestigationBudgetConfig,
    InvestigationStateGraph,
    ProvenanceEdge,
    ProvenanceRelation,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    ParentContextState,
    ResolvedActorScope,
    ResolvedTemporalScope,
    SearchQuery,
    TemporalResolutionStatus,
    TemporalStrictness,
    ThreadContextStatus,
    ThreadReplyItem,
)
from pesu_agent.investigation.selector import (
    EvidenceSelector,
    get_canonical_evidence_key,
)
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.search.actor import ActorScopeKind
from pesu_agent.search.temporal import resolve_candidate_authored_datetime
from pesu_agent.search.webview_thread_reader import (
    MAX_ACCEPTED_REPLIES,
    ThreadReadFailureReason,
    ThreadReadResult,
    is_eligible_thread_candidate,
)

logger = logging.getLogger(__name__)

# Architectural Constants
RC4_FINAL_EVIDENCE_CAP: int = 5
RC4_EXISTING_THREAD_ENRICHMENT_MAX: int = 1
MAX_CONTEXT_THREAD_EXPANSIONS: int = 4
INVESTIGATION_GRAPH_EVIDENCE_BUDGET: int = 30
CONTEXT_RELOCATION_SEARCH_BUDGET: int = 1

INVESTIGATION_SEED_POOL_CAN_EXCEED_FINAL_EVIDENCE_CAP: bool = True
INVESTIGATION_SEED_DEDUPLICATION: str = "CANONICAL"
SAME_THREAD_EXPANDED_MORE_THAN_ONCE: bool = False
THREAD_READER_NEW_DOM_PRIMITIVES: int = 0
THREAD_AUTOMATED_SCROLL: bool = False
THREAD_URL_NAVIGATION_FALLBACK: bool = False
THREAD_CONTEXT_AUTOMATICALLY_PRIMARY: bool = False
THREAD_CONTEXT_AUTOMATICALLY_SUPPORTS_CLAIM: bool = False
THREAD_REPLY_PRESENTATION_AUTHOR_USED_AS_IDENTITY: bool = False
CONTEXT_TEMPORAL_WIDENING_IMPLEMENTED: bool = False
INVESTIGATION_GRAPH_EVIDENCE_BUDGET_ENFORCED: bool = True
THREAD_READER_REPLY_CAP_CHANGED: bool = False
CONTEXT_RELOCATION_PRESERVES_ACTOR_CONSTRAINT: bool = True
THREAD_COMPLETENESS_PROMOTED: bool = False
CLAIM_GENERATION_IN_PHASE_13_2: bool = False
SELF_ACTION_VALIDATOR_APPLIES_TO_ALL_SELF_SUBJECT_CLAIMS: bool = False
INVESTIGATION_GRAPH_FEEDS_ANALYZER: bool = False
INVESTIGATION_GRAPH_FEEDS_UI: bool = False
INVESTIGATION_GRAPH_PERSISTED_TO_HISTORY: bool = False
RAW_CONTEXT_GRAPH_WRITTEN_TO_DISK: bool = False
DEFAULT_RC4_RUNTIME_EXTRA_THREAD_READS: int = 0
PHASE_13_2_CHANGES_USER_VISIBLE_ANSWER: bool = False
PHASE_13_2_CHANGES_ANALYZER_PAYLOAD: bool = False
HISTORY_SCHEMA_CHANGED: bool = False
RELOCATION_BUDGET_SOURCE_OF_TRUTH: str = "SHARED_SLACK_OPERATION_BUDGET"
CONTEXT_COORDINATOR_HAS_INDEPENDENT_RELOCATION_ALLOWANCE: bool = False



@dataclass(frozen=True)
class CandidateQueryProvenanceHit:
    """Immutable record of query hit provenance."""

    query_index: int
    search_query: SearchQuery


def derive_canonical_thread_key(item: EvidenceItem) -> str:
    """Derives deterministic thread identifier to prevent duplicate thread expansion."""
    if item.thread_identifier:
        return f"thr:{item.thread_identifier}"

    permalink = getattr(item, "permalink", None) or getattr(item, "message_identifier", None)
    if permalink:
        identity = parse_slack_message_permalink(permalink)
        if identity:
            root_ts = identity.thread_ts or identity.message_ts
            return f"{identity.channel_id}:{root_ts}"

    return get_canonical_evidence_key(item)


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


class InvestigationSeedPoolBuilder:
    """Builds and populates the investigation-local seed pool of EvidenceNodes.

    Rules:
    - Ingests candidates after search structural integrity and authoritative scope validation.
    - Dedupes canonically via get_canonical_evidence_key while retaining query provenance.
    - Maps seeds to EvidenceNode with retrieval_role = PRIMARY_SCOPE.
    - Zero canonical Slack member IDs (author_id is excluded from EvidenceNode).
    - Can exceed final answer cap (5) up to max_investigation_evidence (30).
    """

    def __init__(self, budget_config: Optional[InvestigationBudgetConfig] = None) -> None:
        self.budget_config = budget_config or InvestigationBudgetConfig()

    def build_seed_nodes(
        self,
        candidates: list[EvidenceItem],
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
    ) -> tuple[list[EvidenceNode], dict[str, Any]]:
        """Transforms raw search candidates into deduplicated primary EvidenceNodes."""
        stats = {
            "initial_candidates": len(candidates),
            "temporal_filtered_out": 0,
            "duplicates_removed": 0,
            "seeds_admitted": 0,
        }

        # 1. Strict temporal pre-filter
        eligible_candidates: list[EvidenceItem] = []
        if temporal_scope and temporal_scope.strictness == TemporalStrictness.STRICT:
            if temporal_scope.status == TemporalResolutionStatus.RESOLVED:
                for item in candidates:
                    authored_dt = resolve_candidate_authored_datetime(item)
                    if authored_dt is not None and temporal_scope.is_in_range(authored_dt):
                        eligible_candidates.append(item)
                    else:
                        stats["temporal_filtered_out"] += 1
            else:
                stats["temporal_filtered_out"] = len(candidates)
                eligible_candidates = []
        else:
            eligible_candidates = list(candidates)

        # 2. Canonical deduplication (INVESTIGATION_SEED_DEDUPLICATION = "CANONICAL")
        seen_canonical_keys: set[str] = set()
        unique_items: list[EvidenceItem] = []

        for item in eligible_candidates:
            c_key = get_canonical_evidence_key(item)
            if c_key in seen_canonical_keys:
                stats["duplicates_removed"] += 1
                continue
            seen_canonical_keys.add(c_key)
            unique_items.append(item)

        # 3. Map to EvidenceNode under investigation evidence budget (max 30)
        seed_nodes: list[EvidenceNode] = []
        for idx, item in enumerate(unique_items):
            if len(seed_nodes) >= self.budget_config.max_investigation_evidence:
                break

            canonical_id = f"E{idx + 1}"
            authored_dt = resolve_candidate_authored_datetime(item)
            c_key = get_canonical_evidence_key(item)

            first_query = None
            if candidate_query_provenance and c_key in candidate_query_provenance:
                hits = candidate_query_provenance[c_key]
                if hits:
                    first_query = getattr(hits[0], "search_query", None)
                    if hasattr(first_query, "query"):
                        first_query = first_query.query

            node = EvidenceNode(
                evidence_id=canonical_id,
                source_type=item.source or "slack",
                canonical_permalink=item.permalink,
                presentation_author=item.author,
                actor_relation=item.author_relation or "UNKNOWN",
                timestamp=item.timestamp,
                authored_at=authored_dt,
                temporal_relation=item.temporal_relation,
                text=item.text,
                thread_root_id=item.thread_identifier,
                retrieval_round=1,
                source_query=str(first_query) if first_query else None,
                retrieval_role=EvidenceRetrievalRole.PRIMARY_SCOPE,
                parent_context_state=getattr(item, "parent_context_state", ParentContextState.SEARCH_SNIPPET),
            )
            seed_nodes.append(node)

        stats["seeds_admitted"] = len(seed_nodes)
        return seed_nodes, stats

    def populate_seed_graph(
        self,
        graph: InvestigationStateGraph,
        candidates: list[EvidenceItem],
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
    ) -> dict[str, Any]:
        """Populates the graph with seed EvidenceNodes, setting authoritative scopes."""
        if temporal_scope is not None and graph.authoritative_temporal_scope is None:
            graph.authoritative_temporal_scope = temporal_scope
        if actor_scope is not None and graph.authoritative_actor_scope is None:
            graph.authoritative_actor_scope = actor_scope

        seeds, stats = self.build_seed_nodes(
            candidates=candidates,
            temporal_scope=graph.authoritative_temporal_scope or temporal_scope,
            actor_scope=graph.authoritative_actor_scope or actor_scope,
            candidate_query_provenance=candidate_query_provenance,
        )

        for seed in seeds:
            if seed.evidence_id not in graph.evidence_nodes:
                graph.add_evidence(seed)

        return stats


class ThreadContextExpansionCoordinator:
    """Coordinates bounded, multi-thread context expansion for Investigation v2.

    Distinctions:
    - MAX_CONTEXT_THREAD_EXPANSIONS = 4 (separate from RC4_EXISTING_THREAD_ENRICHMENT_MAX = 1).
    - Ingests replies as retrieval_role = CONTEXT_SCOPE and actor_relation = "UNKNOWN".
    - Zero automated claim generation or claim linkage in Phase 13.2.
    - SAME_THREAD_EXPANDED_MORE_THAN_ONCE = False.
    - CONTEXT_RELOCATION_SEARCH_BUDGET <= 1.
    - Preserves exact SearchQuery on relocation replay.
    - Zero DOM changes; uses existing qualified ThreadReader primitives only.
    """

    def __init__(
        self,
        budget_config: Optional[InvestigationBudgetConfig] = None,
        max_context_thread_expansions: int = MAX_CONTEXT_THREAD_EXPANSIONS,
        max_context_relocations: int = CONTEXT_RELOCATION_SEARCH_BUDGET,
        operation_budget: Optional[Any] = None,
    ) -> None:
        self.budget_config = budget_config or InvestigationBudgetConfig()
        self.max_context_thread_expansions = min(
            max_context_thread_expansions,
            self.budget_config.max_thread_expansions,
        )
        self.max_context_relocations = max_context_relocations
        self.operation_budget = operation_budget

        # Local coordination state
        self.visited_thread_keys: set[str] = set()
        self.thread_expansions_performed: int = 0
        self.relocation_searches_performed: int = 0
        self.expansion_records: list[dict[str, Any]] = []

    def _can_relocate(self, budget_override: Optional[Any] = None) -> bool:
        budget = budget_override or self.operation_budget
        if budget is not None:
            return budget.can_perform_relocation()
        return self.relocation_searches_performed < self.max_context_relocations

    def _record_relocation(self, budget_override: Optional[Any] = None) -> None:
        budget = budget_override or self.operation_budget
        if budget is not None:
            budget.record_relocation_search()
        self.relocation_searches_performed += 1

    def _can_expand_thread(self, budget_override: Optional[Any] = None) -> bool:
        budget = budget_override or self.operation_budget
        if budget is not None:
            if not budget.can_perform_thread_expansion():
                return False
        return self.thread_expansions_performed < self.max_context_thread_expansions

    def _record_expansion(self, budget_override: Optional[Any] = None) -> None:
        budget = budget_override or self.operation_budget
        if budget is not None:
            budget.record_thread_expansion()
        self.thread_expansions_performed += 1

    def select_thread_candidates(
        self,
        candidates: list[EvidenceItem],
        temporal_scope: Optional[ResolvedTemporalScope] = None,
    ) -> list[EvidenceItem]:
        """Selects up to MAX_CONTEXT_THREAD_EXPANSIONS distinct, eligible thread candidates."""
        eligible_items: list[EvidenceItem] = []
        seen_thread_keys: set[str] = set()

        for c in candidates:
            if not is_eligible_thread_candidate(c):
                continue

            # Temporal eligibility check if strict
            if temporal_scope and temporal_scope.strictness == TemporalStrictness.STRICT:
                if temporal_scope.status == TemporalResolutionStatus.RESOLVED:
                    authored_dt = resolve_candidate_authored_datetime(c)
                    if authored_dt is None or not temporal_scope.is_in_range(authored_dt):
                        continue

            t_key = derive_canonical_thread_key(c)
            if t_key in seen_thread_keys or t_key in self.visited_thread_keys:
                continue

            seen_thread_keys.add(t_key)
            eligible_items.append(c)

            if len(eligible_items) >= self.max_context_thread_expansions:
                break

        return eligible_items

    def expand_threads_sync(
        self,
        graph: InvestigationStateGraph,
        candidates: list[EvidenceItem],
        thread_reader: Any,
        search_backend: Optional[Any] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        operation_budget: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Synchronous execution of multi-thread context expansion."""
        selected_candidates = self.select_thread_candidates(
            candidates=candidates,
            temporal_scope=temporal_scope or graph.authoritative_temporal_scope,
        )

        telemetry: dict[str, Any] = {
            "selected_thread_candidates_count": len(selected_candidates),
            "thread_expansions_performed": 0,
            "relocation_searches_performed": 0,
            "context_replies_admitted": 0,
            "skipped_already_visited": 0,
            "skipped_budget_exhausted": 0,
            "skipped_relocation_exhausted": 0,
        }

        active_budget = operation_budget or self.operation_budget

        for target in selected_candidates:
            if not self._can_expand_thread(active_budget):
                break

            # Check overall evidence budget
            if len(graph.evidence_nodes) >= self.budget_config.max_investigation_evidence:
                telemetry["skipped_budget_exhausted"] += 1
                break

            thread_key = derive_canonical_thread_key(target)
            if thread_key in self.visited_thread_keys:
                telemetry["skipped_already_visited"] += 1
                continue

            chosen_key = get_canonical_evidence_key(target)
            parent_evidence_id = None
            for ev_id, ev_node in graph.evidence_nodes.items():
                if ev_node.canonical_permalink == target.permalink:
                    parent_evidence_id = ev_id
                    break
            if not parent_evidence_id:
                parent_evidence_id = target.evidence_id or f"E_root_{len(self.visited_thread_keys)+1}"

            # Step 1: Initial residency check (First ThreadReader call)
            first_res = thread_reader.read_thread_result(target)
            if asyncio.iscoroutine(first_res) or hasattr(first_res, "__await__"):
                first_res = _run_coroutine_sync(first_res)

            final_read_res: ThreadReadResult = first_res

            # Step 2: Evaluate residency and relocation eligibility
            is_resident = (
                first_res.status == ThreadContextStatus.PARTIAL
                or len(first_res.replies) > 0
            )

            if not is_resident and first_res.failure_reason == ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM:
                # Target not in DOM -> check relocation budget
                if self._can_relocate(active_budget) and search_backend is not None:
                    hits = (candidate_query_provenance or {}).get(chosen_key, [])
                    if hits:
                        # Select highest executed query hit snapshot
                        selected_hit = max(hits, key=lambda h: getattr(h, "query_index", 0))
                        source_query = getattr(selected_hit, "search_query", None)
                        if source_query is not None:
                            # Verify SELF actor constraint preservation
                            if actor_scope and actor_scope.kind == ActorScopeKind.SELF:
                                q_str = source_query.query if hasattr(source_query, "query") else str(source_query)
                                if not re.search(r'(?:^|\s)from:me(?:\s|$)', q_str):
                                    raise RuntimeError(
                                        f"Relocation query violated SELF constraint (missing 'from:me'): {q_str}"
                                    )

                            logger.info(f"Executing context relocation search (query='{getattr(source_query, 'query', '')}')")
                            try:
                                search_call = search_backend.search(source_query, max_results=5)
                                if asyncio.iscoroutine(search_call) or hasattr(search_call, "__await__"):
                                    _ = _run_coroutine_sync(search_call)

                                self._record_relocation(active_budget)
                                telemetry["relocation_searches_performed"] = (
                                    active_budget.relocation_searches
                                    if active_budget is not None
                                    else self.relocation_searches_performed
                                )

                                second_res = thread_reader.read_thread_result(target)
                                if asyncio.iscoroutine(second_res) or hasattr(second_res, "__await__"):
                                    second_res = _run_coroutine_sync(second_res)
                                final_read_res = second_res
                            except Exception as rel_err:
                                logger.warning(f"Context relocation search failed safely: {rel_err}")
                    else:
                        logger.warning(f"Target row not in DOM and no query provenance for {chosen_key}")
                else:
                    telemetry["skipped_relocation_exhausted"] += 1
                    logger.info(
                        f"Skipping context thread expansion for {chosen_key}: "
                        f"relocation budget exhausted"
                    )

            # Step 3: Extract and validate replies
            raw_replies = list(getattr(final_read_res, "replies", ()))

            # Reply temporal filtering if strict (CONTEXT_TEMPORAL_WIDENING_IMPLEMENTED = False)
            auth_temporal = temporal_scope or graph.authoritative_temporal_scope
            if (
                auth_temporal
                and auth_temporal.strictness == TemporalStrictness.STRICT
                and auth_temporal.status == TemporalResolutionStatus.RESOLVED
                and raw_replies
            ):
                eligible_replies = []
                for r in raw_replies:
                    r_dt = resolve_candidate_authored_datetime(
                        EvidenceItem(
                            evidence_id="tmp",
                            source="slack",
                            timestamp=r.message_ts or r.timestamp,
                            permalink=r.permalink,
                            text=r.text,
                        )
                    )
                    if r_dt is not None and auth_temporal.is_in_range(r_dt):
                        eligible_replies.append(r)
                raw_replies = eligible_replies

            # Step 4: Admitting replies into graph under evidence budget
            admitted_count = 0
            for r_idx, r in enumerate(raw_replies):
                if len(graph.evidence_nodes) >= self.budget_config.max_investigation_evidence:
                    telemetry["skipped_budget_exhausted"] += 1
                    break

                reply_ev_id = f"{parent_evidence_id}-R{r_idx + 1}"
                r_authored_dt = resolve_candidate_authored_datetime(
                    EvidenceItem(
                        evidence_id="tmp",
                        source="slack",
                        timestamp=r.message_ts or r.timestamp,
                        permalink=r.permalink,
                        text=r.text,
                    )
                )

                reply_node = EvidenceNode(
                    evidence_id=reply_ev_id,
                    source_type="slack_thread_reply",
                    canonical_permalink=r.permalink,
                    presentation_author=r.author,
                    actor_relation="UNKNOWN",  # THREAD_REPLY_PRESENTATION_AUTHOR_USED_AS_IDENTITY = False
                    timestamp=r.message_ts or r.timestamp,
                    authored_at=r_authored_dt,
                    text=r.text,
                    thread_root_id=r.thread_ts,
                    retrieval_round=1,
                    retrieval_role=EvidenceRetrievalRole.CONTEXT_SCOPE,  # THREAD_CONTEXT_AUTOMATICALLY_PRIMARY = False
                )

                if reply_node.evidence_id not in graph.evidence_nodes:
                    graph.add_evidence(reply_node)
                    # Provenance edge: reply DISCOVERED_FROM parent
                    if parent_evidence_id in graph.evidence_nodes:
                        graph.add_provenance_edge(
                            ProvenanceEdge(
                                source_id=reply_node.evidence_id,
                                target_id=parent_evidence_id,
                                relation=ProvenanceRelation.DISCOVERED_FROM,
                            )
                        )
                    admitted_count += 1

            self.visited_thread_keys.add(thread_key)
            self._record_expansion(active_budget)
            telemetry["thread_expansions_performed"] = (
                active_budget.thread_expansions
                if active_budget is not None
                else self.thread_expansions_performed
            )
            telemetry["context_replies_admitted"] += admitted_count

        return telemetry

    async def expand_threads(
        self,
        graph: InvestigationStateGraph,
        candidates: list[EvidenceItem],
        thread_reader: Any,
        search_backend: Optional[Any] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        operation_budget: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Asynchronous execution of multi-thread context expansion."""
        selected_candidates = self.select_thread_candidates(
            candidates=candidates,
            temporal_scope=temporal_scope or graph.authoritative_temporal_scope,
        )

        telemetry: dict[str, Any] = {
            "selected_thread_candidates_count": len(selected_candidates),
            "thread_expansions_performed": 0,
            "relocation_searches_performed": 0,
            "context_replies_admitted": 0,
            "skipped_already_visited": 0,
            "skipped_budget_exhausted": 0,
            "skipped_relocation_exhausted": 0,
        }

        active_budget = operation_budget or self.operation_budget

        for target in selected_candidates:
            if not self._can_expand_thread(active_budget):
                break

            if len(graph.evidence_nodes) >= self.budget_config.max_investigation_evidence:
                telemetry["skipped_budget_exhausted"] += 1
                break

            thread_key = derive_canonical_thread_key(target)
            if thread_key in self.visited_thread_keys:
                telemetry["skipped_already_visited"] += 1
                continue

            chosen_key = get_canonical_evidence_key(target)
            parent_evidence_id = None
            for ev_id, ev_node in graph.evidence_nodes.items():
                if ev_node.canonical_permalink == target.permalink:
                    parent_evidence_id = ev_id
                    break
            if not parent_evidence_id:
                parent_evidence_id = target.evidence_id or f"E_root_{len(self.visited_thread_keys)+1}"

            # Step 1: Initial residency check
            first_res = thread_reader.read_thread_result(target)
            if asyncio.iscoroutine(first_res) or hasattr(first_res, "__await__"):
                first_res = await first_res

            final_read_res: ThreadReadResult = first_res

            # Step 2: Evaluate residency and relocation
            is_resident = (
                first_res.status == ThreadContextStatus.PARTIAL
                or len(first_res.replies) > 0
            )

            if not is_resident and first_res.failure_reason == ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM:
                if self._can_relocate(active_budget) and search_backend is not None:
                    hits = (candidate_query_provenance or {}).get(chosen_key, [])
                    if hits:
                        selected_hit = max(hits, key=lambda h: getattr(h, "query_index", 0))
                        source_query = getattr(selected_hit, "search_query", None)
                        if source_query is not None:
                            if actor_scope and actor_scope.kind == ActorScopeKind.SELF:
                                q_str = source_query.query if hasattr(source_query, "query") else str(source_query)
                                if not re.search(r'(?:^|\s)from:me(?:\s|$)', q_str):
                                    raise RuntimeError(
                                        f"Relocation query violated SELF constraint (missing 'from:me'): {q_str}"
                                    )

                            logger.info(f"Executing context relocation search (query='{getattr(source_query, 'query', '')}')")
                            try:
                                search_call = search_backend.search(source_query, max_results=5)
                                if asyncio.iscoroutine(search_call) or hasattr(search_call, "__await__"):
                                    _ = await search_call

                                self._record_relocation(active_budget)
                                telemetry["relocation_searches_performed"] = (
                                    active_budget.relocation_searches
                                    if active_budget is not None
                                    else self.relocation_searches_performed
                                )

                                second_res = thread_reader.read_thread_result(target)
                                if asyncio.iscoroutine(second_res) or hasattr(second_res, "__await__"):
                                    second_res = await second_res
                                final_read_res = second_res
                            except Exception as rel_err:
                                logger.warning(f"Context relocation search failed safely: {rel_err}")
                    else:
                        logger.warning(f"Target row not in DOM and no query provenance for {chosen_key}")
                else:
                    telemetry["skipped_relocation_exhausted"] += 1

            # Step 3: Extract and validate replies
            raw_replies = list(getattr(final_read_res, "replies", ()))

            auth_temporal = temporal_scope or graph.authoritative_temporal_scope
            if (
                auth_temporal
                and auth_temporal.strictness == TemporalStrictness.STRICT
                and auth_temporal.status == TemporalResolutionStatus.RESOLVED
                and raw_replies
            ):
                eligible_replies = []
                for r in raw_replies:
                    r_dt = resolve_candidate_authored_datetime(
                        EvidenceItem(
                            evidence_id="tmp",
                            source="slack",
                            timestamp=r.message_ts or r.timestamp,
                            permalink=r.permalink,
                            text=r.text,
                        )
                    )
                    if r_dt is not None and auth_temporal.is_in_range(r_dt):
                        eligible_replies.append(r)
                raw_replies = eligible_replies

            # Step 4: Admitting replies into graph
            admitted_count = 0
            for r_idx, r in enumerate(raw_replies):
                if len(graph.evidence_nodes) >= self.budget_config.max_investigation_evidence:
                    telemetry["skipped_budget_exhausted"] += 1
                    break

                reply_ev_id = f"{parent_evidence_id}-R{r_idx + 1}"
                r_authored_dt = resolve_candidate_authored_datetime(
                    EvidenceItem(
                        evidence_id="tmp",
                        source="slack",
                        timestamp=r.message_ts or r.timestamp,
                        permalink=r.permalink,
                        text=r.text,
                    )
                )

                reply_node = EvidenceNode(
                    evidence_id=reply_ev_id,
                    source_type="slack_thread_reply",
                    canonical_permalink=r.permalink,
                    presentation_author=r.author,
                    actor_relation="UNKNOWN",
                    timestamp=r.message_ts or r.timestamp,
                    authored_at=r_authored_dt,
                    text=r.text,
                    thread_root_id=r.thread_ts,
                    retrieval_round=1,
                    retrieval_role=EvidenceRetrievalRole.CONTEXT_SCOPE,
                )

                if reply_node.evidence_id not in graph.evidence_nodes:
                    graph.add_evidence(reply_node)
                    if parent_evidence_id in graph.evidence_nodes:
                        graph.add_provenance_edge(
                            ProvenanceEdge(
                                source_id=reply_node.evidence_id,
                                target_id=parent_evidence_id,
                                relation=ProvenanceRelation.DISCOVERED_FROM,
                            )
                        )
                    admitted_count += 1

            self.visited_thread_keys.add(thread_key)
            self._record_expansion(active_budget)
            telemetry["thread_expansions_performed"] = (
                active_budget.thread_expansions
                if active_budget is not None
                else self.thread_expansions_performed
            )
            telemetry["context_replies_admitted"] += admitted_count

        return telemetry
