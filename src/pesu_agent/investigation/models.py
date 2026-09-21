"""Investigation Domain Data Models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class ActorScopeKind(str, Enum):
    """액터 범위 종류."""

    SELF = "SELF"
    UNSPECIFIED = "UNSPECIFIED"
    NAMED_ACTOR = "NAMED_ACTOR"
    GROUP = "GROUP"
    ORGANIZATION = "ORGANIZATION"


class ActorResolutionStatus(str, Enum):
    """액터 범위 해석 상태."""

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    NONE = "NONE"


class InvestigationStopReason(str, Enum):
    """조사 종료 사유."""

    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    NO_NEW_EVIDENCE = "NO_NEW_EVIDENCE"
    MAX_SEARCH_ROUNDS_REACHED = "MAX_SEARCH_ROUNDS_REACHED"
    QUERY_BUDGET_REACHED = "QUERY_BUDGET_REACHED"
    LLM_BUDGET_REACHED = "LLM_BUDGET_REACHED"
    ANALYSIS_INCONCLUSIVE = "ANALYSIS_INCONCLUSIVE"
    ERROR = "ERROR"


class ResolvedActorScope(BaseModel):
    """결정론적 런타임에 의해 확정된 권한 액터 범위 (Authoritative Resolved Actor Scope).

    불변(frozen) 시맨틱을 보장하며, 공개/영속 모델에 Canonical Slack ID는 포함되지 않습니다.
    """

    model_config = ConfigDict(frozen=True)

    kind: ActorScopeKind = Field(
        default=ActorScopeKind.UNSPECIFIED,
        description="해석된 액터 범위 종류 (SELF, UNSPECIFIED, NAMED_ACTOR 등)",
    )
    status: ActorResolutionStatus = Field(
        default=ActorResolutionStatus.NONE,
        description="해석 상태 (RESOLVED, AMBIGUOUS, UNRESOLVED, NONE)",
    )
    source_expression: Optional[str] = Field(
        default=None,
        description="해석 대상이 된 원본 액터/활동 표현 문자열",
    )
    is_implicit: bool = Field(
        default=False,
        description="암시적 개인 회고/활동 질의 여부 (True: 생략된 주어 회고 질의, False: 명시적 표현)",
    )
    matched_author_name: Optional[str] = Field(
        default=None,
        description="매칭된 권한 액터/작성자 명칭 (WorkItem synthesis 호환 필드)",
    )



class TemporalConstraint(BaseModel):
    """자연어 질문에서 추출된 시간적/시기적 제약 조건."""

    has_temporal_intent: bool = Field(
        default=False, description="사용자 질문에 명시적 시간/시기/기간 의도가 포함되어 있는지 여부"
    )
    start_date: Optional[str] = Field(
        default=None, description="ISO-8601 시작 일자 (예: '2026-07-01'), 미지정 시 null"
    )
    end_date: Optional[str] = Field(
        default=None, description="ISO-8601 종료 일자 (예: '2026-12-31'), 미지정 시 null"
    )
    source_text: Optional[str] = Field(
        default=None, description="질문에서 시간 의도를 나타내는 원본 표현 (예: '26년 하반기')"
    )


class TemporalStrictness(str, Enum):
    """시간 제약의 엄격성 구분."""

    STRICT = "STRICT"
    SOFT = "SOFT"
    NONE = "NONE"


class TemporalResolutionKind(str, Enum):
    """시간 제약의 출처 및 권한 계층."""

    USER_EXPLICIT_ABSOLUTE = "USER_EXPLICIT_ABSOLUTE"
    USER_RELATIVE_DETERMINISTIC = "USER_RELATIVE_DETERMINISTIC"
    PLANNER_INFERRED = "PLANNER_INFERRED"
    NONE = "NONE"


class TemporalResolutionStatus(str, Enum):
    """시간 제약 해석 상태."""

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


class ResolvedTemporalScope(BaseModel):
    """결정론적 런타임에 의해 확정된 권한 시계열 범위 (Authoritative Resolved Temporal Scope).

    반열린 구간 [start_at, end_at)을 사용하며 불변(frozen) 시맨틱을 보장합니다.
    """

    model_config = ConfigDict(frozen=True)

    intent_present: bool = Field(
        default=False, description="사용자 질문에 시간 의도가 존재하는지 여부"
    )
    strictness: TemporalStrictness = Field(
        default=TemporalStrictness.NONE, description="시간 제약의 엄격성 (STRICT / SOFT / NONE)"
    )
    status: TemporalResolutionStatus = Field(
        default=TemporalResolutionStatus.NONE,
        description="시간 해석 상태 (RESOLVED / UNRESOLVED / AMBIGUOUS / NONE)",
    )
    start_at: Optional[datetime] = Field(
        default=None, description="반열린 구간 시작 시각 (포함, inclusive)"
    )
    end_at: Optional[datetime] = Field(
        default=None, description="반열린 구간 종료 시각 (미포함, exclusive)"
    )
    timezone: str = Field(
        default="Asia/Seoul", description="해석에 사용된 명시적 타임존 문자열"
    )
    source_expression: Optional[str] = Field(
        default=None, description="해석 대상이 된 원본 시간 표현 문자열"
    )
    resolution_kind: TemporalResolutionKind = Field(
        default=TemporalResolutionKind.NONE, description="해석 출처 및 권한 계층"
    )

    def is_in_range(self, dt: Optional[datetime]) -> bool:
        """반열린 구간 [start_at, end_at) 소속 여부 판정.

        NAIVE_TIMESTAMP_ASSUMED_SCOPE_TIMEZONE = false:
        dt가 None이거나 naive(tzinfo=None)인 경우 unproven으로 간주하여 False를 반환합니다.
        """
        if dt is None or dt.tzinfo is None:
            return False
        if not self.intent_present or not self.start_at or not self.end_at:
            return True
        scope_tz = self.start_at.tzinfo
        norm_dt = dt.astimezone(scope_tz)
        return self.start_at <= norm_dt < self.end_at


class TemporalRelation(str, Enum):
    """후보 증거와 시간 제약 조건 간의 범주형 관계."""

    DIRECT_TEMPORAL_MATCH = "DIRECT_TEMPORAL_MATCH"
    POSSIBLE_TEMPORAL_MATCH = "POSSIBLE_TEMPORAL_MATCH"
    TEMPORAL_UNKNOWN = "TEMPORAL_UNKNOWN"
    EXPLICIT_TEMPORAL_MISMATCH = "EXPLICIT_TEMPORAL_MISMATCH"




class RetrievalCoverage(str, Enum):
    """검색된 최종 증거들의 커버리지 상태."""

    STRONG = "STRONG"
    LIMITED = "LIMITED"
    NO_DIRECT_MATCH = "NO_DIRECT_MATCH"


class ThreadContextStatus(str, Enum):
    """스레드 컨텍스트 확장 수집 상태."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class ParentContextState(str, Enum):
    """Parent message body completeness state (orthogonal to ThreadContextStatus)."""

    SEARCH_SNIPPET = "SEARCH_SNIPPET"
    CANONICAL_PARENT = "CANONICAL_PARENT"
    HYDRATION_FAILED = "HYDRATION_FAILED"


class ThreadReplyItem(BaseModel):
    """스레드 내부 단일 댓글 항목 (Canonical Sub-citation 대상)."""

    reply_id: str = Field(description="스레드 내 하위 인용 ID (예: 'R1', 'R2')")
    author: Optional[str] = Field(default=None, description="댓글 작성자")
    timestamp: Optional[str] = Field(default=None, description="댓글 작성 시각 원본")
    text: str = Field(description="댓글 본문 텍스트")
    permalink: str = Field(description="해당 댓글의 Canonical 검증된 Slack 퍼머링크")
    channel_id: str = Field(description="채널 ID")
    message_ts: str = Field(description="댓글 자체의 message_ts (API 형식)")
    thread_ts: str = Field(description="스레드 루트의 thread_ts (API 형식)")


class RetrievalPlan(BaseModel):
    """Retrieval v2 다중 쿼리 및 제약 조건 파라미터 (단일 소스 연계용 뷰)."""

    queries: list[str] = Field(
        default_factory=list,
        description="1~3개의 독립 실행용 Slack 검색 질의",
    )
    temporal_constraint: TemporalConstraint = Field(
        default_factory=TemporalConstraint,
        description="시간/기간 조건",
    )
    anchor_terms: list[str] = Field(
        default_factory=list,
        description="질문에서 추출된 핵심 고유 개념/명사 앵커 목록 (1~5개)",
    )


class UserQuestion(BaseModel):
    """사용자의 조사 질의 입력."""

    question: str = Field(description="조사하고자 하는 사용자의 자연어 질문")
    context_hint: Optional[str] = Field(
        default=None, description="조사에 도움이 되는 선택적 배경 힌트 (예: 관련 팀, 대화 시기 등)"
    )


class SearchQuery(BaseModel):
    """단일 검색 질의 정의 (Planner가 생성하는 대상/의도 중심 후보 쿼리).

    Domain Contract:
    - query: Planner가 생성한 의미적/대상 지향 검색 후보 문자열 (Generic Candidate Query).
    - 특정 백엔드 실행 직전 각 SearchBackend/Compiler(예: SlackQueryCompiler)에 의해
      백엔드 친화적인 실행 준비 쿼리(Compiled Query)로 정규화 및 검증됩니다.
    """

    query: str = Field(description="검색 키워드 또는 검색식")
    purpose: str = Field(description="이 검색 질의를 수행하는 명확한 목적 및 의도")
    target_channel_hint: Optional[str] = Field(
        default=None, description="검색 대상 채널 힌트 (선택 사항)"
    )


class InvestigationPlan(BaseModel):
    """구조화된 조사 계획 (Planner 출력물).

    Single Source of Truth 원칙 (DUPLICATED_RETRIEVAL_PLAN_STATE = false):
    - search_queries, temporal_constraint, anchor_terms를 본 모델에서 직접 관리합니다.
    """

    objective: str = Field(description="조사의 핵심 목표 및 해결 과제 요약")
    search_queries: list[SearchQuery] = Field(
        description="단계별 검색 질의 목록 (1~3개)", min_length=1
    )
    questions_to_resolve: list[str] = Field(
        description="수집된 증거를 통해 해결해야 할 핵심 질문 목록", min_length=1
    )
    temporal_constraint: TemporalConstraint = Field(
        default_factory=TemporalConstraint,
        description="질문에서 식별된 시간적 제약 조건",
    )
    anchor_terms: list[str] = Field(
        default_factory=list,
        description="질문의 핵심 의미를 담은 앵커 단어/명사구 목록",
    )
    plan_version: int = Field(default=2, description="계획 버전")

    @property
    def retrieval_plan(self) -> RetrievalPlan:
        """단일 상태로부터 RetrievalPlan 뷰 동적 생성."""
        return RetrievalPlan(
            queries=[sq.query for sq in self.search_queries],
            temporal_constraint=self.temporal_constraint,
            anchor_terms=self.anchor_terms,
        )


class EvidenceItem(BaseModel):
    """표준화된 개별 증거 항목 (Slack DOM 내부 구조와 분리된 순수 도메인 엔티티)."""

    evidence_id: str = Field(description="고유 증거 식별자 (예: E1, E2, ...)")
    source: str = Field(description="증거 출처 (예: slack_channel, synthetic_fixture)")
    author: Optional[str] = Field(default=None, description="메시지/문서 작성자")
    author_id: Optional[str] = Field(
        default=None,
        description="Canonical Slack author member ID (e.g. U...)",
        exclude=True,
        repr=False,
    )
    author_relation: Optional[str] = Field(
        default=None,
        description="Author relation to investigated actor (SELF, OTHER, UNKNOWN)",
    )
    timestamp: Optional[str] = Field(default=None, description="작성 시각")
    text: str = Field(description="메시지 또는 문서 내용 본문")
    channel: Optional[str] = Field(default=None, description="발생 채널명")
    message_identifier: Optional[str] = Field(
        default=None, description="결정론적 메시지 식별자 해시"
    )
    permalink: Optional[str] = Field(
        default=None, description="메시지 퍼머링크 URL"
    )
    thread_identifier: Optional[str] = Field(
        default=None, description="스레드 식별자 또는 thread_ts"
    )
    has_thread: bool = Field(
        default=False, description="스레드 댓글 존재 여부 (검색 메타데이터 기준)"
    )
    has_attachments: bool = Field(default=False, description="파일 첨부 포함 여부")
    attachment_count: int = Field(default=0, description="첨부된 파일 수")
    attachment_names: list[str] = Field(
        default_factory=list, description="첨부 파일명 목록"
    )
    query_hit_count: int = Field(
        default=1, description="다중 검색 쿼리에서 hit된 횟수"
    )
    temporal_relation: Optional[str] = Field(
        default=None, description="종합 시간적 일치 관계 (DIRECT_TEMPORAL_MATCH 등)"
    )
    message_timestamp_relation: Optional[str] = Field(
        default=None, description="메시지 작성 시각 기준 시간 관계"
    )
    mentioned_date_relation: Optional[str] = Field(
        default=None, description="본문 명시 일자 기준 시간 관계"
    )
    anchor_match_count: int = Field(
        default=0, description="일치한 질문 앵커 용어 수"
    )
    thread_replies: list[ThreadReplyItem] = Field(
        default_factory=list, description="스레드 하위 댓글 목록"
    )
    thread_context_status: str = Field(
        default="NOT_ATTEMPTED",
        description="스레드 컨텍스트 확장 상태 (NOT_ATTEMPTED, COMPLETE, PARTIAL, UNAVAILABLE)",
    )
    parent_context_state: ParentContextState = Field(
        default=ParentContextState.SEARCH_SNIPPET,
        description="Parent message context completeness state (SEARCH_SNIPPET, CANONICAL_PARENT, HYDRATION_FAILED)",
    )


class EvidencePackage(BaseModel):
    """조사 결과 취합된 표준 증거 패키지."""

    package_id: str = Field(description="패키지 고유 식별자")
    query: str = Field(description="대표 검색어 또는 조사 주제")
    evidence_items: list[EvidenceItem] = Field(
        default_factory=list, description="수집된 증거 항목 목록"
    )
    total_items: int = Field(default=0, description="총 증거 항목 수")
    created_at: str = Field(description="패키지 생성 일시 (ISO-8601)")
    is_synthetic: bool = Field(
        default=False,
        description="가상(Synthetic) 데이터 여부. Phase 5에서는 True인 경우에만 외부 LLM 전송 허용",
    )
    retrieval_coverage: Optional[str] = Field(
        default=None,
        description="검색 커버리지 평가 (STRONG, LIMITED, NO_DIRECT_MATCH)",
    )
    thread_context_status: Optional[str] = Field(
        default=None,
        description="스레드 확장 수집 상태 (NOT_ATTEMPTED, COMPLETE, PARTIAL, UNAVAILABLE)",
    )
    temporal_scope: Optional[ResolvedTemporalScope] = Field(
        default=None,
        description="확정된 조사 대상 시계열 범위 (Authoritative Resolved Temporal Scope)",
    )
    actor_scope: Optional[ResolvedActorScope] = Field(
        default=None,
        description="확정된 조사 대상 액터 범위 (Authoritative Resolved Actor Scope)",
    )


class ConfirmedFact(BaseModel):
    """증거를 통해 입증된 단일 확정 사실 (Claim-level)."""

    statement: str = Field(description="확정된 사실 진술문")
    supporting_evidence_ids: list[str] = Field(
        description="해당 사실을 뒷받침하는 증거 ID 목록 (예: ['E1', 'E3'])", min_length=1
    )


class DisputedPoint(BaseModel):
    """당사자 간 주장이 엇갈리는 단일 쟁점 (Claim-level)."""

    point: str = Field(description="쟁점 내용 요약")
    sides: dict[str, str] = Field(
        description="당사자/입장별 주장 내용 (예: {'A': '기존 기준 변경 요청', 'B': '기존 룰 준수 필요'})"
    )
    supporting_evidence_ids: list[str] = Field(
        description="각 주장을 뒷받침하는 증거 ID 목록", min_length=1
    )


class Contradiction(BaseModel):
    """증거 간 명백한 모순 또는 정책 충돌 (Claim-level)."""

    statement: str = Field(description="발견된 모순 또는 충돌 상황 설명")
    conflicting_evidence_ids: list[str] = Field(
        description="상호 충돌하는 증거 ID 목록 (예: ['E2', 'E4'])", min_length=2
    )


class ConclusionStatus(str):
    CONFIRMED = "CONFIRMED"
    DISPUTED = "DISPUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceSufficiency(str):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class AnalysisResult(BaseModel):
    """종합 증거 분석 결과 (Analyzer 출력물)."""

    summary: str = Field(description="전체 조사 결과 요약")
    confirmed_facts: list[ConfirmedFact] = Field(
        default_factory=list, description="증거로 입증된 확정 사실 목록"
    )
    disputed_points: list[DisputedPoint] = Field(
        default_factory=list, description="주요 쟁점 및 당사자별 주장 목록"
    )
    contradictions: list[Contradiction] = Field(
        default_factory=list, description="증거 간 발견된 모순 및 정책 충돌 목록"
    )
    assessment: str = Field(description="상황에 대한 균형 잡힌 종합 판단 및 평가")
    uncertainty: str = Field(
        description="현재 확보된 증거만으로는 판단하기 어려운 불확실성 요인"
    )
    missing_information: list[str] = Field(
        default_factory=list, description="정확한 판단을 위해 추가로 확인해야 할 정보"
    )
    suggested_followups: list[str] = Field(
        default_factory=list, description="추천 후속 조치 또는 추가 조사 항목"
    )
    cited_evidence_ids: list[str] = Field(
        default_factory=list, description="분석에 인용된 모든 유효 증거 ID 목록"
    )
    hallucinated_evidence_ids: list[str] = Field(
        default_factory=list,
        description="실제 EvidencePackage에 존재하지 않는데 모델이 생성해 낸 환각 ID 목록 (0건이어야 정상)",
    )
    conclusion_status: Optional[str] = Field(
        default=None,
        description="결론 확정 상태 (CONFIRMED, DISPUTED, INCONCLUSIVE)",
    )
    responsibility_determinable: Optional[bool] = Field(
        default=None,
        description="책임 소재 또는 결론의 귀속을 명확히 판정 가능한지 여부 (불충분 시 False)",
    )
    evidence_sufficiency: Optional[str] = Field(
        default=None,
        description="확보된 증거의 충분성 (SUFFICIENT, PARTIAL, INSUFFICIENT)",
    )
    finding_type: Optional[str] = Field(
        default=None,
        description="증거 기반 결론 유형 (POSITIVE_SUPPORTED, EXPLICIT_NEGATIVE_SUPPORTED, INCONCLUSIVE)",
    )
    retrieval_coverage: Optional[str] = Field(
        default=None,
        description="분석에 사용된 검색 커버리지 상태 (STRONG, LIMITED, NO_DIRECT_MATCH)",
    )
    thread_context_status: Optional[str] = Field(
        default=None,
        description="분석에 반영된 스레드 컨텍스트 확장 상태",
    )

