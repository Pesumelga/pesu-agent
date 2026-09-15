"""Phase 13.5D Cycle 2: Deterministic Evaluator v2 Tests.

Strictly offline, deterministic verification of Evaluator v2 logic without any LLM calls.
Covers the 10 required cases from Section 13:
1. expected semantic paraphrase -> TP
2. optional acceptable proposition -> no penalty (neutral)
3. evidence-grounded atomic split -> not ungrounded (TP)
4. composite realization -> accepted (TP)
5. gold에 없지만 evidence-grounded proposition -> EXTRA_GROUNDED_RELEVANT, never ACTUALLY_UNGROUNDED
6. evidence에 전혀 없는 proposition -> ACTUALLY_UNGROUNDED
7. correct reciprocal contradiction links -> contradiction preservation PASS
8. missing/wrong contradiction links -> contradiction preservation FAIL
9. speculative meta-speech -> speculative leakage / forbidden
10. invalid evidence ID -> hallucinated evidence ref
"""

from __future__ import annotations

import pytest

from pesu_agent.investigation.gap_assessment import ProposedClaim
from pesu_agent.investigation.graph_models import ClaimKind, ClaimSemanticType
from tests.qualification_support.evidence_assessor_evaluator_v2 import (
    ClaimClassification,
    evaluate_fixture_v2,
    NO_LLM_JUDGE_CALLS,
    SCORING_MATRIX_FROZEN_BEFORE_PROVIDER_EXECUTION,
)


def _build_sample_fixture() -> dict:
    return {
        "fixture_id": "EA2-TEST-01",
        "category": "single clear claim",
        "question": "PaymentService 배포 상태는?",
        "evidence_nodes": [
            {
                "evidence_id": "EV_01",
                "presentation_author": "DevOps",
                "text": "PaymentService v3.1.0 deployment to production completed successfully at 15:00 UTC.",
            }
        ],
        "expected_semantic_units": [
            {
                "semantic_unit_id": "UNIT-01",
                "canonical_fact": "PaymentService v3.1.0 deployment completed successfully",
                "semantic_type": ["OUTCOME", "ACTION", "EVENT"],
                "governing_evidence_ids": ["EV_01"],
                "required_contradicting_evidence_ids": [],
                "required_concept_groups": [
                    ["paymentservice", "payment"],
                    ["v3.1.0", "3.1.0"],
                    ["deployment", "배포", "deploy"],
                    ["completed", "완료", "성공", "success"],
                ],
            }
        ],
        "optional_claims": [],
        "forbidden_claims": [],
        "expected_fail_closed": False,
        "expected_contradiction_preservation": False,
    }


def test_invariants_frozen():
    assert NO_LLM_JUDGE_CALLS is True
    assert SCORING_MATRIX_FROZEN_BEFORE_PROVIDER_EXECUTION is True


# Case 1: expected semantic paraphrase -> TP
def test_case_01_expected_semantic_paraphrase_tp():
    fixture = _build_sample_fixture()
    claims = [
        ProposedClaim(
            statement="운영 환경에 PaymentService 3.1.0 배포가 성공적으로 완료되었습니다.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        )
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 1
    assert res.false_positives == 0
    assert res.false_negatives == 0
    assert res.classified_claims[0].classification == ClaimClassification.EXPECTED_MATCH
    assert res.classified_claims[0].matched_unit_id == "UNIT-01"


# Case 2: optional acceptable proposition -> no penalty (neutral)
def test_case_02_optional_acceptable_proposition_neutral():
    fixture = _build_sample_fixture()
    fixture["optional_claims"] = [
        {
            "semantic_unit_id": "OPT-01",
            "canonical_fact": "Deployment occurred at 15:00 UTC",
            "semantic_type": ["EVENT", "STATE"],
            "governing_evidence_ids": ["EV_01"],
            "required_concept_groups": [["15:00", "utc"]],
        }
    ]
    claims = [
        ProposedClaim(
            statement="PaymentService v3.1.0 deployment completed successfully.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        ),
        ProposedClaim(
            statement="The deployment happened at 15:00 UTC.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.EVENT,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        ),
    ]
    res = evaluate_fixture_v2(fixture, claims)
    # Expected unit satisfied = 1 TP, Optional claim neutral = 0 FP
    assert res.true_positives == 1
    assert res.false_positives == 0
    assert res.false_negatives == 0
    assert any(c.classification == ClaimClassification.OPTIONAL_ACCEPTABLE for c in res.classified_claims)


# Case 3: evidence-grounded atomic split -> not ungrounded (TP for each unit)
def test_case_03_atomic_split_not_ungrounded():
    fixture = {
        "fixture_id": "EA2-MULTI-01",
        "category": "multiple grounded claims",
        "question": "마이그레이션 결과는?",
        "evidence_nodes": [
            {
                "evidence_id": "EV_03",
                "text": "DBA aborted migration and completed rollback to schema v2 at 09:40.",
            }
        ],
        "expected_semantic_units": [
            {
                "semantic_unit_id": "UNIT-03A",
                "canonical_fact": "Migration aborted by DBA",
                "semantic_type": ["ACTION", "EVENT", "OUTCOME"],
                "governing_evidence_ids": ["EV_03"],
                "required_contradicting_evidence_ids": [],
                "required_concept_groups": [["abort", "중단", "취소"]],
            },
            {
                "semantic_unit_id": "UNIT-03B",
                "canonical_fact": "Rollback to schema v2 completed",
                "semantic_type": ["ACTION", "OUTCOME", "EVENT"],
                "governing_evidence_ids": ["EV_03"],
                "required_contradicting_evidence_ids": [],
                "required_concept_groups": [["rollback", "롤백", "v2"], ["completed", "완료", "성공"]],
            },
        ],
        "optional_claims": [],
        "forbidden_claims": [],
        "expected_fail_closed": False,
    }
    # Atomic split into two propositions
    claims = [
        ProposedClaim(
            statement="DBA가 마이그레이션을 중단했다.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.ACTION,
            supporting_evidence_ids=["EV_03"],
            contradicting_evidence_ids=[],
        ),
        ProposedClaim(
            statement="스키마 v2로 롤백이 완료되었다.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_03"],
            contradicting_evidence_ids=[],
        ),
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 2
    assert res.false_positives == 0
    assert res.false_negatives == 0
    assert len(res.ungrounded_propositions) == 0


# Case 4: composite realization -> accepted
def test_case_04_composite_realization_accepted():
    fixture = {
        "fixture_id": "EA2-MULTI-01",
        "category": "multiple grounded claims",
        "question": "마이그레이션 결과는?",
        "evidence_nodes": [
            {
                "evidence_id": "EV_03",
                "text": "DBA aborted migration and completed rollback to schema v2 at 09:40.",
            }
        ],
        "expected_semantic_units": [
            {
                "semantic_unit_id": "UNIT-03A",
                "canonical_fact": "Migration aborted by DBA",
                "semantic_type": ["ACTION", "EVENT", "OUTCOME"],
                "governing_evidence_ids": ["EV_03"],
                "required_contradicting_evidence_ids": [],
                "required_concept_groups": [["abort", "중단", "취소"]],
            },
            {
                "semantic_unit_id": "UNIT-03B",
                "canonical_fact": "Rollback to schema v2 completed",
                "semantic_type": ["ACTION", "OUTCOME", "EVENT"],
                "governing_evidence_ids": ["EV_03"],
                "required_contradicting_evidence_ids": [],
                "required_concept_groups": [["rollback", "롤백", "v2"], ["completed", "완료", "성공"]],
            },
        ],
        "optional_claims": [],
        "forbidden_claims": [],
        "expected_fail_closed": False,
    }
    # One composite claim covering both units
    claims = [
        ProposedClaim(
            statement="DBA aborted migration and completed rollback to schema v2.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.ACTION,
            supporting_evidence_ids=["EV_03"],
            contradicting_evidence_ids=[],
        )
    ]
    res = evaluate_fixture_v2(fixture, claims)
    # Composite claim satisfies both UNIT-03A and UNIT-03B -> 2 TP
    assert res.true_positives == 2
    assert res.false_positives == 0
    assert res.false_negatives == 0
    assert len(res.ungrounded_propositions) == 0
    assert res.classified_claims[0].classification == ClaimClassification.ACCEPTABLE_ALTERNATIVE_REALIZATION


# Case 5: gold에 없지만 evidence-grounded proposition -> EXTRA_GROUNDED_RELEVANT, never ACTUALLY_UNGROUNDED
def test_case_05_grounded_but_not_in_gold_is_extra_not_ungrounded():
    fixture = _build_sample_fixture()
    claims = [
        ProposedClaim(
            statement="PaymentService v3.1.0 deployment completed successfully.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        ),
        ProposedClaim(
            statement="Deployment occurred at 15:00 UTC time.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.STATE,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        ),
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 1
    # Extra claim is penalized in selection precision (FP=1)
    assert res.false_positives == 1
    # But strictly NOT counted as ACTUALLY_UNGROUNDED!
    assert len(res.ungrounded_propositions) == 0
    assert len(res.extra_grounded_claims) == 1
    extra_c = next(c for c in res.classified_claims if c.classification == ClaimClassification.EXTRA_GROUNDED_RELEVANT)
    assert "15:00 UTC" in extra_c.statement


# Case 6: evidence에 전혀 없는 proposition -> ACTUALLY_UNGROUNDED
def test_case_06_hallucinated_fact_is_actually_ungrounded():
    fixture = _build_sample_fixture()
    claims = [
        ProposedClaim(
            statement="Kubernetes cluster nodes were destroyed by a solar flare.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.EVENT,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        )
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 0
    assert res.false_positives == 1
    assert len(res.ungrounded_propositions) == 1
    assert res.classified_claims[0].classification == ClaimClassification.ACTUALLY_UNGROUNDED


# Case 7: correct reciprocal contradiction links -> contradiction preservation PASS
def test_case_07_correct_reciprocal_contradiction_links_pass():
    fixture = {
        "fixture_id": "EA2-CONTRADICTION-01",
        "category": "contradictory evidence",
        "question": "Billing API v2 배포 성공 여부",
        "evidence_nodes": [
            {"evidence_id": "EV_01", "text": "Billing API v2 deployment succeeded with zero errors."},
            {"evidence_id": "EV_02", "text": "Billing API v2 deployment failed completely with fatal crashloop."},
        ],
        "expected_semantic_units": [
            {
                "semantic_unit_id": "UNIT-01",
                "canonical_fact": "Billing API v2 deployment succeeded",
                "semantic_type": ["OUTCOME", "STATE"],
                "governing_evidence_ids": ["EV_01"],
                "required_contradicting_evidence_ids": ["EV_02"],
                "required_concept_groups": [["billing", "v2"], ["succeeded", "성공"]],
            },
            {
                "semantic_unit_id": "UNIT-02",
                "canonical_fact": "Billing API v2 deployment failed",
                "semantic_type": ["OUTCOME", "STATE"],
                "governing_evidence_ids": ["EV_02"],
                "required_contradicting_evidence_ids": ["EV_01"],
                "required_concept_groups": [["billing", "v2"], ["failed", "실패", "crashloop"]],
            },
        ],
        "optional_claims": [],
        "forbidden_claims": [],
        "expected_fail_closed": False,
        "expected_contradiction_preservation": True,
    }
    claims = [
        ProposedClaim(
            statement="Billing API v2 deployment succeeded with zero errors.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=["EV_02"],
        ),
        ProposedClaim(
            statement="Billing API v2 deployment failed with fatal crashloop.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_02"],
            contradicting_evidence_ids=["EV_01"],
        ),
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 2
    assert res.false_positives == 0
    assert res.contradiction_preservation_passed is True
    assert all(c.classification == ClaimClassification.GROUNDED_BUT_CONTRADICTED for c in res.classified_claims)


# Case 8: missing/wrong contradiction links -> contradiction preservation FAIL
def test_case_08_missing_or_wrong_contradiction_links_fail():
    fixture = {
        "fixture_id": "EA2-CONTRADICTION-01",
        "category": "contradictory evidence",
        "question": "Billing API v2 배포 성공 여부",
        "evidence_nodes": [
            {"evidence_id": "EV_01", "text": "Billing API v2 deployment succeeded with zero errors."},
            {"evidence_id": "EV_02", "text": "Billing API v2 deployment failed completely with fatal crashloop."},
        ],
        "expected_semantic_units": [
            {
                "semantic_unit_id": "UNIT-01",
                "canonical_fact": "Billing API v2 deployment succeeded",
                "semantic_type": ["OUTCOME", "STATE"],
                "governing_evidence_ids": ["EV_01"],
                "required_contradicting_evidence_ids": ["EV_02"],
                "required_concept_groups": [["billing", "v2"], ["succeeded", "성공"]],
            },
            {
                "semantic_unit_id": "UNIT-02",
                "canonical_fact": "Billing API v2 deployment failed",
                "semantic_type": ["OUTCOME", "STATE"],
                "governing_evidence_ids": ["EV_02"],
                "required_contradicting_evidence_ids": ["EV_01"],
                "required_concept_groups": [["billing", "v2"], ["failed", "실패"]],
            },
        ],
        "optional_claims": [],
        "forbidden_claims": [],
        "expected_fail_closed": False,
        "expected_contradiction_preservation": True,
    }
    # Missing contradicting_evidence_ids!
    claims = [
        ProposedClaim(
            statement="Billing API v2 deployment succeeded with zero errors.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],  # Missing link!
        ),
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.contradiction_preservation_passed is False


# Case 9: speculative meta-speech -> speculative leakage / forbidden
def test_case_09_speculative_meta_speech_leakage():
    fixture = {
        "fixture_id": "EA2-AMBIGUOUS-02",
        "category": "ambiguous evidence",
        "question": "노드풀 프로비저닝 여부",
        "evidence_nodes": [
            {"evidence_id": "EV_01", "text": "Someone mentioned we might need more nodes next week."},
        ],
        "expected_semantic_units": [],
        "optional_claims": [],
        "forbidden_claims": [
            {
                "forbidden_concept": "nodepool provisioned",
                "forbidden_tokens": [["nodepool", "nodes"], ["provisioned", "생성"]],
            }
        ],
        "expected_fail_closed": True,
    }
    claims = [
        ProposedClaim(
            statement="Someone mentioned we might need more nodes next week.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OTHER,
            supporting_evidence_ids=["EV_01"],
            contradicting_evidence_ids=[],
        )
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.false_positives == 1
    assert len(res.speculative_leaks) == 1
    assert res.classified_claims[0].classification == ClaimClassification.SPECULATIVE_OR_NONFACTUAL
    assert res.fail_closed_passed is False


# Case 10: invalid evidence ID -> hallucinated evidence ref
def test_case_10_invalid_evidence_id_hallucination():
    fixture = _build_sample_fixture()
    claims = [
        ProposedClaim(
            statement="PaymentService v3.1.0 deployment completed successfully.",
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME,
            supporting_evidence_ids=["EV_GHOST_99"],  # Hallucinated ID
            contradicting_evidence_ids=[],
        )
    ]
    res = evaluate_fixture_v2(fixture, claims)
    assert res.true_positives == 0
    assert res.false_positives == 1
    assert "EV_GHOST_99" in res.hallucinated_evidence_ids
    assert res.classified_claims[0].classification == ClaimClassification.INVALID_EVIDENCE_REFERENCE
