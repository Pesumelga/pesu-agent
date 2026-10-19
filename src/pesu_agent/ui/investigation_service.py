"""Background Investigation Worker & Service Controller (Phase 10.3).

Executes InvestigationOrchestrator pipelines asynchronously on a dedicated single-threaded
worker pool, safely marshaling progress events and final results back to the WinForms UI STA thread.

Guarantees:
1. Concurrency Model:
   - ThreadPoolExecutor(max_workers=1)
   - MAX_CONCURRENT_INVESTIGATIONS = 1
   - INVESTIGATION_RUNS_ON_UI_THREAD = False
   - Overlapping submissions are immediately rejected with 'investigation.busy' (zero silent queueing).
2. Lazy Session-Scoped Runtime:
   - Zero Slack Host / Backend creation on UI startup.
   - Initialized on first query submission and persistent across the entire session.
   - SAME_DIRECT_HOST_REUSED_ACROSS_JOBS = True.
3. Thread-Safe UI Marshaling:
   - ALL_PRODUCT_UI_MUTATION_ON_UI_STA = True.
   - Progress and completion events are dispatched via Form.BeginInvoke -> Bridge.send_message.
4. Clean Teardown:
   - Graceful bounded executor shutdown; zero unsafe Thread.Abort / os._exit kills.
   - COOPERATIVE_CANCELLATION_SUPPORTED = False.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Optional
import uuid

from pesu_agent.investigation.models import UserQuestion
from pesu_agent.investigation.orchestrator import (
    InvestigationAuditRecord,
    InvestigationOrchestrator,
    InvestigationResult,
    InvestigationState,
    InvestigationStopReason,
    OrchestratorConfig,
)
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.openrouter import OpenRouterProvider
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
)
from pesu_agent.ui.consent_manager import SessionConsentManager
from pesu_agent.ui.messages import UiMessage
from pesu_agent.ui.result_presenter import (
    format_user_friendly_error,
    present_investigation_result,
    validate_citation_permalink,
    present_grounded_investigation_result,
)
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.investigation.grounded_runtime import create_grounded_coordinator
from pesu_agent.llm.budgeted import BudgetedProvider
from pesu_agent.security.credential_store import (
    CredentialStore,
    default_credential_store,
)
from pesu_agent.ui.preferences import (
    ProductPreferencesStore,
    default_preferences_store,
)
from pesu_agent.platform.windows_startup import WindowsStartupManager, AutoStartStatus
from pesu_agent.history.store import (
    InvestigationHistoryStore,
    HISTORY_LLM_CONTEXT_INJECTION,
    HISTORY_WRITE_FAILURE_BREAKS_INVESTIGATION,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_INVESTIGATIONS: int = 1
INVESTIGATION_RUNS_ON_UI_THREAD: bool = False
COOPERATIVE_CANCELLATION_SUPPORTED: bool = False
SAME_DIRECT_HOST_REUSED_ACROSS_JOBS: bool = True
ALL_PRODUCT_UI_MUTATION_ON_UI_STA: bool = True
COMPLETION_POSITIVE_GATE_IMPLEMENTED: bool = True

SUCCESS_TERMINAL_STATES: frozenset[InvestigationState] = frozenset({
    InvestigationState.COMPLETED,
})

SUCCESS_STOP_REASONS: frozenset[InvestigationStopReason] = frozenset({
    InvestigationStopReason.EVIDENCE_SUFFICIENT,
    InvestigationStopReason.NO_NEW_EVIDENCE,
    InvestigationStopReason.MAX_SEARCH_ROUNDS_REACHED,
    InvestigationStopReason.QUERY_BUDGET_REACHED,
    InvestigationStopReason.LLM_BUDGET_REACHED,
    InvestigationStopReason.ANALYSIS_INCONCLUSIVE,
})

NO_EVIDENCE_ANSWER_TEXT: str = (
    "입력하신 질문과 관련된 Slack 대화나 근거를 찾지 못했습니다.\n"
    "검색 조건을 바꾸거나 질문을 조금 더 구체적으로 작성해 주세요."
)


class AppStatusCode:
    READY = "ready"
    INVESTIGATING = "investigating"
    SLACK_LOGIN_REQUIRED = "slack_login_required"
    SLACK_LOGIN_IN_PROGRESS = "slack_login_in_progress"
    PROVIDER_SETUP_REQUIRED = "provider_setup_required"
    ERROR = "error"


class InvestigationService:
    """Session-scoped Investigation worker service and UI marshaler."""

    def __init__(
        self,
        post_to_ui: Optional[Callable[[UiMessage], None]] = None,
        orchestrator_factory: Optional[Callable[[], InvestigationOrchestrator]] = None,
        allow_real_slack_external_analysis: bool = False,
        credential_store: Optional[CredentialStore] = None,
        preferences_store: Optional[ProductPreferencesStore] = None,
        startup_manager: Optional[WindowsStartupManager] = None,
        history_store: Optional[InvestigationHistoryStore] = None,
    ) -> None:
        self._post_to_ui = post_to_ui
        self._orchestrator_factory = orchestrator_factory
        self.allow_real_slack_external_analysis = allow_real_slack_external_analysis
        self.credential_store: CredentialStore = credential_store or default_credential_store
        self.preferences_store: ProductPreferencesStore = preferences_store or default_preferences_store
        self.startup_manager: WindowsStartupManager = startup_manager or WindowsStartupManager()
        self.history_store: Optional[InvestigationHistoryStore] = (
            history_store if history_store is not None else InvestigationHistoryStore()
        )
        self._pending_provider_query: Optional[tuple[str, Optional[str]]] = None
        self.current_status: str = AppStatusCode.READY
        self.on_status_changed: Optional[Callable[[str, Optional[str]], None]] = None

        self._lock = threading.Lock()
        self._is_busy: bool = False
        self._is_shutting_down: bool = False
        self._current_request_id: Optional[str] = None

        # Fixed worker pool: exactly 1 background worker thread
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_INVESTIGATIONS,
            thread_name_prefix="pesu-inv-worker",
        )

        # Lazy Session-Scoped Runtime instances
        self._backend: Any = None
        self._orchestrator: Optional[InvestigationOrchestrator] = None
        self.backend_creation_count: int = 0
        self.investigation_execution_count: int = 0
        self._current_citations: dict[str, str] = {}
        self.citation_launcher: Optional[Callable[[str], None]] = None
        self.consent_manager: SessionConsentManager = SessionConsentManager()
        self._is_login_in_progress: bool = False
        if self._orchestrator_factory is not None:
            # Unit test double mode: pre-grant consent for mock test fixtures
            self.consent_manager.grant()

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._is_busy

    @property
    def is_login_in_progress(self) -> bool:
        with self._lock:
            return self._is_login_in_progress

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._is_shutting_down

    def _set_status(self, status: str, message: Optional[str] = None) -> None:
        """Sets the canonical status and notifies status change listener (e.g. Tray)."""
        self.current_status = status
        if self.on_status_changed is not None:
            try:
                self.on_status_changed(status, message)
            except Exception as ex:
                logger.warning(f"Error in on_status_changed callback: {ex}")

    def is_onboarding_completed(self) -> bool:
        """Checks if onboarding has been completed per user preference."""
        return self.preferences_store.is_onboarding_completed()

    def complete_onboarding(self) -> bool:
        """Marks onboarding as completed and persists preference atomically."""
        return self.preferences_store.set_onboarding_completed(True)

    def check_and_show_onboarding(self, force: bool = False) -> None:
        """Pushes onboarding modal display to UI if not completed or if forced."""
        if force or not self.is_onboarding_completed():
            self._dispatch_ui(
                UiMessage(
                    type="onboarding.show",
                    payload={"force": force},
                )
            )

    def _get_or_create_orchestrator(self) -> InvestigationOrchestrator:
        """Lazily creates and caches the InvestigationOrchestrator and Direct Host backend."""
        if self._orchestrator is not None:
            return self._orchestrator

        if self._orchestrator_factory:
            self._orchestrator = self._orchestrator_factory()
            self.backend_creation_count += 1
            return self._orchestrator

        logger.info("Initializing lazy production InvestigationOrchestrator (Direct WebView2)...")
        search_config = SlackSearchConfig(backend_kind=SlackBackendKind.WEBVIEW_DIRECT)
        backend = self._backend
        if backend is None:
            backend = create_slack_search_backend(search_config)
            if hasattr(backend, "start_runtime"):
                logger.info("Starting Direct Slack Host runtime...")
                backend.start_runtime(timeout=25.0)
            self._backend = backend
            self.backend_creation_count += 1

        provider = BudgetedProvider(OpenRouterProvider(config=LLMConfig(max_retries=0)),
                                    should_stop=lambda: self.is_shutting_down)
        orch_config = OrchestratorConfig(
            max_search_rounds=1,
            max_queries_per_round=3,
            max_total_queries=3,
            max_evidence_items=5,
            allow_real_slack_external_analysis=self.allow_real_slack_external_analysis,
            max_external_llm_calls=provider.max_calls,
            base_output_dir=Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "PESU" / "investigations",
        )
        self._orchestrator = InvestigationOrchestrator(
            provider=provider,
            search_backend=backend,
            config=orch_config,
            trace_coordinator=create_grounded_coordinator(provider),
        )
        logger.info("Lazy InvestigationOrchestrator initialized and cached for session.")
        return self._orchestrator

    def submit_investigation(
        self,
        query: str,
        request_id: Optional[str] = None,
    ) -> bool:
        """Submits a user investigation, requiring session consent if not yet granted."""
        with self._lock:
            if self._is_shutting_down:
                logger.warning(f"Rejected submission: Service is shutting down (req_id={request_id})")
                self._dispatch_ui(
                    UiMessage(
                        type="investigation.failed",
                        request_id=request_id,
                        payload={"error": "애플리케이션이 종료 중입니다."},
                    )
                )
                return False

            if self._is_login_in_progress:
                logger.warning(f"Rejected submission: Slack login setup in progress (req_id={request_id})")
                self._dispatch_ui(
                    UiMessage(
                        type="investigation.busy",
                        request_id=request_id,
                        payload={"message": "현재 Slack 로그인이 진행 중입니다. 로그인을 마친 후 질문해 주세요."},
                    )
                )
                return False

        # 1. Gate: Verify OpenRouter credential availability
        if self._orchestrator_factory is None:
            key, source = self.credential_store.get_openrouter_api_key()
            if not key:
                logger.info(f"OpenRouter credential missing for query (req_id='{request_id}'). Prompting provider setup.")
                with self._lock:
                    self._pending_provider_query = (query, request_id)
                self._set_status(AppStatusCode.PROVIDER_SETUP_REQUIRED, "OpenRouter API 키 설정 필요")
                self._dispatch_ui(
                    UiMessage(
                        type="provider.openrouter.setup.required",
                        request_id=request_id,
                        payload={
                            "title": "OpenRouter API 키 설정 필요",
                            "message": "PESU의 AI 분석 기능을 사용하려면 OpenRouter API 키를 설정해 주세요.",
                        },
                    )
                )
                return False

        # 2. Gate: Verify session-scoped external analysis consent
        if not self.allow_real_slack_external_analysis and not self.consent_manager.is_granted():
            logger.info(f"External analysis consent required for query (req_id='{request_id}'). Prompting user.")
            self.consent_manager.set_pending_query(query, request_id)
            self._dispatch_ui(
                UiMessage(
                    type="consent.external_analysis.required",
                    request_id=request_id,
                    payload={
                        "query": query,
                        "title": "외부 AI 분석 허용",
                        "message": (
                            "질문에 답하기 위해 검색된 Slack 메시지 일부와 입력한 질문이 "
                            "설정된 AI 모델 제공자에게 전송될 수 있습니다.\n\n"
                            "이 허용은 PESU를 종료할 때까지 유지됩니다.\n"
                            "Slack 메시지를 작성하거나 전송하는 권한은 포함되지 않습니다."
                        ),
                    },
                )
            )
            return False

        return self._execute_submission(query, request_id)

    def _execute_submission(
        self,
        query: str,
        request_id: Optional[str] = None,
    ) -> bool:
        """Dispatches an accepted investigation query to the background worker pool."""
        with self._lock:
            if self._is_shutting_down:
                logger.warning(f"Rejected submission: Service is shutting down (req_id={request_id})")
                self._dispatch_ui(
                    UiMessage(
                        type="investigation.failed",
                        request_id=request_id,
                        payload={"error": "애플리케이션이 종료 중입니다."},
                    )
                )
                return False

            if self._is_busy:
                logger.warning(f"Rejected submission: Worker is busy with req_id={self._current_request_id}")
                self._dispatch_ui(
                    UiMessage(
                        type="investigation.busy",
                        request_id=request_id,
                        payload={"message": "이미 다른 조사가 진행 중입니다. 잠시 후 다시 시도해 주세요."},
                    )
                )
                return False

            self._is_busy = True
            self._current_request_id = request_id
            self._current_citations.clear()

        # 1. Acknowledge acceptance
        self._dispatch_ui(
            UiMessage(
                type="investigation.accepted",
                request_id=request_id,
                payload={"query": query},
            )
        )

        # 2. Dispatch to background worker
        self._executor.submit(self._run_investigation_worker, query, request_id)
        return True

    def grant_consent(self, request_id: Optional[str] = None) -> bool:
        """Grants session-scoped external analysis consent and executes the pending query if present."""
        pending = self.consent_manager.grant()
        self.allow_real_slack_external_analysis = True
        if self._orchestrator and hasattr(self._orchestrator, "config"):
            self._orchestrator.config.allow_real_slack_external_analysis = True

        self._dispatch_ui(
            UiMessage(
                type="consent.external_analysis.state",
                request_id=request_id,
                payload={"state": "GRANTED_FOR_SESSION"},
            )
        )

        if pending:
            logger.info(f"Executing pending query after consent grant: req_id='{pending.get('request_id')}'")
            return self._execute_submission(pending["query"], pending.get("request_id"))
        return True

    def cancel_consent(self, request_id: Optional[str] = None) -> None:
        """Cancels consent prompt and discards the pending query."""
        self.consent_manager.cancel()
        self._dispatch_ui(
            UiMessage(
                type="consent.external_analysis.state",
                request_id=request_id,
                payload={"state": "NOT_GRANTED"},
            )
        )
        self._dispatch_ui(
            UiMessage(
                type="app.status",
                request_id=request_id,
                payload={"status": "ready", "message": "준비됨 (외부 분석 허용이 취소되었습니다)"},
            )
        )

    def _run_investigation_worker(self, query: str, request_id: Optional[str]) -> None:
        """Executes the investigation pipeline on the background thread pool."""
        logger.info(f"Worker started for request '{request_id}' on thread '{threading.current_thread().name}'")

        def _on_progress(stage: str, message: str):
            self._set_status(AppStatusCode.INVESTIGATING, message)
            self._dispatch_ui(
                UiMessage(
                    type="investigation.progress",
                    request_id=request_id,
                    payload={"stage": stage, "message": message},
                )
            )

        try:
            self._set_status(AppStatusCode.INVESTIGATING, "조사 준비 중...")
            _on_progress("starting", "조사 준비 중...")
            orchestrator = self._get_or_create_orchestrator()

            # Execute pipeline
            user_question = UserQuestion(question=query)
            result = asyncio.run(
                orchestrator.run_investigation(
                    question=user_question,
                    on_progress=_on_progress,
                )
            )
            self.investigation_execution_count += 1

            # 1. Positive Success Gate:
            # Explicitly verify legitimate successful terminal outcome.
            is_successful_terminal = (
                result.state in SUCCESS_TERMINAL_STATES
                and result.stop_reason in SUCCESS_STOP_REASONS
                and result.state != InvestigationState.ERROR
                and result.stop_reason != InvestigationStopReason.ERROR
            )

            if not is_successful_terminal:
                logger.warning(
                    f"Investigation {result.investigation_id} finished in non-success terminal state: "
                    f"state={result.state}, stop_reason={result.stop_reason}. Dispatching failure."
                )
                auth_required = result.failure_code == "AUTH_REQUIRED"
                self._set_status(AppStatusCode.SLACK_LOGIN_REQUIRED if auth_required else AppStatusCode.READY,
                                 "Slack 로그인이 필요합니다." if auth_required else "준비됨")
                if auth_required:
                    self._dispatch_ui(UiMessage(type="app.status", request_id=request_id,
                        payload={"status": "slack_login_required", "message": "Slack 로그인이 필요합니다."}))
                self._dispatch_ui(
                    UiMessage(
                        type="investigation.failed",
                        request_id=request_id,
                        payload={
                            "error": format_user_friendly_error(result.failure_code or result.error_message or "INVESTIGATION_FAILED"),
                            "auth_required": auth_required,
                        },
                    )
                )
                return

            # 2. Present result and validate answer
            if result.production_trace is not None:
                trace = result.production_trace
                answer = GroundedWorkItemAnswerBuilder.build_answer(
                    question=query, graph=trace.graph, work_items=trace.work_items,
                    traceability_chains=trace.traceability_chains, stop_reason=trace.stop_reason,
                )
                presented_payload = present_grounded_investigation_result(result.investigation_id, answer)
            else:
                presented_payload = present_investigation_result(result)
            answer_text = (presented_payload.get("answer") or "").strip()
            evidence_count = presented_payload.get("evidence_count", 0)

            if not answer_text:
                if evidence_count > 0:
                    # Empty answer despite having evidence is a failure
                    logger.warning(
                        f"Investigation {result.investigation_id} has {evidence_count} evidence items "
                        "but analysis/presented answer is empty. Dispatching failure."
                    )
                    self._set_status(AppStatusCode.READY, "준비됨")
                    self._dispatch_ui(
                        UiMessage(
                            type="investigation.failed",
                            request_id=request_id,
                            payload={
                                "error": "수집된 증거를 바탕으로 답변을 구성하지 못했습니다.",
                                "auth_required": False,
                            },
                        )
                    )
                    return
                else:
                    # Explicit legitimate zero-evidence result
                    presented_payload["answer"] = NO_EVIDENCE_ANSWER_TEXT
                    presented_payload["has_evidence"] = False

            with self._lock:
                self._current_citations = {}
                for item in presented_payload.get("evidence", []):
                    if item.get("permalink"):
                        self._current_citations[item["id"]] = item["permalink"]
                    for r in item.get("thread_replies", []):
                        if r.get("permalink"):
                            self._current_citations[r["reply_id"]] = r["permalink"]

            # Safe persistence into investigation history (failure must never break investigation)
            if self.history_store is not None:
                try:
                    inv_id = str(result.investigation_id or request_id or f"inv-{int(time.time()*1000)}")
                    created_ts = getattr(result.audit, "created_at", None) if hasattr(result, "audit") and result.audit else None
                    completed_ts = getattr(result.audit, "completed_at", None) if hasattr(result, "audit") and result.audit else None
                    self.history_store.save_investigation(
                        investigation_id=inv_id,
                        question=query,
                        answer=presented_payload.get("answer", ""),
                        status="completed",
                        evidence=presented_payload.get("evidence", []),
                        created_at=created_ts,
                        completed_at=completed_ts,
                    )
                except Exception as save_err:
                    logger.warning(f"Failed to persist investigation history (non-fatal): {save_err}")

            self._set_status(AppStatusCode.READY, "준비됨")
            self._dispatch_ui(
                UiMessage(
                    type="investigation.completed",
                    request_id=request_id,
                    payload=presented_payload,
                )
            )
            self.push_history_status(request_id=request_id)
            logger.info(f"Worker completed successfully for request '{request_id}'.")

        except Exception as ex:
            logger.error(f"Investigation execution failed for request '{request_id}': {ex}", exc_info=True)
            friendly_err = format_user_friendly_error(ex)
            raw_ex = str(ex).lower()
            is_auth_required = "auth_required" in raw_ex or "login" in raw_ex or "auth" in raw_ex
            self._dispatch_ui(
                UiMessage(
                    type="investigation.failed",
                    request_id=request_id,
                    payload={
                        "error": friendly_err,
                        "auth_required": is_auth_required,
                    },
                )
            )
            if is_auth_required:
                self._set_status(AppStatusCode.SLACK_LOGIN_REQUIRED, "Slack 로그인이 필요합니다.")
                self._dispatch_ui(
                    UiMessage(
                        type="app.status",
                        request_id=request_id,
                        payload={"status": "slack_login_required", "message": "Slack 로그인이 필요합니다."},
                    )
                )
            else:
                self._set_status(AppStatusCode.ERROR, friendly_err)

        finally:
            with self._lock:
                self._is_busy = False
                self._current_request_id = None

    def start_login_setup(
        self,
        request_id: Optional[str] = None,
        host_factory: Optional[Callable[[], Any]] = None,
    ) -> bool:
        """Launches interactive Slack Login Setup host after cleanly stopping Direct Slack Host."""
        with self._lock:
            if self._is_busy:
                logger.warning(f"Rejected login request: Investigation worker is busy with req_id={self._current_request_id}")
                self._dispatch_ui(
                    UiMessage(
                        type="slack.login.busy",
                        request_id=request_id,
                        payload={"message": "현재 조사가 진행 중입니다. 완료된 후 Slack 로그인을 다시 시도해 주세요."},
                    )
                )
                return False

            if self._is_login_in_progress:
                logger.warning("Rejected login request: Login setup is already in progress")
                self._dispatch_ui(
                    UiMessage(
                        type="slack.login.busy",
                        request_id=request_id,
                        payload={"message": "이미 Slack 로그인 창이 열려 있습니다."},
                    )
                )
                return False

            self._is_login_in_progress = True

        # ONE_OWNER_PER_SLACK_PROFILE: Stop Direct Slack Host before starting Login Setup
        if self._backend is not None:
            logger.info("Stopping background Direct Slack Host before starting Login Setup (ONE_OWNER_PER_SLACK_PROFILE)...")
            try:
                if hasattr(self._backend, "stop_runtime"):
                    self._backend.stop_runtime()
            except Exception as ex:
                logger.warning(f"Error stopping Direct Slack Host: {ex}")
            self._backend = None
            self._orchestrator = None

        self._set_status(AppStatusCode.SLACK_LOGIN_IN_PROGRESS, "Slack 로그인 진행 중...")
        self._dispatch_ui(
            UiMessage(
                type="slack.login.started",
                request_id=request_id,
                payload={"message": "Slack 로그인 창이 열렸습니다. 로그인을 완료하고 창을 닫아주세요."},
            )
        )
        self._dispatch_ui(
            UiMessage(
                type="app.status",
                request_id=request_id,
                payload={"status": "slack_login_in_progress", "message": "Slack 로그인 진행 중..."},
            )
        )

        def _login_worker():
            try:
                logger.info("Starting interactive Login Setup host...")
                if host_factory:
                    login_host = host_factory()
                else:
                    from pesu_agent.runtime.pesu_webview_host import PesuInteractiveLoginHost
                    login_host = PesuInteractiveLoginHost()

                if hasattr(login_host, "start_and_wait"):
                    login_host.start_and_wait()

                logger.info("Interactive Login Setup host window closed. Destroying host...")
                if hasattr(login_host, "destroy"):
                    login_host.destroy()

                with self._lock:
                    self._is_login_in_progress = False

                self._set_status(AppStatusCode.READY, "준비됨 (Slack 로그인 완료)")
                self._dispatch_ui(
                    UiMessage(
                        type="slack.login.completed",
                        request_id=request_id,
                        payload={
                            "success": True,
                            "verification": "LOGIN_COMPLETED_UNVERIFIED",
                            "message": "Slack 로그인 창이 닫혔습니다. 준비 상태로 복귀합니다.",
                        },
                    )
                )
                self._dispatch_ui(
                    UiMessage(
                        type="app.status",
                        request_id=request_id,
                        payload={"status": "ready", "message": "준비됨 (Slack 로그인 완료)"},
                    )
                )

            except Exception as ex:
                logger.error(f"Error during Login Setup: {ex}", exc_info=True)
                with self._lock:
                    self._is_login_in_progress = False
                self._set_status(AppStatusCode.READY, "준비됨 (로그인 실패)")
                self._dispatch_ui(
                    UiMessage(
                        type="slack.login.failed",
                        request_id=request_id,
                        payload={"error": "Slack 로그인 창을 실행하는 중 오류가 발생했습니다."},
                    )
                )
                self._dispatch_ui(
                    UiMessage(
                        type="app.status",
                        request_id=request_id,
                        payload={"status": "ready", "message": "준비됨 (Slack 로그인 실패)"},
                    )
                )

        login_thread = threading.Thread(target=_login_worker, name="pesu-login-worker", daemon=True)
        login_thread.start()
        return True

    def open_citation(
        self,
        evidence_id: str,
        launcher: Optional[Callable[[str], None]] = None,
    ) -> tuple[bool, str]:
        """Validates and opens a citation's canonical Slack permalink in the default browser."""
        with self._lock:
            permalink = self._current_citations.get(evidence_id)

        if not permalink:
            logger.warning(f"Citation open rejected: Unknown or missing citation id '{evidence_id}'")
            return False, "UNKNOWN_CITATION_ID"

        if not validate_citation_permalink(permalink):
            logger.warning(f"Citation open rejected: Invalid canonical Slack permalink '{permalink}'")
            return False, "INVALID_SLACK_PERMALINK"

        # Execute opening
        active_launcher = launcher or self.citation_launcher
        if active_launcher:
            try:
                active_launcher(permalink)
                logger.info(f"Citation '{evidence_id}' opened via custom launcher: {permalink}")
                return True, "OPENED"
            except Exception as e:
                logger.error(f"Failed to open citation with custom launcher: {e}")
                return False, f"LAUNCHER_ERROR: {e}"
        else:
            try:
                import webbrowser
                webbrowser.open(permalink)
                logger.info(f"Citation '{evidence_id}' opened in system default browser: {permalink}")
                return True, "OPENED"
            except Exception as e:
                logger.error(f"Failed to open browser for citation: {e}")
                return False, f"BROWSER_ERROR: {e}"

    def _dispatch_ui(self, msg: UiMessage) -> None:
        """Dispatches typed message to UI bridge (thread-marshaled via BeginInvoke)."""
        if self._post_to_ui:
            try:
                self._post_to_ui(msg)
            except Exception as e:
                logger.error(f"Failed to post message to UI bridge: {e}")

    def shutdown(self, wait: bool = True, timeout: float = 5.0) -> None:
        """Gracefully shuts down the background worker and stops cached Direct Host."""
        logger.info("Shutting down InvestigationService...")
        with self._lock:
            self._is_shutting_down = True

        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except Exception as e:
            logger.warning(f"Exception during executor shutdown: {e}")

        # Stop cached Direct Host backend
        if self._backend is not None and hasattr(self._backend, "stop_runtime"):
            try:
                self._backend.stop_runtime()
                logger.info("Direct Slack Host stopped on InvestigationService shutdown.")
            except Exception as ex:
                logger.warning(f"Error stopping search backend runtime: {ex}")
            self._backend = None

        self._orchestrator = None
        logger.info("InvestigationService shutdown complete.")

    def save_openrouter_key(self, key: str, request_id: Optional[str] = None) -> bool:
        """Saves OpenRouter API key securely via DPAPI and resumes pending query."""
        try:
            self.credential_store.save_openrouter_api_key(key)
            with self._lock:
                self._orchestrator = None
            self._dispatch_ui(
                UiMessage(
                    type="provider.openrouter.key.saved",
                    request_id=request_id,
                    payload={"success": True, "source": "secure_store"},
                )
            )
            self._dispatch_ui(
                UiMessage(
                    type="provider.openrouter.status",
                    payload=self.credential_store.get_status(),
                )
            )
            # Resume pending query (Gate 1 passed -> will now evaluate Gate 2: consent)
            pending = None
            with self._lock:
                if self._pending_provider_query:
                    pending = self._pending_provider_query
                    self._pending_provider_query = None
            if pending:
                pending_query, pending_req = pending
                logger.info(f"Resuming pending query after API key save (req_id='{pending_req}')...")
                self.submit_investigation(pending_query, request_id=pending_req)
            else:
                self._set_status(AppStatusCode.READY, "준비됨")
            return True
        except Exception as ex:
            logger.error(f"Failed to save OpenRouter API key: {ex}")
            self._dispatch_ui(
                UiMessage(
                    type="provider.openrouter.error",
                    request_id=request_id,
                    payload={"error": str(ex)},
                )
            )
            return False

    def cancel_openrouter_key_setup(self, request_id: Optional[str] = None) -> None:
        """Cancels API key setup and discards any pending query."""
        with self._lock:
            self._pending_provider_query = None
        self._set_status(AppStatusCode.READY, "준비됨 (API 키 설정 취소)")
        self._dispatch_ui(
            UiMessage(
                type="app.status",
                request_id=request_id,
                payload={"status": "ready", "message": "준비됨 (API 키 설정 취소)"},
            )
        )

    def remove_openrouter_key(self, request_id: Optional[str] = None) -> bool:
        """Removes the stored key from secure storage (environment keys cannot be removed)."""
        status = self.credential_store.get_status()
        if status.get("source") == "environment":
            self._dispatch_ui(
                UiMessage(
                    type="provider.openrouter.error",
                    request_id=request_id,
                    payload={"error": "환경 변수로 설정된 API 키는 삭제할 수 없습니다."},
                )
            )
            return False

        removed = self.credential_store.remove_openrouter_api_key()
        with self._lock:
            self._orchestrator = None
        self._set_status(AppStatusCode.READY, "준비됨")
        self._dispatch_ui(
            UiMessage(
                type="provider.openrouter.key.removed",
                request_id=request_id,
                payload={"success": removed},
            )
        )
        self._dispatch_ui(
            UiMessage(
                type="provider.openrouter.status",
                payload=self.credential_store.get_status(),
            )
        )
        return removed

    def push_openrouter_status(self) -> None:
        """Pushes current OpenRouter credential status to the UI bridge."""
        self._dispatch_ui(
            UiMessage(
                type="provider.openrouter.status",
                payload=self.credential_store.get_status(),
            )
        )

    def get_autostart_status(self) -> dict[str, Any]:
        """Queries current Windows auto-start registration status."""
        status, detail = self.startup_manager.get_status()
        return {
            "supported": self.startup_manager.is_supported(),
            "status": status.value,
            "message": detail,
        }

    def enable_autostart(self, request_id: Optional[str] = None) -> bool:
        """Enables Windows auto-start registration in HKCU Run."""
        success, message = self.startup_manager.enable()
        status, detail = self.startup_manager.get_status()
        self._dispatch_ui(
            UiMessage(
                type="startup.auto.status",
                request_id=request_id,
                payload={
                    "supported": self.startup_manager.is_supported(),
                    "status": status.value,
                    "message": detail if success else message,
                    "success": success,
                },
            )
        )
        if not success:
            self._dispatch_ui(
                UiMessage(
                    type="startup.auto.error",
                    request_id=request_id,
                    payload={"error": message},
                )
            )
        return success

    def disable_autostart(self, request_id: Optional[str] = None) -> bool:
        """Disables Windows auto-start registration in HKCU Run."""
        success, message = self.startup_manager.disable()
        status, detail = self.startup_manager.get_status()
        self._dispatch_ui(
            UiMessage(
                type="startup.auto.status",
                request_id=request_id,
                payload={
                    "supported": self.startup_manager.is_supported(),
                    "status": status.value,
                    "message": detail if success else message,
                    "success": success,
                },
            )
        )
        if not success:
            self._dispatch_ui(
                UiMessage(
                    type="startup.auto.error",
                    request_id=request_id,
                    payload={"error": message},
                )
            )
        return success

    def push_autostart_status(self, request_id: Optional[str] = None) -> None:
        """Pushes current Windows auto-start registration status to UI."""
        status_info = self.get_autostart_status()
        self._dispatch_ui(
            UiMessage(
                type="startup.auto.status",
                request_id=request_id,
                payload=status_info,
            )
        )

    def list_history(
        self,
        limit: int = 20,
        offset: int = 0,
        request_id: Optional[str] = None,
    ) -> None:
        """Pushes a server-bounded paginated list of history records to UI."""
        if self.history_store is None:
            self._dispatch_ui(
                UiMessage(
                    type="history.error",
                    request_id=request_id,
                    payload={"error": "조사 기록 저장소가 설정되지 않았습니다."},
                )
            )
            return

        result = self.history_store.list_investigations(limit=limit, offset=offset)
        self._dispatch_ui(
            UiMessage(
                type="history.list.result",
                request_id=request_id,
                payload=result,
            )
        )

    def open_history_record(
        self,
        history_id: str,
        request_id: Optional[str] = None,
    ) -> None:
        """Retrieves past investigation record and pushes snapshot to UI.

        Invariants:
        - LIVE_SLACK_ACTIONS = 0
        - LIVE_OPENROUTER_ACTIONS = 0
        - Registers citations in _current_citations for safe permalink opening.
        """
        if self.history_store is None:
            self._dispatch_ui(
                UiMessage(
                    type="history.error",
                    request_id=request_id,
                    payload={"error": "조사 기록 저장소가 설정되지 않았습니다."},
                )
            )
            return

        record = self.history_store.get_investigation(history_id)
        if not record:
            self._dispatch_ui(
                UiMessage(
                    type="history.error",
                    request_id=request_id,
                    payload={"error": f"조사 기록을 찾을 수 없습니다: {history_id}"},
                )
            )
            return

        with self._lock:
            self._current_citations = {}
            for item in record.get("evidence", []):
                if item.get("permalink"):
                    self._current_citations[item["id"]] = item["permalink"]
                for r in item.get("thread_replies", []):
                    if r.get("permalink"):
                        self._current_citations[r["reply_id"]] = r["permalink"]

        self._dispatch_ui(
            UiMessage(
                type="history.record",
                request_id=request_id,
                payload={"record": record},
            )
        )

    def delete_history_record(
        self,
        history_id: str,
        request_id: Optional[str] = None,
    ) -> None:
        """Deletes an individual history record and pushes status update."""
        if self.history_store is None:
            return
        success = self.history_store.delete_investigation(history_id)
        self._dispatch_ui(
            UiMessage(
                type="history.deleted",
                request_id=request_id,
                payload={"id": history_id, "success": success},
            )
        )
        self.push_history_status()

    def clear_history(self, request_id: Optional[str] = None) -> None:
        """Deletes all history records. Preserves credentials, preferences, and Slack profile."""
        if self.history_store is None:
            return
        success, deleted_count = self.history_store.clear_all()
        with self._lock:
            self._current_citations.clear()
        self._dispatch_ui(
            UiMessage(
                type="history.cleared",
                request_id=request_id,
                payload={"success": success, "count": deleted_count},
            )
        )
        self.push_history_status()

    def push_history_status(self, request_id: Optional[str] = None) -> None:
        """Pushes history availability and record count to UI."""
        count = self.history_store.count() if self.history_store else 0
        available = self.history_store.is_available() if self.history_store else False
        self._dispatch_ui(
            UiMessage(
                type="history.status",
                request_id=request_id,
                payload={"count": count, "available": available},
            )
        )

    def exit_history_view(self) -> None:
        """Clears citation lookup state upon exiting historical view.
        Guarantees STALE_HISTORY_CITATIONS_SURVIVE_HISTORY_EXIT = False.
        """
        with self._lock:
            self._current_citations.clear()
