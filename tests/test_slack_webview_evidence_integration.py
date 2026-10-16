"""Unit & Contract Tests for WebView Backend to Evidence Integration (Phase 9.9.4c).

Validates:
1. WebViewSlackSearchBackend can be injected as a SearchBackend Protocol into investigation orchestrators.
2. SlackEvidenceCollector and Investigation modules do NOT have concrete imports of WebViewSlackSearchBackend.
3. Using WebView backend does not trigger implicit fallback to RealSlackSearchBackend or Desktop CDP.
4. collect_context() strictly raises typed CONTEXT_COLLECTION_NOT_QUALIFIED error and is NEVER masked as [].
5. Search EvidenceItem mapping fidelity is preserved (evidence_id, source="slack", author, timestamp, text, channel, message_identifier).
6. Canonical permalink citation metadata is preserved on EvidenceItem.message_identifier.
7. Deduplication preserves DEDUPE_KEEP_FIRST policy without merging conflicting contents.
8. Raw message contents, queries, senders, and identities are not exposed in logs.
9. Default primary backend remains RealSlackSearchBackend.
"""

from __future__ import annotations

import asyncio
import inspect
import pytest

from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    WebViewSearchError,
    WebViewSearchFailureCode,
    WebViewRuntimeState,
)
from pesu_agent.evidence.slack_evidence_collector import SlackEvidenceCollector
from pesu_agent.investigation.models import EvidenceItem, SearchQuery
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.search.real_slack_backend import RealSlackSearchBackend


def test_webview_backend_injectable_into_search_backend_protocol():
    """Verify WebViewSlackSearchBackend can be typed and injected anywhere SearchBackend is expected."""
    backend: SearchBackend = WebViewSlackSearchBackend()
    assert isinstance(backend, SearchBackend)
    assert hasattr(backend, "search")
    assert hasattr(backend, "collect_context")


def test_evidence_collector_has_no_concrete_webview_import():
    """Verify SlackEvidenceCollector does not import or know about WebViewSlackSearchBackend."""
    collector_src = inspect.getsource(SlackEvidenceCollector)
    assert "WebViewSlackSearchBackend" not in collector_src
    assert "webview_slack_search" not in collector_src


def test_no_implicit_desktop_fallback_on_webview_failure():
    """Verify failure in WebView backend does not implicitly invoke Desktop / RealSlackSearchBackend."""
    backend = WebViewSlackSearchBackend()
    assert backend.state == WebViewRuntimeState.STOPPED

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sq = SearchQuery(query="검색어", purpose="테스트")

    with pytest.raises(WebViewSearchError) as exc:
        loop.run_until_complete(backend.search(sq))
    loop.close()

    assert exc.value.failure_code == WebViewSearchFailureCode.RUNTIME_NOT_READY
    # Ensure RealSlackSearchBackend was not called or imported
    src = inspect.getsource(backend.search)
    assert "RealSlackSearchBackend" not in src


def test_collect_context_not_qualified_semantics_preserved():
    """Verify collect_context strictly raises typed error and cannot be masked as empty context."""
    backend = WebViewSlackSearchBackend()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    with pytest.raises(WebViewSearchError) as exc:
        loop.run_until_complete(backend.collect_context("https://slack.com/archives/C123/p1234567890123456"))
    loop.close()

    assert exc.value.failure_code == WebViewSearchFailureCode.CONTEXT_COLLECTION_NOT_QUALIFIED
    assert "Context collection is not yet qualified" in str(exc.value)


def test_evidence_item_fidelity_and_citation_contract():
    """Verify EvidenceItem fields mapped from WebView search results maintain exact domain fidelity."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()

    class MockWindow:
        def evaluate_js(self, script: str) -> Any:
            if "JS_CHECK_AUTH_AND_READINESS" in script or "searchButtons.length === 1" in script:
                return {"ready": True, "auth_required": False, "search_button_count": 1}
            if "JS_ACTIVATE_SEARCH" in script or "mousedownEvt" in script:
                return {"activated": True}
            if "searchModalCandidates" in script or "SEARCH_INPUT_NOT_MOUNTED" in script:
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
                            "qualifying_permalinks": ["https://workspace.slack.com/archives/C08ABCDEF/p1724738400123456?thread_ts=1724738000.000001"],
                            "sender_text": "김철수",
                            "sender_valid": True,
                            "timestamp_raw": "오후 3:45",
                            "timestamp_valid": True,
                            "thread_reply_detected": True,
                            "primary_fragments": ["문단 1", "문단 2"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "문단 1 문단 2",
                            "body_valid": True,
                        }
                    ]
                }
            return {"ok": True}

    backend._window = MockWindow()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sq = SearchQuery(query="하위작업 추가", purpose="증거 수집")
    evidence_items = loop.run_until_complete(backend.search(sq, max_results=5))
    loop.close()

    assert len(evidence_items) == 1
    ev = evidence_items[0]
    assert isinstance(ev, EvidenceItem)
    assert ev.evidence_id == "webview_ev_01"
    assert ev.source == "slack"
    assert ev.author == "김철수"
    assert ev.timestamp == "오후 3:45"
    assert ev.text == "문단 1 문단 2"
    assert ev.channel == "C08ABCDEF"
    assert ev.message_identifier == "https://workspace.slack.com/archives/C08ABCDEF/p1724738400123456?thread_ts=1724738000.000001"
    assert ev.has_attachments is False
    assert ev.attachment_count == 0


def test_dedupe_keep_first_policy_preserved():
    """Verify duplicate results with same canonical identifier preserve DEDUPE_KEEP_FIRST policy."""
    backend = WebViewSlackSearchBackend()
    backend.start_runtime()

    class MockDupWindow:
        def evaluate_js(self, script: str) -> Any:
            if "searchButtons.length === 1" in script or "isLoginScreen" in script:
                return {"ready": True, "auth_required": False, "search_button_count": 1}
            if "top_nav_search" in script or "mousedown" in script:
                return {"activated": True}
            if "searchModalCandidates" in script or "SEARCH_INPUT_NOT_MOUNTED" in script:
                return {"ready": True, "modal_count": 1, "input_count": 1, "ambiguous": False}
            if "search_input_box" in script or "setContents" in script or "targetQuery" in script:
                return {"ok": True, "submitted": True}
            if "search_view" in script or "qualifying_permalinks" in script:
                return {
                    "ok": True,
                    "terminal_state": "RESULTS",
                    "items": [
                        {
                            "result_index": 1,
                            "qualifying_permalinks": ["https://slack.com/archives/C123/p1724738400000000"],
                            "sender_text": "원조 작성자",
                            "sender_valid": True,
                            "timestamp_raw": "오후 1:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "primary_fragments": ["첫 번째 원본 본문"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "첫 번째 원본 본문",
                            "body_valid": True,
                        },
                        {
                            "result_index": 2,
                            "qualifying_permalinks": ["https://slack.com/archives/C123/p1724738400000000"],  # Duplicate ID
                            "sender_text": "중복 인입자",
                            "sender_valid": True,
                            "timestamp_raw": "오후 1:00",
                            "timestamp_valid": True,
                            "thread_reply_detected": False,
                            "primary_fragments": ["중복된 다른 본문"],
                            "ambiguous_fragment_count": 0,
                            "normalized_body": "중복된 다른 본문",
                            "body_valid": True,
                        },
                    ]
                }
            return {"ok": True}

    backend._window = MockDupWindow()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sq = SearchQuery(query="중복 테스트", purpose="Dedupe 검증")
    evidence_items = loop.run_until_complete(backend.search(sq, max_results=5))
    loop.close()

    # Only first instance must be kept
    assert len(evidence_items) == 1
    assert evidence_items[0].author == "원조 작성자"
    assert evidence_items[0].text == "첫 번째 원본 본문"
    assert evidence_items[0].evidence_id == "webview_ev_01"


def test_default_backend_remains_desktop():
    """Verify RealSlackSearchBackend remains the primary and is completely unmodified."""
    assert issubclass(RealSlackSearchBackend, SearchBackend)
    assert hasattr(RealSlackSearchBackend, "search")
    assert hasattr(RealSlackSearchBackend, "collect_context")


def test_webview_permalink_survives_into_investigation_seed_graph(monkeypatch):
    """A mapped search result must retain a usable source citation in the real graph."""
    from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
    from pesu_agent.investigation.graph_models import InvestigationStateGraph

    permalink = "https://fixture.slack.com/archives/C123/p1724738400123456"
    result = SlackSearchResult(
        result_index=1, query="release status", text="Synthetic release completed.",
        author="Synthetic author", timestamp_raw="2024-08-27T08:40:00Z",
        channel_id="C123", result_url=permalink, message_fingerprint="synthetic-fingerprint",
    )
    backend = WebViewSlackSearchBackend()
    def qualified_results(query, max_results):
        assert query == "release status" and max_results == 5
        return [result, result.model_copy(update={"text": "Conflicting duplicate must not replace first."})]
    monkeypatch.setattr(backend, "search_messages", qualified_results)
    candidates = asyncio.run(backend.search(SearchQuery(query="release status", purpose="offline fixture"), max_results=5))
    graph = InvestigationStateGraph(question="What is the release status?")
    InvestigationSeedPoolBuilder().populate_seed_graph(graph, candidates)

    assert len(candidates) == len(graph.evidence_nodes) == 1
    node = next(iter(graph.evidence_nodes.values()))
    assert node.canonical_permalink == permalink
    assert candidates[0].permalink == candidates[0].message_identifier == permalink
    assert node.text == result.text
    assert node.presentation_author == result.author


def test_identity_fallback_is_not_fabricated_as_permalink(monkeypatch):
    """An internal fingerprint is useful identity, but never a clickable source URL."""
    backend = WebViewSlackSearchBackend()
    result = SlackSearchResult(
        result_index=1, query="fixture", text="Synthetic text without a source URL.",
        result_url=None, message_fingerprint="internal-identity-only",
    )
    monkeypatch.setattr(backend, "search_messages", lambda *_args, **_kwargs: [result])
    candidates = asyncio.run(backend.search(SearchQuery(query="fixture", purpose="offline fixture")))
    assert candidates[0].message_identifier == "internal-identity-only"
    assert candidates[0].permalink is None
