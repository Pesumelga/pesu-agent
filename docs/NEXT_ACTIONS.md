# 다음 작업

갱신: 2026-10-16. 제품 목표는 자연어 질문에서 추적 가능한 근거 기반 답변까지다. 작업 순서는 Engineering Lead가 관리하며, 아래 승인 조건은 별개다.

| 우선순위 | 작업 / 현재 상태 | 선행 조건 | 완료 조건 |
| --- | --- | --- | --- |
| P0 | **최소 query 추출 수정 완료 → 새 SST4 1회 실행** | 오프라인 90 PASS. 사용자가 새 Live 실행을 명시적으로 위임했다. 커밋 후 정확한 새 HEAD/runner/protocol 해시와 미사용 상태를 확인하고 SST4 승인 기록을 생성한다. | 기존 전용 Host에서 같은 query `26년 하반기 간담회`를 입력/guarded submit 각각 최대 1회. 정확한 query·로딩 완료 search view를 확인하고 종료한다. 본문/스레드/LLM 호출 없음. 실패 시 추가 제출 없이 증거 보존. SST3 파일은 그대로 유지한다. |
| P1 | 실제 검색 결과를 Evidence로 연결 | 검색 전환 qualification 통과 및 새로운 실데이터 추출·저장 범위의 별도 승인. SST3 자체를 재실행하거나 그 FAIL을 수정하지 않는다. | 식별자·permalink·본문/문맥의 출처 연결과 중복 처리가 실제 허용 데이터에서 확인된다. 최소 수집 범위와 실패 시 중단 조건을 먼저 제시한다. |
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

**SST3는 재실행하지 않는다.** 그 승인은 소비됐다. 이후 사용자의 새 지시는 오프라인 수정과 새 Live 실행을 명시적으로 허용한다. 이번에는 원본을 보존하는 SST4 한 번으로 제한하고, 새 commit identity와 한정 범위를 승인 기록에 묶는다. 과거 SST3 승인을 재사용하지 않는다. 추가 실데이터 범위·외부 전송·provider 비용·동결 Stage 의미 변경은 여전히 별도 승인 대상이다.
