#!/usr/bin/env python
"""Slack Desktop UI Automation Tree Inspector (MVP 0).

실행 중인 Slack 데스크톱 애플리케이션의 Microsoft UI Automation (UIA) 구조를 탐색하여
콘솔(rich 트리) 및 JSON 파일로 저장합니다.

사용법:
    python scripts/inspect_slack.py
    python scripts/inspect_slack.py --max-depth 20 --max-elements 3000 --output output/slack_uia_tree.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add src to sys.path to allow running directly without installation
project_root = Path(__file__).resolve().parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Windows 콘솔 인코딩 UTF-8 설정
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pesu_agent.adapters.slack_desktop import (
    SlackDesktopAdapter,
    SlackNotFoundError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slack 데스크톱 앱 Microsoft UI Automation Tree 검사 도구 (MVP 0)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="UI Automation Tree 재귀 탐색 최대 깊이",
    )
    parser.add_argument(
        "--max-elements",
        type=int,
        default=3000,
        help="수집할 최대 UI 요소 수",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="output/slack_uia_tree.json",
        help="결과 JSON 파일 저장 경로",
    )
    parser.add_argument(
        "--console-max-depth",
        type=int,
        default=12,
        help="콘솔에 출력할 트리의 최대 깊이",
    )
    parser.add_argument(
        "--console-max-elements",
        type=int,
        default=500,
        help="콘솔에 출력할 트리의 최대 요소 수",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="콘솔 트리 출력을 생략하고 요약 통계 및 파일 저장만 수행",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="상세 디버그 로그 및 Windows 데스크톱 컨텍스트 진단 출력",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()

    # 로깅 레벨 설정
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. 헤더 출력
    console.print(
        Panel(
            "[bold cyan]Slack Desktop UI Automation Tree Inspector[/bold cyan] [dim](MVP 0: Read-Only)[/dim]\n"
            "[white]안전한 읽기 전용 모드로 실행 중인 Slack 데스크톱 애플리케이션의 UI 구조를 수집합니다.[/white]",
            border_style="cyan",
        )
    )

    adapter = SlackDesktopAdapter()

    # 디버그 모드일 경우 Windows 데스크톱 컨텍스트 출력
    if args.debug:
        desktop_ctx = adapter.get_desktop_context()
        console.print(
            f"[dim]Windows Desktop 진단: Session={desktop_ctx.get('session_id')}, "
            f"WinStation={desktop_ctx.get('window_station')}, "
            f"Desktop={desktop_ctx.get('desktop_name')}[/dim]\n"
        )

    # 2. Slack 창 탐색
    with console.status("[bold green]실행 중인 Slack 데스크톱 창 탐색 중...[/bold green]", spinner="dots"):
        try:
            slack_window = adapter.find_slack_window()
        except SlackNotFoundError as err:
            console.print(
                Panel(
                    f"[bold red]Slack 감지 실패[/bold red]\n\n{err}",
                    title="오류",
                    border_style="red",
                )
            )
            return 1
        except Exception as err:
            console.print(
                Panel(
                    f"[bold red]예상치 못한 오류 발생[/bold red]\n\n{err}",
                    title="오류",
                    border_style="red",
                )
            )
            return 1

    # 3. UI Automation Tree 탐색
    with console.status(
        f"[bold cyan]UI Automation Tree 읽는 중...[/bold cyan] [dim](최대 깊이: {args.max_depth}, 최대 요소: {args.max_elements})[/dim]",
        spinner="dots",
    ):
        try:
            result = adapter.inspect_tree(
                slack_window=slack_window,
                max_depth=args.max_depth,
                max_elements=args.max_elements,
            )
        except Exception as err:
            console.print(
                Panel(
                    f"[bold red]UI Tree 탐색 중 오류 발생[/bold red]\n\n{err}",
                    title="오류",
                    border_style="red",
                )
            )
            return 1

    # 4. 콘솔 트리 출력 (옵션)
    if not args.no_console:
        console.print("\n[bold yellow]── UI Automation 계층 구조 ──[/bold yellow]")
        rich_tree = adapter.to_rich_tree(
            result.root,
            max_display_depth=args.console_max_depth,
            max_display_elements=args.console_max_elements,
        )
        console.print(rich_tree)
        console.print("")

    # 5. JSON 파일 저장
    output_path = Path(args.output)
    saved_path = adapter.save_json(result, output_path)
    file_size_kb = saved_path.stat().st_size / 1024

    # 6. 실행 요약 테이블 출력
    summary_table = Table(title="검사 실행 요약", border_style="cyan", show_header=True)
    summary_table.add_column("항목", style="bold cyan", width=24)
    summary_table.add_column("값", style="white")

    summary_table.add_row("Slack 창 제목", result.slack_window_title)
    summary_table.add_row("Slack Process ID", str(result.slack_process_id))
    summary_table.add_row("수집된 UI 요소 수", f"{result.total_elements:,} 개")
    summary_table.add_row("최대 도달 깊이", f"{result.max_depth_reached} (설정 한도: {args.max_depth})")

    # Truncated 상태 표시
    if result.is_truncated:
        reasons_str = ", ".join(result.truncation_reasons)
        trunc_display = f"[bold yellow]True[/bold yellow] [dim]({reasons_str})[/dim]"
    else:
        trunc_display = "[bold green]False[/bold green] [dim](전체 트리 완전 수집됨)[/dim]"
    summary_table.add_row("탐색 제한으로 트리 잘림 여부", trunc_display)

    summary_table.add_row("소요 시간", f"{result.duration_seconds:.3f} 초")
    summary_table.add_row("JSON 저장 위치", f"{saved_path.resolve()} ({file_size_kb:.1f} KB)")

    console.print(summary_table)

    # 7. ControlType별 발견 개수 집계 테이블 출력
    if result.control_type_counts:
        type_table = Table(
            title="ControlType별 발견 개수 집계",
            border_style="magenta",
            show_header=True,
        )
        type_table.add_column("ControlType", style="bold magenta", width=20)
        type_table.add_column("발견 개수", style="white", justify="right", width=12)
        type_table.add_column("비율", style="dim white", justify="right", width=10)

        for ctype, count in result.control_type_counts.items():
            pct = (count / result.total_elements * 100) if result.total_elements else 0
            type_table.add_row(ctype, f"{count:,}", f"{pct:.1f}%")

        console.print(type_table)

    console.print(
        f"\n[bold green]✓ 성공:[/bold green] UI Automation Tree가 [bold underline]{saved_path}[/bold underline] 에 안전하게 저장되었습니다.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
