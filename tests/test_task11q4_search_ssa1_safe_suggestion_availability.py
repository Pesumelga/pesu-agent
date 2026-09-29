"""Unit, Mock, and Safety Contract Tests for SSA1 Safe Search Suggestion Availability.

Phase 13.5D / Task 11 / Search Safety / SSA1

Validates:
1. SAFE_SEARCH_SUGGESTION_OBSERVED: safe candidate present immediately at poll 1.
2. SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY: safe candidate appears at poll > 1.
3. ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED: only channel nav candidates present.
4. Multiple unsafe candidates (channel, member, slackbot) with zero safe candidates.
5. SUGGESTION_STRUCTURE_UNRECOGNIZED: unknown candidate formats only.
6. NO_SUGGESTION_SURFACE_OBSERVED: suggestion surface absent across all polls.
7. QUERY_STATE_NOT_VERIFIED: query state sync mismatch.
8. Safe + unsafe candidates together: verified counts and summaries.
9. Candidate ordering changes between polls: robust tracking.
10. Safe candidate not default (e.g. index 1) but still detected.
11. Zero submit contract: static audit proves 0 onEnter, 0 onAccept, 0 submit.
12. Zero interaction contract: static audit proves 0 clicks, 0 keys, 0 navigation.
13. Action invariants: 0 submissions, 0 clicks, 0 nav, 0 scroll, 0 provider, 0 retry.
14. Sanitization: ensures zero raw URLs, permalinks, or text.
15. End-to-end dry run: completes with CANONICAL_DRY_RUN_VERDICT.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1 import (
    classify_ssa1_terminal_state,
    sanitize_report_for_persistence,
    verify_static_zero_submit_contract,
    verify_static_zero_interaction_contract,
    verify_runtime_api_signature_parity,
    run_ssa1_qualification,
    build_mock_dry_run_observation_polls,
    CANONICAL_DRY_RUN_VERDICT,
    MAX_SUGGESTION_OBSERVATION_POLLS,
    POLL_INTERVAL_MS,
    MAX_SEARCH_SUBMISSIONS,
    MAX_SUGGESTION_CLICKS,
    MAX_ENTER_KEY_EVENTS,
    MAX_SCROLL_ACTIONS,
    MAX_NAVIGATION_ACTIONS,
    MAX_PROVIDER_CALLS,
    MAX_RETRIES,
)


def test_ssa1_safe_candidate_present_immediately():
    """Safe candidate is present at poll 1 -> SAFE_SEARCH_SUGGESTION_OBSERVED."""
    polls = build_mock_dry_run_observation_polls(scenario="safe_immediate")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=True)

    assert term_cls == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert details["first_safe_candidate_poll_index"] == 1
    assert details["final_safe_candidate_count"] == 1
    assert details["final_connected_candidate_count"] == 2
    assert details["final_default_candidate_classification"] == "SAFE_EXPLICIT_SEARCH"


def test_ssa1_safe_candidate_appears_after_bounded_delay():
    """Safe candidate absent at poll 1-2, appears at poll 3 -> SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY."""
    polls = build_mock_dry_run_observation_polls(scenario="safe_delayed")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=True)

    assert term_cls == "SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY"
    assert details["first_safe_candidate_poll_index"] == 3
    assert details["final_safe_candidate_count"] == 1
    assert details["final_connected_candidate_count"] == 2


def test_ssa1_only_channel_nav_unsafe_candidates():
    """All polls have only channel nav candidates -> ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED."""
    polls = build_mock_dry_run_observation_polls(scenario="only_channel_nav")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=True)

    assert term_cls == "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED"
    assert details["first_safe_candidate_poll_index"] is None
    assert details["final_safe_candidate_count"] == 0
    assert details["final_connected_candidate_count"] == 3
    assert details["final_default_candidate_classification"] == "UNSAFE_NON_SEARCH_CHANNEL_NAV"


def test_ssa1_multiple_unsafe_candidates_no_safe_candidate():
    """Candidates contain channel, member, and slackbot, but 0 safe candidates."""
    poll = {
        "poll_index": 1,
        "elapsed_ms": 200,
        "search_input_query_matches_requested": True,
        "suggestion_surface_present": True,
        "connected_candidate_count": 3,
        "safe_candidate_count": 0,
        "unsafe_candidate_count": 3,
        "candidate_classifications": [
            "UNSAFE_WRITE_CAPABLE_SLACKBOT",
            "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "UNSAFE_PERSON_NAV",
        ],
        "default_candidate_classification": "UNSAFE_WRITE_CAPABLE_SLACKBOT",
        "selected_candidate_classification": "UNSAFE_WRITE_CAPABLE_SLACKBOT",
        "explicit_search_candidate_present": False,
        "safe_search_candidate_present": False,
    }
    term_cls, details = classify_ssa1_terminal_state([poll], query_state_all_synced=True)

    assert term_cls == "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED"
    assert details["first_safe_candidate_poll_index"] is None
    assert details["final_safe_candidate_count"] == 0


def test_ssa1_unknown_candidate_only():
    """Unrecognized candidate format only -> SUGGESTION_STRUCTURE_UNRECOGNIZED."""
    polls = build_mock_dry_run_observation_polls(scenario="unrecognized")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=True)

    assert term_cls == "SUGGESTION_STRUCTURE_UNRECOGNIZED"
    assert details["first_safe_candidate_poll_index"] is None


def test_ssa1_suggestion_surface_absent():
    """Suggestion surface absent across all polls -> NO_SUGGESTION_SURFACE_OBSERVED."""
    polls = build_mock_dry_run_observation_polls(scenario="no_surface")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=True)

    assert term_cls == "NO_SUGGESTION_SURFACE_OBSERVED"
    assert details["first_safe_candidate_poll_index"] is None
    assert details["final_connected_candidate_count"] == 0


def test_ssa1_query_state_mismatch():
    """Query state not synced across surfaces -> QUERY_STATE_NOT_VERIFIED."""
    polls = build_mock_dry_run_observation_polls(scenario="safe_immediate")
    term_cls, details = classify_ssa1_terminal_state(polls, query_state_all_synced=False)

    assert term_cls == "QUERY_STATE_NOT_VERIFIED"
    assert details["query_state_all_synced"] is False


def test_ssa1_safe_and_unsafe_candidates_together():
    """Safe search and channel nav candidates coexist in observation."""
    polls = build_mock_dry_run_observation_polls(scenario="safe_immediate")
    p1 = polls[0]

    assert p1["safe_candidate_count"] == 1
    assert p1["unsafe_candidate_count"] == 1
    assert p1["explicit_search_candidate_present"] is True
    assert p1["safe_search_candidate_present"] is True
    assert len(p1["candidate_summaries"]) == 2
    assert p1["candidate_summaries"][0]["is_safe_search"] is True
    assert p1["candidate_summaries"][1]["is_channel_nav"] is True


def test_ssa1_candidate_ordering_changes_between_polls():
    """Candidate ordering changes between poll 1 and poll 2."""
    poll1 = {
        "poll_index": 1,
        "elapsed_ms": 200,
        "search_input_query_matches_requested": True,
        "suggestion_surface_present": True,
        "connected_candidate_count": 2,
        "safe_candidate_count": 1,
        "unsafe_candidate_count": 1,
        "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "SAFE_EXPLICIT_SEARCH"],
        "default_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
        "selected_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
        "explicit_search_candidate_present": True,
        "safe_search_candidate_present": True,
    }
    poll2 = {
        "poll_index": 2,
        "elapsed_ms": 400,
        "search_input_query_matches_requested": True,
        "suggestion_surface_present": True,
        "connected_candidate_count": 2,
        "safe_candidate_count": 1,
        "unsafe_candidate_count": 1,
        "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_NON_SEARCH_CHANNEL_NAV"],
        "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
        "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
        "explicit_search_candidate_present": True,
        "safe_search_candidate_present": True,
    }
    term_cls, details = classify_ssa1_terminal_state([poll1, poll2], query_state_all_synced=True)

    assert term_cls == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert details["first_safe_candidate_poll_index"] == 1
    assert details["final_default_candidate_classification"] == "SAFE_EXPLICIT_SEARCH"


def test_ssa1_safe_candidate_not_default_but_still_observed():
    """Safe candidate is at index 1 (not default at index 0), but still observed."""
    poll = {
        "poll_index": 1,
        "elapsed_ms": 200,
        "search_input_query_matches_requested": True,
        "suggestion_surface_present": True,
        "connected_candidate_count": 2,
        "safe_candidate_count": 1,
        "unsafe_candidate_count": 1,
        "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "SAFE_EXPLICIT_SEARCH"],
        "default_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
        "selected_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
        "explicit_search_candidate_present": True,
        "safe_search_candidate_present": True,
    }
    term_cls, details = classify_ssa1_terminal_state([poll], query_state_all_synced=True)

    assert term_cls == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert details["final_safe_candidate_count"] == 1
    assert details["final_default_candidate_classification"] == "UNSAFE_NON_SEARCH_CHANNEL_NAV"


def test_ssa1_static_zero_submit_contract():
    """Static AST audit guarantees 0 onEnter, 0 onAccept, 0 submit in observation JS."""
    assert verify_static_zero_submit_contract() is True


def test_ssa1_static_zero_interaction_contract():
    """Static AST audit guarantees 0 clicks, 0 keys, 0 mouse dispatches in runner."""
    assert verify_static_zero_interaction_contract() is True


def test_ssa1_action_invariants_and_zero_scroll_nav_provider():
    """Guarantees strictly zero submissions, clicks, scroll, navigation, and provider calls."""
    assert MAX_SEARCH_SUBMISSIONS == 0
    assert MAX_ENTER_KEY_EVENTS == 0
    assert MAX_SUGGESTION_CLICKS == 0
    assert MAX_NAVIGATION_ACTIONS == 0
    assert MAX_SCROLL_ACTIONS == 0
    assert MAX_PROVIDER_CALLS == 0
    assert MAX_RETRIES == 0


def test_ssa1_sanitization_removes_sensitive_content():
    """Sanitizer purges raw URLs, permalinks, and sensitive message texts."""
    raw = {
        "candidate": "Search for 26년 하반기 간담회",
        "raw_permalink": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469",
        "raw_url": "https://heumlabs.slack.com",
        "token": ('xox' + 'b-1234567890-abcdef'),
        "safe": True,
        "count": 3,
    }
    sanitized = sanitize_report_for_persistence(raw)

    assert "raw_permalink" not in sanitized
    assert "raw_url" not in sanitized
    assert "token" not in sanitized
    assert sanitized["safe"] is True
    assert sanitized["count"] == 3


def test_ssa1_dry_run_end_to_end():
    """Executes full offline dry-run of SSA1 runner and validates output structure."""
    report = run_ssa1_qualification(dry_run=True, timeout=10.0)

    assert report["verdict"] == CANONICAL_DRY_RUN_VERDICT
    assert report["diagnostic_verdict"] == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert report["terminal_classification"] == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert report["live_action_inventory"]["search_submissions"] == 0
    assert report["live_action_inventory"]["enter_key_events"] == 0
    assert report["live_action_inventory"]["suggestion_clicks"] == 0
    assert report["live_action_inventory"]["navigation_actions"] == 0
    assert report["live_action_inventory"]["scroll_actions"] == 0
    assert report["checks"]["query_entry_executed"] is True
    assert report["checks"]["query_state_all_synced"] is True
    assert report["checks"]["bounded_observation_executed"] is True
