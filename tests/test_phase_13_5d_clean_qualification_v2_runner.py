"""Phase 13.5D Task 11 — Clean Qualification v2 Offline Tests.

Validates all 4 contracts requested by the user and all offline freeze requirements:
1. Physical separation of cases.json and gold.json (zero ground truth leak to production).
2. Q2-02 evaluator-only branch candidate identities ("정용기 세무사", "박은혜 세무사").
3. Canonical WorkItemStatus and WorkItemLifecycleSignal enums with multi-WorkItem separation.
4. Structured-output-centric deterministic evaluation (no freeform LLM judge).
5. Strict search safety telemetry contracts (R10H/R10J).
6. Mechanical self-contamination evaluation.
7. Provider schema-400 recurrence detection.
8. Offline dry-run execution with zero live Slack reads and zero provider calls.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict
import pytest

from pesu_agent.investigation.graph_models import (
    WorkItemStatus,
    WorkItemLifecycleSignal,
)
from pesu_agent.investigation.models import EvidenceItem
from scripts.qualify_phase_13_5d_clean_v2 import (
    CASES_FIXTURE_PATH,
    GOLD_FIXTURE_PATH,
    FROZEN_CASE_ORDER,
    HISTORICAL_PROHIBITED_CASES,
    REQUIRED_TELEMETRY_FIELDS,
    CleanPredeclarationLoader,
    CleanQualificationGoldLoader,
    CleanQualificationEvaluator,
    SearchSafetyTracker,
    build_production_investigation_plan,
    evaluate_self_contamination,
    run_clean_qualification_v2,
)


# =============================================================================
# 1. Contract 1: Physical Separation & Leak Prevention
# =============================================================================

def test_physical_separation_of_cases_and_gold():
    """cases.json and gold.json must be physically distinct files with zero leak in cases.json."""
    assert CASES_FIXTURE_PATH.exists()
    assert GOLD_FIXTURE_PATH.exists()

    with open(CASES_FIXTURE_PATH, "r", encoding="utf-8") as f:
        cases_data = json.load(f)

    with open(GOLD_FIXTURE_PATH, "r", encoding="utf-8") as f:
        gold_data = json.load(f)

    # cases.json must NOT contain any ground truth structures
    assert "independent_ground_truth" not in cases_data
    assert "authoritative_pre_state" not in cases_data

    cases_json_str = json.dumps(cases_data, ensure_ascii=False)
    # Candidate identities must NOT be in cases.json
    assert "정용기" not in cases_json_str
    assert "박은혜" not in cases_json_str
    # Expected answers / states must NOT be in cases.json
    assert "expected_workitem_status" not in cases_json_str
    assert "expected_lifecycle_signal" not in cases_json_str
    assert "acceptance_contract" not in cases_json_str

    # gold.json MUST contain the evaluator-only identities and contracts
    assert "authoritative_pre_state" in gold_data
    q2_02_gold = gold_data["cases"]["Q2-02"]
    identities = q2_02_gold["independent_ground_truth"]["evaluator_only_target_identities"]
    assert "정용기 세무사" in identities
    assert "박은혜 세무사" in identities


def test_production_investigation_plan_has_zero_gold_leakage():
    """InvestigationPlan created for production pipeline contains zero gold data."""
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    for case_spec in cases_bundle["cases"]:
        plan, actor_scope, temporal_scope = build_production_investigation_plan(case_spec)
        # Verify plan contains questions and search queries
        assert len(plan.questions_to_resolve) == 1
        assert len(plan.search_queries) == 3

        plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan.__dict__
        plan_str = json.dumps(plan_dict, ensure_ascii=False, default=str)

        # Invariant: No ground truth facts leaked
        assert "정용기" not in plan_str
        assert "박은혜" not in plan_str
        assert "expected_workitem_status" not in plan_str
        assert "acceptance_contract" not in plan_str


# =============================================================================
# 2. Case Integrity, Order, and Historical Case Exclusion
# =============================================================================

def test_predeclarations_integrity_and_exact_counts():
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    cases = cases_bundle["cases"]
    assert len(cases) == 3
    assert [c["case_id"] for c in cases] == FROZEN_CASE_ORDER
    assert cases_bundle["execution_order"] == FROZEN_CASE_ORDER

    expected_questions = {
        "Q2-01": "현재 제휴 업무 담당자는 누구이며, 기존 제휴처에 대해 지금 어떤 업무를 진행하고 있어?",
        "Q2-02": "현재 지점 개설이 진행 중인 후보는 몇 곳이며, Slack에서 확인되는 각 후보의 현재 진행 단계는 어디까지야?",
        "Q2-03": "현재 지점 정산 업무는 누가 담당하고 있으며, 앞으로 지점 정산은 어떤 방식으로 진행하기로 최종 결정되었어?",
    }

    expected_queries = {
        "Q2-01": [
            "제휴 담당자 김춘구 제휴 업무",
            "Office 제휴 관리 제휴처 관계 확인 매뉴얼",
            "제휴처 컨택 제휴 관계 확인",
        ],
        "Q2-02": [
            "지점 개설 계약서 출자좌수 양수도 계약서 보안서약서",
            "지점 개설 김영선 검수 계약서",
            "지점 개설 일정 계약 서약서",
        ],
        "Q2-03": [
            "지점 정산 손익계산서 업로드",
            "지점 정산 공동인증서 손익계산서",
            "지점 정산 슈퍼바이저 정산 방식",
        ],
    }

    for c in cases:
        cid = c["case_id"]
        assert c["question"] == expected_questions[cid]
        assert c["max_results_per_query"] == 5
        queries = [q["compiled_query"] for q in c["exact_slack_queries"]]
        assert queries == expected_queries[cid]


def test_historical_cases_permanently_rejected():
    for hist_case in HISTORICAL_PROHIBITED_CASES:
        assert hist_case not in FROZEN_CASE_ORDER


# =============================================================================
# 3. Contract 3: Canonical Enums & Multi-WorkItem Separation
# =============================================================================

def test_canonical_enums_used_in_gold():
    gold = CleanQualificationGoldLoader.load_gold_bundle()
    valid_statuses = {s.value for s in WorkItemStatus}
    valid_signals = {s.value for s in WorkItemLifecycleSignal}

    # Q2-01
    q1_status = gold["cases"]["Q2-01"]["expected_semantic_outcome"]["expected_workitem_status"]
    q1_signal = gold["cases"]["Q2-01"]["expected_semantic_outcome"]["expected_lifecycle_signal"]
    assert q1_status in valid_statuses
    assert q1_status == WorkItemStatus.IN_PROGRESS.value
    assert q1_signal in valid_signals
    assert q1_signal == WorkItemLifecycleSignal.PROGRESS.value

    # Q2-02
    q2_status = gold["cases"]["Q2-02"]["expected_semantic_outcome"]["expected_workitem_status"]
    q2_signal = gold["cases"]["Q2-02"]["expected_semantic_outcome"]["expected_lifecycle_signal"]
    assert q2_status == WorkItemStatus.IN_PROGRESS.value
    assert q2_signal == WorkItemLifecycleSignal.PROGRESS.value

    # Q2-03 Multi-WorkItem
    decision_wi = gold["cases"]["Q2-03"]["expected_semantic_outcome"]["decision_workitem"]
    operational_wi = gold["cases"]["Q2-03"]["expected_semantic_outcome"]["operational_workitem"]
    assert decision_wi["expected_workitem_status"] == WorkItemStatus.COMPLETED.value
    assert decision_wi["expected_lifecycle_signal"] == WorkItemLifecycleSignal.COMPLETION.value
    assert operational_wi["expected_workitem_status"] == WorkItemStatus.IN_PROGRESS.value
    assert operational_wi["expected_lifecycle_signal"] == WorkItemLifecycleSignal.PROGRESS.value


# =============================================================================
# 4. Search Safety Telemetry Contracts (R10H / R10J)
# =============================================================================

def _build_valid_telemetry(query: str = "test query") -> dict[str, Any]:
    return {
        "slackbot_action_invoked_by_pesu": False,
        "write_capable_action_invoked_by_pesu": False,
        "unsafe_non_search_action_invoked_by_pesu": False,
        "generic_default_onenter_used": False,
        "safe_search_action_selected": True,
        "submit_method": "SafeSearchSuggestionDOMItem.click[index=0]",
        "default_suggestion_type": "SAFE_KEYWORD_SEARCH",
        "safe_candidates_count": 1,
        "SEARCH_RESULT_READINESS": "RESULTS_READY",
        "forensic_transition": {
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
            "SEARCH_SURFACE_QUERY_AFTER": f"다음에 대한 결과: {query}",
        },
    }


def test_safety_tracker_passes_valid_telemetry():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry("제휴 담당자 김춘구 제휴 업무")
    tracker.record_query_execution("제휴 담당자 김춘구 제휴 업무", 3, telem, 0.8)
    assert tracker.total_queries == 1
    assert not tracker.safety_regression_confirmed


@pytest.mark.parametrize("missing_field", REQUIRED_TELEMETRY_FIELDS)
def test_safety_tracker_fails_on_missing_field(missing_field: str):
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    del telem[missing_field]
    with pytest.raises(RuntimeError, match="SAFETY_TELEMETRY_INCOMPLETE"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_on_surface_mismatch_with_results_ready():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["forensic_transition"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = False
    with pytest.raises(RuntimeError, match="SURFACE_QUERY_INVARIANT_VIOLATION"):
        tracker.record_query_execution("query", 5, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_on_slackbot_invocation():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["slackbot_action_invoked_by_pesu"] = True
    with pytest.raises(RuntimeError, match="SAFETY_REGRESSION_CONFIRMED"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


# =============================================================================
# 5. Contamination Evaluator Contracts
# =============================================================================

def test_contamination_selected_self_generated_echo_invalidates():
    cand = EvidenceItem(
        evidence_id="e_contam_1",
        source="slack_channel",
        query_source="제휴 담당자 김춘구 제휴 업무",
        permalink="https://workspace.slack.com/archives/D07RA9YDE8G/p12345",
        text="제휴 담당자 김춘구 제휴 업무",
        author="USLACKBOT",
    )
    report = evaluate_self_contamination(
        raw_query_records=[],
        selected_candidates=[cand],
        target_queries={"제휴 담당자 김춘구 제휴 업무"},
    )
    assert report["SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 1
    assert report["CASE_VALIDITY"] == "INVALIDATED_BY_PREEXISTING_SELF_CONTAMINATION"


def test_contamination_clean_evidence_passes():
    cand = EvidenceItem(
        evidence_id="e_clean_1",
        source="slack_channel",
        query_source="제휴 담당자 김춘구 제휴 업무",
        permalink="https://workspace.slack.com/archives/C01234567/p12345",
        text="제휴 업무는 김춘구 님이 담당하고 있습니다.",
        author="김영선",
    )
    report = evaluate_self_contamination(
        raw_query_records=[],
        selected_candidates=[cand],
        target_queries={"제휴 담당자 김춘구 제휴 업무"},
    )
    assert report["SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert report["CASE_VALIDITY"] == "VALID"


# =============================================================================
# 6. Contract 4: Deterministic Structured Output Evaluator Tests
# =============================================================================

def test_q2_01_evaluator_no_action_inference_rule():
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-01"]

    # Prohibited: Claiming no action was taken without direct evidence
    checks_bad = evaluator._evaluate_q2_01(
        gold=gold,
        workitems=[{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="김춘구 님이 담당자이지만 기존 제휴처에 대해 아무런 업무도 하지 않고 있습니다.",
        claims=[],
        evidence=[],
    )
    assert checks_bad["NO_UNSUPPORTED_NO_ACTION_INFERENCE"] is False

    # Allowed: Grounded fact stating current partnership operation
    checks_good = evaluator._evaluate_q2_01(
        gold=gold,
        workitems=[{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="현재 제휴 담당자는 김춘구 님이며, Office 제휴 관리 시스템 도입을 위해 기존 제휴처에 대한 관계 확인 및 매뉴얼 전달 업무를 진행 중입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_good["NO_UNSUPPORTED_NO_ACTION_INFERENCE"] is True
    assert checks_good["RESPONSIBLE_PERSON_CORRECT"] is True
    assert checks_good["CURRENT_WORK_STATE_CORRECT"] is True


def test_q2_02_evaluator_branch_identities_and_unsupported_signature_sent():
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-02"]

    # Fails if candidates not identified
    checks_missing_identities = evaluator._evaluate_q2_02(
        gold=gold,
        workitems=[{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="현재 지점 개설 진행 중인 곳은 2곳입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_missing_identities["BOTH_ACTIVE_OPENINGS_REPRESENTED"] is False

    # Fails if unauthorized overclaim of signature or sending
    checks_overclaim = evaluator._evaluate_q2_02(
        gold=gold,
        workitems=[{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="정용기 세무사와 박은혜 세무사 2곳 후보이며, 계약서 서명 완료 및 문서 발송 완료 단계입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_overclaim["UNSUPPORTED_SIGNATURE_CLAIM"] is True
    assert checks_overclaim["UNSUPPORTED_DOCUMENT_SENT_CLAIM"] is True
    assert checks_overclaim["GROUNDING_DISCIPLINE"] is False

    # Passes with correct identities, progress stage, and no overclaim
    checks_valid = evaluator._evaluate_q2_02(
        gold=gold,
        workitems=[{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="현재 지점 개설 진행 중인 후보는 정용기 세무사와 박은혜 세무사 2곳이며, 계약서 및 보안서약서 작성과 검수 절차가 진행 중입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_valid["BOTH_ACTIVE_OPENINGS_REPRESENTED"] is True
    assert checks_valid["ACTIVE_BRANCH_OPENING_COUNT_CORRECT"] is True
    assert checks_valid["CURRENT_WORK_STATE_CORRECT"] is True
    assert checks_valid["UNSUPPORTED_SIGNATURE_CLAIM"] is False
    assert checks_valid["UNSUPPORTED_DOCUMENT_SENT_CLAIM"] is False
    assert checks_valid["GROUNDING_DISCIPLINE"] is True


def test_q2_03_evaluator_superseded_option_rule():
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-03"]

    # Fails if superseded option (공동인증서) presented as final
    checks_superseded = evaluator._evaluate_q2_03(
        gold=gold,
        workitems=[{"status": "COMPLETED", "lifecycle_signal": "COMPLETION"}],
        answer="지점운영팀 슈퍼바이저가 담당하며, 최종 결정된 방식은 공동인증서를 통한 정산 방식입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_superseded["SUPERSEDED_OPTION_PRESENTED_AS_FINAL"] is True
    assert checks_superseded["GROUNDING_DISCIPLINE"] is False

    # Passes if finalized method is 손익계산서 업로드
    checks_valid = evaluator._evaluate_q2_03(
        gold=gold,
        workitems=[{"status": "COMPLETED", "lifecycle_signal": "COMPLETION"}],
        answer="지점 정산 업무는 지점운영팀 슈퍼바이저가 담당하며, 앞으로 지점에서 손익계산서를 업로드하여 정산하는 방식으로 최종 결정되었습니다.",
        claims=[],
        evidence=[],
    )
    assert checks_valid["RESPONSIBLE_ROLE_CORRECT"] is True
    assert checks_valid["FINAL_SETTLEMENT_METHOD_CORRECT"] is True
    assert checks_valid["DECISION_WORKITEM_STATE_CORRECT"] is True
    assert checks_valid["SUPERSEDED_OPTION_PRESENTED_AS_FINAL"] is False
    assert checks_valid["GROUNDING_DISCIPLINE"] is True


# =============================================================================
# 7. Provider 400 Schema Error Detection
# =============================================================================

def test_provider_schema_400_recurrence_fails_qualification():
    evaluator = CleanQualificationEvaluator()
    prod_res = {
        "case_id": "Q2-01",
        "question": "question",
        "workitems": [{"status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        "grounded_answer": "현재 제휴 담당자는 김춘구 님이며 매뉴얼 전달 중입니다.",
        "claims": [],
        "evidence": [],
    }
    contam_report = {"CASE_VALIDITY": "VALID"}
    tracker = SearchSafetyTracker()

    scorecard = evaluator.evaluate_case(
        case_id="Q2-01",
        production_result=prod_res,
        contamination_report=contam_report,
        safety_tracker=tracker,
        provider_telemetry={"schema_400_recurrence": True, "fallback_count": 1},
    )
    assert scorecard["overall_pass"] is False
    assert scorecard.get("failure_reason") == "PROVIDER_SCHEMA_400_RECURRENCE"


# =============================================================================
# 8. Dry-Run Offline Execution & Safeguards
# =============================================================================

def test_live_execution_safeguard_blocks_without_authorization():
    """Attempting live qualification without authorization flag fails immediately."""
    exit_code = asyncio.run(run_clean_qualification_v2(is_dry_run=False, authorized_live=False))
    assert exit_code == 1


def test_dry_run_offline_execution_pure():
    """Offline dry-run executes purely with SyntheticSearchBackend and zero live network."""
    exit_code = asyncio.run(run_clean_qualification_v2(is_dry_run=True, authorized_live=False))
    assert exit_code == 0


# =============================================================================
# 9. Real Production Coordinator & Boundary Interface Contract Tests
# =============================================================================

def test_real_trace_coordinator_api_boundary_contract():
    """Authoritative check: ProductionRealTraceCoordinator exposes trace_and_synthesize_async and NOT execute_trace."""
    from pesu_agent.investigation.production_trace_coordinator import (
        ProductionRealTraceCoordinator,
        ProductionTraceResult,
    )
    import inspect
    from scripts.qualify_phase_13_5d_clean_v2 import execute_case_production

    # 1. Real coordinator interface invariant
    assert hasattr(ProductionRealTraceCoordinator, "trace_and_synthesize_async")
    assert not hasattr(ProductionRealTraceCoordinator, "execute_trace")

    # 2. ProductionTraceResult attributes invariant
    assert "work_items" in ProductionTraceResult.model_fields
    assert "traceability_chains" in ProductionTraceResult.model_fields
    assert "workitems" not in ProductionTraceResult.model_fields
    assert "claims" not in ProductionTraceResult.model_fields

    # 3. Runner code must NOT invoke the non-existent execute_trace method
    runner_src = inspect.getsource(execute_case_production)
    assert "execute_trace" not in runner_src, (
        "Runner calls non-existent 'execute_trace' instead of real 'trace_and_synthesize_async'!"
    )


def test_execute_case_production_against_real_coordinator_spec():
    """Ensures execute_case_production executes cleanly without AttributeError against real ProductionRealTraceCoordinator spec."""
    import asyncio
    from unittest.mock import AsyncMock, create_autospec
    from pesu_agent.investigation.production_trace_coordinator import (
        ProductionRealTraceCoordinator,
        ProductionTraceResult,
    )
    from pesu_agent.investigation.graph_models import (
        InvestigationStateGraph,
        WorkItemNode,
        WorkItemStatus,
        WorkItemLifecycleSignal,
    )
    from pesu_agent.investigation.workitem_synthesis import (
        WorkItemTraceabilityChain,
        ClaimTraceabilityRecord,
    )
    from pesu_agent.investigation.retrieval_coordinator import (
        ProductionInvestigationRetrievalCoordinator,
        RetrievalCoordinatorResult,
    )
    from pesu_agent.investigation.models import (
        EvidenceItem,
        EvidencePackage,
        InvestigationPlan,
        ResolvedActorScope,
        ResolvedTemporalScope,
        SearchQuery,
    )
    from scripts.qualify_phase_13_5d_clean_v2 import (
        execute_case_production,
        SearchSafetyTracker,
        CleanPredeclarationLoader,
    )

    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    case_spec = cases_bundle["cases"][0]

    # Create real-spec mock for trace coordinator
    mock_trace_coord = create_autospec(ProductionRealTraceCoordinator, instance=True)

    test_ev = EvidenceItem(
        evidence_id="ev_01",
        text="김춘구 님이 기존 제휴처에 매뉴얼 전달 업무를 진행 중이다.",
        source="slack",
        permalink="https://slack.com/archives/C1/p1",
    )

    # Real graph with valid nodes and links
    real_graph = InvestigationStateGraph(question=case_spec["question"])
    test_claim_trace = ClaimTraceabilityRecord(
        claim_id="claim_01",
        statement="김춘구 님이 기존 제휴처에 매뉴얼 전달 업무를 진행 중이다.",
        evidence_ids=["ev_01"],
        lifecycle_signal=WorkItemLifecycleSignal.PROGRESS,
    )
    test_chain = WorkItemTraceabilityChain(
        work_item_id="wi_01",
        title="제휴 업무 진행",
        final_status=WorkItemStatus.IN_PROGRESS,
        governing_claim_ids=["claim_01"],
        claim_traces=[test_claim_trace],
        all_evidence_ids=["ev_01"],
        is_traceable=True,
    )
    test_wi_node = WorkItemNode(
        work_item_id="wi_01",
        title="제휴 업무 진행",
        status=WorkItemStatus.IN_PROGRESS,
        claim_ids=["claim_01"],
        evidence_ids=["ev_01"],
    )

    mock_trace_result = ProductionTraceResult(
        success=True,
        question=case_spec["question"],
        graph=real_graph,
        work_items={"wi_01": test_wi_node},
        traceability_chains={"wi_01": test_chain},
    )

    mock_trace_coord.trace_and_synthesize_async = AsyncMock(return_value=mock_trace_result)

    # Mock retrieval coordinator returning valid RetrievalCoordinatorResult
    mock_retrieval_coord = create_autospec(ProductionInvestigationRetrievalCoordinator, instance=True)
    mock_retrieval_res = RetrievalCoordinatorResult(
        evidence_package=EvidencePackage(
            package_id="pkg_test",
            query=case_spec["question"],
            total_items=1,
            created_at="2026-09-17T00:00:00Z",
            is_synthetic=False,
            evidence_items=[test_ev],
            temporal_scope=ResolvedTemporalScope(),
            actor_scope=ResolvedActorScope(),
        ),
        candidates=[test_ev],
        selected_candidates=[test_ev],
        candidate_query_provenance={},
        plan=InvestigationPlan(
            objective="test",
            search_queries=[SearchQuery(query="test", purpose="test")],
            questions_to_resolve=[case_spec["question"]],
            anchor_terms=[],
        ),
        temporal_scope=ResolvedTemporalScope(),
        actor_scope=ResolvedActorScope(),
        raw_query_records=[{"query": "test", "raw_items": []}],
    )
    mock_retrieval_coord.retrieve_precomputed = AsyncMock(return_value=mock_retrieval_res)

    async def _run():
        return await execute_case_production(
            case_spec=case_spec,
            search_backend=None,
            trace_coordinator=mock_trace_coord,
            retrieval_coordinator=mock_retrieval_coord,
            thread_reader=None,
            safety_tracker=SearchSafetyTracker(),
            is_dry_run=False,
        )

    prod_res = asyncio.run(_run())

    assert prod_res is not None
    assert "workitems" in prod_res
    assert len(prod_res["workitems"]) == 1
    assert prod_res["workitems"][0]["status"] == "IN_PROGRESS"
    assert "raw_query_records" in prod_res

    # Also verify that Evaluator can consume this production_result cleanly
    evaluator = CleanQualificationEvaluator()
    scorecard = evaluator.evaluate_case(
        case_id="Q2-01",
        production_result=prod_res,
        contamination_report={"CASE_VALIDITY": "VALID"},
        safety_tracker=SearchSafetyTracker(),
        provider_telemetry={"schema_400_recurrence": False, "fallback_count": 0},
    )
    assert scorecard["traceability"]["complete"] is True
    assert scorecard["traceability"]["every_final_state_has_traceable_claim_evidence"] is True


def test_raw_query_records_missing_fails_closed():
    """Fail-closed contract: missing or None raw_query_records on RetrievalCoordinatorResult MUST raise and cannot silently become []."""
    import asyncio
    from unittest.mock import AsyncMock, create_autospec, MagicMock
    from pesu_agent.investigation.retrieval_coordinator import ProductionInvestigationRetrievalCoordinator
    from scripts.qualify_phase_13_5d_clean_v2 import (
        execute_case_production,
        SearchSafetyTracker,
        CleanPredeclarationLoader,
    )

    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    case_spec = cases_bundle["cases"][0]

    mock_retrieval_coord = create_autospec(ProductionInvestigationRetrievalCoordinator, instance=True)

    # 1. Test missing attribute on retrieval result
    bad_result_no_attr = MagicMock(spec=[
        "evidence_package", "candidates", "selected_candidates",
        "candidate_query_provenance", "plan", "temporal_scope", "actor_scope"
    ])
    bad_result_no_attr.selected_candidates = []
    mock_retrieval_coord.retrieve_precomputed = AsyncMock(return_value=bad_result_no_attr)

    with pytest.raises(AttributeError, match="missing canonical 'raw_query_records' field"):
        asyncio.run(execute_case_production(
            case_spec=case_spec,
            search_backend=None,
            trace_coordinator=None,
            retrieval_coordinator=mock_retrieval_coord,
            thread_reader=None,
            safety_tracker=SearchSafetyTracker(),
            is_dry_run=True,
        ))

    # 2. Test raw_query_records is None
    bad_result_none = MagicMock(spec=[
        "evidence_package", "candidates", "selected_candidates",
        "candidate_query_provenance", "plan", "temporal_scope", "actor_scope", "raw_query_records"
    ])
    bad_result_none.selected_candidates = []
    bad_result_none.raw_query_records = None
    mock_retrieval_coord.retrieve_precomputed = AsyncMock(return_value=bad_result_none)

    with pytest.raises(AttributeError, match="missing canonical 'raw_query_records' field"):
        asyncio.run(execute_case_production(
            case_spec=case_spec,
            search_backend=None,
            trace_coordinator=None,
            retrieval_coordinator=mock_retrieval_coord,
            thread_reader=None,
            safety_tracker=SearchSafetyTracker(),
            is_dry_run=True,
        ))



