# marathon_search

[marathon_finder](../marathon_finder/)가 `data/marathon.db`의 `race` 테이블에 수집해 둔 대회를
알림 여부/기간/장소/대회명 조건으로 검색하는 읽기 전용 스킬. Hermes가 사용자의 자연어 질문
("서울에서 열리는 대회 알려줘", "9월에 있는 대회 뭐 있어")을 커맨드라인 인자로 변환해 호출하고,
표준출력 결과를 그대로 사용자에게 회신한다.

- 요구사항 문서: [`docs/PRD.md`](docs/PRD.md)

## 구성

- `src/main.py` — 실행 진입점 (필터 조건 파싱 → SQL 검색 → 결과 출력)
- `tests/test_main.py` — 필터별/AND·OR 조합/`run()` 흐름 스모크 테스트
- `requirements.txt` — 외부 의존성 없음 (표준 라이브러리만 사용)
- `skill.yaml` — Hermes 등록 메타데이터

## 실행 방법

```bash
# 저장소 루트에서 (marathon_finder가 이미 만들어 둔 data/marathon.db 필요)
python skills/marathon_search/src/main.py --location 서울
python skills/marathon_search/src/main.py --date-from 2026-09-01 --date-to 2026-09-30 --location 서울
python skills/marathon_search/src/main.py --notify off --location 서울 --match or
```

## 입력

| 인자 | 필수 여부 | 설명 |
|---|---|---|
| `--notify` | 4개 필터 카테고리 중 최소 1개 필요 | `on`/`off` — `notify_telegram` 필터 |
| `--date-from` / `--date-to` | 〃 | 대회 개최일(`race_date`) 범위(`YYYY-MM-DD`), 하나만 줘도 됨(열린 범위) |
| `--location` | 〃 | 장소 부분 일치 검색어 |
| `--name` | 〃 | 대회명 부분 일치 검색어 |
| `--match` | 선택 (기본 `and`) | 2개 이상 카테고리 지정 시 `and`/`or` 결합 방식 |

4개 카테고리(알림/기간/장소/대회명) 중 최소 1개는 반드시 지정해야 한다. `--match`는 카테고리
"사이"에만 적용되며, 기간 안의 `--date-from`/`--date-to`는 항상 AND로 결합된다.

## 출력

- 매칭 1건 이상: 검색 조건 요약 + 총 건수 + 대회별 국가/이름/날짜/장소/거리/접수마감/신청링크/알림
  상태(`ON`/`OFF`)를 개최일 오름차순으로 출력
- 매칭 0건: "조건에 맞는 대회를 찾지 못했습니다" + 검색 조건 요약 (정상 결과, 오류 아님)
- 종료 코드: 성공(매칭 0건 포함) `0`, 인자 오류 `1`, DB 오류(파일 없음 등) `2`

DB를 갱신하지 않는 읽기 전용 스킬이다. `notify_telegram` 값을 바꾸려면
[marathon_notify_toggle](../marathon_notify_toggle/)을 사용한다.

## 테스트

```bash
pip install pytest
pytest skills/marathon_search/tests/
```

DB는 `tmp_path`로 격리된 임시 SQLite 파일을 사용하며 외부 API 호출이 없어 별도 mock이 필요 없다.
