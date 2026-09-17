"""LLM Usage, Latency, and Cost Instrumentation."""

from __future__ import annotations

import logging
from typing import Optional
from pydantic import BaseModel, Field
from pesu_agent.llm.models import LLMUsageStats

logger = logging.getLogger(__name__)

# Representative local pricing snapshot (USD per 1M tokens: input, output)
# Used only as fallback when provider does not report actual cost in API response.
LOCAL_PRICING_SNAPSHOT: dict[str, tuple[float, float]] = {
    # Gemini 2.0 Flash
    "google/gemini-2.0-flash-001": (0.10, 0.40),
    "google/gemini-2.0-flash-thinking-exp": (0.00, 0.00),
    "google/gemini-flash-1.5": (0.075, 0.30),
    # OpenAI
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    # Anthropic
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "anthropic/claude-3-haiku": (0.25, 1.25),
    # Moonshot / DeepSeek
    "moonshotai/moonshot-v1-8k": (0.12, 0.12),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-r1": (0.55, 2.19),
}


def estimate_call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """로컬 단가 스냅샷을 기반으로 예상 비용을 산출합니다 ($)."""
    clean_model = model.lower().strip()
    rate = LOCAL_PRICING_SNAPSHOT.get(clean_model)
    if not rate:
        # Check partial match
        for k, v in LOCAL_PRICING_SNAPSHOT.items():
            if k.split("/")[-1] in clean_model:
                rate = v
                break
    if not rate:
        # Default conservative fallback rate ($0.50 / $1.50 per 1M tokens)
        rate = (0.50, 1.50)

    cost = (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000.0
    return round(cost, 6)


class LLMUsageTracker(BaseModel):
    """세션 전체의 LLM 호출 사용량 및 비용 감사 추적기."""

    total_calls: int = Field(default=0, description="총 LLM API 호출 횟수")
    total_input_tokens: int = Field(default=0, description="총 입력 토큰 수")
    total_output_tokens: int = Field(default=0, description="총 출력 토큰 수")
    total_tokens: int = Field(default=0, description="총 토큰 합계")
    total_latency_ms: float = Field(default=0.0, description="총 누적 지연시간 (ms)")
    total_cost_usd: float = Field(default=0.0, description="총 누적 비용 ($)")
    call_records: list[LLMUsageStats] = Field(
        default_factory=list, description="개별 호출별 감사 지표 (프롬프트 원문은 저장하지 않음)"
    )

    def record_call(self, stats: LLMUsageStats) -> None:
        """단일 LLM 호출 계측 결과를 세션 추적기에 기록합니다."""
        self.total_calls += 1
        self.total_input_tokens += stats.input_tokens
        self.total_output_tokens += stats.output_tokens
        self.total_tokens += stats.total_tokens
        self.total_latency_ms += stats.latency_ms

        cost = (
            stats.actual_cost_usd
            if stats.actual_cost_usd is not None
            else stats.estimated_cost_usd
        )
        self.total_cost_usd += cost
        self.call_records.append(stats)

        logger.debug(
            f"[LLM_USAGE] model={stats.model} tokens={stats.total_tokens} (in={stats.input_tokens}, out={stats.output_tokens}) "
            f"latency={stats.latency_ms:.1f}ms cost=${cost:.6f} ({stats.cost_source})"
        )

    def get_summary(self) -> dict[str, Any]:
        """사용량 요약 보고서를 반환합니다."""
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "avg_latency_ms": (
                round(self.total_latency_ms / self.total_calls, 2)
                if self.total_calls > 0
                else 0.0
            ),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "records_count": len(self.call_records),
        }
