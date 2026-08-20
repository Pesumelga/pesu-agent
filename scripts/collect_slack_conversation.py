#!/usr/bin/env python
"""Slack Conversation Scroll Collector CLI (MVP 2).

현재 Slack 데스크톱 앱에서 열려 있는 대화(채널 / DM / 스레드)를 위로 스크롤하며
메시지를 누적 수집하고, Scroll-Safe Fingerprint 기반으로 중복 없이 하나의 대화 스트림으로 병합합니다.

사용법:
    # 기본 실행 (최대 10회 스크롤, 최대 300개 메시지)
    python scripts/collect_slack_conversation.py

    # 5회 스크롤 제한 실행
    python scripts/collect_slack_conversation.py --max-scrolls 5 --max-messages 300

    # 옵션 지정 실행
    python scripts/collect_slack_conversation.py --max-scrolls 20 --settle-ms 1000 --output output/slack_conversation.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add src to sys.path
project_root = Path(__file__).resolve().parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Windows 콘솔 UTF-8 설정
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
from pesu_agent.collectors.slack_scroll_collector import (
    SlackConversationCollection,
    SlackScrollCollector,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slack 대화 컨테이너 상향 스크롤 수집기 (MVP 2)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=10,
        help="최대 스크롤 반복 횟수",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=300,
        help="최대 누적 수집 메시지 개수",
    )
    parser.add_argument(
        "--no-new-message-limit",
        type=int,
        default=3,
        help="새 메시지가 발견되지 않을 때 종료할 연속 횟수",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=800,
        help="스크롤 후 UI 렌더링 및 가상화 안정화 대기 시간 (밀리초)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="output/slack_conversation.json",
        help="수집된 대화 JSON 파일 저장 경로",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="상세 디버그 로그 출력",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console.print(
        Panel(
            "[bold cyan]Slack Conversation Collector[/bold cyan] [dim](MVP 2: Scroll & Stitching)[/dim]\n"
            "[white]열려 있는 Slack 대화 컨테이너를 위로 스크롤하며 메시지를 누적 수집합니다.[/white]\n"
            "[dim yellow]※ 엄격한 읽기/스크롤 전용: 클릭, 텍스트 입력, 메시지 전송, 검색은 수행하지 않습니다.[/dim yellow]",
            border_style="cyan",
        )
    )

    adapter = SlackDesktopAdapter()

    with console.status("[bold green]실행 중인 Slack 데스크톱 창 탐색 중...[/bold green]"):
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
                    f"[bold red]오류 발생[/bold red]\n\n{err}",
                    title="오류",
                    border_style="red",
                )
            )
            return 1

    collector = SlackScrollCollector(adapter=adapter)

    def on_progress(stat: dict[str, Any]):
        it = stat["iteration"]
        if it == 0:
            console.print(f"\n[bold green]Initial Viewport[/bold green]")
            console.print(f"Visible messages: {stat['visible']}")
            console.print(f"New messages: {stat['new']}")
            console.print(f"Total unique: {stat['total_unique']}\n")
        else:
            console.print(f"[bold cyan]↑ scrolling...[/bold cyan]")
            console.print(f"\n[bold green]Iteration {it}[/bold green]")
            console.print(f"Visible messages: {stat['visible']}")
            console.print(f"Overlap: {stat['overlap']}")
            console.print(f"New messages: {stat['new']}")
            console.print(f"Total unique: {stat['total_unique']}\n")

    try:
        collection: SlackConversationCollection = collector.collect_conversation(
            slack_window=slack_window,
            max_scrolls=args.max_scrolls,
            max_messages=args.max_messages,
            no_new_message_limit=args.no_new_message_limit,
            settle_ms=args.settle_ms,
            on_progress=on_progress,
        )
    except Exception as err:
        console.print(
            Panel(
                f"[bold red]수집 중 오류 발생[/bold red]\n\n{err}",
                title="오류",
                border_style="red",
            )
        )
        return 1

    # JSON 저장
    output_path = Path(args.output)
    saved_path = collector.save_json(collection, output_path)
    file_size_kb = saved_path.stat().st_size / 1024

    # 요약 테이블 출력
    summary_table = Table(
        title="Slack Conversation Scroll Collection Summary",
        border_style="cyan",
        show_header=True,
    )
    summary_table.add_column("항목", style="bold cyan", width=30)
    summary_table.add_column("값", style="white")

    summary_table.add_row("대화명 (Conversation)", collection.conversation_name)
    summary_table.add_row("대화 컨텍스트 (Context)", collection.context)
    summary_table.add_row("스크롤 방식 (Method)", collection.scroll_method)
    summary_table.add_row("수행된 스크롤 횟수", f"{collection.scroll_iterations} 회")
    summary_table.add_row(
        "초기 가시 메시지 수",
        f"{collection.iteration_stats[0]['visible'] if collection.iteration_stats else 0} 개",
    )
    summary_table.add_row(
        "최종 누적 메시지 수 (Total)",
        f"[bold green]{collection.message_count} 개[/bold green]",
    )
    summary_table.add_row("고유 메시지 수 (Unique)", f"{collection.unique_message_count} 개")
    summary_table.add_row("종료 사유 (Stop Reason)", f"[bold yellow]{collection.stop_reason}[/bold yellow]")
    summary_table.add_row("최상단 도달 여부 (Reached Start)", str(collection.is_reached_start))
    summary_table.add_row("전체 대화 완전 수집 여부", str(collection.is_complete))
    summary_table.add_row("JSON 저장 위치", f"{saved_path.resolve()} ({file_size_kb:.1f} KB)")

    console.print(summary_table)

    # 반복별 세부 통계 테이블
    stat_table = Table(title="Iteration Breakdown", border_style="dim cyan")
    stat_table.add_column("Iteration", justify="center")
    stat_table.add_column("Visible", justify="right")
    stat_table.add_column("Overlap", justify="right")
    stat_table.add_column("New Added", justify="right", style="green")
    stat_table.add_column("Total Unique", justify="right", style="bold cyan")

    for s in collection.iteration_stats:
        it_label = "Initial" if s["iteration"] == 0 else str(s["iteration"])
        stat_table.add_row(
            it_label,
            str(s["visible"]),
            str(s["overlap"]),
            str(s["new"]),
            str(s["total_unique"]),
        )

    console.print(stat_table)

    # 최초 및 최신 메시지 비교
    if collection.first_visible_message and collection.last_visible_message:
        console.print("\n[bold]수집된 메시지 범위 (Chronological Span):[/bold]")
        console.print(
            f"  [bold cyan]Oldest (First):[/bold cyan] [{collection.first_visible_message.author_resolved}] "
            f"{collection.first_visible_message.timestamp_raw} | {collection.first_visible_message.text[:60]!r}"
        )
        console.print(
            f"  [bold green]Newest (Last):[/bold green]  [{collection.last_visible_message.author_resolved}] "
            f"{collection.last_visible_message.timestamp_raw} | {collection.last_visible_message.text[:60]!r}"
        )

    console.print(
        f"\n[bold green]✓ 성공:[/bold green] 대화 수집 결과가 [bold underline]{saved_path}[/bold underline] 에 저장되었습니다.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
