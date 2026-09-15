"""Deterministic Actor Scope Engine (Phase 12.6g).

Provides domain-agnostic, rule-based deterministic actor scope resolution.
Enforces strict boundaries:
- Zero Planner authority over actor constraints (PLANNER_IS_ACTOR_SCOPE_AUTHORITY = false).
- Deterministic runtime resolution (ACTOR_SCOPE_AUTHORITY = DETERMINISTIC_RUNTIME).
- Explicit first-person Korean expressions map to SELF / RESOLVED.
- Implicit personal-retrospective activity expressions map to SELF / RESOLVED.
- Omitted-subject topic/incident queries remain UNSPECIFIED / RESOLVED.
- Explicit non-SELF actors/groups/orgs map to UNRESOLVED (fail closed; unrestricted fallback prohibited).
- Ambiguous actor queries map to AMBIGUOUS (fail closed; never fallback to SELF).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from pesu_agent.investigation.models import (
    ActorResolutionStatus,
    ActorScopeKind,
    ResolvedActorScope,
)

logger = logging.getLogger(__name__)


# 1. Bounded generic first-person Korean expressions
# 내가, 나는, 난, 내, 제가, 저는, 제, 나의, 저의
RE_EXPLICIT_SELF = re.compile(
    r"(?<![가-힣])(내가|나는|난|내|제가|저는|제|나의|저의)(?![가-힣])"
)

# 2. Implicit personal retrospective activity formulations
# 한 일, 뭐 했지, 뭐 했어, 무슨 일 했어, 어떤 업무 했지, 작업한 일, 진행한 일 등
RE_RETROSPECTIVE_ACTIVITY = re.compile(
    r"(?:(?<![가-힣])한\s*일(?![가-힣])|"
    r"(?<![가-힣])한일(?![가-힣])|"
    r"뭐\s*했(?:지|어|는지|나요|습니까)|"
    r"무슨\s*일\s*했(?:지|어|는지|나요|습니까)|"
    r"어떤\s*업무\s*(?:를\s*)?했(?:지|어|는지|나요|습니까)|"
    r"어떤\s*작업\s*(?:을\s*)?했(?:지|어|는지|나요|습니까)|"
    r"(?:작업|진행|개발|작성)한\s*(?:일|내용|내역|업무))"
)

# 3. Topic / System / Incident / Announcement inquiries that must NOT resolve to SELF
# 장애, 공지, 이슈, 릴리즈, 배포, 점검, 소식, 뉴스, 회의록, 무슨 일 있었어 등
RE_TOPIC_NON_PERSONAL = re.compile(
    r"(?:장애|공지(?:사항)?|이슈|릴리즈|배포|점검|소식|뉴스(?:레터)?|회의록|"
    r"무슨\s*일\s*있었(?:어|지|는지|나요|습니까))"
)

# 4. Explicit non-SELF Organization / Group expressions
# 우리 팀, 개발팀에서, 운영팀이, 회사에서, 부서에서, 지점에서 등
RE_EXPLICIT_ORG_GROUP = re.compile(
    r"(?<![가-힣])((?:우리\s*)?[가-힣]*(?:팀|부서|본부|지점|회사|실|그룹|조직|TF|tf)(?:에서|이|가|의|은|는)?)(?![가-힣])"
)

# 5. Explicit Third-Party Person expressions
# 직급/호칭 접미사: 김철수님, 박팀장, 이매니저 등
RE_HONORIFIC_PERSON = re.compile(
    r"(?<![가-힣])([가-힣]{2,4}(?:님|프로|매니저|팀장|책임|선임|수석|대표|이사|기획자|개발자)(?:이|가|은|는|의|과|와)?)(?![가-힣])"
)
# 3인칭 대명사
RE_THIRD_PERSON_PRONOUN = re.compile(
    r"(?<![가-힣])(그사람|그분|다른\s*사람|타인|동료(?:가|는|의|들)?)(?![가-힣])"
)
# 일반 2~4글자 고유명사 주격 (공통 명사 제외)
COMMON_NON_PERSON_NOUNS = {
    "지난주", "이번주", "다음주", "지난달", "이번달", "다음달",
    "오늘", "어제", "내일", "그저께", "모레", "작년", "올해", "내년",
    "장애", "공지", "이슈", "정리", "업무", "진행", "결과", "확인",
    "내용", "보고", "회의", "공유", "일정", "주간", "월간", "시간",
    "개발", "배포", "릴리즈", "점검", "문서", "운영", "시스템", "프로젝트",
    "보고서", "작업", "기안서", "요청", "내역", "기록",
}
# 행동/동사 수식 주어: [가-힣]{2,4}(이|가|은|는) 뒤에 활동 또는 의문 키워드가 오는 구조
# 종결어미(-는가, -는가?, -은가) 오인 방지
RE_PERSON_ACTION_SUBJECT = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})(이|가|은|는)\s+(?:한|작성|진행|수행|개발|요청|공유|보고|전달|올린|뭐|무슨|어떤|어제|지난|이번)"
)


def resolve_actor_scope(text: str) -> ResolvedActorScope:
    """사용자의 자연어 질의로부터 권한 액터 범위를 결정론적으로 해석합니다.

    규칙:
    1. 명시적 1인칭 표현(내가, 나는, 내, 제가 등) 검출:
       - 경쟁하는 다른 명시적 액터가 함께 검출될 경우 AMBIGUOUS (fail closed).
       - 단독 검출 시 SELF / RESOLVED (is_implicit=False).
    2. 명시적 비-SELF 액터(타인, 팀, 회사 등) 검출:
       - 12.6g에서 비-SELF 검색은 지원되지 않으므로 UNRESOLVED (fail closed, unrestricted fallback 금지).
    3. 주어가 생략된 개인 회고/활동 표현(지난주에 한 일, 뭐 했지 등):
       - 토픽/장애/공지 질의가 아니고 경쟁 액터가 없으면 SELF / RESOLVED (is_implicit=True).
    4. 주어가 생략된 일반 토픽 질문(장애 뭐 있었지, 공지 뭐였어 등):
       - UNSPECIFIED / RESOLVED (is_implicit=False).
    5. 모호하거나 상충하는 액터 표현:
       - AMBIGUOUS (fail closed).
    """
    if not text or not text.strip():
        return ResolvedActorScope(
            kind=ActorScopeKind.UNSPECIFIED,
            status=ActorResolutionStatus.NONE,
        )

    clean_text = text.strip()

    # Step 1: Detect explicit first-person expressions
    self_match = RE_EXPLICIT_SELF.search(clean_text)
    explicit_self_expr = self_match.group(1) if self_match else None

    # Step 2: Detect explicit non-SELF group / organization expressions
    org_match = RE_EXPLICIT_ORG_GROUP.search(clean_text)
    explicit_org_expr = org_match.group(1) if org_match else None

    # Step 3: Detect explicit third-party person expressions
    honorific_match = RE_HONORIFIC_PERSON.search(clean_text)
    third_pronoun_match = RE_THIRD_PERSON_PRONOUN.search(clean_text)
    
    competing_person_expr = None
    if honorific_match:
        competing_person_expr = honorific_match.group(1)
    elif third_pronoun_match:
        competing_person_expr = third_pronoun_match.group(1)
    else:
        # Check potential name subject followed by action
        for m in RE_PERSON_ACTION_SUBJECT.finditer(clean_text):
            stem = m.group(1)
            if stem.endswith(("팀", "부서", "본부", "지점", "회사", "실", "그룹", "조직", "TF", "tf")):
                if not explicit_org_expr:
                    explicit_org_expr = f"{stem}{m.group(2)}"
            elif stem not in COMMON_NON_PERSON_NOUNS and not RE_EXPLICIT_SELF.match(stem):
                competing_person_expr = f"{stem}{m.group(2)}"
                break

    # Evaluation Case A: Conflicting explicit actors -> AMBIGUOUS
    competing_actors = [
        a for a in [explicit_org_expr, competing_person_expr] if a is not None
    ]
    if explicit_self_expr and competing_actors:
        logger.info(
            f"👤 [ACTOR_SCOPE_AMBIGUOUS] Conflicting actors detected: self='{explicit_self_expr}' "
            f"competing={competing_actors}"
        )
        return ResolvedActorScope(
            kind=ActorScopeKind.UNSPECIFIED,
            status=ActorResolutionStatus.AMBIGUOUS,
            source_expression=f"{explicit_self_expr} vs {', '.join(competing_actors)}",
            is_implicit=False,
        )

    # Evaluation Case B: Sole explicit SELF
    if explicit_self_expr:
        logger.info(f"👤 [ACTOR_SCOPE_RESOLVED] Explicit SELF detected: '{explicit_self_expr}'")
        return ResolvedActorScope(
            kind=ActorScopeKind.SELF,
            status=ActorResolutionStatus.RESOLVED,
            source_expression=explicit_self_expr,
            is_implicit=False,
        )

    # Evaluation Case C: Unsupported explicit non-SELF actor/group/org
    # Mandatory Correction A: Must NOT fall back to UNSPECIFIED/RESOLVED search.
    # Must map to UNRESOLVED so that orchestrator terminates fail-closed.
    if explicit_org_expr:
        # If the query is just asking general news/events in company (e.g. "이번달 회사에서 무슨 일 있었어?"),
        # check if it's a general topic question
        if RE_TOPIC_NON_PERSONAL.search(clean_text):
            return ResolvedActorScope(
                kind=ActorScopeKind.UNSPECIFIED,
                status=ActorResolutionStatus.RESOLVED,
                source_expression=explicit_org_expr,
                is_implicit=False,
            )
        logger.info(
            f"👤 [ACTOR_SCOPE_UNRESOLVED] Unsupported group/org actor detected: '{explicit_org_expr}'"
        )
        return ResolvedActorScope(
            kind=ActorScopeKind.GROUP,
            status=ActorResolutionStatus.UNRESOLVED,
            source_expression=explicit_org_expr,
            is_implicit=False,
        )

    if competing_person_expr:
        logger.info(
            f"👤 [ACTOR_SCOPE_UNRESOLVED] Unsupported named actor detected: '{competing_person_expr}'"
        )
        return ResolvedActorScope(
            kind=ActorScopeKind.NAMED_ACTOR,
            status=ActorResolutionStatus.UNRESOLVED,
            source_expression=competing_person_expr,
            is_implicit=False,
        )

    # Evaluation Case D: Implicit Personal Retrospective Activity
    # e.g., "지난주에 한 일 정리해줘", "어제 뭐 했지?", "이번달에 한 일"
    activity_match = RE_RETROSPECTIVE_ACTIVITY.search(clean_text)
    is_topic_non_personal = bool(RE_TOPIC_NON_PERSONAL.search(clean_text))

    if activity_match and not is_topic_non_personal:
        act_expr = activity_match.group(0).strip()
        logger.info(
            f"👤 [ACTOR_SCOPE_RESOLVED] Implicit personal retrospective SELF detected: '{act_expr}'"
        )
        return ResolvedActorScope(
            kind=ActorScopeKind.SELF,
            status=ActorResolutionStatus.RESOLVED,
            source_expression=act_expr,
            is_implicit=True,
        )

    # Evaluation Case E: Omitted Subject Topic Questions & General Queries
    # e.g. "지난주 장애 뭐 있었지?", "지난주 공지 뭐였어?", "지난주 지점 이슈 정리해줘"
    # GENERAL_OMITTED_SUBJECT_DEFAULT_SELF = false
    topic_match = RE_TOPIC_NON_PERSONAL.search(clean_text)
    topic_expr = topic_match.group(0).strip() if topic_match else None
    logger.info(
        f"👤 [ACTOR_SCOPE_RESOLVED] Omitted-subject general query -> UNSPECIFIED: '{topic_expr or clean_text[:30]}'"
    )
    return ResolvedActorScope(
        kind=ActorScopeKind.UNSPECIFIED,
        status=ActorResolutionStatus.RESOLVED,
        source_expression=topic_expr,
        is_implicit=False,
    )
