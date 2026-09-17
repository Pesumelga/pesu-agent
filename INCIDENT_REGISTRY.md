# Slack Agent Accidental Write Incident Registry

## 사고 개요

pesu-agent Slack Evidence Collector 테스트 과정에서 Agent가 사용자 의도 없이
실제 Slack 채널에 메시지를 발송한 사고에 대한 영구 기록입니다.

## 확정 사고 내역

| 시각 (KST) | 내용 | 유형 | 누적 발생 건수 |
| :--- | :--- | :--- | :--- |
| 2026-08-21 08:52 | "수임" | Top-Level Channel Message | 1건 |
| 2026-08-21 08:59 | "수임" | Top-Level Channel Message | 2건 |
| 2026-09-17 00:30 | "신규 Office 영업지원 조회 기능 어디까지 진행됐고 누가 무엇을 결정했으며 무엇이 막혀" | Slackbot DM Message Send (Typeahead Intercept) | 3건 (Slackbot 1건) |

```
incident_attribution = CONFIRMED_AGENT_WRITE
exact_runtime_path_confirmed = true
runtime_path = webview_slack_search.py → JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER → SearchInputBox.onEnter() → getSelectedSuggestion() defaults to suggestions[0] (Slackbot)
root_cause_confidence = ABSOLUTE_CERTAINTY
confirmed_accidental_agent_writes = 4
cumulative_total = 4
slackbot_dm_write_occurred = true
```

## 근본 원인

1. **2026-08-21 Incident**:
   검색 입력 과정에서 Global Search Input이 DOM에 렌더링되지 않았을 때,
   `[data-qa="texty_input"]` (Channel Message Composer)를 검색창으로 오인하여
   쿼리 텍스트("수임")를 입력하고 Enter 키를 디스패치하여 실제 메시지가 발송됨.

2. **2026-09-17 Incident (Task 11R10D/E Case B Q2)**:
   자연어 형태의 긴 쿼리(>= 4 단어) 입력 시 Slack 클라이언트 머신러닝 분류기(`classifyOmniswitcherScenario`)가
   `Slackbot에게 질문하기` (`is_type_slackbot: true`, `type: "querySlackbot"`)를 `suggestions[0]`으로 최상단 배치하고
   일반 키워드 검색을 `suggestions[1]`로 강등시킴.
   `autocompleteSelectedIndex`가 `null`인 상태에서 무방비한 `SearchInputBox.onEnter()`가 호출되자,
   Slack 내부 `getSelectedSuggestion()` 로직이 `suggestions[0]`(Slackbot)을 기본 선택하여 Slackbot DM에 쿼리를 게시함.

## 대응 조치

1. **Phase 1 — Read-Only Barrier 구축 (완료)**
   - `texty_input` 계열을 검색창으로 채택하는 경로 영구 제거
   - `SearchInputNotVerifiedError` / `ReadOnlyViolationError`로 Fail-Closed
   - DOM Capture-Phase Guard: Composer/Send/Form 요소에 대한 모든 이벤트 차단
   - Composer 요소 `contenteditable=false` + `pointerEvents=none` 비활성화

2. **Task 11R10F — Fail-Closed Safe Search Suggestion Containment & Activation (완료)**
   - **Authority Contract**: suggestions 전체에서 엄격한 Exact Safe Search Candidate가 **정확히 1개(EXACTLY ONE)**일 때만 진행. 0개 또는 2개 이상이면 즉시 Fail-Closed.
   - **Exact Positive Allowlist**: `is_type_query_completion === true && type === "queryUser" && suggestion_sub_type === "query-echo"` + Unsafe flags 일체 부재.
   - **Generic onEnter 영구 차단**: `safeIndex === 0` 여부와 무관하게 `SearchInputBox.onEnter()`를 검색 Authority로 사용 금지 (`GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH = false`).
   - **원자적 TOCTOU 방지**: 단일 원자적 JS 클로저에서 snapshot → classify → unique-safe check → candidate identity 재검증 → safe activation 수행.
   - **검증된 UI 액션 실행**: DOM `[role="option"][data-index="${safeIndex}"]` click 이벤트 및 React Fiber `onAccept(false, false)`만으로 검색 실행.
   - **도달 가능 경로 전수 차단**: `webview_slack_search.py` 및 `slack_cdp.py` 양대 경로 모두 동일한 Fail-Closed Guard 적용 (`UNGUARDED_REACHABLE_PATH_COUNT = 0`).

3. **영구 회귀 테스트**
   - `test_regression_accidental_top_level_slack_message_incident` (영구 유지)
   - `test_regression_accidental_top_level_slack_message_incident_composer_detected` (영구 유지)
   - `test_slack_webview_search_safety_guard.py` 18종 안전성 테스트 스위트 (영구 유지)
   - `test_reachable_slack_search_submission_paths_audit` 전수 감사 테스트 (영구 유지)

## 갱신 이력

- 2026-08-21: 초기 사고 등록 (2건, 누적 3건)
- 2026-08-21: Phase 1 Read-Only Barrier 완료
- 2026-08-21: Phase 4 Closure — BootstrapCapability 도입, AST 범위 확대
- 2026-09-17: Task 11R10D/E Slackbot Typeahead DM 오발송 사고 등록 (누적 4건, Slackbot 1건)
- 2026-09-17: Task 11R10F Safe Search Suggestion Containment & Explicit Safe Activation 구현 완료
- 2026-09-17: Task 11R10J Case B Historical Self-Contamination Adjudication & Diagnostic Suite Retirement

## Task 11R10J — Case B Diagnostic Contamination Adjudication & Suite Retirement

```
CASE_B_DIAGNOSTIC_VALIDITY = INVALIDATED_BY_HISTORICAL_SELF_CONTAMINATION
CASE_B_LIVE_REPLAY = PROHIBITED
ORIGINAL_A_B_C_DIAGNOSTIC_SUITE_STATUS = RETIRED_DUE_CASE_B_SELF_CONTAMINATION
A_B_C_FULL_DIAGNOSTIC_SUITE_COMPLETED = false
```

### Formal Adjudication Scope
Task 11R10I established that in Case B, all five selected evidence items originated from historical accidental agent-generated Slackbot/query echo messages containing only the exact Case B question text (originating in DM channel `D07RA9YDE8G`).
- `CASE_B_SEMANTIC_PIPELINE_DEFECT = false`
- `CASE_B_DIAGNOSTIC_CONTAMINATION = CONFIRMED`
- `SEMANTIC_PIPELINE_REMEDIATION_REQUIRED = false`
- `EVIDENCE_ASSESSOR_REMEDIATION_REQUIRED = false`
- `CLAIM_VERIFIER_REMEDIATION_REQUIRED = false`
- `FROZEN_STAGE_REMEDIATION_REQUIRED = false`
- `PROVIDER_REMEDIATION_FOR_CASE_B_FAILURE = false`
- `CASE_B_RETRIEVAL_ADEQUACY = UNRESOLVED`

Case B is permanently retired as a clean diagnostic case and must never be replayed.
Predeclared Case C is executed independently exactly once in Task 11R10J.

