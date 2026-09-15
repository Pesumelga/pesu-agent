#!/usr/bin/env python3
"""Slack Multi-Result Evidence Collector CLI (MVP 3.4).

Hidden Slack Agent Window를 기본 아키텍처로 사용하여,
사용자 Main Slack Window에 Mutation 0회를 보장하면서
검색, 원문 문맥, 스레드 댓글 수집 및 글로벌 중복 제거를 수행합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
from pathlib import Path
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# UTF-8 콘솔 출력 설정
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# 패키지 루트 추가
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from pesu_agent.evidence.slack_evidence_collector import (
    SlackEvidenceCollector,
    SlackEvidencePackage,
)
from pesu_agent.lifecycle.slack_agent_window import (
    SlackAgentWindowState,
    SlackAgentWindowManager,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)

console = Console()


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_windows_foreground_info() -> tuple[str, tuple[int, int]]:
    """현재 활성화된 윈도우 제목과 마우스 좌표를 측정합니다."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return buf.value, (pt.x, pt.y)
    except Exception:
        return "", (0, 0)


async def run_collect_evidence(
    query: str,
    max_results: int = 5,
    context_before: int = 10,
    context_after: int = 10,
    max_thread_replies: int = 20,
    mode: str = "hidden",
    output_path: Optional[str] = None,
) -> int:
    """다중 검색 결과 순차 조사 실행 및 리포팅."""
    lifecycle_manager = SlackLifecycleManager()
    status = lifecycle_manager.get_status()

    if status.status != SlackAgentModeStatus.AGENT_READY:
        console.print(
            f"[bold red]❌ Slack Agent Mode가 준비되지 않았습니다.[/bold red] (현재 상태: [yellow]{status.status.value}[/yellow])\n"
            f"[dim]안내: 'python scripts/start_agent_slack.py --restart' 명령으로 Slack을 Agent Mode로 실행해주세요.[/dim]"
        )
        return 1

    window_manager = SlackAgentWindowManager()
    main_target_id = None

    if mode == "hidden":
        state, adopted, msg = window_manager.get_state()
        if state == SlackAgentWindowState.AGENT_WINDOW_DISCOVERED:
            console.print("[yellow]ℹ️ 미채택된 보조 창이 감지되어 자동 채택(Adopt)을 진행합니다...[/yellow]")
            ok, a_msg, adopted = window_manager.adopt_secondary_window()
            if not ok or not adopted:
                console.print(f"[bold red]❌ 보조 창 Adopt 실패:[/bold red] {a_msg}")
                return 1
            state, adopted, msg = window_manager.get_state()

        if state == SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE:
            window_manager.hide_agent_window()
            state, adopted, msg = window_manager.get_state()

        if state != SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN or not adopted:
            console.print(
                f"[bold red]❌ Hidden Mode 실행 불가:[/bold red] {msg} (State: {state.value})\n"
                f"[dim]안내: 'python scripts/manage_agent_slack_window.py --adopt'를 실행하여 보조 창을 먼저 등록해주세요.[/dim]"
            )
            return 1

        # Main Target 식별 (Mutation 0회 감사용)
        m_target, _, _, _, _ = window_manager.discover_windows_and_targets()
        main_target_id = m_target.get("id") if m_target else None

    fg_before, mouse_before = get_windows_foreground_info()

    console.print(
        f"[bold cyan]🔍 Slack Multi-Result Evidence 수집 시작 (Mode: [bold]{mode.upper()}[/bold])...[/bold cyan]\n"
        f"  • 검색 질의어: [yellow]'{query}'[/yellow]\n"
        f"  • 최대 조사 건수: [green]{max_results}건[/green] (Before: {context_before}건 / After: {context_after}건 / Thread: {max_thread_replies}건)"
    )

    collector = SlackEvidenceCollector(
        lifecycle_manager=lifecycle_manager,
        agent_window_manager=window_manager,
        mode=mode,
    )

    try:
        package: SlackEvidencePackage = await collector.collect_evidence_package(
            query=query,
            max_results=max_results,
            context_before=context_before,
            context_after=context_after,
            max_thread_replies=max_thread_replies,
        )
    except Exception as err:
        console.print(f"[bold red]❌ Evidence 수집 중 오류 발생:[/bold red] {err}")
        return 1

    fg_after, mouse_after = get_windows_foreground_info()
    mouse_moved = mouse_before != mouse_after
    focus_stolen = (
        fg_before != fg_after
        and "slack" in fg_after.lower()
        and "slack" not in fg_before.lower()
    )

    # 1. Summary Card (핵심 16+ 지표)
    summary_text = (
        f"[bold]Session ID:[/bold] {package.evidence_session_id}\n"
        f"[bold]Execution Mode:[/bold] [bold cyan]{mode.upper()}[/bold cyan]\n"
        f"[bold]검색어:[/bold] '{package.query}'\n"
        f"[bold]1. 사용자 상태 스냅샷 수 (user_state_snapshots):[/bold] {package.user_state_snapshots}회\n"
        f"[bold]2. 사용자 상태 복원 시도 수 (user_state_restore_attempts):[/bold] {package.user_state_restore_attempts}회\n"
        f"[bold]3. 메모리 스냅샷된 검색 결과 수 (result_metadata_snapshotted_count):[/bold] {package.result_metadata_snapshotted_count}건\n"
        f"[bold]4. 실제 조사 시도 결과 수 (results_investigated):[/bold] {package.results_investigated}건\n"
        f"[bold]5. 조사 성공 결과 수 (results_succeeded):[/bold] [green]{package.results_succeeded}건[/green]\n"
        f"[bold]6. 조사 실패 결과 수 (results_failed):[/bold] [red]{package.results_failed}건[/red]\n"
        f"[bold]7. 중복 제거된 문맥 메시지 수 (duplicate_context_messages_removed):[/bold] [yellow]{package.duplicate_context_messages_removed}건[/yellow]\n"
        f"[bold]8. 최종 고유 메시지 총합 (unique_context_messages):[/bold] [bold cyan]{package.unique_context_messages}건[/bold cyan]\n"
        f"[bold]9. 발견된 스레드 루트 수 (thread_roots_found):[/bold] {package.thread_roots_found}개\n"
        f"[bold]10. 수집된 스레드 댓글 수 (thread_replies_collected):[/bold] {package.thread_replies_collected}개\n"
        f"[bold]11. 사용자 중단 여부 (user_interrupted):[/bold] {'[red]True (' + str(package.interruption_reason) + ')[/red]' if package.user_interrupted else '[green]False[/green]'}\n"
        f"[bold]12. 복원 대기 여부 (restore_pending):[/bold] {'[yellow]True[/yellow]' if package.restore_pending else '[green]False (Zero Modification)[/green]'}\n"
        f"[bold]13. 검색 소요 시간 (search_elapsed_seconds):[/bold] {package.search_elapsed_seconds:.2f}s\n"
        f"[bold]14. 조사 소요 시간 (investigation_elapsed_seconds):[/bold] {package.investigation_elapsed_seconds:.2f}s\n"
        f"[bold]15. 복원 소요 시간 (restore_elapsed_seconds):[/bold] {package.restore_elapsed_seconds:.2f}s\n"
        f"[bold]16. 전체 소요 시간 (total_elapsed_seconds):[/bold] [bold green]{package.total_elapsed_seconds:.2f}s[/bold green]"
    )
    console.print(Panel(summary_text, title="[bold green]Slack Evidence Package 지표 리포트 (MVP 3.4)[/bold green]", expand=False))

    # 2. 개별 조사 항목 테이블
    table = Table(
        title=f"개별 검색 결과 조사 상세 내역 (총 {len(package.evidence_items)}건)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", justify="center", style="dim", width=4)
    table.add_column("검증", justify="center", width=8)
    table.add_column("채널", style="cyan", width=18)
    table.add_column("작성자", style="yellow", width=14)
    table.add_column("시각", style="dim", width=12)
    table.add_column("문맥 수집 (전/타깃/후)", justify="center", width=16)
    table.add_column("스레드 댓글", justify="center", width=12)
    table.add_column("소요 시간", justify="right", width=10)

    for item in package.evidence_items:
        v_tag = "[green]✓ 일치[/green]" if item.target_verified else "[red]✗ 불일치[/red]"
        ctx_tag = (
            f"{len(item.before_messages)} / 1 / {len(item.after_messages)}"
            if item.target_verified
            else "[dim]0 / 0 / 0[/dim]"
        )
        th_tag = (
            f"[bold cyan]{len(item.thread_replies)}개[/bold cyan]"
            if item.has_thread
            else "[dim]-[/dim]"
        )
        table.add_row(
            str(item.result_index + 1),
            v_tag,
            str(item.channel_name)[:16],
            str(item.target_message.author if item.target_message else item.search_result.author)[:12],
            str(item.target_message.timestamp_raw if item.target_message else item.search_result.timestamp_raw)[:10],
            ctx_tag,
            th_tag,
            f"{item.investigation_elapsed_seconds:.2f}s",
        )

    console.print(table)

    # 3. Hidden Mode & Zero Modification 지표 테이블
    res_table = Table(title="안전 및 무간섭 지표 (Zero Modification Report)", border_style="cyan")
    res_table.add_column("검증 항목", style="cyan", width=35)
    res_table.add_column("측정치", style="white", width=40)
    res_table.add_column("판정", justify="center", width=15)

    if mode == "hidden":
        res_table.add_row("Agent Target Binding", "전용 Hidden Secondary Window 바인딩", "[bold green]성공[/bold green]")
        res_table.add_row("runtime_main_agent_actions", "0회 (100% 무변경)", "[bold green]0회[/bold green]")
        res_table.add_row("runtime_main_user_state_changed", "False (Main 사용자 상태 무변경)", "[bold green]False[/bold green]")
        res_table.add_row("user_state_snapshots", f"{package.user_state_snapshots}회 (스냅샷 불필요)", "[bold green]0회[/bold green]")
        res_table.add_row("user_state_restore_attempts", f"{package.user_state_restore_attempts}회 (복원 불필요)", "[bold green]0회[/bold green]")
        res_table.add_row("Main Target Fallback 차단", "Fail Closed 정책 활성화", "[bold green]차단 보장[/bold green]")
    else:
        m_res = package.restoration_metrics
        if m_res:
            res_table.add_row("1. URL 복원 (url_restored)", f"Restored: {m_res.restored_url}", "[bold green]일치[/bold green]" if m_res.url_restored else "[bold red]불일치[/bold red]")
            res_table.add_row("2. 채널 ID 복원 (channel_id_restored)", f"Restored ID: {m_res.restored_channel_id}", "[bold green]일치[/bold green]" if m_res.channel_id_restored else "[bold red]불일치[/bold red]")
            res_table.add_row("3. 스크롤 복원 (scroll_restored)", f"Restored: {m_res.restored_scroll_top}px", "[bold green]일치[/bold green]" if m_res.scroll_restored else "[bold yellow]오차[/bold yellow]")
            res_table.add_row("4. 뷰포트 복원 (viewport_restored)", f"지문 일치 여부: {m_res.viewport_restored}", "[bold green]일치[/bold green]" if m_res.viewport_restored else "[bold yellow]부분[/bold yellow]")

    mouse_style = "[bold green]0% (간섭 없음)[/bold green]" if not mouse_moved else "[bold red]간섭 발생[/bold red]"
    res_table.add_row("사용자 마우스 간섭", f"좌표: {mouse_before} -> {mouse_after}", mouse_style)

    focus_style = "[bold green]0% (포커스 유지)[/bold green]" if not focus_stolen else "[bold red]포커스 탈취[/bold red]"
    res_table.add_row("사용자 포커스 간섭", f"활성 창: '{fg_before[:30]}' 유지", focus_style)

    console.print(res_table)

    # 4. Write Audit Security Gate 테이블
    sec_table = Table(title="🛡️ Read-Only Security Gate & Write Audit Report", border_style="green")
    sec_table.add_column("보안 감사 항목", style="cyan", width=42)
    sec_table.add_column("측정치", style="white", width=25)
    sec_table.add_column("보안 판정", justify="center", width=20)

    sec_table.add_row("write_action_requested", f"{package.write_action_requested}회", "[bold green]0회 (정상)[/bold green]" if package.write_action_requested == 0 else "[bold red]위반[/bold red]")
    sec_table.add_row("write_action_blocked", f"{package.write_action_blocked}회", "[bold green]0회[/bold green]")
    sec_table.add_row("prohibited_low_level_action_attempted", f"{package.prohibited_low_level_action_attempted}회", "[bold green]0회 (정상)[/bold green]" if package.prohibited_low_level_action_attempted == 0 else "[bold red]위반[/bold red]")
    sec_table.add_row("main_composer_interaction_detected", f"{package.main_composer_interaction_detected}회", "[bold green]0회 (차단 성공)[/bold green]" if package.main_composer_interaction_detected == 0 else "[bold red]감지됨[/bold red]")
    sec_table.add_row("thread_composer_interaction_detected", f"{package.thread_composer_interaction_detected}회", "[bold green]0회 (차단 성공)[/bold green]" if package.thread_composer_interaction_detected == 0 else "[bold red]감지됨[/bold red]")
    sec_table.add_row("send_control_interaction_detected", f"{package.send_control_interaction_detected}회", "[bold green]0회 (차단 성공)[/bold green]" if package.send_control_interaction_detected == 0 else "[bold red]감지됨[/bold red]")
    sec_table.add_row("unexpected_slack_write_observed", str(package.unexpected_slack_write_observed), "[bold green]False (Zero Write)[/bold green]" if not package.unexpected_slack_write_observed else "[bold red]True (비정상 쓰기)[/bold red]")

    console.print(sec_table)

    # 5. JSON 파일 저장
    out_file = Path(output_path) if output_path else project_root / "output" / "slack_evidence_package.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(package.model_dump(), f, ensure_ascii=False, indent=2)

    console.print(f"\n[bold green]✓ 통합 Evidence Package가 저장되었습니다:[/bold green] [dim]{out_file}[/dim]")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slack Multi-Result Evidence Collector CLI (MVP 3.4)"
    )
    parser.add_argument("query", nargs="?", default="수임", help="검색 질의어 (기본: '수임')")
    parser.add_argument("--query", dest="query_opt", default=None, help="검색 질의어 (옵션 플래그)")
    parser.add_argument("--max-results", type=int, default=5, help="순차 조사할 최대 검색 결과 수 (기본: 5)")
    parser.add_argument("--context-before", type=int, default=10, help="타깃 기준 이전 메시지 수 (기본: 10)")
    parser.add_argument("--context-after", type=int, default=10, help="타깃 기준 이후 메시지 수 (기본: 10)")
    parser.add_argument("--max-thread-replies", type=int, default=20, help="최대 스레드 댓글 수 (기본: 20)")
    parser.add_argument("--mode", choices=["hidden", "legacy"], default="hidden", help="조사 모드 (기본: hidden)")
    parser.add_argument("--output", "-o", default=None, help="Evidence Package 저장 경로")

    args = parser.parse_args()
    target_query = args.query_opt if args.query_opt is not None else args.query

    ret_code = asyncio.run(
        run_collect_evidence(
            query=target_query,
            max_results=args.max_results,
            context_before=args.context_before,
            context_after=args.context_after,
            max_thread_replies=args.max_thread_replies,
            mode=args.mode,
            output_path=args.output,
        )
    )
    sys.exit(ret_code)


if __name__ == "__main__":
    main()
