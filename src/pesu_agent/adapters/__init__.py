"""Adapters package for external desktop applications."""

from pesu_agent.adapters.slack_desktop import (
    InspectionResult,
    RectangleModel,
    SlackDesktopAdapter,
    SlackElementNode,
    SlackNotFoundError,
)

__all__ = [
    "SlackDesktopAdapter",
    "SlackElementNode",
    "InspectionResult",
    "RectangleModel",
    "SlackNotFoundError",
]
