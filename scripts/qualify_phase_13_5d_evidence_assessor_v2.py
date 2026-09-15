#!/usr/bin/env python3
"""PESU — Phase 13.5D Task 10: EvidenceAssessor Real-Provider Qualification Runner (Cycle 2).

Executes a bounded real-model qualification session for OpenRouterEvidenceAssessor
against the frozen Benchmark v2 in tests/fixtures/evidence_assessor_qualification_benchmark_v2.json.

Governance & Invariants:
- LIVE_SLACK_ACTIONS = 0
- SLACK_WRITE_ACTIONS = 0
- REAL_PROVIDER_CALLS <= 30 (Candidate A: 15 logical, Candidate B: 15 logical if escalated)
- SEMANTIC_RETRIES = 0
- POST_HOC_GOLD_LABEL_CHANGES = 0
- POST_HOC_SCORING_RULE_CHANGES = 0
- SCORING_MATRIX_FROZEN_BEFORE_PROVIDER_EXECUTION = True
- NO_LLM_JUDGE_CALLS = True
- FROZEN_ARTIFACT_HASH_DRIFT_COUNT = 0
- FROZEN_BASELINE_MANIFEST_DRIFT = False
- SCHEMA_VALIDITY_RATE = 100%
- CLAIM_EXTRACTION_MICRO_F1 >= 90.0%
- HALLUCINATED_EVIDENCE_ID_COUNT = 0
- ACTUALLY_UNGROUNDED_PROPOSITION_COUNT = 0
- FORBIDDEN_CLAIM_EMISSION_COUNT = 0
- SPECULATIVE_OR_NONFACTUAL_LEAK_COUNT = 0
- FAIL_CLOSED_ACCURACY = 100%
- CONTRADICTION_PRESERVATION_ACCURACY = 100%
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
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
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.openrouter import OpenRouterProvider
from tests.qualification_support.evidence_assessor_evaluator_v2 import (
    FixtureEvaluationResultV2,
    evaluate_fixture_v2,
)

# Governance Constants
LIVE_SLACK_ACTIONS: int = 0
SLACK_WRITE_ACTIONS: int = 0
SEMANTIC_RETRIES: int = 0

EXPECTED_BENCHMARK_V2_SHA256: str = "21b9797bb0961a1645a1a30ade4a5f69760ffa5e98eed37c1ceaa3bb006f34f0"
BENCHMARK_V2_FILE: Path = PROJECT_ROOT / "tests" / "fixtures" / "evidence_assessor_qualification_benchmark_v2.json"
MANIFEST_FILE: Path = PROJECT_ROOT / "tests" / "fixtures" / "frozen_artifact_manifest_13_5d.json"
AUTHORITATIVE_MANIFEST_COMMIT: str = "853f5e084f975319787adfcfcdc61678e562b9e0"


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
    return hasher.hexdigest().lower()


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
        if h != item["sha256"].lower():
            drift_count += 1
    return drift_count


def verify_manifest_git_integrity() -> bool:
    """Verifies that the manifest file matches the authoritative git commit without uncommitted diff."""
    res = subprocess.run(
        ["git", "diff", "--exit-code", AUTHORITATIVE_MANIFEST_COMMIT, "HEAD", "--", str(MANIFEST_FILE)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    if res.returncode != 0:
        return False
    res2 = subprocess.run(
        ["git", "diff", "--exit-code", "--", str(MANIFEST_FILE)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    return res2.returncode == 0


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
            is_infra_error = any(
                code in err_str
                for code in ["429", "500", "502", "503", "504", "timeout", "timed out", "Connection reset", "Connection refused"]
            )
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
                return None, e, retry_records


async def evaluate_candidate_v2(
    candidate: CandidateConfig,
    benchmark_data: dict[str, Any],
    provider: OpenRouterProvider,
) -> tuple[dict[str, Any], list[FixtureEvaluationResultV2], list[dict[str, Any]]]:
    """Runs qualification evaluation for a single candidate configuration under Cycle 2 contract."""
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
    fixture_results: list[FixtureEvaluationResultV2] = []
    fixtures = benchmark_data["fixtures"]

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_hallucinated_ids = 0
    total_ungrounded_props = 0
    total_forbidden_emitted = 0
    total_speculative_leaks = 0
    total_extra_grounded = 0
    total_schema_valid = 0
    total_fail_closed_checks = 0
    passed_fail_closed_checks = 0
    total_contra_checks = 0
    passed_contra_checks = 0

    for idx, f in enumerate(fixtures, 1):
        fid = f["fixture_id"]
        cat = f["category"]
        question = f["question"]
        expected_fail_closed = f.get("expected_fail_closed", False)
        expected_contra = f.get("expected_contradiction_preservation", False)

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
            schema_valid = False
            err_msg = str(err)
            proposed_claims: list[ProposedClaim] = []
        else:
            schema_valid = True
            err_msg = None
            proposed_claims = assessment.proposed_claims if assessment else []

        if schema_valid:
            total_schema_valid += 1

        res = evaluate_fixture_v2(
            fixture=f,
            proposed_claims=proposed_claims,
            schema_valid=schema_valid,
            error_message=err_msg,
        )

        total_tp += res.true_positives
        total_fp += res.false_positives
        total_fn += res.false_negatives
        total_hallucinated_ids += len(res.hallucinated_evidence_ids)
        total_ungrounded_props += len(res.ungrounded_propositions)
        total_forbidden_emitted += len(res.forbidden_claims_emitted)
        total_speculative_leaks += len(res.speculative_leaks)
        total_extra_grounded += len(res.extra_grounded_claims)

        if expected_fail_closed:
            total_fail_closed_checks += 1
            if res.fail_closed_passed:
                passed_fail_closed_checks += 1

        if expected_contra:
            total_contra_checks += 1
            if res.contradiction_preservation_passed:
                passed_contra_checks += 1

        status_str = (
            "PASS"
            if (
                res.schema_valid
                and len(res.hallucinated_evidence_ids) == 0
                and len(res.ungrounded_propositions) == 0
                and len(res.forbidden_claims_emitted) == 0
                and len(res.speculative_leaks) == 0
                and res.false_negatives == 0
                and res.fail_closed_passed
                and res.contradiction_preservation_passed
            )
            else "FLAGGED"
        )
        print(
            f"{status_str} (claims={res.proposed_claims_count}, tp={res.true_positives}, "
            f"fp={res.false_positives}, fn={res.false_negatives})"
        )
        fixture_results.append(res)

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

    contra_accuracy = (
        (passed_contra_checks / total_contra_checks) * 100.0
        if total_contra_checks > 0
        else 100.0
    )

    # 8 Qualification Gates
    schema_gate = schema_validity_rate >= 100.0
    f1_gate = micro_f1 >= 90.0
    hallucination_gate = total_hallucinated_ids == 0
    ungrounded_gate = total_ungrounded_props == 0
    forbidden_gate = total_forbidden_emitted == 0
    speculative_gate = total_speculative_leaks == 0
    fail_closed_gate = fail_closed_accuracy >= 100.0
    contra_gate = contra_accuracy >= 100.0

    all_gates_pass = (
        schema_gate
        and f1_gate
        and hallucination_gate
        and ungrounded_gate
        and forbidden_gate
        and speculative_gate
        and fail_closed_gate
        and contra_gate
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
        "extra_grounded_claim_count": total_extra_grounded,
        "forbidden_claim_emission_count": total_forbidden_emitted,
        "speculative_or_nonfactual_leak_count": total_speculative_leaks,
        "fail_closed_accuracy": fail_closed_accuracy,
        "contradiction_preservation_accuracy": contra_accuracy,
        "gates": {
            "schema_validity_rate_100": schema_gate,
            "claim_extraction_micro_f1_gte_90": f1_gate,
            "hallucinated_evidence_id_count_zero": hallucination_gate,
            "ungrounded_proposition_count_zero": ungrounded_gate,
            "forbidden_claim_emission_count_zero": forbidden_gate,
            "speculative_or_nonfactual_leak_count_zero": speculative_gate,
            "fail_closed_accuracy_100": fail_closed_gate,
            "contradiction_preservation_accuracy_100": contra_gate,
        },
        "verdict": verdict,
    }

    return metrics, fixture_results, all_retry_records


async def main() -> None:
    parser = argparse.ArgumentParser(description="EvidenceAssessor Qualification Runner Cycle 2")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks only and exit")
    args = parser.parse_args()

    print("\n" + "=" * 76)
    print("PESU Phase 13.5D: OpenRouterEvidenceAssessor Real-Provider Qualification (Cycle 2)")
    print("=" * 76)

    # 1. Preflight checks
    if not BENCHMARK_V2_FILE.exists():
        raise FileNotFoundError(f"Benchmark v2 file missing: {BENCHMARK_V2_FILE}")
    bench_sha = compute_file_sha256(BENCHMARK_V2_FILE)
    if bench_sha != EXPECTED_BENCHMARK_V2_SHA256:
        raise ValueError(f"Benchmark v2 SHA256 mismatch: expected {EXPECTED_BENCHMARK_V2_SHA256}, got {bench_sha}")
    print(f"[PREFLIGHT] Benchmark v2 SHA256 matches frozen hash: {bench_sha}")

    if not verify_manifest_git_integrity():
        raise RuntimeError("Frozen baseline manifest git drift detected!")
    print(f"[PREFLIGHT] Frozen baseline manifest git tree verified against {AUTHORITATIVE_MANIFEST_COMMIT}")

    drift_count = verify_frozen_manifest()
    if drift_count != 0:
        raise RuntimeError(f"Frozen artifact manifest drift detected: {drift_count} files drifted!")
    print("[PREFLIGHT] Frozen artifact manifest integrity verified: ZERO DRIFT")

    with open(BENCHMARK_V2_FILE, "r", encoding="utf-8") as f:
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

    # Check uncommitted drift on qualification-affecting files
    check_files = [
        "src/pesu_agent/investigation/gap_assessment.py",
        "tests/fixtures/evidence_assessor_qualification_benchmark_v2.json",
        "tests/qualification_support/evidence_assessor_evaluator_v2.py",
        "scripts/qualify_phase_13_5d_evidence_assessor_v2.py",
    ]
    diff_res = subprocess.run(
        ["git", "diff", "--exit-code", "--"] + check_files,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    if diff_res.returncode != 0:
        print("[WARNING] Qualification inputs contain uncommitted changes.")

    provider = OpenRouterProvider(config=config)
    run_id = f"run_cycle2_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # 2. Candidate A execution
    cand_a_metrics, cand_a_fixtures, cand_a_retries = await evaluate_candidate_v2(
        CANDIDATE_A, benchmark_data, provider
    )

    candidate_b_metrics = None
    candidate_b_fixtures = None
    qualified_config = None

    if cand_a_metrics["verdict"] == "PASS":
        print("\n>>> CANDIDATE A QUALIFIED! All 8 qualification gates passed.")
        final_verdict = "PASS"
        qualified_config = CANDIDATE_A
    else:
        print("\n>>> CANDIDATE A FAILED ONE OR MORE GATES. Escalating to Candidate B...")
        cand_b_metrics, cand_b_fixtures, cand_b_retries = await evaluate_candidate_v2(
            CANDIDATE_B, benchmark_data, provider
        )
        candidate_b_metrics = cand_b_metrics
        candidate_b_fixtures = cand_b_fixtures

        if cand_b_metrics["verdict"] == "PASS":
            print("\n>>> CANDIDATE B QUALIFIED! All 8 qualification gates passed.")
            final_verdict = "PASS"
            qualified_config = CANDIDATE_B
        else:
            print("\n>>> CANDIDATE B FAILED QUALIFICATION GATES. Qualification verdict: FAIL")
            final_verdict = "FAIL"

    # Save qualification artifacts
    output_dir = PROJECT_ROOT / "output" / "qualification_13_5d" / "evidence_assessor" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_data = {
        "run_id": run_id,
        "cycle": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_v2_sha256": bench_sha,
        "candidate_a": cand_a_metrics,
        "candidate_b": candidate_b_metrics,
        "final_verdict": final_verdict,
        "qualified_config": dataclasses.asdict(qualified_config) if qualified_config else None,
    }

    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    predictions_data = {
        "candidate_a": [dataclasses.asdict(r) for r in cand_a_fixtures],
        "candidate_b": [dataclasses.asdict(r) for r in candidate_b_fixtures] if candidate_b_fixtures else None,
    }

    with open(output_dir / "fixture_predictions.json", "w", encoding="utf-8") as f:
        json.dump(predictions_data, f, indent=2, ensure_ascii=False)

    report_lines = [
        "=" * 76,
        f"PHASE 13.5D EVIDENCE ASSESSOR QUALIFICATION REPORT (CYCLE 2) — {run_id}",
        "=" * 76,
        f"Benchmark v2 SHA256: {bench_sha}",
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"Final Verdict: {final_verdict}",
        "",
        "--- CANDIDATE A METRICS ---",
        f"Model: {cand_a_metrics['model']}",
        f"Reasoning Effort: {cand_a_metrics['reasoning_effort']}",
        f"Logical Calls: {cand_a_metrics['logical_provider_calls']}",
        f"Physical Calls: {cand_a_metrics['physical_provider_calls']}",
        f"Schema Validity Rate: {cand_a_metrics['schema_validity_rate']:.1f}%",
        f"Micro F1: {cand_a_metrics['claim_extraction_micro_f1']:.1f}%",
        f"Hallucinated Evidence IDs: {cand_a_metrics['hallucinated_evidence_id_count']}",
        f"Ungrounded Propositions: {cand_a_metrics['ungrounded_proposition_count']}",
        f"Extra Grounded Claims: {cand_a_metrics['extra_grounded_claim_count']}",
        f"Forbidden Claims Emitted: {cand_a_metrics['forbidden_claim_emission_count']}",
        f"Speculative Leaks: {cand_a_metrics['speculative_or_nonfactual_leak_count']}",
        f"Fail-Closed Accuracy: {cand_a_metrics['fail_closed_accuracy']:.1f}%",
        f"Contradiction Preservation Accuracy: {cand_a_metrics['contradiction_preservation_accuracy']:.1f}%",
        f"Candidate A Verdict: {cand_a_metrics['verdict']}",
    ]

    if candidate_b_metrics:
        report_lines.extend([
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
            f"Extra Grounded Claims: {candidate_b_metrics['extra_grounded_claim_count']}",
            f"Forbidden Claims Emitted: {candidate_b_metrics['forbidden_claim_emission_count']}",
            f"Speculative Leaks: {candidate_b_metrics['speculative_or_nonfactual_leak_count']}",
            f"Fail-Closed Accuracy: {candidate_b_metrics['fail_closed_accuracy']:.1f}%",
            f"Contradiction Preservation Accuracy: {candidate_b_metrics['contradiction_preservation_accuracy']:.1f}%",
            f"Candidate B Verdict: {candidate_b_metrics['verdict']}",
        ])
    else:
        report_lines.extend([
            "",
            "--- CANDIDATE B METRICS ---",
            "NOT_RUN_BECAUSE_CANDIDATE_A_PASSED",
        ])

    report_content = "\n".join(report_lines) + "\n"
    with open(output_dir / "report.txt", "w", encoding="utf-8") as f:
        f.write(report_content)

    print("\n" + report_content)
    print(f"Artifacts saved to: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
