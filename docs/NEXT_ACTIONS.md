# 다음 작업

갱신: 2026-10-16. 제품 목표는 자연어 질문에서 추적 가능한 근거 기반 답변까지다. 작업 순서는 Engineering Lead가 관리하며, 아래 승인 조건은 별개다.

현재 GitHub 푸시 차단은 과거 테스트용 가짜 토큰 때문이다. 원본 브랜치를 보존한 **`codex/secret-clean-publish`**가 로컬에 준비·선택됐고, 관련 6개 마스킹 테스트 및 outgoing 이력의 원문 패턴 0개 검증을 통과했다. 원격 반영은 아직 하지 않았다. 승인되면 이 브랜치만 일반 push하며 원본 브랜치/`--all`/force push는 사용하지 않는다. 원본 실행 HEAD와 동결 증거는 재작성하지 않는다.

| 우선순위 | 작업 / 현재 상태 | 선행 조건 | 완료 조건 |
| --- | --- | --- | --- |
| P0 | **실제 검색 결과를 Evidence로 연결하는 다음 단계 준비** | 검색 전환은 SST4에서 통과했다. 오프라인 연결·검증은 자율 진행한다. 새로운 실제 본문 추출·저장 범위는 별도 확인이 필요하다. SST3/SST4는 재실행하지 않는다. | 기존 backend와 EvidenceItem 경로를 재사용해 식별자·permalink·본문 출처 연결과 중복 처리를 검증한다. 실제 실행은 최소 수집 범위와 보관 위치·실패 중단 조건을 먼저 제시한 뒤 승인받는다. thread/외부 provider 호출로 확대하지 않는다. |
| P1 | Evidence → Claim → WorkItem → 근거 답변의 전체 흐름 검증 | 우선 Windows asyncio/AnyIO 테스트 준비 대기를 오프라인에서 구분·해결한다. 실제 데이터 단계는 검증된 Evidence 접근 및 실제 provider 비용/민감 처리의 별도 승인 필요 | 동결 Stage 의미를 유지한 하나의 graph에서 확인된 사실·모순·불확실성이 출처와 연결된다. 오프라인과 실제 데이터/provider 검증을 분리하며, 실제 provider 실패와 근거 부족을 구분하고 전체 흐름 통과 후 제품 UI 연결 범위를 결정한다. |
| P2 | 제품 UI 연결과 사용성·실행 패키지 검증 | 앞선 전체 흐름 검증. 기존 합의 설계의 오프라인 연결은 자율 진행하며, 중대 설계 변경·추가 Live·배포는 별도 승인 | 기존 조사 경로에서 새 근거 답변 경로로 연결되는 동작을 오프라인 검증하고, 사용자가 자연어 질문·진행 상태·출처·실패 이유를 이해할 수 있다. 배포 전 허용된 환경에서 패키지까지 확인한다. |

현재 완료한 오프라인 준비:

- SST3 실제 JS 호출/반환 계약, Host 인터페이스와 one-shot 수명주기 감사·수정: `2a1e58e62704c34a3c9a93c3603109f119b4ce3c` 및 [감사 기록](SST3_EXECUTION_PATH_AUDIT.md).
- 지속 운영 지침과 상태 문서 정비. 기존 PROJECT_STATE의 과거 내용은 아래 역사 기록으로 보존한다.
- SST2 dry-run 실패의 원인은 과거 Live 승인에 종속된 테스트 fixture다. 임시 fixture로 분리하고, stale 승인 거부 검증을 유지한다.
- QSC2 dry-run 테스트 출력도 임시 경로로 격리해 기존 증거 파일 재생성을 방지한다.
- 관련 회귀 테스트 145 PASS. 기존 artifact/output 781개 해시 동일. Production runner·동결 승인·SST3 runner/protocol 해시는 유지됐다.
- 후속 비동기 E2E는 테스트 본문 전 Windows asyncio loopback 준비에서 대기해 제한 시간으로 종료했다. 제품 회귀 여부는 미확정이다. 이번 E2E 통과로 간주하지 않고 P1에서 다루며, 동기식 SST3의 단일 검색 확인과 혼동하지 않는다.
- **SST3 Live 종료:** 승인 HEAD `7fc0675cf08ae4d22447cea4fcfca1ecce745ae4`에서 입력/guarded submit 각 1회, 자동 재시도 0회. 로딩 완료 search view와 결과 DOM 20개가 관측됐지만 지역화 제목을 query와 비교해 공식 결과는 `FAIL_CLOSED / TRANSITION_TIMED_OUT`. 실제 authorization/marker/live trace를 보존했다. 본문·스레드·LLM 호출은 없다. 상세 identity·해시·관측은 PROJECT_STATE의 Live 결과를 따른다.
- **검색어 추출 수정 / SST4 완료:** Production observer만 최소 수정, 정확한 query·모호함 거부·로딩 조건 유지. 실제 JS 및 전체 단일 제출 경로를 포함한 관련 회귀 90 PASS. 커밋 `7ab6157b74404dccf451a50ef02e03fd4ce59a35`에서 SST4 단일 Live 실행 **QUALIFICATION_PASS**. 입력/제출 각 1회, 약914.89ms에 정확한 query·로딩 완료 화면 확인. 재시도/본문/스레드/LLM 호출 없음. 실행 전 증거785개 해시 동일. 승인/marker/trace를 보존했다.

**SST3/SST4는 재실행하지 않는다.** 사용자가 위임한 새 Live는 결과 화면 확인만 하는 SST4 한 번으로 완료됐다. 다음은 실제 Evidence 연결을 위한 오프라인 준비이며, 추가 본문 수집·저장 범위·외부 전송·provider 비용·동결 Stage 의미 변경은 여전히 별도 승인 대상이다. 이전 실패 기록이나 승인 identity를 다시 쓰지 않는다.
