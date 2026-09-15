"""Comprehensive Unit and Integration Tests for Phase 12.6g: Deterministic ActorScope & SELF Retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from pesu_agent.investigation.models import (
    ActorResolutionStatus,
    ActorScopeKind,
    AnalysisResult,
    ConfirmedFact,
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    SearchQuery,
    TemporalConstraint,
    TemporalResolutionKind,
    TemporalResolutionStatus,
    TemporalStrictness,
    ResolvedActorScope,
    ResolvedTemporalScope,
    UserQuestion,
)
from pesu_agent.investigation.orchestrator import (
    CandidateQueryHit,
    InvestigationOrchestrator,
    InvestigationState,
    InvestigationStopReason,
    OrchestratorConfig,
    SelfAuthorConstraintIntegrityError,
)
from pesu_agent.investigation.payload_builder import ExternalModelPayloadBuilder
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.investigation.selector import EvidenceSelector, get_canonical_evidence_key
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.search.actor import resolve_actor_scope
from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
    ThreadReplyItem,
)


class MockActorModelProvider(ModelProvider):
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


# ==============================================================================
# 1. Deterministic Actor Intent Resolution Tests
# ==============================================================================

def test_actor_scope_explicit_first_person() -> None:
    """명시적 1인칭 표현 -> SELF / RESOLVED (is_implicit=False)."""
    cases = [
        "내가 지난주에 작성한 보고서 찾아줘",
        "나는 어제 무슨 회의 들어갔지?",
        "난 지난주에 어떤 작업을 했는가?",
        "내 업무 진행 내역 정리해줘",
        "제가 이번 달에 올린 PR 확인해주세요",
        "저는 지난주에 무엇을 했습니까?",
        "제 작업 내역 정리 부탁드립니다",
        "나의 지난주 성과 정리",
        "저의 업무 내역 확인",
    ]
    for q in cases:
        scope = resolve_actor_scope(q)
        assert scope.kind == ActorScopeKind.SELF, f"Failed for {q}: {scope}"
        assert scope.status == ActorResolutionStatus.RESOLVED, f"Failed for {q}: {scope}"
        assert scope.is_implicit is False, f"Failed for {q}: {scope}"
        assert scope.source_expression is not None


def test_actor_scope_implicit_personal_retrospective() -> None:
    """암시적 개인 회고/활동 질의 -> SELF / RESOLVED (is_implicit=True)."""
    cases = [
        "지난주에 한 일 정리해줘",
        "지난주에 뭐 했지?",
        "지난주에 뭐 했어?",
        "이번달에 한 일",
        "어제 뭐 했지?",
        "지난주에 무슨 일 했어?",
        "어제 어떤 업무 했지?",
        "어제 진행한 업무 정리해줘",
        "지난주에 작업한 내역 공유해줘",
    ]
    for q in cases:
        scope = resolve_actor_scope(q)
        assert scope.kind == ActorScopeKind.SELF, f"Failed for {q}: {scope}"
        assert scope.status == ActorResolutionStatus.RESOLVED, f"Failed for {q}: {scope}"
        assert scope.is_implicit is True, f"Failed for {q}: {scope}"
        assert scope.source_expression is not None


def test_actor_scope_omitted_subject_topic_questions() -> None:
    """주어 생략 일반 토픽/장애/공지 질문 -> UNSPECIFIED / RESOLVED (is_implicit=False)."""
    cases = [
        "지난주 장애 뭐 있었지?",
        "지난주 공지 뭐였어?",
        "지난주 지점 이슈 정리해줘",
        "이번달 회사에서 무슨 일 있었어?",
        "어제 릴리즈 배포 공지 확인해줘",
        "지난주 주간 점검 결과 알려줘",
        "어제 전체 회의록 어디 있어?",
    ]
    for q in cases:
        scope = resolve_actor_scope(q)
        assert scope.kind == ActorScopeKind.UNSPECIFIED, f"Failed for {q}: {scope}"
        assert scope.status == ActorResolutionStatus.RESOLVED, f"Failed for {q}: {scope}"
        assert scope.is_implicit is False, f"Failed for {q}: {scope}"


def test_actor_scope_unsupported_explicit_non_self_actors() -> None:
    """Mandatory Correction A: 비-SELF 명시적 액터 -> UNRESOLVED (fail closed)."""
    cases_person = [
        ("홍길동이 한 일 정리해줘", ActorScopeKind.NAMED_ACTOR),
        ("김철수님이 작성한 기안서 확인", ActorScopeKind.NAMED_ACTOR),
        ("박팀장이 공유한 일정 알려줘", ActorScopeKind.NAMED_ACTOR),
        ("그사람이 지난주에 뭐 했어?", ActorScopeKind.NAMED_ACTOR),
        ("다른 사람이 작성한 공지", ActorScopeKind.NAMED_ACTOR),
    ]
    for q, expected_kind in cases_person:
        scope = resolve_actor_scope(q)
        assert scope.kind == expected_kind, f"Failed for {q}: {scope}"
        assert scope.status == ActorResolutionStatus.UNRESOLVED, f"Failed for {q}: {scope}"

    cases_group = [
        ("우리 팀이 지난주에 한 일", ActorScopeKind.GROUP),
        ("개발팀에서 작성한 문서", ActorScopeKind.GROUP),
        ("운영팀이 진행한 작업", ActorScopeKind.GROUP),
    ]
    for q, expected_kind in cases_group:
        scope = resolve_actor_scope(q)
        assert scope.kind == expected_kind, f"Failed for {q}: {scope}"
        assert scope.status == ActorResolutionStatus.UNRESOLVED, f"Failed for {q}: {scope}"


def test_actor_scope_ambiguous_actors() -> None:
    """상충하는 복수 액터 검출 -> AMBIGUOUS (fail closed, never fallback to SELF)."""
    cases = [
        "내가 홍길동이 한 일 정리해줘",
        "제가 우리 팀이 진행한 업무 확인",
        "나는 다른 사람이 작성한 보고서",
    ]
    for q in cases:
        scope = resolve_actor_scope(q)
        assert scope.status == ActorResolutionStatus.AMBIGUOUS, f"Failed for {q}: {scope}"
        assert scope.kind == ActorScopeKind.UNSPECIFIED, f"Failed for {q}: {scope}"


# ==============================================================================
# 2. Query Composition & Collision Guard Tests
# ==============================================================================

@pytest.mark.anyio
async def test_collision_guard_rejects_planner_author_operator(tmp_path: Path) -> None:
    """Mandatory Correction B: Planner 쿼리에 from: 포함 시 fail-closed 거부."""
    forbidden_queries = [
        "from:someone 업무 진행",
        "From:someone 업무 진행",
        '"from:someone 업무"',
        "긴급 이슈 from:user1",
    ]

    for f_q in forbidden_queries:
        plan = InvestigationPlan(
            objective="업무 확인",
            search_queries=[SearchQuery(query=f_q, purpose="검색")],
            questions_to_resolve=["무엇을 했는가?"],
        )
        analysis = AnalysisResult(
            summary="완료",
            confirmed_facts=[],
            disputed_points=[],
            contradictions=[],
            assessment="완료",
            uncertainty="없음",
            missing_information=[],
            suggested_followups=[],
        )
        provider = MockActorModelProvider(plan_return=plan, analysis_return=analysis)
        backend = SyntheticSearchBackend()
        orchestrator = InvestigationOrchestrator(
            provider=provider,
            search_backend=backend,
            config=OrchestratorConfig(base_output_dir=tmp_path / "investigations"),
        )

        res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
        assert res.state == InvestigationState.ERROR
        assert res.stop_reason == InvestigationStopReason.ERROR
        assert "Planner semantic query violates actor authority contract" in (res.error_message or "")


@pytest.mark.anyio
async def test_query_composition_injects_from_me_for_self(tmp_path: Path) -> None:
    """SELF 조사 시 exactly one from:me 가 결정론적으로 주입되고, non-SELF에서는 주입되지 않음."""
    recorded_queries: list[str] = []

    class SpySearchBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            recorded_queries.append(query.query)
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="나",
                    author_id="U07Q7006G3Z",
                    text="작업 내용",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_1",
                )
            ]

    # Case 1: SELF query -> from:me injected
    plan_self = InvestigationPlan(
        objective="업무 확인",
        search_queries=[
            SearchQuery(query="업무 진행 완료", purpose="1차"),
            SearchQuery(query="프로젝트 현황", purpose="2차"),
        ],
        questions_to_resolve=["무엇을 했는가?"],
    )
    analysis = AnalysisResult(
        summary="완료",
        confirmed_facts=[],
        disputed_points=[],
        contradictions=[],
        assessment="완료",
        uncertainty="없음",
        missing_information=[],
        suggested_followups=[],
    )
    provider_self = MockActorModelProvider(plan_return=plan_self, analysis_return=analysis)
    backend_self = SpySearchBackend()
    orchestrator_self = InvestigationOrchestrator(
        provider=provider_self,
        search_backend=backend_self,
        config=OrchestratorConfig(base_output_dir=tmp_path / "self"),
    )
    await orchestrator_self.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))

    assert len(recorded_queries) == 2
    assert recorded_queries[0] == "from:me 업무 진행 완료"
    assert recorded_queries[1] == "from:me 프로젝트 현황"
    assert recorded_queries[0].count("from:me") == 1

    # Case 2: Non-SELF query -> NO from:me injected
    recorded_queries.clear()
    plan_topic = InvestigationPlan(
        objective="장애 확인",
        search_queries=[SearchQuery(query="시스템 장애", purpose="1차")],
        questions_to_resolve=["장애 발생 여부?"],
    )
    provider_topic = MockActorModelProvider(plan_return=plan_topic, analysis_return=analysis)
    backend_topic = SpySearchBackend()
    orchestrator_topic = InvestigationOrchestrator(
        provider=provider_topic,
        search_backend=backend_topic,
        config=OrchestratorConfig(base_output_dir=tmp_path / "topic"),
    )
    await orchestrator_topic.run_investigation(question=UserQuestion(question="지난주 장애 뭐 있었지?"))

    assert len(recorded_queries) == 1
    assert recorded_queries[0] == "시스템 장애"
    assert "from:me" not in recorded_queries[0]


# ==============================================================================
# 3. Canonical Author-ID Validation & Cross-Query Invariants
# ==============================================================================

@pytest.mark.anyio
async def test_self_author_integrity_missing_author_id_fails_closed(tmp_path: Path) -> None:
    """SELF 검색 결과 중 author_id가 누락된 row가 있으면 fail closed."""
    class MissingIdBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="나",
                    author_id=None,  # Missing!
                    text="작업 내용",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_1",
                )
            ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[SearchQuery(query="업무 진행", purpose="검색")],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=MissingIdBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "missing_id"),
    )

    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Missing or malformed canonical author_id" in (res.error_message or "")


@pytest.mark.anyio
async def test_self_author_integrity_malformed_author_id_fails_closed(tmp_path: Path) -> None:
    """Mandatory Correction C: Canonical Slack member-ID 패턴(^U[A-Z0-9]{8,14}$) 불일치 시 fatal."""
    class MalformedIdBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="나",
                    author_id="NOT_A_VALID_SLACK_ID",  # Malformed!
                    text="작업 내용",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_1",
                )
            ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[SearchQuery(query="업무 진행", purpose="검색")],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=MalformedIdBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "malformed_id"),
    )

    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Missing or malformed canonical author_id" in (res.error_message or "")


@pytest.mark.anyio
async def test_self_author_integrity_multiple_author_ids_in_single_query_fails_closed(tmp_path: Path) -> None:
    """단일 SELF 쿼리 결과에 복수 author ID 포함 시 fatal."""
    class MultiIdBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="사용자A",
                    author_id="U07Q7006G3Z",
                    text="작업 내용 1",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_1",
                ),
                EvidenceItem(
                    evidence_id="E2",
                    source="slack",
                    author="사용자B",
                    author_id="U08ABCDEF12",
                    text="작업 내용 2",
                    timestamp="2026-09-01T10:05:00+09:00",
                    message_identifier="msg_2",
                ),
            ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[SearchQuery(query="업무 진행", purpose="검색")],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=MultiIdBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "multi_id"),
    )

    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Multiple author IDs in single SELF query" in (res.error_message or "")


@pytest.mark.anyio
async def test_self_author_integrity_cross_query_mismatch_fails_closed(tmp_path: Path) -> None:
    """이후 SELF 쿼리의 author ID가 최초 부트스트랩된 investigation-local SELF ID와 불일치 시 fatal."""
    call_count = 0

    class CrossQueryMismatchBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [
                    EvidenceItem(
                        evidence_id="E1",
                        source="slack",
                        author="나",
                        author_id="U07Q7006G3Z",
                        text="1차 쿼리 작업",
                        timestamp="2026-09-01T10:00:00+09:00",
                        message_identifier="msg_1",
                    )
                ]
            else:
                return [
                    EvidenceItem(
                        evidence_id="E2",
                        source="slack",
                        author="타인",
                        author_id="U09OTHER999",  # Cross-query mismatch!
                        text="2차 쿼리 작업",
                        timestamp="2026-09-01T11:00:00+09:00",
                        message_identifier="msg_2",
                    )
                ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[
            SearchQuery(query="1차 검색", purpose="검색1"),
            SearchQuery(query="2차 검색", purpose="검색2"),
        ],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=CrossQueryMismatchBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "cross_query"),
    )

    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Cross-query author ID mismatch" in (res.error_message or "")


# ==============================================================================
# 4. Ordering: Author Integrity PRECEDES Temporal Filtering
# ==============================================================================

@pytest.mark.anyio
async def test_author_integrity_precedes_temporal_filtering(tmp_path: Path) -> None:
    """시계열 범위를 벗어난 다른 사람의 글이라도 temporal 필터링 전에 author 무결성 검증으로 fail closed 되어야 함."""
    class OutOfRangeOtherAuthorBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="synthetic",
                    author="타인",
                    author_id="NOT_A_VALID_AUTHOR_ID",  # Malformed author ID!
                    # Timed in 2025 (completely out of range for 지난주)
                    timestamp="2025-01-01T10:00:00+09:00",
                    text="오래된 타인 메시지",
                    message_identifier="msg_old",
                )
            ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[SearchQuery(query="업무 내역", purpose="검색")],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=OutOfRangeOtherAuthorBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "ordering"),
    )

    # Must fail closed on author integrity BEFORE temporal filter can drop the row!
    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Missing or malformed canonical author_id" in (res.error_message or "")


# ==============================================================================
# 5. Privacy Regression Locks across Real Artifact Files (Mandatory Correction G)
# ==============================================================================

@pytest.mark.anyio
async def test_privacy_regression_canonical_id_leak_audit(tmp_path: Path) -> None:
    """Mandatory Correction G: Canonical Slack ID(e.g. U07Q7006G3Z)가 모든 영속 아티팩트 및 LLM 페이로드에서 제외됨을 검증."""
    secret_author_id = "U07Q7006G3Z"

    # Step 1: Pydantic serialization check
    ev_item = EvidenceItem(
        evidence_id="E1",
        source="slack",
        author="테스터",
        author_id=secret_author_id,
        author_relation="SELF",
        timestamp="2026-09-01T10:00:00+09:00",
        text="테스트 본문",
        message_identifier="msg_test",
    )
    dumped_dict = ev_item.model_dump()
    assert "author_id" not in dumped_dict
    assert secret_author_id not in json.dumps(dumped_dict)
    assert secret_author_id not in repr(ev_item)

    sr = SlackSearchResult(
        result_index=1,
        query="from:me 테스트",
        author="테스터",
        author_id=secret_author_id,
        text="테스트 본문",
        message_fingerprint="fp1",
    )
    assert "author_id" not in sr.model_dump()
    assert secret_author_id not in repr(sr)

    # Step 2: Full orchestration and inspect saved JSON artifacts
    class ValidSelfBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            return [ev_item]

    plan = InvestigationPlan(
        objective="본인 업무 확인",
        search_queries=[SearchQuery(query="본인 업무", purpose="검색")],
        questions_to_resolve=["작업 내용"],
    )
    analysis = AnalysisResult(
        summary="사용자가 지난주에 업무를 성공적으로 완료했습니다.",
        confirmed_facts=[ConfirmedFact(statement="업무 완료", supporting_evidence_ids=["E1"])],
        disputed_points=[],
        contradictions=[],
        assessment="정상",
        uncertainty="없음",
        missing_information=[],
        suggested_followups=[],
    )
    provider = MockActorModelProvider(plan_return=plan, analysis_return=analysis)
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        search_backend=ValidSelfBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "privacy_test"),
    )
    result = await orchestrator.run_investigation(
        question=UserQuestion(question="지난주에 한 일 정리해줘"),
        investigation_id="inv_privacy_check",
    )

    # Step 3: Inspect all created files on disk
    inv_dir = tmp_path / "privacy_test" / "inv_privacy_check"
    assert inv_dir.exists()

    json_files = list(inv_dir.glob("*.json"))
    assert len(json_files) >= 3  # plan.json, search_round_01.json, evidence_package.json, analysis.json

    for jf in json_files:
        content = jf.read_text(encoding="utf-8")
        assert secret_author_id not in content, f"Leak found in {jf.name}: {content}"

    # Step 4: Inspect LLM messages generated by ExternalModelPayloadBuilder
    pkg = result.evidence_package
    assert pkg is not None
    messages = ExternalModelPayloadBuilder.build_analysis_payload(
        question=UserQuestion(question="지난주에 한 일 정리해줘"),
        package=pkg,
        allow_real_slack_external_analysis=True,
    )
    for msg in messages:
        assert secret_author_id not in msg.content


# ==============================================================================
# 6. Relocation Preserves Author Constraint (Mandatory Correction D)
# ==============================================================================

@pytest.mark.anyio
async def test_self_relocation_preserves_author_constraint(tmp_path: Path) -> None:
    """Mandatory Correction D: SELF 후보의 relocation 재실행 시 from:me 연산자가 보존됨."""
    relocation_executed_queries: list[str] = []

    class RelocationTrackingBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            relocation_executed_queries.append(query.query)
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="나",
                    author_id="U07Q7006G3Z",
                    text="루트 스레드 메시지",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_thread_root",
                    has_thread=True,
                    thread_identifier="1725152400.000100",
                )
            ]

    backend = RelocationTrackingBackend()
    plan = InvestigationPlan(
        objective="스레드 업무 확인",
        search_queries=[SearchQuery(query="스레드 업무", purpose="검색")],
        questions_to_resolve=["스레드 내용 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=backend,
        config=OrchestratorConfig(
            base_output_dir=tmp_path / "relocation",
            reference_time=datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
        ),
    )

    # Mock ThreadReader: First read returns TARGET_ROW_NOT_IN_CURRENT_DOM, second read succeeds
    call_idx = 0
    async def mock_read_candidate_thread(target_candidate):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return ThreadReadResult(
                status="UNAVAILABLE",
                replies=[],
                failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
            )
        return ThreadReadResult(
            status="PARTIAL",
            replies=[
                ThreadReplyItem(
                    reply_id="R1",
                    text="댓글 본문",
                    permalink="https://slack.com/archives/C1/p1",
                    channel_id="C1",
                    message_ts="1725152410.000200",
                    thread_ts="1725152400.000100",
                )
            ],
        )

    orchestrator._read_candidate_thread = mock_read_candidate_thread

    await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))

    # Verify that relocation ran and the replayed query contained exactly one from:me
    assert len(relocation_executed_queries) == 2  # 1 initial + 1 relocation
    initial_q = relocation_executed_queries[0]
    relocation_q = relocation_executed_queries[1]

    assert initial_q == "from:me 스레드 업무"
    assert relocation_q == "from:me 스레드 업무"
    assert relocation_q.count("from:me") == 1


# ==============================================================================
# 7. Active Team Binding (Mandatory Correction F)
# ==============================================================================

@pytest.mark.anyio
async def test_active_team_binding_change_fails_closed(tmp_path: Path) -> None:
    """Mandatory Correction F: 조사 도중 활성 팀 ID가 변경되거나 누락되면 fail closed."""
    team_state = "T32QA15GC"

    class TeamChangingBackend(SyntheticSearchBackend):
        def get_active_team_id(self) -> str | None:
            return team_state

        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            nonlocal team_state
            # Team changes after first search
            team_state = "T99ANOTHER_TEAM"
            return [
                EvidenceItem(
                    evidence_id="E1",
                    source="slack",
                    author="나",
                    author_id="U07Q7006G3Z",
                    text="작업 내용",
                    timestamp="2026-09-01T10:00:00+09:00",
                    message_identifier="msg_1",
                )
            ]

    plan = InvestigationPlan(
        objective="업무 확인",
        search_queries=[
            SearchQuery(query="1차", purpose="1차"),
            SearchQuery(query="2차", purpose="2차"),
        ],
        questions_to_resolve=["작업 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=TeamChangingBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "team_change"),
    )

    res = await orchestrator.run_investigation(question=UserQuestion(question="지난주에 한 일 정리해줘"))
    assert res.state == InvestigationState.ERROR
    assert res.stop_reason == InvestigationStopReason.ERROR
    assert "Active team integrity failure" in (res.error_message or "")


# ==============================================================================
# 8. Thread Reply Containment for SELF (Mandatory Correction E)
# ==============================================================================

def test_unqualified_thread_replies_excluded_from_self_payload() -> None:
    """Mandatory Correction E: SELF 조사에서는 자격 증명되지 않은 스레드 댓글이 LLM 프롬프트에 포함되지 않음."""
    ev_item = EvidenceItem(
        evidence_id="E1",
        source="slack",
        author="나",
        author_id="U07Q7006G3Z",
        author_relation="SELF",
        timestamp="2026-09-01T10:00:00+09:00",
        text="내가 작성한 루트 업무 메시지",
        message_identifier="msg_root",
        thread_replies=[
            ThreadReplyItem(
                reply_id="R1",
                author="동료A",
                text="동료A가 작성한 확인 댓글",
                permalink="https://slack.com/archives/C1/p2",
                channel_id="C1",
                message_ts="1725152410.000200",
                thread_ts="1725152400.000100",
            )
        ],
    )

    # Case A: SELF scope -> reply excluded
    pkg_self = EvidencePackage(
        package_id="pkg_self",
        query="지난주에 한 일",
        evidence_items=[ev_item],
        total_items=1,
        created_at="2026-09-07T12:00:00Z",
        is_synthetic=True,
        actor_scope=ResolvedActorScope(
            kind=ActorScopeKind.SELF,
            status=ActorResolutionStatus.RESOLVED,
            is_implicit=True,
        ),
    )
    messages_self = ExternalModelPayloadBuilder.build_analysis_payload(
        question=UserQuestion(question="지난주에 한 일 정리해줘"),
        package=pkg_self,
        allow_real_slack_external_analysis=True,
    )
    prompt_self = messages_self[1].content
    assert "내가 작성한 루트 업무 메시지" in prompt_self
    assert "동료A가 작성한 확인 댓글" not in prompt_self
    assert "[스레드 댓글 목록]" not in prompt_self
    assert "[조사 대상 액터 범위]" in prompt_self
    assert "SELF (사용자 본인)" in prompt_self

    # Case B: Non-SELF scope -> reply included
    pkg_topic = EvidencePackage(
        package_id="pkg_topic",
        query="장애 공지",
        evidence_items=[ev_item],
        total_items=1,
        created_at="2026-09-07T12:00:00Z",
        is_synthetic=True,
        actor_scope=ResolvedActorScope(
            kind=ActorScopeKind.UNSPECIFIED,
            status=ActorResolutionStatus.RESOLVED,
            is_implicit=False,
        ),
    )
    messages_topic = ExternalModelPayloadBuilder.build_analysis_payload(
        question=UserQuestion(question="장애 공지 확인해줘"),
        package=pkg_topic,
        allow_real_slack_external_analysis=True,
    )
    prompt_topic = messages_topic[1].content
    assert "동료A가 작성한 확인 댓글" in prompt_topic
    assert "[스레드 댓글 목록]" in prompt_topic


# ==============================================================================
# 9. Mandatory Correction A: Unsupported/Ambiguous Actors Fail Closed (No Search)
# ==============================================================================

@pytest.mark.anyio
async def test_unsupported_explicit_actor_fails_closed_without_search(tmp_path: Path) -> None:
    """Mandatory Correction A: 비-SELF 명시적 액터 -> UNRESOLVED fail-closed, 검색 쿼리 실행 0회 (UNSUPPORTED_EXPLICIT_ACTOR_FALLS_BACK_TO_UNSPECIFIED_SEARCH = false)."""
    recorded_searches: list[str] = []

    class SpyBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            recorded_searches.append(query.query)
            return []

    plan = InvestigationPlan(
        objective="타인 업무 확인",
        search_queries=[SearchQuery(query="홍길동 업무", purpose="검색")],
        questions_to_resolve=["홍길동이 한 일"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=SpyBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "unsupported_actor"),
    )

    result = await orchestrator.run_investigation(
        question=UserQuestion(question="홍길동이 한 일 정리해줘")
    )

    assert result.state == InvestigationState.COMPLETED
    assert result.stop_reason == InvestigationStopReason.ANALYSIS_INCONCLUSIVE
    assert result.evidence_package is not None
    assert result.evidence_package.actor_scope.status == ActorResolutionStatus.UNRESOLVED
    assert result.evidence_package.actor_scope.kind == ActorScopeKind.NAMED_ACTOR
    assert len(recorded_searches) == 0
    assert result.audit.queries_executed == 0


@pytest.mark.anyio
async def test_ambiguous_actor_fails_closed_without_search(tmp_path: Path) -> None:
    """Mandatory Correction A: 상충하는 액터 -> AMBIGUOUS fail-closed, 검색 쿼리 실행 0회."""
    recorded_searches: list[str] = []

    class SpyBackend(SyntheticSearchBackend):
        async def search(self, query: SearchQuery, **kwargs) -> list[EvidenceItem]:
            recorded_searches.append(query.query)
            return []

    plan = InvestigationPlan(
        objective="상충 액터 확인",
        search_queries=[SearchQuery(query="내가 홍길동", purpose="검색")],
        questions_to_resolve=["상충 확인"],
    )
    analysis = AnalysisResult(
        summary="완료", confirmed_facts=[], disputed_points=[], contradictions=[],
        assessment="완료", uncertainty="없음", missing_information=[], suggested_followups=[],
    )
    orchestrator = InvestigationOrchestrator(
        provider=MockActorModelProvider(plan_return=plan, analysis_return=analysis),
        search_backend=SpyBackend(),
        config=OrchestratorConfig(base_output_dir=tmp_path / "ambiguous_actor"),
    )

    result = await orchestrator.run_investigation(
        question=UserQuestion(question="내가 홍길동이 한 일 정리해줘")
    )

    assert result.state == InvestigationState.COMPLETED
    assert result.stop_reason == InvestigationStopReason.ANALYSIS_INCONCLUSIVE
    assert result.evidence_package is not None
    assert result.evidence_package.actor_scope.status == ActorResolutionStatus.AMBIGUOUS
    assert len(recorded_searches) == 0
    assert result.audit.queries_executed == 0
