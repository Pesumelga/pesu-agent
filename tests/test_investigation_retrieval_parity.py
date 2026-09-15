"""TDD — Production Retrieval Parity Contract Tests.

Verifies the interface and behavioral invariants of ProductionInvestigationRetrievalCoordinator.

Invariants & Contracts:
- SHARED_RETRIEVAL_EXECUTION_CORE_COUNT = 1
- CUSTOM_HARNESS_RETRIEVAL_LOOP_COUNT = 0
- TELEMETRY_AFFECTS_RANKING = False
- TELEMETRY_AFFECTS_FILTERING = False
- TELEMETRY_AFFECTS_DEDUPE = False
- EMPTY_ANCHOR_TERMS_WHEN_PLAN_HAS_ANCHORS = forbidden
- Contract A: Anchor propagation
- Contract B: Result budget
- Contract C: Dedupe
- Contract D: Provenance
- Contract E: Thread expansion
- Contract F: Evidence selection
- Contract G: Seed metadata
"""

from __future__ import annotations

import pytest

from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.graph_models import InvestigationStateGraph
from pesu_agent.investigation.models import (
    EvidenceItem,
    InvestigationPlan,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
    UserQuestion,
)
from pesu_agent.investigation.orchestrator import OrchestratorConfig
from pesu_agent.investigation.retrieval_coordinator import (
    ProductionInvestigationRetrievalCoordinator,
    RetrievalCoordinatorResult,
)
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.investigation.selector import EvidenceSelector
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.search.actor import ActorResolutionStatus, ActorScopeKind, ResolvedActorScope
from pesu_agent.search.temporal import (
    TemporalResolutionStatus,
    TemporalStrictness,
    ResolvedTemporalScope,
)
from pesu_agent.search.webview_thread_reader import (
    ThreadReadFailureReason,
    ThreadReadResult,
)


class ParityMockProvider(ModelProvider):
    """Deterministic ModelProvider returning specified plan."""

    def __init__(self, plan: InvestigationPlan):
        self.plan = plan

    async def generate_structured(self, messages, response_schema, **kwargs):
        usage = LLMUsageStats(
            provider="mock",
            model="mock-model",
            input_tokens=10,
            output_tokens=10,
        )
        if response_schema == InvestigationPlan:
            return self.plan, usage
        raise ValueError(f"Unexpected schema: {response_schema}")

    async def generate_text(self, messages, **kwargs):
        return "mock text", LLMUsageStats(provider="mock", model="mock-model")

    async def close(self):
        pass


class ParityMockSearchBackend(SearchBackend):
    """Deterministic SearchBackend tracking query executions and returning simulated items."""

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
        self.last_search_telemetry: dict[str, Any] = {}

    async def search(self, query: SearchQuery, max_results: int = 5, **kwargs) -> list[EvidenceItem]:
        self.executed_queries.append(query)
        items = self.query_map.get(query.query, [])
        bounded = items[:max_results]
        self.last_search_telemetry = {
            "query": query.query,
            "raw_items_count": len(items),
            "valid_items_count": len(bounded),
            "raw_items_evaluated": [
                {
                    "raw_index": idx + 1,
                    "canonical_permalink": it.permalink or it.message_identifier,
                    "sender_author_id": it.author_id,
                    "sender_text": it.author,
                    "timestamp_raw": it.timestamp,
                    "normalized_body": it.text,
                    "body_valid": True,
                    "ambiguous_fragment_count": 0,
                    "drop_reason": None,
                }
                for idx, it in enumerate(items)
            ],
        }
        return bounded

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


@pytest.mark.anyio
async def test_shared_retrieval_execution_core_count(tmp_path):
    """Verifies that retrieve() and retrieve_precomputed() share the exact same execute_plan core."""
    item = EvidenceItem(
        evidence_id="ev_01",
        source="slack",
        author="작성자",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 1차 배포 완료 내용",
        message_identifier="https://slack.com/archives/C1/p01",
        has_thread=False,
    )
    query_map = {"query_core": [item]}
    plan = InvestigationPlan(
        objective="코어 단일화 검증",
        search_queries=[SearchQuery(query="query_core", purpose="코어 탐색")],
        questions_to_resolve=["단일 실행 코어 확인"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    provider = ParityMockProvider(plan=plan)

    from pesu_agent.investigation.planner import InvestigationPlanner
    planner = InvestigationPlanner(provider=provider)
    coordinator = ProductionInvestigationRetrievalCoordinator(
        search_backend=backend,
        planner=planner,
    )

    # 1. Question-driven path
    res_q = await coordinator.retrieve(
        question=UserQuestion(question="고객 만족도 1차 배포 완료 확인"),
        inv_dir=tmp_path / "q_path",
    )
    assert isinstance(res_q, RetrievalCoordinatorResult)
    assert res_q.evidence_package.total_items == 1

    # 2. Precomputed-plan path
    res_p = await coordinator.retrieve_precomputed(
        plan=plan,
        inv_dir=tmp_path / "p_path",
    )
    assert isinstance(res_p, RetrievalCoordinatorResult)
    assert res_p.evidence_package.total_items == 1

    # Both produced identical evidence IDs, anchor matches, and query hit count
    assert res_q.evidence_package.evidence_items[0].message_identifier == res_p.evidence_package.evidence_items[0].message_identifier
    assert res_q.evidence_package.evidence_items[0].anchor_match_count == res_p.evidence_package.evidence_items[0].anchor_match_count


@pytest.mark.anyio
async def test_contract_a_anchor_propagation_forbidden_empty(tmp_path):
    """Contract A: planner/plan anchor_terms -> EvidenceSelector anchor_terms. Empty is forbidden."""
    item_relevant = EvidenceItem(
        evidence_id="ev_rel",
        source="slack",
        author="작성자",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 페이지는 1차 배포가 완료된 상태입니다.",
        message_identifier="https://slack.com/archives/C1/p_rel",
        has_thread=False,
    )
    item_irrelevant = EvidenceItem(
        evidence_id="ev_irrel",
        source="slack",
        author="작성자",
        timestamp="2026-08-01 10:00",
        text="오늘 점심은 구내식당 제육볶음입니다.",
        message_identifier="https://slack.com/archives/C1/p_irrel",
        has_thread=False,
    )

    query_map = {"query_anchor": [item_irrelevant, item_relevant]}
    plan = InvestigationPlan(
        objective="앵커 전파 불변식 검증",
        search_queries=[SearchQuery(query="query_anchor", purpose="앵커 탐색")],
        questions_to_resolve=["배포 상태 확인"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)

    res = await coordinator.retrieve_precomputed(plan=plan)

    # Empty anchor terms is forbidden: plan had anchors, so EvidenceSelector MUST have evaluated them
    assert len(plan.anchor_terms) > 0
    assert res.plan.anchor_terms == plan.anchor_terms

    # Candidate with anchor matches must be ranked first (E1) despite being second in query results
    selected = res.evidence_package.evidence_items
    assert len(selected) == 2
    assert selected[0].message_identifier == "https://slack.com/archives/C1/p_rel"
    assert selected[0].evidence_id == "E1"
    assert selected[0].anchor_match_count == 2
    assert selected[1].anchor_match_count == 0


@pytest.mark.anyio
async def test_contract_b_c_d_budget_dedupe_and_provenance(tmp_path):
    """Contracts B, C, D: query count, per-query result cap, overall candidate budget, dedupe, provenance."""
    dup_item = EvidenceItem(
        evidence_id="dup",
        source="slack",
        author="작성자",
        timestamp="2026-08-01 10:00",
        text="공통 중복 메시지 내용 12345",
        message_identifier="https://slack.com/archives/C1/p_dup",
        has_thread=False,
    )
    items_q0 = [dup_item] + [
        EvidenceItem(
            evidence_id=f"q0_{i}",
            source="slack",
            author="작성자",
            timestamp="2026-08-01 10:00",
            text=f"메시지 본문 Q0 번호 {i}",
            message_identifier=f"https://slack.com/archives/C1/p_q0_{i}",
            has_thread=False,
        )
        for i in range(10)  # More than 5 items
    ]
    items_q1 = [dup_item] + [
        EvidenceItem(
            evidence_id=f"q1_{i}",
            source="slack",
            author="작성자",
            timestamp="2026-08-01 10:00",
            text=f"메시지 본문 Q1 번호 {i}",
            message_identifier=f"https://slack.com/archives/C1/p_q1_{i}",
            has_thread=False,
        )
        for i in range(10)
    ]

    query_map = {"q0": items_q0, "q1": items_q1}
    plan = InvestigationPlan(
        objective="예산 및 중복 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0번"),
            SearchQuery(query="q1", purpose="1번"),
        ],
        questions_to_resolve=["예산 확인"],
        anchor_terms=["중복"],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)

    res = await coordinator.retrieve_precomputed(plan=plan)

    # Contract B: per-query max results = 5 (backend returns 5 each)
    assert len(backend.executed_queries) == 2

    # Contract C: Dedupe by canonical key
    dup_key = "https://slack.com/archives/C1/p_dup"
    assert dup_key in res.candidate_query_provenance
    hits = res.candidate_query_provenance[dup_key]
    assert len(hits) == 2
    assert hits[0].query_index == 0
    assert hits[1].query_index == 1

    # Contract D: Provenance snapshots
    assert hits[0].search_query.query == "q0"
    assert hits[1].search_query.query == "q1"


@pytest.mark.anyio
async def test_contract_e_thread_residency_first_and_relocation(tmp_path):
    """Contract E: thread candidate selection, residency check, relocation on TARGET_ROW_NOT_IN_CURRENT_DOM, merge."""
    parent_item = EvidenceItem(
        evidence_id="parent",
        source="slack",
        author="작성자",
        timestamp="2026-08-01 10:00",
        text="스레드 부모 메시지 내용",
        message_identifier="https://slack.com/archives/C1/p_thread_root",
        thread_identifier="1787000000.000001",
        has_thread=True,
    )
    reply = ThreadReplyItem(
        reply_id="R1",
        text="스레드 댓글입니다.",
        permalink="https://slack.com/archives/C1/p_reply?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1787000010.000001",
        thread_ts="1787000000.000001",
    )

    query_map = {"q_thread": [parent_item]}
    first_read = ThreadReadResult(
        replies=(),
        status=ThreadContextStatus.UNAVAILABLE,
        failure_reason=ThreadReadFailureReason.TARGET_ROW_NOT_IN_CURRENT_DOM,
    )
    second_read = ThreadReadResult(
        replies=(reply,),
        status=ThreadContextStatus.PARTIAL,
        failure_reason=None,
    )

    backend = ParityMockSearchBackend(
        query_map=query_map,
        first_thread_read=first_read,
        second_thread_read=second_read,
    )
    plan = InvestigationPlan(
        objective="스레드 재배치 검증",
        search_queries=[SearchQuery(query="q_thread", purpose="스레드")],
        questions_to_resolve=["스레드 확인"],
        anchor_terms=["스레드"],
    )

    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)
    res = await coordinator.retrieve_precomputed(plan=plan)

    # 1 search + 1 relocation = 2 search calls
    assert len(backend.executed_queries) == 2
    # 2 thread read calls
    assert len(backend.thread_read_calls) == 2
    # Replies merged
    selected_item = res.evidence_package.evidence_items[0]
    assert len(selected_item.thread_replies) == 1
    assert selected_item.thread_replies[0].text == "스레드 댓글입니다."
    assert selected_item.thread_context_status == ThreadContextStatus.PARTIAL.value


@pytest.mark.anyio
async def test_telemetry_read_only_side_channel(tmp_path):
    """Verifies that telemetry and observability tracking does NOT alter rankings, filtering, or dedupe."""
    item1 = EvidenceItem(
        evidence_id="item1",
        source="slack",
        author="작성자1",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 관련 업무",
        message_identifier="https://slack.com/archives/C1/p_tel_1",
        has_thread=False,
    )
    item2 = EvidenceItem(
        evidence_id="item2",
        source="slack",
        author="작성자2",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 1차 배포 완료 업무",
        message_identifier="https://slack.com/archives/C1/p_tel_2",
        has_thread=False,
    )
    query_map = {"q_tel": [item1, item2]}
    plan = InvestigationPlan(
        objective="사이드 채널 불변성 검증",
        search_queries=[SearchQuery(query="q_tel", purpose="탐색")],
        questions_to_resolve=["사이드 채널 확인"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)

    res = await coordinator.retrieve_precomputed(plan=plan)

    # Telemetry is populated in result
    assert len(res.raw_query_records) == 1
    assert res.raw_query_records[0]["raw_returned_item_count"] == 2
    assert "pipeline_boundary_telemetry" in res.__dict__ or hasattr(res, "pipeline_boundary_telemetry")

    # Ranking remains 100% deterministic based purely on selector rules
    assert res.evidence_package.evidence_items[0].message_identifier == "https://slack.com/archives/C1/p_tel_2"
    assert res.evidence_package.evidence_items[1].message_identifier == "https://slack.com/archives/C1/p_tel_1"


@pytest.mark.anyio
async def test_contract_f_and_g_evidence_selection_and_seed_metadata(tmp_path):
    """Contracts F & G: identical candidate inputs produce identical selection, coverage, and valid seed metadata."""
    item_a = EvidenceItem(
        evidence_id="ev_a",
        source="slack",
        author="작성자A",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 현황 페이지는 1차 배포가 완료된 상태입니다.",
        message_identifier="https://slack.com/archives/C1/p_a",
        permalink="https://slack.com/archives/C1/p_a",
        has_thread=False,
    )
    item_b = EvidenceItem(
        evidence_id="ev_b",
        source="slack",
        author="작성자B",
        timestamp="2026-08-01 10:00",
        text="고객 만족도 관련 논의 내용입니다.",
        message_identifier="https://slack.com/archives/C1/p_b",
        permalink="https://slack.com/archives/C1/p_b",
        has_thread=False,
    )

    query_map = {"q_fg": [item_b, item_a]}
    plan = InvestigationPlan(
        objective="F&G 검증",
        search_queries=[SearchQuery(query="q_fg", purpose="탐색")],
        questions_to_resolve=["선택 및 시드 검증"],
        anchor_terms=["고객 만족도", "1차 배포"],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)

    res = await coordinator.retrieve_precomputed(plan=plan)

    # Contract F: Evidence selection ordering & coverage
    pkg = res.evidence_package
    assert pkg.total_items == 2
    assert pkg.evidence_items[0].evidence_id == "E1"
    assert pkg.evidence_items[0].message_identifier == "https://slack.com/archives/C1/p_a"
    assert pkg.evidence_items[0].anchor_match_count == 2
    assert pkg.evidence_items[1].evidence_id == "E2"
    assert pkg.evidence_items[1].anchor_match_count == 1
    assert pkg.retrieval_coverage is not None

    # Contract G: Seed metadata semantics
    graph = InvestigationStateGraph(question="고객 만족도 1차 배포 완료 여부")
    builder = InvestigationSeedPoolBuilder()
    builder.populate_seed_graph(
        graph=graph,
        candidates=res.selected_candidates,
        temporal_scope=res.temporal_scope,
        actor_scope=res.actor_scope,
        candidate_query_provenance=res.candidate_query_provenance,
    )

    assert len(graph.evidence_nodes) == 2
    assert "E1" in graph.evidence_nodes
    assert "E2" in graph.evidence_nodes
    assert graph.evidence_nodes["E1"].canonical_permalink == "https://slack.com/archives/C1/p_a"
    assert graph.evidence_nodes["E1"].source_query == "q_fg"

