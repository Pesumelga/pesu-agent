"""Unit tests for MVP 2.1 Background Feasibility logic."""

from unittest.mock import MagicMock, patch

from scripts.poc_cdp_background import (
    check_cdp_endpoint,
    check_slack_processes,
    check_uia_background_capabilities,
)


def test_check_cdp_endpoint_offline():
    """Test CDP endpoint check handles offline / closed port gracefully."""
    res = check_cdp_endpoint(port=59999)
    assert res["available"] is False
    assert "error" in res


def test_check_slack_processes_mock():
    """Test process scanner extracts PID, cmdline, and debug flags."""
    mock_proc = MagicMock()
    mock_proc.info = {
        "pid": 12345,
        "name": "slack.exe",
        "cmdline": ["slack.exe", "--remote-debugging-port=9222"],
    }
    mock_proc.net_connections.return_value = []

    with patch("psutil.process_iter", return_value=[mock_proc]):
        res = check_slack_processes()
        assert res["process_count"] == 1
        assert res["has_remote_debugging_flag"] is True
        assert res["debug_port"] == 9222


def test_check_uia_capabilities_when_slack_not_found():
    """Test UIA capability check handles missing Slack window cleanly."""
    with patch("pesu_agent.adapters.slack_desktop.SlackDesktopAdapter.find_slack_window") as mock_find:
        from pesu_agent.adapters.slack_desktop import SlackNotFoundError
        mock_find.side_effect = SlackNotFoundError("Slack window not found")
        res = check_uia_background_capabilities()
        assert res["slack_found"] is False
