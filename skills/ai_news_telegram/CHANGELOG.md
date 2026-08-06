# Changelog — ai_news_telegram

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
