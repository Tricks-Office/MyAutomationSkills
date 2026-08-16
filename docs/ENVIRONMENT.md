# 환경 변수 (.env) 관리

여러 스킬이 공통으로 사용하는 비밀정보(API Key, Token 등)는 저장소 루트의 `.env` 파일 하나로
관리합니다. `.env`는 절대 커밋하지 않으며(`.gitignore`에 등록됨), 실제 값이 없는 템플릿인
`.env.example`만 저장소에 커밋합니다.

## 사용 방법
1. 저장소 루트의 `.env.example`을 `.env`로 복사합니다.
   ```bash
   cp .env.example .env
   ```
2. `.env` 파일을 열어 발급받은 실제 키 값을 채워 넣습니다.
3. 각 스킬의 `requirements.txt`에 `python-dotenv`를 추가하고, `src/main.py` 상단에서
   저장소 루트의 `.env`를 로드합니다.
   ```python
   from dotenv import load_dotenv
   from pathlib import Path
   import os

   load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # 저장소 루트의 .env
   api_key = os.environ["ANTHROPIC_API_KEY"]
   ```

## 등록된 변수 목록

| 변수명 | 설명 | 발급처 | 형식/예시 | 비고 |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API 인증 키 | https://console.anthropic.com/settings/keys | `sk-ant-...` | Anthropic 공식 SDK가 이 이름을 기본으로 자동 인식함 |
| `OPENAI_API_KEY` | OpenAI API 인증 키 | https://platform.openai.com/api-keys | `sk-...` | OpenAI 공식 SDK가 이 이름을 기본으로 자동 인식함 |
| `TELEGRAM_BOT_TOKEN` | Telegram 봇 API 토큰 | Telegram에서 [@BotFather](https://t.me/BotFather)와 대화해 봇 생성 후 발급 | `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` | 콜론(`:`) 앞은 봇 ID, 뒤는 인증 해시 |
| `TELEGRAM_CHAT_ID` | 봇이 메시지를 보낼 대상 chat_id (기본적으로 본인 개인 채팅) | 봇에게 메시지를 한 번 보낸 뒤 `https://api.telegram.org/bot<TOKEN>/getUpdates` 응답의 `result[0].message.chat.id` 확인, 또는 [@userinfobot](https://t.me/userinfobot)과 대화해 본인 id 확인 | `123456789` (양수는 개인, 음수는 그룹/채널) | 여러 스킬이 같은 본인 채팅으로 알림을 보낼 경우 공용으로 재사용 |
| `SLACK_TOKEN` | Slack App-Level Token | https://api.slack.com/apps → 앱 선택 → Basic Information → App-Level Tokens | `xapp-...` | Socket Mode 등 앱 단위 이벤트 구독에 사용 |
| `SLACK_BOT_USER_TOKEN` | Slack Bot User OAuth Token | https://api.slack.com/apps → 앱 선택 → OAuth & Permissions → Bot User OAuth Token | `xoxb-...` | 메시지 전송 등 봇으로 API를 호출할 때 사용 (Web API 호출은 대부분 이 토큰 사용) |
| `GOOGLE_CLIENT_ID` | Google OAuth2 클라이언트 ID | https://console.cloud.google.com/apis/credentials | `xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com` | `GOOGLE_CLIENT_SECRET`과 한 쌍으로 사용 |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 클라이언트 보안 비밀번호 | https://console.cloud.google.com/apis/credentials | `GOCSPX-...` | `GOOGLE_CLIENT_ID`와 한 쌍으로 사용 |
| `NOTION_API_KEY` | Notion Integration Token | https://www.notion.so/my-integrations | `ntn_...`(구 `secret_...`) | 사용할 Notion 페이지/데이터베이스에 이 Integration을 개별로 공유(Connect)해야 접근 가능 |

> 새 공용 변수가 필요하면 이 표에 행을 추가하고, `.env.example`에도 같은 이름으로
> 플레이스홀더를 추가합니다. 특정 스킬 하나에서만 쓰는 값(예: 특정 스킬 전용 API Key)은
> 이 표 대신 해당 스킬의 `docs/PRD.md`(또는 `SRS.md`)와 `README.md`에 기록합니다.

## 원칙
- 변수명은 각 서비스의 공식 SDK/문서가 기본으로 인식하는 이름을 그대로 사용합니다
  (별도 설정 없이 SDK가 바로 읽도록 하여 혼동을 줄임).
- `.env`는 절대 커밋하지 않습니다. 커밋 전 `git status`/`git diff`로 항상 확인합니다.
- 키를 재발급/폐기한 경우 이 문서의 발급처 링크는 유지하고, 실제 값은 각자의 `.env`에서만 갱신합니다.
