# Automation_Script

로컬 컴퓨터의 **Hermes**가 필요할 때 불러 쓰는 자동화 Python 스크립트("스킬") 모음입니다.
운영 원칙과 폴더 구조는 [CLAUDE.md](CLAUDE.md)를 참고하세요.

## 스킬 목록

| 스킬 | 설명 | 상태 |
|---|---|---|
| _example_skill | 신규 스킬 작성 시 참고용 골격 예시 (실제 스킬 아님) | 예시 |
| [ai_news_telegram](skills/ai_news_telegram/) | Hacker News에서 AI 기술/비즈니스 Hot 뉴스를 각 5건씩 골라 한국어로 요약해 텔레그램으로 발송 (daily/weekly) | 완료 |
| [marathon_finder](skills/marathon_finder/) | 한국/중국 남경의 접수중인 5km 이상 도로 레이스를 찾아 DB에 저장하고 텔레그램으로 요약 발송 | 완료 |
| [marathon_notify_toggle](skills/marathon_notify_toggle/) | 등록된 마라톤 대회의 텔레그램 알림을 이름/id로 특정해 해제하거나 재활성화 | 완료 |
| [marathon_search](skills/marathon_search/) | 등록된 마라톤 대회를 알림 여부/기간/장소/대회명 조건(AND/OR)으로 검색해 조회 | 완료 |

## 새 스킬 추가하기
[CLAUDE.md의 "신규 스킬 추가 워크플로우"](CLAUDE.md#4-신규-스킬-추가-워크플로우-체크리스트) 참고.
