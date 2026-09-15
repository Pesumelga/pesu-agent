#!/usr/bin/env python3
"""PESU — Phase 13.5D Task 10: EvidenceAssessor Real-Provider Qualification Runner.

Executes a bounded real-model qualification session for OpenRouterEvidenceAssessor
against the frozen 15-fixture benchmark in tests/fixtures/evidence_assessor_qualification_benchmark.json.

Governance & Invariants:
- LIVE_SLACK_ACTIONS = 0
- SLACK_WRITE_ACTIONS = 0
- REAL_PROVIDER_CALLS <= 30 (Candidate A: 15 logical, Candidate B: 15 logical if escalated)
- SEMANTIC_RETRIES = 0
- POST_HOC_GOLD_LABEL_CHANGES = 0
- FROZEN_ARTIFACT_HASH_DRIFT_COUNT = 0
- UNTRUSTED_EVIDENCE_DATA_BOUNDARY = True
- SCHEMA_VALIDITY_RATE = 100%
- CLAIM_EXTRACTION_MICRO_F1 >= 90.0%
- HALLUCINATED_EVIDENCE_ID_COUNT = 0
- UNGROUNDED_PROPOSITION_COUNT = 0
- FORBIDDEN_CLAIM_EMISSION_COUNT = 0
- FAIL_CLOSED_ACCURACY = 100%
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from pesu_agent.investigation.gap_assessment import (
    AssessmentAnswerability,
    AssessmentEvidenceItem,
    AssessmentPayload,
    EvidenceAssessmentProviderError,
    EvidenceAssessmentSchemaError,
    InvestigationAssessment,
    OpenRouterEvidenceAssessor,
    ProposedClaim,
)
from pesu_agent.investigation.graph_models import (
    ClaimKind,
    ClaimSemanticType,
)
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.openrouter import OpenRouterProvider

# Governance Constants
LIVE_SLACK_ACTIONS: int = 0
SLACK_WRITE_ACTIONS: int = 0
SEMANTIC_RETRIES: int = 0

EXPECTED_BENCHMARK_SHA256: str = "53132745bdbb01181d481534f7c249d1704e0b632db12caaf5c652daf1e13c8e"
BENCHMARK_FILE: Path = PROJECT_ROOT / "tests" / "fixtures" / "evidence_assessor_qualification_benchmark.json"
MANIFEST_FILE: Path = PROJECT_ROOT / "tests" / "fixtures" / "frozen_artifact_manifest_13_5d.json"


@dataclasses.dataclass(frozen=True)
class CandidateConfig:
    name: str
    model: str
    reasoning_effort: str
    temperature: float = 0.0


CANDIDATE_A = CandidateConfig(
    name="Candidate A",
    model="openai/gpt-5.6-luna",
    reasoning_effort="medium",
    temperature=0.0,
)

CANDIDATE_B = CandidateConfig(
    name="Candidate B",
    model="openai/gpt-5.6-luna",
    reasoning_effort="high",
    temperature=0.0,
)


def compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


def verify_frozen_manifest() -> int:
    """Verifies all 11 frozen files against the baseline manifest."""
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    artifacts = data.get("artifacts", [])
    drift_count = 0
    for item in artifacts:
        p = PROJECT_ROOT / item["path"]
        if not p.exists():
            drift_count += 1
            continue
        h = compute_file_sha256(p)
        if h != item["sha256"]:
            drift_count += 1
    return drift_count


def scan_payload_for_privacy(payload_text: str) -> tuple[bool, list[str]]:
    """Scans payload text to prevent leaking API keys, Slack tokens, or local paths."""
    violations: list[str] = []
    if re.search(r"sk-or-[a-zA-Z0-9_-]{20,}", payload_text):
        violations.append("Detected potential OpenRouter API key")
    if re.search(r"xox[baprs]-[0-9a-zA-Z]{10,}", payload_text):
        violations.append("Detected potential Slack token")
    if re.search(r"[a-zA-Z]:\\Users\\[^\\]+", payload_text, re.IGNORECASE):
        violations.append("Detected local absolute user file path")
    return (len(violations) == 0, violations)


def matches_concept_groups(text: str, concept_groups: list[list[str]]) -> bool:
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


def evaluate_proposed_claim_against_expected(
    proposed: ProposedClaim,
    expected: dict[str, Any],
    valid_evidence_ids: set[str],
) -> bool:
    """Evaluates whether a proposed claim semantically matches an expected gold claim."""
    # 1. Supporting evidence overlap
    expected_eids = set(expected.get("supporting_evidence_ids", []))
    prop_eids = set(proposed.supporting_evidence_ids)
    if not (expected_eids & prop_eids):
        return False

    # 2. No hallucinated evidence IDs
    if not prop_eids.issubset(valid_evidence_ids):
        return False

    # 3. Semantic type compatibility
    expected_types = expected.get("semantic_type", [])
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    prop_type_val = (
        proposed.semantic_type.value
        if hasattr(proposed.semantic_type, "value")
        else str(proposed.semantic_type)
    )
    if expected_types and prop_type_val not in expected_types:
        return False

    # 4. Required concept groups
    concept_groups = expected.get("required_concept_groups", [])
    if concept_groups and not matches_concept_groups(proposed.statement, concept_groups):
        return False

    return True


def check_forbidden_claim(
    proposed: ProposedClaim,
    forbidden_defs: list[dict[str, Any]],
) -> Optional[str]:
    """Checks whether a proposed claim matches any forbidden proposition definition."""
    norm_statement = proposed.statement.lower()
    for fdef in forbidden_defs:
        tokens_groups = fdef.get("forbidden_tokens", [])
        if matches_concept_groups(norm_statement, tokens_groups):
            return fdef.get("forbidden_concept", "forbidden concept matched")
    return None


@dataclasses.dataclass
class FixtureEvaluationResult:
    fixture_id: str
    category: str
    expected_fail_closed: bool
    schema_valid: bool
    error_message: Optional[str]
    proposed_claims_count: int
    true_positives: int
    false_positives: int
    false_negatives: int
    hallucinated_evidence_ids: list[str]
    ungrounded_propositions: list[str]
    forbidden_claims_emitted: list[str]
    fail_closed_passed: bool
    details: dict[str, Any]


async def execute_fixture_with_retry(
    assessor: OpenRouterEvidenceAssessor,
    payload: AssessmentPayload,
    fixture_id: str,
    candidate_name: str,
    max_retries: int = 2,
) -> tuple[Optional[InvestigationAssessment], Optional[Exception], list[dict[str, Any]]]:
    """Executes assessor on one fixture with physical infrastructure retry for 429/5xx/timeout.
    
    Zero semantic retries allowed.
    """
    retry_records: list[dict[str, Any]] = []
    attempt = 0
    while attempt <= max_retries:
        attempt += 1
        try:
            assessment = await assessor.assess(payload)
            return assessment, None, retry_records
        except Exception as e:
            err_str = str(e)
            is_infra_error = any(code in err_str for code in ["429", "500", "502", "503", "504", "timeout", "timed out", "Connection reset", "Connection refused"])
            
            if is_infra_error and attempt <= max_retries:
                backoff_s = 3.0 * attempt
                print(f"  [INFRA RETRY] Fixture {fixture_id} encountered {e}. Retrying attempt {attempt}/{max_retries} in {backoff_s:.1f}s...")
                retry_records.append({
                    "fixture_id": fixture_id,
                    "candidate": candidate_name,
                    "attempt": attempt,
                    "retry_reason": "INFRASTRUCTURE_ERROR",
                    "original_failure": err_str,
                })
                await asyncio.sleep(backoff_s)
                continue
            else:
                # Fatal or non-retryable (or retries exhausted)
                return None, e, retry_records


async def evaluate_candidate(
    candidate: CandidateConfig,
    benchmark_data: dict[str, Any],
    provider: OpenRouterProvider,
) -> tuple[dict[str, Any], list[FixtureEvaluationResult], list[dict[str, Any]]]:
    """Runs qualification evaluation for a single candidate configuration."""
    print("\n" + "=" * 76)
    print(f"QUALIFYING CANDIDATE: {candidate.name}")
    print(f"Model: {candidate.model} | Reasoning: {candidate.reasoning_effort} | Temp: {candidate.temperature}")
    print("=" * 76)

    assessor = OpenRouterEvidenceAssessor(
        provider=provider,
        model=candidate.model,
        reasoning_effort=candidate.reasoning_effort,
        temperature=candidate.temperature,
        fail_closed_on_provider_error=False,
    )

    all_retry_records: list[dict[str, Any]] = []
    fixture_results: list[FixtureEvaluationResult] = []
    fixtures = benchmark_data["fixtures"]

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_hallucinated_ids = 0
    total_ungrounded_props = 0
    total_forbidden_emitted = 0
    total_schema_valid = 0
    total_fail_closed_checks = 0
    passed_fail_closed_checks = 0

    for idx, f in enumerate(fixtures, 1):
        fid = f["fixture_id"]
        cat = f["category"]
        question = f["question"]
        expected_fail_closed = f["expected_fail_closed"]
        expected_claims = f.get("expected_claims", [])
        optional_claims = f.get("optional_claims", [])
        forbidden_claims = f.get("forbidden_claims", [])

        print(f"[{idx:02d}/15] {fid} ({cat}) ... ", end="", flush=True)

        payload = AssessmentPayload(
            question=question,
            evidence_items=[
                AssessmentEvidenceItem(
                    evidence_id=item["evidence_id"],
                    presentation_author=item.get("presentation_author", "Unknown"),
                    text=item["text"],
                    timestamp=item.get("timestamp", "None"),
                    retrieval_role=item.get("retrieval_role", "PRIMARY_SCOPE"),
                )
                for item in f["evidence_nodes"]
            ],
        )

        valid_eids = {item["evidence_id"] for item in f["evidence_nodes"]}

        # Privacy scan
        user_msg = assessor._build_user_message(payload)
        full_msg = assessor.SYSTEM_PROMPT + "\n" + user_msg
        ok_priv, violations = scan_payload_for_privacy(full_msg)
        if not ok_priv:
            raise RuntimeError(f"Privacy scan failed on {fid}: {violations}")

        assessment, err, retries = await execute_fixture_with_retry(
            assessor, payload, fid, candidate.name
        )
        all_retry_records.extend(retries)

        if err is not None:
            # Check if schema error or provider error
            schema_valid = False
            err_msg = str(err)
            proposed_claims: list[ProposedClaim] = []
        else:
            schema_valid = True
            err_msg = None
            proposed_claims = assessment.proposed_claims if assessment else []

        if schema_valid:
            total_schema_valid += 1

        # Check hallucinated evidence IDs
        fixture_hallucinations: list[str] = []
        for claim in proposed_claims:
            for eid in claim.supporting_evidence_ids + claim.contradicting_evidence_ids:
                if eid not in valid_eids:
                    fixture_hallucinations.append(eid)
        total_hallucinated_ids += len(fixture_hallucinations)

        # Check forbidden claims
        fixture_forbidden: list[str] = []
        for claim in proposed_claims:
            matched_f = check_forbidden_claim(claim, forbidden_claims)
            if matched_f:
                fixture_forbidden.append(f"{matched_f} in '{claim.statement}'")
        total_forbidden_emitted += len(fixture_forbidden)

        # Match claims
        tp = 0
        fp = 0
        fn = 0
        matched_expected_idx = set()
        ungrounded_claims: list[str] = []

        for p_claim in proposed_claims:
            claim_matched = False
            # Check against expected claims
            for e_idx, exp in enumerate(expected_claims):
                if e_idx in matched_expected_idx:
                    continue
                if evaluate_proposed_claim_against_expected(p_claim, exp, valid_eids):
                    matched_expected_idx.add(e_idx)
                    claim_matched = True
                    tp += 1
                    break

            if not claim_matched:
                # Check against optional claims
                for opt in optional_claims:
                    if evaluate_proposed_claim_against_expected(p_claim, opt, valid_eids):
                        claim_matched = True
                        tp += 1
                        break

            if not claim_matched:
                fp += 1
                ungrounded_claims.append(p_claim.statement)

        # False negatives: expected claims that were not matched
        fn = len(expected_claims) - len(matched_expected_idx)

        total_tp += tp
        total_fp += fp
        total_fn += fn
        total_ungrounded_props += len(ungrounded_claims)

        # Fail closed evaluation
        fail_closed_passed = True
        if expected_fail_closed:
            total_fail_closed_checks += 1
            if len(proposed_claims) == 0 and schema_valid:
                passed_fail_closed_checks += 1
                fail_closed_passed = True
            else:
                fail_closed_passed = False

        status_str = "PASS" if (schema_valid and len(fixture_hallucinations) == 0 and len(fixture_forbidden) == 0 and len(ungrounded_claims) == 0 and fn == 0 and fail_closed_passed) else "FLAGGED"
        print(f"{status_str} (claims={len(proposed_claims)}, tp={tp}, fp={fp}, fn={fn})")

        fixture_results.append(
            FixtureEvaluationResult(
                fixture_id=fid,
                category=cat,
                expected_fail_closed=expected_fail_closed,
                schema_valid=schema_valid,
                error_message=err_msg,
                proposed_claims_count=len(proposed_claims),
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                hallucinated_evidence_ids=fixture_hallucinations,
                ungrounded_propositions=ungrounded_claims,
                forbidden_claims_emitted=fixture_forbidden,
                fail_closed_passed=fail_closed_passed,
                details={
                    "proposed_claims": [
                        {
                            "statement": c.statement,
                            "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind),
                            "semantic_type": c.semantic_type.value if hasattr(c.semantic_type, "value") else str(c.semantic_type),
                            "supporting_evidence_ids": c.supporting_evidence_ids,
                            "contradicting_evidence_ids": c.contradicting_evidence_ids,
                        }
                        for c in proposed_claims
                    ]
                },
            )
        )

    # Compute Aggregate Metrics
    n_fixtures = len(fixtures)
    schema_validity_rate = (total_schema_valid / n_fixtures) * 100.0
    
    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    micro_f1 = (
        (2 * micro_precision * micro_recall) / (micro_precision + micro_recall) * 100.0
        if (micro_precision + micro_recall) > 0
        else 0.0
    )

    fail_closed_accuracy = (
        (passed_fail_closed_checks / total_fail_closed_checks) * 100.0
        if total_fail_closed_checks > 0
        else 100.0
    )

    # Gates Check
    schema_gate = schema_validity_rate >= 100.0
    f1_gate = micro_f1 >= 90.0
    hallucination_gate = total_hallucinated_ids == 0
    ungrounded_gate = total_ungrounded_props == 0
    forbidden_gate = total_forbidden_emitted == 0
    fail_closed_gate = fail_closed_accuracy >= 100.0

    all_gates_pass = (
        schema_gate
        and f1_gate
        and hallucination_gate
        and ungrounded_gate
        and forbidden_gate
        and fail_closed_gate
    )

    verdict = "PASS" if all_gates_pass else "FAIL"

    metrics = {
        "candidate": candidate.name,
        "model": candidate.model,
        "reasoning_effort": candidate.reasoning_effort,
        "temperature": candidate.temperature,
        "logical_provider_calls": assessor.logical_call_count,
        "physical_provider_calls": assessor.logical_call_count + len(all_retry_records),
        "infrastructure_retries": len(all_retry_records),
        "semantic_retries": 0,
        "prompt_tokens": assessor.prompt_tokens,
        "completion_tokens": assessor.completion_tokens,
        "total_tokens": assessor.total_tokens,
        "estimated_cost_usd": assessor.estimated_cost_usd,
        "schema_validity_rate": schema_validity_rate,
        "claim_extraction_micro_f1": micro_f1,
        "micro_precision": micro_precision * 100.0,
        "micro_recall": micro_recall * 100.0,
        "hallucinated_evidence_id_count": total_hallucinated_ids,
        "ungrounded_proposition_count": total_ungrounded_props,
        "forbidden_claim_emission_count": total_forbidden_emitted,
        "fail_closed_accuracy": fail_closed_accuracy,
        "gates": {
            "schema_validity_rate_100": schema_gate,
            "claim_extraction_micro_f1_gte_90": f1_gate,
            "hallucinated_evidence_id_count_zero": hallucination_gate,
            "ungrounded_proposition_count_zero": ungrounded_gate,
            "forbidden_claim_emission_count_zero": forbidden_gate,
            "fail_closed_accuracy_100": fail_closed_gate,
        },
        "verdict": verdict,
    }

    return metrics, fixture_results, all_retry_records


def save_qualification_artifacts(
    run_id: str,
    benchmark_commit: str,
    benchmark_sha256: str,
    candidate_a_metrics: dict[str, Any],
    candidate_a_fixtures: list[FixtureEvaluationResult],
    candidate_b_metrics: Optional[dict[str, Any]],
    candidate_b_fixtures: Optional[list[FixtureEvaluationResult]],
    final_verdict: str,
    qualified_config: Optional[CandidateConfig],
) -> Path:
    out_dir = PROJECT_ROOT / "output" / "qualification_13_5d" / "evidence_assessor" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. summary.json
    summary_data = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_commit": benchmark_commit,
        "benchmark_sha256": benchmark_sha256,
        "candidate_a": candidate_a_metrics,
        "candidate_b": candidate_b_metrics,
        "final_verdict": final_verdict,
        "qualified_config": dataclasses.asdict(qualified_config) if qualified_config else None,
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    # 2. fixture_predictions.json
    def serialize_fixtures(f_list: list[FixtureEvaluationResult]) -> list[dict[str, Any]]:
        return [dataclasses.asdict(fr) for fr in f_list]

    predictions_data = {
        "candidate_a": serialize_fixtures(candidate_a_fixtures),
        "candidate_b": serialize_fixtures(candidate_b_fixtures) if candidate_b_fixtures else None,
    }
    with open(out_dir / "fixture_predictions.json", "w", encoding="utf-8") as f:
        json.dump(predictions_data, f, indent=2, ensure_ascii=False)

    # 3. report.txt
    lines = [
        "=" * 76,
        f"PHASE 13.5D EVIDENCE ASSESSOR QUALIFICATION REPORT — {run_id}",
        "=" * 76,
        f"Benchmark Commit: {benchmark_commit}",
        f"Benchmark SHA256: {benchmark_sha256}",
        f"Timestamp: {summary_data['timestamp']}",
        f"Final Verdict: {final_verdict}",
        "",
        "--- CANDIDATE A METRICS ---",
        f"Model: {candidate_a_metrics['model']}",
        f"Reasoning Effort: {candidate_a_metrics['reasoning_effort']}",
        f"Logical Calls: {candidate_a_metrics['logical_provider_calls']}",
        f"Physical Calls: {candidate_a_metrics['physical_provider_calls']}",
        f"Schema Validity Rate: {candidate_a_metrics['schema_validity_rate']:.1f}%",
        f"Micro F1: {candidate_a_metrics['claim_extraction_micro_f1']:.1f}%",
        f"Hallucinated Evidence IDs: {candidate_a_metrics['hallucinated_evidence_id_count']}",
        f"Ungrounded Propositions: {candidate_a_metrics['ungrounded_proposition_count']}",
        f"Forbidden Claims Emitted: {candidate_a_metrics['forbidden_claim_emission_count']}",
        f"Fail-Closed Accuracy: {candidate_a_metrics['fail_closed_accuracy']:.1f}%",
        f"Candidate A Verdict: {candidate_a_metrics['verdict']}",
    ]
    if candidate_b_metrics:
        lines.extend([
            "",
            "--- CANDIDATE B METRICS ---",
            f"Model: {candidate_b_metrics['model']}",
            f"Reasoning Effort: {candidate_b_metrics['reasoning_effort']}",
            f"Logical Calls: {candidate_b_metrics['logical_provider_calls']}",
            f"Physical Calls: {candidate_b_metrics['physical_provider_calls']}",
            f"Schema Validity Rate: {candidate_b_metrics['schema_validity_rate']:.1f}%",
            f"Micro F1: {candidate_b_metrics['claim_extraction_micro_f1']:.1f}%",
            f"Hallucinated Evidence IDs: {candidate_b_metrics['hallucinated_evidence_id_count']}",
            f"Ungrounded Propositions: {candidate_b_metrics['ungrounded_proposition_count']}",
            f"Forbidden Claims Emitted: {candidate_b_metrics['forbidden_claim_emission_count']}",
            f"Fail-Closed Accuracy: {candidate_b_metrics['fail_closed_accuracy']:.1f}%",
            f"Candidate B Verdict: {candidate_b_metrics['verdict']}",
        ])
    else:
        lines.extend([
            "",
            "--- CANDIDATE B NOT EXECUTED (Candidate A passed all qualification gates) ---",
        ])

    with open(out_dir / "report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_dir


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 13.5D EvidenceAssessor Qualification Runner")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks only without model calls")
    args = parser.parse_args()

    print("=" * 76)
    print("PESU Phase 13.5D: OpenRouterEvidenceAssessor Real-Provider Qualification")
    print("=" * 76)

    # 1. Preflight checks
    if not BENCHMARK_FILE.exists():
        raise FileNotFoundError(f"Benchmark file missing: {BENCHMARK_FILE}")
    bench_sha = compute_file_sha256(BENCHMARK_FILE)
    if bench_sha != EXPECTED_BENCHMARK_SHA256:
        raise ValueError(f"Benchmark SHA256 mismatch: expected {EXPECTED_BENCHMARK_SHA256}, got {bench_sha}")
    print(f"[PREFLIGHT] Benchmark SHA256 matches frozen hash: {bench_sha}")

    drift_count = verify_frozen_manifest()
    if drift_count != 0:
        raise RuntimeError(f"Frozen artifact manifest drift detected: {drift_count} files drifted!")
    print("[PREFLIGHT] Frozen artifact manifest integrity verified: ZERO DRIFT")

    # Get git commit of benchmark
    import subprocess
    res = subprocess.run(
        ["git", "log", "-n", "1", "--format=%H", "--", str(BENCHMARK_FILE)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    benchmark_commit = res.stdout.strip()
    print(f"[PREFLIGHT] Benchmark Commit SHA: {benchmark_commit}")

    with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
        benchmark_data = json.load(f)

    if len(benchmark_data["fixtures"]) != 15:
        raise ValueError(f"Expected 15 fixtures, got {len(benchmark_data['fixtures'])}")

    config = LLMConfig()
    if not config.api_key:
        raise RuntimeError("OPENROUTER_API_KEY could not be resolved from environment or credentials store")
    print("[PREFLIGHT] OpenRouter API key resolved successfully (secret preserved)")

    if args.preflight_only:
        print("[PREFLIGHT] Preflight checks passed cleanly. Exiting (--preflight-only specified).")
        return

    provider = OpenRouterProvider(config=config)
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # 2. Execute Candidate A
    cand_a_metrics, cand_a_fixtures, cand_a_retries = await evaluate_candidate(
        CANDIDATE_A, benchmark_data, provider
    )

    candidate_b_metrics = None
    candidate_b_fixtures = None
    qualified_config = None

    if cand_a_metrics["verdict"] == "PASS":
        print("\n>>> CANDIDATE A QUALIFIED! All qualification gates passed.")
        final_verdict = "PASS"
        qualified_config = CANDIDATE_A
    else:
        print("\n>>> CANDIDATE A FAILED ONE OR MORE GATES. Escalating to Candidate B...")
        cand_b_metrics, cand_b_fixtures, cand_b_retries = await evaluate_candidate(
            CANDIDATE_B, benchmark_data, provider
        )
        candidate_b_metrics = cand_b_metrics
        candidate_b_fixtures = cand_b_fixtures
        if cand_b_metrics["verdict"] == "PASS":
            print("\n>>> CANDIDATE B QUALIFIED! All qualification gates passed.")
            final_verdict = "PASS"
            qualified_config = CANDIDATE_B
        else:
            print("\n>>> CANDIDATE B ALSO FAILED. Qualification VERDICT = FAIL.")
            final_verdict = "FAIL"

    # 3. Post-gate checks
    post_drift = verify_frozen_manifest()
    if post_drift != 0:
        raise RuntimeError(f"POST-GATE FROZEN ARTIFACT DRIFT DETECTED: {post_drift} files drifted!")

    # 4. Save artifacts
    out_dir = save_qualification_artifacts(
        run_id=run_id,
        benchmark_commit=benchmark_commit,
        benchmark_sha256=bench_sha,
        candidate_a_metrics=cand_a_metrics,
        candidate_a_fixtures=cand_a_fixtures,
        candidate_b_metrics=candidate_b_metrics,
        candidate_b_fixtures=candidate_b_fixtures,
        final_verdict=final_verdict,
        qualified_config=qualified_config,
    )

    print("\n" + "=" * 76)
    print("QUALIFICATION COMPLETE")
    print(f"Artifacts saved to: {out_dir}")
    print(f"Final Verdict: {final_verdict}")
    if qualified_config:
        print(f"Qualified Model: {qualified_config.model}")
        print(f"Qualified Reasoning Effort: {qualified_config.reasoning_effort}")
        print(f"Qualified Temperature: {qualified_config.temperature}")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())
