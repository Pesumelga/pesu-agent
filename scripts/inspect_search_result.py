#!/usr/bin/env python3
"""Slack Search Result Context Inspector CLI (MVP 3.1 & MVP 3.1.1).

Slack 검색 결과 중 특정 순번의 메시지를 선택하여 원문 대화 뷰로 이동하고,
타깃 검증, 전후 문맥 수집(최대 20건씩), 스레드 댓글 정보 추출, 및
원래 상태(URL/대화창/스크롤/뷰포트) 복원 결과를 측정하여 리포팅합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# UTF-8 콘솔 출력 설정
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# 패키지 루트 추가
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from pesu_agent.context.slack_context_collector import (
    SlackContextCollector,
    SlackMessageContext,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.search.slack_search import SlackSearch, SlackSearchSession

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


async def inspect_search_result_context(
    query: str,
    result_index: int = 0,
    max_before: int = 20,
    max_after: int = 20,
    output_path: Optional[str] = None,
) -> int:
    """검색 실행 후 지정된 인덱스의 결과 문맥을 수집하고 리포팅합니다."""
    manager = SlackLifecycleManager()
    status = manager.get_status()

    if status.status != SlackAgentModeStatus.AGENT_READY:
        console.print(
            f"[bold red]❌ Slack Agent Mode가 준비되지 않았습니다.[/bold red] (현재 상태: [yellow]{status.status.value}[/yellow])\n"
            f"[dim]안내: 'python scripts/start_agent_slack.py --restart' 명령으로 Slack을 Agent Mode로 실행해주세요.[/dim]"
        )
        return 1

    fg_title_before, mouse_before = get_windows_foreground_info()

    # 1. 전역 검색 실행
    console.print(f"[bold cyan]1. 전역 검색 실행 중...[/bold cyan] (Query: [yellow]'{query}'[/yellow])")
    searcher = SlackSearch(lifecycle_manager=manager)
    try:
        session: SlackSearchSession = await searcher.search(query=query, max_scrolls=0)
    except Exception as err:
        console.print(f"[bold red]❌ 검색 실행 중 오류 발생:[/bold red] {err}")
        return 1

    if not session.results:
        console.print(f"[bold yellow]⚠️ 검색 결과가 0건입니다.[/bold yellow] (Query: '{query}')")
        return 1

    if result_index < 0 or result_index >= len(session.results):
        console.print(
            f"[bold red]❌ 유효하지 않은 결과 인덱스입니다:[/bold red] {result_index} (총 {len(session.results)}건 수집됨)"
        )
        return 1

    target_result = session.results[result_index]

    # 선택된 결과 요약 카드
    target_summary = (
        f"[bold]Index:[/bold] {result_index} / {len(session.results) - 1}\n"
        f"[bold]채널:[/bold] {target_result.channel_name} ([dim]{target_result.channel_id}[/dim])\n"
        f"[bold]작성자:[/bold] {target_result.author}\n"
        f"[bold]시각:[/bold] {target_result.timestamp_raw}\n"
        f"[bold]내용:[/bold] {target_result.text[:120]}\n"
        f"[bold]퍼머링크:[/bold] [blue underline]{target_result.result_url}[/blue underline]"
    )
    console.print(Panel(target_summary, title="[bold green]선택된 검색 결과 항목[/bold green]", expand=False))

    # 2. 문맥 수집 및 원래 상태 복원 실행
    console.print("\n[bold cyan]2. 원문 대화 뷰로 이동하여 Target 검증 및 문맥 수집 중...[/bold cyan]")
    collector = SlackContextCollector(lifecycle_manager=manager)
    try:
        context: SlackMessageContext = await collector.collect_context(
            target_result=target_result,
            max_before=max_before,
            max_after=max_after,
        )
    except Exception as err:
        console.print(f"[bold red]❌ 문맥 수집 중 오류 발생:[/bold red] {err}")
        return 1

    fg_title_after, mouse_after = get_windows_foreground_info()
    mouse_moved = mouse_before != mouse_after
    focus_stolen = (
        fg_title_before != fg_title_after
        and "slack" in fg_title_after.lower()
        and "slack" not in fg_title_before.lower()
    )

    # 3. 대화 문맥 테이블 렌더링
    table = Table(
        title=f"대화 문맥 스트림 (Target 기준 앞 {context.before_count}건 / 뒤 {context.after_count}건)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("구분", justify="center", style="dim", width=12)
    table.add_column("작성자", style="cyan", width=16)
    table.add_column("시각", style="dim", width=12)
    table.add_column("메시지 본문 (일부)", style="white")

    # Before (최대 3건 샘플)
    for m in context.before_messages[:3]:
        table.add_row("Before", m.author or "-", m.timestamp_raw or "-", m.text[:80].replace("\n", " "))
    if len(context.before_messages) > 3:
        table.add_row("...", "...", "...", f"... (이전 메시지 총 {len(context.before_messages)}건 수집됨) ...")

    # Target
    if context.target_message:
        tm = context.target_message
        thread_tag = f" [bold yellow](댓글 {tm.reply_count}개)[/bold yellow]" if tm.has_thread else ""
        table.add_row(
            "[bold red]▶ TARGET[/bold red]",
            f"[bold red]{tm.author or '-'}[/bold red]",
            f"[bold red]{tm.timestamp_raw or '-'}[/bold red]",
            f"[bold red]{tm.text[:100].replace(chr(10), ' ')}{thread_tag}[/bold red]",
        )
    else:
        table.add_row("[bold red]▶ TARGET[/bold red]", "미확인", "-", "[dim]타깃 메시지 식별 실패[/dim]")

    # After (최대 3건 샘플)
    for m in context.after_messages[:3]:
        table.add_row("After", m.author or "-", m.timestamp_raw or "-", m.text[:80].replace("\n", " "))
    if len(context.after_messages) > 3:
        table.add_row("...", "...", "...", f"... (이후 메시지 총 {len(context.after_messages)}건 수집됨) ...")

    console.print(table)

    # 4. 세부 상태 복원 및 검증 테이블
    m_res = context.restoration_metrics
    res_table = Table(title="무간섭 및 상태 복원 검증 리포트 (MVP 3.1.1)", show_header=True, header_style="bold blue")
    res_table.add_column("검증 항목", style="cyan", width=32)
    res_table.add_column("측정값", style="white")
    res_table.add_column("상태", justify="center", width=14)

    # Target Verified
    t_style = "[bold green]성공[/bold green]" if context.target_verified else "[bold red]실패[/bold red]"
    res_table.add_row("Target Message Identity 검증", context.verification_reason, t_style)

    # State Restoration Detailed Metrics
    if m_res:
        u_style = "[bold green]일치[/bold green]" if m_res.url_restored else "[bold red]불일치[/bold red]"
        res_table.add_row("1. URL 복원 (url_restored)", f"Before: {context.before_state.url if context.before_state else '-'} -> Restored: {m_res.restored_url}", u_style)

        c_style = "[bold green]일치[/bold green]" if m_res.conversation_restored else "[bold red]불일치[/bold red]"
        res_table.add_row("2. 채널 복원 (conversation_restored)", f"Before: {context.before_state.conversation_name if context.before_state else '-'} -> Restored: {m_res.restored_conversation_name}", c_style)

        s_style = "[bold green]일치[/bold green]" if m_res.scroll_restored else "[bold yellow]오차[/bold yellow]"
        res_table.add_row("3. 스크롤 복원 (scroll_restored)", f"Before: {context.before_state.scroll_top if context.before_state else 0}px -> Restored: {m_res.restored_scroll_top}px", s_style)

        v_style = "[bold green]일치[/bold green]" if m_res.viewport_restored else "[bold yellow]부분[/bold yellow]"
        res_table.add_row("4. 뷰포트 복원 (viewport_restored)", f"지문 일치 여부: {m_res.viewport_restored}", v_style)

    # Overall Status
    st_style = "[bold green]성공[/bold green]" if context.state_restore_succeeded else "[bold red]실패[/bold red]"
    res_table.add_row("최종 상태 복원 (state_restore_succeeded)", f"Overall: {context.overall_status} (Reason: {context.interruption_reason or 'None'})", st_style)

    # Interference
    mouse_style = "[bold green]0% (간섭 없음)[/bold green]" if not mouse_moved else "[bold red]간섭 발생[/bold red]"
    res_table.add_row("사용자 마우스 간섭", f"좌표: {mouse_before} -> {mouse_after}", mouse_style)

    focus_style = "[bold green]0% (포커스 유지)[/bold green]" if not focus_stolen else "[bold red]포커스 탈취[/bold red]"
    res_table.add_row("사용자 포커스 간섭", f"활성 창: '{fg_title_before[:30]}' 유지", focus_style)

    # Thread Info
    th_style = "[bold green]발견[/bold green]" if context.has_thread else "[dim]없음[/dim]"
    res_table.add_row("스레드 댓글 정보 (Thread Positive)", f"Has Thread: {context.has_thread}, Reply Count: {context.reply_count}, Thread ID: {context.thread_identifier_candidate}", th_style)

    console.print(res_table)

    # 5. 결과 파일 저장
    out_file = Path(output_path) if output_path else project_root / "output" / "slack_result_context.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(context.model_dump(), f, ensure_ascii=False, indent=2)

    console.print(f"\n[bold green]✓ 문맥 수집 결과가 저장되었습니다:[/bold green] [dim]{out_file}[/dim]")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slack Search Result Context Inspector CLI (MVP 3.1 & MVP 3.1.1)"
    )
    parser.add_argument("query", nargs="?", default="수임", help="검색할 키워드 (기본: '수임')")
    parser.add_argument("--query", dest="query_opt", default=None, help="검색할 키워드 (옵션 플래그)")
    parser.add_argument("--result-index", type=int, default=0, help="조사할 검색 결과 항목 인덱스 (기본: 0)")
    parser.add_argument("--max-before", type=int, default=20, help="타깃 기준 이전 메시지 최대 수집 수 (기본: 20)")
    parser.add_argument("--max-after", type=int, default=20, help="타깃 기준 이후 메시지 최대 수집 수 (기본: 20)")
    parser.add_argument("--output", "-o", default=None, help="문맥 수집 결과 저장 JSON 경로")

    args = parser.parse_args()
    target_query = args.query_opt if args.query_opt is not None else args.query

    ret_code = asyncio.run(
        inspect_search_result_context(
            query=target_query,
            result_index=args.result_index,
            max_before=args.max_before,
            max_after=args.max_after,
            output_path=args.output,
        )
    )
    sys.exit(ret_code)


if __name__ == "__main__":
    main()
