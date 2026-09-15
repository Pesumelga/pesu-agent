"""Real Slack Search Backend Adapter (Phase 9).

Frozen Slack Infrastructure(SlackBootstrapAdapter, SlackEvidenceCollector 등)를 
전혀 수정하지 않고 감싸서, SearchBackend Protocol 인터페이스에 맞춰 실제 Slack 검색 및 문맥 수집을 수행합니다.

보안 규칙:
1. 실제 Slack에서 가져온 메시지는 로컬 벤치마크 평가기(Evaluator) 내부에서만 사용되며,
   외부 LLM으로는 어떠한 Slack 원문 데이터도 전송되지 않습니다 (actual_slack_data_external_egress = 0).
2. 사용자 메인 작업 윈도우는 전혀 건드리지 않고 격리된 Secondary Agent Window를 통해서만 작동합니다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from pesu_agent.evidence.slack_evidence_collector import (
    SlackEvidenceCollector,
    SlackEvidencePackage,
    normalize_slack_message_ts,
)
from pesu_agent.investigation.models import EvidenceItem, SearchQuery
from pesu_agent.investigation.search_backend import SearchBackend
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler

logger = logging.getLogger(__name__)


class RealSlackSearchBackend(SearchBackend):
    """실제 Slack CDP 환경과 연동되는 읽기 전용 SearchBackend 구현체."""

    def __init__(
        self,
        evidence_collector: Optional[SlackEvidenceCollector] = None,
        default_max_results: int = 5,
    ) -> None:
        self._collector = evidence_collector
        self.default_max_results = default_max_results
        self.last_package: Optional[SlackEvidencePackage] = None

    @property
    def collector(self) -> SlackEvidenceCollector:
        """SlackEvidenceCollector 인스턴스를 지연 생성(Lazy Initialization)합니다."""
        if self._collector is None:
            self._collector = SlackEvidenceCollector()
        return self._collector

    async def search(
        self, query: SearchQuery, max_results: Optional[int] = None, **kwargs: Any
    ) -> list[EvidenceItem]:
        """주어진 SearchQuery를 SlackQueryCompiler로 컴파일하여 복수 단순 쿼리로 실행 및 Union 수집합니다."""
        limit = max_results or self.default_max_results
        compiled_queries = SlackQueryCompiler.compile(query, max_queries=4)
        logger.info(
            f"🔎 [REAL_SLACK_SEARCH] Original query='{query.query}' compiled into "
            f"{len(compiled_queries)} Slack-native simple queries: {compiled_queries}"
        )

        start_time = time.perf_counter()
        all_evidence_items: list[EvidenceItem] = []
        seen_ids: set[str] = set()

        for q_str in compiled_queries:
            logger.info(f"  ▶ Executing compiled simple query: '{q_str}' (limit={limit})")
            package = await self.collector.collect_evidence_package(
                query=q_str,
                max_results=limit,
            )
            self.last_package = package

            for msg in package.unique_messages:
                norm_ts = normalize_slack_message_ts(msg.timestamp_raw)
                canonical_id = msg.result_url or norm_ts or msg.message_fingerprint
                if not canonical_id or canonical_id in seen_ids:
                    continue
                seen_ids.add(canonical_id)

                item = EvidenceItem(
                    evidence_id=f"real_ev_{len(all_evidence_items)+1:02d}",
                    source="slack",
                    author=msg.author,
                    timestamp=msg.timestamp_raw,
                    text=msg.text,
                    channel=msg.channel_name or msg.channel_id,
                    message_identifier=canonical_id,
                    thread_identifier=msg.thread_identifier_candidate,
                    has_attachments=msg.has_attachments,
                    attachment_count=msg.attachment_count,
                    attachment_names=msg.attachment_names,
                )
                all_evidence_items.append(item)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            f"✅ [REAL_SLACK_SEARCH_COMPLETE] compiled_queries={len(compiled_queries)} "
            f"total_unique_items={len(all_evidence_items)} latency_ms={elapsed_ms:.1f}ms"
        )
        return all_evidence_items

    async def collect_context(
        self, message_identifier: str, **kwargs: Any
    ) -> list[EvidenceItem]:
        """특정 메시지 식별자에 대한 상세 문맥을 수집합니다."""
        logger.info(f"🔍 [REAL_SLACK_COLLECT_CONTEXT] identifier='{message_identifier}'")
        # 실제 메시지 식별자 기반 문맥 수집이 필요한 경우 collector의 상세 탐색 활용
        return []
