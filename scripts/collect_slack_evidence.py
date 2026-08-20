#!/usr/bin/env python3
"""Slack Multi-Result Evidence Collector CLI (MVP 3.2).

하나의 검색어에 대해 복수의 검색 결과를 순차 조사하여,
원문, 전후 대화 문맥, 스레드 댓글을 수집하고 단일 사용자 상태 복원 및
글로벌 중복 제거를 적용한 통합 Evidence Package를 생성합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import sys
from pathlib import Path
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
    output_path: Optional[str] = None,
) -> int:
    """다중 검색 결과 순차 조사 실행 및 리포팅."""
    manager = SlackLifecycleManager()
    status = manager.get_status()

    if status.status != SlackAgentModeStatus.AGENT_READY:
        console.print(
            f"[bold red]❌ Slack Agent Mode가 준비되지 않았습니다.[/bold red] (현재 상태: [yellow]{status.status.value}[/yellow])\n"
            f"[dim]안내: 'python scripts/start_agent_slack.py --restart' 명령으로 Slack을 Agent Mode로 실행해주세요.[/dim]"
        )
        return 1

    fg_before, mouse_before = get_windows_foreground_info()

    console.print(
        f"[bold cyan]🔍 Slack Multi-Result Evidence 수집 시작 (MVP 3.2)...[/bold cyan]\n"
        f"  • 검색 질의어: [yellow]'{query}'[/yellow]\n"
        f"  • 최대 조사 건수: [green]{max_results}건[/green] (Before: {context_before}건 / After: {context_after}건 / Thread: {max_thread_replies}건)"
    )

    collector = SlackEvidenceCollector(lifecycle_manager=manager)
    try:
        package: SlackEvidencePackage = await collector.collect_evidence_package(
            query=query,
            max_results=max_results,
            context_before=context_before,
            context_after=context_after,
            max_thread_replies=max_thread_replies,
        )
    except Exception as err:
        console.print(f"[bold red]❌ Evidence 수집 중 치명적 오류 발생:[/bold red] {err}")
        return 1

    fg_after, mouse_after = get_windows_foreground_info()
    mouse_moved = mouse_before != mouse_after
    focus_stolen = (
        fg_before != fg_after
        and "slack" in fg_after.lower()
        and "slack" not in fg_before.lower()
    )

    # 1. Summary Card (16대 핵심 지표)
    summary_text = (
        f"[bold]Session ID:[/bold] {package.evidence_session_id}\n"
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
        f"[bold]11. 사용자 중단 여부 (user_interrupted):[/bold] {'[red]True (user_opened_slack)[/red]' if package.user_interrupted else '[green]False[/green]'}\n"
        f"[bold]12. 복원 대기 여부 (restore_pending):[/bold] {'[yellow]True (Foreground 활성화로 보류)[/yellow]' if package.restore_pending else '[green]False[/green]'}\n"
        f"[bold]13. 검색 소요 시간 (search_elapsed_seconds):[/bold] {package.search_elapsed_seconds:.2f}s\n"
        f"[bold]14. 조사 소요 시간 (investigation_elapsed_seconds):[/bold] {package.investigation_elapsed_seconds:.2f}s\n"
        f"[bold]15. 복원 소요 시간 (restore_elapsed_seconds):[/bold] {package.restore_elapsed_seconds:.2f}s\n"
        f"[bold]16. 전체 소요 시간 (total_elapsed_seconds):[/bold] [bold green]{package.total_elapsed_seconds:.2f}s[/bold green]"
    )
    console.print(Panel(summary_text, title="[bold green]Slack Evidence Package 16대 지표 리포트 (MVP 3.2)[/bold green]", expand=False))

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
            else f"[dim]{item.failure_reason or '실패'}[/dim]"
        )
        th_tag = (
            f"[bold yellow]{len(item.thread_replies)}개 댓글[/bold yellow]"
            if item.has_thread
            else "[dim]없음[/dim]"
        )
        cand = item.search_result
        table.add_row(
            str(item.result_index + 1),
            v_tag,
            cand.channel_name or "-",
            cand.author or "-",
            cand.timestamp_raw or "-",
            ctx_tag,
            th_tag,
            f"{item.investigation_elapsed_seconds:.2f}s",
        )

    console.print(table)

    # 3. 상태 복원 및 간섭 리포트
    res_table = Table(title="무간섭 및 상태 복원 정밀 검증 리포트", show_header=True, header_style="bold blue")
    res_table.add_column("검증 항목", style="cyan", width=30)
    res_table.add_column("측정값", style="white")
    res_table.add_column("상태", justify="center", width=14)

    m_res = package.restoration_metrics
    if m_res:
        u_style = "[bold green]일치[/bold green]" if m_res.url_restored else "[bold red]불일치[/bold red]"
        res_table.add_row("1. URL 복원 (url_restored)", f"Restored: {m_res.restored_url}", u_style)

        c_style = "[bold green]일치[/bold green]" if m_res.conversation_restored else "[bold red]불일치[/bold red]"
        res_table.add_row("2. 채널 복원 (conv_restored)", f"Restored: {m_res.restored_conversation_name}", c_style)

        s_style = "[bold green]일치[/bold green]" if m_res.scroll_restored else "[bold yellow]오차[/bold yellow]"
        res_table.add_row("3. 스크롤 복원 (scroll_restored)", f"Restored: {m_res.restored_scroll_top}px", s_style)

        v_style = "[bold green]일치[/bold green]" if m_res.viewport_restored else "[bold yellow]부분[/bold yellow]"
        res_table.add_row("4. 뷰포트 복원 (viewport_restored)", f"지문 일치 여부: {m_res.viewport_restored}", v_style)

    st_style = "[bold green]성공[/bold green]" if package.state_restore_succeeded else "[bold red]실패[/bold red]"
    res_table.add_row("최종 상태 복원 (state_restore_succeeded)", f"성공 여부: {package.state_restore_succeeded}", st_style)

    mouse_style = "[bold green]0% (간섭 없음)[/bold green]" if not mouse_moved else "[bold red]간섭 발생[/bold red]"
    res_table.add_row("사용자 마우스 간섭", f"좌표: {mouse_before} -> {mouse_after}", mouse_style)

    focus_style = "[bold green]0% (포커스 유지)[/bold green]" if not focus_stolen else "[bold red]포커스 탈취[/bold red]"
    res_table.add_row("사용자 포커스 간섭", f"활성 창: '{fg_before[:30]}' 유지", focus_style)

    console.print(res_table)

    # 4. JSON 파일 저장
    out_file = Path(output_path) if output_path else project_root / "output" / "slack_evidence_package.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(package.model_dump(), f, ensure_ascii=False, indent=2)

    console.print(f"\n[bold green]✓ 통합 Evidence Package가 저장되었습니다:[/bold green] [dim]{out_file}[/dim]")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slack Multi-Result Evidence Collector CLI (MVP 3.2)"
    )
    parser.add_argument("query", nargs="?", default="수임", help="검색 질의어 (기본: '수임')")
    parser.add_argument("--query", dest="query_opt", default=None, help="검색 질의어 (옵션 플래그)")
    parser.add_argument("--max-results", type=int, default=5, help="순차 조사할 최대 검색 결과 수 (기본: 5)")
    parser.add_argument("--context-before", type=int, default=10, help="타깃 기준 이전 메시지 수 (기본: 10)")
    parser.add_argument("--context-after", type=int, default=10, help="타깃 기준 이후 메시지 수 (기본: 10)")
    parser.add_argument("--max-thread-replies", type=int, default=20, help="최대 스레드 댓글 수 (기본: 20)")
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
            output_path=args.output,
        )
    )
    sys.exit(ret_code)


if __name__ == "__main__":
    main()
