#!/usr/bin/env python
"""Slack Agent Mode Launcher & Status Manager (MVP 2.3).

Slack 데스크톱 애플리케이션의 Agent Mode(CDP 백그라운드) 실행 및 상태를 관리합니다.
사용자의 동의 없는 Slack 강제 종료를 방지하고, 명시적 --restart 옵션이 있을 때만 안전하게 재시작합니다.

사용법:
    # 현재 상태 확인 및 필요시 기동 (일반 Slack 실행 중이면 재시작 요구 안내)
    python scripts/start_agent_slack.py

    # 상태만 조회 (프로세스 변경 없음)
    python scripts/start_agent_slack.py --status

    # 일반 Slack이 실행 중일 때 명시적 재시작 수행
    python scripts/start_agent_slack.py --restart
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
    SlackStatusResult,
)


def render_status_badge(console: Console, status_res: SlackStatusResult) -> None:
    """Slack Agent Mode 상태 뱃지 및 세부 정보 출력."""
    if status_res.status == SlackAgentModeStatus.AGENT_READY:
        badge = "[bold green]● Ready[/bold green]"
        border_col = "green"
    elif status_res.status == SlackAgentModeStatus.RESTART_REQUIRED:
        badge = "[bold yellow]● Restart Required[/bold yellow]"
        border_col = "yellow"
    elif status_res.status == SlackAgentModeStatus.OFF:
        badge = "[dim white]● Off[/dim white]"
        border_col = "dim"
    else:
        badge = "[bold red]● Error[/bold red]"
        border_col = "red"

    panel_content = (
        f"[bold cyan]Slack Agent Mode[/bold cyan]\n"
        f"{badge}\n\n"
        f"[white]{status_res.message}[/white]"
    )

    console.print(Panel(panel_content, border_style=border_col))

    table = Table(title="Slack Process & CDP Details", border_style="dim cyan", show_header=True)
    table.add_column("항목", style="bold cyan", width=25)
    table.add_column("값", style="white")

    table.add_row("Agent Mode 상태", status_res.status.value)
    table.add_row("CDP 포트", str(status_res.cdp_port))
    table.add_row(
        "실행 중인 Slack 프로세스",
        f"{len(status_res.running_pids)}개 (PIDs: {status_res.running_pids[:4]}...)"
        if status_res.running_pids
        else "0개 (미실행)",
    )
    table.add_row("CDP 활성 타깃 수", f"{status_res.cdp_targets_count} 개")
    table.add_row("루프백 격리 (127.0.0.1)", "안전 (Loopback 전용)" if status_res.is_loopback_only else "[bold red]위험[/bold red]")
    if status_res.slack_app_binary:
        table.add_row("Slack 바이너리", status_res.slack_app_binary)

    console.print(table)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slack Agent Mode 수명주기 및 상태 관리자 (MVP 2.3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=9222, help="Remote debugging 포트")
    parser.add_argument(
        "--status",
        action="store_true",
        help="상태만 조회하고 프로세스를 변경하지 않음",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="일반 Slack이 실행 중일 때 명시적으로 정상 종료 후 Agent Mode로 재시작",
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

    manager = SlackLifecycleManager(cdp_port=args.port)

    # 1. 상태만 조회
    if args.status:
        status_res = manager.get_status()
        render_status_badge(console, status_res)
        return 0 if status_res.status == SlackAgentModeStatus.AGENT_READY else 1

    # 2. 상태 점검 및 안전 기동
    initial_status = manager.get_status()

    if initial_status.status == SlackAgentModeStatus.AGENT_READY:
        console.print("[bold green]✓ 이미 정상적인 Slack CDP가 실행 중입니다. 재시작 없이 그대로 사용합니다.[/bold green]\n")
        render_status_badge(console, initial_status)
        return 0

    if initial_status.status in (
        SlackAgentModeStatus.RESTART_REQUIRED,
        SlackAgentModeStatus.NORMAL_SLACK,
    ):
        if not args.restart:
            render_status_badge(console, initial_status)
            console.print(
                Panel(
                    "[bold yellow]안내: Slack이 일반 모드로 실행 중입니다.[/bold yellow]\n\n"
                    "사용자의 작업 보호를 위해 실행 중인 Slack을 자동으로 종료하지 않습니다.\n"
                    "Agent 백그라운드 모드를 사용하려면 아래 명령으로 명시적 재시작을 수행해주세요:\n\n"
                    "  [bold cyan]python scripts/start_agent_slack.py --restart[/bold cyan]\n"
                    "  또는 PowerShell: [bold cyan].\\scripts\\start-agent-slack.ps1 -Restart[/bold cyan]",
                    border_style="yellow",
                    title="Slack 재시작 필요",
                )
            )
            return 2  # Exit code 2 indicates restart required

        # 명시적 재시작 수행
        with console.status("[bold yellow]실행 중인 Slack 프로세스 정상 종료 및 Agent Mode 재기동 중...[/bold yellow]"):
            final_res = manager.ensure_agent_ready(allow_restart=True)
            render_status_badge(console, final_res)
            return 0 if final_res.status == SlackAgentModeStatus.AGENT_READY else 1

    # Slack이 미실행 상태인 경우 자동 기동
    with console.status("[bold green]Slack Agent Mode 기동 중...[/bold green]"):
        final_res = manager.ensure_agent_ready(allow_restart=False)
        render_status_badge(console, final_res)
        return 0 if final_res.status == SlackAgentModeStatus.AGENT_READY else 1


if __name__ == "__main__":
    sys.exit(main())
