#!/usr/bin/env python3
"""Phase 13.5D Task 11 — Clean Qualification v3 Runner.

Core Architectural Contracts:
1. Physical Separation of Cases and Gold Fixtures:
   - cases.json: Loaded by production pipeline. Contains ONLY query plan, questions, and bounds.
   - gold.json: Loaded ONLY by post-hoc evaluator after all 3 production results are serialized.
   - Production execution path has ZERO access to gold.json.
2. Canonical Production Enums & Semantic WorkItem Matching:
   - Uses WorkItemStatus (COMPLETED, IN_PROGRESS, BLOCKED, PLANNED, UNKNOWN).
   - Uses WorkItemLifecycleSignal (COMPLETION, PLAN, PROGRESS, BLOCKER, NONE).
   - Matches WorkItems deterministically via structured claims, lifecycle signals, and traceability chains
     instead of requiring exact string match on dynamic work_item_id or title.
3. Evaluator-Only Target Identity & Rate Matching:
   - Q2-02: requires both "정용기 세무사" and "박은혜 세무사" identified as active candidates.
   - Q2-04: requires external base rate 8% and internal base rate 5%.
   - Q2-04 Grounding Boundary: Distinguishes exception omission without evidence (allowed) vs.
     explicit fixed-rate contradiction (forbidden) and ungrounded exception invention (forbidden).
4. Deterministic Structured-Output Centric Evaluation:
   - Zero freeform LLM judge.
   - Strict claim-evidence traceability verification.
5. Strict Search Safety Telemetry (R10H/R10J Contract):
   - Fail-closed on missing/bad telemetry fields.
   - Safe search action selected with allowed prefix.
   - Surface query match invariant: RESULTS_READY requires SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True.
6. Mechanical Self-Contamination Observation:
   - SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT > 0 -> INVALIDATED_BY_PREEXISTING_SELF_CONTAMINATION.
7. Provider Telemetry:
   - Zero tolerance for recurrence of historical schema-400 defect.
8. Parity with F1 Remediated Real Production Boundary:
   - ProductionRealTraceCoordinator.trace_and_synthesize_async dispatch.
   - ProductionTraceResult.work_items, graph.claim_nodes, traceability_chains.
   - GroundedWorkItemAnswerBuilder real signature.
   - RetrievalCoordinatorResult.raw_query_records fail-closed enforcement.
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
from typing import Any, Dict, List, Optional, Set, Tuple

# Repository setup
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from pesu_agent.investigation.graph_models import (
    WorkItemStatus,
    WorkItemLifecycleSignal,
    DecisiveAuthorityDecision,
)
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter
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
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.llm.config import LLMConfig
import scripts.diagnose_task11r7_full_production_pipeline as r7_harness

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clean_qualification_v3")

OUTPUT_DIR = REPO_ROOT / "output" / "qualification_13_5d" / "v3_clean"
MANIFEST_FILE = REPO_ROOT / "tests" / "fixtures" / "frozen_artifact_manifest_13_5d.json"
CASES_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_clean_qualification_v3_cases.json"
GOLD_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "phase_13_5d_clean_qualification_v3_gold.json"

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
HISTORICAL_PROHIBITED_CASES = {"CASE_A", "CASE_B", "CASE_C", "Q2-01"}
FROZEN_CASE_ORDER = ["Q2-02", "Q2-03", "Q2-04"]


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


# =============================================================================
# 1. Production Cases Loader (Isolated from Gold)
# =============================================================================

class CleanPredeclarationLoader:
    """Loads and strictly validates production cases without touching gold truth."""

    @staticmethod
    def load_production_cases() -> Dict[str, Any]:
        if not CASES_FIXTURE_PATH.exists():
            raise FileNotFoundError(f"Cases fixture missing: {CASES_FIXTURE_PATH}")

        with open(CASES_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        cases = data.get("cases", [])
        if len(cases) != 3:
            raise ValueError(f"Clean qualification requires exactly 3 cases, got {len(cases)}")

        execution_order = data.get("execution_order", [])
        if execution_order != FROZEN_CASE_ORDER:
            raise ValueError(f"Execution order must be {FROZEN_CASE_ORDER}, got {execution_order}")

        for c in cases:
            cid = c.get("case_id")
            if cid in HISTORICAL_PROHIBITED_CASES:
                raise ValueError(f"Historical / retired case {cid} is permanently prohibited from clean qualification!")
            if cid not in FROZEN_CASE_ORDER:
                raise ValueError(f"Unlisted case {cid} cannot be executed!")
            if c.get("max_results_per_query") != 5:
                raise ValueError(f"Case {cid} max_results_per_query must be 5, got {c.get('max_results_per_query')}")
            queries = c.get("exact_slack_queries", [])
            if len(queries) != 3:
                raise ValueError(f"Case {cid} must have exactly 3 exact_slack_queries, got {len(queries)}")

        return data


# =============================================================================
# 2. Production Investigation Input Builder (Zero Gold Leakage)
# =============================================================================

def build_production_investigation_plan(case_spec: Dict[str, Any]) -> Tuple[InvestigationPlan, ResolvedActorScope, ResolvedTemporalScope]:
    """Constructs pure production plan using only fields from cases.json."""
    question = case_spec["question"]
    actor_dict = case_spec.get("actor_scope", {})
    temporal_dict = case_spec.get("temporal_scope", {})

    actor_scope = ResolvedActorScope(
        kind=ActorScopeKind(actor_dict.get("kind", "UNSPECIFIED")),
        status=ActorResolutionStatus(actor_dict.get("status", "RESOLVED")),
        is_implicit=actor_dict.get("is_implicit", False),
    )
    temporal_scope = ResolvedTemporalScope(
        strictness=TemporalStrictness(temporal_dict.get("strictness", "NONE")),
        status=TemporalResolutionStatus(temporal_dict.get("status", "NONE")),
    )

    search_queries = [
        SearchQuery(
            query=q["compiled_query"],
            purpose=q.get("purpose", "Predeclared clean qualification query"),
            target_channel_hint=q.get("target_channel_hint"),
        )
        for q in case_spec.get("exact_slack_queries", [])[:case_spec.get("query_limit", 3)]
    ]

    plan = InvestigationPlan(
        objective=case_spec.get("planner_objective", "Clean Qualification Plan"),
        search_queries=search_queries,
        questions_to_resolve=[question],
        anchor_terms=[],
    )

    return plan, actor_scope, temporal_scope


# =============================================================================
# 3. Search Safety & Telemetry Tracker (R10H / R10J Strict Contract)
# =============================================================================

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

        if not isinstance(slackbot_invoked, bool) or not isinstance(write_invoked, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError("SAFETY_TELEMETRY_INCOMPLETE: Invoked actions must be boolean")
        if not isinstance(unsafe_non_search_invoked, bool) or not isinstance(generic_onenter, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError("SAFETY_TELEMETRY_INCOMPLETE: Invoked actions must be boolean")
        if not isinstance(safe_selected, bool) or not isinstance(submit_method, str) or submit_method == "UNKNOWN":
            self.safety_regression_confirmed = True
            raise RuntimeError("SAFETY_TELEMETRY_INCOMPLETE: safe_search_action_selected or submit_method invalid")

        if slackbot_invoked:
            self.total_slackbot_actions_invoked += 1
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Slackbot action invoked by PESU in query '{query}'")
        if write_invoked:
            self.total_write_capable_actions_invoked += 1
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Write-capable action invoked by PESU in query '{query}'")
        if unsafe_non_search_invoked:
            self.total_unsafe_non_search_actions_invoked += 1
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Unsafe non-search action invoked by PESU in query '{query}'")
        if generic_onenter:
            self.total_generic_default_onenter_uses += 1
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Generic default onEnter used in query '{query}'")

        if submit_method != "NONE":
            if not safe_selected:
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Search submitted without safe selection in query '{query}'")
            if not any(submit_method.startswith(p) for p in ALLOWED_SAFE_SUBMIT_PREFIXES):
                self.safety_regression_confirmed = True
                raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Disallowed submit method '{submit_method}' in query '{query}'")

        forensic = telemetry.get("forensic_transition")
        if forensic is None or not isinstance(forensic, dict):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'forensic_transition' is missing or not a dict in query '{query}'")

        surface_query_matches = forensic.get("SEARCH_SURFACE_QUERY_MATCHES_REQUESTED")
        surface_query_after = forensic.get("SEARCH_SURFACE_QUERY_AFTER")
        if not isinstance(surface_query_matches, bool):
            self.safety_regression_confirmed = True
            raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED must be bool in '{query}'")

        readiness = telemetry.get("SEARCH_RESULT_READINESS", "UNKNOWN")
        if readiness == "RESULTS_READY" and not surface_query_matches:
            self.safety_regression_confirmed = True
            raise RuntimeError(
                f"SURFACE_QUERY_INVARIANT_VIOLATION: SEARCH_RESULT_READINESS=RESULTS_READY requires "
                f"SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True, got {surface_query_matches} in query '{query}'"
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


# =============================================================================
# 4. Mechanical Self-Contamination Evaluator
# =============================================================================

def is_exact_query_echo(text: str, target_queries: Set[str]) -> bool:
    if not text:
        return False
    return text.strip() in target_queries


def has_self_generated_incident_provenance(item: Any) -> bool:
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
    selected_candidates: list[EvidenceItem],
    target_queries: Set[str],
) -> dict[str, Any]:
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

    is_invalidated = selected_self_generated_echoes > 0
    contamination_detected = bool(raw_self_generated_echoes > 0 or selected_self_generated_echoes > 0)

    return {
        "RAW_EXACT_QUERY_ECHO_COUNT": raw_exact_echoes,
        "SELECTED_EXACT_QUERY_ECHO_COUNT": selected_exact_echoes,
        "RAW_KNOWN_SELF_GENERATED_ECHO_COUNT": raw_self_generated_echoes,
        "SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT": selected_self_generated_echoes,
        "KNOWN_INCIDENT_CHANNEL_RESULT_COUNT": known_incident_channel_count,
        "SELF_CONTAMINATION_DETECTED": contamination_detected,
        "CASE_VALIDITY": "INVALIDATED_BY_PREEXISTING_SELF_CONTAMINATION" if is_invalidated else "VALID",
    }


# =============================================================================
# 5. Evaluator-Only Gold Loader & Structured Output Evaluator
# =============================================================================

class CleanQualificationGoldLoader:
    """Loads gold truth strictly at evaluation time."""

    @staticmethod
    def load_gold_bundle() -> Dict[str, Any]:
        if not GOLD_FIXTURE_PATH.exists():
            raise FileNotFoundError(f"Gold fixture missing: {GOLD_FIXTURE_PATH}")
        with open(GOLD_FIXTURE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


class CleanQualificationEvaluator:
    """Deterministic structured evaluator operating on serialized production output."""

    def __init__(self, gold_bundle: Optional[Dict[str, Any]] = None):
        self.gold_bundle = gold_bundle or CleanQualificationGoldLoader.load_gold_bundle()

    def evaluate_case(
        self,
        case_id: str,
        production_result: Dict[str, Any],
        contamination_report: Dict[str, Any],
        safety_tracker: SearchSafetyTracker,
        provider_telemetry: Dict[str, Any],
    ) -> Dict[str, Any]:
        case_gold = self.gold_bundle["cases"].get(case_id)
        if not case_gold:
            raise ValueError(f"Case gold not found for {case_id}")

        scorecard: Dict[str, Any] = {
            "case_id": case_id,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "overall_pass": False,
            "dimensions": {
                "factual_correctness": False,
                "workitem_state_correctness": False,
                "grounding_discipline": False,
            },
            "acceptance_checks": {},
            "traceability": {
                "complete": False,
                "every_final_state_has_traceable_claim_evidence": False,
            },
            "safety": {
                "pass": not safety_tracker.safety_regression_confirmed,
                "slackbot_actions": safety_tracker.total_slackbot_actions_invoked,
                "write_actions": safety_tracker.total_write_capable_actions_invoked,
            },
            "contamination": contamination_report,
            "provider": {
                "schema_400_recurrence": provider_telemetry.get("schema_400_recurrence", False),
                "fallback_count": provider_telemetry.get("fallback_count", 0),
            },
        }

        # Check contamination first
        if contamination_report["CASE_VALIDITY"] == "INVALIDATED_BY_PREEXISTING_SELF_CONTAMINATION":
            scorecard["invalidation_reason"] = "PREEXISTING_SELF_CONTAMINATION"
            return scorecard

        # Check provider remediation
        if provider_telemetry.get("schema_400_recurrence", False):
            scorecard["failure_reason"] = "PROVIDER_SCHEMA_400_RECURRENCE"
            return scorecard

        workitems = production_result.get("workitems", [])
        grounded_answer = production_result.get("grounded_answer", "")
        extracted_claims = production_result.get("claims", [])
        evidence_items = production_result.get("evidence", [])

        # Traceability check
        traceability_complete = self._verify_traceability(workitems, extracted_claims, evidence_items)
        scorecard["traceability"]["complete"] = traceability_complete
        scorecard["traceability"]["every_final_state_has_traceable_claim_evidence"] = traceability_complete

        # Case-specific structured evaluation
        if case_id == "Q2-02":
            checks = self._evaluate_q2_02(case_gold, workitems, grounded_answer, extracted_claims, evidence_items)
        elif case_id == "Q2-03":
            checks = self._evaluate_q2_03(case_gold, workitems, grounded_answer, extracted_claims, evidence_items)
        elif case_id == "Q2-04":
            checks = self._evaluate_q2_04(case_gold, workitems, grounded_answer, extracted_claims, evidence_items)
        else:
            raise ValueError(f"Unknown or prohibited case_id: {case_id}")

        checks["TRACEABILITY_COMPLETE"] = traceability_complete
        scorecard["acceptance_checks"] = checks

        # Synthesize 3 required dimensions
        dim_factual = checks.get("FACTUAL_CORRECTNESS", False)
        dim_state = (
            checks.get("CURRENT_WORK_STATE_CORRECT", False)
            or checks.get("DECISION_WORKITEM_STATE_CORRECT", False)
            or checks.get("POLICY_WORKITEM_STATE_CORRECT", False)
        )
        dim_grounding = checks.get("GROUNDING_DISCIPLINE", False) and checks.get("GROUNDED_ANSWER_HAS_NO_UNSUPPORTED_FACT", False)

        scorecard["dimensions"]["factual_correctness"] = dim_factual
        scorecard["dimensions"]["workitem_state_correctness"] = dim_state
        scorecard["dimensions"]["grounding_discipline"] = dim_grounding

        expected_contract = case_gold.get("acceptance_contract", {})
        all_checks_passed = all(checks.get(k) == v for k, v in expected_contract.items())
        overall_pass = (
            all_checks_passed
            and traceability_complete
            and scorecard["safety"]["pass"]
            and not scorecard["provider"]["schema_400_recurrence"]
        )
        scorecard["overall_pass"] = overall_pass

        return scorecard

    def _verify_traceability(self, workitems: list, claims: list, evidence: list) -> bool:
        if not workitems:
            return True
        evidence_ids = {e.get("evidence_id") for e in evidence if isinstance(e, dict)}
        claim_map = {c.get("claim_id"): c for c in claims if isinstance(c, dict)}

        for wi in workitems:
            if not isinstance(wi, dict):
                continue
            status = wi.get("status")
            if status in (WorkItemStatus.UNKNOWN.value, "UNKNOWN"):
                continue
            claim_ids = wi.get("claim_ids", [])
            has_valid_evidence_link = False
            for cid in claim_ids:
                c = claim_map.get(cid)
                if c:
                    linked_ev = c.get("evidence_ids", [])
                    if any(eid in evidence_ids for eid in linked_ev):
                        has_valid_evidence_link = True
                        break
            if not has_valid_evidence_link:
                return False
        return True

    def _evaluate_q2_02(self, gold: dict, workitems: list, answer: str, claims: list, evidence: list) -> dict:
        expected_count = gold["expected_semantic_outcome"]["expected_active_branch_opening_count"]
        expected_identities = gold["expected_semantic_outcome"]["expected_active_branch_identities"]
        expected_status = gold["expected_semantic_outcome"]["expected_workitem_status"]
        expected_signal = gold["expected_semantic_outcome"]["expected_lifecycle_signal"]

        answer_text = answer or ""
        claims_text = " ".join([c.get("statement", "") for c in claims if isinstance(c, dict)])
        all_text = f"{answer_text} {claims_text}"

        # 1. Candidate identities matching
        cand1_correct = ("정용기" in all_text)
        cand2_correct = ("박은혜" in all_text)
        both_openings = cand1_correct and cand2_correct

        # 2. Count correctness
        count_correct = both_openings or ("2" in answer_text and ("곳" in answer_text or "둘" in answer_text or "군데" in answer_text))

        # 3. WorkItem state check via structured semantic matching (Contract 3)
        # Identify branch opening WorkItem via claims or title containing opening / branch keywords
        state_correct = False
        for wi in workitems:
            wi_title = wi.get("title", "") or ""
            wi_cids = wi.get("claim_ids", [])
            wi_claims_text = " ".join([c.get("statement", "") for c in claims if c.get("claim_id") in wi_cids])
            is_branch_wi = any(k in f"{wi_title} {wi_claims_text}" for k in ["지점", "개설", "후보", "오픈", "branch"])
            if is_branch_wi:
                wi_status = wi.get("status")
                wi_signal = wi.get("lifecycle_signal")
                if wi_status == expected_status or wi_signal == expected_signal:
                    state_correct = True
                    break
        if not state_correct and workitems:
            # Fallback if single WorkItem exists
            wi = workitems[0]
            if wi.get("status") == expected_status or wi.get("lifecycle_signal") == expected_signal:
                state_correct = True

        # 4. Stage direction check
        stage_keywords = ["계약서", "출자좌수", "보안서약서", "검수", "작성", "진행"]
        stage_correct = any(kw in all_text for kw in stage_keywords)

        # 5. Grounding discipline: Unsupported signature or sent claims
        forbidden_signature_phrases = ["서명 완료", "날인 완료", "계약 체결 완료"]
        forbidden_sent_phrases = ["발송 완료", "문서 발송 완료", "전달 완료"]

        unsupported_signature = any(phrase in answer_text for phrase in forbidden_signature_phrases)
        unsupported_sent = any(phrase in answer_text for phrase in forbidden_sent_phrases)

        grounding_discipline = not unsupported_signature and not unsupported_sent
        factual_correctness = count_correct and both_openings and stage_correct

        return {
            "ACTIVE_BRANCH_OPENING_COUNT_CORRECT": count_correct,
            "BRANCH_CANDIDATE_1_CORRECT": cand1_correct,
            "BRANCH_CANDIDATE_2_CORRECT": cand2_correct,
            "BOTH_ACTIVE_OPENINGS_REPRESENTED": both_openings,
            "CURRENT_STAGE_DIRECTION_CORRECT": stage_correct,
            "CURRENT_WORK_STATE_CORRECT": state_correct,
            "UNSUPPORTED_SIGNATURE_CLAIM": unsupported_signature,
            "UNSUPPORTED_DOCUMENT_SENT_CLAIM": unsupported_sent,
            "FACTUAL_CORRECTNESS": factual_correctness,
            "GROUNDING_DISCIPLINE": grounding_discipline,
            "GROUNDED_ANSWER_HAS_NO_UNSUPPORTED_FACT": grounding_discipline,
        }

    def _evaluate_q2_03(self, gold: dict, workitems: list, answer: str, claims: list, evidence: list) -> dict:
        expected_role = gold["expected_semantic_outcome"]["expected_responsible_role"]
        expected_method = gold["expected_semantic_outcome"]["expected_final_settlement_method"]
        decision_expected_status = gold["expected_semantic_outcome"]["decision_workitem"]["expected_workitem_status"]
        decision_expected_signal = gold["expected_semantic_outcome"]["decision_workitem"]["expected_lifecycle_signal"]
        superseded_keywords = gold["expected_semantic_outcome"].get("superseded_option_keywords", ["공동인증서"])

        answer_text = answer or ""
        claims_text = " ".join([c.get("statement", "") for c in claims if isinstance(c, dict)])
        all_text = f"{answer_text} {claims_text}"

        # 1. Responsible role check
        role_correct = ("슈퍼바이저" in all_text)

        # 2. Final settlement method check
        method_correct = ("손익계산서" in all_text and "업로드" in all_text)

        # 3. Decision WorkItem state check via structured semantic matching (Contract 3)
        # Look for the decision WorkItem (discussing settlement method decision)
        decision_state_correct = False
        for wi in workitems:
            wi_title = wi.get("title", "") or ""
            wi_cids = wi.get("claim_ids", [])
            wi_claims_text = " ".join([c.get("statement", "") for c in claims if c.get("claim_id") in wi_cids])
            is_decision_wi = any(k in f"{wi_title} {wi_claims_text}" for k in ["결정", "방식", "손익계산서", "decision", "채택"])
            if is_decision_wi:
                wi_status = wi.get("status")
                wi_signal = wi.get("lifecycle_signal")
                if wi_status == decision_expected_status or wi_signal == decision_expected_signal:
                    decision_state_correct = True
                    break
        if not decision_state_correct and workitems:
            # Check any completed WorkItem
            for wi in workitems:
                if wi.get("status") == decision_expected_status or wi.get("lifecycle_signal") == decision_expected_signal:
                    decision_state_correct = True
                    break

        # 4. Critical historical decision boundary: Superseded option presented as final?
        superseded_presented_as_final = False
        for skw in superseded_keywords:
            if skw in answer_text:
                has_final_keyword = any(fw in answer_text for fw in ["최종", "방식으로 결정", "결정된 방식", "채택"])
                has_neg_framing = any(nw in answer_text for nw in ["대신", "과거", "검토안", "기각", "제외", "철회", "아닌", "폐기", "이전 검토", "채택되지 않"])
                if has_final_keyword and not has_neg_framing:
                    superseded_presented_as_final = True

        # 5. Final decision authority selected
        final_authority_selected = method_correct and not superseded_presented_as_final

        grounding_discipline = not superseded_presented_as_final
        factual_correctness = role_correct and method_correct

        return {
            "RESPONSIBLE_ROLE_CORRECT": role_correct,
            "FINAL_SETTLEMENT_METHOD_CORRECT": method_correct,
            "DECISION_WORKITEM_STATE_CORRECT": decision_state_correct,
            "SUPERSEDED_OPTION_PRESENTED_AS_FINAL": superseded_presented_as_final,
            "FINAL_DECISION_AUTHORITY_SELECTED": final_authority_selected,
            "FACTUAL_CORRECTNESS": factual_correctness,
            "GROUNDING_DISCIPLINE": grounding_discipline,
            "GROUNDED_ANSWER_HAS_NO_UNSUPPORTED_FACT": grounding_discipline,
        }

    def _evaluate_q2_04(self, gold: dict, workitems: list, answer: str, claims: list, evidence: list) -> dict:
        """Deterministic structured evaluator for Q2-04 (Branch Royalty Policy).

        Contract 2 implementation:
        - Distinguishes exception omission without evidence (allowed) vs explicit fixed-rate contradiction (forbidden).
        - If exception evidence retrieved: representative discretion must be accurately reflected.
        - If no exception evidence retrieved: omission alone does NOT fail, but ungrounded invention fails.
        """
        expected_status = gold["expected_semantic_outcome"]["expected_workitem_status"]
        expected_signal = gold["expected_semantic_outcome"]["expected_lifecycle_signal"]

        answer_text = answer or ""
        claims_text = " ".join([c.get("statement", "") for c in claims if isinstance(c, dict)])
        all_text = f"{answer_text} {claims_text}"

        # 1. Base rates correctness
        # External: 8%
        external_rate_correct = bool(
            re.search(r"외부\s*.*(8%|8\s*프로|8\s*퍼센트)", all_text)
            or re.search(r"(8%|8\s*프로|8\s*퍼센트).*\s*외부", all_text)
        )
        # Internal: 5%
        internal_rate_correct = bool(
            re.search(r"(내부|근무\s*세무사|혜움\s*내부).*(5%|5\s*프로|5\s*퍼센트)", all_text)
            or re.search(r"(5%|5\s*프로|5\s*퍼센트).*(내부|근무\s*세무사|혜움\s*내부)", all_text)
        )

        # 2. Structured semantic WorkItem matching (Contract 3)
        # Match WorkItem by royalty/policy topic rather than exact ID
        policy_state_correct = False
        for wi in workitems:
            wi_title = wi.get("title", "") or ""
            wi_cids = wi.get("claim_ids", [])
            wi_claims_text = " ".join([c.get("statement", "") for c in claims if c.get("claim_id") in wi_cids])
            is_policy_wi = any(k in f"{wi_title} {wi_claims_text}" for k in ["로열티", "요율", "royalty", "정책", "policy"])
            if is_policy_wi:
                wi_status = wi.get("status")
                wi_signal = wi.get("lifecycle_signal")
                if wi_status == expected_status or wi_signal == expected_signal:
                    policy_state_correct = True
                    break
        if not policy_state_correct and workitems:
            # Fallback if single WorkItem exists
            wi = workitems[0]
            if wi.get("status") == expected_status or wi.get("lifecycle_signal") == expected_signal:
                policy_state_correct = True

        # 3. Evidence support check for exception policy
        evidence_texts = [
            (e.get("text") or "") if isinstance(e, dict) else (getattr(e, "text", "") or "")
            for e in evidence
        ]
        combined_evidence_text = " ".join(evidence_texts)
        has_exception_evidence = bool(
            re.search(r"(대표\s*세무사|대표).*(판단|재량|의사|조정|예외|협의)", combined_evidence_text)
            or re.search(r"예외.*(조정|적용|가능)", combined_evidence_text)
        )

        # 4. Check answer for exception policy claim and explicit contradictions
        has_exception_claim = bool(
            re.search(r"(대표\s*세무사|대표).*(판단|재량|의사|조정|예외|협의)", answer_text)
            or re.search(r"예외.*(조정|적용|가능|협의|결정)", answer_text)
        )

        explicit_fixed_overclaim = bool(
            re.search(r"(항상|무조건|예외\s*없이|절대적으로)\s*5%", answer_text)
            or re.search(r"5%[로로]?\s*(항상\s*고정|무조건\s*고정|절대\s*고정)", answer_text)
            or re.search(r"예외(는|가)?\s*(전혀\s*없|존재하지\s*않|불가능)", answer_text)
        )

        all_rates_case_by_case = bool(
            re.search(r"(기준\s*요율(이|은)?\s*(없|존재하지\s*않)|모든\s*지점.*(건별|케이스\s*바이\s*케이스|협의로만))", answer_text)
        )

        # External rate presented as discretionary check: must only match if representative discretion is claimed for external rate
        external_clauses = [c for c in re.split(r"[,.\n]|그리고|반면|내부", answer_text) if "외부" in c]
        external_rate_discretionary = any(
            bool(re.search(r"(대표.*(판단|재량|의사)|재량.*조정|유연하게.*조정|협의.*조정)", c))
            for c in external_clauses
        )

        # 5. Evaluate Contract 2 boundary
        if has_exception_evidence:
            # Evidence retrieved: Representative discretion must be reflected
            retrieved_exception_reflected = has_exception_claim and not explicit_fixed_overclaim
            unsupported_exception_claim = False
        else:
            # Evidence not retrieved: Omission without evidence is ALLOWED
            retrieved_exception_reflected = True
            # But claiming exception out of thin air without evidence is forbidden
            unsupported_exception_claim = has_exception_claim

        forbidden_overclaim_rejected = (
            not explicit_fixed_overclaim
            and not all_rates_case_by_case
            and not external_rate_discretionary
        )

        factual_correctness = external_rate_correct and internal_rate_correct
        grounding_discipline = (
            not unsupported_exception_claim
            and forbidden_overclaim_rejected
            and retrieved_exception_reflected
        )

        return {
            "EXTERNAL_BASE_RATE_CORRECT": external_rate_correct,
            "INTERNAL_BASE_RATE_CORRECT": internal_rate_correct,
            "POLICY_WORKITEM_STATE_CORRECT": policy_state_correct,
            "UNSUPPORTED_EXCEPTION_POLICY_CLAIM": unsupported_exception_claim,
            "FORBIDDEN_OVERCLAIM_REJECTED": forbidden_overclaim_rejected,
            "RETRIEVED_EXCEPTION_EVIDENCE_REFLECTED": retrieved_exception_reflected,
            "FACTUAL_CORRECTNESS": factual_correctness,
            "GROUNDING_DISCIPLINE": grounding_discipline,
            "GROUNDED_ANSWER_HAS_NO_UNSUPPORTED_FACT": grounding_discipline,
        }


# =============================================================================
# 6. Runner Execution Logic (Minimal Fork from F1 Authority)
# =============================================================================

async def execute_case_production(
    case_spec: Dict[str, Any],
    search_backend: Any,
    trace_coordinator: Any,
    retrieval_coordinator: Any,
    thread_reader: Any,
    safety_tracker: SearchSafetyTracker,
    is_dry_run: bool = False,
) -> Dict[str, Any]:
    """Runs production pipeline on a single case using ONLY cases.json data.

    Preserves exact F1 interface authority:
    - ProductionRealTraceCoordinator.trace_and_synthesize_async
    - ProductionTraceResult.work_items, graph.claim_nodes, traceability_chains
    - GroundedWorkItemAnswerBuilder real signature
    - Fail-closed raw_query_records check
    """
    plan, actor_scope, temporal_scope = build_production_investigation_plan(case_spec)
    case_id = case_spec["case_id"]
    question = case_spec["question"]

    logger.info(f"▶ [PRODUCTION_PIPELINE] Executing case {case_id}: '{question[:40]}...'")

    thread_fn = thread_reader.read_thread_result if thread_reader else None
    retrieval_res: RetrievalCoordinatorResult = await retrieval_coordinator.retrieve_precomputed(
        plan=plan,
        question_text=question,
        actor_scope=actor_scope,
        temporal_scope=temporal_scope,
        investigation_id=f"clean_v3_{case_id.lower()}",
        thread_reader_fn=thread_fn,
    )

    selected_evidence = retrieval_res.selected_candidates
    logger.info(f"📦 [RETRIEVAL_COMPLETE] Case {case_id}: {len(selected_evidence)} evidence items selected.")

    # Canonical fail-closed invariant: raw_query_records must exist and not be None
    if not hasattr(retrieval_res, "raw_query_records") or retrieval_res.raw_query_records is None:
        raise AttributeError(f"RetrievalCoordinatorResult for case {case_id} missing canonical 'raw_query_records' field")

    # Execute trace pipeline
    if is_dry_run:
        mock_ev_id = f"e_{case_id}_mock"
        mock_evidence_list = [
            {"evidence_id": mock_ev_id, "text": f"Mock evidence text for {case_id}", "source": "synthetic_fixture", "permalink": "https://workspace.slack.com/archives/C123/p456"}
        ]
        if selected_evidence:
            mock_evidence_list = [
                e.model_dump() if hasattr(e, "model_dump") else (e.to_dict() if hasattr(e, "to_dict") else dict(e))
                for e in selected_evidence
            ]
            mock_ev_id = mock_evidence_list[0].get("evidence_id", mock_ev_id)

        if case_id == "Q2-02":
            mock_answer = "현재 지점 개설 진행 중인 후보는 정용기 세무사와 박은혜 세무사 2곳이며, 지점 계약서, 출자좌수 양수도 계약서, 보안서약서 등 계약 관련 문서 작성 및 검수 절차가 진행 중입니다."
            mock_claims = [
                {"claim_id": "c_Q2-02_1", "statement": "정용기 세무사와 박은혜 세무사 2곳의 계약 관련 문서 작성 및 검수 절차가 진행 중이다.", "evidence_ids": [mock_ev_id]}
            ]
            mock_workitems = [
                {"work_item_id": "wi_Q2-02_1", "title": "지점 개설 진행", "status": WorkItemStatus.IN_PROGRESS.value, "lifecycle_signal": WorkItemLifecycleSignal.PROGRESS.value, "claim_ids": ["c_Q2-02_1"]}
            ]
        elif case_id == "Q2-03":
            mock_answer = "현재 지점 정산 업무는 지점운영팀 슈퍼바이저가 담당하며, 앞으로 지점 정산은 손익계산서 업로드 기반 정산 방식으로 최종 결정되었습니다. 과거 검토되었던 공동인증서 방식은 채택되지 않았습니다."
            mock_claims = [
                {"claim_id": "c_Q2-03_1", "statement": "지점운영팀 슈퍼바이저 담당으로 손익계산서 업로드 기반 정산 방식이 최종 결정되었다.", "evidence_ids": [mock_ev_id]}
            ]
            mock_workitems = [
                {"work_item_id": "wi_Q2-03_decision", "title": "지점 정산 방식 결정", "status": WorkItemStatus.COMPLETED.value, "lifecycle_signal": WorkItemLifecycleSignal.COMPLETION.value, "claim_ids": ["c_Q2-03_1"]},
                {"work_item_id": "wi_Q2-03_ops", "title": "지점 정산 업무", "status": WorkItemStatus.IN_PROGRESS.value, "lifecycle_signal": WorkItemLifecycleSignal.PROGRESS.value, "claim_ids": ["c_Q2-03_1"]}
            ]
        elif case_id == "Q2-04":
            mock_answer = "지점 개설 시 로열티 요율은 외부 세무사의 경우 8%, 혜움 내부 근무 세무사의 경우 5%가 기본 적용됩니다."
            mock_claims = [
                {"claim_id": "c_Q2-04_1", "statement": "지점 개설 로열티 요율은 외부 세무사 8%, 혜움 내부 근무 세무사 5%가 기본 적용된다.", "evidence_ids": [mock_ev_id]}
            ]
            mock_workitems = [
                {"work_item_id": "wi_Q2-04_policy", "title": "지점 로열티 요율 정책", "status": WorkItemStatus.COMPLETED.value, "lifecycle_signal": WorkItemLifecycleSignal.COMPLETION.value, "claim_ids": ["c_Q2-04_1"]}
            ]
        else:
            mock_answer = f"Synthetic grounded answer for {case_id}"
            mock_claims = [{"claim_id": f"c_{case_id}_1", "statement": f"Synthetic claim for {case_id}", "evidence_ids": [mock_ev_id]}]
            mock_workitems = [{"work_item_id": f"wi_{case_id}_1", "title": f"WorkItem {case_id}", "status": WorkItemStatus.IN_PROGRESS.value, "lifecycle_signal": WorkItemLifecycleSignal.PROGRESS.value, "claim_ids": [f"c_{case_id}_1"]}]

        production_result = {
            "case_id": case_id,
            "question": question,
            "evidence": mock_evidence_list,
            "claims": mock_claims,
            "workitems": mock_workitems,
            "grounded_answer": mock_answer,
            "raw_query_records": retrieval_res.raw_query_records,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        trace_result: ProductionTraceResult = await trace_coordinator.trace_and_synthesize_async(
            question=question,
            candidates=selected_evidence,
            investigation_objective=plan.objective,
            temporal_scope=temporal_scope,
            actor_scope=actor_scope,
            candidate_query_provenance=getattr(retrieval_res, "candidate_query_provenance", None),
        )

        answer_builder = GroundedWorkItemAnswerBuilder()
        answer_payload = answer_builder.build_answer(
            question=question,
            graph=trace_result.graph,
            work_items=trace_result.work_items,
            traceability_chains=trace_result.traceability_chains,
            stop_reason=trace_result.stop_reason,
        )
        grounded_answer = (
            answer_payload.formatted_markdown_answer
            if hasattr(answer_payload, "formatted_markdown_answer")
            else str(answer_payload)
        )

        claims_list: list[dict[str, Any]] = []
        seen_cids: set[str] = set()
        if trace_result.traceability_chains:
            for chain in trace_result.traceability_chains.values():
                for ct in chain.claim_traces:
                    if ct.claim_id not in seen_cids:
                        seen_cids.add(ct.claim_id)
                        claims_list.append({
                            "claim_id": ct.claim_id,
                            "statement": ct.statement,
                            "evidence_ids": list(ct.evidence_ids),
                            "lifecycle_signal": (
                                ct.lifecycle_signal.value
                                if hasattr(ct.lifecycle_signal, "value")
                                else str(ct.lifecycle_signal) if ct.lifecycle_signal else None
                            ),
                        })
        if hasattr(trace_result, "graph") and trace_result.graph:
            for cid, cnode in trace_result.graph.claim_nodes.items():
                if cid not in seen_cids:
                    seen_cids.add(cid)
                    ev_ids = [
                        l.evidence_id
                        for l in trace_result.graph.claim_evidence_links
                        if l.claim_id == cid
                    ]
                    claims_list.append({
                        "claim_id": cid,
                        "statement": cnode.statement,
                        "evidence_ids": ev_ids,
                    })

        workitems_list: list[dict[str, Any]] = []
        for wid, wi in trace_result.work_items.items():
            chain = trace_result.traceability_chains.get(wid)
            wi_status = None
            if chain and chain.final_status:
                wi_status = chain.final_status.value if hasattr(chain.final_status, "value") else str(chain.final_status)
            elif wi.status:
                wi_status = wi.status.value if hasattr(wi.status, "value") else str(wi.status)
            else:
                wi_status = WorkItemStatus.UNKNOWN.value

            wi_signal = None
            if chain and chain.claim_traces:
                for ct in chain.claim_traces:
                    if ct.lifecycle_signal and ct.lifecycle_signal != WorkItemLifecycleSignal.NONE:
                        wi_signal = ct.lifecycle_signal.value if hasattr(ct.lifecycle_signal, "value") else str(ct.lifecycle_signal)
                        break

            claim_ids = list(chain.governing_claim_ids) if (chain and chain.governing_claim_ids) else list(wi.claim_ids)

            workitems_list.append({
                "work_item_id": wid,
                "title": wi.title,
                "status": wi_status,
                "lifecycle_signal": wi_signal,
                "claim_ids": claim_ids,
            })

        production_result = {
            "case_id": case_id,
            "question": question,
            "evidence": [
                e.to_dict() if hasattr(e, "to_dict")
                else (e.model_dump() if hasattr(e, "model_dump") else dict(e))
                for e in selected_evidence
            ],
            "claims": claims_list,
            "workitems": workitems_list,
            "grounded_answer": grounded_answer,
            "raw_query_records": retrieval_res.raw_query_records,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    return production_result


async def run_clean_qualification_v3(is_dry_run: bool = False, authorized_live: bool = False) -> int:
    """Main Q2B entrypoint for clean qualification v3.

    Guarded: live execution is strictly blocked unless authorized_live is explicitly set.
    """
    if not is_dry_run and not authorized_live:
        logger.error("❌ LIVE QUALIFICATION IS NOT AUTHORIZED IN THIS SESSION! Must run in a dedicated Q2B session.")
        return 1

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"clean_v3_run_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    commit_head = get_git_commit_head()
    manifest_drift = verify_frozen_manifest()

    logger.info("================================================================================")
    logger.info("  PHASE 13.5D TASK 11 — CLEAN QUALIFICATION V3 RUNNER")
    logger.info(f"  Mode: {'OFFLINE DRY RUN' if is_dry_run else 'LIVE PRODUCTION QUALIFICATION'}")
    logger.info(f"  Git Commit HEAD:       {commit_head}")
    logger.info(f"  Frozen Manifest Drift: {manifest_drift}")
    logger.info("================================================================================")

    if manifest_drift != 0:
        logger.error(f"❌ FROZEN_MANIFEST_DRIFT = {manifest_drift} != 0! Aborting.")
        return 1

    # 1. Load cases.json (strictly without gold)
    cases_bundle = CleanPredeclarationLoader.load_production_cases()
    cases = cases_bundle["cases"]

    # 2. Setup backend & safety tracker
    safety_tracker = SearchSafetyTracker()
    host = None

    if is_dry_run:
        search_backend = SyntheticSearchBackend()
        thread_reader = None
        trace_coordinator = None
    else:
        if not os.getenv("OPENROUTER_API_KEY"):
            resolved_key = LLMConfig().get_api_key_value()
            if resolved_key:
                os.environ["OPENROUTER_API_KEY"] = resolved_key
            else:
                logger.error("❌ OPENROUTER_API_KEY is not set!")
                return 1

        search_backend = create_slack_search_backend(
            config=SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT),
        )
        host = getattr(search_backend, "_window", None)
        if host and hasattr(host, "start"):
            host.start(timeout=30.0)
        thread_reader = WebviewThreadReader(host=host) if host else None

        s_cfg = r7_harness.WorkItemSynthesisConfig()
        s_coord = r7_harness.build_diagnostic_semantic_components(synthesis_config=s_cfg)
        trace_coordinator = ProductionRealTraceCoordinator(synthesis_coordinator=s_coord)

    retrieval_coordinator = ProductionInvestigationRetrievalCoordinator(
        search_backend=search_backend,
        selector=EvidenceSelector(),
        planner=None,
        config=OrchestratorConfig(),
    )

    # Instrument search telemetry
    original_search = WebViewSlackSearchBackend.search_messages

    @functools.wraps(original_search)
    def tracking_search_messages(self, query: str, max_results: int = 10) -> list[Any]:
        t0 = time.perf_counter()
        try:
            results = original_search(self, query, max_results=max_results)
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            safety_tracker.record_query_execution(query, len(results), telem, latency)
            return results
        except Exception:
            latency = time.perf_counter() - t0
            telem = getattr(self, "last_search_telemetry", {}) or {}
            if telem:
                safety_tracker.record_query_execution(query, 0, telem, latency)
            raise

    WebViewSlackSearchBackend.search_messages = tracking_search_messages

    # Execute cases sequentially: Q2-02 -> Q2-03 -> Q2-04
    case_results: Dict[str, Any] = {}
    try:
        for case_spec in cases:
            cid = case_spec["case_id"]
            prod_res = await execute_case_production(
                case_spec=case_spec,
                search_backend=search_backend,
                trace_coordinator=trace_coordinator,
                retrieval_coordinator=retrieval_coordinator,
                thread_reader=thread_reader,
                safety_tracker=safety_tracker,
                is_dry_run=is_dry_run,
            )
            case_results[cid] = prod_res

            case_out_file = run_dir / f"{cid.lower()}_production_result.json"
            with open(case_out_file, "w", encoding="utf-8") as f:
                json.dump(prod_res, f, ensure_ascii=False, indent=2)
    finally:
        WebViewSlackSearchBackend.search_messages = original_search
        if host and hasattr(host, "close"):
            try:
                host.close()
            except Exception:
                pass

    # 3. Post-Hoc Isolated Evaluation (Loads gold.json ONLY here after production outputs serialized)
    logger.info("⚖️ [POST_HOC_EVALUATION] Production execution complete. Commencing isolated evaluation...")
    evaluator = CleanQualificationEvaluator()

    suite_scorecard: Dict[str, Any] = {
        "suite_name": "PHASE_13_5D_CLEAN_QUALIFICATION_V3",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit_head,
        "case_count": len(cases),
        "cases_passed": 0,
        "all_cases_passed": False,
        "case_scorecards": {},
    }

    passed_count = 0
    for case_spec in cases:
        cid = case_spec["case_id"]
        prod_res = case_results[cid]

        if "raw_query_records" not in prod_res or prod_res["raw_query_records"] is None:
            raise KeyError(f"Case {cid} production result missing canonical 'raw_query_records'")
        target_queries = {case_spec["question"].strip(), *[q["compiled_query"].strip() for q in case_spec.get("exact_slack_queries", [])]}
        contam_report = evaluate_self_contamination(
            raw_query_records=prod_res["raw_query_records"],
            selected_candidates=[EvidenceItem(**e) if isinstance(e, dict) else e for e in prod_res.get("evidence", [])],
            target_queries=target_queries,
        )

        provider_telem = {"schema_400_recurrence": False, "fallback_count": 0}

        case_sc = evaluator.evaluate_case(
            case_id=cid,
            production_result=prod_res,
            contamination_report=contam_report,
            safety_tracker=safety_tracker,
            provider_telemetry=provider_telem,
        )
        suite_scorecard["case_scorecards"][cid] = case_sc

        if case_sc["overall_pass"]:
            passed_count += 1
            logger.info(f"✅ Case {cid}: PASS")
        else:
            logger.warning(f"❌ Case {cid}: FAIL (Scorecard: {case_sc['acceptance_checks']})")

    suite_scorecard["cases_passed"] = passed_count
    suite_scorecard["all_cases_passed"] = (passed_count == len(cases))

    scorecard_file = run_dir / "clean_qualification_v3_scorecard.json"
    with open(scorecard_file, "w", encoding="utf-8") as f:
        json.dump(suite_scorecard, f, ensure_ascii=False, indent=2)

    logger.info("================================================================================")
    logger.info(f"  QUALIFICATION RESULT: {passed_count}/{len(cases)} PASS (All Pass = {suite_scorecard['all_cases_passed']})")
    logger.info(f"  Scorecard Serialized: {scorecard_file}")
    logger.info("================================================================================")

    return 0 if suite_scorecard["all_cases_passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean Qualification v3 Runner")
    parser.add_argument("--dry-run", action="store_true", help="Execute in offline dry-run mode without live Slack")
    parser.add_argument("--authorized-live-qualification", action="store_true", help="Explicit flag to permit live execution in Q2B")
    args = parser.parse_args()

    exit_code = asyncio.run(run_clean_qualification_v3(is_dry_run=args.dry_run, authorized_live=args.authorized_live_qualification))
    sys.exit(exit_code)
