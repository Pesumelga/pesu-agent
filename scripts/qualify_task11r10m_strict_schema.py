"""Task 11R10M: Bounded Provider Qualification Script.

Executes strictly 5 physical provider calls (1 per affected component) using
native json_schema only (no fallback enabled).
Completely offline from Slack (zero Slack reads/calls/writes, synthetic data only).

Qualification Matrix:
1. EvidenceAssessor -> InvestigationAssessment
2. ClaimVerifier -> ClaimVerificationAssessment
3. Stage1A -> EpisodeMembershipProposal
4. Stage1B -> WorkItemRelationProposal
5. Stage2 -> WorkItemLifecycleVerificationProposal
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath("src"))

from dotenv import load_dotenv
load_dotenv()

from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import LLMMessage
from pesu_agent.llm.openrouter import OpenRouterProvider, OpenRouterAPIError
from pesu_agent.investigation.gap_assessment import InvestigationAssessment
from pesu_agent.investigation.claim_verification import ClaimVerificationAssessment
from pesu_agent.investigation.workitem_synthesis import (
    EpisodeMembershipProposal,
    WorkItemRelationProposal,
    WorkItemLifecycleVerificationProposal,
)

TARGET_MODEL = "openai/gpt-5.6-luna"

MATRIX: list[dict[str, Any]] = [
    {
        "component": "EvidenceAssessor",
        "schema_cls": InvestigationAssessment,
        "prompt": "Evaluate this synthetic evidence and emit an InvestigationAssessment: Message from user: Task completed successfully.",
    },
    {
        "component": "ClaimVerifier",
        "schema_cls": ClaimVerificationAssessment,
        "prompt": "Verify this synthetic claim against synthetic evidence: Claim c-1 supported by Evidence e-1.",
    },
    {
        "component": "Stage1A",
        "schema_cls": EpisodeMembershipProposal,
        "prompt": "Group these synthetic claim IDs into an episode: c-1, c-2.",
    },
    {
        "component": "Stage1B",
        "schema_cls": WorkItemRelationProposal,
        "prompt": "Assign semantic relation for claim c-1 in work item WI-1: PRIMARY.",
    },
    {
        "component": "Stage2",
        "schema_cls": WorkItemLifecycleVerificationProposal,
        "prompt": "Assign lifecycle signal for claim c-1 in work item WI-1: COMPLETION.",
    },
]


async def run_qualification() -> dict[str, Any]:
    cfg = LLMConfig()
    api_key = cfg.get_api_key_value()
    if not api_key:
        print("FATAL: OPENROUTER_API_KEY is not configured.")
        sys.exit(1)

    provider = OpenRouterProvider(config=cfg)
    results: list[dict[str, Any]] = []
    total_physical_calls = 0
    total_fallback_count = 0
    total_logical_calls = 0

    print("=" * 70)
    print("TASK 11R10M: BOUNDED NATIVE-ONLY PROVIDER QUALIFICATION")
    print(f"Target Model: {TARGET_MODEL}")
    print("Slack Activity: 0 reads, 0 searches, 0 writes (COMPLETELY INERT)")
    print("Automatic Fallback: DISABLED (Native json_schema primary request only)")
    print(f"Planned Physical Calls: Exactly {len(MATRIX)}")
    print("=" * 70)

    for idx, item in enumerate(MATRIX, 1):
        comp_name = item["component"]
        schema_cls = item["schema_cls"]
        prompt = item["prompt"]

        print(f"\n[{idx}/{len(MATRIX)}] Executing {comp_name} ({schema_cls.__name__})...")

        # Construct strictly normalized response format
        response_format = provider.build_native_response_format(schema_cls)

        messages = [
            LLMMessage(
                role="system",
                content="You are a precise investigation assessment engine. Respond strictly conforming to the requested schema.",
            ),
            LLMMessage(role="user", content=prompt),
        ]

        total_logical_calls += 1
        t0 = time.perf_counter()

        try:
            # Native-only primary call: NO fallback, exactly 1 physical request
            total_physical_calls += 1
            resp = await provider.generate(
                messages=messages,
                model=TARGET_MODEL,
                temperature=0.0,
                response_format=response_format,
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0

            # Local Pydantic validation authority
            parsed = provider._parse_and_validate(resp.content, schema_cls)

            call_result = {
                "component": comp_name,
                "schema": schema_cls.__name__,
                "http_status": 200,
                "success": True,
                "physical_calls": 1,
                "fallback_count": 0,
                "latency_ms": round(latency_ms, 1),
                "tokens": resp.usage.total_tokens if resp.usage else None,
            }
            results.append(call_result)
            print(f"  --> HTTP 200 OK | Latency: {latency_ms:.1f}ms | Tokens: {call_result['tokens']}")
            print(f"  --> Local Pydantic validation: SUCCESS")

        except OpenRouterAPIError as err:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            print(f"  --> FAILED: HTTP {err.status_code}")
            print(f"  --> Error Code: {err.error_code}")
            print(f"  --> Upstream Provider: {err.upstream_provider}")
            print(f"  --> Provider Message: {err.provider_message}")
            print(f"  --> Raw Error Text: {err.raw_error_text}")

            call_result = {
                "component": comp_name,
                "schema": schema_cls.__name__,
                "http_status": err.status_code or 500,
                "success": False,
                "physical_calls": 1,
                "fallback_count": 0,
                "latency_ms": round(latency_ms, 1),
                "error": str(err),
                "raw_error_text": err.raw_error_text,
            }
            results.append(call_result)
            print("\nCRITICAL: Qualification failed on item! STOPPING IMMEDIATELY per protocol.")
            break

        except Exception as ex:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            print(f"  --> FAILED with unexpected exception: {ex}")
            call_result = {
                "component": comp_name,
                "schema": schema_cls.__name__,
                "http_status": 500,
                "success": False,
                "physical_calls": 1,
                "fallback_count": 0,
                "latency_ms": round(latency_ms, 1),
                "error": str(ex),
            }
            results.append(call_result)
            print("\nCRITICAL: Qualification failed on item! STOPPING IMMEDIATELY per protocol.")
            break

    await provider.close()

    # Summarize
    success_count = sum(1 for r in results if r["success"])
    success_rate = (success_count / len(MATRIX)) * 100.0
    amplification = (total_physical_calls / total_logical_calls) if total_logical_calls > 0 else 0.0

    summary = {
        "results": results,
        "total_logical_calls": total_logical_calls,
        "total_physical_calls": total_physical_calls,
        "total_fallback_count": total_fallback_count,
        "success_rate_percent": success_rate,
        "amplification": amplification,
        "all_passed": success_count == len(MATRIX),
    }

    print("\n" + "=" * 70)
    print("QUALIFICATION SUMMARY:")
    for r in results:
        status_str = "PASS (HTTP 200)" if r["success"] else f"FAIL (HTTP {r['http_status']})"
        print(f"  - {r['component']:<18}: {status_str} | physical={r['physical_calls']} | fallback={r['fallback_count']}")
    print(f"\nTotal Physical Calls : {total_physical_calls} / {len(MATRIX)}")
    print(f"Total Fallback Count : {total_fallback_count}")
    print(f"Native Success Rate  : {success_rate:.1f}%")
    print(f"Call Amplification   : {amplification:.1f}x")
    print("=" * 70)

    return summary


if __name__ == "__main__":
    summary = asyncio.run(run_qualification())
    if not summary["all_passed"]:
        sys.exit(1)
