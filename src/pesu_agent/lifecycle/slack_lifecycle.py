"""Slack Lifecycle and Agent Mode Manager (MVP 2.3).

Slack 데스크톱 애플리케이션의 실행 상태, CDP(Remote Debugging) 활성화 여부를 감지하고,
사용자의 동의 없는 강제 종료를 방지하면서 안전한 Agent Mode 기동 및 상태 조회를 제공합니다.

상태 구분 (SlackAgentModeStatus):
- OFF: Slack 프로세스가 실행 중이지 않음
- NORMAL_SLACK / RESTART_REQUIRED: 일반 모드로 실행 중 (CDP 비활성, 재시작 필요)
- AGENT_READY: CDP(포트 9222)가 활성화되어 백그라운드 수집 준비 완료
- ERROR: 포트 충돌 또는 프로세스 오류
"""

from __future__ import annotations

import enum
import glob
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import psutil
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SlackAgentModeStatus(str, enum.Enum):
    """Slack Agent Mode 상태 열거형."""

    OFF = "OFF"
    NORMAL_SLACK = "NORMAL_SLACK"
    RESTART_REQUIRED = "RESTART_REQUIRED"
    AGENT_READY = "AGENT_READY"
    ERROR = "ERROR"


class SlackStatusResult(BaseModel):
    """Slack 실행 및 Agent Mode 상태 진단 결과."""

    status: SlackAgentModeStatus = Field(description="현재 Slack Agent Mode 상태")
    message: str = Field(description="상태 설명 및 사용자 안내 문구")
    cdp_port: int = Field(default=9222, description="CDP 포트 번호")
    running_pids: list[int] = Field(default_factory=list, description="실행 중인 Slack PID 목록")
    main_pid: Optional[int] = Field(default=None, description="Slack 메인 프로세스 PID")
    cdp_targets_count: int = Field(default=0, description="CDP 활성 타깃 수")
    is_loopback_only: bool = Field(default=True, description="소켓이 127.0.0.1 로컬 루프백에만 바인딩되었는지 여부")
    slack_app_binary: Optional[str] = Field(default=None, description="탐색된 Slack 실행 바이너리 경로")


class SlackLifecycleManager:
    """Slack 프로세스 수명주기 및 Agent Mode 상태 관리자."""

    def __init__(self, cdp_port: int = 9222):
        self.cdp_port = cdp_port

    @staticmethod
    def find_slack_app_binary() -> Optional[str]:
        """Slack App 실행 바이너리(app-X.X.X/slack.exe) 경로를 탐색합니다."""
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            return None
        pattern = os.path.join(local_app_data, "slack", "app-*", "slack.exe")
        app_exes = glob.glob(pattern)
        if app_exes:
            return sorted(app_exes)[-1]
        fallback = os.path.join(local_app_data, "slack", "slack.exe")
        if os.path.exists(fallback):
            return fallback
        return None

    def get_running_slack_pids(self) -> list[int]:
        """현재 실행 중인 모든 slack.exe PID 목록을 조회합니다."""
        pids = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and "slack" in p.info["name"].lower():
                    pids.append(p.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return pids

    def check_cdp_ready(self, port: Optional[int] = None) -> tuple[bool, int, bool]:
        """CDP 엔드포인트 응답 및 로컬 루프백 바인딩을 확인합니다.

        Returns:
            (is_ready, targets_count, is_loopback_only)
        """
        target_port = port or self.cdp_port
        url = f"http://127.0.0.1:{target_port}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PesuAgent/1.0"})
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    # 루프백 바인딩 여부 검증
                    is_loopback = True
                    for p in psutil.process_iter(["name", "pid"]):
                        try:
                            if p.info["name"] and "slack" in p.info["name"].lower():
                                for conn in p.net_connections():
                                    if conn.laddr.port == target_port:
                                        if conn.laddr.ip not in ("127.0.0.1", "::1", "localhost"):
                                            is_loopback = False
                        except Exception:
                            pass
                    return True, len(data), is_loopback
        except Exception:
            pass
        return False, 0, True

    def get_status(self) -> SlackStatusResult:
        """현재 Slack 데스크톱 앱의 Agent Mode 상태를 정밀 진단합니다."""
        slack_binary = self.find_slack_app_binary()
        running_pids = self.get_running_slack_pids()
        main_pid = running_pids[0] if running_pids else None

        # 1. CDP 엔드포인트 확인
        cdp_ready, targets_cnt, is_loopback = self.check_cdp_ready()

        if cdp_ready:
            return SlackStatusResult(
                status=SlackAgentModeStatus.AGENT_READY,
                message="Slack Agent Mode가 준비되었습니다 (CDP 활성, 백그라운드 탐색 가능).",
                cdp_port=self.cdp_port,
                running_pids=running_pids,
                main_pid=main_pid,
                cdp_targets_count=targets_cnt,
                is_loopback_only=is_loopback,
                slack_app_binary=slack_binary,
            )

        # 2. CDP가 비활성인데 Slack 프로세스가 실행 중인 경우
        if running_pids:
            return SlackStatusResult(
                status=SlackAgentModeStatus.RESTART_REQUIRED,
                message="Agent 백그라운드 모드를 사용하려면 Slack을 한 번 재시작해야 합니다.",
                cdp_port=self.cdp_port,
                running_pids=running_pids,
                main_pid=main_pid,
                cdp_targets_count=0,
                is_loopback_only=True,
                slack_app_binary=slack_binary,
            )

        # 3. Slack이 전혀 실행 중이지 않은 경우
        return SlackStatusResult(
            status=SlackAgentModeStatus.OFF,
            message="Slack 데스크톱 애플리케이션이 실행 중이지 않습니다.",
            cdp_port=self.cdp_port,
            running_pids=[],
            main_pid=None,
            cdp_targets_count=0,
            is_loopback_only=True,
            slack_app_binary=slack_binary,
        )

    def terminate_slack_gracefully(self, timeout_sec: float = 5.0) -> bool:
        """실행 중인 Slack 프로세스를 정상 종료(terminate) 후 대기합니다."""
        running_pids = self.get_running_slack_pids()
        if not running_pids:
            return True

        logger.info(f"실행 중인 Slack 프로세스({len(running_pids)}개) 정상 종료 요청...")
        for p in psutil.process_iter(["name"]):
            try:
                if p.info["name"] and "slack" in p.info["name"].lower():
                    p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # 종료 대기
        start_t = time.time()
        while time.time() - start_t < timeout_sec:
            time.sleep(0.5)
            if not self.get_running_slack_pids():
                logger.info("모든 Slack 프로세스가 정상 종료되었습니다.")
                return True

        # 타임아웃 초과 시 강제 종료
        logger.warning("정상 종료 타임아웃 초과. 강제 종료(kill)를 수행합니다.")
        for p in psutil.process_iter(["name"]):
            try:
                if p.info["name"] and "slack" in p.info["name"].lower():
                    p.kill()
            except Exception:
                pass
        time.sleep(1.0)
        return len(self.get_running_slack_pids()) == 0

    def launch_slack_in_agent_mode(self, timeout_sec: float = 25.0) -> SlackStatusResult:
        """Slack을 --remote-debugging-port 옵션으로 기동합니다."""
        slack_binary = self.find_slack_app_binary()
        if not slack_binary or not os.path.exists(slack_binary):
            return SlackStatusResult(
                status=SlackAgentModeStatus.ERROR,
                message=f"Slack 실행 바이너리를 찾을 수 없습니다: {slack_binary}",
                cdp_port=self.cdp_port,
            )

        # Slack Agent Mode 기동
        logger.info(f"Slack Agent Mode 기동: {slack_binary} --remote-debugging-port={self.cdp_port}")
        if sys.platform == "win32":
            # WMI Win32_Process.Create를 사용하여 부모 프로세스나 Job Object와 완전히 분리된 독립 데스크톱 프로세스로 기동
            cmd_line = f'"{slack_binary}" --remote-debugging-port={self.cdp_port}'
            ps_cmd = f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine = '{cmd_line}'}}"
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], check=False)
        else:
            subprocess.Popen([slack_binary, f"--remote-debugging-port={self.cdp_port}"])

        # CDP 준비 대기
        start_t = time.time()
        while time.time() - start_t < timeout_sec:
            time.sleep(1.0)
            ready, targets_cnt, is_loopback = self.check_cdp_ready()
            if ready:
                running_pids = self.get_running_slack_pids()
                return SlackStatusResult(
                    status=SlackAgentModeStatus.AGENT_READY,
                    message="Slack Agent Mode가 성공적으로 기동되었습니다.",
                    cdp_port=self.cdp_port,
                    running_pids=running_pids,
                    main_pid=running_pids[0] if running_pids else None,
                    cdp_targets_count=targets_cnt,
                    is_loopback_only=is_loopback,
                    slack_app_binary=slack_binary,
                )

        return SlackStatusResult(
            status=SlackAgentModeStatus.ERROR,
            message=f"Slack 기동 후 {timeout_sec}초 내에 CDP 포트({self.cdp_port}) 응답이 확인되지 않았습니다.",
            cdp_port=self.cdp_port,
            slack_app_binary=slack_binary,
        )

    def ensure_agent_ready(self, allow_restart: bool = False) -> SlackStatusResult:
        """Slack Agent Mode를 준비합니다.

        규칙:
        - 이미 AGENT_READY이면 재시작 없이 즉시 반환.
        - OFF 상태이면 자동으로 Agent Mode Slack 실행.
        - RESTART_REQUIRED 상태에서 allow_restart가 False이면 재시작하지 않고 RESTART_REQUIRED 반환.
        - RESTART_REQUIRED 상태에서 allow_restart가 True일 때만 정상 종료 후 Agent Mode로 재실행.
        """
        current_status = self.get_status()

        if current_status.status == SlackAgentModeStatus.AGENT_READY:
            return current_status

        if current_status.status == SlackAgentModeStatus.OFF:
            return self.launch_slack_in_agent_mode()

        if current_status.status in (
            SlackAgentModeStatus.RESTART_REQUIRED,
            SlackAgentModeStatus.NORMAL_SLACK,
        ):
            if not allow_restart:
                return current_status

            # 사용자가 명시적으로 재시작을 승인한 경우
            self.terminate_slack_gracefully()
            return self.launch_slack_in_agent_mode()

        return current_status
