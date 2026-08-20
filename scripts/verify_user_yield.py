#!/usr/bin/env python3
"""Mid-operation User Yield Live Path Verification Script (MVP 3.2.1).

검증 A:
  사용자 상태 저장 -> Result 1 탐색 완료 -> 조사 도중 Slack Foreground 발생
  -> Agent 즉시 중단 -> restore_pending=True -> 사용자 계속 사용 -> Timeout
  Expected:
    user_interrupted = True
    state_restore_attempted = False
    restore_pending = True

검증 B:
  사용자 상태 저장 -> Result 1 탐색 완료 -> 조사 도중 Slack Foreground 발생
  -> Agent 즉시 중단 -> restore_pending=True -> Slack Background 전환 -> 원래 상태 복원
  Expected:
    user_interrupted = True
    state_restore_attempted = True
    state_restore_succeeded = True
    restore_pending = False
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

from rich.console import Console
from rich.table import Table

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from pesu_agent.context.slack_context_collector import (
    SlackRestorationResult,
    SlackContextCollector,
    SlackViewState,
)
from pesu_agent.evidence.slack_evidence_collector import (
    SlackEvidenceCollector,
)
from pesu_agent.search.slack_search import SlackSearchResult, SlackSearchSession

console = Console()


async def run_yield_verification():
    collector = SlackEvidenceCollector()

    console.print("🚀 [bold cyan]Mid-operation User Yield 경로 검증 시작 (MVP 3.2.1)[/bold cyan]\n")

    # 가상 검색 결과 2건 준비
    mock_results = [
        SlackSearchResult(
            result_index=1,
            query="테스트",
            author="테스터1",
            timestamp_raw="오후 1:00",
            text="첫 번째 결과 메시지",
            channel_id="C_TEST_1",
            channel_name="test-channel-1",
            result_url="https://heumlabs.slack.com/archives/C_TEST_1/p1787000000000001",
            context="search_result",
            message_fingerprint="fp1",
        ),
        SlackSearchResult(
            result_index=2,
            query="테스트",
            author="테스터2",
            timestamp_raw="오후 1:05",
            text="두 번째 결과 메시지",
            channel_id="C_TEST_2",
            channel_name="test-channel-2",
            result_url="https://heumlabs.slack.com/archives/C_TEST_2/p1787000000000002",
            context="search_result",
            message_fingerprint="fp2",
        ),
    ]
    mock_session = SlackSearchSession(
        searched_at="2026-08-20T17:00:00Z",
        requested_query="테스트",
        observed_query="테스트",
        query_verified=True,
        result_freshness_verified=True,
        result_signature="sig123",
        result_count=len(mock_results),
        unique_result_count=len(mock_results),
        results=mock_results,
    )

    # -------------------------------------------------------------
    # [검증 A: Timeout Case]
    # -------------------------------------------------------------
    console.print("=" * 70)
    console.print("📌 [bold yellow]검증 A: Foreground 발생 후 사용자 지속 사용 (Timeout)[/bold yellow]")
    console.print("=" * 70)

    # 시나리오 A:
    # 1) 시작 시 백그라운드 (is_slack_foreground = False)
    # 2) Result 1 이동 후 도중 foreground 전환 (is_slack_foreground = True)
    # 3) wait_for_slack_background -> timeout (False)
    fg_calls = 0

    def mock_fg_timeout():
        nonlocal fg_calls
        fg_calls += 1
        # 처음 시작 시(스냅샷 단계)는 background(False), 그 후(조사 도중)는 foreground(True)
        if fg_calls <= 1:
            return False
        return True

    mock_view_state = SlackViewState(
        url="https://app.slack.com/client/T/C_ORIGIN",
        channel_id="C_ORIGIN",
        conversation_name="origin-channel",
        scroll_top=1000,
    )

    with patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", side_effect=mock_fg_timeout):
        with patch.object(collector.searcher, "search", return_value=mock_session):
            with patch.object(collector.cdp, "connect", new_callable=AsyncMock):
                with patch.object(collector.cdp, "disconnect", new_callable=AsyncMock):
                    with patch.object(collector.cdp, "evaluate_js", return_value={"ok": True}):
                        with patch.object(collector.context_collector, "capture_view_state", new_callable=AsyncMock) as mock_snap:
                            mock_snap.return_value = mock_view_state
                            with patch.object(collector.context_collector, "wait_for_slack_background", new_callable=AsyncMock) as mock_wait_bg:
                                mock_wait_bg.return_value = False  # 사용자 계속 사용 (timeout)
                                
                                pkg_a = await collector.collect_evidence_package(
                                    query="테스트",
                                    max_results=2,
                                    bg_wait_timeout_sec=1.0,
                                    check_user_interference=True
                                )

    table_a = Table(title="검증 A 결과 요약 (Timeout Case)")
    table_a.add_column("항목", style="cyan")
    table_a.add_column("Expected", style="yellow")
    table_a.add_column("Actual", style="green")
    table_a.add_column("일치 여부", style="bold")

    m1 = pkg_a.user_interrupted == True
    table_a.add_row("user_interrupted", "True", str(pkg_a.user_interrupted), "✓ PASS" if m1 else "✗ FAIL")

    m2 = pkg_a.state_restore_attempted == False
    table_a.add_row("state_restore_attempted", "False", str(pkg_a.state_restore_attempted), "✓ PASS" if m2 else "✗ FAIL")

    m3 = pkg_a.restore_pending == True
    table_a.add_row("restore_pending", "True", str(pkg_a.restore_pending), "✓ PASS" if m3 else "✗ FAIL")

    console.print(table_a)

    # -------------------------------------------------------------
    # [검증 B: Resume and Restore Case]
    # -------------------------------------------------------------
    console.print("\n" + "=" * 70)
    console.print("📌 [bold yellow]검증 B: Foreground 발생 후 Background 복귀 (Resume & Restore)[/bold yellow]")
    console.print("=" * 70)

    # 시나리오 B:
    # 1) 시작 시 백그라운드 (is_slack_foreground = False)
    # 2) Result 1 이동 후 foreground 전환 감지 (is_slack_foreground = True)
    # 3) wait_for_slack_background -> 복귀 감지 (True)
    # 4) restore_view_state 정상 성공
    fg_calls_b = 0

    def mock_fg_resume():
        nonlocal fg_calls_b
        fg_calls_b += 1
        if fg_calls_b <= 1:
            return False
        return True

    mock_restored_metrics = SlackRestorationResult(
        url_restored=True,
        conversation_restored=True,
        scroll_restored=True,
        viewport_restored=True,
        restored_url="https://app.slack.com/client/T/C_ORIGIN",
        restored_channel_id="C_ORIGIN",
        restored_conversation_name="origin-channel",
        restored_scroll_top=1000,
        restored_visible_message_fingerprints=["fp_origin"]
    )

    with patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", side_effect=mock_fg_resume):
        with patch.object(collector.searcher, "search", return_value=mock_session):
            with patch.object(collector.cdp, "connect", new_callable=AsyncMock):
                with patch.object(collector.cdp, "disconnect", new_callable=AsyncMock):
                    with patch.object(collector.cdp, "evaluate_js", return_value={"ok": True}):
                        with patch.object(collector.context_collector, "capture_view_state", new_callable=AsyncMock) as mock_snap:
                            mock_snap.return_value = mock_view_state
                            with patch.object(collector.context_collector, "wait_for_slack_background", new_callable=AsyncMock) as mock_wait_bg:
                                mock_wait_bg.return_value = True  # 백그라운드로 복귀됨
                                with patch.object(collector.context_collector, "restore_view_state", new_callable=AsyncMock) as mock_restore:
                                    mock_restore.return_value = mock_restored_metrics

                                    pkg_b = await collector.collect_evidence_package(
                                        query="테스트",
                                        max_results=2,
                                        bg_wait_timeout_sec=5.0,
                                        check_user_interference=True
                                    )

    table_b = Table(title="검증 B 결과 요약 (Resume & Restore Case)")
    table_b.add_column("항목", style="cyan")
    table_b.add_column("Expected", style="yellow")
    table_b.add_column("Actual", style="green")
    table_b.add_column("일치 여부", style="bold")

    mb1 = pkg_b.user_interrupted == True
    table_b.add_row("user_interrupted", "True", str(pkg_b.user_interrupted), "✓ PASS" if mb1 else "✗ FAIL")

    mb2 = pkg_b.state_restore_attempted == True
    table_b.add_row("state_restore_attempted", "True", str(pkg_b.state_restore_attempted), "✓ PASS" if mb2 else "✗ FAIL")

    mb3 = pkg_b.state_restore_succeeded == True
    table_b.add_row("state_restore_succeeded", "True", str(pkg_b.state_restore_succeeded), "✓ PASS" if mb3 else "✗ FAIL")

    mb4 = pkg_b.restore_pending == False
    table_b.add_row("restore_pending", "False", str(pkg_b.restore_pending), "✓ PASS" if mb4 else "✗ FAIL")

    console.print(table_b)

    all_passed = m1 and m2 and m3 and mb1 and mb2 and mb3 and mb4
    if all_passed:
        console.print("\n[bold green]✨ 검증 A 및 검증 B 모두 100% 성공 (무간섭 정책 정확 준수 확인)[/bold green]")
        return 0
    else:
        console.print("\n[bold red]❌ 검증 항목 중 불일치 발생[/bold red]")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_yield_verification()))
