"""Unit tests for SlackSearch and Freshness Guard (MVP 3.0.1)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from pesu_agent.adapters.slack_cdp import (
    SlackCdpAdapter,
    SlackCdpError,
    SlackNotReadyError,
    SlackTargetNotFoundError,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)
from pesu_agent.search.slack_search import (
    SlackSearch,
    SlackSearchResult,
    SlackSearchSession,
    SlackSearchStaleError,
)


@pytest.mark.anyio
async def test_search_blocked_when_not_agent_ready():
    """1. Agent Ready가 아닌 경우 검색 차단."""
    mock_lifecycle = MagicMock(spec=SlackLifecycleManager)
    mock_lifecycle.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.RESTART_REQUIRED,
        message="재시작 필요",
        cdp_port=9222,
    )

    searcher = SlackSearch(lifecycle_manager=mock_lifecycle)
    with pytest.raises(SlackNotReadyError) as exc_info:
        await searcher.search(query="테스트")

    assert "Agent Mode로 준비되지 않았습니다" in str(exc_info.value)


@pytest.mark.anyio
async def test_open_search_ui_success():
    """2. 방어적 다중 셀렉터로 검색 UI 열기 성공."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)
    mock_cdp.evaluate_js = AsyncMock(return_value={"ok": True, "via": "click_btn", "qa": "top_nav_search"})

    searcher = SlackSearch(cdp_adapter=mock_cdp)
    res = await searcher.open_search_ui()
    assert res is True
    mock_cdp.evaluate_js.assert_called_once()


@pytest.mark.anyio
async def test_enter_query_and_search_execution():
    """3. 검색어 입력 및 CDP Enter 키 디스패치 실행."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)
    mock_cdp.evaluate_js = AsyncMock(return_value={"ok": True, "value": "영업지원", "qa": "top_nav_search__input"})
    mock_cdp.dispatch_key_event = AsyncMock()

    searcher = SlackSearch(cdp_adapter=mock_cdp)
    res = await searcher.enter_query_and_search("영업지원")
    assert res.get("ok") is True
    assert mock_cdp.dispatch_key_event.call_count == 2  # rawKeyDown + keyUp


@pytest.mark.anyio
async def test_parse_current_visible_results_with_channel_id():
    """4. 검색 결과 파싱 (작성자, 시간, 본문, 실제 채널명, 채널 ID, 퍼머링크)."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)
    mock_cdp.evaluate_js = AsyncMock(
        return_value=[
            {
                "idx": 0,
                "channel_name": "team-tax",
                "channel_id": "C03K8UX2074",
                "sender": "김철수",
                "time": "오후 2:30",
                "text": "@이영희 영업지원 리드 배분 건입니다. https://example.com/lead",
                "permalink": "https://slack.com/archives/C03K8UX2074/p456",
                "item_id": "msg_001",
            },
            {
                "idx": 1,
                "channel_name": None,
                "channel_id": None,
                "sender": "박민수",
                "time": "오전 11:00",
                "text": "오늘 회의 일정 공유합니다.",
                "permalink": None,
                "item_id": "msg_002",
            },
        ]
    )

    searcher = SlackSearch(cdp_adapter=mock_cdp)
    results = await searcher.parse_current_visible_results(query="영업지원")

    assert len(results) == 2
    assert results[0].channel_name == "team-tax"
    assert results[0].channel_id == "C03K8UX2074"
    assert results[0].author == "김철수"
    assert results[0].timestamp_raw == "오후 2:30"
    assert "@이영희" in results[0].mentions
    assert "https://example.com/lead" in results[0].links
    assert results[0].result_url == "https://slack.com/archives/C03K8UX2074/p456"
    assert len(results[0].message_fingerprint) == 16

    # When channel name is null, it stays None (never 'Default')
    assert results[1].channel_name is None
    assert results[1].channel_id is None


@pytest.mark.anyio
async def test_search_freshness_guard_and_session_id():
    """5. Freshness Guard, Session ID, 쿼리 검증 확인."""
    mock_lifecycle = MagicMock(spec=SlackLifecycleManager)
    mock_lifecycle.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Ready",
        cdp_port=9222,
    )

    mock_cdp = MagicMock(spec=SlackCdpAdapter)
    mock_cdp.__aenter__ = AsyncMock(return_value=mock_cdp)
    mock_cdp.__aexit__ = AsyncMock(return_value=None)
    mock_cdp.evaluate_js = AsyncMock(return_value={"ready": True})
    mock_cdp.dispatch_key_event = AsyncMock()

    searcher = SlackSearch(cdp_adapter=mock_cdp, lifecycle_manager=mock_lifecycle)

    with patch.object(searcher, "open_search_ui", new_callable=AsyncMock, return_value=True), \
         patch.object(searcher, "enter_query_and_search", new_callable=AsyncMock, return_value={"ok": True}), \
         patch.object(searcher, "get_observed_query_state", new_callable=AsyncMock, return_value=("수임", False)), \
         patch.object(searcher, "scroll_search_results", new_callable=AsyncMock, return_value=False), \
         patch.object(searcher, "parse_current_visible_results", new_callable=AsyncMock, return_value=[
             SlackSearchResult(
                 result_index=1, query="수임", author="A", timestamp_raw="1:00", text="수임 완료",
                 channel_name="noti", result_url="url1", message_fingerprint="fp1"
             )
         ]):

        session: SlackSearchSession = await searcher.search(query="수임", max_scrolls=0)
        assert session.requested_query == "수임"
        assert session.observed_query == "수임"
        assert session.query_verified is True
        assert session.result_freshness_verified is True
        assert len(session.search_session_id) > 10
        assert session.query_literal_match_count == 1
        assert session.stale_result_suspected is False


@pytest.mark.anyio
async def test_search_zero_results():
    """6. 검색 결과가 0개인 경우 정상 처리."""
    mock_lifecycle = MagicMock(spec=SlackLifecycleManager)
    mock_lifecycle.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Ready",
        cdp_port=9222,
    )

    mock_cdp = MagicMock(spec=SlackCdpAdapter)
    mock_cdp.__aenter__ = AsyncMock(return_value=mock_cdp)
    mock_cdp.__aexit__ = AsyncMock(return_value=None)
    mock_cdp.evaluate_js = AsyncMock(return_value={"ready": True})
    mock_cdp.dispatch_key_event = AsyncMock()

    searcher = SlackSearch(cdp_adapter=mock_cdp, lifecycle_manager=mock_lifecycle)
    with patch.object(searcher, "open_search_ui", new_callable=AsyncMock, return_value=True), \
         patch.object(searcher, "enter_query_and_search", new_callable=AsyncMock, return_value={"ok": True}), \
         patch.object(searcher, "get_observed_query_state", new_callable=AsyncMock, return_value=("없는검색어", True)), \
         patch.object(searcher, "scroll_search_results", new_callable=AsyncMock, return_value=False), \
         patch.object(searcher, "parse_current_visible_results", new_callable=AsyncMock, return_value=[]):

        session = await searcher.search(query="없는검색어", max_scrolls=0)
        assert session.result_count == 0
        assert session.unique_result_count == 0
        assert session.query_verified is True
        assert session.result_freshness_verified is True


def test_target_not_found_error():
    """7. Slack target이 없는 경우 SlackTargetNotFoundError 발생."""
    adapter = SlackCdpAdapter(port=9222)
    with patch.object(adapter, "get_cdp_targets", return_value=[]):
        with pytest.raises(SlackTargetNotFoundError):
            adapter.find_slack_renderer_target()
