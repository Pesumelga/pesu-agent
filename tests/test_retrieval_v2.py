"""Unit tests for Slack Retrieval v2 Core components (Phase 12.2)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from pesu_agent.investigation.analyzer import InvestigationAnalyzer
from pesu_agent.investigation.models import (
    AnalysisResult,
    ConfirmedFact,
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    RetrievalCoverage,
    SearchQuery,
    TemporalConstraint,
    TemporalRelation,
    UserQuestion,
)
from pesu_agent.investigation.payload_builder import ExternalModelPayloadBuilder
from pesu_agent.investigation.planner import InvestigationPlanner, MAX_SLACK_SEARCH_QUERIES
from pesu_agent.investigation.selector import EvidenceSelector, compute_anchor_matches, normalize_text_for_matching
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler
from pesu_agent.search.temporal import (
    classify_span_relation,
    combine_temporal_relations,
    evaluate_candidate_temporal_relation,
    extract_mentioned_dates,
    parse_authored_timestamp,
    parse_iso_date,
)


def test_planner_query_count_and_clamping():
    """1. Planner는 1~3개의 쿼리를 생성하며, 3개 초과 시 code-side에서 3개로 엄격히 제한하고 중복/공백을 제거함."""
    mock_provider = MagicMock()
    # Mock planner returning 5 queries with duplicate and empty query
    raw_plan = InvestigationPlan(
        objective="일정 확인",
        search_queries=[
            SearchQuery(query="핵심 질의 A", purpose="A"),
            SearchQuery(query="  핵심 질의 A  ", purpose="A duplicate"),
            SearchQuery(query="", purpose="empty"),
            SearchQuery(query="질의 B", purpose="B"),
            SearchQuery(query="질의 C", purpose="C"),
            SearchQuery(query="질의 D (초과분)", purpose="D"),
        ],
        questions_to_resolve=["Q1"],
        temporal_constraint=TemporalConstraint(has_temporal_intent=True, start_date="2026-07-01", end_date="2026-12-31"),
        anchor_terms=["핵심", "질의"],
    )

    mock_provider.generate_structured = AsyncMock(
        return_value=(
            raw_plan,
            LLMUsageStats(
                provider="mock",
                model="mock-model",
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
            ),
        )
    )

    planner = InvestigationPlanner(provider=mock_provider)
    import asyncio
    plan, usage = asyncio.run(
        planner.plan(UserQuestion(question="테스트 질문"), max_search_queries=MAX_SLACK_SEARCH_QUERIES, search_target="slack")
    )

    assert len(plan.search_queries) <= 3
    assert len(plan.search_queries) == 3
    assert [sq.query for sq in plan.search_queries] == ["핵심 질의 A", "질의 B", "질의 C"]
    # All queries must pass SlackQueryCompiler
    for sq in plan.search_queries:
        compiled = SlackQueryCompiler.compile(sq.query)
        assert compiled.compiled_query == sq.query


def test_canonical_deduplication_and_query_hit_count():
    """2. Canonical Slack identity 기반 중복 제거 및 query_hit_count 추적 검증."""
    selector = EvidenceSelector(max_evidence_items=5)
    candidates = [
        EvidenceItem(
            evidence_id="q1_item1",
            source="slack",
            author="UserA",
            timestamp="2026-08-20",
            text="본문 내용 동일",
            message_identifier="slack_msg_123",
        ),
        EvidenceItem(
            evidence_id="q2_item1",
            source="slack",
            author="UserA",
            timestamp="2026-08-20",
            text="본문 내용 동일",
            message_identifier="slack_msg_123", # Duplicate from query 2
        ),
        EvidenceItem(
            evidence_id="q3_item1",
            source="slack",
            author="UserA",
            timestamp="2026-08-20",
            text="본문 내용 동일",
            message_identifier="slack_msg_123", # Duplicate from query 3
        ),
        EvidenceItem(
            evidence_id="q1_item2",
            source="slack",
            author="UserB",
            timestamp="2026-08-21",
            text="다른 본문 내용",
            message_identifier="slack_msg_456",
        ),
    ]

    pkg, stats = selector.select_and_package(candidates=candidates)
    assert stats["initial_candidates"] == 4
    assert stats["duplicates_removed"] == 2
    assert stats["final_selected"] == 2
    assert pkg.total_items == 2

    # Verify query_hit_count on deduped item
    hit_3_item = next(it for it in pkg.evidence_items if it.message_identifier == "slack_msg_123")
    assert hit_3_item.query_hit_count == 3
    assert hit_3_item.evidence_id == "E1"

    hit_1_item = next(it for it in pkg.evidence_items if it.message_identifier == "slack_msg_456")
    assert hit_1_item.query_hit_count == 1
    assert hit_1_item.evidence_id == "E2"


def test_static_domain_keyword_boost_removed():
    """3. 기존 공식, 결재, 규정 등 고정 비즈니스 키워드 boost가 제거되었는지 검증."""
    selector = EvidenceSelector(max_evidence_items=5)
    
    # item1 has static keywords ("공식 결재 규정"), but 0 anchors
    # item2 has 0 static keywords, but matches 2 question anchors ("시스템 이전")
    item_with_static_kw = EvidenceItem(
        evidence_id="ev_static",
        source="slack",
        author="User1",
        timestamp="2025-01-01",
        text="공식 결재 규정에 따른 안내사항입니다.",
        message_identifier="id_static",
    )
    item_with_anchor = EvidenceItem(
        evidence_id="ev_anchor",
        source="slack",
        author="User2",
        timestamp="2025-01-01",
        text="신규 시스템 이전 계획 공유드립니다.",
        message_identifier="id_anchor",
    )

    plan = InvestigationPlan(
        objective="시스템 이전 확인",
        search_queries=[SearchQuery(query="시스템 이전", purpose="조회")],
        questions_to_resolve=["Q1"],
        temporal_constraint=TemporalConstraint(has_temporal_intent=False),
        anchor_terms=["시스템", "이전"],
    )

    pkg, _ = selector.select_and_package(candidates=[item_with_static_kw, item_with_anchor], plan=plan)
    # The anchor-matching item MUST rank first (E1), NOT the static keyword item
    assert pkg.evidence_items[0].message_identifier == "id_anchor"
    assert pkg.evidence_items[0].evidence_id == "E1"


def test_temporal_separation_and_deterministic_combination():
    """4. Message authored timestamp와 body mentioned dates의 분리 평가 및 결정론적 합성 검증."""
    constraint = TemporalConstraint(
        has_temporal_intent=True,
        start_date="2026-07-01",
        end_date="2026-12-31",
        source_text="2026년 하반기",
    )

    # Case A: Old-authored message (2024) explicitly mentioning 2026.08.27
    old_msg_target_date = EvidenceItem(
        evidence_id="ev1",
        source="slack",
        author="Admin",
        timestamp="2024-05-10 10:00",
        text="중장기 로드맵 공지: 2026.08.27 13:00 일정으로 전체 지점 확대 진행 예정",
        message_identifier="msg_old_future",
    )
    comb, ts_rel, ment_rel = evaluate_candidate_temporal_relation(old_msg_target_date, constraint)
    assert ts_rel == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
    assert ment_rel == TemporalRelation.DIRECT_TEMPORAL_MATCH
    assert comb == TemporalRelation.DIRECT_TEMPORAL_MATCH

    # Case B: Current-authored message (2026-08) discussing old 2024 date
    current_msg_old_date = EvidenceItem(
        evidence_id="ev2",
        source="slack",
        author="UserB",
        timestamp="2026-08-15 11:00",
        text="지난 2024-03-10 회고 자료를 참조하여 개선안을 작성했습니다.",
        message_identifier="msg_curr_old",
    )
    comb2, ts_rel2, ment_rel2 = evaluate_candidate_temporal_relation(current_msg_old_date, constraint)
    assert ts_rel2 == TemporalRelation.DIRECT_TEMPORAL_MATCH
    assert ment_rel2 == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
    assert comb2 == TemporalRelation.DIRECT_TEMPORAL_MATCH

    # Case C: Authored at 2024, body only mentions 2024 -> EXPLICIT_TEMPORAL_MISMATCH
    pure_mismatch_item = EvidenceItem(
        evidence_id="ev3",
        source="slack",
        author="UserC",
        timestamp="2024-01-10",
        text="2024년도 업무 보고서입니다.",
        message_identifier="msg_mismatch",
    )
    comb3, ts_rel3, ment_rel3 = evaluate_candidate_temporal_relation(pure_mismatch_item, constraint)
    assert comb3 == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH

    # Case D: No dates at all -> TEMPORAL_UNKNOWN
    unknown_item = EvidenceItem(
        evidence_id="ev4",
        source="slack",
        author="UserD",
        timestamp=None,
        text="일반 업무 대화 메시지입니다.",
        message_identifier="msg_unknown",
    )
    comb4, _, _ = evaluate_candidate_temporal_relation(unknown_item, constraint)
    assert comb4 == TemporalRelation.TEMPORAL_UNKNOWN


def test_no_temporal_intent_unaffected_by_temporal_ranking():
    """5. 시간 조건이 없는 질문에는 temporal ranking이 순위를 왜곡하지 않음."""
    selector = EvidenceSelector(max_evidence_items=5)
    plan_no_time = InvestigationPlan(
        objective="기능 문의",
        search_queries=[SearchQuery(query="알프레드 기능", purpose="조회")],
        questions_to_resolve=["Q1"],
        temporal_constraint=TemporalConstraint(has_temporal_intent=False),
        anchor_terms=["알프레드", "기능"],
    )

    item_2024 = EvidenceItem(
        evidence_id="ev_2024",
        source="slack",
        author="A",
        timestamp="2024-01-01",
        text="알프레드 기능에 대한 주요 매뉴얼 안내입니다.",
        message_identifier="id_2024",
    )
    item_2026 = EvidenceItem(
        evidence_id="ev_2026",
        source="slack",
        author="B",
        timestamp="2026-08-01",
        text="일반적인 인사 메시지입니다.", # No anchor match
        message_identifier="id_2026",
    )

    pkg, _ = selector.select_and_package(candidates=[item_2024, item_2026], plan=plan_no_time)
    # item_2024 has anchor match, item_2026 does not. item_2024 must rank first despite being 2024
    assert pkg.evidence_items[0].message_identifier == "id_2024"
    assert pkg.evidence_items[0].evidence_id == "E1"


def test_retrieval_coverage_calculation():
    """6. Retrieval coverage의 STRONG, LIMITED, NO_DIRECT_MATCH 상태 산출 검증."""
    selector = EvidenceSelector(max_evidence_items=5)
    plan_time = InvestigationPlan(
        objective="2026년 하반기 행사",
        search_queries=[SearchQuery(query="행사", purpose="조회")],
        questions_to_resolve=["Q1"],
        temporal_constraint=TemporalConstraint(has_temporal_intent=True, start_date="2026-07-01", end_date="2026-12-31"),
        anchor_terms=["행사", "일정"],
    )

    # 1. STRONG: both anchor and direct temporal match exist
    strong_item = EvidenceItem(
        evidence_id="ev_strong",
        source="slack",
        author="A",
        timestamp="2026-08-20",
        text="2026.08.27 13:00 행사 일정 확정",
        message_identifier="id_strong",
    )
    pkg_strong, _ = selector.select_and_package(candidates=[strong_item], plan=plan_time)
    assert pkg_strong.retrieval_coverage == RetrievalCoverage.STRONG.value

    # 2. LIMITED: anchor match exists, but explicit temporal mismatch (e.g. 2024)
    limited_item = EvidenceItem(
        evidence_id="ev_limited",
        source="slack",
        author="B",
        timestamp="2024-05-10",
        text="2024년 5월 10일 행사 일정 공유",
        message_identifier="id_limited",
    )
    pkg_limited, _ = selector.select_and_package(candidates=[limited_item], plan=plan_time)
    assert pkg_limited.retrieval_coverage == RetrievalCoverage.LIMITED.value

    # 3. NO_DIRECT_MATCH: no anchor match and temporal mismatch
    no_match_item = EvidenceItem(
        evidence_id="ev_no_match",
        source="slack",
        author="C",
        timestamp="2024-01-01",
        text="전혀 무관한 2024년도 내용",
        message_identifier="id_no_match",
    )
    pkg_no_match, _ = selector.select_and_package(candidates=[no_match_item], plan=plan_time)
    assert pkg_no_match.retrieval_coverage == RetrievalCoverage.NO_DIRECT_MATCH.value


def test_negative_answer_guard_in_analyzer():
    """7. EXPLICIT_NEGATIVE_SUPPORTED에는 유효한 citation이 필수이며, 미인용 시 INCONCLUSIVE로 강등됨."""
    mock_provider = MagicMock()
    # Model claims EXPLICIT_NEGATIVE_SUPPORTED without citing any evidence
    raw_analysis = AnalysisResult(
        summary="확인 결과 관련 일정이 전혀 없습니다.",
        confirmed_facts=[],
        disputed_points=[],
        contradictions=[],
        assessment="계획이 존재하지 않음",
        uncertainty="",
        missing_information=[],
        suggested_followups=[],
        conclusion_status="CONFIRMED",
        finding_type="EXPLICIT_NEGATIVE_SUPPORTED",
        cited_evidence_ids=[], # Empty citations!
    )

    mock_provider.generate_structured = AsyncMock(
        return_value=(
            raw_analysis,
            LLMUsageStats(
                provider="mock",
                model="mock-model",
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
            ),
        )
    )

    analyzer = InvestigationAnalyzer(provider=mock_provider)
    pkg = EvidencePackage(
        package_id="pkg1",
        query="일정 확인",
        total_items=1,
        created_at="2026-09-04T00:00:00Z",
        is_synthetic=True,
        retrieval_coverage=RetrievalCoverage.LIMITED.value,
        evidence_items=[
            EvidenceItem(
                evidence_id="E1",
                source="synthetic",
                text="관련 대화 내용",
                message_identifier="id1",
            )
        ],
    )

    import asyncio
    res, _ = asyncio.run(
        analyzer.analyze(UserQuestion(question="일정이 있어?"), package=pkg)
    )

    # Must be demoted from EXPLICIT_NEGATIVE_SUPPORTED to INCONCLUSIVE because cited_evidence_ids was empty
    assert res.finding_type == "INCONCLUSIVE"
    assert res.retrieval_coverage == RetrievalCoverage.LIMITED.value


def test_generalized_bounded_4digit_year_parser():
    """8. Bounded 4-digit year parsing with calendar validation across centuries."""
    from pesu_agent.search.temporal import extract_mentioned_dates

    # 1. Various 4-digit years with full dates
    text = (
        "창립 1985-05-12, 현재 2026.08.27, 미래 계획 2045/10/15, 장기 목표 2120년 03월 01일. "
        "잘못된 날짜 2026-02-30 및 2026-13-01은 무시되어야 함."
    )
    spans = extract_mentioned_dates(text)
    years = {sp.year for sp in spans}

    assert 1985 in years
    assert 2026 in years
    assert 2045 in years
    assert 2120 in years

    # Invalid dates (Feb 30, Month 13) must not be in exact dates
    exact_dates = [(sp.year, sp.month, sp.day) for sp in spans if sp.is_exact_date]
    assert (2026, 2, 30) not in exact_dates
    assert (2026, 13, 1) not in exact_dates
    assert (2026, 8, 27) in exact_dates
    assert (1985, 5, 12) in exact_dates
    assert (2045, 10, 15) in exact_dates
    assert (2120, 3, 1) in exact_dates


def test_pure_non_mutating_selector_deduplication():
    """9. Pure non-mutating query_hit_count aggregation and idempotency in EvidenceSelector."""
    selector = EvidenceSelector(max_evidence_items=5)

    # 1. Raw count1 single -> output 1
    raw_single = [
        EvidenceItem(
            evidence_id="s1",
            source="slack",
            author="UserA",
            text="단일 유효 증거 본문입니다.",
            message_identifier="msg_single",
            query_hit_count=1,
        )
    ]
    pkg1, stats1 = selector.select_and_package(candidates=raw_single)
    assert len(pkg1.evidence_items) == 1
    assert pkg1.evidence_items[0].query_hit_count == 1
    assert stats1["duplicates_removed"] == 0

    # 2. Raw count1 duplicates x3 -> output 3
    raw_dups = [
        EvidenceItem(
            evidence_id="d1",
            source="slack",
            author="UserA",
            text="중복 유효 증거 본문입니다.",
            message_identifier="msg_dup_raw",
            query_hit_count=1,
        ),
        EvidenceItem(
            evidence_id="d2",
            source="slack",
            author="UserA",
            text="중복 유효 증거 본문입니다.",
            message_identifier="msg_dup_raw",
            query_hit_count=1,
        ),
        EvidenceItem(
            evidence_id="d3",
            source="slack",
            author="UserA",
            text="중복 유효 증거 본문입니다.",
            message_identifier="msg_dup_raw",
            query_hit_count=1,
        ),
    ]
    pkg2, stats2 = selector.select_and_package(candidates=raw_dups)
    assert len(pkg2.evidence_items) == 1
    assert pkg2.evidence_items[0].query_hit_count == 3
    assert stats2["duplicates_removed"] == 2

    # 3. Pre-aggregated single count3 -> output 3 (no information loss)
    pre_single = [
        EvidenceItem(
            evidence_id="p1",
            source="slack",
            author="UserB",
            text="사전 집계된 증거 본문입니다.",
            message_identifier="msg_pre_single",
            query_hit_count=3,
        )
    ]
    pkg3, stats3 = selector.select_and_package(candidates=pre_single)
    assert len(pkg3.evidence_items) == 1
    assert pkg3.evidence_items[0].query_hit_count == 3
    assert stats3["duplicates_removed"] == 0

    # 4. Pre-aggregated duplicate count2 + count3 -> output 5
    pre_dups = [
        EvidenceItem(
            evidence_id="p2",
            source="slack",
            author="UserC",
            text="사전 집계 중복 증거 본문 A",
            message_identifier="msg_pre_dups",
            query_hit_count=2,
        ),
        EvidenceItem(
            evidence_id="p3",
            source="slack",
            author="UserC",
            text="사전 집계 중복 증거 본문 A",
            message_identifier="msg_pre_dups",
            query_hit_count=3,
        ),
    ]
    pkg4, stats4 = selector.select_and_package(candidates=pre_dups)
    assert len(pkg4.evidence_items) == 1
    assert pkg4.evidence_items[0].query_hit_count == 5
    assert stats4["duplicates_removed"] == 1

    # 5. Input EvidenceItems remain byte/value-equivalent after selection (immutability)
    test_input = EvidenceItem(
        evidence_id="imm_1",
        source="slack",
        author="UserOrig",
        text="원본 불변성 검증 본문입니다.",
        message_identifier="msg_imm_1",
        query_hit_count=2,
    )
    dump_before = test_input.model_dump()
    pkg5, _ = selector.select_and_package(candidates=[test_input, test_input])
    assert test_input.model_dump() == dump_before
    assert test_input.query_hit_count == 2
    assert test_input.temporal_relation is None

    # 6. Same original input passed twice -> outputs identical (idempotency)
    pkg6_a, stats6_a = selector.select_and_package(candidates=raw_dups)
    pkg6_b, stats6_b = selector.select_and_package(candidates=raw_dups)
    assert pkg6_a.model_dump(exclude={"created_at"}) == pkg6_b.model_dump(exclude={"created_at"})
    assert stats6_a == stats6_b

    # 7. Already-packaged single item count3 passed again -> remains count3
    packaged_item = pkg3.evidence_items[0]
    assert packaged_item.query_hit_count == 3
    pkg7, stats7 = selector.select_and_package(candidates=[packaged_item])
    assert pkg7.evidence_items[0].query_hit_count == 3
    assert stats7["duplicates_removed"] == 0

    # 8. Edge Case 1: First accepted valid item + later short duplicate
    edge1 = [
        EvidenceItem(
            evidence_id="e1_valid",
            source="slack",
            author="UserD",
            text="충분히 긴 유효 증거 본문입니다.",
            message_identifier="msg_edge1",
            query_hit_count=1,
        ),
        EvidenceItem(
            evidence_id="e1_short",
            source="slack",
            author="UserD",
            text="짧음",
            message_identifier="msg_edge1",
            query_hit_count=1,
        ),
    ]
    pkg_edge1, stats_edge1 = selector.select_and_package(candidates=edge1)
    assert len(pkg_edge1.evidence_items) == 1
    assert pkg_edge1.evidence_items[0].query_hit_count == 2
    assert pkg_edge1.evidence_items[0].text == "충분히 긴 유효 증거 본문입니다."
    assert stats_edge1["duplicates_removed"] == 1
    assert stats_edge1["noise_filtered"] == 0

    # 9. Edge Case 2: First short/noise item + later valid same-key item
    edge2 = [
        EvidenceItem(
            evidence_id="e2_short",
            source="slack",
            author="UserE",
            text="짧음",
            message_identifier="msg_edge2",
            query_hit_count=1,
        ),
        EvidenceItem(
            evidence_id="e2_valid",
            source="slack",
            author="UserE",
            text="이후에 도착한 유효한 긴 본문입니다.",
            message_identifier="msg_edge2",
            query_hit_count=2,
        ),
    ]
    pkg_edge2, stats_edge2 = selector.select_and_package(candidates=edge2)
    assert len(pkg_edge2.evidence_items) == 1
    assert pkg_edge2.evidence_items[0].query_hit_count == 2
    assert pkg_edge2.evidence_items[0].text == "이후에 도착한 유효한 긴 본문입니다."
    assert stats_edge2["duplicates_removed"] == 0
    assert stats_edge2["noise_filtered"] == 1


def test_selector_query_hit_count_ranking_tiebreaker():
    """10. Same temporal tier & anchor score: candidate with higher aggregate query_hit_count ranks ahead."""
    selector = EvidenceSelector(max_evidence_items=5)
    plan_no_time = InvestigationPlan(
        objective="기능 문의",
        search_queries=[SearchQuery(query="테스트 기능", purpose="조회")],
        questions_to_resolve=["Q1"],
        temporal_constraint=TemporalConstraint(has_temporal_intent=False),
        anchor_terms=[],
    )

    # Candidate B is encountered first with query_hit_count = 1
    cand_b = EvidenceItem(
        evidence_id="cand_b",
        source="slack",
        author="UserB",
        text="후보 B 메시지 내용입니다.",
        message_identifier="msg_b",
        query_hit_count=1,
    )
    # Candidate A is encountered later, but appears twice (aggregate query_hit_count = 2)
    cand_a1 = EvidenceItem(
        evidence_id="cand_a1",
        source="slack",
        author="UserA",
        text="후보 A 메시지 내용입니다.",
        message_identifier="msg_a",
        query_hit_count=1,
    )
    cand_a2 = EvidenceItem(
        evidence_id="cand_a2",
        source="slack",
        author="UserA",
        text="후보 A 메시지 내용입니다.",
        message_identifier="msg_a",
        query_hit_count=1,
    )

    # Order in raw candidates: B first, then A1, A2
    pkg, stats = selector.select_and_package(
        candidates=[cand_b, cand_a1, cand_a2],
        plan=plan_no_time,
    )
    assert len(pkg.evidence_items) == 2
    # Candidate A has query_hit_count=2 and must sort ahead of Candidate B (query_hit_count=1)
    first_ranked = pkg.evidence_items[0]
    second_ranked = pkg.evidence_items[1]

    assert first_ranked.message_identifier == "msg_a"
    assert first_ranked.evidence_id == "E1"
    assert first_ranked.query_hit_count == 2

    assert second_ranked.message_identifier == "msg_b"
    assert second_ranked.evidence_id == "E2"
    assert second_ranked.query_hit_count == 1


def test_temporal_permalink_epoch_fallback_regression_lock():
    """11. Regression lock for temporal.py permalink epoch fallback (KEEP_AS_INTENTIONAL)."""
    constraint = TemporalConstraint(
        has_temporal_intent=True,
        start_date="2026-07-01",
        end_date="2026-12-31",
    )

    # 1. Non-thread + year-less Slack timestamp ("8월 27일") + valid permalink epoch (2026-09-04)
    # Slack epoch 1788479531.716659 -> 2026-09-04T07:52:11 UTC
    item_yearless = EvidenceItem(
        evidence_id="e_yearless",
        source="slack",
        author="User",
        timestamp="8월 27일",
        text="간담회 일정 관련 문의",
        message_identifier="https://myworkspace.slack.com/archives/C012345/p1788479531716659",
        has_thread=False,
    )
    comb1, ts1, ment1 = evaluate_candidate_temporal_relation(item_yearless, constraint)
    assert ts1 == TemporalRelation.DIRECT_TEMPORAL_MATCH
    assert ment1 == TemporalRelation.TEMPORAL_UNKNOWN
    assert comb1 == TemporalRelation.DIRECT_TEMPORAL_MATCH

    # 2. Non-thread + no authored timestamp (None) + valid permalink epoch
    item_no_ts = EvidenceItem(
        evidence_id="e_no_ts",
        source="slack",
        author="User",
        timestamp=None,
        text="간담회 일정 관련 문의",
        message_identifier="https://myworkspace.slack.com/archives/C012345/p1788479531716659",
        has_thread=False,
    )
    comb2, ts2, ment2 = evaluate_candidate_temporal_relation(item_no_ts, constraint)
    assert ts2 == TemporalRelation.DIRECT_TEMPORAL_MATCH
    assert ment2 == TemporalRelation.TEMPORAL_UNKNOWN
    assert comb2 == TemporalRelation.DIRECT_TEMPORAL_MATCH

    # 3. Explicit full authored date (2024-05-10) + permalink (epoch 2026)
    # Explicit authored date remains authoritative and is not overwritten by permalink
    item_explicit_old = EvidenceItem(
        evidence_id="e_old",
        source="slack",
        author="User",
        timestamp="2024-05-10 14:00",
        text="일반 업무 보고",
        message_identifier="https://myworkspace.slack.com/archives/C012345/p1788479531716659",
        has_thread=False,
    )
    comb3, ts3, ment3 = evaluate_candidate_temporal_relation(item_explicit_old, constraint)
    assert ts3 == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
    assert ment3 == TemporalRelation.TEMPORAL_UNKNOWN
    assert comb3 == TemporalRelation.TEMPORAL_UNKNOWN

    # 4. Malformed/non-Slack permalink with no parseable timestamp + year-less timestamp
    item_non_slack = EvidenceItem(
        evidence_id="e_non_slack",
        source="slack",
        author="User",
        timestamp="8월 27일",
        text="일반 업무 보고",
        message_identifier="msg_non_slack_12345",
        has_thread=False,
    )
    comb4, ts4, ment4 = evaluate_candidate_temporal_relation(item_non_slack, constraint)
    assert ts4 == TemporalRelation.TEMPORAL_UNKNOWN
    assert ment4 == TemporalRelation.TEMPORAL_UNKNOWN
    assert comb4 == TemporalRelation.TEMPORAL_UNKNOWN


