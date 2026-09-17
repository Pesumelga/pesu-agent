"""OpenRouter Model Provider Implementation."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional, TypeVar
import httpx
from pydantic import BaseModel, ValidationError

from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import CostSource, LLMMessage, LLMResponse, LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.llm.schema import normalize_strict_json_schema
from pesu_agent.llm.usage import estimate_call_cost

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OpenRouterAPIError(Exception):
    """OpenRouter API 호출 실패 예외 (API Key 등 시크릿 마스킹 및 구조화 진단 정보 보장)."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str | int] = None,
        provider_message: Optional[str] = None,
        upstream_provider: Optional[str] = None,
        raw_error_text: Optional[str] = None,
    ):
        sanitized = self._sanitize(message)
        super().__init__(sanitized)
        self.status_code = status_code
        self.error_code = error_code
        self.provider_message = self._sanitize(provider_message) if provider_message else None
        self.upstream_provider = upstream_provider
        self.raw_error_text = self._sanitize(raw_error_text) if raw_error_text else None

    @staticmethod
    def _sanitize(text: Optional[str]) -> str:
        if not text:
            return ""
        s = str(text)
        s = re.sub(r"sk-or-[a-zA-Z0-9_-]+", "[REDACTED_API_KEY]", s)
        s = re.sub(r"Bearer\s+[a-zA-Z0-9_.-]+", "Bearer [REDACTED_API_KEY]", s)
        return s

    @classmethod
    def from_response(cls, status_code: int, response_text: str) -> OpenRouterAPIError:
        """Constructs an OpenRouterAPIError parsing OpenRouter structured error JSON."""
        error_code: Optional[str | int] = None
        provider_msg: Optional[str] = None
        upstream_provider: Optional[str] = None
        raw_text: str = response_text

        try:
            data = json.loads(response_text)
            if isinstance(data, dict) and "error" in data:
                err_dict = data["error"]
                if isinstance(err_dict, dict):
                    error_code = err_dict.get("code")
                    provider_msg = err_dict.get("message")
                    metadata = err_dict.get("metadata", {})
                    if isinstance(metadata, dict):
                        upstream_provider = metadata.get("provider_name")
                        if metadata.get("provider_error_code"):
                            error_code = metadata.get("provider_error_code")
                        inner_raw = metadata.get("raw")
                        if isinstance(inner_raw, str):
                            try:
                                inner_data = json.loads(inner_raw)
                                if isinstance(inner_data, dict) and "error" in inner_data:
                                    inner_err = inner_data["error"]
                                    if isinstance(inner_err, dict) and "message" in inner_err:
                                        provider_msg = inner_err["message"]
                            except Exception:
                                pass
        except Exception:
            pass

        full_msg = f"OpenRouter API 호출 실패 (HTTP {status_code}): {provider_msg or response_text}"
        return cls(
            message=full_msg,
            status_code=status_code,
            error_code=error_code,
            provider_message=provider_msg,
            upstream_provider=upstream_provider,
            raw_error_text=raw_text,
        )


class OpenRouterProvider(ModelProvider):
    """OpenRouter API를 통해 다양한 LLM에 접근하는 ModelProvider 구현체."""

    provider_name: str = "openrouter"

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        import asyncio
        current_loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or getattr(self, "_bound_loop", None) != current_loop
        ):
            self._bound_loop = current_loop
            self._client = httpx.AsyncClient(
                timeout=self.config.request_timeout_sec,
                headers={
                    "HTTP-Referer": "https://github.com/Pesumelga/pesu-agent",
                    "X-Title": "pesu-agent",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _extract_usage(
        self,
        raw_res: dict[str, Any],
        model: str,
        latency_ms: float,
        response_headers: Optional[httpx.Headers] = None,
    ) -> LLMUsageStats:
        """API 응답 및 헤더에서 토큰 수 및 실제/추정 비용을 추출합니다."""
        usage_data = raw_res.get("usage", {})
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)
        total_tokens = usage_data.get("total_tokens", prompt_tokens + completion_tokens)

        # Reasoning tokens (OpenAI / DeepSeek / Moonshot standard)
        reasoning_tokens = None
        comp_details = usage_data.get("completion_tokens_details", {})
        if isinstance(comp_details, dict):
            reasoning_tokens = comp_details.get("reasoning_tokens")
        if reasoning_tokens is None:
            reasoning_tokens = usage_data.get("reasoning_tokens")

        # 1. Provider가 응답 payload 또는 헤더에 비용을 직접 보고했는지 확인
        actual_cost = usage_data.get("cost")
        if actual_cost is None and response_headers:
            header_cost = response_headers.get("x-openrouter-cost")
            if header_cost:
                try:
                    actual_cost = float(header_cost)
                except ValueError:
                    pass

        if actual_cost is not None:
            cost_val = float(actual_cost)
            return LLMUsageStats(
                provider=self.provider_name,
                model=model,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=total_tokens,
                reasoning_tokens=reasoning_tokens,
                latency_ms=latency_ms,
                actual_cost_usd=cost_val,
                estimated_cost_usd=cost_val,
                cost_source="provider_reported",
            )

        # 2. Provider 보고 비용이 없으면 로컬 단가 스냅샷으로 추정
        est_cost = estimate_call_cost(model, prompt_tokens, completion_tokens)
        cost_source: CostSource = "free" if est_cost == 0.0 else "local_estimate"
        return LLMUsageStats(
            provider=self.provider_name,
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            actual_cost_usd=None,
            estimated_cost_usd=est_cost,
            cost_source=cost_source,
        )

    async def generate(
        self,
        messages: list[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResponse:
        """비구조화된 텍스트 응답을 생성합니다."""
        target_model = model or self.config.analyzer_model
        api_key = self.config.get_api_key_value()
        if not api_key:
            raise OpenRouterAPIError(
                "OPENROUTER_API_KEY가 설정되지 않았습니다. .env 또는 환경변수를 확인해주세요."
            )

        endpoint = f"{self.config.openrouter_base_url.rstrip('/')}/chat/completions"
        call_kwargs = dict(kwargs)
        extra_body = call_kwargs.pop("extra_body", {})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            **call_kwargs,
        }

        # Merge extra_body if provided
        if isinstance(extra_body, dict):
            payload.update(extra_body)

        # Handle direct reasoning_effort keyword
        reasoning_effort = call_kwargs.get("reasoning_effort")
        if reasoning_effort and "reasoning" not in payload:
            payload["reasoning"] = {"effort": reasoning_effort}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        client = await self._get_client()
        t0 = time.perf_counter()

        try:
            res = await client.post(endpoint, json=payload, headers=headers)
            latency_ms = (time.perf_counter() - t0) * 1000.0

            if res.status_code != 200:
                err = OpenRouterAPIError.from_response(res.status_code, res.text)
                logger.error(
                    f"OpenRouter API 오류 (HTTP {res.status_code}): model={target_model}, "
                    f"code={err.error_code}, provider={err.upstream_provider}"
                )
                raise err

            res_json = res.json()
            choices = res_json.get("choices", [])
            if not choices:
                raise OpenRouterAPIError("OpenRouter 응답에 choices 항목이 없습니다.")

            content = choices[0].get("message", {}).get("content", "")
            usage_stats = self._extract_usage(
                res_json, target_model, latency_ms, res.headers
            )

            return LLMResponse(
                content=content,
                raw_payload={"id": res_json.get("id"), "model": res_json.get("model")},
                usage=usage_stats,
            )

        except httpx.HTTPError as err:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            raise OpenRouterAPIError(f"HTTP 네트워크 통신 오류: {err}") from None

    @staticmethod
    def build_native_response_format(response_schema: type[BaseModel]) -> dict[str, Any]:
        """Strict Structured Outputs 호환 native json_schema response_format 딕셔너리를 생성합니다."""
        strict_schema = normalize_strict_json_schema(response_schema)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "strict": True,
                "schema": strict_schema,
            },
        }

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        response_schema: type[T],
        model: Optional[str] = None,
        temperature: float = 0.0,
        structured_mode: str = "auto",
        **kwargs: Any,
    ) -> tuple[T, LLMUsageStats]:
        """Pydantic 스키마를 준수하는 구조화된 출력을 생성합니다.
        
        Structured Output Capability Modes:
        1. structured_mode == "auto": Native JSON Schema 우선 시도 후 에러 시 JSON Object Fallback
        2. structured_mode == "json_object": Native probe를 생략하고 즉시 JSON Object 모드 사용
        3. Pydantic 유효성 검증 실패 시 -> 에러 피드백 기반 제한적 재시도 (최대 max_retries회)
        """
        target_model = model or self.config.analyzer_model
        schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False, indent=2)

        # 1. Native JSON Schema 시도 (structured_mode가 json_object가 아닌 경우에만 실행)
        if structured_mode != "json_object" and kwargs.get("structured_mode") != "json_object":
            native_kwargs = dict(kwargs)
            native_kwargs.pop("structured_mode", None)
            native_kwargs["response_format"] = self.build_native_response_format(response_schema)

            try:
                resp = await self.generate(
                    messages=messages,
                    model=target_model,
                    temperature=temperature,
                    **native_kwargs,
                )
                parsed = self._parse_and_validate(resp.content, response_schema)
                return parsed, resp.usage

            except (OpenRouterAPIError, ValidationError, json.JSONDecodeError) as first_err:
                logger.warning(
                    "Native JSON Schema attempt failed (HTTP %s, code=%s, upstream=%s): %s. "
                    "Switching to JSON Object Prompt Fallback mode.",
                    getattr(first_err, "status_code", None),
                    getattr(first_err, "error_code", None),
                    getattr(first_err, "upstream_provider", None),
                    getattr(first_err, "provider_message", str(first_err)),
                )

        # 2. JSON Object Prompt + System Instructions Mode
        fallback_messages = list(messages)
        has_schema_instruction = any(
            "[OUTPUT_FORMAT_INSTRUCTION]" in (m.content or "") for m in fallback_messages
        )
        if not has_schema_instruction:
            schema_instruction = (
                f"\n\n[OUTPUT_FORMAT_INSTRUCTION]\n"
                f"You MUST respond ONLY with a valid, parseable JSON object satisfying this exact JSON Schema:\n"
                f"```json\n{schema_json}\n```\n"
                f"Do not output markdown codeblock ticks if possible, and do not output any explanatory text before or after the JSON."
            )

            # Append schema instruction to system or last user message
            if fallback_messages and fallback_messages[0].role == "system":
                fallback_messages[0] = LLMMessage(
                    role="system",
                    content=fallback_messages[0].content + schema_instruction,
                )
            else:
                fallback_messages.insert(
                    0, LLMMessage(role="system", content=schema_instruction)
                )

        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("structured_mode", None)
        fallback_kwargs["response_format"] = {"type": "json_object"}

        last_error: Optional[Exception] = None
        current_messages = fallback_messages

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await self.generate(
                    messages=current_messages,
                    model=target_model,
                    temperature=temperature,
                    **fallback_kwargs,
                )
                parsed = self._parse_and_validate(resp.content, response_schema)
                return parsed, resp.usage

            except (ValidationError, json.JSONDecodeError, ValueError) as err:
                last_error = err
                logger.warning(
                    f"Structured output validation failed (attempt {attempt + 1}/{self.config.max_retries + 1}): {err}"
                )
                if attempt < self.config.max_retries:
                    # Provide exact validation error feedback to model for retry
                    feedback = (
                        f"Your previous JSON output was invalid.\n"
                        f"Validation Error: {err}\n"
                        f"Please correct the output to strictly conform to the required schema."
                    )
                    current_messages = current_messages + [
                        LLMMessage(role="assistant", content=resp.content if 'resp' in locals() else "{}"),
                        LLMMessage(role="user", content=feedback),
                    ]

        raise OpenRouterAPIError(
            f"Structured Output 생성 및 스키마 검증에 최종 실패했습니다 ({self.config.max_retries + 1}회 시도): {last_error}"
        )

    def _parse_and_validate(self, text: str, schema_cls: type[T]) -> T:
        """응답 텍스트에서 JSON 블록을 추출하여 Pydantic 모델로 엄격히 검증합니다."""
        clean_text = text.strip()

        # Strip markdown codeblocks if present
        if clean_text.startswith("```"):
            lines = clean_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()

        # Parse JSON
        parsed_json = json.loads(clean_text)
        return schema_cls.model_validate(parsed_json)
