"""6 Standard End-to-End Synthetic Investigation Cases (Cases A ~ F)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pesu_agent.investigation.models import EvidenceItem, UserQuestion


class SyntheticE2ECase(BaseModel):
    """End-to-End 가상 조사 검증용 케이스 정의."""

    case_id: str
    case_name: str
    description: str
    question: UserQuestion
    corpus_items: list[EvidenceItem]
    expected_stop_reason: str
    expected_rounds: int
    expected_inconclusive: bool = False


def get_all_synthetic_e2e_cases() -> list[SyntheticE2ECase]:
    """6대 핵심 패턴 End-to-End 테스트 케이스 목록을 반환합니다."""
    cases: list[SyntheticE2ECase] = []

    # =========================================================================
    # Case A: 한 번 검색으로 충분한 단순 사건 (Single-Round Sufficient)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-A",
            case_name="PG 수수료율 공식 인하 승인 확인",
            description="1회 검색으로 결재 공지 및 적용 일자가 완벽히 확인되어 1라운드만에 종료되는 케이스",
            question=UserQuestion(
                question="신규 결제 PG사 수수료율 인하안의 최종 승인 여부와 적용 일자를 확인해줘.",
                context_hint="PG사 수수료, 재무팀장 공지",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_a_01",
                    source="synthetic",
                    author="재무팀장",
                    timestamp="2026-08-08 11:45",
                    channel="general",
                    text="[공지] PG사 신규 수수료율(1.8% -> 1.4%) 인하안 전자결재 완료되어 9월 1일부터 전 지점 적용됩니다.",
                    message_identifier="syn_a_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_a_02",
                    source="synthetic",
                    author="직원1",
                    timestamp="2026-08-08 11:50",
                    channel="general",
                    text="수수료 인하 공지 확인했습니다. 감사합니다.",
                    message_identifier="syn_a_02_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=1,
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case B: 최초 검색 부재 -> Follow-up 검색으로 해결 (Multi-Round Discovery)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-B",
            case_name="마이그레이션 스펙 변경 최종 합의 추적",
            description="1차 검색에서는 초기 요청/제안만 발견되어 2차 Follow-up 검색을 통해 최종 합의문 및 배포 완료 증거를 찾아 완결되는 케이스",
            question=UserQuestion(
                question="담당자 A가 요청한 자동 마이그레이션 기능이 최종 스펙 문서에 합의되어 구현된 것인지 확인해줘.",
                context_hint="담당자 A(기획), 담당자 B(개발), 시스템팀",
            ),
            corpus_items=[
                # 1차 일반 검색 시 매칭되는 항목 (초기 요구 및 협의 제안)
                EvidenceItem(
                    evidence_id="syn_b_01",
                    source="synthetic",
                    author="담당자 A",
                    timestamp="2026-08-01 10:00",
                    channel="project-migration",
                    text="고객사 과거 3개년 데이터 일괄 자동 마이그레이션 기능을 이번 릴리즈에 필수로 포함 요청합니다.",
                    message_identifier="syn_b_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_b_02",
                    source="synthetic",
                    author="담당자 B",
                    timestamp="2026-08-02 14:00",
                    channel="project-migration",
                    text="시스템 부하 검토 결과 3개년 일괄 처리는 장애 위험이 큽니다. 최근 1개년 우선 자동화하고 2개년은 요청 시 수동 배치로 축소 협의를 제안합니다.",
                    message_identifier="syn_b_02_ident",
                ),
                # 2차 Follow-up 검색 시 매칭되는 핵심 최종 승인 및 배포 항목
                EvidenceItem(
                    evidence_id="syn_b_03",
                    source="synthetic",
                    author="담당자 A",
                    timestamp="2026-08-03 11:00",
                    channel="project-migration",
                    text="개발팀 의견 수용합니다. '1개년 자동 + 2개년 수동 배치'로 최종 스펙 문서 합의 완료합니다.",
                    message_identifier="syn_b_03_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_b_04",
                    source="synthetic",
                    author="담당자 B",
                    timestamp="2026-08-10 17:00",
                    channel="project-migration",
                    text="최종 합의된 스펙 문서대로 '1개년 자동 마이그레이션 및 2개년 온디맨드 배치' 개발 및 운영 릴리즈 배포 완료했습니다.",
                    message_identifier="syn_b_04_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=1, # Initial plan covers all queries
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case C: 상충하는 양측 증거가 존재하는 모순 탐지 사건 (Contradiction Case)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-C",
            case_name="서버 DB 백업 보고 모순 탐지",
            description="동일 담당자의 과거 백업 성공 보고와 사후 백업 미수행 진술 간의 모순을 탐지하는 케이스",
            question=UserQuestion(
                question="담당자 A의 이전 보고 내용과 이번 장애 발생 시 발언 사이에 모순이 존재하는지 조사해줘.",
                context_hint="담당자 A, 7월 정기 DB 백업, 스토리지 용량",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_c_01",
                    source="synthetic",
                    author="시스템팀_담당자A",
                    timestamp="2026-08-01 18:00",
                    channel="infra-status",
                    text="금일 7월 정기 DB 풀 백업 스케줄 정상 수행 완료되었으며 스토리지 용량 90% 확보되어 이상 없습니다.",
                    message_identifier="syn_c_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_c_02",
                    source="synthetic",
                    author="시스템팀_담당자A",
                    timestamp="2026-08-08 09:30",
                    channel="infra-status",
                    text="확인해보니 8월 1일 당시 스토리지 용량 부족 에러로 풀 백업이 실제로는 돌지 않았습니다.",
                    message_identifier="syn_c_02_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=1,
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case D: 많은 노이즈 속에서 핵심 Evidence 필터링 (High-Noise Extraction)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-D",
            case_name="노이즈 대화 속 VIP SLA 위반 기록 추출",
            description="일상 잡담과 타 업무 문의 노이즈 속에서 지점 X의 6시간 30분 지연 콜백 증거를 정확히 선별하는 케이스",
            question=UserQuestion(
                question="지점 X의 VIP 고객 응대 과정이 공식 콜백 SLA 규정을 준수했는지 확인해줘.",
                context_hint="운영 가이드 제5조, 지점 X, VIP 고객 102번",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_d_n1",
                    source="synthetic",
                    author="직원1",
                    timestamp="2026-08-06 09:10",
                    channel="branch-x-ops",
                    text="오늘 본사 정기 교육 링크가 어디 있나요?",
                    message_identifier="syn_d_n1_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_d_01",
                    source="synthetic",
                    author="운영본부장",
                    timestamp="2026-08-01 09:00",
                    channel="policy-notices",
                    text="공식 운영 가이드 제5조: 지점 X 영업시간(평일 09:00~18:00) 기준, VIP 고객 인입 건은 영업시간 2시간 이내 1차 콜백 및 이력 등록이 필수 규정입니다.",
                    message_identifier="syn_d_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_d_n2",
                    source="synthetic",
                    author="직원2",
                    timestamp="2026-08-06 09:30",
                    channel="branch-x-ops",
                    text="간식 창고에 원두 커피 리필해 두었습니다.",
                    message_identifier="syn_d_n2_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_d_02",
                    source="synthetic",
                    author="지점X_상담원",
                    timestamp="2026-08-06 10:00",
                    channel="branch-x-ops",
                    text="VIP 고객(고객번호 102) 긴급 세무 질의 인입되었습니다.",
                    message_identifier="syn_d_02_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_d_03",
                    source="synthetic",
                    author="지점X_상담원",
                    timestamp="2026-08-06 16:30",
                    channel="branch-x-ops",
                    text="VIP 고객 102번 콜백 완료 및 상담 일지 등록했습니다 (인입 후 6시간 30분 경과).",
                    message_identifier="syn_d_03_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=1,
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case E: 첨부 파일 메타데이터 검증 (Attachment Context Case)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-E",
            case_name="첨부 파일 버전 rev3.0 검증",
            description="첨부된 PDF 파일명을 통해 정산 가이드라인 개정 버전(v3.0) 적용 여부를 확정하는 케이스",
            question=UserQuestion(
                question="운영팀에서 8월에 공유한 정산 정책이 이전 버전(v2.0)인지 개정 버전(v3.0)인지 첨부 문서를 근거로 판별해줘.",
                context_hint="정산 가이드라인, 운영팀 정산담당, 첨부 파일",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_e_01",
                    source="synthetic",
                    author="운영팀_정산담당",
                    timestamp="2026-08-09 10:30",
                    channel="settlement-ops",
                    text="8월 1일부로 개정된 최신 가이드라인 첨부하여 공유드립니다. 이번 정산부터 적용됩니다.",
                    message_identifier="syn_e_01_ident",
                    has_attachments=True,
                    attachment_count=1,
                    attachment_names=["2026_하반기_정산가이드라인_rev3.0_최종.pdf"],
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=1,
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case F: 2라운드 검색 후에도 증거 부족 -> INCONCLUSIVE 종료 (Inconclusive Case)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-F",
            case_name="구두 위약금 감면 약속 증거 부족 완결",
            description="2라운드 검색을 수행해도 상반된 구두 진술 외 공식 품의서나 결재 기록이 없어 ANALYSIS_INCONCLUSIVE로 안전하게 종료되는 케이스",
            question=UserQuestion(
                question="고객사에 위약금 50% 감면을 구두 약속한 책임자가 누구인지 명확히 판정해줘.",
                context_hint="영업담당자 A, 운영담당자 B, 위약금 감면",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_f_01",
                    source="synthetic",
                    author="영업담당자_A",
                    timestamp="2026-08-07 11:00",
                    channel="contracts-dispute",
                    text="저는 위약금 감면 권한이 없어서 운영팀 B님께 문의드렸고 B님이 감면 가능하다고 하여 안내했을 뿐입니다.",
                    message_identifier="syn_f_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_f_02",
                    source="synthetic",
                    author="운영담당자_B",
                    timestamp="2026-08-07 11:40",
                    channel="contracts-dispute",
                    text="저는 일반적인 절차만 설명했을 뿐 감면 확정 약속을 한 적이 전혀 없습니다. 공식 품의서도 올라오지 않았습니다.",
                    message_identifier="syn_f_02_ident",
                ),
            ],
            expected_stop_reason="ANALYSIS_INCONCLUSIVE",
            expected_rounds=2,
            expected_inconclusive=True,
        )
    )

    # =========================================================================
    # Case G: Round 1 단서(서브프로젝트 코드) 기반 Round 2 심층 발굴 완결 (Multi-Round Clue Tracking)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-G",
            case_name="인프라 전환 서브프로젝트 단서 추적 및 보안 적합성 통과 확인",
            description="1라운드에서는 서브프로젝트 코드 분리 사실만 검색되고, 해당 단서로 2라운드 검색하여 최종 승인 공지를 발굴하는 다회차 케이스",
            question=UserQuestion(
                question="2026년도 상반기 클라우드 인프라 전환 프로젝트에서 보안 적합성 심의가 최종 통과 승인되었는지 확인해줘.",
                context_hint="클라우드 인프라 전환 프로젝트, 보안 적합성 심의",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_g_01",
                    source="synthetic",
                    author="인프라PM",
                    timestamp="2026-06-10 10:00",
                    channel="infra-migration",
                    text="2026년 상반기 클라우드 인프라 전환 프로젝트의 보안성 심의 안건은 전사 관리코드 'SEC-ORION-99'로 채번되어 비공개 채널 'security-audit'로 이관되었습니다. 최종 심의 승인 결과는 security-audit 채널 또는 'SEC-ORION-99' 코드로 조회해 주시기 바랍니다.",
                    message_identifier="syn_g_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_g_02",
                    source="synthetic",
                    author="보안감사팀장",
                    timestamp="2026-06-25 16:30",
                    channel="security-audit",
                    text="[보안감사팀 최종 공지] 안건 SEC-ORION-99: 보안 적합성 필수 통제 항목 심의를 완료하여 '최종 통과(PASS)' 승인 의결되었습니다.",
                    message_identifier="syn_g_02_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=2,
            expected_inconclusive=False,
        )
    )

    # =========================================================================
    # Case H: Round 1 채널/스레드 이관 단서 기반 Round 2 특수 채널 발굴 완결 (Multi-Round Redirect Tracking)
    # =========================================================================
    cases.append(
        SyntheticE2ECase(
            case_id="E2E-CASE-H",
            case_name="해외 PG 소급정산 특수 스레드 이관 추적 및 환급금 지급 확인",
            description="1라운드 일반 검색으로는 특수 스레드 이관 사실만 확인되고, 2라운드에서 해당 스레드 명칭으로 최종 결재 및 송금 완료를 발굴하는 케이스",
            question=UserQuestion(
                question="해외 법인 PG사 수수료 소급 정산 환급금이 재무본부장 최종 승인을 받아 송금 완료되었는지 확인해줘.",
                context_hint="해외 법인 PG사, 수수료 소급 정산, 환급금",
            ),
            corpus_items=[
                EvidenceItem(
                    evidence_id="syn_h_01",
                    source="synthetic",
                    author="정산담당자",
                    timestamp="2026-08-05 14:00",
                    channel="settlement-ops",
                    text="해외 법인 PG사 수수료 소급 정산 환급금 안건은 일반 정산 채널에서 분리되어 비공개 채널 'tax-audit-special'의 'ZEPHYROS-REFUND' 코드로 이관되었습니다. 최종 승인 및 송금 여부는 'ZEPHYROS-REFUND' 코드로 확인해야 합니다.",
                    message_identifier="syn_h_01_ident",
                ),
                EvidenceItem(
                    evidence_id="syn_h_02",
                    source="synthetic",
                    author="재무본부장",
                    timestamp="2026-08-15 11:30",
                    channel="tax-audit-special",
                    text="[전자결재 승인 공지] 안건 ZEPHYROS-REFUND: 재무본부장 전자결재(결재번호 #2026-FIN-889) 최종 승인 완료되었으며, 해외 PG 수수료 소급정산 환급액 4,500만원 전액 송금 처리 완료되었습니다.",
                    message_identifier="syn_h_02_ident",
                ),
            ],
            expected_stop_reason="EVIDENCE_SUFFICIENT",
            expected_rounds=2,
            expected_inconclusive=False,
        )
    )

    return cases
