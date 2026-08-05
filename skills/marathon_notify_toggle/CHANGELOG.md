# Changelog — marathon_notify_toggle

## [0.1.0]
- `data/marathon.db`의 `race.notify_telegram`을 이름/id 검색으로 특정해 해제(`--disable`)/
  재활성화(`--enable`)하는 기능 구현
- 모호한 매칭(2건 이상)은 갱신하지 않고 후보 목록만 반환하도록 구현
- 이미 원하는 상태인 경우 DB를 건드리지 않는 멱등성 보장
- `--list`로 전체 대회 알림 상태 조회 기능 추가
- 스모크 테스트 추가 (`tests/test_main.py`)
