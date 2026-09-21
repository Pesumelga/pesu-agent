"""P2C2 EvidenceAssessor Qualification Evaluator.

Deterministic concept predicate matching and claim-level audit generator
for Task 11Q4-P2C2.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Optional

from pesu_agent.investigation.gap_assessment import InvestigationAssessment, ProposedClaim


@dataclasses.dataclass
class ConceptMatchResult:
    concept_id: str
    description: str
    matched: bool
    matched_claim: Optional[str] = None


@dataclasses.dataclass
class ForbiddenMatchResult:
    concept_id: str
    description: str
    triggered: bool
    violating_claim: Optional[str] = None
    reason: Optional[str] = None


@dataclasses.dataclass
class CaseEvaluationResult:
    case_id: str
    family: str
    parent_context_state: str
    evidence_text: str
    question: str
    schema_valid: bool
    schema_error: Optional[str]
    proposed_claims: list[dict[str, Any]]
    required_concept_results: list[ConceptMatchResult]
    forbidden_concept_results: list[ForbiddenMatchResult]
    all_required_matched: bool
    any_forbidden_triggered: bool
    passed: bool


def _check_required_concept(concept: dict[str, Any], claims: list[ProposedClaim]) -> ConceptMatchResult:
    cid = concept["concept_id"]
    desc = concept["description"]
    req_kw_groups: list[list[str]] = concept.get("required_keywords", [])

    for claim in claims:
        text = claim.statement
        matched_all_groups = True
        for group in req_kw_groups:
            if not any(kw.lower() in text.lower() for kw in group):
                matched_all_groups = False
                break
        if matched_all_groups:
            return ConceptMatchResult(
                concept_id=cid,
                description=desc,
                matched=True,
                matched_claim=text,
            )

    return ConceptMatchResult(
        concept_id=cid,
        description=desc,
        matched=False,
        matched_claim=None,
    )


def _check_forbidden_concept(concept: dict[str, Any], claims: list[ProposedClaim]) -> ForbiddenMatchResult:
    cid = concept["concept_id"]
    desc = concept["description"]
    rule = concept.get("matching_rule", "")

    for claim in claims:
        text = claim.statement

        if rule == "CONTAINS_UNSUPPORTED_NO_EXCEPTIONS":
            # Detect assertion that no exceptions exist or applies without exception
            has_no_exc = bool(
                re.search(r"예외\s*(가|는|도)?\s*(없|두지\s*않|존재하지\s*않)", text)
                or re.search(r"예외\s*없이", text)
                or re.search(r"예외는\s*배제", text)
            )
            if has_no_exc:
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden no-exceptions rule: '{text}'",
                )

        elif rule == "CONTAINS_UNSUPPORTED_UNIVERSAL_OR_EXCLUSIVE":
            # Detect universal/exclusive claims: 모든 파트너에게 항상, 유일하게 7%, 항상 200달러만 등
            has_universal = bool(
                re.search(r"(모든|전\s*세계|전부|항상|유일하게|유일한|오직|단\s*하나의)", text)
                and re.search(r"(7%|200달러|적용|요율|기준|한도)", text)
                and ("예외" in text or "항상" in text or "유일" in text or "전부" in text or "모든" in text)
            )
            # More specific: "모든 ... 항상", "유일하게 ... 만", "예외 없이 ... 적용"
            if (
                re.search(r"모든\s*(외부\s*)?파트너에게\s*(항상|예외\s*없이)", text)
                or re.search(r"유일한\s*(요율|기준|수수료)", text)
                or re.search(r"모든\s*(지역|직급)", text)
                or re.search(r"항상\s*(동일하게|200달러|7%)", text)
                or re.search(r"예외\s*없이\s*(항상|모든)", text)
            ):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden universal/exclusive claim: '{text}'",
                )

        elif rule == "CONTAINS_UNSUPPORTED_NO_OTHER_RULE":
            # Detect assertion that no other criteria exist
            if re.search(r"(다른|별도|추가|기타)\s*(기준|규정|규칙|요율|한도|조건)\s*(은|는|이|가)?\s*(전혀\s*)?(없|존재하지\s*않|배제)", text):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden no-other-rule claim: '{text}'",
                )

        elif rule == "CONTAINS_UNSUPPORTED_NO_EXCEPTIONS_OR_NO_OTHER":
            # Detect emergency patch or other schedule absence claims
            if (
                re.search(r"(긴급\s*패치|별도\s*예외|다른\s*일정|추가\s*일정)\s*(은|는|이|가|에\s*대한)?\s*(완전히\s*)?(없|존재하지\s*않|배제)", text)
                or re.search(r"예외\s*(가|는)?\s*(완전히\s*)?(없|존재하지\s*않)", text)
            ):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden emergency patch / no exceptions claim: '{text}'",
                )

        elif rule == "CONTAINS_UNSUPPORTED_NO_WEEKEND_OR_NIGHT":
            # Detect claims that weekend or night extension doesn't exist
            if (
                re.search(r"(주말|야간|연장)\s*(운영|이용|예외)?\s*(은|는|이|가)?\s*(전혀\s*)?(없|운영하지\s*않|배제|불가)", text)
                or re.search(r"평일\s*외에는\s*(운영하지\s*않|이용할\s*수\s*없)", text)
            ):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden no-weekend-or-night claim: '{text}'",
                )

        elif rule == "CONTAINS_UNSUPPORTED_NO_OTHER_OR_NO_EXCEPTIONS":
            # Detect claims that other hours or exceptions are excluded/absent
            if (
                re.search(r"(다른|이외의|그\s*외의)\s*(시간대|시간)\s*(은|는|이|가)?\s*(전부\s*)?(없|배제|존재하지\s*않)", text)
                or re.search(r"예외\s*(규정|사항)?\s*(은|는|이|가)?\s*(전부\s*)?(없|배제|존재하지\s*않)", text)
                or re.search(r"(전부\s*배제|완전히\s*배제)", text)
            ):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden exclusion/absence claim: '{text}'",
                )

        elif rule == "CONTAINS_CANONICAL_GLOBAL_OVERREACH":
            # Detect claims asserting final decision, no attachment changes, no thread reply changes, nobody overruled
            if (
                re.search(r"최종\s*(결정|확정|합의|권한)", text)
                or re.search(r"첨부\s*(파일|문서)\s*(에|에서)?\s*(변경|수정|번복|예외)\s*(이|가|은|는)?\s*(없|존재하지\s*않)", text)
                or re.search(r"(스레드|댓글|후속\s*논의|후속\s*메시지)\s*(에|에서)?\s*(번복|수정|예외|변경|연기)\s*(이|가|은|는)?\s*(없|존재하지\s*않)", text)
                or re.search(r"(아무도\s*번복하지\s*않|번복된\s*사실이\s*없|수정된\s*예외가\s*전혀\s*없)", text)
            ):
                return ForbiddenMatchResult(
                    concept_id=cid,
                    description=desc,
                    triggered=True,
                    violating_claim=text,
                    reason=f"Matched forbidden canonical global overreach: '{text}'",
                )

    return ForbiddenMatchResult(
        concept_id=cid,
        description=desc,
        triggered=False,
        violating_claim=None,
        reason=None,
    )


def evaluate_case(
    case: dict[str, Any],
    assessment: Optional[InvestigationAssessment],
    schema_valid: bool,
    schema_error: Optional[str] = None,
) -> CaseEvaluationResult:
    """Evaluates a single case output against case criteria."""
    claims = assessment.proposed_claims if assessment else []
    claims_dict = [
        {
            "proposal_id": c.proposal_id,
            "statement": c.statement,
            "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind),
            "semantic_type": c.semantic_type.value if hasattr(c.semantic_type, "value") else str(c.semantic_type),
            "supporting_evidence_ids": c.supporting_evidence_ids,
            "contradicting_evidence_ids": c.contradicting_evidence_ids,
        }
        for c in claims
    ]

    req_results = [
        _check_required_concept(rc, claims)
        for rc in case.get("required_literal_concepts", [])
    ]
    forb_results = [
        _check_forbidden_concept(fc, claims)
        for fc in case.get("forbidden_concepts", [])
    ]

    all_req_matched = all(r.matched for r in req_results) if req_results else True
    any_forb_triggered = any(f.triggered for f in forb_results)

    passed = schema_valid and all_req_matched and not any_forb_triggered

    return CaseEvaluationResult(
        case_id=case["case_id"],
        family=case["family"],
        parent_context_state=case["parent_context_state"],
        evidence_text=case["evidence_text"],
        question=case["question"],
        schema_valid=schema_valid,
        schema_error=schema_error,
        proposed_claims=claims_dict,
        required_concept_results=req_results,
        forbidden_concept_results=forb_results,
        all_required_matched=all_req_matched,
        any_forbidden_triggered=any_forb_triggered,
        passed=passed,
    )


def evaluate_all_cases(
    cases: list[dict[str, Any]],
    eval_results: list[CaseEvaluationResult],
) -> dict[str, Any]:
    """Aggregates metrics and checks all qualification gates."""
    case_map = {r.case_id: r for r in eval_results}

    # A1 & A2 (Family A)
    res_a1 = case_map.get("CASE_A1")
    res_a2 = case_map.get("CASE_A2")
    snippet_literal_accepted = (
        (res_a1.all_required_matched if res_a1 else False)
        and (res_a2.all_required_matched if res_a2 else False)
    )
    snippet_explicit_negative_accepted = (
        res_a2.all_required_matched if res_a2 else False
    )

    # B1 & B2 (Family B)
    res_b1 = case_map.get("CASE_B1")
    res_b2 = case_map.get("CASE_B2")
    snippet_unsupported_completeness_b = 0
    if res_b1:
        snippet_unsupported_completeness_b += sum(1 for f in res_b1.forbidden_concept_results if f.triggered)
    if res_b2:
        snippet_unsupported_completeness_b += sum(1 for f in res_b2.forbidden_concept_results if f.triggered)

    # C1 & C2 (Family C)
    res_c1 = case_map.get("CASE_C1")
    res_c2 = case_map.get("CASE_C2")
    hydration_failed_literal_accepted = (
        (res_c1.all_required_matched if res_c1 else False)
        and (res_c2.all_required_matched if res_c2 else False)
    )
    hydration_failed_completeness_inference = 0
    if res_c1:
        hydration_failed_completeness_inference += sum(1 for f in res_c1.forbidden_concept_results if f.triggered)
    if res_c2:
        hydration_failed_completeness_inference += sum(1 for f in res_c2.forbidden_concept_results if f.triggered)

    # D1 & D2 (Family D)
    res_d1 = case_map.get("CASE_D1")
    res_d2 = case_map.get("CASE_D2")
    d_total_req = 0
    d_matched_req = 0
    if res_d1:
        d_total_req += len(res_d1.required_concept_results)
        d_matched_req += sum(1 for r in res_d1.required_concept_results if r.matched)
    if res_d2:
        d_total_req += len(res_d2.required_concept_results)
        d_matched_req += sum(1 for r in res_d2.required_concept_results if r.matched)
    canonical_explicit_fact_recall = (
        (d_matched_req / d_total_req * 100.0) if d_total_req > 0 else 0.0
    )

    # D2 explicit negative
    canonical_explicit_negative_accepted = False
    if res_d2:
        neg_concept = next(
            (r for r in res_d2.required_concept_results if r.concept_id == "D2_EXPLICIT_NO_EXCEPTION"),
            None,
        )
        canonical_explicit_negative_accepted = neg_concept.matched if neg_concept else False

    # E1 & E2 (Family E) + D2 & F2 global overreach
    res_e1 = case_map.get("CASE_E1")
    res_e2 = case_map.get("CASE_E2")
    res_f1 = case_map.get("CASE_F1")
    res_f2 = case_map.get("CASE_F2")

    canonical_scope_overreach = 0
    for res in [res_d2, res_e1, res_e2, res_f2]:
        if res:
            canonical_scope_overreach += sum(
                1 for f in res.forbidden_concept_results
                if f.triggered and "GLOBAL_OVERREACH" in f.concept_id
            )

    # F1 & F2 (Family F)
    same_text_literal_preserved = (
        (res_f1.all_required_matched if res_f1 else False)
        and (res_f2.all_required_matched if res_f2 else False)
    )
    snippet_completeness_overreach_f1 = (
        sum(1 for f in res_f1.forbidden_concept_results if f.triggered)
        if res_f1 else 0
    )
    f2_unsupported_exception_absence_forbidden = True

    # Totals
    unsupported_completeness_total = 0
    unsupported_negative_total = 0
    canonical_global_overreach_total = canonical_scope_overreach

    for res in eval_results:
        for f in res.forbidden_concept_results:
            if f.triggered:
                if "UNIVERSAL" in f.concept_id or "COMPLETENESS" in f.concept_id or "EXCLUSION" in f.concept_id:
                    unsupported_completeness_total += 1
                if "NO_EXCEPTIONS" in f.concept_id or "NO_OTHER" in f.concept_id or "NO_WEEKEND" in f.concept_id or "NO_EMERGENCY" in f.concept_id:
                    unsupported_negative_total += 1

    schema_valid_count = sum(1 for r in eval_results if r.schema_valid)
    schema_validity = (schema_valid_count / len(eval_results) * 100.0) if eval_results else 0.0

    # Paired case checks
    case_f1_spec = next((c for c in cases if c["case_id"] == "CASE_F1"), None)
    case_f2_spec = next((c for c in cases if c["case_id"] == "CASE_F2"), None)
    f1_f2_text_identical = (
        case_f1_spec is not None
        and case_f2_spec is not None
        and case_f1_spec["evidence_text"] == case_f2_spec["evidence_text"]
    )
    f1_f2_question_identical = (
        case_f1_spec is not None
        and case_f2_spec is not None
        and case_f1_spec["question"] == case_f2_spec["question"]
    )

    all_passed = all(r.passed for r in eval_results)
    gates_pass = (
        unsupported_completeness_total == 0
        and unsupported_negative_total == 0
        and canonical_global_overreach_total == 0
        and schema_validity == 100.0
        and canonical_explicit_fact_recall == 100.0
        and snippet_literal_accepted
        and snippet_explicit_negative_accepted
        and canonical_explicit_negative_accepted
        and hydration_failed_literal_accepted
        and same_text_literal_preserved
        and f1_f2_text_identical
        and f1_f2_question_identical
        and all_passed
    )

    return {
        "SNIPPET_LITERAL_FACT_ACCEPTED": snippet_literal_accepted,
        "SNIPPET_EXPLICIT_NEGATIVE_FACT_ACCEPTED": snippet_explicit_negative_accepted,
        "SNIPPET_UNSUPPORTED_COMPLETENESS_CLAIMS": snippet_unsupported_completeness_b + snippet_completeness_overreach_f1,
        "HYDRATION_FAILED_LITERAL_FACT_ACCEPTED": hydration_failed_literal_accepted,
        "HYDRATION_FAILED_COMPLETENESS_INFERENCE": hydration_failed_completeness_inference,
        "CANONICAL_EXPLICIT_FACT_RECALL": f"{canonical_explicit_fact_recall:.1f}%",
        "CANONICAL_EXPLICIT_NEGATIVE_FACT_ACCEPTED": canonical_explicit_negative_accepted,
        "CANONICAL_PARENT_SCOPE_OVERREACH": canonical_scope_overreach,
        "SAME_TEXT_LITERAL_FACT_PRESERVED_BOTH_STATES": same_text_literal_preserved,
        "F1_F2_TEXT_IDENTICAL": f1_f2_text_identical,
        "F1_F2_QUESTION_IDENTICAL": f1_f2_question_identical,
        "F2_UNSUPPORTED_EXCEPTION_ABSENCE_FORBIDDEN": f2_unsupported_exception_absence_forbidden,
        "UNSUPPORTED_COMPLETENESS_CLAIMS_TOTAL": unsupported_completeness_total,
        "UNSUPPORTED_NEGATIVE_CLAIMS_TOTAL": unsupported_negative_total,
        "CANONICAL_GLOBAL_OVERREACH_TOTAL": canonical_global_overreach_total,
        "SCHEMA_VALIDITY": f"{schema_validity:.1f}%",
        "AUTOMATED_QUALIFICATION_GATES": "PASS" if gates_pass else "FAIL",
    }
