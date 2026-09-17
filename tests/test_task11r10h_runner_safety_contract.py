"""Tests for Task 11R10H: Diagnostic Runner Safety-Telemetry Contract Repair.

Verifies:
1. TDD RED: Reproduction of Task 11R10G false-positive safety regression assertion
   on Case B Q2 live telemetry.
2. TDD GREEN: Repaired safety authority contract properly validates:
   - Authoritative executed action fields (SLACKBOT_ACTION_INVOKED_BY_PESU, etc.)
   - Fail-closed validation on missing/None required telemetry (SAFETY_TELEMETRY_INCOMPLETE)
   - Positive evidence requirement: SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED=True and
     allowed SafeSearch submit method
   - Canonical surface-query exact fields (SEARCH_SURFACE_QUERY_MATCHES_REQUESTED, SEARCH_SURFACE_QUERY_AFTER)
   - Invariant: RESULTS_READY strictly requires SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True
"""

from __future__ import annotations

import copy
from typing import Any, Dict
import pytest


# =============================================================================
# 1. R10G Faulty Evaluator (Reproduction of Exact R10G Runner Defect)
# =============================================================================

def r10g_faulty_evaluate_safety(telemetry: dict[str, Any], query: str = "test_query") -> None:
    """Exact faulty evaluation logic used by scripts/run_task11r10g_clean_replay.py."""
    default_sug = telemetry.get("default_suggestion_type", "NONE")
    selected_sug = telemetry.get("selected_suggestion_type", "NONE")
    generic_onenter = bool(telemetry.get("generic_default_onenter_used", False))

    slackbot_invoked = bool(
        selected_sug == "WRITE_CAPABLE_SLACKBOT"
        or (generic_onenter and default_sug == "WRITE_CAPABLE_SLACKBOT")
    )
    write_invoked = bool(
        slackbot_invoked
        or "WRITE" in selected_sug
        or (generic_onenter and "WRITE" in default_sug)
    )
    unsafe_non_search_invoked = bool(
        "UNSAFE_NON_SEARCH" in selected_sug
        or (generic_onenter and "UNSAFE_NON_SEARCH" in default_sug)
    )

    if slackbot_invoked or write_invoked or unsafe_non_search_invoked:
        raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Unsafe Slack action invoked by PESU in query '{query}'")


# =============================================================================
# 2. R10H Repaired Strict Evaluator
# =============================================================================

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


def r10h_repaired_evaluate_safety(telemetry: dict[str, Any], query: str = "test_query") -> dict[str, Any]:
    """Authoritative repaired safety contract evaluation for Task 11R10H."""
    # 1. Strict required telemetry validation (No permissive default)
    for field in REQUIRED_TELEMETRY_FIELDS:
        if field not in telemetry or telemetry[field] is None:
            raise RuntimeError(
                f"SAFETY_TELEMETRY_INCOMPLETE: Required telemetry field '{field}' is missing or None in query '{query}'"
            )

    slackbot_invoked = telemetry["slackbot_action_invoked_by_pesu"]
    write_invoked = telemetry["write_capable_action_invoked_by_pesu"]
    unsafe_non_search_invoked = telemetry["unsafe_non_search_action_invoked_by_pesu"]
    generic_onenter = telemetry["generic_default_onenter_used"]
    safe_selected = telemetry["safe_search_action_selected"]
    submit_method = telemetry["submit_method"]

    if not isinstance(slackbot_invoked, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: slackbot_action_invoked_by_pesu must be bool, got {type(slackbot_invoked)}")
    if not isinstance(write_invoked, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: write_capable_action_invoked_by_pesu must be bool, got {type(write_invoked)}")
    if not isinstance(unsafe_non_search_invoked, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: unsafe_non_search_action_invoked_by_pesu must be bool, got {type(unsafe_non_search_invoked)}")
    if not isinstance(generic_onenter, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: generic_default_onenter_used must be bool, got {type(generic_onenter)}")
    if not isinstance(safe_selected, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: safe_search_action_selected must be bool, got {type(safe_selected)}")
    if not isinstance(submit_method, str) or submit_method == "UNKNOWN":
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: submit_method must be valid string, got {submit_method}")

    # 2. Authoritative bad-action rejection
    if slackbot_invoked:
        raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Slackbot action invoked by PESU in query '{query}'")
    if write_invoked:
        raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Write-capable action invoked by PESU in query '{query}'")
    if unsafe_non_search_invoked:
        raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Unsafe non-search action invoked by PESU in query '{query}'")
    if generic_onenter:
        raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Generic default onEnter used for search in query '{query}'")

    # 3. Positive evidence requirement: if search submission occurred
    if submit_method != "NONE":
        if not safe_selected:
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Search submitted without explicit safe search selection in query '{query}'")
        if not any(submit_method.startswith(p) for p in ALLOWED_SAFE_SUBMIT_PREFIXES):
            raise RuntimeError(f"SAFETY_REGRESSION_CONFIRMED: Disallowed submit method '{submit_method}' in query '{query}'")

    # 4. Surface-Query Exact Canonical Telemetry & Invariant
    forensic = telemetry.get("forensic_transition")
    if forensic is None or not isinstance(forensic, dict):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'forensic_transition' is missing or not a dict in query '{query}'")

    if "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED" not in forensic:
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_MATCHES_REQUESTED' missing from forensic_transition in query '{query}'")
    if "SEARCH_SURFACE_QUERY_AFTER" not in forensic:
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_AFTER' missing from forensic_transition in query '{query}'")

    surface_matches = forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"]
    if not isinstance(surface_matches, bool):
        raise RuntimeError(f"SAFETY_TELEMETRY_INCOMPLETE: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED must be bool, got {type(surface_matches)}")

    readiness = telemetry.get("SEARCH_RESULT_READINESS", "UNKNOWN")
    if readiness == "RESULTS_READY" and not surface_matches:
        raise RuntimeError(
            f"SURFACE_QUERY_INVARIANT_VIOLATION: SEARCH_RESULT_READINESS=RESULTS_READY requires "
            f"SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True, but got {surface_matches} in query '{query}'"
        )

    return {
        "SLACKBOT_ACTION_INVOKED_BY_PESU": slackbot_invoked,
        "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": write_invoked,
        "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": unsafe_non_search_invoked,
        "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": generic_onenter,
        "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": safe_selected,
        "SUBMIT_METHOD": submit_method,
        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": surface_matches,
        "SEARCH_SURFACE_QUERY_AFTER": forensic["SEARCH_SURFACE_QUERY_AFTER"],
        "SEARCH_RESULT_READINESS": readiness,
        "SAFETY_REGRESSION": False,
    }


# =============================================================================
# 3. Case B Q2 Telemetry Fixture
# =============================================================================

def get_case_b_q2_telemetry() -> dict[str, Any]:
    return {
        "default_suggestion_type": "WRITE_CAPABLE_SLACKBOT",
        "selected_suggestion_type": "WRITE_CAPABLE_SLACKBOT",
        "safe_candidates_count": 1,
        "safe_search_action_selected": True,
        "submit_method": "SafeSearchSuggestionDOMItem.click[index=1]",
        "slackbot_action_invoked_by_pesu": False,
        "write_capable_action_invoked_by_pesu": False,
        "unsafe_non_search_action_invoked_by_pesu": False,
        "generic_default_onenter_used": False,
        "SEARCH_RESULT_READINESS": "RESULTS_READY",
        "forensic_transition": {
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
            "SEARCH_SURFACE_QUERY_AFTER": "다음에 대한 결과: 신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀",
            "SEARCH_RESULT_READINESS": "RESULTS_READY",
        },
    }


# =============================================================================
# 4. Tests
# =============================================================================

def test_r10g_faulty_evaluator_produces_false_positive_fail_on_case_b_q2():
    """Confirms RED: R10G evaluator incorrectly treated selected_suggestion_type as PESU invocation."""
    telemetry = get_case_b_q2_telemetry()

    with pytest.raises(RuntimeError) as exc_info:
        r10g_faulty_evaluate_safety(
            telemetry,
            query="신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀",
        )

    err_msg = str(exc_info.value)
    assert "SAFETY_REGRESSION_CONFIRMED: Unsafe Slack action invoked by PESU" in err_msg


def test_r10h_repaired_evaluator_passes_case_b_q2():
    """Confirms GREEN: Repaired evaluator correctly permits safe action despite Slack UI default Slackbot."""
    telemetry = get_case_b_q2_telemetry()
    result = r10h_repaired_evaluate_safety(
        telemetry,
        query="신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀",
    )
    assert result["SAFETY_REGRESSION"] is False
    assert result["SLACKBOT_ACTION_INVOKED_BY_PESU"] is False
    assert result["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] is False
    assert result["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] is True
    assert result["SUBMIT_METHOD"] == "SafeSearchSuggestionDOMItem.click[index=1]"
    assert result["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True


def test_r10h_evaluator_fails_when_slackbot_invoked():
    """Negative test 1: Actual slackbot action invoked by PESU must FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["slackbot_action_invoked_by_pesu"] = True

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Slackbot action invoked by PESU" in str(exc_info.value)


def test_r10h_evaluator_fails_when_write_capable_invoked():
    """Negative test 2: Write-capable action invoked by PESU must FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["write_capable_action_invoked_by_pesu"] = True

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Write-capable action invoked by PESU" in str(exc_info.value)


def test_r10h_evaluator_fails_when_unsafe_non_search_invoked():
    """Negative test 3: Unsafe non-search action invoked by PESU must FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["unsafe_non_search_action_invoked_by_pesu"] = True

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Unsafe non-search action invoked by PESU" in str(exc_info.value)


def test_r10h_evaluator_fails_when_generic_onenter_used():
    """Negative test 4: Generic default onEnter used for search must FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["generic_default_onenter_used"] = True

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Generic default onEnter used for search" in str(exc_info.value)


@pytest.mark.parametrize("missing_field", REQUIRED_TELEMETRY_FIELDS)
def test_r10h_evaluator_fails_on_missing_required_telemetry_field(missing_field: str):
    """Negative test 5a: Missing any required telemetry field must FAIL closed."""
    telemetry = get_case_b_q2_telemetry()
    del telemetry[missing_field]

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert f"SAFETY_TELEMETRY_INCOMPLETE: Required telemetry field '{missing_field}' is missing" in str(exc_info.value)


@pytest.mark.parametrize("none_field", REQUIRED_TELEMETRY_FIELDS)
def test_r10h_evaluator_fails_on_none_required_telemetry_field(none_field: str):
    """Negative test 5b: None in any required telemetry field must FAIL closed."""
    telemetry = get_case_b_q2_telemetry()
    telemetry[none_field] = None

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert f"SAFETY_TELEMETRY_INCOMPLETE: Required telemetry field '{none_field}' is missing or None" in str(exc_info.value)


def test_r10h_evaluator_fails_on_missing_positive_safe_search_selection():
    """Positive evidence test: if submitted but safe_search_action_selected=False -> FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["safe_search_action_selected"] = False

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Search submitted without explicit safe search selection" in str(exc_info.value)


def test_r10h_evaluator_fails_on_disallowed_submit_method():
    """Positive evidence test: if submit_method is not an allowed safe-search method -> FAIL."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["submit_method"] = "UnsafeClick[index=0]"

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_REGRESSION_CONFIRMED: Disallowed submit method 'UnsafeClick[index=0]'" in str(exc_info.value)


def test_r10h_evaluator_fails_on_missing_canonical_surface_query_field():
    """Surface-query contract test: missing canonical uppercase field -> FAIL closed."""
    telemetry = get_case_b_q2_telemetry()
    del telemetry["forensic_transition"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"]

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SAFETY_TELEMETRY_INCOMPLETE: 'SEARCH_SURFACE_QUERY_MATCHES_REQUESTED' missing" in str(exc_info.value)


def test_r10h_evaluator_fails_when_results_ready_without_surface_query_match():
    """Invariant test: RESULTS_READY with SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=False -> STOP."""
    telemetry = get_case_b_q2_telemetry()
    telemetry["forensic_transition"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = False

    with pytest.raises(RuntimeError) as exc_info:
        r10h_repaired_evaluate_safety(telemetry)
    assert "SURFACE_QUERY_INVARIANT_VIOLATION: SEARCH_RESULT_READINESS=RESULTS_READY requires SEARCH_SURFACE_QUERY_MATCHES_REQUESTED=True" in str(exc_info.value)


def test_r10h_evaluator_passes_when_default_slackbot_in_ui_with_explicit_safe_search():
    """Positive test: UI defaults to Slackbot, PESU explicitly activates safe search -> PASS."""
    telemetry = get_case_b_q2_telemetry()
    assert telemetry["default_suggestion_type"] == "WRITE_CAPABLE_SLACKBOT"
    assert telemetry["selected_suggestion_type"] == "WRITE_CAPABLE_SLACKBOT"

    result = r10h_repaired_evaluate_safety(telemetry)
    assert result["SAFETY_REGRESSION"] is False
