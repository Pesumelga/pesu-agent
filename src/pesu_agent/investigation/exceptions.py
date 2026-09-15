"""Investigation Domain Exceptions."""

from __future__ import annotations


class InvestigationError(Exception):
    """Investigation 파이프라인 기본 예외."""


class ExternalTransmissionBlockedError(InvestigationError):
    """🚨 [HARD_GATE_VIOLATION] Phase 5 보안 경계 위반: 비가상(실제) Slack 데이터의 외부 LLM 전송 시도 차단."""


class EvidenceCitationError(InvestigationError):
    """분석 결과 내 증거 인용(Evidence Citation) 유효성 검증 실패 예외."""


class PlanningError(InvestigationError):
    """조사 계획(Planning) 수립 실패 예외."""


class AnalysisError(InvestigationError):
    """증거 분석(Analysis) 수행 실패 예외."""


class SelfAuthorConstraintIntegrityError(RuntimeError):
    """Raised when a SELF-scoped query violates canonical author integrity."""
