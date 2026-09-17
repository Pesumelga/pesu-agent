"""LLM Core Data Models and Usage Tracking Structures."""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

CostSource = Literal["provider_reported", "local_estimate", "free", "unknown"]


class LLMMessage(BaseModel):
    """단일 LLM 대화 메시지."""

    role: Literal["system", "user", "assistant"] = Field(description="메시지 역할")
    content: str = Field(description="메시지 내용")
    name: Optional[str] = Field(default=None, description="선택적 화자 식별자")


class LLMUsageStats(BaseModel):
    """단일 LLM 호출의 토큰, 지연시간, 비용 감사 계측치."""

    provider: str = Field(description="호출 대상 Provider 식별자 (예: openrouter)")
    model: str = Field(description="호출된 모델명")
    input_tokens: int = Field(default=0, description="입력 프롬프트 토큰 수")
    output_tokens: int = Field(default=0, description="생성된 응답 토큰 수")
    total_tokens: int = Field(default=0, description="총 토큰 수")
    reasoning_tokens: Optional[int] = Field(
        default=None, description="모델이 보고한 추론(Reasoning/Thinking) 토큰 수"
    )
    latency_ms: float = Field(default=0.0, description="요청 시작부터 응답 완료까지의 지연시간 (ms)")
    actual_cost_usd: Optional[float] = Field(
        default=None, description="Provider가 직접 보고한 실제 과금 비용 ($)"
    )
    estimated_cost_usd: float = Field(
        default=0.0, description="로컬 단가표 기반 추정 비용 ($)"
    )
    cost_source: CostSource = Field(
        default="unknown", description="비용 산출 출처 (provider_reported / local_estimate / free)"
    )


class LLMResponse(BaseModel):
    """LLM 원시 텍스트 응답 및 사용량 통계."""

    content: str = Field(description="생성된 텍스트 본문")
    raw_payload: Optional[dict[str, Any]] = Field(
        default=None, description="선택적 원시 응답 메타데이터 (API Key 등 민감정보 배제)"
    )
    usage: LLMUsageStats = Field(description="호출 사용량 및 계측치")
