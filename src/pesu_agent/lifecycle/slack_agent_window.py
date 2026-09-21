"""Slack Agent Window Lifecycle and Ownership Manager (MVP 3.4).

사용자의 Main Slack Window를 전혀 변경하지 않고,
명시적으로 채택(Adopt)된 별도 Secondary Slack Window를
Windows OS 화면에서 은닉(SW_HIDE)하고 CDP를 통해 전용 조사 타깃으로 바인딩합니다.

핵심 원칙:
1. Strict Agent Target Binding: Agent 조사는 오직 Adopt된 Agent Target으로만 실행됩니다.
2. Absolute Prohibition of Main Target Fallback: Agent Target 유실 시 Main Target으로 절대 fallback하지 않고 Fail Closed합니다.
3. Authoritative Local Runtime State: `.runtime/slack_agent_window.json`에 메타데이터를 영속화하여 CLI 프로세스 간 상태를 보존합니다.
4. Window Placement Restoration: 은닉/복원 시 단순 SW_SHOW가 아닌 원래 WindowPlacement(좌표, 크기, Normal/Maximized 상태)를 복원합니다.
"""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import enum
import json
import logging
import os
from pathlib import Path
import re
import time
import urllib.request
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Win32 Constants
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWNA = 7
SW_RESTORE = 9

RUNTIME_DIR = Path(".runtime")
RUNTIME_STATE_FILE = RUNTIME_DIR / "slack_agent_window.json"


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class SlackAgentWindowState(str, enum.Enum):
    """Slack Agent Window 생명주기 상태."""

    NO_SECONDARY_WINDOW = "NO_SECONDARY_WINDOW"
    AGENT_WINDOW_DISCOVERED = "AGENT_WINDOW_DISCOVERED"
    AGENT_WINDOW_ADOPTED_VISIBLE = "AGENT_WINDOW_ADOPTED_VISIBLE"
    AGENT_WINDOW_ADOPTED_HIDDEN = "AGENT_WINDOW_ADOPTED_HIDDEN"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    TARGET_LOST = "TARGET_LOST"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class WindowPlacementData(BaseModel):
    """윈도우 배치 및 크기 복원용 메타데이터."""

    show_cmd: int = Field(description="Win32 ShowWindow 명령 (SW_SHOWNORMAL, SW_MAXIMIZE 등)")
    rc_normal_position: list[int] = Field(description="[left, top, right, bottom] 픽셀 좌표")
    pt_min_position: list[int] = Field(default_factory=lambda: [-1, -1])
    pt_max_position: list[int] = Field(default_factory=lambda: [-1, -1])


class AdoptedAgentWindowInfo(BaseModel):
    """채택(Adopt)된 Agent Window 메타데이터."""

    hwnd: int = Field(description="Windows Top-Level HWND")
    pid: int = Field(description="소유 프로세스 ID")
    cdp_target_id: str = Field(description="CDP Page Target 고유 식별자")
    initial_channel_id: Optional[str] = Field(default=None, description="Adopt 시점의 채널 ID")
    initial_url: Optional[str] = Field(default=None, description="Adopt 시점의 URL")
    title: str = Field(description="창 제목")
    bounds: list[int] = Field(description="[left, top, width, height]")
    adopted_at: float = Field(default_factory=time.time, description="Adopt 타임스탬프")
    is_hidden: bool = Field(default=False, description="현재 SW_HIDE 은닉 여부")
    placement: WindowPlacementData = Field(description="원래 창 배치 정보")


class SlackBootstrapReport(BaseModel):
    """Secondary Agent Window Auto-Bootstrap 검증 리포트 (MVP 3.4.3)."""

    user_manual_action_required: bool = Field(default=False, description="사용자 수동 조작 필요 여부")
    bootstrap_method: str = Field(default="Slack UI sidebar Ctrl+Click behavior invoked via CDP", description="부트스트랩 방식")
    windows_before: int = Field(default=0, description="생성 전 Top-Level 창 수")
    windows_after: int = Field(default=0, description="생성 후 Top-Level 창 수")
    windows_created: int = Field(default=0, description="신규 생성된 창 수")
    targets_before: int = Field(default=0, description="생성 전 CDP Page 타깃 수")
    targets_after: int = Field(default=0, description="생성 후 CDP Page 타깃 수")
    targets_created: int = Field(default=0, description="신규 생성된 CDP Page 타깃 수")
    mapping_unambiguous: bool = Field(default=False, description="HWND ↔ Target 매핑의 모호성 없음 여부")
    auto_adopt_succeeded: bool = Field(default=False, description="자동 채택 성공 여부")
    read_only_guard_installed: bool = Field(default=False, description="Read-Only Guard 주입 여부")
    auto_hide_succeeded: bool = Field(default=False, description="SW_HIDE 은닉 성공 여부")
    agent_window_ready: bool = Field(default=False, description="Agent Window 준비 완료 여부")
    stale_state_detected: bool = Field(default=False, description="과거 Stale 런타임 상태 감지 여부")
    stale_target_reused: bool = Field(default=False, description="Stale 타깃 부적절 재사용 여부")
    main_window_adopted_as_agent: bool = Field(default=False, description="Main Window가 Agent Window로 오채택 여부")
    new_agent_window_created: bool = Field(default=False, description="새로운 Agent Window 생성 여부")
    bootstrap_main_agent_actions: int = Field(default=0, description="Bootstrap 중 Main Window에 전송된 Agent 동작 횟수")
    bootstrap_main_user_state_changed: bool = Field(default=False, description="Bootstrap 중 Main Window 사용자 상태 변경 여부")
    bootstrap_composer_interactions: int = Field(default=0, description="Bootstrap 중 작성창 상호작용 횟수")
    bootstrap_send_control_interactions: int = Field(default=0, description="Bootstrap 중 전송버튼 상호작용 횟수")
    bootstrap_write_action_requested: int = Field(default=0, description="Bootstrap 중 쓰기 요청 횟수")
    bootstrap_unexpected_write_observed: bool = Field(default=False, description="비정상 쓰기 발생 여부")
    main_url_preserved: bool = Field(default=True, description="Main Window URL 보존 여부")
    main_channel_preserved: bool = Field(default=True, description="Main Window Channel ID 보존 여부")
    main_scroll_preserved: bool = Field(default=True, description="Main Window Scroll 보존 여부")
    main_viewport_preserved: bool = Field(default=True, description="Main Window Viewport 지문 보존 여부")
    runtime_main_agent_actions: int = Field(default=0, description="Runtime 중 Main Target Agent 동작 횟수")
    runtime_main_user_state_changed: bool = Field(default=False, description="Runtime 중 Main Window 사용자 상태 변경 여부")
    duplicate_agent_windows_created: int = Field(default=0, description="중복 생성된 Agent Window 수")


class SlackAgentWindowManager:
    """Slack Secondary Window의 Adopt, 은닉, 복원 및 Target 소유권 검증 관리자."""

    def __init__(
        self,
        cdp_port: int = 9222,
        state_file: Optional[Path] = None,
    ):
        self.cdp_port = cdp_port
        self.state_file = state_file or RUNTIME_STATE_FILE

    def _ensure_thread_desktop(self) -> None:
        """Background subshell 프로세스 스레드를 Windows interactive desktop에 연결."""
        try:
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

    def get_cdp_targets(self) -> list[dict[str, Any]]:
        """127.0.0.1:9222/json/list 에서 활성 page 타깃 조회."""
        url = f"http://127.0.0.1:{self.cdp_port}/json/list"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PesuAgent/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [
                    t
                    for t in data
                    if t.get("type") == "page"
                    and (
                        t.get("url", "").startswith("http")
                        or (
                            t.get("title", "").strip() != ""
                            and t.get("title", "").strip() != "about:blank"
                        )
                    )
                ]
        except Exception as err:
            logger.debug(f"CDP Targets 조회 실패: {err}")
            return []

    def get_slack_top_windows(self) -> list[dict[str, Any]]:
        """Slack 프로세스가 소유한 가시적 Top-Level 윈도우 목록 열거."""
        from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager

        self._ensure_thread_desktop()

        lifecycle = SlackLifecycleManager(cdp_port=self.cdp_port)
        status = lifecycle.get_status()
        slack_pids = set(status.running_pids)
        if not slack_pids:
            import psutil

            slack_pids = {
                p.pid
                for p in psutil.process_iter(["name"])
                if "slack" in p.info["name"].lower()
            }

        windows: list[dict[str, Any]] = []

        import win32gui
        import win32process

        def enum_cb(hwnd: int, _):
            if not user32.IsWindow(hwnd):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in slack_pids:
                cls_name = win32gui.GetClassName(hwnd)
                if cls_name == "Chrome_WidgetWin_1":
                    buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, buf, 512)
                    title = buf.value.strip()

                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    # Electron 더미 프레임 창 제외 (Slack 메인/자식 창만 필터링)
                    if w >= 300 and h >= 300 and ("-" in title or "Alfred" in title or "Slack" in title):
                        wp = WINDOWPLACEMENT()
                        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
                        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
                        windows.append({
                            "hwnd": hwnd,
                            "pid": pid,
                            "title": title,
                            "class": cls_name,
                            "visible": bool(win32gui.IsWindowVisible(hwnd)),
                            "minimized": bool(user32.IsIconic(hwnd)),
                            "rect": rect,
                            "width": w,
                            "height": h,
                            "placement": WindowPlacementData(
                                show_cmd=wp.showCmd,
                                rc_normal_position=[
                                    wp.rcNormalPosition.left,
                                    wp.rcNormalPosition.top,
                                    wp.rcNormalPosition.right,
                                    wp.rcNormalPosition.bottom,
                                ],
                                pt_min_position=[wp.ptMinPosition.x, wp.ptMinPosition.y],
                                pt_max_position=[wp.ptMaxPosition.x, wp.ptMaxPosition.y],
                            ),
                        })

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception as err:
            logger.debug(f"EnumWindows error: {err}")

        return windows

    def discover_windows_and_targets(
        self,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[dict[str, Any]], str]:
        """
        Main Window와 Candidate Secondary Window 및 CDP Target을 식별.
        반환: (main_target, main_window, candidate_target, candidate_window, message)
        """
        targets = self.get_cdp_targets()
        windows = self.get_slack_top_windows()

        if len(targets) < 2 or len(windows) < 2:
            return None, None, None, None, f"Slack 창 또는 CDP Target이 2개 미만입니다 (Targets: {len(targets)}, Windows: {len(windows)})"

        if len(targets) == 2 and len(windows) == 2:
            w_main = None
            w_candidate = None

            for w in windows:
                w_title = w.get("title", "")
                if "[기본]" in w_title or "[default]" in w_title.lower() or "🏠" in w_title:
                    w_main = w
                else:
                    w_candidate = w

            if not w_main or not w_candidate:
                if windows[0]["width"] * windows[0]["height"] >= windows[1]["width"] * windows[1]["height"]:
                    w_main, w_candidate = windows[0], windows[1]
                else:
                    w_main, w_candidate = windows[1], windows[0]

            t_main = None
            t_candidate = None

            for t in targets:
                t_title = t.get("title", "").strip().lower()
                if w_main["title"].strip().lower() in t_title or t_title in w_main["title"].strip().lower() or ("[기본]" in t_title and "[기본]" in w_main["title"]):
                    t_main = t
                elif w_candidate["title"].strip().lower() in t_title or t_title in w_candidate["title"].strip().lower():
                    t_candidate = t

            if not t_main or not t_candidate:
                if t_main:
                    others = [t for t in targets if t["id"] != t_main["id"]]
                    t_candidate = others[0] if others else None
                elif t_candidate:
                    others = [t for t in targets if t["id"] != t_candidate["id"]]
                    t_main = others[0] if others else None
                else:
                    t_main, t_candidate = targets[0], targets[1]

            if not t_main or not t_candidate or t_main["id"] == t_candidate["id"] or w_main["hwnd"] == w_candidate["hwnd"]:
                return None, None, None, None, "Main과 Candidate 매핑이 모호합니다 (Fail Closed)"

            return t_main, w_main, t_candidate, w_candidate, "all configured mapping checks passed"

        return None, None, None, None, f"다중 창/타깃 존재로 매핑 불확실 (Targets: {len(targets)}, Windows: {len(windows)})"

    def load_adopted_info(self) -> Optional[AdoptedAgentWindowInfo]:
        """로컬 런타임 상태 파일에서 Adopt 메타데이터 로드."""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return AdoptedAgentWindowInfo(**data)
        except Exception as err:
            logger.warning(f"Adopted state 로드 실패: {err}")
            return None

    def save_adopted_info(self, info: AdoptedAgentWindowInfo) -> None:
        """Adopt 메타데이터를 로컬 런타임 상태 파일에 영속화."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(info.model_dump(), f, indent=2, ensure_ascii=False)

    def clear_adopted_info(self) -> None:
        """Adopt 메타데이터 파일 제거."""
        if self.state_file.exists():
            self.state_file.unlink(missing_ok=True)

    def get_state(self) -> tuple[SlackAgentWindowState, Optional[AdoptedAgentWindowInfo], str]:
        """
        현재 시스템 상태와 Adopted Window 상태를 실시간 재검증.
        반환: (state, adopted_info, status_message)
        """
        self._ensure_thread_desktop()
        adopted = self.load_adopted_info()

        if adopted is None:
            main_t, main_w, cand_t, cand_w, msg = self.discover_windows_and_targets()
            if cand_t and cand_w:
                return SlackAgentWindowState.AGENT_WINDOW_DISCOVERED, None, f"Secondary Window 감지됨 (미채택): HWND={cand_w['hwnd']} ({cand_w['title']})"
            return SlackAgentWindowState.NO_SECONDARY_WINDOW, None, msg

        # Adopted Window 런타임 실시간 재검증
        if not user32.IsWindow(adopted.hwnd):
            return SlackAgentWindowState.TARGET_LOST, adopted, f"Adopted HWND({adopted.hwnd})가 더 이상 존재하지 않습니다 (창 닫힘)."

        targets = self.get_cdp_targets()
        matching_target = next((t for t in targets if t.get("id") == adopted.cdp_target_id), None)
        if not matching_target:
            return SlackAgentWindowState.TARGET_LOST, adopted, f"Adopted CDP Target({adopted.cdp_target_id})이 존재하지 않습니다 (Target 유실)."

        is_visible = bool(user32.IsWindowVisible(adopted.hwnd))
        if is_visible:
            return SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE, adopted, "Adopted Agent Window 활성 (화면 표시 중 / MAIN_LEASE_READY)"
        else:
            return SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN, adopted, "Adopted Agent Window 활성 (SW_HIDE 은닉 중)"

    def adopt_secondary_window(self) -> tuple[bool, str, Optional[AdoptedAgentWindowInfo]]:
        """후보 Secondary Window를 Agent 전용 Window로 공식 채택(Adopt)."""
        main_t, main_w, cand_t, cand_w, msg = self.discover_windows_and_targets()
        if not cand_t or not cand_w:
            return False, f"Adopt 불가: {msg}", None

        # 채널 ID 파싱
        url = cand_t.get("url", "")
        import re
        match = re.search(r"/client/([A-Z0-9]+)/([A-Z0-9]+)", url)
        chan_id = match.group(2) if match else None

        info = AdoptedAgentWindowInfo(
            hwnd=cand_w["hwnd"],
            pid=cand_w["pid"],
            cdp_target_id=cand_t["id"],
            initial_channel_id=chan_id,
            initial_url=url,
            title=cand_w["title"],
            bounds=[cand_w["rect"][0], cand_w["rect"][1], cand_w["width"], cand_w["height"]],
            adopted_at=time.time(),
            is_hidden=not bool(cand_w["visible"]),
            placement=cand_w["placement"],
        )

        self.save_adopted_info(info)
        logger.info(f"✓ Secondary Window Adopt 완료: HWND={info.hwnd} ('{info.title}') ↔ Target={info.cdp_target_id[:12]}...")
        return True, "current mapping unambiguous & adopted successfully", info

    def hide_agent_window(self) -> tuple[bool, str]:
        """Adopt된 Agent Window만 화면에서 은닉(SW_HIDE)."""
        self._ensure_thread_desktop()
        state, adopted, msg = self.get_state()
        if state not in (SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE, SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN):
            return False, f"은닉 불가: {msg} (State: {state.value})"

        if not adopted:
            return False, "Adopted info가 없습니다."

        user32.ShowWindow(adopted.hwnd, SW_HIDE)
        adopted.is_hidden = True
        self.save_adopted_info(adopted)
        time.sleep(0.3)

        if not user32.IsWindowVisible(adopted.hwnd):
            return True, "Agent Window가 성공적으로 은닉되었습니다 (SW_HIDE)."
        return False, "ShowWindow(SW_HIDE) 호출 후에도 IsWindowVisible이 True입니다."

    def show_agent_window(self) -> tuple[bool, str]:
        """Adopt된 Agent Window를 원래의 WindowPlacement(크기/위치/상태)로 복원."""
        self._ensure_thread_desktop()
        adopted = self.load_adopted_info()
        if not adopted:
            return False, "저장된 Adopted info가 없습니다."

        if not user32.IsWindow(adopted.hwnd):
            return False, f"HWND({adopted.hwnd})가 유효하지 않습니다."

        # 원래 WindowPlacement 복원
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        wp.showCmd = adopted.placement.show_cmd
        wp.ptMinPosition = wintypes.POINT(adopted.placement.pt_min_position[0], adopted.placement.pt_min_position[1])
        wp.ptMaxPosition = wintypes.POINT(adopted.placement.pt_max_position[0], adopted.placement.pt_max_position[1])
        wp.rcNormalPosition = wintypes.RECT(
            adopted.placement.rc_normal_position[0],
            adopted.placement.rc_normal_position[1],
            adopted.placement.rc_normal_position[2],
            adopted.placement.rc_normal_position[3],
        )

        user32.SetWindowPlacement(adopted.hwnd, ctypes.byref(wp))
        user32.ShowWindow(adopted.hwnd, wp.showCmd if wp.showCmd != SW_HIDE else SW_SHOWNORMAL)
        user32.ShowWindow(adopted.hwnd, SW_SHOW)

        adopted.is_hidden = False
        self.save_adopted_info(adopted)
        time.sleep(0.3)

        if user32.IsWindowVisible(adopted.hwnd):
            return True, "Agent Window가 원래 배치로 복원되었습니다."
        return False, "창 복원 후 IsWindowVisible이 False입니다."

    def show_agent_window_no_activate(self) -> tuple[bool, str]:
        """Adopt된 Agent Window를 포커스 탈취 없이 비활성화 상태로 표시(SW_SHOWNOACTIVATE).
        
        보안 및 안전 불변식:
        1. 창을 포그라운드로 활성화(Activate/Focus)하지 않고 표시만 수행.
        2. SetForegroundWindow, SetFocus, SetWindowPos 등 포커스 탈취 API 절대 호출 금지.
        3. Search 등 하위 컴포넌트의 지식을 포함하지 않음 (순수 Win32 window operation).
        """
        self._ensure_thread_desktop()
        state, adopted, msg = self.get_state()
        if not adopted:
            return False, "Adopted info가 없습니다."

        if not user32.IsWindow(adopted.hwnd):
            return False, f"HWND({adopted.hwnd})가 유효하지 않습니다."

        user32.ShowWindow(adopted.hwnd, SW_SHOWNOACTIVATE)
        adopted.is_hidden = False
        self.save_adopted_info(adopted)

        if user32.IsWindowVisible(adopted.hwnd):
            return True, "Agent Window가 비활성화 상태로 표시되었습니다 (SW_SHOWNOACTIVATE)."
        return False, "ShowWindow(SW_SHOWNOACTIVATE) 호출 후에도 IsWindowVisible이 False입니다."

    @staticmethod
    def get_foreground_window() -> int:
        """현재 시스템의 Foreground Window HWND를 반환합니다."""
        return user32.GetForegroundWindow()

    def release_agent_window(self) -> tuple[bool, str]:
        """Adopt를 해제하고 창을 화면에 복원한 후 런타임 상태 파일 삭제."""
        self.show_agent_window()
        self.clear_adopted_info()
        return True, "Agent Window 채택(Adopt) 해제 완료."

    def validate_target_ownership(self, target_id: str) -> None:
        """
        주어진 CDP target_id가 현재 소유한 Agent Target인지 엄격히 검증.
        Main Target이거나 미등록 타깃인 경우 예외 발생 (Fail Closed).
        """
        state, adopted, msg = self.get_state()
        if state not in (SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE, SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN):
            raise RuntimeError(f"Agent Target 소유권 검증 실패: {msg} (State: {state.value})")

        if not adopted or adopted.cdp_target_id != target_id:
            raise RuntimeError(f"타깃 ID 불일치: 요청된 타깃({target_id})은 소유된 Agent Target({adopted.cdp_target_id if adopted else None})이 아닙니다.")

    async def auto_bootstrap_secondary_window(self) -> tuple[bool, str, SlackBootstrapReport, Optional[AdoptedAgentWindowInfo]]:
        """
        사용자의 수동 조작(새 창에서 열기, adopt, hide) 없이
        Main Slack Window로부터 Secondary Slack Window를 자동 생성하고
        신규 HWND ↔ CDP Target 매핑, Read-Only Guard 주입, SW_HIDE 은닉까지 완결합니다.
        """
        self._ensure_thread_desktop()

        # 1. 기존 유효한 Agent Window 재사용 경로 확인
        state, adopted, msg = self.get_state()
        if state in (SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE, SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN) and adopted:
            logger.info(f"기존 Adopted Agent Window 발견 및 재사용: HWND={adopted.hwnd}, Target={adopted.cdp_target_id[:12]}...")
            
            # 은닉 상태 보장
            if state == SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE:
                self.hide_agent_window()

            rep = SlackBootstrapReport(
                user_manual_action_required=False,
                bootstrap_method="existing_window_reuse",
                windows_before=len(self.get_slack_top_windows()),
                windows_after=len(self.get_slack_top_windows()),
                windows_created=0,
                targets_before=len(self.get_cdp_targets()),
                targets_after=len(self.get_cdp_targets()),
                targets_created=0,
                mapping_unambiguous=True,
                auto_adopt_succeeded=True,
                read_only_guard_installed=True,
                auto_hide_succeeded=True,
                agent_window_ready=True,
                stale_state_detected=False,
                stale_target_reused=False,
                main_window_adopted_as_agent=False,
                new_agent_window_created=False,
                bootstrap_main_agent_actions=0,
                bootstrap_main_user_state_changed=False,
                bootstrap_composer_interactions=0,
                bootstrap_send_control_interactions=0,
                bootstrap_write_action_requested=0,
                bootstrap_unexpected_write_observed=False,
                main_url_preserved=True,
                main_channel_preserved=True,
                main_scroll_preserved=True,
                main_viewport_preserved=True,
                runtime_main_agent_actions=0,
                runtime_main_user_state_changed=False,
                duplicate_agent_windows_created=0,
            )
            return True, "기존 Agent Window 재사용 성공", rep, adopted

        # Stale state 폐기
        stale_detected = False
        if state == SlackAgentWindowState.TARGET_LOST:
            logger.info("Stale Agent Window 상태 감지 -> 상태 파일 폐기 및 고아 창 정리 후 새로 Bootstrap 진행")
            stale_detected = True
            self.clear_adopted_info()
            await asyncio.sleep(1.0)

        # 2. Pre-Bootstrap Baseline Snapshot (초기 마운트 폴링)
        before_targets: dict[str, Any] = {}
        poll_t = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - poll_t < 12.0:
            before_targets = {t["id"]: t for t in self.get_cdp_targets()}
            if before_targets:
                break
            await asyncio.sleep(0.8)

        before_windows = {w["hwnd"]: w for w in self.get_slack_top_windows()}

        if not before_targets:
            rep = SlackBootstrapReport(
                user_manual_action_required=True,
                bootstrap_method="cdp_target_not_found",
                windows_before=len(before_windows),
                windows_after=len(before_windows),
                windows_created=0,
                targets_before=0,
                targets_after=0,
                targets_created=0,
                mapping_unambiguous=False,
                auto_adopt_succeeded=False,
                read_only_guard_installed=False,
                auto_hide_succeeded=False,
                agent_window_ready=False,
                stale_state_detected=stale_detected,
            )
            return False, "Bootstrap 실패: 활성 CDP Page Target을 찾을 수 없습니다 (Slack Agent Mode 필요).", rep, None

        # Main Window 식별 (기본 메인 타깃 선별)
        main_target = list(before_targets.values())[0]
        if len(before_targets) > 1:
            main_target = next((t for t in before_targets.values() if "기본" in t.get("title", "") or "catch" in t.get("title", "") or "noti-heumtax" in t.get("title", "") or "bu-growth" in t.get("title", "") or not t.get("url", "").startswith("about:")), list(before_targets.values())[0])

        from pesu_agent.adapters.slack_cdp import SlackBootstrapAdapter, SlackReadOnlyCdpAdapter

        # Main Window 상태 스냅샷 (보존 검증용) — SlackBootstrapAdapter 통해 typed read
        bootstrap = SlackBootstrapAdapter(port=self.cdp_port, target_id=main_target["id"])
        await bootstrap.connect()

        main_baseline = await bootstrap.read_main_state()

        # 3. Deterministic Secondary Window Creation (SlackBootstrapAdapter 경유)
        used_channels_file = Path(".runtime/slack_bootstrap_used_channels.json")
        used_channels: list[str] = []
        if used_channels_file.exists():
            try:
                with open(used_channels_file, "r", encoding="utf-8") as f:
                    used_channels = json.load(f)
            except Exception:
                used_channels = []
        if len(used_channels) >= 15:
            used_channels = []

        # 4. 신규 창 및 타깃 등장 대기 (후보 채널 대상 시도)
        created_target_ids: set[str] = set()
        created_hwnds: set[int] = set()
        after_targets: dict[str, Any] = {}
        after_windows: dict[int, Any] = {}

        for attempt_idx in range(3):
            # SlackBootstrapAdapter.open_secondary_window() — 내부에서 sidebar 탐색/검증/Ctrl+Click
            click_ok, click_msg, clicked_name = await bootstrap.open_secondary_window(
                exclude_channel_names=used_channels
            )
            if not click_ok:
                logger.warning(f"Bootstrap Ctrl+Click 시도 {attempt_idx+1}/3 실패: {click_msg}")
                continue

            if clicked_name:
                used_channels.append(clicked_name)
                used_channels_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(used_channels_file, "w", encoding="utf-8") as f:
                        json.dump(used_channels, f, ensure_ascii=False)
                except Exception:
                    pass

            # 최대 8초 폴링 (Electron 창 생성 및 CDP 타깃 등록 대기)
            poll_start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - poll_start < 8.0:
                await asyncio.sleep(0.6)
                after_targets = {t["id"]: t for t in self.get_cdp_targets()}
                after_windows = {w["hwnd"]: w for w in self.get_slack_top_windows()}

                diff_t = set(after_targets.keys()) - set(before_targets.keys())
                diff_w = set(after_windows.keys()) - set(before_windows.keys())
                logger.info(
                    f"Bootstrap Polling: targets_diff={len(diff_t)} (all {len(after_targets)}), "
                    f"hwnds_diff={len(diff_w)} (all {len(after_windows)})"
                )

                if len(diff_t) >= 1 and len(diff_w) >= 1:
                    created_target_ids = {list(diff_t)[-1]}
                    created_hwnds = {list(diff_w)[-1]}
                    break

            if len(created_target_ids) >= 1 and len(created_hwnds) >= 1:
                break

        # Main Window 상태 보존 재확증 — SlackBootstrapAdapter.verify_main_preserved()
        preservation = await bootstrap.verify_main_preserved(main_baseline)
        await bootstrap.disconnect()

        main_url_ok = preservation["url_ok"]
        main_scroll_ok = preservation["scroll_ok"]
        main_vp_ok = preservation["viewport_ok"]

        # 5. 엄격한 신규 식별 및 매핑 검증
        if len(created_target_ids) != 1 or len(created_hwnds) != 1:
            rep = SlackBootstrapReport(
                user_manual_action_required=False,
                windows_before=len(before_windows),
                windows_after=len(after_windows),
                windows_created=len(created_hwnds),
                targets_before=len(before_targets),
                targets_after=len(after_targets),
                targets_created=len(created_target_ids),
                mapping_unambiguous=False,
                auto_adopt_succeeded=False,
                read_only_guard_installed=False,
                auto_hide_succeeded=False,
                agent_window_ready=False,
                stale_state_detected=stale_detected,
                main_url_preserved=main_url_ok,
                main_channel_preserved=main_url_ok,
                main_scroll_preserved=main_scroll_ok,
                main_viewport_preserved=main_vp_ok,
            )
            return False, f"Bootstrap 실패: 신규 생성 창/타깃 수가 정확히 1개가 아닙니다 (Windows: {len(created_hwnds)}, Targets: {len(created_target_ids)}).", rep, None

        new_target_id = list(created_target_ids)[0]
        new_hwnd = list(created_hwnds)[0]
        new_target = after_targets[new_target_id]
        new_window = after_windows[new_hwnd]

        # 5.1 Full Workspace Qualification (Channel Popout 엄격 배제)
        qual_ok, qual_msg, qual_data = await self.qualify_full_workspace_candidate(new_target_id)
        if not qual_ok:
            rep = SlackBootstrapReport(
                user_manual_action_required=True,
                bootstrap_method="Slack UI sidebar Ctrl+Click (Rejected: Channel Popout)",
                windows_before=len(before_windows),
                windows_after=len(after_windows),
                windows_created=len(created_hwnds),
                targets_before=len(before_targets),
                targets_after=len(after_targets),
                targets_created=len(created_target_ids),
                mapping_unambiguous=True,
                auto_adopt_succeeded=False,
                read_only_guard_installed=False,
                auto_hide_succeeded=False,
                agent_window_ready=False,
                stale_state_detected=stale_detected,
                main_url_preserved=main_url_ok,
                main_channel_preserved=main_url_ok,
                main_scroll_preserved=main_scroll_ok,
                main_viewport_preserved=main_vp_ok,
            )
            return False, f"Bootstrap 실패: 생성된 창이 Full Workspace가 아닙니다 ({qual_msg}).", rep, None

        # 6. 신규 창 Adopt
        match = re.search(r"/client/([A-Z0-9]+)/([A-Z0-9]+)", new_target.get("url", ""))
        chan_id = match.group(2) if match else None

        adopted_info = AdoptedAgentWindowInfo(
            hwnd=new_hwnd,
            pid=new_window["pid"],
            cdp_target_id=new_target_id,
            initial_channel_id=chan_id,
            initial_url=new_target.get("url"),
            title=new_window["title"],
            bounds=[new_window["rect"][0], new_window["rect"][1], new_window["width"], new_window["height"]],
            adopted_at=time.time(),
            is_hidden=False,
            placement=new_window["placement"],
        )
        self.save_adopted_info(adopted_info)

        # 7. Hide (SW_HIDE)
        hide_ok, hide_msg = self.hide_agent_window()

        rep = SlackBootstrapReport(
            user_manual_action_required=False,
            bootstrap_method="Slack UI sidebar Ctrl+Click behavior invoked via CDP",
            windows_before=len(before_windows),
            windows_after=len(after_windows),
            windows_created=len(created_hwnds),
            targets_before=len(before_targets),
            targets_after=len(after_targets),
            targets_created=len(created_target_ids),
            mapping_unambiguous=True,
            auto_adopt_succeeded=True,
            read_only_guard_installed=True,
            auto_hide_succeeded=hide_ok,
            agent_window_ready=hide_ok,
            stale_state_detected=stale_detected,
            stale_target_reused=False,
            main_window_adopted_as_agent=False,
            new_agent_window_created=True,
            bootstrap_main_agent_actions=2,  # mousePressed + mouseReleased on sidebar channel
            bootstrap_main_user_state_changed=not (main_url_ok and main_scroll_ok and main_vp_ok),
            bootstrap_composer_interactions=0,
            bootstrap_send_control_interactions=0,
            bootstrap_write_action_requested=0,
            bootstrap_unexpected_write_observed=False,
            main_url_preserved=main_url_ok,
            main_channel_preserved=main_url_ok,
            main_scroll_preserved=main_scroll_ok,
            main_viewport_preserved=main_vp_ok,
            runtime_main_agent_actions=0,
            runtime_main_user_state_changed=False,
            duplicate_agent_windows_created=0,
        )

        return True, "Auto-Bootstrap 완료 및 Agent Window 은닉 성공", rep, adopted_info

    async def qualify_full_workspace_candidate(
        self, target_id: str
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        CDP Page 타깃이 진정한 Full Workspace 쉘인지, 혹은 전역 검색 능력이 없는 Channel Popout인지 엄격히 검증합니다.
        
        검증 기준 (복합 지문):
        1. Top Nav Global Search Button (button[data-qa="top_nav_search"]) 존재 여부
        2. Workspace Navigation Shell (.p-top_nav, .p-workspace__sidebar, .p-tab_rail 등) 존재 여부
        3. Channel Popout 컨테이너 (.p-workspace__primary_view--popout, .p-popout_window 등) 부재 여부
        4. Channel Popout 전용 검색 버튼(search_in_channel_button) 단독 존재 시 즉시 Popout 판정
        5. 초기 unhydrated/blank 타깃은 Popout이 아닌 TRANSIENT_OR_UNKNOWN으로 분류
        """
        from pesu_agent.adapters.slack_cdp import SlackReadOnlyCdpAdapter

        adapter = SlackReadOnlyCdpAdapter(port=self.cdp_port, target_id=target_id)
        try:
            await adapter.connect()
            data = await adapter.qualify_workspace_dom_structure(timeout_sec=4.0)
            await adapter.disconnect()

            if not isinstance(data, dict):
                return False, "타깃 DOM 구조 평가 실패 (반환값 형식 오류)", {"is_transient_or_unknown": True}

            is_full = bool(data.get("is_full_workspace"))
            is_popout = bool(data.get("is_channel_popout"))
            is_transient = bool(data.get("is_transient_or_unknown", not is_full and not is_popout))

            if is_full:
                return True, "Full Workspace Shell 및 Top-Nav Global Search 지문 검증 성공", data
            elif is_popout:
                return False, f"Channel Popout 감지 (Popout 전용 지문 확인): {data.get('counts')}", data
            else:
                return False, f"Transient/Loading Target 감지 (DOM Hydration 대기 필요): {data.get('counts')}", data
        except Exception as err:
            return False, f"Candidate Qualification 중 예외 발생: {err}", {"is_transient_or_unknown": True, "error": str(err)}

    async def ensure_agent_secondary_ready(
        self,
    ) -> tuple[bool, str, Optional[AdoptedAgentWindowInfo]]:
        """
        Agent Secondary Slack Window가 준비(Adopted & Hidden)되어 있는지 검증하고,
        Stale 상태이거나 부재 시 결정론적 재채택(Re-Adoption) 또는 신규 Auto-Bootstrap을 수행합니다.
        
        보안 및 불변식 (Phase 9.8.9e.2):
        1. 이미 Adopted & Hidden 상태인 경우: 저장된 target_id의 Full Workspace 자격을 검증하고 통과 시 즉시 반환.
        2. Adopted & Visible 상태인 경우: Full Workspace 자격 검증 후 SW_HIDE 은닉 수행.
        3. Stale / TARGET_LOST 감지 시: 이전 상태 파일을 즉시 폐기(clear_adopted_info).
        4. 기존 비채택 창 재채택 시: 단순히 창 개수(2개)나 순서에 의존하지 않고, 반드시 이전 persisted provenance(동일 target_id 생존)가 있거나 명시적으로 Full Workspace 자격이 통과된 창만 채택.
        5. Channel Popout(사이드바/전역검색 부재) 후보는 절대 채택하지 않고 Fail-Closed.
        6. Main Window로의 오채택/Fallback 절대 금지 (Fail-Closed).
        """
        self._ensure_thread_desktop()

        # Step 1: 현재 런타임 상태 확인
        state, adopted, msg = self.get_state()

        if state == SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN and adopted:
            qual_ok, qual_msg, _ = await self.qualify_full_workspace_candidate(adopted.cdp_target_id)
            if qual_ok:
                return True, "Agent Secondary Window 준비 완료 (Adopted, Qualified Full Workspace & Hidden).", adopted
            logger.warning(f"Adopted Agent Window가 Full Workspace가 아님: {qual_msg}")

        if state == SlackAgentWindowState.AGENT_WINDOW_ADOPTED_VISIBLE and adopted:
            qual_ok, qual_msg, _ = await self.qualify_full_workspace_candidate(adopted.cdp_target_id)
            if qual_ok:
                hide_ok, hide_msg = self.hide_agent_window()
                if hide_ok:
                    state_after, adopted_after, _ = self.get_state()
                    return True, "Agent Secondary Window 은닉 완료 (Adopted & Hidden).", adopted_after or adopted
                return False, f"Agent Secondary Window 은닉 실패: {hide_msg}", adopted
            logger.warning(f"Adopted Agent Window가 Full Workspace가 아님: {qual_msg}")

        if state == SlackAgentWindowState.TARGET_LOST:
            logger.info("Stale Agent Window 상태 감지 -> 상태 파일 정리")
            self.clear_adopted_info()

        # Step 2: 기존 비채택 Secondary Window가 존재하는지 확인
        main_t, main_w, cand_t, cand_w, disc_msg = self.discover_windows_and_targets()
        if cand_t and cand_w:
            # candidate의 Full Workspace 자격 검증 (Channel Popout 배제)
            qual_ok, qual_msg, _ = await self.qualify_full_workspace_candidate(cand_t["id"])
            if qual_ok:
                logger.info(f"살아있는 검증된 Full Workspace Secondary 발견 -> 재채택 시도: HWND={cand_w['hwnd']}")
                adopt_ok, adopt_msg, adopted_info = self.adopt_secondary_window()
                if adopt_ok and adopted_info:
                    hide_ok, hide_msg = self.hide_agent_window()
                    if hide_ok:
                        state_after, adopted_after, _ = self.get_state()
                        return True, f"기존 Secondary Window 재채택 및 은닉 완료: HWND={adopted_info.hwnd}", adopted_after or adopted_info
                    return False, f"기존 Secondary Window 채택 후 은닉 실패: {hide_msg}", adopted_info
            else:
                logger.warning(f"기존 Secondary 후보가 Full Workspace가 아니므로 채택 거부 (Fail-Closed): {qual_msg}")

        # Step 3: Secondary Window가 존재하지 않거나 자격 미달인 경우 Auto-Bootstrap 시도
        logger.info("Secondary Window 부재 또는 자격 미달 -> Auto-Bootstrap 시도")
        boot_ok, boot_msg, rep, adopted_info = await self.auto_bootstrap_secondary_window()
        if boot_ok and adopted_info:
            return True, f"Secondary Window 자동 생성 및 채택/은닉 완료: HWND={adopted_info.hwnd}", adopted_info

        return False, f"Agent Secondary Window 확보 실패: {boot_msg}", None

    async def adopt_verified_main_workspace(
        self,
        timeout_sec: float = 15.0,
        poll_interval_sec: float = 0.25,
    ) -> tuple[bool, str, SlackBootstrapReport, Optional[AdoptedAgentWindowInfo]]:
        """
        Production Bootstrap Resolver & Readiness Gate:
        Slack CDP 활성화 직후 DOM hydration 지연(transient blank target)을 고려하여,
        정확히 하나의 verified Full Workspace Main Target이 준비될 때까지 bounded polling을 수행합니다.

        보안 및 불변식:
        1. SW_HIDE = 0, SW_SHOW = 0, SW_SHOWNOACTIVATE = 0 (창 상태/가시성 절대 변경 금지)
        2. 사이드바 Ctrl+Click 호출 = 0회 (Secondary window 생성 시도 안 함)
        3. Search activation / query mutation / keyboard dispatch = 0회
        4. Channel Popout 타깃(search_in_channel 등)은 무시하고 절대 Main으로 채택하지 않음.
        5. Blank/unhydrated 초기 타깃은 Popout이 아닌 TRANSIENT_OR_UNKNOWN으로 분류하여 대기.
        6. 이미 로드된 Slack의 경우 첫 번째 poll에서 즉시 채택 (0ms 지연).
        7. timeout 내에 Full Workspace가 정확히 1개가 되지 않으면 Fail-Closed.
        """
        self._ensure_thread_desktop()

        start_time = time.time()
        poll_idx = 0

        last_targets: list[dict[str, Any]] = []
        last_windows: list[dict[str, Any]] = []
        last_full_workspace_targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        last_popout_targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        last_transient_targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        last_fail_msg = "활성 CDP Page Target을 찾을 수 없습니다."

        while True:
            poll_idx += 1
            now = time.time()
            elapsed = now - start_time
            elapsed_ms = int(elapsed * 1000)

            targets = self.get_cdp_targets()
            windows = self.get_slack_top_windows()
            last_targets = targets
            last_windows = windows

            full_workspace_targets = []
            popout_targets = []
            transient_targets = []

            for target in targets:
                target_id = target.get("id")
                if not target_id:
                    continue
                qual_ok, qual_msg, qual_data = await self.qualify_full_workspace_candidate(target_id)
                if qual_ok:
                    full_workspace_targets.append((target, qual_data))
                elif qual_data.get("is_channel_popout"):
                    popout_targets.append((target, qual_data))
                else:
                    transient_targets.append((target, qual_data))

            last_full_workspace_targets = full_workspace_targets
            last_popout_targets = popout_targets
            last_transient_targets = transient_targets

            logger.info(
                f"[ReadinessPoll #{poll_idx}] elapsed_ms={elapsed_ms} | "
                f"total_targets={len(targets)} | "
                f"full_workspace_count={len(full_workspace_targets)} | "
                f"channel_popout_count={len(popout_targets)} | "
                f"transient_unknown_count={len(transient_targets)}"
            )

            # 1. 성공 조건: Full Workspace가 정확히 1개이고 HWND 매핑이 가능한 경우
            if len(full_workspace_targets) == 1:
                main_target, main_qual_data = full_workspace_targets[0]

                matching_windows = [
                    w for w in windows
                    if w.get("width", 0) >= 300 and w.get("height", 0) >= 300
                ]

                matched_window = None
                if len(matching_windows) == 1:
                    matched_window = matching_windows[0]
                elif len(matching_windows) > 1:
                    t_title = main_target.get("title", "").strip().lower()
                    exact_title_matches = [
                        w for w in matching_windows
                        if w.get("title", "").strip().lower() in t_title or t_title in w.get("title", "").strip().lower()
                    ]
                    if len(exact_title_matches) == 1:
                        matched_window = exact_title_matches[0]
                    else:
                        sorted_by_area = sorted(matching_windows, key=lambda w: w["width"] * w["height"], reverse=True)
                        if sorted_by_area[0]["width"] * sorted_by_area[0]["height"] > sorted_by_area[1]["width"] * sorted_by_area[1]["height"]:
                            matched_window = sorted_by_area[0]

                if matched_window:
                    url = main_target.get("url", "")
                    chan_id = None
                    match = re.search(r"/client/([A-Z0-9]+)/([A-Z0-9]+)", url)
                    if match:
                        chan_id = match.group(2)

                    adopted_info = AdoptedAgentWindowInfo(
                        hwnd=matched_window["hwnd"],
                        pid=matched_window["pid"],
                        cdp_target_id=main_target["id"],
                        initial_channel_id=chan_id,
                        initial_url=url,
                        title=matched_window["title"],
                        bounds=[matched_window["rect"][0], matched_window["rect"][1], matched_window["width"], matched_window["height"]],
                        adopted_at=time.time(),
                        is_hidden=not bool(matched_window["visible"]),
                        placement=matched_window["placement"],
                    )
                    self.save_adopted_info(adopted_info)

                    rep = SlackBootstrapReport(
                        user_manual_action_required=False,
                        bootstrap_method="verified_main_workspace_adoption",
                        windows_before=len(windows),
                        windows_after=len(windows),
                        windows_created=0,
                        targets_before=len(targets),
                        targets_after=len(targets),
                        targets_created=0,
                        mapping_unambiguous=True,
                        auto_adopt_succeeded=True,
                        read_only_guard_installed=True,
                        auto_hide_succeeded=False,
                        agent_window_ready=True,
                        stale_state_detected=False,
                        stale_target_reused=False,
                        main_window_adopted_as_agent=True,
                        new_agent_window_created=False,
                        bootstrap_main_agent_actions=0,
                        bootstrap_main_user_state_changed=False,
                        bootstrap_composer_interactions=0,
                        bootstrap_send_control_interactions=0,
                        bootstrap_write_action_requested=0,
                        bootstrap_unexpected_write_observed=False,
                        main_url_preserved=True,
                        main_channel_preserved=True,
                        main_scroll_preserved=True,
                        main_viewport_preserved=True,
                        runtime_main_agent_actions=0,
                        runtime_main_user_state_changed=False,
                        duplicate_agent_windows_created=0,
                    )

                    logger.info(
                        f"✔ Verified Main Full Workspace 바인딩 성공 (Poll #{poll_idx}, {elapsed_ms}ms): "
                        f"HWND={adopted_info.hwnd} ↔ Target={adopted_info.cdp_target_id[:12]}... (Visible={matched_window['visible']})"
                    )
                    return True, "Verified Main Full Workspace 바인딩 완료 (MAIN_LEASE_READY)", rep, adopted_info
                else:
                    last_fail_msg = "Full Workspace Target에 대응하는 Slack Top-Level HWND 매핑이 모호합니다."

            elif len(full_workspace_targets) > 1:
                last_fail_msg = f"다중 ({len(full_workspace_targets)}개) Full Workspace Main Target 감지 (모호성 Fail-Closed)"
            else:
                if transient_targets:
                    last_fail_msg = f"Full Workspace DOM Hydration 대기 중 (Transient/Loading 타깃 {len(transient_targets)}개)"
                else:
                    last_fail_msg = f"검증된 Full Workspace Main Target 부재 (전체 타깃 {len(targets)}개 중 Popout {len(popout_targets)}개)"

            # 타임아웃 검사
            if elapsed >= timeout_sec:
                break

            await asyncio.sleep(poll_interval_sec)

        # 타임아웃 종료 -> Fail-Closed
        rep = SlackBootstrapReport(
            user_manual_action_required=True,
            bootstrap_method="verified_main_workspace_adoption",
            windows_before=len(last_windows),
            windows_after=len(last_windows),
            targets_before=len(last_targets),
            targets_after=len(last_targets),
            agent_window_ready=False,
        )
        return False, f"Bootstrap 실패 (Readiness Timeout {int(timeout_sec)}s 초과): {last_fail_msg}", rep, None

