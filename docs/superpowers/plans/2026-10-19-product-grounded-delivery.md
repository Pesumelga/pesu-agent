# PESU 실제품 근거 답변 연결 계획

목적: 기존 앱의 실제 실행 버튼이 자연어 계획→검증된 Slack 검색→근거/주장/업무 상태→출처 답변을 수행하도록 연결하고 실행 패키지를 만든다. 합성 미리보기는 납품물이 아니다.

기존 검색·actor/temporal 제약·동의·credential·세션 수명주기 및 동결 Stage 의미는 재사용한다. 제품 service의 기본 factory만 기존 coordinator를 조립하며 semantic provider에 stub을 넣지 않는다. 실제 서비스 호출은 명시적 Live/외부전송 승인 전 하지 않는다.

- [x] 제품 기본 factory에 실제 shared provider와 bounded trace coordinator 연결. Orchestrator는 검색/조건 검증을 유지하고 기존 freeform 분석 대신 trace 결과를 생성한다. 서비스는 결과의 trace로 grounded 화면과 출처/기록을 구성한다.
- [x] 기본 factory부터 worker/presenter/기록까지 합성 provider transport와 backend 경계만 대체한 회귀 검증. provider 실패·근거 부족·동의·예산 초과·요청 간 상태 격리를 확인한다.
- [x] 기존 spec으로 새 폴더에 실행 파일을 빌드하여 이전 배포물을 보존한다. --version 및 기존 UI 시작 검증을 실행한다. 자격 증명/원문은 패키지에 넣지 않는다.
- [x] 검증 결과/제한을 문서화하고 로컬 커밋한다. 실제 Slack/LLM end-to-end 승인이 필요한 경우 실행 가능한 패키지와 정확한 한정 범위를 먼저 제시한다. 실제 검증 전에는 최종 완성으로 주장하지 않는다.

검증: 관련 오프라인 109 PASS (4.62s), 최종 RC5 네이티브 시작 PASS (7.25s). 실제 Slack/provider 전체 흐름은 새 승인 전 미실행. 기존 증거 794개 동일. 실행 패키지와 승인 경계는 PROJECT_STATE/NEXT_ACTIONS에 기록한다.
