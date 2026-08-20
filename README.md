# Pesu Agent - Slack Desktop UI Automation Inspector (MVP 0)

Windows 환경에서 실행 중인 Slack 데스크톱 애플리케이션의 **Microsoft UI Automation (UIA) Tree**를 안전하게 탐색하고, 사람이 분석할 수 있도록 콘솔(Rich 트리) 및 JSON 파일로 추출하는 **MVP 0 기술검증 도구**입니다.

---

## 📌 주요 목적 및 특징

- **Windows 전용**: Microsoft UI Automation (UIA backend) API를 사용합니다.
- **안전한 읽기 전용 (Strict Read-Only)**:
  - ❌ 메시지 전송, 텍스트 입력, 버튼 클릭, 검색 실행 등 일체의 UI 인터랙션을 수행하지 않습니다.
  - ❌ 쿠키, 인증 토큰, 로컬 캐시/DB 직접 분석, 패킷 가로채기 등을 수행하지 않습니다.
  - ⭕ 실행 중인 Slack 창의 Accessibility / UI 계층 정보만 순수하게 읽어옵니다.
- **동적 창 탐색**: 특정 프로세스 ID(PID)나 회사/워크스페이스/채널명을 하드코딩하지 않고, 실행 중인 Slack 데스크톱 메인 창을 감지합니다.
- **방어적 속성 추출**: 특정 UI 요소의 속성 읽기 오류가 발생해도 전체 트리 탐색이 중단되지 않습니다.
- **트리 잘림(Truncation) 추적**: `max_depth` 또는 `max_elements` 한도 도달 시 `is_truncated` 및 `truncation_reason`을 명시하여 '요소 없음'과 '한도 초과'를 명확히 구분합니다.
- **직관적인 콘솔 & JSON 출력**: `rich` 라이브러리를 통한 시각적 계층 출력과 한글이 보존되는 UTF-8 JSON 파일 저장을 지원합니다.

---

## 🛠 사전 요구사항 및 설치

### 1. 사전 요구사항
- **OS**: Windows 10 / 11
- **Python**: 3.10+
- **Slack 데스크톱 앱**: **검사 실행 전 Slack 데스크톱 앱이 실행되어 있어야 합니다.** (창이 화면에 표시되어 있는 상태 권장)

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

### 기본 실행
Slack 데스크톱 앱을 실행한 상태에서 아래 명령을 실행합니다.

```powershell
python scripts/inspect_slack.py
```

### 옵션 지정 실행

```powershell
# 최대 탐색 깊이 20, 최대 수집 요소 수 3000 지정
python scripts/inspect_slack.py --max-depth 20 --max-elements 3000

# 결과 저장 경로 변경 및 콘솔 출력 생략 (파일만 저장)
python scripts/inspect_slack.py --output output/my_slack_tree.json --no-console

# 콘솔에 표시할 트리 깊이 및 요소 수 조절
python scripts/inspect_slack.py --console-max-depth 10 --console-max-elements 300

# 디버그 모드 (Windows Desktop 컨텍스트 진단 정보 출력)
python scripts/inspect_slack.py --debug
```

### CLI 옵션 목록

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--max-depth` | `20` | UI Automation Tree 재귀 탐색 최대 깊이 |
| `--max-elements` | `3000` | 수집할 최대 UI 요소 개수 |
| `--output`, `-o` | `output/slack_uia_tree.json` | 결과 JSON 파일 저장 경로 |
| `--console-max-depth` | `12` | 터미널 콘솔에 출력할 트리의 최대 깊이 |
| `--console-max-elements` | `500` | 터미널 콘솔에 출력할 트리의 최대 노드 개수 |
| `--no-console` | `False` | 콘솔 트리 출력을 생략하고 요약 표 및 파일 저장만 수행 |
| `--debug` | `False` | 데스크톱 세션 진단 및 상세 로그 출력 |

---

## 📊 결과 데이터 형식

결과 파일은 기본적으로 다음 위치에 UTF-8 인코딩으로 저장됩니다:
```text
output/slack_uia_tree.json
```

### JSON 구조 요약

```json
{
  "timestamp": "2026-08-20T04:17:21.123456Z",
  "duration_seconds": 1.649,
  "slack_window_title": "* 채널명 - 워크스페이스 - Slack",
  "slack_process_id": 17552,
  "total_elements": 614,
  "max_depth_reached": 18,
  "is_truncated": false,
  "truncation_reasons": [],
  "control_type_counts": {
    "Group": 187,
    "TreeItem": 166,
    "Text": 101,
    "Button": 54,
    "Hyperlink": 36,
    "ListItem": 15,
    "Pane": 13,
    "Document": 13,
    "TabItem": 12,
    "ToolBar": 6,
    "Edit": 2,
    "Window": 1,
    "List": 1
  },
  "root": {
    "depth": 0,
    "name": "* 채널명 - 워크스페이스 - Slack",
    "control_type": "Window",
    "automation_id": "",
    "class_name": "Chrome_WidgetWin_1",
    "is_enabled": true,
    "is_visible": true,
    "rectangle": {
      "left": -8,
      "top": -8,
      "right": 1928,
      "bottom": 1040,
      "width": 1936,
      "height": 1048
    },
    "process_id": 17552,
    "is_truncated": false,
    "truncation_reason": null,
    "children": [ ... ]
  }
}
```

---

## 🧪 테스트 실행

오프라인 단위 테스트 (Slack 미실행 상태에서도 전체 통과):

```powershell
pytest
```

실제 실행 중인 Slack을 대상으로 하는 스모크 테스트 포함 실행:

```powershell
pytest -m "live_slack or not live_slack"
```

---

## ⚠️ 현재 알려진 제한사항

1. **가상화 리스트 (Virtualization)**: Slack 데스크톱(Electron)의 메시지 리스트(`List` / `ListItem`)는 성능 최적화를 위해 현재 화면에 스크롤되어 보이는 영역(Visible Viewport) 위주로 UIA 트리에 노출됩니다. 과거 메시지를 읽으려면 향후 단계에서 스크롤 패턴 검증이 필요합니다.
2. **트레이 최소화 상태**: Slack 창이 시스템 트레이로 완전히 숨겨져(Minimized to tray) 렌더링되지 않는 경우, UIA Tree 상에서 자식 요소가 비어 있거나 창 핸들을 찾지 못할 수 있습니다.
3. **읽기 전용 MVP**: 본 버전은 UIA 구조 검증용 도구이며, 메시지 파싱(SlackMessageParser)이나 자동화 인터랙션, LLM 연결은 포함되어 있지 않습니다.