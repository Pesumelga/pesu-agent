#!/usr/bin/env python
"""Slack Background Search CLI with Freshness & Correctness Validation (MVP 3.0.1).

사용자가 다른 프로그램을 사용하는 동안,
Slack 데스크톱 앱의 UI/마우스/키보드에 전혀 간섭하지 않고(0% 간섭)
CDP를 통해 Slack 검색을 실행하고 Freshness Guard로 검증된 구조화된 검색 결과를 수집/저장합니다.

사용법:
    python scripts/search_slack.py "수임"
    python scripts/search_slack.py --query "영업지원" --max-scrolls 3
    python scripts/search_slack.py --query "테스트" --output output/my_search.json
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import sys
from pathlib import Path

# Add src to sys.path
project_root = Path(__file__).resolve().parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Windows UTF-8 설정
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.search.slack_search import SlackSearch, SlackSearchSession


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_system_input_state() -> tuple[str, tuple[int, int]]:
    """현재 포그라운드 윈도우 제목 및 마우스 커서 위치를 측정합니다."""
    user32 = ctypes.windll.user32
    fg_hwnd = user32.GetForegroundWindow()
    fg_buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(fg_hwnd, fg_buf, 256)
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return fg_buf.value, (pt.x, pt.y)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slack Background Search CLI with Freshness Validation (MVP 3.0.1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("positional_query", nargs="?", default=None, help="검색할 검색어 (위치 인자)")
    parser.add_argument("--query", "-q", default=None, help="검색할 검색어 (옵션 인자)")
    parser.add_argument("--port", type=int, default=9222, help="Slack CDP 포트 번호")
    parser.add_argument("--max-scrolls", type=int, default=3, help="검색 결과 추가 백그라운드 스크롤 횟수")
    parser.add_argument("--output", "-o", default="output/slack_search_results.json", help="결과 JSON 저장 경로")
    parser.add_argument("--debug", action="store_true", help="상세 디버그 로그 활성화")
    return parser.parse_args()


async def run_search(query: str, port: int, max_scrolls: int, output_path: str, debug: bool) -> int:
    console = Console()

    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console.print("\n[bold cyan]Slack Search[/bold cyan]\n")

    # 1. 상태 점검
    lifecycle = SlackLifecycleManager(cdp_port=port)
    status_res = lifecycle.get_status()

    if status_res.status != SlackAgentModeStatus.AGENT_READY:
        console.print(f"[bold yellow]Agent Mode:[/bold yellow] [bold red]{status_res.status.value}[/bold red]")
        console.print(
            Panel(
                f"[bold red]검색 실행 불가[/bold red]\n\n"
                f"{status_res.message}\n\n"
                f"Agent Mode를 실행하려면 다음 명령을 실행하세요:\n"
                f"  [bold cyan]python scripts/start_agent_slack.py[/bold cyan]",
                border_style="red",
            )
        )
        return 1

    console.print(f"Agent Mode: [bold green]Ready[/bold green]")
    console.print(f"Query: [bold yellow]{query}[/bold yellow]\n")

    # 포그라운드 및 마우스 위치 사전 측정
    fg_before, pt_before = get_system_input_state()

    # 2. 백그라운드 검색 실행
    searcher = SlackSearch(lifecycle_manager=lifecycle, cdp_port=port)
    
    with console.status(f"[bold green]Searching for {query!r} in background...[/bold green]"):
        try:
            session: SlackSearchSession = await searcher.search(
                query=query,
                max_scrolls=max_scrolls,
            )
        except Exception as err:
            console.print(f"[bold red]검색 실행 중 오류 발생:[/bold red] {err}")
            return 1

    # 포그라운드 및 마우스 위치 사후 측정
    fg_after, pt_after = get_system_input_state()

    # 3. 검증 지표 표 출력
    val_table = Table(title="Search Freshness & Validation Metrics", border_style="cyan")
    val_table.add_column("항목", style="bold cyan")
    val_table.add_column("값", style="white")

    val_table.add_row("Session ID", session.search_session_id)
    val_table.add_row("Requested Query", session.requested_query)
    val_table.add_row("Observed Query", session.observed_query)
    val_table.add_row(
        "Query Verified",
        "[bold green]True (일치)[/bold green]" if session.query_verified else "[bold red]False (불일치)[/bold red]",
    )
    val_table.add_row(
        "Result Freshness Verified",
        "[bold green]True (신규 서명 확인)[/bold green]"
        if session.result_freshness_verified
        else "[bold red]False (Stale 의심)[/bold red]",
    )
    val_table.add_row("Result Signature", session.result_signature)
    val_table.add_row("Query Literal Matches", f"{session.query_literal_match_count} / {session.result_count} 건")
    val_table.add_row("Total Results (Unique)", f"{session.result_count} (Unique: {session.unique_result_count})")
    console.print(val_table)
    console.print()

    # 4. 콘솔 결과 목록 출력
    for item in session.results[:15]:
        console.print(f"[[bold cyan]{item.result_index}[/bold cyan]]")
        if item.channel_name:
            chan_str = f"{item.channel_name}"
            if item.channel_id:
                chan_str += f" ({item.channel_id})"
            console.print(f"Channel: [bold magenta]{chan_str}[/bold magenta]")
        if item.author:
            console.print(f"Author: [bold white]{item.author}[/bold white]")
        if item.timestamp_raw:
            console.print(f"Time: [dim]{item.timestamp_raw}[/dim]")
        console.print(f"Text: {item.text}")
        if item.result_url:
            console.print(f"Link: [blue]{item.result_url}[/blue]")
        console.print()

    if len(session.results) > 15:
        console.print(f"[dim]... 외 {len(session.results) - 15}개 결과 생략 ...[/dim]\n")

    # 5. 백그라운드 무간섭 검증 정보 출력
    safety_table = Table(title="Background Non-Interference Verification", border_style="dim cyan")
    safety_table.add_column("항목", style="bold cyan")
    safety_table.add_column("검색 전 (Before)", style="white")
    safety_table.add_column("검색 후 (After)", style="white")
    safety_table.add_column("간섭 여부", style="bold green")

    safety_table.add_row(
        "Foreground Window",
        fg_before[:35] if fg_before else "(None)",
        fg_after[:35] if fg_after else "(None)",
        "0% 간섭 (유지)" if fg_before == fg_after else "포커스 변경 감지",
    )
    safety_table.add_row(
        "Mouse Cursor Position",
        str(pt_before),
        str(pt_after),
        "0% 간섭 (커서 불변)" if pt_before == pt_after else "사용자 이동 감지",
    )
    console.print(safety_table)

    # 6. JSON 파일 저장
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(session.model_dump(), f, ensure_ascii=False, indent=2)

    console.print(f"\n[bold green]✓ 검색 결과가 저장되었습니다:[/bold green] [cyan]{out_file}[/cyan]")
    return 0


def main() -> int:
    args = parse_args()
    query = args.query or args.positional_query
    if not query:
        print("오류: 검색어를 입력해주세요. (예: python scripts/search_slack.py \"수임\")")
        return 1

    return asyncio.run(
        run_search(
            query=query,
            port=args.port,
            max_scrolls=args.max_scrolls,
            output_path=args.output,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
