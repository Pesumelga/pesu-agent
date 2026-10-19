"""User Product Preferences Store (Phase 11.4).

Manages lightweight, non-secret user preferences (e.g. onboarding completion).
Stored locally in %LOCALAPPDATA%\\PESU\\preferences.json.

Safety Invariants:
1. No Secrets or Auth Data:
   - Never stores API keys, tokens, cookies, queries, answers, or session consent.
2. Fail-Safe:
   - Malformed, corrupt, or missing file safely defaults to onboarding_completed=False without crashing.
3. Atomic Writes:
   - Writes to a temporary file first, then atomically replaces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)

PREFERENCES_DIR: str = os.path.expandvars(r"%LOCALAPPDATA%\PESU")
DEFAULT_PREFERENCES_FILE: str = os.path.join(PREFERENCES_DIR, "preferences.json")

# Safety policy invariants
PREFERENCE_STORES_CREDENTIALS: bool = False
PREFERENCE_STORES_CONSENT: bool = False
PREFERENCE_ATOMIC_WRITE_ENABLED: bool = True

ALLOWED_PREFERENCE_KEYS: frozenset[str] = frozenset({
    "onboarding_completed",
})


@dataclass
class ProductPreferences:
    """Typed product preferences data model."""
    onboarding_completed: bool = False


class ProductPreferencesStore:
    """Manages atomic read/write of non-secret product preferences."""

    def __init__(self, preferences_file: Optional[str] = None) -> None:
        self.preferences_file: str = preferences_file or DEFAULT_PREFERENCES_FILE

    def load(self) -> ProductPreferences:
        """Loads preferences from disk with fail-safe error recovery."""
        if not os.path.isfile(self.preferences_file):
            logger.debug(f"Preferences file does not exist at '{self.preferences_file}'. Using defaults.")
            return ProductPreferences()

        try:
            with open(self.preferences_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning(f"Preferences data at '{self.preferences_file}' is not a JSON object. Using defaults.")
                return ProductPreferences()

            return ProductPreferences(
                onboarding_completed=bool(data.get("onboarding_completed", False))
            )
        except Exception as ex:
            logger.warning(f"Failed to read/parse preferences from '{self.preferences_file}': {ex}. Using safe defaults.")
            return ProductPreferences()

    def save(self, prefs: ProductPreferences) -> bool:
        """Atomically saves preferences to disk."""
        target_dir = os.path.dirname(self.preferences_file)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        payload = {
            "onboarding_completed": bool(prefs.onboarding_completed),
        }

        # Write to temporary file in same directory for atomic replace
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target_dir or None,
                delete=False,
                prefix="pref_",
                suffix=".tmp",
            ) as tf:
                temp_file = tf.name
                json.dump(payload, tf, indent=2, ensure_ascii=False)

            os.replace(temp_file, self.preferences_file)
            logger.info(f"Preferences successfully saved to '{self.preferences_file}'.")
            return True
        except Exception as ex:
            logger.error(f"Failed to atomically write preferences to '{self.preferences_file}': {ex}")
            if temp_file and os.path.isfile(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False

    def is_onboarding_completed(self) -> bool:
        """Returns True if onboarding has already been completed."""
        return self.load().onboarding_completed

    def set_onboarding_completed(self, completed: bool = True) -> bool:
        """Sets and persists the onboarding completion flag."""
        prefs = self.load()
        prefs.onboarding_completed = completed
        return self.save(prefs)


default_preferences_store = ProductPreferencesStore()
