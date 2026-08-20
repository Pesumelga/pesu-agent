# Pesu Agent - Slack Desktop UI Automation & Message Parser (MVP 0 & MVP 1)

Windows 환경에서 실행 중인 Slack 데스크톱 애플리케이션의 **Microsoft UI Automation (UIA) Tree**를 안전하게 탐색하고, 현재 화면에 노출된 가시적 메시지를 구조화된 데이터로 추출하는 개인용 로컬 에이전트 프로젝트입니다.

---

## 📌 주요 목적 및 특징

- **Windows 전용**: Microsoft UI Automation (UIA backend) API 사용
- **안전한 읽기 전용 (Strict Read-Only)**:
  - ❌ 메시지 전송, 텍스트 입력, 버튼 클릭, 검색 실행 등 일체의 UI 조작을 수행하지 않습니다.
  - ❌ 쿠키, 인증 토큰, 로컬 캐시/DB 직접 분석, 패킷 가로채기를 수행하지 않습니다.
  - ⭕ 실행 중인 Slack 창의 Accessibility / UI 계층 정보만 순수하게 읽어옵니다.
- **동적 창 탐색**: 특정 프로세스 ID(PID)나 회사/워크스페이스/채널명을 하드코딩하지 않고, 실행 중인 Slack 데스크톱 메인 창을 감지합니다.
- **방어적 파싱**: 개별 UI 속성이나 subtree 오류가 발생해도 전체 파싱이 중단되지 않습니다.
- **가시적 메시지 정규화 (MVP 1)**:
  - 작성자(`author`), Slack 원본 시간(`timestamp_raw`), 멘션(`mentions`), 링크(`links`), 본문(`text`), 컨텍스트(`context`), 트리 깊이(`tree_depth`) 추출
  - 동일 메시지 컨테이너의 subtree 내에서만 속성을 조합하여 메시지 간 데이터 혼합 방지
- **UI Virtualization 인식**:
  - `scope = "visible_uia_only"`, `is_complete_conversation = false` 메타데이터를 명시하여 현재 화면 노출 영역과 전체 대화를 명확히 구분합니다.

---

## 🛠 사전 요구사항 및 설치

### 1. 사전 요구사항
- **OS**: Windows 10 / 11
- **Python**: 3.10+
- **Slack 데스크톱 앱**: 실행 전 Slack 데스크톱 앱이 실행되어 있어야 합니다.

### 2. 의존성 설치

```powershell
pip install -r requirements.txt
```

주요 패키지:
- `pywinauto` (UIA 백엔드 접근)
- `rich` (터미널 계층 구조 및 서식 출력)
- `pydantic` (데이터 모델링 및 검증)
- `psutil` (프로세스 탐색 및 식별)
- `pytest` (단위 테스트)

---

## 🚀 실행 방법

### 1. 가시적 메시지 파싱 (MVP 1)

Slack 데스크톱 앱을 실행한 상태에서 아래 명령을 실행합니다.

```powershell
# 실시간 Slack 화면에서 가시적 메시지 파싱
python scripts/parse_slack_messages.py

# 이미 캡처된 UIA JSON 파일로부터 메시지 파싱
python scripts/parse_slack_messages.py --from-json output/slack_uia_tree.json

# 결과 파일 경로 변경
python scripts/parse_slack_messages.py --output output/slack_visible_messages.json
```

### 2. UI Automation Tree 원본 검사 (MVP 0)

```powershell
# 기본 실행 (max-depth 20, max-elements 3000)
python scripts/inspect_slack.py

# 옵션 지정 실행
python scripts/inspect_slack.py --max-depth 25 --max-elements 3000 --output output/slack_uia_tree.json
```

---

## 📊 결과 데이터 형식

### 가시적 메시지 JSON (`output/slack_visible_messages.json`)

```json
{
  "captured_at": "2026-08-20T04:17:23.057855+00:00",
  "slack_window_title": "* 채널명 - 워크스페이스 - Slack",
  "message_count": 12,
  "scope": "visible_uia_only",
  "is_complete_conversation": false,
  "excluded_candidates_count": 3,
  "messages": [
    {
      "author": "홍길동",
      "timestamp_raw": "오늘, 오후 12:29:12",
      "text": "업무 전달 알림 [담당자] @김영희 [상담건] https://example.com/item/123",
      "mentions": ["@김영희"],
      "links": ["https://example.com/item/123"],
      "context": "channel",
      "source_node_name": "홍길동 오후 12:29 ...",
      "tree_depth": 15
    },
    {
      "author": null,
      "timestamp_raw": "오늘, 오후 12:30:00",
      "text": "추가 확인 부탁드립니다.",
      "mentions": [],
      "links": [],
      "context": "channel",
      "source_node_name": "12:30 추가 확인 부탁드립니다.",
      "tree_depth": 15
    }
  ]
}
```

---

## 🧪 테스트 실행

오프라인 단위 테스트 (Slack 미실행 상태에서도 전체 16개 테스트 통과):

```powershell
pytest
```

실제 실행 중인 Slack을 대상으로 하는 스모크 테스트 포함 실행:

```powershell
pytest -m "live_slack or not live_slack"
```

---

## ⚠️ 현재 알려진 제한사항

1. **UI Virtualization (가상화 리스트)**: Slack 데스크톱(Electron)의 메시지 리스트는 화면에 현재 렌더링된 뷰포트 영역 위주로 UIA 트리에 노출됩니다. 스크롤 밖의 과거 대화는 화면에 렌더링되지 않으므로 현재 결과가 전체 대화가 아닙니다.
2. **연속 메시지(Consecutive Messages)의 작성자 생략**: Slack UI 특성상 동일 작성자가 연속으로 보낸 메시지는 두 번째 메시지부터 작성자 버튼이 렌더링되지 않으므로 `author`가 `null`로 추출됩니다.
3. **트레이 최소화 상태**: Slack 창이 시스템 트레이로 완전히 숨겨져(Minimized to tray) 렌더링되지 않는 경우, UIA Tree 상에서 자식 요소가 비어 있거나 창 핸들을 찾지 못할 수 있습니다.