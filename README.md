# Pesu Agent - Slack Desktop UI Automation & Message Parser (MVP 0 ~ MVP 1.2)

Windows 환경에서 실행 중인 Slack 데스크톱 애플리케이션의 **Microsoft UI Automation (UIA) Tree**를 안전하게 탐색하고, 현재 화면에 노출된 가시적 메시지를 구조화된 데이터로 추출 및 보정하는 개인용 로컬 에이전트 프로젝트입니다.

---

## 📌 주요 목적 및 특징

- **Windows 전용**: Microsoft UI Automation (UIA backend) API 사용
- **안전한 읽기 전용 (Strict Read-Only)**:
  - ❌ 메시지 전송, 텍스트 입력, 버튼 클릭, 검색 실행 등 일체의 UI 조작을 수행하지 않습니다.
  - ❌ 쿠키, 인증 토큰, 로컬 캐시/DB 직접 분석, 패킷 가로채기를 수행하지 않습니다.
  - ⭕ 실행 중인 Slack 창의 Accessibility / UI 계층 정보만 순수하게 읽어옵니다.
- **동적 창 탐색**: 특정 프로세스 ID(PID)나 회사/워크스페이스/채널명을 하드코딩하지 않고, 실행 중인 Slack 데스크톱 메인 창을 감지합니다.
- **방어적 파싱**: 개별 UI 속성이나 subtree 오류가 발생해도 전체 파싱이 중단되지 않습니다.
- **메시지 신뢰성 및 작성자 보정 (MVP 1.1)**:
  - `author_raw`: UIA subtree에서 실제 발견된 원본 작성자
  - `author_resolved`: 직접 발견 또는 동일 컨테이너 직전 메시지로부터 상속된 보정 작성자
  - `author_resolution`: 보정 상태 구분 (`explicit`, `inherited_from_previous_message`, `unresolved`)
  - 컨테이너 경계(채널/스레드 등)를 넘는 잘못된 상속 방지 및 첫 화면 작성자 부재 시 추측 없이 `unresolved` 처리
- **Scroll-Safe Message Identity & Fingerprint (MVP 1.2)**:
  - `message_fingerprint`: 스크롤 뷰포트에 따라 변할 수 있는 `author_resolved`를 제외하고, UIA 불변 속성(`context`, `timestamp_raw`, `text`, `mentions`, `links`, `uia_message_id`)만을 조합하여 생성한 불변 SHA-256 해시값.
  - `viewport_index`: 현재 뷰포트 내 표시 순서(0부터 시작).
  - 중첩 글머리 기호(`p-rich_text_list`)의 자식 ListItem을 별도 메시지로 분리하지 않고 상위 메시지 본문으로 통합.
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

### 1. 가시적 메시지 파싱 및 Scroll-Safe Identity (MVP 1.2)

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
  "captured_at": "2026-08-20T05:54:59.123456+00:00",
  "slack_window_title": "김학진(DM) - Alfred - Slack",
  "message_count": 28,
  "scope": "visible_uia_only",
  "is_complete_conversation": false,
  "excluded_candidates_count": 17,
  "explicit_author_count": 23,
  "inherited_author_count": 5,
  "unresolved_author_count": 0,
  "unique_fingerprints_count": 28,
  "duplicate_fingerprint_groups_count": 0,
  "messages": [
    {
      "viewport_index": 0,
      "author": "이주영",
      "author_raw": "이주영",
      "author_resolved": "이주영",
      "author_resolution": "explicit",
      "timestamp_raw": "2월 24일, 오전 9:24:21",
      "text": "넵",
      "mentions": [],
      "links": [],
      "context": "channel",
      "source_container": "List:sr-only:14",
      "source_node_name": "이주영 오전 9:24 넵",
      "tree_depth": 15,
      "message_fingerprint": "7c1fb74c6e2e533890f5b12..."
    },
    {
      "viewport_index": 1,
      "author": "김학진",
      "author_raw": null,
      "author_resolved": "김학진",
      "author_resolution": "inherited_from_previous_message",
      "timestamp_raw": "2월 24일, 오전 9:24:38",
      "text": "지금?",
      "mentions": [],
      "links": [],
      "context": "channel",
      "source_container": "List:sr-only:14",
      "source_node_name": "9:24지금?",
      "tree_depth": 15,
      "message_fingerprint": "8088dc39d268276c2437df4..."
    }
  ]
}
```

---

## 🧪 테스트 실행

오프라인 단위 테스트 (Slack 미실행 상태에서도 전체 17개 테스트 통과):

```powershell
pytest
```

실제 실행 중인 Slack을 대상으로 하는 스모크 테스트 포함 실행:

```powershell
pytest -m "live_slack or not live_slack"
```

---

## ⚠️ Scroll-Safe Identity 및 중복 처리 원칙

1. **Fingerprint의 뷰포트 불변성 (Scroll-Safe)**:
   - `author_resolved`는 스크롤 위치에 따라 상속 여부가 바뀔 수 있으므로 fingerprint 해시 생성 시 제외됩니다.
   - 따라서 동일 메시지는 뷰포트 상단에 노출되든 하단에 노출되든 동일한 `message_fingerprint`를 가집니다.
2. **동일 본문 반복 전송 건**:
   - 봇 알림이나 짧은 응답(예: "넵", 동일 템플릿 알림)이 동일 시간대/컨텍스트에서 정확히 같은 내용으로 중복 전송된 경우 동일 fingerprint를 가질 수 있으며, 이는 파서 오류가 아닌 실제 내용 중복입니다. UIA `automation_id`의 타임스탬프(`ts`)가 노출되는 경우 이를 함께 반영하여 고유성을 강화합니다.
3. **가상화(Virtualization) 고려**:
   - `viewport_index`는 현재 화면 내 순서이며, MVP 2 스크롤 수집 시 이전/이후 뷰포트의 `message_fingerprint`를 연결하여 중복 없는 전체 대화 스트림을 구축할 수 있습니다.