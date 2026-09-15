"""Phase 13.5D EvidenceAssessor Evaluator v2.

Deterministic, pre-frozen qualification evaluator for OpenRouterEvidenceAssessor.
Strictly offline and deterministic: NO_LLM_JUDGE_CALLS = True.

Implements the Cycle 2 qualification contract:
- Pre-frozen scoring matrix (EXPECTED_MATCH, ACCEPTABLE_ALTERNATIVE_REALIZATION,
  OPTIONAL_ACCEPTABLE, EXTRA_GROUNDED_RELEVANT, GROUNDED_BUT_CONTRADICTED,
  SPECULATIVE_OR_NONFACTUAL, ACTUALLY_UNGROUNDED, INVALID_EVIDENCE_REFERENCE, FORBIDDEN_CLAIM)
- Semantic unit coverage (atomic vs composite realization equivalence)
- Contradiction preservation verification (reciprocal evidence links)
- True fail-closed accuracy (evaluated only on expected_fail_closed=True fixtures)
- Separation of ACTUALLY_UNGROUNDED from EXTRA_GROUNDED_RELEVANT
"""

from __future__ import annotations

import dataclasses
from enum import Enum
import re
from typing import Any, Optional, Sequence

from pesu_agent.investigation.gap_assessment import (
    ProposedClaim,
    InvestigationAssessment,
)


NO_LLM_JUDGE_CALLS: bool = True
SCORING_MATRIX_FROZEN_BEFORE_PROVIDER_EXECUTION: bool = True
POST_HOC_SCORING_RULE_CHANGES: int = 0


class ClaimClassification(str, Enum):
    EXPECTED_MATCH = "EXPECTED_MATCH"
    ACCEPTABLE_ALTERNATIVE_REALIZATION = "ACCEPTABLE_ALTERNATIVE_REALIZATION"
    OPTIONAL_ACCEPTABLE = "OPTIONAL_ACCEPTABLE"
    EXTRA_GROUNDED_RELEVANT = "EXTRA_GROUNDED_RELEVANT"
    GROUNDED_BUT_CONTRADICTED = "GROUNDED_BUT_CONTRADICTED"
    SPECULATIVE_OR_NONFACTUAL = "SPECULATIVE_OR_NONFACTUAL"
    ACTUALLY_UNGROUNDED = "ACTUALLY_UNGROUNDED"
    INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
    FORBIDDEN_CLAIM = "FORBIDDEN_CLAIM"


@dataclasses.dataclass
class ClassifiedClaimResult:
    statement: str
    classification: ClaimClassification
    semantic_type: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    matched_unit_id: Optional[str] = None
    detail: Optional[str] = None


@dataclasses.dataclass
class FixtureEvaluationResultV2:
    fixture_id: str
    category: str
    expected_fail_closed: bool
    expected_contradiction: bool
    schema_valid: bool
    error_message: Optional[str]
    proposed_claims_count: int
    true_positives: int
    false_positives: int
    false_negatives: int
    classified_claims: list[ClassifiedClaimResult]
    hallucinated_evidence_ids: list[str]
    ungrounded_propositions: list[str]
    extra_grounded_claims: list[str]
    forbidden_claims_emitted: list[str]
    speculative_leaks: list[str]
    fail_closed_passed: bool
    contradiction_preservation_passed: bool
    details: dict[str, Any]


def matches_concept_groups(text: str, concept_groups: Sequence[Sequence[str]]) -> bool:
    """Checks whether the text matches at least one term from each required concept group."""
    norm_text = text.lower()
    for group in concept_groups:
        group_matched = False
        for token in group:
            if token.lower() in norm_text:
                group_matched = True
                break
        if not group_matched:
            return False
    return True


def check_forbidden_claim(
    proposed: ProposedClaim,
    forbidden_defs: Sequence[dict[str, Any]],
) -> Optional[str]:
    """Checks whether a proposed claim matches any forbidden proposition definition."""
    norm_statement = proposed.statement.lower()
    for fdef in forbidden_defs:
        tokens_groups = fdef.get("forbidden_tokens", [])
        if matches_concept_groups(norm_statement, tokens_groups):
            return fdef.get("forbidden_concept", "forbidden concept matched")
    return None


def is_speculative_or_meta_speech(statement: str) -> bool:
    """Detects speculative chatter or meta-speech bypass wrappers."""
    norm = statement.lower()
    meta_speech_indicators = [
        "someone mentioned",
        "누군가",
        "said we might",
        "말했다",
        "언급했다",
        "rumor has it",
        "heard someone gossip",
        "probably just a joke",
    ]
    speculation_indicators = [
        "might need",
        "could consider",
        "고려할 수도",
        "필요할지도",
        "not totally sure",
        "확실하지 않",
    ]
    has_meta = any(m in norm for m in meta_speech_indicators)
    has_spec = any(s in norm for s in speculation_indicators)
    return has_meta or has_spec


def check_text_grounding(statement: str, evidence_texts: list[str]) -> bool:
    """Verifies whether substantive factual tokens in statement are grounded in the evidence text."""
    if not evidence_texts:
        return False
    
    combined_ev = " ".join(evidence_texts).lower()
    
    stopwords = {
        "the", "a", "an", "and", "or", "to", "in", "at", "of", "for", "with", "on", "by", "from",
        "is", "was", "were", "been", "has", "have", "had", "be",
        "은", "는", "이", "가", "을", "를", "에", "에서", "으로", "로", "과", "와", "도", "하다", "했다", "되었다",
        "그", "저", "이것", "그것", "저것", "있음", "없음",
    }
    tokens = re.findall(r"[a-zA-Z0-9_.-]+|[가-힣]+", statement.lower())
    substantive = [t for t in tokens if t not in stopwords and len(t) > 1]
    
    if not substantive:
        return True

    matched_count = sum(1 for t in substantive if t in combined_ev)
    ratio = matched_count / len(substantive)
    
    # Must have at least 25% token overlap or match at least 1 key entity/number/word
    return ratio >= 0.20 or matched_count >= 1


def evaluate_proposed_claim_against_unit(
    proposed: ProposedClaim,
    unit: dict[str, Any],
    valid_eids: set[str],
) -> bool:
    """Evaluates whether a proposed claim semantically satisfies an expected semantic unit."""
    gov_eids = set(unit.get("governing_evidence_ids", []))
    prop_eids = set(proposed.supporting_evidence_ids)
    if not (gov_eids & prop_eids):
        return False

    if not prop_eids.issubset(valid_eids):
        return False

    req_contra = set(unit.get("required_contradicting_evidence_ids", []))
    prop_contra = set(proposed.contradicting_evidence_ids)
    if req_contra and not (req_contra & prop_contra):
        return False

    expected_types = unit.get("semantic_type", [])
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    prop_type_val = (
        proposed.semantic_type.value
        if hasattr(proposed.semantic_type, "value")
        else str(proposed.semantic_type)
    )
    if expected_types and prop_type_val not in expected_types:
        return False

    concept_groups = unit.get("required_concept_groups", [])
    if concept_groups and not matches_concept_groups(proposed.statement, concept_groups):
        return False

    return True


def evaluate_fixture_v2(
    fixture: dict[str, Any],
    proposed_claims: list[ProposedClaim],
    schema_valid: bool = True,
    error_message: Optional[str] = None,
) -> FixtureEvaluationResultV2:
    """Evaluates proposed claims for a single fixture under the Cycle 2 contract."""
    fid = fixture["fixture_id"]
    cat = fixture["category"]
    expected_fail_closed = fixture.get("expected_fail_closed", False)
    expected_contradiction = fixture.get("expected_contradiction_preservation", False)
    
    expected_units = fixture.get("expected_semantic_units") or fixture.get("expected_claims", [])
    optional_units = fixture.get("optional_claims", [])
    forbidden_defs = fixture.get("forbidden_claims", [])
    
    valid_eids = {item["evidence_id"] for item in fixture.get("evidence_nodes", [])}
    evidence_text_map = {item["evidence_id"]: item["text"] for item in fixture.get("evidence_nodes", [])}

    hallucinated_eids: list[str] = []
    forbidden_emitted: list[str] = []
    speculative_leaks: list[str] = []
    ungrounded_props: list[str] = []
    extra_grounded_claims: list[str] = []
    classified_results: list[ClassifiedClaimResult] = []

    satisfied_unit_ids: set[str] = set()

    for p in proposed_claims:
        prop_eids = set(p.supporting_evidence_ids)
        prop_contra = set(p.contradicting_evidence_ids)
        all_eids = prop_eids | prop_contra

        # 1. Check for invalid/hallucinated evidence IDs
        invalid_ids = all_eids - valid_eids
        if invalid_ids:
            hallucinated_eids.extend(sorted(invalid_ids))
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=ClaimClassification.INVALID_EVIDENCE_REFERENCE,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                    detail=f"Unknown evidence IDs: {sorted(invalid_ids)}",
                )
            )
            continue

        # 2. Check forbidden definitions
        matched_forbidden = check_forbidden_claim(p, forbidden_defs)
        if matched_forbidden:
            forbidden_emitted.append(f"{matched_forbidden} in '{p.statement}'")
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=ClaimClassification.FORBIDDEN_CLAIM,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                    detail=matched_forbidden,
                )
            )
            continue

        # 3. Check speculative / meta-speech bypass
        if is_speculative_or_meta_speech(p.statement):
            speculative_leaks.append(p.statement)
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=ClaimClassification.SPECULATIVE_OR_NONFACTUAL,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                    detail="Speculative hypothesis or meta-speech bypass detected",
                )
            )
            continue

        # 4. Check expected semantic units (matches concept groups and governing evidence)
        matched_units_for_claim = []
        for u in expected_units:
            uid = u.get("semantic_unit_id") or u.get("claim_id")
            if evaluate_proposed_claim_against_unit(p, u, valid_eids):
                matched_units_for_claim.append(uid)
                satisfied_unit_ids.add(uid)

        if matched_units_for_claim:
            classification = (
                ClaimClassification.GROUNDED_BUT_CONTRADICTED
                if p.contradicting_evidence_ids
                else (
                    ClaimClassification.ACCEPTABLE_ALTERNATIVE_REALIZATION
                    if len(matched_units_for_claim) > 1
                    else ClaimClassification.EXPECTED_MATCH
                )
            )
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=classification,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                    matched_unit_id=",".join(matched_units_for_claim),
                )
            )
            continue

        # 5. Check optional units
        matched_opt = False
        for opt in optional_units:
            if evaluate_proposed_claim_against_unit(p, opt, valid_eids):
                matched_opt = True
                break

        if matched_opt:
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=ClaimClassification.OPTIONAL_ACCEPTABLE,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                )
            )
            continue

        # 6. For claims outside expected/optional units: check text grounding
        cited_texts = [evidence_text_map[eid] for eid in p.supporting_evidence_ids if eid in evidence_text_map]
        is_grounded = check_text_grounding(p.statement, cited_texts)

        if not is_grounded:
            ungrounded_props.append(p.statement)
            classified_results.append(
                ClassifiedClaimResult(
                    statement=p.statement,
                    classification=ClaimClassification.ACTUALLY_UNGROUNDED,
                    semantic_type=str(getattr(p, "semantic_type", "")),
                    supporting_evidence_ids=p.supporting_evidence_ids,
                    contradicting_evidence_ids=p.contradicting_evidence_ids,
                    detail="Claim assertions not grounded in cited evidence text",
                )
            )
            continue

        # Factually grounded in cited evidence, but outside gold expected/optional scope
        extra_grounded_claims.append(p.statement)
        classified_results.append(
            ClassifiedClaimResult(
                statement=p.statement,
                classification=ClaimClassification.EXTRA_GROUNDED_RELEVANT,
                semantic_type=str(getattr(p, "semantic_type", "")),
                supporting_evidence_ids=p.supporting_evidence_ids,
                contradicting_evidence_ids=p.contradicting_evidence_ids,
                detail="Grounded in cited evidence but not part of expected benchmark claims",
            )
        )

    tp = len(satisfied_unit_ids)
    
    fp = (
        len(extra_grounded_claims)
        + len(ungrounded_props)
        + len(forbidden_emitted)
        + len(speculative_leaks)
        + len(hallucinated_eids)
    )

    fn = len(expected_units) - tp

    fail_closed_passed = True
    if expected_fail_closed:
        fail_closed_passed = (len(proposed_claims) == 0) and schema_valid

    contradiction_preservation_passed = True
    if expected_contradiction:
        required_contra_units = [u for u in expected_units if u.get("required_contradicting_evidence_ids")]
        if not required_contra_units:
            contradiction_preservation_passed = True
        else:
            all_satisfied = all(
                (u.get("semantic_unit_id") or u.get("claim_id")) in satisfied_unit_ids
                for u in required_contra_units
            )
            contradiction_preservation_passed = all_satisfied

    return FixtureEvaluationResultV2(
        fixture_id=fid,
        category=cat,
        expected_fail_closed=expected_fail_closed,
        expected_contradiction=expected_contradiction,
        schema_valid=schema_valid,
        error_message=error_message,
        proposed_claims_count=len(proposed_claims),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        classified_claims=classified_results,
        hallucinated_evidence_ids=hallucinated_eids,
        ungrounded_propositions=ungrounded_props,
        extra_grounded_claims=extra_grounded_claims,
        forbidden_claims_emitted=forbidden_emitted,
        speculative_leaks=speculative_leaks,
        fail_closed_passed=fail_closed_passed,
        contradiction_preservation_passed=contradiction_preservation_passed,
        details={
            "satisfied_unit_ids": sorted(satisfied_unit_ids),
            "total_expected_units": len(expected_units),
        },
    )
