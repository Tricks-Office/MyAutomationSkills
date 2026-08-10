# kakaotalk_sender

이 컴퓨터(macOS)에 설치되어 이미 로그인되어 있는 카카오톡 데스크톱 앱을 자동화해, 지정한
대화방(들)에 텍스트 메시지를 전송하는 범용 스킬. 세션 토큰 추출이나 비공식 API/프로토콜을
전혀 쓰지 않고, 이미 로그인된 앱 화면을 사람이 클릭·타이핑하는 것과 동일한 방식(macOS
Accessibility API + 실제 클릭/키 이벤트)으로 조작한다.

특정 리포트에 종속되지 않는 범용 발송기로 설계했다. 다른 스킬(예: `ai_news_telegram`류의
리포트 생성 스킬)에서 완성된 리포트를 카카오톡으로도 보내고 싶을 때, 이 스킬을
서브프로세스로 호출해 재사용한다.

- 요구사항 문서: [`docs/SRS.md`](docs/SRS.md) (PRD 역할을 겸함 — `docs/SRS.md` 헤더 참고)
- 구현 순서: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)

## 구성

- `src/main.py` — 실행 진입점 (검색으로 대화방 찾기 → 열기(이름 재확인) → 클립보드
  붙여넣기(확인 포함) → 전송 → 전송 검증)
- `tests/test_main.py` — 인자 검증/메시지 해석/앱 상태 분류/방별 결과 집계 스모크 테스트
  (실제 카카오톡 자동화 호출은 mock 처리)
- `requirements.txt` — 표준 라이브러리만 사용(추가 pip 의존성 없음)
- `skill.yaml` — Hermes 등록 메타데이터

## 사전 준비 (필수)

1. **카카오톡 데스크톱 앱**이 설치되어 있고 로그인되어 있어야 한다(자동 로그인 기능 없음).
2. **`cliclick`**(Homebrew 패키지)이 설치되어 있어야 한다. macOS 접근성 API의 합성
   클릭/일부 키 입력만으로는 카카오톡 앱의 커스텀 UI 요소가 반응하지 않는다는 것을 실제
   확인했다(`docs/SRS.md` 8절).
   ```bash
   # Homebrew가 없다면 먼저 설치 (관리자 비밀번호 필요, 터미널에서 직접 실행)
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

   brew install cliclick
   ```
3. **손쉬운 사용(Accessibility) 권한**을 이 스킬을 실행하는 터미널/프로세스에 부여해야
   한다: 시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용에서 추가/활성화.

## 실행 방법

```bash
# 저장소 루트에서
python skills/kakaotalk_sender/src/main.py --room "대화방 이름" --message "안녕하세요"

# 여러 방에 동시에 (순차 처리)
python skills/kakaotalk_sender/src/main.py --room "방A" --room "방B" --message "공지사항"

# 긴 텍스트(리포트 등)는 파일로
python skills/kakaotalk_sender/src/main.py --room "방A" --message-file report.txt
```

## 다른 스킬에서 재사용하기

`CLAUDE.md` 3.3(스킬 격리 원칙)에 따라 코드를 직접 import하지 않고, 완성된 리포트를 파일로
저장한 뒤 서브프로세스로 호출한다.

```python
import subprocess

subprocess.run(
    [
        "python",
        "skills/kakaotalk_sender/src/main.py",
        "--room", "AI 뉴스 알림방",
        "--message-file", "report.txt",
    ],
    check=True,
)
```

## 입력

| 항목 | 필수 여부 | 설명 |
|---|---|---|
| `--room` | 필수(반복 가능) | 메시지를 보낼 대화방 이름(정확히 일치해야 함) |
| `--message` | `--message-file`과 상호배타, 둘 중 하나 필수 | 전송할 메시지 본문 |
| `--message-file` | `--message`와 상호배타, 둘 중 하나 필수 | 전송할 메시지 본문 파일 경로 |

## 출력

- 실행 로그: 앱 준비 상태, 방별 검색/열기/붙여넣기 확인/전송/전송 검증 결과, 최종 요약
  (메시지 원문은 로그에 남기지 않는다 — 개인 대화 내용 보호)
- 종료 코드: 모든 방 성공 시 0, 하나라도 실패(완전 실패/부분 실패 포함) 시 0이 아닌 값
- DB/파일 등 영속 상태는 남기지 않는다(무상태 실행)

## 제약사항

- macOS 전용, 텍스트 메시지 전송만 지원(이미지/파일/이모티콘 없음), 자동 로그인 없음,
  자동 재시도 없음. 화면 잠금 상태에서의 정확한 동작은 미검증이다. 전체 목록은
  [`docs/SRS.md`](docs/SRS.md) 10절 참고.

## 테스트

```bash
pip install pytest
pytest skills/kakaotalk_sender/tests/
```

macOS UI 자동화(`osascript`/`cliclick`) 호출은 모두 mock으로 대체한다. 실제 전송 확인은
로그인된 카카오톡 계정과 테스트용 대화방이 있는 환경에서 수동 실행으로 한다.
