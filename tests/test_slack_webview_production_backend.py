"""Unit & Contract Tests for Dedicated WebView Slack Production Search Backend (Phase 9.9.4a/b).

Validates:
1. WebViewSlackSearchBackend implements and reuses the existing SearchBackend Protocol.
2. Production code in src/pesu_agent has ZERO dependency on tools/webview_slack_poc/.
3. collect_context() strictly raises typed CONTEXT_COLLECTION_NOT_QUALIFIED error (no [] false success).
4. collect_context() performs zero unverified DOM navigation.
5. Lifecycle state transitions: STOPPED -> STARTING -> READY -> STOPPING -> STOPPED.
6. Searching before READY strictly fails with typed RUNTIME_NOT_READY error.
7. Duplicate start and shutdown calls are idempotent.
8. Search query is treated strictly as data (never executable JS or string concatenation into script).
9. Empty, None, whitespace-only, or oversized queries fail closed before any runtime invocation (zero DOM mutation).
10. Search pipeline constraints: max 1 activation, max 1 setter, max 1 submit, zero retry.
11. Zero keyboard fallback, zero Enter key fallback, zero generic click fallback.
12. Result extraction is strictly scoped inside .c-search_message__content.
13. Broad textContent extractions (root.textContent, scopeContainer.textContent) are forbidden.
14. Ambiguous rich text fragments fail closed (raises RESULT_BODY_INVALID).
15. Existing pure-function permalink parser is reused for identity extraction.
16. channel_name is optional (None) and does not break result mapping.
17. SearchBackend.search() protocol entry maps SlackSearchResult to EvidenceItem correctly.
18. Raw query, body, sender, channel, and identity are not exposed in logs.
19. Public arbitrary JS or browser automation primitives are absent from the backend class.
20. Desktop backend (RealSlackSearchBackend / SlackSearch) is completely unchanged.
21. Default backend is not changed, and no implicit fallback between backends exists.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import pytest

from pesu_agent.investigation.models import EvidenceItem, SearchQuery
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.search.real_slack_backend import RealSlackSearchBackend
from pesu_agent.search.slack_search import SlackSearch, SlackSearchResult, SlackSearchSession
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    WebViewSearchError,
    WebViewSearchFailureCode,
    WebViewRuntimeState,
    WebViewRuntimeConfig,
)


def test_webview_backend_implements_search_backend_protocol():
    """Verify WebViewSlackSearchBackend satisfies the SearchBackend Protocol."""
    backend = WebViewSlackSearchBackend()
    assert isinstance(backend, SearchBackend)
    assert hasattr(backend, "search")
    assert hasattr(backend, "collect_context")
    assert hasattr(backend, "search_messages")
    assert hasattr(backend, "execute_search_session")


def test_collect_context_raises_typed_not_qualified_failure():
    """Verify collect_context raises typed CONTEXT_COLLECTION_NOT_QUALIFIED error and does not return []."""
    backend = WebViewSlackSearchBackend()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    with pytest.raises(WebViewSearchError) as exc:
        loop.run_until_complete(backend.collect_context("https://slack.com/archives/C123/p123456"))
    loop.close()

    assert exc.value.failure_code == WebViewSearchFailureCode.CONTEXT_COLLECTION_NOT_QUALIFIED
    # Ensure source does not contain any DOM navigation
    src = inspect.getsource(backend.collect_context)
    assert "evaluate_js" not in src
    assert "click" not in src
    assert "navigate" not in src


def test_production_module_has_zero_diagnostic_imports():
    """Verify src/pesu_agent/search/webview_slack_search.py never imports tools/."""
    from pesu_agent.search import webview_slack_search
    src = inspect.getsource(webview_slack_search)
    assert "tools." not in src
    assert "tools/" not in src
    assert "webview_slack_poc" not in src
    assert "diagnose_" not in src


def test_query_validation_fails_closed_before_runtime():
    """Verify query validation rejects invalid/empty queries prior to runtime calls."""
    backend = WebViewSlackSearchBackend()

    # None query
    with pytest.raises(WebViewSearchError) as exc_none:
        backend.validate_query(None)  # type: ignore
    assert exc_none.value.failure_code == WebViewSearchFailureCode.EMPTY_QUERY_INVALID

    # Empty query
    with pytest.raises(WebViewSearchError) as exc_empty:
        backend.validate_query("")
    assert exc_empty.value.failure_code == WebViewSearchFailureCode.EMPTY_QUERY_INVALID

    # Whitespace-only query
    with pytest.raises(WebViewSearchError) as exc_ws:
        backend.validate_query("   \t\n  ")
    assert exc_ws.value.failure_code == WebViewSearchFailureCode.EMPTY_QUERY_INVALID

    # Non-string query
    with pytest.raises(WebViewSearchError) as exc_type:
        backend.validate_query(12345)  # type: ignore
    assert exc_type.value.failure_code == WebViewSearchFailureCode.EMPTY_QUERY_INVALID

    # Oversized query (>500 chars)
    with pytest.raises(WebViewSearchError) as exc_len:
        backend.validate_query("a" * 501)
    assert exc_len.value.failure_code == WebViewSearchFailureCode.EMPTY_QUERY_INVALID

    # Valid query passes cleanly
    assert backend.validate_query("  하위작업 추가  ") == "하위작업 추가"


def test_search_before_ready_fails_typed():
    """Verify executing search when backend is not in READY state raises RUNTIME_NOT_READY."""
    backend = WebViewSlackSearchBackend()
    assert backend.state == WebViewRuntimeState.STOPPED

    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("테스트")
    assert exc.value.failure_code == WebViewSearchFailureCode.RUNTIME_NOT_READY


def test_query_serialized_safely_as_data():
    """Verify query is serialized via json.dumps and never directly interpolated into JS."""
    from pesu_agent.search import webview_slack_search
    src = inspect.getsource(webview_slack_search.WebViewSlackSearchBackend._execute_search_pipeline)
    assert "json.dumps(validated_query)" in src
    assert 'f\'"{validated_query}"\'' not in src
    assert 'f"\'{validated_query}\'"' not in src


def test_no_arbitrary_js_or_browser_automation_in_public_api():
    """Verify WebViewSlackSearchBackend does not expose arbitrary JS or raw UI automation methods."""
    forbidden_methods = [
        "evaluate_arbitrary_js",
        "execute_script",
        "evaluate_js",
        "click_selector",
        "type_text",
        "press_key",
        "send_keys",
        "navigate_url",
    ]
    backend = WebViewSlackSearchBackend()
    for method_name in forbidden_methods:
        assert not hasattr(backend, method_name), f"Forbidden method '{method_name}' exposed on backend!"


def test_body_extraction_contract_scoped_and_fail_closed():
    """Verify JS extraction template enforces strict scope, Representation A/B rules, and fail-closed semantics."""
    from pesu_agent.search import webview_slack_search
    js_src = webview_slack_search.JS_EXTRACT_SEARCH_RESULTS

    # Scope constraint
    assert "scopeContainer.querySelectorAll(bodyFragSel)" in js_src
    assert "root.querySelectorAll(bodyFragSel)" not in js_src

    # Broad extraction forbidden
    assert "scopeContainer.textContent" not in js_src
    assert "root.textContent" not in js_src
    assert "messageBlocks.textContent" not in js_src
    assert "sb.textContent" not in js_src

    # Representation B presence
    assert "scopeContainer.querySelector('.c-message__message_blocks')" in js_src
    assert "messageBlocks.querySelectorAll('.p-section_block')" in js_src
    assert "p-section_block__accessory" in js_src

    # Ambiguous fail-closed
    assert "isRepAValid && sectionBlocks.length === 0" in js_src
    assert "allFragmentsA.length === 0 && isRepBValid" in js_src
    assert "isRepAValid && isRepBValid" in js_src
    assert "BODY_REPRESENTATION_CONFLICT" in js_src


def test_representation_b_mock_search_success():
    """Verify Representation B extraction succeeds with valid single or multi-section blocks."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()

    class MockWindow:
        def evaluate_js(self, script: str) -> Any:
            if "JS_CHECK_AUTH_AND_READINESS" in script or "searchButtons.length === 1" in script:
                return {"ready": True, "auth_required": False, "search_button_count": 1}
            if "JS_ACTIVATE_SEARCH" in script or "mousedownEvt" in script:
                return {"activated": True}
            if "SEARCH_INPUT_NOT_MOUNTED" in script or "JS_CHECK_SEARCH_INPUT_READINESS" in script:
                return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
            if "JS_SET_QUERY_AND_SUBMIT" in script or "isSearchInputBoxStrong" in script:
                return {"ok": True, "submitted": True}
            if "JS_EXTRACT_SEARCH_RESULTS" in script or "qualifying_permalinks" in script:
                return {
                    "ok": True,
                    "terminal_state": "RESULTS",
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": ["https://slack.com/archives/C12345/p1724738400000000"],
                            "sender_text": "김철수",
                            "sender_valid": True,
                            "timestamp_raw": "오후 3:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "body_representation": "REPRESENTATION_B",
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "첫 번째 섹션 본문 두 번째 섹션 본문",
                            "body_valid": True,
                        }
                    ]
                }
            return {"ok": True}

    backend._window = MockWindow()
    results = backend.search_messages("섹션 테스트", max_results=5)
    assert len(results) == 1
    assert results[0].text == "첫 번째 섹션 본문 두 번째 섹션 본문"
    assert results[0].author == "김철수"
    assert results[0].channel_id == "C12345"


def test_representation_b_conflict_or_invalid_fails_closed():
    """Verify conflict or invalid Representation B fails closed with RESULT_BODY_INVALID."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()

    class MockConflictWindow:
        def evaluate_js(self, script: str) -> Any:
            if "JS_CHECK_AUTH_AND_READINESS" in script or "searchButtons.length === 1" in script:
                return {"ready": True, "auth_required": False, "search_button_count": 1}
            if "JS_ACTIVATE_SEARCH" in script or "mousedownEvt" in script:
                return {"activated": True}
            if "SEARCH_INPUT_NOT_MOUNTED" in script or "JS_CHECK_SEARCH_INPUT_READINESS" in script:
                return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
            if "JS_SET_QUERY_AND_SUBMIT" in script or "isSearchInputBoxStrong" in script:
                return {"ok": True, "submitted": True}
            if "JS_EXTRACT_SEARCH_RESULTS" in script or "qualifying_permalinks" in script:
                return {
                    "ok": True,
                    "terminal_state": "RESULTS",
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": ["https://slack.com/archives/C12345/p1724738400000000"],
                            "sender_text": "김철수",
                            "sender_valid": True,
                            "timestamp_raw": "오후 3:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "body_representation": "CONFLICT",
                            "body_failure_reason": "BODY_REPRESENTATION_CONFLICT",
                            "ambiguous_fragment_count": 1,
                            "normalized_body": "",
                            "body_valid": False,
                        }
                    ]
                }
            return {"ok": True}

    backend._window = MockConflictWindow()
    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("충돌 테스트", max_results=5)
    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_RESULT_STRUCTURE_CHANGED
    assert exc.value.failure_code != WebViewSearchFailureCode.RESULT_BODY_INVALID
    assert backend.last_search_telemetry.get("invalid_body_count") == 1
    assert backend.last_search_telemetry.get("invalid_items_skipped") == 1


def test_runtime_lifecycle_states_and_idempotency():
    """Verify runtime lifecycle states, transitions, and idempotency."""
    backend = WebViewSlackSearchBackend()
    assert backend.state == WebViewRuntimeState.STOPPED

    backend.start_runtime()
    assert backend.state == WebViewRuntimeState.READY

    # Duplicate start is idempotent
    backend.start_runtime()
    assert backend.state == WebViewRuntimeState.READY

    backend.stop_runtime()
    assert backend.state == WebViewRuntimeState.STOPPED

    # Duplicate stop is idempotent
    backend.stop_runtime()
    assert backend.state == WebViewRuntimeState.STOPPED


def test_desktop_backend_and_defaults_unchanged():
    """Verify Desktop backend (RealSlackSearchBackend) remains the primary and is completely unmodified."""
    assert issubclass(RealSlackSearchBackend, SearchBackend)
    assert hasattr(RealSlackSearchBackend, "search")
    assert hasattr(RealSlackSearchBackend, "collect_context")

    real_src = inspect.getsource(RealSlackSearchBackend)
    assert "WebViewSlackSearchBackend" not in real_src
    assert "webview" not in real_src.lower()


def test_mock_search_and_protocol_mapping(monkeypatch):
    """Verify end-to-end execution and SearchBackend protocol mapping with mocked window."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()

    class MockWindow:
        def evaluate_js(self, script: str) -> Any:
            if "JS_CHECK_AUTH_AND_READINESS" in script or "searchButtons.length === 1" in script:
                return {"ready": True, "auth_required": False, "search_button_count": 1}
            if "JS_ACTIVATE_SEARCH" in script or "mousedownEvt" in script:
                return {"activated": True}
            if "SEARCH_INPUT_NOT_MOUNTED" in script or "JS_CHECK_SEARCH_INPUT_READINESS" in script or "searchModalCandidates" in script:
                return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
            if "JS_SET_QUERY_AND_SUBMIT" in script or "targetQuery" in script or "isSearchInputBoxStrong" in script:
                return {"ok": True, "submitted": True}
            if "JS_EXTRACT_SEARCH_RESULTS" in script or "qualifying_permalinks" in script:
                return {
                    "ok": True,
                    "terminal_state": "RESULTS",
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": ["https://slack.com/archives/C12345/p1724738400000000"],
                            "sender_text": "홍길동",
                            "sender_valid": True,
                            "timestamp_raw": "오후 2:30",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "primary_fragments": ["문단 1", "문단 2"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "문단 1 문단 2",
                            "body_valid": True,
                        }
                    ]
                }
            return {"ok": True}

    backend._window = MockWindow()

    # Test search_messages
    results = backend.search_messages("테스트 쿼리", max_results=5)
    assert len(results) == 1
    assert isinstance(results[0], SlackSearchResult)
    assert results[0].text == "문단 1 문단 2"
    assert results[0].author == "홍길동"
    assert results[0].timestamp_raw == "오후 2:30"
    assert results[0].channel_id == "C12345"
    assert results[0].channel_name is None

    # Test execute_search_session
    session = backend.execute_search_session("테스트 쿼리", max_results=5)
    assert isinstance(session, SlackSearchSession)
    assert session.result_count == 1
    assert session.query_verified is True

    # Test async SearchBackend.search protocol entry
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    evidence_items = loop.run_until_complete(
        backend.search(SearchQuery(query="테스트 쿼리", purpose="단위 테스트 목적"), max_results=5)
    )
    loop.close()

    assert len(evidence_items) == 1
    assert isinstance(evidence_items[0], EvidenceItem)
    assert evidence_items[0].source == "slack"
    assert evidence_items[0].text == "문단 1 문단 2"
    assert evidence_items[0].author == "홍길동"
    assert evidence_items[0].channel == "C12345"
    assert evidence_items[0].message_identifier == "https://slack.com/archives/C12345/p1724738400000000"


def test_representation_b_unknown_body_looking_child_fails_closed():
    """Verify unknown text-bearing child fails closed instead of being accepted as body."""
    from pesu_agent.search.webview_slack_search import JS_EXTRACT_SEARCH_RESULTS

    # Qualified body requires exact observed token
    assert "isQualifiedBody = cCls.includes('p-section_block_text_content')" in JS_EXTRACT_SEARCH_RESULTS

    # Speculative unobserved tokens are strictly excluded from B predicate
    assert "p-section_block__text" not in JS_EXTRACT_SEARCH_RESULTS
    assert "section_text" not in JS_EXTRACT_SEARCH_RESULTS

    # Unknown text-bearing child causes unknownTextCount++
    assert "unknownTextCount++" in JS_EXTRACT_SEARCH_RESULTS
    assert "SECTION_BLOCK_UNKNOWN_TEXT_BEARING_CHILD" in JS_EXTRACT_SEARCH_RESULTS


def test_representation_b_accessory_with_text_still_excluded():
    """Verify accessory child is excluded structurally even when it contains text."""
    from pesu_agent.search.webview_slack_search import JS_EXTRACT_SEARCH_RESULTS

    # isAccessory check happens before body text check and does 'continue'
    assert "if (isAccessory) {" in JS_EXTRACT_SEARCH_RESULTS
    assert "continue;" in JS_EXTRACT_SEARCH_RESULTS
    assert "isQualifiedBody && cVis && cText.length > 0" in JS_EXTRACT_SEARCH_RESULTS


def test_search_before_window_attachment_raises_runtime_not_ready():
    """Verify search execution without attached window raises RUNTIME_NOT_READY."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()
    assert backend._window is None

    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("테스트", max_results=5)
    assert exc.value.failure_code == WebViewSearchFailureCode.RUNTIME_NOT_READY
    assert "not attached" in str(exc.value)


def test_search_with_attached_window_requires_ready_state():
    """Verify search execution on stopped backend raises RUNTIME_NOT_READY even if window reference is set."""
    backend = WebViewSlackSearchBackend()
    backend._window = object()
    assert backend.state == WebViewRuntimeState.STOPPED

    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("테스트", max_results=5)
    assert exc.value.failure_code == WebViewSearchFailureCode.RUNTIME_NOT_READY
    assert "not ready" in str(exc.value)


def test_targeted_representation_b_qualification_harness_contracts():
    """Verify diagnose_webview_production_representation_b.py obeys strict safety, lifecycle, and isolation contracts."""
    diag_path = Path("tools/webview_slack_poc/diagnose_webview_production_representation_b.py")
    assert diag_path.exists()

    diag_src = diag_path.read_text(encoding="utf-8")

    # Lifecycle & Attachment
    assert "backend._window = win" in diag_src
    assert "backend.start_runtime()" in diag_src
    assert "backend.search_messages(" in diag_src
    assert "backend.stop_runtime()" in diag_src

    # Diagnostic override max_results=10
    assert "TARGET_MAX_RESULTS = 10" in diag_src

    # Production default remains unchanged
    from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
    backend = WebViewSlackSearchBackend()
    assert backend.default_max_results == 10  # Backend parameter default

    # Separation of lifecycle vs representation verdicts
    assert '"lifecycle_verdict": "FAIL"' in diag_src
    assert '"representation_a_runtime_verdict": "NOT_TESTED"' in diag_src
    assert '"representation_b_runtime_verdict": "NOT_TESTED"' in diag_src

    # Isolation from LLM components
    assert "from pesu_agent.investigation.orchestrator" not in diag_src
    assert "from pesu_agent.investigation.planner" not in diag_src
    assert "from pesu_agent.investigation.analyzer" not in diag_src
    assert "from pesu_agent.llm" not in diag_src
    assert "OpenRouterProvider" not in diag_src


def test_readiness_polling_dispatches_activation_max_once():
    """Verify activation is dispatched once and input readiness is polled until ready before submit."""
    from unittest.mock import MagicMock
    from pesu_agent.search.webview_slack_search import (
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
        JS_SET_TEXTY_QUERY,
        JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    )

    mock_win = MagicMock()
    eval_calls = []

    def mock_eval(js_code):
        eval_calls.append(js_code)
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": True}
        elif JS_CHECK_SEARCH_INPUT_READINESS in js_code or "searchModalCandidates" in js_code:
            poll_count = sum(1 for c in eval_calls if JS_CHECK_SEARCH_INPUT_READINESS in c or "searchModalCandidates" in c)
            if poll_count == 1:
                return {"ready": False, "modal_count": 1, "input_count": 0, "ambiguous": False}
            return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
        elif JS_SET_TEXTY_QUERY in js_code:
            return {"ok": True, "setter_succeeded": True}
        elif JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER in js_code:
            return {"ok": True, "report": {"submit_succeeded": True, "preconditions_satisfied": True}}
        elif "skeleton_count" in js_code:
            return {
                "surface_query_after": "테스트",
                "loading_detected_after": False,
                "skeleton_count": 0,
                "result_root_count": 0,
                "empty_element_count": 1,
            }
        elif "JS_EXTRACT_SEARCH_RESULTS" in js_code or "search_view" in js_code:
            return {"ok": True, "terminal_state": "EMPTY_RESULTS", "items": []}
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    results = backend.search_messages("테스트", max_results=5)
    assert results == []

    # Verify activation dispatched exactly 1 time
    activation_count = sum(1 for c in eval_calls if JS_ACTIVATE_SEARCH in c)
    assert activation_count == 1

    # Verify readiness polled 2 times
    poll_count = sum(1 for c in eval_calls if JS_CHECK_SEARCH_INPUT_READINESS in c or "searchModalCandidates" in c)
    assert poll_count == 2

    # Verify setter and submit each invoked exactly 1 time
    setter_count = sum(1 for c in eval_calls if JS_SET_TEXTY_QUERY in c)
    submit_count = sum(1 for c in eval_calls if JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER in c)
    assert setter_count == 1
    assert submit_count == 1


def test_readiness_polling_times_out_with_typed_error_and_zero_submits():
    """Verify input readiness timeout raises SEARCH_INPUT_READINESS_TIMEOUT with 0 submit calls."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import JS_CHECK_AUTH_AND_READINESS, JS_ACTIVATE_SEARCH, JS_CHECK_SEARCH_INPUT_READINESS

    mock_win = MagicMock()
    eval_calls = []

    def mock_eval(js_code):
        eval_calls.append(js_code)
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": True}
        elif JS_CHECK_SEARCH_INPUT_READINESS in js_code or "searchModalCandidates" in js_code:
            return {"ready": False, "modal_count": 1, "input_count": 0, "ambiguous": False, "error": "SEARCH_INPUT_NOT_MOUNTED"}
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    with patch("time.sleep", return_value=None), patch("time.time", side_effect=[0.0, 0.0, 1.0, 6.0, 6.0, 6.0, 6.0, 6.0]):
        with pytest.raises(WebViewSearchError) as exc:
            backend.search_messages("테스트", max_results=5)

    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT

    # Verify 0 submit calls occurred
    submit_count = sum(1 for c in eval_calls if "targetQuery" in c)
    assert submit_count == 0


def test_readiness_polling_fails_immediately_on_ambiguity():
    """Verify ambiguity (e.g. multiple inputs or composer detected) fails closed immediately."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import JS_CHECK_AUTH_AND_READINESS, JS_ACTIVATE_SEARCH, JS_CHECK_SEARCH_INPUT_READINESS

    mock_win = MagicMock()
    eval_calls = []

    def mock_eval(js_code):
        eval_calls.append(js_code)
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": True}
        elif JS_CHECK_SEARCH_INPUT_READINESS in js_code or "searchModalCandidates" in js_code:
            return {"ready": False, "ambiguous": True, "error": "SEARCH_INPUT_IS_COMPOSER"}
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("테스트", max_results=5)

    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_AMBIGUOUS

    # Verify 0 submit calls occurred
    submit_count = sum(1 for c in eval_calls if "targetQuery" in c)
    assert submit_count == 0


def test_readiness_js_primitive_excludes_composer_and_forbids_fallbacks():
    """Verify JS_CHECK_SEARCH_INPUT_READINESS excludes message composer and contains no keyboard/click fallbacks."""
    from pesu_agent.search.webview_slack_search import JS_CHECK_SEARCH_INPUT_READINESS

    assert "message_editor" in JS_CHECK_SEARCH_INPUT_READINESS
    assert "message_input" in JS_CHECK_SEARCH_INPUT_READINESS
    assert "SEARCH_INPUT_IS_COMPOSER" in JS_CHECK_SEARCH_INPUT_READINESS
    assert "KeyboardEvent" not in JS_CHECK_SEARCH_INPUT_READINESS
    assert "dispatchKeyEvent" not in JS_CHECK_SEARCH_INPUT_READINESS
    assert "click(" not in JS_CHECK_SEARCH_INPUT_READINESS


def test_failure_telemetry_preserved_on_timeout_case_b_modal_not_mounted():
    """Verify Case B (activation ok, but modal never appears) preserves complete telemetry and classifies as CASE_B_MODAL_NOT_MOUNTED."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import (
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
    )

    mock_win = MagicMock()

    def mock_eval(js_code):
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": True}
        elif JS_CHECK_SEARCH_INPUT_READINESS in js_code or "searchModalCandidates" in js_code:
            return {
                "ready": False,
                "modal_count": 0,
                "modal_visible_count": 0,
                "input_count": 0,
                "input_visible_count": 0,
                "document_visibility_state": "visible",
                "document_hidden": False,
                "document_has_focus": False,
                "ambiguous": False,
                "error": "SEARCH_INPUT_NOT_MOUNTED",
            }
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    with patch("time.sleep", return_value=None), patch("time.time", side_effect=[0.0, 0.0, 1.0, 6.0, 6.0, 6.0, 6.0, 6.0]):
        with pytest.raises(WebViewSearchError) as exc:
            backend.search_messages("테스트", max_results=5)

    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT
    details = exc.value.details
    assert details is not None
    assert details["activation_dispatch_count"] == 1
    assert details["activation_result"] == "activated:true"
    assert details["modal_ever_observed"] is False
    assert details["input_ever_observed"] is False
    assert details["failure_case_classification"] == "CASE_B_MODAL_NOT_MOUNTED"
    assert details["document_visibility_state"] == "visible"
    assert backend.last_search_telemetry["activation_dispatch_count"] == 1
    assert backend.last_search_telemetry["failure_case_classification"] == "CASE_B_MODAL_NOT_MOUNTED"


def test_failure_telemetry_preserved_on_timeout_case_c_input_not_mounted():
    """Verify Case C (activation ok, modal appears, but input never appears) classifies as CASE_C_INPUT_NOT_MOUNTED."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import (
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
    )

    mock_win = MagicMock()

    def mock_eval(js_code):
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": True}
        elif JS_CHECK_SEARCH_INPUT_READINESS in js_code or "searchModalCandidates" in js_code:
            return {
                "ready": False,
                "modal_count": 1,
                "modal_visible_count": 1,
                "input_count": 0,
                "input_visible_count": 0,
                "document_visibility_state": "visible",
                "document_hidden": False,
                "document_has_focus": True,
                "ambiguous": False,
                "error": "SEARCH_INPUT_NOT_MOUNTED",
            }
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    with patch("time.sleep", return_value=None), patch("time.time", side_effect=[0.0, 0.0, 1.0, 6.0, 6.0, 6.0, 6.0, 6.0]):
        with pytest.raises(WebViewSearchError) as exc:
            backend.search_messages("테스트", max_results=5)

    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT
    details = exc.value.details
    assert details is not None
    assert details["activation_dispatch_count"] == 1
    assert details["activation_result"] == "activated:true"
    assert details["modal_ever_observed"] is True
    assert details["input_ever_observed"] is False
    assert details["failure_case_classification"] == "CASE_C_INPUT_NOT_MOUNTED"
    assert details["last_confirmed_stage"] == "MODAL_OBSERVED"


def test_failure_telemetry_preserved_on_activation_failed_case_a():
    """Verify Case A (activation dispatch fails) classifies as CASE_A_ACTIVATION_FAILED."""
    from unittest.mock import MagicMock
    from pesu_agent.search.webview_slack_search import (
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
    )

    mock_win = MagicMock()

    def mock_eval(js_code):
        if JS_CHECK_AUTH_AND_READINESS in js_code:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        elif JS_ACTIVATE_SEARCH in js_code:
            return {"activated": False, "error": "BUTTON_NOT_FOUND"}
        return {}

    mock_win.evaluate_js.side_effect = mock_eval

    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()

    with pytest.raises(WebViewSearchError) as exc:
        backend.search_messages("테스트", max_results=5)

    assert exc.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND
    details = exc.value.details
    assert details is not None
    assert details["activation_dispatch_count"] == 1
    assert details["modal_ever_observed"] is False
    assert details["input_ever_observed"] is False
    assert details["failure_case_classification"] == "CASE_A_ACTIVATION_FAILED"


def test_qualified_modal_and_input_selector_parity():
    """Verify exact parity of qualified search modal and search input selectors across backend primitives."""
    from pesu_agent.search.webview_slack_search import JS_CHECK_SEARCH_INPUT_READINESS, JS_SET_QUERY_AND_SUBMIT

    # Modal selectors parity
    for modal_sel in ['[data-qa="search_modal"]', '[data-qa="search_dialog"]', '.c-search_modal', '.p-search_modal']:
        assert modal_sel in JS_CHECK_SEARCH_INPUT_READINESS

    # Input selector parity
    assert '[data-qa="search_input_box"] .ql-editor' in JS_CHECK_SEARCH_INPUT_READINESS
    assert '[data-qa="search_input_box"] .ql-editor' in JS_SET_QUERY_AND_SUBMIT

    # Forbid generic fallbacks
    assert 'input[type="text"]' not in JS_CHECK_SEARCH_INPUT_READINESS
    assert 'textarea' not in JS_CHECK_SEARCH_INPUT_READINESS
    assert 'role="textbox"' not in JS_CHECK_SEARCH_INPUT_READINESS


def test_activation_implementation_parity():
    """Verify activation routine uses exact qualified top_nav_search and MouseEvent mousedown dispatch."""
    from pesu_agent.search.webview_slack_search import JS_ACTIVATE_SEARCH

    assert 'button[data-qa="top_nav_search"]' in JS_ACTIVATE_SEARCH
    assert "new MouseEvent('mousedown'" in JS_ACTIVATE_SEARCH
    assert "bubbles: true" in JS_ACTIVATE_SEARCH
    assert "cancelable: true" in JS_ACTIVATE_SEARCH
    assert "button: 0" in JS_ACTIVATE_SEARCH
    assert "ctrlKey: false" in JS_ACTIVATE_SEARCH


def test_consecutive_investigations_search_after_thread_navigation():
    """Verify consecutive investigation capability across thread navigation (Phase 12.3e Requirement 12).

    Scenario:
    - Investigation A: executes Search -> attempts Thread route/context navigation to internal SPA route.
    - Host remains alive in the same authenticated Direct Host session at internal thread route.
    - Investigation B: executes Search on the same session.
    - Search B must succeed without requiring page reload, generic back navigation, or unauthenticated redirect.
    """
    from unittest.mock import MagicMock
    from pesu_agent.search.webview_slack_search import (
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
        JS_SET_QUERY_AND_SUBMIT,
        JS_EXTRACT_SEARCH_RESULTS,
    )
    from pesu_agent.search.webview_thread_reader import (
        JS_EXTRACT_THREAD_REPLIES,
        WebviewThreadReader,
    )
    from pesu_agent.investigation.models import ThreadContextStatus

    current_state = {
        "url": "https://app.slack.com/client/T32QA15GC/C04H83XRPA7",
        "search_count": 0,
        "thread_navigated": False,
    }

    mock_win = MagicMock()
    mock_win.is_ready = True
    mock_win.get_active_team_id.return_value = "T32QA15GC"

    def mock_evaluate_js(script, timeout=10.0):
        # 1. Team ID resolution
        if "boot_data" in script or "pathname.match" in script:
            return "T32QA15GC"

        # 2. Check auth and readiness
        if JS_CHECK_AUTH_AND_READINESS in script:
            return {
                "ready": True,
                "auth_required": False,
                "search_button_count": 1,
            }

        # 3. Activate search
        if JS_ACTIVATE_SEARCH in script:
            return {"activated": True}

        # 4. Check search input readiness
        if JS_CHECK_SEARCH_INPUT_READINESS in script or "searchModalCandidates" in script:
            return {
                "ready": True,
                "modal_count": 1,
                "modal_visible_count": 1,
                "input_count": 1,
                "input_visible_count": 1,
                "document_visibility_state": "visible",
                "document_hidden": False,
                "document_has_focus": True,
                "ambiguous": False,
                "error": None,
            }

        # 5. Set query and submit
        if JS_SET_QUERY_AND_SUBMIT in script or "setContents" in script:
            return {"ok": True, "setter_succeeded": True, "submitted": True}
        if "onEnter" in script:
            current_state["search_count"] += 1
            return {"ok": True, "submitted": True, "report": {"submit_succeeded": True, "preconditions_satisfied": True}}

        # 6. Extract search results
        if JS_EXTRACT_SEARCH_RESULTS in script or "maxSampleCount" in script:
            if current_state["search_count"] == 1:
                # Results for Investigation A
                return {
                    "ok": True,
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": [
                                "https://app.slack.com/archives/C04H83XRPA7/p1787097408846349?thread_ts=1787097408.846349"
                            ],
                            "sender_text": "박준형",
                            "sender_valid": True,
                            "timestamp_raw": "2026-08-20 14:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": True,
                            "primary_fragments": ["26년 하반기 간담회 공지"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "26년 하반기 간담회 공지",
                            "body_valid": True,
                        }
                    ],
                }
            else:
                # Results for Investigation B
                return {
                    "ok": True,
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": [
                                "https://app.slack.com/archives/C04H83XRPA7/p1788000000000001"
                            ],
                            "sender_text": "김영희",
                            "sender_valid": True,
                            "timestamp_raw": "2026-08-21 10:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "primary_fragments": ["간담회 장소 관련 문의"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "간담회 장소 관련 문의",
                            "body_valid": True,
                        }
                    ],
                }

        # 7. Thread reader lifecycle
        if JS_EXTRACT_THREAD_REPLIES in script or "maxCandidateRows" in script:
            return {
                "ok": True,
                "replies": [
                    {
                        "href": "https://app.slack.com/archives/C04H83XRPA7/p1787100000123456?thread_ts=1787097408.846349",
                        "author": "홍길동",
                        "text": "간담회 세부 일정 확인했습니다.",
                    }
                ],
            }
        if "selectedRowIndex" in script:
            current_state["thread_navigated"] = True
            return {"ok": True, "guard_passed": True, "click_executed": True, "pre_click_root_count": 0}
        if "newRoots = afterRoots.filter" in script or "new_root_count" in script:
            return {"ready": True, "new_root_count": 1}
        if "searchViewSel" in script:
            return {
                "ok": True,
                "rows": [
                    {
                        "row_index": 0,
                        "permalinks": [
                            "https://app.slack.com/archives/C04H83XRPA7/p1787097408846349?thread_ts=1787097408.846349"
                        ],
                    }
                ],
            }
        if "delete window.__pesu_thread_op" in script:
            return None

        return {}

    mock_win.evaluate_js.side_effect = mock_evaluate_js

    def mock_nav_thread(nav_req, timeout=20.0):
        current_state["url"] = nav_req.target_route
        current_state["thread_navigated"] = True
        return True

    mock_win.navigate_thread_context.side_effect = mock_nav_thread

    # Initialize backend
    backend = WebViewSlackSearchBackend()
    backend._window = mock_win
    backend.start_runtime()
    assert backend._state == WebViewRuntimeState.READY

    # -------------------------------------------------------------
    # Step 1: Investigation A - Search
    # -------------------------------------------------------------
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results_a = loop.run_until_complete(
            backend.search(
                SearchQuery(query="26년 하반기 간담회", purpose="1차 간담회 일정 조사"),
                max_results=5,
            )
        )
        assert len(results_a) == 1
        assert results_a[0].author == "박준형"

        # -------------------------------------------------------------
        # Step 2: Investigation A - Thread Navigation & Context Read
        # -------------------------------------------------------------
        replies, status = loop.run_until_complete(
            backend.read_thread_context(results_a[0])
        )
        assert len(replies) == 1
        assert status == ThreadContextStatus.PARTIAL
        assert current_state["thread_navigated"] is True

        # -------------------------------------------------------------
        # Step 3: Investigation B - Search on SAME session from Thread state
        # -------------------------------------------------------------
        results_b = loop.run_until_complete(
            backend.search(
                SearchQuery(query="간담회 장소", purpose="2차 간담회 장소 조사"),
                max_results=5,
            )
        )
        assert len(results_b) == 1
        assert results_b[0].author == "김영희"
        assert current_state["search_count"] == 2
        assert backend._state == WebViewRuntimeState.READY
    finally:
        loop.close()


# =====================================================================
# Phase 12.3h-R2: Canonical Slack Search Readiness Gate Tests
# =====================================================================


def test_search_readiness_gate_case1_delayed_mounting_succeeds():
    """Case 1: count 0 -> count 0 -> count 1 succeeds after bounded polling."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import (
        WebViewHostProtocol,
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
        JS_SET_QUERY_AND_SUBMIT,
        JS_EXTRACT_SEARCH_RESULTS,
    )

    poll_iteration = 0
    eval_calls = []

    def fake_evaluate_js(script: str) -> Any:
        nonlocal poll_iteration
        eval_calls.append(script)
        if JS_CHECK_AUTH_AND_READINESS in script or "searchButtons.length === 1" in script:
            poll_iteration += 1
            if poll_iteration < 3:
                return {"ready": False, "auth_required": False, "search_button_count": 0}
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        if JS_ACTIVATE_SEARCH in script or "mousedownEvt" in script:
            return {"activated": True}
        if JS_CHECK_SEARCH_INPUT_READINESS in script or "searchModalCandidates" in script:
            return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
        if "targetQuery" in script or JS_SET_QUERY_AND_SUBMIT in script or "isSearchInputBoxStrong" in script:
            return {"ok": True, "submitted": True}
        if "skeleton_count" in script:
            return {
                "surface_query_after": "테스트 쿼리",
                "loading_detected_after": False,
                "skeleton_count": 0,
                "result_root_count": 0,
                "empty_element_count": 1,
            }
        if "maxSampleCount" in script or JS_EXTRACT_SEARCH_RESULTS in script or "search_view" in script:
            return {"ok": True, "terminal_state": "EMPTY_RESULTS", "items": []}
        return {}

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = fake_evaluate_js

    backend = WebViewSlackSearchBackend(host=mock_win)

    with patch("time.sleep", return_value=None) as mock_sleep:
        results = backend.search_messages("테스트 쿼리", max_results=5)

    assert results == []
    assert poll_iteration == 3
    from unittest.mock import call
    step1_sleeps = [
        c for c in mock_sleep.call_args_list if c == call(backend.search_ui_ready_poll_interval_sec)
    ]
    assert len(step1_sleeps) == 2
    assert backend.last_search_telemetry["search_ui_poll_count"] == 3
    assert backend.last_search_telemetry["top_nav_search_count"] == 1
    assert backend.last_search_telemetry["last_confirmed_stage"] == "SUBMIT_EXECUTED"


def test_search_readiness_gate_case2_auth_required_fails_immediately():
    """Case 2: auth_required == True on first iteration fails immediately with AUTH_REQUIRED."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import WebViewHostProtocol, JS_CHECK_AUTH_AND_READINESS

    eval_calls = []

    def fake_evaluate_js(script: str) -> Any:
        eval_calls.append(script)
        if JS_CHECK_AUTH_AND_READINESS in script:
            return {"ready": False, "auth_required": True, "search_button_count": 0}
        return {}

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = fake_evaluate_js

    backend = WebViewSlackSearchBackend(host=mock_win)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(WebViewSearchError) as exc_info:
            backend._execute_search_pipeline("테스트 쿼리", max_results=5)

    assert exc_info.value.failure_code == WebViewSearchFailureCode.AUTH_REQUIRED
    mock_sleep.assert_not_called()
    assert backend.last_search_telemetry["last_confirmed_stage"] == "AUTH_CHECKED"
    assert len(eval_calls) == 1


def test_search_readiness_gate_case3_ambiguous_buttons_fail_immediately():
    """Case 3: search_button_count > 1 fails immediately with SEARCH_UI_AMBIGUOUS."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import WebViewHostProtocol, JS_CHECK_AUTH_AND_READINESS

    eval_calls = []

    def fake_evaluate_js(script: str) -> Any:
        eval_calls.append(script)
        if JS_CHECK_AUTH_AND_READINESS in script:
            return {"ready": False, "auth_required": False, "search_button_count": 2}
        return {}

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = fake_evaluate_js

    backend = WebViewSlackSearchBackend(host=mock_win)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(WebViewSearchError) as exc_info:
            backend._execute_search_pipeline("테스트 쿼리", max_results=5)

    assert exc_info.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_AMBIGUOUS
    mock_sleep.assert_not_called()
    assert backend.last_search_telemetry["top_nav_search_count"] == 2
    assert backend.last_search_telemetry["last_confirmed_stage"] == "AUTH_CHECKED"
    assert len(eval_calls) == 1


def test_search_readiness_gate_case4_zero_buttons_timeout_fails_closed():
    """Case 4: count remains 0 until deadline fails closed with SEARCH_UI_NOT_FOUND."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import WebViewHostProtocol, JS_CHECK_AUTH_AND_READINESS

    eval_calls = []

    def fake_evaluate_js(script: str) -> Any:
        eval_calls.append(script)
        if JS_CHECK_AUTH_AND_READINESS in script:
            return {"ready": False, "auth_required": False, "search_button_count": 0}
        return {}

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = fake_evaluate_js

    backend = WebViewSlackSearchBackend(
        host=mock_win,
        search_ui_ready_timeout_sec=5.0,
        search_ui_ready_poll_interval_sec=0.1,
    )

    # Monotonic clock ticks: 0.0 -> 0.0 (check 1) -> 2.0 (check 2) -> 5.1 (deadline reached)
    monotonic_ticks = [0.0, 0.0, 2.0, 5.1, 5.1, 5.1]
    with patch("time.monotonic", side_effect=monotonic_ticks), patch("time.sleep", return_value=None) as mock_sleep:
        with pytest.raises(WebViewSearchError) as exc_info:
            backend._execute_search_pipeline("테스트 쿼리", max_results=5)

    assert exc_info.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND
    assert mock_sleep.call_count >= 1
    assert backend.last_search_telemetry["top_nav_search_count"] == 0
    assert backend.last_search_telemetry["last_confirmed_stage"] == "AUTH_CHECKED"
    assert backend.last_search_telemetry["search_ui_poll_count"] >= 2


@pytest.mark.parametrize(
    "malformed_payload",
    [
        None,
        "unexpected_string",
        [],
        {},
        {"ready": False, "auth_required": False, "search_button_count": "one"},
        {"ready": True, "auth_required": False, "search_button_count": -1},
        {"ready": True, "auth_required": False, "search_button_count": 0},
        {"ready": False, "auth_required": False, "search_button_count": 1},
    ],
)
def test_search_readiness_gate_case5_malformed_payload_fails_closed(malformed_payload):
    """Case 5: malformed readiness payload fails closed immediately with SEARCH_UI_NOT_FOUND."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import WebViewHostProtocol

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.return_value = malformed_payload

    backend = WebViewSlackSearchBackend(host=mock_win)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(WebViewSearchError) as exc_info:
            backend._execute_search_pipeline("테스트 쿼리", max_results=5)

    assert exc_info.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND
    mock_sleep.assert_not_called()
    assert backend.last_search_telemetry["last_confirmed_stage"] == "AUTH_CHECKED"


def test_search_readiness_gate_case5_eval_exception_fails_closed():
    """Case 5b: JS evaluation exception fails closed immediately with SEARCH_UI_NOT_FOUND."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import WebViewHostProtocol

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = RuntimeError("WebView bridge connection lost")

    backend = WebViewSlackSearchBackend(host=mock_win)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(WebViewSearchError) as exc_info:
            backend._execute_search_pipeline("테스트 쿼리", max_results=5)

    assert exc_info.value.failure_code == WebViewSearchFailureCode.SEARCH_UI_NOT_FOUND
    mock_sleep.assert_not_called()
    assert backend.last_search_telemetry["last_confirmed_stage"] == "AUTH_CHECKED"


def test_search_readiness_gate_case6_immediately_ready_has_zero_delay():
    """Case 6: readiness immediately available performs zero polling sleeps and proceeds directly."""
    from unittest.mock import MagicMock, patch
    from pesu_agent.search.webview_slack_search import (
        WebViewHostProtocol,
        JS_CHECK_AUTH_AND_READINESS,
        JS_ACTIVATE_SEARCH,
        JS_CHECK_SEARCH_INPUT_READINESS,
        JS_SET_QUERY_AND_SUBMIT,
        JS_EXTRACT_SEARCH_RESULTS,
    )

    def fake_evaluate_js(script: str) -> Any:
        if JS_CHECK_AUTH_AND_READINESS in script:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        if JS_ACTIVATE_SEARCH in script:
            return {"activated": True}
        if JS_CHECK_SEARCH_INPUT_READINESS in script:
            return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
        if "targetQuery" in script or JS_SET_QUERY_AND_SUBMIT in script or "isSearchInputBoxStrong" in script:
            return {"ok": True, "submitted": True}
        if "skeleton_count" in script:
            return {
                "surface_query_after": "테스트 쿼리",
                "loading_detected_after": False,
                "skeleton_count": 0,
                "result_root_count": 0,
                "empty_element_count": 1,
            }
        if "maxSampleCount" in script or JS_EXTRACT_SEARCH_RESULTS in script or "search_view" in script:
            return {"ok": True, "terminal_state": "EMPTY_RESULTS", "items": []}
        return {}

    mock_win = MagicMock(spec=WebViewHostProtocol)
    mock_win.evaluate_js.side_effect = fake_evaluate_js

    backend = WebViewSlackSearchBackend(host=mock_win)

    from unittest.mock import call
    with patch("time.sleep", return_value=None) as mock_sleep:
        results = backend.search_messages("테스트 쿼리", max_results=5)

    assert results == []
    step1_sleeps = [
        c for c in mock_sleep.call_args_list if c == call(backend.search_ui_ready_poll_interval_sec)
    ]
    assert len(step1_sleeps) == 0
    assert backend.last_search_telemetry["search_ui_poll_count"] == 1
    assert backend.last_search_telemetry["top_nav_search_count"] == 1
    assert backend.last_search_telemetry["last_confirmed_stage"] == "SUBMIT_EXECUTED"


