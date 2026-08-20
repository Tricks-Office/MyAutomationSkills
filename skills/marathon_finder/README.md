# marathon_finder

중국 남경(南京)시와 한국에서 열리는 5km 이상 도로 레이스(5km/10km/하프마라톤/풀코스 등) 중
현재 접수 가능한 대회를 자동으로 찾아 DB에 누적 관리하고, 텔레그램(주 채널)과 카카오톡
"러닝97" 방(보조 채널)으로 요약해 보내는 스킬.

- 요구사항 문서: [`docs/PRD.md`](docs/PRD.md), [`docs/SRS.md`](docs/SRS.md)

## 구성

- `src/main.py` — 실행 진입점 (크롤링 → 정규화 → DB upsert → 텔레그램 발송 → 카카오톡 발송)
- `tests/test_main.py` — 거리 파싱/DB upsert/알림 필터/`run()` 흐름 스모크 테스트
- `requirements.txt` — 의존성 (`requests`, `beautifulsoup4`, `anthropic`, `python-dotenv`)
- `skill.yaml` — Hermes 등록 메타데이터

## 동작 개요

1. **한국**: `marathonmate.store`의 `/marathon-schedule` 페이지를 크롤링해 접수중이며
   5km 이상 종목을 포함한 대회를 수집한다.
2. **중국(남경)**: Claude API(`web_search` 도구)로 남경시 행정구역 내 접수중인 대회를
   검색해 JSON으로 정리한다.
3. 두 결과를 공통 스키마로 정규화해 `data/marathon.db`(SQLite, 저장소 공용)에 upsert한다.
4. 이번 실행에서 수집된 대회 중 `notify_telegram=TRUE`인 대회를 텔레그램으로 요약 발송한다
   (접수 마감일은 원본 사이트에 명시되어 있지 않아 메시지에 포함하지 않는다 — `docs/SRS.md` 참고).
5. 텔레그램과 동일한 메시지를 `kakaotalk_sender` 스킬을 통해 카카오톡 "러닝97" 방에도
   보조 채널로 발송한다. `ai_news_telegram`과 동일한 패턴(서브프로세스 호출, best-effort)을
   따르며, 카카오톡 발송이 실패해도 텔레그램 발송이나 DB 반영 결과, 종료 코드에는 영향을
   주지 않는다.

## 실행 방법

```bash
# 저장소 루트에서
cp .env.example .env   # 최초 1회, TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID/ANTHROPIC_API_KEY 채우기
pip install -r skills/marathon_finder/requirements.txt
python skills/marathon_finder/src/main.py
```

## 입력

| 항목 | 필수 여부 | 설명 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 필수 | 저장소 공용 `.env` |
| `TELEGRAM_CHAT_ID` | 필수 | 저장소 공용 `.env` |
| `ANTHROPIC_API_KEY` | 필수 | 저장소 공용 `.env` |
| DB 파일 경로 | 선택 | 기본값 `data/marathon.db`, `run(db_path=...)`로 재정의 가능 |

## 출력

- `data/marathon.db`의 `race` 테이블에 신규/갱신 반영
- 접수중인 대회 목록(또는 0건 안내)을 텔레그램 메시지로 발송
- 같은 메시지를 카카오톡 "러닝97" 방에도 보조 채널로 발송(best-effort, 실패해도 완전
  실패로 치지 않음 — 카카오톡 발송 자체의 신뢰성/알려진 제약은
  [`kakaotalk_sender`](../kakaotalk_sender/docs/SRS.md) 참고)
- 실행 로그(수집 건수, 신규/갱신 건수, 발송 결과)

## 테스트

```bash
pip install pytest
pytest skills/marathon_finder/tests/
```

외부 API/사이트 호출은 mock으로 대체하며, 실제 연동 확인은 `python src/main.py` 수동 실행으로 한다.
