"""Tests for deterministic GroundedWorkItemAnswerBuilder (Phase 13.5D Task 6).

Guarantees:
- SEMANTIC_AUTHORITY = 0: Pure deterministic projection engine without re-judging state.
- FREEFORM_LLM_CALLS = 0: Zero external LLM or model generation calls.
- Fail-closed on broken trace or ungrounded facts.
- Strict canonical permalink preservation.
"""

from __future__ import annotations

import pytest

from pesu_agent.investigation.graph_models import (
    ClaimKind,
    ClaimNode,
    ClaimSemanticType,
    EvidenceNode,
    InvestigationStateGraph,
    WorkItemKind,
    WorkItemNode,
    WorkItemStatus,
)
from pesu_agent.investigation.workitem_synthesis import (
    ClaimTraceabilityRecord,
    WorkItemTraceabilityChain,
)
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
    GroundedWorkItemCard,
)


def _build_test_graph_with_trace(
    status: WorkItemStatus = WorkItemStatus.COMPLETED,
    claim_statement: str = "AuthService v2.4.0 rollout completed",
    is_traceable: bool = True,
    blocker_statement: str | None = None,
    multiple_evidence: bool = False,
) -> tuple[InvestigationStateGraph, dict[str, WorkItemNode], dict[str, WorkItemTraceabilityChain]]:
    graph = InvestigationStateGraph(question="Rollout status?")

    e1 = EvidenceNode(
        evidence_id="E1",
        text="Release v2.4.0 deployed successfully to prod.",
        presentation_author="alice",
        timestamp="2026-09-14T10:00:00Z",
        canonical_permalink="https://slack.com/archives/C123/p10001",
    )
    graph.add_evidence(e1)

    citations = [
        {
            "evidence_id": "E1",
            "author": "alice",
            "permalink": "https://slack.com/archives/C123/p10001",
            "snippet": "Release v2.4.0 deployed successfully to prod.",
        }
    ]
    evidence_ids = ["E1"]

    if multiple_evidence:
        e2 = EvidenceNode(
            evidence_id="E2",
            text="Health checks verified green across all pods.",
            presentation_author="bob",
            timestamp="2026-09-14T10:05:00Z",
            canonical_permalink="https://slack.com/archives/C123/p10002",
        )
        graph.add_evidence(e2)
        citations.append(
            {
                "evidence_id": "E2",
                "author": "bob",
                "permalink": "https://slack.com/archives/C123/p10002",
                "snippet": "Health checks verified green across all pods.",
            }
        )
        evidence_ids.append("E2")

    c1 = ClaimNode(
        claim_id="C1",
        statement=claim_statement,
        kind=ClaimKind.FACT,
        semantic_type=ClaimSemanticType.OUTCOME,
    )
    graph.add_claim(c1)

    wi = WorkItemNode(
        work_item_id="WI-1",
        title="AuthService v2.4.0 Rollout",
        kind=WorkItemKind.TASK,
        status=status,
    )
    graph.add_work_item(wi)

    claim_traces = [
        ClaimTraceabilityRecord(
            claim_id="C1",
            statement=claim_statement,
            evidence_ids=evidence_ids,
            citations=citations,
        )
    ]
    governing_claims = ["C1"]

    if blocker_statement:
        c_block = ClaimNode(
            claim_id="C_BLOCK",
            statement=blocker_statement,
            kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.STATE,
        )
        graph.add_claim(c_block)
        e_block = EvidenceNode(
            evidence_id="E_BLOCK",
            text=f"Blocked: {blocker_statement}",
            presentation_author="charlie",
            canonical_permalink="https://slack.com/archives/C123/p10003",
        )
        graph.add_evidence(e_block)
        claim_traces.append(
            ClaimTraceabilityRecord(
                claim_id="C_BLOCK",
                statement=blocker_statement,
                evidence_ids=["E_BLOCK"],
                citations=[
                    {
                        "evidence_id": "E_BLOCK",
                        "author": "charlie",
                        "permalink": "https://slack.com/archives/C123/p10003",
                        "snippet": f"Blocked: {blocker_statement}",
                    }
                ],
            )
        )
        governing_claims.append("C_BLOCK")

    chain = WorkItemTraceabilityChain(
        work_item_id="WI-1",
        is_traceable=is_traceable,
        final_status=status,
        governing_claim_ids=governing_claims,
        claim_traces=claim_traces,
        traceability_error=None if is_traceable else "Broken link to evidence",
    )

    return graph, {"WI-1": wi}, {"WI-1": chain}


def test_completed_workitem_renders_completed_badge_and_facts():
    """COMPLETED WorkItem renders '[완료]' badge, decisive facts, and canonical permalink."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.COMPLETED,
        claim_statement="AuthService v2.4.0 rollout completed",
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
    )

    assert isinstance(payload, GroundedWorkItemAnswerPayload)
    assert payload.has_evidence is True
    assert payload.is_conclusive is True
    assert len(payload.work_item_cards) == 1

    card = payload.work_item_cards[0]
    assert card.status == "COMPLETED"
    assert card.status_label_ko == "[완료]"
    assert "AuthService v2.4.0 rollout completed" in card.decisive_claims
    assert len(card.evidence_citations) == 1
    assert card.evidence_citations[0]["permalink"] == "https://slack.com/archives/C123/p10001"

    # Formatted markdown text checks
    assert "[완료]" in payload.formatted_markdown_answer
    assert "AuthService v2.4.0 rollout completed" in payload.formatted_markdown_answer
    assert "https://slack.com/archives/C123/p10001" in payload.formatted_markdown_answer
    assert GroundedWorkItemAnswerBuilder.FREEFORM_LLM_CALLS == 0


def test_in_progress_workitem_renders_in_progress_badge():
    """IN_PROGRESS WorkItem renders '[진행 중]' badge without hallucinating completion."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.IN_PROGRESS,
        claim_statement="Canary rollout at 25% stage",
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
    )

    assert payload.is_conclusive is True
    card = payload.work_item_cards[0]
    assert card.status == "IN_PROGRESS"
    assert card.status_label_ko == "[진행 중]"
    assert "Canary rollout at 25% stage" in card.decisive_claims
    assert "[진행 중]" in payload.formatted_markdown_answer


def test_blocked_workitem_renders_blocked_badge_and_blocker_reason():
    """BLOCKED WorkItem renders '[차단됨]' badge and explicitly lists grounded blocker details."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.BLOCKED,
        claim_statement="Deployment halted",
        blocker_statement="Database migration failure on staging",
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
    )

    card = payload.work_item_cards[0]
    assert card.status == "BLOCKED"
    assert card.status_label_ko == "[차단됨]"
    assert len(card.blocker_details) >= 1
    assert "Database migration failure on staging" in card.blocker_details
    assert "[차단됨]" in payload.formatted_markdown_answer
    assert "Database migration failure on staging" in payload.formatted_markdown_answer


def test_unknown_renders_non_speculative_explanation():
    """UNKNOWN status renders '[확인 불가]' with safe explanation and zero guessing."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.UNKNOWN,
        claim_statement="Unresolved status",
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
        stop_reason="SAFE_UNKNOWN",
    )

    assert payload.is_conclusive is False
    card = payload.work_item_cards[0]
    assert card.status == "UNKNOWN"
    assert card.status_label_ko == "[확인 불가]"
    assert payload.uncertainty_explanation is not None
    assert "확정할 수 없습니다" in payload.uncertainty_explanation or "불확실" in payload.uncertainty_explanation


def test_untraceable_chain_fails_closed():
    """Traceability chain marked is_traceable=False fails closed to safe UNKNOWN/unverified."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.COMPLETED,
        is_traceable=False,
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
    )

    assert payload.is_conclusive is False
    assert len(payload.work_item_cards) == 1
    card = payload.work_item_cards[0]
    # Broken trace must NOT be presented as a verified completed status
    assert card.status == "UNKNOWN"
    assert card.status_label_ko == "[확인 불가]"
    assert "추적" in str(payload.uncertainty_explanation) or "불완전" in str(payload.uncertainty_explanation)


def test_multiple_evidence_citations_and_canonical_permalinks():
    """All evidence citations preserve canonical Slack permalinks and authors."""
    graph, work_items, chains = _build_test_graph_with_trace(
        status=WorkItemStatus.COMPLETED,
        multiple_evidence=True,
    )

    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Rollout status?",
        graph=graph,
        work_items=work_items,
        traceability_chains=chains,
    )

    assert payload.evidence_count == 2
    card = payload.work_item_cards[0]
    assert len(card.evidence_citations) == 2
    permalinks = {c["permalink"] for c in card.evidence_citations}
    assert "https://slack.com/archives/C123/p10001" in permalinks
    assert "https://slack.com/archives/C123/p10002" in permalinks
    authors = {c["author"] for c in card.evidence_citations}
    assert "alice" in authors
    assert "bob" in authors


def test_zero_external_model_calls_and_semantic_authority():
    """Asserts that GroundedWorkItemAnswerBuilder makes zero LLM calls and has zero semantic authority."""
    assert GroundedWorkItemAnswerBuilder.SEMANTIC_AUTHORITY == 0
    assert GroundedWorkItemAnswerBuilder.FREEFORM_LLM_CALLS == 0


def test_empty_work_items_returns_clean_non_speculative_payload():
    """When work_items are empty, returns safe fail-closed answer with zero speculation."""
    graph = InvestigationStateGraph(question="Any updates?")
    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Any updates?",
        graph=graph,
        work_items={},
        traceability_chains={},
        stop_reason="NO_SUPPORTED_CLAIMS",
    )

    assert payload.has_evidence is False
    assert payload.is_conclusive is False
    assert payload.work_item_cards == []
    assert "확인할 수 없습니다" in payload.formatted_markdown_answer or "확정할 수 없습니다" in payload.formatted_markdown_answer


def test_inference_is_not_presented_as_confirmed_fact():
    graph, work_items, chains = _build_test_graph_with_trace()
    graph.claim_nodes["C1"].kind = ClaimKind.INFERENCE
    answer = GroundedWorkItemAnswerBuilder.build_answer(question="Rollout status?",
        graph=graph, work_items=work_items, traceability_chains=chains)
    assert answer.work_item_cards[0].findings[0]["label"] == "추론"
    assert "**확인된 사실:**" not in answer.formatted_markdown_answer
