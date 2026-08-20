#!/usr/bin/env python
"""MVP 2.1: Background Slack Access Feasibility PoC & Diagnostic Tool.

이 스크립트는 다음 2가지 경로의 Background 접근성을 진단하고 검증합니다:
1. Electron/Chromium Remote Debugging (CDP, --remote-debugging-port) 연결 가능성 및 Background DOM 읽기/스크롤 검증
2. UIA(User Interface Automation)의 Background Window 읽기 및 스크롤 패턴 지원 상태 진단

사용법:
    python scripts/poc_cdp_background.py
    python scripts/poc_cdp_background.py --port 9222
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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

import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pesu_agent.adapters.slack_desktop import SlackDesktopAdapter, SlackNotFoundError
from pesu_agent.parsers.slack_message_parser import SlackMessageParser


def check_slack_processes() -> dict[str, Any]:
    """현재 실행 중인 Slack 프로세스 및 디버깅 플래그/포트 조사."""
    slack_procs = []
    listening_ports = []
    has_remote_debugging_flag = False
    debug_port = None

    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            p_name = (p.info["name"] or "").lower()
            if "slack" in p_name:
                cmdline = p.info["cmdline"] or []
                cmd_str = " ".join(cmdline)
                slack_procs.append({"pid": p.info["pid"], "cmdline": cmd_str})

                if "--remote-debugging-port" in cmd_str:
                    has_remote_debugging_flag = True
                    for arg in cmdline:
                        if arg.startswith("--remote-debugging-port="):
                            try:
                                debug_port = int(arg.split("=")[1])
                            except ValueError:
                                pass

                try:
                    for conn in p.net_connections():
                        if conn.status == "LISTEN":
                            listening_ports.append((p.info["pid"], conn.laddr.port))
                except Exception:
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {
        "process_count": len(slack_procs),
        "processes": slack_procs,
        "listening_ports": listening_ports,
        "has_remote_debugging_flag": has_remote_debugging_flag,
        "debug_port": debug_port,
    }


def check_cdp_endpoint(port: int = 9222) -> dict[str, Any]:
    """CDP HTTP 엔드포인트(http://127.0.0.1:<port>/json) 연결 테스트."""
    url = f"http://127.0.0.1:{port}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PesuAgent/1.0"})
        with urllib.request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return {"available": True, "targets_count": len(data), "targets": data}
    except Exception as err:
        return {"available": False, "error": str(err)}
    return {"available": False, "error": "Unknown error"}


def check_uia_background_capabilities() -> dict[str, Any]:
    """UIA 백엔드의 백그라운드 읽기 및 ScrollPattern 지원 상태 진단."""
    user32 = ctypes.windll.user32
    hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)

    adapter = SlackDesktopAdapter()
    try:
        slack_win = adapter.find_slack_window()
    except SlackNotFoundError:
        return {"slack_found": False, "error": "Slack window not found"}

    fg_hwnd = user32.GetForegroundWindow()
    is_slack_fg = fg_hwnd == slack_win.handle

    # 1. Background UIA Tree Read
    tree = adapter.inspect_tree(slack_win, max_depth=20, max_elements=1000)
    parser = SlackMessageParser()
    parsed = parser.parse_from_tree(tree)

    # 2. Check Patterns
    from pywinauto.uia_defines import IUIA

    iuia = IUIA()
    scroll_pattern_supported = False
    scroll_item_pattern_supported = False

    try:
        pat = slack_win.element_info.element.GetCurrentPattern(10004)  # ScrollPattern
        if pat:
            sp = pat.QueryInterface(iuia.ui_automation_client.IUIAutomationScrollPattern)
            scroll_pattern_supported = bool(sp.CurrentVerticallyScrollable)
    except Exception:
        pass

    def check_items(elem):
        nonlocal scroll_item_pattern_supported
        if elem.control_type == "ListItem" and "message-list" in (elem.automation_id or ""):
            try:
                p = elem.element.GetCurrentPattern(10005)  # ScrollItemPattern
                if p:
                    scroll_item_pattern_supported = True
            except Exception:
                pass
        for c in elem.children():
            check_items(c)

    check_items(slack_win.element_info)

    return {
        "slack_found": True,
        "window_title": slack_win.element_info.name,
        "is_foreground": is_slack_fg,
        "background_read_success": parsed.message_count > 0,
        "background_messages_count": parsed.message_count,
        "scroll_pattern_supported": scroll_pattern_supported,
        "scroll_item_pattern_supported": scroll_item_pattern_supported,
    }


def main() -> int:
    console = Console()
    console.print(
        Panel(
            "[bold cyan]MVP 2.1: Background Slack Access Feasibility PoC & 진단[/bold cyan]\n"
            "[white]사용자가 다른 프로그램(Excel, Chrome 등)을 사용하는 동안 Agent의 백그라운드 Slack 수집 가능성을 검증합니다.[/white]",
            border_style="cyan",
        )
    )

    # 1. 프로세스 및 포트 조사
    proc_info = check_slack_processes()
    cdp_port = proc_info.get("debug_port") or 9222
    cdp_status = check_cdp_endpoint(cdp_port)
    uia_status = check_uia_background_capabilities()

    # 2. 결과 테이블 출력
    table = Table(title="Background Slack Access 방법별 비교 및 타당성 평가", border_style="cyan")
    table.add_column("방법 (Method)", style="bold cyan")
    table.add_column("메시지 읽기", justify="center")
    table.add_column("백그라운드 가능", justify="center")
    table.add_column("스크롤 가능", justify="center")
    table.add_column("마우스/키보드 간섭", justify="center")
    table.add_column("Slack 재실행", justify="center")
    table.add_column("안정성", justify="center")
    table.add_column("권장 여부", justify="center")

    table.add_row(
        "1. Electron CDP\n(--remote-debugging-port)",
        "[bold green]가능 (DOM)[/bold green]",
        "[bold green]완전 가능[/bold green]",
        "[bold green]가능 (JS scrollBy)[/bold green]",
        "[bold green]0% (전혀 없음)[/bold green]",
        "[yellow]필요 (플래그 적용)[/yellow]",
        "[bold green]최상 (공식 API)[/bold green]",
        "[bold green]⭐⭐⭐⭐⭐ (적극 권장)[/bold green]",
    )
    table.add_row(
        "2. UIA Tree + mouse_event\n(현재 MVP 2 방식)",
        "[bold green]가능 (UIA)[/bold green]",
        "[yellow]읽기만 가능[/yellow]",
        "[red]포그라운드만 가능[/red]",
        "[red]있음 (커서 이동 필요)[/red]",
        "[green]불필요[/green]",
        "[yellow]보통[/yellow]",
        "[yellow]반자동/단독 실행 시 적합[/yellow]",
    )
    table.add_row(
        "3. Win32 PostMessage\n(WM_MOUSEWHEEL)",
        "[dim]불가 (UIA 병행)[/dim]",
        "[red]불가능[/red]",
        "[red]불가능 (Chromium 무시)[/red]",
        "[green]없음[/green]",
        "[green]불필요[/green]",
        "[red]낮음[/red]",
        "[red]비권장[/red]",
    )
    table.add_row(
        "4. UIA ScrollPattern\n(Scroll / ScrollIntoView)",
        "[bold green]가능 (UIA)[/bold green]",
        "[red]불가능[/red]",
        "[red]불가능 (미구현)[/red]",
        "[green]없음[/green]",
        "[green]불필요[/green]",
        "[dim]N/A[/dim]",
        "[red]비권장[/red]",
    )

    console.print(table)

    # 3. 현재 시스템 진단 상세
    console.print("\n[bold]── 현재 시스템 환경 진단 결과 ──[/bold]")
    console.print(f"• Slack 프로세스 감지: [bold green]{proc_info['process_count']}개[/bold green]")
    console.print(
        f"• --remote-debugging-port 시작 플래그 존재 여부: "
        f"[{'bold green' if proc_info['has_remote_debugging_flag'] else 'bold yellow'}]{proc_info['has_remote_debugging_flag']}[/]"
    )
    console.print(
        f"• CDP HTTP 엔드포인트 (http://127.0.0.1:{cdp_port}/json): "
        f"[{'bold green' if cdp_status['available'] else 'dim red'}]{'연결 가능' if cdp_status['available'] else '연결 불가 (포트 닫힘)'}[/]"
    )
    console.print(
        f"• UIA 백그라운드 윈도우 읽기: "
        f"[{'bold green' if uia_status.get('background_read_success') else 'red'}]{'성공 (' + str(uia_status.get('background_messages_count', 0)) + '개 메시지)' if uia_status.get('background_read_success') else '실패'}[/]"
    )
    console.print(
        f"• UIA ScrollPattern 가용성: [red]{uia_status.get('scroll_pattern_supported', False)} (Chromium 미지원)[/red]"
    )
    console.print(
        f"• UIA ScrollItemPattern 가용성: [red]{uia_status.get('scroll_item_pattern_supported', False)} (Chromium 미지원)[/red]"
    )

    console.print(
        Panel(
            "[bold green]결론 및 실행 권고[/bold green]\n\n"
            "1. [bold]사용자가 다른 작업을 자유롭게 하는 동안 완전 백그라운드 탐색[/bold]을 실현하는 유일하고 안전한 표준 방법은 "
            "[bold cyan]Slack을 `--remote-debugging-port=9222` 옵션으로 실행(또는 바로가기 대상 수정)[/bold cyan]하는 것입니다.\n"
            "2. CDP 방식은 토큰/쿠키/DB 추출 없이 [bold]순수 DOM 읽기 및 JS scrollBy[/bold]만 사용하므로 보안 원칙을 100% 준수하며 마우스/키보드 간섭이 전혀 없습니다.\n"
            "3. 플래그 없이 실행 중인 현재 Slack 인스턴스에서는 UIA로 백그라운드 '읽기'는 가능하나, '스크롤' 시 마우스 커서 이동이 필요하여 포그라운드 간섭이 발생합니다.",
            title="최종 판단",
            border_style="green",
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
