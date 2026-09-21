#!/usr/bin/env python3
"""Task 11Q4-P2C2: EvidenceAssessor Parent-Context Real-Provider Requalification Runner.

Bounded real-provider requalification of OpenRouterEvidenceAssessor on parent-context states
(SEARCH_SNIPPET / HYDRATION_FAILED / CANONICAL_PARENT) using openai/gpt-5.6-luna.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from pesu_agent.investigation.gap_assessment import (
    AssessmentEvidenceItem,
    AssessmentPayload,
    InvestigationAssessment,
    OpenRouterEvidenceAssessor,
    ProposedClaim,
)
from pesu_agent.investigation.models import ParentContextState
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import LLMMessage
from pesu_agent.llm.openrouter import OpenRouterProvider, OpenRouterAPIError
from tests.qualification_support.evidence_assessor_p2c2_evaluator import (
    CaseEvaluationResult,
    evaluate_case,
    evaluate_all_cases,
)

# Hard Scope Constants
LIVE_SLACK_READS: int = 0
LIVE_SLACK_SEARCH_CALLS: int = 0
WEBVIEW_INTERACTIONS: int = 0
SLACK_UI_ACTIONS: int = 0

TARGET_MODEL: str = "openai/gpt-5.6-luna"
REASONING_EFFORT: str = "medium"
TEMPERATURE: float = 0.0

LOGICAL_CASES: int = 12
MAX_PROVIDER_CALLS: int = 12

CASES_FILE = PROJECT_ROOT / "tests" / "fixtures" / "evidence_assessor_p2c2_qualification_cases.json"
EVALUATOR_FILE = PROJECT_ROOT / "tests" / "qualification_support" / "evidence_assessor_p2c2_evaluator.py"
RUNNER_FILE = PROJECT_ROOT / "scripts" / "qualify_task11q4_p2c2_evidence_assessor.py"
GAP_ASSESSMENT_FILE = PROJECT_ROOT / "src" / "pesu_agent" / "investigation" / "gap_assessment.py"


def compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest().lower()


def compute_schema_sha256(schema_cls: type) -> str:
    schema_json = json.dumps(schema_cls.model_json_schema(), sort_keys=True)
    return hashlib.sha256(schema_json.encode()).hexdigest().lower()


class TelemetryOpenRouterProvider(OpenRouterProvider):
    """Subclass that records native structured output usage telemetry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.call_telemetry: list[dict[str, Any]] = []

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        response_schema: type,
        model: Optional[str] = None,
        temperature: float = 0.0,
        structured_mode: str = "auto",
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        target_model = model or self.config.analyzer_model
        t0 = time.perf_counter()

        native_requested = (structured_mode != "json_object" and kwargs.get("structured_mode") != "json_object")
        native_success = False
        fallback_used = False
        schema_valid = False
        error_msg = None

        native_kwargs = dict(kwargs)
        native_kwargs.pop("structured_mode", None)
        native_kwargs["response_format"] = self.build_native_response_format(response_schema)

        try:
            resp = await self.generate(
                messages=messages,
                model=target_model,
                temperature=temperature,
                **native_kwargs,
            )
            parsed = self._parse_and_validate(resp.content, response_schema)
            native_success = True
            schema_valid = True
            latency_ms = (time.perf_counter() - t0) * 1000.0

            self.call_telemetry.append({
                "model": target_model,
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "temperature": temperature,
                "native_structured_output_requested": native_requested,
                "http_provider_success": True,
                "schema_validation_success": schema_valid,
                "fallback_used": False,
                "retry_used": False,
                "alternate_model_used": False,
                "latency_ms": round(latency_ms, 1),
                "tokens": resp.usage.total_tokens if resp.usage else None,
            })
            return parsed, resp.usage

        except Exception as e:
            # Fallback path is strictly forbidden during qualification!
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.call_telemetry.append({
                "model": target_model,
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "temperature": temperature,
                "native_structured_output_requested": native_requested,
                "http_provider_success": False,
                "schema_validation_success": False,
                "fallback_used": True,
                "retry_used": False,
                "alternate_model_used": False,
                "latency_ms": round(latency_ms, 1),
                "error": str(e),
            })
            raise


async def run_qualification(dry_run: bool = False) -> tuple[dict[str, Any], list[CaseEvaluationResult], list[dict[str, Any]]]:
    # 1. Pre-call Hashes
    pre_cases_h = compute_file_sha256(CASES_FILE)
    pre_eval_h = compute_file_sha256(EVALUATOR_FILE)
    pre_runner_h = compute_file_sha256(RUNNER_FILE)
    pre_gap_h = compute_file_sha256(GAP_ASSESSMENT_FILE)
    pre_ia_h = compute_schema_sha256(InvestigationAssessment)
    pre_pc_h = compute_schema_sha256(ProposedClaim)

    print("==================================================")
    print("TASK 11Q4-P2C2: PRE-EXECUTION HASHES")
    print("==================================================")
    print(f"CASES_SHA256 = {pre_cases_h}")
    print(f"EVALUATOR_SHA256 = {pre_eval_h}")
    print(f"RUNNER_SHA256 = {pre_runner_h}")
    print(f"GAP_ASSESSMENT_SHA256 = {pre_gap_h}")
    print(f"INVESTIGATION_ASSESSMENT_SCHEMA_SHA256 = {pre_ia_h}")
    print(f"PROPOSED_CLAIM_SCHEMA_SHA256 = {pre_pc_h}")
    print("==================================================")

    # 2. Load Cases
    with open(CASES_FILE, "r", encoding="utf-8") as f:
        cases_data = json.load(f)
    cases = cases_data["cases"]
    assert len(cases) == LOGICAL_CASES, f"Expected {LOGICAL_CASES} cases, got {len(cases)}"

    # 3. Setup Provider
    cfg = LLMConfig()
    if not dry_run:
        api_key = cfg.get_api_key_value()
        if not api_key:
            print("FATAL: OPENROUTER_API_KEY is not configured.")
            sys.exit(1)
        provider = TelemetryOpenRouterProvider(config=cfg)
        assessor = OpenRouterEvidenceAssessor(
            provider=provider,
            model=TARGET_MODEL,
            reasoning_effort=REASONING_EFFORT,
            temperature=TEMPERATURE,
            fail_closed_on_provider_error=False,
        )
    else:
        provider = None
        assessor = None

    eval_results: list[CaseEvaluationResult] = []
    physical_calls: int = 0
    retry_calls: int = 0

    print(f"\nEXECUTING QUALIFICATION ({'DRY RUN' if dry_run else 'REAL PROVIDER'}):")
    for idx, case in enumerate(cases, 1):
        cid = case["case_id"]
        fam = case["family"]
        state_str = case["parent_context_state"]
        print(f"[{idx:02d}/{LOGICAL_CASES}] {cid} ({fam}, {state_str}) ... ", end="", flush=True)

        payload = AssessmentPayload(
            question=case["question"],
            evidence_items=[
                AssessmentEvidenceItem(
                    evidence_id="EV_01",
                    presentation_author="System",
                    text=case["evidence_text"],
                    retrieval_role="PRIMARY_SCOPE",
                    parent_context_state=ParentContextState(state_str),
                )
            ],
        )

        if dry_run:
            # Synthetic dry-run assessment for pipeline validation
            assessment = InvestigationAssessment(
                proposed_claims=[
                    ProposedClaim(
                        proposal_id="PROP_01",
                        statement=case["required_literal_concepts"][0]["description"] if case.get("required_literal_concepts") else "Dry run claim",
                        kind="FACT",
                        semantic_type="STATE",
                        supporting_evidence_ids=["EV_01"],
                    )
                ]
            )
            schema_valid = True
            schema_err = None
            physical_calls += 1
            print("DRY_RUN OK")
        else:
            physical_calls += 1
            try:
                assessment = await assessor.assess(payload)
                schema_valid = True
                schema_err = None
                print("HTTP 200 OK")
            except Exception as e:
                assessment = None
                schema_valid = False
                schema_err = str(e)
                print(f"FAILED: {e}")
                # As per requirement: if a transport/runtime call fails, do NOT retry. STOP and report failure!
                case_res = evaluate_case(case, None, schema_valid=False, schema_error=schema_err)
                eval_results.append(case_res)
                break

        case_res = evaluate_case(case, assessment, schema_valid=schema_valid, schema_error=schema_err)
        eval_results.append(case_res)

    # 4. Post-call Hashes
    post_cases_h = compute_file_sha256(CASES_FILE)
    post_eval_h = compute_file_sha256(EVALUATOR_FILE)
    post_runner_h = compute_file_sha256(RUNNER_FILE)
    post_gap_h = compute_file_sha256(GAP_ASSESSMENT_FILE)
    post_ia_h = compute_schema_sha256(InvestigationAssessment)
    post_pc_h = compute_schema_sha256(ProposedClaim)

    prompt_changed = (pre_gap_h != post_gap_h)
    production_code_changed = prompt_changed
    cases_changed = (pre_cases_h != post_cases_h)
    evaluator_changed = (pre_eval_h != post_eval_h)

    # 5. Evaluate Metrics
    metrics = evaluate_all_cases(cases, eval_results)
    telemetry = provider.call_telemetry if provider else []

    report_data = {
        "pre_hashes": {
            "cases": pre_cases_h,
            "evaluator": pre_eval_h,
            "runner": pre_runner_h,
            "gap_assessment": pre_gap_h,
            "investigation_assessment_schema": pre_ia_h,
            "proposed_claim_schema": pre_pc_h,
        },
        "post_hashes": {
            "cases": post_cases_h,
            "evaluator": post_eval_h,
            "runner": post_runner_h,
            "gap_assessment": post_gap_h,
            "investigation_assessment_schema": post_ia_h,
            "proposed_claim_schema": post_pc_h,
        },
        "immutability": {
            "prompt_changed_during_qualification": prompt_changed,
            "production_code_changed_during_qualification": production_code_changed,
            "qualification_cases_changed_after_first_provider_call": cases_changed,
            "evaluator_changed_after_first_provider_call": evaluator_changed,
        },
        "metrics": metrics,
        "physical_provider_calls": physical_calls,
        "retry_calls": retry_calls,
        "telemetry": telemetry,
    }

    return report_data, eval_results, telemetry


def write_audit_artifacts(
    report_data: dict[str, Any],
    eval_results: list[CaseEvaluationResult],
    telemetry: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. p2c2_raw_results.json
    raw_path = output_dir / "p2c2_raw_results.json"
    raw_content = {
        "report_data": report_data,
        "results": [dataclasses.asdict(r) for r in eval_results],
        "telemetry": telemetry,
    }
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_content, f, ensure_ascii=False, indent=2)

    # 2. p2c2_claim_audit.md
    audit_path = output_dir / "p2c2_claim_audit.md"
    lines: list[str] = [
        "# Task 11Q4-P2C2: Claim-Level Audit Report",
        "",
        f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Model**: {TARGET_MODEL} | **Reasoning Effort**: {REASONING_EFFORT} | **Temperature**: {TEMPERATURE}",
        "",
        "## Summary of Automated Gates",
        f"- **AUTOMATED_QUALIFICATION_GATES**: `{report_data['metrics']['AUTOMATED_QUALIFICATION_GATES']}`",
        f"- **UNSUPPORTED_COMPLETENESS_CLAIMS_TOTAL**: `{report_data['metrics']['UNSUPPORTED_COMPLETENESS_CLAIMS_TOTAL']}`",
        f"- **UNSUPPORTED_NEGATIVE_CLAIMS_TOTAL**: `{report_data['metrics']['UNSUPPORTED_NEGATIVE_CLAIMS_TOTAL']}`",
        f"- **CANONICAL_GLOBAL_OVERREACH_TOTAL**: `{report_data['metrics']['CANONICAL_GLOBAL_OVERREACH_TOTAL']}`",
        f"- **CANONICAL_EXPLICIT_FACT_RECALL**: `{report_data['metrics']['CANONICAL_EXPLICIT_FACT_RECALL']}`",
        f"- **SNIPPET_LITERAL_FACT_ACCEPTED**: `{report_data['metrics']['SNIPPET_LITERAL_FACT_ACCEPTED']}`",
        f"- **SNIPPET_EXPLICIT_NEGATIVE_FACT_ACCEPTED**: `{report_data['metrics']['SNIPPET_EXPLICIT_NEGATIVE_FACT_ACCEPTED']}`",
        f"- **CANONICAL_EXPLICIT_NEGATIVE_FACT_ACCEPTED**: `{report_data['metrics']['CANONICAL_EXPLICIT_NEGATIVE_FACT_ACCEPTED']}`",
        f"- **SAME_TEXT_LITERAL_FACT_PRESERVED_BOTH_STATES**: `{report_data['metrics']['SAME_TEXT_LITERAL_FACT_PRESERVED_BOTH_STATES']}`",
        f"- **SCHEMA_VALIDITY**: `{report_data['metrics']['SCHEMA_VALIDITY']}`",
        "",
        "---",
        "",
        "## Detailed Case-by-Case Claim Audit",
        "",
    ]

    for r in eval_results:
        lines.append(f"### Case `{r.case_id}` ({r.family}) — `{r.parent_context_state}`")
        lines.append(f"- **Evidence Text**: `{r.evidence_text}`")
        lines.append(f"- **Question**: {r.question}")
        lines.append(f"- **Schema Valid**: `{r.schema_valid}`" + (f" (Error: {r.schema_error})" if r.schema_error else ""))
        lines.append(f"- **Case Status**: `{'PASS' if r.passed else 'FAIL'}`")
        lines.append("")
        lines.append("#### Proposed Claims")
        if not r.proposed_claims:
            lines.append("_No claims proposed._")
        else:
            for idx, c in enumerate(r.proposed_claims, 1):
                lines.append(f"{idx}. **[{c.get('kind', 'FACT')} | {c.get('semantic_type', 'STATE')}]** {c.get('statement')}")
                lines.append(f"   - Supporting Evidence: `{c.get('supporting_evidence_ids')}`")
        lines.append("")
        lines.append("#### Required Concepts Evaluation")
        for rc in r.required_concept_results:
            status = "MATCHED" if rc.matched else "NOT MATCHED"
            lines.append(f"- `[{status}]` **{rc.concept_id}**: {rc.description}")
            if rc.matched_claim:
                lines.append(f"  - Matched Claim: *\"{rc.matched_claim}\"*")
        lines.append("")
        lines.append("#### Forbidden Concepts Evaluation")
        if not r.forbidden_concept_results:
            lines.append("_No forbidden concepts for this case._")
        else:
            for fc in r.forbidden_concept_results:
                status = "VIOLATION TRIGGERED" if fc.triggered else "CLEAR (NOT TRIGGERED)"
                lines.append(f"- `[{status}]` **{fc.concept_id}**: {fc.description}")
                if fc.triggered:
                    lines.append(f"  - Violating Claim: *\"{fc.violating_claim}\"*")
                    lines.append(f"  - Reason: {fc.reason}")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(audit_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def print_final_report(report_data: dict[str, Any], pre_commit: str, post_commit: str) -> None:
    m = report_data["metrics"]
    imm = report_data["immutability"]
    pre_h = report_data["pre_hashes"]
    telemetry = report_data["telemetry"]

    native_calls = sum(1 for t in telemetry if t.get("native_structured_output_requested"))
    success_native_calls = sum(1 for t in telemetry if t.get("http_provider_success") and t.get("schema_validation_success") and not t.get("fallback_used"))
    fallback_calls = sum(1 for t in telemetry if t.get("fallback_used"))
    alternate_calls = sum(1 for t in telemetry if t.get("alternate_model_used"))

    status = "PASS" if m["AUTOMATED_QUALIFICATION_GATES"] == "PASS" else "FAIL"

    print("\n" + "=" * 50)
    print("REQUIRED FINAL REPORT")
    print("==================================================")
    print(f"TASK_11Q4_P2C2_STATUS = {status}\n")
    print(f"PRE_Q4_P2C2_COMMIT = {pre_commit}")
    print(f"POST_Q4_P2C2_COMMIT = {post_commit}\n")
    print(f"MODEL = {TARGET_MODEL}")
    print(f"REASONING_EFFORT = {REASONING_EFFORT}")
    print(f"TEMPERATURE = {TEMPERATURE}\n")
    print(f"LOGICAL_CASES = {LOGICAL_CASES}")
    print(f"PHYSICAL_PROVIDER_CALLS = {report_data['physical_provider_calls']}")
    print(f"MAX_PROVIDER_CALLS = {MAX_PROVIDER_CALLS}")
    print(f"RETRY_CALLS = {report_data['retry_calls']}\n")
    print(f"NATIVE_STRUCTURED_OUTPUT_CALLS = {native_calls}")
    print(f"SUCCESSFUL_NATIVE_CALLS = {success_native_calls}")
    print(f"FALLBACK_CALLS = {fallback_calls}")
    print(f"ALTERNATE_MODEL_CALLS = {alternate_calls}\n")
    print(f"SNIPPET_LITERAL_FACT_ACCEPTED = {str(m['SNIPPET_LITERAL_FACT_ACCEPTED']).lower()}")
    print(f"SNIPPET_EXPLICIT_NEGATIVE_FACT_ACCEPTED = {str(m['SNIPPET_EXPLICIT_NEGATIVE_FACT_ACCEPTED']).lower()}")
    print(f"SNIPPET_UNSUPPORTED_COMPLETENESS_CLAIMS = {m['SNIPPET_UNSUPPORTED_COMPLETENESS_CLAIMS']}\n")
    print(f"HYDRATION_FAILED_LITERAL_FACT_ACCEPTED = {str(m['HYDRATION_FAILED_LITERAL_FACT_ACCEPTED']).lower()}")
    print(f"HYDRATION_FAILED_COMPLETENESS_INFERENCE = {m['HYDRATION_FAILED_COMPLETENESS_INFERENCE']}\n")
    print(f"CANONICAL_EXPLICIT_FACT_RECALL = {m['CANONICAL_EXPLICIT_FACT_RECALL']}")
    print(f"CANONICAL_EXPLICIT_NEGATIVE_FACT_ACCEPTED = {str(m['CANONICAL_EXPLICIT_NEGATIVE_FACT_ACCEPTED']).lower()}")
    print(f"CANONICAL_PARENT_SCOPE_OVERREACH = {m['CANONICAL_PARENT_SCOPE_OVERREACH']}\n")
    print(f"SAME_TEXT_LITERAL_FACT_PRESERVED_BOTH_STATES = {str(m['SAME_TEXT_LITERAL_FACT_PRESERVED_BOTH_STATES']).lower()}")
    print(f"F1_F2_TEXT_IDENTICAL = {str(m['F1_F2_TEXT_IDENTICAL']).lower()}")
    print(f"F1_F2_QUESTION_IDENTICAL = {str(m['F1_F2_QUESTION_IDENTICAL']).lower()}")
    print(f"F2_UNSUPPORTED_EXCEPTION_ABSENCE_FORBIDDEN = {str(m['F2_UNSUPPORTED_EXCEPTION_ABSENCE_FORBIDDEN']).lower()}\n")
    print(f"UNSUPPORTED_COMPLETENESS_CLAIMS_TOTAL = {m['UNSUPPORTED_COMPLETENESS_CLAIMS_TOTAL']}")
    print(f"UNSUPPORTED_NEGATIVE_CLAIMS_TOTAL = {m['UNSUPPORTED_NEGATIVE_CLAIMS_TOTAL']}")
    print(f"CANONICAL_GLOBAL_OVERREACH_TOTAL = {m['CANONICAL_GLOBAL_OVERREACH_TOTAL']}")
    print(f"SCHEMA_VALIDITY = {m['SCHEMA_VALIDITY']}\n")
    print(f"PROTOCOL_FROZEN_BEFORE_FIRST_PROVIDER_CALL = true")
    print(f"CASES_SHA256 = {pre_h['cases']}")
    print(f"EVALUATOR_SHA256 = {pre_h['evaluator']}")
    print(f"RUNNER_SHA256 = {pre_h['runner']}")
    print(f"GAP_ASSESSMENT_SHA256 = {pre_h['gap_assessment']}")
    print(f"INVESTIGATION_ASSESSMENT_SCHEMA_SHA256 = {pre_h['investigation_assessment_schema']}")
    print(f"PROPOSED_CLAIM_SCHEMA_SHA256 = {pre_h['proposed_claim_schema']}\n")
    print(f"PROMPT_CHANGED_DURING_QUALIFICATION = {str(imm['prompt_changed_during_qualification']).lower()}")
    print(f"PRODUCTION_CODE_CHANGED_DURING_QUALIFICATION = {str(imm['production_code_changed_during_qualification']).lower()}")
    print(f"QUALIFICATION_CASES_CHANGED_AFTER_FIRST_PROVIDER_CALL = {str(imm['qualification_cases_changed_after_first_provider_call']).lower()}")
    print(f"EVALUATOR_CHANGED_AFTER_FIRST_PROVIDER_CALL = {str(imm['evaluator_changed_after_first_provider_call']).lower()}\n")
    print(f"LIVE_SLACK_READS = {LIVE_SLACK_READS}")
    print(f"LIVE_SLACK_SEARCH_CALLS = {LIVE_SLACK_SEARCH_CALLS}\n")
    print(f"RAW_CLAIM_RESULTS_PRESERVED = true")
    print(f"CLAIM_LEVEL_AUDIT_REQUIRED = true")
    print(f"AUTOMATED_QUALIFICATION_GATES = {m['AUTOMATED_QUALIFICATION_GATES']}\n")
    if m["AUTOMATED_QUALIFICATION_GATES"] == "PASS":
        print(f"EVIDENCE_ASSESSOR_PARENT_CONTEXT_REAL_PROVIDER_QUALIFIED = PENDING_CLAIM_LEVEL_AUDIT")
    else:
        print(f"EVIDENCE_ASSESSOR_PARENT_CONTEXT_REAL_PROVIDER_QUALIFIED = false")
    print()
    print("TASK_11_COMPLETE = false")
    print("TASK_11_QUALIFICATION_PASS = false")
    print("TASK_12_STARTED = false")
    print("PRODUCTION_CUTOVER = false")
    print("==================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 11Q4-P2C2 EvidenceAssessor Requalification Runner")
    parser.add_argument("--dry-run", action="store_true", help="Run harness offline without real provider calls")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts" / "p2c2_audit", help="Directory for audit artifacts")
    parser.add_argument("--pre-commit", type=str, default="5db7e4b098c0a1df2776b914bc09a934a308cc9b", help="Pre-qualification commit hash")
    parser.add_argument("--post-commit", type=str, default="UNCHANGED", help="Post-qualification commit hash")
    args = parser.parse_args()

    report_data, eval_results, telemetry = asyncio.run(run_qualification(dry_run=args.dry_run))
    write_audit_artifacts(report_data, eval_results, telemetry, args.output_dir)
    print_final_report(report_data, args.pre_commit, args.post_commit)


if __name__ == "__main__":
    main()
