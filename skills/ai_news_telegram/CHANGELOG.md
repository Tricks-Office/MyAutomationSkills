# Changelog — ai_news_telegram

## [0.2.0]
- 텔레그램 발송/발송 이력 기록이 끝난 뒤, 동일한 메시지를 `kakaotalk_sender` 스킬을
  서브프로세스로 호출해 카카오톡 대화방("금칠", "화공97", "고대97")에도 발송
  (`send_kakaotalk_notification`)
- 카카오톡 발송은 보조 채널로 취급해 best-effort로 설계 — macOS UI 자동화 기반이라
  텔레그램 API보다 실패 가능성이 높다고 보고, 실패해도 완전 실패로 처리하지 않으며
  `run()`의 종료 코드나 이미 완료된 텔레그램 발송/발송 이력 기록에는 영향을 주지 않음
- `CLAUDE.md` 3.3(스킬 격리)에 따라 `kakaotalk_sender`의 코드는 import하지 않고
  서브프로세스로만 연동
- PRD/SRS에 FR-14/FR-15 반영, 실제 카카오톡 계정으로 연동 end-to-end 검증 완료
- 스모크 테스트 9개 추가(카카오톡 발송 성공/실패/예외/임시 파일 처리)

## [0.1.1]
- 운영 중 HN Algolia API 응답 지연으로 스킬 전체가 실패(exit 1)하는 장애 확인 후 대응
- HN 검색 호출(`_search_hn`)에 연결 오류/타임아웃/5xx 재시도 로직 추가 — 최대 3회 시도,
  1초·2초 지수 백오프. 4xx 응답은 재시도해도 결과가 같아 제외
- HN 요청 타임아웃을 30초 → 45초로 상향 (Claude/텔레그램 타임아웃은 그대로 30초 유지)
- Claude API·텔레그램 발송은 중복 과금/중복 발송 우려로 재시도 대상에서 의도적으로 제외
- PRD/SRS에 재시도 정책(SRS FR-13) 반영, 재시도 시나리오 테스트 5개 추가

## [0.1.0]
- Hacker News Algolia API 검색 → AI 키워드 필터 → 기술/비즈니스 카테고리 분류 → Hot 점수화
  (`points + comments * 2.0`)까지 규칙 기반 파이프라인 구현
- Claude API를 실행당 1회만 호출해 카테고리별 최종 Top5 랭킹 + 한국어 요약 생성 (구조화 출력)
- 텔레그램 메시지 포맷팅(후보 부족/0건 안내 포함) 및 발송
- 발송 이력 DB(`data/sent_items.db`)로 중복 발송 방지
- `daily`(최근 24시간)/`weekly`(최근 7일) 두 실행 모드 지원 (`--mode`)
- 실제 HN/Claude/Telegram API 호출로 end-to-end 검증 완료 — 동일 모드 연속 2회 실행 시
  발송 대상이 전혀 겹치지 않음을 확인
- 스모크 테스트 43개 추가 (`tests/test_main.py`)
