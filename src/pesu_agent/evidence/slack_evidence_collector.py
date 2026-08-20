"""Slack Multi-Result Evidence Collector (MVP 3.2).

하나의 검색어(query)에 대해 검색 결과 여러 건(기본 max_results=5)을 순차적으로 조사하여,
각 검색 결과의 원문, 전후 대화 문맥(before/after), 및 스레드 댓글(thread replies)을 수집하고,
단일 사용자 상태 저장/복원 및 글로벌 중복 메시지 제거(Global Deduplication)를 거쳐
통합 Evidence Package(SlackEvidencePackage)를 구성합니다.

핵심 원칙 및 안전 제약:
1. 최적화된 상태 복원 (단일 세션 관리):
   - 검색 결과 N건마다 복원하지 않고, 작업 시작 시 1회 저장 -> N건 순차 조사 -> 작업 종료 시 1회 복원
   - 불필요한 navigation 감소를 통한 성능 개선 기대
2. 개별 실패 격리 (Fault Isolation):
   - 특정 결과의 target mismatch 또는 파싱 실패가 전체 Evidence Package를 중단시키지 않음
3. 검색 메타데이터 메모리 스냅샷 (In-Memory Snapshotting):
   - 검색 직후 상위 N개 결과 메타데이터를 메모리에 스냅샷한 후 순회 (검색 UI DOM 재조회 배제)
4. 개선된 사용자 활성화 양보 (Restore-Pending & Background Wait):
   - 조사 도중 Slack Foreground 감지 시 즉시 탐색 중단
   - 사용자가 보고 있는 동안에는 복원 navigation을 시도하지 않고, Background 복귀 대기 후 복원
   - 사용자 지속 활성화 시 restore_pending=True 상태로 안전 종료
5. 스레드 댓글 심층 수집 (Thread Replies Extraction):
   - has_thread=True인 경우 root 검증 -> thread panel open(CDP/DOM) -> root와 replies 분리 -> 수집
6. 참조 보존 글로벌 중복 제거 (Reference-Preserving Global Deduplication):
   - unique_messages 풀을 유지하면서 각 Evidence Item의 before/target/after/thread_reply 참조 ID 보존
7. 성능 지표 정밀 측정:
   - search_elapsed_seconds, investigation_elapsed_seconds, restore_elapsed_seconds, total_elapsed_seconds
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from pesu_agent.adapters.slack_cdp import (
    SlackCdpAdapter,
    SlackCdpError,
    SlackNotReadyError,
)
from pesu_agent.context.slack_context_collector import (
    SlackContextCollector,
    SlackContextMessage,
    SlackRestorationResult,
    SlackViewState,
    is_slack_foreground,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.search.slack_search import (
    SlackSearch,
    SlackSearchResult,
    SlackSearchSession,
)

logger = logging.getLogger(__name__)


class SlackEvidenceItem(BaseModel):
    """검색 결과 1건에 대한 상세 조사 증거 항목."""

    result_index: int = Field(description="검색 결과 내 인덱스")
    search_result: SlackSearchResult = Field(description="출발점이 된 원본 검색 결과")
    target_verified: bool = Field(default=False, description="Target 메시지 검증 일치 여부")
    failure_reason: Optional[str] = Field(default=None, description="조사 실패 시 원인 설명")

    # 메시지 및 문맥
    target_message: Optional[SlackContextMessage] = Field(
        default=None, description="실제 대화 뷰에서 확인된 Target 메시지"
    )
    before_messages: list[SlackContextMessage] = Field(
        default_factory=list, description="Target 이전 대화 목록"
    )
    after_messages: list[SlackContextMessage] = Field(
        default_factory=list, description="Target 이후 대화 목록"
    )

    # 스레드 정보 및 댓글
    has_thread: bool = Field(default=False, description="스레드 존재 여부")
    reply_count: int = Field(default=0, description="스레드 댓글 수")
    thread_identifier_candidate: Optional[str] = Field(
        default=None, description="스레드 식별자 후보"
    )
    thread_replies: list[SlackContextMessage] = Field(
        default_factory=list, description="수집된 실제 스레드 댓글 목록 (최대 20건)"
    )

    # 참조 보존 지표 (Global Deduplication Reference Mapping)
    before_message_refs: list[str] = Field(
        default_factory=list, description="Before 메시지 지문 참조 목록"
    )
    target_message_ref: Optional[str] = Field(
        default=None, description="Target 메시지 지문 참조"
    )
    after_message_refs: list[str] = Field(
        default_factory=list, description="After 메시지 지문 참조 목록"
    )
    thread_reply_refs: list[str] = Field(
        default_factory=list, description="Thread reply 메시지 지문 참조 목록"
    )

    # 메타데이터
    source_permalink: Optional[str] = Field(default=None, description="메시지 퍼머링크")
    channel_id: Optional[str] = Field(default=None, description="채널 ID")
    channel_name: Optional[str] = Field(default=None, description="채널명")
    message_ts_candidate: Optional[str] = Field(default=None, description="타임스탬프 원시 식별자")
    investigation_elapsed_seconds: float = Field(
        default=0.0, description="해당 항목 조사 소요 시간 (초)"
    )


class SlackEvidencePackage(BaseModel):
    """복수 검색 결과에 대한 통합 Evidence 패키지."""

    evidence_session_id: str = Field(description="Evidence 세션 고유 ID")
    query: str = Field(description="검색 질의어")

    # 세션 상태 캡처 및 복원 횟수
    user_state_snapshots: int = Field(default=1, description="사용자 상태 캡처 횟수")
    user_state_restore_attempts: int = Field(default=0, description="사용자 상태 복원 시도 횟수")

    # 수집 통계
    result_metadata_snapshotted_count: int = Field(
        default=0, description="메모리에 스냅샷된 검색 결과 수"
    )
    results_investigated: int = Field(default=0, description="조사 시도한 결과 수")
    results_succeeded: int = Field(default=0, description="검증 성공한 결과 수")
    results_failed: int = Field(default=0, description="검증 실패한 결과 수")
    duplicate_context_messages_removed: int = Field(
        default=0, description="글로벌 중복 제거된 메시지 수"
    )
    unique_context_messages: int = Field(
        default=0, description="중복 제거 후 고유 메시지 총합"
    )
    thread_roots_found: int = Field(default=0, description="발견된 스레드 루트 수")
    thread_replies_collected: int = Field(default=0, description="수집된 총 스레드 댓글 수")

    # 하위 호환성 필드
    searched_result_count: int = Field(default=0, description="검색된 총 결과 수")
    investigated_result_count: int = Field(default=0, description="조사 시도한 결과 수")
    successful_evidence_count: int = Field(default=0, description="성공적으로 검증된 증거 수")
    failed_evidence_count: int = Field(default=0, description="검증 실패한 증거 수")
    duplicate_evidence_removed: int = Field(
        default=0, description="글로벌 중복 제거된 메시지 수"
    )
    unique_total_messages_count: int = Field(
        default=0, description="중복 제거 후 고유 메시지 총합"
    )

    # 통합 고유 메시지 풀 & 개별 증거 목록
    unique_messages: list[SlackContextMessage] = Field(
        default_factory=list, description="전체 조사 결과에서 추출된 고유 메시지 목록"
    )
    evidence_items: list[SlackEvidenceItem] = Field(
        default_factory=list, description="개별 검색 결과 조사 항목 목록"
    )

    # 상태 및 복원 지표
    user_interrupted: bool = Field(
        default=False, description="사용자 Slack 활성화에 의한 안전 중단 여부"
    )
    interruption_reason: Optional[str] = Field(
        default=None, description="중단 원인 설명"
    )
    restore_pending: bool = Field(
        default=False, description="복원 대기/보류 여부 (사용자가 Foreground 사용 중)"
    )
    state_restore_attempted: bool = Field(
        default=False, description="원래 Slack 상태 복원 시도 여부"
    )
    state_restore_succeeded: bool = Field(
        default=False, description="원래 Slack 상태 복원 성공 여부"
    )
    restoration_metrics: Optional[SlackRestorationResult] = Field(
        default=None, description="상태 복원 세부 지표"
    )

    # 시간 측정
    search_elapsed_seconds: float = Field(default=0.0, description="검색 소요 시간 (초)")
    investigation_elapsed_seconds: float = Field(
        default=0.0, description="문맥 조사 총 소요 시간 (초)"
    )
    restore_elapsed_seconds: float = Field(
        default=0.0, description="상태 복원 소요 시간 (초)"
    )
    total_elapsed_seconds: float = Field(default=0.0, description="전체 파이프라인 소요 시간 (초)")
    collected_at: str = Field(
        default_factory=lambda: datetime.datetime.now().isoformat(),
        description="수집 완료 시각 (ISO 8601)",
    )


class SlackEvidenceCollector:
    """Slack 다중 검색 결과 순차 조사 및 Evidence Package 구성 엔진."""

    def __init__(
        self,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        cdp_adapter: Optional[SlackCdpAdapter] = None,
    ):
        self.lifecycle_manager = lifecycle_manager or SlackLifecycleManager()
        self.cdp = cdp_adapter or SlackCdpAdapter(lifecycle_manager=self.lifecycle_manager)
        self.context_collector = SlackContextCollector(
            lifecycle_manager=self.lifecycle_manager, cdp_adapter=self.cdp
        )
        self.searcher = SlackSearch(
            cdp_adapter=self.cdp, lifecycle_manager=self.lifecycle_manager
        )

    async def _extract_thread_replies(
        self, target_ts: str, max_replies: int = 20
    ) -> list[SlackContextMessage]:
        """열려 있는 스레드 패널(.p-flexpane)에서 Root 메시지와 Replies를 분리하여 Replies 목록을 추출합니다."""
        js_thread = f"""
        (() => {{
            const threadPane = document.querySelector(
                '.p-flexpane, [data-qa="threads_flexpane"], [data-qa="thread_view"], [aria-label*="스레드"], [aria-label*="Thread"], .c-split_view__secondary, [data-qa="flexpane"]'
            );
            if (!threadPane) {{
                return {{ found: false, messages: [] }};
            }}

            const items = Array.from(threadPane.querySelectorAll(
                '.c-virtual_list__item, [data-qa="virtual-list-item"], .c-message_kit__message, [data-qa="message_container"], [role="listitem"]'
            ));

            const replies = [];
            for (let i = 0; i < items.length; i++) {{
                const item = items[i];
                const textElem = item.querySelector(
                    '[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body'
                );
                const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");
                const sender = item.querySelector(
                    '[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender]'
                )?.innerText.trim() || null;
                const time = item.querySelector(
                    '[data-qa="message_timestamp"], .c-timestamp'
                )?.innerText.trim() || null;
                const linkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') || item.querySelector('a.c-timestamp');
                const href = linkElem ? linkElem.getAttribute('href') : null;

                if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {{
                    replies.push({{
                        idx: i,
                        author: sender,
                        timestamp_raw: time,
                        text: text,
                        href: href
                    }});
                }}
            }}

            return {{
                found: true,
                total_items: items.length,
                messages: replies
            }};
        }})()
        """
        raw_res = await self.cdp.evaluate_js(js_thread) or {}
        raw_msgs = raw_res.get("messages", [])

        # thread root(첫 번째 메시지)는 제외하고 실제 reply(인덱스 1 이후)만 분리 추출
        extracted_replies: list[SlackContextMessage] = []
        reply_slice = raw_msgs[1 : 1 + max_replies] if len(raw_msgs) > 1 else []

        for m in reply_slice:
            fp = hashlib.md5(
                f"{m.get('author')}|{m.get('timestamp_raw')}|{m.get('text')}".encode("utf-8")
            ).hexdigest()[:16]
            extracted_replies.append(
                SlackContextMessage(
                    idx=m.get("idx", 0),
                    author=m.get("author"),
                    timestamp_raw=m.get("timestamp_raw"),
                    text=m.get("text", ""),
                    result_url=m.get("href"),
                    message_fingerprint=fp,
                    is_target=False,
                    has_thread=False,
                    reply_count=0,
                    thread_identifier_candidate=target_ts,
                )
            )

        return extracted_replies

    async def collect_evidence_package(
        self,
        query: str,
        max_results: int = 5,
        context_before: int = 10,
        context_after: int = 10,
        max_thread_replies: int = 20,
        check_user_interference: bool = True,
        bg_wait_timeout_sec: float = 3.0,
    ) -> SlackEvidencePackage:
        """
        검색어에 대해 상위 max_results건을 순차 조사하여 단일 Evidence Package로 반환합니다.
        
        흐름:
        1. 상태 사전 검증 및 시작 전 Foreground 확인
        2. 사용자 초기 뷰 상태 스냅샷 (1회)
        3. 검색 실행 및 상위 N개 메타데이터 메모리 스냅샷
        4. 순차 조사 루프 (Main Renderer 직접 이동, DOM 파싱, 스레드 추출)
           - 루프 도중 Foreground 감지 시 즉시 중단 및 Background 복귀 대기
        5. 작업 종료 시 원래 상태 정밀 복원 (1회)
        6. 참조 보존 글로벌 중복 제거 및 Evidence Package 생성
        """
        session_id = f"ev_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        total_start = time.time()

        # 1. 상태 사전 확인
        status = self.lifecycle_manager.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 준비되지 않았습니다. (현재 상태: {status.status.value})"
            )

        if check_user_interference and is_slack_foreground():
            logger.warning("조사 시작 전 사용자가 Slack을 활성화(Foreground)했습니다.")
            return SlackEvidencePackage(
                evidence_session_id=session_id,
                query=query,
                user_state_snapshots=1,
                user_state_restore_attempts=0,
                user_interrupted=True,
                interruption_reason="user_opened_slack",
                restore_pending=False,
                state_restore_attempted=False,
                state_restore_succeeded=False,
            )

        evidence_items: list[SlackEvidenceItem] = []
        user_interrupted = False
        interruption_reason = None
        restore_pending = False
        search_elapsed = 0.0
        investigation_elapsed = 0.0
        restore_elapsed = 0.0
        before_state: Optional[SlackViewState] = None
        restoration_metrics: Optional[SlackRestorationResult] = None
        state_restore_attempted = False
        state_restore_succeeded = False
        user_state_restore_attempts = 0
        thread_roots_found = 0
        thread_replies_collected = 0

        async with self.cdp:
            # 2. 원래 Slack 상태 스냅샷 저장 (1회)
            before_state = await self.context_collector.capture_view_state()
            logger.info(
                f"Initial Slack View State Saved: URL={before_state.url}, Conv={before_state.conversation_name}"
            )

            # 3. 검색 실행
            search_start = time.time()
            search_session: SlackSearchSession = await self.searcher.search(
                query=query, max_scrolls=0
            )
            search_elapsed = time.time() - search_start

            # 검색 직후 상위 N개 결과 메타데이터를 메모리에 Snapshot (검색 DOM 재조회 배제)
            candidates: list[SlackSearchResult] = [
                cand.model_copy(deep=True) for cand in search_session.results[:max_results]
            ]
            result_metadata_snapshotted_count = len(candidates)
            logger.info(
                f"Search completed in {search_elapsed:.2f}s: Snapshotted {result_metadata_snapshotted_count} candidate metadata items in memory"
            )

            # 4. 순차 조사 루프
            inv_start = time.time()
            team_id = "T32QA15GC"
            if before_state.url:
                t_match = re.search(r"/client/([A-Z0-9]+)", before_state.url)
                if t_match:
                    team_id = t_match.group(1)

            for idx, cand in enumerate(candidates):
                item_start = time.time()
                logger.info(
                    f"Investigating Result [{idx + 1}/{len(candidates)}]: Channel={cand.channel_name}, Permalink={cand.result_url}"
                )

                # 순차 조사 중 사용자 활성화(Foreground) 확인
                if check_user_interference and is_slack_foreground():
                    logger.warning(
                        f"조사 도중(Result {idx + 1} 진입 전) 사용자가 Slack을 활성화했습니다. 즉시 중단합니다."
                    )
                    user_interrupted = True
                    interruption_reason = "user_opened_slack"
                    restore_pending = True
                    break

                channel_id, raw_ts, _ = SlackContextCollector.parse_permalink(cand.result_url)
                if not channel_id or not raw_ts:
                    evidence_items.append(
                        SlackEvidenceItem(
                            result_index=idx,
                            search_result=cand,
                            target_verified=False,
                            failure_reason=f"퍼머링크 파싱 실패: {cand.result_url}",
                            source_permalink=cand.result_url,
                            investigation_elapsed_seconds=time.time() - item_start,
                        )
                    )
                    continue

                try:
                    # Target In-app URL 이동 (archive permalink 우선)
                    target_url = (
                        cand.result_url
                        if cand.result_url
                        else f"https://app.slack.com/client/{team_id}/{channel_id}?p=p{raw_ts}"
                    )
                    await self.cdp.evaluate_js(
                        f"window.location.href = {json.dumps(target_url)};"
                    )
                    await asyncio.sleep(3.5)

                    # 이동 후 Foreground 확인
                    if check_user_interference and is_slack_foreground():
                        logger.warning("원문 이동 직후 사용자가 Slack을 활성화했습니다.")
                        user_interrupted = True
                        interruption_reason = "user_opened_slack"
                        restore_pending = True
                        break

                    # DOM 메시지 추출
                    js_extract = f"""
                    (() => {{
                        const targetTs = {json.dumps(raw_ts)};
                        const targetTextSnippet = {json.dumps(cand.text[:40])};

                        const items = Array.from(document.querySelectorAll(
                            '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
                        ));

                        const messages = [];
                        let targetIdx = -1;

                        for (let i = 0; i < items.length; i++) {{
                            const item = items[i];
                            const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body');
                            const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");
                            const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender]');
                            const sender = senderElem ? senderElem.innerText.trim() : null;
                            const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp');
                            const timeStr = timeElem ? timeElem.innerText.trim() : null;
                            const linkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') || item.querySelector('a.c-timestamp');
                            const href = linkElem ? linkElem.getAttribute('href') : null;
                            const itemKey = item.getAttribute('data-item-key') || item.id || '';

                            const replyElem = item.querySelector(
                                '[data-qa="reply_count"], .c-message__reply_count, [data-qa="message_reply_count"], [aria-label*="댓글"], [aria-label*="replies"], [aria-label*="reply"], button[data-qa="reply_bar"]'
                            );
                            const replyBar = item.querySelector('.c-message__reply_bar, [data-qa="reply_bar"]');

                            let replyCount = 0;
                            let hasThread = false;
                            const replyText = replyElem ? replyElem.innerText.trim() : (replyBar ? replyBar.innerText.trim() : "");
                            const replyAria = replyElem ? replyElem.getAttribute('aria-label') : (replyBar ? replyBar.getAttribute('aria-label') : "");
                            const combinedReply = replyText + ' ' + (replyAria || '');
                            
                            const countMatch = combinedReply.match(/(\\d+)/);
                            if (countMatch) {{
                                replyCount = parseInt(countMatch[1]);
                                hasThread = true;
                            }} else if (replyElem || replyBar) {{
                                hasThread = true;
                                replyCount = 1;
                            }}

                            if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {{
                                const msgObj = {{
                                    idx: i,
                                    author: sender,
                                    timestamp_raw: timeStr,
                                    text: text,
                                    href: href,
                                    has_thread: hasThread,
                                    reply_count: replyCount,
                                    item_key: itemKey
                                }};

                                const cleanTargetTs = (targetTs || '').replace(/[^0-9]/g, '');
                                const cleanHref = (href || '').replace(/[^0-9]/g, '');
                                const cleanKey = (itemKey || '').replace(/[^0-9]/g, '');
                                const tsMatch = (cleanHref && cleanHref.includes(cleanTargetTs)) || (cleanKey && cleanKey.includes(cleanTargetTs));

                                const normText = (text || '').replace(/\\s+/g, ' ');
                                const normSnippet = (targetTextSnippet || '').replace(/\\s+/g, ' ');
                                const textMatch = normSnippet.length >= 10 && normText.includes(normSnippet.substring(0, Math.min(20, normSnippet.length)));

                                if (targetIdx === -1 && (tsMatch || textMatch)) {{
                                    targetIdx = messages.length;
                                    msgObj.is_target = true;
                                }}
                                messages.push(msgObj);
                            }}
                        }}

                        return {{
                            target_idx: targetIdx,
                            messages: messages
                        }};
                    }})()
                    """
                    dom_res = await self.cdp.evaluate_js(js_extract) or {}
                    target_idx = dom_res.get("target_idx", -1)
                    raw_messages = dom_res.get("messages", [])

                    if target_idx >= 0 and target_idx < len(raw_messages):
                        tm = raw_messages[target_idx]
                        target_fp = hashlib.md5(
                            f"{tm.get('author')}|{tm.get('timestamp_raw')}|{tm.get('text')}".encode(
                                "utf-8"
                            )
                        ).hexdigest()[:16]

                        has_thread = tm.get("has_thread", False)
                        reply_count = tm.get("reply_count", 0)
                        thread_id = raw_ts if has_thread else None

                        target_msg = SlackContextMessage(
                            idx=tm.get("idx"),
                            author=tm.get("author") or cand.author,
                            timestamp_raw=tm.get("timestamp_raw") or cand.timestamp_raw,
                            text=tm.get("text"),
                            channel_name=cand.channel_name,
                            channel_id=channel_id,
                            result_url=tm.get("href") or cand.result_url,
                            message_fingerprint=target_fp,
                            is_target=True,
                            has_thread=has_thread,
                            reply_count=reply_count,
                            thread_identifier_candidate=thread_id,
                        )

                        # Before messages
                        before_msgs: list[SlackContextMessage] = []
                        start_before = max(0, target_idx - context_before)
                        for m in raw_messages[start_before:target_idx]:
                            fp = hashlib.md5(
                                f"{m.get('author')}|{m.get('timestamp_raw')}|{m.get('text')}".encode(
                                    "utf-8"
                                )
                            ).hexdigest()[:16]
                            before_msgs.append(
                                SlackContextMessage(
                                    idx=m.get("idx"),
                                    author=m.get("author"),
                                    timestamp_raw=m.get("timestamp_raw"),
                                    text=m.get("text"),
                                    channel_name=cand.channel_name,
                                    channel_id=channel_id,
                                    result_url=m.get("href"),
                                    message_fingerprint=fp,
                                    is_target=False,
                                    has_thread=m.get("has_thread", False),
                                    reply_count=m.get("reply_count", 0),
                                )
                            )

                        # After messages
                        after_msgs: list[SlackContextMessage] = []
                        end_after = min(len(raw_messages), target_idx + 1 + context_after)
                        for m in raw_messages[target_idx + 1 : end_after]:
                            fp = hashlib.md5(
                                f"{m.get('author')}|{m.get('timestamp_raw')}|{m.get('text')}".encode(
                                    "utf-8"
                                )
                            ).hexdigest()[:16]
                            after_msgs.append(
                                SlackContextMessage(
                                    idx=m.get("idx"),
                                    author=m.get("author"),
                                    timestamp_raw=m.get("timestamp_raw"),
                                    text=m.get("text"),
                                    channel_name=cand.channel_name,
                                    channel_id=channel_id,
                                    result_url=m.get("href"),
                                    message_fingerprint=fp,
                                    is_target=False,
                                    has_thread=m.get("has_thread", False),
                                    reply_count=m.get("reply_count", 0),
                                )
                            )

                        # 5. 스레드 댓글 심층 수집 (has_thread=True인 경우)
                        thread_replies: list[SlackContextMessage] = []
                        if has_thread and reply_count > 0:
                            thread_roots_found += 1
                            # Thread Root Identity 검증 완료 (target_msg)
                            # Foreground Guard 확인
                            if check_user_interference and is_slack_foreground():
                                logger.warning("스레드 오픈 직전 Foreground 활성화 감지됨.")
                                user_interrupted = True
                                interruption_reason = "user_opened_slack"
                                restore_pending = True
                                break

                            # CDP/DOM 방식으로 reply bar 클릭하여 스레드 flexpane 오픈
                            js_open_thread = f"""
                            (() => {{
                                const cleanTargetTs = {json.dumps(raw_ts)}.replace(/[^0-9]/g, '');
                                const items = Array.from(document.querySelectorAll('.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'));
                                for (const item of items) {{
                                    const replyBar = item.querySelector('[data-qa="reply_count"], .c-message__reply_count, [data-qa="reply_bar"], button[aria-label*="댓글"], button[aria-label*="replies"], button[aria-label*="reply"], button[data-qa="reply_bar"]');
                                    if (replyBar) {{
                                        const href = (item.querySelector('a[href*="/archives/"]')?.getAttribute('href') || '').replace(/[^0-9]/g, '');
                                        const itemKey = (item.getAttribute('data-item-key') || item.id || '').replace(/[^0-9]/g, '');
                                        if ((cleanTargetTs && (href.includes(cleanTargetTs) || itemKey.includes(cleanTargetTs))) || !cleanTargetTs) {{
                                            replyBar.click();
                                            return {{ clicked: true }};
                                        }}
                                    }}
                                }}
                                return {{ clicked: false }};
                            }})()
                            """
                            await self.cdp.evaluate_js(js_open_thread)
                            await asyncio.sleep(2.0)

                            # Foreground Guard 재확인
                            if check_user_interference and is_slack_foreground():
                                logger.warning("스레드 패널 오픈 후 Foreground 활성화 감지됨.")
                                user_interrupted = True
                                interruption_reason = "user_opened_slack"
                                restore_pending = True
                                break

                            # Root와 Replies 분리 추출
                            thread_replies = await self._extract_thread_replies(
                                target_ts=raw_ts, max_replies=max_thread_replies
                            )
                            thread_replies_collected += len(thread_replies)

                        evidence_items.append(
                            SlackEvidenceItem(
                                result_index=idx,
                                search_result=cand,
                                target_verified=True,
                                target_message=target_msg,
                                before_messages=before_msgs,
                                after_messages=after_msgs,
                                has_thread=has_thread,
                                reply_count=reply_count,
                                thread_identifier_candidate=thread_id,
                                thread_replies=thread_replies,
                                source_permalink=cand.result_url,
                                channel_id=channel_id,
                                channel_name=cand.channel_name,
                                message_ts_candidate=raw_ts,
                                investigation_elapsed_seconds=time.time() - item_start,
                            )
                        )
                    else:
                        # Target 식별 실패 (Fault Isolation)
                        evidence_items.append(
                            SlackEvidenceItem(
                                result_index=idx,
                                search_result=cand,
                                target_verified=False,
                                failure_reason=f"타깃 메시지 식별자({raw_ts})를 DOM({len(raw_messages)}건)에서 찾을 수 없음",
                                source_permalink=cand.result_url,
                                channel_id=channel_id,
                                channel_name=cand.channel_name,
                                message_ts_candidate=raw_ts,
                                investigation_elapsed_seconds=time.time() - item_start,
                            )
                        )
                except Exception as ex:
                    logger.warning(f"Result {idx + 1} 조사 중 오류 발생 (격리): {ex}")
                    evidence_items.append(
                        SlackEvidenceItem(
                            result_index=idx,
                            search_result=cand,
                            target_verified=False,
                            failure_reason=f"예외 발생: {str(ex)}",
                            source_permalink=cand.result_url,
                            channel_id=channel_id,
                            channel_name=cand.channel_name,
                            message_ts_candidate=raw_ts,
                            investigation_elapsed_seconds=time.time() - item_start,
                        )
                    )

            investigation_elapsed = time.time() - inv_start

            # 6. 작업 종료 시 또는 중단 시 상태 복원 처리
            if before_state:
                if user_interrupted:
                    logger.warning(
                        "사용자가 Slack을 활성화하여 조사가 중단되었습니다. 사용자가 보고 있는 동안에는 복원을 수행하지 않습니다."
                    )
                    # Background 복귀 대기
                    logger.info(
                        f"Slack Background 복귀 대기 중 (최대 {bg_wait_timeout_sec}초)..."
                    )
                    became_bg = await self.context_collector.wait_for_slack_background(
                        timeout_sec=bg_wait_timeout_sec
                    )
                    if became_bg:
                        logger.info("Slack이 Background로 복귀하여 원래 사용자 상태를 복원합니다.")
                        restore_start = time.time()
                        user_state_restore_attempts = 1
                        state_restore_attempted = True
                        restoration_metrics = await self.context_collector.restore_view_state(
                            before_state
                        )
                        restore_elapsed = time.time() - restore_start
                        state_restore_succeeded = (
                            restoration_metrics.url_restored
                            and restoration_metrics.conversation_restored
                            and (
                                restoration_metrics.scroll_restored
                                or restoration_metrics.viewport_restored
                            )
                        )
                        restore_pending = not state_restore_succeeded
                    else:
                        logger.warning(
                            "사용자가 여전히 Slack을 활성화한 상태입니다. 상태 복원을 보류(restore_pending=True)합니다."
                        )
                        state_restore_attempted = False
                        state_restore_succeeded = False
                        restore_pending = True
                else:
                    logger.info("순차 조사 완료: 원래 Slack 상태를 1회 정밀 복원합니다...")
                    restore_start = time.time()
                    user_state_restore_attempts = 1
                    state_restore_attempted = True
                    restoration_metrics = await self.context_collector.restore_view_state(
                        before_state
                    )
                    restore_elapsed = time.time() - restore_start
                    state_restore_succeeded = (
                        restoration_metrics.url_restored
                        and restoration_metrics.conversation_restored
                        and (
                            restoration_metrics.scroll_restored
                            or restoration_metrics.viewport_restored
                        )
                    )
                    restore_pending = False

        # 7. 참조 보존 글로벌 중복 제거 (Reference-Preserving Global Deduplication)
        seen_fps: set[str] = set()
        unique_messages_list: list[SlackContextMessage] = []
        total_raw_messages_count = 0

        for ev in evidence_items:
            # Before messages
            ev_before_refs: list[str] = []
            for bm in ev.before_messages:
                total_raw_messages_count += 1
                ev_before_refs.append(bm.message_fingerprint)
                if bm.message_fingerprint not in seen_fps:
                    seen_fps.add(bm.message_fingerprint)
                    unique_messages_list.append(bm)
            ev.before_message_refs = ev_before_refs

            # Target message
            if ev.target_message:
                total_raw_messages_count += 1
                ev.target_message_ref = ev.target_message.message_fingerprint
                if ev.target_message.message_fingerprint not in seen_fps:
                    seen_fps.add(ev.target_message.message_fingerprint)
                    unique_messages_list.append(ev.target_message)
            else:
                ev.target_message_ref = None

            # After messages
            ev_after_refs: list[str] = []
            for am in ev.after_messages:
                total_raw_messages_count += 1
                ev_after_refs.append(am.message_fingerprint)
                if am.message_fingerprint not in seen_fps:
                    seen_fps.add(am.message_fingerprint)
                    unique_messages_list.append(am)
            ev.after_message_refs = ev_after_refs

            # Thread replies
            ev_thread_refs: list[str] = []
            for tr in ev.thread_replies:
                total_raw_messages_count += 1
                ev_thread_refs.append(tr.message_fingerprint)
                if tr.message_fingerprint not in seen_fps:
                    seen_fps.add(tr.message_fingerprint)
                    unique_messages_list.append(tr)
            ev.thread_reply_refs = ev_thread_refs

        duplicate_removed_count = total_raw_messages_count - len(unique_messages_list)
        successful_ev_count = sum(1 for ev in evidence_items if ev.target_verified)
        failed_ev_count = len(evidence_items) - successful_ev_count
        total_elapsed = time.time() - total_start

        return SlackEvidencePackage(
            evidence_session_id=session_id,
            query=query,
            user_state_snapshots=1,
            user_state_restore_attempts=user_state_restore_attempts,
            result_metadata_snapshotted_count=result_metadata_snapshotted_count,
            results_investigated=len(evidence_items),
            results_succeeded=successful_ev_count,
            results_failed=failed_ev_count,
            duplicate_context_messages_removed=duplicate_removed_count,
            unique_context_messages=len(unique_messages_list),
            thread_roots_found=thread_roots_found,
            thread_replies_collected=thread_replies_collected,
            searched_result_count=len(search_session.results),
            investigated_result_count=len(evidence_items),
            successful_evidence_count=successful_ev_count,
            failed_evidence_count=failed_ev_count,
            duplicate_evidence_removed=duplicate_removed_count,
            unique_total_messages_count=len(unique_messages_list),
            unique_messages=unique_messages_list,
            evidence_items=evidence_items,
            user_interrupted=user_interrupted,
            interruption_reason=interruption_reason,
            restore_pending=restore_pending,
            state_restore_attempted=state_restore_attempted,
            state_restore_succeeded=state_restore_succeeded,
            restoration_metrics=restoration_metrics,
            search_elapsed_seconds=round(search_elapsed, 2),
            investigation_elapsed_seconds=round(investigation_elapsed, 2),
            restore_elapsed_seconds=round(restore_elapsed, 2),
            total_elapsed_seconds=round(total_elapsed, 2),
        )
