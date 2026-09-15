"""External Model Payload Builder with Phase 5 Security Hard Gate."""

from __future__ import annotations

import logging
import re
from typing import Optional
from pesu_agent.investigation.exceptions import ExternalTransmissionBlockedError
from pesu_agent.investigation.models import EvidencePackage, UserQuestion
from pesu_agent.search.actor import ActorScopeKind
from pesu_agent.llm.models import LLMMessage

logger = logging.getLogger(__name__)


class ExternalModelPayloadBuilder:
    """EvidencePackage를 안전하고 정제된 외부 LLM 프롬프트 페이로드로 변환하는 빌더.
    
    보안 원칙:
    - Phase 5 Hard Gate: is_synthetic == False인 실제 Slack 데이터는 HTTP 생성 전 원천 차단.
    - Slack 내부 DOM 속성, 셀렉터, 비인가 해시 등 내부 구현 디테일 제거.
    - Claim 단위 인용이 용이하도록 [E1], [E2] 형태의 표준 인용 헤더 부여.
    """

    @staticmethod
    def enforce_phase5_hard_gate(package: EvidencePackage) -> None:
        """Phase 5 전송 게이트: 가상(Synthetic) 데이터가 아닌 경우 예외를 발생시키고 네트워크 호출을 차단합니다."""
        if not package.is_synthetic:
            logger.critical(
                "🚨 [HARD_GATE_BLOCKED] 실제 Slack EvidencePackage의 외부 LLM 전송 시도가 감지되어 차단되었습니다."
            )
            raise ExternalTransmissionBlockedError(
                "Phase 5에서는 실제 Slack 데이터의 외부 전송이 엄격히 금지됩니다 (is_synthetic == False). "
                "Synthetic Evidence Fixture만 사용해야 합니다."
            )

    @classmethod
    def build_analysis_payload(
        cls,
        question: UserQuestion,
        package: EvidencePackage,
        system_prompt: Optional[str] = None,
        max_evidence_chars: int = 15000,
        allow_real_slack_external_analysis: bool = False,
    ) -> list[LLMMessage]:
        """UserQuestion과 EvidencePackage를 결합하여 Analyzer용 LLMMessage 목록을 생성합니다."""
        # 1. Phase 5 Hard Gate 검증 (HTTP 요청 생성 전 사전 실행)
        if not allow_real_slack_external_analysis:
            cls.enforce_phase5_hard_gate(package)

        # 2. 증거 항목 정제 및 표준 포맷 변환
        evidence_blocks: list[str] = []
        for item in package.evidence_items:
            author_str = item.author or "알 수 없음"
            time_str = item.timestamp or "시각 정보 없음"
            channel_str = f" (#{item.channel})" if item.channel else ""
            
            att_info = ""
            if item.has_attachments:
                names_str = f" ({', '.join(item.attachment_names)})" if item.attachment_names else ""
                att_info = f"\n  [첨부 파일: {item.attachment_count}개{names_str}]"

            # Clean any internal HTML or excessive whitespace
            clean_text = re.sub(r"<[^>]+>", "", item.text).strip()
            reply_lines = []
            is_self_scope = bool(
                package.actor_scope and package.actor_scope.kind == ActorScopeKind.SELF
            )
            # Mandatory Correction E: Unqualified thread replies must not reach SELF analyzer
            if getattr(item, "thread_replies", None) and not is_self_scope:
                reply_lines.append("  [스레드 댓글 목록]")
                for r in item.thread_replies:
                    r_clean = re.sub(r"<[^>]+>", "", r.text).strip()
                    r_author = r.author or "알 수 없음"
                    r_time = f" ({r.timestamp})" if r.timestamp else ""
                    sub_id = f"{item.evidence_id}-{r.reply_id}"
                    reply_lines.append(f"  ↳ [{sub_id}] {r_author}{r_time}: {r_clean}")

            thread_section = ("\n" + "\n".join(reply_lines)) if reply_lines else ""
            author_rel_str = " (본인 작성)" if item.author_relation == "SELF" else ""
            block = (
                f"[{item.evidence_id}] 작성자: {author_str}{author_rel_str} | 시각: {time_str}{channel_str}{att_info}\n"
                f"{clean_text}{thread_section}"
            )
            evidence_blocks.append(block)

        evidence_section = "\n\n".join(evidence_blocks)
        if len(evidence_section) > max_evidence_chars:
            evidence_section = evidence_section[:max_evidence_chars] + "\n\n...[증거 분량 초과로 축약됨]..."

        # 3. System & User Messages 구성
        coverage_val = package.retrieval_coverage or "UNKNOWN"
        default_system = (
            "당신은 사내 사실관계 및 쟁점 조사를 수행하는 수석 조사관 AI(Chief Investigator)입니다.\n"
            "제공된 증거(Evidence)에 근거하여 사용자 질문에 대한 객관적이고 균형 잡힌 분석 결과를 도출하십시오.\n"
            "\n"
            "[핵심 분석 원칙]\n"
            "1. 절대 증거에 없는 사실을 지어내거나 추측을 사실로 단정하지 마십시오 (Zero Hallucination).\n"
            "2. 모든 확인된 사실(confirmed_facts)과 쟁점(disputed_points) 및 모순(contradictions)에는 반드시 이를 뒷받침하는 유효한 증거 ID(['E1', 'E2'] 등 또는 스레드 댓글인 경우 ['E4-R1'] 등)를 명시하십시오.\n"
            "3. 결론 유형(finding_type) 판정 규칙:\n"
            "   - POSITIVE_SUPPORTED: 제공된 증거에서 질문 내용에 부합하는 사실이 명시적으로 확인된 경우.\n"
            "   - EXPLICIT_NEGATIVE_SUPPORTED: 증거 본문에서 '해당 계획이나 일정이 전혀 없다/취소되었다/미정이다'라는 명시적인 부정/부인 사실이 확인되고 이를 뒷받침하는 유효 증거 ID가 인용된 경우.\n"
            "   - INCONCLUSIVE: 검색된 증거가 부족하거나 질문의 사실 여부를 확정할 수 없는 경우.\n"
            "4. 부정적 답변 가드 (Negative-Answer Guard):\n"
            f"   - 현재 검색 커버리지(Retrieval Coverage)는 '{coverage_val}' 상태입니다.\n"
            "   - 검색 커버리지가 LIMITED 또는 NO_DIRECT_MATCH인 경우, 명시적 부정 증거(EXPLICIT_NEGATIVE_SUPPORTED)가 없다면 '회사 전체에 일정이 없다/존재하지 않는다'고 단정하지 마십시오.\n"
            "   - 대신 '현재 검색된 Slack 대화 범위에서는 확인되지 않음'과 같은 한계와 불확실성을 표현하십시오.\n"
            "5. 증거가 명백히 확인된 경우 conclusion_status='CONFIRMED', 주장이 상충되는 경우 'DISPUTED', 증거 부족이나 판단 불가 시 'INCONCLUSIVE'로 설정하십시오.\n"
            "6. 책임 소재 판정 가능 여부(responsibility_determinable: True/False) 및 증거의 충분성(evidence_sufficiency: 'SUFFICIENT'/'PARTIAL'/'INSUFFICIENT')을 명확히 기재하십시오.\n"
            "7. 판단할 수 없는 사항은 uncertainty 및 missing_information에 명확히 기록하십시오."
        )

        user_content = (
            f"[조사 대상 질문]\n{question.question}\n\n"
        )
        if question.context_hint:
            user_content += f"[참고 배경]\n{question.context_hint}\n\n"

        if package.temporal_scope and package.temporal_scope.intent_present:
            scope = package.temporal_scope
            user_content += (
                f"[조사 대상 시계열 범위]\n"
                f"- 원본 표현: {scope.source_expression or '없음'}\n"
                f"- 엄격성: {scope.strictness.value}\n"
                f"- 시작 시각: {scope.start_at.isoformat() if scope.start_at else '미지정'}\n"
                f"- 종료 시각: {scope.end_at.isoformat() if scope.end_at else '미지정'}\n"
                f"- 기준 타임존: {scope.timezone}\n\n"
            )

        if package.actor_scope and package.actor_scope.kind == ActorScopeKind.SELF:
            scope_actor = package.actor_scope
            user_content += (
                f"[조사 대상 액터 범위]\n"
                f"- 대상: SELF (사용자 본인)\n"
                f"- 해석 유형: {'암시적 (개인 활동/회고 질의)' if scope_actor.is_implicit else '명시적 1인칭'}\n"
                f"- 주의: 본인이 작성한 메시지(SELF 작성)라도 타인의 업무나 팀 상황을 공유한 것일 수 있으므로, "
                f"메시지 본문 내용에 근거하여 본인이 수행/작업한 것으로 객관적으로 확인되는 내용만 서술하십시오.\n\n"
            )

        user_content += f"[검색 커버리지 상태]: {coverage_val}\n\n"
        user_content += f"[수집된 증거 목록 (총 {len(package.evidence_items)}건)]\n{evidence_section}\n\n"
        user_content += "위 증거들을 분석하여 엄격한 Pydantic JSON Schema 형식으로 종합 조사 결과를 출력하십시오."

        return [
            LLMMessage(role="system", content=system_prompt or default_system),
            LLMMessage(role="user", content=user_content),
        ]
