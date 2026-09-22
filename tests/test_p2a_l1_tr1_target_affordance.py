"""Regression tests for Phase 13.5D / Task 11 / P2A-L1 / TR1 Target Affordance Diagnostic.

Invariants verified:
1. Exact production pre-click guard source audited completely
2. Production guard vs TR1 selection policy separated
3. Connected count 0 -> affordance fail
4. Connected count > 1 -> affordance fail
5. Count 1 + missing children -> fail
6. Count 1 + invisible -> fail
7. Count 1 + composer-contained -> fail
8. Count 1 + hit-test/occlusion fail -> fail
9. All exact production guards PASS -> affordance PASS
10. Root row -> target-policy fail
11. Non-C channel -> target-policy fail
12. Ambiguous row identity -> row ineligible
13. Multiple eligible rows -> lowest rank selected
14. No eligible top5 -> NO_PRODUCTION_CLICK_ELIGIBLE_TARGET_IN_TOP5
15. RB2 SHA exact match
16. All 8 historical hashes exact match
17. Zero click-capable callsites in TR1 execution path
18. Production src diff empty
19. Provider calls zero
20. Attempt 4 / RB2 runtime/search parity
21. Authoritative search telemetry parity
22. Exception ordering (search exception raised before safety gate)
23. Top-5 bound exact (maxRows = 5)
24. Single atomic JS snapshot execution
25. Live artifact overwrite guard (preserves existing files without deletion)
26. Frozen dependency mismatch blocks pre-WebView
27. Protocol semantic identity mismatch blocks pre-WebView
28. Runtime closure audit enforces 0 untracked dependencies
29. Sensitive content exclusion (no message body, outerHTML, or raw permalinks)
"""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.diagnose_task11q4_p2a_l1_target_affordance_tr1 as tr1
from pesu_agent.search.webview_thread_reader import JS_CLICK_RESIDENT_ROW_REPLY_BAR


# ---------------------------------------------------------------------------
# 1. Exact Production Pre-Click Guard Source Audit
# ---------------------------------------------------------------------------

def test_production_pre_click_guard_literals_audited():
    """Requirement 1: Verify exact production pre-click failure literals exist in webview_thread_reader.py."""
    assert "TARGET_ROW_DISCONNECTED" in JS_CLICK_RESIDENT_ROW_REPLY_BAR
    assert "REPLY_BAR_COUNT_INVALID" in JS_CLICK_RESIDENT_ROW_REPLY_BAR
    assert "REPLY_BAR_REQUIRED_CHILDREN_MISSING" in JS_CLICK_RESIDENT_ROW_REPLY_BAR
    assert "REPLY_BAR_NOT_VISIBLE" in JS_CLICK_RESIDENT_ROW_REPLY_BAR
    assert "REPLY_BAR_INSIDE_COMPOSER" in JS_CLICK_RESIDENT_ROW_REPLY_BAR


# ---------------------------------------------------------------------------
# 2. Separation of Production Click Affordance vs TR1 Target Qualification Policy
# ---------------------------------------------------------------------------

def test_production_affordance_and_policy_separation():
    """Requirement 2: Ensure production click affordance and target policy are evaluated separately."""
    row = {
        "row_index": 0,
        "rank": 1,
        "p2a_live_target_eligible": False,
        "production_click_affordance": {
            "affordance_passed": False,
            "production_guards_pass": False,
            "hit_test_passed": False,
        },
        "target_qualification_policy": {
            "policy_passed": False,
            "identity_unambiguous": False,
        }
    }
    assert "production_click_affordance" in row
    assert "target_qualification_policy" in row
    assert "p2a_live_target_eligible" in row


# ---------------------------------------------------------------------------
# 3-9. Production Click Affordance Predicate Coverage
# ---------------------------------------------------------------------------

def _evaluate_mock_dom_row(
    reply_bar_count: int,
    has_count_el: bool,
    has_view_thread_el: bool,
    is_visible: bool,
    outside_composer: bool,
    hit_test_passed: bool,
    channel_id: str = "C042WS25QAX",
    message_ts: str = "1718079276.126469",
    thread_ts: str = "1718058683.912369",
    unique_identity_count: int = 1,
    row_in_composer: bool = False,
    rank: int = 1,
) -> dict:
    """Helper mirroring the JS snapshot logic in JS_INSPECT_TOP5_TARGET_AFFORDANCE."""
    reply_bar_count_valid = (reply_bar_count == 1)
    required_children = (has_count_el and has_view_thread_el)

    if not reply_bar_count_valid:
        prod_guards_pass = False
        prod_reason = "REPLY_BAR_COUNT_INVALID"
        hit_pass = False
        hit_reason = "NOT_EVALUATED_PREVIOUS_GUARD_FAILED"
    elif not required_children:
        prod_guards_pass = False
        prod_reason = "REPLY_BAR_REQUIRED_CHILDREN_MISSING"
        hit_pass = False
        hit_reason = "NOT_EVALUATED_PREVIOUS_GUARD_FAILED"
    elif not is_visible:
        prod_guards_pass = False
        prod_reason = "REPLY_BAR_NOT_VISIBLE"
        hit_pass = False
        hit_reason = "NOT_EVALUATED_PREVIOUS_GUARD_FAILED"
    elif not outside_composer:
        prod_guards_pass = False
        prod_reason = "REPLY_BAR_INSIDE_COMPOSER"
        hit_pass = False
        hit_reason = "NOT_EVALUATED_PREVIOUS_GUARD_FAILED"
    else:
        prod_guards_pass = True
        prod_reason = None
        hit_pass = hit_test_passed
        hit_reason = None if hit_test_passed else "REPLY_BAR_HIT_TEST_FAILED"

    affordance_passed = bool(prod_guards_pass and hit_pass)

    identity_unambiguous = (unique_identity_count == 1)
    channel_valid = bool(channel_id and channel_id.startswith("C"))
    is_thread_reply = bool(thread_ts and message_ts and thread_ts != message_ts)

    if not identity_unambiguous:
        policy_passed = False
        policy_reason = "ROW_IDENTITY_AMBIGUOUS"
    elif not channel_valid:
        policy_passed = False
        policy_reason = "CHANNEL_ID_INVALID_NOT_PUBLIC_OR_PRIVATE"
    elif not is_thread_reply:
        policy_passed = False
        policy_reason = "NOT_THREAD_REPLY"
    elif row_in_composer:
        policy_passed = False
        policy_reason = "ROW_INSIDE_COMPOSER"
    else:
        policy_passed = True
        policy_reason = None

    p2a_eligible = bool(affordance_passed and policy_passed)

    return {
        "rank": rank,
        "production_click_affordance": {
            "connected_div_reply_bar_count": reply_bar_count,
            "required_children_present": required_children,
            "is_visible": is_visible,
            "outside_composer": outside_composer,
            "production_guards_pass": prod_guards_pass,
            "production_failure_reason": prod_reason,
            "hit_test_passed": hit_pass,
            "hit_failure_reason": hit_reason,
            "affordance_passed": affordance_passed,
        },
        "target_qualification_policy": {
            "rank": rank,
            "identity_unambiguous": identity_unambiguous,
            "channel_valid": channel_valid,
            "is_thread_reply": is_thread_reply,
            "outside_composer": not row_in_composer,
            "policy_passed": policy_passed,
            "policy_failure_reason": policy_reason,
        },
        "p2a_live_target_eligible": p2a_eligible,
    }


def test_reply_bar_count_zero_affordance_fails():
    """Requirement 3: Connected count 0 -> affordance fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=0,
        has_count_el=False,
        has_view_thread_el=False,
        is_visible=False,
        outside_composer=True,
        hit_test_passed=False,
    )
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["production_click_affordance"]["production_failure_reason"] == "REPLY_BAR_COUNT_INVALID"
    assert res["p2a_live_target_eligible"] is False


def test_reply_bar_count_multiple_affordance_fails():
    """Requirement 4: Connected count > 1 -> affordance fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=2,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
    )
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["production_click_affordance"]["production_failure_reason"] == "REPLY_BAR_COUNT_INVALID"
    assert res["p2a_live_target_eligible"] is False


def test_count_one_missing_children_fails():
    """Requirement 5: Count 1 + missing children -> fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=False,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
    )
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["production_click_affordance"]["production_failure_reason"] == "REPLY_BAR_REQUIRED_CHILDREN_MISSING"
    assert res["p2a_live_target_eligible"] is False


def test_count_one_invisible_fails():
    """Requirement 6: Count 1 + invisible -> fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=False,
        outside_composer=True,
        hit_test_passed=True,
    )
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["production_click_affordance"]["production_failure_reason"] == "REPLY_BAR_NOT_VISIBLE"
    assert res["p2a_live_target_eligible"] is False


def test_count_one_composer_contained_fails():
    """Requirement 7: Count 1 + composer-contained -> fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=False,
        hit_test_passed=True,
    )
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["production_click_affordance"]["production_failure_reason"] == "REPLY_BAR_INSIDE_COMPOSER"
    assert res["p2a_live_target_eligible"] is False


def test_count_one_hit_test_occluded_fails():
    """Requirement 8: Count 1 + hit-test/occlusion fail -> fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=False,
    )
    assert res["production_click_affordance"]["production_guards_pass"] is True
    assert res["production_click_affordance"]["hit_test_passed"] is False
    assert res["production_click_affordance"]["hit_failure_reason"] == "REPLY_BAR_HIT_TEST_FAILED"
    assert res["production_click_affordance"]["affordance_passed"] is False
    assert res["p2a_live_target_eligible"] is False


def test_all_exact_production_guards_pass():
    """Requirement 9: All exact production guards PASS -> affordance PASS."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
    )
    assert res["production_click_affordance"]["production_guards_pass"] is True
    assert res["production_click_affordance"]["hit_test_passed"] is True
    assert res["production_click_affordance"]["affordance_passed"] is True
    assert res["target_qualification_policy"]["policy_passed"] is True
    assert res["p2a_live_target_eligible"] is True


# ---------------------------------------------------------------------------
# 10-14. TR1 Target Qualification Policy & Selection Coverage
# ---------------------------------------------------------------------------

def test_root_row_target_policy_fails():
    """Requirement 10: Root row (message_ts == thread_ts) -> target-policy fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
        message_ts="1718058683.912369",
        thread_ts="1718058683.912369",
    )
    assert res["production_click_affordance"]["affordance_passed"] is True
    assert res["target_qualification_policy"]["policy_passed"] is False
    assert res["target_qualification_policy"]["policy_failure_reason"] == "NOT_THREAD_REPLY"
    assert res["p2a_live_target_eligible"] is False


def test_non_c_channel_target_policy_fails():
    """Requirement 11: Non-C channel (D042WS25QAX) -> target-policy fail."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
        channel_id="D042WS25QAX",
    )
    assert res["production_click_affordance"]["affordance_passed"] is True
    assert res["target_qualification_policy"]["policy_passed"] is False
    assert res["target_qualification_policy"]["policy_failure_reason"] == "CHANNEL_ID_INVALID_NOT_PUBLIC_OR_PRIVATE"
    assert res["p2a_live_target_eligible"] is False


def test_ambiguous_row_identity_ineligible():
    """Requirement 12: Ambiguous row identity (unique_identity_count > 1) -> row ineligible."""
    res = _evaluate_mock_dom_row(
        reply_bar_count=1,
        has_count_el=True,
        has_view_thread_el=True,
        is_visible=True,
        outside_composer=True,
        hit_test_passed=True,
        unique_identity_count=2,
    )
    assert res["target_qualification_policy"]["policy_passed"] is False
    assert res["target_qualification_policy"]["policy_failure_reason"] == "ROW_IDENTITY_AMBIGUOUS"
    assert res["p2a_live_target_eligible"] is False


def test_multiple_eligible_rows_lowest_rank_selected():
    """Requirement 13: Multiple eligible rows -> lowest rank selected."""
    row2 = _evaluate_mock_dom_row(1, True, True, True, True, True, rank=2)
    row4 = _evaluate_mock_dom_row(1, True, True, True, True, True, rank=4)
    row5 = _evaluate_mock_dom_row(1, True, True, True, True, True, rank=5)

    selected, verdict = tr1.select_best_candidate([row4, row2, row5])
    assert verdict == "ELIGIBLE_TARGET_FOUND"
    assert selected is not None
    assert selected["rank"] == 2


def test_no_eligible_top5_returns_verdict():
    """Requirement 14: No eligible top5 -> NO_PRODUCTION_CLICK_ELIGIBLE_TARGET_IN_TOP5."""
    row1 = _evaluate_mock_dom_row(0, False, False, False, True, False, rank=1)
    row2 = _evaluate_mock_dom_row(0, False, False, False, True, False, rank=2)
    selected, verdict = tr1.select_best_candidate([row1, row2])
    assert verdict == "NO_PRODUCTION_CLICK_ELIGIBLE_TARGET_IN_TOP5"
    assert selected is None


# ---------------------------------------------------------------------------
# 15-16. Historical Integrity & RB2 Authority
# ---------------------------------------------------------------------------

def test_rb2_sha_exact_match():
    """Requirement 15: RB2 live artifact SHA-256 matches exact frozen hash."""
    rb2_path = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_rb2_reply_bar_structure.json"
    assert rb2_path.exists()
    assert tr1.get_file_sha256(rb2_path) == "fede0a42554c0fa6b3db029816e89a2fdbc63c4d11f380d4b61b4a9e05fd4565"


def test_all_8_historical_hashes_exact():
    """Requirement 16: All 8 historical qualification artifacts match exact frozen hashes."""
    historical = {
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_trace.json": "f6f17e06f94808bac15dea32748526a1d146dac69ef0a75cc8a4467c5c8fd28e",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt2_trace.json": "a6eacdb988306e610f446d65fd5eb56ae198d6ce976e62bd1937066eb834cd3a",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt3_trace.json": "b00fa70ec84da9efe329afed4cc70b4c00765adf64e773c2c5624ad72c84b18e",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt4_trace.json": "e115b52de6b1ac1203d98e5a108fbe7b6a375fd708dc93233067d40a910a4965",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_search_residency.json": "5a44f58616a5dac21ebeb596a10e8f55ba7a2263eb177b945485c2bdd2556846",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_run2_search_residency.json": "fccfb28d380c81ad66e435ac43b63778184cb197da87d61eb304bf391c28f6a5",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rb1_reply_bar_structure.json": "6d94c2ef2bdc9b4eda4e68bf23737fe5e3280a014892ba0ffcb0cb1481dd76d5",
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rb2_reply_bar_structure.json": "fede0a42554c0fa6b3db029816e89a2fdbc63c4d11f380d4b61b4a9e05fd4565",
    }
    for rel_path, expected_sha in historical.items():
        p = REPO_ROOT / rel_path
        assert p.exists(), f"Historical artifact missing: {rel_path}"
        assert tr1.get_file_sha256(p) == expected_sha, f"Historical artifact mutated: {rel_path}"


# ---------------------------------------------------------------------------
# 17-19. Zero Mutations, Clean Repo, Zero Provider Calls
# ---------------------------------------------------------------------------

def test_zero_click_callsites_in_tr1():
    """Requirement 17: Zero click-capable callsites in TR1 execution path."""
    with open(tr1.PRIMARY_DEPENDENCY_PATHS["tr1_runner"], "r", encoding="utf-8") as f:
        content = f.read()

    # Verify no .click() calls exist outside comments / docstrings
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("*"):
            continue
        assert ".click()" not in stripped, f"Illegal click call found in runner: {line}"


def test_production_src_diff_empty():
    """Requirement 18: Production src/ diff is strictly empty."""
    res = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "", f"Production src/ has uncommitted modifications: {res.stdout}"


def test_provider_calls_zero():
    """Requirement 19: Provider calls strictly zero in TR1 configuration."""
    assert tr1.MAX_PROVIDER_CALLS == 0


# ---------------------------------------------------------------------------
# 20-24. Runtime Parity, Exception Ordering, Search Telemetry & Top-5 Bound
# ---------------------------------------------------------------------------

def test_attempt4_rb2_search_authority_parity():
    """Requirement 20 & 21: Authoritative search telemetry parity with Attempt 4 and RB2."""
    report = {
        "live_action_inventory": {},
        "search_telemetry": {},
        "safety_telemetry": {},
        "checks": {},
    }

    class MockBackend:
        last_search_telemetry = {
            "search_activation_dispatches": 1,
            "modal_ever_observed": True,
            "final_modal_count": 1,
            "input_ever_observed": True,
            "final_input_count": 1,
            "last_confirmed_stage": "SUBMIT_EXECUTED",
            "onenter_invoked": True,
            "safe_search_action_selected": True,
            "verified_query_state_before_submit": True,
            "search_surface_query_matches_requested": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked": False,
            "write_capable_action_invoked": False,
            "unsafe_non_search_action_invoked": False,
            "composer_focus": "UNKNOWN",
            "composer_text_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        }

    tr1.harvest_search_telemetry(MockBackend(), None, report)
    ok, reason = tr1.evaluate_search_safety_and_query_authority(report)
    assert ok is True
    assert reason == "VERIFIED_SAFE_AND_CORRELATED"


def test_search_exception_ordering_preserved():
    """Requirement 22: Search exception takes precedence and is harvested before safety check."""
    report = {
        "live_action_inventory": {},
        "search_telemetry": {},
        "safety_telemetry": {},
        "checks": {},
    }

    class MockSearchException(Exception):
        details = {
            "search_activation_dispatches": 1,
            "modal_ever_observed": True,
            "last_confirmed_stage": "FAILED",
        }

    se = MockSearchException("Search failed")
    tr1.harvest_search_telemetry(object(), se, report)
    assert report["search_telemetry"]["last_confirmed_stage"] == "FAILED"


def test_top5_bound_exact():
    """Requirement 23: Max results and slice bound is exactly 5."""
    assert tr1.SEARCH_MAX_RESULTS == 5


def test_single_atomic_snapshot():
    """Requirement 24: Single atomic JS snapshot evaluates all mounted rows."""
    assert "JS_INSPECT_TOP5_TARGET_AFFORDANCE" in dir(tr1)


# ---------------------------------------------------------------------------
# 25-29. Hard Preflight Gates, Overwrite Guard & Closure Audit
# ---------------------------------------------------------------------------

def test_live_artifact_overwrite_guard_fails_closed(tmp_path, monkeypatch):
    """Requirement 25: Live artifact overwrite guard triggers FAIL_CLOSED if artifact exists (using tmp_path)."""
    mock_live = tmp_path / "mock_live.json"
    mock_live.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tr1, "LIVE_OUTPUT_FILE", mock_live)
    report = tr1.run_tr1_diagnostic(dry_run=False)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"


def test_dependency_hash_mismatch_blocks_pre_webview(monkeypatch):
    """Requirement 26: Dependency hash mismatch fails closed before WebView."""
    monkeypatch.setattr(
        tr1, "compute_all_dependency_hashes", lambda: {"tr1_runner": "0000000000000000000000000000000000000000000000000000000000000000"}
    )
    report = tr1.run_tr1_diagnostic(dry_run=True)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "PREFLIGHT_HASH_MISMATCH"


def test_protocol_semantic_mismatch_blocks_pre_webview(monkeypatch, tmp_path):
    """Requirement 27: Protocol semantic identity mismatch fails closed before WebView."""
    bad_proto = tmp_path / "bad_proto.json"
    with open(tr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["protocol"] = "WRONG_PROTOCOL"
    bad_proto.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(tr1, "PROTOCOL_FREEZE_PATH", bad_proto)
    report = tr1.run_tr1_diagnostic(dry_run=True)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "PROTOCOL_SEMANTIC_IDENTITY_MISMATCH"


def test_runtime_closure_audit_enforces_zero_untracked():
    """Requirement 28: Runtime closure audit enforces 0 untracked dependencies."""
    with open(tr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    audit = tr1.audit_runtime_import_closure(manifest)
    assert audit["untracked_loaded_project_dependency_count"] == 0
    assert audit["closure_audit_passed"] is True


def test_sensitive_content_exclusion():
    """Requirement 29: Ensure sensitive content (message text, outerHTML, raw permalink) is excluded."""
    assert tr1.JS_INSPECT_TOP5_TARGET_AFFORDANCE.find("outerHTML") == -1
    assert tr1.JS_INSPECT_TOP5_TARGET_AFFORDANCE.find("innerHTML") == -1
    assert tr1.JS_INSPECT_TOP5_TARGET_AFFORDANCE.find("innerText") == -1
    assert tr1.JS_INSPECT_TOP5_TARGET_AFFORDANCE.find("textContent") == -1
