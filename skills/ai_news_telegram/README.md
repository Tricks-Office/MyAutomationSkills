# ai_news_telegram

Hacker News에서 AI 관련 스토리를 수집해 규칙 기반으로 "기술"/"비즈니스" 카테고리로
분류하고, 각 카테고리에서 가장 화제성 있는(Hot) 5건을 골라 한국어로 요약해 텔레그램으로
발송하는 스킬. `daily`(최근 24시간)/`weekly`(최근 7일) 두 모드를 지원한다.

- 요구사항 문서: [`docs/PRD.md`](docs/PRD.md), [`docs/SRS.md`](docs/SRS.md)
- 구현 순서: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)

## 구성

- `src/main.py` — 실행 진입점 (HN 검색 → 규칙 기반 필터/분류/점수화 → Claude 랭킹/요약 →
  텔레그램 발송 → 발송 이력 기록)
- `tests/test_main.py` — 키워드 필터/카테고리 분류/Hot 점수화/발송 이력 dedup/`run()` 흐름
  스모크 테스트
- `requirements.txt` — 의존성 (`requests`, `python-dotenv`, `anthropic`)
- `skill.yaml` — Hermes 등록 메타데이터

## 동작 개요

1. Hacker News Algolia Search API에서 AI 관련 키워드로 스토리를 검색한다(기간 필터:
   daily=24시간, weekly=7일). 이미 발송한 적 있는 글(`data/sent_items.db`)은 제외한다.
2. title/본문에 AI 키워드가 실제로 있는지 재검증하고, 기술/비즈니스 키워드 매칭 개수로
   카테고리를 나눈다(전부 규칙 기반, LLM 미사용).
3. `points + comments * 2.0`로 계산한 Hot 점수 상위 15건씩을 카테고리별 후보 풀로 추린다.
4. 후보 풀을 Claude API에 **실행당 1회만** 전달해 카테고리별 최종 Top5와 한국어 제목/한줄
   요약을 구조화된 JSON으로 받는다(후보가 아예 없으면 이 호출 자체를 건너뛴다).
5. 결과를 텔레그램 메시지로 포맷팅해 발송하고(후보 부족/0건 안내 문구 포함), 발송된 글의
   ID를 `data/sent_items.db`에 기록해 다음 실행에서 중복 발송을 막는다.

## 실행 방법

```bash
# 저장소 루트에서
cp .env.example .env   # 최초 1회, TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID/ANTHROPIC_API_KEY 채우기
pip install -r skills/ai_news_telegram/requirements.txt
python skills/ai_news_telegram/src/main.py --mode daily   # 또는 --mode weekly
```

## 입력

| 항목 | 필수 여부 | 설명 |
|---|---|---|
| `--mode` | 필수 | `daily`(최근 24시간) 또는 `weekly`(최근 7일) |
| `TELEGRAM_BOT_TOKEN` | 필수 | 저장소 공용 `.env` |
| `TELEGRAM_CHAT_ID` | 필수 | 저장소 공용 `.env` |
| `ANTHROPIC_API_KEY` | 필수 | 저장소 공용 `.env` (실행당 Claude API 1회 호출) |
| 발송 이력 DB 경로 | 선택 | 기본값 `skills/ai_news_telegram/data/sent_items.db`, `run(db_path=...)`로 재정의 가능 |

## 출력

- 텔레그램 메시지 1건: "📌 AI 기술 Hot 5" + "💼 AI 비즈니스 Hot 5" (후보 부족/0건 안내 포함)
- `skills/ai_news_telegram/data/sent_items.db`에 이번에 발송한 item_id 기록 (스킬 전용
  로컬 데이터라 저장소 공용 `data/`가 아닌 스킬 폴더 내부에 둔다, 커밋 대상 아님)
- 실행 로그(검색/필터/후보 풀/랭킹 건수, 발송 결과)

## 테스트

```bash
pip install pytest
pytest skills/ai_news_telegram/tests/
```

HN Algolia API/Claude API/Telegram Bot API 호출은 모두 mock으로 대체하며, 실제 연동 확인은
`python src/main.py --mode daily` 수동 실행으로 한다.
