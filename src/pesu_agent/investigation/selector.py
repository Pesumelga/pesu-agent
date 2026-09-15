"""Deterministic Evidence Selector and Packaging Engine (Retrieval v2).

Features:
- Canonical identity deduplication with query_hit_count tracking.
- Zero static business keyword boost (purged: 공식, 결재, 합의, etc.).
- Generic Korean/Unicode NFKC anchor matching.
- Categorical temporal relevance reranking.
- Retrieval coverage evaluation (STRONG, LIMITED, NO_DIRECT_MATCH).
- Final evidence limit enforcement (<= 5).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Optional
import unicodedata

from pesu_agent.investigation.models import (
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    RetrievalCoverage,
    TemporalRelation,
    ResolvedTemporalScope,
    TemporalStrictness,
    TemporalResolutionStatus,
    ResolvedActorScope,
)
from pesu_agent.search.temporal import (
    evaluate_candidate_temporal_relation,
    resolve_candidate_authored_datetime,
)

logger = logging.getLogger(__name__)


def normalize_text_for_matching(text: str) -> str:
    """Generic text normalization: Unicode NFKC, casefold, punctuation removal, whitespace collapsing."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    # Replace punctuation with space
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def compute_anchor_matches(text: str, anchor_terms: list[str]) -> int:
    """Counts matching anchor terms using bounded substring/phrase matching."""
    if not text or not anchor_terms:
        return 0
    norm_text = normalize_text_for_matching(text)
    match_count = 0
    for term in anchor_terms:
        norm_term = normalize_text_for_matching(term)
        if norm_term and norm_term in norm_text:
            match_count += 1
    return match_count


def get_canonical_evidence_key(item: EvidenceItem) -> str:
    """Derives deterministic canonical identity for deduplication and provenance tracking."""
    canonical_key = item.message_identifier or getattr(item, "permalink", None)
    if not canonical_key:
        clean_text = normalize_text_for_matching(item.text)
        canonical_key = f"author:{item.author}|ts:{item.timestamp}|snip:{clean_text[:60]}"
    return canonical_key


class EvidenceSelector:
    """수집된 증거 후보군에서 중복을 제거하고 제약조건 기반으로 최적의 증거 패키지를 구성하는 결정론적 선택기."""

    def __init__(
        self,
        max_evidence_items: int = 5,
        max_total_chars: int = 12000,
        min_text_len: int = 5,
    ):
        self.max_evidence_items = max_evidence_items
        self.max_total_chars = max_total_chars
        self.min_text_len = min_text_len

    def select_and_package(
        self,
        candidates: list[EvidenceItem],
        plan: Optional[InvestigationPlan] = None,
        package_id: str = "pkg_orchestrated",
        query_summary: str = "종합 조사 질의",
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
    ) -> tuple[EvidencePackage, dict[str, int]]:
        """후보 증거군에서 중복을 제거하고 최적의 EvidencePackage를 생성합니다."""
        initial_candidate_count = len(candidates)
        stats = {
            "initial_candidates": initial_candidate_count,
            "duplicates_removed": 0,
            "noise_filtered": 0,
            "temporal_filtered_out": 0,
            "final_selected": 0,
        }

        temporal_constraint = plan.temporal_constraint if plan else None
        anchor_terms = plan.anchor_terms if plan else []

        if not candidates:
            empty_pkg = EvidencePackage(
                package_id=package_id,
                query=query_summary,
                total_items=0,
                created_at=datetime.now(timezone.utc).isoformat(),
                is_synthetic=True,
                evidence_items=[],
                retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                temporal_scope=temporal_scope,
                actor_scope=actor_scope,
            )
            return empty_pkg, stats

        # 0. Strict Temporal Pre-Filter (Phase 12.6d: STRICT_TEMPORAL_FILTER_PRECEDES_ANCHOR_RANKING)
        # Authoritative ResolvedTemporalScope with STRICT strictness filters candidates BEFORE ranking.
        if temporal_scope and temporal_scope.strictness == TemporalStrictness.STRICT:
            if temporal_scope.status in (
                TemporalResolutionStatus.AMBIGUOUS,
                TemporalResolutionStatus.UNRESOLVED,
            ):
                logger.warning(
                    f"⚠️ [STRICT_TEMPORAL_FAIL_CLOSED] Strict temporal resolution status is {temporal_scope.status.value}. "
                    "Failing closed: all candidates disqualified."
                )
                stats["temporal_filtered_out"] = len(candidates)
                candidates = []
            elif temporal_scope.status == TemporalResolutionStatus.RESOLVED:
                eligible_candidates: list[EvidenceItem] = []
                for item in candidates:
                    authored_dt = resolve_candidate_authored_datetime(item)
                    if authored_dt is not None and temporal_scope.is_in_range(authored_dt):
                        eligible_candidates.append(item)
                    else:
                        stats["temporal_filtered_out"] += 1
                candidates = eligible_candidates

            if not candidates:
                logger.info(
                    "ℹ️ [PERIOD_NO_EVIDENCE] No candidates matched the strict temporal window. "
                    f"Filtered out: {stats['temporal_filtered_out']}"
                )
                empty_pkg = EvidencePackage(
                    package_id=package_id,
                    query=query_summary,
                    total_items=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    is_synthetic=False,
                    evidence_items=[],
                    retrieval_coverage=RetrievalCoverage.NO_DIRECT_MATCH.value,
                    temporal_scope=temporal_scope,
                    actor_scope=actor_scope,
                )
                return empty_pkg, stats

        # 1. 중복 제거 및 query_hit_count 추적 (pure non-mutating out-of-band 집계)
        # local evidence_id는 쿼리별로 달라질 수 있으므로 canonical primary key로 단독 사용하지 않음
        first_items: dict[str, EvidenceItem] = {}
        hit_counts: dict[str, int] = {}
        item_order: list[str] = []

        for item in candidates:
            canonical_key = get_canonical_evidence_key(item)

            if canonical_key in first_items:
                stats["duplicates_removed"] += 1
                incoming_count = max(1, int(getattr(item, "query_hit_count", 1) or 1))
                hit_counts[canonical_key] += incoming_count
                continue

            # 너무 짧거나 의미 없는 노이즈 필터링
            if len(item.text.strip()) < self.min_text_len:
                stats["noise_filtered"] += 1
                continue

            first_items[canonical_key] = item
            incoming_count = max(1, int(getattr(item, "query_hit_count", 1) or 1))
            hit_counts[canonical_key] = incoming_count
            item_order.append(canonical_key)

        # 2. 범용 시계열 및 앵커 관련성 평가 & 결정론적 리랭킹 (Static business keyword boost 전면 배제)
        # 랭킹 입력은 집계된 query_hit_count를 보유한 새로운 클론으로 생성하여 원본 불변성 보장
        ranked_candidates = []
        for idx, key in enumerate(item_order):
            base_item = first_items[key]
            item = base_item.model_copy(update={"query_hit_count": hit_counts[key]})

            # 시계열 평가 (메시지 작성 시각 vs 본문 명시 일자 분리 평가 및 합성)
            combined_rel, ts_rel, mentioned_rel = evaluate_candidate_temporal_relation(
                item, temporal_constraint, temporal_scope=temporal_scope
            )
            item.temporal_relation = combined_rel.value
            item.message_timestamp_relation = ts_rel.value
            item.mentioned_date_relation = mentioned_rel.value

            # 범용 앵커 매칭 (스레드 댓글 포함)
            candidate_anchor_text = item.text
            if getattr(item, "thread_replies", None):
                candidate_anchor_text += "\n" + "\n".join(r.text for r in item.thread_replies)
            matches = compute_anchor_matches(candidate_anchor_text, anchor_terms)
            item.anchor_match_count = matches

            # 정렬 우선순위 계층 계산
            has_temporal_intent = (
                (temporal_scope and temporal_scope.intent_present and temporal_scope.status == TemporalResolutionStatus.RESOLVED)
                or (temporal_constraint and temporal_constraint.has_temporal_intent)
            )
            if has_temporal_intent:
                if combined_rel == TemporalRelation.DIRECT_TEMPORAL_MATCH:
                    temporal_tier = 0
                elif combined_rel == TemporalRelation.POSSIBLE_TEMPORAL_MATCH:
                    temporal_tier = 1
                elif combined_rel == TemporalRelation.TEMPORAL_UNKNOWN:
                    temporal_tier = 2
                else:
                    temporal_tier = 3
            else:
                # 시간 의도가 없는 질문은 모든 후보가 동일 계층 (시간 왜곡 방지)
                temporal_tier = 0

            # 정렬 튜플: (temporal_tier, -anchor_matches, -query_hit_count, original_idx)
            ranked_candidates.append(
                (temporal_tier, -matches, -item.query_hit_count, idx, item)
            )

        ranked_candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        sorted_items = [x[4] for x in ranked_candidates]

        # 3. 최대 개수(최대 5건) 및 글자 수 예산 제한 적용
        selected_items: list[EvidenceItem] = []
        total_chars = 0

        for idx, item in enumerate(sorted_items):
            if len(selected_items) >= self.max_evidence_items:
                break

            item_len = len(item.text)
            if total_chars + item_len > self.max_total_chars and len(selected_items) >= 2:
                break

            canonical_id = f"E{len(selected_items) + 1}"
            reindexed_item = item.model_copy(
                update={"evidence_id": canonical_id}
            )
            selected_items.append(reindexed_item)
            total_chars += item_len

        stats["final_selected"] = len(selected_items)

        # 4. Retrieval Coverage 결정론적 산출
        coverage = RetrievalCoverage.NO_DIRECT_MATCH
        if selected_items:
            anchored_count = sum(1 for it in selected_items if it.anchor_match_count > 0)
            has_temporal = (
                (temporal_scope and temporal_scope.intent_present and temporal_scope.status == TemporalResolutionStatus.RESOLVED)
                or (temporal_constraint and temporal_constraint.has_temporal_intent)
            )
            if has_temporal:
                compatible_count = sum(
                    1
                    for it in selected_items
                    if it.temporal_relation
                    in (
                        TemporalRelation.DIRECT_TEMPORAL_MATCH.value,
                        TemporalRelation.POSSIBLE_TEMPORAL_MATCH.value,
                    )
                )
                if anchored_count >= 1 and compatible_count >= 1:
                    coverage = RetrievalCoverage.STRONG
                elif anchored_count >= 1 or compatible_count >= 1:
                    coverage = RetrievalCoverage.LIMITED
                else:
                    coverage = RetrievalCoverage.NO_DIRECT_MATCH
            else:
                if anchored_count >= 1:
                    coverage = RetrievalCoverage.STRONG
                else:
                    coverage = RetrievalCoverage.LIMITED

        package = EvidencePackage(
            package_id=package_id,
            query=query_summary,
            total_items=len(selected_items),
            created_at=datetime.now(timezone.utc).isoformat(),
            is_synthetic=all(it.source == "synthetic" for it in candidates) if candidates else False,
            evidence_items=selected_items,
            retrieval_coverage=coverage.value,
            temporal_scope=temporal_scope,
            actor_scope=actor_scope,
        )

        logger.info(
            f"📦 [EVIDENCE_SELECTED] initial={stats['initial_candidates']} "
            f"deduped={stats['duplicates_removed']} temporal_filtered={stats['temporal_filtered_out']} "
            f"selected={stats['final_selected']} coverage={coverage.value}"
        )
        return package, stats

