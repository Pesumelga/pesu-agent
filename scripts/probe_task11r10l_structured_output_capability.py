"""Task 11R10L -- Bounded Provider-Only Structured Output Capability Probe.

Strict execution protocol:
- Maximum physical calls = 3 (P1, P2, P3).
- Exact model route: openai/gpt-5.6-luna.
- Synthetic non-sensitive content only.
- ZERO Slack interaction (no browser, no CDP, no API).
- ZERO production code mutations.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Any, Optional

import httpx

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure workspace root and src in sys.path
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from pesu_agent.investigation.gap_assessment import InvestigationAssessment
from pesu_agent.llm.config import LLMConfig

TARGET_MODEL = "openai/gpt-5.6-luna"
MAX_PHYSICAL_CALLS = 3


class CallTracker:
    def __init__(self) -> None:
        self.call_count = 0

    def increment(self) -> None:
        if self.call_count >= MAX_PHYSICAL_CALLS:
            raise RuntimeError(
                f"SAFETY INVARIANT VIOLATION: Maximum physical calls ({MAX_PHYSICAL_CALLS}) exceeded!"
            )
        self.call_count += 1


tracker = CallTracker()


def get_api_credentials() -> tuple[str, str]:
    cfg = LLMConfig()
    api_key = cfg.get_api_key_value()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not configured.")
    base_url = cfg.openrouter_base_url.rstrip("/")
    return api_key, f"{base_url}/chat/completions"


def make_call(
    api_key: str,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    tracker.increment()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Pesumelga/pesu-agent",
        "X-Title": "pesu-agent-task11r10l",
    }

    t0 = time.perf_counter()
    with httpx.Client(timeout=60.0) as client:
        res = client.post(endpoint, json=payload, headers=headers)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    raw_text = res.text
    parsed_json = None
    try:
        parsed_json = res.json()
    except Exception:
        pass

    return {
        "http_status": res.status_code,
        "latency_ms": latency_ms,
        "headers": dict(res.headers),
        "raw_text": raw_text,
        "parsed_json": parsed_json,
    }


def execute_probe() -> dict[str, Any]:
    print("=" * 70)
    print("TASK 11R10L: BOUNDED PROVIDER-ONLY STRUCTURED OUTPUT CAPABILITY PROBE")
    print(f"Target Model: {TARGET_MODEL}")
    print(f"Max Calls Allowed: {MAX_PHYSICAL_CALLS}")
    print("=" * 70)

    api_key, endpoint = get_api_credentials()

    results: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_requested": TARGET_MODEL,
        "p1": None,
        "p2": None,
        "p3": None,
    }

    # ------------------------------------------------------------------
    # P1: Historical Failure Reproduction
    # ------------------------------------------------------------------
    print("\n[Executing P1 -- Historical Failure Reproduction]")
    p1_schema = InvestigationAssessment.model_json_schema()
    p1_payload = {
        "model": TARGET_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are an evidence assessment evaluator.",
            },
            {
                "role": "user",
                "content": "Analyze synthetic evidence and produce an assessment: 'Synthetic claim A verified.'",
            },
        ],
        "temperature": 0.0,
        "reasoning": {"effort": "medium"},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "InvestigationAssessment",
                "strict": True,
                "schema": p1_schema,
            },
        },
    }

    p1_res = make_call(api_key, endpoint, p1_payload)
    results["p1"] = {
        "http_status": p1_res["http_status"],
        "latency_ms": p1_res["latency_ms"],
        "raw_text": p1_res["raw_text"],
        "parsed_json": p1_res["parsed_json"],
        "routed_model": p1_res["headers"].get("x-openrouter-model")
        or (p1_res["parsed_json"].get("model") if p1_res["parsed_json"] else None),
        "response_format_payload": p1_payload["response_format"],
    }
    print(f"  P1 Status: {p1_res['http_status']}")
    print(f"  P1 Latency: {p1_res['latency_ms']:.1f}ms")
    print(f"  P1 Raw Body: {p1_res['raw_text']}")

    # ------------------------------------------------------------------
    # P2: Strict Subset Control
    # ------------------------------------------------------------------
    print("\n[Executing P2 -- Strict Subset Control]")
    p2_schema = {
        "type": "object",
        "properties": {
            "val": {"type": "string"},
        },
        "required": ["val"],
        "additionalProperties": False,
    }
    p2_payload = {
        "model": TARGET_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a minimal test assistant.",
            },
            {
                "role": "user",
                "content": "Return a JSON object with val set to 'ok'.",
            },
        ],
        "temperature": 0.0,
        "reasoning": {"effort": "medium"},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "StrictSubsetControl",
                "strict": True,
                "schema": p2_schema,
            },
        },
    }

    p2_res = make_call(api_key, endpoint, p2_payload)
    p2_content = None
    if p2_res["parsed_json"] and "choices" in p2_res["parsed_json"]:
        choices = p2_res["parsed_json"]["choices"]
        if choices:
            p2_content = choices[0].get("message", {}).get("content")

    results["p2"] = {
        "http_status": p2_res["http_status"],
        "latency_ms": p2_res["latency_ms"],
        "raw_text": p2_res["raw_text"],
        "parsed_json": p2_res["parsed_json"],
        "content": p2_content,
        "routed_model": p2_res["headers"].get("x-openrouter-model")
        or (p2_res["parsed_json"].get("model") if p2_res["parsed_json"] else None),
    }
    print(f"  P2 Status: {p2_res['http_status']}")
    print(f"  P2 Latency: {p2_res['latency_ms']:.1f}ms")
    print(f"  P2 Result: {p2_content if p2_content else p2_res['raw_text']}")

    # ------------------------------------------------------------------
    # P3: json_object Baseline
    # ------------------------------------------------------------------
    print("\n[Executing P3 -- json_object Baseline]")
    p3_payload = {
        "model": TARGET_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a minimal test assistant.\n\n"
                    "[OUTPUT_FORMAT_INSTRUCTION]\n"
                    'You MUST respond ONLY with a valid, parseable JSON object: {"val": "string"}\n'
                    "Do not output markdown codeblock ticks if possible, and do not output any explanatory text before or after the JSON."
                ),
            },
            {
                "role": "user",
                "content": "Return a JSON object with val set to 'ok'.",
            },
        ],
        "temperature": 0.0,
        "reasoning": {"effort": "medium"},
        "response_format": {
            "type": "json_object",
        },
    }

    p3_res = make_call(api_key, endpoint, p3_payload)
    p3_content = None
    if p3_res["parsed_json"] and "choices" in p3_res["parsed_json"]:
        choices = p3_res["parsed_json"]["choices"]
        if choices:
            p3_content = choices[0].get("message", {}).get("content")

    results["p3"] = {
        "http_status": p3_res["http_status"],
        "latency_ms": p3_res["latency_ms"],
        "raw_text": p3_res["raw_text"],
        "parsed_json": p3_res["parsed_json"],
        "content": p3_content,
        "routed_model": p3_res["headers"].get("x-openrouter-model")
        or (p3_res["parsed_json"].get("model") if p3_res["parsed_json"] else None),
    }
    print(f"  P3 Status: {p3_res['http_status']}")
    print(f"  P3 Latency: {p3_res['latency_ms']:.1f}ms")
    print(f"  P3 Result: {p3_content if p3_content else p3_res['raw_text']}")

    # ------------------------------------------------------------------
    # Decision Table Evaluation & Verdict Generation
    # ------------------------------------------------------------------
    p1_status = results["p1"]["http_status"]
    p2_status = results["p2"]["http_status"]
    p3_status = results["p3"]["http_status"]

    p1_raw = results["p1"]["raw_text"]
    p1_json = results["p1"]["parsed_json"]
    p1_root_err_msg = ""
    if p1_json and isinstance(p1_json, dict) and "error" in p1_json:
        err_obj = p1_json["error"]
        if isinstance(err_obj, dict):
            p1_root_err_msg = err_obj.get("message", str(err_obj))
        else:
            p1_root_err_msg = str(err_obj)
    else:
        p1_root_err_msg = p1_raw

    http_400_body_captured = bool(p1_status == 400 and p1_raw)

    if p1_status == 400 and p2_status == 200 and p3_status == 200:
        model_json_schema_capability = "SUPPORTED"
        current_schema_strict_compatibility = "FAILED"
        general_json_schema_valid = True
        strict_structured_output_schema_compatible = False
        provider_anomaly_root_cause = "RESPONSE_FORMAT_CONSTRUCTION_DEFECT"
        root_cause_confidence = "CONFIRMED"
        production_remediation_required = True
        preferred_remediation_direction = "USE_DIRECT_JSON_OBJECT"
    elif p1_status == 400 and p2_status == 400 and p3_status == 200:
        model_json_schema_capability = "UNSUPPORTED"
        current_schema_strict_compatibility = "UNRESOLVED"
        general_json_schema_valid = True
        strict_structured_output_schema_compatible = False
        provider_anomaly_root_cause = "MODEL_DOES_NOT_SUPPORT_JSON_SCHEMA"
        root_cause_confidence = "CONFIRMED"
        production_remediation_required = True
        preferred_remediation_direction = "USE_DIRECT_JSON_OBJECT"
    elif p1_status == 200:
        model_json_schema_capability = "SUPPORTED"
        current_schema_strict_compatibility = "SUPPORTED"
        general_json_schema_valid = True
        strict_structured_output_schema_compatible = True
        provider_anomaly_root_cause = "ANOMALY_NOT_DETERMINISTICALLY_REPRODUCED"
        root_cause_confidence = "UNRESOLVED"
        production_remediation_required = False
        preferred_remediation_direction = "UNRESOLVED"
    else:
        model_json_schema_capability = "UNRESOLVED"
        current_schema_strict_compatibility = "UNRESOLVED"
        general_json_schema_valid = True
        strict_structured_output_schema_compatible = False
        provider_anomaly_root_cause = "UNRESOLVED"
        root_cause_confidence = "UNRESOLVED"
        production_remediation_required = True
        preferred_remediation_direction = "UNRESOLVED"

    verdicts = {
        "HTTP_400_BODY_CAPTURED": http_400_body_captured,
        "P1_HTTP_STATUS": p1_status,
        "P2_HTTP_STATUS": p2_status,
        "P3_HTTP_STATUS": p3_status,
        "P1_ROOT_ERROR_MESSAGE": p1_root_err_msg,
        "MODEL_JSON_SCHEMA_CAPABILITY": model_json_schema_capability,
        "CURRENT_SCHEMA_STRICT_COMPATIBILITY": current_schema_strict_compatibility,
        "GENERAL_JSON_SCHEMA_VALID": general_json_schema_valid,
        "STRICT_STRUCTURED_OUTPUT_SCHEMA_COMPATIBLE": strict_structured_output_schema_compatible,
        "PROVIDER_ANOMALY_ROOT_CAUSE": provider_anomaly_root_cause,
        "ROOT_CAUSE_CONFIDENCE": root_cause_confidence,
        "PRODUCTION_REMEDIATION_REQUIRED": production_remediation_required,
        "PREFERRED_REMEDIATION_DIRECTION": preferred_remediation_direction,
    }

    results["verdicts"] = verdicts

    # Print Formatted Section 9 Verdicts
    print("\n" + "=" * 70)
    print("REQUIRED FINAL VERDICTS")
    print("=" * 70)
    for k, v in verdicts.items():
        print(f"{k} = {v}")
    print("=" * 70)

    # Save to artifacts
    artifacts_dir = WORKSPACE_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = artifacts_dir / "task11r10l_probe_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nArtifact saved to: {out_file}")

    return results


if __name__ == "__main__":
    execute_probe()
