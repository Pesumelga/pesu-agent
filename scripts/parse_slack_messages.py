#!/usr/bin/env python
"""Slack Visible Message Parser CLI (MVP 1.2: Scroll-Safe Message Identity).

현재 Slack 데스크톱 앱의 UI Automation Tree에서 가시적 메시지 목록을 추출하여
작성자 보정(Author Resolution), viewport_index, 및 Scroll-Safe Fingerprint를 생성하고,
콘솔 상세 출력 및 JSON 파일로 저장합니다.

사용법:
    # 실시간 실행 중인 Slack에서 바로 파싱
    python scripts/parse_slack_messages.py

    # 기존 덤프된 JSON 트리 파일로부터 파싱
    python scripts/parse_slack_messages.py --from-json output/slack_uia_tree.json

    # 옵션 지정
    python scripts/parse_slack_messages.py --max-depth 25 --max-elements 3000 --output output/slack_visible_messages.json
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
from pesu_agent.parsers.slack_message_parser import SlackMessageParser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slack 화면에 노출된 가시적 메시지 추출 및 Scroll-Safe Identity 파서 (MVP 1.2)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--from-json",
        type=str,
        default=None,
        help="이미 저장된 UIA Tree JSON 파일 경로 (지정 시 실시간 UIA 탐색 대신 파일에서 파싱)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=25,
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
        default="output/slack_visible_messages.json",
        help="파싱된 메시지 JSON 파일 저장 경로",
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
            "[bold cyan]Slack Visible Message Parser[/bold cyan] [dim](MVP 1.2: Scroll-Safe Message Identity)[/dim]\n"
            "[white]현재 Slack 화면(UIA Tree)의 가시적 메시지를 추출하고 Scroll-Safe Fingerprint 및 viewport_index를 부여합니다.[/white]\n"
            "[dim yellow]※ UI Virtualization으로 인해 화면에 보이는 메시지만 수집되며 전체 대화가 아닙니다.[/dim yellow]",
            border_style="cyan",
        )
    )

    parser = SlackMessageParser()

    # 1. 트리 데이터 확보 (파일 또는 실시간 UIA 탐색)
    if args.from_json:
        json_file = Path(args.from_json)
        if not json_file.exists():
            console.print(f"[bold red]오류:[/bold red] 지정한 파일이 존재하지 않습니다: {json_file}")
            return 1
        with console.status(f"[bold green]JSON 파일 로딩 중: {json_file}...[/bold green]"):
            result = parser.parse_from_json(json_file)
    else:
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

        with console.status(
            f"[bold cyan]Slack UI Tree 수집 및 메시지 파싱 중...[/bold cyan] [dim](최대 깊이: {args.max_depth}, 최대 요소: {args.max_elements})[/dim]"
        ):
            try:
                tree_result = adapter.inspect_tree(
                    slack_window=slack_window,
                    max_depth=args.max_depth,
                    max_elements=args.max_elements,
                )
                result = parser.parse_from_tree(tree_result)
            except Exception as err:
                console.print(
                    Panel(
                        f"[bold red]메시지 파싱 중 오류 발생[/bold red]\n\n{err}",
                        title="오류",
                        border_style="red",
                    )
                )
                return 1

    # 2. 콘솔 출력
    console.print(f"\n[bold green]Found {result.message_count} visible Slack messages[/bold green]\n")

    for i, msg in enumerate(result.messages, 1):
        if msg.author_resolution == "explicit":
            author_badge = f"[bold green]{msg.author_resolved}[/bold green] [dim cyan](explicit)[/dim cyan]"
        elif msg.author_resolution == "inherited_from_previous_message":
            author_badge = (
                f"[bold yellow]{msg.author_resolved}[/bold yellow] "
                f"[dim italic](inherited from prev message)[/dim italic]"
            )
        else:
            author_badge = "[dim red](unresolved - no author found)[/dim red]"

        time_display = msg.timestamp_raw if msg.timestamp_raw else "[dim](None)[/dim]"
        mentions_display = ", ".join(msg.mentions) if msg.mentions else "[dim]None[/dim]"
        links_display = ", ".join(msg.links) if msg.links else "[dim]None[/dim]"
        fp_short = msg.message_fingerprint[:16] + "..." if msg.message_fingerprint else "[dim]None[/dim]"

        console.print(f"[bold cyan][{i}][/bold cyan] [dim](viewport_index: {msg.viewport_index})[/dim]")
        console.print(f"[bold]Author:[/bold] {author_badge}")
        if msg.author_raw != msg.author_resolved:
            console.print(f"[dim]  └ Raw Author in UIA: {msg.author_raw!r}[/dim]")
        console.print(f"[bold]Time:[/bold] {time_display}")
        if msg.mentions:
            console.print(f"[bold]Mentions:[/bold] [yellow]{mentions_display}[/yellow]")
        if msg.links:
            console.print(f"[bold]Links:[/bold] [blue]{links_display}[/blue]")
        console.print(f"[bold]Context:[/bold] [dim]{msg.context}[/dim]")
        console.print(f"[bold]Fingerprint:[/bold] [dim cyan]{fp_short}[/dim cyan] [dim](Scroll-Safe SHA-256)[/dim]")
        console.print(f"[bold]Text:[/bold] {msg.text}")
        console.print("")

    # 3. JSON 저장
    output_path = Path(args.output)
    saved_path = parser.save_json(result, output_path)
    file_size_kb = saved_path.stat().st_size / 1024

    # 4. 요약 테이블 출력
    summary_table = Table(title="메시지 파싱 및 Scroll-Safe Identity 요약", border_style="cyan", show_header=True)
    summary_table.add_column("항목", style="bold cyan", width=30)
    summary_table.add_column("값", style="white")

    summary_table.add_row("Slack 창 제목", result.slack_window_title)
    summary_table.add_row("추출된 가시적 메시지 수 (Total)", f"[bold green]{result.message_count} 개[/bold green]")
    summary_table.add_row(
        "  ├ 직접 발견된 작성자 (Explicit)",
        f"[green]{result.explicit_author_count} 개[/green]",
    )
    summary_table.add_row(
        "  ├ 직전 메시지 상속 (Inherited)",
        f"[yellow]{result.inherited_author_count} 개[/yellow]",
    )
    summary_table.add_row(
        "  └ 미확인 작성자 (Unresolved)",
        f"[red]{result.unresolved_author_count} 개[/red]",
    )
    summary_table.add_row(
        "고유 Fingerprint 수 (Unique)",
        f"[bold green]{result.unique_fingerprints_count} 개[/bold green]",
    )
    summary_table.add_row(
        "Fingerprint 중복 그룹 수 (Duplicates)",
        f"{result.duplicate_fingerprint_groups_count} 개",
    )
    summary_table.add_row(
        "제외된 비메시지 후보 수",
        f"{result.excluded_candidates_count} 개 [dim](구분선/공백/글머리기호/액션버튼)[/dim]",
    )
    summary_table.add_row("수집 범위 (Scope)", f"{result.scope} [dim](화면 노출 영역만)[/dim]")
    summary_table.add_row("전체 대화 여부", f"{result.is_complete_conversation} [dim](가상화 제한)[/dim]")
    summary_table.add_row("JSON 저장 위치", f"{saved_path.resolve()} ({file_size_kb:.1f} KB)")

    console.print(summary_table)

    # 5. 중복 그룹이 있는 경우 상세 출력
    fp_groups: dict[str, list] = {}
    for m in result.messages:
        fp_groups.setdefault(m.message_fingerprint, []).append(m)

    dup_groups = {fp: msgs for fp, msgs in fp_groups.items() if len(msgs) > 1}
    if dup_groups:
        console.print("\n[bold yellow]── 중복 Fingerprint 그룹 검토 ──[/bold yellow]")
        for fp, group in dup_groups.items():
            console.print(f"[bold red]Group: {fp[:16]}... ({len(group)}건)[/bold red]")
            for item in group:
                console.print(
                    f"  - [v_idx={item.viewport_index}] Author: {item.author_resolved} | Time: {item.timestamp_raw} | Text: {item.text[:60]!r}"
                )

    # 6. Unresolved 메시지가 있는 경우 상세 출력
    unresolved_list = [m for m in result.messages if m.author_resolution == "unresolved"]
    if unresolved_list:
        console.print("\n[bold yellow]── Unresolved 작성자 메시지 검토 ──[/bold yellow]")
        for item in unresolved_list:
            console.print(
                f"  - [v_idx={item.viewport_index}] Time: {item.timestamp_raw} | Node: {item.source_node_name[:40]!r} | Text: {item.text[:60]!r}"
            )

    console.print(
        f"\n[bold green]✓ 성공:[/bold green] 가시적 메시지가 [bold underline]{saved_path}[/bold underline] 에 저장되었습니다.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
