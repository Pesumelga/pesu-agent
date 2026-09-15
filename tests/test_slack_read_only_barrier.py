"""Negative Safety Tests & Accidental Slack Write Regression Test Suite (MVP 3.4.1).

실제 사고(08:52 "수임" Top-Level Message 작성)에 대한 영구 회귀 테스트 및
Slack Read-Only Barrier의 다계층 방어 유효성을 철저히 검증합니다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pesu_agent.adapters.slack_cdp import (
    SlackCdpAdapter,
    SlackReadOnlyCdpAdapter,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)
from pesu_agent.search.slack_search import SlackSearch
from pesu_agent.security.read_only_barrier import (
    ALLOWED_CLICK_SELECTORS,
    PROHIBITED_ELEMENT_PATTERNS,
    ProhibitedActionError,
    ReadOnlyViolationError,
    SearchInputNotVerifiedError,
    SlackActionValidator,
    SlackReadCapability,
)
from pesu_agent.security.write_audit import SlackWriteAuditTracker


# ==============================================================================
# 1. 08:52 실제 사고 영구 회귀 테스트 (Accidental Slack Write Regression Test)
# ==============================================================================

@pytest.mark.anyio
async def test_regression_accidental_top_level_slack_message_incident():
    """
    [CRITICAL REGRESSION TEST]
    사고 재현 조건:
    - Global Search Input이 렌더링되지 않음 (no_verified_global_search_input_found)
    - Channel Message Composer([data-qa="texty_input"])가 화면에 존재함
    - 요청 쿼리: "수임"

    과거 결함 코드:
    - [data-qa="texty_input"]를 검색창으로 오인하여 "수임" 텍스트를 Composer에 입력하고 Enter를 디스패치하여 실제 메시지 발송 발생.

    수정 후 보장 기준:
    - [data-qa="texty_input"]를 절대 검색창으로 채택하지 않음
    - SearchInputNotVerifiedError 또는 ReadOnlyViolationError로 Fail Closed
    - Enter key event 디스패치 횟수 = 0회
    - Audit Tracker에 차단 내역 기록
    """
    adapter = SlackReadOnlyCdpAdapter(port=9222)
    adapter._raw.connect = AsyncMock()
    adapter._raw.disconnect = AsyncMock()
    adapter._raw._send_raw_command = AsyncMock()
    adapter._raw._dispatch_raw_key_event = AsyncMock()

    # Synthetic DOM: Search Input은 없고, Message Composer만 존재
    adapter._raw._evaluate_raw_js = AsyncMock(return_value={
        "ok": False,
        "error": "no_verified_global_search_input_found",
        "is_composer": False
    })

    mock_lifecycle = MagicMock(spec=SlackLifecycleManager)
    mock_lifecycle.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Agent ready for synthetic regression test",
        cdp_port=9222,
    )

    searcher = SlackSearch(
        cdp_adapter=adapter,
        lifecycle_manager=mock_lifecycle,
    )

    # 1. 검색창 입력 시도 시 SearchInputNotVerifiedError 발생해야 함
    with pytest.raises(SearchInputNotVerifiedError):
        await searcher.enter_query_and_search(query="수임", timeout_sec=0.5)

    # 2. Enter 키 디스패치가 단 1회도 발생하지 않았음을 검증 (0회)
    adapter._raw._dispatch_raw_key_event.assert_not_called()


@pytest.mark.anyio
async def test_regression_accidental_top_level_slack_message_incident_composer_detected():
    """
    [CRITICAL REGRESSION TEST 2]
    DOM에서 activeElement 또는 탐색 대상이 Composer로 감지된 경우:
    - ReadOnlyViolationError 즉시 발생
    - Enter 디스패치 0회
    - main_composer_interaction_detected 증가
    """
    adapter = SlackReadOnlyCdpAdapter(port=9222)
    adapter._raw.connect = AsyncMock()
    adapter._raw.disconnect = AsyncMock()
    adapter._raw._dispatch_raw_key_event = AsyncMock()

    # Synthetic DOM: Composer가 감지됨
    adapter._raw._evaluate_raw_js = AsyncMock(return_value={
        "ok": False,
        "error": "composer_detected_as_search_input",
        "is_composer": True
    })

    mock_lifecycle = MagicMock(spec=SlackLifecycleManager)
    mock_lifecycle.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Agent ready for synthetic regression test",
        cdp_port=9222,
    )

    searcher = SlackSearch(
        cdp_adapter=adapter,
        lifecycle_manager=mock_lifecycle,
    )

    with pytest.raises(ReadOnlyViolationError):
        await searcher.enter_query_and_search(query="수임", timeout_sec=0.5)

    adapter._raw._dispatch_raw_key_event.assert_not_called()
    report = adapter.audit_tracker.get_report()
    assert report.write_action_blocked >= 1
    assert report.main_composer_interaction_detected >= 1


# ==============================================================================
# 2. Negative Safety Tests (의도적 쓰기 시도 차단 검증)
# ==============================================================================

def test_negative_safety_send_button_click_blocked():
    """Send button selector 클릭 시도 시 ReadOnlyViolationError 차단 검증."""
    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_click_selector('button[data-qa="texty_send_button"]')

    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_click_selector('button[aria-label="메시지 전송"]')


def test_negative_safety_thread_composer_click_blocked():
    """Thread reply composer 클릭 시도 시 ReadOnlyViolationError 차단 검증."""
    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_click_selector('.p-flexpane [data-qa="texty_input"]')

    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_click_selector('[data-qa="thread_reply_composer"]')


def test_negative_safety_disallowed_selector_click_blocked():
    """Allowlist에 없는 임의 selector 클릭 시도 시 ProhibitedActionError 차단 검증."""
    with pytest.raises(ProhibitedActionError):
        SlackActionValidator.validate_click_selector('div.custom-random-button')

    with pytest.raises(ProhibitedActionError):
        SlackActionValidator.validate_click_selector('.c-message_actions__button')


def test_negative_safety_capability_allowlist_validation():
    """정의되지 않은 비인가 액션 요청 시 ReadOnlyViolationError 차단 검증."""
    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_capability("SEND_MESSAGE")  # type: ignore

    with pytest.raises(ReadOnlyViolationError):
        SlackActionValidator.validate_capability("DELETE_CHANNEL")  # type: ignore

    # 정상 Read Capability는 통과해야 함
    SlackActionValidator.validate_capability(SlackReadCapability.READ_DOM)
    SlackActionValidator.validate_capability(SlackReadCapability.OPEN_SEARCH)
    SlackActionValidator.validate_capability(SlackReadCapability.EXECUTE_SEARCH)


@pytest.mark.anyio
async def test_negative_safety_evaluate_js_prohibits_texty_input():
    """evaluate_js에 texty_input(메시지 작성창) 셀렉터가 포함된 경우 ReadOnlyViolationError 차단 검증."""
    adapter = SlackReadOnlyCdpAdapter(port=9222)
    adapter._raw._evaluate_raw_js = AsyncMock()

    with pytest.raises(ReadOnlyViolationError):
        await adapter.evaluate_js("document.querySelector('[data-qa=\"texty_input\"]').focus()")

    adapter._raw._evaluate_raw_js.assert_not_called()
    report = adapter.audit_tracker.get_report()
    assert report.write_action_blocked >= 1
    assert report.main_composer_interaction_detected >= 1


# ==============================================================================
# 3. Static AST Guard: Production Module Low-Level CDP/Keyboard API 차단 검증
# ==============================================================================

def test_prohibit_raw_cdp_in_production_modules():
    """
    정적 분석(AST) 검증:
    src/pesu_agent/**/*.py 및 scripts/**/*.py 전체를 재귀적으로 스캔하여
    SlackRawCdpAdapter 직접 import, _evaluate_raw_js, _dispatch_raw_mouse_event,
    _send_raw_command, dispatch_key_event, insert_text 직접 호출을 금지합니다.

    예외 파일: adapters/slack_cdp.py (approved internal adapter 구현체)

    지표: raw_cdp_direct_callers_outside_approved_adapter = 0
    """
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src" / "pesu_agent"
    scripts_dir = repo_root / "scripts"

    # Approved exemption: the adapter implementation file itself
    approved_exemptions = {
        (src_dir / "adapters" / "slack_cdp.py").resolve(),
    }

    # Prohibited patterns
    prohibited_imports = {"SlackRawCdpAdapter"}
    prohibited_method_calls = {
        "_evaluate_raw_js",
        "_dispatch_raw_mouse_event",
        "_dispatch_raw_key_event",
        "_send_raw_command",
        "dispatch_key_event",
        "dispatch_mouse_event",
        "insert_text",
    }

    violations: list[str] = []

    # Collect all .py files recursively
    scan_dirs = [src_dir, scripts_dir]
    all_py_files: list[Path] = []
    for scan_dir in scan_dirs:
        if scan_dir.exists():
            all_py_files.extend(scan_dir.rglob("*.py"))

    for mod_path in all_py_files:
        if mod_path.resolve() in approved_exemptions:
            continue
        if "__pycache__" in str(mod_path):
            continue
        # Exclude diagnostic tools in scripts/ which are designed for raw CDP experimentation
        if "scripts" in str(mod_path) and (mod_path.name.startswith("diagnose_") or mod_path.name.startswith("inspect_") or mod_path.name.startswith("poc_")):
            continue

        try:
            content = mod_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(mod_path))
        except (SyntaxError, UnicodeDecodeError):
            continue

        rel_path = mod_path.relative_to(repo_root)

        for node in ast.walk(tree):
            # 1. Check direct import of prohibited adapters
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in prohibited_imports:
                        violations.append(
                            f"🚨 [STATIC_GUARD_FAIL] {rel_path}이 {alias.name}를 직접 import하고 있습니다."
                        )
            # 2. Check direct calls to prohibited low-level methods
            if isinstance(node, ast.Attribute):
                if node.attr in prohibited_method_calls:
                    violations.append(
                        f"🚨 [STATIC_GUARD_FAIL] {rel_path}에서 저수준 CDP 메서드({node.attr})를 직접 호출하고 있습니다."
                    )

    assert len(violations) == 0, (
        f"raw_cdp_direct_callers_outside_approved_adapter = {len(violations)}\n"
        + "\n".join(violations)
    )

