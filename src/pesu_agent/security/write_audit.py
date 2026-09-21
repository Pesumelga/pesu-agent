"""Slack Write & Safety Audit Tracker (MVP 3.4.1).

Slack Evidence Agent의 무간섭/무쓰기(Zero Modification & Zero Write) 안전 지표를
실시간 추적하고 기록하는 감사(Audit) 모듈입니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SlackWriteAuditReport(BaseModel):
    """Slack 쓰기 시도 및 차단 내역 감사 리포트."""

    write_action_requested: int = Field(
        default=0, description="에이전트 또는 테스트에서 요청된 쓰기 동작 수"
    )
    write_action_blocked: int = Field(
        default=0, description="Read-Only Barrier에 의해 성공적으로 차단된 쓰기 동작 수"
    )
    prohibited_low_level_action_attempted: int = Field(
        default=0, description="금지된 Low-Level CDP/DOM 명령 호출 시도 수"
    )
    main_composer_interaction_detected: int = Field(
        default=0, description="메인 채널 Composer 상호작용 감지 수 (목표: 0)"
    )
    thread_composer_interaction_detected: int = Field(
        default=0, description="스레드 패널 Composer 상호작용 감지 수 (목표: 0)"
    )
    send_control_interaction_detected: int = Field(
        default=0, description="전송 버튼/Submit 상호작용 감지 수 (목표: 0)"
    )
    unexpected_slack_write_observed: bool = Field(
        default=False, description="Slack 원격 데이터 쓰기 발생 여부 (목표: False)"
    )

    @property
    def is_completely_safe(self) -> bool:
        """모든 쓰기 지표가 0이고 원격 변경이 0회인지 확인."""
        return (
            self.write_action_requested == 0
            and self.prohibited_low_level_action_attempted == 0
            and self.main_composer_interaction_detected == 0
            and self.thread_composer_interaction_detected == 0
            and self.send_control_interaction_detected == 0
            and not self.unexpected_slack_write_observed
        )


class SlackWriteAuditTracker:
    """단일 실행 세션 동안의 쓰기 시도 감사 추적기."""

    def __init__(self):
        self.report = SlackWriteAuditReport()

    def record_blocked_write(self, reason: str, is_main_composer: bool = False, is_thread_composer: bool = False, is_send_control: bool = False) -> None:
        self.report.write_action_requested += 1
        self.report.write_action_blocked += 1
        if is_main_composer:
            self.report.main_composer_interaction_detected += 1
        if is_thread_composer:
            self.report.thread_composer_interaction_detected += 1
        if is_send_control:
            self.report.send_control_interaction_detected += 1

    def record_prohibited_action(self) -> None:
        self.report.prohibited_low_level_action_attempted += 1

    def record_unexpected_write(self) -> None:
        self.report.unexpected_slack_write_observed = True

    def get_report(self) -> SlackWriteAuditReport:
        return self.report.model_copy()
