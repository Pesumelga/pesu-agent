"""Tests for Task 11Q4-P1A: EvidenceSelector Query-Starvation Remediation.

Contracts verified:
A. Identical Quality — Query 0 Starvation Remediation
B. Quality Must Strictly Dominate Coverage
C. Single Query Backward Compatibility
D. Multi-Query Duplicate Coverage Accounting
E. Empty anchor_terms Starvation Remediation
F. No Forced Noise Promotion
G. Selection Determinism
H. Fixed Evidence Budget Enforcement (<= 5)
I. Character Budget (max_total_chars) Contract Preserved
J. Full RetrievalCoordinator Pipeline Integration (preliminary & final selection)
K. Omitted Provenance Legacy Fallback
"""

from __future__ import annotations

import pytest
from pesu_agent.investigation.models import (
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    SearchQuery,
)
from pesu_agent.investigation.retrieval_coordinator import (
    CandidateQueryHit,
    ProductionInvestigationRetrievalCoordinator,
)
from pesu_agent.investigation.selector import (
    EvidenceSelector,
    get_canonical_evidence_key,
)
from tests.test_investigation_retrieval_parity import ParityMockSearchBackend


def _make_candidate(
    ev_id: str,
    text: str = "표준 테스트 증거 본문 메시지 1234567890",
    permalink: str | None = None,
    author: str = "테스터",
) -> EvidenceItem:
    """Helper to create candidate EvidenceItem."""
    return EvidenceItem(
        evidence_id=ev_id,
        source="slack",
        author=author,
        timestamp="2026-08-01 10:00",
        text=text,
        permalink=permalink or f"https://slack.com/archives/C1/{ev_id}",
        message_identifier=permalink or f"https://slack.com/archives/C1/{ev_id}",
        has_thread=False,
    )


# =============================================================================
# Test A: Identical Quality — Query 0 Starvation Remediation
# =============================================================================

def test_identical_quality_query_0_starvation_remediated():
    """Test A: 3 complementary queries, 5+ equal-quality candidates.

    Old selector fills all 5 slots from earliest query (Query 0).
    Expected new behavior: equally-qualified candidates from Query 1 and Query 2 survive.
    """
    selector = EvidenceSelector(max_evidence_items=5)

    # 5 candidates from Q0, 2 from Q1, 2 from Q2 (total 9)
    q0_items = [_make_candidate(f"q0_{i}", text=f"본문 Q0 메시지 인덱스 {i} 내용 12345") for i in range(5)]
    q1_items = [_make_candidate(f"q1_{i}", text=f"본문 Q1 메시지 인덱스 {i} 내용 12345") for i in range(2)]
    q2_items = [_make_candidate(f"q2_{i}", text=f"본문 Q2 메시지 인덱스 {i} 내용 12345") for i in range(2)]

    all_candidates = q0_items + q1_items + q2_items

    # Explicit provenance map from RetrievalCoordinator
    provenance_map: dict[str, list[CandidateQueryHit]] = {}
    for it in q0_items:
        k = get_canonical_evidence_key(it)
        provenance_map[k] = [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))]
    for it in q1_items:
        k = get_canonical_evidence_key(it)
        provenance_map[k] = [CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1"))]
    for it in q2_items:
        k = get_canonical_evidence_key(it)
        provenance_map[k] = [CandidateQueryHit(query_index=2, search_query=SearchQuery(query="q2", purpose="2"))]

    # No anchors, no temporal intent -> strictly equal quality (0, 0, 1)
    plan = InvestigationPlan(
        objective="동일 품질 쿼리 기아 현상 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0"),
            SearchQuery(query="q1", purpose="1"),
            SearchQuery(query="q2", purpose="2"),
        ],
        questions_to_resolve=["기아 해소 확인"],
        anchor_terms=[],
    )

    pkg, stats = selector.select_and_package(
        candidates=all_candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    assert len(selected_keys) == 5

    # Check query representation in selected evidence
    q0_keys = {get_canonical_evidence_key(it) for it in q0_items}
    q1_keys = {get_canonical_evidence_key(it) for it in q1_items}
    q2_keys = {get_canonical_evidence_key(it) for it in q2_items}

    selected_q0 = [k for k in selected_keys if k in q0_keys]
    selected_q1 = [k for k in selected_keys if k in q1_keys]
    selected_q2 = [k for k in selected_keys if k in q2_keys]

    # Old selector would have len(selected_q0) == 5, len(selected_q1) == 0, len(selected_q2) == 0
    # Expected: Query 1 and Query 2 candidates MUST be represented!
    assert len(selected_q1) >= 1, f"Query 1 was starved! selected: {selected_keys}"
    assert len(selected_q2) >= 1, f"Query 2 was starved! selected: {selected_keys}"
    assert len(selected_q0) >= 1, f"Query 0 was starved! selected: {selected_keys}"

    # Balanced distribution expectation:
    # slot 0: q0_0, slot 1: q1_0, slot 2: q2_0, slot 3: q0_1, slot 4: q1_1
    expected_keys = [
        get_canonical_evidence_key(q0_items[0]),
        get_canonical_evidence_key(q1_items[0]),
        get_canonical_evidence_key(q2_items[0]),
        get_canonical_evidence_key(q0_items[1]),
        get_canonical_evidence_key(q1_items[1]),
    ]
    assert selected_keys == expected_keys


# =============================================================================
# Test B: Quality Must Strictly Dominate Coverage
# =============================================================================

def test_quality_must_beat_coverage():
    """Test B: Quality strictly dominates coverage.

    Query 0 candidate has higher anchor match count than Query 1 candidate.
    Even though Query 1 is unrepresented, Query 0 candidate MUST win.
    """
    selector = EvidenceSelector(max_evidence_items=2)

    # Item A has anchor match ("배포", "완료"), Item B has anchor match ("배포"), Item C has 0 matches
    item_q0_high = _make_candidate("q0_high", text="고객 만족도 시스템 1차 배포 완료되었습니다.")
    item_q0_mid = _make_candidate("q0_mid", text="고객 만족도 1차 배포 진행 상황입니다.")
    item_q1_low = _make_candidate("q1_low", text="오늘 점심 구내식당 식단 안내입니다.")

    all_candidates = [item_q0_high, item_q0_mid, item_q1_low]

    provenance_map = {
        get_canonical_evidence_key(item_q0_high): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))],
        get_canonical_evidence_key(item_q0_mid): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))],
        get_canonical_evidence_key(item_q1_low): [CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1"))],
    }

    plan = InvestigationPlan(
        objective="품질 우선 원칙 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0"),
            SearchQuery(query="q1", purpose="1"),
        ],
        questions_to_resolve=["품질 우선 여부"],
        anchor_terms=["고객 만족도", "배포 완료", "진행 상황"],
    )

    pkg, _ = selector.select_and_package(
        candidates=all_candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    assert len(selected_keys) == 2

    # Both higher-quality Q0 candidates MUST win over Q1 low candidate
    assert selected_keys[0] == get_canonical_evidence_key(item_q0_high)
    assert selected_keys[1] == get_canonical_evidence_key(item_q0_mid)
    assert get_canonical_evidence_key(item_q1_low) not in selected_keys


# =============================================================================
# Test C: Single Query Backward Compatibility
# =============================================================================

def test_single_query_selection_behavior_preserved():
    """Test C: Single query candidates maintain exact deterministic arrival order."""
    selector = EvidenceSelector(max_evidence_items=5)

    candidates = [_make_candidate(f"sq_{i}", text=f"단일 쿼리 메시지 본문 {i}") for i in range(5)]

    provenance_map = {
        get_canonical_evidence_key(it): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="single", purpose="s"))]
        for it in candidates
    }

    plan = InvestigationPlan(
        objective="단일 쿼리 하위 호환성 검증",
        search_queries=[SearchQuery(query="single", purpose="s")],
        questions_to_resolve=["단일 쿼리"],
        anchor_terms=[],
    )

    pkg, _ = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    expected_keys = [get_canonical_evidence_key(it) for it in candidates]
    assert selected_keys == expected_keys


# =============================================================================
# Test D: Multi-Query Duplicate Coverage Accounting
# =============================================================================

def test_multi_query_duplicate_coverage_accounting():
    """Test D: Multi-query candidate with provenance {0, 1} counts both as represented.

    After selecting it, an equal-quality Query 2 candidate is preferred over Query 0 candidate.
    """
    selector = EvidenceSelector(max_evidence_items=2)

    item_a = _make_candidate("dup_0_1", text="공통 중복 메시지 내용 12345")
    item_b = _make_candidate("q0_only", text="0번 쿼리 단독 메시지 내용 12345")
    item_c = _make_candidate("q2_only", text="2번 쿼리 단독 메시지 내용 12345")

    # Item A has hit count 2 from two queries, but let's test within an equal quality bucket:
    # Here let's test where item_a is already selected (e.g. higher hit count or first in bucket)
    # When item_a with provenance {0, 1} is selected, query 0 and 1 are represented.
    # The next choice between item_b (query 0) and item_c (query 2) MUST be item_c!
    all_candidates = [item_a, item_b, item_c]

    provenance_map = {
        get_canonical_evidence_key(item_a): [
            CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0")),
            CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1")),
        ],
        get_canonical_evidence_key(item_b): [
            CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0")),
        ],
        get_canonical_evidence_key(item_c): [
            CandidateQueryHit(query_index=2, search_query=SearchQuery(query="q2", purpose="2")),
        ],
    }

    plan = InvestigationPlan(
        objective="다중 쿼리 중복 커버리지 계량 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0"),
            SearchQuery(query="q1", purpose="1"),
            SearchQuery(query="q2", purpose="2"),
        ],
        questions_to_resolve=["다중 쿼리 커버리지"],
        anchor_terms=[],
    )

    pkg, _ = selector.select_and_package(
        candidates=all_candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    assert len(selected_keys) == 2

    # Item A is selected first (it has query_hit_count=2, so higher quality)
    assert selected_keys[0] == get_canonical_evidence_key(item_a)

    # For the second slot:
    # Query 0 and Query 1 are represented.
    # Query 2 is unrepresented (count=0).
    # Item C (query 2) MUST beat Item B (query 0)!
    assert selected_keys[1] == get_canonical_evidence_key(item_c)


# =============================================================================
# Test E: Empty anchor_terms Starvation Remediation
# =============================================================================

def test_empty_anchor_terms_query_starvation_remediated():
    """Test E: plan.anchor_terms = [] -> all candidates anchor_matches = 0.

    Equal-quality results from Q1 / Q2 must not be starved purely because Q0 arrived first.
    """
    selector = EvidenceSelector(max_evidence_items=5)

    q0_items = [_make_candidate(f"e_q0_{i}", text=f"빈 앵커 Q0 메시지 {i}") for i in range(5)]
    q1_items = [_make_candidate(f"e_q1_{i}", text=f"빈 앵커 Q1 메시지 {i}") for i in range(2)]
    q2_items = [_make_candidate(f"e_q2_{i}", text=f"빈 앵커 Q2 메시지 {i}") for i in range(2)]

    all_candidates = q0_items + q1_items + q2_items

    provenance_map: dict[str, list[CandidateQueryHit]] = {}
    for it in q0_items:
        provenance_map[get_canonical_evidence_key(it)] = [
            CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))
        ]
    for it in q1_items:
        provenance_map[get_canonical_evidence_key(it)] = [
            CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1"))
        ]
    for it in q2_items:
        provenance_map[get_canonical_evidence_key(it)] = [
            CandidateQueryHit(query_index=2, search_query=SearchQuery(query="q2", purpose="2"))
        ]

    plan = InvestigationPlan(
        objective="빈 앵커 기아 해소 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0"),
            SearchQuery(query="q1", purpose="1"),
            SearchQuery(query="q2", purpose="2"),
        ],
        questions_to_resolve=["빈 앵커 검증"],
        anchor_terms=[],  # Explicit empty anchor terms
    )

    pkg, _ = selector.select_and_package(
        candidates=all_candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    q0_keys = {get_canonical_evidence_key(it) for it in q0_items}
    q1_keys = {get_canonical_evidence_key(it) for it in q1_items}
    q2_keys = {get_canonical_evidence_key(it) for it in q2_items}

    # Verify starvation is remediated
    assert any(k in q1_keys for k in selected_keys), f"Q1 was starved with empty anchors: {selected_keys}"
    assert any(k in q2_keys for k in selected_keys), f"Q2 was starved with empty anchors: {selected_keys}"


# =============================================================================
# Test F: No Forced Noise
# =============================================================================

def test_no_forced_noise():
    """Test F: Candidate from unrepresented query with worse quality is NOT promoted."""
    selector = EvidenceSelector(max_evidence_items=2)

    # Q0 has 2 high quality items (matching anchor)
    item_q0_1 = _make_candidate("q0_anchor_1", text="지점 정산 업무 최종 결정 손익계산서 업로드 확정")
    item_q0_2 = _make_candidate("q0_anchor_2", text="지점 정산 업무 관련 세무 데이터 검증")
    # Q1 has 1 low quality noise item (no anchor match)
    item_q1_noise = _make_candidate("q1_noise", text="오늘 날씨가 매우 맑고 따뜻합니다.")

    all_candidates = [item_q0_1, item_q0_2, item_q1_noise]

    provenance_map = {
        get_canonical_evidence_key(item_q0_1): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))],
        get_canonical_evidence_key(item_q0_2): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))],
        get_canonical_evidence_key(item_q1_noise): [CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1"))],
    }

    plan = InvestigationPlan(
        objective="노이즈 강제 편입 방지 검증",
        search_queries=[
            SearchQuery(query="q0", purpose="0"),
            SearchQuery(query="q1", purpose="1"),
        ],
        questions_to_resolve=["노이즈 편입 방지"],
        anchor_terms=["지점 정산", "업무", "손익계산서"],
    )

    pkg, _ = selector.select_and_package(
        candidates=all_candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    assert get_canonical_evidence_key(item_q1_noise) not in selected_keys
    assert selected_keys == [
        get_canonical_evidence_key(item_q0_1),
        get_canonical_evidence_key(item_q0_2),
    ]


# =============================================================================
# Test G: Determinism
# =============================================================================

def test_selection_determinism():
    """Test G: Repeated selection with identical input produces exactly identical evidence ordering."""
    selector = EvidenceSelector(max_evidence_items=5)

    candidates = [
        _make_candidate(f"det_{i}", text=f"결정론적 메시지 본문 {i}")
        for i in range(8)
    ]
    provenance_map = {
        get_canonical_evidence_key(it): [CandidateQueryHit(query_index=i % 3, search_query=SearchQuery(query=f"q{i%3}", purpose=str(i%3)))]
        for i, it in enumerate(candidates)
    }

    plan = InvestigationPlan(
        objective="결정론 검증",
        search_queries=[SearchQuery(query="q0", purpose="0"), SearchQuery(query="q1", purpose="1"), SearchQuery(query="q2", purpose="2")],
        questions_to_resolve=["결정론"],
        anchor_terms=[],
    )

    pkg1, _ = selector.select_and_package(candidates=candidates, plan=plan, candidate_query_provenance=provenance_map)
    pkg2, _ = selector.select_and_package(candidates=candidates, plan=plan, candidate_query_provenance=provenance_map)

    keys1 = [get_canonical_evidence_key(it) for it in pkg1.evidence_items]
    keys2 = [get_canonical_evidence_key(it) for it in pkg2.evidence_items]
    assert keys1 == keys2
    assert [it.evidence_id for it in pkg1.evidence_items] == [it.evidence_id for it in pkg2.evidence_items]


# =============================================================================
# Test H: Fixed Evidence Budget (len <= 5)
# =============================================================================

def test_fixed_budget_enforced():
    """Test H: Regardless of query count, len(selected_evidence) <= 5."""
    selector = EvidenceSelector(max_evidence_items=5)

    # 15 candidates across 3 queries
    candidates = [
        _make_candidate(f"bud_{i}", text=f"예산 검증 메시지 본문 {i}")
        for i in range(15)
    ]
    provenance_map = {
        get_canonical_evidence_key(it): [CandidateQueryHit(query_index=i % 3, search_query=SearchQuery(query=f"q{i%3}", purpose=str(i%3)))]
        for i, it in enumerate(candidates)
    }

    plan = InvestigationPlan(
        objective="예산 상한 검증",
        search_queries=[SearchQuery(query="q0", purpose="0"), SearchQuery(query="q1", purpose="1"), SearchQuery(query="q2", purpose="2")],
        questions_to_resolve=["예산 상한"],
        anchor_terms=[],
    )

    pkg, stats = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    assert len(pkg.evidence_items) == 5
    assert stats["final_selected"] == 5


# =============================================================================
# Test I: Character Budget Safety Regression
# =============================================================================

def test_max_total_chars_contract_preserved():
    """Test I: Coverage tie-breaking must not bypass max_total_chars bound."""
    # Set tight char limit
    selector = EvidenceSelector(max_evidence_items=5, max_total_chars=120)

    # Each item 50 chars
    q0_item = _make_candidate("c_q0", text="A" * 50)
    q1_item = _make_candidate("c_q1", text="B" * 50)
    q2_item = _make_candidate("c_q2", text="C" * 50)  # total would be 150 > 120

    candidates = [q0_item, q1_item, q2_item]
    provenance_map = {
        get_canonical_evidence_key(q0_item): [CandidateQueryHit(query_index=0, search_query=SearchQuery(query="q0", purpose="0"))],
        get_canonical_evidence_key(q1_item): [CandidateQueryHit(query_index=1, search_query=SearchQuery(query="q1", purpose="1"))],
        get_canonical_evidence_key(q2_item): [CandidateQueryHit(query_index=2, search_query=SearchQuery(query="q2", purpose="2"))],
    }

    plan = InvestigationPlan(
        objective="글자 수 한도 검증",
        search_queries=[SearchQuery(query="q0", purpose="0"), SearchQuery(query="q1", purpose="1"), SearchQuery(query="q2", purpose="2")],
        questions_to_resolve=["글자 수 한도"],
        anchor_terms=[],
    )

    pkg, stats = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    # Minimum 2 items selected, third would exceed 120 chars
    assert len(pkg.evidence_items) == 2
    total_chars = sum(len(it.text) for it in pkg.evidence_items)
    assert total_chars <= 120


# =============================================================================
# Test J: Pipeline Integration (ProductionInvestigationRetrievalCoordinator)
# =============================================================================

@pytest.mark.anyio
async def test_provenance_survives_real_retrieval_coordinator_pipeline(tmp_path):
    """Test J: Verify candidate_query_provenance reaches preliminary and final selection
    through ProductionInvestigationRetrievalCoordinator, remediating query starvation.
    """
    q0_items = [_make_candidate(f"pipe_q0_{i}", text=f"파이프라인 Q0 메시지 본문 {i}") for i in range(5)]
    q1_items = [_make_candidate(f"pipe_q1_{i}", text=f"파이프라인 Q1 메시지 본문 {i}") for i in range(5)]
    q2_items = [_make_candidate(f"pipe_q2_{i}", text=f"파이프라인 Q2 메시지 본문 {i}") for i in range(5)]

    query_map = {
        "query_0": q0_items,
        "query_1": q1_items,
        "query_2": q2_items,
    }

    plan = InvestigationPlan(
        objective="파이프라인 통합 검증",
        search_queries=[
            SearchQuery(query="query_0", purpose="0번 질의"),
            SearchQuery(query="query_1", purpose="1번 질의"),
            SearchQuery(query="query_2", purpose="2번 질의"),
        ],
        questions_to_resolve=["파이프라인 검증"],
        anchor_terms=[],
    )

    backend = ParityMockSearchBackend(query_map=query_map)
    coordinator = ProductionInvestigationRetrievalCoordinator(search_backend=backend)

    # Spy on selector.select_and_package to verify provenance reaches both preliminary and final selection
    recorded_calls = []
    orig_select = coordinator.selector.select_and_package

    def spy_select(*args, **kwargs):
        recorded_calls.append({
            "package_id": kwargs.get("package_id") or (args[2] if len(args) > 2 else None),
            "candidate_query_provenance": kwargs.get("candidate_query_provenance"),
            "candidate_count": len(kwargs.get("candidates") or args[0]),
        })
        return orig_select(*args, **kwargs)

    coordinator.selector.select_and_package = spy_select

    res = await coordinator.retrieve_precomputed(plan=plan, inv_dir=tmp_path / "coord_inv")

    # Verify calls: preliminary packaging and final packaging both occurred
    prelim_calls = [c for c in recorded_calls if c["package_id"] and "prelim" in c["package_id"]]
    final_calls = [c for c in recorded_calls if c["package_id"] and "pkg" in c["package_id"]]

    assert len(prelim_calls) >= 1, "Preliminary selection was not invoked"
    assert len(final_calls) >= 1, "Final selection was not invoked"

    # QUERY_PROVENANCE_REACHES_PRELIMINARY_SELECTION = true
    prelim_prov = prelim_calls[0]["candidate_query_provenance"]
    assert prelim_prov is not None and len(prelim_prov) == 15

    # QUERY_PROVENANCE_REACHES_FINAL_SELECTION = true
    final_prov = final_calls[0]["candidate_query_provenance"]
    assert final_prov is not None and len(final_prov) == 15

    # Verify provenance map was constructed and populated in result
    assert len(res.candidate_query_provenance) == 15

    # Check that selected evidence contains items from multiple queries
    selected_keys = [get_canonical_evidence_key(it) for it in res.selected_candidates]
    assert len(selected_keys) == 5

    q0_keys = {get_canonical_evidence_key(it) for it in q0_items}
    q1_keys = {get_canonical_evidence_key(it) for it in q1_items}
    q2_keys = {get_canonical_evidence_key(it) for it in q2_items}

    # Starvation remediated in the real coordinator pipeline!
    assert any(k in q1_keys for k in selected_keys), f"Q1 was starved in pipeline! selected: {selected_keys}"
    assert any(k in q2_keys for k in selected_keys), f"Q2 was starved in pipeline! selected: {selected_keys}"
    assert any(k in q0_keys for k in selected_keys), f"Q0 was starved in pipeline! selected: {selected_keys}"


# =============================================================================
# Test K: Omitted Provenance Legacy Fallback
# =============================================================================

def test_omitted_provenance_legacy_fallback():
    """Test K: When candidate_query_provenance is None, selector maintains existing deterministic order."""
    selector = EvidenceSelector(max_evidence_items=5)

    candidates = [_make_candidate(f"legacy_{i}", text=f"레거시 메시지 {i}") for i in range(7)]

    plan = InvestigationPlan(
        objective="레거시 폴백 검증",
        search_queries=[SearchQuery(query="q0", purpose="0")],
        questions_to_resolve=["레거시 폴백"],
        anchor_terms=[],
    )

    # Calling without candidate_query_provenance parameter
    pkg, _ = selector.select_and_package(candidates=candidates, plan=plan)

    selected_keys = [get_canonical_evidence_key(it) for it in pkg.evidence_items]
    expected_keys = [get_canonical_evidence_key(it) for it in candidates[:5]]
    assert selected_keys == expected_keys


# =============================================================================
# Test L: Retired Q2-02 / Q2-03 Offline Starvation Regressions
# =============================================================================

def test_retired_q2_02_offline_starvation_regression():
    """Verify that in retired Q2-02 preserved raw candidate artifact,
    the earliest query no longer monopolizes all 5 slots when complementary
    candidates belong to the same quality bucket.
    """
    import json
    from pathlib import Path

    artifact_path = Path("output/qualification_13_5d/v3_clean/clean_v3_run_20260917_231655/q2-02_production_result.json")
    if not artifact_path.exists():
        pytest.skip("Retired Q2-02 artifact not available")

    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    candidates = []
    provenance_map: dict[str, list[CandidateQueryHit]] = {}
    search_queries = []

    for q_idx, qr in enumerate(data.get("raw_query_records", [])):
        sq = SearchQuery(query=qr["query_text"], purpose=qr.get("purpose", ""))
        search_queries.append(sq)
        for it_idx, raw in enumerate(qr.get("raw_items", [])):
            ev = EvidenceItem(
                evidence_id=f"cand_{q_idx}_{it_idx}",
                source="slack",
                author=raw.get("sender_text"),
                author_id=raw.get("sender_author_id"),
                timestamp=raw.get("timestamp_raw"),
                text=raw.get("normalized_body", ""),
                permalink=raw.get("canonical_permalink"),
                message_identifier=raw.get("canonical_permalink"),
            )
            c_key = get_canonical_evidence_key(ev)
            if c_key not in provenance_map:
                provenance_map[c_key] = []
            provenance_map[c_key].append(CandidateQueryHit(query_index=q_idx, search_query=sq))
            candidates.append(ev)

    plan = InvestigationPlan(
        objective="Q2-02 Offline Regression",
        search_queries=search_queries,
        questions_to_resolve=["Q2-02 offline"],
        anchor_terms=[],
    )

    selector = EvidenceSelector(max_evidence_items=5)
    pkg_new, _ = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )
    pkg_old, _ = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=None,
    )

    # Prove old selector starved Q1
    old_provenances = [
        [h.query_index for h in provenance_map.get(get_canonical_evidence_key(it), [])]
        for it in pkg_old.evidence_items
    ]
    assert all(0 in prov for prov in old_provenances)

    # Prove new selector remediated starvation: Q1 candidates now survive!
    new_provenances = [
        [h.query_index for h in provenance_map.get(get_canonical_evidence_key(it), [])]
        for it in pkg_new.evidence_items
    ]
    has_q1 = any(1 in prov for prov in new_provenances)
    assert has_q1, "Q2-02 offline regression failed: Query 1 still starved!"


def test_retired_q2_03_offline_starvation_regression():
    """Verify that in retired Q2-03 preserved raw candidate artifact,
    the earliest query no longer monopolizes all 5 slots when complementary
    candidates belong to the same quality bucket.
    """
    import json
    from pathlib import Path

    artifact_path = Path("output/qualification_13_5d/v3_clean/clean_v3_run_20260917_231655/q2-03_production_result.json")
    if not artifact_path.exists():
        pytest.skip("Retired Q2-03 artifact not available")

    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    candidates = []
    provenance_map: dict[str, list[CandidateQueryHit]] = {}
    search_queries = []

    for q_idx, qr in enumerate(data.get("raw_query_records", [])):
        sq = SearchQuery(query=qr["query_text"], purpose=qr.get("purpose", ""))
        search_queries.append(sq)
        for it_idx, raw in enumerate(qr.get("raw_items", [])):
            ev = EvidenceItem(
                evidence_id=f"cand_{q_idx}_{it_idx}",
                source="slack",
                author=raw.get("sender_text"),
                author_id=raw.get("sender_author_id"),
                timestamp=raw.get("timestamp_raw"),
                text=raw.get("normalized_body", ""),
                permalink=raw.get("canonical_permalink"),
                message_identifier=raw.get("canonical_permalink"),
            )
            c_key = get_canonical_evidence_key(ev)
            if c_key not in provenance_map:
                provenance_map[c_key] = []
            provenance_map[c_key].append(CandidateQueryHit(query_index=q_idx, search_query=sq))
            candidates.append(ev)

    plan = InvestigationPlan(
        objective="Q2-03 Offline Regression",
        search_queries=search_queries,
        questions_to_resolve=["Q2-03 offline"],
        anchor_terms=[],
    )

    selector = EvidenceSelector(max_evidence_items=5)
    pkg_new, _ = selector.select_and_package(
        candidates=candidates,
        plan=plan,
        candidate_query_provenance=provenance_map,
    )

    new_provenances = [
        [h.query_index for h in provenance_map.get(get_canonical_evidence_key(it), [])]
        for it in pkg_new.evidence_items
    ]
    # In new selector, Query 1 and Query 2 candidates are both selected
    has_q1 = any(1 in prov for prov in new_provenances)
    has_q2 = any(2 in prov for prov in new_provenances)
    assert has_q1 and has_q2, "Q2-03 offline regression failed: Query 1 or Query 2 still starved!"
