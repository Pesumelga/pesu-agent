"""Investigation History Storage Module (Phase 12.1).

Manages local SQLite-backed persistence of completed investigation snapshots.
Stored locally in %LOCALAPPDATA%\\PESU\\history\\investigations.db.

Architectural Contracts & Invariants:
1. Not Conversation Memory:
   - HISTORY_LLM_CONTEXT_INJECTION = False
   - Past records are never injected into Planner/Analyzer prompts or Slack queries.
2. Threading Contract:
   - SHARED_SQLITE_CONNECTION_ACROSS_THREADS = False
   - Every operation creates a fresh connection, executes within a short transaction,
     and closes cleanly. No connection sharing between STA UI thread and background workers.
3. Duplicate Completion Semantics:
   - INSERT ... ON CONFLICT(id) DO NOTHING
   - Completed snapshots are immutable. Duplicates cause zero count increment and zero mutation.
   - EXISTING_HISTORY_RECORD_MUTATED_ON_DUPLICATE = False
4. Bounded Snapshot Isolation:
   - Only bounded presentation snapshots are stored.
   - HISTORY_STORES_RAW_SLACK_DOM = False
   - HISTORY_STORES_API_KEY = False
   - HISTORY_STORES_INTERNAL_AUDIT = False
   - HISTORY_DB_ENCRYPTED = False (standard SQLite file in local OS user profile)
5. Retention & Deletion:
   - AUTOMATIC_RETENTION_DELETION = "none" (no automatic silent pruning)
   - Individual hard delete: DELETE FROM investigations WHERE id = ?
   - Clear all: DELETE FROM investigations (preserves credentials, preferences, profiles)
6. Resilience:
   - HISTORY_WRITE_FAILURE_BREAKS_INVESTIGATION = False
   - Corrupted or locked database safely reports as unavailable; never crashes Product Shell.
   - CORRUPT_DB_AUTO_DELETED_OR_REPLACED = False (never silently delete/rename user's DB)
7. Server-Enforced Bounded Pagination:
   - HISTORY_PAGE_SIZE = 20
   - HISTORY_MAX_PAGE_SIZE = 20
   - HISTORY_PAGE_SIZE_SERVER_BOUNDED = True
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Architectural Constants
SHARED_SQLITE_CONNECTION_ACROSS_THREADS: bool = False
HISTORY_LLM_CONTEXT_INJECTION: bool = False
HISTORY_WRITE_FAILURE_BREAKS_INVESTIGATION: bool = False
AUTOMATIC_RETENTION_DELETION: str = "none"
HISTORY_STORES_RAW_SLACK_DOM: bool = False
HISTORY_STORES_API_KEY: bool = False
HISTORY_STORES_INTERNAL_AUDIT: bool = False
HISTORY_DB_ENCRYPTED: bool = False
CORRUPT_DB_AUTO_DELETED_OR_REPLACED: bool = False

HISTORY_PAGE_SIZE: int = 20
HISTORY_MAX_PAGE_SIZE: int = 20
HISTORY_PAGE_SIZE_SERVER_BOUNDED: bool = True
SCHEMA_VERSION: int = 1

ALLOWED_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "id",
    "source",
    "author",
    "timestamp",
    "channel",
    "snippet",
    "permalink",
    "has_valid_permalink",
    "thread_replies",
    "thread_context_status",
})


def get_default_history_db_path() -> str:
    """Returns the canonical OS user-local path for investigations.db."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.join(tempfile.gettempdir(), "PESU")
    return os.path.join(local_app_data, "PESU", "history", "investigations.db")


class InvestigationHistoryStore:
    """Manages SQLite storage for completed investigation snapshots."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path: str = db_path or get_default_history_db_path()
        self._init_attempted: bool = False

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Creates a short-lived SQLite connection per operation.

        Guarantees:
        - SHARED_SQLITE_CONNECTION_ACROSS_THREADS = False
        - Zero connection sharing across threads
        - Clean closing on exit
        - Zero silent deletion or replacement of corrupt files
        """
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def init_db(self) -> bool:
        """Initializes the investigations schema if it does not exist."""
        try:
            with self._get_connection() as conn:
                with conn:
                    conn.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS investigations (
                            id TEXT PRIMARY KEY,
                            created_at TEXT NOT NULL,
                            completed_at TEXT,
                            question TEXT NOT NULL,
                            answer TEXT NOT NULL,
                            status TEXT NOT NULL,
                            evidence_json TEXT NOT NULL,
                            schema_version INTEGER DEFAULT {SCHEMA_VERSION}
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_investigations_created_at
                        ON investigations(created_at DESC);
                        """
                    )
            self._init_attempted = True
            logger.info(f"Investigation history database initialized at '{self.db_path}'.")
            return True
        except Exception as ex:
            logger.warning(f"Failed to initialize investigation history database at '{self.db_path}': {ex}")
            return False

    def is_available(self) -> bool:
        """Checks if the history database is accessible and functional."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM investigations LIMIT 1;")
                return True
        except Exception:
            # Try initializing in case the table simply hasn't been created yet
            return self.init_db()

    def sanitize_evidence_items(self, evidence_items: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Filters evidence items to strict bounded presentation fields only."""
        if not evidence_items or not isinstance(evidence_items, list):
            return []

        sanitized: list[dict[str, Any]] = []
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            filtered = {k: v for k, v in item.items() if k in ALLOWED_EVIDENCE_KEYS}
            # Ensure required string representations
            filtered["id"] = str(filtered.get("id", f"E{len(sanitized) + 1}"))
            filtered["source"] = str(filtered.get("source", "Slack"))
            filtered["snippet"] = str(filtered.get("snippet", ""))[:400]
            sanitized.append(filtered)
        return sanitized

    def save_investigation(
        self,
        investigation_id: str,
        question: str,
        answer: str,
        status: str = "completed",
        evidence: Optional[list[dict[str, Any]]] = None,
        created_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> bool:
        """Persists a completed investigation snapshot.

        Semantics:
        - INSERT ... ON CONFLICT(id) DO NOTHING
        - Completed snapshots are immutable; duplicate completion callbacks do NOT overwrite
          or increment count.
        - Failures return False without raising exceptions
          (HISTORY_WRITE_FAILURE_BREAKS_INVESTIGATION = False).
        """
        if not investigation_id or not question:
            logger.warning("save_investigation called with empty id or question.")
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        final_created = created_at or now_iso
        final_completed = completed_at or now_iso

        sanitized_evidence = self.sanitize_evidence_items(evidence)
        evidence_json = json.dumps(sanitized_evidence, ensure_ascii=False)

        try:
            if not self._init_attempted:
                self.init_db()

            with self._get_connection() as conn:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO investigations (
                            id, created_at, completed_at, question, answer, status, evidence_json, schema_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO NOTHING;
                        """,
                        (
                            investigation_id,
                            final_created,
                            final_completed,
                            question,
                            answer,
                            status,
                            evidence_json,
                            SCHEMA_VERSION,
                        ),
                    )
                    was_inserted = cursor.rowcount > 0
                    if was_inserted:
                        logger.info(f"Saved investigation snapshot '{investigation_id}'.")
                    else:
                        logger.debug(f"Investigation '{investigation_id}' already exists. Preserved existing record.")
                    return True
        except Exception as ex:
            logger.warning(f"Error saving investigation history (non-fatal): {ex}")
            return False

    def list_investigations(
        self,
        limit: int = HISTORY_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Lists investigations newest-first with server-enforced bounded pagination.

        Returns lightweight previews (without full answer/evidence_json).
        """
        # Server-side pagination bounds enforcement
        bounded_limit = max(1, min(limit, HISTORY_MAX_PAGE_SIZE))
        bounded_offset = max(0, offset)

        try:
            if not self._init_attempted:
                self.init_db()

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as cnt FROM investigations;")
                row = cursor.fetchone()
                total_count = row["cnt"] if row else 0

                cursor.execute(
                    """
                    SELECT id, created_at, completed_at, question, status, schema_version
                    FROM investigations
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (bounded_limit, bounded_offset),
                )
                rows = cursor.fetchall()
                items = [
                    {
                        "id": r["id"],
                        "created_at": r["created_at"],
                        "completed_at": r["completed_at"],
                        "question": r["question"],
                        "status": r["status"],
                        "schema_version": r["schema_version"],
                    }
                    for r in rows
                ]

                return {
                    "items": items,
                    "total_count": total_count,
                    "limit": bounded_limit,
                    "offset": bounded_offset,
                    "available": True,
                }
        except Exception as ex:
            logger.warning(f"Error listing investigation history: {ex}")
            return {
                "items": [],
                "total_count": 0,
                "limit": bounded_limit,
                "offset": bounded_offset,
                "available": False,
                "error": str(ex),
            }

    def get_investigation(self, investigation_id: str) -> Optional[dict[str, Any]]:
        """Retrieves a single full investigation snapshot including parsed evidence."""
        if not investigation_id:
            return None

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, created_at, completed_at, question, answer, status, evidence_json, schema_version
                    FROM investigations
                    WHERE id = ?;
                    """,
                    (investigation_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                try:
                    evidence_list = json.loads(row["evidence_json"])
                except Exception:
                    evidence_list = []

                return {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "completed_at": row["completed_at"],
                    "question": row["question"],
                    "answer": row["answer"],
                    "status": row["status"],
                    "evidence": evidence_list,
                    "schema_version": row["schema_version"],
                }
        except Exception as ex:
            logger.warning(f"Error getting investigation history record '{investigation_id}': {ex}")
            return None

    def delete_investigation(self, investigation_id: str) -> bool:
        """Hard-deletes an individual investigation record.

        DELETE FROM investigations WHERE id = ?
        """
        if not investigation_id:
            return False

        try:
            with self._get_connection() as conn:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM investigations WHERE id = ?;",
                        (investigation_id,),
                    )
                    return cursor.rowcount > 0
        except Exception as ex:
            logger.warning(f"Error deleting investigation '{investigation_id}': {ex}")
            return False

    def clear_all(self) -> tuple[bool, int]:
        """Hard-deletes all investigation rows.

        DELETE FROM investigations
        Preserves database schema, user credentials, preferences, and Slack profile.
        Returns (success, deleted_count).
        """
        try:
            with self._get_connection() as conn:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) as cnt FROM investigations;")
                    row = cursor.fetchone()
                    total = row["cnt"] if row else 0

                    cursor.execute("DELETE FROM investigations;")
                    logger.info(f"Cleared all investigation history ({total} records removed).")
                    return True, total
        except Exception as ex:
            logger.warning(f"Error clearing investigation history: {ex}")
            return False, 0

    def count(self) -> int:
        """Returns the total number of saved investigations, or 0 if unavailable."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as cnt FROM investigations;")
                row = cursor.fetchone()
                return int(row["cnt"]) if row else 0
        except Exception:
            return 0
