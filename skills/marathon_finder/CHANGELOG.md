# Changelog — marathon_finder

## [0.1.0]
- 한국(marathonmate.store) 크롤링, 중국 남경(Claude web_search) 검색, DB upsert, 텔레그램 발송까지
  전체 흐름 구현
- 실제 사이트/API 호출로 end-to-end 검증 완료 (한국 24건 + 중국 1건 수집, 텔레그램 발송 성공)
- 스모크 테스트 추가 (`tests/test_main.py`)
