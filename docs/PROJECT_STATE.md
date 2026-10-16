# PESU Agent — 현재 상태

갱신: 2026-10-16. **현재 단계: Phase 13.5D / Task 11, 검색어 추출 수정 후 SST4 실제 검색·결과 화면 전환 검증 통과. SST3 FAIL과 SST4 PASS는 별도 증거로 보존하며 둘 다 재실행하지 않는다. 다음은 Evidence 연결 준비 및 실제 본문 수집 범위의 승인 경계다.** 이 요약과 [AGENTS.md](../AGENTS.md)가 현재 운영 기준이다. 아래 Phase 9 이하의 과거 기록은 역사 참고이며 현재 단계·승인·모델 선택을 대체하지 않는다.

## 제품이 실제로 도달한 범위

목표는 조직의 논의·결정·업무 맥락에 대해 자연어 질문을 받고, 출처가 연결된 사실·추론·모순·불확실성을 구분해 답하는 것이다. 특정 업무 도메인에 한정하지 않는다.

| 영역 | 확인된 구현 / 검증 수준 | 아직 입증되지 않은 부분 |
| --- | --- | --- |
| 제품 화면·실행·설정 | `src/pesu_agent/ui/` 및 기존 패키징/동의/로그인 코드 존재. Phase 10–12 완료는 이전 인계 기록이다. | 이번 작업에서 패키지/사용자 화면을 새로 실행하지 않았다. |
| Slack 검색 | SST3의 제목/query 불일치를 최소 수정하고 SST4 Live에서 입력·guarded submit 각각 1회, 정확한 query와 로딩 완료 search view를 확인했다. 공식 결과 QUALIFICATION_PASS. | 결과 DOM 20개는 전체 검색 결과 수가 아니다. 메시지 본문·스레드 수집과 실제 Evidence 연결은 이번 검증에 포함하지 않았다. |
| 근거·주장·업무항목 | `graph_models.py`, `claim_verification.py`, `workitem_synthesis.py`, `production_trace_coordinator.py`에 graph·검증·다단계 합성 구현 존재. 과거 Phase 13.5C의 composed qualification은 fixture/stub 기준 통과다. | 실제 Slack + 실제 provider를 결합한 전체 흐름 통과는 입증되지 않았다. |
| 근거 답변과 UI 연결 | 답변 구성/표시 코드와 production-shaped 오프라인 E2E 테스트 존재. `InvestigationService._get_or_create_orchestrator()`는 현재 기존 `InvestigationOrchestrator`를 생성한다. | 새 ProductionRealTraceCoordinator 경로의 제품 UI cutover는 아직 완료로 볼 수 없다. |

과거 real-model verifier qualification은 개별 단계의 역사 검증이다. 현재 실제 Slack 전체 흐름 성공으로 확대 해석하지 않는다. Stage 1A/1B/2/2.5의 기존 계약·모델·동결 증거를 임의로 다시 열지 않는다.

## SST3 실행 준비와 증거

- 검증된 SST3 실행 코드 기준 커밋: `2a1e58e62704c34a3c9a93c3603109f119b4ce3c`. 운영/테스트 문서 정비 커밋은 이후 HEAD를 바꾼다. **승인 대상 HEAD는 실행 직전 `git rev-parse HEAD`의 실제 값**이며 이전 승인을 재사용하지 않는다.
- Runner SHA-256: `c3df20e5df80d2ed297e9a9d4d6ba5e792f648ae2c9a11f7ab9b2baf17664a00`.
- Protocol SHA-256: `0d3e8c4979e7fdac486358dd4d09d271b099b238b2d207027c26e555b37c46c1`.
- 상세 계약·수정·이전 검증: [SST3_EXECUTION_PATH_AUDIT.md](SST3_EXECUTION_PATH_AUDIT.md). 직전 집중 Python 82 PASS, Node 6 PASS는 오프라인 증거다.
- SST1/SST2는 소비된 실패 시도다. SST3도 아래의 승인 Live 실행으로 소비됐다. 실제 marker·authorization·live trace를 보존하며 재실행하지 않는다. LLM 호출과 본문/스레드 수집은 수행하지 않았다.
- 기존 확장 테스트 143 PASS / 1 FAIL은 SST2 dry-run 테스트가 동결된 과거 Live 승인을 기본 경로에서 읽어 생긴 HEAD 불일치였다. runner/동결 승인 내용은 유지하고 테스트 fixture만 임시 경로로 격리한다. stale 승인 거부 검증은 별도로 유지한다.
- 이전 QSC2 테스트가 비동결 dry-run 파일을 재생성한 내역은 감사 문서에 기록돼 있다. 이번에는 해당 테스트의 출력을 임시 경로로 바꿔 재발을 막는다. 이전 원본이 복원됐다고 주장하지 않는다.

## 현재 운영 / 인계

Engineering Lead가 [NEXT_ACTIONS.md](NEXT_ACTIONS.md)의 우선순위에 따라 승인 불필요 작업을 자율 진행한다. 실제 검색·새 실데이터 수집·실제 provider 비용/민감 처리·동결 계약 변경·중대 설계 변경·배포는 Product Owner의 별도 승인을 받는다. Slack 쓰기와 Slackbot은 금지다.

사용자가 검색어 추출 수정·테스트·커밋과 새 Live 실행을 명시적으로 위임했고, 이를 **같은 query `26년 하반기 간담회`의 SST4 1회, 결과 화면 확인까지만**으로 한정해 완료했다. 정확한 새 HEAD/runner/protocol 해시에 승인 기록을 묶었으며 재시도 없이 통과했다. 다음 제품 작업은 Evidence 연결 준비다. 실제 본문 수집·저장 범위와 provider 비용/외부 전송은 별도 사용자 확인이 필요하다. 이번 승인을 thread 열기·추가 검색·provider 호출·production cutover로 확대하지 않는다.

## 2026-10-16 SST4 승인 Live 결과

- 실행 HEAD: `7ab6157b74404dccf451a50ef02e03fd4ce59a35` (검색어 추출 수정 커밋). 이 시점에 tracked tree clean, 현재 runtime closure 60개·역사 기준 34개·SST3 원본 증거 해시 3개와 미사용 상태를 대조했다. 이후 증거 보존 커밋은 별도 HEAD다.
- 시작 기록: **16:19:10 KST** (`2026-10-16T07:19:10.112787+00:00`). 원본 SST4 CLI 1회 실행, exit code **0**.
- 공식 결과: **QUALIFICATION_PASS / SEARCH_SUBMIT_TRANSITION_SUCCESS**. 입력 1회, readiness poll 1회, guarded submit 시도/성공 각각 1회. 전환 poll 6에서 **914.89ms**에 `search_view_mounted=true`, `loading_detected_after=false`, `skeleton_count=0`, `surface_query_after=26년 하반기 간담회`, `result_root_count=20`을 관측했다.
- 첫 관측은 로딩 중·query 미확정이었고 완료 관측에서 정확한 query가 확인됐다. 단순 제목 포함 여부나 editor echo로 성공시키지 않았다. 결과 DOM 수는 화면에 감지된 항목 수이며 전체 검색 결과 수는 아니다.
- 자동 재시도·본문 수집·thread 열기·Slack 쓰기/Slackbot·LLM 호출은 없다. Host cleanup 후 종료했고 추가 Host 관측/검색은 하지 않았다. CLI stdout/stderr는 `.runtime/sst4-approved-live.stdout.json` / `.runtime/sst4-approved-live.stderr.log`에 보존했으며 stderr는 0바이트다.
- 실행 직전부터 있던 artifact/output **785개 해시 모두 동일**. SST1/SST2/SST3의 이전 증거와 원자적 제출 guard를 보존했다. SST4는 소비됐으며 다음 단계에서 재사용하지 않는다.

| 보존 파일 (`artifacts/task11_search_submit_transition_sst4/`) | SHA-256 |
| --- | --- |
| `sst4_authorization.json` | `dc315fe08ab12a50af7771474b69e2ab5b171cd3a636a53cec0a3ad3379a04c1` |
| `sst4_marker.json` | `11347596e2f4649d4cf10fdd374e55fb0198139569ac05cd3901900e18f31802` |
| `sst4_live_trace.json` | `1738dc322710e7ca960ac7f5724a890ea443bcdeba6ff1bee8a882a922c95d22` |

## 2026-10-16 검색어 추출 수정 / SST4 준비 기록

- Production 변경은 `JS_PROBE_POST_WAIT_SEARCH_STATE`에 한정했다. 유일한 search view 안의 query 전용 필드는 그대로 읽고, presentation title에서만 관측된 UI 안내 문구 `다음에 대한 결과:`를 시작 위치에서 한 번 제거한다. 업무 도메인/정답 규칙은 추가하지 않았다.
- URL/header query가 다르거나 header/search view가 모호하면 query를 확정하지 않는다. 입력창 echo만으로 결과 검색어를 증명하는 fallback을 제거했다. 완료 판정의 정확한 query 일치·로딩 해제 조건 및 기존 원자적 제출 가드는 그대로다.
- 관측된 제목을 합성 DOM fixture로 옮겨 실제 Production JS를 Node에서 실행했다. 최초 올바른 재현 run은 3 FAIL / 13 PASS로 제목 불일치와 editor-only 오판을 확인했다. 현재 관련 query/SST4/Production guard/transition 테스트 **90 PASS (25.08s)**. 지역화 제목의 전체 non-dry runner 경로 및 다른 query·접미어·상충 URL·중복 header·로딩을 검증했다. 실제 본문을 읽으려 하면 실패하는 DOM fixture도 사용한다.
- 독립 검토에서 Production/수명주기 추가 결함은 발견되지 않았다. 실제 SST3 trace를 테스트 기본 fixture로 읽지 말라는 지적은, 허용된 제목 필드만 literal fixture로 복사하는 방식으로 반영했다.
- SST3 runner·protocol·승인·marker·trace는 변경하지 않았다. 기존 artifact/output **784개 해시 동일**, 원자적 guard JS도 동일하다. 과거 protocol의 runtime closure는 이전 Production 해시를 계속 보존하므로 변경된 현재 코드를 과거 승인으로 실행할 수 없다.
- `scripts/diagnose_task11_search_submit_transition_sst4.py`는 기존 단일 제출 runner의 versioned 후속이다. SST4 identity/path/scope를 사용하고, 기존 34개 역사 기준 외에 소비된 SST3 승인·marker·trace의 해시 3개도 검증한다. 새 protocol의 60개 runtime closure에서 변경한 항목은 위 Production observer 파일뿐이다. 새 프레임워크나 제출 경로는 만들지 않았다.
- SST4 runner SHA-256: `2a8cb74b5209bf6d499496a3e9540473314d01a90581515ebc847f347ade8490`.
- SST4 protocol SHA-256: `53b2d3a20a8e8c4a9cf766c8ead1ead6b0f8b106d7f6e240c79896228d6410d9`.
- Production module SHA-256: `d1db3f5034fc7b03b8b676e78fb615ebc7876134b14014fc338a7cd536724b42`.
- 이 준비 시점에는 SST4 authorization/marker/live trace가 없었다. 이후 승인된 실행 결과와 증거는 위의 별도 Live 기록을 따른다.

## 2026-10-16 SST3 승인 Live 결과

- 실행 기준: HEAD `7fc0675cf08ae4d22447cea4fcfca1ecce745ae4`, 위 runner/protocol 해시. Product Owner가 이 대화에서 정확한 identity와 1회 범위를 명시적으로 승인했다. 실행 전 tracked tree clean, 34 역사 기준·60 runtime hash 일치 및 실제 artifact 부재를 확인했다.
- 시작 기록: **15:56:56 KST** (`2026-10-16T06:56:56.136798+00:00`). 기존 전용 프로필/Host의 인증 사전 점검 후 marker를 획득했다. 승인된 원본 CLI를 **1회만** 실행했고 exit code는 **1**이다.
- 공식 결과: **FAIL_CLOSED / TRANSITION_TIMED_OUT**. Query 입력 1회, readiness poll 1회, guarded submit 시도/성공 각각 1회, 전환 관측 86회. 자동 재시도 0회.
- 첫 로딩 완료 관측은 poll 11, 약 **1,250.65ms**였다. `search_view_mounted=true`, `loading_detected_after=false`, `skeleton_count=0`, `result_root_count=20`이었으며 마지막 관측에서도 유지됐다. 결과 원문은 읽거나 저장하지 않았다.
- 기록된 `surface_query_after`는 `다음에 대한 결과: 26년 하반기 간담회`였다. 현재 runner의 `sq.strip() == TARGET_QUERY` 조건에 맞지 않아 관측 제한 10초 내 성공 판정을 못 했다. 이는 제목/검색어 추출과 검증 조건의 불일치를 뒷받침한다. 원본 FAIL을 PASS로 재분류하거나 동결 계약을 바꾸지 않는다.
- 본문/스레드 수집, Slack 쓰기/Slackbot, LLM provider 호출은 하지 않았다. 실행 후 Host cleanup 경로를 거쳐 종료했다. 실패 후 추가 Host 관측이나 검색은 하지 않았다.
- 실행 전부터 있던 artifact/output **781개 해시 모두 동일**. Runner/protocol 및 SST1/SST2 증거도 유지됐다. 새 SST3 파일은 아래 세 개만 추가됐다.

| 보존 파일 (`artifacts/task11_search_submit_transition_sst3/`) | SHA-256 |
| --- | --- |
| `sst3_authorization.json` | `590dc24fb3fc5749baf88b5606acbabe15f07406c2efc324159ff8279e5d5619` |
| `sst3_marker.json` | `8251e401b2c1956aac0023494dc43e8825a5d7815302e1111f1d22a16b57a612` |
| `sst3_live_trace.json` | `6d4198920ec4994705856040683481bd2e5f208d87e8b70aeba43bccbc2488dc` |

CLI stdout/stderr는 `.runtime/sst3-approved-live.stdout.json` / `.runtime/sst3-approved-live.stderr.log`에 추가 보존했다. stdout은 runner 반환 report, stderr는 0바이트다. 이 실행의 승인 HEAD는 이후 결과 기록 커밋의 HEAD와 구분하며, 해당 승인을 재사용하지 않는다.

## 2026-10-16 운영 체계 정비 검증

- 독립 조사에서 SST2 runner와 기존 실패 테스트의 Git blob이 `95ba4cf`와 `2a1e58e` 사이에 동일함을 확인했다. 과거 HEAD에서도 동일한 승인 거부가 발생했고, 올바른 임시 승인에서는 통과했다. HEAD·runner hash·protocol hash·scope가 잘못되면 거부하는 8개 오프라인 probe를 확인했다.
- SST1/SST2/SST3, SFC2 Production guard, search safety, QSC2 observer의 관련 회귀 테스트: **145 PASS (49.95s)**. 과거의 143 PASS / 1 FAIL 기록은 덮어쓰지 않고, fixture 수정 후 결과로 구분한다.
- 테스트 수정은 SST2/QSC2 테스트 파일에만 적용했다. Production runner·승인 gate·동결 승인/프로토콜/증거는 변경하지 않았다. 관련 테스트 뒤 기존 artifact/output **781개 해시가 모두 동일**하다.
- 검토에서 테스트의 보안 약화나 문서 승인 경계의 추가 문제는 발견되지 않았다. SST3 실행 코드와 protocol 해시도 위 값 그대로다.
- 근거→주장→업무항목의 추가 E2E를 포함한 합산 실행은 완료되지 않아 중단했다. 제한된 별도 재현에서도 첫 테스트 본문 진입 전 Windows `asyncio`/AnyIO 준비의 `_fallback_socketpair → accept`에서 대기했고, 40초 child timeout 후 종료를 확인했다. 진단의 네트워크 차단 guard/실행 환경 영향을 포함해 원인은 미확정이며, 제품 코드 회귀라고 단정하지 않는다. 이번에 이 E2E가 통과했다고 기록하지 않는다. 상세 stack과 종료 기록은 `.runtime/production-e2e-diagnostic-71b41723c9394c6ca690f95f80cfa414/`에 있다.
- 이 후속 비동기 테스트 준비 문제는 별도로 관리한다. SST3는 동기식 Host/JS 호출 경로로, 현재 해당 경로의 실제 JS 오프라인 검증은 통과했다. 새로운 실제 Slack/실제 provider 전체 흐름을 주장하거나 비동기 제약을 우회해 실행하지 않는다.

---

# 역사 참고 — 기존 Project State & Context (Phase 9 시점, 원문 보존)

> [!NOTE]
> This document serves as the repository-local, persistent source of truth for the project state.
> It contains no secrets, API keys, real corporate Slack raw texts, or personal data.

---

## 1. Project Overview & Current Phase

- **Project**: PESU Agent (Autonomous Read-Only Slack Investigation Agent for Tax/Finance Ops)
- **Current Phase**: **Phase 9.8 — User Main Hidden Lease Safety Feasibility**
- **Second Full Main Status**: `SECOND_FULL_MAIN_INVESTIGATION_STATUS = CLOSED_NO_REPRODUCIBLE_PATH_FOUND`
- **Recent Input State Anomaly**: `RECENT_INPUT_STATE_ANOMALY = observed (unresolved)`
- **Slack Evidence Infrastructure Status**: `SLACK_EVIDENCE_INFRASTRUCTURE_STATUS = FROZEN`
- **Synthetic RTS Status**: `SYNTHETIC_RTS_STATUS = FINALIZED`
- **Synthetic Auth Status**: `SYNTHETIC_AUTH_STATUS = FINALIZED`
- **Policy RTS Readiness**: `PROCEED_WITH_CONTROLLED_VALIDATION`
- **In-Slack Experience Policy Status**: `GUIDELINE_UNRESOLVED_NOT_HARD_BLOCKER`
- **Search Primitive Hardening Status**: `SEARCH_PRIMITIVE_HARDENING_STATUS = FINALIZED`
- **Synthetic Orchestrator Status**: `SYNTHETIC_ORCHESTRATOR_STATUS = FINALIZED`
- **Authoritative Historical Brain UUID**: `05cd34f1-6e3f-4d3c-8cfb-a4ebccfe27c1`
- **Active Workspace**: `c:\Users\HEUM\Documents\pesu-agent`

---

## 2. Completed Phases Summary

| Phase | Description | Status | Verification & Artifacts |
| :--- | :--- | :---: | :--- |
| **Phase 1** | Slack Windows App Automation & UIA DOM Inspector | **DONE** | Process lifecycle, UIA selector discovery |
| **Phase 2** | Secondary Hidden Agent Window Isolation | **DONE** | User Main Window 100% untouched |
| **Phase 3** | Search Modal & Thread Context Collection | **DONE** | Multi-result context crawler |
| **Phase 4** | **Slack Evidence Infrastructure Freeze** (Closure 4.1~4.4) | **FROZEN** | Gates A, B, C, D verified (`unexpected_write_observed_in_test_scope = false`) |
| **Phase 5** | AI Investigation Foundation (OpenRouter Provider & Analyzer) | **DONE** | ModelProvider abstraction, Grounded Analyzer, Cost tracking |
| **Phase 6.1** | Live OpenRouter Initial Benchmark (10 PESU Cases) | **DONE** | `live_calls_total = 109`, `actual_cost_total = $0.8366 USD` (Historical Exploratory) |
| **Phase 6.2** | Live 3-Run Stability & Failure Analysis (4 Contenders + Sol Ref) | **DONE** | `live_calls_total = 124`, `actual_cost_total = $0.3377 USD` (Historical Stability Reference) |
| **Phase 6.3** | Evaluator Repair & Regression Fixtures | **DONE** | Fixed PESU-03/06/10 Evaluator, added 4 regression test variations (`5 passed`) |
| **Phase 6.3.1** | Clean Live Re-Benchmark & Durable Persistence | **DONE** | 40/40 calls persisted to `output/benchmark_raw/`, `offline_rescore_verified = true`, `actual_cost = $0.0787 USD` |
| **Phase 6.3.2** | Offline Benchmark Audit & Dominance Analysis | **DONE** | Per-case full audit, hard-case analysis, dominance verification (`NEW_EXTERNAL_CALLS = 0`) |
| **Phase 6.3.3** | Evaluator Semantic Hardening & Minimal Stability Gate | **DONE** | Synonym clusters, canonical elapsed time, structured state primacy, 6 regression tests passed (`NEW_EXTERNAL_CALLS = 0`) |
| **Phase 6.3.4** | Luna Medium Final Stability Gate | **FINALIZED** | 30/30 (100%) Strict Pass, Quality Mean 98.80pt (Stddev 1.49pt), 0 Hallucinations, Cost $0.00161/call (`actual_cost = $0.0315 USD`) |
| **Phase 7** | Investigation Planner Benchmark | **DONE** | 20 calls (2 candidates × 10 cases), Intent Recall 100%, 0 Hallucinated Entities, Cost $0.0142 USD (`output/benchmark_raw/phase_7_planner/`) |
| **Phase 8** | Synthetic End-to-End Investigation Orchestrator | **FINALIZED** | Full E2E Pipeline (`SYNTHETIC_ORCHESTRATOR_STATUS = FINALIZED`), 8/8 (100%) Strict PASS, Durable Persistence (`output/investigations/`) |
| **Phase 8.1** | Retrieval / Selection / Sufficiency Failure Analysis & Fix | **DONE** | Root cause diagnosis across Cases C, D, E; Conclusiveness stop logic refined; 6/6 (100%) E2E PASS verified |
| **Phase 8.2** | Multi-Round Retrieval & Clue Tracking Verification | **DONE** | Cases G & H Multi-Round Clue & Redirect Discovery verified (R1 Recall 50% ➔ R2 Recall 100%, New Gold in R2 = True), 8/8 (100%) E2E PASS, Total Cost $0.0229 USD |
| **Phase 9.5.2** | **Safe Typed Global Search Primitive Hardening** | **FINALIZED** | Removed generic contenteditable & URL navigation fallbacks, added pre-dispatch reverification (TOCTOU defense), strict fail-closed |
| **Phase 9.5.3** | **Verified Search UI Activation** | **FINALIZED** | Exact `button[data-qa="top_nav_search"]` activation, bounded polling input mount, fail-closed, re-frozen |
| **Phase 9.5.4** | **PowerShell-Free Runtime Bootstrap** | **DONE** | Pure Python Windows detached process launcher, zero PowerShell, `pesu-start.cmd`, 8 unit tests passed |
| **Phase 9.6** | **Official Slack RTS Feasibility Spike** | **DONE** | Granular scopes (`search:read.*`), OAuth PKCE semantics, in-memory zero persistence contract verified |
| **Phase 9.6.2** | **Synthetic Slack RTS SearchBackend & Token Protocol** | **DONE** | `SlackRtsSearchBackend`, `RtsSearchResponse` schema, `(channel_id, message_ts)` mapping, budget <= 6, 6 unit tests passed |
| **Phase 9.6.4** | **Synthetic Slack OAuth 2.0 PKCE Auth Infrastructure** | **DONE** | `SlackPkceAuthManager`, RFC 7636 S256 Public Client, Secret Redaction, TokenProvider handoff, 11 unit tests passed |

---

## 3. Frozen Components (`SLACK_EVIDENCE_INFRASTRUCTURE = FROZEN`)

The following core components have passed rigorous read-only safety gates and are **FROZEN**. No arbitrary modifications are permitted without explicit gate re-verification:

1. `src/pesu_agent/adapters/slack_cdp.py` (`SlackBootstrapAdapter`)
2. `src/pesu_agent/lifecycle/slack_agent_window.py`
3. `src/pesu_agent/lifecycle/slack_lifecycle.py`
4. `src/pesu_agent/security/read_only_barrier.py`
5. `src/pesu_agent/evidence/slack_evidence_collector.py`

**Safety Metrics**:
- `raw_cdp_direct_callers_outside_approved_adapter = 0`
- `unexpected_write_observed_in_test_scope = false`
- `runtime_main_agent_actions = 0`

---

## 4. Current Safety Boundaries

- **HARD BLOCK**: `actual Slack data → external LLM = BLOCKED`
  - `actual_slack_data_external_egress = 0` (Zero Slack messages/threads/attachments sent to external LLMs).
  - Planner takes only the user's natural language question (no Slack text input).
  - Analyzer is disabled during Phase 9 retrieval quality benchmarking.
  - Evaluation is performed locally by matching metadata/identifiers (`RealSlackRetrievalEvaluator`).

---

## 5. Production Model Configuration

```yaml
DEFAULT_PLANNER_MODEL: "openai/gpt-5.6-luna"
DEFAULT_PLANNER_REASONING_EFFORT: "medium"
DEFAULT_PLANNER_STATUS: "FINALIZED"

DEFAULT_ANALYZER_MODEL: "openai/gpt-5.6-luna"
DEFAULT_ANALYZER_REASONING_EFFORT: "medium"
DEFAULT_ANALYZER_STATUS: "FINALIZED"

BUDGET_BACKGROUND_MODEL: "deepseek/deepseek-v4-flash"
BUDGET_BACKGROUND_STATUS: "PROVISIONAL"
```

---

## 6. Phase 9 Real Slack Retrieval Quality Benchmark Architecture

- **Objective**: Measure real Slack evidence retrieval precision, recall, and multi-round search expansion performance without AI analysis or data egress.
- **Search Adapter**: `src/pesu_agent/search/real_slack_backend.py` (`RealSlackSearchBackend` wrapping frozen `SlackEvidenceCollector`).
- **Evaluator**: `src/pesu_agent/benchmark/real_slack_retrieval_evaluator.py` (`RealSlackRetrievalEvaluator`).
- **Metrics Computed**:
  - `retrieval_recall`: Overall gold message recall ($0.0 \sim 1.0$)
  - `recall_at_5`, `recall_at_10`, `recall_at_20`: Top-K recall
  - `thread_recall`: Gold thread recall
  - `round_1_recall`: Recall after Round 1 queries
  - `round_2_recall`: Cumulative recall after Round 2 queries
  - `round_2_recall_gain`: Incremental gain in Round 2 ($R2 - R1$)
  - `new_gold_in_round_2`: True if Round 2 expansion discovered new gold evidence
  - `irrelevant_result_ratio`: Percentage of retrieved messages not matching gold criteria
  - `duplicate_ratio`: Percentage of duplicates filtered by global deduplicator
  - `time_to_first_gold_evidence`: Latency in ms to retrieve the first gold item
  - `total_search_queries`: Count of executed Slack search queries
- **Case Input Schema**: `RealSlackGoldCase` loaded from `config/real_slack_gold_cases.json` (untracked in git):
  ```json
  [
    {
      "case_id": "REAL-CASE-01",
      "question": {"question": "...", "context_hint": "..."},
      "gold_message_identifiers": ["https://workspace.slack.com/archives/.../p...", "...ts..."],
      "gold_thread_identifiers": ["...ts..."],
      "optional_time_range": {"start": "2026-08-01", "end": "2026-08-15"},
      "optional_actor_hints": ["담당자A", "결재자B"],
      "notes": "..."
    }
  ]
  ```
- **Execution Scripts & Tools**:
  - `scripts/create_real_slack_gold_case.py`: Interactive CLI Gold Case Authoring Helper (no manual JSON editing required).
  - `scripts/audit_phase_9_1_search_sanity.py`: Phase 9.1 Real Slack Search Sanity & Selector Audit Script.
  - `scripts/run_phase_9_real_slack_retrieval_benchmark.py`: Real Slack Retrieval Benchmark Runner (Compatible with `InvestigationPlanner(provider, config)`).
- **Slack-Native Query Compiler**: `src/pesu_agent/search/slack_query_compiler.py` (`SlackQueryCompiler` decomposing boolean expressions and parenthesized OR queries into native simple queries + union).
- **Phase 9.1 Sanity Findings**:
  - `REAL-CASE-01`, `REAL-CASE-02` invalidated (contained textual descriptions rather than permalinks).
  - `REAL-CASE-03`: `gold_identifier_normalization_success = 3/3 (100%)`.
  - Root Cause of Repeated Results: Slack search doesn't accept boolean `AND/OR/(...)` syntax; now compiled via `SlackQueryCompiler`.
- **Test Suite Status**: **108 passed in 46.2s (0 failures, 0 warnings)**.

---

## 7. Next Steps

1. Phase 9 Design: PII Masking & User Consent Gate Design (prior to connecting real Slack messages).
2. SearchBackend Adapter for Read-Only Slack Infrastructure.

---

## 7. Next Steps

1. Review Phase 8 Synthetic End-to-End Results.
2. Architecture Design for PII Masking & User Consent Barrier (prior to connecting real Slack messages).
3. SearchBackend Adapter for Read-Only Slack Infrastructure.

---

## 3. Frozen Components (`SLACK_EVIDENCE_INFRASTRUCTURE = FROZEN`)

The following core components have passed rigorous read-only safety gates and are **FROZEN**. No arbitrary modifications are permitted without explicit gate re-verification:

1. `src/pesu_agent/adapters/slack_cdp.py` (`SlackBootstrapAdapter`)
2. `src/pesu_agent/lifecycle/slack_agent_window.py`
3. `src/pesu_agent/lifecycle/slack_lifecycle.py`
4. `src/pesu_agent/security/read_only_barrier.py`
5. `src/pesu_agent/evidence/slack_evidence_collector.py`

**Safety Metrics**:
- `raw_cdp_direct_callers_outside_approved_adapter = 0`
- `unexpected_write_observed_in_test_scope = false`
- `runtime_main_agent_actions = 0`

---

## 4. Current Safety Boundaries

- **HARD BLOCK**: `actual Slack → external LLM = BLOCKED`
  - `EvidencePackage.is_synthetic == False` transmission is strictly prevented by `ExternalTransmissionBlockedError`.
  - All benchmarks execute solely against synthetic fixtures (`is_synthetic == True`).
  - `actual_company_slack_data_sent = False`

---

## 5. Production Model Configuration

```yaml
DEFAULT_ANALYZER_MODEL: "openai/gpt-5.6-luna"
DEFAULT_ANALYZER_REASONING_EFFORT: "medium"
DEFAULT_ANALYZER_STATUS: "FINALIZED"

PLANNER_BEST_DEFAULT: "openai/gpt-5.6-luna (medium)"
PLANNER_BEST_VALUE: "deepseek/deepseek-v4-flash"

BUDGET_BACKGROUND_MODEL: "deepseek/deepseek-v4-flash"
BUDGET_BACKGROUND_STATUS: "PROVISIONAL"
```

---

## 6. Phase 7 Investigation Planner Benchmark Metrics

- **Dataset**: 2 Candidates × 10 PESU Cases = **20 Evaluations**
- **Persistence Directory**: `output/benchmark_raw/phase_7_planner/`
- **Total Cost**: `$0.0142 USD` (Budget $0.10 guarded)

| Candidate Model | Runs | Plan Quality Mean | Intent Recall | Actor Coverage | Temporal Coverage | Question Coverage | Query Count Mean | Hallucinated Entities | Latency Mean | Cost/Call |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **luna-medium** | 10회 | **98.1 pt** | **100.0%** | **96.0%** | **100.0%** | **100.0%** | 4.3개 | **0건** | **7,421ms** | $0.001140 |
| **deepseek-v4-flash** | 10회 | **97.4 pt** | **100.0%** | **96.0%** | **100.0%** | 96.0% | 4.7개 | **0건** | 25,159ms | **$0.000277** |

---

## 7. Next Steps

1. Review Phase 7 Planner Benchmark Results.
2. Architecture Design for PII Masking & Consent Barrier (prior to connecting real Slack messages).
3. End-to-End Synthetic Pipeline Integration Test (Planner $\to$ Evidence Collector $\to$ Analyzer).

---

## 3. Frozen Components (`SLACK_EVIDENCE_INFRASTRUCTURE = FROZEN`)

The following core components have passed rigorous read-only safety gates and are **FROZEN**. No arbitrary modifications are permitted without explicit gate re-verification:

1. `src/pesu_agent/adapters/slack_cdp.py` (`SlackBootstrapAdapter`)
2. `src/pesu_agent/lifecycle/slack_agent_window.py`
3. `src/pesu_agent/lifecycle/slack_lifecycle.py`
4. `src/pesu_agent/security/read_only_barrier.py`
5. `src/pesu_agent/evidence/slack_evidence_collector.py`

**Safety Metrics**:
- `raw_cdp_direct_callers_outside_approved_adapter = 0`
- `unexpected_write_observed_in_test_scope = false`
- `runtime_main_agent_actions = 0`

---

## 4. Current Safety Boundaries

- **HARD BLOCK**: `actual Slack → external LLM = BLOCKED`
  - `EvidencePackage.is_synthetic == False` transmission is strictly prevented by `ExternalTransmissionBlockedError`.
  - All benchmarks execute solely against synthetic fixtures (`is_synthetic == True`).
  - `actual_company_slack_data_sent = False`

---

## 5. Production Model Finalization & Routing Status

```yaml
DEFAULT_MODEL: "openai/gpt-5.6-luna"
DEFAULT_REASONING_EFFORT: "medium"
DEFAULT_MODEL_STATUS: "FINALIZED"
BUDGET_BACKGROUND_MODEL: "deepseek/deepseek-v4-flash (PROVISIONAL)"
```

### Model Classification & Dominance
- **`luna-medium`**: **FINALIZED PRODUCTION WORKHORSE** (Quality 98.80pt, Stddev 1.49pt, Strict 100%, 9.0s latency, $0.00161/call).
- **`luna-high`**: **`DOMINATED_BY_LUNA_MEDIUM`** (Equal quality 98.80pt, but 44% higher latency and 49% higher cost).
- **`luna-xhigh`**: **`NOT_COST_EFFECTIVE / PRACTICALLY_DOMINATED_FOR_PESU_DEFAULT`** (Equal practical quality 99.1pt vs 98.8pt (+0.3pt diff is negligible), but 2.3x higher latency (23s) and 2.2x higher cost ($0.00361)).
- **`deepseek-v4-flash`**: **`BUDGET_BACKGROUND_PROVISIONAL`** (Quality 98.2pt, Strict 90%, Cost $0.00027/call, Latency 44.3s for batch/async).

---

## 6. luna-medium 30-Run Full Verification Metrics (Phase 6.3.4)

- **Dataset**: 10 PESU Cases × 3 Runs = **30 Total Evaluations**
- **Persistence Path**: `output/benchmark_raw/phase_6_3_4/luna-medium/`

| Metric | Measured Value | Acceptance Threshold | Result |
| :--- | :---: | :---: | :---: |
| **Quality Mean** | **98.80 pt** | $\ge 90.0$ pt | **PASS (✓)** |
| **Quality Stddev** | **1.49 pt** | $< 5.0$ pt | **PASS (✓)** |
| **Overall Strict Success Rate** | **100.0% (30/30)** | $\ge 90.0\%$ | **PASS (✓)** |
| **Per-Case Minimum Strict Rate** | **100% (3/3 for all 10 cases)** | $\ge 2/3$ | **PASS (✓)** |
| **Factual Accuracy** | **100.0%** | $100.0\%$ | **PASS (✓)** |
| **Citation Precision** | **100.0%** | $100.0\%$ | **PASS (✓)** |
| **Citation Recall** | **100.0%** | $100.0\%$ | **PASS (✓)** |
| **Contradiction Detection** | **92.0%** | $\ge 80.0\%$ | **PASS (✓)** |
| **Uncertainty Calibration** | **100.0%** | $\ge 90.0\%$ | **PASS (✓)** |
| **Unsupported Claims** | **0 건** | $= 0$ 건 | **PASS (✓)** |
| **Hallucinated Evidence IDs** | **0 건** | $= 0$ 건 | **PASS (✓)** |
| **Structured Field Success Rate** | **100.0% (6/6)** | $100.0\%$ | **PASS (✓)** |
| **Latency (P50 / Mean / P95)** | **8,619ms / 8,995ms / 12,876ms** | $< 15,000$ms | **PASS (✓)** |
| **Cost Mean / Total (30 runs)** | **$0.001611 / $0.0483 USD** | $< $0.10 USD | **PASS (✓)** |

---

## 7. Next Steps

1. User Alignment on Finalized Production Model.
2. Architecture Design for PII Masking & User Consent Gate (prior to any real Slack LLM connection).
3. Planner Architecture Benchmark Design (Offline/Synthetic only).

---

## 6. Provisional Model Routing Decision

- **`PROVISIONAL_BEST_DEFAULT`**: `luna-medium` (openai/gpt-5.6-luna, effort=medium) — 98.8pt, 100% Strict, 9.8s, $0.00168/call
- **`PROVISIONAL_BUDGET_MODE`**: `deepseek-v4-flash` (deepseek/deepseek-v4-flash) — 98.2pt, $0.00027/call (Background/Batch)
- **`PROVISIONAL_DEEP_ANALYSIS`**: `luna-medium` (xhigh 대비 동등 품질, 57% 빠른 속도 및 54% 저비용으로 xhigh 대체)
- **`DOMINATED_CONFIGURATIONS`**:
  - `luna-high` (DOMINATED by luna-medium: 품질 동일, 비용 1.5배, 지연 1.4배)
  - `luna-xhigh` (DOMINATED by luna-medium: 품질 실질 동등, 비용 2.2배, 지연 2.3배)

---

## 7. Next Step: Minimal Stability Gate (Awaiting User Approval)

- **Target Candidate**: `luna-medium`
- **Target Cases**: PESU-01 ~ PESU-10 (10 Cases)
- **Additional Runs**: 2 Runs (Total 3 Runs per case)
- **Estimated Calls & Cost**: 20 calls, ~$0.0336 USD

### Model Performance Summary Table

| Candidate Model | Runs | Quality Mean | Strict Pass (>=90) | Basic Pass (>=70) | Cit. Prec | Cit. Rec | Unsupp. | Halluc. IDs | Latency Mean | Cost/Run | Total Cost |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **luna-high** | 10회 | **98.8 pt** | **100% (10/10)** | 100% | 100% | 100% | 0건 | 0건 | 14,357ms | $0.002505 | $0.02505 |
| **luna-medium** | 10회 | **98.8 pt** | **100% (10/10)** | 100% | 100% | 100% | 0건 | 0건 | 9,879ms | $0.001677 | $0.01677 |
| **deepseek-v4-flash** | 10회 | **98.2 pt** | **90% (9/10)** | 100% | 100% | 100% | 0건 | 0건 | 44,792ms | **$0.000272** | **$0.00272** |
| **luna-xhigh** | 10회 | **92.1 pt** | **80% (8/10)** | 80% | 100% | 100% | 0건 | 0건 | 23,456ms | $0.003607 | $0.03607 |

---

## 6. Evaluator Repair Effect Verification

| Case ID | Case Type | Phase 6.2 (Pre-Repair) | Phase 6.3.1 (Post-Repair) | Status & Verification |
| :--- | :--- | :---: | :---: | :--- |
| **PESU-03** | `LIFECYCLE_TIMELINE` | 65.0 pt | **100.0 pt** (All Models) | ✅ ISO Date Normalization Verified |
| **PESU-06** | `INSUFFICIENT_EVIDENCE`| 55.0 pt | **97.0 ~ 100.0 pt** | ✅ Structured Status Ground Truth Verified |
| **PESU-10** | `INCONCLUSIVE_UNCERTAINTY` | 52.0 pt | **97.0 ~ 100.0 pt** | ✅ Inconclusive Calibration Verified |

---

## 7. Next Steps

1. Review Phase 6.3.1 Clean Dataset and Model Ranking.
2. (Optional) Run focused 2-Run stability comparison between `luna-medium` vs `luna-high` if needed.
3. Design PII Masking & User Consent Gate prior to actual Slack data integration.

---

## 3. Frozen Components (`SLACK_EVIDENCE_INFRASTRUCTURE = FROZEN`)

The following core components have passed rigorous read-only safety gates and are **FROZEN**. No arbitrary modifications are permitted without explicit gate re-verification:

1. `src/pesu_agent/adapters/slack_cdp.py` (`SlackBootstrapAdapter`)
2. `src/pesu_agent/lifecycle/slack_agent_window.py`
3. `src/pesu_agent/lifecycle/slack_lifecycle.py`
4. `src/pesu_agent/security/read_only_barrier.py`
5. `src/pesu_agent/evidence/slack_evidence_collector.py`

**Safety Metrics**:
- `raw_cdp_direct_callers_outside_approved_adapter = 0`
- `unexpected_write_observed_in_test_scope = false`
- `runtime_main_agent_actions = 0`

---

## 4. Current Safety Boundaries

- **HARD BLOCK**: `actual Slack → external LLM = BLOCKED`
  - `EvidencePackage.is_synthetic == False` transmission is strictly prevented by `ExternalTransmissionBlockedError`.
  - All benchmarks execute solely against synthetic fixtures (`is_synthetic == True`).
  - `actual_company_slack_data_sent = False`

---

## 5. Verified Benchmark Metrics (Phase 6.1 & Phase 6.2)

### Phase 6.1 (Initial Live Exploration)
- **Live Calls Total**: `109`
- **Actual Cost Total**: `$0.8366 USD`
- **Scope**: Explored Luna (medium/high/xhigh/max), DeepSeek V4 Flash, Kimi K3, Terra High, Sol High.

### Phase 6.2 (3-Run Stability & Hard Case Comparison)
- **Live Calls Total**: `124`
- **Actual Cost Total**: `$0.3377 USD`
- **Scope**: 4 Target Contenders (luna-medium, luna-high, luna-xhigh, deepseek-v4-flash) across 10 PESU Cases × 3 Runs (30 runs/model) + 4 Hard Cases against Sol-High reference.
- **Key Findings**:
  - `luna-high`: Quality Mean **82.9 pt**, StdDev $\pm 20.4$, Cit. Prec 100%, Hallucinated IDs 0, Cost/run $0.002258 ➔ **BEST_DEFAULT**
  - `luna-xhigh`: Quality Mean **84.3 pt**, StdDev $\pm 20.1$, Cit. Prec 100%, Hallucinated IDs 0, Cost/run $0.003274 ➔ **BEST_DEEP_ANALYSIS**
  - `deepseek-v4-flash`: Quality Mean **84.1 pt**, StdDev $\pm 22.5$, Basic Pass 70%, Cost/run **$0.000278** ➔ **CHEAPEST_ACCEPTABLE**
  - `kimi-k3` & `terra-high`: Disqualified due to excessive cost/latency with no quality advantage over Luna.

---

## 6. Known Defects & Evaluator Repair Status

### Identified Defect Root Causes (from Phase 6.2 Failure Analysis)
1. **PESU-03 (Timeline Reconstruction)**:
   - *Cause*: Evaluator required exact string matching of Korean date literals (`8월 1일` ~ `8월 5일`). Models using standard date formats (`2026-08-01`, `8/1`, `2026. 8. 1.`) scored 0 on Factual Accuracy despite perfect event reconstruction.
   - *Fix in Phase 6.3*: Implemented `extract_canonical_dates()` and `normalize_keyword_match()` for format-invariant date comparison.
2. **PESU-06 & PESU-10 (Inconclusive / Insufficient Evidence)**:
   - *Cause*: Evaluator required exact keyword presence (`불충분`, `품의서`, `판단 유보`).
   - *Fix in Phase 6.3*: Added machine-readable semantic state fields (`conclusion_status`, `responsibility_determinable`, `evidence_sufficiency`) and semantic regex cluster matching for uncertainty/inconclusive expressions.

---

## 7. Next Steps

1. Review Raw AnalysisResult persistence audit status (`RESCORING_BLOCKED_BY_MISSING_RAW_RESULTS`).
2. Decide minimal re-run / execution plan for repaired evaluator rescoring.
3. Establish final model acceptance ranking and PII masking gate before real Slack data integration.
