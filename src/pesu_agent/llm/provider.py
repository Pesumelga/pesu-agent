"""Model Provider Abstract Protocol."""

from __future__ import annotations

from typing import Any, Optional, Protocol, TypeVar, runtime_checkable
from pydantic import BaseModel
from pesu_agent.llm.models import LLMMessage, LLMResponse, LLMUsageStats

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class ModelProvider(Protocol):
    """LLM 호출을 위한 최상위 추상 프로토콜.
    
    Planner, Analyzer 등 PESU Agent Core는 특정 벤더 구현체(OpenRouter 등)에
    직접 의존하지 않고 이 프로토콜만을 호출합니다.
    """

    provider_name: str

    async def generate(
        self,
        messages: list[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResponse:
        """비구조화된 텍스트 응답을 생성합니다."""
        ...

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        response_schema: type[T],
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> tuple[T, LLMUsageStats]:
        """Pydantic 스키마를 만족하는 구조화된 출력(Structured Output)을 생성 및 검증합니다."""
        ...
