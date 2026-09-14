"""Deterministic Grounded WorkItem Answer Builder (Phase 13.5D Task 6).

Pure deterministic projection engine transforming WorkItemTraceabilityChain
and WorkItemNodes into verified user-facing grounded text and citation cards.

Invariants:
- SEMANTIC_AUTHORITY = 0: Strictly reflects validated graph state; zero re-judging.
- FREEFORM_LLM_CALLS = 0: Zero external LLM or generative model invocations.
- EVERY_SUBSTANTIVE_OUTPUT_TRACEABLE = True: Facts must originate from validated claims.
- UNKNOWN_FAIL_CLOSED = True: Broken trace or missing evidence fails closed to safe UNKNOWN.
- SAFE_PERMALINK_CONTRACT: Preserves canonical HTTPS Slack permalinks.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemNode,
    WorkItemStatus,
)
from pesu_agent.investigation.workitem_synthesis import (
    ClaimTraceabilityRecord,
    WorkItemTraceabilityChain,
)
from pesu_agent.ui.result_presenter import validate_citation_permalink

logger = logging.getLogger(__name__)

STATUS_LABEL_MAP: dict[WorkItemStatus | str, tuple[str, str]] = {
    WorkItemStatus.COMPLETED: ("[완료]", "COMPLETED"),
    WorkItemStatus.IN_PROGRESS: ("[진행 중]", "IN_PROGRESS"),
    WorkItemStatus.BLOCKED: ("[차단됨]", "BLOCKED"),
    WorkItemStatus.PLANNED: ("[계획됨]", "PLANNED"),
    WorkItemStatus.UNKNOWN: ("[확인 불가]", "UNKNOWN"),
    "COMPLETED": ("[완료]", "COMPLETED"),
    "IN_PROGRESS": ("[진행 중]", "IN_PROGRESS"),
    "BLOCKED": ("[차단됨]", "BLOCKED"),
    "PLANNED": ("[계획됨]", "PLANNED"),
    "UNKNOWN": ("[확인 불가]", "UNKNOWN"),
}


class GroundedWorkItemCard(BaseModel):
    """User-facing structured summary card for a single WorkItem."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    work_item_id: str
    title: str
    status: str  # "COMPLETED" | "IN_PROGRESS" | "BLOCKED" | "PLANNED" | "UNKNOWN"
    status_label_ko: str  # "[완료]" | "[진행 중]" | "[차단됨]" | "[계획됨]" | "[확인 불가]"
    decisive_claims: list[str] = Field(default_factory=list)
    blocker_details: list[str] = Field(default_factory=list)
    evidence_citations: list[dict[str, Any]] = Field(default_factory=list)


class GroundedWorkItemAnswerPayload(BaseModel):
    """Structured, verified grounded answer data ready for UI presentation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    question: str
    has_evidence: bool
    summary_headline: str
    work_item_cards: list[GroundedWorkItemCard] = Field(default_factory=list)
    formatted_markdown_answer: str
    evidence_count: int
    is_conclusive: bool
    uncertainty_explanation: Optional[str] = None
    stop_reason: str = "COMPLETED"


class GroundedWorkItemAnswerBuilder:
    """Pure deterministic projection engine transforming WorkItemTraceabilityChain

    and WorkItemNodes into verified user-facing grounded text and citation cards.
    Semantic authority = ZERO (strictly reflects validated graph state).
    """

    SEMANTIC_AUTHORITY: int = 0
    FREEFORM_LLM_CALLS: int = 0

    @classmethod
    def build_answer(
        cls,
        question: str,
        graph: InvestigationStateGraph,
        work_items: dict[str, WorkItemNode],
        traceability_chains: dict[str, WorkItemTraceabilityChain],
        stop_reason: Optional[str] = None,
    ) -> GroundedWorkItemAnswerPayload:
        """Deterministically projects graph state into a GroundedWorkItemAnswerPayload."""
        effective_stop_reason = stop_reason or "COMPLETED"

        # Step 1: Handle empty work items (no evidence or no claims)
        if not work_items:
            uncertainty_text = cls._resolve_empty_uncertainty_explanation(effective_stop_reason)
            headline = f"[확인 불가] {question}"
            return GroundedWorkItemAnswerPayload(
                question=question,
                has_evidence=False,
                summary_headline=headline,
                work_item_cards=[],
                formatted_markdown_answer=f"### [확인 불가]\n\n{uncertainty_text}",
                evidence_count=0,
                is_conclusive=False,
                uncertainty_explanation=uncertainty_text,
                stop_reason=effective_stop_reason,
            )

        # Step 2: Build cards for each WorkItem
        cards: list[GroundedWorkItemCard] = []
        all_citation_permalinks: set[str] = set()
        has_any_untraceable = False
        has_any_unknown_status = False

        for wid, wi in work_items.items():
            chain = traceability_chains.get(wid)

            # Traceability gate: if chain is missing or marked untraceable -> fail closed
            if chain is None or not chain.is_traceable:
                has_any_untraceable = True
                status_str = "UNKNOWN"
                status_label = "[확인 불가]"
            else:
                raw_status = chain.final_status or wi.status
                status_label, status_str = STATUS_LABEL_MAP.get(
                    raw_status, ("[확인 불가]", "UNKNOWN")
                )

            if status_str == "UNKNOWN":
                has_any_unknown_status = True

            # Extract decisive claims and blocker details
            decisive_claims: list[str] = []
            blocker_details: list[str] = []
            seen_claims: set[str] = set()

            if chain is not None and chain.claim_traces:
                for trace in chain.claim_traces:
                    statement = trace.statement.strip() if trace.statement else ""
                    if not statement and trace.claim_id in graph.claim_nodes:
                        statement = graph.claim_nodes[trace.claim_id].statement.strip()

                    if statement and statement not in seen_claims:
                        seen_claims.add(statement)
                        decisive_claims.append(statement)

                    # Check for explicit blocker signal or relation
                    signal_str = str(getattr(trace, "lifecycle_signal", "") or "")
                    if "BLOCKER" in signal_str or status_str == "BLOCKED":
                        if statement and statement not in blocker_details:
                            blocker_details.append(statement)

            elif chain is not None and chain.governing_claim_ids:
                for cid in chain.governing_claim_ids:
                    if cid in graph.claim_nodes:
                        statement = graph.claim_nodes[cid].statement.strip()
                        if statement and statement not in seen_claims:
                            seen_claims.add(statement)
                            decisive_claims.append(statement)
                            if status_str == "BLOCKED":
                                blocker_details.append(statement)

            # If BLOCKED status but no specific blocker detail found, fallback to decisive claims
            if status_str == "BLOCKED" and not blocker_details and decisive_claims:
                blocker_details = list(decisive_claims)

            # Extract evidence citations
            evidence_citations: list[dict[str, Any]] = []
            seen_evidence_ids: set[str] = set()

            if chain is not None and chain.claim_traces:
                for trace in chain.claim_traces:
                    for cit in trace.citations:
                        eid = cit.get("evidence_id")
                        if eid and eid not in seen_evidence_ids:
                            seen_evidence_ids.add(eid)
                            # Verify if evidence node has richer fields in graph
                            enode = graph.evidence_nodes.get(eid)
                            author = cit.get("author") or (enode.presentation_author if enode else None) or "알 수 없음"
                            permalink = cit.get("permalink") or (enode.canonical_permalink if enode else None)
                            snippet = cit.get("snippet") or (enode.text[:300] if enode else "")

                            card_cit = {
                                "evidence_id": eid,
                                "author": author,
                                "permalink": permalink,
                                "snippet": snippet,
                                "has_valid_permalink": validate_citation_permalink(permalink),
                            }
                            evidence_citations.append(card_cit)
                            if permalink:
                                all_citation_permalinks.add(permalink)

            cards.append(
                GroundedWorkItemCard(
                    work_item_id=wid,
                    title=wi.title or "업무 항목",
                    status=status_str,
                    status_label_ko=status_label,
                    decisive_claims=decisive_claims,
                    blocker_details=blocker_details,
                    evidence_citations=evidence_citations,
                )
            )

        # Step 3: Determine overall conclusiveness & headline
        is_conclusive = (
            not has_any_untraceable
            and not has_any_unknown_status
            and effective_stop_reason == "COMPLETED"
            and len(cards) > 0
        )

        uncertainty_explanation: Optional[str] = None
        if not is_conclusive:
            if has_any_untraceable or effective_stop_reason == "TRACEABILITY_FAILURE":
                uncertainty_explanation = (
                    "근거 추적 경로가 불완전하여 업무 상태를 검증할 수 없습니다."
                )
            elif effective_stop_reason == "PROVIDER_EXECUTION_FAILURE":
                uncertainty_explanation = (
                    "조사 실행 중 모델 제공자 오류가 발생하여 업무 상태를 확인할 수 없습니다."
                )
            elif effective_stop_reason == "INVALID_PROVIDER_OUTPUT":
                uncertainty_explanation = (
                    "근거 분석 중 유효하지 않은 모델 출력이 발생하여 안전하게 중단되었습니다."
                )
            elif effective_stop_reason == "SAFE_UNKNOWN" or has_any_unknown_status:
                uncertainty_explanation = (
                    "현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다."
                )
            else:
                uncertainty_explanation = (
                    "현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다."
                )

        primary_card = cards[0] if cards else None
        if primary_card and is_conclusive:
            summary_headline = f"{primary_card.status_label_ko} {primary_card.title}"
        elif primary_card and not is_conclusive:
            summary_headline = f"[확인 불가] {primary_card.title}"
        else:
            summary_headline = f"[확인 불가] {question}"

        # Step 4: Build deterministic Markdown answer
        md_parts: list[str] = []
        for card in cards:
            md_parts.append(f"### {card.status_label_ko} {card.title}")

            if card.decisive_claims:
                facts_list = "\n".join(f"- {c}" for c in card.decisive_claims)
                md_parts.append(f"**확인된 사실:**\n{facts_list}")

            if card.blocker_details:
                blockers_list = "\n".join(f"- {b}" for b in card.blocker_details)
                md_parts.append(f"**차단 요인:**\n{blockers_list}")

            if card.status == "UNKNOWN" or not is_conclusive:
                md_parts.append(f"**안내:** {uncertainty_explanation}")

            if card.evidence_citations:
                cit_lines: list[str] = []
                for c in card.evidence_citations:
                    author_str = f"[{c['author']}]" if c.get("author") else ""
                    link_str = f" ({c['permalink']})" if c.get("permalink") else ""
                    cit_lines.append(f"- {author_str} {c.get('snippet', '')}{link_str}")
                if cit_lines:
                    md_parts.append(f"**참조 근거:**\n" + "\n".join(cit_lines))

        formatted_markdown_answer = "\n\n".join(md_parts)

        # Count distinct evidence citations across all cards
        distinct_eids = {
            c["evidence_id"]
            for card in cards
            for c in card.evidence_citations
            if c.get("evidence_id")
        }

        return GroundedWorkItemAnswerPayload(
            question=question,
            has_evidence=len(distinct_eids) > 0,
            summary_headline=summary_headline,
            work_item_cards=cards,
            formatted_markdown_answer=formatted_markdown_answer,
            evidence_count=len(distinct_eids),
            is_conclusive=is_conclusive,
            uncertainty_explanation=uncertainty_explanation,
            stop_reason=effective_stop_reason,
        )

    @classmethod
    def _resolve_empty_uncertainty_explanation(cls, stop_reason: str) -> str:
        if stop_reason == "PROVIDER_EXECUTION_FAILURE":
            return "조사 실행 중 모델 제공자 오류가 발생하여 업무 상태를 확인할 수 없습니다."
        if stop_reason in ("INVALID_PROVIDER_OUTPUT", "TRACEABILITY_FAILURE"):
            return "근거 분석 및 추적 검증에 실패하여 안전한 결론을 내릴 수 없습니다."
        return "현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다."
