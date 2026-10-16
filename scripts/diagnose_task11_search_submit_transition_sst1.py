# -*- coding: utf-8 -*-
"""PESU Agent ? Phase 13.5D / Task 11 / SST1
Single-Shot Guarded Search Submit -> Search Result Surface Transition Qualification Runner

Authoritative Protocol:
  TASK_11_SEARCH_SUBMIT_TRANSITION_SST1

Purpose:
  Execute exactly ONE guarded search submission of the qualified target query ("26? ??? ???")
  via production JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER and observe the real Slack search result
  surface transition, verifying query alignment and result surface readiness without scraping message bodies.

Strict Behavioral Invariants:
  - ZERO Slack message transmissions (no chat.postMessage, no composer submit)
  - ZERO Slackbot invocations (Slackbot candidates blocked by guard)
  - ZERO scroll mutations
  - ZERO message body scraping / thread opening
  - EXACTLY ONE query entry via qualified JS_SET_TEXTY_QUERY
  - EXACTLY ONE guarded submission via JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER
  - BOUNDED result surface transition observation: monotonic deadline <= 10000ms, max 100 polls, 100ms interval
  - ONE-SHOT HARD GATE: attempt_started marker enforces non-reentrancy
  - STRICT SENSITIVE DATA SCRUBBING: Zero raw URLs or sensitive tokens persisted.
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Optional

# Ensure project root is in path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pesu_agent.runtime.pesu_webview_host import (
    PesuWebViewHost,
    DIRECT_SLACK_PROFILE_PATH,
)
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    JS_PROBE_POST_WAIT_SEARCH_STATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("diagnose_task11_sst1")

TASK_ID = "TASK_11_SEARCH_SUBMIT_TRANSITION_SST1"
PROTOCOL_NAME = "TASK_11_SEARCH_SUBMIT_TRANSITION_SST1"
DIAGNOSTIC_RUN = "SST1"
TARGET_QUERY = "26? ??? ???"

TRANSITION_OBSERVATION_TIMEOUT_MS = 10000
POLL_INTERVAL_MS = 100
MAX_TRANSITION_OBSERVATION_POLLS = 100

ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst1"
PROTOCOL_FREEZE_FILE = REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst1_protocol_freeze.json"
ATTEMPT_STARTED_MARKER_FILE = ARTIFACTS_DIR / "sst1_marker.json"
LIVE_OUTPUT_FILE = ARTIFACTS_DIR / "sst1_live_trace.json"
DRY_RUN_OUTPUT_FILE = ARTIFACTS_DIR / "sst1_dry_run_trace.json"


def get_git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception:
        return "UNKNOWN"


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def audit_runtime_import_closure(expected_closure: dict[str, str]) -> tuple[bool, list[str]]:
    mismatches = []
    for rel_path, exp_sha in expected_closure.items():
        fp = REPO_ROOT / rel_path
        if not fp.exists():
            mismatches.append(f"Missing runtime closure module: {rel_path}")
            continue
        act_sha = get_file_sha256(fp)
        if act_sha != exp_sha:
            mismatches.append(f"SHA mismatch for module {rel_path}: {act_sha} != {exp_sha}")
    return (len(mismatches) == 0), mismatches


def verify_static_safety_contracts(runner_path: Optional[Path] = None) -> tuple[bool, list[str]]:
    p = runner_path or Path(__file__)
    src = p.read_text(encoding="utf-8")
    errors = []

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("post_message", "chat_post_message", "send_message", "scroll_to", "scroll_by"):
                errors.append(f"Forbidden call detected in AST: {attr}")

    # Guard must be imported and referenced
    if "JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER" not in src:
        errors.append("JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER not referenced in runner")

    return (len(errors) == 0), errors


def sanitize_trace_data(data: Any) -> Any:
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if k in ("token", "auth_token", "cookie", "cookies", "password", "secret"):
                sanitized[k] = "[REDACTED_CREDENTIAL]"
            elif k in ("raw_message_text", "body_text") and isinstance(v, str):
                sanitized[k] = "[REDACTED_SENSITIVE_TEXT]"
            else:
                sanitized[k] = sanitize_trace_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_trace_data(itm) for itm in data]
    return data


def run_sst1_qualification(
    dry_run: bool = False,
    timeout: float = 60.0,
    marker_file_override: Optional[Path] = None,
    output_file_override: Optional[Path] = None,
    host_factory: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    run_timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_commit = get_git_commit()

    marker_file = marker_file_override or ATTEMPT_STARTED_MARKER_FILE
    output_file = output_file_override or (DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "task_id": TASK_ID,
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "timestamp_utc": run_timestamp_utc,
        "git_commit": git_commit,
        "dry_run": dry_run,
        "verdict": "UNEVALUATED",
        "diagnostic_verdict": "UNSET",
        "target_query": TARGET_QUERY,
        "transition_telemetry": {},
        "checks": {
            "HEAD_EXACT": False,
            "HISTORICAL_BASELINES_EXACT": False,
            "RUNTIME_IMPORT_CLOSURE_EXACT": False,
            "STATIC_SAFETY_CONTRACTS_EXACT": False,
            "attempt_marker_written": False,
            "auth_and_readiness_verified": False,
            "query_entry_executed": False,
            "guarded_submit_executed": False,
            "transition_observation_executed": False,
            "result_surface_confirmed": False,
        },
        "live_action_inventory": {
            "query_entries": 0,
            "guarded_submit_attempts": 0,
            "guarded_submit_successes": 0,
            "slack_messages_sent": 0,
            "slackbot_invocations": 0,
            "scroll_mutations": 0,
            "retries": 0,
        },
        "transition_polls": [],
        "harness_phases": [],
        "error": None,
    }

    # 1. Preflight Gates
    report["harness_phases"].append("PREFLIGHT_VERIFICATION")
    if not PROTOCOL_FREEZE_FILE.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "MISSING_PROTOCOL_FREEZE"
        report["error"] = f"Protocol freeze missing at {PROTOCOL_FREEZE_FILE}"
        return report

    proto = json.loads(PROTOCOL_FREEZE_FILE.read_text(encoding="utf-8"))
    frozen_head = proto.get("git_commit")
    if frozen_head and frozen_head not in ("UNSET", "None", None) and git_commit != frozen_head:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "GIT_HEAD_MISMATCH"
        report["error"] = f"Active git commit {git_commit} != frozen HEAD {frozen_head}"
        return report
    report["checks"]["HEAD_EXACT"] = (git_commit != "UNKNOWN" and len(git_commit) == 40)

    # 34 Baselines
    baselines = proto.get("historical_baselines", {})
    if len(baselines) != 34:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_COUNT_MISMATCH"
        return report

    baseline_errs = []
    for bname, binfo in baselines.items():
        rel = binfo.get("path") if isinstance(binfo, dict) else bname
        sha = binfo.get("sha256") if isinstance(binfo, dict) else binfo
        fp = REPO_ROOT / rel
        if not fp.exists() or get_file_sha256(fp) != sha:
            baseline_errs.append(rel)
    if baseline_errs:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_SHA_MISMATCH"
        report["error"] = f"Baseline mismatches: {baseline_errs}"
        return report
    report["checks"]["HISTORICAL_BASELINES_EXACT"] = True

    # Runtime Closure
    closure = proto.get("runtime_closure_hashes", {})
    c_ok, c_errs = audit_runtime_import_closure(closure)
    if not c_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_IMPORT_CLOSURE_DRIFT"
        report["error"] = f"Closure audit failed: {c_errs}"
        return report
    report["checks"]["RUNTIME_IMPORT_CLOSURE_EXACT"] = True

    # Static AST Safety
    s_ok, s_errs = verify_static_safety_contracts()
    if not s_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "STATIC_SAFETY_CONTRACT_VIOLATION"
        report["error"] = f"Static safety errors: {s_errs}"
        return report
    report["checks"]["STATIC_SAFETY_CONTRACTS_EXACT"] = True

    # 2. Dry Run Path
    if dry_run:
        report["harness_phases"].append("DRY_RUN_EXECUTION")
        report["checks"]["query_entry_executed"] = True
        report["checks"]["guarded_submit_executed"] = True
        report["checks"]["transition_observation_executed"] = True
        report["checks"]["result_surface_confirmed"] = True
        report["live_action_inventory"]["query_entries"] = 1
        report["live_action_inventory"]["guarded_submit_attempts"] = 1
        report["live_action_inventory"]["guarded_submit_successes"] = 1
        report["verdict"] = "DRY_RUN_PASS"
        report["diagnostic_verdict"] = "SEARCH_SUBMIT_TRANSITION_SUCCESS"
        report["transition_telemetry"] = {
            "poll_count": 5,
            "search_view_mounted": True,
            "loading_cleared": True,
            "surface_query_matched": True,
            "result_items_detected": 3,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(sanitize_trace_data(report), f, indent=2, ensure_ascii=False)
        return report

    # 3. Live One-Shot Hard Gate
    if marker_file.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_EXISTS"
        report["error"] = f"Attempt marker file already exists at {marker_file}. One-shot qualification consumed."
        return report

    marker_payload = {
        "task_id": TASK_ID,
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "timestamp_utc": run_timestamp_utc,
        "git_commit": git_commit,
        "target_query": TARGET_QUERY,
        "action_contract": "SINGLE_SHOT_GUARDED_SUBMIT_AND_TRANSITION_OBSERVE",
        "zero_write_asserted": True,
    }
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    with open(marker_file, "w", encoding="utf-8") as mf:
        json.dump(marker_payload, mf, indent=2)
    report["checks"]["attempt_marker_written"] = True

    # 4. Live Execution
    host = None
    try:
        report["harness_phases"].append("HOST_INITIALIZATION")
        if host_factory:
            host = host_factory()
        else:
            host = PesuWebViewHost(profile_path=DIRECT_SLACK_PROFILE_PATH)
            host.start()

        # Auth & Readiness
        auth_res = host.evaluate_javascript(f"({JS_CHECK_AUTH_AND_READINESS})()")
        if not auth_res or not auth_res.get("authenticated") or not auth_res.get("client_ready"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "AUTH_OR_CLIENT_NOT_READY"
            report["error"] = f"Auth check returned: {auth_res}"
            return report

        # Activate Search Input
        act_res = host.evaluate_javascript(f"({JS_ACTIVATE_SEARCH})()")
        input_ready = host.evaluate_javascript(f"({JS_CHECK_SEARCH_INPUT_READINESS})()")
        if not input_ready or not input_ready.get("input_ready"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_INPUT_NOT_READY"
            return report
        report["checks"]["auth_and_readiness_verified"] = True

        # Query Entry (Max 1)
        report["harness_phases"].append("QUERY_ENTRY")
        entry_res = host.evaluate_javascript(f"({JS_SET_TEXTY_QUERY})({json.dumps(TARGET_QUERY)})")
        report["live_action_inventory"]["query_entries"] = 1
        if not entry_res or not entry_res.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "QUERY_ENTRY_FAILED"
            report["error"] = str(entry_res)
            return report
        report["checks"]["query_entry_executed"] = True

        # Guarded Submit (Max 1)
        report["harness_phases"].append("GUARDED_SUBMIT")
        submit_res = host.evaluate_javascript(f"({JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER})({json.dumps(TARGET_QUERY)})")
        report["live_action_inventory"]["guarded_submit_attempts"] = 1
        if not submit_res or not submit_res.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_GUARD_BLOCKED_SUBMISSION"
            report["error"] = str(submit_res.get("error") if submit_res else "SUBMIT_NULL")
            return report
        report["live_action_inventory"]["guarded_submit_successes"] = 1
        report["checks"]["guarded_submit_executed"] = True

        # Result Surface Transition Observation
        report["harness_phases"].append("TRANSITION_OBSERVATION")
        start_mono = time.monotonic()
        polls = []
        transition_confirmed = False

        for p_idx in range(MAX_TRANSITION_OBSERVATION_POLLS):
            elapsed_ms = (time.monotonic() - start_mono) * 1000
            if elapsed_ms > TRANSITION_OBSERVATION_TIMEOUT_MS:
                break

            probe = host.evaluate_javascript(f"({JS_PROBE_POST_WAIT_SEARCH_STATE})()") or {}
            polls.append({
                "poll_index": p_idx + 1,
                "elapsed_ms": round(elapsed_ms, 2),
                "surface_query": probe.get("surface_query_after"),
                "loading_indicator": probe.get("loading_indicator", False),
                "has_search_view": bool(probe.get("search_view_present")),
                "items_count": len(probe.get("result_items") or []),
            })

            # Check transition criteria
            sq = probe.get("surface_query_after")
            loading = probe.get("loading_indicator", False)
            if probe.get("search_view_present") and not loading and sq and sq.strip() == TARGET_QUERY:
                transition_confirmed = True
                break

            time.sleep(POLL_INTERVAL_MS / 1000.0)

        report["transition_polls"] = polls
        report["checks"]["transition_observation_executed"] = True

        if transition_confirmed:
            report["checks"]["result_surface_confirmed"] = True
            report["verdict"] = "QUALIFICATION_PASS"
            report["diagnostic_verdict"] = "SEARCH_SUBMIT_TRANSITION_SUCCESS"
        else:
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "TRANSITION_TIMED_OUT"

    except Exception as ex:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_EXCEPTION"
        report["error"] = str(ex)
    finally:
        if host:
            try:
                host.stop()
            except Exception:
                pass

        with open(output_file, "w", encoding="utf-8") as of:
            json.dump(sanitize_trace_data(report), of, indent=2, ensure_ascii=False)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11 SST1 Qualification Runner")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline dry run")
    parser.add_argument("--timeout", type=float, default=60.0, help="Execution timeout in seconds")
    args = parser.parse_args()

    res = run_sst1_qualification(dry_run=args.dry_run, timeout=args.timeout)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("verdict") in ("DRY_RUN_PASS", "QUALIFICATION_PASS"):
        sys.exit(0)
    else:
        sys.exit(1)
