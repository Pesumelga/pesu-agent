"""Production Investigation Retrieval Coordinator.

Authoritative shared retrieval seam extracted from InvestigationOrchestrator.
Guarantees:
- QUALIFICATION_HARNESS_DOES_NOT_REIMPLEMENT_PRODUCTION_RETRIEVAL = True
- SHARED_RETRIEVAL_EXECUTION_CORE_COUNT = 1
- CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT = 0
- TELEMETRY_AFFECTS_RANKING = False
- TELEMETRY_AFFECTS_FILTERING = False
- TELEMETRY_AFFECTS_DEDUPE = False
- EMPTY_ANCHOR_TERMS_WHEN_PLAN_HAS_ANCHORS = forbidden
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import uuid
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesu_agent.investigation.exceptions import SelfAuthorConstraintIntegrityError
from pesu_agent.search.webview_slack_search import (
    WebViewSearchError,
    WebViewSearchFailureCode,
)

EXPLICIT_QUERY_LOCAL_CODES: frozenset[Any] = frozenset({
    WebViewSearchFailureCode.EMPTY_QUERY_INVALID,
    WebViewSearchFailureCode.EMPTY_QUERY_INVALID.value,
})
from pesu_agent.investigation.models import (
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    InvestigationStopReason,
    RetrievalCoverage,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
    UserQuestion,
)


@dataclass(frozen=True)
class CandidateQueryHit:
    """Internal provenance tracking for search query hits."""

    query_index: int
    search_query: SearchQuery
DEFAULT_INVESTIGATIONS_DIR = Path("output/investigations")


class OrchestratorConfig(BaseModel):
    """Orchestrator 및 RetrievalCoordinator 안전성 및 실행 파라미터 설정."""

    max_search_rounds: int = Field(default=2, description="최대 반복 조사 라운드 수")
    max_queries_per_round: int = Field(default=3, description="라운드당 최대 쿼리 수 (Retrieval v2 상한 3)")
    max_total_queries: int = Field(default=6, description="전체 누적 최대 쿼리 수")
    max_evidence_items: int = Field(default=5, description="최종 증거 패키지 최대 항목 수 (FINAL_EVIDENCE_LIMIT = 5)")
    max_external_llm_calls: int = Field(default=4, description="단일 조사당 최대 LLM 호출 수")
    max_cost_usd: float = Field(default=0.05, description="단일 조사당 최대 허용 비용 ($)")
    planner_model: str = Field(default="openai/gpt-5.6-luna")
    analyzer_model: str = Field(default="openai/gpt-5.6-luna")
    planner_reasoning_effort: Optional[str] = Field(default="medium")
    analyzer_reasoning_effort: Optional[str] = Field(default="high")
    base_output_dir: Path = Field(default=DEFAULT_INVESTIGATIONS_DIR)
    search_target: str = Field(default="slack")
    reference_time: Optional[datetime] = Field(default=None)
    timezone: str = Field(default="Asia/Seoul")
    allow_real_slack_external_analysis: bool = Field(default=False)

from pesu_agent.investigation.planner import InvestigationPlanner
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.investigation.selector import (
    EvidenceSelector,
    compute_anchor_matches,
    get_canonical_evidence_key,
)
from pesu_agent.search.actor import (
    ActorResolutionStatus,
    ActorScopeKind,
    ResolvedActorScope,
    resolve_actor_scope,
)
from pesu_agent.search.temporal import (
    TemporalResolutionStatus,
    TemporalStrictness,
    ResolvedTemporalScope,
    resolve_candidate_authored_datetime,
    resolve_temporal_scope,
)
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
    is_eligible_thread_candidate,
)

logger = logging.getLogger(__name__)

# Single execution core invariants
SHARED_RETRIEVAL_EXECUTION_CORE_COUNT: int = 1
CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT: int = 0
TELEMETRY_AFFECTS_RANKING: bool = False
TELEMETRY_AFFECTS_FILTERING: bool = False
TELEMETRY_AFFECTS_DEDUPE: bool = False


from pesu_agent.llm.models import LLMUsageStats


class RetrievalCoordinatorResult(BaseModel):
    """Result envelope for ProductionInvestigationRetrievalCoordinator execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    evidence_package: EvidencePackage
    candidates: list[EvidenceItem]
    selected_candidates: list[EvidenceItem]
    candidate_query_provenance: dict[str, list[CandidateQueryHit]]
    plan: InvestigationPlan
    temporal_scope: ResolvedTemporalScope
    actor_scope: ResolvedActorScope
    thread_expansion_telemetry: Optional[dict[str, Any]] = None
    raw_query_records: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_boundary_telemetry: dict[str, Any] = Field(default_factory=dict)
    stop_reason: Optional[InvestigationStopReason] = None
    error_message: Optional[str] = None
    plan_usage: Optional[LLMUsageStats] = None
    duplicates_removed: int = 0
    queries_executed: int = 0
    relocation_search_count: int = 0
    thread_expansions_performed: int = 0
    investigation_local_self_id: Optional[str] = None



class ProductionInvestigationRetrievalCoordinator:
    """Authoritative semantic-neutral retrieval coordinator.

    Unifies question-driven and precomputed-plan retrieval across production
    InvestigationOrchestrator and qualification/diagnostic harnesses through
    a single shared execute_plan core.
    """

    def __init__(
        self,
        search_backend: SearchBackend,
        selector: Optional[EvidenceSelector] = None,
        planner: Optional[InvestigationPlanner] = None,
        config: Optional[OrchestratorConfig] = None,
    ) -> None:
        self.search_backend = search_backend
        self.config = config or OrchestratorConfig()
        self.planner = planner
        self.selector = selector or EvidenceSelector(
            max_evidence_items=self.config.max_evidence_items,
        )
        self.last_thread_expansion_telemetry: Optional[dict[str, Any]] = None

    async def _read_candidate_thread(
        self, candidate: EvidenceItem, thread_reader_fn: Optional[Callable] = None
    ) -> ThreadReadResult:
        """Reads mounted thread context for an eligible candidate, returning typed ThreadReadResult.

        Maintains fail-closed semantics identical to InvestigationOrchestrator:
        - If thread_reader_fn is provided, delegates to it.
        - If search_backend supports read_thread_result, delegates directly.
        - If search_backend only supports legacy read_thread_context, wraps result without typed failure reason.
        - If search_backend has host window, delegates to WebviewThreadReader.read_thread_result.
        - Otherwise returns ThreadReadResult with NOT_ATTEMPTED.
        """
        if thread_reader_fn is not None:
            res = thread_reader_fn(candidate)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                res = await res
            if isinstance(res, ThreadReadResult):
                return res
            replies = getattr(res, "replies", ())
            status = getattr(res, "status", ThreadContextStatus.UNAVAILABLE)
            reason = getattr(res, "failure_reason", None)
            root = getattr(res, "root", None)
            return ThreadReadResult(replies=tuple(replies), status=status, failure_reason=reason, root=root)

        if hasattr(self.search_backend, "read_thread_result"):
            res = self.search_backend.read_thread_result(candidate)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                res = await res
            if isinstance(res, ThreadReadResult):
                return res
            replies = getattr(res, "replies", ())
            status = getattr(res, "status", ThreadContextStatus.UNAVAILABLE)
            reason = getattr(res, "failure_reason", None)
            root = getattr(res, "root", None)
            return ThreadReadResult(replies=tuple(replies), status=status, failure_reason=reason, root=root)
        elif hasattr(self.search_backend, "read_thread_context"):
            call_res = self.search_backend.read_thread_context(candidate)
            if asyncio.iscoroutine(call_res) or hasattr(call_res, "__await__"):
                call_res = await call_res
            replies, thr_status = call_res
            return ThreadReadResult(
                replies=tuple(replies),
                status=thr_status,
                failure_reason=None,
            )
        else:
            from pesu_agent.search.webview_thread_reader import WebviewThreadReader
            host = getattr(self.search_backend, "_window", None)
            if host:
                reader = WebviewThreadReader(host=host)
                return reader.read_thread_result(candidate)
            else:
                return ThreadReadResult(
                    replies=(),
                    status=ThreadContextStatus.NOT_ATTEMPTED,
                    failure_reason=None,
                )

    def _save_atomic_json(self, target_path: Path, data: BaseModel | dict[str, Any]) -> None:
        """Atomic Write 방식으로 JSON 파일 저장."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        json_str = (
            data.model_dump_json(indent=2)
            if isinstance(data, BaseModel)
            else json.dumps(data, indent=2, ensure_ascii=False)
        )
        temp_fd, temp_file_path = tempfile.mkstemp(
            dir=str(target_path.parent), prefix=".tmp_ret_", suffix=".json"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(json_str)
            os.replace(temp_file_path, str(target_path))
        except Exception as err:
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass
            logger.error(f"Failed to persist {target_path}: {err}")
            raise

    async def retrieve(
        self,
        question: UserQuestion | str,
        investigation_id: Optional[str] = None,
        inv_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        reference_time: Optional[datetime] = None,
        thread_reader_fn: Optional[Callable] = None,
    ) -> RetrievalCoordinatorResult:
        """Question-driven retrieval entrypoint: resolves scopes, plans, and executes plan."""
        q_text = question.question if isinstance(question, UserQuestion) else question
        u_question = question if isinstance(question, UserQuestion) else UserQuestion(question=q_text)

        inv_id = investigation_id or str(uuid.uuid4())
        ref_dt = reference_time or self.config.reference_time

        resolved_temporal_scope = resolve_temporal_scope(
            text=q_text,
            reference_time=ref_dt,
            timezone_name=self.config.timezone,
        )
        resolved_actor_scope = resolve_actor_scope(q_text)

        # Plan construction / loading
        plan: Optional[InvestigationPlan] = None
        if inv_dir:
            plan_path = inv_dir / "plan.json"
            if plan_path.exists():
                logger.info(f"[RESUME] Loading existing plan from {plan_path}")
                plan = InvestigationPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

        plan_usage: Optional[LLMUsageStats] = None
        if plan is None:
            if self.planner is None:
                raise ValueError("InvestigationPlanner required for question-driven retrieval when plan is not cached.")
            if on_progress:
                on_progress("planning", "조사 계획 수립 중...")
            plan, plan_usage = await self.planner.plan(
                question=u_question,
                model=self.config.planner_model,
                reasoning_effort=self.config.planner_reasoning_effort,
                max_search_queries=self.config.max_queries_per_round,
                search_target=self.config.search_target,
            )
            if inv_dir:
                self._save_atomic_json(inv_dir / "plan.json", plan)

        return await self.execute_plan(
            plan=plan,
            question_text=q_text,
            resolved_temporal_scope=resolved_temporal_scope,
            resolved_actor_scope=resolved_actor_scope,
            investigation_id=inv_id,
            inv_dir=inv_dir,
            on_progress=on_progress,
            plan_usage=plan_usage,
            thread_reader_fn=thread_reader_fn,
        )

    async def retrieve_precomputed(
        self,
        plan: InvestigationPlan,
        question_text: Optional[str] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        investigation_id: Optional[str] = None,
        inv_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        thread_reader_fn: Optional[Callable] = None,
    ) -> RetrievalCoordinatorResult:
        """Precomputed-plan retrieval entrypoint: executes pre-declared plan via single execute_plan core."""
        q_text = question_text or (plan.questions_to_resolve[0] if plan.questions_to_resolve else plan.objective)
        resolved_temporal = temporal_scope or ResolvedTemporalScope(
            strictness=TemporalStrictness.NONE,
            status=TemporalResolutionStatus.NONE,
        )
        resolved_actor = actor_scope or ResolvedActorScope(
            kind=ActorScopeKind.UNSPECIFIED,
            status=ActorResolutionStatus.RESOLVED,
            is_implicit=False,
        )
        inv_id = investigation_id or str(uuid.uuid4())

        return await self.execute_plan(
            plan=plan,
            question_text=q_text,
            resolved_temporal_scope=resolved_temporal,
            resolved_actor_scope=resolved_actor,
            investigation_id=inv_id,
            inv_dir=inv_dir,
            on_progress=on_progress,
            thread_reader_fn=thread_reader_fn,
        )

    async def execute_plan(
        self,
        plan: InvestigationPlan,
        question_text: str,
        resolved_temporal_scope: ResolvedTemporalScope,
        resolved_actor_scope: ResolvedActorScope,
        investigation_id: str,
        inv_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        round_number: int = 1,
        plan_usage: Optional[LLMUsageStats] = None,
        override_queries: Optional[list[SearchQuery]] = None,
        prior_candidates: Optional[list[EvidenceItem]] = None,
        prior_provenance: Optional[dict[str, list[CandidateQueryHit]]] = None,
        prior_relocation_count: int = 0,
        prior_queries_executed: int = 0,
        thread_expansions_performed: int = 0,
        thread_reader_fn: Optional[Callable] = None,
        investigation_local_self_id: Optional[str] = None,
    ) -> RetrievalCoordinatorResult:
        """THE SINGLE, AUTHORITATIVE RETRIEVAL EXECUTION CORE.

        SHARED_RETRIEVAL_EXECUTION_CORE_COUNT = 1.
        All query execution, dedupe, thread expansion, relocation, and selector
        packaging logic resides strictly here.
        """
        # Scope fail-closed checks
        if (
            resolved_temporal_scope.strictness == TemporalStrictness.STRICT
            and resolved_temporal_scope.status in (
                TemporalResolutionStatus.AMBIGUOUS,
                TemporalResolutionStatus.UNRESOLVED,
            )
        ):
            logger.warning("⚠️ [STRICT_TEMPORAL_RESOLUTION_FAILED] Terminating retrieval fail-closed.")
            empty_pkg = EvidencePackage(
                package_id=f"pkg_{investigation_id}_unresolved",
                query=question_text,
                total_items=0,
                created_at=datetime.now(timezone.utc).isoformat(),
                is_synthetic=False,
                evidence_items=[],
                retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                temporal_scope=resolved_temporal_scope,
                actor_scope=resolved_actor_scope,
            )
            if inv_dir:
                self._save_atomic_json(inv_dir / "evidence_package.json", empty_pkg)
            return RetrievalCoordinatorResult(
                evidence_package=empty_pkg,
                candidates=[],
                selected_candidates=[],
                candidate_query_provenance={},
                plan=plan,
                temporal_scope=resolved_temporal_scope,
                actor_scope=resolved_actor_scope,
                stop_reason=InvestigationStopReason.ANALYSIS_INCONCLUSIVE,
            )

        if resolved_actor_scope.status in (
            ActorResolutionStatus.AMBIGUOUS,
            ActorResolutionStatus.UNRESOLVED,
        ):
            logger.warning("⚠️ [ACTOR_SCOPE_RESOLUTION_FAILED] Terminating retrieval fail-closed.")
            empty_pkg = EvidencePackage(
                package_id=f"pkg_{investigation_id}_actor_unresolved",
                query=question_text,
                total_items=0,
                created_at=datetime.now(timezone.utc).isoformat(),
                is_synthetic=False,
                evidence_items=[],
                retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                temporal_scope=resolved_temporal_scope,
                actor_scope=resolved_actor_scope,
            )
            if inv_dir:
                self._save_atomic_json(inv_dir / "evidence_package.json", empty_pkg)
            return RetrievalCoordinatorResult(
                evidence_package=empty_pkg,
                candidates=[],
                selected_candidates=[],
                candidate_query_provenance={},
                plan=plan,
                temporal_scope=resolved_temporal_scope,
                actor_scope=resolved_actor_scope,
                stop_reason=InvestigationStopReason.ANALYSIS_INCONCLUSIVE,
            )

        active_team_id: Optional[str] = None
        if resolved_actor_scope.kind == ActorScopeKind.SELF:
            if hasattr(self.search_backend, "get_active_team_id"):
                active_team_id = self.search_backend.get_active_team_id()
            if not active_team_id:
                logger.warning("⚠️ [SELF_ACTIVE_TEAM_UNAVAILABLE] Terminating retrieval fail-closed.")
                empty_pkg = EvidencePackage(
                    package_id=f"pkg_{investigation_id}_team_unavailable",
                    query=question_text,
                    total_items=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    is_synthetic=False,
                    evidence_items=[],
                    retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                    temporal_scope=resolved_temporal_scope,
                    actor_scope=resolved_actor_scope,
                )
                if inv_dir:
                    self._save_atomic_json(inv_dir / "evidence_package.json", empty_pkg)
                return RetrievalCoordinatorResult(
                    evidence_package=empty_pkg,
                    candidates=[],
                    selected_candidates=[],
                    candidate_query_provenance={},
                    plan=plan,
                    temporal_scope=resolved_temporal_scope,
                    actor_scope=resolved_actor_scope,
                    stop_reason=InvestigationStopReason.ANALYSIS_INCONCLUSIVE,
                )

        all_candidates: list[EvidenceItem] = list(prior_candidates) if prior_candidates is not None else []
        candidate_query_provenance: dict[str, list[CandidateQueryHit]] = (
            {k: list(v) for k, v in prior_provenance.items()} if prior_provenance is not None else {}
        )
        raw_query_records: list[dict[str, Any]] = []
        pipeline_boundary_telemetry: dict[str, Any] = {}

        if override_queries is not None:
            queries_to_execute = list(override_queries)[: self.config.max_queries_per_round]
        else:
            queries_to_execute = list(plan.search_queries)[: self.config.max_queries_per_round]
        total_to_run = min(len(queries_to_execute), 3, self.config.max_queries_per_round)

        relocation_searches_performed = prior_relocation_count
        thread_expansions_done = thread_expansions_performed
        total_queries_executed = prior_queries_executed
        round_queries_executed = 0
        round_candidates: list[EvidenceItem] = []


        # -------------------------------------------------------------
        # Step 1: Execute Queries
        # -------------------------------------------------------------
        for q_idx, sq in enumerate(queries_to_execute[:total_to_run]):
            if total_queries_executed >= self.config.max_total_queries:
                logger.warning("🚨 [QUERY_BUDGET_REACHED] 최대 쿼리 한도 도달")
                stop_reason = InvestigationStopReason.QUERY_BUDGET_REACHED
                break

            if len(round_candidates) >= 15:
                logger.info("ℹ️ [MAX_RAW_CANDIDATES_REACHED] 최대 원본 후보군(15건) 도달")
                break

            if on_progress:
                on_progress(
                    "searching_slack",
                    f"Slack에서 관련 표현을 검색 중입니다... ({q_idx + 1}/{total_to_run})",
                )

            raw_planner_q = sq.query
            if re.search(r'(?:^|\s)["\']?from:\s*', raw_planner_q, re.IGNORECASE):
                logger.error(
                    f"🚨 [PLANNER_AUTHOR_OPERATOR_COLLISION] Query contains forbidden author operator: '{raw_planner_q}'"
                )
                raise ValueError(
                    f"Planner semantic query violates actor authority contract (contains 'from:'): '{raw_planner_q}'"
                )

            if resolved_actor_scope.kind == ActorScopeKind.SELF:
                executed_query_str = f"from:me {raw_planner_q}"
            else:
                executed_query_str = raw_planner_q

            executed_query = sq.model_copy(update={"query": executed_query_str}, deep=True)

            try:
                try:
                    items = await self.search_backend.search(executed_query, max_results=5)
                except TypeError:
                    items = await self.search_backend.search(executed_query)
            except WebViewSearchError as search_err:
                f_code = getattr(search_err, "failure_code", None)
                if f_code in EXPLICIT_QUERY_LOCAL_CODES:
                    logger.warning(
                        f"⚠️ [QUERY_LOCAL_SEARCH_ERROR] Contained query-local search failure (code={f_code}): {search_err}"
                    )
                    raw_query_records.append({
                        "query_index": q_idx,
                        "query_text": executed_query_str,
                        "purpose": sq.purpose,
                        "raw_returned_item_count": 0,
                        "raw_items": [],
                        "error": str(search_err),
                    })
                    continue
                logger.error(f"🚨 [FATAL_SEARCH_ERROR] Fatal search failure (code={f_code}): {search_err}")
                raise

            bounded_items = items[:5]
            total_queries_executed += 1
            round_queries_executed += 1

            # Observability capture from search_backend telemetry (Pure read-only side channel)
            backend_telemetry = getattr(self.search_backend, "last_search_telemetry", {}) or {}
            raw_evals = backend_telemetry.get("raw_items_evaluated", [])
            raw_query_records.append({
                "query_index": q_idx,
                "query_text": executed_query_str,
                "purpose": sq.purpose,
                "raw_returned_item_count": backend_telemetry.get("raw_items_count", len(items)),
                "valid_returned_item_count": len(bounded_items),
                "raw_items": raw_evals,
                "forensic_transition": backend_telemetry.get("forensic_transition", {}),
            })

            # SELF Author Integrity check
            if resolved_actor_scope.kind == ActorScopeKind.SELF and items:
                current_team = None
                if hasattr(self.search_backend, "get_active_team_id"):
                    current_team = self.search_backend.get_active_team_id()
                if current_team != active_team_id:
                    raise SelfAuthorConstraintIntegrityError(
                        f"Active team integrity failure for SELF scope: {current_team} != {active_team_id}"
                    )

                unique_author_ids: set[str] = set()
                for itm_idx, itm in enumerate(items):
                    a_id = getattr(itm, "author_id", None)
                    if not a_id or not re.match(r"^U[A-Z0-9]{8,14}$", a_id):
                        raise SelfAuthorConstraintIntegrityError(
                            f"SELF_AUTHOR_CONSTRAINT_INTEGRITY_FAILURE: Missing or malformed canonical author_id '{a_id}' in row #{itm_idx+1}"
                        )
                    unique_author_ids.add(a_id)

                if len(unique_author_ids) != 1:
                    raise SelfAuthorConstraintIntegrityError(
                        f"SELF_AUTHOR_CONSTRAINT_INTEGRITY_FAILURE: Multiple author IDs in single SELF query: {len(unique_author_ids)}"
                    )

                batch_author_id = unique_author_ids.pop()
                if investigation_local_self_id is None:
                    investigation_local_self_id = batch_author_id
                else:
                    if batch_author_id != investigation_local_self_id:
                        raise SelfAuthorConstraintIntegrityError(
                            f"SELF_AUTHOR_CONSTRAINT_INTEGRITY_FAILURE: Cross-query author ID mismatch: {batch_author_id} != {investigation_local_self_id}"
                        )

                for itm in items:
                    itm.author_relation = "SELF"

            # Record CandidateQueryHit before round_candidates.extend()
            for it in bounded_items:
                c_key = get_canonical_evidence_key(it)
                if c_key not in candidate_query_provenance:
                    candidate_query_provenance[c_key] = []
                candidate_query_provenance[c_key].append(
                    CandidateQueryHit(query_index=q_idx, search_query=executed_query)
                )

            round_candidates.extend(bounded_items)
            if len(round_candidates) > 15:
                round_candidates = round_candidates[:15]

        # Save Round Raw Result if inv_dir provided
        if inv_dir:
            round_data = {
                "round": round_number,
                "queries": [q.model_dump() for q in queries_to_execute[:round_queries_executed]],
                "candidates_found": len(round_candidates),
                "candidate_items": [it.model_dump() for it in round_candidates],
            }
            self._save_atomic_json(inv_dir / f"search_round_{round_number:02d}.json", round_data)

        all_candidates.extend(round_candidates)

        stop_reason: Optional[InvestigationStopReason] = None
        if round_number > 1 and not round_candidates:
            logger.info("ℹ️ [NO_NEW_EVIDENCE] Round > 1에서 신규 증거가 발견되지 않음")
            stop_reason = InvestigationStopReason.NO_NEW_EVIDENCE

        # Boundary Telemetry: post-conversion and post-dedupe
        pipeline_boundary_telemetry["raw_candidate_keys"] = [get_canonical_evidence_key(c) for c in all_candidates]
        seen_keys: set[str] = set()
        deduped_keys: list[str] = []
        for c in all_candidates:
            k = get_canonical_evidence_key(c)
            if k not in seen_keys:
                seen_keys.add(k)
                deduped_keys.append(k)
        pipeline_boundary_telemetry["post_dedupe_keys"] = deduped_keys

        # -------------------------------------------------------------
        # Step 2: Thread Context Expansion (Residency-First with Relocation)
        # -------------------------------------------------------------
        overall_thread_context_status: Optional[str] = ThreadContextStatus.NOT_ATTEMPTED.value
        expansion_telemetry: Optional[dict[str, Any]] = None

        if thread_expansions_done == 0 and all_candidates:
            prelim_pkg, _ = self.selector.select_and_package(
                candidates=all_candidates,
                plan=plan,
                package_id=f"prelim_{investigation_id}_r{round_number}",
                query_summary=question_text,
                temporal_scope=resolved_temporal_scope,
                actor_scope=resolved_actor_scope,
                candidate_query_provenance=candidate_query_provenance,
            )
            eligible_candidates = [
                it for it in prelim_pkg.evidence_items
                if is_eligible_thread_candidate(it)
            ]
            if not eligible_candidates:
                if resolved_temporal_scope.strictness == TemporalStrictness.STRICT:
                    if resolved_temporal_scope.status == TemporalResolutionStatus.RESOLVED:
                        eligible_candidates = [
                            c for c in all_candidates
                            if is_eligible_thread_candidate(c)
                            and (
                                (authored_dt := resolve_candidate_authored_datetime(c)) is not None
                                and resolved_temporal_scope.is_in_range(authored_dt)
                            )
                        ]
                else:
                    eligible_candidates = [
                        c for c in all_candidates if is_eligible_thread_candidate(c)
                    ]

            pipeline_boundary_telemetry["thread_eligibility"] = {
                get_canonical_evidence_key(c): is_eligible_thread_candidate(c)
                for c in all_candidates
            }

            if eligible_candidates:
                chosen = eligible_candidates[0]
                chosen_key = get_canonical_evidence_key(chosen)
                target_candidates = [
                    c for c in all_candidates
                    if get_canonical_evidence_key(c) == chosen_key
                ]
                target_candidate = target_candidates[0] if target_candidates else chosen
                logger.info(f"🧵 [THREAD_EXPANSION_INITIATED] Expanding thread for {chosen_key}")

                if on_progress:
                    on_progress("reading_thread", "관련 스레드 댓글 수집 중...")

                first_read_res: Optional[ThreadReadResult] = None
                second_read_res: Optional[ThreadReadResult] = None
                selected_hit: Optional[CandidateQueryHit] = None
                final_replies: list[ThreadReplyItem] = []
                final_status: str = ThreadContextStatus.NOT_ATTEMPTED.value

                pre_reloc_cand_count = len(all_candidates)
                pre_reloc_keys = [get_canonical_evidence_key(c) for c in all_candidates]
                pre_reloc_prov_counts = {k: len(v) for k, v in candidate_query_provenance.items()}

                try:
                    # Initial residency check
                    first_read_res = await self._read_candidate_thread(target_candidate, thread_reader_fn=thread_reader_fn)

                    if (
                        first_read_res.status == ThreadContextStatus.PARTIAL
                        or len(first_read_res.replies) > 0
                    ):
                        final_replies = list(first_read_res.replies)
                        final_status = (
                            first_read_res.status.value
                            if hasattr(first_read_res.status, "value")
                            else str(first_read_res.status)
                        )
                    elif (
                        first_read_res.failure_reason == ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM
                        and relocation_searches_performed == 0
                    ):
                        hits = candidate_query_provenance.get(chosen_key, [])
                        if hits:
                            selected_hit = max(hits, key=lambda h: h.query_index)
                            relocation_searches_performed += 1
                            logger.info(
                                f"🔄 [THREAD_RELOCATION_SEARCH] query_index={selected_hit.query_index} query='{selected_hit.search_query.query}'"
                            )
                            try:
                                try:
                                    _ = await self.search_backend.search(selected_hit.search_query, max_results=5)
                                except TypeError:
                                    _ = await self.search_backend.search(selected_hit.search_query)

                                second_read_res = await self._read_candidate_thread(target_candidate, thread_reader_fn=thread_reader_fn)
                                final_replies = list(second_read_res.replies)
                                final_status = (
                                    second_read_res.status.value
                                    if hasattr(second_read_res.status, "value")
                                    else str(second_read_res.status)
                                )
                            except Exception as rel_err:
                                logger.warning(f"⚠️ [THREAD_RELOCATION_SEARCH_FAILED] Relocation search failed: {rel_err}")
                                final_replies = []
                                final_status = ThreadContextStatus.UNAVAILABLE.value
                        else:
                            final_replies = list(first_read_res.replies)
                            final_status = (
                                first_read_res.status.value
                                if hasattr(first_read_res.status, "value")
                                else str(first_read_res.status)
                            )
                    else:
                        final_replies = list(first_read_res.replies)
                        final_status = (
                            first_read_res.status.value
                            if hasattr(first_read_res.status, "value")
                            else str(first_read_res.status)
                        )

                    # Temporal reply eligibility check
                    if (
                        resolved_temporal_scope.strictness == TemporalStrictness.STRICT
                        and resolved_temporal_scope.status == TemporalResolutionStatus.RESOLVED
                        and final_replies
                    ):
                        in_range_replies = []
                        for r in final_replies:
                            r_dt = resolve_candidate_authored_datetime(
                                EvidenceItem(
                                    evidence_id="tmp",
                                    source="slack",
                                    timestamp=r.message_ts or r.timestamp,
                                    permalink=r.permalink,
                                    text=r.text,
                                )
                            )
                            if r_dt is not None and resolved_temporal_scope.is_in_range(r_dt):
                                in_range_replies.append(r)
                        reindexed_replies = [
                            r.model_copy(update={"reply_id": f"R{r_idx + 1}"})
                            for r_idx, r in enumerate(in_range_replies)
                        ]
                        final_replies = reindexed_replies

                    final_read_res = second_read_res if (second_read_res is not None) else first_read_res
                    final_root = final_read_res.root if final_read_res else None

                    if final_root is not None:
                        for c in all_candidates:
                            if get_canonical_evidence_key(c) == chosen_key:
                                c.text = final_root.text
                                if final_root.author and not c.author:
                                    c.author = final_root.author
                                if final_root.author_id and not c.author_id:
                                    c.author_id = final_root.author_id
                                if final_root.timestamp and not c.timestamp:
                                    c.timestamp = final_root.timestamp
                                c.thread_replies = final_replies
                                c.thread_context_status = final_status
                        target_candidate.text = final_root.text
                        if final_root.author and not target_candidate.author:
                            target_candidate.author = final_root.author
                        if final_root.author_id and not target_candidate.author_id:
                            target_candidate.author_id = final_root.author_id
                        if final_root.timestamp and not target_candidate.timestamp:
                            target_candidate.timestamp = final_root.timestamp
                        target_candidate.thread_replies = final_replies
                        target_candidate.thread_context_status = final_status
                    else:
                        for c in all_candidates:
                            if get_canonical_evidence_key(c) == chosen_key:
                                c.thread_replies = final_replies
                                c.thread_context_status = final_status
                        target_candidate.thread_replies = final_replies
                        target_candidate.thread_context_status = final_status
                    overall_thread_context_status = final_status
                    thread_expansions_done = 1

                    post_reloc_cand_count = len(all_candidates)
                    post_reloc_keys = [get_canonical_evidence_key(c) for c in all_candidates]
                    post_reloc_prov_counts = {k: len(v) for k, v in candidate_query_provenance.items()}

                    reloc_added_to_evidence = (post_reloc_cand_count != pre_reloc_cand_count)
                    hit_count_changed = (post_reloc_prov_counts != pre_reloc_prov_counts)
                    evidence_order_changed = (post_reloc_keys != pre_reloc_keys)

                    expansion_telemetry = {
                        "selected_thread_candidate": getattr(target_candidate, "permalink", None) or getattr(target_candidate, "result_url", None) or getattr(target_candidate, "message_identifier", None) or chosen_key,
                        "selected_candidate_canonical_key": chosen_key,
                        "selected_candidate_query_hit_indices": [h.query_index for h in candidate_query_provenance.get(chosen_key, [])],
                        "selected_candidate_relocation_source_query_index": selected_hit.query_index if selected_hit else None,
                        "selected_candidate_relocation_source_query": selected_hit.search_query.query if selected_hit else None,
                        "first_thread_read_status": first_read_res.status.value if (first_read_res and hasattr(first_read_res.status, "value")) else (str(first_read_res.status) if first_read_res else None),
                        "first_thread_read_failure_reason": (first_read_res.failure_reason.value if (first_read_res and first_read_res.failure_reason) else None),
                        "first_resident_match_count": (getattr(first_read_res, "resident_match_count", 0) if first_read_res else 0),
                        "relocation_search_executed": relocation_searches_performed > prior_relocation_count,
                        "relocation_search_operations": relocation_searches_performed,
                        "relocation_source_query_index": selected_hit.query_index if selected_hit else None,
                        "relocation_source_query": selected_hit.search_query.query if selected_hit else None,
                        "relocation_used_stored_executed_snapshot": (relocation_searches_performed > prior_relocation_count and selected_hit is not None),
                        "relocation_reconstructed_query_from_text": False,
                        "relocation_invoked_planner": False,
                        "relocation_created_new_query": False,
                        "relocation_results_added_to_evidence": reloc_added_to_evidence,
                        "query_hit_count_changed_by_relocation": hit_count_changed,
                        "ranking_changed_by_relocation": evidence_order_changed,
                        "evidence_ids_changed_by_relocation": False,
                        "evidence_order_changed_by_relocation": evidence_order_changed,
                        "second_thread_read_status": (second_read_res.status.value if (second_read_res and hasattr(second_read_res.status, "value")) else (str(second_read_res.status) if second_read_res else None)),
                        "second_thread_read_failure_reason": (second_read_res.failure_reason.value if (second_read_res and second_read_res.failure_reason) else None),
                        "second_resident_match_count": (getattr(second_read_res, "resident_match_count", 0) if second_read_res else 0),
                        "thread_enrichment_workflows": 1,
                        "thread_reader_calls": (1 if first_read_res is not None else 0) + (1 if second_read_res is not None else 0),
                        "residency_checks": (1 if first_read_res is not None else 0) + (1 if second_read_res is not None else 0),
                        "final_replies_count": len(final_replies),
                        "final_status": final_status,
                        "root_captured": final_root is not None,
                        "root_body_characters": len(final_root.text) if final_root else 0,
                    }
                    self.last_thread_expansion_telemetry = expansion_telemetry
                    if inv_dir:
                        self._save_atomic_json(inv_dir / "thread_expansion_telemetry.json", expansion_telemetry)

                except Exception as thr_err:
                    logger.warning(f"⚠️ [THREAD_EXPANSION_FAILED] Thread expansion failed safely: {thr_err}")
                    for c in all_candidates:
                        if get_canonical_evidence_key(c) == chosen_key:
                            c.thread_context_status = ThreadContextStatus.UNAVAILABLE.value
                    target_candidate.thread_context_status = ThreadContextStatus.UNAVAILABLE.value
                    overall_thread_context_status = ThreadContextStatus.UNAVAILABLE.value
                    thread_expansions_done = 1

                    expansion_telemetry = {
                        "selected_thread_candidate": getattr(target_candidate, "permalink", None) or getattr(target_candidate, "result_url", None) or getattr(target_candidate, "message_identifier", None) or chosen_key,
                        "selected_candidate_canonical_key": chosen_key,
                        "selected_candidate_query_hit_indices": [h.query_index for h in candidate_query_provenance.get(chosen_key, [])],
                        "selected_candidate_relocation_source_query_index": selected_hit.query_index if selected_hit else None,
                        "selected_candidate_relocation_source_query": selected_hit.search_query.query if selected_hit else None,
                        "first_thread_read_status": first_read_res.status.value if (first_read_res and hasattr(first_read_res.status, "value")) else (str(first_read_res.status) if first_read_res else None),
                        "first_thread_read_failure_reason": (first_read_res.failure_reason.value if (first_read_res and first_read_res.failure_reason) else None),
                        "first_resident_match_count": (getattr(first_read_res, "resident_match_count", 0) if first_read_res else 0),
                        "relocation_search_executed": relocation_searches_performed > prior_relocation_count,
                        "relocation_search_operations": relocation_searches_performed,
                        "relocation_source_query_index": selected_hit.query_index if selected_hit else None,
                        "relocation_source_query": selected_hit.search_query.query if selected_hit else None,
                        "relocation_used_stored_executed_snapshot": (relocation_searches_performed > prior_relocation_count and selected_hit is not None),
                        "relocation_reconstructed_query_from_text": False,
                        "relocation_invoked_planner": False,
                        "relocation_created_new_query": False,
                        "relocation_results_added_to_evidence": False,
                        "query_hit_count_changed_by_relocation": False,
                        "ranking_changed_by_relocation": False,
                        "evidence_ids_changed_by_relocation": False,
                        "evidence_order_changed_by_relocation": False,
                        "second_thread_read_status": (second_read_res.status.value if (second_read_res and hasattr(second_read_res.status, "value")) else (str(second_read_res.status) if second_read_res else None)),
                        "second_thread_read_failure_reason": (second_read_res.failure_reason.value if (second_read_res and second_read_res.failure_reason) else None),
                        "second_resident_match_count": (getattr(second_read_res, "resident_match_count", 0) if second_read_res else 0),
                        "thread_enrichment_workflows": 1,
                        "thread_reader_calls": (1 if first_read_res is not None else 0) + (1 if second_read_res is not None else 0),
                        "residency_checks": (1 if first_read_res is not None else 0) + (1 if second_read_res is not None else 0),
                        "error": str(thr_err),
                    }
                    self.last_thread_expansion_telemetry = expansion_telemetry
                    if inv_dir:
                        self._save_atomic_json(inv_dir / "thread_expansion_telemetry.json", expansion_telemetry)

        pipeline_boundary_telemetry["thread_expansion_status"] = overall_thread_context_status
        pipeline_boundary_telemetry["relocation_attempted"] = relocation_searches_performed > 0
        pipeline_boundary_telemetry["relocation_result"] = expansion_telemetry

        # -------------------------------------------------------------
        # Step 3: Evidence Selection & Packaging
        # -------------------------------------------------------------
        pipeline_boundary_telemetry["selector_input_keys"] = [get_canonical_evidence_key(c) for c in all_candidates]
        anchor_terms = plan.anchor_terms if plan else []
        pipeline_boundary_telemetry["anchor_match_count_per_candidate"] = {
            get_canonical_evidence_key(c): compute_anchor_matches(c.text, anchor_terms)
            for c in all_candidates
        }

        evidence_package, sel_stats = self.selector.select_and_package(
            candidates=all_candidates,
            plan=plan,
            package_id=f"pkg_{investigation_id}_r{round_number}",
            query_summary=question_text,
            temporal_scope=resolved_temporal_scope,
            actor_scope=resolved_actor_scope,
            candidate_query_provenance=candidate_query_provenance,
        )
        evidence_package = evidence_package.model_copy(
            update={"thread_context_status": overall_thread_context_status}
        )

        pipeline_boundary_telemetry["selected_evidence_ids"] = [
            e.evidence_id for e in evidence_package.evidence_items
        ]
        pipeline_boundary_telemetry["selected_canonical_keys"] = [
            get_canonical_evidence_key(e) for e in evidence_package.evidence_items
        ]
        pipeline_boundary_telemetry["coverage"] = evidence_package.retrieval_coverage

        if inv_dir:
            self._save_atomic_json(inv_dir / f"evidence_package_round_{round_number:02d}.json", evidence_package)
            self._save_atomic_json(inv_dir / "evidence_package.json", evidence_package)
            # Save comprehensive diagnostics artifact
            diag_artifact = {
                "investigation_id": investigation_id,
                "raw_query_records": raw_query_records,
                "pipeline_boundary_telemetry": pipeline_boundary_telemetry,
                "selection_stats": sel_stats,
            }
            self._save_atomic_json(inv_dir / "retrieval_diagnostics.json", diag_artifact)

        return RetrievalCoordinatorResult(
            evidence_package=evidence_package,
            candidates=all_candidates,
            selected_candidates=list(evidence_package.evidence_items),
            candidate_query_provenance=candidate_query_provenance,
            plan=plan,
            temporal_scope=resolved_temporal_scope,
            actor_scope=resolved_actor_scope,
            thread_expansion_telemetry=expansion_telemetry,
            raw_query_records=raw_query_records,
            pipeline_boundary_telemetry=pipeline_boundary_telemetry,
            stop_reason=stop_reason,
            plan_usage=plan_usage,
            duplicates_removed=sel_stats["duplicates_removed"],
            queries_executed=total_queries_executed,
            relocation_search_count=relocation_searches_performed,
            thread_expansions_performed=thread_expansions_done,
            investigation_local_self_id=investigation_local_self_id,
        )
