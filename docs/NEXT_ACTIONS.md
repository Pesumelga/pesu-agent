# 다음 작업

갱신: 2026-10-19. 제품 목표는 자연어 질문에서 추적 가능한 근거 기반 답변까지다. 작업 순서는 Engineering Lead가 관리하며, 아래 승인 조건은 별개다.

GitHub 푸시 차단은 테스트용 가짜 토큰의 과거 이력을 정리해 해소했다. 사용자 승인 후 **`codex/secret-clean-publish`**만 일반 push했고 GitHub 수락과 원격 ref 일치를 확인했다. 관련 마스킹 테스트 6 PASS와 원문 패턴 0개 검증도 완료됐다. 원본 로컬 브랜치·원격 main·동결 증거는 유지됐다. 원본 브랜치/`--all`/force push는 사용하지 않는다. PR 생성·병합은 별도 작업이며 이번에는 수행하지 않았다.

| 우선순위 | 작업 / 현재 상태 | 선행 조건 | 완료 조건 |
| --- | --- | --- | --- |
| P0 | RC5 실제 제품 흐름의 제한된 실데이터 검증 | EXE·109개 오프라인 테스트·네이티브 시작 확인 완료. 새 검색/본문·스레드 수집/외부 전송/실제 비용은 사용자 명시 승인 필요 | 실제 사용자 질문부터 출처 답변까지 확인하고 실패 시 재시도 없이 증거 보존. 실제 실행 전 최종 HEAD·실행 코드 해시·질문·상한·저장/전송 범위를 고정한다. |
| P1 | 실제 검증에서 확인된 차단 요소 수정 | P0 결과 | 실패를 오프라인 재현해 최소 수정하고 관련 검증·로컬 커밋. 과거 실패를 PASS로 바꾸지 않는다. |

실제 제품 검증 권장 범위(아직 승인/실행 아님): 질문 `26년 하반기 간담회의 진행 상황과 남은 일을 알려줘` 1회, 계획 검색 최대 3회+재탐색 최대 1회, 검색별 결과 최대 5개(후보 최대 20개)·최종 근거 최대 5개, 관련 스레드 최대 1개(검증 답글 최대 10개, DOM 후보 최대 15개), 모델 요청 최대 16회. 본문/검증 trace는 로컬 저장하고 선택한 근거는 OpenRouter로 전송한다. 누적 보고 비용 $0.05부터 다음 호출을 차단하지만 마지막 호출의 초과 과금 가능성이 있다. 실패 시 자동 재시도 없이 중단·보존한다. SST1–4는 사용하지 않는다. 승인 전 새 marker/authorization/live trace를 만들지 않는다.

현재 완료한 실제 제품 작업:

- `release/PESU-1.0.0-rc5/PESU/PESU.exe` 및 포터블 ZIP 생성. 기본 실행 버튼이 실제 검색 orchestration→authoritative 검증/합성→근거 답변/출처/기록으로 연결된다. 과거 HTML 미리보기는 납품물이 아니다.
- 관련 오프라인 **109 PASS (4.62s)**, 최종 EXE의 실제 네이티브 시작 **PASS (7.25s)**. 검색/LLM 호출 없는 시작 검증으로 전체 실데이터 검증을 대신하지 않는다. 기존 증거 794개 해시 동일, 원본 브랜치/이전 패키지 보존, push 없음.

과거 오프라인 준비 기록(당시 상태):

- **사용자 확인용 결과물:** `.runtime/product-preview/index.html`에서 완료·차단 공존·근거 부족·분석 실패 4개 고정 합성 예시를 볼 수 있다. 실제 근거 매핑·coordinator(명시적 의미 stub)·상태 결정·답변·제품 renderer를 연결했다. 임의 질문의 실제 AI 분석은 아니다. 업무별 상태/판단/차단/불확실성/출처 카드와 원문 확인을 제공한다. 재생성: `python scripts/build_offline_product_preview.py`.
- 답변 builder의 완료 보고/차단 요인 혼합을 회귀 재현 후 수정했다. 표시 한도에 따른 업무·출처 생략도 안내한다. 관련 **45 PASS**, 실제 브라우저의 4상황/원문 모달 확인, 기존 증거·Live 기록 **794개 동일**. 기존 기본 서비스의 coordinator cutover나 실제 provider 검증은 하지 않았다.

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
