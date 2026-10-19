"""Tests for present_grounded_investigation_result (Phase 13.5D Task 7).

Verifies deterministic translation of GroundedWorkItemAnswerPayload into
WinForms UI investigation.completed schema with permalink preservation.
"""

from __future__ import annotations

import pytest

from pesu_agent.ui.grounded_answer_builder import (
    GroundedWorkItemAnswerPayload,
    GroundedWorkItemCard,
)
from pesu_agent.ui.result_presenter import (
    MAX_EVIDENCE_CARDS,
    MAX_SNIPPET_LENGTH,
    present_grounded_investigation_result,
)


def _build_sample_payload(
    status: str = "COMPLETED",
    status_label_ko: str = "[완료]",
    decisive_claims: list[str] | None = None,
    blocker_details: list[str] | None = None,
    is_conclusive: bool = True,
    uncertainty_explanation: str | None = None,
    evidence_count: int = 1,
    invalid_permalink: bool = False,
) -> GroundedWorkItemAnswerPayload:
    claims = decisive_claims or ["AuthService v2.4.0 rollout completed"]
    blockers = blocker_details or []

    citations = []
    for i in range(evidence_count):
        link = (
            "http://insecure.com/bad"
            if invalid_permalink
            else f"https://pesu.slack.com/archives/C0123456789/p1724738400{i:06d}"
        )
        citations.append(
            {
                "evidence_id": f"E{i+1}",
                "author": f"user_{i+1}",
                "permalink": link,
                "snippet": f"Verified snippet {i+1}",
                "has_valid_permalink": not invalid_permalink,
            }
        )

    card = GroundedWorkItemCard(
        work_item_id="WI-1",
        title="AuthService v2.4.0 Rollout",
        status=status,
        status_label_ko=status_label_ko,
        decisive_claims=claims,
        blocker_details=blockers,
        evidence_citations=citations,
    )

    md_answer = f"### {status_label_ko} AuthService v2.4.0 Rollout\n\n**확인된 사실:**\n" + "\n".join(f"- {c}" for c in claims)
    if blockers:
        md_answer += "\n\n**차단 요인:**\n" + "\n".join(f"- {b}" for b in blockers)
    if uncertainty_explanation:
        md_answer += f"\n\n**안내:** {uncertainty_explanation}"

    return GroundedWorkItemAnswerPayload(
        question="Rollout status?",
        has_evidence=evidence_count > 0,
        summary_headline=f"{status_label_ko} AuthService v2.4.0 Rollout",
        work_item_cards=[card],
        formatted_markdown_answer=md_answer,
        evidence_count=evidence_count,
        is_conclusive=is_conclusive,
        uncertainty_explanation=uncertainty_explanation,
        stop_reason="COMPLETED" if is_conclusive else "SAFE_UNKNOWN",
    )


def test_completed_render():
    """COMPLETED payload renders correct UI presentation contract with permalinks."""
    payload = _build_sample_payload(
        status="COMPLETED",
        status_label_ko="[완료]",
        is_conclusive=True,
    )

    res = present_grounded_investigation_result("inv-001", payload)

    assert res["investigation_id"] == "inv-001"
    assert res["has_evidence"] is True
    assert res["evidence_count"] == 1
    assert "[완료]" in res["answer"]
    assert "AuthService v2.4.0 rollout completed" in res["answer"]
    assert res["stop_reason"] == "COMPLETED"
    assert len(res["evidence"]) == 1
    evidence_item = res["evidence"][0]
    assert evidence_item["id"] == "E1"
    assert evidence_item["has_valid_permalink"] is True
    assert evidence_item["permalink"] == "https://pesu.slack.com/archives/C0123456789/p1724738400000000"


def test_blocked_render():
    """BLOCKED payload renders '[차단됨]' badge and blocker reasons in answer."""
    payload = _build_sample_payload(
        status="BLOCKED",
        status_label_ko="[차단됨]",
        decisive_claims=["Rollout paused"],
        blocker_details=["Database lock timeout on replica"],
        is_conclusive=True,
    )

    res = present_grounded_investigation_result("inv-002", payload)

    assert "[차단됨]" in res["answer"]
    assert "Database lock timeout on replica" in res["answer"]
    assert res["has_evidence"] is True


def test_unknown_render():
    """UNKNOWN payload renders safe uncertainty explanation without speculation."""
    payload = _build_sample_payload(
        status="UNKNOWN",
        status_label_ko="[확인 불가]",
        is_conclusive=False,
        uncertainty_explanation="현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다.",
    )

    res = present_grounded_investigation_result("inv-003", payload)

    assert "[확인 불가]" in res["answer"]
    assert "확정할 수 없습니다" in res["answer"]
    assert res["stop_reason"] == "SAFE_UNKNOWN"


def test_multiple_evidence_citation_render_and_bounding():
    """Bounds maximum evidence cards to MAX_EVIDENCE_CARDS (5)."""
    payload = _build_sample_payload(
        status="COMPLETED",
        evidence_count=8,  # Exceeds max 5
    )

    res = present_grounded_investigation_result("inv-004", payload)

    assert len(res["evidence"]) == MAX_EVIDENCE_CARDS
    assert res["evidence_count"] == 8  # Telemetry keeps total count
    for item in res["evidence"]:
        assert item["has_valid_permalink"] is True
        assert len(item["snippet"]) <= MAX_SNIPPET_LENGTH + 3


def test_invalid_permalink_sanitization():
    """Invalid or insecure permalinks are filtered to None with has_valid_permalink=False."""
    payload = _build_sample_payload(
        status="COMPLETED",
        invalid_permalink=True,
    )

    res = present_grounded_investigation_result("inv-005", payload)

    assert len(res["evidence"]) == 1
    assert res["evidence"][0]["has_valid_permalink"] is False
    assert res["evidence"][0]["permalink"] is None


def test_no_evidence_payload():
    """Empty payload renders no_evidence_message properly."""
    payload = GroundedWorkItemAnswerPayload(
        question="Empty question?",
        has_evidence=False,
        summary_headline="[확인 불가] Empty question?",
        work_item_cards=[],
        formatted_markdown_answer="### [확인 불가]\n\n현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다.",
        evidence_count=0,
        is_conclusive=False,
        uncertainty_explanation="현재 확보된 근거만으로는 업무 상태를 확정할 수 없습니다.",
        stop_reason="NO_SUPPORTED_CLAIMS",
    )

    res = present_grounded_investigation_result("inv-006", payload)

    assert res["has_evidence"] is False
    assert res["evidence_count"] == 0
    assert res["evidence"] == []
    assert res["no_evidence_message"] is not None
    assert "확정할 수 없습니다" in res["no_evidence_message"] or "구체적으로" in res["no_evidence_message"]


def test_structured_workitems_preserve_reasoning_and_source_references():
    payload = _build_sample_payload(status="BLOCKED", status_label_ko="[차단됨]",
        blocker_details=["승인 대기"], is_conclusive=True)
    result = present_grounded_investigation_result("structured", payload)
    assert result["summary_headline"] == payload.summary_headline
    assert result["is_conclusive"] is True
    assert result["work_items"][0]["status"] == "BLOCKED"
    assert result["work_items"][0]["blocker_details"] == ["승인 대기"]
    assert result["work_items"][0]["evidence_ids"] == ["E1"]


def test_structured_unknown_preserves_uncertainty_for_product_ui():
    payload = _build_sample_payload(status="UNKNOWN", status_label_ko="[확인 불가]",
        is_conclusive=False, uncertainty_explanation="완료 여부에 대한 근거가 상충합니다.")
    result = present_grounded_investigation_result("unknown", payload)
    assert result["is_conclusive"] is False
    assert result["uncertainty_explanation"] == payload.uncertainty_explanation
    assert result["work_items"][0]["status"] == "UNKNOWN"


def test_structured_display_reports_omitted_reasoning():
    payload = _build_sample_payload(decisive_claims=[f"Claim {i}" for i in range(6)])
    result = present_grounded_investigation_result("bounded", payload)
    assert len(result["work_items"][0]["decisive_claims"]) == 5
    assert result["content_truncated"] is True


def test_structured_display_discloses_sources_outside_visible_limit():
    payload = _build_sample_payload(evidence_count=MAX_EVIDENCE_CARDS + 1)
    result = present_grounded_investigation_result("sources-bounded", payload)
    assert len(result["evidence"]) == MAX_EVIDENCE_CARDS
    assert result["work_items"][0]["omitted_evidence_count"] == 1
    assert result["content_truncated"] is True
