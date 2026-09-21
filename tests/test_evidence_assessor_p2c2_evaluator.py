"""Evaluator self-tests for Task 11Q4-P2C2."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from pesu_agent.investigation.gap_assessment import (
    InvestigationAssessment,
    ProposedClaim,
)
from pesu_agent.investigation.graph_models import ClaimKind, ClaimSemanticType
from tests.qualification_support.evidence_assessor_p2c2_evaluator import (
    evaluate_case,
    evaluate_all_cases,
)

CASES_FILE = Path(__file__).resolve().parent / "fixtures" / "evidence_assessor_p2c2_qualification_cases.json"


@pytest.fixture
def benchmark_cases() -> list[dict]:
    with open(CASES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


def test_cases_fixture_integrity(benchmark_cases):
    """Verify that exactly 12 cases exist across 6 families and paired contract holds."""
    assert len(benchmark_cases) == 12
    case_ids = [c["case_id"] for c in benchmark_cases]
    assert len(case_ids) == len(set(case_ids))

    families = {c["family"] for c in benchmark_cases}
    assert families == {"FAMILY_A", "FAMILY_B", "FAMILY_C", "FAMILY_D", "FAMILY_E", "FAMILY_F"}

    # Paired case F1/F2 check
    f1 = next(c for c in benchmark_cases if c["case_id"] == "CASE_F1")
    f2 = next(c for c in benchmark_cases if c["case_id"] == "CASE_F2")
    assert f1["evidence_text"] == f2["evidence_text"]
    assert f1["question"] == f2["question"]
    assert f1["parent_context_state"] == "SEARCH_SNIPPET"
    assert f2["parent_context_state"] == "CANONICAL_PARENT"


def test_positive_cases_pass_with_expected_claims(benchmark_cases):
    """Verify that valid claims matching required concepts pass evaluation."""
    a1 = next(c for c in benchmark_cases if c["case_id"] == "CASE_A1")
    assessment_a1 = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="P1",
                statement="2026년 하반기 클라우드 인프라 예산은 분기당 5,000만원으로 배정되었다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            )
        ]
    )
    res_a1 = evaluate_case(a1, assessment_a1, schema_valid=True)
    assert res_a1.passed
    assert res_a1.all_required_matched
    assert not res_a1.any_forbidden_triggered


def test_explicit_negative_controls(benchmark_cases):
    """Verify positive controls for explicit negative facts in A2 and D2."""
    # A2
    a2 = next(c for c in benchmark_cases if c["case_id"] == "CASE_A2")
    assessment_a2 = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="P1",
                statement="야간 긴급 배포 작업에는 별도 예외가 없다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            )
        ]
    )
    res_a2 = evaluate_case(a2, assessment_a2, schema_valid=True)
    assert res_a2.passed
    assert res_a2.all_required_matched

    # D2
    d2 = next(c for c in benchmark_cases if c["case_id"] == "CASE_D2")
    assessment_d2 = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="P1",
                statement="킥오프 회의는 매주 월요일 오전 10시에 진행한다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.EVENT,
                supporting_evidence_ids=["EV_01"],
            ),
            ProposedClaim(
                proposal_id="P2",
                statement="회의록은 24시간 이내에 등록해야 한다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.ACTION,
                supporting_evidence_ids=["EV_01"],
            ),
            ProposedClaim(
                proposal_id="P3",
                statement="회의록 등록 기한에는 예외를 두지 않는다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            ),
        ]
    )
    res_d2 = evaluate_case(d2, assessment_d2, schema_valid=True)
    assert res_d2.passed
    assert res_d2.all_required_matched
    assert not res_d2.any_forbidden_triggered


def test_forbidden_concepts_trigger_violations(benchmark_cases):
    """Verify that forbidden propositions trigger violations and fail cases."""
    # B1 forbidden: "예외 없이 모든 파트너에게 항상 7%만 적용된다"
    b1 = next(c for c in benchmark_cases if c["case_id"] == "CASE_B1")
    bad_assessment_b1 = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="P1",
                statement="외부 파트너 수수료는 7%로 적용된다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            ),
            ProposedClaim(
                proposal_id="P2",
                statement="이 수수료는 예외 없이 모든 외부 파트너에게 항상 적용된다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            ),
        ]
    )
    res_b1 = evaluate_case(b1, bad_assessment_b1, schema_valid=True)
    assert not res_b1.passed
    assert res_b1.any_forbidden_triggered

    # E1 forbidden: "이것이 최종 결정이며 첨부파일이나 댓글에 번복은 없다"
    e1 = next(c for c in benchmark_cases if c["case_id"] == "CASE_E1")
    bad_assessment_e1 = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="P1",
                statement="외부 계약자 수수료 7% 적용은 최종 결정이다.",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.STATE,
                supporting_evidence_ids=["EV_01"],
            ),
        ]
    )
    res_e1 = evaluate_case(e1, bad_assessment_e1, schema_valid=True)
    assert not res_e1.passed
    assert res_e1.any_forbidden_triggered
