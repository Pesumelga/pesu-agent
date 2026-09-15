"""Investigation End-to-End Pipeline."""

from __future__ import annotations

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from pesu_agent.investigation.analyzer import InvestigationAnalyzer
from pesu_agent.investigation.models import (
    AnalysisResult,
    EvidencePackage,
    InvestigationPlan,
    UserQuestion,
)
from pesu_agent.investigation.planner import InvestigationPlanner
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.llm.usage import LLMUsageTracker

logger = logging.getLogger(__name__)


class InvestigationPipelineResult(BaseModel):
    """Investigation 파이프라인 최종 실행 결과 봉투."""

    question: UserQuestion
    plan: InvestigationPlan
    evidence_package: EvidencePackage
    analysis: AnalysisResult
    usage_summary: dict[str, Any] = Field(description="토큰, 지연시간, 비용 집계 요약")
    is_synthetic: bool = Field(description="가상 데이터 기반 실행 여부")


class InvestigationPipeline:
    """조사 계획 수립 -> 증거 수집/연계 -> 분석 및 평가 파이프라인."""

    def __init__(self, provider: ModelProvider, config: Optional[LLMConfig] = None):
        self.provider = provider
        self.config = config or LLMConfig()
        self.planner = InvestigationPlanner(provider=provider, config=self.config)
        self.analyzer = InvestigationAnalyzer(provider=provider, config=self.config)
        self.usage_tracker = LLMUsageTracker()

    async def run_synthetic_investigation(
        self,
        question: UserQuestion,
        package: EvidencePackage,
        planner_model: Optional[str] = None,
        analyzer_model: Optional[str] = None,
    ) -> InvestigationPipelineResult:
        """가상(Synthetic) 증거 패키지를 사용하여 전체 파이프라인을 실행합니다."""
        logger.info(f"▶ Phase 5 Synthetic Investigation 파이프라인 시작: '{question.question[:50]}...'")

        # 1. Step 1: Investigation Plan 수립
        plan, plan_usage = await self.planner.plan(
            question=question, model=planner_model
        )
        self.usage_tracker.record_call(plan_usage)

        # 2. Step 2: Evidence Package 주입 (Synthetic 확인)
        # Phase 5에서는 실제 검색 대신 검증된 Synthetic Package 연계

        # 3. Step 3: Investigation Analysis 수행
        analysis, analysis_usage = await self.analyzer.analyze(
            question=question, package=package, model=analyzer_model
        )
        self.usage_tracker.record_call(analysis_usage)

        usage_summary = self.usage_tracker.get_summary()
        logger.info(
            f"✓ Investigation 파이프라인 완료: calls={usage_summary['total_calls']} "
            f"tokens={usage_summary['total_tokens']} cost=${usage_summary['total_cost_usd']:.6f}"
        )

        return InvestigationPipelineResult(
            question=question,
            plan=plan,
            evidence_package=package,
            analysis=analysis,
            usage_summary=usage_summary,
            is_synthetic=package.is_synthetic,
        )
