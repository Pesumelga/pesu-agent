"""End-to-End Investigation Orchestrator with Multi-Round Discovery and Durable Persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Optional
import uuid

from pydantic import BaseModel, Field

from pesu_agent.investigation.analyzer import InvestigationAnalyzer
from pesu_agent.investigation.exceptions import InvestigationError
from pesu_agent.investigation.models import (
    AnalysisResult,
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    InvestigationStopReason,
    RetrievalCoverage,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
    UserQuestion,
    ResolvedTemporalScope,
    TemporalStrictness,
    TemporalResolutionStatus,
)
from pesu_agent.investigation.planner import InvestigationPlanner
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.investigation.selector import EvidenceSelector, get_canonical_evidence_key
from pesu_agent.search.temporal import (
    resolve_temporal_scope,
    resolve_candidate_authored_datetime,
)
from pesu_agent.search.actor import (
    ActorResolutionStatus,
    ActorScopeKind,
    ResolvedActorScope,
    resolve_actor_scope,
)
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
    is_eligible_thread_candidate,
)
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.llm.usage import LLMUsageTracker
from pesu_agent.llm.budgeted import BudgetedProvider
from pesu_agent.investigation.production_trace_coordinator import ProductionRealTraceCoordinator, ProductionTraceResult
from pesu_agent.search.webview_slack_search import (
    WebViewSearchError,
    WebViewSearchFailureCode,
)

logger = logging.getLogger(__name__)


from pesu_agent.investigation.exceptions import SelfAuthorConstraintIntegrityError
from pesu_agent.investigation.retrieval_coordinator import (
    CandidateQueryHit,
    DEFAULT_INVESTIGATIONS_DIR,
    OrchestratorConfig,
    ProductionInvestigationRetrievalCoordinator,
)

EXPLICIT_QUERY_LOCAL_CODES: frozenset[Any] = frozenset({
    WebViewSearchFailureCode.EMPTY_QUERY_INVALID,
    WebViewSearchFailureCode.EMPTY_QUERY_INVALID.value,
})


class InvestigationState(str, Enum):
    """조사 상태 머신 상태값."""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    SEARCHING = "SEARCHING"
    SELECTING_EVIDENCE = "SELECTING_EVIDENCE"
    ANALYZING = "ANALYZING"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class InvestigationAuditRecord(BaseModel):
    """민감정보가 제거된 구조화된 조사 실행 감사 기록."""

    investigation_id: str
    failure_code: Optional[str] = None
    question_summary: str
    state: InvestigationState
    stop_reason: InvestigationStopReason
    planner_model: str
    analyzer_model: str
    search_rounds: int
    queries_executed: int
    planned_query_count: int = 0
    relocation_search_count: int = 0
    total_slack_search_operations: int = 0
    candidate_count: int
    selected_evidence_count: int
    duplicates_removed: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    latency_ms: float
    created_at: str
    completed_at: str


class InvestigationResult(BaseModel):
    """Investigation 최종 산출물 봉투."""

    investigation_id: str
    question: UserQuestion
    plan: Optional[InvestigationPlan] = None
    evidence_package: Optional[EvidencePackage] = None
    analysis: Optional[AnalysisResult] = None
    audit: InvestigationAuditRecord
    state: InvestigationState
    stop_reason: InvestigationStopReason
    error_message: Optional[str] = None
    production_trace: Optional[ProductionTraceResult] = None
    failure_code: Optional[str] = None


class InvestigationOrchestrator:
    """전체 사실관계 조사를 총괄 지휘하는 오케스트레이터."""

    def __init__(
        self,
        provider: ModelProvider,
        search_backend: SearchBackend,
        config: Optional[OrchestratorConfig] = None,
        llm_config: Optional[LLMConfig] = None,
        trace_coordinator: Optional[ProductionRealTraceCoordinator] = None,
    ):
        self.provider = provider
        self.search_backend = search_backend
        self.config = config or OrchestratorConfig()
        self.llm_config = llm_config or LLMConfig()
        self.trace_coordinator = trace_coordinator
        
        self.planner = InvestigationPlanner(provider=provider, config=self.llm_config)
        self.analyzer = InvestigationAnalyzer(provider=provider, config=self.llm_config)
        self.selector = EvidenceSelector(max_evidence_items=self.config.max_evidence_items)
        self.retrieval_coordinator = ProductionInvestigationRetrievalCoordinator(
            search_backend=search_backend,
            selector=self.selector,
            planner=self.planner,
            config=self.config,
        )
        self.last_thread_expansion_telemetry: Optional[dict[str, Any]] = None

    async def _read_candidate_thread(
        self, candidate: EvidenceItem
    ) -> ThreadReadResult:
        """Reads mounted thread context for an eligible candidate, returning typed ThreadReadResult.

        Delegates to self.retrieval_coordinator._read_candidate_thread.
        """
        return await self.retrieval_coordinator._read_candidate_thread(candidate)

    def _get_investigation_dir(self, investigation_id: str) -> Path:
        inv_dir = self.config.base_output_dir / investigation_id
        inv_dir.mkdir(parents=True, exist_ok=True)
        return inv_dir

    def _save_atomic_json(self, target_path: Path, data: BaseModel | dict[str, Any]) -> None:
        """Atomic Write 방식으로 JSON 파일 저장."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        json_str = data.model_dump_json(indent=2) if isinstance(data, BaseModel) else json.dumps(data, indent=2, ensure_ascii=False)
        temp_fd, temp_file_path = tempfile.mkstemp(
            dir=str(target_path.parent), prefix=".tmp_inv_", suffix=".json"
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

    async def run_investigation(
        self,
        question: UserQuestion,
        investigation_id: Optional[str] = None,
        on_progress: Optional[Callable[[str, str], None]] = None,
        reference_time: Optional[datetime] = None,
    ) -> InvestigationResult:
        """UserQuestion에 대한 전체 조사를 수행합니다 (Multi-Round 지원)."""
        inv_id = investigation_id or str(uuid.uuid4())
        inv_dir = self._get_investigation_dir(inv_id)
        start_time_iso = datetime.now(timezone.utc).isoformat()
        
        # Single reference clock captured once per investigation (TEMPORAL_REFERENCE_TIME_CAPTURE_COUNT = 1)
        ref_dt = reference_time or self.config.reference_time
        resolved_temporal_scope = resolve_temporal_scope(
            text=question.question,
            reference_time=ref_dt,
            timezone_name=self.config.timezone,
        )
        logger.info(
            f"⏱️ [TEMPORAL_SCOPE_RESOLVED] intent={resolved_temporal_scope.intent_present} "
            f"strictness={resolved_temporal_scope.strictness.value} "
            f"status={resolved_temporal_scope.status.value} "
            f"kind={resolved_temporal_scope.resolution_kind.value} "
            f"interval=[{resolved_temporal_scope.start_at}, {resolved_temporal_scope.end_at})"
        )

        resolved_actor_scope = resolve_actor_scope(question.question)
        logger.info(
            f"👤 [ACTOR_SCOPE_RESOLVED] kind={resolved_actor_scope.kind.value} "
            f"status={resolved_actor_scope.status.value} "
            f"is_implicit={resolved_actor_scope.is_implicit} "
            f"expr='{resolved_actor_scope.source_expression}'"
        )

        if isinstance(self.provider, BudgetedProvider):
            self.provider.reset()
            usage_tracker = self.provider.usage_tracker
        else:
            usage_tracker = LLMUsageTracker()
        state = InvestigationState.CREATED
        stop_reason = InvestigationStopReason.EVIDENCE_SUFFICIENT

        plan: Optional[InvestigationPlan] = None
        analysis: Optional[AnalysisResult] = None
        production_trace: Optional[ProductionTraceResult] = None
        evidence_package: Optional[EvidencePackage] = None

        all_candidates: list[EvidenceItem] = []
        candidate_query_provenance: dict[str, list[CandidateQueryHit]] = {}
        relocation_searches_performed = 0
        total_queries_executed = 0
        current_round = 0
        total_duplicates_removed = 0
        thread_expansions_performed = 0
        overall_thread_context_status: Optional[str] = ThreadContextStatus.NOT_ATTEMPTED.value
        error_message: Optional[str] = None
        failure_code: Optional[str] = None

        logger.info(f"🚀 [INVESTIGATION_START] id={inv_id} question='{question.question[:50]}...'")

        try:
            # -------------------------------------------------------------
            # Stage 1: Planning
            # -------------------------------------------------------------
            state = InvestigationState.PLANNING
            if on_progress:
                on_progress("planning", "조사 계획 수립 중...")
            plan_path = inv_dir / "plan.json"

            if plan_path.exists():
                logger.info(f"[RESUME] Loading existing plan from {plan_path}")
                plan = InvestigationPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
            else:
                available_query_budget = min(
                    self.config.max_queries_per_round,
                    max(0, self.config.max_total_queries - total_queries_executed)
                )
                plan, plan_usage = await self.planner.plan(
                    question=question,
                    model=self.config.planner_model,
                    reasoning_effort=self.config.planner_reasoning_effort,
                    max_search_queries=available_query_budget,
                    search_target=self.config.search_target,
                )
                if not isinstance(self.provider, BudgetedProvider):
                    usage_tracker.record_call(plan_usage)
                self._save_atomic_json(plan_path, plan)

            # Check fail-closed for ambiguous / unresolved strict temporal intent (Phase 12.6d)
            if (
                resolved_temporal_scope.strictness == TemporalStrictness.STRICT
                and resolved_temporal_scope.status in (
                    TemporalResolutionStatus.AMBIGUOUS,
                    TemporalResolutionStatus.UNRESOLVED,
                )
            ):
                logger.warning(
                    f"⚠️ [STRICT_TEMPORAL_RESOLUTION_FAILED] Ambiguous/unresolved strict temporal scope "
                    f"({resolved_temporal_scope.status.value}). Terminating investigation fail-closed."
                )
                stop_reason = InvestigationStopReason.ANALYSIS_INCONCLUSIVE
                evidence_package = EvidencePackage(
                    package_id=f"pkg_{inv_id}_unresolved",
                    query=question.question,
                    total_items=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    is_synthetic=True,
                    evidence_items=[],
                    retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                    temporal_scope=resolved_temporal_scope,
                    actor_scope=resolved_actor_scope,
                )
                self._save_atomic_json(inv_dir / "evidence_package.json", evidence_package)
                analysis = AnalysisResult(
                    summary=f"시간 제약 조건('{resolved_temporal_scope.source_expression}')이 모호하거나 상충되어 조사를 진행할 수 없습니다.",
                    confirmed_facts=[],
                    disputed_points=[],
                    contradictions=[],
                    assessment="명확하지 않은 시계열 조건으로 인해 증거 수집이 안전하게 중단되었습니다.",
                    uncertainty="해석 불가능한 시계열 제약 조건",
                    missing_information=[],
                    suggested_followups=[],
                    conclusion_status="INCONCLUSIVE",
                    responsibility_determinable=False,
                    evidence_sufficiency="INSUFFICIENT",
                    cited_evidence_ids=[],
                )
                self._save_atomic_json(inv_dir / "analysis.json", analysis)
                state = InvestigationState.COMPLETED

            # Check fail-closed for ambiguous / unresolved actor intent (Phase 12.6g Mandatory Correction A)
            if (
                state != InvestigationState.COMPLETED
                and resolved_actor_scope.status in (
                    ActorResolutionStatus.AMBIGUOUS,
                    ActorResolutionStatus.UNRESOLVED,
                )
            ):
                logger.warning(
                    f"⚠️ [ACTOR_SCOPE_RESOLUTION_FAILED] Ambiguous/unresolved actor scope "
                    f"({resolved_actor_scope.status.value}, kind={resolved_actor_scope.kind.value}). "
                    f"Terminating investigation fail-closed."
                )
                stop_reason = InvestigationStopReason.ANALYSIS_INCONCLUSIVE
                evidence_package = EvidencePackage(
                    package_id=f"pkg_{inv_id}_actor_unresolved",
                    query=question.question,
                    total_items=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    is_synthetic=True,
                    evidence_items=[],
                    retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                    temporal_scope=resolved_temporal_scope,
                    actor_scope=resolved_actor_scope,
                )
                self._save_atomic_json(inv_dir / "evidence_package.json", evidence_package)
                analysis = AnalysisResult(
                    summary=f"요청하신 대상/액터 조건('{resolved_actor_scope.source_expression or '미지정'}')이 모호하거나 지원되지 않아 조사를 진행할 수 없습니다.",
                    confirmed_facts=[],
                    disputed_points=[],
                    contradictions=[],
                    assessment="명확하지 않거나 지원되지 않는 액터 조건으로 인해 증거 수집이 안전하게 중단되었습니다.",
                    uncertainty="해석 불가능한 액터 제약 조건",
                    missing_information=[],
                    suggested_followups=[],
                    conclusion_status="INCONCLUSIVE",
                    responsibility_determinable=False,
                    evidence_sufficiency="INSUFFICIENT",
                    cited_evidence_ids=[],
                )
                self._save_atomic_json(inv_dir / "analysis.json", analysis)
                state = InvestigationState.COMPLETED

            # Active team binding for SELF scope (Phase 12.6g Mandatory Correction F)
            active_team_id: Optional[str] = None
            if resolved_actor_scope.kind == ActorScopeKind.SELF and state != InvestigationState.COMPLETED:
                if hasattr(self.search_backend, "get_active_team_id"):
                    active_team_id = self.search_backend.get_active_team_id()
                if not active_team_id:
                    logger.warning(
                        "⚠️ [SELF_ACTIVE_TEAM_UNAVAILABLE] Active team could not be established for SELF investigation. "
                        "Terminating investigation fail-closed."
                    )
                    stop_reason = InvestigationStopReason.ANALYSIS_INCONCLUSIVE
                    evidence_package = EvidencePackage(
                        package_id=f"pkg_{inv_id}_team_unavailable",
                        query=question.question,
                        total_items=0,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        is_synthetic=True,
                        evidence_items=[],
                        retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                        temporal_scope=resolved_temporal_scope,
                        actor_scope=resolved_actor_scope,
                    )
                    self._save_atomic_json(inv_dir / "evidence_package.json", evidence_package)
                    analysis = AnalysisResult(
                        summary="활성 Slack 워크스페이스/팀 정보를 확인할 수 없어 본인(SELF) 조사를 안전하게 중단했습니다.",
                        confirmed_facts=[],
                        disputed_points=[],
                        contradictions=[],
                        assessment="활성 팀 식별 불가로 인한 fail-closed 중단.",
                        uncertainty="워크스페이스 바인딩 실패",
                        missing_information=[],
                        suggested_followups=[],
                        conclusion_status="INCONCLUSIVE",
                        responsibility_determinable=False,
                        evidence_sufficiency="INSUFFICIENT",
                        cited_evidence_ids=[],
                    )
                    self._save_atomic_json(inv_dir / "analysis.json", analysis)
                    state = InvestigationState.COMPLETED

            # Investigation-local self ID (Zero cross-investigation cache)
            investigation_local_self_id: Optional[str] = None

            # -------------------------------------------------------------
            # Stage 2: Multi-Round Search & Analysis Loop
            # -------------------------------------------------------------
            queries_to_execute = list(plan.search_queries)[:self.config.max_queries_per_round]

            while state != InvestigationState.COMPLETED and current_round < self.config.max_search_rounds:
                current_round += 1
                state = InvestigationState.SEARCHING
                if on_progress:
                    on_progress("searching_slack", f"Slack 메시지 검색 중... (라운드 {current_round})")
                logger.info(f"▶ [ROUND_{current_round}] Executing search queries via shared retrieval coordinator (count={len(queries_to_execute)})...")

                retrieval_res = await self.retrieval_coordinator.execute_plan(
                    plan=plan,
                    question_text=question.question,
                    resolved_temporal_scope=resolved_temporal_scope,
                    resolved_actor_scope=resolved_actor_scope,
                    investigation_id=inv_id,
                    inv_dir=inv_dir,
                    on_progress=on_progress,
                    round_number=current_round,
                    override_queries=queries_to_execute,
                    prior_candidates=all_candidates,
                    prior_provenance=candidate_query_provenance,
                    prior_relocation_count=relocation_searches_performed,
                    prior_queries_executed=total_queries_executed,
                    thread_expansions_performed=thread_expansions_performed,
                    thread_reader_fn=self._read_candidate_thread,
                    investigation_local_self_id=investigation_local_self_id,
                )

                all_candidates = retrieval_res.candidates
                candidate_query_provenance = retrieval_res.candidate_query_provenance
                relocation_searches_performed = retrieval_res.relocation_search_count
                total_queries_executed = retrieval_res.queries_executed
                total_duplicates_removed += retrieval_res.duplicates_removed
                thread_expansions_performed = retrieval_res.thread_expansions_performed
                investigation_local_self_id = retrieval_res.investigation_local_self_id
                evidence_package = retrieval_res.evidence_package
                if retrieval_res.thread_expansion_telemetry:
                    self.last_thread_expansion_telemetry = retrieval_res.thread_expansion_telemetry
                if retrieval_res.stop_reason:
                    stop_reason = retrieval_res.stop_reason

                # Analysis Execution
                state = InvestigationState.ANALYZING
                if on_progress:
                    on_progress("analyzing_evidence", "수집된 증거 분석 중...")
                if self.trace_coordinator is not None:
                    if not evidence_package.is_synthetic and not self.config.allow_real_slack_external_analysis:
                        raise InvestigationError("EXTERNAL_ANALYSIS_CONSENT_REQUIRED")
                    production_trace = await self.trace_coordinator.trace_and_synthesize_async(
                        question=question.question, candidates=evidence_package.evidence_items,
                        investigation_objective=plan.objective,
                        temporal_scope=resolved_temporal_scope, actor_scope=resolved_actor_scope,
                        candidate_query_provenance=candidate_query_provenance,
                    )
                    self._save_atomic_json(inv_dir / "production_trace.json", production_trace)
                    if not production_trace.success:
                        failure = production_trace.stop_reason
                        if "LLM_BUDGET_REACHED" in (production_trace.error_message or ""):
                            failure = "LLM_BUDGET_REACHED"
                        raise InvestigationError(failure)
                    conclusive = production_trace.stop_reason == "COMPLETED"
                    # This legacy envelope only controls continuation/audit. The UI
                    # renders the validated trace, never an additional freeform answer.
                    analysis = AnalysisResult(
                        summary="근거와 업무 상태 검증을 완료했습니다." if conclusive else "현재 근거만으로 업무 상태를 확정할 수 없습니다.",
                        assessment="", uncertainty="" if conclusive else production_trace.stop_reason,
                        conclusion_status="CONFIRMED" if conclusive else "INCONCLUSIVE",
                        evidence_sufficiency="SUFFICIENT" if conclusive else "INSUFFICIENT",
                    )
                else:
                    analysis, analysis_usage = await self.analyzer.analyze(
                        question=question,
                        package=evidence_package,
                        model=self.config.analyzer_model,
                        reasoning_effort=self.config.analyzer_reasoning_effort,
                        allow_real_slack_external_analysis=self.config.allow_real_slack_external_analysis,
                    )
                    if not isinstance(self.provider, BudgetedProvider):
                        usage_tracker.record_call(analysis_usage)
                self._save_atomic_json(inv_dir / "analysis.json", analysis)

                # Check Continuation (Iterative Loop Evaluation)
                is_inconclusive = (
                    analysis.conclusion_status == "INCONCLUSIVE"
                    or analysis.evidence_sufficiency == "INSUFFICIENT"
                    or (analysis.conclusion_status == "DISPUTED" and not analysis.responsibility_determinable)
                )

                if is_inconclusive and current_round < self.config.max_search_rounds:
                    # Check Budget Guards before starting next round
                    summary_now = usage_tracker.get_summary()
                    if summary_now["total_calls"] >= self.config.max_external_llm_calls:
                        stop_reason = InvestigationStopReason.LLM_BUDGET_REACHED
                        break
                    if summary_now["total_cost_usd"] >= self.config.max_cost_usd:
                        stop_reason = InvestigationStopReason.LLM_BUDGET_REACHED
                        break
                    if total_queries_executed >= self.config.max_total_queries:
                        stop_reason = InvestigationStopReason.QUERY_BUDGET_REACHED
                        break

                    state = InvestigationState.NEED_MORE_EVIDENCE
                    logger.info(f"🔄 [NEED_MORE_EVIDENCE] Round {current_round} 결론 불충분 ➔ 후속 라운드 질의 생성")

                    # Formulate Follow-up queries from suggested_followups & missing_information
                    followup_queries: list[SearchQuery] = []
                    for fu in (analysis.suggested_followups + analysis.missing_information)[:self.config.max_queries_per_round]:
                        fu_clean = fu.replace("확인한다.", "").replace("확인하십시오.", "").strip()
                        if len(fu_clean) >= 2:
                            followup_queries.append(SearchQuery(query=fu_clean[:80], purpose=f"추가 증거 보강: {fu_clean[:40]}"))

                    if not followup_queries:
                        stop_reason = InvestigationStopReason.NO_NEW_EVIDENCE
                        break

                    queries_to_execute = followup_queries
                    continue
                else:
                    # Completed or reached max rounds
                    if is_inconclusive:
                        stop_reason = InvestigationStopReason.ANALYSIS_INCONCLUSIVE
                    else:
                        stop_reason = InvestigationStopReason.EVIDENCE_SUFFICIENT
                    break

            state = InvestigationState.COMPLETED

        except Exception as err:
            logger.error(f"🚨 [INVESTIGATION_ERROR] {err}", exc_info=True)
            state = InvestigationState.ERROR
            stop_reason = InvestigationStopReason.ERROR
            error_message = str(err)
            code = getattr(err, "failure_code", None)
            failure_code = getattr(code, "value", code)
            if failure_code is None and error_message in {
                "LLM_BUDGET_REACHED", "PROVIDER_EXECUTION_FAILURE", "INVALID_PROVIDER_OUTPUT",
                "TRACEABILITY_FAILURE", "EXTERNAL_ANALYSIS_CONSENT_REQUIRED",
            }:
                failure_code = error_message

        # -------------------------------------------------------------
        # Stage 3: Audit Trail & Final Packaging
        # -------------------------------------------------------------
        end_time_iso = datetime.now(timezone.utc).isoformat()
        usage_summary = usage_tracker.get_summary()

        logger.info(
            f"📊 [SEARCH_OPERATIONS_TELEMETRY] "
            f"PLANNED_QUERY_COUNT={total_queries_executed} "
            f"RELOCATION_SEARCH_COUNT={relocation_searches_performed} "
            f"TOTAL_SLACK_SEARCH_OPERATIONS={total_queries_executed + relocation_searches_performed}"
        )

        audit = InvestigationAuditRecord(
            investigation_id=inv_id,
            failure_code=failure_code,
            question_summary=question.question[:100],
            state=state,
            stop_reason=stop_reason,
            planner_model=self.config.planner_model,
            analyzer_model=self.config.analyzer_model,
            search_rounds=current_round,
            queries_executed=total_queries_executed,
            planned_query_count=total_queries_executed,
            relocation_search_count=relocation_searches_performed,
            total_slack_search_operations=total_queries_executed + relocation_searches_performed,
            candidate_count=len(all_candidates),
            selected_evidence_count=len(evidence_package.evidence_items) if evidence_package else 0,
            duplicates_removed=total_duplicates_removed,
            llm_calls=self.provider.attempt_count if isinstance(self.provider, BudgetedProvider) else usage_summary["total_calls"],
            input_tokens=usage_summary["total_input_tokens"],
            output_tokens=usage_summary["total_output_tokens"],
            total_cost_usd=usage_summary["total_cost_usd"],
            latency_ms=usage_tracker.total_latency_ms,
            created_at=start_time_iso,
            completed_at=end_time_iso,
        )
        self._save_atomic_json(inv_dir / "audit.json", audit)

        logger.info(
            f"🏁 [INVESTIGATION_DONE] id={inv_id} state={state.value} "
            f"stop_reason={stop_reason.value} rounds={current_round} "
            f"calls={audit.llm_calls} cost=${audit.total_cost_usd:.6f}"
        )

        if on_progress:
            on_progress("completed", "조사 완료")

        return InvestigationResult(
            investigation_id=inv_id,
            question=question,
            plan=plan,
            evidence_package=evidence_package,
            analysis=analysis,
            audit=audit,
            state=state,
            stop_reason=stop_reason,
            error_message=error_message,
            production_trace=production_trace,
            failure_code=failure_code,
        )
