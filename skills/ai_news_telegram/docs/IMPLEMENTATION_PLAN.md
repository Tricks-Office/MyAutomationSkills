# Implementation Plan: ai_news_telegram

- 작성일: 2026-08-05
- 작성자: devjan
- 상태: 초안
- 관련 SRS: [`SRS.md`](./SRS.md) — 이 문서는 SRS를 대체하지 않는다. SRS는 "무엇을/왜" 만드는지,
  이 문서는 "어떤 순서로" 만드는지를 다룬다.

## 1. 단계로 나누는 이유
- 연동해야 하는 외부 API/서비스가 3개(Hacker News Algolia API, Anthropic Messages API,
  Telegram Bot API)로 2개 이상이다.
- 발송 이력 DB(중복 발송 방지)라는 상태 관리 로직이 있다 (SRS FR-6, FR-11).
- HN Algolia API의 정확한 쿼리 파라미터(`numericFilters`, `tags` 조합)와 응답 스키마는
  구현 전 실제 호출로 재검증이 필요하다고 SRS 8절에 명시되어 있다 — 선(先) 조사가 필요한
  불확실한 외부 스펙이 있다.
- 위 세 가지 모두 `CLAUDE.md` 3.1.1의 Implementation Plan 필요 기준에 해당한다.

## 2. Phase 개요
| Phase | 목표 | 관련 요구사항(SRS FR-ID) | 완료 기준 |
|---|---|---|---|
| Phase 0 | 뼈대: HN Algolia API 스펙 확인 + `.env`/`--mode` 로딩 + 텔레그램 발송 함수(하드코딩 데이터로 end-to-end 1회 성공) | FR-1, FR-2, FR-3(스파이크), FR-9(발송 함수만) | 하드코딩된 가짜 Top5/Top5로 실제 텔레그램 메시지 발송 성공 |
| Phase 1 | 규칙 기반 후보 수집 파이프라인(HN 검색 → AI 필터 → 카테고리 분류 → Hot 점수 정렬 → 후보 풀) | FR-3, FR-4, FR-5, FR-7, FR-12 | 두 모드(daily/weekly) 모두 실제 HN 데이터로 카테고리별 후보 풀을 로그로 확인 가능 |
| Phase 2 | Claude 랭킹/요약 연동 + 텔레그램 메시지 최종 포맷팅 | FR-8, FR-9, FR-10 | 실제 후보 풀로 end-to-end 실행 시 요구된 포맷(부족/0건 안내 포함)의 텔레그램 메시지가 발송됨 |
| Phase 3 | 발송 이력 DB(중복 방지) + 예외 처리 전체 | FR-6, FR-11, SRS 7절 예외 처리 전체 | 동일 item 재실행 시 중복 발송되지 않음, SRS 9절 테스트 시나리오 전체 통과 |

## 3. Phase별 상세

### Phase 0: 뼈대 + HN API 스펙 확인
- 범위:
  - `skills/ai_news_telegram/`에 `src/`, `tests/`, `requirements.txt` 골격 생성
  - HN Algolia API(`https://hn.algolia.com/api/v1/search*`)를 실제로 호출해 쿼리 파라미터
    (`tags=story`, `numericFilters=created_at_i>...`)와 응답 필드(points, num_comments,
    created_at_i, title, url, objectID 등)를 확인하고 SRS 8절 각주를 실제 스펙으로 갱신
  - `.env` 로드(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`) 및 `--mode`
    인자 파싱(값 검증 포함) 골격 작성
  - 텔레그램 발송 함수 작성(4096자 분할 포함, `marathon_finder`의 `send_telegram_message`
    패턴 재사용)
  - 하드코딩된 가짜 Top5(기술)/Top5(비즈니스) 데이터로 위 발송 함수를 호출해 실제 텔레그램
    수신까지 확인
- 이번 Phase에서 제외하는 것(다음 Phase로 미룸): 실제 HN 검색/분류/점수화 로직, Claude 호출,
  발송 이력 DB
- **실제 발송 여부 결정**: 실제 텔레그램 채팅으로 테스트 메시지를 보내는 대신, `requests.post`를
  mock한 스모크 테스트(발송 함수 호출/청크 분할/실패 처리 검증)로 Phase 0 배선 확인을 갈음하기로
  사용자와 합의함(2026-08-05). 실제 발송 확인은 Phase 2에서 최종 메시지 포맷이 나온 뒤 함께 진행.
- 완료 기준(Definition of Done): `python src/main.py --mode daily`가 하드코딩 데이터로 실제
  텔레그램 메시지 1건을 성공적으로 발송하고, HN Algolia API 스펙 확인 결과가 SRS 8절에
  반영됨
- 커밋 단위:
  1. `ai_news_telegram: 기본 골격(src/tests/requirements.txt) 구성`
  2. `ai_news_telegram: HN Algolia API 스펙 확인 결과 SRS 반영`
  3. `ai_news_telegram: 텔레그램 발송 함수 + mock 데이터 end-to-end 확인`

### Phase 1: 규칙 기반 후보 수집 파이프라인
- 범위:
  - HN Algolia 검색 함수: `daily`(24시간)/`weekly`(7일) 기간 필터 적용 (FR-3)
  - AI 관련 키워드 필터링 (FR-4)
  - 기술/비즈니스 카테고리 분류 키워드 매칭 로직, 동률 시 "기술" 우선 규칙 (FR-5)
  - Hot 점수식(`points + comments * 가중치`) 계산 및 카테고리별 상위 N건(기본 15건) 후보 풀
    구성 (FR-7)
  - 각 단계 결과를 `logging`으로 기록 (FR-12)
- 이번 Phase에서 제외하는 것: 발송 이력 DB 기반 제외(FR-6, Phase 3으로 미룸 — 이 Phase에서는
  이력 없이 전체 후보로 진행), Claude 호출, 실제 텔레그램 발송(로그 출력으로 검증)
- 완료 기준(Definition of Done): 두 모드 모두 실제 HN 데이터로 카테고리별 후보 풀(제목/URL/
  점수/댓글수)이 로그에 정상 출력됨. 소규모 스모크 테스트로 점수식/분류 함수 검증
- 커밋 단위:
  1. `ai_news_telegram: HN 검색 + 기간 필터 구현`
  2. `ai_news_telegram: AI 키워드 필터 + 카테고리 분류 구현`
  3. `ai_news_telegram: Hot 점수화 + 후보 풀 구성 구현, 스모크 테스트 추가`

### Phase 2: Claude 랭킹/요약 연동 + 메시지 포맷팅
- 범위:
  - 카테고리별 후보 풀(최대 30건)을 Claude API에 1회 전달해 카테고리별 최종 Top5(부족 시
    있는 만큼)와 한국어 제목/한줄 요약을 구조화된 JSON 스키마로 받기 (FR-8, `marathon_finder`의
    구조화 출력 패턴 참고)
  - Claude 응답 → 텔레그램 메시지 포맷팅: 카테고리 제목/순번/한국어 제목/요약/링크/점수·댓글수,
    4096자 초과 시 분할 (FR-9)
  - 후보 5건 미만/0건 시 안내 문구 반영 (FR-10)
- 이번 Phase에서 제외하는 것: 발송 이력 DB(FR-6, FR-11), SRS 7절의 세부 예외 처리 전체(이번
  Phase는 정상 경로 위주로 구현)
- 완료 기준(Definition of Done): 실제 후보 풀로 end-to-end 실행 시 SRS FR-9/FR-10 형식을
  만족하는 텔레그램 메시지가 발송됨
- 커밋 단위:
  1. `ai_news_telegram: Claude 랭킹/요약 1회 호출 구현`
  2. `ai_news_telegram: 텔레그램 메시지 포맷팅(부족/0건 안내 포함) 구현`

### Phase 3: 발송 이력 DB + 예외 처리 전체
- 범위:
  - `skills/ai_news_telegram/data/sent_items.db`(SQLite) 스키마 생성, 조회(이미 발송된 item
    제외, FR-6), 발송 성공 후 기록(FR-11)
  - SRS 7절 예외 처리 표 전체 구현(`.env`/`--mode` 검증, 각 API 실패 시 종료 코드, 로그 메시지)
  - SRS 9절 테스트 시나리오 전체를 `tests/`에 반영(정상/후보부족/0건/중복방지/각종 실패 케이스,
    외부 API는 mock 처리)
- 이번 Phase에서 제외하는 것: 없음 (SRS 요구사항 전체 충족이 목표)
- 완료 기준(Definition of Done): SRS 9절 테스트 시나리오 전부 통과, 동일 item으로 반복 실행 시
  중복 발송되지 않음
- 커밋 단위:
  1. `ai_news_telegram: 발송 이력 DB(중복 방지) 구현`
  2. `ai_news_telegram: 예외 처리 전체 구현`
  3. `ai_news_telegram: SRS 테스트 시나리오 기반 테스트 보강`

## 4. 불확실 요소 / 선(先) 확인 필요 항목
| 항목 | 불확실한 이유 | 확인 방법(스파이크/프로토타입 등) | 어느 Phase 전에 확인해야 하는가 |
|---|---|---|---|
| HN Algolia API 쿼리 파라미터/응답 스키마 | SRS 작성 시점에는 공개 문서 기준으로만 설계, 실제 호출 검증 안 함 | `requests`로 실제 엔드포인트 호출해 파라미터 조합과 응답 필드 확인 | Phase 0 |
| Claude 구조화 출력(JSON 스키마 강제) 방식 | `marathon_finder`는 `web_search` 도구 + `output_config.json_schema` 조합을 썼으나, 이번엔 web_search 없이 후보 리스트만 전달하는 순수 랭킹/요약 호출이라 동일 패턴이 그대로 적용되는지 확인 필요 | Anthropic SDK 문서 확인 + 소규모 프로토타입 호출 | Phase 2 |
| HN Algolia API 요청 빈도 제한(rate limit) | 공식 문서상 제한 여부가 명확히 파악되지 않음, daily/weekly 각 1회 호출이라 영향은 적을 것으로 추정 | 실제 호출 시 응답 헤더/에러 확인 | Phase 0 |

## 5. Phase 간 의존성
- 전체적으로 순차 진행이다: Phase 1은 Phase 0에서 확인한 HN API 스펙에 의존하고, Phase 2는
  Phase 1이 만드는 후보 풀 데이터 구조에 의존하며, Phase 3은 Phase 2의 최종 선정 결과(발송
  대상 item id)에 의존한다.
- Phase 0의 "텔레그램 발송 함수" 작성과 "HN API 스펙 확인"은 서로 독립적이라 병렬로 진행 가능
  하지만, 나머지 Phase는 병렬화 이점이 크지 않아 순차로 진행한다.

## 6. 중단 시 상태
- Phase 0까지만 완료 후 중단: 하드코딩 데이터 기반 텔레그램 발송 골격만 존재. 다른 스킬에는
  영향 없음(스킬 격리 원칙, `CLAUDE.md` 3.3). 실사용 불가.
- Phase 1까지만 완료 후 중단: 실제 후보 데이터를 얻을 수 있으나 텔레그램에는 발송되지 않고
  로그로만 확인 가능. 실사용 불가.
- Phase 2까지만 완료 후 중단: 실제로 텔레그램 발송까지 가능해 기능적으로는 사용할 수 있으나,
  중복 발송 방지가 없어 같은 뉴스가 반복 발송될 수 있음 (SRS FR-6/FR-11 미충족 상태로 운영
  가능은 함).
- Phase 3까지 완료: SRS 요구사항 전체 충족.
- 각 Phase는 다른 스킬 폴더를 참조하지 않으므로, 어느 시점에 중단되어도 `marathon_finder` 등
  기존 스킬의 동작에는 영향을 주지 않는다.
