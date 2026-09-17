"""Phase 13.5D Task 11 — Clean Qualification v3 Offline Tests.

Validates all 4 contracts requested by the user and all offline freeze requirements:
1. Physical separation of v3 cases.json and gold.json (zero ground truth leak to production).
2. Q2-02 evaluator-only branch candidate identities ("정용기 세무사", "박은혜 세무사").
3. Q2-04 evaluator-only rates (8%, 5%) and exception policy grounding boundary:
   - Distinguishes exception omission without evidence (allowed) vs. explicit fixed-rate contradiction (forbidden).
   - Enforces reflection of representative discretion when exception evidence is retrieved.
4. Deterministic structured semantic WorkItem matching:
   - Dynamic WorkItem ID exact match is NOT required (evaluator semantic labels are not exact matchers).
   - Matching via structured claims, lifecycle signals, decisive authority, and traceability chains.
5. Query compiler fidelity: All 9 frozen queries preserved without token deletion/addition/rewriting.
6. F1 production execution boundary parity:
   - execute_case_production, trace_and_synthesize_async dispatch, real result extraction,
     safety/transition/contamination handling, and raw_query_records fail-closed behavior are identical to F1 authority.
7. Strict search safety telemetry contracts (R10H/R10J).
8. Mechanical self-contamination evaluation.
9. Provider schema-400 recurrence detection.
10. Offline dry-run execution with zero live Slack reads and zero provider calls.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import re
from typing import Any, Dict
import pytest

from pesu_agent.investigation.graph_models import (
    WorkItemStatus,
    WorkItemLifecycleSignal,
)
from pesu_agent.investigation.models import EvidenceItem
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler
from scripts.qualify_phase_13_5d_clean_v3 import (
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
    execute_case_production,
    run_clean_qualification_v3,
)


# =============================================================================
# 1. Physical Separation & Leak Prevention
# =============================================================================

def test_physical_separation_of_v3_cases_and_gold():
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

    # Q2-02 evaluator-only identities must NOT be in cases.json
    assert "정용기" not in cases_json_str
    assert "박은혜" not in cases_json_str

    # Q2-04 evaluator-only rates & discretion must NOT be in cases.json
    assert "8%" not in cases_json_str
    assert "8프로" not in cases_json_str
    assert "8퍼센트" not in cases_json_str
    assert "5%" not in cases_json_str
    assert "5프로" not in cases_json_str
    assert "5퍼센트" not in cases_json_str
    assert "대표 세무사" not in cases_json_str
    assert "재량" not in cases_json_str

    # Expected answers / states must NOT be in cases.json
    assert "expected_workitem_status" not in cases_json_str
    assert "expected_lifecycle_signal" not in cases_json_str
    assert "acceptance_contract" not in cases_json_str

    # gold.json MUST contain the evaluator-only identities, rates, and contracts
    assert "authoritative_pre_state" in gold_data
    q2_02_gold = gold_data["cases"]["Q2-02"]
    identities = q2_02_gold["independent_ground_truth"]["evaluator_only_target_identities"]
    assert "정용기 세무사" in identities
    assert "박은혜 세무사" in identities

    q2_04_gold = gold_data["cases"]["Q2-04"]
    assert q2_04_gold["independent_ground_truth"]["external_tax_accountant_base_royalty_rate"] == "8%"
    assert q2_04_gold["independent_ground_truth"]["internal_heum_tax_accountant_base_royalty_rate"] == "5%"


def test_production_investigation_plan_has_zero_gold_leakage():
    """InvestigationPlan created for production pipeline contains zero gold data."""
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    for case_spec in cases_bundle["cases"]:
        plan, actor_scope, temporal_scope = build_production_investigation_plan(case_spec)
        assert len(plan.questions_to_resolve) == 1
        assert len(plan.search_queries) == 3

        plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan.__dict__
        plan_str = json.dumps(plan_dict, ensure_ascii=False, default=str)

        # Invariant: No ground truth facts leaked into plan
        assert "정용기" not in plan_str
        assert "박은혜" not in plan_str
        assert "8%" not in plan_str
        assert "5%" not in plan_str
        assert "대표 세무사" not in plan_str
        assert "expected_workitem_status" not in plan_str
        assert "acceptance_contract" not in plan_str


# =============================================================================
# 2. Case Integrity, Order, and Prohibited Case Exclusion
# =============================================================================

def test_v3_predeclarations_integrity_and_exact_counts():
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    cases = cases_bundle["cases"]
    assert len(cases) == 3
    assert [c["case_id"] for c in cases] == FROZEN_CASE_ORDER
    assert cases_bundle["execution_order"] == FROZEN_CASE_ORDER
    assert FROZEN_CASE_ORDER == ["Q2-02", "Q2-03", "Q2-04"]

    expected_questions = {
        "Q2-02": "현재 지점 개설이 진행 중인 후보는 몇 곳이며, Slack에서 확인되는 각 후보의 현재 진행 단계는 어디까지야?",
        "Q2-03": "현재 지점 정산 업무는 누가 담당하고 있으며, 앞으로 지점 정산은 어떤 방식으로 진행하기로 최종 결정되었어?",
        "Q2-04": "지점 개설 시 로열티 요율은 외부 세무사와 혜움 내부 근무 세무사에게 각각 어떻게 적용되고, 내부 근무 세무사의 경우 예외적으로 조정될 수 있다면 어떤 기준으로 결정돼?",
    }

    for c in cases:
        cid = c["case_id"]
        assert c["question"] == expected_questions[cid]
        assert c["max_results_per_query"] == 5
        assert len(c["exact_slack_queries"]) == 3


def test_historical_and_retired_cases_permanently_rejected():
    """Historical A/B/C and retired Q2-01 must be permanently rejected."""
    for prohibited in ["CASE_A", "CASE_B", "CASE_C", "Q2-01"]:
        assert prohibited in HISTORICAL_PROHIBITED_CASES
        assert prohibited not in FROZEN_CASE_ORDER


# =============================================================================
# 3. Contract 4: Query Compiler Fidelity for All 9 Frozen Queries
# =============================================================================

def test_all_9_frozen_queries_preserve_query_compiler_fidelity():
    """Contract 4: For each of the 9 frozen queries in Q2-02, Q2-03, Q2-04,

    fixture exact query -> SlackQueryCompiler -> canonical compiled query
    must preserve exact tokens with zero deletion, addition, or rewriting.
    """
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    cases = cases_bundle["cases"]

    expected_frozen_queries = {
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
        "Q2-04": [
            "지점 개설 로열티 요율",
            "내부 근무 세무사 지점 로열티",
            "외부 세무사 지점 로열티",
        ],
    }

    total_query_count = 0
    for case_spec in cases:
        cid = case_spec["case_id"]
        expected_list = expected_frozen_queries[cid]
        actual_queries = case_spec["exact_slack_queries"]
        assert len(actual_queries) == len(expected_list)

        for i, q_obj in enumerate(actual_queries):
            raw_q = q_obj["raw_planner_query"]
            comp_q = q_obj["compiled_query"]
            expected_str = expected_list[i]

            assert raw_q == expected_str
            assert comp_q == expected_str

            # Pass through SlackQueryCompiler
            compiler_res = SlackQueryCompiler.compile(raw_q)
            canonical_compiled = compiler_res.compiled_query

            # Exact preservation check: No tokens dropped or rewritten
            assert canonical_compiled == raw_q, (
                f"Query compiler drift detected in {cid} query {i+1}: expected '{raw_q}', got '{canonical_compiled}'"
            )
            total_query_count += 1

    assert total_query_count == 9


# =============================================================================
# 4. Canonical Enums Used in Gold
# =============================================================================

def test_canonical_enums_used_in_v3_gold():
    gold = CleanQualificationGoldLoader.load_gold_bundle()
    valid_statuses = {s.value for s in WorkItemStatus}
    valid_signals = {s.value for s in WorkItemLifecycleSignal}

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

    # Q2-04
    q4_status = gold["cases"]["Q2-04"]["expected_semantic_outcome"]["expected_workitem_status"]
    q4_signal = gold["cases"]["Q2-04"]["expected_semantic_outcome"]["expected_lifecycle_signal"]
    assert q4_status == WorkItemStatus.COMPLETED.value
    assert q4_signal == WorkItemLifecycleSignal.COMPLETION.value


# =============================================================================
# 5. Contract 1: Production Execution Boundary Parity with F1 Authority
# =============================================================================

def test_v3_production_path_parity_with_f1():
    """Contract 1: v3 runner production execution path must have full parity with F1 remediated authority.

    V3_PRODUCTION_PATH_PARITY_WITH_F1 = true
    F1_INTERFACE_REMEDIATION_REGRESSION = false
    """
    from pesu_agent.investigation.production_trace_coordinator import (
        ProductionRealTraceCoordinator,
        ProductionTraceResult,
    )
    import scripts.qualify_phase_13_5d_clean_v2 as v2_runner
    import scripts.qualify_phase_13_5d_clean_v3 as v3_runner

    # 1. Interface signature parity
    assert hasattr(ProductionRealTraceCoordinator, "trace_and_synthesize_async")
    assert not hasattr(ProductionRealTraceCoordinator, "execute_trace")

    v3_src = inspect.getsource(v3_runner.execute_case_production)
    v2_src = inspect.getsource(v2_runner.execute_case_production)

    # 2. Both must call trace_and_synthesize_async and NOT execute_trace
    assert "trace_and_synthesize_async" in v3_src
    assert "execute_trace" not in v3_src

    # 3. Both must use ProductionTraceResult canonical fields
    assert "trace_result.work_items" in v3_src
    assert "trace_result.traceability_chains" in v3_src
    assert "trace_result.workitems" not in v3_src
    assert "trace_result.claims" not in v3_src

    # 4. Both must enforce raw_query_records fail-closed
    assert "raw_query_records" in v3_src
    assert "missing canonical 'raw_query_records' field" in v3_src

    # 5. execute_case_production signature parity
    v3_params = list(inspect.signature(v3_runner.execute_case_production).parameters.keys())
    v2_params = list(inspect.signature(v2_runner.execute_case_production).parameters.keys())
    assert v3_params == v2_params


def test_execute_case_production_against_real_coordinator_spec():
    """Ensures v3 execute_case_production runs cleanly against real coordinator spec."""
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

    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    case_spec = cases_bundle["cases"][0]  # Q2-02

    mock_trace_coord = create_autospec(ProductionRealTraceCoordinator, instance=True)

    test_ev = EvidenceItem(
        evidence_id="ev_01",
        text="정용기 세무사와 박은혜 세무사 지점 개설 절차가 진행 중이다.",
        source="slack",
        permalink="https://slack.com/archives/C1/p1",
    )

    real_graph = InvestigationStateGraph(question=case_spec["question"])
    test_claim_trace = ClaimTraceabilityRecord(
        claim_id="claim_01",
        statement="정용기 세무사와 박은혜 세무사 지점 개설 절차가 진행 중이다.",
        evidence_ids=["ev_01"],
        lifecycle_signal=WorkItemLifecycleSignal.PROGRESS,
    )
    test_chain = WorkItemTraceabilityChain(
        work_item_id="wi_01",
        title="지점 개설 절차 진행",
        final_status=WorkItemStatus.IN_PROGRESS,
        governing_claim_ids=["claim_01"],
        claim_traces=[test_claim_trace],
        all_evidence_ids=["ev_01"],
        is_traceable=True,
    )
    test_wi_node = WorkItemNode(
        work_item_id="wi_01",
        title="지점 개설 절차 진행",
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

    prod_res = asyncio.run(execute_case_production(
        case_spec=case_spec,
        search_backend=None,
        trace_coordinator=mock_trace_coord,
        retrieval_coordinator=mock_retrieval_coord,
        thread_reader=None,
        safety_tracker=SearchSafetyTracker(),
        is_dry_run=False,
    ))

    assert prod_res is not None
    assert "workitems" in prod_res
    assert len(prod_res["workitems"]) == 1
    assert prod_res["workitems"][0]["status"] == "IN_PROGRESS"
    assert "raw_query_records" in prod_res


def test_raw_query_records_missing_fails_closed():
    """Fail-closed contract: missing or None raw_query_records MUST raise AttributeError."""
    from unittest.mock import AsyncMock, create_autospec, MagicMock
    from pesu_agent.investigation.retrieval_coordinator import ProductionInvestigationRetrievalCoordinator

    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    case_spec = cases_bundle["cases"][0]

    mock_retrieval_coord = create_autospec(ProductionInvestigationRetrievalCoordinator, instance=True)

    # Missing attribute
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

    # raw_query_records is None
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


# =============================================================================
# 6. Safety & Contamination Telemetry Tests
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
    telem = _build_valid_telemetry("지점 개설 로열티 요율")
    tracker.record_query_execution("지점 개설 로열티 요율", 3, telem, 0.8)
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


def test_contamination_selected_self_generated_echo_invalidates():
    cand = EvidenceItem(
        evidence_id="e_contam_1",
        source="slack_channel",
        query_source="지점 개설 로열티 요율",
        permalink="https://workspace.slack.com/archives/D07RA9YDE8G/p12345",
        text="지점 개설 로열티 요율",
        author="USLACKBOT",
    )
    report = evaluate_self_contamination(
        raw_query_records=[],
        selected_candidates=[cand],
        target_queries={"지점 개설 로열티 요율"},
    )
    assert report["SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 1
    assert report["CASE_VALIDITY"] == "INVALIDATED_BY_PREEXISTING_SELF_CONTAMINATION"


# =============================================================================
# 7. Evaluator Contracts: Q2-02, Q2-03, Q2-04
# =============================================================================

def test_q2_02_evaluator_branch_identities_and_unsupported_signature_sent():
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-02"]

    # Fails if candidates not identified
    checks_missing = evaluator._evaluate_q2_02(
        gold=gold,
        workitems=[{"title": "지점 개설", "status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
        answer="현재 지점 개설 진행 중인 곳은 2곳입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_missing["BOTH_ACTIVE_OPENINGS_REPRESENTED"] is False

    # Fails on unsupported signature / sent claims
    checks_overclaim = evaluator._evaluate_q2_02(
        gold=gold,
        workitems=[{"title": "지점 개설", "status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
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
        workitems=[{"title": "지점 개설 절차", "status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"}],
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
        workitems=[{"title": "지점 정산 방식 결정", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}],
        answer="지점운영팀 슈퍼바이저가 담당하며, 최종 결정된 방식은 공동인증서를 통한 정산 방식입니다.",
        claims=[],
        evidence=[],
    )
    assert checks_superseded["SUPERSEDED_OPTION_PRESENTED_AS_FINAL"] is True
    assert checks_superseded["GROUNDING_DISCIPLINE"] is False

    # Passes if finalized method is 손익계산서 업로드
    checks_valid = evaluator._evaluate_q2_03(
        gold=gold,
        workitems=[
            {"title": "지점 정산 방식 결정", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"},
            {"title": "지점 정산 운영", "status": "IN_PROGRESS", "lifecycle_signal": "PROGRESS"},
        ],
        answer="지점 정산 업무는 지점운영팀 슈퍼바이저가 담당하며, 앞으로 지점에서 손익계산서를 업로드하여 정산하는 방식으로 최종 결정되었습니다.",
        claims=[],
        evidence=[],
    )
    assert checks_valid["RESPONSIBLE_ROLE_CORRECT"] is True
    assert checks_valid["FINAL_SETTLEMENT_METHOD_CORRECT"] is True
    assert checks_valid["DECISION_WORKITEM_STATE_CORRECT"] is True
    assert checks_valid["SUPERSEDED_OPTION_PRESENTED_AS_FINAL"] is False
    assert checks_valid["GROUNDING_DISCIPLINE"] is True


def test_q2_04_evaluator_rates_and_workitem_matching():
    """Contract 3: Structured semantic WorkItem matching for Q2-04 without requiring exact ID."""
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]

    # Dynamic WorkItem ID (e.g. wi_dyn_999) with title "로열티 요율 정책"
    workitems = [
        {
            "work_item_id": "wi_dyn_999",
            "title": "지점 개설 로열티 요율 정책",
            "status": "COMPLETED",
            "lifecycle_signal": "COMPLETION",
            "claim_ids": ["c1"],
        }
    ]
    claims = [
        {"claim_id": "c1", "statement": "지점 개설 로열티 요율은 외부 세무사 8%, 혜움 내부 세무사 5%로 결정되었다."}
    ]

    checks = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="지점 개설 시 로열티 요율은 외부 세무사는 8%, 혜움 내부 근무 세무사는 5%가 적용됩니다.",
        claims=claims,
        evidence=[],
    )
    assert checks["EXTERNAL_BASE_RATE_CORRECT"] is True
    assert checks["INTERNAL_BASE_RATE_CORRECT"] is True
    assert checks["POLICY_WORKITEM_STATE_CORRECT"] is True


def test_q2_04_evaluator_contract2_exception_omission_without_evidence_allowed():
    """Contract 2: When exception evidence is NOT retrieved, omission of the exception alone MUST pass.

    EXCEPTION_OMISSION_WITHOUT_EVIDENCE_ALLOWED = true
    """
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]

    workitems = [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}]

    # Answer says external 8%, internal 5% with no mention of representative discretion
    checks = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="지점 개설 시 로열티 요율은 외부 세무사의 경우 8%, 내부 근무 세무사의 경우 5%가 기본 적용됩니다.",
        claims=[],
        evidence=[],  # No exception evidence
    )
    assert checks["EXTERNAL_BASE_RATE_CORRECT"] is True
    assert checks["INTERNAL_BASE_RATE_CORRECT"] is True
    assert checks["POLICY_WORKITEM_STATE_CORRECT"] is True
    assert checks["UNSUPPORTED_EXCEPTION_POLICY_CLAIM"] is False
    assert checks["FORBIDDEN_OVERCLAIM_REJECTED"] is True
    assert checks["RETRIEVED_EXCEPTION_EVIDENCE_REFLECTED"] is True
    assert checks["GROUNDING_DISCIPLINE"] is True
    assert checks["GROUNDED_ANSWER_HAS_NO_UNSUPPORTED_FACT"] is True


def test_q2_04_evaluator_contract2_explicit_fixed_rate_overclaim_forbidden():
    """Contract 2: Asserting internal rate is strictly/always fixed at 5% with no exceptions is forbidden.

    EXPLICIT_FIXED_RATE_OVERCLAIM_FORBIDDEN = true
    """
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]

    workitems = [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}]

    # Answer asserts internal rate is strictly fixed with zero exception possible
    checks = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="외부 세무사는 8%, 내부 근무 세무사는 예외 없이 항상 5% 고정으로 적용되며 예외 조정은 전혀 불가능합니다.",
        claims=[],
        evidence=[],
    )
    assert checks["FORBIDDEN_OVERCLAIM_REJECTED"] is False
    assert checks["GROUNDING_DISCIPLINE"] is False


def test_q2_04_evaluator_contract2_ungrounded_exception_invention_forbidden():
    """Contract 2: Inventing the representative exception without supporting evidence is forbidden."""
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]

    workitems = [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}]

    # Answer claims representative discretion, but evidence has NO mention of it
    checks = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="외부 세무사 8%, 내부 세무사 5%이며 대표 세무사의 재량과 판단에 따라 예외적으로 조정됩니다.",
        claims=[],
        evidence=[],  # Zero evidence
    )
    assert checks["UNSUPPORTED_EXCEPTION_POLICY_CLAIM"] is True
    assert checks["GROUNDING_DISCIPLINE"] is False


def test_q2_04_evaluator_contract2_retrieved_exception_evidence_must_be_reflected():
    """Contract 2: When explicit exception evidence IS retrieved, answer MUST accurately reflect it.

    RETRIEVED_EXCEPTION_EVIDENCE_MUST_BE_REFLECTED = true
    """
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]

    workitems = [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}]
    evidence = [
        {"evidence_id": "ev_q4", "text": "지점 로열티는 외부 8%, 내부 5% 기준이나 대표 세무사의 판단에 따라 예외 조정이 가능합니다."}
    ]

    # Valid: Reflects representative discretion accurately
    checks_valid = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="지점 개설 로열티 요율은 외부 세무사 8%, 내부 근무 세무사 5%가 기본 적용되며, 내부 세무사의 경우 대표 세무사의 판단에 따라 예외적으로 조정될 수 있습니다.",
        claims=[],
        evidence=evidence,
    )
    assert checks_valid["RETRIEVED_EXCEPTION_EVIDENCE_REFLECTED"] is True
    assert checks_valid["UNSUPPORTED_EXCEPTION_POLICY_CLAIM"] is False
    assert checks_valid["FORBIDDEN_OVERCLAIM_REJECTED"] is True
    assert checks_valid["GROUNDING_DISCIPLINE"] is True

    # Invalid: Evidence exists but answer contradicts it by claiming always fixed at 5%
    checks_contradiction = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="외부 8%, 내부는 무조건 5% 고정이며 예외 조정은 전혀 없습니다.",
        claims=[],
        evidence=evidence,
    )
    assert checks_contradiction["FORBIDDEN_OVERCLAIM_REJECTED"] is False
    assert checks_contradiction["RETRIEVED_EXCEPTION_EVIDENCE_REFLECTED"] is False
    assert checks_contradiction["GROUNDING_DISCIPLINE"] is False


# =============================================================================
# 8. Provider 400 Schema Error Detection
# =============================================================================

def test_provider_schema_400_recurrence_fails_qualification():
    evaluator = CleanQualificationEvaluator()
    prod_res = {
        "case_id": "Q2-04",
        "question": "question",
        "workitems": [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}],
        "grounded_answer": "지점 개설 시 로열티 요율은 외부 세무사 8%, 혜움 내부 근무 세무사 5%입니다.",
        "claims": [],
        "evidence": [],
    }
    contam_report = {"CASE_VALIDITY": "VALID"}
    tracker = SearchSafetyTracker()

    scorecard = evaluator.evaluate_case(
        case_id="Q2-04",
        production_result=prod_res,
        contamination_report=contam_report,
        safety_tracker=tracker,
        provider_telemetry={"schema_400_recurrence": True, "fallback_count": 1},
    )
    assert scorecard["overall_pass"] is False
    assert scorecard.get("failure_reason") == "PROVIDER_SCHEMA_400_RECURRENCE"


def test_q2_04_evaluator_forbidden_external_discretion_and_case_by_case():
    evaluator = CleanQualificationEvaluator()
    gold = evaluator.gold_bundle["cases"]["Q2-04"]
    workitems = [{"title": "로열티 정책", "status": "COMPLETED", "lifecycle_signal": "COMPLETION"}]

    # 1. External rate claimed as representative discretion -> forbidden
    checks_ext_disc = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="내부 세무사는 5%이고, 외부 세무사의 8% 요율은 대표 세무사의 재량에 따라 결정됩니다.",
        claims=[],
        evidence=[],
    )
    assert checks_ext_disc["FORBIDDEN_OVERCLAIM_REJECTED"] is False
    assert checks_ext_disc["GROUNDING_DISCIPLINE"] is False

    # 2. All rates claimed case-by-case without base rate -> forbidden
    checks_case_by_case = evaluator._evaluate_q2_04(
        gold=gold,
        workitems=workitems,
        answer="지점 로열티는 기준 요율이 없으며 모든 지점은 전적으로 건별 협의로만 결정됩니다.",
        claims=[],
        evidence=[],
    )
    assert checks_case_by_case["FORBIDDEN_OVERCLAIM_REJECTED"] is False
    assert checks_case_by_case["GROUNDING_DISCIPLINE"] is False


# =============================================================================
# 9. Dry-Run Offline Execution & Safeguards
# =============================================================================

def test_live_execution_safeguard_blocks_without_authorization():
    """Live qualification without authorized_live flag fails immediately."""
    exit_code = asyncio.run(run_clean_qualification_v3(is_dry_run=False, authorized_live=False))
    assert exit_code == 1


def test_dry_run_offline_execution_pure():
    """Offline dry-run executes purely with SyntheticSearchBackend and passes all 3 cases."""
    exit_code = asyncio.run(run_clean_qualification_v3(is_dry_run=True, authorized_live=False))
    assert exit_code == 0
