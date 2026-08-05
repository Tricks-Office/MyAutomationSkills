# marathon_notify_toggle

[marathon_finder](../marathon_finder/)가 `data/marathon.db`의 `race` 테이블에 수집해 둔 대회 중
특정 대회의 텔레그램 알림(`notify_telegram`)을 해제하거나 다시 켜는 스킬. Hermes가 사용자의
자연어 요청("OO마라톤 알림 그만 보내줘" / "OO마라톤 알림 다시 켜줘")을 커맨드라인 인자로 변환해
호출한다.

- 요구사항 문서: [`docs/PRD.md`](docs/PRD.md)

## 구성

- `src/main.py` — 실행 진입점 (대회 검색/매칭 → notify_telegram 갱신 → 결과 출력)
- `tests/test_main.py` — 검색/토글/멱등성/`run()` 흐름 스모크 테스트
- `requirements.txt` — 외부 의존성 없음 (표준 라이브러리만 사용)
- `skill.yaml` — Hermes 등록 메타데이터

## 실행 방법

```bash
# 저장소 루트에서 (marathon_finder가 이미 만들어 둔 data/marathon.db 필요)
python skills/marathon_notify_toggle/src/main.py --disable --name "춘천"
python skills/marathon_notify_toggle/src/main.py --enable --name "춘천"
python skills/marathon_notify_toggle/src/main.py --list
```

## 입력

| 인자 | 필수 여부 | 설명 |
|---|---|---|
| `--disable` | `--enable`과 택 1 (`--list` 사용 시 불필요) | `notify_telegram`을 `FALSE`로 설정(알림 해제) |
| `--enable` | `--disable`과 택 1 (`--list` 사용 시 불필요) | `notify_telegram`을 `TRUE`로 설정(알림 재활성화) |
| `--name` | `--id` 미사용 시 필수 | 대회명 부분 일치 검색어 |
| `--country` | 선택 (`KR`/`CN`) | 국가로 후보 좁히기 |
| `--race-date` | 선택 (`YYYY-MM-DD`) | 대회 개최일로 후보 좁히기 |
| `--id` | `--name` 미사용 시 필수 | `race.id`로 단일 레코드 바로 지정 |
| `--list` | 선택 | 매칭 없이 전체 대회 목록과 현재 알림 상태만 출력 |

`--name` 검색으로 후보가 2건 이상 매칭되면 DB를 바꾸지 않고 후보 목록(`id` 포함)을 출력한다.
이후 `--id`를 지정해 다시 실행하면 정확히 특정할 수 있다.

## 출력

- 성공: 대회명/알림 상태를 요약한 1줄 메시지 (표준출력) + 로그
- 매칭 0건/2건 이상: 원인과 후보 목록을 표준출력에 출력하고 0이 아닌 종료 코드 반환
- `--list`: 전체 대회와 현재 `notify_telegram` 상태(`ON`/`OFF`) 출력
- 종료 코드: 성공 `0`, 매칭 0건 `2`, 매칭 다건(모호) `3`, 그 외 시스템/인자 오류 `1`

## 테스트

```bash
pip install pytest
pytest skills/marathon_notify_toggle/tests/
```

DB는 `tmp_path`로 격리된 임시 SQLite 파일을 사용하며 외부 API 호출이 없어 별도 mock이 필요 없다.
