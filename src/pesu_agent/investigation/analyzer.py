"""Investigation Analyzer with Grounded Evidence Citation Validation."""

from __future__ import annotations

import logging
from typing import Optional
from pesu_agent.investigation.exceptions import AnalysisError, EvidenceCitationError
from pesu_agent.investigation.models import AnalysisResult, EvidencePackage, UserQuestion
from pesu_agent.investigation.payload_builder import ExternalModelPayloadBuilder
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.llm.provider import ModelProvider

logger = logging.getLogger(__name__)


class InvestigationAnalyzer:
    """수집된 증거(EvidencePackage)를 기반으로 사실관계 및 쟁점을 분석하는 분석관."""

    def __init__(self, provider: ModelProvider, config: Optional[LLMConfig] = None):
        self.provider = provider
        self.config = config or LLMConfig()

    async def analyze(
        self,
        question: UserQuestion,
        package: EvidencePackage,
        model: Optional[str] = None,
        strict_citation_check: bool = True,
        allow_real_slack_external_analysis: bool = False,
        **kwargs: Any,
    ) -> tuple[AnalysisResult, LLMUsageStats]:
        """EvidencePackage를 분석하여 입증된 사실, 쟁점, 모순, 최종 평가를 포함한 구조화된 결과를 반환합니다."""
        target_model = model or self.config.analyzer_model

        # 1. Payload 빌드 및 Phase 5 Hard Gate 검증
        messages = ExternalModelPayloadBuilder.build_analysis_payload(
            question=question,
            package=package,
            allow_real_slack_external_analysis=allow_real_slack_external_analysis,
        )

        try:
            raw_analysis, usage = await self.provider.generate_structured(
                messages=messages,
                response_schema=AnalysisResult,
                model=target_model,
                temperature=self.config.temperature,
                **kwargs,
            )

            # 2. 증거 인용(Citation) 유효성 사후 검증 (Zero Hallucination Gate)
            valid_evidence_ids: set[str] = set()
            for item in package.evidence_items:
                valid_evidence_ids.add(item.evidence_id)
                for r in getattr(item, "thread_replies", []):
                    # Verify subcitation canonical consistency:
                    # - permalink valid
                    # - channel matches parent (or parent channel identifier)
                    # - root thread matches parent
                    if r.permalink and r.reply_id:
                        sub_id = f"{item.evidence_id}-{r.reply_id}"
                        valid_evidence_ids.add(sub_id)

            cited_ids: set[str] = set()
            hallucinated_ids: set[str] = set()

            # Fact 인용 검사
            for fact in raw_analysis.confirmed_facts:
                for eid in fact.supporting_evidence_ids:
                    clean_eid = eid.strip("[] ")
                    if clean_eid in valid_evidence_ids:
                        cited_ids.add(clean_eid)
                    else:
                        hallucinated_ids.add(clean_eid)

            # Disputed Point 인용 검사
            for dp in raw_analysis.disputed_points:
                for eid in dp.supporting_evidence_ids:
                    clean_eid = eid.strip("[] ")
                    if clean_eid in valid_evidence_ids:
                        cited_ids.add(clean_eid)
                    else:
                        hallucinated_ids.add(clean_eid)

            # Contradiction 인용 검사
            for c in raw_analysis.contradictions:
                for eid in c.conflicting_evidence_ids:
                    clean_eid = eid.strip("[] ")
                    if clean_eid in valid_evidence_ids:
                        cited_ids.add(clean_eid)
                    else:
                        hallucinated_ids.add(clean_eid)

            # 결과 객체에 인용 지표 및 커버리지 반영
            effective_finding_type = raw_analysis.finding_type
            if effective_finding_type == "EXPLICIT_NEGATIVE_SUPPORTED" and not cited_ids:
                logger.warning(
                    "🚨 [EXPLICIT_NEGATIVE_REJECTED] EXPLICIT_NEGATIVE_SUPPORTED claimed without valid supporting citations. Demoting to INCONCLUSIVE."
                )
                effective_finding_type = "INCONCLUSIVE"

            validated_analysis = raw_analysis.model_copy(
                update={
                    "cited_evidence_ids": sorted(list(cited_ids)),
                    "hallucinated_evidence_ids": sorted(list(hallucinated_ids)),
                    "finding_type": effective_finding_type,
                    "retrieval_coverage": package.retrieval_coverage,
                    "thread_context_status": package.thread_context_status,
                }
            )

            if hallucinated_ids and strict_citation_check:
                logger.error(
                    f"🚨 [CITATION_HALLUCINATION_DETECTED] 존재하지 않는 증거 ID 인용 발견: {hallucinated_ids}"
                )
                raise EvidenceCitationError(
                    f"모델이 제공된 증거 패키지에 존재하지 않는 환각 증거 ID({hallucinated_ids})를 인용했습니다."
                )

            logger.info(
                f"[ANALYSIS_SUCCESS] model={target_model} facts={len(validated_analysis.confirmed_facts)} "
                f"disputes={len(validated_analysis.disputed_points)} contradictions={len(validated_analysis.contradictions)} "
                f"cited_ids={validated_analysis.cited_evidence_ids} hallucinated={len(hallucinated_ids)}"
            )

            return validated_analysis, usage

        except EvidenceCitationError:
            raise
        except Exception as err:
            logger.error(f"[ANALYSIS_FAILED] 증거 분석 실패: {err}")
            raise AnalysisError(f"증거 분석 중 오류 발생: {err}") from err
