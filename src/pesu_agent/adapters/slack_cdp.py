"""Slack CDP (Chrome DevTools Protocol) Adapter (MVP 3).

Electron/Chromium 기반 Slack Desktop의 로컬 Remote Debugging 인터페이스(기본 9222)에
안전하게 연결하여 제한된 DOM 조회 및 조작(검색어 입력, 결과 읽기, 스크롤)을 수행합니다.

보안 원칙:
- 쿠키, 인증 토큰, 네트워크 가로채기, 로컬 DB 직접 접근 일절 배제
- 127.0.0.1 루프백 소켓 및 slack.exe 프로세스 검증
- 순수 UI DOM 읽기 및 검색 조작만 수행
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

import psutil
import websockets

from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)

logger = logging.getLogger(__name__)


class SlackCdpError(Exception):
    """Slack CDP 통신 관련 기본 예외."""


class SlackNotReadyError(SlackCdpError):
    """Slack이 Agent Mode로 준비되지 않았을 때 발생하는 예외."""


class SlackTargetNotFoundError(SlackCdpError):
    """Slack renderer page target을 찾을 수 없을 때 발생하는 예외."""


class SlackCdpAdapter:
    """Slack Desktop renderer와 CDP 프로토콜로 통신하는 어댑터."""

    def __init__(
        self,
        port: int = 9222,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        timeout_sec: float = 10.0,
    ):
        self.port = port
        self.lifecycle = lifecycle_manager or SlackLifecycleManager(cdp_port=port)
        self.timeout_sec = timeout_sec
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._msg_id = 0
        self._connected_target: Optional[dict[str, Any]] = None
        self._ref_count = 0

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        if hasattr(self._ws, "state"):
            state = getattr(self._ws, "state")
            if hasattr(state, "name"):
                return state.name == "OPEN"
            return state == 1
        if hasattr(self._ws, "closed"):
            return not self._ws.closed
        return True

    def get_cdp_targets(self) -> list[dict[str, Any]]:
        """CDP HTTP 엔드포인트(/json)로부터 활성 타깃 목록을 조회합니다."""
        url = f"http://127.0.0.1:{self.port}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PesuAgent/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            raise SlackCdpError(f"CDP 엔드포인트({url}) 연결 실패: {err}") from err
        return []

    def find_slack_renderer_target(self) -> dict[str, Any]:
        """Slack 메인 렌더러 page 타깃을 선택합니다 (스플래시/빈 페이지 배제)."""
        targets = self.get_cdp_targets()
        pages = [t for t in targets if t.get("type") == "page"]

        # 1. 'page' 타입 중 app.slack.com URL을 포함하는 타깃 최우선
        for t in pages:
            url = t.get("url", "")
            if "app.slack.com" in url or "slack.com" in url:
                return t

        # 2. 제목에 Slack 또는 워크스페이스명이 있는 타깃
        for t in pages:
            title = t.get("title", "").lower()
            if "slack" in title or "채널" in title or "dm" in title:
                return t

        # 3. 빈 URL(about:blank)이 아닌 타깃
        for t in pages:
            url = t.get("url", "")
            if url and url != "about:blank":
                return t

        # 4. 첫 번째 페이지 타깃 fallback
        if pages:
            return pages[0]

        raise SlackTargetNotFoundError(
            f"Slack 메인 렌더러 타깃을 찾을 수 없습니다. (발견된 타깃 수: {len(targets)})"
        )

    async def connect(self) -> None:
        """Slack Agent Mode 상태를 검증하고 CDP WebSocket 세션을 연결합니다 (재진입 지원)."""
        if self.is_connected:
            self._ref_count += 1
            return

        # 1. 사전 수명주기 상태 확인
        status: SlackStatusResult = self.lifecycle.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 준비되지 않았습니다. (현재 상태: {status.status.value})\n"
                f"안내: {status.message}"
            )

        # 2. 루프백 및 slack.exe 프로세스 검증
        if not status.is_loopback_only:
            raise SlackCdpError("보안 경고: CDP 포트가 127.0.0.1 로컬 루프백이 아닌 외부 IP에 노출되어 연결을 중단합니다.")

        # 3. 렌더러 타깃 선택
        target = self.find_slack_renderer_target()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise SlackTargetNotFoundError("선택된 Slack 타깃에 WebSocket 디버깅 URL이 없습니다.")

        self._connected_target = target
        logger.info(f"CDP WebSocket 연결 시도: {ws_url} (Target: {target.get('title')!r})")

        self._ws = await websockets.connect(
            ws_url,
            max_size=20 * 1024 * 1024,  # 20MB
            ping_interval=20,
            ping_timeout=10,
        )
        self._ref_count = 1
        logger.info("CDP WebSocket 세션 연결 성공.")

    async def disconnect(self, force: bool = False) -> None:
        """WebSocket 세션을 안전하게 종료합니다 (재진입 참조 카운트 적용)."""
        if self._ref_count > 1 and not force:
            self._ref_count -= 1
            return

        self._ref_count = 0
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            finally:
                self._ws = None
                self._connected_target = None
                logger.info("CDP WebSocket 세션 종료.")

    async def evaluate_js(
        self,
        expression: str,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout_sec: Optional[float] = None,
    ) -> Any:
        """V8 JavaScript 엔진에서 표현식을 실행하고 반환값을 얻습니다."""
        if not self.is_connected:
            raise SlackCdpError("CDP WebSocket이 연결되어 있지 않습니다.")

        self._msg_id += 1
        req_id = self._msg_id
        payload = {
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
            },
        }

        timeout = timeout_sec or self.timeout_sec
        await self._ws.send(json.dumps(payload))

        start_time = asyncio.get_event_loop().time()
        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(f"CDP Runtime.evaluate 응답 타임아웃 ({timeout}s)")

            raw_msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            resp = json.loads(raw_msg)

            if resp.get("id") == req_id:
                if "error" in resp:
                    raise RuntimeError(f"CDP 에러: {resp['error']}")
                result_obj = resp.get("result", {})
                exception_details = result_obj.get("exceptionDetails")
                if exception_details:
                    raise RuntimeError(f"JS 실행 예외: {exception_details}")
                return result_obj.get("result", {}).get("value")

    async def send_cdp_command(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        timeout_sec: Optional[float] = None,
    ) -> Any:
        """임의의 CDP 메소드를 호출하고 응답을 수신합니다."""
        if not self.is_connected:
            raise SlackCdpError("CDP WebSocket이 연결되어 있지 않습니다.")

        self._msg_id += 1
        req_id = self._msg_id
        payload = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        timeout = timeout_sec or self.timeout_sec
        await self._ws.send(json.dumps(payload))

        start_time = asyncio.get_event_loop().time()
        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(f"CDP {method} 응답 타임아웃 ({timeout}s)")

            raw_msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            resp = json.loads(raw_msg)

            if resp.get("id") == req_id:
                if "error" in resp:
                    raise RuntimeError(f"CDP 에러 ({method}): {resp['error']}")
                return resp.get("result", {})

    async def dispatch_mouse_event(
        self,
        type_: str,
        x: int,
        y: int,
        button: str = "left",
        click_count: int = 1,
    ) -> None:
        """CDP Input.dispatchMouseEvent를 통해 가상 마우스 이벤트를 전송합니다 (OS 마우스 비침범)."""
        if not self.is_connected:
            raise SlackCdpError("CDP WebSocket이 연결되어 있지 않습니다.")

        self._msg_id += 1
        payload = {
            "id": self._msg_id,
            "method": "Input.dispatchMouseEvent",
            "params": {
                "type": type_,
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
            },
        }
        await self._ws.send(json.dumps(payload))

    async def dispatch_key_event(
        self,
        type_: str,
        text: str = "",
        key: str = "",
        code: str = "",
        windows_virtual_key_code: int = 0,
        modifiers: int = 0,
    ) -> None:
        """CDP Input.dispatchKeyEvent를 통해 가상 키 이벤트를 전송합니다."""
        if not self.is_connected:
            raise SlackCdpError("CDP WebSocket이 연결되어 있지 않습니다.")

        self._msg_id += 1
        payload = {
            "id": self._msg_id,
            "method": "Input.dispatchKeyEvent",
            "params": {
                "type": type_,
                "text": text,
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": windows_virtual_key_code,
                "modifiers": modifiers,
            },
        }
        await self._ws.send(json.dumps(payload))

    async def __aenter__(self) -> SlackCdpAdapter:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
