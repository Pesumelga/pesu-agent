"""PESU Direct Slack Profile 1-Time Interactive Login Entrypoint (Phase 9.9.6).

Provides an explicit, interactive GUI entrypoint for human users to log in to Slack.
Saves session state into the dedicated Direct Profile directory (%LOCALAPPDATA%\\PESU\\slack-runtime-direct).

Contracts:
- Zero automated typing / script injection.
- Zero cookie extraction / copying.
- Zero background search execution.
- Completely separated from the offscreen non-activating background runtime.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from pesu_agent.runtime.pesu_webview_host import (
    DIRECT_SLACK_PROFILE_PATH,
    PesuInteractiveLoginHost,
)

logger = logging.getLogger(__name__)


def run_login_setup(
    user_data_folder: str = DIRECT_SLACK_PROFILE_PATH,
    target_url: str = "https://app.slack.com/client",
) -> int:
    """Launches an interactive window for human login and waits until closed."""
    print("=" * 70)
    print("🔑 PESU Slack Direct Profile Setup (Interactive Login)")
    print(f"Profile Directory: {user_data_folder}")
    print(f"Target URL:        {target_url}")
    print("=" * 70)
    print("1. Log in to your Slack workspace in the opened window.")
    print("2. Once logged in and inside the workspace shell, close the window.")
    print("=" * 70)

    Path(user_data_folder).mkdir(parents=True, exist_ok=True)
    host = PesuInteractiveLoginHost(
        user_data_folder=user_data_folder,
        initial_url=target_url,
    )
    try:
        host.start_and_wait()
        print("\n✅ Login setup window closed. Direct profile is ready.")
        return 0
    except Exception as err:
        print(f"\n❌ Error during login setup: {err}", file=sys.stderr)
        return 1
    finally:
        host.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="PESU Slack Direct Profile Interactive Login Setup")
    parser.add_argument(
        "--profile-dir",
        default=DIRECT_SLACK_PROFILE_PATH,
        help="Custom user data directory for Direct Slack profile",
    )
    parser.add_argument(
        "--target-url",
        default="https://app.slack.com/client",
        help="Target Slack URL to navigate to",
    )
    args = parser.parse_args()
    sys.exit(run_login_setup(user_data_folder=args.profile_dir, target_url=args.target_url))


if __name__ == "__main__":
    main()
