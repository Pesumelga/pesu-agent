"""TDD Contract Test for Grounded Answer Payload and Harness Reporting.

Verifies:
1. The bug: Diagnostic harness extract_grounded_answer_report accesses .grounded_summary
   on GroundedWorkItemAnswerPayload, raising AttributeError.
2. The fix: When fixed, extract_grounded_answer_report extracts formatted_markdown_answer
   and summary_headline from the canonical declared schema.
3. Builder contract: build_answer requires work_items: dict[str, WorkItemNode], not a list.
4. Schema integrity: GroundedWorkItemAnswerPayload must NOT have compatibility aliases added.
"""

import pytest
from scripts.diagnose_task11r7_full_production_pipeline import (
    extract_grounded_answer_report,
)
from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerBuilder,
    GroundedWorkItemAnswerPayload,
    GroundedWorkItemCard,
)
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemNode,
    WorkItemKind,
    WorkItemStatus,
    WorkItemCategory,
)


def test_harness_grounded_answer_reporting_contract():
    """Asserts that extract_grounded_answer_report successfully serializes
    a valid GroundedWorkItemAnswerPayload.
    
    MUST FAIL in the buggy harness because it accesses .grounded_summary.
    """
    card = GroundedWorkItemCard(
        work_item_id="WI-1",
        title="1차 배포",
        status="COMPLETED",
        status_label_ko="[완료]",
        decisive_claims=["선제적으로 정의한 기준 기반 1차 배포하였습니다"],
        blocker_details=[],
        evidence_citations=[],
    )
    payload = GroundedWorkItemAnswerPayload(
        question="고객 만족도 현황 페이지는 1차 배포가 완료된 상태인가?",
        has_evidence=True,
        summary_headline="[완료] 1차 배포",
        work_item_cards=[card],
        formatted_markdown_answer="### [완료] 1차 배포\n\n**확인된 사실:**\n- 선제적으로 정의한 기준 기반 1차 배포하였습니다",
        evidence_count=1,
        is_conclusive=True,
        uncertainty_explanation=None,
        stop_reason="COMPLETED",
    )

    # In buggy harness, this raises AttributeError: 'GroundedWorkItemAnswerPayload' object has no attribute 'grounded_summary'
    report = extract_grounded_answer_report(payload)

    assert isinstance(report, dict)
    assert report.get("summary") is not None
    assert report.get("summary_headline") == "[완료] 1차 배포"
    assert report.get("formatted_markdown_answer") is not None
    assert report.get("is_conclusive") is True
    assert report.get("cards_count") == 1


def test_build_answer_requires_dict_not_list():
    """Verifies that GroundedWorkItemAnswerBuilder expects dict[str, WorkItemNode].
    
    Passing a list must raise AttributeError (because build_answer calls work_items.items()).
    """
    graph = InvestigationStateGraph()
    wi = WorkItemNode(
        work_item_id="WI-1",
        title="Test WI",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.COMPLETED,
        category=WorkItemCategory.WORK_ITEM,
    )

    # 1. Passing a list (what buggy harness did) fails
    with pytest.raises(AttributeError, match="'list' object has no attribute 'items'"):
        GroundedWorkItemAnswerBuilder.build_answer(
            question="Status?",
            graph=graph,
            work_items=[wi],  # type: ignore[arg-type]
            traceability_chains={},
        )

    # 2. Passing a dict (canonical production contract) succeeds
    payload = GroundedWorkItemAnswerBuilder.build_answer(
        question="Status?",
        graph=graph,
        work_items={"WI-1": wi},
        traceability_chains={},
    )
    assert isinstance(payload, GroundedWorkItemAnswerPayload)
    assert payload.question == "Status?"


def test_production_answer_payload_has_no_alias():
    """Production contract guard: GroundedWorkItemAnswerPayload must not be polluted with aliases."""
    declared_fields = set(GroundedWorkItemAnswerPayload.model_fields.keys())
    assert "grounded_summary" not in declared_fields, (
        "Forbidden: grounded_summary alias must NOT be added to production GroundedWorkItemAnswerPayload"
    )
    assert declared_fields == {
        "question",
        "has_evidence",
        "summary_headline",
        "work_item_cards",
        "formatted_markdown_answer",
        "evidence_count",
        "is_conclusive",
        "uncertainty_explanation",
        "stop_reason",
    }
