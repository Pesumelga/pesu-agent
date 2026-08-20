# Pesu Agent - Slack Desktop Background Search & Collector (MVP 0 ~ MVP 3.1.1)

Windows 환경에서 실행 중인 Slack 데스크톱 애플리케이션의 **수명주기 관리(Lifecycle Manager)**, **CDP 백그라운드 탐색 및 Freshness Guard 검색 엔진(Search Engine)**, 및 **검색 결과 원문 검증 & 정밀 상태 복원 문맥 수집기(Context Collector)**를 통합하여 사용자가 다른 작업(Excel, Chrome, PDF 등)을 자유롭게 수행하는 동안에도 백그라운드에서 Slack 메시지 검색 및 대화 수집을 안전하고 정확하게 수행하는 로컬 에이전트 프로젝트입니다.

---

## 📌 주요 목적 및 안전 원칙

### 1. 보안 및 범위 제약 (Strict Guarantees)
- ❌ **금지 항목**:
  - Cookie / Token 조회
  - Local/Session Storage 인증정보 복제
  - Slack 내부 DB / Cache 직접 접근
  - Network Request/Response 패킷 가로채기
  - OS 레벨 마우스 클릭/하드웨어 키보드 타이핑
  - 메시지 작성/전송/리액션 등 데이터 변경
- ⭕ **허용 범위**:
  - 전역 검색창(`top_nav_search__input`) DOM 값 변경 및 Enter 실행
  - 검색 결과 구조화 파싱 및 중복 제거 (`message_fingerprint`)
  - **Freshness Guard (MVP 3.0.1)**: `observed_query`, `result_signature` 검증을 통한 Stale 결과 원천 차단
  - **Context Collector (MVP 3.1)**: Target permalink 이동, Target Message Identity 검증, 전후 대화(최대 20건씩) 및 스레드 정보 수집
  - **State Restoration & Interruption Guard (MVP 3.1.1)**:
    - URL, 채널, `scrollTop`, 뷰포트 메시지 지문 복원 지표 분리
    - 사용자가 Slack을 Foreground로 활성화 시 즉시 작업 양보 (`INTERRUPTED_BY_USER`)
    - 실제 Thread Positive (댓글 수 정밀 일치) 검증

---

## 🚀 실행 방법

### 1. Slack Agent Mode 상태 조회 및 기동

```powershell
# 상태만 조회 (프로세스 변경 없음)
python scripts/start_agent_slack.py --status

# 명시적 정상 재시작 수행
python scripts/start_agent_slack.py --restart
```

### 2. 백그라운드 Slack 검색 실행 (MVP 3.0.1)

```powershell
# 위치 인자 검색
python scripts/search_slack.py "수임"

# 옵션 인자 검색 (최대 스크롤 수 및 출력 경로 지정)
python scripts/search_slack.py --query "테스트" --max-scrolls 3 --output output/slack_search_results.json
```

### 3. 검색 결과 원문 조사 및 전후 대화 문맥 수집 (MVP 3.1 & MVP 3.1.1)

```powershell
# 검색 결과 0번 항목의 원문 검증, 전후 20건 대화 문맥 수집, 및 상태 복원
python scripts/inspect_search_result.py --query "수임" --result-index 0
```

---

## 📊 콘솔 화면 예시 (MVP 3.1.1)

```text
무간섭 및 상태 복원 검증 리포트 (MVP 3.1.1)
┌─────────────────────────────────┬────────────────────────────────────────────────────┬──────────┐
│ 검증 항목                       │ 측정값                                             │ 상태     │
├─────────────────────────────────┼────────────────────────────────────────────────────┼──────────┤
│ Target Message Identity 검증    │ 타깃 메시지 식별자(1786608054914119) 본문 일치 성공 │ 성공     │
│ 1. URL 복원 (url_restored)      │ Before: .../C3R53LYV8 -> Restored: .../C3R53LYV8   │ 일치     │
│ 2. 채널 복원 (conv_restored)    │ Before: noti-heumtax-수임 -> Restored: noti-heumtax │ 일치     │
│ 3. 스크롤 복원 (scroll_restored)│ Before: 1000px -> Restored: 1000px (Diff: 0px)     │ 일치     │
│ 4. 뷰포트 복원 (viewport_rest)  │ 지문 교집합 일치 여부: True                        │ 일치     │
│ 최종 상태 복원 (state_restore)  │ Overall: SUCCESS (Reason: None)                    │ 성공     │
│ 사용자 마우스 간섭              │ 좌표: (839, 412) -> (839, 412)                     │ 0% 간섭  │
│ 사용자 포커스 간섭              │ 활성 창: 'Antigravity IDE' 유지                    │ 0% 간섭  │
│ 스레드 댓글 (Thread Positive)   │ Has Thread: True, Reply Count: 2, ID: 17866080...  │ 발견     │
└─────────────────────────────────┴────────────────────────────────────────────────────┴──────────┘

✓ 문맥 수집 결과가 저장되었습니다: output/slack_result_context.json
```

---

## 🧪 테스트 실행

```powershell
pytest
```
- **45 passed, 2 deselected in 4.10s (100% 통과)**
  - `test_capture_and_restore_view_state_full_success`: 세부 복원 지표 및 SUCCESS 상태 검증
  - `test_partial_success_context_collected_restore_failed`: 복원 실패 시 `PARTIAL_SUCCESS_CONTEXT_COLLECTED_RESTORE_FAILED` 분리
  - `test_user_foreground_interference_prevents_start`: Foreground 활성화 감지 및 `INTERRUPTED_BY_USER` 검증
  - `test_thread_positive_extraction`: 댓글 2개 스레드 파싱 검증