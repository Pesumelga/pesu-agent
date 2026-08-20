"""Unit tests for SlackLifecycleManager and Agent Mode states (MVP 2.3)."""

from unittest.mock import MagicMock, patch

from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)


def test_status_when_slack_off():
    """Scenario 1: Slack is not running -> status is OFF."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_running_slack_pids", return_value=[]), \
         patch.object(manager, "check_cdp_ready", return_value=(False, 0, True)), \
         patch.object(manager, "find_slack_app_binary", return_value="C:/dummy/slack.exe"):

        res = manager.get_status()
        assert res.status == SlackAgentModeStatus.OFF
        assert len(res.running_pids) == 0


def test_status_when_normal_slack_running():
    """Scenario 2: Normal Slack is running without CDP -> status is RESTART_REQUIRED, does not terminate."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_running_slack_pids", return_value=[1234, 5678]), \
         patch.object(manager, "check_cdp_ready", return_value=(False, 0, True)), \
         patch.object(manager, "find_slack_app_binary", return_value="C:/dummy/slack.exe"):

        res = manager.get_status()
        assert res.status == SlackAgentModeStatus.RESTART_REQUIRED
        assert "재시작" in res.message
        assert res.running_pids == [1234, 5678]


def test_ensure_agent_ready_refuses_auto_restart_when_not_allowed():
    """Scenario 2-B: ensure_agent_ready with allow_restart=False does not kill Slack."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_running_slack_pids", return_value=[1234]), \
         patch.object(manager, "check_cdp_ready", return_value=(False, 0, True)), \
         patch.object(manager, "terminate_slack_gracefully") as mock_term:

        res = manager.ensure_agent_ready(allow_restart=False)
        assert res.status == SlackAgentModeStatus.RESTART_REQUIRED
        mock_term.assert_not_called()


def test_ensure_agent_ready_with_explicit_restart():
    """Scenario 3: When allow_restart=True, gracefully restarts Slack in Agent Mode."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_status") as mock_get_status, \
         patch.object(manager, "terminate_slack_gracefully", return_value=True) as mock_term, \
         patch.object(manager, "launch_slack_in_agent_mode") as mock_launch:

        mock_get_status.return_value = SlackStatusResult(
            status=SlackAgentModeStatus.RESTART_REQUIRED,
            message="재시작 필요",
            cdp_port=9222,
            running_pids=[1234],
        )
        mock_launch.return_value = SlackStatusResult(
            status=SlackAgentModeStatus.AGENT_READY,
            message="Ready",
            cdp_port=9222,
            running_pids=[9999],
        )

        res = manager.ensure_agent_ready(allow_restart=True)
        assert res.status == SlackAgentModeStatus.AGENT_READY
        mock_term.assert_called_once()
        mock_launch.assert_called_once()


def test_agent_mode_reuse_without_restart():
    """Scenario 4: When already AGENT_READY, reuses existing instance without restart."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_running_slack_pids", return_value=[10468]), \
         patch.object(manager, "check_cdp_ready", return_value=(True, 2, True)), \
         patch.object(manager, "terminate_slack_gracefully") as mock_term, \
         patch.object(manager, "launch_slack_in_agent_mode") as mock_launch:

        res = manager.ensure_agent_ready(allow_restart=False)
        assert res.status == SlackAgentModeStatus.AGENT_READY
        assert res.cdp_targets_count == 2
        mock_term.assert_not_called()
        mock_launch.assert_not_called()


def test_consecutive_tasks_reuse_same_slack_process():
    """Scenario 5: Consecutive calls maintain the same PID without any restarts."""
    manager = SlackLifecycleManager(cdp_port=9222)
    with patch.object(manager, "get_running_slack_pids", return_value=[20000]), \
         patch.object(manager, "check_cdp_ready", return_value=(True, 2, True)), \
         patch.object(manager, "terminate_slack_gracefully") as mock_term:

        # Task 1
        res1 = manager.ensure_agent_ready(allow_restart=False)
        assert res1.status == SlackAgentModeStatus.AGENT_READY
        assert res1.main_pid == 20000

        # Task 2
        res2 = manager.ensure_agent_ready(allow_restart=False)
        assert res2.status == SlackAgentModeStatus.AGENT_READY
        assert res2.main_pid == 20000

        mock_term.assert_not_called()
