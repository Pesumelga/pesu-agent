"""Investigation Planner Implementation (Retrieval v2)."""

from __future__ import annotations

import logging
from typing import Any, Optional
from pesu_agent.investigation.exceptions import PlanningError
from pesu_agent.investigation.models import InvestigationPlan, SearchQuery, UserQuestion
from pesu_agent.llm.config import LLMConfig
from pesu_agent.llm.models import LLMMessage, LLMUsageStats
from pesu_agent.llm.provider import ModelProvider
from pesu_agent.search.slack_query_compiler import SlackQueryCompiler

logger = logging.getLogger(__name__)

MAX_SLACK_SEARCH_QUERIES = 3


def build_planner_system_prompt(
    max_search_queries: Optional[int] = None,
    search_target: Optional[str] = None,
) -> str:
    """Build budget-aware and target-aware system prompt for InvestigationPlanner."""
    is_slack = bool(search_target and search_target.strip().lower() == "slack")
    effective_max_queries = min(max_search_queries or MAX_SLACK_SEARCH_QUERIES, MAX_SLACK_SEARCH_QUERIES) if is_slack else (max_search_queries or 3)

    if is_slack:
        if max_search_queries is not None and max_search_queries == 1:
            query_instruction = """   - 이번 조사 대상 플랫폼은 Slack이며, 허용된 최대 검색 질의 예산은 1개입니다.
   - Slack 검색창에 직접 입력되어 실행될 단일 핵심 검색어(Standalone Slack query) 1개만 작성하십시오.
   - 사용자의 질문에서 가장 핵심적인 키워드 및 주요 명사구 중심으로 간결하게 구성하십시오.
   - Slack exact-phrase syntax가 의도적으로 필요한 특수 상황이 아니라면, 쿼리 전체를 따옴표("...")로 불필요하게 감싸지 말고 일반 검색어 형태로 작성하십시오.
   - 과도하게 많은 동의어 나열이나 복잡한 다중 OR 연결식, 긴 자연어 문장 나열을 지양하고, 실제 대화 메시지에 포함되었을 가능성이 가장 높은 핵심 표현 위주로 검색어를 작성하십시오."""
        elif max_search_queries is not None and max_search_queries > 1:
            query_instruction = f"""   - 이번 조사 대상 플랫폼은 Slack이며, 허용된 최대 검색 질의 예산은 {effective_max_queries}개입니다.
   - Slack 검색에 적합한 1~{effective_max_queries}개의 구체적이고 간결한 핵심 검색 질의를 작성하십시오.
   - 질의는 중요도 순으로 정렬하고, 각 질의는 과도한 다중 연산자 나열 대신 핵심 키워드 조합으로 구성하십시오.
   - 상호보완적인 검색 축을 구성하십시오:
     * 축 1: 핵심 주제 의미와 정규화된 시기/조건 표현을 결합한 질의
     * 축 2: 사용자의 실제 자연어 표현을 최대한 유지한 질의
     * 축 3: 시기나 세부 수식어를 완화하여 핵심 주제 중심으로 넓게 탐색하는 질의
   - 모든 질의는 Slack 고급 검색 연산자(from:, in:, before:)를 과도하게 사용하지 말고, 실제 대화 메시지에 포함되었을 법한 일반 검색어(Plain standalone Slack query)로 간결하게 작성하십시오.
   - 쿼리 전체를 불필요한 따옴표("...")로 감싸지 마십시오."""
        else:
            query_instruction = """   - 이번 조사 대상 플랫폼은 Slack입니다.
   - Slack 검색에 적합한 1~3개의 구체적이고 간결한 핵심 검색 질의를 중요도 순으로 작성하십시오.
   - 상호보완적인 검색 축을 구성하십시오:
     * 축 1: 핵심 주제 의미와 정규화된 시기/조건 표현을 결합한 질의
     * 축 2: 사용자의 실제 자연어 표현을 최대한 유지한 질의
     * 축 3: 시기나 세부 수식어를 완화하여 핵심 주제 중심으로 넓게 탐색하는 질의
   - 모든 질의는 Slack 고급 검색 연산자(from:, in:, before:)를 과도하게 사용하지 말고, 실제 대화 메시지에 포함되었을 법한 일반 검색어(Plain standalone Slack query)로 간결하게 작성하십시오.
   - 쿼리 전체를 불필요한 따옴표("...")로 감싸지 마십시오."""
    else:
        # Generic targets (non-Slack or unspecified)
        if max_search_queries is not None and max_search_queries == 1:
            query_instruction = """   - 이번 조사에 허용된 최대 검색 질의 예산은 1개입니다. 상호보완 질의를 여러 개 나누어 작성하지 말고, 단독으로 실행했을 때 가장 높은 검색 적합도(Recall & Precision)와 핵심 사실관계 조사 가치를 갖는 독립적인 단일 검색 질의(Standalone Query) 1개만 작성하십시오.
   - 단일 질의는 질문의 핵심 키워드를 가장 잘 포괄하고 자연스러운 검색 매칭이 가능한 최적의 질의여야 합니다."""
        elif max_search_queries is not None and max_search_queries > 1:
            query_instruction = f"""   - 이번 조사에 허용된 최대 검색 질의 예산은 {max_search_queries}개입니다. 1~{max_search_queries}개의 구체적이고 상호보완적인 검색 질의를 작성하십시오.
   - 작성하는 검색 질의는 중요도와 조사 기여도가 높은 순서대로 정렬(Priority Ordering)하십시오. 첫 번째 질의(search_queries[0])는 단독 실행 시 가장 유용하고 포괄적인 핵심 질의여야 합니다."""
        else:
            query_instruction = """   - 2~5개의 구체적이고 상호보완적인 검색 질의를 작성하십시오.
   - 작성하는 검색 질의는 중요도와 조사 기여도가 높은 순서대로 정렬(Priority Ordering)하십시오. 첫 번째 질의(search_queries[0])는 단독 실행 시 가장 유용하고 포괄적인 핵심 질의여야 합니다."""

    return f"""당신은 사내 지식 및 커뮤니케이션 조사를 위한 수석 전략 기획관(Investigation Planner)입니다.
사용자의 질문을 면밀히 분석하여, 핵심 사실관계를 규명하기 위한 정밀하고 다각적인 조사 계획(Investigation Plan)을 수립하십시오.

[작성 가이드라인]
1. objective: 조사의 핵심 목표와 명확한 타깃 결과를 명시하십시오.
2. search_queries (최대 {effective_max_queries}개):
{query_instruction}
   - 각 쿼리의 명확한 의도(purpose)와 대상 채널 힌트(target_channel_hint)를 지정하십시오.
3. temporal_constraint:
   - 사용자의 질문에 특정 연도, 반기, 분기, 월 등 명시적 시간 의도가 있다면 has_temporal_intent=True로 설정하고, start_date 및 end_date를 ISO-8601 형식(YYYY-MM-DD)으로 정규화하십시오.
   - 질문에 시간 의도가 없다면 has_temporal_intent=False로 두고 start_date/end_date는 null로 설정하십시오.
   - source_text에는 질문에서 시간 의도를 나타내는 원본 표현을 기재하십시오.
4. anchor_terms:
   - 질문의 핵심 의미와 고유 주제를 나타내는 핵심 개념 단어/명사구 1~5개를 추출하십시오 (조사나 일반 수식어 제외).
5. questions_to_resolve: 수집된 증거를 통해 최종적으로 답해야 할 핵심 의문 사항 목록(1~3개)을 작성하십시오.
"""


class InvestigationPlanner:
    """사용자 질의를 구조화된 조사 계획(InvestigationPlan)으로 변환하는 기획관."""

    def __init__(self, provider: ModelProvider, config: Optional[LLMConfig] = None):
        self.provider = provider
        self.config = config or LLMConfig()

    async def plan(
        self,
        question: UserQuestion,
        model: Optional[str] = None,
        max_search_queries: Optional[int] = None,
        search_target: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[InvestigationPlan, LLMUsageStats]:
        """사용자 질문에 대해 구조화된 조사 계획을 생성합니다."""
        target_model = model or self.config.planner_model
        user_prompt = f"[조사 질문]\n{question.question}"
        if question.context_hint:
            user_prompt += f"\n\n[배경 힌트]\n{question.context_hint}"

        effective_limit = min(
            max_search_queries or MAX_SLACK_SEARCH_QUERIES, MAX_SLACK_SEARCH_QUERIES
        )
        system_prompt = build_planner_system_prompt(
            max_search_queries=effective_limit,
            search_target=search_target,
        )
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        try:
            plan_obj, usage = await self.provider.generate_structured(
                messages=messages,
                response_schema=InvestigationPlan,
                model=target_model,
                temperature=self.config.temperature,
                **kwargs,
            )

            # Code-side Enforcement: clamp query count <= 3, strip empty/duplicates
            cleaned_queries: list[SearchQuery] = []
            seen_query_texts: set[str] = set()

            for sq in plan_obj.search_queries:
                raw_q = sq.query.strip()
                if not raw_q:
                    continue

                # Compile and normalize with SlackQueryCompiler if targeting Slack
                is_slack = bool(search_target and search_target.strip().lower() == "slack") or True
                compiled_q = raw_q
                if is_slack:
                    try:
                        compiled_res = SlackQueryCompiler.compile(raw_q)
                        compiled_q = compiled_res.compiled_query
                    except Exception as comp_err:
                        logger.warning(f"Query compilation warning for '{raw_q}': {comp_err}")
                        compiled_q = raw_q.strip("\"' ")

                q_key = compiled_q.lower()
                if q_key in seen_query_texts or not compiled_q:
                    continue

                seen_query_texts.add(q_key)
                cleaned_queries.append(
                    SearchQuery(
                        query=compiled_q,
                        purpose=sq.purpose or "관련 대화 검색",
                        target_channel_hint=sq.target_channel_hint,
                    )
                )

                if len(cleaned_queries) >= effective_limit:
                    break

            if not cleaned_queries:
                # Fallback to user question text if all generated queries were empty/invalid
                fallback_compiled = question.question.strip()[:100]
                try:
                    fallback_compiled = SlackQueryCompiler.compile(fallback_compiled).compiled_query
                except Exception:
                    pass
                cleaned_queries.append(
                    SearchQuery(query=fallback_compiled, purpose="사용자 질문 기반 검색")
                )

            plan_obj.search_queries = cleaned_queries

            # Clean and clamp anchor terms (<= 5)
            clean_anchors = []
            seen_anchors: set[str] = set()
            for at in plan_obj.anchor_terms:
                c = at.strip()
                if c and c.lower() not in seen_anchors:
                    seen_anchors.add(c.lower())
                    clean_anchors.append(c)
                if len(clean_anchors) >= 5:
                    break
            plan_obj.anchor_terms = clean_anchors

            logger.info(
                f"[PLANNER_SUCCESS] model={target_model} objective='{plan_obj.objective[:40]}...' "
                f"queries_count={len(plan_obj.search_queries)} temporal_intent={plan_obj.temporal_constraint.has_temporal_intent} "
                f"anchors={plan_obj.anchor_terms} tokens={usage.total_tokens}"
            )
            return plan_obj, usage

        except Exception as err:
            logger.error(f"[PLANNER_FAILED] 조사 계획 수립 실패: {err}")
            raise PlanningError(f"조사 계획 수립 중 오류 발생: {err}") from err

