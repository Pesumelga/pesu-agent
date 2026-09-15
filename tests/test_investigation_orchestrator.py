"""Unit and Mock Integration Tests for Phase 8 Investigation Orchestrator."""

from __future__ import annotations

import pytest

from pesu_agent.investigation.models import (
    AnalysisResult,
    ConfirmedFact,
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
    UserQuestion,
)
from pesu_agent.investigation.orchestrator import (
    CandidateQueryHit,
    InvestigationOrchestrator,
    InvestigationState,
    InvestigationStopReason,
    OrchestratorConfig,
)
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.investigation.selector import EvidenceSelector, get_canonical_evidence_key
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
)
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.llm.provider import ModelProvider


class MockModelProvider(ModelProvider):
    """테스트용 가상 모델 프로바이더."""

    def __init__(self, plan_return: InvestigationPlan, analysis_return: AnalysisResult):
        self.plan_return = plan_return
        self.analysis_return = analysis_return

    async def generate_structured(self, messages, response_schema, **kwargs):
        usage = LLMUsageStats(
            provider="mock",
            model="mock-model",
            input_tokens=100,
            output_tokens=50,
            actual_cost_usd=0.0001,
            latency_ms=50.0,
        )
        if response_schema == InvestigationPlan:
            return self.plan_return, usage
        elif response_schema == AnalysisResult:
            return self.analysis_return, usage
        raise ValueError(f"Unexpected schema: {response_schema}")

    async def generate_text(self, messages, **kwargs):
        return "mock text", LLMUsageStats(provider="mock", model="mock-model")

    async def close(self):
        pass


@pytest.mark.anyio
async def test_search_backend_and_selector(tmp_path) -> None:
    """SyntheticSearchBackend와 EvidenceSelector의 중복 제거 및 선별 기능 검증."""
    corpus = [
        EvidenceItem(
            evidence_id="s1",
            source="synthetic",
            author="홍길동",
            timestamp="2026-08-01 10:00",
            channel="dev",
            text="PG사 수수료 인하안 1.4% 승인 완료 공지합니다.",
            message_identifier="msg_1",
        ),
        EvidenceItem(
            evidence_id="s2",
            source="synthetic",
            author="홍길동",
            timestamp="2026-08-01 10:00",
            channel="dev",
            text="PG사 수수료 인하안 1.4% 승인 완료 공지합니다.", # Duplicate message
            message_identifier="msg_1",
        ),
        EvidenceItem(
            evidence_id="s3",
            source="synthetic",
            author="직원",
            timestamp="2026-08-01 10:05",
            channel="dev",
            text="ㅇㅇ", # Short noise
            message_identifier="msg_3",
        ),
    ]

    backend = SyntheticSearchBackend(corpus=corpus)
    results = await backend.search(SearchQuery(query="PG사 수수료 인하안", purpose="수수료 확인"))
    assert len(results) == 2 # s1 and s2 match

    selector = EvidenceSelector(max_evidence_items=10, min_text_len=5)
    pkg, stats = selector.select_and_package(candidates=results)

    assert stats["initial_candidates"] == 2
    assert stats["duplicates_removed"] == 1
    assert stats["final_selected"] == 1
    assert pkg.total_items == 1
    assert pkg.evidence_items[0].evidence_id == "E1"
    assert pkg.is_synthetic is True


@pytest.mark.anyio
async def test_orchestrator_single_round_success(tmp_path) -> None:
    """InvestigationOrchestrator 단일 라운드 정상 완결 검증."""
    mock_plan = InvestigationPlan(
        objective="PG사 수수료 인하안 최종 승인 확인",
        search_queries=[
            SearchQuery(query="PG사 수수료 인하 공지", purpose="승인 공지 확인")
        ],
        questions_to_resolve=["수수료율 인하안이 승인되었는가?"],
    )
    mock_analysis = AnalysisResult(
        summary="PG사 수수료율이 1.4%로 정상 승인되었습니다.",
        confirmed_facts=[
            ConfirmedFact(statement="PG 수수료율이 1.4%로 인하됨", supporting_evidence_ids=["E1"])
        ],
        disputed_points=[],
        contradictions=[],
        assessment="재무팀장 전자결재 공지에 따라 9월 1일부터 전 지점 1.4% 적용이 확정됨.",
        uncertainty="추가적인 불확실성 없음.",
        missing_information=[],
        suggested_followups=[],
        conclusion_status="CONFIRMED",
        responsibility_determinable=True,
        evidence_sufficiency="SUFFICIENT",
        cited_evidence_ids=["E1"],
    )

    corpus = [
        EvidenceItem(
            evidence_id="item_01",
            source="synthetic",
            author="재무팀장",
            timestamp="2026-08-01 09:00",
            channel="notices",
            text="PG사 신규 수수료율 1.4% 전자결재 완료되어 9월 1일부터 적용됩니다.",
            message_identifier="ident_01",
        )
    ]

    backend = SyntheticSearchBackend(corpus=corpus)
    provider = MockModelProvider(plan_return=mock_plan, analysis_return=mock_analysis)
    config = OrchestratorConfig(base_output_dir=tmp_path / "investigations")

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        search_backend=backend,
        config=config,
    )

    question = UserQuestion(question="PG사 수수료율 인하안 최종 승인 여부를 확인해줘.")
    result = await orchestrator.run_investigation(question=question)

    assert result.state == InvestigationState.COMPLETED
    assert result.stop_reason == InvestigationStopReason.EVIDENCE_SUFFICIENT
    assert result.audit.search_rounds == 1
    assert result.audit.llm_calls == 2 # 1 plan + 1 analyze
    assert (tmp_path / "investigations" / result.investigation_id / "plan.json").exists()
    assert (tmp_path / "investigations" / result.investigation_id / "evidence_package.json").exists()
    assert (tmp_path / "investigations" / result.investigation_id / "analysis.json").exists()
    assert (tmp_path / "investigations" / result.investigation_id / "audit.json").exists()


def test_canonical_key_single_source_of_truth():
    """Verify that EvidenceSelector and Orchestrator share the exact pure get_canonical_evidence_key."""
    from pesu_agent.investigation.selector import get_canonical_evidence_key as sel_key
    from pesu_agent.investigation.orchestrator import get_canonical_evidence_key as orch_key

    assert sel_key is orch_key

    # Test permalink / message_identifier identity
    item1 = EvidenceItem(
        evidence_id="e1",
        source="slack",
        text="간담회 일정 공지",
        message_identifier="https://app.slack.com/archives/C123/p1787000000000001",
        has_thread=True,
    )
    assert sel_key(item1) == orch_key(item1)
    assert sel_key(item1) == "https://app.slack.com/archives/C123/p1787000000000001"

    # Test fallback identity
    item2 = EvidenceItem(
        evidence_id="e2",
        source="slack",
        text="텍스트 본문",
        author="작성자",
        timestamp="2026-08-01 10:00",
        has_thread=False,
    )
    assert sel_key(item2) == orch_key(item2)


@pytest.mark.anyio
async def test_executed_search_query_snapshot_not_mutated_by_planned_search():
    """Verify executed SearchQuery snapshot model value is not mutated by backend.search()."""
    sq = SearchQuery(query="2026 간담회 일정", purpose="일정 조사")
    snapshot = sq.model_copy(deep=True)
    before_dump = snapshot.model_dump()

    backend = SyntheticSearchBackend(corpus=[])
    await backend.search(snapshot, max_results=5)

    after_dump = snapshot.model_dump()
    assert before_dump == after_dump
    assert snapshot.query == "2026 간담회 일정"
    assert snapshot.purpose == "일정 조사"


class RelocationTestSearchBackend:
    """Configurable SearchBackend for testing provenance, residency checks, and relocation."""

    def __init__(
        self,
        query_results: dict[str, list[EvidenceItem]],
        initial_read_res: ThreadReadResult,
        post_reloc_read_res: Optional[ThreadReadResult] = None,
    ):
        self.query_results = query_results
        self.initial_read_res = initial_read_res
        self.post_reloc_read_res = post_reloc_read_res
        self.executed_searches: list[tuple[SearchQuery, int]] = []
        self.read_results_history: list[ThreadReadResult] = []
        self.last_telemetry = {"residency_matched": True, "stale": True}
        self.fail_relocation_search = False

    async def search(self, query: SearchQuery, max_results: int = 5, **kwargs) -> list[EvidenceItem]:
        self.executed_searches.append((query, max_results))
        if self.fail_relocation_search and len(self.executed_searches) > 3:
            raise RuntimeError("Simulated relocation search network failure")
        return [it.model_copy(deep=True) for it in self.query_results.get(query.query, [])]

    async def read_thread_result(self, candidate: EvidenceItem) -> ThreadReadResult:
        if len(self.read_results_history) == 0:
            res = self.initial_read_res
        else:
            res = self.post_reloc_read_res or self.initial_read_res
        self.read_results_history.append(res)
        return res


def _build_test_plan() -> InvestigationPlan:
    return InvestigationPlan(
        objective="간담회 일정 파악",
        search_queries=[
            SearchQuery(query="query_0", purpose="p0"),
            SearchQuery(query="query_1", purpose="p1"),
            SearchQuery(query="query_2", purpose="p2"),
        ],
        questions_to_resolve=["언제인가?"],
    )


def _build_test_analysis() -> AnalysisResult:
    return AnalysisResult(
        summary="간담회 일정이 확인되었습니다.",
        assessment="간담회 일정이 11월 15일로 확인됨.",
        uncertainty="추가적인 불확실성 없음.",
        conclusion_status="CONFIRMED",
        responsibility_determinable=True,
        evidence_sufficiency="SUFFICIENT",
        cited_evidence_ids=["E1"],
    )


@pytest.mark.anyio
async def test_provenance_retention_and_same_snapshot_reference(tmp_path):
    """Verify Q0, Q1, Q2 provenance retained, stores exact executed snapshot reference, and relocation reuses it."""
    target_item = EvidenceItem(
        evidence_id="item_q0",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    other_item1 = EvidenceItem(
        evidence_id="item_q1",
        source="synthetic",
        text="관련 없는 Q1 메시지",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000002",
        has_thread=False,
    )
    other_item2 = EvidenceItem(
        evidence_id="item_q2",
        source="synthetic",
        text="관련 없는 Q2 메시지",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000003",
        has_thread=False,
    )

    query_results = {
        "query_0": [target_item],
        "query_1": [other_item1],
        "query_2": [other_item2],
    }

    reply = ThreadReplyItem(
        reply_id="R1",
        text="간담회는 11월 15일입니다.",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )

    # Initial read: TARGET_ROW_NOT_IN_CURRENT_DOM -> triggers relocation
    initial_read = ThreadReadResult(
        replies=(),
        status=ThreadContextStatus.UNAVAILABLE,
        failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
    )
    post_reloc_read = ThreadReadResult(
        replies=(reply,),
        status=ThreadContextStatus.PARTIAL,
        failure_reason=None,
    )

    backend = RelocationTestSearchBackend(
        query_results=query_results,
        initial_read_res=initial_read,
        post_reloc_read_res=post_reloc_read,
    )
    provider = MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis())
    orch = InvestigationOrchestrator(
        provider=provider,
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv"),
    )

    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    # Assert 4 total search operations: 3 planned + 1 relocation
    assert len(backend.executed_searches) == 4
    planned_q0, _ = backend.executed_searches[0]
    planned_q1, _ = backend.executed_searches[1]
    planned_q2, _ = backend.executed_searches[2]
    reloc_q, _ = backend.executed_searches[3]

    # Relocation must reuse the EXACT same object reference as executed Q0 snapshot
    assert reloc_q is planned_q0
    assert reloc_q.query == "query_0"

    # Audit telemetry assertions
    assert result.audit.planned_query_count == 3
    assert result.audit.relocation_search_count == 1
    assert result.audit.total_slack_search_operations == 4
    assert len(backend.read_results_history) == 2


@pytest.mark.anyio
async def test_multi_hit_relocation_source_policy(tmp_path):
    """Verify multi-hit relocation source policy selects highest executed query_index (Q0+Q1 -> Q1, Q0+Q1+Q2 -> Q2)."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )

    # Scenario A: Target in Q0 and Q1 -> Highest query index is Q1
    query_results_a = {
        "query_0": [target_item],
        "query_1": [target_item],
        "query_2": [],
    }
    backend_a = RelocationTestSearchBackend(
        query_results=query_results_a,
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
        post_reloc_read_res=ThreadReadResult(replies=(), status=ThreadContextStatus.PARTIAL),
    )
    provider_a = MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis())
    orch_a = InvestigationOrchestrator(
        provider=provider_a,
        search_backend=backend_a,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_a"),
    )
    await orch_a.run_investigation(UserQuestion(question="간담회 일정"))

    assert len(backend_a.executed_searches) == 4
    reloc_q_a, _ = backend_a.executed_searches[3]
    q1_executed, _ = backend_a.executed_searches[1]
    assert reloc_q_a is q1_executed
    assert reloc_q_a.query == "query_1"

    # Scenario B: Target in Q0, Q1, and Q2 -> Highest query index is Q2
    query_results_b = {
        "query_0": [target_item],
        "query_1": [target_item],
        "query_2": [target_item],
    }
    backend_b = RelocationTestSearchBackend(
        query_results=query_results_b,
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
        post_reloc_read_res=ThreadReadResult(replies=(), status=ThreadContextStatus.PARTIAL),
    )
    provider_b = MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis())
    orch_b = InvestigationOrchestrator(
        provider=provider_b,
        search_backend=backend_b,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_b"),
    )
    await orch_b.run_investigation(UserQuestion(question="간담회 일정"))

    assert len(backend_b.executed_searches) == 4
    reloc_q_b, _ = backend_b.executed_searches[3]
    q2_executed, _ = backend_b.executed_searches[2]
    assert reloc_q_b is q2_executed
    assert reloc_q_b.query == "query_2"


@pytest.mark.anyio
async def test_initially_resident_triggers_zero_relocation(tmp_path):
    """Verify that if candidate is resident on initial check, relocation search is not performed."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    reply = ThreadReplyItem(
        reply_id="R1",
        text="댓글 내용",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )
    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(reply,),
            status=ThreadContextStatus.PARTIAL,
            failure_reason=None,
        ),
    )
    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    assert len(backend.executed_searches) == 3
    assert result.audit.relocation_search_count == 0
    assert result.audit.total_slack_search_operations == 3
    assert len(backend.read_results_history) == 1
    assert result.evidence_package.thread_context_status == "PARTIAL"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "non_reloc_reason",
    [
        ThreadReadFailureReason.ROW_IDENTITY_AMBIGUOUS,
        ThreadReadFailureReason.REPLY_BAR_UNAVAILABLE,
        ThreadReadFailureReason.CLICK_CORRELATED_ROOT_FAILED,
        ThreadReadFailureReason.MESSAGE_REGION_UNAVAILABLE,
        ThreadReadFailureReason.EXTRACTION_FAILED,
        ThreadReadFailureReason.ELIGIBILITY_FAILED,
        ThreadReadFailureReason.IDENTITY_PARSE_FAILED,
        None,
    ],
)
async def test_non_relocatable_failure_reasons_trigger_zero_relocation(tmp_path, non_reloc_reason):
    """Verify non-relocatable typed terminal failure reasons result in relocation_search_count == 0."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=non_reloc_reason,
        ),
    )
    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / f"inv_{non_reloc_reason}"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    assert len(backend.executed_searches) == 3
    assert result.audit.relocation_search_count == 0
    assert len(backend.read_results_history) == 1
    assert result.state == InvestigationState.COMPLETED


@pytest.mark.anyio
async def test_stale_telemetry_cannot_control_relocation(tmp_path):
    """Verify that reader.last_telemetry is never used for control flow and cannot trigger/suppress relocation."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    reply = ThreadReplyItem(
        reply_id="R1",
        text="댓글 내용",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )
    # Stale telemetry claims NOT resident, but typed result is PARTIAL
    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(reply,),
            status=ThreadContextStatus.PARTIAL,
            failure_reason=None,
        ),
    )
    backend.last_telemetry = {"residency_matched": False, "failure_reason": "TARGET_ROW_NOT_IN_CURRENT_DOM"}

    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_telemetry"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    # Must NOT trigger relocation because typed result was PARTIAL
    assert result.audit.relocation_search_count == 0
    assert len(backend.executed_searches) == 3


@pytest.mark.anyio
async def test_legacy_backend_without_typed_result_never_triggers_relocation(tmp_path):
    """Verify that a legacy backend without read_thread_result falls back safely and never triggers relocation."""
    class LegacyTestSearchBackend:
        def __init__(self, target_item: EvidenceItem):
            self.target_item = target_item
            self.executed_searches: list[tuple[SearchQuery, int]] = []
            self.read_thread_context_called = False

        async def search(self, query: SearchQuery, max_results: int = 5, **kwargs) -> list[EvidenceItem]:
            self.executed_searches.append((query, max_results))
            return [self.target_item.model_copy(deep=True)]

        async def read_thread_context(self, candidate: EvidenceItem) -> tuple[list[Any], ThreadContextStatus]:
            self.read_thread_context_called = True
            return [], ThreadContextStatus.UNAVAILABLE

    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    search_backend = LegacyTestSearchBackend(target_item=target_item)

    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=search_backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_legacy"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    assert search_backend.read_thread_context_called is True
    assert result.audit.relocation_search_count == 0
    assert len(search_backend.executed_searches) == 3
    assert result.state == InvestigationState.COMPLETED
    assert result.evidence_package.thread_context_status == "UNAVAILABLE"


@pytest.mark.anyio
async def test_relocation_search_failure_preserves_parent_evidence(tmp_path):
    """Verify that relocation search failure isolates safely: parent evidence preserved, investigation continues."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
    )
    backend.fail_relocation_search = True  # Raises exception during 4th search

    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_reloc_fail"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    assert result.state == InvestigationState.COMPLETED
    assert result.analysis is not None
    assert len(result.evidence_package.evidence_items) == 1
    parent = result.evidence_package.evidence_items[0]
    assert parent.text == target_item.text
    assert parent.message_identifier == target_item.message_identifier
    assert parent.thread_context_status == "UNAVAILABLE"


@pytest.mark.anyio
async def test_evidence_freeze_invariant_after_relocation(tmp_path):
    """Verify evidence freeze: only selected parent's thread_replies and thread_context_status may change."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
        author="홍길동",
        timestamp="2026-08-01 10:00",
        channel="C1",
    )
    other_item = EvidenceItem(
        evidence_id="other",
        source="synthetic",
        text="다른 메시지 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000002",
        has_thread=False,
        author="김철수",
        timestamp="2026-08-01 10:05",
        channel="C1",
    )

    # Initial read: TARGET_ROW_NOT_IN_CURRENT_DOM -> relocation
    initial_read = ThreadReadResult(
        replies=(),
        status=ThreadContextStatus.UNAVAILABLE,
        failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
    )
    reply = ThreadReplyItem(
        reply_id="R1",
        text="확정 일자 댓글",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )
    post_reloc_read = ThreadReadResult(
        replies=(reply,),
        status=ThreadContextStatus.PARTIAL,
        failure_reason=None,
    )

    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item, other_item]},
        initial_read_res=initial_read,
        post_reloc_read_res=post_reloc_read,
    )
    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_freeze"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    # Compute selector output on candidates without thread enrichment to compare package equality
    selector = EvidenceSelector(max_evidence_items=5)
    baseline_pkg, _ = selector.select_and_package(
        candidates=[target_item, other_item],
        plan=_build_test_plan(),
    )

    final_pkg = result.evidence_package
    assert len(final_pkg.evidence_items) == len(baseline_pkg.evidence_items)

    for base_it, final_it in zip(baseline_pkg.evidence_items, final_pkg.evidence_items):
        assert base_it.evidence_id == final_it.evidence_id
        assert base_it.text == final_it.text
        assert base_it.message_identifier == final_it.message_identifier
        assert base_it.author == final_it.author
        assert base_it.channel == final_it.channel
        assert base_it.query_hit_count == final_it.query_hit_count
        assert base_it.temporal_relation == final_it.temporal_relation

    # Target parent has exactly the enriched fields
    target_final = final_pkg.evidence_items[0]
    assert len(target_final.thread_replies) == 1
    assert target_final.thread_context_status == "PARTIAL"


@pytest.mark.anyio
async def test_relocated_exact_rematch_cases(tmp_path):
    """Verify rematch outcomes after relocation: exact resident match vs absent vs ambiguous."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    # Case A: Relocated target absent on rematch -> second ThreadReader returns TARGET_ROW_NOT_IN_CURRENT_DOM
    backend_absent = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
        post_reloc_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
    )
    orch_absent = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend_absent,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_absent"),
    )
    res_absent = await orch_absent.run_investigation(UserQuestion(question="간담회 일정"))

    # Relocation performed exactly once, not twice
    assert res_absent.audit.relocation_search_count == 1
    assert len(backend_absent.executed_searches) == 4
    assert res_absent.evidence_package.thread_context_status == "UNAVAILABLE"

    # Case B: Relocated target ambiguous on rematch -> second ThreadReader returns ROW_IDENTITY_AMBIGUOUS
    backend_ambig = RelocationTestSearchBackend(
        query_results={"query_0": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
        post_reloc_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.ROW_IDENTITY_AMBIGUOUS,
        ),
    )
    orch_ambig = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend_ambig,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_ambig"),
    )
    res_ambig = await orch_ambig.run_investigation(UserQuestion(question="간담회 일정"))

    assert res_ambig.audit.relocation_search_count == 1
    assert len(backend_ambig.executed_searches) == 4
    assert res_ambig.evidence_package.thread_context_status == "UNAVAILABLE"


@pytest.mark.anyio
async def test_hard_budget_enforcement(tmp_path):
    """Verify hard budgets: planned <= 3, relocation <= 1, total search <= 4."""
    target_item = EvidenceItem(
        evidence_id="target",
        source="synthetic",
        text="간담회 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    backend = RelocationTestSearchBackend(
        query_results={"query_0": [target_item], "query_1": [target_item], "query_2": [target_item]},
        initial_read_res=ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
        ),
        post_reloc_read_res=ThreadReadResult(replies=(), status=ThreadContextStatus.PARTIAL),
    )
    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_budget"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    assert result.audit.planned_query_count <= 3
    assert result.audit.relocation_search_count <= 1
    assert result.audit.total_slack_search_operations <= 4
    assert result.audit.llm_calls <= 2


@pytest.mark.anyio
async def test_orchestrator_raw_candidate_selection_parity_and_non_mutation(tmp_path):
    """Verify raw search candidate selection parity and candidate object non-mutation across investigation lifecycle."""
    raw_item_a = EvidenceItem(
        evidence_id="raw_a",
        source="synthetic",
        text="26년 하반기 간담회 스레드 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        thread_identifier="1787000000.000001",
        has_thread=True,
        query_hit_count=1,
    )
    raw_item_b = EvidenceItem(
        evidence_id="raw_b",
        source="synthetic",
        text="기타 일반 업무 공지 본문",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000002",
        has_thread=False,
        query_hit_count=1,
    )
    dump_a_before = raw_item_a.model_dump()
    dump_b_before = raw_item_b.model_dump()

    # Query 0 returns both items; Query 1 returns duplicate of A; Query 2 returns B
    backend = RelocationTestSearchBackend(
        query_results={
            "query_0": [raw_item_a, raw_item_b],
            "query_1": [raw_item_a],
            "query_2": [raw_item_b],
        },
        initial_read_res=ThreadReadResult(
            replies=(
                ThreadReplyItem(
                    reply_id="R1",
                    author="User1",
                    text="간담회 세부 일정 안내",
                    timestamp="8월 28일",
                    permalink="https://app.slack.com/archives/C1/p1787000000000010",
                    channel_id="C1",
                    message_ts="1787000000.000010",
                    thread_ts="1787000000.000001",
                ),
            ),
            status=ThreadContextStatus.PARTIAL,
        ),
    )
    orch = InvestigationOrchestrator(
        provider=MockModelProvider(plan_return=_build_test_plan(), analysis_return=_build_test_analysis()),
        search_backend=backend,
        config=OrchestratorConfig(base_output_dir=tmp_path / "inv_parity"),
    )
    result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

    # Backend raw items must remain untouched (non-mutating contract)
    assert raw_item_a.model_dump() == dump_a_before
    assert raw_item_b.model_dump() == dump_b_before

    # Verify parity: item A has 2 hits (q0, q1); item B has 2 hits (q0, q2)
    final_items = result.evidence_package.evidence_items
    assert len(final_items) == 2

    item_a_pkg = next(it for it in final_items if it.message_identifier == raw_item_a.message_identifier)
    item_b_pkg = next(it for it in final_items if it.message_identifier == raw_item_b.message_identifier)

    assert item_a_pkg.query_hit_count == 2
    assert item_b_pkg.query_hit_count == 2
    assert item_a_pkg.evidence_id == "E1"
    assert len(item_a_pkg.thread_replies) == 1



