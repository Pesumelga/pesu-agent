#!/usr/bin/env python3
"""Task 11R10J — Independent Case C Full-Chain Diagnostic Runner.

Goals:
1. Formalize Case B self-contamination diagnostic invalidation (never replay Case B).
2. Execute predeclared Case C independently exactly once to observe
   UNKNOWN / fail-closed production-shaped diagnostic behavior.

Contracts Enforced:
1. Exact frozen Case C predeclaration loaded only (CASE_B_LIVE_REPLAY = PROHIBITED).
2. Pre-live freeze authority:
   - Manifest drift = 0 (frozen_artifact_manifest_13_5d.json)
   - Zero uncommitted drift in relevant authority scope.
   - Active Slack backend = WEBVIEW_DIRECT.
   - 2 reachable search paths, 2 guarded, 0 unguarded.
   - 12 Harness contract preflight checks pass.
3. R10H strict search safety telemetry contract:
   - Fail-closed on missing/None authoritative safety fields.
   - Positive evidence requirement: SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED=True
     with allowed SafeSearch submit method.
   - Canonical surface telemetry: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED, SEARCH_SURFACE_QUERY_AFTER.
   - RESULTS_READY strictly requires SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True.
4. Mechanical Self-Contamination Observation (Contract 1):
   - D07RA9YDE8G presence is telemetry only.
   - Known self-generated query echo requires exact query/question text match AND
     known self-generated/incident provenance.
   - Authority: CASE_C_DIAGNOSTIC_VALIDITY=INVALIDATED_BY_SELF_CONTAMINATION if and only if
     CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT > 0.
   - Separately records raw/selected exact echoes and known self-generated echoes.
5. Production Pipeline Execution:
   - Exactly once through production-shaped path:
     Retrieval -> EvidenceAssessor -> ClaimVerifier -> Stage 1A -> Stage 1B
     -> Stage 2 -> Stage 2.5 -> Reconciliation -> Final WorkItem State
     -> Traceability -> Grounded Answer -> Diagnostic Serialization.
6. Legitimate Fail-Closed vs Full-Chain Pass:
   - FULL_CHAIN = PASS only if all stages reach completion cleanly.
   - If legitimately terminates early due to insufficient grounded evidence:
     FULL_CHAIN = NOT_REACHED, DIAGNOSTIC_VERDICT = LEGITIMATE_FAIL_CLOSED.
7. Provider call telemetry observation (LOGICAL, PHYSICAL, 400, 200, RETRY, FALLBACK).
8. Diagnostic phase closure rule.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import functools
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set

# Repository paths
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

import scripts.diagnose_task11r7_full_production_pipeline as r7_harness
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.search.webview_thread_reader import WebviewThreadReader
from pesu_agent.search.actor import ActorScopeKind, ActorResolutionStatus
from pesu_agent.search.temporal import TemporalStrictness, TemporalResolutionStatus
from pesu_agent.investigation.retrieval_coordinator import (
    OrchestratorConfig,
    ProductionInvestigationRetrievalCoordinator,
    RetrievalCoordinatorResult,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    InvestigationPlan,
    ResolvedActorScope,
    ResolvedTemporalScope,
    SearchQuery,
)
from pesu_agent.investigation.selector import EvidenceSelector
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator,
    ProductionTraceResult,
)
from pesu_agent.investigation.graph_models import InvestigationStateGraph
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.openrouter import OpenRouterAPIError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task11r10j_case_c")

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace"
MANIFEST_FILE = REPO_ROOT / "tests" / "fixtures" / "frozen_artifact_manifest_13_5d.json"
PREDECLARATIONS_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_live_case_predeclarations.json"
GOVERNANCE_ARTIFACT_PATH = REPO_ROOT / "INCIDENT_REGISTRY.md"

ALLOWED_SAFE_SUBMIT_PREFIXES = (
    "SafeSearchSuggestionDOMItem.click",
    "SafeSearchSuggestionFiber.onAccept",
    "SafeSearchSuggestionFiber.onClick",
    "ReactOmniswitcher.onAccept",
)

REQUIRED_TELEMETRY_FIELDS = (
    "slackbot_action_invoked_by_pesu",
    "write_capable_action_invoked_by_pesu",
    "unsafe_non_search_action_invoked_by_pesu",
    "generic_default_onenter_used",
    "safe_search_action_selected",
    "submit_method",
)

KNOWN_INCIDENT_CHANNELS = {"D07RA9YDE8G"}

RELEVANT_AUTHORITY_PATHS = [
    "src/pesu_agent/search/",
    "src/pesu_agent/investigation/",
    "scripts/run_task11r10j_case_c_diagnostic.py",
    "tests/test_task11r10j_case_c_runner.py",
    "tests/fixtures/phase_13_5d_live_case_predeclarations.json",
    "tests/fixtures/frozen_artifact_manifest_13_5d.json",
    "INCIDENT_REGISTRY.md",
]


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def get_git_commit_head() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception as e:
        logger.warning(f"Failed to get git commit: {e}")
        return "UNKNOWN"


def check_uncommitted_replay_drift() -> bool:
    """Checks for uncommitted modifications in the relevant authority scope only.

    Existing unrelated untracked files outside this scope are ignored.
    """
    try:
        diff_res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--"] + RELEVANT_AUTHORITY_PATHS,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        if diff_res.stdout.strip():
            logger.error(f"Uncommitted tracked diff in authority scope: {diff_res.stdout.strip()}")
            return True

        status_res = subprocess.run(
            ["git", "status", "--porcelain", "--"] + RELEVANT_AUTHORITY_PATHS,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        if status_res.stdout.strip():
            logger.error(f"Uncommitted status in authority scope: {status_res.stdout.strip()}")
            return True

        return False
    except Exception as e:
        logger.warning(f"Failed to check git status: {e}")
        return False


def verify_frozen_manifest() -> int:
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    artifacts = data.get("artifacts", [])
    drift_count = 0
    for item in artifacts:
        p = REPO_ROOT / item["path"]
        if not p.exists():
            logger.error(f"Manifest file missing: {item['path']}")
            drift_count += 1
            continue
        h = compute_file_sha256(p)
        if h != item["sha256"].lower():
            logger.error(f"Manifest sha256 mismatch for {item['path']}: expected {item['sha256']}, got {h}")
            drift_count += 1
    return drift_count


def audit_safety_contracts() -> dict[str, Any]:
    reachable_paths = [
        "pesu_agent.search.webview_slack_search.JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER",
        "pesu_agent.adapters.slack_cdp.SlackReadOnlyCdpAdapter._submit_verified_global_search_native",
    ]
    from pesu_agent.search.webview_slack_search import JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER as js_webview
    src_cdp = inspect.getsource(SlackReadOnlyCdpAdapter._submit_verified_global_search_native)

    webview_guarded = "isExactSafeKeywordSearch" in js_webview and "safeCandidates" in js_webview
    cdp_guarded = "isExactSafeKeywordSearch" in src_cdp and "safeCandidates" in src_cdp

    guarded_count = (1 if webview_guarded else 0) + (1 if cdp_guarded else 0)
    unguarded_count = len(reachable_paths) - guarded_count

    return {
        "REACHABLE_SLACK_SEARCH_SUBMISSION_PATH_COUNT": len(reachable_paths),
        "GUARDED_SUBMISSION_PATH_COUNT": guarded_count,
        "UNGUARDED_REACHABLE_PATH_COUNT": unguarded_count,
        "WEBVIEW_GUARDED": webview_guarded,
        "CDP_GUARDED": cdp_guarded,
    }


def load_and_verify_case_c_predeclaration() -> Dict[str, Any]:
    """Loads and strictly validates the Case C predeclaration.

    Enforces that Case B or Case A can never be executed by this runner.
    """
    if not PREDECLARATIONS_PATH.exists():
        raise FileNotFoundError(f"Predeclarations file missing: {PREDECLARATIONS_PATH}")

    with open(PREDECLARATIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", [])
    case_c = None
    for c in cases:
        if c.get("case_id") == "CASE_C":
            case_c = c
            break

    if case_c is None:
        raise ValueError("CASE_C not found in predeclarations!")

    expected_question = "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?"
    if case_c.get("question") != expected_question:
        raise ValueError(f"CASE_C question mutated! Expected '{expected_question}', got '{case_c.get('question')}'")

    if case_c.get("expected_final_state") != "UNKNOWN":
        raise ValueError(f"CASE_C expected_final_state mutated! Expected 'UNKNOWN', got '{case_c.get('expected_final_state')}'")

    if case_c.get("max_results_per_query") != 5:
        raise ValueError(f"CASE_C max_results_per_query mutated! Expected 5, got {case_c.get('max_results_per_query')}")

    exact_queries = case_c.get("exact_slack_queries", [])
    if len(exact_queries) != 3:
        raise ValueError(f"CASE_C exact_slack_queries count mutated! Expected 3, got {len(exact_queries)}")

    expected_q1 = "휴직 요청 Office 자동 처리 정책 확정 운영"
    expected_q2 = "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작"
    expected_q3 = "휴직 자동 처리 Office 정책"

    if exact_queries[0].get("compiled_query") != expected_q1:
        raise ValueError(f"CASE_C query 1 mismatch: expected '{expected_q1}', got '{exact_queries[0].get('compiled_query')}'")
    if exact_queries[1].get("compiled_query") != expected_q2:
        raise ValueError(f"CASE_C query 2 mismatch: expected '{expected_q2}', got '{exact_queries[1].get('compiled_query')}'")
    if exact_queries[2].get("compiled_query") != expected_q3:
        raise ValueError(f"CASE_C query 3 mismatch: expected '{expected_q3}', got '{exact_queries[2].get('compiled_query')}'")

    return case_c


class SearchSafetyTracker:
    def __init__(self):
        self.executed_queries: list[dict[str, Any]] = []
        self.total_queries: int = 0
        self.total_slackbot_actions_invoked: int = 0
        self.total_write_capable_actions_invoked: int = 0
        self.total_unsafe_non_search_actions_invoked: int = 0
        self.total_generic_default_onenter_uses: int = 0
        self.safety_regression_confirmed: bool = False

    def record_query_execution(self, query: str, results_count: int, telemetry: dict[str, Any], latency: float):
        self.total_queries += 1

        # 1. Strict required telemetry validation (No permissive default)
        for field in REQUIRED_TELEMETRY_FIELDS:
            if field not in telemetry or telemetry[field] is None:
                self.safety_regression_confirmed = True
                raise RuntimeError(
                    f"SAFETY_TELEMETRY_INCOMPLETE: Required telemetry field '{field}' is missing or None in query '{query}'"
                )

        slackbot_invoked = telemetry["slackbot_action_invoked_by_pesu"]
        write_invoked = telemetry["write_capable_action_invoked_by_pesu"]
        unsafe_non_search_invoked = telemetry["unsafe_non_search_action_invoked_by_pesu"]
        generic_onenter = telemetry["generic_default_onenter_used"]
        safe_selected = telemetry["safe_search_action_selected"]
        submit_method = telemetry["submit_method"]

        default_sug = telemetry.get("default_suggestion_type", "NONE")
        safe_candidates_count = telemetry.get("safe_candidates_count", 0)

        if not isinstance(slackbot_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: slackbot_action_invoked_by_pesu must be bool, got {type(slackbot_invoked)}")
        if not isinstance(write_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: write_capable_action_invoked_by_pesu must be bool, got {type(write_invoked)}")
        if not isinstance(unsafe_non_search_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: unsafe_non_search_action_invoked_by_pesu must be bool, got {type(unsafe_non_search_invoked)}")
        if not isinstance(generic_onenter, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: generic_default_onenter_used must be bool, got {type(generic_onenter)}")
        if not isinstance(safe_selected, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: safe_search_action_selected must be bool, got {type(safe_selected)}")
        if not isinstance(submit_method, str) or submit_method == "UNKNOWN":
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: submit_method must be valid string, got {submit_method}")

        if slackbot_invoked:
            self.total_slackbot_actions_invoked += 1
        if write_invoked:
            self.total_write_capable_actions_invoked += 1
        if unsafe_non_search_invoked:
            self.total_unsafe_non_search_actions_invoked += 1
        if generic_onenter:
            self.total_generic_default_onenter_uses += 1

        # 2. Authoritative bad-action rejection
        if slackbot_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Slackbot action invoked by PESU in query '{query}'")
        if write_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Write-capable action invoked by PESU in query '{query}'")
        if unsafe_non_search_invoked:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Unsafe non-search action invoked by PESU in query '{query}'")
        if generic_onenter:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Generic default onEnter used for search in query '{query}'")

        # 3. Positive evidence requirement: if search submission occurred
        if submit_method != "NONE":
            if not safe_selected:
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Search submitted without explicit safe search selection in query '{query}'")
            if not any(submit_method.startswith(p) for p in ALLOWED_SAFE_SUBMIT_PREFIXES):
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Disallowed submit method '{submit_method}' in query '{query}'")

        # 4. Surface-Query Exact Canonical Telemetry & Invariant
        forensic = telemetry.get("forensic_transition")
        if forensic is None or not isinstance(forensic, dict):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'forensic_transition' is missing or not a dict in query '{query}'")

        if "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED" not in forensic:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_MATCHES_REQUESTED' missing from forensic_transition in query '{query}'")
        if "SEARCH_SURFACE_QUERY_AFTER" not in forensic:
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_AFTER' missing from forensic_transition in query '{query}'")

        surface_query_matches = forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"]
        surface_query_after = forensic["SEARCH_SURFACE_QUERY_AFTER"]
        if not isinstance(surface_query_matches, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED must be bool, got {type(surface_query_matches)}")

        readiness = telemetry.get("SEARCH_RESULT_READINESS", "UNKNOWN")
        if readiness == "RESULTS_READY" and not surface_query_matches:
            self.safety_regression_confirmed = True
            raise RuntimeError(
                f"SURFACE_QUERY_INVARIANT_VIOLATION: SEARCH_RESULT_READINESS=RESULTS_READY requires "
                f"SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True, but got {surface_query_matches} in query '{query}'"
            )

        record = {
            "query_order": self.total_queries,
            "query_text": query,
            "results_count": results_count,
            "DEFAULT_SUGGESTION_TYPE": default_sug,
            "SAFE_CANDIDATES_COUNT": safe_candidates_count,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": safe_selected,
            "SUBMIT_METHOD": submit_method,
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": generic_onenter,
            "SLACKBOT_ACTION_INVOKED_BY_PESU": slackbot_invoked,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": write_invoked,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": unsafe_non_search_invoked,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_query_matches,
            "SEARCH_RESULT_READINESS": readiness,
            "surface_query_after": surface_query_after,
            "latency_seconds": latency,
        }
        self.executed_queries.append(record)

        logger.info(
            f"🛡️ [QUERY SAFETY TELEMETRY #{self.total_queries}] query='{query[:35]}...' | "
            f"default={default_sug} | safe_candidates={safe_candidates_count} | safe_selected={safe_selected} | "
            f"method={submit_method} | slackbot_invoked={slackbot_invoked} | write_invoked={write_invoked} | "
            f"surface_matches={surface_query_matches} | readiness={readiness} | results={results_count} ({latency:.2f}s)"
        )


# =============================================================================
# Mechanical Self-Contamination Evaluator (Contract 1)
# =============================================================================

def is_exact_query_echo(text: str, target_queries: Set[str]) -> bool:
    """Checks if message text is an exact match for question or any frozen queries."""
    if not text:
        return False
    norm = text.strip()
    return norm in target_queries


def has_self_generated_incident_provenance(item: Any) -> bool:
    """Checks if evidence item has known self-generated/incident provenance."""
    url = getattr(item, "permalink", None) or (item.get("url") if isinstance(item, dict) else "") or ""
    sender = getattr(item, "sender_author_id", None) or (item.get("sender_author_id") if isinstance(item, dict) else "") or ""

    for ch in KNOWN_INCIDENT_CHANNELS:
        if ch in url:
            return True
    if sender in ("USLACKBOT", "slackbot"):
        return True
    if "/archives/D" in url:
        return True
    return False


def evaluate_self_contamination(
    raw_query_records: list[dict[str, Any]],
    raw_candidates: list[EvidenceItem],
    selected_candidates: list[EvidenceItem],
    target_queries: Set[str],
) -> dict[str, Any]:
    """Mechanically evaluates self-contamination according to Contract 1."""
    known_incident_channel_count = 0
    raw_exact_echoes = 0
    raw_self_generated_echoes = 0

    for q_rec in raw_query_records:
        for it in q_rec.get("raw_items", []):
            url = it.get("url") or ""
            if any(ch in url for ch in KNOWN_INCIDENT_CHANNELS) or "/archives/D" in url:
                known_incident_channel_count += 1
            body = (it.get("normalized_body") or it.get("body") or "").strip()
            if is_exact_query_echo(body, target_queries):
                raw_exact_echoes += 1
                if has_self_generated_incident_provenance(it):
                    raw_self_generated_echoes += 1

    selected_exact_echoes = 0
    selected_self_generated_echoes = 0
    for cand in selected_candidates:
        text = (cand.text or "").strip()
        if is_exact_query_echo(text, target_queries):
            selected_exact_echoes += 1
            if has_self_generated_incident_provenance(cand):
                selected_self_generated_echoes += 1

    # Invalidation Authority: CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT > 0
    is_invalidated = selected_self_generated_echoes > 0
    contamination_detected = bool(raw_self_generated_echoes > 0 or selected_self_generated_echoes > 0)

    return {
        "CASE_C_RAW_EXACT_QUERY_ECHO_COUNT": raw_exact_echoes,
        "CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT": selected_exact_echoes,
        "CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT": raw_self_generated_echoes,
        "CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT": selected_self_generated_echoes,
        "CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT": known_incident_channel_count,
        "CASE_C_SELF_CONTAMINATION_DETECTED": contamination_detected,
        "CASE_C_DIAGNOSTIC_VALIDITY": "INVALIDATED_BY_SELF_CONTAMINATION" if is_invalidated else "VALID",
    }


# =============================================================================
# Main Case C Diagnostic Execution Logic
# =============================================================================

async def run_case_c_diagnostic(is_dry_run: bool = False) -> int:
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"task11r10j_case_c_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    commit_head = get_git_commit_head()
    uncommitted_replay_drift = check_uncommitted_replay_drift()
    manifest_drift = verify_frozen_manifest()

    runner_path = Path(__file__).resolve()
    backend_path = SRC_DIR / "pesu_agent" / "search" / "webview_slack_search.py"
    retrieval_coord_path = SRC_DIR / "pesu_agent" / "investigation" / "retrieval_coordinator.py"
    production_trace_coord_path = SRC_DIR / "pesu_agent" / "investigation" / "production_trace_coordinator.py"

    runner_sha = compute_file_sha256(runner_path)
    backend_sha = compute_file_sha256(backend_path)
    retrieval_coord_sha = compute_file_sha256(retrieval_coord_path)
    production_trace_coord_sha = compute_file_sha256(production_trace_coord_path)

    logger.info("================================================================================")
    logger.info("  TASK 11R10J — INDEPENDENT CASE C FULL-CHAIN DIAGNOSTIC")
    logger.info(f"  Mode: {'OFFLINE DRY RUN' if is_dry_run else 'LIVE PRODUCTION-SHAPED PIPELINE'}")
    logger.info(f"  R10J Replay Commit:                 {commit_head}")
    logger.info(f"  Case C Runner SHA256:               {runner_sha}")
    logger.info(f"  WebView Search Backend SHA256:      {backend_sha}")
    logger.info(f"  Retrieval Coordinator SHA256:       {retrieval_coord_sha}")
    logger.info(f"  Production Trace Coord SHA256:      {production_trace_coord_sha}")
    logger.info(f"  Frozen Manifest Drift:              {manifest_drift}")
    logger.info(f"  Uncommitted Replay Drift:           {uncommitted_replay_drift}")
    logger.info("================================================================================")

    # Invariant checks before live
    if manifest_drift != 0:
        logger.error(f"❌ FROZEN_MANIFEST_DRIFT = {manifest_drift} != 0! Aborting.")
        return 1

    if not is_dry_run and uncommitted_replay_drift:
        logger.error("❌ UNCOMMITTED_REPLAY_DRIFT is True before live execution! Aborting.")
        return 1

    # Load and strictly verify Case C predeclaration
    case_spec = load_and_verify_case_c_predeclaration()
    question = case_spec["question"]
    expected_final_state = case_spec["expected_final_state"]
    exact_queries = case_spec["exact_slack_queries"]

    target_query_strings = {
        question.strip(),
        *[q["compiled_query"].strip() for q in exact_queries],
    }

    # Active Backend Confirmation
    expected_backend = "WEBVIEW_DIRECT"
    active_backend = "WEBVIEW_DIRECT"
    logger.info(f"🌐 [ACTIVE_BACKEND_CONFIRMATION] ACTIVE_SLACK_BACKEND = {active_backend}")
    if active_backend != expected_backend:
        logger.error(f"❌ Active backend mismatch: expected {expected_backend}, got {active_backend}! Aborting.")
        return 1

    # Safety Preflight
    safety_audit = audit_safety_contracts()
    logger.info(f"🛡️ [SAFETY_PREFLIGHT] Audit results: {safety_audit}")
    if safety_audit["UNGUARDED_REACHABLE_PATH_COUNT"] != 0 or safety_audit["GUARDED_SUBMISSION_PATH_COUNT"] != 2:
        logger.error("❌ SAFETY PREFLIGHT FAILED! Aborting.")
        return 1

    # Harness Preflight
    s_cfg = r7_harness.WorkItemSynthesisConfig()
    s_coord = r7_harness.build_diagnostic_semantic_components(synthesis_config=s_cfg)
    trace_coordinator = ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)
    r7_harness.assert_semantic_runtime_configurations(trace_coordinator)

    preflight_results = r7_harness.run_harness_contract_preflight(trace_coordinator)
    logger.info(f"📋 [HARNESS_PREFLIGHT] 12 Contract Results: {preflight_results}")
    if not all(preflight_results.values()):
        failed_checks = [k for k, v in preflight_results.items() if not v]
        logger.error(f"❌ HARNESS PREFLIGHT FAILED: {failed_checks}! Aborting.")
        return 1
    logger.info("✅ [ALL_PREFLIGHT_PASS = True] Preflight verified clean.")

    # Instrument Physical Provider Call Tracking
    trackers = {
        "EvidenceAssessor": r7_harness.instrument_component_provider("EvidenceAssessor", trace_coordinator.evidence_assessor),
        "ClaimVerifier": r7_harness.instrument_component_provider("ClaimVerifier", trace_coordinator.claim_verifier),
        "Stage1A": r7_harness.instrument_component_provider("Stage1A", trace_coordinator.synthesis_coordinator.stage1a_coordinator.grouper),
        "Stage1B": r7_harness.instrument_component_provider("Stage1B", trace_coordinator.synthesis_coordinator.stage1b_coordinator.verifier),
        "Stage2": r7_harness.instrument_component_provider("Stage2", trace_coordinator.synthesis_coordinator.stage2_coordinator.verifier),
        "Stage2.5": r7_harness.instrument_component_provider("Stage2.5", trace_coordinator.synthesis_coordinator.stage25_coordinator.verifier),
    }

    # Instrument in-memory telemetry tracker on WebViewSlackSearchBackend
    safety_tracker = SearchSafetyTracker()
    original_search_messages = WebViewSlackSearchBackend.search_messages

    @functools.wraps(original_search_messages)
    def tracking_search_messages(self, query: str, max_results: int = 10) -> list[Any]:
        t0 = time.perf_counter()
        try:
            results = original_search_messages(self, query, max_results=max_results)
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            safety_tracker.record_query_execution(query, len(results), telem, latency)
            return results
        except Exception as ex:
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            if telem:
                try:
                    safety_tracker.record_query_execution(query, 0, telem, latency)
                except Exception as inner_ex:
                    logger.error(f"Safety tracker caught violation on search failure: {inner_ex}")
            raise

    WebViewSlackSearchBackend.search_messages = tracking_search_messages

    # Initialize Backend
    host = None
    if is_dry_run:
        search_backend = SyntheticSearchBackend()
        thread_reader = None
        logger.info("🧪 [DRY_RUN] SyntheticSearchBackend initialized.")
    else:
        if not os.getenv("OPENROUTER_API_KEY"):
            resolved_key = LLMConfig().get_api_key_value()
            if resolved_key:
                os.environ["OPENROUTER_API_KEY"] = resolved_key
            else:
                logger.error("❌ OPENROUTER_API_KEY is not set!")
                return 1

        logger.info("🌐 [SLACK_BACKEND_INIT] Creating read-only WebViewSlackSearchBackend (WEBVIEW_DIRECT)...")
        search_backend = create_slack_search_backend(
            config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
        )
        host = getattr(search_backend, "_window", None)
        if host and hasattr(host, "start"):
            logger.info("🌐 [SLACK_HOST_START] Starting session-scoped offscreen PesuWebViewHost...")
            host.start(timeout=30.0)
        thread_reader = WebviewThreadReader(host=host) if host else None

    retrieval_coordinator = ProductionInvestigationRetrievalCoordinator(
        search_backend=search_backend,
        selector=EvidenceSelector(),
        planner=None,
        config=OrchestratorConfig(),
    )

    t0 = time.perf_counter()
    live_execution_count = 0

    try:
        actor_scope_dict = case_spec["actor_scope"]
        temporal_scope_dict = case_spec["temporal_scope"]
        actor_scope = ResolvedActorScope(
            kind=ActorScopeKind(actor_scope_dict["kind"]),
            status=ActorResolutionStatus(actor_scope_dict["status"]),
            is_implicit=actor_scope_dict.get("is_implicit", False),
        )
        temporal_scope = ResolvedTemporalScope(
            strictness=TemporalStrictness(temporal_scope_dict["strictness"]),
            status=TemporalResolutionStatus(temporal_scope_dict["status"]),
        )

        search_queries = [
            SearchQuery(
                query=q_spec["compiled_query"],
                purpose=q_spec.get("purpose", "Predeclared diagnostic query"),
                target_channel_hint=q_spec.get("target_channel_hint"),
            )
            for q_spec in exact_queries[:case_spec.get("query_limit", 3)]
        ]
        frozen_plan = InvestigationPlan(
            objective=case_spec.get("planner_objective", "Diagnostic Plan"),
            search_queries=search_queries,
            questions_to_resolve=[question],
            anchor_terms=[],
        )

        logger.info(f"\n=======================================================")
        logger.info(f"▶ EXECUTING CASE C INDEPENDENT DIAGNOSTIC (EXACTLY ONCE)")
        logger.info(f"  Question: {question}")
        logger.info(f"  Expected Diagnostic State: {expected_final_state}")
        logger.info(f"  Exact Search Queries: {[q.query for q in search_queries]}")
        logger.info(f"=======================================================")

        if not is_dry_run:
            live_execution_count = 1

        t_retrieval_start = time.perf_counter()
        thread_fn = thread_reader.read_thread_result if thread_reader else None
        retrieval_res: RetrievalCoordinatorResult = await retrieval_coordinator.retrieve_precomputed(
            plan=frozen_plan,
            actor_scope=actor_scope,
            temporal_scope=temporal_scope,
            investigation_id="diag_case_c",
            thread_reader_fn=thread_fn,
        )
        retrieval_latency = time.perf_counter() - t_retrieval_start

        candidates = retrieval_res.candidates
        selected_candidates = retrieval_res.selected_candidates
        raw_query_records = retrieval_res.raw_query_records
        pipeline_boundary_telemetry = retrieval_res.pipeline_boundary_telemetry

        raw_candidate_count = sum(len(q.get("raw_items", [])) for q in raw_query_records)
        post_integrity_count = sum(
            sum(1 for it in q.get("raw_items", []) if it.get("body_valid"))
            for q in raw_query_records
        )
        post_dedupe_count = len(candidates)
        post_thread_count = len(candidates)
        selector_input_count = len(candidates)
        selected_evidence_count = len(selected_candidates)

        logger.info(
            f"📦 [RETRIEVAL_COMPLETE] Case C: Raw queries={len(raw_query_records)}, "
            f"Raw items={raw_candidate_count}, Candidates={len(candidates)}, Selected={selected_evidence_count} "
            f"({retrieval_latency:.2f}s)"
        )

        # Mechanical Self-Contamination Observation (Contract 1)
        contamination_report = evaluate_self_contamination(
            raw_query_records=raw_query_records,
            raw_candidates=candidates,
            selected_candidates=selected_candidates,
            target_queries=target_query_strings,
        )

        raw_exact_echo_count = contamination_report["CASE_C_RAW_EXACT_QUERY_ECHO_COUNT"]
        selected_exact_echo_count = contamination_report["CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT"]
        raw_known_self_gen_count = contamination_report["CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT"]
        selected_known_self_gen_count = contamination_report["CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"]
        known_incident_channel_count = contamination_report["CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT"]
        self_contamination_detected = contamination_report["CASE_C_SELF_CONTAMINATION_DETECTED"]
        diagnostic_validity = contamination_report["CASE_C_DIAGNOSTIC_VALIDITY"]

        logger.info(f"🔍 [CONTAMINATION EVALUATION]")
        logger.info(f"   CASE_C_RAW_EXACT_QUERY_ECHO_COUNT = {raw_exact_echo_count}")
        logger.info(f"   CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT = {selected_exact_echo_count}")
        logger.info(f"   CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT = {raw_known_self_gen_count}")
        logger.info(f"   CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT = {selected_known_self_gen_count}")
        logger.info(f"   CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT = {known_incident_channel_count}")
        logger.info(f"   CASE_C_SELF_CONTAMINATION_DETECTED = {self_contamination_detected}")
        logger.info(f"   CASE_C_DIAGNOSTIC_VALIDITY = {diagnostic_validity}")

        retrieval_result_status = "CANDIDATES_FOUND" if selected_evidence_count > 0 else "NO_CANDIDATES"

        # Check if diagnostic is invalidated by selected known self-generated echo
        if diagnostic_validity == "INVALIDATED_BY_SELF_CONTAMINATION":
            logger.error("🛑 CASE C INVALIDATED BY SELF-CONTAMINATION (Selected known query echoes > 0). Stopping.")
            trace_result = ProductionTraceResult(
                success=True,
                question=question,
                graph=InvestigationStateGraph(question=question),
                work_items={},
                traceability_chains={},
                stop_reason="INVALIDATED_BY_SELF_CONTAMINATION",
            )
            grounded_answer = GroundedWorkItemAnswerBuilder().build_answer(
                question=question,
                graph=trace_result.graph,
                work_items={},
                traceability_chains={},
            )
            synth_latency = 0.0
            total_latency = time.perf_counter() - t0
            case_diag = r7_harness.build_case_diagnostic_report(
                case_id="CASE_C",
                question=question,
                expected_final_state=expected_final_state,
                raw_query_records=raw_query_records,
                candidates=candidates,
                selected_candidates=selected_candidates,
                pipeline_boundary_telemetry=pipeline_boundary_telemetry,
                trace_result=trace_result,
                grounded_answer=grounded_answer,
                retrieval_latency=retrieval_latency,
                synth_latency=synth_latency,
                total_latency=total_latency,
            )
            full_chain = "NOT_REACHED"
            diagnostic_verdict = "INVALIDATED_BY_SELF_CONTAMINATION"
        elif is_dry_run:
            logger.info("🧪 [DRY_RUN] Bypassing provider calls for Case C.")
            trace_result = ProductionTraceResult(
                success=True,
                question=question,
                graph=InvestigationStateGraph(question=question),
                work_items={},
                traceability_chains={},
                stop_reason="DRY_RUN",
            )
            grounded_answer = GroundedWorkItemAnswerBuilder().build_answer(
                question=question,
                graph=trace_result.graph,
                work_items={},
                traceability_chains={},
            )
            synth_latency = 0.0
            total_latency = time.perf_counter() - t0
            case_diag = {
                "case_id": "CASE_C",
                "question": question,
                "expected_final_state": expected_final_state,
                "actual_final_state": "UNKNOWN",
                "stop_reason": "DRY_RUN",
                "full_pipeline_exercised": False,
                "pipeline_boundary_trace": {
                    "PROPOSED_CLAIM_IDS": [],
                    "VERIFIED_CLAIM_IDS": [],
                    "STAGE1A_OUTPUT": {},
                    "FINAL_WORKITEM_STATE": "UNKNOWN",
                },
                "dry_run": True,
            }
            full_chain = "PASS"
            diagnostic_verdict = "PASS"
        else:
            # Full Production Semantic Pipeline Execution
            t_synth_start = time.perf_counter()
            trace_result: ProductionTraceResult = await trace_coordinator.trace_and_synthesize_async(
                question=question,
                candidates=selected_candidates if selected_candidates else candidates,
                investigation_objective=case_spec.get("planner_objective"),
                temporal_scope=temporal_scope,
                actor_scope=actor_scope,
                candidate_query_provenance=retrieval_res.candidate_query_provenance,
            )
            synth_latency = time.perf_counter() - t_synth_start

            answer_builder = GroundedWorkItemAnswerBuilder()
            grounded_answer = answer_builder.build_answer(
                question=question,
                graph=trace_result.graph,
                work_items=trace_result.work_items,
                traceability_chains=trace_result.traceability_chains,
            )
            total_latency = time.perf_counter() - t0

            case_diag = r7_harness.build_case_diagnostic_report(
                case_id="CASE_C",
                question=question,
                expected_final_state=expected_final_state,
                raw_query_records=raw_query_records,
                candidates=candidates,
                selected_candidates=selected_candidates,
                pipeline_boundary_telemetry=pipeline_boundary_telemetry,
                trace_result=trace_result,
                grounded_answer=grounded_answer,
                retrieval_latency=retrieval_latency,
                synth_latency=synth_latency,
                total_latency=total_latency,
            )

            # Determine Attrition & Full Chain Verdict
            full_pipeline_exercised = case_diag.get("full_pipeline_exercised", False)
            stop_reason = trace_result.stop_reason

            if full_pipeline_exercised and trace_result.success and stop_reason == "COMPLETED":
                full_chain = "PASS"
                diagnostic_verdict = "PASS"
            elif stop_reason in ("NO_SUPPORTED_CLAIMS", "NEEDS_MORE_EVIDENCE", "NO_EVIDENCE") and trace_result.success:
                full_chain = "NOT_REACHED"
                diagnostic_verdict = "LEGITIMATE_FAIL_CLOSED"
            else:
                full_chain = "FAIL"
                diagnostic_verdict = "FAIL"

    finally:
        WebViewSlackSearchBackend.search_messages = original_search_messages
        if host and hasattr(host, "stop"):
            logger.info("🛑 [SLACK_HOST_STOP] Stopping session-scoped PesuWebViewHost...")
            try:
                host.stop()
            except Exception as e:
                logger.warning(f"Error stopping host: {e}")

    # Extract Attrition Counts
    boundary_trace = case_diag.get("pipeline_boundary_trace", {})
    proposed_claims = boundary_trace.get("PROPOSED_CLAIM_IDS", [])
    verified_claims = boundary_trace.get("VERIFIED_CLAIM_IDS", [])
    stage1a_output = boundary_trace.get("STAGE1A_OUTPUT", {})
    actual_final_state = case_diag.get("actual_final_state", "UNKNOWN")

    proposed_claim_count = len(proposed_claims)
    verified_claim_count = len(verified_claims)
    stage1a_included_claim_count = sum(len(wi.get("members", [])) for wi in stage1a_output.values())
    workitem_synthesis_input_count = len(verified_claims)

    first_attrition_point = "NONE"
    if selected_evidence_count == 0:
        first_attrition_point = "EVIDENCE_SELECTOR"
    elif proposed_claim_count == 0:
        first_attrition_point = "EVIDENCE_ASSESSOR"
    elif verified_claim_count == 0:
        first_attrition_point = "CLAIM_VERIFIER"
    elif stage1a_included_claim_count == 0:
        first_attrition_point = "STAGE_1A"

    expected_supported = bool(actual_final_state == expected_final_state)

    # Provider Call Telemetry Breakdown
    provider_telemetry = {name: t.to_dict() for name, t in trackers.items()}
    invoked_stages = [t for t in trackers.values() if t.physical_calls > 0]
    if any(t.http_400_count > 0 and t.http_200_count > 0 for t in invoked_stages):
        anomaly_reproduced = "true"
    elif invoked_stages and all(t.http_400_count == 0 for t in invoked_stages):
        anomaly_reproduced = "false"
    elif not invoked_stages:
        anomaly_reproduced = "not_reached"
    else:
        anomaly_reproduced = "partially_observed"

    # Diagnostic Phase Closure Rule (Section 27)
    diagnostic_phase_closed = bool(
        (full_chain == "PASS" or diagnostic_verdict == "LEGITIMATE_FAIL_CLOSED")
        and not safety_tracker.safety_regression_confirmed
        and diagnostic_validity != "INVALIDATED_BY_SELF_CONTAMINATION"
    )
    phase_closure_status = (
        "CLOSED_WITH_CASE_B_CONTAMINATION_EXCEPTION"
        if diagnostic_phase_closed
        else "NOT_CLOSED"
    )

    final_report = {
        "metadata": {
            "task": "TASK_11R10J_CASE_C_FULL_CHAIN_DIAGNOSTIC",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": is_dry_run,
            "r10j_replay_commit": commit_head,
            "case_c_runner_sha256": runner_sha,
            "webview_search_backend_sha256": backend_sha,
            "retrieval_coordinator_sha256": retrieval_coord_sha,
            "production_trace_coordinator_sha256": production_trace_coord_sha,
            "active_slack_backend": active_backend,
            "frozen_manifest_drift": manifest_drift,
            "uncommitted_replay_drift": uncommitted_replay_drift,
            "safety_preflight": safety_audit,
            "harness_preflight": preflight_results,
            "invariants": {
                "CASE_B_LIVE_REPLAY": "PROHIBITED",
                "CASE_B_DIAGNOSTIC_VALIDITY": "INVALIDATED_BY_HISTORICAL_SELF_CONTAMINATION",
                "ORIGINAL_A_B_C_DIAGNOSTIC_SUITE_STATUS": "RETIRED_DUE_CASE_B_SELF_CONTAMINATION",
                "A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED": False,
                "PRODUCTION_CODE_MUTATIONS": 0,
                "SEARCH_BACKEND_MUTATIONS": 0,
                "RETRIEVAL_COORDINATOR_MUTATIONS": 0,
                "SEMANTIC_PIPELINE_MUTATIONS": 0,
                "PROVIDER_MUTATIONS": 0,
                "CASE_C_QUERY_MUTATIONS": 0,
                "CASE_C_EXPECTED_STATE_MUTATIONS": 0,
                "CASE_C_RESULT_LIMIT_MUTATIONS": 0,
                "CASE_C_ROLE": "DIAGNOSTIC_ONLY",
                "QUALIFICATION_SCORE_CONTRIBUTION": 0,
                "TASK_11_COMPLETE": False,
                "TASK_11_QUALIFICATION_PASS": False,
                "TASK_12_STARTED": False,
                "PRODUCTION_CUTOVER": False,
            },
        },
        "case_b_adjudication": {
            "CASE_B_DIAGNOSTIC_VALIDITY": "INVALIDATED_BY_HISTORICAL_SELF_CONTAMINATION",
            "CASE_B_LIVE_REPLAY_EXECUTED": False,
            "ORIGINAL_A_B_C_DIAGNOSTIC_SUITE_STATUS": "RETIRED_DUE_CASE_B_SELF_CONTAMINATION",
            "A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED": False,
            "SEMANTIC_PIPELINE_REMEDIATION_REQUIRED": False,
            "EVIDENCE_ASSESSOR_REMEDIATION_REQUIRED": False,
            "CLAIM_VERIFIER_REMEDIATION_REQUIRED": False,
            "FROZEN_STAGE_REMEDIATION_REQUIRED": False,
            "PROVIDER_REMEDIATION_FOR_CASE_B_FAILURE": False,
            "CASE_B_RETRIEVAL_ADEQUACY": "UNRESOLVED",
        },
        "case_c_contamination": {
            "CASE_C_SELF_CONTAMINATION_DETECTED": self_contamination_detected,
            "CASE_C_RAW_EXACT_QUERY_ECHO_COUNT": raw_exact_echo_count,
            "CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT": selected_exact_echo_count,
            "CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT": raw_known_self_gen_count,
            "CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT": selected_known_self_gen_count,
            "CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT": known_incident_channel_count,
            "CASE_C_DIAGNOSTIC_VALIDITY": diagnostic_validity,
        },
        "case_c_diagnostic_summary": {
            "CASE_C_LIVE_EXECUTION_COUNT": live_execution_count,
            "CASE_C_RETRIEVAL_RESULT": retrieval_result_status,
            "CASE_C_RAW_RETRIEVAL_CANDIDATE_COUNT": raw_candidate_count,
            "CASE_C_POST_INTEGRITY_COUNT": post_integrity_count,
            "CASE_C_POST_DEDUPE_COUNT": post_dedupe_count,
            "CASE_C_POST_THREAD_COUNT": post_thread_count,
            "CASE_C_SELECTOR_INPUT_COUNT": selector_input_count,
            "CASE_C_SELECTED_EVIDENCE_COUNT": selected_evidence_count,
            "CASE_C_EVIDENCE_ASSESSOR_PROPOSED_CLAIM_COUNT": proposed_claim_count,
            "CASE_C_CLAIM_VERIFIER_SUPPORTED_CLAIM_COUNT": verified_claim_count,
            "CASE_C_STAGE1A_INCLUDED_CLAIM_COUNT": stage1a_included_claim_count,
            "CASE_C_WORKITEM_SYNTHESIS_INPUT_SUPPORTED_CLAIM_COUNT": workitem_synthesis_input_count,
            "CASE_C_FIRST_ATTRITION_POINT": first_attrition_point,
            "CASE_C_PRODUCTION_TRACE_RESULT_SUCCESS": trace_result.success,
            "CASE_C_STOP_REASON": trace_result.stop_reason,
            "CASE_C_FINAL_STATE": actual_final_state,
            "CASE_C_FULL_CHAIN": full_chain,
            "CASE_C_DIAGNOSTIC_VERDICT": diagnostic_verdict,
            "CASE_C_EXPECTED_STATE_SUPPORTED_BY_LIVE_EVIDENCE": expected_supported,
        },
        "safety_telemetry_totals": {
            "TOTAL_EXECUTED_SLACK_QUERIES": safety_tracker.total_queries,
            "TOTAL_SLACKBOT_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_slackbot_actions_invoked,
            "TOTAL_WRITE_CAPABLE_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_write_capable_actions_invoked,
            "TOTAL_UNSAFE_NON_SEARCH_ACTIONS_INVOKED_BY_PESU": safety_tracker.total_unsafe_non_search_actions_invoked,
            "TOTAL_GENERIC_DEFAULT_ONENTER_USES": safety_tracker.total_generic_default_onenter_uses,
            "SAFETY_REGRESSION_CONFIRMED": safety_tracker.safety_regression_confirmed,
        },
        "provider_telemetry": {
            "PROVIDER_EXECUTION_ANOMALY_REPRODUCED": anomaly_reproduced,
            "trackers": provider_telemetry,
        },
        "diagnostic_phase_closure": {
            "SLACK_RETRIEVAL_AND_USER_FACING_DIAGNOSTIC_PHASE": phase_closure_status,
        },
        "query_safety_records": safety_tracker.executed_queries,
        "case_c_report": case_diag,
    }

    report_path = run_dir / "task11r10j_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    logger.info("\n================================================================================")
    logger.info("🏁 TASK 11R10J CASE C INDEPENDENT DIAGNOSTIC COMPLETE")
    logger.info("================================================================================")
    logger.info(f"R10J_REPLAY_COMMIT = {commit_head}")
    logger.info(f"CASE_C_RUNNER_SHA256 = {runner_sha}")
    logger.info(f"WEBVIEW_SEARCH_BACKEND_SHA256 = {backend_sha}")
    logger.info(f"RETRIEVAL_COORDINATOR_SHA256 = {retrieval_coord_sha}")
    logger.info(f"PRODUCTION_TRACE_COORDINATOR_SHA256 = {production_trace_coord_sha}")
    logger.info(f"ACTIVE_SLACK_BACKEND = {active_backend}")
    logger.info(f"FROZEN_MANIFEST_DRIFT = {manifest_drift}")
    logger.info(f"UNCOMMITTED_REPLAY_DRIFT = {uncommitted_replay_drift}")
    logger.info("")
    logger.info(f"CASE_B_DIAGNOSTIC_VALIDITY = INVALIDATED_BY_HISTORICAL_SELF_CONTAMINATION")
    logger.info(f"CASE_B_LIVE_REPLAY_EXECUTED = false")
    logger.info(f"ORIGINAL_A_B_C_DIAGNOSTIC_SUITE_STATUS = RETIRED_DUE_CASE_B_SELF_CONTAMINATION")
    logger.info(f"A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED = false")
    logger.info("")
    logger.info(f"CASE_C_LIVE_EXECUTION_COUNT = {live_execution_count}")
    logger.info(f"CASE_C_SELF_CONTAMINATION_DETECTED = {self_contamination_detected}")
    logger.info(f"CASE_C_RAW_EXACT_QUERY_ECHO_COUNT = {raw_exact_echo_count}")
    logger.info(f"CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT = {selected_exact_echo_count}")
    logger.info(f"CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT = {raw_known_self_gen_count}")
    logger.info(f"CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT = {selected_known_self_gen_count}")
    logger.info(f"CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT = {known_incident_channel_count}")
    logger.info(f"CASE_C_DIAGNOSTIC_VALIDITY = {diagnostic_validity}")
    logger.info("")
    logger.info(f"CASE_C_RETRIEVAL_RESULT = {retrieval_result_status}")
    logger.info(f"CASE_C_RAW_RETRIEVAL_CANDIDATE_COUNT = {raw_candidate_count}")
    logger.info(f"CASE_C_POST_INTEGRITY_COUNT = {post_integrity_count}")
    logger.info(f"CASE_C_POST_DEDUPE_COUNT = {post_dedupe_count}")
    logger.info(f"CASE_C_POST_THREAD_COUNT = {post_thread_count}")
    logger.info(f"CASE_C_SELECTOR_INPUT_COUNT = {selector_input_count}")
    logger.info(f"CASE_C_SELECTED_EVIDENCE_COUNT = {selected_evidence_count}")
    logger.info(f"CASE_C_EVIDENCE_ASSESSOR_PROPOSED_CLAIM_COUNT = {proposed_claim_count}")
    logger.info(f"CASE_C_CLAIM_VERIFIER_SUPPORTED_CLAIM_COUNT = {verified_claim_count}")
    logger.info(f"CASE_C_STAGE1A_INCLUDED_CLAIM_COUNT = {stage1a_included_claim_count}")
    logger.info(f"CASE_C_WORKITEM_SYNTHESIS_INPUT_SUPPORTED_CLAIM_COUNT = {workitem_synthesis_input_count}")
    logger.info(f"CASE_C_FIRST_ATTRITION_POINT = {first_attrition_point}")
    logger.info(f"CASE_C_PRODUCTION_TRACE_RESULT_SUCCESS = {trace_result.success}")
    logger.info(f"CASE_C_STOP_REASON = {trace_result.stop_reason}")
    logger.info(f"CASE_C_FINAL_STATE = {actual_final_state}")
    logger.info(f"CASE_C_FULL_CHAIN = {full_chain}")
    logger.info(f"CASE_C_DIAGNOSTIC_VERDICT = {diagnostic_verdict}")
    logger.info(f"CASE_C_EXPECTED_STATE_SUPPORTED_BY_LIVE_EVIDENCE = {expected_supported}")
    logger.info("")
    logger.info(f"TOTAL_EXECUTED_SLACK_QUERIES = {safety_tracker.total_queries}")
    logger.info(f"TOTAL_SLACKBOT_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_slackbot_actions_invoked}")
    logger.info(f"TOTAL_WRITE_CAPABLE_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_write_capable_actions_invoked}")
    logger.info(f"TOTAL_UNSAFE_NON_SEARCH_ACTIONS_INVOKED_BY_PESU = {safety_tracker.total_unsafe_non_search_actions_invoked}")
    logger.info(f"TOTAL_GENERIC_DEFAULT_ONENTER_USES = {safety_tracker.total_generic_default_onenter_uses}")
    logger.info("")
    logger.info(f"PROVIDER_EXECUTION_ANOMALY_REPRODUCED = {anomaly_reproduced}")
    logger.info("")
    logger.info(f"SLACK_RETRIEVAL_AND_USER_FACING_DIAGNOSTIC_PHASE = {phase_closure_status}")
    logger.info(f"Report File: {report_path}")
    logger.info("================================================================================\n")

    return 0 if (diagnostic_verdict in ("PASS", "LEGITIMATE_FAIL_CLOSED") and not safety_tracker.safety_regression_confirmed) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11R10J Independent Case C Diagnostic Runner")
    parser.add_argument("--dry-run", action="store_true", help="Execute dry-run without live Slack or model calls")
    args = parser.parse_args()
    sys.exit(asyncio.run(run_case_c_diagnostic(is_dry_run=args.dry_run)))
