"""SearchBackend Interface and Synthetic Search Implementation."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Protocol, runtime_checkable

from pesu_agent.investigation.models import (
    EvidenceItem,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class SearchBackend(Protocol):
    """검색 백엔드 공통 추상화 인터페이스.
    
    실제 Slack CDP 수집기와 인공(Synthetic) 검색 백엔드가 동일한 인터페이스를 따르도록 합니다.
    """

    async def search(self, query: SearchQuery, **kwargs: Any) -> list[EvidenceItem]:
        """주어진 SearchQuery를 기반으로 관련 증거 항목들을 검색합니다."""
        ...

    async def collect_context(
        self, message_identifier: str, **kwargs: Any
    ) -> list[EvidenceItem]:
        """특정 메시지 식별자를 기준으로 스레드 또는 전후 맥락 증거를 수집합니다."""
        ...

    async def read_thread_context(
        self, candidate_item: EvidenceItem
    ) -> tuple[list[ThreadReplyItem], ThreadContextStatus]:
        """특정 후보 메시지의 스레드 댓글 목록과 확장 수집 상태를 반환합니다."""
        ...


class SyntheticSearchBackend:
    """테스트 및 벤치마크용 가상(Synthetic) 증거 검색 백엔드."""

    def __init__(
        self,
        corpus: Optional[list[EvidenceItem]] = None,
        active_team_id: Optional[str] = "T32QA15GC",
    ):
        self.corpus: list[EvidenceItem] = corpus or []
        self.active_team_id: Optional[str] = active_team_id
        self._identifier_map: dict[str, EvidenceItem] = {
            item.message_identifier or item.evidence_id: item for item in self.corpus
        }
        self.last_search_telemetry: dict[str, Any] = {}

    def get_active_team_id(self) -> Optional[str]:
        """Returns the synthetic active team ID."""
        return self.active_team_id

    def add_evidence_items(self, items: list[EvidenceItem]) -> None:
        """코퍼스에 가상 증거 항목을 추가합니다."""
        for item in items:
            self.corpus.append(item)
            key = item.message_identifier or item.evidence_id
            self._identifier_map[key] = item

    async def search(
        self, query: SearchQuery, max_results: int = 10, **kwargs: Any
    ) -> list[EvidenceItem]:
        """쿼리 키워드 매칭을 통해 가상 증거 코퍼스를 검색합니다."""
        query_text = query.query.strip().lower()
        
        # 기본 불용어 / 연산자 제거
        noise_tokens = {"and", "or", "not", "in", "2026", "2026년", "2026년도", "상반기", "하반기", "확인", "관련", "대해", "안건", "통해", "결과", "내용", "여부", "대한", "진행", "식별", "검색"}
        raw_tokens = [t.strip("\"'()[]") for t in re.split(r"[\s,;+]+", query_text) if len(t) >= 1]
        query_tokens = [t for t in raw_tokens if t and t not in noise_tokens and len(t) >= 2]
        
        if not query_tokens:
            query_tokens = [t for t in raw_tokens if len(t) >= 1]

        matched_items: list[tuple[float, EvidenceItem]] = []

        for item in self.corpus:
            # 채널 격리: 비공개/감사 채널(security-audit, tax-audit-special)은 채널 힌트 또는 고유 식별 코드가 쿼리에 포함되어야만 검색 가능
            if item.channel and any(sec in item.channel.lower() for sec in ("security-audit", "tax-audit-special")):
                has_channel_clue = bool(
                    (query.target_channel_hint and (item.channel.lower() in query.target_channel_hint.lower() or query.target_channel_hint.lower() in item.channel.lower()))
                    or (item.channel.lower() in query_text)
                )
                has_code_clue = any(
                    code in query_text for code in ("sec-orion-99", "zephyros-refund", "orion-99", "zephyros")
                )
                if not (has_channel_clue or has_code_clue):
                    continue

            # 채널 힌트가 있는 경우 우선 매칭 점수 부여
            channel_boost = 1.0
            if query.target_channel_hint and item.channel:
                if query.target_channel_hint.lower() in item.channel.lower() or item.channel.lower() in query.target_channel_hint.lower():
                    channel_boost = 1.5

            item_text = f"{item.text} {item.author or ''} {item.channel or ''} {' '.join(item.attachment_names)}".lower()
            
            # 매칭된 유의미 토큰 수 계산
            token_matches = sum(1 for token in query_tokens if token in item_text)
            
            # 검색 매칭 임계치: 쿼리 토큰이 2개 이상이면 최소 2개 이상 매칭되거나 매칭 비율이 25% 이상이어야 함
            is_valid_match = False
            if len(query_tokens) == 1:
                is_valid_match = (token_matches >= 1)
            else:
                is_valid_match = (token_matches >= 2) or (token_matches / len(query_tokens) >= 0.25)

            if is_valid_match:
                # 점수 산출: 토큰 매칭 비율 * 채널 부스트
                score = (token_matches / max(len(query_tokens), 1)) * channel_boost
                # 전체 구문 또는 주요 키워드 포함 시 가산점
                if query_text in item_text:
                    score += 0.5
                matched_items.append((score, item))

        # 점수 내림차순 정렬 후 최대 개수 반환
        matched_items.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in matched_items[:max_results]]
        
        self.last_search_telemetry = {
            "query": query.query,
            "raw_items_count": len(results),
            "valid_items_count": len(results),
            "raw_items_evaluated": [
                {
                    "raw_index": idx + 1,
                    "canonical_permalink": it.permalink or it.message_identifier,
                    "sender_author_id": it.author_id,
                    "sender_text": it.author,
                    "timestamp_raw": it.timestamp,
                    "normalized_body": it.text,
                    "body_valid": True,
                    "ambiguous_fragment_count": 0,
                    "thread_reply_detected": it.has_thread,
                    "drop_reason": None,
                }
                for idx, it in enumerate(results)
            ],
        }
        logger.info(
            f"🔍 [SYNTHETIC_SEARCH] query='{query.query}' matches={len(results)}/{len(self.corpus)}"
        )
        return results

    async def collect_context(
        self, message_identifier: str, **kwargs: Any
    ) -> list[EvidenceItem]:
        """메시지 식별자를 기반으로 연관 맥락을 반환합니다."""
        if message_identifier in self._identifier_map:
            return [self._identifier_map[message_identifier]]
        return []

    async def read_thread_context(
        self, candidate_item: EvidenceItem
    ) -> tuple[list[ThreadReplyItem], ThreadContextStatus]:
        """가상 검색 백엔드용 스레드 확장 구현."""
        if candidate_item.thread_replies:
            status_val = candidate_item.thread_context_status
            status = (
                ThreadContextStatus(status_val)
                if status_val in ThreadContextStatus._value2member_map_
                else ThreadContextStatus.COMPLETE
            )
            return candidate_item.thread_replies, status
        return [], ThreadContextStatus.NOT_ATTEMPTED
