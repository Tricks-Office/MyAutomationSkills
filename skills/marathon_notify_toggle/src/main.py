"""marathon_notify_toggle 진입점.

data/marathon.db의 race 테이블에서 대회를 검색해 notify_telegram 값을
FALSE(해제)/TRUE(재활성화)로 전환한다. 요구사항은 docs/PRD.md 참고.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "marathon.db"

EXIT_SUCCESS = 0
EXIT_SYSTEM_ERROR = 1
EXIT_NOT_FOUND = 2
EXIT_AMBIGUOUS = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class RaceRecord:
    id: int
    country: str
    race_name: str
    race_date: str
    notify_telegram: bool


class ArgumentError(ValueError):
    """커맨드라인 인자 조합이 잘못됐을 때 발생 (PRD 6절: 시스템 오류)."""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="data/marathon.db의 race.notify_telegram 값을 해제/재활성화한다."
    )
    parser.add_argument("--disable", action="store_true", help="notify_telegram을 FALSE로 설정(알림 해제)")
    parser.add_argument("--enable", action="store_true", help="notify_telegram을 TRUE로 설정(알림 재활성화)")
    parser.add_argument("--name", help="대회명 부분 일치 검색어")
    parser.add_argument("--country", choices=["KR", "CN"], help="국가로 후보 좁히기")
    parser.add_argument("--race-date", help="대회 개최일(YYYY-MM-DD)로 후보 좁히기")
    parser.add_argument("--id", type=int, help="race.id로 단일 레코드 지정")
    parser.add_argument(
        "--list", action="store_true", help="전체 대회 목록과 현재 notify_telegram 값 출력"
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    """PRD 3절 인자 규칙을 검증한다 (--list는 다른 모든 검증을 건너뛴다)."""
    if args.list:
        return
    if args.disable and args.enable:
        raise ArgumentError("--disable와 --enable을 동시에 지정할 수 없습니다.")
    if not args.disable and not args.enable:
        raise ArgumentError("--disable 또는 --enable 중 하나를 지정해야 합니다.")
    if not args.id and not args.name:
        raise ArgumentError("--name 또는 --id 중 하나를 지정해야 합니다.")


def _row_to_record(row: sqlite3.Row) -> RaceRecord:
    return RaceRecord(
        id=row["id"],
        country=row["country"],
        race_name=row["race_name"],
        race_date=row["race_date"],
        notify_telegram=bool(row["notify_telegram"]),
    )


def list_races(db_path: Path) -> list[RaceRecord]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, country, race_name, race_date, notify_telegram FROM race "
            "ORDER BY race_date, country, race_name"
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def find_races(
    db_path: Path,
    *,
    race_id: int | None = None,
    name: str | None = None,
    country: str | None = None,
    race_date: str | None = None,
) -> list[RaceRecord]:
    """PRD 5절: --id가 있으면 단건 조회, 없으면 name 부분 일치 + 선택적 country/race_date로 조회."""
    conditions: list[str] = []
    params: list[object] = []

    if race_id is not None:
        conditions.append("id = ?")
        params.append(race_id)
    else:
        conditions.append("race_name LIKE ?")
        params.append(f"%{name}%")
        if country:
            conditions.append("country = ?")
            params.append(country)
        if race_date:
            conditions.append("race_date = ?")
            params.append(race_date)

    query = (
        "SELECT id, country, race_name, race_date, notify_telegram FROM race WHERE "
        + " AND ".join(conditions)
        + " ORDER BY race_date, country, race_name"
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_record(row) for row in rows]


def set_notify(db_path: Path, race_id: int, *, notify: bool) -> bool:
    """notify_telegram을 갱신한다. 값이 실제로 바뀌면 True, 이미 같은 값이면 False (멱등성)."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE race SET notify_telegram = ?, updated_at = ? WHERE id = ? AND notify_telegram = ?",
            (int(notify), now, race_id, int(not notify)),
        )
        conn.commit()
        return cursor.rowcount > 0


def _format_record_line(record: RaceRecord) -> str:
    state = "ON" if record.notify_telegram else "OFF"
    return f"  [id={record.id}] {record.country} {record.race_name} ({record.race_date}) - 알림 {state}"


def format_candidates(records: list[RaceRecord]) -> str:
    lines = [
        f"조건에 맞는 대회가 {len(records)}건 있습니다. --id 또는 --race-date로 좁혀서 다시 실행하세요."
    ]
    lines.extend(_format_record_line(r) for r in records)
    return "\n".join(lines)


def format_list(records: list[RaceRecord]) -> str:
    if not records:
        return "DB에 등록된 대회가 없습니다."
    lines = [f"전체 대회 {len(records)}건"]
    lines.extend(_format_record_line(r) for r in records)
    return "\n".join(lines)


def format_result(record: RaceRecord, *, enable: bool, changed: bool) -> str:
    if not changed:
        state_label = "받고 있는" if enable else "해제된"
        return f"이미 알림을 {state_label} 대회입니다: {record.country} {record.race_name} ({record.race_date})"
    action_label = "재활성화" if enable else "해제"
    return f"알림을 {action_label}했습니다: {record.country} {record.race_name} ({record.race_date})"


def run(argv: list[str] | None = None, db_path: Path = DEFAULT_DB_PATH) -> int:
    """PRD 5절 처리 흐름을 오케스트레이션한다."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        validate_args(args)
    except ArgumentError as exc:
        logger.error("인자 오류: %s", exc)
        print(f"인자 오류: {exc}")
        return EXIT_SYSTEM_ERROR

    if not db_path.exists():
        logger.error("DB 파일을 찾을 수 없습니다: %s", db_path)
        print(f"DB 파일을 찾을 수 없습니다: {db_path}")
        return EXIT_SYSTEM_ERROR

    try:
        if args.list:
            records = list_races(db_path)
            logger.info("전체 목록 조회: %d건", len(records))
            print(format_list(records))
            return EXIT_SUCCESS

        records = find_races(
            db_path,
            race_id=args.id,
            name=args.name,
            country=args.country,
            race_date=args.race_date,
        )
    except sqlite3.Error:
        logger.exception("DB 조회 실패")
        print("DB 조회 중 오류가 발생했습니다.")
        return EXIT_SYSTEM_ERROR

    if not records:
        logger.info(
            "매칭 0건 (id=%s, name=%s, country=%s, race_date=%s)",
            args.id, args.name, args.country, args.race_date,
        )
        print("조건에 맞는 대회를 찾지 못했습니다.")
        return EXIT_NOT_FOUND

    if len(records) > 1:
        logger.info("매칭 %d건, 모호하여 갱신하지 않음", len(records))
        print(format_candidates(records))
        return EXIT_AMBIGUOUS

    record = records[0]
    try:
        changed = set_notify(db_path, record.id, notify=args.enable)
    except sqlite3.Error:
        logger.exception("DB 갱신 실패")
        print("DB 갱신 중 오류가 발생했습니다.")
        return EXIT_SYSTEM_ERROR

    logger.info(
        "notify_telegram 갱신: id=%d action=%s changed=%s",
        record.id, "enable" if args.enable else "disable", changed,
    )
    print(format_result(record, enable=args.enable, changed=changed))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(run())
