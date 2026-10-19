# 다음 작업

갱신: 2026-10-19. 제품 목표는 자연어 질문에서 추적 가능한 근거 기반 답변까지다. 작업 순서는 Engineering Lead가 관리하며, 아래 승인 조건은 별개다.

GitHub 푸시 차단은 테스트용 가짜 토큰의 과거 이력을 정리해 해소했다. 사용자 승인 후 **`codex/secret-clean-publish`**만 일반 push했고 GitHub 수락과 원격 ref 일치를 확인했다. 관련 마스킹 테스트 6 PASS와 원문 패턴 0개 검증도 완료됐다. 원본 로컬 브랜치·원격 main·동결 증거는 유지됐다. 원본 브랜치/`--all`/force push는 사용하지 않는다. PR 생성·병합은 별도 작업이며 이번에는 수행하지 않았다.

| 우선순위 | 작업 / 현재 상태 | 선행 조건 | 완료 조건 |
| --- | --- | --- | --- |
| P0 | **수정된 검색 경로의 실제 Evidence 연결 검증 승인 경계** | 지난 실제 Evidence 시도는 제출 1회·본문 추출 0회로 FAIL_CLOSED 종료됐다. 추출 진입 조건과 JS 내 재검증을 보강했고 관련 회귀 125 PASS다. 과거 시도는 재실행하지 않는다. 새 실제 검색은 명시적 승인이 필요하다. | 승인 후 같은 query 검색 최대 1회·새 결과 최대 5개의 본문/출처를 로컬에만 저장해 실제 backend→Evidence seed graph 연결을 확인한다. 기존 화면 최대 10개 메모리 비교 범위를 명시한다. 실패 자동 재시도 없이 보존하며 thread/첨부/LLM은 제외한다. 다음 기록에는 본문 없는 gate 상태와 typed failure_code를 남겨 이전 진단 공백을 막는다. |
| P1 | Evidence → Claim → WorkItem → 근거 답변의 전체 흐름 검증 | Windows asyncio 준비 대기는 실행 환경 문제로 구분됐고 기존 비동기 E2E 8개가 통과했다. 실제 데이터 단계는 검증된 Evidence 접근 및 실제 provider 비용/민감 처리의 별도 승인 필요 | 동결 Stage 의미를 유지한 하나의 graph에서 확인된 사실·모순·불확실성이 출처와 연결된다. 오프라인과 실제 데이터/provider 검증을 분리하며, 실제 provider 실패와 근거 부족을 구분하고 전체 흐름 통과 후 제품 UI 연결 범위를 결정한다. |
| P2 | 제품 UI 연결과 사용성·실행 패키지 검증 | 앞선 전체 흐름 검증. 기존 합의 설계의 오프라인 연결은 자율 진행하며, 중대 설계 변경·추가 Live·배포는 별도 승인 | 기존 조사 경로에서 새 근거 답변 경로로 연결되는 동작을 오프라인 검증하고, 사용자가 자연어 질문·진행 상태·출처·실패 이유를 이해할 수 있다. 배포 전 허용된 환경에서 패키지까지 확인한다. |

현재 완료한 오프라인 준비:

- SST3 실제 JS 호출/반환 계약, Host 인터페이스와 one-shot 수명주기 감사·수정: `2a1e58e62704c34a3c9a93c3603109f119b4ce3c` 및 [감사 기록](SST3_EXECUTION_PATH_AUDIT.md).
- 지속 운영 지침과 상태 문서 정비. 기존 PROJECT_STATE의 과거 내용은 아래 역사 기록으로 보존한다.
- SST2 dry-run 실패의 원인은 과거 Live 승인에 종속된 테스트 fixture다. 임시 fixture로 분리하고, stale 승인 거부 검증을 유지한다.
- QSC2 dry-run 테스트 출력도 임시 경로로 격리해 기존 증거 파일 재생성을 방지한다.
- 관련 회귀 테스트 145 PASS. 기존 artifact/output 781개 해시 동일. Production runner·동결 승인·SST3 runner/protocol 해시는 유지됐다.
- 이전 비동기 E2E 준비 대기는 제한된 Windows 실행 환경의 asyncio loopback에서 재현됐다. 허용된 실행 환경에서는 최소 재현과 기존 E2E 8개가 통과했다. 제품 event loop 수정은 없다.
- 실제 검색 매핑에서 `EvidenceItem.permalink`가 누락돼 graph·출처 카드에 링크가 사라지는 실패를 재현하고 한 줄 수정했다. 합성 검색→Evidence→Claim→WorkItem→답변·출처 카드 및 관련 회귀 **52 PASS**. URL 없는 결과의 식별자를 링크로 만들지 않는 검증도 포함한다. 기존 artifact/output **788개 해시 동일**.
- 위 범위로 사용자 승인 후 실제 Evidence 연결을 1회 시도했다. 안전한 검색 제출 1회 뒤 FAIL_CLOSED, 본문 추출 0회·이전 본문 읽기 0개·재시도 없음. 788개 기존 증거 해시는 유지됐다. 로컬 `.runtime/slack-evidence-validation/`에 시도와 report·원본 스크립트를 보존했다. 상세 원인은 마지막 surface/오류 메시지 미기록으로 단정하지 않는다.
- 합성 Host 재현에서 query 미확정 상태에도 기존 backend가 추출을 시도하는 경로를 확인했다. 실행 wrapper는 이를 막았다. 이 경로를 2026-10-19 Production에서 보강했다. 정확한 query/unique mounted view/loading clear와 JS 내 원자적 재검증, timeout forensic 추출 제거, failed-probe snapshot 재사용 차단을 검증했다. 관련 회귀 **125 PASS**, 기존 증거 및 이전 Live 기록 **794개 해시 동일**. 새 Live 승인은 아직 없다.
- **SST3 Live 종료:** 승인 HEAD `7fc0675cf08ae4d22447cea4fcfca1ecce745ae4`에서 입력/guarded submit 각 1회, 자동 재시도 0회. 로딩 완료 search view와 결과 DOM 20개가 관측됐지만 지역화 제목을 query와 비교해 공식 결과는 `FAIL_CLOSED / TRANSITION_TIMED_OUT`. 실제 authorization/marker/live trace를 보존했다. 본문·스레드·LLM 호출은 없다. 상세 identity·해시·관측은 PROJECT_STATE의 Live 결과를 따른다.
- **검색어 추출 수정 / SST4 완료:** Production observer만 최소 수정, 정확한 query·모호함 거부·로딩 조건 유지. 실제 JS 및 전체 단일 제출 경로를 포함한 관련 회귀 90 PASS. 커밋 `7ab6157b74404dccf451a50ef02e03fd4ce59a35`에서 SST4 단일 Live 실행 **QUALIFICATION_PASS**. 입력/제출 각 1회, 약914.89ms에 정확한 query·로딩 완료 화면 확인. 재시도/본문/스레드/LLM 호출 없음. 실행 전 증거785개 해시 동일. 승인/marker/trace를 보존했다.

**SST3/SST4는 재실행하지 않는다.** 사용자가 위임한 새 Live는 결과 화면 확인만 하는 SST4 한 번으로 완료됐다. 다음은 실제 Evidence 연결을 위한 오프라인 준비이며, 추가 본문 수집·저장 범위·외부 전송·provider 비용·동결 Stage 의미 변경은 여전히 별도 승인 대상이다. 이전 실패 기록이나 승인 identity를 다시 쓰지 않는다.
