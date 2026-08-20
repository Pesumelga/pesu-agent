"""Adapters package."""

from pesu_agent.adapters.slack_cdp import (
    SlackCdpAdapter,
    SlackCdpError,
    SlackNotReadyError,
    SlackTargetNotFoundError,
)
from pesu_agent.adapters.slack_desktop import SlackDesktopAdapter

__all__ = [
    "SlackDesktopAdapter",
    "SlackCdpAdapter",
    "SlackCdpError",
    "SlackNotReadyError",
    "SlackTargetNotFoundError",
]
