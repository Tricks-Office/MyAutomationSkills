# Changelog — marathon_search

## [0.1.0]
- `data/marathon.db`의 `race` 테이블을 알림 여부/기간/장소/대회명 조건으로 검색하는 읽기 전용
  기능 구현
- 4개 필터 카테고리 중 최소 1개 필수, `--match`로 카테고리 간 AND/OR 결합 지원
- 매칭 0건은 오류가 아닌 정상 검색 결과로 처리(종료 코드 0)
- 스모크 테스트 추가 (`tests/test_main.py`)
