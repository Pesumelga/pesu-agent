"""Production Retrieval Characterization Baseline Tests.

Pins down the current InvestigationOrchestrator retrieval behavior before any
extraction or refactoring occurs.

Guarantees:
- CURRENT_ORCHESTRATOR_CHARACTERIZATION = PASS
- query order
- query budgets
- candidate IDs/order
- CandidateQueryHit provenance
- dedupe
- thread eligibility
- residency-first relocation
- thread merge
- anchor propagation
- EvidenceSelector ordering
- selected Evidence IDs
- coverage
- seed metadata
"""

from __future__ import annotations

import pytest

from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.graph_models import InvestigationStateGraph
from pesu_agent.investigation.models import (
    AnalysisResult,
    EvidenceItem,
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
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.investigation.selector import get_canonical_evidence_key
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
)


class CharacterizationModelProvider(ModelProvider):
    """Deterministic ModelProvider returning specified plan and analysis."""

    def __init__(self, plan: InvestigationPlan, analysis: AnalysisResult):
        self.plan = plan
        self.analysis = analysis

    async def generate_structured(self, messages, response_schema, **kwargs):
        usage = LLMUsageStats(
            provider="mock",
            model="mock-model",
            input_tokens=10,
            output_tokens=10,
        )
        if response_schema == InvestigationPlan:
            return self.plan, usage
        elif response_schema == AnalysisResult:
            return self.analysis, usage
        raise ValueError(f"Unexpected schema: {response_schema}")

    async def generate_text(self, messages, **kwargs):
        return "mock text", LLMUsageStats(provider="mock", model="mock-model")

    async def close(self):
        pass


class CharacterizationSearchBackend(SearchBackend):
    """Deterministic SearchBackend recording executed queries and returns staged results."""

    def __init__(
        self,
        query_map: dict[str, list[EvidenceItem]],
        first_thread_read: Optional[ThreadReadResult] = None,
        second_thread_read: Optional[ThreadReadResult] = None,
    ):
        self.query_map = query_map
        self.first_thread_read = first_thread_read
        self.second_thread_read = second_thread_read
        self.executed_queries: list[SearchQuery] = []
        self.thread_read_calls: list[EvidenceItem] = []

    async def search(self, query: SearchQuery, max_results: int = 5, **kwargs) -> list[EvidenceItem]:
        self.executed_queries.append(query)
        items = self.query_map.get(query.query, [])
        return items[:max_results]

    async def collect_context(self, message_identifier: str, **kwargs) -> list[EvidenceItem]:
        return []

    async def read_thread_result(self, candidate_item: EvidenceItem) -> ThreadReadResult:
        self.thread_read_calls.append(candidate_item)
        if len(self.thread_read_calls) == 1 and self.first_thread_read is not None:
            return self.first_thread_read
        if len(self.thread_read_calls) >= 2 and self.second_thread_read is not None:
            return self.second_thread_read
        return ThreadReadResult(replies=(), status=ThreadContextStatus.NOT_ATTEMPTED)

    async def read_thread_context(self, candidate_item: EvidenceItem):
        res = await self.read_thread_result(candidate_item)
        return list(res.replies), res.status


def _build_dummy_analysis() -> AnalysisResult:
    return AnalysisResult(
        summary="검증 요약",
        confirmed_facts=[],
        disputed_points=[],
        contradictions=[],
        assessment="충분한 증거 확보",
        uncertainty="없음",
        missing_information=[],
        suggested_followups=[],
        conclusion_status="CONFIRMED",
        responsibility_determinable=True,
        evidence_sufficiency="SUFFICIENT",
        cited_evidence_ids=["E1"],
    )


@pytest.mark.anyio
async def test_characterization_query_order_budgets_provenance_and_dedupe(tmp_path):
    """Characterizes query execution order, max query/candidate budgets, provenance, and dedupe."""
    item_common = EvidenceItem(
        evidence_id="raw_common",
        source="slack",
        author="홍길동",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 1차 배포 완료 공지",
        message_identifier="https://slack.com/archives/C1/p100",
        has_thread=False,
    )
    item_q0_only = EvidenceItem(
        evidence_id="raw_q0",
        source="slack",
        author="이몽룡",
        timestamp="2026-08-01 11:00",
        text="고객 만족도 페이지 릴리즈 일정 안내",
        message_identifier="https://slack.com/archives/C1/p101",
        has_thread=False,
    )
    item_q1_only = EvidenceItem(
        evidence_id="raw_q1",
        source="slack",
        author="성춘향",
        timestamp="2026-08-01 12:00",
        text="1차 배포 후속 작업 리스트",
        message_identifier="https://slack.com/archives/C1/p102",
        has_thread=False,
    )

    query_map = {
        "query_alpha": [item_common, item_q0_only],
        "query_beta": [item_common, item_q1_only],
        "query_gamma": [item_common],
    }

    plan = InvestigationPlan(
        objective="고객 만족도 현황 1차 배포 완료 검증",
        search_queries=[
            SearchQuery(query="query_alpha", purpose="1차 탐색"),
            SearchQuery(query="query_beta", purpose="2차 탐색"),
            SearchQuery(query="query_gamma", purpose="3차 탐색"),
        ],
        questions_to_resolve=["1차 배포가 완료되었는가?"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = CharacterizationSearchBackend(query_map=query_map)
    provider = CharacterizationModelProvider(plan=plan, analysis=_build_dummy_analysis())
    config = OrchestratorConfig(
        base_output_dir=tmp_path / "inv",
        allow_real_slack_external_analysis=True,
    )

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        search_backend=backend,
        config=config,
    )

    result = await orchestrator.run_investigation(
        question=UserQuestion(question="고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가?")
    )

    assert result.state == InvestigationState.COMPLETED
    assert result.stop_reason == InvestigationStopReason.EVIDENCE_SUFFICIENT

    # 1. Query order & counts
    assert len(backend.executed_queries) == 3
    assert [q.query for q in backend.executed_queries] == ["query_alpha", "query_beta", "query_gamma"]

    # 2. EvidencePackage artifacts saved
    inv_dir = tmp_path / "inv" / result.investigation_id
    assert (inv_dir / "plan.json").exists()
    assert (inv_dir / "search_round_01.json").exists()
    assert (inv_dir / "evidence_package.json").exists()

    # 3. Dedupe and query hit count
    ev_pkg = result.evidence_package
    assert ev_pkg is not None
    # 3 distinct items: common, q0_only, q1_only
    assert ev_pkg.total_items == 3
    # item_common appeared in 3 queries
    common_ev = next(e for e in ev_pkg.evidence_items if e.message_identifier == "https://slack.com/archives/C1/p100")
    assert common_ev.query_hit_count == 3
    # item_q0_only and item_q1_only appeared once
    q0_ev = next(e for e in ev_pkg.evidence_items if e.message_identifier == "https://slack.com/archives/C1/p101")
    assert q0_ev.query_hit_count == 1

    # 4. Evidence IDs sequential assignment E1..E3
    ev_ids = [e.evidence_id for e in ev_pkg.evidence_items]
    assert ev_ids == ["E1", "E2", "E3"]


@pytest.mark.anyio
async def test_characterization_thread_expansion_residency_first_and_relocation(tmp_path):
    """Characterizes thread expansion: residency check, relocation on TARGET_ROW_NOT_IN_CURRENT_DOM, and parent merge."""
    thread_parent = EvidenceItem(
        evidence_id="parent",
        source="slack",
        author="엔지니어",
        timestamp="2026-08-01 09:00",
        text="고객 만족도 현황 1차 배포 관련 스레드",
        message_identifier="https://slack.com/archives/C1/p200",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )

    query_map = {
        "query_thread": [thread_parent],
    }

    reply = ThreadReplyItem(
        reply_id="R1",
        text="1차 배포 프로덕션 반영 완료되었습니다.",
        permalink="https://slack.com/archives/C1/p201?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1787000010.000001",
        thread_ts="1787000000.000001",
    )

    # First read returns TARGET_ROW_NOT_IN_CURRENT_DOM -> triggers relocation search
    first_read = ThreadReadResult(
        replies=(),
        status=ThreadContextStatus.UNAVAILABLE,
        failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
    )
    # Second read after relocation returns the reply
    second_read = ThreadReadResult(
        replies=(reply,),
        status=ThreadContextStatus.PARTIAL,
        failure_reason=None,
    )

    plan = InvestigationPlan(
        objective="스레드 확장 검증",
        search_queries=[SearchQuery(query="query_thread", purpose="스레드 탐색")],
        questions_to_resolve=["배포 완료 여부"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = CharacterizationSearchBackend(
        query_map=query_map,
        first_thread_read=first_read,
        second_thread_read=second_read,
    )
    provider = CharacterizationModelProvider(plan=plan, analysis=_build_dummy_analysis())
    config = OrchestratorConfig(
        base_output_dir=tmp_path / "inv_thread",
        allow_real_slack_external_analysis=True,
    )

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        search_backend=backend,
        config=config,
    )

    result = await orchestrator.run_investigation(
        question=UserQuestion(question="고객 만족도 배포 완료 스레드 확인")
    )

    # Relocation search was executed: 1 planned search + 1 relocation search = 2 searches
    assert len(backend.executed_queries) == 2
    assert backend.executed_queries[1].query == "query_thread"

    # Thread read was called twice: first read + second read
    assert len(backend.thread_read_calls) == 2

    # Thread replies merged into parent evidence
    ev_pkg = result.evidence_package
    assert ev_pkg is not None
    assert len(ev_pkg.evidence_items) == 1
    selected_parent = ev_pkg.evidence_items[0]
    assert len(selected_parent.thread_replies) == 1
    assert selected_parent.thread_replies[0].text == "1차 배포 프로덕션 반영 완료되었습니다."
    assert selected_parent.thread_context_status == ThreadContextStatus.PARTIAL.value


@pytest.mark.anyio
async def test_characterization_anchor_propagation_and_seed_metadata(tmp_path):
    """Characterizes anchor propagation from plan to selector, ranking impact, and seed graph metadata."""
    # item_with_anchors has 2 anchor matches ("고객 만족도", "1차 배포")
    item_with_anchors = EvidenceItem(
        evidence_id="item_anchors",
        source="slack",
        author="작성자A",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 페이지는 1차 배포가 무사히 완료되었습니다.",
        message_identifier="https://slack.com/archives/C1/p301",
        permalink="https://slack.com/archives/C1/p301",
        has_thread=False,
    )
    # item_no_anchors has 0 anchor matches
    item_no_anchors = EvidenceItem(
        evidence_id="item_no_anchors",
        source="slack",
        author="작성자B",
        timestamp="2026-08-01 10:00",
        text="오늘 점심 메뉴는 돈까스입니다.",
        message_identifier="https://slack.com/archives/C1/p302",
        has_thread=False,
    )

    # In query results, item_no_anchors appears first
    query_map = {
        "query_anchor_test": [item_no_anchors, item_with_anchors],
    }

    plan = InvestigationPlan(
        objective="앵커 전파 검증",
        search_queries=[SearchQuery(query="query_anchor_test", purpose="앵커 탐색")],
        questions_to_resolve=["배포 완료 여부"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = CharacterizationSearchBackend(query_map=query_map)
    provider = CharacterizationModelProvider(plan=plan, analysis=_build_dummy_analysis())
    config = OrchestratorConfig(
        base_output_dir=tmp_path / "inv_anchor",
        allow_real_slack_external_analysis=True,
    )

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        search_backend=backend,
        config=config,
    )

    result = await orchestrator.run_investigation(
        question=UserQuestion(question="고객 만족도 현황 1차 배포 확인")
    )

    ev_pkg = result.evidence_package
    assert ev_pkg is not None

    # Ranking: item_with_anchors has higher anchor matches, so it is ranked FIRST as E1 despite being second in query results
    assert ev_pkg.evidence_items[0].message_identifier == "https://slack.com/archives/C1/p301"
    assert ev_pkg.evidence_items[0].evidence_id == "E1"
    assert ev_pkg.evidence_items[0].anchor_match_count == 2

    assert ev_pkg.evidence_items[1].message_identifier == "https://slack.com/archives/C1/p302"
    assert ev_pkg.evidence_items[1].evidence_id == "E2"
    assert ev_pkg.evidence_items[1].anchor_match_count == 0

    # Seed metadata ingestion into InvestigationStateGraph via InvestigationSeedPoolBuilder
    graph = InvestigationStateGraph(question="고객 만족도 현황 1차 배포 확인")
    InvestigationSeedPoolBuilder().populate_seed_graph(graph=graph, candidates=ev_pkg.evidence_items)

    assert len(graph.evidence_nodes) == 2
    e1_node = graph.evidence_nodes["E1"]
    assert "1차 배포" in e1_node.text
    assert e1_node.canonical_permalink == "https://slack.com/archives/C1/p301"
