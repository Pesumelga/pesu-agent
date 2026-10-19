"""Session-Scoped External Analysis Consent Manager (Phase 10.7).

Enforces explicit, in-memory, session-scoped consent before user queries or
Slack evidence are transmitted to external AI model providers (e.g. OpenRouter).

Invariants:
1. CONSENT_SCOPE = APPLICATION_SESSION:
   Consent exists only in memory for the lifetime of the PESU process.
2. Zero persistence:
   CONSENT_PERSISTED_TO_DISK = False
   Zero disk, registry, or configuration persistence.
3. Default State:
   DEFAULT_CONSENT_STATE = NOT_GRANTED
   New processes start with NOT_GRANTED.
4. Pre-Call Hard Gate:
   FIRST_EXTERNAL_MODEL_CALL_BEFORE_CONSENT = False
   Zero Planner, Zero Analyzer, and Zero OpenRouter calls before consent is granted.
5. Max Pending Queries:
   MAX_PENDING_CONSENT_QUERIES = 1
   Only the active submitted query is held pending consent.
6. Zero Duplicate Submissions:
   Repeated or concurrent grant requests cannot trigger duplicate investigation executions.
"""

from __future__ import annotations

from enum import Enum
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONSENT_SCOPE: str = "APPLICATION_SESSION"
DEFAULT_CONSENT_STATE: str = "NOT_GRANTED"
CONSENT_PERSISTED_TO_DISK: bool = False
MAX_PENDING_CONSENT_QUERIES: int = 1
PRODUCT_UI_CONSENT_BYPASS: bool = False
FIRST_EXTERNAL_MODEL_CALL_BEFORE_CONSENT: bool = False
CURRENT_EXTERNAL_ANALYSIS_GATE_LOCATION: str = "src/pesu_agent/investigation/payload_builder.py:47"


class ConsentScope(str, Enum):
    """Scope of user consent."""
    APPLICATION_SESSION = "APPLICATION_SESSION"


class ConsentState(str, Enum):
    """Session-scoped consent states."""
    NOT_GRANTED = "NOT_GRANTED"
    GRANTED_FOR_SESSION = "GRANTED_FOR_SESSION"


class SessionConsentManager:
    """Thread-safe in-memory session-scoped consent controller."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: ConsentState = ConsentState.NOT_GRANTED
        self._pending_query: Optional[dict[str, Any]] = None
        self.prompt_count: int = 0
        self.grant_count: int = 0
        self.cancel_count: int = 0

    @property
    def state(self) -> ConsentState:
        with self._lock:
            return self._state

    def is_granted(self) -> bool:
        """Returns True if consent has been granted for the current application session."""
        with self._lock:
            return self._state == ConsentState.GRANTED_FOR_SESSION

    def set_pending_query(self, query: str, request_id: Optional[str] = None) -> bool:
        """Stores at most one pending query while awaiting user consent."""
        with self._lock:
            self._pending_query = {
                "query": query,
                "request_id": request_id,
            }
            self.prompt_count += 1
            logger.info(f"Recorded pending query for consent: req_id='{request_id}' (total prompts={self.prompt_count})")
            return True

    def get_pending_query(self) -> Optional[dict[str, Any]]:
        """Retrieves current pending query without consuming it."""
        with self._lock:
            return dict(self._pending_query) if self._pending_query else None

    def grant(self) -> Optional[dict[str, Any]]:
        """Grants consent for the remainder of this session and consumes the pending query exactly once."""
        with self._lock:
            self._state = ConsentState.GRANTED_FOR_SESSION
            self.grant_count += 1
            pending = self._pending_query
            self._pending_query = None
            logger.info(f"Session consent GRANTED (grant_count={self.grant_count}, had_pending={pending is not None})")
            return pending

    def cancel(self) -> None:
        """Cancels consent request and drops the pending query."""
        with self._lock:
            self.cancel_count += 1
            self._pending_query = None
            logger.info(f"Session consent CANCELLED (cancel_count={self.cancel_count})")
