"""Synthetic Evidence Fixtures for Phase 5 Testing and Verification."""

from __future__ import annotations

from pesu_agent.investigation.models import EvidenceItem, EvidencePackage, UserQuestion


def get_synthetic_lead_allocation_fixture() -> tuple[UserQuestion, EvidencePackage]:
    """영업지원 리드 배분 정책 분쟁에 대한 표준 가상(Synthetic) 시나리오 픽스처."""
    question = UserQuestion(
        question="최근 영업지원 리드 배분 정책을 둘러싼 팀 간 분쟁에서 현재 유효한 공식 기준은 무엇이고 누가 더 타당한지 분석해줘.",
        context_hint="영업 1팀(팀장 A), 영업 2팀(팀장 B), 운영지원팀(C), 경영지원팀(D) 참여",
    )

    items = [
        EvidenceItem(
            evidence_id="E1",
            source="synthetic_fixture",
            author="영업1팀_팀장A",
            timestamp="2026-08-10 10:15",
            channel="sales-operations",
            text="7월부터 인입된 대형 인바운드 리드는 당초 협의대로 1팀에 우선 배분되어야 합니다. 최근 2팀으로 임의 분배된 건에 대해 재조정 요청합니다.",
            message_identifier="synth_msg_001",
            has_attachments=False,
            attachment_count=0,
            attachment_names=[],
        ),
        EvidenceItem(
            evidence_id="E2",
            source="synthetic_fixture",
            author="영업2팀_팀장B",
            timestamp="2026-08-10 11:30",
            channel="sales-operations",
            text="기존 영업 운영 규정 제4조에 따르면 지역별 균등 배분이 공식 원칙입니다. 팀 간 구두 논의만으로 기존 배분 기준을 일방 변경할 수 없습니다.",
            message_identifier="synth_msg_002",
            has_attachments=False,
            attachment_count=0,
            attachment_names=[],
        ),
        EvidenceItem(
            evidence_id="E3",
            source="synthetic_fixture",
            author="운영지원_C",
            timestamp="2026-08-11 14:00",
            channel="sales-operations",
            text="관련하여 개정된 운영 규정 문서를 공유드립니다. 3분기 인입 채널별 가중치 배분 기준이 명시되어 있습니다.",
            message_identifier="synth_msg_003",
            has_attachments=True,
            attachment_count=1,
            attachment_names=["2026_영업리드_배분운영정책_개정안_v2.1.pdf"],
        ),
        EvidenceItem(
            evidence_id="E4",
            source="synthetic_fixture",
            author="영업2팀_팀장B",
            timestamp="2026-08-11 15:20",
            channel="sales-operations",
            text="공유해주신 v2.1 개정안은 아직 경영지원팀 최종 결재가 완료되지 않은 초안으로 알고 있습니다. 결재 전까지는 기존 v1.0 균등 배분이 유지되어야 합니다.",
            message_identifier="synth_msg_004",
            has_attachments=False,
            attachment_count=0,
            attachment_names=[],
        ),
        EvidenceItem(
            evidence_id="E5",
            source="synthetic_fixture",
            author="경영지원_D",
            timestamp="2026-08-12 09:10",
            channel="sales-operations",
            text="확인 결과, '2026_영업리드_배분운영정책_개정안_v2.1'은 지난 8월 1일 자로 전자결재(결재번호 2026-HR-0801) 완료되어 8월 1일부터 효력이 발생한 공식 정책입니다.",
            message_identifier="synth_msg_005",
            has_attachments=False,
            attachment_count=0,
            attachment_names=[],
        ),
    ]

    package = EvidencePackage(
        package_id="synth_pkg_sales_lead_001",
        query="영업지원 리드 배분",
        evidence_items=items,
        total_items=len(items),
        created_at="2026-08-21T10:00:00Z",
        is_synthetic=True,
    )

    return question, package


def get_non_synthetic_company_slack_fixture() -> EvidencePackage:
    """Hard Gate 차단 테스트를 위한 실제 회사 데이터 시뮬레이션 픽스처 (is_synthetic = False)."""
    return EvidencePackage(
        package_id="company_pkg_real_001",
        query="실제 회사 기밀 세무 조사",
        evidence_items=[
            EvidenceItem(
                evidence_id="E1",
                source="real_slack_workspace",
                author="회사_실제직원",
                timestamp="2026-08-21 11:00",
                channel="internal-secret",
                text="실제 고객사 세무 신고 데이터 및 내부 매출 현황...",
                message_identifier="real_secret_hash_001",
            )
        ],
        total_items=1,
        created_at="2026-08-21T11:00:00Z",
        is_synthetic=False,
    )
