"""LLM & Model Provider Configuration Management."""

from __future__ import annotations

import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

# Load local .env automatically
load_dotenv()


def _resolve_default_api_key() -> Optional[str]:
    try:
        from pesu_agent.security.credential_store import get_openrouter_api_key
        key, _ = get_openrouter_api_key()
        return key
    except Exception:
        return os.getenv("OPENROUTER_API_KEY")


class LLMConfig(BaseModel):
    """LLM Provider 및 모델 설정."""

    provider: str = Field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "openrouter"),
        description="LLM Provider (openrouter, openai, anthropic, google, moonshot)",
    )
    api_key: Optional[SecretStr] = Field(
        default_factory=lambda: (
            SecretStr(_resolve_default_api_key())
            if _resolve_default_api_key()
            else None
        ),
        description="Provider API Key (SecretStr로 래핑되어 로그에 노출되지 않음)",
    )
    openrouter_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        description="OpenRouter API Base URL",
    )
    planner_model: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_PLANNER_MODEL", "openai/gpt-5.6-luna"
        ),
        description="조사 계획 수립에 사용할 LLM 모델 식별자",
    )
    selector_model: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_SELECTOR_MODEL", "openai/gpt-5.6-luna"
        ),
        description="검색 결과 필터링/선택에 사용할 LLM 모델 식별자",
    )
    analyzer_model: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_ANALYZER_MODEL", "openai/gpt-5.6-luna"
        ),
        description="증거 분석 및 종합 평가에 사용할 LLM 모델 식별자",
    )
    claim_verifier_model: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_CLAIM_VERIFIER_MODEL", "openai/gpt-5.6-luna"
        ),
        description="주장 의미 검증(Claim Verification)에 사용할 LLM 모델 식별자",
    )
    claim_verifier_reasoning_effort: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_CLAIM_VERIFIER_REASONING_EFFORT", "medium"
        ),
        description="주장 의미 검증 추론 노력 수준 (low, medium, high)",
    )
    episode_grouper_model: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_EPISODE_GROUPER_MODEL", "openai/gpt-5.6-luna"
        ),
        description="Stage 1A 에피소드 멤버십 그루퍼 모델 식별자",
    )
    episode_grouper_reasoning_effort: str = Field(
        default_factory=lambda: os.getenv(
            "LLM_EPISODE_GROUPER_REASONING_EFFORT", "medium"
        ),
        description="Stage 1A 에피소드 멤버십 그루퍼 추론 노력 수준 (low, medium, high)",
    )
    temperature: float = Field(default=0.0, description="Sampling temperature")
    max_retries: int = Field(
        default=2, description="Structured Output 파싱 실패 시 최대 재시도 횟수"
    )
    request_timeout_sec: float = Field(
        default=60.0, description="HTTP 요청 타임아웃 (초)"
    )

    def get_api_key_value(self) -> Optional[str]:
        """시크릿 값을 안전하게 조회 (오직 HTTP Authorization 헤더 구성 시에만 사용)."""
        return self.api_key.get_secret_value() if self.api_key else None
