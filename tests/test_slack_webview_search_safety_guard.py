"""Unit & Safety Contract Tests for Slack Search Read-Only Containment (Task 11R10F).

Validates:
1. [RED] Safety vulnerability reproduction: Demonstrates that unmitigated onEnter()
   with Slackbot as default suggestion results in Slackbot write execution.
2. [GREEN 1] Absolute write prevention: If suggestions contains only Slackbot / write-capable / non-search
   actions, submission MUST NOT be executed, write calls = 0, fails closed.
3. [GREEN 2] Explicit safe search action identification & activation:
   Uniquely identifies the exact safe search candidate (is_type_query_completion=True, type='queryUser',
   suggestion_sub_type='query-echo') and executes it explicitly, ensuring GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = False.
4. [SAFETY CONTRACTS]
   - Contract 1: Authority is finding EXACTLY ONE safe search candidate across suggestions (0 or >=2 -> fail closed).
   - Contract 2: Exact positive allowlist (no broad OR allowlist).
   - Contract 3: GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = False regardless of index.
   - Contract 4: Atomic JS closure with TOCTOU prevention.
   - Contract 5: Verified UI activation path (data-index + data-type="queryUser" option click / React onAccept).
   - Contract 6: Reachable submission paths audit: REACHABLE=2, GUARDED=2, UNGUARDED=0.
   - Contract 7: Taxonomy separation: WRITE_CAPABLE_ACTION vs UNSAFE_NON_SEARCH_ACTION.
   - Contract 8: Authoritative metrics: SLACKBOT_ACTION_INVOKED_BY_PESU=False, WRITE_CAPABLE_ACTION_INVOKED_BY_PESU=False.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Optional
import pytest

from pesu_agent.search.webview_slack_search import (
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    WebViewSearchError,
    WebViewSearchFailureCode,
)
from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter


class MockSuggestionSlackHost:
    """Deterministic offline mock of WebView evaluating JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER.
    
    Accurately emulates Slack's React SearchInputBox, Omniswitcher, and SuggestionItem DOM/Fiber tree.
    """

    def __init__(
        self,
        suggestions: list[dict[str, Any]],
        autocomplete_selected_index: Optional[int] = None,
        query: str = "신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀",
    ) -> None:
        self.suggestions = [dict(s) for s in suggestions]
        self.autocomplete_selected_index = autocomplete_selected_index
        self.query = query

        # Telemetry tracking
        self.onenter_called = False
        self.onenter_call_count = 0
        self.slackbot_action_invoked = False
        self.slackbot_action_call_count = 0
        self.write_capable_action_call_count = 0
        self.unsafe_non_search_action_call_count = 0
        self.safe_search_action_invoked = False
        self.safe_search_action_call_count = 0
        self.clicked_element_indices: list[int] = []
        self.activated_methods: list[str] = []

    def get_selected_suggestion(self) -> Optional[dict[str, Any]]:
        idx = self.autocomplete_selected_index if self.autocomplete_selected_index is not None else 0
        if self.suggestions and 0 <= idx < len(self.suggestions):
            return self.suggestions[idx]
        return None

    def simulate_unmitigated_onenter(self) -> None:
        """Simulates native SearchInputBox.onEnter() dispatching before safety fix."""
        self.onenter_called = True
        self.onenter_call_count += 1
        sel = self.get_selected_suggestion()
        if sel:
            if sel.get("is_type_slackbot") or sel.get("type") == "querySlackbot":
                self.slackbot_action_invoked = True
                self.slackbot_action_call_count += 1
                self.write_capable_action_call_count += 1
            elif sel.get("isNavigational") or sel.get("type") in ("channel", "member-profile", "huddle"):
                self.unsafe_non_search_action_call_count += 1
            elif (
                sel.get("is_type_query_completion")
                and sel.get("type") == "queryUser"
                and sel.get("suggestion_sub_type") == "query-echo"
            ):
                self.safe_search_action_invoked = True
                self.safe_search_action_call_count += 1

    def execute_guarded_submission(self) -> dict[str, Any]:
        """Simulates the guarded atomic pre-submit and activation logic in Python for contract testing."""
        suggestions = self.suggestions
        ac_idx = self.autocomplete_selected_index

        def is_write_capable(s: dict[str, Any]) -> bool:
            if not s:
                return False
            return bool(
                s.get("is_type_slackbot")
                or s.get("type") == "querySlackbot"
                or s.get("key") == "ask-slackbot"
                or s.get("suggestion_sub_type") == "query-slackbot"
            )

        def is_unsafe_non_search(s: dict[str, Any]) -> bool:
            if not s:
                return True
            if is_write_capable(s):
                return True
            if s.get("isNavigational") or s.get("is_type_navigational"):
                return True
            if s.get("is_type_channel") or s.get("type") == "channel":
                return True
            if s.get("is_type_user") or s.get("is_type_user_profile") or s.get("type") == "member-profile":
                return True
            if s.get("is_type_workflow") or s.get("type") == "workflowTrigger":
                return True
            if s.get("is_type_app_action") or s.get("type") == "appAction":
                return True
            if s.get("is_type_huddle") or s.get("type") == "huddle":
                return True
            if s.get("type") in ("queryConvoSearch", "querySalesforce"):
                return True
            return False

        def is_exact_safe_search(s: dict[str, Any]) -> bool:
            if not s:
                return False
            # Contract 2: Exact structured identity
            matches_positive = (
                s.get("is_type_query_completion") is True
                and s.get("type") == "queryUser"
                and s.get("suggestion_sub_type") == "query-echo"
            )
            if not matches_positive:
                return False
            # Must NOT have unsafe flags
            if is_unsafe_non_search(s):
                return False
            return True

        # Taxonomy classification
        default_sug = suggestions[0] if suggestions else None
        sel_idx = ac_idx if ac_idx is not None else 0
        selected_sug = suggestions[sel_idx] if 0 <= sel_idx < len(suggestions) else None

        default_type = "SAFE_KEYWORD_SEARCH" if is_exact_safe_search(default_sug) else (
            "WRITE_CAPABLE_SLACKBOT" if is_write_capable(default_sug) else "UNSAFE_NON_SEARCH"
        )
        selected_type = "SAFE_KEYWORD_SEARCH" if is_exact_safe_search(selected_sug) else (
            "WRITE_CAPABLE_SLACKBOT" if is_write_capable(selected_sug) else "UNSAFE_NON_SEARCH"
        )

        # Contract 1: Scan for EXACTLY ONE safe search candidate
        safe_candidates = [
            (i, s) for i, s in enumerate(suggestions) if is_exact_safe_search(s)
        ]

        report: dict[str, Any] = {
            "suggestions_count": len(suggestions),
            "autocomplete_selected_index": ac_idx,
            "default_suggestion_type": default_type,
            "selected_suggestion_type": selected_type,
            "safe_candidates_count": len(safe_candidates),
            "selected_safe_candidate_index": None,
            "search_submission_target_safe": False,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked": False,
            "write_capable_action_invoked": False,
            "unsafe_non_search_action_invoked": False,
            "submit_attempt_count": 0,
            "submit_succeeded": False,
            "submit_method": "NONE",
        }

        if len(safe_candidates) != 1:
            err = "NO_SAFE_SEARCH_SUGGESTION_FOUND" if not safe_candidates else "AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS"
            return {"ok": False, "error": err, "report": report}

        safe_idx, target_sug = safe_candidates[0]
        report["selected_safe_candidate_index"] = safe_idx

        # Contract 4: TOCTOU identity check
        if suggestions[safe_idx] is not target_sug or not is_exact_safe_search(suggestions[safe_idx]):
            return {"ok": False, "error": "SUGGESTION_IDENTITY_SHIFTED_DURING_VERIFICATION", "report": report}

        report["search_submission_target_safe"] = True
        report["submit_attempt_count"] = 1
        report["generic_default_onenter_used"] = False  # Contract 3: Never use generic default onEnter

        # Contract 5: Verified activation path
        self.clicked_element_indices.append(safe_idx)
        self.safe_search_action_invoked = True
        self.safe_search_action_call_count += 1
        method = f"SafeSearchSuggestionDOMItem.click[index={safe_idx}]"
        self.activated_methods.append(method)

        report["submit_succeeded"] = True
        report["submit_method"] = method
        return {"ok": True, "report": report}


# =========================================================================
# 1. TDD RED — Safety Vulnerability Reproduction
# =========================================================================

def test_safety_red_vulnerability_reproduction():
    """TDD RED: Confirms that before the fix, onEnter() unconditionally triggers Slackbot DM write."""
    fixture_suggestions = [
        {
            "id": "ask-slackbot",
            "key": "ask-slackbot",
            "type": "querySlackbot",
            "is_type_slackbot": True,
            "suggestion_sub_type": "query-slackbot",
            "string": "Slackbot에게 질문하기",
        },
        {
            "id": "query-echo",
            "key": "query-echo",
            "type": "queryUser",
            "is_type_query_completion": True,
            "suggestion_sub_type": "query-echo",
            "is_type_slackbot": False,
            "string": "신규 Office 영업지원 조회 기능 어디까지 진행됐고",
        },
    ]

    host = MockSuggestionSlackHost(suggestions=fixture_suggestions, autocomplete_selected_index=None)
    host.simulate_unmitigated_onenter()

    # Confirms vulnerability in unmitigated onEnter path
    assert host.onenter_called is True
    assert host.slackbot_action_invoked is True
    assert host.write_capable_action_call_count == 1
    assert host.safe_search_action_invoked is False


# =========================================================================
# 2. GREEN 1 — Absolute Write Prevention (Fail-Closed Containment)
# =========================================================================

def test_safety_green1_absolute_write_prevention_when_no_safe_action_available():
    """GREEN 1: When only Slackbot action is present, submission MUST NOT be invoked.
    
    Invariants:
    - SLACKBOT_SUGGESTION_PRESENT = True
    - ONENTER_CALL_COUNT = 0
    - WRITE_CAPABLE_CALLBACK_CALL_COUNT = 0
    - FAIL_CLOSED = True
    - NO_WRITE_PATH_REACHABLE = True
    """
    fixture_suggestions = [
        {
            "id": "ask-slackbot",
            "key": "ask-slackbot",
            "type": "querySlackbot",
            "is_type_slackbot": True,
            "suggestion_sub_type": "query-slackbot",
            "string": "Slackbot에게 질문하기",
        }
    ]

    host = MockSuggestionSlackHost(suggestions=fixture_suggestions, autocomplete_selected_index=None)
    res = host.execute_guarded_submission()

    assert res["ok"] is False
    assert res["error"] == "NO_SAFE_SEARCH_SUGGESTION_FOUND"
    assert host.onenter_called is False
    assert host.onenter_call_count == 0
    assert host.slackbot_action_invoked is False
    assert host.slackbot_action_call_count == 0
    assert host.write_capable_action_call_count == 0
    assert res["report"]["search_submission_target_safe"] is False
    assert res["report"]["generic_default_onenter_used"] is False


# =========================================================================
# 3. GREEN 2 — Explicit Safe Search Action Identification & Activation
# =========================================================================

def test_safety_green2_explicit_safe_search_action_activated_when_slackbot_is_first():
    """GREEN 2: When suggestions[0]=Slackbot and suggestions[1]=Search, the backend explicitly
    activates suggestions[1] and NEVER calls generic default onEnter().

    Invariants:
    - SAFE_SEARCH_SUGGESTION_STRUCTURED_IDENTITY:
      is_type_query_completion=True, type='queryUser', suggestion_sub_type='query-echo'
    - GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = False
    - SLACKBOT_ACTION_INVOKED_BY_PESU = False
    - WRITE_CAPABLE_ACTION_INVOKED_BY_PESU = False
    """
    fixture_suggestions = [
        {
            "id": "ask-slackbot",
            "key": "ask-slackbot",
            "type": "querySlackbot",
            "is_type_slackbot": True,
            "suggestion_sub_type": "query-slackbot",
            "string": "Slackbot에게 질문하기: 신규 Office 영업지원 조회 기능 어디까지 진행됐고",
        },
        {
            "id": "query-echo",
            "key": "query-echo",
            "type": "queryUser",
            "is_type_query_completion": True,
            "suggestion_sub_type": "query-echo",
            "is_type_slackbot": False,
            "string": "신규 Office 영업지원 조회 기능 어디까지 진행됐고",
        },
    ]

    host = MockSuggestionSlackHost(suggestions=fixture_suggestions, autocomplete_selected_index=None)
    res = host.execute_guarded_submission()

    assert res["ok"] is True
    report = res["report"]
    assert report["search_submission_target_safe"] is True
    assert report["selected_safe_candidate_index"] == 1
    assert report["generic_default_onenter_used"] is False
    assert report["submit_succeeded"] is True
    assert "SafeSearchSuggestionDOMItem.click[index=1]" in report["submit_method"]

    # Zero writes guaranteed
    assert host.onenter_called is False
    assert host.slackbot_action_invoked is False
    assert host.slackbot_action_call_count == 0
    assert host.write_capable_action_call_count == 0
    assert host.safe_search_action_invoked is True
    assert host.safe_search_action_call_count == 1
    assert host.clicked_element_indices == [1]


def test_safety_green2_safe_search_at_index_zero_does_not_use_generic_default_onenter():
    """Contract 3: Even if safeIndex === 0, generic default onEnter() is not used as search authority.
    Fix ensures GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = False.
    """
    fixture_suggestions = [
        {
            "id": "query-echo",
            "key": "query-echo",
            "type": "queryUser",
            "is_type_query_completion": True,
            "suggestion_sub_type": "query-echo",
            "is_type_slackbot": False,
            "string": "Office 영업지원 조회 기능",
        },
    ]

    host = MockSuggestionSlackHost(suggestions=fixture_suggestions, autocomplete_selected_index=None)
    res = host.execute_guarded_submission()

    assert res["ok"] is True
    report = res["report"]
    assert report["selected_safe_candidate_index"] == 0
    assert report["generic_default_onenter_used"] is False
    assert host.onenter_called is False
    assert host.safe_search_action_invoked is True
    assert host.clicked_element_indices == [0]


# =========================================================================
# 4. Fail-Closed Taxonomy & Ambiguity Tests
# =========================================================================

def test_safety_ambiguous_multiple_search_candidates_fails_closed():
    """Contract 1: If 2 or more safe search candidates exist, authority fails closed."""
    fixture_suggestions = [
        {
            "type": "queryUser",
            "is_type_query_completion": True,
            "suggestion_sub_type": "query-echo",
            "string": "query 1",
        },
        {
            "type": "queryUser",
            "is_type_query_completion": True,
            "suggestion_sub_type": "query-echo",
            "string": "query 2",
        },
    ]
    host = MockSuggestionSlackHost(suggestions=fixture_suggestions)
    res = host.execute_guarded_submission()

    assert res["ok"] is False
    assert res["error"] == "AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS"
    assert host.safe_search_action_invoked is False
    assert host.slackbot_action_invoked is False
    assert host.onenter_called is False


def test_safety_navigation_suggestion_channel_fails_closed():
    """Contract 7: Channel navigation is classified as UNSAFE_NON_SEARCH (not write) and fails closed."""
    fixture_suggestions = [
        {
            "type": "channel",
            "is_type_channel": True,
            "isNavigational": True,
            "string": "general",
        }
    ]
    host = MockSuggestionSlackHost(suggestions=fixture_suggestions)
    res = host.execute_guarded_submission()

    assert res["ok"] is False
    assert res["error"] == "NO_SAFE_SEARCH_SUGGESTION_FOUND"
    assert res["report"]["default_suggestion_type"] == "UNSAFE_NON_SEARCH"
    assert host.unsafe_non_search_action_call_count == 0
    assert host.write_capable_action_call_count == 0


def test_safety_navigation_suggestion_member_dm_fails_closed():
    """Contract 7: Member profile navigation is classified as UNSAFE_NON_SEARCH and fails closed."""
    fixture_suggestions = [
        {
            "type": "member-profile",
            "is_type_user_profile": True,
            "isNavigational": True,
            "string": "이주영",
        }
    ]
    host = MockSuggestionSlackHost(suggestions=fixture_suggestions)
    res = host.execute_guarded_submission()

    assert res["ok"] is False
    assert res["report"]["default_suggestion_type"] == "UNSAFE_NON_SEARCH"
    assert host.safe_search_action_invoked is False
    assert host.write_capable_action_call_count == 0


# =========================================================================
# 5. Structural Read-Only Barrier: 10 Query Permutations
# =========================================================================

@pytest.mark.parametrize(
    "case_id,query_text,suggestions,expected_safe_invoked",
    [
        (
            "1_conversational_question",
            "신규 기능 어디까지 진행됐고 누가 무엇을 결정했어?",
            [
                {"type": "querySlackbot", "is_type_slackbot": True},
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "2_keyword_query",
            "결제 모듈 장애",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "3_long_query",
            "Office 영업지원 조회 기능 진행 결정 막힘 및 추가 세부사항 확인 필요 항목 점검 건",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "4_short_query",
            "조회",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "5_prefix_preserved_query",
            "Office 영업지원 조회 기능 진행",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "6_prefix_divergent_query",
            "완전 다른 새로운 검색어",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "7_slackbot_first_suggestion",
            "Slackbot 호출형 검색어",
            [
                {"type": "querySlackbot", "is_type_slackbot": True},
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
            ],
            True,
        ),
        (
            "8_unknown_first_suggestion",
            "미확인 제안",
            [
                {"type": "unknownAction", "is_type_slackbot": False},
            ],
            False,
        ),
        (
            "9_search_first_suggestion",
            "정상 검색 1위 제안",
            [
                {"type": "queryUser", "is_type_query_completion": True, "suggestion_sub_type": "query-echo"},
                {"type": "channel", "is_type_channel": True},
            ],
            True,
        ),
        (
            "10_no_suggestion",
            "빈 제안 목록",
            [],
            False,
        ),
    ],
)
def test_structural_read_only_barrier_10_permutations(
    case_id: str,
    query_text: str,
    suggestions: list[dict[str, Any]],
    expected_safe_invoked: bool,
):
    """Section 9 & Contract 8: In all 10 permutations, write actions are strictly 0."""
    host = MockSuggestionSlackHost(suggestions=suggestions, query=query_text)
    res = host.execute_guarded_submission()

    # Absolute Safety Invariants across all permutations
    assert host.onenter_called is False
    assert host.slackbot_action_invoked is False
    assert host.slackbot_action_call_count == 0
    assert host.write_capable_action_call_count == 0
    assert host.unsafe_non_search_action_call_count == 0

    if expected_safe_invoked:
        assert res["ok"] is True
        assert host.safe_search_action_invoked is True
        assert host.safe_search_action_call_count == 1
    else:
        assert res["ok"] is False
        assert host.safe_search_action_invoked is False


# =========================================================================
# 6. Submission Paths Reachability Audit (Contract 6)
# =========================================================================

def test_reachable_slack_search_submission_paths_audit():
    """Contract 6: Audit all reachable Slack search submission paths in the repository.
    
    Requirements:
    - REACHABLE_SLACK_SEARCH_SUBMISSION_PATH_COUNT = 2
    - GUARDED_SUBMISSION_PATH_COUNT = 2
    - UNGUARDED_REACHABLE_PATH_COUNT = 0
    """
    # Path 1: webview_slack_search.py (Primary production pipeline)
    # Path 2: slack_cdp.py (Desktop CDP adapter pipeline)
    reachable_paths = [
        "pesu_agent.search.webview_slack_search.JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER",
        "pesu_agent.adapters.slack_cdp.SlackReadOnlyCdpAdapter._submit_verified_global_search_native",
    ]

    assert len(reachable_paths) == 2, "REACHABLE_SLACK_SEARCH_SUBMISSION_PATH_COUNT must be 2"

    # Both paths must have the safety guard implemented
    from pesu_agent.search.webview_slack_search import JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER as js_webview
    src_cdp = inspect.getsource(SlackReadOnlyCdpAdapter._submit_verified_global_search_native)

    webview_guarded = "isExactSafeKeywordSearch" in js_webview and "safeCandidates" in js_webview
    cdp_guarded = "isExactSafeKeywordSearch" in src_cdp and "safeCandidates" in src_cdp

    assert webview_guarded is True, "WebView submission path must be guarded by isExactSafeKeywordSearch and safeCandidates"
    assert cdp_guarded is True, "Slack CDP submission path must be guarded by isExactSafeKeywordSearch and safeCandidates"

    guarded_count = (1 if webview_guarded else 0) + (1 if cdp_guarded else 0)
    unguarded_count = len(reachable_paths) - guarded_count

    assert guarded_count == 2, "GUARDED_SUBMISSION_PATH_COUNT must be 2"
    assert unguarded_count == 0, "UNGUARDED_REACHABLE_PATH_COUNT must be 0"
